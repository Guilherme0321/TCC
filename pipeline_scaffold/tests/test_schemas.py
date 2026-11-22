"""Testes dos contratos Pydantic (AD-02)."""

import pytest
from pydantic import ValidationError

from schemas import (
    CodeAnalysis,
    CriticFeedback,
    ExecutionResult,
    GeneratedTests,
    RunRecord,
    build_run_key,
)
from schemas.feedback import TestSuggestion


def test_code_analysis_from_llm_json():
    payload = """
    {
      "symbols": [
        {"name": "soma", "kind": "function", "signature": "soma(a, b)",
         "complexity": 2, "edge_cases": ["a negativo"]}
      ],
      "dependencies_to_mock": ["requests"],
      "notes": "ok"
    }
    """
    analysis = CodeAnalysis.model_validate_json(payload)
    assert analysis.symbols[0].name == "soma"
    assert analysis.symbols[0].complexity == 2
    assert analysis.dependencies_to_mock == ["requests"]


def test_code_analysis_defaults_are_lenient():
    analysis = CodeAnalysis.model_validate_json('{"symbols": [{"name": "f"}]}')
    assert analysis.symbols[0].kind == "function"
    assert analysis.symbols[0].complexity == 0
    assert analysis.dependencies_to_mock == []


def test_generated_tests_empty_detection():
    assert GeneratedTests(test_code="   \n ").is_empty()
    assert not GeneratedTests(test_code="def test_x(): assert True").is_empty()


def test_execution_result_flags():
    ok = ExecutionResult(passed=3, failed=0, errors=0)
    assert ok.all_passed
    assert not ok.has_execution_error

    broken = ExecutionResult(passed=0, failed=0, errors=1, execution_error="ImportError")
    assert not broken.all_passed
    assert broken.has_execution_error

    empty = ExecutionResult()
    assert not empty.all_passed  # nenhum teste rodou


def test_critic_feedback_valid():
    fb = CriticFeedback(
        uncovered_lines=[10, 11],
        surviving_mutants=["m1"],
        suggestions=[
            TestSuggestion(
                kind="coverage_gap",
                target="linha 10",
                suggestion="testar o ramo em que o valor é None",
            )
        ],
        summary="Faltam casos de borda para entradas nulas.",
    )
    assert fb.suggestions[0].kind == "coverage_gap"


def test_critic_feedback_rejects_code_ad14():
    # AD-14: o Critic jamais emite Python.
    with pytest.raises(ValidationError):
        TestSuggestion(
            kind="coverage_gap",
            target="linha 10",
            suggestion="def test_none(): assert f(None) is None",
        )
    with pytest.raises(ValidationError):
        CriticFeedback(summary="import os\nos.system('x')")


def test_run_key_format():
    key = build_run_key("A4", "claude-opus-4-8", "projeto-x", 3)
    assert key == "A4__claude-opus-4-8__projeto-x__rep-3"


def test_run_record_roundtrip_and_partial():
    # AD-08: run falho é serializável e completo, com métricas parciais.
    rec = RunRecord(
        run_key=build_run_key("A2", "gpt-5-mini", "proj", 1),
        architecture="A2",
        model="gpt-5-mini",
        tier="cost",
        project="proj",
        repetition=1,
        status="failed",
        converged=False,
        failure_reason="repair não convergiu",
    )
    rec.tokens.add(input_tokens=8000, output_tokens=3000, cached_input_tokens=5000)
    rec.execution = ExecutionResult(passed=1, failed=2, line_coverage=42.0)
    rec.sync_metrics_from_execution()

    assert rec.line_coverage == 42.0
    assert rec.tokens.n_llm_calls == 1
    # roundtrip JSON completo
    dumped = rec.model_dump_json()
    restored = RunRecord.model_validate_json(dumped)
    assert restored.run_key == rec.run_key
    assert restored.execution.failed == 2


def _record_minimo(started_at=None):
    return RunRecord(
        run_key=build_run_key("A1", "gpt-5-mini", "proj", 1),
        architecture="A1",
        model="gpt-5-mini",
        tier="cost",
        project="proj",
        repetition=1,
        status="completed",
        started_at=started_at,
    )


def test_stamp_finish_deriva_duration_de_started_at():
    rec = _record_minimo(started_at="2026-07-06T00:00:00+00:00")
    rec.stamp_finish("2026-07-06T00:01:30+00:00")
    assert rec.finished_at == "2026-07-06T00:01:30+00:00"
    assert rec.duration_sec == 90.0


def test_stamp_finish_sem_started_at_nao_deriva_duration():
    rec = _record_minimo(started_at=None)
    rec.stamp_finish("2026-07-06T00:01:30+00:00")
    assert rec.finished_at == "2026-07-06T00:01:30+00:00"
    assert rec.duration_sec is None


def test_stamp_finish_timestamp_invalido_nao_derruba_escrita():
    # AD-12: a escrita do registro nunca pode falhar por causa do carimbo.
    rec = _record_minimo(started_at="nao-e-iso")
    rec.stamp_finish("2026-07-06T00:01:30+00:00")
    assert rec.finished_at == "2026-07-06T00:01:30+00:00"
    assert rec.duration_sec is None
