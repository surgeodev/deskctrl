"""deskctrl server -- captures screen, streams video, receives input."""

import io
import os
import socket
import struct
import threading
import time
import logging
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
import cv2

from . import __version__
from .protocol import (
    MsgType, HEADER_SIZE, encode_msg, decode_header,
    encode_hello, decode_hello,
    encode_pointer_move, decode_pointer_move,
    encode_pointer_button, decode_pointer_button,
    encode_key_event, decode_key_event,
    encode_scroll, decode_scroll,
    encode_clipboard, decode_clipboard,
    encode_resolution, decode_resolution,
    encode_settings, decode_settings,
    encode_video_frame,
)
from .platform import IS_WINDOWS, IS_LINUX, IS_MACOS

log = logging.getLogger(__name__)

# ---- Screen Capture -----------------------------------------------------------------------------------------------------------------

class ScreenCapture:
    """Cross-platform screen capture using mss."""

    def __init__(self, monitor: int = 1):
        import mss
        self._sct = mss.mss()
        self._monitor_idx = monitor
        self._monitor = self._get_monitor()
        self.width = self._monitor["width"]
        self.height = self._monitor["height"]

    def _get_monitor(self) -> dict:
        monitors = self._sct.monitors
        if self._monitor_idx < len(monitors):
            return monitors[self._monitor_idx]
        return monitors[-1]  # fallback to last monitor

    def capture(self) -> np.ndarray:
        """Capture screen as BGRA numpy array."""
        img = self._sct.grab(self._monitor)
        return np.array(img)  # BGRA

    def update_monitor(self, monitor_idx: int):
        self._monitor_idx = monitor_idx
        self._monitor = self._get_monitor()
        self.width = self._monitor["width"]
        self.height = self._monitor["height"]

    def close(self):
        self._sct.close()


# ---- JPEG Encoder ---------------------------------------------------------------------------------------------------------------------

class JPEGEncoder:
    """Encodes numpy frames to JPEG bytes."""

    def __init__(self, quality: int = 80, target_fps: int = 30):
        self.quality = quality
        self.target_fps = target_fps
        self._encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]

    def encode(self, frame_bgra: np.ndarray) -> tuple:
        """Encode frame to JPEG. Returns (jpeg_bytes, width, height)."""
        # BGRA -> BGR (mss captures in BGRA)
        frame_bgr = cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2BGR)
        height, width = frame_bgr.shape[:2]
        # Resize if needed (optional downscaling)
        success, encoded = cv2.imencode(".jpg", frame_bgr, self._encode_params)
        if not success:
            return b"", width, height
        return encoded.tobytes(), width, height

    def update_quality(self, quality: int):
        self.quality = max(1, min(100, quality))
        self._encode_params[1] = self.quality


# ---- Input Simulator ----------------------------------------------------------------------------------------------------------------

class InputSimulator:
    """Simulate keyboard/mouse input on the host machine."""

    def __init__(self):
        self._mouse = None
        self._keyboard = None
        self._init()

    def _init(self):
        try:
            from pynput.mouse import Controller as MouseCtrl
            from pynput.keyboard import Controller as KeyCtrl
            self._mouse = MouseCtrl()
            self._keyboard = KeyCtrl()
        except ImportError:
            log.warning("pynput not available -- input simulation disabled")

    def move_mouse(self, x: float, y: float, relative: bool = False):
        if not self._mouse:
            return
        if relative:
            self._mouse.move(x, y)
        else:
            self._mouse.position = (x, y)

    def click_mouse(self, button: int, pressed: bool, x: float, y: float):
        if not self._mouse:
            return
        self._mouse.position = (x, y)
        btn = self._pynput_button(button)
        if pressed:
            self._mouse.press(btn)
        else:
            self._mouse.release(btn)

    def scroll(self, dx: float, dy: float):
        if not self._mouse:
            return
        self._mouse.scroll(int(dx), int(dy))

    def key_event(self, keysym: int, keycode: int, pressed: bool):
        if not self._keyboard:
            return
        # Try using keycode / keysym via pynput
        from pynput.keyboard import Key, KeyCode
        # Map common special keys
        special_map = {
            0xFF08: Key.backspace, 0xFF09: Key.tab, 0xFF0D: Key.enter,
            0xFF1B: Key.esc, 0xFF50: Key.home, 0xFF57: Key.end,
            0xFF51: Key.left, 0xFF52: Key.up, 0xFF53: Key.right, 0xFF54: Key.down,
            0xFF55: Key.page_up, 0xFF56: Key.page_down,
            0xFFBE: Key.f1, 0xFFBF: Key.f2, 0xFFC0: Key.f3,
            0xFFC1: Key.f4, 0xFFC2: Key.f5, 0xFFC3: Key.f6,
            0xFFC4: Key.f7, 0xFFC5: Key.f8, 0xFFC6: Key.f9,
            0xFFC7: Key.f10, 0xFFC8: Key.f11, 0xFFC9: Key.f12,
            0xFFE1: Key.shift, 0xFFE2: Key.shift, 0xFFE3: Key.ctrl_l,
            0xFFE4: Key.ctrl_r, 0xFFE5: Key.caps_lock,
            0xFFE7: Key.alt_l, 0xFFE8: Key.alt_r,
            0xFFE9: Key.alt_gr, 0xFFEB: Key.cmd,
            0xFFEC: Key.cmd_r, 0xFFFF: Key.delete,
            0xFF13: Key.pause, 0xFF14: Key.scroll_lock,
            0xFF7F: Key.num_lock, 0xFF61: Key.print_screen,
            0xFE03: Key.alt_gr,
        }
        if keysym in special_map:
            key = special_map[keysym]
        elif keysym > 0 and keysym < 256:
            key = KeyCode.from_char(chr(keysym))
        else:
            key = KeyCode.from_vk(keycode) if keycode else None

        if key is None:
            return

        try:
            if pressed:
                self._keyboard.press(key)
            else:
                self._keyboard.release(key)
        except Exception:
            pass  # pynput can throw on some key combinations

    def _pynput_button(self, button_id: int):
        from pynput.mouse import Button
        if button_id == 1:
            return Button.left
        elif button_id == 2:
            return Button.middle
        elif button_id == 3:
            return Button.right
        elif button_id == 4:
            return Button.x1
        elif button_id == 5:
            return Button.x2
        return Button.left

    def close(self):
        self._mouse = None
        self._keyboard = None


