"""Platform-aware input capture & simulation for X11 and Wayland.

Abstracts away the differences between input backends so higher-level
modules (MonitorControl, Server InputSimulator) work everywhere.

Backends tried in order:
  1. libei  — native Wayland input emulation (portal-based, future-proof)
  2. pynput — X11 / XWayland (works on most current desktops)
  3. uinput — Linux kernel virtual input (needs permissions)
  4. ydotool — Wayland CLI fallback (needs ydotoold running)

Mouse POSITION CAPTURE on Wayland is fundamentally limited:
  - Wayland does NOT expose absolute mouse position to clients (privacy).
  - We approximate it via last-known-position + relative delta tracking,
    or use the compositor's wlr-virtual-pointer protocol.
  - For Monitor Control mode, the mouse edge detection works by
    tracking the cursor position via pynput (XWayland) or by using
    the screen edge heuristics with libei.
"""

import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Optional, Callable, Tuple

log = logging.getLogger(__name__)


# ── Environment detection ──────────────────────────────────────────────────

def is_wayland() -> bool:
    """Detect if we're running under Wayland (vs X11/XWayland)."""
    return bool(os.environ.get("WAYLAND_DISPLAY")) or \
           os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def is_x11() -> bool:
    """Detect if we're running under X11 (or XWayland)."""
    return bool(os.environ.get("DISPLAY")) and not is_wayland()


def is_xwayland() -> bool:
    """Detect if we're under XWayland (X11 on Wayland)."""
    return bool(os.environ.get("DISPLAY")) and is_wayland()


def available_backends() -> list:
    """Return available input backends in order of preference."""
    backends = []

    # 1. libei (Wayland native)
    try:
        import ei  # noqa: F401
        backends.append("libei")
    except ImportError:
        pass

    # 2. pynput (X11 / XWayland)
    try:
        from pynput import mouse, keyboard  # noqa: F401
        if is_x11() or is_xwayland():
            backends.append("pynput")
    except ImportError:
        pass

    # 3. uinput (Linux direct)
    if shutil.which("evtest"):
        try:
            import uinput  # noqa: F401
            backends.append("uinput")
        except ImportError:
            pass

    # 4. ydotool (Wayland CLI)
    if shutil.which("ydotool"):
        backends.append("ydotool")

    return backends


# ═══════════════════════════════════════════════════════════════════════════
# Mouse Capture — track mouse position
# ═══════════════════════════════════════════════════════════════════════════

