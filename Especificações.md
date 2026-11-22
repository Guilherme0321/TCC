# ADR — Decisões Arquiteturais: Geração de Testes com LLMs

**Projeto:** Estudo Empírico Comparativo de Arquiteturas LLM para Geração Automatizada de Testes Unitários em Python
**Autor:** Guilherme — PUC Minas
**Orientador:** Prof. Wladmir Cardoso Brandão
**Versão do documento:** 2.2
**Data:** 2026-07-17
**Status geral:** Todas as decisões aceitas. AD-16 adicionada com a composição final do benchmark (22 projetos, estratificação por tercis de CC média).

---

## Como versionar este documento

Cada decisão recebe um identificador estável (`AD-NN`). Mudanças de decisão **não apagam** o registro anterior: marque o status como `Substituída por AD-NN` e adicione a nova decisão ao final. O histórico de mudanças fica na tabela ao final do documento. Recomenda-se manter este arquivo sob controle de versão no mesmo repositório do pipeline (`pipeline/docs/ADR_arquiteturas_geracao_testes.md`).

**Status possíveis:** `Aceita` · `Proposta` · `Pendente (orientador)` · `Substituída por AD-NN` · `Rejeitada`

---

## Sumário das decisões

| ID | Decisão | Status |
|----|---------|--------|
| AD-01 | Motor de orquestração: Python puro, sem frameworks de agentes | Aceita |
| AD-02 | Contratos de interface via Pydantic | Aceita |
| AD-03 | Memória do loop A4: resumo filtrado, janela de 2 iterações | Aceita |
| AD-04 | Sandboxing: Docker efêmero por run | Aceita |
| AD-05 | Proteção contra código malicioso: análise estática de AST + restrições de runtime | Aceita |
| AD-06 | Timeouts de execução (pytest e mutmut) | Aceita |
| AD-07 | Limites de iteração: N=3 repair, N=2 reflexion | Aceita |
| AD-08 | Fallback de falha: salvar como falha, nunca descartar | Aceita |
| AD-09 | Circuit breaker financeiro por run e global | Aceita |
| AD-10 | Chave de idempotência | Aceita |
| AD-11 | Política de retry/backoff da API | Aceita |
| AD-12 | Gestão de falhas parciais e crashes | Aceita |
| AD-13 | Versionamento de prompts em arquivos externos | Aceita |
| AD-14 | Delimitação de escopo CriticAgent vs RepairAgent | Aceita |
| AD-15 | Estratégia de contenção de custo: Batch API + prompt caching (Opção A) | Aceita |
| AD-16 | Composição final do benchmark: 22 projetos, tercis de CC média | Aceita |

---

## 1. Orquestração e framework de agentes

### AD-01 — Motor de orquestração

**Status:** Aceita

**Contexto.** As quatro arquiteturas precisam de um mecanismo de transição de estado entre agentes, sobretudo A4, que possui dois loops (repair e Reflexion).

**Decisão.** Orquestração manual em Python puro. Cada pipeline é uma função que compõe agentes de forma explícita. O loop Reflexion de A4 é um `while iteration < N and not converged:`. O estado é um objeto explícito passado entre os steps, sem camada de abstração externa.

**Justificativa.** Frameworks como LangGraph ou CrewAI introduzem: (a) risco de versionamento, em que uma atualização altera o comportamento entre execuções; (b) caixas-pretas no controle de fluxo que comprometem a reprodutibilidade exigida pela banca; (c) abstrações de estado e memória fora do controle do pesquisador. Para um estudo empírico, controle total do fluxo supera a conveniência.

**Consequências.** Mais código de orquestração escrito à mão, porém transparente e auditável. Reprodutibilidade preservada como variável controlada.

---

### AD-02 — Contratos de interface (schemas)

**Status:** Aceita

**Contexto.** Os agentes trocam dados estruturados entre si, e a saída do LLM precisa ser validada antes de propagar.

**Decisão.** Definir um schema Pydantic por fronteira de agente:

