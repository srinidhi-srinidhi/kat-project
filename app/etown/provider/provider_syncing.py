"""Provider Dataverse Sync - mirrors provider onboarding, edits, deletes and ratings to the CRM.

Background Dataverse mirror for provider writes: upserts contact, userprofile, provider and providerservice records, patches promotions and claims, deletes providers and services, and records ratings; it runs as FastAPI BackgroundTasks with client-credentials tokens, and Postgres stays authoritative.

Features:
    - Provider Onboard Sync: Upserts contact, userprofile, provider and providerservice records
    - Provider Edit Sync: Mirrors profile updates and full onboard edits, including contact swaps
    - Promotion and Claim Sync: PATCHes verification fields on promotion and names on claim
    - Provider Delete Sync: Deletes provider records from Dataverse after the DB deletion
    - Provider Service Delete Sync: Deletes a single providerservice record from Dataverse
    - Add Service Sync: Upserts a single new providerservice record in Dataverse after add_services workflow
    - Rating Sync: Upserts ets_ratings row and updates allrequest rating fields after rating submission
    - Provider Image Sync: PATCHes ets_serviceimageurl on existing provider and service records only, reporting success
    - OAuth Token Management: Acquires client-credentials tokens for Dataverse API auth
    - BackgroundTask Integration: All sync functions designed for non-blocking execution

Classes:
    No classes defined (module uses function-based sync utilities)

Functions:
    _cap_len(): Truncate an over-long string to a Dataverse attribute's max length (code-point safe)
    _dataverse_bool(): Render a stored 'true'/'false' as a Dataverse Yes/No value
    get_dataverse_token(): Acquire OAuth2 client-credentials token for Dataverse
    _build_dataverse_context(): Build the (base_url, headers) pair used by every Web API call
    _upsert_record(): PATCH one Dataverse record, retrying with invalid/over-long props dropped or truncated
    sync_provider_onboard_to_dataverse(): Upsert provider onboard records in Dataverse
    sync_provider_promotion_to_dataverse(): PATCH only the verification fields when an unverified provider is promoted on login
    sync_provider_claim_rename_to_dataverse(): Carry a claimant's name onto a claimed unverified provider's userprofiles/providers
    _delete_record(): DELETE one Dataverse record, tolerating an already-gone (404) target
    sync_provider_delete_from_dataverse(): Delete a provider's records from Dataverse
    sync_provider_service_delete_from_dataverse(): Delete one providerservice record from Dataverse
    sync_add_service_to_dataverse(): Upsert a single new providerservice row after add_services workflow
    sync_allrequest_rating_to_dataverse(): Sync isRating+ratingvalue to ets_allrequestses
    sync_rating_to_dataverse(): Create or update ets_ratings row after rating submission
    sync_provider_profile_update_to_dataverse(): PATCH ets_providers + all ets_providerserviceses for a profile update
    sync_provider_image_to_dataverse(): PATCH ets_serviceimageurl on an existing provider and its services, reporting success
    sync_provider_edit_to_dataverse(): Mirror a full onboard edit across all four entities, deleting a vacated contact

Dependencies:
    - asyncio: Async execution support
    - json: Serialises JSON-valued fields for Dataverse text columns
    - re: Field-name parsing from Dataverse error messages
    - typing: Type hints for sync parameters
    - httpx: Async HTTP client for Dataverse Web API calls
    - app.core.config: Dataverse API configuration and OAuth credentials
    - app.core.logging: Centralized application logging

Updates:
    26-May-2026: Added Features, Classes, Functions, Dependencies, Updates sections to complete module docstring
    17-Jun-2026: Added sync_add_service_to_dataverse(), sync_allrequest_rating_to_dataverse() and sync_rating_to_dataverse().
    03-Jul-2026: Added sync_provider_profile_update_to_dataverse() for /edit_profile endpoint Dataverse sync
    08-Jul-2026: Added website parameter to sync_provider_onboard_to_dataverse().
    14-Jul-2026: Added sync_provider_promotion_to_dataverse() for on-login provider promotion.
    06-Aug-2026: Added sync_provider_edit_to_dataverse(), covering the whole onboard edit in one task.
    24-Aug-2026: _upsert_record() takes allow_null/log_prefix.
    08-Sep-2026: The onboard ets_providers payload carries ets_plancode from provider_info.
    23-Sep-2026: Added sync_provider_claim_rename_to_dataverse(); onboard sync drops ets_alternatemobileverified.
    30-Sep-2026: Onboard ets_providers payload carries ets_interested as Yes/No via _dataverse_bool().
    26-Oct-2026: Added sync_provider_image_to_dataverse().
"""

import asyncio
import json
import re
from typing import Any, Dict, Iterable, List, Optional

import httpx

from app.core.config import settings
from app.core.logging import logger

