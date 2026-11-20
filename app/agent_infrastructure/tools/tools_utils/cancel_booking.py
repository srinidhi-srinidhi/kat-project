"""Cancel Booking Tool - cancels one of the caller's own bookings from a Chaatie conversation.

Member- and provider-agent tool handler that resolves a tracking id, booking id or the latest booking for the caller, refuses completed or already-cancelled bookings, and cancels through BookingService.cancel_booking so notifications, push and the Dataverse mirror match the app path.

Features:
    - Ownership Enforcement: A user can only cancel their own bookings
    - Reference Resolution: Accepts a tracking ID, a booking ID, or nothing (latest booking)
    - Status Guards: Refuses to "cancel" a completed or already-cancelled booking
    - Shared Side Effects: Notification, push, and CRM mirror reuse the app's cancel path
    - Structured JSON: Errors are returned as data, never raised at the agent

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    handle_cancel_booking(): Resolve, validate, and cancel a booking

Dependencies:
    - app.core.logging: Centralized logging
    - app.etown.booking.booking_utils: BookingService cancellation path

Updates:
    29-Jul-2026: Initial creation - cancellation was already implemented behind /book/update.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from typing import Any, Dict, Optional, Tuple

from app.core.logging import logger
from app.etown.booking.booking_utils import BookingService


async def handle_cancel_booking(
    booking_reference: Optional[str] = None,
    cancel_reason: Optional[str] = None,
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Cancel the requesting user's booking via BookingService and return relay text plus the result dict.

    Args:
        booking_reference: Tracking or booking ID; None targets the most recent booking.
        cancel_reason: Optional free-text reason supplied by the user.
        contact_id: Contact ID of the requesting user; required.
        user_profile_id: User profile ID of the requesting user; required.

    Returns:
        Tuple of outcome text and a dict whose status is success, not_found, not_allowed or error.
    """
    if not contact_id or not user_profile_id:
        return (
            "Authentication required. Please log in to cancel a booking.",
            {"status": "error", "message": "User not authenticated"},
        )

    try:
        result = await BookingService.cancel_booking(
            user_profile_id=user_profile_id,
            contact_id=contact_id,
            booking_reference=booking_reference,
            cancel_reason=cancel_reason,
        )
    except Exception as e:
        logger.error(f"cancel_booking failed for reference '{booking_reference}': {e}", exc_info=True)
        return (
            "I couldn't cancel that booking right now. Please try again in a moment.",
            {"status": "error", "message": "Cancellation failed"},
        )

    status = result.get("status")
    tracking_id = result.get("tracking_id") or result.get("booking_id")

    if status == "success":
        text = f"Booking {tracking_id} has been cancelled. The provider has been notified."
    elif status == "not_found":
        if booking_reference:
            text = (
                f"I couldn't find a booking with the reference '{booking_reference}' on your "
                f"account, so nothing was cancelled. Please check the tracking ID."
            )
        else:
            text = "You don't have any bookings to cancel."
    elif status == "not_allowed":
        text = f"{result.get('message', 'This booking cannot be cancelled.')} (Booking {tracking_id})."
    else:
        text = "I couldn't cancel that booking right now. Please try again in a moment."

    return text, result
