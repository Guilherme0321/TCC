"""Criação de venv isolado por projeto, instalação best-effort de
dependências, e validação da suíte de testes existente (coletabilidade
via `pytest --co` + cobertura inicial via `pytest-cov`).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import venv
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger("benchmark_selector.test_validation")


def venv_python(venv_path: Path) -> Path:
    """Retorna o caminho do interpretador Python dentro do venv.

    Sempre absoluto (`.resolve()`): este caminho é usado como executável
    em chamadas subprocess que também definem `cwd=repo_path` (um
    diretório diferente). Se `venv_path` fosse relativo, o exec() do
    sistema o resolveria relativo ao novo `cwd`, não ao cwd do processo
    atual -- e o binário "desapareceria" silenciosamente.
    """
    venv_path = venv_path.resolve()
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def create_venv(venv_path: Path) -> Path:
    """Cria um venv isolado (com pip) para o projeto candidato.

    Não fazemos `pip install --upgrade pip` aqui: no Windows, o self-upgrade
    do pip falha silenciosamente deixando o diretório `pip/_internal/cli/`
    vazio e produz erros 'No module named pip._internal.cli' em todas as
    instalações subsequentes. O pip que vem com `EnvBuilder(with_pip=True)`
    no Python 3.12+ já é >=24.x e é suficiente para o pipeline.
    """
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_path)
    return venv_python(venv_path)


def _run(cmd: list[str], cwd: Path, timeout: int,
         env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """Wrapper de subprocess.run que mata toda a árvore de processos em
    caso de timeout.

    Necessário porque `subprocess.run(..., capture_output=True, timeout=…)`
    no Windows mata apenas o processo pai. pytest-cov, pytest-xdist e pip
    com build isolation fazem fork/spawn de filhos; se o pai morre mas os
    filhos seguem vivos segurando os pipes de stdout/stderr, as threads
    internas (`stdout_thread`/`stderr_thread`) ficam bloqueadas em
    `_wait_for_tstate_lock` indefinidamente -- programa trava em deadlock
    sem nunca propagar TimeoutExpired ao chamador.
    """
    kwargs: dict = dict(cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, env=env)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # start_new_session: o filho vira líder de grupo de processos próprio.
        # Sem isso, os filhos herdam o pgid do pipeline e o os.killpg do
        # _kill_tree no timeout mataria o grupo INTEIRO -- incluindo o próprio
        # cli.py (SIGKILL em si mesmo). No Windows não há esse risco porque o
        # kill usa taskkill /T (árvore a partir do pid, não o grupo).
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)


def _kill_tree(pid: int) -> None:
    """Mata o processo `pid` e todos os seus descendentes. Sem dependência
    externa: usa taskkill no Windows e os.killpg no Unix.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=10)
    else:
        try:
            os.killpg(os.getpgid(pid), 9)
        except (ProcessLookupError, PermissionError):
            pass


def install_dependencies(python_bin: Path, repo_path: Path) -> tuple[bool, str]:
    """Instala, em ordem de tentativa, as dependências do projeto e as
    ferramentas de avaliação (pytest, pytest-cov, mutmut).

    Resiliente por design: cada etapa é tentada de forma independente;
    falhas são acumuladas em `log` mas nunca interrompem o pipeline.
    Retorna (instalou_algo_do_projeto, log_de_falhas).
    """
    log_parts: list[str] = []
    ok_any = False
    base_cmd = [str(python_bin), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]

    req_files = ("requirements.txt", "requirements-dev.txt", "requirements_test.txt", "test-requirements.txt")
    for fname in req_files:
        req = repo_path / fname
        if not req.exists():
            continue
        try:
            r = _run(base_cmd + ["-r", str(req)], cwd=repo_path, timeout=config.PIP_INSTALL_TIMEOUT_S)
            if r.returncode == 0:
                ok_any = True
            else:
                log_parts.append(f"falha em {fname}: {r.stderr.strip()[-300:]}")
        except subprocess.TimeoutExpired:
            log_parts.append(f"timeout instalando {fname}")

    if (repo_path / "pyproject.toml").exists() or (repo_path / "setup.py").exists():
        try:
            r = _run(base_cmd + ["-e", "."], cwd=repo_path, timeout=config.PIP_INSTALL_TIMEOUT_S)
            if r.returncode == 0:
                ok_any = True
            else:
                log_parts.append(f"falha em `pip install -e .`: {r.stderr.strip()[-300:]}")
        except subprocess.TimeoutExpired:
            log_parts.append("timeout em `pip install -e .`")

    try:
        # Pin do mutmut centralizado em config.MUTMUT_PIN e alinhado à major
        # version do executor do experimento (pipeline_scaffold): o venv do
        # candidato deve validar com a MESMA ferramenta que medirá CPKM no
        # TCC II. Versão divergente aqui produz falso-positivo/negativo
        # sistemático de compatibilidade, não um problema do repo.
        r = _run(base_cmd + ["pytest", "pytest-cov", config.MUTMUT_PIN], cwd=repo_path,
                 timeout=config.PIP_INSTALL_TIMEOUT_S)
        if r.returncode != 0:
            log_parts.append(f"falha instalando pytest/pytest-cov/mutmut: {r.stderr.strip()[-300:]}")
    except subprocess.TimeoutExpired:
        log_parts.append("timeout instalando ferramentas de avaliação (pytest/pytest-cov/mutmut)")

    return ok_any, "; ".join(log_parts)


def validate_collection(python_bin: Path, repo_path: Path) -> tuple[bool, str]:
    """Roda `pytest --co -q` para validar que a suíte é coletável e
    contém ao menos um teste (critério de inclusão #3).

    returncode 0 = coleta OK com testes encontrados.
    returncode 5 = "no tests collected" -> tratado como falha (não há
    baseline de cobertura possível sem testes).
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_path) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        r = _run([str(python_bin), "-m", "pytest", "--co", "-q"],
                 cwd=repo_path, timeout=config.PYTEST_COLLECT_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return False, f"timeout (>{config.PYTEST_COLLECT_TIMEOUT_S}s) na coleta de testes"

    ok = r.returncode == 0
    output = (r.stdout + r.stderr)[-2000:]
    if r.returncode == 5:
        output = "nenhum teste coletado (suíte vazia ou ausente): " + output
    return ok, output


def measure_baseline_coverage(python_bin: Path, repo_path: Path) -> Optional[float]:
    """Roda a suíte completa com pytest-cov e retorna a cobertura de
    linha inicial como fração 0.0-1.0, ou None se a execução falhar."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_path) + os.pathsep + env.get("PYTHONPATH", "")
    cov_json = repo_path / ".coverage_baseline.json"

    try:
        _run(
            [str(python_bin), "-m", "pytest", "-p", "no:cacheprovider",
             "--cov=.", f"--cov-report=json:{cov_json}", "-q"],
            cwd=repo_path, timeout=config.PYTEST_RUN_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Timeout (>%ss) ao medir cobertura baseline em %s", config.PYTEST_RUN_TIMEOUT_S, repo_path)
        return None

    if not cov_json.exists():
        return None

    try:
        with open(cov_json, encoding="utf-8") as f:
            data = json.load(f)
        return data["totals"]["percent_covered"] / 100.0
    except (KeyError, json.JSONDecodeError, OSError):
        return None
