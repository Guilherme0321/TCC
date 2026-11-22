"""Infra compartilhada dos orquestradores A1–A4 (AD-01).

Sem framework de agentes: cada arquitetura é uma função que compõe agentes e
executor com estado explícito (`PipelineState`). Aqui ficam as peças comuns:
- `RunContext`: dependências de um run (projeto, modelo, cliente, executor…).
- `guarded_execute`: análise estática AST (AD-05) antes de executar (AD-04).
- `finalize`/`run_guarded`: montagem do RunRecord e captura do breaker por run
  (AD-08, AD-09, AD-12). O breaker global propaga para o runner.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from config import N_REPAIR
from infra.ast_guard import AstGuard
from infra.cost_tracker import CostTracker, RunBudgetExceeded
from infra.llm_client import LLMClient
from infra.project_loader import ProjectContext
from infra.prompt_loader import PromptLoader
from infra.test_executor import TestExecutor
from schemas.analysis import CodeAnalysis
from schemas.execution import ExecutionResult
from schemas.run_record import RunRecord, build_run_key
from schemas.tests_gen import GeneratedTests


@dataclass
class RunContext:
    """Todas as dependências de um único run (estado imutável de entrada)."""

    architecture: str
    project: ProjectContext
    model: str
    tier: str
    repetition: int
    client: LLMClient
    prompt_loader: PromptLoader
    cost_tracker: CostTracker
    ast_guard: AstGuard
    executor: TestExecutor
    started_at: str | None = None  # ISO, injetado pelo runner


@dataclass
class PipelineState:
    """Estado mutável acumulado durante o run.

    Mantém DOIS estados (AD-08, ratchet): o `tests`/`execution` CORRENTE, que
    alimenta os loops de repair e Reflexion, e o MELHOR já alcançado, que é o
    que vai para o RunRecord. Sem essa separação, uma re-geração que piora uma
    suite que já passava faz o run inteiro ser registrado pelo pior momento —
    penalizando por artefato justamente as arquiteturas que iteram.
    """

    tests: GeneratedTests | None = None
    execution: ExecutionResult | None = None
    analysis: CodeAnalysis | None = None
    repair_iterations: int = 0
    reflexion_iterations: int = 0
    prompt_versions: dict[str, str] = field(default_factory=dict)

    # Melhor estado alcançado no run (AD-08).
    best_tests: GeneratedTests | None = None
    best_execution: ExecutionResult | None = None
    states_evaluated: int = 0
    # Última execução já contabilizada, para o `track_best` defensivo de
    # `finalize` não contar de novo o estado que o fluxo já registrou.
    last_tracked: ExecutionResult | None = None


def new_record(ctx: RunContext) -> RunRecord:
    """RunRecord inicial com status placeholder `crashed` (AD-12)."""
    return RunRecord(
        run_key=build_run_key(ctx.architecture, ctx.model, ctx.project.name, ctx.repetition),
        architecture=ctx.architecture,
        model=ctx.model,
        tier=ctx.tier,
        project=ctx.project.name,
        repetition=ctx.repetition,
        status="crashed",
        started_at=ctx.started_at,
    )


def guarded_execute(ctx: RunContext, tests: GeneratedTests) -> ExecutionResult:
    """Análise estática (AD-05) e, se aprovada, execução isolada (AD-04)."""
    verdict = ctx.ast_guard.check(tests.test_code)
    if not verdict.allowed:
        return ExecutionResult(
            errors=1,
            execution_error="AST rejeitou a suite (AD-05): " + "; ".join(verdict.violations),
            stderr="; ".join(verdict.violations),
        )
    return ctx.executor.run(tests.test_code)


def quality_key(execution: ExecutionResult | None) -> tuple[float, ...]:
    """Ordem de preferência entre estados de um MESMO run (AD-08, ratchet).

    Lexicográfica, maior é melhor:

    1. **convergiu** — uma suite que roda e passa vence qualquer outra. É a
       própria definição de sucesso do AD-08, não um critério novo.
    2. cobertura de linha, 3. de branch, 4. mutation score — nessa ordem porque
       a cobertura é medida em todo run, enquanto o mutation score é `None`
       sempre que o mutmut é desligado ou estoura o timeout de AD-06.
    5. menos testes falhando — desempate entre estados que não convergiram.

    `None` vira -1.0: ausência de métrica nunca ganha de métrica medida.
    """
    if execution is None:
        return (-1.0,) * 5
    m = (execution.line_coverage, execution.branch_coverage, execution.mutation_score)
    return (
        1.0 if _converged(execution) else 0.0,
        *(-1.0 if v is None else float(v) for v in m),
        float(-(execution.failed + execution.errors)),
    )


def track_best(state: PipelineState) -> None:
    """Promove o estado corrente a melhor, se for melhor (AD-08).

    Chamado após CADA execução, e mais uma vez por `finalize` como rede de
    segurança — daí o guard de idempotência por identidade. Empate mantém o
    incumbente: entre estados equivalentes preserva-se o primeiro alcançado,
    o que evita trocar a suite por outra igualmente boa só porque veio depois.
    """
    if state.execution is None or state.execution is state.last_tracked:
        return
    state.last_tracked = state.execution
    state.states_evaluated += 1
    if state.best_execution is None or quality_key(state.execution) > quality_key(
        state.best_execution
    ):
        state.best_execution = state.execution
        state.best_tests = state.tests


def execute_and_track(ctx: RunContext, state: PipelineState, tests: GeneratedTests) -> None:
    """Executa a suite, registra como estado corrente e atualiza o melhor (AD-08).

    Ponto ÚNICO de execução dos quatro pipelines: qualquer suite que rode passa
    por aqui, então nenhum estado escapa do ratchet.
    """
    state.tests = tests
    state.execution = guarded_execute(ctx, tests)
    track_best(state)


def error_summary(execution: ExecutionResult) -> str:
    """Resumo do erro para o RepairAgent (traceback resumido ou testes falhando)."""
    if execution.execution_error:
        return execution.execution_error
    lines = [f"{t.name}: {t.message}" for t in execution.tests if t.outcome in ("failed", "error")]
    joined = "\n".join(lines)[:4000]
    return joined or (execution.stderr[:2000] or "testes falhando sem detalhe capturado")


def repair_loop(ctx: RunContext, state: PipelineState, repair_agent, source_code: str) -> None:
    """Loop de reparo até a suite rodar (máx N_REPAIR por geração, AD-07, AD-14)."""
    local = 0
    while (
        state.execution is not None
        and not state.execution.all_passed
        and not state.execution.timed_out
        and local < N_REPAIR
    ):
        local += 1
        state.repair_iterations += 1
        repaired = repair_agent.repair(
            source_code=source_code,
            current_tests=state.tests.test_code,
            error_summary=error_summary(state.execution),
            target_functions=state.tests.target_functions if state.tests else [],
        )
        execute_and_track(ctx, state, repaired)


def _converged(execution: ExecutionResult | None) -> bool:
    """Convergiu = suite executa e passa integralmente, sem timeout (AD-08)."""
    return bool(execution and execution.all_passed and not execution.timed_out)


def finalize(
    ctx: RunContext,
    record: RunRecord,
    state: PipelineState,
    *,
    failure_reason: str | None = None,
    aborted_budget: bool = False,
) -> RunRecord:
    """Preenche o RunRecord com o MELHOR estado alcançado e define o status.

    Ratchet do AD-08: o resultado do run é o melhor estado que ele produziu, não
    o último. O CUSTO, porém, continua sendo o total de TODAS as chamadas —
    inclusive as das iterações cujo resultado foi descartado. É o que mantém o
    CCP/CPKM honesto: a arquitetura é cobrada por tudo que gastou e creditada
    pelo melhor que entregou.
    """
    # Antes de escolher, garante que o estado corrente foi avaliado — cobre
    # fluxos que atribuíram `state.execution` sem passar por execute_and_track
    # e o caminho do breaker de custo, que interrompe o fluxo no meio.
    track_best(state)
    best_execution = state.best_execution
    best_tests = state.best_tests

    record.execution = best_execution
    record.sync_metrics_from_execution()
    record.final_tests = best_tests.test_code if best_tests else ""
    record.repair_iterations = state.repair_iterations
    record.reflexion_iterations = state.reflexion_iterations
    record.states_evaluated = state.states_evaluated
    record.discarded_regression = (
        best_execution is not None and best_execution is not state.execution
    )
    record.ast_rejections = ctx.ast_guard.rejections
    record.prompt_versions = dict(state.prompt_versions)
    record.tokens = ctx.cost_tracker.tokens
    record.cost_usd = round(ctx.cost_tracker.run_cost, 6)

    converged = _converged(best_execution)
    timed_out = bool(best_execution and best_execution.timed_out)
    record.converged = converged
    record.timed_out = timed_out
    record.aborted_budget = aborted_budget

    if aborted_budget:
        record.status = "aborted_budget"
        record.failure_reason = failure_reason or "circuit breaker de custo por run (AD-09)"
    elif timed_out:
        record.status = "timeout"
        record.failure_reason = failure_reason or "timeout de execução (AD-06)"
    elif converged:
        record.status = "completed"
        record.failure_reason = None
    else:
        record.status = "failed"
        record.failure_reason = failure_reason or _default_failure_reason(best_execution)
    return record


def _default_failure_reason(execution: ExecutionResult | None) -> str:
    if execution is None:
        return "nenhuma execução produzida"
    if execution.execution_error:
        return f"testes não executam: {execution.execution_error[:200]}"
    if execution.failed:
        return f"{execution.failed} teste(s) falhando após esgotar o pipeline"
    return "não convergiu"


def run_guarded(ctx: RunContext, flow: Callable[[RunContext, PipelineState], None]) -> RunRecord:
    """Executa o fluxo capturando o breaker por run (AD-09).

    `RunBudgetExceeded` → estado parcial com `aborted_budget` (AD-08/AD-12).
    `GlobalBudgetExceeded` propaga para o runner pausar (AD-09).
    Outras exceções propagam; o `finalize_run` do writer registra como crash.
    """
    record = new_record(ctx)
    state = PipelineState()
    try:
        flow(ctx, state)
        return finalize(ctx, record, state)
    except RunBudgetExceeded as exc:
        return finalize(ctx, record, state, aborted_budget=True, failure_reason=str(exc))
