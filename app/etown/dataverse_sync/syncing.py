"""Provider Records Dataverse Sync - mirrors /dataverse-sync, approval and review writes to the CRM.

Background Dataverse mirror that PATCHes existing provider, provider-service, userprofile and contact records with the verification, address, business-name, source, approval, review, mobile and soft-delete values Postgres already holds; Postgres stays authoritative and every call swallows its own errors.

Features:
    - Field names and types follow scripts/dataverse_schema.json: contact coordinates are numbers, provider ones strings
    - Verification Mirror: PATCH provider, providerservices, userprofile, and contact records
    - Both Directions: Writes verified or non-verified values from one boolean flag
    - Address Mirror: PATCH contact, provider, and provider-service address fields
    - Business-name Mirror: PATCH ets_businessname on the provider and its provider-service records
    - Source Mirror: PATCH ets_source on the provider and its provider-service records
    - Approval Mirror: PATCH ets_approved on providers from POST /provider/approve
    - Review Approval Mirror: PATCH the approved profile fields from an applied provider review
    - Mobile and Member-link Mirror: PATCH a provider's mobile number or member re-link
    - Soft-delete Mirror: PATCH ets_deleted and ets_deletiondate; nothing is deleted in the CRM
    - Partial Payloads: Omitted fields are left out, so Dataverse keeps its current values
    - OAuth Token Management: Acquires client-credentials tokens for Dataverse API auth
    - Transient Retry: Timeouts and connection errors retried with backoff
    - Invalid-property Recovery: A property Dataverse rejects is dropped and the PATCH retried
    - BackgroundTask Integration: Never raises, so an API response is never affected

Classes:
    No classes defined (module uses function-based sync utilities)

Functions:
    get_dataverse_token(): Acquire OAuth2 client-credentials token for Dataverse
    _build_dataverse_context(): Build the base URL and auth headers for Dataverse Web API calls
    _patch_record(): PATCH one Dataverse record, retrying transient errors and invalid properties
    sync_provider_verification_to_dataverse(): Mirror a provider verification change to Dataverse
    sync_provider_approval_to_dataverse(): Mirror POST /provider/approve's decision as ets_approved
    sync_address_to_dataverse(): Mirror a contact's address change to Dataverse
    _without_nones(): Drop None values so an omitted field never overwrites Dataverse with null
    sync_mobilenumber_to_dataverse(): Mirror a provider mobile-number change to Dataverse
    sync_member_link_to_dataverse(): Mirror a provider-to-member re-link to Dataverse
    sync_businessname_to_dataverse(): Mirror a provider business-name change to Dataverse
    sync_source_to_dataverse(): Mirror a provider source change to Dataverse
    sync_provider_review_approval_to_dataverse(): Mirror an applied review approval to Dataverse
    _query_ids(): Fetch record ids from a Dataverse OData query
    sync_provider_soft_delete_to_dataverse(): Mirror a provider soft-delete to Dataverse

Dependencies:
    - asyncio: Backoff sleeps between transient retries
    - httpx: Async HTTP client for Dataverse Web API calls
    - app.core.config: Dataverse API configuration and OAuth credentials
    - app.core.logging: Centralized application logging
    - app.etown.dataverse_sync.dataverse_sync_utils: Shared verification field values
    - app.etown.user.account_lifecycle_syncing: lifecycle flag encoding and date rendering
    - app.etown.provider.provider_syncing: _dataverse_bool() for Yes/No columns

Updates:
    04-Aug-2026: Created with the verifiedproviders mirror; added sync_address_to_dataverse() and sync_provider_delete_to_dataverse().
    06-Aug-2026: Added sync_mobilenumber_to_dataverse() and sync_member_link_to_dataverse().
    11-Aug-2026: Added sync_businessname_to_dataverse(), patching ets_businessname on provider and services.
    15-Sep-2026: Added sync_provider_approval_to_dataverse() for POST /provider/approve.
    17-Sep-2026: sync_provider_delete_to_dataverse() became sync_provider_soft_delete_to_dataverse(); removed _delete_record().
    22-Sep-2026: Added sync_provider_review_approval_to_dataverse() for the review approval endpoint.
    30-Sep-2026: sync_provider_review_approval_to_dataverse() mirrors interested as ets_interested.
"""

