<#
.SYNOPSIS
    Download a training artifact from the Docker host to the local GPU machine.

.DESCRIPTION
    Fetches either mtg_dataset.pt (co-occurrence path, default) or
    mtg_dataset_compositional.pt (compositional path) from the API and saves
    it to ingest_cache/.

    The trainer uses it with:
        .\scripts\run.ps1 -Train 1
        .\scripts\run.ps1 -Train 1 -TrainingPath compositional

.PARAMETER TrainingPath
    Which artifact to download: 'cooccurrence' (default) or 'compositional'.

.PARAMETER DatasetUrl
    Override the download URL.  Defaults to https://<API_HOST>/dataset/download
    (or /dataset/compositional/download) read from .env.

.PARAMETER OutputDir
    Local directory to save the file (default: .\ingest_cache).

.EXAMPLE
    .\scripts\download_dataset.ps1

.EXAMPLE
    .\scripts\download_dataset.ps1 -TrainingPath compositional
#>

param(
    [ValidateSet('cooccurrence', 'compositional')]
    [string]$TrainingPath = 'cooccurrence',
    [string]$DatasetUrl   = "",
    [string]$OutputDir    = ""
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
    if ($TrainingPath -eq 'compositional') {
        $DatasetUrl = "https://$apiHost/dataset/compositional/download"
    } else {
        $DatasetUrl = "https://$apiHost/dataset/download"
    }
}

$artifactName = if ($TrainingPath -eq 'compositional') {
    'mtg_dataset_compositional.pt'
} else {
    'mtg_dataset.pt'
}
$OutputPath = Join-Path $OutputDir $artifactName

# -- Fetch metadata (fast, shows what we're about to download) ----------------

$infoUrl = $DatasetUrl -replace '/download$', '/info'
try {
    Write-Host "Checking artifact metadata at $infoUrl..."
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
Write-Host "  .\scripts\run.ps1 -Train 1 -TrainingPath $TrainingPath" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Train 2 -TrainingPath $TrainingPath" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Train 3 -TrainingPath $TrainingPath" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Train 4 -TrainingPath $TrainingPath" -ForegroundColor Yellow
