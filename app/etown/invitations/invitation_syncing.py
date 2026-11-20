"""Invitation Dataverse Sync - mirrors invitations, relations and dynamic_send contacts to the CRM.

Background Dataverse mirror for /api/invitations writes: upserts invitations, accepted relations and notifications, marketing names, favourites, MyCircle verification flags and the dynamic_send contact tree by PATCH-first calls; Postgres stays authoritative and every call swallows its own errors.

Features:
    - Invitation Insert Sync: PATCH-then-POST a new invitation row to adx_invitations
    - Accepted-Relation Sync: PATCH-then-POST the accepted notification and provider/member relation
    - Marketing Name Sync: PATCH ets_virtualid / ets_marketingpersonname on providers/members
    - Relation Favorite Sync: PATCH ets_favorite on a member/provider relation (update-only)
    - Dynamic-Send Contact Tree Sync: sequential contact -> userprofile -> member -> relation upserts
    - MyCircle Verification Sync: PATCH access token + verified flags on contact/userprofile/member
    - Record Delete Mirror: DELETE a record from any Dataverse entity set; 404 counts as success
    - OAuth Token Management: Acquires client-credentials tokens for Dataverse API auth

Classes:
    No classes defined (module uses function-based sync utilities)

Functions:
    get_dataverse_token(): Acquire OAuth2 client-credentials token for Dataverse
    delete_record_from_dataverse(): DELETE a record from a Dataverse entity set (404 = success)
    sync_invitation_to_dataverse(): PATCH-then-POST a new invitation to adx_invitations
    sync_provider_marketing_name_to_dataverse(): PATCH ets_virtualid on ets_providers
    sync_member_marketing_name_to_dataverse(): PATCH ets_marketingpersonname on ets_members
    sync_accepted_notification_to_dataverse(): PATCH-then-POST an accepted notification to ets_notificationses
    sync_accepted_providerrelation_to_dataverse(): PATCH-then-POST an accepted provider relation
    sync_accepted_memberrelation_to_dataverse(): PATCH-then-POST an accepted member relation
    sync_relation_favorite_to_dataverse(): PATCH ets_favorite on a member/provider relation (If-Match)
    sync_dynamic_send_contact_to_dataverse(): PATCH-then-POST a new contact from dynamic_send
    sync_dynamic_send_userprofile_to_dataverse(): PATCH-then-POST a new user profile bound to its contact
    sync_dynamic_send_member_to_dataverse(): PATCH-then-POST a new member bound to contact + userprofile
    sync_dynamic_send_new_contact_to_dataverse(): Sequentially sync the contact->userprofile->member->relation tree
    sync_dynamic_send_relation_to_dataverse(): Mirror the inviter->invitee relation to its provider/member entity
    sync_dynamic_send_invitation_to_dataverse(): PATCH-then-POST a dynamic_send invitation to adx_invitations
    sync_mycircle_update_contact_to_dataverse(): PATCH access token + verified flags on contact/userprofile/members

Dependencies:
    - httpx: Async HTTP client for Dataverse Web API calls
    - app.core.config: Dataverse API configuration and OAuth credentials
    - app.core.logging: Centralized application logging

Updates:
    09-Jun-2026: Initial creation for /verify invite background Dataverse syncing
    14-Jul-2026: Added sync_mycircle_update_contact_to_dataverse() and sync_relation_favorite_to_dataverse().
    05-Aug-2026: Added sync_dynamic_send_new_contact_to_dataverse().
"""

import asyncio
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import logger

INVITATION_ENTITY = "adx_invitations"
PROVIDER_ENTITY = "ets_providers"
MEMBER_ENTITY = "ets_members"


async def get_dataverse_token() -> str:
    """Acquire an OAuth2 client-credentials access token for Dataverse, raising if none is returned."""
    token_url = (
        f"https://login.microsoftonline.com/{settings.DATAVERSE_TENANT_ID}"
        "/oauth2/v2.0/token"
    )
    payload = {
        "grant_type": "client_credentials",
        "client_id": settings.DATAVERSE_CLIENT_ID,
        "client_secret": settings.DATAVERSE_CLIENT_SECRET,
        "scope": f"{settings.DATAVERSE_URL}/.default",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(token_url, data=payload)
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValueError("Dataverse token response did not contain access_token")
        return token


async def delete_record_from_dataverse(entity: str, record_id: str) -> None:
    """Delete one record from a Dataverse entity set, treating 404 as success and never raising."""
    if not entity or not record_id:
        logger.warning(
            "DATAVERSE_DELETE | SKIP | entity=%s | record_id=%s", entity, record_id
        )
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_DELETE | FAILED_TOKEN | entity=%s | record_id=%s | %s",
            entity, record_id, exc, exc_info=True,
        )
        return

    delete_url = f"{settings.DATAVERSE_URL}/api/data/v9.2/{entity}({record_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "OData-Version": "4.0",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.delete(delete_url, headers=headers)
            if response.status_code in (204, 404):
                logger.info(
                    "DATAVERSE_DELETE | SUCCESS | entity=%s | record_id=%s | status=%s",
                    entity, record_id, response.status_code,
                )
                return
            logger.error(
                "DATAVERSE_DELETE | FAILED | entity=%s | record_id=%s | status=%s | body=%s",
                entity, record_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_DELETE | ERROR | entity=%s | record_id=%s | %s",
            entity, record_id, exc, exc_info=True,
        )


