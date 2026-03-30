#!/usr/bin/env python3
"""
Import Claude Code JSONL session files into claude-mem SQLite.

Populates:
  sdk_sessions     — one row per JSONL file (Claude Code session)
  user_prompts     — one row per user turn
  assistant_responses — one row per assistant turn (text only, skips tool/thinking)

Usage:
  python scripts/import_jsonl_to_sqlite.py [--dry-run] [--project nanobot]
  python scripts/import_jsonl_to_sqlite.py [--dry-run] --all

Environment / defaults:
  JSONL_DIR  — path to .claude/projects/<slug>/ dir (auto-detected from --project)
  DB_PATH    — path to claude-mem SQLite (default ~/.claude-mem/claude-mem.db)
"""

import argparse
import glob
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────

HOME = Path.home()
DEFAULT_DB = HOME / ".claude-mem" / "claude-mem.db"

# Maps a logical project name to the .claude/projects/ directory slug.
# The project name is stored in sdk_sessions.project.
PROJECT_DIR_MAP = {
    "nanobot":        HOME / ".claude/projects/-Users-keithmackay1-Projects-nanobot",
    "openclaw":       HOME / ".claude/projects/-Users-keithmackay1--openclaw-workspace",
    "openclaw-proj":  HOME / ".claude/projects/-Users-keithmackay1-Projects-openclaw",
    "user-root":      HOME / ".claude/projects/-Users-keithmackay1",
    "projects-root":  HOME / ".claude/projects/-Users-keithmackay1-Projects",
    "foo":            HOME / ".claude/projects/-Users-keithmackay1-Projects--foo",
    "n8n":            HOME / ".claude/projects/-Users-keithmackay1-Projects-n8n",
    "memvault":       HOME / ".claude/projects/-Users-keithmackay1-Projects-memvault",
    "sec-seer":       HOME / ".claude/projects/-Users-keithmackay1-Projects-sec-seer",
    "home-assistant": HOME / ".claude/projects/-Users-keithmackay1-Projects-home-assistant",
    "writing":        HOME / ".claude/projects/-Users-keithmackay1-Projects-writing",
    "tinyclaw":       HOME / ".claude/projects/-Users-keithmackay1-Projects-tinyclaw",
    "iswear":         HOME / ".claude/projects/-Users-keithmackay1-Projects-iswear",
    "embedhub":       HOME / ".claude/projects/-Users-keithmackay1-Projects-embedhub",
    "autoresearch":   HOME / ".claude/projects/-Users-keithmackay1-Projects-autoresearch-macos",
    "admin":          HOME / ".claude/projects/-Users-keithmackay1-Projects-admin",
}

# ── Helpers ────────────────────────────────────────────────────────────────

def extract_text(content) -> str:
    """Extract plain text from a message content field (str or list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text", "").strip()
                if txt:
                    parts.append(txt)
        return "\n\n".join(parts).strip()
    return ""


def ts_to_epoch(ts: str | None) -> int | None:
    """ISO timestamp → Unix epoch ms."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


# ── Core ───────────────────────────────────────────────────────────────────

def parse_jsonl(path: Path) -> dict:
    """Parse a JSONL file and return structured session data."""
    session_id = path.stem  # UUID filename without .jsonl
    messages = []

    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type")
            msg = entry.get("message", {})
            role = msg.get("role")

            if entry_type not in ("user", "assistant") or role not in ("user", "assistant"):
                continue

            content = msg.get("content", "")
            text = extract_text(content)

            # Skip tool_result / empty assistant frames
            if not text:
                continue

            # For user turns: skip lines that are purely tool_result blocks
            if role == "user" and isinstance(content, list):
                non_tool = [b for b in content if isinstance(b, dict) and b.get("type") not in ("tool_result",)]
                if not non_tool:
                    continue

            ts = entry.get("timestamp")
            messages.append({
                "role": role,
                "text": text,
                "timestamp": ts,
                "epoch": ts_to_epoch(ts),
                "uuid": entry.get("uuid"),
            })

    if not messages:
        return None

    started_at = messages[0]["timestamp"]
    started_epoch = messages[0]["epoch"]
    completed_at = messages[-1]["timestamp"]
    completed_epoch = messages[-1]["epoch"]

    user_turns = [m for m in messages if m["role"] == "user"]
    asst_turns = [m for m in messages if m["role"] == "assistant"]

    return {
        "session_id": session_id,
        "started_at": started_at,
        "started_at_epoch": started_epoch,
        "completed_at": completed_at,
        "completed_at_epoch": completed_epoch,
        "user_turns": user_turns,
        "asst_turns": asst_turns,
        "total_prompts": len(user_turns),
    }


