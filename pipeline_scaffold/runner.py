"""ExperimentRunner — produto cartesiano com retomada idempotente (AD-09, AD-10, AD-15).

- Produto cartesiano: arquiteturas × modelos(do tier) × projetos × repetições.
- Retomada idempotente (AD-10): pula chaves com status definitivo; runs `crashed`
  são reexecutados (AD-12).
- Disciplina de tier (AD-15): `--tier cost` roda primeiro; `frontier` só depois,
  com confirmação explícita.
- Circuit breaker global (AD-09): ao estourar o teto global, pausa o experimento.
- Escrita JSONL única por run, crash-safe (AD-12).

Segurança de custo: sem `--execute`, o runner apenas IMPRIME o plano (dry-run) e
não faz nenhuma chamada de API. `frontier` + `--execute` exige `--confirm-frontier`.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from architectures import PIPELINES
from architectures.common import RunContext, new_record
from config import (
    ARCHITECTURES,
    BATCH_POOL_SIZE,
    BENCHMARK_DIR,
    DOCKER_MAX_CONCURRENT,
    MAX_COST_GLOBAL_USD,
    MODELS,
    N_REPETITIONS,
    RESULTS_JSONL,
)
from infra.ast_guard import AstGuard
from infra.batch import BatchCoalescer
from infra.cost_tracker import CostTracker, GlobalBudget, GlobalBudgetExceeded
from infra.llm_client import LLMClient
from infra.project_loader import ProjectContext, load_project
from infra.prompt_loader import PromptLoader
from infra.result_writer import ResultWriter
from infra.test_executor import DockerRunner, TestExecutor
from schemas.run_record import RunRecord, build_run_key

logger = logging.getLogger("runner")


@dataclass(frozen=True)
class RunSpec:
    architecture: str
    model: str
    tier: str
    project: str
    repetition: int

    @property
    def run_key(self) -> str:
        return build_run_key(self.architecture, self.model, self.project, self.repetition)


def iter_models(tier: str, models: dict = MODELS) -> list[str]:
    """Modelos de um tier, em ordem estável."""
    return sorted(name for name, spec in models.items() if spec["tier"] == tier)


def build_plan(
    *,
    tier: str,
    projects: list[str],
    architectures: list[str] = ARCHITECTURES,
    repetitions: int = N_REPETITIONS,
    completed: set[str] | None = None,
    models: dict = MODELS,
) -> list[RunSpec]:
    """Produto cartesiano do experimento, pulando chaves já concluídas (AD-10).

    Ordena com a repetição no laço mais externo: assim os primeiros runs cobrem
    todas as células em rep-1 (útil para piloto com `--limit`).
    """
    completed = completed or set()
    tier_models = iter_models(tier, models)
    plan: list[RunSpec] = []
    for rep in range(1, repetitions + 1):
        for project in projects:
            for arch in architectures:
                for model in tier_models:
                    spec = RunSpec(arch, model, tier, project, rep)
                    if spec.run_key not in completed:
                        plan.append(spec)
    return plan


def default_executor_factory(project_ctx: ProjectContext) -> TestExecutor:
    """Executor de produção: Docker efêmero por projeto (AD-04)."""
    from scripts.build_images import image_tag

    image = image_tag(project_ctx.name)
    # source_paths RELATIVOS ao workdir: o executor copia os fontes para /work
    # antes de mutar, porque /project é read-only sob AD-04 (AD-21).
    return TestExecutor(
        DockerRunner(image),
        cov_target=project_ctx.cov_target,
        mutmut_source_paths=list(project_ctx.source_paths),
        mutate_targets=list(project_ctx.mutate_targets),
        project_root=project_ctx.root,
    )


def load_projects(benchmark_dir: Path = BENCHMARK_DIR) -> dict[str, ProjectContext]:
    """Carrega todos os projetos do benchmark (AD-04 exige contexto por projeto)."""
    if not benchmark_dir.is_dir():
        return {}
    out: dict[str, ProjectContext] = {}
    for path in sorted(benchmark_dir.iterdir()):
        if path.is_dir():
            try:
                out[path.name] = load_project(path)
            except (OSError, ValueError) as exc:
                logger.warning("falha ao carregar projeto %s: %s", path.name, exc)
    return out


def _partial_record(
    ctx: RunContext,
    placeholder: RunRecord,
    *,
    status: str,
    reason: str,
    aborted_budget: bool = False,
) -> RunRecord:
    """RunRecord parcial (crash/breaker global) com custo já consumido (AD-08, AD-12)."""
    placeholder.status = status  # type: ignore[assignment]
    placeholder.failure_reason = reason
    placeholder.aborted_budget = aborted_budget
    placeholder.cost_usd = round(ctx.cost_tracker.run_cost, 6)
    placeholder.tokens = ctx.cost_tracker.tokens
    placeholder.ast_rejections = ctx.ast_guard.rejections
    return placeholder


class ExperimentRunner:
    """Executa um plano de runs, com retomada e breaker global (AD-09, AD-10).

    Sob a Batch API (AD-15), mantém `pool_size` runs em voo em threads distintas:
    é o que dá ao `BatchCoalescer` chamadas concorrentes para agrupar num único
    job. Com `pool_size=1` (ou `--no-batch`) o comportamento volta a ser
    estritamente serial e síncrono, como antes.
    """

    def __init__(
        self,
        *,
        results_path: Path = RESULTS_JSONL,
        batch: bool = True,
        pool_size: int = BATCH_POOL_SIZE,
        client: LLMClient | None = None,
        prompt_loader: PromptLoader | None = None,
        executor_factory: Callable[[ProjectContext], TestExecutor] = default_executor_factory,
        pipelines: dict = PIPELINES,
        now_iso: Callable[[], str] | None = None,
        coalescer: BatchCoalescer | None = None,
    ) -> None:
        self.writer = ResultWriter(results_path)
        self.batch = batch
        # Sem batch não há o que coalescer: volta ao caminho serial síncrono.
        self.pool_size = max(1, pool_size) if batch else 1
        # `batch=True` no cost_tracker (−50%) só é honesto se as chamadas
        # realmente forem pela Batch API — daí o coalescer acompanhar a flag.
        self.coalescer = coalescer or (BatchCoalescer() if batch else None)
        self.client = client or LLMClient(coalescer=self.coalescer)
        self.prompt_loader = prompt_loader or PromptLoader()
        self.executor_factory = executor_factory
        self.pipelines = pipelines
        self.now_iso = now_iso or _utc_now_iso
        self._docker_gate = (
            threading.Semaphore(DOCKER_MAX_CONCURRENT) if self.pool_size > 1 else None
        )

    def run(
        self,
        plan: list[RunSpec],
        projects: dict[str, ProjectContext],
        *,
        global_budget: GlobalBudget,
    ) -> dict[str, int]:
        """Executa o plano. Retorna contagem por status. Pausa no breaker global."""
        if self.pool_size <= 1:
            return self._run_serial(plan, projects, global_budget=global_budget)
        return self._run_pool(plan, projects, global_budget=global_budget)

    # ── Modos de execução ──────────────────────────────────────────────────

    def _run_serial(
        self,
        plan: list[RunSpec],
        projects: dict[str, ProjectContext],
        *,
        global_budget: GlobalBudget,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for spec in plan:
            project_ctx = projects.get(spec.project)
            if project_ctx is None:
                logger.warning("projeto ausente, pulando: %s", spec.project)
                continue
            record, stop = self._run_one(spec, project_ctx, global_budget)
            counts[record.status] = counts.get(record.status, 0) + 1
            self._log_done(spec, record, global_budget)
            if stop:
                break
        return counts

    def _run_pool(
        self,
        plan: list[RunSpec],
        projects: dict[str, ProjectContext],
        *,
        global_budget: GlobalBudget,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        counts_lock = threading.Lock()
        stop = threading.Event()

        def work(spec: RunSpec) -> None:
            # Breaker global disparado: não inicia novos runs (os em voo terminam).
            if stop.is_set():
                return
            project_ctx = projects.get(spec.project)
            if project_ctx is None:
                logger.warning("projeto ausente, pulando: %s", spec.project)
                return
            record, should_stop = self._run_one(spec, project_ctx, global_budget)
            if should_stop:
                stop.set()
            with counts_lock:
                counts[record.status] = counts.get(record.status, 0) + 1
            self._log_done(spec, record, global_budget)

        logger.info("executando %d run(s) com pool de %d", len(plan), self.pool_size)
        with ThreadPoolExecutor(max_workers=self.pool_size, thread_name_prefix="run") as pool:
            for future in [pool.submit(work, spec) for spec in plan]:
                future.result()  # propaga KeyboardInterrupt/SystemExit
        return counts

    def _run_one(
        self, spec: RunSpec, project_ctx: ProjectContext, global_budget: GlobalBudget
    ) -> tuple[RunRecord, bool]:
        """Executa um run e o grava. Retorna (registro, pausar_experimento)."""
        ctx = self._make_context(spec, project_ctx, global_budget)
        placeholder = new_record(ctx)
        try:
            record = self.pipelines[spec.architecture](ctx)
            record.stamp_finish(self.now_iso())
            self.writer.append(record)
            return record, False
        except GlobalBudgetExceeded as exc:
            rec = _partial_record(
                ctx, placeholder, status="aborted_budget", reason=str(exc), aborted_budget=True
            )
            rec.stamp_finish(self.now_iso())
            self.writer.append(rec)
            logger.error("BREAKER GLOBAL (AD-09): %s — pausando experimento", exc)
            return rec, True
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # noqa: BLE001 — crash é registrado, não descartado
            rec = _partial_record(
                ctx, placeholder, status="crashed", reason=f"{type(exc).__name__}: {exc}"
            )
            rec.stamp_finish(self.now_iso())
            self.writer.append(rec)
            logger.exception("CRASH em %s (reexecutado na retomada, AD-12)", spec.run_key)
            return rec, False

    @staticmethod
    def _log_done(spec: RunSpec, record: RunRecord, global_budget: GlobalBudget) -> None:
        logger.info(
            "%s → %s (custo $%.4f, global $%.2f)",
            spec.run_key,
            record.status,
            record.cost_usd,
            global_budget.spent,
        )

    def _make_context(
        self, spec: RunSpec, project_ctx: ProjectContext, global_budget: GlobalBudget
    ) -> RunContext:
        executor = self.executor_factory(project_ctx)
        if self._docker_gate is not None and isinstance(executor, TestExecutor):
            executor.gate = self._docker_gate
        return RunContext(
            architecture=spec.architecture,
            project=project_ctx,
            model=spec.model,
            tier=spec.tier,
            repetition=spec.repetition,
            client=self.client,
            prompt_loader=self.prompt_loader,
            cost_tracker=CostTracker(tier=spec.tier, batch=self.batch, global_budget=global_budget),
            ast_guard=AstGuard(),
            executor=executor,
            started_at=self.now_iso(),
        )


def _utc_now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Runner do experimento (AD-09, AD-10, AD-15).")
    p.add_argument("--tier", choices=["cost", "frontier"], default="cost", help="tier de modelos")
    p.add_argument("--execute", action="store_true", help="faz chamadas reais (senão: dry-run)")
    p.add_argument(
        "--confirm-frontier", action="store_true", help="obrigatório p/ frontier+execute"
    )
    p.add_argument("--limit", type=int, default=None, help="máximo de runs (piloto)")
    # Tamanho de amostra, não hiperparâmetro: não altera o que o LLM recebe nem
    # produz, então não cai na regra 3 (constantes experimentais). Existe para a
    # segunda rodada de AD-25, que roda 3 repetições por restrição orçamentária
    # contra as 5 da coleta de agosto. A assimetria é desvio declarado, não
    # acidente: reduz a precisão da estimativa de variância do Sonnet 5, mas
    # preserva os 17 pares por projeto que sustentam o Wilcoxon de AD-23.
    p.add_argument(
        "--reps",
        type=int,
        default=N_REPETITIONS,
        help=f"repetições por célula (padrão: {N_REPETITIONS}; AD-25 usa 3)",
    )
    p.add_argument("--arch", action="append", help="filtra arquitetura (repetível)")
    p.add_argument("--project", action="append", help="filtra projeto (repetível)")
    p.add_argument(
        "--model",
        action="append",
        help="filtra modelo dentro do tier (repetível). AD-20: a execução vigente "
        "usa apenas gpt-5-mini, por restrição de orçamento.",
    )
    p.add_argument("--results", default=str(RESULTS_JSONL), help="caminho do JSONL")
    p.add_argument("--no-batch", action="store_true", help="desliga Batch API (restaura teto $600)")
    p.add_argument(
        "--pool",
        type=int,
        default=BATCH_POOL_SIZE,
        help=f"runs simultâneos em voo p/ alimentar o batch (padrão: {BATCH_POOL_SIZE})",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)

    writer = ResultWriter(args.results)
    completed = writer.completed_keys()
    projects = load_projects()

    project_names = sorted(projects)
    if args.project:
        project_names = [p for p in project_names if p in set(args.project)]
    architectures = args.arch or ARCHITECTURES

    models = MODELS
    if args.model:
        chosen = set(args.model)
        unknown = chosen - set(MODELS)
        if unknown:
            print(f"modelo(s) desconhecido(s): {sorted(unknown)}", file=sys.stderr)
            return 1
        models = {name: spec for name, spec in MODELS.items() if name in chosen}

    plan = build_plan(
        tier=args.tier,
        projects=project_names,
        architectures=architectures,
        completed=completed,
        models=models,
        repetitions=args.reps,
    )
    if args.limit is not None:
        plan = plan[: args.limit]

    batch_on = not args.no_batch
    pool = max(1, args.pool) if batch_on else 1
    print(f"Tier: {args.tier} | modelos: {iter_models(args.tier, models)}")
    print(f"Teto global (AD-20): ${MAX_COST_GLOBAL_USD:.2f}")
    print(f"Batch API: {'ligada' if batch_on else 'DESLIGADA'} | runs em voo: {pool}")
    print(f"Projetos: {project_names or '(nenhum em benchmark/)'}")
    print(f"Runs concluídos (pulados): {len(completed)} | pendentes no plano: {len(plan)}")

    if not plan:
        print("Nada a executar.")
        return 0

    if not args.execute:
        print("\n[DRY-RUN] Nenhuma chamada de API feita. Use --execute para rodar de verdade.")
        for spec in plan[:20]:
            print(f"  - {spec.run_key}")
        if len(plan) > 20:
            print(f"  … (+{len(plan) - 20} runs)")
        return 0

    if args.tier == "frontier" and not args.confirm_frontier:
        print(
            "\nRECUSADO: tier frontier gasta muito. Rode o tier cost primeiro (AD-15) e,"
            " ao liberar frontier, use --confirm-frontier.",
            file=sys.stderr,
        )
        return 2

    if not projects:
        print("Sem projetos em benchmark/. Rode scripts/build_images.py antes.", file=sys.stderr)
        return 1

    spent = sum(r.get("cost_usd", 0.0) for r in writer.read_all())
    global_budget = GlobalBudget(spent=spent)
    runner = ExperimentRunner(results_path=Path(args.results), batch=batch_on, pool_size=pool)
    counts = runner.run(plan, projects, global_budget=global_budget)

    print(f"\nResumo: {counts} | custo global acumulado: ${global_budget.spent:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
