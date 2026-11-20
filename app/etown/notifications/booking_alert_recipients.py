"""Booking Alert Recipients - DB-backed support-team booking alert recipients and excluded booker mobiles.

Notification config module: get_booking_alert_recipients reads enabled CSR numbers from notifications.etown_booking_alert_sms_recipients through a TTL cache, and is_booking_alert_excluded checks the booker's mobile against notifications.etown_booking_alert_excluded_mobiles.

Features:
    - Operators toggle a recipient with UPDATE ... SET enabled = false WHERE mobile_number = '<number>'
    - TTL-cached Lookup: get_booking_alert_recipients() serves from an in-process cache refreshed per TTL.
    - Env Fallback: an empty table or a DB read failure resolves to BOOKING_ALERT_SMS_RECIPIENTS.
    - Manual Invalidation: invalidate_cache() drops the cache immediately for config_reload.
    - Booker Exclusion: an enabled etown_booking_alert_excluded_mobiles row suppresses the CSR SMS and email.
    - Live Exclusion Lookup: the exclusion is read per booking, uncached, falling back to BOOKING_EMAIL_EXCLUDED_MOBILE_NUMBERS on DB failure.

Classes:
    No classes defined (module exposes cache-refresh and lookup functions)

Functions:
    _refresh_cache(): Reload the mobile_number -> enabled cache from the DB.
    _ensure_fresh(): Refresh the cache if the TTL has expired (lock-guarded).
    invalidate_cache(): Force the next get_booking_alert_recipients() call to re-read from the DB.
    get_booking_alert_recipients(): Return the enabled recipient numbers, or the env list when the table has no rows.
    is_booking_alert_excluded(): Return True when the booker's mobile has an enabled exclusion row.

Dependencies:
    - app.db.client: Async PostgreSQL pool (lazy import inside _refresh_cache() and is_booking_alert_excluded())
    - app.core.config: settings.BOOKING_ALERT_SMS_RECIPIENTS, BOOKING_EMAIL_EXCLUDED_MOBILE_NUMBERS and WA_TEMPLATE_CONFIG_CACHE_TTL_SECONDS
    - app.core.logging: Centralised logger

Updates:
    28-Sep-2026: Initial creation; added is_booking_alert_excluded over etown_booking_alert_excluded_mobiles.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Dict, List, Optional

from app.core.config import settings
from app.core.logging import logger


_CACHE: Optional[Dict[str, bool]] = None
_CACHE_EXP: float = 0.0
_CACHE_TTL: int = max(60, int(getattr(settings, "WA_TEMPLATE_CONFIG_CACHE_TTL_SECONDS", 7200)))
_ERROR_RETRY_SECONDS: int = 15
_LOCK = asyncio.Lock()


async def _refresh_cache() -> None:
    """Reload recipient enabled flags from the DB, keeping None and retrying after 15 seconds on failure."""
    global _CACHE, _CACHE_EXP
    try:
        from app.db.client import Database
        rows = await Database.fetch(
            "SELECT mobile_number, enabled "
            "FROM notifications.etown_booking_alert_sms_recipients "
            "ORDER BY mobile_number"
        )
        _CACHE = {str(r["mobile_number"]).strip(): bool(r["enabled"]) for r in rows}
        _CACHE_EXP = time.monotonic() + _CACHE_TTL
        logger.debug(
            "[BOOKING_ALERT_RCPT] Cache refreshed: %d recipients, TTL=%ds",
            len(_CACHE), _CACHE_TTL,
        )
    except Exception as exc:
        _CACHE = None
        _CACHE_EXP = time.monotonic() + _ERROR_RETRY_SECONDS
        logger.error(
            "[BOOKING_ALERT_RCPT] Failed to refresh cache - falling back to BOOKING_ALERT_SMS_RECIPIENTS: %s",
            exc,
            exc_info=True,
        )


async def _ensure_fresh() -> None:
    """Refresh the recipient cache under a lock once its expiry has passed."""
    if time.monotonic() < _CACHE_EXP:
        return
    async with _LOCK:
        if time.monotonic() >= _CACHE_EXP:
            await _refresh_cache()


def invalidate_cache() -> None:
    """Expire the recipient cache so the next get_booking_alert_recipients call re-reads the DB."""
    global _CACHE_EXP
    _CACHE_EXP = 0.0


async def get_booking_alert_recipients() -> List[str]:
    """Return the enabled booking alert SMS numbers, or BOOKING_ALERT_SMS_RECIPIENTS when the table is empty or unreadable."""
    await _ensure_fresh()

    if not _CACHE:
        return list(settings.BOOKING_ALERT_SMS_RECIPIENTS or [])

    return [number for number, enabled in _CACHE.items() if enabled]


async def is_booking_alert_excluded(mobile_number: str) -> bool:
    """Return True when the booker's mobile has an enabled etown_booking_alert_excluded_mobiles row, using the env list on DB failure."""
    normalized = re.sub(r"\D", "", str(mobile_number or ""))[-10:]
    if not normalized:
        return False
    try:
        from app.db.client import Database
        enabled = await Database.fetchval(
            "SELECT enabled "
            "FROM notifications.etown_booking_alert_excluded_mobiles "
            "WHERE right(regexp_replace(mobile_number, '\\D', '', 'g'), 10) = $1 "
            "ORDER BY enabled DESC LIMIT 1",
            normalized,
        )
        return bool(enabled)
    except Exception as exc:
        logger.error(
            "[BOOKING_ALERT_EXCL] Exclusion lookup failed - falling back to BOOKING_EMAIL_EXCLUDED_MOBILE_NUMBERS: %s",
            exc,
            exc_info=True,
        )
        return normalized in {
            re.sub(r"\D", "", str(number))[-10:]
            for number in settings.BOOKING_EMAIL_EXCLUDED_MOBILE_NUMBERS or []
        }
