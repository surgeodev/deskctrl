"""
Keysym mappings and pygame key helpers.

Keysyms follow X11 conventions where possible, giving a stable wire value
regardless of platform.

_PG_SPECIAL maps pygame key constants (pygame.K_*) to X11 keysyms for
non-printable / special keys so the server knows what virtual key to inject.
"""

from __future__ import annotations

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False

# X11 keysym constants for special keys (subset)
XK_BackSpace    = 0xFF08
XK_Tab          = 0xFF09
XK_Return       = 0xFF0D
XK_Escape       = 0xFF1B
XK_Delete       = 0xFFFF
XK_Home         = 0xFF50
XK_Left         = 0xFF51
XK_Up           = 0xFF52
XK_Right        = 0xFF53
XK_Down         = 0xFF54
XK_Prior        = 0xFF55  # Page Up
XK_Next         = 0xFF56  # Page Down
XK_End          = 0xFF57
XK_Insert       = 0xFF63
XK_F1           = 0xFFBE
XK_F2           = 0xFFBF
XK_F3           = 0xFFC0
XK_F4           = 0xFFC1
XK_F5           = 0xFFC2
XK_F6           = 0xFFC3
XK_F7           = 0xFFC4
XK_F8           = 0xFFC5
XK_F9           = 0xFFC6
XK_F10          = 0xFFC7
XK_F11          = 0xFFC8
XK_F12          = 0xFFC9
XK_Shift_L      = 0xFFE1
XK_Shift_R      = 0xFFE2
XK_Control_L    = 0xFFE3
XK_Control_R    = 0xFFE4
XK_Alt_L        = 0xFFE9
XK_Alt_R        = 0xFFEA
XK_Super_L      = 0xFFEB  # Win key left
XK_Super_R      = 0xFFEC  # Win key right
XK_Caps_Lock    = 0xFFE5
XK_Num_Lock     = 0xFFEF
XK_Scroll_Lock  = 0xFF14
XK_Pause        = 0xFF13
XK_Print        = 0xFF61
XK_Menu         = 0xFF67
XK_space        = 0x0020

# Pygame K_* → X11 keysym mapping for special keys
_PG_SPECIAL: dict[int, int] = {}

if _PYGAME_AVAILABLE:
    import pygame as _pg
    _PG_SPECIAL = {
        _pg.K_BACKSPACE:   XK_BackSpace,
        _pg.K_TAB:         XK_Tab,
        _pg.K_RETURN:      XK_Return,
        _pg.K_ESCAPE:      XK_Escape,
        _pg.K_DELETE:      XK_Delete,
        _pg.K_HOME:        XK_Home,
        _pg.K_LEFT:        XK_Left,
        _pg.K_UP:          XK_Up,
        _pg.K_RIGHT:       XK_Right,
        _pg.K_DOWN:        XK_Down,
        _pg.K_PAGEUP:      XK_Prior,
        _pg.K_PAGEDOWN:    XK_Next,
        _pg.K_END:         XK_End,
        _pg.K_INSERT:      XK_Insert,
        _pg.K_F1:          XK_F1,
        _pg.K_F2:          XK_F2,
        _pg.K_F3:          XK_F3,
        _pg.K_F4:          XK_F4,
        _pg.K_F5:          XK_F5,
        _pg.K_F6:          XK_F6,
        _pg.K_F7:          XK_F7,
        _pg.K_F8:          XK_F8,
        _pg.K_F9:          XK_F9,
        _pg.K_F10:         XK_F10,
        _pg.K_F11:         XK_F11,
        _pg.K_F12:         XK_F12,
        _pg.K_LSHIFT:      XK_Shift_L,
        _pg.K_RSHIFT:      XK_Shift_R,
        _pg.K_LCTRL:       XK_Control_L,
        _pg.K_RCTRL:       XK_Control_R,
        _pg.K_LALT:        XK_Alt_L,
        _pg.K_RALT:        XK_Alt_R,
        _pg.K_LMETA:       XK_Super_L,
        _pg.K_RMETA:       XK_Super_R,
        _pg.K_CAPSLOCK:    XK_Caps_Lock,
        _pg.K_NUMLOCK:     XK_Num_Lock,
        _pg.K_SCROLLOCK:   XK_Scroll_Lock,
        _pg.K_PAUSE:       XK_Pause,
        _pg.K_PRINT:       XK_Print,
        _pg.K_MENU:        XK_Menu,
        _pg.K_SPACE:       XK_space,
    }


def pygame_key_to_keysym(pg_key: int, pg_unicode: str) -> int:
    """
    Convert a pygame key event to an X11 keysym.
    For printable characters use the Unicode code point directly.
    For special keys, fall back to _PG_SPECIAL.
    """
    if pg_key in _PG_SPECIAL:
        return _PG_SPECIAL[pg_key]
    if pg_unicode and len(pg_unicode) == 1:
        cp = ord(pg_unicode)
        if cp > 0:
            return cp
    # Last resort: use pygame's key value as keysym
    return pg_key


# pynput Key → X11 keysym (for server-side injection on Linux via Xlib)
try:
    from pynput.keyboard import Key as _PynputKey
    PYNPUT_SPECIAL_KEYSYMS: dict = {
        _PynputKey.backspace:    XK_BackSpace,
        _PynputKey.tab:          XK_Tab,
        _PynputKey.enter:        XK_Return,
        _PynputKey.esc:          XK_Escape,
        _PynputKey.delete:       XK_Delete,
        _PynputKey.home:         XK_Home,
        _PynputKey.left:         XK_Left,
        _PynputKey.up:           XK_Up,
        _PynputKey.right:        XK_Right,
        _PynputKey.down:         XK_Down,
        _PynputKey.page_up:      XK_Prior,
        _PynputKey.page_down:    XK_Next,
        _PynputKey.end:          XK_End,
        _PynputKey.insert:       XK_Insert,
        _PynputKey.f1:           XK_F1,
        _PynputKey.f2:           XK_F2,
        _PynputKey.f3:           XK_F3,
        _PynputKey.f4:           XK_F4,
        _PynputKey.f5:           XK_F5,
        _PynputKey.f6:           XK_F6,
        _PynputKey.f7:           XK_F7,
        _PynputKey.f8:           XK_F8,
        _PynputKey.f9:           XK_F9,
        _PynputKey.f10:          XK_F10,
        _PynputKey.f11:          XK_F11,
        _PynputKey.f12:          XK_F12,
        _PynputKey.shift:        XK_Shift_L,
        _PynputKey.shift_r:      XK_Shift_R,
        _PynputKey.ctrl:         XK_Control_L,
        _PynputKey.ctrl_r:       XK_Control_R,
        _PynputKey.alt:          XK_Alt_L,
        _PynputKey.alt_r:        XK_Alt_R,
        _PynputKey.cmd:          XK_Super_L,
        _PynputKey.cmd_r:        XK_Super_R,
        _PynputKey.caps_lock:    XK_Caps_Lock,
        _PynputKey.num_lock:     XK_Num_Lock,
        _PynputKey.scroll_lock:  XK_Scroll_Lock,
        _PynputKey.pause:        XK_Pause,
        _PynputKey.print_screen: XK_Print,
        _PynputKey.menu:         XK_Menu,
        _PynputKey.space:        XK_space,
    }
except ImportError:
    PYNPUT_SPECIAL_KEYSYMS = {}
