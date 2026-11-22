"""Testes do test_executor: parsers puros + integração local (Fase 1, AD-04, AD-06)."""

from pathlib import Path

from infra.test_executor import (
    CommandResult,
    DockerRunner,
    LocalRunner,
    Runner,
    TestExecutor,
    mutation_score_from_stats,
    parse_coverage_json,
    parse_junit_xml,
    parse_mutmut_stats,
    parse_mutmut_survivors,
)
from schemas.execution import ExecutionResult

FIXTURE = Path(__file__).parent / "fixtures" / "sample_project"


# ── Parsers puros ─────────────────────────────────────────────────────────


def test_parse_junit_counts_outcomes():
    xml = """<?xml version="1.0"?>
    <testsuite tests="4" failures="1" errors="1" skipped="1">
      <testcase name="test_ok"/>
      <testcase name="test_bad"><failure message="assert">detail</failure></testcase>
      <testcase name="test_err"><error message="boom">tb</error></testcase>
      <testcase name="test_skip"><skipped message="wip"/></testcase>
    </testsuite>"""
    outcomes, counts = parse_junit_xml(xml)
    assert counts == {"passed": 1, "failed": 1, "errors": 1, "skipped": 1}
    assert {o.name: o.outcome for o in outcomes}["test_bad"] == "failed"


def test_parse_junit_malformed_is_safe():
    outcomes, counts = parse_junit_xml("<not-xml")
    assert outcomes == []
    assert counts["passed"] == 0


def test_parse_coverage_json():
    payload = """
    {
      "files": {
        "calc.py": {"missing_lines": [10, 11]}
      },
      "totals": {
        "num_statements": 10, "covered_lines": 8,
        "num_branches": 4, "covered_branches": 3
      }
    }
    """
    line, branch, uncovered = parse_coverage_json(payload)
    assert line == 80.0
    assert branch == 75.0
    assert uncovered == [10, 11]


def test_parse_coverage_json_no_branch():
    line, branch, _ = parse_coverage_json('{"totals": {"num_statements": 5, "covered_lines": 5}}')
    assert line == 100.0
    assert branch is None


def test_parse_mutmut_stats_from_progress_line():
    # Formato do mutmut 3.6 (print_stats).
    out = "some noise\n2/3  🎉 2 🫥 0  ⏰ 0  🤔 0  🙁 1  🔇 0  🧙 0\nmore\n"
    stats = parse_mutmut_stats(out)
    assert stats == {"killed": 2, "no_tests": 0, "timeout": 0, "suspicious": 0, "survived": 1}


def test_parse_mutmut_stats_uses_last_line():
    out = "1/3  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 2\n3/3  🎉 3 🫥 0  ⏰ 0  🤔 0  🙁 0\n"
    stats = parse_mutmut_stats(out)
    assert stats["killed"] == 3
    assert stats["survived"] == 0


def test_parse_mutmut_stats_none_when_absent():
    assert parse_mutmut_stats("nenhuma estatística aqui") is None


def test_mutation_score():
    score, killed, total = mutation_score_from_stats(
        {"killed": 3, "survived": 1, "timeout": 0, "suspicious": 0, "no_tests": 5}
    )
    assert killed == 3
    assert total == 4  # no_tests fica de fora
    assert score == 0.75


def test_parse_mutmut_survivors():
    out = "    calc.divide__mutmut_1: survived\n    calc.soma__mutmut_2: killed\n"
    assert parse_mutmut_survivors(out) == ["calc.divide__mutmut_1"]


# ── DockerRunner: comando endurecido (AD-04), sem executar ────────────────


def test_docker_command_has_hardening_flags():
    runner = DockerRunner("tcc-proj")
    cmd = runner.build_command(["python", "-m", "pytest"], workdir=Path("/tmp/wd"))
    assert "--rm" in cmd
    assert "--network=none" in cmd
    assert "--read-only" in cmd
    assert "--cpus=2" in cmd
    assert "--memory=2g" in cmd
    assert "-u" in cmd and "1000:1000" in cmd
    assert "tcc-proj" in cmd
    # o comando do usuário vem por último
    assert cmd[-3:] == ["python", "-m", "pytest"]