# ---- HDMI Capture Source --------------------------------------------------------------------------------------------------------

class HDMICapture:
    """Capture from HDMI capture card via OpenCV."""

    def __init__(self, device_path: str = None):
        self._device_path = device_path
        self._cap = None
        self.width = 0
        self.height = 0

    def open(self, device_path: str = None) -> bool:
        path = device_path or self._device_path
        if not path:
            # Auto-detect on Linux
            if IS_LINUX:
                path = "/dev/video0"
            elif IS_WINDOWS:
                path = 0  # First DShow device
            elif IS_MACOS:
                path = "0"  # First AVFoundation device

        try:
            if IS_LINUX:
                self._cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
            elif IS_WINDOWS:
                self._cap = cv2.VideoCapture(path, cv2.CAP_DSHOW)
            elif IS_MACOS:
                self._cap = cv2.VideoCapture(int(path) if path.isdigit() else 0)
            else:
                self._cap = cv2.VideoCapture(path)

            if not self._cap or not self._cap.isOpened():
                return False

            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            self._cap.set(cv2.CAP_PROP_FPS, 30)

            self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            return True
        except Exception:
            return False

    def read(self) -> Optional[np.ndarray]:
        if not self._cap:
            return None
        ret, frame = self._cap.read()
        if not ret:
            return None
        return frame  # BGR

    def close(self):
        if self._cap:
            self._cap.release()


# ---- Server Engine --------------------------------------------------------------------------------------------------------------------

