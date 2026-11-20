"""External Invite Common Tool - shared duplicate check, suffix id, email and SMS delivery for invites.

Helpers shared by the member and provider external-invite tools: they detect an invite already sent to a mobile number or email in etown_invitation, create the pending row with its ETEI_ suffix id, and deliver the invite by Microsoft Graph email or Etown SMS.

Features:
    - Duplicate Detection: Matches an already-sent external invite by mobile number or email
    - Suffix Generation: Creates the pending etown_invitation row and returns its ETEI_ suffix id
    - Email Delivery: Fills the template's Role/AppLink/Name placeholders and sends via Microsoft Graph
    - SMS Delivery: Sends the invite code by SMS through the Etown SMS gateway

Classes:
    No classes defined (shared helpers plus ROLE_MAP and MY_CIRCLE_ROLES constants)

Functions:
    check_existing_external_invite(): Detect an already-sent external invite by mobile/email.
    generate_external_suffix(): Create the pending external invitation row + ETEI_ suffix id.
    deliver_email_invite(): Render the invite email template and send it via Microsoft Graph.
    deliver_mobile_invite(): Send the invite SMS carrying the suffix code.

Dependencies:
    - app.core.logging: Logger for duplicate-check, suffix, email, and SMS failures
    - app.db.client: Database access for the duplicate-invite check
    - app.etown.invitations.invitation_utils: external_invite_send, get_invite_email_template
    - app.services.email_service: send_email (Microsoft Graph)
    - app.utils.sms: send_invite_sms_notification (Etown SMS gateway)

Updates:
    16-Jul-2026: Initial creation — native replacement for the PowerApps suffix/email/mobile invite flows
    27-Jul-2026: Added check_existing_external_invite() - external invitees have no contactid to match on.
"""

from typing import Optional

from app.core.logging import logger
from app.db.client import Database
from app.etown.invitations.invitation_utils import external_invite_send, get_invite_email_template
from app.services.email_service import send_email
from app.utils.sms import send_invite_sms_notification


ROLE_MAP = {1: "Provider", 2: "Member"}
MY_CIRCLE_ROLES = {"provider": 1, "member": 2}


async def check_existing_external_invite(
    inviter_contact_id: str,
    invitecontact_email: Optional[str] = None,
    invitecontact_mobilenumber: Optional[str] = None,
) -> Optional[str]:
    """Return a user-facing message if the inviter already has a live external invite to this mobile or email, else None.

    Args:
        inviter_contact_id: Contact ID of the person sending the invite.
        invitecontact_email: Invitee email, checked only when no mobile is given.
        invitecontact_mobilenumber: Invitee mobile, matched on its last 10 digits.

    Returns:
        A duplicate-invite or already-in-circle message, or None when none exists or on error.
    """
    if not inviter_contact_id:
        return None

    try:
        if invitecontact_mobilenumber:
            digits = "".join(filter(str.isdigit, invitecontact_mobilenumber))
            if len(digits) < 10:
                return None
            row = await Database.fetchrow(
                """
                SELECT invitationcode, receivername, createdon
                FROM etown_invitation
                WHERE invitercontact = $1
                  AND RIGHT(invitecontactmobilenumber, 10) = $2
                  AND LOWER(invitationcode) NOT IN ('declined', 'cancelled', 'error')
                ORDER BY createdon DESC
                LIMIT 1
                """,
                inviter_contact_id,
                digits[-10:],
            )
            target = invitecontact_mobilenumber
        elif invitecontact_email:
            row = await Database.fetchrow(
                """
                SELECT invitationcode, receivername, createdon
                FROM etown_invitation
                WHERE invitercontact = $1
                  AND LOWER(invitecontactemail) = LOWER($2)
                  AND LOWER(invitationcode) NOT IN ('declined', 'cancelled', 'error')
                ORDER BY createdon DESC
                LIMIT 1
                """,
                inviter_contact_id,
                invitecontact_email,
            )
            target = invitecontact_email
        else:
            return None

        if not row:
            return None

        code = (row.get("invitationcode") or "").strip().lower()
        name = row.get("receivername") or target
        logger.info(
            f"Existing external invite found for {target} from {inviter_contact_id} "
            f"(code={code!r}); blocking duplicate."
        )
        if code == "accepted":
            return f"{name} is already in your circle."
        return f"You've already sent an invite to {name}. Please wait for them to respond."
    except Exception as e:
        logger.error(f"Error checking existing external invite: {e}", exc_info=True)
        return None


async def generate_external_suffix(
    contact_id: str,
    user_profile_id: str,
    receiver_name: str,
    inviter_type: int,
    invite_role: int,
    invitecontact_email: Optional[str] = None,
    invitecontact_mobilenumber: Optional[str] = None,
) -> Optional[str]:
    """Create a Requested external invitation row via external_invite_send and return its suffix id.

    Args:
        contact_id: Inviter's contact ID.
        user_profile_id: Inviter's user profile ID.
        receiver_name: Display name of the invitee.
        inviter_type: Inviter type integer (2 = member, 1 = provider).
        invite_role: Role integer being invited to (1 = Provider, 2 = Member).
        invitecontact_email: Invitee email (email channel).
        invitecontact_mobilenumber: Invitee mobile number (mobile channel).

    Returns:
        The generated suffix id, or None if creation failed.
    """
    result = await external_invite_send(
        inviter=contact_id,
        invite_contact=None,
        type=inviter_type,
        invite_role=invite_role,
        invitation_code="Requested",
        invitation_row_id=None,
        invitecontact_email=invitecontact_email,
        invitecontact_mobilenumber=invitecontact_mobilenumber,
        receiver_name=receiver_name,
        inviter_user_profile_id=user_profile_id,
        invite_contact_user_profile_id=None,
    )
    suffix_id = (result or {}).get("suffix_id")
    if not suffix_id:
        logger.error(f"External invite suffix generation failed: {result}")
    return suffix_id


async def deliver_email_invite(
    receiver_name: str,
    suffix_id: str,
    email: str,
    role_string: str,
) -> bool:
    """Fill the invite email template's Role, AppLink and Name placeholders and send it; return True on success."""
    try:
        template = await get_invite_email_template()
        subject = template.get("subject_text") or ""
        body = template.get("body") or template.get("email_content") or ""

        app_link = f"https://www.etown.com/openapp?code={suffix_id}"
        body = (
            body.replace("Role", role_string or "")
            .replace("AppLink", app_link)
            .replace("Name", receiver_name or "")
            .replace("]]>", "")
        )

        await send_email(to=email, subject=subject, body=body)
        return True
    except Exception as e:
        logger.error(f"External invite email send failed for {email}: {e}", exc_info=True)
        return False


async def deliver_mobile_invite(
    suffix_id: str,
    mobile_number: str,
    language: str = "en",
) -> bool:
    """Send the invite SMS carrying the suffix code; return True on success, False on failure."""
    try:
        return await send_invite_sms_notification(
            mobile_number=str(mobile_number),
            invite_code=suffix_id,
            language=language or "en",
        )
    except Exception as e:
        logger.error(f"External invite SMS send failed for {mobile_number}: {e}", exc_info=True)
        return False
