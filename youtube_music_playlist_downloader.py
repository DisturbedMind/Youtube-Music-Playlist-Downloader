from __future__ import annotations

import argparse
import importlib.util
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk


APP_TITLE = "YouTube Music Playlist Downloader"
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "downloader_settings.json"
INSTALLER_PATH = SCRIPT_DIR / "install.ps1"
LOGO_PATH = SCRIPT_DIR / "assets" / "wolf-banner.png"
DEFAULT_OUTPUT = Path.home() / "Downloads" / "YouTube Music"
DEFAULT_COOKIES_FILE = SCRIPT_DIR / "youtube-cookies.txt"
DEFAULT_FORMAT = "FLAC"
DEFAULT_SLEEP_MIN = "3"
DEFAULT_SLEEP_MAX = "8"
DEFAULT_REMOTE_COMPONENTS = "ejs:github"


class DownloadCancelled(Exception):
    """Raised inside yt-dlp hooks when the user presses Stop."""


class QueueLogger:
    def __init__(self, messages: "queue.Queue[tuple[str, str]]") -> None:
        self.messages = messages

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug]"):
            return
        self.messages.put(("log", msg))

    def info(self, msg: str) -> None:
        self.messages.put(("log", msg))

    def warning(self, msg: str) -> None:
        self.messages.put(("log", f"WARNING: {msg}"))

    def error(self, msg: str) -> None:
        self.messages.put(("log", f"ERROR: {msg}"))


def strip_ansi(value: object) -> str:
    text = str(value or "")
    return re.sub(r"\x1b\[[0-9;]*m", "", text).strip()


def load_config() -> dict[str, object]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(values: dict[str, object]) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(values, indent=2), encoding="utf-8")
    except OSError:
        pass


def command_version_at_least(executable: str, args: list[str], minimum: tuple[int, int]) -> bool:
    try:
        result = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    match = re.search(r"(\d+)\.(\d+)", f"{result.stdout} {result.stderr}")
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= minimum


def default_js_runtime_setting() -> str:
    runtimes: list[str] = []
    deno = shutil.which("deno")
    node = shutil.which("node")
    if deno and command_version_at_least(deno, ["--version"], (2, 3)):
        runtimes.append("deno")
    if node and command_version_at_least(node, ["--version"], (22, 0)):
        runtimes.append("node")
    if shutil.which("qjs"):
        runtimes.append("quickjs")
    elif shutil.which("quickjs"):
        runtimes.append(f"quickjs:{shutil.which('quickjs')}")
    return ",".join(runtimes)


def dependency_status() -> list[tuple[str, bool, str]]:
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    status = [("Python 3.10+", sys.version_info >= (3, 10), python_version)]
    status.append(("yt-dlp[default]", importlib.util.find_spec("yt_dlp") is not None, "Python package"))

    ffmpeg = shutil.which("ffmpeg")
    status.append(("FFmpeg", bool(ffmpeg), ffmpeg or "needed for conversion, metadata, and cover art"))

    deno = shutil.which("deno")
    node = shutil.which("node")
    deno_ok = bool(deno and command_version_at_least(deno, ["--version"], (2, 3)))
    node_ok = bool(node and command_version_at_least(node, ["--version"], (22, 0)))
    details = []
    if deno:
        details.append(f"Deno {'OK' if deno_ok else 'too old'}")
    if node:
        details.append(f"Node {'OK' if node_ok else 'too old'}")
    status.append(("YouTube JS runtime", deno_ok or node_ok, ", ".join(details) or "install Deno 2.3+ or Node.js 22+"))
    status.append(("Installer", INSTALLER_PATH.exists(), str(INSTALLER_PATH)))
    return status


def is_cookie_decrypt_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "dpapi" in message or "failed to decrypt" in message or "could not copy chrome cookie database" in message


def is_forbidden_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "403" in message or "forbidden" in message


def is_ejs_challenge_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "n challenge" in message or "challenge solver" in message or "javascript runtime" in message


def without_browser_cookies(opts: dict[str, object]) -> dict[str, object]:
    clean_opts = dict(opts)
    clean_opts.pop("cookiesfrombrowser", None)
    return clean_opts


def without_any_cookies(opts: dict[str, object]) -> dict[str, object]:
    clean_opts = without_browser_cookies(opts)
    clean_opts.pop("cookiefile", None)
    return clean_opts


def parse_js_runtimes(value: str) -> dict[str, dict[str, str]]:
    runtimes: dict[str, dict[str, str]] = {}
    for part in str(value or "").split(","):
        part = part.strip()
        if not part:
            continue
        runtime, _, runtime_path = part.partition(":")
        runtime = runtime.strip().lower()
        runtime_path = runtime_path.strip()
        if runtime:
            runtimes[runtime] = {"path": runtime_path} if runtime_path else {}
    return runtimes