async def sync_invitation_to_dataverse(invitation_row: dict) -> None:
    """Upsert an inserted invitation row into Dataverse adx_invitations by PATCH, falling back to POST on 404; never raises."""
    invitation_id = invitation_row.get("invitationid")
    if not invitation_id:
        logger.warning("DATAVERSE_INVITATION_SYNC | SKIP | missing invitationid")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_INVITATION_SYNC | FAILED_TOKEN | invitationid=%s | %s",
            invitation_id, exc, exc_info=True,
        )
        return

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{INVITATION_ENTITY}({invitation_id})"
    create_url = f"{base_url}/{INVITATION_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    _type = invitation_row.get("type")
    _invite_role = invitation_row.get("invite_role")
    _createdon = invitation_row.get("createdon")

    payload: dict = {
        "adx_invitationcode": invitation_row.get("invitationcode"),
        "adx_type": int(_type) if _type is not None else None,
        "adx_name": invitation_row.get("invitecontactname") or invitation_row.get("invitercontactname"),
        "ets_invitationtype": invitation_row.get("invitationtype"),
        "ets_invite_role": int(_invite_role) if _invite_role is not None else None,
        "ets_isopponentadded": str(invitation_row.get("isopponentadded")) if invitation_row.get("isopponentadded") is not None else None,
        "ets_suffixid": invitation_row.get("suffixid"),
        "ets_receivername": invitation_row.get("invitecontactname"),
        "createdon": (
            _createdon.isoformat() if hasattr(_createdon, "isoformat")
            else str(_createdon) if _createdon is not None
            else None
        ),
    }

    invitecontact = invitation_row.get("invitecontact")
    invitercontact = invitation_row.get("invitercontact")
    inviteuserprofile = invitation_row.get("inviteuserprofile")
    inviteruserprofile = invitation_row.get("inviteruserprofile")

    if invitecontact:
        payload["adx_inviteContact@odata.bind"] = f"/contacts({invitecontact})"
    if invitercontact:
        payload["adx_invitercontact@odata.bind"] = f"/contacts({invitercontact})"
    if inviteuserprofile:
        payload["ets_InviteContactUserProfile@odata.bind"] = f"/ets_userprofiles({inviteuserprofile})"
    if inviteruserprofile:
        payload["ets_InviterUserProfile@odata.bind"] = f"/ets_userprofiles({inviteruserprofile})"

    payload = {k: v for k, v in payload.items() if v is not None or k.endswith("@odata.bind")}

    def _without_lookups(p: dict) -> dict:
        return {k: v for k, v in p.items() if not k.endswith("@odata.bind")}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)

            if response.status_code == 204:
                logger.info("DATAVERSE_INVITATION_SYNC | SUCCESS_PATCH | invitationid=%s", invitation_id)
                return

            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info("DATAVERSE_INVITATION_SYNC | SUCCESS_POST | invitationid=%s", invitation_id)
                    return

            logger.error(
                "DATAVERSE_INVITATION_SYNC | FAILED | invitationid=%s | status=%s | body=%s",
                invitation_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_INVITATION_SYNC | ERROR | invitationid=%s | %s",
            invitation_id, exc, exc_info=True,
        )


async def sync_provider_marketing_name_to_dataverse(
    provider_id: str,
    marketing_person_name: str,
) -> None:
    """PATCH a provider's marketing person name onto ets_virtualid in Dataverse ets_providers; never raises."""
    if not provider_id or not marketing_person_name:
        logger.warning(
            "DATAVERSE_PROVIDER_MARKETING_SYNC | SKIP | provider_id=%s | name=%s",
            provider_id, marketing_person_name,
        )
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_PROVIDER_MARKETING_SYNC | FAILED_TOKEN | provider_id=%s | %s",
            provider_id, exc, exc_info=True,
        )
        return

    url = f"{settings.DATAVERSE_URL}/api/data/v9.2/{PROVIDER_ENTITY}({provider_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }
    payload = {"ets_virtualid": marketing_person_name}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(url, json=payload, headers=headers)

        if response.status_code == 204:
            logger.info(
                "DATAVERSE_PROVIDER_MARKETING_SYNC | SUCCESS | provider_id=%s", provider_id
            )
        else:
            logger.error(
                "DATAVERSE_PROVIDER_MARKETING_SYNC | FAILED | provider_id=%s | status=%s | body=%s",
                provider_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_PROVIDER_MARKETING_SYNC | ERROR | provider_id=%s | %s",
            provider_id, exc, exc_info=True,
        )


