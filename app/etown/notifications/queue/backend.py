"""Notification Queue Backend - abstract queue interface and its Postgres implementation.

Notification-layer queue used by producers and the queue runner: PostgresQueueBackend stores serialized GovernanceRequest payloads in notifications.etown_notification_queue, claims with FOR UPDATE SKIP LOCKED, retries with backoff before dead-lettering, and dedupes enqueues on dedupe_key.

Features:
    - Contention-free Claims: claim() uses ``FOR UPDATE SKIP LOCKED`` so no row is claimed twice.
    - Backoff + Dead-lettering: nack() retries with capped exponential backoff, then dead-letters.
    - Stale-lock Reaping: reap_stale() requeues rows stuck in 'processing' by a crashed worker.
    - Pause/Resume per Lane: is_paused()/set_paused() halt one lane, cached with a short TTL.
    - Producer Idempotency: enqueue() no-ops on a duplicate ``dedupe_key`` and NOTIFYs the lane.
    - Claimable Probe: any_claimable() reports whether any lane has due work.
    - Self-healing Schema: ensure_queue_table() creates the queue table and indexes if absent.

Classes:
    QueueItem(): Dataclass for one claimed queue row, ready to dispatch.
    QueueBackend(): Abstract backend interface (enqueue/claim/ack/nack/reap/pause/stats).
    PostgresQueueBackend(): ``FOR UPDATE SKIP LOCKED``-backed implementation of QueueBackend on Postgres.

Functions:
    _notify(): Best-effort pg_notify so listening runners wake on new claimable work.
    serialize_request(): Flatten a GovernanceRequest into a JSON-safe dict for the queue payload.
    deserialize_request(): Rebuild a GovernanceRequest from a queue payload.
    _backoff_seconds(): Exponential backoff (capped) for the next retry attempt.
    ensure_queue_table(): Create the queue table + indexes if absent (idempotent safety net).

Dependencies:
    - app.db.client: Async PostgreSQL client backing PostgresQueueBackend
    - app.etown.notifications.service: GovernanceRequest shape (TYPE_CHECKING and lazy import)
    - app.core.logging: Centralised logging infrastructure

Updates:
    11-Jun-2026: Module created with the QueueBackend interface and PostgresQueueBackend.
    09-Jul-2026: Added pg_notify-based wake-up (_notify) and the any_claimable() probe.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.core.logging import logger
from app.db.client import Database

if TYPE_CHECKING:
    from app.etown.notifications.service import GovernanceRequest

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600

NOTIFY_CHANNEL = "etown_notif_queue"


async def _notify(queue: str) -> None:
    """Send a pg_notify on the queue channel with the lane name, logging and swallowing any error."""
    try:
        await Database.execute("SELECT pg_notify($1, $2)", NOTIFY_CHANNEL, queue)
    except Exception as exc:
        logger.warning("queue notify failed (queue=%s): %s", queue, exc)


@dataclass
class QueueItem:
    """A claimed queue row, ready to dispatch."""

    id: str
    queue: str
    notification_type: str
    priority: int
    payload: Dict[str, Any]
    attempts: int
    dedupe_key: Optional[str] = None


def serialize_request(req: "GovernanceRequest") -> Dict[str, Any]:
    """Flatten a GovernanceRequest into a JSON-safe dict for the queue payload."""
    return {
        "userprofileid": req.userprofileid,
        "notification_type": req.notification_type,
        "contactid": req.contactid,
        "sender_businessid": req.sender_businessid,
        "mobile_number": req.mobile_number,
        "ctx": req.ctx,
        "dedupe_key": req.dedupe_key,
        "feature": req.feature,
        "oauth_token": req.oauth_token,
        "channel": req.channel,
        "priority": req.priority,
    }


def deserialize_request(payload: Dict[str, Any]) -> "GovernanceRequest":
    """Rebuild a GovernanceRequest from a queue payload."""
    from app.etown.notifications.service import GovernanceRequest

    return GovernanceRequest(
        userprofileid=payload["userprofileid"],
        notification_type=payload["notification_type"],
        contactid=payload.get("contactid"),
        sender_businessid=payload.get("sender_businessid"),
        mobile_number=payload.get("mobile_number"),
        ctx=payload.get("ctx") or {},
        dedupe_key=payload.get("dedupe_key"),
        feature=payload.get("feature"),
        oauth_token=payload.get("oauth_token"),
        channel=payload.get("channel"),
        priority=int(payload.get("priority", 50)),
    )


def _backoff_seconds(attempts: int) -> int:
    """Return the capped exponential backoff in seconds before the next attempt after the given number of tries."""
    exp = max(0, attempts - 1)
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** exp))


class QueueBackend(ABC):
    @abstractmethod
    async def enqueue(
        self,
        queue: str,
        req: "GovernanceRequest",
        *,
        priority: int,
        dedupe_key: Optional[str],
        run_after: Optional[datetime] = None,
    ) -> bool:
        """Insert a queued item and return False if it was deduped."""

    @abstractmethod
    async def claim(self, queue: str, limit: int) -> List[QueueItem]:
        """Atomically claim up to limit ready items from a lane."""

    @abstractmethod
    async def ack(self, item_id: str) -> None:
        """Mark an item delivered."""

    @abstractmethod
    async def nack(self, item: QueueItem, error: str) -> None:
        """Reschedule an item with backoff, or dead-letter it once attempts are exhausted."""

    @abstractmethod
    async def reap_stale(self, visibility_timeout_s: int) -> int:
        """Requeue items abandoned in processing by a crashed worker and return the count."""

    @abstractmethod
    async def depth(self, queue: str) -> int:
        """Return the number of claimable items in a lane."""

    async def any_claimable(self) -> bool:
        """Return whether any lane has due work; the default always returns True."""
        return True

    @abstractmethod
    async def is_paused(self, queue: str) -> bool:
        """Return whether a lane is paused."""

    @abstractmethod
    async def set_paused(self, queue: str, paused: bool, *, by: Optional[str] = None) -> None:
        """Pause or resume a lane."""

    @abstractmethod
    async def requeue_dead(self, queue: str, limit: int) -> int:
        """Reset up to limit dead-lettered items in a lane back to queued and return the count."""

    @abstractmethod
    async def stats(self) -> Dict[str, Dict[str, Any]]:
        """Return a per-lane lifecycle snapshot keyed by lane name."""


class PostgresQueueBackend(QueueBackend):
    """``FOR UPDATE SKIP LOCKED`` backed queue on Postgres."""

    _TABLE = "notifications.etown_notification_queue"

    async def enqueue(
        self,
        queue: str,
        req: "GovernanceRequest",
        *,
        priority: int,
        dedupe_key: Optional[str],
        run_after: Optional[datetime] = None,
    ) -> bool:
        """Insert a queue row, skipping duplicates by dedupe_key, and notify runners when it is due now.

        Args:
            queue: Lane to enqueue into.
            req: Governance request serialised as the payload.
            priority: Lower values are claimed first.
            dedupe_key: Unique key; a conflicting insert is a no-op.
            run_after: Earliest claim time; None means now.

        Returns:
            True if a row was inserted, False if it was deduped.
        """
        inserted = await Database.fetchval(
            """
            INSERT INTO notifications.etown_notification_queue
                (queue, notification_type, priority, payload, dedupe_key, run_after)
            VALUES ($1, $2, $3, $4::jsonb, $5, COALESCE($6, now()))
            ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
            RETURNING id
            """,
            queue,
            req.notification_type,
            int(priority),
            json.dumps(serialize_request(req)),
            dedupe_key,
            run_after,
        )
        if inserted is not None and run_after is None:
            await _notify(queue)
        return inserted is not None

    async def claim(self, queue: str, limit: int) -> List[QueueItem]:
        """Lock up to limit due items in a lane with SKIP LOCKED, mark them processing, and return them."""
        if limit <= 0:
            return []
        rows = await Database.fetch(
            """
            UPDATE notifications.etown_notification_queue q
            SET status = 'processing', locked_at = now(), attempts = attempts + 1
            WHERE q.id IN (
                SELECT id FROM notifications.etown_notification_queue
                WHERE queue = $1 AND status IN ('queued', 'retry') AND run_after <= now()
                ORDER BY priority ASC, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT $2
            )
            RETURNING id, queue, notification_type, priority, payload, attempts, dedupe_key
            """,
            queue,
            int(limit),
        )
        items: List[QueueItem] = []
        for r in rows:
            payload = r["payload"]
            if isinstance(payload, (str, bytes)):
                payload = json.loads(payload)
            items.append(
                QueueItem(
                    id=str(r["id"]),
                    queue=r["queue"],
                    notification_type=r["notification_type"],
                    priority=int(r["priority"]),
                    payload=payload or {},
                    attempts=int(r["attempts"]),
                    dedupe_key=r["dedupe_key"],
                )
            )
        return items

    async def ack(self, item_id: str) -> None:
        """Mark an item sent and clear its lock."""
        await Database.execute(
            "UPDATE notifications.etown_notification_queue SET status = 'sent', locked_at = NULL WHERE id = $1",
            item_id,
        )

    async def nack(self, item: QueueItem, error: str) -> None:
        """Schedule an item for retry after backoff, or mark it dead once max_attempts is reached."""
        backoff =_backoff_seconds(item.attempts)
        await Database.execute(
            """
            UPDATE notifications.etown_notification_queue
            SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'retry' END,
                run_after = now() + ($2 || ' seconds')::interval,
                locked_at = NULL,
                last_error = $3
            WHERE id = $1
            """,
            item.id,
            str(backoff),
            (error or "")[:2000],
        )

    async def reap_stale(self, visibility_timeout_s: int) -> int:
        """Move processing items locked longer than the timeout back to retry and return how many were reaped."""
        rows = await Database.fetch(
            """
            UPDATE notifications.etown_notification_queue
            SET status = 'retry', locked_at = NULL,
                last_error = 'reaped: stale processing lock'
            WHERE status = 'processing'
              AND locked_at < now() - ($1 || ' seconds')::interval
            RETURNING id
            """,
            str(int(visibility_timeout_s)),
        )
        if rows:
            await _notify(queue="critical")
        return len(rows)

    async def depth(self, queue: str) -> int:
        """Return the number of queued or retry items in a lane that are due now."""
        return await Database.fetchval(
            """
            SELECT count(*) FROM notifications.etown_notification_queue
            WHERE queue = $1 AND status IN ('queued', 'retry') AND run_after <= now()
            """,
            queue,
        ) or 0

    async def any_claimable(self) -> bool:
        """Return whether any lane has a queued or retry item that is due now."""
        val = await Database.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM notifications.etown_notification_queue
                WHERE status IN ('queued', 'retry') AND run_after <= now()
            )
            """
        )
        return bool(val)

    _paused_cache: Dict[str, bool] = {}
    _paused_exp: float = 0.0
    _PAUSED_TTL = 5.0
    _stats_cache: Optional[Dict[str, Dict[str, Any]]] = None
    _stats_ts: float = 0.0
    _STATS_TTL = 30.0

    async def _refresh_paused(self) -> None:
        """Reload lane pause flags from the control table into the class cache and reset its expiry."""
        try:
            rows = await Database.fetch(
                "SELECT queue, paused FROM notifications.etown_notification_queue_control"
            )
            PostgresQueueBackend._paused_cache = {r["queue"]: bool(r["paused"]) for r in rows}
        except Exception as exc:
            logger.warning("queue pause-state refresh failed: %s", exc)
        PostgresQueueBackend._paused_exp = time.monotonic() + self._PAUSED_TTL

    async def is_paused(self, queue: str) -> bool:
        """Return whether a lane is paused, refreshing the 5-second pause cache when it has expired."""
        if time.monotonic() >= PostgresQueueBackend._paused_exp:
            await self._refresh_paused()
        return PostgresQueueBackend._paused_cache.get(queue, False)

    async def set_paused(self, queue: str, paused: bool, *, by: Optional[str] = None) -> None:
        """Upsert a lane's pause flag, invalidate the pause cache, and notify runners on resume."""
        await Database.execute(
            """
            INSERT INTO notifications.etown_notification_queue_control (queue, paused, updated_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (queue) DO UPDATE
                SET paused = EXCLUDED.paused, updated_at = now(), updated_by = EXCLUDED.updated_by
            """,
            queue, bool(paused), by,
        )
        PostgresQueueBackend._paused_exp = 0.0
        if not paused:
            await _notify(queue)

    async def requeue_dead(self, queue: str, limit: int) -> int:
        """Reset up to limit oldest dead items in a lane to queued with zero attempts, notify, and return the count."""
        if limit <= 0:
            return 0
        rows = await Database.fetch(
            """
            UPDATE notifications.etown_notification_queue
            SET status = 'queued', attempts = 0, run_after = now(),
                locked_at = NULL, last_error = NULL
            WHERE id IN (
                SELECT id FROM notifications.etown_notification_queue
                WHERE queue = $1 AND status = 'dead'
                ORDER BY created_at ASC
                LIMIT $2
            )
            RETURNING id
            """,
            queue, int(limit),
        )
        if rows:
            await _notify(queue)
        return len(rows)

    async def stats(self) -> Dict[str, Dict[str, Any]]:
        """Return per-lane pending, processing, retrying, dead, 24h sent and oldest-pending counts, cached for 30 seconds."""
        now = time.monotonic()
        if PostgresQueueBackend._stats_cache is not None and now - PostgresQueueBackend._stats_ts < self._STATS_TTL:
            return PostgresQueueBackend._stats_cache
        rows = await Database.fetch(
            """
            SELECT queue,
                COUNT(*) FILTER (WHERE status IN ('queued','retry')) AS pending,
                COUNT(*) FILTER (WHERE status = 'processing')        AS processing,
                COUNT(*) FILTER (WHERE status = 'retry')             AS retrying,
                COUNT(*) FILTER (WHERE status = 'dead')              AS dead,
                COUNT(*) FILTER (WHERE status = 'sent'
                                 AND created_at >= now() - interval '24 hours') AS sent_24h,
                COALESCE(EXTRACT(EPOCH FROM (now() - MIN(created_at)
                    FILTER (WHERE status IN ('queued','retry'))))::int, 0) AS oldest_pending_age_s
            FROM notifications.etown_notification_queue
            GROUP BY queue
            """
        )
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            out[r["queue"]] = {
                "pending": int(r["pending"] or 0),
                "processing": int(r["processing"] or 0),
                "retrying": int(r["retrying"] or 0),
                "dead": int(r["dead"] or 0),
                "sent_24h": int(r["sent_24h"] or 0),
                "oldest_pending_age_s": int(r["oldest_pending_age_s"] or 0),
            }
        PostgresQueueBackend._stats_cache = out
        PostgresQueueBackend._stats_ts = time.monotonic()
        return out


