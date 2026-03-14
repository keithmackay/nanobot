# How This Nanobot Instance Was Built

This document chronicles the customizations made to [nanobot](https://github.com/nanobot-ai/nanobot) by Keith MacKay, starting from the upstream fork point on 2026-02-26. It captures what was built, why each decision was made, and the iterative process of turning an open-source framework into a production personal AI assistant.

---

## Project Overview

| Attribute | Value |
|-----------|-------|
| **Base Project** | nanobot-ai v0.1.4 (MIT) |
| **Fork Point** | 2026-02-26 (commit `cab901b`) |
| **Customization Start** | 2026-02-26 |
| **Last Updated** | 2026-03-13 |
| **Channels** | Telegram (`@snuglife_macbot`), Discord (`@snuglife_macbot`) |
| **Primary Language** | Python 3.13 |
| **Runtime** | `uv`, LaunchAgent, port 18790 |
| **Prior System** | OpenClaw (replaced) |

---

## Background

Keith ran **OpenClaw** as his personal AI gateway (Telegram + Discord bot) before this. Nanobot was adopted because OpenClaw lacked a subscription-based provider path — it required Anthropic API keys on every call. The migration goal: use a local `claude` binary (Pro/Max subscription) to eliminate per-token costs while keeping parity with OpenClaw's capabilities.

---

## Development Timeline

### Phase 1 — Claude CLI Provider Foundation (2026-02-26)

The entire migration hinged on one missing piece: nanobot had no way to use a local `claude` binary. Everything else followed from solving this first.

**Commits:** `f457fef`, `027231f`

| File | Change |
|------|--------|
| `nanobot/providers/claude_cli_provider.py` | New provider calling `claude --print` subprocess |
| `nanobot/config/schema.py` | `ClaudeCliConfig` added to provider registry |
| `tests/test_claude_cli_provider.py` | 29 tests: model aliases, prompt building, tool call parsing |

**Key decisions:**
- Tool calling via prompt injection + `<tool_call>` XML parsing (the `claude` binary doesn't expose a tool-use API over subprocess)
- Model shorthands (`haiku-4.5`, `sonnet-4.6`, etc.) mapped to full model IDs so config stays readable
- Tests written alongside the provider — this was the foundation everything else would run on

---

### Phase 2 — Core Infrastructure (2026-02-27)

A single day of foundational work establishing the full system shape.

**Commits:** `a0e1ee9`, `6ca9032`, `9fbffea`, `c072140`, `9c28142`, `ca5978c`, `25aad85`

#### Heartbeat model override (`a0e1ee9`)
The nanobot heartbeat (responsiveness probe) was using the main agent model. Added `gateway.heartbeat.model` config so a cheap local model (`phi3:mini` via ollama-proxy) handles heartbeats instead.

#### Discord allowlists + claude-mem (`6ca9032`)
OpenClaw had per-guild/per-channel allowlists. Nanobot didn't. Added:
- `DiscordGuildConfig` / `DiscordChannelRule` for guild → channel → user access control
- `ClaudeMemClient` for conversation logging and persistent context retrieval
- Context builder injects claude-mem history as a system prompt section per message

#### Config camelCase fix (`9fbffea`)
Root `Config` (BaseSettings) lacked `alias_generator=to_camel`, so top-level camelCase keys in `config.json` (e.g. `claudeMem`) silently failed to map to snake_case fields. Fixed with `populate_by_name=True`.

#### Provider timeout (`c072140`)
Default 120s subprocess timeout killed complex requests. Raised to 300s, made configurable via `providers.claude_cli.timeout`.

#### Skills migration (`9c28142`, `ca5978c`)
- Ported 54 skills from OpenClaw: `1password`, `apple-notes`, `notion`, `spotify-player`, `obsidian`, `home-assistant`, `youtube-transcript`, `stock-analysis`, and 46 others
- Skills are `.md` files with tool definitions; no code changes needed

#### Health service (`25aad85`)
Added `HealthService` that writes `health.json` to workspace every 60s, tracks last agent turn per channel, and logs WARNING when agent is silent beyond threshold. Wired into gateway, heartbeat, and cron callbacks. `nanobot health` CLI command reads it.

---

### Phase 3 — Background Task System (2026-02-27)

Long-running tasks (briefings, code analysis, research) can't block the agent loop. Built a fire-and-forget background runner on day one.

**Commits:** `da46b80`, `e37dcd4`, `45d01c5`, `c4792b5`, `0f2a517`

#### Core background runner (`da46b80`)
- `ClaudeCliProvider.run_task_streaming()` — async generator using `--output-format stream-json`, 15-minute stream timeout separate from per-turn chat timeout
- `TaskRegistry` persists task state in `workspace/tasks/`
- Per-session asyncio locks (replacing a global lock) so different channels run concurrently
- Immediate ACK before task starts; "still working…" updates every 3 minutes

#### Stderr drain + non-zero exit handling (`e37dcd4`)
Initial version missed failures: if `claude` exited non-zero with no JSON output, the task silently posted "✓ Task completed." Fixed by draining stderr concurrently via `asyncio.create_task` and synthesizing an error result event on non-zero exit.

**Key insight:** `asyncio.wait_for(readline())` corrupts `StreamReader`'s internal buffer on cancellation, causing false EOF. Replaced with `asyncio.wait({Task}, timeout)` which polls without cancelling — discovered and fixed in `594daa0` (Phase 6).

---

### Phase 4 — UX & Discord Polish (2026-02-27 – 2026-02-28)

**Commits:** `d4546d9`, `46e711e`, `23e28e6`, `ff4834b`

#### Emoji ACK (`d4546d9`, `46e771e`)
Replaced text "On it" acknowledgements with 👀 emoji reactions across Discord and Telegram. Matches Slack's existing pattern — consistent across all channels.

#### Multi-personality system (`23e28e6`)
The biggest UX feature: each Discord channel gets its own named AI persona.

| Config level | Field | Example |
|---|---|---|
| Channel | `personality: "brandy"` | `#marketing` → Brandy |
| Guild default | `personality: "default"` | fallback |
| Root config | `personalities.brandy: {...}` | skill filters, model overrides |

Each personality loads its own `SOUL.md` from `workspace/personalities/{name}/SOUL.md`. Skill filtering (allowed/denied lists) per personality. Personalities created: `default`, `briefing-bot`, `coding-assistant`, plus custom personas (`rex`, `archie`, `finn`, `sage`, `smoky`, `penn`, `theo`, `tessa`, `prax`, `brandy`).

#### Discord reconnect fix (`ff4834b`)
Fatal gateway close codes (4004 auth failure, 4010-4014) were causing infinite reconnect loops with a fixed 5s delay — identical to a prior OpenClaw bug. Fixed:
- Immediate stop on fatal codes
- Exponential backoff: 5s → 10s → 20s → … → 300s cap
- 50-attempt limit with counter reset on successful connection

---

### Phase 5 — Session Control & Home Automation (2026-03-01)

**Commits:** `565e921`, `dfeccef`, `d92b815`, `fd408ed`, `5ad03a8`

#### `new:` prefix session reset (`565e921`)
Added `new:` / `new topic:` prefix handling: clears the session's conversation history before processing the remainder of the message. Matches memory policy in `MEMORY.md` (clean-slate response).

#### Home automation skills (`dfeccef`)
Six home automation skills added: `smartthings`, `cync-ge`, `schlage-home`, `netgear-nighthawk`, `bhyve`, `google-nest`. Enables voice/text control of locks, lights, irrigation, and networking gear.

#### Snugban integration (`d92b815`, `fd408ed`)
Snugban is a local kanban board at `localhost:7420`. Added a skill for querying board state and a health sidecar check — snugban status now appears in `health.json`.

#### Typing indicator persistence (`5ad03a8`)
When background tasks post "Still working…" updates every 60s, the typing indicator was being killed by `send()`'s `finally` block. Fixed by tagging those messages with `_keep_typing=True` metadata and restarting the typing loop after sending.

---

### Phase 6 — Task Orchestration System (2026-03-01 – 2026-03-02)

The background runner (Phase 3) was ephemeral — tasks were lost on restart. Replaced with a durable SQLite-backed orchestration system.

**Commits:** `e5ad83a`, `594daa0`, `c33d3ef`, `85f165a`, `7e000bb`

#### Streaming timeout fixes (`e5ad83a`, `594daa0`)
Two bugs found and fixed during heavy task testing:
1. Per-readline 60s `asyncio.TimeoutError` was treated as overall stream timeout → premature "timed out after 900s" when Claude was just thinking quietly
2. Cancelling `readline()` via `wait_for()` corrupted `StreamReader` buffer → false EOF, tasks appearing complete when they weren't

Fix: `asyncio.wait({Task}, timeout=60)` polls without cancelling; deadline checked separately.

#### Permission + turn limits (`c33d3ef`)
Without `--dangerously-skip-permissions`, `claude` subprocess hung indefinitely waiting for permission prompts in non-TTY context. Added `--dangerously-skip-permissions` and `--max-turns 30` to all CLI calls.

#### Durable task orchestrator (`85f165a`)
Replaced ephemeral background runner with:
- `nanobot/tasks/db.py` — SQLite `TaskDB`, full lifecycle: `pending → running → done/failed`
- `nanobot/tasks/detector.py` — detects `Task:` / `/task:` prefix with inline options (e.g. `Task[model=haiku,poll=120]: research X`)
- `nanobot/tasks/orchestrator.py` — asyncio service polling every 5s, dispatching via `ClaudeCliProvider`, reporting progress to originating channel
- Stale tasks from prior process notified on restart

#### claude-mem response logging (`7e000bb`)
Extended `ClaudeMemClient` to POST assistant response text after every turn (sync and background paths). Enables persistent conversation memory across sessions and restarts.

---

### Phase 7 — Token Efficiency & Model Routing (2026-03-03)

**Commits:** `506ecc5`, `7a6c08c`, `8e7c9b7`, `41bc2d0`

#### Token efficiency work (`506ecc5`, `7a6c08c`)
Two categories of savings:
- **LLM-to-script replacements**: `brew-upgrade-claude-code.sh` replaced a 40K-token nanobot cron session with a direct shell script. Similarly `briefing-precheck.sh`, `github-trending.sh`, `briefing-fetch-data.sh`.
- **Eval data preprocessing**: `scripts/gather_eval_data.py` pre-processes Claude session JSONL files into compact Markdown before the nightly context eval — ~50-80K token savings per eval session.

#### Model routing (`8e7c9b7`)
Added a two-phase routing subsystem that classifies each inbound message on complexity (1–5) and statefulness (1–5) using haiku, then selects the cheapest capable tier:

| Condition | Route |
|---|---|
| complexity ≤ 1 AND statefulness ≤ 1 | haiku |
| complexity == 5, or ≥ 4 AND statefulness ≥ 3 | opus |
| everything else | sonnet (default floor) |

Routing metrics stored in SQLite (`routing/metrics.db`), viewable via `/routing-stats` CLI command. Biased toward sonnet to avoid quality regressions.

---

### Phase 8 — n8n & Webhook Integration (2026-03-07)

**Commits:** `d168280`, `bfcb84d`

#### n8n workflows (`d168280`)
Two automated workflows running in Colima (port 5678) on the Linux box:
- **Daily Briefing Pre-fetch** (5:50 AM): fetches BBC World/Tech, HN top 15, TechCrunch AI → writes `.prefetch-YYYY-MM-DD.md` to `KeithVault/Briefings/` before the 6am briefing session
- **Eval Data Pre-compute** (2:55 AM): runs `gather_eval_data.py` → writes `/tmp/eval-data-YYYY-MM-DD.md` for the 3am eval cron to read

#### HTTP webhook server (`bfcb84d`)
Added two endpoints on the gateway port (18790):
- `GET /health` — health snapshot JSON
- `POST /message` — inject a message into the agent synchronously, return response; supports `personality` routing field

Enables n8n workflows on the Linux box to call the Mac-side agent over LAN without going through Telegram/Discord.

---

### Phase 9 — Infrastructure Reliability (2026-03-06 – 2026-03-13)

**Commits:** `b6c252a`, `04b1cbe`

#### brew upgrade symlink repair (`b6c252a`, `04b1cbe`)
When `brew upgrade claude-code` fails mid-rename, it unlinks `/usr/local/bin/claude` and leaves it broken. This happened twice:

1. First fix (`b6c252a`): post-upgrade symlink repair — find latest Caskroom version, re-link.
2. Second fix (`04b1cbe`): the first fix assumed Caskroom still had files. When brew purges Caskroom entirely on rename failure, add a second fallback: `brew reinstall --cask claude-code`.

Root cause was a 24-hour outage on 2026-03-12 where `claude` was silently broken. The script now handles both failure modes.

---

## Current Configuration

### Channel Map

| Discord Channel | Personality | Notes |
|---|---|---|
| `#general-mac` | default | Main Mac channel |
| `#coding-assistant` | coding-assistant | Code help |
| `#rex` | rex | |
| `#archie` | archie | |
| `#finn` | finn | |
| `#sage` | sage | |
| `#smoky` | smoky | |
| `#penn` | penn | Writing assistant |
| `#theo` | theo | |
| `#tessa` | tessa | |
| `#prax` | prax | |
| `#marketing-brandy` | brandy | Marketing channel |
| Telegram | default | `@snuglife_macbot` |

### Service Architecture

```
LaunchAgent (com.nanobot.gateway)
└── nanobot gateway (Python/uv, port 18790)
    ├── Telegram channel (long polling)
    ├── Discord channel (WebSocket gateway)
    ├── HTTP webhook server (/health, /message)
    ├── Task orchestrator (SQLite, polls every 5s)
    ├── Health service (writes health.json every 60s)
    ├── Model router (haiku classifier → tier select)
    └── Cron service

LaunchAgent (com.openclaw.ollama-proxy, port 11435)
└── Proxies to ollama (port 11434) for phi3:mini heartbeat

n8n (Colima, port 5678)
├── Daily Briefing Pre-fetch (5:50 AM)
└── Eval Data Pre-compute (2:55 AM)
```

### Key Files

| Path | Purpose |
|---|---|
| `~/.nanobot/config.json` | Main config (tokens, providers, routing, personalities) |
| `~/.nanobot/workspace/` | Sessions, tasks DB, health.json, personalities/ |
| `~/.nanobot/workspace/sessions/` | Per-channel conversation history (JSONL) |
| `~/.nanobot/tasks/tasks.db` | Durable task queue |
| `~/.nanobot/routing/metrics.db` | Daily routing analytics |
| `scripts/` | Shell scripts replacing high-token cron sessions |
| `nanobot/routing/router.py` | Model routing logic |
| `nanobot/tasks/orchestrator.py` | Long-running task system |
| `nanobot/channels/discord.py` | Discord WebSocket channel |
| `nanobot/providers/claude_cli_provider.py` | Local `claude` binary provider |

---

## CLI Reference

### `nanobot cron` — Scheduled Tasks

```
Usage: nanobot cron [OPTIONS] COMMAND [ARGS]...

  Manage scheduled tasks

Options:
  --help    Show this message and exit.

Commands:
  list    List scheduled jobs.
  add     Add a scheduled job.
  remove  Remove a scheduled job.
  enable  Enable or disable a job.
  run     Manually run a job.
```

Cron jobs are stored in the workspace and run by the cron service inside the gateway process. Jobs can target any channel/chat and support standard cron expressions. Shell scripts are preferred over LLM-driven cron jobs for deterministic tasks (see Lessons Learned).

---

## Key Technical Decisions

| Decision | Rationale |
|---|---|
| Local `claude` binary as provider | Eliminates per-token API costs for Pro/Max subscribers |
| SQLite for task persistence | Tasks survive restarts; no external dependencies |
| `asyncio.wait()` instead of `wait_for()` for readline | Cancelling `StreamReader.readline()` corrupts buffer — learned from streaming bugs |
| Per-session asyncio locks | Global lock blocked all channels; per-session lets concurrent conversations run |
| Shell scripts for high-frequency cron jobs | 40K-token savings per brew-upgrade run vs. LLM-driven approach |
| Exponential backoff on Discord reconnect | Fixed-delay loops hammered Discord on auth failures — same bug existed in OpenClaw |
| Haiku as routing classifier | Cheap enough that classification cost < savings from downrouting; fast |
| `new:` prefix for session reset | Matches memory policy; simpler UX than `/new` command |

---

## Lessons Learned

- **`StreamReader` is not cancellation-safe.** Never `cancel()` an in-flight `readline()` coroutine — it corrupts internal buffer state. Use `asyncio.wait({task}, timeout)` to poll.
- **Brew upgrade is fragile.** The cask rename step can fail in two distinct ways (Caskroom survives vs. purged). Handle both.
- **LLM for cron jobs is expensive.** Every scheduled `nanobot` session that launches a Claude agent costs 30-80K tokens. Shell scripts for deterministic tasks pay for themselves immediately.
- **Non-zero exit without stderr surfacing = silent failures.** Always drain stderr concurrently; always synthesize an error event on non-zero exit.
- **Config camelCase/snake_case mismatch fails silently.** Pydantic's `BaseSettings` needs explicit `alias_generator` for camelCase JSON keys — missing it means config values just never load.

---

*Generated 2026-03-13. Updated with each significant change.*
