"""Testes dos pipelines A1–A4 com LLM e executor mockados (Fase 4).

Cobre: convergência, estouro de N (repair/reflexion), estouro de budget (AD-09),
timeout (AD-06) e crash (AD-12). Sem rede, sem Docker.
"""

from pathlib import Path

import pytest

from architectures.a1 import run_a1
from architectures.a2 import run_a2
from architectures.a3 import run_a3
from architectures.a4 import run_a4
from architectures.common import RunContext
from config import N_REFLEXION, N_REPAIR, PROMPTS_DIR
from infra.ast_guard import AstGuard
from infra.cost_tracker import CostTracker
from infra.llm_client import LLMResponse
from infra.project_loader import ProjectContext
from infra.prompt_loader import PromptLoader
from schemas.execution import ExecutionResult

# ── Dublês ─────────────────────────────────────────────────────────────────

GEN_CODE = "import calc\ndef test_soma():\n    assert calc.soma(1, 2) == 3\n"
ANALYZER_JSON = '{"symbols": [{"name": "soma", "complexity": 1}], "dependencies_to_mock": []}'
CRITIC_JSON = (
    '{"uncovered_lines": [5], "surviving_mutants": ["m1"], '
    '"suggestions": [{"kind": "coverage_gap", "target": "linha 5", '
    '"suggestion": "testar divisão por zero"}], "summary": "faltam bordas"}'
)


class Router:
    """Roteia a resposta do LLM pelo papel do agente (detecta pelo prompt)."""

    def __init__(
        self, *, gen=GEN_CODE, analyzer=ANALYZER_JSON, repair=GEN_CODE, critic=CRITIC_JSON
    ):
        self.map = {"gen": gen, "analyzer": analyzer, "repair": repair, "critic": critic}

    def __call__(self, request):
        s = request.dynamic_suffix
        if "crítico de qualidade" in s:
            return self.map["critic"]
        if "NÃO executam" in s:
            return self.map["repair"]
        if "Analise o código-fonte" in s:
            return self.map["analyzer"]
        return self.map["gen"]


class FakeClient:
    """Imita LLMClient.complete; contabiliza custo real para os breakers/tokens."""

    def __init__(self, responder, *, input_tokens=8000, output_tokens=2000, cached=6000):
        self.responder = responder
        self.tokens = (input_tokens, output_tokens, cached)

    def complete(self, request, *, cost_tracker):
        text = self.responder(request)
        inp, out, cached = self.tokens
        cost_tracker.record(
            request.model, input_tokens=inp, output_tokens=out, cached_input_tokens=cached
        )
        return LLMResponse(text=text, input_tokens=inp, output_tokens=out, model=request.model)


class FakeExecutor:
    """Devolve ExecutionResults roteirizados; repete o último ao esgotar a lista."""

    __test__ = False

    def __init__(self, results):
        self.results = results
        self.calls = 0

    def run(self, code):
        idx = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[idx]


def _passed_clean():
    return ExecutionResult(passed=2, line_coverage=100.0, branch_coverage=100.0, mutation_score=1.0)


def _passed_lowquality():
    return ExecutionResult(
        passed=2, line_coverage=60.0, uncovered_lines=[5, 6], surviving_mutants=["m1"]
    )


def _exec_error():
    return ExecutionResult(errors=1, execution_error="ImportError: no module")


def _failing():
    return ExecutionResult(passed=1, failed=1)


def _timed_out():
    return ExecutionResult(timed_out=True, execution_error="excedeu 90s")


def _make_ctx(arch, responder, exec_results, *, per_run_caps=None, model="gpt-5-mini"):
    project = ProjectContext(
        name="proj",
        root=Path("."),
        cov_target="calc",
        source_paths=["calc.py"],
        source_code="def soma(a, b):\n    return a + b\n",
    )
    tracker = CostTracker(
        tier="cost", batch=True, per_run_caps=per_run_caps or {"frontier": 2.0, "cost": 1.0}
    )
    return RunContext(
        architecture=arch,
        project=project,
        model=model,
        tier="cost",
        repetition=1,
        client=FakeClient(responder),
        prompt_loader=PromptLoader(PROMPTS_DIR),
        cost_tracker=tracker,
        ast_guard=AstGuard(),
        executor=FakeExecutor(exec_results),
        started_at="2026-07-06T00:00:00",
    )


