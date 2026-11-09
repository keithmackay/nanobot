"""The Snugban receiver's settings: from config (gateway.snugban), else from the environment.

Environment variables: SNUGBAN_URL, SNUGBAN_AGENT_KEY and SNUGBAN_WEBHOOK_SECRET.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from nanobot.config.schema import SnugbanConfig


@dataclass(frozen=True)
class SnugbanSettings:
    url: str  # Snugban's origin, e.g. http://snugban-host.tailnet.ts.net:7420
    agent_key: str = field(repr=False)
    webhook_secret: str = field(repr=False)


def snugban_settings(
    config: SnugbanConfig, environ: Mapping[str, str] = os.environ
) -> SnugbanSettings | None:
    """The receiver's settings; None when none are given, ValueError when only some are."""
    values = {
        "url": (config.url or environ.get("SNUGBAN_URL", "")).strip(),
        "agentKey": (config.agent_key or environ.get("SNUGBAN_AGENT_KEY", "")).strip(),
        "webhookSecret": (
            config.webhook_secret or environ.get("SNUGBAN_WEBHOOK_SECRET", "")
        ).strip(),
    }
    if not any(values.values()):
        return None
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(
            "gateway.snugban is incomplete: set "
            + ", ".join(f"{name}" for name in missing)
            + " (or SNUGBAN_URL, SNUGBAN_AGENT_KEY, SNUGBAN_WEBHOOK_SECRET)"
        )
    parts = urlsplit(values["url"])
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("gateway.snugban.url must be an http(s) URL, e.g. http://host:7420")
    return SnugbanSettings(
        url=values["url"], agent_key=values["agentKey"], webhook_secret=values["webhookSecret"]
    )
