"""Testes das regras de leitura do analisador (AD-08, AD-12, AD-22).

Estas regras decidem os números que vão para o artigo: quantos runs convergiram,
quanto custou cada arquitetura e o que entra no denominador do CCP. Um erro aqui
não quebra nada — só publica o número errado.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze import _postos_medios, carregar, celulas  # noqa: E402


def _registro(**campos):
    """Registro mínimo no formato do results.jsonl."""
    base = {
        "run_key": "A2__gpt-5-mini__proj__rep-1",
        "architecture": "A2",
        "project": "proj",
        "repetition": 1,
        "status": "completed",
        "converged": True,
        "cost_usd": 0.01,
        "line_coverage": 40.0,
        "branch_coverage": 30.0,
        "mutation_score": 0.5,
        "execution": {"passed": 10, "failed": 0, "errors": 0, "mutants_killed": 50},
    }
    base.update(campos)
    return base


def _jsonl(tmp_path: Path, registros: list[dict]) -> Path:
    caminho = tmp_path / "results.jsonl"
    with caminho.open("w", encoding="utf-8") as fh:
        for r in registros:
            fh.write(json.dumps(r) + "\n")
    return caminho


# --------------------------------------------------------------------------
# AD-12 — o JSONL é log de tentativas
# --------------------------------------------------------------------------
def test_custo_soma_todas_as_tentativas_metrica_vem_da_definitiva(tmp_path):
    """A tentativa perdida foi paga; a métrica dela, não.

    `A2__gpt-5-mini__codeindex__rep-2` apareceu duas vezes na coleta real:
    `crashed` a US$ 0,012037 e `completed` a US$ 0,011072. Contar só a linha
    vencedora subestimaria o custo do braço.
    """
    caminho = _jsonl(
        tmp_path,
        [
            _registro(status="crashed", converged=False, cost_usd=0.012037, line_coverage=None),
            _registro(status="completed", converged=True, cost_usd=0.011072, line_coverage=40.0),
        ],
    )
    runs, diag = carregar(caminho)
    assert len(runs) == 1
    assert runs[0].custo == pytest.approx(0.023109)  # somado
    assert runs[0].line_coverage == 40.0  # da definitiva
    assert diag["duplicadas"] == 1


def test_crashed_sem_tentativa_definitiva_nao_vira_run(tmp_path):
    """Sem status final o run não foi observado — não pode entrar como falha."""
    caminho = _jsonl(tmp_path, [_registro(status="crashed", converged=False)])
    runs, diag = carregar(caminho)
    assert runs == []
    assert diag["crashed"] == 1


def test_ordem_das_tentativas_nao_altera_o_resultado(tmp_path):
    """A definitiva vence mesmo vindo antes do crash no arquivo."""
    caminho = _jsonl(
        tmp_path,
        [
            _registro(status="completed", converged=True, line_coverage=55.0),
            _registro(status="crashed", converged=False, line_coverage=None),
        ],
    )
    runs, _ = carregar(caminho)
    assert runs[0].line_coverage == 55.0


# --------------------------------------------------------------------------
# AD-22 — timeout de mutação não invalida convergência
# --------------------------------------------------------------------------
def test_timeout_com_suite_verde_e_reclassificado_como_convergido(tmp_path):
    """25 dos 26 timeouts da coleta eram convergências reais."""
    caminho = _jsonl(
        tmp_path,
        [
            _registro(
                status="timeout",
                converged=False,
                line_coverage=23.5,
                mutation_score=None,
                execution={"passed": 19, "failed": 0, "errors": 0},
            )
        ],
    )
    runs, diag = carregar(caminho)
    assert runs[0].converged is True
    assert runs[0].reclassificado is True
    assert runs[0].line_coverage == 23.5
    assert runs[0].mutation_score is None  # a mutação é que estourou
    assert diag["reclassificados"] == 1


def test_timeout_com_teste_vermelho_nao_e_reclassificado(tmp_path):
    """Só recupera quem tinha suite verde — senão viraria convergência inventada."""
    caminho = _jsonl(
        tmp_path,
        [
            _registro(
                status="timeout",
                converged=False,
                execution={"passed": 5, "failed": 2, "errors": 0},
            )
        ],
    )
    runs, diag = carregar(caminho)
    assert runs[0].converged is False
    assert diag["reclassificados"] == 0


def test_timeout_sem_cobertura_medida_nao_e_reclassificado(tmp_path):
    """Sem cobertura não há o que somar ao CCP; convergir seria só ruído."""
    caminho = _jsonl(
        tmp_path,
        [
            _registro(
                status="timeout",
                converged=False,
                line_coverage=None,
                execution={"passed": 8, "failed": 0, "errors": 0},
            )
        ],
    )
    runs, _ = carregar(caminho)
    assert runs[0].converged is False


def test_suite_vazia_nao_conta_como_verde(tmp_path):
    """Zero testes executados não é suite verde (espelha `all_passed`)."""
    caminho = _jsonl(
        tmp_path,
        [
            _registro(
                status="timeout",
                converged=False,
                execution={"passed": 0, "failed": 0, "errors": 0},
            )
        ],
    )
    runs, _ = carregar(caminho)
    assert runs[0].converged is False


# --------------------------------------------------------------------------
# AD-08 — o custo do fracasso entra no denominador
# --------------------------------------------------------------------------
def test_ccp_inclui_o_custo_dos_runs_que_falharam(tmp_path):
    """Quatro tentativas, uma convergiu: o CCP paga pelas quatro.

    É o que separa A1 de A2 no artigo — A1 é barata por run e cara por ponto de
    cobertura, porque desperdiça 95% das tentativas.
    """
    caminho = _jsonl(
        tmp_path,
        [
            _registro(
                run_key="k1", repetition=1, status="completed", converged=True, cost_usd=0.02
            ),
            _registro(run_key="k2", repetition=2, status="failed", converged=False, cost_usd=0.02),
            _registro(run_key="k3", repetition=3, status="failed", converged=False, cost_usd=0.02),
            _registro(run_key="k4", repetition=4, status="failed", converged=False, cost_usd=0.02),
        ],
    )
    runs, _ = carregar(caminho)
    c = celulas(runs)[("A2", "proj")]
    assert c.n_runs == 4
    assert c.n_convergidos == 1
    assert c.custo_medio == pytest.approx(0.02)  # 0.08 / 4
    assert c.cobertura_media == pytest.approx(40.0)  # só a que convergiu
    assert c.ccp == pytest.approx(0.02 / 40.0)


def test_metricas_de_run_nao_convergido_sao_ignoradas(tmp_path):
    """Cobertura parcial de suite vermelha não entra na média."""
    caminho = _jsonl(
        tmp_path,
        [
            _registro(
                status="failed",
                converged=False,
                line_coverage=99.0,
                execution={"passed": 1, "failed": 9, "errors": 0},
            )
        ],
    )
    runs, _ = carregar(caminho)
    assert runs[0].line_coverage is None
    assert celulas(runs)[("A2", "proj")].cobertura_media is None


# --------------------------------------------------------------------------
# Tamanho de efeito
# --------------------------------------------------------------------------
def test_postos_medios_empata_pela_media():
    assert _postos_medios([10, 20, 30]) == [1, 2, 3]
    assert _postos_medios([30, 10, 20]) == [3, 1, 2]
    assert _postos_medios([5, 5, 9]) == [1.5, 1.5, 3]
    assert _postos_medios([7, 7, 7, 7]) == [2.5, 2.5, 2.5, 2.5]


# --------------------------------------------------------------------------
# --por-modelo — decomposição do n pareado
# --------------------------------------------------------------------------
def test_comparar_pareado_separa_sem_medida_de_empate(tmp_path):
    """Projeto sem convergência sai por (a); diferença zero sai por (b)."""
    from analyze import comparar_pareado

    regs = []
    for i in range(8):  # p0..p7: A2 converge sempre, A4 em 1 de 2
        for rep in (1, 2):
            regs.append(
                _registro(
                    run_key=f"A2__m__p{i}__rep-{rep}",
                    project=f"p{i}",
                    repetition=rep,
                    line_coverage=40.0 + i,
                )
            )
            regs.append(
                _registro(
                    run_key=f"A4__m__p{i}__rep-{rep}",
                    architecture="A4",
                    project=f"p{i}",
                    repetition=rep,
                    converged=rep == 1,
                    line_coverage=30.0 + 2 * i,
                )
            )
    for rep in (1, 2):  # p8: empate na convergência (ambos 100%)
        regs.append(_registro(run_key=f"A2__m__p8__rep-{rep}", project="p8", repetition=rep))
        regs.append(
            _registro(
                run_key=f"A4__m__p8__rep-{rep}", architecture="A4", project="p8", repetition=rep
            )
        )
    for rep in (1, 2):  # p9: A4 nunca converge → sem cobertura
        regs.append(_registro(run_key=f"A2__m__p9__rep-{rep}", project="p9", repetition=rep))
        regs.append(
            _registro(
                run_key=f"A4__m__p9__rep-{rep}",
                architecture="A4",
                project="p9",
                repetition=rep,
                converged=False,
                status="failed",
                line_coverage=None,
            )
        )
    runs, _ = carregar(_jsonl(tmp_path, regs))
    assert {r.model for r in runs} == {"m"}  # recuperado da run_key
    cel = celulas(runs)
    x = {p: c for (a, p), c in cel.items() if a == "A2"}
    y = {p: c for (a, p), c in cel.items() if a == "A4"}
    projetos = sorted(x)

    conv = comparar_pareado(x, y, projetos, "taxa_convergencia")
    assert conv["sem_medida"] == []
    assert conv["empates"] == ["p8"]
    assert conv["n"] == 9  # 10 − 0 − 1
    assert conv["r"] == pytest.approx(1.0)

    cob = comparar_pareado(x, y, projetos, "cobertura_media")
    assert cob["sem_medida"] == ["p9"]
    assert cob["n"] + len(cob["sem_medida"]) + len(cob["empates"]) == len(projetos)
