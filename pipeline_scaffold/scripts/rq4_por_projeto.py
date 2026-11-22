"""RQ4 no nível de projeto: reproduz a Tabela 10 (Kruskal-Wallis) do artigo.

Cada projeto contribui com UMA observação por célula (modelo × arquitetura):
a média das suas repetições convergidas. Isso evita a pseudorreplicação de
tratar repetições do mesmo projeto como observações independentes.

Saídas:
  1. Kruskal-Wallis entre os tercis de CC (baixa/média/alta), por modelo,
     arquitetura (A2, A4) e métrica (cobertura de linha, mutation score),
     com p bruto e p corrigido por Benjamini-Hochberg sobre os oito testes.
  2. Teste de moderação: Kruskal-Wallis sobre a diferença A2 − A4 de cada
     projeto, entre os tercis.

Reaproveita as funções de `analyze.py` (carregar, tercis, recorte de AD-24)
sem alterá-las. Uso, a partir de `pipeline_scaffold/`:

    python scripts/rq4_por_projeto.py [--jsonl output/results.jsonl]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze as A  # noqa: E402

MODELOS = ("gpt-5-mini", "claude-sonnet-5")
ARQUITETURAS = ("A2", "A4")
METRICAS = (("line_coverage", "Cobertura"), ("mutation_score", "Mutation score"))
BANDAS = ("baixa", "média", "alta")


def media_por_projeto(runs, modelo: str, arq: str, campo: str, projeto: str) -> float | None:
    """Média das repetições convergidas de um projeto numa célula, ou None."""
    valores = [
        getattr(r, campo)
        for r in runs
        if r.model == modelo
        and r.architecture == arq
        and r.project == projeto
        and r.converged
        and getattr(r, campo) is not None
    ]
    return statistics.mean(valores) if valores else None


def kruskal_por_banda(grupos: dict[str, list[float]]) -> tuple[float, float]:
    from scipy import stats

    usaveis = [v for v in grupos.values() if len(v) >= 2]
    h, p = stats.kruskal(*usaveis)
    return float(h), float(p)


def benjamini_hochberg(ps: list[float]) -> list[float]:
    """p-valores ajustados por Benjamini-Hochberg (step-up, monotônico)."""
    m = len(ps)
    ordem = sorted(range(m), key=lambda i: ps[i])
    ajustados = [0.0] * m
    menor = 1.0
    for posto, i in reversed(list(enumerate(ordem, start=1))):
        menor = min(menor, ps[i] * m / posto)
        ajustados[i] = menor
    return ajustados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jsonl", type=Path, default=A.RESULTS)
    args = ap.parse_args()
    if not args.jsonl.exists():
        print(f"não encontrei {args.jsonl}", file=sys.stderr)
        return 1

    runs, _ = A.carregar(args.jsonl)
    runs = [r for r in runs if r.project not in A.FORA_DA_POPULACAO]
    projetos = sorted({r.project for r in runs})
    banda = A.tercis(set(projetos))
    print(f"Projetos: {len(projetos)} | tercis: "
          + ", ".join(f"{b} = {sum(banda[p] == b for p in projetos)}" for b in BANDAS))

    # 1. Efeito principal da CC, por célula, uma observação por projeto
    linhas = []
    for modelo in MODELOS:
        for arq in ARQUITETURAS:
            for campo, rotulo in METRICAS:
                grupos = {b: [] for b in BANDAS}
                for p in projetos:
                    v = media_por_projeto(runs, modelo, arq, campo, p)
                    if v is not None:
                        grupos[banda[p]].append(v)
                h, pv = kruskal_por_banda(grupos)
                linhas.append((modelo, arq, rotulo, h, pv, {b: len(grupos[b]) for b in BANDAS}))
    ajustados = benjamini_hochberg([l[4] for l in linhas])

    print("\n## Kruskal-Wallis entre tercis de CC (Tabela 10)\n")
    print("| Modelo | Arq. | Métrica | H | p | p (BH) | n por tercil |")
    print("|---|---|---|--:|--:|--:|---|")
    for (modelo, arq, rotulo, h, pv, n), pa in zip(linhas, ajustados):
        print(f"| {modelo} | {arq} | {rotulo} | {h:.2f} | {pv:.4f} | {pa:.4f} | {n} |")

    # 2. Moderação: a diferença A2 − A4 por projeto varia entre os tercis?
    print("\n## Moderação: diferença A2 − A4 por projeto entre tercis\n")
    print("| Modelo | Métrica | H | p | n por tercil |")
    print("|---|---|--:|--:|---|")
    for modelo in MODELOS:
        for campo, rotulo in METRICAS:
            grupos = {b: [] for b in BANDAS}
            for p in projetos:
                a2 = media_por_projeto(runs, modelo, "A2", campo, p)
                a4 = media_por_projeto(runs, modelo, "A4", campo, p)
                if a2 is not None and a4 is not None:
                    grupos[banda[p]].append(a2 - a4)
            h, pv = kruskal_por_banda(grupos)
            print(f"| {modelo} | {rotulo} | {h:.2f} | {pv:.4f} | { {b: len(grupos[b]) for b in BANDAS} } |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
