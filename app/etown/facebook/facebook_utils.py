"""Facebook Utilities - Page OAuth, publishing and insights through the Graph API.

Domain layer behind /api/facebook: runs the Facebook Login code exchange and Page discovery, stores connections in etown_facebookconnections, publishes Etown feed posts to the connected Page as text, photo, album or video, and records posts and insights in etown_facebook_posts and etown_social_insights_snapshots.

Features:
    - OAuth + Page Discovery: Code exchange, long-lived token upgrade, Pages via /me/accounts
    - Page Token Storage: the Page access token from /me/accounts is stored and used for every publish and insights call
    - Media-aware Publishing: Picks video, photo, album or text status from the feed post's media
    - Post + Insights Storage: Saves feed_id -> facebook_post_id and seeds insight snapshot rows
    - Live + Cached Insights: Live Graph API insights, or the last stored snapshot from the DB
    - Signed Request Verification: HMAC-SHA256 check of Meta's signed_request payloads
    - Deauthorize + Data Deletion Handling: Deactivates or deletes a Facebook user's stored data
    - Caption Link Tracking: openapp deep-link builder plus a click counter

Classes:
    No classes defined (module exposes OAuth, publishing, and insights helper functions)

Functions:
    build_app_open_link(): Build the openapp deep link (with campaign code) for captions
    get_oauth_url(): Build the Facebook authorization URL with required scopes
    exchange_code_for_token(): Exchange an authorization code for a short-lived user token
    upgrade_to_long_lived_token(): Exchange short-lived user token for a ~60-day long-lived token
    get_user_id(): Return the Facebook User ID for a user access token
    get_user_pages(): Discover the Pages the user manages (with Page access tokens)
    get_facebook_page_profile(): Fetch Facebook Page profile data (name, fan_count, etc.)
    save_facebook_connection(): Persist a new or updated connection record to the database
    get_connection_by_user(): Fetch an active Facebook connection record for a user
    deactivate_connection(): Soft-delete (deactivate) a Facebook connection record
    parse_signed_request(): Decode and HMAC-verify a Meta signed_request payload
    deactivate_connections_by_fb_user(): Deactivate every connection owned by a Facebook user
    delete_user_data_by_fb_user(): Delete stored posts and connections for a Facebook user
    connect_facebook_account(): Orchestrate the full OAuth callback flow end-to-end
    get_connection_for_publishing(): Fetch the active connection's Page token for publishing
    _publish_text(): Publish a text-only status to the Page feed
    _publish_photo(): Publish a single photo to the Page
    _upload_unpublished_photo(): Upload a photo without publishing it, for use in an album
    _publish_album(): Publish a multi-photo album via unpublished photo uploads + attached_media
    _publish_video(): Publish a video to the Page
    auto_publish_feed_to_facebook(): Publish an Etown feed post to the connected Page
    save_facebook_post(): Persist the feed_id -> facebook_post_id mapping after a publish
    get_all_facebook_posts(): Return all published Facebook posts for a user from the database
    _fetch_latest_snapshot(): Read a post's stored insights snapshot for a given/widest window
    get_post_insights_from_db(): Return DB-only Facebook insights for a feed post, no live call
    get_post_insights(): Fetch live insights for a published Facebook post
    track_caption_link_click(): Increment the caption-link click counter

Dependencies:
    - httpx: Async HTTP client for Facebook Graph API calls
    - asyncio: Concurrent album photo uploads and parallel insights/fields fetches
    - hmac / hashlib: HMAC-SHA256 verification of Meta's signed_request payloads
    - app.db.client: Async PostgreSQL database client
    - app.core.config: Application settings (app IDs, secrets, redirect URIs)
    - app.core.logging: Centralized logging
    - app.utils.blob_storage: to_public_blob_url() to make stored blob paths Graph-API-fetchable
    - app.services.social_insights_collector: create_initial_insight_rows() after a publish

Updates:
    30-Jun-2026: Initial implementation (mirrors the Instagram Business integration)
    10-Jul-2026: Local _to_public_url replaced by the canonical to_public_blob_url.
    06-Aug-2026: Caption app links carry the campaign code through the openapp deep link.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import httpx
from urllib.parse import urlencode
from typing import Optional
from datetime import datetime, timezone, timedelta

from app.db.client import Database
from app.core.config import settings
from app.core.logging import logger
from app.utils.blob_storage import to_public_blob_url

_GRAPH_VERSION = settings.FACEBOOK_GRAPH_VERSION or "v24.0"
FACEBOOK_OAUTH_AUTHORIZE_URL = f"https://www.facebook.com/{_GRAPH_VERSION}/dialog/oauth"
FACEBOOK_GRAPH_BASE = f"https://graph.facebook.com/{_GRAPH_VERSION}"
FACEBOOK_OAUTH_TOKEN_URL = f"{FACEBOOK_GRAPH_BASE}/oauth/access_token"


def build_app_open_link(feed_id: Optional[str] = None) -> str:
    """Build the Etown openapp deep link for captions, adding feed_id and the campaign code when set."""
    params = {}
    if feed_id:
        params["feed_id"] = feed_id
    if settings.APP_LINK_CAMPAIGN_CODE:
        params["code"] = settings.APP_LINK_CAMPAIGN_CODE
    if not params:
        return settings.APP_OPEN_URL
    return f"{settings.APP_OPEN_URL}?{urlencode(params)}"


REQUIRED_SCOPES = [
    "pages_show_list",
    "pages_manage_posts",
    "pages_manage_metadata",
    "pages_manage_engagement",
    "pages_read_engagement",
    "pages_read_user_content",
    "read_insights",
    "business_management",
]

PAGE_PROFILE_FIELDS = "id,name,about,category,fan_count,followers_count,link,picture{url}"

MAX_MESSAGE_LEN = 5000


def get_oauth_url(state: Optional[str] = None) -> str:
    """Build the Facebook OAuth authorization URL with the required scopes and optional state."""
    params = {
        "client_id": settings.FACEBOOK_APP_ID,
        "redirect_uri": settings.FACEBOOK_REDIRECT_URI,
        "scope": ",".join(REQUIRED_SCOPES),
        "response_type": "code",
    }
    if state:
        params["state"] = state

    url = f"{FACEBOOK_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
    logger.info(f"FACEBOOK_OAUTH | Generated OAuth URL for state={state}")
    return url


async def exchange_code_for_token(code: str) -> dict:
    """Exchange a Facebook OAuth authorization code for a short-lived user access token.

    Raises:
        ValueError: When Facebook returns an error or the token is missing.
    """
    code = code.split("#")[0]

    params = {
        "client_id": settings.FACEBOOK_APP_ID,
        "client_secret": settings.FACEBOOK_APP_SECRET,
        "redirect_uri": settings.FACEBOOK_REDIRECT_URI,
        "code": code,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(FACEBOOK_OAUTH_TOKEN_URL, params=params)

    try:
        payload = response.json()
    except Exception:
        logger.error(
            f"FACEBOOK_TOKEN_EXCHANGE_ERROR | Non-JSON response (status={response.status_code}) | "
            f"body={response.text[:500]}"
        , exc_info=True)
        raise ValueError(
            f"Facebook token exchange returned a non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )

    if isinstance(payload, dict) and payload.get("error"):
        error_msg = (payload.get("error", {}) or {}).get("message", "Unknown error from Facebook")
        logger.error(f"FACEBOOK_TOKEN_EXCHANGE_ERROR | {error_msg}")
        raise ValueError(f"Facebook token exchange failed: {error_msg}")

    if "access_token" not in payload:
        logger.error(f"FACEBOOK_TOKEN_EXCHANGE_ERROR | Unexpected response: {payload}")
        raise ValueError("No access_token in Facebook token exchange response")

    logger.info("FACEBOOK_TOKEN_EXCHANGE | Short-lived user token obtained successfully")
    return payload


async def upgrade_to_long_lived_token(short_lived_token: str) -> dict:
    """Exchange a short-lived Facebook user token for a long-lived one.

    Raises:
        ValueError: When the upgrade call fails or returns an error.
    """
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": settings.FACEBOOK_APP_ID,
        "client_secret": settings.FACEBOOK_APP_SECRET,
        "fb_exchange_token": short_lived_token,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(FACEBOOK_OAUTH_TOKEN_URL, params=params)

    try:
        data = response.json()
    except Exception:
        logger.error(
            f"FACEBOOK_TOKEN_UPGRADE_ERROR | Non-JSON response (status={response.status_code}) | "
            f"body={response.text[:500]}"
        , exc_info=True)
        raise ValueError(
            f"Facebook token upgrade returned a non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error from Facebook")
        logger.error(f"FACEBOOK_TOKEN_UPGRADE_ERROR | {error_msg}")
        raise ValueError(f"Long-lived token upgrade failed: {error_msg}")

    if "access_token" not in data:
        raise ValueError("No access_token in long-lived token upgrade response")

    expires_in = data.get("expires_in", 0)
    logger.info(f"FACEBOOK_TOKEN_UPGRADE | Long-lived user token obtained. Expires in {expires_in}s")
    return data


async def get_user_id(user_token: str) -> Optional[str]:
    """Return the Facebook user id for a user access token, or None on a Graph error."""
    url = f"{FACEBOOK_GRAPH_BASE}/me"
    params = {"fields": "id,name", "access_token": user_token}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params)
    data = response.json()
    if "error" in data:
        logger.warning("FACEBOOK_ME_ERROR | %s", data["error"].get("message"))
        return None
    return data.get("id")


async def get_user_pages(user_token: str) -> list:
    """Return the Pages the user manages, each with id, name, access_token and category.

    Raises:
        ValueError: When the Graph API call fails.
    """
    url = f"{FACEBOOK_GRAPH_BASE}/me/accounts"
    params = {"fields": "id,name,access_token,category", "access_token": user_token}

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params)

    try:
        data = response.json()
    except Exception:
        logger.error(
            f"FACEBOOK_PAGES_ERROR | Non-JSON response (status={response.status_code}) | "
            f"body={response.text[:500]}"
        , exc_info=True)
        raise ValueError(
            f"Facebook pages fetch returned a non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error from Facebook")
        logger.error(f"FACEBOOK_PAGES_ERROR | {error_msg}")
        raise ValueError(f"Failed to fetch Facebook Pages: {error_msg}")

    pages = data.get("data", [])
    logger.info(f"FACEBOOK_PAGES | Found {len(pages)} page(s) for user token")
    return pages


async def get_facebook_page_profile(page_id: str, page_token: str) -> dict:
    """Fetch a Facebook Page profile and add a flattened profile_picture_url.

    Raises:
        ValueError: When the Facebook Graph API call fails.
    """
    url = f"{FACEBOOK_GRAPH_BASE}/{page_id}"
    params = {"fields": PAGE_PROFILE_FIELDS, "access_token": page_token}

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params)

    try:
        data = response.json()
    except Exception:
        logger.error(
            f"FACEBOOK_PROFILE_FETCH_ERROR | Non-JSON response (status={response.status_code}) | "
            f"page_id={page_id} | body={response.text[:500]}"
        , exc_info=True)
        raise ValueError(
            f"Facebook profile fetch returned a non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error from Facebook")
        logger.error(f"FACEBOOK_PROFILE_FETCH_ERROR | page_id={page_id} | {error_msg}")
        raise ValueError(f"Failed to fetch Facebook Page profile: {error_msg}")

    picture = (data.get("picture") or {}).get("data") or {}
    data["profile_picture_url"] = picture.get("url")
    logger.info(f"FACEBOOK_PROFILE_FETCH | page_id={page_id} name={data.get('name')}")
    return data


async def save_facebook_connection(
    contactid: str,
    userprofileid: Optional[str],
    fb_user_id: Optional[str],
    fb_page_id: str,
    fb_page_name: Optional[str],
    page_access_token: str,
    user_access_token: Optional[str],
    token_expires_at: Optional[datetime],
) -> dict:
    """Upsert an active Facebook Page connection for a contact and profile and return the saved row."""
    query = """
        INSERT INTO etown_facebookconnections (
            contactid,
            userprofileid,
            fb_user_id,
            fb_page_id,
            fb_page_name,
            page_access_token,
            user_access_token,
            token_expires_at,
            is_active,
            connected_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, NOW(), NOW())
        ON CONFLICT ON CONSTRAINT etown_facebookconnections_contactid_userprofileid_fb_page_i_key
        DO UPDATE SET
            userprofileid        = EXCLUDED.userprofileid,
            fb_user_id           = EXCLUDED.fb_user_id,
            fb_page_name         = EXCLUDED.fb_page_name,
            page_access_token    = EXCLUDED.page_access_token,
            user_access_token    = EXCLUDED.user_access_token,
            token_expires_at     = EXCLUDED.token_expires_at,
            is_active            = TRUE,
            updated_at           = NOW()
        RETURNING
            facebookconnectionsid::text,
            contactid,
            userprofileid,
            fb_user_id,
            fb_page_id,
            fb_page_name,
            token_expires_at,
            is_active,
            connected_at,
            updated_at
    """
    row = await Database.fetchrow(
        query,
        contactid,
        userprofileid,
        fb_user_id,
        fb_page_id,
        fb_page_name,
        page_access_token,
        user_access_token,
        token_expires_at,
    )
    logger.info(
        f"FACEBOOK_CONNECTION_SAVED | contactid={contactid} userprofileid={userprofileid} "
        f"page_id={fb_page_id} page_name={fb_page_name}"
    )
    return dict(row)


async def get_connection_by_user(contactid: str, userprofileid: Optional[str] = None) -> Optional[dict]:
    """Return the newest active Facebook connection for a contact, narrowed to a profile when given, or None."""
    if userprofileid:
        query = """
            SELECT
                facebookconnectionsid::text,
                contactid,
                userprofileid,
                fb_user_id,
                fb_page_id,
                fb_page_name,
                token_expires_at,
                is_active,
                connected_at,
                updated_at
            FROM etown_facebookconnections
            WHERE contactid = $1 AND userprofileid = $2 AND is_active = TRUE
            ORDER BY connected_at DESC
            LIMIT 1
        """
        row = await Database.fetchrow(query, contactid, userprofileid)
    else:
        query = """
            SELECT
                facebookconnectionsid::text,
                contactid,
                userprofileid,
                fb_user_id,
                fb_page_id,
                fb_page_name,
                token_expires_at,
                is_active,
                connected_at,
                updated_at
            FROM etown_facebookconnections
            WHERE contactid = $1 AND is_active = TRUE
            ORDER BY connected_at DESC
            LIMIT 1
        """
        row = await Database.fetchrow(query, contactid)
    if row:
        return dict(row)
    return None


async def deactivate_connection(contactid: str, fb_page_id: str) -> bool:
    """Mark a contact's active connection to a Page inactive and return whether a row changed."""
    query = """
        UPDATE etown_facebookconnections
        SET is_active = FALSE, updated_at = NOW()
        WHERE contactid = $1
          AND fb_page_id = $2
          AND is_active = TRUE
        RETURNING facebookconnectionsid
    """
    row = await Database.fetchrow(query, contactid, fb_page_id)
    success = row is not None
    logger.info(
        f"FACEBOOK_DISCONNECT | contactid={contactid} "
        f"page_id={fb_page_id} success={success}"
    )
    return success


