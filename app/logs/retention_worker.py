"""Log Retention Worker - periodically prunes old log rows and provider search appearances.

Background worker started from the app lifespan when LOG_RETENTION_SWEEP_ENABLED is on: each sweep prunes etown_application_logs, etown_terminal_logs and etown_provider_search_appearances past their configured retention windows, isolating each prune's failure.

Features:
    - Periodic sweep: prunes both log tables past their configured retention window.
    - Search appearances: prunes etown_provider_search_appearances past SEARCH_APPEARANCE_RETENTION_DAYS.
    - Feature-flagged: LOG_RETENTION_SWEEP_ENABLED; cadence via LOG_RETENTION_SWEEP_INTERVAL_HOURS.
    - Reuses existing cleanup SQL (no duplication) and settings-driven retention days.
    - Independent failures: each prune has its own try/except, so one failure cannot skip the rest.
    - Graceful shutdown: the sweep loop cancels promptly on app shutdown.

Classes:
    No classes defined (module exposes the background sweep loop and its start/stop controls)

Functions:
    _run_sweep(): Prune application logs, terminal logs and search appearances past retention
    _worker_loop(): Wait out the startup grace period, then repeat _run_sweep() every interval until shutdown
    start_log_retention_worker(): Start the background sweep loop (no-op if disabled/running).
    stop_log_retention_worker(): Signal and await graceful shutdown of the loop.

Dependencies:
    - app.core.config: retention days, enable flag, sweep interval
    - app.db.client: Database.execute for the search-appearance prune
    - app.logs.storage: LogStorageService.cleanup_old_logs (application logs)
    - app.logs.terminal_storage: TerminalLogStorageService.cleanup_old_terminal_logs (terminal logs)
    - asyncio: background task management

Updates:
    09-Jul-2026: Initial creation — schedules the previously-unwired log cleanup to run periodically.
    26-Aug-2026: Logged the sweep stop timeout instead of cancelling silently.
    17-Sep-2026: Sweep also prunes etown_provider_search_appearances.
"""

import asyncio
import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("app.logs.retention_worker")

_worker_task: Optional[asyncio.Task] = None
_shutdown_event: Optional[asyncio.Event] = None

_STARTUP_GRACE_SECONDS = 60


async def _run_sweep() -> None:
    """Prune old application logs, terminal logs and provider search appearances, logging each failure."""
    from app.db.client import Database
    from app.logs.storage import LogStorageService
    from app.logs.terminal_storage import TerminalLogStorageService

    try:
        await LogStorageService().cleanup_old_logs()
    except Exception as e:
        logger.error(f"Application-log retention sweep failed: {e}", exc_info=True)

    try:
        await TerminalLogStorageService().cleanup_old_terminal_logs()
    except Exception as e:
        logger.error(f"Terminal-log retention sweep failed: {e}", exc_info=True)

    try:
        deleted = await Database.execute(
            """
            DELETE FROM etown_provider_search_appearances
            WHERE search_date < (now() AT TIME ZONE 'Asia/Kolkata')::date - $1::int
            """,
            settings.SEARCH_APPEARANCE_RETENTION_DAYS,
        )
        logger.info(
            "Search-appearance retention sweep complete (retention=%s days): %s",
            settings.SEARCH_APPEARANCE_RETENTION_DAYS,
            deleted,
        )
    except Exception as e:
        logger.error(f"Search-appearance retention sweep failed: {e}", exc_info=True)


async def _worker_loop(interval_seconds: float) -> None:
    """Wait out the startup grace, then sweep every interval until shutdown is signalled."""
    assert _shutdown_event is not None
    try:
        await asyncio.wait_for(_shutdown_event.wait(), timeout=_STARTUP_GRACE_SECONDS)
        return
    except asyncio.TimeoutError:
        pass

    while not _shutdown_event.is_set():
        await _run_sweep()
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue


async def start_log_retention_worker() -> None:
    """Start the log-retention sweep task unless the flag is off or it is already running."""
    global _worker_task, _shutdown_event

    if not settings.LOG_RETENTION_SWEEP_ENABLED:
        logger.info("Log-retention sweep disabled (LOG_RETENTION_SWEEP_ENABLED=false)")
        return

    if _worker_task is not None and not _worker_task.done():
        return

    _shutdown_event = asyncio.Event()
    interval_seconds = max(1, settings.LOG_RETENTION_SWEEP_INTERVAL_HOURS) * 3600
    _worker_task = asyncio.create_task(_worker_loop(interval_seconds))
    logger.info(
        "Log-retention sweep started (app=%dd, terminal=%dd, every %dh)",
        settings.DB_LOG_RETENTION_DAYS,
        settings.TERMINAL_LOG_RETENTION_DAYS,
        settings.LOG_RETENTION_SWEEP_INTERVAL_HOURS,
    )


async def stop_log_retention_worker() -> None:
    """Signal shutdown and wait up to 10 seconds for the sweep loop, cancelling it if it lingers."""
    global _worker_task, _shutdown_event

    if _shutdown_event is not None:
        _shutdown_event.set()

    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("log-retention sweep did not stop within 10s; cancelling it")
            _worker_task.cancel()
        except Exception as e:
            logger.debug(f"Log-retention worker stop error: {e}")

    _worker_task = None
    _shutdown_event = None
