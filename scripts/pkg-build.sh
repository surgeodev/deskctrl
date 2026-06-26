#!/usr/bin/env bash
# Build a .deb package for deskctrl
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=$(python3 -c "import sys; sys.path.insert(0, '$PROJECT_DIR'); from deskctrl import __version__; print(__version__)")

echo "==> Building deskctrl v$VERSION .deb package"

# ── Create package directory structure ──────────────────────────────
PKG_DIR=$(mktemp -d)
trap "rm -rf '$PKG_DIR'" EXIT

DEB_DIR="$PKG_DIR/deskctrl_$VERSION-1_amd64"
mkdir -p "$DEB_DIR/DEBIAN"
mkdir -p "$DEB_DIR/usr/bin"
mkdir -p "$DEB_DIR/usr/lib/deskctrl"
mkdir -p "$DEB_DIR/usr/share/doc/deskctrl"
mkdir -p "$DEB_DIR/usr/share/man/man1"

# ── DEBIAN/control ──────────────────────────────────────────────────
cat > "$DEB_DIR/DEBIAN/control" << EOF
Package: deskctrl
Version: $VERSION-1
Section: net
Priority: optional
Architecture: amd64
Depends: python3 (>= 3.8), python3-pip, python3-venv, libxcb-xinerama0
Recommends: python3-pyqt6, python3-pyqt6.qtsvg
Maintainer: surgeodev <surgeodev@users.noreply.github.com>
Description: Remote desktop controller — like scrcpy for desktops
 Control one computer from another over your local network (or HDMI).
 .
 Features:
  • Network mode: control any PC on your LAN
  • HDMI mode: plug a capture card for direct video input
  • Monitor Control (Barrier-like): seamless cursor across machines
  • Full input forwarding (keyboard, mouse, clipboard)
  • Headless mode (--nowindow): control without display
  • LAN auto-discovery via mDNS (Zeroconf)
  • GUI (PyQt6) and CLI (click) interfaces
  • Cross-platform: Linux, Windows, macOS
EOF

# ── postinst: set up the virtual environment + wrapper ──────────────
cat > "$DEB_DIR/DEBIAN/postinst" << 'POSTINST'
#!/bin/sh
set -e

DESKCTRL_LIB="/usr/lib/deskctrl"
VENV_DIR="$DESKCTRL_LIB/venv"

case "$1" in
    configure)
        # Create venv if it doesn't exist
        if [ ! -d "$VENV_DIR" ]; then
            python3 -m venv "$VENV_DIR"
        fi

        # Install/upgrade deskctrl in the venv
        "$VENV_DIR/bin/pip" install --quiet --upgrade \
            "$DESKCTRL_LIB" 2>/dev/null || true

        # Ensure wrapper is in place
        if [ ! -f /usr/bin/deskctrl ]; then
            ln -sf "$DESKCTRL_LIB/deskctrl.sh" /usr/bin/deskctrl
        fi
        chmod +x /usr/bin/deskctrl
        ;;
esac
exit 0
POSTINST

cat > "$DEB_DIR/DEBIAN/postrm" << 'POSTRM'
#!/bin/sh
set -e
case "$1" in
    remove|purge)
        rm -rf /usr/lib/deskctrl/venv 2>/dev/null || true
        rm -f /usr/bin/deskctrl 2>/dev/null || true
        ;;
esac
exit 0
POSTRM

chmod 755 "$DEB_DIR/DEBIAN/postinst"
chmod 755 "$DEB_DIR/DEBIAN/postrm"

# ── Wrapper script ──────────────────────────────────────────────────
cat > "$DEB_DIR/usr/lib/deskctrl/deskctrl.sh" << 'WRAPPER'
#!/usr/bin/env bash
exec /usr/lib/deskctrl/venv/bin/deskctrl "$@"
WRAPPER
chmod 755 "$DEB_DIR/usr/lib/deskctrl/deskctrl.sh"

# ── Copy project files ──────────────────────────────────────────────
cp -r "$PROJECT_DIR/deskctrl" "$DEB_DIR/usr/lib/deskctrl/deskctrl"
cp "$PROJECT_DIR/setup.py" "$DEB_DIR/usr/lib/deskctrl/"
cp "$PROJECT_DIR/requirements.txt" "$DEB_DIR/usr/lib/deskctrl/"
cp "$PROJECT_DIR/pyproject.toml" "$DEB_DIR/usr/lib/deskctrl/" 2>/dev/null || true

# ── Man page ────────────────────────────────────────────────────────
cat > "$DEB_DIR/usr/share/man/man1/deskctrl.1" << 'MANPAGE'
.TH DESKCTRL 1 "2025" "deskctrl" "Remote Desktop Controller"
.SH NAME
deskctrl \- Remote desktop controller (scrcpy for desktops)
.SH SYNOPSIS
.B deskctrl
[\fIcommand\fR] [\fIoptions\fR]
.SH DESCRIPTION
Control one computer from another over local network or HDMI.
.SH COMMANDS
.TP
.B serve
Start server (machine being controlled)
.TP
.B connect
Connect as client with display
.TP
.B headless
Connect in headless mode (no display)
.TP
.B monitor
Barrier-like seamless cursor control
.TP
.B scan
Scan LAN for servers
.TP
.B hdmi
List HDMI capture devices
.TP
.B gui
Launch graphical interface
.SH OPTIONS
.TP
.B \-\-help
Show help message
.TP
.B \-\-version
Show version
.SH AUTHOR
surgeodev
MANPAGE

gzip -9 "$DEB_DIR/usr/share/man/man1/deskctrl.1"

# ── Build .deb ──────────────────────────────────────────────────────
mkdir -p "$PROJECT_DIR/dist"
cd "$PKG_DIR"
dpkg-deb --root-owner-group --build "deskctrl_$VERSION-1_amd64"
mv "deskctrl_$VERSION-1_amd64.deb" "$PROJECT_DIR/dist/"
echo "==> Created: dist/deskctrl_$VERSION-1_amd64.deb"
