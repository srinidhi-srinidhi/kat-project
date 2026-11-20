"""Feed Dataverse Sync - mirrors feed posts, comments and likes from Postgres to the CRM.

Background Dataverse mirror for /api/feeds and the Chaatie post tool: inserts, updates and deletes ets_feeds records with their comments and likes; Postgres stays authoritative and every call swallows its own errors.

Features:
    - Feed Insert Sync: PATCH-then-POST, retrying without lookups when a bound record is missing
    - Feed Update Sync: Updates feed text, audience and image slots of existing Dataverse records
    - Feed Delete Sync: Deletes a feed record after deleting its comment and like records
    - Comment Insert Sync: Creates new feed comment records in Dataverse via PATCH-then-POST
    - Comment Update Sync: Updates existing feed comment content in Dataverse
    - Comment Delete Sync: Deletes one feed comment record from Dataverse
    - Feed Like Sync: Creates or updates a feed like record in Dataverse via PATCH-then-POST
    - OAuth Token Management: Acquires client-credentials tokens for Dataverse API auth
    - BackgroundTask Integration: All sync functions designed for non-blocking execution

Classes:
    No classes defined (module uses function-based sync utilities)

Functions:
    _serialize_jsonb_text(): Serialize a jsonb column value into JSON text for a Dataverse field
    get_dataverse_token(): Acquire OAuth2 client-credentials token for Dataverse
    update_feed_in_dataverse(): PATCH the text, audience and images of an existing Dataverse feed
    insert_feed_to_dataverse(): PATCH-then-POST a feed row into the ets_feeds entity
    insert_feed_comment_to_dataverse(): Create a new feed comment record in Dataverse
    update_feed_comment_in_dataverse(): Update existing feed comment content in Dataverse
    delete_feed_comment_in_dataverse(): DELETE one feed comment record from Dataverse
    _delete_dataverse_records(): Delete a list of Dataverse records by id from one entity
    insert_feed_like_to_dataverse(): Create or update a feed like record in Dataverse
    delete_feed_in_dataverse(): DELETE a feed and its associated comments and likes from Dataverse

Dependencies:
    - json: Serializes jsonb column values (groups/phonebook) into Dataverse text fields
    - httpx: Async HTTP client for Dataverse Web API calls
    - app.core.config: Dataverse API configuration and OAuth credentials
    - app.core.logging: Centralized application logging
    - app.etown.feed.feed_utils: normalize_quill_delta(), used to format feed text for the PATCH

Updates:
    26-May-2026: Added Features, Classes, Functions, Dependencies, Updates sections to complete module docstring
    18-Aug-2026: update_feed_in_dataverse mirrors audience and image slots; ets_role sent as Picklist int.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import json
import logging

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.etown.feed.feed_utils import normalize_quill_delta

DATAVERSE_ENTITY = "ets_feeds"


def _serialize_jsonb_text(value) -> str | None:
    """Return a jsonb value as a JSON string for a Dataverse text field, or None if empty or unserialisable."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        logger.warning("feed mirror: value is not JSON-serialisable; storing null", exc_info=True)
        return None


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


