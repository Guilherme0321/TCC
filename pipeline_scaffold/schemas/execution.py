"""Schema ExecutionResult — saída do test_executor (AD-02).

Carrega pass/fail por teste, cobertura line e branch, mutation score, stdout/stderr,
tempo e sinalização de erro de execução (para o RepairAgent, AD-14) e de timeout
(AD-06). Acomoda runs incompletos com métricas parciais (AD-08).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TestOutcomeKind = Literal["passed", "failed", "error", "skipped"]


class TestOutcome(BaseModel):
    """Resultado de um único teste."""

    __test__ = False  # não é uma classe de teste do pytest

    name: str
    outcome: TestOutcomeKind
    message: str = ""


class ExecutionResult(BaseModel):
    """Resultado completo da execução de uma suite em container efêmero."""

    # Contagens agregadas.
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    tests: list[TestOutcome] = Field(default_factory=list)

    # Cobertura (percentual 0–100) e mutação.
    line_coverage: float | None = None
    branch_coverage: float | None = None
    uncovered_lines: list[int] = Field(default_factory=list)

    mutation_score: float | None = None  # killed / total, 0–1
    mutants_total: int | None = None
    mutants_killed: int | None = None
    surviving_mutants: list[str] = Field(default_factory=list)

    # Saída bruta e tempo.
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.0

    # Sinalização de falha.
    timed_out: bool = False
    # Resumo do traceback quando a suite nem chegou a rodar (input do Repair).
    execution_error: str | None = None
    # Por que a mutação não produziu métrica (AD-21). `mutation_score=None` era
    # indistinguível de "não tentou": os 4 runs do piloto vieram null porque o
    # mutmut morria com Errno 30 sob o fs read-only, e nada no registro dizia
    # isso. Sem este campo, uma coleta inteira sai com o CPKM vazio e sem erro.
    mutation_error: str | None = None

    @property
    def has_execution_error(self) -> bool:
        """True quando testes não executaram (erro de sintaxe/import/setup)."""
        return self.execution_error is not None or self.errors > 0

    @property
    def all_passed(self) -> bool:
        """True quando há testes e nenhum falhou/errou."""
        ran = self.passed + self.failed + self.errors
        return ran > 0 and self.failed == 0 and self.errors == 0
