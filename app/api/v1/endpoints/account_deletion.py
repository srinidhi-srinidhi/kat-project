"""Account Deletion Endpoints - HTTP route that soft-deletes, deactivates or reactivates a contact's data.

HTTP layer for DELETE /api/users/delete_account: checks the shared bearer token, awaits the Postgres lifecycle-flag statement from account_deletion_utils, then schedules the Dataverse flag mirror, plan mirror and confirmation email as background tasks. No row is ever physically deleted.

Features:
    - Modes via action_mode: delete (default) stamps deleted + deletiondate, deactivate stamps deactivated, reactivate clears it
    - Fast PostgreSQL Cascade: the transactional flag statement is awaited inline (~1-2s)
    - Timeout Protection: Raises HTTP 408 if the PostgreSQL cascade exceeds 180 seconds
    - Dataverse Mirror: background lifecycle-flag PATCH on the contact's CRM records, no deletes
    - Subscription Mirror: a delete also syncs the closed member/provider plan to Dataverse
    - Confirmation Email: background HTML email worded per mode
    - Admin Alert: Emails ADMIN_ALERT_EMAIL on timeout for manual follow-up
    - Bearer Auth: Protected with existing verify_token dependency

Classes:
    DeleteAccountRequest(): Request body - contact_id, optional mobilenumber, action_mode
    DeleteAccountResponse(): Response body - status, contact_id, rows flagged, action_mode

Endpoints:
    DELETE /delete_account: Delete, deactivate or reactivate all data for a contact

Functions:
    delete_account(): Handle DELETE /delete_account endpoint
    _send_confirmation_email(): Send the mode-specific confirmation email; never raises

Dependencies:
    - fastapi: Web framework for endpoint definition and security
    - app.etown.user.account_deletion_utils: the PostgreSQL flag statements for all 3 modes
    - app.etown.user.account_lifecycle_syncing: the background Dataverse mirror for all 3 modes
    - app.etown.plans.plans_syncing: member/provider plan mirror on delete
    - app.services.email_service: Graph API confirmation and alert emails
    - app.services.email_templates: branded email shell (render_email, paragraph)
    - app.core.config: ADMIN_ALERT_EMAIL and timeout settings
    - app.core.security: verify_token Bearer authentication
    - app.core.logging: Centralized application logging

Updates:
    06-Jul-2026: Initial creation - migration from PowerApps DeleteDBData flow
    09-Jul-2026: Dataverse cascade and confirmation email moved into FastAPI BackgroundTasks.
    06-Aug-2026: Dataverse entity list gained bookings; no change here.
    12-Aug-2026: Added the action_mode request field with its 'deactivate' branch.
    13-Aug-2026: Dropped the Dataverse cascade entirely - no mode writes to Dataverse now.
    19-Aug-2026: Dataverse written again as a background lifecycle flag mirror; no Dataverse deletes.
    24-Aug-2026: Lifecycle emails use the branded shell; firstname is HTML-escaped.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    01-Sep-2026: A delete also mirrors the closed subscription (expired) to Dataverse.
"""

import asyncio
import html
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Security
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import logger
from app.core.security import verify_token
from app.etown.user.account_deletion_utils import (
    deactivate_account_in_db,
    delete_account_from_db,
    reactivate_account_in_db,
)
from app.etown.plans.plans_syncing import (
    sync_member_plan_to_dataverse,
    sync_provider_plan_to_dataverse,
)
from app.etown.user.account_lifecycle_syncing import sync_account_lifecycle_to_dataverse
from app.services.email_service import send_email
from app.services.email_templates import paragraph, render_email

router = APIRouter()

_DELETION_TIMEOUT_SECONDS = 180

_MODE_DELETE = "delete"
_MODE_DEACTIVATE = "deactivate"
_MODE_REACTIVATE = "reactivate"
_VALID_ACTION_MODES = (_MODE_DELETE, _MODE_DEACTIVATE, _MODE_REACTIVATE)

