# Plano de Implementação Faseado

Ordem obrigatória. Cada fase termina com seus testes passando (`pytest tests/ -x`)
antes de iniciar a próxima. Referências AD-NN apontam para o ADR (fonte da verdade).

## Fase 0 — Fundação (sem chamadas de API)  ✅

- [x] `schemas/`: `CodeAnalysis`, `GeneratedTests`, `ExecutionResult`, `CriticFeedback`,
      `RunRecord` (AD-02). RunRecord inclui: chave de idempotência, status, custo,
      tokens in/out, versão de prompt por agente, métricas, `converged`, `failure_reason`.
- [x] `infra/ast_guard.py`: análise estática do AST (AD-05) + contador de rejeições.
- [x] `infra/result_writer.py`: escrita JSONL única ao final, `try/finally` para
      crash (AD-12); leitura de chaves definitivas para retomada (AD-10).
- [x] `infra/cost_tracker.py`: tokens→USD com fatores de batch e cache (config),
      breaker por run e global (AD-09).
- [x] Testes unitários de tudo acima (sem rede, sem Docker).

## Fase 1 — Execução isolada  ✅

- [x] `docker/Dockerfile.base`: imagem base parametrizável por projeto (AD-04).
- [x] `scripts/build_images.py`: constrói uma imagem por projeto do benchmark,
      com venv e dependências pré-instaladas (rede desligada no run, AD-04).
- [x] `infra/test_executor.py`: ciclo de vida do container efêmero, pytest-cov,
      mutmut, timeouts de AD-06, parse dos resultados para `ExecutionResult`.
      Atenção: usar campos de config atuais do mutmut (`source_paths`,
      `pytest_add_cli_args_test_selection`); tratar deprecation warning como
      warning, nunca como falha. (Confirmado mutmut 3.6; parsers unit-testados.)
- [x] `infra/project_loader.py`: extração de código-fonte (excluindo testes),
      metadados radon, truncagem por limite de contexto do modelo.
- [x] Teste de integração com um projeto-fixture mínimo local (LocalRunner +
      pytest-cov reais em `tests/fixtures/sample_project`).

## Fase 2 — Cliente LLM  ✅

- [x] `infra/llm_client.py`: Strategy por provedor (anthropic/openai/google),
      Batch API + prompt caching sempre ativos (AD-15) com código-fonte no
      prefixo cacheável, retry/backoff de AD-11, contabilização de tokens
      reais (incl. cache hits) repassada ao cost_tracker.
      Nota 2026-08-10: a Batch API estava apenas MODELADA no cost_tracker
      (`batch=True` aplicava −50% sobre chamadas síncronas a preço cheio, com
      viés de 2x para baixo no custo registrado). Passou a ser efetivamente
      usada: `submit_batch`/`poll_batch` nos três provedores + `BatchCoalescer`
      (`infra/batch.py`), que agrupa chamadas de runs concorrentes num único
      job. Mecanismo e consequências registrados em AD-17. Exercitado por
      dublê (23 testes); ainda NÃO validado contra API real.
- [x] Carregador de prompts versionados com hash no RunRecord (AD-13).
- [ ] Smoke test com 1 chamada real no modelo mais barato (gpt-5-mini),
      SOMENTE mediante confirmação do usuário. → `scripts/smoke_run.py` pronto
      (gated por `--yes`); execução real PENDENTE de confirmação.
      Nota 2026-07-17: smoke GRATUITO (dev-free, llama 3.2 via Docker Model
      Runner) executado com A1 e A4 sobre `benchmark/book-to-skill` em Docker:
      infraestrutura validada ponta a ponta (5 chamadas LLM, repair loop até
      N_REPAIR, pytest-cov em container, RunRecord AD-08 correto, 85% de
      cache hit no prefixo estável). Custo $0; nada gravado no JSONL oficial.

## Fase 3 — Agentes  ✅

- [x] `agents/generator.py` (todas as arquiteturas).
- [x] `agents/analyzer.py` (A3, A4).
- [x] `agents/repair.py` (A2, A4) — input traceback, output testes que rodam.
- [x] `agents/critic.py` (A4) — output APENAS feedback textual estruturado,
      nunca código (AD-14, garantido pelo schema).
- [x] Prompts v1 em `prompts/` refinados (código no prefixo cacheável, AD-15).
- [x] Testes com respostas de LLM mockadas.

## Fase 4 — Pipelines A1 → A4 (nesta ordem)  ✅

