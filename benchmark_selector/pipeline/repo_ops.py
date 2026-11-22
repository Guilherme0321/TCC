"""Operações de clone superficial e limpeza dos diretórios de trabalho
de cada candidato (isolamento de execução de código de terceiros)."""
from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path

from . import config

logger = logging.getLogger("benchmark_selector.repo_ops")


class CloneError(Exception):
    """Falha ao clonar um repositório candidato."""


def shallow_clone(html_url: str, dest: Path) -> Path:
    """Clona superficialmente (--depth 1) um repositório para `dest`."""
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "clone", "--depth", "1", "--quiet", html_url, str(dest)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=config.CLONE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"Timeout (>{config.CLONE_TIMEOUT_S}s) ao clonar {html_url}") from exc

    if result.returncode != 0:
        raise CloneError(f"git clone falhou para {html_url}: {result.stderr.strip()[:300]}")

    return dest


def _force_remove(func, path, exc_info):
    """Handler para shutil.rmtree: torna arquivos write-permitidos e tenta de novo.
    Necessário no Windows, onde .git/objects cria arquivos read-only."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def cleanup(path: Path) -> None:
    """Remove o diretório de trabalho de um candidato (clone + venv).

    Best-effort: nunca lança exceção, apenas loga em caso de falha (ex.:
    arquivos com permissão somente-leitura criados por git dentro do venv).
    """
    try:
        shutil.rmtree(path, onerror=_force_remove)
    except Exception as exc:  # pragma: no cover
        logger.warning("Falha ao limpar %s: %s", path, exc)
