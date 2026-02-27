"""claude-mem client for persistent conversation history and memory retrieval."""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from typing import Any

from loguru import logger


class ClaudeMemClient:
    """Async HTTP client for the claude-mem worker service (port 37777 by default)."""

    def __init__(self, url: str = "http://127.0.0.1:37777", project: str = "nanobot"):
        self.url = url.rstrip("/")
        self.project = project
        self._available: bool | None = None  # None = not yet probed

    async def _get(self, path: str, **params: str) -> dict[str, Any] | None:
        if params:
            path += "?" + urllib.parse.urlencode(params)
        full = f"{self.url}{path}"
        try:
            def _do() -> dict[str, Any]:
                r = urllib.request.urlopen(full, timeout=3)
                return json.loads(r.read())
            return await asyncio.to_thread(_do)
        except Exception as e:
            logger.debug("claude-mem GET {} failed: {}", path, e)
            return None

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        full = f"{self.url}{path}"
        try:
            def _do() -> dict[str, Any]:
                data = json.dumps(body).encode()
                req = urllib.request.Request(
                    full, data=data, headers={"Content-Type": "application/json"}
                )
                r = urllib.request.urlopen(req, timeout=3)
                return json.loads(r.read())
            return await asyncio.to_thread(_do)
        except Exception as e:
            logger.debug("claude-mem POST {} failed: {}", path, e)
            return None

    async def is_available(self) -> bool:
        """Probe the claude-mem health endpoint."""
        result = await self._get("/api/health")
        available = isinstance(result, dict) and result.get("status") == "ok"
        if self._available is None:
            if available:
                logger.info("claude-mem connected at {}", self.url)
            else:
                logger.warning("claude-mem not reachable at {} — history integration disabled", self.url)
        self._available = available
        return available

    async def log_turn(self, session_id: str, prompt: str) -> None:
        """Register a new conversation turn with claude-mem (fire-and-forget)."""
        await self._post("/api/sessions/init", {
            "claudeSessionId": session_id,
            "project": self.project,
            "prompt": prompt,
        })

    async def get_context(self) -> str | None:
        """Return recent session context markdown for this project, or None."""
        result = await self._get("/api/context/recent", project=self.project)
        if not result:
            return None
        content = result.get("content", [])
        if isinstance(content, list) and content:
            text = content[0].get("text", "")
            if text and "No previous sessions" not in text:
                return text
        return None

    async def search(self, query: str) -> str | None:
        """Search stored memories and observations, return text or None."""
        result = await self._get("/api/search", query=query)
        if not result:
            return None
        content = result.get("content", [])
        if isinstance(content, list) and content:
            text = content[0].get("text", "")
            if text and "No results found" not in text:
                return text
        return None
