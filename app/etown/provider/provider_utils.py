"""Provider Utilities - Postgres business logic for provider onboarding, editing, verification and analytics.

Domain layer behind /api/provider: onboards providers through form, unverified and web-insert paths, edits profiles and services, promotes and approves providers, lists booking requests and ratings, builds analytics and viewer lists, and runs the background AI, geocoding, embedding and Dataverse enhancement after a fast insert.

Features:
    - Provider Onboarding: Transactional multi-table inserts for form, unverified and web-insert paths
    - Provider Editing: Business/address/contact edits, contact merge, and profile edits to both service tables
    - Verification Promotion: Atomic unverified->verified flip across every provider-side table
    - Review Approval: Sets etown_provider.approve and lists providers behind a mobile number
    - Background Enhancement: AI copy, geocoding, blob upload and embeddings, then Dataverse sync
    - Generated Profile Image: a provider inserted without an image gets an AI picture of its trade, behind PROVIDER_IMAGE_AUTOGENERATE
    - Service Generation Preview: AI services and categories for an onboarding, returned without storing
    - Booking Request Lists & Ratings: Pending/completed provider bookings and rating submission
    - Analytics: Date-range offerview/mycircle/offer/booking summaries bucketed daily, weekly or monthly
    - Profile Viewers: Paginated list of users who viewed a provider's profile
    - Instruction Hydration: Resolves stored booking-instruction sender ids to live role/name/picture
    - WhatsApp Signals: Tracks WhatsApp reachability and sends deduplicated unverified-provider nudges
    - FOS Onboarding Email: Welcome email for a field-captured provider's new profile
    - AI Onboarding: Gemini reads facts from a capture image and/or prompt and writes description and 3-4 services
    - AI Onboarding Existing Check: A mobile that already has providers returns them beside the generated listing
    - AI Onboarding Merge: A picked provider keeps its values, fills gaps and adds dissimilar services from the listing
    - Merge Confirmation: A differing business name or distant address holds the merge until the agent confirms
    - Own Provider Guard: A mobile whose provider belongs to the caller's contactid is refused, never shown or merged
    - Onboard As New: "onboard as new provider" returns the saved generated listing, setting existing providers aside
    - Confirmation Reading: Gemini reads the agent's choices and contact details from the reply; its mobile becomes alternate
    - Merge Description Rewrite: Gemini rewrites business and service descriptions to fit a confirmed business name
    - AI Onboarding Bubble: Gemini applies a follow-up prompt and/or image to onboarding details, per stored conversation
    - Business Name Confirmation: A bubble name that does not fit the services waits for the agent's yes
    - Business Rename: A bubble rename replaces the old name in the business and service descriptions
    - Bubble Mobiles: The main mobile is fixed; a new alternate mobile already on a provider is refused
    - Chat Replies: Greetings and "what can you do" questions get a chatbot answer on /ai_onboarding and /bubble

Classes:
    ProviderService(): Provider onboarding, editing, verification, deletion, and background enhancement
    ProviderRequestListService(): Booking request lists (pending and completed) for a provider
    RatingService(): Provider rating submission and aggregate rating maintenance

Functions:
    summarise_provider_status(): Overall status of a mobile's providers: verified, FOS, unverified or available
    _iso_or_none_value(): Render a timestamp as ISO 8601, passing strings straight through
    _coerce_json_list(): Coerce a jsonb column value (str/list/other) into a Python list
    _parse_instruction_date(): Parse a stored instruction date (ISO or legacy formats) into a datetime
    _normalize_instructions(): Normalize the stored instructions column into chronologically-sorted entries
    _instruction_sender_display(): Pick the display name/image/role label for one profile row, by role
    hydrate_instruction_senders(): Batch-resolve instruction sender role/name/profileimage from the profile tables
    convert_state_name_to_code(): Convert an Indian state name to its 2-letter state code
    _normalize_biz_category(): Normalize businesscategory to 'product' / 'service' / 'product,service'
    _attach_provider_badges(): Fill Etown_providertype and Etown_badge_label on each booking in place
    _add_months(): Shift a date forward by N calendar months, clamped to month end
    fetch_date_range_summary(): Fetch offerview/mycircle/offer/booking counts and bucketed insights for a date range
    fetch_profile_viewers(): Fetch one page of the users who viewed a provider's profile, newest first, with the total
    extract_onboard_details(): Extract structured provider + service details from a business image via Gemini Vision
    _tidy_ai_onboarding_listing(): Strip "onboard" from the name, normalise quantities and derive business_category
    _onboarding_city_state(): Pick the city and two-letter statecode from Google address components
    _find_onboarding_place(): Find an address's coordinates with Google Places Text Search in India
    _geocode_onboarding_address(): Geocode an onboarding address within India to coordinates, city and statecode
    _clean_business_image(): Strip business_image text, treating a missing value or 'null' as none
    _store_business_image(): Validate a base64 business image and upload it to Azure Blob
    _generate_ai_onboarding_listing(): Read facts from an image and/or notes and generate 3-4 services with Gemini
    analyse_ai_onboarding(): Merge a picked provider, onboard as new on request, else generate beside existing providers
    _existing_ai_onboarding_response(): Build an /ai_onboarding response with a mobile's existing providers and the listing
    _is_own_provider(): Say whether any of a mobile's providers belongs to the caller's contact
    _names_similar(): Say whether two business or service names match, ignoring filler words
    _ask_gemini_json(): Send a text prompt to Gemini Flash and return its reply's JSON object
    _merge_ai_onboarding_existing(): Merge a listing into a picked provider once a differing name or address is confirmed, up to five services
    apply_ai_onboarding_bubble(): Apply a follow-up prompt and/or new image to AI onboarding provider and service details
    record_ai_onboarding_bubble(): Log one /bubble turn as timed user and assistant rows in etown_ai_onboarding_bubble
    record_ai_onboarding(): Log one /ai_onboarding call as timed user and assistant rows in etown_ai_onboarding
    update_provider_whatsapp_reachability(): Flip etown_provider.whatsapp from an observed WhatsApp delivery signal
    is_provider_mobile_known_off_whatsapp(): True when the number's providers are marked not on WhatsApp
    send_unverified_provider_nudge(): Send a deduplicated WhatsApp nudge to an unverified provider on a member's behalf
    send_fos_business_onboarding_email(): Email a field-captured provider the welcome note for its new profile
    build_provider_image_prompt(): Build the fallback image-model prompt for a still life of a business's trade
    build_provider_image_brief(): Build the business details gpt-4o-mini turns into a profile image prompt
    render_provider_image(): Write the scene with gpt-4o-mini, generate a watermarked JPG on the local image model and upload it under provider_images/
    store_provider_profile_image(): Store an image on a provider and its blank service rows, then mirror it to Dataverse
    generate_provider_profile_image(): Generate and store a trade picture for a provider with no profile image
    ProviderService.enhance_provider_data(): AI-generate description, catalogue, and category/general-category for a business
    ProviderService.check_phone_exists(): Check if a phone is a live contact's primary or alternate number
    ProviderService.promote_provider_to_verified(): Atomically flip a contact's provider records to verified, healing partial promotions
    ProviderService.onboard_provider_in_db(): Register a new provider with transactional multi-table inserts
    ProviderService.onboard_unverified_provider_in_db(): Register a new non-verified provider with the same inserts
    ProviderService.fetch_providers_by_mobile(): List every live provider behind a mobile number, classified, with services
    ProviderService.set_provider_approval_by_providerid(): Set etown_provider.approve on one provider
    ProviderService.update_provider_onboard_in_db(): Update an existing provider in edit mode, including contact merge and verification
    ProviderService.update_unverified_provider_in_db(): Update the non-verified provider on a mobile number, leaving it non-verified
    ProviderService.insert_web_provider_to_db(): Idempotently upsert a Google/RapidAPI-sourced provider using deterministic UUID5 ids
    ProviderService.upsert_provider_details(): Upsert extended Google Places details (ratings, hours, reviews, photos)
    ProviderService.run_background_enhancements(): Background AI enhancement, geocoding, blob upload, embedding and Dataverse sync after a fast insert
    ProviderService.validate_phone_format(): Validate a phone number is 8-15 digits
    ProviderService.get_unrated_provider_list(): Fetch providers with a completed but unrated booking for a member
    ProviderService.fetch_provider_identifiers(): Resolve a providerid to its owning contactid + userprofileid
    ProviderService.delete_provider_from_db(): Delete a provider and all associated records in FK-safe order
    ProviderService.edit_provider_profile_workflow(): Update a provider profile and mirror changes onto both service tables
    ProviderService.reassign_town_after_address_change(): Background task re-deriving and moving the provider's town link after an address edit
    ProviderRequestListService.get_provider_request_list(): Retrieve booking requests where the caller is the provider, with service line items
    ProviderRequestListService.get_provider_completed_list(): Retrieve completed booking requests where the caller is the provider
    RatingService.add_rating(): Submit/update a booking rating and roll it into the provider's aggregate rating stats

Dependencies:
    - app.db.client: Database connection pool access for EtownDB operations, with_retry for transient failures
    - app.schemas.api: Pydantic models for onboarding-extraction request/response validation
    - app.core.logging / app.core.config: Centralized logging and application settings
    - app.etown.services.services_utils: AI description/category/offerings generation and Google reverse-geocoding logic
    - app.utils.embeddings: Vector embedding generation for search
    - app.etown.provider.provider_syncing: Dataverse sync calls after DB writes
    - app.etown.mytown.mytown_utils: Provider-town link assignment
    - app.etown.feed.feed_utils: Propagates a business-name change onto etown_feed
    - app.etown.plans.plans_utils: PlanLimitError, provider_onboard cap, and badge resolution for provider payloads
    - app.agent_infrastructure.infrastructure.llm_clients: Gemini Vision model for image-based onboarding extraction
    - app.agent_infrastructure.prompt_templates.templates: onboarding extraction prompts and the agent-notes wrapper
    - app.etown.offers.offerings_utils: Catalogue/offerings generation and AI-response JSON extraction
    - app.utils.blob_storage: Azure Blob upload and download helpers for logos/images
    - difflib: business-name similarity when merging into an existing provider
    - geopy: distance between an existing and a generated address when merging
    - app.utils.http_client: Shared async HTTP client for Google Places photo fetches
    - uuid / time: Primary-key generation and DB acquire/query performance logging
    - app.utils.phone: Indian mobile number normalisation
    - scripts/migrations/2026_09_ai_onboarding.sql: creates etown_ai_onboarding
    - scripts/migrations/2026_09_ai_onboarding_roles.sql: splits etown_ai_onboarding into timed user/assistant rows
    - scripts/migrations/2026_09_ai_onboarding_bubble.sql: creates etown_ai_onboarding_bubble
    - app.etown.auth.auth_utils: MOBILE_HELD_BY_OTHER_SQL mobile-ownership check
    - app.etown.plans.plans_syncing: Mirrors an inherited provider plan to Dataverse
    - app.etown.mytown.mytown_syncing: Mirrors provider-town links to Dataverse
    - app.utils.whatsapp: Unverified-provider enquiry WhatsApp notification
    - app.agent_infrastructure.tools.tools_utils.search_provider: is_provider_verified()
    - app.services.email_service / email_templates: FOS onboarding email send and rendering
    - app.agent_infrastructure.tools.tools_utils.generate_image: handle_generate_image for generated profile images

Updates:
    21-Apr-2026: Initial creation with provider onboarding operations
    27-May-2026: Added fetch_date_range_summary() for offerview/mycircle/booking counts within a date range
    15-Jun-2026: Added ONBOARD_VISION_PROMPT and extract_onboard_details() for image-based onboarding extraction.
    17-Jun-2026: Added RatingService for provider rating submission.
    25-Jun-2026: Added send_unverified_provider_nudge() with per-member-per-provider deduplication.
    02-Jul-2026: run_background_enhancements() generates offerings for Public Insert providers.
    03-Jul-2026: Added edit_provider_profile_workflow(), updating the provider and both service tables.
    06-Jul-2026: Added handle_provider_town_assignment; town profiles are created directly in Postgres.
    10-Jul-2026: Unverified nudge resolves by phone; added update_provider_whatsapp_reachability().
    13-Jul-2026: Request lists report the stored per-booking requestor role and caller-aware images.
    27-Jul-2026: Added promote_provider_to_verified().
    06-Aug-2026: Added update_provider_onboard_in_db() for POST /provider/onboard edit mode.
    07-Aug-2026: onboard_provider_in_db() only 409s when a member contact holds the number.
    10-Aug-2026: Added fetch_provider_identifiers(); date-range summary adds totals and bucketed insights.
    11-Aug-2026: run_background_enhancements() generates offerings for every onboarding.
    18-Aug-2026: Added hydrate_instruction_senders(); per-contact onboard cap; request lists carry badge_label.
    19-Aug-2026: Onboard cap on the written contact; instructions oldest-first; background_image on profile edit.
    20-Aug-2026: Verified markers report the badge label; business-name changes reach etown_feed.
    21-Aug-2026: _normalize_instructions() emits a document's filename and a video's thumbnail.
    24-Aug-2026: New provider rows inherit the contact's active subscription, mirrored to Dataverse.
    26-Aug-2026: Added fetch_profile_viewers() for GET /provider/profile_viewers; logged previously-silent exception handlers.
    01-Sep-2026: Provider delete keeps etown_contact; onboarding seeds form services; parallel town assignment.
    03-Sep-2026: A Public Insert resolves its town against existing towns and never creates one.
    08-Sep-2026: Insert paths return the inherited plan_code, mirrored as ets_plancode.
    15-Sep-2026: Added unverified-provider onboarding, lookup by mobile and approval; FOS providers stay non-verified.
    16-Sep-2026: Added fetch_providers_by_mobile(), FOS onboarding email and unverified-provider edit.
    17-Sep-2026: Unverified onboarding drops refusals; edits park in review; approval by providerid.
    18-Sep-2026: Onboard paths carry an alternate mobile; approval returns needs_nonverification.
    21-Sep-2026: fetch_providers_by_mobile() drops providers held by an unapproved review row.
    22-Sep-2026: Reverted the 21-Sep review exclusion in fetch_providers_by_mobile().
    23-Sep-2026: Completed list returns Etown_businesscreatedon; unverified onboarding takes source.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    25-Sep-2026: Added is_provider_mobile_known_off_whatsapp() so onboarding goes straight to SMS.
    28-Sep-2026: Added analyse_ai_onboarding() with a business-name response without "onboard" and record_ai_onboarding(); extract_onboard_details() takes a prompt, reads list replies.
    29-Sep-2026: Added apply_ai_onboarding_bubble() with conversation history and name confirmation, and record_ai_onboarding_bubble(); image handling moved to _store_business_image(); record_ai_onboarding() writes timed user/assistant rows with conversation and caller ids; analyse_ai_onboarding() returns existing providers on the mobile and asks for a missing mobile; added summarise_provider_status(); added _merge_ai_onboarding_existing() reusing the earlier turn's shown listing; AI onboarding and bubble geocode the address to latitude/longitude; /ai_onboarding mobiles, coordinates and category moved into Provider_Details; AI onboarding and bubble link a photo to a service's Service_Image_Url; bubble uses the /ai_onboarding Provider_Details fields; analyse_ai_onboarding() always generates beside existing providers; merge fills gaps and dedupes similar services via _names_similar(); merge waits for a confirmed business name or address on a mismatch and rewrites descriptions for a confirmed name; added _ask_gemini_json() reading confirmation replies, whose mobile becomes the alternate; "onboard as new provider" returns the saved generated listing without existing providers; added _is_own_provider() refusing the caller's own provider; AI onboarding and bubble replies reworded to friendly, name-specific text; onboard service rows take the provider's contact and profile names; background enhancement fills area and state on every service row; analyse_ai_onboarding() reads every earlier prompt in the conversation; bubble drops business_image_url, reading the earlier photo from stored turns.
    30-Sep-2026: analyse_ai_onboarding() and apply_ai_onboarding_bubble() answer chat messages like a chatbot instead of asking for details; analyse_ai_onboarding() refuses gibberish and non-business photos and says what it prepared before asking for missing details; analyse_ai_onboarding() reads earlier turns as a transcript and names an invalid mobile or business name; /ai_onboarding sets confirmation_required whenever response asks something; unverified onboarding stores interested and mirrors it to Dataverse; bubble keeps interested; a merge caps services at five; the transcript marks which photo is attached; bubble renames the business in descriptions; bubble keeps the main mobile and refuses a used alternate mobile; invalid-mobile replies drop the starting-digit hint; analyse_ai_onboarding() skips the existing-provider lookup when any conversation prompt asks for a new business; AI onboarding and bubble carry the provider's Service_Type, bubble keeps it unless the prompt changes it.
    01-Oct-2026: Unverified onboarding stores the capturing agent's marketing_mobilenumber.
    05-Oct-2026: _store_business_image() downscales photos past 2048 px via _shrink_business_image() before the 10 MB check and blob upload.
    08-Oct-2026: Removed LangSmith tracing.
    21-Oct-2026: promote_provider_to_verified() skips CSR and STAFF captures as well as FOS.
    22-Oct-2026: Added ProviderService.generate_onboard_services() returning /onboard's AI services without storing them.
    23-Oct-2026: _geocode_onboarding_address() restricts Geocoding to India, falls back to Places Text Search on a partial or state-level match, and returns city and statecode, which /ai_onboarding and /bubble now fill.
    26-Oct-2026: Added generate_provider_profile_image() with build_provider_image_prompt(), build_provider_image_brief(), render_provider_image() and store_provider_profile_image(); background enhancement generates a profile image for providers inserted without one (verified onboard, unverified onboard and Public Insert), gpt-4o-mini writing the scene from business name, description, category and services.
"""

from typing import Optional, Dict, Any, List, NamedTuple, Tuple
import asyncio
import asyncpg
import base64
import difflib
import httpx
import json
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from geopy.distance import geodesic

from app.db.client import Database, with_retry
from app.core.logging import logger
from app.core.config import settings
from app.utils.phone import normalize_indian_mobile
from app.etown.services.services_utils import (
    enhance_business_description_logic,
    generate_actual_offerings_logic,
    generate_category_logic,
    generate_general_category_logic,
    get_location_details_logic,
    resolve_description_word_cap,
)
from app.utils.embeddings import generate_embedding
from app.etown.provider.provider_syncing import (
    sync_provider_image_to_dataverse,
    sync_provider_onboard_to_dataverse,
    sync_provider_profile_update_to_dataverse,
)
from app.etown.mytown.mytown_utils import handle_provider_town_assignment
from app.etown.feed.feed_utils import propagate_display_name_to_feed
from app.etown.auth.auth_utils import MOBILE_HELD_BY_OTHER_SQL
from app.etown.plans.plans_syncing import sync_provider_plan_to_dataverse
from app.etown.plans.plans_utils import (
    CONTACT_PLAN_COLUMNS,
    CONTACT_PLAN_CTE,
    CONTACT_PLAN_VALUES,
    PlanLimitError,
    check_provider_onboard_cap,
    resolve_provider_badges,
    resolve_provider_onboard,
)
from app.agent_infrastructure.infrastructure.llm_clients import gemini_2_5_flash_vision
from app.agent_infrastructure.prompt_templates.templates import (
    AI_ONBOARDING_BUBBLE_PROMPT,
    AI_ONBOARDING_CHAT_PROMPT,
    AI_ONBOARDING_MERGE_CONFIRM_PROMPT,
    AI_ONBOARDING_MERGE_REWRITE_PROMPT,
    AI_ONBOARDING_NOTES_TEMPLATE,
    AI_ONBOARDING_PROMPT,
    ONBOARD_VISION_PROMPT,
)
from app.etown.offers.offerings_utils import extract_json, generate_and_store_offerings_from_provider
from app.schemas.api import (
    OnboardDetailsResponse,
    OnboardItem,
    OnboardProviderDetails,
    OnboardServiceDetail,
    AIOnboardingBubbleRequest,
    AIOnboardingBubbleResponse,
    AIOnboardingProviderDetails,
    AIOnboardingRequest,
    AIOnboardingResponse,
)
from app.utils.blob_storage import (
    detect_mime_type,
    normalize_container_name as _normalize_container_name,
    to_bytes as _to_bytes,
    upload_blob_bytes as _upload_blob_bytes,
    download_blob_bytes as _download_blob_bytes,
    current_timestamp_prefix as _current_timestamp_prefix,
    is_stored_media_url as _is_stored_media_url,
)
from app.utils.http_client import get_http_client


PROVIDER_STATUS_VERIFIED = "verified_provider"
PROVIDER_STATUS_ADDED_BY_FOS = "added_by_fos"
PROVIDER_STATUS_UNVERIFIED = "unverified"
PROVIDER_STATUS_AVAILABLE = "available"


PROVIDER_STATUS_MESSAGES = {
    PROVIDER_STATUS_VERIFIED: "This is already a verified provider on Etown",
    PROVIDER_STATUS_ADDED_BY_FOS: "This provider is already added by another FOS",
}


def summarise_provider_status(providers: List[Dict[str, Any]]) -> str:
    """Return a mobile number's overall status from its providers: verified, else FOS, else unverified, else available.

    Args:
        providers: Provider dicts from ProviderService.fetch_providers_by_mobile(), each with a status.
    """
    statuses = {provider["status"] for provider in providers}
    if PROVIDER_STATUS_VERIFIED in statuses:
        return PROVIDER_STATUS_VERIFIED
    if PROVIDER_STATUS_ADDED_BY_FOS in statuses:
        return PROVIDER_STATUS_ADDED_BY_FOS
    return PROVIDER_STATUS_UNVERIFIED if providers else PROVIDER_STATUS_AVAILABLE


def _iso_or_none_value(value: Any) -> Optional[str]:
    """Render a timestamp as ISO 8601, passing anything already stringly typed straight through."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _coerce_json_list(value: Any) -> list:
    """Coerce a jsonb column value (raw JSON string or decoded object) into a list, empty when None."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except (json.JSONDecodeError, TypeError):
            logger.debug("provider: value is not valid JSON; wrapping it in a single-element list")
            return [value]
    return [value]


def _parse_instruction_date(raw: Any) -> Optional[datetime]:
    """Parse a stored instruction date in ISO or a legacy format into a datetime, or None."""
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%d-%m-%Y %I:%M:%S %p",
        "%d-%m-%Y %I:%M %p",
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        logger.debug("provider: instruction date %r did not parse in any known format", text)
        return None


def _normalize_instructions(value: Any) -> list:
    """Normalize the instructions column into uniform entries with ISO dates, sorted oldest first."""
    normalized = []
    for entry in _coerce_json_list(value):
        if isinstance(entry, dict):
            raw_date = entry.get("date")
            raw_instruction = entry.get("instruction")
            if raw_instruction is None and "value" in entry:
                raw_instruction = entry.get("value")
            instruction = raw_instruction if raw_instruction is not None else entry
            entry_type = str(entry.get("type") or "").lower() if entry.get("type") is not None else None
            sender_userprofileid = entry.get("userprofileid")
            sender_contactid = entry.get("contactid")
            sender_role = entry.get("role")
            sender_name = entry.get("name")
            sender_profileimage = entry.get("profileimage")
            entry_filename = entry.get("filename")
            entry_thumbnail = entry.get("thumbnail")
        else:
            raw_date = None
            instruction = entry
            entry_type = None
            sender_userprofileid = None
            sender_contactid = None
            sender_role = None
            sender_name = None
            sender_profileimage = None
            entry_filename = None
            entry_thumbnail = None

        parsed = _parse_instruction_date(raw_date)
        normalized.append({
            "instruction": instruction,
            "type": entry_type if entry_type else ("image" if isinstance(instruction, str) and instruction.startswith(("http://", "https://")) else "text"),
            "date": parsed.strftime("%Y-%m-%dT%H:%M:%S") if parsed else raw_date,
            "userprofileid": sender_userprofileid if sender_userprofileid is not None else None,
            "contactid": sender_contactid if sender_contactid is not None else None,
            "role": sender_role if sender_role is not None else None,
            "name": sender_name if sender_name is not None else None,
            "profileimage": sender_profileimage if sender_profileimage is not None else None,
            "filename": entry_filename if entry_filename is not None else None,
            "thumbnail": entry_thumbnail if entry_thumbnail is not None else None,
            "_parsed": parsed,
        })

    normalized.sort(key=lambda item: (0, item["_parsed"].timestamp()) if item["_parsed"] else (1, 0))

    for item in normalized:
        item.pop("_parsed", None)
    return normalized


_INSTRUCTION_SENDER_QUERY = """
SELECT
    LOWER(up.userprofileid)  AS lookup_id,
    up.userprofileid         AS userprofileid,
    up.contactid             AS contactid,
    up.role                  AS role,
    up.rolename              AS rolename,
    up.name                  AS name,
    up.contactidname         AS contactidname,
    p.businessname           AS provider_businessname,
    p.serviceimageurl        AS provider_serviceimageurl,
    c.profileimage           AS contact_profileimage
FROM public.etown_userprofile up
LEFT JOIN public.etown_provider p
    ON LOWER(p.userprofileid) = LOWER(up.userprofileid)
LEFT JOIN public.etown_contact c
    ON c.contactid = up.contactid
WHERE LOWER(up.userprofileid) = ANY($1::text[])
"""


