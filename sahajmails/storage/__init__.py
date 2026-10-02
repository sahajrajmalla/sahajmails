"""Local persistence.

One SQLite file under ``~/.sahajmails/``. No ORM, no server, no migrations
framework — plain SQL and a version counter.

The important table is ``run_events``: an append-only row per recipient per run.
It is what makes a crashed batch resumable and what guarantees nobody is mailed
twice. 1.x kept no record at all, so a connection drop at contact 600 left you
with no way to find out who had already received the message.
"""

from __future__ import annotations

from .db import Database, default_db_path
from .repo import Repository

__all__ = ["Database", "Repository", "default_db_path"]
