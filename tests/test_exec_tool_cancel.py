"""Tests that the exec tool stops the commands it started when its turn is cancelled."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from nanobot.agent.tools.shell import ExecTool


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _wait_for_file(path: Path, timeout: float = 5.0) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        await asyncio.sleep(0.05)
    raise AssertionError(f"{path} was never written")


async def _wait_until_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_cancelling_exec_kills_the_command_and_its_children(tmp_path: Path):
    pidfile = tmp_path / "child.pid"
    tool = ExecTool(timeout=60, working_dir=str(tmp_path))
    run = asyncio.create_task(
        tool.execute(command=f"sleep 30 & echo $! > {pidfile}; wait")
    )
    child = int(await _wait_for_file(pidfile))

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert await _wait_until_gone(child), "the command's child survived cancellation"


@pytest.mark.asyncio
async def test_timed_out_exec_kills_the_command_and_its_children(tmp_path: Path):
    pidfile = tmp_path / "child.pid"
    tool = ExecTool(timeout=1, working_dir=str(tmp_path))

    result = await tool.execute(command=f"sleep 30 & echo $! > {pidfile}; wait")

    assert "timed out" in result
    assert await _wait_until_gone(int(pidfile.read_text().strip()))


@pytest.mark.asyncio
async def test_exec_runs_the_command_in_its_own_group_but_nanobots_session(tmp_path: Path):
    """Its own process group can be killed whole; staying in our session keeps the terminal
    (sudo, ssh and git prompts in `nanobot agent`)."""
    import sys

    tool = ExecTool(timeout=30, working_dir=str(tmp_path))
    out = await tool.execute(
        command=f"{sys.executable} -c 'import os; print(os.getsid(0), os.getpgid(0))'"
    )
    sid, pgid = (int(x) for x in out.split())
    assert sid == os.getsid(0)
    assert pgid != os.getpgid(0)
