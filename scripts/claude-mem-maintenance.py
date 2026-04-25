#!/usr/bin/env python3
"""
claude-mem-maintenance.py — Daily gap-fill and cleanup for claude-mem.

Runs at 2:30pm via com.keithmackay1.claude-mem-maintenance LaunchAgent.

Tasks:
  1. DISCOVER new Claude Code project directories not yet in PROJECT_DIR_MAP
     → logs them for manual review (can't auto-add without knowing project name)
  2. IMPORT unprocessed JSONL sessions → SQLite for all known projects
  3. EMBED unindexed SQLite rows → ChromaDB (cm__nanobot)
  4. CLEANUP JSONL entries older than CLEANUP_AGE_DAYS that are confirmed captured
     in both SQLite and ChromaDB (deletes the file if all sessions in it are safe)

Usage:
  python3 scripts/claude-mem-maintenance.py [--dry-run] [--skip-embed] [--skip-cleanup]

Requirements for embed step:
  - chromadb installed (or use uvx --with chromadb)
  - Ollama reachable at OLLAMA_BASE_URL with nomic-embed-text
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────

HOME = Path.home()
CLAUDE_PROJECTS_DIR = HOME / ".claude/projects"
CLAUDE_MEM_DB = HOME / ".claude-mem/claude-mem.db"
CHROMA_DIR = HOME / ".claude-mem/vector-db"
COLLECTION = "cm__nanobot"
LOG_FILE = HOME / ".nanobot/logs/claude-mem-maintenance.log"

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://192.168.86.21:11434") + "/api/embed"
EMBED_MODEL = "nomic-embed-text"
MAX_CHARS = 2000
EMBED_BATCH_SIZE = 50
EMBED_TIMEOUT = 60

CLEANUP_AGE_DAYS = 7  # only delete JSONL files older than this

# ── MemPalace config ────────────────────────────────────────────────────────────
# Directories to mine into the MemPalace palace (project files and vault notes).
# Conversation history is handled by our own JSONL pipeline above; MemPalace
# mining here is for code, docs, and markdown notes only.
MEMPALACE_MINE_TARGETS: list[Path] = [
    HOME / "Projects/nanobot",
    HOME / "KeithVault",
]

# Known slug → project name. The maintenance script uses this to:
#   a) import JSONL for known projects
#   b) detect unknown slugs (new projects)
KNOWN_PROJECTS: dict[str, str] = {
    "-Users-keithmackay1-Projects-nanobot":             "nanobot",
    "-Users-keithmackay1--openclaw-workspace":          "openclaw",
    "-Users-keithmackay1-Projects-openclaw":            "openclaw-proj",
    "-Users-keithmackay1":                              "user-root",
    "-Users-keithmackay1-Projects":                     "projects-root",
    "-Users-keithmackay1-Projects--foo":                "foo",
    "-Users-keithmackay1-Projects-n8n":                 "n8n",
    "-Users-keithmackay1-Projects-memvault":            "memvault",
    "-Users-keithmackay1-Projects-sec-seer":            "sec-seer",
    "-Users-keithmackay1-Projects-home-assistant":      "home-assistant",
    "-Users-keithmackay1-Projects-writing":             "writing",
    "-Users-keithmackay1-Projects-tinyclaw":            "tinyclaw",
    "-Users-keithmackay1-Projects-iswear":              "iswear",
    "-Users-keithmackay1-Projects-embedhub":            "embedhub",
    "-Users-keithmackay1-Projects-autoresearch-macos":  "autoresearch",
    "-Users-keithmackay1-Projects-admin":               "admin",
}

# Projects whose SQLite content gets embedded into cm__nanobot
EMBED_PROJECTS = list(KNOWN_PROJECTS.values())

# ── Logging ────────────────────────────────────────────────────────────────────

_log_lines: list[str] = []

def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    _log_lines.append(line)

def flush_log() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        for line in _log_lines:
            f.write(line + "\n")
    _log_lines.clear()


# ── JSONL parsing (shared with import_jsonl_to_sqlite.py logic) ────────────────

def extract_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [b.get("text", "").strip() for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n\n".join(p for p in parts if p).strip()
    return ""


def ts_to_epoch(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def parse_jsonl(path: Path) -> dict | None:
    session_id = path.stem
    messages = []
    try:
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
                if not text:
                    continue
                if role == "user" and isinstance(content, list):
                    if all(isinstance(b, dict) and b.get("type") == "tool_result"
                           for b in content if isinstance(b, dict)):
                        continue
                ts = entry.get("timestamp")
                messages.append({
                    "role": role, "text": text,
                    "timestamp": ts, "epoch": ts_to_epoch(ts),
                })
    except Exception as e:
        log(f"  [parse error {path.name}] {e}")
        return None

    if not messages:
        return None

    user_turns = [m for m in messages if m["role"] == "user"]
    asst_turns = [m for m in messages if m["role"] == "assistant"]
    return {
        "session_id": session_id,
        "started_at": messages[0]["timestamp"],
        "started_at_epoch": messages[0]["epoch"],
        "completed_at": messages[-1]["timestamp"],
        "completed_at_epoch": messages[-1]["epoch"],
        "user_turns": user_turns,
        "asst_turns": asst_turns,
    }


# ── Step 1: Discover new project directories ───────────────────────────────────

def discover_new_projects() -> list[str]:
    """Return slugs of project dirs that have JSONL files but aren't in KNOWN_PROJECTS."""
    unknown = []
    if not CLAUDE_PROJECTS_DIR.exists():
        return unknown
    for d in CLAUDE_PROJECTS_DIR.iterdir():
        if not d.is_dir():
            continue
        slug = d.name
        if slug in KNOWN_PROJECTS:
            continue
        jsonl_files = list(d.glob("*.jsonl"))
        if jsonl_files:
            unknown.append(slug)
    return sorted(unknown)


