"""Offers Endpoints - HTTP routes for circle offers, offer creation, views and deletion.

HTTP layer for /api/offers: checks the shared bearer token and delegates to offers_utils to list circle offers grouped by provider, create plan-capped offers with media upload and notification fan-out, track views and delete offers, mirroring writes to Dataverse.

Features:
    - Circle offers: Social network based offer retrieval, grouped by provider
    - Offer creation: Media upload to Azure Blob, phonebook/group distribution, and notification fan-out
    - Offer tracking: View history and engagement analytics
    - Story grouping: WhatsApp-style provider-grouped display with unviewed-first ordering
    - Social filtering: Member and provider relationship integration
    - View management: Offer interaction tracking
    - Personalization: User-specific offer customization
    - Deduplication: Unique offer presentation
    - Performance optimization: Parallel database operations

Endpoints:
    GET /myoffers: Retrieve offers from user's social circle, grouped by provider
    POST /viewed: Track offer view interactions
    GET /viewcount: Get the view count for a specific offer
    POST /create: Create a new offer, upload media, and send notifications
    DELETE /{offersid}: Delete an offer by its offersid
    GET /offerscontent_by_offersid: Retrieve offer content joined with provider city/statecode
    GET /valid_and_pending_offers: Retrieve all Valid and Pending offers for a user

Classes:
    PhonebookContact(): One phonebook entry (name, phone, optional contact_id) for offer distribution
    OfferCreateRequest(): Body for POST /create — full offer payload including media, scheduling, and targeting

Functions:
    _log_sms_result(): asyncio task-done callback that logs a failed/False offer SMS send
    get_offers(): Fetch circle-based offers grouped by provider (WhatsApp story style)
    mark_offer_as_viewed(): Record offer viewing interactions
    get_offer_view_count(): Return the view count for a specific offer
    create_offer(): Create a new offer, upload its media, and dispatch notifications
    delete_offer(): Delete an offer and mirror the deletion to Dataverse
    offerscontent_by_offersid(): Retrieve offer content joined with provider city and statecode
    get_valid_and_pending_offers(): Retrieve all Valid and Pending offers for a user profile

Dependencies:
    - fastapi: API framework, background tasks, and query parameters
    - app.core.security: shared bearer-token verification
    - app.core.logging: Centralized logging for offer operations
    - app.db.client: Direct queries for offer existence checks and deletion
    - app.etown.offers.offers_utils: Offer business logic, rate limiting and notification fan-out
    - app.etown.offers.offers_syncing: Dataverse mirroring for offer create/delete
    - app.etown.plans.plans_utils: PlanLimitError for plan-capped offer creation
    - app.services.api_activity: API call tracking
    - app.schemas.api: Offer response models and validation

Updates:
    23-Feb-2026: Enhanced documentation with social circle features
    05-Jun-2026: Changed myoffers to return provider-grouped offers (WhatsApp story style)
    20-Aug-2026: GET /myoffers groups and offers carry Etown_providertype
    26-Aug-2026: Logged previously-silent exception handlers with tracebacks.

Notes:
    Combines member and provider relationships for comprehensive social circles.
    Uses parallel database queries for optimal performance and user experience.
"""

import asyncio
from fastapi import APIRouter, BackgroundTasks, Query, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from app.core.security import verify_token
from app.core.config import settings
from app.core.logging import logger
from app.schemas.api import OfferItem, ProviderOfferGroup, ViewedOfferRequest, ValidPendingOfferItem, OfferContentItem, OfferViewCountResponse
from app.etown.offers.offers_utils import (
    fetch_mycircle_offers,
    fetch_mycircle_offers_grouped,
    add_viewed_offer,
    get_offer_viewcount,
    upload_offer_media,
    insert_offer_record,
    format_offer_row,
    create_offer_full,
    OfferRateLimitError,
    send_offer_circle_notifications,
    send_offer_circle_messages,
    check_user_offer_rate_limit,
    get_group_member_userprofile_ids,
    send_offer_group_notifications,
    fetch_valid_and_pending_offers,
    is_provider_boosted,
    send_boosted_provider_broadcast,
    get_circle_user_profile_ids,
    fetch_offer_content_by_unicid,
)
from app.db.client import Database
from app.etown.plans.plans_utils import PlanLimitError
from app.etown.offers.offers_syncing import delete_offer_from_dataverse, insert_offer_to_dataverse
from app.etown.notifications.core.dispatcher import dispatch
from app.etown.notifications.core.models import NotificationRequest, Recipient
from app.etown.notifications.core.registry import NotificationType
from app.etown.notifications.service import GovernanceRequest, send as gov_send
from app.services.api_activity import track_api_call

