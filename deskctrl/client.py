"""
TCP client: connects to deskctrl server, handles handshake, receive loop,
and dispatches to display module.
"""

from __future__ import annotations
import json
import queue
import socket
import threading
import sys
from typing import Callable, Optional
from . import protocol
from .clipboard import ClipboardWatcher, set_text


DEFAULT_PORT = 5900
FRAME_BUFFER = 4  # max queued frames before drop


class Client:
    def __init__(self, host: str, port: int, monitor: int = 0,
                 mode: str = "connect"):
        """
        Args:
            host:    Remote hostname or IP.
            port:    TCP port.
            monitor: Which remote monitor to stream (0 = primary).
            mode:    "connect" (fullscreen) or "extend" (side panel).
        """
        self._host = host
        self._port = port
        self._monitor = monitor
        self._mode = mode
        self._sock: socket.socket | None = None
        self._frame_queue: queue.Queue[bytes] = queue.Queue(maxsize=FRAME_BUFFER)
        self._remote_w = 1920
        self._remote_h = 1080
        self._running = False
        self._clipboard_watcher: ClipboardWatcher | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def connect_and_run(self) -> None:
        self._sock = socket.create_connection((self._host, self._port), timeout=10)
        self._sock.settimeout(None)
        self._running = True

        # Handshake: send SETTINGS
        settings = {
            "mode": self._mode,
            "monitor": self._monitor,
            "client_version": "0.1.0",
        }
        protocol.send_message(self._sock, protocol.MSG_SETTINGS,
                              protocol.encode_settings(settings))

        # Receive server SETTINGS (resolution, etc.)
        msg_type, payload = protocol.recv_message(self._sock)
        if msg_type == protocol.MSG_SETTINGS:
            srv = protocol.decode_settings(payload)
            self._remote_w = srv.get("width", 1920)
            self._remote_h = srv.get("height", 1080)

        # Start receive loop in background thread
        recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
        recv_thread.start()

        # Start clipboard watcher
        self._clipboard_watcher = ClipboardWatcher(self._on_local_clipboard_change)
        self._clipboard_watcher.start()

        # Run display (blocks until window is closed)
        self._run_display()

        # Cleanup
        self._running = False
        if self._clipboard_watcher:
            self._clipboard_watcher.stop()
        try:
            self._sock.close()
        except Exception:
            pass

    # ── Private ───────────────────────────────────────────────────────────────

    def _send(self, msg_type: int, payload: bytes) -> None:
        if self._sock and self._running:
            try:
                protocol.send_message(self._sock, msg_type, payload)
            except Exception:
                pass

    def _receive_loop(self) -> None:
        while self._running:
            try:
                msg_type, payload = protocol.recv_message(self._sock)
            except Exception:
                self._running = False
                break

            if msg_type == protocol.MSG_FRAME:
                # Drop oldest frame if queue is full (avoid backpressure lag)
                if self._frame_queue.full():
                    try:
                        self._frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    self._frame_queue.put_nowait(payload)
                except queue.Full:
                    pass

            elif msg_type == protocol.MSG_CLIPBOARD:
                text = payload.decode("utf-8", errors="replace")
                set_text(text)

            elif msg_type == protocol.MSG_PING:
                self._send(protocol.MSG_PONG, b"")

    def _on_local_clipboard_change(self, text: str) -> None:
        self._send(protocol.MSG_CLIPBOARD, text.encode("utf-8"))

    def _run_display(self) -> None:
        if self._mode == "extend":
            from .display_extend import run as run_extend
            run_extend(
                frame_queue=self._frame_queue,
                remote_w=self._remote_w,
                remote_h=self._remote_h,
                send_input_fn=self._send,
            )
        else:
            from .display import run as run_fullscreen
            run_fullscreen(
                sock=self._sock,
                frame_queue=self._frame_queue,
                width=self._remote_w,
                height=self._remote_h,
                send_input_fn=self._send,
            )


# ── v0.2.7 compatibility wrapper for PyQt6 GUI ──────────────────────────

