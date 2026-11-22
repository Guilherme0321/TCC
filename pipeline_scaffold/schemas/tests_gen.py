"""Schema GeneratedTests — saída do TestGeneratorAgent e do RepairAgent (AD-02).

O Generator e o Repair emitem código Python bruto (ver prompts). O agente extrai
o código (removendo cercas markdown) e o embrulha neste schema, anexando a lista
de funções-alvo cobertas. O Generator é o único autor de testes (AD-14).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GeneratedTests(BaseModel):
    """Suite de testes pytest gerada por um agente."""

    test_code: str
    target_functions: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.test_code.strip()
