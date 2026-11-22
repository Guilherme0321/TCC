"""Estrutura de dados do resultado de cada candidato e aplicação
programática dos critérios de inclusão/exclusão (Seção 4.4 da
Metodologia do TCC).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import config


@dataclass
class CandidateResult:
    full_name: str
    html_url: str
    status: str = "pendente"           # pendente | aprovado | reprovado | erro
    motivo: str = ""
    faixa: Optional[str] = None         # baixa | media | alta (apenas se aprovado)

    commit_hash: Optional[str] = None
    stargazers_count: Optional[int] = None
    license_spdx: Optional[str] = None
    created_at: Optional[str] = None
    pushed_at: Optional[str] = None

    loc_sloc: Optional[int] = None
    cc_media: Optional[float] = None
    cc_pico: Optional[float] = None       # maior CC de bloco no repo (alternativa à média)
    cc_p90: Optional[float] = None        # percentil 90 da CC por bloco (alternativa à média)
    n_blocos_cc: Optional[int] = None
    ipynb_ratio: Optional[float] = None

    testes_coletaveis: Optional[bool] = None
    cobertura_inicial: Optional[float] = None

    mutmut_compativel: Optional[bool] = None
    mutmut_detalhe: str = ""

    ambiente_risco: Optional[bool] = None
    ambiente_detalhe: str = ""

    contaminacao_risco: Optional[bool] = None
    contaminacao_detalhe: str = ""

    erros: list[str] = field(default_factory=list)


def classify_faixa(cc_value: float) -> str:
    if cc_value < config.CC_BAIXA_MAX:
        return "baixa"
    if cc_value < config.CC_MEDIA_MAX:
        return "media"
    return "alta"


def cc_value_for_metric(result: CandidateResult, cc_metric: str) -> Optional[float]:
    """Valor de complexidade do resultado conforme a métrica escolhida.
    A métrica só afeta a CLASSIFICAÇÃO por faixa, nunca a aprovação."""
    return {
        "media": result.cc_media,
        "pico": result.cc_pico,
        "p90": result.cc_p90,
    }[cc_metric]


def evaluate_criteria(result: CandidateResult, cc_metric: str = "media") -> CandidateResult:
    """Aplica, em sequência, cada critério de inclusão/exclusão sobre um
    CandidateResult já preenchido com os dados coletados nas etapas
    anteriores. Atualiza `status`, `motivo` e `faixa` in-place e retorna
    o próprio objeto (todos os motivos de reprovação são acumulados, não
    apenas o primeiro, para facilitar revisão manual depois).

    `cc_metric` (media|pico|p90) escolhe qual agregado de complexidade define
    a faixa do aprovado. Não altera nenhum critério de aprovação/reprovação --
    apenas o rótulo de faixa (baixa/media/alta).
    """
    motivos: list[str] = []

    # --- Critérios de exclusão (qualquer um descarta o candidato) -------
    if result.ipynb_ratio is not None and result.ipynb_ratio > config.IPYNB_RATIO_MAX:
        motivos.append(f"exclusão: composição majoritária por notebooks ({result.ipynb_ratio:.0%} .ipynb)")

    if result.cobertura_inicial is not None and result.cobertura_inicial > config.COBERTURA_INICIAL_MAX:
        motivos.append(f"exclusão: cobertura inicial já acima de 90% ({result.cobertura_inicial:.0%})")

    if result.ambiente_risco:
        motivos.append(f"exclusão: dependência de hardware/ambiente não reproduzível ({result.ambiente_detalhe})")

    if result.mutmut_compativel is False:
        motivos.append(f"exclusão: incompatível com mutmut ({result.mutmut_detalhe})")

    # Licença: combina inclusão #4 (licença OSI aprovada) e exclusão #4
    # (licença que restrinja análise estática/derivados) em um único
    # filtro baseado na allowlist de config.LICENCAS_ACEITAS.
    # NOASSERTION = GitHub detectou arquivo de licença mas não mapeou ao SPDX;
    # pode ser licença permissiva válida -- vira aviso, não rejeição automática.
    if result.license_spdx is None:
        motivos.append("licença ausente (inclusão #4 / exclusão #4): repositório sem arquivo de licença")
    elif result.license_spdx.upper() == "NOASSERTION":
        result.erros.append(
            "licença com SPDX=NOASSERTION: GitHub não conseguiu identificar automaticamente "
            "-- verificar manualmente antes de incluir no estudo"
        )
    elif result.license_spdx.lower() not in config.LICENCAS_ACEITAS:
        motivos.append(f"licença não aprovada (inclusão #4 / exclusão #4): {result.license_spdx!r}")

    # --- Critérios de inclusão (todos devem ser satisfeitos) ------------
    if result.loc_sloc is not None:
        if not (config.LOC_MIN <= result.loc_sloc <= config.LOC_MAX):
            motivos.append(f"inclusão: LOC executável ({result.loc_sloc}) fora da faixa {config.LOC_MIN}-{config.LOC_MAX}")
    else:
        motivos.append("inclusão: LOC executável não calculável")

    if result.cc_media is None:
        motivos.append("inclusão: complexidade ciclomática não computável via radon (erro de parsing)")

    if not result.testes_coletaveis:
        motivos.append("inclusão: suíte de testes inexistente, não coletável via pytest, ou vazia")

    if result.pushed_at:
        pushed = datetime.fromisoformat(result.pushed_at.replace("Z", "+00:00"))
        idle_days = (datetime.now(timezone.utc) - pushed).days
        if idle_days > config.ATIVIDADE_MAX_DIAS:
            motivos.append(f"inclusão: sem atividade há {idle_days} dias (> {config.ATIVIDADE_MAX_DIAS})")
    else:
        motivos.append("inclusão: data do último push indisponível")

    if motivos:
        result.status = "reprovado"
        result.motivo = " | ".join(motivos)
        result.faixa = None
    else:
        result.status = "aprovado"
        result.motivo = "todos os critérios de inclusão/exclusão satisfeitos"
        result.faixa = classify_faixa(cc_value_for_metric(result, cc_metric))  # type: ignore[arg-type]

    return result