def parse_signed_request(signed_request: str) -> Optional[dict]:
    """Decode a Meta signed_request and return its payload, or None when malformed or the HMAC mismatches."""
    app_secret = settings.FACEBOOK_APP_SECRET
    if not signed_request or "." not in signed_request:
        logger.warning("FACEBOOK_SIGNED_REQUEST | Malformed signed_request")
        return None

    encoded_sig, payload = signed_request.split(".", 1)

    def _b64url_decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    try:
        sig = _b64url_decode(encoded_sig)
        data = json.loads(_b64url_decode(payload))
    except Exception as exc:
        logger.warning("FACEBOOK_SIGNED_REQUEST | Decode error: %s", exc)
        return None

    if not app_secret:
        logger.warning("FACEBOOK_SIGNED_REQUEST | FACEBOOK_APP_SECRET unset — skipping signature verification")
        return data

    expected_sig = hmac.new(app_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        logger.warning("FACEBOOK_SIGNED_REQUEST | Signature mismatch")
        return None

    return data


async def deactivate_connections_by_fb_user(fb_user_id: str) -> int:
    """Deactivate every active connection owned by a Facebook user and return the count."""
    query = """
        UPDATE etown_facebookconnections
        SET is_active = FALSE, updated_at = NOW()
        WHERE fb_user_id = $1 AND is_active = TRUE
        RETURNING facebookconnectionsid
    """
    rows = await Database.fetch(query, fb_user_id)
    count = len(rows)
    logger.info("FACEBOOK_DEAUTHORIZE | fb_user_id=%s deactivated=%d", fb_user_id, count)
    return count


async def delete_user_data_by_fb_user(fb_user_id: str) -> int:
    """Delete a Facebook user's post mappings and connection rows and return the connections deleted."""
    await Database.execute(
        """
        DELETE FROM etown_facebook_posts
        WHERE fb_page_id IN (
            SELECT fb_page_id FROM etown_facebookconnections WHERE fb_user_id = $1
        )
        """,
        fb_user_id,
    )
    rows = await Database.fetch(
        """
        DELETE FROM etown_facebookconnections
        WHERE fb_user_id = $1
        RETURNING facebookconnectionsid
        """,
        fb_user_id,
    )
    count = len(rows)
    logger.info("FACEBOOK_DATA_DELETION | fb_user_id=%s deleted_connections=%d", fb_user_id, count)
    return count


async def connect_facebook_account(contactid: str, userprofileid: Optional[str], code: str) -> dict:
    """Exchange an OAuth code, connect the user's first managed Page and return the connection plus profile.

    Raises:
        ValueError: When any Facebook API call fails or the user has no manageable Page.
    """
    short_lived_data = await exchange_code_for_token(code)
    short_lived_token = short_lived_data["access_token"]

    long_lived_data = await upgrade_to_long_lived_token(short_lived_token)
    long_lived_token = long_lived_data["access_token"]
    expires_in_seconds = long_lived_data.get("expires_in", 0)

    token_expires_at: Optional[datetime] = None
    if expires_in_seconds:
        token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)

    fb_user_id = await get_user_id(long_lived_token)
    pages = await get_user_pages(long_lived_token)
    if not pages:
        raise ValueError(
            "No Facebook Page found for this account. Please create a Facebook Page (or grant "
            "the app access to one) and try connecting again."
        )

    if len(pages) > 1:
        logger.info(
            "FACEBOOK_CONNECT | contactid=%s manages %d pages — connecting the first one (page_id=%s)",
            contactid, len(pages), pages[0].get("id"),
        )

    page = pages[0]
    fb_page_id = page["id"]
    page_access_token = page["access_token"]
    profile = await get_facebook_page_profile(fb_page_id, page_access_token)

    connection = await save_facebook_connection(
        contactid=contactid,
        userprofileid=userprofileid,
        fb_user_id=fb_user_id,
        fb_page_id=fb_page_id,
        fb_page_name=profile.get("name") or page.get("name"),
        page_access_token=page_access_token,
        user_access_token=long_lived_token,
        token_expires_at=token_expires_at,
    )

    return {
        "connection": connection,
        "profile": {
            "fb_page_id": fb_page_id,
            "fb_page_name": profile.get("name") or page.get("name"),
            "fb_user_id": fb_user_id,
            "category": profile.get("category") or page.get("category"),
            "about": profile.get("about"),
            "fan_count": profile.get("fan_count"),
            "followers_count": profile.get("followers_count"),
            "link": profile.get("link"),
            "profile_picture_url": profile.get("profile_picture_url"),
        },
    }


