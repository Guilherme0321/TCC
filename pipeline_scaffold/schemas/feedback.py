"""Schema CriticFeedback — saída do CriticAgent (AD-02, AD-14).

O Critic descreve o gap de qualidade; o Generator escreve o código. O Critic
JAMAIS emite Python (AD-14). Um validador leve rejeita feedback que pareça código,
tratando a violação como falha de iteração (não crash).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Marcadores fortes de código Python em texto que deveria ser linguagem natural.
_CODE_SMELL = re.compile(
    r"(^|\n)\s*(def |class |import |from \w+ import |assert |@pytest|"
    r"def test_|self\.assert)",
)


def _reject_code(value: str, field: str) -> str:
    if _CODE_SMELL.search(value):
        raise ValueError(f"CriticFeedback.{field} contém código Python (viola AD-14)")
    return value


class TestSuggestion(BaseModel):
    """Descrição textual de que tipo de teste atacaria um gap. Sem código."""

    __test__ = False  # não é uma classe de teste do pytest

    kind: Literal["coverage_gap", "surviving_mutant"]
    target: str  # linha(s) não coberta(s) ou mutante sobrevivente
    suggestion: str  # em linguagem natural: que caso de teste cobriria isso

    @field_validator("suggestion")
    @classmethod
    def _no_code(cls, v: str) -> str:
        return _reject_code(v, "suggestion")


class CriticFeedback(BaseModel):
    """Feedback estruturado de qualidade que realimenta o Generator (Reflexion)."""

    uncovered_lines: list[int] = Field(default_factory=list)
    surviving_mutants: list[str] = Field(default_factory=list)
    suggestions: list[TestSuggestion] = Field(default_factory=list)
    summary: str = ""

    @field_validator("summary")
    @classmethod
    def _no_code(cls, v: str) -> str:
        return _reject_code(v, "summary")
