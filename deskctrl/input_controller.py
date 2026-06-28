"""
Cross-platform input injection on the server side.

Receives keysym + mouse events from the protocol and injects them into the
local OS via pynput (Linux/macOS/Windows for VK keys) and win32_input
(Windows Unicode char keys).

On Linux: also supports Xlib-based key injection via pynput.
"""

from __future__ import annotations
import sys
import threading
from . import win32_input
from .keymap import PYNPUT_SPECIAL_KEYSYMS, XK_Shift_L, XK_Shift_R

try:
    from pynput.keyboard import Controller as KbController, Key as KbKey, KeyCode
    from pynput.mouse import Controller as MouseController, Button as MouseButton
    _PYNPUT_AVAILABLE = True
except ImportError:
    _PYNPUT_AVAILABLE = False

_IS_WINDOWS = sys.platform == "win32"

# Button index → pynput Button
_BUTTON_MAP = {}
if _PYNPUT_AVAILABLE:
    _BUTTON_MAP = {
        1: MouseButton.left,
        2: MouseButton.middle,
        3: MouseButton.right,
    }


def _keysym_to_pynput(keysym: int):
    """
    Convert an X11 keysym to a pynput Key or KeyCode.
    """
    if keysym in PYNPUT_SPECIAL_KEYSYMS:
        return PYNPUT_SPECIAL_KEYSYMS[keysym]
    if 0x20 <= keysym <= 0x10FFFF:
        return KeyCode.from_char(chr(keysym))
    return None


class InputController:
    def __init__(self):
        if not _PYNPUT_AVAILABLE:
            raise RuntimeError("pynput is required for input injection.")
        self._kb = KbController()
        self._mouse = MouseController()
        self._lock = threading.Lock()

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def key_down(self, keysym: int) -> None:
        with self._lock:
            if _IS_WINDOWS and win32_input.is_available():
                handled = win32_input.send_key_down(keysym)
                if handled:
                    return
            # Fallback: pynput
            key = _keysym_to_pynput(keysym)
            if key is not None:
                try:
                    self._kb.press(key)
                except Exception:
                    pass

    def key_up(self, keysym: int) -> None:
        with self._lock:
            if _IS_WINDOWS and win32_input.is_available():
                handled = win32_input.send_key_up(keysym)
                if handled:
                    return
            key = _keysym_to_pynput(keysym)
            if key is not None:
                try:
                    self._kb.release(key)
                except Exception:
                    pass

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def mouse_move(self, x: int, y: int) -> None:
        with self._lock:
            try:
                self._mouse.position = (x, y)
            except Exception:
                pass

    def mouse_press(self, x: int, y: int, button: int) -> None:
        with self._lock:
            btn = _BUTTON_MAP.get(button, MouseButton.left)
            try:
                self._mouse.position = (x, y)
                self._mouse.press(btn)
            except Exception:
                pass

    def mouse_release(self, x: int, y: int, button: int) -> None:
        with self._lock:
            btn = _BUTTON_MAP.get(button, MouseButton.left)
            try:
                self._mouse.position = (x, y)
                self._mouse.release(btn)
            except Exception:
                pass

    def mouse_scroll(self, dx: int, dy: int) -> None:
        with self._lock:
            try:
                self._mouse.scroll(dx, dy)
            except Exception:
                pass

    def reset(self) -> None:
        """Called on new connection to clear any leftover state."""
        if _IS_WINDOWS and win32_input.is_available():
            win32_input.reset()
