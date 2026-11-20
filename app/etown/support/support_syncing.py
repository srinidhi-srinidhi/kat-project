"""Support Dataverse Sync - mirrors filed support tickets from Postgres to ets_supportticket.

Background Dataverse mirror that writes each support ticket onto the ets_supportticket record keyed by its ticketid, capping fields to their Dataverse widths; Postgres stays authoritative and every call swallows its own errors.

Features:
    - Runs on insert and again after email_sent flips, sending the whole ticket both times
    - _MAX_LEN duplicates the Dataverse column widths and must change when those widths do
    - created_at and updated_at are never sent; Dataverse stamps its own createdon and modifiedon
    - One idempotent PATCH per ticket, keyed on the Postgres ticketid
    - Every field capped to the attribute's real MaxLength, so a long body cannot 400 the PATCH
    - ets_message sized to the API's own 5000-character ceiling, so REST tickets never trim
    - Truncation logged per field, so a too-narrow Dataverse column is visible, not silent
    - The email_sent Picklist mapped to its metadata values (1/0), not sent as a bool
    - Naive datetimes read as UTC, so a due_at never lands an SLA in the wrong timezone

Classes:
    No classes defined (module exposes one sync function).

Functions:
    _as_datetime_str(): Render a value as ISO 8601 for a DateTime attribute, naive read as UTC.
    _as_int(): Coerce a value to int, or None when empty or unusable.
    _emailsent_choice(): Map the email_sent boolean onto the ets_emailsent Picklist values.
    _capped(): Trim strings past their attribute MaxLength, logging each cut.
    sync_support_ticket_to_dataverse(): PATCH one ticket onto its ets_supportticket record.

Dependencies:
    - app.etown.provider.provider_syncing: Token acquisition, request context and the shared PATCH helper.
    - app.core.logging: Centralised logger.

Updates:
    24-Aug-2026: Created ets_supportticket mirror for tickets and email_sent; _MAX_LEN follows widened Dataverse fields.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.logging import logger
from app.etown.provider.provider_syncing import (
    get_dataverse_token,
    _build_dataverse_context,
    _upsert_record,
)

_ENTITY_SUPPORTTICKETS = "ets_supporttickets"
_LOG = "DATAVERSE_SUPPORT_TICKET"

_EMAILSENT_YES = 1
_EMAILSENT_NO = 0

_MAX_LEN = {
    "ets_category": 100,
    "ets_contactemail": 100,
    "ets_contactid": 100,
    "ets_contactname": 100,
    "ets_contactphone": 100,
    "ets_deleted": 100,
    "ets_message": 5000,
    "ets_name": 850,
    "ets_plancode": 100,
    "ets_reference": 100,
    "ets_resolutionnotes": 2000,
    "ets_source": 100,
    "ets_status": 100,
    "ets_subject": 200,
    "ets_supportchannel": 100,
    "ets_ticketid": 100,
    "ets_userprofileid": 100,
}


def _as_datetime_str(value: Any) -> Optional[str]:
    """Return a value as an ISO 8601 string, treating naive datetimes as UTC, or None when empty."""
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.isoformat()
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> Optional[int]:
    """Return a value as an int, or None when it is empty or not an integer."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.debug("support mirror: %r is not an integer; storing null", value)
        return None


def _emailsent_choice(value: Any) -> int:
    """Map an email_sent boolean or truthy string onto the ets_emailsent Yes/No picklist value."""
    if isinstance(value, bool):
        return _EMAILSENT_YES if value else _EMAILSENT_NO
    return (
        _EMAILSENT_YES
        if str(value).strip().lower() in ("true", "1", "yes")
        else _EMAILSENT_NO
    )


def _capped(payload: Dict[str, Any], reference: Optional[str]) -> Dict[str, Any]:
    """Return a copy of the payload with strings trimmed to their Dataverse MaxLength, logging each cut."""
    trimmed = dict(payload)
    for key, limit in _MAX_LEN.items():
        value = trimmed.get(key)
        if isinstance(value, str) and len(value) > limit:
            logger.warning(
                "%s | TRUNCATED | ref=%s | field=%s | %d chars -> %d (Dataverse MaxLength)",
                _LOG, reference, key, len(value), limit,
            )
            trimmed[key] = value[:limit]
    return trimmed


async def sync_support_ticket_to_dataverse(ticket: Dict[str, Any]) -> None:
    """Upsert a support ticket onto its ets_supportticket record keyed by ticketid, skipping tickets without one."""
    ticket_id = str(ticket.get("ticketid") or "").strip()
    reference = ticket.get("reference")
    if not ticket_id:
        logger.warning("%s | SKIPPED | ticket has no ticketid | ref=%s", _LOG, reference)
        return

    subject = ticket.get("subject")
    payload: Dict[str, Any] = {
        "ets_ticketid": ticket_id,
        "ets_name": subject,
        "ets_reference": reference,
        "ets_contactid": ticket.get("contactid"),
        "ets_userprofileid": ticket.get("userprofileid"),
        "ets_role": _as_int(ticket.get("role")),
        "ets_category": ticket.get("category"),
        "ets_subject": subject,
        "ets_message": ticket.get("message"),
        "ets_contactemail": ticket.get("contact_email"),
        "ets_contactphone": ticket.get("contact_phone"),
        "ets_contactname": ticket.get("contact_name"),
        "ets_plancode": ticket.get("plan_code"),
        "ets_supportchannel": ticket.get("support_channel"),
        "ets_responsehours": _as_int(ticket.get("response_hours")),
        "ets_dueat": _as_datetime_str(ticket.get("due_at")),
        "ets_source": ticket.get("source"),
        "ets_status": ticket.get("status"),
        "ets_emailsent": _emailsent_choice(ticket.get("email_sent")),
        "ets_resolvedat": _as_datetime_str(ticket.get("resolved_at")),
        "ets_resolutionnotes": ticket.get("resolution_notes"),
        "ets_deleted": ticket.get("deleted") or "false",
    }

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("%s | FAILED_TOKEN | ref=%s | %s", _LOG, reference, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    await _upsert_record(
        headers,
        f"{base_url}/{_ENTITY_SUPPORTTICKETS}({ticket_id})",
        _capped(payload, reference),
        "SUPPORT_TICKET",
        ticket_id,
        log_prefix=_LOG,
    )
