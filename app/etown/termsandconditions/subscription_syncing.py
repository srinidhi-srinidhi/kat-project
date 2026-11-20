"""Subscription Dataverse Sync - mirrors subscription status writes from Postgres to the CRM.

Background Dataverse mirror for etown_subscriptions writes from the terms-and-conditions routes: upserts the status and userprofile lookups on ets_subscriptions; Postgres stays authoritative and the call swallows its own errors.

Features:
    - OAuth Token Acquisition: Client-credentials token fetch for the Dataverse Web API
    - Status Upsert: PATCH the existing record, falling back to POST when it doesn't yet exist
    - Non-blocking Sync: Logs but never raises, so a subscription write is unaffected by Dataverse

Classes:
    No classes defined (module exposes two async functions)

Functions:
    get_dataverse_token(): Acquire an OAuth2 client-credentials access token for Dataverse
    upsert_subscription_to_dataverse(): PATCH (or POST on 404) a subscription's status and userprofile lookups

Dependencies:
    - httpx: Async HTTP client for the Dataverse Web API and OAuth token endpoint
    - app.core.config: Dataverse tenant/client credentials and base URL
    - app.core.logging: Application logger for sync outcomes

Updates:
    28-May-2026: Module created to sync etown_subscriptions status and lookups to Dataverse.
"""

import logging

import httpx

from app.core.config import settings
from app.core.logging import logger

DATAVERSE_ENTITY = "ets_subscriptions"


async def get_dataverse_token() -> str:
    """Return an OAuth2 client-credentials access token for Dataverse, raising when none is returned."""
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


async def upsert_subscription_to_dataverse(subscription_row: dict) -> None:
    """PATCH a subscription's status and profile lookups to Dataverse, POSTing on 404, logging and never raising."""
    subscription_id = subscription_row.get("subscriptionsid")
    current_userprofile_id = subscription_row.get("current_userprofileid")
    opponent_userprofile_id = subscription_row.get("opponent_userprofileid")

    if not subscription_id:
        logger.warning("DATAVERSE_SUBSCRIPTION_SYNC | SKIP | missing subscriptionsid")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_SUBSCRIPTION_SYNC | FAILED_TOKEN | subscriptionsid=%s | %s",
            subscription_id,
            exc,
            exc_info=True,
        )
        return

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    record_url = f"{base_url}/{DATAVERSE_ENTITY}({subscription_id})"
    create_url = f"{base_url}/{DATAVERSE_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    status_value = subscription_row.get("subscriptionstatus")
    payload = {
        "ets_status": status_value,
    }
    if current_userprofile_id:
        payload["ets_currentuserprofileid@odata.bind"] = (
            f"/ets_userprofiles({current_userprofile_id})"
        )
    if opponent_userprofile_id:
        payload["ets_opponentuserprofileid@odata.bind"] = (
            f"/ets_userprofiles({opponent_userprofile_id})"
        )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            payload = {k: v for k, v in payload.items() if v is not None}

            response = await client.patch(record_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info(
                    "DATAVERSE_SUBSCRIPTION_SYNC | SUCCESS_PATCH | subscriptionsid=%s",
                    subscription_id,
                )
                return

            if response.status_code == 404:
                create_response = await client.post(create_url, json=payload, headers=headers)
                if create_response.status_code in (200, 201):
                    logger.info(
                        "DATAVERSE_SUBSCRIPTION_SYNC | SUCCESS_POST | subscriptionsid=%s",
                        subscription_id,
                    )
                    return

            logger.error(
                "DATAVERSE_SUBSCRIPTION_SYNC | FAILED | subscriptionsid=%s | status=%s | body=%s",
                subscription_id,
                response.status_code,
                response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_SUBSCRIPTION_SYNC | ERROR | subscriptionsid=%s | %s",
            subscription_id,
            exc,
            exc_info=True,
        )