"""Tests for nanobot's reports to Snugban about its dispatches."""

from __future__ import annotations

import json

import httpx
import pytest
from loguru import logger

from nanobot.snugban.client import SnugbanClient

KEY = "sb_agent_secretkey123"
DISPATCH = "01KJN2Q8Z5V7W3X4Y6A8B0C2D4"


class FakeSnugban:
    """Answers peer-API requests as Snugban would, recording each one."""

    def __init__(self, status: int = 200, state: str = "running"):
        self.status = status
        self.state = state
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status == 200:
            return httpx.Response(200, json={"dispatchId": DISPATCH, "state": self.state})
        return httpx.Response(self.status, json={"error": {"code": "X", "message": "no"}})


def _client(snugban) -> SnugbanClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(snugban))
    return SnugbanClient("http://snugban.example.ts.net:7420", KEY, http=http)


@pytest.mark.asyncio
async def test_acknowledges_with_the_agent_key_and_no_body():
    snugban = FakeSnugban()
    reply = await _client(snugban).acknowledge(DISPATCH)

    request = snugban.requests[0]
    assert request.method == "POST"
    assert str(request.url) == (
        f"http://snugban.example.ts.net:7420/api/peer/dispatches/{DISPATCH}/ack"
    )
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "content-type" not in request.headers
    assert request.content == b""
    assert reply.delivered and not reply.ended and reply.state == "running"


@pytest.mark.asyncio
async def test_a_base_url_with_a_trailing_slash_or_path_still_reaches_the_peer_api():
    snugban = FakeSnugban()
    http = httpx.AsyncClient(transport=httpx.MockTransport(snugban))
    await SnugbanClient("http://snugban:7420/", KEY, http=http).heartbeat(DISPATCH)
    assert str(snugban.requests[0].url) == (
        f"http://snugban:7420/api/peer/dispatches/{DISPATCH}/heartbeat"
    )


@pytest.mark.asyncio
async def test_sends_a_heartbeat():
    snugban = FakeSnugban(state="waiting_input")
    reply = await _client(snugban).heartbeat(DISPATCH)
    assert snugban.requests[0].url.path == f"/api/peer/dispatches/{DISPATCH}/heartbeat"
    assert reply.state == "waiting_input"


@pytest.mark.asyncio
async def test_reports_progress_as_json():
    snugban = FakeSnugban()
    await _client(snugban).progress(DISPATCH, "Tests written", percent=40)
    request = snugban.requests[0]
    assert request.url.path == f"/api/peer/dispatches/{DISPATCH}/progress"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"message": "Tests written", "percent": 40}


@pytest.mark.asyncio
async def test_reports_progress_without_a_percent():
    snugban = FakeSnugban()
    await _client(snugban).progress(DISPATCH, "Started")
    assert json.loads(snugban.requests[0].content) == {"message": "Started"}


@pytest.mark.asyncio
async def test_fails_a_dispatch_with_a_reason():
    snugban = FakeSnugban()
    await _client(snugban).fail(DISPATCH, "No GitHub repository")
    request = snugban.requests[0]
    assert request.url.path == f"/api/peer/dispatches/{DISPATCH}/fail"
    assert json.loads(request.content) == {"reason": "No GitHub repository"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 409])
async def test_a_dispatch_that_ended_or_is_not_ours_is_reported_as_ended(status):
    reply = await _client(FakeSnugban(status=status)).heartbeat(DISPATCH)
    assert reply.ended and not reply.delivered


@pytest.mark.asyncio
async def test_another_error_is_neither_delivered_nor_ended():
    reply = await _client(FakeSnugban(status=500)).heartbeat(DISPATCH)
    assert not reply.delivered and not reply.ended


@pytest.mark.asyncio
async def test_an_unreachable_snugban_is_neither_delivered_nor_ended_and_the_key_is_not_logged():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect (Authorization: Bearer {KEY})")

    logged: list[str] = []
    sink = logger.add(lambda message: logged.append(str(message)), level="DEBUG")
    try:
        reply = await _client(refuse).acknowledge(DISPATCH)
    finally:
        logger.remove(sink)

    assert not reply.delivered and not reply.ended
    assert logged, "the failure should be logged"
    assert not any(KEY in line for line in logged)
