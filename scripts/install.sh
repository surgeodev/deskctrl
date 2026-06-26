#!/usr/bin/env bash
#
# deskctrl — Full automatic installer
# ====================================
# This script checks prerequisites, installs everything needed,
# and sets up the `deskctrl` command globally or per-user.
#
# Usage:
#   curl -fsSL https://deskctrl.dev/install.sh | bash
#   # or locally:
#   cd deskctrl && bash scripts/install.sh
#
# Options:
#   --prefix=<dir>    Install to custom location (default: ~/.local)
#   --venv-dir=<dir>  Venv location (default: ~/.local/share/deskctrl/venv)
#   --no-gui          Skip GUI dependencies (PyQt6)
#   --system          Install system-wide (requires sudo)
#

set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────────
REPO="surgeodev/deskctrl"
RELEASE_URL="https://github.com/${REPO}/releases/latest/download"
MIN_PYTHON="3.8"
INSTALL_DIR="${HOME}/.local"
VENV_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/deskctrl/venv"
BIN_DIR="${INSTALL_DIR}/bin"
WITH_GUI=true
SYSTEM_WIDE=false
# Detect if running from local source or piped from curl
if [ -f "$(dirname "$0")/../setup.py" ]; then
  PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
  LOCAL_SOURCE=true
else
  PROJECT_DIR=""
  LOCAL_SOURCE=false
  CACHE_DIR="${XDG_CACHE_DIR:-${HOME}/.cache}/deskctrl"
fi

# ── Colors ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

info()  { echo -e "${CYAN}::${NC} $1"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()  { echo -e "  ${RED}✗${NC} $1"; }
header(){ echo -e "\n${BOLD}── $1 ──${NC}\n"; }

# ── Parse args ─────────────────────────────────────────────────────────────
USE_DEB=false
for arg in "$@"; do
  case "$arg" in
    --prefix=*) INSTALL_DIR="${arg#*=}"; BIN_DIR="${INSTALL_DIR}/bin" ;;
    --venv-dir=*) VENV_DIR="${arg#*=}" ;;
    --no-gui) WITH_GUI=false ;;
    --system) SYSTEM_WIDE=true ;;
    --deb) USE_DEB=true ;;  # Use .deb package on Linux
    --help|-h)
      echo "deskctrl installer — usage: bash install.sh [options]"
      echo "  --prefix=<dir>    Install location (default: ~/.local)"
      echo "  --venv-dir=<dir>  Virtual environment path"
      echo "  --no-gui          Skip PyQt6 GUI dependencies"
      echo "  --system          System-wide install (needs sudo)"
      echo "  --deb             Install via .deb package (Linux, needs sudo)"
      exit 0
      ;;
  esac
done

# On Linux, --system implies --deb
if [ "$SYSTEM_WIDE" = true ] && [[ "$OSTYPE" == "linux-gnu"* ]]; then
  USE_DEB=true
fi

# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: Check Python
# ═══════════════════════════════════════════════════════════════════════════
header "Checking prerequisites"

PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PYTHON="$cmd"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  fail "Python not found! Please install Python ${MIN_PYTHON}+"
  if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "       sudo apt install python3 python3-pip python3-venv"
  elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo "       brew install python@3"
  fi
  exit 1
fi

PY_VER=$("$PYTHON" --version 2>&1 | grep -oP '\d+\.\d+')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
  fail "Python ${MIN_PYTHON}+ required (found ${PY_VER})"
  exit 1
fi
ok "Python ${PY_VER} found at $(command -v "$PYTHON")"

# Check pip
if ! "$PYTHON" -m pip --version &>/dev/null; then
  fail "pip not available for ${PYTHON}"
  if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "       sudo apt install python3-pip python3-venv"
  fi
  exit 1
fi
ok "pip available"

# ── STEP 2: venv module ───────────────────────────────────────────────────
header "Setting up virtual environment"

if ! "$PYTHON" -m venv --help &>/dev/null; then
  fail "Python venv module not available"
  if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "       sudo apt install python3-venv"
  fi
  exit 1
fi

