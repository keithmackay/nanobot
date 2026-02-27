"""Tests for the ClaudeCliProvider."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from nanobot.providers.claude_cli_provider import (
    ClaudeCliProvider,
    _build_prompt,
    _parse_response,
)
from nanobot.providers.registry import find_by_name


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_entry_exists():
    spec = find_by_name("claude_cli")
    assert spec is not None
    assert spec.is_oauth is True
    assert spec.is_direct is True
    assert spec.is_local is True


# ---------------------------------------------------------------------------
# Model name resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model_in,expected", [
    ("claude-cli/opus-4.6",        "claude-opus-4-6"),
    ("claude-cli/haiku-4.5",       "claude-haiku-4-5-20251001"),
    ("claude-cli/sonnet-4.5",      "claude-sonnet-4-5"),
    ("claude-cli/sonnet-4.6",      "claude-sonnet-4-6"),
    ("opus-4.6",                   "claude-opus-4-6"),
    ("haiku-4.5",                  "claude-haiku-4-5-20251001"),
    # Pass-through: unknown shorthand is returned as-is
    ("claude-cli/claude-opus-4-6", "claude-opus-4-6"),
    ("some-custom-model",          "some-custom-model"),
])
def test_resolve_model(model_in, expected):
    prov = ClaudeCliProvider()
    assert prov._resolve_model(model_in) == expected


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def test_build_prompt_no_tools_simple():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user",   "content": "Hello!"},
    ]
    prompt = _build_prompt(messages, None)
    assert "You are helpful." in prompt
    assert "User: Hello!" in prompt
    assert "<tool_call>" not in prompt


def test_build_prompt_with_history():
    messages = [
        {"role": "system",    "content": "System."},
        {"role": "user",      "content": "First message"},
        {"role": "assistant", "content": "First reply"},
        {"role": "user",      "content": "Second message"},
    ]
    prompt = _build_prompt(messages, None)
    assert "User: First message" in prompt
    assert "Assistant: First reply" in prompt
    assert "User: Second message" in prompt


def test_build_prompt_with_tool_result():
    messages = [
        {"role": "user",      "content": "Search for cats"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"function": {"name": "web_search", "arguments": '{"query":"cats"}'}}]},
        {"role": "tool",      "content": "Cats are great"},
        {"role": "user",      "content": "Thanks"},
    ]
    prompt = _build_prompt(messages, None)
    assert "web_search" in prompt
    assert "Tool result: Cats are great" in prompt


def test_build_prompt_injects_tool_schema():
    messages = [{"role": "user", "content": "Do something"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "my_tool",
                "description": "Does something useful",
                "parameters": {"properties": {"arg1": {"type": "string"}}},
            },
        }
    ]
    prompt = _build_prompt(messages, tools)
    assert "my_tool" in prompt
    assert "Does something useful" in prompt
    assert "<tool_call>" in prompt


def test_build_prompt_user_list_content():
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": " world"},
        ]},
    ]
    prompt = _build_prompt(messages, None)
    assert "Hello" in prompt
    assert "world" in prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def test_parse_response_plain_text():
    resp = _parse_response("Hello there!", None)
    assert resp.content == "Hello there!"
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"


def test_parse_response_json_success():
    raw = json.dumps({"type": "result", "result": "All done.", "is_error": False})
    resp = _parse_response(raw, None)
    assert resp.content == "All done."
    assert resp.finish_reason == "stop"


def test_parse_response_json_error():
    raw = json.dumps({"type": "result", "result": "Something broke", "is_error": True})
    resp = _parse_response(raw, [])
    assert resp.finish_reason == "error"
    assert "Something broke" in resp.content


def test_parse_response_tool_call():
    raw = json.dumps({
        "type": "result",
        "is_error": False,
        "result": '<tool_call>\n{"name": "web_search", "arguments": {"query": "cats"}}\n</tool_call>',
    })
    tools = [{"function": {"name": "web_search"}}]
    resp = _parse_response(raw, tools)
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "web_search"
    assert resp.tool_calls[0].arguments == {"query": "cats"}
    assert resp.finish_reason == "tool_calls"


def test_parse_response_tool_call_with_preamble():
    raw = "Let me search for that.\n<tool_call>\n{\"name\": \"web_search\", \"arguments\": {\"query\": \"cats\"}}\n</tool_call>"
    tools = [{"function": {"name": "web_search"}}]
    resp = _parse_response(raw, tools)
    assert resp.content == "Let me search for that."
    assert resp.tool_calls[0].name == "web_search"


def test_parse_response_no_tool_call_when_no_tools():
    """Tool call block in response should be returned as plain text if no tools registered."""
    raw = '<tool_call>{"name": "x", "arguments": {}}</tool_call>'
    resp = _parse_response(raw, None)
    assert resp.tool_calls == []
    assert resp.content == raw


def test_parse_response_malformed_tool_call_falls_back_to_text():
    raw = json.dumps({
        "type": "result",
        "is_error": False,
        "result": "<tool_call>not valid json</tool_call>",
    })
    tools = [{"function": {"name": "x"}}]
    resp = _parse_response(raw, tools)
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"


# ---------------------------------------------------------------------------
# chat() — subprocess integration (mocked)
# ---------------------------------------------------------------------------

@pytest.fixture
def provider():
    return ClaudeCliProvider(default_model="claude-cli/sonnet-4.5")


def _make_completed_process(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.mark.asyncio
async def test_chat_success(provider):
    raw = json.dumps({"type": "result", "result": "Hi there!", "is_error": False})
    with patch("subprocess.run", return_value=_make_completed_process(raw)):
        resp = await provider.chat([{"role": "user", "content": "Hello"}])
    assert resp.content == "Hi there!"
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
async def test_chat_passes_model_flag(provider):
    raw = json.dumps({"type": "result", "result": "ok", "is_error": False})
    with patch("subprocess.run", return_value=_make_completed_process(raw)) as mock_run:
        await provider.chat([{"role": "user", "content": "Hi"}], model="claude-cli/haiku-4.5")
    cmd = mock_run.call_args[0][0]
    assert "--model" in cmd
    assert "claude-haiku-4-5-20251001" in cmd


@pytest.mark.asyncio
async def test_chat_cli_not_found(provider):
    with patch("subprocess.run", side_effect=FileNotFoundError):
        resp = await provider.chat([{"role": "user", "content": "Hi"}])
    assert resp.finish_reason == "error"
    assert "claude" in resp.content.lower()


@pytest.mark.asyncio
async def test_chat_timeout(provider):
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120)):
        resp = await provider.chat([{"role": "user", "content": "Hi"}])
    assert resp.finish_reason == "error"
    assert "timed out" in resp.content.lower()


@pytest.mark.asyncio
async def test_chat_nonzero_exit(provider):
    with patch("subprocess.run", return_value=_make_completed_process("", returncode=1, stderr="auth error")):
        resp = await provider.chat([{"role": "user", "content": "Hi"}])
    assert resp.finish_reason == "error"
    assert "auth error" in resp.content


@pytest.mark.asyncio
async def test_chat_with_tool_call(provider):
    raw = json.dumps({
        "type": "result",
        "is_error": False,
        "result": '<tool_call>\n{"name": "web_search", "arguments": {"query": "python"}}\n</tool_call>',
    })
    tools = [{"type": "function", "function": {"name": "web_search", "description": "Search the web"}}]
    with patch("subprocess.run", return_value=_make_completed_process(raw)):
        resp = await provider.chat([{"role": "user", "content": "Search python"}], tools=tools)
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "web_search"
    assert resp.tool_calls[0].arguments == {"query": "python"}


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------

def test_config_schema_has_claude_cli_field():
    from nanobot.config.schema import ProvidersConfig
    p = ProvidersConfig()
    assert hasattr(p, "claude_cli")


def test_make_provider_returns_claude_cli():
    from nanobot.cli.commands import _make_provider
    from nanobot.config.schema import Config
    from nanobot.providers.claude_cli_provider import ClaudeCliProvider

    config = Config()
    config.agents.defaults.model = "claude-cli/sonnet-4.5"
    result = _make_provider(config)
    assert isinstance(result, ClaudeCliProvider)
