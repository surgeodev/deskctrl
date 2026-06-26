"""Network protocol for deskctrl.

Binary framing: [4 bytes message type][4 bytes payload length][N bytes payload]

Message types:
    HELLO / HELLO_ACK    — Handshake
    VIDEO_FRAME           — JPEG-encoded frame (or H.264 NAL)
    POINTER_MOVE          — Mouse move (x, y as f32)
    POINTER_BUTTON        — Mouse button (button id, pressed bool, x, y)
    KEY_EVENT             — Keyboard key (keysym as u32, pressed bool)
    SCROLL                — Scroll (dx, dy as f32)
    CLIPBOARD             — Clipboard text (utf-8)
    RESOLUTION            — Screen resolution (width, height as u32)
    SETTINGS              — Encoding settings
    HDMI_TOGGLE           — Toggle HDMI capture mode
    DISCONNECT            — Graceful disconnect
    KEEPALIVE             — Ping/pong keepalive
"""

import struct
import json
from enum import IntEnum
from typing import Optional


# ── Message Types ──────────────────────────────────────────────────────────

class MsgType(IntEnum):
    HELLO        = 0x01
    HELLO_ACK    = 0x02
    VIDEO_FRAME  = 0x10
    POINTER_MOVE = 0x20
    POINTER_BUTTON = 0x21
    KEY_EVENT    = 0x22
    SCROLL       = 0x23
    CLIPBOARD    = 0x30
    RESOLUTION   = 0x40
    SETTINGS     = 0x50
    HDMI_TOGGLE  = 0x60
    DISCONNECT   = 0xF0
    KEEPALIVE    = 0xFF


# ── Frame Header ───────────────────────────────────────────────────────────

HEADER_FMT = "!II"   # network byte order: type (u32), length (u32)
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 8 bytes


def encode_msg(msg_type: MsgType, payload: bytes = b"") -> bytes:
    """Encode a message with type + length prefix."""
    return struct.pack(HEADER_FMT, msg_type.value, len(payload)) + payload


def decode_header(data: bytes) -> tuple:
    """Decode header from raw bytes. Returns (type_value, payload_length)."""
    t, length = struct.unpack(HEADER_FMT, data)
    return MsgType(t), length


# ── Payload Helpers ────────────────────────────────────────────────────────

def encode_hello(version: str) -> bytes:
    return json.dumps({"version": version}).encode("utf-8")


def decode_hello(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


def encode_pointer_move(x: float, y: float, relative: bool = False) -> bytes:
    flags = 1 if relative else 0
    return struct.pack("!Bff", flags, x, y)


def decode_pointer_move(payload: bytes) -> tuple:
    flags, x, y = struct.unpack("!Bff", payload)
    return x, y, bool(flags)


def encode_pointer_button(button: int, pressed: bool, x: float, y: float) -> bytes:
    return struct.pack("!B?ff", button, pressed, x, y)


def decode_pointer_button(payload: bytes) -> tuple:
    button, pressed, x, y = struct.unpack("!B?ff", payload)
    return button, pressed, x, y


def encode_key_event(keysym: int, keycode: int, pressed: bool) -> bytes:
    return struct.pack("!II?", keysym, keycode, pressed)


def decode_key_event(payload: bytes) -> tuple:
    keysym, keycode, pressed = struct.unpack("!II?", payload)
    return keysym, keycode, pressed


def encode_scroll(dx: float, dy: float) -> bytes:
    return struct.pack("!ff", dx, dy)


def decode_scroll(payload: bytes) -> tuple:
    return struct.unpack("!ff", payload)


def encode_clipboard(text: str) -> bytes:
    return text.encode("utf-8")


def decode_clipboard(payload: bytes) -> str:
    return payload.decode("utf-8")


def encode_resolution(width: int, height: int) -> bytes:
    return struct.pack("!II", width, height)


def decode_resolution(payload: bytes) -> tuple:
    return struct.unpack("!II", payload)


def encode_settings(**kwargs) -> bytes:
    return json.dumps(kwargs).encode("utf-8")


def decode_settings(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


# ── Video Frame helpers ────────────────────────────────────────────────────

def encode_video_frame(frame_data: bytes, frame_type: str = "jpeg",
                        width: int = 0, height: int = 0,
                        pts: int = 0) -> bytes:
    """Encode video frame with metadata header before raw data."""
    # Extended header: frame_type(1B) + width(2B) + height(2B) + pts(8B)
    ft = 0  # 0=jpeg, 1=h264, 2=h265
    if frame_type == "h264":
        ft = 1
    elif frame_type == "h265":
        ft = 2
    meta = struct.pack("!BHHQ", ft, width, height, pts)
    return meta + frame_data
