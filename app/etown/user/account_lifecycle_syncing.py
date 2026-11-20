"""Account Lifecycle Dataverse Sync - mirrors account and provider lifecycle flags from Postgres to the CRM.

Background Dataverse mirror for deletion, deactivation and reactivation: reads back the flags and timestamps Postgres committed and PATCHes the six ets_ lifecycle columns on each mapped entity, gated by ACCOUNT_DATAVERSE_LIFECYCLE_SYNC_ENABLED; Postgres stays authoritative and every failure is logged and swallowed.

Features:
    - Read-Back Date: the stamp comes from PostgreSQL, so both stores show one transition date
    - Single Scope Query: one PostgreSQL round trip supplies the ids for every entity
    - Correct Calendar Date: DATE ONLY columns take the IST date as-is, not via UTC
    - Choice Flags: flag columns take integer Choice option values, not 'true'/'false' text
    - Shared Client: one httpx client and one OAuth token for the whole fan-out
    - Bounded Concurrency: entities and records are PATCHed concurrently under a semaphore
    - Fail-Soft Per Entity: a missing entity or column is skipped and reported, never aborting others
    - Per-provider Mode: flags one provider's records without touching the contact or siblings
    - Flag, Never Delete: no mode deletes a Dataverse record; each is retained and stamped
    - Two-Party Entities Excluded: relations, requests, notifications and invitations are never flagged
    - Contacts Stay Active: contacts get the ets_ flags but never an Inactive statecode/statuscode

Classes:
    No classes defined (module exposes async functions plus the _LIFECYCLE_ENTITIES mapping)

Functions:
    _build_payload(): Build the SET list (with cleared flags) for one lifecycle mode
    _to_dataverse_date(): Render a PostgreSQL lifecycle timestamp as a Dataverse Date Only value, IST-correct
    _odata_literal(): Quote a value for an OData $filter, escaping embedded single quotes
    _scope_values(): Return the non-empty scope values for one leg key as a list
    _spec_columns(): Return every Dataverse column one entity's entry names, for verify to bind
    _build_filter(): Build the OData $filter selecting one contact's records in one entity
    _fetch_scope(): Read the contact's shared ids and the timestamps PostgreSQL just stamped
    _query_record_ids(): Page through one entity collecting the ids of this contact's records
    _patch_lifecycle(): Stamp the lifecycle fields on one Dataverse record
    _sync_entity(): Flag every record of one entity belonging to a contact
    sync_account_lifecycle_to_dataverse(): Mirror one contact's lifecycle flags to Dataverse.
    sync_provider_lifecycle_to_dataverse(): Mirror one provider's flags, leaving the contact untouched
    verify_lifecycle_entities(): Deployment check that every mapped entity has the 6 columns; never raises

Dependencies:
    - app.db.client: Database, for the scope + timestamp read-back
    - app.etown.user.users_syncing: get_dataverse_token(), the shared OAuth helper
    - app.core.config: DATAVERSE_URL and ACCOUNT_DATAVERSE_LIFECYCLE_SYNC_ENABLED
    - app.core.logging: Centralized application logging
    - httpx: Async HTTP client for Dataverse Web API calls

Requires:
    - The 6 ets_ lifecycle columns on the entities in _LIFECYCLE_ENTITIES
    - Dataverse app registration update rights on those tables

Updates:
    19-Aug-2026: Initial creation as a lifecycle flag sync; `contacts` added as a mapped entity.
    25-Aug-2026: Fixed ets_chatmessageses filter legs; added _spec_columns(); folded _UNCONFIRMED_ENTITIES into _LIFECYCLE_ENTITIES.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    01-Sep-2026: Added sync_provider_lifecycle_to_dataverse() for per-business deactivation.
"""

from __future__ import annotations

import asyncio
import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.user.users_syncing import get_dataverse_token

_LOG = "DATAVERSE_ACCOUNT_LIFECYCLE"

MODE_DELETE = "delete"
MODE_DEACTIVATE = "deactivate"
MODE_REACTIVATE = "reactivate"

_F_DELETED = "ets_deleted"
_F_DEACTIVATED = "ets_deactivated"
_F_REACTIVATION = "ets_reactivation"
_D_DELETION = "ets_deletiondate"
_D_DEACTIVATION = "ets_deactivationdate"
_D_REACTIVATION = "ets_reactivationdate"