_ENTITY_CONTACTS = "contacts"
_ENTITY_USERPROFILES = "ets_userprofiles"
_ENTITY_PROVIDERS = "ets_providers"
_ENTITY_PROVIDERSERVICES = "ets_providerserviceses"
_INVALID_PROP_PATTERN = re.compile(r"Invalid property '([^']+)' was found")
_LENGTH_LIMIT_PATTERN = re.compile(
    r"length of the '([^']+)' attribute .*?exceeded the maximum allowed length of '(\d+)'"
)
_ETS_NAME_MAX_LEN = 100


def _cap_len(value: Any, max_len: int = _ETS_NAME_MAX_LEN) -> Any:
    """Return a string truncated to max_len characters, or the value unchanged if not an over-long string."""
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len]
    return value


def _dataverse_bool(value: Any) -> Optional[bool]:
    """Return True for a stored 'true', False for 'false', and None for anything else, for a Dataverse Yes/No column."""
    text = str(value).strip().lower() if value is not None else ""
    if text == "true":
        return True
    if text == "false":
        return False
    return None


async def get_dataverse_token() -> str:
    """Acquire an OAuth2 client-credentials access token for Dataverse, raising if none is returned."""
    token_url = (
        f"https://login.microsoftonline.com/{settings.DATAVERSE_TENANT_ID}"
        "/oauth2/v2.0/token"
    )
    payload = {
        "grant_type": "client_credentials",
        "client_id": settings.DATAVERSE_CLIENT_ID,
        "client_secret": settings.DATAVERSE_CLIENT_SECRET,
        "scope": f"{settings.DATAVERSE_URL}/.default",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(token_url, data=payload)
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValueError("Dataverse token response did not contain access_token")
        return token


def _build_dataverse_context(access_token: str) -> tuple:
    """Return (base_url, headers) for Dataverse Web API calls."""
    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
        "OData-MaxVersion": "4.0",
    }
    return base_url, headers


