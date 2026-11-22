"""Testes do carregador de prompts versionados (AD-13)."""

import pytest

from config import PROMPTS_DIR
from infra.prompt_loader import PromptLoader


def test_loads_real_generator_prompt():
    loader = PromptLoader(PROMPTS_DIR)
    prompt = loader.load("generator")
    assert prompt.name == "generator"
    assert prompt.version >= 1
    # version_tag rastreável no RunRecord (AD-13)
    assert prompt.version_tag.startswith("generator.v")
    assert "|" in prompt.version_tag


def test_meta_header_is_stripped():
    loader = PromptLoader(PROMPTS_DIR)
    prompt = loader.load("generator")
    # As linhas de comentário meta do topo não vão para o modelo.
    assert not prompt.template.lstrip().startswith("#")


def test_render_preserves_code_braces(tmp_path):
    (tmp_path / "x.v1.txt").write_text(
        "# meta\n{source_code}\n---\nfim {unknown}", encoding="utf-8"
    )
    prompt = PromptLoader(tmp_path).load("x")
    rendered = prompt.render(source_code="def f():\n    return {'a': 1}")
    assert "return {'a': 1}" in rendered  # chaves do código preservadas
    assert "{unknown}" in rendered  # placeholder não fornecido fica literal
    assert "{source_code}" not in rendered


def test_picks_highest_version(tmp_path):
    (tmp_path / "ag.v1.txt").write_text("v1", encoding="utf-8")
    (tmp_path / "ag.v2.txt").write_text("v2", encoding="utf-8")
    loader = PromptLoader(tmp_path)
    assert loader.load("ag").version == 2
    assert loader.load("ag", version=1).version == 1


def test_hash_changes_with_content(tmp_path):
    (tmp_path / "a.v1.txt").write_text("conteúdo A", encoding="utf-8")
    (tmp_path / "b.v1.txt").write_text("conteúdo B diferente", encoding="utf-8")
    loader = PromptLoader(tmp_path)
    assert loader.load("a").sha8 != loader.load("b").sha8


def test_missing_prompt_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        PromptLoader(tmp_path).load("inexistente")


def test_missing_specific_version_raises(tmp_path):
    (tmp_path / "ag.v1.txt").write_text("v1", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        PromptLoader(tmp_path).load("ag", version=9)
