#!/usr/bin/env python3
# ABOUTME: Synthesizes recent Discord channel conversations into KeithVault wiki notes.
# ABOUTME: Reads per-channel JSONL session files, groups by personality, invokes Claude CLI to write notes.
"""
Reads Discord session JSONLs from ~/.nanobot/workspace/sessions/,
groups recent messages by personality, and uses Claude CLI to synthesize
a wiki note per personality into ~/KeithVault/wiki/{personality}.md.

Usage:
    python3 scripts/ingest_vault.py [--days N] [--personality NAME] [--dry-run]

Options:
    --days N         Only include messages from the last N days (default: 7)
    --personality    Only process one personality
    --dry-run        Show what would be written without invoking Claude
    --min-messages N Minimum user messages needed to synthesize (default: 3)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


SESSIONS_DIR = Path.home() / ".nanobot/workspace/sessions"
CONFIG_PATH = Path.home() / ".nanobot/config.json"
VAULT_WIKI_DIR = Path.home() / "KeithVault/wiki"
LOG_PATH = Path.home() / "KeithVault/wiki/log.md"
CLAUDE_BIN = "/usr/local/bin/claude"


def load_channel_map() -> dict[str, str]:
    """Return {channel_id: personality_name} from nanobot config."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        result: dict[str, str] = {}
        for guild in cfg.get("channels", {}).get("discord", {}).get("guilds", {}).values():
            for ch_id, ch_cfg in guild.get("channels", {}).items():
                result[ch_id] = ch_cfg.get("personality", "default")
        return result
    except Exception as e:
        print(f"[warn] Could not load channel map: {e}", file=sys.stderr)
        return {}


def load_personality_descriptions() -> dict[str, str]:
    """Return {personality_name: description} from nanobot config."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return {
            name: p.get("description", name)
            for name, p in cfg.get("personalities", {}).items()
        }
    except Exception:
        return {}


def _parse_ts(ts: str) -> datetime | None:
    """Parse ISO timestamp to UTC datetime."""
    if not ts:
        return None
    try:
        # Handle both with and without timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def read_recent_messages(
    jsonl_path: Path,
    since: datetime,
) -> list[dict]:
    """Extract user/assistant messages from a Discord JSONL newer than `since`."""
    messages = []
    for line in jsonl_path.read_text(errors="replace").splitlines():
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

        ts_str = obj.get("timestamp", "")
        ts = _parse_ts(ts_str)
        if ts and ts < since:
            continue

        content = obj.get("content", "")
        if isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
        else:
            text = str(content).strip()

        # Skip runtime context injection lines
        if text.startswith("[Runtime Context"):
            continue

        if len(text) < 10:
            continue

        messages.append({"role": role, "text": text, "timestamp": ts_str})

    return messages


def build_synthesis_prompt(
    personality: str,
    description: str,
    messages: list[dict],
    note_path: Path,
    today: str,
) -> str:
    """Build the Claude prompt to synthesize a wiki note."""
    existing = ""
    if note_path.exists():
        existing = f"\nExisting note (update/extend it, don't discard prior knowledge):\n```\n{note_path.read_text()[:3000]}\n```\n"

    conversation_text = "\n".join(
        f"[{m['timestamp'][:10] if m['timestamp'] else '?'}] {m['role'].upper()}: {m['text'][:400]}"
        for m in messages
    )

    return f"""You are a knowledge curator. Synthesize recent Discord conversations into a structured KeithVault wiki note.

Personality: {personality} — {description}
Today: {today}
{existing}
Recent conversation ({len(messages)} messages):
---
{conversation_text}
---

Output ONLY the complete wiki note content (no commentary, no preamble). Start directly with the YAML frontmatter.

The note MUST have this YAML frontmatter:
---
context: personal
type: wiki
subtype: conversation-log
tags:
  - nanobot
  - {personality}
  - discord
interpreter: Claude
created: "[[{today}]]"
updated: "[[{today}]]"
---

After the frontmatter, write a structured markdown note with these sections (only include sections that have content):

## Summary
1-3 sentence overview of what this channel is about and recent focus areas.

## Key Topics
Bullet list of distinct topics discussed recently.

## Decisions & Conclusions
Any decisions made, conclusions reached, or action items identified.

## Knowledge & Notes
Factual or reference information captured in conversations worth preserving.

## Open Questions
Unresolved questions or things to follow up on.

---
*Last synthesized: {today} from {len(messages)} messages*