_FLAG_FIELDS = (_F_DELETED, _F_DEACTIVATED, _F_REACTIVATION)
_LIFECYCLE_FIELDS = _FLAG_FIELDS + (_D_DELETION, _D_DEACTIVATION, _D_REACTIVATION)

_CHOICE_TRUE = 1
_CHOICE_FALSE = 0

_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_MODE_SOURCE_DATE = {
    MODE_DELETE: "deletiondate",
    MODE_DEACTIVATE: "deactivationdate",
    MODE_REACTIVATE: "reactivationdate",
}


def _build_payload(mode: str, stamp: str) -> Dict[str, Any]:
    """Return the Dataverse flag and date fields to PATCH for a delete, deactivate or reactivate mode.

    Raises:
        ValueError: If mode is not one of the three lifecycle modes.
    """
    if mode == MODE_DELETE:
        return {_F_DELETED: _CHOICE_TRUE, _D_DELETION: stamp}
    if mode == MODE_DEACTIVATE:
        return {_F_DEACTIVATED: _CHOICE_TRUE, _D_DEACTIVATION: stamp,
                _F_REACTIVATION: _CHOICE_FALSE}
    if mode == MODE_REACTIVATE:
        return {_F_REACTIVATION: _CHOICE_TRUE, _D_REACTIVATION: stamp,
                _F_DEACTIVATED: _CHOICE_FALSE}
    raise ValueError(f"Unknown lifecycle mode: {mode!r}")


_LIFECYCLE_ENTITIES: Dict[str, Dict[str, Any]] = {
    "contacts": {
        "id": "contactid",
        "guid": (("contactid", "contact"),),
    },
    "ets_userprofiles": {
        "id": "ets_userprofileid",
        "guid": (("_ets_contact_value", "contact"),),
    },
    "ets_members": {
        "id": "ets_memberid",
        "guid": (("_ets_contact_id_value", "contact"),
                 ("_ets_userprofileid_value", "upids")),
    },
    "ets_providers": {
        "id": "ets_providerid",
        "guid": (("_ets_contact_id_value", "contact"),
                 ("_ets_userprofileid_value", "upids")),
    },

    "ets_providerserviceses": {
        "id": "ets_providerservicesid",
        "guid": (("_ets_contactid_value", "contact"),
                 ("_ets_userprofile_value", "upids"),
                 ("_ets_providerid_value", "pids")),
    },
    "ets_etownproviderses": {
        "id": "ets_etownprovidersid",
        "guid": (("_ets_providerid_value", "pids"),),
    },
    "ets_ratings": {
        "id": "ets_ratingid",
        "guid": (("_ets_providercontactid_value", "contact"),
                 ("_ets_provideruserprofileid_value", "upids")),
    },

    "ets_feeds": {
        "id": "ets_feedid",
        "guid": (("_ets_contactid_value", "contact"),
                 ("_ets_userprofileid_value", "upids")),
    },
    "ets_feedlikeses": {
        "id": "ets_feedlikesid",
        "guid": (("_ets_likedby_value", "contact"),
                 ("_ets_likesby_value", "upids")),
    },
    "ets_feedcommentses": {
        "id": "ets_feedcommentsid",
        "guid": (("_ets_commentedby_value", "contact"),
                 ("_ets_commentedbyuserprofile_value", "upids")),
    },

    "ets_collections": {
        "id": "ets_collectionid",
        "guid": (("_ets_usercontactid_value", "contact"),
                 ("_ets_useruserprofileid_value", "upids")),
    },
    "ets_collectionitemses": {
        "id": "ets_collectionitemsid",
        "guid": (("_ets_usercontactid_value", "contact"),
                 ("_ets_useruserprofileid_value", "upids"),
                 ("_ets_providerid_value", "pids")),
    },
    "ets_favouritetowns": {
        "id": "ets_favouritetownid",
        "guid": (("_ets_usercontactid_value", "contact"),
                 ("_ets_useruserprofileid_value", "upids")),
    },
    "ets_offerses": {
        "id": "ets_offersid",
        "guid": (("_ets_contactid_value", "contact"),
                 ("_ets_userprofileid_value", "upids")),
    },
    "ets_groups": {
        "id": "ets_groupid",
        "guid": (("_ets_usercontactid_value", "contact"),
                 ("_ets_useruserprofileid_value", "upids")),
    },
    "ets_groupitemses": {
        "id": "ets_groupitemsid",
        "guid": (("_ets_usercontactid_value", "contact"),
                 ("_ets_useruserprofileid_value", "upids"),
                 ("_ets_mycircleuserprofileid_value", "upids")),
    },

    "ets_userlocationdetailses": {
        "id": "ets_userlocationdetailsid",
        "guid": (("_ets_contactid_value", "contact"),),
    },
    "ets_connectycubedetailses": {
        "id": "ets_connectycubedetailsid",
        "guid": (("_ets_contactid_value", "contact"),
                 ("_ets_userprofileid_value", "upids")),
    },
    "ets_contact_deviceses": {
        "id": "ets_contact_devicesid",
        "guid": (("_ets_contactid_value", "contact"),
                 ("_ets_userprofileid_value", "upids")),
    },

    "ets_activeuserslogs": {
        "id": "ets_activeuserslogid",
        "text": (("ets_contactid", "contact"), ("ets_mobilephone", "mobile")),
    },

    "ets_chatmessageses": {
        "id": "ets_chatmessagesid",
        "guid": (("_ets_contactid_value", "contact"),
                 ("_ets_userprofileid_value", "upids"),
                 ("_ets_memberprofileid_value", "mids"),
                 ("_ets_providerprofileid_value", "pids")),
    },
    "ets_franchises": {
        "id": "ets_franchiseid",
        "text": (("ets_phone", "mobile"), ("ets_email", "email")),
    },
    "ets_mobileverifications": {
        "id": "ets_mobileverificationid",
        "text": (("ets_mobilenumber", "mobile"),),
    },
}