def _instruction_sender_display(row: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Return the contact id, rolename, display name and image for one profile row, chosen by role."""
    role_code = row.get("role")
    if role_code is not None and str(role_code).strip().lstrip("-").isdigit():
        role_code = int(str(role_code).strip())

    if role_code == 1:
        name = (
            row.get("provider_businessname")
            or row.get("name")
            or row.get("contactidname")
        )
        profileimage = row.get("provider_serviceimageurl") or row.get("contact_profileimage")
    elif role_code == 2:
        name = row.get("contactidname") or row.get("name")
        profileimage = row.get("contact_profileimage")
    else:
        name = row.get("name") or row.get("contactidname")
        profileimage = row.get("contact_profileimage")

    return {
        "contactid": row.get("contactid"),
        "role": row.get("rolename"),
        "name": name,
        "profileimage": profileimage,
    }


async def hydrate_instruction_senders(instruction_lists: List[list], con: Any = None) -> None:
    """Fill each instruction's sender role, name and image in place from one profile query; never raises.

    Args:
        instruction_lists: Normalized instruction lists from `_normalize_instructions`.
        con: Optional connection to reuse; falls back to the shared pool.
    """
    entries = [
        entry
        for instructions in instruction_lists
        if instructions
        for entry in instructions
        if isinstance(entry, dict) and entry.get("userprofileid")
    ]
    if not entries:
        return

    lookup_ids = sorted({str(entry["userprofileid"]).strip().lower() for entry in entries})

    try:
        if con is not None:
            rows = await con.fetch(_INSTRUCTION_SENDER_QUERY, lookup_ids)
        else:
            rows = await Database.fetch(_INSTRUCTION_SENDER_QUERY, lookup_ids)
    except Exception as exc:
        logger.warning(
            "Unable to hydrate instruction senders | ids=%d | error=%s", len(lookup_ids), exc
        )
        return

    senders = {row["lookup_id"]: _instruction_sender_display(dict(row)) for row in rows}

    for entry in entries:
        sender = senders.get(str(entry["userprofileid"]).strip().lower())
        if sender is None:
            continue
        entry["contactid"] = sender["contactid"] if sender["contactid"] is not None else entry.get("contactid")
        entry["role"] = sender["role"]
        entry["name"] = sender["name"]
        entry["profileimage"] = sender["profileimage"]


def convert_state_name_to_code(state_name: str) -> str:
    """Convert an Indian state name to its two-letter code, falling back to the first two letters."""
    if not state_name:
        return None
        
    state_mapping = {
        'andhra pradesh': 'AP', 'arunachal pradesh': 'AR', 'assam': 'AS',
        'bihar': 'BR', 'chhattisgarh': 'CG', 'goa': 'GA', 'gujarat': 'GJ',
        'haryana': 'HR', 'himachal pradesh': 'HP', 'jharkhand': 'JH',
        'karnataka': 'KA', 'kerala': 'KL', 'madhya pradesh': 'MP',
        'maharashtra': 'MH', 'manipur': 'MN', 'meghalaya': 'ML',
        'mizoram': 'MZ', 'nagaland': 'NL', 'odisha': 'OR', 'punjab': 'PB',
        'rajasthan': 'RJ', 'sikkim': 'SK', 'tamil nadu': 'TN',
        'telangana': 'TS', 'tripura': 'TR', 'uttar pradesh': 'UP',
        'uttarakhand': 'UK', 'west bengal': 'WB',
        'andaman and nicobar islands': 'AN', 'chandigarh': 'CH',
        'dadra and nagar haveli': 'DN', 'daman and diu': 'DD',
        'lakshadweep': 'LD', 'delhi': 'DL', 'puducherry': 'PY'
    }
    
    normalized_name = state_name.lower().strip()
    
    if normalized_name in state_mapping:
        return state_mapping[normalized_name]
    
    for full_name, code in state_mapping.items():
        if normalized_name in full_name or full_name in normalized_name:
            return code
    
    if len(state_name) == 2 and state_name.isalpha():
        return state_name.upper()
    
    return state_name[:2].upper()


_VALID_BIZ_CATEGORIES = frozenset({'product', 'service', 'product,service'})

_MOBILE_TAKEN_DETAIL = "This mobile number already has a member account in etown"

_ALTERNATE_MOBILE_TAKEN_DETAIL = (
    "This alternate mobile number already belongs to another etown account"
)

_MOBILE_HELD_BY_OTHER_SQL = MOBILE_HELD_BY_OTHER_SQL


_CONTENTLESS_DESCRIPTIONS = {
    "point of interest", "establishment", "store", "service", "premise",
}


def _normalize_biz_category(value: Optional[str], fallback: str = 'service') -> str:
    """Return value as 'product', 'service' or 'product,service', or fallback when unrecognised."""
    if value:
        normalised = value.strip().lower()
        if normalised in _VALID_BIZ_CATEGORIES:
            return normalised
    return fallback


class ProviderService:
    """Service class for provider management operations."""

    @staticmethod
    async def enhance_provider_data(
        business_name: str,
        description: str,
        place_types: Optional[List[str]] = None,
        reviews: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate an AI description, actual offerings and categories for a provider, with safe defaults on failure.

        Args:
            business_name: Business name, the strongest signal for every prompt.
            description: Original business description.
            place_types: Google place types, if the provider came from Places.
            reviews: Google review texts, if any.

        Returns:
            Dict of enhanced description, services, offerings and business/service/general categories.
        """
        try:
            logger.info(f"Starting AI enhancement for business: {business_name}")
            description_seed = description or ""
            if not description_seed.strip() or description_seed.strip().lower() in _CONTENTLESS_DESCRIPTIONS:
                description_seed = business_name

            enhanced_desc_response = await enhance_business_description_logic(
                description_seed,
                business_name=business_name,
                place_types=place_types,
                reviews=reviews,
                word_cap=resolve_description_word_cap(description_seed),
            )
            enhanced_description = enhanced_desc_response.enhanced_description

            category_seed = f"{business_name}. {enhanced_description}"
            category_response = await generate_category_logic(category_seed, include_general=False)
            business_category = category_response.business_type
            service_category = category_response.subcategory

            offerings_task = asyncio.create_task(
                generate_actual_offerings_logic(
                    business_name=business_name,
                    place_types=list(place_types or [])
                    + ([service_category] if service_category else []),
                    reviews=reviews,
                    description=description,
                )
            )
            general_category_task = asyncio.create_task(
                generate_general_category_logic(
                    category_seed,
                    business_name=business_name,
                    place_types=place_types,
                )
            )
            offerings, general_category_response = await asyncio.gather(offerings_task, general_category_task)
            services_list = [
                item["name"]
                for item in offerings
                if item.get("name")
            ]
            services_text = ", ".join(services_list)
            general_category = general_category_response.generalcategory

            logger.info(
                f"AI enhancement completed for business: {business_name} "
                f"({len(services_list)} offering(s))"
            )

            return {
                'enhanced_description': enhanced_description,
                'services': services_text,
                'services_list': services_list,
                'offerings': offerings,
                'business_category': business_category,
                'service_category': service_category,
                'general_category': general_category,
                'original_description': description
            }

        except (NameError, AttributeError, TypeError) as e:
            logger.critical(
                "Error enhancing provider data for %s: %s", business_name, e, exc_info=True,
            )
            raise
        except Exception as e:
            logger.error(f"Error enhancing provider data for {business_name}: {e}", exc_info=True)
            return {
                'enhanced_description': description,
                'services': '',
                'services_list': [],
                'offerings': [],
                'business_category': 'service',
                'service_category': 'General Services',
                'general_category': 'general',
                'original_description': description
            }

    @staticmethod
    async def generate_onboard_services(
        business_name: str,
        description: Optional[str] = None,
        business_category_input: Optional[str] = None,
        service_category_input: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate the services and categories /onboard's background pass would write, without storing anything.

        Args:
            business_name: Business name.
            description: Original business description.
            business_category_input: Pre-provided business category, used when the AI returns none.
            service_category_input: Pre-provided service category, preferred over the AI's.

        Returns:
            Dict of providerservice_info and ai_enhancements, shaped as in the /onboard response.
        """
        enhanced_data = await ProviderService.enhance_provider_data(business_name, description or business_name)

        business_category_final = _normalize_biz_category(
            enhanced_data.get('business_category') or business_category_input
        )
        service_category_final = (
            service_category_input
            or enhanced_data.get('service_category')
            or 'General Services'
        )
        services_final = (
            enhanced_data.get('services')
            or (service_category_input or '').strip()
            or business_name
        )
        description_final = enhanced_data.get('enhanced_description') or description or business_name

        logger.info(
            "[onboard_services] Generated %d service(s) for %s, nothing stored",
            len(enhanced_data.get('services_list') or []), business_name,
        )
        return {
            "providerservice_info": {
                "businessname": business_name,
                "servicedescription": description_final,
                "services": services_final,
                "businesscategory": business_category_final,
                "servicecategory": service_category_final,
            },
            "ai_enhancements": {
                "original_description": description or '',
                "enhanced_description": description_final,
                "generated_services": enhanced_data.get('services_list') or [],
                "business_category": business_category_final,
                "service_category": service_category_final,
                "general_category": enhanced_data.get('general_category') or 'general',
            },
        }

    @staticmethod
    @with_retry()
    async def check_phone_exists(
        phone_number: str,
        exclude_contact_id: Optional[str] = None,
    ) -> bool:
        """Return True when another non-deleted contact holds the number as its mobile or alternate mobile.

        Args:
            phone_number: Phone number to check.
            exclude_contact_id: Contact to ignore, usually the one being written; None checks all.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            try:
                result = await con.fetchrow(
                    _MOBILE_HELD_BY_OTHER_SQL, phone_number, exclude_contact_id
                )
                return result is not None
                
            except Exception as e:
                logger.error(f"Error checking phone number existence: {e}", exc_info=True)
                raise

    @staticmethod
    async def promote_provider_to_verified(contact_id: str) -> Optional[Dict[str, Any]]:
        """Promote a contact's provider, service and profile rows to verified within its plan cap, skipping FOS, CSR and STAFF captures.

        Args:
            contact_id: Contact whose provider records should be promoted.

        Returns:
            Dict of the contact id and affected row ids per table, or None when nothing changed.

        Raises:
            RuntimeError: If the database pool is not initialized.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        allowed_provider_ids: Optional[List[str]] = None
        try:
            allowance = await resolve_provider_onboard(contact_id)
            if allowance is not None and allowance["limit"] is not None:
                cap = allowance["limit"]
                provider_rows = await Database.fetch(
                    """
                    SELECT providerid,
                           (providertype = 'etown_verified'
                            AND publicsearchprovider = 'verified') AS is_verified,
                           COALESCE(source, '') IN ('FOS', 'CSR', 'STAFF') AS is_captured
                      FROM etown_provider
                     WHERE contactid = $1 AND COALESCE(deleted, 'false') <> 'true'
                     ORDER BY creationdate ASC NULLS LAST, providerid ASC
                    """,
                    contact_id,
                )
                verified_ids = [str(r["providerid"]) for r in provider_rows if r["is_verified"]]
                pending_ids = [
                    str(r["providerid"]) for r in provider_rows
                    if not r["is_verified"] and not r["is_captured"]
                ]
                remaining_slots = max(0, cap - len(verified_ids))
                skipped_ids = pending_ids[remaining_slots:]
                allowed_provider_ids = verified_ids + pending_ids[:remaining_slots]
                if skipped_ids:
                    logger.warning(
                        "[provider_promote] contact_id=%s plan=%s caps verified providers at "
                        "%d (already has %d); leaving %d provider(s) unverified: %s",
                        contact_id, allowance["plan_code"], cap, len(verified_ids),
                        len(skipped_ids), skipped_ids,
                    )
        except Exception as e:
            logger.warning(
                "[provider_promote] onboard-cap lookup failed for contact_id=%s, "
                "promoting uncapped: %s", contact_id, e,
            )
            allowed_provider_ids = None

        row = await Database.fetchrow(
            """
            WITH target AS (
                SELECT $1::text AS contactid
                WHERE EXISTS (SELECT 1 FROM etown_provider WHERE contactid = $1)
                  AND (
                      EXISTS (
                          SELECT 1 FROM etown_provider
                          WHERE contactid = $1
                            AND COALESCE(source, '') NOT IN ('FOS', 'CSR', 'STAFF')
                            AND (providertype IS DISTINCT FROM 'etown_verified'
                                 OR publicsearchprovider IS DISTINCT FROM 'verified')
                      )
                      OR EXISTS (
                          SELECT 1 FROM etown_providerservices ps
                          WHERE ps.contactid = $1
                            AND NOT EXISTS (
                                SELECT 1 FROM etown_provider fp
                                WHERE fp.providerid = ps.providerid
                                  AND COALESCE(fp.source, '') IN ('FOS', 'CSR', 'STAFF')
                            )
                            AND (ps.providertype IS DISTINCT FROM 'etown_verified'
                                 OR ps.publicsearchprovider IS DISTINCT FROM 'verified')
                      )
                      OR EXISTS (
                          SELECT 1 FROM etown_providerservices_copy psc
                          WHERE psc.contactid = $1
                            AND NOT EXISTS (
                                SELECT 1 FROM etown_provider fp
                                WHERE fp.providerid = psc.providerid
                                  AND COALESCE(fp.source, '') IN ('FOS', 'CSR', 'STAFF')
                            )
                            AND (psc.providertype IS DISTINCT FROM 'etown_verified'
                                 OR psc.publicsearchprovider IS DISTINCT FROM 'verified')
                      )
                      OR EXISTS (
                          SELECT 1 FROM etown_userprofile
                          WHERE contactid = $1 AND role = 1 AND verified IS DISTINCT FROM TRUE
                      )
                      OR EXISTS (
                          SELECT 1 FROM etown_contact
                          WHERE contactid = $1
                            AND (verified IS DISTINCT FROM TRUE
                                 OR publicsearchstatus IS DISTINCT FROM 'verified')
                      )
                  )
            ),
            upd_contact AS (
                UPDATE etown_contact c
                SET verified = TRUE, publicsearchstatus = 'verified', modifieddate = NOW()
                FROM target t WHERE c.contactid = t.contactid
                RETURNING c.contactid
            ),
            upd_userprofile AS (
                UPDATE etown_userprofile up
                SET verified = TRUE, modifieddate = NOW()
                FROM target t WHERE up.contactid = t.contactid AND up.role = 1
                RETURNING up.userprofileid
            ),
            upd_provider AS (
                UPDATE etown_provider p
                SET providertype = 'etown_verified', publicsearchprovider = 'verified',
                    modifieddate = NOW()
                FROM target t
                WHERE p.contactid = t.contactid
                  AND COALESCE(p.source, '') NOT IN ('FOS', 'CSR', 'STAFF')
                  AND ($2::text[] IS NULL OR p.providerid = ANY($2::text[]))
                RETURNING p.providerid
            ),
            upd_services AS (
                UPDATE etown_providerservices ps
                SET providertype = 'etown_verified', publicsearchprovider = 'verified',
                    modifieddate = NOW()
                FROM target t
                WHERE ps.contactid = t.contactid
                  AND NOT EXISTS (
                      SELECT 1 FROM etown_provider fp
                      WHERE fp.providerid = ps.providerid
                        AND COALESCE(fp.source, '') IN ('FOS', 'CSR', 'STAFF')
                  )
                  AND ($2::text[] IS NULL OR ps.providerid = ANY($2::text[]))
                RETURNING ps.providerservicesid
            ),
            upd_services_copy AS (
                UPDATE etown_providerservices_copy psc
                SET providertype = 'etown_verified', publicsearchprovider = 'verified',
                    modifieddate = NOW()
                FROM target t
                WHERE psc.contactid = t.contactid
                  AND NOT EXISTS (
                      SELECT 1 FROM etown_provider fp
                      WHERE fp.providerid = psc.providerid
                        AND COALESCE(fp.source, '') IN ('FOS', 'CSR', 'STAFF')
                  )
                  AND ($2::text[] IS NULL OR psc.providerid = ANY($2::text[]))
                RETURNING psc.providerservicesid
            )
            SELECT
                (SELECT contactid FROM target)                            AS contact_id,
                ARRAY(SELECT userprofileid FROM upd_userprofile)          AS userprofile_ids,
                ARRAY(SELECT providerid FROM upd_provider)                AS provider_ids,
                ARRAY(SELECT providerservicesid FROM upd_services)        AS providerservices_ids,
                ARRAY(SELECT providerservicesid FROM upd_services_copy)   AS providerservices_copy_ids,
                (SELECT count(*) FROM upd_contact)                        AS contact_rows
            """,
            contact_id, allowed_provider_ids,
        )

        if not row or row["contact_id"] is None:
            return None

        if not row["contact_rows"]:
            logger.warning(
                f"[provider_promote] No etown_contact row for contact_id={contact_id}; "
                "provider records promoted without a contact flip"
            )

        return {
            "contact_id": str(row["contact_id"]),
            "userprofile_ids": [str(x) for x in row["userprofile_ids"]],
            "provider_ids": [str(x) for x in row["provider_ids"]],
            "providerservices_ids": [str(x) for x in row["providerservices_ids"]],
            "providerservices_copy_ids": [str(x) for x in row["providerservices_copy_ids"]],
        }

    @staticmethod
    @with_retry()
    async def onboard_provider_in_db(
        business_name: str,
        phone_number: str,
        city: Optional[str] = None,
        description: Optional[str] = None,
        first_name: Optional[str] = None,
        email: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        enhanced_data: Optional[Dict[str, Any]] = None,
        additionaladdressinfo: Optional[str] = None,
        area_location: Optional[str] = None,
        state: Optional[str] = None,
        formatted_address: Optional[str] = None,
        businesslogo: Optional[str] = None,
        contact_id: Optional[str] = None,
        business_category_input: Optional[str] = None,
        service_category_input: Optional[str] = None,
        servicetype: Optional[str] = None,
        website: Optional[str] = None,
        availability: Optional[str] = None,
        services: Optional[str] = None,
        alternate_mobile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert a verified provider's contact, userprofile, provider and service rows in one transaction.

        Returns:
            Dict of contact, userprofile, provider, providerservice and AI-enhancement info.

        Raises:
            RuntimeError: If the database pool is not initialized.
            HTTPException: 400 for a missing name or mobile, 409 for a duplicate or taken number.
            PlanLimitError: When the target contact already holds every provider its plan allows.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        if not business_name or not business_name.strip():
            raise HTTPException(status_code=400, detail="Business name is required")
        if not phone_number or not phone_number.strip():
            raise HTTPException(status_code=400, detail="Mobile number is required")

        if not first_name:
            first_name = business_name.strip()

        business_category_for_db = _normalize_biz_category(business_category_input)
        service_category_for_db = service_category_input or 'General Services'
        desc_for_insert = description or business_name
        services_for_insert = (services or '').strip() or business_name
        initial_statecode = convert_state_name_to_code(state) if state else None
        initial_areaname = area_location or None
        initial_address = formatted_address or city or ''

        acquire_start = time.perf_counter()
        try:
            async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
                acquire_ms = (time.perf_counter() - acquire_start) * 1000
                logger.info("[provider_onboard] EtownDB acquire_time_ms=%.2f", acquire_ms)
            
                async with con.transaction():
                    try:
                        await con.execute(
                            "SELECT pg_advisory_xact_lock(42, hashtext($1))",
                            f"{phone_number}:{business_name.lower().strip()}",
                        )
                        _phone_owners = await con.fetch(
                            """SELECT c.contactid, c.rolecode,
                                      (SELECT count(*) FROM etown_userprofile           WHERE contactid = c.contactid)
                                    + (SELECT count(*) FROM etown_provider              WHERE contactid = c.contactid)
                                    + (SELECT count(*) FROM etown_providerservices      WHERE contactid = c.contactid)
                                    + (SELECT count(*) FROM etown_providerservices_copy WHERE contactid = c.contactid)
                                    + (SELECT count(*) FROM etown_member                WHERE contactid = c.contactid)
                                      AS profile_refs
                               FROM etown_contact c
                               WHERE c.mobilephone = $1
                                 AND c.deleted IS DISTINCT FROM 'true'
                                 AND ($2::text IS NULL OR c.contactid <> $2)""",
                            phone_number, contact_id,
                        )
                        _live_owner = next(
                            (
                                row for row in _phone_owners
                                if row['profile_refs'] and row['rolecode'] != 1
                            ),
                            None,
                        )
                        if _live_owner:
                            logger.warning(
                                "[provider_onboard] Mobile %s already held by member contact %s "
                                "(rolecode=%s) with %d profile row(s) — onboard rejected "
                                "(contact_id=%s)",
                                phone_number, _live_owner['contactid'],
                                _live_owner['rolecode'], _live_owner['profile_refs'], contact_id,
                            )
                            raise HTTPException(
                                status_code=409,
                                detail=_MOBILE_TAKEN_DETAIL,
                            )
                        if _phone_owners:
                            logger.info(
                                "[provider_onboard] Mobile %s held only by provider/orphan "
                                "contact(s) %s — reusing instead of rejecting",
                                phone_number,
                                [
                                    f"{r['contactid']}(rolecode={r['rolecode']},refs={r['profile_refs']})"
                                    for r in _phone_owners
                                ],
                            )
                        _idempotent_query = """
                            SELECT
                                c.contactid, c.firstname, c.mobilephone, c.email, c.createdon, c.userlogin, c.city,
                                up.userprofileid, up.role, up.name AS userprofile_name,
                                p.providerid, p.businessname, p.businesscategory, p.description, p.publicsearchprovider,
                                p.serviceimageurl,
                                psc.providerservicesid, psc.services, psc.servicecategory,
                                ps.providerservicesid AS providerservicesid_orig
                            FROM etown_contact c
                            LEFT JOIN etown_userprofile up  ON up.contactid  = c.contactid AND up.role = 1
                            LEFT JOIN etown_provider p      ON p.contactid   = c.contactid
                            LEFT JOIN etown_providerservices_copy psc ON psc.providerid = p.providerid
                            LEFT JOIN etown_providerservices ps       ON ps.providerid  = p.providerid
                            WHERE {where_clause}
                            LIMIT 1
                        """

                        _live_row = "c.deleted IS DISTINCT FROM 'true'"
                        if not contact_id:
                            existing_check = await con.fetchrow(
                                _idempotent_query.format(
                                    where_clause=(
                                        f"c.mobilephone = $1 AND {_live_row} "
                                        "AND LOWER(TRIM(p.businessname)) = LOWER(TRIM($2))"
                                    )
                                ),
                                phone_number, business_name,
                            )
                        else:
                            existing_check = await con.fetchrow(
                                _idempotent_query.format(
                                    where_clause=(
                                        f"c.contactid = $1 AND {_live_row} "
                                        "AND LOWER(TRIM(p.businessname)) = LOWER(TRIM($2))"
                                    )
                                ),
                                contact_id, business_name,
                            )
                        if existing_check and existing_check.get('providerid'):
                            logger.info(
                                "[provider_onboard] Duplicate blocked for %s",
                                f"phone={phone_number}" if not contact_id else f"contact_id={contact_id}",
                            )
                            raise HTTPException(
                                status_code=409,
                                detail="A provider with this business name and phone number is already registered",
                            )

                        if contact_id:
                            generated_contact_id = contact_id
                        else:
                            _existing_contact = await con.fetchrow(
                                "SELECT contactid FROM etown_contact WHERE mobilephone = $1 LIMIT 1",
                                phone_number,
                            )
                            generated_contact_id = (
                                str(_existing_contact['contactid']) if _existing_contact
                                else str(uuid.uuid4())
                            )
                        if alternate_mobile:
                            _alt_owner = await con.fetchrow(
                                _MOBILE_HELD_BY_OTHER_SQL,
                                alternate_mobile, generated_contact_id,
                            )
                            if _alt_owner:
                                logger.warning(
                                    "[provider_onboard] Alternate mobile %s already held by "
                                    "contact %s -- onboard rejected (contact_id=%s)",
                                    alternate_mobile, _alt_owner['contactid'],
                                    generated_contact_id,
                                )
                                raise HTTPException(
                                    status_code=409,
                                    detail=_ALTERNATE_MOBILE_TAKEN_DETAIL,
                                )

                        await check_provider_onboard_cap(generated_contact_id, business_name)
                        userprofile_id = str(uuid.uuid4())
                        provider_id = str(uuid.uuid4())
                        providerservice_id = str(uuid.uuid4())
                        providerservice_orig_id = providerservice_id
                        current_timestamp = datetime.now(timezone.utc).replace(tzinfo=None)

                        register_query = f"""
                            WITH {CONTACT_PLAN_CTE.format(contact='$1')},
                            inserted_contact AS (
                                INSERT INTO etown_contact (
                                    contactid, firstname, mobilephone, email, userlogin,
                                    createdon, rolecode, rolecodename, city, content_vector, address, statecode, latitude, longitude, publicsearchstatus, verified,
                                    alternatemobile
                                )
                                VALUES ($1, $2, $3, $23, 'mobile', $4, 1, 'Provider', $5, $22::vector, $24, $16, $17, $18, 'unverified', TRUE,
                                        $30)
                                ON CONFLICT (contactid) DO UPDATE SET
                                    content_vector = EXCLUDED.content_vector,
                                    address = COALESCE(EXCLUDED.address, etown_contact.address),
                                    statecode = COALESCE(EXCLUDED.statecode, etown_contact.statecode),
                                    latitude = COALESCE(EXCLUDED.latitude, etown_contact.latitude),
                                    longitude = COALESCE(EXCLUDED.longitude, etown_contact.longitude),
                                    alternatemobile = COALESCE(EXCLUDED.alternatemobile, etown_contact.alternatemobile),
                                    publicsearchstatus = 'verified',
                                    verified = TRUE
                                RETURNING contactid, firstname, mobilephone, email, createdon, userlogin, city,
                                          alternatemobile
                            ),
                            inserted_userprofile AS (
                                INSERT INTO etown_userprofile (
                                    userprofileid, contactid, contactidname,
                                    role, rolename, name, mobilenumber, createdon, verified
                                )
                                SELECT $6, ic.contactid, $2,
                                       1, 'Provider', $2, $3, $4, TRUE
                                FROM inserted_contact ic
                                ON CONFLICT (userprofileid) DO UPDATE SET
                                    contactidname = EXCLUDED.contactidname,
                                    name = EXCLUDED.name,
                                    mobilenumber = EXCLUDED.mobilenumber,
                                    createdon = EXCLUDED.createdon,
                                    verified = TRUE
                                RETURNING userprofileid, contactid, role, name, mobilenumber
                            ),
                            inserted_provider AS (
                                INSERT INTO etown_provider (
                                    providerid, businessname, businesscategory, contactid, userprofileid,
                                    contactidname, userprofileidname, firstname, email, mobilenumber, city, description,
                                    providertype, publicsearchprovider, role, rolename, creationdate,
                                    areaname, townname, statecode, latitude, longitude, content_vector, address,
                                    serviceimageurl, additionaladdressinfo, source, servicetype, websiteurl, availability,
                                    alternatemobile,
                                    {CONTACT_PLAN_COLUMNS}
                                )
                                SELECT $7, $13, $11, iup.contactid, iup.userprofileid,
                                       $2, $2, $2, $23, $3, $5, $8,
                                       'etown_verified', 'verified', 1, 'Provider', $4,
                                       $14, $15, $16, $17, $18, $19::vector, $24,
                                       $25, $26, 'Onboard', $27, $28, $29,
                                       $30,
                                       {CONTACT_PLAN_VALUES}
                                FROM inserted_userprofile iup
                                ON CONFLICT (providerid) DO UPDATE SET
                                    businessname = EXCLUDED.businessname,
                                    businesscategory = EXCLUDED.businesscategory,
                                    contactidname = EXCLUDED.contactidname,
                                    userprofileidname = EXCLUDED.userprofileidname,
                                    description = EXCLUDED.description,
                                    city = EXCLUDED.city,
                                    areaname = EXCLUDED.areaname,
                                    townname = EXCLUDED.townname,
                                    statecode = EXCLUDED.statecode,
                                    latitude = EXCLUDED.latitude,
                                    longitude = EXCLUDED.longitude,
                                    content_vector = EXCLUDED.content_vector,
                                    address = EXCLUDED.address,
                                    providertype = EXCLUDED.providertype,
                                    publicsearchprovider = EXCLUDED.publicsearchprovider,
                                    role = EXCLUDED.role,
                                    rolename = EXCLUDED.rolename,
                                    serviceimageurl = COALESCE(EXCLUDED.serviceimageurl, etown_provider.serviceimageurl),
                                    additionaladdressinfo = COALESCE(EXCLUDED.additionaladdressinfo, etown_provider.additionaladdressinfo),
                                    source = EXCLUDED.source,
                                    servicetype = COALESCE(EXCLUDED.servicetype, etown_provider.servicetype),
                                    websiteurl = COALESCE(EXCLUDED.websiteurl, etown_provider.websiteurl),
                                    availability = COALESCE(EXCLUDED.availability, etown_provider.availability),
                                    alternatemobile = COALESCE(EXCLUDED.alternatemobile, etown_provider.alternatemobile)
                                RETURNING providerid, businessname, businesscategory, contactid, userprofileid,
                                         city, description, publicsearchprovider, contactidname, userprofileidname,
                                         serviceimageurl, plan_code, alternatemobile
                            ),

                            inserted_providerservice_copy AS (
                                INSERT INTO etown_providerservices_copy (
                                    providerservicesid, businessname, businesscategory, servicecategory, generalcategory,
                                    providerid, contactid, contactidname, userprofileid, userprofileidname, email, mobilenumber,
                                    servicedescription, services, city, address,
                                    providertype, publicsearchprovider, createdon, content_vector,
                                    areaname, statecode, latitude, longitude,
                                    serviceimageurl, additionaladdressinfo, source, websiteurl, availability
                                )
                                SELECT $9, $13, $11, $12, NULL,
                                       ip.providerid, ip.contactid, ip.contactidname, ip.userprofileid, ip.userprofileidname, $23, $3,
                                       $8, $10, ip.city, $24,
                                       'etown_verified', 'verified', $4, $20::vector,
                                       $14, $16, $17, $18,
                                       $25, $26, 'Onboard', $28, $29
                                FROM inserted_provider ip
                                ON CONFLICT (providerservicesid) DO UPDATE SET
                                    businessname = EXCLUDED.businessname,
                                    businesscategory = EXCLUDED.businesscategory,
                                    servicecategory = EXCLUDED.servicecategory,
                                    generalcategory = EXCLUDED.generalcategory,
                                    servicedescription = EXCLUDED.servicedescription,
                                    services = EXCLUDED.services,
                                    contactidname = EXCLUDED.contactidname,
                                    userprofileidname = EXCLUDED.userprofileidname,
                                    email = EXCLUDED.email,
                                    mobilenumber = EXCLUDED.mobilenumber,
                                    city = EXCLUDED.city,
                                    content_vector = EXCLUDED.content_vector,
                                    address = EXCLUDED.address,
                                    areaname = EXCLUDED.areaname,
                                    statecode = EXCLUDED.statecode,
                                    latitude = EXCLUDED.latitude,
                                    longitude = EXCLUDED.longitude,
                                    providertype = EXCLUDED.providertype,
                                    publicsearchprovider = EXCLUDED.publicsearchprovider,
                                    serviceimageurl = COALESCE(EXCLUDED.serviceimageurl, etown_providerservices_copy.serviceimageurl),
                                    additionaladdressinfo = COALESCE(EXCLUDED.additionaladdressinfo, etown_providerservices_copy.additionaladdressinfo),
                                    websiteurl = COALESCE(EXCLUDED.websiteurl, etown_providerservices_copy.websiteurl),
                                    availability = COALESCE(EXCLUDED.availability, etown_providerservices_copy.availability)
                                RETURNING providerservicesid, providerid, businessname, servicedescription, services,
                                         businesscategory, servicecategory, availability
                            ),
                            inserted_providerservice AS (
                                INSERT INTO etown_providerservices (
                                    providerservicesid, businessname, businesscategory, servicecategory, generalcategory,
                                    providerid, contactid, contactidname, userprofileid, userprofileidname, email, mobilenumber,
                                    servicedescription, services, city, address,
                                    providertype, publicsearchprovider, createdon,
                                    areaname, statecode, latitude, longitude,
                                    serviceimageurl, additionaladdressinfo, source, websiteurl, availability
                                )
                                SELECT $21, $13, $11, $12, NULL,
                                       ip.providerid, ip.contactid, ip.contactidname, ip.userprofileid, ip.userprofileidname, $23, $3,
                                       $8, $10, ip.city, $24,
                                       'etown_verified', 'verified', $4,
                                       $14, $16, $17, $18,
                                       $25, $26, 'Onboard', $28, $29
                                FROM inserted_provider ip
                                ON CONFLICT (providerservicesid) DO UPDATE SET
                                    businessname = EXCLUDED.businessname,
                                    businesscategory = EXCLUDED.businesscategory,
                                    servicecategory = EXCLUDED.servicecategory,
                                    generalcategory = EXCLUDED.generalcategory,
                                    servicedescription = EXCLUDED.servicedescription,
                                    services = EXCLUDED.services,
                                    contactidname = EXCLUDED.contactidname,
                                    userprofileidname = EXCLUDED.userprofileidname,
                                    email = EXCLUDED.email,
                                    mobilenumber = EXCLUDED.mobilenumber,
                                    city = EXCLUDED.city,
                                    address = EXCLUDED.address,
                                    areaname = EXCLUDED.areaname,
                                    statecode = EXCLUDED.statecode,
                                    latitude = EXCLUDED.latitude,
                                    longitude = EXCLUDED.longitude,
                                    providertype = EXCLUDED.providertype,
                                    publicsearchprovider = EXCLUDED.publicsearchprovider,
                                    serviceimageurl = COALESCE(EXCLUDED.serviceimageurl, etown_providerservices.serviceimageurl),
                                    additionaladdressinfo = COALESCE(EXCLUDED.additionaladdressinfo, etown_providerservices.additionaladdressinfo),
                                    websiteurl = COALESCE(EXCLUDED.websiteurl, etown_providerservices.websiteurl),
                                    availability = COALESCE(EXCLUDED.availability, etown_providerservices.availability)
                                RETURNING providerservicesid, providerid, businessname, servicedescription, services,
                                         businesscategory, servicecategory
                            )
                            SELECT
                                ic.contactid, ic.firstname, ic.mobilephone, ic.email, ic.createdon, ic.userlogin, ic.city,
                                ic.alternatemobile,
                                iup.userprofileid, iup.role,
                                ip.providerid, ip.businessname, ip.businesscategory, ip.description, ip.publicsearchprovider,
                                ip.serviceimageurl, ip.plan_code,
                                ipsc.providerservicesid, ipsc.services, ipsc.servicecategory, ipsc.availability,
                                ips.providerservicesid as providerservicesid_orig
                            FROM inserted_contact ic
                            JOIN inserted_userprofile iup ON iup.contactid = ic.contactid
                            JOIN inserted_provider ip ON ip.contactid = ic.contactid
                            JOIN inserted_providerservice_copy ipsc ON ipsc.providerid = ip.providerid
                            JOIN inserted_providerservice ips ON ips.providerid = ip.providerid
                        """

                        query_start = time.perf_counter()
                        registration_result = await con.fetchrow(
                            register_query,
                            generated_contact_id,
                            first_name,
                            phone_number,
                            current_timestamp,
                            city or '',
                            userprofile_id,
                            provider_id,
                            desc_for_insert,
                            providerservice_id,
                            services_for_insert,
                            business_category_for_db,
                            service_category_for_db,
                            business_name,
                            initial_areaname,
                            None,
                            initial_statecode,
                            latitude,
                            longitude,
                            None,
                            None,
                            providerservice_orig_id,
                            None,
                            email,
                            initial_address,
                            None,
                            additionaladdressinfo,
                            servicetype,
                            website,
                            availability,
                            alternate_mobile,
                        )
                        query_ms = (time.perf_counter() - query_start) * 1000
                        logger.info("[provider_onboard] EtownDB query_time_ms=%.2f", query_ms)

                        if not registration_result:
                            raise Exception("Provider registration failed - no result returned")
                    
                        response = {
                            "contact_info": {
                                "contactid": registration_result['contactid'],
                                "firstname": registration_result['firstname'],
                                "mobilephone": registration_result['mobilephone'],
                                "email": registration_result.get('email'),
                                "createdon": registration_result['createdon'].isoformat() + "Z" if registration_result['createdon'] else None,
                                "userlogin": registration_result['userlogin'],
                                "city": registration_result['city'],
                                "alternatemobile": registration_result.get('alternatemobile'),
                            },
                            "userprofile_info": {
                                "userprofileid": registration_result['userprofileid'],
                                "contactid": registration_result['contactid'],
                                "role": registration_result['role'],
                                "name": registration_result['firstname']
                            },
                            "provider_info": {
                                "providerid": registration_result['providerid'],
                                "businessname": registration_result['businessname'],
                                "businesscategory": registration_result['businesscategory'],
                                "contactid": registration_result['contactid'],
                                "userprofileid": registration_result['userprofileid'],
                                "city": registration_result['city'],
                                "description": registration_result['description'],
                                "publicsearchprovider": registration_result['publicsearchprovider'],
                                "serviceimageurl": registration_result.get('serviceimageurl'),
                                "plan_code": registration_result.get('plan_code'),
                                "alternatemobile": registration_result.get('alternatemobile'),
                            },
                            "providerservice_info": {
                                "providerservicesid": registration_result['providerservicesid'],
                                "providerid": registration_result['providerid'],
                                "businessname": registration_result['businessname'],
                                "servicedescription": registration_result['description'],
                                "services": registration_result['services'],
                                "businesscategory": registration_result['businesscategory'],
                                "servicecategory": registration_result['servicecategory'],
                                "providerservicesid_orig": registration_result['providerservicesid_orig'],
                                "availability": registration_result.get('availability'),
                                "availability1": registration_result.get('availability1'),
                                "pricerange": registration_result.get('pricerange'),
                                "quantity": registration_result.get('quantity'),
                                "websiteurl": registration_result.get('websiteurl'),
                                "yearsinbusiness": registration_result.get('yearsinbusiness'),
                                "businessnumber": registration_result.get('mobilenumber'),
                                "contactidname": registration_result.get('contactidname'),
                                "userprofileidname": registration_result.get('userprofileidname'),
                                "aiserviceid": registration_result.get('aiserviceid'),
                                "serviceimageurl": registration_result.get('serviceimageurl'),
                                "serviceimageurl1": registration_result.get('serviceimageurl1'),
                                "serviceimageurl2": registration_result.get('serviceimageurl2'),
                                "serviceimageurl3": registration_result.get('serviceimageurl3')
                            },
                            "ai_enhancements": {
                                "original_description": description or '',
                                "enhanced_description": description or '',
                                "generated_services": [],
                                "business_category": business_category_for_db,
                                "service_category": service_category_for_db,
                                "general_category": business_category_for_db,
                            }
                        }

                        logger.info(f"Provider '{business_name}' successfully onboarded with ID: {provider_id}")
                        return response

                    except Exception as e:
                        logger.error(f"Provider registration transaction failed: {e}", exc_info=True)
                        if isinstance(e, (HTTPException, PlanLimitError)):
                            raise
                        raise Exception(f"Provider registration failed: {str(e)}")
        except asyncpg.exceptions.InternalClientError:
            logger.warning(
                "[provider_onboard] Pool connection was reset by the server "
                "(InternalClientError). Retrying with a fresh connection via @with_retry()."
            )
            raise asyncio.TimeoutError("Database connection was reset by the server; retrying.")

    @staticmethod
    @with_retry()
    async def onboard_unverified_provider_in_db(
        business_name: str,
        phone_number: str,
        city: Optional[str] = None,
        description: Optional[str] = None,
        first_name: Optional[str] = None,
        email: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        enhanced_data: Optional[Dict[str, Any]] = None,
        additionaladdressinfo: Optional[str] = None,
        area_location: Optional[str] = None,
        state: Optional[str] = None,
        formatted_address: Optional[str] = None,
        businesslogo: Optional[str] = None,
        marketing_contact_id: Optional[str] = None,
        business_category_input: Optional[str] = None,
        service_category_input: Optional[str] = None,
        servicetype: Optional[str] = None,
        website: Optional[str] = None,
        availability: Optional[str] = None,
        services: Optional[str] = None,
        alternate_mobile: Optional[str] = None,
        source: str = "FOS",
        interested: Optional[str] = None,
        marketing_mobilenumber: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert a non-verified, source-stamped provider and its rows in one transaction, refusing no duplicates.

        Returns:
            Dict of contact, userprofile, provider, providerservice and AI-enhancement info.

        Raises:
            RuntimeError: If the database pool is not initialized.
            HTTPException: 400 only, for a missing business name or mobile number.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        if not business_name or not business_name.strip():
            raise HTTPException(status_code=400, detail="Business name is required")
        if not phone_number or not phone_number.strip():
            raise HTTPException(status_code=400, detail="Mobile number is required")

        if not first_name:
            first_name = business_name.strip()

        business_category_for_db = _normalize_biz_category(business_category_input)
        service_category_for_db = service_category_input or 'General Services'
        desc_for_insert = description or business_name
        services_for_insert = (services or '').strip() or business_name
        initial_statecode = convert_state_name_to_code(state) if state else None
        initial_areaname = area_location or None
        initial_address = formatted_address or city or ''
        marketing_mobile10 = re.sub(r"\D", "", marketing_mobilenumber or "")[-10:] or None

        acquire_start = time.perf_counter()
        try:
            async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
                acquire_ms = (time.perf_counter() - acquire_start) * 1000
                logger.info("[unverified_provider_onboard] EtownDB acquire_time_ms=%.2f", acquire_ms)
            
                async with con.transaction():
                    try:
                        await con.execute(
                            "SELECT pg_advisory_xact_lock(42, hashtext($1))",
                            f"{phone_number}:{business_name.lower().strip()}",
                        )
                        marketing_person_name: Optional[str] = None
                        if marketing_contact_id:
                            marketing_person_name = await con.fetchval(
                                """SELECT firstname FROM etown_contact
                                    WHERE contactid = $1
                                      AND deleted IS DISTINCT FROM 'true'""",
                                marketing_contact_id,
                            )
                            if not marketing_person_name:
                                logger.warning(
                                    "[unverified_provider_onboard] marketing_contact_id %s matched no live "
                                    "contact (or it has no firstname) - '%s' is stored with no "
                                    "marketing_person_name",
                                    marketing_contact_id, business_name,
                                )

                        _phone_owners = await con.fetch(
                            """SELECT c.contactid, c.rolecode,
                                      (SELECT count(*) FROM etown_userprofile           WHERE contactid = c.contactid)
                                    + (SELECT count(*) FROM etown_provider              WHERE contactid = c.contactid)
                                    + (SELECT count(*) FROM etown_providerservices      WHERE contactid = c.contactid)
                                    + (SELECT count(*) FROM etown_providerservices_copy WHERE contactid = c.contactid)
                                    + (SELECT count(*) FROM etown_member                WHERE contactid = c.contactid)
                                      AS profile_refs
                               FROM etown_contact c
                               WHERE c.mobilephone = $1
                                 AND c.deleted IS DISTINCT FROM 'true'""",
                            phone_number,
                        )
                        _existing_contact = await con.fetchrow(
                            """SELECT contactid
                                 FROM etown_contact
                                WHERE mobilephone = $1
                                  AND deleted IS DISTINCT FROM 'true'
                                ORDER BY (SELECT count(*) FROM etown_member      WHERE contactid = etown_contact.contactid)
                                       + (SELECT count(*) FROM etown_userprofile WHERE contactid = etown_contact.contactid) DESC,
                                         createdon ASC NULLS LAST
                                LIMIT 1""",
                            phone_number,
                        )
                        if _phone_owners:
                            logger.info(
                                "[unverified_provider_onboard] Mobile %s already held by contact(s) %s "
                                "— the capture reuses %s rather than minting a new contact "
                                "(marketing_contact_id=%s)",
                                phone_number,
                                [
                                    f"{r['contactid']}(rolecode={r['rolecode']},refs={r['profile_refs']})"
                                    for r in _phone_owners
                                ],
                                _existing_contact['contactid'] if _existing_contact else "a new id",
                                marketing_contact_id,
                            )

                        generated_contact_id = (
                            str(_existing_contact['contactid']) if _existing_contact
                            else str(uuid.uuid4())
                        )
                        userprofile_id = str(uuid.uuid4())
                        provider_id = str(uuid.uuid4())
                        providerservice_id = str(uuid.uuid4())
                        providerservice_orig_id = providerservice_id
                        current_timestamp = datetime.now(timezone.utc).replace(tzinfo=None)

                        register_query = f"""
                            WITH {CONTACT_PLAN_CTE.format(contact='$1')},
                            inserted_contact AS (
                                INSERT INTO etown_contact (
                                    contactid, firstname, mobilephone, email, userlogin,
                                    createdon, rolecode, rolecodename, city, content_vector, address, statecode, latitude, longitude, publicsearchstatus, verified,
                                    alternatemobile
                                )
                                VALUES ($1, $2, $3, $23, 'mobile', $4, 1, 'Provider', $5, $22::vector, $24, $16, $17, $18, 'unverified', FALSE,
                                        $31)
                                ON CONFLICT (contactid) DO UPDATE SET
                                    content_vector = EXCLUDED.content_vector,
                                    address = COALESCE(EXCLUDED.address, etown_contact.address),
                                    statecode = COALESCE(EXCLUDED.statecode, etown_contact.statecode),
                                    latitude = COALESCE(EXCLUDED.latitude, etown_contact.latitude),
                                    longitude = COALESCE(EXCLUDED.longitude, etown_contact.longitude),
                                    alternatemobile = COALESCE(etown_contact.alternatemobile, EXCLUDED.alternatemobile)
                                RETURNING contactid, firstname, mobilephone, email, createdon, userlogin, city,
                                          alternatemobile
                            ),
                            inserted_userprofile AS (
                                INSERT INTO etown_userprofile (
                                    userprofileid, contactid, contactidname,
                                    role, rolename, name, mobilenumber, createdon, verified
                                )
                                SELECT $6, ic.contactid, $2,
                                       1, 'Provider', $2, $3, $4, FALSE
                                FROM inserted_contact ic
                                ON CONFLICT (userprofileid) DO UPDATE SET
                                    contactidname = EXCLUDED.contactidname,
                                    name = EXCLUDED.name,
                                    mobilenumber = EXCLUDED.mobilenumber,
                                    createdon = EXCLUDED.createdon,
                                    verified = FALSE
                                RETURNING userprofileid, contactid, role, name, mobilenumber
                            ),
                            inserted_provider AS (
                                INSERT INTO etown_provider (
                                    providerid, businessname, businesscategory, contactid, userprofileid,
                                    contactidname, userprofileidname, firstname, email, mobilenumber, city, description,
                                    providertype, publicsearchprovider, role, rolename, creationdate,
                                    areaname, townname, statecode, latitude, longitude, content_vector, address,
                                    serviceimageurl, additionaladdressinfo, source, servicetype, websiteurl, availability,
                                    marketing_person_name, alternatemobile, interested, marketing_mobilenumber,
                                    {CONTACT_PLAN_COLUMNS}
                                )
                                SELECT $7, $13, $11, iup.contactid, iup.userprofileid,
                                       $2, $2, $2, $23, $3, $5, $8,
                                       'etown_nonverified', 'non-verified', 1, 'Provider', $4,
                                       $14, $15, $16, $17, $18, $19::vector, $24,
                                       $25, $26, $32, $27, $28, $29,
                                       $30, $31, $33, $34,
                                       {CONTACT_PLAN_VALUES}
                                FROM inserted_userprofile iup
                                ON CONFLICT (providerid) DO UPDATE SET
                                    businessname = EXCLUDED.businessname,
                                    businesscategory = EXCLUDED.businesscategory,
                                    contactidname = EXCLUDED.contactidname,
                                    userprofileidname = EXCLUDED.userprofileidname,
                                    description = EXCLUDED.description,
                                    city = EXCLUDED.city,
                                    areaname = EXCLUDED.areaname,
                                    townname = EXCLUDED.townname,
                                    statecode = EXCLUDED.statecode,
                                    latitude = EXCLUDED.latitude,
                                    longitude = EXCLUDED.longitude,
                                    content_vector = EXCLUDED.content_vector,
                                    address = EXCLUDED.address,
                                    providertype = EXCLUDED.providertype,
                                    publicsearchprovider = EXCLUDED.publicsearchprovider,
                                    role = EXCLUDED.role,
                                    rolename = EXCLUDED.rolename,
                                    serviceimageurl = COALESCE(EXCLUDED.serviceimageurl, etown_provider.serviceimageurl),
                                    additionaladdressinfo = COALESCE(EXCLUDED.additionaladdressinfo, etown_provider.additionaladdressinfo),
                                    source = EXCLUDED.source,
                                    servicetype = COALESCE(EXCLUDED.servicetype, etown_provider.servicetype),
                                    websiteurl = COALESCE(EXCLUDED.websiteurl, etown_provider.websiteurl),
                                    availability = COALESCE(EXCLUDED.availability, etown_provider.availability),
                                    marketing_person_name = COALESCE(EXCLUDED.marketing_person_name,
                                                                     etown_provider.marketing_person_name),
                                    alternatemobile = COALESCE(etown_provider.alternatemobile, EXCLUDED.alternatemobile),
                                    interested = COALESCE(EXCLUDED.interested, etown_provider.interested),
                                    marketing_mobilenumber = COALESCE(EXCLUDED.marketing_mobilenumber,
                                                                      etown_provider.marketing_mobilenumber)
                                RETURNING providerid, businessname, businesscategory, contactid, userprofileid,
                                         city, description, publicsearchprovider, contactidname, userprofileidname,
                                         serviceimageurl, plan_code, alternatemobile, interested
                            ),

                            inserted_providerservice_copy AS (
                                INSERT INTO etown_providerservices_copy (
                                    providerservicesid, businessname, businesscategory, servicecategory, generalcategory,
                                    providerid, contactid, contactidname, userprofileid, userprofileidname, email, mobilenumber,
                                    servicedescription, services, city, address,
                                    providertype, publicsearchprovider, createdon, content_vector,
                                    areaname, statecode, latitude, longitude,
                                    serviceimageurl, additionaladdressinfo, source, websiteurl, availability
                                )
                                SELECT $9, $13, $11, $12, NULL,
                                       ip.providerid, ip.contactid, ip.contactidname, ip.userprofileid, ip.userprofileidname, $23, $3,
                                       $8, $10, ip.city, $24,
                                       'etown_nonverified', 'non-verified', $4, $20::vector,
                                       $14, $16, $17, $18,
                                       $25, $26, $32, $28, $29
                                FROM inserted_provider ip
                                ON CONFLICT (providerservicesid) DO UPDATE SET
                                    businessname = EXCLUDED.businessname,
                                    businesscategory = EXCLUDED.businesscategory,
                                    servicecategory = EXCLUDED.servicecategory,
                                    generalcategory = EXCLUDED.generalcategory,
                                    servicedescription = EXCLUDED.servicedescription,
                                    services = EXCLUDED.services,
                                    contactidname = EXCLUDED.contactidname,
                                    userprofileidname = EXCLUDED.userprofileidname,
                                    email = EXCLUDED.email,
                                    mobilenumber = EXCLUDED.mobilenumber,
                                    city = EXCLUDED.city,
                                    content_vector = EXCLUDED.content_vector,
                                    address = EXCLUDED.address,
                                    areaname = EXCLUDED.areaname,
                                    statecode = EXCLUDED.statecode,
                                    latitude = EXCLUDED.latitude,
                                    longitude = EXCLUDED.longitude,
                                    providertype = EXCLUDED.providertype,
                                    publicsearchprovider = EXCLUDED.publicsearchprovider,
                                    serviceimageurl = COALESCE(EXCLUDED.serviceimageurl, etown_providerservices_copy.serviceimageurl),
                                    additionaladdressinfo = COALESCE(EXCLUDED.additionaladdressinfo, etown_providerservices_copy.additionaladdressinfo),
                                    websiteurl = COALESCE(EXCLUDED.websiteurl, etown_providerservices_copy.websiteurl),
                                    availability = COALESCE(EXCLUDED.availability, etown_providerservices_copy.availability)
                                RETURNING providerservicesid, providerid, businessname, servicedescription, services,
                                         businesscategory, servicecategory, availability
                            ),
                            inserted_providerservice AS (
                                INSERT INTO etown_providerservices (
                                    providerservicesid, businessname, businesscategory, servicecategory, generalcategory,
                                    providerid, contactid, contactidname, userprofileid, userprofileidname, email, mobilenumber,
                                    servicedescription, services, city, address,
                                    providertype, publicsearchprovider, createdon,
                                    areaname, statecode, latitude, longitude,
                                    serviceimageurl, additionaladdressinfo, source, websiteurl, availability
                                )
                                SELECT $21, $13, $11, $12, NULL,
                                       ip.providerid, ip.contactid, ip.contactidname, ip.userprofileid, ip.userprofileidname, $23, $3,
                                       $8, $10, ip.city, $24,
                                       'etown_nonverified', 'non-verified', $4,
                                       $14, $16, $17, $18,
                                       $25, $26, $32, $28, $29
                                FROM inserted_provider ip
                                ON CONFLICT (providerservicesid) DO UPDATE SET
                                    businessname = EXCLUDED.businessname,
                                    businesscategory = EXCLUDED.businesscategory,
                                    servicecategory = EXCLUDED.servicecategory,
                                    generalcategory = EXCLUDED.generalcategory,
                                    servicedescription = EXCLUDED.servicedescription,
                                    services = EXCLUDED.services,
                                    contactidname = EXCLUDED.contactidname,
                                    userprofileidname = EXCLUDED.userprofileidname,
                                    email = EXCLUDED.email,
                                    mobilenumber = EXCLUDED.mobilenumber,
                                    city = EXCLUDED.city,
                                    address = EXCLUDED.address,
                                    areaname = EXCLUDED.areaname,
                                    statecode = EXCLUDED.statecode,
                                    latitude = EXCLUDED.latitude,
                                    longitude = EXCLUDED.longitude,
                                    providertype = EXCLUDED.providertype,
                                    publicsearchprovider = EXCLUDED.publicsearchprovider,
                                    serviceimageurl = COALESCE(EXCLUDED.serviceimageurl, etown_providerservices.serviceimageurl),
                                    additionaladdressinfo = COALESCE(EXCLUDED.additionaladdressinfo, etown_providerservices.additionaladdressinfo),
                                    websiteurl = COALESCE(EXCLUDED.websiteurl, etown_providerservices.websiteurl),
                                    availability = COALESCE(EXCLUDED.availability, etown_providerservices.availability)
                                RETURNING providerservicesid, providerid, businessname, servicedescription, services,
                                         businesscategory, servicecategory
                            )
                            SELECT
                                ic.contactid, ic.firstname, ic.mobilephone, ic.email, ic.createdon, ic.userlogin, ic.city,
                                ic.alternatemobile,
                                iup.userprofileid, iup.role,
                                ip.providerid, ip.businessname, ip.businesscategory, ip.description, ip.publicsearchprovider,
                                ip.serviceimageurl, ip.plan_code, ip.interested,
                                ipsc.providerservicesid, ipsc.services, ipsc.servicecategory, ipsc.availability,
                                ips.providerservicesid as providerservicesid_orig
                            FROM inserted_contact ic
                            JOIN inserted_userprofile iup ON iup.contactid = ic.contactid
                            JOIN inserted_provider ip ON ip.contactid = ic.contactid
                            JOIN inserted_providerservice_copy ipsc ON ipsc.providerid = ip.providerid
                            JOIN inserted_providerservice ips ON ips.providerid = ip.providerid
                        """

                        query_start = time.perf_counter()
                        registration_result = await con.fetchrow(
                            register_query,
                            generated_contact_id,
                            first_name,
                            phone_number,
                            current_timestamp,
                            city or '',
                            userprofile_id,
                            provider_id,
                            desc_for_insert,
                            providerservice_id,
                            services_for_insert,
                            business_category_for_db,
                            service_category_for_db,
                            business_name,
                            initial_areaname,
                            None,
                            initial_statecode,
                            latitude,
                            longitude,
                            None,
                            None,
                            providerservice_orig_id,
                            None,
                            email,
                            initial_address,
                            None,
                            additionaladdressinfo,
                            servicetype,
                            website,
                            availability,
                            marketing_person_name,
                            alternate_mobile,
                            source,
                            interested,
                            marketing_mobile10,
                        )
                        query_ms = (time.perf_counter() - query_start) * 1000
                        logger.info("[unverified_provider_onboard] EtownDB query_time_ms=%.2f", query_ms)

                        if not registration_result:
                            raise Exception("Provider registration failed - no result returned")
                    
                        response = {
                            "contact_info": {
                                "contactid": registration_result['contactid'],
                                "firstname": registration_result['firstname'],
                                "mobilephone": registration_result['mobilephone'],
                                "email": registration_result.get('email'),
                                "createdon": registration_result['createdon'].isoformat() + "Z" if registration_result['createdon'] else None,
                                "userlogin": registration_result['userlogin'],
                                "city": registration_result['city'],
                                "alternatemobile": registration_result.get('alternatemobile'),
                            },
                            "userprofile_info": {
                                "userprofileid": registration_result['userprofileid'],
                                "contactid": registration_result['contactid'],
                                "role": registration_result['role'],
                                "name": registration_result['firstname']
                            },
                            "provider_info": {
                                "providerid": registration_result['providerid'],
                                "businessname": registration_result['businessname'],
                                "businesscategory": registration_result['businesscategory'],
                                "contactid": registration_result['contactid'],
                                "userprofileid": registration_result['userprofileid'],
                                "city": registration_result['city'],
                                "description": registration_result['description'],
                                "publicsearchprovider": registration_result['publicsearchprovider'],
                                "serviceimageurl": registration_result.get('serviceimageurl'),
                                "plan_code": registration_result.get('plan_code'),
                                "alternatemobile": registration_result.get('alternatemobile'),
                                "interested": registration_result.get('interested'),
                            },
                            "providerservice_info": {
                                "providerservicesid": registration_result['providerservicesid'],
                                "providerid": registration_result['providerid'],
                                "businessname": registration_result['businessname'],
                                "servicedescription": registration_result['description'],
                                "services": registration_result['services'],
                                "businesscategory": registration_result['businesscategory'],
                                "servicecategory": registration_result['servicecategory'],
                                "providerservicesid_orig": registration_result['providerservicesid_orig'],
                                "availability": registration_result.get('availability'),
                                "availability1": registration_result.get('availability1'),
                                "pricerange": registration_result.get('pricerange'),
                                "quantity": registration_result.get('quantity'),
                                "websiteurl": registration_result.get('websiteurl'),
                                "yearsinbusiness": registration_result.get('yearsinbusiness'),
                                "businessnumber": registration_result.get('mobilenumber'),
                                "contactidname": registration_result.get('contactidname'),
                                "userprofileidname": registration_result.get('userprofileidname'),
                                "aiserviceid": registration_result.get('aiserviceid'),
                                "serviceimageurl": registration_result.get('serviceimageurl'),
                                "serviceimageurl1": registration_result.get('serviceimageurl1'),
                                "serviceimageurl2": registration_result.get('serviceimageurl2'),
                                "serviceimageurl3": registration_result.get('serviceimageurl3')
                            },
                            "ai_enhancements": {
                                "original_description": description or '',
                                "enhanced_description": description or '',
                                "generated_services": [],
                                "business_category": business_category_for_db,
                                "service_category": service_category_for_db,
                                "general_category": business_category_for_db,
                            }
                        }

                        logger.info(f"Unverified provider '{business_name}' successfully onboarded with ID: {provider_id}")
                        return response

                    except Exception as e:
                        logger.error(f"Unverified provider registration transaction failed: {e}", exc_info=True)
                        if isinstance(e, (HTTPException, PlanLimitError)):
                            raise
                        raise Exception(f"Unverified provider registration failed: {str(e)}")
        except asyncpg.exceptions.InternalClientError:
            logger.warning(
                "[unverified_provider_onboard] Pool connection was reset by the server "
                "(InternalClientError). Retrying with a fresh connection via @with_retry()."
            )
            raise asyncio.TimeoutError("Database connection was reset by the server; retrying.")

    @staticmethod
    @with_retry()
    async def update_provider_onboard_in_db(
        provider_id: str,
        contact_id: Optional[str] = None,
        business_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        description: Optional[str] = None,
        city: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        additionaladdressinfo: Optional[str] = None,
        area_location: Optional[str] = None,
        state: Optional[str] = None,
        formatted_address: Optional[str] = None,
        businesslogo: Optional[str] = None,
        business_category_input: Optional[str] = None,
        service_category_input: Optional[str] = None,
        servicetype: Optional[str] = None,
        services: Optional[str] = None,
        email: Optional[str] = None,
        website: Optional[str] = None,
        availability: Optional[str] = None,
        alternate_mobile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply an onboarding edit to a provider in one transaction, marking it verified and merging contacts if needed.

        Returns:
            Onboard-shaped dict plus service row ids, embedding_text_changed and the contact-merge outcome.

        Raises:
            HTTPException: 400 without provider_id, 404 missing provider or contact, 409 taken alternate mobile.
            RuntimeError: If the database pool is not initialised.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        if not provider_id or not str(provider_id).strip():
            raise HTTPException(
                status_code=400,
                detail="providerid is required when action_mode is 'edit'",
            )
        provider_id = str(provider_id).strip()

        def _keep_if_null(value: Optional[str]) -> Optional[str]:
            """Treat None / '' / the literal 'Null' as 'keep the existing value'."""
            if value is None:
                return None
            text = str(value).strip()
            return None if not text or text.lower() == "null" else text

        business_name = _keep_if_null(business_name)
        phone_number = _keep_if_null(phone_number)
        description = _keep_if_null(description)
        city = _keep_if_null(city)
        additionaladdressinfo = _keep_if_null(additionaladdressinfo)
        area_location = _keep_if_null(area_location)
        state = _keep_if_null(state)
        formatted_address = _keep_if_null(formatted_address)
        business_category_input = _keep_if_null(business_category_input)
        service_category_input = _keep_if_null(service_category_input)
        servicetype = _keep_if_null(servicetype)
        services = _keep_if_null(services)
        email = _keep_if_null(email)
        website = _keep_if_null(website)
        availability = _keep_if_null(availability)
        alternate_mobile = _keep_if_null(alternate_mobile)

        existing = await Database.fetchrow(
            """SELECT providerid, contactid, userprofileid
               FROM etown_provider
               WHERE providerid = $1
               LIMIT 1""",
            provider_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Provider not found")

        real_provider_id = str(existing["providerid"])
        real_contact_id = str(existing["contactid"]) if existing["contactid"] else contact_id
        real_userprofile_id = str(existing["userprofileid"]) if existing["userprofileid"] else None

        new_contact_id: Optional[str] = None
        new_contact_name: Optional[str] = None
        if contact_id and real_contact_id and contact_id != real_contact_id:
            new_contact_row = await Database.fetchrow(
                "SELECT contactid, firstname FROM etown_contact WHERE contactid = $1 LIMIT 1",
                contact_id,
            )
            if not new_contact_row:
                raise HTTPException(
                    status_code=404,
                    detail="contact_id not found — cannot move the provider to a contact that does not exist",
                )
            new_contact_id = str(new_contact_row["contactid"])
            new_contact_name = new_contact_row["firstname"]

        effective_contact_id = new_contact_id or real_contact_id

        own_contact_ids = [c for c in {effective_contact_id, real_contact_id} if c]
        orphan_phone_contact_ids: List[str] = []
        if phone_number and own_contact_ids:
            phone_owners = await Database.fetch(
                """SELECT c.contactid,
                          (SELECT count(*) FROM etown_userprofile           WHERE contactid = c.contactid)
                        + (SELECT count(*) FROM etown_provider              WHERE contactid = c.contactid)
                        + (SELECT count(*) FROM etown_providerservices      WHERE contactid = c.contactid)
                        + (SELECT count(*) FROM etown_providerservices_copy WHERE contactid = c.contactid)
                        + (SELECT count(*) FROM etown_member                WHERE contactid = c.contactid)
                          AS profile_refs
                   FROM etown_contact c
                   WHERE c.mobilephone = $1

                     AND c.deleted IS DISTINCT FROM 'true'
                     AND c.contactid <> ALL($2::text[])""",
                phone_number, own_contact_ids,
            )
            for row in phone_owners:
                if row["profile_refs"]:
                    logger.warning(
                        "[provider_onboard_edit] Mobile %s also stays on contact %s "
                        "(%d profile row(s)) — provider %s is taking the same number, so it is "
                        "now shared across contacts and login by that number is ambiguous",
                        phone_number, row["contactid"], row["profile_refs"], real_provider_id,
                    )
                else:
                    orphan_phone_contact_ids.append(str(row["contactid"]))
            if orphan_phone_contact_ids:
                logger.info(
                    "[provider_onboard_edit] Mobile %s was also on orphan contact(s) %s — "
                    "deleting them for provider %s",
                    phone_number, orphan_phone_contact_ids, real_provider_id,
                )

        if alternate_mobile:
            alt_owner = await Database.fetchrow(
                _MOBILE_HELD_BY_OTHER_SQL, alternate_mobile, effective_contact_id,
            )
            if alt_owner:
                logger.warning(
                    "[provider_onboard_edit] Alternate mobile %s already held by contact %s "
                    "-- edit refused (provider=%s, contact=%s)",
                    alternate_mobile, alt_owner["contactid"], real_provider_id,
                    effective_contact_id,
                )
                raise HTTPException(
                    status_code=409,
                    detail=_ALTERNATE_MOBILE_TAKEN_DETAIL,
                )

        final_image_url: Optional[str] = None
        if businesslogo and businesslogo.strip().lower() not in ("null", "no image"):
            if _is_stored_media_url(businesslogo):
                final_image_url = businesslogo.strip()
            else:
                try:
                    logo_bytes = _to_bytes(businesslogo)
                    if logo_bytes:
                        container_name = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
                        blob_name = f"{effective_contact_id}/{_current_timestamp_prefix()}_ProfileImg.png"
                        final_image_url = await _upload_blob_bytes(
                            container_name, blob_name, logo_bytes, "image/png"
                        ) or None
                except Exception as e:
                    logger.warning(
                        "[provider_onboard_edit] Logo upload failed for provider=%s: %s",
                        real_provider_id, e,
                    )
                    final_image_url = None

        business_category_for_db = (
            _normalize_biz_category(business_category_input) if business_category_input else None
        )
        statecode = convert_state_name_to_code(state) if state else None
        address = formatted_address
        final_city = city
        final_availability = availability
        final_services = services
        lat_text = str(latitude) if latitude is not None else None
        lon_text = str(longitude) if longitude is not None else None

        acquire_start = time.perf_counter()
        try:
            async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
                logger.info(
                    "[provider_onboard_edit] EtownDB acquire_time_ms=%.2f",
                    (time.perf_counter() - acquire_start) * 1000,
                )
                query_start = time.perf_counter()
                old_contact_deleted = False
                async with con.transaction():
                    if new_contact_id:
                        stranded_refs = await con.fetchval(
                            """SELECT
                                    (SELECT count(*) FROM etown_userprofile
                                      WHERE contactid = $1 AND userprofileid IS DISTINCT FROM $2)
                                  + (SELECT count(*) FROM etown_provider
                                      WHERE contactid = $1 AND providerid    IS DISTINCT FROM $3)
                                  + (SELECT count(*) FROM etown_providerservices
                                      WHERE contactid = $1 AND providerid    IS DISTINCT FROM $3)
                                  + (SELECT count(*) FROM etown_providerservices_copy
                                      WHERE contactid = $1 AND providerid    IS DISTINCT FROM $3)
                                  + (SELECT count(*) FROM etown_member WHERE contactid = $1)""",
                            real_contact_id, real_userprofile_id, real_provider_id,
                        )

                        await con.execute(
                            "DELETE FROM etown_contact WHERE contactid = $1", real_contact_id
                        )
                        old_contact_deleted = True

                        for _table, _key in (
                            ("etown_userprofile", "userprofileid"),
                            ("etown_provider", "providerid"),
                            ("etown_providerservices", "providerid"),
                            ("etown_providerservices_copy", "providerid"),
                        ):
                            _key_value = (
                                real_userprofile_id if _key == "userprofileid" else real_provider_id
                            )
                            if not _key_value:
                                continue
                            await con.execute(
                                f"""UPDATE {_table} SET
                                        contactid     = $2,
                                        contactidname = COALESCE($3, contactidname)
                                    WHERE {_key} = $1""",
                                _key_value, new_contact_id, new_contact_name,
                            )

                        logger.info(
                            "[provider_onboard_edit] Provider %s moved from contact %s to %s; "
                            "old contact deleted",
                            real_provider_id, real_contact_id, new_contact_id,
                        )
                        if stranded_refs:
                            logger.warning(
                                "[provider_onboard_edit] Deleted contact %s still had %d profile "
                                "row(s) belonging to other accounts — those now point at a "
                                "missing contact (provider=%s)",
                                real_contact_id, stranded_refs, real_provider_id,
                            )

                    await con.execute(
                        """UPDATE etown_provider SET
                            businessname          = COALESCE($2,  businessname),
                            description           = COALESCE($3,  description),
                            businesscategory      = COALESCE($4,  businesscategory),
                            email                 = COALESCE($5,  email),
                            mobilenumber          = COALESCE($6,  mobilenumber),
                            city                  = COALESCE($7,  city),
                            townname              = COALESCE($7,  townname),
                            address               = COALESCE($8,  address),
                            areaname              = COALESCE($9,  areaname),
                            statecode             = COALESCE($10, statecode),
                            latitude              = COALESCE($11, latitude),
                            longitude             = COALESCE($12, longitude),
                            additionaladdressinfo = COALESCE($13, additionaladdressinfo),
                            servicetype           = COALESCE($14, servicetype),
                            websiteurl            = COALESCE($15, websiteurl),
                            availability          = COALESCE($16, availability),
                            serviceimageurl       = COALESCE($17, serviceimageurl),
                            alternatemobile       = COALESCE($18, alternatemobile),
                            providertype          = 'etown_verified',
                            publicsearchprovider  = 'verified',
                            source                = 'Onboard',
                            whatsapp              = CASE
                                                        WHEN $6 IS NOT NULL AND $6 IS DISTINCT FROM mobilenumber
                                                        THEN true ELSE whatsapp
                                                    END,
                            modifieddate          = NOW()
                        WHERE providerid = $1""",
                        real_provider_id,
                        business_name, description, business_category_for_db,
                        email, phone_number, final_city, address,
                        area_location, statecode, latitude, longitude,
                        additionaladdressinfo, servicetype, website,
                        final_availability, final_image_url, alternate_mobile,
                    )

                    await con.execute(
                        """UPDATE etown_providerservices SET
                            businessname          = COALESCE($2,  businessname),
                            servicedescription    = COALESCE($3,  servicedescription),
                            businesscategory      = COALESCE($4,  businesscategory),
                            servicecategory       = COALESCE($5,  servicecategory),
                            email                 = COALESCE($6,  email),
                            mobilenumber          = COALESCE($7,  mobilenumber),
                            city                  = COALESCE($8,  city),
                            address               = COALESCE($9,  address),
                            areaname              = COALESCE($10, areaname),
                            statecode             = COALESCE($11, statecode),
                            latitude              = COALESCE($12, latitude),
                            longitude             = COALESCE($13, longitude),
                            additionaladdressinfo = COALESCE($14, additionaladdressinfo),
                            websiteurl            = COALESCE($15, websiteurl),
                            services              = COALESCE($16, services),
                            availability          = COALESCE($17, availability),
                            serviceimageurl       = COALESCE($18, serviceimageurl),
                            providertype          = 'etown_verified',
                            publicsearchprovider  = 'verified',
                            source                = 'Onboard',
                            modifieddate          = NOW()
                        WHERE providerid = $1""",
                        real_provider_id,
                        business_name, description, business_category_for_db,
                        service_category_input, email, phone_number, final_city, address,
                        area_location, statecode, lat_text, lon_text,
                        additionaladdressinfo, website, final_services,
                        final_availability, final_image_url,
                    )

                    await con.execute(
                        """UPDATE etown_providerservices_copy SET
                            businessname          = COALESCE($2,  businessname),
                            servicedescription    = COALESCE($3,  servicedescription),
                            businesscategory      = COALESCE($4,  businesscategory),
                            servicecategory       = COALESCE($5,  servicecategory),
                            email                 = COALESCE($6,  email),
                            mobilenumber          = COALESCE($7,  mobilenumber),
                            city                  = COALESCE($8,  city),
                            address               = COALESCE($9,  address),
                            areaname              = COALESCE($10, areaname),
                            statecode             = COALESCE($11, statecode),
                            latitude              = COALESCE($12, latitude),
                            longitude             = COALESCE($13, longitude),
                            additionaladdressinfo = COALESCE($14, additionaladdressinfo),
                            websiteurl            = COALESCE($15, websiteurl),
                            services              = COALESCE($16, services),
                            availability          = COALESCE($17, availability),
                            serviceimageurl       = COALESCE($18, serviceimageurl),
                            providertype          = 'etown_verified',
                            publicsearchprovider  = 'verified',
                            source                = 'Onboard',
                            modifieddate          = NOW()
                        WHERE providerid = $1""",
                        real_provider_id,
                        business_name, description, business_category_for_db,
                        service_category_input, email, phone_number, final_city, address,
                        area_location, statecode, latitude, longitude,
                        additionaladdressinfo, website, final_services,
                        final_availability, final_image_url,
                    )

                    for _orphan_id in orphan_phone_contact_ids:
                        deleted_orphan = await con.fetchval(
                            """DELETE FROM etown_contact
                               WHERE contactid = $1
                                 AND NOT EXISTS (SELECT 1 FROM etown_userprofile           WHERE contactid = $1)
                                 AND NOT EXISTS (SELECT 1 FROM etown_provider              WHERE contactid = $1)
                                 AND NOT EXISTS (SELECT 1 FROM etown_providerservices      WHERE contactid = $1)
                                 AND NOT EXISTS (SELECT 1 FROM etown_providerservices_copy WHERE contactid = $1)
                                 AND NOT EXISTS (SELECT 1 FROM etown_member                WHERE contactid = $1)
                               RETURNING contactid""",
                            _orphan_id,
                        )
                        if not deleted_orphan:
                            logger.warning(
                                "[provider_onboard_edit] Orphan contact %s gained a profile row "
                                "mid-edit — kept, and it still holds mobile %s (provider=%s)",
                                _orphan_id, phone_number, real_provider_id,
                            )

                    if effective_contact_id:
                        await con.execute(
                            """UPDATE etown_contact SET
                                mobilephone           = COALESCE($2, mobilephone),
                                email                 = COALESCE($3, email),
                                city                  = COALESCE($4, city),
                                address               = COALESCE($5, address),
                                statecode             = COALESCE($6, statecode),
                                latitude              = COALESCE($7, latitude),
                                longitude             = COALESCE($8, longitude),
                                additionaladdressinfo = COALESCE($9, additionaladdressinfo),
                                alternatemobile       = COALESCE($10, alternatemobile),
                                verified              = TRUE,
                                publicsearchstatus    = 'verified',
                                modifieddate          = NOW()
                            WHERE contactid = $1""",
                            effective_contact_id,
                            phone_number, email, final_city, address,
                            statecode, latitude, longitude, additionaladdressinfo,
                            alternate_mobile,
                        )
                    if real_userprofile_id:
                        await con.execute(
                            """UPDATE etown_userprofile SET
                                mobilenumber = COALESCE($2, mobilenumber),
                                verified     = TRUE,
                                modifieddate = NOW()
                            WHERE userprofileid = $1""",
                            real_userprofile_id, phone_number,
                        )

                    updated = await con.fetchrow(
                        """SELECT
                                p.providerid, p.businessname, p.businesscategory, p.description,
                                p.publicsearchprovider, p.serviceimageurl, p.contactid, p.userprofileid,
                                c.firstname, c.mobilephone, c.email, c.createdon, c.userlogin, c.city,
                                c.alternatemobile,
                                p.alternatemobile AS provider_alternatemobile,
                                up.role, up.name AS userprofile_name,
                                psc.providerservicesid, psc.services, psc.servicecategory,
                                psc.servicedescription, psc.availability, psc.pricerange, psc.quantity,
                                psc.websiteurl, psc.yearsinbusiness, psc.mobilenumber AS service_mobilenumber,
                                psc.contactidname, psc.userprofileidname,
                                psc.serviceimageurl AS service_imageurl,
                                psc.serviceimageurl1, psc.serviceimageurl2, psc.serviceimageurl3,
                                ps.providerservicesid AS providerservicesid_orig
                           FROM etown_provider p
                           LEFT JOIN etown_contact c            ON c.contactid       = p.contactid
                           LEFT JOIN etown_userprofile up       ON up.userprofileid  = p.userprofileid
                           LEFT JOIN etown_providerservices_copy psc ON psc.providerid = p.providerid
                           LEFT JOIN etown_providerservices ps       ON ps.providerid  = p.providerid
                           WHERE p.providerid = $1
                           LIMIT 1""",
                        real_provider_id,
                    )
                    svc_rows = await con.fetch(
                        "SELECT providerservicesid FROM etown_providerservices WHERE providerid = $1",
                        real_provider_id,
                    )
                    svc_copy_rows = await con.fetch(
                        "SELECT providerservicesid FROM etown_providerservices_copy WHERE providerid = $1",
                        real_provider_id,
                    )
                logger.info(
                    "[provider_onboard_edit] EtownDB query_time_ms=%.2f",
                    (time.perf_counter() - query_start) * 1000,
                )
        except asyncpg.exceptions.InternalClientError:
            logger.warning(
                "[provider_onboard_edit] Pool connection was reset by the server "
                "(InternalClientError). Retrying with a fresh connection via @with_retry()."
            )
            raise asyncio.TimeoutError("Database connection was reset by the server; retrying.")

        if not updated:
            raise HTTPException(status_code=404, detail="Provider not found")

        final_business_name = updated["businessname"] or business_name or ''
        final_description = updated["description"] or ''
        final_biz_category = updated["businesscategory"] or ''
        final_svc_category = updated["servicecategory"] or ''

        response = {
            "contact_info": {
                "contactid": str(updated["contactid"] or effective_contact_id or ''),
                "firstname": updated["firstname"],
                "mobilephone": updated["mobilephone"],
                "email": updated["email"],
                "createdon": updated["createdon"].isoformat() + "Z" if updated["createdon"] else None,
                "userlogin": updated["userlogin"],
                "city": updated["city"],
                "alternatemobile": updated["alternatemobile"],
            },
            "userprofile_info": {
                "userprofileid": str(updated["userprofileid"] or real_userprofile_id or ''),
                "contactid": str(updated["contactid"] or effective_contact_id or ''),
                "role": updated["role"] if updated["role"] is not None else 1,
                "name": updated["userprofile_name"] or final_business_name,
            },
            "provider_info": {
                "providerid": real_provider_id,
                "businessname": final_business_name,
                "businesscategory": updated["businesscategory"],
                "contactid": str(updated["contactid"] or effective_contact_id or ''),
                "userprofileid": str(updated["userprofileid"] or real_userprofile_id or ''),
                "city": updated["city"],
                "description": updated["description"],
                "publicsearchprovider": updated["publicsearchprovider"] or 'verified',
                "serviceimageurl": updated["serviceimageurl"],
                "alternatemobile": updated["provider_alternatemobile"],
            },
            "providerservice_info": {
                "providerservicesid": str(updated["providerservicesid"] or ''),
                "providerid": real_provider_id,
                "businessname": final_business_name,
                "servicedescription": updated["servicedescription"] or final_description,
                "services": updated["services"] or '',
                "businesscategory": final_biz_category,
                "servicecategory": final_svc_category,
                "providerservicesid_orig": str(updated["providerservicesid_orig"] or ''),
                "availability": updated["availability"],
                "availability1": updated["availability"],
                "pricerange": updated["pricerange"],
                "quantity": updated["quantity"],
                "websiteurl": updated["websiteurl"],
                "yearsinbusiness": updated["yearsinbusiness"],
                "businessnumber": updated["service_mobilenumber"],
                "contactidname": updated["contactidname"],
                "userprofileidname": updated["userprofileidname"],
                "serviceimageurl": updated["service_imageurl"],
                "serviceimageurl1": updated["serviceimageurl1"],
                "serviceimageurl2": updated["serviceimageurl2"],
                "serviceimageurl3": updated["serviceimageurl3"],
            },
            "ai_enhancements": {
                "original_description": description or final_description,
                "enhanced_description": final_description,
                "generated_services": [
                    s.strip() for s in (updated["services"] or '').split(',') if s.strip()
                ],
                "business_category": final_biz_category,
                "service_category": final_svc_category,
                "general_category": final_biz_category,
            },
            "providerservices_ids": [str(r["providerservicesid"]) for r in svc_rows],
            "providerservices_copy_ids": [str(r["providerservicesid"]) for r in svc_copy_rows],
            "contact_repointed_from": real_contact_id if new_contact_id else None,
            "old_contact_deleted": old_contact_deleted,
            "contactid": effective_contact_id,
            "embedding_text_changed": bool(new_contact_id) or any(
                v is not None for v in (business_name, description, final_city, address)
            ),
            "resolved_fields": {
                "business_name": business_name,
                "description": description,
                "business_category": business_category_for_db,
                "service_category": service_category_input,
                "city": final_city,
                "address": address,
                "areaname": area_location,
                "statecode": statecode,
                "latitude": lat_text,
                "longitude": lon_text,
                "additionaladdressinfo": additionaladdressinfo,
                "servicetype": servicetype,
                "websiteurl": website,
                "availability": final_availability,
                "services": final_services,
                "email": email,
                "mobilenumber": phone_number,
                "serviceimageurl": updated["serviceimageurl"],
            },
        }

        logger.info(
            "[provider_onboard_edit] Provider updated: providerid=%s business='%s'",
            real_provider_id, final_business_name,
        )
        return response

    @staticmethod
    @with_retry()
    async def update_unverified_provider_in_db(
        phone_number: str,
        business_name: Optional[str] = None,
        description: Optional[str] = None,
        city: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        additionaladdressinfo: Optional[str] = None,
        area_location: Optional[str] = None,
        state: Optional[str] = None,
        formatted_address: Optional[str] = None,
        businesslogo: Optional[str] = None,
        marketing_contact_id: Optional[str] = None,
        business_category_input: Optional[str] = None,
        service_category_input: Optional[str] = None,
        servicetype: Optional[str] = None,
        services: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Edit the single live non-verified provider on a mobile number in one transaction, leaving it non-verified.

        Returns:
            Onboard-shaped dict plus service row ids, embedding_text_changed and resolved_fields.

        Raises:
            HTTPException: 400 for under ten digits, 404 when no provider matches, 409 when several do.
            RuntimeError: If the database pool is not initialised.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        digits = re.sub(r"\D+", "", phone_number or "")
        national = normalize_indian_mobile(phone_number) or (digits[-10:] if len(digits) >= 10 else None)
        if not national:
            raise HTTPException(
                status_code=400,
                detail="A mobile number of at least 10 digits is required to edit a provider",
            )

        candidates = await Database.fetch(
            """SELECT providerid, contactid, userprofileid, businessname
                 FROM etown_provider
                WHERE RIGHT(regexp_replace(COALESCE(mobilenumber, ''), '[^0-9]', '', 'g'), 10) = $1
                  AND lower(COALESCE(deleted, 'false')) <> 'true'
                  AND NOT (
                      btrim(COALESCE(providertype, '')) <> ''
                      AND position('nonverified' in lower(btrim(providertype))) = 0
                      AND position('unverified'  in lower(btrim(providertype))) = 0
                      AND (
                          lower(btrim(providertype)) = 'verified'
                          OR right(lower(btrim(providertype)), 9) = '_verified'
                      )
                  )
                ORDER BY creationdate DESC NULLS LAST""",
            national,
        )
        if not candidates:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No non-verified provider is registered on this mobile number. A verified "
                    "listing is edited through POST /provider/onboard with action_mode='edit'."
                ),
            )
        if len(candidates) > 1:
            names = ", ".join(f"'{r['businessname']}'" for r in candidates)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{len(candidates)} non-verified providers share this mobile number "
                    f"({names}) — the body carries no providerid, so which one to edit is "
                    "ambiguous. Edit it through POST /provider/onboard with action_mode='edit' "
                    "and its providerid."
                ),
            )

        existing = candidates[0]
        real_provider_id = str(existing["providerid"])
        real_contact_id = str(existing["contactid"]) if existing["contactid"] else None
        real_userprofile_id = str(existing["userprofileid"]) if existing["userprofileid"] else None

        def _keep_if_null(value: Optional[str]) -> Optional[str]:
            """Treat None / '' / the literal 'Null' as 'keep the existing value'."""
            if value is None:
                return None
            text = str(value).strip()
            return None if not text or text.lower() == "null" else text

        business_name = _keep_if_null(business_name)
        description = _keep_if_null(description)
        city = _keep_if_null(city)
        additionaladdressinfo = _keep_if_null(additionaladdressinfo)
        area_location = _keep_if_null(area_location)
        state = _keep_if_null(state)
        formatted_address = _keep_if_null(formatted_address)
        business_category_input = _keep_if_null(business_category_input)
        service_category_input = _keep_if_null(service_category_input)
        servicetype = _keep_if_null(servicetype)
        services = _keep_if_null(services)
        email = _keep_if_null(email)

        marketing_person_name: Optional[str] = None
        if marketing_contact_id:
            marketing_person_name = await Database.fetchval(
                """SELECT firstname FROM etown_contact
                    WHERE contactid = $1
                      AND deleted IS DISTINCT FROM 'true'""",
                marketing_contact_id,
            )
            if not marketing_person_name:
                logger.warning(
                    "[unverified_provider_edit] marketing_contact_id %s matched no live contact "
                    "(or it has no firstname) — provider %s keeps its stored marketing_person_name",
                    marketing_contact_id, real_provider_id,
                )

        final_image_url: Optional[str] = None
        if businesslogo and businesslogo.strip().lower() not in ("null", "no image"):
            if _is_stored_media_url(businesslogo):
                final_image_url = businesslogo.strip()
            else:
                try:
                    logo_bytes = _to_bytes(businesslogo)
                    if logo_bytes:
                        container_name = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
                        blob_name = f"{real_contact_id}/{_current_timestamp_prefix()}_ProfileImg.png"
                        final_image_url = await _upload_blob_bytes(
                            container_name, blob_name, logo_bytes, "image/png"
                        ) or None
                except Exception as e:
                    logger.warning(
                        "[unverified_provider_edit] Logo upload failed for provider=%s: %s",
                        real_provider_id, e,
                    )
                    final_image_url = None

        business_category_for_db = (
            _normalize_biz_category(business_category_input) if business_category_input else None
        )
        statecode = convert_state_name_to_code(state) if state else None
        address = formatted_address
        lat_text = str(latitude) if latitude is not None else None
        lon_text = str(longitude) if longitude is not None else None

        acquire_start = time.perf_counter()
        try:
            async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
                logger.info(
                    "[unverified_provider_edit] EtownDB acquire_time_ms=%.2f",
                    (time.perf_counter() - acquire_start) * 1000,
                )
                query_start = time.perf_counter()
                async with con.transaction():
                    await con.execute(
                        """UPDATE etown_provider SET
                            businessname          = COALESCE($2,  businessname),
                            description           = COALESCE($3,  description),
                            businesscategory      = COALESCE($4,  businesscategory),
                            email                 = COALESCE($5,  email),
                            city                  = COALESCE($6,  city),
                            townname              = COALESCE($6,  townname),
                            address               = COALESCE($7,  address),
                            areaname              = COALESCE($8,  areaname),
                            statecode             = COALESCE($9,  statecode),
                            latitude              = COALESCE($10, latitude),
                            longitude             = COALESCE($11, longitude),
                            additionaladdressinfo = COALESCE($12, additionaladdressinfo),
                            servicetype           = COALESCE($13, servicetype),
                            serviceimageurl       = COALESCE($14, serviceimageurl),
                            marketing_person_name = COALESCE($15, marketing_person_name),
                            modifieddate          = NOW()
                        WHERE providerid = $1""",
                        real_provider_id,
                        business_name, description, business_category_for_db,
                        email, city, address, area_location, statecode,
                        latitude, longitude, additionaladdressinfo, servicetype,
                        final_image_url, marketing_person_name,
                    )

                    await con.execute(
                        """UPDATE etown_providerservices SET
                            businessname          = COALESCE($2,  businessname),
                            servicedescription    = COALESCE($3,  servicedescription),
                            businesscategory      = COALESCE($4,  businesscategory),
                            servicecategory       = COALESCE($5,  servicecategory),
                            email                 = COALESCE($6,  email),
                            city                  = COALESCE($7,  city),
                            address               = COALESCE($8,  address),
                            areaname              = COALESCE($9,  areaname),
                            statecode             = COALESCE($10, statecode),
                            latitude              = COALESCE($11, latitude),
                            longitude             = COALESCE($12, longitude),
                            additionaladdressinfo = COALESCE($13, additionaladdressinfo),
                            services              = COALESCE($14, services),
                            serviceimageurl       = COALESCE($15, serviceimageurl),
                            modifieddate          = NOW()
                        WHERE providerid = $1""",
                        real_provider_id,
                        business_name, description, business_category_for_db,
                        service_category_input, email, city, address,
                        area_location, statecode, lat_text, lon_text,
                        additionaladdressinfo, services, final_image_url,
                    )

                    await con.execute(
                        """UPDATE etown_providerservices_copy SET
                            businessname          = COALESCE($2,  businessname),
                            servicedescription    = COALESCE($3,  servicedescription),
                            businesscategory      = COALESCE($4,  businesscategory),
                            servicecategory       = COALESCE($5,  servicecategory),
                            email                 = COALESCE($6,  email),
                            city                  = COALESCE($7,  city),
                            address               = COALESCE($8,  address),
                            areaname              = COALESCE($9,  areaname),
                            statecode             = COALESCE($10, statecode),
                            latitude              = COALESCE($11, latitude),
                            longitude             = COALESCE($12, longitude),
                            additionaladdressinfo = COALESCE($13, additionaladdressinfo),
                            services              = COALESCE($14, services),
                            serviceimageurl       = COALESCE($15, serviceimageurl),
                            modifieddate          = NOW()
                        WHERE providerid = $1""",
                        real_provider_id,
                        business_name, description, business_category_for_db,
                        service_category_input, email, city, address,
                        area_location, statecode, latitude, longitude,
                        additionaladdressinfo, services, final_image_url,
                    )

                    if real_contact_id:
                        await con.execute(
                            """UPDATE etown_contact SET
                                email                 = COALESCE($2, email),
                                city                  = COALESCE($3, city),
                                address               = COALESCE($4, address),
                                statecode             = COALESCE($5, statecode),
                                latitude              = COALESCE($6, latitude),
                                longitude             = COALESCE($7, longitude),
                                additionaladdressinfo = COALESCE($8, additionaladdressinfo),
                                modifieddate          = NOW()
                            WHERE contactid = $1""",
                            real_contact_id,
                            email, city, address, statecode,
                            latitude, longitude, additionaladdressinfo,
                        )

                    updated = await con.fetchrow(
                        """SELECT
                                p.providerid, p.businessname, p.businesscategory, p.description,
                                p.publicsearchprovider, p.serviceimageurl, p.contactid, p.userprofileid,
                                c.firstname, c.mobilephone, c.email, c.createdon, c.userlogin, c.city,
                                up.role, up.name AS userprofile_name,
                                psc.providerservicesid, psc.services, psc.servicecategory,
                                psc.servicedescription, psc.availability, psc.pricerange, psc.quantity,
                                psc.websiteurl, psc.yearsinbusiness, psc.mobilenumber AS service_mobilenumber,
                                psc.contactidname, psc.userprofileidname,
                                psc.serviceimageurl AS service_imageurl,
                                psc.serviceimageurl1, psc.serviceimageurl2, psc.serviceimageurl3,
                                ps.providerservicesid AS providerservicesid_orig
                           FROM etown_provider p
                           LEFT JOIN etown_contact c            ON c.contactid       = p.contactid
                           LEFT JOIN etown_userprofile up       ON up.userprofileid  = p.userprofileid
                           LEFT JOIN etown_providerservices_copy psc ON psc.providerid = p.providerid
                           LEFT JOIN etown_providerservices ps       ON ps.providerid  = p.providerid
                           WHERE p.providerid = $1
                           LIMIT 1""",
                        real_provider_id,
                    )
                    svc_rows = await con.fetch(
                        "SELECT providerservicesid FROM etown_providerservices WHERE providerid = $1",
                        real_provider_id,
                    )
                    svc_copy_rows = await con.fetch(
                        "SELECT providerservicesid FROM etown_providerservices_copy WHERE providerid = $1",
                        real_provider_id,
                    )
                logger.info(
                    "[unverified_provider_edit] EtownDB query_time_ms=%.2f",
                    (time.perf_counter() - query_start) * 1000,
                )
        except asyncpg.exceptions.InternalClientError:
            logger.warning(
                "[unverified_provider_edit] Pool connection was reset by the server "
                "(InternalClientError). Retrying with a fresh connection via @with_retry()."
            )
            raise asyncio.TimeoutError("Database connection was reset by the server; retrying.")

        if not updated:
            raise HTTPException(status_code=404, detail="Provider not found")

        final_business_name = updated["businessname"] or business_name or ''
        final_description = updated["description"] or ''
        final_biz_category = updated["businesscategory"] or ''
        final_svc_category = updated["servicecategory"] or ''

        response = {
            "contact_info": {
                "contactid": str(updated["contactid"] or real_contact_id or ''),
                "firstname": updated["firstname"],
                "mobilephone": updated["mobilephone"],
                "email": updated["email"],
                "createdon": updated["createdon"].isoformat() + "Z" if updated["createdon"] else None,
                "userlogin": updated["userlogin"],
                "city": updated["city"],
            },
            "userprofile_info": {
                "userprofileid": str(updated["userprofileid"] or real_userprofile_id or ''),
                "contactid": str(updated["contactid"] or real_contact_id or ''),
                "role": updated["role"] if updated["role"] is not None else 1,
                "name": updated["userprofile_name"] or final_business_name,
            },
            "provider_info": {
                "providerid": real_provider_id,
                "businessname": final_business_name,
                "businesscategory": updated["businesscategory"],
                "contactid": str(updated["contactid"] or real_contact_id or ''),
                "userprofileid": str(updated["userprofileid"] or real_userprofile_id or ''),
                "city": updated["city"],
                "description": updated["description"],
                "publicsearchprovider": updated["publicsearchprovider"] or 'non-verified',
                "serviceimageurl": updated["serviceimageurl"],
            },
            "providerservice_info": {
                "providerservicesid": str(updated["providerservicesid"] or ''),
                "providerid": real_provider_id,
                "businessname": final_business_name,
                "servicedescription": updated["servicedescription"] or final_description,
                "services": updated["services"] or '',
                "businesscategory": final_biz_category,
                "servicecategory": final_svc_category,
                "providerservicesid_orig": str(updated["providerservicesid_orig"] or ''),
                "availability": updated["availability"],
                "availability1": updated["availability"],
                "pricerange": updated["pricerange"],
                "quantity": updated["quantity"],
                "websiteurl": updated["websiteurl"],
                "yearsinbusiness": updated["yearsinbusiness"],
                "businessnumber": updated["service_mobilenumber"],
                "contactidname": updated["contactidname"],
                "userprofileidname": updated["userprofileidname"],
                "serviceimageurl": updated["service_imageurl"],
                "serviceimageurl1": updated["serviceimageurl1"],
                "serviceimageurl2": updated["serviceimageurl2"],
                "serviceimageurl3": updated["serviceimageurl3"],
            },
            "ai_enhancements": {
                "original_description": description or final_description,
                "enhanced_description": final_description,
                "generated_services": [
                    s.strip() for s in (updated["services"] or '').split(',') if s.strip()
                ],
                "business_category": final_biz_category,
                "service_category": final_svc_category,
                "general_category": final_biz_category,
            },
            "providerservices_ids": [str(r["providerservicesid"]) for r in svc_rows],
            "providerservices_copy_ids": [str(r["providerservicesid"]) for r in svc_copy_rows],
            "embedding_text_changed": any(
                v is not None for v in (business_name, description, city, address)
            ),
            "resolved_fields": {
                "business_name": business_name,
                "description": description,
                "business_category": business_category_for_db,
                "service_category": service_category_input,
                "city": city,
                "address": address,
                "areaname": area_location,
                "statecode": statecode,
                "latitude": lat_text,
                "longitude": lon_text,
                "additionaladdressinfo": additionaladdressinfo,
                "servicetype": servicetype,
                "services": services,
                "email": email,
                "serviceimageurl": updated["serviceimageurl"],
            },
        }

        logger.info(
            "[unverified_provider_edit] Provider updated: providerid=%s business='%s'",
            real_provider_id, final_business_name,
        )
        return response

    @staticmethod
    async def insert_web_provider_to_db(
        place_id: str,
        business_name: str,
        city: str,
        description: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        state: Optional[str] = None,
        areaname: Optional[str] = None,
        formatted_address: Optional[str] = None,
        phone_number: Optional[str] = None,
        business_category: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Upsert a Google/RapidAPI provider into Postgres under ids derived deterministically from place_id."""
        generated_contact_id    = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"contact:{place_id}"))
        userprofile_id          = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"userprofile:{place_id}"))
        provider_id             = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"provider:{place_id}"))
        providerservice_id      = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pscopy:{place_id}"))
        providerservice_orig_id = providerservice_id

        current_timestamp        = datetime.now(timezone.utc).replace(tzinfo=None)
        phone_str                = str(phone_number) if phone_number else ""
        desc_for_insert          = description or business_name
        initial_statecode        = convert_state_name_to_code(state) if state else None
        initial_address          = formatted_address or city or ''
        business_category_for_db = _normalize_biz_category(business_category)
        service_category_for_db  = business_category or 'General Services'

        web_register_query = f"""
            WITH {CONTACT_PLAN_CTE.format(contact='$1')},
            inserted_contact AS (
                INSERT INTO etown_contact (
                    contactid, firstname, mobilephone, email, userlogin,
                    createdon, rolecode, rolecodename, city, content_vector, address, statecode, latitude, longitude, publicsearchstatus, verified
                )
                VALUES ($1, $2, $3, $23, 'mobile', $4, 1, 'Provider', $5, $22::vector, $24, $16, $17, $18, 'unverified', FALSE)
                ON CONFLICT (contactid) DO UPDATE SET
                    content_vector = EXCLUDED.content_vector,
                    address = COALESCE(EXCLUDED.address, etown_contact.address),
                    statecode = COALESCE(EXCLUDED.statecode, etown_contact.statecode),
                    latitude = COALESCE(EXCLUDED.latitude, etown_contact.latitude),
                    longitude = COALESCE(EXCLUDED.longitude, etown_contact.longitude),
                    publicsearchstatus = 'unverified',
                    verified = FALSE
                RETURNING contactid, firstname, mobilephone, email, createdon, userlogin, city
            ),
            inserted_userprofile AS (
                INSERT INTO etown_userprofile (
                    userprofileid, contactid, contactidname,
                    role, rolename, name, mobilenumber, createdon, verified
                )
                SELECT $6, ic.contactid, $2,
                       1, 'Provider', $2, $3, $4, FALSE
                FROM inserted_contact ic
                ON CONFLICT (userprofileid) DO UPDATE SET
                    contactidname = EXCLUDED.contactidname,
                    name = EXCLUDED.name,
                    mobilenumber = EXCLUDED.mobilenumber,
                    createdon = EXCLUDED.createdon,
                    verified = FALSE
                RETURNING userprofileid, contactid, role, name, mobilenumber
            ),
            inserted_provider AS (
                INSERT INTO etown_provider (
                    providerid, businessname, businesscategory, contactid, userprofileid,
                    contactidname, userprofileidname, firstname, email, mobilenumber, city, description,
                    providertype, publicsearchprovider, role, rolename, creationdate,
                    areaname, townname, statecode, latitude, longitude, content_vector, address,
                    serviceimageurl, additionaladdressinfo, source,
                    {CONTACT_PLAN_COLUMNS}
                )
                SELECT $7, $13, $11, iup.contactid, iup.userprofileid,
                       $2, $2, $2, $23, $3, $5, $8,
                       'etown_nonverified', 'non-verified', 1, 'Provider', $4,
                       $14, $15, $16, $17, $18, $19::vector, $24,
                       $25, $26, 'Public Insert',
                       {CONTACT_PLAN_VALUES}
                FROM inserted_userprofile iup
                ON CONFLICT (providerid) DO UPDATE SET
                    businessname = EXCLUDED.businessname,
                    businesscategory = EXCLUDED.businesscategory,
                    contactidname = EXCLUDED.contactidname,
                    userprofileidname = EXCLUDED.userprofileidname,
                    description = EXCLUDED.description,
                    city = EXCLUDED.city,
                    areaname = EXCLUDED.areaname,
                    townname = EXCLUDED.townname,
                    statecode = EXCLUDED.statecode,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    content_vector = EXCLUDED.content_vector,
                    address = EXCLUDED.address,
                    serviceimageurl = COALESCE(EXCLUDED.serviceimageurl, etown_provider.serviceimageurl),
                    additionaladdressinfo = COALESCE(EXCLUDED.additionaladdressinfo, etown_provider.additionaladdressinfo),
                    source = EXCLUDED.source
                RETURNING providerid, businessname, businesscategory, contactid, userprofileid,
                         city, description, publicsearchprovider, contactidname, userprofileidname,
                         serviceimageurl, plan_code
            ),
            inserted_providerservice_copy AS (
                INSERT INTO etown_providerservices_copy (
                    providerservicesid, businessname, businesscategory, servicecategory, generalcategory,
                    providerid, contactid, contactidname, userprofileid, userprofileidname, email, mobilenumber,
                    servicedescription, services, city, address,
                    providertype, publicsearchprovider, createdon, content_vector,
                    areaname, statecode, latitude, longitude,
                    serviceimageurl, additionaladdressinfo, source
                )
                SELECT $9, $13, $11, $12, NULL,
                       ip.providerid, ip.contactid, $2, ip.userprofileid, $2, $23, $3,
                       $8, $10, ip.city, $24,
                       'etown_nonverified', 'non-verified', $4, $20::vector,
                       $14, $16, $17, $18,
                       $25, $26, 'Public Insert'
                FROM inserted_provider ip
                ON CONFLICT (providerservicesid) DO UPDATE SET
                    businessname = EXCLUDED.businessname,
                    businesscategory = EXCLUDED.businesscategory,
                    servicecategory = EXCLUDED.servicecategory,
                    generalcategory = EXCLUDED.generalcategory,
                    servicedescription = EXCLUDED.servicedescription,
                    services = EXCLUDED.services,
                    contactidname = EXCLUDED.contactidname,
                    userprofileidname = EXCLUDED.userprofileidname,
                    email = EXCLUDED.email,
                    mobilenumber = EXCLUDED.mobilenumber,
                    city = EXCLUDED.city,
                    content_vector = EXCLUDED.content_vector,
                    address = EXCLUDED.address,
                    areaname = EXCLUDED.areaname,
                    statecode = EXCLUDED.statecode,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    providertype = EXCLUDED.providertype,
                    publicsearchprovider = EXCLUDED.publicsearchprovider,
                    serviceimageurl = COALESCE(EXCLUDED.serviceimageurl, etown_providerservices_copy.serviceimageurl),
                    additionaladdressinfo = COALESCE(EXCLUDED.additionaladdressinfo, etown_providerservices_copy.additionaladdressinfo)
                RETURNING providerservicesid, providerid, businessname, servicedescription, services,
                         businesscategory, servicecategory
            ),
            inserted_providerservice AS (
                INSERT INTO etown_providerservices (
                    providerservicesid, businessname, businesscategory, servicecategory, generalcategory,
                    providerid, contactid, contactidname, userprofileid, userprofileidname, email, mobilenumber,
                    servicedescription, services, city, address,
                    providertype, publicsearchprovider, createdon,
                    areaname, statecode, latitude, longitude,
                    serviceimageurl, additionaladdressinfo, source
                )
                SELECT $21, $13, $11, $12, NULL,
                       ip.providerid, ip.contactid, $2, ip.userprofileid, $2, $23, $3,
                       $8, $10, ip.city, $24,
                       'etown_nonverified', 'non-verified', $4,
                       $14, $16, $17, $18,
                       $25, $26, 'Public Insert'
                FROM inserted_provider ip
                ON CONFLICT (providerservicesid) DO UPDATE SET
                    businessname = EXCLUDED.businessname,
                    businesscategory = EXCLUDED.businesscategory,
                    servicecategory = EXCLUDED.servicecategory,
                    generalcategory = EXCLUDED.generalcategory,
                    servicedescription = EXCLUDED.servicedescription,
                    services = EXCLUDED.services,
                    contactidname = EXCLUDED.contactidname,
                    userprofileidname = EXCLUDED.userprofileidname,
                    email = EXCLUDED.email,
                    mobilenumber = EXCLUDED.mobilenumber,
                    city = EXCLUDED.city,
                    address = EXCLUDED.address,
                    areaname = EXCLUDED.areaname,
                    statecode = EXCLUDED.statecode,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    providertype = EXCLUDED.providertype,
                    publicsearchprovider = EXCLUDED.publicsearchprovider,
                    serviceimageurl = COALESCE(EXCLUDED.serviceimageurl, etown_providerservices.serviceimageurl),
                    additionaladdressinfo = COALESCE(EXCLUDED.additionaladdressinfo, etown_providerservices.additionaladdressinfo)
                RETURNING providerservicesid, providerid, businessname, servicedescription, services,
                         businesscategory, servicecategory
            )
            SELECT
                ic.contactid, ic.firstname, ic.mobilephone, ic.email, ic.createdon, ic.userlogin, ic.city,
                iup.userprofileid, iup.role,
                ip.providerid, ip.businessname, ip.businesscategory, ip.description, ip.publicsearchprovider,
                ip.serviceimageurl, ip.plan_code,
                ipsc.providerservicesid, ipsc.services, ipsc.servicecategory,
                ips.providerservicesid as providerservicesid_orig
            FROM inserted_contact ic
            JOIN inserted_userprofile iup ON iup.contactid = ic.contactid
            JOIN inserted_provider ip ON ip.contactid = ic.contactid
            JOIN inserted_providerservice_copy ipsc ON ipsc.providerid = ip.providerid
            JOIN inserted_providerservice ips ON ips.providerid = ip.providerid
        """

        result = await Database.fetchrow(
            web_register_query,
            generated_contact_id,
            business_name,
            phone_str,
            current_timestamp,
            city or '',
            userprofile_id,
            provider_id,
            desc_for_insert,
            providerservice_id,
            '',
            business_category_for_db,
            service_category_for_db,
            business_name,
            areaname,
            None,
            initial_statecode,
            lat,
            lon,
            None,
            None,
            providerservice_orig_id,
            None,
            None,
            initial_address,
            None,
            None,
        )

        if not result:
            logger.warning("[web_provider_insert] DB returned no row for place_id=%s business=%s", place_id, business_name)
            return None

        logger.info("[web_provider_insert] Inserted/upserted place_id=%s provider_id=%s business=%s", place_id, provider_id, business_name)
        return {
            "contact_info": {
                "contactid": str(result['contactid']),
                "firstname": result['firstname'],
                "mobilephone": result['mobilephone'],
                "email": result.get('email'),
                "createdon": result['createdon'].isoformat() + "Z" if result['createdon'] else None,
                "userlogin": result['userlogin'],
                "city": result['city'],
            },
            "userprofile_info": {
                "userprofileid": userprofile_id,
                "contactid": str(result['contactid']),
                "role": result['role'],
                "name": result['firstname'],
            },
            "provider_info": {
                "providerid": str(result['providerid']),
                "businessname": result['businessname'],
                "businesscategory": result['businesscategory'],
                "contactid": str(result['contactid']),
                "userprofileid": userprofile_id,
                "city": result['city'],
                "description": result['description'],
                "publicsearchprovider": result['publicsearchprovider'],
                "serviceimageurl": result.get('serviceimageurl'),
                "plan_code": result.get('plan_code'),
            },
            "providerservice_info": {
                "providerservicesid": str(result['providerservicesid']),
                "providerid": str(result['providerid']),
                "businessname": result['businessname'],
                "servicedescription": result['description'],
                "services": result.get('services'),
                "businesscategory": result['businesscategory'],
                "servicecategory": result.get('servicecategory'),
                "providerservicesid_orig": str(result['providerservicesid_orig']) if result.get('providerservicesid_orig') else None,
            },
        }

    @staticmethod
    async def upsert_provider_details(
        provider_id: str,
        place_id: Optional[str] = None,
        google_rating: Optional[float] = None,
        google_rating_count: Optional[int] = None,
        business_status: Optional[str] = None,
        website_uri: Optional[str] = None,
        google_maps_uri: Optional[str] = None,
        opening_hours: Optional[Any] = None,
        reviews: Optional[Any] = None,
        images: Optional[Any] = None,
        comments: Optional[Any] = None,
        photo_blob_urls: Optional[Any] = None,
    ) -> None:
        """Upsert Google Places details into etown_provider_details, keeping stored values where arguments are None."""
        if not provider_id:
            logger.warning("[provider_details] Skipping upsert — no provider_id supplied")
            return

        def _as_jsonb(value: Any) -> Optional[str]:
            return json.dumps(value) if value is not None else None

        query = """
            INSERT INTO etown_provider_details (
                providerid, place_id, google_rating, google_rating_count, business_status,
                website_uri, google_maps_uri, opening_hours, reviews, images, comments,
                photo_blob_urls, createdon, updatedon
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8::jsonb, $9::jsonb, $10::jsonb, $11::jsonb,
                $12::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (providerid) DO UPDATE SET
                place_id = COALESCE(EXCLUDED.place_id, etown_provider_details.place_id),
                google_rating = COALESCE(EXCLUDED.google_rating, etown_provider_details.google_rating),
                google_rating_count = COALESCE(EXCLUDED.google_rating_count, etown_provider_details.google_rating_count),
                business_status = COALESCE(EXCLUDED.business_status, etown_provider_details.business_status),
                website_uri = COALESCE(EXCLUDED.website_uri, etown_provider_details.website_uri),
                google_maps_uri = COALESCE(EXCLUDED.google_maps_uri, etown_provider_details.google_maps_uri),
                opening_hours = COALESCE(EXCLUDED.opening_hours, etown_provider_details.opening_hours),
                reviews = COALESCE(EXCLUDED.reviews, etown_provider_details.reviews),
                images = COALESCE(EXCLUDED.images, etown_provider_details.images),
                comments = COALESCE(EXCLUDED.comments, etown_provider_details.comments),
                photo_blob_urls = COALESCE(EXCLUDED.photo_blob_urls, etown_provider_details.photo_blob_urls),
                updatedon = CURRENT_TIMESTAMP
        """
        try:
            await Database.execute(
                query,
                provider_id,
                place_id,
                google_rating,
                google_rating_count,
                business_status,
                website_uri,
                google_maps_uri,
                _as_jsonb(opening_hours),
                _as_jsonb(reviews),
                _as_jsonb(images),
                _as_jsonb(comments),
                _as_jsonb(photo_blob_urls),
            )
            logger.info("[provider_details] Upserted details for provider_id=%s (place_id=%s)", provider_id, place_id)
        except Exception as exc:
            logger.warning("[provider_details] Upsert failed for provider_id=%s: %s", provider_id, exc)

    @staticmethod
    async def run_background_enhancements(
        provider_id: str,
        providerservice_copy_id: str,
        providerservice_orig_id: str,
        contact_id: str,
        userprofile_id: str,
        business_name: str,
        description: str,
        business_category_input: Optional[str],
        service_category_input: Optional[str],
        latitude: Optional[float],
        longitude: Optional[float],
        businesslogo: Optional[str],
        phone_number: str,
        email: Optional[str],
        city: Optional[str],
        first_name: Optional[str],
        formatted_address: Optional[str],
        area_location: Optional[str],
        state: Optional[str],
        additionaladdressinfo: Optional[str],
        google_photos: Optional[List[Dict]] = None,
        servicetype: Optional[str] = None,
        website: Optional[str] = None,
        providertype: str = 'etown_verified',
        place_types: Optional[List[str]] = None,
        google_reviews: Optional[List[Dict]] = None,
        services_input: Optional[str] = None,
        plan_code: Optional[str] = None,
        generate_services: bool = True,
        source: Optional[str] = None,
        interested: Optional[str] = None,
    ) -> None:
        """Run AI enhancement, geocoding, blob upload and embedding after an onboarding insert, then sync to Dataverse."""
        try:
            logger.info(f"[bg_enhance] Starting background enhancements for provider: {business_name} ({provider_id})")
            desc_for_ai = description or business_name

            review_texts = [
                str((r or {}).get("text", {}).get("text") or (r or {}).get("text") or "").strip()
                for r in (google_reviews or [])
                if isinstance(r, dict)
            ]
            ai_coro = ProviderService.enhance_provider_data(
                business_name,
                desc_for_ai,
                place_types=[
                    t for t in (place_types or [])
                    if str(t).strip().lower() not in _CONTENTLESS_DESCRIPTIONS
                    and str(t).strip().lower() != "point_of_interest"
                ],
                reviews=[t for t in review_texts if t][:8],
            )

            async def _geo():
                if latitude is not None and longitude is not None:
                    try:
                        return await get_location_details_logic(latitude, longitude)
                    except Exception as e:
                        logger.warning(f"[bg_enhance] Geocoding failed for {business_name}: {e}")
                return None

            async def _blob():
                if businesslogo and businesslogo.strip().lower() not in ('null', ''):
                    try:
                        logo_bytes = _to_bytes(businesslogo)
                        if logo_bytes:
                            container_name = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
                            timestamp = _current_timestamp_prefix()
                            blob_name = f"{contact_id}/{timestamp}_ProfileImg.png"
                            return await _upload_blob_bytes(container_name, blob_name, logo_bytes, "image/png")
                    except Exception as e:
                        logger.warning(f"[bg_enhance] Blob upload failed for {business_name}: {e}")
                return None

            async def _photos():
                if not google_photos:
                    return []
                container_name = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
                timestamp = _current_timestamp_prefix()
                blob_urls = []
                for i, photo in enumerate(google_photos[:10]):
                    photo_name = (photo or {}).get('name')
                    if not photo_name:
                        continue
                    try:
                        url = (
                            f"https://places.googleapis.com/v1/{photo_name}/media"
                            f"?maxWidthPx=800&key={settings.google_maps_api_key}"
                        )
                        async with get_http_client() as client:
                            resp = await client.get(url, follow_redirects=True)
                            resp.raise_for_status()
                        blob_name = f"{contact_id}/{timestamp}_{i}_PlacePhoto.png"
                        blob_url_i = await _upload_blob_bytes(container_name, blob_name, resp.content, "image/png")
                        if blob_url_i:
                            blob_urls.append(blob_url_i)
                    except httpx.HTTPStatusError as _pe:
                        if _pe.response.status_code in (401, 403):
                            logger.error(
                                "[bg_enhance] Google rejected the photo fetch for %s (HTTP %s) — abandoning the "
                                "remaining %s photo(s) for this provider. Enable 'Places API (New)' "
                                "(google.maps.places.v1.Places.GetPhotoMedia) for the google_maps_api_key in the "
                                "Cloud Console; the key currently answers API_KEY_SERVICE_BLOCKED, so every photo "
                                "fetch fails and scraped providers stay imageless. Response: %s",
                                business_name, _pe.response.status_code,
                                len(google_photos[:10]) - i - 1, _pe.response.text[:200],
                             exc_info=True)
                            break
                        logger.warning(f"[bg_enhance] Place photo {i} fetch/upload failed for {business_name}: {_pe}")
                    except Exception as _pe:
                        logger.warning(f"[bg_enhance] Place photo {i} fetch/upload failed for {business_name}: {_pe}")
                logger.info(f"[bg_enhance] Uploaded {len(blob_urls)}/{len(google_photos[:10])} place photos for {business_name}")
                return blob_urls

            enhanced_data, geo_result, blob_url, photo_urls = await asyncio.gather(
                ai_coro, _geo(), _blob(), _photos(), return_exceptions=True
            )

            if isinstance(enhanced_data, Exception):
                logger.error(f"[bg_enhance] AI enhancement failed for {business_name}: {enhanced_data}")
                enhanced_data = {
                    'enhanced_description': description or business_name,
                    'services': '',
                    'services_list': [],
                    'business_category': _normalize_biz_category(business_category_input),
                    'service_category': service_category_input or 'General Services',
                    'general_category': 'general',
                    'original_description': description or '',
                }
            if isinstance(geo_result, Exception):
                geo_result = None
            if isinstance(blob_url, Exception):
                blob_url = None

            business_category_final = _normalize_biz_category(
                enhanced_data.get('business_category') or business_category_input
            )
            service_category_final = (
                service_category_input
                or enhanced_data.get('service_category')
                or 'General Services'
            )
            ai_services = enhanced_data.get('services') if generate_services else None
            services_final = (
                ai_services
                or (services_input or '').strip()
                or (service_category_input or '').strip()
                or business_name
            )
            if generate_services and not ai_services:
                logger.info(
                    "[bg_enhance] AI returned no catalogue for %s — service name falls back to %r",
                    business_name, services_final,
                )
            if not generate_services:
                logger.info(
                    "[bg_enhance] Service catalogue generation is off for %s — keeping the "
                    "request body's services (%r)",
                    business_name, services_final,
                )
            description_final = (
                enhanced_data.get('enhanced_description') or description or business_name
            )
            servicedescription_final = description_final if generate_services else None

            loc = {'areaname': None, 'townname': None, 'statecode': None, 'address': None}
            if geo_result and hasattr(geo_result, 'parsed_components') and geo_result.parsed_components:
                components = geo_result.parsed_components
                loc['areaname'] = (
                    components.get('sublocality_level_1') or
                    components.get('sublocality') or
                    components.get('locality')
                )
                loc['townname'] = (
                    components.get('locality') or
                    components.get('administrative_area_level_3') or
                    components.get('administrative_area_level_2')
                )
                state_name = components.get('administrative_area_level_1')
                if state_name:
                    loc['statecode'] = convert_state_name_to_code(state_name)
                parts = list(dict.fromkeys(p for p in [loc['areaname'], loc['townname'], loc['statecode']] if p))
                if parts:
                    loc['address'] = ', '.join(parts)

            final_areaname = loc['areaname'] or area_location
            final_townname = loc['townname'] or city
            final_statecode = loc['statecode'] or (convert_state_name_to_code(state) if state else None)
            final_address = loc['address'] or formatted_address or city or ''

            async def _assign_town() -> Optional[str]:
                """Resolve and link the provider's home town, reporting failures rather than raising.

                A Public Insert (providertype 'etown_nonverified') resolves against the towns
                that exist and never opens a new one — see handle_provider_town_assignment's
                allow_create. The town mapping sweep picks it up once a town covers it.

                Returns:
                    Optional[str]: townprofileid the provider was linked to, or None.
                """
                try:
                    return await handle_provider_town_assignment(
                        provider_id=provider_id,
                        business_name=business_name,
                        townname=final_townname,
                        cityname=city,
                        statecode=final_statecode,
                        areaname=final_areaname,
                        sync_link=False,
                        allow_create=providertype != 'etown_nonverified',
                    )
                except PlanLimitError as ex:
                    logger.warning(f"[bg_enhance] Town cap reached for provider {provider_id}: {ex}")
                except Exception as ex:
                    logger.error(
                        f"[bg_enhance] handle_provider_town_assignment failed for provider {provider_id}: {ex}",
                        exc_info=True,
                    )
                return None

            town_task = asyncio.create_task(_assign_town())

            content_vector = None
            try:
                embedding_text = f"Business: {business_name}"
                if business_category_final:
                    embedding_text += f" Category: {business_category_final}"
                if service_category_final:
                    embedding_text += f" Service Category: {service_category_final}"
                if enhanced_data.get('enhanced_description'):
                    embedding_text += f" Description: {enhanced_data['enhanced_description']}"
                services_for_embedding = ai_services if generate_services else services_final
                if services_for_embedding:
                    embedding_text += f" Services: {services_for_embedding}"
                embedding_vector = await generate_embedding(embedding_text)
                content_vector = '[' + ','.join(map(str, embedding_vector)) + ']'
                logger.info(f"[bg_enhance] Generated content vector for {business_name}")
            except Exception as e:
                logger.warning(f"[bg_enhance] Embedding generation failed for {business_name}: {e}")

            async def _upd_provider():
                await Database.execute(
                    """UPDATE etown_provider SET
                        description = $2,
                        businesscategory = $3,
                        content_vector = COALESCE($4::vector, content_vector),
                        areaname = COALESCE($5, areaname),
                        townname = COALESCE($6, townname),
                        statecode = COALESCE($7, statecode),
                        address = COALESCE($8, address),
                        serviceimageurl = COALESCE($9, serviceimageurl),
                        modifieddate = NOW()
                    WHERE providerid = $1""",
                    provider_id,
                    description_final,
                    business_category_final,
                    content_vector,
                    final_areaname, final_townname, final_statecode, final_address,
                    blob_url,
                )

            async def _upd_psc():
                await Database.execute(
                    """UPDATE etown_providerservices_copy SET
                        servicedescription = COALESCE($2, servicedescription),
                        services = $3,
                        businesscategory = $4,
                        servicecategory = $5,
                        generalcategory = $6,
                        content_vector = COALESCE($7::vector, content_vector),
                        serviceimageurl = COALESCE($8, serviceimageurl),
                        areaname = COALESCE($9, areaname),
                        statecode = COALESCE($10, statecode),
                        modifieddate = NOW()
                    WHERE providerservicesid = $1""",
                    providerservice_copy_id,
                    servicedescription_final,
                    services_final,
                    business_category_final,
                    service_category_final,
                    enhanced_data.get('general_category') or '',
                    content_vector,
                    blob_url,
                    final_areaname, final_statecode,
                )
                await Database.execute(
                    """UPDATE etown_providerservices_copy SET
                        areaname = COALESCE(areaname, $2),
                        statecode = COALESCE(statecode, $3),
                        modifieddate = NOW()
                    WHERE providerid = $1
                      AND (areaname IS NULL OR statecode IS NULL)""",
                    provider_id, final_areaname, final_statecode,
                )

            async def _upd_ps():
                await Database.execute(
                    """UPDATE etown_providerservices ps
                        SET servicedescription = COALESCE($2, ps.servicedescription),
                            services = $3,
                            businesscategory = $4,
                            servicecategory = $5,
                            generalcategory = $6,
                            serviceimageurl = COALESCE($7, ps.serviceimageurl),
                            areaname = COALESCE($8, ps.areaname),
                            statecode = COALESCE($9, ps.statecode),
                            city = COALESCE(p.city, ps.city),
                            mobilenumber = COALESCE(p.mobilenumber, ps.mobilenumber),
                            modifieddate = NOW()
                        FROM etown_provider p
                        WHERE p.providerid = ps.providerid
                          AND ps.providerservicesid = $1""",
                    providerservice_orig_id,
                    servicedescription_final,
                    services_final,
                    business_category_final,
                    service_category_final,
                    enhanced_data.get('general_category') or '',
                    blob_url,
                    final_areaname, final_statecode,
                )
                await Database.execute(
                    """UPDATE etown_providerservices SET
                        areaname = COALESCE(areaname, $2),
                        statecode = COALESCE(statecode, $3),
                        modifieddate = NOW()
                    WHERE providerid = $1
                      AND (areaname IS NULL OR statecode IS NULL)""",
                    provider_id, final_areaname, final_statecode,
                )

            async def _upd_contact():
                await Database.execute(
                    """UPDATE etown_contact SET
                        content_vector = COALESCE($2::vector, content_vector),
                        statecode = COALESCE($3, statecode),
                        address = COALESCE($4, address),
                        modifieddate = NOW()
                    WHERE contactid = $1""",
                    contact_id,
                    content_vector,
                    final_statecode, final_address,
                )

            results = await asyncio.gather(
                _upd_provider(), _upd_psc(), _upd_ps(), _upd_contact(),
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"[bg_enhance] DB update {i} failed for {business_name}: {r}")

            if isinstance(photo_urls, Exception):
                photo_urls = []
            if photo_urls:
                try:
                    await ProviderService.upsert_provider_details(
                        provider_id=provider_id,
                        photo_blob_urls=photo_urls,
                    )
                    logger.info(f"[bg_enhance] Stored {len(photo_urls)} place photo URL(s) for {business_name}")
                except Exception as _pex:
                    logger.warning(f"[bg_enhance] Failed to store place photo URLs for {business_name}: {_pex}")

            display_name = first_name or business_name
            enhanced_description = description_final

            contact_info_for_sync = {
                "contactid": contact_id,
                "firstname": display_name,
                "mobilephone": phone_number,
                "email": email,
                "city": city,
            }
            userprofile_info_for_sync = {
                "userprofileid": userprofile_id,
                "contactid": contact_id,
                "role": 1,
                "name": display_name,
            }
            publicsearchprovider_for_sync = "non-verified" if providertype == "etown_nonverified" else "verified"
            provider_info_for_sync = {
                "providerid": provider_id,
                "businessname": business_name,
                "businesscategory": business_category_final,
                "city": city,
                "description": enhanced_description,
                "publicsearchprovider": publicsearchprovider_for_sync,
                "serviceimageurl": blob_url,
                "plan_code": plan_code,
                "interested": interested,
            }
            providerservice_info_for_sync = {
                "providerservicesid_orig": providerservice_orig_id,
                "businessname": business_name,
                "businesscategory": business_category_final,
                "servicecategory": service_category_final,
                "servicedescription": enhanced_description,
                "services": services_final,
            }

            assigned_townprofileid = await town_task

            await sync_provider_onboard_to_dataverse(
                contact_info=contact_info_for_sync,
                userprofile_info=userprofile_info_for_sync,
                provider_info=provider_info_for_sync,
                providerservice_info=providerservice_info_for_sync,
                latitude=latitude,
                longitude=longitude,
                address=final_address,
                areaname=final_areaname,
                townname=final_townname,
                townprofileid=assigned_townprofileid,
                statecode=final_statecode,
                additionaladdressinfo=additionaladdressinfo,
                general_category=enhanced_data.get('general_category'),
                providertype=providertype,
                serviceimageurl=blob_url,
                servicetype=servicetype,
                website=website,
                verified=providertype != 'etown_nonverified',
                source=source,
            )

            if not blob_url and settings.PROVIDER_IMAGE_AUTOGENERATE:
                try:
                    blob_url = await generate_provider_profile_image(provider_id)
                except Exception as _iex:
                    logger.warning(
                        "[bg_enhance] Profile image generation failed for %s (%s): %s",
                        business_name, provider_id, _iex, exc_info=True,
                    )

            try:
                await sync_provider_plan_to_dataverse([provider_id])
            except Exception as _pex:
                logger.warning(
                    "[bg_enhance] Plan Dataverse sync failed for provider %s: %s",
                    provider_id, _pex,
                )

            if assigned_townprofileid:
                from app.etown.mytown.mytown_syncing import sync_etownprovider_to_dataverse
                try:
                    await sync_etownprovider_to_dataverse(
                        etownproviderid=provider_id,
                        provider_id=provider_id,
                        business_name=business_name,
                        townprofileid=assigned_townprofileid,
                    )
                except Exception as ex:
                    logger.error(
                        "[bg_enhance] etownprovider Dataverse sync failed for provider %s: %s",
                        provider_id, ex, exc_info=True,
                    )

            if not generate_services:
                logger.info(
                    "[bg_enhance] Skipping AI offerings generation for %s (%s) — the request body "
                    "supplied the services",
                    userprofile_id, business_name,
                )
            else:
                try:
                    await generate_and_store_offerings_from_provider(
                        userprofile_id=userprofile_id,
                        provider_id=provider_id,
                        contact_id=contact_id,
                        business_name=business_name,
                        business_category=business_category_final,
                        service_category=service_category_final,
                        description=description_final,
                        services=services_final,
                        serviceimageurl=blob_url,
                        address=final_address,
                        phone_number=phone_number,
                        email=email,
                    )
                except Exception as _oex:
                    logger.warning(
                        "[bg_enhance] Offerings generation failed for %s (%s): %s",
                        userprofile_id, business_name, _oex,
                    )

            logger.info(f"[bg_enhance] Completed background enhancements for provider: {business_name} ({provider_id})")

        except Exception as e:
            logger.error(f"[bg_enhance] Background enhancement failed for {business_name} ({provider_id}): {e}", exc_info=True)

    @staticmethod
    async def validate_phone_format(phone_number: str) -> bool:
        """Return True when the phone number string contains 8 to 15 digits."""
        if not phone_number or not isinstance(phone_number, str):
            return False
        
        digits_only = ''.join(filter(str.isdigit, phone_number))
        return 8 <= len(digits_only) <= 15

    @classmethod
    @with_retry()
    async def get_unrated_provider_list(cls, contact_id: str, userprofile_id: str) -> List[Dict]:
        """Return one row per completed, unrated booking of the requestor, with provider details, newest first."""
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT * FROM (
                SELECT DISTINCT ON (ar.allrequestid)
                    rsl.serviceprovidercontactid,
                    rsl.serviceprovideruserprofileid,
                    p.businessname AS provider_businessname,
                    p.serviceimageurl AS provider_serviceimageurl,
                    p.providertype,
                    pcs.createdon AS providerservices_createdon,
                    ar.allrequestid,
                    ar.requestid,

                    COALESCE(p.deleted, 'false') AS provider_deleted,
                    CASE
                        WHEN p.deactivated = 'true'
                         AND p.reactivation IS DISTINCT FROM 'true'
                        THEN 'true' ELSE 'false'
                    END AS provider_deactivated
                FROM
                    etown_allrequests ar
                inner JOIN
                    etown_requestservicelists rsl ON ar.allrequestid = rsl.bookingid
                INNER JOIN
                    etown_provider p ON rsl.serviceprovideruserprofileid = p.userprofileid
                INNER JOIN
                    etown_providerservices_copy pcs ON rsl.providerservices = pcs.providerservicesid
                WHERE
                    ar.requestorid = $1
                    AND ar.requestoruserprofileid = $2
                    AND ar.requeststatus = 'completed'
                    AND "ar"."isRating" = 'False'
                ORDER BY
                    ar.allrequestid, pcs.createdon DESC
            ) dedup
            ORDER BY
                dedup.providerservices_createdon DESC;