_MODE_STATUS = {
    _MODE_DELETE: "deleted",
    _MODE_DEACTIVATE: "deactivated",
    _MODE_REACTIVATE: "reactivated",
}
_MODE_COUNT_KEY = {
    _MODE_DELETE: "tables_deleted",
    _MODE_DEACTIVATE: "tables_deactivated",
    _MODE_REACTIVATE: "tables_reactivated",
}
_MODE_HANDLER = {
    _MODE_DELETE: delete_account_from_db,
    _MODE_DEACTIVATE: deactivate_account_in_db,
    _MODE_REACTIVATE: reactivate_account_in_db,
}

_SUPPORT_LINK = (
    '<a href="mailto:support@etown.com" style="color:#0E7C6B;">support@etown.com</a>'
)

_CONFIRMATION_EMAIL_SUBJECT = "Confirmation of Your Account Deletion Request"
_CONFIRMATION_EMAIL_TEMPLATE = render_email(
    heading="Your account has been deleted",
    preheader="Your Etown account deletion request has been processed.",
    content=(
        paragraph("Dear <strong>{firstname}</strong>,")
        + paragraph(
            "We're writing to confirm that your request to delete your <strong>Etown</strong> "
            "account has been successfully processed."
        )
        + paragraph(
            "Your account and all associated personal data have been permanently removed "
            "from our system in accordance with our data protection policy. This action is "
            "irreversible and you will no longer be able to access your account or retrieve "
            "any previously stored information."
        )
        + paragraph(
            "If this was done in error or you have questions, please contact our support "
            f"team at {_SUPPORT_LINK}."
        )
        + paragraph(
            "We appreciate the time you spent with us and hope to serve you again in the future."
        )
        + paragraph("<strong>Etown Services</strong>", muted=True)
    ),
)

_DEACTIVATION_EMAIL_SUBJECT = "Confirmation of Your Account Deactivation Request"
_DEACTIVATION_EMAIL_TEMPLATE = render_email(
    heading="Your account has been deactivated",
    preheader="Nothing has been deleted - your account can be restored at any time.",
    content=(
        paragraph("Dear <strong>{firstname}</strong>,")
        + paragraph(
            "We're writing to confirm that your request to deactivate your "
            "<strong>Etown</strong> account has been successfully processed."
        )
        + paragraph(
            "Your profile and activity are now hidden from other users. Nothing has been "
            "deleted - your data is retained and your account can be restored at any time "
            "by contacting us."
        )
        + paragraph(
            "If this was done in error or you have questions, please contact our support "
            f"team at {_SUPPORT_LINK}."
        )
        + paragraph("We hope to see you back soon.")
        + paragraph("<strong>Etown Services</strong>", muted=True)
    ),
)

_REACTIVATION_EMAIL_SUBJECT = "Your Etown Account Has Been Reactivated"
_REACTIVATION_EMAIL_TEMPLATE = render_email(
    heading="Welcome back",
    preheader="Your Etown account is live again.",
    content=(
        paragraph("Dear <strong>{firstname}</strong>,")
        + paragraph(
            "Welcome back. Your <strong>Etown</strong> account has been reactivated and is "
            "live again."
        )
        + paragraph(
            "Your profile, listings and activity are visible to other users once more, "
            "exactly as you left them."
        )
        + paragraph(
            "If you did not request this, please contact our support team immediately at "
            f"{_SUPPORT_LINK}."
        )
        + paragraph("<strong>Etown Services</strong>", muted=True)
    ),
)

_MODE_EMAIL = {
    _MODE_DELETE: (_CONFIRMATION_EMAIL_SUBJECT, _CONFIRMATION_EMAIL_TEMPLATE),
    _MODE_DEACTIVATE: (_DEACTIVATION_EMAIL_SUBJECT, _DEACTIVATION_EMAIL_TEMPLATE),
    _MODE_REACTIVATE: (_REACTIVATION_EMAIL_SUBJECT, _REACTIVATION_EMAIL_TEMPLATE),
}

