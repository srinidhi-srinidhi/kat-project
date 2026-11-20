"""Instagram Utilities - Instagram Login OAuth, feed auto-publishing and post insights.

Domain layer behind /api/instagram: connects an Instagram Business or Creator account through Instagram Login with a long-lived token in etown_instagramconnections, auto-publishes feed posts as photos, carousels or Reels into etown_instagram_posts, and serves cached and live post insights.

Features:
    - No Facebook Page Needed: the Instagram Login token exchange returns the Instagram account id directly
    - Instagram Login OAuth: authorize/exchange/upgrade against instagram.com and graph.instagram.com
    - Connection Lifecycle: save/fetch/deactivate connections; HMAC-verified deauthorize and data deletion
    - Auto-Publish: publish a feed post as a photo, carousel (up to 10 images) or Reel
    - Aspect-Ratio Normalization: out-of-range images are letterboxed on white and re-uploaded
    - Insights: DB-cached metrics plus a live Graph API insights call
    - Best-Time-To-Post: ranks top-viewed Instagram and Facebook posts to suggest a publish time
    - Caption Deep-Link Tracking: campaign-tagged app-open links with redirect click counting

Classes:
    No classes defined (module exposes function-based OAuth, publishing and insights utilities)

Functions:
    build_app_open_link(): Build the campaign-tagged Etown app deep link used in captions
    get_oauth_url(): Build the Instagram authorization URL with required scopes
    get_social_oauth_url(): Dispatch to the Instagram or Facebook OAuth URL builder
    exchange_code_for_token(): Exchange an authorization code for a short-lived token + IG user id
    upgrade_to_long_lived_token(): Exchange short-lived token for a ~60-day long-lived token
    get_instagram_profile(): Fetch Instagram Business profile data (username, name, followers, etc.)
    save_instagram_connection(): Upsert a user's Instagram connection record
    get_connection_by_user(): Fetch the active Instagram connection record for a user
    deactivate_connection(): Soft-delete (deactivate) an Instagram connection record
    parse_signed_request(): Decode and HMAC-verify a Meta signed_request payload
    deactivate_connections_by_ig_user(): Deactivate all connections for a deauthorizing IG user
    delete_user_data_by_ig_user(): Delete stored posts + connections for a data-deletion request
    connect_instagram_account(): Orchestrate the full OAuth callback flow end-to-end
    get_connection_for_publishing(): Fetch a user's active connection including the access token
    _normalize_image_for_instagram(): Letterbox an image onto Instagram's supported aspect ratio
    _create_media_container(): Create an Instagram media container (image/video/carousel item)
    _wait_for_container_ready(): Poll a video/carousel container until Instagram finishes processing
    _publish_container(): Publish a ready media container and return the published media id
    auto_publish_feed_to_instagram(): Background task publishing a feed post as photo, carousel or Reel
    _publish_as_photo(): Create, wait for, and publish a single-image container
    _publish_as_carousel(): Create per-image carousel items then publish the carousel container
    _publish_as_video(): Create, wait for, and publish a Reel/video container
    save_instagram_post(): Persist the feed_id -> instagram_media_id mapping after publish
    get_all_instagram_posts(): List a user's published Instagram posts from the database
    _fetch_latest_snapshot(): Read the stored insights snapshot row for a feed/window
    get_post_insights_from_db(): Return DB-cached insights for a post (no live Graph API call)
    get_post_insights(): Fetch live Instagram insights for a post via the Graph API
    track_caption_link_click(): Increment a post's caption-link click counter and return the redirect URL
    _format_post_time(): Format a datetime's time-of-day in IST as a 12-hour clock string
    get_best_time_to_post(): Rank a user's top-viewed posts to surface a best time to post

Dependencies:
    - httpx: Async HTTP client for Instagram OAuth and Graph API calls
    - PIL (Pillow): Aspect-ratio normalization for carousel/photo publishing
    - app.db.client: Async PostgreSQL database client
    - app.core.config: Application settings (app IDs, secrets, redirect URIs, campaign code)
    - app.core.logging: Centralized logging
    - app.utils.blob_storage: Public URL resolution and blob upload for normalized images
    - app.services.social_insights_collector (lazy import): seeds insight snapshot rows after publish

Updates:
    17-Jun-2026: Initial implementation with Facebook Login OAuth flow
    19-Jun-2026: Migrated to Instagram Login OAuth; the Page discovery step was removed.
    10-Jul-2026: Local _to_public_url replaced by the canonical to_public_blob_url.
    06-Aug-2026: Caption app links carry the campaign code through the openapp deep link.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import asyncio
import base64
import hashlib
import hmac
import io
import json
import math
import httpx
from urllib.parse import urlencode
from typing import Optional
from datetime import datetime, timezone, timedelta

from PIL import Image as PILImage

from app.db.client import Database
from app.core.config import settings
from app.core.logging import logger
from app.utils.blob_storage import (
    to_public_blob_url,
    upload_blob_bytes,
    current_timestamp_prefix,
    normalize_container_name,
)

INSTAGRAM_OAUTH_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
INSTAGRAM_OAUTH_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
INSTAGRAM_GRAPH_BASE = "https://graph.instagram.com"


def build_app_open_link(feed_id: Optional[str] = None) -> str:
    """Return the app-open deep link for Instagram captions, with optional feed_id and campaign code."""
    params = {}
    if feed_id:
        params["feed_id"] = feed_id
    if settings.APP_LINK_CAMPAIGN_CODE:
        params["code"] = settings.APP_LINK_CAMPAIGN_CODE
    if not params:
        return settings.APP_OPEN_URL
    return f"{settings.APP_OPEN_URL}?{urlencode(params)}"


REQUIRED_SCOPES = [
    "instagram_business_basic",
    "instagram_business_manage_messages",
    "instagram_business_manage_comments",
    "instagram_business_content_publish",
]

PROFILE_FIELDS = "id,username,name,account_type,biography,profile_picture_url,followers_count,follows_count,media_count,website"


def get_oauth_url(state: Optional[str] = None) -> str:
    """Return the Instagram OAuth authorization URL with the required scopes and optional state."""
    params = {
        "client_id": settings.INSTAGRAM_APP_ID,
        "redirect_uri": settings.INSTAGRAM_REDIRECT_URI,
        "scope": ",".join(REQUIRED_SCOPES),
        "response_type": "code",
    }
    if state:
        params["state"] = state

    url = f"{INSTAGRAM_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
    logger.info(f"INSTAGRAM_OAUTH | Generated OAuth URL for state={state}")
    return url


def get_social_oauth_url(platform: str, state: Optional[str] = None) -> str:
    """Return the OAuth authorization URL for 'instagram' or 'facebook'.

    Raises:
        ValueError: When the platform is not supported.
    """
    platform = platform.lower().strip()
    if platform == "instagram":
        return get_oauth_url(state=state)
    if platform == "facebook":
        from app.etown.facebook.facebook_utils import get_oauth_url as fb_get_oauth_url
        return fb_get_oauth_url(state=state)
    raise ValueError(f"Unsupported platform '{platform}'. Use 'instagram' or 'facebook'.")


async def exchange_code_for_token(code: str) -> dict:
    """Exchange an Instagram OAuth code for a short-lived token and return access_token and user_id.

    Raises:
        ValueError: When Instagram returns an error, non-JSON, or no token.
    """
    code = code.split("#")[0]

    data = {
        "client_id": settings.INSTAGRAM_APP_ID,
        "client_secret": settings.INSTAGRAM_APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": settings.INSTAGRAM_REDIRECT_URI,
        "code": code,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(INSTAGRAM_OAUTH_TOKEN_URL, data=data)

    try:
        payload = response.json()
    except Exception:
        logger.error(
            f"INSTAGRAM_TOKEN_EXCHANGE_ERROR | Non-JSON response (status={response.status_code}) | "
            f"body={response.text[:500]}"
        , exc_info=True)
        raise ValueError(
            f"Instagram token exchange returned a non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )

    if isinstance(payload, dict) and (payload.get("error_message") or payload.get("error")):
        error_msg = (
            payload.get("error_message")
            or (payload.get("error", {}) or {}).get("message")
            or "Unknown error from Instagram"
        )
        logger.error(f"INSTAGRAM_TOKEN_EXCHANGE_ERROR | {error_msg}")
        raise ValueError(f"Instagram token exchange failed: {error_msg}")

    if isinstance(payload, dict) and isinstance(payload.get("data"), list) and payload["data"]:
        token_data = payload["data"][0]
    else:
        token_data = payload

    if "access_token" not in token_data or "user_id" not in token_data:
        logger.error(f"INSTAGRAM_TOKEN_EXCHANGE_ERROR | Unexpected response: {payload}")
        raise ValueError("No access_token/user_id in Instagram token exchange response")

    token_data["user_id"] = str(token_data["user_id"])
    logger.info("INSTAGRAM_TOKEN_EXCHANGE | Short-lived token obtained successfully")
    return token_data


async def upgrade_to_long_lived_token(short_lived_token: str) -> dict:
    """Exchange a short-lived Instagram token for a long-lived one and return the token payload.

    Raises:
        ValueError: When the upgrade call fails or returns an error.
    """
    url = f"{INSTAGRAM_GRAPH_BASE}/access_token"
    params = {
        "grant_type": "ig_exchange_token",
        "client_secret": settings.INSTAGRAM_APP_SECRET,
        "access_token": short_lived_token,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params)

    try:
        data = response.json()
    except Exception:
        logger.error(
            f"INSTAGRAM_TOKEN_UPGRADE_ERROR | Non-JSON response (status={response.status_code}) | "
            f"body={response.text[:500]}"
        , exc_info=True)
        raise ValueError(
            f"Instagram token upgrade returned a non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error from Instagram")
        logger.error(f"INSTAGRAM_TOKEN_UPGRADE_ERROR | {error_msg}")
        raise ValueError(f"Long-lived token upgrade failed: {error_msg}")

    if "access_token" not in data:
        raise ValueError("No access_token in long-lived token upgrade response")

    expires_in = data.get("expires_in", 0)
    logger.info(f"INSTAGRAM_TOKEN_UPGRADE | Long-lived token obtained. Expires in {expires_in}s")
    return data


async def get_instagram_profile(ig_account_id: str, access_token: str) -> dict:
    """Fetch an Instagram Business Account's profile fields from the Graph API.

    Raises:
        ValueError: When the Graph API call fails.
    """
    url = f"{INSTAGRAM_GRAPH_BASE}/{ig_account_id}"
    params = {
        "fields": PROFILE_FIELDS,
        "access_token": access_token,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params)

    try:
        data = response.json()
    except Exception:
        logger.error(
            f"INSTAGRAM_PROFILE_FETCH_ERROR | Non-JSON response (status={response.status_code}) | "
            f"ig_id={ig_account_id} | body={response.text[:500]}"
        , exc_info=True)
        raise ValueError(
            f"Instagram profile fetch returned a non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error from Instagram")
        logger.error(f"INSTAGRAM_PROFILE_FETCH_ERROR | ig_id={ig_account_id} | {error_msg}")
        raise ValueError(f"Failed to fetch Instagram profile: {error_msg}")

    logger.info(f"INSTAGRAM_PROFILE_FETCH | ig_id={ig_account_id} username={data.get('username')}")
    return data


async def save_instagram_connection(
    contactid: str,
    userprofileid: Optional[str],
    fb_user_id: Optional[str],
    fb_page_id: Optional[str],
    instagram_business_id: str,
    instagram_username: Optional[str],
    instagram_name: Optional[str],
    access_token: str,
    token_expires_at: Optional[datetime],
) -> dict:
    """Upsert an active Instagram connection row for a user and return it without the token."""
    query = """
        INSERT INTO etown_instagramconnections (
            contactid,
            userprofileid,
            fb_user_id,
            fb_page_id,
            instagram_business_id,
            instagram_username,
            instagram_name,
            access_token,
            token_expires_at,
            is_active,
            connected_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, TRUE, NOW(), NOW())
        ON CONFLICT (contactid, userprofileid, instagram_business_id)
        DO UPDATE SET
            userprofileid        = EXCLUDED.userprofileid,
            fb_user_id           = EXCLUDED.fb_user_id,
            fb_page_id           = EXCLUDED.fb_page_id,
            instagram_username   = EXCLUDED.instagram_username,
            instagram_name       = EXCLUDED.instagram_name,
            access_token         = EXCLUDED.access_token,
            token_expires_at     = EXCLUDED.token_expires_at,
            is_active            = TRUE,
            updated_at           = NOW()
        RETURNING
            instagramconnectionsid::text,
            contactid,
            userprofileid,
            fb_user_id,
            fb_page_id,
            instagram_business_id,
            instagram_username,
            instagram_name,
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
        instagram_business_id,
        instagram_username,
        instagram_name,
        access_token,
        token_expires_at,
    )
    logger.info(
        f"INSTAGRAM_CONNECTION_SAVED | contactid={contactid} userprofileid={userprofileid} "
        f"ig_id={instagram_business_id} username={instagram_username}"
    )
    return dict(row)


