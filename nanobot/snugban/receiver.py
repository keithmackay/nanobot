"""Takes Snugban's signed webhooks and works its dispatches with nanobot's agent.

Every webhook is verified (signature, timestamp, delivery id) before its body is
read. Each dispatch is acknowledged over REST and run as agent turns in its own
session, "snugban:<dispatchId>", with a heartbeat to Snugban while a turn runs.
Keith's answers continue the dispatch that asked; pause and abort cancel the
running turn (killing the processes it started), and resume starts it again.

State is kept in memory only. After a restart nanobot knows no dispatch, so
Snugban's stall handling resends the dispatch (same dispatchId), and it is taken
again.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from nanobot.snugban.client import SnugbanClient
from nanobot.snugban.prompts import (
    Dispatch,
    checkout_path,
    decision_prompt,
    dispatch_prompt,
    resume_prompt,
)
from nanobot.snugban.verification import (
    DELIVERY_HEADER,
    SIGNATURE_HEADER,
    TOLERANCE_SECONDS,
    SeenIds,
    verify_signature,
)

CHANNEL = "snugban"
QUESTIONS_SESSION = "snugban:questions"

_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
_BRANCH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,254}$")

# How long a cancelled turn or heartbeat is given to finish unwinding.
_STOP_WAIT_S = 5.0
_MAX_RUNS_KEPT = 100

Answer = tuple[int, dict[str, Any]]


class AgentRunner(Protocol):
    """What the receiver needs of nanobot's AgentLoop."""

    subagents: Any

    async def process_direct(
        self, content: str, session_key: str, channel: str, chat_id: str
    ) -> str: ...


class _InvalidWebhookError(Exception):
    """A signed webhook whose body is not one nanobot can act on."""


@dataclass
class _Run:
    """nanobot's work on one dispatch."""

    dispatch: Dispatch
    pending: list[str] = field(default_factory=list)  # messages still to give the agent, in order
    worker: asyncio.Task | None = None
    heartbeat: asyncio.Task | None = None
    paused: bool = False
    interrupted: bool = False  # a pause cancelled a turn part-way
    ended: bool = False

    @property
    def session_key(self) -> str:
        return f"snugban:{self.dispatch.dispatch_id}"

    @property
    def working(self) -> bool:
        return self.worker is not None and not self.worker.done()


