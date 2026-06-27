"""deskctrl CLI -- command-line interface using click.

Usage:
    deskctrl serve          Start server (host mode)
    deskctrl connect        Connect as client (with display)
    deskctrl headless       Connect as client (no display, control only)
    deskctrl scan           Scan LAN for servers
    deskctrl hdmi           List HDMI capture devices
    deskctrl gui            Launch graphical interface
"""

import logging
import os
import sys
import time
from typing import Optional

import click

from . import __version__, __appname__
from .platform import IS_WINDOWS

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


@click.group()
@click.version_option(version=__version__, prog_name=__appname__)
def cli():
    """Remote desktop controller for PCs -- like scrcpy for desktops.

    Control one computer from another over your local network (or HDMI).

    \b
    Quick start:
        # On the remote machine (host):
        deskctrl serve

        # On your local machine (controller):
        deskctrl connect 192.168.1.100

        # Or discover servers on the LAN:
        deskctrl scan
        deskctrl connect --auto

        # GUI mode:
        deskctrl gui
    """
    pass


# ???????????????????????????????????????????????????????????????????????????
# server
# ???????????????????????????????????????????????????????????????????????????

@cli.command()
@click.option("--host", default="0.0.0.0", help="Interface to bind to")
@click.option("--port", default=5830, help="TCP port to listen on", type=int)
@click.option("--fps", default=30, help="Target frames per second", type=int)
@click.option("--quality", default=80, help="JPEG quality 1-100", type=int,
              show_default=True)
@click.option("--monitor", default=1, help="Monitor index to capture (2+ for extended)",
              type=int, show_default=True)
@click.option("--virtual", is_flag=True, default=False,
              help="Auto-create a virtual monitor on Windows (requires admin)")
@click.option("--nowindow", is_flag=True, default=False,
              help="Don't show local preview window (like scrcpy --nowindow)")
@click.option("--discovery/--no-discovery", default=True,
              help="Advertise server via mDNS (LAN auto-discovery)")
