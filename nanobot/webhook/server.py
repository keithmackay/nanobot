"""Lightweight async HTTP webhook server for nanobot gateway.

Endpoints
---------
GET  /health   → health snapshot JSON (same data as health.json)
POST /message  → inject a message into the agent, returns response JSON
POST /snugban  → Snugban's signed webhooks (only when the Snugban receiver is
                 configured; see nanobot/snugban/receiver.py)

POST /message body (JSON):
  {
    "message":     "do something",          # required
    "channel":     "webhook",               # optional, default "webhook"
    "chat_id":     "n8n",                   # optional, default "webhook"
    "session":     "n8n:my-workflow",       # optional, default "webhook:<channel>"
    "personality": "archie"                 # optional, routes to a named personality
  }

Response:
  {"response": "...", "session": "n8n:my-workflow"}
"""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.health.service import HealthService
    from nanobot.snugban.receiver import SnugbanReceiver

SNUGBAN_PATH = "/snugban"
# Snugban's own limit on a request body.
SNUGBAN_MAX_BODY = 1024 * 1024


def _json_response(status: str, data: dict) -> bytes:
    body = json.dumps(data, ensure_ascii=False).encode()
    header = (
        f"HTTP/1.1 {status}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    return header.encode() + body


class WebhookServer:
    """Minimal async HTTP server exposing a webhook API for nanobot."""

    def __init__(
        self,
        agent: "AgentLoop",
        health_service: "HealthService",
        host: str = "0.0.0.0",
        port: int = 18790,
        snugban: "SnugbanReceiver | None" = None,
    ):
        self.agent = agent
        self.health_service = health_service
        self.host = host
        self.port = port
        self.snugban = snugban
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        # The port actually bound (differs from the one asked for when that was 0).
        self.port = self._server.sockets[0].getsockname()[1]
        logger.info("WebhookServer: listening on {}:{}", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("WebhookServer: stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not request_line:
                return

            parts = request_line.decode(errors="replace").strip().split()
            if len(parts) < 2:
                writer.write(_json_response("400 Bad Request", {"error": "bad request line"}))
                await writer.drain()
                return

            method, path = parts[0].upper(), parts[1].split("?")[0]

            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10.0)
                stripped = line.decode(errors="replace").strip()
                if not stripped:
                    break
                if ":" in stripped:
                    k, _, v = stripped.partition(":")
                    headers[k.strip().lower()] = v.strip()

            body = b""
            content_length = int(headers.get("content-length", "0"))
            if path == SNUGBAN_PATH and content_length > SNUGBAN_MAX_BODY:
                writer.write(_json_response("413 Payload Too Large", {"error": "body too large"}))
                await writer.drain()
                return
            if content_length > 0:
                body = await asyncio.wait_for(
                    reader.readexactly(content_length), timeout=30.0
                )

            response = await self._route(method, path, headers, body)
            writer.write(response)
            await writer.drain()

        except asyncio.TimeoutError:
            logger.warning("WebhookServer: client timed out")
        except Exception as exc:
            logger.error("WebhookServer: error handling client: {}", exc)
            try:
                writer.write(_json_response("500 Internal Server Error", {"error": str(exc)}))
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _route(
        self, method: str, path: str, headers: dict[str, str], body: bytes
    ) -> bytes:
        if path == "/health":
            if method != "GET":
                return _json_response("405 Method Not Allowed", {"error": "use GET"})
            return _json_response("200 OK", self.health_service.get_snapshot())

        if path == "/message":
            if method != "POST":
                return _json_response("405 Method Not Allowed", {"error": "use POST"})
            return await self._handle_message(body)

        if path == SNUGBAN_PATH and self.snugban is not None:
            if method != "POST":
                return _json_response("405 Method Not Allowed", {"error": "use POST"})
            status, data = await self.snugban.handle(headers, body)
            return _json_response(f"{status} {HTTPStatus(status).phrase}", data)

        return _json_response("404 Not Found", {"error": f"unknown path: {path}"})

    async def _handle_message(self, body: bytes) -> bytes:
        try:
            data = json.loads(body.decode())
        except Exception:
            return _json_response("400 Bad Request", {"error": "invalid JSON body"})

        message = data.get("message", "").strip()
        if not message:
            return _json_response("400 Bad Request", {"error": "missing 'message' field"})

        channel = str(data.get("channel", "webhook"))
        chat_id = str(data.get("chat_id", "webhook"))
        session = str(data.get("session", f"webhook:{channel}"))
        personality = data.get("personality")

        metadata: dict = {}
        if personality:
            metadata["personality"] = personality

        logger.info(
            "WebhookServer: /message channel={} session={} personality={}",
            channel, session, personality or "(none)",
        )

        try:
            response = await self.agent.process_direct(
                content=message,
                session_key=session,
                channel=channel,
                chat_id=chat_id,
                metadata=metadata or None,
            )
            return _json_response("200 OK", {"response": response, "session": session})
        except Exception as exc:
            logger.error("WebhookServer: agent error: {}", exc)
            return _json_response("500 Internal Server Error", {"error": str(exc)})