- `CodeAnalysis` (saída do Analyzer): funções e classes com nome, assinatura, complexidade ciclomática individual via radon, dependências externas a mockar, casos de borda prováveis.
- `GeneratedTests` (saída do Generator): código de teste e lista de funções-alvo cobertas.
- `ExecutionResult` (saída do executor): pass/fail por teste, cobertura line e branch, mutation score, stdout, stderr, tempo.
- `CriticFeedback` (saída do Critic): linhas não cobertas, mutantes sobreviventes, sugestões de casos de teste.

**Justificativa.** Pydantic valida a saída do LLM, rejeitando JSON malformado antes que ele contamine o pipeline, e serializa diretamente para o JSONL. Usa-se `model_validate_json()` na saída do LLM; uma `ValidationError` é tratada como falha de iteração, não como crash do processo.

**Consequências.** Baixo custo de implementação. Garante integridade dos dados no JSONL.

---

### AD-03 — Memória e contexto do loop A4

**Status:** Aceita

**Contexto.** No loop A4 (`CriticAgent` realimentando o `TestGenerator`), é preciso decidir o que do histórico de falhas é repassado a cada iteração.

**Decisão.** Resumo filtrado, não log bruto. O estado mantém, por iteração anterior, apenas: linhas não cobertas, mutantes sobreviventes, número de testes falhando e mensagens de erro deduplicadas. A janela é limitada às últimas 2 iterações, sem acumular todo o histórico.

**Justificativa.** Repassar o traceback bruto do pytest a cada iteração consome tokens em excesso e degrada o sinal. O padrão Reflexion (Shinn et al., 2023) depende do delta mais recente, não de memória longa. A janela é uma variável de design reportável nas ameaças à validade.

**Consequências.** Menor custo por iteração. A janela fixa precisa ser registrada como hiperparâmetro do experimento.

---

## 2. Execução de código e segurança

### AD-04 — Isolamento (sandboxing)

**Status:** Aceita

**Contexto.** O `test_executor` roda código de teste gerado por LLM, sobre o qual não há controle prévio de conteúdo. Há um trade-off entre segurança/reprodutibilidade e esforço de engenharia em escala (~2.040 runs).

**Decisão.** Docker efêmero por run. Cada projeto do benchmark roda em um container descartável, criado com a venv e as dependências do projeto pré-instaladas via imagem base. O container é destruído ao final do run.

Configuração de segurança do container:
- Filesystem read-only, exceto um diretório temporário de trabalho montado como volume.
- Sem acesso à rede (`--network none`) durante a execução dos testes.
- Limites de CPU e memória definidos por container (ex.: `--cpus`, `--memory`).
- Usuário sem privilégios dentro do container.

**Justificativa.** Garante reprodutibilidade (ambiente idêntico a cada execução, eliminando variância de ambiente como confundidor) e isolamento forte contra código gerado por LLM. O overhead de build e teardown é aceitável dado o volume e mitigável com uma imagem base por projeto, reaproveitada entre as repetições.

**Consequências.** Maior complexidade de orquestração: o `test_executor` gerencia ciclo de vida de containers. Recomenda-se construir uma imagem base por projeto uma vez e reutilizá-la nos 5 repetições e nas 4 arquiteturas, evitando rebuild a cada run. A indisponibilidade de rede exige que todas as dependências estejam pré-instaladas na imagem.

---

### AD-05 — Proteção contra código malicioso

**Status:** Aceita

**Contexto.** O LLM pode gerar comandos destrutivos (`os.system("rm -rf /")`) ou loops infinitos, independentemente do nível de sandboxing.

**Decisão.** Três camadas de proteção:

1. Análise estática do AST do teste gerado antes da execução, rejeitando imports de `os.system`, `subprocess`, `shutil.rmtree`, `socket` e escrita fora do diretório temporário.
2. Execução sob usuário sem privilégios, em diretório de trabalho descartável.
3. Se Docker (AD-04 Opção A): filesystem read-only exceto o tempdir, sem acesso à rede, limites de CPU e memória.

