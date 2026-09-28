"""SMTP transport.

Three things the 1.x code did not do, each of which cost real deliveries:

* **Reconnects.** Gmail drops idle connections after a few minutes. In 1.x that
  raised out of the send loop and abandoned the batch with no record of who had
  already been mailed.
* **Distinguishes 4xx from 5xx.** A 4xx is "try again shortly"; a 5xx means the
  address is dead and retrying it wastes quota and hurts sender reputation. 1.x
  treated every failure identically.
* **Paces sending.** A fixed ``time.sleep(2)`` is not a rate limit — it ignores
  how long the send itself took. This uses a token bucket against the
  provider's documented rate.
"""

from __future__ import annotations

import contextlib
import random
import smtplib
import socket
import ssl
import threading
import time
from email.message import EmailMessage
from functools import lru_cache
from types import TracebackType

from ..errors import AuthenticationError, SendError, TransportError
from ..presets import Security

__all__ = ["RateLimiter", "SmtpTransport"]

# SMTP enhanced status codes that mean "this mailbox will never accept mail".
# Everything else in the 5xx range is treated as permanent too, but these are
# worth naming because they should feed the suppression list.
_HARD_BOUNCE_CODES = frozenset({550, 551, 553, 554})

# 421 is "service unavailable, closing channel" — the connection is gone and
# must be re-established before the next attempt.
_DISCONNECT_CODES = frozenset({421})


@lru_cache(maxsize=1)
def local_hostname() -> str:
    """The name to announce in EHLO.

    Computed here rather than left to ``smtplib``, which calls
    ``socket.getfqdn()`` — a **reverse DNS lookup on every connection**. On a
    machine with slow or absent reverse DNS (corporate networks, VPNs, plenty of
    stock macOS setups) that blocks for tens of seconds per connect; it cost 35
    seconds a connection on the machine this was written on.

    ``gethostname()`` is a local syscall and never touches the network. Servers
    do not validate this value for authenticated submission, so the exact string
    matters far less than not hanging on it.
    """
    name = socket.gethostname()
    if "." in name:
        return name
    try:
        return f"[{socket.gethostbyname(name)}]"
    except OSError:
        # RFC 5321 address literal: always acceptable, never needs a lookup.
        return "[127.0.0.1]"


class RateLimiter:
    """Token bucket with jitter.

    Jitter matters: a perfectly regular send cadence is itself a bot signal, and
    identical inter-message gaps across thousands of messages are trivially
    detectable. A little randomness costs nothing.
    """

    def __init__(self, per_minute: int, *, jitter: float = 0.15) -> None:
        self.per_minute = max(1, per_minute)
        self.jitter = max(0.0, jitter)
        self._interval = 60.0 / self.per_minute
        self._next_at = 0.0
        self._lock = threading.Lock()

    def wait(self) -> float:
        """Block until the next send is allowed. Returns seconds slept."""
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            spread = self._interval * self.jitter
            self._next_at = max(now, self._next_at) + self._interval
            if spread:
                self._next_at += random.uniform(-spread, spread)  # noqa: S311
        if delay > 0:
            time.sleep(delay)
        return delay


