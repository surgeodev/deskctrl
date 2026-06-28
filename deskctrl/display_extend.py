"""
Extended / side-panel display using PyQt6.

Used by `deskctrl extend`. Shows a fixed-size panel (no input grab, no
fullscreen). The user's local desktop remains usable.

Receives JPEG/PNG frames from frame_queue and renders them in the panel.
Input events are forwarded to the server with coordinates scaled to the
remote monitor's resolution.
"""

from __future__ import annotations
import io
import queue
import sys

try:
    from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow
    from PyQt6.QtGui import QImage, QPixmap, QKeyEvent, QMouseEvent, QWheelEvent
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
    _PYQT6_AVAILABLE = True
except ImportError:
    _PYQT6_AVAILABLE = False

from .keymap import PYNPUT_SPECIAL_KEYSYMS, XK_Alt_L, XK_Alt_R, XK_F4
from . import protocol


def _keysym_from_qt(key: int, text: str) -> int:
    """Convert Qt key code to X11 keysym."""
    from PyQt6.QtCore import Qt as _Qt
    _QT_SPECIAL = {
        _Qt.Key.Key_Backspace:   0xFF08,
        _Qt.Key.Key_Tab:         0xFF09,
        _Qt.Key.Key_Return:      0xFF0D,
        _Qt.Key.Key_Escape:      0xFF1B,
        _Qt.Key.Key_Delete:      0xFFFF,
        _Qt.Key.Key_Home:        0xFF50,
        _Qt.Key.Key_Left:        0xFF51,
        _Qt.Key.Key_Up:          0xFF52,
        _Qt.Key.Key_Right:       0xFF53,
        _Qt.Key.Key_Down:        0xFF54,
        _Qt.Key.Key_PageUp:      0xFF55,
        _Qt.Key.Key_PageDown:    0xFF56,
        _Qt.Key.Key_End:         0xFF57,
        _Qt.Key.Key_Insert:      0xFF63,
        _Qt.Key.Key_F1:          0xFFBE,
        _Qt.Key.Key_F2:          0xFFBF,
        _Qt.Key.Key_F3:          0xFFC0,
        _Qt.Key.Key_F4:          0xFFC1,
        _Qt.Key.Key_F5:          0xFFC2,
        _Qt.Key.Key_F6:          0xFFC3,
        _Qt.Key.Key_F7:          0xFFC4,
        _Qt.Key.Key_F8:          0xFFC5,
        _Qt.Key.Key_F9:          0xFFC6,
        _Qt.Key.Key_F10:         0xFFC7,
        _Qt.Key.Key_F11:         0xFFC8,
        _Qt.Key.Key_F12:         0xFFC9,
        _Qt.Key.Key_Shift:       0xFFE1,
        _Qt.Key.Key_Control:     0xFFE3,
        _Qt.Key.Key_Alt:         0xFFE9,
        _Qt.Key.Key_Meta:        0xFFEB,
        _Qt.Key.Key_CapsLock:    0xFFE5,
        _Qt.Key.Key_NumLock:     0xFFEF,
        _Qt.Key.Key_ScrollLock:  0xFF14,
        _Qt.Key.Key_Pause:       0xFF13,
        _Qt.Key.Key_Print:       0xFF61,
        _Qt.Key.Key_Menu:        0xFF67,
        _Qt.Key.Key_Space:       0x0020,
    }
    if key in _QT_SPECIAL:
        return _QT_SPECIAL[key]
    if text and len(text) == 1:
        return ord(text)
    return key


