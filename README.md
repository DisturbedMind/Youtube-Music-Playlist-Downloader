# YouTube Music Playlist Downloader

![Moonlit wolf banner](assets/wolf-banner.png)

A small Python/Tkinter desktop app for downloading YouTube Music playlists with `yt-dlp`.

Use it only for audio you own, created, have permission to archive, or are otherwise legally allowed to save.

## What It Does

- Downloads one or more `music.youtube.com` playlists from a queue.
- Lets you paste multiple playlist URLs, reorder them, remove them, and clear finished items.
- Downloads the highest available source audio and converts every track to FLAC through FFmpeg.
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
6. Use `Kill Queue` when you want to stop the queue immediately and mark waiting playlists as stopped.

Rows move through `Queued`, `Downloading`, `Complete`, `Failed`, or `Stopped`. Failed and stopped rows can be run again by clicking `Start Queue`.

`Kill Queue` prevents any further playlists from starting and interrupts the active download at the next yt-dlp progress/checkpoint. Partial files are left in place so yt-dlp can resume them later.

## FLAC Output And FFmpeg

FFmpeg is required for:

- FLAC conversion for every downloaded song
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

The app always saves songs as `.flac`. FLAC is lossless as an output format, but it cannot improve YouTube Music's source quality; it preserves the downloaded audio as cleanly as possible during conversion.

## Cookies For YouTube Music

Public playlists often work without cookies. For private playlists, Premium account streams, unavailable tracks, or account-specific recommendations, choose one of the browser cookie options in the app:

- `Chrome`
- `Edge`
- `Firefox`
- `Brave`
- `Opera`

Recommended GUI flow:

1. Choose a cookies file path, for example `youtube-cookies.txt`.
2. Pick `Edge`, `Chrome`, or your browser under `Extract from`.
3. Click `Extract Cookies`.
4. Leave `Cookies` set to `cookies.txt file`.

Close the browser before extracting if cookie extraction fails. Direct browser cookies are also available, but a reusable cookies file is usually more predictable.

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

The app creates a subfolder named after the playlist and filenames like:

```text
001 - Track Title.flac
002 - Track Title.flac
```

## Build A Standalone EXE

Optional, if you want a double-clickable app:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install pyinstaller
pyinstaller --onefile --windowed --name "YouTube Music Playlist Downloader" .\youtube_music_playlist_downloader.py
```

The EXE will be created in `dist`. FFmpeg still needs to be installed on the machine, unless you package it separately.
