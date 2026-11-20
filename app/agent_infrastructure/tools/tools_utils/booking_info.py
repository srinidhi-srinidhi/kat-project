"""Booking Info Tool - Chaatie lookup of one of the user's own bookings.

Member- and provider-agent tool that resolves a tracking ID against etown_allrequests with ownership enforced, reads the booking through the same AllRequestsListService that backs the booking-list API, and returns a status summary or status='not_found' for an unknown reference.

Features:
    - Booking Retrieval: Fetches real booking details by tracking ID or internal booking ID
    - Ownership Enforcement: A user can only ever resolve their own bookings
    - Latest-Booking Fallback: Resolves the acting profile's most recent booking when no ID is given
    - Not-Found Reporting: Unknown references are reported as such, never as success
    - Status Summary: Human-readable status, provider, schedule and service lines
    - UI Triggering: Signals frontend to display appropriate booking interfaces
    - Structured Responses: Consistent JSON responses for frontend integration

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    _summarize(): Builds a human-readable one-paragraph booking summary for the agent
    handle_booking_info(): Main booking information retrieval handler with authentication

Dependencies:
    - typing: Type hints for function parameters and returns
    - app.core.logging: Centralized logging for booking operations
    - app.etown.booking.booking_utils: BookingService/AllRequestsListService data access

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    12-Aug-2026: The no-reference latest-booking fallback is scoped to the acting userprofileid.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from typing import Tuple, Any, Dict, Optional

from app.core.logging import logger
from app.etown.booking.booking_utils import AllRequestsListService, BookingService


def _summarize(booking: Dict[str, Any]) -> str:
    """Build a human-readable one-paragraph summary of a booking for the agent."""
    details = booking.get("BookingDetails", {}) or {}
    services = booking.get("ServiceDetails", []) or []

    track_id = details.get("Etown_BookingTrackId") or details.get("Etown_BookingId")
    status = (details.get("Etown_requeststatus") or "unknown").strip()
    provider = details.get("Etown_ServiceProviderBusinessname")
    created = details.get("Etown_bookingcreatedon")
    cancel_reason = details.get("Etown_cancelreason")

    parts = [f"Booking {track_id} is currently **{status}**."]
    if provider:
        parts.append(f"Provider: {provider}.")

    service_names = [s.get("Etown_servicedescription") for s in services if s.get("Etown_servicedescription")]
    if service_names:
        parts.append("Service: " + ", ".join(str(name) for name in service_names[:5]) + ".")

    if created:
        parts.append(f"Booked on {created}.")
    if details.get("Etown_updatedacceptedtime"):
        parts.append(f"Accepted at {details['Etown_updatedacceptedtime']}.")
    if details.get("Etown_updatedcompletedtime") and status.lower() == "completed":
        parts.append(f"Completed at {details['Etown_updatedcompletedtime']}.")
    if cancel_reason and status.lower() == "cancelled":
        parts.append(f"Cancellation reason: {cancel_reason}.")

    return " ".join(parts)


async def handle_booking_info(
    booking_id: Optional[str] = None,
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None
) -> Tuple[str, Dict[str, Any]]:
    """Resolve one of the user's own bookings and return a status summary plus a booking_info UI payload.

    Args:
        booking_id: Tracking ID or internal booking ID; empty uses the profile's latest booking.
        contact_id: Contact ID of the requesting user.
        user_profile_id: User profile ID of the requesting user.

    Returns:
        (message, payload) where payload status is success, error or not_found.
    """
    if not contact_id or not user_profile_id:
        result_text = "Authentication required. Please log in to view booking information."
        implicit_output = {"status": "error", "message": "User not authenticated"}
        return result_text, implicit_output

    reference = (booking_id or "").strip() or None

    try:
        resolved_id = await BookingService.resolve_booking_id_for_user(
            user_profile_id=user_profile_id,
            contact_id=contact_id,
            booking_reference=reference,
        )
    except Exception as e:
        logger.error(f"booking_info: resolution failed for reference '{reference}': {e}", exc_info=True)
        return (
            "I couldn't look up that booking right now. Please try again in a moment.",
            {"status": "error", "message": "Booking lookup failed"},
        )

    if not resolved_id:
        if reference:
            result_text = (
                f"I couldn't find a booking with the reference '{reference}' on your account. "
                f"Please double-check the tracking ID, or ask me to show your bookings."
            )
            return result_text, {
                "status": "not_found",
                "booking_id": reference,
                "message": "No booking matching that reference belongs to this user",
            }
        result_text = "You don't have any bookings yet."
        return result_text, {"status": "not_found", "message": "User has no bookings"}

    try:
        listing = await AllRequestsListService.get_all_requests_list(
            contactid=contact_id,
            userprofileid=user_profile_id,
            page_number=0,
            page_size=1,
            allrequest_id=resolved_id,
        )
    except Exception as e:
        logger.error(f"booking_info: fetch failed for booking '{resolved_id}': {e}", exc_info=True)
        return (
            "I couldn't load that booking's details right now. Please try again in a moment.",
            {"status": "error", "message": "Booking fetch failed"},
        )

    bookings = (listing or {}).get("bookings") or []
    if not bookings:
        logger.info(
            f"booking_info: '{resolved_id}' has no service-list row; reading the booking directly"
        )
        try:
            booking = await BookingService.fetch_booking_core(resolved_id)
        except Exception as e:
            logger.error(f"booking_info: direct fetch failed for '{resolved_id}': {e}", exc_info=True)
            booking = None
        if not booking:
            return (
                "I couldn't retrieve the details for that booking. Please try again in a moment.",
                {"status": "not_found", "booking_id": resolved_id,
                 "message": "Booking details unavailable"},
            )
        bookings = [booking]

    booking = bookings[0]
    details = booking.get("BookingDetails", {}) or {}
    result_text = _summarize(booking)

    implicit_output = {
        "status": "success",
        "booking_id": resolved_id,
        "tracking_id": details.get("Etown_BookingTrackId"),
        "request_status": details.get("Etown_requeststatus"),
        "booking": booking,
        "ui_trigger": "booking_info",
    }
    return result_text, implicit_output
