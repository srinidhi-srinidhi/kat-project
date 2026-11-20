"""Provider External Invite Tool - email and SMS invites from a provider to unregistered people.

Provider-agent tool that invites a not-yet-registered person to join Etown as a member or provider, blocking self-invites, already-registered invitees and existing circle links or invitations, then delivering the email or SMS through external_invite_common.

Features:
    - Multi-channel Invitations: Email and SMS delivery paths for the same invite workflow
    - Self-invite Prevention: Blocks a provider from inviting their own email/mobile number
    - Registered-user Detection: Redirects to the internal invite when the invitee is already registered
    - Relationship/Duplicate Guards: Blocks on an existing circle link, accepted or pending invite
    - Native Delivery: Suffix, email templating and SMS run in-house via external_invite_common
    - Error Recovery: Logs failures and returns a user-facing message instead of raising

Classes:
    No classes defined (module uses function-based invitation utilities)

Functions:
    _check_if_self_invite_by_identifier(): Detects self-invite attempts by email or mobile
    _check_if_registered_user(): Checks if email/mobile belongs to an existing Etown user
    _check_existing_relationship(): Validates no existing circle relationship exists
    handle_external_email_invite_provider(): Email invitation handler for provider-initiated invites
    handle_external_mobile_invite_provider(): SMS invitation handler for provider-initiated invites

Dependencies:
    - app.core.logging: Centralized logging for invitation operations
    - app.db.client: Database operations for user and relationship/invitation lookups
    - app.agent_infrastructure.tools.tools_utils.external_invite_common: native suffix + email/SMS delivery

Updates:
    26-May-2026: Expanded minimal stub to full module docstring following project standards
    16-Jul-2026: Suffix generation and delivery now run in-house via external_invite_common.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from typing import Dict, Any, Optional, Tuple

from app.core.logging import logger
from app.db.client import Database
from app.agent_infrastructure.tools.tools_utils.external_invite_common import (
    ROLE_MAP,
    MY_CIRCLE_ROLES,
    check_existing_external_invite,
    generate_external_suffix,
    deliver_email_invite,
    deliver_mobile_invite,
)


async def _check_if_self_invite_by_identifier(
    contact_id: str,
    user_profile_id: str, 
    email: str = None,
    mobile: str = None
) -> bool:
    """Return True when the email or last-10-digit mobile matches the inviter's own member or provider row."""
    try:
        if email:
            member_query = "SELECT contactid FROM etown_member WHERE contactid = $1 AND email = $2"
            provider_query = "SELECT contactid FROM etown_provider WHERE contactid = $1 AND email = $2"
            
            member_result = await Database.fetchrow(member_query, contact_id, email)
            provider_result = await Database.fetchrow(provider_query, contact_id, email)
            
            if member_result or provider_result:
                logger.info(f"Self-invite detected: email {email} belongs to current user {contact_id}")
                return True
                
        if mobile:
            digits = ''.join(filter(str.isdigit, mobile))
            if len(digits) >= 10:
                last_10 = digits[-10:]
                
                member_query = "SELECT contactid FROM etown_member WHERE contactid = $1 AND RIGHT(mobilenumber, 10) = $2"
                provider_query = "SELECT contactid FROM etown_provider WHERE contactid = $1 AND RIGHT(mobilenumber, 10) = $2"
                
                member_result = await Database.fetchrow(member_query, contact_id, last_10)
                provider_result = await Database.fetchrow(provider_query, contact_id, last_10)
                
                if member_result or provider_result:
                    logger.info(f"Self-invite detected: mobile {mobile} belongs to current user {contact_id}")
                    return True
                    
        return False
        
    except Exception as e:
        logger.error(f"Error checking self-invite: {e}", exc_info=True)
        return False


