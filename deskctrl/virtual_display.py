"""Virtual display driver management for extended monitor mode.

On Windows, creates a virtual monitor so the OS sees an additional display.
The deskctrl server can then capture this virtual monitor (--monitor 2)
and stream it to the extended display client.

Uses usbmmidd_v2 (https://github.com/nomi-san/usbmmidd_v2) under the hood.
"""

import os
import sys
import json
import shutil
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError

from .platform import IS_WINDOWS

log = logging.getLogger(__name__)

USBMIMM_URL = (
    "https://github.com/nomi-san/usbmmidd_v2/releases/latest/download/usbmmidd.zip"
)

# ---- helpers ------------------------------------------------------------------

def _ensure_windows():
    if not IS_WINDOWS:
        raise RuntimeError("Virtual display driver is Windows-only")


def _driver_dir() -> Path:
    """Return path to local driver cache."""
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    d = base / "deskctrl" / "usbmmidd"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- install -----------------------------------------------------------------

def is_installed() -> bool:
    """Check if usbmmidd driver is installed (the .inf is present)."""
    if not IS_WINDOWS:
        return False
    dd = _driver_dir()
    return (dd / "usbmmidd.inf").exists()


def download(force: bool = False) -> Optional[str]:
    """Download usbmmidd_v2 zip and extract to driver dir.

    Returns path to extracted directory, or None on failure.
    """
    if is_installed() and not force:
        return str(_driver_dir())

    dd = _driver_dir()
    zip_path = dd / "usbmmidd.zip"

    log.info(f"Downloading usbmmidd from {USBMIMM_URL}...")
    try:
        resp = urlopen(USBMIMM_URL, timeout=30)
        with open(zip_path, "wb") as f:
            f.write(resp.read())
    except URLError as e:
        log.error(f"Download failed: {e}")
        return None

    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dd)

    zip_path.unlink(missing_ok=True)
    return str(dd)


def install() -> bool:
    """Download and install the virtual display driver.

    Returns True on success.
    """
    _ensure_windows()
    dd = download()
    if not dd:
        return False

    log.info("Installing usbmmidd driver (admin required)...")
    bat = Path(dd) / "usbmmidd.bat"
    if not bat.exists():
        log.error(f"usbmmidd.bat not found in {dd}")
        return False

    try:
        subprocess.run(
            ["pnputil", "/add-driver", str(Path(dd) / "usbmmidd.inf"), "/install"],
            check=True, capture_output=True, timeout=30,
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Driver install failed: {e}")
        return False
    except FileNotFoundError:
        log.info("pnputil not found, you may need to install the driver manually.")

    log.info("Virtual display driver installed.")
    return True


def uninstall() -> bool:
    """Remove the virtual display driver."""
    _ensure_windows()
    dd = _driver_dir()
    inf = dd / "usbmmidd.inf"
    if not inf.exists():
        log.info("Driver not installed.")
        return True

    try:
        subprocess.run(
            ["pnputil", "/delete-driver", str(inf), "/uninstall", "/force"],
            check=True, capture_output=True, timeout=30,
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Driver uninstall failed: {e}")
        return False
    except FileNotFoundError:
        log.warning("pnputil not found. Remove usbmmidd.inf manually.")

    shutil.rmtree(dd, ignore_errors=True)
    log.info("Virtual display driver removed.")
    return True


# ---- monitor management -------------------------------------------------------

def add_monitor() -> bool:
    """Add a virtual monitor (requires driver installed).

    The monitor appears in Windows Display Settings as an additional display.
    You can then extend your desktop to it.
    """
    _ensure_windows()
    if not is_installed():
        log.error("Driver not installed. Run 'deskctrl driver install' first.")
        return False

    dd = _driver_dir()
    bat = dd / "usbmmidd.bat"
    if not bat.exists():
        log.error(f"usbmmidd.bat not found in {dd}")
        return False

    log.info("Adding virtual monitor...")
    try:
        subprocess.run(
            [str(bat), "add"],
            check=True, capture_output=True, timeout=15,
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to add monitor: {e}")
        return False

    log.info("Virtual monitor added. Configure it in Windows Display Settings.")
    return True


def remove_monitor() -> bool:
    """Remove all virtual monitors."""
    _ensure_windows()
    dd = _driver_dir()
    bat = dd / "usbmmidd.bat"
    if not bat.exists():
        log.info("No driver found to remove monitors.")
        return True

    log.info("Removing virtual monitors...")
    try:
        subprocess.run(
            [str(bat), "remove"],
            check=True, capture_output=True, timeout=15,
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to remove monitor: {e}")
        return False

    log.info("Virtual monitors removed.")
    return True


def get_monitor_count() -> int:
    """Return number of detected monitors (Windows only)."""
    if not IS_WINDOWS:
        return 0
    try:
        import mss
        with mss.mss() as sct:
            return len(sct.monitors) - 1  # monitors[0] is the combined virtual screen
    except Exception:
        return 0


# ---- CLI helpers ---------------------------------------------------------------

def install_and_add() -> bool:
    """Full setup: install driver + add virtual monitor."""
    if not install():
        return False
    return add_monitor()


def status_text() -> str:
    """Return human-readable virtual display status."""
    if not IS_WINDOWS:
        return "Not supported on this platform"
    lines = []
    lines.append(f"Driver installed: {is_installed()}")
    try:
        n = get_monitor_count()
        lines.append(f"Monitors detected: {n}")
        if n >= 2:
            lines.append("Use: deskctrl serve --monitor 2")
    except Exception:
        lines.append("Monitors detected: unknown")
    return "\n".join(lines)
