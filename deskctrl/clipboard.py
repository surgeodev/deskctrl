"""
Optional clipboard sync between client and server.

Clipboard access must be thread-safe. Uses subprocess tools (xclip, wl-copy)
on Linux to avoid Qt-threading segfaults that pyperclip can trigger.
Falls back to pyperclip when no subprocess tool is available (risk of segfault
on Wayland+Qt — user should install wl-clipboard).
"""

from __future__ import annotations
import subprocess
import sys
import threading

# ── Backend detection ─────────────────────────────────────────────────────
# Priority: wl-clipboard (Wayland) > xclip (X11) > pyperclip (cross-platform)

_CLIPBOARD_CMD: tuple[str, ...] | None = None  # ("wl-paste",) or ("xclip", ...)
_CLIPBOARD_PASTE_ARGS: tuple[str, ...] = ()
_CLIPBOARD_COPY_ARGS: tuple[str, ...] = ()

def _probe_wl_clipboard() -> bool:
    try:
        subprocess.run(["wl-paste", "--version"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return True
    except Exception:
        return False

def _probe_xclip() -> bool:
    try:
        subprocess.run(["xclip", "-version"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return True
    except Exception:
        return False

if _probe_wl_clipboard():
    _CLIPBOARD_CMD = ("wl-paste",)
    _CLIPBOARD_PASTE_ARGS = ("wl-paste", "-n")  # -n = no newline
    _CLIPBOARD_COPY_ARGS = ("wl-copy", "--foreground", "--type", "text/plain")
elif _probe_xclip():
    _CLIPBOARD_CMD = ("xclip",)
    _CLIPBOARD_PASTE_ARGS = ("xclip", "-o", "-selection", "clipboard")
    _CLIPBOARD_COPY_ARGS = ("xclip", "-i", "-selection", "clipboard")

# Only pyperclip as fallback — but pyperclip can segfault on Wayland+Qt when
# called from background threads (ClipboardWatcher + receive loop).
# We only use it on platforms where subprocess tools are absent but we
# know it's safe (macOS, Windows). On Linux without wl-clipboard/xclip we
# disable clipboard sync — user should install wl-clipboard.
_PYPERCLIP_AVAILABLE = False
if not _CLIPBOARD_CMD and sys.platform != "linux":
    try:
        import pyperclip as _pyperclip
        _PYPERCLIP_AVAILABLE = True
    except ImportError:
        pass

_AVAILABLE = _CLIPBOARD_CMD is not None or _PYPERCLIP_AVAILABLE

# Subprocess is naturally thread-safe (separate process per call),
# but we keep a lock to prevent overlapping invocations.
_clipboard_lock = threading.Lock()


def is_available() -> bool:
    return _AVAILABLE


def get_text() -> str | None:
    if not _AVAILABLE:
        return None
    with _clipboard_lock:
        try:
            if _CLIPBOARD_CMD:
                result = subprocess.run(
                    list(_CLIPBOARD_PASTE_ARGS),
                    capture_output=True, timeout=2, text=True,
                )
                return result.stdout if result.returncode == 0 else None
            else:
                return _pyperclip.paste()
        except Exception:
            return None


def set_text(text: str) -> bool:
    if not _AVAILABLE:
        return False
    with _clipboard_lock:
        try:
            if _CLIPBOARD_CMD:
                result = subprocess.run(
                    list(_CLIPBOARD_COPY_ARGS),
                    input=text, capture_output=True, timeout=2, text=True,
                )
                return result.returncode == 0
            else:
                _pyperclip.copy(text)
                return True
        except Exception:
            return False


class ClipboardWatcher:
    """
    Polls clipboard every `interval` seconds, calls `on_change(text)` when it changes.
    Run in a daemon thread.
    """

    def __init__(self, on_change, interval: float = 0.5):
        self._on_change = on_change
        self._interval = interval
        self._last: str | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if not _AVAILABLE:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            current = get_text()
            if current is not None and current != self._last:
                self._last = current
                try:
                    self._on_change(current)
                except Exception:
                    pass
