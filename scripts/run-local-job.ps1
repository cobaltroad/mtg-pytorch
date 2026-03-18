param(
    [ValidateSet('train', 'ingest')]
    [string]$Mode = 'train',

    [ValidateSet(2, 3, 4)]
    [int]$Phase = 3,

    [ValidateSet('fetch_cards', 'load_cards', 'embed_cards', 'tag_abilities', 'compute_synergy', 'compute_tribal_typeline_synergy', 'all')]
    [string]$Stage = 'compute_synergy',

    [Nullable[int]]$Epochs = $null,
    [Nullable[double]]$LearningRate = $null,

    [Nullable[bool]]$Resume = $null,
    [bool]$FreezeEncoder = $false,
    [double]$EncoderLrScale = 0.1,
    [double]$TempStart = 0.5,
    [double]$TempEnd = 0.05,

    [int]$Sample = 500000,
    [int]$RoleDemandSample = 100000,

    [int]$SynergyLimit = 500000,
    [int]$TribalMemberLimit = 50000
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $RepoRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    throw "Missing .venv. Create it first with: py -3.12 -m venv .venv"
}

if (Test-Path '.env') {
    Get-Content '.env' | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0], $parts[1], 'Process')
        }
    }
}

function Ensure-AsyncDbUrl {
    if ($env:DATABASE_URL) {
        if ($env:DATABASE_URL.StartsWith('postgresql://')) {
            return $env:DATABASE_URL.Replace('postgresql://', 'postgresql+asyncpg://')
        }
        return $env:DATABASE_URL
    }

    if (-not $env:POSTGRES_USER -or -not $env:POSTGRES_PASSWORD -or -not $env:POSTGRES_DB) {
        throw "Set DATABASE_URL or POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB in .env"
    }

    return "postgresql+asyncpg://$($env:POSTGRES_USER):$($env:POSTGRES_PASSWORD)@localhost:5432/$($env:POSTGRES_DB)"
}

function Ensure-SyncDbUrl {
    if ($env:DATABASE_URL) {
        if ($env:DATABASE_URL.StartsWith('postgresql+asyncpg://')) {
            return $env:DATABASE_URL.Replace('postgresql+asyncpg://', 'postgresql://')
        }
        return $env:DATABASE_URL
    }

    if (-not $env:POSTGRES_USER -or -not $env:POSTGRES_PASSWORD -or -not $env:POSTGRES_DB) {
        throw "Set DATABASE_URL or POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB in .env"
    }

    return "postgresql://$($env:POSTGRES_USER):$($env:POSTGRES_PASSWORD)@localhost:5432/$($env:POSTGRES_DB)"
}

$checkpointsDir = Join-Path $RepoRoot 'checkpoints'
$cacheDir = Join-Path $RepoRoot 'ingest_cache'
New-Item -ItemType Directory -Force -Path $checkpointsDir | Out-Null
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

if ($Mode -eq 'train') {
    $env:DATABASE_URL = Ensure-SyncDbUrl
    $env:CHECKPOINT_DIR = $checkpointsDir

    if ($null -eq $Epochs) {
        if ($Phase -eq 2) { $Epochs = 20 }
        elseif ($Phase -eq 3) { $Epochs = 50 }
        else { $Epochs = 50 }
    }

    if ($null -eq $LearningRate) {
        $LearningRate = 1e-4
    }

    if ($null -eq $Resume) {
        if ($Phase -eq 4) { $Resume = $false }
        else { $Resume = $true }
    }

    $cmd = @(
        'train.py',
        '--phase', $Phase,
        '--epochs', $Epochs,
        '--lr', $LearningRate
    )

    if ($Resume) {
        $cmd += '--resume'
    }

    if ($Phase -eq 2) {
        $cmd += @('--sample', $Sample, '--role-demand-sample', $RoleDemandSample)
    }

    if ($Phase -eq 4) {
        if (-not $FreezeEncoder) {
            $cmd += @('--no-freeze-encoder', '--encoder-lr-scale', $EncoderLrScale)
        }
        $cmd += @('--temp-start', $TempStart, '--temp-end', $TempEnd)
    }

    Write-Host "Running trainer with args: $($cmd -join ' ')"
    Set-Location (Join-Path $RepoRoot 'services\trainer')
    & "$RepoRoot\.venv\Scripts\python.exe" @cmd
    exit $LASTEXITCODE
}

$env:DATABASE_URL = Ensure-AsyncDbUrl
$env:EDHREC_CACHE_DIR = $cacheDir
$env:BATCH_SIZE = if ($env:INGEST_BATCH_SIZE) { $env:INGEST_BATCH_SIZE } else { '256' }
$env:SYNERGY_LIMIT = "$SynergyLimit"
$env:TRIBAL_MEMBER_LIMIT = "$TribalMemberLimit"

$ingestCmd = @('pipeline.py')
if ($Stage -ne 'all') {
    $ingestCmd += @('--stage', $Stage)
}

Write-Host "Running ingest with args: $($ingestCmd -join ' ')"
Set-Location (Join-Path $RepoRoot 'services\ingest')
& "$RepoRoot\.venv\Scripts\python.exe" @ingestCmd
exit $LASTEXITCODE
