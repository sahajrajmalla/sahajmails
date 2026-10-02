"""Dry-run transport: writes ``.eml`` files instead of sending.

The point is that ``.eml`` is a real format. Double-click one and it opens in
Apple Mail, Outlook or Thunderbird exactly as the recipient would see it —
including how the HTML actually renders, which no in-app preview can promise.
"""

from __future__ import annotations

import re
from email.generator import BytesGenerator
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from ..errors import TransportError

__all__ = ["FileTransport"]

_UNSAFE = re.compile(r"[^A-Za-z0-9._@+-]+")


class FileTransport:
    """Serialises each message to ``<outdir>/NNNN-<recipient>.eml``."""

    name = "file"

    def __init__(self, *, outdir: str | Path = "outbox", overwrite: bool = True) -> None:
        self.outdir = Path(outdir).expanduser()
        self.overwrite = overwrite
        self._count = 0
        self._opened = False

    def open(self) -> None:
        if self._opened:
            return
        try:
            self.outdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TransportError(f"Could not create {self.outdir}: {exc}") from exc
        self._opened = True

    def verify(self) -> None:
        self.open()
        probe = self.outdir / ".sahajmails-write-check"
        try:
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise TransportError(f"{self.outdir} is not writable: {exc}") from exc

    def send(self, message: EmailMessage, *, sender: str, recipient: str) -> str:
        self.open()
        self._count += 1

        stem = _UNSAFE.sub("_", recipient)[:60] or "recipient"
        path = self.outdir / f"{self._count:04d}-{stem}.eml"
        if path.exists() and not self.overwrite:
            raise TransportError(f"{path} already exists.")

        try:
            with path.open("wb") as handle:
                # BytesGenerator with the default policy writes the message
                # exactly as it would go on the wire, so what you open is what
                # would have been delivered.
                BytesGenerator(handle, policy=message.policy).flatten(message)
        except OSError as exc:
            raise TransportError(f"Could not write {path}: {exc}") from exc

        return str(message.get("Message-ID") or make_msgid())

    def close(self) -> None:
        self._opened = False

    @property
    def written(self) -> int:
        return self._count
