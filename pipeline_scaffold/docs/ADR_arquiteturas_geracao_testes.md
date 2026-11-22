# ADR — Decisões Arquiteturais: Geração de Testes com LLMs

**Projeto:** Estudo Empírico Comparativo de Arquiteturas LLM para Geração Automatizada de Testes Unitários em Python
**Autor:** Guilherme — PUC Minas
**Orientador:** Prof. Wladmir Cardoso Brandão
**Versão do documento:** 3.4
**Data:** 2026-08-24
**Status geral (v3.2):** **AD-21 destravada e a inferência de alvos corrigida — o CPKM tem observação em 21 dos 22 projetos** (`mutation_score = 0,372` no `codeindex`, pacote na raiz; 0,554 no `aioamazondevices`, layout `src/`), com `--read-only` intacto. `scripts/check_benchmark_targets.py` dá 21/22 PASS e é pré-requisito de disparo. **`Waza` foi excluído por não ter pacote importável: a coleta vigente é de 420 runs, não 440** — desvio de AD-16 a declarar como ameaça à validade. Falta o aceite formal de AD-21. Fica aberto o risco que a medição revelou: `MUTMUT_TIMEOUT_SEC = 1800` tende a enviesar o CPKM contra as suites de maior cobertura. AD-17 torna AD-15 efetiva (Batch API por coalescência entre runs); AD-18 unifica a temperatura em 1.0; AD-19 dimensiona os tetos de saída para modelos de raciocínio; **AD-20 impõe teto de R$ 200 e restringe a coleta a um único modelo**. O AD-08 foi clarificado em 2026-08-12 com o ratchet do melhor estado, resolvendo a distorção que fazia o A4 medir pior que o A1. Pendência aberta: endosso do orientador quanto à redução de escopo de AD-20.

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
| AD-08 | Fallback de falha: salvar como falha, nunca descartar; run é creditado pelo MELHOR estado e cobrado por todos (ratchet, clarificado em 2026-08-12) | Aceita |
| AD-09 | Circuit breaker financeiro por run e global | Aceita |
| AD-10 | Chave de idempotência | Aceita |
| AD-11 | Política de retry/backoff da API | Aceita |
| AD-21 | mutmut não roda sob o filesystem read-only de AD-04 | **PROPOSTA — opção (A) validada e implementada; falta aceite** |
| AD-12 | Gestão de falhas parciais e crashes | Aceita |
| AD-13 | Versionamento de prompts em arquivos externos | Aceita |
| AD-14 | Delimitação de escopo CriticAgent vs RepairAgent | Aceita |
| AD-15 | Estratégia de contenção de custo: Batch API + prompt caching (Opção A) | Aceita |
| AD-16 | Composição final do benchmark: 22 projetos, tercis de CC média | Aceita |
| AD-17 | Mecanismo de execução da Batch API: coalescência entre runs | Aceita |
| AD-18 | Temperatura única do experimento: 1.0 (era 0.7) | Aceita |
| AD-19 | Teto de tokens de saída por agente, dimensionado para modelos de raciocínio | Aceita |
| AD-20 | Teto de orçamento de R$ 200 e redução do escopo a um único modelo | Aceita (escopo pendente de orientador) |
| AD-22 | Timeout de mutação não invalida a convergência: reclassificação na análise | Aceita |
| AD-23 | Recorte analítico: A2×A4 quantitativo, A1/A3 qualitativo | Aceita |
| AD-24 | Quatro projetos com convergência zero: falha silenciosa de instalação e incompatibilidade com AD-05 | Proposta |
| AD-25 | Segunda rodada de coleta em um segundo modelo, restrita a A2 e A4 | Proposta |

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

**Clarificação de 2026-08-12 — ratchet do melhor estado.** A redação acima era ambígua: dizia "registra o melhor estado alcançado" e, na mesma frase, "última versão dos testes". Enquanto o pipeline implementou a segunda leitura, o resultado de um run era o seu ÚLTIMO estado. A medição de 2026-08-12 mostrou o custo disso: sobre `benchmark/codeindex`, o A1 (1 chamada, US$ 0,0140) cobriu 28,7% de linha contra 24,7% do A4 (13 chamadas, US$ 0,0954) — a arquitetura mais barata venceu a mais cara gastando 6,8x menos, porque os loops de A4 degradaram uma suite melhor do que a que entregaram.

**Resolve-se pela primeira leitura.** O resultado de um run passa a ser o MELHOR estado que ele alcançou; o CUSTO segue sendo o total de todas as chamadas, inclusive as das iterações descartadas. A arquitetura é **cobrada por tudo que gastou e creditada pelo melhor que entregou** — que é exatamente o que a justificativa original desta decisão pede ("um run que falhou ainda consumiu tokens, e esse custo entra no cálculo de CCP e CPKM").

Critério de comparação entre estados de um mesmo run, lexicográfico e declarado:

1. **convergiu** (a suite roda e passa, sem timeout) — dominante, e é a própria definição de sucesso desta decisão, não um critério novo;
2. cobertura de linha; 3. cobertura de branch; 4. mutation score — nessa ordem porque a cobertura é medida em todo run enquanto o mutation score é `None` sempre que o mutmut é desligado ou estoura o timeout de AD-06;
5. menos testes falhando — desempate entre estados que não convergiram.

Empate mantém o incumbente, preservando o primeiro estado alcançado entre equivalentes. Implementado em `architectures/common.py`: `quality_key`, `track_best` e `execute_and_track` — este último é o ponto ÚNICO de execução dos quatro pipelines, de modo que nenhum estado escapa do ratchet.

**Consequências da clarificação.**

(a) Dois campos novos no `RunRecord`: `states_evaluated` (quantas suites executáveis o run produziu) e `discarded_regression` (o estado registrado não é o último — ou seja, o ratchet reverteu). O segundo **é métrica do estudo**: mede com que frequência os loops de A2/A4 degradam o que já tinham alcançado, e é resultado publicável por si só.

(b) O ratchet é **inerte em A1 e A3**, que produzem um único estado, e quase inerte em A2, cujo repair loop já para assim que a suite passa — A2 só pode regredir entre estados que não passam. O efeito concentra-se em A4, que é justamente onde a distorção foi medida.

(c) **Ameaça à validade a declarar.** Tomar o máximo sobre `k` estados favorece arquiteturas com `k` maior (efeito "melhor de k"): A4 avalia até 13 estados, A1 apenas 1. A alternativa — manter o último estado — não é neutra tampouco, pois penaliza por construção quem itera. Nenhuma das duas leituras é imparcial; esta foi escolhida por ser a que a justificativa original do AD-08 sustenta, e o viés residual deve ser reportado junto de `states_evaluated`, que o quantifica por run.

(d) O ratchet **não fabrica sucesso**: se nenhum estado convergiu, o run continua `failed`. Ele escolhe o melhor entre os disponíveis, nunca inventa um.

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
- **400 / 401 / 403 / 422 (erro de request):** sem retry — falha alto, pois indica bug.

Cada retry não conta como iteração de pipeline, e o custo de tokens em falha de request é zero. Todo retry é logado para auditoria.

**Justificativa.** Distingue erro transitório (retentável) de erro de programação (não retentável), evitando mascarar bugs.

**Consequências.** Pode-se usar a biblioteca `tenacity` ou implementação manual equivalente.

**Correção de 2026-08-14 — 403 era retentável e divergia entre caminhos.**

O texto original listava apenas 400/401/422, e `_classify_status` seguia a lista à risca: 403 caía no ramo padrão e virava `NetworkTimeout`, com 3 tentativas e backoff. O caminho de batch, porém, classifica o item pelo CORPO do erro (`_classify_batch_error`), onde `permission_error` — que é exatamente um 403 — já era `FatalRequestError`.

Os dois classificadores discordavam sobre o mesmo erro: chave sem permissão falhava na hora por um caminho e rendia três tentativas pelo outro. 403 passou para o conjunto fatal (`_FATAL_STATUS`), alinhando os dois. Há teste pinando a convergência, não só o código isolado, porque a falha original não foi o valor errado e sim a existência de duas classificações independentes da mesma condição.

Em aberto, não decidido: **404** (modelo inexistente — o erro mais provável em operação, um typo em `--model`) e **413** (payload acima do limite) também são irrecuperáveis por retentativa, mas estender a lista além de 403 é decisão nova e não entrou aqui.

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

**Clarificação de 2026-08-16 — o JSONL é um log de TENTATIVAS, não uma tabela de células.** A reexecução de `crashed` (AD-10) anexa um novo registro sem apagar o antigo: durante a coleta observaram-se 240 registros para 224 `run_key` distintas. Exemplo real:

```
A2__gpt-5-mini__codeindex__rep-2   status=crashed    custo=0,012037
A2__gpt-5-mini__codeindex__rep-2   status=completed  custo=0,011072
```

A análise precisa deduplicar por `run_key` com **duas regras distintas**, e não com "fique com a última linha":

1. **Métricas** (cobertura, mutação, iterações, convergência) vêm **apenas** da tentativa com status definitivo.
2. **Custo** é a **soma de todas as tentativas** da mesma célula. O registro `crashed` do exemplo consumiu US$ 0,012 antes de morrer, e por AD-08 esse custo é real e entra no CCP/CPKM. Descartar a linha `crashed` inteira subestimaria o custo do experimento.

Contar registros sem deduplicar infla o *n* e quebra o pareamento do Wilcoxon.

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

**Nota de implementação (2026-08-10).** Até esta data a decisão estava registrada mas NÃO implementada: `batch=True` apenas aplicava o multiplicador de 0,5 no `cost_tracker`, enquanto as chamadas seguiam síncronas e a preço cheio. O efeito era um viés de 2x para baixo no custo registrado — exatamente o número que o piloto do tier cost-effective precisa medir para liberar o frontier. A Batch API passou a ser efetivamente usada nos três provedores; o mecanismo de execução está registrado em AD-17, incluindo suas consequências sobre AD-09 e sobre a premissa de cache desta decisão.

**Nota sobre contabilização de tokens de raciocínio (2026-08-10).** Ao instrumentar `reasoning_tokens`, constatou-se que os SDKs divergem no que reportam como output, com efeito direto sobre o custo registrado:

- **OpenAI:** `completion_tokens` já inclui os tokens de raciocínio; `completion_tokens_details.reasoning_tokens` é subconjunto. O custo estava correto.
- **Gemini:** `thoughts_token_count` é reportado FORA de `candidates_token_count`, mas é faturado como output. Como o `llm_client` usava apenas `candidates_token_count`, o custo dos modelos Gemini com raciocínio estava **subestimado**. Corrigido: `output_tokens = candidates + thoughts`.
- **Anthropic:** com extended thinking os tokens de raciocínio já entram em `output_tokens`, sem campo separado. Custo correto; a sub-contagem instrumental é indisponível neste provedor.