async def update_feed_in_dataverse(
    feed_id: str,
    feed_text: str,
    audience: int | None = None,
    feedfilelocationurl: str | None = None,
    feedfilelocation2: str | None = None,
    feedfilelocation3: str | None = None,
    feedfilelocation4: str | None = None,
    feedfilelocation5: str | None = None,
) -> None:
    """PATCH a Dataverse feed's normalised text plus any supplied audience or image paths, logging failures.

    Args:
        feed_id: The ets_feedid of the record to patch.
        feed_text: New text content, normalised from Quill delta.
        audience: 1 mycircle or 2 public, sent as a string; None leaves it alone.
        feedfilelocationurl: New primary image path; None leaves the field alone.
        feedfilelocation2: New second image path; None leaves the field alone.
        feedfilelocation3: New third image path; None leaves the field alone.
        feedfilelocation4: New fourth image path; None leaves the field alone.
        feedfilelocation5: New fifth image path; None leaves the field alone.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_UPDATE_FEED | FAILED_TOKEN | feedid=%s | %s", feed_id, exc, exc_info=True)
        return

    url = f"{settings.DATAVERSE_URL}/api/data/v9.2/{DATAVERSE_ENTITY}({feed_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }
    formatted_text = normalize_quill_delta(feed_text)
    payload = {
        "ets_feedtext":             formatted_text,
        "ets_audience":             str(audience) if audience is not None else None,
        "ets_feedfilelocationurl":  feedfilelocationurl,
        "ets_feedfile2":            feedfilelocation2,
        "ets_feedfile3":            feedfilelocation3,
        "ets_feedfile4":            feedfilelocation4,
        "ets_feedfile5":            feedfilelocation5,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(url, json=payload, headers=headers)

        if response.status_code == 204:
            logger.info("DATAVERSE_UPDATE_FEED | SUCCESS | feedid=%s", feed_id)
        else:
            logger.error(
                "DATAVERSE_UPDATE_FEED | FAILED | feedid=%s | status=%s | body=%s",
                feed_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error("DATAVERSE_UPDATE_FEED | ERROR | feedid=%s | %s", feed_id, exc, exc_info=True)

async def insert_feed_to_dataverse(feed_row: dict) -> None:
    """Upsert a feed row into Dataverse by PATCH then POST, retrying without lookups when a related record is missing."""
    feed_id = feed_row.get("feedid")
    if not feed_id:
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_INSERT_FEED | FAILED_TOKEN | feedid=%s | %s", feed_id, exc, exc_info=True)
        return

    _audience = feed_row.get("audience")
    payload = {
        "ets_feedtext":             feed_row.get("feedtext"),
        "ets_servicename":          feed_row.get("servicename"),
        "ets_audience":             str(_audience) if _audience is not None else None,
        "ets_role":                 feed_row.get("role"),
        "ets_feedfilelocationurl":  feed_row.get("feedfilelocationurl"),
        "ets_thumbnailfileurl":     feed_row.get("thumbnailfileurl"),
        "ets_serviceimageurl":      feed_row.get("serviceimageurl"),
        "ets_feedfile2":            feed_row.get("feedfilelocation2"),
        "ets_feedfile3":            feed_row.get("feedfilelocation3"),
        "ets_feedfile4":            feed_row.get("feedfilelocation4"),
        "ets_feedfile5":            feed_row.get("feedfilelocation5"),
        "ets_feedfile6":            feed_row.get("feedvideofilelocation"),
        "ets_mycircle":             feed_row.get("mycircle"),
        "ets_sharetooffer":         feed_row.get("sharetooffer"),
        "ets_groups":               _serialize_jsonb_text(feed_row.get("groups")),
        "ets_phonebook":            _serialize_jsonb_text(feed_row.get("phonebook")),
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    contact_id = feed_row.get("contactid")
    userprofile_id = feed_row.get("userprofileid")
    if contact_id:
        payload["ets_ContactId@odata.bind"] = f"/contacts({contact_id})"
    if userprofile_id:
        payload["ets_UserProfileID@odata.bind"] = f"/ets_userprofiles({userprofile_id})"

    def _without_lookups(p: dict) -> dict:
        return {k: v for k, v in p.items() if not k.endswith("@odata.bind")}

    def _is_missing_related_entity(resp) -> bool:
        try:
            return resp.status_code == 404 and "Does Not Exist" in resp.json()["error"]["message"]
        except Exception:
            logger.debug("feed mirror: could not read the Dataverse error body; assuming the related entity exists", exc_info=True)
            return False

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{DATAVERSE_ENTITY}({feed_id})"
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
                logger.info("DATAVERSE_INSERT_FEED | SUCCESS | feedid=%s", feed_id)
                return

            if _is_missing_related_entity(response):
                response = await client.patch(upsert_url, json=_without_lookups(payload), headers=headers)
                if response.status_code == 204:
                    logger.info("DATAVERSE_INSERT_FEED | SUCCESS | feedid=%s", feed_id)
                    return

            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)

                if response.status_code in (200, 201):
                    logger.info("DATAVERSE_INSERT_FEED | SUCCESS | feedid=%s", feed_id)
                    return

                if _is_missing_related_entity(response):
                    response = await client.post(create_url, json=_without_lookups(payload), headers=headers)
                    if response.status_code in (200, 201):
                        logger.info("DATAVERSE_INSERT_FEED | SUCCESS | feedid=%s", feed_id)
                        return

            logger.error(
                "DATAVERSE_INSERT_FEED | FAILED | feedid=%s | status=%s | body=%s",
                feed_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error("DATAVERSE_INSERT_FEED | ERROR | feedid=%s | %s", feed_id, exc, exc_info=True)


async def insert_feed_comment_to_dataverse(comment_row: dict) -> None:
    """Upsert a feed comment row into the ets_feedcommentses Dataverse entity by PATCH then POST."""
    feed_comment_id = comment_row.get("feedcommentsid")
    if not feed_comment_id:
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_INSERT_FEEDCOMMENT | FAILED_TOKEN | feedcommentsid=%s | %s", feed_comment_id, exc, exc_info=True)
        return

    payload = {
        "ets_textcomments": comment_row.get("textcomments"),
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    commented_by_contact_id = comment_row.get("commentedbycontactid")
    if commented_by_contact_id:
        payload["ets_CommentedBy@odata.bind"] = f"/contacts({commented_by_contact_id})"

    commentedby_userprofile_id = comment_row.get("commentedbyuserprofileid")
    if commentedby_userprofile_id:
        payload["ets_CommentedByUserprofile@odata.bind"] = f"/ets_userprofiles({commentedby_userprofile_id})"

    feed_id = comment_row.get("feedid")
    if feed_id:
        payload["ets_FeedId@odata.bind"] = f"/ets_feeds({feed_id})"

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/ets_feedcommentses({feed_comment_id})"
    create_url = f"{base_url}/ets_feedcommentses"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info("DATAVERSE_INSERT_FEEDCOMMENT | SUCCESS | feedcommentsid=%s", feed_comment_id)
                return
            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info("DATAVERSE_INSERT_FEEDCOMMENT | SUCCESS | feedcommentsid=%s", feed_comment_id)
                    return
            logger.error(
                "DATAVERSE_INSERT_FEEDCOMMENT | FAILED | feedcommentsid=%s | status=%s | body=%s",
                feed_comment_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error("DATAVERSE_INSERT_FEEDCOMMENT | ERROR | feedcommentsid=%s | %s", feed_comment_id, exc, exc_info=True)


async def update_feed_comment_in_dataverse(feed_comment_row_id: str, commented_by: str, feed_id: str, comments: str, commentedby_userprofile: str) -> None:
    """PATCH an existing Dataverse feed comment's text and its commenter, userprofile and feed lookups."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_UPDATE_FEEDCOMMENT | FAILED_TOKEN | feedcommentsid=%s | %s", feed_comment_row_id, exc, exc_info=True)
        return

    url = f"{settings.DATAVERSE_URL}/api/data/v9.2/ets_feedcommentses({feed_comment_row_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }
    payload = {
        "ets_textcomments": comments,
        "ets_CommentedBy@odata.bind": f"/contacts({commented_by})",
        "ets_CommentedByUserprofile@odata.bind": f"/ets_userprofiles({commentedby_userprofile})",
        "ets_FeedId@odata.bind": f"/ets_feeds({feed_id})",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(url, json=payload, headers=headers)
        if response.status_code == 204:
            logger.info("DATAVERSE_UPDATE_FEEDCOMMENT | SUCCESS | feedcommentsid=%s", feed_comment_row_id)
        else:
            logger.error(
                "DATAVERSE_UPDATE_FEEDCOMMENT | FAILED | feedcommentsid=%s | status=%s | body=%s",
                feed_comment_row_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error("DATAVERSE_UPDATE_FEEDCOMMENT | ERROR | feedcommentsid=%s | %s", feed_comment_row_id, exc, exc_info=True)


async def delete_feed_comment_in_dataverse(feed_comment_row_id: str) -> None:
    """DELETE a feed comment record from the ets_feedcommentses Dataverse entity, logging the outcome."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_DELETE_FEEDCOMMENT | FAILED_TOKEN | feedcommentsid=%s | %s", feed_comment_row_id, exc, exc_info=True)
        return

    url = f"{settings.DATAVERSE_URL}/api/data/v9.2/ets_feedcommentses({feed_comment_row_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "OData-Version": "4.0",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.delete(url, headers=headers)
        if response.status_code == 204:
            logger.info("DATAVERSE_DELETE_FEEDCOMMENT | SUCCESS | feedcommentsid=%s", feed_comment_row_id)
        elif response.status_code == 404:
            logger.warning("DATAVERSE_DELETE_FEEDCOMMENT | NOT_FOUND | feedcommentsid=%s", feed_comment_row_id)
        else:
            logger.error(
                "DATAVERSE_DELETE_FEEDCOMMENT | FAILED | feedcommentsid=%s | status=%s | body=%s",
                feed_comment_row_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error("DATAVERSE_DELETE_FEEDCOMMENT | ERROR | feedcommentsid=%s | %s", feed_comment_row_id, exc, exc_info=True)


async def _delete_dataverse_records(client: httpx.AsyncClient, base_url: str, entity: str, record_ids: list, headers: dict, label: str) -> None:
    """Delete each listed record id from a Dataverse entity collection, logging per-record outcomes."""
    for record_id in record_ids:
        url = f"{base_url}/{entity}({record_id})"
        try:
            resp = await client.delete(url, headers=headers)
            if resp.status_code == 204:
                logger.info("%s | DELETED | id=%s", label, record_id)
            elif resp.status_code == 404:
                logger.warning("%s | NOT_FOUND | id=%s", label, record_id)
            else:
                logger.error("%s | FAILED | id=%s | status=%s | body=%s", label, record_id, resp.status_code, resp.text)
        except Exception as exc:
            logger.error("%s | ERROR | id=%s | %s", label, record_id, exc, exc_info=True)


async def insert_feed_like_to_dataverse(like_row: dict) -> None:
    """Upsert a feed like row into the ets_feedlikeses Dataverse entity by PATCH then POST."""
    feed_likes_id = like_row.get("feedlikesid")
    if not feed_likes_id:
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_INSERT_FEEDLIKE | FAILED_TOKEN | feedlikesid=%s | %s", feed_likes_id, exc, exc_info=True)
        return

    payload = {
        "ets_likemode": like_row.get("likemode"),
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    feed_id = like_row.get("feedid")
    if feed_id:
        payload["ets_FeedId@odata.bind"] = f"/ets_feeds({feed_id})"

    userprofile_id = like_row.get("likesbyuserprofileid")
    if userprofile_id:
        payload["ets_likesby@odata.bind"] = f"/ets_userprofiles({userprofile_id})"

    liked_by_contact_id = like_row.get("likedbycontactid")
    if liked_by_contact_id:
        payload["ets_LikedBy@odata.bind"] = f"/contacts({liked_by_contact_id})"

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/ets_feedlikeses({feed_likes_id})"
    create_url = f"{base_url}/ets_feedlikeses"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info("DATAVERSE_INSERT_FEEDLIKE | SUCCESS | feedlikesid=%s", feed_likes_id)
                return
            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info("DATAVERSE_INSERT_FEEDLIKE | SUCCESS | feedlikesid=%s", feed_likes_id)
                    return
            logger.error(
                "DATAVERSE_INSERT_FEEDLIKE | FAILED | feedlikesid=%s | status=%s | body=%s",
                feed_likes_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error("DATAVERSE_INSERT_FEEDLIKE | ERROR | feedlikesid=%s | %s", feed_likes_id, exc, exc_info=True)


async def delete_feed_in_dataverse(feed_id: str) -> None:
    """Delete a feed's comments and likes, then the feed itself, from Dataverse, logging failures."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_DELETE_FEED | FAILED_TOKEN | feedid=%s | %s", feed_id, exc, exc_info=True)
        return

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "OData-Version": "4.0",
    }
    json_headers = {**headers, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            comments_url = (
                f"{base_url}/ets_feedcommentses"
                f"?$filter=_ets_feedid_value eq {feed_id}&$select=ets_feedcommentsid"
            )
            comments_resp = await client.get(comments_url, headers=json_headers)
            if comments_resp.status_code == 200:
                comment_ids = [r["ets_feedcommentsid"] for r in comments_resp.json().get("value", [])]
                await _delete_dataverse_records(client, base_url, "ets_feedcommentses", comment_ids, headers, "DATAVERSE_DELETE_FEEDCOMMENT")
            else:
                logger.warning("DATAVERSE_DELETE_FEED | COMMENTS_FETCH_FAILED | feedid=%s | status=%s", feed_id, comments_resp.status_code)

            likes_url = (
                f"{base_url}/ets_feedlikeses"
                f"?$filter=_ets_feedid_value eq {feed_id}&$select=ets_feedlikesid"
            )
            likes_resp = await client.get(likes_url, headers=json_headers)
            if likes_resp.status_code == 200:
                like_ids = [r["ets_feedlikesid"] for r in likes_resp.json().get("value", [])]
                await _delete_dataverse_records(client, base_url, "ets_feedlikeses", like_ids, headers, "DATAVERSE_DELETE_FEEDLIKE")
            else:
                logger.warning("DATAVERSE_DELETE_FEED | LIKES_FETCH_FAILED | feedid=%s | status=%s", feed_id, likes_resp.status_code)

            feed_url = f"{base_url}/{DATAVERSE_ENTITY}({feed_id})"
            response = await client.delete(feed_url, headers=headers)
            if response.status_code == 204:
                logger.info("DATAVERSE_DELETE_FEED | SUCCESS | feedid=%s", feed_id)
            elif response.status_code == 404:
                logger.warning("DATAVERSE_DELETE_FEED | NOT_FOUND | feedid=%s", feed_id)
            else:
                logger.error(
                    "DATAVERSE_DELETE_FEED | FAILED | feedid=%s | status=%s | body=%s",
                    feed_id, response.status_code, response.text,
                )
    except Exception as exc:
        logger.error("DATAVERSE_DELETE_FEED | ERROR | feedid=%s | %s", feed_id, exc, exc_info=True)
