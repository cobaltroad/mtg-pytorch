<#
.SYNOPSIS
    Phase 4 deck quality evaluator.

.DESCRIPTION
    Autoregressively generates a 99-card Commander deck from a Phase 4
    checkpoint and prints quality metrics: recall against the artifact's
    known positives, color-identity violations, mean pairwise similarity
    (collapse check), and card-type distribution.

    Requires no database connection — loads entirely from the commanders
    artifact and the checkpoint.

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

    [int]$N = 30
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
$env:PYTHONUTF8   = '1'
$env:CHECKPOINT_DIR = Join-Path $RepoRoot 'checkpoints'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location (Join-Path $RepoRoot 'services\trainer')

if ($Stats) {
    & "$RepoRoot\.venv\Scripts\python.exe" -u eval_deck.py `
        --stats `
        --n          $N `
        --checkpoint $Checkpoint `
        --dataset    $Dataset
} else {
    & "$RepoRoot\.venv\Scripts\python.exe" -u eval_deck.py $Commander `
        --top        $Top `
        --checkpoint $Checkpoint `
        --dataset    $Dataset
}

exit $LASTEXITCODE
