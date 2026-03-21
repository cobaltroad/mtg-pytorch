param(
    [ValidateSet('train', 'ingest')]
    [string]$Mode = 'train',

    [ValidateSet(2, 3, 4)]
    [int]$Phase = 3,

    [ValidateSet('fetch_cards', 'load_cards', 'embed_cards', 'tag_abilities', 'compute_synergy', 'compute_commander_value_synergy', 'compute_tribal_typeline_synergy', 'backfill_roles', 'all')]
    [string]$Stage = 'compute_synergy',

    [Nullable[int]]$Epochs = $null,
    [Nullable[double]]$LearningRate = $null,
    [int]$BatchSize = 512,

    [Nullable[bool]]$Resume = $null,
    [bool]$FreezeEncoder = $false,
    [double]$EncoderLrScale = 0.1,
    [double]$TempStart = 0.5,
    [double]$TempEnd = 0.05,

    [int]$Sample = 500000,
    [int]$RoleDemandSample = 100000,
    [int]$ComboSample = 200000,
    [int]$CommanderValueSample = 200000,

    [int]$SynergyLimit = 500000,
    [int]$TribalMemberLimit = 80000,
    [int]$CommanderValueLimit = 200000
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

function Assert-Prerequisites {
    param(
        [string]$mode,
        [int]$phase,
        [System.Nullable[bool]]$resume,
        [string]$checkpointsDir
    )
    $ok = $true
    Write-Host ""
    Write-Host "Pre-flight checks:" -ForegroundColor Cyan

    # ── PostgreSQL reachable? ──────────────────────────────────────────────
    Write-Host -NoNewline "  PostgreSQL localhost:5432  "
    $connected = $false
    try {
        $tcp = [System.Net.Sockets.TcpClient]::new()
        $ar  = $tcp.BeginConnect('127.0.0.1', 5432, $null, $null)
        $connected = $ar.AsyncWaitHandle.WaitOne(2000)
        $tcp.Close()
    } catch { $connected = $false }

    if ($connected) {
        Write-Host "[OK]" -ForegroundColor Green
    } else {
        Write-Host "[NOT REACHABLE]" -ForegroundColor Red
        Write-Host "    PostgreSQL is not listening on localhost:5432." -ForegroundColor Yellow
        Write-Host "    Find your service:  Get-Service | Where-Object { `$_.DisplayName -like '*postgres*' }" -ForegroundColor Yellow
        Write-Host "    Start it:           Start-Service <service-name>" -ForegroundColor Yellow
        $ok = $false
    }

    # ── W&B API key (train only, soft warning) ────────────────────────────
    if ($mode -eq 'train') {
        Write-Host -NoNewline "  WANDB_API_KEY             "
        if ($env:WANDB_API_KEY) {
            Write-Host "[set]" -ForegroundColor Green
        } else {
            Write-Host "[not set - W&B logging disabled]" -ForegroundColor Yellow
        }
    }

    # ── Warm-start checkpoint (train phase 3/4 with --resume) ─────────────
    if ($mode -eq 'train' -and $phase -ge 3) {
        # Mirror the default-resume logic from the train block
        $resolvedResume = if ($null -ne $resume) { [bool]$resume } else { $true }
        if ($resolvedResume) {
            $ckptMap = @{ 3 = 'phase2_best.pt'; 4 = 'phase3_best.pt' }
            $needed  = $ckptMap[$phase]
            if ($needed) {
                Write-Host -NoNewline "  Checkpoint $needed      "
                if (Test-Path (Join-Path $checkpointsDir $needed)) {
                    Write-Host "[found]" -ForegroundColor Green
                } else {
                    Write-Host "[missing - will cold-start]" -ForegroundColor Yellow
                    Write-Host "    Expected: $checkpointsDir\$needed" -ForegroundColor Yellow
                    Write-Host "    Use -Resume:`$false to suppress this warning." -ForegroundColor Yellow
                }
            }
        }
    }

    Write-Host ""
    if (-not $ok) {
        throw "Pre-flight check failed. Fix the issues above and retry."
    }
}

Assert-Prerequisites -mode $Mode -phase $Phase -resume $Resume -checkpointsDir $checkpointsDir

$env:PYTHONUNBUFFERED = '1'

if ($Mode -eq 'train') {
    $env:DATABASE_URL = Ensure-SyncDbUrl
    $env:CHECKPOINT_DIR = $checkpointsDir

    if ($null -eq $Epochs) {
        if ($Phase -eq 2) { $Epochs = 30 }
        elseif ($Phase -eq 3) { $Epochs = 50 }
        else { $Epochs = 30 }
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
        '--lr', $LearningRate,
        '--batch-size', $BatchSize
    )

    if ($Resume) {
        $cmd += '--resume'
    }

    if ($Phase -eq 2) {
        $cmd += @('--sample', $Sample, '--role-demand-sample', $RoleDemandSample, '--combo-sample', $ComboSample, '--commander-value-sample', $CommanderValueSample)
    }

    if ($Phase -eq 4) {
        if (-not $FreezeEncoder) {
            $cmd += @('--no-freeze-encoder', '--encoder-lr-scale', $EncoderLrScale)
        }
        $cmd += @('--temp-start', $TempStart, '--temp-end', $TempEnd)
    }

    Write-Host "Running trainer with args: $($cmd -join ' ')"
    Set-Location (Join-Path $RepoRoot 'services\trainer')
    & "$RepoRoot\\.venv\\Scripts\\python.exe" -u @cmd
    exit $LASTEXITCODE
}

$env:DATABASE_URL = Ensure-AsyncDbUrl
$env:EDHREC_CACHE_DIR = $cacheDir
$env:BATCH_SIZE = if ($env:INGEST_BATCH_SIZE) { $env:INGEST_BATCH_SIZE } else { '256' }
$env:SYNERGY_LIMIT = "$SynergyLimit"
$env:TRIBAL_MEMBER_LIMIT = "$TribalMemberLimit"
$env:COMMANDER_VALUE_LIMIT = "$CommanderValueLimit"

if ($Stage -eq 'backfill_roles') {
    $apiUrl = if ($env:API_URL) { $env:API_URL } else { 'http://localhost:8000' }
    $env:API_URL = $apiUrl
    Write-Host "Running backfill_roles against API at $apiUrl"
    Set-Location (Join-Path $RepoRoot 'services\ingest')
    & "$RepoRoot\.venv\Scripts\python.exe" -u 'backfill_roles.py'
    exit $LASTEXITCODE
}

$ingestCmd = @('pipeline.py')
if ($Stage -ne 'all') {
    $ingestCmd += @('--stage', $Stage)
}

Write-Host "Running ingest with args: $($ingestCmd -join ' ')"
Set-Location (Join-Path $RepoRoot 'services\ingest')
& "$RepoRoot\\.venv\\Scripts\\python.exe" -u @ingestCmd
exit $LASTEXITCODE
