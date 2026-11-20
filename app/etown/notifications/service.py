"""Notification Governance Service - the single gate every notification passes before dispatch.

Notification-domain chokepoint: send() applies the per-type config and rollout gate, dedupe, opt-outs, marketing quiet hours and caps to each channel, then calls the central dispatcher and records the outcome on the ledger; enqueue() routes through the priority queue when it is enabled.

Features:
    - Lane Bypass: critical and transactional lanes skip opt-out and cap gates, but a WhatsApp STOP always wins.
    - Single Governance Chokepoint: send() applies config, preference, quiet-hours and cap gates.
    - Priority Queue Routing: enqueue() uses the lane's queue, else falls back to synchronous send().
    - Per-channel Fan-out: multi-channel types are cloned so each channel is governed independently.
    - Coarse Outcome Reporting: send() returns "sent"/"suppressed"/"failed" for queue ack or retry.

Classes:
    GovernanceRequest(): Dataclass with recipient, type, context, dedupe key, channel and priority.

Functions:
    send(): Apply governance rules then dispatch; never raises, returns a coarse outcome.
    enqueue(): Route a notification through the priority queue, or send it synchronously.
    _send(): The actual governance pipeline (config gate, preferences, quiet hours, caps, dispatch).
    _clone(): Return a shallow copy of a GovernanceRequest with a given channel set.
    _clone_and_record(): Fire-and-forget ledger record for a suppressed type.
    _within_cap(): Check whether a channel send is within its daily/weekly cap (or preempts it); push counts Marketing sends only.
    _dispatch(): Build a NotificationRequest from a GovernanceRequest and call the central dispatcher.

Dependencies:
    - app.etown.notifications.core.dispatcher: dispatch() — the existing central dispatcher
    - app.etown.notifications.core.config: db_get()/in_rollout() — per-type DB config and rollout gating
    - app.etown.notifications.core.registry: queue_for(), Category, get()/is_registered() — lane and category lookup
    - app.etown.notifications.core.models: Channel, NotificationRequest, PushAudience, Recipient
    - app.etown.notifications.ledger, rules, preferences, deferred: sent-state, caps/quiet-hours, opt-out, and deferred-send helpers
    - app.etown.notifications.queue.backend: backend.enqueue() (lazy import) — priority-queue producer side
    - app.core.config: settings.NOTIFICATIONS_QUEUE_ENABLED
    - app.core.logging: Centralised logging infrastructure

Updates:
    05-Jun-2026: Module created as the governance chokepoint with GovernanceRequest and send()/_send().
    11-Jun-2026: Added enqueue() onto the priority queue with a synchronous send() fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logging import logger
from app.etown.notifications import ledger, rules, preferences, deferred
from app.etown.notifications.core import config as ncfg, registry
from app.etown.notifications.core.dispatcher import dispatch
from app.etown.notifications.core.models import (
    Channel, NotificationRequest, PushAudience, Recipient,
)


@dataclass
class GovernanceRequest:
    """A notification request at the governance layer.

    Carries everything needed to apply caps/quiet-hours/preferences before
    handing off to the existing dispatcher.
    """

    userprofileid: str
    notification_type: str

    contactid: Optional[str] = None
    sender_businessid: Optional[str] = None
    mobile_number: Optional[str] = None

    ctx: Dict[str, Any] = field(default_factory=dict)

    dedupe_key: Optional[str] = None
    feature: Optional[str] = None
    oauth_token: Optional[str] = None

    channel: Optional[str] = None
    priority: int = 50


async def send(req: GovernanceRequest) -> str:
    """Apply governance rules and dispatch a notification, logging instead of raising.

    Returns:
        "sent", "suppressed", or "failed" (retryable), for the queue worker's ack decision.
    """
    try:
        return await _send(req)
    except Exception as exc:
        logger.error(
            "governance.send unhandled error (type=%s user=%s): %s",
            req.notification_type, req.userprofileid, exc, exc_info=True,
        )
        return "failed"

_SYNC_QUEUES = {"critical", "transactional"}

async def enqueue(req: GovernanceRequest) -> None:
    """Enqueue a notification on its priority queue, or send it synchronously when queueing is off, sync-laned or fails."""
    try:
        if not bool(getattr(settings, "NOTIFICATIONS_QUEUE_ENABLED", False)):
            await send(req)
            return

        from app.etown.notifications.core import registry

        cfg = await ncfg.db_get(req.notification_type)
        queue = (cfg or {}).get("queue") or registry.queue_for(req.notification_type)

        if queue in _SYNC_QUEUES:
            await send(req)
            return

        priority = int((cfg or {}).get("priority", req.priority))
        from app.etown.notifications.queue.backend import backend

        await backend.enqueue(
            queue, req, priority=priority, dedupe_key=req.dedupe_key, run_after=None
        )
    except Exception as exc:
        logger.error(
            "governance.enqueue error (type=%s user=%s): %s — falling back to sync send",
            req.notification_type, req.userprofileid, exc, exc_info=True,
        )
        await send(req)


async def _send(req: GovernanceRequest) -> str:
    """Gate each configured channel on config, rollout, dedupe, opt-out, quiet hours and caps, then dispatch and record it."""
    cfg = await ncfg.db_get(req.notification_type)
    if cfg is None:
        _clone_and_record(req, "push", "suppressed_disabled")
        return "suppressed"

    if not ncfg.in_rollout(cfg, req.userprofileid):
        _clone_and_record(req, "push", "suppressed_rollout")
        return "suppressed"

    req.priority = int(cfg.get("priority", 50))
    channels: List[str] = cfg.get("channels") or []
    if not channels:
        logger.info("governance.send: no channels configured for %s", req.notification_type)
        return "suppressed"

    prefs = await preferences.get(req.userprofileid)

    any_sent = False
    any_failed = False

    for channel in channels:
        r = _clone(req, channel)

        if r.dedupe_key and await ledger.was_sent(r.dedupe_key):
            any_sent = True
            continue

        queue_lane = (cfg.get("queue") or "") or registry.queue_for(r.notification_type)
        if queue_lane in ("critical", "transactional"):
            if channel == "whatsapp" and prefs.stopped:
                await ledger.record(r, "suppressed_optout")
                continue
        elif not preferences.can_send(prefs, channel):
            await ledger.record(r, "suppressed_optout")
            continue

        type_cfg = registry.get(r.notification_type) if registry.is_registered(r.notification_type) else None
        category = type_cfg.category if type_cfg else None

        if channel == "push" and category == registry.Category.MARKETING and cfg.get("respect_quiet_hours", True) and rules.in_quiet_hours():
            if cfg.get("defer_if_quiet", True):
                await deferred.enqueue(r)
                await ledger.record(r, "suppressed_quiet", "deferred")
            else:
                await ledger.record(r, "suppressed_quiet", "dropped")
            continue

        within = await _within_cap(r, cfg)
        if not within:
            await ledger.record(r, "suppressed_cap")
            continue
        if r.dedupe_key:
            if not await ledger.claim_send(r):
                any_sent = True
                continue

        ok = await _dispatch(r, cfg)
        if r.dedupe_key:
            await ledger.finalize(r.dedupe_key, "sent" if ok else "failed")
        else:
            await ledger.record(r, "sent" if ok else "failed")
        any_sent = any_sent or ok
        any_failed = any_failed or (not ok)

    if any_sent:
        return "sent"
    if any_failed:
        return "failed"
    return "suppressed"


def _clone(req: GovernanceRequest, channel: str) -> GovernanceRequest:
    """Return a copy of the request for one channel, suffixing its dedupe key with the channel."""
    return GovernanceRequest(
        userprofileid=req.userprofileid,
        notification_type=req.notification_type,
        contactid=req.contactid,
        sender_businessid=req.sender_businessid,
        mobile_number=req.mobile_number,
        ctx=req.ctx,
        dedupe_key=f"{req.dedupe_key}:{channel}" if req.dedupe_key else None,
        feature=req.feature,
        oauth_token=req.oauth_token,
        channel=channel,
        priority=req.priority,
    )


def _clone_and_record(req: GovernanceRequest, channel: str, status: str) -> None:
    """Record a per-channel ledger status in a fire-and-forget task."""
    r = _clone(req, channel)
    asyncio.create_task(ledger.record(r, status))


async def _within_cap(r: GovernanceRequest, cfg: Dict[str, Any]) -> bool:
    """Return whether the send fits the marketing push cap or WhatsApp daily and per-business caps; sync lanes always pass."""
    if (cfg.get("queue") or "") in ("critical", "transactional"):
        return True

    caps = await rules.get_caps()

    if r.channel == "push":
        type_cfg = registry.get(r.notification_type) if registry.is_registered(r.notification_type) else None
        category = type_cfg.category if type_cfg else None

        if category != registry.Category.MARKETING:
            return True
        inactive = not await ledger.last_open_within_days(r.userprofileid, 28)
        cap = rules.effective_push_cap(cfg, inactive, caps)
        marketing_types = [
            key for key, type_config in registry.all_types().items()
            if type_config.category == registry.Category.MARKETING
        ]
        if r.contactid:
            used = await ledger.count_today_by_contact(r.contactid, "push", marketing_types)
        else:
            used = await ledger.count_today(r.userprofileid, "push", marketing_types)
        if used < cap:
            return True
        lowest = await ledger.lowest_digest_priority_today(r.userprofileid)
        return rules.may_preempt(cfg, lowest)

    if r.channel == "whatsapp":
        if await ledger.count_today(r.userprofileid, "whatsapp") >= int(caps["wa_daily_cap"]):
            return False
        if r.sender_businessid and (
            await ledger.count_business_week(r.userprofileid, r.sender_businessid)
            >= int(caps["wa_weekly_per_business_cap"])
        ):
            return False
        return True

    return True


async def _dispatch(r: GovernanceRequest, cfg: Dict[str, Any]) -> bool:
    """Dispatch the request on its channel via the central dispatcher and return whether it was delivered."""
    try:
        ctx = dict(r.ctx)
        if r.oauth_token:
            ctx["oauth_token"] = r.oauth_token

        recipient = Recipient(
            userprofile_id=r.userprofileid,
            contact_id=r.contactid,
            mobile_number=r.mobile_number,
        )

        notif_req = NotificationRequest(
            notification_type=r.notification_type,
            recipient=recipient,
            context=ctx,
            oauth_token=r.oauth_token,
            channels=[Channel(r.channel)] if r.channel else None,
        )
        result = await dispatch(notif_req)
        channel_enum = Channel(r.channel) if r.channel else None
        delivered = (
            result.delivered.get(channel_enum, False)
            if channel_enum
            else any(result.delivered.values())
        )
        return bool(delivered)
    except Exception as exc:
        logger.error(
            "governance._dispatch error (type=%s channel=%s user=%s): %s",
            r.notification_type, r.channel, r.userprofileid, exc, exc_info=True,
        )
        return False
