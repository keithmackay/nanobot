#!/usr/bin/env python3
# ABOUTME: Registers the weekly KeithVault lint cron job in nanobot's cron store.
# ABOUTME: Run once to set up; the cron job fires every Sunday at 3am.
"""
Registers a weekly lint job in nanobot's cron store (JSON file).
The job sends an agent turn that scans KeithVault for schema violations.

Usage:
    python3 scripts/register_lint_cron.py [--remove]
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

CRON_STORE_PATH = Path.home() / ".nanobot/workspace/cron/jobs.json"

LINT_JOB_NAME = "vault-lint-weekly"

LINT_MESSAGE = """\
Weekly KeithVault schema lint. Scan ~/KeithVault for Obsidian notes that:
1. Have YAML frontmatter but missing required fields (context, type, or created).
2. Have a context value not in the canonical list: EY, ideas, personal, system, briefings, gardening, admin, book, cognitive-bias, MOC, philosophy, tech, YouTube.
3. Are .md files with no YAML frontmatter at all (skip system/templates/ and _inbox/).

Use the bash tool to run find/grep commands. Report findings as a concise summary:
- Count of notes missing required fields (list up to 10 examples with paths)
- Count of notes with invalid context values (list all)
- Count of notes with no frontmatter (list up to 10 examples)
- Overall health score: (valid notes / total notes with frontmatter) * 100

Send the summary to Discord channel 1472143342753153064 (archie/#automations) using the message tool.
Write a brief entry to ~/KeithVault/wiki/log.md with the lint results.
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _next_sunday_3am_ms() -> int:
    """Compute milliseconds until next Sunday 3:00am local time."""
    import datetime
    now = datetime.datetime.now()
    days_until_sunday = (6 - now.weekday()) % 7 or 7
    next_sunday = now.replace(hour=3, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days_until_sunday)
    return int(next_sunday.timestamp() * 1000)


def load_store(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"version": 1, "jobs": []}


def save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true", help="Remove the lint job")
    args = parser.parse_args()

    store = load_store(CRON_STORE_PATH)

    # Remove existing lint job if present
    existing = [j for j in store["jobs"] if j.get("name") == LINT_JOB_NAME]
    if existing:
        store["jobs"] = [j for j in store["jobs"] if j.get("name") != LINT_JOB_NAME]
        if args.remove:
            save_store(CRON_STORE_PATH, store)
            print(f"Removed existing '{LINT_JOB_NAME}' job.")
            return
        print(f"Replaced existing '{LINT_JOB_NAME}' job.")

    if args.remove:
        print(f"No '{LINT_JOB_NAME}' job found.")
        return

    now = _now_ms()
    next_run = _next_sunday_3am_ms()

    job = {
        "id": str(uuid.uuid4())[:8],
        "name": LINT_JOB_NAME,
        "enabled": True,
        "schedule": {
            "kind": "cron",
            "atMs": None,
            "everyMs": None,
            "expr": "0 3 * * 0",   # Every Sunday at 3am
            "tz": "America/New_York",
        },
        "payload": {
            "kind": "agent_turn",
            "message": LINT_MESSAGE,
            "cmd": None,
            "deliver": False,
            "channel": None,
            "to": None,
        },
        "state": {
            "nextRunAtMs": next_run,
            "lastRunAtMs": None,
            "lastStatus": None,
            "lastError": None,
        },
        "createdAtMs": now,
        "updatedAtMs": now,
        "deleteAfterRun": False,
    }

    store["jobs"].append(job)
    save_store(CRON_STORE_PATH, store)

    import datetime
    next_dt = datetime.datetime.fromtimestamp(next_run / 1000)
    print(f"Registered '{LINT_JOB_NAME}' (id: {job['id']})")
    print(f"Next run: {next_dt.strftime('%Y-%m-%d %H:%M %Z')} (every Sunday at 3am ET)")
    print(f"Cron store: {CRON_STORE_PATH}")


if __name__ == "__main__":
    main()
