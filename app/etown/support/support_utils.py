"""Support Utilities - Postgres logic that files contact-support tickets and emails them to support.

Domain layer behind /api/support: inserts an etown_support_ticket row with the caller's plan tier and SLA snapshotted, then sends a routing email to SUPPORT_EMAIL and an acknowledgement to the requester through Microsoft Graph, flagging email_sent, and pages a caller's own tickets.

Features:
    - Tier Snapshot: plan_code, channel, SLA hours and the derived due_at frozen at submit
    - Free Tier Included: a caller with no plan row falls back to the free tier's terms
    - Durable First: the row is committed before either email is attempted
    - Two Emails: routing mail to the support inbox, acknowledgement to the requester
    - Never Raises On Mail: dispatch failures are logged and flagged, not propagated
    - Mirrored To Dataverse: the ticket, and the email_sent flip once Postgres has it
    - Server-Side Identity: name, email, phone and role read from the caller's profile rows
    - Callable Routing Mail: the support copy leads with the caller's name and a tel: link
    - IST Times: raised time and SLA deadline shown in IST, the timezone support works in

Functions:
    create_support_ticket(): Insert one ticket, snapshotting the caller's tier, and return it
    dispatch_support_emails(): Send the routing and acknowledgement emails; flag email_sent
    list_support_tickets(): Page a caller's own tickets, newest first

Dependencies:
    - app.db.client.Database: Postgres access
    - app.etown.plans.plans_utils.resolve_plan: the caller's effective tier
    - app.etown.support.support_syncing: mirrors the email_sent flip to Dataverse
    - app.services.email_service.send_email: Microsoft Graph sender
    - app.services.email_templates: branded HTML shell
    - app.core.config.settings: SUPPORT_EMAIL routing address
    - app.core.logging.logger: centralised logging
    - app.utils.phone.normalize_indian_mobile: national form behind the tel: link

Updates:
    24-Aug-2026: Created contact support routed to support@etown.com; email_sent mirrored.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import html
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.plans.plans_utils import resolve_plan
from app.etown.support.support_syncing import sync_support_ticket_to_dataverse
from app.services.email_service import send_email
from app.services.email_templates import paragraph, render_email
from app.utils.phone import normalize_indian_mobile

_LINK = "#0E7C6B"

_IST = timezone(timedelta(hours=5, minutes=30))

SUPPORT_CATEGORIES = (
    "general",
    "account",
    "booking",
    "payment",
    "subscription",
    "provider",
    "technical",
    "feedback",
    "other",
)

_FREE_TIER_FALLBACK = {
    "plan_code": "free",
    "support_channel": "email",
    "support_response_hours": None,
}

_RESOLVE_IDENTITY = """
    WITH profile AS (
        SELECT up.userprofileid, up.contactid, up.role, up.name, up.email, up.mobilenumber
        FROM public.etown_userprofile up
        WHERE ($2::text IS NOT NULL AND up.userprofileid = $2)
           OR ($2::text IS NULL AND $1::text IS NOT NULL
               AND up.contactid = $1
               AND COALESCE(up.deleted, 'false') <> 'true')
        ORDER BY (up.role = 1) DESC NULLS LAST, up.createdon
        LIMIT 1
    ),
    person AS (
        SELECT c.contactid, c.firstname, c.email, c.mobilephone
        FROM public.etown_contact c
        WHERE c.contactid = COALESCE($1::text, (SELECT contactid FROM profile))
        ORDER BY c.contactid
        LIMIT 1
    )
    SELECT
        COALESCE((SELECT contactid FROM person), (SELECT contactid FROM profile)) AS contactid,
        (SELECT userprofileid FROM profile) AS userprofileid,
        (SELECT role FROM profile) AS role,
        COALESCE(
            NULLIF(BTRIM((SELECT firstname FROM person)), ''),
            NULLIF(BTRIM((SELECT name FROM profile)), '')
        ) AS contact_name,
        COALESCE(
            NULLIF(BTRIM((SELECT email FROM person)), ''),
            NULLIF(BTRIM((SELECT email FROM profile)), '')
        ) AS contact_email,
        COALESCE(
            NULLIF(BTRIM((SELECT mobilephone FROM person)), ''),
            NULLIF(BTRIM((SELECT mobilenumber FROM profile)), '')
        ) AS contact_phone
