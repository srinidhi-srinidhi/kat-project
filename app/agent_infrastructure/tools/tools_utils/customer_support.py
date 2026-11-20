"""Customer Support Tool - files the user's support request as a real ticket.

Member- and provider-agent tool behind connect_support_agent: writes an etown_support_ticket row through support_utils, mirrors it to Dataverse and emails the support inbox, then returns the ETS- reference; identity comes from the execution context, never the model.

Features:
    - Real Ticket: writes the row and returns the quotable ETS- reference
    - Single Field: reason_for_contact becomes both the truncated subject and the message body
    - Dataverse Mirror: the filed ticket is synced to the CRM
    - Email Routed: routing mail to the support inbox, acknowledgement to the user
    - Tier Aware: repeats the SLA when the user's plan carries one
    - Fail Soft: on any failure the user is given the support address to write to directly

Classes:
    No classes defined (module exposes a single handler function)

Functions:
    handle_connect_support_agent(): File the support request and return the reference

Dependencies:
    - app.etown.support.support_utils: create_support_ticket, dispatch_support_emails
    - app.etown.support.support_syncing: Dataverse mirror for the filed ticket
    - app.core.config.settings: SUPPORT_EMAIL, used in the fallback message
    - app.core.logging.logger: centralised logging

Updates:
    11-Mar-2026: Added comprehensive module docstring and function documentation
    24-Aug-2026: Now files a real ticket and emails support instead of returning a canned string.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.etown.support.support_syncing import sync_support_ticket_to_dataverse
from app.etown.support.support_utils import create_support_ticket, dispatch_support_emails


async def handle_connect_support_agent(
    reason_for_contact: str,
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None,
):
    """File a Chaatie support ticket, mirror it to Dataverse, send the emails and return a confirmation.

    Args:
        reason_for_contact: The issue in the user's words; first 120 chars become the subject.
        contact_id: Requester's contactid, from the execution context.
        user_profile_id: Requester's userprofileid; drives the role, tier and SLA.

    Returns:
        Confirmation with the ticket reference and SLA, or a support-email fallback on failure.
    """
    reason = (reason_for_contact or "").strip()
    if not reason:
        return (
            "Could you tell me briefly what you need help with? I'll pass it straight to "
            "our support team."
        )

    try:
        ticket = await create_support_ticket(
            subject=reason[:120],
            message=reason,
            contact_id=contact_id,
            userprofile_id=user_profile_id,
            category="general",
            source="chaatie",
        )
    except Exception as exc:
        logger.error(
            "SUPPORT | CHAATIE_TICKET_FAILED | contact_id=%s | %s", contact_id, exc
        , exc_info=True)
        return (
            "I couldn't log that with our support team just now. Please email "
            f"{settings.SUPPORT_EMAIL} and they'll pick it up straight away."
        )

    await sync_support_ticket_to_dataverse(ticket)
    await dispatch_support_emails(ticket)

    hours = ticket.get("response_hours")
    sla = (
        f"They'll get back to you within {hours} hours."
        if hours else "They'll get back to you as soon as possible."
    )
    return (
        f"I've passed this to our support team. Your reference is {ticket['reference']}. "
        f"{sla} You can also reach them at {settings.SUPPORT_EMAIL} - quote that reference "
        "and they'll pick up where we left off."
    )
