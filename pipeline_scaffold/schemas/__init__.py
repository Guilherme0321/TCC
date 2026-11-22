"""Contratos Pydantic entre agentes e para o JSONL (AD-02)."""

from schemas.analysis import CodeAnalysis, SymbolAnalysis
from schemas.execution import ExecutionResult, TestOutcome
from schemas.feedback import CriticFeedback, TestSuggestion
from schemas.run_record import RunRecord, RunStatus, TokenUsage, build_run_key
from schemas.tests_gen import GeneratedTests

__all__ = [
    "CodeAnalysis",
    "SymbolAnalysis",
    "GeneratedTests",
    "ExecutionResult",
    "TestOutcome",
    "CriticFeedback",
    "TestSuggestion",
    "RunRecord",
    "RunStatus",
    "TokenUsage",
    "build_run_key",
]
