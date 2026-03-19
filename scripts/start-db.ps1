param(
    # If set, also creates the DB and runs migrations after the service is up
    [switch]$Migrate
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $RepoRoot

# ── Load .env ────────────────────────────────────────────────────────────────
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

# ── Derive connection params ──────────────────────────────────────────────────
$pgUser = $env:POSTGRES_USER
$pgPass = $env:POSTGRES_PASSWORD
$pgDb   = $env:POSTGRES_DB
$pgHost = 'localhost'
$pgPort = 5432

if (-not $pgUser -or -not $pgPass -or -not $pgDb) {
    throw "Set POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB in .env"
}

# ── Find PostgreSQL Windows service ──────────────────────────────────────────
$pgService = Get-Service -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -like '*PostgreSQL*' -or $_.Name -like 'postgresql*' } |
    Select-Object -First 1

if (-not $pgService) {
    Write-Host ""
    Write-Host "No PostgreSQL Windows service found." -ForegroundColor Red
    Write-Host "Install PostgreSQL from https://www.postgresql.org/download/windows/" -ForegroundColor Yellow
    Write-Host "Or if installed elsewhere, start it manually and retry." -ForegroundColor Yellow
    exit 1
}

Write-Host "Found service: $($pgService.Name) ($($pgService.DisplayName))" -ForegroundColor Cyan

# ── Start service if not running ─────────────────────────────────────────────
if ($pgService.Status -eq 'Running') {
    Write-Host "Service is already running." -ForegroundColor Green
} else {
    # Starting a service requires administrator
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if (-not $isAdmin) {
        Write-Host ""
        Write-Host "Cannot start service: not running as Administrator." -ForegroundColor Red
        Write-Host "Re-run this script in an elevated PowerShell window, or start the service manually:" -ForegroundColor Yellow
        Write-Host "    Start-Service '$($pgService.Name)'" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "Starting $($pgService.Name)..." -ForegroundColor Cyan
    Start-Service $pgService.Name
}

# ── Wait for TCP port to accept connections ───────────────────────────────────
Write-Host -NoNewline "Waiting for localhost:$pgPort to accept connections"
$deadline = (Get-Date).AddSeconds(30)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $tcp = [System.Net.Sockets.TcpClient]::new()
        $ar  = $tcp.BeginConnect($pgHost, $pgPort, $null, $null)
        if ($ar.AsyncWaitHandle.WaitOne(500)) {
            $tcp.Close()
            $ready = $true
            break
        }
        $tcp.Close()
    } catch {}
    Write-Host -NoNewline "."
    Start-Sleep -Milliseconds 500
}

if ($ready) {
    Write-Host " ready." -ForegroundColor Green
} else {
    Write-Host " timed out." -ForegroundColor Red
    exit 1
}

# ── Optional: create DB + run migrations ─────────────────────────────────────
if ($Migrate) {
    $env:PGPASSWORD = $pgPass

    # Create database if it doesn't exist
    $dbExists = & psql -h $pgHost -p $pgPort -U $pgUser -d postgres -tAc `
        "SELECT 1 FROM pg_database WHERE datname = '$pgDb'" 2>$null
    if ($dbExists -ne '1') {
        Write-Host "Creating database '$pgDb'..." -ForegroundColor Cyan
        & createdb -h $pgHost -p $pgPort -U $pgUser $pgDb
    } else {
        Write-Host "Database '$pgDb' already exists." -ForegroundColor Green
    }

    # Ensure pgvector extension
    Write-Host "Ensuring pgvector extension..." -ForegroundColor Cyan
    & psql -h $pgHost -p $pgPort -U $pgUser -d $pgDb `
        -c "CREATE EXTENSION IF NOT EXISTS vector;"

    # Run migrations in order
    $migrationsDir = Join-Path $RepoRoot 'data\migrations'
    Get-ChildItem -Path $migrationsDir -Filter '*.sql' | Sort-Object Name | ForEach-Object {
        Write-Host "Applying $($_.Name)..." -ForegroundColor Cyan
        & psql -h $pgHost -p $pgPort -U $pgUser -d $pgDb -f $_.FullName
    }

    Write-Host "Migrations complete." -ForegroundColor Green
}

Write-Host ""
Write-Host "PostgreSQL is up at ${pgHost}:${pgPort} (db: $pgDb, user: $pgUser)" -ForegroundColor Green
