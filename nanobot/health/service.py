"""Health service — tracks nanobot responsiveness and writes periodic snapshots."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger


def _now_ms() -> int:
    return int(time.time() * 1000)


def _age_s(ts_ms: int | None) -> float | None:
    if ts_ms is None:
        return None
    return (time.time() * 1000 - ts_ms) / 1000


class HealthService:
    """
    Tracks the health of a running nanobot gateway.

    Records timestamps for key events (agent turns, heartbeat ticks, cron runs,
    channel messages) and writes a ``health.json`` snapshot to the workspace.

    The snapshot is written:
    - At startup (``mark_started``)
    - After each ``mark_*`` call
    - Every ``snapshot_interval_s`` seconds by the background watchdog

    A ``stale_threshold_s`` controls how long without an agent turn before a
    WARNING is logged (default: 3600 s / 1 hour).
    """

    def __init__(
        self,
        workspace: Path,
        stale_threshold_s: int = 3600,
        snapshot_interval_s: int = 60,
    ):
        self.workspace = workspace
        self.stale_threshold_s = stale_threshold_s
        self.snapshot_interval_s = snapshot_interval_s

        self._started_at_ms: int | None = None
        self._last_agent_turn_ms: int | None = None
        self._last_heartbeat_ms: int | None = None
        self._last_cron_ms: int | None = None
        self._last_cron_job: str | None = None
        self._channel_last_msg: dict[str, int] = {}
        self._channels_enabled: list[str] = []
        self._heartbeat_interval_s: int | None = None
        self._cron_job_count: int = 0

        self._running = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Event markers (called by gateway, agent, heartbeat, cron)
    # ------------------------------------------------------------------

    def mark_started(
        self,
        channels: list[str],
        heartbeat_interval_s: int | None = None,
        cron_job_count: int = 0,
    ) -> None:
        self._started_at_ms = _now_ms()
        self._channels_enabled = list(channels)
        self._heartbeat_interval_s = heartbeat_interval_s
        self._cron_job_count = cron_job_count
        self._write_snapshot()

    def mark_agent_turn(self, channel: str = "", chat_id: str = "") -> None:
        self._last_agent_turn_ms = _now_ms()
        if channel:
            self._channel_last_msg[channel] = _now_ms()
        self._write_snapshot()

    def mark_heartbeat_tick(self) -> None:
        self._last_heartbeat_ms = _now_ms()
        self._write_snapshot()

    def mark_cron_run(self, job_name: str = "") -> None:
        self._last_cron_ms = _now_ms()
        self._last_cron_job = job_name
        self._write_snapshot()

    def update_cron_count(self, count: int) -> None:
        self._cron_job_count = count

    def update_channels(self, channels: list[str]) -> None:
        self._channels_enabled = list(channels)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def get_snapshot(self) -> dict[str, Any]:
        now_ms = _now_ms()
        uptime_s = (now_ms - self._started_at_ms) / 1000 if self._started_at_ms else None
        agent_age_s = _age_s(self._last_agent_turn_ms)
        stale = (
            agent_age_s is not None and agent_age_s > self.stale_threshold_s
        ) if agent_age_s is not None else (
            uptime_s is not None and uptime_s > self.stale_threshold_s
        )

        channel_health = {
            ch: {
                "last_message_age_s": round(_age_s(self._channel_last_msg.get(ch)), 1)
                if self._channel_last_msg.get(ch) else None
            }
            for ch in self._channels_enabled
        }

        return {
            "ok": not stale,
            "ts": now_ms,
            "started_at_ms": self._started_at_ms,
            "uptime_s": round(uptime_s, 1) if uptime_s is not None else None,
            "stale": stale,
            "stale_threshold_s": self.stale_threshold_s,
            "agent": {
                "last_turn_at_ms": self._last_agent_turn_ms,
                "last_turn_age_s": round(agent_age_s, 1) if agent_age_s is not None else None,
            },
            "heartbeat": {
                "last_tick_at_ms": self._last_heartbeat_ms,
                "last_tick_age_s": round(_age_s(self._last_heartbeat_ms), 1)
                    if self._last_heartbeat_ms else None,
                "interval_s": self._heartbeat_interval_s,
            },
            "cron": {
                "job_count": self._cron_job_count,
                "last_run_at_ms": self._last_cron_ms,
                "last_run_age_s": round(_age_s(self._last_cron_ms), 1)
                    if self._last_cron_ms else None,
                "last_job": self._last_cron_job,
            },
            "channels": channel_health,
        }

    def _write_snapshot(self) -> None:
        try:
            path = self.workspace / "health.json"
            path.write_text(
                json.dumps(self.get_snapshot(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("HealthService: failed to write snapshot: {}", e)

    # ------------------------------------------------------------------
    # Watchdog loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._watchdog_loop())
        logger.info("HealthService: watchdog started (interval={}s, stale_threshold={}s)",
                    self.snapshot_interval_s, self.stale_threshold_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _watchdog_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.snapshot_interval_s)
                if not self._running:
                    break
                snap = self.get_snapshot()
                self._write_snapshot()
                if snap.get("stale"):
                    age = snap["agent"].get("last_turn_age_s")
                    logger.warning(
                        "HealthService: agent appears stale (last turn {}s ago, threshold {}s)",
                        age, self.stale_threshold_s,
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("HealthService watchdog error: {}", e)
