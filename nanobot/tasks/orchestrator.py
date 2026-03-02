"""Task orchestrator: polls SQLite for pending tasks and runs them via Claude Code."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.tasks.db import TaskDB, TaskRecord
from nanobot.tasks.detector import TaskIntent, detect

if TYPE_CHECKING:
    from nanobot.bus.events import InboundMessage
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.claude_cli_provider import ClaudeCliProvider


class TaskOrchestrator:
    """
    Asyncio service that:
    1. Detects task-prefixed inbound messages and queues them in SQLite
    2. Polls for pending tasks and dispatches them to Claude Code
    3. Reports progress back to the originating channel
    """

    # How often the poll loop checks for new pending tasks (internal tick)
    _POLL_TICK_S = 5

    def __init__(
        self,
        db: TaskDB,
        bus: "MessageBus",
        provider: "ClaudeCliProvider",
        *,
        default_model: str,
        default_poll_interval_s: int = 60,
    ) -> None:
        self._db = db
        self._bus = bus
        self._provider = provider
        self._default_model = default_model
        self._default_poll_s = default_poll_interval_s
        self._running = False
        self._active: dict[str, asyncio.Task] = {}  # task_id -> asyncio.Task

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_task_message(self, content: str) -> bool:
        """Return True if the message should be queued as a long-running task."""
        return detect(content) is not None

    async def queue_task(self, msg: "InboundMessage") -> None:
        """Extract task intent, persist to SQLite, and ACK the sender."""
        from nanobot.bus.events import OutboundMessage

        intent = detect(msg.content)
        if intent is None:
            return

        message_id = (msg.metadata or {}).get("message_id")
        rec = self._db.create(
            msg.channel,
            msg.chat_id,
            intent.prompt,
            model=intent.model,
            poll_interval_s=intent.poll_interval_s,
            message_id=message_id,
        )

        poll_s = self._resolve_poll(rec)
        if poll_s == 0:
            ack = f"📋 Task `{rec.id}` queued. I'll reply when it's done."
        else:
            ack = f"📋 Task `{rec.id}` queued. I'll check in every {poll_s}s."

        logger.info("Task {} queued: {!r}", rec.id, intent.prompt[:80])
        await self._bus.publish_outbound(OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=ack,
            metadata={"reply_to": message_id} if message_id else {},
        ))

    async def start(self) -> None:
        """Start the background orchestrator loop."""
        # Mark any orphaned tasks from prior process as stale
        stale = self._db.drain_stale()
        if stale:
            logger.warning("Found {} stale task(s) from previous process", len(stale))
            await self._notify_stale(stale)

        self._running = True
        logger.info("Task orchestrator started (poll={}s)", self._POLL_TICK_S)
        asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Signal the loop to stop and cancel in-flight tasks."""
        self._running = False
        for tid, t in list(self._active.items()):
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._active.clear()

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                pending = self._db.pending()
                for rec in pending:
                    if rec.id not in self._active:
                        t = asyncio.create_task(self._run_task(rec))
                        self._active[rec.id] = t
                        t.add_done_callback(lambda x, tid=rec.id: self._active.pop(tid, None))
            except Exception:
                logger.exception("Error in task orchestrator poll loop")
            await asyncio.sleep(self._POLL_TICK_S)

    async def _run_task(self, rec: TaskRecord) -> None:
        """Execute one task via Claude Code with periodic status updates."""
        from nanobot.agent.background import _event_to_activity
        from nanobot.bus.events import OutboundMessage

        self._db.mark_running(rec.id)
        model = rec.model or self._default_model
        poll_s = self._resolve_poll(rec)

        started = time.monotonic()
        last_status_at = started
        last_activity = "starting…"
        result_text: str | None = None
        is_error = False

        reply_to = rec.message_id

        async def post(content: str) -> None:
            meta: dict = {}
            if reply_to:
                meta["reply_to"] = reply_to
            await self._bus.publish_outbound(OutboundMessage(
                channel=rec.channel,
                chat_id=rec.chat_id,
                content=content,
                metadata=meta,
            ))

        logger.info("Task {} starting (model={}, poll={}s)", rec.id, model, poll_s)

        try:
            async for event in self._provider.run_task_streaming(rec.prompt, model):
                now = time.monotonic()

                activity = _event_to_activity(event)
                if activity:
                    last_activity = activity
                    self._db.update_activity(rec.id, activity)

                if event.get("type") == "result":
                    result_text = event.get("result") or ""
                    is_error = (
                        bool(event.get("is_error"))
                        or event.get("subtype") == "error_during_execution"
                    )

                if poll_s > 0 and now - last_status_at >= poll_s:
                    elapsed_s = int(now - started)
                    mins, secs = divmod(elapsed_s, 60)
                    elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
                    await post(
                        f"⏳ Task `{rec.id}` still working… ({elapsed_str} elapsed)\n"
                        f"`{last_activity}`"
                    )
                    last_status_at = now

        except asyncio.CancelledError:
            self._db.finish(rec.id, status="cancelled")
            await post(f"⏹ Task `{rec.id}` cancelled.")
            raise
        except Exception as exc:
            logger.exception("Task {} failed", rec.id)
            self._db.finish(rec.id, status="failed", error=str(exc))
            await post(f"❌ Task `{rec.id}` failed: {exc}")
            return

        final_status = "failed" if is_error else "done"
        self._db.finish(rec.id, status=final_status, result=result_text)

        if result_text:
            prefix = "❌ " if is_error else f"✅ Task `{rec.id}` complete:\n\n"
            await post(f"{prefix}{result_text}")
        else:
            await post(f"✅ Task `{rec.id}` complete.")

    def _resolve_poll(self, rec: TaskRecord) -> int:
        """Return the effective poll interval for this task (0 = ACK+final only)."""
        if rec.poll_interval_s is not None:
            return max(0, rec.poll_interval_s)
        return max(0, self._default_poll_s)

    # ------------------------------------------------------------------
    # Stale notification
    # ------------------------------------------------------------------

    async def _notify_stale(self, stale: list[TaskRecord]) -> None:
        from nanobot.bus.events import OutboundMessage
        for rec in stale:
            await self._bus.publish_outbound(OutboundMessage(
                channel=rec.channel,
                chat_id=rec.chat_id,
                content=(
                    f"⚠️ Task `{rec.id}` was interrupted by a bot restart "
                    f"and did not complete. Re-submit if needed."
                ),
            ))
