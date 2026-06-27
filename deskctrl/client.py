"""deskctrl client -- receives video, sends input, optional display."""

import io
import os
import socket
import threading
import time
import logging
from typing import Optional, Callable
from dataclasses import dataclass, field

import numpy as np
from PIL import Image
import cv2

from . import __version__
from .protocol import (
    MsgType, HEADER_SIZE, encode_msg, decode_header,
    encode_hello, decode_hello,
    encode_pointer_move, encode_pointer_button,
    encode_key_event, encode_scroll,
    encode_clipboard, encode_settings,
    decode_resolution, decode_clipboard,
    encode_video_frame,
)

log = logging.getLogger(__name__)

# ---- Display modes --------------------------------------------------------------------------------------------------------------------

DISPLAY_NONE = 0     # No display (--nowindow / headless control)
DISPLAY_OPENCV = 1   # OpenCV imshow window (fallback)
DISPLAY_PYGAME = 2   # Pygame window (low latency)
DISPLAY_QT = 3       # PyQt6 widget (for GUI integration)


# ---- Client state ---------------------------------------------------------------------------------------------------------------------

@dataclass
class ClientState:
    """Tracking state for the client session."""
    connected: bool = False
    server_version: str = ""
    screen_width: int = 0
    screen_height: int = 0
    connected_at: float = 0.0
    frames_received: int = 0
    last_frame_time: float = 0.0
    fps: float = 0.0
    bitrate: float = 0.0  # bits/sec
    latency_ms: float = 0.0
    hdmi_mode: bool = False


# ---- JPEG Decoder ---------------------------------------------------------------------------------------------------------------------

class JPEGDecoder:
    """Decode JPEG bytes to numpy array (BGR)."""

    def decode(self, data: bytes) -> Optional[np.ndarray]:
        """Decode JPEG to BGR numpy array."""
        arr = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return frame


# ---- Input Capturer -----------------------------------------------------------------------------------------------------------------

class InputCapturer:
    """Capture keyboard and mouse input to send to server.

    Coordinates from the client are scaled to the server's screen
    resolution so the remote cursor matches the local pointer.
    """

    def __init__(self, send_callback: Callable[[MsgType, bytes], None],
                 server_width: int = 0, server_height: int = 0):
        self._send = send_callback
        self._server_w = server_width
        self._server_h = server_height
        self._client_w, self._client_h = self._get_client_screen_size()
        self._mouse_listener = None
        self._keyboard_listener = None
        self._running = False

    def set_server_resolution(self, w: int, h: int):
        """Update server resolution (received after handshake)."""
        self._server_w = w
        self._server_h = h

    def _get_client_screen_size(self):
        """Detect the client's own screen dimensions."""
        try:
            import mss
            with mss.mss() as sct:
                mon = sct.monitors[1]  # primary monitor
                return mon["width"], mon["height"]
        except Exception:
            return 1920, 1080  # safe fallback

    def _scale(self, x: float, y: float):
        """Scale client coordinates to server screen space."""
        if self._server_w and self._server_h and self._client_w and self._client_h:
            return (
                x * self._server_w / self._client_w,
                y * self._server_h / self._client_h,
            )
        return x, y

    def start(self):
        """Start listening for input events."""
        self._running = True
        from pynput import mouse, keyboard

        self._mouse_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_scroll,
        )
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def stop(self):
        """Stop listening."""
        self._running = False
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()

    def _on_mouse_move(self, x, y):
        if not self._running:
            return
        sx, sy = self._scale(x, y)
        payload = encode_pointer_move(sx, sy, relative=False)
        self._send(MsgType.POINTER_MOVE, payload)

    def _on_mouse_click(self, x, y, button, pressed):
        if not self._running:
            return
        btn_id = self._button_id(button)
        sx, sy = self._scale(x, y)
        payload = encode_pointer_button(btn_id, pressed, sx, sy)
        self._send(MsgType.POINTER_BUTTON, payload)

    def _on_scroll(self, x, y, dx, dy):
        if not self._running:
            return
        payload = encode_scroll(dx, dy)
        self._send(MsgType.SCROLL, payload)

    def _on_key_press(self, key):
        if not self._running:
            return
        keysym, keycode = self._key_info(key)
        if keysym is not None:
            payload = encode_key_event(keysym, keycode, True)
            self._send(MsgType.KEY_EVENT, payload)

    def _on_key_release(self, key):
        if not self._running:
            return
        keysym, keycode = self._key_info(key)
        if keysym is not None:
            payload = encode_key_event(keysym, keycode, False)
            self._send(MsgType.KEY_EVENT, payload)

    def _button_id(self, button):
        from pynput.mouse import Button
        mapping = {
            Button.left: 1, Button.middle: 2, Button.right: 3,
            Button.x1: 4, Button.x2: 5,
        }
        return mapping.get(button, 1)

    def _key_info(self, key):
        """Extract keysym and keycode from a pynput key."""
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


