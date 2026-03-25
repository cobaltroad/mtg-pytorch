<#
.SYNOPSIS
    Show centroid expansion candidates for a commander.

.DESCRIPTION
    Projects all card embeddings through the trained CardEncoder, computes the
    centroid of the commander's positive set, and prints the top-N colour-legal
    nearest neighbours that are not already in the positive set.

    Runs entirely from the commander artifact and a Phase 2 checkpoint — no
    database connection required.

    If the artifact already contains stored expansion candidates (from running
    .\scripts\run.ps1 -Train 3), a note is shown with the stored count.

.PARAMETER Commander
    Commander name to query.  Partial / case-insensitive match is accepted.

.PARAMETER Top
    Number of expansion candidates to display (default 20).

.PARAMETER Checkpoint
    Override the checkpoint name (default: phase2_best).

.PARAMETER Dataset
    Override the artifact path (default: .\ingest_cache\mtg_commanders.pt).

.EXAMPLE
    .\scripts\eval_commander.ps1 "Sythis, Harvest's Hand"

.EXAMPLE
    .\scripts\eval_commander.ps1 "Anje Falkenrath" -Top 30

.EXAMPLE
    .\scripts\eval_commander.ps1 "Atraxa" -Checkpoint phase2_best -Dataset .\ingest_cache\mtg_commanders.pt
#>

param(
    [Parameter(Mandatory)]
    [string]$Commander,

    [int]$Top = 20,

    [string]$Checkpoint = 'phase2_best',
    [string]$Dataset    = '',
    [switch]$ShowBasis
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

if (-not (Test-Path "$RepoRoot\.venv\Scripts\python.exe")) {
    throw "Missing .venv. Create it with: py -3.12 -m venv .venv"
}

if (-not $Dataset) {
    $Dataset = Join-Path $RepoRoot "ingest_cache\mtg_commanders.pt"
}

if (-not (Test-Path $Dataset)) {
    Write-Error "Artifact not found: $Dataset`nRun: .\scripts\download_commanders.ps1"
    exit 1
}

$checkpointFile = Join-Path $RepoRoot "checkpoints\${Checkpoint}.pt"
if (-not (Test-Path $checkpointFile)) {
    Write-Error "Checkpoint not found: $checkpointFile`nTrain Phase 2 first: .\scripts\run.ps1 -Train 2"
    exit 1
}

$env:CHECKPOINT_DIR = Join-Path $RepoRoot 'checkpoints'

Set-Location (Join-Path $RepoRoot 'services\trainer')
$pyArgs = @($Commander, '--checkpoint', $Checkpoint, '--dataset', $Dataset, '--top', $Top)
if ($ShowBasis) { $pyArgs += '--show-basis' }

& "$RepoRoot\.venv\Scripts\python.exe" -u eval_commander.py @pyArgs

exit $LASTEXITCODE
