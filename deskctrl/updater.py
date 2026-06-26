"""Self-update logic for deskctrl.

Checks GitHub releases, downloads the right binary for the platform,
and replaces the current executable.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from . import __version__, __appname__

GITHUB_REPO = "surgeodev/deskctrl"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(tag: str) -> tuple:
    """Parse 'v0.2.0' into (0, 2, 0) for comparison."""
    tag = tag.lstrip("v")
    parts = tag.split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)


def _get_platform_asset() -> str:
    """Return the release asset filename for the current platform."""
    if sys.platform == "win32":
        return "deskctrl-windows-x64.zip"
    elif sys.platform == "darwin":
        return "deskctrl-macos-x64.tar.gz"
    else:
        return "deskctrl_0.2.3-1_amd64.deb"


def _get_download_url(asset_name: str, tag: str) -> str:
    """Build download URL for a release asset."""
    return f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{asset_name}"


def check_latest() -> Optional[str]:
    """Check latest GitHub release version. Returns tag string or None."""
    import urllib.request

    try:
        req = urllib.request.Request(GITHUB_API, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("tag_name")
    except Exception:
        return None


def download_asset(dest_dir: str, tag: str, asset_name: str) -> Optional[str]:
    """Download a release asset to dest_dir. Returns local path or None."""
    import urllib.request

    url = _get_download_url(asset_name, tag)
    dest_path = os.path.join(dest_dir, asset_name)

    try:
        print(f"  Downloading {url}...")
        urllib.request.urlretrieve(url, dest_path)
        return dest_path
    except Exception as e:
        print(f"  Download failed: {e}")
        return None


def install_windows_zip(zip_path: str) -> bool:
    """Extract zip and replace current exe on Windows."""
    import zipfile

    tmp = tempfile.mkdtemp(prefix="deskctrl_update_")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        exe_src = os.path.join(tmp, "deskctrl.exe")
        if not os.path.exists(exe_src):
            # maybe extracted into a subfolder
            for root, _dirs, files in os.walk(tmp):
                for f in files:
                    if f.endswith(".exe"):
                        exe_src = os.path.join(root, f)
                        break

        if not os.path.exists(exe_src):
            print("  No deskctrl.exe found in zip")
            return False

        current_exe = sys.executable

        if getattr(sys, "frozen", False):
            # PyInstaller one-file: write a batch script that replaces and restarts
            batch = f"""@echo off
timeout /t 2 /nobreak >nul
copy /y "{exe_src}" "{current_exe}" >nul
start "" "{current_exe}" update --done
del "%~f0"
"""
            batch_path = os.path.join(tempfile.gettempdir(), "deskctrl_update.bat")
            with open(batch_path, "w") as f:
                f.write(batch)
            print("  Update will complete on next launch (restart required).")
            subprocess.Popen(["cmd.exe", "/c", batch_path],
                             creationflags=subprocess.CREATE_NO_WINDOW)
            return True
        else:
            shutil.copy2(exe_src, current_exe)
            return True

    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


def install_macos_tgz(tgz_path: str) -> bool:
    """Extract tar.gz and replace current binary on macOS."""
    import tarfile

    tmp = tempfile.mkdtemp(prefix="deskctrl_update_")
    try:
        with tarfile.open(tgz_path, "r:gz") as tf:
            tf.extractall(tmp)

        binary_candidates = []
        for root, _dirs, files in os.walk(tmp):
            for f in files:
                if f == "deskctrl":
                    binary_candidates.append(os.path.join(root, f))

        if not binary_candidates:
            print("  No deskctrl binary found in archive")
            return False

        exe_src = binary_candidates[0]

        if getattr(sys, "frozen", False):
            current_exe = sys.executable
            os.chmod(exe_src, 0o755)
            shutil.copy2(exe_src, current_exe)
            return True
        else:
            print(f"  Binary extracted to {exe_src}")
            print("  Copy it manually to your PATH")
            return False

    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


def install_linux_deb(deb_path: str) -> bool:
    """Install .deb on Linux using dpkg."""
    try:
        subprocess.run(
            ["sudo", "dpkg", "-i", deb_path],
            check=True, capture_output=True, text=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  dpkg failed: {e.stderr}")
        return False
    except FileNotFoundError:
        print("  dpkg not found (not a Debian-based system?)")
        return False


def update(force: bool = False) -> bool:
    """Check and apply update. Returns True if updated."""
    print(f"{__appname__} v{__version__}")
    print("Checking for updates...")

    latest_tag = check_latest()
    if not latest_tag:
        print("  Could not reach GitHub. Check your connection.")
        return False

    latest_ver = _parse_version(latest_tag)
    current_ver = _parse_version(__version__)

    print(f"  Current: v{__version__}")
    print(f"  Latest:  {latest_tag}")

    if latest_ver <= current_ver and not force:
        print("  Already up to date.")
        return False

    if latest_ver > current_ver:
        print(f"  New version available: {latest_tag}")

    asset_name = _get_platform_asset()
    tmpdir = tempfile.mkdtemp(prefix="deskctrl_dl_")

    try:
        dl_path = download_asset(tmpdir, latest_tag, asset_name)
        if not dl_path:
            return False

        if sys.platform == "win32":
            ok = install_windows_zip(dl_path)
        elif sys.platform == "darwin":
            ok = install_macos_tgz(dl_path)
        else:
            ok = install_linux_deb(dl_path)

        if ok:
            print(f"  Updated to {latest_tag}!")
        return ok

    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