# ── Step 2: Import JSONL → SQLite ──────────────────────────────────────────────

def import_project(db: sqlite3.Connection, project: str, jsonl_dir: Path, dry_run: bool) -> dict:
    """Import all JSONL files for one project into SQLite. Returns counts."""
    files = sorted(jsonl_dir.glob("*.jsonl"))
    totals = {"sessions": 0, "user_prompts": 0, "asst_responses": 0, "skipped": 0}

    for f in files:
        session = parse_jsonl(f)
        if session is None:
            continue
        sid = session["session_id"]

        row = db.execute(
            "SELECT id FROM sdk_sessions WHERE claude_session_id=?", (sid,)
        ).fetchone()
        if row:
            existing = db.execute(
                "SELECT count(*) FROM user_prompts WHERE claude_session_id=?", (sid,)
            ).fetchone()[0]
            if existing > 0:
                totals["skipped"] += 1
                continue

        if not dry_run:
            db.execute(
                """INSERT INTO sdk_sessions
                   (claude_session_id, sdk_session_id, project, started_at, started_at_epoch,
                    completed_at, completed_at_epoch, status, prompt_counter)
                   VALUES (?,?,?,?,?,?,?,'completed',?)
                   ON CONFLICT(claude_session_id) DO UPDATE SET
                     completed_at=excluded.completed_at,
                     completed_at_epoch=excluded.completed_at_epoch,
                     status='completed',
                     prompt_counter=excluded.prompt_counter""",
                (sid, sid, project,
                 session["started_at"], session["started_at_epoch"],
                 session["completed_at"], session["completed_at_epoch"],
                 len(session["user_turns"])),
            )
            existing_up = set(r[0] for r in db.execute(
                "SELECT prompt_number FROM user_prompts WHERE claude_session_id=?", (sid,)))
            for i, t in enumerate(session["user_turns"], 1):
                if i not in existing_up:
                    db.execute(
                        "INSERT OR IGNORE INTO user_prompts (claude_session_id, prompt_number, prompt_text, created_at, created_at_epoch) VALUES (?,?,?,?,?)",
                        (sid, i, t["text"], t["timestamp"], t["epoch"]))
                    totals["user_prompts"] += 1
            existing_ar = set(r[0] for r in db.execute(
                "SELECT prompt_number FROM assistant_responses WHERE claude_session_id=?", (sid,)))
            for i, t in enumerate(session["asst_turns"], 1):
                if i not in existing_ar:
                    db.execute(
                        "INSERT OR IGNORE INTO assistant_responses (claude_session_id, prompt_number, response_text, created_at, created_at_epoch) VALUES (?,?,?,?,?)",
                        (sid, i, t["text"], t["timestamp"], t["epoch"]))
                    totals["asst_responses"] += 1
        else:
            totals["user_prompts"] += len(session["user_turns"])
            totals["asst_responses"] += len(session["asst_turns"])

        totals["sessions"] += 1

    return totals