# ── STEP 2a: Install via .deb (Linux, fast path) ─────────────────────
if [ "$USE_DEB" = true ] && [[ "$OSTYPE" == "linux-gnu"* ]]; then
  header "Downloading .deb package"
  # Try to get version from pip package or use latest
  DEB_FILE="deskctrl_latest_amd64.deb"
  DEB_URL="https://github.com/${REPO}/releases/latest/download/${DEB_FILE}"
  CACHE_DEB="${CACHE_DIR}/${DEB_FILE}"
  mkdir -p "$CACHE_DIR"

  info "Downloading ${DEB_FILE}..."
  curl -sSL "$DEB_URL" -o "$CACHE_DEB"
  ok "Downloaded ${DEB_FILE}"

  info "Installing via dpkg..."
  if [ "$EUID" -eq 0 ]; then
    dpkg -i "$CACHE_DEB"
  else
    sudo dpkg -i "$CACHE_DEB"
  fi
  sudo apt-get install -f -y 2>/dev/null || true  # Fix deps
  ok ".deb package installed"

  # Verify
  if deskctrl --version &>/dev/null; then
    header "Installation complete!"
    deskctrl --version
    exit 0
  else
    fail "Deb installation failed, falling back to pip install..."
  fi
fi

# ── STEP 2b: Download source if piped from curl ──────────────────────
if [ "$LOCAL_SOURCE" = false ]; then
  header "Downloading deskctrl"
  mkdir -p "$CACHE_DIR"
  if command -v git &>/dev/null; then
    info "Cloning repository..."
    git clone --depth 1 "https://github.com/${REPO}.git" "$CACHE_DIR/src"
    PROJECT_DIR="$CACHE_DIR/src"
  else
    info "Downloading archive..."
    curl -sSL "${RELEASE_URL}/deskctrl-src.tar.gz" -o "$CACHE_DIR/src.tar.gz"
    mkdir -p "$CACHE_DIR/src"
    tar xzf "$CACHE_DIR/src.tar.gz" -C "$CACHE_DIR/src" --strip-components=1
    PROJECT_DIR="$CACHE_DIR/src"
  fi
  LOCAL_SOURCE=true
  ok "Source downloaded to ${PROJECT_DIR}"
fi

if [ "$SYSTEM_WIDE" = true ]; then
  # System-wide: check for sudo
  if [ "$EUID" -ne 0 ]; then
    fail "System-wide install requires sudo"
    echo "       sudo bash scripts/install.sh --system"
    exit 1
  fi
  VENV_DIR="/opt/deskctrl/venv"
  BIN_DIR="/usr/local/bin"
  info "System-wide installation to /opt/deskctrl"
fi

# Create venv
if [ -d "$VENV_DIR" ]; then
  warn "Virtual environment exists at ${VENV_DIR}"
  info "Remove it first with: rm -rf ${VENV_DIR}"
  info "Or run: source ${VENV_DIR}/bin/activate && pip install --upgrade deskctrl"
  read -r -p "  Overwrite existing venv? [y/N] " response
  if [[ "$response" =~ ^[Yy]$ ]]; then
    rm -rf "$VENV_DIR"
    info "Creating fresh virtual environment..."
  else
    info "Using existing virtual environment."
  fi
fi

if [ ! -d "$VENV_DIR" ]; then
  info "Creating virtual environment at ${VENV_DIR}..."
  mkdir -p "$(dirname "$VENV_DIR")"
  "$PYTHON" -m venv "$VENV_DIR"
  ok "Virtual environment created"
fi

# Activate venv
source "${VENV_DIR}/bin/activate"
ok "Virtual environment activated ($("$PYTHON" -c "import sys; print(sys.executable)"))"

# Upgrade pip
info "Upgrading pip..."
"$PYTHON" -m pip install --upgrade pip -q
ok "pip upgraded"

# ── STEP 3: Install dependencies ──────────────────────────────────────────
header "Installing dependencies"

PIP="${VENV_DIR}/bin/pip"
PY="${VENV_DIR}/bin/python"

# Installation groupée des dépendances (plus rapide, pip résout tout ensemble)
echo -n "  -> Installation des dépendances (numpy, opencv, mss, pynput...) ... "
$PIP install --quiet -e "$PROJECT_DIR" > /tmp/deskctrl_install.log 2>&1
if [ $? -eq 0 ]; then
  echo -e "${GREEN}fait${NC}"
else
  echo -e "${RED}échec${NC}"
  tail -10 /tmp/deskctrl_install.log
  fail "Impossible d'installer les dépendances de base"
  echo "       Consultez /tmp/deskctrl_install.log pour les détails"
  exit 1
fi
ok "Core dependencies installed (deskctrl + numpy, opencv, mss, pynput, click)"

