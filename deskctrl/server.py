"""
TCP server: captures the desktop, streams frames, receives and injects input.

Usage (via CLI):
    deskctrl serve [--monitor N] [--virtual]

One ServerSession is created per connected client. The server supports
multiple simultaneous clients but only one per physical monitor slot.

For "extend" mode clients, video streaming is skipped — only input injection
is active (they supply a virtual monitor canvas for the OS to render to).
"""

from __future__ import annotations
import io
import json
import socket
import threading
import time
import sys
from typing import Callable
from . import protocol, __version__
from .clipboard import set_text, get_text, ClipboardWatcher

try:
    import mss
    import mss.tools
    _MSS_AVAILABLE = True
except ImportError:
    _MSS_AVAILABLE = False

DEFAULT_PORT = 5900
JPEG_QUALITY = 60
TARGET_FPS = 30


def _capture_frame(sct, monitor_index: int, quality: int) -> bytes:
    """Capture a single frame as JPEG bytes using mss."""
    monitors = sct.monitors  # [0] = all, [1] = primary, [2]...
    idx = min(monitor_index + 1, len(monitors) - 1)  # sct.monitors[0] is "all"
    mon = monitors[idx]
    img = sct.grab(mon)
    buf = io.BytesIO()
    from PIL import Image
    pil = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
    pil.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class ServerSession(threading.Thread):
    def __init__(self, sock: socket.socket, addr, monitor: int = 0,
                 virtual: bool = False):
        super().__init__(daemon=True)
        self._sock = sock
        self._addr = addr
        self._monitor = monitor
        self._virtual = virtual
        self._running = False
        self._mode = "connect"
        self._client_monitor = 0

    def run(self) -> None:
        self._running = True
        print(f"[server] Client connected: {self._addr}")

        try:
            from .input_controller import InputController
            ctrl = InputController()
        except Exception as e:
            print(f"[server] InputController unavailable: {e}")
            ctrl = None

        try:
            # Receive client SETTINGS
            msg_type, payload = protocol.recv_message(self._sock)
            if msg_type != protocol.MSG_SETTINGS:
                print("[server] Expected SETTINGS, got:", msg_type)
                return

            client_settings = protocol.decode_settings(payload)
            self._mode = client_settings.get("mode", "connect")
            self._client_monitor = client_settings.get("monitor", 0)

            # Determine monitor to capture
            mon_idx = self._client_monitor if self._client_monitor else self._monitor

            # Get screen dimensions
            width, height = 1920, 1080
            if _MSS_AVAILABLE:
                with mss.mss() as sct:
                    monitors = sct.monitors
                    idx = min(mon_idx + 1, len(monitors) - 1)
                    m = monitors[idx]
                    width = m["width"]
                    height = m["height"]

            # Send server SETTINGS
            srv_settings = {
                "width": width,
                "height": height,
                "monitor": mon_idx,
                "server_version": "0.1.0",
            }
            protocol.send_message(self._sock, protocol.MSG_SETTINGS,
                                  protocol.encode_settings(srv_settings))

            if ctrl:
                ctrl.reset()

            is_extend = (self._mode == "extend")

            # Start video streaming thread (skipped for extend mode)
            if not is_extend:
                stream_thread = threading.Thread(
                    target=self._stream_loop,
                    args=(mon_idx, width, height),
                    daemon=True,
                )
                stream_thread.start()

            # Start clipboard watcher (send local clipboard changes to client)
            clipboard_watcher = ClipboardWatcher(
                lambda text: self._send_clipboard(text)
            )
            clipboard_watcher.start()

            # Input receive loop
            self._input_loop(ctrl)

        except Exception as e:
            print(f"[server] Session error: {e}")
        finally:
            self._running = False
            try:
                self._sock.close()
            except Exception:
                pass
            print(f"[server] Client disconnected: {self._addr}")

    def _send(self, msg_type: int, payload: bytes) -> None:
        if self._running:
            try:
                protocol.send_message(self._sock, msg_type, payload)
            except Exception:
                self._running = False

    def _send_clipboard(self, text: str) -> None:
        self._send(protocol.MSG_CLIPBOARD, text.encode("utf-8"))

    def _stream_loop(self, mon_idx: int, width: int, height: int) -> None:
        interval = 1.0 / TARGET_FPS
        if not _MSS_AVAILABLE:
            print("[server] mss not available, no video stream.")
            return
        with mss.mss() as sct:
            while self._running:
                t0 = time.monotonic()
                try:
                    frame = _capture_frame(sct, mon_idx, JPEG_QUALITY)
                    self._send(protocol.MSG_FRAME, frame)
                except Exception as e:
                    print(f"[server] Stream error: {e}")
                    break
                elapsed = time.monotonic() - t0
                sleep = interval - elapsed
                if sleep > 0:
                    time.sleep(sleep)

    def _input_loop(self, ctrl) -> None:
        while self._running:
            try:
                msg_type, payload = protocol.recv_message(self._sock)
            except Exception:
                self._running = False
                break

            if msg_type == protocol.MSG_INPUT_KEY:
                pressed, keysym = protocol.decode_key(payload)
                if ctrl:
                    if pressed:
                        ctrl.key_down(keysym)
                    else:
                        ctrl.key_up(keysym)

            elif msg_type == protocol.MSG_INPUT_MOUSE:
                evt = protocol.decode_mouse(payload)
                if ctrl:
                    if evt["type"] == "move":
                        ctrl.mouse_move(evt["x"], evt["y"])
                    elif evt["type"] == "press":
                        ctrl.mouse_press(evt["x"], evt["y"], evt["button"])
                    elif evt["type"] == "release":
                        ctrl.mouse_release(evt["x"], evt["y"], evt["button"])
                    elif evt["type"] == "scroll":
                        ctrl.mouse_scroll(evt["dx"], evt["dy"])

            elif msg_type == protocol.MSG_CLIPBOARD:
                text = payload.decode("utf-8", errors="replace")
                set_text(text)

            elif msg_type == protocol.MSG_PING:
                self._send(protocol.MSG_PONG, b"")