async def get_connection_by_user(contactid: str, userprofileid: Optional[str] = None) -> Optional[dict]:
    """Return the newest active Instagram connection for a contact, optionally narrowed to one profile, or None."""
    if userprofileid:
        query = """
            SELECT
                instagramconnectionsid::text,
                contactid,
                userprofileid,
                fb_user_id,
                fb_page_id,
                instagram_business_id,
                instagram_username,
                instagram_name,
                token_expires_at,
                is_active,
                connected_at,
                updated_at
            FROM etown_instagramconnections
            WHERE contactid = $1 AND userprofileid = $2 AND is_active = TRUE
            ORDER BY connected_at DESC
            LIMIT 1
        """
        row = await Database.fetchrow(query, contactid, userprofileid)
    else:
        query = """
            SELECT
                instagramconnectionsid::text,
                contactid,
                userprofileid,
                fb_user_id,
                fb_page_id,
                instagram_business_id,
                instagram_username,
                instagram_name,
                token_expires_at,
                is_active,
                connected_at,
                updated_at
            FROM etown_instagramconnections
            WHERE contactid = $1 AND is_active = TRUE
            ORDER BY connected_at DESC
            LIMIT 1
        """
        row = await Database.fetchrow(query, contactid)
    if row:
        return dict(row)
    return None