**Justificativa.** A análise estática é barata e captura a maioria dos casos antes da execução. O número de testes rejeitados por análise estática é, em si, um dado de qualidade do output dos modelos e deve ser registrado.

**Consequências.** Necessário registrar a contagem de rejeições por análise estática como métrica auxiliar.

---

### AD-06 — Timeouts de execução

**Status:** Aceita

**Contexto.** Testes com loop infinito e a lentidão do mutation testing exigem limites de tempo distintos.

**Decisão.** Dois timeouts independentes, definidos como constantes únicas em `config.py`:

- **`PYTEST_TIMEOUT_SEC = 90`** por suite de testes gerada. Um teste unitário que excede esse limite provavelmente contém loop infinito. Valor central da faixa de 60 a 120 segundos.
- **`MUTMUT_TIMEOUT_SEC = 1800`** (30 minutos) como teto absoluto por projeto, combinado com o `--test-time-multiplier` do próprio mutmut para escalar proporcionalmente ao tempo da suite-base. Mutation testing é intrinsecamente lento e exige um teto mais generoso.

Timeout estourado registra o run com status `timeout`; não descarta a execução.

**Justificativa.** Mutation testing é intrinsecamente lento; um único teto para ambas as ferramentas penalizaria injustamente a etapa de mutação. Os valores são idênticos entre todos os modelos e arquiteturas, para não introduzir confundidores.

**Consequências.** Os valores são fixos no experimento. Caso algum projeto da banda de alta complexidade ciclomática estoure sistematicamente o teto de mutmut, o evento é registrado como `timeout` e tratado na análise, não ajustado por projeto (o que comprometeria a comparabilidade).

---

## 3. Critérios de parada e limites

### AD-07 — Limites de iteração (N)

**Status:** Aceita

**Contexto.** Os loops de A2 (repair) e A4 (repair e Reflexion) precisam de um limite de iterações.

**Decisão.** Duas constantes em `config.py`:

- **`N_REPAIR = 3`** — limite do repair loop em A2 e A4.
- **`N_REFLEXION = 2`** — limite do Reflexion loop em A4 (ciclo Critic para Generator).

**Justificativa.** A literatura de Reflexion (Shinn et al., 2023) indica retornos decrescentes acentuados após 2 a 3 iterações. Valores baixos também controlam custo de API, conforme refletido na estimativa de AD-09.

**Restrição crítica.** Ambos os valores são constantes únicas, idênticas entre todos os modelos e projetos. Variar N entre modelos confundiria efeito de arquitetura com efeito de hiperparâmetro, invalidando a comparação. Esta restrição deve ser registrada nas ameaças à validade.

**Consequências.** Os limites entram diretamente no modelo de custo de AD-09. Runs que atingem o limite sem convergir são tratados por AD-08.

---

### AD-08 — Fallback de falha de reparo

**Status:** Aceita

**Contexto.** Define-se o que ocorre quando o pipeline atinge o limite de iterações e o teste continua falhando.

**Decisão.** Salvar como falha, nunca descartar. O `ExecutionResult` registra o melhor estado alcançado: última versão dos testes, cobertura e mutation finais (possivelmente parciais ou zero), flag `converged: false` e `failure_reason`.

**Justificativa.** Descartar runs falhos enviesa os resultados ao esconder os casos em que a arquitetura não obteve sucesso. Um run que falhou ainda consumiu tokens, e esse custo entra no cálculo de CCP e CPKM. É justamente o tipo de dado que diferencia as arquiteturas.

**Consequências.** O schema de resultado precisa acomodar runs incompletos com métricas parciais.

---

### AD-09 — Circuit breaker financeiro

**Status:** Aceita

**Contexto.** Um modelo em loop de alucinação pode consumir tokens em excesso e gerar custo descontrolado. Os tetos precisam de base quantitativa, derivada da reestimativa de custo (ver seção "Reestimativa de custo" ao final).

