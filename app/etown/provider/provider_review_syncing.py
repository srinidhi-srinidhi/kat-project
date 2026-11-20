"""Provider Review Dataverse Sync - mirrors queued unverified-provider captures to ets_providerreviews.

Background Dataverse mirror for etown_provider_review rows written by POST /api/provider/unverified_provider, so the back office can work the review queue; it upserts each row by its Postgres reviewid, binds the provider lookup, and swallows its own errors because Postgres stays authoritative.

Features:
    - Queue-order invariant: the create path must queue this sync after run_background_enhancements, or the provider bind cannot land
    - Text columns capped at 100 characters; businesslogo and servicedetails (base64) and createdon are not mirrored
    - Approver columns: review and rejectedreason travel as stored, and this sync never sets them
    - Upsert by reviewid: One PATCH against ets_providerreviews keyed on the Postgres uuid
    - Provider lookup bind: ets_ProviderID wired to the ets_providers record the row is about
    - Lookup preflight: the bind is added only once Dataverse holds the provider, with short retries
    - Lookup-failure fallback: A rejected bind is retried once without it
    - Choice mapping: NULL/'approved'/'rejected' rendered as the Pending/Approved/Rejected options
    - Yes/No mapping: interested 'true'/'false' sent as ets_interested true/false, anything else left unset
    - Oversized columns skipped rather than truncated into noise, and never read from Postgres
    - Values read back from Postgres, so the mirror sends what the row actually says
    - Gated by settings.PROVIDER_REVIEW_DATAVERSE_SYNC_ENABLED

Classes:
    No classes defined (module exposes one sync function).

Functions:
    _review_choice(): Render the Postgres review text as its ets_review Choice value
    _provider_in_dataverse(): Report whether the ets_providers record a bind names exists yet
    sync_provider_review_to_dataverse(): Mirror one etown_provider_review row to Dataverse

Dependencies:
    - app.db.client.Database: Shared asyncpg pool, for reading the stored row back
    - app.etown.provider.provider_syncing: Token, request context, length cap and upsert helper
    - httpx: Async HTTP client for Dataverse Web API calls
    - public.etown_provider_review: Source of every mirrored column
    - app.core.config: Dataverse credentials and the sync toggle
    - app.core.logging: Centralised logger

Updates:
    17-Sep-2026: Created; mirrors etown_provider_review to Dataverse ets_providerreview.
    18-Sep-2026: Fixed empty ets_ProviderID with _provider_in_dataverse(); mirrors alternatemobile as ets_alternatemobilenumber.
    22-Sep-2026: _review_choice() reads '1'/'2' as Approved/Rejected too.
    30-Sep-2026: Mirrors interested as the ets_interested Yes/No column.
"""

import asyncio
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.provider.provider_syncing import (
    _build_dataverse_context,
    _cap_len,
    _dataverse_bool,
    _upsert_record,
    get_dataverse_token,
)

_ENTITY = "ets_providerreviews"
_ENTITY_PROVIDERS = "ets_providers"
_LOG = "DATAVERSE_PROVIDER_REVIEW"

_FIELD_MAP = {
    "reviewid": "ets_reviewid",
    "action_mode": "ets_actionmode",
    "mobilenumber": "ets_mobilenumber",
    "alternatemobile": "ets_alternatemobilenumber",
    "marketing_person_name": "ets_marketingpersonname",
    "businessname": "ets_businessname",
    "description": "ets_description",
    "types": "ets_types",
    "formattedaddress": "ets_formattedaddress",
    "latitude": "ets_latitude",
    "longitude": "ets_longitude",
    "business_category": "ets_businesscategory",
    "service_category": "ets_servicecategory",
    "city": "ets_city",
    "additionaladdressinfo": "ets_additionaladdressinfo",
    "services": "ets_services",
    "area_location": "ets_arealocation",
    "servicetype": "ets_servicetype",
    "state": "ets_state",
    "language": "ets_language",
    "email": "ets_email",
    "rejectedreason": "ets_rejectedreason",
}

_UNMIRRORED_FIELDS = ("businesslogo", "servicedetails")

_REVIEW_PENDING = 0
_REVIEW_APPROVED = 1
_REVIEW_REJECTED = 2

_ETS_NAME_MAX_LEN = 850

_PROVIDER_WAIT_DELAYS = (0.0, 3.0, 9.0)

_SELECT_COLUMNS = ", ".join(_FIELD_MAP) + ", providerid, review, interested"