O invariante adotado é: `output_tokens` significa sempre output FATURADO, e `reasoning_tokens` é um subconjunto dele, exposto apenas para instrumentação — nunca somado ao custo, sob pena de dupla contagem. A métrica importa porque o teto de saída (`max_tokens` do request) é consumido também pelo raciocínio invisível: se a fração for alta nos projetos reais, o código de teste pode ser truncado antes de terminar.

**Nota de validação (2026-07-17).** Smoke test gratuito (llama 3.2 via Docker Model Runner, A1 e A4, projeto `book-to-skill`, execução em Docker) mediu 85% de cache hit no prefixo estável, acima da premissa de 70% acima. O resultado valida a mecânica de caching e a estrutura de prompt (AD-13), mas não substitui o piloto pago: a taxa real por provedor depende do comportamento de cada API sob Batch (itens processados em paralelo podem não compartilhar cache) e não foi medida com tráfego real. O modelo de custo do `cost_tracker` também simplifica o preço de escrita de cache (cobra o preço base, não o multiplicador de ~1,25x que a Anthropic aplica); efeito pequeno, mas soma-se ao viés otimista da premissa de 70%. Nenhuma mudança de decisão neste ponto, apenas registro do que o piloto pago (tier cost-effective) precisa recalibrar.

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

## 8. Execução da Batch API

### AD-17 — Mecanismo de execução da Batch API: coalescência entre runs

**Status:** Aceita

**Contexto.** AD-15 determina que todas as chamadas de LLM sejam submetidas via Batch API, com 50% de desconto. A decisão estava registrada mas não implementada (ver nota de implementação em AD-15).

A Batch API dos três provedores é assíncrona: submete-se um job, faz-se poll e recuperam-se os resultados, com SLA de turnaround de até 24h. Um job com uma única requisição obtém o desconto, mas paga o turnaround inteiro. Como os loops de repair e Reflexion são **sequenciais dentro de um run** (A4 encadeia cerca de 10 chamadas), agrupar chamadas dentro de um run implicaria cerca de 10 turnarounds em série por run, multiplicados por 1.320 runs só no tier cost-effective — inviável.

Batch real, portanto, só existe agrupando chamadas **entre runs distintos**, o que exige vários runs em voo simultaneamente. O `ExperimentRunner` era estritamente serial.

**Decisão.** Coalescência entre runs por pool de threads:

1. O `ExperimentRunner` mantém `BATCH_POOL_SIZE` runs em voo, um por thread.
2. `LLMClient.complete()` permanece bloqueante do ponto de vista do agente. Sob batch, delega ao `BatchCoalescer` (`infra/batch.py`), que agrupa as chamadas pendentes do mesmo par (provedor, modelo) em um único job, faz o poll e devolve o resultado à thread de origem.
3. O payload é montado uma única vez por `_params()` em cada provider e reutilizado nos caminhos síncrono e batch, garantindo que o batch envie exatamente o mesmo prompt, modelo e temperatura. Se divergissem, o tier rodado em batch estaria medindo outra coisa.
4. O agrupamento é por (provedor, modelo), não apenas por provedor: o Gemini exige modelo único por job, e a homogeneidade simplifica o mapeamento nos outros dois.
5. `--no-batch` volta ao caminho serial síncrono, coerente com `batch=False` no `cost_tracker` e com o teto global de $600 previsto em AD-09.

Constantes operacionais em `config.py`: `BATCH_POOL_SIZE=8`, `BATCH_MAX_SIZE=32`, `BATCH_WINDOW_SEC=45`, `BATCH_POLL_INTERVAL_SEC=15`, `BATCH_MAX_WAIT_SEC=86400`, `DOCKER_MAX_CONCURRENT=4`. Estas **não** são constantes experimentais no sentido de AD-07: não alteram o que o LLM recebe nem o que produz, apenas quantos runs ficam em voo e como as chamadas são agrupadas. Podem variar por ambiente sem afetar a comparabilidade entre células.

**Justificativa.** O desenho preserva AD-01: agentes e pipelines permanecem funções lineares síncronas com `while` explícito, e toda a assincronia fica confinada à camada de infraestrutura. Preserva também o breaker por run de AD-09, pois cada thread mantém seu próprio `CostTracker` e continua abortando apenas o próprio run.

A alternativa considerada — converter os pipelines em geradores suspensos e dirigir ondas de scatter-gather no runner — mudaria a forma de orquestração descrita em AD-01, enfraqueceria o breaker por run (só verificável entre ondas) e exigiria reescrever grande parte da suíte de testes do pipeline.

**Consequências.**

(a) **Breaker global sob concorrência (AD-09).** Com pool maior que 1, o breaker impede o agendamento de novos runs, mas os já em voo terminam: o teto global pode ser ultrapassado por até `BATCH_POOL_SIZE` runs. Não é possível interromper thread em execução sem descartar custo já consumido, o que violaria AD-08. Com `pool_size=1` a semântica serial estrita é preservada. Registrar como limite operacional nas ameaças à validade.

(b) **A taxa de cache hit sob batch é incógnita.** Itens processados em paralelo dentro de um job podem não compartilhar o prefixo cacheado, e o TTL do cache (5 min ou 1h) pode expirar entre jobs sucessivos. Os 85% medidos no smoke gratuito de 2026-07-17 vieram de chamadas sequenciais e não se transferem automaticamente. O piloto pago precisa medir a taxa real por provedor: o ganho combinado projetado em AD-15 pode ser menor que a soma de −50% (batch) e −90% (cache), caso um mecanismo cancele parcialmente o outro.

(c) **Concorrência exige teto de containers simultâneos** (`DOCKER_MAX_CONCURRENT`), para o paralelismo não saturar a máquina e contaminar o tempo de execução medido sob os timeouts de AD-06.

(d) **Estado compartilhado entre threads.** `GlobalBudget` e `ResultWriter.append` passaram a ser protegidos por lock; sem isso, incrementos concorrentes de custo se perderiam e linhas do JSONL poderiam se intercalar.

---

## 9. Parâmetros de amostragem

### AD-18 — Temperatura única do experimento: 1.0

**Status:** Aceita

**Contexto.** O pipeline usava `TEMPERATURE = 0.7`, constante única entre todos os modelos, projetos e arquiteturas, pela mesma lógica de AD-07: variar um hiperparâmetro de amostragem entre células confundiria efeito de arquitetura com efeito de hiperparâmetro.

Ao validar a integração com o GPT-5 mini em 2026-08-10, a API respondeu `400 unsupported_value`: *"'temperature' does not support 0.7 with this model. Only the default (1) value is supported."* A família GPT-5 aceita somente o default na Chat Completions. A restrição alcança presumivelmente também o GPT-5.5 do tier frontier, o que atingiria 2 dos 6 modelos do estudo.

Restava então um trilema: manter 0.7 e perder os modelos da OpenAI; usar 0.7 nos demais e 1 na OpenAI, fazendo a temperatura covariar com o provedor; ou unificar todos em 1.0.

**Decisão.** `TEMPERATURE = 1.0` para os seis modelos.

**Justificativa.** Preserva o núcleo da restrição de AD-07: um único valor, idêntico em todas as células do produto cartesiano. A justificativa original para temperatura maior que zero permanece intacta — a amostragem estocástica é intencional e sua variância é capturada pelas 5 repetições por célula, não suprimida pelo parâmetro. Claude, Gemini e GPT-5 aceitam 1.0, então a uniformidade é alcançável de fato.

A alternativa de usar 0.7 onde há suporte e 1 na OpenAI foi rejeitada: qualquer diferença medida entre os modelos da OpenAI e os demais ficaria inseparável do efeito de temperatura, uma ameaça à validade interna que a banca levantaria com razão. A alternativa de omitir o parâmetro e herdar o default de cada provedor foi rejeitada por ser implícita e frágil: um provedor pode alterar seu default durante a janela do experimento (jul–set/2026) sem aviso, quebrando a reprodutibilidade.

**Consequências.** (a) Temperatura 1.0 produz maior variância intra-célula que 0.7; registrar nas ameaças à validade, observando que as 5 repetições existem justamente para caracterizá-la. (b) Qualquer medição-piloto feita a 0.7 não é comparável com as coletas definitivas — nenhuma existe até esta data, pois o smoke de 2026-07-17 foi gratuito e serviu apenas para validar infraestrutura. (c) A restrição da OpenAI é uma dependência externa: se um provedor passar a rejeitar também 1.0, a decisão precisa ser revista por novo AD.

---

## 10. Orçamento de saída dos agentes

### AD-19 — Teto de tokens de saída por agente, dimensionado para modelos de raciocínio

**Status:** Aceita

**Contexto.** Os tetos de saída viviam como default hardcoded em cada agente: analyzer 2048, critic 2048, generator 4096, repair 4096. A instrumentação de `reasoning_tokens` (ver nota em AD-15) revelou que nos modelos de raciocínio esse teto é consumido também pelo raciocínio invisível — no GPT-5 mini, `max_completion_tokens` cobre as duas coisas.

Medições de 2026-08-10 com GPT-5 mini:

- Smoke A1 sobre um arquivo trivial (`calc.py`): 1.792 de 3.002 tokens de saída foram raciocínio (**59,7%**).
- Smoke A4 sobre `benchmark/voluptuous` com os tetos antigos: **73,7%** de raciocínio; 6 chamadas de LLM e `reflexion_iterations = 0`.

O valor zero no Reflexion não era ausência de necessidade: a cobertura estava em 30%, logo `_quality_ok` era falso e o CriticAgent foi chamado (é a única sexta chamada possível no fluxo do A4). Com ~1.800 tokens de raciocínio dentro de um teto de 2.048, sobravam poucas centenas para o JSON, que truncava e caía em `ValidationError` → `AgentOutputError` → o loop Reflexion era abortado em silêncio.

O efeito era grave e invisível: **o A4 degenerava em "A3 + repair"**, perdendo o mecanismo que o distingue, e o run era registrado como `completed` e `converged`. Pior, o efeito é assimétrico — atinge apenas modelos de raciocínio (GPT-5 mini, GPT-5.5), tornando-se um confundidor na comparação entre modelos.

**Decisão.** Mover os tetos para `config.py` como constante experimental única `MAX_OUTPUT_TOKENS`, dimensionada em cerca de 4x o orçamento visível desejado:

| Agente | Antes | Agora |
|--------|-------|-------|
| analyzer | 2048 | 8192 |
| critic | 2048 | 8192 |
| generator | 4096 | 16384 |
| repair | 4096 | 16384 |

Como toda constante experimental (regra 3 / AD-07), os valores são idênticos entre modelos, projetos e arquiteturas.

**Validação empírica.** Repetição do mesmo smoke A4 sobre `voluptuous`, apenas com os tetos novos:

| Métrica | Tetos antigos | Tetos novos |
|---------|---------------|-------------|
| `reflexion_iterations` | 0 | **1** |
| Cobertura de linha | 30,0% | **41,5%** |
| Cobertura de branch | 40,4% | **65,7%** |
| Cache hit | 74,5% | 84,0% |
| Raciocínio / output | 73,7% | 37,7% |
| Custo do run | $0,0225 | **$0,0725** |

