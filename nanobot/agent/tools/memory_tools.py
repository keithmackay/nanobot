"""Memory/LCM recall tools: memory_search, memory_describe, memory_expand."""

from __future__ import annotations

import asyncio
from typing import Any

from nanobot.agent.tools.base import Tool


_STORE_UNAVAILABLE = "Memory search unavailable (SQLite store not initialized)"


def _get_store() -> "object | None":
    """Attempt to get a SessionStore from config. Returns None if unavailable."""
    try:
        from nanobot.config.loader import load_config
        from nanobot.session.store import SessionStore

        cfg = load_config()
        if not hasattr(cfg, "session_store") or cfg.session_store.backend != "sqlite":
            return None
        return SessionStore(cfg.session_store)
    except Exception:
        return None


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


class MemorySearchTool(Tool):
    """Search past conversation history by keyword or full-text."""

    name = "memory_search"
    description = (
        "Search past conversation history by keyword or regex. "
        "Returns matching messages and summaries."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (keyword or regex pattern)",
            },
            "mode": {
                "type": "string",
                "enum": ["full_text", "regex"],
                "description": "Search mode: full_text (default) or regex",
            },
            "scope": {
                "type": "string",
                "enum": ["messages", "summaries", "both"],
                "description": "What to search: messages, summaries, or both (default)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 20)",
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": ["query"],
    }

    async def execute(
        self,
        query: str,
        mode: str = "full_text",
        scope: str = "both",
        limit: int = 20,
        **kwargs: Any,
    ) -> str:
        store = await asyncio.to_thread(_get_store)
        if store is None:
            return _STORE_UNAVAILABLE

        try:
            results = await asyncio.to_thread(
                store.search,  # type: ignore[attr-defined]
                query,
                mode=mode,
                scope=scope,
                limit=limit,
            )
        except Exception as e:
            return f"Error searching memory: {e}"

        if not results:
            return f"No results found for: {query!r}"

        lines = [f"Found {len(results)} result(s) for {query!r}:\n"]
        for r in results:
            item_type = getattr(r, "item_type", "msg")
            item_id = getattr(r, "item_id", "?")
            session_id = getattr(r, "session_id", "?")
            created_at = getattr(r, "created_at", "")
            snippet = getattr(r, "snippet", "") or getattr(r, "content", "")[:200]

            # Format a compact label: [msg:42] or [sum:17]
            prefix = "sum" if item_type == "summary" else "msg"
            label = f"[{prefix}:{item_id}]"
            lines.append(f"{label} session:{session_id} ({created_at})")
            if snippet:
                # Truncate long snippets
                display = snippet[:300] + ("..." if len(snippet) > 300 else "")
                lines.append(f"  {display}")
            lines.append("")

        return "\n".join(lines)


class MemoryDescribeTool(Tool):
    """Look up a specific summary or message by ID."""

    name = "memory_describe"
    description = (
        "Get full content and lineage for a message (msg:N) or summary (sum:N). "
        "Use IDs returned by memory_search."
    )
    parameters = {
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": 'Item identifier, e.g. "msg:42" or "sum:17"',
                "minLength": 3,
            },
        },
        "required": ["item_id"],
    }

    async def execute(self, item_id: str, **kwargs: Any) -> str:
        store = await asyncio.to_thread(_get_store)
        if store is None:
            return _STORE_UNAVAILABLE

        try:
            result = await asyncio.to_thread(
                store.describe,  # type: ignore[attr-defined]
                item_id,
            )
        except Exception as e:
            return f"Error describing {item_id!r}: {e}"

        if result is None:
            return f"Item {item_id!r} not found."

        return str(result)


