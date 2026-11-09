"""Reports to Snugban about nanobot's dispatches, over Snugban's peer REST API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from loguru import logger


@dataclass(frozen=True)
class DispatchReply:
    """What Snugban said to a report on a dispatch."""

    delivered: bool  # Snugban took the report (2xx)
    ended: bool  # the dispatch is over, or is not ours (404 or 409)
    state: str | None = None  # the dispatch's session state, when delivered


class SnugbanClient:
    """Sends nanobot's reports on a dispatch to Snugban, authenticated with the agent key."""

    def __init__(self, base_url: str, agent_key: str, *, http: httpx.AsyncClient | None = None):
        parts = urlsplit(base_url)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._headers = {"Authorization": f"Bearer {agent_key}"}
        self._http = http or httpx.AsyncClient(timeout=10.0)

    async def acknowledge(self, dispatch_id: str) -> DispatchReply:
        return await self._post(dispatch_id, "ack")

    async def heartbeat(self, dispatch_id: str) -> DispatchReply:
        return await self._post(dispatch_id, "heartbeat")

    async def progress(
        self, dispatch_id: str, message: str, percent: float | None = None
    ) -> DispatchReply:
        body: dict[str, Any] = {"message": message}
        if percent is not None:
            body["percent"] = percent
        return await self._post(dispatch_id, "progress", body)

    async def fail(self, dispatch_id: str, reason: str) -> DispatchReply:
        return await self._post(dispatch_id, "fail", {"reason": reason})

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(
        self, dispatch_id: str, report: str, body: dict[str, Any] | None = None
    ) -> DispatchReply:
        url = f"{self._origin}/api/peer/dispatches/{dispatch_id}/{report}"
        try:
            # No body is sent with no Content-Type: Snugban refuses an empty JSON body.
            if body is None:
                response = await self._http.post(url, headers=self._headers)
            else:
                response = await self._http.post(url, headers=self._headers, json=body)
        except httpx.HTTPError as exc:
            logger.warning(
                "Snugban: {} for dispatch {} not sent: {}", report, dispatch_id, type(exc).__name__
            )
            return DispatchReply(delivered=False, ended=False)
        if response.status_code in (404, 409):
            logger.info(
                "Snugban: {} for dispatch {} refused ({}): the dispatch has ended",
                report, dispatch_id, response.status_code,
            )
            return DispatchReply(delivered=False, ended=True)
        if not response.is_success:
            logger.warning(
                "Snugban: {} for dispatch {} answered {}", report, dispatch_id, response.status_code
            )
            return DispatchReply(delivered=False, ended=False)
        state = None
        try:
            state = response.json().get("state")
        except ValueError:
            pass
        return DispatchReply(delivered=True, ended=False, state=state)
