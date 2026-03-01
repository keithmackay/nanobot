#!/usr/bin/env python3
"""
Cync / GE Cync helper — wraps python-cync-lights for common device operations.

Usage:
  cync.py list
  cync.py status <name-or-id>
  cync.py on <name-or-id>
  cync.py off <name-or-id>
  cync.py brightness <name-or-id> <0-100>
  cync.py color <name-or-id> <r> <g> <b>
  cync.py colortemp <name-or-id> <kelvin>

Auth:
  Set CYNC_EMAIL and CYNC_PASSWORD env vars,
  or place in ~/.config/cync/config.json.
"""

import asyncio
import json
import os
import sys
from pathlib import Path


def get_credentials():
    email = os.environ.get("CYNC_EMAIL")
    password = os.environ.get("CYNC_PASSWORD")
    if email and password:
        return email, password
    cfg = Path.home() / ".config" / "cync" / "config.json"
    if cfg.exists():
        data = json.loads(cfg.read_text())
        return data["email"], data["password"]
    sys.exit("ERROR: Set CYNC_EMAIL/CYNC_PASSWORD env vars or create ~/.config/cync/config.json")


def find_device(devices, query: str):
    """Find device by ID or name substring (case-insensitive)."""
    query_lower = query.lower()
    # Try exact ID match first
    for d in devices:
        if str(getattr(d, 'id', '')) == query:
            return d
    # Name substring match
    matches = [d for d in devices if query_lower in d.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous query '{query}' matched: {[d.name for d in matches]}")
        sys.exit(1)
    sys.exit(f"ERROR: No device found matching '{query}'")


async def main():
    try:
        from cync_lights.cync_hub import CyncHub
    except ImportError:
        sys.exit("ERROR: Install python-cync-lights — pip install git+https://github.com/nikshriv/cync_lights.git")

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    email, password = get_credentials()

    # Initialize hub and authenticate
    hub = CyncHub(email, password)
    await hub.authenticate()
    await hub.get_devices()

    devices = list(hub.devices.values()) if hasattr(hub, 'devices') else []

    if cmd == "list":
        if not devices:
            print("No devices found.")
            return
        for d in sorted(devices, key=lambda x: x.name):
            status = "on" if getattr(d, 'power_state', False) else "off"
            brightness = getattr(d, 'brightness', '?')
            print(f"{d.name:<40} id={getattr(d, 'id', '?')}  [{status}] brightness={brightness}")

    elif cmd == "status":
        if len(args) < 2:
            sys.exit("Usage: cync.py status <name-or-id>")
        d = find_device(devices, args[1])
        print(f"Name:       {d.name}")
        print(f"ID:         {getattr(d, 'id', 'N/A')}")
        print(f"Power:      {'on' if getattr(d, 'power_state', False) else 'off'}")
        print(f"Brightness: {getattr(d, 'brightness', 'N/A')}")
        print(f"Color RGB:  {getattr(d, 'rgb', 'N/A')}")
        print(f"Color temp: {getattr(d, 'color_temp', 'N/A')}")

    elif cmd == "on":
        if len(args) < 2:
            sys.exit("Usage: cync.py on <name-or-id>")
        d = find_device(devices, args[1])
        await d.turn_on()
        print(f"OK: {d.name} turned on")

    elif cmd == "off":
        if len(args) < 2:
            sys.exit("Usage: cync.py off <name-or-id>")
        d = find_device(devices, args[1])
        await d.turn_off()
        print(f"OK: {d.name} turned off")

    elif cmd == "brightness":
        if len(args) < 3:
            sys.exit("Usage: cync.py brightness <name-or-id> <0-100>")
        d = find_device(devices, args[1])
        level = max(0, min(100, int(args[2])))
        await d.set_brightness(level)
        print(f"OK: {d.name} brightness set to {level}%")

    elif cmd == "color":
        if len(args) < 5:
            sys.exit("Usage: cync.py color <name-or-id> <r> <g> <b>")
        d = find_device(devices, args[1])
        r, g, b = int(args[2]), int(args[3]), int(args[4])
        await d.set_rgb(r, g, b)
        print(f"OK: {d.name} color set to RGB({r},{g},{b})")

    elif cmd == "colortemp":
        if len(args) < 3:
            sys.exit("Usage: cync.py colortemp <name-or-id> <kelvin>")
        d = find_device(devices, args[1])
        kelvin = int(args[2])
        await d.set_color_temp(kelvin)
        print(f"OK: {d.name} color temp set to {kelvin}K")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
