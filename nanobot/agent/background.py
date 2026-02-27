"""Background task runner for long-running claude-cli tasks with streaming progress."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator, Callable

from loguru import logger

if TYPE_CHECKING:
    from nanobot.bus.queue import MessageBus

# How often to post "still working" status updates
_STATUS_INTERVAL_S = 60  # 1 minute


@dataclass
class TaskRecord:
    id: str
    channel: str
    chat_id: str
    prompt_preview: str
    started_at: float
    status: str = "running"   # running | done | error | stale | cancelled
    last_activity: str = ""


class TaskRegistry:
    """Persists running task state so stale tasks can be reported after restart."""

    def __init__(self, task_dir: Path) -> None:
        self.task_dir = task_dir
        self.task_dir.mkdir(parents=True, exist_ok=True)

    def create(self, channel: str, chat_id: str, prompt: str) -> TaskRecord:
        record = TaskRecord(
            id=str(uuid.uuid4())[:8],
            channel=channel,
            chat_id=chat_id,
            prompt_preview=prompt[:100],
            started_at=time.time(),
        )
        self._save(record)
        return record

    def update_activity(self, task_id: str, activity: str) -> None:
        path = self.task_dir / f"{task_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["last_activity"] = activity
                path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass

    def finish(self, task_id: str, status: str) -> None:
        path = self.task_dir / f"{task_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["status"] = status
                path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass

    def drain_stale(self) -> list[TaskRecord]:
        """Return tasks that were running when the gateway last died; mark them stale."""
        stale = []
        for path in sorted(self.task_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == "running":
                    stale.append(TaskRecord(**data))
                    data["status"] = "stale"
                    path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass
        return stale

    def _save(self, record: TaskRecord) -> None:
        (self.task_dir / f"{record.id}.json").write_text(
            json.dumps(asdict(record)), encoding="utf-8"
        )


def _event_to_activity(event: dict) -> str | None:
    """Extract a short activity label from a claude stream-json event."""
    if event.get("type") != "assistant":
        return None
    for block in (event.get("message") or {}).get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            name = block.get("name", "tool")
            inp = block.get("input") or {}
            val = next((str(v)[:300] for v in inp.values() if v), "")
            return f'{name}("{val}")' if val else name
        if block.get("type") == "text":
            text = (block.get("text") or "").strip()
            if text:
                return text[:500] + ("…" if len(text) > 500 else "")
    return None


async def run_background_task(
    *,
    task_id: str,
    channel: str,
    chat_id: str,
    bus: "MessageBus",
    registry: TaskRegistry,
    stream_fn: Callable[[], AsyncGenerator[dict, None]],
    reply_to: str | None = None,
) -> None:
    """Run a streaming task, posting periodic status updates and final result to channel."""
    from nanobot.bus.events import OutboundMessage

    started = time.monotonic()
    last_status_at = started
    last_activity = "starting…"
    result_text: str | None = None
    is_error = False

    async def post(content: str) -> None:
        meta: dict = {}
        if reply_to:
            meta["reply_to"] = reply_to
        await bus.publish_outbound(OutboundMessage(
            channel=channel, chat_id=chat_id, content=content, metadata=meta,
        ))

    try:
        async for event in stream_fn():
            now = time.monotonic()

            activity = _event_to_activity(event)
            if activity:
                last_activity = activity
                registry.update_activity(task_id, activity)

            if event.get("type") == "result":
                result_text = event.get("result") or ""
                is_error = (
                    bool(event.get("is_error"))
                    or event.get("subtype") == "error_during_execution"
                )

            if now - last_status_at >= _STATUS_INTERVAL_S:
                elapsed_s = int(now - started)
                mins, secs = divmod(elapsed_s, 60)
                elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
                await post(f"⏳ Still working… ({elapsed_str} elapsed)\n`{last_activity}`")
                last_status_at = now

    except asyncio.CancelledError:
        registry.finish(task_id, "cancelled")
        await post("⏹ Task cancelled.")
        raise
    except Exception as e:
        logger.exception("Background task {} failed", task_id)
        registry.finish(task_id, "error")
        await post(f"❌ Task failed: {e}")
        return

    registry.finish(task_id, "error" if is_error else "done")

    if result_text:
        prefix = "❌ " if is_error else ""
        await post(f"{prefix}{result_text}")
    else:
        await post("✓ Task completed.")
