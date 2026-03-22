<#
.SYNOPSIS
    Download the training artifact from the Docker host to the local GPU machine.

.DESCRIPTION
    Fetches mtg_dataset.pt from the API's /dataset/download endpoint and saves
    it to the local ingest_cache/ directory.  The trainer uses it with:

        .\scripts\run.ps1 -Mode train -Phase 2 -Dataset .\ingest_cache\mtg_dataset.pt

.PARAMETER DatasetUrl
    Full URL to the /dataset/download endpoint.
    Defaults to https://<API_HOST>/dataset/download (read from .env).

.PARAMETER OutputDir
    Local directory to save the file (default: .\ingest_cache).

.EXAMPLE
    .\scripts\download_dataset.ps1

.EXAMPLE
    .\scripts\download_dataset.ps1 -DatasetUrl https://edh-api.cardtrak.app/dataset/download
#>

param(
    [string]$DatasetUrl = "",
    [string]$OutputDir  = ""
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

# ── Resolve output directory ──────────────────────────────────────────────────

if (-not $OutputDir) {
    $OutputDir = Join-Path $RepoRoot 'ingest_cache'
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputPath = Join-Path $OutputDir 'mtg_dataset.pt'

# ── Resolve download URL ──────────────────────────────────────────────────────

if (-not $DatasetUrl) {
    # Load .env to find API_HOST
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
        Write-Warning "API_HOST not set in .env — defaulting to $apiHost"
    }
    $DatasetUrl = "https://$apiHost/dataset/download"
}

# ── Fetch metadata first (fast, shows what we're about to download) ───────────

$infoUrl = $DatasetUrl -replace '/download$', '/info'
try {
    Write-Host "Checking artifact metadata at $infoUrl…"
    $info = Invoke-RestMethod -Uri $infoUrl -TimeoutSec 10
    $sizeMb = [math]::Round($info.size_bytes / 1MB, 0)
    $created = $info.created_at.Substring(0, 19).Replace('T', ' ')
    Write-Host ""
    Write-Host "  Cards:            $($info.card_count.ToString('N0'))" -ForegroundColor Cyan
    Write-Host "  Training pairs:   $($info.synergy_count.ToString('N0'))" -ForegroundColor Cyan
    Write-Host "  Decks:            $($info.deck_count.ToString('N0'))" -ForegroundColor Cyan
    Write-Host "  Phase 4 positions:$($info.position_count.ToString('N0'))" -ForegroundColor Cyan
    Write-Host "  Model:            $($info.model)" -ForegroundColor Cyan
    Write-Host "  Size:             $sizeMb MB" -ForegroundColor Cyan
    Write-Host "  Created:          $created UTC" -ForegroundColor Cyan
    Write-Host ""
} catch {
    Write-Warning "Could not fetch metadata ($infoUrl): $_"
    Write-Host "Proceeding with download anyway…"
    Write-Host ""
}

# ── Download ──────────────────────────────────────────────────────────────────

Write-Host "Downloading $DatasetUrl"
Write-Host "  → $OutputPath"
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
    Write-Host "Train with:" -ForegroundColor Yellow
    Write-Host "  .\scripts\run.ps1 -Mode train -Phase 1 -Dataset .\ingest_cache\mtg_dataset.pt" -ForegroundColor Yellow
    Write-Host "  .\scripts\run.ps1 -Mode train -Phase 2 -Dataset .\ingest_cache\mtg_dataset.pt" -ForegroundColor Yellow
    Write-Host "  .\scripts\run.ps1 -Mode train -Phase 3 -Dataset .\ingest_cache\mtg_dataset.pt" -ForegroundColor Yellow
    Write-Host "  .\scripts\run.ps1 -Mode train -Phase 4 -Dataset .\ingest_cache\mtg_dataset.pt" -ForegroundColor Yellow
} catch {
    $sw.Stop()
    Write-Error "Download failed: $_"
    exit 1
}