import asyncio
import datetime
import re
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.etown.dataverse_sync.dataverse_sync_utils import (
    NONVERIFIED_PROVIDERTYPE,
    NONVERIFIED_PUBLICSEARCHPROVIDER,
    VERIFIED_PROVIDERTYPE,
    VERIFIED_PUBLICSEARCHPROVIDER,
)
from app.etown.provider.provider_syncing import _dataverse_bool
from app.etown.user.account_lifecycle_syncing import (
    _CHOICE_TRUE,
    _D_DELETION,
    _F_DELETED,
    _to_dataverse_date,
)

_ENTITY_CONTACTS = "contacts"
_ENTITY_USERPROFILES = "ets_userprofiles"
_ENTITY_PROVIDERS = "ets_providers"
_ENTITY_PROVIDERSERVICES = "ets_providerserviceses"
_ENTITY_ETOWNPROVIDERS = "ets_etownproviderses"

_LOG = "DATAVERSE_PROVIDER_VERIFICATION"
_LOG_ADDRESS = "DATAVERSE_ADDRESS"
_LOG_DELETE = "DATAVERSE_PROVIDER_DELETE"
_LOG_MOBILE = "DATAVERSE_PROVIDER_MOBILE"
_LOG_LINK_MEMBER = "DATAVERSE_PROVIDER_MEMBER_LINK"
_LOG_BUSINESSNAME = "DATAVERSE_PROVIDER_BUSINESSNAME"
_LOG_APPROVAL = "DATAVERSE_PROVIDER_APPROVAL"
_LOG_SOURCE = "DATAVERSE_PROVIDER_SOURCE"
_LOG_REVIEW_APPROVAL = "DATAVERSE_PROVIDER_REVIEW_APPROVAL"

_ETS_NAME_MAX_LEN = 100

_APPROVED_YES = 1
_APPROVED_NO = 0
_INVALID_PROP_PATTERN = re.compile(r"Invalid property '([^']+)' was found")

_MAX_NET_RETRIES = 3
_RETRY_DELAYS = [2.0, 5.0, 10.0]


async def get_dataverse_token() -> str:
    """Acquire an OAuth2 client-credentials access token for Dataverse."""
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


