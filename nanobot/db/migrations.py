"""Database schema and migrations for nanobot SQLite session store."""

from __future__ import annotations

import sqlite3

# Whether FTS5 is available in this SQLite build.
FTS5_AVAILABLE: bool = False

_CURRENT_VERSION = 1

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    personality TEXT NOT NULL DEFAULT 'mac',
    created_at TEXT NOT NULL,
    last_consolidated_seq INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    UNIQUE(channel, chat_id, personality)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER DEFAULT 0,
    tools_used TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(session_id, seq)
);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    kind TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    earliest_at TEXT NOT NULL,
    latest_at TEXT NOT NULL,
    descendant_count INTEGER DEFAULT 0,
    descendant_token_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summary_messages (
    summary_id INTEGER REFERENCES summaries(id),
    message_id INTEGER REFERENCES messages(id),
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (summary_id, message_id)
);

CREATE TABLE IF NOT EXISTS summary_parents (
    summary_id INTEGER REFERENCES summaries(id),
    parent_id INTEGER REFERENCES summaries(id),
    PRIMARY KEY (summary_id, parent_id)
);

CREATE TABLE IF NOT EXISTS context_items (
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ordinal REAL NOT NULL,
    item_type TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    token_count INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, ordinal)
);
"""

_FTS5_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(content, content=messages, content_rowid=id);
"""


def _detect_fts5(conn: sqlite3.Connection) -> bool:
    """Return True if FTS5 is available in the linked SQLite library."""
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts5_probe")
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending schema migrations to *conn*.

    Idempotent — safe to call on an already-migrated database.

    Args:
        conn: Open SQLite connection (WAL mode + foreign keys already enabled).
    """
    global FTS5_AVAILABLE

    # Determine current schema version.
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current_version = row["version"] if row else 0
    except sqlite3.OperationalError:
        # schema_version table doesn't exist yet.
        current_version = 0

    if current_version >= _CURRENT_VERSION:
        # Detect FTS5 availability even when schema is already up-to-date.
        FTS5_AVAILABLE = _detect_fts5(conn)
        return

    # --- Apply V1 ---
    for statement in _SCHEMA_V1.strip().split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)

    # FTS5 — graceful fallback.
    FTS5_AVAILABLE = _detect_fts5(conn)
    if FTS5_AVAILABLE:
        conn.execute(_FTS5_DDL.strip())

    # Record version.
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (_CURRENT_VERSION,))
    conn.commit()
