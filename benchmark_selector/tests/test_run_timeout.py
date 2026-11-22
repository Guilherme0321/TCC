"""Teste de regressão: timeout no `_run` não pode matar o próprio pipeline.

Bug original (POSIX): os filhos herdavam o pgid do processo principal e o
`os.killpg` do `_kill_tree` derrubava o grupo inteiro com SIGKILL -- a triagem
morria sempre que um candidato estourava PYTEST_RUN_TIMEOUT_S.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pipeline.test_validation import _run


@pytest.mark.skipif(os.name == "nt", reason="cenário do os.killpg é específico de POSIX")
def test_run_timeout_mata_o_filho_e_preserva_o_pai(tmp_path):
    inicio = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run([sys.executable, "-c", "import time; time.sleep(60)"],
             cwd=Path(tmp_path), timeout=1)
    # Se o killpg tivesse atingido o grupo do pai, este teste nunca chegaria
    # aqui (o processo do pytest teria recebido SIGKILL).
    assert time.monotonic() - inicio < 30


def test_run_retorna_saida_do_filho(tmp_path):
    r = _run([sys.executable, "-c", "print('ola')"], cwd=Path(tmp_path), timeout=30)
    assert r.returncode == 0
    assert "ola" in r.stdout