backend: QueueBackend = PostgresQueueBackend()


async def ensure_queue_table() -> None:
    """Create the notification queue table and its claim, stale and dedupe indexes if absent, re-raising on failure."""
    try:
        await Database.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications.etown_notification_queue (
                id uuid NOT NULL DEFAULT gen_random_uuid(),
                queue text NOT NULL,
                notification_type text NOT NULL,
                priority smallint NOT NULL DEFAULT 50,
                payload jsonb NOT NULL,
                dedupe_key text NULL,
                status text NOT NULL DEFAULT 'queued',
                attempts smallint NOT NULL DEFAULT 0,
                max_attempts smallint NOT NULL DEFAULT 5,
                run_after timestamp with time zone NOT NULL DEFAULT now(),
                locked_at timestamp with time zone NULL,
                last_error text NULL,
                created_at timestamp with time zone NOT NULL DEFAULT now(),
                PRIMARY KEY (id)
            )
            """
        )
        await Database.execute(
            "CREATE INDEX IF NOT EXISTS ix_queue_claim ON notifications.etown_notification_queue (queue, priority, run_after) WHERE status IN ('queued','retry')"
        )
        await Database.execute(
            "CREATE INDEX IF NOT EXISTS ix_queue_stale ON notifications.etown_notification_queue (locked_at) WHERE status = 'processing'"
        )
        await Database.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_queue_dedupe ON notifications.etown_notification_queue (dedupe_key) WHERE dedupe_key IS NOT NULL"
        )
    except Exception as exc:
        logger.error("ensure_queue_table failed: %s", exc, exc_info=True)
        raise