router = APIRouter()


def _log_sms_result(task: asyncio.Task) -> None:
    """Log a warning when an offer SMS task returned False and an error when it raised."""
    try:
        result = task.result()
        if result is False:
            logger.warning("Offer SMS send returned false")
    except Exception as exc:
        logger.error("Offer SMS send failed: %s", exc, exc_info=True)


class PhonebookContact(BaseModel):
    name: Optional[str] = None
    phone: str
    contact_id: Optional[Any] = None

class OfferCreateRequest(BaseModel):
    contact_id: str
    userprofile_id: str
    title: Optional[str] = None
    businessname: str
    businessprofileimagefile: Optional[str] = None
    description: str
    thumbnail_file: Optional[str] = None
    Oauth: Optional[str] = Field(None, description="OAuth token for the FCM push fan-out")
    tags: Optional[str] = None
    tags_groups: Optional[List[Any]] = None
    offerfile1: Optional[str] = None
    offerfile2: Optional[str] = None
    offerfile3: Optional[str] = None
    offerfile4: Optional[str] = None
    offerfile5: Optional[str] = None
    offervideofile: Optional[str] = None
    scheduled_time: Optional[str] = None
    color_code: Optional[str] = None
    validity: Optional[str] = None
    offer_type: Optional[str] = None
    createdtime: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    fontfamily: Optional[str] = None
    fontsize: Optional[str] = None
    fontstyle: Optional[str] = None
    language_code: Optional[str] = None
    phonebook_contacts: Optional[List[PhonebookContact]] = None
    groupuniqueid: Optional[str] = None

@router.get("/myoffers", response_model=List[ProviderOfferGroup])
async def get_offers(
    contact_id: str = Query(..., description="The contact ID of the user requesting offers."),
    user_profile_id: str = Query(..., description="The user profile ID of the user requesting offers."),
    page: int = Query(1, ge=1, description="Page number for pagination."),
    limit: int = Query(100, ge=1, description="Number of offers to fetch per page before grouping."),
    token: str = Depends(verify_token),
):
    """Handle GET /api/offers/myoffers: return a page of MyCircle providers' offers grouped by provider."""
    try:
        offset = (page - 1) * limit
        return await fetch_mycircle_offers_grouped(contact_id, user_profile_id, limit, offset)
    except Exception as e:
        logger.error(f"Error fetching offers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch offers")

@router.post("/viewed", response_model=Dict[str, Any])
async def mark_offer_as_viewed(
    request: ViewedOfferRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token)
):
    """Handle POST /api/offers/viewed: record a profile's view of an offer, returning 404 when the update fails."""
    try:
        result = await add_viewed_offer(request.offersid, request.userprofileid)
        
        if result.get("status") == "error":
            return JSONResponse(status_code=404, content=result)

        track_api_call(
            background_tasks, "/api/offers/viewed", userprofile_id=request.userprofileid
        )
        return result
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error marking offer as viewed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update view status")


@router.get("/viewcount", response_model=OfferViewCountResponse)
async def get_offer_view_count(
    offersid: str = Query(..., description="The ID of the offer."),
    token: str = Depends(verify_token),
):
    """Handle GET /api/offers/viewcount: return an offer's view count, or 404 when the offer is missing."""
    try:
        count = await get_offer_viewcount(offersid)
        if count is None:
            raise HTTPException(status_code=404, detail="Offer not found")
        return OfferViewCountResponse(offersid=offersid, viewcount=count)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching offer view count: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch view count")