async def _patch_record(
    headers: Dict[str, str],
    url: str,
    payload: Dict[str, Any],
    label: str,
    record_id: str,
    log_prefix: str = _LOG,
) -> bool:
    """PATCH one Dataverse record, retrying network errors and dropping rejected properties; never raise.

    Args:
        headers: Dataverse request headers including the bearer token.
        url: Full record URL.
        payload: Attributes to write.
        label: Log label identifying the record kind.
        record_id: Record GUID, for logs.
        log_prefix: Log prefix identifying the sync flow.

    Returns:
        True when Dataverse accepted the PATCH, False on any failure.
    """
    clean_payload = dict(payload)
    net_attempts = 0

    for _ in range(len(clean_payload) + 1):
        if not clean_payload:
            logger.error(f"{log_prefix} | {label} | FAILED | id={record_id} | no writable properties left")
            return False
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.patch(url, json=clean_payload, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            net_attempts += 1
            if net_attempts <= _MAX_NET_RETRIES:
                delay = _RETRY_DELAYS[net_attempts - 1]
                logger.warning(
                    f"{log_prefix} | {label} | TRANSIENT_ERROR | id={record_id} "
                    f"| attempt={net_attempts}/{_MAX_NET_RETRIES} | retrying in {delay:.0f}s | {exc}"
                )
                await asyncio.sleep(delay)
                continue
            logger.error(
                f"{log_prefix} | {label} | ERROR | id={record_id} "
                f"| exhausted {_MAX_NET_RETRIES} retries | {exc}",
                exc_info=True,
            )
            return False
        except Exception as exc:
            logger.error(
                f"{log_prefix} | {label} | ERROR | id={record_id} | {exc}", exc_info=True
            )
            return False

        if response.status_code in (200, 204):
            logger.info(f"{log_prefix} | {label} | SUCCESS | id={record_id}")
            return True

        if response.status_code == 400:
            match = _INVALID_PROP_PATTERN.search(response.text)
            if match and match.group(1) in clean_payload:
                bad_prop = match.group(1)
                logger.warning(
                    f"{log_prefix} | {label} | DROP_INVALID_PROP | id={record_id} | prop={bad_prop}"
                )
                del clean_payload[bad_prop]
                continue

        logger.error(
            f"{log_prefix} | {label} | FAILED | id={record_id} "
            f"| status={response.status_code} | body={response.text}"
        )
        return False

    logger.error(
        f"{log_prefix} | {label} | FAILED | id={record_id} | exhausted invalid-prop retries"
    )
    return False


async def sync_provider_verification_to_dataverse(
    providerid: str,
    verified: bool,
    userprofileid: Optional[str] = None,
    contactid: Optional[str] = None,
    providerservices_ids: Optional[List[str]] = None,
) -> None:
    """Mirror a provider verification change to the provider, service, userprofile and contact records in Dataverse.

    Args:
        providerid: Provider GUID, also the ets_providers record id.
        verified: True writes the verified values, False the non-verified ones.
        userprofileid: User profile GUID to flag, when the provider has one.
        contactid: Contact GUID to flag, when the provider row carries one.
        providerservices_ids: ets_providerserviceses record ids updated in Postgres.
    """
    if not providerid:
        logger.warning(f"{_LOG} | SKIP | missing providerid")
        return

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG} | SKIP | DATAVERSE_URL not configured | providerid={providerid}")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            f"{_LOG} | FAILED_TOKEN | providerid={providerid} | {exc}", exc_info=True
        )
        return

    base_url, headers = _build_dataverse_context(access_token)

    provider_fields = {
        "ets_providertype": VERIFIED_PROVIDERTYPE if verified else NONVERIFIED_PROVIDERTYPE,
        "ets_publicsearchprovider": (
            VERIFIED_PUBLICSEARCHPROVIDER if verified else NONVERIFIED_PUBLICSEARCHPROVIDER
        ),
    }

    await _patch_record(
        headers,
        f"{base_url}/{_ENTITY_PROVIDERS}({providerid})",
        provider_fields,
        "PROVIDER",
        providerid,
    )

    for ps_id in providerservices_ids or []:
        if ps_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}({ps_id})",
                provider_fields,
                "PROVIDERSERVICE",
                ps_id,
            )

    if userprofileid:
        await _patch_record(
            headers,
            f"{base_url}/{_ENTITY_USERPROFILES}({userprofileid})",
            {"ets_verified": verified},
            "USERPROFILE",
            userprofileid,
        )

    if contactid:
        await _patch_record(
            headers,
            f"{base_url}/{_ENTITY_CONTACTS}({contactid})",
            {"ets_verified": verified},
            "CONTACT",
            contactid,
        )

    logger.info(
        f"{_LOG} | COMPLETE | providerid={providerid} | verified={verified} "
        f"| services={len(providerservices_ids or [])} "
        f"| userprofileid={userprofileid or '-'} | contactid={contactid or '-'}"
    )


async def sync_provider_approval_to_dataverse(
    providerids: List[str],
    approve: bool,
) -> None:
    """Mirror an approval decision to Dataverse by PATCHing ets_approved to 1 or 0 on each provider."""
    ids = [pid for pid in (providerids or []) if pid]
    if not ids:
        logger.warning(f"{_LOG_APPROVAL} | SKIP | no providerids")
        return

    if not settings.DATAVERSE_URL:
        logger.warning(
            f"{_LOG_APPROVAL} | SKIP | DATAVERSE_URL not configured | providers={len(ids)}"
        )
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            f"{_LOG_APPROVAL} | FAILED_TOKEN | providers={len(ids)} | {exc}", exc_info=True
        )
        return

    base_url, headers = _build_dataverse_context(access_token)
    payload = {"ets_approved": _APPROVED_YES if approve else _APPROVED_NO}

    patched = 0
    for providerid in ids:
        if await _patch_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({providerid})",
            dict(payload),
            "PROVIDER",
            providerid,
            log_prefix=_LOG_APPROVAL,
        ):
            patched += 1

    logger.info(
        f"{_LOG_APPROVAL} | COMPLETE | approve={approve} | "
        f"ets_approved={payload['ets_approved']} | {patched}/{len(ids)} provider(s) patched"
    )


