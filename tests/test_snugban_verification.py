"""Tests for checking that a webhook came from Snugban, and for remembering ids already seen."""

from __future__ import annotations

import hashlib
import hmac

from nanobot.snugban.verification import SeenIds, verify_signature

SECRET = "whsec_test_0123456789abcdef"
TIMESTAMP = "1762646400"
BODY = b'{"type":"status_request","dispatchId":"01KJN2Q8Z5V7W3X4Y6A8B0C2D4"}'
# Computed by Snugban's recipe in Node:
#   `sha256=${createHmac('sha256', secret).update(`${timestamp}.${rawBody}`).digest('hex')}`
NODE_SIGNATURE = "sha256=96e9a726a3d3a4d1b7efa120d5004eb3cde2cefc10f6ba4f7105ab900978c45f"


def _headers(timestamp: str = TIMESTAMP, signature: str = NODE_SIGNATURE) -> dict[str, str]:
    return {"x-snugban-timestamp": timestamp, "x-snugban-signature": signature}


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Signs a body as Snugban's sender does."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + digest.hexdigest()


def test_accepts_the_signature_snugbans_node_sender_produces():
    assert verify_signature(SECRET, _headers(), BODY, now=float(TIMESTAMP))


def test_signs_exactly_as_snugbans_sender():
    assert sign(SECRET, TIMESTAMP, BODY) == NODE_SIGNATURE


def test_header_names_are_matched_without_regard_to_case():
    headers = {"X-Snugban-Timestamp": TIMESTAMP, "X-Snugban-Signature": NODE_SIGNATURE}
    assert verify_signature(SECRET, headers, BODY, now=float(TIMESTAMP))


def test_refuses_a_signature_made_with_another_secret():
    headers = _headers(signature=sign("another secret", TIMESTAMP, BODY))
    assert not verify_signature(SECRET, headers, BODY, now=float(TIMESTAMP))


def test_refuses_a_changed_body():
    changed = BODY.replace(b"status_request", b"abort")
    assert not verify_signature(SECRET, _headers(), changed, now=float(TIMESTAMP))


def test_refuses_a_timestamp_more_than_five_minutes_away():
    assert not verify_signature(SECRET, _headers(), BODY, now=float(TIMESTAMP) + 301)
    assert not verify_signature(SECRET, _headers(), BODY, now=float(TIMESTAMP) - 301)


def test_accepts_a_timestamp_within_five_minutes():
    assert verify_signature(SECRET, _headers(), BODY, now=float(TIMESTAMP) + 300)
    assert verify_signature(SECRET, _headers(), BODY, now=float(TIMESTAMP) - 300)


def test_refuses_a_missing_signature_or_timestamp():
    now = float(TIMESTAMP)
    assert not verify_signature(SECRET, {"x-snugban-timestamp": TIMESTAMP}, BODY, now=now)
    assert not verify_signature(SECRET, {"x-snugban-signature": NODE_SIGNATURE}, BODY, now=now)
    assert not verify_signature(SECRET, {}, BODY, now=now)


def test_refuses_a_timestamp_that_is_not_whole_seconds():
    for timestamp in ("1762646400.0", "-1762646400", "", "abc", "١٧٦٢٦٤٦٤٠٠"):
        headers = _headers(timestamp=timestamp, signature=sign(SECRET, timestamp, BODY))
        assert not verify_signature(SECRET, headers, BODY, now=float(TIMESTAMP)), timestamp


def test_refuses_a_bare_hex_signature_without_its_prefix():
    headers = _headers(signature=NODE_SIGNATURE.removeprefix("sha256="))
    assert not verify_signature(SECRET, headers, BODY, now=float(TIMESTAMP))


def test_refuses_everything_when_the_secret_is_empty():
    headers = _headers(signature=sign("", TIMESTAMP, BODY))
    assert not verify_signature("", headers, BODY, now=float(TIMESTAMP))


def test_seen_ids_reports_an_id_new_once():
    seen = SeenIds(max_entries=10)
    assert seen.add("a", now=0)
    assert not seen.add("a", now=1)
    assert seen.add("b", now=1)


def test_seen_ids_forget_an_id_after_its_time_to_live():
    seen = SeenIds(max_entries=10, ttl_s=600)
    assert seen.add("a", now=0)
    assert not seen.add("a", now=599)
    assert seen.add("a", now=601)


def test_seen_ids_hold_at_most_max_entries_dropping_the_oldest():
    seen = SeenIds(max_entries=2)
    seen.add("a", now=0)
    seen.add("b", now=1)
    seen.add("c", now=2)
    assert len(seen) == 2
    assert seen.add("a", now=3)
    assert not seen.add("c", now=3)


def test_seen_ids_can_forget_an_id():
    seen = SeenIds(max_entries=10)
    seen.add("a", now=0)
    seen.discard("a")
    assert seen.add("a", now=1)
    assert "a" in seen


def test_refuses_an_over_long_timestamp_without_raising():
    timestamp = "1" * 5000
    headers = _headers(timestamp=timestamp, signature=sign(SECRET, timestamp, BODY))
    assert not verify_signature(SECRET, headers, BODY, now=float(TIMESTAMP))
