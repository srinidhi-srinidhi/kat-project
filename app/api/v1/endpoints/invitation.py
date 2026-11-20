"""Invitation Endpoints - HTTP routes for invitations, MyCircle and favourites.

HTTP layer for /api/invitations: checks the shared bearer token, then sends, accepts, verifies and lists invitations, manages MyCircle providers and members and the favourite flag, delegating to invitation_utils and scheduling the Dataverse mirror as background tasks.

Features:
    - Contact Invite List: Retrieves External/QR invitations sent by an inviter
    - Invite Notifications: Paginated invite notifications for a user
    - MyCircle Contact Update: Stores a contact's access token and marks it verified
    - MyCircle Retrieval: Returns the actor's MyCircle split into provider and member arrays
    - MyCircle Provider Add/Remove: Requests or removes a provider from the actor's MyCircle
    - MyCircle Member Add/Remove: The same flow with a member as the counterparty (push only)
    - Invite Accept/Verify: Accepts a pending invitation or verifies a QR/link suffix code
    - Mobile/Email Invite: Sends a localized EN/TA invite SMS or email via template
    - New/Dynamic/External Invite Send: Creates invitation + notification atomically, with FCM push
    - Favorite Toggle: Sets or clears the favorite flag on a member/provider MyCircle relation
    - Clear All: Marks all of a receiver's invitations Inactive/Read
    - Authentication: All routes require valid Bearer token via verify_token
    - Dataverse Sync: Writes are mirrored to Dataverse via background tasks

Classes:
    VerifyInviteRequest(): Body for POST /verify — suffix_id or invitee contact/userprofile ids
    AcceptInviteRequest(): Body for POST /accept — inviter/invitee identity, role, and invitation code
    MyCircleUpdateContactRequest(): Body for POST /mycircle/update-contact — contactid, userprofileid, mobile access token
    DynamicSendRequest(): Body for POST /dynamic_send — the Chaatie dynamic-send invitation payload
    ClearAllInvitationsRequest(): Body for POST /clearall — contact_id and userprofile_id of the receiver
    MobileEmailRequest(): Body for POST /mobile_email — recipients, invite_through, and template fields
    NewInviteSendRequest(): Body for POST /new_invite_send — internal invite between two known accounts
    ExternalInviteSendRequest(): Body for POST /external_invite_send — external (email/mobile) invite payload
    AddFavoriteRequest(): Body for POST /add_favorite — LoginUserProfileId, RelationGuidId, favorite flag

Functions:
    contact_invite_list(): GET endpoint to fetch invitations sent by a specific inviter
    invite_notification(): GET endpoint to fetch a user's invite notifications, paginated
    mycircle(): GET endpoint returning the actor's MyCircle provider/member arrays
    add_provider_to_mycircle(): POST endpoint to request adding, or remove, a provider from MyCircle
    add_member_to_mycircle(): POST endpoint to request adding, or remove, a member from MyCircle
    update_contact(): POST endpoint to store a MyCircle contact's access token and mark it verified
    accept_invite(): POST endpoint to accept a pending invitation and create the MyCircle relation
    verify_invite(): POST endpoint to verify a QR/link suffix invitation code
    clear_all_invitations(): POST endpoint to mark all of a receiver's invitations Inactive/Read
    dynamic_send(): POST endpoint to send the Chaatie dynamic invitation
    mobile_email(): POST endpoint to send a localized invite SMS or email to a mobile/email recipient
    new_invite_send(): POST endpoint to send a new internal invitation with FCM notification
    external_invite_send(): POST endpoint to create or update an external (Chaatie) invitation
    add_favorite(): POST endpoint to toggle the favorite flag on the login user's relation

Dependencies:
    - fastapi: Router, dependency injection, and HTTP exception handling
    - app.core.security: Token verification for authenticated routes
    - app.etown.invitations.invitation_utils: Core invitation, MyCircle, and favorite business logic
    - app.etown.invitations.invitation_syncing: Background Dataverse sync helpers
    - app.schemas.api: AddToMyCircleProviderRequest / AddToMyCircleMemberRequest request models
    - app.services.email_service: Email dispatch for the mobile_email endpoint's email branch
    - app.utils.sms: SMS dispatch for mobile invite notifications
    - app.services.api_activity: track_api_call activity tracking
    - pydantic: Request body models

Updates:
    26-May-2026: Added module docstring following project documentation standards
    07-Jul-2026: Added mobile_email and new_invite_send POST endpoints, migrated from PowerApps.
    08-Jul-2026: Added external_invite_send POST endpoint, migrated from PowerApps.
    13-Jul-2026: Added POST /mycircle/add-provider, migrated from PowerApps AddtoMycircleProvider.
    14-Jul-2026: Added POST /mycircle/update-contact; added POST /add_favorite.
    28-Jul-2026: GET /mycircle returns separate provider and member arrays, capped at 10 each.
    30-Jul-2026: add-provider and dynamic_send leave the invitation Requested; relations wait for accept.
    20-Aug-2026: GET /mycircle records include Etown_providertype.
    02-Sep-2026: GET /mycircle leaves out circle entries whose counterparty is a disabled provider.
    07-Sep-2026: Added POST /mycircle/add-members, the member counterpart of add-provider.
"""

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import verify_token
from app.etown.invitations.invitation_utils import get_contact_invite_list, get_invite_notification, get_mycircle, verify_invite as verify_invite_util, accept_invite as accept_invite_util, clear_all_invitations as clear_all_invitations_db, dynamic_send as dynamic_send_util, new_invite_send as new_invite_send_util, get_invite_email_template, external_invite_send as external_invite_send_util, add_provider_to_mycircle as add_provider_to_mycircle_util, mycircle_add_provider_deliver, remove_provider_from_mycircle as remove_provider_from_mycircle_util, mycircle_remove_deliver, add_member_to_mycircle as add_member_to_mycircle_util, mycircle_add_member_deliver, remove_member_from_mycircle as remove_member_from_mycircle_util, mycircle_update_contact as mycircle_update_contact_util, update_relation_favorite as update_relation_favorite_util
from app.etown.invitations.invitation_syncing import sync_mycircle_update_contact_to_dataverse, sync_relation_favorite_to_dataverse
from app.schemas.api import AddToMyCircleMemberRequest, AddToMyCircleProviderRequest
from app.services.email_service import send_email
from app.services.api_activity import track_api_call
from app.utils.sms import send_invite_sms_notification