- [x] `architectures/a1.py`: baseline linear.
- [x] `architectures/a2.py`: A1 + repair loop (`N_REPAIR`), fallback AD-08.
- [x] `architectures/a3.py`: analyzer + generator (complexidade radon por símbolo).
- [x] `architectures/a4.py`: A3 + repair loop + Reflexion loop (`N_REFLEXION`),
      memória filtrada com janela de 2 (AD-03).
- [x] Teste de cada pipeline com LLM mockado, cobrindo: convergência,
      estouro de N, estouro de budget, timeout, crash.

## Fase 5 — Runner e piloto  (código ✅, piloto pendente)

- [x] `runner.py`: produto cartesiano, retomada idempotente (AD-10),
      flag `--tier` (cost roda primeiro, AD-15), teto global (AD-09).
      Dry-run por padrão; `--execute` para chamadas reais; frontier exige
      `--confirm-frontier`. Pool de `BATCH_POOL_SIZE` runs em voo para
      alimentar o coalescer de batch (AD-17); `--pool` ajusta, `--no-batch`
      volta ao serial síncrono. Atenção (AD-17a): com pool > 1 o breaker
      global impede novos runs mas os em voo terminam — o teto pode ser
      excedido por até `BATCH_POOL_SIZE` runs.
- [x] `scripts/smoke_run.py`: 1 run completo end-to-end (gated por `--yes`).
- [x] **Pré-requisito do piloto: Docker + imagens.** Verificado em 2026-08-14:
      daemon 29.1.3 no ar, 22/22 imagens `tcc-*` construídas, nenhuma faltando.
- [ ] **BLOQUEIO: o CPKM não tem uma única observação (AD-21, Proposta).**
      O piloto rodou e os quatro runs vieram com `mutation_score: null`,
      inclusive o A2 que convergiu. Diagnóstico no container:
      `OSError: [Errno 30] Read-only file system: '/project/codeindex/analyze.py'`
      — o mutmut 3.6 escreve os mutantes ao lado do fonte, e `/project` é
      read-only por AD-04. **AD-04 e AD-06 são incompatíveis como implementados.**
      A falha é SILENCIOSA (`test_executor.py:411-412`): rodar os 440 runs assim
      daria um dataset íntegro em tudo menos no CPKM, sem erro nenhum no log.
      Quatro opções em AD-21; nenhuma escolhida — decisão do autor.
- [ ] Piloto: 1 projeto × 4 arquiteturas × 1 modelo barato × 1 repetição,
      mediante confirmação do usuário. Medir consumo real de tokens e taxa
      de cache hit; recalibrar projeção de custo do ADR. → PENDENTE (requer
      chaves de API + projetos em `benchmark/` + confirmação do usuário).
      Medir obrigatoriamente a taxa de cache hit SOB BATCH (AD-17b): os 85%
      do smoke gratuito vieram de chamadas sequenciais e não se transferem —
      itens paralelos podem não compartilhar prefixo cacheado e o TTL pode
      expirar entre jobs. É o número que libera (ou não) o tier frontier.
      Medir também `tokens.reasoning_tokens` / `tokens.output_tokens`: o teto
      de saída é consumido pelo raciocínio invisível dos modelos de reasoning
      (no smoke de 2026-08-10 o gpt-5-mini gastou 2.097 tokens de output num
      `calc.py` trivial). Fração alta nos projetos reais = risco de truncar o
      código de teste antes do fim, exigindo rever o `max_tokens` do request.

## Escopo vigente da coleta (AD-20, 2026-08-10)

Teto rígido de **R$ 200 (~US$ 36)**. `MAX_COST_GLOBAL_USD = 35`.
Coleta restrita ao `gpt-5-mini`: **440 runs** (4 arquiteturas × 22 projetos ×
5 repetições).

Projeção recalibrada em 2026-08-12 com três medições reais (A4 sobre
`voluptuous` e sobre `codeindex`, A1 sobre `codeindex`), modelo validado com
erro de −0,2% e +2,6%: **US$ 18,06** (cache medido) a **US$ 30,43** (pior caso,
sem cache algum) — cabe sob o teto nos três cenários. Custo/run em projeto médio:
A1 US$ 0,0141 · A3 US$ 0,0193 · A2 US$ 0,0365 · A4 US$ 0,0943.

O cache é derivado por arquitetura, não fixo: a 1ª chamada de um run é sempre
fria, então `cache(arch) = (n_chamadas − 1) / n_chamadas × 0,9425`. O A1, com
uma só chamada, opera a 0% de cache — medido.

