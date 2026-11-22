"""Geração do pool inicial de candidatos via GitHub Search API.

Roda múltiplas queries estratificadas (por faixa de estrelas e por tópico)
em vez de uma única busca ordenada por popularidade, para reduzir o viés
de amostragem em direção apenas aos projetos mais conhecidos -- isso é
relevante para a representatividade do benchmark (ameaça à validade
externa, ver checklist [DESIGN] da metodologia).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import config
from .github_api import GitHubClient

logger = logging.getLogger("benchmark_selector.search")


def generate_candidates(
    client: GitHubClient,
    target_n: int = config.N_CANDIDATOS_ALVO,
    exclude: set[str] | None = None,
) -> list[dict]:
    """Executa as queries de config.SEARCH_QUERIES e retorna um pool
    deduplicado de candidatos (full_name, html_url, stargazers_count).

    Não aplica nenhum critério de inclusão/exclusão -- isso é feito na
    etapa `evaluate`. Esta função apenas amostra um pool inicial razoável.

    `exclude` é um conjunto de full_names a ignorar (repos já conhecidos de
    execuções anteriores), evitando duplicatas no pool acumulado.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=config.ATIVIDADE_MAX_DIAS)).strftime("%Y-%m-%d")
    excluded = exclude or set()
    seen: dict[str, dict] = {}

    # Busca TODAS as queries e depois intercala (round-robin) em vez de preencher
    # sequencialmente até a meta. Sem isso, as primeiras queries esgotam a cota
    # `target_n` e os estratos no fim da lista (ex.: tópicos de maior
    # complexidade -- compiler/interpreter) nunca são amostrados, anulando a
    # estratificação que justifica usar múltiplas queries (representatividade /
    # validade externa, ver docstring do módulo).
    #
    # Pagina página a página: como execuções acumuladas já excluem centenas de
    # repos conhecidos, a página 1 de cada query fica quase toda repetida; as
    # páginas seguintes é que trazem candidatos inéditos. Varre até juntar
    # target_n novos ou esgotar MAX_SEARCH_PAGES.
    for page in range(1, config.MAX_SEARCH_PAGES + 1):
        if len(seen) >= target_n:
            break

        per_query: list[list[dict]] = []
        for template in config.SEARCH_QUERIES:
            query = template.format(since=since)
            logger.info("Buscando (pág %d): %s", page, query)
            items = client.search_repositories(query, per_page=config.RESULTS_PER_QUERY, page=page)
            logger.info("  -> %d resultados", len(items))
            per_query.append([dict(item, source_query=query) for item in items])

        if not any(per_query):
            break  # todas as queries esgotaram

        depth = 0
        while len(seen) < target_n and any(depth < len(bucket) for bucket in per_query):
            for bucket in per_query:
                if depth >= len(bucket):
                    continue
                item = bucket[depth]
                full_name = item["full_name"]
                if full_name not in seen and full_name not in excluded:
                    seen[full_name] = {
                        "full_name": full_name,
                        "html_url": item["html_url"],
                        "stargazers_count": item.get("stargazers_count"),
                        "source_query": item["source_query"],
                    }
                    if len(seen) >= target_n:
                        break
            depth += 1

    candidates = list(seen.values())[:target_n]
    logger.info("Pool de candidatos gerado: %d novos repositórios únicos (meta: %d).", len(candidates), target_n)
    if len(candidates) < target_n:
        logger.warning(
            "Pool ficou abaixo da meta (%d < %d). Considere relaxar os filtros de "
            "estrelas/tamanho em config.SEARCH_QUERIES ou adicionar mais queries.",
            len(candidates), target_n,
        )
    return candidates
