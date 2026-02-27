"""Claude CLI provider — uses the local `claude` binary (subscription-based)."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from typing import Any

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest

# Map shorthand model names (OpenClaw-style) to claude CLI model IDs
_MODEL_ALIASES: dict[str, str] = {
    "haiku-4.5": "claude-haiku-4-5-20251001",
    "haiku-4-5": "claude-haiku-4-5-20251001",
    "sonnet-4.5": "claude-sonnet-4-5",
    "sonnet-4-5": "claude-sonnet-4-5",
    "opus-4.5": "claude-opus-4-5",
    "opus-4-5": "claude-opus-4-5",
    "opus-4.6": "claude-opus-4-6",
    "opus-4-6": "claude-opus-4-6",
    "sonnet-4.6": "claude-sonnet-4-6",
    "sonnet-4-6": "claude-sonnet-4-6",
}

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

_TOOL_INJECTION = (
    "When you need to call a tool, output ONLY the following block and nothing else:\n"
    "<tool_call>\n"
    '{"name": "tool_name", "arguments": {"arg1": "value1"}}\n'
    "</tool_call>\n"
    "After calling a tool you will receive a Tool result: line — then continue from there.\n"
    "When you have a final answer (not calling a tool), output plain text without any <tool_call> block."
)


class ClaudeCliProvider(LLMProvider):
    """Provider that calls the `claude` CLI binary using the user's Claude subscription."""

    def __init__(self, default_model: str = "claude-cli/claude-sonnet-4-5", claude_bin: str = "claude"):
        super().__init__(api_key=None, api_base=None)
        self.default_model = default_model
        self.claude_bin = claude_bin

    def _resolve_model(self, model: str) -> str:
        """Strip claude-cli/ prefix and map shorthand names to full model IDs."""
        if "/" in model:
            model = model.split("/", 1)[1]
        return _MODEL_ALIASES.get(model, model)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        model_id = self._resolve_model(model or self.default_model)
        prompt = _build_prompt(messages, tools)

        try:
            raw = await asyncio.to_thread(self._run, prompt, model_id)
        except FileNotFoundError:
            return LLMResponse(
                content=(
                    "Error: `claude` CLI not found. "
                    "Install it from https://claude.ai/download or via npm: npm install -g @anthropic-ai/claude-code"
                ),
                finish_reason="error",
            )
        except subprocess.TimeoutExpired:
            return LLMResponse(content="Error: claude CLI timed out (120s).", finish_reason="error")
        except Exception as e:
            return LLMResponse(content=f"Error calling claude CLI: {e}", finish_reason="error")

        return _parse_response(raw, tools)

    def _run(self, prompt: str, model_id: str) -> str:
        cmd = [self.claude_bin, "--print", prompt, "--output-format", "json"]
        if model_id:
            cmd += ["--model", model_id]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            err = result.stderr.strip() or f"claude exited with code {result.returncode}"
            raise RuntimeError(err)
        return result.stdout

    def get_default_model(self) -> str:
        return self.default_model


def _build_prompt(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> str:
    """Serialize messages + optional tool schema into a single prompt string."""
    parts: list[str] = []

    # System message
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content") or ""
            if content:
                parts.append(content)
            break

    # Tool injection
    if tools:
        tool_lines = [_TOOL_INJECTION, "\nAvailable tools:"]
        for tool in tools:
            fn = tool.get("function") or tool
            name = fn.get("name", "")
            desc = fn.get("description", "")
            params = fn.get("parameters") or {}
            tool_lines.append(f"\n- **{name}**: {desc}")
            props = params.get("properties")
            if props:
                tool_lines.append(f"  Parameters: {json.dumps(props, ensure_ascii=False)}")
        parts.append("".join(tool_lines))

    # Conversation history (non-system messages)
    history: list[str] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue
        content = msg.get("content")
        if role == "user":
            if isinstance(content, list):
                text = " ".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                )
            else:
                text = content or ""
            history.append(f"User: {text}")
        elif role == "assistant":
            if isinstance(content, str) and content:
                history.append(f"Assistant: {content}")
            for tc in msg.get("tool_calls", []) or []:
                fn = tc.get("function") or {}
                history.append(
                    f"Assistant called tool: {fn.get('name')}({fn.get('arguments', '{}')})"
                )
        elif role == "tool":
            tool_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            history.append(f"Tool result: {tool_text}")

    if history:
        parts.append("\n".join(history))

    return "\n\n".join(parts)


def _parse_response(raw: str, tools: list[dict[str, Any]] | None) -> LLMResponse:
    """Parse claude CLI JSON output into LLMResponse."""
    result_text = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            if data.get("is_error"):
                return LLMResponse(
                    content=f"Error: {data.get('result', 'unknown error')}",
                    finish_reason="error",
                )
            result_text = data.get("result", "") or ""
    except (json.JSONDecodeError, ValueError):
        pass  # treat raw as plain text

    if not tools:
        return LLMResponse(content=result_text, finish_reason="stop")

    # Try to extract a tool call
    match = _TOOL_CALL_RE.search(result_text)
    if match:
        try:
            call_data = json.loads(match.group(1))
            name = call_data.get("name", "")
            arguments = call_data.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {"raw": arguments}
            tool_call = ToolCallRequest(id="claudecli_0", name=name, arguments=arguments)
            content = result_text[: match.start()].strip() or None
            return LLMResponse(content=content, tool_calls=[tool_call], finish_reason="tool_calls")
        except Exception:
            pass

    return LLMResponse(content=result_text, finish_reason="stop")
