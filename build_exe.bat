@echo off
setlocal

cd /d "%~dp0"

echo.
echo == YouTube Music Playlist Downloader EXE build ==

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    set "PY_CMD=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo == Creating virtual environment ==
    %PY_CMD% -m venv ".venv"
    if errorlevel 1 goto :fail
)

set "VENV_PY=.venv\Scripts\python.exe"

echo.
echo == Installing build dependencies ==
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail

"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

"%VENV_PY%" -m pip install --upgrade pyinstaller
if errorlevel 1 goto :fail

echo.
echo == Building standalone EXE ==
"%VENV_PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "YouTube Music Playlist Downloader" ^
  --collect-all yt_dlp ^
  --add-data "assets\wolf-banner.png;assets" ^
  youtube_music_playlist_downloader.py
if errorlevel 1 goto :fail

if not exist "dist" mkdir "dist"
copy /Y "install.ps1" "dist\install.ps1" >nul

echo.
echo Done.
echo EXE created at:
echo   %cd%\dist\YouTube Music Playlist Downloader.exe
echo.
echo FFmpeg is still required on the computer that runs the EXE for FLAC conversion.
pause
exit /b 0

:fail
echo.
echo Build failed. Check the error above.
pause
exit /b 1
