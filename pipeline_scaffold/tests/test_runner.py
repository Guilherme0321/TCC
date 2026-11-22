"""Testes do runner: plano, idempotência, tier e loop de execução (Fase 5)."""

from pathlib import Path

from architectures.common import new_record
from infra.cost_tracker import GlobalBudget, GlobalBudgetExceeded
from infra.llm_client import LLMClient
from infra.project_loader import ProjectContext
from runner import ExperimentRunner, RunSpec, build_plan, iter_models
from schemas.run_record import build_run_key

# ── Plano e idempotência ───────────────────────────────────────────────────


def test_iter_models_by_tier():
    cost = iter_models("cost")
    frontier = iter_models("frontier")
    assert "gpt-5-mini" in cost
    assert "claude-opus-4-8" in frontier
    assert set(cost).isdisjoint(frontier)


def test_build_plan_full_cartesian():
    plan = build_plan(tier="cost", projects=["p1", "p2"], architectures=["A1", "A2"], repetitions=5)
    n_models = len(iter_models("cost"))
    assert len(plan) == 5 * 2 * 2 * n_models


def test_build_plan_repetition_outermost():
    # Os primeiros runs cobrem todas as células em rep-1 (útil p/ piloto com --limit).
    plan = build_plan(tier="cost", projects=["p1"], architectures=["A1", "A2"], repetitions=3)
    per_rep = 2 * len(iter_models("cost"))
    assert all(s.repetition == 1 for s in plan[:per_rep])
    assert plan[per_rep].repetition == 2


def test_build_plan_skips_completed():
    done = build_run_key("A1", "gpt-5-mini", "p1", 1)
    plan = build_plan(
        tier="cost",
        projects=["p1"],
        architectures=["A1"],
        repetitions=1,
        completed={done},
    )
    assert all(s.run_key != done for s in plan)


def test_runspec_run_key():
    spec = RunSpec("A4", "gpt-5-mini", "cost", "proj", 2)
    assert spec.run_key == "A4__gpt-5-mini__proj__rep-2"


# ── Loop de execução ────────────────────────────────────────────────────────


def _project(name="p1"):
    return ProjectContext(
        name=name,
        root=Path("."),
        cov_target="calc",
        source_paths=["calc.py"],
        source_code="def soma(a, b):\n    return a + b\n",
    )


def _make_pipeline(status="completed", *, cost_tokens=(1000, 500), raise_exc=None):
    def _pipeline(ctx):
        if raise_exc is not None:
            raise raise_exc
        inp, out = cost_tokens
        ctx.cost_tracker.record(ctx.model, input_tokens=inp, output_tokens=out)
        rec = new_record(ctx)
        rec.status = status
        rec.cost_usd = ctx.cost_tracker.run_cost
        rec.tokens = ctx.cost_tracker.tokens
        return rec

    return _pipeline


def _runner(tmp_path, pipelines, *, pool_size=1, batch=True):
    return ExperimentRunner(
        results_path=tmp_path / "results.jsonl",
        client=LLMClient(),  # não é chamado pelos pipelines fakes
        executor_factory=lambda ctx: None,
        pipelines=pipelines,
        now_iso=lambda: "2026-07-06T00:00:00",
        batch=batch,
        pool_size=pool_size,
    )


def test_run_writes_records_and_counts(tmp_path):
    runner = _runner(tmp_path, {"A1": _make_pipeline("completed")})
    plan = [RunSpec("A1", "gpt-5-mini", "cost", "p1", 1)]
    counts = runner.run(plan, {"p1": _project()}, global_budget=GlobalBudget())
    assert counts == {"completed": 1}
    assert len(runner.writer.read_all()) == 1


def test_run_resumes_by_skipping_completed(tmp_path):
    runner = _runner(tmp_path, {"A1": _make_pipeline("completed")})
    plan = [RunSpec("A1", "gpt-5-mini", "cost", "p1", 1)]
    runner.run(plan, {"p1": _project()}, global_budget=GlobalBudget())
    # segunda passada: a chave já está concluída → não entra em novo plano
    completed = runner.writer.completed_keys()
    plan2 = build_plan(
        tier="cost", projects=["p1"], architectures=["A1"], repetitions=1, completed=completed
    )
    assert all(s.run_key != "A1__gpt-5-mini__p1__rep-1" for s in plan2)


def test_run_crash_is_recorded_and_continues(tmp_path):
    # AD-12: crash vira registro 'crashed' com custo parcial; segue para o próximo.
    pipelines = {
        "A1": _make_pipeline(raise_exc=RuntimeError("boom")),
        "A2": _make_pipeline("completed"),
    }
    runner = _runner(tmp_path, pipelines)
    plan = [
        RunSpec("A1", "gpt-5-mini", "cost", "p1", 1),
        RunSpec("A2", "gpt-5-mini", "cost", "p1", 1),
    ]
    counts = runner.run(plan, {"p1": _project()}, global_budget=GlobalBudget())
    assert counts.get("crashed") == 1
    assert counts.get("completed") == 1
    statuses = {r["status"] for r in runner.writer.read_all()}
    assert statuses == {"crashed", "completed"}


def test_run_global_budget_pauses(tmp_path):
    # AD-09: breaker global → registra aborted_budget e PARA (não executa o resto).
    def _explode(ctx):
        raise GlobalBudgetExceeded("teto global atingido")

    runner = _runner(tmp_path, {"A1": _explode, "A2": _make_pipeline("completed")})
    plan = [
        RunSpec("A1", "gpt-5-mini", "cost", "p1", 1),
        RunSpec("A2", "gpt-5-mini", "cost", "p1", 1),
    ]
    counts = runner.run(plan, {"p1": _project()}, global_budget=GlobalBudget())
    assert counts.get("aborted_budget") == 1
    assert "completed" not in counts  # parou antes do segundo run
    assert len(runner.writer.read_all()) == 1


