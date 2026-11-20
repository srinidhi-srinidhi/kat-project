"""Offerings Endpoints - HTTP routes for image-based offering extraction and services-to-offerings conversion.

HTTP layer for /api/offerings: checks the shared bearer token, extracts structured offerings from an uploaded image with Gemini Vision, and runs the batch workflow that previews a provider's etown_providerservices rows as offerings in the background, then persists the user-confirmed set to etown_offerings.

Features:
    - Image Upload: Accepts multipart file uploads for offering image analysis
    - AI Vision Analysis: Extracts structured offering data from images via Gemini Vision
    - Provider Profile Building: Builds provider context for AI-guided extraction
    - Batch Services→Offerings Conversion: Queues per-provider preview generation (202, background)
    - Preview Polling: A batch-status endpoint the UI polls until previews are ready
    - Selective Confirm: Persists only the user-reviewed/edited offerings, per provider
    - Listing Cap Check: confirm checks max_listings before uploading any media
    - Data Normalization: Normalizes raw AI output to a consistent offering schema
    - File Size Formatting: Human-readable file size display in responses
    - Authentication: All routes require valid Bearer token via verify_token

Classes:
    ConvertServicesRequest(): Body for POST /convert-services — provider userprofileids and force flag
    ConvertServicesResponse(): 202 acknowledgement — batch_id, accepted/rejected provider ids
    ConvertServicePreview(): A generated, not-yet-confirmed offerings preview for one provider
    ConvertServiceJobStatus(): Per-provider status within a convert-services batch (with preview once ready)
    ConvertServicesBatchStatusResponse(): GET /convert-services/{batch_id} response — overall status + per-job status
    ConfirmedProviderOfferings(): One provider's user-confirmed/edited offerings, submitted to finalize
    ConfirmConvertServicesRequest(): Body for POST /convert-services/confirm — batch_id + confirmed providers
    ConfirmConvertServicesResultItem(): Outcome of confirming one provider (confirmed/failed/not_found/not_ready)
    ConfirmConvertServicesResponse(): Response for POST /convert-services/confirm — per-provider results

Functions:
    upload_image(): POST endpoint for business offering extraction from uploaded image
    _generate_previews_into_staging(): Background worker that builds and stores each provider's offerings preview
    convert_services(): POST endpoint queuing per-provider offering previews into staging (202)
    convert_services_status(): GET endpoint returning a batch's per-provider status and previews
    convert_services_confirm(): POST endpoint persisting reviewed offerings to etown_offerings and serviceitems

Dependencies:
    - fastapi: Router, file upload, dependency injection, and HTTP exception handling
    - pydantic: Request/response validation models
    - app.core.logging: Centralized logging for offering operations
    - app.core.security: Token verification for authenticated routes
    - app.db.client: Database access for the member-account pre-flight check and offering reads/writes
    - app.etown.offers.offerings_utils: AI extraction, normalization, and conversion-batch persistence
    - app.etown.provider.provider_services_utils: Provider service management, offering-id assignment, and media upload for new services
    - app.schemas.api: Pydantic models for image upload response

Updates:
    26-May-2026: Added full module docstring following project documentation standards
    15-Jun-2026: Restructured response to flat ImageUploadResponse with offerings_data, business_details, and meta at root level
    17-Jun-2026: Upgraded to schema v1.1 response shape.
    16-Jun-2026: Added convert-services endpoints for batch processing of provider offerings
    29-Jul-2026: convert_services_confirm() drops update_service_items_for_offerings; the mirror handles it.
    30-Jul-2026: convert_services_confirm() re-runs the mirror after inserting user-added offering rows.
    18-Aug-2026: convert_services_confirm() checks max_listings before uploading media, so a refused confirm orphans no blobs.
"""

import base64
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.core.security import verify_token
from app.db.client import Database
from app.etown.offers.offerings_utils import (
    build_provider_profile,
    build_service_conversion_preview,
    claim_conversion_job,
    create_conversion_jobs,
    extract_and_normalize,
    fetch_conversion_batch,
    format_file_size,
    record_conversion_confirmed,
    mirror_offering_tree_to_serviceitems,
    save_parsed_offerings_to_db,
    set_conversion_job_status,
)
from app.etown.provider.provider_services_utils import (
    ProviderServiceManagementService,
    assign_pydantic_offering_ids,
    check_listing_cap,
    map_parsed_offerings_to_service_details,
    upload_parsed_business_media,
)
from app.schemas.api import ImageUploadResponse, ParsedBusinessDetails, ParsedOfferingData