O loop Reflexion passou a executar, confirmando a truncagem como causa. A cobertura subiu de forma relevante nas duas métricas.

**Consequências.** (a) O custo por run subiu cerca de 3x neste caso: o teto não é cobrado, mas destrava gerações que antes truncavam. (b) Combinado com o consumo real de tokens medido (~26k de input e ~7k de output por chamada, contra premissas de 8k/3k na tabela de custo), há indício forte de que a projeção de ~$158 do AD-16 está subestimada e o teto global de $250 (AD-09) pode ficar apertado — recalibrar no piloto antes de liberar o tier frontier. (c) Tetos maiores removem uma contenção implícita contra loop de alucinação; a proteção passa a ser exclusivamente o circuit breaker por run de AD-09. (d) O run de validação terminou com `status: failed` porque a re-geração do Reflexion piorou uma suíte que já passava — questão distinta, de interpretação do AD-08 (melhor estado versus último estado), registrada como pendência.

**Verificação de 2026-08-12: a truncagem foi DESCARTADA como causa residual.** O A4 sobre `benchmark/codeindex` falhou com `failure_reason` exibindo um fragmento de código iniciado no meio de uma expressão, compatível com arquivo truncado. Para decidir, a detecção de truncagem foi instrumentada: `LLMResponse` ganhou o campo `truncated`, normalizado dos três SDKs (`finish_reason == "length"` na OpenAI, `stop_reason == "max_tokens"` na Anthropic, `MAX_TOKENS` no Gemini), com aviso em log no único ponto por onde toda chamada passa. Uma sonda A1 sobre o mesmo projeto não disparou o aviso e consumiu **5.765 dos 16.384 tokens disponíveis** — 35% do teto. Os tetos do AD-19 estão, portanto, bem dimensionados, e a falha do A4 tem outra origem.

A instrumentação permanece no código independentemente disso: era a lacuna que tornou a falha do AD-19 silenciosa, e agora nenhuma truncagem futura passa despercebida.

**Evidência direta para o AD-08 (mesma sonda).** Comparando A1 e A4 sobre `codeindex`, mesmo modelo e mesmo projeto:

| | A1 | A4 |
|---|---|---|
| Chamadas | 1 | 13 |
| Custo | US$ 0,0140 | US$ 0,0954 |
| Cobertura de linha | **28,7%** | 24,7% |
| Cobertura de branch | **20,4%** | 12,5% |

**A arquitetura mais barata cobriu mais que a mais cara, gastando 6,8x menos.** Como o A4 passa necessariamente por um estado equivalente ao do A1 antes de iniciar os loops, o resultado só é possível porque o pipeline registra o estado FINAL e não o MELHOR: os loops de repair e Reflexion degradaram uma suíte melhor do que a que entregaram. Isso eleva a pendência do AD-08 de risco teórico a distorção medida, e ela incide sobre a comparação primária do estudo — agravada por AD-20, que reduziu a coleta a um único modelo.

---

## 11. Restrição de orçamento

### AD-20 — Teto de R$ 200 e redução do escopo a um único modelo

**Status:** Aceita quanto ao teto financeiro (decisão do autor). A redução de escopo dela decorrente está **Pendente (orientador)**.

**Contexto.** O autor fixou em 2026-08-10 um teto absoluto de **R$ 200** para o custo total de API do experimento. À cotação comercial de R$ 5,11/USD, acrescida de IOF e spread de cartão (~7,5%), isso equivale a cerca de **US$ 36**.

A projeção do AD-16 (~US$ 158) mostrou-se muito otimista quando repreciada com consumo real de tokens. As premissas da tabela de custo eram de 8.000 tokens de input e 3.000 de output por chamada, com cerca de 6 chamadas por run; o smoke A4 medido sobre `benchmark/voluptuous` consumiu ~26.000 de input e ~7.000 de output por chamada, em 9 chamadas. Repreciando o perfil medido para os seis modelos, o experimento completo custaria entre **US$ 741 e US$ 1.263** (R$ 3.800 a R$ 6.500) — de 5 a 8 vezes a projeção vigente.

Duas observações estruturais orientaram o corte:

1. **O custo é ~90% output.** Para o GPT-5 mini, o input efetivo (já com 84% de cache hit) responde por US$ 0,014 de um run de US$ 0,0725. Como o output escala pouco com o tamanho do projeto, **manter os 22 projetos é quase gratuito** — o que custa é o número de células modelo × repetição.
2. **O GPT-5 mini é desproporcionalmente barato.** O desenho completo custa US$ 25 nele, contra US$ 120 no segundo mais barato (Gemini 3.5 Flash, 4,7x) e US$ 401 no mais caro (GPT-5.5, 15,8x). Dentro de US$ 36 cabe exatamente um modelo, e ele é forçosamente o GPT-5 mini.

**Decisão.**

1. **`MAX_COST_GLOBAL_USD = 35.00`**, substituindo os US$ 250 de AD-09/AD-15. O valor deixa margem sob os US$ 36 porque a projeção tem incerteza alta, extrapolada de poucos runs.
2. **`MAX_COST_PER_RUN_USD["cost"] = 0.50`** (era 1.00): com teto global de US$ 35, um run de US$ 1 consumiria 3% de todo o orçamento. O A4 medido custou US$ 0,0725, então 0,50 preserva a margem de ~4x sobre o esperado que AD-09 exige.
3. **Escopo da coleta: apenas `gpt-5-mini`**, mantendo integralmente as demais dimensões — 4 arquiteturas × 22 projetos × 5 repetições = **440 runs**, custo projetado de US$ 25 (R$ 128).
4. **Execução em fases.** Rodar primeiro o desenho completo no GPT-5 mini e medir o custo real. Se sobrar folga sob o teto, avaliar um segundo modelo em escopo reduzido como verificação de robustez entre provedores.
5. `runner.py` recebe filtro `--model` para restringir a coleta sem alterar `MODELS` em `config.py` — a tabela dos seis modelos permanece registrada, preservando o desenho original para retomada futura.

**Justificativa.** O corte incide sobre o eixo **secundário** do estudo. A pergunta primária é a comparação entre as quatro arquiteturas no trade-off custo-qualidade, medida por CCP e CPKM; o modelo é dimensão de generalização. Preservam-se, portanto, intactos: as 4 arquiteturas, os 22 projetos, a estratificação em tercis de CC (7/7/8 conforme AD-16), as 5 repetições e, com elas, o poder do teste de Wilcoxon (n=22 pares) e do Kruskal-Wallis (3 faixas). Nenhuma dessas dimensões encolhe.

A alternativa de manter dois modelos exigiria cortar repetições para 1 (eliminando a caracterização de variância, agravada pela temperatura 1.0 de AD-18) ou reduzir pela metade o número de projetos (derrubando o n dos testes não-paramétricos e quebrando os tercis). Ambas atingiriam o eixo primário; a escolhida não.

**Consequências.**

(a) **A comparação entre modelos é perdida.** O trabalho não poderá afirmar que o ranking de arquiteturas generaliza entre modelos ou provedores. Isso deve ser reportado como limitação central e ameaça à validade externa, não como resultado nulo.

(b) **A dicotomia frontier versus cost-effective deixa de existir na coleta.** A disciplina de tiers de AD-15 torna-se inaplicável na prática: só o tier cost-effective roda, e apenas parcialmente.

(c) **Esta redução altera o escopo científico declarado na Metodologia** (6 modelos em dois tiers) e por isso **requer endosso do orientador** antes da coleta definitiva. O teto financeiro é decisão do autor; a forma de acomodá-lo dentro do método, não.

(d) A tabela `MODELS` com os seis modelos permanece em `config.py` e o desenho de 2.640 runs continua registrado em AD-16. Havendo créditos acadêmicos ou de pesquisa (via institucional já sugerida em AD-15), a coleta pode ser retomada sem mudança de código, apenas removendo o filtro `--model`.

(e) A projeção de custo de AD-16 fica formalmente **desatualizada**; os números vigentes para decisão de orçamento são os desta decisão.

**Recalibração de 2026-08-12 (segunda medição).** A projeção de US$ 25 do item 3 era extrapolada de um único run, sobre o `voluptuous` (96.747 chars, 3º menor do benchmark). Um segundo A4 foi medido sobre `benchmark/codeindex` (275.193 chars, praticamente a média do benchmark, que é 265.222): **US$ 0,0954**, 13 chamadas, 87,0% de cache hit, 905.859 tokens de input e 70.917 de output.

O par de medições permite separar a parcela de custo que escala com o tamanho do projeto da que não escala, em vez de extrapolar de um ponto só. **Um projeto 2,84x maior custou apenas 32% a mais** (US$ 0,0725 → US$ 0,0954), o que confirma quantitativamente a observação estrutural 1: o input, mesmo repetido a cada chamada, é barato sob cache e batch; o output domina.

**Correção do modelo de cache.** Uma terceira medição — A1 sobre o mesmo `codeindex`, US$ 0,0140, 1 chamada, 5.765 tokens de output — revelou um erro na primeira versão desta projeção, que aplicava os 87% de cache do A4 às quatro arquiteturas. **A primeira chamada de um run é sempre fria**: não há nada no cache até que o prefixo com o código-fonte seja enviado uma vez. Só as `n−1` chamadas seguintes reaproveitam. O A1, com uma única chamada, mediu **0,0% de cache** — e arquiteturas de poucas chamadas quase não se beneficiam do caching.

O modelo passa a derivar a taxa por arquitetura: `cache(arch) = (n−1)/n × w`, com `w` (taxa da chamada aquecida) calibrado pelo A4: `0,870 = (12/13) × w → w = 0,9425`. Assim calibrado, o modelo reproduz **as duas** medições: erro de −0,2% no A4 e +2,6% no A1.

| Arquitetura | Chamadas (pior caso) | Cache esperado | Custo/run (projeto médio) |
|---|---|---|---|
| A1 | 1 | 0,0% | US$ 0,0141 |
| A3 | 2 | 47,1% | US$ 0,0193 |
| A2 | 4 | 70,7% | US$ 0,0365 |
| A4 | 13 | 87,0% | US$ 0,0943 |

Projeção dos 440 runs, variando a taxa da chamada aquecida:

| Cache (aquecido) | Input | Output | **Total** | R$ (cartão) |
|---|---|---|---|---|
| 94,2% (medido) | US$ 5,86 | US$ 12,20 | **US$ 18,06** | R$ 99,15 |
| 50% (conservador) | US$ 11,67 | US$ 12,20 | **US$ 23,87** | R$ 131,05 |
| 0% (sem cache algum) | US$ 18,23 | US$ 12,20 | **US$ 30,43** | R$ 167,08 |

O cenário de cache nulo é o limite superior defensável e ainda cabe sob o teto de US$ 35 (R$ 192,15). Isso torna a coleta **segura sob o teto de R$ 200 mesmo se o prompt caching não funcionar sob batch com pool > 1** — cenário que segue sem medição (pendência AD-17b), mas que deixa de ser risco orçamentário. `MAX_COST_GLOBAL_USD = 35.00` fica **mantido**; a folga sobre o pior cenário é de 13%.

