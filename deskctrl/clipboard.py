"""
Optional clipboard sync between client and server.

Uses pyperclip when available (cross-platform). Gracefully degrades if not installed.
"""

from __future__ import annotations
import threading

try:
    import pyperclip as _pyperclip
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def is_available() -> bool:
    return _AVAILABLE


def get_text() -> str | None:
    if not _AVAILABLE:
        return None
    try:
        return _pyperclip.paste()
    except Exception:
        return None


def set_text(text: str) -> bool:
    if not _AVAILABLE:
        return False
    try:
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