_ALL_ENTITIES: Dict[str, Dict[str, Any]] = _LIFECYCLE_ENTITIES

_SCOPE_SQL = """
    SELECT c.contactid,
           COALESCE($2::text, c.mobilephone)                            AS mobile,
           c.email                                                      AS email,
           c.deletiondate                                               AS deletiondate,
           c.deactivationdate                                           AS deactivationdate,
           c.reactivationdate                                           AS reactivationdate,
           COALESCE((SELECT array_agg(u.userprofileid)
                       FROM etown_userprofile u
                      WHERE u.contactid = c.contactid
                        AND u.userprofileid IS NOT NULL), '{}'::text[]) AS upids,
           COALESCE((SELECT array_agg(m.memberid)
                       FROM etown_member m
                      WHERE m.contactid = c.contactid
                        AND m.memberid IS NOT NULL), '{}'::text[])      AS mids,
           COALESCE((SELECT array_agg(p.providerid)
                       FROM etown_provider p
                      WHERE p.contactid = c.contactid
                        AND p.providerid IS NOT NULL), '{}'::text[])    AS pids
      FROM etown_contact c
     WHERE c.contactid = $1
"""

_PAGE_SIZE = 5000
_MAX_RECORDS_PER_ENTITY = 50_000

_HTTP_CONCURRENCY = 8
_HTTP_TIMEOUT = 30.0

_VERIFY_TIMEOUT = 8.0

_GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _to_dataverse_date(value: Optional[datetime.datetime]) -> str:
    """Return a lifecycle timestamp's IST calendar date as YYYY-MM-DD, or today's IST date when None."""
    if value is None:
        logger.warning(f"{_LOG} | NO_SOURCE_DATE | falling back to today's IST date")
        return datetime.datetime.now(_IST).date().isoformat()
    if value.tzinfo is not None:
        value = value.astimezone(_IST)
    return value.date().isoformat()


def _odata_literal(value: str) -> str:
    """Quote a value for an OData $filter, escaping embedded single quotes."""
    return "'" + str(value).replace("'", "''") + "'"


def _scope_values(scope: Dict[str, Any], key: str) -> List[str]:
    """Return the non-empty scope values for one leg key as a list."""
    value = scope.get(key)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)] if str(value).strip() else []


