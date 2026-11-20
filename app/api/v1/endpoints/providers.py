"""Provider Endpoints - HTTP routes for provider onboarding, edits, review, lifecycle and analytics.

HTTP layer for /api/provider: checks the shared bearer token and delegates onboarding, field captures and approval, profile and service edits, deletes, lifecycle, ratings and analytics to provider_utils and its sibling modules, scheduling the Dataverse mirror for each write.

Features:
    - Provider Onboarding & Editing: POST /onboard creates a provider or edits one with action_mode='edit'
    - Service Generation Preview: /onboard_services returns AI services for an onboarding without storing them
    - Profile & Service Edits: /edit_profile and /edit_service with ownership checks and image sentinels
    - Background Dataverse Sync: every write schedules a background Dataverse mirror
    - Search Vector Refresh: content_vector regeneration when an edit touches embedding fields
    - AI Vision Onboarding: /onboard_details extracts provider and service details from an image
    - Rating & Analytics: /rating records booking ratings; /rangesummary reports date-range metrics
    - WhatsApp Nudges: /notify_unverified sends a deduplicated nudge to an unverified provider
    - Field Capture: /unverified_provider onboards non-verified providers with an FOS proximity gate
    - AI Onboarding: /ai_onboarding reads a business from an image and/or prompt and generates its listing copy
    - AI Onboarding Bubble: /bubble applies a field agent's follow-up prompt to its provider and service details
    - Review & Approval: every create and every edit capture goes to etown_provider_review; /approve verifies
    - Onboarding Confirmation: field captures get a WhatsApp (SMS fallback) and a support email
    - Lifecycle: /lifecycle deactivates or reactivates one business
    - Plan Gating: listing caps and plan-gated reads return 402
    - API Documentation: Automatic OpenAPI documentation generation via FastAPI response models

Endpoints:
    POST /onboard: Register a new provider with at least one service, or update one when action_mode='edit'
    POST /onboard_services: Generate an onboarding's AI services and categories without storing them
    POST /unverified_provider: Register a non-verified field capture; every create and edit goes to etown_provider_review
    GET /unverified_provider_list: List live providers on a mobile number with services, each classified
    POST /approve: Set approve on a providerid, (un)verify it, apply corrections, announce nearby
    POST /add_services: Add new services to an existing provider
    POST /onboard_details: Extract provider and service details from a business image
    POST /share: Push a shared-provider notification to a list of recipients
    POST /edit_profile: Update a provider profile and every service row underneath it
    POST /edit_service: Update one of a provider's service rows
    GET /identifiers: Resolve a providerid to its owning contactid + userprofileid
    GET /unrated_provider_list: List providers the requesting user has not yet rated
    GET /provider_request_list: List all booking requests received by a provider
    GET /provider_completed_list: List all completed bookings received by a provider, with ratings
    DELETE /delete_service: Delete one service row from a provider
    DELETE /delete: Physically delete a provider and its records, keeping the owning contact
    POST /lifecycle: Deactivate or reactivate one business, leaving its owner alone
    GET /rangesummary: Date-range offerview/mycircle/offer/booking counts, plus lifetime totals
    GET /profile_viewers: List the users who viewed the provider's profile, newest first
    POST /rating: Submit a star rating for a completed booking
    POST /notify_unverified: Send a one-per-member-per-provider WhatsApp nudge to an unverified provider

Classes:
    No classes defined (module exposes FastAPI router endpoints plus private helpers)

Functions:
    _str_or_none(): Stringify a value, keeping None as None so a field stays null rather than 'None'
    _iso_or_none(): Render a timestamp as ISO 8601, passing through anything already stringly typed
    share_provider_endpoint(): Handle POST /share — push a shared-provider notification to recipients
    edit_provider_profile(): Handle POST /edit_profile — provider profile + all service rows in one statement
    edit_provider_service(): Handle POST /edit_service — one service row in one statement
    _edit_onboarded_provider(): Apply an action_mode='edit' payload to an existing provider and mirror it to Dataverse
    _add_onboard_service_details(): Write /onboard's Service_Details as verified services and queue their enhancement
    onboard_provider(): Handle POST /onboard for new provider registration (action_mode='create') and edits (action_mode='edit')
    generate_onboard_services(): Handle POST /onboard_services — AI services for an onboarding, nothing stored
    _parse_coordinate(): Parse a string coordinate, or None when blank, invalid or out of range
    _enforce_fos_proximity(): Refuse a rostered FOS capture taken away from the business
    _normalize_contact_id(): Comparable bare lowercase form of a contact UUID
    _contact_ids_on_mobile(): Every live contact holding a mobile number, as comparable ids
    _reject_fos_own_business(): Refuse a capture whose business number belongs to the capturing agent
    _alternate_mobile_conflict(): Why an alternate mobile cannot join the main number's account, or None
    _first_service_name(): Name of the first service in the request body
    onboard_unverified_provider(): Handle POST /unverified_provider — non-verified registration plus edit mode
    _send_fos_business_onboarding_whatsapp_or_sms(): Send the field-capture confirmation WhatsApp, falling back to SMS
    _sms_if_whatsapp_fails(): Watch an accepted confirmation WhatsApp's wamid and send the SMS if it fails
    _queue_fos_business_onboarding_whatsapp(): Queue the field-capture confirmation for a create or an edit
    _edit_unverified_provider(): Park an action_mode='edit' payload in etown_provider_review
    _record_create_review(): Store a create-mode body as a review row, failing quietly
    get_unverified_provider_list(): Handle GET /unverified_provider_list — providers on a mobile, classified
    _apply_approval_updates(): Write POST /approve's optional fields and servicedetails onto the listing
    set_provider_approval(): Handle POST /approve — write etown_provider.approve for one providerid and verify on approval
    _add_unverified_provider_services(): Run /add_services' workflow over a just-onboarded non-verified provider
    add_services_to_providers(): Handle POST /add_services endpoint for adding services to providers
    get_unrated_provider_list(): Handle GET /unrated_provider_list — providers the caller has not yet rated
    get_provider_identifiers(): Handle GET /identifiers endpoint returning the contactid + userprofileid behind a providerid
    get_provider_request_list(): Handle GET /provider_request_list — bookings where the caller is the provider
    provider_completed_list(): Handle GET /provider_completed_list — completed bookings with their rating
    delete_provider_service(): Handle DELETE /delete_service — remove one service row after an ownership check
    delete_provider(): Handle DELETE /delete — remove a provider and its records, keeping the contact
    provider_lifecycle(): Handle POST /lifecycle — deactivate or reactivate one business
    get_date_range_summary(): Handle GET /rangesummary — date-range metrics plus lifetime total_metric
    get_profile_viewers(): Handle GET /profile_viewers — who viewed the provider's profile
    onboard_details(): Handle POST /onboard_details — AI vision extraction of provider/service details from an image
    ai_onboarding(): Handle POST /ai_onboarding — read a business from an image and/or prompt
    ai_onboarding_bubble(): Handle POST /bubble — apply a follow-up prompt and/or image to AI onboarding details
    add_rating(): Handle POST /rating — submit/update a booking rating and sync it to Dataverse
    notify_unverified_provider(): Handle POST /notify_unverified — send a WhatsApp nudge to an unverified provider

Dependencies:
    - fastapi: Router, BackgroundTasks, Security/Depends auth, request/response models
    - app.etown.provider.provider_utils: provider, request-list and rating services, summaries, vision, nudges
    - app.etown.provider.provider_services_utils: service management, offering mapping, check_listing_cap
    - app.etown.plans.plans_utils: PlanLimitError, resolve_plan and badge helpers
    - app.etown.offers.offerings_utils: save_parsed_offerings_to_db for AI-parsed offerings
    - app.etown.provider.provider_lifecycle_utils: deactivate/reactivate a provider
    - app.etown.provider.provider_review_utils: etown_provider_review rows and mobile lookups
    - app.etown.user.user_utils: staff-role checks, phone contact lookup, capture source
    - app.etown.user.account_lifecycle_syncing: Dataverse mirror of lifecycle flags
    - app.etown.dataverse_sync.dataverse_sync_utils: refresh_content_vectors and set_provider_verification
    - app.etown.dataverse_sync.syncing: approval and verification Dataverse mirrors
    - app.etown.provider.provider_syncing: background Dataverse sync tasks (delete, edit, rating, profile update)
    - app.utils.ist_clock: IST arrival and reply times for /bubble turns
    - app.utils.whatsapp / app.utils.sms: field-capture onboarding confirmation messages
    - app.utils.phone: normalize_indian_mobile()
    - geopy: FOS proximity distance
    - app.services.api_activity: track_api_call() activity recording
    - app.schemas.api: Pydantic request/response models for every endpoint
    - app.core.security: verify_token bearer-token dependency
    - app.etown.notifications.triggers: notify_provider_shared / notify_new_provider_nearby push notifications
    - app.core.logging: Centralized logging infrastructure

Updates:
    21-Apr-2026: Initial creation with provider onboarding endpoint.
    06-May-2026: Added add_services_to_providers endpoint.
    27-May-2026: Added date_range_summary endpoint.
    15-Jun-2026: Added onboard_details endpoint for AI vision-based provider extraction.
    17-Jun-2026: Added POST /rating with Dataverse sync; userprofile_id passed to run_background_enhancements.
    25-Jun-2026: Added POST /notify_unverified WhatsApp nudge endpoint.
    29-Jun-2026: add_services_to_providers processes services concurrently.
    03-Jul-2026: Added POST /edit_profile.
    08-Jul-2026: Email and website passed to onboard_provider_in_db() and run_background_enhancements().
    10-Jul-2026: notify_unverified accepts provider_mobilenumber and provider_name.
    13-Jul-2026: Added POST /share (migrated from PowerApps ShareProviders).
    14-Jul-2026: /add_services accepts nested and legacy flat payloads.
    06-Aug-2026: POST /onboard accepts action_mode; edit mode keyed on providerid.
    10-Aug-2026: Added GET /identifiers; /rangesummary adds total_metric, insights and offer_count.
    18-Aug-2026: /add_services and /rangesummary return 402 on plan limits.
    19-Aug-2026: /provider_completed_list returns Rating per booking.
    20-Aug-2026: Request and completed lists return Etown_providertype; /unrated_provider_list maps rows to UnratedProviderItem.
    24-Aug-2026: /edit_profile mirrors background_image as ets_backgroundimage.
    26-Aug-2026: Added GET /profile_viewers; logged previously-silent exception handlers.
    01-Sep-2026: Added POST /lifecycle; /delete keeps the contact; /profile_viewers plan-gated.
    08-Sep-2026: /onboard passes plan_code to run_background_enhancements.
    15-Sep-2026: Added POST /unverified_provider, GET /unverified_provider_list and POST /approve.
    16-Sep-2026: /unverified_provider mints its own contact and supports edit; nearby push moved to /approve.
    17-Sep-2026: /unverified_provider writes review rows instead of refusing; /approve keyed on providerid.
    18-Sep-2026: /approve cascade demotes on rejection and skips the contact; added FOS proximity gate to /unverified_provider.
    21-Sep-2026: FOS proximity gate behind a flag; own-number captures refused; review-pending providers hidden.
    22-Sep-2026: Edit captures send onboarding WhatsApp; reverted the 21-Sep review exclusion.
    23-Sep-2026: Proximity gate needs a fos role in etown_staff_numbers; source stamped from staff role; onboarding SMS fallback.
    24-Sep-2026: Onboarding WhatsApp also sent to alternatemobile.
    25-Sep-2026: Onboarding SMS fallback also covers WhatsApps later reported 'failed' and numbers known off WhatsApp.
    28-Sep-2026: Added POST /ai_onboarding, reading a business from an image and/or prompt, logged to etown_ai_onboarding; asks for a missing business name.
    29-Sep-2026: Added POST /bubble for prompt/image follow-ups to AI onboarding details, stored per conversation, confirming unfit names; /ai_onboarding stores timed user/assistant rows with conversation and caller ids; /ai_onboarding returns a mobile's existing providers and asks for a missing mobile; /ai_onboarding merges into a picked existing provider; /ai_onboarding and /bubble return latitude and longitude; /ai_onboarding nests mobiles, coordinates and category in Provider_Details; both link a photo to a service; /bubble Provider_Details uses the /ai_onboarding field names; /ai_onboarding always generates beside existing providers and merges similar services; /ai_onboarding merges a mismatched name or address once confirmed; a confirmed name rewrites descriptions; confirmation replies are read by Gemini, taking their mobile as alternate; "onboard as new provider" returns the generated listing alone; /ai_onboarding refuses the caller's own provider; /bubble drops business_image_url.
    30-Sep-2026: /ai_onboarding sets confirmation_required whenever response asks the agent something, caps merged services at 5; /unverified_provider takes interested, mirrored to Dataverse; /bubble keeps the main mobile, refuses a used alternate mobile, renames the business in descriptions; AI onboarding drops Phone_Number; /unverified_provider refuses and /ai_onboarding drops a conflicting alternate mobile; /ai_onboarding skips existing providers once the conversation asks for a new provider; /ai_onboarding and /bubble return Provider_Details.Service_Type.
    01-Oct-2026: /unverified_provider stores marketing_mobilenumber on the provider.
    06-Oct-2026: /unverified_provider stores every create in etown_provider_review, not only duplicates.
    22-Oct-2026: /onboard takes Service_Details in create and edit, storing Service_Type as businesscategory, requires one on create, and stops background service generation; added POST /onboard_services.
"""

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Request, Response, Security, Query, UploadFile, status
from fastapi.responses import JSONResponse
from typing import Any, Dict, Optional, List
from datetime import timedelta, timezone
import asyncio
import re

from geopy.distance import geodesic

from app.etown.provider.provider_utils import (
    PROFILE_VIEWERS_PAGE_SIZE,
    PROVIDER_STATUS_MESSAGES,
    ProviderService,
    ProviderRequestListService,
    RatingService,
    fetch_date_range_summary,
    fetch_profile_viewers,
    analyse_ai_onboarding,
    apply_ai_onboarding_bubble,
    record_ai_onboarding,
    record_ai_onboarding_bubble,
    summarise_provider_status,
    extract_onboard_details,
    send_unverified_provider_nudge,
    send_fos_business_onboarding_email,
)
from app.etown.provider.provider_services_utils import (
    ProviderServiceManagementService,
    check_listing_cap,
    map_parsed_offerings_to_service_details,
    assign_pydantic_offering_ids,
    upload_parsed_business_media,
)
from app.etown.offers.offerings_utils import save_parsed_offerings_to_db
from app.etown.plans.plans_utils import PlanLimitError, premium_display, resolve_plan, resolve_provider_badges
from app.etown.provider.provider_lifecycle_utils import (
    deactivate_provider_in_db,
    reactivate_provider_in_db,
)
from app.etown.provider.provider_review_utils import (
    fetch_provider_review_snapshot,
    find_live_providers_by_mobile,
    record_provider_review,
)
from app.etown.user.account_lifecycle_syncing import sync_provider_lifecycle_to_dataverse
from app.etown.user.user_utils import (
    STAFF_ROLE_FOS,
    check_alternate_mobile_status,
    fetch_phone_contact_ids,
    has_etown_staff_role,
    resolve_capture_source,
)
from app.etown.dataverse_sync.dataverse_sync_utils import refresh_content_vectors, set_provider_verification
from app.etown.dataverse_sync.syncing import (
    sync_provider_approval_to_dataverse,
    sync_provider_verification_to_dataverse,
)
from app.etown.provider.provider_syncing import (
    sync_provider_delete_from_dataverse,
    sync_provider_service_delete_from_dataverse,
    sync_allrequest_rating_to_dataverse,
    sync_rating_to_dataverse,
    sync_provider_edit_to_dataverse,
    sync_provider_profile_update_to_dataverse,
)
from app.schemas.api import (
    ProviderOnboardingRequest,
    ProviderOnboardingResponse,
    OnboardServiceDetailRequest,
    OnboardServicesRequest,
    OnboardServicesResponse,
    UnverifiedProviderOnboardingRequest,
    UnverifiedProviderOnboardingResponse,
    UnverifiedProviderItem,
    UnverifiedProviderListResponse,
    ProviderApprovalRequest,
    ProviderApprovalResponse,
    AddServicesToProvidersRequest,
    AddServicesToProvidersResponse,
    ProviderDeleteRequest,
    ProviderDeleteResponse,
    ProviderLifecycleRequest,
    ProviderLifecycleResponse,
    DeleteServiceRequest,
    DeleteServiceResponse,
    EditServiceRequest,
    EditServiceResponse,
    UnratedProviderItem,
    ProviderRequestListResponse,
    ProviderBookingDetails,
    ProviderServiceDetail,
    ProviderBookingItem,
    ProviderCompletedBookingItem,
    ProviderCompletedListResponse,
    OnboardDetailsResponse,
    AIOnboardingBubbleRequest,
    AIOnboardingBubbleResponse,
    AIOnboardingRequest,
    AIOnboardingResponse,
    AddRatingRequest,
    AddRatingResponse,
    NotifyUnverifiedProviderRequest,
    NotifyUnverifiedProviderResponse,
    EditProviderProfileRequest,
    EditProviderProfileResponse,
    ProviderIdentifiersResponse,
    ProfileViewerItem,
    ProfileViewersResponse,
    ShareProviderRequest,
    ShareProviderResponse,
)
from app.core.config import settings
from app.core.logging import logger
from app.utils.phone import normalize_indian_mobile
from app.utils.ist_clock import now_ist_naive
from app.core.security import verify_token
from app.etown.notifications.triggers import notify_provider_shared
from app.services.api_activity import track_api_call