Ressalva: o pior caso projetado assume que todo run esgota os loops, mas não inclui reexecução de runs `crashed` (AD-10) nem o custo de retries de AD-11. A proteção contra ambos continua sendo o circuit breaker, não a projeção.

**Precificação do eixo de modelos.** Com o modelo validado, o desenho completo (440 runs) por modelo, na taxa de cache medida:

| Modelo | Tier | Desenho completo | × gpt-5-mini |
|---|---|---|---|
| gpt-5-mini | cost | US$ 18,06 | 1,0x |
| gemini-3.5-flash | cost | US$ 79,18 | 4,4x |
| gemini-3.1-pro | frontier | US$ 105,57 | 5,8x |
| claude-sonnet-4-6 | cost | US$ 139,88 | 7,7x |
| claude-opus-4-8 | frontier | US$ 233,14 | 12,9x |
| gpt-5.5 | frontier | US$ 263,94 | 14,6x |
| **os seis** | | **US$ 838** | |

Registra-se, para futura consulta, que **excluir os modelos do Google não é um corte viável**: o Gemini Flash é o segundo mais barato do conjunto e o Gemini Pro é mais barato que o Sonnet. Manter apenas GPT e Claude custaria US$ 653 (R$ 3.586), 19x o teto, porque preserva justamente os dois modelos mais caros. Como o custo é ~70% output e o preço de output varia 15x entre `gpt-5-mini` e `gpt-5.5`, nenhum rearranjo do conjunto de modelos contorna a restrição orçamentária.

Ressalva de precisão para essa tabela: os valores de Claude estão **subestimados**, porque o `cost_tracker` ignora o custo de ESCRITA de cache (a Anthropic cobra 1,25x o preço base), simplificação inofensiva enquanto a coleta usa só a OpenAI. Se algum modelo Anthropic entrar na coleta, o `cost_tracker` precisa ser corrigido antes — CCP e CPKM dependem dele.

---

## 12. Conflito entre isolamento e teste de mutação

### AD-21 — mutmut não roda sob o filesystem read-only de AD-04

**Status:** PROPOSTA — **opção (A) validada e implementada em 2026-08-16; falta o aceite formal do autor.** Ver "Resolução" ao fim desta seção.

**Contexto.** O piloto de 2026-08-14 (4 arquiteturas × `codeindex` × rep-1) produziu `mutation_score: null` nos quatro runs, inclusive no A2, que convergiu (`all_passed = true`, condição que dispara o mutmut em `test_executor.py:355`). Diagnóstico direto no container, sem API:

```
OSError: [Errno 30] Read-only file system: '/project/codeindex/analyze.py'
```

O mutmut 3.6 escreve os mutantes **ao lado do código-fonte**. Com `source_paths` absoluto (`/project/codeindex`), ele tenta escrever dentro de `/project`, que é read-only: a imagem é construída com o projeto em `/project` (Dockerfile.base) e o container roda com `--read-only`, tendo como graváveis apenas `/tmp` (tmpfs) e `/work` (bind mount).

**AD-04 e AD-06 são, portanto, incompatíveis como implementados.** Não é bug de uma linha: é o isolamento exigido por um AD bloqueando a métrica exigida por outro. Enquanto isso valer, **o CPKM não tem denominador** — metade do par de métricas primárias do estudo.

Agrava o quadro que a falha é **silenciosa**: `parse_mutmut_stats` devolve `None`, `_apply_mutation_results` retorna sem erro (`test_executor.py:411-412`) e o run é gravado como `completed`. Rodar os 440 runs assim produziria um dataset íntegro em tudo menos no CPKM, sem um único erro no log — a mesma classe de falha que motivou AD-19.

**Opções levantadas** (nenhuma escolhida):

- **(A) Mutar uma cópia em `/work`.** Copiar o pacote de `/project/<pkg>` para `/work/<pkg>` dentro do container e passar `source_paths` **relativo**, de modo que o `mutants/` do mutmut caia em `/work`, que já é gravável. Preserva `--read-only` integralmente. Risco a verificar: o pacote instalado via `pip install -e .` aponta para `/project`, então `import <pkg>` pode resolver para o original em vez do mutante — precisa de teste antes de adotar.
- **(B) Tornar `/project` gravável na fase de mutação.** Simples, mas contradiz a garantia que AD-04 declara no texto do TCC. O container é efêmero e sem rede, então o raio de dano é pequeno — ainda assim é enfraquecer uma decisão registrada.
- **(C) Overlay tmpfs sobre `/project`.** Mantém o host intacto e dá visão gravável ao mutmut, ao custo de complexidade no `build_command`.
- **(D) Abrir mão do CPKM.** Descarta metade do desenho. Registrada apenas para deixar explícito que é a alternativa a não resolver o problema.

**Custo de tempo, ainda não medido.** No diagnóstico, a fase "Generating mutants" do `codeindex` rodou minutos antes de estourar. `MUTMUT_TIMEOUT_SEC = 1800` por run, sobre 440 runs, é risco de cronograma independente da opção escolhida e precisa ser medido assim que a mutação funcionar em ao menos um projeto.

#### Resolução — opção (A) validada empiricamente (2026-08-16)

O risco que travava a opção (A) era a resolução de import: se `import <pkg>` fosse resolvido para `/project` (o install editável) em vez da cópia mutada em `/work`, o mutmut reportaria **zero mutantes mortos** em vez de um erro — falha silenciosa pior que o `Errno 30`. O teste, com um marcador injetado na cópia, descartou o risco:

```
FILE=  /work/codeindex/__init__.py     MARKER=True
FINDERS= ['builtins', ...]      # o install editável não registrou meta_path finder
```

O `cwd` do container precede o install editável no `sys.path`, então a cópia vence. Execução completa do mutmut sobre a suite convergida do A2:

```
🎉 killed 781   🙁 survived 1318   🫥 no_tests 9544   ⏰ 0   🤔 0
mutation_score = 781 / 2099 = 0,372
```

Mutantes são de fato **mortos**, confirmando que os testes exercitam a cópia. `--read-only` permanece integralmente em vigor: nada em `/project` é tocado.

**Implementação.** `TestExecutor` ganhou `project_root` e `_materialize_sources`, que copia os `source_paths` para o workdir antes de `mutmut run`; `runner.default_executor_factory` voltou a passar `source_paths` **relativos**. O `ExecutionResult` ganhou **`mutation_error`**: a ausência de métrica de mutação passa a registrar a causa, em vez de sair como `None` indistinguível de "não tentou" — era exatamente essa indistinguibilidade que teria deixado uma coleta de 440 runs sair sem CPKM e sem erro em log nenhum.

**Custo de tempo, agora medido.** No `codeindex` (7.757 LOC — praticamente a mediana do benchmark, cuja média é 7.447), com a suite do A2 a 20,7% de cobertura:

| Fase | Tempo |
|------|-------|
| pytest + cobertura | 3,1 s |
| mutmut (11.643 mutantes gerados, 2.099 executados) | **366,2 s** |

A mutação é ~120× mais cara que a execução da suite e passa a **dominar o cronograma**. Projeção para 440 runs, a uma execução de mutação por run e `DOCKER_MAX_CONCURRENT = 4`: ~11 h só de mutação, contra ~10 h de latência da Batch API. Como a mutação roda **a cada estado que passa** (`test_executor.py:355`), e não só no estado final, A2 e A4 multiplicam esse custo — estimativa realista de **16 a 24 h** para a coleta completa.

#### Correção de 2026-08-16 — a validação num projeto só escondeu 7 dos 22

A resolução acima foi declarada com evidência de **um** projeto (`codeindex`, pacote na raiz). O primeiro disparo real da coleta parou em 21/440 runs porque **todo run que convergia perdia a mutação**, com mensagem diferente do `Errno 30`:

```
done in 97ms (0 files mutated, 0 ignored, 0 unmodified)
Stopping early, because we could not find any test case for any mutant.
```

Dois defeitos independentes, ambos em `project_loader._infer_cov_target`, que inferia os alvos pelo **nome da pasta**:

1. **Separador do Windows.** `source_paths` saía como `src\pkg\api.py`; escrito no `setup.cfg` e lido pelo mutmut dentro do container Linux, virava um nome de arquivo sem sentido — daí `0 files mutated`.
2. **A cópia não sombreava o pacote.** Em layout `src/`, copiar `src/pkg` para `/work/src/pkg` não põe `pkg` no caminho de import: os testes seguiriam exercitando o original em `/project`. Corrigido só o separador, o mutmut mutaria uma cópia que ninguém importa e **todos os mutantes sobreviveriam** — `mutation_score` baixo e plausível, sem erro. Era exatamente o falso-zero que o teste do marcador havia descartado, mas para o layout errado.

Um terceiro defeito, **anterior a AD-21 e alheio à mutação**, apareceu no mesmo diagnóstico: `--cov=claude-agent-sdk-python` não é módulo importável (o pacote é `claude_agent_sdk`, sob `src/`). Runs **convergiam** com `line_coverage=None`, isto é, com **CCP e CPKM os dois vazios**. Nos 21 runs coletados, 14 estavam sem cobertura.

**Correção.** `_infer_cov_target` passou a ler o empacotamento declarado (`pyproject.toml`: `tool.setuptools.packages.find.where`, `package-dir`, `py-modules`, `tool.hatch.build.targets.*.packages`; `setup.cfg`: `[options] package_dir`, `[options.packages.find] where`) e devolve pares `(origem, destino)` em POSIX, com o destino sempre na **raiz** do workdir. Resultado: 21 dos 22 projetos resolvem para o pacote importável correto.

**Prevenção.** `scripts/check_benchmark_targets.py` roda os 22 projetos no container de produção, sem API, e exige de cada um (a) cobertura não-nula sob o `cov_target` inferido e (b) `__file__` do pacote importado começando em `/work`, provando o sombreamento. É pré-requisito de qualquer disparo. Resultado atual: **21/22 PASS**.

**Limitação aceita — `Waza` fora da coleta.** É o único projeto sem qualquer metadado de empacotamento: 23 scripts soltos em `plugins/waza/skills/*/scripts/`, sem pacote importável. Não há alvo de `--cov` nem sombreamento possível sem tratamento ad hoc. Decisão do autor em 2026-08-16: **não construir exceção; excluir da coleta e declarar como limitação** no capítulo de ameaças à validade. A coleta vigente passa a ser **21 projetos × 4 arquiteturas × 5 repetições = 420 runs**, contra os 440 de AD-20. Isso altera a composição fixada por AD-16 (tercis de 7/7/8) e precisa ser reportado como desvio, não como desenho original.

**Observação relacionada, também para ameaças à validade:** o run do `Waza` que havia convergido tinha como suite inteira `def test_ignored_doc_marker(): assert "@docs/ignored.md" == "@docs/ignored.md"` — uma tautologia que não importa nada do projeto, registrada como `converged=true`. Com a cobertura funcionando, uma suite assim mede 0% e perde o ratchet para qualquer alternativa; mas em A1, que produz um único estado, ela ainda é registrada como convergida. A taxa de convergência de A1 deve ser lida com essa ressalva.

