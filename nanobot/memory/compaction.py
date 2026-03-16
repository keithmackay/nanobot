"""Leaf + condensed compaction passes for LCM-style DAG session compression."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Optional

from nanobot.memory.dag import (
    estimate_tokens,
    format_messages_for_summary,
    format_summaries_for_condensation,
    get_condensable_summaries,
    get_leaf_messages,
)

if TYPE_CHECKING:
    from nanobot.session.store import Session, SessionStore, Summary

logger = logging.getLogger(__name__)

# ── Compaction parameters ──────────────────────────────────────────────────────

LEAF_CHUNK_TOKENS = 20_000
LEAF_TARGET_TOKENS = 1_200
CONDENSED_TARGET_TOKENS = 2_000
FRESH_TAIL_COUNT = 32
LEAF_MIN_FANOUT = 8       # min messages before leaf pass triggers
CONDENSED_MIN_FANOUT = 4  # min summaries before condensation triggers
INCREMENTAL_MAX_DEPTH = 2

# Deterministic fallback: truncate raw text to ~512 tokens (≈2048 chars)
_FALLBACK_MAX_CHARS = 512 * 4

# ── Prompts ────────────────────────────────────────────────────────────────────

LEAF_SUMMARY_PROMPT = """You are compressing conversation history into a lossless summary.
Create a dense summary that preserves all key information: decisions made, facts learned,
questions asked, code written, errors encountered, and open items.

Include an "Expand for details about:" line listing topics that were discussed but compressed.
Format with an XML wrapper:

<summary id="{summary_id}" kind="leaf" depth="0"
         earliest_at="{earliest_at}"
         latest_at="{latest_at}">
  <content>
    [Your summary here]

    Expand for details about: [comma-separated topics]
  </content>
</summary>

Target: ~{target_tokens} tokens. Be dense and factual."""

CONDENSED_SUMMARY_PROMPT = """You are creating a higher-level summary from multiple conversation summaries.
Synthesize the key themes, decisions, and outcomes across these summaries.
Be more abstract than a leaf summary — focus on narrative arc and key outcomes.

Format:
<summary id="{summary_id}" kind="condensed" depth="{depth}"
         earliest_at="{earliest_at}"
         latest_at="{latest_at}">
  <content>
    [Your synthesis here]
  </content>
</summary>