router = APIRouter()
_PROVIDER_LIFECYCLE_HANDLER = {
    "deactivate": deactivate_provider_in_db,
    "reactivate": reactivate_provider_in_db,
}

_PROVIDER_LIFECYCLE_STATUS = {
    "deactivate": "deactivated",
    "reactivate": "reactivated",
}


def _str_or_none(value) -> Optional[str]:
    """Return str(value), or None when value is None."""
    return str(value) if value is not None else None


def _iso_or_none(value) -> Optional[str]:
    """Return a timestamp as ISO 8601, other values as str, and None as None."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@router.post(
    "/share",
    response_model=ShareProviderResponse,
    status_code=200,
    summary="Share Provider",
    description=(
        "Send a push notification to a list of recipients when a user shares a provider "
        "profile. Notifies every ccuserid-linked device of each recipient with the body "
        "'<sender first name> shared a provider profile'. Migrated from the PowerApps "
        "ShareProviders flow."
    ),
    responses={
        200: {"description": "Share notification dispatched", "model": ShareProviderResponse},
        500: {"description": "Internal server error"},
    },
)
async def share_provider_endpoint(
    request: ShareProviderRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/provider/share: push a shared-provider notification to each recipient.

    Raises:
        HTTPException: 500 when dispatching fails.
    """
    try:
        recipient_ids = [r.userprofileid for r in request.shared_userprofileids if r.userprofileid]
        delivered = await notify_provider_shared(
            providerid=request.providerid,
            shared_userprofile_ids=recipient_ids,
            sender_contactid=request.sender_contactid,
            oauth_token=request.Oauth,
        )
        logger.info(
            f"share_provider - provider={request.providerid} "
            f"recipients={len(recipient_ids)} delivered={delivered}"
        )
        return ShareProviderResponse(
            success=True,
            recipients=len(recipient_ids),
            delivered=delivered,
        )
    except Exception as e:
        logger.error(f"share_provider - failed provider={request.providerid} error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to share provider")


@router.post(
    "/edit_profile",
    response_model=EditProviderProfileResponse,
    status_code=200,
    summary="Edit Provider Profile",
    description=(
        "Update a provider's profile and every service row underneath it. Only the fields "
        "supplied are changed. Migrated from the PowerApps ProviderEditAPI flow, so the field "
        "names match that HTTP trigger (provider_userprofile is accepted for userprofile_id).\n\n"
        "The provider is resolved from contact_id + userprofile_id, then etown_provider, "
        "etown_providerservices and etown_providerservices_copy are written in one statement. "
        "Pass the literal 'Null' for service_image_url or availability to keep the stored value; "
        "any other service_image_url is treated as base64 and uploaded to Azure Blob first.\n\n"
        "A business_name change is also written to etown_feed.servicename on this profile's "
        "own posts - that column is the frozen copy behind ets_servicename on the Dataverse "
        "mirror. The feed API itself reads the business name live off etown_provider, so the "
        "post header changes with this call whether or not the snapshot write succeeds.\n\n"
        "Three background tasks follow: the Dataverse mirror, town reassignment when "
        "addresschange is true, and a content_vector refresh when the edit touched a field "
        "feeding the embedding text."
    ),
    responses={
        200: {
            "description": "Profile update completed",
            "model": EditProviderProfileResponse,
            "content": {"application/json": {"examples": {
                "success": {"value": {"message": "Profile updated successfully"}},
            }}},
        },
        401: {"description": "Missing or invalid bearer token"},
        404: {"description": "No provider matches contact_id + userprofile_id"},
        422: {"description": "No editable field supplied"},
        500: {"description": "Internal server error"},
    },
)
async def edit_provider_profile(
    background_tasks: BackgroundTasks,
    request: EditProviderProfileRequest = Body(...),
    _token: str = Security(verify_token),
) -> EditProviderProfileResponse:
    """Handle POST /api/provider/edit_profile: update a provider and its service rows, then mirror, retown and re-embed.

    Raises:
        HTTPException: 404 when no provider matches the identifiers, 500 on an unexpected error.
    """
    try:
        updates = request.model_dump(
            exclude_unset=True,
            exclude={"contact_id", "userprofile_id", "addresschange"},
        )
        logger.info(
            f"Edit provider profile request | contact_id={request.contact_id} "
            f"userprofile_id={request.userprofile_id} fields={sorted(updates.keys())} "
            f"addresschange={bool(request.addresschange)}"
        )

        message, provider_data = await ProviderService.edit_provider_profile_workflow(
            contact_id=request.contact_id,
            provider_userprofile=request.userprofile_id,
            updates=updates,
        )
        if provider_data is None:
            raise HTTPException(status_code=404, detail=message)

        provider_id = provider_data["providerid"]
        resolved = provider_data["resolved"]

        lat = str(resolved["latitude"]) if resolved.get("latitude") is not None else None
        lon = str(resolved["longitude"]) if resolved.get("longitude") is not None else None
        provider_payload = {
            "ets_businessname": resolved.get("business_name"),
            "ets_name": resolved.get("business_name"),
            "ets_description": resolved.get("description"),
            "ets_businesscategory": resolved.get("business_category"),
            "ets_address": resolved.get("address"),
            "ets_city": resolved.get("city"),
            "ets_townname": resolved.get("townname"),
            "ets_areaname": resolved.get("areaname"),
            "ets_statecode": resolved.get("statecode"),
            "ets_latitude": lat,
            "ets_longitude": lon,
            "ets_mobilenumber": resolved.get("mobilenumber"),
            "ets_email": resolved.get("email"),
            "ets_additionaladdressinfo": resolved.get("additionaladdressinfo"),
            "ets_servicetype": resolved.get("servicetype"),
            "ets_availability": resolved.get("availability"),
            "ets_serviceimageurl": resolved.get("serviceimageurl"),
            "ets_backgroundimage": resolved.get("background_image"),
        }
        service_payload = {
            "ets_businessname": resolved.get("business_name"),
            "ets_name": resolved.get("business_name"),
            "ets_address": resolved.get("address"),
            "ets_city": resolved.get("city"),
            "ets_areaname": resolved.get("areaname"),
            "ets_statecode": resolved.get("statecode"),
            "ets_servicelatitude": lat,
            "ets_servicelongitude": lon,
            "ets_mobile": resolved.get("mobilenumber"),
            "ets_email": resolved.get("email"),
            "ets_additionaladdressinfo": resolved.get("additionaladdressinfo"),
            "ets_availability": resolved.get("availability"),
            "ets_serviceimageurl": resolved.get("serviceimageurl"),
        }
        background_tasks.add_task(
            sync_provider_profile_update_to_dataverse,
            provider_id=provider_id,
            providerservices_ids=provider_data["providerservices_ids"],
            provider_payload=provider_payload,
            service_payload=service_payload,
            provider_allow_null=(
                ["ets_backgroundimage"] if "background_image" in updates else None
            ),
        )

        if request.addresschange:
            background_tasks.add_task(
                ProviderService.reassign_town_after_address_change,
                provider_id=provider_id,
                business_name=resolved.get("business_name"),
                latitude=resolved.get("latitude"),
                longitude=resolved.get("longitude"),
                stored_townname=resolved.get("townname"),
                city=resolved.get("city"),
                areaname=resolved.get("areaname"),
                statecode=resolved.get("statecode"),
                providerservices_ids=provider_data["providerservices_ids"],
            )

        if provider_data["embedding_text_changed"]:
            background_tasks.add_task(
                refresh_content_vectors,
                targets={
                    "etown_provider": [provider_id],
                    "etown_providerservices_copy": provider_data["providerservices_copy_ids"],
                },
            )
        else:
            logger.info(
                "Provider profile edit did not touch any embedding field — content_vector left "
                "as is (providerid=%s)", provider_id,
            )

        return EditProviderProfileResponse(message=message)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            f"edit_provider_profile error | contact_id={request.contact_id} | error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post(
    "/edit_service",
    response_model=EditServiceResponse,
    status_code=200,
    summary="Edit Provider Service",
    description=(
        "Update one of a provider's service rows. Only the fields supplied are changed. "
        "Migrated from the PowerApps ProviderServiceEditAPI flow, so the field names match "
        "that HTTP trigger (providerserviceid, servicename, price_range, image1-image3 are "
        "accepted alongside the in-repo names).\n\n"
        "providerservices_id alone identifies the row; contact_id, provider_id and "
        "userprofile_id are ownership guards, and a row that exists but fails them returns "
        "403 rather than 404. etown_providerservices and etown_providerservices_copy are "
        "written in one statement.\n\n"
        "Image slots use the flow's sentinels: 'Null' keeps the stored image, 'Deleted' clears "
        "it, anything else is base64 and is uploaded to Azure Blob first. is_description=true "
        "regenerates businesscategory / servicecategory / generalcategory from the new "
        "description before the write; otherwise the stored categories are kept.\n\n"
        "Two background tasks follow: the offerings-catalogue convergence plus Dataverse "
        "mirror, and a content_vector refresh when the description changed (the service name "
        "does not feed the copy table's embedding text)."
    ),
    responses={
        200: {
            "description": "Service update completed",
            "model": EditServiceResponse,
            "content": {"application/json": {"examples": {
                "success": {"value": {"message": "Service updated successfully"}},
            }}},
        },
        401: {"description": "Missing or invalid bearer token"},
        403: {"description": "The service exists but is not owned by the supplied identifiers"},
        404: {"description": "No service matches providerservices_id"},
        422: {"description": "No editable field supplied"},
        500: {"description": "Internal server error"},
    },
)
async def edit_provider_service(
    background_tasks: BackgroundTasks,
    request: EditServiceRequest = Body(...),
    _token: str = Security(verify_token),
) -> EditServiceResponse:
    """Handle POST /api/provider/edit_service: update one service row and schedule catalogue, mirror and embedding tasks.

    Raises:
        HTTPException: 403 on an ownership mismatch, 404 when no service matches, 500 otherwise.
    """
    try:
        updates = request.model_dump(
            exclude_unset=True,
            exclude={"contact_id", "provider_id", "providerservices_id", "userprofile_id"},
        )
        logger.info(
            f"Edit service request | contact_id={request.contact_id} "
            f"providerservices_id={request.providerservices_id} fields={sorted(updates.keys())}"
        )

        message, provider, bg_payload, serviceitems = (
            await ProviderServiceManagementService.edit_service_workflow(
                contact_id=request.contact_id,
                provider_id=request.provider_id,
                providerservices_id=request.providerservices_id,
                updates=updates,
                userprofile_id=request.userprofile_id,
            )
        )
        if message == "Unauthorized":
            raise HTTPException(status_code=403, detail="Not permitted to edit this service")
        if provider is None or bg_payload is None:
            raise HTTPException(status_code=404, detail=message)

        background_tasks.add_task(
            ProviderServiceManagementService.run_edit_enhancements,
            provider=provider,
            contact_id=bg_payload["contact_id"],
            bg_payload=bg_payload,
        )

        if request.description is not None and bg_payload["copy_ids"]:
            background_tasks.add_task(
                refresh_content_vectors,
                targets={"etown_providerservices_copy": bg_payload["copy_ids"]},
            )

        return EditServiceResponse(message=message, serviceitems=serviceitems)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            f"edit_provider_service error | providerservices_id={request.providerservices_id} "
            f"| error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc


async def _edit_onboarded_provider(
    provider_data: ProviderOnboardingRequest,
    background_tasks: BackgroundTasks,
    response: Response,
) -> ProviderOnboardingResponse:
    """Apply an edit-mode onboarding payload to an existing provider, schedule mirror and embedding refresh, and return 200.

    Raises:
        HTTPException: 400 bad mobile, 404 unknown provider or contact, 409 alternatemobile taken.
    """
    logger.info(
        "Editing onboarded provider: providerid=%s contact_id=%s business=%s",
        provider_data.providerid, provider_data.contact_id, provider_data.businessname,
    )

    if provider_data.mobilenumber and not await ProviderService.validate_phone_format(provider_data.mobilenumber):
        raise HTTPException(
            status_code=400,
            detail="Invalid mobile number format. Must be 8-15 digits."
        )

    lat: Optional[float] = None
    lng: Optional[float] = None
    if provider_data.latitude:
        try:
            lat = float(provider_data.latitude)
        except (ValueError, TypeError):
            logger.warning("provider edit: latitude %r is not a number; leaving it unset", provider_data.latitude, exc_info=True)
    if provider_data.longitude:
        try:
            lng = float(provider_data.longitude)
        except (ValueError, TypeError):
            logger.warning("provider edit: longitude %r is not a number; leaving it unset", provider_data.longitude, exc_info=True)

    result = await ProviderService.update_provider_onboard_in_db(
        provider_id=provider_data.providerid,
        contact_id=provider_data.contact_id,
        business_name=provider_data.businessname,
        phone_number=provider_data.mobilenumber,
        description=provider_data.description,
        city=provider_data.city,
        latitude=lat,
        longitude=lng,
        additionaladdressinfo=provider_data.additionaladdressinfo,
        area_location=provider_data.area_location,
        state=provider_data.state,
        formatted_address=provider_data.formattedAddress,
        businesslogo=provider_data.businesslogo,
        business_category_input=provider_data.business_category,
        service_category_input=provider_data.service_category,
        servicetype=provider_data.servicetype,
        services=provider_data.services,
        email=provider_data.email,
        website=provider_data.website,
        availability=provider_data.availability,
        alternate_mobile=provider_data.alternatemobile,
    )

    provider_info = result['provider_info']
    contact_info = result['contact_info']
    userprofile_info = result['userprofile_info']
    providerservice_info = result['providerservice_info']
    resolved = result.get('resolved_fields', {})

    provider_payload = {
        "ets_businessname": resolved.get("business_name"),
        "ets_name": resolved.get("business_name"),
        "ets_description": resolved.get("description"),
        "ets_businesscategory": resolved.get("business_category"),
        "ets_city": resolved.get("city"),
        "ets_townname": resolved.get("city"),
        "ets_mobilenumber": resolved.get("mobilenumber"),
        "ets_email": resolved.get("email"),
        "ets_address": resolved.get("address"),
        "ets_areaname": resolved.get("areaname"),
        "ets_statecode": resolved.get("statecode"),
        "ets_latitude": resolved.get("latitude"),
        "ets_longitude": resolved.get("longitude"),
        "ets_additionaladdressinfo": resolved.get("additionaladdressinfo"),
        "ets_servicetype": resolved.get("servicetype"),
        "ets_websiteurl": resolved.get("websiteurl"),
        "ets_availability": resolved.get("availability"),
        "ets_serviceimageurl": resolved.get("serviceimageurl"),
        "ets_providertype": "etown_verified",
        "ets_publicsearchprovider": "verified",
        "ets_source": "Onboard",
        "ets_alternatemobile": provider_info.get("alternatemobile"),
    }
    if result.get('contact_repointed_from'):
        new_contactid = result.get('contactid')
        provider_payload["ets_Contact_id@odata.bind"] = f"/contacts({new_contactid})"
        logger.info(
            "Provider %s moved from contact %s to %s (old contact deleted=%s)",
            provider_info['providerid'], result['contact_repointed_from'],
            new_contactid, result.get('old_contact_deleted'),
        )
    service_payload = {
        "ets_businessname": resolved.get("business_name"),
        "ets_name": resolved.get("business_name"),
        "ets_servicedescription": resolved.get("description"),
        "ets_businesscategory": resolved.get("business_category"),
        "ets_servicecategory": resolved.get("service_category"),
        "ets_services": resolved.get("services"),
        "ets_city": resolved.get("city"),
        "ets_mobile": resolved.get("mobilenumber"),
        "ets_businessnumber": resolved.get("mobilenumber"),
        "ets_email": resolved.get("email"),
        "ets_address": resolved.get("address"),
        "ets_areaname": resolved.get("areaname"),
        "ets_statecode": resolved.get("statecode"),
        "ets_servicelatitude": resolved.get("latitude"),
        "ets_servicelongitude": resolved.get("longitude"),
        "ets_additionaladdressinfo": resolved.get("additionaladdressinfo"),
        "ets_websiteurl": resolved.get("websiteurl"),
        "ets_availability": resolved.get("availability"),
        "ets_availability1": resolved.get("availability"),
        "ets_serviceimageurl": resolved.get("serviceimageurl"),
        "ets_providertype": "etown_verified",
        "ets_publicsearchprovider": "verified",
        "ets_source": "Onboard",
    }
    if result.get('contact_repointed_from'):
        service_payload["ets_ContactId@odata.bind"] = f"/contacts({result.get('contactid')})"

    contact_payload = {
        "mobilephone": resolved.get("mobilenumber"),
        "emailaddress1": resolved.get("email"),
        "address1_city": resolved.get("city"),
        "ets_verified": True,
        "ets_alternatemobile": contact_info.get("alternatemobile"),
    }
    userprofile_payload = {
        "ets_mobilenumber": resolved.get("mobilenumber"),
        "ets_verified": True,
    }
    if result.get('contact_repointed_from'):
        userprofile_payload["ets_Contact@odata.bind"] = f"/contacts({result.get('contactid')})"

    background_tasks.add_task(
        sync_provider_edit_to_dataverse,
        provider_id=provider_info['providerid'],
        providerservices_ids=result.get('providerservices_ids', []),
        contact_id=contact_info['contactid'],
        userprofile_id=userprofile_info.get('userprofileid') or None,
        contact_payload=contact_payload,
        userprofile_payload=userprofile_payload,
        provider_payload=provider_payload,
        service_payload=service_payload,
        deleted_contact_id=(
            result.get('contact_repointed_from') if result.get('old_contact_deleted') else None
        ),
    )

    if result.get('embedding_text_changed'):
        embedding_targets = {
            "etown_contact": [contact_info.get('contactid')],
            "etown_provider": [provider_info['providerid']],
            "etown_providerservices_copy": result.get('providerservices_copy_ids', []),
        }
        background_tasks.add_task(
            refresh_content_vectors,
            targets={k: [i for i in v if i] for k, v in embedding_targets.items()},
        )
    else:
        logger.info(
            "Provider edit did not touch any embedding field — content_vector left as is "
            "(providerid=%s)", provider_info['providerid'],
        )

    logger.info(
        "Provider edit completed: providerid=%s business='%s'",
        provider_info['providerid'], provider_info['businessname'],
    )

    add_services_message = await _add_onboard_service_details(
        provider_data,
        provider_id=provider_info['providerid'],
        contact_id=provider_info.get('contactid') or contact_info.get('contactid'),
        userprofile_id=provider_info.get('userprofileid') or userprofile_info.get('userprofileid'),
        background_tasks=background_tasks,
    )

    response.status_code = status.HTTP_200_OK
    return ProviderOnboardingResponse(
        contact_info=contact_info,
        userprofile_info=userprofile_info,
        provider_info=provider_info,
        providerservice_info=providerservice_info,
        ai_enhancements=result['ai_enhancements'],
        add_services_message=add_services_message,
    )