"""

_INSERT = """
    INSERT INTO public.etown_support_ticket (
        ticketid, reference, contactid, userprofileid, role,
        category, subject, message,
        contact_email, contact_phone, contact_name,
        plan_code, support_channel, response_hours, due_at,
        source, status
    ) VALUES (
        $1, $2, $3, $4, $5,
        $6, $7, $8,
        $9, $10, $11,
        $12, $13, $14, $15,
        $16, 'open'
    )
    RETURNING ticketid, reference, contactid, userprofileid, role, category, subject, message,
              status, plan_code, support_channel, response_hours, due_at, source,
              contact_email, contact_phone, contact_name, email_sent, created_at
"""


async def create_support_ticket(
    *,
    subject: str,
    message: str,
    contact_id: Optional[str] = None,
    userprofile_id: Optional[str] = None,
    category: str = "general",
    source: str = "api",
) -> Dict[str, Any]:
    """Insert a support ticket with the requester's resolved identity and plan SLA, and return the stored row.

    Args:
        subject: One-line summary of the request.
        message: Request body as the user wrote it.
        contact_id: Requester's contactid.
        userprofile_id: Requester's userprofileid; role and plan tier come from it.
        category: One of SUPPORT_CATEGORIES; anything else is stored as 'other'.
        source: 'api' for the REST endpoint, 'chaatie' for the agent tool.

    Returns:
        The inserted ticket, including its reference.

    Raises:
        ValueError: When subject or message is blank after stripping.
    """
    subject = (subject or "").strip()
    message = (message or "").strip()
    if not subject or not message:
        raise ValueError("subject and message are both required")

    category = (category or "general").strip().lower()
    if category not in SUPPORT_CATEGORIES:
        category = "other"

    contact_email = contact_phone = contact_name = None
    role = None
    try:
        identity = await Database.fetchrow(_RESOLVE_IDENTITY, contact_id, userprofile_id)
    except Exception as exc:
        logger.warning(
            "SUPPORT | IDENTITY_RESOLVE_FAILED | contact_id=%s | userprofile_id=%s | %s",
            contact_id, userprofile_id, exc,
        )
        identity = None
    if identity:
        contact_id = identity["contactid"] or contact_id
        userprofile_id = identity["userprofileid"] or userprofile_id
        role = identity["role"]
        contact_email = identity["contact_email"]
        contact_phone = identity["contact_phone"]
        contact_name = identity["contact_name"]

    plan = await resolve_plan(userprofile_id) if userprofile_id else None
    tier = plan or _FREE_TIER_FALLBACK
    response_hours = tier.get("support_response_hours")
    due_at = (
        datetime.now(timezone.utc) + timedelta(hours=response_hours)
        if response_hours else None
    )

    ticket = dict(
        await Database.fetchrow(
            _INSERT,
            str(uuid.uuid4()), f"ETS-{uuid.uuid4().hex[:8].upper()}",
            contact_id, userprofile_id, role,
            category, subject, message,
            contact_email, contact_phone, contact_name,
            tier.get("plan_code"), tier.get("support_channel") or "email",
            response_hours, due_at,
            source,
        )
    )
    logger.info(
        "SUPPORT | TICKET_CREATED | ref=%s | plan=%s | sla_hours=%s | source=%s | contact_id=%s",
        ticket.get("reference"), tier.get("plan_code"), response_hours, source, contact_id,
    )
    return ticket


async def dispatch_support_emails(ticket: Dict[str, Any]) -> None:
    """Email the support inbox and acknowledge the requester, then flag email_sent and mirror the ticket to Dataverse."""
    reference = ticket.get("reference") or "unknown"
    esc = {
        key: html.escape(str(ticket.get(key) or "-"))
        for key in (
            "subject", "message", "category", "plan_code", "support_channel",
            "contact_email", "contact_phone", "contact_name", "source",
            "contactid", "userprofileid",
        )
    }
    body_html = esc["message"].replace("\n", "<br>")
    hours = ticket.get("response_hours")

    def in_ist(value):
        if not value:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(_IST)

    due_ist = in_ist(ticket.get("due_at"))
    raised_ist = in_ist(ticket.get("created_at"))
    sla_line = (
        f"Respond within <strong>{hours} hours</strong> "
        f"(by {due_ist:%d %b %Y, %I:%M %p} IST)."
        if due_ist and hours
        else "No contractual SLA on this tier - handle in queue order."
    )
    raised_line = f"Raised: {raised_ist:%d %b %Y, %I:%M %p} IST<br>" if raised_ist else ""

    national = normalize_indian_mobile(ticket.get("contact_phone"))
    phone_html = (
        f'<a href="tel:+91{national}" style="color:{_LINK};text-decoration:none;">'
        f'+91 {national}</a>'
        if national
        else (esc["contact_phone"] if ticket.get("contact_phone") else "not on file")
    )
    email_html = (
        f'<a href="mailto:{esc["contact_email"]}?subject={reference}" '
        f'style="color:{_LINK};text-decoration:none;">{esc["contact_email"]}</a>'
        if ticket.get("contact_email")
        else "not on file"
    )
    named = html.escape(str(ticket.get("contact_name")).strip()) if ticket.get("contact_name") else ""
    caller_html = (
        f"<strong>{named or 'Name not on file'}</strong>"
        f"{' (provider)' if ticket.get('role') == 1 else ' (member)' if ticket.get('role') == 2 else ''}"
        f"<br>Phone: {phone_html}"
        f"<br>Email: {email_html}"
    )

    routed = False
    try:
        await send_email(
            to=settings.SUPPORT_EMAIL,
            subject=(
                f"[{reference}] {ticket.get('category') or 'general'}: "
                f"{ticket.get('subject') or ''}"
            ),
            body=render_email(
                heading=f"Support request {reference}",
                preheader=f"{esc['category']} - {esc['subject']}",
                content=(
                    paragraph(caller_html)
                    + paragraph(f"<strong>{esc['subject']}</strong>")
                    + paragraph(body_html)
                    + paragraph(
                        f"Reference: <strong>{reference}</strong><br>"
                        f"Category: {esc['category']}<br>"
                        f"Tier: {esc['plan_code']} ({esc['support_channel']})<br>"
                        f"Raised via: {esc['source']}<br>"
                        f"{raised_line}"
                        f"{sla_line}",
                        muted=True,
                    )
                    + paragraph(
                        f"contactid: {esc['contactid']}<br>"
                        f"userprofileid: {esc['userprofileid']}",
                        muted=True,
                    )
                ),
                footer="Routed automatically by the Etown API.",
            ),
        )
        routed = True
    except Exception as exc:
        logger.error("SUPPORT | ROUTING_EMAIL_FAILED | ref=%s | %s", reference, exc, exc_info=True)

    if ticket.get("contact_email"):
        try:
            await send_email(
                to=ticket["contact_email"],
                subject=f"We've received your request ({reference})",
                body=render_email(
                    heading="We're on it",
                    preheader=f"Your support reference is {reference}.",
                    content=(
                        paragraph(
                            f"Dear <strong>{named}</strong>," if named else "Hello,"
                        )
                        + paragraph(
                            "Thank you for contacting <strong>Etown</strong> support. We have "
                            f"logged your request under reference <strong>{reference}</strong>."
                        )
                        + paragraph(
                            f"Our team will get back to you within <strong>{hours} hours</strong>."
                            if hours
                            else "Our team will get back to you as soon as possible."
                        )
                        + paragraph(
                            "For your records, this is what you sent us:<br><br>"
                            f"<em>{esc['subject']}</em><br>{body_html}",
                            muted=True,
                        )
                        + paragraph(
                            "Please quote your reference number in any follow-up so we can "
                            "pick up where we left off."
                        )
                        + paragraph("<strong>Etown Support</strong>", muted=True)
                    ),
                ),
            )
        except Exception as exc:
            logger.warning("SUPPORT | ACK_EMAIL_FAILED | ref=%s | %s", reference, exc)

    if routed:
        try:
            await Database.execute(
                "UPDATE public.etown_support_ticket "
                "SET email_sent = TRUE, updated_at = now() "
                "WHERE ticketid = $1",
                ticket.get("ticketid"),
            )
        except Exception as exc:
            logger.warning("SUPPORT | EMAIL_FLAG_FAILED | ref=%s | %s", reference, exc)
        else:
            await sync_support_ticket_to_dataverse({**ticket, "email_sent": True})


async def list_support_tickets(
    *,
    contact_id: Optional[str] = None,
    userprofile_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Return a page of non-deleted tickets matching every supplied identifier, newest first, or [] when none is given."""
    if not contact_id and not userprofile_id:
        return []

    rows = await Database.fetch(
        """
        SELECT ticketid, reference, category, subject, message, status,
               plan_code, support_channel, response_hours, due_at, source,
               email_sent, resolved_at, created_at, updated_at
        FROM public.etown_support_ticket
        WHERE ($1::text IS NULL OR contactid = $1)
          AND ($2::text IS NULL OR userprofileid = $2)
          AND ($3::text IS NULL OR status = $3)
          AND COALESCE(deleted, 'false') <> 'true'
        ORDER BY created_at DESC
        LIMIT $4 OFFSET $5
        """,
        contact_id, userprofile_id, status, limit, offset,
    )
    return [dict(row) for row in rows]