async def _check_if_registered_user(
    email: str = None,
    mobile: str = None
) -> Tuple[bool, Optional[str]]:
    """Return (is_registered, name) for the member or provider owning the email or mobile, else (False, None)."""
    try:
        if email:
            member_query = "SELECT contactidname FROM etown_member WHERE email = $1 LIMIT 1"
            provider_query = "SELECT businessname, firstname FROM etown_provider WHERE email = $1 LIMIT 1"
            
            member_result = await Database.fetchrow(member_query, email)
            if member_result:
                return True, member_result.get('contactidname', 'Unknown User')
                
            provider_result = await Database.fetchrow(provider_query, email)
            if provider_result:
                name = provider_result.get('businessname') or provider_result.get('firstname') or 'Unknown User'
                return True, name
                
        if mobile:
            digits = ''.join(filter(str.isdigit, mobile))
            if len(digits) >= 10:
                last_10 = digits[-10:]
                
                member_query = "SELECT contactidname FROM etown_member WHERE RIGHT(mobilenumber, 10) = $1 LIMIT 1"
                provider_query = "SELECT businessname, firstname FROM etown_provider WHERE RIGHT(mobilenumber, 10) = $1 LIMIT 1"
                
                member_result = await Database.fetchrow(member_query, last_10)
                if member_result:
                    return True, member_result.get('contactidname', 'Unknown User')
                    
                provider_result = await Database.fetchrow(provider_query, last_10)
                if provider_result:
                    name = provider_result.get('businessname') or provider_result.get('firstname') or 'Unknown User'
                    return True, name
                    
        return False, None
        
    except Exception as e:
        logger.error(f"Error checking registered user: {e}", exc_info=True)
        return False, None


