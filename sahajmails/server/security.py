"""Guarding a local server that holds mail credentials.

Running an HTTP server is the real cost of moving off Streamlit, and it deserves
proper treatment. This process can send mail as you, so anything that can reach
it can send mail as you.

Three defences, in order of how easily they are overlooked:

1. **A session token.** Without one, every other process on the machine — and
   every page in your browser — can drive the API on ``localhost``.
2. **A Host-header allowlist.** The non-obvious one. Same-origin policy does not
   protect a localhost server from DNS rebinding: a malicious site can point its
   own domain at ``127.0.0.1`` and its JavaScript then talks to this server as a
   same-origin peer. Checking ``Host`` is what stops it.
3. **Binding to loopback**, with anything else requiring an explicit flag.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
from dataclasses import dataclass, field

from fastapi import Request, Response
from fastapi.responses import JSONResponse

__all__ = [
    "COOKIE_NAME",
    "CSRF_HEADER",
    "SecurityConfig",
    "SecurityMiddleware",
    "is_loopback",
]

COOKIE_NAME = "sahajmails_session"
CSRF_HEADER = "x-sahajmails"
TOKEN_QUERY = "token"  # noqa: S105 - a query-parameter name, not a secret

# Requests to these never need a token: the shell has to load before it can
# authenticate, and the health check is deliberately unauthenticated.
_PUBLIC_PATHS = frozenset({"/", "/index.html", "/health", "/favicon.ico"})
_PUBLIC_PREFIXES = ("/static/",)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _token_from_env() -> str:
    """Allow a fixed token via ``SAHAJMAILS_TOKEN``.

    Useful for scripting the API and for tests. Ignored unless it is long
    enough to be worth anything — a two-character "token" is worse than none,
    because it looks like security.
    """
    candidate = os.environ.get("SAHAJMAILS_TOKEN", "").strip()
    return candidate if len(candidate) >= 16 else ""


def is_loopback(host: str) -> bool:
    """Whether ``host`` refers to this machine only."""
    bare = host.split("%")[0].strip("[]")
    if bare in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


@dataclass(slots=True)
class SecurityConfig:
    """Per-process security state."""

    token: str = field(default_factory=lambda: _token_from_env() or secrets.token_urlsafe(32))
    host: str = "127.0.0.1"
    port: int = 8000
    allow_remote: bool = False

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """Host header values this server will answer to.

        Anything else is a rebinding attempt or a misconfiguration; both deserve
        the same refusal.
        """
        names = {
            f"localhost:{self.port}",
            f"127.0.0.1:{self.port}",
            f"[::1]:{self.port}",
            "localhost",
            "127.0.0.1",
        }
        if self.allow_remote:
            names.add(f"{self.host}:{self.port}")
            names.add(self.host)
        return frozenset(names)

    def url(self, *, with_token: bool = True) -> str:
        display = "localhost" if is_loopback(self.host) else self.host
        base = f"http://{display}:{self.port}/"
        return f"{base}?{TOKEN_QUERY}={self.token}" if with_token else base

    def matches(self, candidate: str) -> bool:
        """Constant-time token comparison."""
        return bool(candidate) and hmac.compare_digest(candidate, self.token)


class SecurityMiddleware:
    """Pure-ASGI middleware enforcing host, token and CSRF rules."""

    def __init__(self, app: object, config: SecurityConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: dict[str, object], receive: object, send: object) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)  # type: ignore[operator]
            return

        request = Request(scope, receive)  # type: ignore[arg-type]
        rejection = self._reject(request)
        if rejection is not None:
            await rejection(scope, receive, send)  # type: ignore[arg-type]
            return

        # A valid token in the query string is exchanged for a cookie, so the
        # secret stops travelling in URLs (which land in history and logs).
        token_in_query = request.query_params.get(TOKEN_QUERY, "")
        set_cookie = bool(token_in_query) and self.config.matches(token_in_query)

        if not set_cookie:
            await self.app(scope, receive, send)  # type: ignore[operator]
            return

        async def send_with_cookie(message: dict[str, object]) -> None:
            if message.get("type") == "http.response.start":
                raw = message.get("headers") or []
                headers = list(raw) if isinstance(raw, list) else []
                cookie = (
                    f"{COOKIE_NAME}={self.config.token}; Path=/; HttpOnly; "
                    f"SameSite=Strict; Max-Age=604800"
                )
                headers.append((b"set-cookie", cookie.encode()))
                message = {**message, "headers": headers}
            await send(message)  # type: ignore[operator]

        await self.app(scope, receive, send_with_cookie)  # type: ignore[operator]

    # -- checks ------------------------------------------------------------

    def _reject(self, request: Request) -> Response | None:
        host = request.headers.get("host", "")
        if host.casefold() not in {h.casefold() for h in self.config.allowed_hosts}:
            # DNS rebinding, or someone pointing a hostname at this port.
            # Same {error, hint} shape the domain exception handler uses, so
            # the UI can render every failure the same way.
            return JSONResponse(
                {
                    "error": f"This server only answers to localhost, not {host!r}.",
                    "hint": "If you meant to expose it, restart with --allow-remote.",
                },
                status_code=400,
            )

        origin = request.headers.get("origin")
        if origin and not self._same_origin(origin, host):
            return JSONResponse(
                {
                    "error": "Cross-origin requests are not allowed.",
                    "hint": f"This request claimed to come from {origin}.",
                },
                status_code=403,
            )

        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            return None

        if not self._authenticated(request):
            return JSONResponse(
                {
                    "error": "This page needs the session token.",
                    "hint": (
                        "Open the link printed in your terminal when the server "
                        "started — it carries a one-time token. Bookmarking the bare "
                        "address will not work."
                    ),
                },
                status_code=401,
            )

        # CSRF: a custom header cannot be set by a plain cross-site form post,
        # and any cross-origin fetch that could set it is already blocked above.
        if request.method not in _SAFE_METHODS and not request.headers.get(CSRF_HEADER):
            return JSONResponse(
                {
                    "error": "Request rejected for safety.",
                    "hint": f"Missing the {CSRF_HEADER} header.",
                },
                status_code=403,
            )

        return None

    def _authenticated(self, request: Request) -> bool:
        cookie = request.cookies.get(COOKIE_NAME, "")
        if self.config.matches(cookie):
            return True
        query = request.query_params.get(TOKEN_QUERY, "")
        if self.config.matches(query):
            return True
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            return self.config.matches(header[7:].strip())
        return False

    @staticmethod
    def _same_origin(origin: str, host: str) -> bool:
        origin_host = origin.split("://", 1)[-1]
        return origin_host.casefold() == host.casefold()
