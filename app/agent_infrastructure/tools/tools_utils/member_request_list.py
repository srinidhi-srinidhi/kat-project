"""Member Request List Tool - a member's own booking and request history.

Member-agent tool behind get_member_requests_list: fetches requests through AllRequestsListService, adds parsed city and state, and returns a structured payload for the app plus a text summary for the agent, with a most-recent-booking shortcut for repeat booking.

Features:
    - Paginated request history via the native service, with tracking id and date filters
    - Per-row Direction: request_list_common labels each row by whether the caller requested or provided it
    - Recent-booking shortcut returning just the latest booking plus repeat-booking guidance
    - Address enrichment deriving city and state from free-form address strings
    - Shared response wording, with agent-only paging kept in a strippable directive
    - Errors are caught and logged, returning a user-facing message rather than raising

Classes:
    No classes defined (module uses function-based request utilities)

Functions:
    parse_booking_date(): Parse a booking date against the known API formats
    get_recent_booking_simple(): Return just the member's most recent booking
    extract_city_state(): Extract city and state from an address string
    handle_member_request_getlist(): Fetch, enrich and format the member's requests
    _enrich_request_addresses(): Derive requestor and provider city and state, in place

Dependencies:
    - app.etown.booking.booking_utils.AllRequestsListService: the in-house request list
    - app.agent_infrastructure.tools.tools_utils.request_list_common: response wording
    - app.core.logging: structured logging

Updates:
    11-Mar-2026: Updated module docstring to follow project standards with comprehensive documentation
    16-Jul-2026: Calls AllRequestsListService.get_all_requests_list in-process instead of the PowerApps flow.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    04-Sep-2026: Response text moved to request_list_common with member-facing wording.
"""

from typing import Dict, Any, Optional, Tuple
import re
from datetime import datetime

from app.core.logging import logger
from app.etown.booking.booking_utils import AllRequestsListService
from app.agent_infrastructure.tools.tools_utils.request_list_common import (
    empty_request_list_message,
    format_request_list,
)


def parse_booking_date(date_str: str) -> datetime:
    """Parse a booking date string in any of several known formats, returning datetime.min on failure."""
    if not date_str:
        return datetime.min
    
    date_formats = [
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y",
    ]
    
    for date_format in date_formats:
        try:
            return datetime.strptime(str(date_str).strip(), date_format)
        except (ValueError, TypeError):
            continue
    
    logger.warning(f"Failed to parse booking date: {date_str}")
    return datetime.min


