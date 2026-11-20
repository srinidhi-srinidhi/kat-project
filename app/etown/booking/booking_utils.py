"""Booking Utilities - Postgres business logic for placing, updating and listing bookings.

Domain layer behind /api/book: inserts etown_allrequests and etown_requestservicelists rows, updates status and instructions, lists member and provider bookings, sends in-app, push, SMS and plan-gated WhatsApp alerts, and schedules the Dataverse mirror for each write.

Features:
    - Request Insertion: Create etown_allrequests rows for new service bookings
    - Booking Instructions: Persist the notes given while placing the booking onto the request row
    - Instruction Media Resolution: byte-sniffing picks the true MIME, else the declared one
    - Service List Creation: Insert etown_requestservicelists rows per booked service
    - In-App Notifications: Write notification rows via central insert_notification_row helper
    - FCM Push Notifications: Send booking push to provider via the central dispatcher
    - Additional-Info Push: Notify the assigned provider when a member adds booking instructions
    - SMS Notifications: Send booking confirmation SMS to requestor via central service
    - WhatsApp Notifications: Send booking alerts to provider (requested) and member (accepted/completed)
    - Plan-gated Customer WhatsApp: free-tier providers' customers get no WhatsApp alerts
    - Provider Badges: list responses carry plan-derived Etown_providertype badges
    - Relation Management: Check and create member/provider relations on first booking
    - Invitation Handling: Create invitation notifications when no relation exists
    - Dataverse Sync: Background sync of all inserted rows to Microsoft Dataverse
    - Booking Update: Update request status, provider details, and booking counters
    - Type Coercion: Safe conversion helpers for int and datetime payload values

Classes:
    BookingService: Static service class for add_booking and update_booking operations
    AllRequestsListService: Static service class for paginated member/provider booking list
    FrequentlyVisitedService: Static service class for the member's frequently-visited providers list

Functions:
    _declared_data_url_mime(): Read the MIME a "data:...;base64," prefix declares
    _sniff_document_mime(): Identify an OOXML document (docx/xlsx/pptx) from its leading bytes
    _resolve_instruction_media_mime(): Resolve the MIME to store an instruction upload under
    _instruction_media_extension(): Pick the file extension for a resolved MIME type
    _clean_document_name(): Reduce a client-supplied file name to a bare basename
    _data_url_filename(): Read the file name a "data:...;name=" prefix declares
    _instruction_document_name(): Name a document instruction for its push preview
    _truncate_preview(): Collapse whitespace and clip a preview line to its limit
    _instruction_push_preview(): Build the preview line a booking-instruction push shows
    _to_optional_int(): Safe int coercion for incoming payload values
    _to_optional_datetime(): Safe datetime coercion supporting multiple string formats
    _booking_whatsapp_destinations(): Every deduplicated number a booking alert should reach
    rand(): Random integer helper for notification/request ID generation
    _format_service_datetime(): Render a booking's requested date/time for a notification body
    _send_fcm_notification(): Resolve provider device and dispatch booking push
    _send_booking_sms_notification(): Resolve requestor mobile and dispatch booking SMS
    _booking_customer_whatsapp_allowed(): Whether the booking's provider plan allows customer WhatsApp
    _booking_whatsapp_enabled(): Check the WhatsApp kill switch and per-type notification config
    _send_booking_request_whatsapp_to_provider(): Resolve provider mobile and send new-booking WhatsApp
    _send_booking_accepted_whatsapp_to_member(): Resolve member mobile and send booking-accepted WhatsApp
    _send_booking_completed_whatsapp_to_member(): Resolve member mobile and send booking-completed WhatsApp
    _send_booking_additional_info_push(): Alert the other party on a booking that instructions were added
    _sync_allrequest_instructions_safe(): Background wrapper that mirrors instructions to Dataverse
    _sync_allrequest_servicetype_safe(): Background wrapper that mirrors servicetype to Dataverse

Dependencies:
    - app.utils.whatsapp: WhatsApp notification helpers for booking events
    - app.utils.blob_storage: Upload and MIME/name helpers for instruction media attachments
    - app.etown.devices.devices_utils: get_device_tokens_by_userprofile_id() for push gating
    - app.etown.plans.plans_utils: customer_whatsapp_allowed, premium_display, resolve_provider_badges
    - logging: Operation and error logging
    - random: Random ID generation for notification/request rows
    - datetime: Timestamp handling for booking records
    - fastapi: HTTPException for error propagation
    - app.db.client: Database operations
    - app.etown.booking.booking_syncing: Dataverse background sync helpers
    - app.etown.notifications.core: Central notification dispatcher, models, and types
    - app.core.config: Application settings (FCM endpoints, SMS config)

Updates:
    29-May-2026: Initial creation with add_booking and update_booking stubs
    05-Jun-2026: Centralised FCM and SMS dispatch through the notification dispatcher; refreshed docs.
    23-Jun-2026: Removed oauth_token gate from booking accept/complete push paths.
    24-Jun-2026: Added fire-and-forget WhatsApp booking notifications.
    03-Jul-2026: Added AllRequestsListService with get_all_requests_list for GET /book/request_list (PowerApps migration)
    13-Jul-2026: get_all_requests_list reports requestor role and opponent number; add_additional_info sends a provider push.
    14-Jul-2026: Added FrequentlyVisitedService.get_frequently_visited for GET /book/frequently_visited.
    20-Jul-2026: update_booking stamps bookingcancelledtime when the update cancels the booking.
    22-Jul-2026: The service-list INSERT carries optional per-line name, price and image overrides.
    28-Jul-2026: Booking push gates prefer a device row holding a usable token.
    06-Aug-2026: add_booking stores request_details; update_booking takes price and service_datetime.
    10-Aug-2026: Notifications carry tracking ID and requested time; added get_booking_communications().
    12-Aug-2026: resolve_booking_id_for_user scopes its no-reference fallback to requestoruserprofileid.
    18-Aug-2026: add_additional_info stores the sender's ids; plan-gated member booking WhatsApp.
    19-Aug-2026: Two-way additional-info push; video and document instructions; deleted parties excluded.
    20-Aug-2026: New additional-info push body; lists emit Etown_providertype and plan badge label.
    21-Aug-2026: New additional-info push title and image; stores document file names and video thumbnails.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    02-Sep-2026: BookingDetails carry Etown_disabled; new-request and cancelled pushes carry booking_state and booking_id.
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.core.config import settings
from app.db.client import Database, with_retry
from app.etown.plans.plans_utils import customer_whatsapp_allowed, premium_display, resolve_provider_badges
from app.etown.devices.devices_utils import get_device_tokens_by_userprofile_id
from app.utils.blob_storage import (
    current_timestamp_prefix,
    detect_mime_type,
    normalize_container_name,
    to_bytes,
    to_relative_blob_url,
    upload_blob_bytes,
)
from app.etown.booking.booking_syncing import (
    insert_invitation_to_dataverse,
    insert_memberrelation_to_dataverse,
    insert_notification_to_dataverse,
    insert_providerrelation_to_dataverse,
    insert_requestservicelist_to_dataverse,
    update_allrequest_instructions_to_dataverse,
    update_allrequest_servicetype_to_dataverse,
    update_allrequest_to_dataverse,
)
from app.etown.notifications.core.registry import NotificationType

logger = logging.getLogger(__name__)

INSTRUCTION_TYPES = ("text", "image", "video", "document")
INSTRUCTION_MEDIA_TYPES = ("image", "video", "document")

_INSTRUCTION_MEDIA_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/rtf": "rtf",
    "application/zip": "zip",
    "text/plain": "txt",
    "text/csv": "csv",
}
_INSTRUCTION_MEDIA_FALLBACK_EXTENSIONS = {"image": "jpg", "video": "mp4", "document": "bin"}

_OOXML_PART_MIMES = (
    (b"word/", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    (b"xl/", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    (b"ppt/", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
)


def _declared_data_url_mime(value: str) -> Optional[str]:
    """Return the MIME type declared by a data-url prefix, or None when there is none."""
    if not value.startswith("data:"):
        return None
    header = value[len("data:"):].split(",", 1)[0]
    mime = header.split(";", 1)[0].strip().lower()
    return mime if "/" in mime else None


def _sniff_document_mime(raw: bytes) -> Optional[str]:
    """Return the OOXML MIME for ZIP bytes by part-name marker, application/zip if none, or None for non-ZIPs."""
    if not raw.startswith(b"PK\x03\x04"):
        return None
    head = raw[:4096]
    for marker, mime in _OOXML_PART_MIMES:
        if marker in head:
            return mime
    return "application/zip"


def _resolve_instruction_media_mime(raw: bytes, declared_mime: Optional[str]) -> str:
    """Resolve an instruction upload's MIME from byte sniffing, then the OOXML peek, then the declared MIME."""
    sniffed = detect_mime_type(raw)
    if sniffed != "application/octet-stream":
        return sniffed

    ooxml = _sniff_document_mime(raw)
    if ooxml:
        return ooxml

    return declared_mime or "application/octet-stream"


def _instruction_media_extension(mime: str, media_type: str) -> str:
    """Return the file extension for a MIME type: mapped, short subtype, or the media type's default."""
    mapped = _INSTRUCTION_MEDIA_EXTENSIONS.get(mime)
    if mapped:
        return mapped

    subtype = mime.split("/")[-1] if "/" in mime else ""
    if subtype.isalnum() and len(subtype) <= 5:
        return subtype

    return _INSTRUCTION_MEDIA_FALLBACK_EXTENSIONS.get(media_type, "bin")


INSTRUCTION_PREVIEW_MAX_CHARS = 100

_INSTRUCTION_PREVIEW_LABELS = {"image": "📷 Photo", "video": "📹 Video"}


def _clean_document_name(value: Optional[str]) -> Optional[str]:
    """Strip quotes and path segments from a payload file name, returning the basename or None."""
    if value is None:
        return None
    name = str(value).strip().strip('"\'').replace("\\", "/").split("/")[-1].strip()
    return name or None


def _data_url_filename(value: str) -> Optional[str]:
    """Return the name or filename parameter declared in a data-url header, or None."""
    if not value.startswith("data:"):
        return None
    header = value[len("data:"):].split(",", 1)[0]
    for param in header.split(";")[1:]:
        key, _, raw_name = param.partition("=")
        if key.strip().lower() in ("name", "filename"):
            cleaned = _clean_document_name(raw_name)
            if cleaned:
                return cleaned
    return None


def _instruction_document_name(
    stored_instruction: Optional[str],
    filename: Optional[str] = None,
) -> str:
    """Return a document instruction's display name: the declared file name, else Document.<ext> from the blob."""
    cleaned = _clean_document_name(filename)
    if cleaned:
        return cleaned

    blob_name = str(stored_instruction or "").split("?", 1)[0].rstrip("/").split("/")[-1]
    ext = blob_name.rsplit(".", 1)[-1] if "." in blob_name else ""
    return f"Document.{ext}" if ext.isalnum() and 0 < len(ext) <= 5 else "Document"


def _truncate_preview(text: Any, limit: int = INSTRUCTION_PREVIEW_MAX_CHARS) -> str:
    """Collapse whitespace and clip a preview line, marking the cut with an ellipsis."""
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _instruction_push_preview(
    instruction_type: str,
    stored_instruction: Optional[str],
    filename: Optional[str] = None,
) -> str:
    """Build the clipped one-line push preview for an instruction: its text, a media label, or the document name.

    Args:
        instruction_type: One of INSTRUCTION_TYPES.
        stored_instruction: The persisted text, or the blob URL for media.
        filename: The document's file name, as read off the payload.
    """
    clean_type = str(instruction_type or "").strip().lower()

    if clean_type == "document":
        return _truncate_preview(
            f"📎 {_instruction_document_name(stored_instruction, filename)}"
        )

    label = _INSTRUCTION_PREVIEW_LABELS.get(clean_type)
    if label:
        return label

    return _truncate_preview(stored_instruction)