**Decisão.** O `cost_tracker` acumula custo durante o run. Ao ultrapassar o teto do tier (`MAX_COST_PER_RUN_USD`), o pipeline aborta a iteração atual, marca `aborted_budget: true` e salva o estado parcial (AD-12). Há também um teto global que pausa o `ExperimentRunner`.

Tetos definidos com folga sobre o custo esperado por run da arquitetura A4 (pior caso de arquitetura), para que o breaker só dispare em comportamento anômalo, não em runs normais:

- **`MAX_COST_PER_RUN_USD["frontier"] = 2.00`** — o A4 esperado mais caro do tier (GPT-5.5) custa ~$0.61/run; o teto dá margem de ~3x para casos longos legítimos antes de classificar como anomalia.
- **`MAX_COST_PER_RUN_USD["cost"] = 1.00`** — o A4 esperado mais caro do tier (Claude Sonnet 4.6) custa ~$0.33/run; margem de ~3x.
- **`MAX_COST_GLOBAL_USD = 600.00`** — teto global do experimento, situado entre o cenário esperado (~$315) e o pessimista (~$471), com folga. Ao ser atingido, o `ExperimentRunner` pausa e exige intervenção manual.

**Justificativa.** Tetos por tier respeitam a diferença grande de preço entre frontier e cost-effective. Margem de ~3x sobre o esperado evita falsos positivos em runs longos legítimos, mas ainda corta loops de alucinação cedo. O teto global é a proteção contra a fatura-surpresa agregada.

**Consequências.** Os valores devem ser revisados se os preços de API mudarem ou se o número de projetos sair da faixa de 15 a 20. Runs com `aborted_budget` entram na análise como falhas (AD-08), com seu custo parcial contabilizado em CCP e CPKM.

**Ajuste sob AD-15 (Batch API).** Com a Batch API ativa (AD-15), o custo por run cai cerca de metade, e o A4 esperado mais caro do tier frontier passa a ~$0,30/run (cost-effective ~$0,16/run). Os tetos por run permanecem em $2,00 (frontier) e $1,00 (cost), o que com batch representa margem ainda maior sobre o esperado (~6x), preservando a função de detectar apenas anomalias. O teto global, contudo, é reduzido para **`MAX_COST_GLOBAL_USD = 250.00`** sob AD-15, situado acima do esperado com batch+caching (~$122) e do esperado só com batch (~$157), com folga, e bem abaixo do standard. Caso a Batch API seja desativada para alguma execução, restaurar o teto global de $600.

---

## 4. Engenharia de dados e idempotência

### AD-10 — Chave de idempotência

**Status:** Aceita

**Contexto.** Cada execução precisa de um identificador unívoco no JSONL para permitir retomada.

**Decisão.** A chave é composta por `arquitetura + modelo + projeto + repeticao`, nessa ordem (ex.: `A4__claude-opus-4-7__projeto-x__rep-3`). Antes de cada run, o `ExperimentRunner` lê o JSONL e monta um conjunto de chaves já concluídas, pulando as existentes.

**Justificativa.** Esses quatro campos identificam univocamente um ponto do produto cartesiano do experimento.

**Consequências.** Só contam como concluídas as execuções finalizadas (ver AD-12); registros parciais ou de crash não bloqueiam a reexecução.

---

### AD-11 — Tratamento de exceções de API

**Status:** Aceita

**Contexto.** Erros transitórios de LLM (429, 503, timeouts) são frequentes em volume alto de chamadas.

**Decisão.** Backoff exponencial com jitter no `llm_client`:

- **429 (rate limit):** backoff exponencial respeitando o header `retry-after` quando presente; 5 a 6 tentativas.
- **503 / 529 (overload):** backoff exponencial; cerca de 4 tentativas.
- **Timeout de rede:** retry com backoff; cerca de 3 tentativas.
- **400 / 401 / 422 (erro de request):** sem retry — falha alto, pois indica bug.