async def _add_onboard_service_details(
    provider_data: ProviderOnboardingRequest,
    provider_id: str,
    contact_id: str,
    userprofile_id: str,
    background_tasks: BackgroundTasks,
) -> Optional[str]:
    """Insert or update the request's Service_Details as verified services and queue their category, embedding and sync.

    Args:
        provider_data: The onboarding request carrying Service_Details.
        provider_id: Provider receiving the services.
        contact_id: Contact owning the provider.
        userprofile_id: Userprofile owning the provider.
        background_tasks: Request task queue for the enhancement pass.

    Returns:
        The workflow's outcome message, or None when the request carried no named service.
    """
    servicedetails = [
        OnboardServiceDetailRequest(
            service_name=service.Service_Name.strip(),
            description=service.Service_Description,
            quantity=service.Service_Quantity,
            pricerange=service.Service_Price or service.Service_Expected_Price,
            serviceimage1=service.serviceimage1,
            serviceimage2=service.serviceimage2,
            serviceimage3=service.serviceimage3,
            service_type={"product": "product", "service": "service"}.get(
                (service.Service_Type or '').strip().lower()
            ),
        )
        for service in provider_data.Service_Details
        if (service.Service_Name or '').strip()
    ]
    if not servicedetails:
        return None

    try:
        message, enhancements, provider = await ProviderServiceManagementService.add_services_to_provider_workflow(
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            provider_id=provider_id,
            servicedetails=servicedetails,
        )
    except PlanLimitError as e:
        logger.info(f"Provider {provider_id} Service_Details refused by plan cap: {e}")
        return f"Provider saved, but its services were not added: {e}"
    except Exception as e:
        logger.error(f"Provider {provider_id} saved, but adding its Service_Details failed: {e}", exc_info=True)
        return f"Provider saved, but adding its services failed: {e}"

    if enhancements and provider:
        background_tasks.add_task(
            ProviderServiceManagementService.run_background_enhancements,
            provider=provider,
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            service_enhancements=enhancements,
        )

    logger.info(f"Provider {provider_id} Service_Details: {message} ({len(enhancements)}/{len(servicedetails)})")
    return message


@router.post(
    "/onboard",
    response_model=ProviderOnboardingResponse,
    status_code=201,
    summary="Onboard New Provider (or edit an existing one)",
    description=(
        "action_mode='create' (default) registers a new provider business with complete profile "
        "information including Contact, UserProfile, Provider, and ProviderService records. "
        "Create requires 'Service_Details' with at least one named service. In both modes each "
        "Service_Details entry is written to etown_providerservices and its _copy table (an "
        "existing Service_Name is updated), then its category, content_vector and Dataverse "
        "sync run in the background; add_services_message reports the outcome. No services are "
        "generated in the background (POST /provider/onboard_services previews AI ones). "
        "action_mode='edit' updates the provider named by providerid (mandatory in that mode) "
        "with only the fields supplied in the body and responds with 200; a contact_id "
        "different from the provider's stored contact moves the listing onto that contact."
    ),
    responses={
        200: {
            "description": "Provider updated successfully (action_mode='edit')",
            "model": ProviderOnboardingResponse
        },
        201: {
            "description": "Provider onboarded successfully",
            "model": ProviderOnboardingResponse
        },
        404: {
            "description": "Provider to edit was not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Provider not found"}
                }
            }
        },
        400: {
            "description": "Invalid input data",
            "content": {
                "application/json": {
                    "example": {"detail": "Phone number must be between 8-15 digits"}
                }
            }
        },
        402: {
            "description": (
                "The contact has already onboarded every provider its subscription pack "
                "allows (provider_onboard). action_mode='create' with a contact_id only."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "Your Silver plan covers 2 business(es). You have already "
                            "onboarded 2, so this one cannot be added."
                        )
                    }
                }
            }
        },
        409: {
            "description": (
                "Conflict — action_mode='create' only: phone registered to another account, "
                "or business name + phone already exists. action_mode='edit' never conflicts "
                "on the mobile number."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "phone_conflict": {
                            "summary": "Phone belongs to another account (create only)",
                            "value": {"detail": "This mobile number already has a member account in etown"}
                        },
                        "duplicate_provider": {
                            "summary": "Same business name and phone already registered",
                            "value": {"detail": "A provider with this business name and phone number is already registered"}
                        }
                    }
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Provider onboarding failed due to internal server error"}
                }
            }
        }
    }
)
async def onboard_provider(
    provider_data: ProviderOnboardingRequest,
    background_tasks: BackgroundTasks,
    response: Response,
) -> ProviderOnboardingResponse:
    """Handle POST /api/provider/onboard: create a provider with background enhancements, or delegate edit mode to the edit path.

    Raises:
        HTTPException: 400 bad input, 402 plan cap, 404 edit target missing, 409 conflicts, 500 otherwise.
    """
    try:
        if provider_data.action_mode == "edit":
            return await _edit_onboarded_provider(provider_data, background_tasks, response)

        logger.info(f"Starting provider onboarding for business: {provider_data.businessname}")

        first_service_name = next(
            (service.Service_Name.strip() for service in provider_data.Service_Details if (service.Service_Name or '').strip()),
            None,
        )
        onboard_services = first_service_name or provider_data.services

        if not await ProviderService.validate_phone_format(provider_data.mobilenumber):
            raise HTTPException(
                status_code=400,
                detail="Invalid mobile number format. Must be 8-15 digits."
            )
        lat: Optional[float] = None
        lng: Optional[float] = None
        if provider_data.latitude:
            try:
                lat = float(provider_data.latitude)
            except (ValueError, TypeError):
                logger.warning("provider onboard: latitude %r is not a number; leaving it unset", provider_data.latitude, exc_info=True)
        if provider_data.longitude:
            try:
                lng = float(provider_data.longitude)
            except (ValueError, TypeError):
                logger.warning("provider onboard: longitude %r is not a number; leaving it unset", provider_data.longitude, exc_info=True)

        result = await ProviderService.onboard_provider_in_db(
            business_name=provider_data.businessname,
            phone_number=provider_data.mobilenumber,
            description=provider_data.description,
            city=provider_data.city,
            latitude=lat,
            longitude=lng,
            additionaladdressinfo=provider_data.additionaladdressinfo,
            area_location=provider_data.area_location,
            state=provider_data.state,
            formatted_address=provider_data.formattedAddress,
            businesslogo=provider_data.businesslogo,
            contact_id=provider_data.contact_id,
            business_category_input=provider_data.business_category,
            service_category_input=provider_data.service_category,
            servicetype=provider_data.servicetype,
            email=provider_data.email,
            website=provider_data.website,
            availability=provider_data.availability,
            services=onboard_services,
            alternate_mobile=provider_data.alternatemobile,
        )

        logger.info(f"Provider onboarding completed for business: {provider_data.businessname}, provider_id: {result['provider_info']['providerid']}")
        providerservice_info = result.get('providerservice_info', {})
        contact_info = result.get('contact_info', {})
        userprofile_info = result.get('userprofile_info', {})
        background_tasks.add_task(
            ProviderService.run_background_enhancements,
            provider_id=result['provider_info']['providerid'],
            providerservice_copy_id=providerservice_info.get('providerservicesid'),
            providerservice_orig_id=providerservice_info.get('providerservicesid_orig'),
            contact_id=contact_info.get('contactid'),
            userprofile_id=userprofile_info.get('userprofileid'),
            business_name=provider_data.businessname,
            description=provider_data.description or '',
            business_category_input=provider_data.business_category,
            service_category_input=provider_data.service_category,
            latitude=lat,
            longitude=lng,
            businesslogo=provider_data.businesslogo,
            phone_number=provider_data.mobilenumber,
            email=contact_info.get('email'),
            city=provider_data.city,
            first_name=contact_info.get('firstname'),
            formatted_address=provider_data.formattedAddress,
            area_location=provider_data.area_location,
            state=provider_data.state,
            additionaladdressinfo=provider_data.additionaladdressinfo,
            servicetype=provider_data.servicetype,
            website=provider_data.website,
            services_input=onboard_services,
            plan_code=result['provider_info'].get('plan_code'),
            generate_services=False,
        )

        add_services_message = await _add_onboard_service_details(
            provider_data,
            provider_id=result['provider_info']['providerid'],
            contact_id=contact_info.get('contactid'),
            userprofile_id=userprofile_info.get('userprofileid'),
            background_tasks=background_tasks,
        )

        response = ProviderOnboardingResponse(
            contact_info=result['contact_info'],
            userprofile_info=result['userprofile_info'],
            provider_info=result['provider_info'],
            providerservice_info=result['providerservice_info'],
            ai_enhancements=result['ai_enhancements'],
            add_services_message=add_services_message,
        )

        from app.core.config import settings as _settings
        if _settings.NOTIFICATIONS_GOVERNANCE_ENABLED:
            from app.etown.notifications.triggers import notify_new_provider_nearby
            background_tasks.add_task(
                notify_new_provider_nearby,
                provider_upid=userprofile_info.get('userprofileid', ''),
                businessname=provider_data.businessname,
                townname=provider_data.city or '',
                contactid=contact_info.get('contactid', ''),
            )

        return response

    except HTTPException:
        raise
    except PlanLimitError as e:
        logger.info(f"Provider onboarding refused by plan cap: {e}")
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error during provider onboarding: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Provider onboarding failed due to internal server error"
        )


@router.post(
    "/onboard_services",
    response_model=OnboardServicesResponse,
    status_code=200,
    summary="Generate Services for Provider Onboarding",
    description=(
        "Runs the AI service generation POST /provider/onboard used to run in the background, "
        "from the business name, description and categories in the body, and returns "
        "providerservice_info and ai_enhancements in the /onboard response's format. Nothing "
        "is stored and no provider or service is created."
    ),
)
async def generate_onboard_services(
    request_data: OnboardServicesRequest,
    _token: str = Security(verify_token),
) -> OnboardServicesResponse:
    """Handle POST /api/provider/onboard_services: return AI-generated services for an onboarding without storing them.

    Raises:
        HTTPException: 500 when generation fails.
    """
    try:
        result = await ProviderService.generate_onboard_services(
            business_name=request_data.businessname,
            description=request_data.description,
            business_category_input=request_data.business_category,
            service_category_input=request_data.service_category,
        )
        return OnboardServicesResponse(**result)
    except Exception as e:
        logger.error(f"Service generation failed for business {request_data.businessname}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Service generation failed due to internal server error"
        )


def _parse_coordinate(value: Optional[str], low: float, high: float) -> Optional[float]:
    """Parse a string coordinate, returning None when blank, non-numeric or outside [low, high]."""
    if value is None or not str(value).strip():
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not low <= parsed <= high:
        return None
    return parsed


async def _enforce_fos_proximity(provider_data: UnverifiedProviderOnboardingRequest) -> None:
    """Refuse a rostered FOS agent's capture made too far from the business, when the proximity check is enabled.

    Raises:
        HTTPException: 400 when a coordinate is missing or invalid, or the agent is beyond the limit.
    """
    if not settings.FOS_ONBOARD_PROXIMITY_CHECK_ENABLED:
        logger.info(
            "Unverified provider %s: FOS proximity check is disabled "
            "(FOS_ONBOARD_PROXIMITY_CHECK_ENABLED), accepting '%s' wherever it "
            "was captured",
            provider_data.action_mode, provider_data.businessname,
        )
        return

    if not await has_etown_staff_role(provider_data.marketing_mobilenumber, STAFF_ROLE_FOS):
        logger.info(
            "Unverified provider %s: marketing_mobilenumber %s is not an active role='%s' "
            "number in etown_staff_numbers, so the proximity check does not apply - "
            "accepting '%s' wherever it was captured",
            provider_data.action_mode, provider_data.marketing_mobilenumber or "(none sent)",
            STAFF_ROLE_FOS, provider_data.businessname,
        )
        return

    fos_lat = _parse_coordinate(provider_data.FOSlatitude, -90.0, 90.0)
    fos_lng = _parse_coordinate(provider_data.FOSlongitude, -180.0, 180.0)
    if fos_lat is None or fos_lng is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "FOSlatitude and FOSlongitude are required and must be valid coordinates "
                "(latitude -90..90, longitude -180..180). They are the capturing device's own "
                "position, checked against the business location before anything is saved."
            ),
        )

    business_lat = _parse_coordinate(provider_data.latitude, -90.0, 90.0)
    business_lng = _parse_coordinate(provider_data.longitude, -180.0, 180.0)
    if business_lat is None or business_lng is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "latitude and longitude are required and must be valid coordinates: without "
                "the business location there is nothing to measure FOSlatitude/FOSlongitude "
                "against."
            ),
        )

    max_distance_km = settings.FOS_ONBOARD_MAX_DISTANCE_KM
    distance_km = geodesic((fos_lat, fos_lng), (business_lat, business_lng)).km
    if distance_km > max_distance_km:
        logger.info(
            "Unverified provider %s refused for '%s' on mobile %s: the capturing device of "
            "FOS agent %s is %.2f km from the business location, over the %s km limit - "
            "nothing was written",
            provider_data.action_mode, provider_data.businessname, provider_data.mobilenumber,
            provider_data.marketing_mobilenumber, distance_km, max_distance_km,
        )
        raise HTTPException(status_code=400, detail=settings.FOS_ONBOARD_TOO_FAR_MESSAGE)

    logger.info(
        "Unverified provider %s proximity check passed for '%s' captured by FOS agent %s: "
        "%.3f km from the business location",
        provider_data.action_mode, provider_data.businessname,
        provider_data.marketing_mobilenumber, distance_km,
    )