"""

        try:
            async with Database._pool.acquire() as con:
                rows = await con.fetch(query, contact_id, userprofile_id)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching unrated provider list for contact_id={contact_id}: {str(e)}", exc_info=True)
            raise

    @staticmethod
    async def fetch_provider_identifiers(providerid: str) -> Optional[Dict[str, Any]]:
        """Resolve a providerid to its owning contactid and userprofileid, falling back to service rows.

        Args:
            providerid: The provider ID to resolve.

        Returns:
            Dict of providerid, contactid, userprofileid, businessname and source, or None if unmatched.
        """
        query = """
            WITH prov AS (
                SELECT providerid, contactid, userprofileid, businessname
                FROM etown_provider
                WHERE providerid = $1::text
            ),
            svc AS (
                SELECT contactid, userprofileid, businessname
                FROM (
                    SELECT contactid, userprofileid, businessname, createdon
                    FROM etown_providerservices_copy
                    WHERE providerid = $1::text AND NOT EXISTS (SELECT 1 FROM prov)
                    UNION ALL
                    SELECT contactid, userprofileid, businessname, createdon
                    FROM etown_providerservices
                    WHERE providerid = $1::text AND NOT EXISTS (SELECT 1 FROM prov)
                ) s
                WHERE s.contactid IS NOT NULL OR s.userprofileid IS NOT NULL
                ORDER BY s.createdon DESC NULLS LAST
                LIMIT 1
            )
            SELECT
                $1::text AS providerid,
                COALESCE(p.contactid, s.contactid, up.contactid) AS contactid,
                COALESCE(p.userprofileid, s.userprofileid)       AS userprofileid,
                COALESCE(p.businessname, s.businessname)         AS businessname,
                (p.providerid IS NOT NULL)                       AS from_provider_table,
                (s.contactid IS NOT NULL OR s.userprofileid IS NOT NULL) AS from_service_table
            FROM (SELECT 1) AS anchor
            LEFT JOIN prov p ON TRUE
            LEFT JOIN svc  s ON TRUE
            LEFT JOIN etown_userprofile up
                   ON up.userprofileid = COALESCE(p.userprofileid, s.userprofileid)

                  AND COALESCE(p.contactid, s.contactid) IS NULL