Rules:
- Be concise. This is a reference note, not a transcript.
- Omit sections that have nothing meaningful.
- If updating an existing note, preserve prior knowledge that's still relevant.
- Do not include raw conversation snippets — synthesize and abstract.
- Output ONLY the note. No tool calls. No explanation. Start with the --- frontmatter delimiter.
"""


def ingest_personality(
    personality: str,
    description: str,
    messages: list[dict],
    dry_run: bool,
    today: str,
) -> bool:
    """Invoke Claude to synthesize a wiki note for one personality."""
    note_path = VAULT_WIKI_DIR / f"{personality}.md"

    prompt = build_synthesis_prompt(personality, description, messages, note_path, today)

    if dry_run:
        user_msgs = [m for m in messages if m["role"] == "user"]
        print(f"  [dry-run] Would synthesize {personality}: {len(user_msgs)} user messages → {note_path}")
        return True

    print(f"  Synthesizing {personality} ({len(messages)} messages) → {note_path}")

    try:
        result = subprocess.run(
            [CLAUDE_BIN, "--dangerously-skip-permissions", "--model", "haiku", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
            env={
                "HOME": str(Path.home()),
                "USER": Path.home().name,
                "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
            },
        )
        if result.returncode != 0:
            print(f"  [error] Claude exited {result.returncode} for {personality}: {result.stderr[:300]}", file=sys.stderr)
            return False

        content = result.stdout.strip()
        if not content or "---" not in content:
            print(f"  [error] Claude returned empty or invalid output for {personality}", file=sys.stderr)
            if result.stderr:
                print(f"  [stderr] {result.stderr[:200]}", file=sys.stderr)
            return False

        note_path.write_text(content + "\n")
        print(f"  Written: {note_path}")
        return True
    except subprocess.TimeoutExpired:
        print(f"  [error] Claude timed out for {personality}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  [error] {personality}: {e}", file=sys.stderr)
        return False


def append_log(successes: list[str], failures: list[str], today: str) -> None:
    """Append a run entry to the wiki log."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = f"\n## {today}\n"
    if successes:
        entry += "Synthesized: " + ", ".join(successes) + "\n"
    if failures:
        entry += "Failed: " + ", ".join(failures) + "\n"
    if not successes and not failures:
        entry += "No personalities had sufficient recent activity.\n"

    if LOG_PATH.exists():
        existing = LOG_PATH.read_text()
        # Insert after frontmatter if present, else prepend
        if existing.startswith("---"):
            end = existing.find("\n---", 3)
            if end != -1:
                LOG_PATH.write_text(existing[: end + 4] + "\n" + entry + existing[end + 4:])
                return
        LOG_PATH.write_text(existing + entry)
    else:
        LOG_PATH.write_text(f"---\ncontext: system\ntype: log\nsubtype: wiki\ntags:\n  - nanobot\n  - wiki\ninterpreter: Claude\n---\n# Wiki Ingest Log\n{entry}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Discord conversations into KeithVault wiki notes")
    parser.add_argument("--days", type=int, default=7, help="Include messages from last N days")
    parser.add_argument("--personality", help="Only process this personality")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-messages", type=int, default=3, help="Minimum user messages to synthesize")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    since = datetime.now(tz=timezone.utc) - timedelta(days=args.days)

    channel_map = load_channel_map()
    descriptions = load_personality_descriptions()

    if not channel_map:
        print("No channel map loaded — check ~/.nanobot/config.json", file=sys.stderr)
        sys.exit(1)

    VAULT_WIKI_DIR.mkdir(parents=True, exist_ok=True)

    # Group messages by personality
    personality_messages: dict[str, list[dict]] = {}

    for jsonl_path in sorted(SESSIONS_DIR.glob("discord_*.jsonl")):
        import re
        m = re.match(r"discord_(\d+)\.jsonl$", jsonl_path.name)
        if not m:
            continue
        channel_id = m.group(1)
        personality = channel_map.get(channel_id, "unknown")

        if args.personality and personality != args.personality:
            continue

        msgs = read_recent_messages(jsonl_path, since)
        if msgs:
            personality_messages.setdefault(personality, []).extend(msgs)

    if not personality_messages:
        print(f"No messages found in the last {args.days} days.")
        return

    print(f"Found activity for: {', '.join(sorted(personality_messages))}")

    successes: list[str] = []
    failures: list[str] = []

    for personality, messages in sorted(personality_messages.items()):
        user_msgs = [m for m in messages if m["role"] == "user"]
        if len(user_msgs) < args.min_messages:
            print(f"  Skipping {personality}: only {len(user_msgs)} user messages (min: {args.min_messages})")
            continue

        description = descriptions.get(personality, personality)
        ok = ingest_personality(personality, description, messages, args.dry_run, today)
        (successes if ok else failures).append(personality)

    if not args.dry_run:
        append_log(successes, failures, today)

    print(f"\nDone. Synthesized: {len(successes)}, Failed: {len(failures)}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
