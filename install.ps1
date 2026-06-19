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
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        return $false
    }
    $source = [string]$command.Source
    $lowerName = $Name.ToLowerInvariant()
    if (($lowerName -eq "python" -or $lowerName -eq "python3") -and $source -like "*\WindowsApps\*") {
        return $false
    }
    return $true
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

function Install-PythonWithWinget {
    if (-not (Test-Command "winget")) {
        return $false
    }

    $pythonPackages = @(
        @{ Id = "Python.Python.3.14"; Name = "Python 3.14"; Minor = 14 },
        @{ Id = "Python.Python.3.13"; Name = "Python 3.13"; Minor = 13 },
        @{ Id = "Python.Python.3.12"; Name = "Python 3.12"; Minor = 12 },
        @{ Id = "Python.Python.3.11"; Name = "Python 3.11"; Minor = 11 },
        @{ Id = "Python.Python.3.10"; Name = "Python 3.10"; Minor = 10 }
    )

    $attempts = @(
        @("--exact", "--source", "winget", "--scope", "user"),
        @("--exact", "--source", "winget"),
        @("--source", "winget", "--scope", "user"),
        @("--source", "winget")
    )

    foreach ($package in $pythonPackages) {
        foreach ($extraArgs in $attempts) {
            Write-Step "Installing $($package.Name) with winget"
            $arguments = @(
                "install",
                "--id", $package.Id,
                "--accept-package-agreements",
                "--accept-source-agreements"
            ) + $extraArgs

            Write-Host "> winget $($arguments -join ' ')"
            & winget @arguments
            $exitCode = $LASTEXITCODE
            Refresh-Path

            if ($exitCode -eq 0) {
                if (Wait-ForPythonCommand -PreferredMinor $package.Minor -TimeoutSeconds 90) {
                    return $true
                }
                Write-Warning "$($package.Name) install completed, but Python is still not visible after waiting."
            }
            else {
                Write-Warning "winget exited with code $exitCode for $($package.Name) using: $($extraArgs -join ' ')"
            }
        }
    }

    return $false
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

function Ensure-WingetDependency {
    param(
        [string]$CommandName,
        [string]$WingetId,
        [string]$DisplayName
    )
    if (Test-Command $CommandName) {
        Write-Host "$DisplayName already found: $((Get-Command $CommandName).Source)" -ForegroundColor Green
        return
    }
    try {
        [void](Install-WithWinget -Id $WingetId -Name $DisplayName)
    }
    catch {
        Write-Warning "winget install failed for ${DisplayName}: $($_.Exception.Message)"
        Write-Warning "Install it manually with: winget install --id $WingetId"
    }
}

function Get-PythonVersionText {
    param([string]$PythonPath)
    try {
        $result = & $PythonPath --version 2>&1
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        return ($result -join " ")
    }
    catch {
        return $null
    }
}

function Test-PythonExecutable {
    param([string]$PythonPath)
    if (-not (Test-Path -LiteralPath $PythonPath)) {
        return $false
    }
    return [bool](Get-PythonVersionText $PythonPath)
}

function Test-PythonExecutableAtLeast {
    param(
        [string]$PythonPath,
        [int]$Major,
        [int]$Minor
    )
    $versionText = Get-PythonVersionText $PythonPath
    if (-not $versionText) {
        return $false
    }
    if ($versionText -notmatch "Python\s+(\d+)\.(\d+)") {
        return $false
    }
    $foundMajor = [int]$Matches[1]
    $foundMinor = [int]$Matches[2]
    return ($foundMajor -gt $Major -or ($foundMajor -eq $Major -and $foundMinor -ge $Minor))
}

function Get-PythonCommandForVersion {
    param([int]$Minor)

    if (Test-Command "py") {
        & py "-3.$Minor" --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return @("py", "-3.$Minor")
        }
    }

    $folderName = "Python3$Minor"
    $candidatePaths = @(
        (Join-Path $env:LocalAppData "Programs\Python\$folderName\python.exe"),
        (Join-Path $env:ProgramFiles "$folderName\python.exe")
    )
    if (${env:ProgramFiles(x86)}) {
        $candidatePaths += (Join-Path ${env:ProgramFiles(x86)} "$folderName\python.exe")
    }

    foreach ($candidate in $candidatePaths) {
        if (Test-PythonExecutableAtLeast -PythonPath $candidate -Major 3 -Minor 10) {
            return @($candidate)
        }
    }

    return $null
}

function Get-PythonCommand {
    foreach ($minor in 14, 13, 12, 11, 10) {
        $command = Get-PythonCommandForVersion -Minor $minor
        if ($command) {
            return $command
        }
    }

    if (Test-Command "python") {
        $pythonCommand = (Get-Command "python" -ErrorAction SilentlyContinue).Source
        if ($pythonCommand -and (Test-PythonExecutableAtLeast -PythonPath $pythonCommand -Major 3 -Minor 10)) {
            return @($pythonCommand)
        }
    }

    return $null
}

function Wait-ForPythonCommand {
    param(
        [int]$PreferredMinor = 0,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Refresh-Path

        if ($PreferredMinor -gt 0) {
            $preferred = Get-PythonCommandForVersion -Minor $PreferredMinor
            if ($preferred) {
                Write-Host "Python found: $($preferred -join ' ')" -ForegroundColor Green
                return $preferred
            }
        }

        $python = Get-PythonCommand
        if ($python) {
            Write-Host "Python found: $($python -join ' ')" -ForegroundColor Green
            return $python
        }

        Write-Host "Waiting for Python to appear on PATH or the py launcher..."
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)

    return $null
}

function Ensure-Python {
    if (Get-PythonCommand) {
        Write-Host "Python 3.10+ already found." -ForegroundColor Green
        return
    }

    if (Install-PythonWithWinget) {
        return
    }

    throw "Python 3.10+ could not be installed automatically. Try: winget search --source winget --id Python.Python"
}

function Get-Python {
    $python = Get-PythonCommand
    if ($python) {
        return $python
    }
    throw "Python 3.10+ was not found. Install Python manually, then rerun install.ps1."
}
Write-Step "YouTube Music Downloader dependency bootstrap"

Ensure-WingetDependency -CommandName "git" -WingetId "Git.Git" -DisplayName "Git"
Ensure-WingetDependency -CommandName "node" -WingetId "OpenJS.NodeJS.LTS" -DisplayName "Node.js LTS"
Ensure-Python
Ensure-WingetDependency -CommandName "dotnet" -WingetId "Microsoft.DotNet.SDK.10" -DisplayName ".NET SDK 10"
Ensure-WingetDependency -CommandName "gh" -WingetId "GitHub.cli" -DisplayName "GitHub CLI"

$venvPath = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if ((Test-Path -LiteralPath $venvPython) -and -not (Test-PythonExecutable $venvPython)) {
    Write-Warning "Existing virtual environment is broken or points to a missing Python install. Recreating .venv."
    Remove-Item -LiteralPath $venvPath -Recurse -Force
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonCommand = @(Get-Python)
    $pythonExe = $pythonCommand[0]
    $pythonArgs = @()
    if ($pythonCommand.Count -gt 1) {
        $pythonArgs = $pythonCommand[1..($pythonCommand.Count - 1)]
    }
    Invoke-Logged $pythonExe ($pythonArgs + @("-m", "venv", $venvPath))
}

if (-not (Test-PythonExecutable $venvPython)) {
    throw "The virtual environment Python is still not usable: $venvPython"
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