"""

        row = await Database.fetchrow(query, providerid)
        if not row or not (row["from_provider_table"] or row["from_service_table"]):
            logger.info(f"fetch_provider_identifiers | no match | providerid={providerid}")
            return None

        return {
            "providerid": row["providerid"],
            "contactid": row["contactid"],
            "userprofileid": row["userprofileid"],
            "businessname": row["businessname"],
            "source": "etown_provider" if row["from_provider_table"] else "etown_providerservices",
        }

    @staticmethod
    async def fetch_providers_by_mobile(mobile_number: str) -> List[Dict[str, Any]]:
        """List every live provider on a mobile number, newest first, with its status and service rows.

        Args:
            mobile_number: Mobile number in any format, matched on its last ten digits.

        Returns:
            JSON-safe provider dicts with is_verified, status and services; empty when nothing matches.
        """
        digits = re.sub(r"\D+", "", mobile_number or "")
        national = normalize_indian_mobile(mobile_number) or (digits[-10:] if len(digits) >= 10 else None)
        if not national:
            logger.info(
                "fetch_providers_by_mobile | unusable number (%d digit(s))", len(digits)
            )
            return []

        provider_query = """
            SELECT p.providerid, p.businessname, p.businesscategory,
                   p.contactid, p.userprofileid, p.contactidname, p.userprofileidname,
                   p.firstname, p.mobilenumber, p.alternatemobile, p.email,
                   p.description, p.city, p.areaname, p.townname, p.statecode,
                   p.address, p.additionaladdressinfo, p.latitude, p.longitude,
                   p.servicetype, p.websiteurl, p.availability, p.serviceimageurl,
                   p.providertype, p.publicsearchprovider, p.source, p.approve, p.creationdate,
                   (
                       btrim(COALESCE(p.providertype, '')) <> ''
                       AND position('nonverified' in lower(btrim(p.providertype))) = 0
                       AND position('unverified' in lower(btrim(p.providertype))) = 0
                       AND (
                           lower(btrim(p.providertype)) = 'verified'
                           OR right(lower(btrim(p.providertype)), 9) = '_verified'
                       )
                   ) AS is_verified
            FROM etown_provider p
            WHERE RIGHT(regexp_replace(COALESCE(p.mobilenumber, ''), '[^0-9]', '', 'g'), 10) = $1
              AND lower(COALESCE(p.deleted, 'false')) <> 'true'
            ORDER BY p.creationdate DESC NULLS LAST
        """

        rows = await Database.fetch(provider_query, national)
        if not rows:
            logger.info("fetch_providers_by_mobile | national=%s | no provider", national)
            return []

        providerids = [str(row["providerid"]) for row in rows]

        service_query = """
            SELECT ps.providerservicesid, ps.providerid, ps.services, ps.servicedescription,
                   ps.businesscategory, ps.servicecategory, ps.generalcategory,
                   ps.pricerange, ps.quantity, ps.availability, ps.yearsinbusiness,
                   ps.serviceimageurl, ps.serviceimageurl1, ps.serviceimageurl2, ps.serviceimageurl3,
                   ps.serviceitems, ps.providertype, ps.publicsearchprovider, ps.source,
                   ps.createdon
            FROM etown_providerservices ps
            WHERE ps.providerid = ANY($1::text[])
              AND lower(COALESCE(ps.deleted, 'false')) <> 'true'
            ORDER BY ps.createdon ASC NULLS LAST, ps.providerservicesid
        """
        service_rows = await Database.fetch(service_query, providerids)

        service_text_columns = (
            "providerservicesid", "providerid", "services", "servicedescription",
            "businesscategory", "servicecategory", "generalcategory", "pricerange", "quantity",
            "availability", "yearsinbusiness", "serviceimageurl", "serviceimageurl1",
            "serviceimageurl2", "serviceimageurl3", "providertype", "publicsearchprovider",
            "source",
        )
        services_by_provider: Dict[str, List[Dict[str, Any]]] = {}
        for row in service_rows:
            service = {
                col: (str(row[col]) if row[col] is not None else None)
                for col in service_text_columns
            }
            service["serviceitems"] = _coerce_json_list(row["serviceitems"])
            service["createdon"] = _iso_or_none_value(row["createdon"])
            services_by_provider.setdefault(str(row["providerid"]), []).append(service)

        provider_text_columns = (
            "providerid", "businessname", "businesscategory", "contactid", "userprofileid",
            "contactidname", "userprofileidname", "firstname", "mobilenumber", "alternatemobile", "email",
            "description", "city", "areaname", "townname", "statecode", "address",
            "additionaladdressinfo", "latitude", "longitude", "servicetype", "websiteurl",
            "availability", "serviceimageurl", "providertype", "publicsearchprovider", "source",
        )
        providers: List[Dict[str, Any]] = []
        for row in rows:
            provider = {
                col: (str(row[col]) if row[col] is not None else None)
                for col in provider_text_columns
            }
            providerid = str(row["providerid"])
            is_verified = bool(row["is_verified"])
            provider["approve"] = row["approve"]
            provider["creationdate"] = _iso_or_none_value(row["creationdate"])
            provider["is_verified"] = is_verified
            provider["status"] = (
                PROVIDER_STATUS_VERIFIED if is_verified
                else PROVIDER_STATUS_ADDED_BY_FOS if (row["source"] or "") == "FOS"
                else PROVIDER_STATUS_UNVERIFIED
            )
            provider["message"] = PROVIDER_STATUS_MESSAGES.get(provider["status"])
            provider["services"] = services_by_provider.get(providerid, [])
            providers.append(provider)

        logger.info(
            "fetch_providers_by_mobile | national=%s | %d provider(s), %d service row(s) | %s",
            national, len(providers), len(service_rows),
            [p["status"] for p in providers],
        )
        return providers
    @staticmethod
    async def set_provider_approval_by_providerid(
        provider_id: str,
        approve: bool,
    ) -> Optional[Dict[str, Any]]:
        """Set etown_provider.approve on one non-deleted provider, matching the id case-insensitively.

        Args:
            provider_id: The provider to decide.
            approve: True to approve, False to reject.

        Returns:
            Dict of the stored id, flag, needs_(non)verification and listing fields, or None if no provider.
        """
        wanted = (provider_id or "").strip()
        if not wanted:
            logger.info("set_provider_approval_by_providerid | blank providerid")
            return None

        row = await Database.fetchrow(
            """
            UPDATE etown_provider
               SET approve = $2, modifieddate = NOW()
             WHERE lower(providerid) = lower($1)
               AND lower(COALESCE(deleted, 'false')) <> 'true'
            RETURNING providerid, businessname, contactid, userprofileid,
                      COALESCE(townname, city) AS townname,
                      (providertype IS DISTINCT FROM 'etown_verified'
                       OR publicsearchprovider IS DISTINCT FROM 'verified') AS needs_verification,
                      (providertype IS DISTINCT FROM 'etown_nonverified'
                       OR publicsearchprovider IS DISTINCT FROM 'non-verified')
                          AS needs_nonverification
            """,
            wanted, approve,
        )
        if not row:
            logger.info(
                "set_provider_approval_by_providerid | no provider on providerid=%s", wanted
            )
            return None

        logger.info(
            "set_provider_approval_by_providerid | providerid=%s approve=%s | "
            "needs_verification=%s needs_nonverification=%s",
            row["providerid"], approve,
            row["needs_verification"], row["needs_nonverification"],
        )
        return {
            "providerid": str(row["providerid"]),
            "approve": approve,
            "needs_verification": bool(row["needs_verification"]),
            "needs_nonverification": bool(row["needs_nonverification"]),
            "businessname": row["businessname"],
            "contactid": str(row["contactid"]) if row["contactid"] else None,
            "userprofileid": str(row["userprofileid"]) if row["userprofileid"] else None,
            "townname": row["townname"],
        }
    @staticmethod
    @with_retry()
    async def delete_provider_from_db(
        contactid: str,
        userprofileid: str,
    ) -> Dict[str, Any]:
        """Hard-delete a provider's plan, service, provider and userprofile rows, keeping the contact.

        Returns:
            Dict of deleted flag, contactid, userprofileid, providerid and business_name.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            provider_row = await con.fetchrow(
                "SELECT providerid, businessname, contactid, userprofileid "
                "FROM etown_provider "
                "WHERE (contactid = $1 AND userprofileid = $2) "
                "   OR (contactid = $2 AND userprofileid = $1) "
                "LIMIT 1",
                contactid,
                userprofileid,
            )
            if not provider_row:
                return {
                    "deleted": False,
                    "contactid": contactid,
                    "userprofileid": userprofileid,
                    "providerid": None,
                    "business_name": None,
                }

            provider_id      = provider_row["providerid"]
            business_name    = provider_row["businessname"]
            real_contactid   = provider_row["contactid"]
            real_userprofile = provider_row["userprofileid"]

            async with con.transaction():
                await con.execute(
                    "DELETE FROM notifications.etown_plan_change WHERE userprofileid = $1",
                    real_userprofile,
                )
                await con.execute(
                    "DELETE FROM notifications.etown_provider_plan_usage WHERE userprofileid = $1",
                    real_userprofile,
                )
                await con.execute(
                    "DELETE FROM notifications.etown_provider_plan_conversation "
                    "WHERE userprofileid = $1",
                    real_userprofile,
                )
                await con.execute(
                    "DELETE FROM notifications.etown_plan_refusal WHERE userprofileid = $1",
                    real_userprofile,
                )
                await con.execute(
                    "DELETE FROM etown_providerservices_copy WHERE providerid = $1", provider_id
                )
                await con.execute(
                    "DELETE FROM etown_providerservices WHERE providerid = $1", provider_id
                )
                await con.execute(
                    "DELETE FROM etown_provider WHERE providerid = $1", provider_id
                )
                await con.execute(
                    "DELETE FROM etown_userprofile WHERE userprofileid = $1", real_userprofile
                )

            logger.info(
                f"Provider deleted: providerid={provider_id} business='{business_name}'"
            )
            return {
                "deleted": True,
                "contactid": real_contactid,
                "userprofileid": real_userprofile,
                "providerid": provider_id,
                "business_name": business_name,
            }

    @staticmethod
    async def edit_provider_profile_workflow(
        contact_id: str,
        provider_userprofile: str,
        updates: Dict[str, Any],
    ) -> tuple:
        """Upload any new images, then update the provider and its service rows from the supplied fields.

        Args:
            contact_id: Contact ID of the provider owner.
            provider_userprofile: User profile ID of the specific provider profile.
            updates: Field names mapped to new values, only those set in the request.

        Returns:
            Tuple of a status message and the updated provider data, or None when not found.
        """
        raw_image = updates.get("service_image_url")
        new_image_url: Optional[str] = None
        if raw_image is not None and str(raw_image).strip().lower() != "null":
            if _is_stored_media_url(raw_image):
                new_image_url = raw_image
            else:
                try:
                    image_bytes = _to_bytes(raw_image)
                    if image_bytes:
                        container_name = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
                        blob_name = f"{contact_id}/{_current_timestamp_prefix()}_ServiceImg.png"
                        new_image_url = await _upload_blob_bytes(container_name, blob_name, image_bytes, "image/png")
                except Exception as e:
                    logger.warning(f"[edit_profile] Image upload failed for contact={contact_id}: {e}")

        raw_background = updates.get("background_image")
        new_background_url: Optional[str] = None
        clear_background = False
        if raw_background is not None:
            token = str(raw_background).strip().lower()
            if token == "deleted":
                clear_background = True
            elif token not in ("", "null"):
                if _is_stored_media_url(raw_background):
                    new_background_url = raw_background
                else:
                    try:
                        background_bytes = _to_bytes(raw_background)
                        if background_bytes:
                            container_name = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
                            blob_name = f"{contact_id}/{_current_timestamp_prefix()}_BackgroundImg.png"
                            new_background_url = await _upload_blob_bytes(container_name, blob_name, background_bytes, "image/png")
                    except Exception as e:
                        logger.warning(f"[edit_profile] Background image upload failed for contact={contact_id}: {e}")

        raw_avail = updates.get("availability")
        new_availability = (
            None if (raw_avail is None or str(raw_avail).strip().lower() == "null") else raw_avail
        )

        lat_str = updates.get("latitude")
        lon_str = updates.get("longitude")
        lat_float = float(lat_str) if lat_str is not None else None
        lon_float = float(lon_str) if lon_str is not None else None

        raw_biz_cat = updates.get("business_category")
        business_category = _normalize_biz_category(raw_biz_cat) if raw_biz_cat else None

        row = await Database.fetchrow(
            """
            WITH target AS (
                SELECT providerid          AS t_providerid,
                       serviceimageurl     AS t_serviceimageurl,
                       availability        AS t_availability
                FROM etown_provider
                WHERE contactid = $1 AND userprofileid = $2
                ORDER BY modifieddate DESC NULLS LAST, providerid
                LIMIT 1
            ),
            upd_provider AS (
                UPDATE etown_provider p SET
                    businessname          = COALESCE($3,  p.businessname),
                    address               = COALESCE($4,  p.address),
                    description           = COALESCE($5,  p.description),
                    availability          = COALESCE($6,  p.availability),
                    email                 = COALESCE($7,  p.email),
                    serviceimageurl       = COALESCE($8,  p.serviceimageurl),
                    mobilenumber          = COALESCE($9,  p.mobilenumber),
                    businesscategory      = COALESCE($10, p.businesscategory),
                    latitude              = COALESCE($11, p.latitude),
                    longitude             = COALESCE($12, p.longitude),
                    city                  = COALESCE($13, p.city),
                    statecode             = COALESCE($14, p.statecode),
                    areaname              = COALESCE($15, p.areaname),
                    additionaladdressinfo = COALESCE($16, p.additionaladdressinfo),
                    servicetype           = COALESCE($17, p.servicetype),
                    background_image      = CASE
                                                WHEN $20::boolean THEN NULL
                                                ELSE COALESCE($21, p.background_image)
                                            END,
                    whatsapp              = CASE
                                                WHEN $9 IS NOT NULL AND $9 IS DISTINCT FROM p.mobilenumber
                                                THEN true ELSE p.whatsapp
                                            END,
                    modifieddate          = NOW()
                FROM target t
                WHERE p.providerid = t.t_providerid
                RETURNING p.providerid, p.businessname, p.description, p.businesscategory,
                          p.address, p.city, p.townname, p.areaname, p.statecode,
                          p.latitude, p.longitude, p.mobilenumber, p.email,
                          p.additionaladdressinfo, p.servicetype,
                          p.availability, p.serviceimageurl, p.background_image
            ),
            upd_services AS (
                UPDATE etown_providerservices ps SET
                    businessname          = COALESCE($3,  ps.businessname),
                    address               = COALESCE($4,  ps.address),
                    availability          = COALESCE($6,  t.t_availability),
                    city                  = COALESCE($13, ps.city),
                    email                 = COALESCE($7,  ps.email),
                    mobilenumber          = COALESCE($9,  ps.mobilenumber),
                    serviceimageurl       = COALESCE($8,  t.t_serviceimageurl),
                    latitude              = COALESCE($18, ps.latitude),
                    longitude             = COALESCE($19, ps.longitude),
                    statecode             = COALESCE($14, ps.statecode),
                    additionaladdressinfo = COALESCE($16, ps.additionaladdressinfo),
                    areaname              = COALESCE($15, ps.areaname),
                    modifieddate          = NOW()
                FROM target t
                WHERE ps.providerid = t.t_providerid
                   OR (ps.contactid = $1 AND ps.userprofileid = $2)
                RETURNING ps.providerservicesid
            ),
            upd_services_copy AS (
                UPDATE etown_providerservices_copy psc SET
                    businessname          = COALESCE($3,  psc.businessname),
                    address               = COALESCE($4,  psc.address),
                    availability          = COALESCE($6,  t.t_availability),
                    city                  = COALESCE($13, psc.city),
                    email                 = COALESCE($7,  psc.email),
                    mobilenumber          = COALESCE($9,  psc.mobilenumber),
                    serviceimageurl       = COALESCE($8,  t.t_serviceimageurl),
                    latitude              = COALESCE($11, psc.latitude),
                    longitude             = COALESCE($12, psc.longitude),
                    statecode             = COALESCE($14, psc.statecode),
                    additionaladdressinfo = COALESCE($16, psc.additionaladdressinfo),
                    areaname              = COALESCE($15, psc.areaname),
                    modifieddate          = NOW()
                FROM target t
                WHERE psc.providerid = t.t_providerid
                   OR (psc.contactid = $1 AND psc.userprofileid = $2)
                RETURNING psc.providerservicesid
            )
            SELECT up.*,
                   ARRAY(SELECT providerservicesid FROM upd_services)      AS providerservices_ids,
                   ARRAY(SELECT providerservicesid FROM upd_services_copy) AS providerservices_copy_ids
            FROM upd_provider up
            """,
            contact_id, provider_userprofile,
            updates.get("business_name"),
            updates.get("address"),
            updates.get("description"),
            new_availability,
            updates.get("email"),
            new_image_url,
            updates.get("mobilenumber"),
            business_category,
            lat_float,
            lon_float,
            updates.get("city"),
            updates.get("statecode"),
            updates.get("area_location"),
            updates.get("additionaladdressinfo"),
            updates.get("servicetype"),
            lat_str,
            lon_str,
            clear_background,
            new_background_url,
        )
        if not row:
            return ("Provider not found", None)

        provider_id = row["providerid"]
        logger.info(
            f"[edit_profile] Provider updated: providerid={provider_id} "
            f"services={len(row['providerservices_ids'])} "
            f"copies={len(row['providerservices_copy_ids'])} "
            f"fields={sorted(k for k, v in updates.items() if v is not None)}"
        )
        if updates.get("business_name"):
            try:
                await propagate_display_name_to_feed(
                    contact_id=contact_id,
                    userprofile_ids=[provider_userprofile],
                    new_name=row["businessname"],
                    role=1,
                )
            except Exception as exc:
                logger.error(
                    "[edit_profile] Feed name propagation failed | providerid=%s | error=%s",
                    provider_id, exc,
                 exc_info=True)

        return ("Profile updated successfully", {
            "providerid": provider_id,
            "businessname": row["businessname"],
            "providerservices_ids": [str(i) for i in row["providerservices_ids"]],
            "providerservices_copy_ids": [str(i) for i in row["providerservices_copy_ids"]],
            "resolved": {
                "business_name": row["businessname"],
                "description": row["description"],
                "business_category": row["businesscategory"],
                "address": row["address"],
                "city": row["city"],
                "townname": row["townname"],
                "areaname": row["areaname"],
                "statecode": row["statecode"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "mobilenumber": row["mobilenumber"],
                "email": row["email"],
                "additionaladdressinfo": row["additionaladdressinfo"],
                "servicetype": row["servicetype"],
                "availability": row["availability"],
                "serviceimageurl": row["serviceimageurl"],
                "background_image": row["background_image"],
            },
            "embedding_text_changed": any(
                updates.get(k) is not None
                for k in ("business_name", "description", "city", "address")
            ),
        })

    @staticmethod
    async def reassign_town_after_address_change(
        provider_id: str,
        business_name: Optional[str],
        latitude: Optional[float],
        longitude: Optional[float],
        stored_townname: Optional[str],
        city: Optional[str],
        areaname: Optional[str],
        statecode: Optional[str],
        providerservices_ids: List[str],
    ) -> None:
        """Re-derive the provider's town from its new coordinates and move the town link; never raises.

        Args:
            provider_id: UUID of the provider whose address changed.
            business_name: Business name stored on the provider-town link record.
            latitude: Post-edit latitude, or None when the caller sent no coordinates.
            longitude: Post-edit longitude, or None when the caller sent no coordinates.
            stored_townname: Town currently on etown_provider, used when geocoding yields none.
            city: Post-edit city, passed to town assignment as the parent-district context.
            areaname: Post-edit areaname, used when geocoding yields none.
            statecode: Post-edit statecode, used when geocoding yields none.
            providerservices_ids: Service rows to mirror the refined areaname/statecode onto.
        """
        try:
            geo_townname = geo_areaname = geo_statecode = None
            if latitude is not None and longitude is not None:
                try:
                    geo_result = await get_location_details_logic(latitude, longitude)
                except Exception as exc:
                    logger.warning(
                        f"[town_reassign] Geocoding failed for provider {provider_id}: {exc}"
                    )
                    geo_result = None
                components = getattr(geo_result, "parsed_components", None) if geo_result else None
                if components:
                    geo_areaname = (
                        components.get("sublocality_level_1")
                        or components.get("sublocality")
                        or components.get("locality")
                    )
                    geo_townname = (
                        components.get("locality")
                        or components.get("administrative_area_level_3")
                        or components.get("administrative_area_level_2")
                    )
                    state_name = components.get("administrative_area_level_1")
                    if state_name:
                        geo_statecode = convert_state_name_to_code(state_name)

            final_townname = geo_townname or stored_townname
            final_areaname = geo_areaname or areaname
            final_statecode = geo_statecode or statecode

            if not final_townname:
                logger.warning(
                    f"[town_reassign] No town resolved for provider {provider_id} "
                    f"(geocoded={geo_townname!r} stored={stored_townname!r}); link left as is"
                )
                return

            if geo_townname or geo_areaname or geo_statecode:
                await Database.execute(
                    """
                    WITH upd_provider AS (
                        UPDATE etown_provider SET
                            townname     = COALESCE($2, townname),
                            areaname     = COALESCE($3, areaname),
                            statecode    = COALESCE($4, statecode),
                            modifieddate = NOW()
                        WHERE providerid = $1
                    ),
                    upd_services AS (
                        UPDATE etown_providerservices SET
                            areaname     = COALESCE($3, areaname),
                            statecode    = COALESCE($4, statecode),
                            modifieddate = NOW()
                        WHERE providerid = $1
                    )
                    UPDATE etown_providerservices_copy SET
                        areaname     = COALESCE($3, areaname),
                        statecode    = COALESCE($4, statecode),
                        modifieddate = NOW()
                    WHERE providerid = $1
                    """,
                    provider_id, geo_townname, geo_areaname, geo_statecode,
                )
                await sync_provider_profile_update_to_dataverse(
                    provider_id=provider_id,
                    providerservices_ids=providerservices_ids,
                    provider_payload={
                        "ets_townname": geo_townname,
                        "ets_areaname": geo_areaname,
                        "ets_statecode": geo_statecode,
                    },
                    service_payload={
                        "ets_areaname": geo_areaname,
                        "ets_statecode": geo_statecode,
                    },
                )

            await handle_provider_town_assignment(
                provider_id=provider_id,
                business_name=business_name,
                townname=final_townname,
                cityname=city,
                statecode=final_statecode,
                areaname=final_areaname,
                upsert_link=True,
            )
            logger.info(
                f"[town_reassign] Provider {provider_id} town resolved to {final_townname!r} "
                f"(geocoded={geo_townname!r} stored={stored_townname!r} area={final_areaname!r})"
            )
        except PlanLimitError as exc:
            logger.warning(f"[town_reassign] Town cap reached for provider {provider_id}: {exc}")
        except Exception as exc:
            logger.error(
                f"[town_reassign] Failed for provider {provider_id}: {exc}", exc_info=True
            )


async def _attach_provider_badges(bookings: List[Dict[str, Any]]) -> None:
    """Fill each booking's Etown_providertype and Etown_badge_label in place from the service provider's plan."""
    badge_map = await resolve_provider_badges(
        b["BookingDetails"].get("Etown_serviceproviderUserProfileId") for b in bookings
    )
    for booking in bookings:
        details = booking["BookingDetails"]
        badge = badge_map.get(details.get("Etown_serviceproviderUserProfileId")) or {}
        details["Etown_providertype"] = badge.get("provider_type")
        details["Etown_badge_label"] = badge.get("badge_label")


class ProviderRequestListService:
    """Service class for provider request list operations."""

    @staticmethod
    @with_retry()
    async def get_provider_request_list(
        contactid: str,
        userprofileid: str,
    ) -> List[Dict[str, Any]]:
        """Return the provider's non-completed bookings, newest first, each with its service line items.

        Args:
            contactid: The provider's contact ID.
            userprofileid: The provider's user profile ID.

        Returns:
            List of dicts with BookingDetails and ServiceDetails keys.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        logger.info(
            f"get_provider_request_list called | contactid={contactid} userprofileid={userprofileid}"
        )

        BOOKING_DETAILS_QUERY = """
        SELECT
            ar.requestorrole AS requestor_role,
            ar.requestorrolename AS requestor_rolename,
            rr.userprofileid AS requestor_userprofileid,
            COALESCE(ar.allrequestid, rsl.bookingid) AS allrequestid,
            ar.requestid,
            ar.requestorname,
            ar.requestorid,
            ar.requestoruserprofileid,
            ar.requestoruserprofileidname,
            ar.requestoraddress,
            ar.requestorlatitude,
            ar.requestorlongitude,
            COALESCE(ar.requeststatus, 'Requested') AS requeststatus,
            COALESCE(ar.createdon, rsl.createdon) AS createdon,
            ar.requestorservicedatetime,
            ar.otp,
            ar.instructions,
            ar.cancelreason,
            ar.bookingacceptedtime,
            ar.bookingcompletedtime,
            ar.bookingcancelledtime,
            rc.profileimage,
            rr.mobilenumber AS requestor_mobilenumber,
            rp.publicsearchprovider,
            rp.providertype,
            rp.servicetype AS provider_servicetype,
            rc.profileimage AS etown_requestorprofileimageurl,

            COALESCE(rc.deleted, 'false') AS requestor_deleted,
            CASE
                WHEN rc.deactivated = 'true' AND rc.reactivation IS DISTINCT FROM 'true'
                THEN 'true' ELSE 'false'
            END AS requestor_deactivated,
            COALESCE(rp.deleted, 'false') AS provider_deleted,
            CASE
                WHEN rp.deactivated = 'true' AND rp.reactivation IS DISTINCT FROM 'true'
                THEN 'true' ELSE 'false'
            END AS provider_deactivated
        FROM etown_requestservicelists rsl
        LEFT JOIN etown_allrequests ar
            ON ar.allrequestid = rsl.bookingid
        LEFT JOIN etown_userprofile rr
            ON ar.requestoruserprofileid = rr.userprofileid
        LEFT JOIN etown_contact rc
            ON ar.requestorid = rc.contactid
        LEFT JOIN etown_provider rp
            ON rsl.serviceproviderid = rp.providerid
        WHERE
            (
                (
                    (SELECT role FROM etown_userprofile
                     WHERE contactid = $1
                       AND userprofileid = $2) <> 1
                    AND ar.requestorid = $1
                    AND ar.requestoruserprofileid = $2
                )
                OR
                (
                    (SELECT role FROM etown_userprofile
                     WHERE contactid = $1
                       AND userprofileid = $2) = 1
                    AND rsl.serviceprovidercontactid = $1
                    AND rsl.serviceprovideruserprofileid = $2
                )
            )
            AND COALESCE(ar.requeststatus, 'Requested') <> 'completed'
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
            ar.createdon,
            rsl.createdon,
            ar.requestorservicedatetime,
            ar.otp,
            ar.instructions,
            ar.cancelreason,
            ar.bookingacceptedtime,
            ar.bookingcompletedtime,
            rc.profileimage,
            rp.publicsearchprovider,
            rp.providertype,
            rp.servicetype,
            rp.serviceimageurl,

            rc.deleted,
            rc.deactivated,
            rc.reactivation,
            rp.deleted,
            rp.deactivated,
            rp.reactivation
        ORDER BY COALESCE(ar.createdon, rsl.createdon) DESC NULLS LAST
"""

        SERVICE_DETAILS_QUERY = """
            SELECT
                rsl.requestservicelistsid,
                rsl.servicename,
                rsl.bookingid,
                rsl.serviceprovidercurrentaddress,
                rsl.serviceprovidercontactid,
                rsl.serviceprovideruserprofileid,
                ar.allrequestid AS bookingidname,
                rsl.serviceprovideraddress,
                rsl.serviceprovidercurrentaddresslatitude,
                rsl.serviceprovidercurrentaddresslongitude,
                rsl.serviceproviderid,
                rsl.serviceproviderlatitude,
                rsl.serviceproviderlongitude,
                rsl.quantity,
                rsl.price,
                rp.serviceimageurl AS own_userprofileimageurl,
                rsl.name,
                ps.servicedescription,
                rsl.serviceimageurl,
                pr_role.role             AS provider_role,
                pr_role.rolename         AS provider_rolename,
                pr_role.mobilenumber     AS provider_mobilenumber
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
            ORDER BY rsl.createdon DESC
        """

        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            caller_role_row = await con.fetchrow(
                "SELECT role FROM etown_userprofile WHERE contactid = $1 AND userprofileid = $2",
                contactid,
                userprofileid,
            )
            caller_is_provider = bool(caller_role_row and caller_role_row["role"] == 1)

            booking_rows = await con.fetch(
                BOOKING_DETAILS_QUERY,
                contactid,
                userprofileid,
            )

            logger.info(
                f"provider_request_list | found {len(booking_rows)} bookings"
                f" for contactid={contactid}"
            )

            bookings: List[Dict[str, Any]] = []

            seen_booking_ids: set = set()

            for row in booking_rows:
                raw = dict(row)

                dedup_key = raw.get("allrequestid")
                if dedup_key is not None:
                    if dedup_key in seen_booking_ids:
                        logger.debug(
                            f"provider_request_list | skipping duplicate booking {dedup_key}"
                        )
                        continue
                    seen_booking_ids.add(dedup_key)

                booking_details = {
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
                    "Etown_requestedservicedatetime":               raw.get("requestorservicedatetime"),
                    "Etown_otp":                                    raw.get("otp"),
                    "Etown_instructions":                           _normalize_instructions(raw.get("instructions")),
                    "Etown_bookingcreatedon":                       (lambda dt: (lambda d: f"{d.month}/{d.day}/{d.year} {int(d.strftime('%I'))}:{d.strftime('%M')} {d.strftime('%p')}")(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("createdon")),
                    "Etown_updatedcompletedtime":                   (lambda dt: (lambda d: d.strftime('%Y-%m-%d %H:%M:%S'))(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("bookingcompletedtime")),
                    "Etown_updatedacceptedtime":                    (lambda dt: (lambda d: d.strftime('%Y-%m-%d %H:%M:%S'))(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("bookingacceptedtime")),
                    "Etown_cancelledtime":                          (lambda dt: (lambda d: d.strftime('%Y-%m-%d %H:%M:%S'))(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("bookingcancelledtime")),
                    "Etown_ownprofileimageurl":                     None,
                    "Etown_cancelreason":                           raw.get("cancelreason"),
                    "Etown_publicsearchprovider":                   raw.get("publicsearchprovider"),
                    "Etown_providertype":                           raw.get("providertype"),
                    "Etown_servicetype":                            raw.get("provider_servicetype"),
                    "Etown_providerrole":                           1,
                    "Etown_providerrolename":                       "Provider",
                    "Etown_requestordeleted":                       raw.get("requestor_deleted"),
                    "Etown_requestordeactivated":                   raw.get("requestor_deactivated"),
                    "Etown_providerdeleted":                        raw.get("provider_deleted"),
                    "Etown_providerdeactivated":                    raw.get("provider_deactivated"),
                    "Etown_mobilenumber":                           None,
                    "Etown_providercontactid":                      None,
                    "Etown_serviceproviderUserProfileId":           None,
                    "Etown_ServiceProviderBusinessname":            None,
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

                svc_rows_raw = [
                    dict(r) for r in await con.fetch(SERVICE_DETAILS_QUERY, booking_id)
                ]

                if svc_rows_raw:
                    first_svc = svc_rows_raw[0]
                    booking_details["Etown_ownprofileimageurl"]                     = (
                        first_svc.get("own_userprofileimageurl") if caller_is_provider else raw.get("profileimage")
                    )
                    if not caller_is_provider:
                        booking_details["Etown_mobilenumber"]                       = first_svc.get("provider_mobilenumber")
                    booking_details["Etown_providercontactid"]                      = first_svc.get("serviceprovidercontactid")
                    booking_details["Etown_serviceproviderUserProfileId"]           = first_svc.get("serviceprovideruserprofileid")
                    booking_details["Etown_ServiceProviderBusinessname"]            = first_svc.get("servicename")
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
                        "Etown_price":              sr.get("price"),
                        "Etown_serviceimageurl":    sr.get("serviceimageurl"),
                        "Etown_bookingId":          str(sr.get("bookingid")) if sr.get("bookingid") is not None else None,
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

            await _attach_provider_badges(bookings)

            logger.info(
                f"provider_request_list | returning {len(bookings)} bookings"
                f" with service details"
            )
            return bookings

    @staticmethod
    @with_retry()
    async def get_provider_completed_list(
        contactid: str,
        userprofileid: str,
    ) -> List[Dict[str, Any]]:
        """Return the provider's completed bookings, newest first, each with its rating and service line items.

        Args:
            contactid: The provider's contact ID.
            userprofileid: The provider's user profile ID.

        Returns:
            List of dicts with BookingDetails, Rating and ServiceDetails keys.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        logger.info(
            f"get_provider_completed_list called | contactid={contactid} userprofileid={userprofileid}"
        )

        BOOKING_DETAILS_QUERY = """
        SELECT
    ar.requestorrole AS requestor_role,
    ar.requestorrolename AS requestor_rolename,
    rr.userprofileid AS requestor_userprofileid,
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
    ar.createdon,
    ar.requestorservicedatetime,
    ar.otp,
    ar.instructions,
    ar.cancelreason,
    ar.bookingacceptedtime,
    ar.bookingcompletedtime,
    ar.ratingvalue,
    rc.profileimage,
    rr.mobilenumber AS requestor_mobilenumber,
    rp.publicsearchprovider,
    rp.providertype,
    rp.servicetype AS provider_servicetype,
    rp.creationdate AS provider_creationdate,
    rc.profileimage AS etown_requestorprofileimageurl,

    COALESCE(rc.deleted, 'false') AS requestor_deleted,
    CASE
        WHEN rc.deactivated = 'true' AND rc.reactivation IS DISTINCT FROM 'true'
        THEN 'true' ELSE 'false'
    END AS requestor_deactivated,
    COALESCE(rp.deleted, 'false') AS provider_deleted,
    CASE
        WHEN rp.deactivated = 'true' AND rp.reactivation IS DISTINCT FROM 'true'
        THEN 'true' ELSE 'false'
    END AS provider_deactivated
FROM etown_allrequests ar
LEFT JOIN etown_userprofile rr
    ON ar.requestoruserprofileid = rr.userprofileid
LEFT JOIN etown_contact rc
    ON ar.requestorid = rc.contactid
LEFT JOIN etown_requestservicelists rsl
    ON ar.allrequestid = rsl.bookingid
LEFT JOIN etown_provider rp
    ON rsl.serviceproviderid = rp.providerid
WHERE
    (
        (SELECT role FROM etown_userprofile
         WHERE contactid = $1
           AND userprofileid = $2) <> 1
        AND ar.requestorid = $1
        AND ar.requestoruserprofileid = $2
    )
    OR
    (
        (SELECT role FROM etown_userprofile
         WHERE contactid = $1
           AND userprofileid = $2) = 1
        AND rsl.serviceprovidercontactid = $1
        AND rsl.serviceprovideruserprofileid = $2
    )
    AND ar.requeststatus IS NOT NULL
    AND ar.requeststatus = 'completed'
    GROUP BY
    rr.role,
    rr.userprofileid,
    rr.mobilenumber,
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
    ar.createdon,
    ar.requestorservicedatetime,
    ar.otp,
    ar.instructions,
    ar.cancelreason,
    ar.bookingacceptedtime,
    ar.bookingcompletedtime,
    ar.ratingvalue,
    rc.profileimage,
	rp.publicsearchprovider,
	rp.providertype,
	rp.servicetype,
	rp.serviceimageurl,
	rp.creationdate,

    rc.deleted,
    rc.deactivated,
    rc.reactivation,

    rp.deleted,
    rp.deactivated,
    rp.reactivation
ORDER BY ar.createdon DESC;
"""

        SERVICE_DETAILS_QUERY = """
            SELECT
                rsl.requestservicelistsid,
                rsl.servicename,
                rsl.bookingid,
                rsl.serviceprovidercurrentaddress,
                rsl.serviceprovidercontactid,
                rsl.serviceprovideruserprofileid,
                ar.allrequestid AS bookingidname,
                rsl.serviceprovideraddress,
                rsl.serviceprovidercurrentaddresslatitude,
                rsl.serviceprovidercurrentaddresslongitude,
                rsl.serviceproviderid,
                rsl.serviceproviderlatitude,
                rsl.serviceproviderlongitude,
                rsl.quantity,
                rsl.price,
                rp.serviceimageurl AS own_userprofileimageurl,
                rsl.name,
                ps.servicedescription,
                rsl.serviceimageurl,
                pr_role.role             AS provider_role,
                pr_role.rolename         AS provider_rolename,
                pr_role.mobilenumber     AS provider_mobilenumber
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
            ORDER BY rsl.createdon DESC
        """

        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            caller_role_row = await con.fetchrow(
                "SELECT role FROM etown_userprofile WHERE contactid = $1 AND userprofileid = $2",
                contactid,
                userprofileid,
            )
            caller_is_provider = bool(caller_role_row and caller_role_row["role"] == 1)

            booking_rows = await con.fetch(
                BOOKING_DETAILS_QUERY,
                contactid,
                userprofileid,
            )

            logger.info(
                f"provider_completed_list | found {len(booking_rows)} bookings"
                f" for contactid={contactid}"
            )

            bookings: List[Dict[str, Any]] = []

            for row in booking_rows:
                raw = dict(row)

                booking_details = {
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
                    "Etown_requestedservicedatetime":               raw.get("requestorservicedatetime"),
                    "Etown_otp":                                    raw.get("otp"),
                    "Etown_instructions":                           _normalize_instructions(raw.get("instructions")),
                    "Etown_bookingcreatedon":                       (lambda dt: (lambda d: f"{d.month}/{d.day}/{d.year} {int(d.strftime('%I'))}:{d.strftime('%M')} {d.strftime('%p')}")(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("createdon")),
                    "Etown_updatedcompletedtime":                   (lambda dt: (lambda d: d.strftime('%Y-%m-%d %H:%M:%S'))(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("bookingcompletedtime")),
                    "Etown_updatedacceptedtime":                    (lambda dt: (lambda d: d.strftime('%Y-%m-%d %H:%M:%S'))(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("bookingacceptedtime")),
                    "Etown_ownprofileimageurl":                     None,
                    "Etown_cancelreason":                           raw.get("cancelreason"),
                    "Etown_publicsearchprovider":                   raw.get("publicsearchprovider"),
                    "Etown_providertype":                           raw.get("providertype"),
                    "Etown_servicetype":                            raw.get("provider_servicetype"),
                    "Etown_businesscreatedon":                      (lambda dt: (lambda d: f"{d.month}/{d.day}/{d.year} {int(d.strftime('%I'))}:{d.strftime('%M')} {d.strftime('%p')}")(dt + timedelta(hours=5, minutes=30)) if dt else None)(raw.get("provider_creationdate")),
                    "Etown_providerrole":                           1,
                    "Etown_providerrolename":                       "Provider",
                    "Etown_requestordeleted":                       raw.get("requestor_deleted"),
                    "Etown_requestordeactivated":                   raw.get("requestor_deactivated"),
                    "Etown_providerdeleted":                        raw.get("provider_deleted"),
                    "Etown_providerdeactivated":                    raw.get("provider_deactivated"),
                    "Etown_mobilenumber":                           None,
                    "Etown_providercontactid":                      None,
                    "Etown_serviceproviderUserProfileId":           None,
                    "Etown_ServiceProviderBusinessname":            None,
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

                svc_rows_raw = [
                    dict(r) for r in await con.fetch(SERVICE_DETAILS_QUERY, booking_id)
                ]

                if svc_rows_raw:
                    first_svc = svc_rows_raw[0]
                    booking_details["Etown_ownprofileimageurl"]                     = (
                        first_svc.get("own_userprofileimageurl") if caller_is_provider else raw.get("profileimage")
                    )
                    if not caller_is_provider:
                        booking_details["Etown_mobilenumber"]                       = first_svc.get("provider_mobilenumber")
                    booking_details["Etown_providercontactid"]                      = first_svc.get("serviceprovidercontactid")
                    booking_details["Etown_serviceproviderUserProfileId"]           = first_svc.get("serviceprovideruserprofileid")
                    booking_details["Etown_ServiceProviderBusinessname"]            = first_svc.get("servicename")
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
                        "Etown_price":              sr.get("price"),
                        "Etown_serviceimageurl":    sr.get("serviceimageurl"),
                        "Etown_bookingId":          str(sr.get("bookingid")) if sr.get("bookingid") is not None else None,
                    }
                    for sr in svc_rows_raw
                ]

                bookings.append({
                    "BookingDetails": booking_details,
                    "Rating": int(raw["ratingvalue"]) if raw.get("ratingvalue") is not None else None,
                    "ServiceDetails": service_details,
                })

            await hydrate_instruction_senders(
                [b["BookingDetails"].get("Etown_instructions") for b in bookings], con
            )

            await _attach_provider_badges(bookings)

            logger.info(
                f"provider_completed_list | returning {len(bookings)} bookings"
                f" with service details"
            )
            return bookings

def _add_months(day, months: int):
    """Return `day` shifted forward by `months` calendar months, clamped to month end."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    dom = day.day
    while True:
        try:
            return datetime(year, month, dom).date()
        except ValueError:
            logger.debug("provider: %04d-%02d has no day %d; clamping towards the month end", year, month, dom)
            dom -= 1


async def fetch_date_range_summary(
    contactid: str,
    userprofileid: str,
    start_date: Optional[str],
    end_date: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Summarise a provider's offer views, circle, offers and bookings over a date range, bucketed by interval.

    Args:
        contactid: The provider's contact ID.
        userprofileid: The provider's user profile ID.
        start_date: Range start as YYYY-MM-DD.
        end_date: Range end as YYYY-MM-DD, inclusive.

    Returns:
        Dict of summary, total_metric, interval and insights, or None on bad dates or failure.
    """
    if not start_date or not end_date:
        return None

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        logger.error(f"fetch_date_range_summary: invalid date format | start={start_date} end={end_date}", exc_info=True)
        return None

    offerview_total_query = """
        SELECT COUNT(*) AS offerview_count
        FROM etown_offer_views ov
        JOIN etown_offers o ON ov.offersid = o.offersid
        WHERE o.userprofileid = $1
          AND ov.created_on::date >= $2::date
          AND ov.created_on::date <= $3::date
    """

    mycircle_total_query = """
        SELECT COUNT(DISTINCT upid) AS mycircle_count
        FROM (
            SELECT touserprofileid AS upid
            FROM etown_providerrelation
            WHERE fromcontactid = $1
              AND fromuserprofileid = $2
              AND createdon::date >= $3::date
              AND createdon::date <= $4::date
              AND touserprofileid IS NOT NULL
            UNION
            SELECT to_userprofile AS upid
            FROM etown_memberrelation
            WHERE from_contactid = $1
              AND from_userprofile = $2
              AND createdon::date >= $3::date
              AND createdon::date <= $4::date
              AND to_userprofile IS NOT NULL
        ) combined
    """

    offer_total_query = """
        SELECT COUNT(*) AS offer_count
        FROM etown_offers
        WHERE contactid = $1
          AND userprofileid = $2
          AND createdon::date >= $3::date
          AND createdon::date <= $4::date
    """

    booking_total_query = """
        SELECT COUNT(DISTINCT rsl.bookingid) AS booking_count
        FROM etown_requestservicelists rsl
        LEFT JOIN etown_allrequests ar ON ar.allrequestid = rsl.bookingid
        WHERE rsl.serviceprovideruserprofileid = $1
          AND (ar.requeststatus IS NULL OR LOWER(ar.requeststatus) IN ('requested', 'accepted', 'confirmed', 'completed', 'cancelled'))
          AND COALESCE(ar.createdon, rsl.createdon)::date >= $2::date
          AND COALESCE(ar.createdon, rsl.createdon)::date <= $3::date
    """

    total_offerview_query = """
        SELECT COUNT(*) AS offerview_count
        FROM etown_offer_views ov
        JOIN etown_offers o ON ov.offersid = o.offersid
        WHERE o.userprofileid = $1
    """

    total_mycircle_query = """
        SELECT COUNT(DISTINCT upid) AS mycircle_count
        FROM (
            SELECT touserprofileid AS upid
            FROM etown_providerrelation
            WHERE fromcontactid = $1
              AND fromuserprofileid = $2
              AND touserprofileid IS NOT NULL
            UNION
            SELECT to_userprofile AS upid
            FROM etown_memberrelation
            WHERE from_contactid = $1
              AND from_userprofile = $2
              AND to_userprofile IS NOT NULL
        ) combined
    """

    total_booking_query = """
        SELECT COUNT(DISTINCT rsl.bookingid) AS booking_count
        FROM etown_requestservicelists rsl
        LEFT JOIN etown_allrequests ar ON ar.allrequestid = rsl.bookingid
        WHERE rsl.serviceprovideruserprofileid = $1
          AND (ar.requeststatus IS NULL OR LOWER(ar.requeststatus) IN ('requested', 'accepted', 'confirmed', 'completed', 'cancelled'))
    """

    offerview_daily_query = """
        SELECT ov.created_on::date AS day, COUNT(*) AS offerview_count
        FROM etown_offer_views ov
        JOIN etown_offers o ON ov.offersid = o.offersid
        WHERE o.userprofileid = $1
          AND ov.created_on::date >= $2::date
          AND ov.created_on::date <= $3::date
        GROUP BY ov.created_on::date
        ORDER BY ov.created_on::date
    """

    mycircle_daily_query = """
        SELECT day, COUNT(DISTINCT upid) AS mycircle_count
        FROM (
            SELECT createdon::date AS day, touserprofileid AS upid
            FROM etown_providerrelation
            WHERE fromcontactid = $1
              AND fromuserprofileid = $2
              AND createdon::date >= $3::date
              AND createdon::date <= $4::date
              AND touserprofileid IS NOT NULL
            UNION
            SELECT createdon::date AS day, to_userprofile AS upid
            FROM etown_memberrelation
            WHERE from_contactid = $1
              AND from_userprofile = $2
              AND createdon::date >= $3::date
              AND createdon::date <= $4::date
              AND to_userprofile IS NOT NULL
        ) combined
        GROUP BY day
        ORDER BY day
    """

    booking_daily_query = """
        SELECT COALESCE(ar.createdon, rsl.createdon)::date AS day, COUNT(DISTINCT rsl.bookingid) AS booking_count
        FROM etown_requestservicelists rsl
        LEFT JOIN etown_allrequests ar ON ar.allrequestid = rsl.bookingid
        WHERE rsl.serviceprovideruserprofileid = $1
          AND (ar.requeststatus IS NULL OR LOWER(ar.requeststatus) IN ('requested', 'accepted', 'confirmed', 'completed', 'cancelled'))
          AND COALESCE(ar.createdon, rsl.createdon)::date >= $2::date
          AND COALESCE(ar.createdon, rsl.createdon)::date <= $3::date
        GROUP BY COALESCE(ar.createdon, rsl.createdon)::date
        ORDER BY COALESCE(ar.createdon, rsl.createdon)::date
    """

    try:
        (
            offerview_row,
            mycircle_row,
            offer_row,
            booking_row,
            offerview_daily_rows,
            mycircle_daily_rows,
            booking_daily_rows,
            total_offerview_row,
            total_mycircle_row,
            total_booking_row,
        ) = await asyncio.gather(
            Database.fetchrow(offerview_total_query, userprofileid, start, end),
            Database.fetchrow(mycircle_total_query, contactid, userprofileid, start, end),
            Database.fetchrow(offer_total_query, contactid, userprofileid, start, end),
            Database.fetchrow(booking_total_query, userprofileid, start, end),
            Database.fetch(offerview_daily_query, userprofileid, start, end),
            Database.fetch(mycircle_daily_query, contactid, userprofileid, start, end),
            Database.fetch(booking_daily_query, userprofileid, start, end),
            Database.fetchrow(total_offerview_query, userprofileid),
            Database.fetchrow(total_mycircle_query, contactid, userprofileid),
            Database.fetchrow(total_booking_query, userprofileid),
        )
        offerview_by_day = {row["day"]: int(row["offerview_count"]) for row in offerview_daily_rows}
        mycircle_by_day = {row["day"]: int(row["mycircle_count"]) for row in mycircle_daily_rows}
        booking_by_day = {row["day"]: int(row["booking_count"]) for row in booking_daily_rows}
        span_end = end + timedelta(days=1)
        if span_end >= _add_months(start, 12):
            interval = "monthly"
        elif span_end >= _add_months(start, 1):
            interval = "weekly"
        else:
            interval = "daily"

        insights = []
        current = start
        while current <= end:
            if interval == "daily":
                insights.append({
                    "date": current.isoformat(),
                    "day": current.strftime("%A"),
                    "offerview_count": offerview_by_day.get(current, 0),
                    "mycircle_count": mycircle_by_day.get(current, 0),
                    "booking_count": booking_by_day.get(current, 0),
                })
                current += timedelta(days=1)
                continue

            if interval == "weekly":
                bucket_end = min(current + timedelta(days=6), end)
                label = f"{current.strftime('%d %b')} - {bucket_end.strftime('%d %b')}"
            else:
                month_start = datetime(current.year, current.month, 1).date()
                bucket_end = min(_add_months(month_start, 1) - timedelta(days=1), end)
                label = current.strftime("%b %Y")

            bucket_days = []
            cursor = current
            while cursor <= bucket_end:
                bucket_days.append(cursor)
                cursor += timedelta(days=1)

            insights.append({
                "date": current.isoformat(),
                "start_date": current.isoformat(),
                "end_date": bucket_end.isoformat(),
                "label": label,
                "offerview_count": sum(offerview_by_day.get(d, 0) for d in bucket_days),
                "mycircle_count": sum(mycircle_by_day.get(d, 0) for d in bucket_days),
                "booking_count": sum(booking_by_day.get(d, 0) for d in bucket_days),
            })
            current = bucket_end + timedelta(days=1)

        logger.info(
            f"fetch_date_range_summary | userprofileid={userprofileid} | days={(end - start).days + 1}"
            f" | interval={interval} | buckets={len(insights)}"
        )

        return {
            "summary": {
                "offerview_count": int(offerview_row["offerview_count"]) if offerview_row else 0,
                "mycircle_count": int(mycircle_row["mycircle_count"]) if mycircle_row else 0,
                "offer_count": int(offer_row["offer_count"]) if offer_row else 0,
                "booking_count": int(booking_row["booking_count"]) if booking_row else 0,
            },
            "total_metric": {
                "offerview_count": int(total_offerview_row["offerview_count"]) if total_offerview_row else 0,
                "mycircle_count": int(total_mycircle_row["mycircle_count"]) if total_mycircle_row else 0,
                "booking_count": int(total_booking_row["booking_count"]) if total_booking_row else 0,
            },
            "interval": interval,
            "insights": insights,
        }
    except Exception as e:
        logger.error(
            f"fetch_date_range_summary failed | userprofileid={userprofileid} | error={e}",
            exc_info=True,
        )
        return None


PROFILE_VIEWERS_PAGE_SIZE = 10


async def fetch_profile_viewers(
    contactid: str,
    userprofileid: str,
    page: int = 1,
    limit: int = PROFILE_VIEWERS_PAGE_SIZE,
) -> List[Dict[str, Any]]:
    """Fetch one page of the users who viewed a provider's profile, newest first, plus the total viewer count.

    Args:
        contactid: The provider's contact ID, used for the relation and invitation legs.
        userprofileid: The provider's user profile ID the views were recorded against.
        page: 1-based page number.
        limit: Viewers per page.

    Returns:
        Tuple of the viewer row dicts and the total from get_provider_view_count().
    """
    query = """
        SELECT
            CASE
                WHEN up.role = 1 THEN pv.businessname
                WHEN up.role = 2 THEN c.firstname
                ELSE NULL
            END                         AS name,
            CASE WHEN EXISTS (
                SELECT 1 FROM public.etown_retired_identity r
                 WHERE (r.identity_id = v.userprofileid
                     OR r.identity_id = up.contactid)
                   AND r.deleted = 'true'
            ) THEN 'true' ELSE 'false' END AS viewer_deleted,
            CASE WHEN EXISTS (
                SELECT 1 FROM public.etown_retired_identity r
                 WHERE (r.identity_id = v.userprofileid
                     OR r.identity_id = up.contactid)
                   AND r.deactivated = 'true'
                   AND r.reactivation IS DISTINCT FROM 'true'
            ) THEN 'true' ELSE 'false' END AS viewer_deactivated,
            up.contactid                AS contactid,
            v.userprofileid             AS userprofileid,
            CASE
                WHEN up.role = 1 THEN pv.serviceimageurl
                ELSE c.profileimage
            END                         AS profileimage,
            up.role                     AS role,
            pv.providertype             AS providertype,
            COALESCE(
                EXISTS (
                    SELECT 1 FROM etown_memberrelation mr
                    WHERE (mr.from_contactid = up.contactid AND mr.to_contactid = $2)
                       OR (mr.to_contactid = up.contactid AND mr.from_contactid = $2)
                       OR (mr.from_userprofile = v.userprofileid AND mr.to_userprofile = $1)
                       OR (mr.to_userprofile = v.userprofileid AND mr.from_userprofile = $1)
                )
                OR EXISTS (
                    SELECT 1 FROM etown_providerrelation pr
                    WHERE (pr.fromcontactid = up.contactid AND pr.tocontactid = $2)
                       OR (pr.tocontactid = up.contactid AND pr.fromcontactid = $2)
                       OR (pr.fromuserprofileid = v.userprofileid AND pr.touserprofileid = $1)
                       OR (pr.touserprofileid = v.userprofileid AND pr.fromuserprofileid = $1)
                ),
                false
            ) AS is_connected,
            (
                SELECT inv.invitationcode
                FROM etown_invitation inv
                WHERE (inv.invitercontact = up.contactid AND inv.invitecontact = $2)
                   OR (inv.invitecontact = up.contactid AND inv.invitercontact = $2)
                LIMIT 1
            ) AS invitation,
            v.created_on                AS viewed_on
        FROM etown_provider_views v
        LEFT JOIN etown_userprofile up ON up.userprofileid = v.userprofileid
        LEFT JOIN etown_contact c ON c.contactid = up.contactid
        LEFT JOIN etown_provider pv ON pv.userprofileid = v.userprofileid
        WHERE v.providerprofileid = $1
          AND v.userprofileid IS NOT NULL
          AND v.userprofileid <> $1
          AND NOT etown_is_deleted(v.userprofileid)
          AND NOT etown_is_deleted(up.contactid)
        ORDER BY v.created_on DESC NULLS LAST
        LIMIT $3 OFFSET $4
"""

    offset = (max(page, 1) - 1) * limit
    rows = await Database.fetch(query, userprofileid, contactid, limit, offset)
    total = await Database.fetchval("SELECT get_provider_view_count($1)", userprofileid)
    logger.info(
        f"fetch_profile_viewers | userprofileid={userprofileid} | page={page}"
        f" | limit={limit} | offset={offset} | rows={len(rows)} | total={total}"
    )
    return [dict(row) for row in rows], int(total or 0)


async def extract_onboard_details(
    image_content_type: Optional[str],
    image_base64: Optional[str],
    prompt: str = ONBOARD_VISION_PROMPT,
    agent_notes: Optional[str] = None,
) -> OnboardDetailsResponse:
    """Extract provider and service details from a business image, typed notes or both with Gemini, empty fields on failure.

    Args:
        image_content_type: MIME type of the image, e.g. 'image/jpeg', or None when no image is sent.
        image_base64: Base64-encoded image bytes, or None when no image is sent.
        prompt: Instruction returning the Provider_Details/Service_Details JSON; defaults to /onboard_details'.
        agent_notes: Free text the field agent typed about the business, or None.
    """
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    if agent_notes:
        content.append({"type": "text", "text": AI_ONBOARDING_NOTES_TEMPLATE.format(notes=agent_notes)})
    if image_base64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{image_content_type};base64,{image_base64}"},
        })

    ai_response_text = ""
    for attempt in range(2):
        try:
            response = await gemini_2_5_flash_vision.ainvoke([{"role": "user", "content": content}])
            raw_content = getattr(response, "content", None)
            if isinstance(raw_content, list):
                ai_response_text = "".join(
                    str(part.get("text") or "") if isinstance(part, dict) else str(part)
                    for part in raw_content
                    if not isinstance(part, dict) or part.get("type", "text") == "text"
                )
            else:
                ai_response_text = str(raw_content or response)
            if len(ai_response_text.strip()) > 10:
                break
        except Exception as e:
            logger.warning("extract_onboard_details attempt %s failed: %s", attempt + 1, e, exc_info=True)
            if attempt == 1:
                ai_response_text = ""

    parsed: Dict[str, Any] = {}
    if ai_response_text:
        parsed = extract_json(ai_response_text) or {}

    if not parsed:
        logger.warning("extract_onboard_details: could not parse AI response; returning empty structure")

    raw_provider = parsed.get("Provider_Details", {}) or {}
    provider_details = OnboardProviderDetails(
        Business_Name=raw_provider.get("Business_Name"),
        Description=raw_provider.get("Description"),
        Phone_Number=raw_provider.get("Phone_Number"),
        Email=raw_provider.get("Email"),
        Website=raw_provider.get("Website"),
        Category=raw_provider.get("Category"),
        Name=raw_provider.get("Name"),
        Address=raw_provider.get("Address"),
        Service_Type=raw_provider.get("Service_Type"),
    )

    raw_services = parsed.get("Service_Details", []) or []
    service_details = [
        OnboardServiceDetail(
            Service_Name=svc.get("Service_Name"),
            Service_Description=svc.get("Service_Description"),
            Service_Type=svc.get("Service_Type"),
            Service_Quantity=svc.get("Service_Quantity"),
            Service_Price=svc.get("Service_Price"),
            Service_Expected_Price=svc.get("Service_Expected_Price"),
            Service_Image_Url=(
                _SERVICE_PHOTO_MARKER
                if str(svc.get("Service_Image_Url") or "").strip().upper() == _SERVICE_PHOTO_MARKER
                else None
            ),
        )
        for svc in raw_services
        if isinstance(svc, dict)
    ]

    return OnboardDetailsResponse(
        Onboard_data=[
            OnboardItem(
                Provider_Details=provider_details,
                Service_Details=service_details,
            )
        ]
    )


_BUSINESS_IMAGE_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_BUSINESS_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_BUSINESS_IMAGE_MAX_SIDE = 2048
_AI_ONBOARDING_MIN_SERVICES = 3
_AI_ONBOARDING_MAX_SERVICES = 4
_AI_ONBOARDING_MAX_MERGED_SERVICES = 5
_AI_ONBOARDING_ATTEMPTS = 2
_AI_ONBOARDING_MISSING_PROMPTS = {
    (True, False): "Could you please tell me the name of this business?",
    (False, True): "Could you please share its 10-digit mobile number?",
    (True, True): "Could you please share the business name and its 10-digit mobile number?",
}
_AI_ONBOARDING_PREPARED = "Thank you! I've prepared the details for {name}{offerings}."
_AI_ONBOARDING_PREPARED_UNNAMED = "Thank you! I've prepared a description{offerings} for this {kind} from what you shared."
_AI_ONBOARDING_REVIEW = "Please take a look and let me know if you'd like any changes."
_AI_ONBOARDING_NOTED = "Thank you! I've noted the mobile number {mobile}."
_AI_ONBOARDING_BAD_MOBILE = (
    "Sorry, {number} doesn't seem to be a valid mobile number. Could you please share a 10-digit "
    "mobile number?"
)
_AI_ONBOARDING_BAD_MOBILE_SKIPPED = "Sorry, {number} doesn't seem to be a valid mobile number, so I've left it out."
_AI_ONBOARDING_BAD_NAME = (
    "Sorry, \"{name}\" doesn't seem to be a business name. Could you please share the name as it "
    "appears on the shop board or visiting card?"
)
_AI_ONBOARDING_ALSO_NAME = "Could you please also tell me the name of this business?"
_AI_ONBOARDING_ALSO_MOBILE = "Could you please also share its 10-digit mobile number?"
_AI_ONBOARDING_NUMBER_RE = re.compile(r"\+?\d[\d\s-]{3,}\d")
_AI_ONBOARDING_TRANSCRIPT_HEADER = (
    "This chat so far, oldest first (Chaatie's lines are context only, never facts):"
)
_AI_ONBOARDING_OFFERING_WORDS = {
    frozenset({"product"}): ("product", "products"),
    frozenset({"service"}): ("service", "services"),
    frozenset({"product", "service"}): ("item", "products and services"),
}
_AI_ONBOARDING_NOT_BUSINESS = (
    "Sorry, I couldn't find any business details in that. Could you please share the business name, "
    "its 10-digit mobile number and what it offers, or upload a photo of the shop?"
)
_AI_ONBOARDING_NOT_BUSINESS_PHOTO = (
    "Sorry, I couldn't find any business details in this photo or message. Could you please upload a "
    "photo of the shopfront board, visiting card or menu, or share the business name and its 10-digit "
    "mobile number?"
)
_AI_ONBOARDING_PROMPT_PHONE_RE = re.compile(r"\+?\d[\d\s-]{8,}\d")
_SERVICE_PHOTO_MARKER = "PHOTO"
_PHOTO_WORDS_RE = re.compile(r"(?i)\b(?:image|photo|pic|picture|img)s?\b")
_AI_ONBOARDING_PHOTO_UNLINKED = "Also, which service is the photo for?"
_AI_ONBOARDING_BUBBLE_NO_PHOTO = "Sorry, I couldn't find the photo for {name}. Could you please attach it once more?"
_AI_ONBOARDING_PROVIDERID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_AI_ONBOARDING_PROVIDERID_LABEL_RE = re.compile(r"(?i)\bprovider[\s_]*id\b\s*[:=-]?")
_AI_ONBOARDING_PROVIDER_NOT_SHOWN = (
    "Sorry, I couldn't match that to the businesses shown for this mobile number. Could you please "
    "choose one of the businesses listed above?"
)
_AI_ONBOARDING_PROVIDER_GONE = (
    "Sorry, that business is no longer registered on this mobile number. Could you please check the "
    "number once more?"
)
_AI_ONBOARDING_MERGED = (
    "Thank you! I've updated {name} with the details you shared. Please take a look and let me know "
    "if you'd like any changes."
)
_AI_ONBOARDING_MERGE_CONFLICT = (
    "Thank you! A few details are a little different from what's already on Etown: {conflicts}. "
    "Please reply yes to use the details you shared, no to keep the current ones, or let me know the "
    "correct {fields}. If this is a different business, just say 'add as a new business'."
)
_AI_ONBOARDING_ADDRESS_MATCH_KM = 2.0
_AI_ONBOARDING_PLACE_MAX_DRIFT_KM = 25.0
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_GEOCODE_TOO_COARSE_TYPES = {"country", "administrative_area_level_1"}
_MERGE_CONFLICT_LABELS = {"Business_Name": "business name", "Address": "address"}
_AI_ONBOARDING_NEW_PROVIDER_RE = re.compile(
    r"(?i)\bas\s+(?:a\s+)?(?:new|separate|fresh|another)\s+(?:provider|listing|business|profile|shop|store|account|one)\b"
    r"|\b(?:new|separate|fresh|another)\s+(?:provider|listing|business|profile|shop|store|account|vendor|seller)\b"
    r"|\b(?:don'?t|do\s+not|no\s+need\s+to|without)\s+merg(?:e|ing)\b"
)
_AI_ONBOARDING_OWN_PROVIDER = (
    "This mobile number is already linked to your own business on Etown, so I'm unable to add it "
    "from here. Could you please check the number or share a different one?"
)
_AI_ONBOARDING_NEW_PROVIDER = "Sure! I'll add {name} as a new business with the details I've prepared."
_AI_ONBOARDING_USE_GENERATED_WORDS = {"yes", "y", "ok", "okay", "confirm", "confirmed", "new"}
_AI_ONBOARDING_USE_EXISTING_WORDS = {"no", "n", "existing", "old", "current"}
_BUSINESS_NAME_FILLER_WORDS = {
    "the", "and", "shop", "shops", "store", "stores", "centre", "center", "enterprise",
    "enterprises", "service", "services", "traders", "trading", "agency", "agencies",
    "co", "company", "pvt", "private", "ltd", "limited",
}
_BUSINESS_NAME_MATCH_RATIO = 0.8
_AI_ONBOARDING_BUBBLE_FAILED = (
    "Sorry, I wasn't able to update the details just now. Could you please try again in a moment?"
)
_AI_ONBOARDING_BUBBLE_UNCHANGED = "I haven't made any changes yet."
_AI_ONBOARDING_BUBBLE_APPLIED = "Thank you! I've updated the details as you asked."
_AI_ONBOARDING_BUBBLE_NO_MESSAGE = "(no message: apply what the attached photo shows)"
_AI_ONBOARDING_BUBBLE_IMAGE_NOTE = (
    "A new photo of the business is attached (shopfront board, visiting card, flyer, menu, price list or similar)."
)
_AI_ONBOARDING_BUBBLE_NO_IMAGE_NOTE = "No photo is attached; work from the message only."
_AI_ONBOARDING_BUBBLE_HISTORY_TURNS = 10
_BUBBLE_HIDDEN_PROVIDER_FIELDS = {"latitude", "longitude", "city", "statecode"}
_AI_ONBOARDING_BUBBLE_NO_LOCATION = (
    "Sorry, I couldn't locate this address on the map. Could you please share the full address, "
    "including the area and city?"
)
_AI_ONBOARDING_BUBBLE_MOBILE_OCCUPIED = (
    "Sorry, {mobile} is already used by another provider, so I've kept the alternate number as it was. "
    "Could you please check and share a new number?"
)
_AI_ONBOARDING_BUBBLE_MOBILE_UNCHECKED = (
    "Sorry, I couldn't check whether {mobile} is already used by another provider, so I've kept the "
    "alternate number as it was. Could you please try again in a moment?"
)
_AI_ONBOARDING_BUBBLE_CONFIRM_NAME = (
    "Just to confirm: '{name}' seems a little different from what this business offers, so I've kept "
    "the current name for now. Please reply yes to use it, or let me know the correct business name."
)


def _tidy_ai_onboarding_listing(
    provider: OnboardProviderDetails,
    services: List[OnboardServiceDetail],
) -> Optional[str]:
    """Strip "onboard" from the business name and normalise service quantities in place, returning the business_category.

    Args:
        provider: Provider details whose Business_Name is cleaned.
        services: Services whose Service_Quantity becomes a number for products and '--' for services.
    """
    provider.Business_Name = " ".join(
        re.sub(r"(?i)\bonboard(?:ing|ed)?\b", " ", provider.Business_Name or "").split()
    ).strip(" -:,.") or None

    for svc in services:
        service_type = (svc.Service_Type or "").strip().lower()
        number = re.search(r"\d+(?:\.\d+)?", svc.Service_Quantity or "")
        if service_type == "service":
            svc.Service_Quantity = "--"
        elif service_type == "product":
            svc.Service_Quantity = number.group() if number else "1"
        else:
            svc.Service_Quantity = number.group() if number else "--"

    service_types = {(svc.Service_Type or "").strip().lower() for svc in services} & {"product", "service"}
    return ",".join(sorted(service_types)) or None


class _OnboardingLocation(NamedTuple):
    """Coordinates, city and two-letter statecode geocoded from an onboarding address; None where unknown."""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city: Optional[str] = None
    statecode: Optional[str] = None


def _onboarding_city_state(components: Dict[str, str]) -> Tuple[Optional[str], Optional[str]]:
    """Pick the city (locality, else admin levels 3 then 2) and two-letter statecode from Google address components keyed by type.

    Args:
        components: Google address components as {type: long_name}, without 'political'.
    """
    city = (
        components.get("locality")
        or components.get("administrative_area_level_3")
        or components.get("administrative_area_level_2")
    )
    state_name = components.get("administrative_area_level_1")
    return city, convert_state_name_to_code(state_name) if state_name else None


async def _find_onboarding_place(query: str) -> Optional[Tuple[float, float]]:
    """Find an address with Google Places Text Search in India, returning the first match's (latitude, longitude), or None.

    Args:
        query: Whitespace-normalised business address.
    """
    try:
        async with get_http_client() as client:
            response = await client.get(
                _PLACES_TEXT_SEARCH_URL,
                params={"query": query, "region": "in", "language": "en", "key": settings.google_maps_api_key},
            )
        data = response.json() if response.status_code == 200 else {}
    except Exception as e:
        logger.warning("[ai_onboarding] place search failed for %r: %s", query, e)
        return None
    for place in data.get("results") or []:
        point = (place.get("geometry") or {}).get("location") or {}
        if "lat" in point and "lng" in point and "india" in (place.get("formatted_address") or "").lower():
            return float(point["lat"]), float(point["lng"])
    return None


async def _geocode_onboarding_address(address: Optional[str]) -> _OnboardingLocation:
    """Geocode an onboarding address within India, returning its coordinates, city and statecode, or an empty location when it is empty or not found.

    A precise Geocoding match is used as is. A partial match, or one no finer than a state, falls back to
    Places Text Search, which reads shop-style addresses with landmarks better, and that point is kept
    only when it lies within _AI_ONBOARDING_PLACE_MAX_DRIFT_KM of the Geocoding match.

    Args:
        address: Business address as read or edited, or None.
    """
    query = " ".join((address or "").split())
    if not query:
        return _OnboardingLocation()
    if not settings.google_maps_api_key:
        logger.warning("[ai_onboarding] Google Maps API key is not configured; address %r not geocoded", query)
        return _OnboardingLocation()

    try:
        async with get_http_client() as client:
            response = await client.get(
                _GEOCODE_URL,
                params={
                    "address": query, "components": "country:IN", "region": "in",
                    "language": "en", "key": settings.google_maps_api_key,
                },
            )
        data = response.json() if response.status_code == 200 else {}
    except Exception as e:
        logger.warning("[ai_onboarding] could not geocode address %r: %s", query, e)
        data = {}

    location = _OnboardingLocation()
    precise = False
    result = (data.get("results") or [None])[0] if data.get("status") == "OK" else None
    if result:
        components = {
            kind: component.get("long_name")
            for component in result.get("address_components") or []
            for kind in component.get("types") or []
            if kind != "political"
        }
        city, statecode = _onboarding_city_state(components)
        point = (result.get("geometry") or {}).get("location") or {}
        if set(result.get("types") or []) & _GEOCODE_TOO_COARSE_TYPES or "lat" not in point or "lng" not in point:
            location = _OnboardingLocation(city=city, statecode=statecode)
        else:
            location = _OnboardingLocation(float(point["lat"]), float(point["lng"]), city, statecode)
            precise = not result.get("partial_match")
    if precise:
        return location

    place = await _find_onboarding_place(query)
    if place and (
        location.latitude is None
        or geodesic((location.latitude, location.longitude), place).km <= _AI_ONBOARDING_PLACE_MAX_DRIFT_KM
    ):
        try:
            city, statecode = _onboarding_city_state((await get_location_details_logic(*place)).parsed_components)
        except Exception as e:
            logger.warning("[ai_onboarding] could not reverse-geocode place for %r: %s", query, e)
            city = statecode = None
        return _OnboardingLocation(place[0], place[1], city or location.city, statecode or location.statecode)
    if location.latitude is None:
        logger.warning("[ai_onboarding] address %r not found on the map", query)
    return location


def _clean_business_image(business_image: Optional[str]) -> str:
    """Return the stripped business_image text, or an empty string when it is missing or the literal 'null'.

    Args:
        business_image: Base64 or data-URL text as sent, or None.
    """
    raw_image = (business_image or "").strip()
    return "" if raw_image.lower() == "null" else raw_image


def _shrink_business_image(image_bytes: bytes, content_type: str) -> Tuple[bytes, str]:
    """Downscale an image whose longest side exceeds _BUSINESS_IMAGE_MAX_SIDE, returning JPEG (WebP when it has alpha) bytes and MIME type, or the input unchanged."""
    from io import BytesIO

    from PIL import Image, ImageOps

    image = Image.open(BytesIO(image_bytes))
    if max(image.size) <= _BUSINESS_IMAGE_MAX_SIDE:
        return image_bytes, content_type
    image = ImageOps.exif_transpose(image)
    image.thumbnail((_BUSINESS_IMAGE_MAX_SIDE, _BUSINESS_IMAGE_MAX_SIDE), Image.LANCZOS)
    buffer = BytesIO()
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        image.save(buffer, "WEBP", quality=85, method=4)
        return buffer.getvalue(), "image/webp"
    image.convert("RGB").save(buffer, "JPEG", quality=85, optimize=True)
    return buffer.getvalue(), "image/jpeg"


async def _store_business_image(
    raw_image: str,
    log_tag: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Validate a base64 business image, downscale it past 2048 px, and upload it to Azure Blob, returning its MIME type, clean base64 and blob path.

    Args:
        raw_image: Base64 or data-URL text of a JPEG, PNG or WebP image, or an empty string for none.
        log_tag: Log prefix naming the calling endpoint, e.g. 'ai_onboarding'.

    Raises:
        HTTPException: 400 when the image is not base64 of a JPEG, PNG or WebP, or is still over 10 MB after downscaling.
    """
    if not raw_image:
        return None, None, None
    try:
        image_bytes = _to_bytes(raw_image)
    except ValueError:
        raise HTTPException(status_code=400, detail="business_image is not valid base64")
    content_type = detect_mime_type(image_bytes)
    extension = _BUSINESS_IMAGE_EXTENSIONS.get(content_type)
    if not extension:
        raise HTTPException(
            status_code=400,
            detail="business_image must be a base64-encoded JPEG, PNG or WebP image",
        )
    original_size = len(image_bytes)
    try:
        image_bytes, content_type = await asyncio.to_thread(_shrink_business_image, image_bytes, content_type)
        extension = _BUSINESS_IMAGE_EXTENSIONS[content_type]
    except Exception as e:
        logger.warning("[%s] Image downscale failed; keeping the original: %s", log_tag, e, exc_info=True)
    if len(image_bytes) != original_size:
        logger.info("[%s] Downscaled business_image from %s to %s bytes", log_tag, original_size, len(image_bytes))
    if len(image_bytes) > _BUSINESS_IMAGE_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"business_image is too large. Maximum size is {_BUSINESS_IMAGE_MAX_BYTES // (1024 * 1024)} MB",
        )
    blob_path: Optional[str] = None
    try:
        blob_name = f"unverified_provider/{uuid.uuid4()}/{_current_timestamp_prefix()}_BusinessImage.{extension}"
        blob_path = await _upload_blob_bytes(
            _normalize_container_name(settings.AZURE_BLOB_CONTAINER), blob_name, image_bytes, content_type
        ) or None
    except Exception as e:
        logger.warning("[%s] Blob upload failed; analysing the image anyway: %s", log_tag, e, exc_info=True)
    return content_type, base64.b64encode(image_bytes).decode("ascii"), blob_path


async def _generate_ai_onboarding_listing(
    content_type: Optional[str],
    image_base64: Optional[str],
    notes: Optional[str],
    blob_path: Optional[str],
) -> Tuple[OnboardItem, Optional[str], List[str]]:
    """Read business facts from an image and/or notes and generate 3-4 services, returning the listing, business_category and mobiles.

    Args:
        content_type: MIME type of the image, or None.
        image_base64: Clean base64 of the image, or None.
        notes: Free text the field agent typed about the business, or None.
        blob_path: Blob path of the image, set as Service_Image_Url on the services the AI links the photo to.
    """
    for attempt in range(_AI_ONBOARDING_ATTEMPTS):
        extracted = await extract_onboard_details(
            content_type,
            image_base64,
            prompt=AI_ONBOARDING_PROMPT,
            agent_notes=notes,
        )
        analysis = extracted.Onboard_data[0]
        analysis.Service_Details = [
            svc for svc in analysis.Service_Details if (svc.Service_Name or "").strip()
        ][:_AI_ONBOARDING_MAX_SERVICES]
        if len(analysis.Service_Details) >= _AI_ONBOARDING_MIN_SERVICES:
            break
        logger.warning(
            "[ai_onboarding] attempt %d returned %d services, fewer than %d",
            attempt + 1, len(analysis.Service_Details), _AI_ONBOARDING_MIN_SERVICES,
        )

    for svc in analysis.Service_Details:
        svc.Service_Image_Url = blob_path if image_base64 and svc.Service_Image_Url == _SERVICE_PHOTO_MARKER else None
    business_category = _tidy_ai_onboarding_listing(analysis.Provider_Details, analysis.Service_Details)
    mobiles = list(dict.fromkeys(
        national
        for part in re.split(r"[,/;|&]|\bor\b", analysis.Provider_Details.Phone_Number or "")
        if (national := normalize_indian_mobile(part))
    ))
    return analysis, business_category, mobiles


async def analyse_ai_onboarding(
    business_image: Optional[str],
    agent_prompt: Optional[str],
    conversation_id: Optional[str] = None,
    contact_id: Optional[str] = None,
) -> AIOnboardingResponse:
    """Merge a picked or confirmed provider, answer a chat message, return the saved listing for a new-provider reply, else generate one beside existing providers.

    Args:
        business_image: Base64 or data-URL text of a JPEG, PNG or WebP image, or None.
        agent_prompt: Free text the field agent typed about the business, or None.
        conversation_id: Conversation the call belongs to; earlier turns are read as a transcript, and a providerid or pending confirmation is looked up there.
        contact_id: Caller's contactid; a mobile whose providers belong to it is refused as the caller's own provider.

    Raises:
        HTTPException: 400 when neither is given, or the image is not base64 of a JPEG, PNG or WebP of at most 10 MB.
    """
    raw_image = _clean_business_image(business_image)
    notes = (agent_prompt or "").strip() or None
    if not raw_image and not notes:
        raise HTTPException(status_code=400, detail="Send a business_image, a prompt, or both")

    picked = _AI_ONBOARDING_PROVIDERID_RE.search(notes or "")
    if picked:
        providerid = picked.group().lower()
        earlier = None
        if (conversation_id or "").strip():
            earlier = await Database.fetchrow(
                """
                SELECT u.prompt_text, u.business_image_url,
                       COALESCE(
                           a.response -> 'Provider_Details' ->> 'mobilenumber',
                           a.response ->> 'mobilenumber'
                       ) AS mobilenumber,
                       jsonb_build_object(
                           'Provider_Details', a.response -> 'Provider_Details',
                           'Service_Details', a.response -> 'Service_Details'
                       )::text AS saved_listing
                FROM etown_ai_onboarding a
                JOIN etown_ai_onboarding u ON u.turnid = a.turnid AND u.role = 'user'
                WHERE a.conversationid = $1
                  AND a.role = 'assistant'
                  AND EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(COALESCE(a.response -> 'existing_providers', '[]'::jsonb)) AS shown
                      WHERE lower(shown ->> 'providerid') = $2
                  )
                ORDER BY a.createdon DESC
                LIMIT 1
                """,
                conversation_id.strip(),
                providerid,
            )
        if not earlier:
            logger.info("[ai_onboarding] providerid %s not shown in conversation %s", providerid, conversation_id)
            return AIOnboardingResponse(confirmation_required=True, response=_AI_ONBOARDING_PROVIDER_NOT_SHOWN)
        extra_notes = _AI_ONBOARDING_PROVIDERID_LABEL_RE.sub(" ", _AI_ONBOARDING_PROVIDERID_RE.sub(" ", notes))
        return await _merge_ai_onboarding_existing(
            providerid, earlier, " ".join(extra_notes.split()) or None, raw_image, contact_id=contact_id
        )

    wants_new_provider = bool(_AI_ONBOARDING_NEW_PROVIDER_RE.search(notes or ""))
    if notes and (conversation_id or "").strip():
        pending = await Database.fetchrow(
            """
            SELECT NULL::text AS prompt_text,
                   a.response ->> 'business_image_url' AS business_image_url,
                   a.response ->> 'status' AS status,
                   a.response -> 'Provider_Details' ->> 'mobilenumber' AS mobilenumber,
                   lower(a.response ->> 'providerid') AS providerid,
                   COALESCE((a.response ->> 'confirmation_required')::boolean, FALSE) AS confirmation_required,
                   jsonb_array_length(COALESCE(a.response -> 'existing_providers', '[]'::jsonb)) > 0 AS showed_existing,
                   jsonb_build_object(
                       'Provider_Details', a.response -> 'Provider_Details',
                       'Service_Details', a.response -> 'Service_Details'
                   )::text AS saved_listing
            FROM etown_ai_onboarding a
            WHERE a.conversationid = $1
              AND a.role = 'assistant'
            ORDER BY a.createdon DESC
            LIMIT 1
            """,
            conversation_id.strip(),
        )
        awaiting_merge = bool(pending) and (
            pending["showed_existing"] or (pending["confirmation_required"] and pending["providerid"])
        )
        if awaiting_merge and wants_new_provider:
            saved = json.loads(pending["saved_listing"] or "{}")
            listing = AIOnboardingProviderDetails.model_validate(saved.get("Provider_Details") or {})
            services = [OnboardServiceDetail.model_validate(svc) for svc in saved.get("Service_Details") or []]
            if listing.Address and (
                listing.latitude is None or listing.longitude is None or not listing.city or not listing.statecode
            ):
                location = await _geocode_onboarding_address(listing.Address)
                listing.latitude, listing.longitude, listing.city, listing.statecode = location
            logger.info(
                "[ai_onboarding] conversation %s onboards as a new provider | business=%r services=%d",
                conversation_id, listing.Business_Name, len(services),
            )
            return AIOnboardingResponse(
                confirmation_required=not ((listing.Business_Name or "").strip() and listing.mobilenumber),
                status=pending["status"],
                business_image_url=pending["business_image_url"],
                Provider_Details=listing,
                Service_Details=services,
                response=" ".join([
                    _AI_ONBOARDING_NEW_PROVIDER.format(name=(listing.Business_Name or "").strip() or "this"),
                    _AI_ONBOARDING_MISSING_PROMPTS.get(
                        (not (listing.Business_Name or "").strip(), not listing.mobilenumber),
                        _AI_ONBOARDING_REVIEW,
                    ),
                ]),
            )
        if pending and pending["confirmation_required"] and pending["providerid"]:
            return await _merge_ai_onboarding_existing(
                pending["providerid"], pending, None, "", confirmation_answer=notes, contact_id=contact_id
            )

    earlier_prompts: List[str] = []
    transcript: List[str] = []
    earlier_image_url: Optional[str] = None
    last_reply: Optional[str] = None
    asked_name = False
    if (conversation_id or "").strip():
        earlier_turns = await Database.fetch(
            """
            SELECT u.prompt_text, u.business_image_url, a.response_text,
                   COALESCE(a.response -> 'Provider_Details' ->> 'Business_Name', '') = '' AS name_missing
            FROM etown_ai_onboarding u
            LEFT JOIN etown_ai_onboarding a ON a.turnid = u.turnid AND a.role = 'assistant'
            WHERE u.conversationid = $1
              AND u.role = 'user'
            ORDER BY u.createdon
            """,
            conversation_id.strip(),
        )
        for turn in earlier_turns:
            prompt_text = (turn["prompt_text"] or "").strip()
            if prompt_text:
                earlier_prompts.append(prompt_text)
            transcript.append(
                f"Agent: {prompt_text or ''}{' [sent an earlier photo, not attached]' if turn['business_image_url'] else ''}".strip()
            )
            if (turn["response_text"] or "").strip():
                transcript.append(f"Chaatie: {turn['response_text'].strip()}")
            earlier_image_url = turn["business_image_url"] or earlier_image_url
        if earlier_turns:
            last_reply = (earlier_turns[-1]["response_text"] or "").strip() or None
            asked_name = bool(last_reply) and bool(earlier_turns[-1]["name_missing"])
        wants_new_provider = wants_new_provider or any(
            _AI_ONBOARDING_NEW_PROVIDER_RE.search(text) for text in earlier_prompts
        )

    content_type, image_base64, blob_path = await _store_business_image(raw_image, "ai_onboarding")
    blob_path = blob_path or (None if raw_image else earlier_image_url)

    prompt_mobile = next(
        (
            national
            for text in [notes, *reversed(earlier_prompts)]
            for candidate in _AI_ONBOARDING_PROMPT_PHONE_RE.findall(text or "")
            if (national := normalize_indian_mobile(candidate))
        ),
        None,
    )
    (analysis, business_category, mobiles), chat = await asyncio.gather(
        _generate_ai_onboarding_listing(
            content_type,
            image_base64,
            "\n".join([
                _AI_ONBOARDING_TRANSCRIPT_HEADER, *transcript,
                f"Agent (latest message): {notes or ''}{' [sent the attached photo]' if raw_image else ''}".strip(),
            ]) if transcript else notes,
            blob_path,
        ),
        _ask_gemini_json(
            AI_ONBOARDING_CHAT_PROMPT.format(
                last_reply=json.dumps(last_reply or "(none: this is the agent's first message)", ensure_ascii=False),
                message=json.dumps(notes, ensure_ascii=False),
            ),
            "chat check",
        ) if notes and not raw_image and not _AI_ONBOARDING_PROMPT_PHONE_RE.search(notes) else asyncio.sleep(0, result={}),
    )
    chat_reply = " ".join(str(chat.get("reply") or "").split()) if str(chat.get("is_chat")).lower() == "true" else ""
    chat_asks = bool(chat_reply) and str(chat.get("confirmation_required")).lower() == "true"
    if chat_reply and not earlier_prompts and not earlier_image_url:
        logger.info("[ai_onboarding] conversation %s chat message answered without a listing", conversation_id)
        return AIOnboardingResponse(confirmation_required=chat_asks, response=chat_reply)
    if earlier_prompts and prompt_mobile:
        mobiles = list(dict.fromkeys([prompt_mobile, *mobiles]))

    business_name = (analysis.Provider_Details.Business_Name or "").strip()
    bad_mobile = next(
        (
            candidate.strip()
            for candidate in _AI_ONBOARDING_NUMBER_RE.findall(notes or "")
            if len(re.sub(r"\D", "", candidate)) >= 8 and not normalize_indian_mobile(candidate)
        ),
        None,
    )
    sent_name = " ".join((notes or "").split())
    rejection: List[Optional[str]] = []
    if bad_mobile and not mobiles:
        rejection = [
            _AI_ONBOARDING_BAD_MOBILE.format(number=bad_mobile),
            None if business_name else _AI_ONBOARDING_ALSO_NAME,
        ]
    elif (
        asked_name and not business_name and sent_name and not raw_image and len(sent_name.split()) <= 5
        and not _AI_ONBOARDING_NUMBER_RE.search(sent_name)
    ):
        rejection = [
            _AI_ONBOARDING_BAD_NAME.format(name=sent_name if len(sent_name) <= 40 else f"{sent_name[:40]}…"),
            None if mobiles else _AI_ONBOARDING_ALSO_MOBILE,
        ]
    if not mobiles and not analysis.Service_Details and not any(analysis.Provider_Details.model_dump().values()):
        logger.info(
            "[ai_onboarding] conversation %s gave no business details | image=%s prompt=%s",
            conversation_id, bool(raw_image), bool(notes),
        )
        return AIOnboardingResponse(
            confirmation_required=chat_asks or not chat_reply,
            response=chat_reply or " ".join(filter(None, rejection)) or (
                _AI_ONBOARDING_NOT_BUSINESS_PHOTO if raw_image else _AI_ONBOARDING_NOT_BUSINESS
            ),
        )
    location = await _geocode_onboarding_address(analysis.Provider_Details.Address)

    lookup_mobile = prompt_mobile or (mobiles[0] if mobiles else None)
    if lookup_mobile and (existing := await _existing_ai_onboarding_response(
        lookup_mobile, blob_path, (analysis, business_category, mobiles), location,
        contact_id, wants_new_provider,
    )):
        return existing

    service_count = len(analysis.Service_Details)
    singular, plural = _AI_ONBOARDING_OFFERING_WORDS.get(
        frozenset((svc.Service_Type or "").strip().lower() for svc in analysis.Service_Details)
        & {"product", "service"},
        ("service", "services"),
    )
    offerings = f" and {service_count} {singular if service_count == 1 else plural}" if service_count else ""
    logger.info(
        "[ai_onboarding] image=%s prompt=%s blob=%s business=%r mobiles=%d services=%d",
        bool(raw_image), bool(notes), blob_path, analysis.Provider_Details.Business_Name,
        len(mobiles), len(analysis.Service_Details),
    )
    photo_unlinked = bool(image_base64) and bool(_PHOTO_WORDS_RE.search(notes or "")) and not any(
        svc.Service_Image_Url for svc in analysis.Service_Details
    )
    return AIOnboardingResponse(
        confirmation_required=chat_asks if chat_reply else bool(
            rejection or not business_name or not mobiles or photo_unlinked
        ),
        status=PROVIDER_STATUS_AVAILABLE if mobiles else None,
        business_image_url=blob_path,
        Provider_Details=AIOnboardingProviderDetails(
            **analysis.Provider_Details.model_dump(),
            mobilenumber=mobiles[0] if mobiles else None,
            alternatemobile=mobiles[1] if len(mobiles) > 1 else None,
            **location._asdict(),
            business_category=business_category,
        ),
        Service_Details=analysis.Service_Details,
        response=chat_reply or " ".join(filter(None, rejection or [
            _AI_ONBOARDING_PREPARED.format(name=business_name, offerings=offerings) if business_name
            else _AI_ONBOARDING_PREPARED_UNNAMED.format(
                offerings=offerings,
                kind=" ".join(f"{analysis.Provider_Details.Category or ''} business".split()),
            ) if offerings or analysis.Provider_Details.Description
            else _AI_ONBOARDING_NOTED.format(mobile=mobiles[0]) if mobiles else None,
            _AI_ONBOARDING_BAD_MOBILE_SKIPPED.format(number=bad_mobile) if bad_mobile else None,
            _AI_ONBOARDING_MISSING_PROMPTS.get((not business_name, not mobiles), _AI_ONBOARDING_REVIEW),
            _AI_ONBOARDING_PHOTO_UNLINKED if photo_unlinked else None,
        ])),
    )


async def _existing_ai_onboarding_response(
    mobile: str,
    blob_path: Optional[str],
    generated: Tuple[OnboardItem, Optional[str], List[str]],
    location: _OnboardingLocation,
    contact_id: Optional[str] = None,
    new_provider: bool = False,
) -> Optional[AIOnboardingResponse]:
    """Return a mobile's existing providers beside the generated listing, or a refusal when they are the caller's own; None when neither applies.

    Args:
        mobile: 10-digit national mobile number to look up.
        blob_path: Blob path of the image stored for this call, or None.
        generated: Listing, business_category and mobiles generated for this call, shown alongside the existing providers.
        location: Coordinates, city and statecode geocoded from the generated listing's address, each None when unknown.
        contact_id: Caller's contactid, compared with the providers' contactid, or None.
        new_provider: True when the agent asked to onboard as a new provider, so only the own-provider check runs.
    """
    try:
        providers = await ProviderService.fetch_providers_by_mobile(mobile)
    except Exception as e:
        logger.warning("[ai_onboarding] existing-provider lookup failed; generating a listing: %s", e, exc_info=True)
        return None
    if not providers:
        return None

    status = summarise_provider_status(providers)
    if _is_own_provider(providers, contact_id):
        logger.info("[ai_onboarding] mobile=*****%s belongs to the caller's own provider; refused", mobile[-4:])
        return AIOnboardingResponse(
            confirmation_required=True,
            status=status,
            business_image_url=blob_path,
            Provider_Details=AIOnboardingProviderDetails(mobilenumber=mobile),
            response=_AI_ONBOARDING_OWN_PROVIDER,
        )
    if new_provider:
        return None

    status_message = PROVIDER_STATUS_MESSAGES.get(status)
    analysis, business_category, mobiles = generated
    logger.info(
        "[ai_onboarding] mobile=*****%s already has %d provider(s) | status=%s | services=%d",
        mobile[-4:], len(providers), status, len(analysis.Service_Details),
    )
    return AIOnboardingResponse(
        confirmation_required=True,
        status=status,
        existing_providers=providers,
        business_image_url=blob_path,
        Provider_Details=AIOnboardingProviderDetails(
            **analysis.Provider_Details.model_dump(),
            mobilenumber=mobile,
            alternatemobile=next((m for m in mobiles if m != mobile), None),
            **location._asdict(),
            business_category=business_category,
        ),
        Service_Details=analysis.Service_Details,
        response=(
            (
                f"This mobile number is already registered on Etown for "
                f"{(providers[0].get('businessname') or '').strip() or 'a business'}."
                if len(providers) == 1
                else f"This mobile number is already registered on Etown for {len(providers)} businesses."
            )
            + (f" {status_message}." if status_message else "")
            + (
                " If it's the same business, please choose it and I'll update it with the details you "
                "shared. If not, just say 'add as a new business'."
            )
        ),
    )


def _is_own_provider(providers: List[Dict[str, Any]], contact_id: Optional[str]) -> bool:
    """Say whether any of a mobile's providers belongs to the caller's contact, comparing ids without braces or case.

    Args:
        providers: Providers on the mobile, as fetch_providers_by_mobile() returns them.
        contact_id: Caller's contactid, or None.
    """
    caller = str(contact_id or "").strip().strip("{}").lower()
    return bool(caller) and any(
        str(provider.get("contactid") or "").strip().strip("{}").lower() == caller for provider in providers
    )


def _names_similar(first: Optional[str], second: Optional[str]) -> bool:
    """Say whether two business or service names match, ignoring filler words; an empty name matches anything.

    Args:
        first: One name, or None.
        second: The other name, or None.
    """
    if not (first or "").strip() or not (second or "").strip():
        return True
    words = [
        {
            word for word in re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
            if word not in _BUSINESS_NAME_FILLER_WORDS
        } or set(name.lower().split())
        for name in (first, second)
    ]
    return (
        words[0] <= words[1]
        or words[1] <= words[0]
        or difflib.SequenceMatcher(
            None, " ".join(sorted(words[0])), " ".join(sorted(words[1]))
        ).ratio() >= _BUSINESS_NAME_MATCH_RATIO
    )


async def _ask_gemini_json(prompt: str, log_tag: str) -> Dict[str, Any]:
    """Send a text prompt to Gemini Flash, retrying once, and return the reply's JSON object, or an empty dict when none parses.

    Args:
        prompt: Full prompt text asking for a raw JSON object.
        log_tag: Short name of the task for log lines, e.g. 'description rewrite'.
    """
    for attempt in range(_AI_ONBOARDING_ATTEMPTS):
        try:
            reply = await gemini_2_5_flash_vision.ainvoke([{"role": "user", "content": prompt}])
            raw_content = getattr(reply, "content", None)
            text = (
                "".join(
                    str(part.get("text") or "") if isinstance(part, dict) else str(part)
                    for part in raw_content
                    if not isinstance(part, dict) or part.get("type", "text") == "text"
                )
                if isinstance(raw_content, list)
                else str(raw_content or "")
            )
            parsed = extract_json(text)
            if isinstance(parsed, dict) and parsed:
                return parsed
            logger.warning("[ai_onboarding] %s attempt %d returned no JSON object", log_tag, attempt + 1)
        except Exception as e:
            logger.warning("[ai_onboarding] %s attempt %d failed: %s", log_tag, attempt + 1, e, exc_info=True)
    return {}


async def _merge_ai_onboarding_existing(
    providerid: str,
    earlier: Any,
    notes: Optional[str],
    raw_image: str,
    confirmation_answer: Optional[str] = None,
    contact_id: Optional[str] = None,
) -> AIOnboardingResponse:
    """Merge a listing into a picked provider once a differing name or address is confirmed, keeping existing values and adding dissimilar services up to five.

    Args:
        providerid: Lower-cased providerid of the existing provider the field agent picked.
        earlier: Row with the earlier turn's prompt_text, business_image_url, mobilenumber and saved_listing JSON.
        notes: Text of this prompt with the providerid removed, or None.
        raw_image: This call's business_image text, or an empty string for none.
        confirmation_answer: The agent's reply to a pending name or address confirmation, or None when none was asked.
        contact_id: Caller's contactid; a mobile whose providers belong to it is refused as the caller's own provider.

    Raises:
        HTTPException: 400 when this call's image is not base64 of a JPEG, PNG or WebP of at most 10 MB.
    """
    providers = await ProviderService.fetch_providers_by_mobile(earlier["mobilenumber"] or "")
    if _is_own_provider(providers, contact_id):
        logger.info("[ai_onboarding] merge into %s refused: the mobile's provider is the caller's own", providerid)
        return AIOnboardingResponse(
            confirmation_required=True,
            status=summarise_provider_status(providers),
            Provider_Details=AIOnboardingProviderDetails(mobilenumber=earlier["mobilenumber"]),
            response=_AI_ONBOARDING_OWN_PROVIDER,
        )
    existing = next((p for p in providers if (p["providerid"] or "").lower() == providerid), None)
    if existing is None:
        logger.info("[ai_onboarding] picked provider %s is no longer live on its mobile", providerid)
        return AIOnboardingResponse(
            confirmation_required=True,
            Provider_Details=AIOnboardingProviderDetails(mobilenumber=earlier["mobilenumber"]),
            response=_AI_ONBOARDING_PROVIDER_GONE,
        )

    content_type, image_base64, blob_path = await _store_business_image(raw_image, "ai_onboarding")
    blob_path = blob_path or earlier["business_image_url"]
    saved = json.loads(earlier["saved_listing"] or "{}")
    saved_details = saved.get("Provider_Details") or {}
    saved_services = saved.get("Service_Details") or []
    generated: Optional[OnboardItem] = None
    generated_mobiles: List[str] = []
    reuse_saved = not notes and not image_base64 and bool(saved_services or saved_details.get("Business_Name"))
    if reuse_saved:
        generated = OnboardItem.model_validate({"Provider_Details": saved_details, "Service_Details": saved_services})
        generated_mobiles = list(dict.fromkeys(filter(None, [
            normalize_indian_mobile(saved_details.get("mobilenumber") or ""),
            normalize_indian_mobile(saved_details.get("alternatemobile") or ""),
        ])))
    else:
        if not image_base64 and earlier["business_image_url"]:
            try:
                image_bytes = await _download_blob_bytes(earlier["business_image_url"])
                content_type = detect_mime_type(image_bytes)
                image_base64 = base64.b64encode(image_bytes).decode("ascii")
            except Exception as e:
                logger.warning(
                    "[ai_onboarding] could not reload earlier image %s: %s",
                    earlier["business_image_url"], e, exc_info=True,
                )
        earlier_notes = "\n".join(filter(None, [earlier["prompt_text"], notes])) or None
        if image_base64 or earlier_notes:
            generated, _, generated_mobiles = await _generate_ai_onboarding_listing(
                content_type, image_base64, earlier_notes, blob_path
            )
    generated_details = generated.Provider_Details if generated else OnboardProviderDetails()

    existing_name = (existing["businessname"] or "").strip() or None
    generated_name = (generated_details.Business_Name or "").strip() or None
    existing_address = (existing["address"] or "").strip() or None
    generated_address = (generated_details.Address or "").strip() or None
    try:
        existing_location: Optional[_OnboardingLocation] = _OnboardingLocation(
            float(existing["latitude"]), float(existing["longitude"]),
            (existing["city"] or "").strip() or None, (existing["statecode"] or "").strip() or None,
        )
    except (TypeError, ValueError):
        existing_location = None
    saved_location = (
        _OnboardingLocation(
            saved_details["latitude"], saved_details["longitude"],
            saved_details.get("city"), saved_details.get("statecode"),
        )
        if reuse_saved and saved_details.get("latitude") is not None and saved_details.get("longitude") is not None
        else None
    )
    generated_location = _OnboardingLocation()
    address_conflict = False
    if existing_address and generated_address and not _names_similar(existing_address, generated_address):
        if saved_location and saved_details.get("Address") == generated_details.Address:
            generated_location = saved_location
        else:
            generated_location = await _geocode_onboarding_address(generated_address)
        address_conflict = not (
            existing_location and generated_location.latitude is not None
            and geodesic(existing_location[:2], generated_location[:2]).km <= _AI_ONBOARDING_ADDRESS_MATCH_KM
        )
    conflicts = {
        field: values
        for field, values, clash in (
            ("Business_Name", (existing_name, generated_name), not _names_similar(existing_name, generated_name)),
            ("Address", (existing_address, generated_address), address_conflict),
        )
        if clash
    }

    reply_text = (confirmation_answer or "").strip()
    reply_mobiles = list(dict.fromkeys(
        national
        for candidate in _AI_ONBOARDING_PROMPT_PHONE_RE.findall(reply_text)
        if (national := normalize_indian_mobile(candidate))
    ))
    reply_facts: Dict[str, Any] = {}
    if reply_text and conflicts:
        reply_facts = await _ask_gemini_json(
            AI_ONBOARDING_MERGE_CONFIRM_PROMPT.format(
                conflicts=json.dumps(
                    {field: {"existing": old_value, "new": new_value} for field, (old_value, new_value) in conflicts.items()},
                    ensure_ascii=False,
                ),
                reply=reply_text,
            ),
            "merge confirmation",
        )

    chosen: Dict[str, Optional[str]] = {}
    for field, (old_value, new_value) in conflicts.items():
        picked_value = str(reply_facts.get(field) or "").strip()
        fits_old, fits_new = _names_similar(picked_value, old_value), _names_similar(picked_value, new_value)
        if picked_value.lower() == "existing" or (picked_value and fits_old and not fits_new):
            chosen[field] = old_value
        elif picked_value.lower() == "new" or (picked_value and fits_new and not fits_old):
            chosen[field] = new_value
        elif picked_value:
            chosen[field] = picked_value

    answer_words = set(re.findall(r"\w+", reply_text.lower()))
    for field, (old_value, new_value) in conflicts.items():
        old_words, new_words = (
            set(re.findall(r"\w+", (value or "").lower())) - _BUSINESS_NAME_FILLER_WORDS
            for value in (old_value, new_value)
        )
        picks_old = bool(answer_words & (old_words - new_words))
        picks_new = bool(answer_words & (new_words - old_words))
        if field not in chosen and picks_old != picks_new:
            chosen[field] = old_value if picks_old else new_value
    says_new = bool(answer_words & _AI_ONBOARDING_USE_GENERATED_WORDS)
    says_old = bool(answer_words & _AI_ONBOARDING_USE_EXISTING_WORDS)
    if says_new != says_old:
        for field, values in conflicts.items():
            chosen.setdefault(field, values[1] if says_new else values[0])
    if len(chosen) < len(conflicts):
        logger.info(
            "[ai_onboarding] provider %s waits for confirmation of %s | answer=%r",
            providerid, list(conflicts), confirmation_answer,
        )
        return AIOnboardingResponse(
            status=existing["status"],
            providerid=existing["providerid"],
            conflicting_business_name=generated_name if "Business_Name" in conflicts else None,
            conflicting_address=generated_address if "Address" in conflicts else None,
            confirmation_required=True,
            business_image_url=blob_path,
            Provider_Details=AIOnboardingProviderDetails(
                **generated_details.model_dump(),
                mobilenumber=earlier["mobilenumber"],
                alternatemobile=next((m for m in generated_mobiles if m != earlier["mobilenumber"]), None),
                **generated_location._asdict(),
                business_category=_tidy_ai_onboarding_listing(generated_details, generated.Service_Details),
            ),
            Service_Details=generated.Service_Details,
            response=_AI_ONBOARDING_MERGE_CONFLICT.format(
                conflicts="; ".join(
                    f"the {_MERGE_CONFLICT_LABELS[field]} on Etown is '{old_value}', but you shared '{new_value}'"
                    for field, (old_value, new_value) in conflicts.items()
                ),
                fields=" and ".join(_MERGE_CONFLICT_LABELS[field] for field in conflicts),
            ),
        )

    reply_value = {
        field: str(reply_facts.get(field) or "").strip() or None for field in ("Email", "Website", "Name")
    }
    provider = OnboardProviderDetails(
        Business_Name=chosen.get("Business_Name", existing_name or generated_name),
        Description=existing["description"] or generated_details.Description,
        Email=reply_value["Email"] or existing["email"] or generated_details.Email,
        Website=reply_value["Website"] or existing["websiteurl"] or generated_details.Website,
        Category=generated_details.Category,
        Name=reply_value["Name"] or existing["firstname"] or generated_details.Name,
        Address=chosen.get("Address", existing["address"] or generated_details.Address),
        Service_Type=generated_details.Service_Type,
    )
    services = [
        OnboardServiceDetail(
            Service_Name=row["services"],
            Service_Description=row["servicedescription"],
            Service_Type={"product": "Product", "service": "Service"}.get((row["businesscategory"] or "").strip().lower()),
            Service_Quantity=row["quantity"],
            Service_Price=row["pricerange"],
            Service_Image_Url=row["serviceimageurl"],
        )
        for row in existing["services"]
        if (row["services"] or "").strip()
    ]
    for new_svc in (generated.Service_Details if generated else []):
        if not (new_svc.Service_Name or "").strip():
            continue
        kept = next((svc for svc in services if _names_similar(svc.Service_Name, new_svc.Service_Name)), None)
        if kept is None:
            if len(services) < _AI_ONBOARDING_MAX_MERGED_SERVICES:
                services.append(new_svc)
            continue
        for field, value in new_svc.model_dump().items():
            if getattr(kept, field) is None or not str(getattr(kept, field)).strip():
                setattr(kept, field, value)
        kept.Service_Image_Url = new_svc.Service_Image_Url or kept.Service_Image_Url
    business_category = _tidy_ai_onboarding_listing(provider, services)

    mobile = normalize_indian_mobile(existing["mobilenumber"]) or earlier["mobilenumber"]
    alternate = (
        next((m for m in reply_mobiles if m != mobile), None)
        or normalize_indian_mobile(existing["alternatemobile"])
        or next((m for m in generated_mobiles if m != mobile), None)
    )

    if "Business_Name" in conflicts:
        rewrite_prompt = AI_ONBOARDING_MERGE_REWRITE_PROMPT.format(
            business_name=provider.Business_Name,
            category=provider.Category or "not given",
            description=provider.Description or "none",
            services=json.dumps(
                [
                    {"Service_Name": svc.Service_Name, "Service_Type": svc.Service_Type, "Service_Description": svc.Service_Description}
                    for svc in services
                ],
                ensure_ascii=False,
            ),
            count=len(services),
        )
        rewritten = await _ask_gemini_json(rewrite_prompt, "description rewrite")
        descriptions = rewritten.get("Service_Descriptions")
        if (
            isinstance(descriptions, list) and len(descriptions) == len(services)
            and str(rewritten.get("Description") or "").strip()
        ):
            provider.Description = str(rewritten["Description"]).strip()
            for svc, description in zip(services, descriptions):
                svc.Service_Description = str(description or "").strip() or svc.Service_Description
        else:
            logger.warning("[ai_onboarding] description rewrite gave no usable reply; descriptions kept")

    if existing_address and existing_location and (provider.Address or "").strip() == existing_address:
        location = existing_location
        if not location.city or not location.statecode:
            try:
                city, statecode = _onboarding_city_state(
                    (await get_location_details_logic(*existing_location[:2])).parsed_components
                )
            except Exception as e:
                logger.warning("[ai_onboarding] could not reverse-geocode provider %s: %s", providerid, e)
                city = statecode = None
            location = location._replace(city=location.city or city, statecode=location.statecode or statecode)
    elif generated_location.latitude is not None and (provider.Address or "").strip() == generated_address:
        location = generated_location
    elif saved_location and provider.Address == saved_details.get("Address"):
        location = saved_location
    else:
        location = await _geocode_onboarding_address(provider.Address)
    logger.info(
        "[ai_onboarding] merged into provider %s | reused_saved=%s confirmed=%s services=%d",
        providerid, reuse_saved, chosen, len(services),
    )
    return AIOnboardingResponse(
        status=existing["status"],
        providerid=existing["providerid"],
        business_image_url=blob_path,
        Provider_Details=AIOnboardingProviderDetails(
            **provider.model_dump(),
            mobilenumber=mobile,
            alternatemobile=alternate,
            **location._asdict(),
            business_category=business_category,
        ),
        Service_Details=services,
        response=_AI_ONBOARDING_MERGED.format(name=provider.Business_Name or "the business"),
    )


async def apply_ai_onboarding_bubble(payload: AIOnboardingBubbleRequest) -> Tuple[AIOnboardingBubbleResponse, Optional[str]]:
    """Apply a follow-up prompt, new image or both to AI onboarding details with Gemini, returning the result and new image's blob path.

    Args:
        payload: The current Provider_Details and Service_Details, conversation id and follow-up prompt and/or image.

    Raises:
        HTTPException: 400 when neither is given, or the image is not base64 of a JPEG, PNG or WebP of at most 10 MB.
    """
    raw_image = _clean_business_image(payload.business_image)
    message = (payload.prompt or "").strip()
    if not raw_image and not message:
        raise HTTPException(status_code=400, detail="Send a business_image, a prompt, or both")

    content_type, image_base64, blob_path = await _store_business_image(raw_image, "ai_onboarding_bubble")
    conversation_id = (payload.conversation_id or "").strip() or str(uuid.uuid4())

    history_rows: List[asyncpg.Record] = []
    if payload.conversation_id:
        try:
            history_rows = await Database.fetch_once(
                """
                SELECT role, prompt_text, response_text, business_image_url IS NOT NULL AS had_image,
                       pending_business_name,
                       (
                           SELECT i.business_image_url
                           FROM etown_ai_onboarding_bubble i
                           WHERE i.conversationid = $1
                             AND i.business_image_url IS NOT NULL
                           ORDER BY i.createdon DESC
                           LIMIT 1
                       ) AS last_image_url
                FROM etown_ai_onboarding_bubble
                WHERE conversationid = $1
                ORDER BY createdon DESC, CASE role WHEN 'assistant' THEN 0 ELSE 1 END
                LIMIT $2
                """,
                conversation_id,
                _AI_ONBOARDING_BUBBLE_HISTORY_TURNS * 2,
            )
        except Exception as e:
            logger.warning("[ai_onboarding_bubble] history lookup failed for %s: %s", conversation_id, e, exc_info=True)
    earlier_image_url = history_rows[0]["last_image_url"] if history_rows else None
    waiting_name = next(
        (row["pending_business_name"] for row in history_rows if row["role"] == "assistant"),
        None,
    )
    history = "\n".join(
        f"Agent: {row['prompt_text'] or ''}{' [sent a photo]' if row['had_image'] else ''}"
        if row["role"] == "user"
        else f"AI: {row['response_text'] or ''}"
        for row in reversed(history_rows)
    ) or "(none: this is the first turn)"

    listing = payload.model_dump(
        include={"Provider_Details", "Service_Details"},
        exclude={"Provider_Details": _BUBBLE_HIDDEN_PROVIDER_FIELDS},
    )
    content: List[Dict[str, Any]] = [{
        "type": "text",
        "text": AI_ONBOARDING_BUBBLE_PROMPT.format(
            listing=json.dumps(listing, ensure_ascii=False, indent=2),
            history=history,
            pending=(
                f"Business name waiting for the agent's confirmation: {json.dumps(waiting_name, ensure_ascii=False)}"
                if waiting_name else "No business name is waiting for confirmation."
            ),
            message=message or _AI_ONBOARDING_BUBBLE_NO_MESSAGE,
            image_note=_AI_ONBOARDING_BUBBLE_IMAGE_NOTE if image_base64 else _AI_ONBOARDING_BUBBLE_NO_IMAGE_NOTE,
        ),
    }]
    if image_base64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{content_type};base64,{image_base64}"},
        })

    parsed: Dict[str, Any] = {}
    for attempt in range(_AI_ONBOARDING_ATTEMPTS):
        try:
            reply = await gemini_2_5_flash_vision.ainvoke([{"role": "user", "content": content}])
            raw_content = getattr(reply, "content", None)
            text = (
                "".join(
                    str(part.get("text") or "") if isinstance(part, dict) else str(part)
                    for part in raw_content
                    if not isinstance(part, dict) or part.get("type", "text") == "text"
                )
                if isinstance(raw_content, list)
                else str(raw_content or "")
            )
            parsed = extract_json(text) or {}
        except Exception as e:
            logger.warning("[ai_onboarding_bubble] attempt %d failed: %s", attempt + 1, e, exc_info=True)
            parsed = {}
        if isinstance(parsed.get("Provider_Details"), dict) and isinstance(parsed.get("Service_Details"), list):
            break
        parsed = {}

    if not parsed:
        logger.warning("[ai_onboarding_bubble] no usable reply after %d attempts", _AI_ONBOARDING_ATTEMPTS)
        return AIOnboardingBubbleResponse(
            conversation_id=conversation_id,
            Provider_Details=payload.Provider_Details,
            Service_Details=payload.Service_Details,
            response=_AI_ONBOARDING_BUBBLE_FAILED,
            confirmation_required=bool(waiting_name),
            pending_business_name=waiting_name,
        ), blob_path

    sent = payload.Provider_Details
    notes: List[str] = []
    provider = AIOnboardingProviderDetails(**{
        key: None if value is None else str(value)
        for key, value in parsed["Provider_Details"].items()
        if key in AIOnboardingProviderDetails.model_fields and key not in _BUBBLE_HIDDEN_PROVIDER_FIELDS
    })
    if "interested" not in parsed["Provider_Details"]:
        provider.interested = sent.interested
    if "Service_Type" not in parsed["Provider_Details"] or (
        parsed["Provider_Details"]["Service_Type"] and not provider.Service_Type
    ):
        provider.Service_Type = sent.Service_Type
    services = [
        OnboardServiceDetail(**{
            key: None if value is None else str(value)
            for key, value in svc.items()
            if key in OnboardServiceDetail.model_fields
        })
        for svc in parsed["Service_Details"]
        if isinstance(svc, dict) and str(svc.get("Service_Name") or "").strip()
    ]
    unnamed = len(parsed["Service_Details"]) - len(services)
    if unnamed:
        notes.append(
            "I've kept 1 service aside as it didn't have a name. Could you please tell me what it's called?"
            if unnamed == 1
            else f"I've kept {unnamed} services aside as they didn't have names. Could you please tell me what they're called?"
        )

    sent_images = {
        (svc.Service_Name or "").strip().lower(): svc.Service_Image_Url
        for svc in payload.Service_Details
        if svc.Service_Image_Url
    }
    image_asked = bool(_PHOTO_WORDS_RE.search(message))
    photo_path = blob_path if raw_image else earlier_image_url
    linked_services: List[str] = []
    for svc in services:
        value = (svc.Service_Image_Url or "").strip()
        if value.upper() == _SERVICE_PHOTO_MARKER:
            svc.Service_Image_Url = photo_path or sent_images.get((svc.Service_Name or "").strip().lower())
            if photo_path:
                linked_services.append(svc.Service_Name or "")
            else:
                notes.append(_AI_ONBOARDING_BUBBLE_NO_PHOTO.format(name=svc.Service_Name))
        elif value and value in sent_images.values():
            svc.Service_Image_Url = value
        elif not value and image_asked:
            svc.Service_Image_Url = None
        else:
            svc.Service_Image_Url = sent_images.get((svc.Service_Name or "").strip().lower())

    pending_name = " ".join(str(parsed.get("pending_business_name") or "").split()) or None
    confirmation_required = str(parsed.get("confirmation_required")).lower() == "true"
    if confirmation_required and pending_name:
        provider.Business_Name = sent.Business_Name
    else:
        pending_name = None

    requested_category = (provider.business_category or "").replace(" ", "").lower()
    provider.business_category = _tidy_ai_onboarding_listing(provider, services) or sent.business_category
    old_name = " ".join((sent.Business_Name or "").split())
    new_name = " ".join((provider.Business_Name or "").split())
    if old_name and new_name and old_name.lower() != new_name.lower():
        old_name_re = re.compile(
            r"(?i)(?<!\w)" + r"\s+".join(map(re.escape, old_name.split())) + r"(?!\w)"
        )
        provider.Description = old_name_re.sub(lambda _: new_name, provider.Description or "") or None
        for svc in services:
            svc.Service_Description = old_name_re.sub(lambda _: new_name, svc.Service_Description or "") or None
    if requested_category and requested_category not in {
        (sent.business_category or "").replace(" ", "").lower(),
        (provider.business_category or "").replace(" ", "").lower(),
    }:
        notes.append(
            "The business type is set automatically from the services listed, so I've kept it as it is. "
            "If you'd like to change it, please let me know which items are products and which are services."
        )

    provider.mobilenumber = sent.mobilenumber
    requested_mobile = (provider.alternatemobile or "").strip()
    provider.alternatemobile = normalize_indian_mobile(requested_mobile)
    if requested_mobile and not provider.alternatemobile:
        provider.alternatemobile = sent.alternatemobile
        notes.append(
            f"Sorry, {requested_mobile} doesn't seem to be a valid mobile number, so I've kept the alternate "
            f"number as it was. Could you please share a 10-digit mobile number?"
        )
    elif provider.alternatemobile and provider.alternatemobile != normalize_indian_mobile(sent.alternatemobile or ""):
        try:
            occupied = bool(await ProviderService.fetch_providers_by_mobile(provider.alternatemobile))
        except Exception as e:
            logger.warning("[ai_onboarding_bubble] alternate mobile lookup failed: %s", e, exc_info=True)
            occupied = None
        if occupied is not False:
            notes.append(
                (_AI_ONBOARDING_BUBBLE_MOBILE_OCCUPIED if occupied else _AI_ONBOARDING_BUBBLE_MOBILE_UNCHECKED)
                .format(mobile=provider.alternatemobile)
            )
            provider.alternatemobile = sent.alternatemobile

    address_key = " ".join((provider.Address or "").split()).lower()
    location = _OnboardingLocation(sent.latitude, sent.longitude, sent.city, sent.statecode)
    if not address_key:
        location = _OnboardingLocation()
    elif (address_changed := address_key != " ".join((sent.Address or "").split()).lower()) or (
        None in location
    ):
        location = await _geocode_onboarding_address(provider.Address)
        if location.latitude is None and address_changed:
            notes.append(_AI_ONBOARDING_BUBBLE_NO_LOCATION)
    provider.latitude, provider.longitude, provider.city, provider.statecode = location

    reply_text = str(parsed.get("response") or "").strip()
    if pending_name and pending_name not in reply_text:
        notes.append(_AI_ONBOARDING_BUBBLE_CONFIRM_NAME.format(name=pending_name))
    result = AIOnboardingBubbleResponse(
        conversation_id=conversation_id,
        Provider_Details=provider,
        Service_Details=services,
        confirmation_required=confirmation_required or bool(notes),
        pending_business_name=pending_name,
    )
    if result.model_dump(
        include={"Provider_Details", "Service_Details"},
        exclude={"Provider_Details": _BUBBLE_HIDDEN_PROVIDER_FIELDS},
    ) == listing:
        if str(parsed.get("is_chat")).lower() == "true" and reply_text and not notes:
            result.confirmation_required = bool(pending_name)
        else:
            reply_text = " ".join(filter(None, [_AI_ONBOARDING_BUBBLE_UNCHANGED, reply_text]))
            result.confirmation_required = True
    result.response = " ".join(filter(None, [reply_text, *notes])) or _AI_ONBOARDING_BUBBLE_APPLIED

    logger.info(
        "[ai_onboarding_bubble] conversation=%s turns=%d image=%s prompt=%s blob=%s business=%r "
        "pending=%r category=%s alternate=%s located=%s photo_linked=%s services=%d->%d notes=%d",
        conversation_id, len(history_rows), bool(raw_image), bool(message), blob_path,
        provider.Business_Name, pending_name, provider.business_category,
        bool(provider.alternatemobile), location.latitude is not None, linked_services,
        len(payload.Service_Details), len(services), len(notes),
    )
    return result, blob_path


async def record_ai_onboarding_bubble(
    payload: AIOnboardingBubbleRequest,
    result: AIOnboardingBubbleResponse,
    image_path: Optional[str],
    received_at: datetime,
    responded_at: datetime,
) -> None:
    """Insert one /bubble turn as a 'user' row and an 'assistant' row in etown_ai_onboarding_bubble, logging and swallowing any failure.

    Args:
        payload: The request as sent; its caller ids, prompt and details are stored, never the image base64.
        result: The response returned to the caller; its message and whole JSON are stored.
        image_path: Blob path of the image uploaded on this turn, stored on the user row, or None.
        received_at: IST wall-clock time the request arrived, the user row's createdon.
        responded_at: IST wall-clock time the response was ready, the assistant row's createdon.
    """
    try:
        await Database.execute(
            """
            INSERT INTO etown_ai_onboarding_bubble (
                messageid, conversationid, turnid, role, contactid, userprofileid,
                prompt_text, response_text, business_image_url, payload,
                pending_business_name, response_time_ms, createdon
            )
            VALUES
                ($1, $3, $4, 'user', $5, $6, $7, NULL, $8, $9::jsonb, NULL, NULL, $10),
                ($2, $3, $4, 'assistant', $5, $6, NULL, $11, NULL, $12::jsonb, $13, $14, $15)
            """,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            result.conversation_id,
            str(uuid.uuid4()),
            (payload.contact_id or "").strip() or None,
            (payload.user_profile_id or "").strip() or None,
            (payload.prompt or "").strip() or None,
            image_path,
            payload.model_dump_json(include={"Provider_Details", "Service_Details"}),
            received_at,
            result.response,
            result.model_dump_json(),
            result.pending_business_name,
            max(0, int((responded_at - received_at).total_seconds() * 1000)),
            responded_at,
        )
    except Exception as e:
        logger.error("[ai_onboarding_bubble] Could not record the turn in etown_ai_onboarding_bubble: %s", e, exc_info=True)


async def record_ai_onboarding(
    payload: AIOnboardingRequest,
    result: AIOnboardingResponse,
    received_at: datetime,
    responded_at: datetime,
) -> None:
    """Insert one /ai_onboarding call as a 'user' row and an 'assistant' row in etown_ai_onboarding, logging and swallowing any failure.

    Args:
        payload: The request as sent; its conversation id, caller ids and prompt are stored, never the image base64.
        result: The response returned to the caller; its message and whole JSON are stored.
        received_at: IST wall-clock time the request arrived, the user row's createdon.
        responded_at: IST wall-clock time the response was ready, the assistant row's createdon.
    """
    try:
        await Database.execute(
            """
            INSERT INTO etown_ai_onboarding (
                aionboardingid, conversationid, turnid, role, contactid, userprofileid,
                prompt_text, business_image_url, response_text, response,
                response_time_ms, createdon
            )
            VALUES
                ($1, $13, $3, 'user', $4, $5, $6, $7, NULL, NULL, NULL, $8),
                ($2, $13, $3, 'assistant', $4, $5, NULL, NULL, $9, $10::jsonb, $11, $12)
            """,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            (payload.contact_id or "").strip() or None,
            (payload.user_profile_id or "").strip() or None,
            (payload.prompt or "").strip() or None,
            result.business_image_url,
            received_at,
            result.response,
            result.model_dump_json(),
            max(0, int((responded_at - received_at).total_seconds() * 1000)),
            responded_at,
            (payload.conversation_id or "").strip() or None,
        )
    except Exception as e:
        logger.error("[ai_onboarding] Could not record the call in etown_ai_onboarding: %s", e, exc_info=True)


class RatingService:
    """Service class for provider rating submission."""

    @staticmethod
    @with_retry()
    async def add_rating(
        booking_id: str,
        rating_value: int,
        israting: str = "True",
    ) -> Dict[str, Any]:
        """Record a booking's rating and create or update the provider's etown_ratings aggregate row.

        Args:
            booking_id: allrequestid or requestid of the booking being rated.
            rating_value: Star rating value, 1 to 5.
            israting: Rating flag string written to isRating.

        Returns:
            The etown_ratings row as a dict, plus _is_new and _allrequestid.

        Raises:
            RuntimeError: If the database pool is not initialized.
            HTTPException: 404 when the booking or its service record is not found.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            booking_row = await con.fetchrow(
                "SELECT allrequestid FROM etown_allrequests WHERE allrequestid = $1 OR requestid = $1 LIMIT 1",
                booking_id,
            )
            if not booking_row:
                raise HTTPException(status_code=404, detail=f"Booking '{booking_id}' not found")
            allrequestid = booking_row["allrequestid"]

            await con.execute(
                """
                UPDATE etown_allrequests
                SET \"isRating\" = $1, ratingvalue = $2, modifieddate = NOW()
                WHERE allrequestid = $3
                """,
                israting,
                rating_value,
                allrequestid,
            )

            service_row = await con.fetchrow(
                """
                SELECT serviceprovideruserprofileid, serviceprovidercontactid
                FROM etown_requestservicelists
                WHERE bookingid = $1
                LIMIT 1
                """,
                allrequestid,
            )
            if not service_row:
                raise HTTPException(
                    status_code=404,
                    detail=f"No service record found for booking '{booking_id}'",
                )
            provider_upid = service_row["serviceprovideruserprofileid"]
            provider_contactid = service_row["serviceprovidercontactid"]

            stats_query = """
                SELECT
                    COUNT(ar.allrequestid) AS booking_count,
                    COALESCE(SUM(ar.ratingvalue), 0) AS rating_sum
                FROM etown_allrequests ar
                WHERE ar.allrequestid IN (
                    SELECT bookingid FROM etown_requestservicelists
                    WHERE serviceprovideruserprofileid = $1
                )
                  AND LOWER(TRIM(ar."isRating")) = 'true'
                  AND ar.ratingvalue IS NOT NULL
            """
            businessname_query = """
                SELECT businessname FROM etown_provider
                WHERE userprofileid = $1
                LIMIT 1
            """

            stats_row = await con.fetchrow(stats_query, provider_upid)
            businessname_row = await con.fetchrow(businessname_query, provider_upid)

            memberbookingcount = int(stats_row["booking_count"]) if stats_row else 1
            ratingcount = int(stats_row["rating_sum"]) if stats_row else rating_value
            overallrating = round(ratingcount / memberbookingcount if memberbookingcount > 0 else float(rating_value), 2)
            business_name = businessname_row["businessname"] if businessname_row else None

            existing_rating = await con.fetchrow(
                "SELECT ratingid FROM etown_ratings WHERE provideruserprofileid = $1 LIMIT 1",
                provider_upid,
            )

            if existing_rating:
                updated_row = await con.fetchrow(
                    """
                    UPDATE etown_ratings
                    SET ratingcount = $1,
                        overallrating = $2,
                        memberbookingcount = $3
                    WHERE ratingid = $4
                    RETURNING *
                    """,
                    ratingcount,
                    overallrating,
                    memberbookingcount,
                    existing_rating["ratingid"],
                )
                result = dict(updated_row)
                result["_is_new"] = False
            else:
                new_ratingid = str(uuid.uuid4())
                inserted_row = await con.fetchrow(
                    """
                    INSERT INTO etown_ratings (
                        ratingid, overallrating, ratingcount,
                        provideruserprofileid, providercontactid,
                        providerbusinessname, memberbookingcount, createdon
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                    RETURNING *
                    """,
                    new_ratingid,
                    overallrating,
                    ratingcount,
                    provider_upid,
                    provider_contactid,
                    business_name,
                    memberbookingcount,
                )
                result = dict(inserted_row)
                result["_is_new"] = True

        createdon = result.get("createdon")
        if createdon and hasattr(createdon, "isoformat"):
            result["createdon"] = createdon.isoformat()

        result["_allrequestid"] = allrequestid

        return result


_NUDGE_DEDUP_SQL = """
    SELECT 1
    FROM notifications.etown_member_provider_wa_nudge
    WHERE customer_userprofileid = $1
      AND provider_userprofileid = $2
    LIMIT 1
"""

_NUDGE_LOOKUP_SQL = """
    SELECT ec.contactid, ec.mobilephone, ep.businessname, ep.providertype, up.userprofileid
    FROM etown_contact ec
    LEFT JOIN etown_userprofile up ON up.contactid = ec.contactid AND up.role = 1
    LEFT JOIN etown_provider ep ON ep.contactid = ec.contactid
    WHERE ec.contactid = $1
    LIMIT 1
"""

_NUDGE_LOOKUP_BY_MOBILE_SQL = """
    SELECT ec.contactid, ec.mobilephone, ep.businessname, ep.providertype, up.userprofileid
    FROM etown_contact ec
    LEFT JOIN etown_userprofile up ON up.contactid = ec.contactid AND up.role = 1
    LEFT JOIN etown_provider ep ON ep.contactid = ec.contactid
    WHERE (right(regexp_replace(ec.mobilephone, '[^0-9]', '', 'g'), 10) = $1 AND $1 <> '')
       OR ec.mobilephone = $2
    ORDER BY (ep.providerid IS NOT NULL) DESC, (up.userprofileid IS NOT NULL) DESC
    LIMIT 1
"""

_NUDGE_CUSTOMER_NAME_SQL = """
    SELECT firstname
    FROM etown_contact
    WHERE contactid = $1
    LIMIT 1
"""

_NUDGE_INSERT_SQL = """
    INSERT INTO notifications.etown_member_provider_wa_nudge
        (customer_userprofileid, customer_contactid,
         provider_userprofileid, provider_contactid,
         mobilenumber, businessname, status, error_detail)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (customer_userprofileid, provider_userprofileid) DO NOTHING
"""


async def update_provider_whatsapp_reachability(
    mobile_number: str | None,
    reachable: bool,
) -> int:
    """Set etown_provider.whatsapp on every provider whose mobile matches, writing only rows that change.

    Args:
        mobile_number: Recipient number from the WhatsApp status webhook, any format.
        reachable: True to mark the number WhatsApp-reachable, False otherwise.

    Returns:
        Number of provider rows updated; 0 for an invalid number or no change.
    """
    national = normalize_indian_mobile(mobile_number)
    if not national:
        return 0

    status = await Database.execute(
        """
        UPDATE public.etown_provider
        SET whatsapp = $1, modifieddate = NOW()
        WHERE RIGHT(regexp_replace(mobilenumber, '[^0-9]', '', 'g'), 10) = $2
          AND whatsapp IS DISTINCT FROM $1
        """,
        reachable,
        national,
    )

    updated = 0
    if isinstance(status, str) and status.startswith("UPDATE "):
        try:
            updated = int(status.rsplit(" ", 1)[1])
        except (ValueError, IndexError):
            logger.warning("provider: could not read a row count out of status %r", status)
            updated = 0
    if updated:
        logger.info(
            "[WA_REACH] Set whatsapp=%s for %d provider(s) matching national=%s",
            reachable, updated, national,
        )
    return updated


async def is_provider_mobile_known_off_whatsapp(mobile_number: str | None) -> bool:
    """Return True when some provider on the number has whatsapp false and none true; False on lookup failure."""
    national = normalize_indian_mobile(mobile_number)
    if not national:
        return False
    try:
        row = await Database.fetchrow(
            """
            SELECT bool_or(whatsapp IS FALSE) AS off, bool_or(whatsapp IS TRUE) AS on
            FROM public.etown_provider
            WHERE RIGHT(regexp_replace(COALESCE(mobilenumber, ''), '[^0-9]', '', 'g'), 10) = $1
            """,
            national,
        )
    except Exception as exc:
        logger.warning("[WA_REACH] Could not read the whatsapp flag for national=%s: %s", national, exc)
        return False
    return bool(row and row["off"] and not row["on"])


async def send_unverified_provider_nudge(
    customer_userprofileid: str,
    customer_contactid: str,
    provider_userprofileid: str | None = None,
    provider_contactid: str | None = None,
    provider_mobilenumber: str | None = None,
    provider_name: str | None = None,
) -> dict:
    """Send a member's once-only WhatsApp nudge to an unverified provider found by id or mobile, and log it.

    Args:
        customer_userprofileid: UserProfile ID of the member triggering the nudge.
        customer_contactid: Contact ID of the member triggering the nudge.
        provider_userprofileid: UserProfile ID of the provider; resolved when not supplied.
        provider_contactid: Contact ID of the provider, tried first for the lookup.
        provider_mobilenumber: Provider mobile, used for lookup or a direct nudge with provider_name.
        provider_name: Business name for the message when the record has none.

    Returns:
        Dict with sent, reason and businessname.
    """
    from app.utils.whatsapp import send_unverified_provider_enquiry_whatsapp_notification
    from app.agent_infrastructure.tools.tools_utils.search_provider import is_provider_verified

    provider_contactid = (provider_contactid or "").strip() or None
    provider_userprofileid = (provider_userprofileid or "").strip() or None
    provider_mobilenumber = (provider_mobilenumber or "").strip() or None
    provider_name = (provider_name or "").strip() or None

    national_mobile = normalize_indian_mobile(provider_mobilenumber)

    provider_row = None
    if provider_contactid:
        provider_row = await Database.fetchrow(_NUDGE_LOOKUP_SQL, provider_contactid)
    if not provider_row and provider_mobilenumber:
        provider_row = await Database.fetchrow(
            _NUDGE_LOOKUP_BY_MOBILE_SQL, national_mobile or "", provider_mobilenumber
        )

    if provider_row:
        provider_contactid = provider_row.get("contactid") or provider_contactid
        provider_userprofileid = (
            provider_row.get("userprofileid") or provider_userprofileid or provider_contactid
        )
        mobile = provider_row.get("mobilephone") or ""
        businessname = provider_row.get("businessname") or ""
        providertype = provider_row.get("providertype") or ""
    elif national_mobile and provider_name:
        mobile = national_mobile
        businessname = ""
        providertype = ""
        provider_userprofileid = f"mobile:{national_mobile}"
        provider_contactid = f"mobile:{national_mobile}"
    else:
        logger.warning(
            "[ProviderNudge] Provider not resolved | provider_contactid=%s mobile=%s",
            provider_contactid,
            provider_mobilenumber,
        )
        if provider_contactid and provider_userprofileid:
            await Database.execute(
                _NUDGE_INSERT_SQL,
                customer_userprofileid, customer_contactid,
                provider_userprofileid, provider_contactid,
                None, None, "failed", "provider contact not found",
            )
        return {"sent": False, "reason": "provider_not_found", "businessname": None}

    businessname = businessname or (provider_name or "")

    existing = await Database.fetchrow(_NUDGE_DEDUP_SQL, customer_userprofileid, provider_userprofileid)
    if existing:
        logger.info(
            "[ProviderNudge] Already notified — skipping | customer=%s provider=%s",
            customer_userprofileid,
            provider_userprofileid,
        )
        return {"sent": False, "reason": "already_notified", "businessname": None}

    if is_provider_verified(providertype):
        logger.info(
            "[ProviderNudge] Skipping verified provider | provider_contactid=%s providertype=%s",
            provider_contactid,
            providertype,
        )
        return {"sent": False, "reason": "provider_verified", "businessname": businessname or None}

    if not mobile.strip():
        logger.warning(
            "[ProviderNudge] No mobile number on record | provider_contactid=%s", provider_contactid
        )
        await Database.execute(
            _NUDGE_INSERT_SQL,
            customer_userprofileid, customer_contactid,
            provider_userprofileid, provider_contactid,
            None, businessname or None, "failed", "no mobile number",
        )
        return {"sent": False, "reason": "no_mobile", "businessname": businessname or None}

    member_name = ""
    customer_row = await Database.fetchrow(_NUDGE_CUSTOMER_NAME_SQL, customer_contactid)
    if customer_row:
        member_name = customer_row.get("firstname") or ""

    sent, reason = await send_unverified_provider_enquiry_whatsapp_notification(
        mobile_number=mobile,
        business_name=businessname,
        member_name=member_name,
    )

    status = "sent" if sent else "failed"
    error_detail = None if sent else (reason or "send_failed")

    await Database.execute(
        _NUDGE_INSERT_SQL,
        customer_userprofileid, customer_contactid,
        provider_userprofileid, provider_contactid,
        mobile, businessname or None, status, error_detail,
    )

    if sent:
        logger.info(
            "[ProviderNudge] Nudge sent | customer=%s provider=%s mobile=%s",
            customer_userprofileid,
            provider_userprofileid,
            mobile,
        )
        return {"sent": True, "reason": None, "businessname": businessname or None}

    logger.warning(
        "[ProviderNudge] Send failed | customer=%s provider=%s mobile=%s reason=%s",
        customer_userprofileid,
        provider_userprofileid,
        mobile,
        reason,
    )
    return {"sent": False, "reason": reason or "send_failed", "businessname": businessname or None}


async def send_fos_business_onboarding_email(
    email: str,
    provider_name: str,
) -> bool:
    """Email a provider the welcome note for a field-agent-created profile; never raises.

    Args:
        email: The business email captured on the provider.
        provider_name: Business name, rendered into the greeting.

    Returns:
        True when Graph accepted the message, False with no address or on failure.
    """
    import html as _html

    from app.core.config import settings
    from app.services.email_service import send_email
    from app.services.email_templates import bullet_list, button, paragraph, render_email

    address = (email or "").strip()
    if not address:
        return False

    safe_name = _html.escape((provider_name or "").strip() or "there")

    content = (
        paragraph(f"Hi <strong>{safe_name}</strong>,")
        + paragraph(
            "Our field team has created your business profile on etown Services, making it "
            "easier for customers to find and connect with your business."
        )
        + paragraph(
            "Your profile is ready — all you need to do is register on etown using the same "
            "mobile number associated with your business profile."
        )
        + paragraph("<strong>What can you do with etown?</strong>")
        + paragraph("With your etown business profile, you can:")
        + bullet_list([
            "Showcase your business to customers looking for your products and services",
            "Manage bookings and customer requests in one place",
            "Keep your business information updated",
            "Connect with more customers in your area",
            "Grow your business with etown",
        ])
        + paragraph("<strong>Get started today</strong>")
        + paragraph(
            "Register using your existing mobile number and take control of your etown "
            "business profile."
        )
        + button("Access My Business Profile", settings.APP_OPEN_URL)
        + paragraph("Need help? Our team is happy to assist you.")
        + paragraph("Thank you,<br>Team etown Services")
    )

    footer_parts = ["Etown Services"]
    if settings.SUPPORT_PHONE:
        footer_parts.append(_html.escape(settings.SUPPORT_PHONE))
    if settings.SUPPORT_EMAIL:
        footer_parts.append(
            f'<a href="mailto:{settings.SUPPORT_EMAIL}" style="color:inherit;">'
            f"{settings.SUPPORT_EMAIL}</a>"
        )

    try:
        await send_email(
            to=address,
            subject="Your Business is Now on etown. Let's Get Started!",
            body=render_email(
                heading="Welcome to etown",
                content=content,
                preheader=(
                    "Your etown business profile is ready. Register using your mobile "
                    "number to get started."
                ),
                footer=(
                    " &middot; ".join(footer_parts)
                    + "<br>This is an automated message from Etown. Please do not reply to this email."
                ),
            ),
        )
        logger.info("[FOSOnboardEmail] Sent | provider=%s to=%s", provider_name, address)
        return True
    except Exception as exc:
        logger.error(
            "[FOSOnboardEmail] Send failed | provider=%s to=%s: %s",
            provider_name, address, exc, exc_info=True,
        )
        return False


def build_provider_image_prompt(category: str, services: str, description: str = "", business_name: str = "") -> str:
    """Build the local image-model prompt for a still life of a business's trade.

    Args:
        category: Service category, e.g. pharmacy or Medical Store.
        services: Comma-separated services or products the business offers.
        description: Optional business description added as context.
        business_name: Subject used only when nothing else is known.
    """
    category = " ".join((category or "").replace("_", " ").split())[:150]
    services = " ".join((services or "").split())[:300]
    description = " ".join((description or "").split())[:250]
    subject = services or category or description or (business_name or "").strip()
    prompt = (
        f"Professional photograph for a local {category or 'shop'} in India: "
        f"a close-up still life of {subject}, the real products, tools and setting of this business. "
        "Items neatly arranged on a clean surface, bright natural light, sharp focus, photorealistic. "
        "Every package, box, bottle and label is plain and blank."
    )
    if description and subject != description:
        prompt += f" About the business: {description}"
    return prompt


def build_provider_image_brief(category: str, services: str, description: str = "", business_name: str = "") -> str:
    """Build the business details gpt-4o-mini turns into a profile image prompt.

    Args:
        category: Service category, e.g. pharmacy or Medical Store.
        services: Comma-separated services or products the business offers.
        description: Optional business description.
        business_name: Optional business name, a clue to the trade.
    """
    fields = (
        ("Business name", " ".join((business_name or "").split())[:100]),
        ("Category", " ".join((category or "").replace("_", " ").split())[:150]),
        ("Services and products", " ".join((services or "").split())[:300]),
        ("Description", " ".join((description or "").split())[:400]),
    )
    return "\n".join(f"{label}: {value}" for label, value in fields if value)


async def render_provider_image(prompt: str, seed: Optional[int] = None, brief: str = "") -> Optional[str]:
    """Write the scene with gpt-4o-mini from a brief, generate a top-centre watermarked JPG and upload it under provider_images/.

    Args:
        prompt: Prompt from build_provider_image_prompt, sent unchanged when there is no brief or enhancement fails.
        seed: Optional seed; distinct seeds give distinct variants of one prompt.
        brief: Business details from build_provider_image_brief that gpt-4o-mini turns into the image prompt.

    Returns:
        The blob path, or None when generation or upload failed.
    """
    from app.agent_infrastructure.prompt_templates.templates import PROVIDER_PROFILE_IMAGE_ENHANCE_PROMPT
    from app.agent_infrastructure.tools.tools_utils.generate_image import enhance_image_prompt, handle_generate_image

    if brief.strip():
        enhanced = await enhance_image_prompt(brief, "general", PROVIDER_PROFILE_IMAGE_ENHANCE_PROMPT)
        if enhanced and enhanced != brief.strip():
            prompt = enhanced
        else:
            logger.warning("[provider_image] Prompt enhancement failed; using the built prompt")

    _result_text, implicit_output = await handle_generate_image(
        prompt=prompt,
        image_type="general",
        width=1024,
        height=1024,
        seed=seed,
        store_format="jpeg",
        store_quality=95,
        watermark_top_center=True,
        enhance=False,
        store_prefix="provider_images",
    )
    return (implicit_output or {}).get("blob_path")


async def store_provider_profile_image(provider_id: str, blob_path: str, sync: bool = True) -> bool:
    """Store an image on a provider and its blank service rows while the provider has none, then mirror it to Dataverse.

    Args:
        provider_id: Provider to give the image.
        blob_path: Stored image path from render_provider_image.
        sync: False leaves the Dataverse mirror to the caller.

    Returns:
        True when the provider took the image; False when it already had one.
    """
    row = await Database.fetchrow(
        """
        WITH upd_provider AS (
            UPDATE etown_provider SET serviceimageurl = $2, modifieddate = NOW()
            WHERE providerid = $1
              AND lower(COALESCE(TRIM(serviceimageurl), '')) IN ('', 'null')
            RETURNING providerid
        ),
        upd_services AS (
            UPDATE etown_providerservices SET serviceimageurl = $2, modifieddate = NOW()
            WHERE providerid = $1
              AND EXISTS (SELECT 1 FROM upd_provider)
              AND lower(COALESCE(TRIM(serviceimageurl), '')) IN ('', 'null')
            RETURNING providerservicesid
        ),
        upd_services_copy AS (
            UPDATE etown_providerservices_copy SET serviceimageurl = $2, modifieddate = NOW()
            WHERE providerid = $1
              AND EXISTS (SELECT 1 FROM upd_provider)
              AND lower(COALESCE(TRIM(serviceimageurl), '')) IN ('', 'null')
            RETURNING providerservicesid
        )
        SELECT EXISTS (SELECT 1 FROM upd_provider) AS updated,
               ARRAY(SELECT providerservicesid FROM upd_services) AS providerservices_ids,
               (SELECT count(*) FROM upd_services_copy) AS copies
        """,
        provider_id, blob_path,
    )
    if not row or not row["updated"]:
        logger.warning("[provider_image] provider=%s already has an image; %s not stored", provider_id, blob_path)
        return False

    logger.info(
        "[provider_image] Stored %s for provider=%s services=%s copies=%s",
        blob_path, provider_id, len(row["providerservices_ids"]), row["copies"],
    )
    if sync:
        await sync_provider_image_to_dataverse(
            provider_id=provider_id,
            provider_image=blob_path,
            service_images={str(i): blob_path for i in row["providerservices_ids"]},
        )
    return True


async def generate_provider_profile_image(provider_id: str, sync: bool = True) -> Optional[str]:
    """Generate a trade picture for a provider without a profile image, store it on every blank row and mirror it to Dataverse.

    Args:
        provider_id: Provider whose serviceimageurl is blank.
        sync: False leaves the Dataverse mirror to the caller.

    Returns:
        The stored blob path, or None when the provider already has an image or generation failed.
    """
    provider = await Database.fetchrow(
        """
        SELECT p.businessname, p.description,
               string_agg(DISTINCT NULLIF(TRIM(psc.servicecategory), ''), ', ') AS servicecategories,
               string_agg(DISTINCT NULLIF(TRIM(psc.services), ''), ', ') AS services
        FROM etown_provider p
        LEFT JOIN etown_providerservices_copy psc
               ON psc.providerid = p.providerid
              AND lower(coalesce(psc.deleted, 'false')) <> 'true'
        WHERE p.providerid = $1
          AND lower(COALESCE(TRIM(p.serviceimageurl), '')) IN ('', 'null')
        GROUP BY p.providerid
        """,
        provider_id,
    )
    if not provider:
        return None

    details = (provider["servicecategories"], provider["services"], provider["description"], provider["businessname"])
    blob_path = await render_provider_image(
        build_provider_image_prompt(*details), brief=build_provider_image_brief(*details),
    )
    if not blob_path:
        logger.warning("[provider_image] Generation or upload failed for provider=%s (%s)", provider_id, provider["businessname"])
        return None
    return blob_path if await store_provider_profile_image(provider_id, blob_path, sync) else None
