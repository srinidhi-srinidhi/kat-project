"""Offers Utilities - Postgres business logic for offer creation, circle distribution and tracking.

Domain layer behind /api/offers: creates offers with media upload, plan caps and Dataverse sync, fetches unexpired offers from a user's circle, records views, and fans new offers out as circle, group and boosted pushes and in-app messages.

Features:
    - Circle management: User relationship network mapping for offer delivery targeting
    - Offer fetching: Circle-based offer retrieval with expiry filtering and deduplication
    - Offer creation: create_offer_full() runs media upload, insert, Dataverse sync and all fan-outs
    - Offer tracking: View history recording and engagement analytics (view counts)
    - Social integration: Member and provider relationship handling for circle targeting
    - Media upload: Azure Blob Storage upload for offer images and videos
    - Push notifications: circle, group and boosted FCM pushes via dispatcher or governance send
    - In-app messages: Direct messages to circle members on new offer creation
    - Plan caps: active-offer and monthly offer limits enforced from the provider's plan
    - Rate limiting: 1 offer per 24h applies only when no plan resolves
    - Boosted broadcast: Broadcast offer notifications to all platform users for boosted providers
    - Group notifications: Targeted push to specific group member lists
    - Description Rendering: delta_to_plain_text() flattens a Quill-delta description to plain text
    - Parallel processing: Optimized concurrent database query execution with asyncio.gather

Classes:
    OfferRateLimitError(): Raised when a provider with no resolvable plan exceeds the 1-offer-per-24h limit.

Functions:
    delta_to_plain_text(): Flatten a Quill-delta JSON description into plain, user-facing text.
    get_circle_user_profile_ids(): Retrieve user's social circle profile IDs from both member and provider relations
    _compute_offer_expiry(): Return True if an offer has passed its scheduledendtime or 24h default expiry
    _update_expired_offers_in_db(): Batch-update offers to Expired in the DB (fire-and-forget background task)
    fetch_mycircle_offers(): Get offers from user's social network with expiry checks and pagination
    fetch_mycircle_offers_grouped(): Get offers grouped by provider for WhatsApp story-style display
    add_viewed_offer(): Record offer view interaction in etown_offersviewed table
    get_offer_viewcount(): Retrieve total view count for a specific offer
    upload_offer_media(): Upload base64-encoded offer image/video to Azure Blob Storage
    _resolve_offer_names(): Resolve the poster's contact/userprofile display names for a new offer
    _parse_datetime(): Parse an ISO-ish datetime string into a naive UTC datetime
    _generate_offer_unicid(): Generate a short "ETOF<n>" public offer identifier
    check_user_offer_rate_limit(): Check how many offers the user has posted today
    get_offer_rate_limit_info(): Return today's offer count plus the retry-after time once the limit is hit
    insert_offer_record(): Insert a new offer record with full metadata and media URLs
    format_offer_row(): Transform a raw DB offer row into the standard API response shape
    get_group_member_userprofile_ids(): Resolve group unique IDs CSV to member userprofile IDs
    send_offer_group_notifications(): Send offer push notification to a specific group's members
    get_provider_circle_members(): Resolve the posting profile's MyCircle (contactid + userprofileid) as {userprofileid, contactid} rows for per-user governance fan-out
    send_offer_circle_notifications(): Send offer push notification to provider's circle via dispatcher
    send_offer_circle_messages(): Send in-app message to each circle member on new offer
    is_provider_boosted(): Check if a provider has boosted status for broadcast eligibility
    get_all_fcm_device_tokens(): Retrieve all active FCM device tokens excluding the poster
    send_boosted_provider_broadcast(): Broadcast offer push to all platform users for boosted providers
    fetch_valid_and_pending_offers(): Retrieve active and pending offers for a provider
    fetch_offer_content_by_unicid(): Retrieve offer content details by offer unique ID
    get_provider_businessname(): Return a provider's business name for a userprofile_id
    _log_sms_result(): Log a warning/error from a fire-and-forget offer-SMS task
    _asyncio_schedule(): BackgroundTasks.add_task-compatible fire-and-forget scheduler for non-HTTP callers
    create_offer_full(): End-to-end offer creation — media upload, insert, Dataverse sync, and all notification fan-outs

Dependencies:
    - asyncio: Asynchronous parallel processing for offer and notification operations
    - datetime: Offer timing, expiration handling, and rate limiting windows
    - uuid: Unique ID generation for offer records
    - app.core.logging: Offer interaction logging
    - app.core.config: Application settings (Azure blob, FCM configuration, governance flag)
    - app.db.client: Database async queries for offers, circles, and device tokens
    - app.utils.blob_storage: Azure Blob upload helpers for offer media
    - app.etown.message.message_utils: MessageDatabase for in-app circle message sending
    - app.etown.notifications.core: Central notification dispatcher, models, and type registry
    - app.etown.notifications.service, ledger, fingerprint, triggers: governance send, dedupe, nearby trigger (lazy)
    - app.etown.plans.plans_utils: plan resolution, offer caps and badge helpers
    - app.etown.offers.offers_syncing: insert_offer_to_dataverse() (lazy import inside create_offer_full())

Updates:
    23-Feb-2026: Enhanced documentation with detailed feature descriptions
    26-May-2026: Fixed get_all_fcm_device_tokens and _mark_fcm_token_invalid.
    05-Jun-2026: Offer pushes go through the dispatcher; added fetch_mycircle_offers_grouped; docs refreshed.
    10-Jul-2026: The inline _is_stored_media_url helper replaced by the canonical one.
    18-Aug-2026: create_offer_full enforces the plan's active-offer and monthly offer caps.
    20-Aug-2026: Unique violation no longer refunds the claim; grouped offers carry Etown_providertype.
    24-Aug-2026: The 1-offer-per-24h cap applies only when no plan resolves.
    26-Aug-2026: Logged previously-silent exception handlers with tracebacks.
    02-Sep-2026: Circle-offer reads drop offers from disabled providers.

Notes:
    Combines member and provider relationships for comprehensive social circles.
    Uses parallel database queries for optimal performance.
"""

import asyncio
import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
import random
import uuid

from app.core.logging import logger
from app.core.config import settings
from app.db.client import Database, with_retry
from app.utils.http_client import get_http_client
from app.etown.message.message_utils import MessageDatabase
from app.etown.plans.plans_utils import (
    check_and_consume,
    check_current_usage,
    premium_display,
    release_consumed,
    resolve_plan,
    resolve_provider_badges,
)
from app.etown.notifications.core.models import NotificationRequest, Recipient, PushAudience
from app.etown.notifications.core.registry import NotificationType
from app.utils.blob_storage import (
    normalize_container_name as _normalize_container_name,
    to_bytes as _to_bytes,
    upload_blob_bytes as _upload_blob_bytes,
    current_timestamp_prefix as _current_timestamp_prefix,
    is_stored_media_url as _is_stored_media_url,
)


