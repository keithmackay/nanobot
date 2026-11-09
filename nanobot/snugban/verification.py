"""Checks that a webhook came from Snugban, and remembers ids already acted on.

Snugban signs each webhook with HMAC-SHA256, keyed with the agent's webhook secret,
over "<X-Snugban-Timestamp>.<raw body>", and sends it as "sha256=<lowercase hex>"
in X-Snugban-Signature. A webhook more than five minutes from our clock is refused.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import OrderedDict
from collections.abc import Mapping

TOLERANCE_SECONDS = 5 * 60

TIMESTAMP_HEADER = "x-snugban-timestamp"
SIGNATURE_HEADER = "x-snugban-signature"
DELIVERY_HEADER = "x-snugban-delivery"


def verify_signature(
    secret: str, headers: Mapping[str, str], raw_body: bytes, *, now: float
) -> bool:
    """Whether the webhook was signed by Snugban with *secret* within five minutes of *now*.

    *raw_body* must be the body bytes exactly as received, before any JSON parsing.
    """
    if not secret:
        return False
    lower = {name.lower(): value for name, value in headers.items()}
    timestamp = lower.get(TIMESTAMP_HEADER, "")
    signature = lower.get(SIGNATURE_HEADER, "")
    if not (timestamp.isascii() and timestamp.isdigit()) or not signature:
        return False
    if abs(now - int(timestamp)) > TOLERANCE_SECONDS:
        return False
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256)
    expected = b"sha256=" + digest.hexdigest().encode()
    return hmac.compare_digest(signature.encode(), expected)


class SeenIds:
    """Ids already seen, oldest dropped first once *max_entries* are held.

    With *ttl_s*, an id is forgotten that long after it was first seen.
    """

    def __init__(self, *, max_entries: int, ttl_s: float | None = None):
        self._max_entries = max_entries
        self._ttl_s = ttl_s
        self._seen: OrderedDict[str, float] = OrderedDict()

    def add(self, key: str, *, now: float) -> bool:
        """Record *key*; True if it was not already seen."""
        self._expire(now)
        if key in self._seen:
            return False
        self._seen[key] = now
        while len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
        return True

    def discard(self, key: str) -> None:
        self._seen.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def _expire(self, now: float) -> None:
        if self._ttl_s is None:
            return
        while self._seen:
            key, seen_at = next(iter(self._seen.items()))
            if now - seen_at <= self._ttl_s:
                break
            del self._seen[key]
