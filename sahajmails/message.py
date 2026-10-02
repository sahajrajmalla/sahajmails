"""Building the actual email.

Uses :class:`email.message.EmailMessage` rather than hand-assembled
``MIMEMultipart`` parts. The modern API gets RFC 2047 header encoding, correct
charset negotiation and internationalised addresses right by construction; the
1.x code had to do each of those by hand and did not.

Two things here are deliberate:

* **Every message is multipart/alternative** — a real ``text/plain`` part
  alongside the HTML. HTML-only mail is one of the strongest spam signals there
  is, and 1.x sent HTML only.
* **Attachments are encoded once.** :func:`prepare_attachments` base64-encodes
  each file a single time and every message reuses that part. 1.x re-encoded
  every attachment for every recipient, so a 5 MB PDF to 1000 contacts meant
  1000 base64 passes.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid, parseaddr
from pathlib import Path

from .errors import AttachmentError
from .models import Attachment, RenderedEmail

__all__ = [
    "PreparedAttachment",
    "build_message",
    "load_attachment",
    "prepare_attachments",
    "split_address",
]


@dataclass(frozen=True, slots=True)
class PreparedAttachment:
    """An attachment encoded once, ready to be attached to many messages."""

    filename: str
    size: int
    maintype: str
    subtype: str
    _part: EmailMessage = field(repr=False)

    @property
    def content_type(self) -> str:
        return f"{self.maintype}/{self.subtype}"


def load_attachment(path: str | Path) -> Attachment:
    """Read a file from disk into an :class:`~sahajmails.models.Attachment`."""
    file = Path(path).expanduser()
    if not file.exists():
        raise AttachmentError(f"Attachment not found: {file}", hint="Check the path.")
    if file.is_dir():
        raise AttachmentError(f"{file} is a folder, not a file.", hint="Zip it first.")
    try:
        content = file.read_bytes()
    except OSError as exc:
        raise AttachmentError(f"Could not read {file}: {exc}") from exc
    guessed, _ = mimetypes.guess_type(file.name)
    return Attachment(filename=file.name, content=content, content_type=guessed)


def _split_type(attachment: Attachment) -> tuple[str, str]:
    declared = attachment.content_type
    if not declared or "/" not in declared:
        declared, _ = mimetypes.guess_type(attachment.filename)
    if not declared or "/" not in declared:
        # The safe default: clients will offer to download rather than trying
        # to render something they cannot identify.
        return "application", "octet-stream"
    maintype, _, subtype = declared.partition("/")
    return maintype.strip() or "application", subtype.strip() or "octet-stream"


def _safe_filename(name: str) -> str:
    """Strip any path components a filename might be carrying.

    Uploaded filenames are attacker-controlled. This never touches the local
    filesystem, but a name like ``../../x`` in a Content-Disposition header is
    still worth neutralising before it reaches a recipient's mail client.
    """
    cleaned = Path(name.replace("\\", "/")).name
    cleaned = cleaned.replace("\r", "").replace("\n", "").strip()
    return cleaned or "attachment"


def prepare_attachments(attachments: Sequence[Attachment]) -> list[PreparedAttachment]:
    """Encode each attachment once for reuse across every recipient."""
    prepared: list[PreparedAttachment] = []
    for attachment in attachments:
        maintype, subtype = _split_type(attachment)
        filename = _safe_filename(attachment.filename)

        part = EmailMessage()
        part.set_content(
            attachment.content,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
            disposition="attachment",
        )
        prepared.append(
            PreparedAttachment(
                filename=filename,
                size=attachment.size,
                maintype=maintype,
                subtype=subtype,
                _part=part,
            )
        )
    return prepared


def split_address(value: str) -> tuple[str, str]:
    """Split ``'Alice <a@b.com>'`` into ``('Alice', 'a@b.com')``."""
    name, address = parseaddr(value)
    return name.strip(), address.strip()


def _format_sender(email: str, name: str | None) -> str:
    if not name:
        return email
    # formataddr handles quoting and RFC 2047 encoding for non-ASCII names.
    return formataddr((name, email))


def _needs_smtputf8(*addresses: str) -> bool:
    return any(not address.isascii() for address in addresses)


def build_message(
    *,
    sender: str,
    recipient: str,
    rendered: RenderedEmail,
    sender_name: str | None = None,
    recipient_name: str | None = None,
    reply_to: str | None = None,
    attachments: Sequence[PreparedAttachment] = (),
    headers: Mapping[str, str] | None = None,
    unsubscribe_mailto: str | None = None,
    unsubscribe_url: str | None = None,
    message_id: str | None = None,
    date: datetime | None = None,
) -> EmailMessage:
    """Assemble one deliverable message.

    Args:
        sender: The envelope and ``From`` address.
        recipient: The single recipient. Bulk means one message each, not one
            message with a thousand names on it.
        rendered: Subject, HTML and text from the template.
        unsubscribe_mailto: Address for one-click unsubscribe. Strongly
            recommended: Gmail and Yahoo require a ``List-Unsubscribe`` header
            from bulk senders, and its absence is treated as a spam signal.
        unsubscribe_url: HTTPS endpoint for one-click unsubscribe, if you have
            somewhere to host one.

    Returns:
        A ``multipart/alternative`` message, wrapped in ``multipart/mixed`` when
        there are attachments.
    """
    message = EmailMessage()

    message["From"] = _format_sender(sender, sender_name)
    message["To"] = _format_sender(recipient, recipient_name)
    message["Subject"] = rendered.subject or "(no subject)"
    message["Date"] = format_datetime(date or datetime.now(UTC))

    # A stable, well-formed Message-ID matters: some filters penalise messages
    # without one, and it is how a send is correlated with a later bounce.
    domain = sender.rpartition("@")[2] or None
    message["Message-ID"] = message_id or make_msgid(domain=domain)

    if reply_to:
        message["Reply-To"] = reply_to

    # RFC 8058 one-click unsubscribe. List-Unsubscribe-Post is only valid
    # alongside at least one URI, and only meaningful with an https target.
    targets: list[str] = []
    if unsubscribe_url:
        targets.append(f"<{unsubscribe_url}>")
    if unsubscribe_mailto:
        targets.append(f"<mailto:{unsubscribe_mailto}?subject=unsubscribe>")
    if targets:
        message["List-Unsubscribe"] = ", ".join(targets)
        if unsubscribe_url:
            message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    for key, value in (headers or {}).items():
        if key.casefold() in {"from", "to", "subject", "date", "message-id"}:
            continue  # never let custom headers shadow the ones we control
        # Header injection guard: a newline here would let a template author
        # append arbitrary headers or a second body.
        message[key] = str(value).replace("\r", " ").replace("\n", " ")

    text = rendered.text.strip() or _fallback_text(rendered)
    message.set_content(text, subtype="plain", charset="utf-8")
    message.add_alternative(rendered.html, subtype="html", charset="utf-8")

    if attachments:
        message.make_mixed()
        for attachment in attachments:
            message.attach(attachment._part)

    return message


def _fallback_text(rendered: RenderedEmail) -> str:
    from .template import html_to_text

    return html_to_text(rendered.html) or rendered.subject


def total_attachment_size(attachments: Sequence[PreparedAttachment]) -> int:
    """Sum of raw sizes. Base64 inflates this by roughly a third on the wire."""
    return sum(a.size for a in attachments)


def encoded_size(attachments: Sequence[PreparedAttachment]) -> int:
    """Approximate on-the-wire size after base64 encoding."""
    return int(total_attachment_size(attachments) * 4 / 3)


def validate_addresses(*addresses: str) -> None:
    """Reject addresses that cannot be put in a header safely."""
    for address in addresses:
        if not address:
            raise AttachmentError("Empty address.")
        if "\r" in address or "\n" in address:
            raise AttachmentError(
                f"Address contains a line break: {address!r}",
                hint="This looks like a header-injection attempt.",
            )


def requires_smtputf8(sender: str, recipient: str) -> bool:
    """Whether this message needs the SMTPUTF8 extension."""
    return _needs_smtputf8(sender, recipient)


def address_object(email: str, display_name: str = "") -> Address:
    """Build an :class:`email.headerregistry.Address` from parts."""
    local, _, domain = email.partition("@")
    return Address(display_name=display_name, username=local, domain=domain)