_TIMEOUT_ALERT_TEMPLATE = render_email(
    heading="Account deletion timed out",
    preheader="Partial deletion may have occurred - manual completion required.",
    content=(
        paragraph("Hi Team,")
        + paragraph(
            "Account deletion for <strong>contact_id={contact_id}</strong> exceeded the "
            "{timeout}s timeout limit. Partial deletion may have occurred."
        )
        + paragraph("Please investigate and complete the deletion manually if required.")
        + paragraph("<strong>Etown Dev Service</strong>", muted=True)
    ),
)


class DeleteAccountRequest(BaseModel):
    contact_id: str
    mobilenumber: Optional[str] = None
    action_mode: str = Field(
        default=_MODE_DELETE,
        description=(
            "What to do with the account. Optional - defaults to 'delete', so an existing "
            "client that omits it behaves exactly as before. No mode removes any row, in "
            "PostgreSQL or in Dataverse - every mode only stamps flags in both. "
            "'delete': flags the account deleted; reads treat it as gone, but the rows are "
            "retained so a retired counterparty can still be resolved and labelled. "
            "'deactivate': reversible pause; reads return the real name plus a deactivated "
            "flag, and the account is promoted to deleted automatically after 90 days. "
            "'reactivate': undoes a deactivation. Case-insensitive."
        ),
        examples=["delete", "deactivate", "reactivate"],
    )


class DeleteAccountResponse(BaseModel):
    status: str
    contact_id: str
    tables_deleted: int
    action_mode: str


async def _send_confirmation_email(
    user_email: str,
    firstname: str,
    contact_id: str,
    action_mode: str,
) -> None:
    """Send the confirmation email for an account lifecycle action, logging instead of raising on failure.

    Args:
        user_email: Recipient email of the user.
        firstname: First name for the email greeting.
        contact_id: Contact id, used only for logging.
        action_mode: Normalised mode that selects the subject and template.
    """
    subject, template = _MODE_EMAIL[action_mode]
    try:
        await send_email(
            to=user_email,
            subject=subject,
            body=template.format(firstname=html.escape(firstname or "")),
        )
    except Exception:
        logger.warning(
            "ACCOUNT_DELETE | USER_EMAIL_FAILED | contact_id=%s | mode=%s | email=%s",
            contact_id, action_mode, user_email,
        )