# ── A1 ─────────────────────────────────────────────────────────────────────


def test_a1_converges():
    rec = run_a1(_make_ctx("A1", Router(), [_passed_clean()]))
    assert rec.status == "completed"
    assert rec.converged
    assert rec.line_coverage == 100.0
    assert rec.prompt_versions["generator"].startswith("generator.v")
    assert rec.cost_usd > 0
    assert rec.tokens.n_llm_calls == 1
    assert rec.repair_iterations == 0


def test_a1_failing_tests_saved_as_failed():
    rec = run_a1(_make_ctx("A1", Router(), [_failing()]))
    assert rec.status == "failed"
    assert not rec.converged
    assert rec.execution.failed == 1  # métricas parciais preservadas (AD-08)


def test_a1_ast_rejection_counted():
    # AD-05: código malicioso é rejeitado e contabilizado; não executa.
    danger = "import os\ndef test_x():\n    os.system('rm -rf /')\n"
    rec = run_a1(_make_ctx("A1", Router(gen=danger), [_passed_clean()]))
    assert rec.status == "failed"
    assert rec.ast_rejections == 1
    assert "AST" in rec.failure_reason


# ── A2 ─────────────────────────────────────────────────────────────────────


def test_a2_repairs_then_converges():
    ctx = _make_ctx("A2", Router(), [_exec_error(), _passed_clean()])
    rec = run_a2(ctx)
    assert rec.status == "completed"
    assert rec.converged
    assert rec.repair_iterations == 1
    assert "repair" in rec.prompt_versions


def test_a2_repair_exhausted():
    ctx = _make_ctx("A2", Router(), [_exec_error()])  # sempre erra
    rec = run_a2(ctx)
    assert rec.status == "failed"
    assert rec.repair_iterations == N_REPAIR  # esgotou o limite (AD-07)


def test_a2_no_repair_when_first_passes():
    ctx = _make_ctx("A2", Router(), [_passed_clean()])
    rec = run_a2(ctx)
    assert rec.repair_iterations == 0
    assert rec.converged


def test_a2_budget_breaker_aborts():
    # AD-09: teto por run minúsculo → aborta com estado parcial.
    ctx = _make_ctx("A2", Router(), [_passed_clean()], per_run_caps={"cost": 1e-9, "frontier": 2.0})
    rec = run_a2(ctx)
    assert rec.status == "aborted_budget"
    assert rec.aborted_budget
    assert rec.cost_usd > 0  # custo parcial contabilizado (AD-08)


# ── A3 ─────────────────────────────────────────────────────────────────────


def test_a3_uses_analyzer():
    rec = run_a3(_make_ctx("A3", Router(), [_passed_clean()]))
    assert rec.status == "completed"
    assert "analyzer" in rec.prompt_versions
    assert "generator" in rec.prompt_versions
    assert rec.tokens.n_llm_calls == 2  # analyzer + generator


def test_a3_survives_invalid_analysis():
    # AD-02: análise inválida é falha de iteração; segue sem análise.
    rec = run_a3(_make_ctx("A3", Router(analyzer="não é json"), [_passed_clean()]))
    assert rec.status == "completed"


# ── A4 ─────────────────────────────────────────────────────────────────────


def test_a4_reflexion_improves_and_stops():
    # passa com baixa qualidade → critic → regenera → passa limpo → para.
    ctx = _make_ctx("A4", Router(), [_passed_lowquality(), _passed_clean()])
    rec = run_a4(ctx)
    assert rec.status == "completed"
    assert rec.converged
    assert rec.reflexion_iterations == 1
    assert set(rec.prompt_versions) == {"analyzer", "generator", "repair", "critic"}


def test_a4_reflexion_exhausts():
    # sempre baixa qualidade → esgota N_REFLEXION.
    ctx = _make_ctx("A4", Router(), [_passed_lowquality()])
    rec = run_a4(ctx)
    assert rec.reflexion_iterations == N_REFLEXION  # AD-07
    # tests passam, mas qualidade insuficiente após esgotar: converged (rodam e passam)
    assert rec.converged


def test_a4_repairs_before_reflexion():
    # erra → repara → passa limpo (sem reflexion necessário).
    ctx = _make_ctx("A4", Router(), [_exec_error(), _passed_clean()])
    rec = run_a4(ctx)
    assert rec.repair_iterations == 1
    assert rec.reflexion_iterations == 0
    assert rec.converged