class DeskctrlServer:
    """Main server that accepts client connections and streams screen."""

    def __init__(self, host: str = "0.0.0.0", port: int = 5830,
                 fps: int = 30, quality: int = 80,
                 monitor: int = 1, no_display: bool = False):
        self.host = host
        self.port = port
        self.target_fps = fps
        self.jpeg_quality = quality
        self.monitor_idx = monitor
        self.no_display = no_display  # --nowindow equivalent

        self._server_socket: Optional[socket.socket] = None
        self._client_socket: Optional[socket.socket] = None
        self._client_addr: Optional[tuple] = None
        self._running = False
        self._streaming = False
        self._hdmi_mode = False
        self._frame_interval = 1.0 / fps

        # Components
        self._capture: Optional[ScreenCapture] = None
        self._hdmi_cap: Optional[HDMICapture] = None
        self._encoder: Optional[JPEGEncoder] = None
        self._input: Optional[InputSimulator] = None

        # Callbacks
        self.on_client_connected: Optional[Callable] = None
        self.on_client_disconnected: Optional[Callable] = None
        self.on_status: Optional[Callable] = None

        # Tracking
        self._client_thread: Optional[threading.Thread] = None
        self._stream_thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._fps_counter = 0
        self._fps_timer = time.time()
        self._lock = threading.RLock()  # Use RLock because _disconnect_client is called inside locked sections

    @property
    def connected(self) -> bool:
        return self._client_socket is not None

    def start(self) -> bool:
        """Start the server (listening for connections)."""
        try:
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.settimeout(1.0)  # Allow checking _running
            self._server_socket.bind((self.host, self.port))
            self._server_socket.listen(1)
            self._running = True
            self._emit_status(f"Server listening on {self.host}:{self.port}")

            # Accept clients in background
            self._client_thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._client_thread.start()
            return True
        except OSError as e:
            self._emit_status(f"Failed to start server: {e}")
            return False

    def stop(self):
        """Stop the server."""
        self._running = False
        self._disconnect_client()
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None
        if self._capture:
            self._capture.close()
            self._capture = None
        if self._hdmi_cap:
            self._hdmi_cap.close()
            self._hdmi_cap = None
        self._emit_status("Server stopped")

    def toggle_hdmi(self, device_path: str = None) -> bool:
        """Toggle between screen capture and HDMI capture."""
        if self._hdmi_mode:
            # Switch back to screen capture
            self._hdmi_mode = False
            if self._hdmi_cap:
                self._hdmi_cap.close()
                self._hdmi_cap = None
            self._capture = ScreenCapture(self.monitor_idx)
            self._emit_status("Switched to screen capture mode")
        else:
            # Switch to HDMI
            hdmi = HDMICapture()
            if hdmi.open(device_path):
                self._hdmi_cap = hdmi
                self._hdmi_mode = True
                if self._capture:
                    self._capture.close()
                    self._capture = None
                self._emit_status(f"Switched to HDMI capture: {device_path}")
                return True
            else:
                self._emit_status(f"Failed to open HDMI device: {device_path}")
                return False
        return True

    def _emit_status(self, msg: str):
        log.info(msg)
        if self.on_status:
            self.on_status(msg)

    def _accept_loop(self):
        """Background thread: accept one client at a time."""
        while self._running:
            try:
                client, addr = self._server_socket.accept()
                log.debug(f"Accepted connection from {addr}")
                with self._lock:
                    # Disconnect previous client if any
                    self._disconnect_client()
                    self._client_socket = client
                    self._client_addr = addr
                self._emit_status(f"Client connected from {addr[0]}:{addr[1]}")
                if self.on_client_connected:
                    self.on_client_connected(addr)
                # Handle client (handshake, stream, input)
                self._handle_client(client)
            except socket.timeout:
                continue
            except OSError:
                break

    def _disconnect_client(self):
        """Disconnect current client."""
        with self._lock:
            if self._client_socket:
                try:
                    self._client_socket.close()
                except Exception:
                    pass
                self._client_socket = None
                self._client_addr = None
            self._streaming = False
        # Stop keepalive
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_thread = None

    def _handle_client(self, client: socket.socket):
        """Handle a connected client: send HELLO, then stream/input loop."""
        # Initialize components
        self._encoder = JPEGEncoder(self.jpeg_quality, self.target_fps)
        self._input = InputSimulator()
        if not self._hdmi_mode:
            try:
                self._capture = ScreenCapture(self.monitor_idx)
                self._emit_status(f"Screen: {self._capture.width}x{self._capture.height}")
            except Exception as e:
                self._emit_status(f"Screen capture init error: {e}")
                self._disconnect_client()
                return
        try:
            # Send HELLO
            client.sendall(encode_msg(MsgType.HELLO, encode_hello(__version__)))

            # Wait for HELLO_ACK
            data = self._recv_exact(client, HEADER_SIZE)
            if not data:
                raise ConnectionError("No handshake response")
            msg_type, length = decode_header(data)
            monitor_mode = False
            if msg_type == MsgType.HELLO_ACK:
                payload = self._recv_exact(client, length)
                if payload:
                    info = decode_hello(payload)
                    client_ver = info.get('version', '?')
                    monitor_mode = "-monitor" in client_ver
                    self._emit_status(f"Client version: {client_ver}")
                    if monitor_mode:
                        self._emit_status("Monitor mode: skipping video stream")
            else:
                self._emit_status(f"Unexpected handshake message: {msg_type}")
                return

            # Send resolution info
            if self._capture:
                res_payload = encode_resolution(self._capture.width, self._capture.height)
                client.sendall(encode_msg(MsgType.RESOLUTION, res_payload))

            # Start streaming (skip for monitor-mode clients)
            self._streaming = True
            if not monitor_mode:
                self._stream_thread = threading.Thread(
                    target=self._stream_loop, args=(client,), daemon=True
                )
                self._stream_thread.start()

            # Start keepalive
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop, args=(client,), daemon=True
            )
            self._keepalive_thread.start()

            # Main loop: receive input events
            self._input_loop(client)

        except (ConnectionError, OSError) as e:
            self._emit_status(f"Client disconnected: {e}")
        except Exception as e:
            log.exception("Error handling client")
            self._emit_status(f"Error: {e}")
        finally:
            self._streaming = False
            if self._capture:
                self._capture.close()
                self._capture = None
            if self._hdmi_cap:
                self._hdmi_cap.close()
                self._hdmi_cap = None
            if self._input:
                self._input.close()
                self._input = None
            addr = self._client_addr
            self._disconnect_client()
            if addr and self.on_client_disconnected:
                self.on_client_disconnected(addr)
            self._emit_status("Client disconnected")

    def _input_loop(self, client: socket.socket):
        """Receive and process input messages from client."""
        buffer = bytearray()
        while self._streaming and self._running:
            try:
                data = client.recv(4096)
            except OSError:
                break
            if not data:
                break
            buffer.extend(data)
            while len(buffer) >= HEADER_SIZE:
                msg_type, length = decode_header(bytes(buffer[:HEADER_SIZE]))
                total = HEADER_SIZE + length
                if len(buffer) < total:
                    break
                payload = bytes(buffer[HEADER_SIZE:total])
                buffer = buffer[total:]
                self._process_input(msg_type, payload)

    def _process_input(self, msg_type: MsgType, payload: bytes):
        """Process a single input message from client."""
        try:
            if msg_type == MsgType.POINTER_MOVE:
                x, y, rel = decode_pointer_move(payload)
                self._input.move_mouse(x, y, rel)
            elif msg_type == MsgType.POINTER_BUTTON:
                button, pressed, x, y = decode_pointer_button(payload)
                self._input.click_mouse(button, pressed, x, y)
            elif msg_type == MsgType.KEY_EVENT:
                keysym, keycode, pressed = decode_key_event(payload)
                self._input.key_event(keysym, keycode, pressed)
            elif msg_type == MsgType.SCROLL:
                dx, dy = decode_scroll(payload)
                self._input.scroll(dx, dy)
            elif msg_type == MsgType.CLIPBOARD:
                text = decode_clipboard(payload)
                self._handle_clipboard(text)
            elif msg_type == MsgType.SETTINGS:
                settings = decode_settings(payload)
                self._apply_settings(settings)
            elif msg_type == MsgType.HDMI_TOGGLE:
                self.toggle_hdmi()
            elif msg_type == MsgType.DISCONNECT:
                self._emit_status("Client requested disconnect")
                self._streaming = False
            elif msg_type == MsgType.KEEPALIVE:
                pass  # Just a ping, no action needed
        except Exception as e:
            log.debug(f"Error processing input {msg_type}: {e}")

    def _handle_clipboard(self, text: str):
        """Set clipboard text on the server (host) machine."""
        try:
            import pyperclip
            pyperclip.copy(text)
        except ImportError:
            pass

    def _apply_settings(self, settings: dict):
        """Apply settings from client."""
        if "quality" in settings and self._encoder:
            self._encoder.update_quality(settings["quality"])
        if "fps" in settings:
            self.target_fps = settings["fps"]
            self._frame_interval = 1.0 / self.target_fps
        if "monitor" in settings:
            self.monitor_idx = settings["monitor"]
            if self._capture:
                self._capture.update_monitor(self.monitor_idx)

    def _stream_loop(self, client: socket.socket):
        """Background thread: capture and send frames."""
        last_frame_time = 0
        self._fps_counter = 0
        self._fps_timer = time.time()

        while self._streaming and self._running:
            now = time.time()
            if now - last_frame_time < self._frame_interval:
                # Sleep a tiny bit to avoid busy-waiting
                time.sleep(max(0, self._frame_interval - (now - last_frame_time)) / 2)
                continue

            try:
                if self._hdmi_mode and self._hdmi_cap:
                    frame = self._hdmi_cap.read()
                    if frame is None:
                        continue
                    # Convert BGR to BGRA for consistent handling
                    frame_bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
                elif self._capture:
                    frame_bgra = self._capture.capture()
                else:
                    continue

                encoded, w, h = self._encoder.encode(frame_bgra)
                if not encoded:
                    continue

                video_payload = encode_video_frame(encoded, "jpeg", w, h)
                msg = encode_msg(MsgType.VIDEO_FRAME, video_payload)
                try:
                    client.sendall(msg)
                except OSError:
                    break

                self._fps_counter += 1
                if self._fps_counter >= self.target_fps:
                    elapsed = time.time() - self._fps_timer
                    actual_fps = self._fps_counter / elapsed if elapsed > 0 else 0
                    log.debug(f"Stream FPS: {actual_fps:.1f}")
                    self._fps_counter = 0
                    self._fps_timer = time.time()

                last_frame_time = now

            except Exception as e:
                log.debug(f"Stream error: {e}")
                break

    def _keepalive_loop(self, client: socket.socket):
        """Send periodic keepalive pings."""
        while self._streaming and self._running:
            time.sleep(5)
            try:
                client.sendall(encode_msg(MsgType.KEEPALIVE))
            except OSError:
                break

    def _recv_exact(self, sock: socket.socket, size: int) -> Optional[bytes]:
        """Receive exactly `size` bytes."""
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
