# Arquiteturas de agentes LLM para geração automática de testes em Python

Código do Trabalho de Conclusão de Curso **"Estudo Empírico Comparativo de
Arquiteturas Baseadas em Modelos de Linguagem para Geração Automática de Testes
em Projetos Python"**, do Bacharelado em Ciência da Computação da PUC Minas.

- **Autor:** Guilherme Otávio de Oliveira
- **Orientador:** Prof. Wladmir Cardoso Brandão

O estudo compara quatro arquiteturas em ablação progressiva:

| ID | Arquitetura |
|---|---|
| A1 | agente único |
| A2 | agente único com laço de reparo |
| A3 | multi-agente sem crítico |
| A4 | multi-agente com crítico |

A comparação usa dois modelos (`gpt-5-mini` e `claude-sonnet-5`) e mede:
- cobertura;
- *mutation score*;
- custo;
- duas métricas de custo-eficiência propostas no trabalho: CCP (custo por ponto
  de cobertura) e CPKM (custo por mutante morto).

## Estrutura

```
./
├── benchmark_selector/   Etapa 1: busca e triagem dos projetos do benchmark
│                         (297 candidatos → 22 aprovados)
├── pipeline_scaffold/    Etapa 2: experimento (agentes, arquiteturas, execução
│   │                     em contêiner, coleta e análise estatística)
│   ├── agents/           agentes (analisador, gerador, reparo, crítico)
│   ├── architectures/    composição das arquiteturas A1–A4
│   ├── infra/            cliente LLM, Batch API, custo, guarda de AST, executor
│   ├── prompts/          prompts de cada agente
│   ├── schemas/          contratos Pydantic entre agentes
│   ├── scripts/          build das imagens, checagem e análise (analyze.py)
│   ├── docker/           imagem base do executor
│   ├── docs/             decisões arquiteturais (ADR) e composição do benchmark
│   ├── tests/            testes automatizados
│   └── runner.py         orquestrador do experimento
└── Especificações.md     especificação técnica do pipeline
```

## Pré-requisitos

- Python 3.12+
- Docker (os testes gerados rodam em contêineres efêmeros, sem rede)
- Chaves de API da OpenAI e/ou da Anthropic (só para execuções reais)
- Token do GitHub (só para a busca de candidatos do `benchmark_selector`)

## Instalação

```bash
cd pipeline_scaffold
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # preencha as chaves no .env (nunca versione esse arquivo)
```

O `benchmark_selector` tem instalação e uso próprios, descritos em
[`benchmark_selector/README.md`](benchmark_selector/README.md).

## Como executar

1. **Projetos do benchmark.** Os 22 projetos aprovados, com repositório e
   *commit* fixado, estão em
   [`pipeline_scaffold/docs/benchmark_composition.md`](pipeline_scaffold/docs/benchmark_composition.md).
   Clone cada um em `pipeline_scaffold/benchmark/<nome>` no *commit* indicado.
2. **Imagens Docker:** `python scripts/build_images.py`, seguido de
   `python scripts/check_benchmark_targets.py` para validar cobertura e pacote
   de cada projeto.
3. **Experimento:** `python runner.py`. O padrão é simulação sem chamadas
   pagas; use `--execute` para chamadas reais. Há filtros por `--arch`,
   `--project`, `--model` e `--reps`.
4. **Análise:**
   `python scripts/analyze.py --jsonl output/results.jsonl --md relatorio.md --por-modelo por_modelo.md`
   e `python scripts/rq4_por_projeto.py` para a Tabela 10 (RQ4, uma observação
   por projeto, com correção de Benjamini-Hochberg).

**Testes:** `python -m pytest -q` em `pipeline_scaffold/`, e
`python -m pytest tests -q` em `benchmark_selector/`.

## Dados e o que não está versionado

Este repositório contém apenas código, *prompts*, testes e documentação.

| Não versionado | Motivo |
|---|---|
| Log de execuções da coleta (`output/`) | dados da coleta |
| Clones dos projetos (`benchmark/`, `work/`) | código de terceiros, reobtido pelos *commits* fixados |
| Arquivos `.env` | contêm chaves de API |

Os custos reportados no artigo são **custo modelado** (*tokens* × preço de
tabela), e não valores de fatura.

## Como citar

> OLIVEIRA, G. O.; BRANDÃO, W. C. *Estudo Empírico Comparativo de Arquiteturas
> Baseadas em Modelos de Linguagem para Geração Automática de Testes em Projetos
> Python*. Trabalho de Conclusão de Curso (Bacharelado em Ciência da Computação),
> Pontifícia Universidade Católica de Minas Gerais, Belo Horizonte, 2026.
> Código disponível em: <https://github.com/Guilherme0321/TCC>.

## Licença

[MIT](LICENSE).
