"""SMTP provider presets.

Picking "Gmail" should be all a user has to do. Each preset carries the host,
port and security mode, plus the operational limits that decide how fast we are
allowed to send — sending faster than a provider tolerates gets an account
throttled or suspended, so these numbers are part of the configuration, not
trivia.

Limits are the conservative documented values. Where a provider publishes a
range (Workspace accounts, paid tiers), the preset takes the lower bound and the
user can raise it in settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["PRESETS", "Preset", "Security", "get_preset", "guess_preset"]


class Security(StrEnum):
    STARTTLS = "starttls"
    """Connect in the clear on 587, then upgrade. The modern default."""

    SSL = "ssl"
    """Implicit TLS from the first byte, normally on 465."""

    NONE = "none"
    """No transport encryption. Only sane for a relay on localhost."""


@dataclass(frozen=True, slots=True)
class Preset:
    """Everything needed to talk to one provider."""

    key: str
    label: str
    host: str
    port: int
    security: Security = Security.STARTTLS

    daily_limit: int | None = None
    """Messages per 24h before the provider starts refusing. ``None`` = unknown."""

    rate_per_minute: int = 30
    max_concurrency: int = 1
    """Simultaneous SMTP connections. Consumer providers dislike more than one."""

    max_attachment_mb: int = 25
    imap_host: str | None = None
    """Where to look for bounces. ``None`` means bounce sync is unavailable."""

    docs_url: str = ""
    setup_hint: str = ""
    """Shown in the UI when authentication fails."""

    @property
    def max_attachment_bytes(self) -> int:
        return self.max_attachment_mb * 1024 * 1024

    @property
    def supports_bounce_sync(self) -> bool:
        return self.imap_host is not None


_GMAIL_HINT = (
    "Gmail rejects your normal password. Turn on 2-Step Verification, then create an "
    "App Password at myaccount.google.com/apppasswords and paste the 16 characters here."
)

PRESETS: dict[str, Preset] = {
    p.key: p
    for p in (
        Preset(
            key="gmail",
            label="Gmail",
            host="smtp.gmail.com",
            port=587,
            daily_limit=500,
            rate_per_minute=20,
            max_attachment_mb=25,
            imap_host="imap.gmail.com",
            docs_url="https://support.google.com/mail/answer/185833",
            setup_hint=_GMAIL_HINT,
        ),
        Preset(
            key="google_workspace",
            label="Google Workspace",
            host="smtp.gmail.com",
            port=587,
            daily_limit=2000,
            rate_per_minute=30,
            max_attachment_mb=25,
            imap_host="imap.gmail.com",
            docs_url="https://support.google.com/a/answer/166852",
            setup_hint=_GMAIL_HINT,
        ),
        Preset(
            key="outlook",
            label="Outlook / Microsoft 365",
            host="smtp-mail.outlook.com",
            port=587,
            daily_limit=300,
            rate_per_minute=20,
            max_attachment_mb=20,
            imap_host="outlook.office365.com",
            docs_url="https://support.microsoft.com/en-us/office/pop-imap-and-smtp-settings-8361e398-8af4-4e97-b147-6c6c4ac95353",
            setup_hint=(
                "Microsoft has disabled basic SMTP auth on many tenants. If sign-in fails, "
                "ask your admin to enable SMTP AUTH, or use an app password."
            ),
        ),
        Preset(
            key="yahoo",
            label="Yahoo Mail",
            host="smtp.mail.yahoo.com",
            port=465,
            security=Security.SSL,
            daily_limit=500,
            rate_per_minute=20,
            max_attachment_mb=25,
            imap_host="imap.mail.yahoo.com",
            docs_url="https://help.yahoo.com/kb/SLN4724.html",
            setup_hint="Yahoo requires an app password from your Account Security page.",
        ),
        Preset(
            key="zoho",
            label="Zoho Mail",
            host="smtp.zoho.com",
            port=587,
            daily_limit=500,
            rate_per_minute=30,
            max_attachment_mb=25,
            imap_host="imap.zoho.com",
            docs_url="https://www.zoho.com/mail/help/zoho-smtp.html",
        ),
        Preset(
            key="icloud",
            label="iCloud Mail",
            host="smtp.mail.me.com",
            port=587,
            daily_limit=200,
            rate_per_minute=15,
            max_attachment_mb=20,
            imap_host="imap.mail.me.com",
            docs_url="https://support.apple.com/en-us/102525",
            setup_hint="iCloud requires an app-specific password from appleid.apple.com.",
        ),
        Preset(
            key="fastmail",
            label="Fastmail",
            host="smtp.fastmail.com",
            port=465,
            security=Security.SSL,
            daily_limit=2000,
            rate_per_minute=60,
            max_concurrency=2,
            max_attachment_mb=50,
            imap_host="imap.fastmail.com",
            docs_url="https://www.fastmail.help/hc/en-us/articles/1500000278342",
        ),
        # --- transactional providers: higher throughput, no consumer mailbox ---
        Preset(
            key="ses",
            label="Amazon SES",
            host="email-smtp.us-east-1.amazonaws.com",
            port=587,
            daily_limit=None,
            rate_per_minute=840,  # 14/sec default sending rate
            max_concurrency=5,
            max_attachment_mb=10,
            docs_url="https://docs.aws.amazon.com/ses/latest/dg/smtp-credentials.html",
            setup_hint=(
                "Use SES SMTP credentials (not your AWS access keys), and change the host "
                "to match your region."
            ),
        ),
        Preset(
            key="mailgun",
            label="Mailgun",
            host="smtp.mailgun.org",
            port=587,
            rate_per_minute=600,
            max_concurrency=5,
            max_attachment_mb=25,
            docs_url="https://documentation.mailgun.com/docs/mailgun/user-manual/sending-messages/",
        ),
        Preset(
            key="postmark",
            label="Postmark",
            host="smtp.postmarkapp.com",
            port=587,
            rate_per_minute=300,
            max_concurrency=3,
            max_attachment_mb=10,
            docs_url="https://postmarkapp.com/developer/user-guide/send-email-with-smtp",
        ),
        Preset(
            key="sendgrid",
            label="SendGrid",
            host="smtp.sendgrid.net",
            port=587,
            rate_per_minute=600,
            max_concurrency=5,
            max_attachment_mb=30,
            docs_url="https://www.twilio.com/docs/sendgrid/for-developers/sending-email/getting-started-smtp",
            setup_hint="The username is literally 'apikey'; the password is your API key.",
        ),
        Preset(
            key="resend",
            label="Resend",
            host="smtp.resend.com",
            port=587,
            rate_per_minute=600,
            max_concurrency=3,
            max_attachment_mb=40,
            docs_url="https://resend.com/docs/send-with-smtp",
            setup_hint="The username is 'resend'; the password is your API key.",
        ),
        Preset(
            key="brevo",
            label="Brevo (Sendinblue)",
            host="smtp-relay.brevo.com",
            port=587,
            daily_limit=300,
            rate_per_minute=120,
            max_concurrency=2,
            max_attachment_mb=10,
            docs_url="https://help.brevo.com/hc/en-us/articles/7924908994450",
        ),
        Preset(
            key="custom",
            label="Custom SMTP server",
            host="",
            port=587,
            daily_limit=None,
            rate_per_minute=60,
            docs_url="",
            setup_hint="Enter the host, port and security mode your provider documents.",
        ),
    )
}

# Maps the domain of a sending address to its preset, so choosing a provider is
# usually unnecessary.
_DOMAIN_HINTS: dict[str, str] = {
    "gmail.com": "gmail",
    "googlemail.com": "gmail",
    "outlook.com": "outlook",
    "hotmail.com": "outlook",
    "live.com": "outlook",
    "msn.com": "outlook",
    "yahoo.com": "yahoo",
    "yahoo.co.uk": "yahoo",
    "ymail.com": "yahoo",
    "zoho.com": "zoho",
    "icloud.com": "icloud",
    "me.com": "icloud",
    "mac.com": "icloud",
    "fastmail.com": "fastmail",
    "fastmail.fm": "fastmail",
}


def get_preset(key: str) -> Preset:
    """Look up a preset, falling back to ``custom`` for unknown keys."""
    return PRESETS.get(key.strip().casefold(), PRESETS["custom"])


def guess_preset(email: str) -> Preset | None:
    """Infer the provider from a sending address, or ``None`` if unrecognised."""
    _, _, domain = email.strip().casefold().rpartition("@")
    key = _DOMAIN_HINTS.get(domain)
    return PRESETS[key] if key else None
