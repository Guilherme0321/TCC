# Pipeline de Seleção de Benchmark — TCC

Ferramenta de linha de comando para selecionar o benchmark de 15-20 projetos
Python (estratificados em 3 faixas de complexidade ciclomática) usado no
estudo empírico comparativo de arquiteturas LLM-based para geração
automática de testes unitários.

Implementa de forma programática e auditável os critérios de inclusão e
exclusão definidos na Seção 4.4 da Metodologia, a partir de um pool de
candidatos gerado via GitHub Search API.

---

## 1. Pré-requisitos

- Python 3.10 ou superior
- `git` instalado e disponível no PATH
- Conexão à internet (GitHub API + clone de repositórios)
- Espaço em disco livre (cada candidato gera um clone + um venv temporário,
  removidos automaticamente ao final do processamento)

## 2. Instalação

```bash
cd benchmark_selector
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Gerando um Personal Access Token (PAT) do GitHub

O pipeline funciona sem token, mas fica limitado a 60 requisições/hora na
API REST e 10 requisições/minuto na Search API — o que torna a triagem de
30-40 candidatos extremamente lenta (ou inviável, dependendo do volume de
chamadas). Um PAT gratuito eleva esses limites para 5.000 req/hora e 30
req/minuto. Não é necessário nenhum escopo especial: o pipeline só lê dados
públicos.

Passo a passo (token clássico, o mais simples para este caso):

1. Acesse https://github.com/settings/tokens (estando logado na sua conta).
2. Clique em **"Generate new token"** → **"Generate new token (classic)"**.
3. Em **Note**, dê um nome descritivo, ex.: `tcc-benchmark-selector`.
4. Em **Expiration**, escolha um prazo (ex.: 90 dias é suficiente para a
   etapa de seleção de benchmark).
5. **Não marque nenhum escopo/checkbox** na lista de "Select scopes" — para
   ler repositórios públicos e usar a Search API isso não é necessário.
   (Se quiser marcar algo por precaução, `public_repo` também funciona e
   continua sendo apenas leitura de repositórios públicos.)
6. Clique em **"Generate token"** no final da página.
7. Copie o token exibido (começa com `ghp_...`) — ele só é mostrado uma vez.

Como usar o token no pipeline (duas formas, escolha uma):

```bash
# Opção A: variável de ambiente (recomendado — evita o token aparecer no histórico do shell)
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# Opção B: flag --token em cada comando
python cli.py search --token ghp_xxxxxxxxxxxxxxxxxxxx
```

Guarde o token em local seguro; ele não fica salvo em nenhum arquivo do
projeto (o `.gitignore` já exclui `.env`, caso você opte por usar um).

## 4. Uso

O pipeline é dividido em três subcomandos independentes, para permitir
interromper e retomar uma triagem longa sem perder progresso.

### 4.1. `search` — gerar o pool inicial de candidatos

```bash
python cli.py search -n 40
```

Roda múltiplas queries estratificadas (por faixa de estrelas e por tópico)
na GitHub Search API e salva o pool deduplicado em
`output/candidatos_raw.json`. Nenhum critério de inclusão/exclusão é
aplicado nesta etapa — isso é feito em `evaluate`.

### 4.2. `evaluate` — rodar a triagem completa

```bash
python cli.py evaluate
```

Para cada candidato: coleta metadados (estrelas, licença, datas, hash do
commit), clona superficialmente, calcula complexidade ciclomática e LOC via
`radon`, calcula proporção de notebooks, cria um venv isolado, instala
dependências, valida coletabilidade da suíte via `pytest --co`, mede
cobertura inicial via `pytest-cov` (se houver suíte), roda um dry-run de
compatibilidade com `mutmut`, avalia risco de contaminação de dados de
treinamento e risco de dependência de hardware/ambiente, e por fim aplica
todos os critérios de inclusão/exclusão.

Cada resultado é gravado **imediatamente** em `output/results.jsonl` (uma
linha JSON por candidato). Se o processo for interrompido (Ctrl+C, queda de
rede, etc.), basta rodar `evaluate` novamente: candidatos já presentes em
`results.jsonl` são automaticamente pulados.

Flags úteis:

```bash
python cli.py evaluate --input meus_candidatos.json   # usar outra lista de entrada
python cli.py evaluate --keep-artifacts                # não remove clone/venv (debug)
```

Ao final, o relatório (CSV + JSON), o resumo no console e a sugestão de
shortlist são gerados automaticamente.

### 4.3. `report` — regenerar o relatório sem re-rodar a triagem

```bash
python cli.py report
```

Útil para reimprimir o resumo/shortlist a partir de um `results.jsonl`
já existente, por exemplo após editar manualmente algum resultado ou após
uma `evaluate` parcial.

## 5. Saídas geradas

```
output/
├── pipeline.log                  # log completo (DEBUG) de toda a execução
├── candidatos_raw.json           # pool bruto gerado por `search`
├── results.jsonl                 # um resultado por linha (permite retomada)
├── relatorio_benchmark.csv       # relatório final, uma linha por candidato
└── relatorio_benchmark.json      # mesmo conteúdo, em JSON
```

`work/` é o diretório de clones + venvs temporários; é limpo automaticamente
a cada candidato, a menos que `--keep-artifacts` seja usado.

## 6. Ajustando critérios e parâmetros

Todos os limiares (faixas de LOC, faixas de CC, licenças aceitas, janela de
atividade, cutoff de contaminação, queries de busca, timeouts) estão
centralizados em `pipeline/config.py`, com comentários explicando a origem
de cada valor na Metodologia. Edite esse arquivo em vez de alterar a lógica
em `pipeline/criteria.py`.

**Importante:** revise `CONTAMINATION_CUTOFF` antes de rodar a triagem
definitiva. O valor atual (2023-10-01) reflete o cutoff de conhecimento
oficial de GPT-4o mini, o mais antigo entre os 6 LLMs do estudo conforme
verificado em 2026-06 — confirme se a composição de modelos e a
documentação dos provedores não mudaram.

## 7. Limitações conhecidas (documentar nas Ameaças à Validade)

- **Contaminação**: a heurística observa apenas a data de criação do
  repositório e a data do primeiro commit acessível via API; não analisa o
  histórico completo de commits em busca de reescritas estruturais
  posteriores ao cutoff.
- **Risco de ambiente/hardware**: detecção por palavras-chave em arquivos
  de dependência declarados (`requirements.txt`, `pyproject.toml`, etc.);
  não detecta dependências indiretas ou uso de hardware via subprocess.
- **Compatibilidade com mutmut**: é um dry-run de viabilidade de
  configuração (curto, com timeout), não uma medição real de mutation
  score — essa medição ocorre na fase de execução do estudo (TCC II).
- **Instalação de dependências**: best-effort (tenta `requirements*.txt` e
  `pip install -e .`); projetos com gerenciadores de dependência mais
  exóticos (Poetry com lockfile estrito, Conda, etc.) podem falhar na
  instalação e ser reprovados por "suíte não coletável" mesmo que sejam
  candidatos válidos — vale revisar manualmente os reprovados por esse
  motivo antes de descartá-los definitivamente.

## 8. Próximos passos depois do relatório

1. Abrir `output/relatorio_benchmark.csv` e revisar manualmente os
   candidatos aprovados com risco de contaminação sinalizado, e os
   reprovados por motivos "best-effort" (instalação/coletabilidade) que
   possam merecer uma segunda tentativa manual.
2. Conferir se cada faixa de CC tem entre 5 e 7 candidatos aprovados; caso
   contrário, relaxar os filtros de `SEARCH_QUERIES` em `config.py` e gerar
   mais candidatos para aquela faixa específica.
3. Fechar a seleção final de 15-20 projetos (a sugestão de shortlist impressa
   ao final NÃO é definitiva — é apenas um ponto de partida).
