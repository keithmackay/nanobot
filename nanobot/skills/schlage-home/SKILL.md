---
name: schlage-home
description: Control Schlage Home smart locks via the Schlage cloud API. Use when locking or unlocking Schlage smart locks, checking lock status, getting battery level, viewing access logs, or managing Schlage Encode/Connect lock devices. Requires Schlage Home account credentials.
---

# Schlage Home

Control Schlage smart locks via `pyschlage` (reverse-engineered cloud API).

## Setup

### Install

```bash
pip install pyschlage
```

### Auth

Schlage uses email + password authentication against the Schlage Home cloud.

Set credentials via environment variables:
```bash
export SCHLAGE_EMAIL="your@email.com"
export SCHLAGE_PASSWORD="yourpassword"
```

Or store in `~/.config/schlage/config.json`:
```json
{
  "email": "your@email.com",
  "password": "yourpassword"
}
```

## Quick Reference

### List locks
```bash
python3 scripts/schlage.py list
```

### Get lock status
```bash
python3 scripts/schlage.py status <name-or-id>
```

### Lock
```bash
python3 scripts/schlage.py lock <name-or-id>
```

### Unlock
```bash
python3 scripts/schlage.py unlock <name-or-id>
```

### Get battery level
```bash
python3 scripts/schlage.py battery <name-or-id>
```

### Get access logs
```bash
python3 scripts/schlage.py logs <name-or-id>
python3 scripts/schlage.py logs <name-or-id> 20   # limit to last N entries
```

## Notes

- `pyschlage` uses the Schlage Home cloud API (same backend as the Schlage Home app).
- Supported devices: Schlage Encode (Wi-Fi), Schlage Connect (Z-Wave via SmartThings/Wink).
- The library is synchronous; no asyncio required.
- Access logs include timestamps and user codes (if named in the app).
- Battery is reported as a percentage (0–100).

## Troubleshooting

- **Auth failure**: Double-check email/password — same credentials as Schlage Home app.
- **Lock not responding**: Ensure the lock has Wi-Fi or bridge connectivity.
- **No devices listed**: Confirm lock is added to your Schlage Home account in the app.
- **pyschlage not found**: `pip install pyschlage`
