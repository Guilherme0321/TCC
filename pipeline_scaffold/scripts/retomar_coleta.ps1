# Retoma a coleta do gpt-5-mini de onde parou (AD-10, AD-20).
#
# Uso, a partir da raiz do repositorio:
#     .\scripts\retomar_coleta.ps1              # retoma em segundo plano
#     .\scripts\retomar_coleta.ps1 -Status      # so mostra o progresso, nao dispara
#     .\scripts\retomar_coleta.ps1 -Parar       # encerra a coleta em andamento
#
# A idempotencia do AD-10 pula runs com status definitivo (completed, failed,
# aborted_budget, timeout) e reexecuta os `crashed`. Rodar duas vezes nao
# duplica trabalho nem gasto -- so nao rode duas instancias ao mesmo tempo.
#
# `Waza` esta fora da lista de proposito: e o unico projeto sem pacote
# importavel, e a coleta vigente e de 420 runs (21 projetos), nao 440.

param(
    [switch]$Status,
    [switch]$Parar
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$pidFile = "output\coleta.pid"
$python = ".venv\Scripts\python.exe"

function Mostrar-Progresso {
    if (-not (Test-Path "output\results.jsonl")) { "Nenhum resultado ainda."; return }
    & $python -c @"
import json
from collections import defaultdict
best = {}
custo = defaultdict(float)
for linha in open('output/results.jsonl', encoding='utf-8'):
    r = json.loads(linha)
    k = r['run_key']
    custo[k] += r.get('cost_usd') or 0
    if k not in best or best[k]['status'] == 'crashed':
        best[k] = r
ok = [r for r in best.values() if r['status'] != 'crashed']
conv = [r for r in ok if r.get('converged')]
cpkm = [r for r in ok if r.get('mutation_score') is not None]
to = [r for r in ok if r['status'] == 'timeout']
print(f'validos      {len(ok)}/420  ({100*len(ok)/420:.0f}%)')
print('custo total  US$ {:.2f}  (teto AD-09: 35.00)'.format(sum(custo.values())))
print(f'convergidos  {len(conv)}   com CPKM: {len(cpkm)}')
print(f'timeouts de mutmut  {len(to)}')
print(f'faltam       {420 - len(ok)} runs')
"@
}

if ($Status) { Mostrar-Progresso; exit 0 }

if ($Parar) {
    if (Test-Path $pidFile) {
        $runnerPid = (Get-Content $pidFile).Trim()
        Stop-Process -Id $runnerPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    "Coleta encerrada."
    Mostrar-Progresso
    exit 0
}

# --- Pre-requisitos ---------------------------------------------------------
# Sem redirecionar stderr: no PS 5.1 isso empacota o WARNING do Docker num
# ErrorRecord e, com ErrorActionPreference=Stop, aborta o script.
docker info --format "{{.ServerVersion}}" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker nao esta no ar. Abra o Docker Desktop e espere subir." -ForegroundColor Red
    exit 1
}

$viva = Get-Process python -ErrorAction SilentlyContinue
if ($viva) {
    Write-Host "Ja existe python rodando (PID $($viva.Id -join ', ')). Rode -Parar antes." -ForegroundColor Red
    exit 1
}

Write-Host "Estado antes de retomar:" -ForegroundColor Cyan
Mostrar-Progresso

# --- Disparo ----------------------------------------------------------------
$projetos = @(
    "aioamazondevices", "boltons", "book-to-skill", "claude-agent-sdk-python",
    "codeindex", "django-treebeard", "f2mc8dasm", "fabric-rti-mcp", "graphique",
    "k0dasm", "lanhu-mcp", "nox", "PlexTraktSync", "pySwitchbot", "regextokens",
    "reviewable-html-workbench", "solvOR", "ssh-audit", "tnz", "voluptuous",
    "waitress"
)
$argList = @("runner.py", "--tier", "cost", "--model", "gpt-5-mini", "--pool", "8", "--execute")
foreach ($p in $projetos) { $argList += "--project"; $argList += $p }

$carimbo = Get-Date -Format "yyyyMMdd-HHmm"
$proc = Start-Process -FilePath $python -ArgumentList $argList `
    -RedirectStandardOutput "output\coleta-$carimbo.log" `
    -RedirectStandardError "output\coleta-$carimbo.err" `
    -WindowStyle Hidden -PassThru
$proc.Id | Out-File -FilePath $pidFile -Encoding utf8

Write-Host ""
Write-Host "Coleta retomada em segundo plano. PID $($proc.Id)" -ForegroundColor Green
Write-Host "Log: output\coleta-$carimbo.log"
Write-Host "Progresso:  .\scripts\retomar_coleta.ps1 -Status"
Write-Host "Encerrar:   .\scripts\retomar_coleta.ps1 -Parar"
