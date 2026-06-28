"""
Binary protocol over TCP.

Frame layout:
  [1 byte: msg_type] [4 bytes: payload_length (big-endian)] [N bytes: payload]

Message types:
  SETTINGS    0x01  JSON-encoded dict (resolution, monitor, fps, etc.)
  FRAME       0x02  Raw JPEG/PNG bytes for a video frame
  INPUT_KEY   0x03  Keyboard event: [1 byte: pressed] [4 bytes: keysym big-endian]
  INPUT_MOUSE 0x04  Mouse event: [1 byte: event_type] [payload varies]
  CLIPBOARD   0x05  Clipboard sync: UTF-8 text
  PING        0x06  Keep-alive (empty payload)
  PONG        0x07  Keep-alive reply (empty payload)
  HELLO       0x08  Handshake for monitor-control clients (UTF-8 version string)
  HELLO_ACK   0x09  Client acknowledges HELLO
  RESOLUTION  0x0A  Screen dimensions packed as >II (width, height)
  DISCONNECT  0x0C  Clean disconnection

Mouse event_type sub-codes (first byte of INPUT_MOUSE payload):
  MOUSE_MOVE   0x01  [4 bytes: x] [4 bytes: y]  (signed big-endian int32)
  MOUSE_PRESS  0x02  [4 bytes: x] [4 bytes: y] [1 byte: button]
  MOUSE_RELEASE 0x03 [4 bytes: x] [4 bytes: y] [1 byte: button]
  MOUSE_SCROLL 0x04  [4 bytes: dx] [4 bytes: dy]  (signed big-endian int32)
"""

import json
import struct
import socket

MSG_SETTINGS    = 0x01
MSG_FRAME       = 0x02
MSG_INPUT_KEY   = 0x03
MSG_INPUT_MOUSE = 0x04
MSG_CLIPBOARD   = 0x05
MSG_PING        = 0x06
MSG_PONG        = 0x07
MSG_HELLO       = 0x08  # Handshake: server announces itself to monitor clients
MSG_HELLO_ACK   = 0x09  # Client acknowledges HELLO
MSG_RESOLUTION  = 0x0A  # Screen dimensions (width, height as >II)
MSG_DISCONNECT  = 0x0C  # Clean disconnection

MOUSE_MOVE    = 0x01
MOUSE_PRESS   = 0x02
MOUSE_RELEASE = 0x03
MOUSE_SCROLL  = 0x04

HEADER_SIZE = 5  # 1 (type) + 4 (length)


def send_message(sock: socket.socket, msg_type: int, payload: bytes) -> None:
    header = struct.pack(">BI", msg_type, len(payload))
    sock.sendall(header + payload)


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed unexpectedly")
        buf += chunk
    return buf


def recv_message(sock: socket.socket):
    """
    Blocking receive. Returns (msg_type: int, payload: bytes).
    Raises ConnectionError if socket closes.
    """
    header = recv_exactly(sock, HEADER_SIZE)
    msg_type, length = struct.unpack(">BI", header)
    payload = recv_exactly(sock, length) if length else b""
    return msg_type, payload


def encode_settings(settings: dict) -> bytes:
    return json.dumps(settings).encode("utf-8")


def decode_settings(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


def encode_key(pressed: bool, keysym: int) -> bytes:
    return struct.pack(">BI", int(pressed), keysym)


def encode_hello(version: str) -> bytes:
    return version.encode("utf-8")


def decode_hello(payload: bytes) -> str:
    return payload.decode("utf-8")


def encode_resolution(width: int, height: int) -> bytes:
    return struct.pack(">II", width, height)


def decode_resolution(payload: bytes):
    return struct.unpack(">II", payload)


def decode_key(payload: bytes):
    pressed_int, keysym = struct.unpack(">BI", payload)
    return bool(pressed_int), keysym


def encode_mouse_move(x: int, y: int) -> bytes:
    return struct.pack(">Bii", MOUSE_MOVE, x, y)


def encode_mouse_button(event_type: int, x: int, y: int, button: int) -> bytes:
    return struct.pack(">BiiB", event_type, x, y, button)


def encode_mouse_scroll(dx: int, dy: int) -> bytes:
    return struct.pack(">Bii", MOUSE_SCROLL, dx, dy)


def decode_mouse(payload: bytes):
    """
    Returns dict with keys depending on sub-type.
    """
    sub = payload[0]
    if sub == MOUSE_MOVE:
        x, y = struct.unpack(">ii", payload[1:9])
        return {"type": "move", "x": x, "y": y}
    elif sub in (MOUSE_PRESS, MOUSE_RELEASE):
        x, y, button = struct.unpack(">iiB", payload[1:10])
        action = "press" if sub == MOUSE_PRESS else "release"
        return {"type": action, "x": x, "y": y, "button": button}
    elif sub == MOUSE_SCROLL:
        dx, dy = struct.unpack(">ii", payload[1:9])
        return {"type": "scroll", "dx": dx, "dy": dy}
    else:
        return {"type": "unknown", "sub": sub}
