"""Notification Fingerprint Helpers - builds canonical dedupe_key strings for notification idempotency.

Notification helper module that builds every dedupe_key as namespace:part1:part2[:bucket], dropping empty parts and appending an optional IST day, week or month bucket; the ledger and queue unique indexes drop any send sharing a key.

Features:
    - Consistent key shape: every producer gets the same colon-joined format
    - Empty-part dropping: None/"" parts are skipped rather than leaving stray colons
    - IST time buckets: day/week/month boundaries are computed from now_ist(), not UTC

Classes:
    No classes defined (module exposes make_key and its once/daily/weekly/monthly wrappers)

Functions:
    _bucket(): Render the current IST time bucket ("day"/"week"/"month") as a string, or None
    make_key(): Build a canonical dedupe key from a namespace, parts, and optional bucket
    once(): A key with no time component (one-per-entity, fire-once-ever)
    daily(): A key scoped to the current IST calendar day
    weekly(): A key scoped to the current IST ISO week
    monthly(): A key scoped to the current IST calendar month

Dependencies:
    - app.etown.notifications.rules.now_ist: the IST clock all bucket boundaries are computed from

Updates:
    11-Jun-2026: Module created to centralise dedupe-key construction (PDF §6).
"""

from __future__ import annotations

from typing import Any, Optional

from app.etown.notifications.rules import now_ist


def _bucket(kind: Optional[str]) -> Optional[str]:
    """Return the current IST day, week or month bucket string, None for no kind, or raise ValueError."""
    if not kind:
        return None
    now = now_ist()
    if kind == "day":
        return now.strftime("%Y-%m-%d")
    if kind == "month":
        return now.strftime("%Y-%m")
    if kind == "week":
        iso = now.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    raise ValueError(f"unknown bucket kind: {kind!r}")


def make_key(namespace: str, *parts: Any, bucket: Optional[str] = None) -> str:
    """Join the namespace, non-empty parts and optional IST bucket into a colon-separated dedupe key."""
    segs = [str(namespace)]
    segs.extend(str(p) for p in parts if p is not None and str(p) != "")
    b = _bucket(bucket)
    if b:
        segs.append(b)
    return ":".join(segs)


def once(namespace: str, *parts: Any) -> str:
    """Return a dedupe key with no time bucket, for fire-once-ever notifications."""
    return make_key(namespace, *parts)


def daily(namespace: str, *parts: Any) -> str:
    """Return a dedupe key scoped to the current IST calendar day."""
    return make_key(namespace, *parts, bucket="day")


def weekly(namespace: str, *parts: Any) -> str:
    """Return a dedupe key scoped to the current IST ISO week."""
    return make_key(namespace, *parts, bucket="week")


def monthly(namespace: str, *parts: Any) -> str:
    """Return a dedupe key scoped to the current IST calendar month."""
    return make_key(namespace, *parts, bucket="month")
