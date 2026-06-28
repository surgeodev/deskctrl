"""
CLI entry point using click.

Commands:
  deskctrl connect HOST:PORT   -- full remote desktop (fullscreen, grabs input)
  deskctrl extend  HOST:PORT   -- extended display mode (side panel, no grab)
  deskctrl serve               -- start server
  deskctrl scan                -- scan LAN for servers
  deskctrl headless HOST:PORT  -- connect without display (control only)
  deskctrl hdmi                -- list HDMI capture devices
  deskctrl gui                 -- launch graphical interface
  deskctrl monitor             -- monitor control mode (Barrier-like)
  deskctrl driver install|remove|status  -- virtual display driver management
  deskctrl update              -- self-update
"""

from __future__ import annotations
import os
import sys
import time
import logging
import click

from . import __version__

log = logging.getLogger(__name__)


def _parse_host_port(addr: str) -> tuple[str, int]:
    if ":" in addr:
        host, port_str = addr.rsplit(":", 1)
        return host, int(port_str)
    return addr, 5900


@click.group()
@click.version_option(version=__version__, prog_name="deskctrl")
def main():
    """deskctrl — cross-platform remote desktop tool (scrcpy for PCs)."""
    pass


# ── connect ─────────────────────────────────────────────────────────────────

@main.command("connect")
@click.argument("address")
@click.option("--monitor", "-m", default=0, show_default=True,
              help="Which remote monitor to display (0 = primary).")
@click.option("--fullscreen", is_flag=True, default=False,
              help="Start in fullscreen mode.")
def cmd_connect(address: str, monitor: int, fullscreen: bool):
    """Connect and view/control remote desktop in fullscreen."""
    host, port = _parse_host_port(address)
    click.echo(f"Connecting to {host}:{port} (monitor={monitor})…")
    from .client import Client
    c = Client(host, port, monitor=monitor, mode="connect")
    c.connect_and_run()


# ── extend ───────────────────────────────────────────────────────────────────

@main.command("extend")
@click.argument("address")
@click.option("--monitor", "-m", default=0, show_default=True,
              help="Which remote monitor to display (0 = primary).")
@click.option("--width", default=480, show_default=True,
              help="Width of the side panel window in pixels.")
@click.option("--height", default=270, show_default=True,
              help="Height of the side panel window in pixels.")
def cmd_extend(address: str, monitor: int, width: int, height: int):
    """Connect in extended display mode (side panel, no input grab)."""
    host, port = _parse_host_port(address)
    click.echo(f"Extending to {host}:{port} (monitor={monitor}, panel={width}x{height})…")
    from .client import Client
    import types
    c = Client(host, port, monitor=monitor, mode="extend")
    def _run_display_patched(self):
        from .display_extend import run as run_extend
        run_extend(
            frame_queue=self._frame_queue,
            remote_w=self._remote_w,
            remote_h=self._remote_h,
            panel_w=width,
            panel_h=height,
            send_input_fn=self._send,
        )
    c._run_display = types.MethodType(_run_display_patched, c)
    c.connect_and_run()


# ── serve ────────────────────────────────────────────────────────────────────

@main.command("serve")
@click.option("--host", default="0.0.0.0", show_default=True,
              help="Interface to listen on.")
@click.option("--port", "-p", default=5900, show_default=True,
              help="TCP port to listen on.")
@click.option("--monitor", "-m", default=0, show_default=True,
              help="Local monitor index to capture (0 = primary).")
@click.option("--virtual", is_flag=True, default=False,
              help="Auto-install + activate virtual display before serving (Windows).")
@click.option("--quality", default=60, show_default=True,
              help="JPEG quality 1-100.")
@click.option("--fps", default=30, show_default=True,
              help="Target frames per second.")
@click.option("--discovery/--no-discovery", default=True,
              help="Advertise server via mDNS (LAN auto-discovery).")
@click.option("--monitor-mode", is_flag=True, default=False,
              help="Monitor-control protocol (input-only, for `deskctrl monitor` clients).")
