---
name: cync-ge
description: Control GE Cync (formerly C by GE) smart lights and plugs via the Cync cloud API. Use when controlling Cync lights, setting brightness, changing colors, turning Cync bulbs or plugs on/off, or getting status of GE Cync/C by GE devices. Uses reverse-engineered cloud API — no official SDK. Supports both cloud control and BLE where available.
---

# Cync / GE Cync (C by GE)

Control GE Cync smart lights via the `python-cync-lights` library (reverse-engineered cloud + BLE API).

## Setup

### Install

```bash
pip install python-cync-lights
```

Or from source (recommended — more current):
```bash
pip install git+https://github.com/nikshriv/cync_lights.git
```

### Auth

Cync uses email + password for cloud authentication. On first login, a 2FA code is sent to your phone/email.

Set credentials via environment variables:
```bash
export CYNC_EMAIL="your@email.com"
export CYNC_PASSWORD="yourpassword"
```

Or store in `~/.config/cync/config.json`:
```json
{
  "email": "your@email.com",
  "password": "yourpassword"
}
```

The library caches an auth token after first login (stored locally).

## Quick Reference

### List devices
```bash
python3 scripts/cync.py list
```

### Get device status
```bash
python3 scripts/cync.py status <name-or-id>
```

### Turn on/off
```bash
python3 scripts/cync.py on <name-or-id>
python3 scripts/cync.py off <name-or-id>
```

### Set brightness (0-100)
```bash
python3 scripts/cync.py brightness <name-or-id> 75
```

### Set color (RGB)
```bash
python3 scripts/cync.py color <name-or-id> 255 128 0
```

### Set color temperature (Kelvin, typically 2700-6500)
```bash
python3 scripts/cync.py colortemp <name-or-id> 3000
```

## Notes

- This is a **reverse-engineered** API — no official Cync/GE developer program exists.
- The library connects to Cync's cloud servers; no local-only mode.
- BLE control requires the machine to have Bluetooth hardware.
- Two-factor auth: on first run, the library will prompt for a code sent to your registered phone/email.
- Device names come from your Cync app; use exact names or partial matches.
- Not all devices support color — white-only bulbs will ignore RGB commands.

## Troubleshooting

- **Auth failure**: Delete any cached token file and re-authenticate.
- **Device unresponsive**: Ensure the bulb/plug is powered on and connected to Wi-Fi.
- **Color not working**: Device may be a white-only bulb — check the Cync app for color support.
- **Library install errors**: Try installing from GitHub source directly.