@router.delete(
    "/delete_account",
    response_model=DeleteAccountResponse,
    status_code=200,
    summary="Delete, deactivate or reactivate all user data in PostgreSQL",
    description=(
        "Retires all records for a contact across the 41 PostgreSQL tables carrying the "
        "lifecycle columns, then mirrors the same flags onto the contact's Dataverse records. "
        "NOTHING IS PHYSICALLY DELETED in either store - every mode stamps flags and retains "
        "the rows. action_mode='delete' (the default) stamps deleted plus an IST deletiondate; "
        "'deactivate' stamps deactivated plus deactivationdate, leaving `deleted` untouched so "
        "the account stays restorable; 'reactivate' clears deactivated. The PostgreSQL flag "
        "statement is awaited inline and is atomic; the Dataverse mirror and the confirmation "
        "email are dispatched as background tasks, so a CRM outage cannot fail the request. "
        "Raises 408 if the PostgreSQL cascade exceeds 180 seconds."
    ),
)
async def delete_account(
    body: DeleteAccountRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> DeleteAccountResponse:
    """Handle DELETE /api/users/delete_account: soft delete, deactivate or reactivate a contact, then mirror to Dataverse in the background.

    Raises:
        HTTPException: 400 for an invalid action_mode, 503 when the mode is disabled.
        HTTPException: 404 when the contact is not found, 408 on cascade timeout, 500 otherwise.
    """
    contact_id = body.contact_id.strip()
    mobilenumber = body.mobilenumber.strip() if body.mobilenumber else None
    action_mode = (body.action_mode or _MODE_DELETE).strip().lower()

    if action_mode not in _VALID_ACTION_MODES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid action_mode '{body.action_mode}'. "
                f"Expected one of: {', '.join(_VALID_ACTION_MODES)}."
            ),
        )

    if action_mode == _MODE_DELETE and not settings.ACCOUNT_DELETION_ENABLED:
        logger.warning(
            "ACCOUNT_DELETE | DISABLED | contact_id=%s | mode=delete", contact_id
        )
        raise HTTPException(
            status_code=503,
            detail="Account deletion is temporarily unavailable. Please try again later.",
            headers={"Retry-After": "3600"},
        )
    if action_mode == _MODE_DEACTIVATE and not settings.ACCOUNT_DEACTIVATION_ENABLED:
        logger.warning(
            "ACCOUNT_DELETE | DISABLED | contact_id=%s | mode=deactivate", contact_id
        )
        raise HTTPException(
            status_code=503,
            detail="Account deactivation is temporarily unavailable. Please try again later.",
            headers={"Retry-After": "3600"},
        )

    cascade = _MODE_HANDLER[action_mode]
    count_key = _MODE_COUNT_KEY[action_mode]

    logger.info(
        "ACCOUNT_DELETE | START | contact_id=%s | mode=%s", contact_id, action_mode
    )

    try:
        db_result = await asyncio.wait_for(
            cascade(contact_id, mobilenumber),
            timeout=_DELETION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error(
            "ACCOUNT_DELETE | TIMEOUT | contact_id=%s | mode=%s | timeout=%ds",
            contact_id, action_mode, _DELETION_TIMEOUT_SECONDS,
         exc_info=True)
        if settings.ACCOUNT_DELETION_EMAIL_ENABLED and settings.ADMIN_ALERT_EMAIL:
            try:
                await send_email(
                    to=settings.ADMIN_ALERT_EMAIL,
                    subject="ACCOUNT DELETION TIMEOUT ALERT",
                    body=_TIMEOUT_ALERT_TEMPLATE.format(
                        contact_id=contact_id,
                        timeout=_DELETION_TIMEOUT_SECONDS,
                    ),
                )
            except Exception:
                logger.warning(
                    "ACCOUNT_DELETE | ADMIN_EMAIL_FAILED | contact_id=%s", contact_id
                )
        raise HTTPException(
            status_code=408,
            detail=(
                f"Account deletion exceeded the {_DELETION_TIMEOUT_SECONDS}s timeout. "
                "Partial deletion may have occurred. The operations team has been alerted."
            ),
        )
    except Exception as exc:
        logger.error(
            "ACCOUNT_DELETE | ERROR | contact_id=%s | mode=%s | %s",
            contact_id, action_mode, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Account {action_mode} failed unexpectedly. Check server logs.",
        )

    if not db_result.get("found"):
        raise HTTPException(
            status_code=404,
            detail=f"Contact '{contact_id}' not found in the database.",
        )

    user_email: Optional[str] = db_result.get("email")
    firstname: str = db_result.get("firstname") or "User"
    if settings.ACCOUNT_DELETION_EMAIL_ENABLED and user_email:
        background_tasks.add_task(
            _send_confirmation_email, user_email, firstname, contact_id, action_mode
        )

    background_tasks.add_task(
        sync_account_lifecycle_to_dataverse, contact_id, action_mode, mobilenumber
    )
    plan_providerids = db_result.get("plan_providerids") or []
    if action_mode == _MODE_DELETE and plan_providerids:
        background_tasks.add_task(sync_provider_plan_to_dataverse, plan_providerids, True)
    if action_mode == _MODE_DELETE:
        background_tasks.add_task(sync_member_plan_to_dataverse, [contact_id], True)

    tables_affected: int = db_result.get(count_key, 0)
    logger.info(
        "ACCOUNT_DELETE | COMPLETE | contact_id=%s | mode=%s | rows=%d",
        contact_id, action_mode, tables_affected,
    )

    return DeleteAccountResponse(
        status=_MODE_STATUS[action_mode],
        contact_id=contact_id,
        tables_deleted=tables_affected,
        action_mode=action_mode,
    )