def cmd_serve(host: str, port: int, monitor: int, virtual: bool,
              quality: int, fps: int, discovery: bool,
              monitor_mode: bool):
    """Start the deskctrl server (the machine being controlled).

    Captures the screen and streams it to connected clients.

    Use --monitor-mode for the input-only protocol used by `deskctrl monitor`
    (Barrier/Synergy-like cursor sharing, no video streaming).
    """
    from .server import serve

    # Optional discovery
    discovery_service = None
    if discovery:
        try:
            from .discovery import DiscoveryService
            discovery_service = DiscoveryService(port=port)
            discovery_service.start()
        except Exception as e:
            log.warning(f"Discovery not available: {e}")

    serve(host=host, port=port, monitor=monitor, virtual=virtual,
          monitor_mode=monitor_mode)

    if discovery_service:
        discovery_service.stop()


# ── scan ─────────────────────────────────────────────────────────────────────

@main.command("scan")
@click.option("--timeout", default=3.0, show_default=True,
              help="Scan duration in seconds.", type=float)
def cmd_scan(timeout: float):
    """Scan the LAN for running deskctrl servers."""
    click.echo(f"Scanning LAN for deskctrl servers ({timeout}s)...")
    try:
        from .discovery import DiscoveryBrowser
        browser = DiscoveryBrowser()
        browser.start()
        services = browser.wait_for_services(min_services=1, timeout=timeout)
        browser.stop()
        if services:
            click.echo(f"\nFound {len(services)} server(s):")
            for svc in services:
                click.echo(f"  {svc['name']:20s}  {svc['host']:15s}:{svc['port']}")
        else:
            click.echo("  No servers found.")
    except Exception as e:
        click.echo(f"  Scan error: {e}", err=True)
        sys.exit(1)


# ── headless ─────────────────────────────────────────────────────────────────

@main.command("headless")
@click.argument("address", required=False, default=None)
@click.option("--port", "-p", default=5900, show_default=True,
              help="Server TCP port.")
@click.option("--auto", is_flag=True, default=False,
              help="Auto-discover server on LAN.")
def cmd_headless(address: str, port: int, auto: bool):
    """Connect in headless mode (no display window, control only)."""
    if not address and not auto:
        click.echo("  Error: provide HOST or use --auto", err=True)
        sys.exit(1)

    if auto:
        from .discovery import DiscoveryBrowser
        browser = DiscoveryBrowser()
        browser.start()
        services = browser.wait_for_services(min_services=1, timeout=3.0)
        browser.stop()
        if not services:
            click.echo("  No servers found on LAN.", err=True)
            sys.exit(1)
        host = services[0]["host"]
        port = services[0]["port"]
    else:
        host, parsed_port = _parse_host_port(address)
        if parsed_port is not None:
            port = parsed_port

    click.echo(f"Connecting headless to {host}:{port}...")
    click.echo("  (No display — control only. Press Ctrl+C to disconnect.)")

    from .client import Client
    c = Client(host, port, monitor=0, mode="connect")

    # Override _run_display to do nothing (headless)
    import types
    def _no_display(self):
        import threading
        while self._running:
            threading.Event().wait(1)
    c._run_display = types.MethodType(_no_display, c)

    c.connect_and_run()


# ── hdmi ─────────────────────────────────────────────────────────────────────

@main.command("hdmi")
def cmd_hdmi():
    """List available HDMI/USB capture devices."""
    from .hdmi_capture import list_capture_devices
    devices = list_capture_devices()
    if devices:
        click.echo("Available HDMI/Video capture devices:")
        for i, dev in enumerate(devices):
            click.echo(f"  [{i}] {dev.get('name', '?')}")
            click.echo(f"       Path: {dev.get('path', '?')}")
            click.echo(f"       Type: {dev.get('type', '?')}")
    else:
        click.echo("No HDMI capture devices found.")


# ── gui ──────────────────────────────────────────────────────────────────────

@main.command("gui")
@click.option("--host", default=None, help="Server host to auto-connect")
@click.option("--port", default=5830, help="Server port", type=int)
def cmd_gui(host, port):
    """Launch the deskctrl graphical interface (PyQt6)."""
    from .gui import launch_gui
    sys.exit(launch_gui())


# ── monitor ──────────────────────────────────────────────────────────────────

@main.command("monitor")
@click.option("--config", default=None,
              help="Path to monitor layout JSON file.")
@click.option("--add", multiple=True, default=[],
              help="Add server: DIRECTION=HOST:PORT (e.g. right=192.168.1.100).")
@click.option("--remove", multiple=True, default=[],
              help="Remove server by direction (e.g. right).")