Cada retry não conta como iteração de pipeline, e o custo de tokens em falha de request é zero. Todo retry é logado para auditoria.

**Justificativa.** Distingue erro transitório (retentável) de erro de programação (não retentável), evitando mascarar bugs.

**Consequências.** Pode-se usar a biblioteca `tenacity` ou implementação manual equivalente.

---

### AD-12 — Gestão de falhas parciais

**Status:** Aceita

**Contexto.** Uma execução pode falhar no meio por erro de rede ou crash de infraestrutura. É preciso decidir se o estado parcial é registrado ou revertido.

**Decisão.** Registro de estado parcial com flag de completude. O padrão:

1. O run é escrito no JSONL uma única vez, ao final, com campo `status: completed | failed | aborted_budget | crashed`.
2. Em caso de crash não tratado, um bloco `try/finally` ou `atexit` grava um registro com `status: crashed` e o acumulado até o ponto (tokens, custo parcial).
3. Na retomada, registros `crashed` são reexecutados; a chave de idempotência só considera definitivos os status `completed`, `failed` e `aborted_budget`.

**Justificativa.** Crash de infraestrutura não deve ser tratado como falha de arquitetura. A política preserva o dado de custo já consumido sem confundir as duas situações.

**Consequências.** O campo `status` torna-se central na análise; runs `crashed` precisam ser refeitos antes do fechamento dos dados.

---

## 5. Engenharia de prompts e versionamento

### AD-13 — Gerenciamento de system prompts

**Status:** Aceita

**Contexto.** Prompts hardcoded nas classes dificultam testes A/B e rastreabilidade entre execuções.

**Decisão.** Prompts em arquivos externos versionados, nunca hardcoded:

```
prompts/
├── analyzer.v1.txt
├── generator.v1.txt
├── repair.v1.txt
└── critic.v1.txt
```

Contexto dinâmico é injetado via template (Jinja2 ou f-string com placeholders). O hash ou a versão do prompt usado em cada run é registrado no JSONL.

**Justificativa.** Registrar a versão do prompt por run mantém os dados rastreáveis e impede a mistura de prompts diferentes na mesma análise — uma ameaça à validade que a banca pode levantar.

**Consequências.** Mudar um prompt no meio do experimento exige bump de versão do arquivo e fica explícito nos dados.

---

### AD-14 — Delimitação de escopo: CriticAgent vs RepairAgent

**Status:** Aceita

**Contexto.** A4 contém os dois agentes; é a distinção conceitual mais importante da arquitetura e precisa evitar sobreposição de responsabilidades.

**Decisão.** Separação pelo tipo de falha tratada:

- **RepairAgent — erros de execução.** Atua quando o teste não roda: erro de sintaxe, import incorreto, exceção no setup, assertion malformada. Input: traceback. Output: testes corrigidos que executam. Não observa cobertura nem mutantes.
- **CriticAgent — insuficiência de qualidade.** Atua quando os testes rodam e passam, mas a cobertura é baixa ou mutantes sobrevivem. Input: métricas de `pytest-cov` e `mutmut`. Output: feedback estruturado em linguagem natural, nunca código.

**Regra de não-sobreposição.** O Critic descreve o gap; o Generator escreve o código. O Critic jamais emite Python. Isso mantém o Generator como único autor de testes e o Critic como avaliador puro, fiel ao padrão Reflexion, em que a auto-reflexão é texto que realimenta o ator.

**Fluxo A4 explícito.**

```
Generator → executor → [erro de execução?]    → Repair → executor      (loop até rodar)
                     → [roda mas qualidade baixa?] → Critic (feedback) → Generator → executor   (loop Reflexion)
```

**Consequências.** Garante separação de responsabilidades clara e defensável na banca.

---

## 6. Contenção de custo

### AD-15 — Estratégia de contenção de custo

**Status:** Aceita

**Contexto.** A reestimativa de custo (seção seguinte) projetou ~$315 em preço standard para o experimento completo. O objetivo é manter o orçamento próximo de $100 sem reduzir a qualidade dos testes gerados nem o escopo científico (6 modelos, 5 repetições, 17 projetos).

