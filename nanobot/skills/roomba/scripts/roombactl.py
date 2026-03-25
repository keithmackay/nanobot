#!/usr/bin/env python3
"""
roombactl.py — iRobot Roomba control via local LAN (roombapy)
Usage: python3 roombactl.py <command> [--json]
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "roomba" / "config.json"


def load_config():
    if not CONFIG_PATH.exists():
        print(f"ERROR: Config not found at {CONFIG_PATH}", file=sys.stderr)
        print("Run: python3 roombactl.py discover", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Config saved to {CONFIG_PATH}")


def cmd_discover(args):
    """Discover Roomba on LAN and extract password."""
    try:
        from roombapy.discovery import RoombaDiscovery
        from roombapy.password import RoombaPassword
    except ImportError:
        print("ERROR: roombapy not installed. Run: pip install roombapy", file=sys.stderr)
        sys.exit(1)

    print("Scanning for Roomba on local network...")
    print("If prompted, press and hold the HOME button on your Roomba for 2 seconds until it beeps.\n")

    discovery = RoombaDiscovery()
    robots = discovery.get_all()

    if not robots:
        print("No Roomba found. Ensure it's powered on and connected to Wi-Fi.")
        sys.exit(1)

    for robot in robots:
        ip = robot.get("ip")
        blid = robot.get("robotid") or robot.get("blid")
        name = robot.get("robotname", "Unknown")
        print(f"Found: {name} | IP: {ip} | BLID: {blid}")

        print(f"\nFetching password for {name} (press HOME button now if not already)...")
        try:
            pwd = RoombaPassword(ip).get_password()
            config = {"ip": ip, "blid": blid, "password": pwd, "name": name}
            save_config(config)
            print(f"Password retrieved successfully.")
            if args.json:
                print(json.dumps(config, indent=2))
        except Exception as e:
            print(f"Failed to get password: {e}", file=sys.stderr)
            print("Tip: Hold HOME button on robot until it beeps, then retry within 30s.")


def _get_roomba(config):
    try:
        from roombapy import Roomba
    except ImportError:
        print("ERROR: roombapy not installed. Run: pip install roombapy", file=sys.stderr)
        sys.exit(1)
    return Roomba(
        address=config["ip"],
        blid=config["blid"],
        password=config["password"],
        continuous=False,
        delay=0.5,
    )


def cmd_status(args):
    config = load_config()
    roomba = _get_roomba(config)

    roomba.connect()
    time.sleep(3)  # allow status to populate

    master_state = roomba.master_state
    roomba.disconnect()

    if not master_state:
        print("ERROR: No state received from Roomba.", file=sys.stderr)
        sys.exit(1)

    state = master_state.get("state", {}).get("reported", {})
    phase = state.get("cleanMissionStatus", {}).get("phase", "unknown")
    battery = state.get("batPct", "?")
    bin_full = state.get("bin", {}).get("full", False)
    error = state.get("cleanMissionStatus", {}).get("error", 0)
    docked = phase in ("charge", "stop") and not state.get("cleanMissionStatus", {}).get("cycle") == "clean"
    cleaning = phase == "run"

    result = {
        "name": config.get("name", "Roomba"),
        "ip": config["ip"],
        "phase": phase,
        "battery": battery,
        "bin_full": bin_full,
        "docked": docked,
        "cleaning": cleaning,
        "error": error,
        "raw_phase": phase,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        name = result["name"]
        print(f"=== {name} Status ===")
        print(f"  Phase:    {phase}")
        print(f"  Battery:  {battery}%")
        print(f"  Cleaning: {'Yes' if cleaning else 'No'}")
        print(f"  Docked:   {'Yes' if docked else 'No'}")
        print(f"  Bin full: {'Yes' if bin_full else 'No'}")
        if error:
            print(f"  Error:    {error}")


def cmd_start(args):
    config = load_config()
    roomba = _get_roomba(config)
    roomba.connect()
    time.sleep(1)
    roomba.send_command("start")
    time.sleep(1)
    roomba.disconnect()
    msg = {"status": "ok", "command": "start", "message": "Cleaning started"}
    if args.json:
        print(json.dumps(msg))
    else:
        print("Roomba started cleaning.")


def cmd_stop(args):
    config = load_config()
    roomba = _get_roomba(config)
    roomba.connect()
    time.sleep(1)
    roomba.send_command("stop")
    time.sleep(1)
    roomba.disconnect()
    msg = {"status": "ok", "command": "stop", "message": "Cleaning stopped"}
    if args.json:
        print(json.dumps(msg))
    else:
        print("Roomba stopped.")


def cmd_dock(args):
    config = load_config()
    roomba = _get_roomba(config)
    roomba.connect()
    time.sleep(1)
    roomba.send_command("dock")
    time.sleep(1)
    roomba.disconnect()
    msg = {"status": "ok", "command": "dock", "message": "Roomba returning to dock"}
    if args.json:
        print(json.dumps(msg))
    else:
        print("Roomba returning to dock.")


def main():
    parser = argparse.ArgumentParser(description="Control iRobot Roomba via local LAN")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("discover", help="Discover Roomba on LAN and save config")
    sub.add_parser("status", help="Get current Roomba status")
    sub.add_parser("start", help="Start cleaning")
    sub.add_parser("stop", help="Stop cleaning")
    sub.add_parser("dock", help="Send Roomba home to dock")

    args = parser.parse_args()

    commands = {
        "discover": cmd_discover,
        "status": cmd_status,
        "start": cmd_start,
        "stop": cmd_stop,
        "dock": cmd_dock,
    }

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands[args.command](args)


if __name__ == "__main__":
    main()
