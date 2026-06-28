"""
Fullscreen remote desktop display using pygame.

Used by `deskctrl connect`. Captures local keyboard/mouse events and
sends them to the server. Receives JPEG/PNG frames and renders them.

Alt+F4 is intercepted locally and closes the window without forwarding.
"""

from __future__ import annotations
import io
import queue
import threading
import socket

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False

from .keymap import pygame_key_to_keysym, XK_Alt_L, XK_Alt_R, XK_F4
from . import protocol


def run(sock: socket.socket, frame_queue: "queue.Queue[bytes]",
        width: int, height: int,
        send_input_fn) -> None:
    """
    Run the fullscreen pygame display loop.

    Args:
        sock:          Connected TCP socket (used for input events).
        frame_queue:   Queue fed by the receive loop with raw JPEG/PNG bytes.
        width, height: Remote desktop dimensions.
        send_input_fn: Callable(msg_type, payload) — sends an input message.
    """
    if not _PYGAME_AVAILABLE:
        raise RuntimeError("pygame is required for fullscreen display mode.")

    pygame.init()
    pygame.display.set_caption("deskctrl — Remote Desktop")

    info = pygame.display.Info()
    screen = pygame.display.set_mode((info.current_w, info.current_h), pygame.FULLSCREEN)

    clock = pygame.time.Clock()
    surface = pygame.Surface((width, height))

    _alt_down = False
    running = True

    while running:
        # ── Process new frame ───────────────────────────────────────────────
        try:
            while True:
                raw = frame_queue.get_nowait()
                img = pygame.image.load(io.BytesIO(raw))
                surface = pygame.transform.scale(img, screen.get_size())
        except queue.Empty:
            pass

        screen.blit(surface, (0, 0))
        pygame.display.flip()

        # ── Handle events ───────────────────────────────────────────────────
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                keysym = pygame_key_to_keysym(event.key, event.unicode)

                # Track Alt state
                if keysym in (XK_Alt_L, XK_Alt_R):
                    _alt_down = True

                # Intercept Alt+F4 locally
                if _alt_down and keysym == XK_F4:
                    running = False
                    continue

                payload = protocol.encode_key(True, keysym)
                send_input_fn(protocol.MSG_INPUT_KEY, payload)

            elif event.type == pygame.KEYUP:
                keysym = pygame_key_to_keysym(event.key, event.unicode)
                if keysym in (XK_Alt_L, XK_Alt_R):
                    _alt_down = False
                payload = protocol.encode_key(False, keysym)
                send_input_fn(protocol.MSG_INPUT_KEY, payload)

            elif event.type == pygame.MOUSEMOTION:
                # Scale mouse coordinates to remote resolution
                sw, sh = screen.get_size()
                rx = int(event.pos[0] * width / sw)
                ry = int(event.pos[1] * height / sh)
                payload = protocol.encode_mouse_move(rx, ry)
                send_input_fn(protocol.MSG_INPUT_MOUSE, payload)

            elif event.type == pygame.MOUSEBUTTONDOWN:
                sw, sh = screen.get_size()
                rx = int(event.pos[0] * width / sw)
                ry = int(event.pos[1] * height / sh)
                payload = protocol.encode_mouse_button(
                    protocol.MOUSE_PRESS, rx, ry, event.button)
                send_input_fn(protocol.MSG_INPUT_MOUSE, payload)

            elif event.type == pygame.MOUSEBUTTONUP:
                sw, sh = screen.get_size()
                rx = int(event.pos[0] * width / sw)
                ry = int(event.pos[1] * height / sh)
                payload = protocol.encode_mouse_button(
                    protocol.MOUSE_RELEASE, rx, ry, event.button)
                send_input_fn(protocol.MSG_INPUT_MOUSE, payload)

            elif event.type == pygame.MOUSEWHEEL:
                payload = protocol.encode_mouse_scroll(event.x, event.y)
                send_input_fn(protocol.MSG_INPUT_MOUSE, payload)

        clock.tick(60)

    pygame.quit()
