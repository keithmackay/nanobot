#!/usr/bin/env python3
"""
backfill_all.py — Comprehensive ChromaDB backfill for all conversation sources.

Handles five source types:
  1. Claude Code JSONL gap — session files newer than what's in Chroma
     (fills the window when claude-mem worker was down)
  2. Nanobot Discord sessions — ~/.nanobot/workspace/sessions/discord_*.jsonl
     (includes channel_id → personality mapping from ~/.nanobot/config.json)
  3. Nanobot Telegram sessions — ~/.nanobot/workspace/sessions/telegram_*.jsonl
  4. SQLite multi-project — user_prompts + assistant_responses from all projects
     in claude-mem SQLite (user-root, foo, iswear, admin, home-assistant, etc.)
  5. KeithVault — Obsidian .md notes with obstagger frontmatter (context field
     present). Includes taxonomy metadata for filtered retrieval.

All sources go into cm__nanobot. OpenClaw Discord history is not recoverable from
files (sessions.json is state-only; per-session JSONLs were cleared).

Usage:
  python3 scripts/backfill_all.py [--dry-run] [--source all|jsonl|discord|telegram|sqlite|vault]
  python3 scripts/backfill_all.py --dry-run        # show counts, no writes
  python3 scripts/backfill_all.py --source vault   # only KeithVault notes

Requirements: chromadb (uvx --with chromadb python3 scripts/backfill_all.py)
"""

import argparse
import hashlib
import json
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

OLLAMA_URL = "http://192.168.86.35:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"
MAX_CHARS = 2000
BATCH_SIZE = 50
CHROMA_DIR = Path.home() / ".claude-mem/vector-db"
COLLECTION_NANOBOT = "cm__nanobot"

NANOBOT_SESSIONS_DIR = Path.home() / ".nanobot/workspace/sessions"
NANOBOT_CONFIG = Path.home() / ".nanobot/config.json"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude/projects"
CLAUDE_MEM_DB = Path.home() / ".claude-mem/claude-mem.db"
KEITHVAULT_DIR = Path.home() / "KeithVault"

# Min file mtime to consider for JSONL gap backfill (last known SQLite entry ~Mar 7)
JSONL_GAP_SINCE_EPOCH = 1741305600  # 2026-03-07 00:00 UTC

# Projects to pull from SQLite into cm__nanobot (excludes nanobot+openclaw, handled separately)
SQLITE_EXTRA_PROJECTS = [
    "openclaw", "openclaw-proj", "user-root", "projects-root", "foo", "n8n", "memvault",
    "sec-seer", "home-assistant", "writing", "tinyclaw", "iswear", "embedhub",
    "autoresearch", "admin",
]


# ── Embedding ──────────────────────────────────────────────────────────────────

def embed_batch(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch of texts via remote Ollama. Returns None on failure."""
    truncated = [t[:MAX_CHARS] for t in texts]
    payload = json.dumps({"model": EMBED_MODEL, "input": truncated}).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data["embeddings"]
    except Exception as e:
        print(f"  [embed error] {e}")
        return None


# ── ChromaDB helpers ───────────────────────────────────────────────────────────

def get_collection(chroma_dir: Path, collection_name: str):
    """Get or create a ChromaDB collection. Returns None on error."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(chroma_dir))
        return client.get_or_create_collection(
            name=collection_name,
            metadata={"embedding_model": EMBED_MODEL},
        )
    except Exception as e:
        print(f"[chroma error] Cannot open collection {collection_name}: {e}")
        return None


def get_existing_ids(collection, prefix: str | None = None) -> set[str]:
    """Fetch all existing doc IDs from a collection, optionally filtered by prefix."""
    existing = set()
    offset = 0
    batch = 1000
    while True:
        try:
            result = collection.get(limit=batch, offset=offset, include=[])
            ids = result.get("ids", [])
            if not ids:
                break
            for doc_id in ids:
                if prefix is None or doc_id.startswith(prefix):
                    existing.add(doc_id)
            offset += batch
        except Exception as e:
            print(f"  [fetch IDs error at offset {offset}] {e}")
            break
    return existing


def upsert_docs(collection, docs: list[dict], dry_run: bool) -> int:
    """Embed and upsert a list of {id, text, metadata} dicts. Returns count added."""
    if not docs:
        return 0
    if dry_run:
        return len(docs)

    added = 0
    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i : i + BATCH_SIZE]
        texts = [d["text"] for d in batch]
        embeddings = embed_batch(texts)
        if embeddings is None:
            print(f"  [skip batch {i}–{i+len(batch)-1}: embed failed]")
            continue
        try:
            collection.upsert(
                ids=[d["id"] for d in batch],
                documents=texts,
                embeddings=embeddings,
                metadatas=[d["metadata"] for d in batch],
            )
            added += len(batch)
        except Exception as e:
            print(f"  [upsert error at batch {i}] {e}")
    return added


