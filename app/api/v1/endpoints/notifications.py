"""Notifications Endpoints - HTTP routes for user notifications, digest copy, metrics and queue administration.

HTTP layer for /api/notifications: checks the shared bearer token, delegates listing, counts and status updates to notifications_utils with a background Dataverse mirror, and exposes digest copy refresh, delivery metrics, queue lanes, global caps and config reload.

Features:
    - Notification count: Retrieve the total unread notification count for a user
    - Notification listing: Paginated retrieval of user notifications
    - Notification status update: Mark a notification as read/inactive (single or clear-all)
    - Digest copy pool: Query the active LLM-generated push copy variants
    - Copy pool refresh: Manually regenerate the LLM morning/evening/midday digest copy pool
    - Delivery metrics: Push open rate, WhatsApp opt-outs, and sent/suppressed/failed counts
    - Queue administration: Per-lane status, pause/resume, and dead-letter requeue
    - Global caps: Read and update the DB-managed daily push/WhatsApp caps and inactivity throttle
    - Config reload: Force an immediate re-read of the DB-backed config caches across all workers
    - Bearer token authentication: Secure access via token verification
    - Dataverse sync: Background sync of status updates to Microsoft Dataverse

Endpoints:
    POST /clearall: Mark all of a receiver's notifications Inactive/Read
    POST /notification_status_update: Mark a notification as Inactive/Read
    GET /notification_count: Get the total unread notification count for a user
    GET /get_notification: Retrieve paginated notifications for a user
    GET /content_refresh: Manually regenerate the LLM digest copy pool
    GET /metrics: Aggregated push/WhatsApp delivery metrics
    GET /queue/status: Per-lane queue depth, lifecycle counts, and paused flags
    POST /queue/pause: Pause draining of a queue lane
    POST /queue/resume: Resume draining of a previously-paused queue lane
    GET /caps: Read the global notification caps and inactivity throttle
    POST /caps: Update the global notification caps / inactivity throttle
    GET /config/reload: Hard-refresh the notification config caches on every worker
    POST /queue/requeue_dead: Reset dead-lettered items in a lane back to 'queued'

Classes:
    NotificationStatusUpdateRequest(): Body for POST /notification_status_update — notification_id
    ClearAllNotificationsRequest(): Body for POST /clearall — contact_id and userprofile_id
    QueuePauseRequest(): Body for POST /queue/pause and /queue/resume — queue lane name
    QueueRequeueDeadRequest(): Body for POST /queue/requeue_dead — queue lane name and item limit
    CapsUpdateRequest(): Body for POST /caps — optional overrides for each cap/throttle field

Functions:
    clear_all_notifications(): Mark all of a receiver's notifications Inactive/Read
    notification_status_update(): Marks a notification as Inactive/Read with background Dataverse sync
    get_notification_count(): Returns unread notification count for the given user
    get_notification(): Returns paginated notifications for the given user
    refresh_digest_copy(): Triggers LLM digest copy pool regeneration and returns the resulting variants
    get_notification_metrics(): Returns aggregated push open-rate and delivery metrics
    _validate_queue(): Validate a queue-lane name against the known lane set
    queue_status(): Return per-lane depth, lifecycle counts, and paused state for every queue
    queue_pause(): Pause draining of one queue lane
    queue_resume(): Resume draining of one queue lane
    get_caps(): Return the DB-managed global caps and inactivity throttle
    update_caps(): Update one or more global cap/throttle fields and invalidate the caps cache
    config_reload(): Force an immediate re-read of all DB-backed config caches, broadcast to workers
    queue_requeue_dead(): Reset up to `limit` dead-lettered items in a lane back to 'queued'

Dependencies:
    - fastapi: API framework and routing
    - pydantic: Request body validation models
    - app.core.security: Shared bearer token verification
    - app.etown.notifications.notifications_utils: Notification DB query helpers
    - app.etown.notifications.notifications_syncing: Dataverse background sync
    - app.etown.notifications.core.notification_generator: LLM digest copy pool management
    - app.etown.notifications.queue.backend: Queue stats, pause/resume, and dead-letter requeue
    - app.etown.notifications.rules: Global caps read/update and cache invalidation
    - app.etown.notifications.core.config / job_config / config_reload: config caches and reload broadcast
    - app.etown.notifications.core.registry: QUEUES lane set
    - app.db.client: Direct query for the /metrics and /caps endpoints
    - app.services.api_activity: track_api_call activity logging

Updates:
    13-May-2026: Initial implementation of notification_count; added notification_status_update endpoint.
    05-Jun-2026: Added get_notification and content_refresh endpoints for digest copy pool management
    28-Sep-2026: Config reload also covers booking alert SMS recipients.
"""
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.core.security import verify_token
from app.etown.notifications.notifications_utils import (
    get_notification_count as fetch_notification_count,
    get_notifications as fetch_notifications,
    update_notification_status,
    clear_all_notifications as clear_all_notifications_db,
)
from app.etown.notifications.notifications_syncing import (
    update_notification_status_in_dataverse,
    clear_all_notifications_in_dataverse,
)
from app.etown.notifications.core.notification_generator import (
    MORNING_DIGEST,
    EVENING_DIGEST,
    MIDDAY_DIGEST,
    get_active_pool as get_digest_copy_pool,
    refresh_copy_pool as refresh_digest_copy_pool,
)
from app.services.api_activity import track_api_call