def _normalize_contact_id(value: Optional[str]) -> Optional[str]:
    """Return a contact UUID lowercased with braces stripped, or None when blank."""
    text = str(value or "").strip().strip("{}").lower()
    return text or None


async def _contact_ids_on_mobile(mobilenumber: Optional[str]) -> set:
    """Return the normalized ids of contacts holding a mobile number, looked up as sent and as ten national digits."""
    raw = str(mobilenumber or "").strip()
    if not raw:
        return set()

    digits = re.sub(r"\D+", "", raw)
    national = normalize_indian_mobile(raw) or (digits[-10:] if len(digits) >= 10 else None)
    keys = [key for key in dict.fromkeys([raw, national]) if key]

    owners = set()
    for key in keys:
        for contactid in await fetch_phone_contact_ids(key):
            normalized = _normalize_contact_id(contactid)
            if normalized:
                owners.add(normalized)
    return owners


async def _reject_fos_own_business(provider_data: UnverifiedProviderOnboardingRequest) -> None:
    """Refuse a capture whose business mobile belongs to the capturing agent's own contact.

    Raises:
        HTTPException: 400 with the own-number message when the number resolves to marketing_contactid.
    """
    agent_contactid = _normalize_contact_id(provider_data.marketing_contactid)
    if not agent_contactid:
        return

    if agent_contactid not in await _contact_ids_on_mobile(provider_data.mobilenumber):
        return

    logger.info(
        "Unverified provider %s refused for '%s': mobile %s belongs to the capturing agent "
        "(marketing_contactid %s) - a field agent cannot onboard their own business here, and "
        "nothing was written",
        provider_data.action_mode, provider_data.businessname, provider_data.mobilenumber,
        provider_data.marketing_contactid,
    )
    raise HTTPException(status_code=400, detail=settings.FOS_ONBOARD_OWN_NUMBER_MESSAGE)


_ALTERNATE_MOBILE_CONFLICTS = {
    "same_as_original": "is the same as the main mobile number",
    "separate_account": "already has a separate Etown account",
    "linked_to_another": "is already linked with another mobile number",
}


async def _alternate_mobile_conflict(mobilenumber: Optional[str], alternatemobile: Optional[str]) -> Optional[str]:
    """Classify an alternate mobile as POST /users/check_alternate_mobile does, returning its conflict status or None when usable.

    Args:
        mobilenumber: Main mobile number, compared on its last 10 digits.
        alternatemobile: Alternate mobile number, compared on its last 10 digits.

    Returns:
        'same_as_original', 'separate_account' or 'linked_to_another', or None when either number is not 10 digits or there is no conflict.
    """
    original = re.sub(r"\D", "", mobilenumber or "")[-10:]
    alternate = re.sub(r"\D", "", alternatemobile or "")[-10:]
    if len(original) != 10 or len(alternate) != 10:
        return None
    if original == alternate:
        return "same_as_original"
    status = await check_alternate_mobile_status(original, alternate)
    return None if status == "no_record" else status


def _first_service_name(provider_data: UnverifiedProviderOnboardingRequest) -> Optional[str]:
    """Return the first non-blank service name in the request's servicedetails, or None."""
    for service in provider_data.servicedetails:
        name = (service.service_name or "").strip()
        if name:
            return name
    return None


async def _add_unverified_provider_services(
    provider_data: UnverifiedProviderOnboardingRequest,
    result: dict,
    background_tasks: BackgroundTasks,
    source: str,
) -> str:
    """Insert the request's services against the newly created unverified provider and queue their AI enhancement.

    Args:
        provider_data: The onboarding request carrying servicedetails.
        result: onboard_unverified_provider_in_db() output, supplying the generated ids.
        background_tasks: Request task queue for the enhancement pass.
        source: Capture source stamped on the service rows.

    Returns:
        The workflow's outcome message.
    """
    contact_info = result.get('contact_info', {})
    userprofile_info = result.get('userprofile_info', {})
    provider_id = result['provider_info']['providerid']
    contact_id = contact_info.get('contactid')
    userprofile_id = userprofile_info.get('userprofileid')

    servicedetails = provider_data.servicedetails
    logger.info(
        f"Unverified add services | provider_id={provider_id} "
        f"contact_id={contact_id} count={len(servicedetails)}"
    )

    message, enhancements, provider = await ProviderServiceManagementService.add_services_to_unverified_provider_workflow(
        contact_id=contact_id,
        userprofile_id=userprofile_id,
        provider_id=provider_id,
        servicedetails=servicedetails,
        source=source,
    )

    if enhancements and provider:
        background_tasks.add_task(
            ProviderServiceManagementService.run_background_enhancements,
            provider=provider,
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            service_enhancements=enhancements,
        )

    logger.info(f"Unverified add services completed | provider_id={provider_id} message='{message}'")
    return message


async def _record_create_review(
    provider_data: UnverifiedProviderOnboardingRequest,
    matched_providers: List[Dict[str, Any]],
    created_provider_id: Optional[str],
    background_tasks: Optional[BackgroundTasks],
) -> Optional[str]:
    """Store a create-mode body in etown_provider_review and return its reviewid, or None if the insert fails.

    Args:
        provider_data: The request body to store.
        matched_providers: Live providers already on this mobile number, newest first; empty for a first account.
        created_provider_id: Provider this call onboarded, or None when it was refused.
        background_tasks: Queue for the support mail, or None to send it inline on refusal.

    Returns:
        The reviewid written, or None when the insert failed.
    """
    try:
        return await record_provider_review(
            provider_data,
            provider_id=created_provider_id,
            matched_providers=matched_providers,
            background_tasks=background_tasks,
        )
    except Exception as e:
        logger.error(
            "Unverified provider onboard on mobile number %s could not be recorded "
            "in etown_provider_review: %s", provider_data.mobilenumber, e, exc_info=True,
        )
        return None


async def _send_fos_business_onboarding_whatsapp_or_sms(
    mobile_number: str,
    provider_name: str,
    provider_id: str,
) -> None:
    """Send the FOS onboarding WhatsApp, sending the SMS instead when off WhatsApp or rejected, and watching accepted sends."""
    from app.etown.provider.provider_utils import is_provider_mobile_known_off_whatsapp
    from app.utils.whatsapp import send_fos_business_onboarding_whatsapp_notification
    from app.utils.sms import send_fos_business_onboarding_sms
    if await is_provider_mobile_known_off_whatsapp(mobile_number):
        logger.info("FOS onboarding: %s is marked not on WhatsApp; sending the SMS instead", mobile_number)
        await send_fos_business_onboarding_sms(mobile_number=mobile_number, businessname=provider_name)
        return
    accepted, wamid = await send_fos_business_onboarding_whatsapp_notification(
        mobile_number=mobile_number,
        provider_name=provider_name,
        provider_id=provider_id,
    )
    if not accepted:
        logger.info("FOS onboarding WhatsApp not delivered to %s; sending the SMS instead", mobile_number)
        await send_fos_business_onboarding_sms(mobile_number=mobile_number, businessname=provider_name)
        return
    if wamid:
        task = asyncio.create_task(_sms_if_whatsapp_fails(wamid, mobile_number, provider_name))
        _fos_whatsapp_watch_tasks.add(task)
        task.add_done_callback(_fos_whatsapp_watch_tasks.discard)


_fos_whatsapp_watch_tasks: set = set()
_FOS_WHATSAPP_POLL_SECONDS = 5.0


async def _sms_if_whatsapp_fails(wamid: str, mobile_number: str, provider_name: str) -> None:
    """Poll a WhatsApp's delivery status until the wait expires and send the FOS SMS if it failed; never raises."""
    from app.etown.notifications.whatsapp_status import fetch_delivery_outcome
    from app.utils.sms import send_fos_business_onboarding_sms
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.FOS_ONBOARDING_WHATSAPP_STATUS_WAIT_SECONDS
        while loop.time() < deadline:
            await asyncio.sleep(_FOS_WHATSAPP_POLL_SECONDS)
            outcome = await fetch_delivery_outcome(wamid)
            if outcome == "delivered":
                logger.info("FOS onboarding WhatsApp delivered | wamid=%s mobile=%s", wamid, mobile_number)
                return
            if outcome == "failed":
                logger.info(
                    "FOS onboarding WhatsApp failed after acceptance | wamid=%s mobile=%s; sending the SMS instead",
                    wamid, mobile_number,
                )
                await send_fos_business_onboarding_sms(mobile_number=mobile_number, businessname=provider_name)
                return
        logger.info(
            "FOS onboarding WhatsApp: no delivered/failed status within %ss | wamid=%s mobile=%s; no SMS sent",
            settings.FOS_ONBOARDING_WHATSAPP_STATUS_WAIT_SECONDS, wamid, mobile_number,
        )
    except Exception as exc:
        logger.error("FOS onboarding WhatsApp watch failed | wamid=%s mobile=%s: %s", wamid, mobile_number, exc, exc_info=True)


