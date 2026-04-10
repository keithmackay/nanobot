"""MemPalace fallback memory client for nanobot.

Used as a low-confidence fallback when ChromaDB semantic search returns
results above the FALLBACK_THRESHOLD distance (i.e. nothing close enough).
Also provides KG entity lookup for explicit recall queries.

Search uses the mempalace CLI subprocess — stable interface, output is
ready-made prose that injects cleanly into the system prompt.
KG queries use the Python API directly (no CLI equivalent).

Both degrade gracefully if MemPalace is not installed or the palace is empty.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger


# Distance threshold: if ChromaDB's best result is above this, fall back to MemPalace.
FALLBACK_THRESHOLD = 0.45

# Proper nouns that look capitalized but aren't entity names worth querying.
_SKIP_WORDS = frozenset([
    "The", "This", "That", "What", "When", "How", "Why", "Where", "Which",
    "Can", "Should", "Would", "Could", "Does", "Did", "Has", "Have", "Was",
    "Are", "Is", "Do", "Just", "Also", "From", "With", "About", "Your",
    "There", "Here", "Then", "Than", "Some", "Any", "All", "Each",
])


class MemPalaceClient:
    """Wrapper around the MemPalace CLI and Python API.

    Instantiate once; it lazily checks availability on first use.
    """

    def __init__(
        self,
        palace_path: str = "~/.mempalace/palace",
        kg_db_path: str = "~/.mempalace/knowledge_graph.sqlite3",
        search_top_k: int = 3,
    ) -> None:
        self.palace_path = str(Path(palace_path).expanduser())
        self.kg_db_path = str(Path(kg_db_path).expanduser())
        self.search_top_k = search_top_k
        self._available: bool | None = None  # None = unchecked

    @property
    def available(self) -> bool:
        """True if the mempalace CLI is on PATH."""
        if self._available is None:
            self._available = shutil.which("mempalace") is not None
            if not self._available:
                logger.debug("MemPalaceClient: mempalace not in PATH — disabled")
        return self._available

    # ── Search ────────────────────────────────────────────────────────────────

    async def search(self, query: str, top_k: int | None = None) -> str | None:
        """Run mempalace search and return formatted results, or None if unavailable.

        Called as a fallback when ChromaDB best_distance > FALLBACK_THRESHOLD.
        The CLI output is human-readable prose — passed through as-is.
        """
        if not self.available:
            return None

        k = top_k if top_k is not None else self.search_top_k

        def _run() -> str | None:
            try:
                result = subprocess.run(
                    ["mempalace", "search", query, "--results", str(k)],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    out = result.stdout.strip()
                    if out and "no results" not in out.lower():
                        return out
            except Exception as e:
                logger.debug("MemPalaceClient.search subprocess failed: {}", e)
            return None

        output = await asyncio.to_thread(_run)
        if output:
            return f"# MemPalace — Structured Memory Search\n\n{output}"
        return None

    # ── Knowledge Graph ───────────────────────────────────────────────────────

    async def kg_query(self, entity: str) -> str | None:
        """Query the temporal knowledge graph for facts about an entity.

        Returns a formatted block, or None if unavailable or entity unknown.
        Uses the Python API directly (no CLI equivalent for KG queries).
        """
        def _run() -> list[Any] | None:
            try:
                from mempalace.knowledge_graph import KnowledgeGraph  # noqa: PLC0415
                kg = KnowledgeGraph(db_path=self.kg_db_path)
                return kg.query_entity(entity)
            except ImportError:
                logger.debug("MemPalaceClient.kg_query: mempalace Python package not importable")
            except Exception as e:
                logger.debug("MemPalaceClient.kg_query({}) failed: {}", entity, e)
            return None

        facts = await asyncio.to_thread(_run)
        if not facts:
            return None

        lines = []
        for fact in facts[:10]:  # cap at 10 to avoid token bloat
            if isinstance(fact, dict):
                subj = fact.get("subject", "")
                pred = fact.get("predicate", "")
                obj = fact.get("object", "")
                valid_from = fact.get("valid_from", "")
                valid_to = fact.get("valid_to", "")
                temporal = f" (since {valid_from})" if valid_from and not valid_to else \
                           f" ({valid_from} → {valid_to})" if valid_from and valid_to else ""
                lines.append(f"- {subj} {pred} {obj}{temporal}")
            else:
                lines.append(f"- {fact}")

        if not lines:
            return None
        return f"# Knowledge Graph: {entity}\n\n" + "\n".join(lines)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def extract_entities(text: str) -> list[str]:
        """Extract candidate entity names for KG lookup.

        Heuristic: capitalized words that are not sentence-starters or
        common English stop words. Caps-only acronyms (EY, AI) are included.
        Returns up to 3 unique candidates.
        """
        # Match capitalized words or all-caps acronyms (2+ chars)
        candidates = re.findall(r'\b([A-Z][a-z]{2,}|[A-Z]{2,})\b', text)
        seen: set[str] = set()
        result: list[str] = []
        for word in candidates:
            if word in _SKIP_WORDS or word in seen:
                continue
            seen.add(word)
            result.append(word)
            if len(result) >= 3:
                break
        return result
