<#
.SYNOPSIS
    Nearest-neighbour spot-check for a Phase 1 checkpoint.

.DESCRIPTION
    Projects all card embeddings through the trained CardEncoder and prints
    the top-N nearest neighbours for a given card name.  Runs entirely from
    the local artifact — no database connection required.

.PARAMETER Card
    Card name to query.  Partial / case-insensitive match is accepted.

.PARAMETER TrainingPath
    Which checkpoint and artifact to use: 'cooccurrence' or 'compositional'
    (default).  Determines both the checkpoint prefix (phase1 vs comp_phase1)
    and the artifact file (mtg_dataset.pt vs mtg_dataset_compositional.pt).

.PARAMETER Top
    Number of nearest neighbours to display (default 20).

.PARAMETER Phase
    Which phase checkpoint to evaluate against (1–4, default 1).  The encoder
    is projected for nearest-neighbour comparison regardless of phase; later
    phases have a fine-tuned encoder that reflects synergy / deck / generation
    training signal.

.PARAMETER Checkpoint
    Override the checkpoint name.  Defaults to <prefix><phase>_best derived
    from -TrainingPath and -Phase.

.PARAMETER Dataset
    Override the artifact path.  Defaults to
    .\ingest_cache\mtg_dataset[_compositional].pt derived from -TrainingPath.

.EXAMPLE
    .\scripts\eval_neighbors.ps1 "Swords to Plowshares"

.EXAMPLE
    .\scripts\eval_neighbors.ps1 "Beast Whisperer" -Phase 2

.EXAMPLE
    .\scripts\eval_neighbors.ps1 "Llanowar Elves" -Top 30

.EXAMPLE
    .\scripts\eval_neighbors.ps1 "Swords to Plowshares" -TrainingPath cooccurrence -Phase 2
#>

param(
    [Parameter(Mandatory)]
    [string]$Card,

    [ValidateSet('cooccurrence', 'compositional')]
    [string]$TrainingPath = 'compositional',

    [ValidateRange(1, 4)]
    [int]$Phase = 1,

    [int]$Top = 20,

    [string]$Checkpoint = '',
    [string]$Dataset    = ''
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

if (-not (Test-Path "$RepoRoot\.venv\Scripts\python.exe")) {
    throw "Missing .venv. Create it with: py -3.12 -m venv .venv"
}

# -- Resolve defaults from TrainingPath ---------------------------------------

if (-not $Checkpoint) {
    $prefix     = if ($TrainingPath -eq 'compositional') { 'comp_phase' } else { 'phase' }
    $Checkpoint = "${prefix}${Phase}_best"
}

if (-not $Dataset) {
    $artifactName = if ($TrainingPath -eq 'compositional') {
        'mtg_dataset_compositional.pt'
    } else {
        'mtg_dataset.pt'
    }
    $Dataset = Join-Path $RepoRoot "ingest_cache\$artifactName"
}

if (-not (Test-Path $Dataset)) {
    Write-Error "Artifact not found: $Dataset`nRun: .\scripts\download_dataset.ps1 -TrainingPath $TrainingPath"
    exit 1
}

$checkpointFile = Join-Path $RepoRoot "checkpoints\${Checkpoint}.pt"
if (-not (Test-Path $checkpointFile)) {
    Write-Error "Checkpoint not found: $checkpointFile`nTrain Phase 1 first: .\scripts\run.ps1 -Train 1 -TrainingPath $TrainingPath"
    exit 1
}

# -- Run eval -----------------------------------------------------------------

$env:CHECKPOINT_DIR = Join-Path $RepoRoot 'checkpoints'

Set-Location (Join-Path $RepoRoot 'services\trainer')
& "$RepoRoot\.venv\Scripts\python.exe" -u eval_neighbors.py $Card `
    --checkpoint $Checkpoint `
    --dataset    $Dataset `
    --top        $Top

exit $LASTEXITCODE
