#!/usr/bin/env python3
"""Clear the briefing cron session history, keeping only the metadata line.

Called by the briefing cron job after a successful run so the next morning
starts with a lean context window (no accumulated error turns).
"""
import json
import sys
from pathlib import Path

SESSION_FILE = Path.home() / ".nanobot/workspace/sessions/cron_6fe42328-02b6-47ee-9310-c144f70b5628.jsonl"

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

    old_count = len(lines)
    if meta_lines:
        SESSION_FILE.write_text(meta_lines[0])
        print(f"Briefing session cleared: {old_count} lines → 1 (metadata kept)")
    else:
        SESSION_FILE.write_text("")
        print(f"Briefing session cleared: {old_count} lines → 0 (no metadata found, truncated)")

if __name__ == "__main__":
    main()
