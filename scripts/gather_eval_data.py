#!/usr/bin/env python3
"""
gather_eval_data.py — Pre-processes Claude session JSONL files for the nightly context eval.

Usage:
  python3 scripts/gather_eval_data.py [--days 2] [--project nanobot]

Outputs a compact Markdown summary of sessions, token usage, and threshold breaches.
This replaces Claude reading raw JSONL files during the eval, saving ~50-80K tokens.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=2, help="Days to look back")
    p.add_argument("--project", default="nanobot", help="Project name (substring match)")
    p.add_argument("--threshold", type=int, default=85000, help="Breach threshold (cache_read tokens)")
    return p.parse_args()


def get_session_files(project: str, since: datetime) -> list:
    base = Path.home() / ".claude" / "projects"
    if not base.exists():
        return []
    candidates = [d for d in base.iterdir() if d.is_dir() and project.lower() in d.name.lower()]
    if not candidates:
        return []
    project_dir = max(candidates, key=lambda d: len(d.name))
    files = []
    for f in project_dir.glob("*.jsonl"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime >= since:
            files.append(f)
    return sorted(files, key=lambda f: f.stat().st_mtime)


def analyze_session(path) -> dict:
    peak_cache_read = 0
    peak_input = 0
    total_output = 0
    total_cache_create = 0
    model = None
    turn_count = 0
    first_ts = None
    last_ts = None

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = obj.get("timestamp")
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

                msg = obj.get("message", {})
                if not isinstance(msg, dict):
                    continue

                if not model and msg.get("model"):
                    model = msg["model"]

                usage = msg.get("usage", {})
                if usage:
                    cr = usage.get("cache_read_input_tokens", 0)
                    inp = usage.get("input_tokens", 0)
                    out = usage.get("output_tokens", 0)
                    cc = usage.get("cache_creation_input_tokens", 0)
                    if cr > peak_cache_read:
                        peak_cache_read = cr
                    if inp > peak_input:
                        peak_input = inp
                    total_output += out
                    total_cache_create += cc
                    if msg.get("role") == "assistant":
                        turn_count += 1
    except Exception as e:
        return {"error": str(e), "id": Path(path).stem[:8]}

    size_kb = Path(path).stat().st_size // 1024
    return {
        "id": Path(path).stem[:8],
        "size_kb": size_kb,
        "model": model or "unknown",
        "peak_cache_read": peak_cache_read,
        "peak_input": peak_input,
        "total_output": total_output,
        "total_cache_create": total_cache_create,
        "turns": turn_count,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def ts_to_hhmm(ts):
    if not ts:
        return "?"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%H:%M")
    except Exception:
        return ts[:5]


def format_report(sessions: list, threshold: int, days: int) -> str:
    lines = [
        f"# Context Eval Data — {datetime.now().strftime('%Y-%m-%d %H:%M')} ET",
        f"Looking back {days} day(s). Breach threshold: {threshold:,} cache_read tokens.",
        "",
        "## Session Summary",
        "",
        "| ID | Time | Size | Peak Cache Read | Turns | Flag |",
        "|-----|------|------|-----------------|-------|------|",
    ]

    breaches = []
    total_output = 0
    total_create = 0

    for s in sessions:
        if "error" in s:
            lines.append(f"| {s['id']} | ERROR | — | — | — | ⚠️ {s['error'][:40]} |")
            continue
        flag = "⛔" if s["peak_cache_read"] >= threshold else "✅"
        lines.append(
            f"| {s['id']} | {ts_to_hhmm(s['last_ts'] or s['first_ts'])} | {s['size_kb']}KB "
            f"| {s['peak_cache_read']:,} | {s['turns']} | {flag} |"
        )
        if s["peak_cache_read"] >= threshold:
            breaches.append(s)
        total_output += s.get("total_output", 0)
        total_create += s.get("total_cache_create", 0)

    lines += [
        "",
        f"**Sessions analyzed:** {len(sessions)}",
        f"**Threshold breaches:** {len(breaches)}",
        f"**Total output tokens (all sessions):** {total_output:,}",
        f"**Total cache creation tokens:** {total_create:,}",
        "",
    ]

    if breaches:
        lines += ["## Breach Details", ""]
        for s in breaches:
            lines.append(
                f"- **{s['id']}** ({s['size_kb']}KB): peak_cache_read={s['peak_cache_read']:,}, "
                f"turns={s['turns']}, model={s['model']}"
            )
        lines.append("")

    return "\n".join(lines)


def main():
    args = parse_args()
    since = datetime.now(tz=timezone.utc) - timedelta(days=args.days)
    files = get_session_files(args.project, since)

    if not files:
        print(f"# Context Eval Data\nNo sessions found for project '{args.project}' in the last {args.days} days.")
        sys.exit(0)

    sessions = [analyze_session(f) for f in files]
    print(format_report(sessions, args.threshold, args.days))


if __name__ == "__main__":
    main()
