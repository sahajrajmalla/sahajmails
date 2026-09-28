"""SPF, DKIM and DMARC checks for the sending domain.

Since February 2024 Gmail and Yahoo have *required* authenticated mail from bulk
senders. Whether these records exist is the single biggest factor in whether a
campaign reaches inboxes, and it is invisible from inside the app — hence a
lookup.

Anyone sending from a provider's own domain (``@gmail.com``, ``@outlook.com``)
is already covered by that provider, and is told so rather than alarmed.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

__all__ = ["DnsFinding", "check_domain"]

Level = Literal["error", "warning", "info"]

DEFAULT_TIMEOUT = 5.0

# Domains where the provider owns authentication. Sending "from" one of these
# means SPF/DKIM/DMARC are the provider's problem, and already handled.
_PROVIDER_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.co.uk",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "proton.me",
        "protonmail.com",
        "zoho.com",
        "aol.com",
        "gmx.com",
        "mail.com",
        "fastmail.com",
    }
)

# Selectors used by the common providers, tried in turn. DKIM cannot be
# discovered from the domain alone — the selector is chosen by whoever signs.
_DKIM_SELECTORS = (
    "google",
    "selector1",
    "selector2",
    "k1",
    "k2",
    "s1",
    "s2",
    "mail",
    "dkim",
    "default",
    "zoho",
    "protonmail",
    "fm1",
)


@dataclass(frozen=True, slots=True)
class DnsFinding:
    level: Level
    message: str
    hint: str = ""


def _txt_records(name: str, timeout: float) -> list[str]:
    """Fetch TXT records, joining the multi-string chunks DNS splits them into."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout

    try:
        answers = resolver.resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except dns.exception.DNSException:
        raise

    records: list[str] = []
    for answer in answers:
        chunks = getattr(answer, "strings", None)
        if chunks:
            records.append(b"".join(chunks).decode("utf-8", errors="replace"))
        else:  # pragma: no cover - depends on dnspython internals
            records.append(str(answer).strip('"'))
    return records


@lru_cache(maxsize=64)
def check_domain(domain: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[DnsFinding, ...]:
    """Report on the sending domain's email authentication.

    Cached: the same domain is checked on every pre-flight, and DNS does not
    change between two clicks.
    """
    domain = domain.strip().casefold().rstrip(".")
    if not domain:
        return ()

    if domain in _PROVIDER_DOMAINS:
        return (
            DnsFinding(
                "info",
                f"{domain} handles SPF, DKIM and DMARC for you.",
                "Nothing to configure while you send from this address.",
            ),
        )

    import dns.exception

    findings: list[DnsFinding] = []

    # -- SPF ---------------------------------------------------------------
    try:
        spf = [r for r in _txt_records(domain, timeout) if r.casefold().startswith("v=spf1")]
    except dns.exception.DNSException as exc:
        return (
            DnsFinding(
                "info",
                f"Could not check DNS for {domain} ({type(exc).__name__}).",
                "Skipping the authentication check. Are you offline?",
            ),
        )

    if not spf:
        findings.append(
            DnsFinding(
                "warning",
                f"{domain} has no SPF record.",
                "Add a TXT record like 'v=spf1 include:_spf.google.com ~all' listing "
                "whoever sends on your behalf. Without it, bulk mail is often refused.",
            )
        )
    elif len(spf) > 1:
        findings.append(
            DnsFinding(
                "warning",
                f"{domain} has {len(spf)} SPF records.",
                "More than one is invalid per RFC 7208 and most receivers fail the check. "
                "Merge them into a single record.",
            )
        )
    else:
        record = spf[0]
        if record.rstrip().endswith("+all"):
            findings.append(
                DnsFinding(
                    "warning",
                    f"{domain}'s SPF ends in '+all', which authorises the entire internet.",
                    "Use '~all' (soft fail) or '-all' (hard fail).",
                )
            )
        else:
            findings.append(DnsFinding("info", f"SPF found for {domain}."))

    # -- DMARC -------------------------------------------------------------
    try:
        dmarc = [
            r
            for r in _txt_records(f"_dmarc.{domain}", timeout)
            if r.casefold().startswith("v=dmarc1")
        ]
    except dns.exception.DNSException:
        dmarc = []

    if not dmarc:
        findings.append(
            DnsFinding(
                "warning",
                f"{domain} has no DMARC record.",
                "Gmail and Yahoo have required one from bulk senders since February 2024. "
                "Start with a TXT record at _dmarc." + domain + ": 'v=DMARC1; p=none;'",
            )
        )
    else:
        policy = "none"
        for part in dmarc[0].split(";"):
            key, _, value = part.strip().partition("=")
            if key.strip().casefold() == "p":
                policy = value.strip().casefold()
        if policy == "none":
            findings.append(
                DnsFinding(
                    "info",
                    f"DMARC found for {domain}, policy p=none (monitor only).",
                    "Once you have been monitoring a while, move to p=quarantine.",
                )
            )
        else:
            findings.append(DnsFinding("info", f"DMARC found for {domain}, policy p={policy}."))

    # -- DKIM --------------------------------------------------------------
    found_selector = None
    for selector in _DKIM_SELECTORS:
        try:
            records = _txt_records(f"{selector}._domainkey.{domain}", timeout)
        except dns.exception.DNSException:
            continue
        if any("p=" in r for r in records):
            found_selector = selector
            break

    if found_selector:
        findings.append(
            DnsFinding("info", f"DKIM found for {domain} (selector '{found_selector}').")
        )
    else:
        findings.append(
            DnsFinding(
                "info",
                f"No DKIM key found for {domain} under the usual selectors.",
                "This may be a false alarm — DKIM selectors cannot be discovered from the "
                "domain alone. Confirm signing is enabled with your mail provider.",
            )
        )

    return tuple(findings)