def test_pool_runs_all_and_writes_every_record(tmp_path):
    # AD-15: com pool, N runs ficam em voo para alimentar o coalescer de batch.
    runner = _runner(tmp_path, {"A1": _make_pipeline("completed")}, pool_size=4)
    plan = [RunSpec("A1", "gpt-5-mini", "cost", "p1", rep) for rep in range(1, 9)]
    counts = runner.run(plan, {"p1": _project()}, global_budget=GlobalBudget())
    assert counts == {"completed": 8}
    assert len(runner.writer.read_all()) == 8


def test_pool_global_budget_stops_scheduling_new_runs(tmp_path):
    # AD-09 sob concorrência: o breaker impede NOVOS runs; os já em voo terminam.
    # Com pool=1 a semântica serial estrita é preservada (teste acima).
    started = []

    def _explode(ctx):
        started.append(ctx.repetition)
        raise GlobalBudgetExceeded("teto global atingido")

    runner = _runner(tmp_path, {"A1": _explode}, pool_size=2)
    plan = [RunSpec("A1", "gpt-5-mini", "cost", "p1", rep) for rep in range(1, 21)]
    counts = runner.run(plan, {"p1": _project()}, global_budget=GlobalBudget())
    # Muito menos que os 20 planejados: o breaker cortou o agendamento.
    assert counts.get("aborted_budget", 0) < len(plan)
    assert len(started) < len(plan)


def test_no_batch_forces_serial_and_no_coalescer(tmp_path):
    # Sem Batch API não há o que agrupar: volta ao caminho serial síncrono, e o
    # cost_tracker passa a cobrar preço cheio (AD-15).
    runner = _runner(tmp_path, {"A1": _make_pipeline("completed")}, batch=False, pool_size=8)
    assert runner.pool_size == 1
    assert runner.coalescer is None
    assert runner.batch is False


def test_batch_enabled_builds_coalescer(tmp_path):
    runner = _runner(tmp_path, {"A1": _make_pipeline("completed")}, pool_size=4)
    assert runner.coalescer is not None
    assert runner.pool_size == 4


def test_run_skips_missing_project(tmp_path):
    runner = _runner(tmp_path, {"A1": _make_pipeline("completed")})
    plan = [RunSpec("A1", "gpt-5-mini", "cost", "ausente", 1)]
    counts = runner.run(plan, {}, global_budget=GlobalBudget())
    assert counts == {}


def test_run_stamps_finished_at_and_duration(tmp_path):
    # Todo registro escrito pelo runner carrega finished_at/duration_sec (AD-12).
    times = iter(["2026-07-06T00:00:00+00:00", "2026-07-06T00:00:42+00:00"])
    runner = ExperimentRunner(
        results_path=tmp_path / "results.jsonl",
        client=LLMClient(),
        executor_factory=lambda ctx: None,
        pipelines={"A1": _make_pipeline("completed")},
        now_iso=lambda: next(times),
    )
    plan = [RunSpec("A1", "gpt-5-mini", "cost", "p1", 1)]
    runner.run(plan, {"p1": _project()}, global_budget=GlobalBudget())
    [rec] = runner.writer.read_all()
    assert rec["finished_at"] == "2026-07-06T00:00:42+00:00"
    assert rec["duration_sec"] == 42.0


def test_run_crash_record_is_stamped(tmp_path):
    runner = _runner(tmp_path, {"A1": _make_pipeline(raise_exc=RuntimeError("boom"))})
    plan = [RunSpec("A1", "gpt-5-mini", "cost", "p1", 1)]
    runner.run(plan, {"p1": _project()}, global_budget=GlobalBudget())
    [rec] = runner.writer.read_all()
    assert rec["status"] == "crashed"
    assert rec["finished_at"] == "2026-07-06T00:00:00"
    assert rec["duration_sec"] == 0.0


def test_run_aborted_budget_record_is_stamped(tmp_path):
    def _explode(ctx):
        raise GlobalBudgetExceeded("teto global atingido")

    runner = _runner(tmp_path, {"A1": _explode})
    plan = [RunSpec("A1", "gpt-5-mini", "cost", "p1", 1)]
    runner.run(plan, {"p1": _project()}, global_budget=GlobalBudget())
    [rec] = runner.writer.read_all()
    assert rec["status"] == "aborted_budget"
    assert rec["finished_at"] == "2026-07-06T00:00:00"
    assert rec["duration_sec"] == 0.0


# ── CLI: gates de segurança (AD-15) ────────────────────────────────────────


def test_main_dry_run_makes_no_calls_and_writes_nothing(tmp_path, monkeypatch, capsys):
    import runner as runner_mod

    monkeypatch.setattr(runner_mod, "load_projects", lambda: {"p1": _project()})
    results = tmp_path / "results.jsonl"
    assert runner_mod.main(["--tier", "cost", "--results", str(results)]) == 0
    assert not results.exists()
    assert "[DRY-RUN]" in capsys.readouterr().out


def test_main_frontier_execute_requires_confirm(tmp_path, monkeypatch, capsys):
    # AD-15: frontier + --execute sem --confirm-frontier é recusado antes de
    # instanciar qualquer cliente de API.
    import runner as runner_mod

    monkeypatch.setattr(runner_mod, "load_projects", lambda: {"p1": _project()})
    results = tmp_path / "results.jsonl"
    code = runner_mod.main(["--tier", "frontier", "--execute", "--results", str(results)])
    assert code == 2
    assert not results.exists()
    assert "--confirm-frontier" in capsys.readouterr().err
