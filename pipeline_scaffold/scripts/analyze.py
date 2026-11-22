"""Análise da coleta: CCP, CPKM e testes estatísticos (AD-08, AD-12, AD-22, AD-23).

Lê `output/results.jsonl` e produz as tabelas do artigo. Não escreve no JSONL:
a coleta é log imutável de tentativas, e toda correção aqui é regra de leitura.

Três regras de leitura, todas registradas no ADR:

- **AD-12** — o JSONL é log de TENTATIVAS, não tabela de células. Uma `run_key`
  pode aparecer várias vezes (retomadas após crash). As MÉTRICAS vêm da tentativa
  definitiva; o CUSTO é somado sobre todas, porque dinheiro gasto em tentativa
  perdida foi gasto do mesmo jeito.
- **AD-08** — falha não se descarta. O custo de runs que não convergiram entra no
  numerador do CCP/CPKM: é o preço real de obter um ponto de cobertura com aquela
  arquitetura, incluindo o que ela desperdiça tentando.
- **AD-24** — quatro projetos não mediram capacidade de modelo algum (falha de
  build silenciosa e incompatibilidade com AD-05). A taxa de convergência é
  reportada sobre os 17 projetos restantes; `--todos-projetos` volta aos 21.
- **AD-22** — run com `status=timeout`, suite verde e cobertura medida convergiu.
  O que estourou os 1800 s foi a mutação, fase posterior ao critério de
  convergência. Reclassificado aqui, com `mutation_score` ausente.

Uso:
    python scripts/analyze.py                 # tabelas no terminal
    python scripts/analyze.py --md saida.md   # também grava em Markdown
    python scripts/analyze.py --por-modelo pm.md  # + tabelas pareadas por modelo
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import BENCHMARK_DIR  # noqa: E402
from infra.project_loader import load_project  # noqa: E402

RESULTS = Path("output/results.jsonl")

# AD-23: só estes dois braços têm massa para estatística.
BRACOS_QUANT = ("A2", "A4")

# AD-24: projetos fora da população medível. Deram zero convergências em TODOS os
# braços e TODAS as repetições, por defeito de infraestrutura e não do modelo:
# os três primeiros nunca tiveram o pacote instalado no container (o `|| true` de
# docker/Dockerfile.base:13 engole a falha de build); o quarto exige `socket` e
# `subprocess`, que o guarda de AD-05 proíbe. Os runs permanecem no JSONL e no
# custo total declarado, mas saem de TODAS as métricas — exceção deliberada a
# AD-08, porque aqui o desperdício é da infraestrutura, não da arquitetura, e
# atinge os quatro braços igualmente (15 runs cada).
FORA_DA_POPULACAO = frozenset({"tnz", "solvOR", "fabric-rti-mcp", "reviewable-html-workbench"})
TODOS = ("A1", "A2", "A3", "A4")

# Status que a coleta considera definitivos (AD-10). `crashed` é reexecutável.
FINAIS = {"completed", "failed", "aborted_budget", "timeout"}


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------
@dataclass
class Run:
    """Uma célula do experimento: arquitetura × projeto × repetição."""

    run_key: str
    architecture: str
    project: str
    repetition: int
    status: str
    converged: bool
    reclassificado: bool  # convergência recuperada por AD-22
    custo: float  # somado sobre TODAS as tentativas (AD-12)
    line_coverage: float | None
    branch_coverage: float | None
    mutation_score: float | None
    mutants_killed: int | None
    repair_iterations: int
    reflexion_iterations: int
    ast_rejections: int
    discarded_regression: bool
    failure_reason: str | None
    mutation_error: str | None  # por que mutmut não produziu score, quando não produziu
    model: str = ""  # só usado por --por-modelo; o relatório padrão agrega os modelos


def _verde(reg: dict) -> bool:
    """Suite executou e nada falhou (espelha ExecutionResult.all_passed)."""
    e = reg.get("execution") or {}
    rodou = (e.get("passed") or 0) + (e.get("failed") or 0) + (e.get("errors") or 0)
    return rodou > 0 and not e.get("failed") and not e.get("errors")


def _modelo_da_chave(chave: str) -> str:
    """`{arquitetura}__{modelo}__{projeto}__rep-{n}` (AD-10) → modelo."""
    partes = chave.split("__")
    return partes[1] if len(partes) >= 4 else ""


def carregar(caminho: Path) -> tuple[list[Run], dict[str, int]]:
    """Aplica AD-12 (dedup) e AD-22 (reclassificação). Devolve runs e diagnóstico."""
    definitiva: dict[str, dict] = {}
    custo: dict[str, float] = defaultdict(float)
    tentativas: dict[str, int] = defaultdict(int)

    with caminho.open(encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha:
                continue
            reg = json.loads(linha)
            chave = reg["run_key"]
            # Custo soma sempre: tentativa perdida também foi paga (AD-08).
            custo[chave] += reg.get("cost_usd") or 0.0
            tentativas[chave] += 1
            # Métrica vem da tentativa definitiva; `crashed` cede lugar (AD-12).
            anterior = definitiva.get(chave)
            if anterior is None or anterior["status"] not in FINAIS:
                definitiva[chave] = reg

    runs: list[Run] = []
    diag = {"crashed": 0, "reclassificados": 0, "duplicadas": 0}
    for chave, reg in definitiva.items():
        if tentativas[chave] > 1:
            diag["duplicadas"] += 1
        if reg["status"] not in FINAIS:
            diag["crashed"] += 1
            continue

        convergiu = bool(reg.get("converged"))
        reclass = False
        # AD-22: timeout de mutação não invalida convergência já demonstrada.
        if (
            not convergiu
            and reg["status"] == "timeout"
            and _verde(reg)
            and reg.get("line_coverage") is not None
        ):
            convergiu, reclass = True, True
            diag["reclassificados"] += 1

        e = reg.get("execution") or {}
        runs.append(
            Run(
                run_key=chave,
                architecture=reg["architecture"],
                project=reg["project"],
                repetition=reg["repetition"],
                status=reg["status"],
                converged=convergiu,
                reclassificado=reclass,
                custo=custo[chave],
                line_coverage=reg.get("line_coverage") if convergiu else None,
                branch_coverage=reg.get("branch_coverage") if convergiu else None,
                mutation_score=reg.get("mutation_score") if convergiu else None,
                mutants_killed=e.get("mutants_killed") if convergiu else None,
                repair_iterations=reg.get("repair_iterations") or 0,
                reflexion_iterations=reg.get("reflexion_iterations") or 0,
                ast_rejections=reg.get("ast_rejections") or 0,
                discarded_regression=bool(reg.get("discarded_regression")),
                failure_reason=reg.get("failure_reason"),
                mutation_error=e.get("mutation_error"),
                model=reg.get("model") or _modelo_da_chave(chave),
            )
        )
    return runs, diag


# --------------------------------------------------------------------------
# Tercis de complexidade (AD-16)
# --------------------------------------------------------------------------
def tercis(projetos: set[str]) -> dict[str, str]:
    """Mapeia projeto → banda de CC média. `Waza` está fora da coleta (AD-21).

    As bandas são **reestratificadas** sobre o conjunto recebido, não herdadas do
    desenho original. Com o recorte de AD-24 isso dá 5/6/6 em vez dos 7/7/7 de 21
    projetos, e três projetos trocam de banda (`aioamazondevices` e `boltons` de
    baixa para média, `k0dasm` de média para alta) — porque os quatro excluídos
    eram majoritariamente de alta CC e a distribuição inteira desloca para baixo.

    A escolha é deliberada: AD-16 define os tercis como partição do *próprio pool*,
    e a leitura fiel a isso é reparticionar o pool efetivo. O custo é grau de
    liberdade do pesquisador — a partição passa a depender de quais projetos
    sobreviveram —, declarado como ameaça à validade em AD-24.
    """
    cc: dict[str, float] = {}
    for nome in sorted(projetos):
        raiz = BENCHMARK_DIR / nome
        if not raiz.is_dir():
            continue
        ctx = load_project(raiz)
        cc[nome] = ctx.total_complexity / ctx.n_blocks if ctx.n_blocks else 0.0

    ordenados = sorted(cc, key=lambda p: cc[p])
    n = len(ordenados)
    corte1, corte2 = n // 3, 2 * n // 3
    banda = {}
    for i, p in enumerate(ordenados):
        banda[p] = "baixa" if i < corte1 else ("média" if i < corte2 else "alta")
    return banda


# --------------------------------------------------------------------------
# Métricas de custo-qualidade
# --------------------------------------------------------------------------
@dataclass
class Celula:
    """Agregado de uma arquitetura num projeto, sobre as repetições."""

    arquitetura: str
    projeto: str
    n_runs: int = 0
    n_convergidos: int = 0
    custo_total: float = 0.0  # inclui runs que falharam (AD-08)
    coberturas: list[float] = field(default_factory=list)
    mutacoes: list[float] = field(default_factory=list)
    mortos: list[int] = field(default_factory=list)

    @property
    def taxa_convergencia(self) -> float:
        return self.n_convergidos / self.n_runs if self.n_runs else 0.0

    @property
    def custo_medio(self) -> float:
        """Custo esperado de UMA tentativa, convergindo ou não."""
        return self.custo_total / self.n_runs if self.n_runs else 0.0

    @property
    def cobertura_media(self) -> float | None:
        return statistics.mean(self.coberturas) if self.coberturas else None

    @property
    def mutacao_media(self) -> float | None:
        return statistics.mean(self.mutacoes) if self.mutacoes else None

    @property
    def ccp(self) -> float | None:
        """Custo por ponto de cobertura, em US$ por ponto percentual.

        Numerador é o custo MÉDIO POR TENTATIVA, não o custo do run que
        convergiu: é assim que o desperdício de A1/A3 aparece no preço (AD-08).
        """
        cob = self.cobertura_media
        if not cob:
            return None
        return self.custo_medio / cob

    @property
    def cpkm(self) -> float | None:
        """Custo por mutante morto, em US$ por mutante."""
        if not self.mortos:
            return None
        media = statistics.mean(self.mortos)
        return self.custo_medio / media if media else None


def celulas(runs: list[Run]) -> dict[tuple[str, str], Celula]:
    saida: dict[tuple[str, str], Celula] = {}
    for r in runs:
        k = (r.architecture, r.project)
        c = saida.setdefault(k, Celula(r.architecture, r.project))
        c.n_runs += 1
        c.custo_total += r.custo
        if r.converged:
            c.n_convergidos += 1
            if r.line_coverage is not None:
                c.coberturas.append(r.line_coverage)
            if r.mutation_score is not None:
                c.mutacoes.append(r.mutation_score)
            if r.mutants_killed:
                c.mortos.append(r.mutants_killed)
    return saida


# --------------------------------------------------------------------------
# Estatística (AD-23: só A2×A4)
# --------------------------------------------------------------------------
def wilcoxon_pareado(cel: dict[tuple[str, str], Celula], metrica: str) -> dict:
    """Wilcoxon pareado A2×A4, pareando por projeto."""
    from scipy import stats

    a, b, projetos = [], [], []
    for proj in sorted({p for _, p in cel}):
        x = cel.get(("A2", proj))
        y = cel.get(("A4", proj))
        if x is None or y is None:
            continue
        vx, vy = getattr(x, metrica), getattr(y, metrica)
        if vx is None or vy is None:
            continue
        a.append(vx)
        b.append(vy)
        projetos.append(proj)

    if len(a) < 6:
        return {"n": len(a), "erro": "pares insuficientes (< 6) para inferência"}

    dif = [u - v for u, v in zip(a, b, strict=True)]
    if all(d == 0 for d in dif):
        return {"n": len(a), "erro": "todas as diferenças são nulas"}

    est, p = stats.wilcoxon(a, b)

    # Tamanho de efeito: correlação bisserial de postos pareada,
    # r = (W+ − W−) / (W+ + W−), em [−1, +1]. Positivo = A2 maior.
    # Não usar r = Z/sqrt(N) invertendo o p: em n pequeno o scipy usa a
    # distribuição EXATA, e recuperar Z da normal daria |r| > 1.
    return {
        "n": len(a),
        "estatistica": est,
        "p": p,
        "r": _r_bisserial(dif),
        "mediana_A2": statistics.median(a),
        "mediana_A4": statistics.median(b),
        "projetos": projetos,
    }


def _r_bisserial(dif: list[float]) -> float:
    """r = (W+ − W−) / (W+ + W−) sobre as diferenças não nulas, em [−1, +1]."""
    naonulas = [d for d in dif if d != 0]
    postos = _postos_medios([abs(d) for d in naonulas])
    w_pos = sum(w for d, w in zip(naonulas, postos, strict=True) if d > 0)
    w_neg = sum(w for d, w in zip(naonulas, postos, strict=True) if d < 0)
    total = w_pos + w_neg
    return (w_pos - w_neg) / total if total else 0.0


def _postos_medios(valores: list[float]) -> list[float]:
    """Postos 1..n com média nos empates, na ordem original da entrada."""
    ordem = sorted(range(len(valores)), key=lambda i: valores[i])
    postos = [0.0] * len(valores)
    i = 0
    while i < len(ordem):
        j = i
        while j + 1 < len(ordem) and valores[ordem[j + 1]] == valores[ordem[i]]:
            j += 1
        media = (i + j) / 2 + 1  # postos são 1-indexados
        for k in range(i, j + 1):
            postos[ordem[k]] = media
        i = j + 1
    return postos


def kruskal_por_banda(runs: list[Run], banda: dict[str, str], arq: str, campo: str) -> dict:
    """Kruskal-Wallis do valor entre os três tercis de CC, dentro de um braço."""
    from scipy import stats

    grupos: dict[str, list[float]] = {"baixa": [], "média": [], "alta": []}
    for r in runs:
        if r.architecture != arq or not r.converged:
            continue
        v = getattr(r, campo)
        if v is None:
            continue
        b = banda.get(r.project)
        if b:
            grupos[b].append(v)

    usaveis = {k: v for k, v in grupos.items() if len(v) >= 3}
    if len(usaveis) < 2:
        contagem = {k: len(v) for k, v in grupos.items()}
        return {"erro": "menos de duas bandas com n>=3", "n": contagem}
    h, p = stats.kruskal(*usaveis.values())
    return {
        "H": h,
        "p": p,
        "n": {k: len(v) for k, v in grupos.items()},
        "medianas": {k: statistics.median(v) for k, v in usaveis.items()},
    }


# --------------------------------------------------------------------------
# Relatório
# --------------------------------------------------------------------------
def f(v, casas=3, vazio="—"):
    return vazio if v is None else f"{v:.{casas}f}"


def pct(v, casas=1, vazio="—"):
    """Renderiza fração 0–1 como percentual.

    `mutation_score` é gravado por `mutation_score_from_stats` como killed/total,
    ou seja fração 0–1, enquanto `line_coverage` vem do pytest-cov já em pontos
    percentuais. As duas escalas conviviam lado a lado nas tabelas, e uma mutação
    de 0.457 (45,7%) era lida como "0,4%" por contágio da coluna vizinha.
    Toda coluna de mutação passa por aqui para que a escala seja explícita.
    """
    return vazio if v is None else f"{100 * v:.{casas}f}%"


def relatorio(runs: list[Run], diag: dict, *, excluidos: frozenset[str] = frozenset()) -> str:
    L: list[str] = []
    add = L.append
    todos_runs = runs
    if excluidos:
        runs = [r for r in runs if r.project not in excluidos]
    cel = celulas(runs)
    banda = tercis({r.project for r in runs})

    add("# Resultados da coleta\n")
    total_custo = sum(r.custo for r in todos_runs)
    add(f"- Runs: **{len(todos_runs)}**, custo total **US$ {total_custo:.2f}**")
    add(f"- `run_key` com mais de uma tentativa (AD-12): {diag['duplicadas']}")
    add(f"- Convergências recuperadas por AD-22: **{diag['reclassificados']}**")
    if excluidos:
        n_proj = len({r.project for r in runs})
        fora = ", ".join(f"`{x}`" for x in sorted(excluidos))
        add(
            f"- **AD-24: recorte sobre {n_proj} projetos** ({len(runs)} runs). "
            f"Fora da população medível: {fora} — zero convergências em todos os "
            "braços, por falha de build silenciosa e incompatibilidade com AD-05. "
            f"O custo dos {len(todos_runs) - len(runs)} runs excluídos permanece "
            "no total acima."
        )
    if diag["crashed"]:
        add(f"- ATENÇÃO: {diag['crashed']} runs em `crashed`, sem status definitivo")
    add("")

    # --- 1. Panorama por arquitetura -------------------------------------
    add("## 1. Convergência e custo por arquitetura\n")
    add("| Arq | runs | converg. | taxa | com CPKM | custo total | custo/run |")
    add("|-----|-----:|---------:|-----:|---------:|------------:|----------:|")
    for arq in TODOS:
        g = [r for r in runs if r.architecture == arq]
        if not g:
            continue
        conv = [r for r in g if r.converged]
        cpkm = [r for r in g if r.mutation_score is not None]
        c = sum(r.custo for r in g)
        add(
            f"| {arq} | {len(g)} | {len(conv)} | {100 * len(conv) / len(g):.0f}% | "
            f"{len(cpkm)} | {c:.2f} | {c / len(g):.4f} |"
        )
    add("")
    if excluidos:
        add("Convergência nas duas populações, para comparação:")
        add("")
        add("| Arq | 21 projetos | 17 saudáveis (AD-24) |")
        add("|-----|------------:|---------------------:|")
        for arq in TODOS:
            a = [r for r in todos_runs if r.architecture == arq]
            b = [r for r in runs if r.architecture == arq]
            if not a or not b:
                continue
            ca = sum(1 for r in a if r.converged)
            cb = sum(1 for r in b if r.converged)
            add(
                f"| {arq} | {ca}/{len(a)} = {100 * ca / len(a):.0f}% | "
                f"{cb}/{len(b)} = {100 * cb / len(b):.0f}% |"
            )
        add("")
    add(
        "> AD-23: A1 e A3 não sustentam inferência. As linhas abaixo que dependem "
        "de teste estatístico cobrem apenas A2 e A4."
    )
    add("")

    # --- 2. CCP e CPKM ----------------------------------------------------
    add("## 2. CCP e CPKM por arquitetura\n")
    add("CCP em US$ por ponto percentual de cobertura; CPKM em US$ por mutante morto.")
    add("O numerador é o custo médio **por tentativa**, incluindo as que falharam (AD-08).\n")
    add("| Arq | cobertura méd. | mutação méd. | CCP | CPKM |")
    add("|-----|---------------:|-------------:|----:|-----:|")
    for arq in TODOS:
        g = [c for (a, _), c in cel.items() if a == arq]
        if not g:
            continue
        n_runs = sum(c.n_runs for c in g)
        custo_medio = sum(c.custo_total for c in g) / n_runs if n_runs else 0
        cobs = [v for c in g for v in c.coberturas]
        muts = [v for c in g for v in c.mutacoes]
        mortos = [v for c in g for v in c.mortos]
        cob = statistics.mean(cobs) if cobs else None
        mut = statistics.mean(muts) if muts else None
        ccp = custo_medio / cob if cob else None
        cpkm = custo_medio / statistics.mean(mortos) if mortos else None
        marca = "" if arq in BRACOS_QUANT else "  ⚠"
        add(
            f"| {arq}{marca} | {f(cob, 2)}% (n={len(cobs)}) | {pct(mut)} (n={len(muts)}) | "
            f"{f(ccp, 5)} | {f(cpkm, 5)} |"
        )
    add("")
    add("⚠ n insuficiente para interpretação (AD-23).\n")

    # --- 2b. Cobertura da própria métrica de mutação ----------------------
    # O CPKM só existe para runs que produziram mutation_score, e mutmut só roda
    # quando a suite passa inteira (test_executor.py: `if run_mutation and
    # all_passed`, sob AD-14). O denominador do CPKM é portanto uma subpopulação
    # dos runs, não todos eles. Sem esta tabela isso fica escondido, e o viés é
    # direcional: projetos grandes estouram o teto de tempo de mutmut (AD-06) com
    # mais frequência, então o CPKM é medido desproporcionalmente sobre projetos
    # pequenos — viés de sobrevivência que precisa aparecer no artigo.
    add("## 2b. Quantos runs sustentam o CPKM\n")
    add(
        "mutmut só roda em suite que passa inteira (AD-14), então o CPKM é medido "
        "sobre uma subpopulação. As ausências abaixo não são dados faltantes: são "
        "runs que não chegaram à fase de mutação.\n"
    )
    add("| Arq | runs | com score | sem score | não convergiu | timeout mutmut | erro mutmut |")
    add("|-----|-----:|----------:|----------:|--------------:|---------------:|------------:|")
    for arq in TODOS:
        g = [r for r in runs if r.architecture == arq]
        if not g:
            continue
        com = [r for r in g if r.mutation_score is not None]
        sem = [r for r in g if r.mutation_score is None]
        t_out = [r for r in sem if r.mutation_error and "excedeu" in r.mutation_error]
        erro = [r for r in sem if r.mutation_error and "excedeu" not in r.mutation_error]
        nao_conv = [r for r in sem if not r.mutation_error]
        add(
            f"| {arq} | {len(g)} | {len(com)} | {len(sem)} | {len(nao_conv)} | "
            f"{len(t_out)} | {len(erro)} |"
        )
    add("")
    perdidos = [r for r in runs if r.mutation_score is None and r.mutation_error]
    if perdidos:
        t_out = [r for r in perdidos if "excedeu" in (r.mutation_error or "")]
        add(
            f"{len(perdidos)} run(s) chegaram à fase de mutação e mesmo assim não "
            f"produziram score — {len(t_out)} por estourar o teto de tempo de AD-06. "
            "Esses são perda de medição, não resultado de qualidade.\n"
        )
        proj = defaultdict(int)
        for r in t_out:
            proj[r.project] += 1
        if proj:
            ordenado = sorted(proj.items(), key=lambda kv: -kv[1])
            add(
                "Timeouts de mutmut por projeto: "
                + ", ".join(f"{p} ({n})" for p, n in ordenado)
                + "\n"
            )

    # --- 3. Wilcoxon A2×A4 ------------------------------------------------
    add("## 3. Wilcoxon pareado por projeto — A2 × A4\n")
    add("| Métrica | n pares | estatística | p | r | mediana A2 | mediana A4 |")
    add("|---------|--------:|------------:|--:|--:|-----------:|-----------:|")
    for rotulo, metrica, fmt in (
        ("Cobertura", "cobertura_media", lambda v: f(v, 2)),
        ("Mutação", "mutacao_media", pct),  # fração 0–1 → percentual
        ("Custo/run", "custo_medio", lambda v: f(v, 4)),
        ("CCP", "ccp", lambda v: f(v, 5)),
        ("CPKM", "cpkm", lambda v: f(v, 5)),
    ):
        w = wilcoxon_pareado(cel, metrica)
        if "erro" in w:
            add(f"| {rotulo} | {w['n']} | — | — | — | — | {w['erro']} |")
            continue
        add(
            f"| {rotulo} | {w['n']} | {w['estatistica']:.1f} | {w['p']:.4f} | "
            f"{w['r']:.2f} | {fmt(w['mediana_A2'])} | {fmt(w['mediana_A4'])} |"
        )
    add("")

    # --- 4. Kruskal-Wallis por tercil ------------------------------------
    add("## 4. Kruskal-Wallis entre tercis de complexidade\n")
    contagem = defaultdict(list)
    for p, b in banda.items():
        contagem[b].append(p)
    add(
        "Bandas: "
        + ", ".join(f"{b} ({len(contagem[b])} projetos)" for b in ("baixa", "média", "alta"))
        + "\n"
    )
    add("| Arq | métrica | H | p | n baixa | n média | n alta |")
    add("|-----|---------|--:|--:|--------:|--------:|-------:|")
    for arq in BRACOS_QUANT:
        for rotulo, campo in (("cobertura", "line_coverage"), ("mutação", "mutation_score")):
            k = kruskal_por_banda(runs, banda, arq, campo)
            if "erro" in k:
                n = k.get("n", {})
                add(
                    f"| {arq} | {rotulo} | — | {k['erro']} | {n.get('baixa', 0)} | "
                    f"{n.get('média', 0)} | {n.get('alta', 0)} |"
                )
                continue
            add(
                f"| {arq} | {rotulo} | {k['H']:.2f} | {k['p']:.4f} | {k['n']['baixa']} | "
                f"{k['n']['média']} | {k['n']['alta']} |"
            )
    add("")

    # --- 5. Comportamento dos laços --------------------------------------
    add("## 5. Comportamento dos laços e do guarda AST\n")
    add("| Arq | repair méd. | reflexion méd. | rejeições AST | regressões descartadas |")
    add("|-----|------------:|---------------:|--------------:|-----------------------:|")
    for arq in TODOS:
        g = [r for r in runs if r.architecture == arq]
        if not g:
            continue
        add(
            f"| {arq} | {statistics.mean(r.repair_iterations for r in g):.2f} | "
            f"{statistics.mean(r.reflexion_iterations for r in g):.2f} | "
            f"{sum(r.ast_rejections for r in g)} | "
            f"{sum(1 for r in g if r.discarded_regression)} |"
        )
    add("")

    # --- 6. Motivos de falha ---------------------------------------------
    add("## 6. Motivos de falha\n")
    add("| Arq | motivo | n |")
    add("|-----|--------|--:|")
    for arq in TODOS:
        motivos: dict[str, int] = defaultdict(int)
        for r in runs:
            if r.architecture == arq and not r.converged:
                motivos[_classifica(r.failure_reason)] += 1
        for motivo, n in sorted(motivos.items(), key=lambda kv: -kv[1]):
            add(f"| {arq} | {motivo} | {n} |")
    add("")
    return "\n".join(L)


def _classifica(motivo: str | None) -> str:
    """Agrupa as mensagens livres nas famílias que interessam ao artigo."""
    m = (motivo or "sem motivo registrado").lower()
    if "ast rejeitou" in m:
        return "guarda AST rejeitou a suite (AD-05)"
    if "não executam" in m or "nao executam" in m:
        return "suite não executa (erro de import/sintaxe/setup)"
    if "falhando após esgotar" in m or "falhando apos esgotar" in m:
        return "testes vermelhos após esgotar o pipeline"
    if "timeout" in m:
        return "timeout de execução (AD-06)"
    if "orçamento" in m or "orcamento" in m or "budget" in m:
        return "breaker financeiro (AD-09)"
    return "outro"


# --------------------------------------------------------------------------
# Comparações separadas por modelo (--por-modelo)
# --------------------------------------------------------------------------
# Saída opcional: não altera o relatório padrão, que agrega os modelos por
# (arquitetura, projeto). Aqui cada modelo tem as próprias células.
MODELOS_COMPARADOS = ("gpt-5-mini", "claude-sonnet-5")

METRICAS_POR_MODELO = (
    ("Convergência", "taxa_convergencia", lambda v: f(v, 3)),
    ("Cobertura", "cobertura_media", lambda v: f(v, 2)),
    ("Mutação (fração 0–1)", "mutacao_media", lambda v: f(v, 3)),
    ("Custo/exec", "custo_medio", lambda v: f(v, 4)),
    ("CCP", "ccp", lambda v: f(v, 5)),
    ("CPKM", "cpkm", lambda v: f(v, 5)),
)


def comparar_pareado(
    x: dict[str, Celula], y: dict[str, Celula], projetos: list[str], metrica: str
) -> dict:
    """Wilcoxon pareado por projeto entre duas condições, com a perda de n explicada.

    Cada projeto da população que sai do teste sai por um de dois motivos:
    (a) `sem_medida` — métrica indefinida numa das condições (nenhuma execução
        convergida: sem cobertura, mutação, CCP ou CPKM);
    (b) `empates` — diferença exatamente zero, descartada pelo Wilcoxon
        (zero_method="wilcox").
    `n` = len(projetos) − (a) − (b). Os empates saem ANTES do scipy: o descarte é
    o mesmo, mas com zeros na entrada o scipy troca a distribuição exata pela
    aproximação normal. Medianas e r usam os mesmos n pares do teste.
    """
    from scipy import stats

    a, b, sem_medida, empates, usados = [], [], [], [], []
    for proj in projetos:
        cx, cy = x.get(proj), y.get(proj)
        vx = getattr(cx, metrica) if cx is not None else None
        vy = getattr(cy, metrica) if cy is not None else None
        if vx is None or vy is None:
            sem_medida.append(proj)
        elif vx == vy:
            empates.append(proj)
        else:
            a.append(vx)
            b.append(vy)
            usados.append(proj)

    base = {"n": len(a), "sem_medida": sem_medida, "empates": empates, "projetos": usados}
    if len(a) < 6:
        return base | {"erro": "pares insuficientes (< 6) para inferência"}

    est, p = stats.wilcoxon(a, b)
    dif = [u - v for u, v in zip(a, b, strict=True)]
    return base | {
        "estatistica": est,
        "p": p,
        "r": _r_bisserial(dif),
        "mediana_x": statistics.median(a),
        "mediana_y": statistics.median(b),
    }


def _p(v: float) -> str:
    return "<0.0001" if v < 0.0001 else f"{v:.4f}"


def _celulas_do_modelo(runs: list[Run], modelo: str, arq: str) -> dict[str, Celula]:
    cel = celulas([r for r in runs if r.model == modelo])
    return {proj: c for (a, proj), c in cel.items() if a == arq}


def _tabela_pareada(add, x, y, projetos: list[str], rx: str, ry: str) -> None:
    add(f"| Métrica | n | sem medida (a) | empate (b) | W | p | r | mediana {rx} | mediana {ry} |")
    add("|---------|--:|---------------:|-----------:|--:|--:|--:|------:|------:|")
    detalhes = []
    for rotulo, metrica, fmt in METRICAS_POR_MODELO:
        w = comparar_pareado(x, y, projetos, metrica)
        detalhes.append((rotulo, w))
        na, nb = len(w["sem_medida"]), len(w["empates"])
        if "erro" in w:
            add(f"| {rotulo} | {w['n']} | {na} | {nb} | — | — | — | — | {w['erro']} |")
            continue
        add(
            f"| {rotulo} | {w['n']} | {na} | {nb} | {w['estatistica']:.1f} | {_p(w['p'])} | "
            f"{w['r']:.2f} | {fmt(w['mediana_x'])} | {fmt(w['mediana_y'])} |"
        )
    add("")
    for rotulo, w in detalhes:
        partes = []
        if w["sem_medida"]:
            partes.append("(a) " + ", ".join(f"`{p}`" for p in w["sem_medida"]))
        if w["empates"]:
            partes.append("(b) " + ", ".join(f"`{p}`" for p in w["empates"]))
        if partes:
            add(f"- {rotulo}: " + "; ".join(partes))
    add("")


def relatorio_por_modelo(runs: list[Run], *, excluidos: frozenset[str] = frozenset()) -> str:
    """Tabelas pareadas separadas por modelo, com a decomposição do n por linha."""
    L: list[str] = []
    add = L.append
    runs = [r for r in runs if r.project not in excluidos]
    projetos = sorted({r.project for r in runs})
    modelos = [m for m in MODELOS_COMPARADOS if any(r.model == m for r in runs)]
    rx, ry = BRACOS_QUANT

    add("# Comparações pareadas por modelo\n")
    recorte = "recorte de AD-24" if excluidos else "sem o recorte de AD-24"
    add(
        f"- População: **{len(projetos)} projetos** ({recorte}); convergência com a "
        "reclassificação de AD-22; custo somado sobre todas as tentativas (AD-12)."
    )
    add(
        "- n = projetos − (a) − (b). **(a)** sem medida: métrica indefinida numa das "
        "condições (nenhuma execução convergida). **(b)** empate: diferença zero, "
        "descartada pelo Wilcoxon."
    )
    add("- r = correlação bisserial de postos pareada; positivo = primeira condição maior.")
    add("- Medianas por projeto, sobre os n pares que entram no teste.\n")

    add(f"## {rx} × {ry} dentro de cada modelo\n")
    for m in modelos:
        add(f"### {m}\n")
        x, y = _celulas_do_modelo(runs, m, rx), _celulas_do_modelo(runs, m, ry)
        _tabela_pareada(add, x, y, projetos, rx, ry)

    if len(modelos) == 2:
        m1, m2 = modelos
        add(f"## {m1} × {m2} dentro de cada arquitetura\n")
        for arq in BRACOS_QUANT:
            add(f"### {arq}\n")
            x, y = _celulas_do_modelo(runs, m1, arq), _celulas_do_modelo(runs, m2, arq)
            _tabela_pareada(add, x, y, projetos, m1, m2)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jsonl", type=Path, default=RESULTS)
    ap.add_argument("--md", type=Path, help="também grava o relatório neste arquivo")
    ap.add_argument(
        "--todos-projetos",
        action="store_true",
        help="não aplica o recorte de AD-24 (volta aos 21 projetos)",
    )
    ap.add_argument(
        "--por-modelo",
        type=Path,
        help="grava também as comparações pareadas separadas por modelo neste arquivo",
    )
    args = ap.parse_args()

    if not args.jsonl.exists():
        print(f"não encontrei {args.jsonl}", file=sys.stderr)
        return 1

    runs, diag = carregar(args.jsonl)
    excluidos = frozenset() if args.todos_projetos else FORA_DA_POPULACAO
    texto = relatorio(runs, diag, excluidos=excluidos)
    print(texto)
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(texto, encoding="utf-8")
        print(f"\n[gravado em {args.md}]")
    if args.por_modelo:
        args.por_modelo.parent.mkdir(parents=True, exist_ok=True)
        texto_pm = relatorio_por_modelo(runs, excluidos=excluidos)
        args.por_modelo.write_text(texto_pm, encoding="utf-8")
        print(f"[comparações por modelo gravadas em {args.por_modelo}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
