"""Starting the server from ``sahajmails run``."""

from __future__ import annotations

import socket
import threading
import webbrowser

import uvicorn

from ..errors import ConfigError
from .app import create_app
from .security import SecurityConfig, is_loopback

__all__ = ["find_free_port", "serve"]


def find_free_port(preferred: int, host: str = "127.0.0.1", tries: int = 20) -> int:
    """Return ``preferred`` if it is free, otherwise the next port that is.

    Refusing to start because something else owns port 8000 would be a poor
    greeting for a tool whose whole promise is that it just works.
    """
    for offset in range(tries):
        candidate = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise ConfigError(
        f"No free port between {preferred} and {preferred + tries - 1}.",
        hint="Pass --port with something else.",
    )


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
    allow_remote: bool = False,
    reload: bool = False,
) -> None:
    """Run the web UI until interrupted."""
    if not is_loopback(host) and not allow_remote:
        raise ConfigError(
            f"Refusing to bind {host}: that would expose your mail credentials.",
            hint=(
                "This tool has no user accounts and can send mail as you. "
                "If you really mean it, add --allow-remote."
            ),
        )

    actual_port = find_free_port(port, host if is_loopback(host) else "0.0.0.0")  # noqa: S104
    if actual_port != port:
        print(f"Port {port} is busy, using {actual_port} instead.")

    security = SecurityConfig(host=host, port=actual_port, allow_remote=allow_remote)
    app = create_app(security=security)

    url = security.url()
    print()
    print("  SahajMails is running.")
    print(f"  Open  {url}")
    print()
    if allow_remote and not is_loopback(host):
        print("  ⚠  Bound to a non-local address. Anyone who can reach this port")
        print("     and knows the token above can send mail as you.")
        print()
    print("  Press Ctrl-C to stop.")
    print()

    if open_browser:
        # Delayed so the server is accepting connections before the tab loads.
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        app,
        host=host,
        port=actual_port,
        log_level="warning",
        reload=reload,
        access_log=False,
    )
