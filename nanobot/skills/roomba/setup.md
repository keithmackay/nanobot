# Roomba Skill Setup

## Requirements

```bash
pip install roombapy
```

## First-Time Setup

### Step 1: Put Roomba in pairing mode

1. Make sure your Roomba is **powered on** and connected to Wi-Fi (set up via the iRobot Home app first)
2. Press and **hold the HOME button** for 2-3 seconds until it beeps and the indicator light flashes

### Step 2: Run discovery (within 30 seconds of the beep)

```bash
python3 scripts/roombactl.py discover
```

This will:
- Scan the local network for your Roomba
- Retrieve the BLID (robot ID) and password
- Save config to `~/.config/roomba/config.json`

### Step 3: Verify

```bash
python3 scripts/roombactl.py status
```

You should see battery, phase, and bin status.

## Config File

`~/.config/roomba/config.json`:
```json
{
  "ip": "192.168.1.123",
  "blid": "1234567890ABCDEF",
  "password": ":1:1234567890:abcdefghij",
  "name": "Roomba i7"
}
```

## Multiple Roombas

Currently single-robot config. To support multiple units, duplicate the scripts directory and use separate config files (e.g., `~/.config/roomba/roomba2.json`), passing `--config` (extend the script).

## Network Notes

- Roomba must be on the same LAN as this machine
- Port 8883 (MQTT/TLS) must be reachable to the Roomba's IP
- No port forwarding or cloud connectivity needed
