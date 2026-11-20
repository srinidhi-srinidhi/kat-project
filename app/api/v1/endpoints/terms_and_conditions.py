"""Info Endpoints - HTTP routes for terms text, banners, file uploads and subscription status.

HTTP layer for /api/info: returns static English and Tamil terms, serves location banners from etown_banner, uploads files to Azure Blob, and fetches or upserts subscriptions with a background Dataverse sync. Only /upload checks the shared bearer token.

Features:
    - Terms and Conditions: Returns static English/Tamil terms and conditions text
    - Location Banners: Returns active banners, hiding onboarding banners from existing providers
    - Banner Image Upload: Uploads an image to Azure Blob's 'banners' folder and returns its URL
    - Generic File Upload: Stores any file in Azure Blob and/or returns its base64; writes no row
    - Subscription Tracking: Fetches or upserts a user's subscription, with background Dataverse sync

Endpoints:
    POST /terms_and_conditions: Returns the Etown terms and conditions text
    POST /banner: Returns the Etown banners (bannerid, type, image) for a location
    POST /banner_img: Uploads an image to the blob 'banners' folder and returns its full URL
    POST /upload: Uploads any file, returning its blob URL (blob=true), its base64 (base64=true), or both
    POST /subscription: Fetches in-progress subscriptions, or adds/updates a subscription status

Classes:
    SubscriptionRequest(): Subscription fetch/add-or-update request payload with optional fields
    BannerRequest(): Banner fetch request payload carrying the contact id and optional coordinates
    FileUploadResponse(): Upload result - always the type and size, plus the URL and/or the base64

Functions:
    get_terms_and_conditions(): Returns static terms and conditions response
    get_banner(): Returns banners from the etown_banner table
    banner_img(): Uploads an image file to the blob 'banners' folder and returns the public URL
    upload_file(): Reads one uploaded file of any type and stores it and/or returns its base64
    subscription(): Fetches in-progress subscriptions, or adds/updates one and syncs it to Dataverse in the background

Dependencies:
    - base64.b64encode: Encoding for the /upload base64 switch
    - fastapi: Router, BackgroundTasks, and multipart file upload handling
    - pydantic: Request and response models
    - app.core.logging: Centralized logging infrastructure
    - app.core.security.verify_token: shared bearer token dependency, guarding /upload
    - app.etown.termsandconditions.termsandconditions_utils: TAC text, banners, subscriptions, blob uploads
    - app.etown.termsandconditions.subscription_syncing: Background Dataverse sync for subscription rows

Updates:
    13-May-2026: Initial implementation of terms_and_conditions endpoint
    22-Jul-2026: Added POST /banner endpoint backed by the etown_banner table
    23-Jul-2026: contactid is mandatory on /banner; provider onboarding banners hide for role 1.
    27-Jul-2026: Added POST /banner_img, uploading to the blob banners folder.
    04-Sep-2026: Added POST /upload for any image, video or document.
"""
from base64 import b64encode

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.core.security import verify_token
from app.etown.termsandconditions.termsandconditions_utils import (
    ENGLISH_TAC,
    TAMIL_TAC,
    MAX_MEDIA_BYTES,
    add_or_update_subscription,
    fetch_banners,
    fetch_inprogress_subscriptions,
    resolve_media_content_type,
    upload_info_image,
    upload_media_file,
)
from app.etown.termsandconditions.subscription_syncing import upsert_subscription_to_dataverse

router = APIRouter()

UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


class SubscriptionRequest(BaseModel):
    """Subscription fetch request payload with optional fields."""

    currentuserprofileid: str | None = None
    opponentuserprofileid: str | None = None
    status: str | None = None
    fetch_status: str | None = None


class BannerRequest(BaseModel):
    """Banner fetch request payload carrying the contact and optional location."""

    contactid: str
    latitude: float | None = None
    longitude: float | None = None