async def _upsert_record(
    headers: Dict[str, str],
    url: str,
    payload: Dict[str, Any],
    label: str,
    record_id: str,
    allow_null: Optional[Iterable[str]] = None,
    log_prefix: str = "DATAVERSE_PROVIDER_ONBOARD",
) -> bool:
    """PATCH one Dataverse record, dropping Nones and retrying bad props or network errors; return success.

    Args:
        headers: Dataverse request headers including the bearer token.
        url: Full record URL.
        payload: Attributes to write.
        label: Log label identifying the record kind.
        record_id: Record GUID, for logs.
        allow_null: Payload keys whose None is sent as null instead of being dropped.
        log_prefix: Log prefix identifying the sync flow.
    """
    nullable = frozenset(allow_null or ())
    clean_payload = {
        k: v for k, v in payload.items()
        if (v is not None or k in nullable)
        and not (k.endswith("@odata.bind") and "(None)" in str(v))
    }
    _MAX_NET_RETRIES = 3
    _RETRY_DELAYS = [2.0, 5.0, 10.0]
    net_attempts = 0

    for _ in range(len(clean_payload) + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.patch(url, json=clean_payload, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            net_attempts += 1
            if net_attempts <= _MAX_NET_RETRIES:
                delay = _RETRY_DELAYS[net_attempts - 1]
                logger.warning(
                    "%s | %s | TRANSIENT_ERROR | id=%s | attempt=%d/%d | retrying in %.0fs | %s",
                    log_prefix, label, record_id, net_attempts, _MAX_NET_RETRIES, delay, exc,
                )
                await asyncio.sleep(delay)
                continue
            logger.error(
                "%s | %s | ERROR | id=%s | exhausted %d retries | %s",
                log_prefix, label, record_id, _MAX_NET_RETRIES, exc, exc_info=True,
            )
            return False
        except Exception as exc:
            logger.error(
                "%s | %s | ERROR | id=%s | %s",
                log_prefix, label, record_id, exc, exc_info=True,
            )
            return False

        if response.status_code in (200, 204):
            logger.info(
                "%s | %s | SUCCESS | id=%s",
                log_prefix, label, record_id,
            )
            return True

        if response.status_code == 400:
            match = _INVALID_PROP_PATTERN.search(response.text)
            if match:
                bad_prop = match.group(1)
                if bad_prop in clean_payload:
                    logger.warning(
                        "%s | %s | DROP_INVALID_PROP | id=%s | prop=%s",
                        log_prefix, label, record_id, bad_prop,
                    )
                    del clean_payload[bad_prop]
                    continue

            length_match = _LENGTH_LIMIT_PATTERN.search(response.text)
            if length_match:
                bad_prop, max_len = length_match.group(1), int(length_match.group(2))
                value = clean_payload.get(bad_prop)
                if isinstance(value, str) and len(value) > max_len:
                    logger.warning(
                        "%s | %s | TRUNCATE_OVERLONG_PROP | id=%s | prop=%s | max=%d",
                        log_prefix, label, record_id, bad_prop, max_len,
                    )
                    clean_payload[bad_prop] = value[:max_len]
                    continue

        logger.error(
            "%s | %s | FAILED | id=%s | status=%s | body=%s",
            log_prefix, label, record_id, response.status_code, response.text,
        )
        return False

    logger.error(
        "%s | %s | FAILED | id=%s | exhausted invalid-prop retries",
        log_prefix, label, record_id,
    )
    return False


async def sync_provider_onboard_to_dataverse(
    contact_info: Dict[str, Any],
    userprofile_info: Dict[str, Any],
    provider_info: Dict[str, Any],
    providerservice_info: Dict[str, Any],
    *,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    address: Optional[str] = None,
    areaname: Optional[str] = None,
    townname: Optional[str] = None,
    townprofileid: Optional[str] = None,
    statecode: Optional[str] = None,
    additionaladdressinfo: Optional[str] = None,
    general_category: Optional[str] = None,
    providertype: Optional[str] = None,
    serviceimageurl: Optional[str] = None,
    servicetype: Optional[str] = None,
    website: Optional[str] = None,
    serviceitems: Optional[List[Dict[str, Any]]] = None,
    verified: bool = False,
    source: Optional[str] = None,
) -> None:
    """Upsert onboarding contact, userprofile, provider and providerservice to Dataverse in order, stopping at the first failure."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_PROVIDER_ONBOARD | FAILED_TOKEN | %s", exc, exc_info=True
        )
        return

    base_url, headers = _build_dataverse_context(access_token)

    contact_id = contact_info.get("contactid")
    userprofile_id = userprofile_info.get("userprofileid")
    provider_id = provider_info.get("providerid")
    providerservice_id = providerservice_info.get("providerservicesid_orig")
    lat_str = str(latitude) if latitude is not None else None
    lng_str = str(longitude) if longitude is not None else None

    contact_payload = {
        "firstname": contact_info.get("firstname"),
        "mobilephone": contact_info.get("mobilephone"),
        "emailaddress1": contact_info.get("email"),
        "address1_city": contact_info.get("city"),
        "ets_verified": verified,
    }
    if "alternatemobile" in contact_info:
        contact_payload["ets_alternatemobile"] = contact_info["alternatemobile"]

    userprofile_payload = {
        "ets_Contact@odata.bind": f"/{_ENTITY_CONTACTS}({contact_id})",
        "ets_role": userprofile_info.get("role"),
        "ets_name": _cap_len(userprofile_info.get("name")),
        "ets_mobilenumber": contact_info.get("mobilephone"),
        "ets_verified": verified,
    }

    _provider_has_alternate = (
        "alternatemobile" in provider_info or "alternatemobile" in contact_info
    )
    _provider_alternate = (
        provider_info.get("alternatemobile") or contact_info.get("alternatemobile")
    )

    provider_payload = {
        "ets_businessname": provider_info.get("businessname"),
        "ets_name": _cap_len(provider_info.get("businessname")),
        "ets_businesscategory": provider_info.get("businesscategory"),
        "ets_Contact_id@odata.bind": f"/{_ENTITY_CONTACTS}({contact_id})",
        "ets_UserProfileID@odata.bind": f"/{_ENTITY_USERPROFILES}({userprofile_id})",
        "ets_city": provider_info.get("city"),
        "ets_description": provider_info.get("description"),
        "ets_publicsearchprovider": provider_info.get("publicsearchprovider") or "verified",
        "ets_mobilenumber": contact_info.get("mobilephone"),
        "ets_email": contact_info.get("email"),
        "ets_address": address,
        "ets_areaname": areaname,
        "ets_townname": townname,
        "ets_statecode": statecode,
        **(
            {"ets_alternatemobile": _provider_alternate}
            if _provider_has_alternate else {}
        ),
        "ets_latitude": lat_str,
        "ets_longitude": lng_str,
        "ets_additionaladdressinfo": additionaladdressinfo,
        "ets_serviceimageurl": serviceimageurl or provider_info.get("serviceimageurl"),
        "ets_providertype": providertype or "etown_verified",
        "ets_role": 1,
        "ets_servicetype": servicetype,
        "ets_websiteurl": website,
        "ets_source": source,
        "ets_plancode": provider_info.get("plan_code"),
        "ets_interested": _dataverse_bool(provider_info.get("interested")),
    }
    if townprofileid:
        provider_payload["ets_TownProfileName@odata.bind"] = (
            f"/ets_townprofiles({townprofileid})"
        )

    service_imageurl = serviceimageurl or providerservice_info.get("serviceimageurl") or provider_info.get("serviceimageurl")
    service_imageurl1 = providerservice_info.get("serviceimageurl1")
    service_imageurl2 = providerservice_info.get("serviceimageurl2")
    service_imageurl3 = providerservice_info.get("serviceimageurl3")
    availability = providerservice_info.get("availability")
    providerservice_payload = {
        "ets_ProviderId@odata.bind": f"/{_ENTITY_PROVIDERS}({provider_id})",
        "ets_ContactId@odata.bind": f"/{_ENTITY_CONTACTS}({contact_id})",
        "ets_UserProfile@odata.bind": f"/{_ENTITY_USERPROFILES}({userprofile_id})",
        "ets_businessname": providerservice_info.get("businessname"),
        "ets_name": providerservice_info.get("businessname") or provider_info.get("businessname"),
        "ets_businesscategory": providerservice_info.get("businesscategory"),
        "ets_servicecategory": providerservice_info.get("servicecategory"),
        "ets_servicedescription": providerservice_info.get("servicedescription"),
        "ets_services": providerservice_info.get("services"),
        "ets_availability": availability,
        "ets_availability1": providerservice_info.get("availability1") or availability,
        "ets_generalcategory": general_category,
        "ets_city": provider_info.get("city"),
        "ets_mobile": contact_info.get("mobilephone"),
        "ets_businessnumber": providerservice_info.get("businessnumber") or contact_info.get("mobilephone"),
        "ets_email": contact_info.get("email"),
        "ets_address": address,
        "ets_areaname": areaname,
        "ets_statecode": statecode,
        "ets_servicelatitude": lat_str,
        "ets_servicelongitude": lng_str,
        "ets_additionaladdressinfo": additionaladdressinfo,
        "ets_pricerange": providerservice_info.get("pricerange"),
        "ets_quantity": providerservice_info.get("quantity"),
        "ets_websiteurl": website or providerservice_info.get("websiteurl"),
        "ets_yearsinbusiness": providerservice_info.get("yearsinbusiness"),
        "ets_aiserviceid": providerservice_info.get("aiserviceid"),
        "ets_providertype": providertype or "etown_verified",
        "ets_publicsearchprovider": provider_info.get("publicsearchprovider") or "verified",
        "ets_serviceimageurl": service_imageurl,
        "ets_serviceimage1": providerservice_info.get("serviceimage1") or service_imageurl1,
        "ets_serviceimage2": providerservice_info.get("serviceimage2") or service_imageurl2,
        "ets_serviceimage3": providerservice_info.get("serviceimage3") or service_imageurl3,
        "ets_serviceimage4": providerservice_info.get("serviceimage4"),
        "ets_serviceimage5": providerservice_info.get("serviceimage5"),
        "ets_serviceimage6": providerservice_info.get("serviceimage6"),
        "ets_serviceimage7": providerservice_info.get("serviceimage7"),
        "ets_serviceimage8": providerservice_info.get("serviceimage8"),
        "ets_serviceimage9": providerservice_info.get("serviceimage9"),
        "ets_serviceimage10": providerservice_info.get("serviceimage10"),
        "ets_serviceitems": json.dumps(serviceitems) if serviceitems is not None else None,
        "ets_source": source,
    }
    if townprofileid:
        providerservice_payload["ets_TownProfileName@odata.bind"] = (
            f"/ets_townprofiles({townprofileid})"
        )

    contact_ok = await _upsert_record(
        headers,
        f"{base_url}/{_ENTITY_CONTACTS}({contact_id})",
        contact_payload,
        "CONTACT",
        contact_id,
    )
    if not contact_ok:
        logger.error(
            "DATAVERSE_PROVIDER_ONBOARD | ABORTED | contact failed — skipping userprofile/provider/providerservice"
        )
        return

    userprofile_ok = await _upsert_record(
        headers,
        f"{base_url}/{_ENTITY_USERPROFILES}({userprofile_id})",
        userprofile_payload,
        "USERPROFILE",
        userprofile_id,
    )
    if not userprofile_ok:
        logger.error(
            "DATAVERSE_PROVIDER_ONBOARD | ABORTED | userprofile failed — skipping provider/providerservice"
        )
        return

    provider_ok = await _upsert_record(
        headers,
        f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})",
        provider_payload,
        "PROVIDER",
        provider_id,
    )
    if not provider_ok:
        logger.error(
            "DATAVERSE_PROVIDER_ONBOARD | ABORTED | provider failed — skipping providerservice"
        )
        return

    await _upsert_record(
        headers,
        f"{base_url}/{_ENTITY_PROVIDERSERVICES}({providerservice_id})",
        providerservice_payload,
        "PROVIDERSERVICE",
        providerservice_id,
    )


async def sync_provider_promotion_to_dataverse(
    contact_id: str,
    userprofile_ids: List[str],
    provider_ids: List[str],
    providerservices_ids: List[str],
) -> None:
    """PATCH the verification flags on a promoted provider's Dataverse contact, profiles, providers and services.

    Args:
        contact_id: Contact UUID to mark verified.
        userprofile_ids: role=1 userprofile UUIDs to mark verified.
        provider_ids: etown_provider UUIDs to flip to verified.
        providerservices_ids: etown_providerservices (original) UUIDs to flip to verified.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_PROVIDER_PROMOTE | FAILED_TOKEN | %s", exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    await _upsert_record(
        headers,
        f"{base_url}/{_ENTITY_CONTACTS}({contact_id})",
        {"ets_verified": True},
        "CONTACT_PROMOTE",
        contact_id,
    )
    for up_id in userprofile_ids:
        await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_USERPROFILES}({up_id})",
            {"ets_verified": True},
            "USERPROFILE_PROMOTE",
            up_id,
        )
    for p_id in provider_ids:
        await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({p_id})",
            {"ets_providertype": "etown_verified", "ets_publicsearchprovider": "verified"},
            "PROVIDER_PROMOTE",
            p_id,
        )
    for ps_id in providerservices_ids:
        await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERSERVICES}({ps_id})",
            {"ets_providertype": "etown_verified", "ets_publicsearchprovider": "verified"},
            "PROVIDERSERVICE_PROMOTE",
            ps_id,
        )