async def sync_member_marketing_name_to_dataverse(
    member_id: str,
    marketing_person_name: str,
) -> None:
    """PATCH a member's marketing person name onto ets_marketingpersonname in Dataverse ets_members; never raises."""
    if not member_id or not marketing_person_name:
        logger.warning(
            "DATAVERSE_MEMBER_MARKETING_SYNC | SKIP | member_id=%s | name=%s",
            member_id, marketing_person_name,
        )
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_MEMBER_MARKETING_SYNC | FAILED_TOKEN | member_id=%s | %s",
            member_id, exc, exc_info=True,
        )
        return

    url = f"{settings.DATAVERSE_URL}/api/data/v9.2/{MEMBER_ENTITY}({member_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }
    payload = {"ets_marketingpersonname": marketing_person_name}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(url, json=payload, headers=headers)

        if response.status_code == 204:
            logger.info(
                "DATAVERSE_MEMBER_MARKETING_SYNC | SUCCESS | member_id=%s", member_id
            )
        else:
            logger.error(
                "DATAVERSE_MEMBER_MARKETING_SYNC | FAILED | member_id=%s | status=%s | body=%s",
                member_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_MEMBER_MARKETING_SYNC | ERROR | member_id=%s | %s",
            member_id, exc, exc_info=True,
        )


NOTIFICATION_ENTITY = "ets_notificationses"
PROVIDER_RELATION_ENTITY = "ets_providerrelations"
MEMBER_RELATION_ENTITY = "ets_memberrelations"


async def sync_accepted_notification_to_dataverse(
    notifications_id: str,
    notification_content: str | None,
    role: int | None,
    notification_status: str | None,
    notification_type: str | None,
    receiver_contact_id: str | None,
    receiver_userprofile_id: str | None,
    sender_contact_id: str | None,
    sender_userprofile_id: str | None,
) -> None:
    """Upsert an accepted notification into Dataverse ets_notificationses by PATCH, falling back to POST on 404; never raises.

    Args:
        notifications_id: PK of the ets_notifications record.
        notification_content: The notification body text.
        role: Numeric role (1 = Provider, 2 = Member).
        notification_status: notificationstatus value (e.g. 'Accepted').
        notification_type: notificationtype value (e.g. 'Invitation').
        receiver_contact_id: contactid of the receiver (inviter).
        receiver_userprofile_id: userprofileid of the receiver (inviter).
        sender_contact_id: contactid of the sender (invite contact).
        sender_userprofile_id: userprofileid of the sender (invite contact).
    """
    if not notifications_id:
        logger.warning("DATAVERSE_ACCEPTED_NOTIFICATION_SYNC | SKIP | missing notifications_id")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_ACCEPTED_NOTIFICATION_SYNC | FAILED_TOKEN | notifications_id=%s | %s",
            notifications_id, exc, exc_info=True,
        )
        return

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{NOTIFICATION_ENTITY}({notifications_id})"
    create_url = f"{base_url}/{NOTIFICATION_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    payload: dict = {
        "ets_notificationcontent": notification_content,
        "ets_role": role,
        "ets_status": notification_status,
        "ets_type": notification_type,
    }

    if receiver_contact_id:
        payload["ets_ReceiverContactID@odata.bind"] = f"/contacts({receiver_contact_id})"
    if receiver_userprofile_id:
        payload["ets_ReceiverUserprofile@odata.bind"] = f"/ets_userprofiles({receiver_userprofile_id})"
    if sender_contact_id:
        payload["ets_SenderContactID@odata.bind"] = f"/contacts({sender_contact_id})"
    if sender_userprofile_id:
        payload["ets_SenderUserprofile@odata.bind"] = f"/ets_userprofiles({sender_userprofile_id})"

    payload = {k: v for k, v in payload.items() if v is not None or k.endswith("@odata.bind")}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info(
                    "DATAVERSE_ACCEPTED_NOTIFICATION_SYNC | SUCCESS_PATCH | notifications_id=%s",
                    notifications_id,
                )
                return
            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info(
                        "DATAVERSE_ACCEPTED_NOTIFICATION_SYNC | SUCCESS_POST | notifications_id=%s",
                        notifications_id,
                    )
                    return
            logger.error(
                "DATAVERSE_ACCEPTED_NOTIFICATION_SYNC | FAILED | notifications_id=%s | status=%s | body=%s",
                notifications_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_ACCEPTED_NOTIFICATION_SYNC | ERROR | notifications_id=%s | %s",
            notifications_id, exc, exc_info=True,
        )


