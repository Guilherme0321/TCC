"""Execução isolada de testes gerados: pytest-cov + mutmut em container efêmero.

AD-04: execução sempre em Docker efêmero (fs read-only exceto tempdir, `--network
none`, limites de CPU/memória, usuário sem privilégios). AD-06: dois timeouts
independentes (`PYTEST_TIMEOUT_SEC`, `MUTMUT_TIMEOUT_SEC`); estouro registra
`timed_out=True`, não descarta o run.

O executor separa a ORQUESTRAÇÃO (ciclo de vida do container) da ANÁLISE (parse de
junit.xml, coverage.json e saída do mutmut). Os parsers são puros e testáveis sem
Docker. Duas estratégias de `Runner`:
- `DockerRunner`: caminho de produção (AD-04).
- `LocalRunner`: subprocess local, para teste de integração com fixture mínima.

mutmut 3.6: config em setup.cfg `[mutmut]` com `source_paths` e
`pytest_add_cli_args_test_selection` (plano da Fase 1). Deprecation warning do mutmut
é tratado como warning, nunca como falha (lição do benchmark_selector).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from config import (
    DOCKER_CPU_LIMIT,
    DOCKER_MEMORY_LIMIT,
    DOCKER_NETWORK,
    MUTMUT_TIMEOUT_SEC,
    PYTEST_TIMEOUT_SEC,
)
from schemas.execution import ExecutionResult, TestOutcome

TEST_FILENAME = "test_generated.py"
COVERAGE_FILE = "cov.json"
JUNIT_FILE = "junit.xml"


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


# ── Runners (estratégia de execução) ──────────────────────────────────────


class Runner(ABC):
    """Executa um comando com um workdir de trabalho e devolve saída + timeout."""

    @abstractmethod
    def run(
        self, args: list[str], *, workdir: Path, timeout: int, env: dict | None = None
    ) -> CommandResult: ...


class LocalRunner(Runner):
    """Executa via subprocess local. Para testes de integração sem Docker.

    O projeto é tornado importável via PYTHONPATH (`project_path`), simulando a
    pré-instalação da imagem base.
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = Path(project_path)

    def run(
        self, args: list[str], *, workdir: Path, timeout: int, env: dict | None = None
    ) -> CommandResult:
        import os
        import sys

        # Usa o interpretador atual (venv) para "python".
        if args and args[0] == "python":
            args = [sys.executable, *args[1:]]

        full_env = os.environ.copy()
        existing = full_env.get("PYTHONPATH", "")
        full_env["PYTHONPATH"] = (
            f"{self.project_path}{os.pathsep}{existing}" if existing else str(self.project_path)
        )
        if env:
            full_env.update(env)
        try:
            proc = subprocess.run(
                args,
                cwd=str(workdir),
                env=full_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return CommandResult(proc.returncode, proc.stdout, proc.stderr, timed_out=False)
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                returncode=-1,
                stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
                stderr=exc.stderr or "" if isinstance(exc.stderr, str) else "",
                timed_out=True,
            )


class DockerRunner(Runner):
    """Executa em container efêmero conforme AD-04 (produção)."""

    def __init__(
        self,
        image: str,
        *,
        cpus: str = DOCKER_CPU_LIMIT,
        memory: str = DOCKER_MEMORY_LIMIT,
        network: str = DOCKER_NETWORK,
        user: str = "1000:1000",
        docker_bin: str = "docker",
    ) -> None:
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.network = network
        self.user = user
        self.docker_bin = docker_bin

    def build_command(self, args: list[str], *, workdir: Path) -> list[str]:
        """Monta o `docker run` efêmero e endurecido (AD-04). Extraído para teste."""
        return [
            self.docker_bin,
            "run",
            "--rm",
            f"--network={self.network}",
            f"--cpus={self.cpus}",
            f"--memory={self.memory}",
            "--read-only",  # fs read-only exceto o volume /work e o tmpfs
            "--tmpfs=/tmp",
            "-v",
            f"{workdir}:/work:rw",
            "-w",
            "/work",
            "-u",
            self.user,
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            self.image,
            *args,
        ]

    def run(
        self, args: list[str], *, workdir: Path, timeout: int, env: dict | None = None
    ) -> CommandResult:
        cmd = self.build_command(args, workdir=workdir)
        if env:
            for k, v in env.items():
                cmd[-len(args) - 1 : -len(args) - 1] = ["-e", f"{k}={v}"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return CommandResult(proc.returncode, proc.stdout, proc.stderr, timed_out=False)
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                returncode=-1,
                stdout=exc.stdout if isinstance(exc.stdout, str) else "",
                stderr=exc.stderr if isinstance(exc.stderr, str) else "",
                timed_out=True,
            )


