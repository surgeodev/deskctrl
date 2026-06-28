"""Windows direct input via SendInput ctypes.

Bypasses pynput for keyboard events to work around issues with
modifier keys (Shift, Ctrl, Alt) not registering in games that
use raw input (GLFW, etc.).

Usage:
    from deskctrl import win32_input
    if win32_input.is_available():
        win32_input.key_event(keysym, True)   # press
        win32_input.key_event(keysym, False)  # release
"""

import ctypes
import ctypes.wintypes
from typing import Optional

# Constants
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

# Map deskctrl keysym → Windows VK code
KEYSYM_TO_VK = {
    # Special keys
    0xFF08: 0x08,  # BackSpace
    0xFF09: 0x09,  # Tab
    0xFF0D: 0x0D,  # Return
    0xFF1B: 0x1B,  # Escape
    0xFF50: 0x24,  # Home
    0xFF51: 0x25,  # Left
    0xFF52: 0x26,  # Up
    0xFF53: 0x27,  # Right
    0xFF54: 0x28,  # Down
    0xFF55: 0x21,  # Page Up
    0xFF56: 0x22,  # Page Down
    0xFF57: 0x23,  # End
    0xFF63: 0x2D,  # Insert
    0xFFFF: 0x2E,  # Delete
    0xFF61: 0x2C,  # Print Screen
    0xFF14: 0x91,  # Scroll Lock
    0xFF13: 0x13,  # Pause
    0xFF7F: 0x90,  # Num Lock
    0xFFE5: 0x14,  # Caps Lock
    0xFF67: 0x5D,  # Menu
    0xFE03: 0xA5,  # AltGr (RMenu)
    # F-keys
    0xFFBE: 0x70, 0xFFBF: 0x71, 0xFFC0: 0x72,
    0xFFC1: 0x73, 0xFFC2: 0x74, 0xFFC3: 0x75,
    0xFFC4: 0x76, 0xFFC5: 0x77, 0xFFC6: 0x78,
    0xFFC7: 0x79, 0xFFC8: 0x7A, 0xFFC9: 0x7B,
    # Modifiers
    0xFFE1: 0xA0,  # Shift L
    0xFFE2: 0xA1,  # Shift R
    0xFFE3: 0xA2,  # Ctrl L
    0xFFE4: 0xA3,  # Ctrl R
    0xFFE9: 0xA4,  # Alt L
    0xFFEA: 0xA5,  # Alt R
    0xFFE7: 0x5B,  # Meta/Super L (Win)
    0xFFE8: 0x5C,  # Meta/Super R (Win)
    0xFFEB: 0x5B,  # Super L
    0xFFEC: 0x5C,  # Super R
}

# Keys that need the EXTENDEDKEY flag
EXTENDED_KEYS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # Nav block
    0x2C, 0x2D, 0x2E,  # PrintScreen, Insert, Delete
    0x5B, 0x5C, 0x5D,  # Win keys, Menu
    0x90, 0x91,  # NumLock, ScrollLock
    0xA3, 0xA5,  # RCtrl, RAlt
}

_LIB: Optional[ctypes.CDLL] = None


def _get_lib():
    global _LIB
    if _LIB is not None:
        return _LIB
    try:
        _LIB = ctypes.windll.user32
    except AttributeError:
        return None
    return _LIB


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUT_union(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("value", INPUT_union),
    ]


def _send_keyboard(vk: int, scan: int, flags: int) -> bool:
    """Send a single keyboard input via SendInput."""
    lib = _get_lib()
    if lib is None:
        return False
    inp = INPUT(
        type=INPUT_KEYBOARD,
        value=INPUT_union(
            ki=KEYBDINPUT(
                wVk=vk,
                wScan=scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    result = lib.SendInput(
        1,
        ctypes.byref(inp),
        ctypes.sizeof(INPUT),
    )
    return result == 1


def _vk_to_scan(vk: int) -> int:
    """Convert VK code to scan code using MapVirtualKey."""
    lib = _get_lib()
    if lib is None:
        return 0
    return lib.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC


def _char_to_vk(char: str):
    """Convert a character to (VK, shift_bit) using VkKeyScanW.

    Returns (vk, needs_shift) or (0, 0) if not found.
    """
    lib = _get_lib()
    if lib is None or not char:
        return (0, 0)
    # VkKeyScanW takes a wchar_t (single character)
    result = lib.VkKeyScanW(ctypes.c_wchar(char))
    if result == -1:
        return (0, 0)
    vk = result & 0xFF
    shift = (result >> 8) & 0xFF
    return (vk, shift)


def key_event(keysym: int, pressed: bool) -> bool:
    """Send a complete key event for a deskctrl keysym.

    Handles special keys, modifiers, and ASCII/Latin-1 characters.
    Returns True if the keysym was handled, False if unknown.
    """
    lib = _get_lib()
    if lib is None:
        return False

    action = 0 if pressed else KEYEVENTF_KEYUP

    # --- ASCII / Latin-1 character (keysym 1-255) ---
    if 0 < keysym < 256:
        char = chr(keysym)
        vk, shift_bit = _char_to_vk(char)
        if vk:
            # Send the VK code WITHOUT shift modifier flag.
            # The separately-sent shift modifier key handles uppercasing.
            scan = _vk_to_scan(vk)
            return _send_keyboard(vk, scan, action)
        else:
            # Fallback: send as Unicode
            flags = KEYEVENTF_UNICODE | action
            return _send_keyboard(0, ord(char), flags)

    # --- Known special / modifier keys ---
    vk = KEYSYM_TO_VK.get(keysym)
    if vk is not None:
        flags = action
        if vk in EXTENDED_KEYS:
            flags |= KEYEVENTF_EXTENDEDKEY
        scan = _vk_to_scan(vk)
        return _send_keyboard(vk, scan, flags)

    # Unknown keysym
    return False


def is_available() -> bool:
    """Check if this module is usable (Windows only)."""
    return _get_lib() is not None