async def deactivate_connection(contactid: str, instagram_business_id: str) -> bool:
    """Mark a contact's Instagram connection inactive and return True when a row changed."""
    query = """
        UPDATE etown_instagramconnections
        SET is_active = FALSE, updated_at = NOW()
        WHERE contactid = $1
          AND instagram_business_id = $2
          AND is_active = TRUE
        RETURNING instagramconnectionsid
    """
    row = await Database.fetchrow(query, contactid, instagram_business_id)
    success = row is not None
    logger.info(
        f"INSTAGRAM_DISCONNECT | contactid={contactid} "
        f"ig_id={instagram_business_id} success={success}"
    )
    return success


def parse_signed_request(signed_request: str) -> Optional[dict]:
    """Decode a Meta signed_request and return its payload, or None when malformed or the HMAC mismatches."""
    app_secret = settings.INSTAGRAM_APP_SECRET
    if not signed_request or "." not in signed_request:
        logger.warning("INSTAGRAM_SIGNED_REQUEST | Malformed signed_request")
        return None

    encoded_sig, payload = signed_request.split(".", 1)

    def _b64url_decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    try:
        sig = _b64url_decode(encoded_sig)
        data = json.loads(_b64url_decode(payload))
    except Exception as exc:
        logger.warning("INSTAGRAM_SIGNED_REQUEST | Decode error: %s", exc)
        return None

    if not app_secret:
        logger.warning("INSTAGRAM_SIGNED_REQUEST | INSTAGRAM_APP_SECRET unset — skipping signature verification")
        return data

    expected_sig = hmac.new(app_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        logger.warning("INSTAGRAM_SIGNED_REQUEST | Signature mismatch")
        return None

    return data


async def deactivate_connections_by_ig_user(instagram_business_id: str) -> int:
    """Deactivate every active connection for an Instagram user id and return how many changed."""
    query = """
        UPDATE etown_instagramconnections
        SET is_active = FALSE, updated_at = NOW()
        WHERE instagram_business_id = $1 AND is_active = TRUE
        RETURNING instagramconnectionsid
    """
    rows = await Database.fetch(query, instagram_business_id)
    count = len(rows)
    logger.info("INSTAGRAM_DEAUTHORIZE | ig_id=%s deactivated=%d", instagram_business_id, count)
    return count


async def delete_user_data_by_ig_user(instagram_business_id: str) -> int:
    """Delete an Instagram user's post mappings and connections and return the connection rows deleted."""
    await Database.execute(
        """
        DELETE FROM etown_instagram_posts
        WHERE instagram_business_id = $1
        """,
        instagram_business_id,
    )
    rows = await Database.fetch(
        """
        DELETE FROM etown_instagramconnections
        WHERE instagram_business_id = $1
        RETURNING instagramconnectionsid
        """,
        instagram_business_id,
    )
    count = len(rows)
    logger.info("INSTAGRAM_DATA_DELETION | ig_id=%s deleted_connections=%d", instagram_business_id, count)
    return count


async def connect_instagram_account(contactid: str, userprofileid: Optional[str], code: str) -> dict:
    """Exchange an OAuth code for a long-lived token, check the account type, and save the connection.

    Raises:
        ValueError: When an Instagram call fails or the account is not Business or Creator.
    """
    short_lived_data = await exchange_code_for_token(code)
    short_lived_token = short_lived_data["access_token"]
    instagram_business_id = short_lived_data["user_id"]

    long_lived_data = await upgrade_to_long_lived_token(short_lived_token)
    long_lived_token = long_lived_data["access_token"]
    expires_in_seconds = long_lived_data.get("expires_in", 0)

    token_expires_at: Optional[datetime] = None
    if expires_in_seconds:
        token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)

    profile = await get_instagram_profile(instagram_business_id, long_lived_token)

    account_type = (profile.get("account_type") or "").upper()
    if account_type not in ("BUSINESS", "CREATOR", "MEDIA_CREATOR"):
        raise ValueError(
            f"Instagram account type '{profile.get('account_type')}' does not support content publishing. "
            "Please switch your Instagram account to a Business or Creator account and try again."
        )

    connection = await save_instagram_connection(
        contactid=contactid,
        userprofileid=userprofileid,
        fb_user_id=None,
        fb_page_id=None,
        instagram_business_id=instagram_business_id,
        instagram_username=profile.get("username"),
        instagram_name=profile.get("name"),
        access_token=long_lived_token,
        token_expires_at=token_expires_at,
    )

    return {
        "connection": connection,
        "profile": {
            "instagram_business_id": instagram_business_id,
            "username": profile.get("username"),
            "name": profile.get("name"),
            "account_type": profile.get("account_type"),
            "biography": profile.get("biography"),
            "followers_count": profile.get("followers_count"),
            "media_count": profile.get("media_count"),
            "website": profile.get("website"),
            "profile_picture_url": profile.get("profile_picture_url"),
        },
    }


