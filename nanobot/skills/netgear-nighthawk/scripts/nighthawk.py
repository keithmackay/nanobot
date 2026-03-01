#!/usr/bin/env python3
"""
Netgear Nighthawk helper — wraps pynetgear for device listing and presence detection.

Usage:
  nighthawk.py list
  nighthawk.py presence <name|mac|ip>
  nighthawk.py info
  nighthawk.py traffic

Auth (all local — no cloud):
  Set NETGEAR_HOST, NETGEAR_USER, NETGEAR_PASSWORD env vars,
  or place in ~/.config/netgear/config.json.
"""

import json
import os
import sys
from pathlib import Path


def get_config():
    host = os.environ.get("NETGEAR_HOST", "routerlogin.net")
    user = os.environ.get("NETGEAR_USER", "admin")
    password = os.environ.get("NETGEAR_PASSWORD")
    if password:
        return host, user, password
    cfg = Path.home() / ".config" / "netgear" / "config.json"
    if cfg.exists():
        data = json.loads(cfg.read_text())
        return data.get("host", host), data.get("user", user), data["password"]
    sys.exit("ERROR: Set NETGEAR_PASSWORD env var or create ~/.config/netgear/config.json")


def get_router(host, user, password):
    from pynetgear import Netgear
    return Netgear(password=password, host=host, user=user)


def main():
    try:
        import pynetgear  # noqa: F401
    except ImportError:
        sys.exit("ERROR: Install pynetgear — pip install pynetgear")

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    host, user, password = get_config()
    router = get_router(host, user, password)

    if cmd == "list":
        devices = router.get_attached_devices()
        if not devices:
            print("No devices found (or login failed).")
            return
        print(f"{'Name':<35} {'MAC':<20} {'IP':<16} {'Link'}")
        print("-" * 80)
        for d in sorted(devices, key=lambda x: x.name or ""):
            name = (d.name or "unknown")[:34]
            mac = d.mac or "?"
            ip = d.ip or "?"
            link = getattr(d, 'link_rate', getattr(d, 'signal', '?'))
            print(f"{name:<35} {mac:<20} {ip:<16} {link}")

    elif cmd == "presence":
        if len(args) < 2:
            sys.exit("Usage: nighthawk.py presence <name|mac|ip>")
        query = args[1].lower()
        devices = router.get_attached_devices()
        if devices is None:
            sys.exit("ERROR: Could not retrieve device list — check credentials/host.")
        found = None
        for d in devices:
            name_match = d.name and query in d.name.lower()
            mac_match = d.mac and query == d.mac.lower()
            ip_match = d.ip and query == d.ip
            if name_match or mac_match or ip_match:
                found = d
                break
        if found:
            print(f"home  — {found.name or 'unknown'} ({found.mac}) @ {found.ip}")
        else:
            print(f"away  — '{args[1]}' not found in connected device list")

    elif cmd == "info":
        info = router.get_info()
        if not info:
            sys.exit("ERROR: Could not get router info — check credentials.")
        for key, val in vars(info).items() if hasattr(info, '__dict__') else info.items():
            print(f"{key}: {val}")

    elif cmd == "traffic":
        traffic = router.get_traffic_meter()
        if not traffic:
            print("Traffic metering not available or not enabled on this router.")
            return
        for key, val in (vars(traffic).items() if hasattr(traffic, '__dict__') else traffic.items()):
            print(f"{key}: {val}")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
