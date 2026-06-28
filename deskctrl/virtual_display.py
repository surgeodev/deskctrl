"""
Windows virtual display driver management via usbmmidd_v2.

The zip is expected at: <package_dir>/drivers/usbmmidd_v2.zip
On install, it is extracted and deviceinstaller64.exe is located recursively.

Do NOT pre-check admin rights — just run the command and let errors surface.
"""

from __future__ import annotations
import os
import sys
import zipfile
import subprocess
import pathlib
import tempfile

def is_available() -> bool:
    return sys.platform == "win32"


def _package_dir() -> pathlib.Path:
    return pathlib.Path(__file__).parent


def _driver_zip() -> pathlib.Path:
    return _package_dir() / "drivers" / "usbmmidd_v2.zip"


def _find_deviceinstaller(root: pathlib.Path) -> pathlib.Path | None:
    for p in root.rglob("deviceinstaller64.exe"):
        return p
    return None


def _extract_zip(zip_path: pathlib.Path, dest: pathlib.Path) -> pathlib.Path | None:
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)
    return _find_deviceinstaller(dest)


def _run_installer(exe: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    cmd = [str(exe)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def install() -> None:
    """Install the virtual display driver."""
    if not is_available():
        raise RuntimeError("Virtual display driver management is Windows-only.")

    zip_path = _driver_zip()
    if not zip_path.exists():
        raise FileNotFoundError(
            f"Driver zip not found: {zip_path}\n"
            "Download usbmmidd_v2.zip and place it in deskctrl/drivers/."
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        exe = _extract_zip(zip_path, tmp_path)
        if exe is None:
            raise RuntimeError("deviceinstaller64.exe not found in extracted zip.")
        result = _run_installer(exe, "install", "usbmmidd")
        print(result.stdout)
        if result.returncode != 0:
            raise RuntimeError(f"Driver install failed:\n{result.stderr}")
        print("Virtual display driver installed.")


def remove() -> None:
    """Remove the virtual display driver."""
    if not is_available():
        raise RuntimeError("Virtual display driver management is Windows-only.")

    zip_path = _driver_zip()
    if not zip_path.exists():
        raise FileNotFoundError(f"Driver zip not found: {zip_path}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        exe = _extract_zip(zip_path, tmp_path)
        if exe is None:
            raise RuntimeError("deviceinstaller64.exe not found in extracted zip.")
        result = _run_installer(exe, "remove", "usbmmidd")
        print(result.stdout)
        if result.returncode != 0:
            raise RuntimeError(f"Driver remove failed:\n{result.stderr}")
        print("Virtual display driver removed.")


def status() -> None:
    """Print driver status."""
    if not is_available():
        raise RuntimeError("Virtual display driver management is Windows-only.")

    zip_path = _driver_zip()
    if not zip_path.exists():
        raise FileNotFoundError(f"Driver zip not found: {zip_path}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        exe = _extract_zip(zip_path, tmp_path)
        if exe is None:
            raise RuntimeError("deviceinstaller64.exe not found in extracted zip.")
        result = _run_installer(exe, "status")
        print(result.stdout or "(no output)")
        if result.returncode != 0:
            print(f"stderr: {result.stderr}")


def activate_virtual_display() -> None:
    """Auto-install + activate virtual display (called by server --virtual flag)."""
    install()
    # After install, usbmmidd adds a virtual monitor automatically on the next
    # enable call. Attempt to enable monitor 2 (the newly added virtual one).
    zip_path = _driver_zip()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        exe = _extract_zip(zip_path, tmp_path)
        if exe is None:
            return
        # Enable second monitor (index 2)
        result = _run_installer(exe, "enableidd", "1")
        if result.returncode == 0:
            print("Virtual monitor activated.")
        else:
            print(f"Could not activate virtual monitor: {result.stderr}")
