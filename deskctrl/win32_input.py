"""
Windows-only: SendInput via ctypes for character keys (Unicode).

Strategy (from hard-won experience):
  - Character keys (keysym < 256, i.e., letters, numbers, symbols):
      Use SendInput with KEYEVENTF_UNICODE + wScan=<unicode_char>.
      Track _shift_down and _caps_on locally to compute uppercase/lowercase.
  - All other keys (modifiers, F-keys, nav keys, etc.):
      Use pynput Controller — do NOT call this module.
  - Tab (0x09) and Escape (0x1B) are accepted here too (they work with SendInput VK).
  - NEVER try SendInput with wVk > 0x1B — it silently fails on some Windows configs.
  - NEVER try keybd_event or SCANCODE workarounds for those VKs.
"""

from __future__ import annotations
import sys

def is_available() -> bool:
    return sys.platform == "win32"

if not is_available():
    # Stub for non-Windows
    def send_key_down(keysym: int) -> bool:
        return False

    def send_key_up(keysym: int) -> bool:
        return False

    def reset() -> None:
        pass

else:
    import ctypes
    from ctypes import wintypes

    KEYEVENTF_KEYUP      = 0x0002
    KEYEVENTF_UNICODE    = 0x0004
    KEYEVENTF_EXTENDEDKEY = 0x0001

    VK_TAB    = 0x09
    VK_ESCAPE = 0x1B
    VK_SHIFT  = 0x10
    VK_CAPITAL = 0x14

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk",         wintypes.WORD),
            ("wScan",       wintypes.WORD),
            ("dwFlags",     wintypes.DWORD),
            ("time",        wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("_input", INPUT_UNION),
        ]

    INPUT_KEYBOARD = 1

    _shift_down: bool = False
    _caps_on: bool = False
    _pressed_keys: dict[int, int] = {}  # keysym → sent unicode char (for KEYUP)

    def reset() -> None:
        global _shift_down, _caps_on, _pressed_keys
        _shift_down = False
        _caps_on = False
        _pressed_keys = {}

    def _get_caps_state() -> bool:
        """Read current CapsLock toggle state from Windows."""
        return bool(ctypes.windll.user32.GetKeyState(VK_CAPITAL) & 0x0001)

    def _send_unicode(char: str, key_up: bool) -> bool:
        flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
        inp = INPUT(
            type=INPUT_KEYBOARD,
            _input=INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=ord(char),
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=None,
                )
            ),
        )
        result = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        return result == 1

    def _send_vk(vk: int, key_up: bool) -> bool:
        flags = (KEYEVENTF_KEYUP if key_up else 0)
        inp = INPUT(
            type=INPUT_KEYBOARD,
            _input=INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=vk,
                    wScan=0,
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=None,
                )
            ),
        )
        result = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        return result == 1

    def _keysym_is_char(keysym: int) -> bool:
        """Returns True if keysym is a printable ASCII char we handle via Unicode."""
        return 0x20 <= keysym <= 0xFF

    def _compute_char(keysym: int) -> str:
        """
        Given a raw keysym (always the unshifted code point), determine what
        character to send via Unicode, accounting for shift and caps lock.
        """
        global _shift_down, _caps_on
        ch = chr(keysym)
        shifted = _shift_down ^ _caps_on if ch.isalpha() else _shift_down
        if shifted:
            return ch.upper() if ch.isalpha() else _shifted_symbol(ch)
        return ch.lower() if ch.isalpha() else ch

    # Shift symbol map for US QWERTY
    _SHIFT_SYMBOL: dict[str, str] = {
        "`": "~", "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
        "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
        "-": "_", "=": "+", "[": "{", "]": "}", "\\": "|",
        ";": ":", "'": '"', ",": "<", ".": ">", "/": "?",
    }

    def _shifted_symbol(ch: str) -> str:
        return _SHIFT_SYMBOL.get(ch, ch)

    def send_key_down(keysym: int) -> bool:
        """
        Send key-down for the given keysym.
        Returns True if handled here, False if the caller should use pynput.
        """
        global _shift_down, _caps_on, _pressed_keys

        # Update local shift/caps tracking based on incoming keysym
        if keysym == 0xFFE1 or keysym == 0xFFE2:  # Shift L/R
            _shift_down = True
            return False  # Let pynput handle modifier keys
        if keysym == 0xFFE5:  # Caps Lock
            _caps_on = not _caps_on
            return False

        # Tab and Escape: use VK (they work)
        if keysym == 0x09:  # Tab
            return _send_vk(VK_TAB, False)
        if keysym == 0x1B:  # Escape
            return _send_vk(VK_ESCAPE, False)

        if _keysym_is_char(keysym):
            ch = _compute_char(keysym)
            ok = _send_unicode(ch, False)
            if ok:
                _pressed_keys[keysym] = ord(ch)
            return ok

        return False  # Let pynput handle everything else

    def send_key_up(keysym: int) -> bool:
        """
        Send key-up for the given keysym.
        Returns True if handled here, False if the caller should use pynput.
        """
        global _shift_down, _pressed_keys

        if keysym == 0xFFE1 or keysym == 0xFFE2:
            _shift_down = False
            return False

        if keysym == 0x09:
            return _send_vk(VK_TAB, True)
        if keysym == 0x1B:
            return _send_vk(VK_ESCAPE, True)

        if _keysym_is_char(keysym):
            char_code = _pressed_keys.pop(keysym, keysym)
            return _send_unicode(chr(char_code), True)

        return False