def _queue_fos_business_onboarding_whatsapp(
    provider_data: UnverifiedProviderOnboardingRequest,
    provider_id: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Queue the FOS onboarding WhatsApp-or-SMS send, with a readable business code, to the mobile and any alternate."""
    business_code = "-".join(
        re.sub(r"[^A-Za-z0-9]+", " ", provider_data.businessname or "").upper().split()[:2]
    ) or "ETOWN"
    readable_id = f"{business_code}-{str(provider_id).replace('-', '')[-4:].upper()}"
    recipients = [provider_data.mobilenumber]
    if provider_data.alternatemobile:
        recipients.append(provider_data.alternatemobile)
    for mobile_number in recipients:
        background_tasks.add_task(
            _send_fos_business_onboarding_whatsapp_or_sms,
            mobile_number=mobile_number,
            provider_name=provider_data.businessname,
            provider_id=readable_id,
        )


async def _edit_unverified_provider(
    provider_data: UnverifiedProviderOnboardingRequest,
    background_tasks: BackgroundTasks,
    response: Response,
) -> UnverifiedProviderOnboardingResponse:
    """Store an edit-mode capture in etown_provider_review without changing live rows, and return the unchanged listing with 200.

    Raises:
        HTTPException: 400 bad mobile, 404 when no live provider holds it or edit_providerid matches none.
    """
    logger.info(
        "Unverified provider edit received for review: business=%s mobile=%s marketing_contactid=%s",
        provider_data.businessname, provider_data.mobilenumber, provider_data.marketing_contactid,
    )

    if not await ProviderService.validate_phone_format(provider_data.mobilenumber):
        raise HTTPException(
            status_code=400,
            detail="Invalid mobile number format. Must be 8-15 digits."
        )

    matched = await find_live_providers_by_mobile(provider_data.mobilenumber)
    if not matched:
        raise HTTPException(
            status_code=404,
            detail=(
                "No provider is registered on this mobile number, so there is no listing to "
                "propose an edit against. Onboard it with action_mode='create' first."
            ),
        )

    requested_provider_id = (provider_data.edit_providerid or "").strip()
    if requested_provider_id:
        wanted = requested_provider_id.lower()
        target = next(
            (row for row in matched if str(row["providerid"]).strip().lower() == wanted),
            None,
        )
        if target is None:
            logger.info(
                "Unverified provider edit: edit_providerid %s holds no live provider on mobile "
                "%s (that number has %d: %s)",
                requested_provider_id, provider_data.mobilenumber, len(matched),
                ", ".join(str(row["providerid"]) for row in matched),
            )
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No live provider with providerid '{requested_provider_id}' is registered "
                    "on this mobile number. edit_providerid must name one of the listings the "
                    "number carries."
                ),
            )
        matched = [target] + [row for row in matched if row is not target]
    elif len(matched) > 1:
        logger.info(
            "Unverified provider edit on mobile %s matches %d live providers (%s) and the body "
            "sent no edit_providerid — the newest is taken and filed as the review row's "
            "providerid; nothing is being written to a provider row, so the ambiguity is the "
            "approver's to settle",
            provider_data.mobilenumber, len(matched),
            ", ".join(f"'{row['businessname']}'" for row in matched),
        )

    target_provider_id = str(matched[0]["providerid"])

    snapshot = await fetch_provider_review_snapshot(target_provider_id)
    if not snapshot:
        logger.warning(
            "Unverified provider edit: provider %s on mobile %s vanished between the lookup and "
            "the read-back — nothing stored", target_provider_id, provider_data.mobilenumber,
        )
        raise HTTPException(
            status_code=404,
            detail=(
                "No provider is registered on this mobile number, so there is no listing to "
                "propose an edit against. Onboard it with action_mode='create' first."
            ),
        )

    review_id = await record_provider_review(
        provider_data,
        provider_id=target_provider_id,
        matched_providers=matched,
        background_tasks=background_tasks,
    )

    logger.info(
        "Unverified provider edit stored for review: reviewid=%s providerid=%s business='%s' services=%d",
        review_id, target_provider_id, provider_data.businessname, len(provider_data.servicedetails),
    )

    _queue_fos_business_onboarding_whatsapp(provider_data, target_provider_id, background_tasks)

    response.status_code = status.HTTP_200_OK
    return UnverifiedProviderOnboardingResponse(
        contact_info=snapshot['contact_info'],
        userprofile_info=snapshot['userprofile_info'],
        provider_info=snapshot['provider_info'],
        providerservice_info=snapshot['providerservice_info'],
        ai_enhancements=snapshot['ai_enhancements'],
        add_services_message=(
            f"Edit stored for review (reviewid={review_id}) with "
            f"{len(provider_data.servicedetails)} service(s). The live listing was not changed."
        ),
    )


@router.post(
    "/unverified_provider",
    response_model=UnverifiedProviderOnboardingResponse,
    status_code=201,
    summary="Onboard New Unverified Provider (with its services), or edit one",
    description=(
        "action_mode='create' (the default) registers a new provider exactly as POST /provider/onboard does — the same Contact, "
        "UserProfile, Provider and ProviderService records and "
        "the same background AI enhancement, geocoding and Dataverse sync — except for two things. "
        "The rows are written as non-verified: providertype='etown_nonverified', "
        "publicsearchprovider='non-verified', and verified = FALSE on the contact and userprofile. "
        "And NOTHING is refused: there is no duplicate check on businessname + mobilenumber, no "
        "check that the mobilenumber is free of a member account, and no subscription-pack cap on "
        "how many businesses a contact may onboard. A field agent cannot act on a 409 or a 402, so "
        "a capture that would have hit one of those is written and flagged for review instead. "
        "(A mobilenumber belonging to a live member account gets its own new contact rather than "
        "reusing the member's, so the capture never touches that account.) "
        "The body also accepts a 'servicedetails' list — a service name, images, price range, "
        "description and quantity each, no offering tree — and runs POST /provider/add_services' "
        "workflow against the provider it just created, so one call onboards the business "
        "and stocks its services. A create carries no providerid, userprofileid or contactid — "
        "it mints them all; its only id is 'marketing_contactid', the field agent capturing "
        "the listing, beside 'marketing_mobilenumber', that same agent's own mobile number. "
        "(An edit may send 'edit_providerid' to pick which listing it is about; that "
        "selects an existing row rather than creating one.) Those services are taken verbatim from the body: none are generated, and N "
        "services in means exactly N etown_providerservices rows out, non-verified and with no "
        "listing cap. Omit 'servicedetails' and the AI catalogue runs as it does on /onboard. "
        "Unlike /onboard, no NEW_PROVIDER_NEARBY push goes out here — a capture is unverified and "
        "may still be rejected, so POST /provider/approve sends that announcement once approve = "
        "Where the capture was taken is checked for rostered field agents only. "
        "'FOSlatitude' and 'FOSlongitude' carry the capturing device's own position; they are "
        "accepted, never stored, and OPTIONAL by default, as are 'latitude'/'longitude'. They "
        "become required for one caller in particular: when 'marketing_mobilenumber' holds an "
        "active role='fos' row in etown_staff_numbers, all four coordinates are mandatory and "
        "a capture taken more than settings.FOS_ONBOARD_MAX_DISTANCE_KM (env "
        "FOS_ONBOARD_MAX_DISTANCE_KM, 1 km by default) from the business is refused with 400 "
        "and the message \"Please go to provider's location to onboard\", before anything at "
        "all is written. Env FOS_ONBOARD_PROXIMITY_CHECK_ENABLED is the master switch over "
        "that rule: with it false nobody is checked, whatever the roster says. Send no "
        "'marketing_mobilenumber', or one nobody has rostered as a field agent, and the "
        "coordinates stay optional and the capture is accepted wherever it was taken. The "
        "roster is read live, so an agent added or deactivated in etown_staff_numbers is "
        "gated, or stops being gated, on their next request. "
        "The one thing this endpoint does refuse is a field agent capturing their own "
        "business: when 'mobilenumber' already belongs to the contact named by "
        "'marketing_contactid' - as their mobile or as a proven alternate - the request is "
        "400 and nothing is written, in create and edit mode alike. That business is "
        "registered through POST /provider/onboard like any other self-service listing. "
        "An 'alternatemobile' is checked as in POST /users/check_alternate_mobile: when it is the "
        "same as 'mobilenumber', already has a separate account, or is linked with another mobile "
        "number, the request is 400 and nothing is written, in create and edit mode alike. "
        "Every create is ALSO stored in etown_provider_review, so the new provider stays out "
        "of search and listings until that review row is approved, first business on the number "
        "or not. When a live provider already carries the body's 'mobilenumber', the onboard "
        "still goes ahead exactly as described and the row also lets the back office compare the "
        "new capture against the listing already on that number. Every review row "
        "written, under either flag, is emailed to the support inbox with the number, the flag, "
        "the submitted details and the listings already on that number. "
        "action_mode='edit' proposes a correction rather than applying one: 'mobilenumber' names "
        "the listing the edit is about — the only handle there is, no ids being accepted — and the "
        "whole body, 'servicedetails' included, is stored in etown_provider_review. "
        "NOTHING is written to the live tables: the provider, its services, its "
        "contact and its user profile all keep the values they had, no Dataverse mirror runs, and "
        "the response reports the listing as it still stands, with add_services_message carrying "
        "the reviewid. At least one live provider must hold the number (none is a 404); several no "
        "longer conflict, since no provider row is being written — the newest is recorded on the "
        "review row along with the match count. Send 'edit_providerid' to say which listing on "
        "that number the edit is about: it must be a live provider carrying the same "
        "mobilenumber, so a wrong id is a 404 rather than an edit aimed at another business, and "
        "omitting it keeps the newest-match behaviour. The listing stays non-verified in both "
        "modes — POST /provider/approve is what promotes it — and an edit returns 200 rather "
        "than 201."
    ),
    responses={
        200: {
            "description": (
                "action_mode='edit' — the edit was stored in etown_provider_review. "
                "The body carries the listing's UNCHANGED stored state; add_services_message "
                "names the reviewid."
            ),
            "model": UnverifiedProviderOnboardingResponse
        },
        201: {
            "description": "Unverified provider onboarded successfully",
            "model": ProviderOnboardingResponse
        },
        400: {
            "description": (
                "Invalid input data, a mobilenumber belonging to the capturing field agent "
                "themselves, an alternatemobile that is the mobilenumber, has a separate account or "
                "is linked with another number, or — only for a capture whose marketing_mobilenumber is a "
                "rostered role='fos' agent, and only while "
                "FOS_ONBOARD_PROXIMITY_CHECK_ENABLED is on — a capture taken beyond "
                "FOS_ONBOARD_MAX_DISTANCE_KM of the business, or one missing usable "
                "coordinates. In every case nothing at all was written"
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "bad_mobile": {
                            "summary": "Malformed mobile number",
                            "value": {"detail": "Phone number must be between 8-15 digits"}
                        },
                        "own_business": {
                            "summary": "The mobile number belongs to the capturing field agent",
                            "value": {
                                "detail": (
                                    "You cannot onboard a business on your own mobile "
                                    "number - please register it from your own account."
                                )
                            }
                        },
                        "alternate_separate_account": {
                            "summary": "The alternatemobile already has its own account",
                            "value": {
                                "detail": (
                                    "The alternate mobile number 9876543210 already has a separate "
                                    "Etown account. Please use a different alternate number."
                                )
                            }
                        },
                        "alternate_linked_to_another": {
                            "summary": "The alternatemobile is another number's alternate",
                            "value": {
                                "detail": (
                                    "The alternate mobile number 9876543210 is already linked with "
                                    "another mobile number. Please use a different alternate number."
                                )
                            }
                        },
                        "too_far_from_business": {
                            "summary": "Rostered FOS agent is not at the business",
                            "value": {"detail": "Please go to provider's location to onboard"}
                        },
                        "missing_fos_coordinates": {
                            "summary": "FOSlatitude/FOSlongitude missing or unusable (rostered FOS agent only)",
                            "value": {
                                "detail": (
                                    "FOSlatitude and FOSlongitude are required and must be "
                                    "valid coordinates (latitude -90..90, longitude "
                                    "-180..180). They are the capturing device's own position, "
                                    "checked against the business location before anything is "
                                    "saved."
                                )
                            }
                        }
                    }
                }
            }
        },
        404: {
            "description": (
                "action_mode='edit' only — no live provider holds this mobile number, or the "
                "edit_providerid sent names none of the ones that do"
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "no_provider_on_number": {
                            "summary": "Nothing registered on this mobile number",
                            "value": {
                                "detail": (
                                    "No provider is registered on this mobile number, so there "
                                    "is no listing to propose an edit against. Onboard it with "
                                    "action_mode='create' first."
                                )
                            }
                        },
                        "edit_providerid_mismatch": {
                            "summary": "edit_providerid is not one of this number's listings",
                            "value": {
                                "detail": (
                                    "No live provider with providerid "
                                    "'3f9c1b2e-0a44-4d1e-9c77-5b2e8a1d6f30' is registered on "
                                    "this mobile number. edit_providerid must name one of the "
                                    "listings the number carries."
                                )
                            }
                        }
                    }
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Provider onboarding failed due to internal server error"}
                }
            }
        }
    }
)
async def onboard_unverified_provider(
    provider_data: UnverifiedProviderOnboardingRequest,
    background_tasks: BackgroundTasks,
    response: Response,
) -> UnverifiedProviderOnboardingResponse:
    """Handle POST /api/provider/unverified_provider: capture a non-verified provider with its services, or queue an edit for review.

    Raises:
        HTTPException: 400 bad input, FOS proximity, own-number or alternate-mobile refusal, 404 edit target missing, 500 otherwise.
    """
    try:
        await _enforce_fos_proximity(provider_data)

        await _reject_fos_own_business(provider_data)

        alternate_conflict = await _alternate_mobile_conflict(
            provider_data.mobilenumber, provider_data.alternatemobile
        )
        if alternate_conflict:
            logger.info(
                "Unverified provider %s refused for '%s': alternatemobile %s is %s (mobile %s); nothing was written",
                provider_data.action_mode, provider_data.businessname, provider_data.alternatemobile,
                alternate_conflict, provider_data.mobilenumber,
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The alternate mobile number {provider_data.alternatemobile.strip()} "
                    f"{_ALTERNATE_MOBILE_CONFLICTS[alternate_conflict]}. Please use a different alternate number."
                ),
            )

        if provider_data.action_mode == "edit":
            return await _edit_unverified_provider(provider_data, background_tasks, response)

        logger.info(f"Starting unverified provider onboarding for business: {provider_data.businessname}")

        for service in provider_data.servicedetails:
            if service.service_name:
                service.service_name = service.service_name.strip()
        body_service_name = _first_service_name(provider_data)
        onboard_services = body_service_name or provider_data.services

        if not await ProviderService.validate_phone_format(provider_data.mobilenumber):
            raise HTTPException(
                status_code=400,
                detail="Invalid mobile number format. Must be 8-15 digits."
            )
        lat: Optional[float] = None
        lng: Optional[float] = None
        if provider_data.latitude:
            try:
                lat = float(provider_data.latitude)
            except (ValueError, TypeError):
                logger.warning("unverified provider onboard: latitude %r is not a number; leaving it unset", provider_data.latitude, exc_info=True)
        if provider_data.longitude:
            try:
                lng = float(provider_data.longitude)
            except (ValueError, TypeError):
                logger.warning("unverified provider onboard: longitude %r is not a number; leaving it unset", provider_data.longitude, exc_info=True)

        duplicate_matches = await find_live_providers_by_mobile(provider_data.mobilenumber)
        if duplicate_matches:
            logger.info(
                "Unverified provider onboard for '%s' lands on mobile %s, already held by %d live "
                "provider(s) (%s) — the onboard goes ahead and the capture is also stored in "
                "etown_provider_review for review",
                provider_data.businessname, provider_data.mobilenumber, len(duplicate_matches),
                ", ".join(f"'{row['businessname']}'" for row in duplicate_matches),
            )

        source = await resolve_capture_source(provider_data.marketing_mobilenumber)

        try:
            result = await ProviderService.onboard_unverified_provider_in_db(
                business_name=provider_data.businessname,
                phone_number=provider_data.mobilenumber,
                description=provider_data.description,
                city=provider_data.city,
                latitude=lat,
                longitude=lng,
                additionaladdressinfo=provider_data.additionaladdressinfo,
                area_location=provider_data.area_location,
                state=provider_data.state,
                formatted_address=provider_data.formattedAddress,
                businesslogo=provider_data.businesslogo,
                marketing_contact_id=provider_data.marketing_contactid,
                business_category_input=provider_data.business_category,
                service_category_input=provider_data.service_category,
                servicetype=provider_data.servicetype,
                email=provider_data.email,
                services=onboard_services,
                alternate_mobile=provider_data.alternatemobile,
                source=source,
                interested=provider_data.interested,
                marketing_mobilenumber=provider_data.marketing_mobilenumber,
            )
        except Exception:
            if duplicate_matches:
                await _record_create_review(provider_data, duplicate_matches, None, None)
            raise

        logger.info(f"Unverified provider onboarding completed for business: {provider_data.businessname}, provider_id: {result['provider_info']['providerid']}")
        providerservice_info = result.get('providerservice_info', {})
        contact_info = result.get('contact_info', {})
        userprofile_info = result.get('userprofile_info', {})
        background_tasks.add_task(
            ProviderService.run_background_enhancements,
            provider_id=result['provider_info']['providerid'],
            providerservice_copy_id=providerservice_info.get('providerservicesid'),
            providerservice_orig_id=providerservice_info.get('providerservicesid_orig'),
            contact_id=contact_info.get('contactid'),
            userprofile_id=userprofile_info.get('userprofileid'),
            business_name=provider_data.businessname,
            description=provider_data.description or '',
            business_category_input=provider_data.business_category,
            service_category_input=provider_data.service_category,
            latitude=lat,
            longitude=lng,
            businesslogo=provider_data.businesslogo,
            phone_number=provider_data.mobilenumber,
            email=contact_info.get('email'),
            city=provider_data.city,
            first_name=contact_info.get('firstname'),
            formatted_address=provider_data.formattedAddress,
            area_location=provider_data.area_location,
            state=provider_data.state,
            additionaladdressinfo=provider_data.additionaladdressinfo,
            servicetype=provider_data.servicetype,
            providertype='etown_nonverified',
            services_input=onboard_services,
            generate_services=body_service_name is None,
            source=source,
            plan_code=result['provider_info'].get('plan_code'),
            interested=provider_data.interested,
        )

        await _record_create_review(
            provider_data, duplicate_matches,
            str(result['provider_info']['providerid']), background_tasks,
        )

        add_services_message: Optional[str] = None
        if provider_data.servicedetails:
            try:
                add_services_message = await _add_unverified_provider_services(
                    provider_data, result, background_tasks, source,
                )
            except Exception as e:
                logger.error(
                    "Unverified provider %s onboarded, but adding its services failed: %s",
                    result['provider_info']['providerid'], e, exc_info=True,
                )
                add_services_message = f"Provider created, but adding its services failed: {e}"

        response = UnverifiedProviderOnboardingResponse(
            contact_info=result['contact_info'],
            userprofile_info=result['userprofile_info'],
            provider_info=result['provider_info'],
            providerservice_info=result['providerservice_info'],
            ai_enhancements=result['ai_enhancements'],
            add_services_message=add_services_message,
        )

        _queue_fos_business_onboarding_whatsapp(
            provider_data, str(result['provider_info']['providerid']), background_tasks,
        )

        if (provider_data.email or "").strip():
            background_tasks.add_task(
                send_fos_business_onboarding_email,
                email=provider_data.email,
                provider_name=provider_data.businessname,
            )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during unverified provider onboarding: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Provider onboarding failed due to internal server error"
        )


@router.get(
    "/unverified_provider_list",
    response_model=UnverifiedProviderListResponse,
    status_code=200,
    summary="List Unverified Providers by Mobile Number",
    description=(
        "Return every live provider registered against a mobile number, with that provider's "
        "service rows, so a field agent can see what is already on file before capturing the "
        "business. The number is matched on its last ten digits, so '+91 98765 43210', "
        "'09876543210' and '9876543210' all resolve to the same listings whatever format the "
        "row was stored in. Deleted providers are never returned, and neither is a capture "
        "still awaiting review - a provider held back by an unapproved etown_provider_review "
        "row is hidden here exactly as it is hidden from search. "
        "Each provider carries a status and, when something stands in the way, the message to "
        "show: 'verified_provider' gives 'This is already a verified provider on Etown'; "
        "'added_by_fos' gives 'This provider is already added by another FOS' (not verified, "
        "but source = 'FOS'); 'unverified' carries no message, nothing blocks the capture. "
        "Verified beats FOS, so a field capture that has since been verified reports as "
        "verified. The top-level status and message summarise the number by that same "
        "precedence, and are 'available' with a null message when the number carries no "
        "provider at all — a 200 with an empty list, not a 404."
    ),
    responses={
        200: {
            "description": "Lookup completed — zero or more providers, each with its services",
            "model": UnverifiedProviderListResponse,
        },
        400: {
            "description": "The mobile number does not contain at least 10 digits",
            "content": {
                "application/json": {
                    "example": {"detail": "mobilenumber must contain at least 10 digits"}
                }
            },
        },
        401: {"description": "Unauthorized — invalid or missing bearer token"},
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to fetch unverified providers"}
                }
            },
        },
    },
)
async def get_unverified_provider_list(
    mobilenumber: str = Query(
        ...,
        description="Mobile number to look up, in any format — only its digits are used",
    ),
    _token: str = Security(verify_token),
) -> UnverifiedProviderListResponse:
    """Handle GET /api/provider/unverified_provider_list: list live providers on a mobile number with services and an overall status.

    Raises:
        HTTPException: 400 when the number holds fewer than 10 digits, 500 on server errors.
    """
    try:
        digits = re.sub(r"\D+", "", mobilenumber or "")
        if len(digits) < 10:
            raise HTTPException(
                status_code=400,
                detail="mobilenumber must contain at least 10 digits",
            )

        logger.info(f"unverified_provider_list | mobile=*****{digits[-4:]}")

        providers = await ProviderService.fetch_providers_by_mobile(mobilenumber)

        status_code = summarise_provider_status(providers)

        logger.info(
            "unverified_provider_list | mobile=*****%s | %d provider(s) | status=%s",
            digits[-4:], len(providers), status_code,
        )

        return UnverifiedProviderListResponse(
            mobilenumber=digits[-10:],
            count=len(providers),
            status=status_code,
            message=PROVIDER_STATUS_MESSAGES.get(status_code),
            providers=[UnverifiedProviderItem(**provider) for provider in providers],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"unverified_provider_list failed | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch unverified providers")


async def _apply_approval_updates(
    request: ProviderApprovalRequest,
    target_providerid: str,
    provider_row: Dict[str, Any],
    background_tasks: BackgroundTasks,
) -> tuple:
    """Apply an approval body's provider edits and servicedetails to one listing, reporting failures instead of raising.

    Args:
        request: The approval body carrying the fields and servicedetails.
        target_providerid: The listing to write to.
        provider_row: Approval row supplying the contactid and userprofileid.
        background_tasks: Queue for the mirror, embedding refresh and AI enhancement.

    Returns:
        Tuple of (update_message, add_services_message), each None when not requested.
    """
    update_message: Optional[str] = None
    add_services_message: Optional[str] = None
    contact_id = provider_row.get("contactid")
    userprofile_id = provider_row.get("userprofileid")

    if request.has_provider_updates():
        try:
            edited = await _edit_onboarded_provider(
                ProviderOnboardingRequest(
                    action_mode="edit",
                    providerid=target_providerid,
                    businessname=request.businessname,
                    description=request.description,
                    types=request.types,
                    formattedAddress=request.formattedAddress,
                    latitude=request.latitude,
                    longitude=request.longitude,
                    businesslogo=request.businesslogo,
                    business_category=request.business_category,
                    service_category=request.service_category,
                    city=request.city,
                    additionaladdressinfo=request.additionaladdressinfo,
                    services=request.services,
                    area_location=request.area_location,
                    servicetype=request.servicetype,
                    state=request.state,
                    language=request.language,
                    email=request.email,
                    alternatemobile=request.alternatemobile,
                ),
                background_tasks,
                Response(),
            )
        except HTTPException as e:
            update_message = f"Provider update failed: {e.detail}"
            logger.error(
                "provider_approve | update failed | providerid=%s | %s",
                target_providerid, e.detail,
            )
        except Exception as e:
            update_message = f"Provider update failed: {e}"
            logger.error(
                "provider_approve | update failed | providerid=%s | %s",
                target_providerid, e, exc_info=True,
            )
        else:
            update_message = "Provider updated successfully"
            contact_id = edited.contact_info.contactid or contact_id
            userprofile_id = edited.userprofile_info.userprofileid or userprofile_id

    if request.servicedetails:
        if not contact_id or not userprofile_id:
            add_services_message = (
                "Services were not added: the provider has no contact or user profile"
            )
            logger.warning(
                "provider_approve | services skipped | providerid=%s contact=%s profile=%s",
                target_providerid, contact_id, userprofile_id,
            )
            return (update_message, add_services_message)

        try:
            add_services_message, enhancements, provider = (
                await ProviderServiceManagementService.add_services_to_provider_workflow(
                    contact_id=contact_id,
                    userprofile_id=userprofile_id,
                    provider_id=target_providerid,
                    servicedetails=request.servicedetails,
                )
            )
        except PlanLimitError as e:
            add_services_message = str(e)
            logger.warning(
                "provider_approve | services refused by the listing cap | providerid=%s | %s",
                target_providerid, e,
            )
        except Exception as e:
            add_services_message = f"Services could not be added: {e}"
            logger.error(
                "provider_approve | add services failed | providerid=%s | %s",
                target_providerid, e, exc_info=True,
            )
        else:
            if enhancements and provider:
                background_tasks.add_task(
                    ProviderServiceManagementService.run_background_enhancements,
                    provider=provider,
                    contact_id=contact_id,
                    userprofile_id=userprofile_id,
                    service_enhancements=enhancements,
                )

    return (update_message, add_services_message)


@router.post(
    "/approve",
    response_model=ProviderApprovalResponse,
    status_code=200,
    summary="Approve or Reject a Provider",
    description=(
        "Set etown_provider.approve on one non-deleted provider — true to approve, false to "
        "reject. The listing is named by 'providerid', matched case-insensitively, and it is "
        "the only row this call touches: a contact can hold several businesses and each is "
        "reviewed on its own. The column is three-state: it is NULL until this "
        "endpoint is called, which is how 'not decided yet' is told apart from 'rejected'. "
        "The flag itself changes nothing else — it does not drive search or the registration "
        "promotion — but the decision also carries the verification status, through the same "
        "cascade as POST /dataverse/verifiedproviders: publicsearchprovider + providertype "
        "across etown_provider, etown_providerservices and etown_providerservices_copy — all "
        "three keyed on the providerid — and the verified boolean on the user profile, keyed "
        "on the userprofileid that same provider row carries. etown_contact is deliberately "
        "left alone: its verified flag describes the person, and one contact can hold several "
        "businesses, so deciding one of them must not re-flag the rest — POST "
        "/dataverse/verifiedproviders is still the call that writes the contact. The four "
        "tables written are mirrored to Dataverse "
        "in the background. approve = true promotes a still non-verified provider to "
        "'etown_verified'/'verified' + verified = true; approve = false demotes an "
        "already-verified one to 'etown_nonverified'/'non-verified' + verified = false. A "
        "provider already holding the status its decision asks for is left alone. The decision "
        "itself is mirrored to Dataverse as ets_approved (1 approved / 0 rejected), in the "
        "background. "
        "A provider promoted by this call also triggers the NEW_PROVIDER_NEARBY push to the "
        "users within ~10 km of it — the announcement POST /provider/unverified_provider used "
        "to send at capture time, now held back until the business is actually approved. A "
        "rejection announces nothing. "
        "The body may also carry the correction the approver is signing off with the decision: "
        "POST /onboard's action_mode='edit' field set (businessname, "
        "description, formattedAddress, latitude, longitude, businesslogo, business_category, "
        "service_category, city, additionaladdressinfo, services, area_location, servicetype, "
        "state, email) and a 'servicedetails' list. All optional, all written only when "
        "approve = true. The fields go "
        "through POST /onboard's edit path, so only what is sent is written and the Dataverse "
        "mirror plus the content_vector refresh follow; the servicedetails go through POST "
        "/provider/add_services' workflow, which updates a service whose name the provider "
        "already has and adds the rest under the plan's max_listings. Neither can fail the "
        "approval: the flag is committed first and the outcome comes back in update_message / "
        "add_services_message."
    ),
    responses={
        200: {"description": "Approval flag written", "model": ProviderApprovalResponse},
        401: {"description": "Unauthorized — invalid or missing bearer token"},
        404: {
            "description": "No non-deleted provider carries that providerid",
            "content": {
                "application/json": {
                    "example": {"detail": "No provider found for this providerid"}
                }
            },
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to update provider approval"}
                }
            },
        },
    },
)
async def set_provider_approval(
    request: ProviderApprovalRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> ProviderApprovalResponse:
    """Handle POST /api/provider/approve: set a provider's approve flag, cascade verification, and apply approved edits.

    Raises:
        HTTPException: 404 when no non-deleted provider carries that providerid, 500 on server errors.
    """
    try:
        logger.info(
            f"provider_approve | providerid={request.providerid} approve={request.approve}"
        )

        result = await ProviderService.set_provider_approval_by_providerid(
            provider_id=request.providerid,
            approve=request.approve,
        )
        if not result:
            raise HTTPException(
                status_code=404,
                detail="No provider found for this providerid",
            )

        providerid = result["providerid"]
        background_tasks.add_task(
            sync_provider_approval_to_dataverse,
            providerids=[providerid],
            approve=result["approve"],
        )

        wants_updates = request.has_provider_updates() or bool(request.servicedetails)
        rejected_updates: Optional[str] = None
        if wants_updates and not request.approve:
            rejected_updates = (
                "approve = false — the fields and servicedetails in this body were not "
                "written; the rejection wrote etown_provider.approve and un-verified the "
                "listing, nothing more"
            )
            logger.info(
                "provider_approve | approve=false | the body's updates were not written"
            )

        verified = False
        needs_cascade = (
            result["needs_verification"]
            if request.approve
            else result["needs_nonverification"]
        )
        if needs_cascade:
            try:
                cascade = await set_provider_verification(
                    providerid, request.approve, update_contact=False,
                )
            except Exception as e:
                logger.error(
                    "provider_approve | verification cascade failed | providerid=%s "
                    "verified=%s | %s",
                    providerid, request.approve, e, exc_info=True,
                )
                cascade = {}
            if not cascade.get("found"):
                logger.warning(
                    "provider_approve | status not written | providerid=%s verified=%s",
                    providerid, request.approve,
                )
            else:
                verified = request.approve
                background_tasks.add_task(
                    sync_provider_verification_to_dataverse,
                    providerid=providerid,
                    verified=request.approve,
                    userprofileid=cascade.get("userprofileid"),
                    contactid=None,
                    providerservices_ids=cascade.get("providerservices_ids") or [],
                )

                if request.approve:
                    from app.core.config import settings as _settings
                    from app.etown.notifications.triggers import notify_new_provider_nearby

                    if _settings.NOTIFICATIONS_GOVERNANCE_ENABLED:
                        background_tasks.add_task(
                            notify_new_provider_nearby,
                            provider_upid=cascade.get("userprofileid") or result.get("userprofileid") or '',
                            businessname=result.get("businessname") or '',
                            townname=result.get("townname") or '',
                            contactid=cascade.get("contactid") or result.get("contactid") or '',
                        )

        update_message: Optional[str] = rejected_updates
        add_services_message: Optional[str] = None
        if wants_updates and request.approve:
            update_message, add_services_message = await _apply_approval_updates(
                request,
                providerid,
                result,
                background_tasks,
            )

        return ProviderApprovalResponse(
            providerid=providerid,
            approve=result["approve"],
            verified=verified,
            update_message=update_message,
            add_services_message=add_services_message,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"provider_approve failed | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update provider approval")


@router.post(
    "/add_services",
    response_model=AddServicesToProvidersResponse,
    status_code=200,
    summary="Add Services to Providers",
    description="Add one or more new services to an existing provider. For each service the workflow uploads up to three base64 images to Azure Blob, generates business/service categories, and inserts rows into etown_providerservices (+ etown_providerservices_copy for search).",
    responses={
        200: {
            "description": "Add-services workflow completed",
            "model": AddServicesToProvidersResponse,
            "content": {
                "application/json": {
                    "examples": {
                        "success": {"value": {"message": "Services added successfully"}},
                        "user_not_found": {"value": {"message": "User Not Found"}},
                        "provider_not_found": {"value": {"message": "Provider not found"}},
                    }
                }
            },
        },
        401: {"description": "Missing or invalid bearer token"},
        402: {"description": "The new services would exceed the plan's listing allowance"},
        500: {"description": "Internal server error"},
    },
)
async def add_services_to_providers(
    request: AddServicesToProvidersRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> AddServicesToProvidersResponse:
    """Handle POST /api/provider/add_services: add flat or parsed-offering services to a provider and queue AI enhancements.

    Raises:
        HTTPException: 402 when the plan's listing cap is exceeded, 500 on other failures.
    """
    try:
        if request.business_details is not None:
            business = request.business_details
            contact_id, userprofile_id, provider_id = (
                business.contactid, business.userprofileid, business.providerid,
            )
            logger.info(
                f"Add services request (nested) | provider_id={provider_id} "
                f"contact_id={contact_id} offerings={len(request.offering_data)}"
            )
            assign_pydantic_offering_ids(request.offering_data)
            if userprofile_id and provider_id:
                try:
                    await check_listing_cap(
                        userprofile_id,
                        provider_id,
                        [(off.offering_name or "").strip() for off in request.offering_data],
                    )
                except PlanLimitError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"[add_services] Pre-upload listing cap check failed, "
                        f"deferring to the workflow: {e}"
                    )
            await upload_parsed_business_media(business, request.offering_data)
            servicedetails = map_parsed_offerings_to_service_details(request.offering_data)
        else:
            contact_id, userprofile_id, provider_id = (
                request.contact_id, request.userprofile_id, request.provider_id,
            )
            servicedetails = request.servicedetails
            logger.info(
                f"Add services request (flat) | provider_id={provider_id} "
                f"contact_id={contact_id} count={len(servicedetails)}"
            )

        message, enhancements, provider = await ProviderServiceManagementService.add_services_to_provider_workflow(
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            provider_id=provider_id,
            servicedetails=servicedetails,
        )

        if request.business_details is not None and provider:
            await save_parsed_offerings_to_db(
                request.business_details.model_dump(),
                [offering.model_dump() for offering in request.offering_data],
            )

        if enhancements and provider:
            background_tasks.add_task(
                ProviderServiceManagementService.run_background_enhancements,
                provider=provider,
                contact_id=contact_id,
                userprofile_id=userprofile_id,
                service_enhancements=enhancements,
            )

        logger.info(f"Add services workflow completed | message='{message}'")
        return AddServicesToProvidersResponse(message=message)

    except PlanLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"add_services_to_providers endpoint error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to add services to provider",
        )


@router.get(
    "/unrated_provider_list",
    response_model=List[UnratedProviderItem],
    status_code=200,
    summary="Get Unrated Provider List",
    description="Retrieve the list of providers that the user has not yet rated",
    responses={
        200: {
            "description": "Unrated provider list retrieved successfully",
            "model": List[UnratedProviderItem]
        },
        400: {
            "description": "Invalid input parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "contact_id and userprofile_id are required"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve unrated provider list"}
                }
            }
        }
    }
)
async def get_unrated_provider_list(
    contact_id: str = Query(..., description="The contact ID of the requesting user"),
    userprofile_id: str = Query(..., description="The user profile ID of the requesting user"),
) -> List[UnratedProviderItem]:
    """Handle GET /api/provider/unrated_provider_list: list providers from the user's bookings that they have not rated yet."""
    try:
        logger.info(f"Fetching unrated provider list for contact_id={contact_id}, userprofile_id={userprofile_id}")

        results = await ProviderService.get_unrated_provider_list(
            contact_id=contact_id,
            userprofile_id=userprofile_id
        )

        return [
            UnratedProviderItem(
                Etown_providercontactid=item.get("serviceprovidercontactid"),
                Etown_provideruserprofileid=item.get("serviceprovideruserprofileid"),
                Etown_businessname=item.get("provider_businessname"),
                Etown_serviceimageurl=item.get("provider_serviceimageurl"),
                Etown_providertype=item.get("providertype"),
                Etown_servicecreatedon=_iso_or_none(item.get("providerservices_createdon")),
                Etown_bookingid=_str_or_none(item.get("allrequestid")),
                Etown_bookingtrackid=_str_or_none(item.get("requestid")),
                Etown_providerdeleted=item.get("provider_deleted"),
                Etown_providerdeactivated=item.get("provider_deactivated"),
            )
            for item in results
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching unrated provider list: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve unrated provider list"
        )

@router.get(
    "/identifiers",
    response_model=ProviderIdentifiersResponse,
    status_code=200,
    summary="Get Provider Identifiers",
    description=(
        "Resolve a providerid to the contactid and userprofileid that own the listing. "
        "Reads etown_provider, falling back to etown_providerservices_copy / "
        "etown_providerservices for listings whose parent provider row is missing, and to "
        "etown_userprofile when the listing carries no contactid."
    ),
    responses={
        200: {"description": "Identifiers resolved", "model": ProviderIdentifiersResponse},
        401: {"description": "Unauthorized — invalid or missing bearer token"},
        404: {
            "description": "No provider or provider-service row matches the providerid",
            "content": {
                "application/json": {
                    "example": {"detail": "Provider not found"}
                }
            },
        },
        500: {"description": "Internal server error"},
    },
)
async def get_provider_identifiers(
    providerid: str = Query(..., description="The provider ID to resolve"),
    token: str = Depends(verify_token),
) -> ProviderIdentifiersResponse:
    """Handle GET /api/provider/identifiers: resolve a providerid to its contactid, userprofileid and business name.

    Raises:
        HTTPException: 404 when the providerid matches no row, 500 on server errors.
    """
    try:
        logger.info(f"provider_identifiers | providerid={providerid}")

        result = await ProviderService.fetch_provider_identifiers(providerid=providerid)
        if not result:
            raise HTTPException(status_code=404, detail="Provider not found")

        return ProviderIdentifiersResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"provider_identifiers failed | providerid={providerid} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to resolve provider identifiers")


