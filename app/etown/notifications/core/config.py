"""Notification Config - runtime overlay of env and database switches on the type registry.

Notification-core configuration layered over registry.py defaults: env kill switches and per-type channel overrides, plus a cached notifications.etown_notification_config table for per-type enable state, rollout percentage and shadow mode, all changeable without a redeploy.

Features:
    - Env kill switches: NOTIFICATIONS_DISABLED_TYPES and PUSH/WHATSAPP/SMS/INAPP_ENABLED
    - Per-type channel override: NOTIFICATIONS_CHANNELS_<TYPEKEY> env var (e.g. "push,inapp")
    - Channel resolution precedence: request.channels > env override > registry default
    - DB-backed config cache: 2-hour TTL over etown_notification_config; db_invalidate() forces a reload
    - Deterministic rollout: sticky per-user hash gate via in_rollout()
    - Shadow mode: is_shadow_mode() lets adapters log a would-be send without delivering

Classes:
    No classes defined (module exposes overlay functions and a module-level DB cache)

Functions:
    _csv(): Parse a comma-separated env value into a lowercased list
    is_server_mint_enabled(): Whether the push path may mint its own FCM OAuth token
    is_shadow_mode(): Dispatcher-wide dry-run flag (log, don't deliver)
    is_type_enabled(): Registry default AND not disabled via NOTIFICATIONS_DISABLED_TYPES
    is_channel_enabled(): Per-channel kill switch (PUSH/WHATSAPP/SMS/INAPP_ENABLED)
    _type_channel_override(): Read NOTIFICATIONS_CHANNELS_<TYPE> and parse it into Channel enums
    resolve_channels(): Effective channel list for a request, filtered by kill switches
    _load_db_config(): Fetch and normalise all rows from etown_notification_config
    _refresh_db_cache(): Reload the DB cache and reset its TTL expiry, retrying sooner on error
    _ensure_db_fresh(): Refresh the DB cache under a lock if its TTL has expired
    db_invalidate(): Drop the DB config cache so the next read re-queries
    db_all(): All DB config rows, refreshing the cache first if expired
    db_get(): DB config row for one notification_type, or None if missing/disabled
    in_rollout(): Deterministic, sticky per-user rollout_percent check

Dependencies:
    - app.core.config.settings: env-based feature flags read via getattr with defaults
    - app.db.client.Database: fetch() for the notifications.etown_notification_config cache
    - app.etown.notifications.core.models: Channel, NotificationRequest
    - app.etown.notifications.core.registry: TypeConfig
    - app.core.logging: cache refresh failure logging

Updates:
    03-Jun-2026: Module created for the notification center's env-based type/channel overrides.
    27-Jul-2026: Added the DB-backed per-type config cache (db_get/db_all) and rollout_percent gating.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from app.core.config import settings
from app.core.logging import logger
from app.etown.notifications.core.models import Channel, NotificationRequest
from app.etown.notifications.core.registry import TypeConfig


def _csv(value: Optional[str]) -> List[str]:
    """Split a comma-separated string into lowercase, trimmed, non-empty parts."""
    if not value:
        return []
    return [part.strip().lower() for part in str(value).split(",") if part.strip()]


def is_server_mint_enabled() -> bool:
    """Return whether push may mint an FCM OAuth token server-side when the caller sent none; defaults to True."""
    return bool(getattr(settings, "FCM_SERVER_MINT_ENABLED", True))


def is_shadow_mode() -> bool:
    """Return whether dispatcher adapters should log what they would send instead of delivering; defaults to False."""
    return bool(getattr(settings, "NOTIFICATIONS_SHADOW_MODE", False))


def is_type_enabled(type_key: str, cfg: TypeConfig) -> bool:
    """Return True when the type is enabled by default and not listed in NOTIFICATIONS_DISABLED_TYPES."""
    if type_key in _csv(getattr(settings, "NOTIFICATIONS_DISABLED_TYPES", None)):
        return False
    return cfg.enabled_by_default


def is_channel_enabled(channel: Channel) -> bool:
    """Return the channel's kill-switch setting, defaulting to enabled."""
    flag = {
        Channel.PUSH: "PUSH_ENABLED",
        Channel.WHATSAPP: "WHATSAPP_ENABLED",
        Channel.SMS: "SMS_ENABLED",
        Channel.INAPP: "INAPP_ENABLED",
    }[channel]
    return bool(getattr(settings, flag, True))


