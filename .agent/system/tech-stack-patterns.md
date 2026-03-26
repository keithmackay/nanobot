# nanobot — Tech Stack Patterns

**Updated**: 2026-03-26 | **Python 3.13, asyncio, Pydantic**

## Skill SKILL.md Format

Every skill is a directory under `nanobot/skills/{name}/` or `{workspace}/skills/{name}/` with a `SKILL.md` file:

```markdown
---
name: skill-name
description: One-line summary of what this skill does.
homepage: https://...
metadata:
  openclaw:
    emoji: "🎯"
    os: ["darwin"]                    # Optional platform restriction
    requires: { bins: ["required-binary"] }
    install: [
      { id: "uv", kind: "uv", package: "pkg-name", bins: ["bin"], label: "Install via uv" }
    ]
---

# Skill Title

## Requirements
- What must be installed or configured

## Common Commands

\`\`\`bash
tool-name do-something --flag value
\`\`\`

## Options
| Flag | Default | Description |
|------|---------|-------------|
| `--flag` | `value` | What it does |
```

### Frontmatter Schema

| Field | Purpose |
|-------|---------|
| `name` | Skill identifier (= directory name) |
| `description` | One-line summary (used for skill selection) |
| `metadata.openclaw.os` | Platform restriction: `["darwin"]`, `["linux"]`, etc. |
| `metadata.openclaw.requires.bins` | Binaries that must be on PATH |
| `metadata.openclaw.install` | Install steps; `kind`: `uv`, `go`, `brew`, `apt` |

### Skill Loading

1. `SkillsLoader` scans `{workspace}/skills/` + `nanobot/skills/`
2. Workspace skills override builtins
3. `_check_requirements()` validates bins/packages before loading
4. Loaded SKILL.md content appended to system prompt

---

## BaseChannel Subclass Pattern

All channels inherit `BaseChannel(ABC)` from `nanobot/channels/base.py`:

```python
class MyChannel(BaseChannel):
    name = "mychannel"  # Must be unique

    def __init__(self, config: MyChannelConfig, bus: MessageBus):
        super().__init__(config, bus)
        self._client = None

    async def start(self) -> None:
        """Long-running listener. Block until stop() called."""
        self._running = True
        self._client = await create_platform_client(self.config.token)
        while self._running:
            msg = await self._client.next_message()
            if msg and self.is_allowed(msg.sender_id):
                await self._handle_message(
                    sender_id=msg.sender_id,
                    chat_id=msg.chat_id,
                    content=msg.text,
                )

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.close()

    async def send(self, msg: OutboundMessage) -> None:
        formatted = _format_for_platform(msg.content)
        await self._client.send_message(chat_id=msg.chat_id, text=formatted)
```

### Key Method Contracts

| Method | Contract |
|--------|----------|
| `start()` | Async long-running; loop on `self._running`; call `_handle_message()` for each msg |
| `stop()` | Set `_running = False`; close connections |
| `send(msg)` | Format content for platform; call platform API |
| `is_allowed(sender_id)` | Inherited; checks `config.allow_from` whitelist |
| `_handle_message()` | Inherited; validates permission, publishes `InboundMessage` to bus |

### Discord-Specific Notes
- `LimitOverrunError` fixed (8MB stream limit in `discord.py`)
- `require_mention` check waived when payload has attachments (no text to @-mention in)
- Per-guild + per-channel allow rules in `DiscordGuildConfig`

---

## Pydantic Config Schema

### Base Pattern

```python
from pydantic import BaseModel, Field, ConfigDict
from pydantic.alias_generators import to_camel

class Base(BaseModel):
    """Accepts both camelCase and snake_case keys."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

class MyChannelConfig(Base):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    retry_count: int = Field(default=3, ge=0)
```

### Adding a New Config Field

1. Add to the relevant model in `nanobot/config/schema.py`
2. Pydantic auto-validates on `Config.model_validate_json()`
3. Access via `self.config.new_field` in channel/tool code

### Nested Config

```python
class PersonalityConfig(Base):
    description: str
    allowed_skills: list[str] = Field(default_factory=list)  # empty = all
    denied_skills: list[str] = Field(default_factory=list)
    model: str | None = None
    temperature: float | None = None
```

---

## Tool Registration

### Tool ABC

```python
class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for parameters."""
        ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """Run tool; return string result."""
        ...

    def to_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters,
        }}
```

### Minimal Custom Tool

```python
class MyTool(Tool):
    @property
    def name(self) -> str: return "my_tool"

    @property
    def description(self) -> str: return "Does something useful."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
            },
            "required": ["query"]
        }

    async def execute(self, query: str) -> str:
        result = await call_external_api(query)
        return json.dumps(result)

# Register in AgentLoop.__init__ or _register_default_tools():
self.tools.register(MyTool())
```

### Invocation Flow

```
LLM returns tool_call {name, arguments}
    ↓
ToolRegistry.execute(name, params)
    ↓
Tool.validate_params() → JSON schema check
    ↓
Tool.execute(**params) → str result
    ↓
Result added to messages as tool_result
    ↓
LLM loop continues
```

---

## Async Patterns

### Main Loop

```python
async def run(self) -> None:
    self._running = True
    while self._running:
        try:
            msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        task = asyncio.create_task(self._dispatch(msg))
        self._active_tasks.setdefault(msg.session_key, []).append(task)
```

### Per-Session Serialization

```python
async def _dispatch(self, msg: InboundMessage) -> None:
    async with self._session_locks.setdefault(msg.session_key, asyncio.Lock()):
        # Only one message processed per session at a time
        ...
```

### Background Consolidation

```python
# Non-blocking — fire and forget
asyncio.create_task(self._consolidate_session(msg.session_key))
```

### Graceful /stop

```python
async def _handle_stop(self, key: str) -> None:
    tasks = self._active_tasks.pop(key, [])
    for t in tasks:
        if not t.done():
            t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
```

---

## LLM Provider Interface

```python
class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...

@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCallRequest]
    finish_reason: str             # "stop", "tool_calls", "length"
    usage: dict[str, int]          # {"input_tokens": N, "output_tokens": M}
    reasoning_content: str | None  # Reasoning models only
```

**Active providers**:
- `claude-cli/sonnet-4.6` — main agent model
- `custom/phi3:mini` — heartbeat model via ollama-proxy (port 11435)

---

## Quick Reference

| Pattern | File | Key Class/Function |
|---------|------|--------------------|
| Tool ABC | `agent/tools/base.py` | `Tool` |
| Tool registry | `agent/tools/registry.py` | `ToolRegistry.register()`, `.execute()` |
| Channel ABC | `channels/base.py` | `BaseChannel` |
| Skill loading | `agent/skills.py` | `SkillsLoader` |
| Config model | `config/schema.py` | `Base` (Pydantic) |
| Context assembly | `agent/context.py` | `ContextBuilder.build_system_prompt()` |
| Session I/O | `session/manager.py` | `SessionManager.get_or_create()` |
| Per-session lock | `agent/loop.py` | `_session_locks[key]` |
| LCM compaction | `memory/compaction.py` | `compact_leaf()`, `compact_condensed()` |
