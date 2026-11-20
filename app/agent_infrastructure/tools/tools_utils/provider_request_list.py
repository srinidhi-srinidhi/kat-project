"""Provider Request List Tool - lists the booking requests a provider has received.

Provider-agent tool that fetches the provider's requests in-process through AllRequestsListService with time, date and pagination filters, returning the structured payload for the app and a request_list_common text summary for the agent.

Features:
    - Paginated incoming requests via the native service
    - Time, date and pagination filters passed straight through
    - Shared response wording that labels each row by its own direction
    - Both directions: the service matches either leg, so a provider's own bookings appear too
    - Errors are caught and logged, returning a user-facing message rather than raising

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    handle_provider_request_getlist(): Fetch and format the provider's incoming requests

Dependencies:
    - app.etown.booking.booking_utils.AllRequestsListService: the in-house request list
    - app.agent_infrastructure.tools.tools_utils.request_list_common: response wording
    - app.core.logging: structured logging

Updates:
    26-May-2026: Expanded minimal stub to full module docstring following project standards
    16-Jul-2026: Calls AllRequestsListService.get_all_requests_list in-process instead of the PowerApps flow.
    04-Sep-2026: Response text moved to request_list_common.
"""

from typing import Dict, Any, Optional, Tuple

from app.core.logging import logger
from app.etown.booking.booking_utils import AllRequestsListService
from app.agent_infrastructure.tools.tools_utils.request_list_common import (
    empty_request_list_message,
    format_request_list,
)


async def handle_provider_request_getlist(
    contact_id: str,
    user_profile_id: str,
    mobilenumber_accesstoken: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    date: Optional[str] = None,
    first_count: Optional[int] = None,
    last_count: Optional[int] = None,
    page_number: int = 0,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Fetch a page of the provider's incoming requests and format them for the agent.

    Args:
        contact_id: Provider's contact ID.
        user_profile_id: Provider's user profile ID.
        mobilenumber_accesstoken: Accepted for signature compatibility; not used.
        start_time: Optional start time filter (HH:MM).
        end_time: Optional end time filter (HH:MM).
        date: Optional date filter (YYYY-MM-DD).
        first_count: Page size when given.
        last_count: Page size fallback when first_count is absent; defaults to 10.
        page_number: Zero-based page number.

    Returns:
        Formatted message and RequestDetails/TotalCount dict, or an error message and None.
    """
    try:
        logger.info(f"Fetching provider request list for user {user_profile_id}, page {page_number}")
        
        page_size = first_count or last_count or 10

        result = await AllRequestsListService.get_all_requests_list(
            contactid=contact_id,
            userprofileid=user_profile_id,
            page_number=page_number,
            page_size=page_size,
            start_time=start_time,
            end_time=end_time,
            date=date,
        )

        provider_details = [b["BookingDetails"] for b in result.get("bookings", [])]
        json_data = {
            "RequestDetails": provider_details,
            "TotalCount": result.get("total_count", len(provider_details)),
        }

        if not provider_details:
            return empty_request_list_message(caller_is_provider=True), json_data
      
        final_string = format_request_list(
            provider_details,
            total_count=json_data.get("TotalCount", len(provider_details)),
            caller_contact_id=contact_id,
            caller_profile_id=user_profile_id,
            caller_is_provider=True,
            tool_name="get_provider_requests_list",
            page_number=page_number,
        )
        
        logger.info(f"Successfully retrieved {len(provider_details)} requests (page {page_number})")
        
        return final_string, json_data
        
    except Exception as e:
        logger.error(f"Error in handle_provider_request_getlist: {str(e)}", exc_info=True)
        return "We encountered an error while fetching your requests. Please try again later.", None



