<#
.SYNOPSIS
    Run BPM Tagger locally (no Docker).

.DESCRIPTION
    Sets the environment variables the app needs and launches
    `python -m bpm_tagger`, preferring the repo-local .venv if present.
    All parameters fall back to the matching env var, then to a dev default.

.EXAMPLE
    .\run.ps1 -MusicDir "D:\Music"
    # Watch mode + web UI on http://localhost:5000 (password: changeme)

.EXAMPLE
    .\run.ps1 -Mode scan_all -MusicDir "D:\Music" -NoUi
    # One-shot full scan, no web UI
#>
[CmdletBinding()]
param(
    [string]$MusicDir,
    [string]$DbPath,
    [string]$Mode,
    [int]   $Port,
    [string]$Password,
    [switch]$NoUi
)

$ErrorActionPreference = 'Stop'

# Resolve the script directory robustly ($PSScriptRoot can be empty depending on
# how the script is invoked, e.g. `powershell -File`).
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $root) { $root = (Get-Location).Path }

# Load a local .env (KEY=value per line) so your machine-specific config —
# music path, password, tokens — lives outside git (.env is .gitignored).
# Existing process env vars win, so an explicitly exported value is never
# clobbered; command-line params still override everything below.
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
    Write-Host "Loading config from .env" -ForegroundColor DarkGray
    foreach ($line in Get-Content $envFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $eq = $trimmed.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $trimmed.Substring(0, $eq).Trim()
        $val = $trimmed.Substring($eq + 1).Trim().Trim('"').Trim("'")
        # Don't override a variable already set in this shell session.
        if (-not [Environment]::GetEnvironmentVariable($key)) {
            Set-Item -Path "Env:$key" -Value $val
        }
    }
}

# Fill parameters from their matching env var, then a dev default.
if (-not $MusicDir) { $MusicDir = if ($env:MUSIC_DIR)   { $env:MUSIC_DIR } else { Join-Path $root 'sample_music' } }
if (-not $DbPath)   { $DbPath   = if ($env:DB_PATH)     { $env:DB_PATH }   else { Join-Path $root 'data\bpm_tagger.db' } }
if (-not $Mode)     { $Mode     = if ($env:MODE)        { $env:MODE }      else { 'watch' } }
if (-not $Port)     { $Port     = if ($env:UI_PORT)     { [int]$env:UI_PORT } else { 5000 } }
if (-not $Password) { $Password = if ($env:UI_PASSWORD) { $env:UI_PASSWORD } else { 'changeme' } }

# Prefer the repo-local virtualenv; otherwise fall back to python on PATH.
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
$py = if (Test-Path $venvPy) { $venvPy } else { 'python' }

# Make sure the music and data directories exist so the app can boot.
if (-not (Test-Path $MusicDir)) {
    New-Item -ItemType Directory -Force -Path $MusicDir | Out-Null
    Write-Host "Created empty music dir: $MusicDir (point -MusicDir at your library)" -ForegroundColor Yellow
}
$dataDir = Split-Path -Parent $DbPath
if ($dataDir -and -not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
}

$env:MODE        = $Mode
$env:MUSIC_DIR   = $MusicDir
$env:DB_PATH     = $DbPath
$env:UI_PORT     = "$Port"
if ($dataDir) { $env:REPORT_PATH = Join-Path $dataDir 'review_report.csv' }
if ($NoUi) {
    $env:ENABLE_UI = 'false'
} else {
    $env:ENABLE_UI   = 'true'
    $env:UI_PASSWORD = $Password
}

Write-Host "BPM Tagger - local run" -ForegroundColor Cyan
Write-Host "  python    : $py"
Write-Host "  mode      : $Mode"
Write-Host "  music_dir : $MusicDir"
Write-Host "  db_path   : $DbPath"
if (-not $NoUi) {
    $url = "http://localhost:" + $Port
    Write-Host "  web UI    : $url  (password: $Password)" -ForegroundColor Green
}
Write-Host ""

& $py -m bpm_tagger
