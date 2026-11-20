"""Uninstall Detection Worker - daily silent push probe that finds uninstalled devices.

Background worker that sends a silent data-only FCM push to devices in etown_contactdevices on a schedule and treats FCM's UNREGISTERED response as an uninstall, recording it through the shared dead-token path in core/fcm.py. It is a no-op unless its feature flag is on.

Features:
    - The verdict comes from the FCM response, not delivery, so iOS needs no separate APNs path
    - Silent data-only push; no notification block, so nothing is shown to the user
    - Baseline mode for an environment's first sweep, so the backlog is not counted as churn
    - One probe per physical handset, not per profile mirror row carrying the same token
    - Never-probed devices served first, so a first run reaches the tail instead of recycling
    - Devices marked probed BEFORE the send, so a crash mid-cycle cannot cause a re-probe storm
    - Bounded concurrency and a per-cycle ceiling, so a large base cannot swamp FCM or the pool
    - Multi-worker safe: pg_try_advisory_lock means one sweep per tick across gunicorn workers
    - Feature-flagged off by default, and a no-op when the flag is off

Functions:
    _build_silent_payload(): Build a data-only FCM message that renders nothing
    probe_devices_once(): Run one sweep; returns how many devices were probed.
    _probe_loop(): Periodic loop; the advisory-lock winner sweeps each interval
    start_uninstall_detection_worker(): Start the loop.
    stop_uninstall_detection_worker(): Cancel and await the loop.

Dependencies:
    - app.core.config.settings: UNINSTALL_PROBE_ENABLED, UNINSTALL_DETECTION_ENABLED
    - app.db.client.Database: Shared asyncpg pool; holds the advisory lock for a sweep
    - app.etown.devices.uninstall_utils: Candidate selection and probe bookkeeping
    - app.etown.notifications.core.fcm: Shared FCM transport and OAuth minting
    - app.core.logging: Centralized logging
    - public.etown_contactdevices: The devices probed and where last_probed_at is stamped

Updates:
    15-Sep-2026: Created. Daily silent probe, advisory-locked, with a forced-sweep baseline mode.
"""

import asyncio
from datetime import timedelta
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.devices.uninstall_utils import fetch_probe_candidates, mark_devices_probed

_probe_task: Optional[asyncio.Task] = None

_PROBE_LOCK_KEY = 830915
UNINSTALL_PROBE_INTERVAL_SECONDS = 86400
UNINSTALL_PROBE_STALE_AFTER_HOURS = 20
UNINSTALL_PROBE_BATCH = 500
UNINSTALL_PROBE_MAX_PER_CYCLE = 20000
UNINSTALL_PROBE_CONCURRENCY = 10


def _build_silent_payload(token: str) -> Dict[str, Any]:
    """Return a data-only, background-priority FCM message for the token that shows nothing on either platform."""
    return {
        "message": {
            "token": token,
            "data": {"type": "silent_ping"},
            "android": {"priority": "normal"},
            "apns": {
                "headers": {"apns-push-type": "background", "apns-priority": "5"},
                "payload": {"aps": {"content-available": 1}},
            },
        }
    }


async def probe_devices_once(
    *,
    detection_source: str = "probe",
    full_sweep: bool = False,
) -> int:
    """Send silent FCM pings in batches to devices due a probe, up to the per-cycle maximum.

    Args:
        detection_source: Label recorded for dead tokens: 'probe' for the worker, 'baseline' for first sweeps.
        full_sweep: Probe every device regardless of when it was last probed.

    Returns:
        The number of devices probed.
    """
    from app.etown.notifications.core.fcm import get_fcm_oauth_token, send_fcm_message


    run_started_at = await Database.fetchval("SELECT NOW()")
    cutoff = (
        run_started_at if full_sweep
        else run_started_at - timedelta(hours=UNINSTALL_PROBE_STALE_AFTER_HOURS)
    )

    oauth_token = await get_fcm_oauth_token()
    semaphore = asyncio.Semaphore(UNINSTALL_PROBE_CONCURRENCY)
    probed = 0

    async def probe(token: str) -> None:
        async with semaphore:
            await send_fcm_message(
                oauth_token=oauth_token,
                payload=_build_silent_payload(token),
                device_token=token,
                invalidate_on_bad_token=True,
                timeout=15.0,
                label="Uninstall probe",
                detection_source=detection_source,
            )

    while probed < UNINSTALL_PROBE_MAX_PER_CYCLE:
        remaining = UNINSTALL_PROBE_MAX_PER_CYCLE - probed
        candidates = await fetch_probe_candidates(
            probed_before=cutoff,
            limit=min(UNINSTALL_PROBE_BATCH, remaining),
        )
        if not candidates:
            break

        tokens = [candidate["token"] for candidate in candidates]

        await mark_devices_probed(tokens)

        await asyncio.gather(*(probe(token) for token in tokens))
        probed += len(tokens)

    return probed


async def _probe_loop() -> None:
    """Run a probe sweep every interval on whichever worker wins the advisory lock; others skip the cycle."""
    while True:
        try:
            async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as lock_con:
                got = await lock_con.fetchval("SELECT pg_try_advisory_lock($1)", _PROBE_LOCK_KEY)
                if got:
                    try:
                        probed = await probe_devices_once()
                        if probed:
                            logger.info("Uninstall detection worker probed %d device(s)", probed)
                    finally:
                        try:
                            await lock_con.fetchval("SELECT pg_advisory_unlock($1)", _PROBE_LOCK_KEY)
                        except Exception:
                            logger.warning(
                                "Uninstall detection worker: failed to release the advisory lock",
                                exc_info=True,
                            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Uninstall detection worker cycle failed: %s", exc, exc_info=True)
        await asyncio.sleep(UNINSTALL_PROBE_INTERVAL_SECONDS)


async def start_uninstall_detection_worker() -> None:
    """Start the uninstall probe worker unless UNINSTALL_PROBE_ENABLED or UNINSTALL_DETECTION_ENABLED is off."""
    global _probe_task
    if not getattr(settings, "UNINSTALL_PROBE_ENABLED", False):
        logger.info("Uninstall detection worker not started (UNINSTALL_PROBE_ENABLED is off)")
        return
    if not getattr(settings, "UNINSTALL_DETECTION_ENABLED", False):
        logger.warning(
            "Uninstall detection worker not started: UNINSTALL_PROBE_ENABLED is on but "
            "UNINSTALL_DETECTION_ENABLED is off, so probing would spend sends and record nothing"
        )
        return
    _probe_task = asyncio.create_task(_probe_loop())
    logger.info(
        "Uninstall detection worker started (interval: %ds, batch: %d, max/cycle: %d)",
        UNINSTALL_PROBE_INTERVAL_SECONDS, UNINSTALL_PROBE_BATCH, UNINSTALL_PROBE_MAX_PER_CYCLE,
    )


async def stop_uninstall_detection_worker() -> None:
    """Cancel and await the uninstall probe worker task."""
    global _probe_task
    if _probe_task and not _probe_task.done():
        _probe_task.cancel()
        try:
            await _probe_task
        except asyncio.CancelledError:
            logger.debug("uninstall detection worker cancelled during shutdown")
    _probe_task = None
    logger.info("Uninstall detection worker stopped")
