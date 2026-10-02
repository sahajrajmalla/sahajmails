"""Pre-flight: everything that can be checked before a single email goes out.

This is the *sahaj* feature — one command, or one panel in the UI, that answers
"will this send work?" before you commit. It renders every contact, so a
placeholder that only breaks on row 412 surfaces now rather than mid-run.

Findings are graded. Only ``error`` blocks a send; warnings inform.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .contacts import ContactList
from .errors import SahajMailsError
from .linter import lint_email
from .message import PreparedAttachment
from .models import normalize_key
from .template import EmailTemplate, RenderTrace

if TYPE_CHECKING:
    from .config import Settings

__all__ = ["Finding", "PreflightReport", "run_preflight"]

Level = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    message: str
    hint: str = ""
    category: str = "general"


@dataclass(slots=True)
class PreflightReport:
    findings: list[Finding] = field(default_factory=list)
    rendered_sample: object | None = None
    contacts_checked: int = 0

    def add(self, level: Level, message: str, hint: str = "", category: str = "general") -> None:
        self.findings.append(Finding(level, message, hint, category))

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "contacts_checked": self.contacts_checked,
            "findings": [
                {
                    "level": f.level,
                    "message": f.message,
                    "hint": f.hint,
                    "category": f.category,
                }
                for f in self.findings
            ],
        }


def run_preflight(
    *,
    contacts: ContactList,
    template: EmailTemplate | None = None,
    settings: Settings | None = None,
    attachments: Sequence[PreparedAttachment] = (),
    suppressed: set[str] | None = None,
    recently_mailed: dict[str, str] | None = None,
    check_connection: bool = False,
    check_dns: bool = False,
) -> PreflightReport:
    """Check a campaign end to end.

    Args:
        check_connection: Actually connect and authenticate. Off by default so
            ``check`` stays fast and offline-friendly.
        check_dns: Look up SPF and DMARC for the sending domain.
    """
    report = PreflightReport(contacts_checked=len(contacts))

    _check_contacts(report, contacts, suppressed, recently_mailed)
    if template is not None:
        _check_template(report, template, contacts, attachments, settings)
    if settings is not None:
        _check_settings(report, settings, contacts, attachments, check_connection, check_dns)

    return report


# ------------------------------------------------------------------ contacts


def _check_contacts(
    report: PreflightReport,
    contacts: ContactList,
    suppressed: set[str] | None,
    recently_mailed: dict[str, str] | None,
) -> None:
    load = contacts.report

    if not contacts:
        report.add("error", "No contacts to send to.", category="contacts")
        return

    report.add("info", f"{len(contacts):,} contact(s) ready.", category="contacts")

    if load.invalid:
        preview = ", ".join(f"row {row} ({reason})" for row, _, reason in load.invalid[:3])
        more = f" and {len(load.invalid) - 3} more" if len(load.invalid) > 3 else ""
        report.add(
            "warning",
            f"{len(load.invalid)} row(s) had no usable address: {preview}{more}.",
            "Fix them in the source file, or send anyway and they will be skipped.",
            category="contacts",
        )

    if load.duplicates:
        report.add(
            "info",
            f"{len(load.duplicates)} duplicate address(es) removed.",
            category="contacts",
        )

    if suppressed:
        overlap = {c.email for c in contacts} & suppressed
        if overlap:
            report.add(
                "warning",
                f"{len(overlap)} contact(s) are on your do-not-contact list.",
                "They will be skipped automatically.",
                category="suppression",
            )

    if recently_mailed:
        report.add(
            "warning",
            f"{len(recently_mailed)} contact(s) already heard from you in the last 30 days.",
            "Check you are not repeating a send.",
            category="duplicates",
        )


# ------------------------------------------------------------------ template


def _check_template(
    report: PreflightReport,
    template: EmailTemplate,
    contacts: ContactList,
    attachments: Sequence[PreparedAttachment],
    settings: Settings | None,
) -> None:
    if not template.subject.strip():
        report.add("error", "The subject is empty.", category="template")

    # Which placeholders exist as columns? Catches a typo before it ships.
    #
    # Both sides are normalized first, because the renderer resolves
    # {{ firstName }} against a first_name column. Comparing the raw names
    # would flag every camelCase placeholder as missing — a false positive on
    # a blocking error, which is worse than no check at all.
    available = {normalize_key(c) for c in contacts.columns} | {"email"}
    unknown = sorted(v for v in template.variables() if normalize_key(v) not in available)
    if unknown:
        labels = sorted(contacts.labels.get(c, c) for c in contacts.columns)
        report.add(
            "error",
            f"Template uses column(s) your file does not have: {', '.join(unknown)}.",
            f"Available: {', '.join(labels)}",
            category="template",
        )

    # Render every contact. A placeholder that only breaks on row 412 is
    # exactly the failure worth finding now instead of mid-send.
    failures: list[tuple[int, str]] = []
    missing: set[str] = set()
    first_rendered = None

    for contact in contacts:
        trace = RenderTrace()
        try:
            rendered = template.render(contact, trace=trace)
            if first_rendered is None:
                first_rendered = rendered
        except SahajMailsError as exc:
            failures.append((contact.row, str(exc)))
            if len(failures) >= 5:
                break
        missing |= trace.missing

    if failures:
        detail = "; ".join(f"row {row}: {message}" for row, message in failures[:3])
        report.add(
            "error",
            f"{len(failures)} contact(s) could not be rendered. {detail}",
            "Use a default, e.g. {{ first_name | default('there') }}.",
            category="template",
        )
    elif missing:
        report.add(
            "warning",
            f"Some contacts are missing values for: {', '.join(sorted(missing))}.",
            "Those spots will be blank. Add a default to control what appears.",
            category="template",
        )

    if first_rendered is not None:
        report.rendered_sample = first_rendered
        has_unsubscribe = bool(
            settings and (settings.unsubscribe_mailto or settings.unsubscribe_url)
        )
        limit = settings.preset.max_attachment_bytes if settings else 25 * 1024 * 1024
        for finding in lint_email(
            first_rendered,
            has_unsubscribe=has_unsubscribe,
            attachment_bytes=sum(a.size for a in attachments),
            attachment_limit=limit,
        ):
            report.add(finding.level, finding.message, finding.hint, category="content")


# ------------------------------------------------------------------ settings


def _check_settings(
    report: PreflightReport,
    settings: Settings,
    contacts: ContactList,
    attachments: Sequence[PreparedAttachment],
    check_connection: bool,
    check_dns: bool,
) -> None:
    try:
        settings.validate()
    except SahajMailsError as exc:
        report.add("error", str(exc), exc.hint or "", category="settings")
        return

    preset = settings.preset
    report.add(
        "info",
        f"Sending as {settings.from_address} via {preset.label}.",
        category="settings",
    )

    if settings.daily_limit and len(contacts) > settings.daily_limit:
        report.add(
            "warning",
            f"{preset.label} allows about {settings.daily_limit:,} messages a day; "
            f"this run is {len(contacts):,}.",
            "Split it across days, or the provider will start refusing partway through.",
            category="quota",
        )

    minutes = len(contacts) / max(1, settings.rate_per_minute)
    if minutes > 60:
        report.add(
            "info",
            f"At {settings.rate_per_minute}/min this will take about {minutes / 60:.1f} hours.",
            "You can raise the rate in settings, within what your provider allows.",
            category="quota",
        )

    if check_dns:
        _check_dns(report, settings)

    if check_connection:
        _check_connection(report, settings)


def _check_dns(report: PreflightReport, settings: Settings) -> None:
    from .dns_check import check_domain

    domain = settings.from_address.rpartition("@")[2]
    if not domain:
        return
    for finding in check_domain(domain):
        report.add(finding.level, finding.message, finding.hint, category="dns")


def _check_connection(report: PreflightReport, settings: Settings) -> None:
    from .transport import create_transport

    transport = create_transport("smtp", **settings.transport_kwargs())
    try:
        transport.verify()
    except SahajMailsError as exc:
        report.add("error", str(exc), exc.hint or "", category="connection")
    else:
        report.add("info", "Signed in to the mail server successfully.", category="connection")
    finally:
        transport.close()