@router.get(
    "/provider_request_list",
    response_model=ProviderRequestListResponse,
    status_code=200,
    summary="Get Provider Request List",
    description="Retrieve the list of all booking requests received by the provider",
    responses={
        200: {
            "description": "Provider request list retrieved successfully",
            "model": ProviderRequestListResponse,
        },
        400: {
            "description": "Invalid input parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "contactid and userprofileid are required"}
                }
            },
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve provider request list"}
                }
            },
        },
    },
)
async def get_provider_request_list(
    contactid: str = Query(..., description="The contact ID of the provider"),
    userprofileid: str = Query(..., description="The user profile ID of the provider"),
) -> ProviderRequestListResponse:
    """Handle GET /api/provider/provider_request_list: return every booking received by the provider with its service lines."""
    try:
        logger.info(
            f"provider_request_list | contactid={contactid} userprofileid={userprofileid}"
        )

        raw_bookings = await ProviderRequestListService.get_provider_request_list(
            contactid=contactid,
            userprofileid=userprofileid,
        )

        booking_items = [
            ProviderBookingItem(
                BookingDetails=ProviderBookingDetails(**b.get("BookingDetails", {})),
                ServiceDetails=[
                    ProviderServiceDetail(**s)
                    for s in b.get("ServiceDetails", [])
                ],
            )
            for b in raw_bookings
        ]

        return ProviderRequestListResponse(Bookings=booking_items)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching provider request list: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve provider request list",
        )


