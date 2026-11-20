"""Devices Dataverse Sync - mirrors device token registrations from Postgres to the CRM.

Background Dataverse mirror for device writes from /api/devices: upserts one ets_contact_devices record per device token with contact and userprofile binds; Postgres stays authoritative and the call runs as a BackgroundTask.

Features:
    - Device Token Upsert Sync: Query existing ets_contact_devices record then PATCH or POST
    - Per-device Matching: Rows match on the device token, so each device gets its own CRM row
    - OAuth Token Management: Acquires client-credentials tokens for Dataverse API auth
    - Navigation Property Binds: Correctly wires contact and userprofile lookups via OData bind
    - BackgroundTask Integration: All sync functions designed for non-blocking execution

Classes:
    No classes defined (module uses function-based sync utilities)

Functions:
    _odata_str(): Escape a value for use inside an OData single-quoted string literal
    get_dataverse_token(): Acquire OAuth2 client-credentials token for Dataverse
    sync_device_token_to_dataverse(): Upsert one device's ets_contact_devices record in Dataverse

Dependencies:
    - httpx: Async HTTP client for Dataverse Web API calls
    - app.core.config: Dataverse API configuration and OAuth credentials
    - app.core.logging: Centralized application logging

Updates:
    02-Jul-2026: Initial creation for SaveDeviceToken PowerApps migration
    28-Jul-2026: The CRM lookup filters on the device token, falling back to contact and userprofile.
"""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import logger

_ENTITY = "ets_contact_deviceses"
_LOG = "DATAVERSE_DEVICE_TOKEN_SYNC"


def _odata_str(value: str) -> str:
    """Escape a value for use inside an OData single-quoted string literal."""
    return value.replace("'", "''")


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


async def sync_device_token_to_dataverse(
    contact_id: str,
    userprofile_id: str,
    devicetoken: Optional[str],
    cc_user_id: Optional[str],
    voip_device_token: Optional[str],
    device_id: Optional[str] = None,
) -> None:
    """Upsert one device's Dataverse row, matched by device token, VoIP token or contact plus profile; never raises.

    Args:
        contact_id: Dynamics contact GUID, bound and used as fallback filter.
        userprofile_id: Etown user profile GUID, bound and used as fallback filter.
        devicetoken: FCM/APNs token for ets_deviceguid; None skips the field.
        cc_user_id: Cloud Communications user ID for ets_ccuserid; None skips the field.
        voip_device_token: VoIP APNs token for ets_voip_device_token; None skips the field.
        device_id: Postgres-side device identity, used for logging only.
    """
    if not contact_id or not userprofile_id:
        logger.warning(f"{_LOG} | SKIP | missing contact_id or userprofile_id")
        return

    base_url = (settings.DATAVERSE_URL or "").rstrip("/")
    if not base_url:
        logger.warning(f"{_LOG} | SKIP | DATAVERSE_URL not configured")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(f"{_LOG} | FAILED_TOKEN | contact_id={contact_id} | {exc}", exc_info=True)
        return

    api_base = f"{base_url}/api/data/v9.2"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
        "OData-MaxVersion": "4.0",
    }

    payload: dict = {}
    if devicetoken is not None:
        payload["ets_deviceguid"] = devicetoken
    if cc_user_id is not None:
        payload["ets_ccuserid"] = cc_user_id
    if voip_device_token is not None:
        payload["ets_voip_device_token"] = voip_device_token

    payload["ets_ContactId@odata.bind"] = f"/contacts({contact_id})"
    payload["ets_userprofileid@odata.bind"] = f"/ets_userprofiles({userprofile_id})"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:

            if devicetoken:
                filter_query = f"ets_deviceguid eq '{_odata_str(devicetoken)}'"
            elif voip_device_token:
                filter_query = f"ets_voip_device_token eq '{_odata_str(voip_device_token)}'"
            else:
                filter_query = (
                    f"_ets_contactid_value eq {contact_id} "
                    f"and _ets_userprofileid_value eq {userprofile_id}"
                )
            query_url = (
                f"{api_base}/{_ENTITY}"
                f"?$filter={filter_query}"
                f"&$select=ets_contact_devicesid"
                f"&$top=1"
            )
            query_resp = await client.get(query_url, headers=headers)

            if query_resp.status_code != 200:
                logger.error(
                    f"{_LOG} | QUERY_FAILED | contact_id={contact_id} "
                    f"| status={query_resp.status_code} | body={query_resp.text}"
                )
                return

            records = query_resp.json().get("value", [])

            if records:
                dataverse_id = records[0]["ets_contact_devicesid"]
                patch_url = f"{api_base}/{_ENTITY}({dataverse_id})"
                response = await client.patch(patch_url, json=payload, headers=headers)

                if response.status_code == 204:
                    logger.info(
                        f"{_LOG} | PATCH_SUCCESS | contact_id={contact_id} "
                        f"| dataverse_id={dataverse_id}"
                    )
                else:
                    logger.error(
                        f"{_LOG} | PATCH_FAILED | contact_id={contact_id} "
                        f"| dataverse_id={dataverse_id} "
                        f"| status={response.status_code} | body={response.text}"
                    )
            else:
                create_url = f"{api_base}/{_ENTITY}"
                response = await client.post(create_url, json=payload, headers=headers)

                if response.status_code in (200, 201, 204):
                    logger.info(
                        f"{_LOG} | POST_SUCCESS | contact_id={contact_id} "
                        f"| userprofile_id={userprofile_id} | device_id={device_id or '-'}"
                    )
                else:
                    logger.error(
                        f"{_LOG} | POST_FAILED | contact_id={contact_id} "
                        f"| status={response.status_code} | body={response.text}"
                    )

    except Exception as exc:
        logger.error(
            f"{_LOG} | ERROR | contact_id={contact_id} | {exc}", exc_info=True
        )
