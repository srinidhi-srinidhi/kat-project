"""Notification Governance Rules - global caps, quiet hours and priority arbitration for pushes.

Governance helpers used by the notification gate: serve the DB-backed global caps with a TTL cache, flag the IST marketing quiet window, compute each user's effective push cap and decide when a high-priority type may bypass a full cap.

Features:
    - DB-backed Global Caps: get_caps() serves etown_notification_global_caps, TTL-cached with fallback.
    - Quiet Hours: in_quiet_hours() flags the IST 21:00-08:00 marketing-push quiet window.
    - Effective Push Cap: effective_push_cap() combines global cap, per-type override and inactivity throttle.
    - Priority Arbitration: may_preempt() lets flash-sale-class types bypass a full cap if they outrank.
    - Global Ceilings: etown_notification_config overrides can only tighten these limits, never breach them.

Classes:
    No classes defined (module exposes governance constants and rule functions)

Functions:
    _load_caps(): Fetch global caps from the DB into the in-process cache; never raises.
    get_caps(): Return the current global caps dict, TTL-cached with a safe fallback.
    invalidate_caps_cache(): Force the next get_caps() call to re-read from the DB.
    now_ist(): Return the current datetime in IST.
    in_quiet_hours(): Return True when the given (or current) IST hour is in the quiet window.
    effective_push_cap(): Compute the per-send effective push cap for a user.
    may_preempt(): Return True if a notification type may bypass a full cap via priority arbitration.

Dependencies:
    - app.db.client: Async database client for the global-caps table

Updates:
    05-Jun-2026: Module created — quiet hours, PRIORITY table, effective_push_cap(), may_preempt().
    15-Jun-2026: Added DB-backed global caps (_load_caps/get_caps/invalidate_caps_cache) with a TTL cache.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.db.client import Database

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

_DEFAULT_CAPS: Dict[str, Any] = {
    "push_daily_cap": 3,
    "wa_daily_cap": 2,
    "wa_weekly_per_business_cap": 1,
    "inactivity_throttle_enabled": True,
    "inactivity_throttle_exempt_lanes": ["social"],
}
_CAPS_CACHE: Dict[str, Any] = dict(_DEFAULT_CAPS)
_CAPS_EXP: float = 0.0
_CAPS_TTL: int = max(60, int(os.getenv("NOTIFICATIONS_CAPS_TTL_SECONDS", "7200")))
_CAPS_ERROR_RETRY_SECONDS: int = 15
_CAPS_LOCK = asyncio.Lock()


async def _load_caps() -> None:
    """Load the global notification caps into the TTL cache, keeping the last or default caps on error."""
    global _CAPS_CACHE, _CAPS_EXP
    try:
        row = await Database.fetchrow(
            """SELECT push_daily_cap, wa_daily_cap, wa_weekly_per_business_cap,
                      inactivity_throttle_enabled, inactivity_throttle_exempt_lanes
               FROM notifications.etown_notification_global_caps WHERE id = 'global'"""
        )
        if row:
            _CAPS_CACHE = {
                "push_daily_cap": int(row["push_daily_cap"]),
                "wa_daily_cap": int(row["wa_daily_cap"]),
                "wa_weekly_per_business_cap": int(row["wa_weekly_per_business_cap"]),
                "inactivity_throttle_enabled": bool(row["inactivity_throttle_enabled"]),
                "inactivity_throttle_exempt_lanes": list(
                    row["inactivity_throttle_exempt_lanes"] or []
                ),
            }
        else:
            _CAPS_CACHE = dict(_DEFAULT_CAPS)
        _CAPS_EXP = _time.monotonic() + _CAPS_TTL
    except Exception as exc:
        logger.warning("notif caps load failed, using last/default: %s", exc)
        _CAPS_CACHE = _CAPS_CACHE or dict(_DEFAULT_CAPS)
        _CAPS_EXP = _time.monotonic() + _CAPS_ERROR_RETRY_SECONDS


async def get_caps() -> Dict[str, Any]:
    """Return the global notification caps, reloading them from the DB when the cache has expired."""
    if _time.monotonic() < _CAPS_EXP:
        return _CAPS_CACHE
    async with _CAPS_LOCK:
        if _time.monotonic() >= _CAPS_EXP:
            await _load_caps()
    return _CAPS_CACHE


def invalidate_caps_cache() -> None:
    """Expire the caps cache so the next get_caps() re-reads the DB."""
    global _CAPS_EXP
    _CAPS_EXP = 0.0

QUIET_START_HOUR: int = 21
QUIET_END_HOUR: int = 8

PRIORITY: Dict[str, int] = {
    "flash_sale": 10,
    "loyalty_reward_unlocked": 15,
    "targeted_offer_circle": 20,
    "targeted_offer_group": 20,
    "loyalty_progress": 25,
    "new_provider_nearby": 30,
    "new_offer_nearby": 30,
    "provider_liked": 35,
    "ai_pick": 45,
    "mycircle_activity": 40,
    "morning_digest": 50,
    "evening_community": 50,
    "weekly_digest": 50,
    "re_engagement": 60,
}


def now_ist() -> datetime:
    """Return the current time in IST."""
    return datetime.now(IST)


def in_quiet_hours(dt: Optional[datetime] = None) -> bool:
    """Return True when the IST hour is in [21:00, 08:00)."""
    h = (dt or now_ist()).hour
    return h >= QUIET_START_HOUR or h < QUIET_END_HOUR


def effective_push_cap(cfg: Dict[str, Any], inactive: bool, caps: Optional[Dict[str, Any]] = None) -> int:
    """Return the daily push cap after per-type tightening and the 28-day inactivity throttle to 1."""
    caps = caps or _CAPS_CACHE
    cap = int(caps.get("push_daily_cap", _DEFAULT_CAPS["push_daily_cap"]))
    override = cfg.get("per_user_daily_cap_override")
    if override is not None:
        cap = min(cap, int(override))
    lane = cfg.get("queue") or ""
    exempt = caps.get("inactivity_throttle_exempt_lanes") or []
    if inactive and caps.get("inactivity_throttle_enabled", True) and lane not in exempt:
        cap = min(cap, 1)
    return cap


def may_preempt(cfg: Dict[str, Any], lowest_sent_priority: Optional[int]) -> bool:
    """Return True when a can_preempt_cap type outranks the lowest-priority digest already sent today."""
    if not cfg.get("can_preempt_cap"):
        return False
    if lowest_sent_priority is None:
        return False
    return int(cfg.get("priority", 50)) < lowest_sent_priority