**Risco novo, ainda sem decisão: `MUTMUT_TIMEOUT_SEC = 1800` enviesa o CPKM contra as suites boas.** O tempo de mutação cresce com a cobertura, porque só mutantes cobertos são executados (aqui, 2.099 de 11.643 = 18%, espelhando os ~20% de cobertura). Num projeto grande como `solvOR` (19.086 LOC, ~28.600 mutantes), uma suite que alcançasse 60% de cobertura executaria ~17.000 mutantes ≈ 3.000 s e **estouraria o timeout**, saindo sem `mutation_score`. O efeito é sistemático e na direção errada: **quanto melhor a suite, maior a chance de perder a medição** — exatamente as células que mais importam para comparar arquiteturas. Como `MUTMUT_TIMEOUT_SEC` é constante experimental (regra 3), alterá-la exige decisão registrada. Não alterada.

---

## 13. Tratamento dos dados coletados

### AD-22 — Timeout de mutação não invalida a convergência: reclassificação na análise

**Status:** Aceita (2026-08-17)
**Contexto:** o risco previsto no fim de AD-21 se materializou. Na coleta completa (420/420 runs, US$ 9,49), **26 runs** terminaram com `status = timeout`. Destes, **25 têm suite verde** — `passed > 0`, `failed = 0`, `errors = 0` — e **cobertura medida**. Ou seja: convergiram de fato, e o que estourou os 1800 s foi apenas a fase de mutação, posterior e independente do critério de convergência.

A causa é conhecida e está em `test_executor.py`: no timeout do mutmut, `result.timed_out = True` propaga para o status do run, que vira `timeout` com `converged = False`. O dado de convergência é descartado por uma falha que não diz respeito a ele.

O viés é sistemático e na direção errada, como previsto: por arquitetura, os 25 se distribuem em **A2 = 14, A4 = 9, A1 = 2** — concentrados exatamente nos braços com laço de reparo, que produzem as suites de maior cobertura e por isso executam mais mutantes.

**Decisão.** Corrigir **na análise**, não recoletando. `scripts/analyze.py` reclassifica como convergido todo run com `status = timeout` que tenha suite verde e `line_coverage` não nula, registrando `mutation_score = None`. O campo `converged` do JSONL **não é reescrito**: o JSONL permanece o log fiel do que a coleta observou (AD-12), e a reclassificação é uma regra de leitura, aplicada e documentada no analisador.

**Efeito:** convergências vão de **81 para 106** (+31% de n útil). Os 25 entram nas estatísticas de cobertura e de CCP; ficam fora do CPKM, junto dos outros 4 convergidos sem métrica de mutação. CPKM final: **77 observações**.

**Alternativas descartadas:**

- *Corrigir o código e recoletar os 25.* Custaria ~US$ 0,60 de API e horas de mutmut, com chance concreta de estourar 1800 s de novo e voltar ao mesmo ponto — a causa é o tamanho do espaço de mutantes, não um defeito transitório.
- *Deixar como está.* Perderia 25 convergências reais com viés declarado contra A2 e A4, os dois braços que o estudo compara.

**Consequência para o texto:** relatar em ameaças à validade que o CPKM é medido sobre um subconjunto **enviesado contra as suites de maior cobertura**, e que a comparação de mutação entre arquiteturas herda esse viés. A correção do `test_executor.py` fica registrada como trabalho futuro; não foi aplicada para não divergir do código que produziu os dados.

### AD-23 — Recorte analítico: A2×A4 quantitativo, A1/A3 qualitativo

**Status:** Aceita (2026-08-17)
**Contexto:** as taxas de convergência da coleta completa são desiguais a ponto de inviabilizar o desenho original:

| Arq | runs | convergidos | taxa | com CPKM |
|-----|------|-------------|------|----------|
| A1 | 105 | 3 | 3% | 3 |
| A2 | 105 | 42 | **40%** | 40 |
| A3 | 105 | 1 | **1%** | 1 |
| A4 | 105 | 35 | **33%** | 33 |

Com n = 3 e n = 1, A1 e A3 não sustentam CCP nem CPKM: uma média sobre uma observação não é estimativa. Pior, o Wilcoxon **pareado** exige o mesmo projeto convergindo nos dois braços, e A1/A3 não fornecem pares suficientes.

Não é defeito de pipeline. As 100 falhas de A1 e as 104 de A3 têm **todas** suite vermelha ou suite que nem executa; a distribuição de motivos é a mesma de A2 e A4, só que sem o laço que as corrige. É o achado, não o ruído: **geração de passe único raramente produz suite executável em projeto real.**

**Decisão.** O teste estatístico (Wilcoxon pareado; Kruskal-Wallis entre os tercis de CC) roda **apenas entre A2 e A4**, que têm massa. A1 e A3 entram no artigo pela **taxa de convergência** e pela análise de motivos de falha, como evidência qualitativa de que o laço de reparo é o que separa "funciona" de "não funciona". As tabelas de CCP/CPKM de A1 e A3 são reportadas com n explícito e marcadas como não interpretáveis.

**Consequência:** a pergunta de pesquisa se reformula de "qual das quatro arquiteturas tem melhor trade-off custo-qualidade" para "**a que custo o laço de reparo compra a convergência, e o reflexion de A4 paga o próprio preço sobre A2**" — com A1/A3 estabelecendo o piso. A leitura de A1 herda ainda a ressalva de AD-21 sobre suites tautológicas registradas como convergidas.

---

## 14. Achados pós-coleta e segunda rodada

### AD-24 — Quatro projetos com convergência zero: falha silenciosa de instalação e incompatibilidade com AD-05

**Status:** Proposta

**Contexto.** A investigação de por que A3 converge em 1% (2026-08-24) não encontrou
defeito em A3 — encontrou um defeito no benchmark. Quatro dos 21 projetos têm
**zero convergências em todas as quatro arquiteturas e todas as cinco repetições**:
`tnz`, `solvOR`, `fabric-rti-mcp` e `reviewable-html-workbench`. São **80 runs, 19%
da coleta**, que não mediram capacidade de modelo algum.

Duas causas distintas, ambas do pipeline, não do LLM:

**(a) O pacote sob teste nunca foi instalado no container** (`tnz`, `solvOR`,
`fabric-rti-mcp` — 60 runs). O `pytest` falha na coleta com
`ModuleNotFoundError: No module named 'tnz'`, e o pipeline credita isso ao modelo
como "suite não executa". A origem é `docker/Dockerfile.base:13`:

```dockerfile
RUN pip install --no-cache-dir -e . 2>/dev/null || \
    pip install --no-cache-dir -r requirements.txt 2>/dev/null || true
```

O `|| true` **engole a falha de build** e o `2>/dev/null` descarta a razão: a imagem
é construída com sucesso e sem o pacote. Os três projetos falham no build por
motivos verificados no `pyproject.toml` de cada um:

| Projeto | Build backend | Por que falha em `python:3.12-slim` |
|---------|---------------|-------------------------------------|
| `tnz` | `versioningit==3.3.0` | deriva a versão do git; a cópia do benchmark não tem `.git` |
| `fabric-rti-mcp` | `setuptools_scm` | idem — versão derivada do git, sem `.git` |
| `solvOR` | `maturin` + `pyo3/extension-module` | extensão Rust; a imagem não tem toolchain Rust |

`scripts/check_benchmark_targets.py` não pega isso: ele valida a **inferência de
alvos** (o que corrigiu a v3.2), não se o pacote de fato importa dentro do container.
É o mesmo defeito que excluiu `Waza`, detectado tarde demais e por outro sintoma.

**(b) Incompatibilidade estrutural com o guarda AST de AD-05**
(`reviewable-html-workbench` — 20 runs). Em 19 dos 20 runs o motivo final é
`import proibido: socket; import proibido: subprocess`. O projeto exige
legitimamente esses módulos para ser testado. A contenção opera como projetada;
o projeto é que é incompatível com ela.

**Decisão proposta.** Tratar os quatro projetos como **fora da população medível**:
os 80 runs permanecem no JSONL (AD-12: log imutável) e no custo total declarado do
experimento (US$ 9,49), mas saem inteiramente das métricas — convergência, CCP,
CPKM, Wilcoxon e Kruskal. Não recoletar.

Note-se que isso é uma **exceção deliberada a AD-08**, que manda o custo de runs
falhos entrar no numerador de CCP/CPKM. AD-08 existe para que o desperdício de uma
*arquitetura* apareça no seu preço; aqui o desperdício é da *infraestrutura* e
atinge os quatro braços igualmente (15 runs cada), de modo que mantê-lo no
numerador não distingue arquiteturas — apenas infla todas na mesma proporção e
mistura duas fontes de custo. A exceção vale só para projetos que deram zero
convergências em todos os braços.

**Efeito nos números publicados:**

| Braço | 21 projetos (atual) | 17 saudáveis (proposto) |
|-------|--------------------:|------------------------:|
| A1 | 5/105 = 5% | 5/85 = **6%** |
| A2 | 56/105 = 53% | 56/85 = **66%** |
| A3 | 1/105 = 1% | 1/85 = **1%** |
| A4 | 44/105 = 42% | 44/85 = **52%** |

**Justificativa.** A taxa sobre 21 projetos mede a união de duas coisas diferentes —
a capacidade do modelo e um defeito de infraestrutura — e subestima a primeira em
13 pontos percentuais no braço vencedor. A separação é limpa porque o defeito é
**uniforme entre os braços**: os quatro projetos deram zero para todos, então a
comparação A2×A4 e todo o Wilcoxon pareado ficam inalterados (aqueles projetos já
não contribuíam pares). Recoletar exigiria consertar o Dockerfile e refazer 80 runs;
o ganho seria marginal, porque `solvOR` continuaria precisando de toolchain Rust e
`reviewable-html-workbench` continuaria batendo em AD-05.

**Consequências — quatro ameaças à validade, quantificadas em 2026-08-24.**

**(1) O atrito não é aleatório: concentra-se na alta complexidade, e a
reestratificação move projetos de banda.** Dos quatro excluídos, três estavam na
banda alta e um na média — nenhum na baixa. A direção tem causa: empacotamento
sofisticado (extensão Rust, versão derivada do git) correlaciona com projetos
maiores e mais complexos. É **viés de sobrevivência na direção de projetos mais
simples**.

**Decisão sobre os tercis: reestratificar o pool efetivo (5/6/6).** AD-16 define os
tercis como partição do *próprio pool*, e a leitura fiel a isso é reparticionar o
pool que sobrou, em vez de manter as bandas originais amputadas (que dariam
7/6/4, com a banda alta reduzida a 4 projetos). Os tercis vão de 7/7/8 (desenho)
para 7/7/7 (`Waza`) e agora para **5/6/6**.

