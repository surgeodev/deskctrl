"""deskctrl Monitor Control -- seamless mouse transition between machines.

Like Barrier / Synergy: configure where your other machines' screens are
relative to yours (left, right, top, bottom). When the mouse hits that edge,
it seamlessly takes control of the remote machine.

Pure input forwarding -- no video streaming -- for zero latency.

Usage:
    # Start with a config file
    deskctrl monitor --config ~/.deskctrl/layout.json

    # Quick setup from CLI
    deskctrl monitor --add right=192.168.1.100
    deskctrl monitor --add left=192.168.1.101 --add top=192.168.1.102
"""

import json
import os
import socket
import struct
import threading
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable, Dict, List, Tuple

from . import __version__

# ── Self-contained protocol for monitor control connections ────────────────
# This is a separate wire protocol (input-only, no video streaming).
# Uses >II framing (8-byte header) independent of the main protocol.py.
# The server sends HELLO first, then the client responds with HELLO_ACK,
# then the server sends RESOLUTION, then they exchange input events.

MSG_HELLO        = 0x01
MSG_HELLO_ACK    = 0x02
MSG_POINTER_MOVE = 0x20
MSG_POINTER_BUTTON = 0x21
MSG_KEY_EVENT    = 0x22
MSG_SCROLL       = 0x23
MSG_RESOLUTION   = 0x40
MSG_KEEPALIVE    = 0xFF
HEADER_SIZE      = 8

def encode_msg(mt: int, payload: bytes = b"") -> bytes:
    return struct.pack("!II", mt, len(payload)) + payload

def decode_header(data: bytes):
    return struct.unpack("!II", data)

def encode_hello(version: str) -> bytes:
    return version.encode("utf-8")

def decode_hello(payload: bytes) -> str:
    return payload.decode("utf-8")

def decode_resolution(payload: bytes):
    return struct.unpack("!II", payload)

def encode_pointer_move(x: float, y: float, relative: bool = False) -> bytes:
    flags = 0x01 if relative else 0x00
    return struct.pack("!Bii", flags, int(x), int(y))

def encode_pointer_button(button: int, pressed: bool, x: float, y: float) -> bytes:
    return struct.pack("!BiiB", 0x01 if pressed else 0x00, int(x), int(y), button)

def encode_key_event(keysym: int, keycode: int, pressed: bool) -> bytes:
    return struct.pack("!IIB", keysym, keycode, 0x01 if pressed else 0x00)

def encode_scroll(dx: float, dy: float) -> bytes:
    return struct.pack("!ii", int(dx), int(dy))

# ── Server-side decode helpers (used by server.py monitor session) ────────

def decode_pointer_move(payload: bytes) -> dict:
    flags, x, y = struct.unpack("!Bii", payload)
    return {"x": x, "y": y, "relative": bool(flags & 0x01)}

def decode_pointer_button(payload: bytes) -> dict:
    pressed, x, y, button = struct.unpack("!BiiB", payload)
    return {"pressed": bool(pressed), "x": x, "y": y, "button": button}

def decode_key_event(payload: bytes) -> dict:
    keysym, keycode, pressed = struct.unpack("!IIB", payload)
    return {"keysym": keysym, "keycode": keycode, "pressed": bool(pressed)}

def decode_scroll(payload: bytes) -> dict:
    dx, dy = struct.unpack("!ii", payload)
    return {"dx": dx, "dy": dy}


# Keep old private aliases for backward compat (used internally below)
_MSG_HELLO = MSG_HELLO
_MSG_HELLO_ACK = MSG_HELLO_ACK
_MSG_POINTER_MOVE = MSG_POINTER_MOVE
_MSG_POINTER_BUTTON = MSG_POINTER_BUTTON
_MSG_KEY_EVENT = MSG_KEY_EVENT
_MSG_SCROLL = MSG_SCROLL
_MSG_RESOLUTION = MSG_RESOLUTION
_MSG_KEEPALIVE = MSG_KEEPALIVE
_HEADER_SIZE = HEADER_SIZE
_encode_msg = encode_msg
_decode_header = decode_header
_encode_hello = encode_hello
_decode_hello = decode_hello
_decode_resolution = decode_resolution
_encode_pointer_move = encode_pointer_move
_encode_pointer_button = encode_pointer_button
_encode_key_event = encode_key_event
_encode_scroll = encode_scroll

log = logging.getLogger(__name__)

# ---- Configuration --------------------------------------------------------------------------------------------------------------------

DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/deskctrl")
DEFAULT_CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, "monitor_layout.json")


@dataclass
class ServerConfig:
    """Configuration for a remote server in the monitor layout."""
    host: str = ""
    port: int = 5830
    name: str = ""
    direction: str = "right"       # left, right, top, bottom
    screen_width: int = 1920       # filled in after handshake
    screen_height: int = 1080      # filled in after handshake
    offset: int = 0                # pixel offset along the shared edge

    def __post_init__(self):
        if not self.name:
            self.name = f"{self.host}:{self.port}"


@dataclass
class MonitorLayout:
    """Full monitor layout: which servers are where."""
    servers: List[ServerConfig] = field(default_factory=list)
    activation_margin: int = 5     # pixels from edge to trigger
    escape_key: str = "esc"        # key to release control

    # ---- IO ------------------------------------------------------------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str) -> "MonitorLayout":
        with open(path) as f:
            data = json.load(f)
        return cls(
            servers=[ServerConfig(**s) for s in data.get("servers", [])],
            activation_margin=data.get("activation_margin", 5),
            escape_key=data.get("escape_key", "esc"),
        )

    def to_file(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "servers": [asdict(s) for s in self.servers],
                "activation_margin": self.activation_margin,
                "escape_key": self.escape_key,
            }, f, indent=2)

    # ---- Helpers -------------------------------------------------------------------------------------------------------------

    def add_server(self, direction: str, host: str, port: int = 5830,
                   name: str = "", **kw):
        """Add or replace a server at the given direction."""
        self.servers = [s for s in self.servers if s.direction != direction]
        self.servers.append(ServerConfig(
            host=host, port=port, direction=direction,
            name=name or f"{host}:{port}",
            **kw,
        ))

    def remove_server(self, direction: str):
        self.servers = [s for s in self.servers if s.direction != direction]

    def get_server(self, direction: str) -> Optional[ServerConfig]:
        for s in self.servers:
            if s.direction == direction:
                return s
        return None


# ???????????????????????????????????????????????????????????????????????????
# Monitor Control Engine
# ???????????????????????????????????????????????????????????????????????????

