---
name: snugban
description: Query and summarize the Snugban kanban board — a local task tracker for Mac's projects.
metadata:
  {
    "nanobot":
      { "emoji": "📋", "requires": { "bins": ["curl", "python3"] } },
  }
---

# Snugban Skill

Snugban is a local kanban board for tracking AI-assisted tasks across projects and channels.
The server runs at **http://localhost:7420** and is backed by `/Users/keithmackay1/Projects/snugban/data.json`.

## Ensure the server is running

Before querying, check if the server is up:

```bash
curl -s --max-time 2 http://localhost:7420/api/tasks > /dev/null 2>&1 || \
  (cd /Users/keithmackay1/Projects/snugban && python3 api.py &)
```

If the server was not running, wait 1-2 seconds before querying.

## Get all tasks

```bash
curl -s http://localhost:7420/api/tasks
```

Returns a JSON array of task objects. Each task has:
- `id`, `project`, `task` (description), `status`, `channel`, `owner`, `model`, `source`, `created`, `updated`

Valid statuses: `Todo`, `In Progress`, `Blocked`, `Done`

## Filter tasks

```bash
# By owner
curl -s "http://localhost:7420/api/tasks?owner=Mac"

# By project
curl -s "http://localhost:7420/api/tasks?project=sec-seer"

# By source
curl -s "http://localhost:7420/api/tasks?source=Telegram"

# Combined
curl -s "http://localhost:7420/api/tasks?owner=Mac&project=nanobot"
```

## Board status summary format

When asked for a board status or overview, fetch all tasks and format like this:

```
📋 SNUGBAN BOARD STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TODO (3):
  • [sec-seer] Add CORS policy (Mac, telegram)
  • [ssg-scanner] Add ARCHITECTURE.md (Archie, discord)
  • [ainstein-ai] Draft Talking Cubes v2 roadmap (Finn, discord)

IN PROGRESS (2):
  • [sec-seer] Fix Stripe webhook (Cody, discord)
  • [nanobot] Daily briefing cron job (Mac, telegram)

BLOCKED (2):
  • [dujour] Deploy to production (Finn, discord)
  • [sec-seer] Write E2E auth tests (Cody, telegram)

DONE (2):
  • [nanobot] Write memory session summary (Mac, telegram)
  • [nanobot] Replace OpenClaw with nanobot (Mac, discord)

Last updated: 2026-03-01 10:00 UTC
```

Format rules:
- Each task line: `  • [project] task description (owner, channel)`
- Truncate description to ~60 chars if long
- Count per column shown in parentheses after the column name
- If a column has no tasks, show `  (none)`

## Filtered status

When asked "show Mac's tasks" or "filter by owner=Mac":

```
📋 SNUGBAN — Mac's Tasks
━━━━━━━━━━━━━━━━━━━━━━━━
Filters: owner=Mac

TODO (1):
  • [sec-seer] Add CORS policy (telegram)

IN PROGRESS (1):
  • [nanobot] Daily briefing cron job (telegram)

BLOCKED (0):
  (none)

DONE (2):
  • [nanobot] Write memory session summary (telegram)
  • [nanobot] Replace OpenClaw with nanobot (discord)
```

## Create a task

```bash
curl -s -X POST http://localhost:7420/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "project": "sec-seer",
    "task": "Add rate limiting to /auth endpoint",
    "status": "Todo",
    "owner": "Mac",
    "channel": "telegram",
    "model": "claude-sonnet-4-6",
    "source": "Telegram"
  }'
```

Required fields: `project`, `task`, `status`, `owner`
Optional fields: `channel`, `model`, `source`

## Update a task (e.g., move to In Progress)

```bash
curl -s -X PUT http://localhost:7420/api/tasks/task-002 \
  -H 'Content-Type: application/json' \
  -d '{"status": "In Progress"}'
```

Any subset of fields can be updated. `updated` timestamp is set automatically.

## Delete a task

```bash
curl -s -X DELETE http://localhost:7420/api/tasks/task-002
```

## Start the server manually

```bash
cd /Users/keithmackay1/Projects/snugban && python3 api.py &
```

The board UI is available at: http://localhost:7420

## Notes

- Data persists in `/Users/keithmackay1/Projects/snugban/data.json`
- The server uses only Python stdlib (http.server) — no dependencies to install
- Filter params are case-insensitive
- IDs are auto-generated (e.g. `task-a1b2c3d4`); use the ID from GET responses for PUT/DELETE
