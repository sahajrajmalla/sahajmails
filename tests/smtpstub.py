"""A minimal SMTP server for tests.

Purpose-built rather than using ``aiosmtpd``, for three reasons: aiosmtpd 1.4.6
does not start at all on Python 3.14 (which the CI matrix covers), it is one
more dependency to keep current, and precise control over *how* a server
misbehaves — a 4xx here, a dropped socket there — is the whole point of these
tests.

Implements just enough of RFC 5321 for ``smtplib`` to be happy: EHLO, AUTH
LOGIN/PLAIN, MAIL, RCPT, DATA, RSET, NOOP, QUIT.
"""

from __future__ import annotations

import base64
import contextlib
import email
import socket
import socketserver
import threading
from email.message import Message
from types import TracebackType

__all__ = ["SMTPStub"]

_MAX_LINE = 65536


class _Handler(socketserver.StreamRequestHandler):
    server: SMTPStub

    def handle(self) -> None:
        stub = self.server
        self._authenticated = not stub.require_auth
        self._sender = ""
        self._recipients: list[str] = []

        self._send(f"220 {stub.hostname} SahajMails test stub")

        while True:
            try:
                raw = self.rfile.readline(_MAX_LINE)
            except (OSError, ValueError):
                return
            if not raw:
                return

            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            command, _, argument = line.partition(" ")
            command = command.upper()

            try:
                if command in {"EHLO", "HELO"}:
                    self._hello(command)
                elif command == "AUTH":
                    self._auth(argument)
                elif command == "MAIL":
                    self._mail(argument)
                elif command == "RCPT":
                    self._rcpt(argument)
                elif command == "DATA":
                    if self._data():
                        return
                elif command == "RSET":
                    self._sender, self._recipients = "", []
                    self._send("250 OK")
                elif command == "NOOP":
                    self._send("250 OK")
                elif command == "QUIT":
                    self._send("221 Bye")
                    return
                else:
                    self._send("502 Command not implemented")
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    # -- protocol pieces --------------------------------------------------

    def _send(self, text: str) -> None:
        self.wfile.write(text.encode("utf-8") + b"\r\n")
        self.wfile.flush()

    def _hello(self, command: str) -> None:
        if command == "HELO":
            self._send(f"250 {self.server.hostname}")
            return
        lines = [
            f"250-{self.server.hostname}",
            "250-8BITMIME",
            "250-SMTPUTF8",
            "250-SIZE 35882577",
        ]
        if self.server.require_auth:
            lines.append("250-AUTH PLAIN LOGIN")
        lines.append("250 HELP")
        for line in lines:
            self._send(line)

    def _auth(self, argument: str) -> None:
        mechanism, _, initial = argument.partition(" ")
        mechanism = mechanism.upper()

        if mechanism == "PLAIN":
            blob = initial
            if not blob:
                self._send("334 ")
                blob = self.rfile.readline(_MAX_LINE).decode().strip()
            try:
                decoded = base64.b64decode(blob).split(b"\0")
                username, password = decoded[1].decode(), decoded[2].decode()
            except (ValueError, IndexError, UnicodeDecodeError):
                self._send("535 Authentication credentials invalid")
                return
        elif mechanism == "LOGIN":
            self._send("334 " + base64.b64encode(b"Username:").decode())
            username = base64.b64decode(self.rfile.readline(_MAX_LINE).strip()).decode()
            self._send("334 " + base64.b64encode(b"Password:").decode())
            password = base64.b64decode(self.rfile.readline(_MAX_LINE).strip()).decode()
        else:
            self._send("504 Unrecognized authentication type")
            return

        if (username, password) == (self.server.username, self.server.password):
            self._authenticated = True
            self._send("235 2.7.0 Authentication successful")
        else:
            self._send("535 5.7.8 Username and Password not accepted")

    def _mail(self, argument: str) -> None:
        if not self._authenticated:
            self._send("530 5.7.0 Authentication required")
            return
        if self.server.reject_sender:
            self._send(f"{self.server.reject_sender} Sender rejected")
            return
        self._sender = _address(argument)
        self._recipients = []
        self._send("250 2.1.0 OK")

    def _rcpt(self, argument: str) -> None:
        if not self._authenticated:
            self._send("530 5.7.0 Authentication required")
            return
        recipient = _address(argument)
        code = self.server.reject_recipients.get(recipient.casefold())
        if code:
            self._send(f"{code} 5.1.1 Mailbox unavailable")
            return
        self._recipients.append(recipient)
        self._send("250 2.1.5 OK")

    def _data(self) -> bool:
        """Read the message body. Returns ``True`` if the connection should end."""
        if not self._authenticated:
            self._send("530 5.7.0 Authentication required")
            return False

        self._send("354 End data with <CR><LF>.<CR><LF>")
        chunks: list[bytes] = []
        while True:
            line = self.rfile.readline(_MAX_LINE)
            if not line or line in {b".\r\n", b".\n"}:
                break
            # Undo transparency: a body line starting with '.' was doubled.
            chunks.append(line[1:] if line.startswith(b"..") else line)

        body = b"".join(chunks)
        stub = self.server

        with stub.lock:
            if stub.disconnect_after is not None and len(stub.messages) >= stub.disconnect_after:
                # Simulate a provider hanging up mid-run, exactly as Gmail does
                # on long-lived connections. Reply with nothing and close.
                stub.disconnect_after = None
                stub.disconnects += 1
                with contextlib.suppress(OSError):
                    self.connection.shutdown(socket.SHUT_RDWR)
                return True

            if stub.reject_with:
                code, text = stub.reject_with
                self._send(f"{code} {text}")
                return False

            stub.messages.append(email.message_from_bytes(body))
            stub.recipients.extend(self._recipients)

        self._send("250 2.0.0 OK: queued")
        return False


