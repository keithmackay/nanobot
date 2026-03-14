#!/usr/bin/env python3
"""Clear the eval cron session history, keeping only the metadata line.

Called by the eval cron job after both the impl plan and briefing section
are written, so the next morning starts with a lean context window.
"""
import json
import sys
from pathlib import Path

SESSION_FILE = Path.home() / ".nanobot/workspace/sessions/cron_a3b7c921-4d5e-4f8a-b6c1-9e2f0d3a5b7c.jsonl"

def main() -> None:
    if not SESSION_FILE.exists():
        print(f"Session file not found: {SESSION_FILE}")
        return

    lines = SESSION_FILE.read_text().splitlines(keepends=True)
    meta_lines = []
    for line in lines:
        try:
            d = json.loads(line.strip())
            if d.get("_type") == "metadata":
                meta_lines.append(line)
                break
        except (json.JSONDecodeError, ValueError):
            pass

    if not meta_lines:
        print("WARNING: no metadata line found, leaving session intact")
        return

    old_count = len(lines)
    SESSION_FILE.write_text(meta_lines[0])
    print(f"Eval session cleared: {old_count} lines → 1")

if __name__ == "__main__":
    main()