async def sync_provider_claim_rename_to_dataverse(
    firstname: str,
    userprofile_ids: List[str],
    provider_ids: List[str],
    wait_for: Optional[Any] = None,
) -> None:
    """Write the claimant's name to ets_name on each userprofile and ets_firstname on each provider in Dataverse.

    Args:
        firstname: Name the claimant registered with.
        userprofile_ids: role=1 userprofile UUIDs renamed in Postgres.
        provider_ids: etown_provider UUIDs renamed in Postgres.
        wait_for: Awaitable to finish first (the registration sync), errors ignored.
    """
    if wait_for is not None:
        try:
            await wait_for
        except Exception as exc:
            logger.debug("DATAVERSE_PROVIDER_CLAIM_RENAME | wait_for failed: %s", exc)

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_PROVIDER_CLAIM_RENAME | FAILED_TOKEN | %s", exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    for up_id in userprofile_ids:
        await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_USERPROFILES}({up_id})",
            {"ets_name": _cap_len(firstname)},
            "USERPROFILE_CLAIM_RENAME",
            up_id,
            log_prefix="DATAVERSE_PROVIDER_CLAIM_RENAME",
        )
    for p_id in provider_ids:
        await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({p_id})",
            {"ets_firstname": _cap_len(firstname)},
            "PROVIDER_CLAIM_RENAME",
            p_id,
            log_prefix="DATAVERSE_PROVIDER_CLAIM_RENAME",
        )


