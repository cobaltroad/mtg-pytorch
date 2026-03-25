<#
.SYNOPSIS
    Run the ingest pipeline (or a single sub-stage) directly on Windows — no Docker.

.DESCRIPTION
    Activates the repo-root .venv, loads .env, builds DATABASE_URL, then runs
    pipeline.py against the PostgreSQL instance reachable on localhost:5432.

    DATASET_OUTPUT defaults to .\ingest_cache\mtg_dataset.pt so the artifact
    lands where download_dataset.ps1 and run.ps1 expect it.

.PARAMETER Stage
    Which pipeline stage to run.  Omit to run the full pipeline (download + process).

    Grouped stages:
      download       — fetch MTGJSON + load cards + Commander Spellbook combos
      process        — embed + tag + compute synergy edges + export_dataset

    Individual sub-stages:
      embed_cards
      tag_abilities                (add -Rescan to re-apply all patterns)
      compute_textmatch_synergy
      compute_xmage_synergy
      compute_xmage_effect_synergy
      export_dataset
      export_dataset_commanders
      composition_profile

.PARAMETER Rescan
    Pass --rescan to tag_abilities so every pattern is re-applied to every card.
    Only meaningful with -Stage tag_abilities.

.PARAMETER OutputDir
    Override the directory where training artifacts are written.
    Default: .\ingest_cache

.EXAMPLE
    .\scripts\ingest.ps1
    .\scripts\ingest.ps1 -Stage export_dataset
    .\scripts\ingest.ps1 -Stage tag_abilities -Rescan
#>

param(
    [string]$Stage     = "",
    [switch]$Rescan,
    [string]$OutputDir = ""
)

$ErrorActionPreference = 'Stop'
$RepoRoot   = Resolve-Path (Join-Path $PSScriptRoot '..')
$IngestDir  = Join-Path $RepoRoot 'services\ingest'
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    throw "Missing .venv.  Create it first:`n  py -3.12 -m venv .venv`n  .\.venv\Scripts\pip install -r services\ingest\requirements.txt"
}

# -- Load .env ----------------------------------------------------------------
$EnvFile = Join-Path $RepoRoot '.env'
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
        }
    }
}

# -- Build DATABASE_URL (asyncpg for pipeline.py) -----------------------------
if (-not $env:DATABASE_URL) {
    if (-not $env:POSTGRES_USER -or -not $env:POSTGRES_PASSWORD -or -not $env:POSTGRES_DB) {
        throw "Set DATABASE_URL or POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB in .env"
    }
    $env:DATABASE_URL = "postgresql+asyncpg://$($env:POSTGRES_USER):$($env:POSTGRES_PASSWORD)@localhost:5432/$($env:POSTGRES_DB)"
}
# Ensure asyncpg scheme
if ($env:DATABASE_URL -notmatch 'asyncpg') {
    $env:DATABASE_URL = $env:DATABASE_URL -replace '^postgresql://', 'postgresql+asyncpg://'
    $env:DATABASE_URL = $env:DATABASE_URL -replace '^postgresql\+psycopg2://', 'postgresql+asyncpg://'
}

# -- Output directory for artifacts -------------------------------------------
if (-not $OutputDir) { $OutputDir = Join-Path $RepoRoot 'ingest_cache' }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

if (-not $env:DATASET_OUTPUT)    { $env:DATASET_OUTPUT     = Join-Path $OutputDir 'mtg_dataset.pt' }
if (-not $env:COMMANDERS_OUTPUT) { $env:COMMANDERS_OUTPUT  = Join-Path $OutputDir 'mtg_commanders.pt' }
if (-not $env:INGEST_CACHE_DIR)  { $env:INGEST_CACHE_DIR   = $OutputDir }

# -- Check DB reachability ----------------------------------------------------
$dbTest = Test-NetConnection -ComputerName localhost -Port 5432 -WarningAction SilentlyContinue
if (-not $dbTest.TcpTestSucceeded) {
    Write-Warning "PostgreSQL not reachable on localhost:5432.  Start it with: .\scripts\start-db.ps1"
}

# -- Build argument list ------------------------------------------------------
$PyArgs = @('pipeline.py')
if ($Stage)  { $PyArgs += '--stage', $Stage }
if ($Rescan) { $PyArgs += '--rescan' }

# -- Run ----------------------------------------------------------------------
Write-Host ""
if ($Stage) { Write-Host "==> ingest stage: $Stage" -ForegroundColor Cyan }
else        { Write-Host "==> ingest: full pipeline (download + process)" -ForegroundColor Cyan }
Write-Host "    DATASET_OUTPUT   = $env:DATASET_OUTPUT"
Write-Host "    COMMANDERS_OUTPUT= $env:COMMANDERS_OUTPUT"
Write-Host "    DATABASE_URL     = $($env:DATABASE_URL -replace ':[^@]+@', ':***@')"
Write-Host ""

Push-Location $IngestDir
try {
    & $VenvPython @PyArgs
    if ($LASTEXITCODE -ne 0) { throw "pipeline.py exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