def serve(host, port, fps, quality, monitor, virtual, nowindow, discovery):
    """Start deskctrl SERVER (the machine being controlled).

    Captures the screen and streams it to connected clients.

    Use --monitor 2 to capture a second monitor (e.g. virtual display
    for extended mode). Use --virtual to auto-create a virtual monitor
    (Windows only, requires admin).
    """
    # Import here so CLI stays fast even without gui deps
    from .server import DeskctrlServer

    server = DeskctrlServer(
        host=host, port=port, fps=fps,
        quality=quality, monitor=monitor,
        no_display=nowindow,
    )

    # Optional discovery
    discovery_service = None
    if discovery:
        try:
            from .discovery import DiscoveryService
            discovery_service = DiscoveryService(port=port)
            discovery_service.start()
        except Exception as e:
            log.warning(f"Discovery not available: {e}")

    # Virtual monitor setup (Windows only)
    if virtual:
        if not IS_WINDOWS:
            click.echo("  --virtual is only supported on Windows", err=True)
            sys.exit(1)
        from .virtual_display import install_and_add
        click.echo("  Setting up virtual monitor (admin required)...")
        if not install_and_add():
            click.echo("  Virtual monitor setup failed. Run 'deskctrl driver install' manually.", err=True)
            sys.exit(1)
        click.echo(f"  Virtual monitor ready. Capturing monitor {monitor}.")

    def on_status(msg):
        click.echo(f"  {msg}")

    def on_client_connected(addr):
        click.echo(f"  Client connected: {addr[0]}:{addr[1]}")
        click.echo("  Press Ctrl+C to stop")

    server.on_status = on_status
    server.on_client_connected = on_client_connected

    if server.start():
        click.echo(f"  deskctrl server v{__version__}")
        click.echo(f"  Listening on {host}:{port}")
        click.echo(f"  Screen will be streamed at {fps} fps, quality {quality}")
        if discovery_service:
            click.echo("  LAN discovery enabled (mDNS)")
        if nowindow:
            click.echo("  Headless mode (no local preview)")
        click.echo("  Waiting for client...")
        click.echo("  Press Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            click.echo("\n  Shutting down...")
        finally:
            server.stop()
            if discovery_service:
                discovery_service.stop()
            click.echo("  Server stopped")
    else:
        click.echo("  Failed to start server", err=True)
        sys.exit(1)


# ???????????????????????????????????????????????????????????????????????????
# connect
# ???????????????????????????????????????????????????????????????????????????

@cli.command()
@click.argument("host", required=False, default=None)
@click.option("--port", default=5830, help="Server TCP port", type=int,
              show_default=True)
@click.option("--auto", is_flag=True, default=False,
              help="Auto-discover server on LAN")
@click.option("--quality", default=80, help="Request JPEG quality", type=int)
@click.option("--fps", default=30, help="Request target FPS", type=int)
@click.option("--fullscreen", is_flag=True, default=False,
              help="Start in fullscreen mode")
def connect(host, port, auto, quality, fps, fullscreen):
    """Connect to a deskctrl SERVER as a client (with display).

    Required: HOST (IP address or hostname of the deskctrl server).
    Use --auto to auto-discover a server on the LAN instead.
    """
    if not host and not auto:
        click.echo("  Error: provide HOST or use --auto to discover", err=True)
        sys.exit(1)

    if auto:
        discovered = _discover_server()
        if not discovered:
            click.echo("  No deskctrl servers found on LAN", err=True)
            sys.exit(1)
        host = discovered["host"]
        port = discovered["port"]
        click.echo(f"  Discovered: {discovered['name']} at {host}:{port}")
    else:
        # Parse optional port from host argument (e.g. "192.168.1.100:5830")
        host, parsed_port = _parse_host_port(host)
        if parsed_port is not None:
            port = parsed_port

    _run_client(host, port, quality, fps, fullscreen)


# ???????????????????????????????????????????????????????????????????????????
# headless
# ???????????????????????????????????????????????????????????????????????????

@cli.command()
@click.argument("host", required=False, default=None)
@click.option("--port", default=5830, help="Server TCP port", type=int,
              show_default=True)
@click.option("--auto", is_flag=True, default=False,
              help="Auto-discover server on LAN")
def headless(host, port, auto):
    """Connect in HEADLESS mode (no display window).

    Like scrcpy's --nowindow: you control the remote machine
    but don't see its screen on the client.
    """
    if not host and not auto:
        click.echo("  Error: provide HOST or use --auto to discover", err=True)
        sys.exit(1)

    if auto:
        discovered = _discover_server()
        if not discovered:
            click.echo("  No deskctrl servers found on LAN", err=True)
            sys.exit(1)
        host = discovered["host"]
        port = discovered["port"]
        click.echo(f"  Discovered: {discovered['name']} at {host}:{port}")
    else:
        host, parsed_port = _parse_host_port(host)
        if parsed_port is not None:
            port = parsed_port

    from .client import DeskctrlClient, DISPLAY_NONE

    client = DeskctrlClient(
        host=host, port=port, display_mode=DISPLAY_NONE,
    )

    def on_status(msg):
        click.echo(f"  {msg}")

    client.on_status = on_status

    click.echo(f"  deskctrl headless client v{__version__}")
    click.echo(f"  Connecting to {host}:{port}...")
    click.echo("  (No display window -- control only)")
    click.echo("  Press Ctrl+C to disconnect")

    if client.connect():
        try:
            while client.state.connected:
                time.sleep(1)
        except KeyboardInterrupt:
            click.echo("\n  Disconnecting...")
        finally:
            client.disconnect()
            click.echo("  Disconnected")
    else:
        click.echo("  Connection failed", err=True)
        sys.exit(1)


# ???????????????????????????????????????????????????????????????????????????
# scan
# ???????????????????????????????????????????????????????????????????????????

@cli.command()
@click.option("--timeout", default=3.0, help="Scan duration in seconds",
              type=float, show_default=True)
def scan(timeout):
    """Scan the LAN for running deskctrl servers."""
    click.echo(f"  Scanning LAN for deskctrl servers ({timeout}s)...")
    discovered = _discover_server(timeout=timeout)

    if discovered:
        click.echo(f"\n  Found {len(discovered)} server(s):")
        for svc in discovered:
            click.echo(f"    {svc['name']:20s}  {svc['host']:15s}:{svc['port']}")
    else:
        click.echo("  No servers found.")
        click.echo("  Make sure deskctrl serve is running on another machine.")
        click.echo("  ZeroConf/mDNS must be enabled on the network.")


def _parse_host_port(host_str: str) -> tuple:
    """Parse 'host:port' from argument. Returns (host, port_or_None)."""
    if host_str and ":" in host_str:
        parts = host_str.rsplit(":", 1)
        try:
            return parts[0], int(parts[1])
        except ValueError:
            pass  # not a valid port, treat as plain host
    return host_str, None


def _discover_server(timeout: float = 3.0) -> list:
    """Discover deskctrl servers on LAN. Returns list of service dicts."""
    try:
        from .discovery import DiscoveryBrowser
        browser = DiscoveryBrowser()
        browser.start()
        services = browser.wait_for_services(min_services=1, timeout=timeout)
        browser.stop()
        return services
    except Exception as e:
        log.debug(f"Discovery error: {e}")
        return []


# ???????????????????????????????????????????????????????????????????????????
# hdmi
# ???????????????????????????????????????????????????????????????????????????

@cli.command()
def hdmi():
    """List available HDMI capture devices."""
    from .hdmi_capture import list_capture_devices

    devices = list_capture_devices()
    if devices:
        click.echo("  Available HDMI/Video capture devices:")
        for i, dev in enumerate(devices):
            click.echo(f"  [{i}] {dev.get('name', '?')}")
            click.echo(f"       Path: {dev.get('path', '?')}")
            click.echo(f"       Type: {dev.get('type', '?')}")
            if "width" in dev and "height" in dev:
                click.echo(f"       Size: {dev['width']}x{dev['height']}")
        click.echo("\n  Use with: deskctrl serve --hdmi /dev/video0")
    else:
        click.echo("  No HDMI capture devices found.")
        click.echo("  Connect an HDMI-to-USB capture card and try again.")


# ???????????????????????????????????????????????????????????????????????????
# gui
# ???????????????????????????????????????????????????????????????????????????

@cli.command()
@click.option("--host", default=None, help="Server host to auto-connect")
@click.option("--port", default=5830, help="Server port", type=int)
def gui(host, port):
    """Launch the deskctrl graphical interface."""
    from .gui import launch_gui as _launch_gui
    sys.exit(_launch_gui())


# ???????????????????????????????????????????????????????????????????????????
# monitor
# ???????????????????????????????????????????????????????????????????????????

@cli.command()
@click.option("--config", default=None,
              help="Path to monitor layout JSON file")
@click.option("--add", multiple=True, default=[],
              help="Add server: DIRECTION=HOST:PORT  (e.g. right=192.168.1.100:5830)")
@click.option("--remove", multiple=True, default=[],
              help="Remove server by direction (e.g. right)")
@click.option("--no-start", is_flag=True, default=False,
              help="Only update config, don't start")
@click.option("--list", "list_only", is_flag=True, default=False,
              help="Show current layout and exit")
@click.option("--margin", default=None, type=int,
              help="Activation margin in pixels (default: 5)")
def monitor(config, add, remove, no_start, list_only, margin):
    """Monitor Control mode -- seamless cursor across machines (Barrier-like).

    Configure where your other machines' screens are relative to yours.
    Move the mouse to the edge -> it takes control of the remote machine.

    Pure input forwarding (no video) = zero latency.

    \b
    Wayland:
      Works under XWayland (most apps). Native Wayland compositors
      need libei (not yet packaged). ydotool is detected automatically
      as a fallback for input simulation on Wayland.

    \b
    Examples:
        # Start with existing config
        deskctrl monitor

        # Quick add + start
        deskctrl monitor --add right=192.168.1.100

        # Add with custom port
        deskctrl monitor --add "left=10.0.0.5:5830"

        # View current layout
        deskctrl monitor --list

        # Configure without starting
        deskctrl monitor --add right=192.168.1.100 --add top=10.0.0.5 --no-start
    """
    from .monitor_control import MonitorLayout, DEFAULT_CONFIG_FILE

    # Resolve config path
    cfg_path = config or DEFAULT_CONFIG_FILE

    # Load or create layout
    if os.path.exists(cfg_path):
        layout = MonitorLayout.from_file(cfg_path)
    else:
        layout = MonitorLayout()

    # Apply margin override
    if margin is not None:
        layout.activation_margin = margin

    # Apply --add directives
    for add_str in add:
        parts = add_str.split("=", 1)
        if len(parts) != 2:
            click.echo(f"  Error: --add expects DIRECTION=HOST:PORT, got '{add_str}'",
                       err=True)
            sys.exit(1)
        direction = parts[0].strip()
        host_part = parts[1].strip()
        # Parse host:port
        if ":" in host_part:
            host_val, port_str = host_part.rsplit(":", 1)
            try:
                port_val = int(port_str)
            except ValueError:
                click.echo(f"  Error: invalid port in '{host_part}'", err=True)
                sys.exit(1)
        else:
            host_val = host_part
            port_val = 5830

        if direction not in ("left", "right", "top", "bottom"):
            click.echo(f"  Error: direction must be left|right|top|bottom, "
                       f"got '{direction}'", err=True)
            sys.exit(1)

        layout.add_server(direction, host_val, port_val)
        click.echo(f"  Added {direction:>6s} -> {host_val}:{port_val}")

    # Apply --remove directives
    for direction in remove:
        if direction not in ("left", "right", "top", "bottom"):
            click.echo(f"  Error: direction must be left|right|top|bottom, "
                       f"got '{direction}'", err=True)
            sys.exit(1)
        removed = layout.get_server(direction)
        if removed:
            layout.remove_server(direction)
            click.echo(f"  Removed {direction:>6s} -> {removed.name}")
        else:
            click.echo(f"  No server configured for {direction}")

    # Save
    layout.to_file(cfg_path)

    # --list: show and exit
    if list_only:
        click.echo(f"\n  Monitor Layout ({cfg_path}):")
        click.echo(f"  Screen: auto-detected on start")
        click.echo(f"  Activation margin: {layout.activation_margin}px")
        if layout.servers:
            click.echo(f"  Servers configured:")
            for s in layout.servers:
                click.echo(f"    {s.direction:>6s} -> {s.name} "
                           f"({s.host}:{s.port})")
        else:
            click.echo(f"  No servers configured.")
            click.echo(f"  Add one: deskctrl monitor --add right=192.168.1.100")
        return

    if no_start or not layout.servers:
        if not layout.servers:
            click.echo("  No servers configured. Use --add to add one.")
        else:
            click.echo(f"  Configuration saved to {cfg_path}")
            click.echo(f"  Run 'deskctrl monitor' to start")
        return

    # ---- Start the engine --------------------------------------------------------------------------------------------
    from .monitor_control import MonitorControlEngine

    engine = MonitorControlEngine(layout)

    def on_status(msg):
        click.echo(f"  {msg}")

    engine.on_status = on_status

    click.echo(f"\n  ??? deskctrl Monitor Control ????????????????????")
    click.echo(f"  ?  Move mouse to an edge -> remote control     ?")
    click.echo(f"  ?  Press ESC to release control                ?")
    click.echo(f"  ?  Ctrl+C to quit                              ?")
    click.echo(f"  ?????????????????????????????????????????????????")

    if engine.start():
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            click.echo("\n  Shutting down...")
        finally:
            engine.stop()
            click.echo("  Stopped")
    else:
        click.echo("  Failed to start monitor control", err=True)
        sys.exit(1)


# ---- Module-level helpers for pygame display modes --------------------------

_PG_BTN_MAP = {1: 1, 2: 3, 3: 2, 4: 4, 5: 5}  # pygame -> deskctrl button

_PG_SPECIAL = {
    # (keycode, name): keysym
    8: 0xFF08, 9: 0xFF09, 13: 0xFF0D, 27: 0xFF1B,
    36: 0xFF50, 35: 0xFF57, 37: 0xFF51, 39: 0xFF53,
    38: 0xFF52, 40: 0xFF54, 33: 0xFF55, 34: 0xFF56,
    127: 0xFFFF, 16: 0xFFE1, 304: 0xFFE1, 303: 0xFFE2,
    306: 0xFFE3, 305: 0xFFE4, 301: 0xFFE5, 308: 0xFFE7,
    307: 0xFFE8, 310: 0xFFEB, 309: 0xFFEC, 32: 0x0020,
}
# Function keys F1-F12
for i in range(12):
    _PG_SPECIAL[282 + i] = 0xFFBE + i


def _pygame_keysym(key) -> tuple:
    """Convert pygame key integer to (keysym, keycode)."""
    if 32 <= key <= 126:
        return key, 0
    keysym = _PG_SPECIAL.get(key, 0)
    return keysym, 0


def _is_local_key(key, mod) -> bool:
    """Check if a key combo should be handled locally (not sent to remote)."""
    import pygame as pg
    return (key == pg.K_ESCAPE or
            (key == pg.K_q and (mod & pg.KMOD_CTRL)) or
            (key == pg.K_f and (mod & pg.KMOD_ALT)))


# ???????????????????????????????????????????????????????????????????????????
# Shared client runner (for connect command)
# ???????????????????????????????????????????????????????????????????????????

def _run_client(host: str, port: int, quality: int = 80,
                fps: int = 30, fullscreen: bool = False):
    """Run client with pygame or OpenCV display."""
    # Try pygame first (lower latency)
    try:
        _run_client_pygame(host, port, quality, fps, fullscreen)
        return
    except ImportError:
        pass

    # Fallback to OpenCV display
    try:
        _run_client_opencv(host, port, quality, fps, fullscreen)
        return
    except Exception as e:
        click.echo(f"  Display not available: {e}")
        click.echo(f"  Install pygame: pip install pygame")
        click.echo(f"  Or use: deskctrl gui")
        sys.exit(1)


def _run_client_pygame(host: str, port: int, quality: int = 80,
                       fps: int = 30, fullscreen: bool = False):
    """Client display using pygame with integrated mouse/keyboard input."""
    import pygame as pg

    from .client import DeskctrlClient, DISPLAY_NONE
    from .protocol import (
        MsgType,
        encode_pointer_move, encode_pointer_button,
        encode_key_event, encode_scroll,
    )

    pg.init()
    pg.display.set_caption(f"{__appname__} -- {host}:{port}")

    client = DeskctrlClient(
        host=host, port=port, display_mode=DISPLAY_NONE,
    )
    # We handle display and input ourselves
    screen = None
    clock = pg.time.Clock()
    running = True
    frame_surface = None
    server_w = 0
    server_h = 0
    window_active = True  # input only sent when window is focused

    def _scale_coords(px, py):
        """Map window pixel to server screen coordinate."""
        if not screen or not server_w or not server_h:
            return px, py
        win_w, win_h = screen.get_size()
        return (px * server_w / win_w, py * server_h / win_h)

    def on_status(msg):
        click.echo(f"  {msg}")

    def on_frame(frame):
        nonlocal frame_surface, screen, server_w, server_h
        h, w = frame.shape[:2]
        server_w, server_h = w, h
        if screen is None:
            flags = pg.FULLSCREEN | pg.SCALED if fullscreen else pg.RESIZABLE
            screen = pg.display.set_mode((w, h), flags)
        frame_rgb = frame[..., ::-1]
        frame_surface = pg.surfarray.make_surface(frame_rgb.swapaxes(0, 1))

    def on_resolution(w, h):
        nonlocal screen, server_w, server_h
        server_w, server_h = w, h
        if screen is None:
            flags = pg.FULLSCREEN | pg.SCALED if fullscreen else pg.RESIZABLE
            screen = pg.display.set_mode((w, h), flags)

    client.on_status = on_status
    client.on_frame = on_frame
    client.on_resolution = on_resolution

    click.echo(f"  deskctrl client v{__version__}")
    click.echo(f"  Connecting to {host}:{port}...")

    if not client.connect():
        click.echo("  Connection failed", err=True)
        pg.quit()
        sys.exit(1)

    click.echo("  Click on the window to send input.")
    click.echo("  Press Ctrl+Q to quit, ESC to close, Alt+F for fullscreen.")

    try:
        while running and client.state.connected:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    running = False

                elif event.type == pg.WINDOWFOCUSGAINED:
                    window_active = True
                elif event.type == pg.WINDOWFOCUSLOST:
                    window_active = False

                elif event.type == pg.KEYDOWN:
                    if event.key == pg.K_ESCAPE:
                        running = False
                    elif event.key == pg.K_f and (event.mod & pg.KMOD_ALT) and screen:
                        pg.display.toggle_fullscreen()
                    elif event.key == pg.K_q and (event.mod & pg.KMOD_CTRL):
                        running = False
                    elif window_active:
                        keysym, keycode = _pygame_keysym(event.key)
                        if keysym:
                            client.send_input(MsgType.KEY_EVENT,
                                              encode_key_event(keysym, keycode, True))

                elif event.type == pg.KEYUP:
                    if window_active and not _is_local_key(event.key, event.mod):
                        keysym, keycode = _pygame_keysym(event.key)
                        if keysym:
                            client.send_input(MsgType.KEY_EVENT,
                                              encode_key_event(keysym, keycode, False))

                elif event.type == pg.MOUSEMOTION:
                    if window_active:
                        sx, sy = _scale_coords(*event.pos)
                        client.send_input(MsgType.POINTER_MOVE,
                                          encode_pointer_move(sx, sy))

                elif event.type == pg.MOUSEBUTTONDOWN:
                    if window_active:
                        btn = _PG_BTN_MAP.get(event.button, 1)
                        sx, sy = _scale_coords(*event.pos)
                        client.send_input(MsgType.POINTER_BUTTON,
                                          encode_pointer_button(btn, True, sx, sy))

                elif event.type == pg.MOUSEBUTTONUP:
                    if window_active:
                        btn = _PG_BTN_MAP.get(event.button, 1)
                        sx, sy = _scale_coords(*event.pos)
                        client.send_input(MsgType.POINTER_BUTTON,
                                          encode_pointer_button(btn, False, sx, sy))

            if frame_surface and screen:
                win_w, win_h = screen.get_size()
                surf_w, surf_h = frame_surface.get_size()
                scale = min(win_w / surf_w, win_h / surf_h)
                new_w = int(surf_w * scale)
                new_h = int(surf_h * scale)
                scaled = pg.transform.smoothscale(frame_surface, (new_w, new_h))
                screen.fill((0, 0, 0))
                screen.blit(scaled, ((win_w - new_w) // 2, (win_h - new_h) // 2))
                pg.display.flip()

            clock.tick(60)

    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        pg.quit()
        click.echo("  Disconnected")


def _run_client_opencv(host: str, port: int, quality: int = 80,
                       fps: int = 30, fullscreen: bool = False):
    """Fallback client display using OpenCV window."""
    import cv2
    from .client import DeskctrlClient, DISPLAY_NONE

    client = DeskctrlClient(
        host=host, port=port, display_mode=DISPLAY_NONE,
    )
    window_name = f"{__appname__} -- {host}:{port}"

    def on_status(msg):
        click.echo(f"  {msg}")

    client.on_status = on_status

    click.echo(f"  deskctrl client v{__version__}")
    click.echo(f"  Connecting to {host}:{port}...")

    if not client.connect():
        click.echo("  Connection failed", err=True)
        sys.exit(1)

    click.echo("  (OpenCV display only -- no input. Use 'deskctrl gui' or")
    click.echo("   install pygame for mouse/keyboard control)")
    click.echo("  ESC to close, F to toggle fullscreen")

    try:
        while client.state.connected:
            frame = client.get_frame()
            if frame is not None:
                cv2.imshow(window_name, frame)
                key = cv2.waitKey(1) & 0xFF
                if key == 27:  # ESC
                    break
                elif key == ord('f'):
                    cv2.setWindowProperty(
                        window_name, cv2.WND_PROP_FULLSCREEN,
                        cv2.WINDOW_FULLSCREEN if not fullscreen
                        else cv2.WINDOW_NORMAL
                    )
            else:
                time.sleep(0.016)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        client.disconnect()
        click.echo("  Disconnected")


# ???????????????????????????????????????????????????????????????????????????
# extend (extended display mode)
# ???????????????????????????????????????????????????????????????????????????

_BACK_EDGE = {"right": "left", "left": "right", "top": "bottom", "bottom": "top"}


def _build_extend_client(host, port, on_frame, on_resolution, on_status):
    """Create a DeskctrlClient in display-less mode for extended display."""
    from .client import DeskctrlClient, DISPLAY_NONE
    client = DeskctrlClient(host=host, port=port, display_mode=DISPLAY_NONE)
    client.on_frame = on_frame
    client.on_resolution = on_resolution
    client.on_status = on_status
    return client


def _run_extended_video(host, port, direction, quality, fps, loc_left, loc_top, loc_w, loc_h):
    """Fullscreen remote desktop window — mouse in/out freely."""
    import pygame as pg

    from .protocol import (
        MsgType, encode_pointer_move, encode_pointer_button,
        encode_key_event, encode_scroll,
    )

    pg.init()
    # Get display info for proper sizing
    info = pg.display.Info()
    disp_w, disp_h = info.current_w, info.current_h
    pg.display.set_caption(f"Extended -- {host}:{port}")
    screen = pg.display.set_mode((disp_w, disp_h), pg.FULLSCREEN | pg.SCALED)
    pg.event.set_grab(False)
    pg.mouse.set_visible(True)
    win_w, win_h = screen.get_size()

    clock = pg.time.Clock()
    running = True
    frame_surface = None
    server_w, server_h = 0, 0
    input_active = False  # only send input when mouse is inside

    def on_status(msg):
        click.echo(f"  {msg}")

    def on_frame(frame):
        nonlocal frame_surface, server_w, server_h
        h, w = frame.shape[:2]
        server_w, server_h = w, h
        frame_rgb = frame[..., ::-1]
        frame_surface = pg.surfarray.make_surface(frame_rgb.swapaxes(0, 1))

    def on_resolution(w, h):
        nonlocal server_w, server_h
        server_w, server_h = w, h

    client = _build_extend_client(host, port, on_frame, on_resolution, on_status)

    def _scale(px, py):
        if not server_w or not server_h:
            return px, py
        return (px * server_w / win_w, py * server_h / win_h)

    click.echo(f"  Extended display -- {host}:{port} [{direction}]")
    click.echo(f"  Window: {win_w}x{win_h} at ({win_x}, {loc_top})")
    click.echo("  Move mouse in/out freely. ESC to close.")

    if not client.connect():
        pg.quit()
        return

    try:
        while running and client.state.connected:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    running = False
                elif event.type == pg.KEYDOWN:
                    if event.key == pg.K_ESCAPE:
                        running = False
                    elif input_active:
                        keysym = _pygame_keysym(event.key)
                        if keysym:
                            client.send_input(MsgType.KEY_EVENT,
                                              encode_key_event(*keysym, True))
                elif event.type == pg.KEYUP:
                    if input_active:
                        keysym = _pygame_keysym(event.key)
                        if keysym:
                            client.send_input(MsgType.KEY_EVENT,
                                              encode_key_event(*keysym, False))

                elif event.type == pg.MOUSEMOTION:
                    if input_active:
                        sx, sy = _scale(*event.pos)
                        client.send_input(MsgType.POINTER_MOVE,
                                          encode_pointer_move(sx, sy))

                elif event.type == pg.MOUSEBUTTONDOWN:
                    if input_active:
                        btn = _PG_BTN_MAP.get(event.button, 1)
                        sx, sy = _scale(*event.pos)
                        client.send_input(MsgType.POINTER_BUTTON,
                                          encode_pointer_button(btn, True, sx, sy))

                elif event.type == pg.MOUSEBUTTONUP:
                    if input_active:
                        btn = _PG_BTN_MAP.get(event.button, 1)
                        sx, sy = _scale(*event.pos)
                        client.send_input(MsgType.POINTER_BUTTON,
                                          encode_pointer_button(btn, False, sx, sy))

                elif event.type == pg.MOUSEWHEEL:
                    if input_active:
                        client.send_input(MsgType.SCROLL,
                                          encode_scroll(event.x, event.y))

            # Mouse in window → send input. Mouse out → stop.
            input_active = pg.mouse.get_focused()

            # Render frame
            if frame_surface and screen:
                surf_w, surf_h = frame_surface.get_size()
                scale = min(win_w / surf_w, win_h / surf_h)
                new_w = int(surf_w * scale)
                new_h = int(surf_h * scale)
                scaled = pg.transform.scale(frame_surface, (new_w, new_h))
                ox = (win_w - new_w) // 2
                oy = (win_h - new_h) // 2
                screen.fill((0, 0, 0))
                screen.blit(scaled, (ox, oy))
                pg.display.flip()
            else:
                pg.time.wait(16)

            clock.tick(60)

    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        pg.quit()
        click.echo("  Extended display closed")


# ???????????????????????????????????????????????????????????????????????????
# extend command
# ???????????????????????????????????????????????????????????????????????????

@cli.command()
@click.argument("host_port", required=True)
@click.option("--direction", "-d", default="right",
              type=click.Choice(["left", "right", "top", "bottom"]),
              help="Direction of this extended display (back edge = opposite)")
def extend(host_port, direction):
    """Open a fullscreen remote desktop window (extended display).

    Shows the remote desktop in fullscreen. Move the mouse to the
    opposite edge (or press ESC) to return to the local desktop.

    \b
    Examples:
        deskctrl extend 192.168.1.10:5830
        deskctrl extend 192.168.1.10:5830 --direction right
    """
    host, port = _parse_host_port(host_port.strip())
    if host is None:
        sys.exit(1)

    # Get screen geometry via tkinter (always available with Python)
    import tkinter as tk
    tk_root = tk.Tk()
    tk_root.withdraw()
    loc_w = tk_root.winfo_screenwidth()
    loc_h = tk_root.winfo_screenheight()
    tk_root.destroy()

    _run_extended_video(host, port, direction, 80, 30,
                        0, 0, loc_w, loc_h)


# ???????????????????????????????????????????????????????????????????????????
# driver (virtual display driver management)
# ???????????????????????????????????????????????????????????????????????????

@cli.group()
def driver():
    """Manage virtual display drivers (Windows only).

    Creates a virtual monitor so the OS sees an additional display.
    The deskctrl server can capture it with --monitor 2.
    """


@driver.command("status")
def driver_status():
    """Show virtual display driver status."""
    from .virtual_display import status_text
    click.echo(status_text())


@driver.command("install")
@click.option("--force", is_flag=True, help="Re-download even if cached")
def driver_install(force):
    """Download and install the virtual display driver (admin required)."""
    from .virtual_display import download, install
    click.echo("  Downloading usbmmidd virtual display driver...")
    ok = download(force=force) is not None
    if not ok:
        click.echo("  Download failed", err=True)
        sys.exit(1)
    click.echo("  Installing driver (admin elevation may be needed)...")
    if install():
        click.echo("  Driver installed. Now run 'deskctrl driver add-monitor'")
    else:
        click.echo("  Install failed. Try running as Administrator.", err=True)
        sys.exit(1)


@driver.command("add-monitor")
def driver_add():
    """Add a virtual monitor (after driver is installed)."""
    from .virtual_display import add_monitor
    click.echo("  Adding virtual monitor...")
    if add_monitor():
        click.echo("  Virtual monitor added!")
        click.echo("  Go to Windows Display Settings and set Extend mode.")
        click.echo("  Then run: deskctrl serve --monitor 2")
    else:
        click.echo("  Failed to add monitor", err=True)
        sys.exit(1)


@driver.command("remove-monitor")
def driver_remove():
    """Remove all virtual monitors."""
    from .virtual_display import remove_monitor
    click.echo("  Removing virtual monitors...")
    remove_monitor()


@driver.command("uninstall")
def driver_uninstall():
    """Uninstall the virtual display driver."""
    from .virtual_display import uninstall
    click.echo("  Uninstalling driver...")
    uninstall()


# ???????????????????????????????????????????????????????????????????????????
# update
# ???????????????????????????????????????????????????????????????????????????

@cli.command()
@click.option("--force", is_flag=True, help="Re-download even if same version")
def update(force):
    """Check for updates and auto-install the latest version.

    Downloads the right binary for your platform from GitHub releases
    and replaces the current installation.

    \b
    Examples:
        deskctrl update          Check and update if available
        deskctrl update --force  Re-install current version
    """
    from .updater import update as _do_update
    ok = _do_update(force=force)
    sys.exit(0 if ok else 1)


# ???????????????????????????????????????????????????????????????????????????
# Entry point
# ???????????????????????????????????????????????????????????????????????????

if __name__ == "__main__":
    cli()