logger = logging.getLogger(__name__)

router = APIRouter()


class VerifyInviteRequest(BaseModel):
    suffix_id: Optional[str] = None
    invitee_contactId: Optional[str] = None
    invitee_userprofileId: Optional[str] = None


class AcceptInviteRequest(BaseModel):
    inviter: Optional[str] = None
    inviter_firstname: Optional[str] = None
    invite_contact: Optional[str] = None
    invite_contact_firstname: Optional[str] = None
    invite_role: Optional[int] = None
    invitation_code: Optional[str] = None
    invitation_row_id: Optional[str] = None
    InviterUserProfileId: Optional[str] = None
    InviteContactUserProfileId: Optional[str] = None
    invitecontact_role: Optional[str] = None
    Oauth: Optional[str] = None


@router.get(
    "/contact_invite_list",
    status_code=200,
    summary="Contact Invite List",
    description="Fetch External/QR invitations sent by an inviter that are Requested or Accepted.",
)
async def contact_invite_list(
    inviter_id: Optional[str] = Query(None, description="Inviter's contact ID"),
    inviter_userprofile_id: Optional[str] = Query(None, description="Inviter's user profile ID"),
    token: str = Depends(verify_token),
):
    """Handle GET /api/invitations/contact_invite_list: return an inviter's Requested or Accepted External/QR invitations."""
    if not inviter_id:
        raise HTTPException(status_code=400, detail="inviter_id is required")
    if not inviter_userprofile_id:
        raise HTTPException(status_code=400, detail="inviter_userprofile_id is required")

    try:
        result = await get_contact_invite_list(
            inviter_id=inviter_id,
            inviter_userprofile_id=inviter_userprofile_id,
        )
        return {"invitations": result}
    except Exception as e:
        logger.error(f"Failed to fetch contact invite list: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch contact invite list")


