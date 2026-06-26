# deskctrl -- Remote Desktop Controller

> Like **scrcpy** but for desktop-to-desktop control.  
> Cross-platform (Windows, Linux, macOS). Network **or** HDMI cable.

Control one computer from another -- locally or over the network -- with optional headless mode (`--nowindow`).

---

## ? Features

| Feature | Description |
|---|---|
| **? Network mode** | Control any computer on your LAN (or Internet via VPN). |
| **? HDMI mode** | Plug an HDMI capture card (HDMI->USB) for direct video input. |
| **?? Full input forwarding** | Keyboard, mouse (move/click/scroll), clipboard. |
| **? Headless mode** | `--nowindow` -- control without displaying (like scrcpy `--nowindow`). |
| **? LAN auto-discovery** | Find servers automatically via mDNS (Bonjour/Zeroconf). |
| **?? GUI + CLI** | Full graphical interface (PyQt6) and command-line. |
| **??? Cross-platform** | Server and client work on all three OSes. |
| **? Low latency** | MJPEG streaming, tunable quality and FPS. |

---

## ? Quick Start

### ? Installation automatique (recommande)

Une seule commande, tout est pris en charge :

```bash
bash <(curl -fsSL https://deskctrl.dev/install.sh)
```

Ou depuis le depot local :

```bash
git clone https://github.com/deskctrl/deskctrl.git
cd deskctrl
bash scripts/install.sh
```

**Ce script fait tout automatiquement :**
- ? Verifie Python 3.8+
- ? Cree un environnement virtuel isole
- ? Installe toutes les dependances (PyQt6, zeroconf, OpenCV...)
- ? Cree la commande `deskctrl` accessible dans le terminal
- ? Fonctionne sur Linux, macOS et Windows (WSL)

### Options d'installation

```bash
# Sans GUI (CLI uniquement)
bash scripts/install.sh --no-gui

# Installation systeme (necessite sudo)
sudo bash scripts/install.sh --system

# Installation manuelle avec pip
pip install deskctrl                  # CLI uniquement
pip install 'deskctrl[gui]'           # Avec interface graphique
pip install 'deskctrl[all]'           # Tout (GUI + decouverte LAN)
```

### Depuis les sources

```bash
git clone https://github.com/deskctrl/deskctrl.git
cd deskctrl
bash scripts/install.sh               # Recommande (auto)
# Ou manuellement :
python3 -m venv venv
source venv/bin/activate
pip install -e '.[all]'
```

---

## ? Usage

### 1?? Start the server (the machine being controlled)

```bash
deskctrl serve
```

Optional flags:
```bash
deskctrl serve --port 5830 --fps 30 --quality 80 --monitor 1
deskctrl serve --nowindow                    # Headless server (no preview)
deskctrl serve --no-discovery                # Disable LAN advertising
```

### 2?? Connect as client (the machine doing the controlling)

**With display:**
```bash
deskctrl connect 192.168.1.100
deskctrl connect --auto                      # Auto-discover on LAN
deskctrl connect host --fullscreen
```

**Headless mode** (no display window, just control like scrcpy `--nowindow`):
```bash
deskctrl headless 192.168.1.100
deskctrl headless --auto
```

### 3?? GUI mode

```bash
deskctrl gui
```

In the GUI:
- **File > Connect** -- enter IP:port or scan LAN
- **File > Start Server** -- start hosting
- **View > Toggle Fit** -- fit/window mode
- **F11** -- fullscreen
- Right panel -- adjust quality, FPS, HDMI mode, headless

### 4?? Scan for servers

```bash
deskctrl scan
```

### 5?? HDMI capture mode

List available capture devices:
```bash
deskctrl hdmi
```

Use HDMI mode on the server:
```bash
deskctrl serve --hdmi /dev/video0
```

Or toggle it from the GUI via the HDMI checkbox.

---

## ? How It Works