def test_a4_timeout_status():
    # AD-06: suite que estoura timeout → status timeout, não descartado.
    ctx = _make_ctx("A4", Router(), [_timed_out()])
    rec = run_a4(ctx)
    assert rec.status == "timeout"
    assert rec.timed_out
    assert not rec.converged


def test_a4_crash_propagates():
    # AD-12: crash não-tratado propaga (o runner registra via finalize_run).
    def boom(_request):
        raise RuntimeError("provedor explodiu")

    ctx = _make_ctx("A4", boom, [_passed_clean()])
    with pytest.raises(RuntimeError):
        run_a4(ctx)


# ── Ratchet do melhor estado (AD-08) ───────────────────────────────────────


def _passed_midquality():
    return ExecutionResult(passed=2, line_coverage=80.0, branch_coverage=70.0, uncovered_lines=[9])


def test_a4_ratchet_keeps_best_when_reflexion_regresses():
    # O caso medido em 2026-08-12: a re-geração do Reflexion piora uma suite
    # que já passava. O run deve registrar o MELHOR estado, não o último.
    ctx = _make_ctx("A4", Router(), [_passed_midquality(), _exec_error()])
    rec = run_a4(ctx)
    assert rec.status == "completed"
    assert rec.converged
    assert rec.line_coverage == 80.0
    assert rec.discarded_regression, "o ratchet deveria ter revertido a regressão"


def test_a4_ratchet_does_not_mask_a_genuine_improvement():
    # Melhorou de verdade: registra o ÚLTIMO, e não marca regressão.
    ctx = _make_ctx("A4", Router(), [_passed_midquality(), _passed_clean()])
    rec = run_a4(ctx)
    assert rec.line_coverage == 100.0
    assert rec.mutation_score == 1.0
    assert not rec.discarded_regression


def test_a2_ratchet_reverts_repair_that_worsens_a_failing_suite():
    # O repair loop de A2 PARA assim que a suite passa, então A2 só pode
    # regredir entre estados que não passam: aqui um repair troca uma suite
    # que ao menos rodava e media cobertura por uma que nem importa.
    partial = ExecutionResult(passed=1, failed=1, line_coverage=50.0)
    ctx = _make_ctx("A2", Router(), [partial, _exec_error()])
    rec = run_a2(ctx)
    assert rec.status == "failed"  # nenhum estado convergiu
    assert rec.line_coverage == 50.0  # mas registrou o menos ruim
    assert rec.discarded_regression


def test_ratchet_never_fabricates_success_when_nothing_ever_passed():
    # Nenhum estado convergiu: o ratchet escolhe o melhor entre os ruins,
    # mas o run continua `failed`. Não pode inventar convergência.
    ctx = _make_ctx("A2", Router(), [_exec_error(), _failing(), _exec_error()])
    rec = run_a2(ctx)
    assert rec.status == "failed"
    assert not rec.converged


def test_cost_accumulates_over_discarded_states():
    # AD-08: creditado pelo melhor, cobrado por TUDO — inclusive o descartado.
    ctx = _make_ctx("A2", Router(), [_failing(), _passed_clean(), _exec_error()])
    rec = run_a2(ctx)
    assert rec.tokens.n_llm_calls > 1
    assert rec.cost_usd > 0
    assert rec.states_evaluated == rec.repair_iterations + 1


def test_single_state_architectures_never_flag_regression():
    # A1/A3 produzem um estado só: o ratchet é inerte por construção.
    for arch, runner in (("A1", run_a1), ("A3", run_a3)):
        rec = runner(_make_ctx(arch, Router(), [_passed_midquality()]))
        assert rec.states_evaluated == 1
        assert not rec.discarded_regression


def test_quality_key_orders_converged_above_higher_coverage():
    # Uma suite que PASSA vence uma que cobre mais mas não roda (AD-08).
    from architectures.common import quality_key

    passing_low = ExecutionResult(passed=1, line_coverage=10.0)
    broken_high = ExecutionResult(errors=1, execution_error="boom", line_coverage=99.0)
    assert quality_key(passing_low) > quality_key(broken_high)
    assert quality_key(None) < quality_key(broken_high)
