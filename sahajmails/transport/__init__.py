"""Mail transports.

A transport is anything that can accept a built message and deliver it. The
protocol is deliberately tiny so plugins can add API-based senders (Postmark's
HTTP API, an internal relay) without touching the core — see
:func:`register_transport`.
"""

from __future__ import annotations

from collections.abc import Callable
from email.message import EmailMessage
from typing import Any, Protocol, runtime_checkable

from ..errors import ConfigError

__all__ = [
    "Transport",
    "available_transports",
    "create_transport",
    "register_transport",
]


@runtime_checkable
class Transport(Protocol):
    """Delivers one message at a time.

    Implementations must be safe to use from a single thread. The sender runs
    one transport per worker rather than sharing one across threads.
    """

    name: str

    def open(self) -> None:
        """Establish the connection. Idempotent."""

    def send(self, message: EmailMessage, *, sender: str, recipient: str) -> str:
        """Deliver one message and return its Message-ID.

        Raises:
            SendError: Delivery failed. ``permanent`` distinguishes a bad
                address (never retry) from a temporary refusal (retry).
            TransportError: The connection itself failed.
        """
        ...

    def verify(self) -> None:
        """Check that credentials work, without sending anything."""

    def close(self) -> None:
        """Release the connection. Idempotent."""


TransportFactory = Callable[..., Transport]

_REGISTRY: dict[str, TransportFactory] = {}


def register_transport(name: str, factory: TransportFactory) -> None:
    """Register a transport under ``name``.

    Called by plugins at load time. Re-registering a name replaces it, which is
    how a plugin can deliberately override the built-in SMTP transport.
    """
    _REGISTRY[name.strip().casefold()] = factory


def available_transports() -> list[str]:
    return sorted(_REGISTRY)


def create_transport(name: str, /, **kwargs: Any) -> Transport:
    """Instantiate a registered transport."""
    key = name.strip().casefold()
    factory = _REGISTRY.get(key)
    if factory is None:
        raise ConfigError(
            f"Unknown transport {name!r}.",
            hint="Available: " + ", ".join(available_transports()),
        )
    return factory(**kwargs)


def _register_builtins() -> None:
    from .file import FileTransport
    from .smtp import SmtpTransport

    register_transport("smtp", SmtpTransport)
    register_transport("file", FileTransport)


_register_builtins()
