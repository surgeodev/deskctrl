"""deskctrl GUI -- PyQt6-based graphical interface for remote desktop control.

Provides:
- Connection dialog (manual IP/port + LAN discovery browser)
- Video viewer with zoom/fit
- Settings panel (quality, FPS, monitor)
- HDMI mode toggle
- Headless mode option
"""

import logging
import sys
import threading
import time
from typing import Optional

import numpy as np

from . import __version__, __appname__
from .client import DeskctrlClient, DISPLAY_QT
from .server import DeskctrlServer

log = logging.getLogger(__name__)

# Will be set to True if PyQt6 imports successfully
_HAS_QT = False

try:
    from PyQt6.QtCore import (
        Qt, QTimer, QThread, pyqtSignal, QObject,
        QRect, QSize, QPoint,
    )
    from PyQt6.QtGui import (
        QPixmap, QImage, QPainter, QKeyEvent,
        QMouseEvent, QWheelEvent, QCloseEvent,
        QAction, QIcon, QFont,
        QShortcut, QKeySequence,
    )
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QLineEdit, QSpinBox, QSlider,
        QComboBox, QCheckBox, QGroupBox, QFormLayout,
        QStatusBar, QMenuBar, QMenu, QMessageBox,
        QDialog, QDialogButtonBox, QTabWidget, QListWidget,
        QListWidgetItem, QSplitter, QFrame, QToolTip,
        QScrollArea, QSizePolicy,
    )
    _HAS_QT = True
except ImportError:
    log.debug("PyQt6 not available -- GUI mode disabled")


