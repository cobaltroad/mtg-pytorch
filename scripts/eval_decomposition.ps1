<#
.SYNOPSIS
    Evaluate the decompose → tag pipeline for a commander.

.DESCRIPTION
    For each mechanic key fired by the commander's oracle text, queries the
    database using the SQL from commander_mechanics.py and prints the cards
    that match — exactly what the deck-building synergy engine would retrieve.

    CONSUMER keys query cards directly (type_line / oracle_text filters).
    PRODUCER keys query via card_abilities.trigger_event (written by tag.py).

.PARAMETER Name
    Commander name — partial, case-insensitive match (e.g. "tyvar", "Syr Konrad").

.PARAMETER Limit
    Max cards to show per key (default: 10).

.PARAMETER Key
    Only evaluate a specific pattern key (e.g. mana_dork, attack_trigger).

.EXAMPLE
    .\scripts\eval_decomposition.ps1 "Tyvar the Bellicose"
    .\scripts\eval_decomposition.ps1 tyvar -Limit 20
    .\scripts\eval_decomposition.ps1 tyvar -Key mana_dork
#>

param(
    [Parameter(Mandatory)][string]$Name,
    [int]$Limit    = 10,
    [string]$Key   = ""
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

# -- Build DATABASE_URL (psycopg2 / plain postgresql://) ----------------------
if (-not $env:DATABASE_URL) {
    if (-not $env:POSTGRES_USER -or -not $env:POSTGRES_PASSWORD -or -not $env:POSTGRES_DB) {
        throw "Set DATABASE_URL or POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB in .env"
    }
    $env:DATABASE_URL = "postgresql://$($env:POSTGRES_USER):$($env:POSTGRES_PASSWORD)@localhost:5432/$($env:POSTGRES_DB)"
}
# Strip asyncpg scheme if inherited from ingest.ps1
$env:DATABASE_URL = $env:DATABASE_URL -replace '^postgresql\+asyncpg://', 'postgresql://'
$env:DATABASE_URL = $env:DATABASE_URL -replace '^postgresql\+psycopg2://', 'postgresql://'

# -- Build argument list ------------------------------------------------------
# Strip any surrounding quotes PowerShell may pass through
$Name = $Name.Trim('"').Trim("'")
$PyArgs = @('-m', 'scripts.eval_decomposition', $Name, '--limit', $Limit)
if ($Key) { $PyArgs += '--key', $Key }

# -- UTF-8 console output (box-drawing characters) ----------------------------
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# -- Run ----------------------------------------------------------------------
Push-Location $IngestDir
try {
    & $VenvPython @PyArgs
    if ($LASTEXITCODE -ne 0) { throw "eval_decomposition.py exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
