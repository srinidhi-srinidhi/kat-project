"""Plans Dataverse Sync - mirrors provider and member subscription plan columns to the CRM.

Background Dataverse mirror for plan writes: reads plan_code, subscription_status and both plan dates back from Postgres and PATCHes them onto ets_providers and ets_members, sending explicit nulls for cleared dates; Postgres stays authoritative and every call logs instead of raising.

Features:
    - One PATCH per provider against ets_providers, carrying all four plan fields
    - One PATCH per member row against ets_members, carrying the same four
    - Deleted rows mirrored on request, for the delete path that closes the subscription
    - A merge variant for syncs that create their record with ``If-None-Match: *``
    - Disabled flag mirrored onto ets_providers for POST /plans/disable
    - Values read from etown_provider at sync time, so they cannot be stale or drifted
    - Deleted providers skipped, so a mirror never resurrects a record a delete sync removed
    - Explicit nulls for both dates, so clearing a plan clears them in Dataverse
    - Field map: ets_plancode, ets_subscriptionstatus, ets_planstartedon and ets_planexpireson
    - One ets_invoicegenerations row per promotion: period dates, contact mobile, send-to-customer off

Classes:
    No classes defined (module exposes plan sync functions).

Functions:
    _ordered_unique(): De-duplicate ids as strings, preserving first-seen order.
    _as_date_str(): Return a value as a YYYY-MM-DD string, or None when empty.
    _plan_payload(): Map one row's four plan columns onto their Dataverse field names.
    plan_fields_for_dataverse(): The four ets_ fields for one provider, to merge into a payload.
    sync_provider_plan_to_dataverse(): PATCH the four plan fields onto each provider's ets_providers record.
    sync_member_plan_to_dataverse(): PATCH the same four onto each ets_members record, keyed on contactid.
    sync_provider_disabled_to_dataverse(): PATCH the disabled flag onto each provider's ets_providers record.
    create_invoice_generation_in_dataverse(): Create one ets_invoicegenerations row for a promoted contact.

Dependencies:
    - app.db.client.Database: Shared asyncpg pool, for reading the current plan columns.
    - app.etown.provider.provider_syncing: Token acquisition, request context and the shared PATCH helper.
    - public.etown_provider: Source of the four plan columns for ets_providers.
    - public.etown_member: Source of the same four for ets_members, resolved from contactid.
    - public.etown_contact: Source of mobilephone for ets_invoicegenerations.
    - app.core.logging: Centralised logger.

Updates:
    24-Aug-2026: Created; mirrors the four plan fields to ets_providers.
    01-Sep-2026: Added include_retired for the account-delete path.
    02-Sep-2026: Added sync_provider_disabled_to_dataverse() mirroring etown_provider.disabled to ets_disabled.
    17-Sep-2026: Added sync_member_plan_to_dataverse() mirroring member plan fields to ets_members.
    26-Oct-2026: Added create_invoice_generation_in_dataverse() writing promotions to ets_invoicegenerations.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, List, Optional, Sequence

from app.core.logging import logger
from app.db.client import Database
from app.etown.provider.provider_syncing import (
    get_dataverse_token,
    _build_dataverse_context,
    _upsert_record,
)

_ENTITY_PROVIDERS = "ets_providers"
_ENTITY_MEMBERS = "ets_members"
_LOG = "DATAVERSE_PROVIDER_PLAN"
_LOG_MEMBER = "DATAVERSE_MEMBER_PLAN"
_LOG_DISABLED = "DATAVERSE_PROVIDER_DISABLED"
_ENTITY_INVOICES = "ets_invoicegenerations"
_LOG_INVOICE = "DATAVERSE_INVOICE_GENERATION"

_F_DISABLED = "ets_disabled"
_CHOICE_TRUE = 1
_CHOICE_FALSE = 0

_NULLABLE_FIELDS = frozenset({"ets_planstartedon", "ets_planexpireson"})

_PLAN_SQL = """
    SELECT providerid, plan_code, subscription_status, plan_started_on, plan_expires_on
      FROM etown_provider
     WHERE providerid = ANY($1::text[])
       AND (COALESCE(deleted, 'false') <> 'true' OR $2::boolean)