async def sync_accepted_providerrelation_to_dataverse(
    providerrelation_id: str,
    from_contactid: str | None,
    from_providerid: str | None,
    from_userprofileid: str | None,
    to_contactid: str | None,
    to_userprofileid: str | None,
    invite_relationshiptype: int | None,
) -> None:
    """Upsert an accepted provider relation into Dataverse ets_providerrelations by PATCH, falling back to POST on 404; never raises.

    Args:
        providerrelation_id: PK of the ets_providerrelation record.
        from_contactid: fromcontactid of the relation.
        from_providerid: fromproviderid of the relation.
        from_userprofileid: fromuserprofileid of the relation.
        to_contactid: tocontactid of the relation.
        to_userprofileid: touserprofileid of the relation.
        invite_relationshiptype: Numeric invite relation type.
    """
    if not providerrelation_id:
        logger.warning("DATAVERSE_ACCEPTED_PROVIDERRELATION_SYNC | SKIP | missing providerrelation_id")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_ACCEPTED_PROVIDERRELATION_SYNC | FAILED_TOKEN | providerrelation_id=%s | %s",
            providerrelation_id, exc, exc_info=True,
        )
        return

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{PROVIDER_RELATION_ENTITY}({providerrelation_id})"
    create_url = f"{base_url}/{PROVIDER_RELATION_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    payload: dict = {
        "ets_inviterelationtype": int(invite_relationshiptype) if invite_relationshiptype is not None else None,
    }

    if from_contactid:
        payload["ets_FromContactID@odata.bind"] = f"/contacts({from_contactid})"
    if from_providerid:
        payload["ets_FromProvider@odata.bind"] = f"/ets_providers({from_providerid})"
    if from_userprofileid:
        payload["ets_FromUserProfile@odata.bind"] = f"/ets_userprofiles({from_userprofileid})"
    if to_contactid:
        payload["ets_ToContact@odata.bind"] = f"/contacts({to_contactid})"
    if to_userprofileid:
        payload["ets_ToUserProfile@odata.bind"] = f"/ets_userprofiles({to_userprofileid})"

    payload = {k: v for k, v in payload.items() if v is not None or k.endswith("@odata.bind")}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info(
                    "DATAVERSE_ACCEPTED_PROVIDERRELATION_SYNC | SUCCESS_PATCH | providerrelation_id=%s",
                    providerrelation_id,
                )
                return
            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info(
                        "DATAVERSE_ACCEPTED_PROVIDERRELATION_SYNC | SUCCESS_POST | providerrelation_id=%s",
                        providerrelation_id,
                    )
                    return
            logger.error(
                "DATAVERSE_ACCEPTED_PROVIDERRELATION_SYNC | FAILED | providerrelation_id=%s | status=%s | body=%s",
                providerrelation_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_ACCEPTED_PROVIDERRELATION_SYNC | ERROR | providerrelation_id=%s | %s",
            providerrelation_id, exc, exc_info=True,
        )


async def sync_accepted_memberrelation_to_dataverse(
    memberrelation_id: str,
    from_contactid: str | None,
    from_memberid: str | None,
    from_userprofileid: str | None,
    to_contactid: str | None,
    to_userprofileid: str | None,
    invite_relationshiptype: int | None,
) -> None:
    """Upsert an accepted member relation into Dataverse ets_memberrelations by PATCH, falling back to POST on 404; never raises.

    Args:
        memberrelation_id: PK of the ets_memberrelation record.
        from_contactid: from_contactid of the relation.
        from_memberid: from_memberid of the relation.
        from_userprofileid: from_userprofile of the relation.
        to_contactid: to_contactid of the relation.
        to_userprofileid: to_userprofile of the relation.
        invite_relationshiptype: Numeric invite relation type.
    """
    if not memberrelation_id:
        logger.warning("DATAVERSE_ACCEPTED_MEMBERRELATION_SYNC | SKIP | missing memberrelation_id")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_ACCEPTED_MEMBERRELATION_SYNC | FAILED_TOKEN | memberrelation_id=%s | %s",
            memberrelation_id, exc, exc_info=True,
        )
        return

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{MEMBER_RELATION_ENTITY}({memberrelation_id})"
    create_url = f"{base_url}/{MEMBER_RELATION_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    payload: dict = {
        "ets_inviterelationtype": int(invite_relationshiptype) if invite_relationshiptype is not None else None,
    }

    if from_contactid:
        payload["ets_FromContactID@odata.bind"] = f"/contacts({from_contactid})"
    if from_memberid:
        payload["ets_FromMember@odata.bind"] = f"/ets_members({from_memberid})"
    if from_userprofileid:
        payload["ets_FromUserProfile@odata.bind"] = f"/ets_userprofiles({from_userprofileid})"
    if to_contactid:
        payload["ets_ToContact@odata.bind"] = f"/contacts({to_contactid})"
    if to_userprofileid:
        payload["ets_ToUserProfile@odata.bind"] = f"/ets_userprofiles({to_userprofileid})"

    payload = {k: v for k, v in payload.items() if v is not None or k.endswith("@odata.bind")}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info(
                    "DATAVERSE_ACCEPTED_MEMBERRELATION_SYNC | SUCCESS_PATCH | memberrelation_id=%s",
                    memberrelation_id,
                )
                return
            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info(
                        "DATAVERSE_ACCEPTED_MEMBERRELATION_SYNC | SUCCESS_POST | memberrelation_id=%s",
                        memberrelation_id,
                    )
                    return
            logger.error(
                "DATAVERSE_ACCEPTED_MEMBERRELATION_SYNC | FAILED | memberrelation_id=%s | status=%s | body=%s",
                memberrelation_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_ACCEPTED_MEMBERRELATION_SYNC | ERROR | memberrelation_id=%s | %s",
            memberrelation_id, exc, exc_info=True,
        )