async def _check_existing_relationship(
    inviter_contact_id: str,
    inviter_user_profile_id: str,
    invitee_email: str = None,
    invitee_mobile: str = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Return (has_relationship, role_name, invitee_name) from member/provider relations or an accepted invitation between the two."""
    try:
        invitee_contact_id = None
        invitee_user_profile_id = None
        invitee_name = None
        
        if invitee_email:
            member_query = "SELECT contactid, userprofileid, contactidname FROM etown_member WHERE email = $1 LIMIT 1"
            provider_query = "SELECT contactid, userprofileid, businessname, firstname FROM etown_provider WHERE email = $1 LIMIT 1"
            
            member_result = await Database.fetchrow(member_query, invitee_email)
            if member_result:
                invitee_contact_id = member_result.get('contactid')
                invitee_user_profile_id = member_result.get('userprofileid')
                invitee_name = member_result.get('contactidname')
            else:
                provider_result = await Database.fetchrow(provider_query, invitee_email)
                if provider_result:
                    invitee_contact_id = provider_result.get('contactid')
                    invitee_user_profile_id = provider_result.get('userprofileid')
                    invitee_name = provider_result.get('businessname') or provider_result.get('firstname')
                    
        elif invitee_mobile:
            digits = ''.join(filter(str.isdigit, invitee_mobile))
            if len(digits) >= 10:
                last_10 = digits[-10:]
                
                member_query = "SELECT contactid, userprofileid, contactidname FROM etown_member WHERE RIGHT(mobilenumber, 10) = $1 LIMIT 1"
                provider_query = "SELECT contactid, userprofileid, businessname, firstname FROM etown_provider WHERE RIGHT(mobilenumber, 10) = $1 LIMIT 1"
                
                member_result = await Database.fetchrow(member_query, last_10)
                if member_result:
                    invitee_contact_id = member_result.get('contactid')
                    invitee_user_profile_id = member_result.get('userprofileid')
                    invitee_name = member_result.get('contactidname')
                else:
                    provider_result = await Database.fetchrow(provider_query, last_10)
                    if provider_result:
                        invitee_contact_id = provider_result.get('contactid')
                        invitee_user_profile_id = provider_result.get('userprofileid')
                        invitee_name = provider_result.get('businessname') or provider_result.get('firstname')
        
        if not invitee_contact_id or not invitee_user_profile_id:
            return False, None, None
        
        member_rel_query = """
            SELECT relationshiptype FROM etown_memberrelation 
            WHERE (from_contactid = $1 AND to_contactid = $2) 
               OR (from_contactid = $2 AND to_contactid = $1)
            LIMIT 1
        """
        member_rel_result = await Database.fetchrow(member_rel_query, inviter_contact_id, invitee_contact_id)
        
        if member_rel_result:
            relationship_type = member_rel_result.get('relationshiptype')
            role_name = ROLE_MAP.get(relationship_type, 'Unknown')
            logger.info(f"Found existing member relationship: {role_name} between {inviter_contact_id} and {invitee_contact_id}")
            return True, role_name, invitee_name
        
        provider_rel_query = """
            SELECT relationshiptype, relationshiptypename FROM etown_providerrelation 
            WHERE (fromcontactid = $1 AND tocontactid = $2) 
               OR (fromcontactid = $2 AND tocontactid = $1)
            LIMIT 1
        """
        provider_rel_result = await Database.fetchrow(provider_rel_query, inviter_contact_id, invitee_contact_id)
        
        if provider_rel_result:
            relationship_type = provider_rel_result.get('relationshiptype')
            role_name = provider_rel_result.get('relationshiptypename') or ROLE_MAP.get(relationship_type, 'Unknown')
            logger.info(f"Found existing provider relationship: {role_name} between {inviter_contact_id} and {invitee_contact_id}")
            return True, role_name, invitee_name
        
        invitation_query = """
            SELECT invite_rolename, isopponentadded FROM etown_invitation 
            WHERE ((invitercontact = $1 AND invitecontact = $2) 
                OR (invitercontact = $2 AND invitecontact = $1))
              AND isopponentadded = 'true'
            LIMIT 1
        """
        invitation_result = await Database.fetchrow(invitation_query, inviter_contact_id, invitee_contact_id)
        
        if invitation_result:
            role_name = invitation_result.get('invite_rolename', 'Unknown')
            logger.info(f"Found accepted invitation: {role_name} between {inviter_contact_id} and {invitee_contact_id}")
            return True, role_name, invitee_name
        
        return False, None, invitee_name
        
    except Exception as e:
        logger.error(f"Error checking existing relationship: {e}", exc_info=True)
        return False, None, None


async def handle_external_email_invite_provider(
    contact_id: str,
    user_profile_id: str,
    receiver_name: str,
    invite_role: str,
    invitecontact_email: str,
    mobilenumber_accesstoken: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Send a provider's external email invite after self, registered-user and duplicate-invite checks.

    Args:
        contact_id: Inviter's contact id.
        user_profile_id: Inviter's userprofile id.
        receiver_name: Name of the person being invited.
        invite_role: MyCircle role name for the invitee.
        invitecontact_email: Email address to invite.
        mobilenumber_accesstoken: Caller's access token; unused.

    Returns:
        Tuple of user-facing message and None.
    """
    try:
        logger.info(f"Processing external email invite (Provider) for {invitecontact_email}")
        
        if await _check_if_self_invite_by_identifier(contact_id, user_profile_id, email=invitecontact_email):
            return "You cannot invite yourself. Please provide a different email address.", None
        
        is_registered, user_name = await _check_if_registered_user(email=invitecontact_email)
        if is_registered:
            has_relationship, existing_role, invitee_name = await _check_existing_relationship(
                inviter_contact_id=contact_id,
                inviter_user_profile_id=user_profile_id,
                invitee_email=invitecontact_email
            )
            
            if has_relationship:
                return f"You already have an existing {existing_role} connection with {invitee_name or user_name}. You cannot send multiple invitations to the same person with different roles.", None
            
            return f"The email {invitecontact_email} belongs to {user_name} who is already registered in E-Town. Please use the internal invite feature to connect with them instead.", None

        already_invited = await check_existing_external_invite(
            inviter_contact_id=contact_id,
            invitecontact_email=invitecontact_email,
        )
        if already_invited:
            return already_invited, None

        role_number = MY_CIRCLE_ROLES.get(invite_role.lower(), 2)
        role_string = ROLE_MAP.get(role_number, "Member")

        suffix_id = await generate_external_suffix(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            receiver_name=receiver_name,
            inviter_type=1,
            invite_role=role_number,
            invitecontact_email=invitecontact_email,
        )

        if not suffix_id:
            return "Failed to generate a valid suffix ID. Unable to send invite.", None

        sent = await deliver_email_invite(
            receiver_name=receiver_name,
            suffix_id=suffix_id,
            email=invitecontact_email,
            role_string=role_string,
        )

        if sent:
            logger.info(f"Email invite sent successfully to {invitecontact_email}")
            return "The invite was successfully sent to the email address.", None
        return "We are unable to send an invite at the moment. Please try again later.", None

    except Exception as e:
        logger.error(f"Error sending email invite: {str(e)}", exc_info=True)
        return f"An unexpected error occurred while sending the invite: {str(e)}", None


async def handle_external_mobile_invite_provider(
    contact_id: str,
    user_profile_id: str,
    receiver_name: str,
    invite_role: str,
    invitecontact_mobilenumber: str,
    mobilenumber_accesstoken: str,
    language: str = "en",
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Send a provider's external mobile invite after self, registered-user and duplicate-invite checks.

    Args:
        contact_id: Inviter's contact id.
        user_profile_id: Inviter's userprofile id.
        receiver_name: Name of the person being invited.
        invite_role: MyCircle role name for the invitee.
        invitecontact_mobilenumber: Mobile number to invite.
        mobilenumber_accesstoken: Caller's access token; unused.
        language: SMS language code, 'en' or 'ta'.

    Returns:
        Tuple of user-facing message and None.
    """
    try:
        logger.info(f"Processing external mobile invite (Provider) for {invitecontact_mobilenumber}")
        
        if await _check_if_self_invite_by_identifier(contact_id, user_profile_id, mobile=invitecontact_mobilenumber):
            return "You cannot invite yourself. Please provide a different mobile number.", None
        
        is_registered, user_name = await _check_if_registered_user(mobile=invitecontact_mobilenumber)
        if is_registered:
            has_relationship, existing_role, invitee_name = await _check_existing_relationship(
                inviter_contact_id=contact_id,
                inviter_user_profile_id=user_profile_id,
                invitee_mobile=invitecontact_mobilenumber
            )
            
            if has_relationship:
                return f"You already have an existing {existing_role} connection with {invitee_name or user_name}. You cannot send multiple invitations to the same person with different roles.", None
            
            return f"The mobile number {invitecontact_mobilenumber} belongs to {user_name} who is already registered in E-Town. Please use the internal invite feature to connect with them instead.", None

        already_invited = await check_existing_external_invite(
            inviter_contact_id=contact_id,
            invitecontact_mobilenumber=invitecontact_mobilenumber,
        )
        if already_invited:
            return already_invited, None

        role_number = MY_CIRCLE_ROLES.get(invite_role.lower(), 2)

        suffix_id = await generate_external_suffix(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            receiver_name=receiver_name,
            inviter_type=1,
            invite_role=role_number,
            invitecontact_mobilenumber=invitecontact_mobilenumber,
        )

        if not suffix_id:
            return "Failed to generate a valid suffix ID. Unable to send invite.", None

        sent = await deliver_mobile_invite(
            suffix_id=suffix_id,
            mobile_number=invitecontact_mobilenumber,
            language=language,
        )

        if sent:
            logger.info(f"Mobile invite sent successfully to {invitecontact_mobilenumber}")
            return "The invite was successfully sent to the user's mobile number.", None
        return "We are unable to send an invite at the moment. Please try again later.", None

    except Exception as e:
        logger.error(f"Error sending mobile invite: {str(e)}", exc_info=True)
        return f"An unexpected error occurred while sending the invite: {str(e)}", None

