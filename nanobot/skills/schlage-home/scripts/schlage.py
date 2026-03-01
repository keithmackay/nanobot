#!/usr/bin/env python3
"""
Schlage Home helper — wraps pyschlage for common lock operations.

Usage:
  schlage.py list
  schlage.py status <name-or-id>
  schlage.py lock <name-or-id>
  schlage.py unlock <name-or-id>
  schlage.py battery <name-or-id>
  schlage.py logs <name-or-id> [limit]

Auth:
  Set SCHLAGE_EMAIL and SCHLAGE_PASSWORD env vars,
  or place in ~/.config/schlage/config.json.
"""

import json
import os
import sys
from pathlib import Path


def get_credentials():
    email = os.environ.get("SCHLAGE_EMAIL")
    password = os.environ.get("SCHLAGE_PASSWORD")
    if email and password:
        return email, password
    cfg = Path.home() / ".config" / "schlage" / "config.json"
    if cfg.exists():
        data = json.loads(cfg.read_text())
        return data["email"], data["password"]
    sys.exit("ERROR: Set SCHLAGE_EMAIL/SCHLAGE_PASSWORD env vars or create ~/.config/schlage/config.json")


def find_lock(locks, query: str):
    """Find lock by device_id or name substring (case-insensitive)."""
    query_lower = query.lower()
    for lock in locks:
        if lock.device_id == query:
            return lock
    matches = [lock for lock in locks if query_lower in lock.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous query '{query}' matched: {[l.name for l in matches]}")
        sys.exit(1)
    sys.exit(f"ERROR: No lock found matching '{query}'")


def main():
    try:
        import pyschlage
    except ImportError:
        sys.exit("ERROR: Install pyschlage — pip install pyschlage")

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    email, password = get_credentials()

    auth = pyschlage.Auth(email, password)
    api = pyschlage.Schlage(auth)
    api.refresh()
    locks = api.locks()

    if cmd == "list":
        if not locks:
            print("No locks found.")
            return
        for lock in sorted(locks, key=lambda x: x.name):
            state = "locked" if lock.is_locked else "unlocked"
            battery = f"{lock.battery_level}%" if lock.battery_level is not None else "?"
            print(f"{lock.name:<40} id={lock.device_id}  [{state}] battery={battery}")

    elif cmd == "status":
        if len(args) < 2:
            sys.exit("Usage: schlage.py status <name-or-id>")
        lock = find_lock(locks, args[1])
        lock.refresh()
        print(f"Name:      {lock.name}")
        print(f"ID:        {lock.device_id}")
        print(f"State:     {'locked' if lock.is_locked else 'unlocked'}")
        print(f"Battery:   {lock.battery_level}%")
        print(f"Model:     {getattr(lock, 'model_name', 'N/A')}")
        print(f"Firmware:  {getattr(lock, 'firmware_version', 'N/A')}")

    elif cmd == "lock":
        if len(args) < 2:
            sys.exit("Usage: schlage.py lock <name-or-id>")
        lock = find_lock(locks, args[1])
        lock.lock()
        print(f"OK: {lock.name} locked")

    elif cmd == "unlock":
        if len(args) < 2:
            sys.exit("Usage: schlage.py unlock <name-or-id>")
        lock = find_lock(locks, args[1])
        lock.unlock()
        print(f"OK: {lock.name} unlocked")

    elif cmd == "battery":
        if len(args) < 2:
            sys.exit("Usage: schlage.py battery <name-or-id>")
        lock = find_lock(locks, args[1])
        lock.refresh()
        print(f"{lock.name}: {lock.battery_level}%")

    elif cmd == "logs":
        if len(args) < 2:
            sys.exit("Usage: schlage.py logs <name-or-id> [limit]")
        lock = find_lock(locks, args[1])
        limit = int(args[2]) if len(args) >= 3 else 10
        logs = lock.logs()
        for entry in list(logs)[:limit]:
            ts = getattr(entry, 'created_at', 'N/A')
            action = getattr(entry, 'message', str(entry))
            print(f"{ts}  {action}")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