O custo dessa escolha é declarado: como a distribuição inteira desloca para baixo,
**três projetos trocam de banda** — `aioamazondevices` e `boltons` de baixa para
média, `k0dasm` de média para alta. A partição passa a depender de quais projetos
sobreviveram, o que é grau de liberdade do pesquisador, e o efeito é mensurável:
o Kruskal-Wallis de cobertura de A2 vai de $p = 0{,}071$ (7/7/7) para
$p = 0{,}207$ (5/6/6). Nenhuma das duas partições altera a conclusão — cobertura
não difere entre bandas em nenhuma delas, e mutação difere em ambas —, mas o
$p$-valor exato é sensível ao corte e isso deve constar do texto.
*Validade externa e de conclusão.*

**(2) A categoria "suíte não executa" está contaminada, e de forma desigual entre
os braços.** Dos runs classificados nesse motivo, 15 por braço são falha de
instalação do pacote, não do modelo:

| Braço | runs na categoria | falha de instalação | % contaminado |
|-------|------------------:|--------------------:|--------------:|
| A1 | 70 | 15 | 21% |
| A2 | 33 | 15 | **45%** |
| A3 | 71 | 15 | 21% |
| A4 | 39 | 15 | **38%** |

A tabela de motivos de falha do artigo superestima o defeito do modelo, e
superestima **quase o dobro** nos braços com repair loop — porque neles as falhas
genuínas são poucas e os 15 runs de infraestrutura pesam mais.
*Validade de construto.*

**(3) O pipeline atribuiu ao modelo uma falha do ambiente, em silêncio.** É a
terceira falha silenciosa registrada neste ADR (v3.0: mutmut sob read-only;
v3.2: inferência de alvos; agora o build). O padrão recorrente é o mesmo: um
caminho de erro que devolve ausência de dado em vez de erro alto.
`scripts/check_benchmark_targets.py` deve passar a validar **o import dentro do
container**, não só a inferência de alvos. *Validade interna.*

**(4) O guarda de AD-05 exclui uma classe inteira de projetos** — os que exigem
I/O de rede ou subprocesso. `reviewable-html-workbench` é o caso extremo (19 de 20
runs), mas o guarda reprovou 167 suítes no total. A generalização fica restrita a
projetos computacionalmente fechados. *Validade externa.* Correção do
`Dockerfile.base` (remover o `|| true`, falhar o build alto) fica como **trabalho
futuro**, e é pré-requisito de qualquer coleta nova, inclusive a de AD-25.

---

### AD-25 — Segunda rodada de coleta em um segundo modelo, restrita a A2 e A4

**Status:** Proposta

**Contexto.** AD-20 reduziu a coleta a um único modelo por restrição orçamentária,
e essa é a limitação mais séria do trabalho: toda conclusão vale "neste modelo".
A conclusão central — a arquitetura mais complexa (A4) é dominada pela mais simples
(A2) em todo custo, sem ganho de cobertura e com mutação pior — é exatamente do
tipo que um revisor pede para ver replicada. A coleta gastou US$ 9,49 dos R$ 200,
deixando margem.

**Decisão proposta.** Uma segunda rodada em **Claude Sonnet 5**, restrita a **A2 e
A4** e aos 17 projetos saudáveis de AD-24, mantendo as 5 repetições. A1 e A3 ficam
de fora: por AD-23 não produzem massa estatística, e replicá-los reproduziria zeros
a custo real.

**⚠ Impacto orçamentário — projeção sobre os tokens reais da coleta**

| Modelo | A2 só | **A2+A4** | A2+A4 (3 rep) |
|--------|------:|----------:|--------------:|
| Haiku 4.5 | R$ 38 | R$ 110 | R$ 66 |
| **Sonnet 5 (promo, até 31/08/2026)** | R$ 76 | **R$ 219** | R$ 132 |
| Sonnet 4.6 / Sonnet 5 padrão | R$ 114 | R$ 329 | R$ 197 |
| Gemini 3.5 Flash | R$ 62 | R$ 180 | R$ 108 |
| Opus 5 | R$ 190 | R$ 549 | R$ 329 |

Com Batch (−50%) e cache, a R$ 5,40/US$. **A opção recomendada leva o total do
projeto a R$ 270, contra o teto de R$ 200 de AD-20** — é uma revisão consciente do
teto, não um estouro acidental, e depende de decisão explícita do autor.

**Justificativa da escolha do modelo.** Sonnet está no mesmo tier `cost` que o
`gpt-5-mini` em `config.py`, e já consta da tabela `MODELS` como `claude-sonnet-4-6`:
é o par de comparação que o desenho original previa e que AD-20 cortou. Haiku 4.5
custaria metade, mas é um modelo mais fraco, e o risco concreto é ele convergir tão
pouco quanto A1/A3 — gastar R$ 110 para produzir outra tabela de zeros é o modo de
falha que este trabalho já observou uma vez. O tier frontier (Opus 5, R$ 549) está
fora de qualquer cenário.

**Pré-requisitos técnicos, todos bloqueantes:**

1. **`Dockerfile.base` precisa falhar alto** (AD-24). Sem isso a segunda rodada
   herda os mesmos 4 projetos mortos.
2. **`cost_tracker` não cobra cache-write da Anthropic.** Registrado em
   `PENDENCIAS.md` como irrelevante — era, enquanto a coleta era só OpenAI. A
   Anthropic cobra escrita de cache a **1,25× a entrada**, e o disjuntor financeiro
   de AD-09 não veria esse gasto: o orçamento poderia estourar sem o freio disparar.
   **É o bloqueador mais sério.**
3. **AD-18 (`TEMPERATURE = 1.0`) é incompatível com Sonnet 5.** O modelo removeu
   `temperature`, `top_p` e `top_k`: enviar qualquer valor devolve 400. O provider
   precisa **omitir** o parâmetro, não passar 1.0. Em Sonnet 4.6 ainda é aceito.
4. **AD-19 (teto de saída 8192) precisa ser reavaliado.** Os modelos Anthropic
   atuais raciocinam por padrão e o raciocínio conta como saída. O `gpt-5-mini`
   gastou 2,4M de tokens de raciocínio; se o Sonnet gastar mais, o teto trunca e
   vira falha artificial — o mesmo defeito que a v2.5 corrigiu.
5. **`ANTHROPIC_API_KEY` não está configurada** (verificada apenas a presença).

**Consequências.** Se aceita, a pergunta de pesquisa de AD-23 ganha uma segunda
dimensão: deixa de ser "o reflexion de A4 paga o próprio preço sobre A2 neste
modelo" e passa a "…e esse resultado se sustenta entre modelos de provedores
diferentes". Se rejeitada, AD-20 permanece como está e o modelo único segue
declarado como ameaça à validade central.

**Incerteza declarada.** A projeção reaproveita a contagem de tokens da OpenAI;
os tokenizadores diferem e a margem real é da ordem de ±20–30%. O endpoint
`messages.count_tokens` da Anthropic é gratuito e elimina essa incerteza antes de
qualquer gasto — deve ser rodado como parte do pré-requisito (5).

#### Atualização de 2026-08-28 — pré-requisitos (1)–(3) implementados

O modelo escolhido é **`claude-sonnet-5`** (e não `claude-sonnet-4-6`): é o
modelo corrente do mesmo tier e o único sob preço promocional. Implementado:

- **(1)** `docker/Dockerfile.base` falha alto: o `2>/dev/null || true` saiu e o
  build aborta com a mensagem de erro original quando o pacote não instala.
  **Correção do próprio AD-24, verificada empiricamente em 2026-08-28:** tirar
  o `|| true` era *insuficiente*, e o diagnóstico original estava incompleto.
  Em `tnz`, o `pip install -e .` falha (backend `versioningit`, sem `.git`) mas
  o fallback `pip install -r requirements.txt` **SUCEDE** — instala as
  dependências e não o pacote. O build passava mesmo sem o `|| true`. Pior:
  `import tnz` funciona a partir de `/project`, porque o diretório do pacote
  está no cwd; a falha só aparece na execução real, feita a partir de `/work`,
  como `ModuleNotFoundError`. Ou seja: **sucesso do `pip install` não prova que
  o pacote sob teste ficou instalado**, e nenhuma checagem feita de dentro de
  `/project` detecta a diferença. O Dockerfile agora valida
  `cd / && python -c "import <cov_target>"` — fora de `/project`, que é a
  condição real dos testes —, com o nome vindo do mesmo `cov_target` que o
  `project_loader` infere, passado por `--build-arg IMPORT_NAME`. Verificado:
  `tnz` quebra o build com mensagem explícita; os 17 projetos saudáveis passam.
- **(2)** `CACHE_WRITE_INPUT_FACTOR = 1.25` em `config.py`, propagado de
  `AnthropicProvider._to_response` (`cache_creation_input_tokens`) até
  `price_call`. `TokenUsage.cache_write_tokens` é campo novo com default 0, de
  modo que os registros da coleta de agosto desserializam com o custo
  inalterado — verificado por teste de regressão.
- **(3)** `NO_SAMPLING_PARAMS` em `config.py`: `_params()` **omite**
  `temperature` para os modelos que a rejeitam. AD-18 permanece intacto —
  omitir equivale a enviar 1.0, que é o default dos três provedores, então a
  constante segue única entre as células; muda só o que trafega no payload.

**Projeção revisada sobre os tokens medidos** (só A2+A4, só os 17 projetos de
AD-24 — 170 runs): **US$ 30–32**, ou **R$ 164–171** a R$ 5,40/US$, contra os
R$ 219 estimados acima. A diferença é o recorte de AD-24, que removeu 19% dos
runs. O total do projeto fica em **R$ 215–222**, não R$ 270.

**Duas decisões novas, exigidas pela aritmética e ainda NÃO aplicadas** (regra
7: nenhuma mudança que aumente consumo de API sem aviso prévio):

| Constante | Hoje | Necessário | Por quê |
|---|---:|---:|---|
| `MAX_COST_GLOBAL_USD` | 35,00 | ~45,00 | US$ 9,49 já gastos + ~US$ 31 da segunda rodada = US$ 40,5. Com o teto atual o disjuntor de AD-09 dispara no meio da coleta. |
| `MAX_COST_PER_RUN_USD["cost"]` | 0,50 | ~1,00 | Reprecificando os runs de A4 já coletados a preço de Sonnet, 2 dos 85 passam de US\$ 0,50 e 8 passam de US\$ 0,40 — **antes** de contar o *thinking* adaptativo, que é ligado por padrão no Sonnet 5 e soma tokens de saída. Mantido em 0,50, o breaker mataria os runs mais caros de A4 e a coleta mediria o disjuntor, não o modelo. |

Elevar o teto global é **revisão explícita de AD-20**, não estouro acidental.

