"""SQLite-backed session store for nanobot LCM (Lossless Context Management)."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from nanobot.db.connection import get_db, init_db
from nanobot.db.migrations import FTS5_AVAILABLE, run_migrations

if TYPE_CHECKING:
    from nanobot.config.schema import SessionStoreConfig


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Session:
    id: int
    channel: str
    chat_id: str
    personality: str
    created_at: str
    last_consolidated_seq: int
    metadata: dict


@dataclass
class Message:
    id: int
    session_id: int
    seq: int
    role: str
    content: str
    token_count: int
    tools_used: list
    created_at: str


@dataclass
class Summary:
    id: int
    session_id: int
    kind: str  # leaf | condensed
    depth: int
    content: str
    token_count: int
    earliest_at: str
    latest_at: str
    descendant_count: int
    descendant_token_count: int
    created_at: str


@dataclass
class ContextItem:
    session_id: int
    ordinal: float
    item_type: str  # message | summary
    item_id: int
    token_count: int


@dataclass
class SearchResult:
    item_type: str
    item_id: int
    content: str
    snippet: str
    session_id: int
    created_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _estimate_tokens(text: str) -> int:
    """Rough token count: word count * 1.3."""
    return max(1, int(len(text.split()) * 1.3))


def _snippet(text: str, max_chars: int = 200) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        channel=row["channel"],
        chat_id=row["chat_id"],
        personality=row["personality"],
        created_at=row["created_at"],
        last_consolidated_seq=row["last_consolidated_seq"] or 0,
        metadata=json.loads(row["metadata"] or "{}"),
    )


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        session_id=row["session_id"],
        seq=row["seq"],
        role=row["role"],
        content=row["content"],
        token_count=row["token_count"] or 0,
        tools_used=json.loads(row["tools_used"] or "[]"),
        created_at=row["created_at"],
    )


def _row_to_summary(row: sqlite3.Row) -> Summary:
    return Summary(
        id=row["id"],
        session_id=row["session_id"],
        kind=row["kind"],
        depth=row["depth"] or 0,
        content=row["content"],
        token_count=row["token_count"] or 0,
        earliest_at=row["earliest_at"],
        latest_at=row["latest_at"],
        descendant_count=row["descendant_count"] or 0,
        descendant_token_count=row["descendant_token_count"] or 0,
        created_at=row["created_at"],
    )


def _row_to_context_item(row: sqlite3.Row) -> ContextItem:
    return ContextItem(
        session_id=row["session_id"],
        ordinal=row["ordinal"],
        item_type=row["item_type"],
        item_id=row["item_id"],
        token_count=row["token_count"] or 0,
    )


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------


class SessionStore:
    """SQLite-backed session store with LCM support."""

    def __init__(self, config: "SessionStoreConfig") -> None:
        self._config = config
        db_path = str(Path(config.db_path).expanduser())
        init_db(db_path)
        conn = get_db()
        run_migrations(conn)
        logger.info("SessionStore initialised at {}", db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _conn(self) -> sqlite3.Connection:
        return get_db()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def get_or_create_session(
        self, channel: str, chat_id: str, personality: str = "mac"
    ) -> Session:
        """Return existing session or create a new one."""
        existing = self.get_session_by_key(channel, chat_id, personality)
        if existing is not None:
            return existing

        now = _now_iso()
        try:
            self._conn.execute(
                """
                INSERT INTO sessions (channel, chat_id, personality, created_at, metadata)
                VALUES (?, ?, ?, ?, '{}')
                """,
                (channel, chat_id, personality, now),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            # Race condition — another thread inserted first; fetch it.
            pass

        result = self.get_session_by_key(channel, chat_id, personality)
        if result is None:
            raise RuntimeError(
                f"Failed to get/create session for {channel}:{chat_id}:{personality}"
            )
        return result

    def get_session(self, session_id: int) -> Optional[Session]:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _row_to_session(row) if row else None

    def get_session_by_key(
        self, channel: str, chat_id: str, personality: str
    ) -> Optional[Session]:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE channel=? AND chat_id=? AND personality=?",
            (channel, chat_id, personality),
        ).fetchone()
        return _row_to_session(row) if row else None

    # ------------------------------------------------------------------
    # Message management
    # ------------------------------------------------------------------

    def append_message(
        self,
        session_id: int,
        role: str,
        content: str,
        token_count: int = 0,
        tools_used: Optional[list] = None,
    ) -> Message:
        """Append a message to a session and return the stored Message."""
        if tools_used is None:
            tools_used = []
        if token_count == 0:
            token_count = _estimate_tokens(content)

        now = _now_iso()
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?",
            (session_id,),
        )
        next_seq: int = cur.fetchone()[0]

        self._conn.execute(
            """
            INSERT INTO messages (session_id, seq, role, content, token_count, tools_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                next_seq,
                role,
                content,
                token_count,
                json.dumps(tools_used, ensure_ascii=False),
                now,
            ),
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT * FROM messages WHERE session_id=? AND seq=?",
            (session_id, next_seq),
        ).fetchone()
        return _row_to_message(row)

    def get_messages(self, message_ids: list[int]) -> list[Message]:
        if not message_ids:
            return []
        placeholders = ",".join("?" * len(message_ids))
        rows = self._conn.execute(
            f"SELECT * FROM messages WHERE id IN ({placeholders}) ORDER BY seq",
            message_ids,
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    def get_recent_messages(self, session_id: int, count: int) -> list[Message]:
        rows = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ?
            ORDER BY seq DESC
            LIMIT ?
            """,
            (session_id, count),
        ).fetchall()
        return [_row_to_message(r) for r in reversed(rows)]

    def get_all_messages(self, session_id: int) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    # ------------------------------------------------------------------
    # Context items
    # ------------------------------------------------------------------

    def get_context_items(self, session_id: int) -> list[ContextItem]:
        rows = self._conn.execute(
            "SELECT * FROM context_items WHERE session_id=? ORDER BY ordinal",
            (session_id,),
        ).fetchall()
        return [_row_to_context_item(r) for r in rows]

    def add_context_item(
        self,
        session_id: int,
        item_type: str,
        item_id: int,
        token_count: int,
    ) -> float:
        """Add a context item and return its assigned ordinal."""
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(ordinal), 0) FROM context_items WHERE session_id=?",
            (session_id,),
        )
        max_ordinal: float = cur.fetchone()[0] or 0.0
        ordinal = max_ordinal + 1.0

        self._conn.execute(
            """
            INSERT INTO context_items (session_id, ordinal, item_type, item_id, token_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, ordinal, item_type, item_id, token_count),
        )
        self._conn.commit()
        return ordinal

    def replace_context_items(
        self,
        session_id: int,
        old_ordinals: list[float],
        new_item_type: str,
        new_item_id: int,
        new_token_count: int,
    ) -> None:
        """Replace multiple context items with a single new one.

        The new item's ordinal is set to the midpoint of the first and last
        removed ordinals so that relative order is preserved.
        """
        if not old_ordinals:
            return

        sorted_ordinals = sorted(old_ordinals)
        mid_ordinal = (sorted_ordinals[0] + sorted_ordinals[-1]) / 2.0

        placeholders = ",".join("?" * len(old_ordinals))
        self._conn.execute(
            f"DELETE FROM context_items WHERE session_id=? AND ordinal IN ({placeholders})",
            [session_id, *old_ordinals],
        )
        self._conn.execute(
            """
            INSERT INTO context_items (session_id, ordinal, item_type, item_id, token_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, mid_ordinal, new_item_type, new_item_id, new_token_count),
        )
        self._conn.commit()

    def get_session_total_tokens(self, session_id: int) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(token_count), 0) FROM context_items WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Summary management
    # ------------------------------------------------------------------

    def insert_summary(
        self,
        session_id: int,
        kind: str,
        depth: int,
        content: str,
        token_count: int,
        earliest_at: str,
        latest_at: str,
        descendant_count: int = 0,
        descendant_token_count: int = 0,
    ) -> Summary:
        now = _now_iso()
        self._conn.execute(
            """
            INSERT INTO summaries
                (session_id, kind, depth, content, token_count,
                 earliest_at, latest_at, descendant_count, descendant_token_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                kind,
                depth,
                content,
                token_count,
                earliest_at,
                latest_at,
                descendant_count,
                descendant_token_count,
                now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM summaries WHERE rowid=last_insert_rowid()"
        ).fetchone()
        return _row_to_summary(row)

    def get_summary(self, summary_id: int) -> Optional[Summary]:
        row = self._conn.execute(
            "SELECT * FROM summaries WHERE id=?", (summary_id,)
        ).fetchone()
        return _row_to_summary(row) if row else None

    def link_summary_messages(self, summary_id: int, message_ids: list[int]) -> None:
        for ordinal, msg_id in enumerate(message_ids):
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO summary_messages (summary_id, message_id, ordinal) VALUES (?, ?, ?)",
                    (summary_id, msg_id, ordinal),
                )
            except sqlite3.IntegrityError:
                pass
        self._conn.commit()

    def link_summary_parents(self, summary_id: int, parent_ids: list[int]) -> None:
        for parent_id in parent_ids:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO summary_parents (summary_id, parent_id) VALUES (?, ?)",
                    (summary_id, parent_id),
                )
            except sqlite3.IntegrityError:
                pass
        self._conn.commit()

    def get_summary_messages(self, summary_id: int) -> list[Message]:
        rows = self._conn.execute(
            """
            SELECT m.* FROM messages m
            JOIN summary_messages sm ON sm.message_id = m.id
            WHERE sm.summary_id = ?
            ORDER BY sm.ordinal
            """,
            (summary_id,),
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    def get_summary_children(self, summary_id: int) -> list[Summary]:
        """Return summaries that have *summary_id* as a parent."""
        rows = self._conn.execute(
            """
            SELECT s.* FROM summaries s
            JOIN summary_parents sp ON sp.summary_id = s.id
            WHERE sp.parent_id = ?
            ORDER BY s.id
            """,
            (summary_id,),
        ).fetchall()
        return [_row_to_summary(r) for r in rows]

    def get_compactable_messages(
        self, session_id: int, fresh_tail_count: int
    ) -> list[Message]:
        """Return messages eligible for compaction (all except the *fresh_tail_count* most recent)."""
        rows = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ?
            ORDER BY seq DESC
            """,
            (session_id,),
        ).fetchall()
        all_msgs = [_row_to_message(r) for r in reversed(rows)]
        compactable = all_msgs[: max(0, len(all_msgs) - fresh_tail_count)]
        return compactable

    def get_compactable_summaries(self, session_id: int, depth: int) -> list[Summary]:
        """Return summaries at a given depth that are eligible for condensation."""
        rows = self._conn.execute(
            """
            SELECT * FROM summaries
            WHERE session_id = ? AND depth = ?
            ORDER BY id
            """,
            (session_id, depth),
        ).fetchall()
        return [_row_to_summary(r) for r in rows]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        mode: str = "full_text",
        scope: str = "both",
        limit: int = 20,
    ) -> list[SearchResult]:
        """Search messages and/or summaries.

        Args:
            query: Search string.
            mode: "full_text" (FTS5 or LIKE fallback) | "regex".
            scope: "messages" | "summaries" | "both".
            limit: Maximum total results.

        Returns:
            List of SearchResult ordered by relevance / recency.
        """
        results: list[SearchResult] = []

        if scope in ("messages", "both"):
            results.extend(self._search_messages(query, mode, limit))
        if scope in ("summaries", "both"):
            results.extend(self._search_summaries(query, mode, limit))

        # Deduplicate and trim.
        seen: set[tuple[str, int]] = set()
        unique: list[SearchResult] = []
        for r in results:
            key = (r.item_type, r.item_id)
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique[:limit]

    def _search_messages(
        self, query: str, mode: str, limit: int
    ) -> list[SearchResult]:
        results: list[SearchResult] = []

        if mode == "full_text" and FTS5_AVAILABLE:
            rows = self._conn.execute(
                """
                SELECT m.id, m.session_id, m.content, m.created_at
                FROM messages_fts fts
                JOIN messages m ON m.id = fts.rowid
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        elif mode == "regex":
            # Fetch all, filter in Python.
            rows = self._conn.execute(
                "SELECT id, session_id, content, created_at FROM messages"
            ).fetchall()
            try:
                pattern = re.compile(query, re.IGNORECASE)
                rows = [r for r in rows if pattern.search(r["content"])][:limit]
            except re.error:
                rows = []
        else:
            rows = self._conn.execute(
                """
                SELECT id, session_id, content, created_at
                FROM messages
                WHERE content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (f"%{query}%", limit),
            ).fetchall()

        for row in rows:
            results.append(
                SearchResult(
                    item_type="message",
                    item_id=row["id"],
                    content=row["content"],
                    snippet=_snippet(row["content"]),
                    session_id=row["session_id"],
                    created_at=row["created_at"],
                )
            )
        return results

    def _search_summaries(
        self, query: str, mode: str, limit: int
    ) -> list[SearchResult]:
        results: list[SearchResult] = []

        if mode == "regex":
            rows = self._conn.execute(
                "SELECT id, session_id, content, created_at FROM summaries"
            ).fetchall()
            try:
                pattern = re.compile(query, re.IGNORECASE)
                rows = [r for r in rows if pattern.search(r["content"])][:limit]
            except re.error:
                rows = []
        else:
            rows = self._conn.execute(
                """
                SELECT id, session_id, content, created_at
                FROM summaries
                WHERE content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (f"%{query}%", limit),
            ).fetchall()

        for row in rows:
            results.append(
                SearchResult(
                    item_type="summary",
                    item_id=row["id"],
                    content=row["content"],
                    snippet=_snippet(row["content"]),
                    session_id=row["session_id"],
                    created_at=row["created_at"],
                )
            )
        return results

    # ------------------------------------------------------------------
    # Describe
    # ------------------------------------------------------------------

    def describe(self, item_id: str) -> str:
        """Return a human-readable description of a message or summary.

        Args:
            item_id: "msg:42" or "sum:17".

        Returns:
            Multi-line string with content + lineage info.
        """
        parts = item_id.split(":", 1)
        if len(parts) != 2:
            return f"[unknown item_id format: {item_id!r}]"

        kind, id_str = parts
        try:
            numeric_id = int(id_str)
        except ValueError:
            return f"[invalid id: {id_str!r}]"

        if kind == "msg":
            msg = self.get_messages([numeric_id])
            if not msg:
                return f"[message {numeric_id} not found]"
            m = msg[0]
            lines = [
                f"Message id={m.id}  seq={m.seq}  role={m.role}  session={m.session_id}",
                f"created_at: {m.created_at}",
                f"tokens: {m.token_count}",
                "---",
                m.content,
            ]
            # Find parent summaries.
            parent_rows = self._conn.execute(
                """
                SELECT DISTINCT sm.summary_id FROM summary_messages sm
                WHERE sm.message_id = ?
                """,
                (numeric_id,),
            ).fetchall()
            if parent_rows:
                parent_ids = [r["summary_id"] for r in parent_rows]
                lines.append(f"covered by summaries: {parent_ids}")
            return "\n".join(lines)

        elif kind == "sum":
            s = self.get_summary(numeric_id)
            if s is None:
                return f"[summary {numeric_id} not found]"
            child_msgs = self.get_summary_messages(numeric_id)
            child_sums = self.get_summary_children(numeric_id)
            parent_rows = self._conn.execute(
                "SELECT parent_id FROM summary_parents WHERE summary_id=?",
                (numeric_id,),
            ).fetchall()
            lines = [
                f"Summary id={s.id}  kind={s.kind}  depth={s.depth}  session={s.session_id}",
                f"created_at: {s.created_at}",
                f"covers: {s.earliest_at} → {s.latest_at}",
                f"tokens: {s.token_count}  descendant_count: {s.descendant_count}",
            ]
            if parent_rows:
                lines.append(f"parent summaries: {[r['parent_id'] for r in parent_rows]}")
            if child_msgs:
                lines.append(f"covers {len(child_msgs)} message(s): {[m.id for m in child_msgs]}")
            if child_sums:
                lines.append(f"child summaries: {[c.id for c in child_sums]}")
            lines += ["---", s.content]
            return "\n".join(lines)

        return f"[unknown item type: {kind!r}]"

    # ------------------------------------------------------------------
    # JSONL migration
    # ------------------------------------------------------------------

    def migrate_from_jsonl(self, jsonl_dir: str) -> dict[str, Any]:
        """Migrate existing JSONL session files into SQLite.

        Scans *jsonl_dir* for ``*.jsonl`` files, parses each file according
        to the SessionManager format (first line = metadata, rest = messages),
        and inserts them into the SQLite database.

        Args:
            jsonl_dir: Path to the directory containing JSONL session files.

        Returns:
            dict with keys: migrated_sessions, migrated_messages, errors.
        """
        jsonl_path = Path(jsonl_dir).expanduser()
        stats: dict[str, Any] = {
            "migrated_sessions": 0,
            "migrated_messages": 0,
            "errors": [],
        }

        if not jsonl_path.exists():
            logger.warning("JSONL migration dir does not exist: {}", jsonl_path)
            return stats

        files = list(jsonl_path.glob("*.jsonl"))
        logger.info("JSONL migration: found {} file(s) in {}", len(files), jsonl_path)

        for filepath in files:
            try:
                messages_raw: list[dict] = []
                metadata: dict = {}

                with open(filepath, encoding="utf-8") as fh:
                    for line_no, line in enumerate(fh):
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("_type") == "metadata":
                            metadata = data
                        else:
                            messages_raw.append(data)

                # Derive channel / chat_id from the file's "key" field or filename.
                key = metadata.get("key") or filepath.stem.replace("_", ":", 1)
                parts = key.split(":", 1)
                channel = parts[0] if len(parts) >= 2 else "unknown"
                chat_id = parts[1] if len(parts) >= 2 else key
                personality = "mac"

                session = self.get_or_create_session(channel, chat_id, personality)

                # Skip sessions that already have messages.
                existing = self.get_all_messages(session.id)
                if existing:
                    logger.debug(
                        "Skipping already-migrated session {} ({} messages)",
                        key,
                        len(existing),
                    )
                    continue

                for msg in messages_raw:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if not content:
                        continue
                    ts = msg.get("timestamp", _now_iso())
                    # Estimate tokens.
                    tc = _estimate_tokens(str(content))
                    # Flatten content to str if it's a list (tool_result blocks etc.)
                    if isinstance(content, list):
                        content = json.dumps(content, ensure_ascii=False)
                    self.append_message(session.id, role, str(content), token_count=tc)
                    stats["migrated_messages"] += 1

                stats["migrated_sessions"] += 1
                logger.info("Migrated session {} ({} messages)", key, len(messages_raw))

            except Exception as exc:
                msg = f"Error migrating {filepath}: {exc}"
                logger.warning(msg)
                stats["errors"].append(msg)

        return stats

    # ------------------------------------------------------------------
    # Compatibility API (matches SessionManager interface)
    # ------------------------------------------------------------------

    def get_history(
        self,
        channel: str,
        chat_id: str,
        personality: str,
        max_messages: int = 500,
    ) -> list[dict[str, Any]]:
        """Return recent messages as dicts, compatible with the JSONL SessionManager API.

        Strips leading non-user messages to avoid orphaned tool_result blocks
        (mirrors SessionManager.get_history behaviour).
        """
        session = self.get_session_by_key(channel, chat_id, personality)
        if session is None:
            return []

        msgs = self.get_recent_messages(session.id, max_messages)

        # Drop leading non-user messages.
        for i, m in enumerate(msgs):
            if m.role == "user":
                msgs = msgs[i:]
                break

        out: list[dict[str, Any]] = []
        for m in msgs:
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tools_used:
                entry["tools_used"] = m.tools_used
            out.append(entry)
        return out

    def add_to_history(
        self,
        channel: str,
        chat_id: str,
        personality: str,
        role: str,
        content: str,
    ) -> None:
        """Append a message, creating the session if needed."""
        session = self.get_or_create_session(channel, chat_id, personality)
        self.append_message(session.id, role, content)
