"""Configurações centrais do pipeline de seleção de benchmark.

Todos os limiares aqui implementam diretamente os critérios de
inclusão/exclusão definidos na Seção 4.4 da Metodologia do TCC. Ajuste
estes valores conforme decisões metodológicas mudem -- evite alterar a
lógica em criteria.py diretamente, prefira mudar os parâmetros aqui.

A maioria também pode ser sobrescrita via flags de CLI (ver `python
cli.py --help`).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------
# Critérios de inclusão / exclusão (Seção 4.4 da Metodologia)
# --------------------------------------------------------------------------

LOC_MIN = 300
# LOC_MAX elevado de 8000 -> 12000 (decisão metodológica, 2026-07): a análise
# empírica do pool mostrou que complexidade correlaciona com tamanho, e o teto
# de 8000 barrava justamente os candidatos de faixa média que TÊM suíte de
# testes (ex.: solvOR cc=6.6/10460 LOC). 12000 é o mínimo que fecha a faixa
# média (>=5 aprovados) sem inflar demais o esforço do experimento de geração
# de testes (TCC II). A faixa alta é insensível a este limiar.
LOC_MAX = 12000

# Faixas de complexidade ciclomática (limiares aplicados à métrica escolhida)
CC_BAIXA_MAX = 5.0      # baixa:  CC < 5
CC_MEDIA_MAX = 10.0     # média:  5 <= CC < 10   |   alta: >= 10

# Métrica de complexidade que define a faixa. 'media' (= radon cc -a) é a
# vigente; 'pico' (máx por bloco) e 'p90' (percentil 90) são alternativas que
# não diluem núcleos complexos -- usadas via subcomando `reclassify`, que grava
# em output/<metrica>/ sem alterar a saída da métrica média em output/.
CC_METRIC = "media"
CC_METRICS_VALIDAS = ("media", "pico", "p90")

# Licenças OSI aceitas (spdx_id em minúsculas, conforme retornado pela API
# do GitHub). Revise esta lista se o critério metodológico mudar.
# "noassertion" é tratado como aviso (não rejeição) em criteria.py, pois a
# detecção automática do GitHub falha com licenças customizadas/combinadas.
LICENCAS_ACEITAS = {
    "mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "bsd-3-clause-clear",
    "gpl-2.0", "gpl-3.0", "lgpl-2.1", "lgpl-3.0", "agpl-3.0",
    "mpl-2.0", "isc", "unlicense", "0bsd",
    "eupl-1.1", "eupl-1.2",       # European Union Public License (OSI aprovada)
    "epl-1.0", "epl-2.0",         # Eclipse Public License (OSI aprovada)
    "artistic-2.0",                # Artistic License (OSI aprovada)
    "osl-3.0",                     # Open Software License (OSI aprovada)
    "ms-pl",                       # Microsoft Public License (OSI aprovada)
    "cc0-1.0",                     # Creative Commons Zero (domínio público -- sem restrições)
}

ATIVIDADE_MAX_DIAS = 730            # inclusão: último push <= 24 meses
COBERTURA_INICIAL_MAX = 0.95        # exclusão: cobertura inicial > 95%
IPYNB_RATIO_MAX = 0.50              # exclusão: maioria dos arquivos é .ipynb

# --------------------------------------------------------------------------
# Mitigação de contaminação de dados de treinamento
# --------------------------------------------------------------------------
# Cutoff mais conservador (mais antigo) entre os 6 LLMs avaliados no estudo:
# {Claude Opus 4.7, Gemini 3.1 Pro Preview, GPT-5.5} (tier frontier) e
# {Claude Sonnet 4.6, Gemini 2.5 Flash, GPT-4o mini} (tier custo-efetivo).
#
# Verificado em 2026-06: o cutoff oficial de GPT-4o mini é 2023-10-01, o mais
# antigo entre os seis. IMPORTANTE: confirme esta data junto à documentação
# oficial de cada provedor antes de rodar a triagem definitiva -- cutoffs
# podem ser atualizados pelos provedores, e a tabela completa deve constar
# na Seção 4.4 (ameaça à validade de construto/repetibilidade).
CONTAMINATION_CUTOFF = date(2023, 10, 1)

# --------------------------------------------------------------------------
# Parâmetros de busca (GitHub Search API) -- subcomando `search`
# --------------------------------------------------------------------------

N_CANDIDATOS_ALVO = 40

# Múltiplas queries estratificadas por faixa de estrelas e por tópico, para
# reduzir o viés de amostragem em direção apenas aos repositórios mais
# populares (ameaça à validade externa / representatividade do benchmark).
SEARCH_QUERIES = [
    "language:Python stars:50..300 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python stars:300..1500 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python stars:1500..8000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:cli stars:30..3000 pushed:>{since} fork:false archived:false",
    "language:Python topic:parser stars:30..3000 pushed:>{since} fork:false archived:false",
    "language:Python topic:data-processing stars:30..3000 pushed:>{since} fork:false archived:false",
    "language:Python topic:web-framework stars:30..5000 pushed:>{since} fork:false archived:false",
    "language:Python topic:algorithms stars:30..3000 pushed:>{since} fork:false archived:false",
    # Domínios com maior complexidade ciclomática por função (alvo das faixas
    # média/alta, sub-representadas no pool inicial). size:<8000 pré-filtra
    # repos grandes demais (LOC_MAX), já que complexidade costuma correlacionar
    # com tamanho. Ver Seção 4.4: estratificação por CC exige cobrir a faixa
    # alta, naturalmente rara (CC médio >= 10 é incomum em repo inteiro).
    "language:Python topic:compiler stars:30..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:interpreter stars:30..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:emulator stars:30..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:static-analysis stars:30..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:virtual-machine stars:30..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:sat-solver stars:20..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:constraint-programming stars:20..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:query-engine stars:20..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:regex stars:20..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:game-engine stars:20..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:chess stars:20..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:state-machine stars:20..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:type-checker stars:20..4000 pushed:>{since} size:<8000 fork:false archived:false",
    # Rodada focada em faixa ALTA (CC>=10): domínios de alta densidade
    # algorítmica/de ramificação por função. size:<8000 mantido para não
    # esbarrar em LOC_MAX enquanto o teto não for revisto.
    "language:Python topic:decompiler stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:disassembler stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:transpiler stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:bytecode stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:solver stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:optimization stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:pathfinding stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:physics-engine stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:code-generation stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:cryptography stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    # Reforço adicional para a faixa ALTA (rodada 2026-07): o round-robin do
    # search distribui slots por query, então mais queries de domínio de alta
    # CC aumentam a proporção desses candidatos no pool sem tocar nos limiares
    # (que são decisão metodológica da Seção 4.4).
    "language:Python topic:assembler stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:parsing stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:symbolic-computation stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:computer-algebra stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:automata stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:graph-algorithms stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:computational-geometry stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:scheduling stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:model-checking stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
    "language:Python topic:theorem-proving stars:10..4000 pushed:>{since} size:<8000 fork:false archived:false",
]

RESULTS_PER_QUERY = 20  # por query/página; o pool é deduplicado e truncado em N_CANDIDATOS_ALVO
MAX_SEARCH_PAGES = 4    # varre até 4 páginas por query buscando candidatos inéditos

# --------------------------------------------------------------------------
# Execução / isolamento
# --------------------------------------------------------------------------

WORKDIR = Path("./work").resolve()        # clones + venvs temporários (limpo por candidato)
OUTPUT_DIR = Path("./output").resolve()    # candidatos_raw.json, results.jsonl, relatórios, log

CLONE_TIMEOUT_S = 120
PIP_INSTALL_TIMEOUT_S = 300

# Pin do mutmut instalado no venv de cada candidato. Alinhado à major version
# usada pelo executor do experimento (pipeline_scaffold, mutmut 3.x): o dry-run
# de compatibilidade deve exercitar a MESMA ferramenta que medirá CPKM no
# TCC II, senão o flag mutmut_compativel não vale para o experimento.
MUTMUT_PIN = "mutmut>=3.0,<4"
PYTEST_COLLECT_TIMEOUT_S = 60
PYTEST_RUN_TIMEOUT_S = 300
MUTMUT_DRYRUN_TIMEOUT_S = 90

# Teto agregado por candidato: se a soma de todos os passos (clone +
# install + collect + coverage + mutmut) exceder este valor, o candidato
# é abortado com status="erro" e o pipeline avança para o próximo.
# Protege contra travamentos imprevistos (ex.: antivírus bloqueando pip,
# subprocess que escapa do kill-tree, network hang silencioso).
CANDIDATE_TIMEOUT_S = 900

GITHUB_API_URL = "https://api.github.com"