async def get_connection_for_publishing(contactid: str, userprofileid: str) -> Optional[dict]:
    """Return the newest active connection for a contact and profile including its Page token, or None."""
    query = """
        SELECT
            fb_page_id,
            page_access_token,
            token_expires_at,
            userprofileid
        FROM etown_facebookconnections
        WHERE contactid = $1 AND userprofileid = $2 AND is_active = TRUE
        ORDER BY connected_at DESC
        LIMIT 1
    """
    row = await Database.fetchrow(query, contactid, userprofileid)
    if row:
        return dict(row)
    return None


async def _publish_text(page_id: str, page_token: str, message: str) -> str:
    """Publish a text-only status to the Page feed and return the post id."""
    url = f"{FACEBOOK_GRAPH_BASE}/{page_id}/feed"
    params = {"message": message, "privacy": json.dumps({"value": "EVERYONE"}), "access_token": page_token}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, params=params)

    data = response.json()
    if "error" in data:
        raise ValueError(f"Facebook feed publish error: {data['error'].get('message', 'Unknown error')}")
    post_id = data.get("id")
    if not post_id:
        raise ValueError(f"Facebook feed publish returned no id: {data}")
    return post_id


async def _publish_photo(page_id: str, page_token: str, image_url: str, message: str) -> str:
    """Publish a single photo to the Page and return the resulting post id."""
    url = f"{FACEBOOK_GRAPH_BASE}/{page_id}/photos"
    params = {"url": image_url, "caption": message, "privacy": json.dumps({"value": "EVERYONE"}), "access_token": page_token}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, params=params)

    data = response.json()
    if "error" in data:
        raise ValueError(f"Facebook photo publish error: {data['error'].get('message', 'Unknown error')}")
    post_id = data.get("post_id") or data.get("id")
    if not post_id:
        raise ValueError(f"Facebook photo publish returned no id: {data}")
    return post_id


