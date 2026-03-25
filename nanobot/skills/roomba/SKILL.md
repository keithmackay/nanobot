---
name: roomba
description: Control iRobot Roomba vacuum cleaners via local LAN (no cloud required). Use when starting/stopping cleaning, docking, checking status, battery level, or bin state. Supports Roomba 900-series, i-series, j-series, s-series (anything with Wi-Fi + app). Uses roombapy for local MQTT-based control.
---

# Roomba

Control iRobot Roomba vacuums via the local LAN REST/MQTT API using `roombapy`.

## Setup

See `setup.md` for first-time configuration (getting the password from your Roomba).

Config is stored at `~/.config/roomba/config.json`:
```json
{
  "ip": "192.168.1.xxx",
  "blid": "your-robot-blid",
  "password": "your-robot-password"
}
```

## Quick Reference

### Discover Roomba on LAN + get password
```bash
python3 scripts/roombactl.py discover
```

### Check status
```bash
python3 scripts/roombactl.py status
python3 scripts/roombactl.py status --json
```

### Start cleaning
```bash
python3 scripts/roombactl.py start
```

### Stop cleaning
```bash
python3 scripts/roombactl.py stop
```

### Send home to dock
```bash
python3 scripts/roombactl.py dock
```

## Status fields

| Field | Description |
|---|---|
| `phase` | Current phase: `run`, `stop`, `hmUsrDock`, `charge`, etc. |
| `battery` | Battery % (0-100) |
| `bin_full` | Whether the bin is full (bool) |
| `docked` | Whether Roomba is on the dock (bool) |
| `cleaning` | Whether currently cleaning (bool) |
| `error` | Error code (0 = no error) |

## Notes

- All control is **local LAN only** -- no cloud, no iRobot account needed after setup
- Roomba must be on the same Wi-Fi network
- Password extraction requires briefly pressing the HOME button on the robot
- `roombapy` uses MQTT over port 8883 (TLS, self-signed cert)
- Commands are fire-and-forget; status polling is async via callback

## Troubleshooting

- **Connection refused**: Check IP in config, ensure Roomba is on same network
- **Auth failed**: Re-run `discover` to get a fresh password
- **No response**: Roomba may be sleeping -- start cleaning via app first, then retry