Target: ~{target_tokens} tokens."""


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _summarize_with_escalation(
    source_text: str,
    llm_caller: Callable,
    prompt_template: str,
    format_kwargs: dict,
    target_tokens: int,
) -> tuple[str, str]:
    """Three-tier escalation: normal → aggressive → deterministic fallback.

    Returns (summary_content, level) where level is one of:
    "normal", "aggressive", "fallback".
    """
    source_text = source_text.strip()
    if not source_text:
        return "[Truncated from 0 tokens]", "fallback"

    input_tokens = max(1, estimate_tokens(source_text))

    # Tier 1: normal
    try:
        prompt = prompt_template.format(
            **{**format_kwargs, "target_tokens": target_tokens}
        )
        result = await llm_caller(prompt, 0.2)
        if estimate_tokens(result) < input_tokens:
            return result, "normal"
    except Exception:
        logger.exception("LCM leaf escalation tier 1 failed")

    # Tier 2: aggressive (lower temperature, smaller target)
    try:
        aggressive_target = target_tokens // 2
        prompt = prompt_template.format(
            **{**format_kwargs, "target_tokens": aggressive_target}
        )
        result = await llm_caller(prompt, 0.1)
        if estimate_tokens(result) < input_tokens:
            return result, "aggressive"
    except Exception:
        logger.exception("LCM leaf escalation tier 2 failed")

    # Tier 3: deterministic truncation fallback
    truncated = (
        source_text[:_FALLBACK_MAX_CHARS]
        if len(source_text) > _FALLBACK_MAX_CHARS
        else source_text
    )
    fallback_content = f"{truncated}\n[Truncated from {input_tokens} tokens]"
    return fallback_content, "fallback"


# ── Public API ─────────────────────────────────────────────────────────────────

async def run_leaf_pass(
    store: "SessionStore",
    session: "Session",
    llm_caller: Callable,
    fresh_tail_count: int = FRESH_TAIL_COUNT,
    chunk_tokens: int = LEAF_CHUNK_TOKENS,
    target_tokens: int = LEAF_TARGET_TOKENS,
) -> "Optional[Summary]":
    """Run a single leaf compaction pass.

    1. Get compactable messages (outside fresh tail)
    2. If len < LEAF_MIN_FANOUT: return None
    3. Get prior leaf summary for context
    4. Format messages for LLM
    5. Try 3-tier escalation
    6. INSERT summary (kind='leaf', depth=0)
    7. Link messages to summary
    8. Replace message context_items with summary context_item
    9. Return new summary
    """
    try:
        messages = get_leaf_messages(store, session.id, fresh_tail_count)
    except Exception:
        logger.exception("LCM leaf pass: failed to fetch compactable messages for session %s", session.id)
        return None

    if len(messages) < LEAF_MIN_FANOUT:
        logger.debug(
            "LCM leaf pass: session %s has %d messages (< %d), skipping",
            session.id, len(messages), LEAF_MIN_FANOUT,
        )
        return None

    # Cap chunk size by token budget
    selected: list = []
    token_count = 0
    for msg in messages:
        msg_tokens = estimate_tokens(msg.content or "")
        if selected and token_count + msg_tokens > chunk_tokens:
            break
        selected.append(msg)
        token_count += msg_tokens
        if token_count >= chunk_tokens:
            break

    if not selected:
        return None

    # Gather time range
    timestamps = [m.created_at for m in selected if m.created_at]
    earliest_at = min(timestamps) if timestamps else ""
    latest_at = max(timestamps) if timestamps else ""

    # Format source text
    source_text = format_messages_for_summary(selected)

    # Use a placeholder ID in the prompt (real ID assigned after insert)
    placeholder_id = f"leaf_{session.id}_{len(selected)}"
    format_kwargs = {
        "summary_id": placeholder_id,
        "earliest_at": earliest_at,
        "latest_at": latest_at,
    }

    content, level = await _summarize_with_escalation(
        source_text=source_text,
        llm_caller=llm_caller,
        prompt_template=LEAF_SUMMARY_PROMPT,
        format_kwargs=format_kwargs,
        target_tokens=target_tokens,
    )

    try:
        summary_token_count = estimate_tokens(content)
        new_summary = store.insert_summary(
            session_id=session.id,
            kind="leaf",
            depth=0,
            content=content,
            token_count=summary_token_count,
            earliest_at=earliest_at,
            latest_at=latest_at,
            descendant_count=0,
            descendant_token_count=0,
        )
    except Exception:
        logger.exception("LCM leaf pass: failed to insert summary for session %s", session.id)
        return None

    try:
        message_ids = [m.id for m in selected]
        store.link_summary_messages(new_summary.id, message_ids)
    except Exception:
        logger.exception("LCM leaf pass: failed to link messages to summary %s", new_summary.id)

    try:
        # Get current context items for the selected messages and replace them
        context_items = store.get_context_items(session.id)
        old_ordinals = [
            ci.ordinal for ci in context_items
            if ci.item_type == "message" and ci.item_id in {m.id for m in selected}
        ]
        if old_ordinals:
            store.replace_context_items(
                session_id=session.id,
                old_ordinals=old_ordinals,
                new_item_type="summary",
                new_item_id=new_summary.id,
                new_token_count=summary_token_count,
            )
    except Exception:
        logger.exception("LCM leaf pass: failed to replace context items for session %s", session.id)

    logger.info(
        "LCM leaf pass: session %s — compacted %d messages into summary %s (%s, level=%s)",
        session.id, len(selected), new_summary.id, f"{summary_token_count} tokens", level,
    )
    return new_summary


async def run_condensed_pass(
    store: "SessionStore",
    session: "Session",
    llm_caller: Callable,
    target_depth: int = 1,
    target_tokens: int = CONDENSED_TARGET_TOKENS,
) -> "Optional[Summary]":
    """Run a single condensed compaction pass.

    1. Get condensable summaries at target_depth-1
    2. If len < CONDENSED_MIN_FANOUT: return None
    3. Format summaries for LLM
    4. Same 3-tier escalation
    5. INSERT summary (kind='condensed', depth=target_depth)
    6. Link parent summaries
    7. Replace summary context_items with condensed summary context_item
    8. Return new summary
    """
    try:
        source_summaries = get_condensable_summaries(store, session.id, target_depth)
    except Exception:
        logger.exception(
            "LCM condensed pass: failed to fetch condensable summaries (depth=%d) for session %s",
            target_depth, session.id,
        )
        return None

    if len(source_summaries) < CONDENSED_MIN_FANOUT:
        logger.debug(
            "LCM condensed pass: session %s has %d summaries at depth %d (< %d), skipping",
            session.id, len(source_summaries), target_depth - 1, CONDENSED_MIN_FANOUT,
        )
        return None

    timestamps_earliest = [s.earliest_at for s in source_summaries if s.earliest_at]
    timestamps_latest = [s.latest_at for s in source_summaries if s.latest_at]
    earliest_at = min(timestamps_earliest) if timestamps_earliest else ""
    latest_at = max(timestamps_latest) if timestamps_latest else ""

    source_text = format_summaries_for_condensation(source_summaries)

    placeholder_id = f"condensed_{session.id}_{target_depth}_{len(source_summaries)}"
    format_kwargs = {
        "summary_id": placeholder_id,
        "depth": target_depth,
        "earliest_at": earliest_at,
        "latest_at": latest_at,
    }

    content, level = await _summarize_with_escalation(
        source_text=source_text,
        llm_caller=llm_caller,
        prompt_template=CONDENSED_SUMMARY_PROMPT,
        format_kwargs=format_kwargs,
        target_tokens=target_tokens,
    )

    # Compute descendant counts
    descendant_count = sum(
        1 + (s.descendant_count or 0) for s in source_summaries
    )
    descendant_token_count = sum(
        (s.token_count or 0) + (s.descendant_token_count or 0)
        for s in source_summaries
    )

    try:
        summary_token_count = estimate_tokens(content)
        new_summary = store.insert_summary(
            session_id=session.id,
            kind="condensed",
            depth=target_depth,
            content=content,
            token_count=summary_token_count,
            earliest_at=earliest_at,
            latest_at=latest_at,
            descendant_count=descendant_count,
            descendant_token_count=descendant_token_count,
        )
    except Exception:
        logger.exception(
            "LCM condensed pass: failed to insert summary (depth=%d) for session %s",
            target_depth, session.id,
        )
        return None

    try:
        parent_ids = [s.id for s in source_summaries]
        store.link_summary_parents(new_summary.id, parent_ids)
    except Exception:
        logger.exception(
            "LCM condensed pass: failed to link parent summaries to summary %s", new_summary.id
        )

    try:
        context_items = store.get_context_items(session.id)
        source_ids = {s.id for s in source_summaries}
        old_ordinals = [
            ci.ordinal for ci in context_items
            if ci.item_type == "summary" and ci.item_id in source_ids
        ]
        if old_ordinals:
            store.replace_context_items(
                session_id=session.id,
                old_ordinals=old_ordinals,
                new_item_type="summary",
                new_item_id=new_summary.id,
                new_token_count=summary_token_count,
            )
    except Exception:
        logger.exception(
            "LCM condensed pass: failed to replace context items for session %s", session.id
        )

    logger.info(
        "LCM condensed pass: session %s — condensed %d summaries -> summary %s (depth=%d, level=%s)",
        session.id, len(source_summaries), new_summary.id, target_depth, level,
    )
    return new_summary


async def run_incremental_compaction(
    store: "SessionStore",
    session: "Session",
    llm_caller: Callable,
    max_depth: int = INCREMENTAL_MAX_DEPTH,
) -> dict:
    """Run leaf pass, then condensation passes up to max_depth.

    Returns: {leaf_summary: Summary|None, condensed_summaries: list[Summary]}
    """
    result: dict = {"leaf_summary": None, "condensed_summaries": []}

    # Leaf pass first
    leaf_summary = await run_leaf_pass(store, session, llm_caller)
    result["leaf_summary"] = leaf_summary

    # Condensation passes from depth=1 up to max_depth
    for depth in range(1, max_depth + 1):
        condensed = await run_condensed_pass(
            store, session, llm_caller, target_depth=depth
        )
        if condensed is None:
            # No more condensation possible at this depth
            break
        result["condensed_summaries"].append(condensed)

    return result


def should_compact(
    store: "SessionStore",
    session: "Session",
    model_context_limit: int,
    context_threshold: float = 0.75,
    fresh_tail_count: int = FRESH_TAIL_COUNT,
) -> bool:
    """Return True if total session tokens exceed threshold * model_context_limit."""
    try:
        total_tokens = store.get_session_total_tokens(session.id)
    except Exception:
        logger.exception("LCM should_compact: failed to get token count for session %s", session.id)
        return False

    threshold = int(context_threshold * model_context_limit)
    over_threshold = total_tokens > threshold

    if over_threshold:
        logger.debug(
            "LCM should_compact: session %s at %d tokens (threshold=%d, limit=%d)",
            session.id, total_tokens, threshold, model_context_limit,
        )

    return over_threshold
