<#
.SYNOPSIS
    Phase 4 + Phase 5 deck evaluator.

.DESCRIPTION
    Scores all legal candidates with the trained CardEncoder (Phase 4), then
    assembles a legal 99-card deck via the Phase 5 deckbuilding layer.

    Phase 5 requires a PostgreSQL connection (DATABASE_URL env var or .env
    file).  When unavailable it falls back to the Phase 4 autoregressive
    output automatically.

.PARAMETER Commander
    Commander name to generate for.  Partial / case-insensitive match accepted.

.PARAMETER Top
    Number of cards to display per type group (default: 0 = show all).

.PARAMETER Checkpoint
    Override the checkpoint name in .\checkpoints\  (default: phase4_best).

.PARAMETER Dataset
    Override the artifact path (default: .\ingest_cache\mtg_commanders.pt).

.PARAMETER Stats
    Run aggregate metrics over --N random commanders instead of a single deck.

.PARAMETER N
    Number of random commanders for -Stats mode (default: 30).

.PARAMETER NoPhase5
    Skip Phase 5 assembly and print the raw Phase 4 output instead.

.EXAMPLE
    .\scripts\eval_deck.ps1 "Atraxa, Praetors' Voice"

.EXAMPLE
    .\scripts\eval_deck.ps1 "Krenko" -Top 10

.EXAMPLE
    .\scripts\eval_deck.ps1 -Stats -N 50
#>

param(
    [string]$Commander = '',

    [int]$Top = 0,

    [string]$Checkpoint = 'phase4_best',

    [string]$Dataset = '',

    [switch]$Stats,

    [int]$N = 30,

    [switch]$NoPhase5
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

if (-not (Test-Path "$RepoRoot\.venv\Scripts\python.exe")) {
    throw "Missing .venv. Create it with: py -3.12 -m venv .venv"
}

if (-not $Dataset) {
    $Dataset = Join-Path $RepoRoot 'ingest_cache\mtg_commanders.pt'
}

if (-not (Test-Path $Dataset)) {
    Write-Error "Artifact not found: $Dataset`nRun: .\scripts\download_commanders.ps1"
    exit 1
}

$CheckpointFile = Join-Path $RepoRoot "checkpoints\${Checkpoint}.pt"
if (-not (Test-Path $CheckpointFile)) {
    Write-Error "Checkpoint not found: $CheckpointFile`nTrain Phase 4 first: .\scripts\run.ps1 -Train 4"
    exit 1
}

if (-not $Stats -and -not $Commander) {
    Write-Host "Usage: .\scripts\eval_deck.ps1 <CommanderName>"
    Write-Host "       .\scripts\eval_deck.ps1 -Stats [-N 50]"
    exit 0
}

# -- UTF-8 console output (box-drawing characters) ----------------------------
$env:PYTHONUTF8     = '1'
$env:CHECKPOINT_DIR = Join-Path $RepoRoot 'checkpoints'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# -- Load DATABASE_URL from .env (Phase 5 requires DB) ------------------------
if (Test-Path (Join-Path $RepoRoot '.env')) {
    Get-Content (Join-Path $RepoRoot '.env') | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0], $parts[1], 'Process')
        }
    }
}
# Normalize to sync psycopg2 URL
if ($env:DATABASE_URL -and $env:DATABASE_URL.StartsWith('postgresql+asyncpg://')) {
    $env:DATABASE_URL = $env:DATABASE_URL.Replace('postgresql+asyncpg://', 'postgresql://')
} elseif (-not $env:DATABASE_URL -and $env:POSTGRES_USER -and $env:POSTGRES_PASSWORD -and $env:POSTGRES_DB) {
    $env:DATABASE_URL = "postgresql://$($env:POSTGRES_USER):$($env:POSTGRES_PASSWORD)@localhost:5432/$($env:POSTGRES_DB)"
}

Set-Location (Join-Path $RepoRoot 'services\trainer')

$extraArgs = @()
if ($NoPhase5) { $extraArgs += '--no-phase5' }

if ($Stats) {
    & "$RepoRoot\.venv\Scripts\python.exe" -u eval_deck.py `
        --stats `
        --n          $N `
        --checkpoint $Checkpoint `
        --dataset    $Dataset `
        @extraArgs
} else {
    & "$RepoRoot\.venv\Scripts\python.exe" -u eval_deck.py $Commander `
        --top        $Top `
        --checkpoint $Checkpoint `
        --dataset    $Dataset `
        @extraArgs
}

exit $LASTEXITCODE