# ── Step 3: Embed SQLite rows → ChromaDB ──────────────────────────────────────

def embed_batch(texts: list[str]) -> list[list[float]] | None:
    truncated = [t[:MAX_CHARS] for t in texts]
    payload = json.dumps({"model": EMBED_MODEL, "input": truncated}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT) as resp:
            return json.loads(resp.read())["embeddings"]
    except Exception as e:
        log(f"  [embed error] {e}")
        return None


def embed_new_sqlite_docs(dry_run: bool) -> int:
    """Embed user_prompts + assistant_responses not yet in ChromaDB."""
    try:
        import chromadb
    except ImportError:
        log("  [skip embed] chromadb not installed")
        return 0

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(
        name=COLLECTION, metadata={"embedding_model": EMBED_MODEL})

    # Fetch all existing IDs
    existing_ids: set[str] = set()
    offset = 0
    while True:
        r = collection.get(limit=1000, offset=offset, include=[])
        ids = r.get("ids", [])
        if not ids:
            break
        existing_ids.update(ids)
        offset += 1000

    db = sqlite3.connect(str(CLAUDE_MEM_DB))
    db.row_factory = sqlite3.Row

    docs = []
    for project in EMBED_PROJECTS:
        sessions = db.execute(
            "SELECT claude_session_id FROM sdk_sessions WHERE project=?", (project,)
        ).fetchall()
        for row in sessions:
            sid = row["claude_session_id"]
            short = sid.replace(":", "_")[:12]

            for prow in db.execute(
                "SELECT prompt_number, prompt_text, created_at FROM user_prompts WHERE claude_session_id=? ORDER BY prompt_number",
                (sid,)
            ):
                doc_id = f"sqlite_{project}_{short}_user_{prow['prompt_number']}"
                if doc_id in existing_ids:
                    continue
                text = (prow["prompt_text"] or "").strip()
                if not text or len(text) < 10:
                    continue
                docs.append({"id": doc_id, "text": text, "metadata": {
                    "doc_type": "user_prompt", "project": project,
                    "session_id": sid[:16], "source": "sqlite",
                    "timestamp": prow["created_at"] or ""}})

            for arow in db.execute(
                "SELECT prompt_number, response_text, created_at FROM assistant_responses WHERE claude_session_id=? ORDER BY prompt_number",
                (sid,)
            ):
                doc_id = f"sqlite_{project}_{short}_asst_{arow['prompt_number']}"
                if doc_id in existing_ids:
                    continue
                text = (arow["response_text"] or "").strip()
                if not text or len(text) < 10:
                    continue
                docs.append({"id": doc_id, "text": text, "metadata": {
                    "doc_type": "assistant_response", "project": project,
                    "session_id": sid[:16], "source": "sqlite",
                    "timestamp": arow["created_at"] or ""}})

    db.close()
    log(f"  {len(docs)} docs to embed across {len(EMBED_PROJECTS)} projects")

    if dry_run:
        log(f"  [dry-run] Would embed {len(docs)} docs")
        return len(docs)

    added = 0
    for i in range(0, len(docs), EMBED_BATCH_SIZE):
        batch = docs[i:i + EMBED_BATCH_SIZE]
        embeddings = embed_batch([d["text"] for d in batch])
        if embeddings is None:
            log(f"  [skip batch {i}] embed failed")
            continue
        try:
            collection.upsert(
                ids=[d["id"] for d in batch],
                documents=[d["text"] for d in batch],
                embeddings=embeddings,
                metadatas=[d["metadata"] for d in batch],
            )
            added += len(batch)
        except Exception as e:
            log(f"  [upsert error batch {i}] {e}")

    log(f"  ✓ Embedded {added} docs → {COLLECTION} (now {collection.count():,})")
    return added