DISPLAY_QT = "qt"


class DeskctrlClient:
    """Compatibility wrapper providing v0.2.7 DeskctrlClient API over zip's Client.

    Runs the connection in a background thread and delivers frames via a queue
    that the GUI polls on a timer. Provides callbacks: on_status, on_frame,
    on_connected, on_disconnected, on_resolution.
    """

    def __init__(self, host: str, port: int, display_mode: str = "qt",
                 monitor: int = 0):
        self._host = host
        self._port = port
        self._monitor = monitor
        self._display_mode = display_mode
        self._thread: threading.Thread | None = None
        self._frame_queue: queue.Queue[bytes] = queue.Queue(maxsize=4)

        # Callbacks
        self.on_status: Callable[[str], None] | None = None
        self.on_frame: Callable[[object], None] | None = None
        self.on_connected: Callable[[], None] | None = None
        self.on_disconnected: Callable[[], None] | None = None
        self.on_resolution: Callable[[int, int], None] | None = None

        # State object
        class _State:
            connected = False
            fps = 0.0
            bitrate = 0.0
        self.state = _State()

        self._running = False
        self._sock: socket.socket | None = None

    def connect(self) -> bool:
        """Non-blocking connect. Returns False immediately on failure."""
        try:
            self._sock = socket.create_connection(
                (self._host, self._port), timeout=10
            )
        except OSError as e:
            self._emit_status(f"Connection failed: {e}")
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def disconnect(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self.state.connected = False

    def send_settings(self, quality: int = 60, fps: int = 30,
                      monitor: int = 0):
        """Send updated settings to the server."""
        settings = {
            "mode": "connect",
            "monitor": monitor,
            "quality": quality,
            "fps": fps,
        }
        if self._sock and self._running:
            try:
                protocol.send_message(self._sock, protocol.MSG_SETTINGS,
                                      protocol.encode_settings(settings))
            except Exception:
                pass

    def toggle_hdmi(self):
        """HDMI toggle (stub — not supported in v2 core)."""
        pass

    def _run(self):
        try:
            # Send SETTINGS
            settings = {
                "mode": "connect",
                "monitor": self._monitor,
                "client_version": "2.0.1",
            }
            protocol.send_message(self._sock, protocol.MSG_SETTINGS,
                                  protocol.encode_settings(settings))

            # Receive server SETTINGS
            msg_type, payload = protocol.recv_message(self._sock)
            if msg_type == protocol.MSG_SETTINGS:
                srv = protocol.decode_settings(payload)
                w = srv.get("width", 1920)
                h = srv.get("height", 1080)
                self.state.connected = True
                self._emit_status(f"Connected — {w}x{h}")
                if self.on_connected:
                    self.on_connected()
                if self.on_resolution:
                    self.on_resolution(w, h)
            else:
                self._emit_status(f"Unexpected msg type: {msg_type}")
                return

            # Receive loop
            while self._running:
                try:
                    msg_type, payload = protocol.recv_message(self._sock)
                except Exception:
                    break

                if msg_type == protocol.MSG_FRAME:
                    # Decode JPEG to numpy array
                    try:
                        from PIL import Image
                        import io as _io
                        import numpy as np
                        buf = _io.BytesIO(payload)
                        img = Image.open(buf)
                        frame = np.array(img)
                        frame = frame[:, :, ::-1]  # RGB → BGR
                        if self.on_frame:
                            self.on_frame(frame)
                    except Exception:
                        pass

                elif msg_type == protocol.MSG_CLIPBOARD:
                    text = payload.decode("utf-8", errors="replace")
                    set_text(text)

                elif msg_type == protocol.MSG_PING:
                    try:
                        protocol.send_message(self._sock, protocol.MSG_PONG, b"")
                    except Exception:
                        pass

        except Exception as e:
            self._emit_status(f"Error: {e}")
        finally:
            self.state.connected = False
            if self.on_disconnected:
                self.on_disconnected()
            self.disconnect()

    def _emit_status(self, msg: str):
        if self.on_status:
            self.on_status(msg)