# ── Discord channel → personality map ─────────────────────────────────────────

def load_channel_map() -> dict[str, str]:
    """Return {channel_id: personality_name} from ~/.nanobot/config.json."""
    try:
        cfg = json.loads(NANOBOT_CONFIG.read_text())
        result = {}
        for guild_id, guild in cfg.get("channels", {}).get("discord", {}).get("guilds", {}).items():
            for ch_id, ch_cfg in guild.get("channels", {}).items():
                result[ch_id] = ch_cfg.get("personality", "default")
        return result
    except Exception as e:
        print(f"[warn] Could not load channel map: {e}")
        return {}


# ── Source 1: Claude Code JSONL gap ───────────────────────────────────────────

def _extract_jsonl_messages(path: Path) -> list[tuple[str, str, str]]:
    """
    Extract (role, text, timestamp) from a Claude Code session JSONL.

    Claude Code JSONL format: each line has a 'message' key with role/content,
    plus a top-level 'timestamp'. Real user prompts have 'promptId'; tool
    result lines have 'toolUseResult' (skipped). Assistant lines with only
    tool_use blocks (no text) are also skipped.
    """
    messages = []
    try:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue

            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue

            # Skip tool result user messages (toolUseResult present at top level)
            if role == "user" and obj.get("toolUseResult"):
                continue

            content = msg.get("content", "")
            ts = obj.get("timestamp", "")

            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                texts = []
                skip = False
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        texts.append(block.get("text", ""))
                    elif btype == "thinking":
                        pass  # skip thinking blocks
                    elif btype == "tool_use":
                        skip = True  # assistant message that's just tool calls
                        break
                    elif btype == "tool_result":
                        skip = True
                        break
                if skip:
                    continue
                text = " ".join(t for t in texts if t).strip()
            else:
                continue

            # Skip runtime-context injections, empty, and very short messages
            if not text or text.startswith("[Runtime Context") or len(text) < 10:
                continue

            messages.append((role, text, ts))
    except Exception as e:
        print(f"  [parse error {path.name}] {e}")
    return messages


