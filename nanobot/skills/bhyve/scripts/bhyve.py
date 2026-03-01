#!/usr/bin/env python3
"""
B-Hyve helper — REST + WebSocket client for Orbit B-Hyve irrigation control.

Usage:
  bhyve.py list
  bhyve.py status <device-id-or-name>
  bhyve.py start <device-id> <zone> <minutes>
  bhyve.py stop <device-id>
  bhyve.py schedules <device-id-or-name>

Auth:
  Set BHYVE_EMAIL and BHYVE_PASSWORD env vars,
  or place in ~/.config/bhyve/config.json.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE_URL = "https://api.orbitbhyve.com/v1"
WS_URL = "wss://api.orbitbhyve.com/v1/events"
APP_ID = "OB01BE45CFC804"  # B-Hyve app ID (from reverse engineering)


def get_credentials():
    email = os.environ.get("BHYVE_EMAIL")
    password = os.environ.get("BHYVE_PASSWORD")
    if email and password:
        return email, password
    cfg = Path.home() / ".config" / "bhyve" / "config.json"
    if cfg.exists():
        data = json.loads(cfg.read_text())
        return data["email"], data["password"]
    sys.exit("ERROR: Set BHYVE_EMAIL/BHYVE_PASSWORD env vars or create ~/.config/bhyve/config.json")


def login(email: str, password: str) -> dict:
    """POST /session and return session dict with orbit_session_token."""
    try:
        import requests
    except ImportError:
        sys.exit("ERROR: Install requests — pip install requests")

    resp = requests.post(
        f"{BASE_URL}/session",
        json={
            "session": {
                "email": email,
                "password": password,
                "app_id": APP_ID,
            }
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_devices(token: str) -> list:
    try:
        import requests
    except ImportError:
        sys.exit("ERROR: Install requests — pip install requests")

    resp = requests.get(
        f"{BASE_URL}/devices",
        headers={"orbit-session-token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def find_device(devices: list, query: str) -> dict:
    query_lower = query.lower()
    for d in devices:
        if d.get("id") == query:
            return d
    matches = [d for d in devices if query_lower in d.get("name", "").lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = [d["name"] for d in matches]
        print(f"Ambiguous query '{query}' matched: {names}")
        sys.exit(1)
    sys.exit(f"ERROR: No device found matching '{query}'")


async def send_ws_command(token: str, payload: dict):
    """Send a command via WebSocket and wait for acknowledgment."""
    try:
        import websockets
    except ImportError:
        sys.exit("ERROR: Install websockets — pip install websockets")

    async with websockets.connect(WS_URL) as ws:
        # Handshake
        handshake = {
            "event": "app_connection",
            "orbit_session_token": token,
            "subscribe_device_id": payload.get("device_id", ""),
        }
        await ws.send(json.dumps(handshake))
        # Wait for connected event
        for _ in range(5):
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            if data.get("event") == "app_connected":
                break

        # Send the command
        await ws.send(json.dumps(payload))

        # Wait briefly for acknowledgment
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=8)
            data = json.loads(msg)
            return data
        except asyncio.TimeoutError:
            return {"status": "command sent (no ack received)"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    email, password = get_credentials()

    print("Authenticating...", file=sys.stderr)
    session = login(email, password)
    token = session.get("orbit_session_token") or session.get("session", {}).get("orbit_session_token")
    if not token:
        sys.exit(f"ERROR: Could not extract session token from response: {session}")

    devices = get_devices(token)

    if cmd == "list":
        if not devices:
            print("No devices found.")
            return
        for d in devices:
            name = d.get("name", "unknown")
            device_id = d.get("id", "?")
            dtype = d.get("type", "?")
            status = d.get("status", {}).get("run_mode", "?")
            zones = len(d.get("zones", []))
            print(f"{name:<40} id={device_id}  type={dtype}  mode={status}  zones={zones}")

    elif cmd == "status":
        if len(args) < 2:
            sys.exit("Usage: bhyve.py status <device-id-or-name>")
        d = find_device(devices, args[1])
        print(f"Name:    {d.get('name')}")
        print(f"ID:      {d.get('id')}")
        print(f"Type:    {d.get('type')}")
        status = d.get("status", {})
        print(f"Mode:    {status.get('run_mode', 'N/A')}")
        print(f"Online:  {d.get('is_connected', 'N/A')}")
        zones = d.get("zones", [])
        for z in zones:
            print(f"  Zone {z.get('station')}: {z.get('name', 'Zone ' + str(z.get('station')))}")

    elif cmd == "start":
        if len(args) < 4:
            sys.exit("Usage: bhyve.py start <device-id> <zone> <minutes>")
        device_id = args[1]
        zone = int(args[2])
        minutes = int(args[3])
        payload = {
            "event": "change_mode",
            "mode": "manual",
            "device_id": device_id,
            "timestamp": now_iso(),
            "stations": [{"station": zone, "run_time": minutes}],
        }
        print(f"Starting zone {zone} for {minutes} minutes on device {device_id}...")
        result = await send_ws_command(token, payload)
        print(f"Result: {result}")

    elif cmd == "stop":
        if len(args) < 2:
            sys.exit("Usage: bhyve.py stop <device-id>")
        device_id = args[1]
        payload = {
            "event": "change_mode",
            "mode": "auto",
            "device_id": device_id,
            "timestamp": now_iso(),
        }
        print(f"Stopping all watering on device {device_id}...")
        result = await send_ws_command(token, payload)
        print(f"Result: {result}")

    elif cmd == "schedules":
        if len(args) < 2:
            sys.exit("Usage: bhyve.py schedules <device-id-or-name>")
        d = find_device(devices, args[1])
        programs = d.get("program", []) or d.get("programs", [])
        if not programs:
            print("No schedules found for this device.")
            return
        for prog in programs:
            print(json.dumps(prog, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
