"""Testes do project_loader (Fase 1)."""

from pathlib import Path

import pytest

from infra.project_loader import load_project

FIXTURE = Path(__file__).parent / "fixtures" / "sample_project"


def test_loads_source_and_metrics():
    ctx = load_project(FIXTURE)
    assert "def soma" in ctx.source_code
    assert "def classifica" in ctx.source_code
    assert ctx.cov_target == "calc"
    assert "calc.py" in ctx.files
    assert ctx.total_complexity > 0
    assert ctx.n_blocks >= 3  # soma, divide, classifica
    assert ctx.loc > 0
    assert not ctx.truncated


def test_source_has_file_header_prefix():
    # Bloco de código no prefixo estável (AD-15).
    ctx = load_project(FIXTURE)
    assert ctx.source_code.startswith("# === calc.py ===")


def test_truncation_flag():
    ctx = load_project(FIXTURE, max_chars=20)
    assert ctx.truncated
    assert len(ctx.source_code) == 20


def test_excludes_test_files(tmp_path):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_mod.py").write_text("def test_f():\n    assert True\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    ctx = load_project(tmp_path)
    assert ctx.files == ["mod.py"]


def test_infers_package_target(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    ctx = load_project(tmp_path)
    assert ctx.cov_target == "mypkg"
    assert "mypkg" in ctx.source_paths


def _pkg(root, rel, mod="core"):
    d = root / rel
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / f"{mod}.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    return d


def test_src_layout_usa_o_pacote_e_nao_o_nome_da_pasta(tmp_path):
    """AD-21: `--cov=<nome-da-pasta>` não é importável em layout `src/`.

    `claude-agent-sdk-python` convergiu com `line_coverage=None` porque o alvo
    inferido era o nome da pasta, com hífens, e não o pacote `claude_agent_sdk`.
    """
    _pkg(tmp_path, "src/meu_pacote")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "meu-pacote"\n[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        encoding="utf-8",
    )
    ctx = load_project(tmp_path)
    assert ctx.cov_target == "meu_pacote"
    # Destino na RAIZ do workdir: é o que faz a cópia sombrear /project.
    assert ctx.mutate_targets == [("src/meu_pacote", "meu_pacote")]


def test_package_dir_do_setup_cfg(tmp_path):
    """`package-dir = {"" = "scripts"}` (reviewable-html-workbench)."""
    _pkg(tmp_path, "scripts/workbench")
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\nname = wb\n[options]\npackage_dir =\n     = scripts\n",
        encoding="utf-8",
    )
    ctx = load_project(tmp_path)
    assert ctx.cov_target == "workbench"
    assert ctx.mutate_targets == [("scripts/workbench", "workbench")]


def test_hatchling_declara_caminho_do_pacote(tmp_path):
    """hatchling lista CAMINHOS, não nomes (claude-agent-sdk-python)."""
    _pkg(tmp_path, "src/sdk_pkg")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sdk"\n[tool.hatch.build.targets.wheel]\npackages = ["src/sdk_pkg"]\n',
        encoding="utf-8",
    )
    ctx = load_project(tmp_path)
    assert ctx.cov_target == "sdk_pkg"
    assert ctx.mutate_targets == [("src/sdk_pkg", "sdk_pkg")]


def test_source_paths_sempre_em_posix(tmp_path):
    """O setup.cfg do mutmut é lido no container Linux (AD-21).

    Com separador do Windows, `src\\pkg` virava um nome de arquivo sem sentido e
    o mutmut reportava `0 files mutated` — sem erro, sem métrica.
    """
    _pkg(tmp_path, "src/meu_pacote")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "meu-pacote"\n[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        encoding="utf-8",
    )
    ctx = load_project(tmp_path)
    assert all("\\" not in origem for origem, _ in ctx.mutate_targets)
    assert all("\\" not in p for p in ctx.source_paths)


def test_py_modules_de_arquivo_unico(tmp_path):
    """`py-modules = ["lanhu_mcp_server"]`: módulo solto, sem pacote."""
    (tmp_path / "lanhu_mcp_server.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "lanhu-mcp-server"\n'
        '[tool.setuptools]\npy-modules = ["lanhu_mcp_server"]\n',
        encoding="utf-8",
    )
    ctx = load_project(tmp_path)
    assert ctx.cov_target == "lanhu_mcp_server"
    assert ctx.mutate_targets == [("lanhu_mcp_server.py", "lanhu_mcp_server.py")]


def test_rejects_non_directory(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        load_project(f)