_DIGEST_TYPE_KEYS = {"morning": MORNING_DIGEST, "evening": EVENING_DIGEST, "midday": MIDDAY_DIGEST}

logger = logging.getLogger(__name__)

router = APIRouter()


class NotificationStatusUpdateRequest(BaseModel):
    notification_id: str


class ClearAllNotificationsRequest(BaseModel):
    contact_id: str
    userprofile_id: str


@router.post(
    "/clearall",
    status_code=200,
    summary="Clear All Notifications",
    description="Mark all notifications as Inactive/Read for a user by passing receiver contact_id and userprofile_id.",
    responses={
        200: {
            "description": "All notifications marked as read successfully",
            "content": {
                "application/json": {
                    "example": {"message": "Notification status update completed successfully"}
                }
            },
        },
        400: {
            "description": "Missing required parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "contact_id and userprofile_id are required"}
                }
            },
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to clear all notifications"}
                }
            },
        },
    },
)
async def clear_all_notifications(
    request: ClearAllNotificationsRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/notifications/clearall: mark all of a receiver's notifications read and mirror to Dataverse."""
    if not request.contact_id or not request.contact_id.strip():
        raise HTTPException(status_code=400, detail="contact_id and userprofile_id are required")
    if not request.userprofile_id or not request.userprofile_id.strip():
        raise HTTPException(status_code=400, detail="contact_id and userprofile_id are required")

    try:
        await clear_all_notifications_db(request.contact_id, request.userprofile_id)
        background_tasks.add_task(
            clear_all_notifications_in_dataverse, request.contact_id, request.userprofile_id
        )
        return {"message": "Notification status update completed successfully"}
    except Exception as e:
        logger.error(f"Error clearing all notifications: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to clear all notifications")


@router.post(
    "/notification_status_update",
    status_code=200,
    summary="Update Notification Status",
    description="Mark a notification as Inactive/Read by notification_id.",
    responses={
        200: {
            "description": "Notification status updated successfully",
            "content": {
                "application/json": {
                    "example": {"message": "Notification status update completed successfully"}
                }
            }
        },
        400: {
            "description": "Missing required parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "notification_id is required"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to update notification status"}
                }
            }
        }
    }
)
async def notification_status_update(
    request: NotificationStatusUpdateRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token)
):
    """Handle POST /api/notifications/notification_status_update: mark one notification read and mirror to Dataverse."""
    if not request.notification_id or not request.notification_id.strip():
        raise HTTPException(
            status_code=400,
            detail="notification_id is required"
        )

    try:
        await update_notification_status(request.notification_id)
        background_tasks.add_task(
            update_notification_status_in_dataverse, request.notification_id
        )
        return {"message": "Notification status update completed successfully"}

    except Exception as e:
        logger.error(f"Error updating notification status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to update notification status"
        )