async def _delete_record(
    headers: Dict[str, str],
    url: str,
    label: str,
    record_id: str,
) -> None:
    """DELETE one Dataverse record, treating 404 as success and logging instead of raising."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.delete(url, headers=headers)
        if response.status_code in (204, 404):
            logger.info(
                "DATAVERSE_PROVIDER_DELETE | %s | SUCCESS | id=%s",
                label, record_id,
            )
        else:
            logger.error(
                "DATAVERSE_PROVIDER_DELETE | %s | FAILED | id=%s | status=%s | body=%s",
                label, record_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_PROVIDER_DELETE | %s | ERROR | id=%s | %s",
            label, record_id, exc, exc_info=True,
        )


async def sync_provider_delete_from_dataverse(
    contact_id: str,
    userprofile_id: str,
    provider_id: Optional[str],
) -> None:
    """Delete a provider's Dataverse services, provider and userprofile in child-first order, keeping the contact."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_PROVIDER_DELETE | FAILED_TOKEN | %s", exc, exc_info=True
        )
        return

    base_url, headers = _build_dataverse_context(access_token)

    if provider_id:
        try:
            filter_url = (
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}"
                f"?$filter=_ets_providerid_value eq {provider_id}"
                f"&$select=ets_providerservicesid"
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(filter_url, headers=headers)
            if resp.status_code == 200:
                service_ids = [
                    r["ets_providerservicesid"]
                    for r in resp.json().get("value", [])
                ]
                for svc_id in service_ids:
                    await _delete_record(
                        headers,
                        f"{base_url}/{_ENTITY_PROVIDERSERVICES}({svc_id})",
                        "PROVIDERSERVICE",
                        svc_id,
                    )
            else:
                logger.error(
                    "DATAVERSE_PROVIDER_DELETE | PROVIDERSERVICE_QUERY | FAILED | "
                    "provider_id=%s | status=%s | body=%s",
                    provider_id, resp.status_code, resp.text,
                )
        except Exception as exc:
            logger.error(
                "DATAVERSE_PROVIDER_DELETE | PROVIDERSERVICE_QUERY | ERROR | provider_id=%s | %s",
                provider_id, exc, exc_info=True,
            )

        await _delete_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})",
            "PROVIDER",
            provider_id,
        )

    await _delete_record(
        headers,
        f"{base_url}/{_ENTITY_USERPROFILES}({userprofile_id})",
        "USERPROFILE",
        userprofile_id,
    )

    logger.info(
        "DATAVERSE_PROVIDER_DELETE | DONE | provider_id=%s | userprofile_id=%s | contact_kept=%s",
        provider_id, userprofile_id, contact_id,
    )


