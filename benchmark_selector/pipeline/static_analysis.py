"""Análise estática via radon: complexidade ciclomática média, LOC
executável e proporção de arquivos .ipynb vs .py.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("benchmark_selector.static_analysis")

EXCLUDE_DIRS = {".git", "venv", ".venv", "env", "node_modules", "__pycache__", "build", "dist", ".tox", ".mypy_cache"}

# Padrões fnmatch passados a `radon -e`. Usar limites de "/" explícitos
# (em vez de `*/test*`) é importante: um padrão amplo demais casaria com
# qualquer caminho contendo a substring "test" -- incluindo nomes de
# diretório legítimos como "latest_utils" ou "contest" -- e excluiria
# código-fonte real por engano.
RADON_EXCLUDE_PATTERN = (
    "*/tests/*,*/test/*,test_*.py,*_test.py,*/.venv/*,*/venv/*,*/build/*,*/dist/*,"
    "*/.tox/*,*/node_modules/*,*/mutants/*,*/.pytest_cache/*,*/__pycache__/*"
)


class RadonError(Exception):
    """Falha ao executar ou interpretar a saída do radon."""


def _percentile(values: list[float], p: float) -> Optional[float]:
    """Percentil `p` (0-100) com interpolação linear entre pontos vizinhos
    (mesmo método padrão do numpy.percentile). Retorna None se lista vazia."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def compute_cyclomatic_complexity(repo_path: Path) -> tuple[Optional[float], Optional[float], Optional[float], int, int]:
    """Retorna (cc_media, cc_pico, cc_p90, n_blocos_analisados, n_arquivos_com_erro).

    Uma única passada de `radon cc -j` coleta a complexidade de todos os blocos
    (funções/métodos/classes); a partir dessa distribuição derivamos três
    agregados. `cc_media` (= `radon cc -a`) é o critério de faixa vigente; `cc_pico`
    (máximo) e `cc_p90` (percentil 90) são alternativas que não diluem os núcleos
    complexos e permitem reclassificação por faixa sem re-executar a análise.
    """
    cmd = [sys.executable, "-m", "radon", "cc", "-j", "-e", RADON_EXCLUDE_PATTERN, str(repo_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise RadonError(f"Timeout ao rodar `radon cc` em {repo_path}") from exc

    if not result.stdout.strip():
        raise RadonError(f"`radon cc` não produziu saída para {repo_path}: {result.stderr[:300]}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RadonError(f"Saída inválida (não-JSON) do `radon cc` em {repo_path}") from exc

    complexities: list[float] = []
    files_with_error = 0
    for _file_path, blocks in data.items():
        if isinstance(blocks, dict) and "error" in blocks:
            files_with_error += 1
            continue
        for block in blocks:
            complexities.append(block["complexity"])

    if not complexities:
        return None, None, None, 0, files_with_error

    avg = sum(complexities) / len(complexities)
    pico = float(max(complexities))
    p90 = _percentile(complexities, 90)
    return avg, pico, p90, len(complexities), files_with_error


def compute_loc(repo_path: Path) -> Optional[dict]:
    """Roda `radon raw` e retorna métricas agregadas (loc, lloc, sloc,
    comments, blank, multi) somadas sobre todos os arquivos .py do repo.

    `sloc` (source lines of code, sem comentários/linhas em branco) é
    usado como proxy de "LOC executável" para o critério de inclusão #2.
    """
    cmd = [sys.executable, "-m", "radon", "raw", "-j", "-e", RADON_EXCLUDE_PATTERN, str(repo_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise RadonError(f"Timeout ao rodar `radon raw` em {repo_path}") from exc

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RadonError(f"Saída inválida (não-JSON) do `radon raw` em {repo_path}") from exc

    totals = {"loc": 0, "lloc": 0, "sloc": 0, "comments": 0, "blank": 0, "multi": 0}
    any_valid = False
    for _file_path, metrics in data.items():
        if not isinstance(metrics, dict) or "error" in metrics:
            continue
        any_valid = True
        for key in totals:
            totals[key] += metrics.get(key, 0)

    return totals if any_valid else None


def compute_ipynb_ratio(repo_path: Path) -> float:
    """Proporção de arquivos .ipynb em relação ao total de .py + .ipynb
    (critério de exclusão #2: composição majoritária por notebooks)."""
    py_count = 0
    ipynb_count = 0
    for path in repo_path.rglob("*"):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.suffix == ".py":
            py_count += 1
        elif path.suffix == ".ipynb":
            ipynb_count += 1

    total = py_count + ipynb_count
    if total == 0:
        return 0.0
    return ipynb_count / total
