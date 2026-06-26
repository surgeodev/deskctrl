"""LAN auto-discovery for deskctrl servers using Zeroconf (Bonjour/mDNS)."""

import logging
import socket
import threading
import time
from typing import Optional, Callable

from . import __appname__

log = logging.getLogger(__name__)

# Zeroconf service type
SERVICE_TYPE = f"_{__appname__}._tcp.local."


class DiscoveryService:
    """
    Advertise a deskctrl server on the LAN via mDNS so clients
    can discover it without manual IP/port entry.
    """

    def __init__(self, port: int = 5830, host: str = "0.0.0.0",
                 name: Optional[str] = None):
        self.port = port
        self.host = host
        self.name = name or socket.gethostname()
        self._zeroconf = None
        self._service_info = None
        self._running = False

    def start(self) -> bool:
        """Start advertising this server on the LAN."""
        try:
            from zeroconf import Zeroconf, ServiceInfo

            # Get local IP for the service info
            local_ip = self._get_local_ip()

            self._service_info = ServiceInfo(
                type_=SERVICE_TYPE,
                name=f"{self.name}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(local_ip)] if local_ip else [],
                port=self.port,
                properties={"version": "0.1.0"},
            )
            self._zeroconf = Zeroconf()
            self._zeroconf.register_service(self._service_info)
            self._running = True
            log.info(f"mDNS service registered: {self.name} on port {self.port}")
            return True
        except ImportError:
            log.warning("zeroconf not installed -- LAN discovery disabled")
            return False
        except Exception as e:
            log.warning(f"Failed to register mDNS service: {e}")
            return False

    def stop(self):
        """Stop advertising."""
        self._running = False
        if self._zeroconf and self._service_info:
            try:
                self._zeroconf.unregister_service(self._service_info)
                self._zeroconf.close()
            except Exception:
                pass
            self._zeroconf = None
            self._service_info = None

    def _get_local_ip(self) -> Optional[str]:
        """Get the local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"


class DiscoveryBrowser:
    """
    Browse the LAN for available deskctrl servers.

    Usage:
        browser = DiscoveryBrowser()
        browser.on_service_found = lambda addr, port, name: print(f"Found {name}")
        browser.start()
        # ... wait for discoveries ...
        servers = browser.get_services()
        browser.stop()
    """

    def __init__(self, timeout: float = 3.0):
        self.timeout = timeout
        self._zeroconf = None
        self._browser = None
        self._services: list[dict] = []
        self._lock = threading.Lock()
        self._running = False

        # Callbacks
        self.on_service_found: Optional[Callable] = None
        self.on_service_lost: Optional[Callable] = None

    def start(self) -> bool:
        """Start browsing for deskctrl servers."""
        try:
            from zeroconf import Zeroconf, ServiceBrowser, ServiceStateChange

            self._zeroconf = Zeroconf()

            def on_change(zeroconf, service_type, name, state_change):
                if state_change == ServiceStateChange.Added:
                    info = zeroconf.get_service_info(service_type, name)
                    if info:
                        addresses = []
                        for addr in info.addresses:
                            addresses.append(socket.inet_ntoa(addr))
                        entry = {
                            "name": name.replace(f".{SERVICE_TYPE}", ""),
                            "host": addresses[0] if addresses else "unknown",
                            "port": info.port,
                            "addresses": addresses,
                            "properties": dict(info.properties),
                        }
                        with self._lock:
                            # Avoid duplicates
                            for existing in self._services:
                                if existing["host"] == entry["host"] and \
                                   existing["port"] == entry["port"]:
                                    break
                            else:
                                self._services.append(entry)
                                log.info(f"Discovered deskctrl server: "
                                         f"{entry['name']} at {entry['host']}:{entry['port']}")
                                if self.on_service_found:
                                    self.on_service_found(entry)
                elif state_change == ServiceStateChange.Removed:
                    with self._lock:
                        self._services = [
                            s for s in self._services
                            if name not in s["name"]
                        ]
                    if self.on_service_lost:
                        self.on_service_lost(name)

            self._browser = ServiceBrowser(
                self._zeroconf, SERVICE_TYPE, handlers=[on_change]
            )
            self._running = True
            return True

        except ImportError:
            log.warning("zeroconf not installed -- LAN discovery disabled")
            return False
        except Exception as e:
            log.warning(f"Failed to start discovery browser: {e}")
            return False

    def stop(self):
        """Stop browsing."""
        self._running = False
        if self._zeroconf:
            try:
                self._zeroconf.close()
            except Exception:
                pass
            self._zeroconf = None
            self._browser = None

    def get_services(self) -> list[dict]:
        """Get discovered services."""
        with self._lock:
            return list(self._services)

    def wait_for_services(self, min_services: int = 1,
                          timeout: float = 5.0) -> list[dict]:
        """Block until at least `min_services` are found or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.get_services()) >= min_services:
                break
            time.sleep(0.2)
        return self.get_services()
