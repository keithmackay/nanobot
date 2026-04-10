"""ChromaDB semantic memory client for nanobot."""
from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path
from typing import Any

from loguru import logger


class ChromaMemClient:
    """Semantic memory retrieval via local ChromaDB + remote Ollama embeddings.

    Gracefully degrades to no-op if:
    - chromadb is not installed
    - Ollama is unreachable (timeout / connection refused)
    - The collection doesn't exist yet
    """

    def __init__(
        self,
        ollama_url: str = "http://192.168.1.8:11434",
        chroma_data_dir: str = "~/.claude-mem/vector-db",
        project: str = "nanobot",
        top_k: int = 5,
        model: str = "nomic-embed-text",
    ) -> None:
        self.ollama_url = ollama_url.rstrip("/")
        self.chroma_data_dir = str(Path(chroma_data_dir).expanduser())
        self.project = project
        self.top_k = top_k
        self.model = model
        self._collection: Any = None
        self._chroma_unavailable: bool = False  # set True on first chromadb import failure

    def _get_collection(self) -> Any | None:
        """Lazily load ChromaDB collection. Returns None if unavailable."""
        if self._collection is not None:
            return self._collection
        if self._chroma_unavailable:
            return None
        try:
            import chromadb  # noqa: PLC0415
            client = chromadb.PersistentClient(path=self.chroma_data_dir)
            col_name = f"cm__{self.project}"
            self._collection = client.get_collection(col_name)
            logger.debug("ChromaMemClient: loaded collection {}", col_name)
            return self._collection
        except ImportError:
            self._chroma_unavailable = True
            logger.warning("chromadb not installed — semantic memory disabled")
            return None
        except Exception as e:
            logger.debug("ChromaMemClient: collection unavailable: {}", e)
            return None

    def _embed_sync(self, text: str) -> list[float] | None:
        """Embed text via Ollama. Returns None if unreachable or times out."""
        try:
            payload = json.dumps({"model": self.model, "input": [text[:2000]]}).encode()
            req = urllib.request.Request(
                f"{self.ollama_url}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return data["embeddings"][0]
        except Exception as e:
            logger.debug("ChromaMemClient: Ollama embed failed ({}): {}", self.ollama_url, e)
            return None

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        max_distance: float | None = None,
    ) -> tuple[str | None, float]:
        """Search ChromaDB for semantically similar past content.

        Args:
            query: The search query text.
            top_k: Max results to return. Defaults to self.top_k.
            max_distance: Only include results with ChromaDB distance ≤ this value.
                Distance is squared L2; ~0.3 = very similar, ~0.6 = loosely related,
                >0.8 = probably irrelevant. None = no threshold (return all top_k).

        Returns:
            (result_text, best_distance) where best_distance is the lowest distance
            seen across ALL queried results (before max_distance filtering), so the
            caller can decide whether to fall back to MemPalace. Returns float('inf')
            if the collection is empty or unavailable.
        """
        col = self._get_collection()
        if col is None:
            return (None, float("inf"))

        embedding = await asyncio.to_thread(self._embed_sync, query)
        if embedding is None:
            return (None, float("inf"))

        k = top_k if top_k is not None else self.top_k

        try:
            count = col.count()
            if count == 0:
                return (None, float("inf"))
            n = min(k, count)

            def _query() -> Any:
                return col.query(
                    query_embeddings=[embedding],
                    n_results=n,
                    include=["documents", "metadatas", "distances"],
                )

            results = await asyncio.to_thread(_query)
        except Exception as e:
            logger.debug("ChromaMemClient: query failed: {}", e)
            return (None, float("inf"))

        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        if not docs:
            return (None, float("inf"))

        # best_distance tracks the closest match seen BEFORE threshold filtering,
        # so the caller can decide whether to fall back to MemPalace.
        best_distance = min(distances) if distances else float("inf")

        _TYPE_LABELS = {
            "user_prompt": "You asked",
            "assistant_response": "I said",
            "observation": "Noted",
            "session_summary": "Session summary",
        }

        lines = []
        for doc, meta, dist in zip(docs, metas, distances):
            if max_distance is not None and dist > max_distance:
                continue
            doc_type = (meta or {}).get("doc_type", "")
            label = _TYPE_LABELS.get(doc_type, "Past context")
            snippet = doc[:300].replace("\n", " ").strip()
            lines.append(f"- [{label}] {snippet}")

        if not lines:
            return (None, best_distance)

        return ("# Relevant Past Context\n\n" + "\n".join(lines), best_distance)

    async def get_vault_context(
        self,
        seed_query: str,
        n_results: int = 15,
    ) -> str | None:
        """Fetch KeithVault notes semantically relevant to a personality's domain.

        Uses the seed_query to rank vault notes by relevance. Filters to only
        vault notes (source == keithvault) so conversation history is excluded.
        Returns a formatted block for injection into the system prompt.
        """
        col = self._get_collection()
        if col is None:
            return None

        embedding = await asyncio.to_thread(self._embed_sync, seed_query)
        if embedding is None:
            return None

        try:
            count = col.count()
            if count == 0:
                return None
            n = min(n_results, count)

            def _query() -> Any:
                return col.query(
                    query_embeddings=[embedding],
                    n_results=n,
                    include=["documents", "metadatas"],
                    where={"source": {"$eq": "keithvault"}},
                )

            results = await asyncio.to_thread(_query)
        except Exception as e:
            logger.debug("ChromaMemClient: vault context query failed: {}", e)
            return None

        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        if not docs:
            return None

        lines = []
        for doc, meta in zip(docs, metas):
            title = (meta or {}).get("title", "Note")
            context = (meta or {}).get("context", "")
            note_type = (meta or {}).get("type", "")
            # Doc text starts with "[KeithVault: title]\ncontext: ...\ntags: ...\n{body}"
            # Skip the header lines to get the actual body for the snippet
            body_lines = doc.split("\n")
            body_start = next(
                (i for i, l in enumerate(body_lines) if not l.startswith("[KeithVault") and not l.startswith("context:") and not l.startswith("tags:") and l.strip()),
                0,
            )
            snippet = " ".join(body_lines[body_start:])[:400].strip()
            lines.append(f"**{title}** ({context} / {note_type}): {snippet}")

        return "# Vault Context — Your Knowledge Base\n\n" + "\n\n".join(lines)