class MemoryExpandTool(Tool):
    """Deep-dive into a summary DAG node to answer a specific question."""

    name = "memory_expand"
    description = (
        "Expand a compressed summary to answer a specific question about past context. "
        "Collects child summaries and source messages up to token_cap, then answers with citations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "summary_id": {
                "type": "string",
                "description": 'Summary identifier, e.g. "sum:17" or just "17"',
                "minLength": 1,
            },
            "question": {
                "type": "string",
                "description": "The specific question to answer from the expanded context",
                "minLength": 1,
            },
            "token_cap": {
                "type": "integer",
                "description": "Max tokens of context to collect (default 4000)",
                "minimum": 100,
                "maximum": 50000,
            },
        },
        "required": ["summary_id", "question"],
    }

    async def execute(
        self,
        summary_id: str,
        question: str,
        token_cap: int = 4000,
        **kwargs: Any,
    ) -> str:
        store = await asyncio.to_thread(_get_store)

        # Parse "sum:17" → 17
        raw_id = summary_id.lstrip("sum:").strip()
        try:
            sid = int(raw_id)
        except ValueError:
            sid = None

        # ── Step 1: Get the top-level summary ─────────────────────────────────
        top_summary = None
        if store is not None and sid is not None:
            try:
                top_summary = await asyncio.to_thread(
                    store.get_summary,  # type: ignore[attr-defined]
                    sid,
                )
            except Exception:
                pass

        if top_summary is None:
            if store is None:
                return _STORE_UNAVAILABLE
            return f"Summary {summary_id!r} not found."

        # ── Step 2: Collect child summaries + source messages up to token_cap ──
        context_parts: list[str] = []
        token_accum = 0
        cited_ids: list[str] = []
        truncated = False

        top_content = getattr(top_summary, "content", "")
        top_tokens = _estimate_tokens(top_content)

        if token_accum + top_tokens <= token_cap:
            context_parts.append(f"[sum:{sid}] {top_content}")
            cited_ids.append(f"sum:{sid}")
            token_accum += top_tokens
        else:
            context_parts.append(f"[sum:{sid}] {top_content[:token_cap * 4]}")
            cited_ids.append(f"sum:{sid}")
            truncated = True

        # Collect child summaries
        if not truncated:
            try:
                children = await asyncio.to_thread(
                    store.get_summary_children,  # type: ignore[attr-defined]
                    sid,
                )
                for child in children:
                    if truncated:
                        break
                    child_id = getattr(child, "id", None)
                    child_content = getattr(child, "content", "")
                    child_tokens = _estimate_tokens(child_content)
                    if token_accum + child_tokens > token_cap:
                        truncated = True
                        break
                    context_parts.append(f"[sum:{child_id}] {child_content}")
                    cited_ids.append(f"sum:{child_id}")
                    token_accum += child_tokens
            except Exception:
                pass

        # Collect source messages
        if not truncated:
            try:
                msgs = await asyncio.to_thread(
                    store.get_summary_messages,  # type: ignore[attr-defined]
                    sid,
                )
                for m in msgs:
                    if truncated:
                        break
                    m_id = getattr(m, "id", None)
                    m_role = getattr(m, "role", "?")
                    m_content = getattr(m, "content", "")
                    m_tokens = _estimate_tokens(m_content)
                    if token_accum + m_tokens > token_cap:
                        truncated = True
                        break
                    context_parts.append(f"[msg:{m_id}] {m_role}: {m_content}")
                    cited_ids.append(f"msg:{m_id}")
                    token_accum += m_tokens
            except Exception:
                pass

        # ── Step 3: If no LLM available inline, just return the context ───────
        # We don't have direct LLM access in tools, so return formatted context
        # with the question highlighted so the agent can answer from it.
        context_text = "\n\n".join(context_parts)
        citations = ", ".join(cited_ids) if cited_ids else "none"
        trunc_note = "\n\n[Context was truncated due to token_cap]" if truncated else ""

        return (
            f"Expanded context for {summary_id!r} (cited: {citations}):{trunc_note}\n\n"
            f"{context_text}\n\n"
            f"---\nQuestion: {question}\n"
            f"(Use the context above to answer this question.)"
        )