@router.get(
    "/notification_count",
    status_code=200,
    summary="Get Notification Count",
    description="Get the total unread notification count for a user by contact_id or userprofile_id.",
    responses={
        200: {
            "description": "Notification count retrieved successfully",
            "content": {
                "application/json": {
                    "example": {"notification_count": 5}
                }
            }
        },
        400: {
            "description": "Missing required parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "At least one of contact_id or userprofile_id must be provided"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve notification count"}
                }
            }
        }
    }
)
async def get_notification_count(
    contact_id: Optional[str] = Query(None, description="The contact ID of the user"),
    userprofile_id: Optional[str] = Query(None, description="The user profile ID of the user"),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/notifications/notification_count: return a user's unread notification count."""
    if not contact_id and not userprofile_id:
        raise HTTPException(
            status_code=400,
            detail="At least one of contact_id or userprofile_id must be provided"
        )

    track_api_call(
        background_tasks, "/api/notifications/notification_count", contact_id, userprofile_id
    )

    try:
        count = await fetch_notification_count(contact_id, userprofile_id)
        return {"notification_count": count}

    except Exception as e:
        logger.error(f"Error retrieving notification count: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve notification count"
        )


@router.get(
    "/get_notification",
    status_code=200,
    summary="Get Notifications",
    description="Retrieve notifications for a user by receiver_Userprofile_id or receiver_id with pagination.",
    responses={
        200: {
            "description": "Notifications retrieved successfully",
        },
        400: {
            "description": "Missing required parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "At least one of receiver_Userprofile_id or receiver_id must be provided"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve notifications"}
                }
            }
        }
    }
)
async def get_notification(
    receiver_id: Optional[str] = Query(None, description="The receiver's contact ID"),
    receiver_Userprofile_id: Optional[str] = Query(None, description="The receiver's user profile ID"),
    PageNumber: int = Query(0, description="Page number for pagination (0-based)"),
    token: str = Depends(verify_token)
):
    """Handle GET /api/notifications/get_notification: return one page of a receiver's notifications."""
    if not receiver_Userprofile_id and not receiver_id:
        raise HTTPException(
            status_code=400,
            detail="At least one of receiver_Userprofile_id or receiver_id must be provided"
        )

    try:
        notifications = await fetch_notifications(
            receiver_id=receiver_id,
            receiver_userprofile_id=receiver_Userprofile_id,
            page_number=PageNumber,
        )
        return {"notifications": notifications or []}

    except Exception as e:
        logger.error(f"Error retrieving notifications: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve notifications"
        )


@router.get(
    "/content_refresh",
    status_code=200,
    summary="Refresh LLM Digest Copy Pool",
    description=(
        "Manually regenerate the pool of LLM-generated, catchy morning-digest push "
        "copy (gpt-4o-mini). Hitting this endpoint replaces the active pool with fresh "
        "variants on success; on a failed/empty generation the existing pool is kept. "
        "Returns the resulting variants."
    ),
    responses={
        200: {
            "description": "Copy pool refreshed (or kept on empty generation)",
            "content": {
                "application/json": {
                    "example": {
                        "type_key": "morning_digest",
                        "refreshed": True,
                        "count": 30,
                        "variants": [
                            {"title": "Psst, your circle's been busy",
                             "body": "Fresh drops are waiting. Peek before they vanish."}
                        ],
                    }
                }
            },
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to refresh digest copy pool"}
                }
            },
        },
    },
)
async def refresh_digest_copy(
    digest: str = Query(
        "morning",
        description="Which digest pool to refresh: 'morning', 'evening', or 'midday'.",
    ),
    count: Optional[int] = Query(
        None, ge=1, le=100,
        description="How many variants to generate (defaults to MORNING_DIGEST_COPY_POOL_SIZE).",
    ),
    token: str = Depends(verify_token),
):
    """Handle GET /api/notifications/content_refresh: regenerate a morning, evening or midday digest copy pool and return it."""
    type_key = _DIGEST_TYPE_KEYS.get(digest.strip().lower())
    if not type_key:
        raise HTTPException(
            status_code=400,
            detail="digest must be one of: morning, evening, midday",
        )
    try:
        inserted = await refresh_digest_copy_pool(type_key, count)
        variants = await get_digest_copy_pool(type_key)
        return {
            "type_key": type_key,
            "refreshed": inserted > 0,
            "count": len(variants),
            "variants": variants,
        }
    except Exception as e:
        logger.error(f"Error refreshing digest copy pool: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to refresh digest copy pool"
        )