@router.post("/create", response_model=OfferItem, status_code=status.HTTP_201_CREATED)
async def create_offer(
    request: OfferCreateRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/offers/create: create an offer with its uploaded media and schedule its notifications.

    Raises:
        HTTPException: 429 on rate limit, 402 on plan limit, 400 on validation, 500 otherwise.
    """
    try:
        phonebook_contacts = (
            [{"name": c.name, "phone": c.phone} for c in request.phonebook_contacts]
            if request.phonebook_contacts
            else None
        )
        created_offer = await create_offer_full(
            contact_id=request.contact_id,
            userprofile_id=request.userprofile_id,
            businessname=request.businessname,
            description=request.description,
            title=request.title,
            businessprofileimagefile=request.businessprofileimagefile,
            thumbnail_file=request.thumbnail_file,
            offerfile1=request.offerfile1,
            offerfile2=request.offerfile2,
            offerfile3=request.offerfile3,
            offerfile4=request.offerfile4,
            offerfile5=request.offerfile5,
            offervideofile=request.offervideofile,
            color_code=request.color_code,
            validity=request.validity,
            offer_type=request.offer_type,
            createdtime=request.createdtime,
            scheduled_time=request.scheduled_time,
            latitude=request.latitude,
            longitude=request.longitude,
            fontfamily=request.fontfamily,
            fontsize=request.fontsize,
            fontstyle=request.fontstyle,
            language_code=request.language_code,
            tags=request.tags,
            oauth_token=request.Oauth,
            phonebook_contacts=phonebook_contacts,
            groupuniqueid=request.groupuniqueid,
            schedule=background_tasks.add_task,
        )
    except OfferRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except PlanLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e),
        )
    except ValueError as e:
        logger.error("Offer creation validation error: %s", e, exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating offer: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create offer",
        )

    track_api_call(
        background_tasks, "/api/offers/create", request.contact_id, request.userprofile_id
    )
    return created_offer


@router.delete("/{offersid}", response_model=Dict[str, Any], status_code=status.HTTP_200_OK)
async def delete_offer(
    offersid: str,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle DELETE /api/offers/{offersid}: hard-delete an offer in Postgres and mirror the delete to Dataverse."""
    existing = await Database.fetchrow(
        "SELECT offersid FROM etown_offers WHERE offersid = $1",
        offersid,
    )
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Offer not found",
        )

    try:
        await Database.execute(
            "DELETE FROM etown_offers WHERE offersid = $1",
            offersid,
        )
        logger.info("Offer deleted: offersid=%s", offersid)
        background_tasks.add_task(delete_offer_from_dataverse, offersid)
        return {"status": "success", "message": "Offer deleted successfully"}
    except Exception as e:
        logger.error("Error deleting offer offersid=%s: %s", offersid, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete offer",
        )


@router.get("/offerscontent_by_offersid", response_model=OfferContentItem)
async def offerscontent_by_offersid(
    offerunicid: str = Query(..., description="The unique offer ID (offerunicid) to retrieve content for."),
    token: str = Depends(verify_token),
):
    """Handle GET /api/offers/offerscontent_by_offersid: return an offer by offerunicid with provider city and statecode."""
    result = await fetch_offer_content_by_unicid(offerunicid)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")
    return result


@router.get("/valid_and_pending_offers", response_model=List[ValidPendingOfferItem])
async def get_valid_and_pending_offers(
    contact_id: str = Query(..., description="The contact ID of the user."),
    userprofile_id: str = Query(..., description="The user profile ID of the user."),
    token: str = Depends(verify_token),
):
    """Handle GET /api/offers/valid_and_pending_offers: return a profile's Valid and Pending offers; contact_id is unused."""
    try:
        results = await fetch_valid_and_pending_offers(userprofile_id)
        return results
    except Exception as e:
        logger.error("Error fetching valid/pending offers for userprofile_id=%s: %s", userprofile_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch valid and pending offers",
        )