**Decisão.** Adotar a Opção A: aplicar Batch API e prompt caching, mantendo o escopo experimental integral.

1. **Batch API (−50%).** Todas as chamadas de LLM são submetidas via Batch API dos três provedores (Anthropic, OpenAI, Google), que oferecem 50% de desconto em input e output para processamento assíncrono com turnaround de até 24h. O experimento é assíncrono por natureza e não sensível a latência, então o trade-off é nulo. Mesmos prompts, mesmos modelos, mesma temperatura, mesmo output.
2. **Prompt caching do código-fonte (−90% no input cacheado).** O código-fonte de cada projeto é idêntico entre as 4 arquiteturas, as 5 repetições e as iterações de loop. Estruturar o prompt de modo que o bloco de código-fonte fique no prefixo cacheável e estável; a partir da segunda chamada, esse trecho de input custa 10% do preço base.

**Resultado projetado.** Total de ~$122 (frontier ~$88, cost-effective ~$34), contra ~$315 standard. Nenhuma alteração no que o LLM produz.

**Disciplina de execução.** Rodar primeiro o tier cost-effective (~$34 com batch e caching) e reavaliar o orçamento com base no consumo real de tokens medido, antes de liberar o tier frontier. Se a projeção real do frontier indicar estouro do alvo, o corte de menor impacto científico é reduzir as repetições de 5 para 3 (Wilcoxon e Kruskal-Wallis derivam poder do número de projetos, n=17 pares, não do número de repetições intra-célula), o que levaria o total a ~$73. Esse corte é uma contingência, não a decisão atual.

**Rejeição explícita de tiers gratuitos.** Descartam-se roteadores e cotas gratuitas (OpenRouter free tier, Hugging Face Inference API) como via de redução de custo. Motivos, todos ameaças diretas à validade do estudo: (a) os modelos frontier fechados (Opus 4.8, GPT-5.5, Gemini 3.1 Pro) não estão disponíveis gratuitamente nesses serviços; (b) tiers gratuitos frequentemente não fixam a versão/build do modelo, quebrando reprodutibilidade; (c) muitos usam os prompts enviados para treino, criando risco de contaminação. A via legítima de redução adicional, se necessária, são créditos acadêmicos ou de pesquisa dos próprios provedores, a verificar com o orientador e a instituição.

**Justificativa.** Batch e caching atacam o custo sem tocar na qualidade nem no escopo, que são os eixos científicos do trabalho. Mantêm versão de modelo fixada, ausência de contaminação e fatura previsível, paga diretamente nas APIs oficiais.

**Consequências.** A economia real do caching depende da estruturação do prompt (bloco de código no prefixo estável) e do TTL do cache (5 min ou 1h) não expirar entre chamadas. A premissa de 70% de input cacheado é razoável, porém otimista; se na prática ficar em 40 a 50%, o total da Opção A sobe. O piloto de agosto fornecerá o número real de aproveitamento de cache e permitirá recalibrar a projeção. Os tetos do circuit breaker (AD-09) foram ajustados para refletir o custo por run com batch.

**Nota de validação (2026-07-17).** Smoke test gratuito (llama 3.2 via Docker Model Runner, A1 e A4, projeto `book-to-skill`, execução em Docker) mediu 85% de cache hit no prefixo estável — acima da premissa de 70% acima. O resultado valida a mecânica de caching e a estrutura de prompt (AD-13), mas não substitui o piloto pago: a taxa real por provedor depende do comportamento de cada API sob Batch (itens processados em paralelo podem não compartilhar cache) e não foi medida com tráfego real. O modelo de custo do `cost_tracker` também simplifica o preço de escrita de cache (cobra o preço base, não o multiplicador de ~1,25x que a Anthropic aplica); efeito pequeno, mas soma-se ao viés otimista da premissa de 70%. Nenhuma mudança de decisão neste ponto — apenas registro do que o piloto pago (tier cost-effective) precisa recalibrar.