def backfill_jsonl_gap(collection, existing_ids: set[str], dry_run: bool) -> int:
    """
    Embed user/assistant messages from Claude Code JSONL files newer than
    JSONL_GAP_SINCE_EPOCH that aren't already in ChromaDB.
    """
    print(f"\n── JSONL gap backfill (files since {datetime.fromtimestamp(JSONL_GAP_SINCE_EPOCH, tz=timezone.utc).date()}) ──")

    nanobot_dir = CLAUDE_PROJECTS_DIR / "-Users-keithmackay1-Projects-nanobot"
    if not nanobot_dir.exists():
        print("  Nanobot projects dir not found, skipping")
        return 0

    # Find JSONL files newer than the gap threshold
    gap_files = []
    for f in nanobot_dir.glob("*.jsonl"):
        if f.stat().st_mtime >= JSONL_GAP_SINCE_EPOCH:
            gap_files.append(f)
    gap_files.sort(key=lambda f: f.stat().st_mtime)
    print(f"  {len(gap_files)} JSONL files newer than gap threshold")

    docs = []
    skipped = 0
    for path in gap_files:
        session_prefix = f"jsonl_{path.stem[:8]}_"
        messages = _extract_jsonl_messages(path)
        for idx, (role, text, ts) in enumerate(messages):
            doc_id = f"{session_prefix}{role}_{idx}"
            if doc_id in existing_ids:
                skipped += 1
                continue
            docs.append({
                "id": doc_id,
                "text": text,
                "metadata": {
                    "doc_type": f"jsonl_{role}",
                    "session_id": path.stem[:8],
                    "source": "claude_jsonl_gap",
                    "project": "nanobot",
                    "timestamp": ts,
                },
            })

    print(f"  {len(docs)} new docs to embed, {skipped} already in Chroma")
    if dry_run:
        print(f"  [dry-run] Would add {len(docs)} docs")
        return len(docs)

    added = upsert_docs(collection, docs, dry_run=False)
    print(f"  ✓ Added {added} docs")
    return added


# ── Source 2: Nanobot Discord sessions ────────────────────────────────────────

def backfill_discord(collection, existing_ids: set[str], dry_run: bool) -> int:
    """
    Embed user/assistant messages from nanobot Discord channel JSONL files.
    Includes channel_id and personality name in metadata.
    """
    print("\n── Nanobot Discord backfill ──")
    channel_map = load_channel_map()

    discord_files = sorted(NANOBOT_SESSIONS_DIR.glob("discord_*.jsonl"))
    if not discord_files:
        print("  No Discord session files found")
        return 0
    print(f"  {len(discord_files)} Discord session files")

    docs = []
    skipped = 0

    for path in discord_files:
        # Extract channel_id from filename: discord_{channel_id}.jsonl
        match = re.match(r"discord_(\d+)\.jsonl$", path.name)
        if not match:
            continue
        channel_id = match.group(1)
        personality = channel_map.get(channel_id, "unknown")

        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip metadata lines
            if obj.get("_type") == "metadata":
                continue

            role = obj.get("role")
            if role not in ("user", "assistant"):
                continue

            content = obj.get("content", "")
            ts = obj.get("timestamp", "")

            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                text = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
            else:
                continue

            if not text or len(text) < 5:
                continue

            # Deterministic ID: channel + role + content hash (stable across reruns)
            content_hash = hashlib.md5(f"{channel_id}:{role}:{text}".encode()).hexdigest()[:12]
            doc_id = f"nanobot_discord_{channel_id}_{role}_{content_hash}"

            if doc_id in existing_ids:
                skipped += 1
                continue

            docs.append({
                "id": doc_id,
                "text": text,
                "metadata": {
                    "doc_type": f"discord_{role}",
                    "channel_id": channel_id,
                    "personality": personality,
                    "bot": "nanobot",
                    "source": "discord",
                    "timestamp": ts,
                },
            })

    # Deduplicate by ID (same message text in same channel → same hash)
    seen_ids: set[str] = set()
    unique_docs = []
    for d in docs:
        if d["id"] not in seen_ids:
            seen_ids.add(d["id"])
            unique_docs.append(d)
    if len(unique_docs) < len(docs):
        print(f"  [dedup] {len(docs) - len(unique_docs)} intra-file duplicates removed")
    docs = unique_docs

    print(f"  {len(docs)} new docs to embed, {skipped} already in Chroma")
    if dry_run:
        print(f"  [dry-run] Would add {len(docs)} docs")
        return len(docs)

    added = upsert_docs(collection, docs, dry_run=False)
    print(f"  ✓ Added {added} docs")
    return added


# ── Source 3: Nanobot Telegram sessions ───────────────────────────────────────