def _type_channel_override(type_key: str) -> Optional[List[Channel]]:
    """Return channels from NOTIFICATIONS_CHANNELS_<TYPE>, skipping unknown names, or None when unset or empty."""
    raw = os.getenv(f"NOTIFICATIONS_CHANNELS_{type_key.upper()}")
    names = _csv(raw)
    if not names:
        return None
    out: List[Channel] = []
    for name in names:
        try:
            out.append(Channel(name))
        except ValueError:
            logger.warning(
                "Ignoring unknown channel %r in NOTIFICATIONS_CHANNELS_%s",
                name, type_key.upper(),
            )
    return out or None


def resolve_channels(request: NotificationRequest, cfg: TypeConfig) -> List[Channel]:
    """Return the request's channels, else the env override, else registry defaults, filtered by channel kill switches."""
    if request.channels:
        chosen = list(request.channels)
    else:
        chosen = _type_channel_override(cfg.key) or list(cfg.default_channels)
    return [c for c in chosen if is_channel_enabled(c)]


import asyncio
import time as _time
from typing import Any, Dict

_DB_CACHE: Dict[str, Dict[str, Any]] = {}
_DB_CACHE_EXP: float = 0.0
_DB_CACHE_TTL = max(60, int(os.getenv("NOTIFICATIONS_CONFIG_TTL_SECONDS", "7200")))
_DB_ERROR_RETRY_SECONDS = 15
_DB_LOCK = asyncio.Lock()


async def _load_db_config() -> Dict[str, Dict[str, Any]]:
    """Load every notification config row keyed by notification_type, with its config JSON decoded."""
    from app.db.client import Database
    rows = await Database.fetch(
        "SELECT * FROM notifications.etown_notification_config WHERE true"
    )
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cfg = dict(r)
        raw = cfg.get("config")
        if isinstance(raw, (str, bytes)):
            try:
                cfg["config"] = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                logger.warning("notification config: config column is not valid JSON; using an empty config", exc_info=True)
                cfg["config"] = {}
        elif raw is None:
            cfg["config"] = {}
        out[cfg["notification_type"]] = cfg
    return out


async def _refresh_db_cache() -> None:
    """Reload the config cache and reset its TTL, retrying after 15 seconds on error."""
    global _DB_CACHE, _DB_CACHE_EXP
    try:
        _DB_CACHE = await _load_db_config()
        _DB_CACHE_EXP = _time.monotonic() + _DB_CACHE_TTL
    except Exception as exc:
        _DB_CACHE_EXP = _time.monotonic() + _DB_ERROR_RETRY_SECONDS
        logger.error("notif db_config refresh failed: %s", exc, exc_info=True)

async def _ensure_db_fresh() -> None:
    """Refresh the config cache under a lock when its TTL has expired."""
    if _time.monotonic() < _DB_CACHE_EXP:
        return
    async with _DB_LOCK:
        if _time.monotonic() >= _DB_CACHE_EXP:
            await _refresh_db_cache()

def db_invalidate() -> None:
    """Expire the config cache so the next read re-queries the database."""
    global _DB_CACHE_EXP
    _DB_CACHE_EXP = 0.0

async def db_all() -> Dict[str, Dict[str, Any]]:
    """Return a copy of all cached config rows, including disabled ones, refreshing first if expired."""
    await _ensure_db_fresh()
    return dict(_DB_CACHE)

async def db_get(notification_type: str) -> Optional[Dict[str, Any]]:
    """Return the cached config row for a notification type, or None when it is missing or disabled."""
    await _ensure_db_fresh()
    cfg = _DB_CACHE.get(notification_type)
    if not cfg or not cfg.get("enabled"):
        return None
    return cfg


def in_rollout(cfg: Dict[str, Any], userprofileid: str) -> bool:
    """Return whether a user falls inside the row's rollout_percent, bucketing by Python hash of type and userprofileid."""
    pct = int(cfg.get("rollout_percent", 100))
    if pct >= 100:
        return True
    if pct <= 0:
        return False
    bucket = (hash((cfg["notification_type"], userprofileid)) % 100 + 100) % 100
    return bucket < pct
