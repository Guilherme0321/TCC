"""RepairAgent — trata ERROS DE EXECUÇÃO (A2, A4), AD-14.

Input: testes atuais + traceback resumido. Output: testes que executam. Não observa
cobertura nem mutantes (isso é do Critic). Emite código Python bruto.
"""

from __future__ import annotations

from agents.base import BaseAgent, strip_code_fences
from config import MAX_OUTPUT_TOKENS
from schemas.tests_gen import GeneratedTests


class RepairAgent(BaseAgent):
    prompt_name = "repair"

    def repair(
        self,
        *,
        source_code: str,
        current_tests: str,
        error_summary: str,
        target_functions: list[str] | None = None,
        max_tokens: int = MAX_OUTPUT_TOKENS["repair"],
    ) -> GeneratedTests:
        dynamic = self.prompt.render(
            current_tests=current_tests,
            error_summary=error_summary or "erro de execução sem detalhes capturados",
        )
        response = self._complete(
            source_code=source_code, dynamic_suffix=dynamic, max_tokens=max_tokens
        )
        return GeneratedTests(
            test_code=strip_code_fences(response.text),
            target_functions=target_functions or [],
        )