class FileUploadResponse(BaseModel):
    """Result of POST /info/upload.

    filename, content_type and size_bytes are always present because they cost nothing and
    describe what the API actually received, which is what a client needs to confirm the
    upload was not truncated or mislabelled. Everything else is switch-dependent and left
    null when its switch is off, so a caller can tell "not requested" from "empty".
    """

    success: bool = Field(True, description="True when the file was read and every requested step ran")
    filename: str = Field(..., description="Filename as the client sent it")
    content_type: str = Field(..., description="Resolved MIME type: magic bytes, else the declared type, else the extension")
    size_bytes: int = Field(..., description="Size of the received file in bytes")
    url: str | None = Field(None, description="Absolute public https URL; null unless blob=true")
    blob_path: str | None = Field(None, description="Relative blob path '/{container}/{blob}'; null unless blob=true")
    base64: str | None = Field(None, description="Standard base64 of the file; null unless base64=true")
    data_url: str | None = Field(None, description="The base64 as a 'data:<type>;base64,...' URL; null unless base64=true")
    message: str = Field(..., description="Human-readable confirmation for display")


@router.post(
    "/terms_and_conditions",
    status_code=200,
    summary="Get Terms and Conditions",
    description="Returns the static Etown Terms and Conditions text. Pass an empty request body.",
    responses={
        200: {
            "description": "Terms and Conditions retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "english_TAC": {"termsAndConditions": "..."},
                        "tamil_TAC": {"termsAndConditions": "..."}
                    }
                }
            }
        }
    }
)
async def get_terms_and_conditions():
    """Handle POST /api/info/terms_and_conditions: return the static English and Tamil terms text."""
    return JSONResponse(
        status_code=200,
        content={
            "english_TAC": {"termsAndConditions": ENGLISH_TAC},
            "tamil_TAC": {"termsAndConditions": TAMIL_TAC},
        }
    )


@router.post(
    "/banner",
    status_code=200,
    summary="Get Banners",
    description="Returns the Etown banners (bannerid, type, image). Requires contactid; accepts optional latitude and longitude in the request body. Provider onboarding banners are hidden for contacts already onboarded as a provider (role = 1).",
    responses={
        200: {
            "description": "Banners retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "banners": [
                            {
                                "bannerid": "uuid-string",
                                "type": "promotion",
                                "image": "https://example.com/banner.png"
                            }
                        ]
                    }
                }
            }
        }
    }
)
async def get_banner(payload: BannerRequest):
    """Handle POST /api/info/banner: return the banners visible to a contact, or 400 without contactid."""
    contactid = (payload.contactid or "").strip()
    if not contactid:
        return JSONResponse(
            status_code=400,
            content={"message": "contactid is required."},
        )

    banners = await fetch_banners(contactid)
    return JSONResponse(
        status_code=200,
        content={
            "banners": banners,
        },
    )