def delta_to_plain_text(text: Optional[str]) -> str:
    """Flatten a Quill-delta JSON string into its text, dropping embeds; return non-delta input unchanged."""
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.debug("offers: text is not a Quill delta; returning it unchanged")
        return text.strip()

    if isinstance(parsed, list):
        parts = []
        for op in parsed:
            if isinstance(op, dict):
                insert = op.get("insert")
                if isinstance(insert, str):
                    parts.append(insert)
        return "".join(parts).strip()

    return text.strip()


async def get_circle_user_profile_ids(current_user_profile_id: str) -> List[str]:
    """Return the userprofile ids in the current user's circle."""
    if not current_user_profile_id:
        return []
        
    member_query = """
    SELECT
        CASE
            WHEN from_userprofile = $1 THEN to_userprofile
            ELSE from_userprofile
        END as circle_upi
    FROM etown_memberrelation
    WHERE from_userprofile = $1 OR to_userprofile = $1;
    """
    
    provider_query = """
    SELECT
        CASE
            WHEN fromuserprofileid = $1 THEN touserprofileid
            ELSE fromuserprofileid
        END as circle_upi
    FROM etown_providerrelation
    WHERE fromuserprofileid = $1 OR touserprofileid = $1;
    """
    
    try:
        member_task = Database.fetch(member_query, current_user_profile_id)
        provider_task = Database.fetch(provider_query, current_user_profile_id)
        
        member_relations, provider_relations = await asyncio.gather(member_task, provider_task)
        
        all_circle_upis = set()
        for rel in member_relations:
            if rel.get('circle_upi'):
                all_circle_upis.add(rel['circle_upi'])
                
        for rel in provider_relations:
            if rel.get('circle_upi'):
                all_circle_upis.add(rel['circle_upi'])
                
        return list(all_circle_upis)
        
    except Exception as e:
        logger.error(f"Error fetching circle IDs for {current_user_profile_id}: {e}", exc_info=True)
        return []


def _compute_offer_expiry(offer_dict: Dict[str, Any]) -> bool:
    """Return True when scheduledendtime has passed, or with no end time, createdon is over 24 hours old."""
    now_utc = datetime.now(timezone.utc)

    def _to_utc(dt: Any) -> Optional[datetime]:
        if not isinstance(dt, datetime):
            return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    scheduledendtime = _to_utc(offer_dict.get("scheduledendtime"))
    createdon = _to_utc(offer_dict.get("createdon"))

    if scheduledendtime is not None:
        return scheduledendtime < now_utc
    return createdon is not None and createdon < now_utc - timedelta(hours=24)


async def _update_expired_offers_in_db(offer_ids: List[str]) -> None:
    """Mark the given offers Expired in one batch update."""
    try:
        await Database.execute(
            """
            UPDATE etown_offers
            SET validity = 'Expired', isactive = 'false', modifieddate = NOW()
            WHERE offersid = ANY($1::text[]) AND validity <> 'Expired'
            """,
            offer_ids,
        )
        logger.debug("Marked %d offer(s) as Expired in DB", len(offer_ids))
    except Exception as exc:
        logger.error("Failed to update expired offers in DB: %s", exc, exc_info=True)


async def fetch_mycircle_offers(
    contact_id: str,
    user_profile_id: str,
    limit: int,
    offset: int
) -> List[Dict[str, Any]]:
    """Return a page of circle offers with view count, watched status and expiry, scheduling expired-row updates."""
    try:
        circle_ids = await get_circle_user_profile_ids(user_profile_id)

        query = """
            SELECT
                o.*,
                p.statecode,
                p.city,
                COALESCE(ov.view_count, 0) AS view_count,
                CASE WHEN wv.userprofileid IS NOT NULL THEN true ELSE false END AS watched_status
            FROM etown_offers o
            LEFT JOIN etown_provider p ON o.userprofileid = p.userprofileid
            LEFT JOIN (
                SELECT offersid, COUNT(DISTINCT userprofileid) AS view_count
                FROM etown_offer_views
                GROUP BY offersid
            ) ov ON ov.offersid = o.offersid
            LEFT JOIN (
                SELECT offersid, userprofileid
                FROM etown_offer_views
                WHERE TRIM(LOWER(userprofileid)) = TRIM(LOWER($4))
            ) wv ON wv.offersid = o.offersid
            WHERE (
                o.userprofileid = ANY($1::text[])
                OR COALESCE(p.boosted, false) = true
            )

            AND NOT etown_is_deleted(o.userprofileid)
            AND NOT etown_is_deleted(o.contactid)
            AND p.disabled IS NOT TRUE
            AND NOT EXISTS (
                SELECT 1 FROM etown_provider ep
                WHERE ep.userprofileid = o.userprofileid
                AND TRIM(LOWER(ep.contactid)) = TRIM(LOWER($5))
            )
            ORDER BY o.createdon DESC
            LIMIT $2 OFFSET $3
"""
        
        raw_offers = await Database.fetch(query, circle_ids, limit, offset, user_profile_id, contact_id)
        
        processed_offers = []
        needs_db_expiry: List[str] = []

        for offer in raw_offers:
            offer_dict = dict(offer)
            
            view_count = offer_dict.pop("view_count", 0)
            watched_status = bool(offer_dict.pop("watched_status", False))

            is_expired = _compute_offer_expiry(offer_dict)

            if is_expired and offer_dict.get("validity") != "Expired":
                offer_id = offer_dict.get("offersid")
                if offer_id:
                    needs_db_expiry.append(offer_id)
            
            formatted_offer = {}
            
            for key, value in offer_dict.items():
                if key == "statecode":
                    formatted_offer["Etown_statecode"] = value
                elif key == "city":
                    formatted_offer["Etown_city"] = value
                else:
                    new_key = f"Etown_{key}"
                
                    if isinstance(value, datetime):
                        formatted_offer[new_key] = value.isoformat()
                    else:
                        formatted_offer[new_key] = value
            
            formatted_offer["Etown_viewcount"] = view_count
            formatted_offer["Etown_watchedstatus"] = watched_status
            formatted_offer["Etown_is_expired"] = is_expired

            if is_expired:
                formatted_offer["Etown_validity"] = "Expired"
            
            processed_offers.append(formatted_offer)

        if needs_db_expiry:
            asyncio.create_task(_update_expired_offers_in_db(needs_db_expiry))
            
        return processed_offers

    except Exception as e:
        logger.error(f"Error in fetch_mycircle_offers: {e}", exc_info=True)
        return []


