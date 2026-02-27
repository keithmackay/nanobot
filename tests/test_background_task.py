"""Tests for background task runner and streaming provider."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.background import (
    TaskRecord,
    TaskRegistry,
    _event_to_activity,
    run_background_task,
)
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus


# ---------------------------------------------------------------------------
# TaskRegistry
# ---------------------------------------------------------------------------

def test_task_registry_create_and_finish(tmp_path):
    registry = TaskRegistry(tmp_path)
    record = registry.create("discord", "chat123", "do something important")
    assert record.channel == "discord"
    assert record.chat_id == "chat123"
    assert record.status == "running"
    assert (tmp_path / f"{record.id}.json").exists()

    registry.finish(record.id, "done")
    data = json.loads((tmp_path / f"{record.id}.json").read_text())
    assert data["status"] == "done"


def test_task_registry_update_activity(tmp_path):
    registry = TaskRegistry(tmp_path)
    record = registry.create("telegram", "42", "task")
    registry.update_activity(record.id, 'bash("ls")')
    data = json.loads((tmp_path / f"{record.id}.json").read_text())
    assert data["last_activity"] == 'bash("ls")'


def test_task_registry_drain_stale(tmp_path):
    registry = TaskRegistry(tmp_path)
    r1 = registry.create("discord", "c1", "task1")
    r2 = registry.create("discord", "c2", "task2")
    registry.finish(r2.id, "done")

    stale = registry.drain_stale()
    assert len(stale) == 1
    assert stale[0].id == r1.id

    # Running r1 is now marked stale
    data = json.loads((tmp_path / f"{r1.id}.json").read_text())
    assert data["status"] == "stale"

    # Second drain returns nothing
    assert registry.drain_stale() == []


def test_task_registry_drain_stale_empty(tmp_path):
    registry = TaskRegistry(tmp_path)
    assert registry.drain_stale() == []


# ---------------------------------------------------------------------------
# _event_to_activity
# ---------------------------------------------------------------------------

def test_event_to_activity_tool_use():
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "bash", "input": {"command": "ls /tmp"}}]},
    }
    result = _event_to_activity(event)
    assert result == 'bash("ls /tmp")'


def test_event_to_activity_text():
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "I will now search for files."}]},
    }
    result = _event_to_activity(event)
    assert result == "I will now search for files."


def test_event_to_activity_truncates_long_text():
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "x" * 100}]},
    }
    result = _event_to_activity(event)
    assert result is not None
    assert len(result) <= 83  # 80 + "…"


def test_event_to_activity_non_assistant():
    assert _event_to_activity({"type": "result", "result": "done"}) is None
    assert _event_to_activity({"type": "user"}) is None


def test_event_to_activity_empty_content():
    event = {"type": "assistant", "message": {"content": []}}
    assert _event_to_activity(event) is None


# ---------------------------------------------------------------------------
# run_background_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_background_task_success(tmp_path):
    bus = MessageBus()
    registry = TaskRegistry(tmp_path)
    record = registry.create("discord", "chat1", "do a thing")

    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "bash", "input": {"command": "echo hi"}}]}},
        {"type": "result", "result": "All done!", "is_error": False},
    ]

    async def stream_fn():
        for e in events:
            yield e

    await run_background_task(
        task_id=record.id,
        channel="discord",
        chat_id="chat1",
        bus=bus,
        registry=registry,
        stream_fn=stream_fn,
    )

    # Final result should be published
    msg = bus.outbound.get_nowait()
    assert msg.content == "All done!"
    assert msg.channel == "discord"
    assert msg.chat_id == "chat1"

    data = json.loads((tmp_path / f"{record.id}.json").read_text())
    assert data["status"] == "done"


@pytest.mark.asyncio
async def test_run_background_task_error_result(tmp_path):
    bus = MessageBus()
    registry = TaskRegistry(tmp_path)
    record = registry.create("telegram", "42", "bad task")

    async def stream_fn():
        yield {"type": "result", "result": "something went wrong", "is_error": True}

    await run_background_task(
        task_id=record.id,
        channel="telegram",
        chat_id="42",
        bus=bus,
        registry=registry,
        stream_fn=stream_fn,
    )

    msg = bus.outbound.get_nowait()
    assert "something went wrong" in msg.content
    assert msg.content.startswith("❌")

    data = json.loads((tmp_path / f"{record.id}.json").read_text())
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_run_background_task_empty_result(tmp_path):
    bus = MessageBus()
    registry = TaskRegistry(tmp_path)
    record = registry.create("discord", "c1", "task")

    async def stream_fn():
        yield {"type": "system", "subtype": "init"}
        # No result event — subprocess exited cleanly with no result

    await run_background_task(
        task_id=record.id,
        channel="discord",
        chat_id="c1",
        bus=bus,
        registry=registry,
        stream_fn=stream_fn,
    )

    msg = bus.outbound.get_nowait()
    assert msg.content == "✓ Task completed."


@pytest.mark.asyncio
async def test_run_background_task_exception(tmp_path):
    bus = MessageBus()
    registry = TaskRegistry(tmp_path)
    record = registry.create("discord", "c1", "task")

    async def stream_fn():
        yield {"type": "system"}
        raise RuntimeError("subprocess exploded")

    await run_background_task(
        task_id=record.id,
        channel="discord",
        chat_id="c1",
        bus=bus,
        registry=registry,
        stream_fn=stream_fn,
    )

    msg = bus.outbound.get_nowait()
    assert "❌" in msg.content

    data = json.loads((tmp_path / f"{record.id}.json").read_text())
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_run_background_task_reply_to(tmp_path):
    """reply_to metadata is passed through when set."""
    bus = MessageBus()
    registry = TaskRegistry(tmp_path)
    record = registry.create("discord", "c1", "task")

    async def stream_fn():
        yield {"type": "result", "result": "done", "is_error": False}

    await run_background_task(
        task_id=record.id,
        channel="discord",
        chat_id="c1",
        bus=bus,
        registry=registry,
        stream_fn=stream_fn,
        reply_to="msg999",
    )

    msg = bus.outbound.get_nowait()
    assert msg.metadata.get("reply_to") == "msg999"


# ---------------------------------------------------------------------------
# ClaudeCliProvider.run_task_streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_task_streaming_parses_events():
    """Streaming subprocess output is parsed as NDJSON events."""
    from nanobot.providers.claude_cli_provider import ClaudeCliProvider

    lines = [
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n',
        b'{"type":"result","result":"done","is_error":false}\n',
    ]

    mock_proc = MagicMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(side_effect=lines + [b""])
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        provider = ClaudeCliProvider(stream_timeout=60)
        events = []
        async for event in provider.run_task_streaming("hello"):
            events.append(event)

    assert any(e.get("type") == "assistant" for e in events)
    assert any(e.get("type") == "result" for e in events)


@pytest.mark.asyncio
async def test_run_task_streaming_timeout():
    """A timeout yields an error result event."""
    from nanobot.providers.claude_cli_provider import ClaudeCliProvider

    async def slow_readline():
        await asyncio.sleep(10)
        return b""

    mock_proc = MagicMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(side_effect=slow_readline)
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        provider = ClaudeCliProvider(stream_timeout=1)
        events = []
        async for event in provider.run_task_streaming("hello"):
            events.append(event)

    assert len(events) == 1
    assert events[0].get("is_error") is True
    assert "timed out" in events[0].get("result", "").lower()


@pytest.mark.asyncio
async def test_run_task_streaming_skips_invalid_json():
    """Non-JSON lines in the stream are silently skipped."""
    from nanobot.providers.claude_cli_provider import ClaudeCliProvider

    lines = [
        b"not json\n",
        b'{"type":"result","result":"ok","is_error":false}\n',
        b"",
    ]

    mock_proc = MagicMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(side_effect=lines)
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        provider = ClaudeCliProvider(stream_timeout=60)
        events = [e async for e in provider.run_task_streaming("hello")]

    assert len(events) == 1
    assert events[0]["type"] == "result"


# ---------------------------------------------------------------------------
# AgentLoop._should_run_background
# ---------------------------------------------------------------------------

def _make_agent(tmp_path, provider=None):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    if provider is None:
        provider = MagicMock()
        provider.get_default_model.return_value = "mock"

    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
    )


def test_should_run_background_true_for_cli_provider(tmp_path):
    from nanobot.bus.events import InboundMessage
    from nanobot.providers.claude_cli_provider import ClaudeCliProvider

    provider = ClaudeCliProvider()
    agent = _make_agent(tmp_path, provider=provider)
    msg = InboundMessage(channel="discord", sender_id="u1", chat_id="c1", content="do something")
    assert agent._should_run_background(msg) is True


def test_should_run_background_false_for_cli_channel(tmp_path):
    from nanobot.bus.events import InboundMessage
    from nanobot.providers.claude_cli_provider import ClaudeCliProvider

    provider = ClaudeCliProvider()
    agent = _make_agent(tmp_path, provider=provider)
    msg = InboundMessage(channel="cli", sender_id="u1", chat_id="direct", content="hello")
    assert agent._should_run_background(msg) is False


def test_should_run_background_false_for_slash_command(tmp_path):
    from nanobot.bus.events import InboundMessage
    from nanobot.providers.claude_cli_provider import ClaudeCliProvider

    provider = ClaudeCliProvider()
    agent = _make_agent(tmp_path, provider=provider)
    msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="/new")
    assert agent._should_run_background(msg) is False


def test_should_run_background_false_for_non_cli_provider(tmp_path):
    from nanobot.bus.events import InboundMessage

    provider = MagicMock()
    provider.get_default_model.return_value = "gpt-4"
    agent = _make_agent(tmp_path, provider=provider)
    msg = InboundMessage(channel="discord", sender_id="u1", chat_id="c1", content="hello")
    assert agent._should_run_background(msg) is False