@click.option("--no-start", is_flag=True, default=False,
              help="Only update config, don't start.")
@click.option("--list", "list_only", is_flag=True, default=False,
              help="Show current layout and exit.")
@click.option("--margin", default=None, type=int,
              help="Activation margin in pixels.")
def cmd_monitor(config, add, remove, no_start, list_only, margin):
    """Monitor Control mode — seamless cursor across machines (Barrier-like).

    Configure where your other machines' screens are relative to yours.
    Move the mouse to the edge -> it takes control of the remote machine.
    Pure input forwarding (no video) = zero latency.

    Examples:
        deskctrl monitor --add right=192.168.1.100
        deskctrl monitor --add left=10.0.0.5:5830 --list
    """
    from .monitor_control import MonitorLayout, MonitorControlEngine, DEFAULT_CONFIG_FILE

    cfg_path = config or DEFAULT_CONFIG_FILE

    if os.path.exists(cfg_path):
        layout = MonitorLayout.from_file(cfg_path)
    else:
        layout = MonitorLayout()

    if margin is not None:
        layout.activation_margin = margin

    for add_str in add:
        parts = add_str.split("=", 1)
        if len(parts) != 2:
            click.echo(f"Error: --add expects DIRECTION=HOST:PORT, got '{add_str}'", err=True)
            sys.exit(1)
        direction = parts[0].strip()
        host_part = parts[1].strip()
        if ":" in host_part:
            host_val, port_str = host_part.rsplit(":", 1)
            try:
                port_val = int(port_str)
            except ValueError:
                click.echo(f"Error: invalid port '{port_str}'", err=True)
                sys.exit(1)
        else:
            host_val = host_part
            port_val = 5830  # monitor protocol default port
        if direction not in ("left", "right", "top", "bottom"):
            click.echo(f"Error: direction must be left|right|top|bottom", err=True)
            sys.exit(1)
        layout.add_server(direction, host_val, port_val)
        click.echo(f"  Added {direction:>6s} -> {host_val}:{port_val}")

    for direction in remove:
        if direction not in ("left", "right", "top", "bottom"):
            click.echo(f"Error: invalid direction '{direction}'", err=True)
            sys.exit(1)
        removed = layout.get_server(direction)
        if removed:
            layout.remove_server(direction)
            click.echo(f"  Removed {direction:>6s} -> {removed.name}")
        else:
            click.echo(f"  No server configured for {direction}")

    layout.to_file(cfg_path)

    if list_only:
        click.echo(f"\nMonitor Layout ({cfg_path}):")
        click.echo(f"  Activation margin: {layout.activation_margin}px")
        if layout.servers:
            click.echo(f"  Servers:")
            for s in layout.servers:
                click.echo(f"    {s.direction:>6s} -> {s.name} ({s.host}:{s.port})")
        else:
            click.echo(f"  No servers configured.")
        return

    if no_start or not layout.servers:
        if not layout.servers:
            click.echo("  No servers configured. Use --add to add one.")
        else:
            click.echo(f"  Configuration saved.")
        return

    engine = MonitorControlEngine(layout)
    engine.on_status = lambda msg: click.echo(f"  {msg}")

    click.echo(f"\n  Monitor Control active — move mouse to edge to take control")
    click.echo(f"  ESC to release — Ctrl+C to quit")

    if engine.start():
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            click.echo("\n  Shutting down...")
        finally:
            engine.stop()


# ── driver ───────────────────────────────────────────────────────────────────

@main.group("driver")
def cmd_driver():
    """Manage the virtual display driver (Windows only)."""
    pass


@cmd_driver.command("install")
def driver_install():
    """Install the usbmmidd virtual display driver."""
    from .virtual_display import install
    try:
        install()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cmd_driver.command("remove")
def driver_remove():
    """Remove the usbmmidd virtual display driver."""
    from .virtual_display import remove
    try:
        remove()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cmd_driver.command("status")
def driver_status():
    """Show the usbmmidd virtual display driver status."""
    from .virtual_display import status
    try:
        status()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ── update ───────────────────────────────────────────────────────────────────

@main.command("update")
@click.option("--force", is_flag=True, help="Re-download even if same version.")
def cmd_update(force: bool):
    """Check for updates and auto-install the latest version."""
    from .updater import update as do_update
    ok = do_update(force=force)
    sys.exit(0 if ok else 1)


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