# ── Monitor protocol server session ──────────────────────────────────────


class MonitorServerSession(threading.Thread):
    """Server session for the monitor control protocol (input-only, no video).

    Handshake:
      1. Server sends HELLO (version string)
      2. Client responds HELLO_ACK
      3. Server sends RESOLUTION (screen dimensions)
      4. Loop: receive input events → inject via InputController
    """

    def __init__(self, sock: socket.socket, addr):
        super().__init__(daemon=True)
        self._sock = sock
        self._addr = addr
        self._running = False

    def run(self) -> None:
        self._running = True
        print(f"[monitor] Client connected: {self._addr}")

        # Import monitor protocol helpers
        from .monitor_control import (
            HEADER_SIZE, MSG_HELLO, MSG_HELLO_ACK, MSG_RESOLUTION,
            MSG_POINTER_MOVE, MSG_POINTER_BUTTON, MSG_KEY_EVENT,
            MSG_SCROLL, MSG_KEEPALIVE,
            encode_msg, decode_header, encode_hello, decode_hello,
            decode_resolution, decode_pointer_move, decode_pointer_button,
            decode_key_event, decode_scroll,
        )

        try:
            from .input_controller import InputController
            ctrl = InputController()
        except Exception as e:
            print(f"[monitor] InputController unavailable: {e}")
            ctrl = None

        def _recv_exact(n: int) -> bytes | None:
            buf = b""
            while len(buf) < n:
                try:
                    chunk = self._sock.recv(n - len(buf))
                except OSError:
                    return None
                if not chunk:
                    return None
                buf += chunk
            return buf

        try:
            # Get screen dimensions
            width, height = 1920, 1080
            try:
                import mss
                with mss.mss() as sct:
                    m = sct.monitors[1]
                    width = m["width"]
                    height = m["height"]
            except Exception:
                pass

            # 1. Send HELLO
            hello_payload = encode_hello(f"deskctrl-server/{__version__}")
            self._sock.sendall(encode_msg(MSG_HELLO, hello_payload))

            # 2. Receive HELLO_ACK
            data = _recv_exact(HEADER_SIZE)
            if not data:
                print("[monitor] No HELLO_ACK received")
                return
            mt, length = decode_header(data)
            if mt != MSG_HELLO_ACK:
                print(f"[monitor] Expected HELLO_ACK, got {mt}")
                return
            payload = _recv_exact(length) if length else b""
            client_ver = decode_hello(payload) if payload else "unknown"
            print(f"[monitor] Client {self._addr} version: {client_ver}")

            # 3. Send RESOLUTION
            import struct
            res_payload = struct.pack("!II", width, height)
            self._sock.sendall(encode_msg(MSG_RESOLUTION, res_payload))

            # 4. Input receive loop
            if ctrl:
                ctrl.reset()

            while self._running:
                data = _recv_exact(HEADER_SIZE)
                if not data:
                    break
                mt, length = decode_header(data)
                payload = _recv_exact(length) if length else b""

                if mt == MSG_KEEPALIVE:
                    pass  # no response needed for keepalive

                elif mt == MSG_POINTER_MOVE and ctrl:
                    evt = decode_pointer_move(payload)
                    ctrl.mouse_move(evt["x"], evt["y"])

                elif mt == MSG_POINTER_BUTTON and ctrl:
                    evt = decode_pointer_button(payload)
                    if evt["pressed"]:
                        ctrl.mouse_press(evt["x"], evt["y"], evt["button"])
                    else:
                        ctrl.mouse_release(evt["x"], evt["y"], evt["button"])

                elif mt == MSG_KEY_EVENT and ctrl:
                    evt = decode_key_event(payload)
                    if evt["pressed"]:
                        ctrl.key_down(evt["keysym"])
                    else:
                        ctrl.key_up(evt["keysym"])

                elif mt == MSG_SCROLL and ctrl:
                    evt = decode_scroll(payload)
                    ctrl.mouse_scroll(evt["dx"], evt["dy"])

        except Exception as e:
            print(f"[monitor] Session error: {e}")
        finally:
            self._running = False
            try:
                self._sock.close()
            except Exception:
                pass
            print(f"[monitor] Client disconnected: {self._addr}")


