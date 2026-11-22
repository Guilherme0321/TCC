"""CodeAnalyzerAgent — análise estrutural do código (A3, A4).

Saída validada no schema CodeAnalysis (AD-02). Se o `ProjectContext` traz a
complexidade ciclomática do radon, ela pode sobrescrever a estimativa do LLM.
"""

from __future__ import annotations

from agents.base import BaseAgent
from config import MAX_OUTPUT_TOKENS
from schemas.analysis import CodeAnalysis


class CodeAnalyzerAgent(BaseAgent):
    prompt_name = "analyzer"

    def analyze(
        self, *, source_code: str, max_tokens: int = MAX_OUTPUT_TOKENS["analyzer"]
    ) -> CodeAnalysis:
        response = self._complete(
            source_code=source_code, dynamic_suffix=self.prompt.render(), max_tokens=max_tokens
        )
        return self._validate_json(CodeAnalysis, response.text)
