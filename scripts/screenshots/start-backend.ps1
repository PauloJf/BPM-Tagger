# Launches a local BPM Tagger instance for screenshot capture: serves the
# built UI on :5088, seeded from ./demo_music, into a throwaway ./screenshots-data
# DB. Run this, wait for "Serving on http://0.0.0.0:5088", then run
# capture_all.py / capture_player.py. Ctrl+C to stop.
#
# Override MUSIC_DIR / UI_PASSWORD / RUN_PASSWORD via env before launching if you
# want a different library or passwords (the capture_*.py defaults match these).
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$data = Join-Path $repo 'screenshots-data'
New-Item -ItemType Directory -Force -Path $data | Out-Null

# watch mode analyses any unscanned files on startup, then keeps the UI alive.
$env:MODE          = 'watch'
if (-not $env:MUSIC_DIR)    { $env:MUSIC_DIR   = Join-Path $repo 'demo_music' }
$env:DB_PATH       = Join-Path $data 'bpm.db'
$env:SETTINGS_PATH = Join-Path $data 'settings.json'
$env:ENABLE_UI     = 'true'
$env:UI_PORT       = '5088'
if (-not $env:UI_PASSWORD)  { $env:UI_PASSWORD  = 'screenshot123' }
if (-not $env:RUN_PASSWORD) { $env:RUN_PASSWORD = 'runmode123' }

if (-not (Test-Path $env:MUSIC_DIR)) {
  Write-Warning "MUSIC_DIR '$($env:MUSIC_DIR)' not found — the library will be empty. Set `$env:MUSIC_DIR to a folder of audio files."
}

$py = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
& $py -m bpm_tagger