@router.get(
    "/provider_completed_list",
    response_model=ProviderCompletedListResponse,
    status_code=200,
    summary="Get Provider Completed List",
    description="Retrieve the list of all completed booking requests received by the provider",
    responses={
        200: {
            "description": "Provider completed list retrieved successfully",
            "model": ProviderCompletedListResponse,
        },
        400: {
            "description": "Invalid input parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "contactid and userprofileid are required"}
                }
            },
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve provider completed list"}
                }
            },
        },
    },
)
async def provider_completed_list(
    contactid: str = Query(..., description="The contact ID of the provider"),
    userprofileid: str = Query(..., description="The user profile ID of the provider"),
) -> ProviderCompletedListResponse:
    """Handle GET /api/provider/provider_completed_list: return the provider's completed bookings with ratings and service lines."""
    try:
        logger.info(
            f"provider_completed_list | contactid={contactid} userprofileid={userprofileid}"
        )

        raw_bookings = await ProviderRequestListService.get_provider_completed_list(
            contactid=contactid,
            userprofileid=userprofileid,
        )

        booking_items = [
            ProviderCompletedBookingItem(
                BookingDetails=ProviderBookingDetails(**b.get("BookingDetails", {})),
                Rating=b.get("Rating"),
                ServiceDetails=[
                    ProviderServiceDetail(**s)
                    for s in b.get("ServiceDetails", [])
                ],
            )
            for b in raw_bookings
        ]

        return ProviderCompletedListResponse(Bookings=booking_items)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching provider completed list: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve provider completed list",
        )


@router.delete(
    "/delete_service",
    response_model=DeleteServiceResponse,
    status_code=200,
    summary="Delete Provider Service",
    description="Delete a specific service belonging to a provider. Removes the row from both etown_providerservices and etown_providerservices_copy. The requesting contact must be the owner of the provider.",
    responses={
        200: {"description": "Delete-service workflow completed", "model": DeleteServiceResponse,
              "content": {"application/json": {"examples": {
                  "success": {"value": {"message": "Service deleted successfully"}},
                  "service_not_found": {"value": {"message": "Service not found"}},
                  "user_not_found": {"value": {"message": "User Not Found"}},
                  "provider_not_found": {"value": {"message": "Provider not found"}},
                  "unauthorized": {"value": {"message": "Unauthorized"}},
              }}}},
        401: {"description": "Missing or invalid bearer token"},
        500: {"description": "Internal server error"},
    },
)
async def delete_provider_service(
    request: DeleteServiceRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> DeleteServiceResponse:
    """Handle DELETE /api/provider/delete_service: delete an owned service row from both service tables and mirror the delete.

    Raises:
        HTTPException: 404 for a missing user, provider or service or an ownership mismatch, 500 otherwise.
    """
    try:
        logger.info(
            f"Delete service request | providerservices_id={request.providerservices_id} "
            f"provider_id={request.provider_id} contact_id={request.contact_id}"
        )

        result = await ProviderServiceManagementService.delete_service_workflow(
            contact_id=request.contact_id,
            provider_id=request.provider_id,
            providerservices_id=request.providerservices_id,
        )
        message = result["message"]

        _not_found = {"Service not found", "User Not Found", "Provider not found", "Unauthorized"}
        if message in _not_found:
            raise HTTPException(status_code=404, detail=message)

        background_tasks.add_task(
            sync_provider_service_delete_from_dataverse,
            providerservices_orig_id=result["providerservices_orig_id"],
        )

        logger.info(f"Delete service workflow completed | message='{message}'")
        return DeleteServiceResponse(message=message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"delete_provider_service endpoint error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to delete provider service",
        )


@router.delete(
    "/delete",
    response_model=ProviderDeleteResponse,
    status_code=200,
    summary="Delete Provider",
    description=(
        "Delete a provider and all associated records using userprofileid + contactid. "
        "This is a PHYSICAL delete of the business - its services, provider row, userprofile "
        "and plan counters. The owning etown_contact is kept, because a member profile can "
        "share it. To retire the person, use DELETE /api/users/delete_account."
    ),
    responses={
        200: {"description": "Provider deleted successfully", "model": ProviderDeleteResponse},
        404: {"description": "Provider not found", "content": {"application/json": {"example": {"detail": "Provider not found for given userprofileid and contactid"}}}},
        500: {"description": "Internal server error"},
    }
)
async def delete_provider(
    http_request: Request,
    request: ProviderDeleteRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token)
) -> ProviderDeleteResponse:
    """Handle DELETE /api/provider/delete: physically delete a provider's plan, service, provider and profile rows, keeping the contact.

    Raises:
        HTTPException: 404 when no provider matches, 500 on failures.
    """
    try:
        client_ip = http_request.client.host if http_request.client else "unknown"
        user_agent = http_request.headers.get("user-agent", "unknown")
        logger.warning(
            "[PROVIDER_DELETE] CALLED | contactid=%s | userprofileid=%s | "
            "client_ip=%s | user_agent=%s",
            request.contactid,
            request.userprofileid,
            client_ip,
            user_agent,
        )
        result = await ProviderService.delete_provider_from_db(
            contactid=request.contactid,
            userprofileid=request.userprofileid,
        )
        if not result["deleted"]:
            raise HTTPException(status_code=404, detail="Provider not found for given userprofileid and contactid")
        logger.warning(
            "[PROVIDER_DELETE] SUCCESS | providerid=%s | business=%s | "
            "client_ip=%s | user_agent=%s",
            result.get('providerid'),
            result.get('business_name'),
            client_ip,
            user_agent,
        )
        background_tasks.add_task(
            sync_provider_delete_from_dataverse,
            contact_id=result['contactid'],
            userprofile_id=result['userprofileid'],
            provider_id=result.get('providerid'),
        )
        return ProviderDeleteResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during provider deletion: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Provider deletion failed due to internal server error")

@router.post(
    "/lifecycle",
    response_model=ProviderLifecycleResponse,
    status_code=200,
    summary="Deactivate or Reactivate One Provider",
    description=(
        "Pause or resume a single business without touching its owner. "
        "'deactivate' hides the provider, its services, offers, offerings and town links from "
        "search, feed and booking, and drops that business to the free tier - it keeps the plan "
        "it holds but none of the tier's allowances, features or badge, and the paid term keeps "
        "running while it is away. 'reactivate' clears the flag and restores both. The owner's "
        "contact, member profile and other businesses are unaffected: to retire the person, use "
        "DELETE /api/users/delete_account. Nothing is physically deleted either way."
    ),
    responses={
        200: {"description": "Business deactivated or reactivated", "model": ProviderLifecycleResponse},
        400: {"description": "Unsupported action_mode"},
        404: {"description": "No eligible provider - unknown, deleted, or already in that state"},
        500: {"description": "Internal server error"},
    },
)
async def provider_lifecycle(
    request: ProviderLifecycleRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> ProviderLifecycleResponse:
    """Handle POST /api/provider/lifecycle: deactivate or reactivate one business and mirror the flag to Dataverse.

    Raises:
        HTTPException: 400 bad action_mode, 404 no eligible provider, 500 when the transition fails.
    """
    action_mode = (request.action_mode or "deactivate").strip().lower()
    if action_mode not in _PROVIDER_LIFECYCLE_HANDLER:
        raise HTTPException(
            status_code=400,
            detail=f"action_mode must be 'deactivate' or 'reactivate', got '{request.action_mode}'",
        )

    try:
        result = await _PROVIDER_LIFECYCLE_HANDLER[action_mode](request.providerid)
    except Exception as e:
        logger.error(
            "[PROVIDER_LIFECYCLE] ERROR | providerid=%s | mode=%s | %s",
            request.providerid, action_mode, e, exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Provider {action_mode} failed unexpectedly")

    if not result["found"]:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No eligible provider for providerid '{request.providerid}' - unknown, "
                f"deleted, or already {_PROVIDER_LIFECYCLE_STATUS[action_mode]}."
            ),
        )

    background_tasks.add_task(
        sync_provider_lifecycle_to_dataverse, result["providerid"], action_mode
    )

    logger.info(
        "[PROVIDER_LIFECYCLE] %s | providerid=%s | business=%s | rows=%d",
        action_mode.upper(), result["providerid"], result["businessname"], result["rows_flagged"],
    )
    return ProviderLifecycleResponse(
        status=_PROVIDER_LIFECYCLE_STATUS[action_mode],
        providerid=result["providerid"],
        userprofileid=result["userprofileid"],
        contactid=result["contactid"],
        business_name=result["businessname"],
        rows_flagged=result["rows_flagged"],
    )