```
?---------------------------------?         TCP/IP          ?---------------------------------?
?   SERVER (Host) ? ?--------------------------------------------??  CLIENT (Ctrl)  ?
?                 ?     video stream         ?                 ?
?  ?---------------------?  ?     input events         ?  ?---------------------?  ?
?  ?Screen Capt.?----?------------------------------------------------??  ?Display    ?  ?
?  ?(mss)       ?  ?   JPEG frames           ?  ?(PyQt6/    ?  ?
?  +---------------------?  ?                          ?  ? Pygame)   ?  ?
?  ?---------------------?  ?                          ?  +---------------------?  ?
?  ?Input Sim.  ??-?-------------------------------------------------?----?Input Capt.  ?  ?
?  ?(pynput)   ?  ?   mouse/keyboard         ?  ?(pynput)    ?  ?
?  +---------------------?  ?                          ?  +---------------------?  ?
?                 ?                          ?                 ?
?  HDMI Capture   ?    Optional: HDMI        ?                 ?
?  (V4L2/DShow)   ?    capture card          ?                 ?
+---------------------------------?                          +---------------------------------?
```

### Protocol

Simple binary protocol over TCP:
- **8-byte header**: message type (u32) + payload length (u32)
- **Payload**: varies by type (JPEG frame, mouse event, keyboard event, etc.)
- **Keepalive**: periodic ping/pong every 5s

### Screen Capture

Uses `mss` (cross-platform) for efficient screen capture:
- **Windows**: DXGI / BitBlt
- **Linux**: X11 (XSHM / Xdamage)
- **macOS**: CoreGraphics (CGDisplayStream)

### Input Forwarding

Uses `pynput` for both input capture (client) and input simulation (server).

---

## ? Requirements

| Dependency | Minimum | Use |
|---|---|---|
| Python | 3.8+ | Runtime |
| click | 8.0+ | CLI |
| mss | 9.0+ | Screen capture |
| opencv-python | 4.8+ | JPEG encode/decode, HDMI capture |
| pynput | 1.7+ | Input capture & simulation |
| Pillow | 9.0+ | Image processing |
| PyQt6 | 6.5+ | GUI (optional) |
| zeroconf | 0.131+ | LAN discovery (optional) |

---

## ?? Cross-Platform Notes

### Linux
- **X11**: Works out of the box (tested on Ubuntu, Fedora, Arch)
- **Wayland**: `mss` works via XWayland. For native Wayland, PipeWire capture is planned.
- **HDMI**: V4L2 (`/dev/video*`) -- plug and play

### Windows
- **Screen capture**: DXGI via mss -- works on Windows 8+
- **HDMI**: DirectShow -- use with any USB capture card
- **Display**: PyQt6 or OpenCV window

### macOS
- **Screen capture**: CoreGraphics via mss -- needs accessibility permissions
- **HDMI**: AVFoundation -- plug and play
- **Input simulation**: Needs accessibility permissions in System Settings

---

## ? Roadmap

- [ ] **H.264/H.265 hardware encoding** -- lower bandwidth, higher FPS
- [ ] **Audio forwarding** -- stream system audio
- [ ] **Clipboard sync** -- bidirectional clipboard sharing
- [ ] **File transfer** -- drag & drop files between machines
- [ ] **Encryption** -- TLS for secure connections over Internet
- [ ] **Wayland native capture** -- PipeWire support
- [ ] **Multiple simultaneous clients** -- broadcast mode
- [ ] **Recording** -- save session to video file

---

## ??? Development

```bash
git clone https://github.com/deskctrl/deskctrl.git
cd deskctrl
pip install -e ".[all]"
deskctrl serve    # Test server
deskctrl gui      # Test GUI
```

Run tests:
```bash
python -m pytest tests/
```

---

## ? License

MIT License -- see [LICENSE](LICENSE).

---

## ? Inspiration

- **[scrcpy](https://github.com/Genymobile/scrcpy)** -- the gold standard for Android screen mirroring
- **VNC / TeamViewer / AnyDesk** -- remote desktop tools
- **mss / pynput / OpenCV** -- amazing Python libraries that make this possible
