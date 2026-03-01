---
name: smartthings
description: Control Samsung SmartThings smart home devices via the SmartThings cloud API. Use when listing SmartThings devices, checking device status, turning switches on/off, adjusting dimmer/light levels, locking/unlocking SmartThings-connected locks, reading thermostat state, or sending any SmartThings device command. Requires a SmartThings Personal Access Token.
---

# SmartThings

Control SmartThings devices via the official cloud API using `pysmartthings`.

## Setup

### 1. Get a Personal Access Token

1. Go to https://developer.smartthings.com
2. Sign in with your Samsung account
3. Navigate to "Personal Access Tokens" → "Generate new token"
4. Select all scopes you need (Devices, Locations, Scenes)
5. Copy the token

### 2. Set the environment variable

```bash
export SMARTTHINGS_TOKEN="your-personal-access-token"
```

Or store it in `~/.config/smartthings/config.json`:
```json
{
  "token": "your-personal-access-token"
}
```

## Quick Reference

### List devices
```bash
python3 scripts/st.py list
```

### Get device status
```bash
python3 scripts/st.py status <device-id-or-name>
```

### Switch on/off
```bash
python3 scripts/st.py switch <device-id-or-name> on
python3 scripts/st.py switch <device-id-or-name> off
```

### Set dimmer level (0-100)
```bash
python3 scripts/st.py dim <device-id-or-name> 50
```

### Lock/unlock
```bash
python3 scripts/st.py lock <device-id-or-name>
python3 scripts/st.py unlock <device-id-or-name>
```

### Thermostat
```bash
python3 scripts/st.py thermostat <device-id-or-name> heat 72
python3 scripts/st.py thermostat <device-id-or-name> cool 68
python3 scripts/st.py thermostat <device-id-or-name> auto 70
```

## Capabilities

| Capability | Commands |
|---|---|
| `switch` | `on`, `off` |
| `switchLevel` | `setLevel` (0–100) |
| `lock` | `lock`, `unlock` |
| `thermostatMode` | `setThermostatMode` (heat/cool/auto/off) |
| `thermostatHeatingSetpoint` | `setHeatingSetpoint` |
| `thermostatCoolingSetpoint` | `setCoolingSetpoint` |

## Notes

- The `pysmartthings` library is async; the helper script wraps calls with `asyncio.run()`.
- Device IDs are UUIDs. The script supports fuzzy name matching to avoid typing full UUIDs.
- Rate limit: 250 requests/minute per token.
- Cloud-only; no local control supported via this library.

## Troubleshooting

- **401**: Token expired or incorrect — regenerate at developer.smartthings.com.
- **403**: Token missing required scopes — regenerate with broader scopes.
- **Device not found**: Run `python3 scripts/st.py list` and copy the exact name or ID.