async def sync_relation_favorite_to_dataverse(
    entity: str,
    relation_id: str,
    favorite: int | None,
) -> None:
    """PATCH ets_favorite on a member or provider relation in Dataverse, skipping unknown entities; never raises.

    Args:
        entity: Target entity set, 'ets_memberrelations' or 'ets_providerrelations'.
        relation_id: PK of the relation record to update (RelationGuidId).
        favorite: Favorite option value (1 = Yes, 2 = No, None to clear).
    """
    if not relation_id or entity not in (MEMBER_RELATION_ENTITY, PROVIDER_RELATION_ENTITY):
        logger.warning(
            "DATAVERSE_RELATION_FAVORITE_SYNC | SKIP | entity=%s | relation_id=%s",
            entity, relation_id,
        )
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_RELATION_FAVORITE_SYNC | FAILED_TOKEN | relation_id=%s | %s",
            relation_id, exc, exc_info=True,
        )
        return

    url = f"{settings.DATAVERSE_URL}/api/data/v9.2/{entity}({relation_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
        "If-Match": "*",
    }
    payload = {"ets_favorite": favorite}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(url, json=payload, headers=headers)
        if response.status_code == 204:
            logger.info(
                "DATAVERSE_RELATION_FAVORITE_SYNC | SUCCESS | entity=%s | relation_id=%s",
                entity, relation_id,
            )
        else:
            logger.error(
                "DATAVERSE_RELATION_FAVORITE_SYNC | FAILED | entity=%s | relation_id=%s | status=%s | body=%s",
                entity, relation_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_RELATION_FAVORITE_SYNC | ERROR | relation_id=%s | %s",
            relation_id, exc, exc_info=True,
        )


CONTACT_ENTITY = "contacts"
USERPROFILE_ENTITY = "ets_userprofiles"


async def sync_dynamic_send_contact_to_dataverse(
    contact_id: str,
    firstname: str | None,
    mobilephone: str | None,
) -> bool:
    """Upsert a dynamic_send contact into Dataverse contacts by PATCH then POST, returning True when it exists afterwards."""
    if not contact_id:
        logger.warning("DATAVERSE_DYNAMIC_CONTACT_SYNC | SKIP | missing contact_id")
        return False

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_DYNAMIC_CONTACT_SYNC | FAILED_TOKEN | contact_id=%s | %s",
            contact_id, exc, exc_info=True,
        )
        return False

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{CONTACT_ENTITY}({contact_id})"
    create_url = f"{base_url}/{CONTACT_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    payload: dict = {
        "firstname": firstname,
        "mobilephone": mobilephone,
        "ets_is_active": "Active",
        "ets_pushnotificationcheck": "true",
        "ets_userlogin": mobilephone,
        "ets_verified": False,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info("DATAVERSE_DYNAMIC_CONTACT_SYNC | SUCCESS_PATCH | contact_id=%s", contact_id)
                return True
            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info("DATAVERSE_DYNAMIC_CONTACT_SYNC | SUCCESS_POST | contact_id=%s", contact_id)
                    return True
            logger.error(
                "DATAVERSE_DYNAMIC_CONTACT_SYNC | FAILED | contact_id=%s | status=%s | body=%s",
                contact_id, response.status_code, response.text,
            )
            return False
    except Exception as exc:
        logger.error(
            "DATAVERSE_DYNAMIC_CONTACT_SYNC | ERROR | contact_id=%s | %s",
            contact_id, exc, exc_info=True,
        )
        return False


