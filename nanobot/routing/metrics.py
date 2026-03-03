"""Routing metrics — tracks model routing decisions per day in SQLite."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


# All metric buckets tracked daily
METRIC_KEYS = [
    "downroute_to_haiku",    # expected >= sonnet, routed to haiku
    "downroute_to_sonnet",   # expected == opus,  routed to sonnet
    "uproute_to_sonnet",     # expected == haiku, routed to sonnet
    "uproute_to_opus",       # expected <= sonnet, routed to opus
    "stayed_haiku",          # expected haiku, routed haiku
    "stayed_sonnet",         # expected sonnet, routed sonnet
    "stayed_opus",           # expected opus,  routed opus
]

_DDL = """
CREATE TABLE IF NOT EXISTS routing_metrics (
    day         TEXT NOT NULL,
    metric      TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, metric)
);
"""


class RoutingMetrics:
    """Thread-safe (SQLite WAL) daily counters for routing decisions."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with self._conn() as conn:
            conn.execute(_DDL)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def record(self, metric: str) -> None:
        """Increment a daily counter."""
        if metric not in METRIC_KEYS:
            return
        today = date.today().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO routing_metrics (day, metric, count) VALUES (?, ?, 1) "
                "ON CONFLICT(day, metric) DO UPDATE SET count = count + 1",
                (today, metric),
            )

    def today(self) -> dict[str, int]:
        """Return all metric counts for today."""
        today = date.today().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT metric, count FROM routing_metrics WHERE day = ?", (today,)
            ).fetchall()
        result = {k: 0 for k in METRIC_KEYS}
        for metric, count in rows:
            result[metric] = count
        return result

    def summary(self, days: int = 7) -> list[dict]:
        """Return per-day metrics for the last N days."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT day, metric, count FROM routing_metrics "
                "ORDER BY day DESC LIMIT ?",
                (days * len(METRIC_KEYS),),
            ).fetchall()
        by_day: dict[str, dict[str, int]] = {}
        for day, metric, count in rows:
            by_day.setdefault(day, {k: 0 for k in METRIC_KEYS})[metric] = count
        return [{"day": d, **counts} for d, counts in sorted(by_day.items(), reverse=True)]

    def format_today(self) -> str:
        """Return a human-readable summary of today's routing stats."""
        stats = self.today()
        total = sum(stats.values())
        if total == 0:
            return "No routing decisions today."
        lines = ["**Routing stats (today)**", ""]
        lines.append(f"Total routed: {total}")
        lines.append("")
        lines.append("*Down-routes (cheaper model)*")
        lines.append(f"  → haiku:  {stats['downroute_to_haiku']}")
        lines.append(f"  → sonnet: {stats['downroute_to_sonnet']}")
        lines.append("")
        lines.append("*Up-routes (more capable model)*")
        lines.append(f"  → sonnet: {stats['uproute_to_sonnet']}")
        lines.append(f"  → opus:   {stats['uproute_to_opus']}")
        lines.append("")
        lines.append("*Stayed at expected model*")
        lines.append(f"  haiku:  {stats['stayed_haiku']}")
        lines.append(f"  sonnet: {stats['stayed_sonnet']}")
        lines.append(f"  opus:   {stats['stayed_opus']}")
        return "\n".join(lines)
