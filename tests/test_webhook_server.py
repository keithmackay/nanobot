"""Tests for nanobot's HTTP webhook server, over a real loopback connection."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx
import pytest

from nanobot.snugban.client import SnugbanClient
from nanobot.snugban.receiver import SnugbanReceiver
from nanobot.webhook.server import WebhookServer

SECRET = "whsec_server_test_secret"


class Health:
    def get_snapshot(self) -> dict:
        return {"ok": True}


class RecordingReceiver:
    """Stands in for the Snugban receiver, recording what the server hands it."""

    def __init__(self):
        self.calls: list[tuple[dict, bytes]] = []

    async def handle(self, headers, body):
        self.calls.append((dict(headers), body))
        return 202, {"status": "accepted"}


async def _started(snugban=None) -> WebhookServer:
    server = WebhookServer(
        agent=None, health_service=Health(), host="127.0.0.1", port=0, snugban=snugban
    )
    await server.start()
    return server


def _url(server: WebhookServer, path: str) -> str:
    return f"http://127.0.0.1:{server.port}{path}"


@pytest.mark.asyncio
async def test_listens_on_the_host_and_port_given_and_reports_the_port_it_bound():
    server = await _started()
    try:
        assert server.port != 0
        async with httpx.AsyncClient() as http:
            response = await http.get(_url(server, "/health"))
        assert response.status_code == 200
        assert response.json() == {"ok": True}
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_hands_snugban_webhooks_the_raw_body_and_headers():
    receiver = RecordingReceiver()
    server = await _started(receiver)
    body = b'{"type":"status_request",  "dispatchId":"01X"}'
    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(
                _url(server, "/snugban"),
                content=body,
                headers={"X-Snugban-Signature": "sha256=ab:cd", "Content-Type": "application/json"},
            )
        assert response.status_code == 202
        assert response.json() == {"status": "accepted"}
        headers, received = receiver.calls[0]
        assert received == body
        assert headers["x-snugban-signature"] == "sha256=ab:cd"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_answers_with_the_receivers_verdict_on_a_real_webhook(tmp_path: Path):
    receiver = SnugbanReceiver(
        secret=SECRET,
        client=SnugbanClient("http://127.0.0.1:9", "sb_agent_x"),
        agent=None,
        workspace=tmp_path,
    )
    server = await _started(receiver)
    try:
        body = json.dumps({"type": "status_request", "dispatchId": "01X"}).encode()
        timestamp = str(int(time.time()))
        digest = hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
        async with httpx.AsyncClient() as http:
            unsigned = await http.post(_url(server, "/snugban"), content=body)
            signed = await http.post(
                _url(server, "/snugban"),
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Snugban-Timestamp": timestamp,
                    "X-Snugban-Signature": "sha256=" + digest.hexdigest(),
                    "X-Snugban-Delivery": "01DELIVERY",
                },
            )
        assert unsigned.status_code == 401
        assert signed.status_code == 200
    finally:
        await server.stop()
        await receiver.close()


@pytest.mark.asyncio
async def test_refuses_a_snugban_body_over_one_mebibyte_without_reading_it():
    receiver = RecordingReceiver()
    server = await _started(receiver)
    try:
        # Only the declared length is sent: the server answers before reading any body.
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.write(
            b"POST /snugban HTTP/1.1\r\nHost: x\r\nContent-Length: 1048577\r\n\r\n"
        )
        await writer.drain()
        status_line = await asyncio.wait_for(reader.readline(), timeout=5)
        writer.close()
        assert status_line.startswith(b"HTTP/1.1 413")
        assert receiver.calls == []
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_snugban_is_not_found_when_no_receiver_is_configured():
    server = await _started()
    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(_url(server, "/snugban"), content=b"{}")
        assert response.status_code == 404
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_snugban_takes_only_post():
    server = await _started(RecordingReceiver())
    try:
        async with httpx.AsyncClient() as http:
            response = await http.get(_url(server, "/snugban"))
        assert response.status_code == 405
    finally:
        await server.stop()
