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
    "https://www.amyuni.com/downloads/usbmmidd_v2.zip"
)

# ---- helpers ------------------------------------------------------------------

def _ensure_windows():
    if not IS_WINDOWS:
        raise RuntimeError("Virtual display driver is Windows-only")


def _is_admin() -> bool:
    """Return True if running with admin privileges on Windows."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _find_in_tree(dirpath: Path, filename: str) -> Optional[Path]:
    """Search for *filename* anywhere under *dirpath* (first match)."""
    for p in dirpath.rglob(filename):
        return p
    return None


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
    return _find_in_tree(dd, "usbmmidd.inf") is not None


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

    if not _is_admin():
        log.error("Administrator privileges required. Run the terminal as Administrator.")
        return False

    dd = download()
    if not dd:
        return False

    dd_path = Path(dd)

    log.info("Installing usbmmidd driver (admin required)...")

    # Search recursively for installer tools in extracted tree
    dev_installer = _find_in_tree(dd_path, "deviceinstaller64.exe")
    bat = _find_in_tree(dd_path, "usbmmidd.bat")
    inf = _find_in_tree(dd_path, "usbmmidd.inf")

    if dev_installer and inf:
        try:
            subprocess.run(
                [str(dev_installer), "install", str(inf), "usbmmidd"],
                check=True, capture_output=True, timeout=30,
            )
        except subprocess.CalledProcessError as e:
            log.error(f"Driver install via deviceinstaller64 failed: {e}")
            return False
    elif bat and inf:
        try:
            subprocess.run(
                [str(bat), "install"],
                check=True, capture_output=True, timeout=30,
            )
        except subprocess.CalledProcessError as e:
            log.error(f"Driver install via bat failed: {e}")
            return False
    elif inf:
        try:
            subprocess.run(
                ["pnputil", "/add-driver", str(inf), "/install"],
                check=True, capture_output=True, timeout=30,
            )
        except subprocess.CalledProcessError as e:
            log.error(f"Driver install via pnputil failed (run as Administrator): {e}")
            return False
        except FileNotFoundError:
            log.info("pnputil not found. Install driver manually:\n"
                      "  deviceinstaller64 install <path>\\usbmmidd.inf usbmmidd")
            return False
    else:
        log.error("No usbmmidd.inf found in downloaded package.\n"
                  "Download manually from https://www.amyuni.com/downloads/usbmmidd_v2.zip\n"
                  "and run: deviceinstaller64 install usbmmidd.inf usbmmidd")
        return False

    log.info("Virtual display driver installed.")
    return True


def uninstall() -> bool:
    """Remove the virtual display driver."""
    _ensure_windows()

    if not _is_admin():
        log.error("Administrator privileges required. Run the terminal as Administrator.")
        return False

    dd = _driver_dir()
    inf = _find_in_tree(dd, "usbmmidd.inf")
    if not inf:
        log.info("Driver not installed.")
        return True

    dev_installer = _find_in_tree(dd, "deviceinstaller64.exe")
    if dev_installer:
        try:
            subprocess.run(
                [str(dev_installer), "stop", "usbmmidd"],
                check=True, capture_output=True, timeout=15,
            )
            subprocess.run(
                [str(dev_installer), "remove", "usbmmidd"],
                check=True, capture_output=True, timeout=15,
            )
        except subprocess.CalledProcessError as e:
            log.error(f"Driver uninstall via deviceinstaller64 failed: {e}")
            return False
    else:
        try:
            subprocess.run(
                ["pnputil", "/delete-driver", str(inf), "/uninstall", "/force"],
                check=True, capture_output=True, timeout=30,
            )
        except subprocess.CalledProcessError as e:
            log.error(f"Driver uninstall via pnputil failed: {e}")
            return False
        except FileNotFoundError:
            log.warning("pnputil not found. Remove usbmmidd.inf manually.")

    shutil.rmtree(dd, ignore_errors=True)
    log.info("Virtual display driver removed.")
    return True


# ---- monitor management -------------------------------------------------------

def _get_driver_dir() -> Optional[Path]:
    """Return driver directory if installed."""
    dd = _driver_dir()
    if not dd.exists():
        return None
    if _find_in_tree(dd, "usbmmidd.inf") is None:
        return None
    return dd


def add_monitor(count: int = 1) -> bool:
    """Add virtual monitor(s) (requires driver installed).

    The monitor appears in Windows Display Settings as an additional display.
    You can then extend your desktop to it.
    """
    _ensure_windows()

    if not _is_admin():
        log.error("Administrator privileges required. Run the terminal as Administrator.")
        return False

    dd = _get_driver_dir()
    inf = _find_in_tree(dd, "usbmmidd.inf") if dd else None
    if not inf:
        log.error("Driver not installed. Run 'deskctrl driver install' first.")
        return False

    dev_installer = _find_in_tree(dd, "deviceinstaller64.exe")
    bat = _find_in_tree(dd, "usbmmidd.bat")

    for i in range(count):
        if dev_installer:
            log.info(f"Adding virtual monitor {i+1}/{count}...")
            try:
                subprocess.run(
                    [str(dev_installer), "enableidd", "1"],
                    check=True, capture_output=True, timeout=15,
                )
            except subprocess.CalledProcessError as e:
                log.error(f"Failed to add monitor: {e}")
                return False
        elif bat:
            log.info(f"Adding virtual monitor {i+1}/{count}...")
            try:
                subprocess.run(
                    [str(bat), "add"],
                    check=True, capture_output=True, timeout=15,
                )
            except subprocess.CalledProcessError as e:
                log.error(f"Failed to add monitor: {e}")
                return False
        else:
            log.error("No deviceinstaller64.exe or usbmmidd.bat found")
            return False

    log.info(f"Virtual monitor(s) added ({count}). Configure in Windows Display Settings.")
    return True


def remove_monitor() -> bool:
    """Remove all virtual monitors."""
    _ensure_windows()

    if not _is_admin():
        log.error("Administrator privileges required. Run the terminal as Administrator.")
        return False

    dd = _get_driver_dir()
    if not dd:
        log.info("No driver found to remove monitors.")
        return True

    dev_installer = _find_in_tree(dd, "deviceinstaller64.exe")
    bat = _find_in_tree(dd, "usbmmidd.bat")

    if dev_installer:
        log.info("Removing virtual monitors...")
        try:
            subprocess.run(
                [str(dev_installer), "enableidd", "0"],
                check=True, capture_output=True, timeout=15,
            )
        except subprocess.CalledProcessError as e:
            log.error(f"Failed to remove monitor: {e}")
            return False
    elif bat:
        log.info("Removing virtual monitors...")
        try:
            subprocess.run(
                [str(bat), "remove"],
                check=True, capture_output=True, timeout=15,
            )
        except subprocess.CalledProcessError as e:
            log.error(f"Failed to remove monitor: {e}")
            return False
    else:
        log.info("No deviceinstaller64 or bat found to remove monitors.")

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