---

Base quantitativa para AD-09. Todos os valores em USD, preços de API standard tier (junho de 2026).

### Preços por milhão de tokens

| Modelo | Tier | Input | Output |
|--------|------|-------|--------|
| Claude Opus 4.8 | frontier | 5,00 | 25,00 |
| Gemini 3.1 Pro | frontier | 2,00 | 12,00 |
| GPT-5.5 | frontier | 5,00 | 30,00 |
| Claude Sonnet 4.6 | cost-effective | 3,00 | 15,00 |
| Gemini 3.5 Flash | cost-effective | 1,50 | 9,00 |
| GPT-5 mini | cost-effective | 0,25 | 2,00 |

> Nota: a metodologia original previa GPT-4o mini no tier cost-effective. O substituto atual de linha equivalente da OpenAI foi adotado na estimativa (GPT-5 mini, ~$0,25/$2,00). Confirmar o ID exato do modelo cost-effective da OpenAI no momento da execução, pois afeta marginalmente o total.

### Premissas de consumo de tokens

Tokens por tipo de chamada de agente (médias conservadoras):

| Chamada | Input | Output |
|---------|-------|--------|
| Generator | 8.000 | 3.000 |
| Analyzer | 8.000 | 1.500 |
| Repair | 6.000 | 2.500 |
| Critic | 7.000 | 1.500 |

Premissas de disparo de loop no cenário esperado: o repair dispara em metade dos runs, com média de 1,5 iterações quando dispara (de um máximo de 3); o Reflexion roda em média 1,5 das 2 iterações possíveis, cada uma somando uma chamada de Critic e uma re-geração.

### Estrutura do experimento

17 projetos (ponto médio de 15 a 20) × 5 repetições = 85 runs por combinação de arquitetura e modelo. Total: 4 arquiteturas × 6 modelos × 85 = 2.040 runs.

> Substituído por AD-16: a composição final do benchmark é de 22 projetos (não 17), elevando o total para 2.640 runs. Os valores desta subseção são preservados como registro da estimativa original; ver AD-16 para os números vigentes.

### Custo por modelo e arquitetura (cenário esperado, base 17 projetos)

| Modelo | A1 | A2 | A3 | A4 | Subtotal |
|--------|------|------|------|------|---------|
| Claude Opus 4.8 | 9,77 | 15,67 | 16,36 | 46,17 | 87,97 |
| Gemini 3.1 Pro | 4,42 | 7,10 | 7,31 | 20,70 | 39,52 |
| GPT-5.5 | 11,05 | 17,74 | 18,27 | 51,74 | 98,81 |
| Claude Sonnet 4.6 | 5,87 | 9,40 | 9,82 | 27,70 | 52,79 |
| Gemini 3.5 Flash | 3,31 | 5,32 | 5,48 | 15,52 | 29,64 |
| GPT-5 mini | 0,68 | 1,09 | 1,11 | 3,15 | 6,02 |

Subtotal frontier: **$226,31**. Subtotal cost-effective: **$88,45**.

### Cenários de custo total (base 17 projetos)

| Cenário | Custo | Premissa |
|---------|-------|----------|
| Otimista | ~$157 | Batch API com 50% de desconto, loops mínimos |
| Esperado | **~$315** | Cálculo acima, preço standard |
| Pessimista | ~$471 | Loops sempre no limite máximo (repair 3 iter em 100% dos runs, Reflexion sempre 2 iter) |

### Recomendações de controle de custo

A disciplina de tiers da metodologia continua válida: rodar primeiro o tier cost-effective, reavaliar orçamento, e só então liberar o tier frontier. Considerar a Batch API (50% de desconto) para os runs, dado que o experimento é assíncrono e não sensível a latência. O custo por run alimenta diretamente os tetos de AD-09. **Os valores absolutos de custo desta seção são recalculados em AD-16 para o benchmark final de 22 projetos; usar os números de AD-16 para decisões de orçamento.**

