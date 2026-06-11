param(
    [switch]$SkipChocolateyFallback
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Logged {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    Write-Host "> $FilePath $($Arguments -join ' ')"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Install-WithWinget {
    param(
        [string]$Id,
        [string]$Name
    )
    if (-not (Test-Command "winget")) {
        return $false
    }
    Write-Step "Installing $Name with winget"
    Invoke-Logged "winget" @(
        "install",
        "--exact",
        "--id", $Id,
        "--accept-package-agreements",
        "--accept-source-agreements"
    )
    Refresh-Path
    return $true
}

function Ensure-Chocolatey {
    if (Test-Command "choco") {
        return $true
    }
    if ($SkipChocolateyFallback) {
        return $false
    }
    Write-Warning "Chocolatey is not installed. Install it manually or rerun with winget available."
    return $false
}

function Install-WithChocolatey {
    param(
        [string]$Package,
        [string]$Name
    )
    if (-not (Ensure-Chocolatey)) {
        return $false
    }
    Write-Step "Installing $Name with Chocolatey"
    Invoke-Logged "choco" @("install", $Package, "-y", "--no-progress")
    Refresh-Path
    return $true
}

function Ensure-Tool {
    param(
        [string]$CommandName,
        [string]$WingetId,
        [string]$ChocoPackage,
        [string]$DisplayName
    )
    if (Test-Command $CommandName) {
        Write-Host "$DisplayName already found: $((Get-Command $CommandName).Source)" -ForegroundColor Green
        return
    }
    $installed = $false
    try {
        $installed = Install-WithWinget -Id $WingetId -Name $DisplayName
    }
    catch {
        Write-Warning "winget install failed for ${DisplayName}: $($_.Exception.Message)"
    }
    if (-not $installed) {
        $installed = Install-WithChocolatey -Package $ChocoPackage -Name $DisplayName
    }
    if (-not $installed) {
        Write-Warning "Could not install $DisplayName automatically. Install it manually if downloads need it."
    }
}

function Get-Python {
    if (Test-Command "py") {
        return @("py", "-3")
    }
    if (Test-Command "python") {
        return @("python")
    }
    throw "Python was not found. Install Python 3.10+ first."
}

Write-Step "YouTube Music Downloader dependency bootstrap"

$venvPath = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    $pythonCommand = Get-Python
    $pythonExe = $pythonCommand[0]
    $pythonArgs = @()
    if ($pythonCommand.Count -gt 1) {
        $pythonArgs = $pythonCommand[1..($pythonCommand.Count - 1)]
    }
    Invoke-Logged $pythonExe ($pythonArgs + @("-m", "venv", $venvPath))
}

Invoke-Logged $venvPython @("-m", "pip", "install", "-U", "pip")
Invoke-Logged $venvPython @("-m", "pip", "install", "-U", "yt-dlp[default]")

Ensure-Tool -CommandName "ffmpeg" -WingetId "Gyan.FFmpeg" -ChocoPackage "ffmpeg" -DisplayName "FFmpeg"
Ensure-Tool -CommandName "deno" -WingetId "DenoLand.Deno" -ChocoPackage "deno" -DisplayName "Deno"

Refresh-Path

Write-Step "Dependency check"
Invoke-Logged $venvPython @((Join-Path $PSScriptRoot "youtube_music_playlist_downloader.py"), "--check-deps")

Write-Host ""
Write-Host "Done. Start the GUI with:" -ForegroundColor Green
Write-Host "  .\run_downloader.ps1"