@router.get(
    "/invite_notification",
    status_code=200,
    summary="Invite Notification",
    description="Etown - Mobile API - Contact - Get Invite Notification API",
)
async def invite_notification(
    LoginContactId: Optional[str] = Query(None, description="Login contact ID"),
    UserProfileId: Optional[str] = Query(None, description="User profile ID"),
    Contact_PageNumber: Optional[int] = Query(None, description="Contact page number"),
    Service_PageNumber: Optional[int] = Query(None, description="Service page number"),
    Invitation_PageNumber: Optional[int] = Query(None, description="Invitation page number"),
    Total_count: Optional[int] = Query(None, description="Total count"),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/invitations/invite_notification: track the call and return a page of the user's invite notifications."""
    if not LoginContactId:
        raise HTTPException(status_code=400, detail="LoginContactId is required")
    if not UserProfileId:
        raise HTTPException(status_code=400, detail="UserProfileId is required")

    track_api_call(
        background_tasks, "/api/invitations/invite_notification", LoginContactId, UserProfileId
    )

    try:
        result = await get_invite_notification(
            login_contact_id=LoginContactId,
            user_profile_id=UserProfileId,
            invitation_page_number=Invitation_PageNumber if Invitation_PageNumber is not None else 0,
        )
        return result
    except Exception as e:
        logger.error(f"Failed to fetch invite notifications: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch invite notifications")


@router.get(
    "/mycircle",
    status_code=200,
    summary="My Circle",
    description=(
        "Etown - Mobile API - Invitation - My Circle API. Returns the actor's MyCircle "
        "data, split into provider and member arrays."
    ),
)
async def mycircle(
    inviter_id: Optional[str] = Query(None, description="Inviter's contact ID"),
    invite_contact_id: Optional[str] = Query(None, description="Invited contact ID"),
    invitation_code: Optional[str] = Query(None, description="Invitation code"),
    invite_role: Optional[int] = Query(None, description="Invite role"),
    mycircle_list: Optional[str] = Query(None, description="My circle list filter"),
    inviter_latitude: Optional[str] = Query(None, description="Inviter's latitude"),
    inviter_longitude: Optional[str] = Query(None, description="Inviter's longitude"),
    UserProfileId: Optional[str] = Query(None, description="User profile ID"),
    PageNumber: Optional[int] = Query(
        1,
        ge=0,
        description="Page number for pagination (starts from 1; legacy 0 is treated as page 1)",
    ),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/invitations/mycircle: return a page of the actor's MyCircle split into providers and members."""
    if not UserProfileId:
        return []
    if not inviter_id:
        return []

    track_api_call(
        background_tasks, "/api/invitations/mycircle", inviter_id, UserProfileId
    )

    try:
        result = await get_mycircle(
            user_profile_id=UserProfileId,
            inviter_id=inviter_id,
            inviter_latitude=inviter_latitude,
            inviter_longitude=inviter_longitude,
            page_number=PageNumber,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to fetch my circle: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch my circle")


@router.post(
    "/mycircle/add-provider",
    status_code=200,
    summary="Request or remove a provider in MyCircle",
    description=(
        "Etown - Mobile API - Request to add, or remove, a provider in the actor's MyCircle. "
        "``action_mode`` selects the operation: 'add' (default) writes a 'Requested' invitation "
        "+ in-app notification to Postgres, then in the background mirrors them to Dataverse and "
        "notifies the invited provider (push if verified, otherwise a 'you're now live' SMS) — the "
        "provider joins the circle only once they accept the invitation, which is what creates the "
        "relations; 'remove' deletes the invitation + relations from Postgres and mirrors the "
        "deletes to Dataverse. ``provider_contactid`` / ``provider_userprofileid`` may be omitted "
        "on either action: whichever is missing is resolved from the provider record identified by "
        "``providerid``."
    ),
    responses={
        200: {
            "description": "MyCircle request created or record removed",
            "content": {"application/json": {"example": {"message": "MyCircle record is created successfully"}}},
        },
        400: {"description": "The provider identity could not be resolved from providerid"},
    },
)
async def add_provider_to_mycircle(
    background_tasks: BackgroundTasks,
    request: AddToMyCircleProviderRequest = Body(
        ...,
        openapi_examples={
            "add_provider": {
                "summary": "Add a provider to my circle",
                "value": {
                    "from_contactid": "00000000-0000-0000-0000-000000000001",
                    "from_userprofileid": "00000000-0000-0000-0000-000000000002",
                    "provider_contactid": "00000000-0000-0000-0000-000000000003",
                    "provider_userprofileid": "00000000-0000-0000-0000-000000000004",
                    "providerid": "00000000-0000-0000-0000-000000000005",
                    "language_code": "EN",
                    "role": "2",
                    "action_mode": "add",
                    "Oauth": "<fcm-oauth-token-or-null>",
                },
            },
            "remove_provider": {
                "summary": "Remove a provider from my circle",
                "value": {
                    "from_contactid": "00000000-0000-0000-0000-000000000001",
                    "from_userprofileid": "00000000-0000-0000-0000-000000000002",
                    "provider_contactid": "00000000-0000-0000-0000-000000000003",
                    "provider_userprofileid": "00000000-0000-0000-0000-000000000004",
                    "providerid": "00000000-0000-0000-0000-000000000005",
                    "role": "2",
                    "action_mode": "remove",
                },
            },
        },
    ),
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/mycircle/add-provider: request or remove a provider in MyCircle, delivering and mirroring in the background.

    Raises:
        HTTPException: 400 when the provider identity cannot be resolved, 500 on unexpected errors.
    """
    action_mode = (request.action_mode or "add").lower()
    if "remove" in action_mode:
        try:
            result = await remove_provider_from_mycircle_util(
                from_contactid=request.from_contactid,
                from_userprofileid=request.from_userprofileid,
                provider_contactid=request.provider_contactid,
                provider_userprofileid=request.provider_userprofileid,
                providerid=request.providerid,
                role=request.role,
                language_code=request.language_code,
                oauth=request.oauth,
            )
            background_tasks.add_task(mycircle_remove_deliver, result)
            return {"message": "MyCircle record is removed successfully"}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Failed to remove provider from my circle: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to remove provider from my circle")

    try:
        result = await add_provider_to_mycircle_util(
            from_contactid=request.from_contactid,
            from_userprofileid=request.from_userprofileid,
            provider_contactid=request.provider_contactid,
            provider_userprofileid=request.provider_userprofileid,
            providerid=request.providerid,
            role=request.role,
            language_code=request.language_code,
            oauth=request.oauth,
        )
        background_tasks.add_task(mycircle_add_provider_deliver, result)
        return {"message": "MyCircle record is created successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to add provider to my circle: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add provider to my circle")


@router.post(
    "/mycircle/add-members",
    status_code=200,
    summary="Request or remove a member in MyCircle",
    description=(
        "Etown - Mobile API - Request to add, or remove, a member in the actor's MyCircle. "
        "The member mirror of ``/mycircle/add-provider``: ``action_mode`` selects the "
        "operation: 'add' (default) writes a 'Requested' invitation (invite_role 2 / "
        "'Member') + in-app notification to Postgres, then in the background mirrors them "
        "to Dataverse and pushes the invited member when their contact is verified - the "
        "member joins the circle only once they accept the invitation, which is what "
        "creates the relations; 'remove' deletes the invitation + relations from Postgres "
        "and mirrors the deletes to Dataverse. Send ``member_contactid`` + "
        "``member_userprofileid``; a caller holding only one of them may send ``memberid`` "
        "instead and the missing id is resolved from that member's record. An unverified "
        "member gets no SMS - unlike a provider they have no listing to announce."
    ),
    responses={
        200: {
            "description": "MyCircle request created or record removed",
            "content": {"application/json": {"example": {"message": "MyCircle record is created successfully"}}},
        },
        400: {"description": "The member identity could not be resolved from the supplied ids"},
    },
)
async def add_member_to_mycircle(
    background_tasks: BackgroundTasks,
    request: AddToMyCircleMemberRequest = Body(
        ...,
        openapi_examples={
            "add_member": {
                "summary": "Add a member to my circle",
                "value": {
                    "from_contactid": "00000000-0000-0000-0000-000000000001",
                    "from_userprofileid": "00000000-0000-0000-0000-000000000002",
                    "member_contactid": "00000000-0000-0000-0000-000000000003",
                    "member_userprofileid": "00000000-0000-0000-0000-000000000004",
                    "role": "2",
                    "action_mode": "add",
                    "Oauth": "<fcm-oauth-token-or-null>",
                },
            },
            "add_member_by_memberid": {
                "summary": "Add a member, resolving the missing id from memberid",
                "value": {
                    "from_contactid": "00000000-0000-0000-0000-000000000001",
                    "from_userprofileid": "00000000-0000-0000-0000-000000000002",
                    "member_userprofileid": "00000000-0000-0000-0000-000000000004",
                    "memberid": "00000000-0000-0000-0000-000000000005",
                    "role": "2",
                    "action_mode": "add",
                },
            },
            "remove_member": {
                "summary": "Remove a member from my circle",
                "value": {
                    "from_contactid": "00000000-0000-0000-0000-000000000001",
                    "from_userprofileid": "00000000-0000-0000-0000-000000000002",
                    "member_contactid": "00000000-0000-0000-0000-000000000003",
                    "member_userprofileid": "00000000-0000-0000-0000-000000000004",
                    "role": "2",
                    "action_mode": "remove",
                },
            },
        },
    ),
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/mycircle/add-members: request or remove a member in MyCircle, pushing and mirroring in the background.

    Raises:
        HTTPException: 400 when the member identity cannot be resolved, 500 on unexpected errors.
    """
    action_mode = (request.action_mode or "add").lower()
    if "remove" in action_mode:
        try:
            result = await remove_member_from_mycircle_util(
                from_contactid=request.from_contactid,
                from_userprofileid=request.from_userprofileid,
                member_contactid=request.member_contactid,
                member_userprofileid=request.member_userprofileid,
                memberid=request.memberid,
            )
            background_tasks.add_task(mycircle_remove_deliver, result)
            return {"message": "MyCircle record is removed successfully"}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Failed to remove member from my circle: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to remove member from my circle")

    try:
        result = await add_member_to_mycircle_util(
            from_contactid=request.from_contactid,
            from_userprofileid=request.from_userprofileid,
            member_contactid=request.member_contactid,
            member_userprofileid=request.member_userprofileid,
            memberid=request.memberid,
            role=request.role,
            oauth=request.oauth,
        )
        background_tasks.add_task(mycircle_add_member_deliver, result)
        return {"message": "MyCircle record is created successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to add member to my circle: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add member to my circle")


class MyCircleUpdateContactRequest(BaseModel):
    contactid: str
    userprofileid: str
    MobilenumberAccesstoken: str


@router.post(
    "/mycircle/update-contact",
    status_code=200,
    summary="MyCircle Update Contact",
    description=(
        "Etown - Mobile API - Store a MyCircle contact's mobile access token and mark it "
        "verified (migrated from the PowerApps MyCircleUpdateContact flow). Sets accesstoken "
        "+ verified on the contact and verified on the userprofile + its member row(s) in "
        "Postgres, then mirrors the same changes to Dataverse in the background."
    ),
    responses={
        200: {
            "description": "Contact verified successfully",
            "content": {"application/json": {"example": {"message": "Contact updated successfully"}}},
        },
        400: {
            "description": "Missing required parameters",
            "content": {"application/json": {"example": {"detail": "contactid and userprofileid are required"}}},
        },
    },
)
async def update_contact(
    request: MyCircleUpdateContactRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/mycircle/update-contact: store a contact's access token, mark it verified, and mirror to Dataverse.

    Raises:
        HTTPException: 400 if required ids are missing, 500 on unexpected errors.
    """
    if not request.contactid or not request.contactid.strip():
        raise HTTPException(status_code=400, detail="contactid and userprofileid are required")
    if not request.userprofileid or not request.userprofileid.strip():
        raise HTTPException(status_code=400, detail="contactid and userprofileid are required")

    try:
        result = await mycircle_update_contact_util(
            contactid=request.contactid,
            userprofileid=request.userprofileid,
            mobilenumber_accesstoken=request.MobilenumberAccesstoken,
        )
        background_tasks.add_task(
            sync_mycircle_update_contact_to_dataverse,
            result["contact_id"],
            result["userprofile_id"],
            result["member_ids"],
            request.MobilenumberAccesstoken,
        )
        return {"message": "Contact updated successfully"}
    except Exception as e:
        logger.error(f"Failed to update my circle contact: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update my circle contact")


@router.post(
    "/accept",
    status_code=200,
    summary="Accept Invite",
    description="Etown - Mobile API - Invitation - New Invite Accept API",
)
async def accept_invite(
    body: AcceptInviteRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/accept: accept an invitation and return the result."""
    try:
        result = await accept_invite_util(
            invite_role=body.invite_role,
            inviter=body.inviter,
            invite_contact_userprofile_id=body.InviteContactUserProfileId,
            invitation_code=body.invitation_code,
            invite_contact=body.invite_contact,
            inviter_userprofile_id=body.InviterUserProfileId,
            invitation_row_id=body.invitation_row_id,
            invitecontact_role=int(body.invitecontact_role) if body.invitecontact_role is not None else None,
            invite_contact_firstname=body.invite_contact_firstname,
            oauth=body.Oauth,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to accept invite: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to accept invite")


@router.post(
    "/verify",
    status_code=200,
    summary="Verify Invite",
    description="Etown - Mobile API - Invitation - Verify Invite API",
)
async def verify_invite(
    body: VerifyInviteRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/verify: verify an invitation by suffix id for the invitee."""
    try:
        result = await verify_invite_util(
            suffix_id=body.suffix_id,
            invitee_contact_id=body.invitee_contactId,
            invitee_userprofile_id=body.invitee_userprofileId,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to verify invite: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to verify invite")

class DynamicSendRequest(BaseModel):
    inviter: Optional[str] = None
    invite_contact: Optional[str] = None
    type: Optional[int] = None
    invite_role: Optional[int] = None
    is_opponentadded: Optional[str] = None
    invitation_code: Optional[str] = None
    invitation_row_id: Optional[str] = None
    notification_row_id: Optional[str] = None
    invitecontact_email: Optional[str] = None
    invitecontact_mobilenumber: Optional[str] = None
    receiver_name: Optional[str] = None
    InviterUserProfileId: Optional[str] = None
    Invite_contact_UserProfileId: Optional[str] = None
    Invitation_Type: Optional[str] = None
    Invitation_mode: Optional[str] = None


class ClearAllInvitationsRequest(BaseModel):
    contact_id: str
    userprofile_id: str


@router.post(
    "/clearall",
    status_code=200,
    summary="Clear All Invitations",
    description="Mark all Invitations as Inactive/Read for a user by passing receiver contact_id and userprofile_id.",
    responses={
        200: {
            "description": "All Invitations marked as read successfully",
            "content": {
                "application/json": {
                    "example": {"message": "Notification status update completed successfully"}
                }
            },
        },
        400: {
            "description": "Missing required parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "contact_id and userprofile_id are required"}
                }
            },
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to clear all Invitations"}
                }
            },
        },
    },
)
async def clear_all_invitations(
    request: ClearAllInvitationsRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/clearall: mark every invitation for a receiver as read."""
    if not request.contact_id or not request.contact_id.strip():
        raise HTTPException(status_code=400, detail="contact_id and userprofile_id are required")
    if not request.userprofile_id or not request.userprofile_id.strip():
        raise HTTPException(status_code=400, detail="contact_id and userprofile_id are required")

    try:
        await clear_all_invitations_db(request.contact_id, request.userprofile_id)
        return {"message": "Invitation status update completed successfully"}
    except Exception as e:
        logger.error(f"Error clearing all invitations: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to clear all invitations")


@router.post(
    "/dynamic_send",
    status_code=200,
    summary="Dynamic Send Invitation",
    description="Etown - Mobile API - Invitation - Dynamic Send Invitation API",
)
async def dynamic_send(
    body: DynamicSendRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/dynamic_send: send an invitation through the dynamic send flow."""
    try:
        result = await dynamic_send_util(
            inviter=body.inviter,
            invite_contact=body.invite_contact,
            type=body.type,
            invite_role=body.invite_role,
            is_opponentadded=body.is_opponentadded,
            invitation_code=body.invitation_code,
            invitation_row_id=body.invitation_row_id,
            notification_row_id=body.notification_row_id,
            invitecontact_email=body.invitecontact_email,
            invitecontact_mobilenumber=body.invitecontact_mobilenumber,
            receiver_name=body.receiver_name,
            inviter_user_profile_id=body.InviterUserProfileId,
            invite_contact_user_profile_id=body.Invite_contact_UserProfileId,
            invitation_type=body.Invitation_Type,
            invitation_mode=body.Invitation_mode,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to send dynamic invitation: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send dynamic invitation")


class MobileEmailRequest(BaseModel):
    mobilenumber: Optional[List[int]] = None
    etown_link: Optional[str] = None
    suffix_ID: Optional[str] = None
    language_code: Optional[str] = None
    name: Optional[str] = None
    email: Optional[List[str]] = None
    email_content: Optional[str] = None
    Role: Optional[str] = None
    invite_through: str


@router.post(
    "/mobile_email",
    status_code=200,
    summary="Mobile Email Invite",
    description="Etown - Mobile API - Contact - Mobile Email Invite API. Sends a localized EN/TA invite SMS to a mobile number via the new or legacy gateway based on suffix_ID presence.",
)
async def mobile_email(
    body: MobileEmailRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/mobile_email: send invite SMS to each number or the invite email to each address."""
    if body.invite_through.lower() == "mobile":
        invite_code = body.suffix_ID if body.suffix_ID is not None else body.etown_link
        results = []
        for number in (body.mobilenumber or []):
            success = await send_invite_sms_notification(
                mobile_number=str(number),
                invite_code=invite_code,
                language=body.language_code,
            )
            results.append({"mobilenumber": number, "success": success})
        return {"results": results}

    if body.invite_through.lower() == "email":
        template = await get_invite_email_template()
        subject = template.get("subject_text") or ""
        email_body_template = template.get("body") or template.get("email_content") or ""

        if body.suffix_ID is None:
            app_link = body.email_content or ""
        else:
            app_link = f"https://www.etown.com/openapp?code={body.suffix_ID}"

        email_body_template = email_body_template.replace("Role", body.Role or "")
        email_body_template = email_body_template.replace("AppLink", app_link)
        email_body_template = email_body_template.replace("Name", body.name or "")
        email_body_template = email_body_template.replace("]]>", "")

        for recipient in (body.email or []):
            await send_email(
                to=recipient,
                subject=subject,
                body=email_body_template,
            )
        return {"statuscode": "200", "Response": "Mail Triggered"}

    return {"success": False, "message": "Unsupported invite_through value"}


class NewInviteSendRequest(BaseModel):
    inviter: str
    inviter_firstname: str
    invite_contact: str
    invite_contact_firstname: str
    type: int
    invite_role: int
    invitation_code: str
    InviterUserProfileId: str
    InviteContactUserProfileId: str
    inviter_favorite: Optional[str] = None
    invite_contact_favorite: Optional[str] = None
    inviter_frequently_viewed: Optional[str] = None
    invite_contact_frequently_viewed: Optional[str] = None
    inviter_blocked: Optional[str] = None
    invite_contact_blocked: Optional[str] = None
    notification_row_id: Optional[str] = None
    invitation_row_id: Optional[str] = None


@router.post(
    "/new_invite_send",
    status_code=200,
    summary="New Invite Send",
    description="Etown - Mobile API - Invitation - New Invite Send API. Creates an invitation and notification atomically, then sends an FCM push to the invite contact's devices.",
)
async def new_invite_send(
    body: NewInviteSendRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/new_invite_send: create an internal invitation and notification, then push the invitee."""
    try:
        result = await new_invite_send_util(
            inviter=body.inviter,
            inviter_firstname=body.inviter_firstname,
            invite_contact=body.invite_contact,
            invite_contact_firstname=body.invite_contact_firstname,
            type=body.type,
            invite_role=body.invite_role,
            invitation_code=body.invitation_code,
            inviter_userprofile_id=body.InviterUserProfileId,
            invite_contact_userprofile_id=body.InviteContactUserProfileId,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to send new invite: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send new invite")


class ExternalInviteSendRequest(BaseModel):
    inviter: Optional[str] = None
    invite_contact: Optional[str] = None
    type: Optional[int] = None
    invite_role: Optional[int] = None
    invitation_code: Optional[str] = None
    invitation_row_id: Optional[str] = None
    notification_row_id: Optional[str] = None
    invitecontact_email: Optional[str] = None
    invitecontact_mobilenumber: Optional[str] = None
    receiver_name: Optional[str] = None
    InviterUserProfileId: Optional[str] = None
    Invite_contact_UserProfileId: Optional[str] = None
    Invitation_Type: Optional[str] = None


@router.post(
    "/external_invite_send",
    status_code=200,
    summary="External Invite Send",
    description="Etown - Mobile API - Invitation - Chaaty External Invite Send API. Creates or updates an external invitation with bidirectional duplicate-relation and notification checks.",
)
async def external_invite_send(
    body: ExternalInviteSendRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/external_invite_send: create or update an external invitation and return its status."""
    try:
        result = await external_invite_send_util(
            inviter=body.inviter,
            invite_contact=body.invite_contact,
            type=body.type,
            invite_role=body.invite_role,
            invitation_code=body.invitation_code,
            invitation_row_id=body.invitation_row_id,
            invitecontact_email=body.invitecontact_email,
            invitecontact_mobilenumber=body.invitecontact_mobilenumber,
            receiver_name=body.receiver_name,
            inviter_user_profile_id=body.InviterUserProfileId,
            invite_contact_user_profile_id=body.Invite_contact_UserProfileId,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to process external invite send: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process external invite send")


class AddFavoriteRequest(BaseModel):
    LoginUserProfileId: str
    RelationGuidId: str
    inviter_favorite: Optional[str] = None


@router.post(
    "/add_favorite",
    status_code=200,
    summary="Add Favorite",
    description=(
        "Etown - Mobile API - MemberRelation - Add Favorite API (migrated from the PowerApps "
        "AddFavorite flow). Sets the favorite flag on the login user's MyCircle relation: role 2 "
        "(member) updates etown_memberrelation, role 1 (provider) updates etown_providerrelation. "
        "'true' marks it favorite (1/'Yes'), 'false' clears it (2/'No'); the change is mirrored to "
        "Dataverse (ets_favorite) in the background."
    ),
    responses={
        200: {
            "description": "Favorite status updated",
            "content": {"application/json": {"example": {"message": "Updated Successfully"}}},
        },
        400: {
            "description": "Missing required parameters",
            "content": {"application/json": {"example": {"detail": "LoginUserProfileId is required"}}},
        },
    },
)
async def add_favorite(
    request: AddFavoriteRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/invitations/add_favorite: set the favorite flag on the login user's relation and mirror it to Dataverse.

    Raises:
        HTTPException: 400 if required ids are missing, 500 on unexpected errors.
    """
    if not request.LoginUserProfileId or not request.LoginUserProfileId.strip():
        raise HTTPException(status_code=400, detail="LoginUserProfileId is required")
    if not request.RelationGuidId or not request.RelationGuidId.strip():
        raise HTTPException(status_code=400, detail="RelationGuidId is required")

    try:
        result = await update_relation_favorite_util(
            login_userprofile_id=request.LoginUserProfileId,
            relation_guid_id=request.RelationGuidId,
            inviter_favorite=request.inviter_favorite,
        )
        if result.get("dataverse_entity") and result.get("relation_id"):
            background_tasks.add_task(
                sync_relation_favorite_to_dataverse,
                result["dataverse_entity"],
                result["relation_id"],
                result["favorite"],
            )
        return {"message": result["message"]}
    except Exception as e:
        logger.error(f"Failed to update favorite status: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update favorite status")