def import_session(db: sqlite3.Connection, session: dict, project: str, dry_run: bool) -> dict:
    """Insert one session into SQLite. Returns counts."""
    sid = session["session_id"]

    # Check if session already exists
    row = db.execute(
        "SELECT id, prompt_counter FROM sdk_sessions WHERE claude_session_id = ?", (sid,)
    ).fetchone()

    counts = {"sessions": 0, "user_prompts": 0, "asst_responses": 0}

    if row:
        # Session exists — check if we have prompts already
        existing_prompts = db.execute(
            "SELECT count(*) FROM user_prompts WHERE claude_session_id = ?", (sid,)
        ).fetchone()[0]
        if existing_prompts > 0:
            return counts  # Already fully imported, skip

    if not dry_run:
        # Upsert session
        db.execute(
            """
            INSERT INTO sdk_sessions
              (claude_session_id, sdk_session_id, project, started_at, started_at_epoch,
               completed_at, completed_at_epoch, status, prompt_counter)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?)
            ON CONFLICT(claude_session_id) DO UPDATE SET
              completed_at = excluded.completed_at,
              completed_at_epoch = excluded.completed_at_epoch,
              status = 'completed',
              prompt_counter = excluded.prompt_counter
            """,
            (
                sid, sid, project,
                session["started_at"], session["started_at_epoch"],
                session["completed_at"], session["completed_at_epoch"],
                session["total_prompts"],
            ),
        )
        counts["sessions"] = 1

        # Insert user prompts (only if not already present)
        existing_nums = set(
            r[0]
            for r in db.execute(
                "SELECT prompt_number FROM user_prompts WHERE claude_session_id = ?", (sid,)
            )
        )
        for i, turn in enumerate(session["user_turns"], 1):
            if i in existing_nums:
                continue
            db.execute(
                """
                INSERT OR IGNORE INTO user_prompts
                  (claude_session_id, prompt_number, prompt_text, created_at, created_at_epoch)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sid, i, turn["text"], turn["timestamp"], turn["epoch"]),
            )
            counts["user_prompts"] += 1

        # Insert assistant responses
        existing_asst = set(
            r[0]
            for r in db.execute(
                "SELECT prompt_number FROM assistant_responses WHERE claude_session_id = ?", (sid,)
            )
        )
        for i, turn in enumerate(session["asst_turns"], 1):
            if i in existing_asst:
                continue
            db.execute(
                """
                INSERT OR IGNORE INTO assistant_responses
                  (claude_session_id, prompt_number, response_text, created_at, created_at_epoch)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sid, i, turn["text"], turn["timestamp"], turn["epoch"]),
            )
            counts["asst_responses"] += 1
    else:
        counts["sessions"] = 1
        counts["user_prompts"] = len(session["user_turns"])
        counts["asst_responses"] = len(session["asst_turns"])

    return counts


# ── Main ───────────────────────────────────────────────────────────────────