# ---- Client Engine --------------------------------------------------------------------------------------------------------------------

class DeskctrlClient:
    """Main client that connects to server and manages the session."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5830,
                 display_mode: int = DISPLAY_NONE,
                 quality: int = 80, fps: int = 30):
        self.host = host
        self.port = port
        self.display_mode = display_mode
        self.quality = quality
        self.target_fps = fps

        self._socket: Optional[socket.socket] = None
        self._running = False
        self._streaming = False

        # Components
        self._decoder = JPEGDecoder()
        self._input: Optional[InputCapturer] = None

        # State
        self.state = ClientState()
        self._frame_buffer: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()

        # Callbacks
        self.on_frame: Optional[Callable[[np.ndarray], None]] = None
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None
        self.on_status: Optional[Callable] = None
        self.on_resolution: Optional[Callable[[int, int], None]] = None

        # Threads
        self._receive_thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._display_thread: Optional[threading.Thread] = None

        # Send queue
        self._send_lock = threading.Lock()
        self._send_buffer = bytearray()
        self._send_thread: Optional[threading.Thread] = None

    def connect(self) -> bool:
        """Connect to the server and perform handshake."""
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(10.0)
            self._socket.connect((self.host, self.port))
            self._socket.settimeout(None)  # Back to blocking mode
            self._emit_status(f"Connected to {self.host}:{self.port}")

            # Handshake: wait for HELLO, send HELLO_ACK
            data = self._recv_exact(self._socket, HEADER_SIZE)
            if not data:
                raise ConnectionError("No server hello")
            msg_type, length = decode_header(data)
            if msg_type != MsgType.HELLO:
                raise ConnectionError(f"Expected HELLO, got {msg_type}")
            payload = self._recv_exact(self._socket, length)
            if payload:
                info = decode_hello(payload)
                self.state.server_version = info.get("version", "?")
                self._emit_status(f"Server version: {self.state.server_version}")

            # Send HELLO_ACK
            self._socket.sendall(
                encode_msg(MsgType.HELLO_ACK, encode_hello(__version__))
            )

            # Wait for resolution
            data = self._recv_exact(self._socket, HEADER_SIZE)
            if not data:
                raise ConnectionError("No resolution info")
            msg_type, length = decode_header(data)
            if msg_type == MsgType.RESOLUTION:
                payload = self._recv_exact(self._socket, length)
                if payload:
                    w, h = decode_resolution(payload)
                    self.state.screen_width = w
                    self.state.screen_height = h
                    self._emit_status(f"Server screen: {w}x{h}")
                    if self._input:
                        self._input.set_server_resolution(w, h)
                    if self.on_resolution:
                        self.on_resolution(w, h)

            self.state.connected = True
            self.state.connected_at = time.time()
            self._running = True
            self._streaming = True

            # Start receive thread
            self._receive_thread = threading.Thread(
                target=self._receive_loop, daemon=True
            )
            self._receive_thread.start()

            # Start send thread
            self._send_thread = threading.Thread(
                target=self._send_loop, daemon=True
            )
            self._send_thread.start()

            # Start keepalive
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop, daemon=True
            )
            self._keepalive_thread.start()

            # Start input capture (if display mode allows input)
            if self.display_mode != DISPLAY_NONE:
                self._input = InputCapturer(
                    self._send_input,
                    server_width=self.state.screen_width,
                    server_height=self.state.screen_height,
                )
                self._input.start()

            if self.on_connected:
                self.on_connected()

            return True

        except (ConnectionError, OSError, TimeoutError) as e:
            self._emit_status(f"Connection failed: {e}")
            self.disconnect()
            return False

    def disconnect(self):
        """Disconnect from server."""
        self._streaming = False
        self._running = False

        if self._input:
            self._input.stop()
            self._input = None

        # Send disconnect
        if self._socket:
            try:
                self._socket.sendall(encode_msg(MsgType.DISCONNECT))
            except Exception:
                pass
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        self.state.connected = False
        if self.on_disconnected:
            self.on_disconnected()

    def send_input(self, msg_type: MsgType, payload: bytes):
        """Send an input event to the server."""
        self._send_input(msg_type, payload)

    def send_settings(self, **kwargs):
        """Send settings update to server."""
        payload = encode_settings(**kwargs)
        self._raw_send(MsgType.SETTINGS, payload)

    def toggle_hdmi(self, device: str = None):
        """Request HDMI mode toggle."""
        payload = encode_settings(hdmi_device=device) if device else b"\x01"
        self._raw_send(MsgType.HDMI_TOGGLE, payload)

    def get_frame(self) -> Optional[np.ndarray]:
        """Get the latest decoded frame."""
        with self._frame_lock:
            if self._frame_buffer is not None:
                return self._frame_buffer.copy()
            return None

    # ---- Internal -------------------------------------------------------------------------------------------------------------

    def _emit_status(self, msg: str):
        log.info(msg)
        if self.on_status:
            self.on_status(msg)

    def _recv_exact(self, sock: socket.socket, size: int) -> Optional[bytes]:
        chunks = []
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

    def _send_input(self, msg_type: MsgType, payload: bytes):
        """Queue input data to be sent."""
        with self._send_lock:
            self._send_buffer.extend(encode_msg(msg_type, payload))

    def _raw_send(self, msg_type: MsgType, payload: bytes):
        """Send immediately."""
        if self._socket:
            try:
                self._socket.sendall(encode_msg(msg_type, payload))
            except OSError:
                pass

    def _send_loop(self):
        """Background thread: flush send buffer."""
        while self._streaming and self._running:
            time.sleep(0.005)  # 5ms flush interval
            with self._send_lock:
                if not self._send_buffer:
                    continue
                try:
                    if self._socket:
                        self._socket.sendall(bytes(self._send_buffer))
                    self._send_buffer.clear()
                except OSError:
                    break

    def _receive_loop(self):
        """Receive messages from server."""
        buffer = bytearray()
        frame_timer = time.time()
        frame_count = 0
        byte_count = 0

        while self._streaming and self._running:
            try:
                data = self._socket.recv(65536)
            except OSError:
                break
            if not data:
                break

            buffer.extend(data)
            byte_count += len(data)

            while len(buffer) >= HEADER_SIZE:
                try:
                    msg_type, length = decode_header(bytes(buffer[:HEADER_SIZE]))
                except Exception:
                    # Bad header, advance by 1
                    buffer.pop(0)
                    continue
                total = HEADER_SIZE + length
                if len(buffer) < total:
                    break

                payload = bytes(buffer[HEADER_SIZE:total])
                buffer = buffer[total:]

                self._process_message(msg_type, payload)

                frame_count += 1
                elapsed = time.time() - frame_timer
                if elapsed >= 2.0:
                    self.state.fps = frame_count / elapsed
                    self.state.bitrate = (byte_count * 8) / elapsed
                    frame_count = 0
                    byte_count = 0
                    frame_timer = time.time()

    def _process_message(self, msg_type: MsgType, payload: bytes):
        """Process a single message from the server."""
        try:
            if msg_type == MsgType.VIDEO_FRAME:
                self._handle_video_frame(payload)
            elif msg_type == MsgType.RESOLUTION:
                w, h = decode_resolution(payload)
                self.state.screen_width = w
                self.state.screen_height = h
                if self._input:
                    self._input.set_server_resolution(w, h)
                if self.on_resolution:
                    self.on_resolution(w, h)
            elif msg_type == MsgType.KEEPALIVE:
                self._raw_send(MsgType.KEEPALIVE, b"")
            elif msg_type == MsgType.DISCONNECT:
                self._emit_status("Server requested disconnect")
                self.disconnect()
            elif msg_type == MsgType.CLIPBOARD:
                text = decode_clipboard(payload)
                self._update_local_clipboard(text)
        except Exception as e:
            log.debug(f"Error processing {msg_type}: {e}")

    def _handle_video_frame(self, payload: bytes):
        """Process a video frame message."""
        if len(payload) < 13:  # Meta header (1+2+2+8) + at least some data
            return
        # Parse video frame header: frame_type(1) + width(2) + height(2) + pts(8)
        frame_type = payload[0]
        width = int.from_bytes(payload[1:3], "big")
        height = int.from_bytes(payload[3:5], "big")
        pts = int.from_bytes(payload[5:13], "big")
        frame_data = payload[13:]

        frame = self._decoder.decode(frame_data)
        if frame is not None:
            with self._frame_lock:
                self._frame_buffer = frame
            self.state.frames_received += 1
            self.state.last_frame_time = time.time()
            if self.on_frame:
                self.on_frame(frame)

    def _update_local_clipboard(self, text: str):
        try:
            import pyperclip
            pyperclip.copy(text)
        except ImportError:
            pass

    def _keepalive_loop(self):
        while self._streaming and self._running:
            time.sleep(5)
            self._raw_send(MsgType.KEEPALIVE, b"")