async def fetch_mycircle_offers_grouped(
    contact_id: str,
    user_profile_id: str,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Return circle offers grouped by provider for story display, unviewed groups first.

    Args:
        contact_id: Requesting user's contact ID, used to exclude their own offers.
        user_profile_id: Requesting user's profile ID, for circle and watched status.
        limit: Maximum raw offers fetched before grouping.
        offset: Raw offers to skip for pagination.

    Returns:
        Provider groups with oldest-first Etown_offers and an Etown_has_unviewed flag.
    """
    try:
        circle_ids = await get_circle_user_profile_ids(user_profile_id)

        query = """
            SELECT
                o.*,
                p.statecode,
                p.city,
                p.providertype,
                COALESCE(ov.view_count, 0) AS view_count,
                ov.viewers,
                CASE WHEN wv.userprofileid IS NOT NULL THEN true ELSE false END AS watched_status
            FROM etown_offers o
            LEFT JOIN etown_provider p ON o.userprofileid = p.userprofileid
            LEFT JOIN (
                SELECT offersid,
                       COUNT(DISTINCT userprofileid) AS view_count,
                       COALESCE(
                           json_agg(
                               json_build_object(
                                   'name', name,
                                   'userprofileid', userprofileid,
                                   'created_on', created_on
                               ) ORDER BY created_on ASC
                           ) FILTER (WHERE userprofileid IS NOT NULL),
                           '[]'::json
                       ) AS viewers
                FROM etown_offer_views
                GROUP BY offersid
            ) ov ON ov.offersid = o.offersid
            LEFT JOIN (
                SELECT offersid, userprofileid
                FROM etown_offer_views
                WHERE TRIM(LOWER(userprofileid)) = TRIM(LOWER($2))
            ) wv ON wv.offersid = o.offersid
            WHERE (
                o.userprofileid = ANY($1::text[])
                OR COALESCE(p.boosted, false) = true
            )
            AND p.disabled IS NOT TRUE
            AND NOT EXISTS (
                SELECT 1 FROM etown_provider ep
                WHERE ep.userprofileid = o.userprofileid
                AND TRIM(LOWER(ep.contactid)) = TRIM(LOWER($3))
            )
            ORDER BY o.createdon ASC
            LIMIT $4 OFFSET $5
        """

        raw_offers = await Database.fetch(query, circle_ids, user_profile_id, contact_id, limit, offset)
        badge_map = await resolve_provider_badges(o["userprofileid"] for o in raw_offers)

        groups: Dict[str, List[Dict[str, Any]]] = {}
        groups_meta: Dict[str, Dict[str, Any]] = {}
        needs_db_expiry: List[str] = []

        for offer in raw_offers:
            offer_dict = dict(offer)
            view_count = offer_dict.pop("view_count", 0)
            watched_status = bool(offer_dict.pop("watched_status", False))
            viewers_raw = offer_dict.pop("viewers", None)
            try:
                if viewers_raw is None:
                    viewers_list = []
                elif isinstance(viewers_raw, list):
                    viewers_list = viewers_raw
                elif isinstance(viewers_raw, str):
                    viewers_list = json.loads(viewers_raw) if viewers_raw.strip() not in ("", "null") else []
                else:
                    viewers_list = json.loads(str(viewers_raw)) if viewers_raw else []
            except Exception:
                logger.warning("offers: viewers column is not valid JSON; treating it as empty", exc_info=True)
                viewers_list = []
            is_expired = _compute_offer_expiry(offer_dict)

            if is_expired:
                if offer_dict.get("validity") != "Expired":
                    if offer_id := offer_dict.get("offersid"):
                        needs_db_expiry.append(offer_id)
                continue

            formatted_offer: Dict[str, Any] = {}
            for key, value in offer_dict.items():
                if key == "statecode":
                    formatted_offer["Etown_statecode"] = value
                elif key == "city":
                    formatted_offer["Etown_city"] = value
                else:
                    new_key = f"Etown_{key}"
                    if isinstance(value, datetime):
                        formatted_offer[new_key] = value.isoformat()
                    else:
                        formatted_offer[new_key] = value

            formatted_offer["Etown_providertype"] = premium_display(
                offer_dict.get("providertype"),
                (badge_map.get(offer_dict.get("userprofileid")) or {}).get("premium_label"),
            )
            formatted_offer["Etown_viewcount"] = view_count
            formatted_offer["Etown_watchedstatus"] = watched_status
            formatted_offer["Etown_is_expired"] = is_expired
            if is_expired:
                formatted_offer["Etown_validity"] = "Expired"
            formatted_offer["Etown_views"] = [
                {
                    "Etown_name": v.get("name"),
                    "Etown_userprofileid": v.get("userprofileid"),
                    "Etown_watchedtime": v.get("created_on"),
                }
                for v in viewers_list
            ]

            provider_id = formatted_offer.get("Etown_userprofileid") or "unknown"
            if provider_id not in groups:
                groups[provider_id] = []
                groups_meta[provider_id] = {
                    "Etown_userprofileid": formatted_offer.get("Etown_userprofileid"),
                    "Etown_contactid": formatted_offer.get("Etown_contactid"),
                    "Etown_businessname": formatted_offer.get("Etown_businessname"),
                    "Etown_businessprofileimageurl": formatted_offer.get("Etown_businessprofileimageurl"),
                    "Etown_providertype": formatted_offer.get("Etown_providertype"),
                }
            groups[provider_id].append(formatted_offer)

        if needs_db_expiry:
            asyncio.create_task(_update_expired_offers_in_db(needs_db_expiry))

        result: List[Dict[str, Any]] = []
        for provider_id, offers in groups.items():
            has_unviewed = any(not o.get("Etown_watchedstatus", True) for o in offers)
            newest_createdon = offers[-1].get("Etown_createdon", "") if offers else ""
            result.append({
                **groups_meta[provider_id],
                "Etown_has_unviewed": has_unviewed,
                "Etown_offers": offers,
                "_newest_createdon": newest_createdon,
            })

        result.sort(key=lambda g: g["_newest_createdon"] or "", reverse=True)
        result.sort(key=lambda g: 0 if g["Etown_has_unviewed"] else 1)
        for g in result:
            g.pop("_newest_createdon", None)

        return result

    except Exception as e:
        logger.error(f"Error in fetch_mycircle_offers_grouped: {e}", exc_info=True)
        return []


async def add_viewed_offer(offers_id: str, user_profile_id: str) -> Dict[str, Any]:
    """Record an offer view with the viewer's profile name via add_offer_view, returning a status dict."""
    try:
        offer_exists = await Database.fetchval(
            "SELECT 1 FROM etown_offers WHERE offersid = $1 LIMIT 1",
            offers_id,
        )
        if not offer_exists:
            return {"status": "error", "message": "Offer not found or has been deleted", "isdeleted": True}

        viewer_name = await Database.fetchval(
            "SELECT COALESCE(name, contactidname) FROM etown_userprofile WHERE userprofileid = $1 LIMIT 1",
            user_profile_id,
        )

        await Database.fetchval(
            "SELECT add_offer_view($1, $2, $3)",
            offers_id,
            user_profile_id,
            viewer_name,
        )
        
        return {"status": "success", "message": "View status updated successfully", "isdeleted": False}
        
    except Exception as e:
        logger.error(f"Error in add_viewed_offer: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "isdeleted": False}


async def get_offer_viewcount(offers_id: str) -> Optional[int]:
    """Return the view count for an offer, or None when the offer does not exist."""
    try:
        offer_exists = await Database.fetchval(
            "SELECT 1 FROM etown_offers WHERE offersid = $1 LIMIT 1",
            offers_id,
        )
        if not offer_exists:
            return None

        count = await Database.fetchval(
            "SELECT get_offer_view_count($1)",
            offers_id,
        )
        return int(count) if count is not None else 0

    except Exception as e:
        logger.error(f"Error in get_offer_viewcount: {e}", exc_info=True)
        raise


async def upload_offer_media(
    contact_id: str,
    businessprofileimagefile: Optional[str] = None,
    thumbnail_file: Optional[str] = None,
    offerfile1: Optional[str] = None,
    offerfile2: Optional[str] = None,
    offerfile3: Optional[str] = None,
    offerfile4: Optional[str] = None,
    offerfile5: Optional[str] = None,
    offervideofile: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Upload the offer's base64 images and video to blob storage concurrently, passing stored paths through."""
    container_name = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
    if not container_name:
        raise RuntimeError("AZURE_BLOB_CONTAINER is not configured")

    timestamp_prefix = _current_timestamp_prefix()

    tasks: List[asyncio.Task] = []
    keys: List[str] = []
    passthrough: Dict[str, str] = {}

    def _schedule_upload(key: str, suffix: str, payload: Optional[str], content_type: str) -> None:
        if not payload:
            return
        if _is_stored_media_url(payload):
            passthrough[key] = payload.strip()
            return
        decoded = _to_bytes(payload)
        if not decoded:
            return
        blob_name = f"{contact_id}/{timestamp_prefix}_{suffix}"
        task = asyncio.create_task(
            _upload_blob_bytes(container_name, blob_name, decoded, content_type)
        )
        tasks.append(task)
        keys.append(key)

    _schedule_upload("businessprofileimageurl", "BusinessImg.png", businessprofileimagefile, "image/png")
    _schedule_upload("thumbnailfileurl", "OfferThumbnailImg.png", thumbnail_file, "image/png")
    _schedule_upload("offerfile1", "OfferImg1.png", offerfile1, "image/png")
    _schedule_upload("offerfile2", "OfferImg2.png", offerfile2, "image/png")
    _schedule_upload("offerfile3", "OfferImg3.png", offerfile3, "image/png")
    _schedule_upload("offerfile4", "OfferImg4.png", offerfile4, "image/png")
    _schedule_upload("offerfile5", "OfferImg5.png", offerfile5, "image/png")
    _schedule_upload("offervideofile", "OfferVideoFile.mp4", offervideofile, "video/mp4")

    results: Dict[str, Optional[str]] = {
        "businessprofileimageurl": None,
        "thumbnailfileurl": None,
        "offerfile1": None,
        "offerfile2": None,
        "offerfile3": None,
        "offerfile4": None,
        "offerfile5": None,
        "offervideofile": None,
    }

    if tasks:
        uploads = await asyncio.gather(*tasks)
        for key, upload_path in zip(keys, uploads):
            results[key] = upload_path

    results.update(passthrough)

    return results


async def _resolve_offer_names(contact_id: Optional[str], userprofile_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (contact_name, userprofile_name) from the userprofile and contact rows, falling back to firstname."""
    contact_name = None
    userprofile_name = None
    contact_lookup_id = contact_id

    try:
        if userprofile_id:
            userprofile_row = await Database.fetchrow(
                """
                SELECT name, contactidname, contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                """,
                userprofile_id,
            )
            if userprofile_row:
                userprofile_name = userprofile_row.get("name") or userprofile_row.get("contactidname")
                contact_name = userprofile_row.get("contactidname")
                if not contact_lookup_id:
                    contact_lookup_id = userprofile_row.get("contactid")

        if contact_lookup_id:
            contact_row = await Database.fetchrow(
                """
                SELECT firstname
                FROM etown_contact
                WHERE contactid = $1
                """,
                contact_lookup_id,
            )
            if contact_row and contact_row.get("firstname"):
                contact_name = contact_name or contact_row.get("firstname")
                userprofile_name = userprofile_name or contact_row.get("firstname")
    except Exception as exc:
        logger.warning("Failed to resolve contact/userprofile names: %s", exc)

    return contact_name, userprofile_name


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp into a naive UTC datetime, or None when blank or invalid."""
    if not value:
        return None
    sanitized = value.strip()
    if not sanitized:
        return None
    if sanitized.endswith("Z"):
        sanitized = sanitized.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(sanitized)
    except ValueError:
        logger.debug("offers: %r is not an ISO timestamp; returning null", value)
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _generate_offer_unicid() -> str:
    """Return a random ETOF-prefixed offer code."""
    return f"ETOF{random.randint(1, 999999)}"


async def check_user_offer_rate_limit(userprofile_id: str) -> int:
    """Return the number of offers created by the user in the last 24 hours."""
    cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None)
    result = await Database.fetchrow(
        "SELECT COUNT(*) AS offer_count FROM etown_offers "
        "WHERE userprofileid = $1 AND createdon >= $2",
        userprofile_id,
        cutoff_time,
    )
    return result["offer_count"] if result else 0


async def get_offer_rate_limit_info(userprofile_id: str) -> tuple[int, Optional[datetime]]:
    """Return the user's offer count in the last 24 hours and when the newest one ages out."""
    cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None)
    result = await Database.fetchrow(
        "SELECT COUNT(*) AS offer_count, MAX(createdon) AS last_createdon "
        "FROM etown_offers "
        "WHERE userprofileid = $1 AND createdon >= $2",
        userprofile_id,
        cutoff_time,
    )
    if not result or not result["offer_count"]:
        return 0, None
    count = result["offer_count"]
    last_createdon: Optional[datetime] = result["last_createdon"]
    if last_createdon is None:
        return count, None
    if last_createdon.tzinfo is None:
        last_createdon = last_createdon.replace(tzinfo=timezone.utc)
    retry_after_utc = last_createdon + timedelta(hours=24)
    return count, retry_after_utc


async def insert_offer_record(
    *,
    contact_id: str,
    userprofile_id: str,
    businessname: str,
    description: str,
    media_paths: Dict[str, Optional[str]],
    color_code: Optional[str] = None,
    validity: Optional[str] = None,
    offer_type: Optional[str] = None,
    createdtime: Optional[str] = None,
    scheduled_time: Optional[str] = None,
    latitude: Optional[str] = None,
    longitude: Optional[str] = None,
    fontfamily: Optional[str] = None,
    fontsize: Optional[str] = None,
    fontstyle: Optional[str] = None,
    language_code: Optional[str] = None,
    tags: Optional[str] = None,
    title: Optional[str] = None,
) -> Tuple[str, str]:
    """Insert an etown_offers row with defaults and a 24-hour end time, returning (offer_id, offer_unicid)."""
    offer_id = str(uuid.uuid4())
    offer_unicid = _generate_offer_unicid()

    created_time_value = _parse_datetime(createdtime) or datetime.now(timezone.utc).replace(tzinfo=None)
    scheduled_time_value = _parse_datetime(scheduled_time) or created_time_value
    scheduled_end_time = scheduled_time_value + timedelta(hours=24) if scheduled_time_value else None
    created_on_value = created_time_value

    contact_name, userprofile_name = await _resolve_offer_names(contact_id, userprofile_id)

    tags_value = tags.strip() if tags and tags.strip() else None
    if not tags_value and userprofile_id:
        tags_value = f"{userprofile_id}_my_circle"

    fontfamily_value = fontfamily.strip() if fontfamily and fontfamily.strip() else "Normal"
    validity_value = validity.strip() if validity and validity.strip() else "Valid"

    offertypename_value = None
    if offer_type is not None and str(offer_type) == "1":
        offertypename_value = "Normal"

    offerfile2_value = media_paths.get("offerfile2") or "null"
    offerfile3_value = media_paths.get("offerfile3") or "null"
    offerfile4_value = media_paths.get("offerfile4") or "null"
    offerfile5_value = media_paths.get("offerfile5") or "null"
    offervideofile_value = media_paths.get("offervideofile") or "null"

    query = """
        INSERT INTO etown_offers (
            offersid,
            businessname,
            businessprofileimageurl,
            colorcode,
            contactid,
            contactidname,
            userprofileid,
            userprofileidname,
            offerunicid,
            offertype,
            offertypename,
            fontfamily,
            description,
            thumbnailfileurl,
            offerfile1,
            offerfile2,
            offerfile3,
            offerfile4,
            offerfile5,
            offervideofile,
            validity,
            createdtime,
            scheduledtime,
            scheduledendtime,
            latitude,
            longitude,
            createdon,
            tags,
            vieweduserprofileid,
            isactive,
            languagecode,
            title,
            fontsize,
            fontstyle
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8,
            $9, $10, $11, $12, $13, $14, $15, $16,
            $17, $18, $19, $20, $21, $22, $23, $24,
            $25, $26, $27, $28, $29, $30, $31, $32,
            $33, $34
        )
    """

    await Database.execute(
        query,
        offer_id,
        businessname,
        media_paths.get("businessprofileimageurl"),
        color_code,
        contact_id,
        contact_name,
        userprofile_id,
        userprofile_name,
        offer_unicid,
        str(offer_type) if offer_type is not None else None,
        offertypename_value,
        fontfamily_value,
        description,
        media_paths.get("thumbnailfileurl"),
        media_paths.get("offerfile1"),
        offerfile2_value,
        offerfile3_value,
        offerfile4_value,
        offerfile5_value,
        offervideofile_value,
        validity_value,
        created_time_value,
        scheduled_time_value,
        scheduled_end_time,
        latitude,
        longitude,
        created_on_value,
        tags_value,
        None,
        "true",
        language_code,
        title,
        fontsize,
        fontstyle,
    )

    return offer_id, offer_unicid


def format_offer_row(
    offer_row: Dict[str, Any],
    viewcount: int = 0,
    watchedstatus: bool = False,
) -> Dict[str, Any]:
    """Return an offer row with Etown_ keys, view and watched fields, and expiry applied to validity."""
    formatted_offer: Dict[str, Any] = {}
    for key, value in offer_row.items():
        if key == "statecode":
            formatted_offer["Etown_statecode"] = value
            continue
        if key == "city":
            formatted_offer["Etown_city"] = value
            continue

        new_key = f"Etown_{key}"
        if isinstance(value, datetime):
            formatted_offer[new_key] = value.isoformat()
        elif key in ("latitude", "longitude"):
            if value is None or (isinstance(value, str) and value.strip().lower() in ("", "null")):
                formatted_offer[new_key] = None
            else:
                try:
                    formatted_offer[new_key] = float(value)
                except (TypeError, ValueError):
                    logger.debug("offers: %s %r is not a number; storing null", key, value)
                    formatted_offer[new_key] = None
        else:
            formatted_offer[new_key] = value

    formatted_offer["Etown_viewcount"] = viewcount
    formatted_offer["Etown_watchedstatus"] = watchedstatus

    is_expired = _compute_offer_expiry(offer_row)
    formatted_offer["Etown_is_expired"] = is_expired
    if is_expired:
        formatted_offer["Etown_validity"] = "Expired"

    return formatted_offer


async def get_group_member_userprofile_ids(group_unique_ids_csv: str) -> List[str]:
    """Resolve comma-separated group unique IDs to a deduplicated list of member userprofile IDs."""
    raw_ids = [uid.strip() for uid in group_unique_ids_csv.split(",") if uid.strip()]
    if not raw_ids:
        return []

    group_ids: List[str] = []
    for unique_id in raw_ids:
        row = await Database.fetchrow(
            "SELECT groupid FROM etown_groups WHERE groupunicid = $1",
            unique_id,
        )
        if row and row["groupid"]:
            group_ids.append(row["groupid"])

    if not group_ids:
        return []

    rows = await Database.fetch(
        "SELECT DISTINCT userprofile_id FROM etown_groupitems WHERE groupid = ANY($1::text[]) AND userprofile_id IS NOT NULL",
        group_ids,
    )
    return [row["userprofile_id"] for row in rows]


async def send_offer_group_notifications(
    *,
    oauth_token: Optional[str],
    userprofile_ids: List[str],
    title: str,
    body: str,
    image: Optional[str],
    offer_id: str,
) -> None:
    """Send offer push to group members — per-user through governance (caps/dedup apply)."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications import fingerprint

    sem = asyncio.Semaphore(20)

    async def _one(upid: str) -> None:
        async with sem:
            await gov_send(GovernanceRequest(
                userprofileid=upid,
                notification_type=NotificationType.OFFER,
                ctx={"title": title, "body": body, "image": image, "offer_id": offer_id},
                oauth_token=oauth_token,
                dedupe_key=fingerprint.once("offer-group", offer_id, upid),
                feature="offer",
            ))

    await asyncio.gather(*[_one(u) for u in (userprofile_ids or []) if u], return_exceptions=True)


_CIRCLE_MEMBERS_BY_PROFILE_QUERY = """
    WITH rel AS (

        SELECT pr.touserprofileid AS circle_userprofileid,
               pr.tocontactid     AS circle_contactid
        FROM public.etown_providerrelation pr
        JOIN public.etown_userprofile poster
          ON poster.userprofileid = $2
         AND poster.role = 1
        WHERE pr.fromuserprofileid = $2
          AND pr.fromcontactid = $1
        UNION

        SELECT mr.to_userprofile AS circle_userprofileid,
               mr.to_contactid   AS circle_contactid
        FROM public.etown_memberrelation mr
        JOIN public.etown_userprofile poster
          ON poster.userprofileid = $2
         AND poster.role = 2
        WHERE mr.from_userprofile = $2
          AND mr.from_contactid = $1
    )
    SELECT DISTINCT ON (up.contactid) up.userprofileid, up.contactid
    FROM rel
    JOIN LATERAL (
        SELECT up2.userprofileid, up2.contactid
        FROM public.etown_userprofile up2
        WHERE up2.userprofileid = rel.circle_userprofileid
           OR up2.contactid = rel.circle_contactid
        ORDER BY (up2.userprofileid = rel.circle_userprofileid) DESC NULLS LAST,
                 up2.createdon DESC NULLS LAST,
                 up2.userprofileid
        LIMIT 1
    ) up ON TRUE
    WHERE up.contactid IS NOT NULL
      AND up.contactid <> $1
      AND up.userprofileid <> $2
      AND NOT etown_is_deleted(up.contactid)
      AND NOT EXISTS (
          SELECT 1
          FROM public.etown_provider dp
          WHERE dp.disabled
            AND dp.userprofileid = up.userprofileid
      )
    ORDER BY up.contactid, up.userprofileid
"""


_CIRCLE_MEMBERS_BY_CONTACT_QUERY = """
    SELECT DISTINCT ON (up.contactid) up.userprofileid, up.contactid
    FROM (

        SELECT pr.tocontactid AS circle_contactid
        FROM public.etown_providerrelation pr
        WHERE pr.fromcontactid = $1
          AND pr.tocontactid IS NOT NULL
          AND pr.tocontactid <> $1
        UNION

        SELECT pr.fromcontactid AS circle_contactid
        FROM public.etown_providerrelation pr
        WHERE pr.tocontactid = $1
          AND pr.fromcontactid IS NOT NULL
          AND pr.fromcontactid <> $1
        UNION

        SELECT CASE
            WHEN mr.from_userprofile = up0.userprofileid THEN c_to.contactid
            ELSE c_from.contactid
        END AS circle_contactid
        FROM public.etown_memberrelation mr
        JOIN public.etown_userprofile up0 ON up0.contactid = $1
        JOIN public.etown_userprofile up_from ON up_from.userprofileid = mr.from_userprofile
        JOIN public.etown_contact c_from ON c_from.contactid = up_from.contactid
        JOIN public.etown_userprofile up_to ON up_to.userprofileid = mr.to_userprofile
        JOIN public.etown_contact c_to ON c_to.contactid = up_to.contactid
        WHERE mr.from_userprofile = up0.userprofileid
           OR mr.to_userprofile = up0.userprofileid
    ) circle
    JOIN public.etown_userprofile up ON up.contactid = circle.circle_contactid
    WHERE up.userprofileid IS NOT NULL
      AND circle.circle_contactid <> $1
    ORDER BY up.contactid, up.createdon
"""


async def get_provider_circle_members(
    poster_contact_id: str,
    poster_userprofile_id: Optional[str] = None,
) -> List[dict]:
    """Return the poster's circle as userprofileid/contactid rows deduped by contact, scoped to one profile when given.

    Args:
        poster_contact_id: Contact GUID of the poster.
        poster_userprofile_id: Profile the post came from; limits the audience to its MyCircle.

    Returns:
        One userprofileid/contactid dict per circle member.
    """
    if not poster_contact_id:
        return []
    try:
        if poster_userprofile_id:
            rows = await Database.fetch(
                _CIRCLE_MEMBERS_BY_PROFILE_QUERY,
                poster_contact_id,
                poster_userprofile_id,
            )
        else:
            rows = await Database.fetch(
                _CIRCLE_MEMBERS_BY_CONTACT_QUERY,
                poster_contact_id,
            )
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning(
            "get_provider_circle_members failed for contact=%s userprofile=%s: %s",
            poster_contact_id, poster_userprofile_id, exc,
        )
        return []


async def send_offer_circle_notifications(
    *,
    oauth_token: Optional[str],
    poster_contact_id: str,
    poster_userprofile_id: Optional[str] = None,
    title: str,
    body: str,
    image: Optional[str],
    offer_id: str,
) -> None:
    """Send offer push to the poster's circle — per-user through governance (caps/dedup apply)."""
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications import fingerprint

    members = await get_provider_circle_members(poster_contact_id, poster_userprofile_id)
    if not members:
        return
    sem = asyncio.Semaphore(20)

    async def _one(m: dict) -> None:
        async with sem:
            await gov_send(GovernanceRequest(
                userprofileid=m["userprofileid"],
                contactid=m.get("contactid"),
                notification_type=NotificationType.OFFER,
                ctx={"title": title, "body": body, "image": image, "offer_id": offer_id},
                oauth_token=oauth_token,
                dedupe_key=fingerprint.once("offer-circle", offer_id, m["contactid"]),
                feature="offer",
            ))

    await asyncio.gather(*[_one(m) for m in members], return_exceptions=True)


async def send_offer_circle_messages(
    *,
    provider_userprofile_id: str,
    businessname: str,
    title: Optional[str],
    description: str,
    image_url: Optional[str] = None,
) -> None:
    """Send a new-offer inbox message to each circle member, reusing any existing conversation."""
    circle_ids = await get_circle_user_profile_ids(provider_userprofile_id)
    if not circle_ids:
        logger.info(
            "send_offer_circle_messages: no circle members for provider %s, skipping",
            provider_userprofile_id,
        )
        return

    if title:
        message_text = f"\U0001f389 New offer from {businessname}: {title} \u2014 {description}"
    else:
        message_text = f"\U0001f389 New offer from {businessname}: {description}"

    logger.info(
        "send_offer_circle_messages: sending to %d circle member(s) for provider %s",
        len(circle_ids), provider_userprofile_id,
    )
    semaphore = asyncio.Semaphore(10)

    async def _send_one(member_id: str) -> None:
        async with semaphore:
            try:
                receiver_type = await MessageDatabase.get_user_participant_type(member_id)
                if receiver_type is None:
                    logger.debug(
                        "send_offer_circle_messages: could not resolve type for %s, skipping",
                        member_id,
                    )
                    return
                conversation, _, _ = await MessageDatabase.create_conversation_with_optional_message(
                    userprofile_id=provider_userprofile_id,
                    user_type="provider",
                    opponent_id=member_id,
                    opponent_type=receiver_type,
                    initial_message=None,
                    message_type="text",
                    file_url=None,
                )
                await MessageDatabase.insert_message(
                    conversation_id=str(conversation["id"]),
                    sender_id=provider_userprofile_id,
                    receiver_id=member_id,
                    sender_type="provider",
                    receiver_type=receiver_type,
                    message_text=message_text,
                    message_type="image" if image_url else "text",
                    file_url=image_url,
                )
            except Exception as exc:
                logger.warning(
                    "send_offer_circle_messages: failed for member %s: %s",
                    member_id, exc,
                )

    await asyncio.gather(*[_send_one(uid) for uid in circle_ids], return_exceptions=True)
    logger.info(
        "send_offer_circle_messages: completed for %d member(s) — provider %s",
        len(circle_ids), provider_userprofile_id,
    )


async def is_provider_boosted(userprofile_id: str) -> bool:
    """Return True if the given userprofile_id belongs to a boosted provider."""
    try:
        row = await Database.fetchrow(
            "SELECT boosted FROM etown_provider WHERE userprofileid = $1",
            userprofile_id,
        )
        return bool(row and row["boosted"])
    except Exception as exc:
        logger.warning("Failed to check boosted status for %s: %s", userprofile_id, exc)
        return False


async def get_all_fcm_device_tokens(exclude_contact_id: str) -> List[str]:
    """Return distinct FCM tokens from device_token and long deviceguid values on contact-linked devices, excluding one contact."""
    try:
        rows = await Database.fetch(
            """
            SELECT DISTINCT token FROM (
                SELECT device_token AS token FROM etown_contactdevices
                WHERE device_token IS NOT NULL AND device_token <> ''
                  AND COALESCE(device_token_type, 'fcm') = 'fcm'
                  AND contactid IS NOT NULL AND contactid != $1
                UNION
                SELECT deviceguid AS token FROM etown_contactdevices
                WHERE deviceguid IS NOT NULL AND LENGTH(deviceguid) >= 100
                  AND contactid IS NOT NULL AND contactid != $1
            ) t
            WHERE token IS NOT NULL
            """,
            exclude_contact_id,
        )
        return [row["token"] for row in rows]
    except Exception as exc:
        logger.warning("Failed to fetch all FCM device tokens: %s", exc)
        return []


async def send_boosted_provider_broadcast(
    *,
    oauth_token: Optional[str],
    title: str,
    body: str,
    image: Optional[str],
    offer_id: str,
    poster_contact_id: str,
) -> None:
    """Send an FCM offer notification to every user (boosted provider broadcast) via the dispatcher."""
    from app.etown.notifications.core.dispatcher import dispatch
    from app.etown.notifications import ledger
    if not await ledger.claim_once(f"broadcast-offer:{offer_id}", notification_type=NotificationType.OFFER):
        logger.info("send_boosted_provider_broadcast: already broadcast for offer_id=%s — skipping", offer_id)
        return
    await dispatch(NotificationRequest(
        notification_type=NotificationType.OFFER,
        recipient=Recipient(exclude_contact_id=poster_contact_id),
        context={"title": title, "body": body, "image": image, "offer_id": offer_id},
        push_audience=PushAudience.BROADCAST,
        oauth_token=oauth_token,
    ))


async def fetch_valid_and_pending_offers(userprofile_id: str) -> List[Dict[str, Any]]:
    """Return a profile's Valid and Pending offers as Etown_-prefixed summary dicts."""
    query = """
        SELECT
    businessname,
    offersid,
    description,
    createdtime,
    scheduledtime,
    scheduledendtime,
 
    CASE
        WHEN offertype = '1' THEN 'Normal'
        WHEN offertype = '2' THEN 'Scheduled'
        ELSE NULL
    END AS offertype,
 
    validity
 
FROM
    etown_offers
 
WHERE
    userprofileid = $1
 
ORDER BY
    createdtime DESC;
    """
    rows = await Database.fetch(query, userprofile_id)
    return [
        {
            "Etown_offersid": str(row["offersid"]) if row["offersid"] is not None else "null",
            "Etown_businessname": row["businessname"] if row["businessname"] else "null",
            "Etown_offerdescription": row["description"] if row["description"] else "null",
            "Etown_createdtime": str(row["createdtime"]) if row["createdtime"] is not None else "null",
            "Etown_scheduledtime": str(row["scheduledtime"]) if row["scheduledtime"] is not None else "null",
            "Etown_scheduleendtime": str(row["scheduledendtime"]) if row["scheduledendtime"] is not None else "null",
            "Etown_offertype": str(row["offertype"]) if row["offertype"] else "null",
            "Etown_validity": row["validity"] if row["validity"] else "null",
        }
        for row in rows
    ]


async def fetch_offer_content_by_unicid(offerunicid: str) -> Optional[Dict[str, Any]]:
    """Return an offer by offerunicid with its provider's city and statecode as Etown_ keys, or None."""
    query = """
        SELECT
            o.*,
            p.city AS prov_city,
            p.statecode AS prov_statecode
        FROM etown_offers o
        INNER JOIN etown_provider p
            ON p.userprofileid = o.userprofileid
        WHERE o.offerunicid = $1
        ORDER BY o.createdon DESC
    """
    row = await Database.fetchrow(query, offerunicid)
    if not row:
        return None

    row_dict = dict(row)
    result: Dict[str, Any] = {}
    for key, value in row_dict.items():
        new_key = f"Etown_{key}"
        if isinstance(value, datetime):
            result[new_key] = value.isoformat()
        elif key in ("latitude", "longitude"):
            if value is None or (isinstance(value, str) and value.strip().lower() in ("", "null")):
                result[new_key] = None
            else:
                try:
                    result[new_key] = float(value)
                except (TypeError, ValueError):
                    logger.debug("offers: %s %r is not a number; storing null", key, value)
                    result[new_key] = None
        elif value is None:
            result[new_key] = None
        else:
            result[new_key] = value
    return result

class OfferRateLimitError(Exception):
    """Raised when a provider with no resolvable plan exceeds the 1-offer-per-24h limit."""

async def get_provider_businessname(userprofile_id: str) -> Optional[str]:
    """Return the business name for a provider's userprofile_id, or None."""
    if not userprofile_id:
        return None
    try:
        row = await Database.fetchrow(
            "SELECT businessname FROM etown_provider WHERE userprofileid = $1",
            userprofile_id,
        )
        return row["businessname"] if row and row["businessname"] else None
    except Exception as exc:
        logger.warning("Failed to resolve businessname for %s: %s", userprofile_id, exc)
        return None

def _log_sms_result(task: "asyncio.Task") -> None:
    """Log a warning when the offer SMS task returned False, or an error when it raised."""
    try:
        result = task.result()
        if result is False:
            logger.warning("Offer SMS send returned false")
    except Exception as exc:
        logger.error("Offer SMS send failed: %s", exc, exc_info=True)


def _asyncio_schedule(func, *args, **kwargs) -> None:
    """Run an async function as a fire-and-forget task, mirroring BackgroundTasks.add_task."""
    task = asyncio.create_task(func(*args, **kwargs))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


async def create_offer_full(
    *,
    contact_id: str,
    userprofile_id: str,
    businessname: str,
    description: str,
    title: Optional[str] = None,
    businessprofileimagefile: Optional[str] = None,
    thumbnail_file: Optional[str] = None,
    offerfile1: Optional[str] = None,
    offerfile2: Optional[str] = None,
    offerfile3: Optional[str] = None,
    offerfile4: Optional[str] = None,
    offerfile5: Optional[str] = None,
    offervideofile: Optional[str] = None,
    color_code: Optional[str] = None,
    validity: Optional[str] = None,
    offer_type: Optional[str] = None,
    createdtime: Optional[str] = None,
    scheduled_time: Optional[str] = None,
    latitude: Optional[str] = None,
    longitude: Optional[str] = None,
    fontfamily: Optional[str] = None,
    fontsize: Optional[str] = None,
    fontstyle: Optional[str] = None,
    language_code: Optional[str] = None,
    tags: Optional[str] = None,
    oauth_token: Optional[str] = None,
    phonebook_contacts: Optional[List[Dict[str, Any]]] = None,
    groupuniqueid: Optional[str] = None,
    schedule=None,
) -> Dict[str, Any]:
    """Create an offer: check the rate limit, upload media, insert the row, schedule notifications, and return it formatted.

    Returns:
        The formatted offer dict with Etown_-prefixed keys.

    Raises:
        OfferRateLimitError: When a provider without a resolvable plan already posted within 24 hours.
        RuntimeError: When the inserted offer cannot be read back.
    """
    from app.etown.offers.offers_syncing import insert_offer_to_dataverse
    from app.etown.notifications.core.dispatcher import dispatch

    if schedule is None:
        schedule = _asyncio_schedule

    description_text = delta_to_plain_text(description)

    logger.info(
        "create_offer_full: contact_id=%s userprofile_id=%s offer_type=%s businessname=%s tags=%s",
        contact_id, userprofile_id, offer_type, businessname, tags,
    )

    if await resolve_plan(userprofile_id) is None and not await is_provider_boosted(userprofile_id):
        recent_offer_count = await check_user_offer_rate_limit(userprofile_id)
        if recent_offer_count >= 1:
            raise OfferRateLimitError("You can only post one offer every 24 hours.")
        logger.info("create_offer_full: unplanned provider passed the 24h cap, userprofile_id=%s", userprofile_id)

    await check_current_usage(userprofile_id, "max_active_offers")

    media_paths = await upload_offer_media(
        contact_id=contact_id,
        businessprofileimagefile=businessprofileimagefile,
        thumbnail_file=thumbnail_file,
        offerfile1=offerfile1,
        offerfile2=offerfile2,
        offerfile3=offerfile3,
        offerfile4=offerfile4,
        offerfile5=offerfile5,
        offervideofile=offervideofile,
    )
    uploaded_count = sum(1 for v in media_paths.values() if v)
    logger.info("create_offer_full: media upload complete — %d file(s) uploaded", uploaded_count)

    await check_and_consume(userprofile_id, "offers_created")
    try:
        offer_id, offer_unicid = await insert_offer_record(
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            businessname=businessname,
            description=description,
            media_paths=media_paths,
            color_code=color_code,
            validity=validity,
            offer_type=offer_type,
            createdtime=createdtime,
            scheduled_time=scheduled_time,
            latitude=latitude,
            longitude=longitude,
            fontfamily=fontfamily,
            fontsize=fontsize,
            fontstyle=fontstyle,
            language_code=language_code,
            tags=tags,
            title=title,
        )
    except Exception as exc:
        from asyncpg.exceptions import UniqueViolationError
        if not isinstance(exc, UniqueViolationError):
            await release_consumed(userprofile_id, "offers_created")
        raise
    logger.info("create_offer_full: offer inserted — offer_id=%s offer_unicid=%s", offer_id, offer_unicid)

    offer_row = await Database.fetchrow(
        "SELECT * FROM etown_offers WHERE offersid = $1",
        offer_id,
    )
    if not offer_row:
        raise RuntimeError("Offer created but could not be retrieved")

    formatted_offer = format_offer_row(dict(offer_row), viewcount=0, watchedstatus=False)

    schedule(insert_offer_to_dataverse, dict(offer_row))

    offer_image_url = formatted_offer.get("Etown_offerfile1")
    if media_paths.get("offervideofile"):
        offer_image_url = formatted_offer.get("Etown_thumbnailfileurl")
    if not offer_image_url or offer_image_url == "null":
        offer_image_url = None

    schedule(
        send_offer_circle_messages,
        provider_userprofile_id=userprofile_id,
        businessname=businessname,
        title=title,
        description=description_text,
        image_url=offer_image_url,
    )

    provider_boosted = await is_provider_boosted(userprofile_id)

    if provider_boosted:
        logger.info("create_offer_full: queuing boosted broadcast for offer_unicid=%s", offer_unicid)
        schedule(
            send_boosted_provider_broadcast,
            oauth_token=oauth_token,
            title=f"{businessname} shared a new offer",
            body=description_text,
            image=offer_image_url,
            offer_id=offer_unicid,
            poster_contact_id=contact_id,
        )

    offer_notification_title = f"{businessname} shared a new offer"
    image_url = formatted_offer.get("Etown_offerfile1")
    if media_paths.get("offervideofile"):
        image_url = formatted_offer.get("Etown_thumbnailfileurl")

    if not provider_boosted:
        logger.info("create_offer_full: queuing circle notification for offer_unicid=%s contact_id=%s", offer_unicid, contact_id)
        if settings.NOTIFICATIONS_GOVERNANCE_ENABLED:
            from app.etown.notifications.service import GovernanceRequest, send as gov_send
            members = await get_provider_circle_members(contact_id, userprofile_id)
            for m in members:
                asyncio.create_task(gov_send(GovernanceRequest(
                    userprofileid=m["userprofileid"],
                    contactid=m.get("contactid"),
                    notification_type=NotificationType.OFFER,
                    sender_businessid=userprofile_id,
                    oauth_token=oauth_token,
                    ctx={
                        "title": offer_notification_title,
                        "body": description_text,
                        "image": image_url,
                        "offer_id": offer_unicid,
                        "business": businessname,
                    },
                    dedupe_key=f"offer:{offer_unicid}:{m['contactid']}",
                )))
        else:
            schedule(
                send_offer_circle_notifications,
                oauth_token=oauth_token,
                poster_contact_id=contact_id,
                poster_userprofile_id=userprofile_id,
                title=offer_notification_title,
                body=description_text,
                image=image_url,
                offer_id=offer_unicid,
            )

    if phonebook_contacts:
        logger.info("create_offer_full: dispatching SMS notifications to %d contact(s)", len(phonebook_contacts))
        from app.etown.notifications import ledger

        async def _send_offer_sms(phone_number: str) -> None:
            if not await ledger.claim_once(
                f"offer-sms:{offer_unicid}:{phone_number}",
                notification_type=NotificationType.OFFER_SMS,
                channel="sms",
            ):
                return
            await dispatch(NotificationRequest(
                notification_type=NotificationType.OFFER_SMS,
                recipient=Recipient(mobile_number=phone_number),
                context={
                    "business": businessname,
                    "offer_title": title or "New Offer",
                    "language": language_code or "en",
                },
            ))

        for phone in phonebook_contacts:
            phone_number = phone.get("phone") if isinstance(phone, dict) else getattr(phone, "phone", None)
            if not phone_number:
                continue
            task = asyncio.create_task(_send_offer_sms(phone_number))
            task.add_done_callback(_log_sms_result)

    if groupuniqueid and not provider_boosted:
        group_member_ids = await get_group_member_userprofile_ids(groupuniqueid)
        if group_member_ids:
            circle_ids = set(await get_circle_user_profile_ids(userprofile_id))
            deduped_member_ids = [gid for gid in group_member_ids if gid not in circle_ids]
            if deduped_member_ids:
                logger.info("create_offer_full: queuing group notifications to %d member(s) for offer_unicid=%s", len(deduped_member_ids), offer_unicid)
                asyncio.create_task(
                    send_offer_group_notifications(
                        oauth_token=oauth_token,
                        userprofile_ids=deduped_member_ids,
                        title=offer_notification_title,
                        body=description_text,
                        image=image_url,
                        offer_id=offer_unicid,
                    )
                )

    if settings.NOTIFICATIONS_GOVERNANCE_ENABLED and not provider_boosted:
        from app.etown.notifications.triggers import notify_new_offer_nearby
        schedule(
            notify_new_offer_nearby,
            offer_unicid=offer_unicid,
            businessname=businessname,
            provider_upid=userprofile_id,
            townname='',
            contactid=contact_id,
        )

    logger.info("create_offer_full: completed successfully — offer_id=%s", offer_id)
    return formatted_offer