"""

_MEMBER_PLAN_SQL = """
    SELECT memberid, contactid, plan_code, subscription_status, plan_started_on, plan_expires_on
      FROM etown_member
     WHERE contactid = ANY($1::text[])
       AND (COALESCE(deleted, 'false') <> 'true' OR $2::boolean)
"""

_DISABLED_SQL = """
    SELECT providerid, disabled
      FROM etown_provider
     WHERE providerid = ANY($1::text[])
       AND COALESCE(deleted, 'false') <> 'true'
"""


def _ordered_unique(provider_ids: Sequence[str]) -> List[str]:
    """Return the non-empty IDs as de-duplicated strings in first-seen order."""
    ordered: List[str] = []
    seen = set()
    for raw in provider_ids or ():
        if not raw:
            continue
        pid = str(raw)
        if pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    return ordered


def _as_date_str(value: Any) -> Optional[str]:
    """Return a date or datetime as a YYYY-MM-DD string, pass other text through, or None when empty."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _plan_payload(row: Any) -> dict:
    """Map a provider or member row's four plan columns onto their Dataverse field names."""
    return {
        "ets_plancode": row["plan_code"],
        "ets_subscriptionstatus": row["subscription_status"],
        "ets_planstartedon": _as_date_str(row["plan_started_on"]),
        "ets_planexpireson": _as_date_str(row["plan_expires_on"]),
    }


async def plan_fields_for_dataverse(provider_id: str) -> dict:
    """Return a live provider's non-empty plan fields keyed for Dataverse, or {} when unreadable."""
    if not provider_id:
        return {}
    try:
        rows = await Database.fetch(_PLAN_SQL, [str(provider_id)], False)
    except Exception as exc:
        logger.error(
            "%s | FAILED_READ | provider=%s | %s", _LOG, provider_id, exc, exc_info=True
        )
        return {}
    if not rows:
        logger.warning("%s | NO_LIVE_ROW | provider=%s", _LOG, provider_id)
        return {}
    return {k: v for k, v in _plan_payload(rows[0]).items() if v is not None}


async def sync_provider_plan_to_dataverse(
    provider_ids: Sequence[str],
    include_retired: bool = False,
) -> None:
    """PATCH each provider's current plan columns from Postgres onto its ets_providers record, logging and never raising.

    Args:
        provider_ids: etown_provider IDs to mirror; empty and duplicate entries are tolerated.
        include_retired: Also mirror rows flagged deleted, for the account-deletion path.
    """
    ordered = _ordered_unique(provider_ids)

    if not ordered:
        logger.info("%s | SKIPPED | no provider ids given", _LOG)
        return

    try:
        rows = await Database.fetch(_PLAN_SQL, ordered, bool(include_retired))
    except Exception as exc:
        logger.error(
            "%s | FAILED_READ | providers=%d | %s", _LOG, len(ordered), exc, exc_info=True
        )
        return

    if not rows:
        logger.warning(
            "%s | NOTHING_TO_SYNC | no live provider row for any of %d id(s)",
            _LOG, len(ordered),
        )
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("%s | FAILED_TOKEN | %s", _LOG, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    synced = 0
    for row in rows:
        provider_id = str(row["providerid"])
        ok = await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})",
            _plan_payload(row),
            "PROVIDER_PLAN",
            provider_id,
            allow_null=_NULLABLE_FIELDS,
            log_prefix=_LOG,
        )
        if ok:
            synced += 1

    logger.info(
        "%s | DONE | requested=%d | synced=%d | failed=%d | skipped_not_live=%d",
        _LOG, len(ordered), synced, len(rows) - synced, len(ordered) - len(rows),
    )


