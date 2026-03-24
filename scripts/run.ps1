param(
    # Shorthand: -Train N sets -Mode train -Phase N -Dataset .\ingest_cache\mtg_dataset.pt
    [ValidateScript({ $_ -eq $null -or $_ -in 1,2,3,4 })]
    [Nullable[int]]$Train = $null,

    [ValidateSet('train', 'ingest')]
    [string]$Mode = 'train',

    [ValidateSet(1, 2, 3, 4)]
    [int]$Phase = 3,

    # Path to a pre-built training artifact (.pt from export_dataset or export_cooccurrence_dataset stage).
    # When set, no DATABASE_URL is required -- all data is loaded from the file.
    [string]$Dataset = '',

    [ValidateSet('download', 'process', 'embed_cards', 'tag_abilities', 'compute_synergy', 'compute_commander_value_synergy', 'compute_tribal_typeline_synergy', 'export_dataset', 'export_cooccurrence_dataset', 'export_dataset_commanders', 'backfill_roles', 'all')]
    [string]$Stage = 'compute_synergy',

    [Nullable[int]]$Epochs = $null,
    [Nullable[double]]$LearningRate = $null,
    [Nullable[int]]$BatchSize = $null,

    [Nullable[bool]]$Resume = $null,
    [string]$FreezeEncoder = 'false',
    [double]$EncoderLrScale = 0.01,
    # Phase 2: scale factor applied to lr for all encoder parameters.
    # Default 0.1 protects Phase 1 geometry — encoder drifts 10x slower.
    [double]$Phase2EncoderLrScale = 0.1,
    # Phase 2: NT-Xent temperature annealing range.
    # Start high for soft gradients, end near Phase 1 value to sharpen clusters.
    [double]$Phase2TempStart = 0.3,
    [double]$Phase2TempEnd = 0.07,
    [int]$Patience = 10,
    [double]$TempStart = 0.1,
    [double]$TempEnd = 0.05,
    # Phase 4 Option A: synergy-only training (default $true).
    # Set -SynergyOnly $false to fall back to the legacy deck+synergy loop.
    [bool]$SynergyOnly = $true,
    [int]$SynBatchSize = 256,

    # Training path: compositional (default) or cooccurrence (see issue #71).
    [ValidateSet('cooccurrence', 'compositional')]
    [string]$TrainingPath = 'compositional',

    # Phase 4 synergy-guided training weights
    [int]$SynPerEpoch = 1000,
    [double]$ComboWeight = 3.0,
    [double]$AbilityWeight = 2.0,
    [double]$TribalWeight = 1.5,
    [int]$P4SynergyLimit = 300,

    [int]$Sample = 500000,
    [int]$RoleDemandSample = 100000,
    [int]$ComboSample = 200000,
    [int]$CommanderValueSample = 200000,

    [int]$SynergyLimit = 500000,
    [int]$TribalMemberLimit = 80000,
    [int]$CommanderValueLimit = 20000
)

$ErrorActionPreference = 'Stop'

# -Train N shorthand: expand to -Mode train -Phase N -Dataset <artifact>
# Artifact path depends on training path: cooccurrence uses mtg_cooccurrence_dataset.pt.
if ($null -ne $Train) {
    $Mode    = 'train'
    $Phase   = $Train
    if (-not $Dataset) {
        $artifactName = if ($TrainingPath -eq 'compositional') {
            'mtg_dataset.pt'
        } else {
            'mtg_cooccurrence_dataset.pt'
        }
        $Dataset = Join-Path $PSScriptRoot "..\ingest_cache\$artifactName"
    }
}

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
        [string]$checkpointsDir,
        [string]$dataset
    )
    $ok = $true
    Write-Host ""
    Write-Host "Pre-flight checks:" -ForegroundColor Cyan

    # -- Dataset artifact (skips DB check when provided) ------------------
    if ($dataset) {
        Write-Host -NoNewline "  Dataset artifact           "
        if (Test-Path $dataset) {
            $sizeMb = [math]::Round((Get-Item $dataset).Length / 1MB, 0)
            Write-Host "[found - ${sizeMb} MB, DB check skipped]" -ForegroundColor Green
        } else {
            Write-Host "[NOT FOUND]" -ForegroundColor Red
            Write-Host "    Expected: $dataset" -ForegroundColor Yellow
            Write-Host "    Run: .\scripts\download_dataset.ps1" -ForegroundColor Yellow
            $ok = $false
        }
    } else {
        # -- PostgreSQL reachable? -----------------------------------------
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
            Write-Host "    Download the artifact instead:" -ForegroundColor Yellow
            Write-Host "        .\scripts\download_dataset.ps1" -ForegroundColor Yellow
            Write-Host "    Then train with: -Dataset .\ingest_cache\mtg_dataset.pt" -ForegroundColor Yellow
            $ok = $false
        }
    }

    # -- W&B API key (train only, soft warning) ----------------------------
    if ($mode -eq 'train') {
        Write-Host -NoNewline "  WANDB_API_KEY             "
        if ($env:WANDB_API_KEY) {
            Write-Host "[set]" -ForegroundColor Green
        } else {
            Write-Host "[not set - W&B logging disabled]" -ForegroundColor Yellow
        }
    }

    # -- Warm-start checkpoint (train phase 3/4 with --resume) ------------
    if ($mode -eq 'train' -and $phase -ge 3) {
        # Mirror the default-resume logic from the train block
        $resolvedResume = if ($null -ne $resume) { [bool]$resume } else { $true }
        if ($resolvedResume) {
            $pfx = if ($TrainingPath -eq 'compositional') { 'comp_phase' } else { 'phase' }
            $ckptMap = @{ 3 = "${pfx}2_best.pt"; 4 = "${pfx}3_best.pt" }
            $needed  = $ckptMap[$phase]
            if ($needed) {
                Write-Host -NoNewline "  Checkpoint $needed"
                if (Test-Path (Join-Path $checkpointsDir $needed)) {
                    Write-Host "[found]" -ForegroundColor Green
                } else {
                    Write-Host "[missing - will cold-start]" -ForegroundColor Yellow
                    Write-Host "    Expected: $checkpointsDir\$needed" -ForegroundColor Yellow
                    Write-Host "    Use -Resume:$false to suppress this warning." -ForegroundColor Yellow
                }
            }
        }
    }

    Write-Host ""
    if (-not $ok) {
        throw "Pre-flight check failed. Fix the issues above and retry."
    }
}

