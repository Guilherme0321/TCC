"""Constantes experimentais do pipeline.

FONTE DA VERDADE: docs/ADR_arquiteturas_geracao_testes.md (v2.1).
Estes valores são idênticos entre todos os modelos, projetos e arquiteturas.
NÃO torná-los configuráveis por CLI nem variá-los por célula experimental:
isso confundiria efeito de arquitetura com efeito de hiperparâmetro (AD-07).
"""

import os
from pathlib import Path

# Desenvolvimento local: carrega .env se existir (nunca sobrescreve o ambiente).
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ── Caminhos ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
PROMPTS_DIR = ROOT / "prompts"
RESULTS_JSONL = ROOT / "output" / "results.jsonl"
BENCHMARK_DIR = ROOT / "benchmark"  # projetos selecionados pelo benchmark_selector

# ── Loops (AD-07) ─────────────────────────────────────────────────────────
N_REPAIR = 3  # máx. iterações do repair loop (A2, A4)
N_REFLEXION = 2  # máx. iterações do Reflexion loop (A4)

# ── Memória do Reflexion (AD-03) ──────────────────────────────────────────
REFLEXION_MEMORY_WINDOW = 2  # nº de iterações anteriores mantidas no resumo

# ── Teto de tokens de saída por agente (AD-19) ────────────────────────────
# Constantes experimentais (regra 3): idênticas entre modelos, projetos e
# arquiteturas. Dimensionadas para modelos de RACIOCÍNIO — na família GPT-5 o
# `max_completion_tokens` inclui os tokens de raciocínio invisível, medidos em
# ~74% do output no smoke A4 de 2026-08-10. Com teto apertado, o JSON do
# analyzer/critic trunca antes do fim, vira `AgentOutputError` e o loop
# correspondente é abortado em silêncio — penalizando SÓ os modelos com
# raciocínio, o que seria confundidor. Regra de dimensionamento: ~4x o
# orçamento visível desejado.
MAX_OUTPUT_TOKENS = {
    "analyzer": 8192,  # JSON estrutural do projeto inteiro
    "generator": 16384,  # suíte de testes completa
    "repair": 16384,  # suíte corrigida completa
    "critic": 8192,  # feedback textual estruturado (nunca código, AD-14)
}

# ── Timeouts (AD-06) ──────────────────────────────────────────────────────
PYTEST_TIMEOUT_SEC = 90
MUTMUT_TIMEOUT_SEC = 1800