async def sync_member_plan_to_dataverse(
    contact_ids: Sequence[str],
    include_retired: bool = False,
) -> None:
    """PATCH the plan columns of each contact's member rows onto their ets_members records, logging and never raising.

    Args:
        contact_ids: Contacts whose member rows to mirror; empty and duplicate entries are tolerated.
        include_retired: Also mirror member rows flagged deleted, for the account-deletion path.
    """
    ordered = _ordered_unique(contact_ids)

    if not ordered:
        logger.info("%s | SKIPPED | no contact ids given", _LOG_MEMBER)
        return

    try:
        rows = await Database.fetch(_MEMBER_PLAN_SQL, ordered, bool(include_retired))
    except Exception as exc:
        logger.error(
            "%s | FAILED_READ | contacts=%d | %s", _LOG_MEMBER, len(ordered), exc, exc_info=True
        )
        return

    if not rows:
        logger.warning(
            "%s | NOTHING_TO_SYNC | no live member row for any of %d contact(s)",
            _LOG_MEMBER, len(ordered),
        )
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("%s | FAILED_TOKEN | %s", _LOG_MEMBER, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    synced = 0
    for row in rows:
        member_id = str(row["memberid"])
        ok = await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_MEMBERS}({member_id})",
            _plan_payload(row),
            "MEMBER_PLAN",
            member_id,
            allow_null=_NULLABLE_FIELDS,
            log_prefix=_LOG_MEMBER,
        )
        if ok:
            synced += 1

    logger.info(
        "%s | DONE | contacts=%d | members=%d | synced=%d | failed=%d",
        _LOG_MEMBER, len(ordered), len(rows), synced, len(rows) - synced,
    )


async def sync_provider_disabled_to_dataverse(provider_ids: Sequence[str]) -> None:
    """PATCH each provider's current disabled flag onto ets_disabled as a Choice value, logging and never raising."""
    ordered = _ordered_unique(provider_ids)

    if not ordered:
        logger.info("%s | SKIPPED | no provider ids given", _LOG_DISABLED)
        return

    try:
        rows = await Database.fetch(_DISABLED_SQL, ordered)
    except Exception as exc:
        logger.error(
            "%s | FAILED_READ | providers=%d | %s",
            _LOG_DISABLED, len(ordered), exc, exc_info=True,
        )
        return

    if not rows:
        logger.warning(
            "%s | NOTHING_TO_SYNC | no live provider row for any of %d id(s)",
            _LOG_DISABLED, len(ordered),
        )
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("%s | FAILED_TOKEN | %s", _LOG_DISABLED, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    synced = 0
    for row in rows:
        provider_id = str(row["providerid"])
        ok = await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})",
            {_F_DISABLED: _CHOICE_TRUE if row["disabled"] else _CHOICE_FALSE},
            "PROVIDER_DISABLED",
            provider_id,
            log_prefix=_LOG_DISABLED,
        )
        if ok:
            synced += 1

    logger.info(
        "%s | DONE | requested=%d | synced=%d | failed=%d | skipped_not_live=%d",
        _LOG_DISABLED, len(ordered), synced, len(rows) - synced, len(ordered) - len(rows),
    )


async def create_invoice_generation_in_dataverse(
    contact_id: str,
    period_start: Any,
    period_end: Any,
) -> None:
    """Create one ets_invoicegenerations row with the plan period, contact mobile and send-to-customer off, logging and never raising.

    Args:
        contact_id: Promoted contact whose etown_contact.mobilephone is sent.
        period_start: Plan start date, sent as ets_periodstart.
        period_end: Plan expiry date, sent as ets_periodend.
    """
    if not contact_id:
        logger.info("%s | SKIPPED | no contact id given", _LOG_INVOICE)
        return

    try:
        mobile = await Database.fetchval(
            "SELECT mobilephone FROM etown_contact WHERE contactid = $1 LIMIT 1",
            str(contact_id),
        )
    except Exception as exc:
        logger.error(
            "%s | FAILED_READ | contact=%s | %s", _LOG_INVOICE, contact_id, exc, exc_info=True
        )
        mobile = None

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("%s | FAILED_TOKEN | %s", _LOG_INVOICE, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)
    invoice_id = str(uuid.uuid4())
    await _upsert_record(
        headers,
        f"{base_url}/{_ENTITY_INVOICES}({invoice_id})",
        {
            "ets_periodstart": _as_date_str(period_start),
            "ets_periodend": _as_date_str(period_end),
            "ets_mobilenumber": mobile,
            "ets_sendtocustomer": False,
        },
        "INVOICE_GENERATION",
        f"{invoice_id} contact={contact_id}",
        log_prefix=_LOG_INVOICE,
    )