class MouseCapture:
    """Capture mouse movement events and report position.

    Provides a unified callback interface regardless of backend.
    """

    def __init__(self, on_move: Callable[[float, float], None],
                 on_click: Optional[Callable] = None,
                 on_scroll: Optional[Callable] = None,
                 backend: str = "auto"):
        self._on_move = on_move
        self._on_click = on_click
        self._on_scroll = on_scroll
        self._backend = backend
        self._listener = None
        self._running = False

    def start(self):
        """Start capturing mouse events."""
        if self._backend == "auto":
            backends = available_backends()
            if "pynput" in backends:
                self._backend = "pynput"
            elif "libei" in backends:
                self._backend = "libei"
            else:
                self._backend = "pynput"  # Will raise ImportError

        self._running = True

        if self._backend == "pynput":
            self._start_pynput()
        elif self._backend == "libei":
            self._start_libei()
        else:
            raise RuntimeError(f"Unsupported mouse backend: {self._backend}")

    def stop(self):
        self._running = False
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def _start_pynput(self):
        from pynput import mouse

        def on_move(x, y):
            if self._running:
                self._on_move(x, y)

        def on_click(x, y, button, pressed):
            if self._running and self._on_click:
                self._on_click(x, y, button, pressed)

        def on_scroll(x, y, dx, dy):
            if self._running and self._on_scroll:
                self._on_scroll(x, y, dx, dy)

        self._listener = mouse.Listener(
            on_move=on_move, on_click=on_click, on_scroll=on_scroll
        )
        self._listener.start()

    def _start_libei(self):
        """libei-based mouse capture (Wayland native)."""
        try:
            import ei
        except ImportError:
            log.warning("libei not available — falling back")
            if is_x11() or is_xwayland():
                self._start_pynput()
            else:
                raise

        # libei EIS client implementation
        # Requires: ei module with EIS (Emulated Input Server) support
        # This is a placeholder for future implementation
        log.warning("libei mouse capture not yet implemented, "
                    "falling back to pynput (XWayland)")
        if is_x11() or is_xwayland():
            self._start_pynput()
        else:
            raise RuntimeError(
                "Mouse position capture on native Wayland is not yet supported.\n"
                "  Options:\n"
                "    1. Use XWayland compatibility\n"
                "    2. Install libei with Python bindings\n"
                "    3. Use a compositor with wlr-virtual-pointer support"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Input Simulation — send mouse/keyboard events to the OS
# ═══════════════════════════════════════════════════════════════════════════

class InputSimulator:
    """Simulate input events on the host machine.

    Used by the SERVER to execute received input commands.
    """

    def __init__(self, backend: str = "auto"):
        self._backend = backend
        self._pynput_mouse = None
        self._pynput_keyboard = None
        self._uinput_dev = None
        self._initialized = False

    def init(self):
        """Initialize the backend."""
        if self._initialized:
            return

        if self._backend == "auto":
            backends = available_backends()
            if "pynput" in backends:
                self._backend = "pynput"
            elif "uinput" in backends:
                self._backend = "uinput"
            elif "ydotool" in backends:
                self._backend = "ydotool"
            else:
                self._backend = "pynput"

        if self._backend == "pynput":
            from pynput.mouse import Controller as MouseCtrl
            from pynput.keyboard import Controller as KeyCtrl
            self._pynput_mouse = MouseCtrl()
            self._pynput_keyboard = KeyCtrl()
        elif self._backend == "uinput":
            self._init_uinput()
        elif self._backend == "ydotool":
            pass  # No init needed
        else:
            raise RuntimeError(f"Unsupported input backend: {self._backend}")

        self._initialized = True

    def move_mouse(self, x: float, y: float, relative: bool = False):
        self.init()
        if self._backend == "pynput":
            if relative:
                self._pynput_mouse.move(x, y)
            else:
                self._pynput_mouse.position = (int(x), int(y))
        elif self._backend == "uinput":
            self._uinput_move(x, y, relative)
        elif self._backend == "ydotool":
            self._ydotool_mousemove(int(x), int(y), relative)

    def click_mouse(self, button: int, pressed: bool, x: float, y: float):
        self.init()
        self.move_mouse(x, y)
        if self._backend == "pynput":
            btn = self._pynput_button(button)
            if pressed:
                self._pynput_mouse.press(btn)
            else:
                self._pynput_mouse.release(btn)
        elif self._backend == "uinput":
            self._uinput_click(button, pressed)
        elif self._backend == "ydotool":
            btn_code = self._ydotool_button_code(button)
            action = "mousedown" if pressed else "mouseup"
            self._ydotool(f"{action} {btn_code}")

    def scroll(self, dx: float, dy: float):
        self.init()
        if self._backend == "pynput":
            self._pynput_mouse.scroll(int(dx), int(dy))
        elif self._backend == "uinput":
            self._uinput_scroll(dx, dy)
        elif self._backend == "ydotool":
            direction = "up" if dy > 0 else "down"
            count = abs(int(dy))
            for _ in range(min(count, 10)):
                self._ydotool(f"click C@{direction}")

    def key_event(self, keysym: int, keycode: int, pressed: bool):
        self.init()
        if self._backend == "pynput":
            key = self._pynput_key(keysym, keycode)
            if key is None:
                return
            try:
                if pressed:
                    self._pynput_keyboard.press(key)
                else:
                    self._pynput_keyboard.release(key)
            except Exception:
                pass
        elif self._backend == "uinput":
            self._uinput_key(keysym, pressed)
        elif self._backend == "ydotool":
            self._ydotool_key(keysym, pressed)

    def close(self):
        self._pynput_mouse = None
        self._pynput_keyboard = None
        self._uinput_dev = None
        self._initialized = False

    # ── pynput helpers ─────────────────────────────────────────────

    @staticmethod
    def _pynput_button(button_id: int):
        from pynput.mouse import Button
        return {
            1: Button.left, 2: Button.middle, 3: Button.right,
            4: Button.x1, 5: Button.x2,
        }.get(button_id, Button.left)

    @staticmethod
    def _pynput_key(keysym: int, keycode: int):
        """Map keysym to pynput key object."""
        from pynput.keyboard import Key, KeyCode
        special = {
            0xFF08: Key.backspace, 0xFF09: Key.tab, 0xFF0D: Key.enter,
            0xFF1B: Key.esc, 0xFF50: Key.home, 0xFF57: Key.end,
            0xFF51: Key.left, 0xFF52: Key.up, 0xFF53: Key.right,
            0xFF54: Key.down, 0xFF55: Key.page_up, 0xFF56: Key.page_down,
            0xFFBE: Key.f1, 0xFFBF: Key.f2, 0xFFC0: Key.f3,
            0xFFC1: Key.f4, 0xFFC2: Key.f5, 0xFFC3: Key.f6,
            0xFFC4: Key.f7, 0xFFC5: Key.f8, 0xFFC6: Key.f9,
            0xFFC7: Key.f10, 0xFFC8: Key.f11, 0xFFC9: Key.f12,
            0xFFE1: Key.shift, 0xFFE2: Key.shift, 0xFFE3: Key.ctrl_l,
            0xFFE4: Key.ctrl_r, 0xFFE5: Key.caps_lock,
            0xFFE7: Key.alt_l, 0xFFE8: Key.alt_r,
            0xFFEB: Key.cmd, 0xFFEC: Key.cmd_r,
            0xFFFF: Key.delete, 0x0020: Key.space,
        }
        if keysym in special:
            return special[keysym]
        if 0 < keysym < 256:
            return KeyCode.from_char(chr(keysym))
        if keycode:
            return KeyCode.from_vk(keycode)
        return None

    # ── uinput helpers ─────────────────────────────────────────────

    def _init_uinput(self):
        try:
            import uinput
            self._uinput_dev = uinput.Device([
                uinput.REL_X, uinput.REL_Y,
                uinput.BTN_LEFT, uinput.BTN_MIDDLE, uinput.BTN_RIGHT,
                uinput.REL_WHEEL,
                uinput.KEY_MAX,  # All keys
            ])
        except ImportError:
            log.warning("python-uinput not available")
            raise

    def _uinput_move(self, x, y, relative):
        if self._uinput_dev:
            if relative:
                self._uinput_dev.emit(uinput.REL_X, int(x))
                self._uinput_dev.emit(uinput.REL_Y, int(y))
            else:
                # uinput only supports relative motion
                # For absolute, we'd need the wacom/tablet protocol
                log.debug("Absolute positioning via uinput not supported, "
                          "using relative")
                self._uinput_dev.emit(uinput.REL_X, int(x))
                self._uinput_dev.emit(uinput.REL_Y, int(y))

    def _uinput_click(self, button, pressed):
        if self._uinput_dev:
            btn_map = {1: uinput.BTN_LEFT, 2: uinput.BTN_MIDDLE,
                       3: uinput.BTN_RIGHT}
            btn = btn_map.get(button, uinput.BTN_LEFT)
            self._uinput_dev.emit(btn, 1 if pressed else 0)

    def _uinput_scroll(self, dx, dy):
        if self._uinput_dev:
            self._uinput_dev.emit(uinput.REL_WHEEL, int(dy))

    def _uinput_key(self, keysym, pressed):
        if self._uinput_dev:
            self._uinput_dev.emit(keysym, 1 if pressed else 0)

    # ── ydotool helpers ─────────────────────────────────────────────

    def _ydotool(self, cmd: str):
        """Run ydotool command."""
        try:
            subprocess.run(
                ["ydotool", *cmd.split()],
                capture_output=True, timeout=2.0,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.debug(f"ydotool failed: {e}")

    def _ydotool_mousemove(self, x, y, relative):
        if relative:
            self._ydotool(f"mousemove --x {x} --y {y}")
        else:
            self._ydotool(f"mousemove --absolute --x {x} --y {y}")

    @staticmethod
    def _ydotool_button_code(button_id: int) -> str:
        return {1: "left", 2: "middle", 3: "right"}.get(button_id, "left")

    def _ydotool_key(self, keysym, pressed):
        action = "keydown" if pressed else "keyup"
        key_hex = f"0x{keysym:04X}" if keysym else ""
        if key_hex:
            self._ydotool(f"{action} {key_hex}")


# ═══════════════════════════════════════════════════════════════════════════
# Convenience
# ═══════════════════════════════════════════════════════════════════════════

def print_backend_info():
    """Print available backends for debugging."""
    print(f"Display:     {os.environ.get('WAYLAND_DISPLAY', '') or 'X11'}")
    print(f"Session:     {os.environ.get('XDG_SESSION_TYPE', 'unknown')}")
    print(f"Is Wayland:  {is_wayland()}")
    print(f"Is XWayland: {is_xwayland()}")
    print(f"Backends:    {', '.join(available_backends()) or 'none'}")