async def get_connection_for_publishing(contactid: str, userprofileid: str) -> Optional[dict]:
    """Return the newest active connection's business id, access token and expiry for a profile, or None."""
    query = """
        SELECT
            instagram_business_id,
            access_token,
            token_expires_at,
            userprofileid
        FROM etown_instagramconnections
        WHERE contactid = $1 AND userprofileid = $2 AND is_active = TRUE
        ORDER BY connected_at DESC
        LIMIT 1
    """
    row = await Database.fetchrow(query, contactid, userprofileid)
    if row:
        return dict(row)
    return None


INSTAGRAM_MIN_ASPECT_RATIO = 0.8
INSTAGRAM_MAX_ASPECT_RATIO = 1.91


async def _normalize_image_for_instagram(image_url: str, contact_id: str) -> str:
    """Return the image URL, or a white-padded re-upload when its aspect ratio is outside Instagram's range."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(image_url)
        response.raise_for_status()

        img = PILImage.open(io.BytesIO(response.content))
        width, height = img.size
        if width <= 0 or height <= 0:
            return image_url

        ratio = width / height
        if INSTAGRAM_MIN_ASPECT_RATIO <= ratio <= INSTAGRAM_MAX_ASPECT_RATIO:
            return image_url

        if ratio < INSTAGRAM_MIN_ASPECT_RATIO:
            new_width, new_height = math.ceil(height * INSTAGRAM_MIN_ASPECT_RATIO), height
        else:
            new_width, new_height = width, math.ceil(width / INSTAGRAM_MAX_ASPECT_RATIO)

        canvas = PILImage.new("RGB", (new_width, new_height), (255, 255, 255))
        canvas.paste(img.convert("RGB"), ((new_width - width) // 2, (new_height - height) // 2))

        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=90, optimize=True)

        container = normalize_container_name(settings.AZURE_BLOB_CONTAINER or "etowndev")
        blob_name = f"{contact_id}/{current_timestamp_prefix()}_IGaspect.jpg"
        blob_path = await upload_blob_bytes(container, blob_name, buf.getvalue(), "image/jpeg")
        public_url = to_public_blob_url(blob_path)
        logger.info(
            "INSTAGRAM_AUTO_PUBLISH | ASPECT_FIXED | contact_id=%s %dx%d (%.3f) → %dx%d | %s",
            contact_id, width, height, ratio, new_width, new_height, public_url,
        )
        return public_url or image_url
    except Exception as exc:
        logger.warning(
            "INSTAGRAM_AUTO_PUBLISH | ASPECT_FIX_FAILED | contact_id=%s url=%s | %s — using original",
            contact_id, image_url, exc,
        )
        return image_url


async def _create_media_container(
    ig_user_id: str,
    access_token: str,
    *,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
    caption: Optional[str] = None,
    is_carousel_item: bool = False,
    carousel_item_ids: Optional[list] = None,
) -> str:
    """Create an image, reel, carousel-item or carousel media container and return its id.

    Raises:
        ValueError: On a Graph API error or a missing id.
    """
    url = f"{INSTAGRAM_GRAPH_BASE}/{ig_user_id}/media"
    params: dict = {"access_token": access_token}

    if carousel_item_ids is not None:
        params["media_type"] = "CAROUSEL"
        params["children"] = ",".join(carousel_item_ids)
        if caption:
            params["caption"] = caption
    elif video_url:
        params["video_url"] = video_url
        params["media_type"] = "REELS"
        if caption:
            params["caption"] = caption
    else:
        params["image_url"] = image_url
        if is_carousel_item:
            params["is_carousel_item"] = "true"
        elif caption:
            params["caption"] = caption

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, params=params)

    data = response.json()
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error")
        raise ValueError(f"Instagram media container error: {error_msg}")

    container_id = data.get("id")
    if not container_id:
        raise ValueError(f"Instagram media container returned no id: {data}")
    return container_id


async def _wait_for_container_ready(
    container_id: str, access_token: str, *, max_polls: int = 10, poll_interval: float = 3.0
) -> None:
    """Poll a media container until its status_code is FINISHED.

    Raises:
        ValueError: If the container errors out or polls are exhausted.
    """
    url = f"{INSTAGRAM_GRAPH_BASE}/{container_id}"
    params = {"fields": "status_code,status", "access_token": access_token}

    for attempt in range(max_polls):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
        data = response.json()
        status_code = data.get("status_code", "")
        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            raise ValueError(f"Instagram video container processing failed: {data.get('status')}")
        logger.info(
            "INSTAGRAM_PUBLISH | container=%s status=%s attempt=%d/%d",
            container_id, status_code, attempt + 1, max_polls,
        )
        await asyncio.sleep(poll_interval)

    raise ValueError(f"Instagram video container {container_id} not ready after {max_polls} polls")


async def _publish_container(ig_user_id: str, container_id: str, access_token: str) -> str:
    """Publish a ready media container and return the published media id."""
    url = f"{INSTAGRAM_GRAPH_BASE}/{ig_user_id}/media_publish"
    params = {"creation_id": container_id, "access_token": access_token}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, params=params)

    data = response.json()
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error")
        raise ValueError(f"Instagram media_publish error: {error_msg}")

    media_id = data.get("id")
    if not media_id:
        raise ValueError(f"Instagram media_publish returned no id: {data}")
    return media_id


async def auto_publish_feed_to_instagram(
    contact_id: str,
    userprofile_id: str,
    feed_text: Optional[str],
    image_url: Optional[str],
    extra_image_urls: Optional[list],
    video_url: Optional[str],
    feed_id: Optional[str] = None,
) -> None:
    """Publish a feed post to the user's Instagram as a reel, carousel or photo, logging instead of raising."""
    try:
        connection = await get_connection_for_publishing(contact_id, userprofile_id)
    except Exception as exc:
        logger.error("INSTAGRAM_AUTO_PUBLISH | DB_ERROR | contact_id=%s | %s", contact_id, exc, exc_info=True)
        return

    if not connection:
        logger.info("INSTAGRAM_AUTO_PUBLISH | NO_CONNECTION | contact_id=%s — skipping", contact_id)
        return

    ig_user_id = connection["instagram_business_id"]
    access_token = connection["access_token"]
    raw_text = feed_text or ""
    try:
        delta = json.loads(raw_text)
        if isinstance(delta, list):
            raw_text = "".join(
                op["insert"] for op in delta
                if isinstance(op, dict) and isinstance(op.get("insert"), str)
            ).rstrip("\n")
    except (json.JSONDecodeError, TypeError):
        logger.debug("instagram: feed text is not a Quill delta; posting it verbatim")
    app_link = (
        f"{settings.API_BASE_URL}/api/instagram/track/{feed_id}"
        if feed_id
        else build_app_open_link()
    )
    app_suffix = f"\n{app_link}"
    max_text_len = 2200 - len(app_suffix)
    caption = raw_text[:max_text_len] + app_suffix

    token_expires_at = connection.get("token_expires_at")
    if token_expires_at:
        if datetime.now(timezone.utc) >= token_expires_at:
            logger.warning(
                "INSTAGRAM_AUTO_PUBLISH | TOKEN_EXPIRED | contact_id=%s ig_id=%s — attempting publish anyway",
                contact_id, ig_user_id,
            )

    try:
        media_id: Optional[str] = None
        media_type: str = "PHOTO"
        if video_url:
            media_id = await _publish_as_video(ig_user_id, access_token, to_public_blob_url(video_url), caption, contact_id)
            media_type = "VIDEO"
        else:
            all_images = [to_public_blob_url(u) for u in ([image_url] + (extra_image_urls or [])) if u]
            if not all_images:
                logger.info(
                    "INSTAGRAM_AUTO_PUBLISH | NO_MEDIA | contact_id=%s ig_id=%s — skipping (no publishable media)",
                    contact_id, ig_user_id,
                )
                return
            if len(all_images) == 1:
                media_id = await _publish_as_photo(ig_user_id, access_token, all_images[0], caption, contact_id)
                media_type = "PHOTO"
            else:
                carousel_images = await asyncio.gather(*[
                    _normalize_image_for_instagram(u, contact_id) for u in all_images[:10]
                ])
                media_id = await _publish_as_carousel(ig_user_id, access_token, list(carousel_images), caption, contact_id)
                media_type = "CAROUSEL_ALBUM"

        if feed_id and media_id:
            await save_instagram_post(
                feed_id=feed_id,
                contactid=contact_id,
                instagram_media_id=media_id,
                instagram_business_id=ig_user_id,
                media_type=media_type,
                userprofileid=connection.get("userprofileid"),
            )
    except Exception as exc:
        logger.error(
            "INSTAGRAM_AUTO_PUBLISH | FAILED | contact_id=%s ig_id=%s | %s",
            contact_id, ig_user_id, exc, exc_info=True,
        )