**Risco residual — pré-requisito (4) permanece aberto.** `MAX_OUTPUT_TOKENS`
não foi alterado: é constante experimental (regra 3) e mexer nela quebraria a
comparabilidade com a coleta do `gpt-5-mini`. O Sonnet 5 raciocina por padrão e
o raciocínio conta contra o mesmo teto, então há risco real de truncagem. A
decisão é **medir antes de decidir**: um smoke run de A2 e A4 em um projeto,
conferindo o sinal `truncated` de AD-19, custa centavos e resolve
empiricamente. Se truncar, a escolha é entre elevar o teto (perde
comparabilidade) ou baixar `output_config.effort` (idem) — e aí vira novo AD.

**Janela da promoção.** O preço de US\$ 2/US\$ 10 vale até **2026-08-31**. A
coleta de 420 runs levou 32 h de relógio; 170 runs projetam ~13 h, o que cabe.
Chamadas que caírem depois da virada são faturadas a US\$ 3/US\$ 15 e o custo
sobe ~50%. A data de execução é condição do número publicado.

#### Atualização de 2026-08-30 — projeção de custo corrigida por medição

Os 20 primeiros runs reais de `claude-sonnet-5` custaram **US\$ 10,76**, média
de **US\$ 0,5381/run** contra os US\$ 0,3755 projetados — **43% acima**. A
projeção anterior modelava a saída como linear no tamanho do prefixo
(`out = 5155 + 0,05421 x prefixo`), ajustada sobre smoke runs, e não capturou
a frequência com que o Sonnet 5 **satura o teto de saída de AD-19**: o run
trunca, paga a saída inteira e ainda entrega suíte incompleta. Extrapolando a
média medida, os 102 runs dariam US\$ 54,89; somados aos US\$ 9,49 do
`gpt-5-mini`, **US\$ 64,38**.

Duas revisões de disjuntor foram decididas pelo autor em 2026-08-30:

| Parâmetro | De | Para | Motivo |
|---|---|---|---|
| `MAX_COST_GLOBAL_USD` | 50,00 | **65,00** | Com 50, o disjuntor de AD-09 abortaria por volta do run 55 e deixaria a rodada de AD-25 pela metade *depois* de gastar ~R\$ 270. ~R\$ 348. |
| `MAX_COST_PER_RUN_USD["cost"]` | 1,50 | **2,00** | O run A4/boltons/rep-1 custou US\$ 1,87 e foi abortado. Por AD-08 o abort não devolve dinheiro: as 3 repetições gastariam ~US\$ 4,50 para produzir zero observação. Concluir custa ~US\$ 1,20 a mais e preserva a célula boltons x A4 do desenho pareado de AD-23. |

Alternativa descartada pelo autor: cair para 2 repetições (cabia em US\$ 46,
mas enfraqueceria o teste estatístico de AD-23).

**Truncamento mantido deliberadamente.** Decidido pelo autor não mexer no teto
de saída de AD-19, apesar de o truncamento explicar parte dos 7 `failed` em 20
runs. Razão: o teto é parte do protocolo e foi **o mesmo para os 420 runs do
`gpt-5-mini`**; elevá-lo só para o Sonnet 5 quebraria a comparabilidade entre
os modelos, que é justamente o que AD-25 existe para medir. O truncamento passa
a ser **resultado reportado**, não defeito corrigido: sob o teto de saída do
protocolo, o Sonnet 5 produz suítes incompletas com frequência apreciável.

**Perda conhecida.** `A4/boltons/rep-1` ficou gravado como `aborted_budget`,
que é status final — não é reexecutado sob o teto novo. Essa célula fica com
n=2. Registrado aqui em vez de corrigido, porque editar o JSONL para forçar a
reexecução violaria AD-08.

#### Correção de 2026-08-30 (segunda) — desenho desbalanceado em A4

A projeção acima, de US\$ 64,38, usava a **média geral** dos runs e estava
errada: o custo é dominado por A4. Com 35 runs reais medidos:

| Arquitetura | n | média/run |
|---|---|---|
| A2 | 21 | US\$ 0,2903 |
| A4 | 14 | **US\$ 1,0311** (3,6x) |

Como os runs pendentes eram justamente os caros (30 de A2 contra 37 de A4), a
projeção correta subiu para **US\$ 76,89** — acima até do teto revisado de
US\$ 65. O disjuntor abortaria com ~2/3 dos pendentes feitos, e a ordem de
execução decidiria quais células ficariam vazias.

**Decisão do autor: desenho desbalanceado por arquitetura.** A2 mantém
**3 repetições** nos 17 projetos; A4 cai para **2 repetições**. Corta-se apenas
a 3ª repetição de A4, que é o item caro, e a projeção volta para ~US\$ 59,
dentro do teto de US\$ 65 **sem nova revisão de orçamento**. O ganho decisivo é
de cobertura do desenho: antes desta mudança, 6 dos 17 projetos
(`django-treebeard`, `graphique`, `k0dasm`, `lanhu-mcp`, `pySwitchbot`,
`regextokens`) não tinham **nenhuma** observação de A4. Consequência declarada:
o teste pareado A2xA4 de AD-23 roda com n=3 de um lado e n=2 do outro.

**`boltons` excluído de A4.** As repetições 1 e 2 abortaram por teto por run a
US\$ 1,87 e US\$ 2,05 — ou seja, elevar o teto de 1,50 para 2,00 na correção
anterior **não resolveu**: a rep-2 custou mais que a rep-1 e continuou sem
gerar observação, gastando ~US\$ 3,90 no total sem retorno. A estimativa de que
o run terminaria por ~US\$ 1,90 estava errada. `boltons` é o maior projeto do
benchmark (238 077 tokens de prefixo). A célula `boltons` x A4 fica declarada
como **excedeu o teto de custo por run**, o que é em si um resultado sobre o
eixo de custo que o trabalho mede, e não uma lacuna acidental. O teto por run
permanece em US\$ 2,00: não foi elevado de novo porque não há evidência de que
US\$ 3,00 bastaria.

---

### AD-26 — Nível de esforço de raciocínio do Sonnet 5 fixado em `low`

**Status:** Aceita (autor, 2026-08-28)

**Contexto.** O pré-requisito (4) de AD-25 alertava que o teto de saída de
AD-19 poderia truncar um modelo que raciocina por padrão. O smoke run de
2026-08-28 mostrou que o problema é bem pior que truncagem parcial.

Medido no prompt real do generator, `max_tokens = 16384`:

| Configuração | Saída | Parou por | Código produzido |
|---|---:|---|---:|
| `adaptive` (default do modelo) | 16.384 | teto | **0 caracteres** |
| `adaptive`, teto de 32.768 | 32.768 | teto | 10.200 ch (incompleto) |
| `thinking: disabled` | 5.884 | fim | 15.899 ch |
| `effort: medium` (graphique / nox) | 8.995 / 16.384 | fim / **teto** | ok / truncado |
| `effort: low` (graphique / nox) | 6.578 / 9.967 | fim | ok / ok |

No default, **o modelo gasta o teto inteiro pensando e devolve texto vazio**.
O generator retorna string vazia, a suite não existe, e o run só sobrevive se o
laço de reparo compensar na chamada seguinte — o que A1 e A3 não têm. Rodar a
coleta assim faria o Sonnet 5 parecer catastroficamente pior que o
`gpt-5-mini`, inteiramente por artefato do teto. É a quarta falha silenciosa
desta classe no projeto (v3.0 mutmut, v3.2 inferência de alvo, AD-24 build).

**Decisão.** `REASONING_EFFORT = {"claude-sonnet-5": "low"}` em `config.py`,
aplicado por `AnthropicProvider._params` como `output_config.effort` — logo,
idêntico nos caminhos síncrono e batch. `MAX_OUTPUT_TOKENS` **não** é alterado.

**Justificativa.** Das três saídas possíveis, é a única que preserva a
constante experimental de AD-19 e, com ela, a comparabilidade com a coleta de
agosto. Elevar o teto mudaria a condição sob a qual o `gpt-5-mini` foi medido;
desligar o raciocínio transformaria o Sonnet num modelo sem raciocínio,
enquanto o `gpt-5-mini` gastou 2,4M de tokens raciocinando. `low` mantém o
raciocínio ligado e coube em todos os portes testados.

**Ameaça à validade, a declarar no texto.** O `gpt-5-mini` rodou no default da
OpenAI (`reasoning_effort` = medium) e o Sonnet 5 roda em `low`: o nível
nominal de raciocínio **não é idêntico entre os dois modelos**, o que tensiona
a regra 3 de AD-07. Não existe configuração em que fiquem idênticos — as duas
APIs não expõem os mesmos controles, e o default do Sonnet produz saída vazia.
A escolha é qual confundidor aceitar, e este é o que deixa intactas as
constantes que governam a coleta já publicada. Consequência conservadora: se o
Sonnet 5 tiver desempenho **inferior**, parte pode ser atribuída ao esforço
menor; se tiver desempenho **igual ou superior**, a conclusão é robusta, porque
foi obtida com menos raciocínio que o comparador.

---

## Histórico de versões

