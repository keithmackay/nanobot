"""Token-budget context assembler for LCM integration (Phase 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, (len(text) + 3) // 4)


@dataclass
class AssembleResult:
    """Result of a budget-aware context assembly."""

    messages: list[dict]      # list of {"role": ..., "content": ...} dicts for LLM
    estimated_tokens: int
    items_included: int
    items_evicted: int


def assemble_context(
    store: object,
    session: object,
    model_context_limit: int,
    fresh_tail_count: int = 32,
    max_budget_fraction: float = 0.75,
) -> "AssembleResult | None":
    """
    Budget-aware context assembly.

    Algorithm:
    1. Get all context_items ordered by ordinal.
    2. Identify fresh_tail = last fresh_tail_count MESSAGE-type items.
    3. Everything before fresh_tail = evictable.
    4. token_budget = model_context_limit * max_budget_fraction.
    5. Reserve tokens for fresh_tail first.
    6. Fill remaining budget from OLDEST evictable items (drop newest-first when over budget).
    7. Convert items to agent message dicts:
       - message items: {"role": msg.role, "content": msg.content}
       - summary items: {"role": "system", "content": "[Context Summary...]\n{summary.content}"}
    8. Return AssembleResult.

    Returns None to signal fallback if store is unavailable or assembly fails.
    """
    try:
        context_items = store.get_context_items(session.id)  # type: ignore[attr-defined]
    except Exception:
        return None

    if not context_items:
        return AssembleResult(messages=[], estimated_tokens=0, items_included=0, items_evicted=0)

    # ── Step 1: Resolve each context item ─────────────────────────────────────
    resolved: list[dict] = []  # {"ordinal", "role", "content", "tokens", "is_message"}

    for item in context_items:
        entry = _resolve_item(store, item)
        if entry is not None:
            resolved.append(entry)

    if not resolved:
        return AssembleResult(messages=[], estimated_tokens=0, items_included=0, items_evicted=0)

    # ── Step 2: Identify fresh tail (last N message-type items) ───────────────
    # Find indices of message-type items in resolved list
    message_indices = [i for i, r in enumerate(resolved) if r["is_message"]]
    if len(message_indices) <= fresh_tail_count:
        tail_start_idx = 0
    else:
        tail_start_idx = message_indices[-fresh_tail_count]

    fresh_tail = resolved[tail_start_idx:]
    evictable = resolved[:tail_start_idx]

    # ── Step 3: Budget calculation ─────────────────────────────────────────────
    token_budget = int(model_context_limit * max_budget_fraction)

    tail_tokens = sum(r["tokens"] for r in fresh_tail)
    remaining_budget = max(0, token_budget - tail_tokens)

    # ── Step 4: Fill remaining budget from evictable, oldest first ────────────
    # Walk evictable newest-to-oldest, accumulate, then reverse (keep oldest that fit)
    evictable_total = sum(r["tokens"] for r in evictable)
    evicted_count = 0

    if evictable_total <= remaining_budget:
        kept_evictable = evictable
        evictable_tokens = evictable_total
    else:
        kept: list[dict] = []
        accum = 0
        for item in reversed(evictable):
            if accum + item["tokens"] <= remaining_budget:
                kept.append(item)
                accum += item["tokens"]
            else:
                # Once an item doesn't fit, stop — all older items are also dropped
                break
        kept.reverse()
        kept_evictable = kept
        evictable_tokens = accum
        evicted_count = len(evictable) - len(kept)

    # ── Step 5: Build final ordered message list ───────────────────────────────
    selected = kept_evictable + fresh_tail
    messages = [{"role": r["role"], "content": r["content"]} for r in selected]
    estimated_tokens = evictable_tokens + tail_tokens

    return AssembleResult(
        messages=messages,
        estimated_tokens=estimated_tokens,
        items_included=len(selected),
        items_evicted=evicted_count,
    )


def _resolve_item(store: object, item: object) -> "dict | None":
    """
    Resolve a single ContextItem to a dict with role/content/tokens/is_message.

    Returns None if the underlying record cannot be fetched.
    """
    try:
        item_type = item.item_type  # type: ignore[attr-defined]
        item_id = item.item_id      # type: ignore[attr-defined]
        token_count = item.token_count  # type: ignore[attr-defined]
        ordinal = item.ordinal          # type: ignore[attr-defined]
    except AttributeError:
        return None

    if item_type == "message":
        return _resolve_message_item(store, item_id, token_count, ordinal)
    elif item_type == "summary":
        return _resolve_summary_item(store, item_id, token_count, ordinal)
    return None


def _resolve_message_item(store: object, item_id: int, token_count: int, ordinal: float) -> "dict | None":
    """Resolve a message-type context item."""
    try:
        # Search recent messages to find the one matching item_id.
        # We use a large count to cover all stored messages.
        messages = store.get_recent_messages(None, count=10000)  # type: ignore[attr-defined]
    except TypeError:
        # Signature may differ — try without session_id
        try:
            messages = store.get_recent_messages(count=10000)  # type: ignore[attr-defined]
        except Exception:
            return None
    except Exception:
        return None

    msg = next((m for m in messages if m.id == item_id), None)
    if msg is None:
        return None

    content = msg.content or ""
    tokens = token_count if token_count > 0 else _estimate_tokens(content)
    return {
        "ordinal": ordinal,
        "role": msg.role,
        "content": content,
        "tokens": tokens,
        "is_message": True,
    }


def _resolve_summary_item(store: object, item_id: int, token_count: int, ordinal: float) -> "dict | None":
    """Resolve a summary-type context item."""
    try:
        summary = store.get_summary(item_id)  # type: ignore[attr-defined]
    except Exception:
        return None

    if summary is None:
        return None

    earliest = getattr(summary, "earliest_at", "")
    latest = getattr(summary, "latest_at", "")
    header = f"[Context Summary from {earliest} to {latest}]"
    content = f"{header}\n{summary.content}"
    tokens = token_count if token_count > 0 else _estimate_tokens(content)

    return {
        "ordinal": ordinal,
        "role": "system",
        "content": content,
        "tokens": tokens,
        "is_message": False,
    }
