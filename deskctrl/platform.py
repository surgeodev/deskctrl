"""Platform detection and helpers."""

import sys
import platform
import subprocess
import shutil
from typing import Optional


IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def system_name() -> str:
    """Return human-readable OS name."""
    return platform.system()


def has_command(cmd: str) -> bool:
    """Check if a command is available on PATH."""
    return shutil.which(cmd) is not None


def ffmpeg_available() -> bool:
    """Check if ffmpeg is installed."""
    return has_command("ffmpeg")


def ffprobe_available() -> bool:
    """Check if ffprobe is installed."""
    return has_command("ffprobe")


def default_audio_device() -> Optional[str]:
    """Return default audio device name hint, if available."""
    if IS_LINUX:
        try:
            result = subprocess.run(
                ["pactl", "info"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if "Default Sink" in line:
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return None


def hdmi_devices() -> list[dict]:
    """List available HDMI capture devices (V4L2 on Linux, DShow on Windows)."""
    devices = []
    if IS_LINUX:
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--list-devices"],
                capture_output=True, text=True, timeout=5
            )
            # Parse v4l2-ctl output
            current_name = None
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    current_name = None
                    continue
                if not line.startswith("/dev/"):
                    current_name = line.rstrip(":")
                elif current_name:
                    devices.append({
                        "name": current_name,
                        "path": line,
                        "type": "v4l2",
                    })
        except FileNotFoundError:
            # Check /dev/video* directly
            import glob as _glob
            for path in _glob.glob("/dev/video*"):
                devices.append({
                    "name": f"Video device {path}",
                    "path": path,
                    "type": "v4l2",
                })
        except Exception:
            pass
    elif IS_WINDOWS:
        try:
            result = subprocess.run(
                ["ffmpeg", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True, text=True, timeout=10
            )
            import re
            for line in (result.stderr or "").splitlines():
                if '"' in line and ('video' in line.lower() or 'capture' in line.lower()):
                    m = re.search(r'"([^"]+)"', line)
                    if m:
                        devices.append({
                            "name": m.group(1),
                            "path": m.group(1),
                            "type": "dshow",
                        })
        except Exception:
            pass
    elif IS_MACOS:
        try:
            result = subprocess.run(
                ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                capture_output=True, text=True, timeout=10
            )
            import re
            for line in result.stderr.splitlines():
                if "capture" in line.lower():
                    m = re.search(r'\[(\d+)\]\s+(.+)', line)
                    if m:
                        devices.append({
                            "name": m.group(2).strip(),
                            "path": m.group(1),
                            "type": "avfoundation",
                        })
        except Exception:
            pass
    return devices
