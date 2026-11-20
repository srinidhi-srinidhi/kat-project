"""Notification Config Reload - cross-worker invalidation of the DB-backed notification config caches.

Background listener service behind GET /api/notifications/config/reload: publishes a pg_notify on RELOAD_CHANNEL and, on every worker, drops the TTL caches for etown_job_config, etown_notification_config, etown_notification_global_caps, etown_whatsapp_template_config and etown_booking_alert_sms_recipients so the next read re-queries.

Features:
    - Idempotent drops: invalidation only resets TTL expiry, so a missed NOTIFY converges at the normal TTL
    - Fan-out invalidation: one pg_notify on RELOAD_CHANNEL drops every worker's cache at once
    - Dedicated LISTEN connection per worker: reconnects with backoff and sends keepalives
    - Independent per-cache invalidation: one cache failing to drop does not block the others
    - Opt-out: NOTIFICATIONS_CONFIG_RELOAD_LISTENER_ENABLED=false degrades to single-worker refresh

Classes:
    No classes defined (module exposes listener lifecycle functions and module state)

Endpoints:
    GET /api/notifications/config/reload

Functions:
    _listener_enabled(): Whether NOTIFICATIONS_CONFIG_RELOAD_LISTENER_ENABLED permits starting the listener
    invalidate_local(): Drop every DB-backed notification config cache in this process
    _on_notify(): asyncpg LISTEN callback that drops the local caches on a NOTIFY
    publish_reload(): Broadcast a config reload to every listening worker via pg_notify
    _listener_loop(): Hold a dedicated LISTEN connection open, reconnecting with backoff
    start_config_reload_listener(): Start the reload listener task (once, from the lifespan)
    stop_config_reload_listener(): Cancel and await the reload listener task
    is_listening(): Whether this process currently has a live listener task

Dependencies:
    app.db.client.Database (pool + open_listener_connection)
    app.core.logging (logger)
    app.etown.notifications.core.job_config, .config (imported lazily)
    app.etown.notifications.rules, .whatsapp_template_config, .booking_alert_recipients (imported lazily)

Updates:
    27-Jul-2026: Created alongside the 2 h config TTLs.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    28-Sep-2026: Reload also drops the booking alert SMS recipient cache.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from app.core.logging import logger
from app.db.client import Database

RELOAD_CHANNEL = "etown_config_reload"

_KEEPALIVE_SECONDS = max(30, int(os.getenv("NOTIFICATIONS_CONFIG_RELOAD_KEEPALIVE_SECONDS", "900")))

_listener_task: Optional[asyncio.Task] = None


def _listener_enabled() -> bool:
    """Return False only when NOTIFICATIONS_CONFIG_RELOAD_LISTENER_ENABLED is false, 0 or no."""
    raw = os.getenv("NOTIFICATIONS_CONFIG_RELOAD_LISTENER_ENABLED", "true")
    return str(raw).strip().lower() not in {"false", "0", "no"}


def invalidate_local() -> str:
    """Invalidate each DB-backed notification config cache in this process independently.

    Returns:
        A comma-joined summary of the caches actually dropped, for logging.
    """
    dropped: list = []

    def _try(label: str, fn) -> None:
        try:
            fn()
            dropped.append(label)
        except Exception as exc:
            logger.error("config reload: %s invalidation failed: %s", label, exc, exc_info=True)

    from app.etown.notifications import booking_alert_recipients as booking_rcpt
    from app.etown.notifications import rules
    from app.etown.notifications import whatsapp_template_config as wa_cfg
    from app.etown.notifications.core import config as ncfg
    from app.etown.notifications.core import job_config as jcfg

    _try("job_config", jcfg.invalidate)
    _try("notification_config", ncfg.db_invalidate)
    _try("global_caps", rules.invalidate_caps_cache)
    _try("whatsapp_template_config", wa_cfg.invalidate_cache)
    _try("booking_alert_recipients", booking_rcpt.invalidate_cache)
    return ", ".join(dropped) if dropped else "nothing"


def _on_notify(connection, pid: int, channel: str, payload: str) -> None:
    """Drop the local config caches when a reload NOTIFY arrives on the LISTEN connection."""
    try:
        dropped = invalidate_local()
        logger.info(
            "config reload NOTIFY received (source=%s): dropped %s", payload or "?", dropped
        )
    except Exception as exc:
        logger.error("config reload invalidation failed: %s", exc, exc_info=True)


async def publish_reload(source: str = "api") -> bool:
    """Broadcast a config reload to every listening worker via pg_notify.

    Args:
        source: Informational payload identifying who triggered the reload.

    Returns:
        True if the NOTIFY was sent, False if it failed.
    """
    try:
        await Database.execute("SELECT pg_notify($1, $2)", RELOAD_CHANNEL, source)
        return True
    except Exception as exc:
        logger.error("config reload NOTIFY failed: %s", exc, exc_info=True)
        return False


async def _listener_loop() -> None:
    """Hold a dedicated LISTEN connection open with periodic keepalives, reconnecting with exponential backoff."""
    backoff = 1
    while True:
        conn = None
        try:
            conn = await Database.open_listener_connection()
            await conn.add_listener(RELOAD_CHANNEL, _on_notify)
            logger.info("config reload listener attached to '%s'", RELOAD_CHANNEL)
            backoff = 1
            invalidate_local()
            while not conn.is_closed():
                await asyncio.sleep(_KEEPALIVE_SECONDS)
                await conn.execute("SELECT 1")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "config reload listener down, reconnecting in %ss: %s", backoff, exc
            )
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    logger.debug("config reload listener: failed to close the listener connection", exc_info=True)
        await asyncio.sleep(backoff)
        backoff = min(30, backoff * 2)


async def start_config_reload_listener() -> None:
    """Start the reload listener task unless it is disabled or already running."""
    global _listener_task
    if not _listener_enabled():
        logger.info(
            "config reload listener disabled "
            "(NOTIFICATIONS_CONFIG_RELOAD_LISTENER_ENABLED=false) — "
            "the reload endpoint will only refresh the worker that serves it"
        )
        return
    if _listener_task is not None and not _listener_task.done():
        return
    _listener_task = asyncio.create_task(_listener_loop())


async def stop_config_reload_listener() -> None:
    """Cancel and await the reload listener task."""
    global _listener_task
    if _listener_task and not _listener_task.done():
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            logger.debug("notification config reload listener cancelled during shutdown")
    _listener_task = None
    logger.info("config reload listener stopped")


def is_listening() -> bool:
    """Return whether this process currently has a live listener task."""
    return _listener_task is not None and not _listener_task.done()