async def get_recent_booking_simple(
    contact_id: str,
    user_profile_id: str,
    mobilenumber_accesstoken: str,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Fetch the member's latest 20 requests and return a message and data for the most recently created one.

    Args:
        contact_id: Member's contact ID.
        user_profile_id: Member's user profile ID.
        mobilenumber_accesstoken: Mobile number access token, passed through to the list call.

    Returns:
        Tuple of (response message, recent booking data or None).
    """
    try:
        logger.info(f"Getting recent booking for user {user_profile_id}")
        
        result_text, full_data = await handle_member_request_getlist(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            mobilenumber_accesstoken=mobilenumber_accesstoken,
            first_count=20
        )
        
        if not full_data or not full_data.get("RequestDetails"):
            return "You haven't made any bookings yet. Would you like to search for services?", None
        
        request_details = full_data.get("RequestDetails", [])
        
        request_details.sort(
            key=lambda x: parse_booking_date(x.get('Etown_bookingcreatedon', '')),
            reverse=True
        )
        
        recent_booking = request_details[0]
        
        business_name = recent_booking.get("Etown_ServiceProviderBusinessname", "Unknown Business")
        booking_date = recent_booking.get("Etown_bookingcreatedon", "Unknown date")
        tracking_id = recent_booking.get("Etown_BookingTrackId", "No tracking ID")
        status = recent_booking.get("Etown_requeststatus", "Unknown status").title()
        
        response_message = (
            f"Your most recent booking is with **{business_name}** "
            f"(Status: {status}). "
            f"Booked on: {booking_date}. "
            f"Tracking ID: {tracking_id}."
        )
        
        recent_data = {
            "RequestDetails": [recent_booking],
            "TotalCount": 1,
            "IsRecentBooking": True
        }
        
        logger.info(f"Found recent booking: {business_name} - {booking_date}")
        return response_message, recent_data
        
    except Exception as e:
        logger.error(f"Error getting recent booking: {str(e)}", exc_info=True)
        return "Could not retrieve your recent booking. Please try again.", None


def extract_city_state(address: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (city, state) from the first 'City, XX' two-letter-code match in an address, or (None, None)."""
    if not address:
        return None, None
    
    try:
        pattern = r'([^,]+),\s*([A-Z]{2})'
        match = re.search(pattern, address)
        
        if match:
            city = match.group(1).strip()
            state = match.group(2).strip()
            return city, state
        
        return None, None
    except Exception as e:
        logger.error(f"Error extracting city/state from address: {str(e)}", exc_info=True)
        return None, None


async def handle_member_request_getlist(
    contact_id: str,
    user_profile_id: str,
    mobilenumber_accesstoken: str,
    request_tracking_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    date: Optional[str] = None,
    first_count: Optional[int] = None,
    last_count: Optional[int] = None,
    page_number: int = 0,
    recent_only: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Fetch a page of the member's requests in-process and format them, or only the latest one, for the agent.

    Args:
        contact_id: Member's contact ID.
        user_profile_id: Member's user profile ID.
        mobilenumber_accesstoken: Mobile number access token; not used by the in-process query.
        request_tracking_id: Optional tracking ID to fetch a single request.
        start_time: Start time filter (HH:MM).
        end_time: End time filter (HH:MM).
        date: Date filter (YYYY-MM-DD).
        first_count: Page size when given; defaults to 20.
        last_count: Page size when first_count is not given.
        page_number: Zero-based page number.
        recent_only: When True, return only the first request with repeat-booking guidance.

    Returns:
        Tuple of (response message, data with RequestDetails and TotalCount, or None on error).
    """
    try:
        logger.info(f"Fetching member request list for user {user_profile_id}, page {page_number}")
        
        page_size = first_count or last_count or 20

        result = await AllRequestsListService.get_all_requests_list(
            contactid=contact_id,
            userprofileid=user_profile_id,
            page_number=page_number,
            page_size=page_size,
            start_time=start_time,
            end_time=end_time,
            date=date,
            request_id=request_tracking_id,
        )

        request_details = [b["BookingDetails"] for b in result.get("bookings", [])]
        json_data = {
            "RequestDetails": request_details,
            "TotalCount": result.get("total_count", len(request_details)),
        }

        if not request_details:
            return empty_request_list_message(caller_is_provider=False), json_data

        _enrich_request_addresses(request_details)
        
        if recent_only:
            recent_booking = request_details[0]
            business_name = recent_booking.get('Etown_ServiceProviderBusinessname', 'Unknown')
            booking_date = recent_booking.get('Etown_bookingcreatedon', 'Date not available')
            track_id = recent_booking.get('Etown_BookingTrackId', 'No tracking ID')
            
            recent_message = (
                f"Your most recent booking was with **{business_name}** on {booking_date} (Tracking ID: {track_id}). "
                f"If you want to repeat this booking, let me know!"
            )
            
            recent_data = {
                "RequestDetails": [recent_booking],
                "TotalCount": 1,
                "IsRecentBooking": True,
                "RepeatBookingGuidance": {
                    "step1": f"FIRST: Use booknow_provider to search for: {business_name}",
                    "step2": "THEN: Show current services and get user confirmation", 
                    "step3": "FINALLY: Use request_provider with current service ID from search",
                    "critical_note": "NEVER use request_provider directly - old service IDs cause 'provider not found' errors",
                    "provider_name": business_name
                }
            }
            
            logger.info(f"Returning recent booking only: {business_name} - {booking_date}")
            return recent_message, recent_data
        
        final_string = format_request_list(
            request_details,
            total_count=json_data.get("TotalCount", len(request_details)),
            caller_contact_id=contact_id,
            caller_profile_id=user_profile_id,
            caller_is_provider=False,
            tool_name="get_member_requests_list",
            page_number=page_number,
        )
        
        logger.info(f"Successfully retrieved and enriched {len(request_details)} requests (page {page_number})")
        
        return final_string, json_data
        
    except Exception as e:
        logger.error(f"Error in handle_member_request_getlist: {str(e)}", exc_info=True)
        return "We encountered an error while fetching your requests. Please try again later.", None


def _enrich_request_addresses(request_details: list) -> None:
    """Set requestor and provider city and state codes on each request detail from its address strings, in place."""
    for detail in request_details:
        requestor_address = detail.get("Etown_requestoraddress")
        req_city, req_state = extract_city_state(requestor_address) if requestor_address else (None, None)
        detail["Etown_requestorcity"] = req_city
        detail["Etown_requestorstatecode"] = req_state

        provider_address = detail.get("Etown_serviceprovideraddress")
        prov_city, prov_state = extract_city_state(provider_address) if provider_address else (None, None)
        detail["Etown_serviceprovidercity"] = prov_city
        detail["Etown_serviceproviderstatecode"] = prov_state