def _review_choice(review: Optional[str]) -> int:
    """Map a stored review word or Choice number to its ets_review value, defaulting to pending."""
    decision = (review or "").strip().lower()
    if decision in ("approved", str(_REVIEW_APPROVED)):
        return _REVIEW_APPROVED
    if decision in ("rejected", str(_REVIEW_REJECTED)):
        return _REVIEW_REJECTED
    return _REVIEW_PENDING


async def _provider_in_dataverse(
    headers: Dict[str, str], base_url: str, provider_id: str
) -> bool:
    """Return True once the Dataverse ets_providers record answers 200, retrying 404s with backoff.

    Args:
        headers: Dataverse request headers including the bearer token.
        base_url: Dataverse Web API base, from _build_dataverse_context().
        provider_id: etown_provider.providerid, which is also the Dataverse record GUID.

    Returns:
        True when found within the retry budget; False on other statuses or exhaustion.
    """
    url = f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})?$select=ets_providerid"
    attempts = len(_PROVIDER_WAIT_DELAYS)

    for attempt, delay in enumerate(_PROVIDER_WAIT_DELAYS, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=headers)
        except Exception as exc:
            logger.warning(
                "%s | LOOKUP_CHECK_ERROR | providerid=%s | attempt=%d/%d | %s",
                _LOG, provider_id, attempt, attempts, exc,
            )
            continue

        if response.status_code == 200:
            return True

        if response.status_code != 404:
            logger.warning(
                "%s | LOOKUP_CHECK_REFUSED | providerid=%s | status=%s | body=%s",
                _LOG, provider_id, response.status_code, response.text[:300],
            )
            return False

        logger.info(
            "%s | LOOKUP_NOT_YET | providerid=%s | attempt=%d/%d",
            _LOG, provider_id, attempt, attempts,
        )

    return False


async def sync_provider_review_to_dataverse(review_id: str) -> None:
    """Upsert one etown_provider_review row to Dataverse, binding the provider when present and retrying without it on failure."""
    if not settings.PROVIDER_REVIEW_DATAVERSE_SYNC_ENABLED:
        logger.info("%s | SKIP | disabled by setting | reviewid=%s", _LOG, review_id)
        return

    if not review_id:
        logger.warning("%s | SKIP | no reviewid", _LOG)
        return

    if not settings.DATAVERSE_URL:
        logger.warning("%s | SKIP | DATAVERSE_URL not configured | reviewid=%s", _LOG, review_id)
        return

    try:
        rows = await Database.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM etown_provider_review WHERE reviewid = $1",
            review_id,
        )
    except Exception as exc:
        logger.error("%s | FAILED_READ | reviewid=%s | %s", _LOG, review_id, exc, exc_info=True)
        return

    if not rows:
        logger.warning("%s | SKIP | no row | reviewid=%s", _LOG, review_id)
        return

    row = rows[0]

    payload: Dict[str, Any] = {
        attribute: _cap_len(row.get(column))
        for column, attribute in _FIELD_MAP.items()
        if row.get(column) is not None
    }
    payload["ets_review"] = _review_choice(row.get("review"))
    interested = _dataverse_bool(row.get("interested"))
    if interested is not None:
        payload["ets_interested"] = interested

    payload["ets_name"] = _cap_len(
        row.get("businessname") or row.get("mobilenumber") or review_id,
        _ETS_NAME_MAX_LEN,
    )

    provider_id = row.get("providerid")

    logger.info(
        "%s | START | reviewid=%s | action_mode=%s | providerid=%s | not_mirrored=%s",
        _LOG, review_id, row.get("action_mode"), provider_id, ",".join(_UNMIRRORED_FIELDS),
    )

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("%s | FAILED_TOKEN | reviewid=%s | %s", _LOG, review_id, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)
    url = f"{base_url}/{_ENTITY}({review_id})"

    if provider_id:
        if await _provider_in_dataverse(headers, base_url, provider_id):
            payload["ets_ProviderID@odata.bind"] = f"/{_ENTITY_PROVIDERS}({provider_id})"
        else:
            logger.warning(
                "%s | LOOKUP_UNRESOLVED | reviewid=%s | providerid=%s | ets_providers holds no "
                "such record - mirroring the row without ets_ProviderID",
                _LOG, review_id, provider_id,
            )

    synced = await _upsert_record(
        headers, url, payload, "PROVIDERREVIEW", review_id, log_prefix=_LOG
    )

    if not synced and "ets_ProviderID@odata.bind" in payload:
        logger.warning(
            "%s | RETRY_WITHOUT_LOOKUP | reviewid=%s | providerid=%s",
            _LOG, review_id, provider_id,
        )
        del payload["ets_ProviderID@odata.bind"]
        await _upsert_record(
            headers, url, payload, "PROVIDERREVIEW", review_id, log_prefix=_LOG
        )
