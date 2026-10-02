"""Exception hierarchy.

Every error carries an optional ``hint``: a plain-language sentence telling the
user what to actually do about it. The CLI prints it under the error and the web
UI shows it beside the message. An error the user cannot act on is a bug.
"""

from __future__ import annotations

__all__ = [
    "AIError",
    "AttachmentError",
    "AuthenticationError",
    "ConfigError",
    "ContactsError",
    "PluginError",
    "SahajMailsError",
    "SendError",
    "TemplateError",
    "TransportError",
]


class SahajMailsError(Exception):
    """Base class for every error raised by sahajmails."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return self.message


class ConfigError(SahajMailsError):
    """Settings are missing, malformed, or contradictory."""


class ContactsError(SahajMailsError):
    """A contact file could not be read, or its contents are unusable."""


class TemplateError(SahajMailsError):
    """A template failed to parse or render."""


class AttachmentError(SahajMailsError):
    """An attachment is missing, unreadable, or too large."""


class TransportError(SahajMailsError):
    """The mail transport failed at the connection level."""


class AuthenticationError(TransportError):
    """The server rejected the credentials."""


class SendError(SahajMailsError):
    """A specific message could not be delivered."""

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        recipient: str | None = None,
        permanent: bool = False,
    ) -> None:
        super().__init__(message, hint=hint)
        self.recipient = recipient
        #: ``True`` for SMTP 5xx (do not retry), ``False`` for 4xx (retry).
        self.permanent = permanent


class AIError(SahajMailsError):
    """An AI backend failed or returned unusable output."""


class PluginError(SahajMailsError):
    """A plugin failed to load or misbehaved."""
