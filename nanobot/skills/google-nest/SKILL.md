---
name: google-nest
description: Control Google Nest devices (thermostats, cameras, doorbells) via the Google Smart Device Management (SDM) API and the google-nest-sdm Python library. Use when getting Nest thermostat temperature, setting heating/cooling setpoints, changing HVAC mode, listing Nest devices, getting camera snapshots, checking Nest sensor readings, or any Google Nest smart home control. Requires a Google Cloud project, Device Access enrollment, and OAuth2 credentials.
---

# Google Nest

Control Nest thermostats, cameras, and sensors via the Google Smart Device Management (SDM) API.

## Setup

See `references/setup.md` for full step-by-step GCP + OAuth2 setup instructions.

### Install

```bash
pip install google-nest-sdm
```

### Auth

Requires OAuth2 credentials from a Google Cloud project. Store in `~/.config/nest/credentials.json`:

```json
{
  "project_id": "your-gcp-project-id",
  "device_access_project_id": "your-device-access-project-id",
  "client_id": "your-oauth-client-id",
  "client_secret": "your-oauth-client-secret",
  "refresh_token": "your-refresh-token"
}
```

## Quick Reference

### List all Nest devices
```bash
python3 scripts/nest.py list
```

### Get thermostat status
```bash
python3 scripts/nest.py status <device-id-or-name>
```

### Set temperature (Celsius)
```bash
python3 scripts/nest.py temp <device-id-or-name> 22.0
```

### Set HVAC mode
```bash
python3 scripts/nest.py mode <device-id-or-name> HEAT
python3 scripts/nest.py mode <device-id-or-name> COOL
python3 scripts/nest.py mode <device-id-or-name> HEATCOOL
python3 scripts/nest.py mode <device-id-or-name> OFF
```

### Set heat/cool range (for HEATCOOL mode)
```bash
python3 scripts/nest.py range <device-id-or-name> 20.0 24.0
```

### Get camera snapshot (saves to file)
```bash
python3 scripts/nest.py snapshot <device-id-or-name> [output.jpg]
```

## Supported Traits

| Trait | Description |
|---|---|
| `ThermostatTemperatureSetpoint` | Set heating/cooling target |
| `ThermostatMode` | HEAT, COOL, HEATCOOL, OFF |
| `Temperature` | Read ambient temperature |
| `Humidity` | Read relative humidity |
| `CameraLiveStream` | Live stream URL |
| `CameraImage` | Last camera image/snapshot |
| `DoorbellChime` | Doorbell event subscription |

## Notes

- Temperature values are always in **Celsius** (SDM API requirement).
- Device Access enrollment costs ~$5 one-time fee per Google account.
- OAuth2 tokens auto-refresh via `google-auth` library.
- Camera snapshots require a camera device with the `CameraImage` trait.
- The `google-nest-sdm` library is async; the helper uses `asyncio.run()`.
- Device IDs are long URIs like `enterprises/<project>/devices/<id>`.

## Troubleshooting

- **401**: OAuth token expired — re-run the auth flow in `references/setup.md`.
- **403**: Check IAM permissions on your GCP project and Device Access project ID.
- **No devices listed**: Confirm devices are linked in Google Home app and your Device Access project has the right account linked.
- **Camera snapshot fails**: Not all camera firmware versions support SDM snapshots.
