"""WhatsApp Template Config - DB-backed per-template send switch with a TTL cache.

Notification config module: is_template_enabled reads the enabled flag for a template_key from notifications.etown_whatsapp_template_config through an in-process cache, so a DB toggle applies within one TTL or at once through the config_reload bus.

Features:
    - Operators toggle a template with UPDATE ... SET enabled = false WHERE template_key = '<key>'
    - TTL-cached Lookup: is_template_enabled() serves from an in-process cache refreshed per TTL.
    - Fail-open Semantics: a DB read failure or unknown template_key resolves to enabled=True.
    - Manual Invalidation: invalidate_cache() drops the cache immediately for config_reload.

Classes:
    No classes defined (module exposes cache-refresh and lookup functions)

Functions:
    _refresh_cache(): Reload the template_key -> enabled cache from the DB.
    _ensure_fresh(): Refresh the cache if the TTL has expired (lock-guarded).
    invalidate_cache(): Force the next is_template_enabled() call to re-read from the DB.
    is_template_enabled(): Return True if the given template is enabled (fail-open on error/unknown key).

Dependencies:
    - app.db.client: Async PostgreSQL pool (lazy import inside _refresh_cache())
    - app.core.config: settings.WA_TEMPLATE_CONFIG_CACHE_TTL_SECONDS
    - app.core.logging: Centralised logger

Updates:
    26-Jun-2026: Initial creation
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from app.core.logging import logger


_CACHE: Dict[str, bool] = {}
_CACHE_EXP: float = 0.0
_CACHE_TTL: int = 7200

try:
    from app.core.config import settings as _settings
    _CACHE_TTL = int(getattr(_settings, "WA_TEMPLATE_CONFIG_CACHE_TTL_SECONDS", 7200))
except Exception:
    logger.warning("WA template config: cache TTL setting unreadable; using %s", _CACHE_TTL, exc_info=True)
_CACHE_TTL = max(60, _CACHE_TTL)
_ERROR_RETRY_SECONDS: int = 15
_LOCK = asyncio.Lock()

_FALLBACK_ENABLED = True


async def _refresh_cache() -> None:
    """Reload template enabled flags from the DB, retrying after 15 seconds on failure."""
    global _CACHE, _CACHE_EXP
    try:
        from app.db.client import Database
        rows = await Database.fetch(
            "SELECT template_key, enabled "
            "FROM notifications.etown_whatsapp_template_config"
        )
        _CACHE = {str(r["template_key"]): bool(r["enabled"]) for r in rows}
        _CACHE_EXP = time.monotonic() + _CACHE_TTL
        logger.debug(
            "[WA_TEMPLATE_CFG] Cache refreshed: %d templates, TTL=%ds",
            len(_CACHE), _CACHE_TTL,
        )
    except Exception as exc:
        _CACHE_EXP = time.monotonic() + _ERROR_RETRY_SECONDS
        logger.error(
            "[WA_TEMPLATE_CFG] Failed to refresh cache — fail-open (all templates allowed): %s",
            exc,
         exc_info=True)


async def _ensure_fresh() -> None:
    """Refresh the template cache under a lock once its expiry has passed."""
    if time.monotonic() < _CACHE_EXP:
        return
    async with _LOCK:
        if time.monotonic() >= _CACHE_EXP:
            await _refresh_cache()


def invalidate_cache() -> None:
    """Expire the template cache so the next is_template_enabled call re-reads the DB."""
    global _CACHE_EXP
    _CACHE_EXP = 0.0


async def is_template_enabled(template_key: str) -> bool:
    """Return the template's cached enabled flag, defaulting to True when the key is not in the config table."""
    await _ensure_fresh()

    enabled = _CACHE.get(template_key)
    if enabled is None:
        logger.warning(
            "[WA_TEMPLATE_CFG] template_key=%r not found in config table — "
            "defaulting to enabled. Add a row to control it.",
            template_key,
        )
        return _FALLBACK_ENABLED

    return enabled
