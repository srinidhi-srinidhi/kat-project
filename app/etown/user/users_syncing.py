"""User Dataverse Sync - mirrors user writes from Postgres to the CRM.

Background Dataverse mirror for contact, userprofile, member, provider, active-user-log and user-location writes, keyed on the Postgres UUIDs; Postgres stays authoritative and every call swallows its own errors.

Features:
    - OAuth2 Client-Credentials Auth: get_dataverse_token() fetches a fresh Azure AD token per call
    - Upsert-by-Key: PATCH with If-None-Match: * keeps Dataverse rows on the PostgreSQL UUID
    - Best-Effort Background Sync: every sync function logs and swallows its own exceptions
    - Picklist Mapping: to_fromsocialmedia_choice() maps the flag to 5=social, 10=default
    - Plan Fields: a new provider's inherited plan fields travel in the create-only provider PATCH
    - Entity Coverage: contacts, userprofiles, members, providers, activeuserslogs, userlocationdetailses

Classes:
    No classes defined (module exposes only async sync helpers plus small pure-function utilities)

Functions:
    to_fromsocialmedia_choice(): Map the PostgreSQL from_socialmedia flag onto the ets_fromsocialmedia picklist
    get_dataverse_token(): Acquire an OAuth2 client-credentials token for Dataverse
    sync_userprofile_lastlogin_to_dataverse(): Sync lastsignupstatus/membersignup for a user profile
    sync_contact_lasttriggeredtime_to_dataverse(): PATCH ets_lasttriggeredtime onto a contact record
    _to_dataverse_datetime(): Format a datetime as Dataverse-friendly ISO-8601 with a trailing Z
    _escape_odata_string(): Escape single quotes for a safe OData $filter string literal
    sync_activeuserslog_to_dataverse(): Upsert an ets_activeuserslogs row (lookup by mobile, then PATCH or POST)
    sync_userlocation_to_dataverse(): Add/edit/delete a user's location record in ets_userlocationdetailses
    sync_switch_profile_to_dataverse(): Create userprofile + member or provider records in Dataverse
    sync_registration_to_dataverse(): Sync new contact + userprofile + member/provider after registration
    sync_member_promotion_to_dataverse(): PATCH ets_verified=True on contact + userprofile(s) + member(s) for on-login member promotion
    sync_member_profile_update_to_dataverse(): Sync a member profile edit's changed fields to contact/userprofile/member
    sync_address_update_to_dataverse(): Sync a contact's address/geo field updates

Dependencies:
    - httpx: Async HTTP client used for every Dataverse Web API call
    - app.core.config: settings (Dataverse tenant/client credentials and DATAVERSE_URL)
    - app.core.logging: Centralized logging infrastructure
    - app.etown.plans.plans_syncing: plan_fields_for_dataverse() for provider plan fields

Updates:
    02-Jul-2026: Added sync_switch_profile_to_dataverse for Switch Profile API migration
    03-Jul-2026: Switch-profile sync uses If-None-Match; added registration and member-profile syncs.
    14-Jul-2026: Added sync_member_promotion_to_dataverse(), mirroring the member verification promotion.
    05-Aug-2026: sync_registration_to_dataverse() takes claimed=True; registration and switch-profile payloads send ets_verified=True.
    06-Aug-2026: Added to_fromsocialmedia_choice() for the ets_fromsocialmedia picklist.
    12-Aug-2026: sync_member_profile_update_to_dataverse() normalises birthdate to YYYY-MM-DD or omits it.
    24-Aug-2026: Registration and switch-profile provider payloads carry the inherited plan fields.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import asyncio
import datetime
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.etown.plans.plans_syncing import plan_fields_for_dataverse

DATAVERSE_ENTITY = "ets_userprofiles"
CONTACT_ENTITY = "contacts"
ACTIVEUSERSLOG_ENTITY = "ets_activeuserslogs"

FROMSOCIALMEDIA_CHOICE_TRUE = 5
FROMSOCIALMEDIA_CHOICE_FALSE = 10
_FROMSOCIALMEDIA_TRUE_VALUES = {"true", "1", "yes"}


def to_fromsocialmedia_choice(value: Any) -> int:
    """Map the stored from_socialmedia flag onto the contacts.ets_fromsocialmedia picklist.

    Args:
        value: The PostgreSQL from_socialmedia value (text "true"/"false", or a bool).

    Returns:
        int: 5 when the signup came from social media, otherwise 10 (the Dataverse default).
    """
    if isinstance(value, bool):
        return FROMSOCIALMEDIA_CHOICE_TRUE if value else FROMSOCIALMEDIA_CHOICE_FALSE
    if value is None:
        return FROMSOCIALMEDIA_CHOICE_FALSE
    return (
        FROMSOCIALMEDIA_CHOICE_TRUE
        if str(value).strip().lower() in _FROMSOCIALMEDIA_TRUE_VALUES
        else FROMSOCIALMEDIA_CHOICE_FALSE
    )


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


async def sync_userprofile_lastlogin_to_dataverse(
    user_profile_id: str,
    lastsignup_status: Optional[int],
    membersignup_status: Optional[str],
) -> None:
    """PATCH a Dataverse userprofile's last-signup and member-signup statuses, logging instead of raising."""
    if not user_profile_id:
        logger.warning("[lastlogin_sync] Skipping sync because user_profile_id is empty")
        return

    try:
        token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[lastlogin_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        endpoint = f"{base_url}/api/data/v9.2/{DATAVERSE_ENTITY}({user_profile_id})"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "If-Match": "*",
        }
        payload = {
            "ets_lastsignupstatus": lastsignup_status,
            "ets_membersignup": membersignup_status,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(endpoint, headers=headers, json=payload)
            response.raise_for_status()

        logger.info(
            f"[lastlogin_sync] Synced to Dataverse | user_profile_id={user_profile_id} "
            f"| lastsignup_status={lastsignup_status} "
            f"| membersignup_status={membersignup_status}"
        )
    except Exception as e:
        logger.error(
            f"[lastlogin_sync] Failed Dataverse sync | user_profile_id={user_profile_id} | error={e}",
            exc_info=True,
        )


async def sync_contact_lasttriggeredtime_to_dataverse(
    contact_id: str,
    last_triggered_time: Optional[datetime.datetime],
) -> None:
    """PATCH a Dataverse contact's ets_lasttriggeredtime as a UTC timestamp, logging instead of raising."""
    if not contact_id:
        logger.warning("[contact_lasttriggered_sync] Skipping sync because contact_id is empty")
        return

    if last_triggered_time is None:
        logger.warning(
            f"[contact_lasttriggered_sync] Skipping sync because last_triggered_time is empty | contact_id={contact_id}"
        )
        return

    try:
        token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[contact_lasttriggered_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        timestamp_str = (
            last_triggered_time.isoformat()
            if hasattr(last_triggered_time, "isoformat")
            else str(last_triggered_time)
        )
        if not timestamp_str.endswith("Z"):
            timestamp_str = f"{timestamp_str}Z"

        endpoint = f"{base_url}/api/data/v9.2/{CONTACT_ENTITY}({contact_id})"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "If-Match": "*",
        }
        payload = {
            "ets_lasttriggeredtime": timestamp_str,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(endpoint, headers=headers, json=payload)
            response.raise_for_status()

        logger.info(
            f"[contact_lasttriggered_sync] Synced to Dataverse | contact_id={contact_id} "
            f"| ets_lasttriggeredtime={timestamp_str}"
        )
    except Exception as e:
        logger.error(
            f"[contact_lasttriggered_sync] Failed Dataverse sync | contact_id={contact_id} | error={e}",
            exc_info=True,
        )


LOCATION_ENTITY = "ets_userlocationdetailses"


def _to_dataverse_datetime(value: Optional[datetime.datetime]) -> str:
    """Return the datetime, or now in UTC when None, as an ISO string ending in Z."""
    raw = value.isoformat() if value is not None and hasattr(value, "isoformat") else None
    timestamp_str = raw or datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not timestamp_str.endswith("Z"):
        timestamp_str = f"{timestamp_str}Z"
    return timestamp_str


def _escape_odata_string(value: str) -> str:
    """Escape single quotes for safe OData $filter string literals."""
    return value.replace("'", "''")


async def sync_activeuserslog_to_dataverse(
    contact_id: str,
    mobilenumber: str,
    createdon: Optional[datetime.datetime],
    action: str,
) -> None:
    """Mirror an active-user log row to Dataverse, patching the latest row for the mobile or creating one.

    Args:
        contact_id: Contact identifier stored in ets_contactid.
        mobilenumber: Mobile number stored in ets_mobilephone.
        createdon: Last active timestamp from DB row.
        action: 'inserted' or 'updated' from local upsert result.
    """
    if not contact_id or not mobilenumber:
        logger.warning(
            "[activeuserslog_sync] Skipping sync because required values are missing "
            f"| contact_id={contact_id} | mobilenumber={mobilenumber}"
        )
        return

    try:
        token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[activeuserslog_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        base_api = f"{base_url}/api/data/v9.2"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        timestamp_str = _to_dataverse_datetime(createdon)
        payload = {
            "ets_contactid": contact_id,
            "ets_mobilephone": mobilenumber,
            "ets_lastactivedate": timestamp_str,
        }

        escaped_mobile = _escape_odata_string(mobilenumber)
        query_url = (
            f"{base_api}/{ACTIVEUSERSLOG_ENTITY}"
            f"?$select=ets_activeuserslogid,ets_mobilephone"
            f"&$filter=ets_mobilephone eq '{escaped_mobile}'"
            "&$orderby=modifiedon desc"
            "&$top=1"
        )

        async with httpx.AsyncClient(timeout=15.0) as client:
            lookup_response = await client.get(query_url, headers=headers)
            lookup_response.raise_for_status()
            existing_rows = (lookup_response.json() or {}).get("value", [])
            existing_id = existing_rows[0].get("ets_activeuserslogid") if existing_rows else None

            if action == "updated" and existing_id:
                patch_url = f"{base_api}/{ACTIVEUSERSLOG_ENTITY}({existing_id})"
                patch_headers = dict(headers)
                patch_headers["If-Match"] = "*"
                patch_response = await client.patch(patch_url, headers=patch_headers, json=payload)
                if patch_response.status_code == 204:
                    logger.info(
                        "[activeuserslog_sync] Updated Dataverse log "
                        f"| mobile={mobilenumber} | ets_activeuserslogid={existing_id}"
                    )
                    return

                logger.error(
                    "[activeuserslog_sync] PATCH failed, falling back to POST "
                    f"| status={patch_response.status_code} | body={patch_response.text}"
                )

            create_url = f"{base_api}/{ACTIVEUSERSLOG_ENTITY}"
            create_response = await client.post(create_url, headers=headers, json=payload)
            if create_response.status_code in (200, 201, 204):
                logger.info(
                    "[activeuserslog_sync] Created Dataverse log "
                    f"| action={action} | mobile={mobilenumber} | ets_lastactivedate={timestamp_str}"
                )
                return

            logger.error(
                "[activeuserslog_sync] POST failed "
                f"| action={action} | status={create_response.status_code} | body={create_response.text}"
            )
    except Exception as e:
        logger.error(
            "[activeuserslog_sync] Failed Dataverse sync "
            f"| action={action} | contact_id={contact_id} | mobilenumber={mobilenumber} | error={e}",
            exc_info=True,
        )


async def sync_userlocation_to_dataverse(
    action: str,
    row: Optional[Dict[str, Any]] = None,
    location_row_id: Optional[str] = None,
) -> None:
    """Mirror a user location add, edit or delete to Dataverse by PATCH, POST fallback or DELETE, never raising.

    Args:
        action: One of 'add', 'edit', or 'delete'.
        row: The DB row returned from add or edit.
        location_row_id: The userlocationdetailsid for edit/delete operations.
    """
    try:
        token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[location_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        base_api = f"{base_url}/api/data/v9.2"

        async with httpx.AsyncClient(timeout=15.0) as client:
            if action in ("add", "edit"):
                if not row:
                    logger.warning(f"[location_sync] No row data for {action} sync; skipping")
                    return
                record_id = row.get("userlocationdetailsid") if action == "add" else location_row_id
                if not record_id:
                    logger.warning(f"[location_sync] No record_id for {action} sync; skipping")
                    return

                payload = {
                    "ets_additionaladdressinfo": row.get("additionaladdressinfo"),
                    "ets_type": row.get("addresstype"),
                    "ets_homeaddress": row.get("homeaddress"),
                    "ets_workaddress": row.get("workaddress"),
                    "ets_landmark": row.get("landmark"),
                    "ets_latitude": str(row["latitude"]) if row.get("latitude") is not None else None,
                    "ets_longitude": str(row["longitude"]) if row.get("longitude") is not None else None,
                    "ets_statecode": row.get("statecode"),
                    "ets_city": row.get("city"),
                }
                contact_id = row.get("contactid")
                if contact_id:
                    payload["ets_ContactId@odata.bind"] = f"/contacts({contact_id})"
                payload = {k: v for k, v in payload.items() if v is not None}

                record_url = f"{base_api}/{LOCATION_ENTITY}({record_id})"
                response = await client.patch(record_url, headers=headers, json=payload)

                if response.status_code == 204:
                    logger.info(f"[location_sync] {action} PATCH success | userlocationdetailsid={record_id}")
                    return

                if response.status_code == 404:
                    create_url = f"{base_api}/{LOCATION_ENTITY}"
                    create_response = await client.post(create_url, headers=headers, json=payload)
                    if create_response.status_code in (200, 201):
                        logger.info(f"[location_sync] {action} POST success | userlocationdetailsid={record_id}")
                        return
                    logger.error(f"[location_sync] {action} POST failed | status={create_response.status_code} | body={create_response.text}")
                    return

                logger.error(f"[location_sync] {action} PATCH failed | status={response.status_code} | body={response.text}")

            elif action == "delete":
                if not location_row_id:
                    logger.warning("[location_sync] No location_row_id for delete sync; skipping")
                    return
                record_url = f"{base_api}/{LOCATION_ENTITY}({location_row_id})"
                response = await client.delete(record_url, headers=headers)
                if response.status_code == 204:
                    logger.info(f"[location_sync] delete success | userlocationdetailsid={location_row_id}")
                    return
                logger.error(f"[location_sync] delete failed | status={response.status_code} | body={response.text}")

            else:
                logger.warning(f"[location_sync] Unknown action '{action}'; skipping sync")

    except Exception as e:
        logger.error(
            f"[location_sync] Failed Dataverse sync | action={action} "
            f"| location_row_id={location_row_id} | error={e}",
            exc_info=True,
        )


MEMBER_ENTITY = "ets_members"
PROVIDER_ENTITY = "ets_providers"


async def sync_switch_profile_to_dataverse(
    contact_id: str,
    userprofile_id: str,
    detail_id: str,
    role: int,
    firstname: str,
    email: str,
    mobilenumber: str,
) -> None:
    """Create-only PATCH the new userprofile and member or provider records into Dataverse under the PG ids.

    Args:
        contact_id: The existing contact UUID.
        userprofile_id: The newly created etown_userprofile UUID.
        detail_id: The newly created etown_member or etown_provider UUID.
        role: 1 for Provider, 2 for Member.
        firstname: Contact's first name.
        email: Email address.
        mobilenumber: Mobile number string.
    """
    if not contact_id or not userprofile_id or not detail_id:
        logger.warning(
            f"[switch_profile_sync] Skipping sync — required ID is empty "
            f"| contact_id={contact_id} | userprofile_id={userprofile_id} | detail_id={detail_id}"
        )
        return

    try:
        token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[switch_profile_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        base_api = f"{base_url}/api/data/v9.2"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        create_headers = {**headers, "If-None-Match": "*"}

        userprofile_payload = {
            "ets_Contact@odata.bind": f"/contacts({contact_id})",
            "ets_email": email,
            "ets_mobilenumber": mobilenumber,
            "ets_name": firstname,
            "ets_role": role,
            "ets_verified": True,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            up_response = await client.patch(
                f"{base_api}/{DATAVERSE_ENTITY}({userprofile_id})",
                headers=create_headers,
                json=userprofile_payload,
            )
            if up_response.status_code not in (204, 412):
                logger.error(
                    f"[switch_profile_sync] Userprofile PATCH failed "
                    f"| status={up_response.status_code} | body={up_response.text}"
                )
                return
            already_existed = up_response.status_code == 412
            logger.info(
                f"[switch_profile_sync] Userprofile synced to Dataverse "
                f"| userprofile_id={userprofile_id} | role={role} "
                f"| {'already_existed' if already_existed else 'created'}"
            )

            if role == 2:
                detail_payload = {
                    "ets_name": None,
                    "ets_email": email,
                    "ets_firstname": firstname,
                    "ets_mobile": mobilenumber,
                    "ets_contact_id@odata.bind": f"/contacts({contact_id})",
                    "ets_role": 2,
                    "ets_UserProfileID@odata.bind": f"/ets_userprofiles({userprofile_id})",
                    "ets_verified": True,
                }
                detail_entity = MEMBER_ENTITY
            else:
                detail_payload = {
                    "ets_email": email,
                    "ets_firstname": firstname,
                    "ets_mobilenumber": mobilenumber,
                    "ets_Contact_id@odata.bind": f"/contacts({contact_id})",
                    "ets_Names@odata.bind": f"/contacts({contact_id})",
                    "ets_role": 1,
                    "ets_UserProfileID@odata.bind": f"/ets_userprofiles({userprofile_id})",
                }
                detail_payload.update(await plan_fields_for_dataverse(detail_id))
                detail_entity = PROVIDER_ENTITY

            detail_response = await client.patch(
                f"{base_api}/{detail_entity}({detail_id})",
                headers=create_headers,
                json=detail_payload,
            )
            if detail_response.status_code not in (204, 412):
                logger.error(
                    f"[switch_profile_sync] Detail PATCH failed "
                    f"| entity={detail_entity} | detail_id={detail_id} "
                    f"| status={detail_response.status_code} | body={detail_response.text}"
                )
                return
            already_existed = detail_response.status_code == 412
            logger.info(
                f"[switch_profile_sync] Detail record synced to Dataverse "
                f"| entity={detail_entity} | detail_id={detail_id} "
                f"| {'already_existed' if already_existed else 'created'}"
            )

    except Exception as e:
        logger.error(
            f"[switch_profile_sync] Failed Dataverse sync "
            f"| contact_id={contact_id} | userprofile_id={userprofile_id} | error={e}",
            exc_info=True,
        )


async def sync_registration_to_dataverse(
    contact_id: str,
    userprofile_id: str,
    detail_id: Optional[str],
    role: int,
    firstname: str,
    email: Optional[str],
    mobilenumber: str,
    contact_fields: Optional[Dict[str, Any]] = None,
    claimed: bool = False,
) -> None:
    """Mirror a new registration's contact, userprofile and member or provider record to Dataverse under the PG ids.

    Args:
        contact_id: Newly created contact UUID.
        userprofile_id: Newly created etown_userprofile UUID.
        detail_id: Newly created etown_member or etown_provider UUID (may be None).
        role: 1 for Provider, 2 for Member.
        firstname: Contact's first name.
        email: Email address (optional).
        mobilenumber: Mobile number string.
        contact_fields: Extra Dataverse-field-keyed values for the contact payload.
        claimed: True when a placeholder was claimed; upserts instead of create-only PATCHes.
    """
    if not contact_id or not userprofile_id:
        logger.warning(
            "[register_sync] Skipping sync — required ID is empty "
            f"| contact_id={contact_id} | userprofile_id={userprofile_id}"
        )
        return

    try:
        token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[register_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        base_api = f"{base_url}/api/data/v9.2"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        create_headers = dict(headers) if claimed else {**headers, "If-None-Match": "*"}

        def _outcome(status_code: int) -> str:
            """Describe a PATCH result for the logs (412 only happens on the create-only path)."""
            if status_code == 412:
                return "existed"
            return "upserted" if claimed else "created"

        async with httpx.AsyncClient(timeout=15.0) as client:
            contact_payload: Dict[str, Any] = {
                "mobilephone": mobilenumber,
                "firstname": firstname,
            }
            if contact_fields:
                contact_payload.update({k: v for k, v in contact_fields.items() if v is not None})

            c_response = await client.patch(
                f"{base_api}/{CONTACT_ENTITY}({contact_id})",
                headers=create_headers,
                json=contact_payload,
            )
            if c_response.status_code not in (204, 412):
                logger.error(
                    f"[register_sync] Contact PATCH failed "
                    f"| status={c_response.status_code} | body={c_response.text}"
                )
                return
            logger.info(
                f"[register_sync] Contact synced | contact_id={contact_id} "
                f"| {_outcome(c_response.status_code)}"
            )

            up_payload = {
                "ets_Contact@odata.bind": f"/contacts({contact_id})",
                "ets_email": email,
                "ets_mobilenumber": mobilenumber,
                "ets_name": firstname,
                "ets_role": role,
                "ets_verified": True,
            }
            up_response = await client.patch(
                f"{base_api}/{DATAVERSE_ENTITY}({userprofile_id})",
                headers=create_headers,
                json=up_payload,
            )
            if up_response.status_code not in (204, 412):
                logger.error(
                    f"[register_sync] Userprofile PATCH failed "
                    f"| status={up_response.status_code} | body={up_response.text}"
                )
                return
            logger.info(
                f"[register_sync] Userprofile synced | userprofile_id={userprofile_id} "
                f"| {_outcome(up_response.status_code)}"
            )

            if not detail_id:
                logger.info("[register_sync] No detail_id; skipping member/provider sync")
                return

            if role == 2:
                detail_payload: Dict[str, Any] = {
                    "ets_name": None,
                    "ets_email": email,
                    "ets_firstname": firstname,
                    "ets_mobile": mobilenumber,
                    "ets_contact_id@odata.bind": f"/contacts({contact_id})",
                    "ets_role": 2,
                    "ets_UserProfileID@odata.bind": f"/ets_userprofiles({userprofile_id})",
                    "ets_verified": True,
                }
                detail_entity = MEMBER_ENTITY
            else:
                detail_payload = {
                    "ets_email": email,
                    "ets_firstname": firstname,
                    "ets_mobilenumber": mobilenumber,
                    "ets_Contact_id@odata.bind": f"/contacts({contact_id})",
                    "ets_Names@odata.bind": f"/contacts({contact_id})",
                    "ets_role": 1,
                    "ets_UserProfileID@odata.bind": f"/ets_userprofiles({userprofile_id})",
                }
                detail_payload.update(await plan_fields_for_dataverse(detail_id))
                detail_entity = PROVIDER_ENTITY

            d_response = await client.patch(
                f"{base_api}/{detail_entity}({detail_id})",
                headers=create_headers,
                json=detail_payload,
            )
            if d_response.status_code not in (204, 412):
                logger.error(
                    f"[register_sync] Detail PATCH failed "
                    f"| entity={detail_entity} | detail_id={detail_id} "
                    f"| status={d_response.status_code} | body={d_response.text}"
                )
                return
            logger.info(
                f"[register_sync] Detail synced | entity={detail_entity} | detail_id={detail_id} "
                f"| {_outcome(d_response.status_code)}"
            )

    except Exception as e:
        logger.error(
            f"[register_sync] Failed Dataverse sync "
            f"| contact_id={contact_id} | userprofile_id={userprofile_id} | error={e}",
            exc_info=True,
        )


async def sync_member_promotion_to_dataverse(
    contact_id: str,
    userprofile_ids: list,
    member_ids: list,
) -> None:
    """PATCH ets_verified=True on the contact, its member userprofiles and member records in Dataverse, never raising.

    Args:
        contact_id: Contact UUID to mark verified.
        userprofile_ids: role=2 userprofile UUIDs to mark verified.
        member_ids: etown_member UUIDs to mark verified.
    """
    if not contact_id:
        return

    try:
        token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[member_promote_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        base_api = f"{base_url}/api/data/v9.2"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "If-Match": "*",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            async def _patch_verified(url: str, label: str) -> None:
                response = await client.patch(url, headers=headers, json={"ets_verified": True})
                if response.status_code == 204:
                    logger.info(f"[member_promote_sync] {label} PATCH success")
                else:
                    logger.error(
                        f"[member_promote_sync] {label} PATCH failed "
                        f"| status={response.status_code} | body={response.text}"
                    )

            tasks = [_patch_verified(f"{base_api}/{CONTACT_ENTITY}({contact_id})", f"contact({contact_id})")]
            for up_id in userprofile_ids:
                tasks.append(_patch_verified(f"{base_api}/{DATAVERSE_ENTITY}({up_id})", f"userprofile({up_id})"))
            for m_id in member_ids:
                tasks.append(_patch_verified(f"{base_api}/{MEMBER_ENTITY}({m_id})", f"member({m_id})"))

            await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as e:
        logger.error(
            f"[member_promote_sync] Failed Dataverse sync | contact_id={contact_id} | error={e}",
            exc_info=True,
        )


async def sync_member_profile_update_to_dataverse(
    contact_id: str,
    contact_payload: Dict[str, Any],
    userprofile_ids: list,
    member_ids: list,
) -> None:
    """PATCH the supplied member profile fields onto the Dataverse contact, userprofiles and members concurrently, never raising.

    Args:
        contact_id: The contact UUID to update in Dataverse.
        contact_payload: Resolved update values keyed by their request field names.
        userprofile_ids: List of etown_userprofile UUIDs linked to this contact.
        member_ids: List of etown_member UUIDs linked to this contact.
    """
    if not contact_id:
        logger.warning("[member_profile_sync] Skipping sync — contact_id is empty")
        return

    try:
        token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[member_profile_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        base_api = f"{base_url}/api/data/v9.2"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "If-Match": "*",
        }

        firstname = contact_payload.get("firstname")
        email = contact_payload.get("email")
        mobilenumber = contact_payload.get("mobilenumber")
        address = contact_payload.get("address")
        city = contact_payload.get("city")
        latitude = contact_payload.get("latitude")
        longitude = contact_payload.get("longitude")
        additionaladdressinfo = contact_payload.get("additionaladdressinfo")
        statecode = contact_payload.get("statecode")
        connectycube_id = contact_payload.get("connectycube_id")
        birthdate = contact_payload.get("birthdate")
        resolved_image = contact_payload.get("resolved_profile_image")
        image_updated = contact_payload.get("image_updated", False)

        contact_dv: Dict[str, Any] = {}
        if firstname is not None:
            contact_dv["firstname"] = firstname
        if email is not None:
            contact_dv["emailaddress1"] = email
        if mobilenumber is not None:
            contact_dv["mobilephone"] = str(mobilenumber)
        if address is not None:
            contact_dv["address1_line1"] = address
        if city is not None:
            contact_dv["address1_city"] = city
        if latitude is not None:
            contact_dv["address1_latitude"] = str(latitude)
        if longitude is not None:
            contact_dv["address1_longitude"] = str(longitude)
        if additionaladdressinfo is not None:
            contact_dv["ets_additionaladdressinfo"] = additionaladdressinfo
        if statecode is not None:
            contact_dv["ets_statecode"] = statecode
        if connectycube_id is not None:
            contact_dv["ets_connectycubeid"] = connectycube_id
        if birthdate is not None:
            if isinstance(birthdate, (datetime.date, datetime.datetime)):
                contact_dv["birthdate"] = birthdate.strftime("%Y-%m-%d")
            else:
                for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        contact_dv["birthdate"] = (
                            datetime.datetime.strptime(str(birthdate).strip(), fmt).strftime("%Y-%m-%d")
                        )
                        break
                    except ValueError:
                        continue
                else:
                    logger.warning(
                        "[member_profile_sync] Unparseable birthdate %r — omitting from the "
                        "Dataverse payload so the rest of the contact still syncs", birthdate,
                    )
        if image_updated:
            contact_dv["ets_profileimage"] = resolved_image
            contact_dv["entityimage"] = None

        async with httpx.AsyncClient(timeout=15.0) as client:
            tasks = []

            async def _patch(url: str, payload: Dict[str, Any], label: str) -> None:
                clean = {k: v for k, v in payload.items() if v is not None or k in ("entityimage", "ets_profileimage")}
                if not clean:
                    return
                response = await client.patch(url, headers=headers, json=clean)
                if response.status_code == 204:
                    logger.info(f"[member_profile_sync] {label} PATCH success")
                else:
                    logger.error(
                        f"[member_profile_sync] {label} PATCH failed "
                        f"| status={response.status_code} | body={response.text}"
                    )

            if contact_dv:
                tasks.append(_patch(
                    f"{base_api}/{CONTACT_ENTITY}({contact_id})",
                    contact_dv,
                    f"contact({contact_id})",
                ))

            for up_id in userprofile_ids:
                up_payload: Dict[str, Any] = {}
                if firstname is not None:
                    up_payload["ets_name"] = firstname
                if email is not None:
                    up_payload["ets_email"] = email
                if mobilenumber is not None:
                    up_payload["ets_mobilenumber"] = str(mobilenumber)
                if up_payload:
                    tasks.append(_patch(
                        f"{base_api}/{DATAVERSE_ENTITY}({up_id})",
                        up_payload,
                        f"userprofile({up_id})",
                    ))

            for m_id in member_ids:
                m_payload: Dict[str, Any] = {}
                if firstname is not None:
                    m_payload["ets_name"] = firstname
                if email is not None:
                    m_payload["ets_email"] = email
                if mobilenumber is not None:
                    m_payload["ets_mobile"] = str(mobilenumber)
                if m_payload:
                    tasks.append(_patch(
                        f"{base_api}/{MEMBER_ENTITY}({m_id})",
                        m_payload,
                        f"member({m_id})",
                    ))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as e:
        logger.error(
            f"[member_profile_sync] Failed Dataverse sync | contact_id={contact_id} | error={e}",
            exc_info=True,
        )


async def sync_address_update_to_dataverse(
    contact_id: str,
    address: Optional[str] = None,
    addresscomp: Optional[str] = None,
    additionaladdressinfo: Optional[str] = None,
    latitude: Optional[str] = None,
    longitude: Optional[str] = None,
    statecode: Optional[str] = None,
    city: Optional[str] = None,
) -> None:
    """PATCH the supplied address fields onto a Dataverse contact, logging instead of raising.

    Args:
        contact_id: The contact UUID to update in Dataverse.
        address: Street address, sent as address1_primarycontactname.
        addresscomp: Composite address, sent as address1_composite.
        additionaladdressinfo: Additional address information.
        latitude: Latitude string, sent as a float when parseable.
        longitude: Longitude string, sent as a float when parseable.
        statecode: State code.
        city: City name.
    """
    if not contact_id:
        logger.warning("[address_sync] Skipping sync — contact_id is empty")
        return

    try:
        token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[address_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        base_api = f"{base_url}/api/data/v9.2"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "If-Match": "*",
        }

        def _to_float(value: Optional[str]) -> Optional[float]:
            if value is None:
                return None
            value = value.strip()
            if value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                logger.debug("member mirror: %r is not a number; storing null", value)
                return None

        contact_dv: Dict[str, Any] = {}
        if address is not None:
            contact_dv["address1_primarycontactname"] = address
        if addresscomp is not None:
            contact_dv["address1_composite"] = addresscomp
        if city is not None:
            contact_dv["address1_city"] = city
        if statecode is not None:
            contact_dv["ets_statecode"] = statecode
        lat_val = _to_float(latitude)
        if lat_val is not None:
            contact_dv["address1_latitude"] = lat_val
        lon_val = _to_float(longitude)
        if lon_val is not None:
            contact_dv["address1_longitude"] = lon_val
        if additionaladdressinfo is not None:
            contact_dv["ets_additionaladdressinfo"] = additionaladdressinfo

        if not contact_dv:
            logger.info(f"[address_sync] No fields to sync | contact_id={contact_id}")
            return

        endpoint = f"{base_api}/{CONTACT_ENTITY}({contact_id})"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(endpoint, headers=headers, json=contact_dv)
            if response.status_code == 204:
                logger.info(f"[address_sync] contact({contact_id}) PATCH success")
            else:
                logger.error(
                    f"[address_sync] contact({contact_id}) PATCH failed "
                    f"| status={response.status_code} | body={response.text}"
                )

    except Exception as e:
        logger.error(
            f"[address_sync] Failed Dataverse sync | contact_id={contact_id} | error={e}",
            exc_info=True,
        )