class SmtpTransport:
    """A single SMTP connection, kept warm and re-established when dropped."""

    name = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        security: Security | str = Security.STARTTLS,
        timeout: float = 30.0,
        rate_per_minute: int = 30,
        max_reconnects: int = 3,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if not host:
            raise TransportError("No SMTP host configured.", hint="Pick a provider preset.")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.security = Security(security)
        self.timeout = timeout
        self.max_reconnects = max_reconnects
        self.limiter = RateLimiter(rate_per_minute)
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._client: smtplib.SMTP | None = None
        self._reconnects = 0

    # -- lifecycle --------------------------------------------------------

    def open(self) -> None:
        if self._client is not None:
            return
        try:
            client = self._connect()
        except (smtplib.SMTPAuthenticationError, AuthenticationError):
            raise
        except (OSError, smtplib.SMTPException) as exc:
            raise TransportError(
                f"Could not reach {self.host}:{self.port} — {exc}",
                hint=(
                    "Check the host and port, and that your network or firewall "
                    "is not blocking outbound SMTP."
                ),
            ) from exc
        self._client = client

    def _connect(self) -> smtplib.SMTP:
        if self.security is Security.SSL:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self.host,
                self.port,
                timeout=self.timeout,
                context=self._ssl_context,
                local_hostname=local_hostname(),
            )
        else:
            client = smtplib.SMTP(
                self.host, self.port, timeout=self.timeout, local_hostname=local_hostname()
            )
            client.ehlo()
            if self.security is Security.STARTTLS:
                if not client.has_extn("starttls"):
                    client.close()
                    raise TransportError(
                        f"{self.host} does not offer STARTTLS on port {self.port}.",
                        hint="Try port 465 with SSL, or check the provider's documentation.",
                    )
                client.starttls(context=self._ssl_context)
                client.ehlo()

        if self.username:
            try:
                client.login(self.username, self.password)
            except smtplib.SMTPAuthenticationError as exc:
                client.close()
                raise AuthenticationError(
                    f"{self.host} rejected the credentials for {self.username}.",
                    hint=self._auth_hint(),
                ) from exc
            except smtplib.SMTPNotSupportedError as exc:
                client.close()
                raise AuthenticationError(
                    f"{self.host} does not support the authentication we offered.",
                    hint="The server may require OAuth2 or have SMTP AUTH disabled.",
                ) from exc
        return client

    def _auth_hint(self) -> str:
        from ..presets import guess_preset

        preset = guess_preset(self.username)
        if preset and preset.setup_hint:
            return preset.setup_hint
        return (
            "Most providers reject your normal account password over SMTP. "
            "Create an app password and use that instead."
        )

    def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.quit()
        except (smtplib.SMTPException, OSError):
            # A server that already hung up is not an error worth surfacing.
            with contextlib.suppress(OSError):
                client.close()

    def __enter__(self) -> SmtpTransport:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- operations -------------------------------------------------------

    def verify(self) -> None:
        """Connect and authenticate, then hang up. Sends no mail."""
        self.close()
        self.open()
        client = self._client
        if client is not None:
            try:
                client.noop()
            except (smtplib.SMTPException, OSError) as exc:
                raise TransportError(f"Connection check failed: {exc}") from exc
        self.close()

    def send(self, message: EmailMessage, *, sender: str, recipient: str) -> str:
        """Deliver one message, reconnecting once if the link has dropped."""
        self.limiter.wait()
        self.open()

        try:
            return self._send_once(message, sender, recipient)
        except smtplib.SMTPServerDisconnected:
            # Expected on long runs: providers close idle connections. Rebuild
            # and retry exactly once, so a genuinely broken link still fails.
            self._reconnect()
            try:
                return self._send_once(message, sender, recipient)
            except (smtplib.SMTPException, OSError) as exc:
                raise self._as_send_error(exc, recipient) from exc
        except (smtplib.SMTPException, OSError) as exc:
            error = self._as_send_error(exc, recipient)
            if (
                isinstance(exc, smtplib.SMTPResponseException)
                and exc.smtp_code in _DISCONNECT_CODES
            ):
                self._reconnect()
            raise error from exc

    def _send_once(self, message: EmailMessage, sender: str, recipient: str) -> str:
        client = self._client
        if client is None:  # pragma: no cover - open() guarantees this
            raise TransportError("SMTP connection is not open.")

        # send_message handles SMTPUTF8 negotiation and Bcc stripping for us.
        client.send_message(message, from_addr=sender, to_addrs=[recipient])
        message_id = message.get("Message-ID", "")
        return str(message_id)

    def _reconnect(self) -> None:
        self._reconnects += 1
        if self._reconnects > self.max_reconnects:
            raise TransportError(
                f"Lost the connection to {self.host} {self._reconnects} times.",
                hint="The provider may be rate-limiting you. Try a slower send rate.",
            )
        self.close()
        # Brief backoff: reconnecting instantly into a throttle just burns the
        # remaining allowance.
        time.sleep(min(2.0 * self._reconnects, 10.0))
        self.open()

    # -- error classification ---------------------------------------------

    @staticmethod
    def _as_send_error(exc: Exception, recipient: str) -> SendError:
        """Map an smtplib failure onto retry/don't-retry."""
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return SendError(
                "Authentication was rejected mid-run.",
                recipient=recipient,
                permanent=True,
                hint="The password may have been revoked while sending.",
            )

        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            code, raw = next(iter(exc.recipients.values()), (550, b""))
            detail = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            permanent = code >= 500
            return SendError(
                f"Rejected: {detail.strip() or code}",
                recipient=recipient,
                permanent=permanent,
                hint=(
                    "This address does not exist. It will be suppressed."
                    if code in _HARD_BOUNCE_CODES
                    else None
                ),
            )

        if isinstance(exc, smtplib.SMTPSenderRefused):
            return SendError(
                f"The server refused the sender address: {exc.smtp_error!r}",
                recipient=recipient,
                permanent=True,
                hint="Your From address must match the account you signed in with.",
            )

        if isinstance(exc, smtplib.SMTPResponseException):
            detail = (
                exc.smtp_error.decode(errors="replace")
                if isinstance(exc.smtp_error, bytes)
                else str(exc.smtp_error)
            )
            permanent = exc.smtp_code >= 500
            hint = None
            if exc.smtp_code in {421, 450, 451, 452}:
                hint = "The provider is throttling you. A slower send rate usually fixes this."
            return SendError(
                f"{exc.smtp_code} {detail.strip()}",
                recipient=recipient,
                permanent=permanent,
                hint=hint,
            )

        if isinstance(exc, socket.timeout | TimeoutError):
            return SendError("Timed out waiting for the server.", recipient=recipient)

        return SendError(str(exc) or exc.__class__.__name__, recipient=recipient)