# GUI dependencies (PyQt6)
if [ "$WITH_GUI" = true ]; then
  echo ""
  info "Vérification de PyQt6 (interface graphique)..."
  if $PY -c "import PyQt6" 2>/dev/null; then
    ok "PyQt6 déjà installé"
  else
    info "Téléchargement de PyQt6 (peut prendre 1-3 minutes)..."
    if $PIP install PyQt6 > /tmp/deskctrl_pyqt6.log 2>&1; then
      ok "PyQt6 installé"
    else
      warn "PyQt6 non disponible via pip — essai paquet système..."
      GUI_INSTALLED=false
      if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        if command -v apt &>/dev/null; then
          sudo apt install -y python3-pyqt6 2>/dev/null && GUI_INSTALLED=true
        elif command -v dnf &>/dev/null; then
          sudo dnf install -y python3-qt6 2>/dev/null && GUI_INSTALLED=true
        fi
      elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install pyqt6 2>/dev/null && GUI_INSTALLED=true
      fi
      if [ "$GUI_INSTALLED" = true ]; then
        ok "PyQt6 installé (paquet système)"
      else
        warn "PyQt6 non installé — l'interface graphique ne sera pas disponible"
        echo "       Utilise la CLI : deskctrl connect <ip>  ou  deskctrl headless <ip>"
        echo "       Réessaye avec : bash scripts/install.sh  (si le réseau est disponible)"
      fi
    fi
  fi
fi

# Discovery (zeroconf)
echo ""
info "Vérification de zeroconf (découverte LAN)..."
$PIP install --quiet zeroconf > /tmp/deskctrl_zeroconf.log 2>&1
if $PY -c "import zeroconf" 2>/dev/null; then
  ok "zeroconf installé — découverte LAN active"
else
  warn "zeroconf non installé — découverte LAN désactivée (optionnel)"
fi

# ── STEP 4: Create launcher ────────────────────────────────────────────────
header "Installing launcher script"

LAUNCHER="${BIN_DIR}/deskctrl"
mkdir -p "$BIN_DIR"

cat > "$LAUNCHER" << LAUNCHER_EOF
#!/usr/bin/env bash
# deskctrl — auto-launcher
# Generated by scripts/install.sh

VENV="${VENV_DIR}"
export DESKCTRL_VENV="\${VENV}"

# Activate venv and run deskctrl
if [ -f "\${VENV}/bin/activate" ]; then
  source "\${VENV}/bin/activate"
  exec "${VENV}/bin/deskctrl" "\$@"
else
  echo "Error: deskctrl virtual environment not found at \${VENV}"
  echo "Reinstall: bash <(curl -fsSL https://deskctrl.dev/install.sh)"
  exit 1
fi
LAUNCHER_EOF

chmod +x "$LAUNCHER"
ok "Launcher created at ${LAUNCHER}"

# Also create a symlink in the project for convenience
ln -sf "$LAUNCHER" "${PROJECT_DIR}/scripts/deskctrl" 2>/dev/null || true

# ── STEP 5: Verify ─────────────────────────────────────────────────────────
header "Verifying installation"

if "${VENV_DIR}/bin/deskctrl" --version &>/dev/null; then
  VERSION=$("${VENV_DIR}/bin/deskctrl" --version 2>&1)
  ok "deskctrl ${VERSION} installed successfully"
else
  fail "Installation verification failed"
  "${VENV_DIR}/bin/deskctrl" --version 2>&1 || true
  exit 1
fi

# Test PyQt6 if GUI was requested
if [ "$WITH_GUI" = true ]; then
  if "${VENV_DIR}/bin/python" -c "from PyQt6.QtWidgets import QApplication; print('ok')" 2>/dev/null; then
    ok "GUI dependencies (PyQt6) ready"
  else
    warn "PyQt6 not available — GUI will not work"
    echo "    Try: ${VENV_DIR}/bin/pip install PyQt6"
    echo "    Or run without GUI: deskctrl --help"
  fi
fi

if "${VENV_DIR}/bin/python" -c "import zeroconf" 2>/dev/null; then
  ok "LAN discovery (zeroconf) ready"
else
  warn "zeroconf not installed — LAN discovery disabled"
fi

# ── Done ───────────────────────────────────────────────────────────────────
header "Installation complete!"

echo -e "  ${GREEN}deskctrl is ready!${NC}"
echo ""
echo "  To use it:"
echo ""
echo "    ${BOLD}deskctrl${NC}                           # See available commands"
echo "    ${BOLD}deskctrl serve${NC}                     # Start server (host)"
echo "    ${BOLD}deskctrl connect <ip>${NC}              # Connect as client"
echo "    ${BOLD}deskctrl headless <ip>${NC}             # Control without display"
echo "    ${BOLD}deskctrl gui${NC}                       # Launch GUI"
echo "    ${BOLD}deskctrl monitor${NC}                   # Barrier-like seamless control"
echo ""
echo "  If '${LAUNCHER}' is not on your PATH, add it:"
echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
echo "    # or add that line to ~/.bashrc or ~/.zshrc"
echo ""
echo "  Or use the venv directly:"
echo "    source ${VENV_DIR}/bin/activate"
echo "    deskctrl --help"
echo ""