class _ExtendWindow(QMainWindow):
    def __init__(self, frame_queue: "queue.Queue[bytes]",
                 remote_w: int, remote_h: int,
                 panel_w: int, panel_h: int,
                 send_input_fn):
        super().__init__()
        self._frame_queue = frame_queue
        self._remote_w = remote_w
        self._remote_h = remote_h
        self._send = send_input_fn
        self._alt_down = False

        self.setWindowTitle("deskctrl — Extended Display")
        self.setFixedSize(panel_w, panel_h)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint
        )

        self._label = QLabel(self)
        self._label.setGeometry(0, 0, panel_w, panel_h)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_frame)
        self._timer.start(16)  # ~60 fps

    def _update_frame(self):
        try:
            while True:
                raw = self._frame_queue.get_nowait()
                qimg = QImage.fromData(raw)
                if not qimg.isNull():
                    pix = QPixmap.fromImage(qimg).scaled(
                        self._label.width(), self._label.height(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self._label.setPixmap(pix)
        except queue.Empty:
            pass

    def _scale_pos(self, pos) -> tuple[int, int]:
        rx = int(pos.x() * self._remote_w / self.width())
        ry = int(pos.y() * self._remote_h / self.height())
        return rx, ry

    def keyPressEvent(self, event: QKeyEvent):
        keysym = _keysym_from_qt(event.key(), event.text())
        if keysym in (XK_Alt_L, XK_Alt_R):
            self._alt_down = True
        if self._alt_down and keysym == XK_F4:
            self.close()
            return
        self._send(protocol.MSG_INPUT_KEY, protocol.encode_key(True, keysym))

    def keyReleaseEvent(self, event: QKeyEvent):
        keysym = _keysym_from_qt(event.key(), event.text())
        if keysym in (XK_Alt_L, XK_Alt_R):
            self._alt_down = False
        self._send(protocol.MSG_INPUT_KEY, protocol.encode_key(False, keysym))

    def mouseMoveEvent(self, event: QMouseEvent):
        x, y = self._scale_pos(event.position().toPoint())
        self._send(protocol.MSG_INPUT_MOUSE, protocol.encode_mouse_move(x, y))

    def mousePressEvent(self, event: QMouseEvent):
        x, y = self._scale_pos(event.position().toPoint())
        btn = {
            Qt.MouseButton.LeftButton: 1,
            Qt.MouseButton.MiddleButton: 2,
            Qt.MouseButton.RightButton: 3,
        }.get(event.button(), 1)
        self._send(protocol.MSG_INPUT_MOUSE,
                   protocol.encode_mouse_button(protocol.MOUSE_PRESS, x, y, btn))

    def mouseReleaseEvent(self, event: QMouseEvent):
        x, y = self._scale_pos(event.position().toPoint())
        btn = {
            Qt.MouseButton.LeftButton: 1,
            Qt.MouseButton.MiddleButton: 2,
            Qt.MouseButton.RightButton: 3,
        }.get(event.button(), 1)
        self._send(protocol.MSG_INPUT_MOUSE,
                   protocol.encode_mouse_button(protocol.MOUSE_RELEASE, x, y, btn))

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta()
        dx = delta.x() // 120
        dy = delta.y() // 120
        self._send(protocol.MSG_INPUT_MOUSE, protocol.encode_mouse_scroll(dx, dy))


def run(frame_queue: "queue.Queue[bytes]",
        remote_w: int, remote_h: int,
        panel_w: int = 480, panel_h: int = 270,
        send_input_fn=None) -> None:
    """
    Launch the PyQt6 side-panel window.

    Args:
        frame_queue:   Queue of raw JPEG/PNG bytes from the receive loop.
        remote_w/h:    Remote desktop resolution (for coordinate scaling).
        panel_w/h:     Side panel pixel size.
        send_input_fn: Callable(msg_type, payload).
    """
    if not _PYQT6_AVAILABLE:
        raise RuntimeError("PyQt6 is required for extend/side-panel display mode.")

    if send_input_fn is None:
        def send_input_fn(mt, p): pass

    app = QApplication.instance() or QApplication(sys.argv)
    win = _ExtendWindow(frame_queue, remote_w, remote_h, panel_w, panel_h, send_input_fn)
    win.show()
    app.exec()