async def _publish_as_photo(
    ig_user_id: str, access_token: str, image_url: str, caption: str, contact_id: str
) -> str:
    """Publish a single-image post and return the Instagram media id."""
    container_id = await _create_media_container(
        ig_user_id, access_token, image_url=image_url, caption=caption
    )
    await _wait_for_container_ready(container_id, access_token)
    media_id = await _publish_container(ig_user_id, container_id, access_token)
    logger.info(
        "INSTAGRAM_AUTO_PUBLISH | PHOTO_SUCCESS | contact_id=%s ig_id=%s media_id=%s",
        contact_id, ig_user_id, media_id,
    )
    return media_id


async def _publish_as_carousel(
    ig_user_id: str, access_token: str, image_urls: list, caption: str, contact_id: str
) -> str:
    """Publish a carousel of images and return the Instagram media id."""
    item_ids = await asyncio.gather(*[
        _create_media_container(ig_user_id, access_token, image_url=url, is_carousel_item=True)
        for url in image_urls
    ])
    carousel_id = await _create_media_container(
        ig_user_id, access_token, caption=caption, carousel_item_ids=list(item_ids)
    )
    await _wait_for_container_ready(carousel_id, access_token)
    media_id = await _publish_container(ig_user_id, carousel_id, access_token)
    logger.info(
        "INSTAGRAM_AUTO_PUBLISH | CAROUSEL_SUCCESS | contact_id=%s ig_id=%s media_id=%s items=%d",
        contact_id, ig_user_id, media_id, len(image_urls),
    )
    return media_id


