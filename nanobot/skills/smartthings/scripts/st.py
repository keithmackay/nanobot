#!/usr/bin/env python3
"""
SmartThings helper — wraps pysmartthings for common device operations.

Usage:
  st.py list
  st.py status <name-or-id>
  st.py switch <name-or-id> on|off
  st.py dim <name-or-id> <0-100>
  st.py lock <name-or-id>
  st.py unlock <name-or-id>
  st.py thermostat <name-or-id> heat|cool|auto|off [setpoint]

Auth:
  Set SMARTTHINGS_TOKEN env var, or place token in ~/.config/smartthings/config.json.
"""

import asyncio
import json
import os
import sys
from pathlib import Path


def get_token() -> str:
    token = os.environ.get("SMARTTHINGS_TOKEN")
    if token:
        return token
    cfg = Path.home() / ".config" / "smartthings" / "config.json"
    if cfg.exists():
        return json.loads(cfg.read_text())["token"]
    sys.exit("ERROR: Set SMARTTHINGS_TOKEN env var or create ~/.config/smartthings/config.json")


def find_device(devices, query: str):
    """Find device by ID or case-insensitive name substring."""
    query_lower = query.lower()
    for d in devices:
        if d.device_id == query or query_lower in d.label.lower():
            return d
    matches = [d for d in devices if query_lower in d.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous query '{query}' matched: {[d.label for d in matches]}")
        sys.exit(1)
    sys.exit(f"ERROR: No device found matching '{query}'")


async def cmd_list(api):
    devices = await api.devices()
    for d in sorted(devices, key=lambda x: x.label):
        print(f"{d.label:<40} {d.device_id}  [{', '.join(d.capabilities)}]")


async def cmd_status(api, query: str):
    devices = await api.devices()
    d = find_device(devices, query)
    await d.status.refresh()
    print(f"Device: {d.label} ({d.device_id})")
    for cap_name, cap_status in d.status.values.items():
        for attr, val in cap_status.items():
            print(f"  {cap_name}.{attr}: {val}")


async def cmd_switch(api, query: str, state: str):
    devices = await api.devices()
    d = find_device(devices, query)
    if state == "on":
        await d.switch_on()
    elif state == "off":
        await d.switch_off()
    else:
        sys.exit("ERROR: state must be 'on' or 'off'")
    print(f"OK: {d.label} turned {state}")


async def cmd_dim(api, query: str, level: int):
    devices = await api.devices()
    d = find_device(devices, query)
    await d.set_level(level)
    print(f"OK: {d.label} set to {level}%")


async def cmd_lock(api, query: str, action: str):
    devices = await api.devices()
    d = find_device(devices, query)
    if action == "lock":
        await d.lock()
    else:
        await d.unlock()
    print(f"OK: {d.label} {action}ed")


async def cmd_thermostat(api, query: str, mode: str, setpoint=None):
    devices = await api.devices()
    d = find_device(devices, query)
    await d.set_thermostat_mode(mode)
    if setpoint is not None:
        sp = float(setpoint)
        if mode in ("heat", "auto"):
            await d.set_heating_setpoint(sp)
        if mode in ("cool", "auto"):
            await d.set_cooling_setpoint(sp)
    print(f"OK: {d.label} thermostat set to {mode}" + (f" @ {setpoint}" if setpoint else ""))


async def main():
    try:
        import pysmartthings
    except ImportError:
        sys.exit("ERROR: Install pysmartthings — pip install pysmartthings")

    token = get_token()
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    async with pysmartthings.SmartThings(token) as api:
        if cmd == "list":
            await cmd_list(api)

        elif cmd == "status":
            if len(args) < 2:
                sys.exit("Usage: st.py status <name-or-id>")
            await cmd_status(api, args[1])

        elif cmd == "switch":
            if len(args) < 3:
                sys.exit("Usage: st.py switch <name-or-id> on|off")
            await cmd_switch(api, args[1], args[2])

        elif cmd == "dim":
            if len(args) < 3:
                sys.exit("Usage: st.py dim <name-or-id> <0-100>")
            await cmd_dim(api, args[1], int(args[2]))

        elif cmd == "lock":
            if len(args) < 2:
                sys.exit("Usage: st.py lock <name-or-id>")
            await cmd_lock(api, args[1], "lock")

        elif cmd == "unlock":
            if len(args) < 2:
                sys.exit("Usage: st.py unlock <name-or-id>")
            await cmd_lock(api, args[1], "unlock")

        elif cmd == "thermostat":
            if len(args) < 3:
                sys.exit("Usage: st.py thermostat <name-or-id> heat|cool|auto|off [setpoint]")
            setpoint = args[3] if len(args) >= 4 else None
            await cmd_thermostat(api, args[1], args[2], setpoint)

        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
