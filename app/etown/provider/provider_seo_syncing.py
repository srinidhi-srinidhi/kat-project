"""Provider SEO Metadata Sync - mirrors verified providers to the Dataverse ets_providerseometadata table.

Background Dataverse mirror that rebuilds one ets_providerseometadatas record per live etown_verified provider from etown_provider, etown_providerservices and rated etown_allrequests bookings, and hard-deletes it once the provider is deleted or unverified.

Features:
    - Reconciles from Postgres: each call reads the provider's current state, so repeated calls are safe
    - Deterministic record id: uuid5 of providerid, so a gone provider's record is deleted without a lookup
    - Listed providers: providertype 'etown_verified' and deleted not 'true'; any other state is hard-deleted
    - Services JSON: services and serviceimageurl1 of each live etown_providerservices row
    - Reviews JSON: overall rating, rating count, and each completed booking's reviewer, stars, service and date    - JSON fitted to the 4000-character Memo columns by dropping trailing entries
    - Provider lookup bound once ets_providers holds the record; until then the caller can retry
    - Plan choice: plan_code mapped onto ets_plancode (free 0, silver 1, unset as free); other codes null
    - Cleared Postgres values sent as null, so Dataverse never keeps a stale value
    - Over-long serviceimageurl left unset rather than truncated into a broken link
    - Returns an error string instead of raising, for the queue worker to retry

Classes:
    No classes defined (module exposes sync functions only)

Functions:
    seo_record_id(): Return the deterministic ets_providerseometadataid for a providerid
    _fit_json(): Serialise a list compactly, dropping trailing entries until it fits a column
    build_services_json(): Render a provider's live services as the ets_providerservices JSON
    build_reviews_json(): Render a provider's rated bookings as the ets_providerreviews JSON
    _creation_date(): Render a naive IST creationdate as an ISO timestamp with its offset
    sync_provider_seo_metadata_to_dataverse(): Upsert or hard-delete one provider's ets_providerseometadatas record

Dependencies:
    - json: Serialises the services and reviews columns
    - uuid: Derives the record id from providerid
    - httpx: Async HTTP client for the lookup check and the DELETE
    - app.db.client.Database: Shared asyncpg pool, for reading the provider back
    - app.etown.provider.provider_syncing: Request context, length cap and upsert helper
    - app.utils.ist_clock: IST offset for creationdate
    - app.core.logging: Centralised logger
    - public.etown_provider / etown_providerservices / etown_allrequests / etown_requestservicelists: Mirrored data
    - app.etown.services.services_utils: _REVIEWS_SQL follows get_provider_ratings(), the provider page's list

Updates:
    21-Oct-2026: Created; mirrors verified providers to ets_providerseometadata, reviews matching the provider page, serviceimageurl1 per service.
    27-Oct-2026: Mirrors plan_code to the ets_plancode choice.
"""

import json
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

import httpx

from app.core.logging import logger
from app.db.client import Database
from app.etown.provider.provider_syncing import _build_dataverse_context, _cap_len, _upsert_record
from app.utils.ist_clock import IST

_ENTITY = "ets_providerseometadatas"
_ENTITY_PROVIDERS = "ets_providers"
_LOG = "DATAVERSE_PROVIDER_SEO"

_RECORD_NAMESPACE = uuid.UUID("6f1d2c4e-8a3b-5e7f-9c0d-1a2b3c4d5e6f")

_LISTED_PROVIDERTYPE = "etown_verified"

_NAME_MAX_LEN = 850
_DESCRIPTION_MAX_LEN = 5000
_JSON_MAX_LEN = 4000
_IMAGE_URL_MAX_LEN = 100

_ROLE_OPTIONS = (1, 2)

_PLAN_OPTIONS = {"free": 0, "silver": 1}

_NULLABLE = (
    "ets_providertype", "ets_role", "ets_description", "ets_address", "ets_city", "ets_townname",
    "ets_email", "ets_mobilenumber", "ets_serviceimageurl", "ets_publicsearchprovider",
    "ets_dateofcreation", "ets_providerservices", "ets_providerreviews", "ets_plancode",
)

_PROVIDER_SQL = """
    SELECT providerid, businessname, providertype, role, description, address, city, townname,
           email, mobilenumber, serviceimageurl, publicsearchprovider, creationdate, userprofileid,
           plan_code, COALESCE(deleted, 'false') <> 'true' AS live
      FROM etown_provider
     WHERE providerid = $1
"""

