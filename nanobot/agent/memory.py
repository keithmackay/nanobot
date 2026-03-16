"""Memory system for persistent agent memory."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

from nanobot.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session

# Attempt to import the SQLite-backed store (Phase 1).  If the module is not
# yet installed we fall back gracefully to the legacy JSONL-based behaviour.
try:
    from nanobot.session.store import SessionStore  # noqa: F401 — used in type hints
    from nanobot.memory.compaction import run_incremental_compaction, should_compact
    _DAG_COMPACTION_AVAILABLE = True
except ImportError:
    _DAG_COMPACTION_AVAILABLE = False

_std_logger = logging.getLogger(__name__)


_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log)."""

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    async def consolidate(
        self,
        session: Session,
        provider: LLMProvider,
        model: str,
        *,
        archive_all: bool = False,
        memory_window: int = 50,
    ) -> bool:
        """Consolidate old messages into MEMORY.md + HISTORY.md via LLM tool call.

        Returns True on success (including no-op), False on failure.

        DEPRECATED: Use trigger_dag_compaction() when the SQLite SessionStore
        backend is active.  This method remains in place as the fallback path
        for JSONL-backed sessions.
        """
        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info("Memory consolidation (archive_all): {} messages", len(session.messages))
        else:
            keep_count = memory_window // 2
            if len(session.messages) <= keep_count:
                return True
            if len(session.messages) - session.last_consolidated <= 0:
                return True
            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return True
            logger.info("Memory consolidation: {} to consolidate, {} keep", len(old_messages), keep_count)

        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}")

        current_memory = self.read_long_term()
        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{chr(10).join(lines)}"""

        try:
            response = await provider.chat(
                messages=[
                    {"role": "system", "content": "You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation."},
                    {"role": "user", "content": prompt},
                ],
                tools=_SAVE_MEMORY_TOOL,
                model=model,
            )

            if not response.has_tool_calls:
                logger.warning("Memory consolidation: LLM did not call save_memory, skipping")
                return False

            args = response.tool_calls[0].arguments
            # Some providers return arguments as a JSON string instead of dict
            if isinstance(args, str):
                args = json.loads(args)
            if not isinstance(args, dict):
                logger.warning("Memory consolidation: unexpected arguments type {}", type(args).__name__)
                return False

            if entry := args.get("history_entry"):
                if not isinstance(entry, str):
                    entry = json.dumps(entry, ensure_ascii=False)
                self.append_history(entry)
            if update := args.get("memory_update"):
                if not isinstance(update, str):
                    update = json.dumps(update, ensure_ascii=False)
                if update != current_memory:
                    self.write_long_term(update)

            session.last_consolidated = 0 if archive_all else len(session.messages) - keep_count
            logger.info("Memory consolidation done: {} messages, last_consolidated={}", len(session.messages), session.last_consolidated)
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return False


async def trigger_dag_compaction(
    session,
    store,
    llm_caller: Callable,
    model_context_limit: int,
) -> Optional[dict]:
    """Trigger DAG-based incremental compaction when the SQLite backend is active.

    Uses run_incremental_compaction() from nanobot.memory.compaction.  Returns
    the compaction result dict on success, or None if compaction was not needed
    or the DAG compaction module is unavailable.

    Falls back to legacy consolidation when _DAG_COMPACTION_AVAILABLE is False
    (i.e. store.py is not yet installed).

    Args:
        session:             SQLite Session object (nanobot.session.store.Session).
        store:               Active SessionStore instance.
        llm_caller:          Async callable (prompt: str, temp: float) -> str.
        model_context_limit: Token budget for the model in use.
    """
    if not _DAG_COMPACTION_AVAILABLE:
        _std_logger.debug(
            "DAG compaction unavailable (session.store not installed); skipping for session %s",
            getattr(session, "id", "?"),
        )
        return None

    try:
        if not should_compact(store, session, model_context_limit):
            return None
    except Exception:
        _std_logger.exception(
            "DAG compaction: should_compact check failed for session %s",
            getattr(session, "id", "?"),
        )
        return None

    try:
        result = await run_incremental_compaction(store, session, llm_caller)
        leaf = result.get("leaf_summary")
        condensed = result.get("condensed_summaries", [])
        _std_logger.info(
            "DAG compaction done for session %s: leaf=%s condensed=%d",
            getattr(session, "id", "?"),
            getattr(leaf, "id", None),
            len(condensed),
        )
        return result
    except Exception:
        _std_logger.exception(
            "DAG compaction failed for session %s", getattr(session, "id", "?")
        )
        return None