def _address(argument: str) -> str:
    """Pull the address out of ``FROM:<a@b> SMTPUTF8 BODY=8BITMIME``."""
    _, _, rest = argument.partition(":")
    rest = rest.strip()
    if rest.startswith("<"):
        end = rest.find(">")
        if end != -1:
            return rest[1:end]
    return rest.split(" ")[0]


class SMTPStub(socketserver.ThreadingTCPServer):
    """A throwaway SMTP server bound to a loopback port.

    Use as a context manager::

        with SMTPStub() as stub:
            ...            # stub.host, stub.port
            assert stub.count == 3
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        *,
        username: str = "sender@example.com",
        password: str = "correct horse battery staple",
        require_auth: bool = True,
        hostname: str = "smtp.test",
    ) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.username = username
        self.password = password
        self.require_auth = require_auth
        self.hostname = hostname

        self.lock = threading.Lock()
        self.messages: list[Message] = []
        self.recipients: list[str] = []
        self.disconnects = 0

        #: ``{address: smtp_code}`` — reject these recipients at RCPT time.
        self.reject_recipients: dict[str, int] = {}
        #: ``(code, text)`` — reject every message at DATA time.
        self.reject_with: tuple[int, str] | None = None
        #: Hang up once, after this many accepted messages.
        self.disconnect_after: int | None = None
        #: SMTP code to reject MAIL FROM with, or 0 to accept.
        self.reject_sender = 0

        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------

    @property
    def host(self) -> str:
        return str(self.server_address[0])

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def start(self) -> SMTPStub:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> SMTPStub:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    # -- assertions -------------------------------------------------------

    @property
    def count(self) -> int:
        with self.lock:
            return len(self.messages)

    def unique_recipients(self) -> set[str]:
        with self.lock:
            return {r.casefold() for r in self.recipients}

    def duplicates(self) -> list[str]:
        """Addresses that received more than one message. Must always be empty."""
        with self.lock:
            seen: set[str] = set()
            dupes: list[str] = []
            for recipient in self.recipients:
                key = recipient.casefold()
                if key in seen:
                    dupes.append(key)
                seen.add(key)
            return dupes

    def subjects(self) -> list[str]:
        with self.lock:
            return [str(m["Subject"]) for m in self.messages]