def backfill_telegram(collection, existing_ids: set[str], dry_run: bool) -> int:
    """
    Embed user/assistant messages from nanobot Telegram JSONL files.
    """
    print("\n── Nanobot Telegram backfill ──")

    telegram_files = sorted(NANOBOT_SESSIONS_DIR.glob("telegram_*.jsonl"))
    if not telegram_files:
        print("  No Telegram session files found")
        return 0
    print(f"  {len(telegram_files)} Telegram session files")

    docs = []
    skipped = 0

    for path in telegram_files:
        match = re.match(r"telegram_(\d+)\.jsonl$", path.name)
        if not match:
            continue
        chat_id = match.group(1)

        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("_type") == "metadata":
                continue

            role = obj.get("role")
            if role not in ("user", "assistant"):
                continue

            content = obj.get("content", "")
            ts = obj.get("timestamp", "")

            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                text = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
            else:
                continue

            if not text or len(text) < 5:
                continue

            content_hash = hashlib.md5(f"{chat_id}:{role}:{text}".encode()).hexdigest()[:12]
            doc_id = f"nanobot_telegram_{chat_id}_{role}_{content_hash}"

            if doc_id in existing_ids:
                skipped += 1
                continue

            docs.append({
                "id": doc_id,
                "text": text,
                "metadata": {
                    "doc_type": f"telegram_{role}",
                    "chat_id": chat_id,
                    "bot": "nanobot",
                    "source": "telegram",
                    "timestamp": ts,
                },
            })

    print(f"  {len(docs)} new docs to embed, {skipped} already in Chroma")
    if dry_run:
        print(f"  [dry-run] Would add {len(docs)} docs")
        return len(docs)

    added = upsert_docs(collection, docs, dry_run=False)
    print(f"  ✓ Added {added} docs")
    return added


# ── Source 4: SQLite multi-project ────────────────────────────────────────────

def backfill_sqlite_projects(collection, existing_ids: set[str], dry_run: bool) -> int:
    """
    Embed user_prompts + assistant_responses from SQLITE_EXTRA_PROJECTS that
    aren't already in ChromaDB. Uses doc IDs of the form:
      sqlite_{project}_{session_id[:8]}_user_{n}
      sqlite_{project}_{session_id[:8]}_asst_{n}
    """
    print(f"\n── SQLite multi-project backfill ({', '.join(SQLITE_EXTRA_PROJECTS)}) ──")

    if not CLAUDE_MEM_DB.exists():
        print(f"  DB not found: {CLAUDE_MEM_DB}")
        return 0

    db = sqlite3.connect(str(CLAUDE_MEM_DB))
    db.row_factory = sqlite3.Row

    docs = []
    skipped = 0

    for project in SQLITE_EXTRA_PROJECTS:
        sessions = db.execute(
            "SELECT claude_session_id FROM sdk_sessions WHERE project=?", (project,)
        ).fetchall()

        proj_docs = 0
        proj_skip = 0
        for row in sessions:
            sid = row["claude_session_id"]
            short = sid.replace(":", "_")[:12]

            # User prompts
            for prow in db.execute(
                "SELECT prompt_number, prompt_text, created_at FROM user_prompts WHERE claude_session_id=? ORDER BY prompt_number",
                (sid,)
            ):
                doc_id = f"sqlite_{project}_{short}_user_{prow['prompt_number']}"
                if doc_id in existing_ids:
                    proj_skip += 1
                    continue
                text = (prow["prompt_text"] or "").strip()
                if not text or len(text) < 10:
                    continue
                docs.append({
                    "id": doc_id,
                    "text": text,
                    "metadata": {
                        "doc_type": "user_prompt",
                        "project": project,
                        "session_id": sid[:16],
                        "source": "sqlite",
                        "timestamp": prow["created_at"] or "",
                    },
                })
                proj_docs += 1

            # Assistant responses
            for arow in db.execute(
                "SELECT prompt_number, response_text, created_at FROM assistant_responses WHERE claude_session_id=? ORDER BY prompt_number",
                (sid,)
            ):
                doc_id = f"sqlite_{project}_{short}_asst_{arow['prompt_number']}"
                if doc_id in existing_ids:
                    proj_skip += 1
                    continue
                text = (arow["response_text"] or "").strip()
                if not text or len(text) < 10:
                    continue
                docs.append({
                    "id": doc_id,
                    "text": text,
                    "metadata": {
                        "doc_type": "assistant_response",
                        "project": project,
                        "session_id": sid[:16],
                        "source": "sqlite",
                        "timestamp": arow["created_at"] or "",
                    },
                })
                proj_docs += 1

        skipped += proj_skip
        print(f"  {project}: {proj_docs} new docs ({proj_skip} skipped)")

    db.close()

    total_new = len(docs)
    print(f"  Total: {total_new} new docs to embed, {skipped} already in Chroma")

    if dry_run:
        print(f"  [dry-run] Would add {total_new} docs")
        return total_new

    added = upsert_docs(collection, docs, dry_run=False)
    print(f"  ✓ Added {added} docs")
    return added


