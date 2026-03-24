<#
.SYNOPSIS
    Download the commander training artifact from the Docker host.

.DESCRIPTION
    Fetches mtg_commanders.pt from the API and saves it to ingest_cache/.
    This artifact is used for Phases 3 and 4 of the compositional training
    path — positives are derived from synergy_edges (no human decklists needed).

    Prerequisites (run on the Docker host before downloading):
        docker compose run --rm ingest python pipeline.py --stage export_dataset_commanders

    Train with:
        .\scripts\run.ps1 -Train 3
        .\scripts\run.ps1 -Train 4
    For Phases 1/2, download the main dataset artifact instead:
        .\scripts\download_dataset.ps1

.PARAMETER DatasetUrl
    Override the download URL.  Defaults to https://<API_HOST>/dataset/commanders/download
    read from .env.

.PARAMETER OutputDir
    Local directory to save the file (default: .\ingest_cache).

.EXAMPLE
    .\scripts\download_commanders.ps1

.EXAMPLE
    .\scripts\download_commanders.ps1 -OutputDir C:\ml\data
#>

param(
    [string]$DatasetUrl = "",
    [string]$OutputDir  = ""
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

# -- Resolve output directory -------------------------------------------------

if (-not $OutputDir) {
    $OutputDir = Join-Path $RepoRoot 'ingest_cache'
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# -- Resolve download URL from .env -------------------------------------------

if (-not $DatasetUrl) {
    $envFile = Join-Path $RepoRoot '.env'
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith('#')) { return }
            $parts = $line -split '=', 2
            if ($parts.Count -eq 2) {
                [Environment]::SetEnvironmentVariable($parts[0], $parts[1], 'Process')
            }
        }
    }
    $apiHost = $env:API_HOST
    if (-not $apiHost) {
        $apiHost = 'edh-api.cardtrak.app'
        Write-Warning "API_HOST not set in .env - defaulting to $apiHost"
    }
    $DatasetUrl = "https://$apiHost/dataset/commanders/download"
}

$OutputPath = Join-Path $OutputDir 'mtg_commanders.pt'

# -- Fetch metadata -----------------------------------------------------------

$infoUrl = $DatasetUrl -replace '/download$', '/info'
try {
    Write-Host "Checking artifact metadata at $infoUrl..."
    $info = Invoke-RestMethod -Uri $infoUrl -TimeoutSec 10
    $sizeMb  = [math]::Round($info.size_bytes / 1MB, 0)
    $created = $info.created_at.Substring(0, 19).Replace('T', ' ')
    Write-Host ""
    Write-Host "  Cards:              $($info.card_count.ToString('N0'))" -ForegroundColor Cyan
    Write-Host "  Commanders:         $($info.deck_count.ToString('N0'))" -ForegroundColor Cyan
    Write-Host "  Avg producers:      $($info.avg_positives)" -ForegroundColor Cyan
    Write-Host "  Model:              $($info.model)" -ForegroundColor Cyan
    Write-Host "  Size:               $sizeMb MB" -ForegroundColor Cyan
    Write-Host "  Created:            $created UTC" -ForegroundColor Cyan
    Write-Host ""
} catch {
    Write-Warning "Could not fetch metadata ($infoUrl): $_"
    Write-Host "Proceeding with download anyway..."
    Write-Host ""
}

# -- Download -----------------------------------------------------------------

Write-Host "Downloading $DatasetUrl"
Write-Host "  -> $OutputPath"
Write-Host ""

$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($DatasetUrl, $OutputPath)
    $sw.Stop()
    $finalMb = [math]::Round((Get-Item $OutputPath).Length / 1MB, 1)
    $elapsed  = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    Write-Host "Downloaded $finalMb MB in ${elapsed}s" -ForegroundColor Green
    Write-Host ""
} catch {
    $sw.Stop()
    Write-Error "Download failed: $_"
    exit 1
}

# -- Usage hint ---------------------------------------------------------------

Write-Host "Train with:" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Mode train -Phase 3 -TrainingPath commander -Dataset .\ingest_cache\mtg_commanders.pt" -ForegroundColor Yellow
