<#
.SYNOPSIS
    Download the training artifact from the Docker host to the local GPU machine.

.DESCRIPTION
    Fetches mtg_dataset.pt (Phases 1-2) from the API and saves it to ingest_cache/.
    For Phases 3-4 download the commanders artifact with download_commanders.ps1.

    The trainer uses it with:
        .\scripts\run.ps1 -Train 1
        .\scripts\run.ps1 -Train 2

.PARAMETER DatasetUrl
    Override the download URL.  Defaults to https://<API_HOST>/dataset/download
    read from .env.

.PARAMETER OutputDir
    Local directory to save the file (default: .\ingest_cache).

.EXAMPLE
    .\scripts\download_dataset.ps1
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
    $DatasetUrl = "https://$apiHost/dataset/download"
}

$OutputPath    = Join-Path $OutputDir 'mtg_dataset.pt'
$SidecarPath   = Join-Path $OutputDir 'mtg_dataset.json'

# -- Fetch metadata (fast, shows what we're about to download) ----------------

$infoUrl = $DatasetUrl -replace '/download$', '/info'
$expectedSha = $null
try {
    Write-Host "Checking artifact metadata at $infoUrl..."
    $info = Invoke-RestMethod -Uri $infoUrl -TimeoutSec 10
    $sizeMb  = [math]::Round($info.size_bytes / 1MB, 0)
    $created = $info.created_at.Substring(0, 19).Replace('T', ' ')
    Write-Host ""
    Write-Host "  Cards:             $($info.card_count.ToString('N0'))" -ForegroundColor Cyan
    Write-Host "  Synergy pairs:     $($info.synergy_count.ToString('N0'))" -ForegroundColor Cyan
    Write-Host "  Model:             $($info.model)" -ForegroundColor Cyan
    Write-Host "  Size:              $sizeMb MB" -ForegroundColor Cyan
    Write-Host "  Created:           $created UTC" -ForegroundColor Cyan
    if ($info.sha256) {
        Write-Host "  SHA256:            $($info.sha256)" -ForegroundColor Cyan
        $expectedSha = $info.sha256
    }
    Write-Host ""
    # Save sidecar JSON
    $info | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $SidecarPath
    Write-Host "Sidecar saved → $SidecarPath" -ForegroundColor DarkGray
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

# -- SHA256 verification ------------------------------------------------------

if ($expectedSha) {
    Write-Host "Verifying SHA256..." -NoNewline
    $sha = (Get-FileHash -Algorithm SHA256 -Path $OutputPath).Hash.ToLower()
    if ($sha -eq $expectedSha) {
        Write-Host " OK" -ForegroundColor Green
    } else {
        Write-Host " MISMATCH" -ForegroundColor Red
        Write-Host "  Expected : $expectedSha" -ForegroundColor Red
        Write-Host "  Got      : $sha" -ForegroundColor Red
        Write-Error "SHA256 mismatch - artifact may be corrupted. Re-download and try again."
        exit 1
    }
    Write-Host ""
} else {
    Write-Warning "No SHA256 in metadata - skipping integrity check (re-export artifact to include it)"
    Write-Host ""
}

# -- Usage hint ---------------------------------------------------------------

Write-Host "Train with:" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Train 1" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1 -Train 2" -ForegroundColor Yellow
Write-Host ""
Write-Host "For Phases 3-4 download the commanders artifact:" -ForegroundColor Yellow
Write-Host "  .\scripts\download_commanders.ps1" -ForegroundColor Yellow
