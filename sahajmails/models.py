"""Core data types.

These are plain dataclasses with no I/O and no dependencies on the web layer,
so they are cheap to construct in tests and safe to pass across threads.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "Attachment",
    "Contact",
    "RenderedEmail",
    "SendResult",
    "SendStatus",
    "normalize_key",
]

# Matches the characters we collapse when normalising a column name.
_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalize_key(name: str) -> str:
    """Fold a spreadsheet column name into a canonical identifier.

    ``"First Name"``, ``"firstName"``, ``"FIRST_NAME"`` and ``"first name "``
    all become ``"first_name"``. This is what makes placeholders tolerant of
    however the user happened to label their columns.
    """
    # Split camelCase before folding case, so "firstName" -> "first_name"
    # rather than "firstname".
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.strip())
    folded = unicodedata.normalize("NFKD", spaced).casefold()
    return _NON_WORD.sub("_", folded).strip("_")


class SendStatus(StrEnum):
    """Terminal state of one delivery attempt."""

    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
    SUPPRESSED = "suppressed"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class Contact:
    """One recipient plus whatever columns came with them.

    ``fields`` is keyed by :func:`normalize_key`, so lookups are predictable no
    matter how the source file was labelled. ``row`` is the 1-based line in the
    source file and exists purely so error messages can point at something the
    user can find.
    """

    email: str
    fields: dict[str, str] = field(default_factory=dict)
    row: int = 0

    def get(self, key: str, default: str = "") -> str:
        return self.fields.get(normalize_key(key), default)


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file to attach. Encoded once and reused across every recipient."""

    filename: str
    content: bytes
    content_type: str | None = None

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """A template rendered for one specific contact."""

    subject: str
    html: str
    text: str
    preheader: str = ""


@dataclass(frozen=True, slots=True)
class SendResult:
    """Outcome of one delivery attempt. One of these per recipient per run."""

    email: str
    status: SendStatus
    message_id: str | None = None
    error: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    row: int = 0

    @property
    def ok(self) -> bool:
        return self.status is SendStatus.SENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "status": str(self.status),
            "message_id": self.message_id,
            "error": self.error,
            "at": self.at.isoformat(),
            "row": self.row,
        }
