"""Notification Segments - resolves a user's notification segment for the governance layer.

Notification service helper: resolve_segment classifies a userprofileid as Business Subscriber, Active, Casual or Dormant from its in-force plan on etown_provider and its app_open history in notifications.etown_app_events, for copy tone, throttling and cadence in the notification service.

Features:
    - Segments: Business Subscriber holds a non-free plan; Active opened within 7 days; Casual within 30; Dormant otherwise
    - Priority-ordered Segmentation: Business Subscriber first, then app-open recency.
    - Fail-safe Default: any DB error or missing event data resolves to ``"Active"``.

Classes:
    No classes defined (module exposes a single resolver function)

Functions:
    resolve_segment(): Return the notification segment for a userprofileid.

Dependencies:
    - app.db.client: Async database client for etown_provider and etown_app_events
    - app.etown.plans.plans_utils: _EFFECTIVE_PLAN_CODE for the in-force plan check

Updates:
    05-Jun-2026: Created resolve_segment() with Business Subscriber / Active / Casual / Dormant segments.
    19-Aug-2026: Reverted the Business Subscriber check to the etown_business_subscription_tier lookup.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from app.db.client import Database
from app.etown.plans.plans_utils import _EFFECTIVE_PLAN_CODE

logger = logging.getLogger(__name__)


async def resolve_segment(userprofileid: str) -> str:
    """Return a user's segment (Business Subscriber, Active, Casual or Dormant) from plan and app opens, defaulting to Active."""
    try:
        tier: Optional[str] = await Database.fetchval(
            f"""
            SELECT plan_code
            FROM etown_provider
            WHERE userprofileid = $1
              AND COALESCE(deleted, 'false') <> 'true'
              AND {_EFFECTIVE_PLAN_CODE.format(a='etown_provider')} <> 'free'
            LIMIT 1
            """,
            userprofileid,
        )
        if tier:
            return "Business Subscriber"

        last_open: Optional[date] = await Database.fetchval(
            """
            SELECT max(event_date)
            FROM notifications.etown_app_events
            WHERE userprofileid = $1 AND event_type = 'app_open'
            """,
            userprofileid,
        )
        opens_30d: int = await Database.fetchval(
            """
            SELECT count(*)
            FROM notifications.etown_app_events
            WHERE userprofileid = $1
              AND event_type = 'app_open'
              AND event_date >= current_date - 30
            """,
            userprofileid,
        ) or 0

        if last_open is None:
            return "Active"

        days_since = (date.today() - last_open).days

        if days_since < 7:
            return "Active"
        if opens_30d >= 1:
            return "Casual"
        return "Dormant"

    except Exception as exc:
        logger.warning("resolve_segment: error for %s: %s", userprofileid, exc)
        return "Active"