# ── Parsers puros ─────────────────────────────────────────────────────────


def parse_junit_xml(xml_text: str) -> tuple[list[TestOutcome], dict[str, int]]:
    """Extrai desfechos por teste e contagens agregadas de um junit.xml."""
    outcomes: list[TestOutcome] = []
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return outcomes, counts

    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    for suite in suites:
        for case in suite.findall("testcase"):
            name = case.get("name", "?")
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            if error is not None:
                outcome, key = "error", "errors"
                msg = error.get("message", "")
            elif failure is not None:
                outcome, key = "failed", "failed"
                msg = failure.get("message", "")
            elif skipped is not None:
                outcome, key = "skipped", "skipped"
                msg = skipped.get("message", "")
            else:
                outcome, key = "passed", "passed"
                msg = ""
            counts[key] += 1
            outcomes.append(TestOutcome(name=name, outcome=outcome, message=msg[:500]))
    return outcomes, counts


def parse_coverage_json(json_text: str) -> tuple[float | None, float | None, list[int]]:
    """Devolve (line_coverage%, branch_coverage%, linhas não cobertas) do coverage.py."""
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return None, None, []

    totals = data.get("totals", {})
    num_statements = totals.get("num_statements", 0)
    covered_lines = totals.get("covered_lines", 0)
    line_cov = 100.0 * covered_lines / num_statements if num_statements else None

    num_branches = totals.get("num_branches", 0)
    covered_branches = totals.get("covered_branches", 0)
    branch_cov = 100.0 * covered_branches / num_branches if num_branches else None

    uncovered: list[int] = []
    for finfo in data.get("files", {}).values():
        uncovered.extend(finfo.get("missing_lines", []))
    return line_cov, branch_cov, sorted(set(uncovered))


# Linha de progresso do mutmut 3.6 (print_stats), com legenda por emoji:
# 🎉 killed · 🫥 no_tests · ⏰ timeout · 🤔 suspicious · 🙁 survived · 🔇 skipped
_MUTMUT_STATS = re.compile(
    r"🎉\s*(\d+).*?🫥\s*(\d+).*?⏰\s*(\d+).*?🤔\s*(\d+).*?🙁\s*(\d+)",
    re.DOTALL,
)


def parse_mutmut_stats(stdout: str) -> dict[str, int] | None:
    """Extrai as contagens da ÚLTIMA linha de progresso do `mutmut run`."""
    matches = list(_MUTMUT_STATS.finditer(stdout))
    if not matches:
        return None
    killed, no_tests, timeout, suspicious, survived = (int(x) for x in matches[-1].groups())
    return {
        "killed": killed,
        "no_tests": no_tests,
        "timeout": timeout,
        "suspicious": suspicious,
        "survived": survived,
    }


def parse_mutmut_survivors(results_stdout: str) -> list[str]:
    """Extrai as chaves dos mutantes sobreviventes da saída de `mutmut results`."""
    survivors: list[str] = []
    for line in results_stdout.splitlines():
        m = re.match(r"\s*(\S+):\s*survived\s*$", line)
        if m:
            survivors.append(m.group(1))
    return survivors


