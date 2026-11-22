"""Schema CodeAnalysis — saída do CodeAnalyzerAgent (AD-02).

Usado por A3 e A4. Validado com `model_validate_json()` sobre a saída do LLM;
uma `ValidationError` é tratada como falha de iteração, não crash (AD-02).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SymbolAnalysis(BaseModel):
    """Uma função, método ou classe alvo de teste."""

    name: str
    kind: Literal["function", "method", "class"] = "function"
    signature: str = ""
    # Complexidade ciclomática individual. O LLM pode estimar; o analyzer
    # sobrescreve com o valor real do radon quando disponível (AD-02).
    complexity: int = Field(default=0, ge=0)
    edge_cases: list[str] = Field(default_factory=list)


class CodeAnalysis(BaseModel):
    """Análise estrutural do código-fonte a ser testado."""

    symbols: list[SymbolAnalysis] = Field(default_factory=list)
    # Dependências externas que os testes precisarão mockar.
    dependencies_to_mock: list[str] = Field(default_factory=list)
    notes: str = ""