@router.get(
    "/metrics",
    status_code=200,
    summary="Notification Delivery Metrics",
    description=(
        "Returns aggregated governance-layer metrics: push open rate (7d), "
        "WhatsApp opt-out count (all-time), and overall sent / suppressed counts."
    ),
)
async def get_notification_metrics(
    token: str = Depends(verify_token),
):
    """Handle GET /api/notifications/metrics: return push open rate, WhatsApp opt-outs, ledger totals and queue stats."""
    from app.db.client import Database as _DB

    try:
        push_stats = await _DB.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE channel = 'push' AND sent_on >= CURRENT_DATE - 7) AS push_sent_7d,
                COUNT(*) FILTER (WHERE channel = 'push' AND opened_at IS NOT NULL AND sent_on >= CURRENT_DATE - 7) AS push_opened_7d
            FROM notifications.etown_notification_ledger
            """
        )
        wa_stop_count = await _DB.fetchval(
            "SELECT COUNT(*) FROM notifications.etown_notification_preferences WHERE whatsapp_opt_in = false AND stopped = true"
        )
        overall = await _DB.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'sent') AS total_sent,
                COUNT(*) FILTER (WHERE status LIKE 'suppressed%') AS total_suppressed,
                COUNT(*) FILTER (WHERE status = 'failed') AS total_failed
            FROM notifications.etown_notification_ledger
            """
        )
        sent_7d = int(push_stats["push_sent_7d"] or 0)
        opened_7d = int(push_stats["push_opened_7d"] or 0)
        open_rate_7d = round(opened_7d / sent_7d, 4) if sent_7d else None

        queues: dict = {}
        try:
            from app.etown.notifications.queue.backend import backend as _queue_backend
            queues = await _queue_backend.stats()
        except Exception as q_exc:
            logger.warning("notification metrics: queue stats unavailable: %s", q_exc)

        return {
            "push_open_rate_7d": open_rate_7d,
            "push_sent_7d": sent_7d,
            "push_opened_7d": opened_7d,
            "whatsapp_opt_out_total": int(wa_stop_count or 0),
            "total_sent_all_time": int(overall["total_sent"] or 0),
            "total_suppressed_all_time": int(overall["total_suppressed"] or 0),
            "total_failed_all_time": int(overall["total_failed"] or 0),
            "queues": queues,
        }
    except Exception as exc:
        logger.error("Error computing notification metrics: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve notification metrics")


_VALID_QUEUES = {"critical", "transactional", "social", "marketing"}


class QueuePauseRequest(BaseModel):
    queue: str


class QueueRequeueDeadRequest(BaseModel):
    queue: str
    limit: int = 100


def _validate_queue(queue: str) -> str:
    """Return the normalised queue lane name, raising 400 when it is not a known lane."""
    q =(queue or "").strip().lower()
    if q not in _VALID_QUEUES:
        raise HTTPException(
            status_code=400,
            detail=f"queue must be one of: {', '.join(sorted(_VALID_QUEUES))}",
        )
    return q


@router.get(
    "/queue/status",
    status_code=200,
    summary="Queue Status",
    description="Per-lane depth + lifecycle counts and paused flags for the notification queue.",
)
async def queue_status(token: str = Depends(verify_token)):
    """Handle GET /api/notifications/queue/status: return depth, lifecycle counts and paused flag per queue lane."""
    from app.etown.notifications.queue.backend import backend
    from app.etown.notifications.core.registry import QUEUES

    try:
        stats = await backend.stats()
        out = {}
        for q in QUEUES:
            s = stats.get(q, {
                "pending": 0, "processing": 0, "retrying": 0,
                "dead": 0, "sent_24h": 0, "oldest_pending_age_s": 0,
            })
            s = dict(s)
            s["paused"] = await backend.is_paused(q)
            out[q] = s
        return {"queues": out}
    except Exception as exc:
        logger.error("queue_status error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve queue status")


@router.post(
    "/queue/pause",
    status_code=200,
    summary="Pause a Queue Lane",
    description="Halt draining of a lane (items stay queued). Use during incidents.",
)
async def queue_pause(req: QueuePauseRequest, token: str = Depends(verify_token)):
    """Handle POST /api/notifications/queue/pause: stop draining a queue lane."""
    from app.etown.notifications.queue.backend import backend
    q = _validate_queue(req.queue)
    try:
        await backend.set_paused(q, True, by="api")
        return {"queue": q, "paused": True}
    except Exception as exc:
        logger.error("queue_pause error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to pause queue")


@router.post(
    "/queue/resume",
    status_code=200,
    summary="Resume a Queue Lane",
    description="Resume draining of a previously-paused lane.",
)
async def queue_resume(req: QueuePauseRequest, token: str = Depends(verify_token)):
    """Handle POST /api/notifications/queue/resume: resume draining a paused queue lane."""
    from app.etown.notifications.queue.backend import backend
    q = _validate_queue(req.queue)
    try:
        await backend.set_paused(q, False, by="api")
        return {"queue": q, "paused": False}
    except Exception as exc:
        logger.error("queue_resume error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to resume queue")


class CapsUpdateRequest(BaseModel):
    push_daily_cap: Optional[int] = None
    wa_daily_cap: Optional[int] = None
    wa_weekly_per_business_cap: Optional[int] = None
    inactivity_throttle_enabled: Optional[bool] = None
    inactivity_throttle_exempt_lanes: Optional[list] = None


@router.get(
    "/caps",
    status_code=200,
    summary="Get Global Notification Caps",
    description="Return the DB-managed daily caps + inactivity throttle (60s cache).",
)
async def get_caps(token: str = Depends(verify_token)):
    """Handle GET /api/notifications/caps: return the global daily caps and inactivity throttle."""
    from app.etown.notifications import rules
    try:
        return await rules.get_caps()
    except Exception as exc:
        logger.error("get_caps error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read caps")


