"""SahajMails — simple, secure bulk email.

*Sahaj* means simple and natural. This package is a local tool first and a
library second: ``sahajmails run`` opens a web UI on your own machine, and
everything that UI does is available to import.

    >>> from sahajmails import send
    >>> send(contacts="contacts.csv", template="email.md", subject="Hi {{ first_name }}")

Nothing here talks to a server we control. Your contacts and credentials stay on
your machine and go straight to your own mail provider.
"""

from __future__ import annotations

__version__ = "2.0.2"
__app_name__ = "SahajMails"

from .contacts import ContactList, load_contacts
from .errors import (
    AIError,
    AttachmentError,
    AuthenticationError,
    ConfigError,
    ContactsError,
    PluginError,
    SahajMailsError,
    SendError,
    TemplateError,
    TransportError,
)
from .models import Attachment, Contact, RenderedEmail, SendResult, SendStatus
from .template import AISlot, BodyFormat, EmailTemplate, MissingPolicy

__all__ = [
    "AIError",
    "AISlot",
    "Attachment",
    "AttachmentError",
    "AuthenticationError",
    "BodyFormat",
    "ConfigError",
    "Contact",
    "ContactList",
    "ContactsError",
    "EmailTemplate",
    "MissingPolicy",
    "PluginError",
    "RenderedEmail",
    "SahajMailsError",
    "SendError",
    "SendResult",
    "SendStatus",
    "TemplateError",
    "TransportError",
    "__app_name__",
    "__version__",
    "load_contacts",
]
