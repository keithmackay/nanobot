"""SQLite-backed durable task store for long-running nanobot tasks."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaskRecord:
    id: str
    channel: str
    chat_id: str
    prompt: str
    status: str = "pending"       # pending | running | done | failed | cancelled | stale
    model: str | None = None      # Per-task model override
    poll_interval_s: int | None = None  # None = use global default
    message_id: str | None = None       # For reply threading
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    last_activity: str = ""
    result: str | None = None
    error: str | None = None


_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,
    chat_id         TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    model           TEXT,
    poll_interval_s INTEGER,
    message_id      TEXT,
    created_at      REAL NOT NULL,
    started_at      REAL,
    completed_at    REAL,
    last_activity   TEXT DEFAULT '',
    result          TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS tasks_created ON tasks(created_at);
"""


class TaskDB:
    """SQLite task store. All operations are synchronous; wrap in asyncio.to_thread if needed."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ------------------------------------------------------------------ writes

    def create(
        self,
        channel: str,
        chat_id: str,
        prompt: str,
        *,
        model: str | None = None,
        poll_interval_s: int | None = None,
        message_id: str | None = None,
    ) -> TaskRecord:
        rec = TaskRecord(
            id=uuid.uuid4().hex[:10],
            channel=channel,
            chat_id=chat_id,
            prompt=prompt,
            model=model,
            poll_interval_s=poll_interval_s,
            message_id=message_id,
        )
        self._conn.execute(
            "INSERT INTO tasks (id,channel,chat_id,prompt,status,model,poll_interval_s,"
            "message_id,created_at,last_activity) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rec.id, rec.channel, rec.chat_id, rec.prompt, rec.status,
             rec.model, rec.poll_interval_s, rec.message_id,
             rec.created_at, rec.last_activity),
        )
        self._conn.commit()
        return rec

    def mark_running(self, task_id: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status='running', started_at=? WHERE id=?",
            (time.time(), task_id),
        )
        self._conn.commit()

    def update_activity(self, task_id: str, activity: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET last_activity=? WHERE id=?",
            (activity[:500], task_id),
        )
        self._conn.commit()

    def finish(self, task_id: str, *, status: str, result: str | None = None, error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE tasks SET status=?, completed_at=?, result=?, error=? WHERE id=?",
            (status, time.time(), result, error, task_id),
        )
        self._conn.commit()

    def cancel(self, task_id: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status='cancelled', completed_at=? "
            "WHERE id=? AND status IN ('pending','running')",
            (time.time(), task_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------ reads

    def get(self, task_id: str) -> TaskRecord | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_record(row) if row else None

    def pending(self) -> list[TaskRecord]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at"
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def running(self) -> list[TaskRecord]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status='running' ORDER BY started_at"
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def recent(self, limit: int = 20) -> list[TaskRecord]:
        rows = self._conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def drain_stale(self) -> list[TaskRecord]:
        """Mark tasks stuck in 'running' as 'stale' (orphaned from a previous process)."""
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status='running'"
        ).fetchall()
        stale = [_row_to_record(r) for r in rows]
        if stale:
            self._conn.execute("UPDATE tasks SET status='stale' WHERE status='running'")
            self._conn.commit()
            for r in stale:
                r.status = 'stale'
        return stale

    def close(self) -> None:
        self._conn.close()


def _row_to_record(row: sqlite3.Row) -> TaskRecord:
    d = dict(row)
    fields = TaskRecord.__dataclass_fields__
    return TaskRecord(**{k: v for k, v in d.items() if k in fields})