Eixo de modelos precificado (desenho completo, por modelo): gpt-5-mini US$ 18 ·
gemini-3.5-flash US$ 79 · gemini-3.1-pro US$ 106 · claude-sonnet-4-6 US$ 140 ·
claude-opus-4-8 US$ 233 · gpt-5.5 US$ 264. Os seis: US$ 838. Excluir o Google
NÃO é corte viável — só GPT+Claude custa US$ 653, 19x o teto.

```bash
python runner.py --tier cost --model gpt-5-mini            # dry-run
python runner.py --tier cost --model gpt-5-mini --execute  # coleta real
```

Preservados: 4 arquiteturas, 22 projetos, tercis de CC, 5 repetições, CCP/CPKM,
poder de Wilcoxon (n=22) e Kruskal-Wallis (3 faixas).
Perdido: comparação entre modelos — limitação central, **pendente de endosso do
orientador**.

## Pendências abertas (2026-08-10)

- [x] **AD-08: melhor estado vs. último estado.** RESOLVIDO em 2026-08-12 pelo
      ratchet do melhor estado (ver clarificação no AD-08). O run passa a ser
      creditado pelo melhor estado e cobrado por todas as chamadas.
      `execute_and_track` é o ponto único de execução dos quatro pipelines;
      `RunRecord` ganhou `states_evaluated` e `discarded_regression`.
      A ameaça à validade do efeito "melhor de k" está declarada no ADR e deve
      constar das limitações do texto. Histórico do problema — dois de dois
      runs A4 sobre projetos reais terminaram em `failed`:
      `voluptuous` (2026-08-10, cobertura 41,5%/65,7%, reflexion_iter=1) e
      `codeindex` (2026-08-12, cobertura 24,7%/12,5%, repair_iter=7,
      reflexion_iter=2, `failure_reason: testes não executam`). Em ambos a
      re-geração do Reflexion piorou uma suíte que já passava, e o run foi
      registrado como `failed` porque o pipeline guarda o estado FINAL, não o
      MELHOR. Sem um ratchet, o A4 mede pior que o A3 por artefato do desenho,
      não por mérito da arquitetura — e como AD-20 reduziu a coleta a um único
      modelo, essa distorção incide direto sobre a comparação PRIMÁRIA do
      estudo. Exige decisão do autor sobre a leitura do AD-08.
      Truncagem DESCARTADA como causa (2026-08-12): `LLMResponse.truncated`
      foi instrumentado nos três provedores e a sonda A1 sobre `codeindex` não
      disparou o aviso, gastando 5.765 dos 16.384 tokens do teto.
      A mesma sonda produziu a evidência decisiva — no MESMO projeto e modelo,
      o A1 cobriu 28,7%/20,4% por US$ 0,0140 enquanto o A4 cobriu 24,7%/12,5%
      por US$ 0,0954. A arquitetura mais barata venceu a mais cara gastando
      6,8x menos, o que só é possível porque o pipeline guarda o estado final.
- [x] **Recalibrar a projeção de custo do AD-16.** Feito em AD-20: o
      experimento completo custaria US$ 741–1.263, não US$ 158. Resolvido
      pela restrição de escopo, não pelo aumento de orçamento.
- [x] **403 classificado como retentável.** RESOLVIDO em 2026-08-14: 403 entrou
      em `_FATAL_STATUS`, alinhando `_classify_status` com `_classify_batch_error`
      (que já tratava `permission_error` como fatal). Há teste pinando a
      convergência entre os dois classificadores, não só o código isolado.
      Ver correção em AD-11. Fica em aberto, não decidido, o mesmo tratamento
      para 404 (typo em `--model`) e 413 (payload acima do limite).
- [x] **Cache hit sob batch com pool > 1 (AD-17b).** MEDIDO no piloto de
      2026-08-14, com pool 8 e 4 itens no mesmo job: A1 99,5% · A2 99,9% ·
      A3 49,1% · A4 95,5%. Acima dos 87% de jobs sequenciais — a coalescência
      entre runs AJUDA o cache, porque runs distintos sobre o mesmo projeto
      compartilham o prefixo com o código-fonte. O A3 fica mais baixo por ter
      duas chamadas de prompts diferentes (analyzer + generator).
      Consequência prática: a projeção de custo é conservadora. O piloto custou
      US$ 0,062 contra US$ 0,164 projetados para as mesmas 4 células.

## Critérios de pronto (globais)

- `ruff check` limpo; `pytest tests/` verde.
- Nenhuma constante experimental duplicada fora de `config.py`.
- Nenhum prompt hardcoded.
- RunRecord de qualquer run (inclusive falho) é serializável e completo.
