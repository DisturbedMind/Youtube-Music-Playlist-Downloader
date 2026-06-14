# YouTube Music Playlist Downloader

![Moonlit wolf banner](assets/wolf-banner.png)

A small Python/Tkinter desktop app for downloading YouTube Music playlists with `yt-dlp`.

Use it only for audio you own, created, have permission to archive, or are otherwise legally allowed to save.

## What It Does

- Downloads one or more `music.youtube.com` playlists from a queue.
- Lets you paste multiple playlist URLs, reorder them, remove them, and clear finished items.
- Downloads the highest available source audio and converts every track to MP3 or FLAC through FFmpeg.
- Can use browser cookies for private playlists, age-gated tracks, or account-specific YouTube Music access.
- Can extract reusable `youtube-cookies.txt` cookies from a browser.
- Keeps a `.downloaded-archive.txt` file so repeated runs skip tracks already downloaded.
- Uses polite download sleeps, alternate YouTube clients, and EJS challenge solver settings learned from the movie trailer downloader.
- Shows per-playlist queue status plus a live GUI log, and can resume partial downloads.
- Saves unfinished queue items so they are still there next time the app opens.

## Setup On Windows

Fast path:

```powershell
.\run_downloader.ps1
```

Repair everything from the GUI with `Install / Repair Dependencies`, or run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Manual path:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python .\youtube_music_playlist_downloader.py
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the virtual environment again.

## Queue Workflow

1. Paste one or more playlist URLs into the queue box.
2. Click `Add`, or use `Paste + Add` to add URLs directly from the clipboard.
3. Reorder playlists with `Move Up` and `Move Down` if needed.
4. Click `Start Queue`.
5. Use `Stop Queue` to stop after the active yt-dlp step.
6. Use `Kill Queue` when you want to terminate the active queue immediately and mark pending/downloading playlists as stopped.

Rows move through `Queued`, `Downloading`, `Complete`, `Failed`, or `Stopped`. Failed and stopped rows can be run again by clicking `Start Queue`.

`Kill Queue` terminates the active queue process and its child processes, prevents any further playlists from starting, and marks pending/downloading rows as stopped. Partial files are left in place so yt-dlp can usually resume them later.

## MP3 / FLAC Output And FFmpeg

FFmpeg is required for:

- MP3 or FLAC conversion for every downloaded song
- embedded cover art
- embedded metadata

Install it with one of these options:

```powershell
winget install Gyan.FFmpeg
```

or:

```powershell
choco install ffmpeg
```

Restart PowerShell after installing FFmpeg so `ffmpeg.exe` is on `PATH`.

Use `MP3` when you want smaller files and better device compatibility; MP3 conversion uses 320 kbps. Use `FLAC` when you prefer larger converted files that avoid another lossy encode step after download. Neither option can improve YouTube Music's original source quality.

The optional `Normalize loudness` checkbox applies FFmpeg loudness normalization during conversion for both MP3 and FLAC. It can make mixed playlists play back at a more even volume. It is off by default because albums may have intentional track-to-track dynamics.

## Cookies For YouTube Music

Public playlists often work without cookies. For private playlists, Premium account streams, unavailable tracks, or account-specific recommendations, choose one of the browser cookie options in the app:

- `Firefox`
- `Chrome`
- `Edge`
- `Brave`
- `Opera`

Firefox is the recommended browser for grabbing cookies. Chrome, Edge, Brave, Opera, and Vivaldi are Chromium-based browsers, and on Windows their cookies are protected by DPAPI. DPAPI ties those cookies to the exact Windows user profile that created them. If the downloader is running as Administrator, under a different Windows user, from a service, or from a copied/odd Python environment, yt-dlp may not be able to decrypt those cookies even after the browser is closed.

That is why some users will need to install Firefox just for cookie extraction:

1. Install Firefox.
2. Open Firefox normally as your Windows user.
3. Sign in to YouTube Music in Firefox.
4. Close Firefox completely.
5. In the downloader, pick `Firefox` under `Extract from`.
6. Click `Extract Cookies`, or use `Kill Browser + Extract`.

After the app creates `youtube-cookies.txt`, leave `Cookies` set to `cookies.txt file`. You do not need to browse with Firefox every day; it can just be the reliable cookie-export browser.

Recommended GUI flow:

1. Choose a cookies file path, for example `youtube-cookies.txt`.
2. Pick `Firefox` under `Extract from`.
3. Click `Extract Cookies`.
4. Leave `Cookies` set to `cookies.txt file`.

Close the selected browser before extracting if cookie extraction fails. Direct browser cookies are also available, but a reusable cookies file is usually more predictable.

## YouTube 403 / Challenge Handling

The app follows the same pattern as the movie trailer downloader:

- installs `yt-dlp[default]`
- prefers Deno or Node.js 22+ for YouTube JavaScript challenge solving
- sets `remote_components` to `ejs:github` by default
- retries blocked downloads with alternate YouTube client profiles
- retries without stale cookies when YouTube returns 403-style failures
- adds a small random sleep between downloads

If YouTube still blocks a playlist, refresh cookies from the GUI and try again later.

## Output

By default, files are saved under:

```text
%USERPROFILE%\Downloads\YouTube Music
```

Normal playlists create a subfolder named after the playlist. The file extension follows your selected audio format:

```text
001 - Track Title.mp3
002 - Track Title.mp3
```

YouTube Music albums are downloaded to a temporary staging folder first. After conversion, the app reads the finished audio file tags, groups albums by album artist first, then album title, and cleans the staging area after a successful album download:

```text
Album Artist
  Album Title
    001 - Track Title.mp3
    002 - Track Title.mp3
```

That keeps all albums by the same artist under one top-level artist folder.

Cleanup is limited to the app-created `_staging` folder. The downloader must not scan or delete existing artist, album, or playlist folders in your output library.

## Build A Standalone EXE

Optional, if you want a double-clickable app:

```cmd
build_exe.bat
```

The batch file creates or reuses `.venv`, installs PyInstaller, bundles `yt-dlp`, includes the wolf logo, and writes:

```text
dist\YouTube Music Playlist Downloader.exe
```

`install.ps1` is also copied into `dist` so the GUI's `Install / Repair Dependencies` button still has a helper script beside the EXE. FFmpeg is still required on the computer that runs the EXE for audio conversion.
