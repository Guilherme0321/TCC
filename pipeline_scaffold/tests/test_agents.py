"""Testes dos agentes com LLM mockado (Fase 3, AD-02, AD-14)."""

import pytest

from agents.analyzer import CodeAnalyzerAgent
from agents.base import AgentOutputError, extract_json, strip_code_fences
from agents.critic import CriticAgent
from agents.generator import TestGeneratorAgent
from agents.repair import RepairAgent
from config import PROMPTS_DIR
from infra.cost_tracker import CostTracker
from infra.llm_client import LLMClient, LLMResponse, Provider, RetryPolicy
from infra.prompt_loader import PromptLoader
from schemas.analysis import CodeAnalysis, SymbolAnalysis
from schemas.feedback import CriticFeedback

MODEL = "gpt-5-mini"  # provider openai


class ScriptedProvider(Provider):
    """Devolve um texto fixo e guarda a última requisição para inspeção."""

    def __init__(self, text: str):
        self.text = text
        self.last_request = None

    def complete(self, request):
        self.last_request = request
        return LLMResponse(
            text=self.text, input_tokens=8000, output_tokens=1500, model=request.model
        )


def _make_agent(agent_cls, text):
    provider = ScriptedProvider(text)
    client = LLMClient(
        providers={"openai": provider},
        retry=RetryPolicy(sleep=lambda _s: None, rng=lambda: 0.0),
    )
    agent = agent_cls(
        client=client,
        prompt_loader=PromptLoader(PROMPTS_DIR),
        model=MODEL,
        cost_tracker=CostTracker(tier="cost", batch=True),
    )
    return agent, provider


# ── Helpers de parsing ─────────────────────────────────────────────────────


def test_strip_code_fences():
    assert strip_code_fences("```python\ndef test(): pass\n```") == "def test(): pass"
    assert strip_code_fences("def test(): pass") == "def test(): pass"


def test_extract_json_from_fenced_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('resposta: {"a": 1} fim') == '{"a": 1}'


# ── Generator ──────────────────────────────────────────────────────────────


def test_generator_returns_tests_and_targets():
    code = "```python\nimport calc\ndef test_soma():\n    assert calc.soma(1,2)==3\n```"
    agent, provider = _make_agent(TestGeneratorAgent, code)
    analysis = CodeAnalysis(symbols=[SymbolAnalysis(name="soma"), SymbolAnalysis(name="divide")])
    result = agent.generate(source_code="def soma(a,b): return a+b", analysis=analysis)

    assert "def test_soma" in result.test_code
    assert "```" not in result.test_code
    assert result.target_functions == ["soma", "divide"]
    # AD-15: código-fonte vai no prefixo cacheável.
    assert "def soma(a,b)" in provider.last_request.cacheable_prefix


def test_generator_includes_feedback_in_reflexion():
    agent, provider = _make_agent(TestGeneratorAgent, "def test_x(): pass")
    feedback = CriticFeedback(summary="faltam casos de divisão por zero")
    agent.generate(source_code="src", feedback=feedback)
    assert "divisão por zero" in provider.last_request.dynamic_suffix


def test_generator_without_analysis_has_no_targets():
    agent, _ = _make_agent(TestGeneratorAgent, "def test_x(): pass")
    result = agent.generate(source_code="src")
    assert result.target_functions == []


# ── Analyzer ───────────────────────────────────────────────────────────────


def test_analyzer_parses_json():
    payload = (
        '{"symbols": [{"name": "soma", "complexity": 2}], "dependencies_to_mock": ["requests"]}'
    )
    agent, _ = _make_agent(CodeAnalyzerAgent, payload)
    analysis = agent.analyze(source_code="def soma(a,b): return a+b")
    assert analysis.symbols[0].name == "soma"
    assert analysis.dependencies_to_mock == ["requests"]


def test_analyzer_invalid_json_raises_agent_error():
    # AD-02: ValidationError vira falha de iteração (AgentOutputError), não crash.
    agent, _ = _make_agent(CodeAnalyzerAgent, "não é json {quebrado")
    with pytest.raises(AgentOutputError):
        agent.analyze(source_code="src")


# ── Repair ─────────────────────────────────────────────────────────────────


def test_repair_returns_fixed_code():
    agent, provider = _make_agent(RepairAgent, "import calc\ndef test_ok(): assert True")
    result = agent.repair(
        source_code="src", current_tests="def test_broken(: pass", error_summary="SyntaxError"
    )
    assert "def test_ok" in result.test_code
    assert "SyntaxError" in provider.last_request.dynamic_suffix


# ── Critic ─────────────────────────────────────────────────────────────────


def test_critic_parses_feedback():
    payload = """
    {"uncovered_lines": [10],
     "surviving_mutants": ["m1"],
     "suggestions": [{"kind": "coverage_gap", "target": "linha 10",
                      "suggestion": "testar entrada nula"}],
     "summary": "faltam casos de borda"}
    """
    agent, _ = _make_agent(CriticAgent, payload)
    fb = agent.critique(
        source_code="src", current_tests="...", uncovered_lines=[10], surviving_mutants=["m1"]
    )
    assert fb.suggestions[0].kind == "coverage_gap"


def test_critic_rejects_code_output_ad14():
    # AD-14: se o LLM tentar devolver código, o schema rejeita → AgentOutputError.
    payload = '{"summary": "def test_x(): assert True", "suggestions": []}'
    agent, _ = _make_agent(CriticAgent, payload)
    with pytest.raises(AgentOutputError):
        agent.critique(
            source_code="src", current_tests="...", uncovered_lines=[], surviving_mutants=[]
        )


# ── Comum ──────────────────────────────────────────────────────────────────


def test_agents_expose_version_tag_and_record_cost():
    agent, _ = _make_agent(TestGeneratorAgent, "def test_x(): pass")
    assert agent.version_tag.startswith("generator.v")
    agent.generate(source_code="src")
    assert agent.cost_tracker.run_cost > 0
