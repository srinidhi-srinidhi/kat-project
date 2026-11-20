"""Notification Queue Runner Worker - event-driven, priority-lane drain loop for the notification queue.

Background worker started from the app lifespan that wakes on LISTEN/NOTIFY or a safety poll, claims queue items lane by lane, sends each through the notification governance gate and acks or nacks it, while a reaper requeues stale claims. It is inert unless NOTIFICATIONS_QUEUE_ENABLED is true.

Features:
    - Event-driven Drain: a LISTEN connection wakes the drain loop, backed by a safety-interval poll.
    - Priority Lanes: critical/transactional/social drain fully per pass; marketing is token-bucket capped.
    - Stale-lock Reaper: requeues abandoned 'processing' items and warns on depth/age thresholds.
    - Governance Handoff: each claimed item runs through ``service.send()``, then is acked or nacked.
    - Multi-worker Safe: claims use FOR UPDATE SKIP LOCKED, so every gunicorn worker can run the loop.

Classes:
    No classes defined (module exposes the runner's async loop functions)

Functions:
    _dispatch_item(): Run one queue item through governance, then ack or nack it.
    _drain_once(): One pass over all lanes in priority order, claiming and dispatching.
    _on_notify(): asyncpg LISTEN callback that wakes the drain loop.
    _listener_loop(): Hold a dedicated LISTEN connection open, reconnecting with backoff.
    _runner_loop(): Main loop — waits for a wake signal or the safety-poll timeout, then drains.
    _check_thresholds(): Emit a WARNING when a lane's depth or oldest-item age breaches its alert threshold.
    _reaper_loop(): Periodically reap stale 'processing' rows and check alert thresholds.
    start_queue_runner(): Start the listener + drain + reaper loops if enabled (call once from lifespan).
    stop_queue_runner(): Cancel and await the listener + runner + reaper loops.

Dependencies:
    - app.etown.notifications.queue.backend: NOTIFY_CHANNEL, QueueItem, backend, deserialize_request, ensure_queue_table
    - app.etown.notifications.service: send() — the unchanged governance gate each item is dispatched through
    - app.etown.notifications.core.registry: QUEUES — the lane names drained each pass
    - app.db.client: Database.open_listener_connection() for the dedicated LISTEN connection
    - app.core.config: settings — concurrency, marketing rate, safety-poll and alert thresholds
    - app.core.logging: Centralised logging infrastructure

Updates:
    11-Jun-2026: Module created with polling runner and reaper loops.
    09-Jul-2026: Added the LISTEN/NOTIFY listener loop (_listener_loop / _on_notify).
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.notifications import service
from app.etown.notifications.core.registry import QUEUES
from app.etown.notifications.queue.backend import (
    NOTIFY_CHANNEL,
    QueueItem,
    backend,
    deserialize_request,
    ensure_queue_table,
)

_BATCH = {
    "critical": 64,
    "transactional": 32,
    "social": 16,
    "marketing": 8,
}

_runner_task: Optional[asyncio.Task] = None
_reaper_task: Optional[asyncio.Task] = None
_listener_task: Optional[asyncio.Task] = None
_wake_event: asyncio.Event = asyncio.Event()
_HIGH_PRIORITY_LANES = ("critical", "transactional", "social")


async def _dispatch_item(item: QueueItem, sem: asyncio.Semaphore) -> None:
    """Send one queue item through the governance gate, acking sent or suppressed and nacking failures or errors."""
    async with sem:
        try:
            req = deserialize_request(item.payload)
            outcome = await service.send(req)
            if outcome == "failed":
                logger.warning(
                    "queue delivery failed, will retry (type=%s id=%s attempt=%d)",
                    item.notification_type, item.id, item.attempts,
                )
                await backend.nack(item, "delivery failed")
            else:
                await backend.ack(item.id)
        except Exception as exc:
            logger.error(
                "queue dispatch error (type=%s id=%s attempt=%d): %s",
                item.notification_type, item.id, item.attempts, exc, exc_info=True,
            )
            try:
                await backend.nack(item, str(exc))
            except Exception as nack_exc:
                logger.error("queue nack failed (id=%s): %s", item.id, nack_exc, exc_info=True)


async def _drain_once(sem: asyncio.Semaphore, marketing_budget: int) -> Dict[str, int]:
    """Claim and dispatch one batch from each unpaused lane in priority order and return counts per lane.

    Args:
        sem: Concurrency limiter shared across dispatched items.
        marketing_budget: Max marketing items to claim this pass.
    """
    counts: Dict[str, int] = {}
    tasks = []
    for queue in QUEUES:
        if await backend.is_paused(queue):
            continue
        limit = _BATCH.get(queue, 8)
        if queue == "marketing":
            limit = min(limit, marketing_budget)
        if limit <= 0:
            continue
        items = await backend.claim(queue, limit)
        counts[queue] = len(items)
        for item in items:
            tasks.append(asyncio.create_task(_dispatch_item(item, sem)))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return counts


def _on_notify(connection, pid: int, channel: str, payload: str) -> None:
    """Wake the drain loop when a queue NOTIFY arrives."""
    _wake_event.set()


async def _listener_loop() -> None:
    """Hold a LISTEN connection with keepalives that wakes the drain loop, reconnecting with capped backoff."""
    backoff = 1
    while True:
        conn = None
        try:
            conn = await Database.open_listener_connection()
            await conn.add_listener(NOTIFY_CHANNEL, _on_notify)
            logger.info("queue runner listening for NOTIFY on '%s'", NOTIFY_CHANNEL)
            backoff = 1
            _wake_event.set()
            keepalive = max(30, int(getattr(
                settings, "NOTIFICATIONS_QUEUE_LISTENER_KEEPALIVE_SECONDS", 300)))
            while not conn.is_closed():
                await asyncio.sleep(keepalive)
                await conn.execute("SELECT 1")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("queue NOTIFY listener down, reconnecting in %ss: %s", backoff, exc)
            _wake_event.set()
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    logger.debug("queue NOTIFY listener: failed to close the listener connection", exc_info=True)
        await asyncio.sleep(backoff)
        backoff = min(30, backoff * 2)


async def _runner_loop() -> None:
    """Drain the lanes on each wake or safety poll, rate-limiting marketing with a token bucket."""
    concurrency = max(1, int(getattr(settings, "NOTIFICATIONS_QUEUE_CONCURRENCY", 20)))
    marketing_rate = max(0, int(getattr(settings, "NOTIFICATIONS_QUEUE_MARKETING_RATE", 50)))
    safety = max(1, int(getattr(settings, "NOTIFICATIONS_QUEUE_SAFETY_POLL_SECONDS", 15)))
    sem = asyncio.Semaphore(concurrency)
    mkt_cap = float(marketing_rate * safety)
    mkt_tokens = float(marketing_rate)
    mkt_last = time.monotonic()

    async def _drain_backlog() -> None:
        nonlocal mkt_tokens, mkt_last
        while True:
            now = time.monotonic()
            mkt_tokens = min(mkt_cap, mkt_tokens + marketing_rate * (now - mkt_last))
            mkt_last = now
            mkt_limit = min(_BATCH.get("marketing", 8), int(mkt_tokens))
            counts = await _drain_once(sem, marketing_budget=mkt_limit)
            mkt_tokens = max(0.0, mkt_tokens - counts.get("marketing", 0))
            hp_full = any(counts.get(q, 0) >= _BATCH.get(q, 8) for q in _HIGH_PRIORITY_LANES)
            mkt_full = mkt_limit > 0 and counts.get("marketing", 0) >= mkt_limit
            if not (hp_full or mkt_full):
                return
    woken_by_notify = True
    while True:
        try:
            _wake_event.clear()
            if woken_by_notify or await backend.any_claimable():
                await _drain_backlog()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("queue runner loop error: %s", exc, exc_info=True)
        try:
            await asyncio.wait_for(_wake_event.wait(), timeout=safety)
            woken_by_notify = True
        except asyncio.TimeoutError:
            woken_by_notify = False


async def _check_thresholds() -> None:
    """Log a warning for each lane whose pending depth or oldest-item age breaches its alert threshold."""
    depth_alert = int(getattr(settings, "NOTIFICATIONS_QUEUE_ALERT_DEPTH", 1000))
    age_alert = int(getattr(settings, "NOTIFICATIONS_QUEUE_ALERT_AGE_S", 600))
    stats = await backend.stats()
    for queue, s in stats.items():
        if s["pending"] >= depth_alert or s["oldest_pending_age_s"] >= age_alert:
            logger.warning(
                "queue ALERT lane=%s pending=%d oldest_age_s=%d dead=%d "
                "(thresholds depth=%d age_s=%d)",
                queue, s["pending"], s["oldest_pending_age_s"], s["dead"],
                depth_alert, age_alert,
            )


async def _reaper_loop() -> None:
    """Requeue stale claimed items periodically and check alert thresholds every few passes."""
    visibility = max(30, int(getattr(settings, "NOTIFICATIONS_QUEUE_VISIBILITY_TIMEOUT", 120)))
    interval = max(15, visibility // 2)
    alert_every = max(1, int(getattr(settings, "NOTIFICATIONS_QUEUE_ALERT_EVERY_N_REAPS", 5)))
    passes = 0
    while True:
        try:
            n = await backend.reap_stale(visibility)
            if n:
                logger.warning("queue reaper requeued %d stale item(s)", n)
            passes += 1
            if passes % alert_every == 0:
                await _check_thresholds()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("queue reaper error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


async def start_queue_runner() -> None:
    """Ensure the queue table and start the listener, runner and reaper loops when the queue is enabled."""
    global _runner_task, _reaper_task, _listener_task
    if not bool(getattr(settings, "NOTIFICATIONS_QUEUE_ENABLED", False)):
        logger.info("Notification queue disabled (NOTIFICATIONS_QUEUE_ENABLED=false)")
        return
    try:
        await ensure_queue_table()
    except Exception as exc:
        logger.error("Notification queue: failed to ensure queue table: %s", exc, exc_info=True)
        return
    _listener_task = asyncio.create_task(_listener_loop())
    _runner_task = asyncio.create_task(_runner_loop())
    _reaper_task = asyncio.create_task(_reaper_loop())
    logger.info(
        "Notification queue runner started (event-driven; lanes=%s, safety_poll=%ss)",
        ",".join(QUEUES), getattr(settings, "NOTIFICATIONS_QUEUE_SAFETY_POLL_SECONDS", 15),
    )


async def stop_queue_runner() -> None:
    """Cancel and await the listener, runner and reaper loops."""
    global _runner_task, _reaper_task, _listener_task
    for task in (_runner_task, _reaper_task, _listener_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug("notification queue runner cancelled during shutdown")
    _runner_task = None
    _reaper_task = None
    _listener_task = None
    logger.info("Notification queue runner stopped")
