"""Notification Dataverse Sync - mirrors notification read and clear-all updates to the CRM.

Background Dataverse mirror for notification status: PATCHes one notification, or every active notification for a receiver, to Inactive/Read; Postgres stays authoritative and every call logs its own errors instead of raising.

Features:
    - Notification Status Sync: PATCHes notification records to Inactive/Read status in Dataverse
    - Bulk Clear: Lists and PATCHes every active notification for a receiver in one call
    - OAuth Token Management: Acquires client-credentials tokens for Dataverse API auth
    - BackgroundTask Integration: Designed for non-blocking execution as background tasks
    - Error Handling: Logs Dataverse API failures without raising to caller

Classes:
    No classes defined (module uses function-based sync utilities)

Functions:
    get_dataverse_token(): Acquire OAuth2 client-credentials token for Dataverse
    update_notification_status_in_dataverse(): PATCH a notification record to Inactive/Read
    clear_all_notifications_in_dataverse(): PATCH every active notification for a receiver to Inactive/Read

Dependencies:
    - httpx: Async HTTP client for Dataverse Web API calls
    - app.core.config: Dataverse API configuration and OAuth credentials
    - app.core.logging: Centralized application logging

Updates:
    26-May-2026: Added Features, Classes, Functions, Dependencies, Updates sections to complete module docstring
"""

import logging

import httpx

from app.core.config import settings
from app.core.logging import logger

DATAVERSE_ENTITY = "ets_notificationses"


async def get_dataverse_token() -> str:
    """Acquire a Dataverse client-credentials access token, raising if the response carries none."""
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


async def update_notification_status_in_dataverse(notification_id: str) -> None:
    """PATCH one Dataverse notification record to Inactive/Read, logging rather than raising on failure."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_NOTIFICATION_UPDATE | FAILED_TOKEN | notificationsid=%s | %s",
            notification_id, exc, exc_info=True,
        )
        return

    url = f"{settings.DATAVERSE_URL}/api/data/v9.2/{DATAVERSE_ENTITY}({notification_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }
    payload = {
        "statecode": 1,
        "statuscode": 2,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(url, json=payload, headers=headers)

        if response.status_code in (200, 204):
            logger.info(
                "DATAVERSE_NOTIFICATION_UPDATE | SUCCESS | notificationsid=%s",
                notification_id,
            )
        else:
            logger.error(
                "DATAVERSE_NOTIFICATION_UPDATE | FAILED | notificationsid=%s | status=%s | body=%s",
                notification_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_NOTIFICATION_UPDATE | ERROR | notificationsid=%s | %s",
            notification_id, exc, exc_info=True,
        )


async def clear_all_notifications_in_dataverse(contact_id: str, userprofile_id: str) -> None:
    """PATCH every active Dataverse notification for a receiver contact and profile to Inactive/Read, logging failures."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_CLEAR_ALL_NOTIFICATIONS | FAILED_TOKEN | contact_id=%s | userprofile_id=%s | %s",
            contact_id, userprofile_id, exc, exc_info=True,
        )
        return

    filter_query = (
        f"_ets_receivercontactid_value eq {contact_id} "
        f"and _ets_receiveruserprofile_value eq {userprofile_id} "
        f"and statecode eq 0"
    )
    list_url = (
        f"{settings.DATAVERSE_URL}/api/data/v9.2/{DATAVERSE_ENTITY}"
        f"?$select=ets_notificationsid&$filter={filter_query}"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            list_response = await client.get(list_url, headers=headers)

        if list_response.status_code != 200:
            logger.error(
                "DATAVERSE_CLEAR_ALL_NOTIFICATIONS | FAILED_LIST | contact_id=%s | userprofile_id=%s | status=%s | body=%s",
                contact_id, userprofile_id, list_response.status_code, list_response.text,
            )
            return

        records = list_response.json().get("value", [])
        if not records:
            logger.info(
                "DATAVERSE_CLEAR_ALL_NOTIFICATIONS | NO_RECORDS | contact_id=%s | userprofile_id=%s",
                contact_id, userprofile_id,
            )
            return

        payload = {
            "statecode": 1,
            "statuscode": 2,
            "ets_ReceiverContactID@odata.bind": f"/contacts({contact_id})",
            "ets_ReceiverUserprofile@odata.bind": f"/ets_userprofiles({userprofile_id})",
        }

        success_count = 0
        fail_count = 0

        async with httpx.AsyncClient(timeout=15.0) as client:
            for record in records:
                record_id = record.get("ets_notificationsid")
                if not record_id:
                    continue
                patch_url = f"{settings.DATAVERSE_URL}/api/data/v9.2/{DATAVERSE_ENTITY}({record_id})"
                try:
                    response = await client.patch(patch_url, json=payload, headers=headers)
                    if response.status_code in (200, 204):
                        success_count += 1
                    else:
                        fail_count += 1
                        logger.warning(
                            "DATAVERSE_CLEAR_ALL_NOTIFICATIONS | PATCH_FAILED | record_id=%s | status=%s | body=%s",
                            record_id, response.status_code, response.text,
                        )
                except Exception as patch_exc:
                    fail_count += 1
                    logger.warning(
                        "DATAVERSE_CLEAR_ALL_NOTIFICATIONS | PATCH_ERROR | record_id=%s | %s",
                        record_id, patch_exc,
                    )

        logger.info(
            "DATAVERSE_CLEAR_ALL_NOTIFICATIONS | COMPLETE | contact_id=%s | userprofile_id=%s | success=%d | failed=%d",
            contact_id, userprofile_id, success_count, fail_count,
        )

    except Exception as exc:
        logger.error(
            "DATAVERSE_CLEAR_ALL_NOTIFICATIONS | ERROR | contact_id=%s | userprofile_id=%s | %s",
            contact_id, userprofile_id, exc, exc_info=True,
        )