"""Request List Helpers - shared response wording for the member and provider request-list tools.

Tool helpers that decide each booking row's direction from the caller's contact and userprofile ids and write the list text for the asking side, keeping agent-only paging guidance in a leading bracketed directive that the Classification Bridge strips.

Features:
    - Per-row Direction: derived from the caller's contact and userprofile ids
    - Perspective Wording: names the counterparty with status, date and tracking id
    - Directive Separation: paging and action guidance confined to a strippable prefix

Classes:
    No classes defined (module exposes formatting functions only)

Functions:
    is_outgoing(): True when the caller placed the request rather than received it
    _row(): One list line naming the counterparty
    format_request_list(): Full tool text - agent directive plus the user-facing list
    empty_request_list_message(): The no-rows sentence for the asking side

Dependencies:
    - typing: Type hints for the BookingDetails dictionaries

Updates:
    04-Sep-2026: Created with role-specific response text for both request-list tools.
"""

from typing import Any, Dict, List

_STATUS_LABELS = {
    "requested": "Requested",
    "accepted": "Accepted",
    "completed": "Completed",
    "cancelled": "Cancelled",
    "canceled": "Cancelled",
    "declined": "Declined",
}


def is_outgoing(detail: Dict[str, Any], caller_contact_id: str, caller_profile_id: str) -> bool:
    """Return True when both the caller's contact id and profile id match this row's requestor."""
    return (
        detail.get("Etown_requestorContactId") == caller_contact_id
        and detail.get("Etown_requestorUserProfileId") == caller_profile_id
    )


def _row(index: int, detail: Dict[str, Any], outgoing: bool) -> str:
    """Return one list line, naming the counterparty rather than the caller."""
    if outgoing:
        counterparty = detail.get("Etown_ServiceProviderBusinessname") or "Unknown business"
        lead = f"{index}. {counterparty}"
    else:
        counterparty = detail.get("Etown_requestorname") or "Unknown customer"
        lead = f"{index}. Request from {counterparty}"

    raw_status = (detail.get("Etown_requeststatus") or "").strip()
    parts = [_STATUS_LABELS.get(raw_status.lower(), raw_status or "Status unknown")]
    if detail.get("Etown_bookingcreatedon"):
        parts.append(f"booked {detail['Etown_bookingcreatedon']}")
    if detail.get("Etown_BookingTrackId"):
        parts.append(f"Tracking ID: {detail['Etown_BookingTrackId']}")
    return f"{lead} — " + ", ".join(parts)


def format_request_list(
    request_details: List[Dict[str, Any]],
    total_count: int,
    caller_contact_id: str,
    caller_profile_id: str,
    caller_is_provider: bool,
    tool_name: str,
    page_number: int = 0,
) -> str:
    """Build the request-list tool text: an agent paging directive, a side-aware heading and one line per request.

    Args:
        request_details: BookingDetails dictionaries from the request-list service.
        total_count: The service's own total, used in the paging instruction.
        caller_contact_id: The caller's contact id, for the row-direction test.
        caller_profile_id: The caller's userprofile id, for the row-direction test.
        caller_is_provider: Caller's role; picks the mixed-list heading and the named actions.
        tool_name: Tool to name in the paging instruction.
        page_number: The page just returned, so the instruction can name the next one.

    Returns:
        A leading bracketed agent directive followed by the user-facing list.
    """
    lines = []
    outgoing_count = 0
    for index, detail in enumerate(request_details, start=1):
        outgoing = is_outgoing(detail, caller_contact_id, caller_profile_id)
        outgoing_count += int(outgoing)
        lines.append(_row(index, detail, outgoing))

    all_outgoing = outgoing_count == len(request_details)
    all_incoming = outgoing_count == 0
    if all_outgoing:
        heading = "Here are the requests you have placed:"
    elif all_incoming:
        heading = "Here are the requests you have received:"
    else:
        heading = (
            "Here are your requests — some you received, some you placed:"
            if caller_is_provider
            else "Here are your requests, including any placed with you:"
        )

    actions = ("accept, decline, or complete" if caller_is_provider
               else "cancel or complete")
    directive = (
        f"[REQUEST LIST: {total_count} request(s) in total; the mobile app renders this page "
        f"as a widget, 10 at a time. If the user wants more, call `{tool_name}` with "
        f"page_number={page_number + 1}. Each line already names the counterparty, status, "
        f"date and tracking id, so summarise rather than repeating the list, and do not "
        f"invent details that are not here. The user can {actions} a request from the widget.]"
    )
    return f"{directive}\n\n{heading}\n" + "\n".join(lines)


def empty_request_list_message(caller_is_provider: bool) -> str:
    """Return the empty request-list sentence for the provider or member side."""
    if caller_is_provider:
        return ("You have no service or product requests yet. "
                "New requests from members will appear here.")
    return ("You have not placed any requests yet. "
            "Once you book a business, your requests will appear here.")
