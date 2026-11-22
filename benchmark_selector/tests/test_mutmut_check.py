"""Testes do dry-run de compatibilidade com mutmut (API 3.x).

A execução real do mutmut não é exercitada aqui (é lenta e depende de venv
por candidato); `_run` é substituído por stubs. As saídas usadas em
`test_classify_dryrun` foram observadas empiricamente no mutmut 3.6.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pipeline import mutmut_check
from pipeline.mutmut_check import (
    _classify_dryrun,
    _guess_source_dir,
    _guess_tests_dir,
    _mutmut_config_block,
    check_mutmut_compatibility,
)


def _make_pkg(root: Path, name: str, n_modules: int) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for i in range(n_modules):
        (pkg / f"mod{i}.py").write_text("x = 1\n", encoding="utf-8")
    return pkg


# -- heurísticas de descoberta ------------------------------------------------

def test_guess_source_dir_prefere_maior_pacote(tmp_path):
    _make_pkg(tmp_path, "menor", 1)
    maior = _make_pkg(tmp_path, "maior", 5)
    assert _guess_source_dir(tmp_path) == maior


def test_guess_source_dir_ignora_pacotes_de_teste(tmp_path):
    _make_pkg(tmp_path, "tests", 10)
    pkg = _make_pkg(tmp_path, "pkg", 2)
    assert _guess_source_dir(tmp_path) == pkg


def test_guess_source_dir_sem_pacotes_retorna_raiz(tmp_path):
    assert _guess_source_dir(tmp_path) == tmp_path


def test_guess_tests_dir_diretorio_padrao(tmp_path):
    (tmp_path / "tests").mkdir()
    assert _guess_tests_dir(tmp_path) == "tests"


def test_guess_tests_dir_singular(tmp_path):
    (tmp_path / "test").mkdir()
    assert _guess_tests_dir(tmp_path) == "test"


def test_guess_tests_dir_arquivos_espalhados(tmp_path):
    sub = tmp_path / "pkg" / "testing"
    sub.mkdir(parents=True)
    (sub / "test_x.py").write_text("", encoding="utf-8")
    assert _guess_tests_dir(tmp_path) == "pkg/testing"


def test_guess_tests_dir_sem_testes_deixa_pytest_descobrir(tmp_path):
    assert _guess_tests_dir(tmp_path) == "."


# -- config no formato 3.x ----------------------------------------------------

def test_config_block_usa_chaves_do_mutmut_3x():
    block = _mutmut_config_block("src/pkg", "tests")
    assert "[mutmut]" in block
    assert "source_paths=src/pkg" in block
    assert "pytest_add_cli_args_test_selection=tests" in block


def test_config_block_nao_regride_para_chaves_2x():
    # Chaves da API 2.x divergiriam do executor do experimento
    # (pipeline_scaffold/infra/test_executor.py) e são ignoradas pelo 3.x.
    block = _mutmut_config_block("src/pkg", "tests")
    assert "paths_to_mutate" not in block
    assert "runner=" not in block
    assert "tests_dir=" not in block


# -- classificação da saída do mutmut 3.x -------------------------------------

@pytest.mark.parametrize(
    ("output", "returncode", "esperado"),
    [
        # coleta do pytest falhou / suíte quebra sem mutação (rc=0 no 3.x!)
        ("failed to collect stats. runner returned 2", 0, False),
        ("RuntimeError: tests don't run cleanly without mutation", 1, False),
        # erro de CLI/configuração
        ("Usage: python -m mutmut run [OPTIONS]\nno such option: --foo", 2, False),
        # crash genérico
        ("Traceback (most recent call last):\n  ...", 1, False),
        # campanha de mutação iniciada = configuração válida
        ("Running mutation testing\n4/4  done", 0, True),
        ("... 68.34 mutations/second", 0, True),
        # saída irreconhecível nunca aprova silenciosamente
        ("saída inesperada qualquer", 0, False),
    ],
)
def test_classify_dryrun(output, returncode, esperado):
    ok, _detalhe = _classify_dryrun(output, returncode)
    assert ok is esperado


# -- orquestração: escrita e restauração do setup.cfg -------------------------

def test_check_escreve_config_3x_e_restaura_setup_cfg(tmp_path, monkeypatch):
    _make_pkg(tmp_path, "pkg", 2)
    (tmp_path / "tests").mkdir()
    setup_cfg = tmp_path / "setup.cfg"
    conteudo_original = "[metadata]\nname = original\n"
    setup_cfg.write_text(conteudo_original, encoding="utf-8")

    visto: dict[str, str] = {}

    def fake_run(cmd, cwd, timeout, env=None):
        visto["setup_cfg"] = setup_cfg.read_text(encoding="utf-8")
        visto["cmd"] = " ".join(str(c) for c in cmd)
        return subprocess.CompletedProcess(cmd, 0, "Running mutation testing", "")

    monkeypatch.setattr(mutmut_check, "_run", fake_run)
    ok, _detalhe = check_mutmut_compatibility(Path("python"), tmp_path)

    assert ok is True
    assert "-m mutmut run" in visto["cmd"]
    assert "source_paths=pkg" in visto["setup_cfg"]
    assert "pytest_add_cli_args_test_selection=tests" in visto["setup_cfg"]
    # restaurado ao final, preservando o conteúdo pré-existente
    assert setup_cfg.read_text(encoding="utf-8") == conteudo_original


def test_check_remove_setup_cfg_que_nao_existia(tmp_path, monkeypatch):
    _make_pkg(tmp_path, "pkg", 1)

    def fake_run(cmd, cwd, timeout, env=None):
        return subprocess.CompletedProcess(
            cmd, 0, "failed to collect stats. runner returned 2", ""
        )

    monkeypatch.setattr(mutmut_check, "_run", fake_run)
    ok, detalhe = check_mutmut_compatibility(Path("python"), tmp_path)

    assert ok is False
    assert "suíte não roda" in detalhe
    assert not (tmp_path / "setup.cfg").exists()


def test_check_timeout_classifica_como_compativel(tmp_path, monkeypatch):
    _make_pkg(tmp_path, "pkg", 1)

    def fake_run(cmd, cwd, timeout, env=None):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(mutmut_check, "_run", fake_run)
    ok, detalhe = check_mutmut_compatibility(Path("python"), tmp_path)

    assert ok is True
    assert "timeout" in detalhe
    assert not (tmp_path / "setup.cfg").exists()