def _to_optional_int(value: Any) -> Optional[int]:
    """Convert a payload value to int where possible, returning None for blanks and unparseable values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            logger.debug("booking: %r is not an integer; storing null", value)
            return None
    return None


def _to_optional_datetime(value: Any) -> Optional[datetime]:
    """Parse a payload value as ISO or one of several fixed datetime formats, returning None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None

        iso_candidate = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(iso_candidate)
        except ValueError:
            logger.debug("booking: %r is not an ISO timestamp; trying the explicit formats", text)

        formats = [
            "%d-%m-%Y %I %p",
            "%d-%m-%Y %I:%M %p",
            "%d-%m-%Y",
            "%Y-%m-%d %I %p",
            "%Y-%m-%d %I:%M %p",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%Y %I %p",
            "%m/%d/%Y",
            "%d/%m/%Y %I:%M %p",
            "%d/%m/%Y %I %p",
            "%d/%m/%Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        logger.warning("booking: %r did not parse in any known datetime format; storing null", text)
    return None


INSERT_ALLREQUESTS_QUERY = """
INSERT INTO etown_allrequests (
    allrequestid,
    requestid,
    requestorname,
    requestorid,
    requestoruserprofileid,
    requestoruserprofileidname,
    requestoraddress,
    requestorlatitude,
    requestorlongitude,
    requestorrequeststatus,
    requeststatus,
    createdon,
    "isRating",
    quantity,
    requestorfromdate,
    requestortodate,
    requestorservicedatetime,
    otp,
    requestorcontactnumber,
    requestoridentification,
    requestorrequestfileurl,
    requestorrole,
    requestorrolename,
    servicetype,
    instructions
)
VALUES (
    gen_random_uuid()::text,
    $1,
    $2,
    $3,
    $4,
    (
        SELECT name
        FROM etown_userprofile
        WHERE userprofileid = $4
        LIMIT 1
    ),
    $5,
    $6,
    $7,
    $8,
    'requested',
    NOW(),
    'False',
    $9,
    $10,
    $11,
    $16,
    $17,
    $12,
    $13,
    $14,
    $15,
    CASE
        WHEN CAST($15 AS text) = '1' THEN 'Provider'
        WHEN CAST($15 AS text) = '2' THEN 'Member'
        ELSE NULL
    END,
    $18,
    $19::jsonb
)
RETURNING *;
"""


INSERT_REQUEST_SERVICE_LIST_QUERY = """
INSERT INTO etown_requestservicelists (
    requestservicelistsid,
    name,
    bookingid,
    serviceprovidercontactid,
    providerservices,
    serviceprovideruserprofileid,
    quantity,
    serviceproviderid,
    serviceprovideraddress,
    serviceproviderlatitude,
    serviceproviderlongitude,
    serviceimageurl,
    servicename,
    price,
    createdon
)
SELECT
    gen_random_uuid()::text,
    COALESCE($4::text, ps.services),
    $2,
    ps.contactid,
    ps.providerservicesid,
    ps.userprofileid,
    $3,
    ps.providerid,
    ps.address,
    ps.latitude,
    ps.longitude,
    COALESCE($5::text, ps.serviceimageurl),
    ps.businessname,
    COALESCE($6::text, ps.pricerange),
    NOW()
FROM etown_providerservices ps
WHERE ps.providerservicesid = $1
LIMIT 1
RETURNING *;
"""


FETCH_PROVIDER_FCM_DATA_QUERY = """
SELECT
    up.userprofileid,
    up.rolename
FROM public.etown_providerservices ps
JOIN public.etown_userprofile up
    ON up.userprofileid = ps.userprofileid
WHERE ps.providerservicesid = $1
LIMIT 1;
"""


FETCH_PROVIDER_BUSINESSNAME_QUERY = """
SELECT businessname
FROM etown_providerservices
WHERE providerservicesid = $1
LIMIT 1;
"""


def _booking_whatsapp_destinations(row: dict) -> list:
    """Return the primary mobile then a distinct alternate for a booking alert, or [] with no primary."""
    primary = (row.get("mobilephone") or "").strip()
    if not primary:
        return []
    alternate = (row.get("alternatemobile") or "").strip()
    if not alternate:
        return [primary]

    def _last_ten(number: str) -> str:
        return "".join(filter(str.isdigit, number))[-10:]

    return [primary] if _last_ten(alternate) == _last_ten(primary) else [primary, alternate]


FETCH_REQUESTOR_MOBILE_QUERY = """
SELECT mobilephone, alternatemobile
FROM etown_contact
WHERE contactid = $1
LIMIT 1;
"""


FETCH_PROVIDER_CONTACT_MOBILE_QUERY = """
SELECT ec.mobilephone, ec.alternatemobile
FROM etown_providerservices eps
JOIN etown_contact ec ON ec.contactid = eps.contactid
WHERE eps.providerservicesid = $1
LIMIT 1;
"""


FETCH_BOOKING_MEMBER_INFO_QUERY = """
SELECT requestorname, requestid
FROM etown_allrequests
WHERE allrequestid = $1
LIMIT 1;
"""


FETCH_BOOKING_PROVIDER_USERPROFILE_QUERY = """
SELECT rsl.serviceprovideruserprofileid
FROM etown_requestservicelists rsl
WHERE rsl.bookingid = $1
  AND rsl.serviceprovideruserprofileid IS NOT NULL
LIMIT 1;
"""


FETCH_BOOKING_ADDITIONAL_INFO_PUSH_QUERY = """
SELECT
    ar.requestorname,
    ar.requestoruserprofileid        AS member_userprofileid,
    member_up.rolename               AS member_rolename,
    member_up.contactidname          AS member_contactidname,
    rsl.serviceprovideruserprofileid AS provider_userprofileid,
    provider_up.rolename             AS provider_rolename,
    provider_up.name                 AS provider_name,
    ep.businessname                  AS provider_businessname,
    sender_up.role                   AS sender_role
FROM etown_allrequests ar
LEFT JOIN etown_requestservicelists rsl
    ON rsl.bookingid = ar.allrequestid
    AND rsl.serviceprovideruserprofileid IS NOT NULL
LEFT JOIN etown_userprofile provider_up
    ON LOWER(provider_up.userprofileid) = LOWER(rsl.serviceprovideruserprofileid)
LEFT JOIN etown_provider ep
    ON LOWER(ep.userprofileid) = LOWER(rsl.serviceprovideruserprofileid)
LEFT JOIN etown_userprofile member_up
    ON LOWER(member_up.userprofileid) = LOWER(ar.requestoruserprofileid)
LEFT JOIN etown_userprofile sender_up
    ON LOWER(sender_up.userprofileid) = LOWER($2)
WHERE ar.allrequestid = $1
LIMIT 1;
"""


FETCH_BOOKING_REQUEST_URL_DATA_QUERY = """
SELECT
    ar.requestorname,
    ar.requestid,
    ar.requestoruserprofileid,
    ps.userprofileid            AS provider_userprofileid,
    rsl.serviceimageurl,
    ep.creationdate             AS provider_createdon
FROM etown_allrequests ar
LEFT JOIN etown_requestservicelists rsl
    ON rsl.bookingid = ar.allrequestid
    AND rsl.providerservices = $2
LEFT JOIN etown_providerservices ps
    ON ps.providerservicesid = $2
LEFT JOIN etown_provider ep
    ON ep.userprofileid = ps.userprofileid
WHERE ar.allrequestid = $1
LIMIT 1;
"""


CHECK_RELATION_QUERY = """
WITH provider_service AS (
    SELECT
        contactid,
        userprofileid
    FROM etown_providerservices
    WHERE providerservicesid = $1
    LIMIT 1
),
member_relation_exists AS (
    SELECT COUNT(*) AS member_count
    FROM etown_memberrelation mr
    CROSS JOIN provider_service ps
    WHERE
    (
        mr.from_contactid = $2
        AND mr.from_userprofile = $3
        AND mr.to_contactid = ps.contactid
        AND mr.to_userprofile = ps.userprofileid
    )
    OR
    (
        mr.from_contactid = ps.contactid
        AND mr.from_userprofile = ps.userprofileid
        AND mr.to_contactid = $2
        AND mr.to_userprofile = $3
    )
),
provider_relation_exists AS (
    SELECT COUNT(*) AS provider_count
    FROM etown_providerrelation pr
    CROSS JOIN provider_service ps
    WHERE
    (
        pr.fromcontactid = $2
        AND pr.fromuserprofileid = $3
        AND pr.tocontactid = ps.contactid
        AND pr.touserprofileid = ps.userprofileid
    )
    OR
    (
        pr.fromcontactid = ps.contactid
        AND pr.fromuserprofileid = ps.userprofileid
        AND pr.tocontactid = $2
        AND pr.touserprofileid = $3
    )
)
SELECT
    member_count,
    provider_count,
    CASE
        WHEN member_count > 0 AND provider_count > 0 THEN true
        ELSE false
    END AS both_relations_exist
FROM member_relation_exists, provider_relation_exists;
"""


CHECK_INVITATION_NOTIFICATION_QUERY = """
SELECT EXISTS (
    SELECT 1
    FROM public.etown_notifications
    WHERE
        sendercontactid = $2
        AND senderuserprofileid = $3
        AND receivercontactid = (
            SELECT contactid
            FROM public.etown_providerservices
            WHERE providerservicesid = $1
            LIMIT 1
        )
        AND receiveruserprofileid = (
            SELECT userprofileid
            FROM public.etown_providerservices
            WHERE providerservicesid = $1
            LIMIT 1
        )
        AND notificationstatus IS NOT NULL
        AND notificationstatus <> 'Cancelled'
        AND notificationtype = 'Invitation'
) AS notification_exists;
"""


FETCH_REQUESTOR_ROLE_QUERY = """
SELECT role
FROM etown_userprofile
WHERE userprofileid = $1
LIMIT 1;
"""


INSERT_PROVIDER_RELATION_QUERY = """
INSERT INTO etown_providerrelation (
    providerrelationid,
    fromproviderid,
    fromprovideridname,
    relationshiptype,
    relationshiptypename,
    fromcontactid,
    fromcontactidname,
    fromuserprofileid,
    fromuserprofileidname,
    tocontactid,
    tocontactidname,
    touserprofileid,
    touserprofileidname,
    invite_relationshiptype,
    invite_relationshiptypename,
    createdon
)
SELECT
    gen_random_uuid()::text,
    ep.providerid,
    ep.businessname,
    2,
    'Member',
    eup.contactid,
    eup.contactidname,
    eup.userprofileid,
    eup.name,
    eps.contactid,
    eps.contactidname,
    eps.userprofileid,
    eps.userprofileidname,
    '2',
    'Member',
    NOW()
FROM etown_userprofile eup
LEFT JOIN etown_provider ep
    ON ep.contactid = eup.contactid
    AND ep.userprofileid = eup.userprofileid
LEFT JOIN etown_providerservices eps
    ON eps.providerservicesid = $2
WHERE eup.userprofileid = $1
  AND NOT EXISTS (
      SELECT 1 FROM etown_providerrelation
      WHERE fromuserprofileid = eup.userprofileid AND touserprofileid = eps.userprofileid
  )
RETURNING *;
"""


INSERT_MEMBER_RELATION_QUERY = """
INSERT INTO etown_memberrelation (
    memberrelationid,
    from_memberid,
    from_memberidname,
    relationshiptype,
    relationshiptypename,
    invite_relationshiptype,
    invite_relationshiptypename,
    from_contactid,
    from_firstname,
    from_userprofile,
    to_contactid,
    to_firstname,
    to_userprofile,
    createdon
)
SELECT
    gen_random_uuid()::text,
    em.memberid,
    em.name,
    1,
    'Provider',
    '1',
    'Provider',
    eup.contactid,
    eup.contactidname,
    eup.userprofileid,
    eps.contactid,
    eps.contactidname,
    eps.userprofileid,
    NOW()
FROM etown_userprofile eup
LEFT JOIN etown_member em
    ON em.contactid = eup.contactid
LEFT JOIN etown_providerservices eps
    ON eps.providerservicesid = $2
WHERE eup.userprofileid = $1
  AND NOT EXISTS (
      SELECT 1 FROM etown_memberrelation
      WHERE from_userprofile = eup.userprofileid AND to_userprofile = eps.userprofileid
  )
RETURNING *;
"""


INSERT_CIRCLE_NOTIFICATION_QUERY = """
INSERT INTO etown_notifications (
    notificationsid,
    sendercontactid,
    sendercontactidname,
    senderuserprofileid,
    senderuserprofileidname,
    receivercontactid,
    receivercontactidname,
    receiveruserprofileid,
    receiveruserprofileidname,
    role,
    rolename,
    notificationcontent,
    notificationstatus,
    notificationtype,
    createdon
)
SELECT
    gen_random_uuid()::text,
    eps.contactid,
    eps.contactidname,
    eps.userprofileid,
    eps.userprofileidname,
    $2,
    ec.firstname,
    $3,
    eup.name,
    1,
    'Provider',
    CONCAT(ep.businessname, ' has been added in your My Circle as Provider.'),
    'Accepted',
    'Booking',
    CURRENT_DATE
FROM etown_providerservices eps
LEFT JOIN etown_provider ep
    ON ep.userprofileid = eps.userprofileid
LEFT JOIN etown_contact ec
    ON ec.contactid = $2
LEFT JOIN etown_userprofile eup
    ON eup.userprofileid = $3
WHERE eps.providerservicesid = $1
RETURNING *;
"""


INSERT_INVITATION_QUERY = """
INSERT INTO etown_invitation (
    invitationid,
    invitationtype,
    invitercontact,
    invitercontactname,
    inviteruserprofile,
    inviteruserprofilename,
    inviteuserprofile,
    inviteuserprofilename,
    invite_role,
    invite_rolename,
    new_inviterole,
    new_inviterolename,
    invitationcode,
    createdon
)
SELECT
    gen_random_uuid()::text,
    'Booking',
    $1,
    ec.firstname,
    $2,
    eup.name,
    eps.userprofileid,
    eps.userprofileidname,
    eup.role,
    CASE
        WHEN eup.role = 1 THEN 'Provider'
        WHEN eup.role = 2 THEN 'Member'
        ELSE NULL
    END,
    '1',
    'Provider',
    'Accepted',
    NOW()
FROM etown_userprofile eup
LEFT JOIN etown_contact ec
    ON ec.contactid = $1
LEFT JOIN etown_providerservices eps
    ON eps.providerservicesid = $3
WHERE eup.userprofileid = $2
RETURNING *;
"""


def rand(min_value: int = 1, max_value: int = 99999) -> int:
    """Return a random integer between min_value and max_value (inclusive)."""
    return random.randint(min_value, max_value)


def _format_service_datetime(value: Any) -> str:
    """Format a booking datetime or ISO string for a notification body, returning "" when unparseable."""
    if not value:
        return ""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (ValueError, TypeError):
            logger.debug("booking: %r is not a parseable datetime; formatting it as empty", value)
            return ""
    if parsed.hour == 0 and parsed.minute == 0:
        return parsed.strftime("%d %b %Y")
    return parsed.strftime("%d %b %Y, %I:%M %p").replace(" 0", " ")


async def _send_fcm_notification(
    service_id: str,
    notification_content: str,
    oauth_token: Optional[str],
    dedupe_key: Optional[str] = None,
    booking_id: str = "",
    booking_state: str = "",
) -> None:
    """Send the booking-request push to the service's provider through the governance layer when they have a device."""
    rows = await Database.fetch(FETCH_PROVIDER_FCM_DATA_QUERY, service_id)
    if not rows:
        logger.warning("FCM send skipped — provider not found | service_id=%s", service_id)
        return

    row = dict(rows[0])
    userprofile_id = row.get("userprofileid")
    role = row.get("rolename")

    if not await get_device_tokens_by_userprofile_id(userprofile_id or ""):
        logger.warning("FCM send skipped — provider has no device | service_id=%s", service_id)
        return

    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    await gov_send(GovernanceRequest(
        userprofileid=userprofile_id or "",
        notification_type=NotificationType.BOOKING_REQUEST,
        ctx={
            "body": notification_content or "",
            "userprofile_id": userprofile_id or "",
            "role": role or "",
            "booking_state": str(booking_state or ""),
            "booking_id": str(booking_id or ""),
        },
        oauth_token=oauth_token,
        feature="booking",
        dedupe_key=dedupe_key,
    ))


async def _send_booking_sms_notification(
    requestor_id: Any,
    provider_businessname: str,
    language_code: Optional[str],
    dedupe_key: Optional[str] = None,
    booking_ref: str = "",
    service_datetime: str = "",
) -> None:
    """Send the booking confirmation SMS to the requestor's mobile through the governance layer."""
    if not requestor_id:
        return

    if not settings.BOOK_SMS_API_URL:
        logger.warning("Booking SMS skipped - BOOK_SMS_API_URL is not configured")
        return

    rows = await Database.fetch(FETCH_REQUESTOR_MOBILE_QUERY, requestor_id)
    if not rows:
        logger.warning("Booking SMS skipped - requestor mobile not found | requestor_id=%s", requestor_id)
        return

    mobile_number = dict(rows[0]).get("mobilephone")
    if not mobile_number:
        logger.warning("Booking SMS skipped - empty requestor mobile | requestor_id=%s", requestor_id)
        return

    lang = (language_code or "en")
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    await gov_send(GovernanceRequest(
        userprofileid=str(requestor_id),
        contactid=str(requestor_id),
        mobile_number=mobile_number,
        notification_type=NotificationType.BOOKING_SMS,
        ctx={
            "business": provider_businessname,
            "language": lang,
            "booking_ref": booking_ref,
            "service_datetime": service_datetime,
        },
        feature="booking",
        dedupe_key=dedupe_key,
    ))


async def _booking_customer_whatsapp_allowed(request_row_id: str) -> bool:
    """Return whether the booking's provider plan allows customer WhatsApp, allowing when the provider is unknown."""
    provider_upid = await Database.fetchval(
        FETCH_BOOKING_PROVIDER_USERPROFILE_QUERY, request_row_id
    )
    if not provider_upid:
        logger.warning(
            "Booking customer WhatsApp: no provider userprofileid | request_row_id=%s — allowing",
            request_row_id,
        )
        return True

    if await customer_whatsapp_allowed(provider_upid):
        return True

    logger.info(
        "Booking customer WhatsApp suppressed - provider's plan excludes it | "
        "request_row_id=%s | provider_userprofileid=%s",
        request_row_id, provider_upid,
    )
    return False


async def _booking_whatsapp_enabled(notification_type: str) -> bool:
    """Return True only when WhatsApp is enabled and the type has an enabled notification config row."""
    if not settings.WHATSAPP_ENABLED:
        return False
    from app.etown.notifications.core import config as ncfg
    if await ncfg.db_get(notification_type) is None:
        logger.info(
            "Booking WhatsApp suppressed by notification config | type=%s", notification_type
        )
        return False
    return True


async def _send_booking_request_whatsapp_to_provider(
    service_id: str,
    booking_id: str,
    provider_name: str,
    service_name: str,
    language_code: Optional[str] = None,
) -> None:
    """Send a gated new-booking WhatsApp with a deep link to each of the provider's mobile numbers."""
    from urllib.parse import urlencode
    from app.utils.whatsapp import send_booking_request_whatsapp_notification

    if not await _booking_whatsapp_enabled(NotificationType.BOOKING_REQUEST_WHATSAPP):
        return

    mobile_rows = await Database.fetch(FETCH_PROVIDER_CONTACT_MOBILE_QUERY, service_id)
    if not mobile_rows:
        logger.warning(
            "Booking request WhatsApp skipped - provider mobile not found | service_id=%s", service_id
        )
        return
    provider_numbers = _booking_whatsapp_destinations(dict(mobile_rows[0]))
    if not provider_numbers:
        logger.warning(
            "Booking request WhatsApp skipped - empty provider mobile | service_id=%s", service_id
        )
        return

    url_rows = await Database.fetch(FETCH_BOOKING_REQUEST_URL_DATA_QUERY, booking_id, service_id)
    member_name = ""
    booking_ref = ""
    view_booking_url = ""
    if url_rows:
        row = dict(url_rows[0])
        member_name        = row.get("requestorname") or ""
        booking_ref        = row.get("requestid") or ""
        requestor_upid     = row.get("requestoruserprofileid") or ""
        provider_upid      = row.get("provider_userprofileid") or ""
        serviceimage       = row.get("serviceimageurl") or ""
        provider_createdon = str(row.get("provider_createdon") or "")

        view_booking_url = "?" + urlencode({
            "type": "Booking",
            "route": "/booking",
            "userprofile": requestor_upid,
            "role": "2",
            "provider_userprofile": provider_upid,
            "booking_state": "requested",
            "booking_id": booking_ref.lower(),
            "business_name": provider_name or "",
            "serviceimage": serviceimage,
            "provider_createdon": provider_createdon,
        })

    for provider_mobile in provider_numbers:
        await send_booking_request_whatsapp_notification(
            mobile_number=provider_mobile,
            provider_name=provider_name or "",
            member_name=member_name,
            booking_items=service_name or "",
            booking_ref=booking_ref,
            view_booking_url=view_booking_url,
            language=language_code or "en",
        )
    logger.info(
        "Booking request WhatsApp sent to provider | service_id=%s | booking_id=%s",
        service_id,
        booking_id,
    )


async def _send_booking_accepted_whatsapp_to_member(
    request_row_id: str,
    provider_name: str,
    language_code: Optional[str] = None,
) -> None:
    """Send a gated booking-accepted WhatsApp to each of the member's mobile numbers when the plan allows."""
    from app.utils.whatsapp import send_booking_accepted_whatsapp_notification

    if not await _booking_whatsapp_enabled(NotificationType.BOOKING_ACCEPTED_WHATSAPP):
        return
    if not await _booking_customer_whatsapp_allowed(request_row_id):
        return

    member_rows = await Database.fetch(FETCH_BOOKING_MEMBER_INFO_QUERY, request_row_id)
    if not member_rows:
        logger.warning(
            "Booking accepted WhatsApp skipped - booking not found | request_row_id=%s", request_row_id
        )
        return
    member_info = dict(member_rows[0])
    member_name = member_info.get("requestorname") or ""
    booking_ref = member_info.get("requestid") or ""

    requestor_id_rows = await Database.fetch(
        "SELECT requestorid FROM etown_allrequests WHERE allrequestid = $1 LIMIT 1",
        request_row_id,
    )
    if not requestor_id_rows:
        logger.warning(
            "Booking accepted WhatsApp skipped - requestorid not found | request_row_id=%s", request_row_id
        )
        return
    requestor_id = dict(requestor_id_rows[0]).get("requestorid")
    if not requestor_id:
        return

    mobile_rows = await Database.fetch(FETCH_REQUESTOR_MOBILE_QUERY, requestor_id)
    if not mobile_rows:
        logger.warning(
            "Booking accepted WhatsApp skipped - member mobile not found | request_row_id=%s", request_row_id
        )
        return
    member_numbers = _booking_whatsapp_destinations(dict(mobile_rows[0]))
    if not member_numbers:
        logger.warning(
            "Booking accepted WhatsApp skipped - empty member mobile | request_row_id=%s", request_row_id
        )
        return

    for member_mobile in member_numbers:
        await send_booking_accepted_whatsapp_notification(
            mobile_number=member_mobile,
            member_name=member_name,
            provider_name=provider_name or "",
            booking_ref=booking_ref,
            language=language_code or "en",
        )
    logger.info(
        "Booking accepted WhatsApp sent to member | request_row_id=%s", request_row_id
    )


async def _send_booking_completed_whatsapp_to_member(
    request_row_id: str,
    provider_name: str,
    language_code: Optional[str] = None,
) -> None:
    """Send a gated booking-completed WhatsApp to each of the member's mobile numbers when the plan allows."""
    from app.utils.whatsapp import send_booking_completed_whatsapp_notification

    if not await _booking_whatsapp_enabled(NotificationType.BOOKING_COMPLETED_WHATSAPP):
        return
    if not await _booking_customer_whatsapp_allowed(request_row_id):
        return

    member_rows = await Database.fetch(FETCH_BOOKING_MEMBER_INFO_QUERY, request_row_id)
    if not member_rows:
        logger.warning(
            "Booking completed WhatsApp skipped - booking not found | request_row_id=%s", request_row_id
        )
        return
    member_info = dict(member_rows[0])
    member_name = member_info.get("requestorname") or ""
    booking_ref = member_info.get("requestid") or ""

    requestor_id_rows = await Database.fetch(
        "SELECT requestorid FROM etown_allrequests WHERE allrequestid = $1 LIMIT 1",
        request_row_id,
    )
    if not requestor_id_rows:
        logger.warning(
            "Booking completed WhatsApp skipped - requestorid not found | request_row_id=%s", request_row_id
        )
        return
    requestor_id = dict(requestor_id_rows[0]).get("requestorid")
    if not requestor_id:
        return

    mobile_rows = await Database.fetch(FETCH_REQUESTOR_MOBILE_QUERY, requestor_id)
    if not mobile_rows:
        logger.warning(
            "Booking completed WhatsApp skipped - member mobile not found | request_row_id=%s", request_row_id
        )
        return
    member_numbers = _booking_whatsapp_destinations(dict(mobile_rows[0]))
    if not member_numbers:
        logger.warning(
            "Booking completed WhatsApp skipped - empty member mobile | request_row_id=%s", request_row_id
        )
        return

    for member_mobile in member_numbers:
        await send_booking_completed_whatsapp_notification(
            mobile_number=member_mobile,
            member_name=member_name,
            provider_name=provider_name or "",
            booking_ref=booking_ref,
            language=language_code or "en",
        )
    logger.info(
        "Booking completed WhatsApp sent to member | request_row_id=%s", request_row_id
    )


async def _send_booking_additional_info_push(
    booking_id: str,
    sender_userprofileid: Optional[str] = None,
    instruction_date: Optional[str] = None,
    preview: Optional[str] = None,
    image_url: Optional[str] = None,
) -> None:
    """Push a new booking instruction to the party who did not send it, logging every failure.

    Args:
        booking_id: The booking's allrequestid.
        sender_userprofileid: Userprofileid stored on the instruction; unmatched falls back to sender_role.
        instruction_date: Instruction timestamp, appended to the dedupe key.
        preview: Push body; a generic "has sent a message" line when empty.
        image_url: Stored blob path of an image instruction, attached to the push.
    """
    try:
        rows = await Database.fetch(
            FETCH_BOOKING_ADDITIONAL_INFO_PUSH_QUERY,
            booking_id,
            str(sender_userprofileid).strip() if sender_userprofileid else "",
        )
        if not rows:
            logger.warning(
                "Additional-info push skipped - booking not found | booking_id=%s",
                booking_id,
            )
            return

        row = dict(rows[0])
        provider_upid = row.get("provider_userprofileid")
        member_upid = row.get("member_userprofileid")
        sender = str(sender_userprofileid).strip().lower() if sender_userprofileid else None

        provider_name = (
            row.get("provider_businessname")
            or row.get("provider_name")
            or "Your service provider"
        )
        member_name = row.get("requestorname") or row.get("member_contactidname") or "A member"

        if sender and provider_upid and sender == str(provider_upid).strip().lower():
            sent_by_provider = True
        elif sender and member_upid and sender == str(member_upid).strip().lower():
            sent_by_provider = False
        else:
            sent_by_provider = _to_optional_int(row.get("sender_role")) == 1

        if sent_by_provider:
            recipient_upid = member_upid
            recipient_role = row.get("member_rolename")
            sender_name = provider_name
            direction = "member"
        else:
            recipient_upid = provider_upid
            recipient_role = row.get("provider_rolename")
            sender_name = member_name
            direction = "provider"

        title = f"{sender_name} (Booking message)"
        body = _truncate_preview(preview) or (
            f"{sender_name} has sent a message about your service request."
        )

        if not recipient_upid:
            logger.warning(
                "Additional-info push skipped - no %s on booking | booking_id=%s",
                direction, booking_id,
            )
            return

        if sender and sender == str(recipient_upid).strip().lower():
            logger.info(
                "Additional-info push skipped - sender is the recipient | booking_id=%s | upid=%s",
                booking_id, recipient_upid,
            )
            return

        dedupe_key = f"booking-additional-info:{booking_id}:{recipient_upid}"
        if instruction_date:
            dedupe_key = f"{dedupe_key}:{instruction_date}"

        ctx: Dict[str, Any] = {
            "title": title,
            "body": body,
            "userprofile_id": recipient_upid,
            "role": recipient_role or "",
        }
        if image_url:
            ctx["image"] = image_url

        from app.etown.notifications.service import GovernanceRequest, send as gov_send
        outcome = await gov_send(GovernanceRequest(
            userprofileid=recipient_upid,
            notification_type=NotificationType.BOOKING_ADDITIONAL_INFO,
            ctx=ctx,
            feature="booking",
            dedupe_key=dedupe_key,
        ))
        logger.info(
            "Additional-info push %s to %s | booking_id=%s | recipient_upid=%s | sender_upid=%s",
            outcome, direction, booking_id, recipient_upid, sender_userprofileid,
        )
    except Exception as exc:
        logger.error(
            "Additional-info push failed | booking_id=%s | error=%s",
            booking_id, exc, exc_info=True,
        )


async def _sync_allrequest_instructions_safe(
    booking_id: str, instructions: Any
) -> None:
    """Mirror a booking's instructions to Dataverse, logging and swallowing any failure."""
    try:
        await update_allrequest_instructions_to_dataverse(booking_id, instructions)
    except Exception as sync_exc:
        logger.error(
            "Dataverse instructions sync failed | bookingid=%s | error=%s",
            booking_id,
            sync_exc,
            exc_info=True,
        )


async def _sync_allrequest_servicetype_safe(
    booking_id: str, servicetype: Optional[str]
) -> None:
    """Mirror a booking's servicetype to Dataverse, logging and swallowing any failure."""
    try:
        await update_allrequest_servicetype_to_dataverse(booking_id, servicetype)
    except Exception as sync_exc:
        logger.error(
            "Dataverse servicetype sync failed | bookingid=%s | error=%s",
            booking_id,
            sync_exc,
            exc_info=True,
        )


class BookingService:
    """Handles Chatty Service Request operations."""

    @staticmethod
    async def create_request_service_lists(
        booking_id: str,
        services: Any,
        requestor_id: Any,
        requestor_userprofileid: Any,
        request_status: Any,
        oauth_token: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> None:
        """Insert a booking's service rows, notify the provider by in-app, push, SMS and WhatsApp, and invite into the circle."""
        if not booking_id or not isinstance(services, list) or not services:
            return

        if Database._pool is None or Database._pool._closed:
            await Database.init()

        final_request_status = request_status or "requested"
        any_service_id = None
        service_items: list = []

        for service in services:
            if not isinstance(service, dict):
                continue

            service_id = service.get("service_id")
            service_quantity_raw = service.get("quantity")
            if service_quantity_raw is None:
                quantity = None
            else:
                quantity_text = str(service_quantity_raw).strip()
                quantity = quantity_text or None
            service_name = service.get("service_name")
            item_name_override = service.get("item_name")
            item_price_override = service.get("item_price")
            item_image_override = service.get("item_image")
            if not service_id:
                continue

            if any_service_id is None:
                any_service_id = service_id

            try:
                request_service_rows = await Database.fetch(
                    INSERT_REQUEST_SERVICE_LIST_QUERY,
                    service_id,
                    booking_id,
                    quantity,
                    item_name_override,
                    item_image_override,
                    item_price_override,
                )
                if request_service_rows:
                    inserted_row = dict(request_service_rows[0])
                    await insert_requestservicelist_to_dataverse(inserted_row)
                    resolved_name = inserted_row.get("name") or service_name or ""
                    if resolved_name:
                        service_items.append((resolved_name, quantity))
                elif service_name:
                    service_items.append((service_name, quantity))
            except Exception as e:
                logger.error(
                    "Failed to insert request service list row | booking_id=%s | service_id=%s | error=%s",
                    booking_id,
                    service_id,
                    e,
                    exc_info=True,
                )
                if service_name:
                    service_items.append((service_name, quantity))

        booking_ref = ""
        stored_request_status = ""
        service_datetime_label = ""
        try:
            booking_row = await Database.fetchrow(
                "SELECT requestid, requeststatus, requestorservicedatetime, requestorfromdate "
                "FROM etown_allrequests WHERE allrequestid = $1 LIMIT 1",
                booking_id,
            )
            if booking_row:
                booking_ref = booking_row.get("requestid") or ""
                stored_request_status = booking_row.get("requeststatus") or ""
                service_datetime_label = _format_service_datetime(
                    booking_row.get("requestorservicedatetime")
                    or booking_row.get("requestorfromdate")
                )
        except Exception as e:
            logger.warning(
                "Could not resolve booking reference for notifications | booking_id=%s | error=%s",
                booking_id, e,
            )

        booking_detail_suffix = ""
        if booking_ref or service_datetime_label:
            parts = [p for p in (booking_ref, service_datetime_label) if p]
            booking_detail_suffix = " (" + " · ".join(parts) + ")"

        notification_content = None
        if any_service_id and requestor_id and requestor_userprofileid:
            booked_service_names = ", ".join(
                name for name, _qty in service_items if name
            )
            try:
                from app.etown.notifications.core.channels.inapp import insert_notification_row
                rcv = await Database.fetchrow(
                    "SELECT ps.contactid, ps.userprofileid, c.firstname "
                    "FROM public.etown_providerservices ps "
                    "JOIN public.etown_contact c ON c.contactid = $1 "
                    "WHERE ps.providerservicesid = $2 LIMIT 1",
                    requestor_id, any_service_id,
                )
                if rcv:
                    content = (
                        f"{rcv['firstname'] or ''} requested your service "
                        f"{booked_service_names}{booking_detail_suffix}"
                    )
                    notif_row = await insert_notification_row(
                        sender_contact_id=requestor_id,
                        sender_userprofile_id=requestor_userprofileid,
                        receiver_contact_id=rcv["contactid"],
                        receiver_userprofile_id=rcv["userprofileid"],
                        content=content,
                        notification_type="Booking",
                        notificationstatus=final_request_status,
                        statecode="Active",
                    )
                    if notif_row:
                        notification_content = notif_row.get("notificationcontent")
                        await insert_notification_to_dataverse(notif_row)
            except Exception as e:
                logger.error(
                    "Failed to insert notification row | booking_id=%s | service_id=%s | error=%s",
                    booking_id,
                    any_service_id,
                    e,
                    exc_info=True,
                )

            await _send_fcm_notification(
                service_id=any_service_id,
                notification_content=notification_content or "",
                oauth_token=oauth_token,
                dedupe_key=f"booking-new:{booking_id}",
                booking_id=booking_id,
                booking_state=stored_request_status or final_request_status,
            )

        if any_service_id:
            businessname = None
            try:
                businessname_rows = await Database.fetch(
                    FETCH_PROVIDER_BUSINESSNAME_QUERY,
                    any_service_id,
                )
                if businessname_rows:
                    businessname = dict(businessname_rows[0]).get("businessname")
                    logger.info(
                        "Provider businessname resolved after service loop | service_id=%s | businessname=%s",
                        any_service_id,
                        businessname,
                    )
            except Exception as e:
                logger.error(
                    "Failed to resolve provider businessname after service loop | service_id=%s | error=%s",
                    any_service_id,
                    e,
                    exc_info=True,
                )

            if businessname:
                await _send_booking_sms_notification(
                    requestor_id=requestor_id,
                    provider_businessname=businessname,
                    language_code=language_code,
                    dedupe_key=f"booking-sms-new:{booking_id}",
                    booking_ref=booking_ref,
                    service_datetime=service_datetime_label,
                )
                booking_items_text = ", ".join(
                    f"{name} \u00d7 {qty}" if qty else name
                    for name, qty in service_items
                )
                asyncio.create_task(
                    _send_booking_request_whatsapp_to_provider(
                        service_id=any_service_id,
                        booking_id=booking_id,
                        provider_name=businessname,
                        service_name=booking_items_text,
                        language_code=language_code,
                    )
                )

        if any_service_id and requestor_id and requestor_userprofileid:
            try:
                relation_rows = await Database.fetch(
                    CHECK_RELATION_QUERY,
                    any_service_id,
                    requestor_id,
                    requestor_userprofileid,
                )
                if relation_rows:
                    row = dict(relation_rows[0])
                    both_exist = row.get("both_relations_exist", False)
                    if both_exist:
                        logger.info("Background process complete")
                    else:
                        logger.info(
                            "Relation check returned false | booking_id=%s | service_id=%s | member_count=%s | provider_count=%s",
                            booking_id,
                            any_service_id,
                            row.get("member_count"),
                            row.get("provider_count"),
                        )
                        try:
                            inv_rows = await Database.fetch(
                                CHECK_INVITATION_NOTIFICATION_QUERY,
                                any_service_id,
                                requestor_id,
                                requestor_userprofileid,
                            )
                            if inv_rows and dict(inv_rows[0]).get("notification_exists"):
                                logger.info("Background process completed")
                            else:
                                logger.info(
                                    "Invitation notification check returned false | booking_id=%s | service_id=%s",
                                    booking_id,
                                    any_service_id,
                                )
                                try:
                                    invitation_rows = await Database.fetch(
                                        INSERT_INVITATION_QUERY,
                                        requestor_id,
                                        requestor_userprofileid,
                                        any_service_id,
                                    )
                                    if invitation_rows:
                                        await insert_invitation_to_dataverse(dict(invitation_rows[0]))
                                    logger.info(
                                        "Invitation inserted | booking_id=%s | service_id=%s",
                                        booking_id,
                                        any_service_id,
                                    )
                                    try:
                                        role_rows = await Database.fetch(
                                            FETCH_REQUESTOR_ROLE_QUERY,
                                            requestor_userprofileid,
                                        )
                                        role = dict(role_rows[0]).get("role") if role_rows else None
                                        if str(role) == "1":
                                            provider_relation_rows = await Database.fetch(
                                                INSERT_PROVIDER_RELATION_QUERY,
                                                requestor_userprofileid,
                                                any_service_id,
                                            )
                                            if provider_relation_rows:
                                                await insert_providerrelation_to_dataverse(dict(provider_relation_rows[0]))
                                            logger.info(
                                                "Provider relation inserted | booking_id=%s | service_id=%s",
                                                booking_id,
                                                any_service_id,
                                            )
                                        elif str(role) == "2":
                                            member_relation_rows = await Database.fetch(
                                                INSERT_MEMBER_RELATION_QUERY,
                                                requestor_userprofileid,
                                                any_service_id,
                                            )
                                            if member_relation_rows:
                                                await insert_memberrelation_to_dataverse(dict(member_relation_rows[0]))
                                            logger.info(
                                                "Member relation inserted | booking_id=%s | service_id=%s",
                                                booking_id,
                                                any_service_id,
                                            )
                                        else:
                                            logger.warning(
                                                "Unknown role for relation insert | role=%s | booking_id=%s",
                                                role,
                                                booking_id,
                                            )
                                    except Exception as rel_e:
                                        logger.error(
                                            "Relation insert failed after invitation | booking_id=%s | service_id=%s | error=%s",
                                            booking_id,
                                            any_service_id,
                                            rel_e,
                                            exc_info=True,
                                        )

                                    try:
                                        circle_notification_rows = await Database.fetch(
                                            INSERT_CIRCLE_NOTIFICATION_QUERY,
                                            any_service_id,
                                            requestor_id,
                                            requestor_userprofileid,
                                        )
                                        if circle_notification_rows:
                                            await insert_notification_to_dataverse(dict(circle_notification_rows[0]))
                                        logger.info(
                                            "Circle notification inserted | booking_id=%s | service_id=%s",
                                            booking_id,
                                            any_service_id,
                                        )
                                    except Exception as cn_e:
                                        logger.error(
                                            "Circle notification insert failed | booking_id=%s | service_id=%s | error=%s",
                                            booking_id,
                                            any_service_id,
                                            cn_e,
                                            exc_info=True,
                                        )
                                except Exception as ins_e:
                                    logger.error(
                                        "Invitation insert failed | booking_id=%s | service_id=%s | error=%s",
                                        booking_id,
                                        any_service_id,
                                        ins_e,
                                        exc_info=True,
                                    )
                        except Exception as inv_e:
                            logger.error(
                                "Invitation notification check failed | booking_id=%s | service_id=%s | error=%s",
                                booking_id,
                                any_service_id,
                                inv_e,
                                exc_info=True,
                            )
            except Exception as e:
                logger.error(
                    "Relation check failed | booking_id=%s | service_id=%s | error=%s",
                    booking_id,
                    any_service_id,
                    e,
                    exc_info=True,
                )

    @staticmethod
    async def add_booking(data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new etown_allrequests booking with generated tracking ID and OTP, and return the row.

        Raises:
            HTTPException: 500 when the insert fails.
        """
        try:
            if Database._pool is None or Database._pool._closed:
                await Database.init()

            request_id = f"ETRQ{rand(1, 99999)}"

            services = data.get("services") or []
            quantity = None
            if isinstance(services, list) and services:
                first_service = services[0] or {}
                if isinstance(first_service, dict):
                    quantity = _to_optional_int(first_service.get("quantity"))

            requestor_from_date = _to_optional_datetime(data.get("requestor_from_date"))
            requestor_to_date = _to_optional_datetime(data.get("requestor_to_date"))

            requestor_service_datetime = data.get("requestor_service_datetime")
            if requestor_service_datetime is None:
                requestor_service_datetime = data.get("requestor_from_date")
            if requestor_service_datetime is not None and not isinstance(requestor_service_datetime, str):
                requestor_service_datetime = str(requestor_service_datetime)

            otp = str(rand(1000, 9999))

            servicetype = None
            first_service_id = None
            if isinstance(services, list) and services:
                first_service = services[0] or {}
                if isinstance(first_service, dict):
                    first_service_id = first_service.get("service_id")
            if first_service_id:
                try:
                    servicetype_row = await Database.fetchrow(
                        """
                        SELECT ep.servicetype
                        FROM etown_providerservices ps
                        LEFT JOIN etown_provider ep ON ep.providerid = ps.providerid
                        WHERE ps.providerservicesid = $1
                        LIMIT 1
                        """,
                        first_service_id,
                    )
                    if servicetype_row:
                        servicetype = servicetype_row.get("servicetype")
                except Exception as e:
                    logger.warning(
                        "Could not resolve provider servicetype for booking | service_id=%s | error=%s",
                        first_service_id,
                        e,
                    )

            request_details = data.get("request_details")
            instructions_json = None
            if request_details is not None and str(request_details).strip():
                ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
                instructions_json = json.dumps([
                    {
                        "instruction": str(request_details).strip(),
                        "date": ist_now.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                ])

            inserted_rows = await Database.fetch(
                INSERT_ALLREQUESTS_QUERY,
                request_id,
                data.get("requestor_name"),
                data.get("requestor_Id"),
                data.get("requestor_userprofileid"),
                data.get("requestor_Address"),
                data.get("requestor_latitude"),
                data.get("requestor_longitude"),
                data.get("requestor_request_status"),
                quantity,
                requestor_from_date,
                requestor_to_date,
                data.get("requestor_Contactnumber"),
                data.get("requestor_identification"),
                data.get("requestor_requestfile_url"),
                data.get("requestor_role"),
                requestor_service_datetime,
                otp,
                servicetype,
                instructions_json,
            )

            if not inserted_rows:
                raise HTTPException(status_code=500, detail="Failed to create booking request")

            return dict(inserted_rows[0])
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Booking add failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to create booking request")

    @staticmethod
    @with_retry()
    async def resolve_booking_id_for_user(
        user_profile_id: Optional[str],
        contact_id: Optional[str] = None,
        booking_reference: Optional[str] = None,
    ) -> Optional[str]:
        """Return the user's booking matching a reference by profile or contact, else the acting profile's latest, or None.

        Args:
            user_profile_id: Acting profile ID; required for the no-reference fallback.
            contact_id: The requesting user's contact ID.
            booking_reference: Optional allrequestid or tracking ID to match.
        """
        if Database._pool is None or Database._pool._closed:
            await Database.init()

        upid = str(user_profile_id) if user_profile_id else None
        cid = str(contact_id) if contact_id else None
        if not upid and not cid:
            return None

        if booking_reference and str(booking_reference).strip():
            ref = str(booking_reference).strip()
            query = """
            SELECT allrequestid
            FROM etown_allrequests
            WHERE (allrequestid = $1 OR requestid = $1)
              AND (
                  ($2::text IS NOT NULL AND requestoruserprofileid = $2)
                  OR ($3::text IS NOT NULL AND requestorid = $3)
              )
            ORDER BY createdon DESC NULLS LAST
            LIMIT 1;
            """
            row = await Database.fetchrow(query, ref, upid, cid)
        else:
            if not upid:
                return None
            query = """
            SELECT allrequestid
            FROM etown_allrequests
            WHERE requestoruserprofileid = $1
            ORDER BY createdon DESC NULLS LAST
            LIMIT 1;
            """
            row = await Database.fetchrow(query, upid)

        return row["allrequestid"] if row else None

    @staticmethod
    @with_retry()
    async def fetch_booking_core(allrequest_id: str) -> Optional[Dict[str, Any]]:
        """Read one booking from etown_allrequests as a list-API-shaped entry with empty ServiceDetails, or None."""
        if Database._pool is None or Database._pool._closed:
            await Database.init()

        row = await Database.fetchrow(
            """
            SELECT allrequestid, requestid, requestorname, requestorid, requestoruserprofileid,
                   requestoraddress, requeststatus, createdon, cancelreason,
                   bookingacceptedtime, bookingcompletedtime
            FROM etown_allrequests
            WHERE allrequestid = $1
            LIMIT 1
            """,
            str(allrequest_id),
        )
        if not row:
            return None

        def _ist(value, fmt="%Y-%m-%d %H:%M:%S"):
            return (value + timedelta(hours=5, minutes=30)).strftime(fmt) if value else None

        created = row.get("createdon")
        created_ist = created + timedelta(hours=5, minutes=30) if created else None
        return {
            "BookingDetails": {
                "Etown_BookingId": row.get("allrequestid"),
                "Etown_BookingTrackId": row.get("requestid"),
                "Etown_requestorname": row.get("requestorname"),
                "Etown_requestorContactId": row.get("requestorid"),
                "Etown_requestorUserProfileId": row.get("requestoruserprofileid"),
                "Etown_requestoraddress": row.get("requestoraddress"),
                "Etown_requeststatus": row.get("requeststatus"),
                "Etown_bookingcreatedon": (
                    f"{created_ist.month}/{created_ist.day}/{created_ist.year} "
                    f"{int(created_ist.strftime('%I'))}:{created_ist.strftime('%M')} "
                    f"{created_ist.strftime('%p')}"
                ) if created_ist else None,
                "Etown_updatedacceptedtime": _ist(row.get("bookingacceptedtime")),
                "Etown_updatedcompletedtime": _ist(row.get("bookingcompletedtime")),
                "Etown_cancelreason": row.get("cancelreason"),
                "Etown_ServiceProviderBusinessname": None,
            },
            "ServiceDetails": [],
        }

    @staticmethod
    async def cancel_booking(
        user_profile_id: Optional[str],
        contact_id: Optional[str],
        booking_reference: Optional[str] = None,
        cancel_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel a booking the caller owns via update_booking, refusing ones already cancelled or completed.

        Args:
            user_profile_id: The requesting user's profile ID.
            contact_id: The requesting user's contact ID.
            booking_reference: Tracking ID or booking ID; None targets the latest booking.
            cancel_reason: Optional free-text reason recorded against the booking.

        Returns:
            Dict with status success, error, not_found or not_allowed, plus booking IDs.
        """
        if not user_profile_id and not contact_id:
            return {"status": "error", "message": "User not authenticated"}

        if Database._pool is None or Database._pool._closed:
            await Database.init()

        resolved_id = await BookingService.resolve_booking_id_for_user(
            user_profile_id=user_profile_id,
            contact_id=contact_id,
            booking_reference=booking_reference,
        )
        if not resolved_id:
            return {
                "status": "not_found",
                "message": "No booking matching that reference belongs to this user",
                "booking_id": booking_reference,
            }

        row = await Database.fetchrow(
            "SELECT requestid, requeststatus FROM etown_allrequests WHERE allrequestid = $1 LIMIT 1",
            resolved_id,
        )
        current_status = ((row or {}).get("requeststatus") or "").strip().lower()
        tracking_id = (row or {}).get("requestid")

        if current_status == "cancelled":
            return {
                "status": "not_allowed",
                "message": "This booking is already cancelled",
                "booking_id": resolved_id,
                "tracking_id": tracking_id,
                "request_status": current_status,
            }
        if current_status == "completed":
            return {
                "status": "not_allowed",
                "message": "This booking is already completed and can no longer be cancelled",
                "booking_id": resolved_id,
                "tracking_id": tracking_id,
                "request_status": current_status,
            }

        role_row = await Database.fetchrow(
            "SELECT role FROM etown_userprofile WHERE userprofileid = $1 LIMIT 1",
            user_profile_id,
        )
        actor_role = "provider" if (role_row and role_row["role"] == 1) else "member"

        await BookingService.update_booking({
            "request_row_id": resolved_id,
            "request_Status": "cancelled",
            "cancel_reason": cancel_reason,
            "role": actor_role,
        })

        logger.info(
            "cancel_booking | booking=%s tracking=%s actor=%s previous_status=%s",
            resolved_id, tracking_id, actor_role, current_status,
        )
        return {
            "status": "success",
            "message": "Booking cancelled",
            "booking_id": resolved_id,
            "tracking_id": tracking_id,
            "request_status": "cancelled",
        }

    @staticmethod
    async def _upload_booking_instruction_media(
        booking_id: str,
        base64_data: Optional[str],
        media_type: str,
        name_suffix: str = "instruction",
    ) -> Optional[str]:
        """Upload instruction media to Azure Blob under its resolved MIME and return the relative URL, or None.

        Args:
            booking_id: The booking's allrequestid, used in the blob path.
            base64_data: Base64 or data-url payload; an http(s) URL is returned unchanged.
            media_type: "image", "video" or "document", selecting the fallback extension.
            name_suffix: Tail of the blob file name, such as "thumbnail".
        """
        if not base64_data or not str(base64_data).strip():
            return None

        try:
            value = str(base64_data).strip()
            if value.lower() == "null":
                return None
            if value.startswith(("http://", "https://")):
                return value

            raw = to_bytes(value)
            if not raw:
                return None

            mime = _resolve_instruction_media_mime(raw, _declared_data_url_mime(value))
            ext = _instruction_media_extension(mime, media_type)
            container = normalize_container_name(settings.AZURE_BLOB_CONTAINER or "etowndev")
            blob_name = f"booking_instructions/{booking_id}/{current_timestamp_prefix()}_{name_suffix}.{ext}"
            blob_path = await upload_blob_bytes(container, blob_name, raw, mime)
            return to_relative_blob_url(blob_path) or blob_path
        except Exception as exc:
            logger.warning(
                "Booking instruction %s %s upload failed | booking_id=%s | error=%s",
                media_type, name_suffix, booking_id, exc,
            )
            return None

    @staticmethod
    async def _resolve_instruction_sender_ids(userprofileid: Optional[str]) -> Dict[str, Optional[str]]:
        """Return the trimmed sender userprofileid and its etown_userprofile contactid, either None when unknown."""
        clean_userprofileid = str(userprofileid).strip() if userprofileid is not None and str(userprofileid).strip() else None
        if clean_userprofileid is None:
            return {"userprofileid": None, "contactid": None}

        try:
            row = await Database.fetchrow(
                """
                SELECT up.contactid
                FROM public.etown_userprofile up
                WHERE LOWER(up.userprofileid) = LOWER($1)
                LIMIT 1
                """,
                clean_userprofileid,
            )
            return {
                "userprofileid": clean_userprofileid,
                "contactid": row["contactid"] if row is not None else None,
            }
        except Exception as exc:
            logger.warning(
                "Unable to resolve booking instruction sender contactid | userprofileid=%s | error=%s",
                clean_userprofileid, exc,
            )
            return {"userprofileid": clean_userprofileid, "contactid": None}

    @staticmethod
    @with_retry()
    async def add_additional_info(
        booking_id: str,
        userprofileid: Optional[str] = None,
        instruction_type: str = "text",
        content: Optional[str] = None,
        thumbnail: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append one text or uploaded-media instruction to a booking, then mirror it and push the other party.

        Args:
            booking_id: The booking's allrequestid.
            userprofileid: The sender's profile ID, stored on the entry.
            instruction_type: One of INSTRUCTION_TYPES: text, image, video or document.
            content: The instruction text, or the base64/data-url media payload.
            thumbnail: A video's poster frame payload or stored URL; ignored for other types.

        Raises:
            HTTPException: 400 on bad input or failed upload, 404 unknown booking, 500 on failure.
        """
        try:
            if Database._pool is None or Database._pool._closed:
                await Database.init()

            if not booking_id or not str(booking_id).strip():
                raise HTTPException(status_code=400, detail="bookingid is required")

            clean_type = str(instruction_type or "").strip().lower()
            if clean_type not in INSTRUCTION_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"type must be one of: {', '.join(INSTRUCTION_TYPES)}",
                )

            clean_content = str(content).strip() if content is not None else ""
            if not clean_content or clean_content.lower() == "null":
                raise HTTPException(status_code=400, detail="content is required")

            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            clean_date = ist_now.strftime("%Y-%m-%dT%H:%M:%S")
            sender = await BookingService._resolve_instruction_sender_ids(userprofileid)
            sender_userprofileid = sender.get("userprofileid")
            sender_contactid = sender.get("contactid")

            payload_name = None
            stored_thumbnail = None
            if clean_type in INSTRUCTION_MEDIA_TYPES:
                payload_name = _data_url_filename(clean_content)
                stored_instruction = await BookingService._upload_booking_instruction_media(
                    booking_id, clean_content, clean_type
                )
                if not stored_instruction:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unable to upload booking instruction {clean_type}",
                    )
                if clean_type == "video":
                    stored_thumbnail = await BookingService._upload_booking_instruction_media(
                        booking_id, thumbnail, "image", name_suffix="thumbnail"
                    )
            else:
                stored_instruction = clean_content

            new_entry: Dict[str, Any] = {
                "type": clean_type,
                "instruction": stored_instruction,
                "date": clean_date,
                "userprofileid": sender_userprofileid,
                "contactid": sender_contactid,
            }
            if stored_thumbnail:
                new_entry["thumbnail"] = stored_thumbnail
            if clean_type == "document" and payload_name:
                new_entry["filename"] = payload_name
            new_instructions = [new_entry]

            query = """
            UPDATE etown_allrequests
            SET instructions = COALESCE(instructions, '[]'::jsonb) || $1::jsonb,
                modifieddate = NOW()
            WHERE allrequestid = $2
            RETURNING instructions;
            """
            row = await Database.fetchrow(
                query, json.dumps(new_instructions), str(booking_id)
            )

            if row is None:
                raise HTTPException(status_code=404, detail="Booking request not found")

            merged_raw = row["instructions"]
            if isinstance(merged_raw, str):
                try:
                    merged = json.loads(merged_raw)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("booking: stored instructions are not valid JSON; replacing them with the new payload", exc_info=True)
                    merged = new_instructions
            else:
                merged = merged_raw if merged_raw is not None else new_instructions

            logger.info(
                "Appended %d instruction(s) to booking %s (total now %d)",
                len(new_instructions),
                booking_id,
                len(merged) if isinstance(merged, list) else 0,
            )

            asyncio.create_task(
                _sync_allrequest_instructions_safe(str(booking_id), merged)
            )

            preview = _instruction_push_preview(
                clean_type,
                stored_instruction,
                filename=payload_name,
            )
            if clean_type == "image":
                push_image = stored_instruction
            elif clean_type == "video":
                push_image = stored_thumbnail
            else:
                push_image = None

            asyncio.create_task(_send_booking_additional_info_push(
                str(booking_id),
                sender_userprofileid=sender_userprofileid,
                instruction_date=clean_date,
                preview=preview,
                image_url=push_image,
            ))

            response_instructions = [
                {k: v for k, v in entry.items() if k not in ("name", "profileimage")}
                if isinstance(entry, dict) else entry
                for entry in (merged if isinstance(merged, list) else [])
            ]

            return {
                "success": True,
                "bookingid": str(booking_id),
                "instructions": response_instructions,
                "message": "Instructions stored successfully",
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to store booking instructions: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to store booking instructions")

    @staticmethod
    async def get_booking_communications(booking_id: str) -> Dict[str, Any]:
        """Return a booking's ledger notification attempts and stored instructions as one list, newest first.

        Raises:
            HTTPException: 400 without a booking ID, 404 unknown booking, 500 on failure.
        """
        try:
            if Database._pool is None or Database._pool._closed:
                await Database.init()

            if not booking_id or not str(booking_id).strip():
                raise HTTPException(status_code=400, detail="bookingid is required")

            booking = await Database.fetchrow(
                "SELECT requestid, requestorname, requestorid, requestoruserprofileid, "
                "instructions, createdon "
                "FROM etown_allrequests WHERE allrequestid = $1 LIMIT 1",
                str(booking_id),
            )
            if booking is None:
                raise HTTPException(status_code=404, detail="Booking request not found")
            
            legacy_sms_keys = [
                f"booking-sms-new:{booking['requestorid']}:{row['providerservices']}"
                for row in await Database.fetch(
                    "SELECT providerservices FROM etown_requestservicelists WHERE bookingid = $1",
                    str(booking_id),
                )
                if row["providerservices"] and booking["requestorid"]
            ]

            communications: List[Dict[str, Any]] = []
            ledger_rows = await Database.fetch(
                """
                SELECT channel, notification_type, status, suppression_reason,
                       userprofileid, created_at, opened_at
                FROM notifications.etown_notification_ledger
                WHERE dedupe_key LIKE 'booking-%'
                  AND (dedupe_key LIKE '%' || $1 || '%'
                       OR EXISTS (
                           SELECT 1 FROM unnest($2::text[]) AS legacy(key)
                           WHERE dedupe_key LIKE legacy.key || '%'
                       ))
                ORDER BY created_at DESC
                """,
                str(booking_id), legacy_sms_keys,
            )
            for row in ledger_rows:
                communications.append({
                    "channel": row["channel"],
                    "type": row["notification_type"],
                    "direction": "outbound",
                    "status": row["status"],
                    "suppression_reason": row["suppression_reason"],
                    "recipient_userprofileid": row["userprofileid"],
                    "sent_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "opened_at": row["opened_at"].isoformat() if row["opened_at"] else None,
                })

            raw_instructions = booking["instructions"]
            if isinstance(raw_instructions, str):
                try:
                    raw_instructions = json.loads(raw_instructions)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("booking: stored instructions are not valid JSON; returning an empty list", exc_info=True)
                    raw_instructions = []
            for entry in raw_instructions or []:
                if not isinstance(entry, dict):
                    continue
                communications.append({
                    "channel": "in_app",
                    "type": "booking_instruction",
                    "direction": "member_to_provider",
                    "status": "sent",
                    "suppression_reason": None,
                    "recipient_userprofileid": None,
                    "message": entry.get("instruction"),
                    "sent_at": entry.get("date"),
                    "opened_at": None,
                })

            communications.sort(key=lambda item: item.get("sent_at") or "", reverse=True)
            logger.info(
                "Booking communication history | booking_id=%s | entries=%d",
                booking_id, len(communications),
            )
            return {
                "success": True,
                "bookingid": str(booking_id),
                "trackingid": booking["requestid"],
                "communications": communications,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to load booking communications: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to load booking communications")

    @staticmethod
    async def update_servicetype(
        booking_id: str,
        servicetype: Optional[str],
    ) -> Dict[str, Any]:
        """Overwrite a booking's servicetype, blank storing NULL, and mirror it to Dataverse in the background.

        Raises:
            HTTPException: 400 without a booking ID, 404 unknown booking, 500 on failure.
        """
        try:
            if Database._pool is None or Database._pool._closed:
                await Database.init()

            if not booking_id or not str(booking_id).strip():
                raise HTTPException(status_code=400, detail="bookingid is required")

            clean_servicetype = (
                str(servicetype).strip() if servicetype is not None and str(servicetype).strip() else None
            )

            query = """
            UPDATE etown_allrequests
            SET servicetype = $1,
                modifieddate = NOW()
            WHERE allrequestid = $2
            RETURNING servicetype;
            """
            row = await Database.fetchrow(query, clean_servicetype, str(booking_id))

            if row is None:
                raise HTTPException(status_code=404, detail="Booking request not found")

            logger.info(
                "Updated servicetype on booking %s -> %s", booking_id, clean_servicetype
            )

            asyncio.create_task(
                _sync_allrequest_servicetype_safe(str(booking_id), clean_servicetype)
            )

            return {
                "success": True,
                "bookingid": str(booking_id),
                "servicetype": row["servicetype"],
                "message": "Servicetype updated successfully",
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update booking servicetype: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to update booking servicetype")

    @staticmethod
    async def update_booking(data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a booking's status, timestamps, price and schedule, notify the other party, and mirror to Dataverse.

        Raises:
            HTTPException: 400 on bad input, 404 unknown booking, 500 on failure.
        """
        try:
            if Database._pool is None or Database._pool._closed:
                await Database.init()

            request_row_id = data.get("request_row_id")
            request_status = data.get("request_Status")
            cancel_reason = data.get("cancel_reason")
            service_provider_name = data.get("service_provider_name")
            service_provider_userprofileid = data.get("service_provideruserprofileid")
            update_requested_bookings = data.get("update_RequestedBookings")
            update_accepted_bookings = data.get("update_AcceptedBookings")
            service_provider_latitude = data.get("service_provider_latitude")
            service_provider_longitude = data.get("service_provider_longitude")
            serviceprovider_current_address = data.get("serviceprovider_currentAddress")
            serviceprovider_current_address_latitude = data.get("serviceprovider_currentAddress_latitude")
            serviceprovider_current_address_longitude = data.get("serviceprovider_currentAddress_longitude")
            accepted_price = data.get("price")
            accepted_service_datetime = data.get("service_datetime")
            parsed_update_accepted_bookings = _to_optional_datetime(update_accepted_bookings)

            if update_accepted_bookings is not None and parsed_update_accepted_bookings is None:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid datetime format for update_AcceptedBookings",
                )

            if not request_row_id:
                raise HTTPException(status_code=400, detail="request_row_id is required")

            role = data.get("role")

            if update_requested_bookings is None:
                query = """
                UPDATE etown_allrequests
                SET
                    bookingacceptedtime = (
                        SELECT bookingacceptedtime
                        FROM etown_allrequests
                        WHERE allrequestid = $3
                    ),
                    bookingcompletedtime = CASE
                        WHEN lower(trim($2)) = 'cancelled' THEN bookingcompletedtime
                        ELSE NOW()
                    END,
                    cancelreason = $1,
                    requeststatus = $2,

                    bookingcancelledtime = CASE
                        WHEN lower(trim($2)) = 'cancelled' THEN NOW()
                        ELSE bookingcancelledtime
                    END,
                    modifieddate = NOW()
                WHERE allrequestid = $3;
"""
                result = await Database.execute(query, cancel_reason, request_status, request_row_id)
            else:
                query = """
                UPDATE etown_allrequests
                SET

                    bookingacceptedtime = CASE
                        WHEN lower(trim($3)) = 'cancelled' THEN bookingacceptedtime
                        ELSE NOW()
                    END,
                    bookingcompletedtime = $1,
                    cancelreason = $2,
                    requeststatus = $3,

                    bookingcancelledtime = CASE
                        WHEN lower(trim($3)) = 'cancelled' THEN NOW()
                        ELSE bookingcancelledtime
                    END,
                    modifieddate = NOW()
                WHERE allrequestid = $4;
"""
                result = await Database.execute(
                    query,
                    parsed_update_accepted_bookings,
                    cancel_reason,
                    request_status,
                    request_row_id,
                )

            def _is_nullish(value: Any) -> bool:
                return value is None or (isinstance(value, str) and not value.strip())

            if (
                _is_nullish(serviceprovider_current_address)
                and _is_nullish(service_provider_latitude)
                and _is_nullish(service_provider_longitude)
            ):
                rating_query = """
                UPDATE etown_allrequests
                SET "isRating" = 'False',
                    modifieddate = NOW()
                WHERE allrequestid = $1;
                """
                await Database.execute(rating_query, request_row_id)
            else:
                rating_query = """
                UPDATE etown_allrequests
                SET "isRating" = 'False',
                    modifieddate = NOW()
                WHERE allrequestid = $1;
                """
                await Database.execute(rating_query, request_row_id)

                service_list_query = """
                UPDATE etown_requestservicelists
                SET
                    serviceproviderlatitude = $1,
                    serviceproviderlongitude = $2,
                    serviceprovidercurrentaddress = $3,
                    serviceprovidercurrentaddresslatitude = $4,
                    serviceprovidercurrentaddresslongitude = $5,
                    modifieddate = NOW()
                WHERE bookingid = $6;
                """
                await Database.execute(
                    service_list_query,
                    service_provider_latitude,
                    service_provider_longitude,
                    serviceprovider_current_address,
                    serviceprovider_current_address_latitude,
                    serviceprovider_current_address_longitude,
                    request_row_id,
                )

            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Booking request not found")

            if accepted_price is not None and str(accepted_price).strip():
                updated_service_rows = await Database.fetch(
                    "UPDATE etown_requestservicelists SET price = $1, modifieddate = NOW() "
                    "WHERE bookingid = $2 RETURNING *",
                    str(accepted_price).strip(),
                    request_row_id,
                )
                try:
                    for updated_service_row in updated_service_rows:
                        await insert_requestservicelist_to_dataverse(dict(updated_service_row))
                except Exception as sync_exc:
                    logger.error(
                        "Dataverse requestservicelist price sync failed | request_row_id=%s | error=%s",
                        request_row_id,
                        sync_exc,
                        exc_info=True,
                    )

            if accepted_service_datetime is not None and str(accepted_service_datetime).strip():
                await Database.execute(
                    """
                    UPDATE etown_allrequests
                    SET requestorservicedatetime = $1,
                        requestorfromdate = COALESCE($2::timestamp, requestorfromdate),
                        requestortodate = COALESCE($2::timestamp, requestortodate),
                        modifieddate = NOW()
                    WHERE allrequestid = $3;
                    """,
                    str(accepted_service_datetime).strip(),
                    _to_optional_datetime(accepted_service_datetime),
                    request_row_id,
                )

            if (request_status or "").strip().lower() in {"accepted", "declined", "accept_declined"}:
                notification_query = """
                INSERT INTO etown_notifications (
                    notificationsid,
                    notificationcontent,
                    receivercontactid,
                    receivercontactidname,
                    receiveruserprofileid,
                    receiveruserprofileidname,
                    sendercontactid,
                    sendercontactidname,
                    senderuserprofileid,
                    senderuserprofileidname,
                    notificationstatus,
                    notificationtype,
                    createdon
                )
                VALUES (
                    gen_random_uuid()::text,
                    CASE
                        WHEN LOWER(TRIM($3::text)) = 'accept_declined' THEN CONCAT(
                            COALESCE($2::text, ''),
                            ' has declined your booking request'
                        )
                        ELSE CONCAT(
                            COALESCE($2::text, ''),
                            ' has ',
                            $3::text,
                            ' your booking request'
                        )
                    END,
                    (SELECT requestorid FROM etown_allrequests WHERE allrequestid = $1),
                    (SELECT requestorname FROM etown_allrequests WHERE allrequestid = $1),
                    (SELECT requestoruserprofileid FROM etown_allrequests WHERE allrequestid = $1),
                    (SELECT requestoruserprofileidname FROM etown_allrequests WHERE allrequestid = $1),
                    (SELECT rsl.serviceprovidercontactid
                     FROM etown_allrequests ar
                     LEFT JOIN etown_requestservicelists rsl
                        ON rsl.bookingid = ar.allrequestid
                     WHERE ar.allrequestid = $1
                     LIMIT 1),
                    (SELECT up.name FROM etown_userprofile up
                     WHERE up.contactid = (
                        SELECT rsl.serviceprovidercontactid
                        FROM etown_allrequests ar
                        LEFT JOIN etown_requestservicelists rsl
                           ON rsl.bookingid = ar.allrequestid
                        WHERE ar.allrequestid = $1
                                LIMIT 1)
                            LIMIT 1),
                    (SELECT rsl.serviceprovideruserprofileid
                     FROM etown_allrequests ar
                     LEFT JOIN etown_requestservicelists rsl
                        ON rsl.bookingid = ar.allrequestid
                     WHERE ar.allrequestid = $1
                     LIMIT 1),
                    (SELECT up.name FROM etown_userprofile up
                     WHERE up.userprofileid = (
                        SELECT rsl.serviceprovideruserprofileid
                        FROM etown_allrequests ar
                        LEFT JOIN etown_requestservicelists rsl
                           ON rsl.bookingid = ar.allrequestid
                        WHERE ar.allrequestid = $1
                                LIMIT 1)
                            LIMIT 1),
                    $3::text,
                    'Booking',
                    NOW()
                )
                RETURNING *;
                """
                notif_inserted = await Database.fetch(
                    notification_query,
                    request_row_id,
                    service_provider_name,
                    request_status,
                )
                logger.info("Booking status notification inserted | request_row_id=%s | request_status=%s", request_row_id, request_status)
                if notif_inserted:
                    await insert_notification_to_dataverse(dict(notif_inserted[0]))
                notification_content = (
                    dict(notif_inserted[0]).get("notificationcontent") or ""
                    if notif_inserted else ""
                )

                fcm_data_query = """
                SELECT
                    cd.deviceguid,
                    ar.requestoruserprofileid,
                    up.role
                FROM etown_allrequests ar
                LEFT JOIN LATERAL (
                    SELECT COALESCE(NULLIF(cd2.device_token, ''), NULLIF(cd2.deviceguid, '')) AS deviceguid
                    FROM etown_contactdevices cd2
                    WHERE cd2.userprofileid = ar.requestoruserprofileid
                      AND COALESCE(NULLIF(cd2.device_token, ''), NULLIF(cd2.deviceguid, '')) IS NOT NULL
                    ORDER BY cd2.updatedon DESC NULLS LAST, cd2.createdon DESC NULLS LAST
                    LIMIT 1
                ) cd ON TRUE
                LEFT JOIN etown_userprofile up
                    ON up.userprofileid = ar.requestoruserprofileid
                WHERE ar.allrequestid = $1
                LIMIT 1;
                """
                fcm_rows = await Database.fetch(fcm_data_query, request_row_id)

                if fcm_rows:
                    fcm_row = dict(fcm_rows[0])
                    device_guid = fcm_row.get("deviceguid")
                    requestor_userprofile_id = fcm_row.get("requestoruserprofileid") or ""
                    requestor_role = fcm_row.get("role") or ""

                    oauth_token = data.get("Oauth")
                    if device_guid:
                        from app.etown.notifications.service import GovernanceRequest, send as gov_send
                        try:
                            await gov_send(GovernanceRequest(
                                userprofileid=str(requestor_userprofile_id),
                                notification_type=NotificationType.BOOKING_REQUEST,
                                ctx={
                                    "body": notification_content or "",
                                    "userprofile_id": str(requestor_userprofile_id),
                                    "role": str(requestor_role),
                                    "booking_state": str(request_status or ""),
                                    "booking_id": str(request_row_id),
                                },
                                oauth_token=oauth_token,
                                feature="booking",
                                dedupe_key=f"booking-status:{request_row_id}:{request_status}",
                            ))
                            logger.info(
                                "Booking status push dispatched | request_row_id=%s | request_status=%s",
                                request_row_id,
                                request_status,
                            )
                        except Exception as fcm_err:
                            logger.error(
                                "Booking status push failed | request_row_id=%s | request_status=%s | error=%s",
                                request_row_id,
                                request_status,
                                fcm_err,
                                exc_info=True,
                            )
                    else:
                        logger.warning(
                            "FCM send skipped — missing device_guid | request_row_id=%s",
                            request_row_id,
                        )
                else:
                    logger.warning(
                        "FCM send skipped — no requestor device data found | request_row_id=%s",
                        request_row_id,
                    )

                if (request_status or "").strip().lower() == "accepted":
                    asyncio.create_task(
                        _send_booking_accepted_whatsapp_to_member(
                            request_row_id=request_row_id,
                            provider_name=service_provider_name or "",
                            language_code=data.get("language", "en"),
                        )
                    )

            elif (request_status or "").strip().lower() == "completed":
                member_completed = (role or "").strip().lower() == "member"

                member_contactid = "(SELECT requestorid FROM etown_allrequests WHERE allrequestid = $1)"
                member_contactname = "(SELECT requestorname FROM etown_allrequests WHERE allrequestid = $1)"
                member_upid = "(SELECT requestoruserprofileid FROM etown_allrequests WHERE allrequestid = $1)"
                member_upname = "(SELECT requestoruserprofileidname FROM etown_allrequests WHERE allrequestid = $1)"

                provider_contactid = (
                    "(SELECT rsl.serviceprovidercontactid FROM etown_allrequests ar "
                    "LEFT JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid "
                    "WHERE ar.allrequestid = $1 LIMIT 1)"
                )
                provider_contactname = (
                    "(SELECT up.name FROM etown_userprofile up WHERE up.contactid = "
                    "(SELECT rsl.serviceprovidercontactid FROM etown_allrequests ar "
                    "LEFT JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid "
                    "WHERE ar.allrequestid = $1 LIMIT 1) LIMIT 1)"
                )
                provider_upid = (
                    "(SELECT rsl.serviceprovideruserprofileid FROM etown_allrequests ar "
                    "LEFT JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid "
                    "WHERE ar.allrequestid = $1 LIMIT 1)"
                )
                provider_upname = (
                    "(SELECT up.name FROM etown_userprofile up WHERE up.userprofileid = "
                    "(SELECT rsl.serviceprovideruserprofileid FROM etown_allrequests ar "
                    "LEFT JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid "
                    "WHERE ar.allrequestid = $1 LIMIT 1) LIMIT 1)"
                )

                if member_completed:
                    receiver_contactid, receiver_contactname = provider_contactid, provider_contactname
                    receiver_upid, receiver_upname = provider_upid, provider_upname
                    sender_contactid, sender_contactname = member_contactid, member_contactname
                    sender_upid, sender_upname = member_upid, member_upname
                    actor_name_sql = "COALESCE((SELECT requestorname FROM etown_allrequests WHERE allrequestid = $1), '')"
                    recipient_upid_expr = (
                        "(SELECT rsl.serviceprovideruserprofileid FROM etown_requestservicelists rsl "
                        "WHERE rsl.bookingid = ar.allrequestid LIMIT 1)"
                    )
                else:
                    receiver_contactid, receiver_contactname = member_contactid, member_contactname
                    receiver_upid, receiver_upname = member_upid, member_upname
                    sender_contactid, sender_contactname = provider_contactid, provider_contactname
                    sender_upid, sender_upname = provider_upid, provider_upname
                    actor_name_sql = "COALESCE($2::text, '')"
                    recipient_upid_expr = "ar.requestoruserprofileid"

                completed_notif_query = f"""
                INSERT INTO etown_notifications (
                    notificationsid,
                    notificationcontent,
                    receivercontactid,
                    receivercontactidname,
                    receiveruserprofileid,
                    receiveruserprofileidname,
                    sendercontactid,
                    sendercontactidname,
                    senderuserprofileid,
                    senderuserprofileidname,
                    notificationstatus,
                    notificationtype,
                    createdon
                )
                VALUES (
                    gen_random_uuid()::text,
                    CONCAT(
                        {actor_name_sql},
                        ' has ',
                        $3::text,
                        ' your booking request'
                    ),
                    {receiver_contactid},
                    {receiver_contactname},
                    {receiver_upid},
                    {receiver_upname},
                    {sender_contactid},
                    {sender_contactname},
                    {sender_upid},
                    {sender_upname},
                    $3::text,
                    'Booking',
                    NOW()
                )
                RETURNING *;
                """
                completed_notif_inserted = await Database.fetch(
                    completed_notif_query,
                    request_row_id,
                    service_provider_name,
                    request_status,
                )
                logger.info("Booking completed notification inserted | request_row_id=%s", request_row_id)
                if completed_notif_inserted:
                    await insert_notification_to_dataverse(dict(completed_notif_inserted[0]))
                completed_notification_content = (
                    dict(completed_notif_inserted[0]).get("notificationcontent") or ""
                    if completed_notif_inserted else ""
                )

                completed_fcm_data_query = f"""
                SELECT
                    cd.deviceguid,
                    ar.requestoruserprofileid AS member_userprofileid,
                    ({recipient_upid_expr}) AS recipient_userprofileid,
                    up.role AS recipient_role,
                    ar.requeststatus,
                    (
                        SELECT up2.name
                        FROM etown_requestservicelists rsl2
                        JOIN etown_userprofile up2
                            ON up2.userprofileid = rsl2.serviceprovideruserprofileid
                        WHERE rsl2.bookingid = ar.allrequestid
                        LIMIT 1
                    ) AS provider_userprofile,
                    (
                        SELECT rsl2.servicename
                        FROM etown_requestservicelists rsl2
                        WHERE rsl2.bookingid = ar.allrequestid
                        LIMIT 1
                    ) AS business_name,
                    (
                        SELECT ep.serviceimageurl
                        FROM etown_provider ep
                        WHERE ep.userprofileid = (
                            SELECT rsl3.serviceprovideruserprofileid
                            FROM etown_requestservicelists rsl3
                            WHERE rsl3.bookingid = ar.allrequestid
                            LIMIT 1
                        )
                        LIMIT 1
                    ) AS serviceimage,
                    (
                        SELECT ep.creationdate
                        FROM etown_provider ep
                        WHERE ep.userprofileid = (
                            SELECT rsl3.serviceprovideruserprofileid
                            FROM etown_requestservicelists rsl3
                            WHERE rsl3.bookingid = ar.allrequestid
                            LIMIT 1
                        )
                        LIMIT 1
                    ) AS provider_createdon
                FROM etown_allrequests ar
                LEFT JOIN LATERAL (
                    SELECT COALESCE(NULLIF(cd2.device_token, ''), NULLIF(cd2.deviceguid, '')) AS deviceguid
                    FROM etown_contactdevices cd2
                    WHERE cd2.userprofileid = ({recipient_upid_expr})
                      AND COALESCE(NULLIF(cd2.device_token, ''), NULLIF(cd2.deviceguid, '')) IS NOT NULL
                    ORDER BY cd2.updatedon DESC NULLS LAST, cd2.createdon DESC NULLS LAST
                    LIMIT 1
                ) cd ON TRUE
                LEFT JOIN etown_userprofile up
                    ON up.userprofileid = ({recipient_upid_expr})
                WHERE ar.allrequestid = $1
                LIMIT 1;
                """
                completed_fcm_rows = await Database.fetch(completed_fcm_data_query, request_row_id)

                if completed_fcm_rows:
                    completed_fcm_row = dict(completed_fcm_rows[0])
                    completed_device_guid = completed_fcm_row.get("deviceguid")
                    recipient_userprofile_id = completed_fcm_row.get("recipient_userprofileid") or ""
                    member_userprofile_id = completed_fcm_row.get("member_userprofileid") or ""
                    recipient_role = completed_fcm_row.get("recipient_role") or ""
                    booking_state = completed_fcm_row.get("requeststatus") or ""
                    provider_userprofile = completed_fcm_row.get("provider_userprofile") or ""
                    business_name = completed_fcm_row.get("business_name") or ""
                    serviceimage = completed_fcm_row.get("serviceimage") or ""
                    provider_createdon = completed_fcm_row.get("provider_createdon")

                    oauth_token = data.get("Oauth")
                    if completed_device_guid:
                        from app.etown.notifications.service import GovernanceRequest, send as gov_send
                        try:
                            await gov_send(GovernanceRequest(
                                userprofileid=str(recipient_userprofile_id),
                                notification_type=NotificationType.BOOKING_REQUEST,
                                ctx={
                                    "body": completed_notification_content or "",
                                    "userprofile_id": str(recipient_userprofile_id),
                                    "role": str(recipient_role),
                                    "provider_userprofile": str(provider_userprofile),
                                    "booking_state": str(booking_state),
                                    "booking_id": str(request_row_id),
                                    "business_name": str(business_name),
                                    "serviceimage": str(serviceimage),
                                    "provider_createdon": str(provider_createdon or ""),
                                },
                                oauth_token=oauth_token,
                                feature="booking",
                                dedupe_key=f"booking-status:{request_row_id}:completed",
                            ))
                            logger.info(
                                "Booking completed push dispatched | request_row_id=%s",
                                request_row_id,
                            )
                        except Exception as fcm_err:
                            logger.error(
                                "Booking completed push failed | request_row_id=%s | error=%s",
                                request_row_id,
                                fcm_err,
                                exc_info=True,
                            )

                        async def delayed_rating_push():
                            await asyncio.sleep(600)
                            is_rating = await Database.fetchval(
                                'SELECT "isRating" FROM etown_allrequests WHERE allrequestid = $1 LIMIT 1',
                                request_row_id
                            )
                            if is_rating and str(is_rating).strip().lower() == 'true':
                                return
                            try:
                                await gov_send(GovernanceRequest(
                                    userprofileid=str(member_userprofile_id),
                                    notification_type=NotificationType.RATING_REMINDER,
                                    ctx={
                                        "service_name": str(business_name or "your recent service"),
                                        "provider_name": str(business_name or ""),
                                        "booking_id": str(request_row_id),
                                        "serviceimage": str(serviceimage or ""),
                                        "provider_createdon": str(provider_createdon or ""),
                                        "userprofileid": str(member_userprofile_id),
                                    },
                                    oauth_token=oauth_token,
                                    feature="booking",
                                    dedupe_key=f"booking-rating-prompt:{request_row_id}",
                                ))
                            except Exception as e:
                                logger.error("Delayed rating push failed | request_row_id=%s | error=%s", request_row_id, e, exc_info=True)

                        asyncio.create_task(delayed_rating_push())
                    else:
                        logger.warning(
                            "Completed FCM skipped — missing device_guid | request_row_id=%s",
                            request_row_id,
                        )
                else:
                    logger.warning(
                        "Completed FCM skipped — no requestor device data found | request_row_id=%s",
                        request_row_id,
                    )

                asyncio.create_task(
                    _send_booking_completed_whatsapp_to_member(
                        request_row_id=request_row_id,
                        provider_name=service_provider_name or "",
                        language_code=data.get("language", "en"),
                    )
                )

            elif (request_status or "").strip().lower() == "cancelled":
                member_cancelled = (role or "").strip().lower() == "member"

                member_contactid = "(SELECT requestorid FROM etown_allrequests WHERE allrequestid = $1)"
                member_contactname = "(SELECT requestorname FROM etown_allrequests WHERE allrequestid = $1)"
                member_upid = "(SELECT requestoruserprofileid FROM etown_allrequests WHERE allrequestid = $1)"
                member_upname = "(SELECT requestoruserprofileidname FROM etown_allrequests WHERE allrequestid = $1)"

                provider_contactid = (
                    "(SELECT rsl.serviceprovidercontactid FROM etown_allrequests ar "
                    "LEFT JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid "
                    "WHERE ar.allrequestid = $1 LIMIT 1)"
                )
                provider_contactname = (
                    "(SELECT up.name FROM etown_userprofile up WHERE up.contactid = "
                    "(SELECT rsl.serviceprovidercontactid FROM etown_allrequests ar "
                    "LEFT JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid "
                    "WHERE ar.allrequestid = $1 LIMIT 1) LIMIT 1)"
                )
                provider_upid = (
                    "(SELECT rsl.serviceprovideruserprofileid FROM etown_allrequests ar "
                    "LEFT JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid "
                    "WHERE ar.allrequestid = $1 LIMIT 1)"
                )
                provider_upname = (
                    "(SELECT up.name FROM etown_userprofile up WHERE up.userprofileid = "
                    "(SELECT rsl.serviceprovideruserprofileid FROM etown_allrequests ar "
                    "LEFT JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid "
                    "WHERE ar.allrequestid = $1 LIMIT 1) LIMIT 1)"
                )

                if member_cancelled:
                    receiver_contactid, receiver_contactname = provider_contactid, provider_contactname
                    receiver_upid, receiver_upname = provider_upid, provider_upname
                    sender_contactid, sender_contactname = member_contactid, member_contactname
                    sender_upid, sender_upname = member_upid, member_upname
                    actor_name_sql = "COALESCE((SELECT requestorname FROM etown_allrequests WHERE allrequestid = $1), '')"
                    recipient_upid_expr = (
                        "(SELECT rsl.serviceprovideruserprofileid FROM etown_requestservicelists rsl "
                        "WHERE rsl.bookingid = ar.allrequestid LIMIT 1)"
                    )
                    cancelled_notif_params = (request_row_id, request_status)
                else:
                    receiver_contactid, receiver_contactname = member_contactid, member_contactname
                    receiver_upid, receiver_upname = member_upid, member_upname
                    sender_contactid, sender_contactname = provider_contactid, provider_contactname
                    sender_upid, sender_upname = provider_upid, provider_upname
                    actor_name_sql = "COALESCE($3::text, '')"
                    recipient_upid_expr = "ar.requestoruserprofileid"
                    cancelled_notif_params = (request_row_id, request_status, service_provider_name)

                cancelled_notif_query = f"""
                INSERT INTO etown_notifications (
                    notificationsid,
                    notificationcontent,
                    sendercontactid,
                    sendercontactidname,
                    senderuserprofileid,
                    senderuserprofileidname,
                    receivercontactid,
                    receivercontactidname,
                    receiveruserprofileid,
                    receiveruserprofileidname,
                    notificationstatus,
                    notificationtype,
                    createdon
                )
                VALUES (
                    gen_random_uuid()::text,
                    CONCAT(
                        {actor_name_sql},
                        ' has ',
                        $2::text,
                        ' your booking request'
                    ),
                    {sender_contactid},
                    {sender_contactname},
                    {sender_upid},
                    {sender_upname},
                    {receiver_contactid},
                    {receiver_contactname},
                    {receiver_upid},
                    {receiver_upname},
                    $2::text,
                    'Booking',
                    NOW()
                )
                RETURNING *;
                """
                cancelled_notif_inserted = await Database.fetch(
                    cancelled_notif_query,
                    *cancelled_notif_params,
                )
                logger.info("Booking cancelled notification inserted | request_row_id=%s", request_row_id)
                if cancelled_notif_inserted:
                    await insert_notification_to_dataverse(dict(cancelled_notif_inserted[0]))
                cancelled_notification_content = (
                    dict(cancelled_notif_inserted[0]).get("notificationcontent") or ""
                    if cancelled_notif_inserted else ""
                )

                cancelled_fcm_data_query = f"""
                SELECT
                    cd.deviceguid,
                    ({recipient_upid_expr}) AS recipient_userprofileid,
                    up.role AS recipient_role
                FROM etown_allrequests ar
                LEFT JOIN LATERAL (
                    SELECT COALESCE(NULLIF(cd2.device_token, ''), NULLIF(cd2.deviceguid, '')) AS deviceguid
                    FROM etown_contactdevices cd2
                    WHERE cd2.userprofileid = ({recipient_upid_expr})
                      AND COALESCE(NULLIF(cd2.device_token, ''), NULLIF(cd2.deviceguid, '')) IS NOT NULL
                    ORDER BY cd2.updatedon DESC NULLS LAST, cd2.createdon DESC NULLS LAST
                    LIMIT 1
                ) cd ON TRUE
                LEFT JOIN etown_userprofile up
                    ON up.userprofileid = ({recipient_upid_expr})
                WHERE ar.allrequestid = $1
                LIMIT 1;
                """
                cancelled_fcm_rows = await Database.fetch(
                    cancelled_fcm_data_query,
                    request_row_id,
                )

                if cancelled_fcm_rows:
                    cancelled_fcm_row = dict(cancelled_fcm_rows[0])
                    cancelled_device_guid = cancelled_fcm_row.get("deviceguid")
                    recipient_userprofile_id = cancelled_fcm_row.get("recipient_userprofileid") or ""
                    recipient_role = cancelled_fcm_row.get("recipient_role") or ""

                    oauth_token = data.get("Oauth")
                    if cancelled_device_guid:
                        from app.etown.notifications.service import GovernanceRequest, send as gov_send
                        try:
                            await gov_send(GovernanceRequest(
                                userprofileid=str(recipient_userprofile_id),
                                notification_type=NotificationType.BOOKING_REQUEST,
                                ctx={
                                    "body": cancelled_notification_content or "",
                                    "userprofile_id": str(recipient_userprofile_id),
                                    "role": str(recipient_role),
                                    "booking_state": str(request_status or ""),
                                    "booking_id": str(request_row_id),
                                },
                                oauth_token=oauth_token,
                                feature="booking",
                                dedupe_key=f"booking-status:{request_row_id}:cancelled",
                            ))
                            logger.info(
                                "Booking cancelled push dispatched | request_row_id=%s | member_cancelled=%s",
                                request_row_id,
                                member_cancelled,
                            )
                        except Exception as fcm_err:
                            logger.error(
                                "Booking cancelled push failed | request_row_id=%s | error=%s",
                                request_row_id,
                                fcm_err,
                                exc_info=True,
                            )
                    else:
                        logger.warning(
                            "Cancelled FCM skipped — missing device_guid | request_row_id=%s",
                            request_row_id,
                        )
                else:
                    logger.warning(
                        "Cancelled FCM skipped — no recipient device data found | request_row_id=%s | member_cancelled=%s",
                        request_row_id,
                        member_cancelled,
                    )

            else:
                pass

            response_query = """
            SELECT
                to_jsonb(ar) || COALESCE(to_jsonb(rsl), '{}'::jsonb) AS item
            FROM etown_allrequests ar
            LEFT JOIN etown_requestservicelists rsl
                ON rsl.bookingid = ar.allrequestid
            WHERE ar.allrequestid = $1;
            """
            response_rows = await Database.fetch(response_query, request_row_id)
            response_data = []
            for row in response_rows:
                item = dict(row).get("item")
                if isinstance(item, dict):
                    for ts_key in ("bookingacceptedtime", "bookingcompletedtime", "bookingcancelledtime"):
                        ts_value = item.get(ts_key)
                        if isinstance(ts_value, str):
                            try:
                                ts_value = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
                            except ValueError:
                                logger.debug("booking: response row timestamp %r is not ISO; storing null", ts_value)
                                ts_value = None
                        if isinstance(ts_value, datetime):
                            item[ts_key] = ts_value + timedelta(hours=5, minutes=30)
                response_data.append(item)

            try:
                updated_ar_rows = await Database.fetch(
                    "SELECT * FROM etown_allrequests WHERE allrequestid = $1 LIMIT 1",
                    request_row_id,
                )
                if updated_ar_rows:
                    await update_allrequest_to_dataverse(dict(updated_ar_rows[0]))
            except Exception as sync_exc:
                logger.error(
                    "Dataverse allrequest sync failed | request_row_id=%s | error=%s",
                    request_row_id,
                    sync_exc,
                    exc_info=True,
                )

            return {
                "status": "success",
                "message": "Booking updated successfully",
                "data": response_data,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Booking update failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to update booking")


class AllRequestsListService:
    """Service class for the combined member/provider paginated booking list."""

    @staticmethod
    @with_retry()
    async def get_all_requests_list(
        contactid: str,
        userprofileid: str,
        page_number: int = 0,
        page_size: int = 10,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        date: Optional[str] = None,
        request_id: Optional[str] = None,
        allrequest_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return a page of a member's or provider's bookings with service lines and total_count, optionally filtered.

        Args:
            contactid: Contact ID of the requesting user.
            userprofileid: User profile ID of the requesting user.
            page_number: Zero-based page index.
            page_size: Number of records per page.
            start_date: Optional YYYY-MM-DD lower bound on createdon.
            end_date: Optional YYYY-MM-DD upper bound on createdon.
            start_time: Optional timestamp lower bound on createdon.
            end_time: Optional timestamp upper bound on createdon.
            date: Optional YYYY-MM-DD exact createdon date.
            request_id: Optional booking tracking ID to match.
            allrequest_id: Optional allrequestid to match.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        from app.etown.provider.provider_utils import (
            _normalize_instructions,
            hydrate_instruction_senders,
        )

        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        logger.info(
            "get_all_requests_list | contactid=%s userprofileid=%s page=%d size=%d",
            contactid, userprofileid, page_number, page_size,
        )

        params: List[Any] = [contactid, userprofileid]
        filter_clauses: List[str] = []
        next_param = 3

        if start_date:
            filter_clauses.append(f"ar.createdon::date >= ${next_param}")
            params.append(datetime.strptime(start_date, "%Y-%m-%d").date())
            next_param += 1
        if end_date:
            filter_clauses.append(f"ar.createdon::date <= ${next_param}")
            params.append(datetime.strptime(end_date, "%Y-%m-%d").date())
            next_param += 1
        if start_time:
            filter_clauses.append(f"ar.createdon >= ${next_param}")
            params.append(_to_optional_datetime(start_time))
            next_param += 1
        if end_time:
            filter_clauses.append(f"ar.createdon <= ${next_param}")
            params.append(_to_optional_datetime(end_time))
            next_param += 1
        if date:
            filter_clauses.append(f"ar.createdon::date = ${next_param}")
            params.append(datetime.strptime(date, "%Y-%m-%d").date())
            next_param += 1
        if request_id:
            filter_clauses.append(f"ar.requestid = ${next_param}")
            params.append(request_id)
            next_param += 1
        if allrequest_id:
            filter_clauses.append(f"ar.allrequestid = ${next_param}")
            params.append(str(allrequest_id))
            next_param += 1

        optional_fragment = (
            "\n    AND " + "\n    AND ".join(filter_clauses)
        ) if filter_clauses else ""

        limit_param = next_param
        offset_param = next_param + 1
        params.append(page_size)
        params.append(page_number * page_size)

        BOOKING_QUERY = f"""
        SELECT
            ar.requestorrole                                 AS requestor_role,
            ar.requestorrolename                             AS requestor_rolename,
            rr.userprofileid                                 AS requestor_userprofileid,
            COALESCE(ar.allrequestid, rsl.bookingid)         AS allrequestid,
            ar.requestid,
            ar.requestorname,
            ar.requestorid,
            ar.requestoruserprofileid,
            ar.requestoruserprofileidname,
            etown_deleted_flag(ar.requestorid)               AS requestor_deleted,
            etown_deactivated_flag(ar.requestorid)           AS requestor_deactivated,
            ar.requestoraddress,
            ar.requestorlatitude,
            ar.requestorlongitude,
            ar.requeststatus,
            ar.instructions,
            COALESCE(ar.createdon, rsl.createdon)            AS createdon,
            ar.cancelreason,
            ar.bookingacceptedtime,
            ar.bookingcompletedtime,
            rc.profileimage,
            rr.mobilenumber                                  AS requestor_mobilenumber,
            rp_prov.businessname,
            rp_prov.publicsearchprovider,
            rp_prov.providertype,
            rp_prov.disabled                                 AS provider_disabled,
            rc.profileimage                                  AS etown_requestorprofileimageurl,
            CASE
                WHEN ar.requestorid = $1 AND ar.requestoruserprofileid = $2
                    THEN CASE WHEN rr.role = 1 THEN rp_req.serviceimageurl ELSE rc.profileimage END
                ELSE rp_prov.serviceimageurl
            END                                              AS etown_ownprofileimageurl,
            COUNT(*) OVER ()                                 AS total_count
        FROM etown_requestservicelists rsl
        LEFT JOIN etown_allrequests ar
            ON ar.allrequestid = rsl.bookingid
        LEFT JOIN etown_userprofile rr
            ON ar.requestoruserprofileid = rr.userprofileid
        LEFT JOIN etown_contact rc
            ON ar.requestorid = rc.contactid
        LEFT JOIN etown_provider rp_req
            ON rr.userprofileid = rp_req.userprofileid
        LEFT JOIN etown_provider rp_prov
            ON rsl.serviceprovideruserprofileid = rp_prov.userprofileid
        WHERE
            (
                (ar.requestorid = $1 AND ar.requestoruserprofileid = $2)
                OR
                (rsl.serviceprovidercontactid = $1 AND rsl.serviceprovideruserprofileid = $2)
            )
            AND ar.requeststatus IS NOT NULL
            AND NOT etown_is_deleted(ar.requestorid)
            AND NOT etown_is_deleted(rsl.serviceproviderid){optional_fragment}
        GROUP BY
            rr.role,
            rr.userprofileid,
            rr.mobilenumber,
            rsl.bookingid,
            ar.allrequestid,
            ar.requestid,
            ar.requestorname,
            ar.requestorid,
            ar.requestoruserprofileid,
            ar.requestoruserprofileidname,
            ar.requestoraddress,
            ar.requestorlatitude,
            ar.requestorlongitude,
            ar.requeststatus,
            ar.instructions,
            ar.createdon,
            rsl.createdon,
            ar.cancelreason,
            ar.bookingacceptedtime,
            ar.bookingcompletedtime,
            rc.profileimage,
            rp_prov.businessname,
            rp_prov.publicsearchprovider,
            rp_prov.providertype,
            rp_prov.disabled,
            rp_prov.serviceimageurl,
            rp_req.serviceimageurl
        ORDER BY COALESCE(ar.createdon, rsl.createdon) DESC NULLS LAST
        LIMIT ${limit_param} OFFSET ${offset_param}
        """

        SERVICE_DETAILS_QUERY = """
            SELECT
                rsl.requestservicelistsid,
                rsl.servicename,
                rsl.bookingid,
                rsl.serviceprovidercurrentaddress,
                rsl.serviceprovidercontactid,
                rsl.serviceprovideruserprofileid,
                ar.allrequestid                              AS bookingidname,
                rsl.serviceprovideraddress,
                rsl.serviceprovidercurrentaddresslatitude,
                rsl.serviceprovidercurrentaddresslongitude,
                rsl.serviceproviderid,
                rsl.serviceproviderlatitude,
                rsl.serviceproviderlongitude,
                rsl.quantity,
                rp.serviceimageurl                           AS own_userprofileimageurl,
                rsl.name,
                etown_deleted_flag(rsl.serviceproviderid)     AS serviceprovider_deleted,
                etown_deactivated_flag(rsl.serviceproviderid) AS serviceprovider_deactivated,
                ps.servicedescription,
                rsl.serviceimageurl,
                pr_role.role                                 AS provider_role,
                pr_role.rolename                             AS provider_rolename,
                pr_role.mobilenumber                         AS provider_mobilenumber
            FROM etown_requestservicelists rsl
            LEFT JOIN etown_allrequests ar
                ON rsl.bookingid = ar.allrequestid
            LEFT JOIN etown_providerservices ps
                ON rsl.providerservices = ps.providerservicesid
            LEFT JOIN etown_userprofile pr_role
                ON rsl.serviceprovideruserprofileid = pr_role.userprofileid
            LEFT JOIN etown_provider rp
                ON rsl.serviceprovideruserprofileid = rp.userprofileid
            WHERE rsl.bookingid = $1

              AND NOT etown_is_deleted(rsl.serviceproviderid)
            ORDER BY rsl.createdon DESC
"""

        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            caller_role_row = await con.fetchrow(
                "SELECT role FROM etown_userprofile WHERE contactid = $1 AND userprofileid = $2",
                contactid,
                userprofileid,
            )
            caller_is_provider = bool(caller_role_row and caller_role_row["role"] == 1)

            booking_rows = await con.fetch(BOOKING_QUERY, *params)

            total_count = int(booking_rows[0]["total_count"]) if booking_rows else 0

            logger.info(
                "get_all_requests_list | %d rows on page (total=%d)",
                len(booking_rows), total_count,
            )

            bookings: List[Dict[str, Any]] = []

            for row in booking_rows:
                raw = dict(row)

                booking_details: Dict[str, Any] = {
                    "Etown_BookingId":                              raw.get("allrequestid"),
                    "Etown_BookingTrackId":                         raw.get("requestid"),
                    "Etown_requestorContactId":                     raw.get("requestorid"),
                    "Etown_requestorUserProfileId":                 raw.get("requestoruserprofileid"),
                    "Etown_requestorname":                          raw.get("requestorname"),
                    "Etown_requestorrole":                          int(raw["requestor_role"]) if (raw.get("requestor_role") or "").isdigit() else raw.get("requestor_role"),
                    "Etown_requestorrolename":                      raw.get("requestor_rolename"),
                    "Etown_requestoraddress":                       raw.get("requestoraddress"),
                    "Etown_requestorlatitude":                      str(raw["requestorlatitude"]) if raw.get("requestorlatitude") is not None else None,
                    "Etown_requestorlongitude":                     str(raw["requestorlongitude"]) if raw.get("requestorlongitude") is not None else None,
                    "Etown_requestorProfileImageUrl":               raw.get("etown_requestorprofileimageurl"),
                    "Etown_requeststatus":                          raw.get("requeststatus"),
                    "Etown_instructions":                           _normalize_instructions(raw.get("instructions")),
                    "Etown_bookingcreatedon":                       (lambda dt: (lambda d: f"{d.month}/{d.day}/{d.year} {int(d.strftime('%I'))}:{d.strftime('%M')} {d.strftime('%p')}")(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("createdon")),
                    "Etown_updatedcompletedtime":                   (lambda dt: (lambda d: d.strftime("%Y-%m-%d %H:%M:%S"))(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("bookingcompletedtime")),
                    "Etown_updatedacceptedtime":                    (lambda dt: (lambda d: d.strftime("%Y-%m-%d %H:%M:%S"))(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("bookingacceptedtime")),
                    "Etown_ownprofileimageurl":                     raw.get("etown_ownprofileimageurl"),
                    "Etown_cancelreason":                           raw.get("cancelreason"),
                    "Etown_publicsearchprovider":                   raw.get("publicsearchprovider"),
                    "Etown_providertype":                           raw.get("providertype"),
                    "Etown_disabled":                               raw.get("provider_disabled"),
                    "Etown_ServiceProviderBusinessname":            raw.get("businessname"),
                    "Etown_providerrole":                           None,
                    "Etown_providerrolename":                       None,
                    "Etown_mobilenumber":                           None,
                    "Etown_providercontactid":                      None,
                    "Etown_serviceproviderUserProfileId":           None,
                    "Etown_serviceprovideraddress":                 None,
                    "Etown_serviceproviderlatitude":                None,
                    "Etown_serviceproviderlongitude":               None,
                    "Etown_serviceprovidercurrentaddress":          None,
                    "Etown_serviceprovidercurrentaddresslatitude":  None,
                    "Etown_serviceprovidercurrentaddresslongitude": None,
                }

                if caller_is_provider:
                    booking_details["Etown_mobilenumber"] = raw.get("requestor_mobilenumber")

                booking_id = raw.get("allrequestid")
                svc_rows_raw = [dict(r) for r in await con.fetch(SERVICE_DETAILS_QUERY, booking_id)]

                if svc_rows_raw:
                    first_svc = svc_rows_raw[0]
                    if not caller_is_provider:
                        booking_details["Etown_mobilenumber"]                       = first_svc.get("provider_mobilenumber")
                    booking_details["Etown_providercontactid"]                      = first_svc.get("serviceprovidercontactid")
                    booking_details["Etown_serviceproviderUserProfileId"]           = first_svc.get("serviceprovideruserprofileid")
                    booking_details["Etown_providerrole"]                           = first_svc.get("provider_role")
                    booking_details["Etown_providerrolename"]                       = first_svc.get("provider_rolename")
                    booking_details["Etown_serviceprovideraddress"]                 = first_svc.get("serviceprovideraddress")
                    booking_details["Etown_serviceproviderlatitude"]                = str(first_svc["serviceproviderlatitude"]) if first_svc.get("serviceproviderlatitude") is not None else None
                    booking_details["Etown_serviceproviderlongitude"]               = str(first_svc["serviceproviderlongitude"]) if first_svc.get("serviceproviderlongitude") is not None else None
                    booking_details["Etown_serviceprovidercurrentaddress"]          = first_svc.get("serviceprovidercurrentaddress")
                    booking_details["Etown_serviceprovidercurrentaddresslatitude"]  = str(first_svc["serviceprovidercurrentaddresslatitude"]) if first_svc.get("serviceprovidercurrentaddresslatitude") is not None else None
                    booking_details["Etown_serviceprovidercurrentaddresslongitude"] = str(first_svc["serviceprovidercurrentaddresslongitude"]) if first_svc.get("serviceprovidercurrentaddresslongitude") is not None else None

                service_details = [
                    {
                        "Etown_servicedescription": sr.get("name"),
                        "Etown_quantity":           sr.get("quantity"),
                        "Etown_serviceimageurl":    sr.get("serviceimageurl"),
                        "Etown_bookingId":          str(sr["bookingid"]) if sr.get("bookingid") is not None else None,
                    }
                    for sr in svc_rows_raw
                ]

                bookings.append({
                    "BookingDetails": booking_details,
                    "ServiceDetails": service_details,
                })

            await hydrate_instruction_senders(
                [b["BookingDetails"].get("Etown_instructions") for b in bookings], con
            )

        badge_map = await resolve_provider_badges(
            b["BookingDetails"].get("Etown_serviceproviderUserProfileId") for b in bookings
        )
        for booking in bookings:
            details = booking["BookingDetails"]
            badge = badge_map.get(details.get("Etown_serviceproviderUserProfileId")) or {}
            details["Etown_providertype"] = premium_display(
                details.get("Etown_providertype"), badge.get("premium_label")
            )

        return {"bookings": bookings, "total_count": total_count}


class FrequentlyVisitedService:
    """Service class for the member's frequently-visited providers list."""

    @staticmethod
    @with_retry()
    async def get_frequently_visited(
        requestor_id: str,
        requestor_userprofileid: str,
    ) -> List[Dict[str, Any]]:
        """Return the member's requested provider-services with Etown_visitcount, ordered by count then recency.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        logger.info(
            "get_frequently_visited | requestor_id=%s userprofileid=%s",
            requestor_id, requestor_userprofileid,
        )

        FREQUENTLY_VISITED_QUERY = """
        SELECT
            c.latitude                 AS etown_contactlatitude,
            c.longitude                AS etown_contactlongitude,
            c.connectycubeid           AS etown_connectycubeid,
            c.address                  AS etown_contactaddress,
            ps.contactid               AS etown_contactid,
            c.firstname                AS etown_firstname,
            etown_deleted_flag(ps.contactid)      AS etown_contact_deleted,
            etown_deactivated_flag(ps.contactid)  AS etown_contact_deactivated,
            c.createdon                AS etown_contactcreatedon,
            c.mobilephone              AS etown_mobilenumber,
            ps.businessname            AS etown_businessname,
            ps.address                 AS etown_provideraddress,
            ps.latitude                AS etown_providerlatitude,
            ps.longitude               AS etown_providerlongitude,
            pr.businessname            AS etown_providername,
            etown_deleted_flag(ps.providerid)     AS etown_provider_deleted,
            etown_deactivated_flag(ps.providerid) AS etown_provider_deactivated,
            ps.contactid               AS etown_providercontact,
            ps.providerid              AS etown_providerid,
            pr.rolename                AS etown_rolename,
            pr.role                    AS etown_role,
            pr.creationdate            AS etown_providercreatedon,
            ps.yearsinbusiness         AS etown_yearinbusiness,
            ps.email                   AS etown_provideremail,
            ps.userprofileid           AS etown_userprofileid,
            ps.serviceimageurl         AS etown_imageurl,
            ps.publicsearchprovider    AS etown_publicsearchprovider,
            ps.providertype            AS etown_providertype,
            ps.address                 AS etown_address,
            ps.city                    AS etown_city,
            ps.statecode               AS etown_statecode,
            COUNT(*)                   AS visitcount,
            MAX(ar.createdon)          AS lastvisitedon
        FROM etown_allrequests ar
        INNER JOIN etown_requestservicelists rsl
            ON rsl.bookingid = ar.allrequestid
        INNER JOIN etown_providerservices ps
            ON ps.providerservicesid = rsl.providerservices
        INNER JOIN etown_contact c
            ON c.contactid = rsl.serviceprovidercontactid
        LEFT JOIN etown_provider pr
            ON pr.providerid = ps.providerid
        WHERE ar.requestorid = $1 AND ar.requestoruserprofileid = $2

          AND NOT etown_is_deleted(ps.contactid)
          AND NOT etown_is_deleted(ps.providerid)
        GROUP BY
            ps.providerservicesid,
            c.latitude, c.longitude, c.connectycubeid, c.address, c.firstname,
            c.createdon, c.mobilephone,
            ps.contactid, ps.businessname, ps.address, ps.latitude, ps.longitude,
            ps.providerid, ps.yearsinbusiness, ps.email, ps.userprofileid,
            ps.serviceimageurl, ps.publicsearchprovider, ps.providertype, ps.city, ps.statecode,
            pr.businessname, pr.rolename, pr.role, pr.creationdate
        ORDER BY visitcount DESC, lastvisitedon DESC NULLS LAST
"""

        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            rows = await con.fetch(FREQUENTLY_VISITED_QUERY, requestor_id, requestor_userprofileid)

        logger.info("get_frequently_visited | %d provider-services returned", len(rows))

        items: List[Dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            items.append({
                "Etown_contactlatitude":      str(raw["etown_contactlatitude"]) if raw.get("etown_contactlatitude") is not None else None,
                "Etown_contactlongitude":     str(raw["etown_contactlongitude"]) if raw.get("etown_contactlongitude") is not None else None,
                "Etown_connectycubeid":       raw.get("etown_connectycubeid"),
                "Etown_contactaddress":       raw.get("etown_contactaddress"),
                "Etown_contactid":            raw.get("etown_contactid"),
                "Etown_firstname":            raw.get("etown_firstname"),
                "Etown_contactcreatedon":     raw["etown_contactcreatedon"].isoformat() if raw.get("etown_contactcreatedon") else None,
                "Etown_mobilenumber":         raw.get("etown_mobilenumber"),
                "Etown_businessname":         raw.get("etown_businessname"),
                "Etown_provideraddress":      raw.get("etown_provideraddress"),
                "Etown_providerlatitude":     str(raw["etown_providerlatitude"]) if raw.get("etown_providerlatitude") is not None else None,
                "Etown_providerlongitude":    str(raw["etown_providerlongitude"]) if raw.get("etown_providerlongitude") is not None else None,
                "Etown_providername":         raw.get("etown_providername"),
                "Etown_providercontact":      raw.get("etown_providercontact"),
                "Etown_providerid":           raw.get("etown_providerid"),
                "Etown_rolename":             raw.get("etown_rolename"),
                "Etown_role":                 raw.get("etown_role"),
                "Etown_providercreatedon":    raw["etown_providercreatedon"].isoformat() if raw.get("etown_providercreatedon") else None,
                "Etown_yearinbusiness":       raw.get("etown_yearinbusiness"),
                "Etown_provideremail":        raw.get("etown_provideremail"),
                "Etown_userprofileid":        raw.get("etown_userprofileid"),
                "Etown_imageurl":             raw.get("etown_imageurl"),
                "Etown_publicsearchprovider": raw.get("etown_publicsearchprovider"),
                "Etown_providertype":         raw.get("etown_providertype"),
                "Etown_address":              raw.get("etown_address"),
                "Etown_city":                 raw.get("etown_city"),
                "Etown_statecode":            raw.get("etown_statecode"),
                "Etown_visitcount":           int(raw["visitcount"]) if raw.get("visitcount") is not None else 0,
            })

        badge_map = await resolve_provider_badges(
            item["Etown_userprofileid"] for item in items
        )
        for item in items:
            badge = badge_map.get(item["Etown_userprofileid"]) or {}
            item["Etown_providertype"] = premium_display(
                item["Etown_providertype"], badge.get("premium_label")
            )

        return items
