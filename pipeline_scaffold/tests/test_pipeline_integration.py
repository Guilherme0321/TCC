"""Integração end-to-end de um pipeline com executor REAL (Fase 4/5, sem API).

Só o LLM é mockado; AST guard, LocalRunner, pytest-cov e finalize são reais. Prova
que a stack toda compõe: agente → guard (AD-05) → execução (AD-04 via LocalRunner) →
cobertura → RunRecord (AD-08/AD-12).
"""

from pathlib import Path

from architectures.a1 import run_a1
from architectures.a2 import run_a2
from architectures.common import RunContext
from config import PROMPTS_DIR
from infra.ast_guard import AstGuard
from infra.cost_tracker import CostTracker
from infra.llm_client import LLMResponse
from infra.project_loader import load_project
from infra.prompt_loader import PromptLoader
from infra.test_executor import LocalRunner, TestExecutor

FIXTURE = Path(__file__).parent / "fixtures" / "sample_project"

PASSING_TESTS = (
    "import calc\n"
    "def test_soma():\n    assert calc.soma(1, 2) == 3\n"
    "def test_divide():\n    assert calc.divide(6, 2) == 3\n"
    "def test_divide_zero():\n"
    "    import pytest\n"
    "    with pytest.raises(ValueError):\n        calc.divide(1, 0)\n"
    "def test_classifica():\n"
    "    assert calc.classifica(-1) == 'negativo'\n"
    "    assert calc.classifica(0) == 'zero'\n"
    "    assert calc.classifica(5) == 'positivo'\n"
)


class _FixedClient:
    def __init__(self, text):
        self.text = text

    def complete(self, request, *, cost_tracker):
        cost_tracker.record(request.model, input_tokens=8000, output_tokens=2000)
        return LLMResponse(text=self.text, input_tokens=8000, output_tokens=2000)


def _ctx(arch, client):
    project = load_project(FIXTURE)
    executor = TestExecutor(LocalRunner(project.root), cov_target="calc", run_mutation=False)
    return RunContext(
        architecture=arch,
        project=project,
        model="gpt-5-mini",
        tier="cost",
        repetition=1,
        client=client,
        prompt_loader=PromptLoader(PROMPTS_DIR),
        cost_tracker=CostTracker(tier="cost", batch=True),
        ast_guard=AstGuard(),
        executor=executor,
        started_at="2026-07-06T00:00:00",
    )


def test_a1_end_to_end_real_execution():
    rec = run_a1(_ctx("A1", _FixedClient(PASSING_TESTS)))
    assert rec.status == "completed"
    assert rec.converged
    assert rec.execution.passed == 4
    assert rec.line_coverage == 100.0
    assert rec.cost_usd > 0
    # RunRecord serializável e completo (critério de pronto global).
    assert rec.model_dump_json()


def test_a2_end_to_end_repairs_broken_then_runs():
    # Primeira geração com erro de sintaxe; o "repair" devolve a suite correta.
    broken = "import calc\ndef test_x(: assert True\n"
    client = _SwitchingClient([broken, PASSING_TESTS])
    rec = run_a2(_ctx("A2", client))
    assert rec.converged
    assert rec.repair_iterations >= 1


class _SwitchingClient:
    """Devolve respostas em sequência (uma por chamada)."""

    def __init__(self, texts):
        self.texts = texts
        self.i = 0

    def complete(self, request, *, cost_tracker):
        text = self.texts[min(self.i, len(self.texts) - 1)]
        self.i += 1
        cost_tracker.record(request.model, input_tokens=8000, output_tokens=2000)
        return LLMResponse(text=text, input_tokens=8000, output_tokens=2000)
