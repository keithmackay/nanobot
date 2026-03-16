"""DAG traversal utilities for LCM-style session compaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.session.store import SessionStore, Message, Summary


def get_leaf_messages(
    store: "SessionStore",
    session_id: int,
    fresh_tail_count: int,
) -> "list[Message]":
    """Get oldest messages outside the fresh tail, eligible for leaf compaction.

    These are messages that are not in the protected fresh tail window and
    have not yet been compacted into a summary.
    """
    return store.get_compactable_messages(session_id, fresh_tail_count)


def get_condensable_summaries(
    store: "SessionStore",
    session_id: int,
    target_depth: int,
) -> "list[Summary]":
    """Get contiguous summaries at target_depth eligible for condensation.

    Returns summaries at depth=(target_depth-1) that can be rolled up into
    a single summary at target_depth.
    """
    source_depth = target_depth - 1
    return store.get_compactable_summaries(session_id, source_depth)


def get_summary_lineage(store: "SessionStore", summary_id: int) -> dict:
    """Return full lineage: {summary, children: [...], parent_summaries: [...]}

    Children are the messages or summaries this summary was built from.
    Parent summaries are higher-level summaries that include this one.
    """
    summary = store.get_summary(summary_id)
    children = store.get_summary_messages(summary_id)
    parent_summaries = store.get_summary_children(summary_id)
    return {
        "summary": summary,
        "children": children,
        "parent_summaries": parent_summaries,
    }


def format_messages_for_summary(messages: "list[Message]") -> str:
    """Concatenate messages with ISO timestamps for LLM input.

    Each message is prefixed with its creation timestamp and role.
    """
    lines = []
    for msg in messages:
        ts = (msg.created_at or "")[:16]  # YYYY-MM-DDTHH:MM
        role = msg.role.upper()
        content = (msg.content or "").strip()
        if content:
            lines.append(f"[{ts}] {role}: {content}")
    return "\n\n".join(lines)


def format_summaries_for_condensation(summaries: "list[Summary]") -> str:
    """Concatenate child summaries with time range headers for LLM input.

    Each summary is wrapped with a time-range header showing earliest_at
    through latest_at.
    """
    sections = []
    for s in summaries:
        earliest = (s.earliest_at or s.created_at or "")[:16]
        latest = (s.latest_at or s.created_at or "")[:16]
        header = f"[{earliest} - {latest}]"
        content = (s.content or "").strip()
        if content:
            sections.append(f"{header}\n{content}")
    return "\n\n".join(sections)


def estimate_tokens(text: str) -> int:
    """Rough token estimation: len(text.split()) * 1.3"""
    return int(len(text.split()) * 1.3)