async def _upload_unpublished_photo(page_id: str, page_token: str, image_url: str) -> str:
    """Upload a photo without publishing it and return its media fbid (for albums)."""
    url = f"{FACEBOOK_GRAPH_BASE}/{page_id}/photos"
    params = {"url": image_url, "published": "false", "access_token": page_token}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, params=params)

    data = response.json()
    if "error" in data:
        raise ValueError(f"Facebook photo upload error: {data['error'].get('message', 'Unknown error')}")
    media_id = data.get("id")
    if not media_id:
        raise ValueError(f"Facebook photo upload returned no id: {data}")
    return media_id


async def _publish_album(page_id: str, page_token: str, image_urls: list, message: str) -> str:
    """Upload each photo unpublished, attach them to one Page feed post and return its id."""
    media_ids = await asyncio.gather(*[
        _upload_unpublished_photo(page_id, page_token, url) for url in image_urls
    ])

    url = f"{FACEBOOK_GRAPH_BASE}/{page_id}/feed"
    attached_media = [{"media_fbid": mid} for mid in media_ids]
    params = {
        "message": message,
        "attached_media": json.dumps(attached_media),
        "privacy": json.dumps({"value": "EVERYONE"}),
        "access_token": page_token,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, params=params)

    data = response.json()
    if "error" in data:
        raise ValueError(f"Facebook album publish error: {data['error'].get('message', 'Unknown error')}")
    post_id = data.get("id")
    if not post_id:
        raise ValueError(f"Facebook album publish returned no id: {data}")
    return post_id