@router.post(
    "/banner_img",
    status_code=200,
    summary="Upload Banner Image",
    description=(
        "Uploads an image to the 'banners' folder in Azure Blob Storage and returns its "
        "full public URL. Send the file as multipart/form-data under the field name 'image'. "
        "No image dimension restrictions are applied."
    ),
    responses={
        200: {
            "description": "Image uploaded successfully",
            "content": {
                "application/json": {
                    "example": {
                        "url": "https://account.blob.core.windows.net/etowndev/banners/20260727120000_ab12cd.png",
                        "blob_path": "/etowndev/banners/20260727120000_ab12cd.png",
                        "content_type": "image/png",
                        "size_bytes": 20481,
                        "filename": "banner.png"
                    }
                }
            }
        }
    }
)
async def banner_img(
    image: UploadFile = File(..., description="Image file to upload. No dimension restrictions."),
):
    """Handle POST /api/info/banner_img: upload an image to the blob banners folder and return its URL."""
    content = await image.read()

    if not content:
        return JSONResponse(
            status_code=400,
            content={"message": "Uploaded file is empty."},
        )

    try:
        result = await upload_info_image(
            content=content,
            filename=image.filename,
            content_type=image.content_type,
        )
    except Exception as exc:
        logger.error(
            "INFO_BANNER_IMG |Upload failed | filename=%s | error=%s",
            image.filename,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload image to blob storage.",
        )

    if not result.get("url"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload image to blob storage.",
        )

    logger.info(
        "INFO_BANNER_IMG |Uploaded | filename=%s | size=%s | url=%s",
        image.filename,
        result["size_bytes"],
        result["url"],
    )

    return JSONResponse(
        status_code=200,
        content={
            **result,
            "filename": image.filename,
        },
    )


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    status_code=200,
    summary="Upload A File",
    description=(
        "Uploads one image, video or document sent as multipart/form-data under the field "
        "name 'file'. Both switches are optional form fields and both may be sent together:\n\n"
        "- blob=true (the default): the file is stored in Azure Blob Storage and its full "
        "public URL comes back in 'url', with the relative path in 'blob_path'.\n"
        "- base64=true: the file's base64 comes back in 'base64', with a ready-to-embed "
        "data URL in 'data_url'.\n\n"
        "Send blob=false with base64=true to get the bytes back without storing anything. "
        "With both false the response carries the resolved type and size only. No file type "
        "is rejected; the limit is "
        f"{MAX_MEDIA_BYTES // (1024 * 1024)}MB per file. Base64 inflates the response to "
        "roughly 1.33x the file size, so it is a poor fit for large video."
    ),
    responses={
        200: {
            "description": "File processed",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "filename": "brochure.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 20481,
                        "url": "https://account.blob.core.windows.net/etowndev/media/brochure_20260904120000_ab12cd.pdf",
                        "blob_path": "/etowndev/media/brochure_20260904120000_ab12cd.pdf",
                        "base64": None,
                        "data_url": None,
                        "message": "File uploaded to blob storage."
                    }
                }
            }
        },
        400: {"description": "The uploaded file is empty"},
        413: {"description": "The file is larger than the size cap"},
        422: {"description": "No file was sent, or the body is not multipart/form-data"},
        500: {"description": "The file could not be stored in blob storage"}
    }
)
async def upload_file(
    file: UploadFile = File(..., description="The file to upload - any image, video or document"),
    blob: bool = Form(True, description="Upload to Azure Blob Storage and return the full public URL"),
    base64: bool = Form(False, description="Return the file's base64 and a data URL"),
    _: str = Depends(verify_token),
) -> FileUploadResponse:
    """Handle POST /api/info/upload: read one file and return its blob URL, its base64, or both.

    Args:
        file: The multipart file part.
        blob: When true, store the file and return its public URL.
        base64: When true, return the file's base64 and a data URL.
        _: Bearer token dependency; the credential itself is unused.

    Returns:
        FileUploadResponse with type and size, plus the URL and/or the base64.

    Raises:
        HTTPException: 400 empty file, 413 over MAX_MEDIA_BYTES, 500 blob upload failure.
    """
    filename = file.filename or "(unnamed)"

    logger.info(
        "INFO_UPLOAD |Received | filename=%s | declared_type=%s | blob=%s | base64=%s",
        filename, file.content_type, blob, base64,
    )

    def _reject(detail: str, code: int, reason: str) -> HTTPException:
        """Log the refusal and return the exception to raise, so no 4xx goes unlogged."""
        logger.warning(
            "INFO_UPLOAD |Rejected | code=%d | filename=%s | reason=%s",
            code, filename, reason,
        )
        return HTTPException(status_code=code, detail=detail)

    buffer = bytearray()
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > MAX_MEDIA_BYTES:
            raise _reject(
                f"File is larger than the {MAX_MEDIA_BYTES // (1024 * 1024)}MB limit.",
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "too_large",
            )
    content = bytes(buffer)

    if not content:
        raise _reject("Uploaded file is empty.", status.HTTP_400_BAD_REQUEST, "empty_file")

    url: str | None = None
    blob_path: str | None = None
    size_bytes = len(content)

    if blob:
        try:
            result = await upload_media_file(
                content=content,
                filename=file.filename,
                content_type=file.content_type,
            )
        except Exception:
            logger.error(
                "INFO_UPLOAD |Upload failed | filename=%s | bytes=%d",
                filename, size_bytes, exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload the file to blob storage.",
            )

        url = result["url"] or None
        blob_path = result["blob_path"] or None
        content_type = result["content_type"]

        if not url:
            logger.error(
                "INFO_UPLOAD |No URL | filename=%s | blob_path=%s | the upload returned no "
                "public URL", filename, blob_path,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload the file to blob storage.",
            )
    else:
        content_type = resolve_media_content_type(content, file.filename, file.content_type)

    encoded: str | None = None
    data_url: str | None = None
    if base64:
        encoded = b64encode(content).decode("ascii")
        data_url = f"data:{content_type};base64,{encoded}"

    if blob and base64:
        message = "File uploaded to blob storage and encoded as base64."
    elif blob:
        message = "File uploaded to blob storage."
    elif base64:
        message = "File encoded as base64; nothing was stored."
    else:
        message = "File read; nothing was stored or encoded. Send blob=true or base64=true."

    logger.info(
        "INFO_UPLOAD |Done | filename=%s | type=%s | bytes=%d | url=%s | base64=%s",
        filename, content_type, size_bytes, url or "-", bool(encoded),
    )

    return FileUploadResponse(
        success=True,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        url=url,
        blob_path=blob_path,
        base64=encoded,
        data_url=data_url,
        message=message,
    )


