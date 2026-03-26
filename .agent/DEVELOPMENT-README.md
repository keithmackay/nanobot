# nanobot - Development Documentation Navigator

**Project**: Mac (nanobot) — Personal AI assistant on Telegram + Discord
**Tech Stack**: Python 3.13, asyncio, Claude API (Anthropic), SQLite, ChromaDB, aiohttp/httpx
**Updated**: 2026-03-25
**Navigator Version**: 4.7.0 (context-engineering only, not plugin-installed)

---

## Quick Start for Development

### New to This Project?
Read in this order:
1. [Project Architecture](./system/project-architecture.md) - Modules, data flow, session lifecycle
2. [Tech Stack Patterns](./system/tech-stack-patterns.md) - Python async patterns, skill authoring
3. README.md in project root - High-level overview

### Starting a New Feature?
1. Check `docs/` for relevant integration plan or prior art
2. Read relevant system docs from `system/` below
3. Check `nanobot/skills/` for existing skill patterns to follow
4. Generate implementation plan in `docs/` before coding

### Fixing a Bug?
1. Check `scripts/status.md` for service status context
2. Check `nanobot/channels/` for channel-specific issues
3. Logs: `nanobot gateway 2>&1 | head -200`

---

## Documentation Structure

```
.agent/
├── DEVELOPMENT-README.md     <- You are here (navigator)
├── system/
│   ├── project-architecture.md   <- Module map, data flow, LCM layers
│   └── tech-stack-patterns.md    <- Async patterns, skill format, config schema
├── tasks/                    <- Active implementation plans
│   └── (none yet)
└── sops/
    ├── integrations/         <- Channel setup, claude-mem, ChromaDB
    └── debugging/            <- Common errors, recovery steps
```

---

## Documentation Index

### System Architecture (`system/`)

#### [Project Architecture](./system/project-architecture.md)
**When to read**: Starting work on session/memory/LCM code; understanding data flow

**Contains**:
- Module map (`nanobot/agent`, `session`, `memory`, `db`, `channels`)
- Message lifecycle: inbound → agent loop → outbound
- LCM phases 1-5 (SQLite session store, DAG compaction, context assembler, tools, large-file)
- Memory layers: JSONL, SQLite, claude-mem, ChromaDB

**Updated**: 2026-03-25

#### [Tech Stack Patterns](./system/tech-stack-patterns.md)
**When to read**: Writing new skills, channels, or agent tools

**Contains**:
- Skill SKILL.md format and metadata schema
- Async channel pattern (BaseChannel subclass)
- Config schema extension (Pydantic models)
- Tool registration in agent loop

**Updated**: 2026-03-25

---

### Active Tasks (`tasks/`)

No active tasks currently. See `docs/lcm-integration-plan.md` for completed LCM work.

---

### SOPs (`sops/`)

#### Integrations
- claude-mem setup: see `docs/claude-mem-backfill-remote-projects.md`
- ChromaDB setup: see `memory/chromadb-setup.md` (in workspace)

#### Debugging
- LCM assembler errors: check `nanobot/db/` schema version, run migrations
- Discord rate-limit: `LimitOverrunError` fixed in `nanobot/channels/discord.py` (8MB stream limit)
- Cron jobs not firing: check `~/.nanobot/crons/` for JSON files, verify CronService logs

---

## On-Demand Loading Strategy

Load only what you need:

| Doc | Tokens | Load when |
|-----|--------|-----------|
| This navigator | ~2k | Session start |
| project-architecture.md | ~4k | Working on agent/session/memory |
| tech-stack-patterns.md | ~3k | Writing skills or channels |
| lcm-integration-plan.md | ~8k | LCM/compaction work |
| discord.py | ~10k | Discord channel bugs |
| loop.py | ~15k | Agent loop changes |

**Do NOT load all files upfront.** Use Task (Explore) for codebase searches.