def _spec_columns(spec: Dict[str, Any]) -> Tuple[str, ...]:
    """Return an entity's id, lifecycle and filter-leg column names, de-duplicated in order."""
    columns = (
        (spec["id"],)
        + _LIFECYCLE_FIELDS
        + tuple(prop for prop, _ in spec.get("guid", ()))
        + tuple(prop for prop, _ in spec.get("text", ()))
    )
    return tuple(dict.fromkeys(columns))


def _build_filter(spec: Dict[str, Any], scope: Dict[str, Any]) -> Optional[str]:
    """Return an OData filter ORing the entity's non-empty GUID and text legs, or None when none apply."""
    legs: List[str] = []

    for prop, key in spec.get("guid", ()):
        for guid in _scope_values(scope, key):
            if _GUID_PATTERN.match(guid):
                legs.append(f"{prop} eq {guid}")
            else:
                logger.warning(
                    f"{_LOG} | BAD_GUID | prop={prop} | value={guid!r} | leg dropped"
                )

    for prop, key in spec.get("text", ()):
        for text in _scope_values(scope, key):
            legs.append(f"{prop} eq {_odata_literal(text)}")

    return " or ".join(legs) if legs else None


async def _fetch_scope(
    contact_id: str,
    mobilenumber: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Return the contact's ids, mobile, email and lifecycle dates for filtering, or None when missing or failed."""
    try:
        row = await Database.fetchrow(_SCOPE_SQL, contact_id, mobilenumber)
    except Exception as exc:
        logger.error(f"{_LOG} | SCOPE_FAILED | contact_id={contact_id} | {exc}", exc_info=True)
        return None

    if row is None:
        logger.warning(f"{_LOG} | SCOPE_EMPTY | contact_id={contact_id} | contact not found")
        return None

    return {
        "contact": row["contactid"],
        "mobile": row["mobile"],
        "email": row["email"],
        "upids": list(row["upids"] or []),
        "mids": list(row["mids"] or []),
        "pids": list(row["pids"] or []),
        "deletiondate": row["deletiondate"],
        "deactivationdate": row["deactivationdate"],
        "reactivationdate": row["reactivationdate"],
    }


async def _query_record_ids(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    base_url: str,
    entity: str,
    spec: Dict[str, Any],
    odata_filter: str,
) -> Optional[List[str]]:
    """Page through one entity via nextLink and return matching record ids up to the cap, or None on failure."""
    id_field = spec["id"]
    url: Optional[str] = (
        f"{base_url}/{entity}?$select={id_field}&$filter={odata_filter}&$top={_PAGE_SIZE}"
    )
    record_ids: List[str] = []

    while url:
        async with sem:
            try:
                response = await client.get(url)
            except Exception as exc:
                logger.error(f"{_LOG} | QUERY_ERROR | entity={entity} | {exc}", exc_info=True)
                return None

        if response.status_code != 200:
            logger.error(
                f"{_LOG} | QUERY_FAILED | entity={entity} | status={response.status_code} "
                f"| body={response.text[:500]}"
            )
            return None

        body = response.json() or {}
        record_ids.extend(r[id_field] for r in body.get("value", []) if r.get(id_field))

        if len(record_ids) >= _MAX_RECORDS_PER_ENTITY:
            logger.error(
                f"{_LOG} | TRUNCATED | entity={entity} | cap={_MAX_RECORDS_PER_ENTITY} "
                f"| records beyond the cap are NOT flagged and need a manual pass"
            )
            return record_ids[:_MAX_RECORDS_PER_ENTITY]

        url = body.get("@odata.nextLink")

    return record_ids


async def _patch_lifecycle(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    base_url: str,
    entity: str,
    record_id: str,
    payload: Dict[str, Any],
) -> bool:
    """PATCH the lifecycle fields onto one Dataverse record and return True on success, logging failures."""
    url = f"{base_url}/{entity}({record_id})"
    headers = {"If-Match": "*"}

    async with sem:
        try:
            response = await client.patch(url, json=payload, headers=headers)
        except Exception as exc:
            logger.error(
                f"{_LOG} | PATCH_ERROR | entity={entity} | id={record_id} | {exc}",
                exc_info=True,
            )
            return False

    if response.status_code in (200, 204):
        return True

    logger.error(
        f"{_LOG} | PATCH_FAILED | entity={entity} | id={record_id} "
        f"| status={response.status_code} | body={response.text[:500]}"
    )
    return False


async def _sync_entity(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    base_url: str,
    entity: str,
    spec: Dict[str, Any],
    scope: Dict[str, Any],
    payload: Dict[str, Any],
) -> Tuple[str, int, int]:
    """PATCH the lifecycle payload onto every record of one entity in scope.

    Returns:
        (entity, flagged, failed), with failed=-1 when the query itself failed.
    """
    odata_filter = _build_filter(spec, scope)
    if not odata_filter:
        logger.info(f"{_LOG} | NO_SCOPE | entity={entity} | no usable key for this contact")
        return entity, 0, 0

    record_ids = await _query_record_ids(client, sem, base_url, entity, spec, odata_filter)
    if record_ids is None:
        return entity, 0, -1
    if not record_ids:
        logger.info(f"{_LOG} | NO_RECORDS | entity={entity}")
        return entity, 0, 0

    results = await asyncio.gather(*(
        _patch_lifecycle(client, sem, base_url, entity, record_id, payload)
        for record_id in record_ids
    ))
    flagged = sum(1 for ok in results if ok)
    failed = len(results) - flagged

    logger.info(
        f"{_LOG} | ENTITY_DONE | entity={entity} | matched={len(record_ids)} "
        f"| flagged={flagged} | failed={failed}"
    )
    return entity, flagged, failed


async def sync_account_lifecycle_to_dataverse(
    contact_id: str,
    mode: str,
    mobilenumber: Optional[str] = None,
) -> Dict[str, Any]:
    """PATCH a contact's committed PostgreSQL lifecycle flags onto all its Dataverse records, best effort, deleting nothing.

    Args:
        contact_id: The contactid that was just flagged.
        mode: "delete", "deactivate" or "reactivate".
        mobilenumber: Optional phone override for text-keyed entities.

    Returns:
        Summary dict with synced, counts and skipped entities; synced is False when it could not run.
    """
    result: Dict[str, Any] = {
        "synced": False, "mode": mode, "contact_id": contact_id,
        "entities": 0, "records_flagged": 0, "records_failed": 0,
        "entities_skipped": [],
    }

    if mode not in _MODE_SOURCE_DATE:
        logger.error(f"{_LOG} | BAD_MODE | contact_id={contact_id} | mode={mode!r}")
        return result

    if not settings.ACCOUNT_DATAVERSE_LIFECYCLE_SYNC_ENABLED:
        logger.info(
            f"{_LOG} | DISABLED | contact_id={contact_id} | mode={mode} "
            f"| ACCOUNT_DATAVERSE_LIFECYCLE_SYNC_ENABLED is false"
        )
        return result

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG} | SKIP | DATAVERSE_URL is not configured")
        return result

    scope = await _fetch_scope(contact_id, mobilenumber)
    if scope is None:
        return result

    stamp = _to_dataverse_date(scope[_MODE_SOURCE_DATE[mode]])
    payload = _build_payload(mode, stamp)

    try:
        token = await get_dataverse_token()
    except Exception as exc:
        logger.error(f"{_LOG} | FAILED_TOKEN | contact_id={contact_id} | {exc}", exc_info=True)
        return result

    base_url = f"{settings.DATAVERSE_URL.rstrip('/')}/api/data/v9.2"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "OData-Version": "4.0",
        "OData-MaxVersion": "4.0",
    }
    sem = asyncio.Semaphore(_HTTP_CONCURRENCY)

    logger.info(
        f"{_LOG} | START | contact_id={contact_id} | mode={mode} | date={stamp} "
        f"| entities={len(_ALL_ENTITIES)} | upids={len(scope['upids'])} "
        f"| mids={len(scope['mids'])} | pids={len(scope['pids'])}"
    )

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
        outcomes = await asyncio.gather(*(
            _sync_entity(client, sem, base_url, entity, spec, scope, payload)
            for entity, spec in _ALL_ENTITIES.items()
        ), return_exceptions=True)

    for entity, outcome in zip(_ALL_ENTITIES, outcomes):
        if isinstance(outcome, BaseException):
            logger.error(
                f"{_LOG} | ENTITY_CRASHED | entity={entity} | {outcome}", exc_info=outcome
            )
            result["entities_skipped"].append(entity)
            continue
        _, flagged, failed = outcome
        if failed < 0:
            result["entities_skipped"].append(entity)
            continue
        result["entities"] += 1
        result["records_flagged"] += flagged
        result["records_failed"] += failed

    result["synced"] = True
    logger.info(
        f"{_LOG} | COMPLETE | contact_id={contact_id} | mode={mode} "
        f"| entities={result['entities']}/{len(_ALL_ENTITIES)} "
        f"| flagged={result['records_flagged']} | failed={result['records_failed']} "
        f"| skipped={','.join(result['entities_skipped']) or 'none'}"
    )
    return result