router = APIRouter(tags=["offerings"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024


@router.post("/image", response_model=ImageUploadResponse)
async def upload_image(
    image: UploadFile = File(..., description="Image file (jpg, png) max 10MB"),
    user_profile_id: str = Form(..., description="User profile ID for the image upload", examples=[""]),
    token: str = Depends(verify_token),
):
    """Handle POST /api/offerings/image: validate an uploaded image and extract a structured provider profile from it."""
    try:
        if not image.filename:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No file provided",
            )

        if image.content_type not in ALLOWED_IMAGE_TYPES:
            logger.warning("Invalid file type uploaded: %s", image.content_type)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file type. Allowed types: {', '.join(ALLOWED_IMAGE_TYPES)}",
            )

        image_data = await image.read()

        if len(image_data) > MAX_IMAGE_SIZE:
            logger.warning("File too large: %s bytes", len(image_data))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large. Maximum size: {MAX_IMAGE_SIZE / (1024 * 1024):.1f}MB",
            )

        if len(image_data) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty",
            )

        logger.info(
            "Successfully received image: %s, size: %s bytes, type: %s",
            image.filename,
            len(image_data),
            image.content_type,
        )

        size_info = format_file_size(len(image_data))
        image_base64 = base64.b64encode(image_data).decode("utf-8")

        structured_data = await extract_and_normalize(image.content_type, image_base64)
        profile_data = build_provider_profile(structured_data)

        return ImageUploadResponse(
            upload_info={
                "filename": image.filename,
                "file_size_bytes": size_info["file_size_bytes"],
                "file_size_formatted": size_info["file_size_formatted"],
                "file_type": image.content_type,
            },
            business_details=profile_data.get("business_details"),
            media_analysis=profile_data.get("media_analysis"),
            offering_data=profile_data.get("offering_data", []),
            offering_suggestions=profile_data.get("offering_suggestions", []),
            selected_offerings=[],
            processing_metadata=profile_data.get("processing_metadata"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error processing image upload: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error processing image upload",
        )


class ConvertServicesRequest(BaseModel):
    """Request body for POST /offerings/convert-services."""

    userprofileids: List[str] = Field(
        ...,
        min_length=1,
        description="One or more provider userprofileids whose legacy etown_providerservices "
                    "rows should be converted into the etown_offerings structure.",
        examples=[["<userprofileid-1>", "<userprofileid-2>"]],
    )
    force: bool = Field(
        default=False,
        description="Optional. When true, providers that already have an etown_offerings row "
                    "are deleted and re-synthesised from scratch. Default false = skip-if-exists.",
    )


class ConvertServicesResponse(BaseModel):
    """202 acknowledgement for a queued services→offerings preview batch."""

    batch_id: str = Field(
        description="Batch id — poll GET /offerings/convert-services/{batch_id} for previews, "
                    "then POST the reviewed offerings to /offerings/convert-services/confirm.",
    )
    status: str = Field(description="'accepted' if any provider was queued, else 'rejected'.")
    accepted: int = Field(description="Number of distinct provider userprofileids queued.")
    userprofileids: List[str] = Field(description="The distinct provider userprofileids queued for conversion.")
    member_accounts: List[str] = Field(
        default_factory=list,
        description="Submitted userprofileids that were rejected because they are member "
                    "accounts (not providers). These are NOT queued and get no offerings.",
    )
    message: str


class ConvertServicePreview(BaseModel):
    """A generated (not-yet-confirmed) offerings preview for one provider."""

    business_details: ParsedBusinessDetails = Field(
        ..., description="Provider/business details, ready to submit back on confirm."
    )
    offering_data: List[ParsedOfferingData] = Field(
        default_factory=list,
        description="Generated offering tree (offering → category → item). Each offering carries "
                    "the source providerservices_id so confirm can update the right service row.",
    )


class ConvertServiceJobStatus(BaseModel):
    """Per-provider status within a convert-services batch."""

    userprofileid: str
    status: str = Field(
        description="pending | processing | ready | confirmed | failed | skipped | member_account",
    )
    reason: Optional[str] = Field(None, description="Skip/failure reason when not ready/confirmed.")
    items_added: Optional[int] = Field(None, description="Items persisted (set once confirmed).")
    preview: Optional[ConvertServicePreview] = Field(
        None, description="Generated preview to review; present once status='ready'."
    )


class ConvertServicesBatchStatusResponse(BaseModel):
    """GET /offerings/convert-services/{batch_id} — the completion checkpoint for a batch."""

    batch_id: str
    overall_status: str = Field(
        description="processing (any pending/processing) | ready (some ready to confirm) | "
                    "completed (all resolved).",
    )
    counts: Dict[str, int] = Field(default_factory=dict, description="Job count per status.")
    jobs: List[ConvertServiceJobStatus] = Field(default_factory=list)


class ConfirmedProviderOfferings(BaseModel):
    """One provider's user-confirmed/edited offerings, submitted to finalize."""

    business_details: ParsedBusinessDetails = Field(
        ..., description="Carries providerid / userprofileid / contactid."
    )
    offering_data: List[ParsedOfferingData] = Field(
        default_factory=list,
        description="Confirmed offering tree. Offerings with a providerservices_id update that "
                    "service row's serviceitems; offerings without one are inserted as new services.",
    )


class ConfirmConvertServicesRequest(BaseModel):
    """Request body for POST /offerings/convert-services/confirm."""

    batch_id: str = Field(..., description="The batch the previews were generated in.")
    providers: List[ConfirmedProviderOfferings] = Field(
        ...,
        min_length=1,
        description="One entry per provider being confirmed. A subset of the batch is allowed "
                    "(per-provider selective confirm).",
    )


class ConfirmConvertServicesResultItem(BaseModel):
    """Outcome of confirming one provider."""

    userprofileid: str
    status: str = Field(description="confirmed | failed | not_found | not_ready (<status>)")
    items_added: Optional[int] = None
    reason: Optional[str] = None


class ConfirmConvertServicesResponse(BaseModel):
    """Response for POST /offerings/convert-services/confirm."""

    batch_id: str
    results: List[ConfirmConvertServicesResultItem] = Field(default_factory=list)


async def _generate_previews_into_staging(
    batch_id: str, userprofileids: List[str], force: bool = False
) -> None:
    """Build an offerings preview per provider into the staging batch, recording failures per row without raising."""
    summary: dict = {}
    for uid in userprofileids:
        try:
            await set_conversion_job_status(batch_id, uid, "processing")
            result = await build_service_conversion_preview(uid, force=force)
            key = result.get("status", "failed")
            await set_conversion_job_status(
                batch_id, uid, key,
                preview=result.get("preview"),
                reason=result.get("reason"),
            )
        except Exception as exc:
            key = "failed"
            logger.error("[convert-services] Unexpected error building preview for %s: %s", uid, exc, exc_info=True)
            try:
                await set_conversion_job_status(batch_id, uid, "failed", reason=str(exc)[:300])
            except Exception:
                logger.error("[convert-services] Could not record failure for %s", uid, exc_info=True)
        summary[key] = summary.get(key, 0) + 1
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(summary.items())) or "nothing processed"
    logger.info(
        "[convert-services] Batch %s preview generation complete (force=%s): %s (of %d)",
        batch_id, force, breakdown, len(userprofileids),
    )