# ── Circuit breaker financeiro (AD-09, ajustado por AD-15) ────────────────
MAX_COST_PER_RUN_USD = {
    "frontier": 2.00,
    # Elevado de 0.50 para 1.50 em 2026-08-28, para a segunda rodada em
    # `claude-sonnet-5` (AD-25). O valor de 0.50 foi dimensionado sobre o
    # `gpt-5-mini`, cujo A4 mais caro custou $0.0725. No Sonnet 5 a saída
    # sozinha de um A4 típico passa de $0.50: reprecificando os 85 runs de A4
    # já coletados, o mais caro dá $0.89 e 30 passam de $0.50. Mantido em 0.50,
    # o breaker mataria justamente os runs mais caros de A4 e a coleta mediria
    # o disjuntor em vez do modelo — o oposto do que AD-09 existe para fazer.
    # 1.50 preserva a margem de ~1.7x sobre o pior run projetado.
    #
    # Elevado de 1.50 para 2.00 em 2026-08-30, por decisão explícita do autor,
    # depois que a margem projetada se mostrou insuficiente na prática: o run
    # A4/boltons/rep-1 real custou $1.87 e foi abortado pelo disjuntor. Como
    # AD-08 manda não descartar runs, o abort não devolve o dinheiro — as 3
    # repetições gastariam ~$4.50 para produzir zero observação, deixando a
    # célula boltons x A4 vazia no desenho pareado de AD-23. Deixar concluir
    # custa ~$5.70, ou seja ~$1.20 a mais, e preserva a célula. 2.00 é o mesmo
    # valor já vigente no tier frontier, então não introduz um patamar novo.
    "cost": 2.00,
}
# TETO RÍGIDO DE ORÇAMENTO (AD-20). R$ 200 é o limite absoluto definido pelo
# autor em 2026-08-10. Conversão: R$ 200 / 5.49 (câmbio comercial de 5.11 mais
# ~7.5% de IOF e spread de cartão) = $36.4; arredondado para baixo com margem,
# porque a projeção de custo tem incerteza alta (extrapolada de poucos runs).
# Substitui o teto de $250 de AD-09/AD-15, que a projeção revisada mostrou
# insuficiente até para o tier cost-effective completo.
#
# ELEVADO PARA $45.00 em 2026-08-28, por decisão explícita do autor, para
# comportar a segunda rodada de AD-25 (3 repetições x 17 projetos x A2/A4 =
# 102 runs em `claude-sonnet-5`). Projeção medida: $34.69 na rodada, somados
# aos $9.49 já gastos = $44.18. É REVISÃO CONSCIENTE do teto de R$ 200 de
# AD-20, não estouro acidental: $45 equivalem a ~R$ 243. O teto continua
# rígido — o disjuntor de AD-09 aborta ao atingi-lo.
#
# ELEVADO PARA $65.00 em 2026-08-30, terceira e última revisão, por decisão
# explícita do autor. Motivo: os 20 primeiros runs reais de `claude-sonnet-5`
# custaram $10.76 — média de $0.5381/run contra os $0.3755 projetados (43%
# acima). A projeção anterior modelava o output como linear no tamanho do
# prefixo e não capturou a frequência com que o Sonnet 5 satura o teto de
# output de AD-19; runs truncados pagam o output cheio. Extrapolando a média
# medida: 102 runs x $0.5381 = $54.89, mais os $9.49 do gpt-5-mini = $64.38.
# Com o teto em $50 o disjuntor abortaria por volta do run 55, deixando a
# rodada de AD-25 pela metade após gastar ~R$ 270 — pior que qualquer das
# alternativas. $65 equivalem a ~R$ 348. Alternativa descartada pelo autor:
# cair para 2 repetições (cabia em $46, mas enfraquecia o teste de AD-23).
MAX_COST_GLOBAL_USD = 65.00

# ── Modelos (AD-15: batch + caching sempre ativos) ────────────────────────
# Preços em USD por milhão de tokens, standard tier (jun/2026).
# O llm_client aplica o desconto de batch (0.5x) e de cache hit (0.1x no input).
MODELS = {
    # tier frontier
    "claude-opus-4-8": {
        "provider": "anthropic",
        "tier": "frontier",
        "price_in": 5.00,
        "price_out": 25.00,
    },
    "gemini-3.1-pro": {
        "provider": "google",
        "tier": "frontier",
        "price_in": 2.00,
        "price_out": 12.00,
    },
    "gpt-5.5": {
        "provider": "openai",
        "tier": "frontier",
        "price_in": 5.00,
        "price_out": 30.00,
    },
    # tier cost-effective
    "claude-sonnet-4-6": {
        "provider": "anthropic",
        "tier": "cost",
        "price_in": 3.00,
        "price_out": 15.00,
    },
    # Segundo modelo da coleta (AD-25). Preço PROMOCIONAL de lançamento
    # ($2/$10 contra $3/$15 de tabela), válido até 2026-08-31 conforme a doc
    # oficial da Anthropic consultada em 2026-08-28. Se a coleta atravessar a
    # virada do mês, as chamadas posteriores são faturadas a $3/$15 e o custo
    # registrado aqui SUBESTIMA o real em 50% — por isso a coleta precisa
    # terminar dentro da janela, e a data de execução vai no texto como
    # condição do número publicado. Preferido a `claude-sonnet-4-6` por ser o
    # modelo corrente do mesmo tier; a diferença de preço é a promoção.
    "claude-sonnet-5": {
        "provider": "anthropic",
        "tier": "cost",
        "price_in": 2.00,
        "price_out": 10.00,
    },
    "gemini-3.5-flash": {
        "provider": "google",
        "tier": "cost",
        "price_in": 1.50,
        "price_out": 9.00,
    },
    # ID e preço confirmados na doc oficial da OpenAI em 2026-07-17. Deprecação
    # ANUNCIADA: snapshot gpt-5-mini-2025-08-07 desliga em 2026-12-11 (substituto
    # oficial: gpt-5.4-mini, $0.75/$4.50). Disponível durante toda a janela do
    # experimento (jul-set/2026); trocar de modelo é decisão de nível ADR --
    # registrar a data de coleta e a deprecação como ameaça à validade.
    "gpt-5-mini": {
        "provider": "openai",
        "tier": "cost",
        "price_in": 0.25,
        "price_out": 2.00,
    },
}

