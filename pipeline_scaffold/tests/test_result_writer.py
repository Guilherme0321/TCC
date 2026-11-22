"""Testes da escrita JSONL e retomada idempotente (AD-10, AD-12)."""

import pytest

from infra.result_writer import ResultWriter, finalize_run
from schemas.run_record import RunRecord, build_run_key


def _record(arch, model, project, rep, status):
    return RunRecord(
        run_key=build_run_key(arch, model, project, rep),
        architecture=arch,
        model=model,
        tier="cost",
        project=project,
        repetition=rep,
        status=status,
    )


def test_append_and_read(tmp_path):
    writer = ResultWriter(tmp_path / "out" / "results.jsonl")
    writer.append(_record("A1", "gpt-5-mini", "p", 1, "completed"))
    writer.append(_record("A2", "gpt-5-mini", "p", 1, "failed"))
    assert len(writer.read_all()) == 2


def test_completed_keys_only_definitive(tmp_path):
    writer = ResultWriter(tmp_path / "results.jsonl")
    writer.append(_record("A1", "m", "p", 1, "completed"))
    writer.append(_record("A2", "m", "p", 1, "failed"))
    writer.append(_record("A3", "m", "p", 1, "aborted_budget"))
    writer.append(_record("A4", "m", "p", 1, "timeout"))
    writer.append(_record("A1", "m", "p", 2, "crashed"))  # NÃO é definitivo (AD-10)

    keys = writer.completed_keys()
    assert build_run_key("A1", "m", "p", 1) in keys
    assert build_run_key("A2", "m", "p", 1) in keys
    assert build_run_key("A3", "m", "p", 1) in keys
    assert build_run_key("A4", "m", "p", 1) in keys
    assert build_run_key("A1", "m", "p", 2) not in keys  # crashed é reexecutado


def test_completed_keys_missing_file(tmp_path):
    assert ResultWriter(tmp_path / "nope.jsonl").completed_keys() == set()


def test_completed_keys_skips_malformed(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text(
        '{"run_key": "A1__m__p__rep-1", "status": "completed"}\nlinha corrompida não-json\n',
        encoding="utf-8",
    )
    keys = ResultWriter(path).completed_keys()
    assert keys == {"A1__m__p__rep-1"}


def test_finalize_run_writes_once_on_success(tmp_path):
    writer = ResultWriter(tmp_path / "results.jsonl")
    rec = _record("A1", "m", "p", 1, "crashed")  # começa não-final
    with finalize_run(writer, rec) as r:
        r.status = "completed"
        r.converged = True
    records = writer.read_all()
    assert len(records) == 1
    assert records[0]["status"] == "completed"


def test_finalize_run_captures_crash_with_partial_cost(tmp_path):
    # AD-12: crash não-tratado grava status=crashed com custo parcial e re-levanta.
    writer = ResultWriter(tmp_path / "results.jsonl")
    rec = _record("A4", "m", "p", 1, "crashed")

    with pytest.raises(RuntimeError):
        with finalize_run(writer, rec) as r:
            r.cost_usd = 0.12  # custo parcial acumulado antes do crash
            raise RuntimeError("boom")

    records = writer.read_all()
    assert len(records) == 1
    assert records[0]["status"] == "crashed"
    assert records[0]["cost_usd"] == 0.12
    assert "boom" in records[0]["failure_reason"]


def test_finalize_run_keeps_final_status_on_late_exception(tmp_path):
    # Se já finalizou (status definitivo) e algo estoura depois, mantém o status.
    writer = ResultWriter(tmp_path / "results.jsonl")
    rec = _record("A1", "m", "p", 1, "crashed")
    with pytest.raises(RuntimeError):
        with finalize_run(writer, rec) as r:
            r.status = "failed"
            raise RuntimeError("late")
    records = writer.read_all()
    assert records[0]["status"] == "failed"