async def _publish_as_video(
    ig_user_id: str, access_token: str, video_url: str, caption: str, contact_id: str
) -> str:
    """Publish a video as a reel and return the Instagram media id."""
    container_id = await _create_media_container(
        ig_user_id, access_token, video_url=video_url, caption=caption
    )
    await _wait_for_container_ready(container_id, access_token)
    media_id = await _publish_container(ig_user_id, container_id, access_token)
    logger.info(
        "INSTAGRAM_AUTO_PUBLISH | VIDEO_SUCCESS | contact_id=%s ig_id=%s media_id=%s",
        contact_id, ig_user_id, media_id,
    )
    return media_id


async def save_instagram_post(
    feed_id: str,
    contactid: str,
    instagram_media_id: str,
    instagram_business_id: str,
    media_type: str,
    userprofileid: Optional[str] = None,
) -> None:
    """Upsert the feed-to-Instagram-media mapping and seed insight rows, logging any error."""
    query = """
        INSERT INTO etown_instagram_posts
            (feed_id, contactid, userprofileid, instagram_media_id, instagram_business_id, media_type)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (feed_id) DO UPDATE SET
            instagram_media_id   = EXCLUDED.instagram_media_id,
            instagram_business_id = EXCLUDED.instagram_business_id,
            media_type           = EXCLUDED.media_type,
            published_at         = NOW()
    """
    try:
        await Database.execute(query, feed_id, contactid, userprofileid, instagram_media_id, instagram_business_id, media_type)
        logger.info(
            "INSTAGRAM_POST_SAVED | feed_id=%s media_id=%s media_type=%s",
            feed_id, instagram_media_id, media_type,
        )
        from app.services.social_insights_collector import create_initial_insight_rows
        await create_initial_insight_rows(
            "instagram", feed_id, contactid, userprofileid, instagram_media_id, media_type,
        )
    except Exception as exc:
        logger.error("INSTAGRAM_POST_SAVE_ERROR | feed_id=%s | %s", feed_id, exc, exc_info=True)


async def get_all_instagram_posts(
    contactid: Optional[str] = None,
    userprofileid: Optional[str] = None,
) -> list:
    """Return a user's published Instagram posts by contactid (optionally profile-scoped) or userprofileid, newest first."""
    if not contactid and not userprofileid:
        return []

    if contactid:
        query = """
            SELECT
                p.instagrampostid::text,
                p.feed_id,
                p.contactid,
                p.instagram_media_id,
                p.instagram_business_id,
                p.media_type,
                p.published_at
            FROM etown_instagram_posts p
            WHERE p.contactid = $1
              AND ($2::text IS NULL OR p.userprofileid = $2)
            ORDER BY p.published_at DESC
        """
        rows = await Database.fetch(query, contactid, userprofileid)
    else:
        query = """
            SELECT
                p.instagrampostid::text,
                p.feed_id,
                p.contactid,
                p.instagram_media_id,
                p.instagram_business_id,
                p.media_type,
                p.published_at
            FROM etown_instagram_posts p
            JOIN etown_instagramconnections c
              ON c.instagram_business_id = p.instagram_business_id
             AND c.contactid = p.contactid
             AND c.is_active = TRUE
            WHERE c.userprofileid = $1
            ORDER BY p.published_at DESC
        """
        rows = await Database.fetch(query, userprofileid)

    return [dict(r) for r in rows]