# ── Step 4: Cleanup old JSONL files ───────────────────────────────────────────

def is_session_captured(db: sqlite3.Connection, session_id: str,
                         chroma_ids: set[str], project: str) -> bool:
    """Return True if this session_id has rows in SQLite AND at least one ChromaDB vector."""
    # SQLite check
    up_count = db.execute(
        "SELECT count(*) FROM user_prompts WHERE claude_session_id=?", (session_id,)
    ).fetchone()[0]
    if up_count == 0:
        return False

    # ChromaDB check: look for any sqlite_{project}_{session_id[:12]}_ prefix
    short = session_id.replace(":", "_")[:12]
    prefix = f"sqlite_{project}_{short}_"
    if not any(cid.startswith(prefix) for cid in chroma_ids):
        return False

    return True


def cleanup_old_jsonl(dry_run: bool) -> dict:
    """Delete JSONL files older than CLEANUP_AGE_DAYS where all sessions are captured."""
    cutoff = time.time() - CLEANUP_AGE_DAYS * 86400
    counts = {"checked": 0, "deleted": 0, "kept_not_captured": 0, "kept_too_new": 0}

    # Load ChromaDB IDs once
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        collection = client.get_or_create_collection(name=COLLECTION,
                                                      metadata={"embedding_model": EMBED_MODEL})
        chroma_ids: set[str] = set()
        offset = 0
        while True:
            r = collection.get(limit=1000, offset=offset, include=[])
            ids = r.get("ids", [])
            if not ids:
                break
            chroma_ids.update(ids)
            offset += 1000
    except Exception as e:
        log(f"  [cleanup] Cannot load ChromaDB IDs: {e} — skipping cleanup")
        return counts

    db = sqlite3.connect(str(CLAUDE_MEM_DB))

    for slug, project in KNOWN_PROJECTS.items():
        project_dir = CLAUDE_PROJECTS_DIR / slug
        if not project_dir.exists():
            continue
        for f in project_dir.glob("*.jsonl"):
            counts["checked"] += 1
            if f.stat().st_mtime >= cutoff:
                counts["kept_too_new"] += 1
                continue
            # Check if captured
            session_id = f.stem
            if not is_session_captured(db, session_id, chroma_ids, project):
                counts["kept_not_captured"] += 1
                continue
            # Safe to delete
            if not dry_run:
                f.unlink()
                log(f"  [cleanup] Deleted {f.name} ({project})")
            counts["deleted"] += 1

    db.close()
    return counts


# ── Step 5: Mine MemPalace ─────────────────────────────────────────────────────