class SnugbanReceiver:
    """Handles Snugban's webhooks for nanobot."""

    def __init__(
        self,
        *,
        secret: str,
        client: SnugbanClient,
        agent: AgentRunner,
        workspace: Path,
        clock: Callable[[], float] = time.time,
        heartbeat_every_s: float | None = None,
    ):
        if not secret:
            raise ValueError("Snugban's webhook secret is required")
        self._secret = secret
        self._client = client
        self._agent = agent
        self._workspace = workspace
        self._clock = clock
        self._heartbeat_every_s = heartbeat_every_s
        # Delivery ids are kept for twice the timestamp tolerance, which covers a webhook
        # stamped up to five minutes in the future.
        self._deliveries = SeenIds(max_entries=10_000, ttl_s=2 * TOLERANCE_SECONDS)
        self._signatures = SeenIds(max_entries=10_000, ttl_s=2 * TOLERANCE_SECONDS)
        self._dispatches = SeenIds(max_entries=1_000)
        self._questions = SeenIds(max_entries=1_000)
        self._aborted = SeenIds(max_entries=1_000)
        self._runs: dict[str, _Run] = {}
        self._branch_locks: dict[str, asyncio.Lock] = {}
        self._background: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    async def handle(self, headers: Mapping[str, str], body: bytes) -> Answer:
        """Verify and act on one webhook; answers (HTTP status, JSON body)."""
        now = self._clock()
        if not verify_signature(self._secret, headers, body, now=now):
            logger.warning("Snugban: refused a webhook with a missing or invalid signature")
            return 401, {"error": "invalid signature"}
        lower = {name.lower(): value for name, value in headers.items()}
        delivery = lower.get(DELIVERY_HEADER, "")
        if not delivery or not self._deliveries.add(delivery, now=now):
            logger.warning("Snugban: refused a webhook with a missing or replayed delivery id")
            return 401, {"error": "missing or replayed delivery id"}
        # The delivery id is not signed, so a replay could carry a new one; the signature
        # (over the timestamp and body) cannot change without the secret.
        if not self._signatures.add(lower.get(SIGNATURE_HEADER, ""), now=now):
            logger.warning("Snugban: refused a replayed webhook (signature already seen)")
            return 401, {"error": "replayed webhook"}

        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise _InvalidWebhookError("the body is not a JSON object")
            kind = payload.get("type")
            if kind == "dispatch":
                return self._on_dispatch(_parse_dispatch(payload), now)
            if kind == "decision":
                return self._on_decision(payload, now)
            control = {
                "pause": self._on_pause,
                "resume": self._on_resume,
                "abort": self._on_abort,
                "status_request": self._on_status_request,
            }.get(kind)
            if control is not None:
                return await control(_require_id(payload, "dispatchId"), now)
            raise _InvalidWebhookError(f"unknown webhook type {kind!r}")
        except (ValueError, _InvalidWebhookError) as exc:
            logger.warning("Snugban: refused webhook {}: {}", delivery, exc)
            return 400, {"error": str(exc)}

    def _on_dispatch(self, dispatch: Dispatch, now: float) -> Answer:
        if not self._dispatches.add(dispatch.dispatch_id, now=now):
            logger.info("Snugban: dispatch {} already taken", dispatch.dispatch_id)
            return 200, {"status": "duplicate"}
        logger.info(
            "Snugban: dispatch {} for task {} on {}",
            dispatch.dispatch_id, dispatch.task_id, dispatch.branch,
        )
        self._in_background(self._take(dispatch))
        return 202, {"status": "accepted"}

    def _on_decision(self, payload: dict, now: float) -> Answer:
        question_id = _require_id(payload, "questionId")
        task_id = payload.get("taskId")
        answer = payload.get("answer")
        dismissed = payload.get("dismissed") is True
        if task_id is not None and not (isinstance(task_id, str) and _ID.match(task_id)):
            raise _InvalidWebhookError("taskId is not an id")
        if not dismissed and not isinstance(answer, str):
            raise _InvalidWebhookError("a decision needs an answer or dismissed: true")
        if not self._questions.add(question_id, now=now):
            logger.info("Snugban: decision on question {} already given", question_id)
            return 200, {"status": "duplicate"}

        message = decision_prompt(question_id, task_id, answer, dismissed)
        run = self._live_run_of_task(task_id)
        if run is not None:
            logger.info("Snugban: decision {} goes to dispatch {}", question_id,
                        run.dispatch.dispatch_id)
            run.pending.append(message)
            self._start_turns(run)
        else:
            logger.info("Snugban: decision {} goes to {}", question_id, QUESTIONS_SESSION)
            self._in_background(self._agent.process_direct(
                message, session_key=QUESTIONS_SESSION, channel=CHANNEL, chat_id="questions",
            ))
        return 202, {"status": "accepted"}

    async def _on_pause(self, dispatch_id: str, now: float) -> Answer:
        run = self._live_run(dispatch_id)
        if run is None:
            return 404, {"error": f"nanobot is not running dispatch {dispatch_id}"}
        if not run.paused:
            logger.info("Snugban: pausing dispatch {}", dispatch_id)
            run.paused = True
            run.interrupted = run.working
            await _cancel(run.worker)
            await _cancel(run.heartbeat)
        return 200, {"status": "paused"}

    async def _on_resume(self, dispatch_id: str, now: float) -> Answer:
        run = self._live_run(dispatch_id)
        if run is None:
            return 404, {"error": f"nanobot is not running dispatch {dispatch_id}"}
        if run.paused:
            logger.info("Snugban: resuming dispatch {}", dispatch_id)
            run.paused = False
            if run.interrupted and run.pending:
                run.pending[0] = resume_prompt(run.pending[0])
            run.interrupted = False
            self._start_turns(run)
        return 200, {"status": "running"}

    async def _on_abort(self, dispatch_id: str, now: float) -> Answer:
        # Remembered, so the dispatch is never run if it is (or was) also sent, even while
        # it is being acknowledged.
        self._aborted.add(dispatch_id, now=now)
        self._dispatches.add(dispatch_id, now=now)
        run = self._runs.get(dispatch_id)
        if run is not None and not run.ended:
            logger.info("Snugban: aborting dispatch {}", dispatch_id)
            await self._stop(run)
        return 200, {"status": "aborted"}

    async def _on_status_request(self, dispatch_id: str, now: float) -> Answer:
        run = self._live_run(dispatch_id)
        if run is None or not (run.working or run.paused):
            return 200, {"status": "not working"}
        self._in_background(self._client.heartbeat(dispatch_id))
        return 202, {"status": "working"}

    # ------------------------------------------------------------------
    # Dispatches
    # ------------------------------------------------------------------

    async def _take(self, dispatch: Dispatch) -> None:
        dispatch_id = dispatch.dispatch_id
        if dispatch.github_repo is None:
            await self._client.fail(
                dispatch_id,
                "nanobot works only on projects with a GitHub repository, and this project "
                "has none.",
            )
            return
        reply = await self._client.acknowledge(dispatch_id)
        if reply.ended or dispatch_id in self._aborted:
            logger.info("Snugban: dispatch {} has ended; not running it", dispatch_id)
            return
        if not reply.delivered:
            # Forgotten, so that Snugban's resend of it is taken.
            self._dispatches.discard(dispatch_id)
            return
        # Never two workers on one branch: stopping the old run and starting this one happen
        # under the branch's lock, so dispatches taken at once replace one another in turn.
        async with self._branch_locks.setdefault(dispatch.branch, asyncio.Lock()):
            for other in list(self._runs.values()):
                if not other.ended and other.dispatch.branch == dispatch.branch:
                    logger.info("Snugban: dispatch {} replaces {} on {}", dispatch_id,
                                other.dispatch.dispatch_id, dispatch.branch)
                    await self._stop(other)
            if dispatch_id in self._aborted:  # aborted while the old run was stopping
                return
            run = _Run(dispatch, pending=[dispatch_prompt(dispatch, self._workspace)])
            self._remember(run)
            self._start_turns(run)

    def _start_turns(self, run: _Run) -> None:
        if run.ended or run.paused or run.working or not run.pending:
            return
        run.worker = asyncio.create_task(self._work(run))
        run.heartbeat = asyncio.create_task(self._beat(run))

    async def _work(self, run: _Run) -> None:
        dispatch_id = run.dispatch.dispatch_id
        try:
            while run.pending:
                await self._agent.process_direct(
                    run.pending[0], session_key=run.session_key, channel=CHANNEL,
                    chat_id=dispatch_id,
                )
                run.pending.pop(0)
                if not run.pending:
                    await _cancel(run.heartbeat)
                    await self._check_reported(run)
                    if run.pending and not run.ended:  # an answer arrived meanwhile
                        run.heartbeat = asyncio.create_task(self._beat(run))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Snugban: the agent's turn on dispatch {} failed", dispatch_id)
            run.ended = True
            await _cancel(run.heartbeat)
            await self._remove_checkout(run)
            await self._client.fail(
                dispatch_id, f"nanobot's agent run stopped with an error ({type(exc).__name__})."
            )
            return

    async def _check_reported(self, run: _Run) -> None:
        """After the agent's turns end, says so if it left the dispatch neither done nor failed."""
        dispatch_id = run.dispatch.dispatch_id
        reply = await self._client.heartbeat(dispatch_id)
        if reply.ended or reply.state == "waiting_input":
            # Completed (its work is pushed) or over: the checkout is no longer needed.
            run.ended = reply.ended
            await self._remove_checkout(run)
            return
        if reply.delivered:
            await self._client.progress(
                dispatch_id,
                "nanobot's agent finished its turn without completing or failing the dispatch. "
                "It may be waiting for Keith's answer to a question; otherwise it has stopped.",
            )

    async def _beat(self, run: _Run) -> None:
        dispatch_id = run.dispatch.dispatch_id
        every = self._heartbeat_every_s or run.dispatch.heartbeat_every_s
        while True:
            await asyncio.sleep(every)
            reply = await self._client.heartbeat(dispatch_id)
            if reply.ended:
                logger.info("Snugban: dispatch {} has ended; stopping its run", dispatch_id)
                run.heartbeat = None  # this task; _stop must not wait on itself
                await self._stop(run)
                return

    async def _stop(self, run: _Run) -> None:
        """Ends nanobot's work on a dispatch: its turn, its subagents and its heartbeats."""
        run.ended = True
        run.pending.clear()
        await _cancel(run.worker)
        await _cancel(run.heartbeat)
        try:
            await self._agent.subagents.cancel_by_session(run.session_key)
        except Exception:
            logger.exception("Snugban: could not cancel subagents of {}", run.session_key)
        await self._remove_checkout(run)

    async def _remove_checkout(self, run: _Run) -> None:
        path = checkout_path(run.dispatch, self._workspace)
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)

    def _live_run(self, dispatch_id: str) -> _Run | None:
        run = self._runs.get(dispatch_id)
        return run if run is not None and not run.ended else None

    def _live_run_of_task(self, task_id: str | None) -> _Run | None:
        if task_id is None:
            return None
        for run in reversed(self._runs.values()):
            if run.dispatch.task_id == task_id and not run.ended:
                return run
        return None

    def _remember(self, run: _Run) -> None:
        self._runs[run.dispatch.dispatch_id] = run
        for dispatch_id, old in list(self._runs.items()):
            if len(self._runs) <= _MAX_RUNS_KEPT:
                break
            if old.ended:
                del self._runs[dispatch_id]

    def _in_background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._finished_in_background)

    def _finished_in_background(self, task: asyncio.Task) -> None:
        self._background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.opt(exception=task.exception()).error("Snugban: background work failed")

    async def close(self) -> None:
        """Stops every run (Snugban is not told) and closes the connection to Snugban."""
        for run in list(self._runs.values()):
            if not run.ended:
                await self._stop(run)
        for task in list(self._background):
            await _cancel(task)
        await self._client.aclose()


