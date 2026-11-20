"""Offers Dataverse Sync - mirrors offer creates and deletes from Postgres to the CRM.

Background Dataverse mirror for etown_offers writes: upserts or deletes the matching ets_offerses record, retrying without lookup binds when a related row is missing; Postgres stays authoritative and every call swallows its own errors.

Features:
    - OAuth2 Token Acquisition: Fetches a fresh client-credentials Azure AD token on every call.
    - Offer Deletion Sync: Deletes the matching Dataverse `ets_offerses` record.
    - Offer Upsert Sync: PATCH upsert, falling back to POST on a 404.
    - Lookup Fallback: Retries without contact/userprofile @odata.bind when the target is missing.

Classes:
    No classes defined (module exposes Dataverse sync functions only)

Functions:
    get_dataverse_token(): Acquire an OAuth2 client-credentials token for Dataverse.
    delete_offer_from_dataverse(): Delete a single offer record in Dataverse by its offersid.
    insert_offer_to_dataverse(): Upsert a newly created offer record into Dataverse.

Dependencies:
    - httpx: Async HTTP client for the Azure AD token request and the Dataverse Web API calls
    - app.core.config: settings.DATAVERSE_TENANT_ID / DATAVERSE_CLIENT_ID / DATAVERSE_CLIENT_SECRET / DATAVERSE_URL
    - app.core.logging: Centralised logger

Updates:
    11-May-2026: Module created with get_dataverse_token() and delete_offer_from_dataverse().
    22-May-2026: Added insert_offer_to_dataverse() to upsert newly created offers into Dataverse.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import logging

import httpx

from app.core.config import settings
from app.core.logging import logger

DATAVERSE_ENTITY = "ets_offerses"


async def get_dataverse_token() -> str:
    """Fetch a Dataverse client-credentials access token, raising if the response lacks one."""
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


async def delete_offer_from_dataverse(offers_id: str) -> None:
    """Delete an offer's Dataverse record, logging failures instead of raising."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("delete_offer_from_dataverse: failed to get Dataverse token: %s", exc, exc_info=True)
        return

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    url = f"{base_url}/{DATAVERSE_ENTITY}({offers_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "OData-Version": "4.0",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.delete(url, headers=headers)

        if response.status_code in (200, 204):
            logger.info("DATAVERSE_DELETE | SUCCESS | offersid=%s", offers_id)
        else:
            logger.error("DATAVERSE_DELETE | FAILED | offersid=%s | status=%s | body=%s", offers_id, response.status_code, response.text)
    except Exception as exc:
        logger.error("DATAVERSE_DELETE | ERROR | offersid=%s | %s", offers_id, exc, exc_info=True)


async def insert_offer_to_dataverse(offer_row: dict) -> None:
    """Upsert an offer into Dataverse by id, falling back to create and retrying without lookups when they 404."""
    offers_id = offer_row.get("offersid")

    if not offers_id:
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_INSERT | FAILED | offersid=%s | %s", offers_id, exc, exc_info=True)
        return

    def _fmt_ts(val):
        if val is None:
            return None
        if hasattr(val, "isoformat"):
            return val.isoformat()
        return str(val)

    payload = {
        "ets_businessname":         offer_row.get("businessname"),
        "ets_businessprofileimage": offer_row.get("businessprofileimageurl"),
        "ets_colorcode":            offer_row.get("colorcode"),
        "ets_offertype":            offer_row.get("offertype"),
        "ets_fontfamily":           offer_row.get("fontfamily"),
        "ets_description":          offer_row.get("description"),
        "ets_thumbnailfileurl":     offer_row.get("thumbnailfileurl"),
        "ets_offerfile1":           offer_row.get("offerfile1"),
        "ets_offerfile2":           offer_row.get("offerfile2"),
        "ets_offerfile3":           offer_row.get("offerfile3"),
        "ets_offerfile4":           offer_row.get("offerfile4"),
        "ets_offerfile5":           offer_row.get("offerfile5"),
        "ets_offervideofile":       offer_row.get("offervideofile"),
        "ets_validity":             offer_row.get("validity"),
        "ets_createdtime":          _fmt_ts(offer_row.get("createdtime")),
        "ets_scheduledtime":        _fmt_ts(offer_row.get("scheduledtime")),
        "ets_scheduledendtime":     _fmt_ts(offer_row.get("scheduledendtime")),
        "ets_latitude":             offer_row.get("latitude"),
        "ets_longitude":            offer_row.get("longitude"),
        "ets_tags":                 offer_row.get("tags"),
        "ets_title":                offer_row.get("title"),
        "ets_offerunicid":          offer_row.get("offerunicid"),
        "ets_languagecode":         offer_row.get("languagecode"),
        "ets_fontsize":             offer_row.get("fontsize"),
        "ets_fontstyle":            offer_row.get("fontstyle"),
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    contact_id = offer_row.get("contactid")
    userprofile_id = offer_row.get("userprofileid")
    if contact_id:
        payload["ets_contactid@odata.bind"] = f"/contacts({contact_id})"
    if userprofile_id:
        payload["ets_userprofileid@odata.bind"] = f"/ets_userprofiles({userprofile_id})"

    def _without_lookups(p: dict) -> dict:
        return {k: v for k, v in p.items() if not k.endswith("@odata.bind")}

    def _is_missing_related_entity(resp) -> bool:
        try:
            if resp.status_code != 404:
                return False
            msg = resp.json()["error"]["message"]
            return "Does Not Exist" in msg and DATAVERSE_ENTITY not in msg
        except Exception:
            logger.debug("offer mirror: could not read the Dataverse error body; assuming the related entity exists", exc_info=True)
            return False

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{DATAVERSE_ENTITY}({offers_id})"
    create_url = f"{base_url}/{DATAVERSE_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)

            if response.status_code == 204:
                logger.info("DATAVERSE_INSERT | SUCCESS | offersid=%s", offers_id)
                return

            if _is_missing_related_entity(response):
                response = await client.patch(upsert_url, json=_without_lookups(payload), headers=headers)
                if response.status_code == 204:
                    logger.info("DATAVERSE_INSERT | SUCCESS | offersid=%s", offers_id)
                    return

            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)

                if response.status_code in (200, 201):
                    logger.info("DATAVERSE_INSERT | SUCCESS | offersid=%s", offers_id)
                    return

                if _is_missing_related_entity(response):
                    response = await client.post(create_url, json=_without_lookups(payload), headers=headers)
                    if response.status_code in (200, 201):
                        logger.info("DATAVERSE_INSERT | SUCCESS | offersid=%s", offers_id)
                        return

            logger.error("DATAVERSE_INSERT | FAILED | offersid=%s | status=%s | body=%s", offers_id, response.status_code, response.text)
    except Exception as exc:
        logger.error("DATAVERSE_INSERT | ERROR | offersid=%s | %s", offers_id, exc, exc_info=True)