async def sync_provider_service_delete_from_dataverse(
    providerservices_orig_id: str,
) -> None:
    """Delete one ets_providerserviceses record from Dataverse by its etown_providerservices id."""
    if not providerservices_orig_id:
        logger.warning("DATAVERSE_SERVICE_DELETE | SKIPPED | providerservices_orig_id is None")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_SERVICE_DELETE | FAILED_TOKEN | %s", exc, exc_info=True
        )
        return

    base_url, headers = _build_dataverse_context(access_token)

    await _delete_record(
        headers,
        f"{base_url}/{_ENTITY_PROVIDERSERVICES}({providerservices_orig_id})",
        "PROVIDERSERVICE",
        providerservices_orig_id,
    )


async def sync_add_service_to_dataverse(
    service_id: str,
    provider: Dict[str, Any],
    contact_id: str,
    userprofile_id: str,
    service_name: Optional[str],
    service_description: Optional[str],
    business_category: Optional[str],
    service_category: Optional[str],
    img1: Optional[str],
    img2: Optional[str],
    img3: Optional[str],
    service_items: Optional[Any] = None,
    source: Optional[str] = None,
    general_category: Optional[str] = None,
    pricerange: Optional[str] = None,
    quantity: Optional[str] = None,
) -> None:
    """Upsert one enriched providerservice to Dataverse, retrying without lookup bindings if the first PATCH fails.

    Args:
        service_id: etown_providerservices id, used as the Dataverse key.
        provider: Provider dict with address, location and business info.
        contact_id: Contact UUID for the lookup binding.
        userprofile_id: UserProfile UUID for the lookup binding.
        service_name: Name of the service.
        service_description: Description of the service.
        business_category: AI-resolved business category, may be None.
        service_category: AI-resolved service sub-category, may be None.
        img1: Blob URL for service image 1, may be None.
        img2: Blob URL for service image 2, may be None.
        img3: Blob URL for service image 3, may be None.
        service_items: Offering-categories list or JSON string, may be None.
        source: Listing source written to ets_source, may be None.
        general_category: AI-resolved general category, may be None.
        pricerange: Price range for the service, may be None.
        quantity: Quantity or unit text for the service, may be None.
    """
    if not service_id:
        logger.warning("DATAVERSE_ADD_SERVICE | SKIPPED | service_id is None")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_ADD_SERVICE | FAILED_TOKEN | service_id=%s | %s",
            service_id, exc, exc_info=True,
        )
        return

    base_url, headers = _build_dataverse_context(access_token)

    provider_id = provider.get("providerid")
    lat_str = str(provider["latitude"]) if provider.get("latitude") is not None else None
    lng_str = str(provider["longitude"]) if provider.get("longitude") is not None else None

    payload: Dict[str, Any] = {
        "ets_ProviderId@odata.bind": f"/{_ENTITY_PROVIDERS}({provider_id})",
        "ets_ContactId@odata.bind": f"/{_ENTITY_CONTACTS}({contact_id})",
        "ets_UserProfile@odata.bind": f"/{_ENTITY_USERPROFILES}({userprofile_id})",
        "ets_businessname": provider.get("businessname"),
        "ets_name": service_name or provider.get("businessname"),
        "ets_businesscategory": business_category or provider.get("businesscategory"),
        "ets_servicecategory": service_category,
        "ets_generalcategory": general_category,
        "ets_servicedescription": service_description,
        "ets_services": service_name,
        "ets_pricerange": pricerange,
        "ets_quantity": quantity,
        "ets_city": provider.get("city"),
        "ets_mobile": provider.get("mobilenumber"),
        "ets_businessnumber": provider.get("mobilenumber"),
        "ets_email": provider.get("email"),
        "ets_address": provider.get("address"),
        "ets_areaname": provider.get("areaname"),
        "ets_statecode": provider.get("statecode"),
        "ets_servicelatitude": lat_str,
        "ets_servicelongitude": lng_str,
        "ets_additionaladdressinfo": provider.get("additionaladdressinfo"),
        "ets_availability": provider.get("availability"),
        "ets_providertype": provider.get("providertype") or "etown_verified",
        "ets_publicsearchprovider": provider.get("publicsearchprovider") or "verified",
        "ets_serviceimageurl": provider.get("serviceimageurl"),
        "ets_serviceimage1": img1,
        "ets_serviceimage2": img2,
        "ets_serviceimage3": img3,
        "ets_serviceitems": (
            json.dumps(service_items)
            if service_items is not None and not isinstance(service_items, str)
            else service_items
        ),
        "ets_source": source,
    }

    url = f"{base_url}/{_ENTITY_PROVIDERSERVICES}({service_id})"

    ok = await _upsert_record(headers, url, payload, "ADD_SERVICE", service_id)

    if not ok:
        payload_no_lookups = {k: v for k, v in payload.items() if not k.endswith("@odata.bind")}
        logger.info(
            "DATAVERSE_ADD_SERVICE | RETRY_WITHOUT_LOOKUPS | id=%s", service_id
        )
        await _upsert_record(headers, url, payload_no_lookups, "ADD_SERVICE_NO_LOOKUPS", service_id)


