"""Thread-safe singleton SQLite connection for nanobot."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

_lock = threading.Lock()
_connection: Optional[sqlite3.Connection] = None
_db_path: Optional[str] = None


def init_db(path: str) -> sqlite3.Connection:
    """Initialize the singleton SQLite connection at the given path.

    Must be called before any call to get_db().  Safe to call multiple times
    with the same path; subsequent calls are no-ops.

    Args:
        path: Filesystem path to the SQLite database file.  Tilde-expanded.

    Returns:
        The open sqlite3.Connection instance.
    """
    global _connection, _db_path

    expanded = str(Path(path).expanduser())

    with _lock:
        if _connection is not None:
            return _connection

        db_dir = Path(expanded).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(expanded, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Enable WAL for concurrent read performance.
        conn.execute("PRAGMA journal_mode=WAL")
        # Enforce foreign-key constraints.
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()

        _connection = conn
        _db_path = expanded

    return _connection


def get_db() -> sqlite3.Connection:
    """Return the active singleton connection.

    Raises:
        RuntimeError: If init_db() has not been called yet.
    """
    if _connection is None:
        raise RuntimeError(
            "SQLite connection has not been initialised.  "
            "Call nanobot.db.connection.init_db(path) first."
        )
    return _connection


def close_db() -> None:
    """Close and reset the singleton connection (primarily for tests)."""
    global _connection, _db_path

    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None
            _db_path = None
