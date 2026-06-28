"""Windows direct input via SendInput ctypes.

Bypasses pynput for keyboard events to work around issues with
modifier keys (Shift, Ctrl, Alt) not registering in games that
use raw input (GLFW, etc.).

Strategy:
- Character keys (keysym < 256): send via Unicode input for reliability.
  Track Shift/Caps state locally to apply correct case since VK
  modifier state may not reach all applications via SendInput.
- Modifier keys: send VK code for raw input consumers AND track
  state locally for character case conversion.
- Special keys (F-keys, arrows, etc.): send VK code.

Usage:
    from deskctrl import win32_input
    if win32_input.is_available():
        win32_input.key_event(keysym, True)   # press
        win32_input.key_event(keysym, False)  # release
"""

import ctypes
import ctypes.wintypes

# Constants
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

INPUT_KEYBOARD = 1

# Map deskctrl keysym → Windows VK code
KEYSYM_TO_VK = {
    0xFF08: 0x08, 0xFF09: 0x09, 0xFF0D: 0x0D, 0xFF1B: 0x1B,
    0xFF50: 0x24, 0xFF51: 0x25, 0xFF52: 0x26, 0xFF53: 0x27,
    0xFF54: 0x28, 0xFF55: 0x21, 0xFF56: 0x22, 0xFF57: 0x23,
    0xFF63: 0x2D, 0xFFFF: 0x2E, 0xFF61: 0x2C,
    0xFF14: 0x91, 0xFF13: 0x13, 0xFF7F: 0x90, 0xFFE5: 0x14,
    0xFF67: 0x5D, 0xFE03: 0xA5,
    # F-keys
    0xFFBE: 0x70, 0xFFBF: 0x71, 0xFFC0: 0x72, 0xFFC1: 0x73,
    0xFFC2: 0x74, 0xFFC3: 0x75, 0xFFC4: 0x76, 0xFFC5: 0x77,
    0xFFC6: 0x78, 0xFFC7: 0x79, 0xFFC8: 0x7A, 0xFFC9: 0x7B,
    # Modifiers
    0xFFE1: 0xA0, 0xFFE2: 0xA1, 0xFFE3: 0xA2, 0xFFE4: 0xA3,
    0xFFE9: 0xA4, 0xFFEA: 0xA5, 0xFFE7: 0x5B, 0xFFE8: 0x5C,
    0xFFEB: 0x5B, 0xFFEC: 0x5C,
}

# VK codes that need KEYEVENTF_EXTENDEDKEY
EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
               0x2C, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x90, 0x91,
               0xA3, 0xA5}

# Keysyms that affect character case
_SHIFT_KEYS = frozenset({0xFFE1, 0xFFE2})
_CAPSLOCK_KEY = 0xFFE5

# --- Internal modifier state tracked locally ---
# (VK modifier state from SendInput may not propagate to all apps)
_shift_down = False
_caps_on = False

_initialized = False
_user32 = None


def _ensure_init():
    """Initialize ctypes function bindings on first use."""
    global _initialized, _user32
    if _initialized:
        return _user32 is not None
    _initialized = True
    try:
        lib = ctypes.windll.user32
    except AttributeError:
        return False

    # SendInput(UINT cInputs, LPINPUT pInputs, int cbSize)
    lib.SendInput.argtypes = [
        ctypes.wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    lib.SendInput.restype = ctypes.wintypes.UINT

    # MapVirtualKeyW(UINT uCode, UINT uMapType)
    lib.MapVirtualKeyW.argtypes = [
        ctypes.wintypes.UINT,
        ctypes.wintypes.UINT,
    ]
    lib.MapVirtualKeyW.restype = ctypes.wintypes.UINT

    _user32 = lib
    return True


def reset():
    """Reset internal modifier state. Call when connection starts."""
    global _shift_down, _caps_on
    _shift_down = False
    _caps_on = False


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUT_STRUCT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("ki", KEYBDINPUT),
    ]


def _send(vk, scan, flags):
    inp = INPUT_STRUCT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0),
    )
    return _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT_STRUCT)) == 1


def _send_vk(vk, pressed):
    flags = KEYEVENTF_KEYUP if not pressed else 0
    if vk in EXTENDED_VK:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = _user32.MapVirtualKeyW(vk, 0)
    return _send(vk, scan, flags)


def _send_unicode(char, pressed):
    flags = KEYEVENTF_UNICODE
    if not pressed:
        flags |= KEYEVENTF_KEYUP
    return _send(0, ord(char), flags)


def key_event(keysym: int, pressed: bool) -> bool:
    """Send a key event for a deskctrl keysym.

    Returns True if handled, False if keysym is unknown.
    """
    global _shift_down, _caps_on
    if not _ensure_init():
        return False

    # ── Character keys: Unicode input with local case tracking ──
    if 0 < keysym < 256:
        char = chr(keysym)
        # a-z (0x61-0x7A): apply shift/caps
        if 0x61 <= keysym <= 0x7A:
            shifted = _shift_down ^ _caps_on  # XOR: shift inverts caps
            if shifted:
                char = chr(keysym - 0x20)  # → uppercase
        # A-Z (0x41-0x5A): client may send uppercase too; normalize
        elif 0x41 <= keysym <= 0x5A:
            shifted = _shift_down ^ _caps_on
            if not shifted:
                char = chr(keysym + 0x20)  # → lowercase
        _send_unicode(char, pressed)
        return True

    # ── Shift keys: track locally + send VK ──
    if keysym in _SHIFT_KEYS:
        _shift_down = pressed
        vk = KEYSYM_TO_VK[keysym]
        _send_vk(vk, pressed)
        return True

    # ── Caps Lock: toggle + send VK ──
    if keysym == _CAPSLOCK_KEY:
        if pressed:
            _caps_on = not _caps_on
        vk = KEYSYM_TO_VK[keysym]
        _send_vk(vk, pressed)
        return True

    # ── Other special/modifier keys: send VK ──
    vk = KEYSYM_TO_VK.get(keysym)
    if vk is not None:
        _send_vk(vk, pressed)
        return True

    return False


def is_available() -> bool:
    """Check if this module works on this platform (Windows only)."""
    return _ensure_init()
