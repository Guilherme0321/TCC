"""Verificação de compatibilidade básica com mutmut ("dry-run de
configuração").

Não executa uma campanha de mutação completa -- isso é custoso demais
para uma etapa de triagem de 30-40 candidatos e é, de qualquer forma, o
objeto de mensuração da fase de execução do estudo (TCC II), não da
seleção de benchmark. Em vez disso, esta função:

1. Escreve uma configuração mínima de mutmut em `setup.cfg` (restaurada
   ao final, mesmo em caso de erro), no formato do mutmut 3.x
   (`source_paths` + `pytest_add_cli_args_test_selection`), espelhando o
   template usado pelo executor do experimento
   (pipeline_scaffold/infra/test_executor.py). Validar com a mesma major
   version que medirá CPKM no TCC II é requisito metodológico.
2. Dispara `mutmut run` com um teto de tempo curto (MUTMUT_DRYRUN_TIMEOUT_S).
3. Classifica como compatível se a ferramenta chegou à fase de mutação
   sem erro de configuração -- mesmo que o tempo se esgote antes de
   concluir (timeout durante geração/execução de mutantes é, em geral,
   sinal de configuração válida porém lenta). Atenção: o mutmut 3.x
   retorna exit code 0 mesmo quando as fases preparatórias falham, então
   a classificação é textual (ver _classify_dryrun).

Limitação documentada: heurística de viabilidade, não uma medição de
mutation score real.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from . import config
from .test_validation import _run

logger = logging.getLogger("benchmark_selector.mutmut_check")


def _guess_source_dir(repo_path: Path) -> Path:
    """Heurística simples: maior pacote (mais arquivos .py) com
    __init__.py fora de diretórios de teste."""
    candidates: list[tuple[int, Path]] = []
    for init_file in repo_path.rglob("__init__.py"):
        pkg_dir = init_file.parent
        if "test" in pkg_dir.name.lower():
            continue
        n_py = len(list(pkg_dir.rglob("*.py")))
        candidates.append((n_py, pkg_dir))

    if not candidates:
        return repo_path
    candidates.sort(reverse=True)
    return candidates[0][1]


def _guess_tests_dir(repo_path: Path) -> str:
    """Descobre onde estão os testes, em vez de assumir 'tests/' fixo.

    O padrão fixo 'tests/' fazia o mutmut abortar com 'no test folders
    found' em repos cujos testes ficam em outro lugar (ex.: 'test/', na
    raiz, ou dentro do pacote) -- um falso-negativo de configuração nosso,
    não incompatibilidade real do repo. Retorna o caminho relativo à raiz
    do repo (ou '.' para deixar o pytest auto-descobrir).
    """
    for name in ("tests", "test"):
        if (repo_path / name).is_dir():
            return name

    test_files = [
        p for pat in ("test_*.py", "*_test.py")
        for p in repo_path.rglob(pat) if ".git" not in p.parts
    ]
    if not test_files:
        return "."

    dirs = {str(p.parent) for p in test_files}
    common = os.path.commonpath(list(dirs))
    rel = os.path.relpath(common, repo_path)
    return "." if rel == os.curdir else rel.replace(os.sep, "/")


def _mutmut_config_block(source_path: str, tests_dir: str) -> str:
    """Bloco `[mutmut]` no formato 3.x, idêntico em chaves ao template do
    executor do experimento. Não há `runner=` no 3.x: o pytest é interno e
    a seleção de testes entra como argumento de CLI extra."""
    return (
        "\n[mutmut]\n"
        f"source_paths={source_path}\n"
        f"pytest_add_cli_args_test_selection={tests_dir}\n"
    )


def _classify_dryrun(output: str, returncode: int) -> tuple[bool, str]:
    """Classifica a saída do `mutmut run` 3.x (exit code não é confiável:
    o 3.x retorna 0 mesmo com falha nas fases preparatórias).

    Sinais observados empiricamente no 3.6:
    - "failed to collect stats. runner returned N": coleta do pytest falhou
      ou a suíte falha sem mutação -> incompatível.
    - "tests don't run cleanly without mutation": idem, variante RuntimeError.
    - "running mutation testing" / "mutations/second": chegou à campanha de
      mutação -> compatível.
    """
    lowered = output.lower()

    suite_broken = (
        "failed to collect stats" in lowered
        or "tests don't run cleanly without mutation" in lowered
    )
    if suite_broken:
        return False, f"suíte não roda sob o mutmut: {lowered[-300:]}"

    config_error = ("no such option" in lowered) or ("usage:" in lowered and returncode != 0)
    if config_error:
        return False, f"erro de configuração do mutmut: {lowered[-300:]}"

    crashed = "traceback" in lowered and returncode != 0
    if crashed:
        return False, f"erro na execução do mutmut: {lowered[-300:]}"

    reached_mutation = "running mutation testing" in lowered or "mutations/second" in lowered
    if reached_mutation:
        return True, "dry-run concluído sem erro de configuração"

    return False, f"mutmut não chegou à fase de mutação: {lowered[-300:]}"


def check_mutmut_compatibility(python_bin: Path, repo_path: Path) -> tuple[bool, str]:
    src_dir = _guess_source_dir(repo_path)
    tests_dir = _guess_tests_dir(repo_path)
    setup_cfg = repo_path / "setup.cfg"
    original = setup_cfg.read_text(encoding="utf-8") if setup_cfg.exists() else None

    # as_posix(): o mutmut espera separador '/' mesmo no Windows.
    config_block = _mutmut_config_block(
        src_dir.relative_to(repo_path).as_posix(), tests_dir
    )

    try:
        setup_cfg.write_text((original or "") + config_block, encoding="utf-8")

        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_path) + os.pathsep + env.get("PYTHONPATH", "")

        try:
            r = _run(
                [str(python_bin), "-m", "mutmut", "run"],
                cwd=repo_path, timeout=config.MUTMUT_DRYRUN_TIMEOUT_S, env=env,
            )
        except subprocess.TimeoutExpired:
            return True, "timeout durante dry-run (configuração parece válida; revisar manualmente se incluído no benchmark)"

        return _classify_dryrun(r.stdout + r.stderr, r.returncode)

    except Exception as exc:  # pragma: no cover - resiliência do pipeline
        return False, f"exceção no dry-run do mutmut: {exc}"

    finally:
        if original is not None:
            setup_cfg.write_text(original, encoding="utf-8")
        elif setup_cfg.exists():
            setup_cfg.unlink()
