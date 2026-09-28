"""Data access.

Plain methods over :class:`~sahajmails.storage.db.Database`. Everything the
sender, the server and the CLI need to persist goes through here, so there is
exactly one place that knows the schema.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import SendResult, SendStatus
from .db import Database

__all__ = ["Repository", "new_id"]


def new_id(prefix: str = "") -> str:
    """Short, sortable-ish, collision-free identifier."""
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return (
        f"{prefix}{stamp}-{secrets.token_hex(3)}" if prefix else f"{stamp}-{secrets.token_hex(3)}"
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Repository:
    """Everything sahajmails persists."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- runs and the ledger ----------------------------------------------

    def create_run(
        self,
        *,
        total: int,
        campaign_id: str | None = None,
        label: str = "",
        config: Mapping[str, Any] | None = None,
    ) -> str:
        run_id = new_id("run-")
        self.db.execute(
            "INSERT INTO runs (id, campaign_id, label, status, total, started_at, config)"
            " VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (run_id, campaign_id, label, total, _now(), json.dumps(dict(config or {}))),
        )
        return run_id

    def finish_run(self, run_id: str, status: str = "completed") -> None:
        self.db.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, _now(), run_id),
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        return dict(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query(
            """
            SELECT r.*,
                   COALESCE(SUM(e.status = 'sent'), 0)   AS sent,
                   COALESCE(SUM(e.status = 'failed'), 0) AS failed
            FROM runs r
            LEFT JOIN run_events e ON e.run_id = r.id
            GROUP BY r.id
            ORDER BY r.started_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]

    def record(self, run_id: str, result: SendResult) -> None:
        """Append one ledger row.

        ``INSERT OR REPLACE`` rather than plain insert so a retry of the same
        recipient updates the outcome instead of violating the unique index.
        """
        self.db.execute(
            "INSERT OR REPLACE INTO run_events"
            " (run_id, email, status, message_id, error, row, at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                result.email,
                str(result.status),
                result.message_id,
                result.error,
                result.row,
                result.at.isoformat(),
            ),
        )

    def already_sent(self, run_id: str) -> set[str]:
        """Addresses this run has already delivered to.

        The basis of ``--resume``: anything in here is skipped, so restarting
        after a crash cannot double-send.
        """
        rows = self.db.query(
            "SELECT email FROM run_events WHERE run_id = ? AND status = 'sent'",
            (run_id,),
        )
        return {str(r["email"]) for r in rows}

    def run_results(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM run_events WHERE run_id = ? ORDER BY id", (run_id,))
        return [dict(r) for r in rows]

    def run_counts(self, run_id: str) -> dict[str, int]:
        rows = self.db.query(
            "SELECT status, COUNT(*) AS n FROM run_events WHERE run_id = ? GROUP BY status",
            (run_id,),
        )
        return {str(r["status"]): int(r["n"]) for r in rows}

    def failed_recipients(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM run_events WHERE run_id = ? AND status = 'failed' ORDER BY id",
            (run_id,),
        )
        return [dict(r) for r in rows]

    # -- duplicate detection ------------------------------------------------

    def recently_mailed(self, emails: Iterable[str], *, days: int = 30) -> dict[str, str]:
        """Addresses mailed successfully in the last ``days``.

        Powers the "12 of these people already heard from you this month"
        warning on the send confirmation — the single most common bulk-email
        mistake, and free to detect once there is a ledger.
        """
        candidates = [e.casefold() for e in emails]
        if not candidates:
            return {}
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

        found: dict[str, str] = {}
        # Chunked to stay under SQLite's variable limit on large lists.
        for start in range(0, len(candidates), 500):
            chunk = candidates[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self.db.query(
                f"SELECT email, MAX(at) AS last_at FROM run_events"  # noqa: S608 - placeholders only
                f" WHERE status = 'sent' AND at >= ? AND email IN ({placeholders})"
                f" GROUP BY email",
                (cutoff, *chunk),
            )
            found.update({str(r["email"]): str(r["last_at"]) for r in rows})
        return found

    # -- suppression --------------------------------------------------------

    def suppress(self, email: str, *, reason: str = "manual", detail: str = "") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO suppression (email, reason, detail, added_at)"
            " VALUES (?, ?, ?, ?)",
            (email.casefold(), reason, detail, _now()),
        )

    def suppress_many(self, entries: Sequence[tuple[str, str, str]]) -> int:
        """Bulk-add ``(email, reason, detail)`` triples. Returns rows written."""
        if not entries:
            return 0
        stamp = _now()
        with self.db.write() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO suppression (email, reason, detail, added_at)"
                " VALUES (?, ?, ?, ?)",
                [(e.casefold(), r, d, stamp) for e, r, d in entries],
            )
        return len(entries)

    def unsuppress(self, email: str) -> None:
        self.db.execute("DELETE FROM suppression WHERE email = ?", (email.casefold(),))

    def suppressed(self) -> set[str]:
        return {str(r["email"]) for r in self.db.query("SELECT email FROM suppression")}

    def list_suppressed(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM suppression ORDER BY added_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # -- campaigns ----------------------------------------------------------

    def save_campaign(self, campaign: Mapping[str, Any]) -> str:
        campaign_id = str(campaign.get("id") or new_id("cmp-"))
        now = _now()
        existing = self.db.query_one(
            "SELECT created_at FROM campaigns WHERE id = ?", (campaign_id,)
        )
        created = str(existing["created_at"]) if existing else now

        self.db.execute(
            "INSERT OR REPLACE INTO campaigns"
            " (id, name, subject, body, preheader, footer, config, contacts,"
            "  created_at, updated_at, archived)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id,
                str(campaign.get("name") or "Untitled campaign"),
                str(campaign.get("subject") or ""),
                str(campaign.get("body") or ""),
                str(campaign.get("preheader") or ""),
                str(campaign.get("footer") or ""),
                json.dumps(dict(campaign.get("config") or {})),
                str(campaign.get("contacts") or ""),
                created,
                now,
                int(bool(campaign.get("archived"))),
            ),
        )
        return campaign_id

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        if not row:
            return None
        data = dict(row)
        data["config"] = json.loads(data.get("config") or "{}")
        return data

    def list_campaigns(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM campaigns"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY updated_at DESC"
        out: list[dict[str, Any]] = []
        for row in self.db.query(sql):
            data = dict(row)
            data["config"] = json.loads(data.get("config") or "{}")
            out.append(data)
        return out

    def delete_campaign(self, campaign_id: str) -> None:
        self.db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))

    # -- settings -----------------------------------------------------------

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    def all_settings(self) -> dict[str, str]:
        return {str(r["key"]): str(r["value"]) for r in self.db.query("SELECT * FROM settings")}

    def delete_setting(self, key: str) -> None:
        self.db.execute("DELETE FROM settings WHERE key = ?", (key,))

    # -- ai --------------------------------------------------------------

    def cache_get(self, key: str) -> str | None:
        row = self.db.query_one("SELECT value FROM ai_cache WHERE key = ?", (key,))
        return str(row["value"]) if row else None

    def cache_put(
        self, key: str, value: str, *, model: str = "", tokens_in: int = 0, tokens_out: int = 0
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO ai_cache"
            " (key, value, model, tokens_in, tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, value, model, tokens_in, tokens_out, _now()),
        )

    def cache_clear(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM ai_cache")
        count = int(row["n"]) if row else 0
        self.db.execute("DELETE FROM ai_cache")
        return count

    def save_slots(
        self, campaign_id: str, entries: Sequence[tuple[str, str, str, bool, str]]
    ) -> None:
        """Persist ``(email, slot, text, approved, generated)`` rows."""
        if not entries:
            return
        stamp = _now()
        with self.db.write() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO ai_slots"
                " (campaign_id, email, slot, text, approved, generated, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (campaign_id, email, slot, text, int(approved), generated, stamp)
                    for email, slot, text, approved, generated in entries
                ],
            )

    def get_slots(self, campaign_id: str) -> dict[str, dict[str, str]]:
        """``{email: {slot: text}}`` for every approved slot in a campaign."""
        rows = self.db.query(
            "SELECT email, slot, text FROM ai_slots WHERE campaign_id = ?", (campaign_id,)
        )
        out: dict[str, dict[str, str]] = {}
        for row in rows:
            out.setdefault(str(row["email"]), {})[str(row["slot"])] = str(row["text"])
        return out

    def slot_rows(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM ai_slots WHERE campaign_id = ? ORDER BY email, slot", (campaign_id,)
        )
        return [dict(r) for r in rows]

    def clear_slots(self, campaign_id: str) -> None:
        self.db.execute("DELETE FROM ai_slots WHERE campaign_id = ?", (campaign_id,))


def result_from_row(row: Mapping[str, Any]) -> SendResult:
    """Rebuild a :class:`SendResult` from a ledger row."""
    return SendResult(
        email=str(row["email"]),
        status=SendStatus(str(row["status"])),
        message_id=row.get("message_id"),
        error=row.get("error"),
        at=datetime.fromisoformat(str(row["at"])),
        row=int(row.get("row") or 0),
    )