async def sync_address_to_dataverse(
    contactid: str,
    address: Optional[str] = None,
    city: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    areaname: Optional[str] = None,
    townname: Optional[str] = None,
    provider_ids: Optional[List[str]] = None,
    providerservices_ids: Optional[List[str]] = None,
) -> None:
    """Mirror an address change to the contact, provider and provider-service records in Dataverse, skipping omitted fields.

    Args:
        contactid: Contact GUID (also the contacts record id).
        address: Street address.
        city: City name.
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.
        areaname: Area / locality name.
        townname: Town name (provider only).
        provider_ids: ets_providers record ids to update.
        providerservices_ids: ets_providerserviceses record ids to update.
    """
    if not contactid:
        logger.warning(f"{_LOG_ADDRESS} | SKIP | missing contactid")
        return

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG_ADDRESS} | SKIP | DATAVERSE_URL not configured | contactid={contactid}")
        return

    lat_str = str(latitude) if latitude is not None else None
    lng_str = str(longitude) if longitude is not None else None

    contact_fields = _without_nones({
        "address1_line1": address,
        "address1_city": city,
        "address1_latitude": latitude,
        "address1_longitude": longitude,
    })
    provider_fields = _without_nones({
        "ets_address": address,
        "ets_city": city,
        "ets_latitude": lat_str,
        "ets_longitude": lng_str,
        "ets_areaname": areaname,
        "ets_townname": townname,
    })
    service_fields = _without_nones({
        "ets_address": address,
        "ets_city": city,
        "ets_servicelatitude": lat_str,
        "ets_servicelongitude": lng_str,
        "ets_areaname": areaname,
    })

    if not (contact_fields or provider_fields or service_fields):
        logger.warning(f"{_LOG_ADDRESS} | SKIP | no fields to write | contactid={contactid}")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            f"{_LOG_ADDRESS} | FAILED_TOKEN | contactid={contactid} | {exc}", exc_info=True
        )
        return

    base_url, headers = _build_dataverse_context(access_token)

    if contact_fields:
        await _patch_record(
            headers,
            f"{base_url}/{_ENTITY_CONTACTS}({contactid})",
            contact_fields,
            "CONTACT_ADDRESS",
            contactid,
            log_prefix=_LOG_ADDRESS,
        )

    if provider_fields:
        for p_id in provider_ids or []:
            if p_id:
                await _patch_record(
                    headers,
                    f"{base_url}/{_ENTITY_PROVIDERS}({p_id})",
                    provider_fields,
                    "PROVIDER_ADDRESS",
                    p_id,
                    log_prefix=_LOG_ADDRESS,
                )

    if service_fields:
        for ps_id in providerservices_ids or []:
            if ps_id:
                await _patch_record(
                    headers,
                    f"{base_url}/{_ENTITY_PROVIDERSERVICES}({ps_id})",
                    service_fields,
                    "PROVIDERSERVICE_ADDRESS",
                    ps_id,
                    log_prefix=_LOG_ADDRESS,
                )

    logger.info(
        f"{_LOG_ADDRESS} | COMPLETE | contactid={contactid} "
        f"| providers={len(provider_ids or [])} | services={len(providerservices_ids or [])}"
    )