def mutation_score_from_stats(stats: dict[str, int]) -> tuple[float | None, int, int]:
    """(score, killed, total_significativo). total = killed+survived+timeout+suspicious.

    no_tests/skipped ficam de fora do denominador por não serem mutantes decidíveis.
    """
    killed = stats["killed"]
    total = killed + stats["survived"] + stats["timeout"] + stats["suspicious"]
    score = killed / total if total else None
    return score, killed, total


# ── Orquestrador ──────────────────────────────────────────────────────────


class TestExecutor:
    """Executa uma suite gerada e devolve um ExecutionResult (AD-04, AD-06)."""

    __test__ = False  # não é uma classe de teste do pytest

    def __init__(
        self,
        runner: Runner,
        *,
        cov_target: str,
        run_mutation: bool = True,
        mutmut_source_paths: list[str] | None = None,
        mutate_targets: list[tuple[str, str]] | None = None,
        project_root: Path | None = None,
        pytest_timeout: int = PYTEST_TIMEOUT_SEC,
        mutmut_timeout: int = MUTMUT_TIMEOUT_SEC,
        gate: AbstractContextManager | None = None,
    ) -> None:
        self.runner = runner
        self.cov_target = cov_target
        # Mutação exige saber quais fontes mutar (source_paths do mutmut 3.6) e
        # de onde copiá-los para o workdir gravável (AD-21).
        self.run_mutation = run_mutation and bool(mutmut_source_paths)
        self.mutmut_source_paths = mutmut_source_paths or []
        # Sem pares explícitos, origem e destino coincidem (pacote na raiz).
        self.mutate_targets = mutate_targets or [(p, p) for p in self.mutmut_source_paths]
        self.project_root = Path(project_root) if project_root else None
        self.pytest_timeout = pytest_timeout
        self.mutmut_timeout = mutmut_timeout
        # Com runs concorrentes (AD-15), limita containers simultâneos para o
        # paralelismo não saturar a máquina e contaminar o tempo medido (AD-06).
        self.gate = gate or nullcontext()

    def _pytest_args(self) -> list[str]:
        return [
            "python",
            "-m",
            "pytest",
            TEST_FILENAME,
            f"--cov={self.cov_target}",
            "--cov-branch",
            f"--cov-report=json:{COVERAGE_FILE}",
            f"--junitxml={JUNIT_FILE}",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-q",
        ]

    def run(self, test_code: str) -> ExecutionResult:
        """Escreve a suite, roda pytest-cov e (opcional) mutmut, e agrega resultados."""
        with self.gate:
            return self._run(test_code)

    def _run(self, test_code: str) -> ExecutionResult:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / TEST_FILENAME).write_text(test_code, encoding="utf-8")

            start = time.monotonic()
            pytest_res = self.runner.run(
                self._pytest_args(), workdir=workdir, timeout=self.pytest_timeout
            )
            duration = time.monotonic() - start

            result = ExecutionResult(
                stdout=pytest_res.stdout[-8000:],
                stderr=pytest_res.stderr[-8000:],
                duration_sec=round(duration, 3),
                timed_out=pytest_res.timed_out,
            )

            if pytest_res.timed_out:
                result.execution_error = (
                    f"pytest excedeu {self.pytest_timeout}s (provável loop infinito, AD-06)"
                )
                return result

            self._apply_pytest_results(result, workdir, pytest_res)

            # mutmut só faz sentido se a suite roda e passa (AD-14: qualidade, não erro).
            if self.run_mutation and result.all_passed:
                self._apply_mutation_results(result, workdir)

            return result

    def _apply_pytest_results(
        self, result: ExecutionResult, workdir: Path, pytest_res: CommandResult
    ) -> None:
        junit_path = workdir / JUNIT_FILE
        if junit_path.exists():
            outcomes, counts = parse_junit_xml(junit_path.read_text(encoding="utf-8"))
            result.tests = outcomes
            result.passed = counts["passed"]
            result.failed = counts["failed"]
            result.errors = counts["errors"]
            result.skipped = counts["skipped"]
        else:
            # Sem junit: a suite nem chegou a coletar (erro de import/sintaxe).
            result.errors = 1
            result.execution_error = _collection_error_summary(pytest_res)
            return

        if result.errors > 0 and result.execution_error is None:
            result.execution_error = _collection_error_summary(pytest_res)

        cov_path = workdir / COVERAGE_FILE
        if cov_path.exists():
            line_cov, branch_cov, uncovered = parse_coverage_json(
                cov_path.read_text(encoding="utf-8")
            )
            result.line_coverage = line_cov
            result.branch_coverage = branch_cov
            result.uncovered_lines = uncovered

    def _write_mutmut_config(self, workdir: Path) -> None:
        """setup.cfg com a config do mutmut 3.6 (source_paths + test selection).

        Escrito no workdir (cwd do mutmut), que é gravável; o `mutants/` gerado
        também cai aqui. Deprecation warning do mutmut é warning, nunca falha.
        """
        source_paths = "\n    ".join(self.mutmut_source_paths)
        config = (
            "[mutmut]\n"
            f"source_paths=\n    {source_paths}\n"
            f"pytest_add_cli_args_test_selection=\n    {TEST_FILENAME}\n"
        )
        (workdir / "setup.cfg").write_text(config, encoding="utf-8")

    def _materialize_sources(self, workdir: Path) -> str | None:
        """Copia os fontes a mutar para o workdir gravável (AD-21).

        O mutmut 3.6 escreve os mutantes ao lado do fonte. Sob AD-04 o projeto
        mora em `/project`, que `--read-only` torna imutável: `mutmut run` morria
        com `OSError: [Errno 30]` e o run saía com `mutation_score=None`. Copiar
        para `/work` resolve sem afrouxar o isolamento — o cwd do container
        precede o install editável no `sys.path`, então os testes importam a
        cópia mutada (verificado em 2026-08-16: 781 mutantes mortos, não zero).

        Devolve None em sucesso, ou a razão da desistência.
        """
        if self.project_root is None:
            return None  # runner local: os fontes já estão no PYTHONPATH
        for origem, destino in self.mutate_targets:
            src = self.project_root / origem
            dest = workdir / destino
            if not src.exists():
                return f"fonte a mutar não existe no host: {src}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)
        return None

    def _apply_mutation_results(self, result: ExecutionResult, workdir: Path) -> None:
        problem = self._materialize_sources(workdir)
        if problem is not None:
            result.mutation_error = problem
            return
        self._write_mutmut_config(workdir)
        run_res = self.runner.run(["mutmut", "run"], workdir=workdir, timeout=self.mutmut_timeout)
        if run_res.timed_out:
            result.timed_out = True
            result.mutation_error = f"mutmut excedeu {self.mutmut_timeout}s (AD-06)"
            # Mantém as métricas de cobertura já obtidas (estado parcial, AD-08).
            return
        stats = parse_mutmut_stats(run_res.stdout + "\n" + run_res.stderr)
        if stats is None:
            # Parcial: sem métrica de mutação, sem crash (AD-08) — mas NUNCA em
            # silêncio. Registrar a cauda da saída é o que teria evitado gerar a
            # coleta inteira sem CPKM e sem erro em log nenhum (AD-21).
            tail = (run_res.stderr or run_res.stdout or "")[-1000:]
            result.mutation_error = (
                f"mutmut não emitiu linha de progresso (exit={run_res.returncode}): {tail}"
            )
            return
        score, killed, total = mutation_score_from_stats(stats)
        result.mutation_score = score
        result.mutants_killed = killed
        result.mutants_total = total
        if stats["survived"]:
            res_res = self.runner.run(["mutmut", "results"], workdir=workdir, timeout=60)
            result.surviving_mutants = parse_mutmut_survivors(res_res.stdout)


def _collection_error_summary(pytest_res: CommandResult) -> str:
    """Resumo curto do erro de coleta/execução para input do RepairAgent (AD-14)."""
    text = (pytest_res.stderr or "") + "\n" + (pytest_res.stdout or "")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = lines[-25:]
    return "\n".join(tail)[:4000] or "erro de execução sem saída capturada"
