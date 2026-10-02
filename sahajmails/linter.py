"""Content checks that run before a send.

Pure heuristics over the rendered message — no dependencies, no network. None of
these are certainties; they are the things that, in aggregate, decide whether a
message lands in the inbox or the spam folder. Each finding names the problem
and what to do about it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .models import RenderedEmail

__all__ = ["LintFinding", "lint_email"]

Level = Literal["error", "warning", "info"]

# Phrases with a long history in spam corpora. Matched as whole phrases to keep
# false positives down — "free" alone is far too common to flag.
_SPAM_PHRASES = (
    "100% free",
    "act now",
    "amazing deal",
    "buy direct",
    "call now",
    "cash bonus",
    "click here now",
    "congratulations you won",
    "credit card offer",
    "dear friend",
    "double your income",
    "earn extra cash",
    "eliminate debt",
    "financial freedom",
    "for free",
    "get paid",
    "guaranteed",
    "increase sales",
    "limited time only",
    "lose weight",
    "make money fast",
    "no credit check",
    "no obligation",
    "once in a lifetime",
    "only $",
    "order now",
    "risk free",
    "satisfaction guaranteed",
    "special promotion",
    "this is not spam",
    "urgent response",
    "why pay more",
    "winner",
    "work from home",
)

# Link shorteners hide the destination, which filters treat as evasion.
_SHORTENERS = (
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "is.gd", "buff.ly",
    "rebrand.ly", "cutt.ly", "shorturl.at", "rb.gy", "tiny.cc",
)  # fmt: skip

_URL_RE = re.compile(r"https?://([^\s/\"'>)]+)", re.IGNORECASE)
_IMG_RE = re.compile(r"<img\b", re.IGNORECASE)
_SUBJECT_MAX = 78
_SUBJECT_COMFORTABLE = 60


@dataclass(frozen=True, slots=True)
class LintFinding:
    level: Level
    message: str
    hint: str = ""


def lint_email(
    rendered: RenderedEmail,
    *,
    has_unsubscribe: bool = False,
    attachment_bytes: int = 0,
    attachment_limit: int = 25 * 1024 * 1024,
) -> list[LintFinding]:
    """Inspect one rendered message and report what might hurt delivery."""
    findings: list[LintFinding] = []
    subject = rendered.subject.strip()
    html = rendered.html
    text = rendered.text.strip()
    body_words = text.split()

    # -- subject ----------------------------------------------------------
    if not subject:
        findings.append(
            LintFinding("error", "The subject is empty.", "Messages with no subject get filtered.")
        )
    elif len(subject) > _SUBJECT_MAX:
        findings.append(
            LintFinding(
                "warning",
                f"The subject is {len(subject)} characters.",
                f"Most clients truncate past ~{_SUBJECT_COMFORTABLE}. Front-load the point.",
            )
        )

    letters = [c for c in subject if c.isalpha()]
    if len(letters) >= 8 and all(c.isupper() for c in letters):
        findings.append(
            LintFinding("warning", "The subject is all capitals.", "Reads as shouting to filters.")
        )

    if subject.count("!") > 1 or "!!!" in subject:
        findings.append(
            LintFinding(
                "warning", "The subject has repeated exclamation marks.", "Use at most one."
            )
        )

    if re.search(r"\bre:\s|\bfwd:\s", subject, re.IGNORECASE):
        findings.append(
            LintFinding(
                "warning",
                "The subject fakes a reply or forward.",
                "'Re:' on a first contact is a well-known spam pattern.",
            )
        )

    # -- body -------------------------------------------------------------
    if not text:
        findings.append(
            LintFinding(
                "error",
                "The plain-text part is empty.",
                "HTML-only mail is one of the strongest spam signals there is.",
            )
        )
    elif len(body_words) < 15:
        findings.append(
            LintFinding(
                "info",
                f"The body is very short ({len(body_words)} words).",
                "Short, link-heavy messages attract more filtering.",
            )
        )

    images = len(_IMG_RE.findall(html))
    if images and len(body_words) < images * 20:
        findings.append(
            LintFinding(
                "warning",
                f"{images} image(s) but only {len(body_words)} words of text.",
                "Image-heavy messages with little text score badly. Add copy or drop images.",
            )
        )

    hosts = [h.casefold() for h in _URL_RE.findall(html)]
    if len(hosts) > 10:
        findings.append(
            LintFinding("warning", f"{len(hosts)} links in one message.", "Trim to the essentials.")
        )

    shortened = sorted({h for h in hosts if any(s in h for s in _SHORTENERS)})
    if shortened:
        findings.append(
            LintFinding(
                "warning",
                f"Uses link shorteners: {', '.join(shortened)}.",
                "Shorteners hide the destination and are widely treated as evasion.",
            )
        )

    haystack = f"{subject}\n{text}".casefold()
    hits = sorted({p for p in _SPAM_PHRASES if p in haystack})
    if hits:
        level: Level = "warning" if len(hits) > 2 else "info"
        findings.append(
            LintFinding(
                level,
                f"Contains spam-associated phrasing: {', '.join(repr(h) for h in hits[:5])}.",
                "Rewrite in plainer language.",
            )
        )

    # -- compliance and size ----------------------------------------------
    if not has_unsubscribe:
        findings.append(
            LintFinding(
                "warning",
                "No unsubscribe header.",
                "Gmail and Yahoo have required List-Unsubscribe from bulk senders since "
                "February 2024. Set an unsubscribe address in settings.",
            )
        )

    if attachment_bytes:
        # Base64 inflates the payload by roughly a third on the wire.
        encoded = int(attachment_bytes * 4 / 3)
        if encoded > attachment_limit:
            findings.append(
                LintFinding(
                    "error",
                    f"Attachments are {encoded / 1024 / 1024:.1f} MB encoded, over the "
                    f"{attachment_limit / 1024 / 1024:.0f} MB limit.",
                    "Link to the file instead of attaching it.",
                )
            )
        elif encoded > attachment_limit * 0.8:
            findings.append(
                LintFinding(
                    "warning",
                    f"Attachments are {encoded / 1024 / 1024:.1f} MB encoded.",
                    "Close to the provider limit; large attachments also slow every send.",
                )
            )

    if "{{" in text or "{%" in text:
        findings.append(
            LintFinding(
                "error",
                "The rendered body still contains template syntax.",
                "A placeholder did not resolve. Check the spelling against your columns.",
            )
        )

    return findings
