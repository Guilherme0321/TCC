"""TestGeneratorAgent — único autor de testes (todas as arquiteturas, AD-14).

Gera a suite a partir do código-fonte, opcionalmente orientado pela análise (A3/A4)
e pelo feedback do Critic na re-geração do loop Reflexion (A4). Emite código Python
bruto; o agente o embrulha em GeneratedTests.
"""

from __future__ import annotations

from agents.base import BaseAgent, strip_code_fences
from config import MAX_OUTPUT_TOKENS
from schemas.analysis import CodeAnalysis
from schemas.feedback import CriticFeedback
from schemas.tests_gen import GeneratedTests


def _analysis_section(analysis: CodeAnalysis | None) -> str:
    if analysis is None or not analysis.symbols:
        return ""
    lines = ["\nAnálise do código (use para priorizar casos de teste):"]
    for s in analysis.symbols:
        edge = f" — bordas: {', '.join(s.edge_cases)}" if s.edge_cases else ""
        lines.append(f"- {s.kind} {s.name}{s.signature} (complexidade {s.complexity}){edge}")
    if analysis.dependencies_to_mock:
        lines.append(f"Dependências a mockar: {', '.join(analysis.dependencies_to_mock)}")
    return "\n".join(lines) + "\n"


def _feedback_section(feedback: CriticFeedback | None) -> str:
    if feedback is None:
        return ""
    lines = ["\nA versão anterior dos testes passou, mas tinha qualidade insuficiente."]
    if feedback.summary:
        lines.append(f"Resumo do crítico: {feedback.summary}")
    for sug in feedback.suggestions:
        lines.append(f"- ({sug.kind}) {sug.target}: {sug.suggestion}")
    lines.append("Gere uma nova suite que cubra esses gaps, mantendo os testes que já existiam.")
    return "\n".join(lines) + "\n"


class TestGeneratorAgent(BaseAgent):
    prompt_name = "generator"

    def generate(
        self,
        *,
        source_code: str,
        analysis: CodeAnalysis | None = None,
        feedback: CriticFeedback | None = None,
        max_tokens: int = MAX_OUTPUT_TOKENS["generator"],
    ) -> GeneratedTests:
        dynamic = self.prompt.render(
            analysis_section=_analysis_section(analysis),
            feedback_section=_feedback_section(feedback),
        )
        response = self._complete(
            source_code=source_code, dynamic_suffix=dynamic, max_tokens=max_tokens
        )
        code = strip_code_fences(response.text)
        targets = [s.name for s in analysis.symbols] if analysis else []
        return GeneratedTests(test_code=code, target_functions=targets)