def parse_remote_components(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def merge_js_runtimes(primary: dict[str, dict[str, str]], fallback: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    merged = dict(fallback)
    merged.update(primary)
    return merged


def with_youtube_player_clients(opts: dict[str, object], clients: list[str]) -> dict[str, object]:
    tuned_opts = dict(opts)
    extractor_args = dict(tuned_opts.get("extractor_args") or {})
    youtube_args = dict(extractor_args.get("youtube") or {})
    youtube_args["player_client"] = clients
    extractor_args["youtube"] = youtube_args
    tuned_opts["extractor_args"] = extractor_args
    return tuned_opts


def with_ejs_solver_options(opts: dict[str, object]) -> dict[str, object]:
    tuned_opts = dict(opts)
    detected_runtimes = parse_js_runtimes(default_js_runtime_setting())
    existing_runtimes = dict(tuned_opts.get("js_runtimes") or {})
    runtimes = merge_js_runtimes(existing_runtimes, detected_runtimes)
    if runtimes:
        tuned_opts["js_runtimes"] = runtimes

    remote_components = list(tuned_opts.get("remote_components") or [])
    if "ejs:github" not in remote_components:
        remote_components.append("ejs:github")
    tuned_opts["remote_components"] = remote_components
    return tuned_opts


def youtube_retry_option_sets(opts: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    profiles = [
        ("with alternate YouTube clients", with_youtube_player_clients(opts, ["default", "web", "web_embedded", "mweb"])),
        ("with web-only YouTube clients", with_youtube_player_clients(opts, ["web", "web_embedded"])),
        ("with EJS challenge solver options", with_ejs_solver_options(opts)),
    ]
    if "cookiefile" in opts or "cookiesfrombrowser" in opts:
        no_cookie_opts = without_any_cookies(opts)
        profiles.extend(
            [
                ("without cookies", no_cookie_opts),
                (
                    "without cookies and alternate YouTube clients",
                    with_youtube_player_clients(no_cookie_opts, ["default", "web", "web_embedded", "mweb"]),
                ),
            ]
        )
    return profiles


def run_ydl_download_with_retries(
    ydl_module: object,
    opts: dict[str, object],
    url: str,
    messages: "queue.Queue[tuple[str, str]]",
    stop_event: threading.Event,
) -> int:
    def run_once(run_opts: dict[str, object]) -> int:
        if stop_event.is_set():
            raise DownloadCancelled("Download stopped by user")
        with ydl_module.YoutubeDL(run_opts) as ydl:
            return int(ydl.download([url]) or 0)

    try:
        result = run_once(opts)
        if result == 0:
            return result
        last_result = result
        for label, retry_opts in youtube_retry_option_sets(opts):
            if stop_event.is_set():
                raise DownloadCancelled("Download stopped by user")
            messages.put(("log", f"yt-dlp reported playlist errors; retrying {label}."))
            retry_result = run_once(retry_opts)
            if retry_result == 0:
                return retry_result
            last_result = retry_result
        return last_result
    except Exception as exc:
        if stop_event.is_set():
            raise DownloadCancelled("Download stopped by user") from exc
        if "cookiesfrombrowser" in opts and is_cookie_decrypt_error(exc):
            messages.put(("log", "Browser cookie decrypt failed; retrying without direct browser cookies."))
            return run_once(without_browser_cookies(opts))
        if is_forbidden_error(exc) or is_ejs_challenge_error(exc):
            last_error: Exception = exc
            for label, retry_opts in youtube_retry_option_sets(opts):
                try:
                    messages.put(("log", f"YouTube blocked or challenged the request; retrying {label}."))
                    return run_once(retry_opts)
                except Exception as retry_exc:
                    last_error = retry_exc
                    if is_forbidden_error(retry_exc) or is_cookie_decrypt_error(retry_exc) or is_ejs_challenge_error(retry_exc):
                        continue
                    raise
            messages.put(("log", "YouTube is still blocking the request. Refresh cookies or try again later."))
            raise last_error
        raise


def build_ydl_options(
    *,
    output_dir: Path,
    format_choice: str,
    cookies_mode: str,
    cookies_file: Path | None,
    embed_metadata: bool,
    skip_downloaded: bool,
    sleep_min: float,
    sleep_max: float,
    js_runtime: str,
    remote_components: str,
    messages: "queue.Queue[tuple[str, str]]",
    stop_event: threading.Event,
) -> dict[str, object]:
    postprocessors: list[dict[str, object]] = [
        {"key": "FFmpegExtractAudio", "preferredcodec": "flac", "preferredquality": "0"}
    ]
    ydl_format = "bestaudio/best"

    if embed_metadata:
        postprocessors.extend([{"key": "FFmpegMetadata", "add_chapters": True}, {"key": "EmbedThumbnail"}])

    def progress_hook(data: dict[str, object]) -> None:
        if stop_event.is_set():
            raise DownloadCancelled("Download stopped by user")
        status = data.get("status")
        filename = Path(str(data.get("filename") or data.get("tmpfilename") or "")).name
        if status == "downloading":
            percent = strip_ansi(data.get("_percent_str"))
            speed = strip_ansi(data.get("_speed_str"))
            eta = strip_ansi(data.get("_eta_str"))
            messages.put(("progress", f"{filename}  {percent}  {speed}  ETA {eta}"))
        elif status == "finished":
            messages.put(("progress", f"Finished {filename}; processing audio..."))

    options: dict[str, object] = {
        "format": ydl_format,
        "outtmpl": str(output_dir / "%(playlist_title).180B" / "%(playlist_index)03d - %(title).180B.%(ext)s"),
        "continuedl": True,
        "ignoreerrors": True,
        "noprogress": True,
        "overwrites": False,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "windowsfilenames": True,
        "writethumbnail": embed_metadata,
        "postprocessors": postprocessors,
        "progress_hooks": [progress_hook],
        "logger": QueueLogger(messages),
        "extractor_args": {"youtube": {"player_client": ["default", "web", "web_embedded", "mweb"]}},
    }

    if sleep_min > 0:
        options["sleep_interval"] = sleep_min
        options["max_sleep_interval"] = max(sleep_min, sleep_max)

    detected_runtimes = parse_js_runtimes(default_js_runtime_setting())
    configured_runtimes = parse_js_runtimes(js_runtime)
    js_runtimes = merge_js_runtimes(configured_runtimes, detected_runtimes)
    if js_runtimes:
        options["js_runtimes"] = js_runtimes

    components = parse_remote_components(remote_components)
    if components:
        options["remote_components"] = components

    if skip_downloaded:
        options["download_archive"] = str(output_dir / ".downloaded-archive.txt")

    if cookies_mode in {"Chrome", "Edge", "Firefox", "Brave", "Opera", "Vivaldi"}:
        options["cookiesfrombrowser"] = (cookies_mode.lower(),)
    elif cookies_mode == "cookies.txt file" and cookies_file:
        options["cookiefile"] = str(cookies_file)

    return options


class DownloaderApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x820")
        self.root.minsize(980, 700)
        self.root.configure(bg="#0f172a")

        self.messages: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.stop_event = threading.Event()
        self.kill_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.config = load_config()
        self.playlist_queue: list[dict[str, str]] = []
        self.next_playlist_id = 1

        self.url = StringVar(value=str(self.config.get("url", "")))
        self.output_dir = StringVar(value=str(self.config.get("output_dir", DEFAULT_OUTPUT)))
        self.format_choice = StringVar(value=DEFAULT_FORMAT)
        self.cookies_mode = StringVar(value=str(self.config.get("cookies_mode", "None / public playlist")))
        self.cookies_file = StringVar(value=str(self.config.get("cookies_file", DEFAULT_COOKIES_FILE)))
        self.extract_browser = StringVar(value=str(self.config.get("extract_browser", "Edge")))
        self.sleep_min = StringVar(value=str(self.config.get("sleep_min", DEFAULT_SLEEP_MIN)))
        self.sleep_max = StringVar(value=str(self.config.get("sleep_max", DEFAULT_SLEEP_MAX)))
        self.js_runtime = StringVar(value=str(self.config.get("js_runtime", default_js_runtime_setting())))
        self.remote_components = StringVar(value=str(self.config.get("remote_components", DEFAULT_REMOTE_COMPONENTS)))
        self.embed_metadata = BooleanVar(value=bool(self.config.get("embed_metadata", True)))
        self.skip_downloaded = BooleanVar(value=bool(self.config.get("skip_downloaded", True)))
        self.status = StringVar(value="Ready")
        self.queue_summary = StringVar(value="0 playlists queued")
        self.logo_image: tk.PhotoImage | None = None
        self.header_logo: tk.PhotoImage | None = None
        self.context_target: tk.Widget | None = None
        self.text_context_menu: tk.Menu | None = None

        self._build_ui()
        saved_urls = self._saved_queue_urls()
        for saved_url in saved_urls:
            self._add_playlist_to_queue(saved_url, save=False)
        if saved_urls:
            self.url.set("")
        self._update_queue_summary()
        self._poll_messages()

    def _load_logo_images(self) -> None:
        if not LOGO_PATH.exists():
            return
        try:
            self.logo_image = tk.PhotoImage(file=str(LOGO_PATH))
            self.root.iconphoto(True, self.logo_image)
            scale = max(self.logo_image.width() // 220, self.logo_image.height() // 96, 1)
            self.header_logo = self.logo_image.subsample(scale, scale)
        except tk.TclError:
            self.logo_image = None
            self.header_logo = None

    def _build_ui(self) -> None:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("App.TFrame", background="#f8fafc")
        style.configure("Hero.TFrame", background="#0f172a")
        style.configure("Card.TLabelframe", background="#ffffff", bordercolor="#cbd5e1", relief="solid")
        style.configure("Card.TLabelframe.Label", background="#ffffff", foreground="#0f172a", font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background="#f8fafc", foreground="#1e293b", font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#64748b")
        style.configure("HeroTitle.TLabel", background="#0f172a", foreground="#f8fafc", font=("Segoe UI", 22, "bold"))
        style.configure("HeroSub.TLabel", background="#0f172a", foreground="#99f6e4", font=("Segoe UI", 10))
        style.configure("Soft.TButton", background="#e5e7eb", foreground="#111827", padding=(10, 6))
        style.configure("Queue.Treeview", rowheight=28, font=("Segoe UI", 9), fieldbackground="#ffffff", background="#ffffff")
        style.configure("Queue.Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#e2e8f0", foreground="#0f172a")
        style.map("Soft.TButton", background=[("active", "#d1d5db")])

        shell = tk.Frame(self.root, bg="#0f172a")
        shell.pack(fill="both", expand=True)
        self._load_logo_images()

        hero = ttk.Frame(shell, style="Hero.TFrame", padding=(22, 18, 22, 16))
        hero.pack(fill="x")
        hero.columnconfigure(1, weight=1)
        if self.header_logo is not None:
            tk.Label(hero, image=self.header_logo, bg="#0f172a", bd=0).grid(
                row=0, column=0, rowspan=2, sticky="w", padx=(0, 16)
            )
        ttk.Label(hero, text=APP_TITLE, style="HeroTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(
            hero,
            text="Queue multiple YouTube Music playlists, keep the best available audio, and let the downloader work through them.",
            style="HeroSub.TLabel",
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))
        ttk.Label(hero, textvariable=self.queue_summary, style="HeroSub.TLabel").grid(row=0, column=2, rowspan=2, sticky="e")

        container = ttk.Frame(shell, style="App.TFrame", padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=3)
        container.columnconfigure(1, weight=2)
        container.rowconfigure(1, weight=1)

        queue_frame = ttk.LabelFrame(container, text="Playlist Queue", style="Card.TLabelframe", padding=12)
        queue_frame.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 12))
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(2, weight=1)

        add_row = ttk.Frame(queue_frame)
        add_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        add_row.columnconfigure(0, weight=1)
        ttk.Entry(add_row, textvariable=self.url).grid(row=0, column=0, sticky="ew")
        ttk.Button(add_row, text="Add", style="Soft.TButton", command=self._add_playlist_from_entry).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(add_row, text="Paste + Add", style="Soft.TButton", command=self._paste_from_clipboard).grid(row=0, column=2, padx=(8, 0))

        queue_tools = ttk.Frame(queue_frame)
        queue_tools.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(queue_tools, text="Move Up", style="Soft.TButton", command=lambda: self._move_selected(-1)).pack(side="left")
        ttk.Button(queue_tools, text="Move Down", style="Soft.TButton", command=lambda: self._move_selected(1)).pack(side="left", padx=(6, 0))
        ttk.Button(queue_tools, text="Remove", style="Soft.TButton", command=self._remove_selected).pack(side="left", padx=(6, 0))
        ttk.Button(queue_tools, text="Clear Done", style="Soft.TButton", command=self._clear_finished).pack(side="left", padx=(6, 0))

        self.queue_tree = ttk.Treeview(
            queue_frame,
            columns=("status", "url", "detail"),
            show="headings",
            selectmode="extended",
            style="Queue.Treeview",
        )
        self.queue_tree.heading("status", text="Status")
        self.queue_tree.heading("url", text="Playlist URL")
        self.queue_tree.heading("detail", text="Detail")
        self.queue_tree.column("status", width=110, anchor="w", stretch=False)
        self.queue_tree.column("url", width=470, anchor="w", stretch=True)
        self.queue_tree.column("detail", width=210, anchor="w", stretch=True)
        self.queue_tree.grid(row=2, column=0, sticky="nsew")
        queue_scroll = ttk.Scrollbar(queue_frame, orient="vertical", command=self.queue_tree.yview)
        queue_scroll.grid(row=2, column=1, sticky="ns")
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)
        self.queue_tree.tag_configure("Queued", background="#ffffff")
        self.queue_tree.tag_configure("Downloading", background="#ccfbf1")
        self.queue_tree.tag_configure("Complete", background="#dcfce7")
        self.queue_tree.tag_configure("Failed", background="#fee2e2")
        self.queue_tree.tag_configure("Stopped", background="#ffedd5")

        run_bar = ttk.Frame(queue_frame)
        run_bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        run_bar.columnconfigure(0, weight=1)
        ttk.Label(run_bar, textvariable=self.status, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(run_bar, text="Stop Queue", style="Soft.TButton", command=self._stop_download, state="disabled")
        self.stop_button.grid(row=0, column=1, padx=(8, 0))
        self.kill_button = ttk.Button(run_bar, text="Kill Queue", style="Soft.TButton", command=self._kill_queue, state="disabled")
        self.kill_button.grid(row=0, column=2, padx=(8, 0))
        self.download_button = ttk.Button(run_bar, text="Start Queue", style="Soft.TButton", command=self._start_queue)
        self.download_button.grid(row=0, column=3, padx=(8, 0))

        settings_frame = ttk.LabelFrame(container, text="Download Settings", style="Card.TLabelframe", padding=12)
        settings_frame.grid(row=0, column=1, sticky="nsew")
        settings_frame.columnconfigure(1, weight=1)

        ttk.Label(settings_frame, text="Output folder").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(settings_frame, textvariable=self.output_dir).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Button(settings_frame, text="Browse", style="Soft.TButton", command=self._choose_output).grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=5)

        ttk.Label(settings_frame, text="Audio format").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Label(
            settings_frame,
            text="FLAC output for every downloaded track",
            foreground="#0f766e",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=5)

        ttk.Label(settings_frame, text="Cookies").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Combobox(
            settings_frame,
            textvariable=self.cookies_mode,
            values=("None / public playlist", "Chrome", "Edge", "Firefox", "Brave", "Opera", "Vivaldi", "cookies.txt file"),
            state="readonly",
        ).grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(settings_frame, text="Cookies file").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(settings_frame, textvariable=self.cookies_file).grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Button(settings_frame, text="Select", style="Soft.TButton", command=self._choose_cookies).grid(row=3, column=2, sticky="ew", padx=(8, 0), pady=5)

        ttk.Label(settings_frame, text="Extract from").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Combobox(
            settings_frame,
            textvariable=self.extract_browser,
            values=("Edge", "Chrome", "Firefox", "Brave", "Opera", "Vivaldi"),
            state="readonly",
        ).grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Button(settings_frame, text="Extract", style="Soft.TButton", command=self._extract_cookies).grid(row=4, column=2, sticky="ew", padx=(8, 0), pady=5)

        ttk.Label(settings_frame, text="Sleep").grid(row=5, column=0, sticky="w", pady=5)
        sleep_frame = ttk.Frame(settings_frame)
        sleep_frame.grid(row=5, column=1, columnspan=2, sticky="ew", pady=5)
        sleep_frame.columnconfigure(0, weight=1)
        sleep_frame.columnconfigure(2, weight=1)
        ttk.Entry(sleep_frame, textvariable=self.sleep_min, width=8).grid(row=0, column=0, sticky="ew")
        ttk.Label(sleep_frame, text="to").grid(row=0, column=1, padx=6)
        ttk.Entry(sleep_frame, textvariable=self.sleep_max, width=8).grid(row=0, column=2, sticky="ew")
        ttk.Label(sleep_frame, text="seconds").grid(row=0, column=3, padx=(6, 0))

        ttk.Label(settings_frame, text="JS runtime").grid(row=6, column=0, sticky="w", pady=5)
        ttk.Entry(settings_frame, textvariable=self.js_runtime).grid(row=6, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(settings_frame, text="Remote components").grid(row=7, column=0, sticky="w", pady=5)
        ttk.Entry(settings_frame, textvariable=self.remote_components).grid(row=7, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Checkbutton(settings_frame, text="Embed metadata and cover art", variable=self.embed_metadata).grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(settings_frame, text="Skip tracks already in archive", variable=self.skip_downloaded).grid(row=9, column=0, columnspan=3, sticky="w", pady=(4, 0))

        tools = ttk.Frame(settings_frame)
        tools.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        ttk.Button(tools, text="Check Dependencies", style="Soft.TButton", command=self._check_dependencies).pack(side="left")
        ttk.Button(tools, text="Install / Repair", style="Soft.TButton", command=self._install_dependencies).pack(side="left", padx=(8, 0))

        log_frame = ttk.LabelFrame(container, text="Download Log", style="Card.TLabelframe", padding=10)
        log_frame.grid(row=1, column=1, sticky="nsew", pady=(12, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = ttk.Treeview(log_frame, show="tree", selectmode="browse", style="Queue.Treeview")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self._install_text_context_menu()

    def _install_text_context_menu(self) -> None:
        self.text_context_menu = tk.Menu(self.root, tearoff=False)
        self.text_context_menu.add_command(label="Cut", command=lambda: self._text_context_action("cut"))
        self.text_context_menu.add_command(label="Copy", command=lambda: self._text_context_action("copy"))
        self.text_context_menu.add_command(label="Paste", command=lambda: self._text_context_action("paste"))
        self.text_context_menu.add_separator()
        self.text_context_menu.add_command(label="Select All", command=lambda: self._text_context_action("select_all"))

        for widget_class in ("Entry", "TEntry", "TCombobox"):
            self.root.bind_class(widget_class, "<Button-3>", self._show_text_context_menu, add="+")
            self.root.bind_class(widget_class, "<Shift-F10>", self._show_text_context_menu, add="+")
            self.root.bind_class(widget_class, "<Control-a>", self._select_all_text_event, add="+")
            self.root.bind_class(widget_class, "<Control-A>", self._select_all_text_event, add="+")

    def _show_text_context_menu(self, event: tk.Event) -> str:
        widget = event.widget
        if self.text_context_menu is None:
            return "break"

        self.context_target = widget
        try:
            widget.focus_set()
            if getattr(event, "num", None) == 3 and not self._has_text_selection(widget):
                widget.icursor(widget.index(f"@{event.x}"))
        except tk.TclError:
            pass

        readonly = self._is_text_widget_readonly(widget)
        self.text_context_menu.entryconfigure("Cut", state="disabled" if readonly else "normal")
        self.text_context_menu.entryconfigure("Paste", state="disabled" if readonly else "normal")
        self.text_context_menu.entryconfigure("Copy", state="normal")
        self.text_context_menu.entryconfigure("Select All", state="normal")

        self.text_context_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _text_context_action(self, action: str) -> None:
        widget = self.context_target
        if widget is None:
            return
        try:
            if action == "cut":
                widget.event_generate("<<Cut>>")
            elif action == "copy":
                widget.event_generate("<<Copy>>")
            elif action == "paste":
                widget.event_generate("<<Paste>>")
            elif action == "select_all":
                self._select_all_text_widget(widget)
        except tk.TclError:
            pass

    def _select_all_text_event(self, event: tk.Event) -> str:
        self._select_all_text_widget(event.widget)
        return "break"

    def _select_all_text_widget(self, widget: tk.Widget) -> None:
        try:
            widget.focus_set()
            widget.selection_range(0, tk.END)
            widget.icursor(tk.END)
        except tk.TclError:
            pass

    def _has_text_selection(self, widget: tk.Widget) -> bool:
        try:
            widget.selection_get()
            return True
        except tk.TclError:
            return False

    def _is_text_widget_readonly(self, widget: tk.Widget) -> bool:
        try:
            return str(widget.cget("state")) in {"disabled", "readonly"}
        except tk.TclError:
            return False

    def _saved_queue_urls(self) -> list[str]:
        saved_queue = self.config.get("queue")
        if isinstance(saved_queue, list):
            return [str(url).strip() for url in saved_queue if str(url).strip()]
        saved_url = str(self.config.get("url", "")).strip()
        return [saved_url] if saved_url else []

    def _make_playlist_item(self, url: str, status: str = "Queued", detail: str = "") -> dict[str, str]:
        item = {"id": str(self.next_playlist_id), "url": url, "status": status, "detail": detail}
        self.next_playlist_id += 1
        return item

    def _add_playlist_to_queue(self, url: str, save: bool = True) -> None:
        url = url.strip()
        if not url:
            return
        if any(item["url"] == url and item["status"] != "Complete" for item in self.playlist_queue):
            self._append_log(f"Already queued: {url}")
            return
        item = self._make_playlist_item(url)
        self.playlist_queue.append(item)
        self.queue_tree.insert("", "end", iid=item["id"], values=(item["status"], item["url"], item["detail"]), tags=(item["status"],))
        self._update_queue_summary()
        if save:
            self._save_current_config()

    def _extract_urls_from_text(self, text: str) -> list[str]:
        urls: list[str] = []
        for part in re.split(r"[\r\n]+", text):
            candidate = part.strip()
            if candidate:
                urls.append(candidate)
        return urls

    def _add_playlist_from_entry(self) -> None:
        urls = self._extract_urls_from_text(self.url.get())
        if not urls:
            messagebox.showinfo(APP_TITLE, "Paste one or more playlist URLs first.")
            return
        for url in urls:
            self._add_playlist_to_queue(url, save=False)
        self.url.set("")
        self._save_current_config()

    def _paste_from_clipboard(self) -> None:
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo(APP_TITLE, "Clipboard is empty or does not contain text.")
            return
        urls = self._extract_urls_from_text(text)
        if not urls:
            messagebox.showinfo(APP_TITLE, "Clipboard does not contain any playlist URLs.")
            return
        for url in urls:
            self._add_playlist_to_queue(url, save=False)
        self._save_current_config()

    def _selected_ids(self) -> list[str]:
        return list(self.queue_tree.selection())

    def _remove_selected(self) -> None:
        selected = set(self._selected_ids())
        if not selected:
            return
        self.playlist_queue = [item for item in self.playlist_queue if item["id"] not in selected]
        for item_id in selected:
            if self.queue_tree.exists(item_id):
                self.queue_tree.delete(item_id)
        self._update_queue_summary()
        self._save_current_config()

    def _clear_finished(self) -> None:
        removable = {item["id"] for item in self.playlist_queue if item["status"] in {"Complete", "Failed", "Stopped"}}
        self.playlist_queue = [item for item in self.playlist_queue if item["id"] not in removable]
        for item_id in removable:
            if self.queue_tree.exists(item_id):
                self.queue_tree.delete(item_id)
        self._update_queue_summary()
        self._save_current_config()

    def _move_selected(self, direction: int) -> None:
        selected = self._selected_ids()
        if len(selected) != 1:
            return
        item_id = selected[0]
        index = next((i for i, item in enumerate(self.playlist_queue) if item["id"] == item_id), None)
        if index is None:
            return
        new_index = index + direction
        if new_index < 0 or new_index >= len(self.playlist_queue):
            return
        self.playlist_queue[index], self.playlist_queue[new_index] = self.playlist_queue[new_index], self.playlist_queue[index]
        self._redraw_queue_tree()
        self.queue_tree.selection_set(item_id)
        self._save_current_config()

    def _redraw_queue_tree(self) -> None:
        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)
        for item in self.playlist_queue:
            self.queue_tree.insert("", "end", iid=item["id"], values=(item["status"], item["url"], item["detail"]), tags=(item["status"],))

    def _set_queue_status(self, item_id: str, status: str, detail: str = "") -> None:
        for item in self.playlist_queue:
            if item["id"] == item_id:
                item["status"] = status
                item["detail"] = detail
                break
        if self.queue_tree.exists(item_id):
            item = next((entry for entry in self.playlist_queue if entry["id"] == item_id), None)
            if item:
                self.queue_tree.item(item_id, values=(item["status"], item["url"], item["detail"]), tags=(status,))
                self.queue_tree.see(item_id)
        self._update_queue_summary()

    def _update_queue_summary(self) -> None:
        total = len(self.playlist_queue)
        pending = sum(1 for item in self.playlist_queue if item["status"] in {"Queued", "Failed", "Stopped"})
        complete = sum(1 for item in self.playlist_queue if item["status"] == "Complete")
        if total == 0:
            self.queue_summary.set("0 playlists queued")
        else:
            self.queue_summary.set(f"{pending} pending / {complete} done / {total} total")

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_dir.get() or str(DEFAULT_OUTPUT))
        if folder:
            self.output_dir.set(folder)

    def _choose_cookies(self) -> None:
        path = filedialog.askopenfilename(title="Select cookies.txt", filetypes=(("Cookies files", "*.txt"), ("All files", "*.*")))
        if path:
            self.cookies_file.set(path)
            self.cookies_mode.set("cookies.txt file")

    def _read_float(self, value: StringVar, default: float) -> float:
        try:
            return max(0.0, float(value.get().strip()))
        except ValueError:
            return default

    def _settings_snapshot(self) -> dict[str, object]:
        sleep_min = self._read_float(self.sleep_min, float(DEFAULT_SLEEP_MIN))
        sleep_max = self._read_float(self.sleep_max, float(DEFAULT_SLEEP_MAX))
        if sleep_max < sleep_min:
            sleep_max = sleep_min
        return {
            "format_choice": DEFAULT_FORMAT,
            "cookies_mode": self.cookies_mode.get(),
            "cookies_file": self.cookies_file.get().strip(),
            "extract_browser": self.extract_browser.get(),
            "sleep_min": sleep_min,
            "sleep_max": sleep_max,
            "js_runtime": self.js_runtime.get().strip(),
            "remote_components": self.remote_components.get().strip(),
            "embed_metadata": self.embed_metadata.get(),
            "skip_downloaded": self.skip_downloaded.get(),
        }

    def _save_current_config(self, url: str | None = None, output_dir: Path | None = None) -> None:
        settings = self._settings_snapshot()
        save_config(
            {
                "url": url if url is not None else self.url.get().strip(),
                "output_dir": str(output_dir if output_dir is not None else self.output_dir.get()),
                "format": DEFAULT_FORMAT,
                "cookies_mode": settings["cookies_mode"],
                "cookies_file": settings["cookies_file"],
                "extract_browser": settings["extract_browser"],
                "sleep_min": settings["sleep_min"],
                "sleep_max": settings["sleep_max"],
                "js_runtime": settings["js_runtime"],
                "remote_components": settings["remote_components"],
                "embed_metadata": settings["embed_metadata"],
                "skip_downloaded": settings["skip_downloaded"],
                "queue": [
                    item["url"]
                    for item in self.playlist_queue
                    if item["status"] in {"Queued", "Failed", "Stopped", "Downloading"}
                ],
            }
        )

    def _start_background_task(self, target: object, args: tuple[object, ...], allow_kill: bool = False) -> bool:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_TITLE, "A task is already running.")
            return False
        self.stop_event.clear()
        self.kill_event.clear()
        self.download_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.kill_button.configure(state="normal" if allow_kill else "disabled")
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()
        return True

    def _check_dependencies(self) -> None:
        self._append_log("Dependency check:")
        for name, ok, detail in dependency_status():
            self._append_log(f"  {'OK' if ok else 'MISSING'}  {name}: {detail}")

    def _install_dependencies(self) -> None:
        if not INSTALLER_PATH.exists():
            messagebox.showerror(APP_TITLE, f"Installer not found:\n{INSTALLER_PATH}")
            return
        self.status.set("Installing dependencies")
        self._append_log("Starting install.ps1 dependency repair...")
        self._start_background_task(self._install_worker, tuple())

    def _install_worker(self) -> None:
        try:
            command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INSTALLER_PATH)]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            assert process.stdout is not None
            for line in process.stdout:
                self.messages.put(("log", line.rstrip()))
            exit_code = process.wait()
            if exit_code == 0:
                self.messages.put(("done", "Dependency install/repair complete."))
            else:
                self.messages.put(("done", f"Dependency installer exited with code {exit_code}."))
        except Exception as exc:
            self.messages.put(("log", traceback.format_exc()))
            self.messages.put(("done", f"Dependency install failed: {exc}"))

    def _extract_cookies(self) -> None:
        path = Path(self.cookies_file.get().strip() or DEFAULT_COOKIES_FILE).expanduser()
        browser = self.extract_browser.get().strip().lower()
        self.cookies_file.set(str(path))
        self._save_current_config()
        self.status.set("Extracting cookies")
        self._append_log(f"Extracting cookies from {browser} to {path}...")
        self._start_background_task(self._extract_cookies_worker, (browser, path))

    def _extract_cookies_worker(self, browser: str, cookies_file: Path) -> None:
        try:
            import yt_dlp

            cookies_file.parent.mkdir(parents=True, exist_ok=True)
            opts = {"cookiesfrombrowser": (browser,), "quiet": False, "logger": QueueLogger(self.messages)}
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.cookiejar.save(str(cookies_file), ignore_discard=True, ignore_expires=True)
            self.messages.put(("set_cookies_file", str(cookies_file)))
            self.messages.put(("done", f"Saved cookies file: {cookies_file}"))
        except Exception as exc:
            self.messages.put(("log", traceback.format_exc()))
            self.messages.put(("done", f"Cookie extraction failed: {exc}. Close the browser and try again."))

    def _validate_download_settings(self, settings: dict[str, object]) -> bool:
        if importlib.util.find_spec("yt_dlp") is None:
            messagebox.showerror(APP_TITLE, "yt-dlp is not installed.\n\nClick 'Install / Repair Dependencies' or run .\\run_downloader.ps1")
            return False
        if settings["cookies_mode"] == "cookies.txt file" and not settings["cookies_file"]:
            messagebox.showerror(APP_TITLE, "Select a cookies.txt file, or choose a browser/none cookie mode.")
            return False
        if not shutil.which("ffmpeg"):
            messagebox.showerror(
                APP_TITLE,
                "FFmpeg is required because every downloaded song is converted to FLAC.\n\n"
                "Click 'Install / Repair Dependencies' or install FFmpeg manually.",
            )
            return False
        return True

    def _start_queue(self) -> None:
        if self.url.get().strip():
            self._add_playlist_from_entry()

        if not self.playlist_queue:
            messagebox.showinfo(APP_TITLE, "Add at least one playlist to the queue first.")
            return

        questionable = [
            item["url"]
            for item in self.playlist_queue
            if item["status"] in {"Queued", "Failed", "Stopped"}
            and "music.youtube.com" not in item["url"]
            and "youtube.com" not in item["url"]
        ]
        if questionable and not messagebox.askyesno(
            APP_TITLE,
            "One or more queued items do not look like YouTube Music URLs. Try them anyway?",
        ):
            return

        settings = self._settings_snapshot()
        if not self._validate_download_settings(settings):
            return

        output_dir = Path(self.output_dir.get()).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)

        targets = [
            {"id": item["id"], "url": item["url"]}
            for item in self.playlist_queue
            if item["status"] in {"Queued", "Failed", "Stopped"}
        ]
        if not targets:
            messagebox.showinfo(APP_TITLE, "There are no pending playlists. Use Clear Done or add another playlist.")
            return

        self._save_current_config(output_dir=output_dir)
        self._append_log(f"Starting queue with {len(targets)} playlist(s)...")
        self.status.set("Queue running")
        self._start_background_task(self._queue_worker, (targets, output_dir, settings), allow_kill=True)

    def _queue_worker(self, targets: list[dict[str, str]], output_dir: Path, settings: dict[str, object]) -> None:
        try:
            import yt_dlp

            completed = 0
            failed = 0
            for index, target in enumerate(targets, start=1):
                item_id = target["id"]
                url = target["url"]
                if self.kill_event.is_set():
                    self.messages.put(("queue_status", {"id": item_id, "status": "Stopped", "detail": "Killed"}))
                    continue
                if self.stop_event.is_set():
                    self.messages.put(("queue_status", {"id": item_id, "status": "Stopped", "detail": "Waiting"}))
                    continue

                self.messages.put(("queue_status", {"id": item_id, "status": "Downloading", "detail": f"{index}/{len(targets)}"}))
                self.messages.put(("log", f"== Playlist {index}/{len(targets)} =="))
                self.messages.put(("log", url))

                options = build_ydl_options(
                    output_dir=output_dir,
                    format_choice=str(settings["format_choice"]),
                    cookies_mode=str(settings["cookies_mode"]),
                    cookies_file=Path(str(settings["cookies_file"])) if settings["cookies_file"] else None,
                    embed_metadata=bool(settings["embed_metadata"]),
                    skip_downloaded=bool(settings["skip_downloaded"]),
                    sleep_min=float(settings["sleep_min"]),
                    sleep_max=float(settings["sleep_max"]),
                    js_runtime=str(settings["js_runtime"]),
                    remote_components=str(settings["remote_components"]),
                    messages=self.messages,
                    stop_event=self.stop_event,
                )

                try:
                    result = run_ydl_download_with_retries(yt_dlp, options, url, self.messages, self.stop_event)
                except DownloadCancelled:
                    detail = "Killed; partial files can resume" if self.kill_event.is_set() else "Partial files can resume"
                    message = "Queue killed. Partial files can be resumed later." if self.kill_event.is_set() else "Queue stopped by user. Partial files can be resumed later."
                    self.messages.put(("queue_status", {"id": item_id, "status": "Stopped", "detail": detail}))
                    self.messages.put(("done", message))
                    return
                except Exception as exc:
                    failed += 1
                    self.messages.put(("log", traceback.format_exc()))
                    self.messages.put(("queue_status", {"id": item_id, "status": "Failed", "detail": str(exc)[:160]}))
                    continue

                if result == 0:
                    completed += 1
                    self.messages.put(("queue_status", {"id": item_id, "status": "Complete", "detail": "Saved"}))
                else:
                    failed += 1
                    self.messages.put(("queue_status", {"id": item_id, "status": "Failed", "detail": "yt-dlp reported errors"}))

            if failed:
                self.messages.put(("done", f"Queue finished: {completed} complete, {failed} failed. Check the log above."))
            else:
                self.messages.put(("done", f"Queue complete. Files are in: {output_dir}"))
        except DownloadCancelled:
            self.messages.put(("done", "Queue stopped by user. Partial files can be resumed later."))
        except Exception as exc:
            self.messages.put(("log", traceback.format_exc()))
            self.messages.put(("done", f"Queue failed: {exc}"))

    def _stop_download(self) -> None:
        self.stop_event.set()
        self.status.set("Stopping after current step...")
        self._append_log("Stop requested.")

    def _kill_queue(self) -> None:
        self.kill_event.set()
        self.stop_event.set()
        self.status.set("Kill switch engaged...")
        self._append_log("Kill switch engaged. No further playlists will start.")
        for item in self.playlist_queue:
            if item["status"] in {"Queued", "Failed", "Stopped"}:
                self._set_queue_status(item["id"], "Stopped", "Killed")
        self._save_current_config()

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, text = self.messages.get_nowait()
                if kind in {"log", "progress"}:
                    self._append_log(str(text))
                    if kind == "progress":
                        self.status.set(str(text))
                elif kind == "queue_status":
                    payload = dict(text)
                    self._set_queue_status(str(payload["id"]), str(payload["status"]), str(payload.get("detail", "")))
                elif kind == "set_cookies_file":
                    self.cookies_file.set(str(text))
                    self.cookies_mode.set("cookies.txt file")
                    self._save_current_config()
                elif kind == "done":
                    self._append_log(str(text))
                    self.status.set(str(text))
                    self.download_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.kill_button.configure(state="disabled")
                    self._save_current_config()
        except queue.Empty:
            pass
        self.root.after(150, self._poll_messages)

    def _append_log(self, text: str) -> None:
        text = text.replace("\r", "").strip()
        if not text:
            return
        for line in text.splitlines():
            self.log_text.insert("", "end", text=line)
        children = self.log_text.get_children()
        if children:
            self.log_text.see(children[-1])


def print_dependency_status() -> None:
    print("Dependency check:")
    for name, ok, detail in dependency_status():
        print(f"  {'OK' if ok else 'MISSING'}  {name}: {detail}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download YouTube Music playlists with a Tkinter GUI.")
    parser.add_argument("--check-deps", action="store_true", help="Print dependency status and exit.")
    parser.add_argument("--install-deps", action="store_true", help="Run install.ps1 dependency repair and exit.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.check_deps:
        print_dependency_status()
        return
    if args.install_deps:
        if not INSTALLER_PATH.exists():
            print(f"Installer not found: {INSTALLER_PATH}", file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(subprocess.call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INSTALLER_PATH)]))

    root = Tk()
    DownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