async def _publish_video(page_id: str, page_token: str, video_url: str, message: str) -> str:
    """Publish a video to the Page and return the resulting post/video id."""
    url = f"{FACEBOOK_GRAPH_BASE}/{page_id}/videos"
    params = {"file_url": video_url, "description": message, "access_token": page_token}

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, params=params)

    data = response.json()
    if "error" in data:
        raise ValueError(f"Facebook video publish error: {data['error'].get('message', 'Unknown error')}")
    post_id = data.get("post_id") or data.get("id")
    if not post_id:
        raise ValueError(f"Facebook video publish returned no id: {data}")
    return post_id


async def auto_publish_feed_to_facebook(
    contact_id: str,
    userprofile_id: str,
    feed_text: Optional[str],
    image_url: Optional[str],
    extra_image_urls: Optional[list],
    video_url: Optional[str],
    feed_id: Optional[str] = None,
) -> None:
    """Publish a feed post to the user's connected Page as video, album, photo or status, logging all errors.

    Args:
        contact_id: Contact whose Facebook connection is used.
        userprofile_id: Profile whose Facebook connection is used.
        feed_text: Caption text or Quill delta JSON, may be None.
        image_url: Primary image blob URL, may be None.
        extra_image_urls: Additional image blob URLs for an album, may be None.
        video_url: Video blob URL, may be None.
        feed_id: Etown feed id for the tracking link and post mapping.
    """
    try:
        connection = await get_connection_for_publishing(contact_id, userprofile_id)
    except Exception as exc:
        logger.error("FACEBOOK_AUTO_PUBLISH | DB_ERROR | contact_id=%s | %s", contact_id, exc, exc_info=True)
        return

    if not connection:
        logger.info("FACEBOOK_AUTO_PUBLISH | NO_CONNECTION | contact_id=%s — skipping", contact_id)
        return

    page_id = connection["fb_page_id"]
    page_token = connection["page_access_token"]

    raw_text = feed_text or ""
    try:
        delta = json.loads(raw_text)
        if isinstance(delta, list):
            raw_text = "".join(
                op["insert"] for op in delta
                if isinstance(op, dict) and isinstance(op.get("insert"), str)
            ).rstrip("\n")
    except (json.JSONDecodeError, TypeError):
        logger.debug("facebook: feed text is not a Quill delta; posting it verbatim")

    app_link = (
        f"{settings.API_BASE_URL}/api/facebook/track/{feed_id}"
        if feed_id
        else build_app_open_link()
    )
    app_suffix = f"\n{app_link}"
    max_text_len = MAX_MESSAGE_LEN - len(app_suffix)
    message = raw_text[:max_text_len] + app_suffix

    token_expires_at = connection.get("token_expires_at")
    if token_expires_at and datetime.now(timezone.utc) >= token_expires_at:
        logger.warning(
            "FACEBOOK_AUTO_PUBLISH | TOKEN_EXPIRED | contact_id=%s page_id=%s — attempting publish anyway",
            contact_id, page_id,
        )

    try:
        post_id: Optional[str] = None
        media_type: str = "STATUS"
        if video_url:
            post_id = await _publish_video(page_id, page_token, to_public_blob_url(video_url), message)
            media_type = "VIDEO"
            logger.info("FACEBOOK_AUTO_PUBLISH | VIDEO_SUCCESS | contact_id=%s page_id=%s post_id=%s", contact_id, page_id, post_id)
        else:
            all_images = [to_public_blob_url(u) for u in ([image_url] + (extra_image_urls or [])) if u]
            if not all_images:
                post_id = await _publish_text(page_id, page_token, message)
                media_type = "STATUS"
                logger.info("FACEBOOK_AUTO_PUBLISH | STATUS_SUCCESS | contact_id=%s page_id=%s post_id=%s", contact_id, page_id, post_id)
            elif len(all_images) == 1:
                post_id = await _publish_photo(page_id, page_token, all_images[0], message)
                media_type = "PHOTO"
                logger.info("FACEBOOK_AUTO_PUBLISH | PHOTO_SUCCESS | contact_id=%s page_id=%s post_id=%s", contact_id, page_id, post_id)
            else:
                post_id = await _publish_album(page_id, page_token, all_images[:10], message)
                media_type = "ALBUM"
                logger.info("FACEBOOK_AUTO_PUBLISH | ALBUM_SUCCESS | contact_id=%s page_id=%s post_id=%s items=%d", contact_id, page_id, post_id, len(all_images))

        if feed_id and post_id:
            await save_facebook_post(
                feed_id=feed_id,
                contactid=contact_id,
                facebook_post_id=post_id,
                fb_page_id=page_id,
                media_type=media_type,
                userprofileid=connection.get("userprofileid"),
            )
    except Exception as exc:
        logger.error(
            "FACEBOOK_AUTO_PUBLISH | FAILED | contact_id=%s page_id=%s | %s",
            contact_id, page_id, exc, exc_info=True,
        )


