"""Geração do relatório final (CSV/JSON), resumo no console e sugestão
de shortlist balanceada por faixa de complexidade ciclomática.

Todas as funções operam sobre `list[dict]` (não sobre `CandidateResult`
diretamente) para permitir regenerar o relatório a qualquer momento a
partir de output/results.jsonl, sem depender de reconstrução de
dataclasses -- importante para a capacidade de retomada do pipeline.
"""
from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger("benchmark_selector.report")

FIELDS = [
    "full_name", "html_url", "status", "motivo", "faixa", "commit_hash",
    "stargazers_count", "license_spdx", "loc_sloc", "cc_media", "n_blocos_cc",
    "ipynb_ratio", "testes_coletaveis", "cobertura_inicial",
    "mutmut_compativel", "mutmut_detalhe", "ambiente_risco", "ambiente_detalhe",
    "contaminacao_risco", "contaminacao_detalhe", "created_at", "pushed_at",
]


def write_csv(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k) for k in FIELDS})


def write_json(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def print_console_summary(results: list[dict]) -> None:
    total = len(results)
    aprovados = [r for r in results if r.get("status") == "aprovado"]
    reprovados = [r for r in results if r.get("status") == "reprovado"]
    erros = [r for r in results if r.get("status") == "erro"]

    por_faixa: dict[str, list[dict]] = defaultdict(list)
    for r in aprovados:
        por_faixa[r.get("faixa")].append(r)

    contaminados = [r for r in aprovados if r.get("contaminacao_risco")]

    print("\n" + "=" * 64)
    print("RESUMO DA TRIAGEM DE BENCHMARK")
    print("=" * 64)
    print(f"Candidatos avaliados : {total}")
    print(f"Aprovados            : {len(aprovados)}")
    print(f"Reprovados           : {len(reprovados)}")
    print(f"Erros de pipeline    : {len(erros)}")
    print("-" * 64)
    print("Distribuição por faixa de CC média (apenas aprovados):")
    for faixa in ("baixa", "media", "alta"):
        nomes = [r["full_name"] for r in por_faixa.get(faixa, [])]
        print(f"  {faixa:6s}: {len(nomes):2d}  {nomes}")
    print("-" * 64)
    print(f"Aprovados com risco de contaminação sinalizado: {len(contaminados)}")
    for r in contaminados:
        print(f"  - {r['full_name']}: {r.get('contaminacao_detalhe')}")
    if erros:
        print("-" * 64)
        print("Candidatos com erro de pipeline (ver output/pipeline.log para detalhes):")
        for r in erros:
            print(f"  - {r['full_name']}: {r.get('motivo')}")
    print("=" * 64 + "\n")


def suggest_shortlist(results: list[dict], min_per_faixa: int = 5, max_per_faixa: int = 7) -> dict:
    """Sugere um shortlist balanceado (5-7 por faixa), priorizando
    candidatos SEM risco de contaminação sinalizado e com mais estrelas
    (proxy de maturidade/uso real). NÃO finaliza a seleção -- a decisão
    final do shortlist é do autor do TCC.
    """
    aprovados = [r for r in results if r.get("status") == "aprovado"]
    por_faixa: dict[str, list[dict]] = defaultdict(list)
    for r in aprovados:
        por_faixa[r.get("faixa")].append(r)

    shortlist: dict[str, list[dict]] = {}
    for faixa, candidatos in por_faixa.items():
        ordenados = sorted(
            candidatos,
            key=lambda r: (bool(r.get("contaminacao_risco")), -(r.get("stargazers_count") or 0)),
        )
        shortlist[faixa] = ordenados[:max_per_faixa]

    print("\n" + "=" * 64)
    print("SUGESTÃO DE SHORTLIST (não finalizada -- decisão final é sua)")
    print("=" * 64)
    for faixa in ("baixa", "media", "alta"):
        cands = shortlist.get(faixa, [])
        alerta = "abaixo do mínimo de 5" if len(cands) < min_per_faixa else "OK"
        print(f"\nFaixa {faixa} ({len(cands)} sugeridos, meta 5-7) [{alerta}]:")
        for r in cands:
            cc = r.get("cc_media")
            cc_str = f"{cc:.2f}" if isinstance(cc, (int, float)) else "N/A"
            flag = " [risco contaminação -- revisar]" if r.get("contaminacao_risco") else ""
            print(f"  - {r['full_name']} (★{r.get('stargazers_count')}, CC={cc_str}){flag}")
    print("=" * 64 + "\n")
    return shortlist