async def _cancel(task: asyncio.Task | None) -> None:
    """Cancel a task and give it a little time to unwind (never the calling task)."""
    if task is None or task.done() or task is asyncio.current_task():
        return
    task.cancel()
    await asyncio.wait({task}, timeout=_STOP_WAIT_S)


def _require_text(obj: Any, key: str) -> str:
    value = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(value, str):
        raise _InvalidWebhookError(f"{key} is missing")
    return value


def _require_id(obj: dict, key: str) -> str:
    value = _require_text(obj, key)
    if not _ID.match(value):
        raise _InvalidWebhookError(f"{key} is not an id")
    return value


def _matching(value: str, pattern: re.Pattern, key: str) -> str:
    if not pattern.match(value) or ".." in value:
        raise _InvalidWebhookError(f"{key} is malformed")
    return value


def _parse_dispatch(payload: dict) -> Dispatch:
    task = payload.get("task")
    project = payload.get("project")
    returns = payload.get("return")
    if not isinstance(task, dict) or not isinstance(project, dict):
        raise _InvalidWebhookError("task and project are required")
    repo = project.get("repo")
    if not isinstance(repo, dict):
        raise _InvalidWebhookError("project.repo is missing")
    github_repo = repo.get("githubRepo")
    if github_repo is not None:
        github_repo = _matching(_require_text(repo, "githubRepo"), _GITHUB_REPO, "githubRepo")
    default_branch = project.get("defaultBranch")
    if default_branch is not None:
        default_branch = _matching(
            _require_text(project, "defaultBranch"), _BRANCH, "defaultBranch"
        )
    revision = payload.get("revision")
    if revision is not None:
        revision = _require_text(revision, "changes")
    every = returns.get("heartbeatEverySeconds") if isinstance(returns, dict) else None
    if not isinstance(every, (int, float)) or isinstance(every, bool) or every <= 0:
        every = 60
    check = task.get("dodCheckCmd")
    return Dispatch(
        dispatch_id=_require_id(payload, "dispatchId"),
        task_id=_require_id(task, "id"),
        title=_require_text(task, "title"),
        description=_require_text(task, "description"),
        dod_criteria=_require_text(task, "dodCriteria"),
        project_slug=_matching(_require_text(project, "slug"), _SLUG, "project.slug"),
        github_repo=github_repo,
        default_branch=default_branch,
        branch=_matching(_require_text(payload, "branch"), _BRANCH, "branch"),
        instructions=_require_text(payload, "instructions"),
        heartbeat_every_s=float(min(every, 60)),
        revision_changes=revision,
        dod_check_cmd=check if isinstance(check, str) else None,
    )
