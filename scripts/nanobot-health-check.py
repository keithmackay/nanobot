#!/usr/bin/env python3
"""Nanobot gateway health monitor.

Runs every 30 minutes via crontab. Checks:
  1. Gateway process alive (launchctl) → kickstart if dead
  2. Health snapshot staleness → kickstart if stale > threshold
  3. Stuck `claude` subprocesses (> 5 min) → kill them
  4. ollama-proxy availability on port 11435 → restart if down
  5. Log file rotation (gateway.err.log > LOG_ROTATE_MB)

Sends a Telegram alert whenever it intervenes.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

UID = os.getuid()
GATEWAY_SERVICE = f"gui/{UID}/ai.nanobot.gateway"
OLLAMA_PROXY_SERVICE = f"gui/{UID}/com.openclaw.ollama-proxy"
OLLAMA_PROXY_PORT = 11435

HEALTH_JSON = Path.home() / ".nanobot/workspace/health.json"
LOG_DIR = Path.home() / ".nanobot/logs"
WATCHDOG_LOG = LOG_DIR / "nanobot-health-check.log"
GATEWAY_ERR_LOG = LOG_DIR / "gateway.err.log"
GATEWAY_LOG = LOG_DIR / "gateway.log"

STALE_RESTART_THRESHOLD_S = 7200   # restart gateway if no agent turn for 2 hours
STUCK_PROCESS_THRESHOLD_S = 300    # kill claude subprocesses older than 5 minutes
LOG_ROTATE_MB = 50                 # rotate gateway logs when they exceed this size

TELEGRAM_BOT_TOKEN = "7913654528:AAFC_lkaqVP4txwVJ6Ghqi-dr_9lfrnKet4"
TELEGRAM_CHAT_ID = "8414985222"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(WATCHDOG_LOG),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("nanobot-health")
if sys.stdout.isatty():
    logging.getLogger().addHandler(logging.StreamHandler())

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def telegram_alert(message: str) -> None:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        body = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": f"🤖 nanobot watchdog:\n{message}"}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        log.info("Telegram alert sent")
    except Exception as exc:
        log.warning("Telegram alert failed: %s", exc)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as exc:
        return -1, "", str(exc)


def gateway_state() -> str:
    rc, out, _ = run(["launchctl", "print", GATEWAY_SERVICE])
    if rc != 0:
        return "not_loaded"
    if "state = running" in out:
        return "running"
    return "loaded_not_running"


def kickstart_gateway() -> bool:
    rc, out, err = run(["launchctl", "kickstart", "-k", GATEWAY_SERVICE])
    if rc == 0:
        log.info("Gateway kickstart succeeded")
        return True
    log.error("Gateway kickstart failed: %s %s", out.strip(), err.strip())
    return False


def read_health_snapshot() -> dict | None:
    try:
        return json.loads(HEALTH_JSON.read_text())
    except Exception as exc:
        log.warning("Could not read health.json: %s", exc)
        return None


def find_stuck_claude_processes() -> list[tuple[int, int, str]]:
    rc, out, _ = run(["ps", "-eo", "pid,etime,command"])
    if rc != 0:
        return []
    stuck = []
    for line in out.splitlines():
        if "claude" not in line or "grep" in line or "nanobot-health" in line:
            continue
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        seconds = _parse_etime(parts[1])
        if seconds is not None and seconds > STUCK_PROCESS_THRESHOLD_S:
            stuck.append((pid, seconds, parts[2]))
    return stuck


def _parse_etime(etime: str) -> int | None:
    try:
        days = 0
        if "-" in etime:
            day_part, etime = etime.split("-", 1)
            days = int(day_part)
        parts = etime.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            h, m, s = 0, int(parts[0]), int(parts[1])
        else:
            return None
        return days * 86400 + h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        return None


def kill_stuck_claude(processes: list[tuple[int, int, str]]) -> list[str]:
    killed = []
    for pid, age, cmd in processes:
        log.warning("Killing stuck process pid=%d age=%ds cmd=%s", pid, age, cmd[:80])
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
                log.warning("Force-killed pid=%d", pid)
            except ProcessLookupError:
                pass
            killed.append(f"pid={pid} age={age}s")
        except ProcessLookupError:
            pass
        except PermissionError:
            log.error("Permission denied killing pid=%d", pid)
    return killed


def check_ollama_proxy() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", OLLAMA_PROXY_PORT), timeout=5):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def restart_ollama_proxy() -> bool:
    rc, _, err = run(["launchctl", "kickstart", "-k", OLLAMA_PROXY_SERVICE])
    if rc == 0:
        log.info("ollama-proxy kickstarted")
        return True
    log.error("ollama-proxy kickstart failed: %s", err.strip())
    return False


def rotate_log(path: Path, max_mb: int) -> bool:
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > max_mb:
            backup = path.with_suffix(path.suffix + ".1")
            if backup.exists():
                backup.unlink()
            path.rename(backup)
            path.touch()
            log.info("Rotated %s (was %.1fMB)", path.name, size_mb)
            return True
    except Exception as exc:
        log.warning("Log rotation failed for %s: %s", path, exc)
    return False

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("--- health check start ---")
    interventions: list[str] = []

    # 1. Gateway process check
    state = gateway_state()
    log.info("Gateway state: %s", state)
    if state != "running":
        log.warning("Gateway not running (state=%s), kickstarting", state)
        kickstart_gateway()
        interventions.append(f"gateway was {state} → kickstarted")
        telegram_alert("\n".join(interventions))
        log.info("--- health check end (gateway dead) ---")
        return  # give it time to start before further checks

    # 2. Health snapshot staleness
    snap = read_health_snapshot()
    if snap:
        agent_age = snap.get("agent", {}).get("last_turn_age_s")
        uptime = snap.get("uptime_s", 0)
        stale = snap.get("stale", False)

        # Only restart if actually stale AND been up long enough to have had a turn
        if stale and uptime and uptime > STALE_RESTART_THRESHOLD_S:
            age_str = f"{agent_age:.0f}s" if agent_age else "never"
            log.warning("Gateway stale (last turn: %s), kickstarting", age_str)
            kickstart_gateway()
            interventions.append(f"gateway stale (last turn {age_str}) → kickstarted")
        else:
            age_str = f"{agent_age:.0f}s ago" if agent_age else "never"
            log.info("Health snapshot: stale=%s, last_turn=%s", stale, age_str)
    else:
        log.warning("No health snapshot found")

    # 3. Stuck claude subprocesses
    stuck = find_stuck_claude_processes()
    if stuck:
        log.warning("Found %d stuck claude process(es)", len(stuck))
        killed = kill_stuck_claude(stuck)
        if killed:
            interventions.append(f"killed {len(killed)} stuck claude process(es): {', '.join(killed)}")
    else:
        log.info("No stuck claude processes")

    # 4. ollama-proxy check
    if check_ollama_proxy():
        log.info("ollama-proxy OK on port %d", OLLAMA_PROXY_PORT)
    else:
        log.warning("ollama-proxy not responding on port %d", OLLAMA_PROXY_PORT)
        if restart_ollama_proxy():
            interventions.append("ollama-proxy was down → kickstarted")
        else:
            interventions.append("ollama-proxy down and kickstart failed")

    # 5. Log rotation
    for logfile in [GATEWAY_ERR_LOG, GATEWAY_LOG]:
        if logfile.exists() and rotate_log(logfile, LOG_ROTATE_MB):
            interventions.append(f"rotated {logfile.name}")

    # Alert if anything needed fixing
    if interventions:
        telegram_alert("\n".join(f"• {i}" for i in interventions))
    else:
        log.info("All checks passed")

    log.info("--- health check end ---")


if __name__ == "__main__":
    main()
