"""Add Booking Instructions Tool - appends member instructions to a booking already placed.

Member-agent tool that resolves the caller's booking by tracking ID, allrequestid or latest request, checks ownership, and appends each instruction through BookingService.add_additional_info; identity comes from the execution context via the tool wrapper.

Features:
    - Reference Resolution: Tracking ID/allrequestid, or none for the user's latest booking
    - Ownership Enforcement: Only a booking the requesting user actually owns can be targeted
    - Append, Never Overwrite: Each instruction is appended via add_additional_info as a text entry
    - Input Cleaning: Blank/None entries are dropped before anything is stored or reported

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    handle_add_booking_instructions(): Resolve the user's booking and append instructions.

Dependencies:
    - app.core.logging: Centralized logging.
    - app.etown.booking.booking_utils: BookingService resolution + append logic.

Updates:
    13-Jul-2026: Module created as part of the Chaatie booking flow refinements.
    18-Aug-2026: Added the post-booking instructions/message feature.
    19-Aug-2026: Stores instructions via add_additional_info, supporting multiple instruction types.
"""

from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import logger
from app.etown.booking.booking_utils import BookingService


async def handle_add_booking_instructions(
    instructions: List[str],
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None,
    booking_reference: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Append the user's text instructions to their referenced or most recent booking.

    Args:
        instructions: One or more instruction strings supplied by the user.
        contact_id: Requesting user's contact id, used to resolve and own the booking.
        user_profile_id: Requesting user's profile id, used to resolve and own the booking.
        booking_reference: Tracking id or allrequestid; the most recent booking when omitted.

    Returns:
        Tuple of (reply message, implicit_output dict or None).
    """
    if not user_profile_id and not contact_id:
        return "Please log in to add instructions to your booking.", None

    clean = [
        str(item).strip()
        for item in (instructions or [])
        if item is not None and str(item).strip()
    ]
    if not clean:
        return "Sure — what instruction would you like me to add to your booking?", None

    try:
        booking_id = await BookingService.resolve_booking_id_for_user(
            user_profile_id=user_profile_id,
            contact_id=contact_id,
            booking_reference=booking_reference,
        )
    except Exception as e:
        logger.error(f"Failed to resolve booking for instructions: {e}", exc_info=True)
        booking_id = None

    if not booking_id:
        if booking_reference:
            return (
                f"I couldn't find a booking matching '{booking_reference}' under your "
                "account to add these instructions to.",
                None,
            )
        return (
            "I couldn't find a recent booking to attach these instructions to. "
            "Please place a booking first, then share your instructions.",
            None,
        )

    try:
        result = await BookingService.add_additional_info(
            booking_id=booking_id,
            userprofileid=user_profile_id,
            instruction_type="text",
            content="\n".join(clean),
        )
    except Exception as e:
        logger.error(f"Failed to store booking instructions: {e}", exc_info=True)
        return (
            "Sorry, I couldn't save your instructions right now. Please try again.",
            None,
        )

    saved = result.get("instructions", clean) if isinstance(result, dict) else clean
    added_text = "; ".join(clean)
    response = f"Done — I've added the following to your booking: {added_text}."
    implicit_output = {
        "status": "success",
        "booking_id": booking_id,
        "instructions": saved,
    }
    return response, implicit_output