_SERVICE_FIELDS = ("services", "serviceimageurl1")

_SERVICES_SQL = f"""
    SELECT {", ".join(_SERVICE_FIELDS)}
      FROM etown_providerservices
     WHERE providerid = $1
       AND COALESCE(deleted, 'false') <> 'true'
     ORDER BY createdon NULLS LAST, providerservicesid
"""

_REVIEWS_SQL = """
    SELECT * FROM (
        SELECT DISTINCT ON (ar.allrequestid)
               ar.allrequestid,
               ar.ratingvalue,
               COALESCE(NULLIF(TRIM(ar.requestorname), ''), ar.requestoruserprofileidname) AS reviewer,
               ps.services AS service,
               ar.bookingcompletedtime AS ratedon
          FROM etown_allrequests ar
          JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid
          LEFT JOIN etown_providerservices ps ON ps.providerservicesid = rsl.providerservices
         WHERE rsl.serviceprovideruserprofileid = $1
           AND ar."isRating" != 'false'
           AND ar.ratingvalue IS NOT NULL
           AND ar.requeststatus = 'completed'
         ORDER BY ar.allrequestid, ps.services ASC NULLS LAST
    ) dedup
    ORDER BY dedup.ratedon DESC NULLS LAST, dedup.allrequestid
"""


def seo_record_id(provider_id: str) -> str:
    """Return the ets_providerseometadataid GUID derived from providerid, the same on every call."""
    return str(uuid.uuid5(_RECORD_NAMESPACE, provider_id))


def _fit_json(
    entries: List[Dict[str, Any]],
    max_len: int,
    wrap: Optional[Callable[[List[Dict[str, Any]]], Any]] = None,
) -> str:
    """Return compact JSON for entries, wrapped if asked, dropping trailing entries until it fits max_len.

    Args:
        entries: Items in priority order; the last ones are dropped first.
        max_len: Column length limit in characters.
        wrap: Builds the serialised object from the kept entries; the bare list when omitted.
    """
    kept = list(entries)
    while True:
        text = json.dumps(
            wrap(kept) if wrap else kept, ensure_ascii=False, separators=(",", ":"), default=str
        )
        if len(text) <= max_len or not kept:
            return text
        kept.pop()


def build_services_json(rows: Iterable[Dict[str, Any]], max_len: int = _JSON_MAX_LEN) -> str:
    """Return the services JSON list of {services, serviceimageurl1}, oldest service first, fitted to max_len.

    Args:
        rows: Live etown_providerservices rows with services and serviceimageurl1.
        max_len: Column length limit in characters.
    """
    entries = [{field: row.get(field) for field in _SERVICE_FIELDS} for row in rows]
    return _fit_json(entries, max_len)


def build_reviews_json(rows: Iterable[Dict[str, Any]], max_len: int = _JSON_MAX_LEN) -> str:
    """Return the reviews JSON with overall rating, count and newest-first reviews, fitted to max_len.

    Args:
        rows: Rated bookings with ratingvalue, reviewer, service and ratedon, newest first.
        max_len: Column length limit in characters.
    """
    entries = [
        {
            "reviewername": row.get("reviewer"),
            "rating": int(row["ratingvalue"]),
            "service": row.get("service"),
            "reviewdate": row["ratedon"].date().isoformat() if row.get("ratedon") else None,
        }
        for row in rows
    ]
    overall = round(sum(e["rating"] for e in entries) / len(entries), 2) if entries else None
    return _fit_json(
        entries,
        max_len,
        lambda kept: {"overallrating": overall, "ratingcount": len(entries), "reviews": kept},
    )


