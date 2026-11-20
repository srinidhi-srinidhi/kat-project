"""Booking Endpoints - HTTP routes for booking updates, instructions, request lists and communications.

HTTP layer for /api/book: validates requests, checks the shared bearer token and delegates status and provider updates, typed instructions, servicetype changes, request lists, frequently-visited providers and communication history to booking_utils.

Features:
    - Request Status Update: Change booking request status (accepted, rejected, cancelled)
    - Accept-Time Terms: Optionally record the agreed price and service date/time on that update
    - Provider Assignment: Link provider details to a booking request
    - Location Tracking: Update provider's current address and coordinates
    - Booking Count Sync: Update accepted and requested booking counters
    - Cancel Reason Recording: Store provider cancellation reasons
    - All-Requests List: Paginated booking list for members and providers with optional filters
    - Frequently-Visited List: Member's providers ranked by how many requests they made to each
    - Communication History: Every notification Etown sent about a booking, plus the member's instructions

Classes:
    BookUpdateRequest: Pydantic model for booking update request payload
    AdditionalInfoRequest: One typed booking instruction; validates type and non-empty content
    ServiceTypeUpdateRequest: Pydantic model for the servicetype update request payload

Endpoints:
    POST /book/update: Update booking request details including status and provider info
    POST /book/additional_info: Append one typed instruction to a booking, notifying the other party
    POST /book/servicetype: Update the servicetype field on a booking, keyed by bookingid
    GET /book/communications: Notification history and member instructions for one booking
    GET /book/request_list: Paginated booking list for a member or provider with optional date/id filters
    GET /book/frequently_visited: Member's frequently-visited providers ranked by request count

Functions:
    update_booking(): Process and persist booking update from provider or member action
    add_additional_info(): Store one typed instruction against a booking and notify the other party
    update_servicetype(): Update the servicetype field on a booking, keyed by bookingid
    get_booking_communications(): Retrieve what Etown sent about a booking, and when
    get_request_list(): Retrieve paginated bookings for the authenticated contact
    get_frequently_visited(): Retrieve the member's frequently-visited providers ranked by request count

Dependencies:
    - fastapi: API framework and routing
    - pydantic: Request validation models
    - app.etown.booking.booking_utils: BookingService, AllRequestsListService, FrequentlyVisitedService, INSTRUCTION_TYPES
    - app.core.security: Bearer token authentication
    - app.services.api_activity: track_api_call on /request_list
    - app.schemas.api: AllRequestsListResponse, FrequentlyVisitedItem, ProviderBookingItem, ProviderBookingDetails, ProviderServiceDetail

Updates:
    29-May-2026: Initial creation with update_booking endpoint
    05-Jun-2026: Updated documentation with complete features and endpoint descriptions
    24-Jun-2026: Added language field to BookUpdateRequest for WhatsApp notification language selection
    03-Jul-2026: Added GET /request_list endpoint (PowerApps migration)
    14-Jul-2026: Added GET /frequently_visited endpoint (PowerApps FrequentlyVisitViewAPI migration)
    06-Aug-2026: BookUpdateRequest accepts optional price and service_datetime when accepting a booking.
    18-Aug-2026: /additional_info stores the sender's userprofileid/contactid instead of their name and image.
    19-Aug-2026: /additional_info notifies the other party; takes type + content; accepts video and document payloads.
    20-Aug-2026: /request_list and /frequently_visited return Etown_providertype.
    21-Aug-2026: /additional_info push titled "<name> (Booking message)"; optional video thumbnail uploaded to blob.
    02-Sep-2026: GET /request_list BookingDetails include Etown_disabled for the service provider.
"""
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator

from app.core.security import verify_token
from app.etown.booking.booking_utils import (
    INSTRUCTION_TYPES,
    AllRequestsListService,
    BookingService,
    FrequentlyVisitedService,
)
from app.schemas.api import (
    AllRequestsListResponse,
    FrequentlyVisitedItem,
    ProviderBookingDetails,
    ProviderBookingItem,
    ProviderServiceDetail,
)
from app.services.api_activity import track_api_call

router = APIRouter()


class BookUpdateRequest(BaseModel):
    request_row_id: Optional[str] = None
    request_Status: Optional[str] = None
    service_provider_id: Optional[str] = None
    service_provideruserprofileid: Optional[str] = None
    cancel_reason: Optional[str] = None
    service_provider_name: Optional[str] = None
    service_provider_latitude: Optional[str] = None
    service_provider_longitude: Optional[str] = None
    update_RequestedBookings: Optional[str] = None
    update_AcceptedBookings: Optional[str] = None
    serviceprovider_currentAddress: Optional[str] = None
    serviceprovider_currentAddress_latitude: Optional[str] = None
    serviceprovider_currentAddress_longitude: Optional[str] = None
    Oauth: Optional[str] = None
    OTP: Optional[str] = None
    role: Optional[str] = None
    language: str = "en"
    price: Optional[str] = None
    service_datetime: Optional[str] = None


