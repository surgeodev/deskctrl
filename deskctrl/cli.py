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
@click.option("--monitor", default=1, help="Monitor index to capture",
              type=int, show_default=True)
@click.option("--nowindow", is_flag=True, default=False,
              help="Don't show local preview window (like scrcpy --nowindow)")
@click.option("--discovery/--no-discovery", default=True,
              help="Advertise server via mDNS (LAN auto-discovery)")
def serve(host, port, fps, quality, monitor, nowindow, discovery):
    """Start deskctrl SERVER (the machine being controlled).

    Captures the screen and streams it to connected clients.
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


def _run_client_opencv(host: str, port: int, quality: int = 80,
                       fps: int = 30, fullscreen: bool = False):
    """Fallback client display using OpenCV window."""
    import cv2


def _run_client_pygame(host: str, port: int, quality: int = 80,
                       fps: int = 30, fullscreen: bool = False):
    """Client display using pygame."""
    import pygame as pg

    from .client import DeskctrlClient, DISPLAY_NONE

    pg.init()
    pg.display.set_caption(f"{__appname__} -- {host}:{port}")

    client = DeskctrlClient(
        host=host, port=port, display_mode=DISPLAY_NONE,
    )
    # We handle display ourselves
    screen = None
    clock = pg.time.Clock()
    running = True
    frame_surface = None

    def on_status(msg):
        click.echo(f"  {msg}")

    def on_frame(frame):
        nonlocal frame_surface, screen
        h, w = frame.shape[:2]
        if screen is None:
            if fullscreen:
                screen = pg.display.set_mode((w, h), pg.FULLSCREEN | pg.SCALED)
            else:
                screen = pg.display.set_mode((w, h), pg.RESIZABLE)
        # Convert BGR to RGB for pygame
        frame_rgb = frame[..., ::-1]  # BGR -> RGB
        frame_surface = pg.surfarray.make_surface(frame_rgb.swapaxes(0, 1))

    def on_resolution(w, h):
        nonlocal screen
        if screen is None:
            if fullscreen:
                screen = pg.display.set_mode((w, h), pg.FULLSCREEN | pg.SCALED)
            else:
                screen = pg.display.set_mode((w, h), pg.RESIZABLE)

    client.on_status = on_status
    client.on_frame = on_frame
    client.on_resolution = on_resolution

    click.echo(f"  deskctrl client v{__version__}")
    click.echo(f"  Connecting to {host}:{port}...")

    if not client.connect():
        click.echo("  Connection failed", err=True)
        pg.quit()
        sys.exit(1)

    # Input capture via pynput (handled by client)
    # But we also handle keyboard events in the pygame window

    try:
        while running and client.state.connected:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    running = False
                elif event.type == pg.KEYDOWN:
                    if event.key == pg.K_ESCAPE:
                        running = False
                    elif event.key == pg.K_f:
                        if screen:
                            pg.display.toggle_fullscreen()
                    elif event.key == pg.K_q and \
                            (event.mod & pg.KMOD_CTRL):
                        running = False

            if frame_surface:
                # Scale to fit window
                if screen:
                    win_w, win_h = screen.get_size()
                    surf_w, surf_h = frame_surface.get_size()
                    scale = min(win_w / surf_w, win_h / surf_h)
                    new_w = int(surf_w * scale)
                    new_h = int(surf_h * scale)
                    scaled = pg.transform.smoothscale(
                        frame_surface, (new_w, new_h)
                    )
                    screen.fill((0, 0, 0))
                    x = (win_w - new_w) // 2
                    y = (win_h - new_h) // 2
                    screen.blit(scaled, (x, y))
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