# ── Integração local (sem Docker), pytest + coverage reais ────────────────


def _executor(run_mutation=False):
    return TestExecutor(
        LocalRunner(project_path=FIXTURE), cov_target="calc", run_mutation=run_mutation
    )


def test_integration_passing_suite():
    code = (
        "import calc\n"
        "def test_soma():\n    assert calc.soma(1, 2) == 3\n"
        "def test_divide():\n    assert calc.divide(6, 2) == 3\n"
    )
    res = _executor().run(code)
    assert res.passed == 2
    assert res.failed == 0
    assert res.all_passed
    assert res.line_coverage is not None and res.line_coverage > 0
    assert res.branch_coverage is not None
    assert not res.has_execution_error


def test_integration_failing_suite():
    code = "import calc\ndef test_wrong():\n    assert calc.soma(1, 1) == 3\n"
    res = _executor().run(code)
    assert res.failed == 1
    assert not res.all_passed


def test_integration_import_error_sets_execution_error():
    # AD-14: erro de execução → execution_error preenchido (input do Repair).
    code = "import inexistente_xyz\ndef test_x():\n    assert True\n"
    res = _executor().run(code)
    assert res.has_execution_error
    assert res.execution_error


# ── Mutação sob o filesystem read-only de AD-04 (AD-21) ───────────────────


class _RecordingRunner(Runner):
    """Registra os comandos e devolve saída fixa, sem executar nada."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.calls: list[tuple[list[str], Path]] = []
        self.stdout = stdout
        self.returncode = returncode

    def run(self, args, *, workdir, timeout, env=None):
        self.calls.append((args, Path(workdir)))
        return CommandResult(self.returncode, self.stdout, "", timed_out=False)


def _mutation_executor(runner, project_root, sources):
    return TestExecutor(
        runner,
        cov_target="calc",
        run_mutation=True,
        mutmut_source_paths=sources,
        project_root=project_root,
    )


def test_sources_copiados_para_o_workdir_gravavel(tmp_path):
    """AD-21: mutar uma CÓPIA em /work, porque /project é read-only sob AD-04."""
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("X = 1\n", encoding="utf-8")

    runner = _RecordingRunner()
    ex = _mutation_executor(runner, project, ["pkg"])
    workdir = tmp_path / "work"
    workdir.mkdir()

    assert ex._materialize_sources(workdir) is None
    assert (workdir / "pkg" / "__init__.py").read_text(encoding="utf-8") == "X = 1\n"


def test_source_path_ausente_vira_mutation_error_nao_silencio(tmp_path):
    """Falha de mutação precisa ficar VISÍVEL no registro (lição do AD-21)."""
    runner = _RecordingRunner()
    ex = _mutation_executor(runner, tmp_path, ["nao_existe"])
    result = ExecutionResult()

    ex._apply_mutation_results(result, tmp_path / "work")

    assert result.mutation_score is None
    assert result.mutation_error and "não existe" in result.mutation_error
    assert runner.calls == []  # nem tentou rodar o mutmut


def test_mutmut_sem_linha_de_progresso_registra_o_motivo(tmp_path):
    """O piloto saiu com `mutation_score=None` e NENHUM erro em log (AD-21).

    Era indistinguível de "não tentou". Sem esta mensagem, uma coleta de 440
    runs produziria a coluna do CPKM inteira vazia sem sinal nenhum de falha.
    """
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("X = 1\n", encoding="utf-8")

    runner = _RecordingRunner(stdout="OSError: [Errno 30] Read-only file system", returncode=1)
    ex = _mutation_executor(runner, project, ["pkg"])
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = ExecutionResult()

    ex._apply_mutation_results(result, workdir)

    assert result.mutation_score is None
    assert result.mutation_error and "Errno 30" in result.mutation_error


def test_integration_timeout(monkeypatch):
    # AD-06: suite que trava vira timed_out, não crash.
    ex = TestExecutor(
        LocalRunner(project_path=FIXTURE),
        cov_target="calc",
        run_mutation=False,
        pytest_timeout=1,
    )
    code = "import time\ndef test_hang():\n    time.sleep(30)\n"
    res = ex.run(code)
    assert res.timed_out
    assert res.execution_error