Assert-Prerequisites -mode $Mode -phase $Phase -resume $Resume -checkpointsDir $checkpointsDir -dataset $Dataset

$env:PYTHONUNBUFFERED = '1'

if ($Mode -eq 'train') {
    # DATABASE_URL is only required when not using a pre-built dataset artifact,
    # EXCEPT for compositional Phase 3: role-matching is always DB-derived
    # (oracle text → roles → card lookup), so it needs the DB even with an artifact.
    $needsDb = (-not $Dataset) -or ($TrainingPath -eq 'compositional' -and $Phase -eq 3)
    if ($needsDb) {
        $env:DATABASE_URL = Ensure-SyncDbUrl
    }
    $env:CHECKPOINT_DIR = $checkpointsDir

    if ($null -eq $Epochs) {
        if ($Phase -eq 1)    { $Epochs = 50 }
        elseif ($Phase -eq 2) { $Epochs = 30 }
        elseif ($Phase -eq 3) { $Epochs = 50 }
        else                  { $Epochs = 30 }
    }

    if ($null -eq $LearningRate) {
        $LearningRate = 1e-4
    }

    if ($null -eq $Resume) {
        if ($Phase -le 1) { $Resume = $false }
        elseif ($Phase -eq 4) { $Resume = $false }
        else { $Resume = $true }
    }

    if ($null -eq $BatchSize) {
        if ($Phase -le 2) { $BatchSize = 512 }
        else              { $BatchSize = 32  }
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

    $cmd += @('--training-path', $TrainingPath)

    if ($Dataset) {
        $cmd += @('--dataset', $Dataset)
    }

    if ($Phase -eq 2) {
        $cmd += @('--sample', $Sample, '--role-demand-sample', $RoleDemandSample, '--combo-sample', $ComboSample, '--commander-value-sample', $CommanderValueSample)
        $cmd += @('--encoder-lr-scale', $Phase2EncoderLrScale)
        $cmd += @('--temp-start', $Phase2TempStart, '--temp-end', $Phase2TempEnd)
    }

    # Compositional Phase 3: pass encoder_lr_scale so the larger role-matched
    # dataset doesn't collapse the encoder.  Co-occurrence Phase 3 always uses
    # scale=1.0 (handled in train.py); no flag needed for that path.
    if ($Phase -eq 3 -and $TrainingPath -eq 'compositional') {
        $cmd += @('--encoder-lr-scale', $Phase2EncoderLrScale)
    }

    if ($Phase -eq 4) {
        $resolvedFreezeEncoder = $FreezeEncoder -notin @('false', '0', 'no', '$false')
        if (-not $resolvedFreezeEncoder) {
            $cmd += @('--no-freeze-encoder', '--encoder-lr-scale', $EncoderLrScale)
        }
        $cmd += @('--patience', $Patience)
        $cmd += @(
            '--temp-start', $TempStart, '--temp-end', $TempEnd,
            '--ability-weight', $AbilityWeight,
            '--tribal-weight', $TribalWeight,
            '--synergy-limit', $P4SynergyLimit
        )
        if ($SynergyOnly) {
            $cmd += @('--synergy-only', '--syn-batch-size', $SynBatchSize)
        } else {
            $cmd += @('--no-synergy-only', '--syn-per-epoch', $SynPerEpoch,
                      '--combo-weight', $ComboWeight)
        }
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