def _without_nones(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Drop None values so an omitted field never overwrites a Dataverse value with null."""
    return {k: v for k, v in payload.items() if v is not None}


async def sync_mobilenumber_to_dataverse(
    mobilenumber: str,
    provider_ids: Optional[List[str]] = None,
    userprofile_ids: Optional[List[str]] = None,
    contact_ids: Optional[List[str]] = None,
    providerservices_ids: Optional[List[str]] = None,
    update_userlogin: bool = False,
) -> None:
    """Mirror a provider mobile-number change to each entity's own mobile field in Dataverse.

    Args:
        mobilenumber: The new mobile number, exactly as written to Postgres.
        provider_ids: ets_providers record ids to update.
        userprofile_ids: ets_userprofiles record ids to update.
        contact_ids: contacts record ids to update.
        providerservices_ids: ets_providerserviceses record ids to update.
        update_userlogin: True to also write ets_userlogin on the contacts.
    """
    number = str(mobilenumber or "").strip()
    if not number:
        logger.warning(f"{_LOG_MOBILE} | SKIP | missing mobilenumber")
        return

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG_MOBILE} | SKIP | DATAVERSE_URL not configured")
        return

    if not any((provider_ids, userprofile_ids, contact_ids, providerservices_ids)):
        logger.warning(f"{_LOG_MOBILE} | SKIP | no record ids to write")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(f"{_LOG_MOBILE} | FAILED_TOKEN | {exc}", exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    contact_fields: Dict[str, Any] = {"mobilephone": number}
    if update_userlogin:
        contact_fields["ets_userlogin"] = number

    for c_id in contact_ids or []:
        if c_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_CONTACTS}({c_id})",
                contact_fields,
                "CONTACT_MOBILE",
                c_id,
                log_prefix=_LOG_MOBILE,
            )

    for up_id in userprofile_ids or []:
        if up_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_USERPROFILES}({up_id})",
                {"ets_mobilenumber": number},
                "USERPROFILE_MOBILE",
                up_id,
                log_prefix=_LOG_MOBILE,
            )

    for p_id in provider_ids or []:
        if p_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERS}({p_id})",
                {"ets_mobilenumber": number},
                "PROVIDER_MOBILE",
                p_id,
                log_prefix=_LOG_MOBILE,
            )

    for ps_id in providerservices_ids or []:
        if ps_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}({ps_id})",
                {"ets_mobile": number},
                "PROVIDERSERVICE_MOBILE",
                ps_id,
                log_prefix=_LOG_MOBILE,
            )

    logger.info(
        f"{_LOG_MOBILE} | COMPLETE | contacts={len(contact_ids or [])} "
        f"| userprofiles={len(userprofile_ids or [])} | providers={len(provider_ids or [])} "
        f"| services={len(providerservices_ids or [])} | userlogin={update_userlogin}"
    )


async def sync_member_link_to_dataverse(
    mobilenumber: str,
    contactid: str,
    provider_ids: Optional[List[str]] = None,
    providerservices_ids: Optional[List[str]] = None,
) -> None:
    """Mirror a provider-to-member re-link: write the new mobile and rebind provider contact lookups in Dataverse.

    Args:
        mobilenumber: The new mobile number, exactly as written to Postgres.
        contactid: The member's contact GUID the listing was re-pointed at.
        provider_ids: ets_providers record ids to update.
        providerservices_ids: ets_providerserviceses record ids to update.
    """
    number = str(mobilenumber or "").strip()
    if not number or not contactid:
        logger.warning(
            f"{_LOG_LINK_MEMBER} | SKIP | missing mobilenumber or contactid"
        )
        return

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG_LINK_MEMBER} | SKIP | DATAVERSE_URL not configured")
        return

    if not any((provider_ids, providerservices_ids)):
        logger.warning(f"{_LOG_LINK_MEMBER} | SKIP | no record ids to write")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(f"{_LOG_LINK_MEMBER} | FAILED_TOKEN | {exc}", exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    provider_fields = {
        "ets_mobilenumber": number,
        "ets_Contact_id@odata.bind": f"/{_ENTITY_CONTACTS}({contactid})",
        "ets_Names@odata.bind": f"/{_ENTITY_CONTACTS}({contactid})",
    }

    for p_id in provider_ids or []:
        if p_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERS}({p_id})",
                provider_fields,
                "PROVIDER_MEMBER_LINK",
                p_id,
                log_prefix=_LOG_LINK_MEMBER,
            )

    for ps_id in providerservices_ids or []:
        if ps_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}({ps_id})",
                {"ets_mobile": number},
                "PROVIDERSERVICE_MEMBER_LINK",
                ps_id,
                log_prefix=_LOG_LINK_MEMBER,
            )

    logger.info(
        f"{_LOG_LINK_MEMBER} | COMPLETE | contactid={contactid} "
        f"| providers={len(provider_ids or [])} | services={len(providerservices_ids or [])}"
    )


async def sync_businessname_to_dataverse(
    businessname: str,
    provider_ids: Optional[List[str]] = None,
    providerservices_ids: Optional[List[str]] = None,
) -> None:
    """Mirror a provider business-name change to Dataverse, also setting ets_name on provider records."""
    name = str(businessname or "").strip()
    if not name:
        logger.warning(f"{_LOG_BUSINESSNAME} | SKIP | missing businessname")
        return

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG_BUSINESSNAME} | SKIP | DATAVERSE_URL not configured")
        return

    if not any((provider_ids, providerservices_ids)):
        logger.warning(f"{_LOG_BUSINESSNAME} | SKIP | no record ids to write")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(f"{_LOG_BUSINESSNAME} | FAILED_TOKEN | {exc}", exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    for p_id in provider_ids or []:
        if p_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERS}({p_id})",
                {"ets_businessname": name, "ets_name": name[:_ETS_NAME_MAX_LEN]},
                "PROVIDER_BUSINESSNAME",
                p_id,
                log_prefix=_LOG_BUSINESSNAME,
            )

    for ps_id in providerservices_ids or []:
        if ps_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}({ps_id})",
                {"ets_businessname": name},
                "PROVIDERSERVICE_BUSINESSNAME",
                ps_id,
                log_prefix=_LOG_BUSINESSNAME,
            )

    logger.info(
        f"{_LOG_BUSINESSNAME} | COMPLETE | business='{name}' "
        f"| providers={len(provider_ids or [])} | services={len(providerservices_ids or [])}"
    )


async def sync_source_to_dataverse(
    source: str,
    provider_ids: Optional[List[str]] = None,
    providerservices_ids: Optional[List[str]] = None,
) -> None:
    """Mirror a provider source change to ets_source on provider and provider-service records in Dataverse."""
    value = str(source or "").strip()
    if not value:
        logger.warning(f"{_LOG_SOURCE} | SKIP | missing source")
        return

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG_SOURCE} | SKIP | DATAVERSE_URL not configured")
        return

    if not any((provider_ids, providerservices_ids)):
        logger.warning(f"{_LOG_SOURCE} | SKIP | no record ids to write")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(f"{_LOG_SOURCE} | FAILED_TOKEN | {exc}", exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    for p_id in provider_ids or []:
        if p_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERS}({p_id})",
                {"ets_source": value},
                "PROVIDER_SOURCE",
                p_id,
                log_prefix=_LOG_SOURCE,
            )

    for ps_id in providerservices_ids or []:
        if ps_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}({ps_id})",
                {"ets_source": value},
                "PROVIDERSERVICE_SOURCE",
                ps_id,
                log_prefix=_LOG_SOURCE,
            )

    logger.info(
        f"{_LOG_SOURCE} | COMPLETE | source='{value}' "
        f"| providers={len(provider_ids or [])} | services={len(providerservices_ids or [])}"
    )


async def sync_provider_review_approval_to_dataverse(
    providerid: str,
    contactid: Optional[str] = None,
    providerservices_ids: Optional[List[str]] = None,
    businessname: Optional[str] = None,
    businesscategory: Optional[str] = None,
    servicecategory: Optional[str] = None,
    servicetype: Optional[str] = None,
    description: Optional[str] = None,
    services: Optional[str] = None,
    marketing_person_name: Optional[str] = None,
    businesslogo: Optional[str] = None,
    email: Optional[str] = None,
    mobilenumber: Optional[str] = None,
    alternatemobile: Optional[str] = None,
    formattedaddress: Optional[str] = None,
    additionaladdressinfo: Optional[str] = None,
    arealocation: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    interested: Optional[str] = None,
) -> None:
    """Mirror an applied provider-review approval to the provider, provider-service and contact records in Dataverse.

    Args:
        providerid: Provider GUID; also the ets_providers record id.
        contactid: Contact GUID read off the provider row; also the contacts record id.
        providerservices_ids: etown_providerservices ids; also the ets_providerserviceses record ids.
        businessname: Business name.
        businesscategory: Business category.
        servicecategory: Service category (service rows only).
        servicetype: Service type (provider only).
        description: Business description; ets_servicedescription on the service rows.
        services: Services blob (service rows only).
        marketing_person_name: Field agent, written to ets_virtualid.
        businesslogo: Logo URL, written to ets_serviceimageurl on both entities.
        email: Email address.
        mobilenumber: Provider mobile number; contacts holds it as mobilephone.
        alternatemobile: Secondary mobile number.
        formattedaddress: Street address.
        additionaladdressinfo: Second address line.
        arealocation: Area or locality; ets_areaname on both provider entities.
        city: City name.
        state: Address state; ets_statecode on providers, address1_stateorprovince on the contact.
        latitude: Latitude in degrees; stringified for provider entities, numeric on the contact.
        longitude: Longitude in degrees; stringified for provider entities, numeric on the contact.
        interested: 'true' or 'false', written to the provider's ets_interested Yes/No column.
    """
    if not providerid:
        logger.warning(f"{_LOG_REVIEW_APPROVAL} | SKIP | missing providerid")
        return

    if not settings.DATAVERSE_URL:
        logger.warning(
            f"{_LOG_REVIEW_APPROVAL} | SKIP | DATAVERSE_URL not configured | providerid={providerid}"
        )
        return

    lat_str = str(latitude) if latitude is not None else None
    lng_str = str(longitude) if longitude is not None else None

    provider_fields = _without_nones({
        "ets_businessname": businessname,
        "ets_businesscategory": businesscategory,
        "ets_servicetype": servicetype,
        "ets_description": description,
        "ets_virtualid": marketing_person_name,
        "ets_serviceimageurl": businesslogo,
        "ets_email": email,
        "ets_mobilenumber": mobilenumber,
        "ets_alternatemobile": alternatemobile,
        "ets_address": formattedaddress,
        "ets_additionaladdressinfo": additionaladdressinfo,
        "ets_areaname": arealocation,
        "ets_city": city,
        "ets_statecode": state,
        "ets_latitude": lat_str,
        "ets_longitude": lng_str,
        "ets_interested": _dataverse_bool(interested),
    })
    service_fields = _without_nones({
        "ets_businessname": businessname,
        "ets_businesscategory": businesscategory,
        "ets_servicecategory": servicecategory,
        "ets_servicedescription": description,
        "ets_services": services,
        "ets_serviceimageurl": businesslogo,
        "ets_email": email,
        "ets_mobile": mobilenumber,
        "ets_address": formattedaddress,
        "ets_additionaladdressinfo": additionaladdressinfo,
        "ets_areaname": arealocation,
        "ets_city": city,
        "ets_statecode": state,
        "ets_servicelatitude": lat_str,
        "ets_servicelongitude": lng_str,
    })
    contact_fields = _without_nones({
        "emailaddress1": email,
        "mobilephone": mobilenumber,
        "ets_alternatemobile": alternatemobile,
        "address1_line1": formattedaddress,
        "address2_line1": additionaladdressinfo,
        "ets_additionaladdressinfo": additionaladdressinfo,
        "address1_city": city,
        "ets_city": city,
        "address1_stateorprovince": state,
        "address1_latitude": latitude,
        "address1_longitude": longitude,
    })

    if not (provider_fields or service_fields or contact_fields):
        logger.warning(
            f"{_LOG_REVIEW_APPROVAL} | SKIP | no fields to write | providerid={providerid}"
        )
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            f"{_LOG_REVIEW_APPROVAL} | FAILED_TOKEN | providerid={providerid} | {exc}",
            exc_info=True,
        )
        return

    base_url, headers = _build_dataverse_context(access_token)

    if provider_fields:
        await _patch_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({providerid})",
            provider_fields,
            "PROVIDER",
            providerid,
            log_prefix=_LOG_REVIEW_APPROVAL,
        )

    services_patched = 0
    if service_fields:
        for ps_id in providerservices_ids or []:
            if ps_id and await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}({ps_id})",
                dict(service_fields),
                "PROVIDERSERVICE",
                ps_id,
                log_prefix=_LOG_REVIEW_APPROVAL,
            ):
                services_patched += 1

    if contact_fields and contactid:
        await _patch_record(
            headers,
            f"{base_url}/{_ENTITY_CONTACTS}({contactid})",
            contact_fields,
            "CONTACT",
            contactid,
            log_prefix=_LOG_REVIEW_APPROVAL,
        )

    logger.info(
        f"{_LOG_REVIEW_APPROVAL} | COMPLETE | providerid={providerid} "
        f"| contact={'yes' if (contact_fields and contactid) else 'no'} "
        f"| services={services_patched}/{len(providerservices_ids or [])}"
    )


async def _query_ids(
    headers: Dict[str, str],
    url: str,
    id_field: str,
    label: str,
    log_prefix: str,
) -> List[str]:
    """Return the id_field values from an OData query, or [] on any failure; never raise."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers)
    except Exception as exc:
        logger.error(f"{log_prefix} | {label}_QUERY | ERROR | {exc}", exc_info=True)
        return []

    if response.status_code != 200:
        logger.error(
            f"{log_prefix} | {label}_QUERY | FAILED "
            f"| status={response.status_code} | body={response.text}"
        )
        return []

    return [r[id_field] for r in response.json().get("value", []) if r.get(id_field)]


async def sync_provider_soft_delete_to_dataverse(
    provider_ids: List[str],
    deletiondate: Optional[datetime.datetime] = None,
    userprofile_ids: Optional[List[str]] = None,
    providerservices_ids: Optional[List[str]] = None,
) -> None:
    """Mirror a provider soft-delete by flagging ets_deleted and ets_deletiondate on its Dataverse records; contacts stay untouched.

    Args:
        provider_ids: ets_providers record ids to flag.
        deletiondate: The IST stamp PostgreSQL wrote; None falls back to today in IST.
        userprofile_ids: ets_userprofiles record ids to flag, only those Postgres flagged.
        providerservices_ids: Known ets_providerserviceses record ids to flag.
    """
    provider_ids = [p for p in (provider_ids or []) if p]
    if not provider_ids:
        logger.warning(f"{_LOG_DELETE} | SKIP | no provider ids")
        return

    if not settings.DATAVERSE_URL:
        logger.warning(f"{_LOG_DELETE} | SKIP | DATAVERSE_URL not configured")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(f"{_LOG_DELETE} | FAILED_TOKEN | {exc}", exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)
    payload = {_F_DELETED: _CHOICE_TRUE, _D_DELETION: _to_dataverse_date(deletiondate)}

    for provider_id in provider_ids:
        service_ids = set(s for s in (providerservices_ids or []) if s)
        service_ids.update(
            await _query_ids(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}"
                f"?$filter=_ets_providerid_value eq {provider_id}"
                f"&$select=ets_providerservicesid",
                "ets_providerservicesid",
                "PROVIDERSERVICE",
                _LOG_DELETE,
            )
        )
        for service_id in service_ids:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_PROVIDERSERVICES}({service_id})",
                payload,
                "PROVIDERSERVICE",
                service_id,
                _LOG_DELETE,
            )

        for link_id in await _query_ids(
            headers,
            f"{base_url}/{_ENTITY_ETOWNPROVIDERS}"
            f"?$filter=_ets_providerid_value eq {provider_id}"
            f"&$select=ets_etownprovidersid",
            "ets_etownprovidersid",
            "ETOWNPROVIDER",
            _LOG_DELETE,
        ):
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_ETOWNPROVIDERS}({link_id})",
                payload,
                "ETOWNPROVIDER",
                link_id,
                _LOG_DELETE,
            )

        await _patch_record(
            headers,
            f"{base_url}/{_ENTITY_PROVIDERS}({provider_id})",
            payload,
            "PROVIDER",
            provider_id,
            _LOG_DELETE,
        )

    for userprofile_id in (userprofile_ids or []):
        if userprofile_id:
            await _patch_record(
                headers,
                f"{base_url}/{_ENTITY_USERPROFILES}({userprofile_id})",
                payload,
                "USERPROFILE",
                userprofile_id,
                _LOG_DELETE,
            )

    logger.info(
        f"{_LOG_DELETE} | COMPLETE | providers={len(provider_ids)} "
        f"| userprofiles={len([u for u in (userprofile_ids or []) if u])} | contacts_kept"
    )