if _HAS_QT:
    # ???????????????????????????????????????????????????????????????????????????
    # Video Display Widget
    # ???????????????????????????????????????????????????????????????????????????

    class VideoWidget(QWidget):
        """Widget that renders video frames via QPainter."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._frame: Optional[QPixmap] = None
            self._raw_frame: Optional[np.ndarray] = None
            self._fit_mode = True  # Fit to widget size
            self._zoom = 1.0
            self._pan_x = 0
            self._pan_y = 0
            self._dragging = False
            self._drag_start = None
            self.setMinimumSize(320, 240)
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
            # Use system theme background
            self.setMouseTracking(True)

        def set_frame(self, frame_bgr: np.ndarray):
            """Update the displayed frame."""
            self._raw_frame = frame_bgr
            h, w, ch = frame_bgr.shape
            bytes_per_line = ch * w
            qimg = QImage(frame_bgr.data, w, h, bytes_per_line,
                          QImage.Format.Format_BGR888)
            self._frame = QPixmap.fromImage(qimg)
            self.update()

    @property
    def has_frame(self) -> bool:
        return self._frame is not None

    def toggle_fit(self):
        """Toggle between fit-to-window and 1:1 zoom."""
        self._fit_mode = not self._fit_mode
        if self._fit_mode:
            self._zoom = 1.0
            self._pan_x = 0
            self._pan_y = 0
        self.update()

    def zoom_in(self):
        self._zoom = min(4.0, self._zoom * 1.25)
        self._fit_mode = False
        self.update()

    def zoom_out(self):
        self._zoom = max(0.25, self._zoom * 0.8)
        self._fit_mode = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Fill background
        painter.fillRect(self.rect(), self.palette().window())

        if self._frame is None:
            # Draw placeholder
            painter.setPen(self.palette().windowText().color())
            font = painter.font()
            font.setPointSize(14)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Waiting for connection...")
            return

        if self._fit_mode:
            scaled = self._frame.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            # Manual zoom + pan
            scaled = self._frame.scaled(
                self._frame.size() * self._zoom,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(self._pan_x, self._pan_y, scaled)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._dragging = True
            self._drag_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_start:
            dx = event.pos().x() - self._drag_start.x()
            dy = event.pos().y() - self._drag_start.y()
            self._pan_x += dx
            self._pan_y += dy
            self._drag_start = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._dragging = False
            self._drag_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def sizeHint(self):
        return QSize(1024, 768)


if _HAS_QT:

    # ???????????????????????????????????????????????????????????????????????????
    # Connection Dialog
    # ???????????????????????????????????????????????????????????????????????????

    class ConnectionDialog(QDialog):
        """Dialog for connecting to a deskctrl server."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle(f"Connect to {__appname__} Server")
            self.setMinimumWidth(450)
            self._result = None
            self._browser_thread = None
            self._discovered = []
            self._build_ui()

        def _build_ui(self):
            layout = QVBoxLayout(self)
            manual_group = QGroupBox("Manual Connection")
            manual_layout = QFormLayout(manual_group)
            ip_layout = QHBoxLayout()
            self.host_input = QLineEdit("127.0.0.1")
            self.host_input.setPlaceholderText("Server IP or hostname")
            self.port_input = QSpinBox()
            self.port_input.setRange(1024, 65535)
            self.port_input.setValue(5830)
            ip_layout.addWidget(self.host_input, 1)
            ip_layout.addWidget(self.port_input)
            manual_layout.addRow("Host:Port", ip_layout)
            self.password_input = QLineEdit()
            self.password_input.setPlaceholderText("(optional)")
            self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
            manual_layout.addRow("Password", self.password_input)
            layout.addWidget(manual_group)

            discover_group = QGroupBox("LAN Discovery")
            discover_layout = QVBoxLayout(discover_group)
            self.discover_btn = QPushButton("Scan LAN")
            self.discover_btn.clicked.connect(self._scan_lan)
            discover_layout.addWidget(self.discover_btn)
            self.server_list = QListWidget()
            self.server_list.setMinimumHeight(100)
            self.server_list.itemDoubleClicked.connect(self._on_item_double_click)
            discover_layout.addWidget(self.server_list)
            layout.addWidget(discover_group)

            btn_layout = QHBoxLayout()
            self.connect_btn = QPushButton("Connect")
            self.connect_btn.setDefault(True)
            self.connect_btn.clicked.connect(self._on_connect)
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.clicked.connect(self.reject)
            btn_layout.addStretch()
            btn_layout.addWidget(self.cancel_btn)
            btn_layout.addWidget(self.connect_btn)
            layout.addLayout(btn_layout)

        def _scan_lan(self):
            self.server_list.clear()
            self.discover_btn.setEnabled(False)
            self.discover_btn.setText("Scanning...")
            def scan():
                try:
                    from .discovery import DiscoveryBrowser
                    browser = DiscoveryBrowser()
                    browser.start()
                    time.sleep(2.0)
                    services = browser.get_services()
                    browser.stop()
                    self._discovered = services
                    for svc in services:
                        self.server_list.addItem(QListWidgetItem(f"{svc['name']} - {svc['host']}:{svc['port']}"))
                except Exception:
                    self.server_list.addItem("Discovery not available (install zeroconf)")
                finally:
                    self.discover_btn.setEnabled(True)
                    self.discover_btn.setText("Scan LAN")
            threading.Thread(target=scan, daemon=True).start()

        def _on_item_double_click(self, item):
            row = self.server_list.row(item)
            if 0 <= row < len(self._discovered):
                svc = self._discovered[row]
                self.host_input.setText(svc["host"])
                self.port_input.setValue(svc["port"])

        def _on_connect(self):
            self._result = {"host": self.host_input.text().strip(), "port": self.port_input.value()}
            self.accept()

        def get_connection_info(self):
            return self._result

    # ???????????????????????????????????????????????????????????????????????????
    # Settings Panel
    # ???????????????????????????????????????????????????????????????????????????

    class SettingsPanel(QWidget):
        settings_changed = pyqtSignal(dict)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._build_ui()

        def _build_ui(self):
            layout = QVBoxLayout(self)
            quality_group = QGroupBox("Video")
            ql = QFormLayout(quality_group)
            self.quality_slider = QSlider(Qt.Orientation.Horizontal)
            self.quality_slider.setRange(10, 100)
            self.quality_slider.setValue(80)
            self.quality_slider.valueChanged.connect(self._on_change)
            ql.addRow("JPEG Quality", self.quality_slider)
            self.fps_spin = QSpinBox()
            self.fps_spin.setRange(1, 60)
            self.fps_spin.setValue(30)
            self.fps_spin.valueChanged.connect(self._on_change)
            ql.addRow("Target FPS", self.fps_spin)
            layout.addWidget(quality_group)

            display_group = QGroupBox("Display")
            dl = QFormLayout(display_group)
            self.fit_check = QCheckBox("Fit to window")
            self.fit_check.setChecked(True)
            dl.addRow(self.fit_check)
            self.fullscreen_btn = QPushButton("Toggle Fullscreen (F11)")
            dl.addRow(self.fullscreen_btn)
            layout.addWidget(display_group)

            mode_group = QGroupBox("Mode")
            ml = QFormLayout(mode_group)
            self.hdmi_check = QCheckBox("HDMI Capture Mode")
            self.hdmi_check.setEnabled(False)
            ml.addRow(self.hdmi_check)
            self.headless_check = QCheckBox("Headless (--nowindow, control only)")
            ml.addRow(self.headless_check)
            layout.addWidget(mode_group)

            monitor_group = QGroupBox("Monitor")
            mon_layout = QFormLayout(monitor_group)
            self.monitor_combo = QComboBox()
            self.monitor_combo.addItems(["Monitor 1", "Monitor 2", "Monitor 3"])
            self.monitor_combo.currentIndexChanged.connect(self._on_change)
            mon_layout.addRow("Source", self.monitor_combo)
            layout.addWidget(monitor_group)
            layout.addStretch()

        def _on_change(self):
            self.settings_changed.emit(self.get_settings())

        def get_settings(self):
            return {"quality": self.quality_slider.value(), "fps": self.fps_spin.value(),
                    "monitor": self.monitor_combo.currentIndex() + 1,
                    "fit_window": self.fit_check.isChecked(),
                    "hdmi_mode": self.hdmi_check.isChecked(), "headless": self.headless_check.isChecked()}

    # ???????????????????????????????????????????????????????????????????????????
    # Main Window
    # ???????????????????????????????????????????????????????????????????????????

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"{__appname__} v{__version__}")
            self.setMinimumSize(800, 600)
            self._client = None
            self._server = None
            self._server_mode = False
            self._connected = False
            self._build_ui()
            self._setup_shortcuts()
            self._frame_timer = QTimer(self)
            self._frame_timer.timeout.connect(self._poll_frame)
            self._frame_timer.setInterval(16)

        def _build_ui(self):
            central = QWidget()
            self.setCentralWidget(central)
            layout = QHBoxLayout(central)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            self.video_widget = VideoWidget()
            layout.addWidget(self.video_widget, 1)
            self.settings_panel = SettingsPanel()
            self.settings_panel.setMaximumWidth(280)
            self.settings_panel.settings_changed.connect(self._on_settings_changed)
            self.settings_panel.hdmi_check.toggled.connect(self._on_hdmi_toggle)
            self.settings_panel.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
            self.settings_panel.headless_check.toggled.connect(self._on_headless_toggle)
            layout.addWidget(self.settings_panel)

            self.status_bar = QStatusBar()
            self.setStatusBar(self.status_bar)
            self.status_label = QLabel("Ready")
            self.status_bar.addWidget(self.status_label, 1)
            self.fps_label = QLabel("")
            self.fps_label.setMinimumWidth(120)
            self.fps_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.status_bar.addPermanentWidget(self.fps_label)

            menubar = self.menuBar()
            file_menu = menubar.addMenu("&File")
            self.connect_action = QAction("&Connect...", self)
            self.connect_action.triggered.connect(self.show_connect_dialog)
            file_menu.addAction(self.connect_action)
            self.serve_action = QAction("&Start Server...", self)
            self.serve_action.triggered.connect(self.start_server_dialog)
            file_menu.addAction(self.serve_action)
            file_menu.addSeparator()
            self.disconnect_action = QAction("&Disconnect", self)
            self.disconnect_action.triggered.connect(self.disconnect)
            self.disconnect_action.setEnabled(False)
            file_menu.addAction(self.disconnect_action)
            file_menu.addSeparator()
            self.quit_action = QAction("&Quit", self)
            self.quit_action.triggered.connect(self.close)
            file_menu.addAction(self.quit_action)
            view_menu = menubar.addMenu("&View")
            self.fit_action = QAction("Toggle &Fit", self)
            self.fit_action.setCheckable(True)
            self.fit_action.setChecked(True)
            self.fit_action.triggered.connect(lambda: self.video_widget.toggle_fit())
            view_menu.addAction(self.fit_action)
            self.fullscreen_action = QAction("Toggle &Fullscreen (F11)", self)
            self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
            view_menu.addAction(self.fullscreen_action)
            tools_menu = menubar.addMenu("&Tools")
            self.monitor_action = QAction("Monitor &Control...", self)
            self.monitor_action.triggered.connect(self._show_monitor_control)
            tools_menu.addAction(self.monitor_action)
            help_menu = menubar.addMenu("&Help")
            about_action = QAction(f"&About {__appname__}", self)
            about_action.triggered.connect(self._show_about)
            help_menu.addAction(about_action)

        def _setup_shortcuts(self):
            QShortcut(QKeySequence("F11"), self, self.toggle_fullscreen)
            QShortcut(QKeySequence("Ctrl+Q"), self, self.close)
            QShortcut(QKeySequence("Ctrl+C"), self, self.show_connect_dialog)
            QShortcut(QKeySequence("Ctrl+N"), self, lambda: self.start_server(0, "0.0.0.0", 5830, 30, 80))

        def show_connect_dialog(self):
            dialog = ConnectionDialog(self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                info = dialog.get_connection_info()
                if info:
                    self.connect(info["host"], info["port"])

        def start_server_dialog(self):
            self.start_server(monitor=1, host="0.0.0.0", port=5830, fps=30, quality=80)

        def connect(self, host, port):
            if self._connected:
                self.disconnect()
            self._server_mode = False
            self._client = DeskctrlClient(host=host, port=port, display_mode=DISPLAY_QT)
            self._client.on_status = lambda msg: self._show_status(msg)
            self._client.on_frame = self._on_frame_received
            self._client.on_connected = self._on_client_connected
            self._client.on_disconnected = self._on_client_disconnected
            self._client.on_resolution = self._on_resolution
            self._show_status(f"Connecting to {host}:{port}...")
            if self._client.connect():
                self._frame_timer.start()
            else:
                self._client = None

        def start_server(self, monitor=1, host="0.0.0.0", port=5830, fps=30, quality=80, no_display=False):
            if self._connected:
                self.disconnect()
            self._server_mode = True
            self._server = DeskctrlServer(host=host, port=port, fps=fps, quality=quality, monitor=monitor, no_display=no_display)
            self._server.on_status = lambda msg: self._show_status(msg)
            self._server.on_client_connected = self._on_server_client_connected
            self._server.on_client_disconnected = self._on_server_client_disconnected
            if self._server.start():
                self._show_status(f"Server running on {host}:{port}")
                self.connect("127.0.0.1", port)
                self._server_mode = True
            else:
                self._server = None

        def disconnect(self):
            if self._client:
                self._client.disconnect()
                self._client = None
            if self._server_mode and self._server:
                self._server.stop()
                self._server = None
            self._connected = False
            self._server_mode = False
            self._frame_timer.stop()
            self.video_widget._frame = None
            self.video_widget.update()
            self.connect_action.setEnabled(True)
            self.serve_action.setEnabled(True)
            self.disconnect_action.setEnabled(False)
            self.fps_label.setText("")
            self._show_status("Disconnected")

        def toggle_fullscreen(self):
            self.showFullScreen() if not self.isFullScreen() else self.showNormal()

        def _on_frame_received(self, frame):
            self.video_widget.set_frame(frame)

        def _on_client_connected(self):
            self._connected = True
            self.connect_action.setEnabled(False)
            self.serve_action.setEnabled(False)
            self.disconnect_action.setEnabled(True)
            self._show_status("Connected")

        def _on_client_disconnected(self):
            self.disconnect()

        def _on_resolution(self, width, height):
            self._show_status(f"Connected - {width}x{height}")

        def _on_server_client_connected(self, addr):
            self._show_status(f"Client connected from {addr[0]}:{addr[1]}")

        def _on_server_client_disconnected(self, addr):
            self._show_status("Client disconnected")

        def _poll_frame(self):
            if self._client and self._client.state.connected:
                fps = self._client.state.fps
                bitrate = self._client.state.bitrate
                self.fps_label.setText(f" {fps:.0f} fps  {self._format_bitrate(bitrate)}")

        def _on_settings_changed(self, settings):
            if self._client and self._client.state.connected:
                self._client.send_settings(quality=settings["quality"], fps=settings["fps"], monitor=settings["monitor"])
            if settings.get("fit_window"):
                self.video_widget._fit_mode = True
                self.video_widget.update()
                self.fit_action.setChecked(True)

        def _on_hdmi_toggle(self, enabled):
            if self._client:
                self._client.toggle_hdmi()

        def _on_headless_toggle(self, enabled):
            self.video_widget.setVisible(not enabled)
            self._show_status("Headless mode - control only" if enabled else "Display shown")

        def _show_status(self, msg):
            self.status_label.setText(msg)

        def _show_about(self):
            QMessageBox.about(self, f"About {__appname__}",
                    f"<h3>{__appname__} v{__version__}</h3>"
                    f"<p>Remote desktop controller for PCs - like scrcpy for desktops.</p>"
                    f"<p>Cross-platform | Network & HDMI | Headless mode | Monitor Control</p>")

        def _show_monitor_control(self):
            dialog = MonitorControlDialog(self)
            dialog.exec()

        def _format_bitrate(self, bits_per_sec):
            if bits_per_sec > 1_000_000:
                return f"{bits_per_sec / 1_000_000:.1f} Mbps"
            elif bits_per_sec > 1_000:
                return f"{bits_per_sec / 1_000:.0f} Kbps"
            return f"{bits_per_sec:.0f} bps"

        def closeEvent(self, event):
            self.disconnect()
            event.accept()

    # ???????????????????????????????????????????????????????????????????????????
    # Monitor Control Panel (Barrier-like)
    # ???????????????????????????????????????????????????????????????????????????

    class MonitorControlDialog(QDialog):
        """Dialog for configuring and running Monitor Control mode."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle(f"{__appname__} -- Monitor Control")
            self.setMinimumSize(500, 400)
            self._engine = None
            self._build_ui()

        def _build_ui(self):
            layout = QVBoxLayout(self)

            # ---- Info label ----------------------------------------------------------------------------------------
            info = QLabel(
                "Configure where your other machines are placed relative to\n"
                "your screen. Move the mouse to the edge -> seamless remote control."
            )
            info.setWordWrap(True)
            info.setStyleSheet("padding: 4px;")
            layout.addWidget(info)

            # ---- Server list -------------------------------------------------------------------------------------
            list_group = QGroupBox("Configured Servers")
            list_layout = QVBoxLayout(list_group)
            self.server_list = QListWidget()
            self.server_list.setMinimumHeight(100)
            list_layout.addWidget(self.server_list)
            btn_row = QHBoxLayout()
            self.remove_btn = QPushButton("Remove Selected")
            self.remove_btn.clicked.connect(self._remove_selected)
            self.remove_btn.setEnabled(False)
            self.server_list.itemSelectionChanged.connect(
                lambda: self.remove_btn.setEnabled(
                    bool(self.server_list.selectedItems())
                )
            )
            btn_row.addStretch()
            btn_row.addWidget(self.remove_btn)
            list_layout.addLayout(btn_row)
            layout.addWidget(list_group)

            # ---- Add server form -----------------------------------------------------------------------------
            add_group = QGroupBox("Add Server")
            add_form = QFormLayout(add_group)
            dir_row = QHBoxLayout()
            self.direction_combo = QComboBox()
            self.direction_combo.addItems(["right", "left", "top", "bottom"])
            dir_row.addWidget(self.direction_combo)
            self.host_input = QLineEdit()
            self.host_input.setPlaceholderText("192.168.1.100")
            dir_row.addWidget(self.host_input, 1)
            self.port_input = QSpinBox()
            self.port_input.setRange(1024, 65535)
            self.port_input.setValue(5830)
            dir_row.addWidget(self.port_input)
            self.add_btn = QPushButton("Add")
            self.add_btn.clicked.connect(self._add_server)
            dir_row.addWidget(self.add_btn)
            add_form.addRow("Direction -> Host:Port", dir_row)

            # Margin slider
            margin_row = QHBoxLayout()
            self.margin_slider = QSlider(Qt.Orientation.Horizontal)
            self.margin_slider.setRange(1, 50)
            self.margin_slider.setValue(5)
            self.margin_label = QLabel("5 px")
            self.margin_slider.valueChanged.connect(
                lambda v: self.margin_label.setText(f"{v} px")
            )
            margin_row.addWidget(QLabel("Activation margin:"))
            margin_row.addWidget(self.margin_slider, 1)
            margin_row.addWidget(self.margin_label)
            add_form.addRow(margin_row)
            layout.addWidget(add_group)

            # ---- Status ------------------------------------------------------------------------------------------------
            self.status_label = QLabel("")
            self.status_label.setWordWrap(True)
            self.status_label.setStyleSheet(
                "padding: 6px; font-family: monospace; font-size: 11px;"
            )
            self.status_label.setMinimumHeight(60)
            layout.addWidget(self.status_label)

            # ---- Buttons ---------------------------------------------------------------------------------------------
            btn_layout = QHBoxLayout()
            self.start_btn = QPushButton("Start Monitor Control")
            self.start_btn.clicked.connect(self._toggle_engine)
            self.stop_btn = QPushButton("Stop")
            self.stop_btn.setEnabled(False)
            self.stop_btn.clicked.connect(self._stop_engine)
            self.close_btn = QPushButton("Close")
            self.close_btn.clicked.connect(self.close)
            btn_layout.addWidget(self.start_btn)
            btn_layout.addWidget(self.stop_btn)
            btn_layout.addStretch()
            btn_layout.addWidget(self.close_btn)
            layout.addLayout(btn_layout)

            # Load existing config
            self._load_config()

        # ---- Config IO ------------------------------------------------------------------------------------------------

        def _load_config(self):
            """Load existing monitor layout from disk."""
            from .monitor_control import MonitorLayout, DEFAULT_CONFIG_FILE
            try:
                if os.path.exists(DEFAULT_CONFIG_FILE):
                    self._layout = MonitorLayout.from_file(DEFAULT_CONFIG_FILE)
                else:
                    self._layout = MonitorLayout()
            except Exception:
                self._layout = MonitorLayout()
            self._refresh_list()
            self.margin_slider.setValue(self._layout.activation_margin)

        def _save_config(self):
            from .monitor_control import DEFAULT_CONFIG_FILE
            self._layout.activation_margin = self.margin_slider.value()
            self._layout.to_file(DEFAULT_CONFIG_FILE)

        def _refresh_list(self):
            self.server_list.clear()
            for s in self._layout.servers:
                self.server_list.addItem(
                    QListWidgetItem(
                        f"{s.direction:>6s}  ->  {s.name}  ({s.host}:{s.port})"
                    )
                )

        # ---- Actions ----------------------------------------------------------------------------------------------------

        def _add_server(self):
            direction = self.direction_combo.currentText()
            host = self.host_input.text().strip()
            port = self.port_input.value()
            if not host:
                return
            self._layout.add_server(direction, host, port)
            self._save_config()
            self._refresh_list()
            self.host_input.clear()
            self._log(f"Added {direction} -> {host}:{port}")

        def _remove_selected(self):
            row = self.server_list.currentRow()
            if row >= 0 and row < len(self._layout.servers):
                s = self._layout.servers[row]
                self._layout.remove_server(s.direction)
                self._save_config()
                self._refresh_list()
                self._log(f"Removed {s.direction} -> {s.name}")

        # ---- Engine -----------------------------------------------------------------------------------------------------

        def _toggle_engine(self):
            if self._engine and self._engine._running:
                self._stop_engine()
            else:
                self._start_engine()

        def _start_engine(self):
            if not self._layout.servers:
                self._log("No servers configured. Add one first.")
                return

            from .monitor_control import MonitorControlEngine

            self._layout.activation_margin = self.margin_slider.value()
            self._save_config()

            self._engine = MonitorControlEngine(self._layout)
            self._engine.on_status = self._log

            if self._engine.start():
                self.start_btn.setEnabled(False)
                self.stop_btn.setEnabled(True)
                self.add_btn.setEnabled(False)
                self.remove_btn.setEnabled(False)
                self.direction_combo.setEnabled(False)
                self.host_input.setEnabled(False)
                self.port_input.setEnabled(False)
                self.margin_slider.setEnabled(False)
                self._log("? Monitor Control ACTIVE -- ESC to release")
                self.setWindowTitle(
                    f"{__appname__} -- Monitor Control (running)"
                )
            else:
                self._engine = None
                self._log("? Failed to start Monitor Control")

        def _stop_engine(self):
            if self._engine:
                self._engine.stop()
                self._engine = None
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.add_btn.setEnabled(True)
            self.direction_combo.setEnabled(True)
            self.host_input.setEnabled(True)
            self.port_input.setEnabled(True)
            self.margin_slider.setEnabled(True)
            self._log("? Monitor Control stopped")
            self.setWindowTitle(f"{__appname__} -- Monitor Control")

        def _log(self, msg):
            current = self.status_label.text()
            lines = (current + "\n" + msg).split("\n")[-8:]
            self.status_label.setText("\n".join(lines))

        def closeEvent(self, event):
            self._stop_engine()
            event.accept()

    # ???????????????????????????????????????????????????????????????????????????
    # Launch helper (Qt available)
    # ???????????????????????????????????????????????????????????????????????????

    def launch_gui():
        app = QApplication(sys.argv)
        app.setApplicationName(__appname__)
        app.setApplicationVersion(__version__)
        # Use system-native theme (no custom palette)
        window = MainWindow()
        window.show()
        return app.exec()

else:
    def launch_gui():
        print("PyQt6 is required for GUI mode.")
        print()
        print("  Installation automatique :")
        print("    bash scripts/install.sh")
        print()
        print("  Ou manuellement :")
        print("    pip install PyQt6")
        print("    # ou: pip install 'deskctrl[gui]'")
        print()
        print("  Ou utiliser le mode CLI sans GUI :")
        print("    deskctrl connect <ip>")
        print("    deskctrl headless <ip>")
        return 1