_PROVIDER_ENTITIES: Dict[str, Dict[str, Any]] = {
    "ets_providers": {
        "id": "ets_providerid",
        "guid": (("_ets_userprofileid_value", "upids"),),
    },
    "ets_providerserviceses": {
        "id": "ets_providerservicesid",
        "guid": (("_ets_userprofile_value", "upids"),
                 ("_ets_providerid_value", "pids")),
    },
    "ets_etownproviderses": {
        "id": "ets_etownprovidersid",
        "guid": (("_ets_providerid_value", "pids"),),
    },
    "ets_offerses": {
        "id": "ets_offersid",
        "guid": (("_ets_userprofileid_value", "upids"),),
    },
}

_PROVIDER_SCOPE_SQL = """
    SELECT providerid, userprofileid, contactid,
           deletiondate, deactivationdate, reactivationdate
      FROM etown_provider
     WHERE providerid = $1
"""


async def sync_provider_lifecycle_to_dataverse(
    provider_id: str,
    mode: str,
) -> Dict[str, Any]:
    """Mirror one provider's committed deactivate or reactivate flags onto its own Dataverse records, best effort.

    Returns:
        Summary dict with synced, counts and skipped entities; synced is False when it could not run.
    """
    result: Dict[str, Any] = {
        "synced": False, "mode": mode, "provider_id": provider_id,
        "entities": 0, "records_flagged": 0, "records_failed": 0,
        "entities_skipped": [],
    }

    if mode not in (MODE_DEACTIVATE, MODE_REACTIVATE):
        logger.error(f"{_LOG} | BAD_MODE | provider_id={provider_id} | mode={mode!r}")
        return result

    if not settings.ACCOUNT_DATAVERSE_LIFECYCLE_SYNC_ENABLED:
        logger.info(
            f"{_LOG} | DISABLED | provider_id={provider_id} | mode={mode} "
            f"| ACCOUNT_DATAVERSE_LIFECYCLE_SYNC_ENABLED is false"
        )
        return result

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG} | SKIP | DATAVERSE_URL is not configured")
        return result

    try:
        row = await Database.fetchrow(_PROVIDER_SCOPE_SQL, provider_id)
    except Exception as exc:
        logger.error(
            f"{_LOG} | SCOPE_FAILED | provider_id={provider_id} | {exc}", exc_info=True
        )
        return result

    if row is None:
        logger.warning(f"{_LOG} | SCOPE_EMPTY | provider_id={provider_id} | not found")
        return result

    scope: Dict[str, Any] = {
        "contact": None,
        "upids": [row["userprofileid"]] if row["userprofileid"] else [],
        "pids": [row["providerid"]] if row["providerid"] else [],
        "deletiondate": row["deletiondate"],
        "deactivationdate": row["deactivationdate"],
        "reactivationdate": row["reactivationdate"],
    }

    stamp = _to_dataverse_date(scope[_MODE_SOURCE_DATE[mode]])
    payload = _build_payload(mode, stamp)

    try:
        token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            f"{_LOG} | FAILED_TOKEN | provider_id={provider_id} | {exc}", exc_info=True
        )
        return result

    base_url = f"{settings.DATAVERSE_URL.rstrip('/')}/api/data/v9.2"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "OData-Version": "4.0",
        "OData-MaxVersion": "4.0",
    }
    sem = asyncio.Semaphore(_HTTP_CONCURRENCY)

    logger.info(
        f"{_LOG} | START_PROVIDER | provider_id={provider_id} | mode={mode} | date={stamp} "
        f"| entities={len(_PROVIDER_ENTITIES)} | upids={len(scope['upids'])}"
    )

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
        outcomes = await asyncio.gather(*(
            _sync_entity(client, sem, base_url, entity, spec, scope, payload)
            for entity, spec in _PROVIDER_ENTITIES.items()
        ), return_exceptions=True)

    for entity, outcome in zip(_PROVIDER_ENTITIES, outcomes):
        if isinstance(outcome, BaseException):
            logger.error(
                f"{_LOG} | ENTITY_CRASHED | entity={entity} | {outcome}", exc_info=outcome
            )
            result["entities_skipped"].append(entity)
            continue
        _, flagged, failed = outcome
        if failed < 0:
            result["entities_skipped"].append(entity)
            continue
        result["entities"] += 1
        result["records_flagged"] += flagged
        result["records_failed"] += failed

    result["synced"] = True
    logger.info(
        f"{_LOG} | COMPLETE_PROVIDER | provider_id={provider_id} | mode={mode} "
        f"| entities={result['entities']}/{len(_PROVIDER_ENTITIES)} "
        f"| flagged={result['records_flagged']} | failed={result['records_failed']} "
        f"| skipped={','.join(result['entities_skipped']) or 'none'}"
    )
    return result