def _creation_date(value: Optional[datetime]) -> Optional[str]:
    """Return creationdate as ISO 8601 with the IST offset, reading a naive value as IST wall-clock time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return value.isoformat()


async def sync_provider_seo_metadata_to_dataverse(
    provider_id: str,
    access_token: str,
    bind_wait: bool = False,
) -> Optional[str]:
    """Upsert a listed provider's ets_providerseometadatas record or hard-delete an unlisted one; return None or an error.

    Args:
        provider_id: etown_provider.providerid, also the ets_providers GUID.
        access_token: Dataverse bearer token.
        bind_wait: When True and ets_providers lacks the provider, fail so the caller retries instead of upserting unbound.

    Returns:
        None on success, otherwise a short error for the caller to record and retry.
    """
    base_url, headers = _build_dataverse_context(access_token)
    record_id = seo_record_id(provider_id)
    record_url = f"{base_url}/{_ENTITY}({record_id})"

    try:
        provider = await Database.fetchrow(_PROVIDER_SQL, provider_id)
    except Exception as exc:
        logger.error("%s | FAILED_READ | providerid=%s | %s", _LOG, provider_id, exc, exc_info=True)
        return f"read failed: {exc}"

    listed = bool(provider) and provider["live"] and provider["providertype"] == _LISTED_PROVIDERTYPE

    if not listed:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.delete(record_url, headers=headers)
        except Exception as exc:
            logger.error("%s | DELETE_ERROR | providerid=%s | %s", _LOG, provider_id, exc, exc_info=True)
            return f"delete failed: {exc}"
        if response.status_code in (204, 404):
            logger.info(
                "%s | DELETED | providerid=%s | id=%s | status=%s",
                _LOG, provider_id, record_id, response.status_code,
            )
            return None
        logger.error(
            "%s | DELETE_FAILED | providerid=%s | status=%s | body=%s",
            _LOG, provider_id, response.status_code, response.text[:300],
        )
        return f"delete status {response.status_code}"

    try:
        services = await Database.fetch(_SERVICES_SQL, provider_id)
        reviews = (
            await Database.fetch(_REVIEWS_SQL, provider["userprofileid"])
            if provider["userprofileid"] else []
        )
    except Exception as exc:
        logger.error("%s | FAILED_READ | providerid=%s | %s", _LOG, provider_id, exc, exc_info=True)
        return f"read failed: {exc}"

    image_url = provider["serviceimageurl"]
    if image_url and len(image_url) > _IMAGE_URL_MAX_LEN:
        logger.warning(
            "%s | IMAGE_URL_TOO_LONG | providerid=%s | length=%d | max=%d",
            _LOG, provider_id, len(image_url), _IMAGE_URL_MAX_LEN,
        )
        image_url = None

    payload: Dict[str, Any] = {
        "ets_name": _cap_len(provider["businessname"] or provider_id, _NAME_MAX_LEN),
        "ets_providertype": _cap_len(provider["providertype"]),
        "ets_role": provider["role"] if provider["role"] in _ROLE_OPTIONS else None,
        "ets_plancode": _PLAN_OPTIONS.get((provider["plan_code"] or "free").lower()),
        "ets_description": _cap_len(provider["description"], _DESCRIPTION_MAX_LEN),
        "ets_address": _cap_len(provider["address"]),
        "ets_city": _cap_len(provider["city"]),
        "ets_townname": _cap_len(provider["townname"]),
        "ets_email": _cap_len(provider["email"]),
        "ets_mobilenumber": _cap_len(provider["mobilenumber"]),
        "ets_serviceimageurl": image_url,
        "ets_publicsearchprovider": _cap_len(provider["publicsearchprovider"]),
        "ets_dateofcreation": _creation_date(provider["creationdate"]),
        "ets_providerservices": build_services_json([dict(r) for r in services]),
        "ets_providerreviews": build_reviews_json([dict(r) for r in reviews]),
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            lookup = await client.get(
                f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})?$select=ets_providerid",
                headers=headers,
            )
    except Exception as exc:
        logger.warning("%s | LOOKUP_CHECK_ERROR | providerid=%s | %s", _LOG, provider_id, exc)
        return f"lookup check failed: {exc}"

    if lookup.status_code == 200:
        payload["ets_ProviderID@odata.bind"] = f"/{_ENTITY_PROVIDERS}({provider_id})"
    elif lookup.status_code == 404 and bind_wait:
        logger.info("%s | LOOKUP_NOT_YET | providerid=%s", _LOG, provider_id)
        return "ets_providers record not yet in Dataverse"
    elif lookup.status_code == 404:
        logger.warning(
            "%s | LOOKUP_UNRESOLVED | providerid=%s | mirroring without ets_ProviderID",
            _LOG, provider_id,
        )
    else:
        logger.warning(
            "%s | LOOKUP_CHECK_REFUSED | providerid=%s | status=%s | body=%s",
            _LOG, provider_id, lookup.status_code, lookup.text[:300],
        )
        return f"lookup check status {lookup.status_code}"

    synced = await _upsert_record(
        headers, record_url, payload, "PROVIDERSEOMETADATA", record_id,
        allow_null=_NULLABLE, log_prefix=_LOG,
    )
    return None if synced else "upsert failed"
