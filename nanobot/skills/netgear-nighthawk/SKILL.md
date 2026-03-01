---
name: netgear-nighthawk
description: Query a Netgear Nighthawk (or other Netgear) router for connected devices, network info, and presence detection. Use when checking which devices are connected to the home network, detecting whether a specific person or device is home (by MAC address or hostname), getting router info, listing all LAN/Wi-Fi clients, or doing local network device presence checks. Fully local — no cloud required.
---

# Netgear Nighthawk

Query your Netgear router locally using `pynetgear` (SOAP-based local API).

## Setup

### Install

```bash
pip install pynetgear
```

### Auth

Local access — router IP + admin credentials.

Set via environment variables:
```bash
export NETGEAR_HOST="192.168.1.1"      # router IP (default: routerlogin.net)
export NETGEAR_USER="admin"             # usually "admin"
export NETGEAR_PASSWORD="yourpassword"  # router admin password
```

Or store in `~/.config/netgear/config.json`:
```json
{
  "host": "192.168.1.1",
  "user": "admin",
  "password": "yourpassword"
}
```

### Finding your router IP

```bash
netstat -nr | grep default | head -1
# or
ip route | grep default
```

## Quick Reference

### List all connected devices
```bash
python3 scripts/nighthawk.py list
```

### Check if a specific device is home (by name, MAC, or IP)
```bash
python3 scripts/nighthawk.py presence "Keith's iPhone"
python3 scripts/nighthawk.py presence "aa:bb:cc:dd:ee:ff"
python3 scripts/nighthawk.py presence "192.168.1.50"
```

### Get router info
```bash
python3 scripts/nighthawk.py info
```

### Get traffic stats
```bash
python3 scripts/nighthawk.py traffic
```

## Notes

- `pynetgear` uses the router's local SOAP API — fully local, no cloud needed.
- Supported routers: Nighthawk series (R6400, R7000, R8000, RAX series, etc.) and many other Netgear models.
- Device list shows currently connected clients (Wi-Fi + Ethernet).
- Presence detection: returns `home` if device appears in connected list, `away` otherwise.
- Some older firmware versions may need `force_login_v2=True` — see Troubleshooting.
- MAC addresses are in `aa:bb:cc:dd:ee:ff` format.

## Troubleshooting

- **Login failure**: Ensure you're using the router admin password (not Wi-Fi password).
- **Connection refused**: Check the host IP — try `192.168.0.1` or `routerlogin.net`.
- **No devices listed**: Some firmware versions require `ssl=True`; edit the script to enable it.
- **Older firmware**: Try initializing `Netgear(force_login_v2=True, ...)`.