async def verify_lifecycle_entities() -> Dict[str, List[str]]:
    """Probe every mapped entity's id, lifecycle and filter columns in Dataverse, logging problems without raising.

    Returns:
        Entity set names grouped under ok, unreachable and missing_columns.
    """
    report: Dict[str, List[str]] = {"ok": [], "unreachable": [], "missing_columns": []}

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG} | VERIFY_SKIP | DATAVERSE_URL is not configured")
        return report

    try:
        token = await get_dataverse_token()
    except Exception as exc:
        logger.warning(f"{_LOG} | VERIFY_SKIP | token unavailable | {exc}")
        return report

    base_url = f"{settings.DATAVERSE_URL.rstrip('/')}/api/data/v9.2"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    sem = asyncio.Semaphore(_HTTP_CONCURRENCY)

    async def check(entity: str, spec: Dict[str, Any]) -> Tuple[str, str]:
        """Return (entity, "ok" | "unreachable" | "missing_columns")."""
        selected = ",".join(_spec_columns(spec))
        async with sem:
            try:
                response = await client.get(
                    f"{base_url}/{entity}?$select={selected}&$top=1"
                )
            except Exception as exc:
                logger.error(f"{_LOG} | VERIFY_ERROR | entity={entity} | {exc}", exc_info=True)
                return entity, "unreachable"

        if response.status_code == 200:
            return entity, "ok"
        bucket = "missing_columns" if response.status_code == 400 else "unreachable"
        logger.error(
            f"{_LOG} | VERIFY_FAILED | entity={entity} | status={response.status_code} "
            f"| body={response.text[:300]}"
        )
        return entity, bucket

    async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT, headers=headers) as client:
        for entity, bucket in await asyncio.gather(*(
            check(entity, spec) for entity, spec in _ALL_ENTITIES.items()
        )):
            report[bucket].append(entity)

    if report["unreachable"] or report["missing_columns"]:
        logger.error(
            f"{_LOG} | VERIFY_INCOMPLETE | ok={len(report['ok'])}/{len(_ALL_ENTITIES)} "
            f"| unreachable={','.join(report['unreachable']) or 'none'} "
            f"| missing_columns={','.join(report['missing_columns']) or 'none'} "
            f"| Records in these entities will NOT be flagged when an account is retired. "
            f"Fix the entity set name or a filter leg name in _LIFECYCLE_ENTITIES, or add "
            f"the 6 ets_ lifecycle columns to the table. The VERIFY_FAILED line above names "
            f"the column Dataverse rejected; scripts/export_dataverse_schema.py prints the "
            f"names the table actually has."
        )
    else:
        logger.info(
            f"{_LOG} | VERIFY_OK | all {len(_ALL_ENTITIES)} entities carry the lifecycle "
            f"columns and every filter leg they are keyed on"
        )
    return report
