#!/usr/bin/env python3
"""
Google Nest SDM helper — wraps google-nest-sdm for common thermostat and camera ops.

Usage:
  nest.py list
  nest.py status <device-id-or-name>
  nest.py temp <device-id-or-name> <celsius>
  nest.py mode <device-id-or-name> HEAT|COOL|HEATCOOL|OFF
  nest.py range <device-id-or-name> <heat-celsius> <cool-celsius>
  nest.py snapshot <device-id-or-name> [output.jpg]

Auth:
  Place credentials in ~/.config/nest/credentials.json.
  See references/setup.md for full OAuth2 setup instructions.
"""

import asyncio
import json
import os
import sys
from pathlib import Path


CREDS_PATH = Path.home() / ".config" / "nest" / "credentials.json"
SDM_SCOPE = "https://www.googleapis.com/auth/sdm.service"


def load_credentials() -> dict:
    if not CREDS_PATH.exists():
        sys.exit(
            f"ERROR: Credentials not found at {CREDS_PATH}\n"
            "See references/setup.md for setup instructions."
        )
    return json.loads(CREDS_PATH.read_text())


def build_google_creds(creds: dict):
    """Build a google.oauth2.credentials.Credentials object from stored creds."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        sys.exit("ERROR: Install google-auth — pip install google-auth google-auth-oauthlib")

    gc = Credentials(
        token=None,
        refresh_token=creds["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        scopes=[SDM_SCOPE],
    )
    # Refresh to get a valid access token
    gc.refresh(Request())
    return gc


def find_device(devices: dict, query: str):
    """Find device by name (display name substring) or device ID suffix."""
    query_lower = query.lower()
    for device_id, device in devices.items():
        # Match on the full device ID
        if device_id == query or device_id.endswith(query):
            return device_id, device
        # Match on display name trait
        display_name = ""
        traits = device.traits
        for trait_name, trait in traits.items():
            if "Info" in trait_name:
                display_name = getattr(trait, "display_name", "")
                break
        if query_lower in display_name.lower():
            return device_id, device

    # Fallback: match on device type
    matches = [(did, d) for did, d in devices.items() if query_lower in did.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        sys.exit(f"Ambiguous query '{query}' — be more specific")
    sys.exit(f"ERROR: No device found matching '{query}'")


def get_display_name(device) -> str:
    for trait_name, trait in device.traits.items():
        if "Info" in trait_name:
            return getattr(trait, "display_name", "unknown")
    return "unknown"


async def cmd_list(api):
    devices = await api.async_get_devices()
    if not devices:
        print("No devices found.")
        return
    for device_id, device in devices.items():
        name = get_display_name(device)
        dtype = device.type
        traits = list(device.traits.keys())
        short_id = device_id.split("/")[-1]
        print(f"{name:<35} type={dtype}")
        print(f"  ID: ...{short_id}")
        print(f"  Traits: {', '.join(t.split('.')[-1] for t in traits)}")
        print()


async def cmd_status(api, query: str):
    devices = await api.async_get_devices()
    device_id, device = find_device(devices, query)

    print(f"Name:   {get_display_name(device)}")
    print(f"Type:   {device.type}")
    print(f"ID:     {device_id}")
    print()

    traits = device.traits
    for trait_name, trait in traits.items():
        short = trait_name.split(".")[-1]
        # Temperature
        if "Temperature" in short:
            temp_c = getattr(trait, "ambient_temperature_celsius", None)
            if temp_c is not None:
                print(f"Temperature:    {temp_c:.1f}°C ({temp_c * 9/5 + 32:.1f}°F)")
        # Humidity
        elif "Humidity" in short:
            h = getattr(trait, "ambient_humidity_percent", None)
            if h is not None:
                print(f"Humidity:       {h}%")
        # Thermostat mode
        elif "ThermostatMode" in short:
            mode = getattr(trait, "mode", None)
            available = getattr(trait, "available_modes", [])
            print(f"HVAC Mode:      {mode}  (available: {available})")
        # Thermostat setpoint
        elif "TemperatureSetpoint" in short:
            heat = getattr(trait, "heat_celsius", None)
            cool = getattr(trait, "cool_celsius", None)
            if heat is not None:
                print(f"Heat setpoint:  {heat:.1f}°C ({heat * 9/5 + 32:.1f}°F)")
            if cool is not None:
                print(f"Cool setpoint:  {cool:.1f}°C ({cool * 9/5 + 32:.1f}°F)")
        # HVAC status
        elif "ThermostatHvac" in short:
            status = getattr(trait, "status", None)
            print(f"HVAC Status:    {status}")


async def cmd_set_temp(api, query: str, celsius: float):
    devices = await api.async_get_devices()
    device_id, device = find_device(devices, query)

    traits = device.traits
    # Determine current mode to decide which setpoint to set
    mode = "HEAT"
    for trait_name, trait in traits.items():
        if "ThermostatMode" in trait_name:
            mode = getattr(trait, "mode", "HEAT")
            break

    for trait_name, trait in traits.items():
        if "TemperatureSetpoint" in trait_name:
            if mode == "HEAT":
                await trait.set_heat(celsius)
            elif mode == "COOL":
                await trait.set_cool(celsius)
            else:
                # For HEATCOOL, set both with a 2-degree spread
                await trait.set_heat(celsius - 1)
                await trait.set_cool(celsius + 1)
            print(f"OK: {get_display_name(device)} setpoint set to {celsius}°C")
            return

    sys.exit("ERROR: Device does not support temperature setpoint")


async def cmd_set_mode(api, query: str, mode: str):
    devices = await api.async_get_devices()
    device_id, device = find_device(devices, query)

    for trait_name, trait in device.traits.items():
        if "ThermostatMode" in trait_name:
            await trait.set_mode(mode)
            print(f"OK: {get_display_name(device)} mode set to {mode}")
            return

    sys.exit("ERROR: Device does not support thermostat mode")


async def cmd_set_range(api, query: str, heat_c: float, cool_c: float):
    devices = await api.async_get_devices()
    device_id, device = find_device(devices, query)

    for trait_name, trait in device.traits.items():
        if "TemperatureSetpoint" in trait_name:
            await trait.set_heat(heat_c)
            await trait.set_cool(cool_c)
            print(f"OK: {get_display_name(device)} range set to {heat_c}°C – {cool_c}°C")
            return

    sys.exit("ERROR: Device does not support temperature setpoint range")


async def cmd_snapshot(api, query: str, output_path: str):
    devices = await api.async_get_devices()
    device_id, device = find_device(devices, query)

    for trait_name, trait in device.traits.items():
        if "CameraImage" in trait_name:
            image = await trait.generate_image("image/jpeg")
            if image and hasattr(image, 'url'):
                import urllib.request
                urllib.request.urlretrieve(image.url, output_path)
                print(f"OK: Snapshot saved to {output_path}")
            else:
                print(f"Snapshot URL: {image}")
            return

    sys.exit("ERROR: Device does not support camera snapshots")


async def main():
    try:
        from google_nest_sdm.google_nest_subscriber import GoogleNestSubscriber
        from google_nest_sdm.auth import AbstractAuth
        import aiohttp
    except ImportError:
        sys.exit("ERROR: Install google-nest-sdm — pip install google-nest-sdm aiohttp")

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    creds_data = load_credentials()
    google_creds = build_google_creds(creds_data)
    device_access_project_id = creds_data["device_access_project_id"]

    class SimpleAuth(AbstractAuth):
        async def async_get_access_token(self) -> str:
            if google_creds.expired:
                from google.auth.transport.requests import Request
                google_creds.refresh(Request())
            return google_creds.token

    async with aiohttp.ClientSession() as session:
        auth = SimpleAuth(session, f"https://smartdevicemanagement.googleapis.com/v1/enterprises/{device_access_project_id}")

        from google_nest_sdm.device_manager import DeviceManager
        from google_nest_sdm import google_nest_api

        api = google_nest_api.GoogleNestAPI(auth, device_access_project_id)

        if cmd == "list":
            await cmd_list(api)

        elif cmd == "status":
            if len(args) < 2:
                sys.exit("Usage: nest.py status <device-id-or-name>")
            await cmd_status(api, args[1])

        elif cmd == "temp":
            if len(args) < 3:
                sys.exit("Usage: nest.py temp <device-id-or-name> <celsius>")
            await cmd_set_temp(api, args[1], float(args[2]))

        elif cmd == "mode":
            if len(args) < 3:
                sys.exit("Usage: nest.py mode <device-id-or-name> HEAT|COOL|HEATCOOL|OFF")
            await cmd_set_mode(api, args[1], args[2].upper())

        elif cmd == "range":
            if len(args) < 4:
                sys.exit("Usage: nest.py range <device-id-or-name> <heat-celsius> <cool-celsius>")
            await cmd_set_range(api, args[1], float(args[2]), float(args[3]))

        elif cmd == "snapshot":
            if len(args) < 2:
                sys.exit("Usage: nest.py snapshot <device-id-or-name> [output.jpg]")
            output = args[2] if len(args) >= 3 else "nest_snapshot.jpg"
            await cmd_snapshot(api, args[1], output)

        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