_ENTITY_ALLREQUESTS = "ets_allrequestses"
_ENTITY_RATINGS = "ets_ratings"


async def sync_allrequest_rating_to_dataverse(
    allrequest_id: str,
    israting: str,
    ratingvalue: int,
) -> None:
    """PATCH ets_israting and ets_ratingvalue on an ets_allrequestses row after a rating."""
    if not allrequest_id:
        return
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_RATING_ALLREQUEST | FAILED_TOKEN | id=%s | %s", allrequest_id, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)
    payload = {
        "ets_israting": israting,
        "ets_ratingvalue": ratingvalue,
    }
    url = f"{base_url}/{_ENTITY_ALLREQUESTS}({allrequest_id})"
    await _upsert_record(headers, url, payload, "DATAVERSE_RATING_ALLREQUEST", allrequest_id)


async def sync_rating_to_dataverse(
    rating_row: dict,
    provider_contact_id: Optional[str],
    provider_userprofile_id: Optional[str],
    is_new: bool = False,
) -> None:
    """Upsert an etown_ratings row to ets_ratings, adding text and business name when new and binding provider lookups."""
    rating_id = rating_row.get("ratingid")
    if not rating_id:
        return
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_UPSERT_RATING | FAILED_TOKEN | id=%s | %s", rating_id, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)
    payload: dict = {
        "ets_overallrating": rating_row.get("overallrating"),
        "ets_ratingcount": rating_row.get("ratingcount"),
        "ets_memberbookingcount": rating_row.get("memberbookingcount"),
    }
    if is_new:
        payload["ets_ratingtext"] = "Etown Rating Feature"
        payload["ets_businessname"] = rating_row.get("providerbusinessname")
    if provider_contact_id:
        payload["ets_providercontactid@odata.bind"] = f"/contacts({provider_contact_id})"
    if provider_userprofile_id:
        payload["ets_provideruserprofileid@odata.bind"] = f"/ets_userprofiles({provider_userprofile_id})"

    url = f"{base_url}/{_ENTITY_RATINGS}({rating_id})"
    await _upsert_record(headers, url, payload, "DATAVERSE_UPSERT_RATING", rating_id)


async def sync_provider_profile_update_to_dataverse(
    provider_id: str,
    providerservices_ids: List[str],
    provider_payload: Dict[str, Any],
    service_payload: Dict[str, Any],
    provider_allow_null: Optional[Iterable[str]] = None,
) -> None:
    """PATCH a provider and all its providerservices in Dataverse concurrently, logging any failure.

    Args:
        provider_id: Local provider UUID, also the ets_providers id.
        providerservices_ids: Local providerservices UUIDs to patch.
        provider_payload: Fields to PATCH on ets_providers.
        service_payload: Fields to PATCH on each ets_providerserviceses record.
        provider_allow_null: provider_payload keys whose None is sent as an explicit null.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_PROFILE_UPDATE | FAILED_TOKEN | provider=%s | %s",
            provider_id, exc, exc_info=True,
        )
        return

    base_url, headers = _build_dataverse_context(access_token)

    tasks = [
        _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})",
            provider_payload,
            "DATAVERSE_PROFILE_UPDATE_PROVIDER",
            provider_id,
            allow_null=provider_allow_null,
        )
    ]
    for sid in providerservices_ids:
        tasks.append(
            _upsert_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}({sid})",
                service_payload,
                "DATAVERSE_PROFILE_UPDATE_SERVICE",
                sid,
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            label = "provider" if i == 0 else f"service[{i - 1}]"
            logger.error(
                "DATAVERSE_PROFILE_UPDATE | TASK_FAILED | %s | provider=%s | %s",
                label, provider_id, r,
            )


async def sync_provider_image_to_dataverse(
    provider_id: str,
    provider_image: Optional[str],
    service_images: Dict[str, str],
) -> bool:
    """PATCH ets_serviceimageurl on an existing provider and its services, returning True only when every write lands.

    Args:
        provider_id: Local provider UUID, also the ets_providers id.
        provider_image: Image path for ets_providers; None leaves the provider record untouched.
        service_images: etown_providerservices (original) UUIDs mapped to their image paths.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_PROVIDER_IMAGE | FAILED_TOKEN | provider=%s | %s",
            provider_id, exc, exc_info=True,
        )
        return False

    base_url, headers = _build_dataverse_context(access_token)
    headers = {**headers, "If-Match": "*"}
    tasks = []
    if provider_image:
        tasks.append(_upsert_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})",
            {"ets_serviceimageurl": provider_image},
            "PROVIDER",
            provider_id,
            log_prefix="DATAVERSE_PROVIDER_IMAGE",
        ))
    for sid, image in service_images.items():
        tasks.append(_upsert_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERSERVICES}({sid})",
            {"ets_serviceimageurl": image},
            "SERVICE",
            sid,
            log_prefix="DATAVERSE_PROVIDER_IMAGE",
        ))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    failed = sum(1 for r in results if r is not True)
    if failed:
        logger.error(
            "DATAVERSE_PROVIDER_IMAGE | FAILED | provider=%s | %s of %s write(s) failed",
            provider_id, failed, len(results),
        )
    return not failed


