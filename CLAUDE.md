# nanobot — Claude Code Project Instructions

## Session Start Protocol

**At the start of every coding or research session**, ask Keith:

> "Should I use Navigator for this session? (y/n)"

**If yes — Navigator mode:**
1. Read `.agent/DEVELOPMENT-README.md` (the docs index, ~2k tokens)
2. Check `.agent/.context-markers/.active` — if it exists, offer to restore the saved marker
3. Load ONLY what that index says is relevant to the current task. Do NOT load all docs upfront.
4. Use Task/Explore agents for codebase research — never manually read 10+ files in-line
5. When a natural pause arrives (sub-task complete, switching focus), offer to run nav-compact: summarize progress into `.agent/.context-markers/` to free context

**If no — standard mode:**
Proceed normally. No Navigator constraints apply.

---

## Navigator Lazy-Loading Rules (when Navigator is active)

| Doc | Load when |
|-----|-----------|
| `.agent/DEVELOPMENT-README.md` | Always (session start) |
| `.agent/system/project-architecture.md` | Working on agent/session/LCM/memory |
| `.agent/system/tech-stack-patterns.md` | Writing new skills, channels, or tools |
| `.agent/tasks/TASK-*.md` | Actively implementing that task |
| `.agent/sops/debugging/*.md` | Debugging a known error pattern |
| `.agent/sops/integrations/*.md` | Setting up a channel or integration |

**Never load all docs at once.** That defeats the purpose.

---

## Nav-Compact (Context Marker) Protocol

When Navigator is active and a sub-task is complete, offer:

> "Sub-task done. Want me to compact context? I'll save a marker and we can continue with a fresh context."

If yes:
1. Write a marker to `.agent/.context-markers/YYYY-MM-DD-HH-MM-<slug>.md` with:
   - What was accomplished
   - Key decisions made
   - Files changed
   - What to do next
2. Write the filename to `.agent/.context-markers/.active`
3. Inform Keith the marker is saved and suggest `/clear` before the next sub-task

---

## Project Identity

- **What it is**: Mac — personal AI assistant on Telegram (@snuglife_macbot) and Discord (@snuglife_macbot)
- **Stack**: Python 3.13, asyncio, Claude API, SQLite, ChromaDB, aiohttp/httpx
- **Entry point**: `nanobot gateway` (runs as LaunchAgent on port 18790)
- **Config**: `~/.nanobot/config.json`

## General Rules

- Use `uv run` for Python commands (not `python3` directly)
- Logs: `nanobot gateway 2>&1 | head -200`
- Tests (if any): `uv run pytest`
- HTTPS remotes only for git push (SSH fails on this machine)
