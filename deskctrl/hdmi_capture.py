"""HDMI capture card support -- detect and capture from HDMI-to-USB devices."""

import logging
from typing import Optional

from .platform import IS_WINDOWS, IS_LINUX, IS_MACOS, hdmi_devices

# Lazy imports: cv2 (opencv-python-headless) is an optional dependency.
# Functions that need cv2 import it locally.
_cv2_available = False
_np_available = False
try:
    import cv2 as _cv2
    _cv2_available = True
except ImportError:
    pass
try:
    import numpy as _np
    _np_available = True
except ImportError:
    pass

log = logging.getLogger(__name__)


def list_capture_devices() -> list[dict]:
    """List available HDMI/USB capture devices."""
    devices = hdmi_devices()
    if not devices and _cv2_available:
        # Fallback: try OpenCV enumeration
        for i in range(10):
            try:
                cap = _cv2.VideoCapture(i)
                if cap.isOpened():
                    name = f"Device {i}"
                    ret, frame = cap.read()
                    if ret:
                        h, w = frame.shape[:2]
                        devices.append({
                            "name": name,
                            "path": str(i),
                            "type": "opencv",
                            "width": w,
                            "height": h,
                        })
                    cap.release()
            except Exception:
                continue
    return devices


class HDMIFrameSource:
    """
    Frame source that reads from an HDMI capture card.

    On Linux: uses V4L2 (device path like /dev/video0)
    On Windows: uses DirectShow (device index)
    On macOS: uses AVFoundation (device index as string)
    """

    def __init__(self, device_path: Optional[str] = None,
                 width: int = 1920, height: int = 1080, fps: int = 30):
        self.device_path = device_path
        self.target_width = width
        self.target_height = height
        self.target_fps = fps
        self._cap: Optional[object] = None
        self._actual_width = 0
        self._actual_height = 0
        self._actual_fps = 0.0

    @property
    def width(self) -> int:
        return self._actual_width

    @property
    def height(self) -> int:
        return self._actual_height

    def open(self) -> bool:
        """Open the capture device."""
        if not _cv2_available:
            log.error("opencv-python-headless not installed")
            return False

        cv2 = _cv2
        path = self.device_path
        try:
            if IS_LINUX:
                if not path:
                    path = "/dev/video0"
                self._cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
                # Set MJPEG format for better performance
                self._cap.set(cv2.CAP_PROP_FOURCC,
                              cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
            elif IS_WINDOWS:
                if not path:
                    path = "0"
                idx = int(path) if path.isdigit() else 0
                self._cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            elif IS_MACOS:
                if not path:
                    path = "0"
                self._cap = cv2.VideoCapture(int(path) if path.isdigit() else 0,
                                              cv2.CAP_AVFOUNDATION)
            else:
                if not path:
                    path = "0"
                self._cap = cv2.VideoCapture(int(path) if path.isdigit() else 0)

            if not self._cap or not self._cap.isOpened():
                log.error(f"Failed to open capture device: {path}")
                return False

            # Set properties
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.target_width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)
            self._cap.set(cv2.CAP_PROP_FPS, self.target_fps)

            # Read actual values
            self._actual_width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self._actual_height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._actual_fps = self._cap.get(cv2.CAP_PROP_FPS)

            log.info(f"HDMI capture opened: {path} -> "
                     f"{self._actual_width}x{self._actual_height} @ {self._actual_fps}fps")
            return True

        except Exception as e:
            log.error(f"Error opening capture device: {e}")
            return False

    def read(self) -> Optional[object]:
        """Read a frame from the capture device. Returns BGR numpy array."""
        if not self._cap:
            return None
        ret, frame = self._cap.read()
        if not ret:
            return None
        return frame

    def release(self):
        """Release the capture device."""
        if self._cap:
            self._cap.release()
            self._cap = None


def probe_device(device_path: str) -> dict:
    """
    Probe a capture device and return its capabilities.

    Returns dict with: path, name, width, height, fps, available
    """
    info = {"path": device_path, "available": False}

    source = HDMIFrameSource(device_path)
    if source.open():
        info["available"] = True
        info["width"] = source.width
        info["height"] = source.height
        # Try to get a test frame
        frame = source.read()
        if frame is not None:
            info["frame_received"] = True
            info["frame_shape"] = frame.shape
        source.release()

    return info