async def sync_dynamic_send_userprofile_to_dataverse(
    userprofile_id: str,
    contact_id: str | None,
    name: str | None,
    mobilephone: str | None,
    role: int | None,
) -> bool:
    """Upsert a dynamic_send userprofile into Dataverse bound to its contact, returning True when it exists afterwards.

    Args:
        userprofile_id: ets_userprofileid (PK) of the new record.
        contact_id: contactid bound as ets_Contact; must already exist in Dataverse.
        name: Profile name (receiver_name).
        mobilephone: Mobile number.
        role: Numeric role (1=Provider, 2=Member).

    Returns:
        True when the userprofile exists in Dataverse after the call, else False.
    """
    if not userprofile_id:
        logger.warning("DATAVERSE_DYNAMIC_USERPROFILE_SYNC | SKIP | missing userprofile_id")
        return False

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_DYNAMIC_USERPROFILE_SYNC | FAILED_TOKEN | userprofile_id=%s | %s",
            userprofile_id, exc, exc_info=True,
        )
        return False

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{USERPROFILE_ENTITY}({userprofile_id})"
    create_url = f"{base_url}/{USERPROFILE_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    payload: dict = {
        "ets_name": name,
        "ets_mobilenumber": mobilephone,
        "ets_lastsignupstatus": 1,
        "ets_membersignup": "true",
        "ets_role": int(role) if role is not None else None,
        "ets_verified": False,
    }
    if contact_id:
        payload["ets_Contact@odata.bind"] = f"/contacts({contact_id})"

    payload = {k: v for k, v in payload.items() if v is not None or k.endswith("@odata.bind")}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info("DATAVERSE_DYNAMIC_USERPROFILE_SYNC | SUCCESS_PATCH | userprofile_id=%s", userprofile_id)
                return True
            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info("DATAVERSE_DYNAMIC_USERPROFILE_SYNC | SUCCESS_POST | userprofile_id=%s", userprofile_id)
                    return True
            logger.error(
                "DATAVERSE_DYNAMIC_USERPROFILE_SYNC | FAILED | userprofile_id=%s | status=%s | body=%s",
                userprofile_id, response.status_code, response.text,
            )
            return False
    except Exception as exc:
        logger.error(
            "DATAVERSE_DYNAMIC_USERPROFILE_SYNC | ERROR | userprofile_id=%s | %s",
            userprofile_id, exc, exc_info=True,
        )
        return False


async def sync_dynamic_send_member_to_dataverse(
    member_id: str,
    contact_id: str | None,
    userprofile_id: str | None,
    firstname: str | None,
    mobilephone: str | None,
) -> bool:
    """Upsert a dynamic_send member into Dataverse bound to its contact and userprofile, returning True when it exists afterwards.

    Args:
        member_id: ets_memberid (PK) of the new record.
        contact_id: contactid bound as ets_contact_id; must already exist in Dataverse.
        userprofile_id: userprofileid bound as ets_UserProfileID; must already exist.
        firstname: Member first name (receiver_name).
        mobilephone: Mobile number.

    Returns:
        True when the member exists in Dataverse after the call, else False.
    """
    if not member_id:
        logger.warning("DATAVERSE_DYNAMIC_MEMBER_SYNC | SKIP | missing member_id")
        return False

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_DYNAMIC_MEMBER_SYNC | FAILED_TOKEN | member_id=%s | %s",
            member_id, exc, exc_info=True,
        )
        return False

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{MEMBER_ENTITY}({member_id})"
    create_url = f"{base_url}/{MEMBER_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    payload: dict = {
        "ets_firstname": firstname,
        "ets_mobile": mobilephone,
        "ets_role": 2,
        "ets_verified": False,
    }
    if contact_id:
        payload["ets_contact_id@odata.bind"] = f"/contacts({contact_id})"
    if userprofile_id:
        payload["ets_UserProfileID@odata.bind"] = f"/ets_userprofiles({userprofile_id})"

    payload = {k: v for k, v in payload.items() if v is not None or k.endswith("@odata.bind")}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info("DATAVERSE_DYNAMIC_MEMBER_SYNC | SUCCESS_PATCH | member_id=%s", member_id)
                return True
            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info("DATAVERSE_DYNAMIC_MEMBER_SYNC | SUCCESS_POST | member_id=%s", member_id)
                    return True
            logger.error(
                "DATAVERSE_DYNAMIC_MEMBER_SYNC | FAILED | member_id=%s | status=%s | body=%s",
                member_id, response.status_code, response.text,
            )
            return False
    except Exception as exc:
        logger.error(
            "DATAVERSE_DYNAMIC_MEMBER_SYNC | ERROR | member_id=%s | %s",
            member_id, exc, exc_info=True,
        )
        return False


