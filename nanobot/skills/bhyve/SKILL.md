---
name: bhyve
description: Control Orbit B-Hyve smart irrigation/sprinkler systems via the B-Hyve cloud API. Use when starting or stopping watering zones, checking irrigation schedules, getting zone status, listing B-Hyve sprinkler devices, or controlling Orbit B-Hyve timers and hose faucet timers. Uses reverse-engineered REST + WebSocket API.
---

# B-Hyve (Orbit)

Control Orbit B-Hyve irrigation systems via the reverse-engineered cloud REST + WebSocket API.

## Setup

### Install dependencies

```bash
pip install requests websockets
```

### Auth

B-Hyve uses email + password to get a session token.

Set credentials via environment variables:
```bash
export BHYVE_EMAIL="your@email.com"
export BHYVE_PASSWORD="yourpassword"
```

Or store in `~/.config/bhyve/config.json`:
```json
{
  "email": "your@email.com",
  "password": "yourpassword"
}
```

## Quick Reference

### List all devices/timers
```bash
python3 scripts/bhyve.py list
```

### Get device/zone status
```bash
python3 scripts/bhyve.py status <device-id-or-name>
```

### Start watering a zone
```bash
python3 scripts/bhyve.py start <device-id> <zone-number> <minutes>
# Example: water zone 1 for 10 minutes
python3 scripts/bhyve.py start abc123 1 10
```

### Stop all watering
```bash
python3 scripts/bhyve.py stop <device-id>
```

### Get schedules
```bash
python3 scripts/bhyve.py schedules <device-id-or-name>
```

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `https://api.orbitbhyve.com/v1/session` | POST | Login, returns `orbit_session_token` |
| `https://api.orbitbhyve.com/v1/devices` | GET | List all devices |
| `https://api.orbitbhyve.com/v1/devices/{id}` | GET | Get single device |
| `wss://api.orbitbhyve.com/v1/events` | WS | Real-time events + command channel |

### WebSocket command to start watering

```json
{
  "event": "change_mode",
  "mode": "manual",
  "device_id": "<device-id>",
  "timestamp": "2024-01-01T00:00:00.000Z",
  "stations": [{"station": 1, "run_time": 10}]
}
```

### WebSocket command to stop watering

```json
{
  "event": "change_mode",
  "mode": "auto",
  "device_id": "<device-id>",
  "timestamp": "2024-01-01T00:00:00.000Z"
}
```

## Notes

- Session tokens expire; the script re-authenticates automatically.
- Zone numbers are 1-indexed.
- `run_time` in WebSocket commands is in minutes.
- WebSocket connection requires `orbit_session_token` in the initial handshake message.
- Hose timer devices have a single zone (zone 1).

## Troubleshooting

- **401/403**: Re-authenticate — session token may be expired.
- **Device not responding**: Check the B-Hyve app to confirm device is online.
- **WebSocket timeout**: The cloud API can be slow; increase timeout if commands don't apply.