async def save_facebook_post(
    feed_id: str,
    contactid: str,
    facebook_post_id: str,
    fb_page_id: str,
    media_type: str,
    userprofileid: Optional[str] = None,
) -> None:
    """Upsert the feed-to-Facebook-post mapping and seed its initial insight rows, logging any error."""
    query = """
        INSERT INTO etown_facebook_posts
            (feed_id, contactid, userprofileid, facebook_post_id, fb_page_id, media_type)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (feed_id) DO UPDATE SET
            facebook_post_id = EXCLUDED.facebook_post_id,
            fb_page_id       = EXCLUDED.fb_page_id,
            media_type       = EXCLUDED.media_type,
            published_at     = NOW()
    """
    try:
        await Database.execute(query, feed_id, contactid, userprofileid, facebook_post_id, fb_page_id, media_type)
        logger.info(
            "FACEBOOK_POST_SAVED | feed_id=%s post_id=%s media_type=%s",
            feed_id, facebook_post_id, media_type,
        )
        from app.services.social_insights_collector import create_initial_insight_rows
        await create_initial_insight_rows(
            "facebook", feed_id, contactid, userprofileid, facebook_post_id, media_type,
        )
    except Exception as exc:
        logger.error("FACEBOOK_POST_SAVE_ERROR | feed_id=%s | %s", feed_id, exc, exc_info=True)


async def get_all_facebook_posts(
    contactid: Optional[str] = None,
    userprofileid: Optional[str] = None,
) -> list:
    """Return a user's published Facebook posts by contact (optionally profile-scoped) or by profile alone."""
    if not contactid and not userprofileid:
        return []

    if contactid:
        query = """
            SELECT
                p.facebookpostid::text,
                p.feed_id,
                p.contactid,
                p.facebook_post_id,
                p.fb_page_id,
                p.media_type,
                p.published_at
            FROM etown_facebook_posts p
            WHERE p.contactid = $1
              AND ($2::text IS NULL OR p.userprofileid = $2)
            ORDER BY p.published_at DESC
        """
        rows = await Database.fetch(query, contactid, userprofileid)
    else:
        query = """
            SELECT
                p.facebookpostid::text,
                p.feed_id,
                p.contactid,
                p.facebook_post_id,
                p.fb_page_id,
                p.media_type,
                p.published_at
            FROM etown_facebook_posts p
            JOIN etown_facebookconnections c
              ON c.fb_page_id = p.fb_page_id
             AND c.contactid = p.contactid
             AND c.is_active = TRUE
            WHERE c.userprofileid = $1
            ORDER BY p.published_at DESC
        """
        rows = await Database.fetch(query, userprofileid)

    return [dict(r) for r in rows]


