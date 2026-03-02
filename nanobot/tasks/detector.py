"""Task message detector — identifies long-running task requests."""

from __future__ import annotations

import re
from dataclasses import dataclass


# Matches: task: ..., Task: ..., /task: ..., /Task: ...
# With optional inline options: task[model=haiku,poll=0]: ...
_TASK_PREFIX_RE = re.compile(
    r"^/?task(?:\[([^\]]*)\])?\s*:\s*",
    re.IGNORECASE,
)

_OPT_RE = re.compile(r"(\w+)=([^\s,\]]+)")


@dataclass
class TaskIntent:
    prompt: str                 # Message with prefix stripped
    model: str | None           # Per-task model override (or None)
    poll_interval_s: int | None # Per-task poll interval (or None = use global)


def detect(content: str) -> TaskIntent | None:
    """Return TaskIntent if message is a task request, else None."""
    m = _TASK_PREFIX_RE.match(content.strip())
    if not m:
        return None

    opts_str = m.group(1) or ""
    prompt = content[m.end():].strip()
    if not prompt:
        return None

    opts: dict[str, str] = {k.lower(): v for k, v in _OPT_RE.findall(opts_str)}

    model: str | None = opts.get("model") or opts.get("m")
    poll_raw = opts.get("poll") or opts.get("p")
    poll_interval_s: int | None = None
    if poll_raw is not None:
        try:
            poll_interval_s = int(poll_raw)
        except ValueError:
            pass

    return TaskIntent(prompt=prompt, model=model, poll_interval_s=poll_interval_s)
