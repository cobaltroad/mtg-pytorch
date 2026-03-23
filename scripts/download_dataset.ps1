<#
.SYNOPSIS
    Download training artifact(s) from the Docker host to the local GPU machine.

.DESCRIPTION
    Fetches mtg_dataset.pt (and optionally mtg_dataset_compositional.pt) from
    the API's /dataset/download endpoints and saves them to ingest_cache/.

    The trainer uses them with:
        .\scripts\run.ps1 -Train 1 -Dataset .\ingest_cache\mtg_dataset.pt
        .\scripts\run.ps1 -Train 1 -TrainingPath compositional

.PARAMETER DatasetUrl
    Full URL to the /dataset/download endpoint.
    Defaults to https://<API_HOST>/dataset/download (read from .env).

.PARAMETER OutputDir
    Local directory to save the file (default: .\ingest_cache).

.PARAMETER Compositional
    Also download mtg_dataset_compositional.pt (from /dataset/compositional/download).

.EXAMPLE
    .\scripts\download_dataset.ps1

.EXAMPLE
    .\scripts\download_dataset.ps1 -Compositional

.EXAMPLE
    .\scripts\download_dataset.ps1 -DatasetUrl https://edh-api.cardtrak.app/dataset/download
#>

param(
    [string]$DatasetUrl   = "",
    [string]$OutputDir    = "",
    [switch]$Compositional
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

# -- Resolve output directory -------------------------------------------------

if (-not $OutputDir) {
    $OutputDir = Join-Path $RepoRoot 'ingest_cache'
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# -- Resolve base API URL from .env -------------------------------------------

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
    $DatasetUrl = "https://$apiHost/dataset/download"
}

# -- Helper: fetch metadata + download one artifact ---------------------------

function Download-Artifact {
    param(
        [string]$Url,
        [string]$OutputPath,
        [string]$Label
    )

    $infoUrl = $Url -replace '/download$', '/info'
    try {
        Write-Host "[$Label] Checking metadata at $infoUrl..."
        $info = Invoke-RestMethod -Uri $infoUrl -TimeoutSec 10
        $sizeMb  = [math]::Round($info.size_bytes / 1MB, 0)
        $created = $info.created_at.Substring(0, 19).Replace('T', ' ')
        Write-Host ""
        Write-Host "  Cards:             $($info.card_count.ToString('N0'))" -ForegroundColor Cyan
        if ($info.functional_pair_count) {
            Write-Host "  Functional pairs:  $($info.functional_pair_count.ToString('N0'))" -ForegroundColor Cyan
        }
        Write-Host "  Synergy pairs:     $($info.synergy_count.ToString('N0'))" -ForegroundColor Cyan
        Write-Host "  Decks:             $($info.deck_count.ToString('N0'))" -ForegroundColor Cyan
        Write-Host "  Phase 4 positions: $($info.position_count.ToString('N0'))" -ForegroundColor Cyan
        Write-Host "  Model:             $($info.model)" -ForegroundColor Cyan
        Write-Host "  Size:              $sizeMb MB" -ForegroundColor Cyan
        Write-Host "  Created:           $created UTC" -ForegroundColor Cyan
        Write-Host ""
    } catch {
        Write-Warning "Could not fetch metadata ($infoUrl): $_"
        Write-Host "Proceeding with download anyway..."
        Write-Host ""
    }

    Write-Host "[$Label] Downloading $Url"
    Write-Host "  -> $OutputPath"
    Write-Host ""

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($Url, $OutputPath)
        $sw.Stop()
        $finalMb = [math]::Round((Get-Item $OutputPath).Length / 1MB, 1)
        $elapsed  = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        Write-Host "[$Label] Downloaded $finalMb MB in ${elapsed}s" -ForegroundColor Green
        Write-Host ""
    } catch {
        $sw.Stop()
        Write-Error "[$Label] Download failed: $_"
        exit 1
    }
}

# -- Download co-occurrence artifact (always) ---------------------------------

$cooccOutputPath = Join-Path $OutputDir 'mtg_dataset.pt'
Download-Artifact -Url $DatasetUrl -OutputPath $cooccOutputPath -Label 'co-occurrence'

# -- Download compositional artifact (opt-in) ---------------------------------

if ($Compositional) {
    $compUrl        = $DatasetUrl -replace '/dataset/download$', '/dataset/compositional/download'
    $compOutputPath = Join-Path $OutputDir 'mtg_dataset_compositional.pt'
    Download-Artifact -Url $compUrl -OutputPath $compOutputPath -Label 'compositional'
}

# -- Usage hint ---------------------------------------------------------------

Write-Host "Train with:" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Train 1   # co-occurrence path" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Train 2" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Train 3" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Train 4" -ForegroundColor Yellow
if ($Compositional) {
    Write-Host ""
    Write-Host "  .\scripts\run.ps1 -Train 1 -TrainingPath compositional   # compositional path" -ForegroundColor Yellow
    Write-Host "  .\scripts\run.ps1 -Train 2 -TrainingPath compositional" -ForegroundColor Yellow
    Write-Host "  .\scripts\run.ps1 -Train 3 -TrainingPath compositional" -ForegroundColor Yellow
    Write-Host "  .\scripts\run.ps1 -Train 4 -TrainingPath compositional" -ForegroundColor Yellow
}