---

## 7. Benchmark

### AD-16 — Composição final do benchmark e estratificação por tercis de CC

**Status:** Aceita

**Contexto.** O `benchmark_selector` triou 297 candidatos e aprovou 22. As três variantes de limiar fixo de banda de complexidade ciclomática produziram estratificações degeneradas sobre o pool aprovado: CC média → 16 baixa / 5 média / 1 alta; CC p90 → 1 / 14 / 7; CC máxima → 0 / 0 / 22. Nenhuma sustenta a comparação estratificada em 3 faixas prevista na Metodologia (Seção 4.4).

**Decisão.** Adotar os 22 projetos aprovados, com estratificação por **tercis de CC média calculados dentro do próprio pool aprovado**:

- **baixa** (CC média ≤ 3,11): 7 projetos;
- **média** (3,12 a 4,31): 7 projetos;
- **alta** (≥ 4,46): 8 projetos.

Cada projeto é fixado no commit avaliado pelo selector (hash no `output/relatorio_benchmark.json`; cópia consolidada em `docs/benchmark_composition.md`). O produto cartesiano passa de 2.040 para **2.640 runs** (4 arquiteturas × 6 modelos × 22 projetos × 5 repetições).

**Justificativa.** Tercis produzem bandas equilibradas por construção, preservando a análise estratificada. O poder dos testes de Wilcoxon e Kruskal-Wallis deriva do número de projetos (n=22 > 17 do ponto médio original), então a ampliação fortalece a análise. Limiares relativos ao pool são objetivos e reprodutíveis.

**Consequências.** (a) Os limiares de banda são relativos ao pool, não absolutos — registrar nas ameaças à validade. (b) 13 dos 22 projetos foram criados antes do cutoff de contaminação (2023-10-01) e carregam flag de risco de contaminação de dados de treino; permanecem no benchmark e o risco é reportado como ameaça à validade, com a flag disponível como covariável na análise. (c) A projeção de custo escala em 22/17: total esperado ~$158 com Batch API + caching (frontier ~$114, cost-effective ~$44), ainda com folga sob o teto global de $250 (AD-09 inalterada). (d) `StevenBtw/solvOR` não tem medição de cobertura baseline no relatório; medir durante a preparação das imagens ou registrar a lacuna.

---

## Histórico de versões

| Versão | Data | Autor | Mudanças |
|--------|------|-------|----------|
| 1.0 | 2026-06-22 | Guilherme | Versão inicial. AD-01 a AD-14 registradas. Três itens pendentes de validação com orientador. |
| 2.0 | 2026-06-23 | Guilherme | AD-04 fechada (Docker efêmero por run). AD-07 fechada (N_REPAIR=3, N_REFLEXION=2). AD-09 fechada com tetos por tier (frontier $2,00; cost $1,00) e global ($600,00), derivados de reestimativa de custo. AD-03 e AD-06 promovidas a Aceita, com valores concretos de timeout. Adicionada seção de reestimativa de custo (esperado ~$315 standard, ~$157 com Batch API). |
| 2.1 | 2026-06-23 | Guilherme | AD-15 adicionada: estratégia de contenção de custo via Batch API + prompt caching (Opção A), com projeção de ~$122 mantendo escopo completo, contingência de redução de repetições e rejeição explícita de tiers gratuitos por ameaça à reprodutibilidade. Teto global de AD-09 reduzido para $250 sob AD-15. |
| 2.2 | 2026-07-17 | Guilherme | AD-16 adicionada: composição final do benchmark com os 22 projetos aprovados pelo selector, estratificados por tercis de CC média do pool (7/7/8), commits pinados. Produto cartesiano passa a 2.640 runs; projeção com batch+caching ~$158, sob o teto de $250. Registrada nota de validação em AD-15 sobre o smoke test gratuito (85% de cache hit, acima da premissa de 70%) e a simplificação do `cost_tracker` quanto ao preço de escrita de cache. |
