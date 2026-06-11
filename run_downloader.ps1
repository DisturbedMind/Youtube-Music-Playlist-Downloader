$ErrorActionPreference = "Stop"

$venvPath = Join-Path $PSScriptRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"
$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue

if (-not (Test-Path $pythonExe)) {
    if ($pythonLauncher) {
        & py -m venv $venvPath
    }
    else {
        & python -m venv $venvPath
    }
}

& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
& $pythonExe (Join-Path $PSScriptRoot "youtube_music_playlist_downloader.py")
