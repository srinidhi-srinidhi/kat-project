"""WhatsApp Status Utilities - stores Meta delivery status webhook events.

Domain layer behind the WhatsApp status webhook: inserts sent, delivered, read and failed events into notifications.etown_whatsapp_message_status, updates the provider's WhatsApp reachability flag, marks read messages opened on the ledger and reports one message's delivery outcome.

Features:
    - Status Persistence: record_status() inserts known statuses; unknown values are skipped.
    - Reachability Signal: delivered/read sets the provider WhatsApp flag; error 131026 clears it.
    - Open Tracking: mark_ledger_opened_by_wamid() links a read receipt to the ledger row.
    - Error Tolerance: DB errors are logged and swallowed so the webhook handler never fails.

Classes:
    No classes defined (module exposes status-persistence and open-tracking functions)

Functions:
    record_status(): Persist a WhatsApp status event to the DB and update reachability.
    _has_error_code(): Return True if a Meta status error list contains a given error code.
    _update_provider_whatsapp_flag(): Flip the provider's WhatsApp reachability flag from a status event.
    fetch_delivery_outcome(): 'failed' / 'delivered' / None for one wamid, from the stored statuses.
    mark_ledger_opened_by_wamid(): Set opened_at on the ledger row whose outbound wamid matches.

Dependencies:
    - app.db.client: Async PostgreSQL connection pool
    - app.etown.notifications.ledger: mark_opened() helper
    - app.etown.provider.provider_utils: update_provider_whatsapp_reachability() (lazy import)
    - app.core.logging: Centralised logger

Updates:
    26-Jun-2026: Initial creation
    10-Jul-2026: record_status() updates etown_provider.whatsapp from the delivery signal.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    25-Sep-2026: Added fetch_delivery_outcome() for the onboarding WhatsApp's SMS fallback.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.core.logging import logger
from app.db.client import Database
from app.etown.notifications import ledger


_INSERT_STATUS_SQL = """
    INSERT INTO notifications.etown_whatsapp_message_status
        (wamid, status, recipient_id, phone_number_id,
         wa_timestamp, errors, raw)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
"""

_LOOKUP_DEDUPE_KEY_SQL = """
    SELECT dedupe_key
    FROM notifications.etown_notification_ledger
    WHERE wamid = $1
      AND dedupe_key IS NOT NULL
    LIMIT 1
"""


async def record_status(ev: dict[str, Any]) -> None:
    """Store a normalised WhatsApp status event, swallowing DB errors, then update the provider's reachability flag."""
    status_val: str = ev.get("status") or ""
    if status_val not in ("sent", "delivered", "read", "failed"):
        logger.debug("[WA_STATUS] Skipping unknown status value: %r", status_val)
        return

    try:
        errors_json: Optional[str] = None
        if ev.get("errors"):
            try:
                errors_json = json.dumps(ev["errors"], ensure_ascii=False)
            except Exception:
                logger.warning("[WA_STATUS] errors payload is not JSON-serialisable; storing null", exc_info=True)

        raw_json: Optional[str] = None
        if ev.get("raw"):
            try:
                raw_json = json.dumps(ev["raw"], ensure_ascii=False)
            except Exception:
                logger.warning("[WA_STATUS] raw payload is not JSON-serialisable; storing null", exc_info=True)

        await Database.execute(
            _INSERT_STATUS_SQL,
            ev.get("message_id"),
            status_val,
            ev.get("recipient_id"),
            ev.get("phone_number_id"),
            ev.get("timestamp"),
            errors_json,
            raw_json,
        )
        logger.info(
            "[WA_STATUS] Stored status=%s wamid=%s recipient=%s",
            status_val,
            ev.get("message_id"),
            ev.get("recipient_id"),
        )

    except Exception as exc:
        logger.warning(
            "[WA_STATUS] Failed to persist status event (status=%s wamid=%s): %s",
            status_val,
            ev.get("message_id"),
            exc,
        )

    await _update_provider_whatsapp_flag(status_val, ev)


def _has_error_code(errors: Any, code: int) -> bool:
    """Return True if *errors* (Meta's status error list) contains error *code*."""
    if not isinstance(errors, (list, tuple)):
        return False
    return any(isinstance(e, dict) and e.get("code") == code for e in errors)


async def _update_provider_whatsapp_flag(status_val: str, ev: dict[str, Any]) -> None:
    """Mark the recipient provider reachable on delivered or read, unreachable on error 131026, else do nothing."""
    if status_val in ("delivered", "read"):
        reachable = True
    elif status_val == "failed" and _has_error_code(ev.get("errors"), 131026):
        reachable = False
    else:
        return

    recipient = ev.get("recipient_id")
    if not recipient:
        return
    try:
        from app.etown.provider.provider_utils import (
            update_provider_whatsapp_reachability,
        )
        await update_provider_whatsapp_reachability(recipient, reachable)
    except Exception as exc:
        logger.warning(
            "[WA_STATUS] Failed to update provider whatsapp flag (recipient=%s): %s",
            recipient, exc,
        )


async def fetch_delivery_outcome(wamid: str) -> Optional[str]:
    """Return 'failed' or 'delivered' for a sent message from stored status rows, failed winning, else None."""
    if not wamid:
        return None
    try:
        row = await Database.fetchrow(
            """
            SELECT bool_or(status = 'failed') AS failed,
                   bool_or(status IN ('delivered', 'read')) AS delivered
            FROM notifications.etown_whatsapp_message_status
            WHERE wamid = $1
            """,
            wamid,
        )
    except Exception as exc:
        logger.warning("[WA_STATUS] fetch_delivery_outcome failed (wamid=%s): %s", wamid, exc)
        return None
    if row and row["failed"]:
        return "failed"
    if row and row["delivered"]:
        return "delivered"
    return None


async def mark_ledger_opened_by_wamid(wamid: str) -> None:
    """Mark the notification ledger row for an outbound wamid as opened, skipping silently when none matches."""
    if not wamid:
        return
    try:
        dedupe_key: Optional[str] = await Database.fetchval(
            _LOOKUP_DEDUPE_KEY_SQL, wamid
        )
        if not dedupe_key:
            logger.debug(
                "[WA_STATUS] No ledger row found for wamid=%s — opened_at not set", wamid
            )
            return
        await ledger.mark_opened(dedupe_key)
        logger.info(
            "[WA_STATUS] Marked ledger opened: wamid=%s dedupe_key=%s",
            wamid, dedupe_key,
        )
    except Exception as exc:
        logger.warning(
            "[WA_STATUS] mark_ledger_opened_by_wamid failed (wamid=%s): %s", wamid, exc
        )