async def sync_provider_edit_to_dataverse(
    provider_id: str,
    providerservices_ids: List[str],
    contact_id: str,
    userprofile_id: Optional[str],
    contact_payload: Dict[str, Any],
    userprofile_payload: Dict[str, Any],
    provider_payload: Dict[str, Any],
    service_payload: Dict[str, Any],
    deleted_contact_id: Optional[str] = None,
) -> None:
    """Mirror a provider edit to Dataverse: contact, userprofile, provider, services, then delete any vacated contact.

    Args:
        provider_id: Local provider UUID, also the ets_providers id.
        providerservices_ids: etown_providerservices (original) UUIDs to PATCH.
        contact_id: Contact the listing ends up on.
        userprofile_id: Provider's userprofile UUID, or None when it has none.
        contact_payload: Fields to PATCH on contacts.
        userprofile_payload: Fields to PATCH on ets_userprofiles.
        provider_payload: Fields to PATCH on ets_providers.
        service_payload: Fields to PATCH on each ets_providerserviceses row.
        deleted_contact_id: Contact removed by a swap, deleted only if the provider PATCH succeeded.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_PROVIDER_EDIT | FAILED_TOKEN | provider=%s | %s",
            provider_id, exc, exc_info=True,
        )
        return

    base_url, headers = _build_dataverse_context(access_token)

    if contact_id and contact_payload:
        contact_ok = await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_CONTACTS}({contact_id})",
            contact_payload,
            "DATAVERSE_PROVIDER_EDIT_CONTACT",
            contact_id,
        )
        if not contact_ok:
            logger.error(
                "DATAVERSE_PROVIDER_EDIT | ABORTED | contact %s failed — skipping "
                "userprofile/provider/service PATCHes and keeping old contact %s",
                contact_id, deleted_contact_id,
            )
            return

    if userprofile_id and userprofile_payload:
        userprofile_ok = await _upsert_record(
            headers,
            f"{base_url}/{_ENTITY_USERPROFILES}({userprofile_id})",
            userprofile_payload,
            "DATAVERSE_PROVIDER_EDIT_USERPROFILE",
            userprofile_id,
        )
        if not userprofile_ok:
            logger.error(
                "DATAVERSE_PROVIDER_EDIT | ABORTED | userprofile %s failed — skipping "
                "provider/service PATCHes and keeping old contact %s",
                userprofile_id, deleted_contact_id,
            )
            return

    provider_ok = await _upsert_record(
        headers,
        f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})",
        provider_payload,
        "DATAVERSE_PROVIDER_EDIT_PROVIDER",
        provider_id,
    )

    service_results = await asyncio.gather(
        *(
            _upsert_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}({sid})",
                service_payload,
                "DATAVERSE_PROVIDER_EDIT_SERVICE",
                sid,
            )
            for sid in providerservices_ids
        ),
        return_exceptions=True,
    )
    for sid, result in zip(providerservices_ids, service_results):
        if isinstance(result, Exception):
            logger.error(
                "DATAVERSE_PROVIDER_EDIT | SERVICE_TASK_FAILED | id=%s | provider=%s | %s",
                sid, provider_id, result,
            )

    if deleted_contact_id and deleted_contact_id != contact_id:
        if not provider_ok:
            logger.error(
                "DATAVERSE_PROVIDER_EDIT | OLD_CONTACT_KEPT | provider %s PATCH failed, so "
                "contact %s is still bound to it in Dataverse — not deleting",
                provider_id, deleted_contact_id,
            )
        else:
            await _delete_record(
                headers,
                f"{base_url}/{_ENTITY_CONTACTS}({deleted_contact_id})",
                "DATAVERSE_PROVIDER_EDIT_OLD_CONTACT",
                deleted_contact_id,
            )
