"""Event Status Worker - moves stored community event statuses to upcoming, live or completed each midnight IST.

Background worker that runs refresh_event_statuses over public.etown_event once at startup and then every day at 00:00 IST, so the stored event_status the community feed returns follows each event's dates. It is a no-op unless EVENT_STATUS_REFRESH_ENABLED is on.

Features:
    - Day-based status in IST: upcoming before start_date, live through end_date, completed after it
    - Open-ended events: no end_date completes EVENT_OPEN_ENDED_HOURS after start_date and start_time
    - Runs at startup, then nightly at 00:00 IST
    - Completed and deleted events are never rewritten
    - Feature-flagged, and a no-op when the flag is off

Functions:
    _event_status_loop(): Refresh event statuses now, then at every 00:00 IST, until cancelled.
    start_event_status_worker(): Start the loop.
    stop_event_status_worker(): Cancel and await the loop.

Dependencies:
    - app.etown.mytown.mytown_utils.refresh_event_statuses: The one UPDATE that applies the status rule.
    - app.core.config.settings: EVENT_STATUS_REFRESH_ENABLED and EVENT_OPEN_ENDED_HOURS.
    - app.utils.ist_clock.IST: The timezone midnight is taken in.
    - app.core.logging: Centralised logger.

Updates:
    20-Oct-2026: Created. Refreshes etown_event.event_status at startup and nightly at 00:00 IST.
"""

import asyncio
from datetime import datetime, time, timedelta
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.etown.mytown.mytown_utils import refresh_event_statuses
from app.utils.ist_clock import IST

_event_status_task: Optional[asyncio.Task] = None


async def _event_status_loop() -> None:
    """Run refresh_event_statuses now and then at every 00:00 IST, until cancelled."""
    while True:
        try:
            count = await refresh_event_statuses()
            logger.info("Event status worker moved %d event(s) to a new status", count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Event status cycle failed: %s", exc, exc_info=True)
        now = datetime.now(IST)
        midnight = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=IST)
        await asyncio.sleep((midnight - now).total_seconds())


async def start_event_status_worker() -> None:
    """Start the event status loop task unless EVENT_STATUS_REFRESH_ENABLED is off."""
    global _event_status_task
    if not getattr(settings, "EVENT_STATUS_REFRESH_ENABLED", False):
        logger.info("Event status worker not started (EVENT_STATUS_REFRESH_ENABLED is off)")
        return
    _event_status_task = asyncio.create_task(_event_status_loop())
    logger.info(
        "Event status worker started (nightly at 00:00 IST, open-ended events close after %dh)",
        settings.EVENT_OPEN_ENDED_HOURS,
    )


async def stop_event_status_worker() -> None:
    """Cancel and await the event status worker task."""
    global _event_status_task
    if _event_status_task and not _event_status_task.done():
        _event_status_task.cancel()
        try:
            await _event_status_task
        except asyncio.CancelledError:
            logger.debug("event status worker cancelled during shutdown")
    _event_status_task = None
    logger.info("Event status worker stopped")