@router.post(
    "/caps",
    status_code=200,
    summary="Update Global Notification Caps",
    description="Update the DB-managed daily caps / inactivity throttle. Effective within ~1 min.",
)
async def update_caps(req: CapsUpdateRequest, token: str = Depends(verify_token)):
    """Handle POST /api/notifications/caps: update the supplied global cap fields and return the fresh caps."""
    from app.db.client import Database as _DB
    from app.etown.notifications import rules

    fields, values = [], []
    for col, val in (
        ("push_daily_cap", req.push_daily_cap),
        ("wa_daily_cap", req.wa_daily_cap),
        ("wa_weekly_per_business_cap", req.wa_weekly_per_business_cap),
        ("inactivity_throttle_enabled", req.inactivity_throttle_enabled),
        ("inactivity_throttle_exempt_lanes", req.inactivity_throttle_exempt_lanes),
    ):
        if val is not None:
            values.append(val)
            fields.append(f"{col} = ${len(values)}")
    if not fields:
        raise HTTPException(status_code=400, detail="No cap fields supplied")
    try:
        await _DB.execute(
            f"UPDATE notifications.etown_notification_global_caps "
            f"SET {', '.join(fields)}, updated_at = now(), updated_by = 'api' WHERE id = 'global'"
        , *values)
        rules.invalidate_caps_cache()
        return await rules.get_caps()
    except Exception as exc:
        logger.error("update_caps error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update caps")


@router.get(
    "/config/reload",
    status_code=200,
    summary="Hard-Refresh Notification Config Caches",
    description=(
        "Force an immediate re-read of the five DB-backed notification config "
        "caches (job config, per-type notification config, global caps, WhatsApp "
        "template switches, booking alert SMS recipients), bypassing their 2-hour TTL. Broadcasts to every "
        "gunicorn worker via LISTEN/NOTIFY, so one call is enough. Call this "
        "after editing any of those tables to apply the change now."
    ),
)
async def config_reload(token: str = Depends(verify_token)):
    """Handle GET /api/notifications/config/reload: broadcast a config-cache reload and return the reloaded state."""
    from app.etown.notifications import rules
    from app.etown.notifications.core import config as ncfg
    from app.etown.notifications.core import config_reload as reload_bus
    from app.etown.notifications.core import job_config as jcfg

    try:
        broadcast_ok = await reload_bus.publish_reload(source="api:config/reload")
        dropped = reload_bus.invalidate_local()
        jobs = await jcfg.get_all_enabled()
        types = await ncfg.db_all()
        caps = await rules.get_caps()

        return {
            "status": "reloaded",
            "dropped": dropped,
            "job_config": {
                "table": "scheduler.etown_job_config",
                "enabled_jobs": len(jobs),
                "jobs": sorted(jobs.keys()),
            },
            "notification_config": {
                "table": "notifications.etown_notification_config",
                "rows": len(types),
                "enabled": sorted(k for k, v in types.items() if v.get("enabled")),
            },
            "global_caps": caps,
            "whatsapp_templates": {
                "table": "notifications.etown_whatsapp_template_config",
                "note": "cache dropped; re-reads lazily on the next WhatsApp send",
            },
            "booking_alert_sms_recipients": {
                "table": "notifications.etown_booking_alert_sms_recipients",
                "note": "cache dropped; re-reads lazily on the next booking alert SMS",
            },
            "broadcast": "sent" if broadcast_ok else "failed",
            "listener_attached": reload_bus.is_listening(),
            "scope": (
                "all workers" if broadcast_ok
                else "this worker only — others refresh at their normal TTL"
            ),
        }
    except Exception as exc:
        logger.error("config_reload error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reload config caches")


@router.post(
    "/queue/requeue_dead",
    status_code=200,
    summary="Requeue Dead-Lettered Items",
    description="Reset up to `limit` dead-lettered items in a lane back to 'queued' for retry.",
)
async def queue_requeue_dead(req: QueueRequeueDeadRequest, token: str = Depends(verify_token)):
    """Handle POST /api/notifications/queue/requeue_dead: requeue up to limit dead-lettered items in a lane."""
    from app.etown.notifications.queue.backend import backend
    q = _validate_queue(req.queue)
    limit = max(1, min(int(req.limit or 100), 5000))
    try:
        n = await backend.requeue_dead(q, limit)
        return {"queue": q, "requeued": n}
    except Exception as exc:
        logger.error("queue_requeue_dead error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to requeue dead items")