@router.get(
    "/rangesummary",
    summary="Get Provider Date Range Summary",
    description="Retrieve offerview count, mycircle count, offer count, and booking count for a provider within a date range, plus lifetime totals under total_metric. Insights are day-by-day, weekly once the range spans a calendar month or more, or monthly once it spans a year or more.",
    responses={
        200: {
            "description": "Date range summary retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "summary": {
                            "offerview_count": 5,
                            "mycircle_count": 3,
                            "offer_count": 4,
                            "booking_count": 2
                        },
                        "total_metric": {
                            "offerview_count": 120,
                            "mycircle_count": 42,
                            "booking_count": 18
                        },
                        "interval": "daily",
                        "insights": [
                            {
                                "date": "2026-05-01",
                                "day": "Friday",
                                "offerview_count": 3,
                                "mycircle_count": 2,
                                "booking_count": 1
                            },
                            {
                                "date": "2026-05-02",
                                "day": "Saturday",
                                "offerview_count": 2,
                                "mycircle_count": 1,
                                "booking_count": 1
                            }
                        ]
                    }
                }
            }
        },
        400: {
            "description": "Missing or invalid date parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "start_date and end_date are required"}
                }
            }
        },
        401: {
            "description": "Unauthorized - invalid or missing access token"
        },
        500: {
            "description": "Internal server error"
        }
    }
)
async def get_date_range_summary(
    contactid: str = Query(..., description="The provider's contact ID"),
    userprofileid: str = Query(..., description="The provider's user profile ID"),
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date, inclusive (YYYY-MM-DD)"),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/provider/rangesummary: return a provider's date-range counts, all-time totals and daily, weekly or monthly insights.

    Raises:
        HTTPException: 402 when the plan excludes view insights, 400 missing dates, 500 on failure.
    """
    track_api_call(background_tasks, "/api/provider/rangesummary", contactid, userprofileid)

    try:
        logger.info(
            f"date_range_summary | contactid={contactid} | userprofileid={userprofileid} "
            f"| start={start_date} | end={end_date}"
        )
        plan = await resolve_plan(userprofileid)
        if plan is not None and plan.get("profile_view_insights") == "none":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Your {plan['plan_name']} plan does not include profile view insights. "
                    "Upgrade your plan to see them."
                ),
            )
        summary = await fetch_date_range_summary(contactid, userprofileid, start_date, end_date)
        if summary is None:
            raise HTTPException(status_code=400, detail="start_date and end_date are required")
        return JSONResponse(status_code=200, content=summary)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"date_range_summary failed | userprofileid={userprofileid} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve date range summary")


@router.get(
    "/profile_viewers",
    response_model=ProfileViewersResponse,
    status_code=200,
    summary="Get Profile Viewers",
    description=(
        "List the users who viewed the provider's profile, newest first. Each viewer carries "
        "the same detail the feed-likes list does — name, profile image, role, provider type, "
        "connection status and invitation code — with the date they first viewed the profile. "
        "Views are deduplicated per viewer, and the provider's own visits are excluded. "
        "Paged like GET /api/suggestions/search: pass page=1, page=2, … and stop when a page "
        "comes back with fewer viewers than the page size. The page size is the optional "
        f"`limit` query parameter (default {PROFILE_VIEWERS_PAGE_SIZE}, max 100). "
        "`total_views` sits beside `data` and counts the viewers across "
        "every page, so it does not shrink as you page. Requires a plan whose "
        "profile_view_insights is not 'none', as the aggregate GET /rangesummary counts do."
    ),
    responses={
        200: {"description": "Profile viewers retrieved successfully", "model": ProfileViewersResponse},
        401: {"description": "Unauthorized — invalid or missing bearer token"},
        402: {
            "description": "The provider's plan does not include profile view insights",
            "content": {
                "application/json": {
                    "example": {"detail": "Your Verified Provider - Free plan does not include profile view insights. Upgrade your plan to see them."}
                }
            },
        },
        500: {"description": "Internal server error"},
    },
)
async def get_profile_viewers(
    contactid: str = Query(..., description="The provider's contact ID"),
    userprofileid: str = Query(..., description="The provider's user profile ID"),
    page: int = Query(1, ge=1, description="Page number for pagination, starts from 1"),
    limit: int = Query(
        PROFILE_VIEWERS_PAGE_SIZE,
        ge=1,
        le=100,
        description=f"Viewers per page (default {PROFILE_VIEWERS_PAGE_SIZE}, max 100)",
    ),
    token: str = Depends(verify_token),
) -> ProfileViewersResponse:
    """Handle GET /api/provider/profile_viewers: return one page of the provider's profile viewers, newest first, with the total.

    Raises:
        HTTPException: 402 when the plan excludes profile view insights, 500 on failure.
    """
    try:
        logger.info(
            f"profile_viewers | contactid={contactid} | userprofileid={userprofileid}"
            f" | page={page} | limit={limit}"
        )

        plan = await resolve_plan(userprofileid)
        if plan is not None and plan.get("profile_view_insights") == "none":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Your {plan['plan_name']} plan does not include profile view insights. "
                    "Upgrade your plan to see them."
                ),
            )

        rows, total_views = await fetch_profile_viewers(
            contactid=contactid,
            userprofileid=userprofileid,
            page=page,
            limit=limit,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"profile_viewers failed | userprofileid={userprofileid} | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve profile viewers")

    badge_map = await resolve_provider_badges(row.get("userprofileid") for row in rows)

    viewers = [
        ProfileViewerItem(
            Etown_name=row.get("name"),
            Etown_contactid=row.get("contactid"),
            Etown_userprofileid=row.get("userprofileid"),
            Etown_profileimage=row.get("profileimage"),
            Etown_role=row.get("role"),
            Etown_providertype=premium_display(
                row.get("providertype"),
                (badge_map.get(row.get("userprofileid")) or {}).get("premium_label"),
            ),
            Etown_isconnected=row.get("is_connected"),
            Etown_invitation=row.get("invitation"),
            Etown_vieweddate=(
                row["viewed_on"]
                .astimezone(timezone(timedelta(hours=5, minutes=30)))
                .strftime("%m/%d/%Y %I:%M %p")
                if row.get("viewed_on") else None
            ),
            Etown_deleted=row.get("viewer_deleted"),
            Etown_deactivated=row.get("viewer_deactivated"),
        )
        for row in rows
    ]
    return ProfileViewersResponse(total_views=total_views, data=viewers)


@router.post(
    "/onboard_details",
    response_model=OnboardDetailsResponse,
    status_code=200,
    summary="Extract Provider Onboarding Details from Image",
    description="Upload a business image (flyer, business card, menu, etc.) and extract structured provider and service details using AI Vision.",
)
async def onboard_details(
    image: UploadFile = File(..., description="Business image (jpg, png) max 10MB"),
    _token: str = Security(verify_token),
) -> OnboardDetailsResponse:
    """Handle POST /api/provider/onboard_details: extract provider and service details from an uploaded JPEG or PNG via AI vision.

    Raises:
        HTTPException: 422 no file, 400 bad type, empty or over 10 MB, 500 on extraction errors.
    """
    import base64

    ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png"}
    MAX_SIZE = 10 * 1024 * 1024

    if not image.filename:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No file provided")

    if image.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type '{image.content_type}'. Allowed: jpeg, jpg, png",
        )

    image_data = await image.read()

    if len(image_data) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    if len(image_data) > MAX_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {MAX_SIZE // (1024 * 1024)} MB",
        )

    try:
        image_base64 = base64.b64encode(image_data).decode("utf-8")
        logger.info("onboard_details | processing image: %s (%d bytes)", image.filename, len(image_data))
        result = await extract_onboard_details(image.content_type, image_base64)
        logger.info("onboard_details | extraction complete for: %s", image.filename)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("onboard_details | unexpected error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to extract onboarding details from image")


@router.post(
    "/ai_onboarding",
    response_model=AIOnboardingResponse,
    status_code=200,
    summary="AI Onboarding: Read a Business from an Image and/or Prompt",
    description=(
        "Takes a base64 (or data-URL) JPEG, PNG or WebP of a business - shopfront board, "
        "visiting card, flyer or menu, 10 MB at most - a free-text 'prompt' the field agent "
        "typed about it, or both. The listing is always generated; its mobile (the prompt's first "
        "valid mobile, else the first one read) is then checked as in "
        "GET /provider/unverified_provider_list, and when it already has providers they are returned "
        "in existing_providers with their status, alongside the generated listing, and a "
        "response saying so. A later prompt in the same conversation_id "
        "that contains one of those providerids merges: that earlier generated listing is reused "
        "(it is generated from the earlier prompt and image only when the earlier turn had none, or "
        "this prompt adds text or an image), and is merged into that provider - its stored values win "
        "and empty ones are filled from the listing; a generated service similar in name to a kept one "
        "only fills that service's empty fields, and dissimilar ones are added - and providerid is set. A similar "
        "business name, and an address worded alike or within 2 km, keep the existing ones. A different "
        "name or a distant address merges nothing yet: confirmation_required is true, providerid is "
        "set, conflicting_business_name and/or conflicting_address hold the generated values, the "
        "unmerged listing is returned and response asks which to use; the next prompt in the "
        "conversation naming them (or 'yes' for the generated values, 'no' for the existing ones, or "
        "another name or address) does the merge with those values, and an unclear reply asks again; "
        "Gemini reads that reply, and an email, website or owner name in it replaces the stored one "
        "and its first valid mobile other than the main one becomes alternatemobile. A confirmed business name also "
        "has Gemini rewrite the business and service descriptions to fit it. When the conversation's last "
        "reply showed existing providers or awaits a confirmation, a prompt such as 'onboard as new "
        "provider', 'add as a new listing' or 'don't merge' returns that reply's generated listing with "
        "existing_providers empty and no providerid, and nothing is merged; such a prompt (or 'onboard a "
        "new business/shop') anywhere earlier in the conversation or in this prompt skips the existing-provider "
        "lookup, so the listing is generated only from the details given. When a provider on the mobile has the caller's "
        "contact_id as its contactid, nothing is shown or merged and response says the caller can't "
        "onboard their own provider. A providerid not shown in the "
        "conversation gets a response saying so. Otherwise an image is uploaded to Azure Blob first. Gemini Flash then "
        "reads both together: the business name, phone numbers, email, website, address, "
        "category, owner name and Service_Type ('Home Delivery', 'Store Delivery' or 'Both') are taken "
        "only from what the prompt and image give - the prompt wins where they disagree - and are null "
        "when neither gives them. A 2-3 "
        "sentence business description and 3-4 services are generated, the services the prompt "
        "names or the image shows first, topped up with ones typical for the business. Given "
        "prices are kept and none is invented. The response carries Provider_Details (the "
        "business facts, generated description, mobilenumber, alternatemobile, latitude, longitude "
        "and business_category; no Phone_Number, as the numbers read become mobilenumber and "
        "alternatemobile) and Service_Details, an array of 3-4 "
        "services each with Service_Name, Service_Description, Service_Type, Service_Quantity "
        "(a number for a product, '--' for a service), Service_Price (null unless given) and "
        "Service_Expected_Price (the AI's estimate of the usual local price in rupees) and "
        "Service_Image_Url: the stored image's blob path on the service the prompt says the photo is "
        "for (added when not listed), or, when the prompt names none or the photo shows a single item, "
        "on the service it best matches, else null - when the prompt mentions a photo that fits no "
        "service, response asks which service it is for; a merge keeps the existing services' images and adds "
        "generated services only until there are 5 in all. The first "
        "and second valid Indian mobiles found come back normalised as "
        "Provider_Details.mobilenumber and alternatemobile, with status 'available'. An alternatemobile that "
        "POST /users/check_alternate_mobile would call separate_account, linked_to_another or same_as_original "
        "is returned null, response says why and asks for another, and confirmation_required is true; "
        "when that check fails the alternatemobile is kept. Its latitude and longitude are "
        "geocoded from the address (null when there is none or it cannot be found); a merge keeps "
        "the existing provider's stored coordinates when its address is kept. The business name and mobile "
        "are required: when either is missing, response asks the field agent for it (or both); "
        "otherwise it is null. Nothing is onboarded and no provider row is "
        "written: the result pre-fills the capture form, which is then submitted to "
        "POST /provider/unverified_provider. Each call that returns a result is stored in "
        "etown_ai_onboarding as two rows sharing a turnid - a 'user' row (prompt_text, image path, "
        "arrival time) and an 'assistant' row (response_text, whole response, reply time, "
        "response_time_ms), both with the request's conversation_id and the caller's contact_id and user_profile_id - as a "
        "background task, so a failed insert never fails the call. A failed upload still returns the analysis, with "
        "business_image_url null. confirmation_required is true whenever response asks the agent to "
        "confirm or provide something - a merge conflict, existing providers to pick from, a missing "
        "or invalid name or mobile, a refused alternate mobile, a photo whose service is unclear, or a chat reply inviting details - "
        "and false when nothing is asked."
    ),
    responses={
        400: {
            "description": (
                "Neither business_image nor prompt sent, or business_image is not base64, "
                "not a JPEG/PNG/WebP, or over 10 MB"
            )
        },
        500: {"description": "Internal server error"},
    },
)
async def ai_onboarding(
    payload: AIOnboardingRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> AIOnboardingResponse:
    """Handle POST /api/provider/ai_onboarding: read business facts from an image and/or prompt, generate listing copy and log the call.

    Raises:
        HTTPException: 400 for no input or an unusable image, 500 on unexpected errors.
    """
    received_at = now_ist_naive()
    try:
        result = await analyse_ai_onboarding(
            payload.business_image, payload.prompt, payload.conversation_id, payload.contact_id
        )
        details = result.Provider_Details
        if details.alternatemobile:
            try:
                alternate_conflict = await _alternate_mobile_conflict(details.mobilenumber, details.alternatemobile)
            except Exception as e:
                logger.warning("ai_onboarding | alternate mobile check failed; keeping it: %s", e, exc_info=True)
                alternate_conflict = None
            if alternate_conflict:
                logger.info(
                    "ai_onboarding | alternatemobile=*****%s is %s; left out",
                    details.alternatemobile[-4:], alternate_conflict,
                )
                result.response = " ".join(filter(None, [
                    result.response,
                    f"Sorry, the alternate number {details.alternatemobile} "
                    f"{_ALTERNATE_MOBILE_CONFLICTS[alternate_conflict]}, so I've left it out. "
                    "Could you please share a different alternate number?",
                ]))
                details.alternatemobile = None
                result.confirmation_required = True
        background_tasks.add_task(record_ai_onboarding, payload, result, received_at, now_ist_naive())
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("ai_onboarding | unexpected error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to analyse the business image")


@router.post(
    "/bubble",
    response_model=AIOnboardingBubbleResponse,
    status_code=200,
    summary="AI Onboarding Bubble: Apply a Follow-up Prompt and/or Image to Provider and Service Details",
    description=(
        "Follow-up to POST /provider/ai_onboarding. The body carries the Provider_Details and "
        "Service_Details from /ai_onboarding (or the previous /bubble response) plus a 'prompt' with "
        "the field agent's follow-up - e.g. 'change the second service price to 300', 'add AC gas "
        "refilling', 'add the items from this menu' or 'make the description shorter' - a new "
        "'business_image' (base64 or data-URL JPEG, PNG or WebP, 10 MB at most), or both. An image is "
        "uploaded to Azure Blob first and stored with the turn; a failed upload still analyses the image. "
        "Gemini Flash reads the current details, prompt and image together and applies only what the "
        "prompt asks for or the image shows - filling empty fields, correcting facts it clearly shows, "
        "updating prices and adding services it shows, never removing a service the image leaves "
        "out; the prompt wins where the two disagree. It returns Provider_Details and "
        "Service_Details in the same structure: untouched fields and services come back as sent, "
        "facts are taken only from the prompt and image, and new services get a generated description, type, "
        "quantity and expected price. Provider_Details uses the same fields as /ai_onboarding: "
        "mobilenumber and alternatemobile, latitude and longitude, and business_category ('product', "
        "'service' or 'product,service', recomputed from the services' types), plus Service_Type "
        "('Home Delivery', 'Store Delivery' or 'Both'), which the prompt can set, change or clear and is "
        "otherwise kept as sent. 'onboard' is stripped "
        "from the business name and Service_Quantity is a number for a product and '--' for a "
        "service. mobilenumber is never changed: it always comes back as sent, and a prompt asking to "
        "change it is declined in response. A new alternatemobile must be a valid 10-digit Indian "
        "mobile that no live provider is registered on (the GET /provider/unverified_provider_list "
        "lookup); otherwise, or when that lookup fails, the sent one is kept and response asks for "
        "another number. Removing the alternate mobile is applied as asked. A "
        "Business_Name change also replaces the old name in Description and every Service_Description. Each service's Service_Image_Url is kept as sent unless the prompt changes it: a "
        "photo the prompt says is for a service (e.g. 'add this image for AC repair') is linked to "
        "that service, replacing its image; a photo for an unnamed service, or with no prompt a "
        "photo of a single item, is linked to the best-matching service, and response asks which "
        "service when none fits; 'remove the image of X' clears it; a prompt referring to the "
        "earlier photo links the conversation's last stored image. Provider_Details.latitude and longitude are "
        "not shown to the AI: they are kept while Provider_Details.Address is unchanged and geocoded again when it "
        "changes or none were sent - null when the address is removed or cannot be found, with a "
        "note in response when a changed address cannot be found. response always carries a message: what was changed and, for every requested "
        "change that was not applied, which field and why - an unclear prompt, a service not in the "
        "list, a fact the image does not show, an unreadable image, a main mobile change, an invalid or already-used alternate mobile or a business_category "
        "that disagrees with the services' types. When nothing changed, response says so. When the AI "
        "gives no usable reply in two attempts the details come back unchanged with a message saying "
        "nothing was updated. When the prompt or image gives a business name that does not fit the "
        "description and services (e.g. a bakery name for a mobile repair shop), Business_Name is not "
        "changed: pending_business_name holds the new name and response asks the agent to confirm; a "
        "'yes' in the next turn of the same conversation applies it. confirmation_required is true "
        "whenever response asks the agent to confirm or provide something - that pending name, an "
        "unclear prompt, a refused alternate mobile, an address that cannot be found, a photo whose service is "
        "unclear or missing, or a missing detail - and false when everything asked was applied. "
        "conversation_id ties the turns together: send the one returned on every follow-up, or omit it "
        "on the first turn to get a new UUID. The last 10 turns of the conversation are given to the AI "
        "as context. Each turn is stored in etown_ai_onboarding_bubble as two rows sharing a turnid - "
        "a 'user' row (prompt_text, new image path, details sent, arrival time) and an 'assistant' row "
        "(response_text, whole response, pending name, reply time, response_time_ms), both with the "
        "caller's contact_id and user_profile_id - as a background task, so a failed insert never "
        "fails the call. The request carries no response, confirmation_required or "
        "pending_business_name; the confirmation state comes from the stored conversation. The provider tables are not written."
    ),
    responses={
        400: {"description": "business_image is not base64, not a JPEG/PNG/WebP, or over 10 MB"},
        422: {"description": "Neither prompt nor business_image sent, or Provider_Details/Service_Details malformed"},
        500: {"description": "Internal server error"},
    },
)
async def ai_onboarding_bubble(
    payload: AIOnboardingBubbleRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> AIOnboardingBubbleResponse:
    """Handle POST /api/provider/bubble: apply a follow-up prompt and/or image to AI onboarding details and store the turn.

    Raises:
        HTTPException: 400 for an unusable image, 500 on unexpected errors.
    """
    received_at = now_ist_naive()
    try:
        result, image_path = await apply_ai_onboarding_bubble(payload)
        background_tasks.add_task(record_ai_onboarding_bubble, payload, result, image_path, received_at, now_ist_naive())
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("ai_onboarding_bubble | unexpected error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to apply the follow-up to the listing")


@router.post(
    "/rating",
    response_model=AddRatingResponse,
    status_code=200,
    summary="Add Provider Rating",
    description="Submit a star rating for a completed booking. Updates etown_allrequests, derives provider from etown_requestservicelists, and creates/updates the etown_ratings aggregate row.",
    responses={
        200: {"description": "Rating submitted successfully", "model": AddRatingResponse},
        404: {"description": "Booking or service record not found"},
        500: {"description": "Internal server error"},
    },
)
async def add_rating(
    rating_data: AddRatingRequest,
    background_tasks: BackgroundTasks,
) -> AddRatingResponse:
    """Handle POST /api/provider/rating: rate a completed booking, update the provider's rating aggregate and mirror both.

    Raises:
        HTTPException: 404 when the booking is not found, 500 on server errors.
    """
    try:
        logger.info("add_rating | booking_id=%s rating_value=%s", rating_data.booking_id, rating_data.rating_value)
        israting = rating_data.israting or "True"
        result = await RatingService.add_rating(
            booking_id=rating_data.booking_id,
            rating_value=rating_data.rating_value,
            israting=israting,
        )
        logger.info("add_rating | complete | ratingid=%s", result.get("ratingid"))

        is_new = result.pop("_is_new", False)
        allrequestid = result.pop("_allrequestid", rating_data.booking_id)
        background_tasks.add_task(
            sync_allrequest_rating_to_dataverse,
            allrequestid,
            israting,
            rating_data.rating_value,
        )
        background_tasks.add_task(
            sync_rating_to_dataverse,
            result,
            result.get("providercontactid"),
            result.get("provideruserprofileid"),
            is_new,
        )

        return AddRatingResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("add_rating | unexpected error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Rating submission failed due to internal server error")


@router.post(
    "/notify_unverified",
    response_model=NotifyUnverifiedProviderResponse,
    status_code=200,
    summary="Notify Unverified Provider via WhatsApp",
    description=(
        "Send a one-time WhatsApp nudge to an unverified provider. "
        "Identify the provider either by both IDs (provider_userprofileid + provider_contactid) "
        "or by provider_mobilenumber + provider_name; the number must be a valid Indian mobile. "
        "Deduplication ensures each customer can trigger at most one notification per provider "
        "(per phone number when identified by mobile). "
        "Returns sent=False with a reason when the nudge is skipped."
    ),
    responses={
        200: {"description": "Result of the notification attempt", "model": NotifyUnverifiedProviderResponse},
        401: {"description": "Unauthorized — invalid or missing bearer token"},
        500: {"description": "Internal server error"},
    },
)
async def notify_unverified_provider(
    payload: NotifyUnverifiedProviderRequest,
    token_data: dict = Security(verify_token),
) -> NotifyUnverifiedProviderResponse:
    """Handle POST /api/provider/notify_unverified: send a deduplicated WhatsApp enquiry nudge to an unverified provider for a member.

    Raises:
        HTTPException: 500 on unexpected server errors.
    """
    try:
        logger.info(
            "notify_unverified_provider | customer=%s provider=%s mobile=%s",
            payload.customer_userprofileid,
            payload.provider_userprofileid,
            payload.provider_mobilenumber,
        )
        result = await send_unverified_provider_nudge(
            customer_userprofileid=payload.customer_userprofileid,
            customer_contactid=payload.customer_contactid,
            provider_userprofileid=payload.provider_userprofileid,
            provider_contactid=payload.provider_contactid,
            provider_mobilenumber=payload.provider_mobilenumber,
            provider_name=payload.provider_name,
        )
        return NotifyUnverifiedProviderResponse(**result)
    except Exception as e:
        logger.error("notify_unverified_provider | unexpected error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send provider notification")