| Versão | Data | Autor | Mudanças |
|--------|------|-------|----------|
| 3.4 | 2026-08-24 | Guilherme | **A investigação de por que A3 falha não achou defeito em A3 — achou um no benchmark.** Fisher exato entre A1 (5/105) e A3 (1/105) dá **p = 0,62**: a diferença é ruído, e a etapa de análise de A3 nem ajuda nem atrapalha. O que separa os braços que convergem dos que não convergem é o **repair loop** (A2 vs A1, p = 9,3e-12), não a topologia de agentes. A hipótese anterior de "defeito de prompt do multi-agente" fica **refutada**. **AD-24 (Proposta):** quatro projetos — `tnz`, `solvOR`, `fabric-rti-mcp`, `reviewable-html-workbench` — deram zero convergências em todos os braços e todas as repetições, 80 runs (19% da coleta). Causa em três deles: `docker/Dockerfile.base:13` silencia a falha de `pip install -e .` com `|| true`, e a imagem sobe sem o pacote sob teste (`versioningit` e `setuptools_scm` derivam versão do git e a cópia do benchmark não tem `.git`; `solvOR` é extensão Rust via `maturin`). No quarto, AD-05 rejeita `socket`/`subprocess` que o projeto exige. Sobre os 17 projetos saudáveis a convergência de A2 sobe de 53% para **66%** e a de A4 de 42% para **52%**; A2×A4 e o Wilcoxon ficam inalterados. **AD-25 (Proposta):** segunda rodada em Claude Sonnet 5, só A2 e A4, para replicar a conclusão central num segundo modelo — **R$ 219, levando o projeto a R$ 270 contra o teto de R$ 200 de AD-20**. Quatro pré-requisitos bloqueantes, o mais sério sendo que o `cost_tracker` não cobra cache-write da Anthropic (1,25× a entrada) e o disjuntor de AD-09 não veria esse gasto. |
| 3.3 | 2026-08-17 | Guilherme | **Coleta encerrada: 420/420 runs, US$ 9,49** — 27% do teto de US$ 35 de AD-09 e bem dentro dos R$ 200 de AD-20. Duas decisões novas sobre o tratamento dos dados. **AD-22:** dos 26 runs em `timeout`, 25 têm suite verde e cobertura medida — convergiram, e só a mutação estourou os 1800 s. Reclassificados **na análise**, sem recoletar e sem reescrever o JSONL; convergências vão de 81 para 106 (+31%), CPKM fica com 77 observações. O viés previsto no fim de AD-21 se confirmou na direção esperada: A2 = 14, A4 = 9, A1 = 2. **AD-23:** A1 converge em 3% (n=3) e A3 em 1% (n=1), sem massa para CCP, CPKM ou Wilcoxon pareado. Não é defeito — as 204 falhas dos dois braços têm todas suite vermelha. O teste estatístico passa a ser **A2×A4**; A1/A3 entram pela taxa de convergência e pelos motivos de falha. Registrado também o custo do guarda de AD-05: ~44 runs perdidos por `import proibido: socket`/`subprocess`. |
| 1.0 | 2026-06-22 | Guilherme | Versão inicial. AD-01 a AD-14 registradas. Três itens pendentes de validação com orientador. |
| 2.0 | 2026-06-23 | Guilherme | AD-04 fechada (Docker efêmero por run). AD-07 fechada (N_REPAIR=3, N_REFLEXION=2). AD-09 fechada com tetos por tier (frontier $2,00; cost $1,00) e global ($600,00), derivados de reestimativa de custo. AD-03 e AD-06 promovidas a Aceita, com valores concretos de timeout. Adicionada seção de reestimativa de custo (esperado ~$315 standard, ~$157 com Batch API). |
| 2.1 | 2026-06-23 | Guilherme | AD-15 adicionada: estratégia de contenção de custo via Batch API + prompt caching (Opção A), com projeção de ~$122 mantendo escopo completo, contingência de redução de repetições e rejeição explícita de tiers gratuitos por ameaça à reprodutibilidade. Teto global de AD-09 reduzido para $250 sob AD-15. |
| 3.2 | 2026-08-16 | Guilherme | **Correção da inferência de alvos, que invalidava 7 dos 22 projetos.** O primeiro disparo real parou em 21/440 runs: todo run convergido perdia a mutação com `0 files mutated`. Causas em `project_loader._infer_cov_target`, que adivinhava pelo nome da pasta — (1) `source_paths` com separador do Windows, ilegível para o mutmut no container Linux; (2) a cópia para `/work/src/pkg` não sombreava o pacote instalado, o que faria **todos** os mutantes sobreviverem em silêncio. Um terceiro defeito, anterior a AD-21: `--cov=<nome-da-pasta>` não é importável em layout `src/`, e 14 dos 21 runs coletados estavam sem cobertura alguma — CCP e CPKM vazios. A inferência passou a ler o empacotamento declarado em `pyproject.toml`/`setup.cfg` e a devolver pares `(origem, destino)` em POSIX, com destino na raiz do workdir. Criado `scripts/check_benchmark_targets.py` (21/22 PASS, sem API) como pré-requisito de disparo. **`Waza` excluído da coleta** por não ter pacote importável: 420 runs em vez de 440, a registrar como desvio de AD-16 e ameaça à validade. Custo do disparo abortado: US$ 0,42. |
| 3.1 | 2026-08-16 | Guilherme | **AD-21 destravada pela opção (A), validada empiricamente e implementada.** O risco que a bloqueava — `import` resolver para `/project` em vez da cópia mutada, produzindo zero mutantes mortos em silêncio — foi descartado por teste com marcador injetado: o `cwd` do container vence o install editável no `sys.path`. Execução completa no `codeindex` deu `mutation_score = 0,372` (781 mortos de 2.099 significativos), com `--read-only` intacto. `TestExecutor` ganhou `project_root` + `_materialize_sources`; `ExecutionResult` ganhou `mutation_error`, para que ausência de métrica de mutação nunca mais saia indistinguível de "não tentou". Tempo de mutação medido pela primeira vez: **366 s** contra 3,1 s de pytest, num projeto de LOC mediano — a mutação passa a dominar o cronograma (16–24 h estimadas para os 440 runs). Risco novo registrado e **sem decisão**: `MUTMUT_TIMEOUT_SEC = 1800` enviesa o CPKM contra suites de alta cobertura, que executam mais mutantes e têm mais chance de estourar. |
| 3.0 | 2026-08-14 | Guilherme | **AD-21 registrada como PROPOSTA: o mutmut não roda sob o filesystem read-only de AD-04**, e por isso o CPKM não tem uma única observação. Descoberto no piloto (4 arquiteturas × `codeindex` × rep-1): `mutation_score` veio `null` nos quatro runs, inclusive no A2 que convergiu. Diagnóstico direto no container aponta `OSError: [Errno 30] Read-only file system: '/project/codeindex/analyze.py'` — o mutmut 3.6 escreve os mutantes ao lado do fonte, e `/project` é read-only por AD-04. A falha é silenciosa: `parse_mutmut_stats` devolve `None` e o run é gravado como `completed`, de modo que os 440 runs produziriam um dataset sem CPKM e sem nenhum erro no log. Quatro opções levantadas, nenhuma escolhida. Registrado também o resultado favorável de AD-17b: sob batch com pool 8, o cache ficou em 95,5% (A4) a 99,9% (A2), acima dos 87% medidos em jobs sequenciais — a coalescência entre runs ajuda o cache em vez de atrapalhar. Custo do piloto US$ 0,062 contra US$ 0,164 projetados. |
| 2.9 | 2026-08-14 | Guilherme | AD-11 corrigida: **403 era retentável e divergia entre os dois caminhos de classificação**. `_classify_status` (caminho síncrono, por status HTTP) tratava 403 como `NetworkTimeout` com 3 tentativas e backoff, enquanto `_classify_batch_error` (caminho batch, pelo corpo do erro) já classificava o mesmo `permission_error` como `FatalRequestError`. Chave sem permissão falhava na hora por um caminho e rendia três tentativas pelo outro. 403 entrou no conjunto fatal, com teste pinando a CONVERGÊNCIA entre os dois classificadores — a falha não foi um valor errado e sim duas classificações independentes da mesma condição. Registrado como não decidido o mesmo tratamento para 404 (typo em `--model`, o erro mais provável em operação) e 413. |
| 2.8 | 2026-08-12 | Guilherme | AD-08 clarificado: **ratchet do melhor estado**. A redação original era ambígua ("registra o melhor estado alcançado: última versão dos testes") e o pipeline implementava a segunda leitura, o que fazia o A4 registrar o pior momento pelo qual passou. Resolve-se pela primeira: o run é creditado pelo MELHOR estado e cobrado por TODAS as chamadas, inclusive as descartadas — que é o que a justificativa original do AD-08 sustenta. Critério de comparação declarado (convergiu > cobertura de linha > branch > mutation > menos falhas), com empate mantendo o incumbente. Implementado em `architectures/common.py` (`quality_key`, `track_best`, `execute_and_track` como ponto único de execução dos quatro pipelines). `RunRecord` ganha `states_evaluated` e `discarded_regression`, este último métrica publicável do estudo. Declarada a ameaça à validade do efeito "melhor de k", que favorece arquiteturas com mais estados. |
| 2.7 | 2026-08-12 | Guilherme | Recalibração de AD-20 com duas novas medições sobre `benchmark/codeindex` (275k chars ≈ a média do benchmark): A4 em US$ 0,0954 (13 chamadas, 87% de cache) e A1 em US$ 0,0140 (1 chamada, 0% de cache). Com pontos de tamanhos e arquiteturas distintos, a projeção deixa de ser extrapolação: um projeto 2,84x maior custou só 32% a mais, confirmando o domínio do output. Corrigido erro do modelo de cache — a 1ª chamada de um run é sempre fria, então o cache passa a ser derivado por arquitetura, `(n−1)/n × 0,9425`; assim calibrado o modelo reproduz as duas medições (−0,2% e +2,6%). Projeção dos 440 runs revista de US$ 25 para **US$ 18,06** (cache medido) a **US$ 30,43** (sem cache algum), cabendo sob o teto de US$ 35 nos três cenários. Precificado o eixo completo de modelos (os seis custariam US$ 838) e registrado que excluir o Google não é corte viável. Truncagem DESCARTADA como causa das falhas do A4, via novo campo `LLMResponse.truncated` instrumentado nos três provedores. Em contrapartida, o A1 cobriu MAIS que o A4 gastando 6,8x menos no mesmo projeto — evidência direta de que a pendência do AD-08 é distorção medida, não risco teórico. |
| 2.6 | 2026-08-10 | Guilherme | AD-20 adicionada: teto absoluto de R$ 200 (~US$ 36) definido pelo autor. Reprecificação com consumo real de tokens mostrou que o experimento completo custaria US$ 741–1.263, de 5 a 8x a projeção de AD-16. Teto global reduzido de US$ 250 para US$ 35 e teto por run do tier cost de US$ 1,00 para US$ 0,50. Coleta restrita ao `gpt-5-mini` (440 runs, ~US$ 25), preservando integralmente as 4 arquiteturas, os 22 projetos, os tercis e as 5 repetições. A perda da comparação entre modelos é limitação central e depende de endosso do orientador. |
| 2.5 | 2026-08-10 | Guilherme | AD-19 adicionada: tetos de tokens de saída movidos para `config.py` e elevados, após medir que o raciocínio invisível consumia 59–74% do output e truncava o JSON do critic, abortando o loop Reflexion em silêncio e degenerando o A4 em "A3 + repair". Validado empiricamente: `reflexion_iterations` 0 → 1, cobertura de linha 30% → 41,5%, branch 40,4% → 65,7%. Registrado o indício de subestimação da projeção de custo do AD-16. |
| 2.4 | 2026-08-10 | Guilherme | AD-18 adicionada: temperatura única do experimento passa de 0.7 para 1.0, após a API da OpenAI rejeitar 0.7 no GPT-5 mini (só o default 1 é aceito). Unificar preserva a restrição de constante única de AD-07 e evita que a temperatura covarie com o provedor. |
| 2.3 | 2026-08-10 | Guilherme | AD-17 adicionada: mecanismo de execução da Batch API por coalescência entre runs (pool de threads + `BatchCoalescer`), após constatar que AD-15 estava registrada mas não implementada — o custo vinha sendo registrado com viés de 2x para baixo. Registradas as consequências sobre AD-09 (breaker global pode exceder o teto por até `BATCH_POOL_SIZE` runs) e sobre a premissa de cache de AD-15 (taxa sob batch é incógnita e precisa ser medida no piloto pago). Nota de implementação adicionada a AD-15. |
| 2.2 | 2026-07-17 | Guilherme | AD-16 adicionada: composição final do benchmark com os 22 projetos aprovados pelo selector, estratificados por tercis de CC média do pool (7/7/8), commits pinados. Produto cartesiano passa a 2.640 runs; projeção com batch+caching ~$158, sob o teto de $250. Registrada nota de validação em AD-15 sobre o smoke test gratuito (85% de cache hit, acima da premissa de 70%) e a simplificação do `cost_tracker` quanto ao preço de escrita de cache. |
