# Google Nest SDM API — Setup Guide

Full step-by-step setup for the Google Smart Device Management (SDM) API,
required for the `google-nest` skill.

---

## Overview

You need three things:
1. A **Google Cloud Platform (GCP) project** with the Smart Device Management API enabled
2. A **Device Access project** (one-time $5 enrollment fee)
3. An **OAuth2 client** + a refresh token

Total setup time: ~30 minutes.

---

## Step 1: Create a Google Cloud Project

1. Go to https://console.cloud.google.com/
2. Click the project dropdown → **New Project**
3. Name it (e.g., `nanobot-nest`) → **Create**
4. Note your **Project ID** (e.g., `nanobot-nest-123456`)

---

## Step 2: Enable the Smart Device Management API

1. In the GCP console, go to **APIs & Services → Library**
2. Search for "Smart Device Management"
3. Click **Smart Device Management API** → **Enable**

---

## Step 3: Create OAuth2 Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. If prompted, configure the **OAuth consent screen**:
   - User type: **External** (or Internal if using Google Workspace)
   - Fill in app name, support email
   - Add your email as a test user
   - Scopes: add `https://www.googleapis.com/auth/sdm.service`
4. Back in Credentials → **Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: `nanobot-nest-client`
5. Download the credentials JSON → save as `client_secret.json`
6. Note your **Client ID** and **Client Secret**

---

## Step 4: Enroll in Device Access

1. Go to https://console.nest.google.com/device-access
2. Sign in with the Google account that owns your Nest devices
3. Click **Get Started** and agree to terms
4. Pay the **$5 one-time enrollment fee** (required per Google account)
5. Create a new project:
   - Project name: anything (e.g., `nanobot`)
   - OAuth client ID: paste the Client ID from Step 3
6. Note your **Device Access Project ID** (looks like `b73b9e72-...`)

---

## Step 5: Link Your Google Account

1. In the Device Access Console, click your project
2. Click **"Link Account"** and sign in with the same Google account as your Nest devices
3. Grant all requested permissions

---

## Step 6: Get a Refresh Token

Run this one-time OAuth flow to get a refresh token:

```bash
pip install google-auth-oauthlib
```

```python
# run_auth.py — run this once to get your refresh token
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/sdm.service"]

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",
    scopes=SCOPES,
)
creds = flow.run_local_server(port=0)
print(f"Refresh token: {creds.refresh_token}")
```

```bash
python3 run_auth.py
```

A browser window will open. Sign in and grant permissions.
Copy the printed **refresh token**.

---

## Step 7: Create the Config File

```bash
mkdir -p ~/.config/nest
```

Create `~/.config/nest/credentials.json`:

```json
{
  "project_id": "your-gcp-project-id",
  "device_access_project_id": "your-device-access-project-id",
  "client_id": "your-oauth-client-id.apps.googleusercontent.com",
  "client_secret": "your-oauth-client-secret",
  "refresh_token": "your-refresh-token"
}
```

**Protect this file:**
```bash
chmod 600 ~/.config/nest/credentials.json
```

---

## Step 8: Test the Connection

```bash
python3 /path/to/skills/google-nest/scripts/nest.py list
```

You should see your Nest devices listed.

---

## Troubleshooting

### "Access blocked: This app's request is invalid"
- Make sure your email is added as a test user in the OAuth consent screen.

### 401 Unauthorized
- Token may be expired. Re-run `run_auth.py` to get a fresh refresh token.

### 403 Forbidden
- Check that the SDM API is enabled in your GCP project.
- Verify the Device Access Project ID is correct.
- Ensure your Google account is linked in the Device Access console.

### No devices returned
- The account you authorized must be the same one that owns the Nest devices in Google Home.
- Check the Device Access console for linked accounts.

### Quota exceeded
- SDM API has rate limits. The free tier allows ~10,000 requests/day.

---

## Useful Links

- GCP Console: https://console.cloud.google.com/
- Device Access Console: https://console.nest.google.com/device-access
- SDM API Reference: https://developers.google.com/nest/device-access/api
- google-nest-sdm library: https://github.com/allenporter/python-google-nest-sdm
- OAuth Playground: https://developers.google.com/oauthplayground/