class AdditionalInfoRequest(BaseModel):
    """One typed instruction to append to a booking.

    `type` says what `content` holds: "text" carries the words themselves, while
    "image", "video" and "document" carry a base64 / data-url payload that is uploaded
    to blob storage. One call appends one entry.

    A video may also carry `thumbnail` - its poster frame as base64, uploaded to blob
    like an offer's or a feed post's thumbnail. It is stored on the entry and becomes the
    clip's preview in the booking APIs and the other party's push; without it a video
    entry simply has none.
    """
    bookingid: str
    userprofileid: Optional[str] = None
    type: str
    content: str
    thumbnail: Optional[str] = None

    @model_validator(mode="after")
    def validate_instruction_payload(self):
        """Lowercase and validate the instruction type and require non-empty content."""
        clean_type = str(self.type or "").strip().lower()
        if clean_type not in INSTRUCTION_TYPES:
            raise ValueError(f"type must be one of: {', '.join(INSTRUCTION_TYPES)}")
        if not str(self.content or "").strip():
            raise ValueError("content is required.")

        self.type = clean_type
        return self


class ServiceTypeUpdateRequest(BaseModel):
    bookingid: str
    servicetype: Optional[str] = None


@router.post("/update")
async def update_booking(request: BookUpdateRequest):
    """Handle POST /api/book/update: apply a booking status or detail update via BookingService."""
    return await BookingService.update_booking(request.model_dump())


@router.post("/additional_info")
async def add_additional_info(request: AdditionalInfoRequest):
    """Handle POST /api/book/additional_info: append one text, image, video or document instruction to a booking."""
    return await BookingService.add_additional_info(
        booking_id=request.bookingid,
        userprofileid=request.userprofileid,
        instruction_type=request.type,
        content=request.content,
        thumbnail=request.thumbnail,
    )


@router.get("/communications", dependencies=[Depends(verify_token)])
async def get_booking_communications(
    bookingid: str = Query(..., description="The booking's allrequestid"),
):
    """Handle GET /api/book/communications: list what Etown sent about a booking plus the member's instructions."""
    return await BookingService.get_booking_communications(booking_id=bookingid)


@router.post("/servicetype")
async def update_servicetype(request: ServiceTypeUpdateRequest):
    """Handle POST /api/book/servicetype: update the servicetype on an existing booking."""
    return await BookingService.update_servicetype(
        booking_id=request.bookingid,
        servicetype=request.servicetype,
    )


@router.get("/request_list", dependencies=[Depends(verify_token)])
async def get_request_list(
    contactid: str = Query(..., description="Contact ID of the requesting user"),
    userprofileid: str = Query(..., description="User profile ID of the requesting user"),
    page_number: int = Query(0, ge=0, description="Zero-based page number"),
    start_date: Optional[str] = Query(None, description="Filter: bookings on or after this date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Filter: bookings on or before this date (YYYY-MM-DD)"),
    start_time: Optional[str] = Query(None, description="Filter: bookings at or after this timestamp"),
    end_time: Optional[str] = Query(None, description="Filter: bookings at or before this timestamp"),
    date: Optional[str] = Query(None, description="Filter: bookings on exactly this date (YYYY-MM-DD)"),
    first_count: Optional[int] = Query(None, ge=1, description="Page size (use with ascending order context)"),
    last_count: Optional[int] = Query(None, ge=1, description="Page size (use with descending order context)"),
    request_id: Optional[str] = Query(None, description="Filter: exact match on booking track ID"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> AllRequestsListResponse:
    """Handle GET /api/book/request_list: return a filtered, paginated booking list for a member or provider.

    Args:
        contactid: Contact ID of the requesting user.
        userprofileid: User profile ID of the requesting user.
        page_number: Zero-based page index.
        start_date: Lower-bound date filter (YYYY-MM-DD).
        end_date: Upper-bound date filter (YYYY-MM-DD).
        start_time: Lower-bound timestamp filter.
        end_time: Upper-bound timestamp filter.
        date: Exact date filter (YYYY-MM-DD).
        first_count: Page size when last_count is absent; default page size is 10.
        last_count: Page size; takes precedence over first_count.
        request_id: Exact booking track ID filter.
        background_tasks: Queue for the API activity counter.

    Returns:
        AllRequestsListResponse with RequestDetails and TotalCount.

    Raises:
        HTTPException: 500 when the lookup fails.
    """
    track_api_call(background_tasks, "/api/book/request_list", contactid, userprofileid)
    try:
        page_size = last_count or first_count or 10

        result = await AllRequestsListService.get_all_requests_list(
            contactid=contactid,
            userprofileid=userprofileid,
            page_number=page_number,
            page_size=page_size,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            end_time=end_time,
            date=date,
            request_id=request_id,
        )

        request_details = [
            ProviderBookingItem(
                BookingDetails=ProviderBookingDetails(**b["BookingDetails"]),
                ServiceDetails=[ProviderServiceDetail(**s) for s in b["ServiceDetails"]],
            )
            for b in result["bookings"]
        ]

        return AllRequestsListResponse(
            RequestDetails=request_details,
            TotalCount=result["total_count"],
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to retrieve request list")

@router.get("/frequently_visited", dependencies=[Depends(verify_token)])
async def get_frequently_visited(
    requestor_id: str = Query(..., description="Requestor's contact ID (etown_allrequests.requestorid)"),
    requestor_userprofile_id: str = Query(..., description="Requestor's user profile ID (etown_allrequests.requestoruserprofileid)"),
) -> List[FrequentlyVisitedItem]:
    """Handle GET /api/book/frequently_visited: list the member's requested provider-services ranked by visit count."""
    try:
        rows = await FrequentlyVisitedService.get_frequently_visited(
            requestor_id=requestor_id,
            requestor_userprofileid=requestor_userprofile_id,
        )
        return [FrequentlyVisitedItem(**r) for r in rows]

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to retrieve frequently visited providers")