async def _fetch_latest_snapshot(feed_id: str, period_days: Optional[int] = None) -> Optional[dict]:
    """Return a feed's Instagram insights snapshot for the given window, else the widest window, or None."""
    base = (
        "SELECT impressions, reach, likes, comments, shares, saved, "
        "total_interactions, views, profile_visits, follows, "
        "link_clicks, caption_link_clicks, captured_at "
        "FROM etown_social_insights_snapshots "
        "WHERE feed_id = $1 AND platform = 'instagram'"
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
    """Return a feed's stored Instagram post metrics with missing values as 0, or None when unpublished."""
    if contactid:
        row = await Database.fetchrow(
            """
            SELECT p.instagram_media_id, p.media_type, p.published_at, p.caption_link_clicks,
                   COALESCE(f.feedfilelocationurl, f.feedvideofilelocation, f.thumbnailfileurl) AS feed_url,
                   f.thumbnailfileurl AS thumbnail_url
            FROM etown_instagram_posts p
            LEFT JOIN etown_feed f ON f.feedid = p.feed_id
            WHERE p.feed_id = $1 AND p.contactid = $2
            LIMIT 1
            """,
            feed_id, contactid,
        )
    else:
        row = await Database.fetchrow(
            """
            SELECT p.instagram_media_id, p.media_type, p.published_at, p.caption_link_clicks,
                   COALESCE(f.feedfilelocationurl, f.feedvideofilelocation, f.thumbnailfileurl) AS feed_url,
                   f.thumbnailfileurl AS thumbnail_url
            FROM etown_instagram_posts p
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
        "instagram_media_id": row["instagram_media_id"],
        "media_type": row["media_type"],
        "published_at": published_at.isoformat() if published_at else None,
        "feed_url": row["feed_url"],
        "thumbnail_url": row["thumbnail_url"],
        "impressions": snapshot.get("impressions") or 0,
        "reach": snapshot.get("reach") or 0,
        "likes": snapshot.get("likes") or 0,
        "comments": snapshot.get("comments") or 0,
        "shares": snapshot.get("shares") or 0,
        "saved": snapshot.get("saved") or 0,
        "total_interactions": snapshot.get("total_interactions") or 0,
        "plays": snapshot.get("views") or 0,
        "caption_link_clicks": row["caption_link_clicks"] or 0,
    }


async def get_post_insights(feed_id: str, contactid: Optional[str] = None) -> dict:
    """Fetch live Graph API insights for the Instagram post linked to a feed, optionally checking ownership."""
    if contactid:
        post_query = """
            SELECT p.instagram_media_id, p.media_type, p.published_at,
                   p.contactid, p.userprofileid, p.caption_link_clicks,
                   c.access_token
            FROM etown_instagram_posts p
            JOIN etown_instagramconnections c
              ON c.instagram_business_id = p.instagram_business_id
             AND c.contactid = p.contactid
             AND c.is_active = TRUE
            WHERE p.feed_id = $1 AND p.contactid = $2
            ORDER BY c.connected_at DESC
            LIMIT 1
        """
        row = await Database.fetchrow(post_query, feed_id, contactid)
    else:
        post_query = """
            SELECT p.instagram_media_id, p.media_type, p.published_at,
                   p.contactid, p.userprofileid, p.caption_link_clicks,
                   c.access_token
            FROM etown_instagram_posts p
            JOIN etown_instagramconnections c
              ON c.instagram_business_id = p.instagram_business_id
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
            "message": "Post is still being published to Instagram. Please check again in a few seconds.",
        }

    media_id = row["instagram_media_id"]
    media_type = row["media_type"]
    access_token = row["access_token"]
    published_at = row["published_at"]
    contactid = contactid or row["contactid"]
    userprofileid = row["userprofileid"]
    caption_link_clicks: int = row["caption_link_clicks"] or 0

    if media_type in ("VIDEO", "REELS"):
        primary_metrics = (
            "reach,likes,comments,shares,saved,total_interactions,"
            "views,ig_reels_video_view_total_time,ig_reels_avg_watch_time,"
            "reels_skip_rate,follows,profile_visits,navigation,link_clicks"
        )
    else:
        primary_metrics = (
            "reach,likes,comments,shares,saved,total_interactions,"
            "views,follows,profile_visits,navigation"
        )

    insights_url = f"{INSTAGRAM_GRAPH_BASE}/{media_id}/insights"
    media_fields_url = f"{INSTAGRAM_GRAPH_BASE}/{media_id}"

    async def _fetch_insights(metrics: str) -> dict:
        params = {"metric": metrics, "access_token": access_token}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(insights_url, params=params)
            return response.json()
        except Exception as exc:
            logger.warning("instagram: insights request failed for media %s", media_id, exc_info=True)
            return {"error": {"message": str(exc)}}

    async def _fetch_media_fields() -> dict:
        """Real-time like_count and comments_count from the media object."""
        params = {"fields": "like_count,comments_count", "access_token": access_token}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(media_fields_url, params=params)
            result = response.json()
            if "error" in result:
                return {}
            return result
        except Exception as exc:
            logger.warning("instagram: media fields request failed for media %s", media_id, exc_info=True)
            return {}

    data, media_fields = await asyncio.gather(
        _fetch_insights(primary_metrics),
        _fetch_media_fields(),
    )

    if "error" in data:
        error_msg = data["error"].get("message", "Unknown error")
        error_code = data["error"].get("code")
        error_subcode = data["error"].get("error_subcode")

        if (
            any(p in error_msg.lower() for p in ("does not exist", "object with id"))
            or error_code == 803
            or (error_code == 100 and error_subcode == 33)
        ):
            return {
                "feed_id": feed_id,
                "instagram_media_id": media_id,
                "media_type": media_type,
                "published_at": published_at.isoformat() if published_at else None,
                "post_deleted": True,
                "error": "Post no longer exists on Instagram (may have been deleted).",
            }

        if "error" in data:
            error_msg = data["error"].get("message", "Unknown error")
            return {
                "feed_id": feed_id,
                "instagram_media_id": media_id,
                "media_type": media_type,
                "published_at": published_at.isoformat() if published_at else None,
                "error": error_msg,
            }

    insights: dict = {
        "feed_id": feed_id,
        "instagram_media_id": media_id,
        "media_type": media_type,
        "published_at": published_at.isoformat() if published_at else None,
        "caption_link_clicks": caption_link_clicks,
    }
    for item in data.get("data", []):
        name = item.get("name")
        values = item.get("values") or item.get("value")
        if isinstance(values, list) and values:
            insights[name] = values[0].get("value") or 0
        elif isinstance(values, (int, float)):
            insights[name] = values
        else:
            insights[name] = 0

    insights["impressions"] = insights.get("views") or 0

    if "like_count" in media_fields:
        insights["likes"] = media_fields["like_count"] or 0
    else:
        pass

    if "comments_count" in media_fields:
        insights["comments"] = media_fields["comments_count"] or 0
    else:
        pass

    app_link_clicks = insights.get("link_clicks", None)
    insights["app_link_clicks"] = app_link_clicks
    insights["app_link"] = build_app_open_link(feed_id) if feed_id else None

    insights_ready = True
    insights_note: Optional[str] = None
    if insights.get("impressions", 0) == 0 and published_at:
        pub_aware = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
        post_age_hours = (datetime.now(timezone.utc) - pub_aware).total_seconds() / 3600
        if post_age_hours < 2:
            insights_ready = False
            insights_note = (
                "Instagram is still processing analytics for this post. "
                "Impressions, reach, and engagement metrics are usually available within 1–2 hours."
            )
    insights["insights_ready"] = insights_ready
    if insights_note:
        insights["insights_note"] = insights_note

    return insights


async def track_caption_link_click(feed_id: str) -> str:
    """Increment a post's caption link click counter and return the app deep link to redirect to."""
    destination = build_app_open_link(feed_id)
    try:
        await Database.execute(
            """
            UPDATE etown_instagram_posts
            SET caption_link_clicks = caption_link_clicks + 1
            WHERE feed_id = $1
            """,
            feed_id,
        )
        logger.info("INSTAGRAM_CAPTION_CLICK | feed_id=%s", feed_id)
    except Exception as exc:
        logger.error("INSTAGRAM_CAPTION_CLICK_ERROR | feed_id=%s | %s", feed_id, exc, exc_info=True)
    return destination


_IST = timezone(timedelta(hours=5, minutes=30))


def _format_post_time(dt: Optional[datetime]) -> Optional[str]:
    """Return a datetime's IST time of day as a 12-hour string like "1 AM" or "1:30 PM", or None."""
    if dt is None:
        return None
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    local = aware.astimezone(_IST)
    suffix = "AM" if local.hour < 12 else "PM"
    hour12 = local.hour % 12 or 12
    if local.minute == 0:
        return f"{hour12} {suffix}"
    return f"{hour12}:{local.minute:02d} {suffix}"


async def get_best_time_to_post(
    contactid: Optional[str],
    userprofileid: Optional[str],
    limit: int = 2,
    period_days: Optional[int] = None,
) -> list:
    """Return the user's top-viewed Instagram and Facebook posts with publish times, highest views first."""
    if not contactid and not userprofileid:
        return []

    window_days = period_days if period_days is not None else settings.INSIGHTS_CANONICAL_WINDOW_DAYS
    query = """
        SELECT feed_id, platform, published_at, feed_url, views
        FROM (
            SELECT p.feed_id, 'instagram' AS platform, p.published_at,
                   COALESCE(f.feedfilelocationurl, f.feedvideofilelocation, f.thumbnailfileurl) AS feed_url,
                   COALESCE(s.views, 0) AS views
            FROM etown_instagram_posts p
            LEFT JOIN etown_social_insights_snapshots s
              ON s.feed_id = p.feed_id AND s.platform = 'instagram' AND s.period_days = $4
            LEFT JOIN etown_feed f ON f.feedid = p.feed_id
            WHERE ($1::text IS NULL OR p.contactid = $1)
              AND ($2::text IS NULL OR p.userprofileid = $2)
            UNION ALL
            SELECT p.feed_id, 'facebook' AS platform, p.published_at,
                   COALESCE(f.feedfilelocationurl, f.feedvideofilelocation, f.thumbnailfileurl) AS feed_url,
                   COALESCE(s.video_views, 0) AS views
            FROM etown_facebook_posts p
            LEFT JOIN etown_social_insights_snapshots s
              ON s.feed_id = p.feed_id AND s.platform = 'facebook' AND s.period_days = $4
            LEFT JOIN etown_feed f ON f.feedid = p.feed_id
            WHERE ($1::text IS NULL OR p.contactid = $1)
              AND ($2::text IS NULL OR p.userprofileid = $2)
        ) combined
        ORDER BY views DESC, published_at DESC
        LIMIT $3
    """
    rows = await Database.fetch(query, contactid, userprofileid, limit, window_days)

    result = []
    for idx, row in enumerate(rows, start=1):
        published_at = row["published_at"]
        result.append({
            "rank": idx,
            "feed_id": row["feed_id"],
            "platform": row["platform"],
            "published_at": published_at.isoformat() if published_at else None,
            "post_time": _format_post_time(published_at),
            "feed_url": row["feed_url"],
            "views": int(row["views"] or 0),
        })
    return result
