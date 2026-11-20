"""Support Endpoints - HTTP routes to file a support request and read the caller's ticket history.

HTTP layer for /api/support behind the shared bearer token: records a ticket through support_utils, then emails support and the requester and mirrors the ticket to Dataverse in the background; every tier, free included, may file, the tier only setting the SLA returned, and nothing here returns 402.

Endpoints:
    POST /contact: File a support request; emails SUPPORT_EMAIL and mirrors to Dataverse in the background.
    GET /my_tickets: The caller's own tickets, newest first.

Functions:
    contact_support(): Record a support request and schedule its emails.
    my_support_tickets(): Page the caller's support history.

Dependencies:
    - app.core.security.verify_token: Shared bearer token dependency.
    - app.core.config: settings.SUPPORT_EMAIL returned in the response.
    - app.etown.support.support_utils: Ticket creation, email dispatch and history reads.
    - app.etown.support.support_syncing: Dataverse mirror for the filed ticket.
    - app.schemas.api: SupportContactRequest/Response, SupportTicketListResponse.

Updates:
    24-Aug-2026: Created so free and paid tiers can actually contact support.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.core.config import settings
from app.core.logging import logger
from app.core.security import verify_token
from app.etown.support.support_syncing import sync_support_ticket_to_dataverse
from app.etown.support.support_utils import (
    create_support_ticket,
    dispatch_support_emails,
    list_support_tickets,
)
from app.schemas.api import (
    SupportContactRequest,
    SupportContactResponse,
    SupportTicketListResponse,
)

router = APIRouter(tags=["support"])


@router.post(
    "/contact",
    response_model=SupportContactResponse,
    status_code=200,
    summary="Contact Support",
    description=(
        "File a contact-support request. Available on every tier including free -- the "
        "subscription tier only decides the SLA reported back, never whether the request is "
        "accepted. The ticket is written to Postgres first and returned immediately; the "
        "routing email to support@etown.com and the acknowledgement to the requester are "
        "sent in the background, so a 200 means the request is recorded, not that it has "
        "already landed in the inbox. Quote the returned reference in any follow-up. "
        "response_hours and due_at are null on the free tier, which gets email support with "
        "no promised deadline; a member (no provider row, so not plan bound at all) is "
        "recorded against the free tier's terms rather than left with no SLA. The "
        "requester's name, email, phone and role are not accepted in the body -- they are read from the contact and profile rows behind the identifiers supplied."
    ),
    responses={
        200: {
            "description": "Support request recorded",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "reference": "ETS-3F9A2C71",
                        "ticketid": "0b5f8c2e-2c1b-4c5a-9f3d-77a1c0e4b912",
                        "status": "open",
                        "plan_code": "silver",
                        "support_channel": "email",
                        "response_hours": 72,
                        "due_at": "2026-08-27T09:15:00Z",
                        "support_email": "support@etown.com",
                        "message": (
                            "Thanks - your request is logged as ETS-3F9A2C71. Our team will "
                            "respond within 72 hours."
                        ),
                    }
                }
            },
        },
        422: {"description": "Blank subject/message, unknown category, or no identifier supplied"},
        500: {"description": "The ticket could not be recorded"},
    },
)
async def contact_support(
    body: SupportContactRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_token),
) -> SupportContactResponse:
    """Handle POST /api/support/contact: record a ticket, then schedule its Dataverse mirror and support emails.

    Raises:
        HTTPException: 422 when the request is unusable, 500 when the insert fails.
    """
    try:
        ticket = await create_support_ticket(
            subject=body.subject,
            message=body.message,
            contact_id=body.contact_id,
            userprofile_id=body.userprofile_id,
            category=body.category,
            source="api",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.error(
            "SUPPORT | CONTACT_FAILED | contact_id=%s | userprofile_id=%s | %s",
            body.contact_id, body.userprofile_id, exc,
         exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not record your support request. Please try again.",
        )

    background_tasks.add_task(sync_support_ticket_to_dataverse, ticket)
    background_tasks.add_task(dispatch_support_emails, ticket)

    hours = ticket.get("response_hours")
    return SupportContactResponse(
        success=True,
        reference=ticket["reference"],
        ticketid=ticket["ticketid"],
        status=ticket.get("status") or "open",
        plan_code=ticket.get("plan_code"),
        support_channel=ticket.get("support_channel"),
        response_hours=hours,
        due_at=ticket.get("due_at"),
        support_email=settings.SUPPORT_EMAIL,
        message=(
            f"Thanks - your request is logged as {ticket['reference']}. "
            + (
                f"Our team will respond within {hours} hours."
                if hours else "Our team will respond as soon as possible."
            )
        ),
    )


@router.get(
    "/my_tickets",
    response_model=SupportTicketListResponse,
    status_code=200,
    summary="List My Support Tickets",
    description=(
        "The caller's own support tickets, newest first. Each identifier supplied narrows "
        "the result, so pass only contact_id to see everything that contact filed including "
        "tickets raised through Chaatie, which may carry no userprofileid. At least one "
        "identifier is required. The tier fields on each ticket are what was promised when "
        "it was filed, not the caller's tier today."
    ),
    responses={
        200: {"description": "Tickets retrieved successfully"},
        422: {"description": "Neither contact_id nor userprofile_id supplied"},
    },
)
async def my_support_tickets(
    contact_id: Optional[str] = Query(None, description="Requester's contactid"),
    userprofile_id: Optional[str] = Query(None, description="Requester's userprofileid"),
    ticket_status: Optional[str] = Query(
        None,
        alias="status",
        description="Filter to one status: open, in_progress, waiting_on_user, resolved, closed",
    ),
    limit: int = Query(20, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
    _: str = Depends(verify_token),
) -> SupportTicketListResponse:
    """Handle GET /api/support/my_tickets: return a page of the caller's support tickets, newest first.

    Raises:
        HTTPException: 422 when no identifier is supplied, 500 when the read fails.
    """
    if not contact_id and not userprofile_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="contact_id or userprofile_id is required",
        )

    try:
        tickets = await list_support_tickets(
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            status=ticket_status,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.error(
            "SUPPORT | LIST_FAILED | contact_id=%s | userprofile_id=%s | %s",
            contact_id, userprofile_id, exc,
         exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read your support tickets.",
        )

    return SupportTicketListResponse(
        success=True,
        count=len(tickets),
        support_email=settings.SUPPORT_EMAIL,
        tickets=tickets,
    )