def mine_mempalace(dry_run: bool) -> None:
    """Mine project files and vault notes into the MemPalace palace.

    Uses the default mining mode (project files + markdown docs).
    Skips targets that don't exist or if mempalace is not on PATH.
    """
    if not shutil.which("mempalace"):
        log("  [skip] mempalace not in PATH — install with: pip install mempalace")
        return

    for target in MEMPALACE_MINE_TARGETS:
        if not target.exists():
            log(f"  [skip] {target} — does not exist")
            continue
        cmd = ["mempalace", "mine", str(target)]
        if dry_run:
            log(f"  [dry-run] Would run: {' '.join(cmd)}")
            continue
        log(f"  Mining {target.name}...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            summary = (result.stdout or result.stderr or "").strip()
            summary_line = summary.splitlines()[0][:120] if summary else "(no output)"
            if result.returncode == 0:
                log(f"  ✓ {target.name}: {summary_line}")
            else:
                log(f"  ✗ {target.name} (exit {result.returncode}): {summary_line}")
        except subprocess.TimeoutExpired:
            log(f"  ✗ {target.name}: timed out after 300s")
        except Exception as e:
            log(f"  ✗ {target.name}: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Show what would happen, no writes")
    ap.add_argument("--skip-embed", action="store_true", help="Skip ChromaDB embedding step")
    ap.add_argument("--skip-cleanup", action="store_true", help="Skip JSONL cleanup step")
    ap.add_argument("--skip-mempalace", action="store_true", help="Skip MemPalace mining step")
    args = ap.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    log(f"claude-mem-maintenance — {mode}")
    log(f"  DB: {CLAUDE_MEM_DB}")
    log(f"  ChromaDB: {CHROMA_DIR}/{COLLECTION}")
    log(f"  Ollama: {OLLAMA_URL}")
    log(f"  Cleanup age: {CLEANUP_AGE_DAYS} days")
    log("")

    # ── 1. Discover new projects ──
    log("── Step 1: Discover new project directories ──")
    unknown = discover_new_projects()
    if unknown:
        log(f"  ⚠ {len(unknown)} unknown project dir(s) with JSONL files — add to KNOWN_PROJECTS:")
        for slug in unknown:
            n = len(list((CLAUDE_PROJECTS_DIR / slug).glob("*.jsonl")))
            log(f"    {slug}  ({n} files)")
    else:
        log("  ✓ No unknown project directories")
    log("")

    # ── 2. Import JSONL → SQLite ──
    log("── Step 2: Import JSONL sessions → SQLite ──")
    db = sqlite3.connect(str(CLAUDE_MEM_DB))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")

    total_new_sessions = 0
    total_new_prompts = 0
    total_new_responses = 0

    for slug, project in KNOWN_PROJECTS.items():
        project_dir = CLAUDE_PROJECTS_DIR / slug
        if not project_dir.exists():
            continue
        counts = import_project(db, project, project_dir, args.dry_run)
        if counts["sessions"] > 0:
            log(f"  {project}: +{counts['sessions']} sessions, "
                f"+{counts['user_prompts']} prompts, +{counts['asst_responses']} responses")
        total_new_sessions += counts["sessions"]
        total_new_prompts += counts["user_prompts"]
        total_new_responses += counts["asst_responses"]

    if not args.dry_run:
        db.commit()
    db.close()

    log(f"  Total: +{total_new_sessions} sessions, +{total_new_prompts} prompts, "
        f"+{total_new_responses} responses")
    log("")

    # ── 3. Embed → ChromaDB ──
    if not args.skip_embed:
        log("── Step 3: Embed new SQLite rows → ChromaDB ──")
        # Test Ollama reachability first
        test = embed_batch(["connectivity check"])
        if test is None:
            log(f"  ✗ Ollama not reachable at {OLLAMA_URL} — skipping embed")
        else:
            log(f"  ✓ Ollama reachable ({len(test[0])}-dim)")
            embed_new_sqlite_docs(args.dry_run)
    else:
        log("── Step 3: Embed skipped (--skip-embed) ──")
    log("")

    # ── 4. Cleanup old JSONL files ──
    if not args.skip_cleanup:
        log(f"── Step 4: Cleanup JSONL files older than {CLEANUP_AGE_DAYS} days ──")
        counts = cleanup_old_jsonl(args.dry_run)
        log(f"  Checked: {counts['checked']}  "
            f"Deleted: {counts['deleted']}  "
            f"Kept (not captured): {counts['kept_not_captured']}  "
            f"Kept (too new): {counts['kept_too_new']}")
    else:
        log("── Step 4: Cleanup skipped (--skip-cleanup) ──")
    log("")

    # ── 5. Mine MemPalace ──
    if not args.skip_mempalace:
        log("── Step 5: Mine MemPalace palace ──")
        mine_mempalace(args.dry_run)
    else:
        log("── Step 5: MemPalace mining skipped (--skip-mempalace) ──")
    log("")

    log("── Done ──")
    flush_log()


if __name__ == "__main__":
    main()