async def _fetch_latest_snapshot(feed_id: str, period_days: Optional[int] = None) -> Optional[dict]:
    """Return a feed's Facebook insights snapshot for the given window, else the widest one, or None."""
    base = (
        "SELECT impressions, reach, reactions, likes, comments, shares, "
        "clicks, video_views, caption_link_clicks, captured_at "
        "FROM etown_social_insights_snapshots "
        "WHERE feed_id = $1 AND platform = 'facebook'"
    )
    if period_days is not None:
        row = await Database.fetchrow(base + " AND period_days = $2 LIMIT 1", feed_id, period_days)
    else:
        row = await Database.fetchrow(base + " ORDER BY period_days DESC, captured_at DESC LIMIT 1", feed_id)
    return dict(row) if row else None


async def get_post_insights_from_db(
    feed_id: str,
    contactid: Optional[str] = None,
    period_days: Optional[int] = None,
) -> Optional[dict]:
    """Return stored Facebook metrics for a feed post with missing values as 0, or None if unpublished.

    Args:
        feed_id: Etown feed post id.
        contactid: Optional owner filter; a post owned by someone else returns None.
        period_days: Optional snapshot window such as 7, 15 or 30.
    """
    if contactid:
        row = await Database.fetchrow(
            """
            SELECT p.facebook_post_id, p.media_type, p.published_at, p.caption_link_clicks,
                   COALESCE(f.feedfilelocationurl, f.feedvideofilelocation, f.thumbnailfileurl) AS feed_url,
                   f.thumbnailfileurl AS thumbnail_url
            FROM etown_facebook_posts p
            LEFT JOIN etown_feed f ON f.feedid = p.feed_id
            WHERE p.feed_id = $1 AND p.contactid = $2
            LIMIT 1
            """,
            feed_id, contactid,
        )
    else:
        row = await Database.fetchrow(
            """
            SELECT p.facebook_post_id, p.media_type, p.published_at, p.caption_link_clicks,
                   COALESCE(f.feedfilelocationurl, f.feedvideofilelocation, f.thumbnailfileurl) AS feed_url,
                   f.thumbnailfileurl AS thumbnail_url
            FROM etown_facebook_posts p
            LEFT JOIN etown_feed f ON f.feedid = p.feed_id
            WHERE p.feed_id = $1
            LIMIT 1
            """,
            feed_id,
        )
    if not row:
        return None

    snapshot = await _fetch_latest_snapshot(feed_id, period_days) or {}
    published_at = row["published_at"]

    return {
        "feed_id": feed_id,
        "facebook_post_id": row["facebook_post_id"],
        "media_type": row["media_type"],
        "published_at": published_at.isoformat() if published_at else None,
        "feed_url": row["feed_url"],
        "thumbnail_url": row["thumbnail_url"],
        "impressions": snapshot.get("impressions") or 0,
        "reach": snapshot.get("reach") or 0,
        "reactions": snapshot.get("reactions") or 0,
        "likes": snapshot.get("likes") or 0,
        "comments": snapshot.get("comments") or 0,
        "shares": snapshot.get("shares") or 0,
        "clicks": snapshot.get("clicks") or 0,
        "video_views": snapshot.get("video_views") or 0,
        "caption_link_clicks": row["caption_link_clicks"] or 0,
    }


