# Composição do Benchmark (AD-16)

Fonte: `benchmark_selector/output/relatorio_benchmark.json` (297 candidatos triados,
22 aprovados). Estratificação por tercis de CC média calculados dentro do pool
aprovado: **baixa** ≤ 3,11 · **média** 3,12 a 4,31 · **alta** ≥ 4,46.

Cada projeto vive em `benchmark/<nome>` no commit pinado abaixo (clonado sem `.git`).
Reconstrução: `git fetch --depth 1 origin <commit>` a partir do repositório de origem.

| Projeto | Repositório | Commit | CC média | LOC | Cobertura inicial | Banda | Risco contaminação |
|---|---|---|---|---|---|---|---|
| pySwitchbot | sblibs/pySwitchbot | 49be3a33 | 2,23 | 6.435 | 57% | baixa | sim |
| PlexTraktSync | Taxel/PlexTraktSync | dbf9fd4e | 2,34 | 6.483 | 56% | baixa | sim |
| voluptuous | alecthomas/voluptuous | 7fec1343 | 2,60 | 1.653 | 90% | baixa | sim |
| f2mc8dasm | mnaberez/f2mc8dasm | de556163 | 2,64 | 2.483 | 83% | baixa | sim |
| graphique | coady/graphique | eaeaa1a9 | 2,66 | 1.251 | 51% | baixa | sim |
| aioamazondevices | chemelli74/aioamazondevices | d74bf216 | 2,90 | 4.042 | 88% | baixa | não |
| nox | wntrblm/nox | 5bd0f835 | 3,11 | 4.200 | 91% | baixa | sim |
| boltons | mahmoud/boltons | 979fa9b6 | 3,12 | 8.381 | 76% | média | sim |
| django-treebeard | django-treebeard/django-treebeard | 8f4db3f6 | 3,22 | 2.415 | 20% | média | sim |
| fabric-rti-mcp | microsoft/fabric-rti-mcp | 3bf57e45 | 3,78 | 2.866 | 62% | média | não |
| waitress | Pylons/waitress | b5906034 | 3,80 | 3.248 | 95% | média | sim |
| k0dasm | mnaberez/k0dasm | ec2a5968 | 4,08 | 2.271 | 86% | média | sim |
| regextokens | odomojuli/regextokens | 1c80404e | 4,08 | 2.229 | 78% | média | sim |
| reviewable-html-workbench | u-ichi/reviewable-html-workbench | 7f67e9c5 | 4,31 | 3.457 | 78% | média | não |
| claude-agent-sdk-python | anthropics/claude-agent-sdk-python | a56450b8 | 4,46 | 9.508 | 88% | alta | não |
| tnz | IBM/tnz | 6f4eefc4 | 4,51 | 10.905 | 19% | alta | sim |
| ssh-audit | jtesta/ssh-audit | 240840f5 | 5,31 | 5.507 | 72% | alta | sim |
| codeindex | scheidydude/codeindex | 0e498214 | 6,55 | 6.323 | 33% | alta | não |
| solvOR | StevenBtw/solvOR | 1dc3b157 | 6,55 | 10.460 | (sem medição) | alta | não |
| book-to-skill | virgiliojr94/book-to-skill | 6ee737d6 | 6,59 | 1.456 | 82% | alta | não |
| Waza | tw93/Waza | 1bc61131 | 8,60 | 5.793 | 57% | alta | não |
| lanhu-mcp | dsphper/lanhu-mcp | aee22e11 | 10,44 | 4.987 | 10% | alta | não |

Distribuição: 7 baixa / 7 média / 8 alta. Produto cartesiano do experimento:
4 arquiteturas × 6 modelos × 22 projetos × 5 repetições = **2.640 runs**.

Notas de validade (AD-16): limiares de banda relativos ao pool; 13/22 projetos com
flag de risco de contaminação (criados antes de 2023-10-01), mantidos e reportados
como ameaça à validade; solvOR sem medição de cobertura baseline no relatório.
