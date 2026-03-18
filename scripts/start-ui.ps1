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

if (-not $env:API_URL) {
    $env:API_URL = 'http://localhost:8000'
}

if (-not $env:STREAMLIT_BROWSER_GATHER_USAGE_STATS) {
    $env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = 'false'
}

Set-Location (Join-Path $RepoRoot 'services\ui')
& "$RepoRoot\.venv\Scripts\python.exe" -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0