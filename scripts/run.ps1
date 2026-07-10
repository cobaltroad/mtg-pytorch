param(
    # Shorthand: -Train N sets -Mode train -Phase N -Dataset .\ingest_cache\mtg_dataset.pt
    [ValidateScript({ $_ -eq $null -or $_ -in 1,2 })]
    [Nullable[int]]$Train = $null,

    [ValidateSet('train', 'ingest')]
    [string]$Mode = 'train',

    [ValidateSet(1, 2)]
    [int]$Phase = 2,

    # Path to a pre-built training artifact (.pt from export_dataset stage).
    # When set, no DATABASE_URL is required -- all data is loaded from the file.
    [string]$Dataset = '',

    [ValidateSet('download', 'process', 'embed_cards', 'tag_abilities', 'compute_synergy', 'compute_commander_value_synergy', 'compute_tribal_typeline_synergy', 'export_dataset', 'export_dataset_commanders', 'backfill_roles', 'all')]
    [string]$Stage = 'compute_synergy',

    [Nullable[int]]$Epochs = $null,
    [Nullable[double]]$LearningRate = $null,
    [Nullable[int]]$BatchSize = $null,

    [switch]$Resume,
    [switch]$NoResume,
    # Phase 2: scale factor applied to lr for all encoder parameters.
    # Default 0.1 protects Phase 1 geometry - encoder drifts 10x slower.
    [double]$Phase2EncoderLrScale = 0.1,
    # Phase 1: weight applied to the staple role-pair NT-Xent loss term.
    # 0 disables staple pairs; default 0.5 = half weight of noise-aug term.
    [double]$StaplePairWeight = 0.5,
    # Phase 2: NT-Xent temperature annealing range.
    # Start high for soft gradients, end near Phase 1 value to sharpen clusters.
    [double]$Phase2TempStart = 0.3,
    [double]$Phase2TempEnd = 0.07,
    # Phase 2 (Option B): train BilinearSynergyHead W_r matrices with frozen
    # encoder instead of NT-Xent on the card encoder.  Saves phase2_bilinear_best.pt.
    # The encoder (phase2_best.pt) is NOT updated — Phase 1 weights are reused as-is.
    # Default true (Option B is preferred).  Pass -Bilinear:$false to use NT-Xent.
    [bool]$Bilinear = $true,
    # Phase 2 bilinear: InfoNCE temperature (fixed, no annealing).
    [double]$BilinearTemperature = 0.07,

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
#   Phase 1-2 -> mtg_dataset.pt       (text equivalence + ability-trigger synergy)
#   (Phases 3-4 retired in #151 — composition-first architecture)
if ($null -ne $Train) {
    $Mode    = 'train'
    $Phase   = $Train
    if (-not $Dataset) {
        $Dataset = Join-Path $PSScriptRoot "..\ingest_cache\mtg_dataset.pt"
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

function Show-PostTrainingGuidance {
    param(
        [int]$Phase,
        [string]$Dataset,
        [string]$CheckpointsDir
    )

    $dsArg   = if ($Dataset) { ' -Dataset "' + $Dataset + '"' } else { '' }
    $ckpt    = $CheckpointsDir
    $sep     = '-------------------------------------------------------------'

    Write-Host ''
    Write-Host $sep -ForegroundColor DarkGray
    Write-Host (' Phase ' + $Phase + ' complete - evaluation guidance') -ForegroundColor Cyan
    Write-Host $sep -ForegroundColor DarkGray

    switch ($Phase) {
        1 {
            Write-Host ''
            Write-Host ' Nearest-neighbour spot-checks  (Phase 1 success criteria):' -ForegroundColor White
            Write-Host ('   .\scripts\eval_neighbors.ps1 "Sol Ring" -Phase 1' + $dsArg)
            Write-Host '     -> top-5 should be mana rocks (Arcane Signet, Mind Stone, ...)'
            Write-Host ('   .\scripts\eval_neighbors.ps1 "Swords to Plowshares" -Phase 1' + $dsArg)
            Write-Host '     -> top-5 should be removal (Path to Exile, Generous Gift, ...)'
            Write-Host ('   .\scripts\eval_neighbors.ps1 "Llanowar Elves" -Phase 1' + $dsArg)
            Write-Host '     -> top-5 should be mana dorks (Elvish Mystic, Fyndhorn Elves, ...)'
            Write-Host ''
            Write-Host ' Regression check: if mana-dork neighbors diverge after staple-pair'
            Write-Host ' training, reduce -StaplePairWeight (default 0.5) and re-run Phase 1.'
            Write-Host ''
            Write-Host ' Next step -train Phase 2:'
            Write-Host ('   .\scripts\run.ps1 -Train 2' + $dsArg)
            Write-Host ''
            Write-Host ' Phase 2 NT-Xent loss benchmarks (batch_size=512, ceiling=6.93):'
            Write-Host '   > 6.5       barely learning'
            Write-Host '   5.0-6.2     good'
            Write-Host '   3.5-5.0     excellent'
            Write-Host '   < 3.5       overfit risk'
        }
        2 {
            if ($Bilinear) {
                Write-Host ''
                Write-Host ' Bilinear (Option B) loss scale  (asymmetric InfoNCE per relation):' -ForegroundColor White
                Write-Host '   Random ceiling = ln(batch_size).  For batch_size=512: ln(512) = 6.24'
                Write-Host '   > 6.0       barely learning'
                Write-Host '   4.0-5.5     good'
                Write-Host '   2.5-4.0     excellent'
                Write-Host '   < 2.5       overfit risk'
                Write-Host ''
                Write-Host ' Encoder is FROZEN — Phase 1 geometry is preserved exactly.'
                Write-Host ' Only the W_r bilinear matrices are trained (one per relation type).'
                Write-Host ''
                Write-Host (' Checkpoint: ' + $ckpt + '\phase2_bilinear_best.pt  (bilinear head)')
                Write-Host (' Encoder:    ' + $ckpt + '\phase1_best.pt           (unchanged)')
                Write-Host ''
                Write-Host ' Regression check (Phase 1 geometry must be undamaged):'
                Write-Host ('   .\scripts\eval_neighbors.ps1 "Llanowar Elves" -Phase 1' + $dsArg)
                Write-Host '     -> should still show mana dorks (Phase 1 encoder, not updated)'
            } else {
                Write-Host ''
                Write-Host ' NT-Xent loss scale  (batch_size=512, random ceiling = ln(1024) = 6.93):' -ForegroundColor White
                Write-Host '   > 6.5       barely learning - check synergy_edges row count'
                Write-Host '   5.0-6.2     good'
                Write-Host '   3.5-5.0     excellent'
                Write-Host '   < 3.5       overfit risk - shorten training'
                Write-Host ''
                Write-Host ' Most learning happens in the second half of training as temperature'
                Write-Host ' anneals toward --temp-end (default 0.07).  A final loss around'
                Write-Host ' epoch 30 in the 5.5-6.2 range is normal - evaluate geometry,'
                Write-Host ' not just loss:'
                Write-Host ''
                Write-Host ' Regression check (verify Phase 2 did not corrupt Phase 1 geometry):'
                Write-Host ('   .\scripts\eval_neighbors.ps1 "Llanowar Elves" -Phase 2' + $dsArg)
                Write-Host '     -> should still show mana dorks'
                Write-Host ('   .\scripts\eval_neighbors.ps1 "Swords to Plowshares" -Phase 2' + $dsArg)
                Write-Host '     -> should still show removal spells'
                Write-Host ''
                Write-Host ('   Checkpoint: ' + $ckpt + '\phase2_best.pt')
            }
            Write-Host ''
            Write-Host ' Upload phase1_best + phase2_bilinear_best to the API (see CLAUDE.md).'
            Write-Host ' (Phases 3-4 retired in #151 — composition-first architecture.)'
        }
    }

    Write-Host $sep -ForegroundColor DarkGray
    Write-Host ''
}

function Assert-Prerequisites {
    param(
        [string]$mode,
        [int]$phase,
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

    # -- Phases 3-4 retired (#151): only Phases 1-2 are trainable ------------
    if ($mode -eq 'train' -and $phase -ge 3) {
        Write-Host "  Phase $phase is retired (#151) — only Phases 1-2 train." -ForegroundColor Red
        $ok = $false
    }

    Write-Host ""
    if (-not $ok) {
        throw "Pre-flight check failed. Fix the issues above and retry."
    }
}

Assert-Prerequisites -mode $Mode -phase $Phase -checkpointsDir $checkpointsDir -dataset $Dataset

$env:PYTHONUNBUFFERED = '1'

if ($Mode -eq 'train') {
    $needsDb = -not $Dataset
    if ($needsDb) {
        $env:DATABASE_URL = Ensure-SyncDbUrl
    }
    $env:CHECKPOINT_DIR = $checkpointsDir

    if ($null -eq $Epochs) {
        if ($Phase -eq 1)    { $Epochs = 50 }
        elseif ($Phase -eq 2) { $Epochs = 320 }
        else                  { $Epochs = 50 }
    }

    if ($null -eq $LearningRate) {
        if ($Phase -eq 2) { $LearningRate = 1e-3 }
        else              { $LearningRate = 1e-4  }
    }

    # Resolve resume: explicit -Resume or -NoResume wins.
    # Default: Phase 2 NT-Xent resumes from phase1_best; bilinear starts fresh (head is new).
    $resolvedResume = if ($Resume) { $true } elseif ($NoResume) { $false } else { $Phase -eq 2 -and -not $Bilinear }

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

    if ($resolvedResume) {
        $cmd += '--resume'
    }

    if ($Dataset) {
        $cmd += @('--dataset', $Dataset)
    }

    if ($Phase -eq 1) {
        $cmd += @('--staple-pair-weight', $StaplePairWeight)
    }

    if ($Phase -eq 2) {
        $cmd += @('--sample', $Sample, '--combo-sample', $ComboSample, '--commander-value-sample', $CommanderValueSample)
        if ($Bilinear) {
            $cmd += '--bilinear'
            $cmd += @('--bilinear-temperature', $BilinearTemperature)
            # Bilinear freezes the encoder; encoder-lr-scale and temp annealing
            # flags are NT-Xent-specific and not passed in this mode.
        } else {
            $cmd += @('--encoder-lr-scale', $Phase2EncoderLrScale)
            $cmd += @('--temp-start', $Phase2TempStart, '--temp-end', $Phase2TempEnd)
        }
    }



    Write-Host "Running trainer with args: $($cmd -join ' ')"
    Set-Location (Join-Path $RepoRoot 'services\trainer')
    & "$RepoRoot\\.venv\\Scripts\\python.exe" -u @cmd
    $trainExit = $LASTEXITCODE

    if ($trainExit -eq 0) {
        Show-PostTrainingGuidance -Phase $Phase -Dataset $Dataset -CheckpointsDir $checkpointsDir
    }
    exit $trainExit
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