BATCH_DISCOUNT = 0.5  # Batch API: 50% de desconto em input e output
CACHE_HIT_INPUT_FACTOR = 0.1  # input cacheado custa 10% do preço base
# ESCRITA de cache custa MAIS que o input normal na Anthropic: 1.25x o preço
# base (AD-25). A OpenAI não cobra escrita — desconta apenas a leitura —, e por
# isso o modelo de custo original ignorava este fator sem erro. Com a Anthropic
# em coleta, ignorá-lo subestimaria o gasto e cegaria o disjuntor de AD-09.
# Provedores que não cobram escrita reportam `cache_write_tokens = 0`, o que
# neutraliza o fator sem ramificação por provedor.
CACHE_WRITE_INPUT_FACTOR = 1.25

# Modelos que REJEITAM `temperature`/`top_p`/`top_k` com HTTP 400 (a família
# Claude 4.7+ removeu os parâmetros de amostragem). AD-18 fixa TEMPERATURE=1.0
# como constante experimental; nestes modelos o parâmetro é OMITIDO, não
# alterado. Isso preserva a regra 3: 1.0 é o comportamento default de todos os
# provedores em uso, então omitir produz a MESMA amostragem que enviar 1.0 —
# a constante continua única entre as células, só não é transmitida onde a API
# não a aceita.
NO_SAMPLING_PARAMS = frozenset({"claude-sonnet-5"})

# Nível de esforço de raciocínio, por modelo (AD-26). MEDIDO, não escolhido por
# gosto: no default (`adaptive`, esforço `high`), o Sonnet 5 gasta os 16.384
# tokens do teto de AD-19 inteiramente em raciocínio e devolve ZERO caracteres
# de código — o generator retorna vazio e o run só sobrevive se o laço de
# reparo salvar. Com `medium` ainda trunca em projeto de porte médio (`nox`).
# Com `low` coube em todos os tamanhos testados, mantendo o raciocínio ligado —
# que é a condição em que o `gpt-5-mini` rodou (2,4M tokens de raciocínio).
#
# Isto NÃO viola a regra 3, mas a tensiona: o `gpt-5-mini` rodou no default da
# OpenAI (`reasoning_effort` = medium) e aqui o nível nominal é menor. Não
# existe configuração em que os dois fiquem idênticos — as APIs não expõem os
# mesmos controles —, então a escolha é qual confundidor aceitar. Este é o
# único que preserva MAX_OUTPUT_TOKENS intacto e, com ele, a comparabilidade
# com a coleta de agosto. Declarar como ameaça à validade.
REASONING_EFFORT = {"claude-sonnet-5": "low"}

# ── Batch API e concorrência (AD-15) ──────────────────────────────────────
# ATENÇÃO: estas constantes são OPERACIONAIS, não experimentais. Elas não
# alteram o que o LLM recebe nem o que produz — apenas quantos runs ficam em
# voo e como as chamadas são agrupadas em jobs de batch. Por isso NÃO estão
# sujeitas à regra 3 (constantes experimentais imutáveis): podem ser ajustadas
# por ambiente/máquina sem afetar a comparabilidade entre células.
#
# A Batch API é assíncrona (submit → poll → resultados). Como os loops de
# repair/Reflexion são sequenciais DENTRO de um run, agrupar só faz sentido
# ENTRE runs distintos: por isso o runner mantém BATCH_POOL_SIZE runs em voo e
# o coalescer (infra/batch.py) junta as chamadas concorrentes em um único job.
BATCH_POOL_SIZE = 8  # runs simultâneos em voo (origem das chamadas a agrupar)
BATCH_MAX_SIZE = 32  # máx. de requisições por job de batch
BATCH_WINDOW_SEC = 45.0  # janela de espera antes de submeter um job parcial
BATCH_POLL_INTERVAL_SEC = 15.0  # intervalo entre polls do job
BATCH_MAX_WAIT_SEC = 24 * 3600  # SLA de turnaround dos três provedores

