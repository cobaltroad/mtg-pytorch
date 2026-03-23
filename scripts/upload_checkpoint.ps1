<#
.SYNOPSIS
    Upload a checkpoint (.pt) to the API and hot-swap the model.

.DESCRIPTION
    POSTs a checkpoint file to POST /admin/checkpoint.
    The API swaps the model immediately with no restart needed.

.PARAMETER File
    Path to the .pt file to upload.
    Defaults to .\checkpoints\phase4_best.pt

.PARAMETER Name
    Name to save the checkpoint as on the server (without .pt extension).
    Defaults to phase4_best (cooccurrence) or comp_phase4_best (compositional).

.PARAMETER TrainingPath
    Training path: cooccurrence (default) or compositional.
    Determines the default checkpoint name and file path.

.PARAMETER ApiHost
    API hostname (without scheme).  Defaults to API_HOST from .env or edh-api.cardtrak.app.

.PARAMETER Token
    Admin token (x-admin-token header).  Defaults to ADMIN_TOKEN from .env.

.EXAMPLE
    .\scripts\upload_checkpoint.ps1

.EXAMPLE
    .\scripts\upload_checkpoint.ps1 -File .\checkpoints\phase3_best.pt -Name phase3_best

.EXAMPLE
    .\scripts\upload_checkpoint.ps1 -TrainingPath compositional
#>

param(
    [string]$File  = '',
    [string]$Name  = '',
    [ValidateSet('cooccurrence', 'compositional')]
    [string]$TrainingPath = 'cooccurrence',
    [string]$ApiHost = '',
    [string]$Token = ''
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

# -- Load .env -----------------------------------------------------------------

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

# -- Resolve parameters --------------------------------------------------------

$defaultName = if ($TrainingPath -eq 'compositional') { 'comp_phase4_best' } else { 'phase4_best' }
if (-not $Name) { $Name = $defaultName }

if (-not $File) {
    $File = Join-Path $RepoRoot "checkpoints\${Name}.pt"
}

if (-not (Test-Path $File)) {
    $trainHint = if ($TrainingPath -eq 'compositional') {
        ".\scripts\run.ps1 -TrainingPath compositional -Train 4"
    } else {
        ".\scripts\run.ps1 -Train 4"
    }
    throw "Checkpoint not found: $File`nTrain first with: $trainHint"
}

if (-not $ApiHost) {
    $ApiHost = $env:API_HOST
    if (-not $ApiHost) {
        $ApiHost = 'edh-api.cardtrak.app'
        Write-Warning "API_HOST not set in .env - defaulting to $ApiHost"
    }
}

if (-not $Token) {
    $Token = $env:ADMIN_TOKEN
    if (-not $Token) {
        Write-Warning "ADMIN_TOKEN not set in .env - uploading without auth (will fail if token is required)"
    }
}

$url     = "https://$ApiHost/admin/checkpoint?name=$Name"
$sizeMb  = [math]::Round((Get-Item $File).Length / 1MB, 1)

Write-Host ""
Write-Host "Uploading checkpoint" -ForegroundColor Cyan
Write-Host "  File:   $File ($sizeMb MB)"
Write-Host "  Save as: $Name"
Write-Host "  URL:    $url"
Write-Host ""

# -- Upload --------------------------------------------------------------------

$boundary = [System.Guid]::NewGuid().ToString()
$fileName = [System.IO.Path]::GetFileName($File)
$fileBytes = [System.IO.File]::ReadAllBytes($File)

$bodyLines = @(
    "--$boundary",
    "Content-Disposition: form-data; name=`"file`"; filename=`"$fileName`"",
    "Content-Type: application/octet-stream",
    "",
    ""
)
$bodyHeader  = [System.Text.Encoding]::UTF8.GetBytes($bodyLines -join "`r`n")
$bodyFooter  = [System.Text.Encoding]::UTF8.GetBytes("`r`n--$boundary--`r`n")

$bodyStream = New-Object System.IO.MemoryStream
$bodyStream.Write($bodyHeader, 0, $bodyHeader.Length)
$bodyStream.Write($fileBytes,  0, $fileBytes.Length)
$bodyStream.Write($bodyFooter, 0, $bodyFooter.Length)
$body = $bodyStream.ToArray()

$headers = @{
    'Content-Type'  = "multipart/form-data; boundary=$boundary"
    'x-admin-token' = $Token
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $response = Invoke-RestMethod -Uri $url -Method Post -Body $body -Headers $headers -TimeoutSec 120
    $sw.Stop()

    $elapsed = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    Write-Host "Uploaded $([int]($response.bytes / 1MB)) MB in ${elapsed}s -> $($response.saved)" -ForegroundColor Green
    Write-Host "Model cache cleared - next deck generation uses the new checkpoint." -ForegroundColor Green
    Write-Host ""
} catch {
    $sw.Stop()
    Write-Error "Upload failed: $_"
    exit 1
}
