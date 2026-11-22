param(
    [Parameter(Position=0)]
    [ValidateSet("search", "evaluate", "report", "reclassify", "build", "shell")]
    [string]$Command = "evaluate",

    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ExtraArgs
)

# Token: lido de env/.env (key=value, uma linha GITHUB_TOKEN=...) ou de $env:GITHUB_TOKEN.
# Arquivo env/.env eh gitignored e nunca eh logado nem montado no container --
# apenas o VALOR vai como variavel de ambiente para o container.
function Read-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return @{} }
    $map = @{}
    foreach ($line in Get-Content $Path) {
        $trim = $line.Trim()
        if (-not $trim -or $trim.StartsWith("#")) { continue }
        $eq = $trim.IndexOf("=")
        if ($eq -lt 1) { continue }
        $key = $trim.Substring(0, $eq).Trim()
        $val = $trim.Substring($eq + 1).Trim().Trim('"').Trim("'")
        $map[$key] = $val
    }
    return $map
}

$dotenv = Read-DotEnv (Join-Path $PSScriptRoot "env/.env")
$token = $dotenv["GITHUB_TOKEN"]
if (-not $token) { $token = $env:GITHUB_TOKEN }

$needsToken = $Command -notin @("build", "shell", "report", "reclassify")
if ($needsToken -and (-not $token -or $token -eq "cole_seu_token_aqui")) {
    Write-Host "Token nao encontrado. Crie env/.env a partir de env/.env.example e coloque seu PAT em GITHUB_TOKEN=..." -ForegroundColor Yellow
    exit 1
}

$image = "benchmark-selector:sandbox"
$repoRoot = $PSScriptRoot

if ($Command -eq "build") {
    docker build -t $image $repoRoot
    exit $LASTEXITCODE
}

# Verifica se imagem existe; se nao, faz build.
$exists = docker images -q $image
if (-not $exists) {
    Write-Host "Imagem nao encontrada -- construindo..." -ForegroundColor Cyan
    docker build -t $image $repoRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# Argumentos do container:
#  --rm                  : remove container ao terminar
#  --network bridge      : rede padrao (pip/git precisam de internet)
#  --read-only           : sistema de arquivos do container eh read-only
#  --tmpfs /tmp          : /tmp gravavel mas em memoria (sumiu ao parar)
#  --cap-drop ALL        : sem capabilities Linux
#  --security-opt no-new-privileges : evita escalada
#  --pids-limit 2048     : limite de processos (evita fork bomb; 2048 porque
#                          512 causava "cannot fork()" em clones grandes)
#  --memory 4g           : limite de RAM
#  --cpus 4              : limite de CPU
#  Volumes:
#    output/ : leitura+escrita (resultados persistem no host)
#    work/   : tmpfs interno (descartavel; nao vaza para host)
# -i (sem -t): mantem STDIN aberto mas nao exige TTY, permitindo rodar tanto
# interativamente quanto em background/CI (onde nao ha terminal e -t falha com
# "the input device is not a TTY").
$dockerArgs = @(
    "run", "--rm", "-i",
    "--read-only",
    # /tmp em 2g (era 512m): ensurepip/pip descompactam wheels aqui a cada
    # venv criado; em runs longos (80+ candidatos) o tmpfs de 512m esgotava por
    # volta do 40o candidato e TODOS os venvs seguintes falhavam com
    # "ensurepip ... exit status 1". PIP_NO_CACHE_DIR evita acumular cache.
    "--tmpfs", "/tmp:rw,size=2g,uid=1000,gid=1000",
    "--tmpfs", "/app/work:rw,size=8g,exec,uid=1000,gid=1000",
    "--cap-drop=ALL",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "2048",
    "--memory", "4g",
    "--cpus", "4",
    "-v", "${repoRoot}/output:/app/output",
    "-e", "GITHUB_TOKEN=$token",
    "-e", "PIP_NO_CACHE_DIR=1"
)

if ($Command -eq "shell") {
    $dockerArgs += @("--entrypoint", "/bin/bash", $image)
} else {
    $dockerArgs += @($image, $Command)
    if ($ExtraArgs) { $dockerArgs += $ExtraArgs }
}

Write-Host "Rodando em sandbox Docker (read-only, sem privilegios, work descartavel)..." -ForegroundColor Cyan
& docker @dockerArgs
exit $LASTEXITCODE