# Containers simultâneos. AD-04 fixa limites POR container (CPU/memória); este
# teto limita quantos existem ao mesmo tempo, para o paralelismo de runs não
# saturar a máquina e contaminar a medição de tempo de execução.
# 5 e não 4 desde 2026-08-16: com a mutação funcionando (AD-21), o mutmut passou
# a dominar o cronograma (366 s contra 3,1 s de pytest num projeto de LOC
# mediano). A máquina tem 12 CPUs e cada container usa 2 (DOCKER_CPU_LIMIT),
# então 5 containers deixam 2 CPUs para o host. Operacional, não experimental:
# não altera o que o LLM recebe nem produz.
DOCKER_MAX_CONCURRENT = 5

# ── Modelos de DESENVOLVIMENTO (gratuitos, FORA do experimento) ───────────
# Servem apenas para testar o pipeline sem gastar orçamento. O runner planeja
# somente sobre MODELS; estes nunca entram nas células experimentais e têm
# preço 0 (o cost_tracker registra tokens, mas o custo é sempre $0).
#
# Uso:
#   python scripts/smoke_run.py --arch A1 --project <path> --model dev-free --yes
#
# "dev-free" aponta para qualquer endpoint compatível com a API da OpenAI
# (Ollama local, LM Studio, OpenRouter, Groq…), configurável por env:
#   DEV_LLM_BASE_URL  (padrão: Ollama local)
#   DEV_LLM_MODEL     (nome do modelo no endpoint)
#   DEV_LLM_API_KEY   (padrão: "ollama"; OpenRouter/Groq exigem chave real)
# "dev-gemini-free" usa o free tier do Google AI Studio (só GOOGLE_API_KEY).
DEV_MODELS = {
    "dev-free": {
        "provider": "openai",
        "tier": "cost",
        "price_in": 0.0,
        "price_out": 0.0,
        "api_model": os.environ.get("DEV_LLM_MODEL", "qwen2.5-coder:7b"),
        "base_url": os.environ.get("DEV_LLM_BASE_URL", "http://localhost:11434/v1"),
        "api_key": os.environ.get("DEV_LLM_API_KEY", "ollama"),
    },
    "dev-gemini-free": {
        "provider": "google",
        "tier": "cost",
        "price_in": 0.0,
        "price_out": 0.0,
        "api_model": "gemini-2.5-flash",
    },
}

# Tabela completa para roteamento/precificação (runner continua usando MODELS).
ALL_MODELS = {**MODELS, **DEV_MODELS}

# ── Experimento ───────────────────────────────────────────────────────────
ARCHITECTURES = ["A1", "A2", "A3", "A4"]
N_REPETITIONS = 5
# > 0 intencionalmente; variância capturada pelas repetições (AD-18).
# 1.0 e não 0.7: a família GPT-5 aceita SOMENTE o default 1 na Chat Completions
# (400 unsupported_value com 0.7). Manter 0.7 nos demais faria a temperatura
# covariar com o provedor — confundidor que a regra 3 existe para impedir.
TEMPERATURE = 1.0

# ── Docker (AD-04) ────────────────────────────────────────────────────────
DOCKER_CPU_LIMIT = "2"
DOCKER_MEMORY_LIMIT = "2g"
DOCKER_NETWORK = "none"

# ── Retry (AD-11) ─────────────────────────────────────────────────────────
RETRY_MAX_ATTEMPTS = {
    "rate_limit": 6,  # 429
    "overload": 4,  # 503, 529
    "network_timeout": 3,
}
# 400/401/422: sem retry — falha imediata (bug de request)

# ── Segurança (AD-05) ─────────────────────────────────────────────────────
AST_FORBIDDEN = [
    "os.system",
    "subprocess",
    "shutil.rmtree",
    "socket",
    "eval",
    "exec",
]

# ── Status de run (AD-12) ─────────────────────────────────────────────────
FINAL_STATUSES = {"completed", "failed", "aborted_budget", "timeout"}
# "crashed" NÃO é final: runs crashed são reexecutados na retomada (AD-10)