async def sync_dynamic_send_new_contact_to_dataverse(
    contact_id: str,
    userprofile_id: str | None,
    member_id: str | None,
    receiver_name: str | None,
    mobilephone: str | None,
    role: int | None,
    relation: dict | None = None,
    inviter_contact_id: str | None = None,
    inviter_userprofile_id: str | None = None,
) -> None:
    """Mirror a dynamic_send contact, userprofile, member and relation to Dataverse in order, stopping when a parent fails.

    Args:
        contact_id: contactid (PK) of the new etown_contact record.
        userprofile_id: userprofileid (PK) of the new etown_userprofile record.
        member_id: memberid (PK) of the new etown_member record.
        receiver_name: Invitee's name, used as contact firstname and profile name.
        mobilephone: Invitee's mobile number.
        role: Numeric role (1=Provider, 2=Member).
        relation: Result of _add_invitee_to_inviter_mycircle(), or None when none was written.
        inviter_contact_id: Inviter's contactid (from side of the relation).
        inviter_userprofile_id: Inviter's userprofileid (from side of the relation).
    """
    contact_ok = await sync_dynamic_send_contact_to_dataverse(
        contact_id=contact_id,
        firstname=receiver_name,
        mobilephone=mobilephone,
    )
    if not contact_ok:
        logger.error(
            "DATAVERSE_DYNAMIC_SEND_SYNC | ABORTED | contact failed — skipping userprofile/member | contact_id=%s",
            contact_id,
        )
        return

    userprofile_ok = await sync_dynamic_send_userprofile_to_dataverse(
        userprofile_id=userprofile_id,
        contact_id=contact_id,
        name=receiver_name,
        mobilephone=mobilephone,
        role=role,
    )
    if not userprofile_ok:
        logger.error(
            "DATAVERSE_DYNAMIC_SEND_SYNC | ABORTED | userprofile failed — skipping member | userprofile_id=%s",
            userprofile_id,
        )
        return

    await sync_dynamic_send_member_to_dataverse(
        member_id=member_id,
        contact_id=contact_id,
        userprofile_id=userprofile_id,
        firstname=receiver_name,
        mobilephone=mobilephone,
    )

    if relation and relation.get("relation_id"):
        await sync_dynamic_send_relation_to_dataverse(
            relation=relation,
            inviter_contact_id=inviter_contact_id,
            inviter_userprofile_id=inviter_userprofile_id,
            invitee_contact_id=contact_id,
            invitee_userprofile_id=userprofile_id,
        )


async def sync_dynamic_send_relation_to_dataverse(
    relation: dict,
    inviter_contact_id: str | None,
    inviter_userprofile_id: str | None,
    invitee_contact_id: str | None,
    invitee_userprofile_id: str | None,
) -> None:
    """Mirror an inviter-to-invitee MyCircle relation to the provider or member relation entity it was written to.

    Args:
        relation: Result of _add_invitee_to_inviter_mycircle().
        inviter_contact_id: Sender's contactid (from side).
        inviter_userprofile_id: Sender's userprofileid (from side).
        invitee_contact_id: Invitee's contactid (to side).
        invitee_userprofile_id: Invitee's userprofileid (to side).
    """
    if not relation or not relation.get("relation_id"):
        return

    if relation.get("entity") == "etown_providerrelation":
        await sync_accepted_providerrelation_to_dataverse(
            providerrelation_id=relation["relation_id"],
            from_contactid=inviter_contact_id,
            from_providerid=relation.get("from_id"),
            from_userprofileid=inviter_userprofile_id,
            to_contactid=invitee_contact_id,
            to_userprofileid=invitee_userprofile_id,
            invite_relationshiptype=relation.get("invite_relationshiptype"),
        )
        return

    await sync_accepted_memberrelation_to_dataverse(
        memberrelation_id=relation["relation_id"],
        from_contactid=inviter_contact_id,
        from_memberid=relation.get("from_id"),
        from_userprofileid=inviter_userprofile_id,
        to_contactid=invitee_contact_id,
        to_userprofileid=invitee_userprofile_id,
        invite_relationshiptype=relation.get("invite_relationshiptype"),
    )