@router.post(
    "/convert-services",
    response_model=ConvertServicesResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def convert_services(
    payload: ConvertServicesRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/offerings/convert-services: stage a preview batch for providers, rejecting member accounts.

    Raises:
        HTTPException: 422 when no valid userprofileid is supplied.
    """
    ids = list(dict.fromkeys(
        uid.strip() for uid in payload.userprofileids if uid and uid.strip()
    ))
    if not ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No valid userprofileids provided",
        )

    member_rows = await Database.fetch(
        """
        SELECT userprofileid FROM etown_userprofile
        WHERE userprofileid = ANY($1::text[])
          AND (role = 2 OR LOWER(COALESCE(rolename, '')) = 'member')
        """,
        ids,
    )
    member_accounts = sorted({r["userprofileid"] for r in member_rows})
    member_set = set(member_accounts)
    queued = [uid for uid in ids if uid not in member_set]

    batch_id = str(uuid.uuid4())
    await create_conversion_jobs(batch_id, queued, member_accounts, payload.force)

    if queued:
        background_tasks.add_task(_generate_previews_into_staging, batch_id, queued, payload.force)
    logger.info(
        "[convert-services] Batch %s: queued %d provider(s), rejected %d member account(s) (force=%s)",
        batch_id, len(queued), len(member_accounts), payload.force,
    )

    parts = []
    if queued:
        parts.append(
            f"Queued {len(queued)} provider(s) for preview generation. "
            f"Poll GET /offerings/convert-services/{batch_id}, then confirm."
        )
    if member_accounts:
        parts.append(f"Rejected {len(member_accounts)} member account(s).")

    return ConvertServicesResponse(
        batch_id=batch_id,
        status="accepted" if queued else "rejected",
        accepted=len(queued),
        userprofileids=queued,
        member_accounts=member_accounts,
        message=" ".join(parts),
    )


@router.get(
    "/convert-services/{batch_id}",
    response_model=ConvertServicesBatchStatusResponse,
)
async def convert_services_status(
    batch_id: str,
    token: str = Depends(verify_token),
):
    """Handle GET /api/offerings/convert-services/{batch_id}: return per-provider status, previews and overall status.

    Raises:
        HTTPException: 404 when the batch id is unknown.
    """
    rows = await fetch_conversion_batch(batch_id)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No conversion batch found for id {batch_id}",
        )

    counts: Dict[str, int] = {}
    jobs: List[ConvertServiceJobStatus] = []
    for r in rows:
        st = r.get("status") or "pending"
        counts[st] = counts.get(st, 0) + 1
        preview_obj = None
        preview_raw = r.get("preview")
        if preview_raw:
            try:
                preview_obj = ConvertServicePreview(**preview_raw)
            except Exception as exc:
                logger.warning(
                    "[convert-services] Preview parse failed for %s in batch %s: %s",
                    r.get("userprofileid"), batch_id, exc,
                )
        jobs.append(ConvertServiceJobStatus(
            userprofileid=r.get("userprofileid"),
            status=st,
            reason=r.get("reason"),
            items_added=r.get("items_added"),
            preview=preview_obj,
        ))

    if counts.get("pending") or counts.get("processing"):
        overall = "processing"
    elif counts.get("ready"):
        overall = "ready"
    else:
        overall = "completed"

    return ConvertServicesBatchStatusResponse(
        batch_id=batch_id,
        overall_status=overall,
        counts=counts,
        jobs=jobs,
    )


@router.post(
    "/convert-services/confirm",
    response_model=ConfirmConvertServicesResponse,
)
async def convert_services_confirm(
    payload: ConfirmConvertServicesRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/offerings/convert-services/confirm: claim and persist each provider's reviewed offerings, reverting failures to ready."""
    results: List[ConfirmConvertServicesResultItem] = []

    for prov in payload.providers:
        bd = prov.business_details
        uid = (bd.userprofileid or "").strip()
        claimed: Optional[Dict[str, Any]] = None
        try:
            claimed = await claim_conversion_job(payload.batch_id, uid)
            if not claimed["ok"]:
                results.append(ConfirmConvertServicesResultItem(
                    userprofileid=uid, status=claimed["reason"] or "not_ready",
                ))
                continue

            provider_id = (bd.providerid or "").strip()
            contact_id = (bd.contactid or "").strip()

            assign_pydantic_offering_ids(prov.offering_data)

            new_offs = [o for o in prov.offering_data if not (o.providerservices_id or "").strip()]
            if new_offs and uid and provider_id:
                await check_listing_cap(
                    uid, provider_id, [(o.offering_name or "").strip() for o in new_offs]
                )

            await upload_parsed_business_media(bd, prov.offering_data)

            if claimed["force"]:
                await Database.execute(
                    "DELETE FROM etown_offerings WHERE userprofileid = $1", uid
                )

            items_added = await save_parsed_offerings_to_db(
                bd.model_dump(),
                [o.model_dump() for o in prov.offering_data],
            )

            if new_offs:
                servicedetails = map_parsed_offerings_to_service_details(new_offs)
                if servicedetails:
                    _msg, enhancements, provider = (
                        await ProviderServiceManagementService.add_services_to_provider_workflow(
                            contact_id=contact_id,
                            userprofile_id=uid,
                            provider_id=provider_id,
                            servicedetails=servicedetails,
                        )
                    )
                    if enhancements and provider:
                        background_tasks.add_task(
                            ProviderServiceManagementService.run_background_enhancements,
                            provider=provider,
                            contact_id=contact_id,
                            userprofile_id=uid,
                            service_enhancements=enhancements,
                        )
                    stored = await Database.fetchrow(
                        "SELECT items FROM etown_offerings WHERE userprofileid = $1", uid
                    )
                    if stored:
                        raw_tree = stored["items"]
                        tree = json.loads(raw_tree) if isinstance(raw_tree, str) else (raw_tree or [])
                        if isinstance(tree, list) and tree:
                            await mirror_offering_tree_to_serviceitems(uid, tree)

            try:
                await record_conversion_confirmed(payload.batch_id, uid, items_added)
            except Exception:
                logger.error(
                    "[convert-services] Confirmed %s (batch %s) but failed to record items_added",
                    uid, payload.batch_id, exc_info=True,
                )
            results.append(ConfirmConvertServicesResultItem(
                userprofileid=uid, status="confirmed", items_added=items_added,
            ))
            logger.info(
                "[convert-services] Confirmed provider %s (batch %s): items_added=%d",
                uid, payload.batch_id, items_added,
            )

        except Exception as exc:
            logger.error(
                "[convert-services] Confirm failed for %s (batch %s): %s",
                uid, payload.batch_id, exc, exc_info=True,
            )
            if claimed and claimed.get("ok"):
                try:
                    await set_conversion_job_status(
                        payload.batch_id, uid, "ready",
                        reason=f"confirm failed: {str(exc)[:200]}",
                    )
                except Exception:
                    logger.error(
                        "[convert-services] Could not revert job %s to ready", uid, exc_info=True
                    )
            results.append(ConfirmConvertServicesResultItem(
                userprofileid=uid, status="failed", reason=str(exc)[:300],
            ))

    return ConfirmConvertServicesResponse(batch_id=payload.batch_id, results=results)
