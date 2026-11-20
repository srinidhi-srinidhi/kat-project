"""Events Endpoints - HTTP route for batch ingestion of client-side app events.

HTTP layer for /api/events: checks the shared bearer token and inserts each event into notifications.etown_app_events, which feeds open-rate metrics and segments; notification_open events with a dedupe_key also mark the ledger entry opened.

Features:
    - Batch ingestion: Accepts up to 50 events per call, one row insert per event
    - Deduplication: ON CONFLICT DO NOTHING on (userprofileid, event_type, event_date, dedupe_key) makes replays safe
    - Ledger integration: notification_open events with a dedupe_key also update the open-rate ledger
    - Fault isolation: A single event's insert/ledger failure is logged and skipped, not raised

Endpoints:
    POST /events/ingest: Batch-insert up to 50 app events per call

Classes:
    AppEvent(): One event's payload — userprofileid, event_type, and optional dedupe_key

Functions:
    ingest_events(): Handle POST /events/ingest

Dependencies:
    - fastapi: API framework and HTTP exception handling
    - pydantic: AppEvent request model validation
    - app.core.security: Shared bearer token verification
    - app.db.client: Database insert for notifications.etown_app_events
    - app.etown.notifications.ledger: mark_opened() for notification-open deduplication

Updates:
    05-Jun-2026: Module created for batch app-event ingestion feeding notification metrics and segments.
"""

import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.security import verify_token
from app.db.client import Database
from app.etown.notifications import ledger

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_BATCH = 50


class AppEvent(BaseModel):
    userprofileid: str = Field(..., description="User profile ID that generated the event.")
    event_type: str = Field(..., description="e.g. 'app_open', 'notification_open', 'feed_view'.")
    dedupe_key: Optional[str] = Field(None, description="Optional deduplication key (required for notification_open events to update ledger).")


@router.post("/ingest", status_code=status.HTTP_204_NO_CONTENT)
async def ingest_events(
    events: List[AppEvent],
    _token: str = Depends(verify_token),
) -> None:
    """Handle POST /api/events/ingest: record a deduplicated batch of app events and mark notification opens."""
    if len(events) > _MAX_BATCH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Batch size exceeds maximum of {_MAX_BATCH} events.",
        )

    today = date.today()
    for ev in events:
        try:
            await Database.execute(
                """
                INSERT INTO notifications.etown_app_events
                    (userprofileid, event_type, event_date, dedupe_key)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT DO NOTHING
                """,
                ev.userprofileid,
                ev.event_type,
                today,
                ev.dedupe_key,
            )
            if ev.event_type == "notification_open" and ev.dedupe_key:
                await ledger.mark_opened(ev.dedupe_key)
        except Exception as exc:
            logger.warning(
                "events/ingest: failed to record event type=%s upid=%s: %s",
                ev.event_type, ev.userprofileid, exc,
            )
