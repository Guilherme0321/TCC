"""Sinalização de risco de contaminação de dados de treinamento.

Usa o cutoff mais conservador (mais antigo) entre os LLMs avaliados no
estudo como referência (ver `config.CONTAMINATION_CUTOFF`).

Limitação documentada (registrar nas Ameaças à Validade): esta heurística
observa apenas a data de criação do repositório e a data do primeiro
commit detectável via API. Não analisa o histórico completo de commits em
busca de "mudanças estruturais majoritárias" posteriores ao cutoff --
candidatos sinalizados (ou muito próximos da fronteira) devem ser
revisados manualmente antes da decisão final de shortlist.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from . import config


def assess_contamination_risk(
    created_at: Optional[datetime],
    first_commit_at: Optional[datetime],
) -> tuple[bool, str]:
    """Retorna (risco, detalhe). `risco=True` se a data mais antiga entre
    `created_at` e `first_commit_at` for anterior ao cutoff configurado.
    """
    cutoff = config.CONTAMINATION_CUTOFF
    candidatas = [d for d in (created_at, first_commit_at) if d is not None]

    if not candidatas:
        return True, "datas de criação/primeiro commit indisponíveis via API (risco assumido por precaução)"

    earliest: date = min(d.date() for d in candidatas)
    if earliest < cutoff:
        return True, f"repositório existe desde {earliest.isoformat()}, anterior ao cutoff de referência ({cutoff.isoformat()})"
    return False, f"repositório criado em {earliest.isoformat()}, posterior ao cutoff de referência ({cutoff.isoformat()})"
