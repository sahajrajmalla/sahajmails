"""Loading and cleaning contact lists.

Reads CSV and Excel into :class:`~sahajmails.models.Contact` objects using the
standard library plus ``openpyxl``. No pandas: this module only ever needs rows
of strings, and pandas costs ~130 MB to provide them.

Every problem found is reported with the source row number, because "row 47 has
no email" is actionable and "invalid input" is not.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from .errors import ContactsError
from .models import Contact, normalize_key

__all__ = [
    "EMAIL_COLUMN_CANDIDATES",
    "ContactList",
    "LoadReport",
    "is_valid_email",
    "load_contacts",
    "normalize_email",
]

#: Column names we will accept as "the email column", in preference order.
EMAIL_COLUMN_CANDIDATES: tuple[str, ...] = (
    "email",
    "email_address",
    "e_mail",
    "mail",
    "recipient",
    "to",
    "address",
)

# Deliberately pragmatic rather than RFC 5322-complete. A full RFC parser
# accepts addresses that no real mail server will route, and rejecting a
# valid-but-exotic address is worse for users than accepting a weird one:
# the SMTP server is the real arbiter. This catches typos, not edge cases.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)

_MAX_LOCAL_PART = 64
_MAX_ADDRESS = 254


def normalize_email(raw: str) -> str:
    """Strip display names and whitespace, and lower-case the address.

    ``'  "Alice" <Alice@Example.COM> '`` becomes ``'alice@example.com'``.

    Lower-casing the local part is technically lossy — RFC 5321 says it is
    case-sensitive — but no mail provider in practice treats it that way, and
    folding it is what makes deduplication behave the way users expect.
    """
    value = raw.strip()
    if not value:
        return ""
    # Pull the address out of "Display Name <addr>" without email.utils, which
    # silently returns ("", "") on some malformed inputs instead of raising.
    if "<" in value and ">" in value:
        start = value.rindex("<")
        end = value.index(">", start)
        value = value[start + 1 : end]
    return value.strip().strip("'\"").casefold()


def is_valid_email(address: str) -> bool:
    """Return whether ``address`` looks routable."""
    if not address or len(address) > _MAX_ADDRESS:
        return False
    local, _, _domain = address.partition("@")
    if not local or len(local) > _MAX_LOCAL_PART:
        return False
    if ".." in address:
        return False
    return _EMAIL_RE.match(address) is not None


@dataclass(slots=True)
class LoadReport:
    """What happened while reading a contact file.

    Kept separate from the contacts themselves so the UI can show "loaded 480 of
    500, here is what went wrong with the other 20" instead of either silently
    dropping rows or refusing the whole file.
    """

    invalid: list[tuple[int, str, str]] = field(default_factory=list)
    """``(row, raw_value, reason)`` for rows that could not be used."""

    duplicates: list[tuple[int, str]] = field(default_factory=list)
    """``(row, email)`` for rows dropped as repeats of an earlier row."""

    blank_rows: list[int] = field(default_factory=list)
    total_rows: int = 0

    @property
    def dropped(self) -> int:
        return len(self.invalid) + len(self.duplicates) + len(self.blank_rows)

    @property
    def clean(self) -> bool:
        return self.dropped == 0


@dataclass(slots=True)
class ContactList:
    """Contacts plus the column metadata a template needs."""

    contacts: list[Contact]
    columns: list[str]
    """Normalized column names, in file order."""

    labels: dict[str, str] = field(default_factory=dict)
    """Normalized name -> the label as it appeared in the file, for display."""

    email_column: str = "email"
    report: LoadReport = field(default_factory=LoadReport)
    source: str = ""

    def __len__(self) -> int:
        return len(self.contacts)

    def __iter__(self) -> Iterator[Contact]:
        return iter(self.contacts)

    def __bool__(self) -> bool:
        return bool(self.contacts)

    def label_for(self, column: str) -> str:
        return self.labels.get(column, column)

    def emails(self) -> list[str]:
        return [c.email for c in self.contacts]

    def filter_out(self, addresses: Iterable[str]) -> ContactList:
        """Return a copy without the given addresses (used for suppression)."""
        blocked = {normalize_email(a) for a in addresses}
        kept = [c for c in self.contacts if c.email not in blocked]
        return ContactList(
            contacts=kept,
            columns=self.columns,
            labels=self.labels,
            email_column=self.email_column,
            report=self.report,
            source=self.source,
        )


def _pick_email_column(columns: Sequence[str]) -> str:
    for candidate in EMAIL_COLUMN_CANDIDATES:
        if candidate in columns:
            return candidate
    # Fall back to any column that merely contains "email".
    for column in columns:
        if "email" in column or "mail" in column:
            return column
    raise ContactsError(
        "No email column found.",
        hint=(
            "Add a column named 'email'. Found: "
            + (", ".join(columns) if columns else "no columns at all")
        ),
    )


def _read_csv(data: bytes) -> tuple[list[str], list[list[str]]]:
    # utf-8-sig transparently eats the BOM Excel writes on export, which
    # otherwise turns the first header into "﻿email" and breaks matching.
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel on Windows still emits cp1252 for non-ASCII names.
        text = data.decode("cp1252", errors="replace")

    sample = text[:8192]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text, newline=""), dialect)
    rows = list(reader)
    if not rows:
        return [], []
    return [str(h) for h in rows[0]], [list(r) for r in rows[1:]]


def _read_xlsx(source: Path | IO[bytes]) -> tuple[list[str], list[list[str]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - openpyxl is a hard dep
        raise ContactsError(
            "Excel support is unavailable.", hint="Run: pip install openpyxl"
        ) from exc

    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        if sheet is None:
            return [], []
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            return [], []
        headers = ["" if c is None else str(c) for c in header_row]
        body = [["" if c is None else str(c) for c in row] for row in rows_iter]
        return headers, body
    finally:
        workbook.close()


def _coerce(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        # openpyxl hands back 1234.0 for an integer cell; "1234.0" in an email
        # body reads as a bug to the recipient.
        return str(int(value))
    return str(value).strip()


def load_contacts(
    source: str | Path | IO[bytes],
    *,
    filename: str | None = None,
    email_column: str | None = None,
    dedupe: bool = True,
) -> ContactList:
    """Read a CSV or Excel file into a :class:`ContactList`.

    Args:
        source: A path, or an open binary file object (as uploaded over HTTP).
        filename: Original name, used to pick the parser when ``source`` is a
            file object. Required in that case.
        email_column: Override the auto-detected email column.
        dedupe: Drop repeat addresses, keeping the first occurrence.

    Raises:
        ContactsError: The file is unreadable, empty, or has no email column.
    """
    if isinstance(source, str | Path):
        path = Path(source)
        if not path.exists():
            raise ContactsError(f"File not found: {path}", hint="Check the path and try again.")
        name = filename or path.name
        raw = path.read_bytes()
        stream: IO[bytes] | None = None
        origin = str(path)
    else:
        if not filename:
            raise ContactsError("filename is required when passing a file object.")
        name = filename
        raw = source.read()
        stream = io.BytesIO(raw)
        origin = filename

    suffix = Path(name).suffix.casefold()

    if suffix == ".xls":
        raise ContactsError(
            "The legacy .xls format is not supported.",
            hint="Open it in Excel or Numbers and save as .xlsx (or export to CSV).",
        )

    try:
        if suffix in {".xlsx", ".xlsm"}:
            headers, body = _read_xlsx(stream if stream is not None else Path(origin))
        elif suffix in {".csv", ".tsv", ".txt", ""}:
            headers, body = _read_csv(raw)
        else:
            raise ContactsError(
                f"Unsupported file type: {suffix or '(none)'}",
                hint="Use a .csv or .xlsx file.",
            )
    except ContactsError:
        raise
    except Exception as exc:
        raise ContactsError(f"Could not read {name}: {exc}", hint="Is the file corrupt?") from exc

    if not headers:
        raise ContactsError(f"{name} is empty.", hint="The first row must be column headers.")

    columns: list[str] = []
    labels: dict[str, str] = {}
    for index, header in enumerate(headers):
        key = normalize_key(header) or f"column_{index + 1}"
        # Two columns can normalize to the same key ("First Name" / "firstName").
        # Suffix rather than silently overwrite, so no data goes missing.
        if key in labels:
            key = f"{key}_{index + 1}"
        columns.append(key)
        labels[key] = header.strip() or key

    chosen = normalize_key(email_column) if email_column else _pick_email_column(columns)
    if chosen not in columns:
        raise ContactsError(
            f"Column {email_column!r} is not in this file.",
            hint="Available columns: " + ", ".join(labels.values()),
        )

    report = LoadReport(total_rows=len(body))
    contacts: list[Contact] = []
    seen: dict[str, int] = {}

    for offset, row in enumerate(body):
        row_number = offset + 2  # 1-based, and row 1 is the header

        values = [_coerce(cell) for cell in row]
        if not any(values):
            report.blank_rows.append(row_number)
            continue

        # Pad short rows rather than dropping them; trailing empty cells are
        # extremely common in hand-edited CSVs.
        if len(values) < len(columns):
            values.extend([""] * (len(columns) - len(values)))

        fields = dict(zip(columns, values, strict=False))
        address = normalize_email(fields.get(chosen, ""))

        if not address:
            report.invalid.append((row_number, fields.get(chosen, ""), "no email address"))
            continue
        if not is_valid_email(address):
            report.invalid.append((row_number, fields.get(chosen, ""), "not a valid address"))
            continue
        if dedupe and address in seen:
            report.duplicates.append((row_number, address))
            continue

        seen[address] = row_number
        fields[chosen] = address
        # Always expose the address as "email" too, so templates can rely on
        # {{ email }} regardless of what the column was actually called.
        fields.setdefault("email", address)
        contacts.append(Contact(email=address, fields=fields, row=row_number))

    if not contacts:
        raise ContactsError(
            f"No usable contacts in {name}.",
            hint=(
                f"Read {report.total_rows} row(s) but none had a valid address in "
                f"the {labels.get(chosen, chosen)!r} column."
            ),
        )

    if "email" not in columns:
        columns.append("email")
        labels.setdefault("email", "email")

    return ContactList(
        contacts=contacts,
        columns=columns,
        labels=labels,
        email_column=chosen,
        report=report,
        source=origin,
    )
