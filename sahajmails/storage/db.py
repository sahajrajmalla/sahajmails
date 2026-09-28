"""SQLite connection handling and schema migrations."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

from ..errors import ConfigError

__all__ = ["Database", "default_data_dir", "default_db_path"]

SCHEMA_VERSION = 1


def default_data_dir() -> Path:
    """Where sahajmails keeps its state.

    Honours ``SAHAJMAILS_HOME`` so tests and portable installs can redirect it.
    """
    override = os.environ.get("SAHAJMAILS_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sahajmails"


def default_db_path() -> Path:
    return default_data_dir() / "sahajmails.db"


_MIGRATIONS: tuple[str, ...] = (
    # -- v1 -------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS campaigns (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        subject      TEXT NOT NULL DEFAULT '',
        body         TEXT NOT NULL DEFAULT '',
        preheader    TEXT NOT NULL DEFAULT '',
        footer       TEXT NOT NULL DEFAULT '',
        config       TEXT NOT NULL DEFAULT '{}',
        contacts     TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        archived     INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS runs (
        id           TEXT PRIMARY KEY,
        campaign_id  TEXT,
        label        TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'running',
        total        INTEGER NOT NULL DEFAULT 0,
        started_at   TEXT NOT NULL,
        finished_at  TEXT,
        config       TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL
    );

    -- The ledger. One row per recipient per run, written immediately after
    -- each attempt so a hard kill loses at most the in-flight message.
    CREATE TABLE IF NOT EXISTS run_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id       TEXT NOT NULL,
        email        TEXT NOT NULL,
        status       TEXT NOT NULL,
        message_id   TEXT,
        error        TEXT,
        row          INTEGER NOT NULL DEFAULT 0,
        at           TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
    );

    -- Resume and duplicate detection both hit this constantly.
    CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_unique
        ON run_events(run_id, email);
    CREATE INDEX IF NOT EXISTS idx_run_events_email ON run_events(email, at);
    CREATE INDEX IF NOT EXISTS idx_run_events_status ON run_events(run_id, status);

    CREATE TABLE IF NOT EXISTS suppression (
        email     TEXT PRIMARY KEY,
        reason    TEXT NOT NULL DEFAULT 'manual',
        detail    TEXT NOT NULL DEFAULT '',
        added_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS settings (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ai_cache (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        model       TEXT NOT NULL DEFAULT '',
        tokens_in   INTEGER NOT NULL DEFAULT 0,
        tokens_out  INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL
    );

    -- Approved AI text, per run and recipient. Kept out of ai_cache because
    -- this is what the *user* signed off on, not what a model produced.
    CREATE TABLE IF NOT EXISTS ai_slots (
        campaign_id TEXT NOT NULL,
        email       TEXT NOT NULL,
        slot        TEXT NOT NULL,
        text        TEXT NOT NULL DEFAULT '',
        approved    INTEGER NOT NULL DEFAULT 0,
        generated   TEXT NOT NULL DEFAULT '',
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (campaign_id, email, slot)
    );
    """,
)


class Database:
    """A thread-safe wrapper around one SQLite file.

    SQLite handles concurrent readers fine but serialises writers. The send path
    writes one ledger row per message from potentially several worker threads,
    so writes are funnelled through a single lock rather than opening a
    connection per thread and fighting over the write lock.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ConfigError(
                    f"Could not create {self.path.parent}: {exc}",
                    hint="Set SAHAJMAILS_HOME to a writable directory.",
                ) from exc
        try:
            conn = sqlite3.connect(
                self.path,
                check_same_thread=False,
                isolation_level=None,  # explicit transactions
                timeout=30.0,
            )
        except sqlite3.Error as exc:
            raise ConfigError(f"Could not open {self.path}: {exc}") from exc

        conn.row_factory = sqlite3.Row
        # WAL lets the UI read progress while the sender is writing to it.
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _migrate(self) -> None:
        with self._lock:
            current = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            for version in range(current, len(_MIGRATIONS)):
                # executescript manages its own transaction — wrapping it in an
                # explicit BEGIN/COMMIT leaves nothing for the COMMIT to close.
                # Every statement is CREATE ... IF NOT EXISTS, so re-running a
                # partially applied migration is safe.
                self._conn.executescript(_MIGRATIONS[version])
                self._conn.execute(f"PRAGMA user_version = {version + 1}")

    # -- access -----------------------------------------------------------

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Exclusive transaction. Commits on success, rolls back on error."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def query_one(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._conn.execute(sql, params).fetchone()
        return row

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        with self.write() as conn:
            conn.execute(sql, params)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