@router.post(
    "/subscription",
    status_code=200,
    summary="Subscription",
    description="Etown- Subscription Table for UserprofileId Fetch",
    responses={
        200: {
            "description": "Subscription data retrieved or upserted successfully",
            "content": {
                "application/json": {
                    "example": {
                        "subscriptions": [
                            {
                                "subscriptionsid": "uuid-string",
                                "current_userprofileid": "current-profile-id",
                                "current_userprofileidname": "Current User Name",
                                "opponent_userprofileid": "opponent-profile-id",
                                "opponent_userprofileidname": "Opponent User Name",
                                "subscriptionstatus": "inprogress"
                            }
                        ]
                    }
                }
            }
        }
    }
)
async def subscription(payload: SubscriptionRequest, background_tasks: BackgroundTasks):
    """Handle POST /api/info/subscription: fetch in-progress subscriptions or upsert one and mirror it to Dataverse."""
    if (payload.fetch_status or "").strip().lower() == "fetch":
        current_userprofileid = (payload.currentuserprofileid or "").strip()
        if not current_userprofileid:
            return JSONResponse(
                status_code=400,
                content={
                    "message": "currentuserprofileid is required when fetch_status is 'fetch'."
                },
            )

        subscriptions = await fetch_inprogress_subscriptions(current_userprofileid)
        return JSONResponse(
            status_code=200,
            content={
                "subscriptions": subscriptions,
            },
        )

    if (payload.fetch_status or "").strip().lower() == "addorupdate":
        current_userprofileid = (payload.currentuserprofileid or "").strip()
        opponent_userprofileid = (payload.opponentuserprofileid or "").strip()
        subscription_status = (payload.status or "").strip()

        if not current_userprofileid or not opponent_userprofileid or not subscription_status:
            return JSONResponse(
                status_code=400,
                content={
                    "message": "currentuserprofileid, opponentuserprofileid and status are required when fetch_status is 'addorupdate'."
                },
            )

        subscriptions = await add_or_update_subscription(
            current_userprofileid=current_userprofileid,
            opponent_userprofileid=opponent_userprofileid,
            status=subscription_status,
        )

        for subscription_row in subscriptions:
            background_tasks.add_task(upsert_subscription_to_dataverse, subscription_row)

        return JSONResponse(
            status_code=200,
            content={
                "subscriptions": subscriptions,
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "message": "Subscription endpoint created. Logic and final response will be added later.",
            "request_data": payload.model_dump(),
        },
    )