def serve(host: str = "0.0.0.0", port: int = DEFAULT_PORT,
          monitor: int = 0, virtual: bool = False,
          monitor_mode: bool = False) -> None:
    """Start the deskctrl TCP server.

    Args:
        monitor_mode: If True, speak the monitor-control protocol (input-only,
                      no video). Used by ``deskctrl monitor`` clients.
    """

    if virtual:
        try:
            from .virtual_display import activate_virtual_display
            activate_virtual_display()
        except Exception as e:
            print(f"[server] Virtual display setup failed: {e}")

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen(5)
    mode_label = "monitor" if monitor_mode else "main"
    print(f"[server] Listening on {host}:{port} "
          f"(mode={mode_label}, monitor_idx={monitor}, virtual={virtual})")

    try:
        while True:
            sock, addr = server_sock.accept()
            if monitor_mode:
                session = MonitorServerSession(sock, addr)
            else:
                session = ServerSession(sock, addr, monitor=monitor, virtual=virtual)
            session.start()
    except KeyboardInterrupt:
        print("\n[server] Shutting down.")
    finally:
        server_sock.close()


# ── v0.2.7 compatibility wrapper for PyQt6 GUI ──────────────────────────


class DeskctrlServer:
    """Compatibility wrapper running the server accept loop in a background thread.

    Provides v0.2.7 DeskctrlServer API: start(), stop(), and callbacks.
    Note: has its own accept loop (does not use serve()) so stop() works cleanly.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT,
                 fps: int = TARGET_FPS, quality: int = JPEG_QUALITY,
                 monitor: int = 0, no_display: bool = False):
        self._host = host
        self._port = port
        self._monitor = monitor
        self._fps = fps
        self._quality = quality
        self._no_display = no_display
        self._thread: threading.Thread | None = None
        self._running = False
        self._server_sock: socket.socket | None = None

        # Callbacks
        self.on_status: Callable[[str], None] | None = None
        self.on_client_connected: Callable[[tuple], None] | None = None
        self.on_client_disconnected: Callable[[tuple], None] | None = None

    def start(self) -> bool:
        """Start server in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if self.on_status:
            self.on_status(f"Server started on {self._host}:{self._port}")
        return True

    def stop(self):
        """Stop the server: close server socket and join thread."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        if self.on_status:
            self.on_status("Server stopped")

    def _run(self):
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self._host, self._port))
            self._server_sock.listen(5)
            self._server_sock.settimeout(1.0)  # periodic timeout to check _running

            while self._running:
                try:
                    sock, addr = self._server_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                # Wrap ServerSession
                session = ServerSession(sock, addr, monitor=self._monitor,
                                        virtual=False)
                if self.on_client_connected:
                    self.on_client_connected(addr)
                session.start()

            print(f"[server] DeskctrlServer accept loop ended ({self._host}:{self._port})")
        except Exception as e:
            if self.on_status:
                self.on_status(f"Server error: {e}")
        finally:
            if self._server_sock:
                try:
                    self._server_sock.close()
                except Exception:
                    pass
