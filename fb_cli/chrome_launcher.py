"""Manage a long-lived debug Chrome bound to fb-cli.

We keep a dedicated Chrome user-data-dir at `~/.fb-cli/chrome-profile/` so the
user logs in once (with "Save login info" checked) and stays authed for ~1
year — the lifetime of Facebook's `xs` cookie. We launch Chrome on
:9222 with `--remote-debugging-port`, write the PID to
`~/.fb-cli/chrome.pid`, and reuse the running instance on subsequent calls.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_PORT = 9222
DEFAULT_DEBUG_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
DEFAULT_LANDING = "https://www.facebook.com/marketplace/"

FB_HOME = Path(os.environ.get("FB_CLI_HOME") or (Path.home() / ".fb-cli"))
PROFILE_DIR = FB_HOME / "chrome-profile"
PID_FILE = FB_HOME / "chrome.pid"
LOG_FILE = FB_HOME / "chrome.log"


class ChromeLauncherError(RuntimeError):
    """Could not start or talk to the managed Chrome."""


# --- discovery ----------------------------------------------------------------


def find_chrome() -> str:
    """Locate a Chrome / Chromium / Edge binary on this OS."""
    explicit = os.environ.get("FB_CLI_CHROME")
    if explicit and Path(explicit).exists():
        return explicit

    if platform.system() == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    elif platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        candidates = []

    for c in candidates:
        if Path(c).exists():
            return c

    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge"):
        path = shutil.which(name)
        if path:
            return path

    raise ChromeLauncherError(
        "No Chrome / Chromium / Edge binary found. Install Chrome or set FB_CLI_CHROME=/path/to/chrome."
    )


def default_user_profile() -> Path | None:
    """Best-effort path to the user's normal Chrome 'Default' profile dir."""
    home = Path.home()
    if platform.system() == "Darwin":
        return home / "Library/Application Support/Google/Chrome/Default"
    if platform.system() == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "Google/Chrome/User Data/Default"
        return None
    return home / ".config/google-chrome/Default"


# --- lifecycle ----------------------------------------------------------------


def is_running(debug_url: str = DEFAULT_DEBUG_URL, *, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(debug_url.rstrip("/") + "/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def status(debug_url: str = DEFAULT_DEBUG_URL) -> dict[str, Any]:
    info: dict[str, Any] = {
        "debug_url": debug_url,
        "profile_dir": str(PROFILE_DIR),
        "pid_file": str(PID_FILE),
        "running": False,
        "pid": None,
        "version": None,
    }
    pid = _read_pid()
    if pid and _process_alive(pid):
        info["pid"] = pid
    if is_running(debug_url):
        info["running"] = True
        try:
            with urllib.request.urlopen(debug_url.rstrip("/") + "/json/version", timeout=2) as resp:
                info["version"] = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
    return info


def start(
    *,
    port: int = DEFAULT_PORT,
    copy_profile: bool = False,
    landing_url: str | None = DEFAULT_LANDING,
    headless: bool = False,
    wait_seconds: float = 15.0,
) -> dict[str, Any]:
    """Start the managed debug Chrome, or no-op if already up."""
    debug_url = f"http://127.0.0.1:{port}"
    if is_running(debug_url):
        return status(debug_url)

    FB_HOME.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    fresh_profile = not any(PROFILE_DIR.iterdir())
    if copy_profile and fresh_profile:
        _seed_profile_from_default()

    chrome = find_chrome()
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=ChromeWhatsNewUI",
        "--disable-session-crashed-bubble",
        "--restore-last-session=false",
    ]
    if headless:
        args.extend(["--headless=new", "--disable-gpu"])
    if landing_url:
        args.append(landing_url)

    log_fh = LOG_FILE.open("ab")
    proc = subprocess.Popen(
        args,
        stdout=log_fh,
        stderr=log_fh,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if is_running(debug_url, timeout=1):
            return status(debug_url)
        if proc.poll() is not None:
            tail = ""
            try:
                tail = LOG_FILE.read_text(errors="replace")[-1000:]
            except OSError:
                pass
            raise ChromeLauncherError(
                f"Chrome exited (code {proc.returncode}) before binding :{port}. "
                f"Log tail:\n{tail}"
            )
        time.sleep(0.25)
    raise ChromeLauncherError(
        f"Chrome did not bind :{port} within {wait_seconds:.0f}s. See {LOG_FILE}."
    )


def stop() -> bool:
    pid = _read_pid()
    killed = False
    if pid and _process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
            for _ in range(40):
                if not _process_alive(pid):
                    break
                time.sleep(0.1)
            if _process_alive(pid):
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    return killed


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --- profile seeding ----------------------------------------------------------

# Files we copy from the user's Default profile into our scraping profile so a
# brand-new Chrome already has their existing facebook.com login. Limited to
# auth-relevant state — we don't drag in extensions, history, or bookmarks.
_PROFILE_COPY = (
    "Cookies",
    "Cookies-journal",
    "Network/Cookies",
    "Network/Cookies-journal",
    "Login Data",
    "Login Data-journal",
    "Local State",
    "Preferences",
)


def _seed_profile_from_default() -> None:
    src = default_user_profile()
    if src is None or not src.exists():
        return
    # Local State lives one level up alongside profiles
    parent_local_state = src.parent / "Local State"
    dest = PROFILE_DIR
    dest_default = dest / "Default"
    dest_default.mkdir(parents=True, exist_ok=True)

    for rel in _PROFILE_COPY:
        s = (src.parent / "Local State") if rel == "Local State" else (src / rel)
        if rel == "Local State":
            d = dest / "Local State"
            s = parent_local_state
        else:
            d = dest_default / rel
        if not s.exists():
            continue
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
        except OSError:
            # Cookies DB is locked while Chrome is running — skip and let the
            # user log in interactively.
            continue