class MonitorControlEngine:
    """Seamless cursor transition between machines -- the Barrier/Synergy mode.

    Monitors local mouse position. When the cursor hits a configured edge,
    connects to the corresponding remote server and forwards all input.
    Press ESC to release control.

    Pure input forwarding -- zero video latency.
    """

    def __init__(self, layout: MonitorLayout):
        self.layout = layout

        # ---- Local screen --------------------------------------------------------------------------------------------
        self._screen_w: int = 0
        self._screen_h: int = 0

        # ---- Mouse tracking ----------------------------------------------------------------------------------------
        self._mouse_x: float = 0
        self._mouse_y: float = 0

        # ---- Server connections --------------------------------------------------------------------------------
        # direction -> (socket, server_config)
        self._connections: Dict[str, Tuple[socket.socket, ServerConfig]] = {}
        self._conn_lock = threading.Lock()

        # ---- Active control state ----------------------------------------------------------------------------
        self._active_direction: Optional[str] = None
        self._active_sock: Optional[socket.socket] = None
        self._active_config: Optional[ServerConfig] = None
        self._active_lock = threading.Lock()

        # ---- Lifecycle -------------------------------------------------------------------------------------------------
        self._running = False
        self._mouse_listener = None
        self._keyboard_listener = None
        self._reconnect_threads: List[threading.Thread] = []
        self._event_count = 0           # how many mouse events received
        self._polling_thread = None     # xdotool fallback polling thread
        self._key_listener_ok = False   # whether keyboard listener works

        # ---- Callbacks -------------------------------------------------------------------------------------------------
        self.on_status: Optional[Callable[[str], None]] = None
        self.on_control_start: Optional[Callable[[str, str], None]] = None
        self.on_control_end: Optional[Callable[[], None]] = None

    # ???????????????????????????????????????????????????????????????????
    # Lifecycle
    # ???????????????????????????????????????????????????????????????????

    def start(self) -> bool:
        """Start the engine: discover screen, connect servers, listen for input."""
        # ---- Get local screen ------------------------------------------------------------------------------------
        try:
            import mss
            with mss.mss() as sct:
                m = sct.monitors[1]
                self._screen_w = m["width"]
                self._screen_h = m["height"]
        except Exception as e:
            self._emit(f"? Cannot get screen size: {e}")
            return False

        self._emit(f"Monitor Control ready -- {self._screen_w}x{self._screen_h}")
        self._emit(f"Servers: {len(self.layout.servers)}")
        for s in self.layout.servers:
            self._emit(f"  {s.direction:>6s} -> {s.name} ({s.host}:{s.port})")
        self._emit("Move mouse to an edge to take control * ESC to release")

        # ---- Connect servers in background ---------------------------------------------------------
        self._running = True
        for cfg in self.layout.servers:
            t = threading.Thread(
                target=self._connect_loop, args=(cfg,),
                daemon=True, name=f"mc-{cfg.direction}",
            )
            t.start()
            self._reconnect_threads.append(t)

        # ---- Start input listeners -------------------------------------------------------------------------
        from pynput.mouse import Listener as MouseListener
        from pynput.keyboard import Listener as KeyboardListener

        self._mouse_listener = MouseListener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_scroll,
        )
        self._keyboard_listener = KeyboardListener(
            on_press=self._on_key_press,
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()

        # On Wayland, pynput listeners don't receive global mouse events.
        # Start a polling fallback using xdotool (via XWayland) if available.
        self._start_polling_fallback()

        return True

    def stop(self):
        """Stop the engine and disconnect all servers."""
        self._running = False
        self._deactivate()

        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        self._polling_thread = None  # thread is daemon, will exit with _running

        with self._conn_lock:
            for direction, (sock, _) in list(self._connections.items()):
                try:
                    sock.close()
                except Exception:
                    pass
            self._connections.clear()

        self._emit("Monitor Control stopped")

    # ── Mouse polling fallback (Wayland) ──────────────────────────────────

    def _start_polling_fallback(self):
        """Start xdotool-based mouse polling if pynput doesn't receive events.

        On Wayland, pynput's mouse listener cannot track global cursor
        position. We fall back to polling xdotool via XWayland.
        """
        if not self._running:
            return

        # Check if xdotool is available
        import subprocess, shutil
        if not shutil.which("xdotool"):
            return

        # Give pynput a moment to deliver events; if none arrive, start polling
        def _check():
            import time
            time.sleep(1.5)
            if self._event_count == 0 and self._running:
                self._emit("? pynput mouse listener not receiving events "
                           "(Wayland?) — falling back to xdotool polling")
                self._polling_thread = threading.Thread(
                    target=self._poll_mouse, daemon=True,
                    name="mc-xdotool",
                )
                self._polling_thread.start()

        t = threading.Thread(target=_check, daemon=True, name="mc-pollcheck")
        t.start()

    def _poll_mouse(self):
        """Periodically read mouse position via xdotool."""
        import subprocess, time
        while self._running:
            try:
                result = subprocess.run(
                    ["xdotool", "getmouselocation", "--shell"],
                    capture_output=True, timeout=1, text=True,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split("\n"):
                        if line.startswith("X="):
                            x = int(line[2:])
                        elif line.startswith("Y="):
                            y = int(line[2:])
                    self._on_mouse_move(x, y)
            except Exception:
                pass
            time.sleep(1 / 60)  # ~60 fps

    # ???????????????????????????????????????????????????????????????????
    # Connection management
    # ???????????????????????????????????????????????????????????????????

    def _connect_loop(self, cfg: ServerConfig):
        """Persistent connection loop -- reconnects on failure."""
        while self._running:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((cfg.host, cfg.port))

                # ---- Handshake ---------------------------------------------------------------------------------
                data = self._recv_exact(sock, _HEADER_SIZE)
                if not data:
                    raise ConnectionError("no hello")
                mt, length = _decode_header(data)
                if mt != _MSG_HELLO:
                    raise ConnectionError(f"expected HELLO, got {mt}")
                payload = self._recv_exact(sock, length)
                self._send_msg(sock, _MSG_HELLO_ACK,
                               _encode_hello(f"{__version__}-monitor"))

                # ---- Read server resolution --------------------------------------------------------
                data = self._recv_exact(sock, _HEADER_SIZE)
                if data:
                    mt, length = _decode_header(data)
                    if mt == _MSG_RESOLUTION:
                        payload = self._recv_exact(sock, length)
                        if payload:
                            w, h = _decode_resolution(payload)
                            cfg.screen_width = w
                            cfg.screen_height = h

                # ---- Register connection -------------------------------------------------------------
                with self._conn_lock:
                    old = self._connections.get(cfg.direction)
                    if old:
                        try:
                            old[0].close()
                        except Exception:
                            pass
                    self._connections[cfg.direction] = (sock, cfg)

                sock.settimeout(None)
                self._emit(f"? Connected {cfg.direction:>6s} -- {cfg.name} "
                           f"({cfg.screen_width}x{cfg.screen_height})")

                # ---- Keepalive ping loop -------------------------------------------------------------
                while self._running:
                    try:
                        sock.sendall(_encode_msg(_MSG_KEEPALIVE))
                        time.sleep(5)
                    except OSError:
                        break

            except (OSError, ConnectionError, struct.error) as e:
                if self._running:
                    # Clean up stale entry
                    with self._conn_lock:
                        if self._connections.get(cfg.direction, (None,))[0] is sock:
                            del self._connections[cfg.direction]
                    if self._active_direction == cfg.direction:
                        self._deactivate()
                    self._emit(f"??  {cfg.direction:>6s} -- {cfg.name}: {e}")
                    time.sleep(3)  # wait before retry
                continue

    # ???????????????????????????????????????????????????????????????????
    # Mouse/keyboard event handlers
    # ???????????????????????????????????????????????????????????????????

    def _on_mouse_move(self, x: float, y: float):
        if not self._running:
            return
        self._event_count += 1
        self._mouse_x, self._mouse_y = x, y

        with self._active_lock:
            if self._active_direction:
                # ---- Forward to active server ----------------------------------------------------
                if self._active_sock is None:
                    return
                sx, sy = self._map_to_server(x, y, self._active_config)
                self._server_mouse_x, self._server_mouse_y = sx, sy
                try:
                    self._send_msg(self._active_sock, _MSG_POINTER_MOVE,
                                   _encode_pointer_move(sx, sy))
                except OSError:
                    self._deactivate()
            else:
                # ---- Check for edge activation -------------------------------------------------
                direction = self._detect_edge(x, y)
                if direction:
                    self._activate(direction)

    def _on_mouse_click(self, x: float, y: float, button, pressed: bool):
        if not self._running:
            return
        with self._active_lock:
            if self._active_sock is None:
                return
            btn_id = self._pynput_button_id(button)
            try:
                self._send_msg(self._active_sock, _MSG_POINTER_BUTTON,
                               _encode_pointer_button(btn_id, pressed,
                                                      self._server_mouse_x,
                                                      self._server_mouse_y))
            except OSError:
                self._deactivate()

    def _on_scroll(self, x: float, y: float, dx: float, dy: float):
        if not self._running:
            return
        with self._active_lock:
            if self._active_sock is None:
                return
            try:
                self._send_msg(self._active_sock, _MSG_SCROLL,
                               _encode_scroll(dx, dy))
            except OSError:
                self._deactivate()

    def _on_key_press(self, key):
        if not self._running:
            return
        from pynput.keyboard import Key

        # ---- ESC: release control ----------------------------------------------------------------------------
        if key == Key.esc and self._active_direction:
            self._deactivate()
            return

        # ---- Forward key to active server ------------------------------------------------------------
        with self._active_lock:
            if self._active_sock is None:
                return
            keysym, keycode = self._key_info(key)
            if keysym is not None:
                try:
                    self._send_msg(self._active_sock, _MSG_KEY_EVENT,
                                   _encode_key_event(keysym, keycode, True))
                except OSError:
                    self._deactivate()

    # ???????????????????????????????????????????????????????????????????
    # Edge detection
    # ???????????????????????????????????????????????????????????????????

    def _detect_edge(self, x: float, y: float) -> Optional[str]:
        """Return direction name if cursor is within margin of a configured edge."""
        margin = max(1, min(self.layout.activation_margin, 100))

        for cfg in self.layout.servers:
            d = cfg.direction
            if d == "right" and self._screen_w - margin <= x <= self._screen_w:
                return d
            if d == "left" and 0 <= x <= margin:
                return d
            if d == "top" and 0 <= y <= margin:
                return d
            if d == "bottom" and self._screen_h - margin <= y <= self._screen_h:
                return d
        return None

    # ???????????????????????????????????????????????????????????????????
    # Coordinate mapping
    # ???????????????????????????????????????????????????????????????????

    def _map_to_server(self, x: float, y: float,
                       cfg: Optional[ServerConfig]) -> Tuple[float, float]:
        """Map local cursor position to the remote server's screen."""
        if cfg is None:
            return 0, 0

        ratio_x = x / self._screen_w if self._screen_w > 0 else 0
        ratio_y = y / self._screen_h if self._screen_h > 0 else 0
        sw = cfg.screen_width
        sh = cfg.screen_height

        if cfg.direction == "right":
            return 0, max(0, min(sh - 1, int(ratio_y * sh)))
        elif cfg.direction == "left":
            return sw - 1, max(0, min(sh - 1, int(ratio_y * sh)))
        elif cfg.direction == "top":
            return max(0, min(sw - 1, int(ratio_x * sw))), sh - 1
        elif cfg.direction == "bottom":
            return max(0, min(sw - 1, int(ratio_x * sw))), 0
        return x, y

    # ???????????????????????????????????????????????????????????????????
    # Activate / deactivate
    # ???????????????????????????????????????????????????????????????????

    def _activate(self, direction: str):
        """Start controlling the server in the given direction."""
        with self._conn_lock:
            entry = self._connections.get(direction)
            if entry is None:
                self._emit(f"Cannot control {direction}: not connected")
                return
            sock, cfg = entry

        with self._active_lock:
            self._active_direction = direction
            self._active_sock = sock
            self._active_config = cfg

        # Move server cursor to entry point
        sx, sy = self._map_to_server(self._mouse_x, self._mouse_y, cfg)
        self._server_mouse_x, self._server_mouse_y = sx, sy
        try:
            self._send_msg(sock, _MSG_POINTER_MOVE,
                           _encode_pointer_move(sx, sy))
        except OSError:
            self._deactivate()
            return

        self._emit(f"? Controlling {cfg.name} [{direction}] -- "
                   f"ESC to release")

        if self.on_control_start:
            self.on_control_start(direction, cfg.host)

    def _deactivate(self):
        """Release control of the current server."""
        with self._active_lock:
            if self._active_direction is None:
                return
            self._active_direction = None
            self._active_sock = None
            self._active_config = None

        self._emit("? Control released")
        if self.on_control_end:
            self.on_control_end()

    # ???????????????????????????????????????????????????????????????????
    # Utilities
    # ???????????????????????????????????????????????????????????????????

    def _send_msg(self, sock: socket.socket, mt: int, payload: bytes = b""):
        """Send a framed message over a raw socket."""
        sock.sendall(_encode_msg(mt, payload))

    def _recv_exact(self, sock: socket.socket, size: int) -> Optional[bytes]:
        chunks: List[bytes] = []
        received = 0
        while received < size:
            try:
                chunk = sock.recv(size - received)
            except OSError:
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _pynput_button_id(button) -> int:
        from pynput.mouse import Button
        mapping = {
            Button.left: 1, Button.middle: 2, Button.right: 3,
            Button.x1: 4, Button.x2: 5,
        }
        return mapping.get(button, 1)

    @staticmethod
    def _key_info(key):
        """Extract (keysym, keycode) from a pynput key."""
        from pynput.keyboard import Key, KeyCode
        if isinstance(key, KeyCode):
            if key.char:
                return ord(key.char), key.vk or 0
            return key.vk or 0, key.vk or 0
        elif isinstance(key, Key):
            mapping = {
                Key.backspace: 0xFF08, Key.tab: 0xFF09, Key.enter: 0xFF0D,
                Key.esc: 0xFF1B, Key.home: 0xFF50, Key.end: 0xFF57,
                Key.left: 0xFF51, Key.up: 0xFF52, Key.right: 0xFF53,
                Key.down: 0xFF54, Key.page_up: 0xFF55, Key.page_down: 0xFF56,
                Key.delete: 0xFFFF, Key.shift: 0xFFE1,
                Key.shift_r: 0xFFE2, Key.ctrl_l: 0xFFE3,
                Key.ctrl_r: 0xFFE4, Key.caps_lock: 0xFFE5,
                Key.alt_l: 0xFFE7, Key.alt_r: 0xFFE8,
                Key.cmd: 0xFFEB, Key.cmd_r: 0xFFEC,
                Key.f1: 0xFFBE, Key.f2: 0xFFBF, Key.f3: 0xFFC0,
                Key.f4: 0xFFC1, Key.f5: 0xFFC2, Key.f6: 0xFFC3,
                Key.f7: 0xFFC4, Key.f8: 0xFFC5, Key.f9: 0xFFC6,
                Key.f10: 0xFFC7, Key.f11: 0xFFC8, Key.f12: 0xFFC9,
                Key.space: 0x0020,
            }
            return mapping.get(key, 0), 0
        return None, None

    def _emit(self, msg: str):
        log.info(f"[mc] {msg}")
        if self.on_status:
            self.on_status(msg)