def run_project(project: str, jsonl_dir: Path, db: sqlite3.Connection, dry_run: bool) -> dict:
    """Import all JSONL files for one project. Returns totals dict."""
    files = sorted(jsonl_dir.rglob("*.jsonl"))
    print(f"\n── {project} ({len(files)} files, {jsonl_dir.name}) ──")

    totals = {"sessions": 0, "user_prompts": 0, "asst_responses": 0, "skipped": 0, "empty": 0}

    for i, f in enumerate(files):
        session = parse_jsonl(f)
        if session is None:
            totals["empty"] += 1
            continue

        counts = import_session(db, session, project, dry_run)

        if counts["sessions"] == 0 and counts["user_prompts"] == 0:
            totals["skipped"] += 1
        else:
            totals["sessions"] += counts["sessions"]
            totals["user_prompts"] += counts["user_prompts"]
            totals["asst_responses"] += counts["asst_responses"]

        if (i + 1) % 200 == 0:
            print(f"  [{i+1}/{len(files)}] sessions={totals['sessions']} "
                  f"prompts={totals['user_prompts']} responses={totals['asst_responses']} "
                  f"skipped={totals['skipped']}")
            if not dry_run:
                db.commit()

    if not dry_run:
        db.commit()

    print(f"  Done: +{totals['sessions']} sessions, +{totals['user_prompts']} prompts, "
          f"+{totals['asst_responses']} responses  ({totals['skipped']} skipped, {totals['empty']} empty)")
    return totals


def main():
    ap = argparse.ArgumentParser(description="Import Claude Code JSONL sessions into SQLite")
    ap.add_argument("--project", default=None, help="Project name (see PROJECT_DIR_MAP). Omit with --all.")
    ap.add_argument("--all", action="store_true", dest="all_projects", help="Import every project in PROJECT_DIR_MAP")
    ap.add_argument("--jsonl-dir", default=None, help="Override JSONL directory (only with --project)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be inserted, don't write")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    # Build the list of (project, dir) pairs to process
    if args.all_projects:
        pairs = [(p, d) for p, d in PROJECT_DIR_MAP.items() if d.exists()]
        skipped_dirs = [p for p, d in PROJECT_DIR_MAP.items() if not d.exists()]
        if skipped_dirs:
            print(f"Note: skipping {skipped_dirs} (dirs not found)")
    elif args.project:
        jsonl_dir = Path(args.jsonl_dir) if args.jsonl_dir else PROJECT_DIR_MAP.get(args.project)
        if not jsonl_dir or not jsonl_dir.exists():
            print(f"ERROR: JSONL directory not found: {jsonl_dir}", file=sys.stderr)
            sys.exit(1)
        pairs = [(args.project, jsonl_dir)]
    else:
        ap.print_help()
        sys.exit(1)

    print(f"import_jsonl_to_sqlite.py — {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"  DB: {db_path}")
    print(f"  Projects: {[p for p, _ in pairs]}")

    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")

    grand = {"sessions": 0, "user_prompts": 0, "asst_responses": 0, "skipped": 0, "empty": 0}

    for project, jsonl_dir in pairs:
        totals = run_project(project, jsonl_dir, db, args.dry_run)
        for k in grand:
            grand[k] += totals[k]

    db.close()

    print()
    print("══ Grand total ══")
    print(f"  New sessions:          {grand['sessions']}")
    print(f"  New user prompts:      {grand['user_prompts']}")
    print(f"  New asst responses:    {grand['asst_responses']}")
    print(f"  Already imported:      {grand['skipped']}")
    print(f"  Empty/no-text files:   {grand['empty']}")

    if not args.dry_run:
        db2 = sqlite3.connect(str(db_path))
        rows = db2.execute(
            "SELECT project, count(DISTINCT s.id), "
            "  (SELECT count(*) FROM user_prompts up WHERE up.claude_session_id IN (SELECT claude_session_id FROM sdk_sessions WHERE project=s.project)), "
            "  (SELECT count(*) FROM assistant_responses ar WHERE ar.claude_session_id IN (SELECT claude_session_id FROM sdk_sessions WHERE project=s.project)) "
            "FROM sdk_sessions s WHERE project IN ({}) GROUP BY project".format(
                ",".join("?" for _ in pairs)
            ),
            [p for p, _ in pairs]
        ).fetchall()
        db2.close()
        print()
        print(f"  {'Project':<20} {'Sessions':>8} {'Prompts':>8} {'Responses':>10}")
        print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*10}")
        for row in rows:
            print(f"  {row[0]:<20} {row[1]:>8} {row[2]:>8} {row[3]:>10}")


if __name__ == "__main__":
    main()
