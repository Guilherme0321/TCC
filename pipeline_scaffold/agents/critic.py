"""CriticAgent — trata INSUFICIÊNCIA DE QUALIDADE (A4), AD-14.

Input: métricas (linhas não cobertas, mutantes sobreviventes) + resumo filtrado do
histórico (janela de 2 iterações, AD-03). Output: feedback textual estruturado —
NUNCA código (garantido pelo schema CriticFeedback). O Critic descreve o gap; o
Generator escreve o teste.
"""

from __future__ import annotations

from agents.base import BaseAgent
from config import MAX_OUTPUT_TOKENS, REFLEXION_MEMORY_WINDOW
from schemas.feedback import CriticFeedback


class CriticAgent(BaseAgent):
    prompt_name = "critic"

    def critique(
        self,
        *,
        source_code: str,
        current_tests: str,
        uncovered_lines: list[int],
        surviving_mutants: list[str],
        history_summary: str = "",
        max_tokens: int = MAX_OUTPUT_TOKENS["critic"],
    ) -> CriticFeedback:
        dynamic = self.prompt.render(
            current_tests=current_tests,
            uncovered_lines=", ".join(map(str, uncovered_lines)) or "nenhuma",
            surviving_mutants=", ".join(surviving_mutants) or "nenhum",
            memory_window=REFLEXION_MEMORY_WINDOW,
            history_summary=history_summary or "(primeira iteração)",
        )
        response = self._complete(
            source_code=source_code, dynamic_suffix=dynamic, max_tokens=max_tokens
        )
        return self._validate_json(CriticFeedback, response.text)