# ── Source 5: KeithVault Obsidian notes ───────────────────────────────────────

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Parse YAML frontmatter from an Obsidian note. No external YAML library needed.
    Returns (fields_dict, body_text). Handles scalar values, YAML lists (indented
    dash format), and inline lists ([item, item]).
    """
    if not content.startswith("---"):
        return {}, content

    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    fm_text = content[3:end]
    body = content[end + 4:].strip()

    fields: dict = {}
    lines = fm_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if ":" not in stripped:
            i += 1
            continue

        colon = stripped.index(":")
        key = stripped[:colon].strip()
        value_part = stripped[colon + 1:].strip()

        if value_part == "":
            # Possible indented list follows
            items = []
            i += 1
            while i < len(lines) and lines[i].startswith("  "):
                item = lines[i].strip()
                if item.startswith("- "):
                    items.append(item[2:].strip().strip('"\''))
                i += 1
            fields[key] = items
            continue
        elif value_part.startswith("["):
            inner = value_part.strip("[]")
            fields[key] = [x.strip().strip('"\'') for x in inner.split(",") if x.strip()]
        else:
            fields[key] = value_part.strip('"\'')

        i += 1

    return fields, body


def backfill_vault(collection, existing_ids: set[str], dry_run: bool) -> int:
    """
    Embed KeithVault Obsidian notes that have obstagger frontmatter (context field
    set). Includes taxonomy fields as metadata for filtered retrieval by personality
    bots. ID is stable across reruns — based on relative path within the vault.
    """
    print(f"\n── KeithVault backfill ({KEITHVAULT_DIR}) ──")

    if not KEITHVAULT_DIR.exists():
        print(f"  KeithVault not found at {KEITHVAULT_DIR}")
        return 0

    all_files = [
        f for f in KEITHVAULT_DIR.rglob("*.md")
        if not any(part.startswith(".") for part in f.parts)
    ]
    print(f"  {len(all_files)} .md files found")

    docs = []
    skipped_no_context = 0
    skipped_existing = 0

    for path in all_files:
        try:
            content = path.read_text(errors="replace")
        except Exception:
            continue

        fields, body = _parse_frontmatter(content)

        # Only process notes with a context field (obstagger-tagged)
        context = fields.get("context", "")
        if not context:
            skipped_no_context += 1
            continue

        # Normalize list fields to strings for metadata (ChromaDB metadata must be scalar)
        def to_str(v) -> str:
            return ", ".join(v) if isinstance(v, list) else str(v)

        context_str = to_str(context)
        type_str = to_str(fields.get("type", ""))
        subtype_str = to_str(fields.get("subtype", ""))
        tags_str = to_str(fields.get("tags", []))
        created_str = to_str(fields.get("created", ""))

        # Stable doc ID from relative path
        rel_path = str(path.relative_to(KEITHVAULT_DIR))
        doc_id = f"vault_{hashlib.md5(rel_path.encode()).hexdigest()[:16]}"

        if doc_id in existing_ids:
            skipped_existing += 1
            continue

        # Build text: title + taxonomy header + body (gives semantic richness)
        body_stripped = body.strip()
        text_parts = [f"[KeithVault: {path.stem}]"]
        text_parts.append(f"context: {context_str} | type: {type_str} | subtype: {subtype_str}")
        if tags_str:
            text_parts.append(f"tags: {tags_str}")
        if body_stripped:
            text_parts.append(body_stripped)
        text = "\n".join(text_parts)

        docs.append({
            "id": doc_id,
            "text": text,
            "metadata": {
                "doc_type": "obsidian_note",
                "source": "keithvault",
                "path": rel_path,
                "title": path.stem,
                "context": context_str,
                "type": type_str,
                "subtype": subtype_str,
                "tags": tags_str,
                "created": created_str,
            },
        })

    print(f"  {len(docs)} new docs to embed, {skipped_existing} already in Chroma, "
          f"{skipped_no_context} without context field")

    if dry_run:
        print(f"  [dry-run] Would add {len(docs)} docs")
        return len(docs)

    added = upsert_docs(collection, docs, dry_run=False)
    print(f"  ✓ Added {added} docs")
    return added


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Show counts, no writes")
    parser.add_argument("--source", default="all",
                        choices=["all", "jsonl", "discord", "telegram", "sqlite", "vault"],
                        help="Which source to backfill (default: all)")
    args = parser.parse_args()

    print(f"backfill_all.py — {'DRY RUN' if args.dry_run else 'LIVE'} — source={args.source}")
    print(f"  Chroma dir: {CHROMA_DIR}")
    print(f"  Collection: {COLLECTION_NANOBOT}")
    print(f"  Embed model: {EMBED_MODEL} via {OLLAMA_URL}")
    print()

    # Verify Ollama connectivity — retry up to 5 times with backoff
    ollama_ok = False
    for attempt in range(1, 6):
        test_embed = embed_batch(["connection test"])
        if test_embed is not None:
            print(f"  ✓ Ollama reachable ({len(test_embed[0])}-dim vectors)")
            ollama_ok = True
            break
        print(f"  [attempt {attempt}/5] Ollama not ready, retrying in 15s...")
        time.sleep(15)
    if not ollama_ok:
        print(f"  ✗ Ollama not reachable at {OLLAMA_URL} after 5 attempts")
        if not args.dry_run:
            return

    # Open ChromaDB collection
    collection = get_collection(CHROMA_DIR, COLLECTION_NANOBOT)
    if collection is None:
        return

    current_count = collection.count()
    print(f"  Current {COLLECTION_NANOBOT} count: {current_count:,}")
    print()

    # Fetch all existing IDs once (shared across sources)
    if not args.dry_run:
        print("Fetching existing ChromaDB IDs...")
        t0 = time.time()
        existing_ids = get_existing_ids(collection)
        print(f"  {len(existing_ids):,} existing IDs fetched in {time.time()-t0:.1f}s")
    else:
        existing_ids = set()

    total_added = 0

    if args.source in ("all", "jsonl"):
        total_added += backfill_jsonl_gap(collection, existing_ids, args.dry_run)

    if args.source in ("all", "discord"):
        total_added += backfill_discord(collection, existing_ids, args.dry_run)

    if args.source in ("all", "telegram"):
        total_added += backfill_telegram(collection, existing_ids, args.dry_run)

    if args.source in ("all", "sqlite"):
        total_added += backfill_sqlite_projects(collection, existing_ids, args.dry_run)

    if args.source in ("all", "vault"):
        total_added += backfill_vault(collection, existing_ids, args.dry_run)

    print(f"\n{'[dry-run] Would add' if args.dry_run else 'Total added:'} {total_added} docs")
    if not args.dry_run:
        new_count = collection.count()
        print(f"Collection {COLLECTION_NANOBOT}: {current_count:,} → {new_count:,}")


if __name__ == "__main__":
    main()