async def sync_dynamic_send_invitation_to_dataverse(
    invitation_id: str,
    inv_type: int | None,
    invitation_type: str | None,
    inviter_contact_id: str | None,
    inviter_userprofile_id: str | None,
    invitecontact_email: str | None,
    invitecontact_mobilenumber: str | None,
    invite_role: int | None,
    receiver_name: str | None,
    suffixid: str | None,
    is_opponentadded: str | None,
    status: str | None,
) -> None:
    """Upsert a dynamic_send invitation into Dataverse adx_invitations by PATCH, falling back to POST on 404; never raises.

    Args:
        invitation_id: adx_invitationid (PK) of the new record.
        inv_type: adx_type integer.
        invitation_type: ets_invitationtype string.
        inviter_contact_id: contactid of the inviter.
        inviter_userprofile_id: userprofileid of the inviter.
        invitecontact_email: Email of the invite contact.
        invitecontact_mobilenumber: Mobile number of the invite contact.
        invite_role: ets_invite_role integer.
        receiver_name: ets_receivername string.
        suffixid: ets_suffixid string.
        is_opponentadded: ets_isopponentadded string.
        status: adx_invitationcode string (e.g. 'Requested').
    """
    if not invitation_id:
        logger.warning("DATAVERSE_DYNAMIC_INVITATION_SYNC | SKIP | missing invitation_id")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_DYNAMIC_INVITATION_SYNC | FAILED_TOKEN | invitation_id=%s | %s",
            invitation_id, exc, exc_info=True,
        )
        return

    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{INVITATION_ENTITY}({invitation_id})"
    create_url = f"{base_url}/{INVITATION_ENTITY}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    payload: dict = {
        "adx_type": int(inv_type) if inv_type is not None else None,
        "adx_invitationcode": status,
        "ets_invitationtype": invitation_type,
        "ets_invite_role": int(invite_role) if invite_role is not None else None,
        "ets_invitecontactemail": invitecontact_email,
        "ets_invitecontactmobilenumber": invitecontact_mobilenumber,
        "ets_receivername": receiver_name,
        "ets_suffixid": suffixid,
        "ets_isnotification": 0,
        "ets_isopponentadded": str(is_opponentadded) if is_opponentadded is not None else None,
    }
    if inviter_contact_id:
        payload["adx_invitercontact@odata.bind"] = f"/contacts({inviter_contact_id})"
    if inviter_userprofile_id:
        payload["ets_InviterUserProfile@odata.bind"] = f"/ets_userprofiles({inviter_userprofile_id})"

    payload = {k: v for k, v in payload.items() if v is not None or k.endswith("@odata.bind")}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=payload, headers=headers)
            if response.status_code == 204:
                logger.info("DATAVERSE_DYNAMIC_INVITATION_SYNC | SUCCESS_PATCH | invitation_id=%s", invitation_id)
                return
            if response.status_code == 404:
                response = await client.post(create_url, json=payload, headers=headers)
                if response.status_code in (200, 201):
                    logger.info("DATAVERSE_DYNAMIC_INVITATION_SYNC | SUCCESS_POST | invitation_id=%s", invitation_id)
                    return
            logger.error(
                "DATAVERSE_DYNAMIC_INVITATION_SYNC | FAILED | invitation_id=%s | status=%s | body=%s",
                invitation_id, response.status_code, response.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_DYNAMIC_INVITATION_SYNC | ERROR | invitation_id=%s | %s",
            invitation_id, exc, exc_info=True,
        )


async def sync_mycircle_update_contact_to_dataverse(
    contact_id: Optional[str],
    userprofile_id: Optional[str],
    member_ids: list,
    mobilenumber_accesstoken: str,
) -> None:
    """Mark a MyCircle contact, userprofile and members verified in Dataverse in parallel PATCHes; never raises.

    Args:
        contact_id: Contact to PATCH with token and verified; skipped when None.
        userprofile_id: User profile to PATCH as verified; skipped when None.
        member_ids: etown_member ids to PATCH as verified.
        mobilenumber_accesstoken: Token stored on the contact.
    """
    if not contact_id and not userprofile_id and not member_ids:
        return

    try:
        access_token = await get_dataverse_token()
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[mycircle_update_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        base_api = f"{base_url}/api/data/v9.2"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "If-Match": "*",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            async def _patch(url: str, payload: dict, label: str) -> None:
                response = await client.patch(url, headers=headers, json=payload)
                if response.status_code == 204:
                    logger.info(f"[mycircle_update_sync] {label} PATCH success")
                else:
                    logger.error(
                        f"[mycircle_update_sync] {label} PATCH failed "
                        f"| status={response.status_code} | body={response.text}"
                    )

            tasks = []
            if contact_id:
                tasks.append(_patch(
                    f"{base_api}/{CONTACT_ENTITY}({contact_id})",
                    {"ets_mobilenumberaccesstoken": mobilenumber_accesstoken, "ets_verified": True},
                    f"contact({contact_id})",
                ))
            if userprofile_id:
                tasks.append(_patch(
                    f"{base_api}/{USERPROFILE_ENTITY}({userprofile_id})",
                    {"ets_verified": True},
                    f"userprofile({userprofile_id})",
                ))
            for m_id in member_ids:
                tasks.append(_patch(
                    f"{base_api}/{MEMBER_ENTITY}({m_id})",
                    {"ets_verified": True},
                    f"member({m_id})",
                ))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as exc:
        logger.error(
            f"[mycircle_update_sync] Failed Dataverse sync | contact_id={contact_id} | error={exc}",
            exc_info=True,
        )