async def get_post_insights(feed_id: str, contactid: Optional[str] = None) -> dict:
    """Fetch live Graph insights and reaction counts for a feed's Facebook post, with publishing or deleted fallbacks."""
    if contactid:
        post_query = """
            SELECT p.facebook_post_id, p.media_type, p.published_at,
                   p.contactid, p.userprofileid, p.caption_link_clicks,
                   c.page_access_token
            FROM etown_facebook_posts p
            JOIN etown_facebookconnections c
              ON c.fb_page_id = p.fb_page_id
             AND c.contactid = p.contactid
             AND c.is_active = TRUE
            WHERE p.feed_id = $1 AND p.contactid = $2
            ORDER BY c.connected_at DESC
            LIMIT 1
        """
        row = await Database.fetchrow(post_query, feed_id, contactid)
    else:
        post_query = """
            SELECT p.facebook_post_id, p.media_type, p.published_at,
                   p.contactid, p.userprofileid, p.caption_link_clicks,
                   c.page_access_token
            FROM etown_facebook_posts p
            JOIN etown_facebookconnections c
              ON c.fb_page_id = p.fb_page_id
             AND c.contactid = p.contactid
             AND c.is_active = TRUE
            WHERE p.feed_id = $1
            ORDER BY c.connected_at DESC
            LIMIT 1
        """
        row = await Database.fetchrow(post_query, feed_id)

    if not row:
        return {
            "feed_id": feed_id,
            "status": "publishing",
            "message": "Post is still being published to Facebook. Please check again in a few seconds.",
        }

    post_id = row["facebook_post_id"]
    media_type = row["media_type"]
    page_token = row["page_access_token"]
    published_at = row["published_at"]
    contactid = contactid or row["contactid"]
    caption_link_clicks: int = row["caption_link_clicks"] or 0

    metrics = "post_media_view,post_impressions_unique,post_engaged_users"

    insights_url = f"{FACEBOOK_GRAPH_BASE}/{post_id}/insights"
    fields_url = f"{FACEBOOK_GRAPH_BASE}/{post_id}"

    async def _fetch_insights(metric_str: str) -> dict:
        params = {"metric": metric_str, "access_token": page_token}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(insights_url, params=params)
            return response.json()
        except Exception as exc:
            logger.warning("facebook: insights request failed for post %s", post_id, exc_info=True)
            return {"error": {"message": str(exc)}}

    async def _fetch_fields() -> dict:
        """Real-time reaction/comment/share counts from the post object."""
        params = {
            "fields": "reactions.summary(true),comments.summary(true),shares",
            "access_token": page_token,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(fields_url, params=params)
            result = response.json()
            if "error" in result:
                return {}
            return result
        except Exception as exc:
            logger.warning("facebook: fields request failed for post %s", post_id, exc_info=True)
            return {}

    data, fields = await asyncio.gather(_fetch_insights(metrics), _fetch_fields())

    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error")
        error_code = data["error"].get("code")
        error_subcode = data["error"].get("error_subcode")
        if (
            any(p in error_msg.lower() for p in ("does not exist", "unsupported get request", "object with id"))
            or error_code == 803
            or (error_code == 100 and error_subcode == 33)
        ):
            return {
                "feed_id": feed_id,
                "facebook_post_id": post_id,
                "media_type": media_type,
                "published_at": published_at.isoformat() if published_at else None,
                "post_deleted": True,
                "error": "Post no longer exists on Facebook (may have been deleted).",
            }
        msg_lower = error_msg.lower()
        is_metric_timing_error = "valid insights metric" in msg_lower or "must be a valid" in msg_lower
        if "error" in data:
            error_msg = data["error"].get("message", "Unknown error")
            rt_reactions = (fields.get("reactions") or {}).get("summary", {}).get("total_count")
            rt_comments = (fields.get("comments") or {}).get("summary", {}).get("total_count")
            rt_shares = (fields.get("shares") or {}).get("count")
            if is_metric_timing_error or any(v is not None for v in (rt_reactions, rt_comments, rt_shares)):
                return {
                    "feed_id": feed_id,
                    "facebook_post_id": post_id,
                    "media_type": media_type,
                    "published_at": published_at.isoformat() if published_at else None,
                    "impressions": None,
                    "reach": None,
                    "reactions": rt_reactions if rt_reactions is not None else 0,
                    "likes": rt_reactions if rt_reactions is not None else 0,
                    "comments": rt_comments if rt_comments is not None else 0,
                    "shares": rt_shares if rt_shares is not None else 0,
                    "clicks": None,
                    "video_views": None,
                    "caption_link_clicks": caption_link_clicks,
                    "insights_note": (
                        "Facebook is still processing analytics for this post. "
                        "Impressions, reach, and clicks are usually available within a few hours."
                    ),
                }
            return {
                "feed_id": feed_id,
                "facebook_post_id": post_id,
                "media_type": media_type,
                "published_at": published_at.isoformat() if published_at else None,
                "error": error_msg,
            }

    raw: dict = {}
    for item in data.get("data", []):
        name = item.get("name")
        values = item.get("values")
        if isinstance(values, list) and values:
            raw[name] = values[0].get("value") or 0
        else:
            raw[name] = 0

    media_views = raw.get("post_media_view", 0)
    insights: dict = {
        "feed_id": feed_id,
        "facebook_post_id": post_id,
        "media_type": media_type,
        "published_at": published_at.isoformat() if published_at else None,
        "impressions": media_views,
        "reach": raw.get("post_impressions_unique", 0),
        "clicks": raw.get("post_engaged_users", 0),
        "video_views": media_views,
        "caption_link_clicks": caption_link_clicks,
    }

    reactions = (fields.get("reactions") or {}).get("summary", {}).get("total_count")
    comments = (fields.get("comments") or {}).get("summary", {}).get("total_count")
    shares = (fields.get("shares") or {}).get("count")
    insights["reactions"] = reactions if reactions is not None else 0
    insights["likes"] = insights["reactions"]
    insights["comments"] = comments if comments is not None else 0
    insights["shares"] = shares if shares is not None else 0

    return insights


async def track_caption_link_click(feed_id: str) -> str:
    """Increment a post's caption link click counter and return the app deep link to redirect to."""
    destination = build_app_open_link(feed_id)
    try:
        await Database.execute(
            """
            UPDATE etown_facebook_posts
            SET caption_link_clicks = caption_link_clicks + 1
            WHERE feed_id = $1
            """,
            feed_id,
        )
        logger.info("FACEBOOK_CAPTION_CLICK | feed_id=%s", feed_id)
    except Exception as exc:
        logger.error("FACEBOOK_CAPTION_CLICK_ERROR | feed_id=%s | %s", feed_id, exc, exc_info=True)
    return destination
