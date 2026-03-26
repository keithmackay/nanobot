# nanobot — Project Architecture

**Updated**: 2026-03-26 | **Version**: LCM-integrated with DAG compaction

## Module Map

### Core Modules

| Module | Role | Key Files |
|--------|------|-----------|
| `nanobot.agent` | Agent loop & decision-making | `loop.py`, `context.py`, `memory.py`, `skills.py`, `subagent.py` |
| `nanobot.bus` | Message broker (inbound/outbound) | `queue.py`, `events.py` |
| `nanobot.channels` | Multi-platform connectors | `base.py`, `telegram.py`, `discord.py` |
| `nanobot.session` | Conversation persistence | `manager.py`, `store.py` |
| `nanobot.memory` | LCM DAG compaction & recall | `compaction.py`, `dag.py`, `assembler.py`, `large_file_handler.py` |
| `nanobot.db` | SQLite storage layer | `connection.py`, `migrations.py` |
| `nanobot.config` | Configuration schema (Pydantic) | `schema.py` |
| `nanobot.providers` | LLM backends | `base.py`, `claude_cli_provider.py`, `custom_provider.py` |
| `nanobot.agent.tools` | Tool registry & executors | `registry.py`, `base.py`, `filesystem.py`, `shell.py`, `web.py`, `mcp.py` |

### Secondary Modules

| Module | Role |
|--------|------|
| `nanobot.cli` | CLI interface (typer) |
| `nanobot.routing` | Model selection & routing |
| `nanobot.cron` | Scheduled task service |
| `nanobot.health` | Service health monitoring |
| `nanobot.webhook` | Webhook receivers |
| `nanobot.skills` | Skill loader & registry |

---

## Message Lifecycle

### 1. Inbound (Platform → Bus)

```
Chat Platform (Telegram/Discord)
    ↓
BaseChannel._handle_message()
    ↓
MessageBus.publish_inbound(InboundMessage)
    ↓
Queue → AgentLoop.run() awaits via bus.consume_inbound()
```

`InboundMessage`: `channel`, `sender_id`, `chat_id`, `content`, `media`, `session_key` (default: `"{channel}:{chat_id}"`)

### 2. Processing (Bus → Agent → Tools)

```
AgentLoop._dispatch(InboundMessage)
    ↓
1. Get/create Session from SessionManager
2. ContextBuilder.build_system_prompt()
3. Session.get_history() → fetch prior messages
4. Assemble prompt: system + history + new message
    ↓
LLMProvider.chat(messages, tools)
    ↓
5a. tool_calls → ToolRegistry.execute() → loop back
5b. no tool_calls → return final_content
    ↓
6. Update Session with assistant message
7. If session > threshold → async compaction pass
```

**Context layers** (system prompt assembly order):
1. Identity (runtime info, workspace)
2. Bootstrap files (SOUL.md, USER.md, AGENTS.md, TOOLS.md)
3. Long-term memory (MEMORY.md, HISTORY.md)
4. Semantic context (ChromaDB if memory_search triggered)
5. Persistent context (claude-mem if available)
6. Active skills (SKILL.md files)
7. Skills summary

### 3. Outbound (Agent → Platform)

```
bus.publish_outbound(OutboundMessage)
    ↓
MessageBus → BaseChannel.send()
    ↓
Channel formats content (e.g., Markdown → Telegram HTML)
    ↓
Platform API call
```

---

## LCM Phases (Lossless Context Management)

Five-stage DAG-based compaction in `nanobot.memory`:

| Phase | Trigger | Action | Output |
|-------|---------|--------|--------|
| 1: Leaf Summarization | ≥8 leaf msgs, ≥20k tokens | Summarize N messages → ~1.2k tokens | `Summary(kind="leaf", depth=0)` |
| 2: Condensed | ≥4 leaf summaries | Synthesize leaf summaries → ~2k tokens | `Summary(kind="condensed", depth=1+)` |
| 3: Context Assembly | Before LLM call (tight context) | ContextBuilder merges summaries in DAG order | Ordered merge with token budget |
| 4: Recall | User triggers memory tool | `memory_search(query)` over messages + summaries | Lexical FTS5 match |
| 5: Large File | File content >25k tokens | Extract to separate storage | Prevents message history bloat |

---

## Memory Layers

| Layer | Location | Purpose | Notes |
|-------|----------|---------|-------|
| JSONL session store | `{workspace}/sessions/{channel}_{chat_id}.jsonl` | Append-only message log | Per-session, LLM cache-friendly |
| SQLite (LCM) | `{workspace}/.nanobot.db` | DAG relationships, structured queries | Optional; WAL mode |
| File-based | `{workspace}/memory/MEMORY.md` | Agent-consolidated facts | Deprecated post-LCM |
| claude-mem | `localhost:37777` | Semantic search over prior prompts | External; manually indexed |
| ChromaDB | Remote `192.168.1.8:11434` | Vector search, ~51k vectors | `nomic-embed-text` via ollama |

---

## Key Files

### Entry Point
- `nanobot/__main__.py` → `nanobot gateway` command
- `nanobot/cli/commands.py` → Typer CLI, startup logic

### Agent Loop
- `nanobot/agent/loop.py` — `AgentLoop.run()`, `._dispatch()`, tool registration, compaction triggers

### Context & Memory
- `nanobot/agent/context.py` — System prompt assembly
- `nanobot/memory/compaction.py` — Leaf & condensed summary generation
- `nanobot/memory/dag.py` — Token estimation, DAG traversal

### Session & Storage
- `nanobot/session/manager.py` — SessionManager: JSONL I/O, caching
- `nanobot/db/connection.py` — Singleton SQLite init

### Channels
- `nanobot/channels/base.py` — BaseChannel ABC
- `nanobot/channels/telegram.py` — Telegram (markdown→HTML)
- `nanobot/channels/discord.py` — Discord WebSocket, rate-limit handling

### Tools
- `nanobot/agent/tools/registry.py` — Register & execute tools
- `nanobot/agent/tools/base.py` — Tool ABC
- `nanobot/agent/tools/filesystem.py` — read_file, write_file, edit_file, list_dir
- `nanobot/agent/tools/shell.py` — exec
- `nanobot/agent/tools/web.py` — web_search, web_fetch
- `nanobot/agent/tools/mcp.py` — MCP server connection

### Config
- `nanobot/config/schema.py` — Pydantic models for all config
- `~/.nanobot/config.json` — Live config (runtime)

---

## Critical Invariants

1. **Append-only JSONL** — never rewrite/delete messages; consolidate to summaries
2. **Session locks** — `_session_locks[session_key]` serializes processing per session
3. **Tool result limits** — `_TOOL_RESULT_MAX_CHARS` cap prevents token bloat
4. **Graceful degradation** — memory tools unavailable → continue without them
5. **Async task cleanup** — `/stop` must cancel all active tasks for session
