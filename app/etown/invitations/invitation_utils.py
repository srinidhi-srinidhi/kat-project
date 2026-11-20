"""Invitation Utilities - Postgres business logic for invitations and the MyCircle relation graph.

Domain layer behind /api/invitations: sends, verifies, accepts, declines and cancels invitations in etown_invitation with notifications and push, links invitations on registration, and maintains MyCircle member and provider relations, returning Etown_-prefixed dicts.

Features:
    - External/QR Invitation Retrieval: outgoing invitations, QR verify/re-issue and invite_role backfill
    - MyCircle Paging: get_mycircle() buckets relations by counterparty live role, paged per bucket
    - Invite Notification Feed: UNION of received/external/sent invites with deleted/deactivated flags
    - Accept / Decline / Cancel: notifications, relation writes and a push to every recipient device
    - Pair Closure: a cancel or decline closes every live request between the pair
    - MyCircle Add/Remove Provider: 'Requested' add-to-circle flow with Dataverse mirror and push/SMS
    - MyCircle Add/Remove Member: the same flow for a member, push-only to a verified member
    - Dynamic Send: placeholder-contact invitation for an unknown mobile number
    - Registration Linking: reconciles relation rows when an invitee signs up
    - New/External Invite Send: single-CTE atomic invitation + notification insert with FCM push
    - Relation Favorite Toggle: role-aware favorite flag update on a member/provider relation
    - Email Template Utility: HTML-to-plain-text conversion for the invite email subject

Classes:
    _HTMLTextExtractor(): Minimal HTML-to-plain-text converter built on stdlib HTMLParser

Functions:
    _background_fetch_invite_role(): Stamp the invitee's marketing_person_name from the inviter's name
    get_contact_invite_list(): Fetch External/QR invitations sent by a specific inviter
    verify_invite(): Verify an invitation by suffix ID; re-issues a QR invite or backfills invite_role
    _mycircle_response(): Assemble the MyCircle response from one page of records and the bucket totals
    _resolve_inviter_coordinates(): Resolve the inviter's coordinates, falling back to their provider record
    get_mycircle(): Fetch one page of MyCircle data for a user profile, bucketed by role
    get_invite_notification(): Fetch a contact's invite notifications via a three-part UNION query
    _send_invite_push_to_all_devices(): Post an invitation push to every device the user has registered
    _background_insert_cancelled_notification(): Insert a cancellation notification and send FCM push
    _background_insert_declined_notification(): Insert a declined notification and send FCM push
    _insert_accepted_relations(): Insert the role-based member/provider relation rows for an accepted invite
    link_invitation_on_registration(): Finalize the invitation a newly registered user came through
    _background_insert_accepted_notification(): Insert relations + an accepted notification, then push the inviter
    _resolve_mycircle_provider_identity(): Fill a missing provider contact/userprofile id from the providerid
    add_provider_to_mycircle(): Request to add a provider to the actor's MyCircle ('Requested' invitation + notification)
    mycircle_add_provider_deliver(): Background mirror-to-Dataverse + push/SMS for the MyCircle request
    _mycircle_remove_circle_rows(): Delete one MyCircle pairing's relation + invitation rows
    remove_provider_from_mycircle(): Remove a provider from the actor's MyCircle (reverse of add)
    mycircle_remove_deliver(): Background mirror-to-Dataverse of a MyCircle removal (both flows)
    _resolve_mycircle_member_identity(): Fill a missing member contact/userprofile id from the memberid
    add_member_to_mycircle(): Request to add a member to the actor's MyCircle ('Requested' invitation + notification)
    mycircle_add_member_deliver(): Background mirror-to-Dataverse + push for the MyCircle member request
    remove_member_from_mycircle(): Remove a member from the actor's MyCircle (reverse of add)
    accept_invite(): Accept an invitation by updating its fields in etown_invitation
    clear_all_invitations(): Mark all invitations as Inactive/Read for a given receiver
    _add_invitee_to_inviter_mycircle(): Write the inviter->invitee relation row the inviter's MyCircle reads
    _resolve_existing_invitee_and_add_to_mycircle(): Resolve an already-registered invitee and add them to MyCircle
    dynamic_send(): Send an invitation ('Requested'), creating a placeholder contact for an unknown mobile number
    new_invite_send(): Create invitation + notification atomically, send FCM push to invite contact
    external_invite_send(): Create/update an external invitation with duplicate-relation and notification checks
    _html_to_text(): Convert an HTML string to plain text by stripping all tags
    get_invite_email_template(): Fetch the invite email template with its subject converted to plain text
    mycircle_update_contact(): Store a MyCircle contact's mobile access token and mark contact/userprofile/member verified
    update_relation_favorite(): Toggle the favorite flag on the login user's member/provider relation

Dependencies:
    - app.core.logging: Centralized application logging
    - app.db.client: Async PostgreSQL database access with connection pooling
    - app.etown.devices.devices_utils: Multi-device push token lookup
    - app.etown.notifications.core: Central notification dispatcher (push / SMS delivery)
    - app.etown.notifications.core.fcm: Direct FCM OAuth token + send call used outside the dispatcher
    - app.etown.invitations.invitation_syncing: Dataverse mirror for invitations, relations and contacts
    - app.etown.plans.plans_utils: premium_display() and resolve_provider_badges() for badge labels
    - app.utils.http_client: Shared HTTP client used for direct FCM push calls

Updates:
    26-May-2026: Added module docstring following project documentation standards
    07-Jul-2026: Added new_invite_send(), a single-CTE invitation and notification insert with FCM push.
    08-Jul-2026: Added external_invite_send(), migrated from Power Automate ChatyExternalInviteSend.
    13-Jul-2026: Added add_provider_to_mycircle() and mycircle_add_provider_deliver().
    14-Jul-2026: Added mycircle_update_contact() and update_relation_favorite().
    27-Jul-2026: get_mycircle() default `limit` raised from 20 to 50 records per page
    28-Jul-2026: Invite pushes fan out to every device; get_mycircle() limit back to 20, split provider/member arrays capped at ten.
    30-Jul-2026: MyCircle membership requires consent - relations are written when the invitee accepts.
    05-Aug-2026: dynamic_send() mirrors via one ordered task; get_mycircle() drops contactdevices and buckets by live role.
    06-Aug-2026: dynamic_send() adds the invitee to the sender's MyCircle at send time.
    18-Aug-2026: MyCircle and invite notifications report the counterparty's providertype and badge_label.
    19-Aug-2026: get_mycircle(), invite_notification and contact_invite_list drop deleted counterparties.
    20-Aug-2026: get_mycircle() and get_invite_notification() carry Etown_providertype; verified markers show the plan badge.
    21-Aug-2026: get_mycircle() resolves the counterparty via the contact and omits entries with no live identity.
    02-Sep-2026: get_mycircle(), get_invite_notification() and dynamic_send() skip disabled providers.
    07-Sep-2026: Added the MyCircle member add/remove flow (invite_role 2) with a shared delete pass.
    08-Sep-2026: Cancel/decline closes the pair's other invitations; accept_invite fills missing identity only.
    23-Oct-2026: get_invite_notification() shifts Etown_invitationcreatedon forward by five hours thirty minutes.
"""

import asyncio
import html
import random
import secrets
import string
from datetime import timedelta
from html.parser import HTMLParser
from typing import Optional

from app.core.logging import logger
from app.db.client import Database
from app.utils.http_client import TrackedHTTPClient
from app.etown.devices.devices_utils import get_device_tokens_by_userprofile_id
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.etown.notifications.core.fcm import get_fcm_oauth_token, send_fcm_message
from app.etown.notifications.core import (
    NotificationRequest,
    Recipient,
    NotificationType,
    PushAudience,
)
from app.etown.invitations.invitation_syncing import (
    sync_invitation_to_dataverse,
    sync_provider_marketing_name_to_dataverse,
    sync_member_marketing_name_to_dataverse,
    sync_accepted_notification_to_dataverse,
    sync_accepted_providerrelation_to_dataverse,
    sync_accepted_memberrelation_to_dataverse,
    sync_dynamic_send_new_contact_to_dataverse,
    sync_dynamic_send_relation_to_dataverse,
    sync_dynamic_send_invitation_to_dataverse,
    sync_relation_favorite_to_dataverse,
    delete_record_from_dataverse,
)


async def _background_fetch_invite_role(
    suffix_id: str,
    invitee_contact_id: str | None,
    invitee_userprofile_id: str | None,
) -> None:
    """Set a NULL invitee provider or member marketing_person_name to the inviter's firstname and mirror it to Dataverse."""
    try:
        role_rows = await Database.fetch(
            "SELECT role FROM etown_userprofile WHERE userprofileid = $1",
            invitee_userprofile_id,
        )
        invitee_role = role_rows[0]["role"] if role_rows else None
        if invitee_role is None:
            inv_rows = await Database.fetch(
                "SELECT invite_role FROM etown_invitation WHERE suffixid = $1",
                suffix_id,
            )
            invitee_role = inv_rows[0]["invite_role"] if inv_rows else None
        logger.info(
            f"Background marketing_person_name resolve for suffix_id={suffix_id}: "
            f"invitee_role={invitee_role}"
        )

        if invitee_role == 1:
            provider_rows = await Database.fetch(
                """
                SELECT marketing_person_name, providerid
                FROM public.etown_provider
                WHERE contactid = $1
                  AND userprofileid = $2
                """,
                invitee_contact_id,
                invitee_userprofile_id,
            )
            if provider_rows:
                marketing_person_name = provider_rows[0]["marketing_person_name"]
                if marketing_person_name is None:
                    updated_provider = await Database.fetch(
                        """
                        UPDATE public.etown_provider ep
                        SET marketing_person_name = c.firstname,
                            modifieddate = NOW()
                        FROM public.etown_invitation ai
                        INNER JOIN etown_contact c
                            ON c.contactid = ai.invitercontact
                        WHERE ai.suffixid = $1
                          AND ep.contactid = $2
                          AND ep.userprofileid = $3
                        RETURNING ep.providerid, ep.marketing_person_name
                        """,
                        suffix_id,
                        invitee_contact_id,
                        invitee_userprofile_id,
                    )
                    if updated_provider:
                        asyncio.create_task(sync_provider_marketing_name_to_dataverse(
                            str(updated_provider[0]["providerid"]),
                            updated_provider[0]["marketing_person_name"],
                        ))
                    logger.info(
                        f"Background: updated provider marketing_person_name for suffix_id={suffix_id}"
                    )
                else:
                    logger.info(
                        f"Background: provider marketing_person_name already set for suffix_id={suffix_id}, no action"
                    )
        else:
            member_rows = await Database.fetch(
                """
                SELECT marketing_person_name, memberid
                FROM public.etown_member
                WHERE contactid = $1
                  AND userprofileid = $2
                """,
                invitee_contact_id,
                invitee_userprofile_id,
            )
            if member_rows:
                marketing_person_name = member_rows[0]["marketing_person_name"]
                if marketing_person_name is None:
                    updated_member = await Database.fetch(
                        """
                        UPDATE public.etown_member em
                        SET marketing_person_name = c.firstname,
                            modifieddate = NOW()
                        FROM public.etown_invitation ai
                        INNER JOIN etown_contact c
                            ON c.contactid = ai.invitercontact
                        WHERE ai.suffixid = $1
                          AND em.contactid = $2
                          AND em.userprofileid = $3
                        RETURNING em.memberid, em.marketing_person_name
                        """,
                        suffix_id,
                        invitee_contact_id,
                        invitee_userprofile_id,
                    )
                    if updated_member:
                        asyncio.create_task(sync_member_marketing_name_to_dataverse(
                            str(updated_member[0]["memberid"]),
                            updated_member[0]["marketing_person_name"],
                        ))
                    logger.info(
                        f"Background: updated member marketing_person_name for suffix_id={suffix_id}"
                    )
                else:
                    logger.info(
                        f"Background: member marketing_person_name already set for suffix_id={suffix_id}, no action"
                    )
    except Exception as e:
        logger.error(
            f"Background invite_role fetch failed for suffix_id={suffix_id}: {str(e)}",
            exc_info=True,
        )


INVITE_NOTIFICATION_QUERY = """
  SELECT
    combined.*,
    COUNT(*) OVER() AS total_count
FROM
(
    SELECT
        inv.invitationid,
        inv.type,
        inv.typename,
        inv.invitationtype,
        inv.invitationcode,
        inv.invitercontact,
        inv.invitercontactname,
        inv.inviteruserprofile,
        inv.inviteruserprofilename,
        inv.invitecontact,
        inv.invitecontactname,
        inv.inviteuserprofile,
        inv.inviteuserprofilename,
        inv.invitecontactmobilenumber,
        inv.invitecontactemail,
        inv.invite_role,
        inv.invite_rolename,
        inv.receivername,

        CASE WHEN EXISTS (
            SELECT 1 FROM public.etown_retired_identity r
             WHERE (r.identity_id = inv.inviteruserprofile
                 OR r.identity_id = inv.invitercontact)
               AND r.deleted = 'true'
        ) THEN 'true' ELSE 'false' END AS inviter_deleted,
        CASE WHEN EXISTS (
            SELECT 1 FROM public.etown_retired_identity r
             WHERE (r.identity_id = inv.inviteruserprofile
                 OR r.identity_id = inv.invitercontact)
               AND r.deactivated = 'true'
               AND r.reactivation IS DISTINCT FROM 'true'
        ) THEN 'true' ELSE 'false' END AS inviter_deactivated,
        CASE WHEN EXISTS (
            SELECT 1 FROM public.etown_retired_identity r
             WHERE (r.identity_id = inv.inviteuserprofile
                 OR r.identity_id = inv.invitecontact)
               AND r.deleted = 'true'
        ) THEN 'true' ELSE 'false' END AS invitee_deleted,
        CASE WHEN EXISTS (
            SELECT 1 FROM public.etown_retired_identity r
             WHERE (r.identity_id = inv.inviteuserprofile
                 OR r.identity_id = inv.invitecontact)
               AND r.deactivated = 'true'
               AND r.reactivation IS DISTINCT FROM 'true'
        ) THEN 'true' ELSE 'false' END AS invitee_deactivated,
        inv.suffixid,
        inv.isopponentadded,
        COALESCE(inv.modifieddate, inv.createdon) AS createdon,

        CASE
            WHEN inv.invitecontact = $1
             AND inv.inviteuserprofile =$2
                THEN up_inviter.role
            ELSE up_invitee.role
        END AS etown_role,

        CASE

    WHEN inv.invitecontact = $1
         AND inv.inviteuserprofile = $2
    THEN ic.publicsearchstatus

    ELSE ec.publicsearchstatus
END AS etown_publicsearchprovider,

        CASE
    WHEN inv.invitecontact = $1
         AND inv.inviteuserprofile = $2
    THEN p_inviter.providertype
    ELSE p_invitee.providertype
END AS etown_providertype,

        CASE
            WHEN inv.invitecontact = $1
             AND inv.inviteuserprofile =$2
            THEN
                CASE
                    WHEN up_inviter.role = 2
                        THEN ic.profileimage
                    WHEN up_inviter.role = 1
                        THEN p_inviter.serviceimageurl
                END
            ELSE
                CASE
                    WHEN up_invitee.role = 2
                        THEN ec.profileimage
                    WHEN up_invitee.role = 1
                        THEN p_invitee.serviceimageurl
                END
        END AS etown_imageurl,

        CASE
            WHEN inv.invitecontact = $1
             AND inv.inviteuserprofile =$2
            THEN
                CASE
                    WHEN up_inviter.role = 2
                        THEN ic.firstname
                    WHEN up_inviter.role = 1
                        THEN p_inviter.businessname
                END
            ELSE
                CASE
                    WHEN up_invitee.role = 2
                        THEN ec.firstname
                    WHEN up_invitee.role = 1
                        THEN p_invitee.businessname
                END
        END AS etown_name,

        COALESCE(
            inv.invitecontactmobilenumber,
            inv.invitecontactemail
        ) AS etown_contactinfo,

        CASE
            WHEN inv.invitecontact = $1
             AND inv.inviteuserprofile =$2
                THEN up_inviter.rolename
            ELSE up_invitee.rolename
        END AS etown_rolename,

        CASE
            WHEN inv.invitecontact = $1
             AND inv.inviteuserprofile =$2
                THEN inv.inviteruserprofile
            ELSE inv.inviteuserprofile
        END AS etown_counterparty_userprofile

    FROM etown_invitation inv

    LEFT JOIN etown_contact ic
        ON ic.contactid = inv.invitercontact

    LEFT JOIN etown_contact ec
        ON ec.contactid = inv.invitecontact

    LEFT JOIN etown_userprofile up_inviter
        ON up_inviter.userprofileid = inv.inviteruserprofile

    LEFT JOIN etown_userprofile up_invitee
        ON up_invitee.userprofileid = inv.inviteuserprofile

    LEFT JOIN etown_provider p_inviter
        ON p_inviter.userprofileid = inv.inviteruserprofile

    LEFT JOIN etown_provider p_invitee
        ON p_invitee.userprofileid = inv.inviteuserprofile

    WHERE

    (
        inv.invitecontact = $1
        AND inv.inviteuserprofile =$2
        AND inv.invitecontact IS NOT NULL
        AND inv.invitationcode = 'Requested'
    )

    OR

    (
        inv.invitercontact = $1
        AND inv.inviteruserprofile =$2
        AND inv.invitationtype IN ('External','ExternalApp_share')
        AND inv.invitecontact IS NULL
    )

    OR

    (
        inv.invitercontact = $1
        AND inv.inviteruserprofile =$2
        AND inv.invitecontact IS NOT NULL
        AND inv.invitationtype <> 'ExternalPublic_provider'
        AND inv.invitationcode = 'Requested'
    )

) combined

WHERE combined.inviter_deleted = 'false'
  AND combined.invitee_deleted = 'false'

  AND NOT EXISTS (
        SELECT 1 FROM etown_provider dp
         WHERE dp.disabled
           AND dp.userprofileid = combined.etown_counterparty_userprofile
      )

ORDER BY combined.createdon DESC
LIMIT $3
OFFSET $4;
"""


async def get_contact_invite_list(
    inviter_id: str,
    inviter_userprofile_id: str,
) -> list:
    """Return an inviter's Requested or Accepted External and QR invitations whose invitee is not deleted."""
    query = """
        SELECT
            invitercontact,
            invitationid,
            invitecontactemail,
            invitecontactmobilenumber,
            invitationtype,
            invitationcode,
            inviteruserprofile,
            invitecontactname,
            invitecontact,
            inviteuserprofile
        FROM etown_invitation
        WHERE invitationtype IN ('External', 'QR')
          AND invitationcode IN ('Requested', 'Accepted')
          AND invitercontact = $1
          AND inviteruserprofile = $2

          AND NOT etown_is_deleted(invitecontact)
          AND NOT etown_is_deleted(inviteuserprofile)
"""
    try:
        rows = await Database.fetch(query, inviter_id, inviter_userprofile_id)
        return [
            {
                "Etown_invitercontact": row["invitercontact"],
                "Etown_invitationid": str(row["invitationid"]),
                "Etown_invitecontactemail": row["invitecontactemail"],
                "Etown_invitecontactmobilenumber": row["invitecontactmobilenumber"],
                "Etown_invitationtype": row["invitationtype"],
                "Etown_invitationcode": row["invitationcode"],
                "Etown_inviteruserprofile": row["inviteruserprofile"],
                "Etown_invitecontactname": row["invitecontactname"],
                "Etown_invitecontact": row["invitecontact"],
                "Etown_inviteuserprofile": row["inviteuserprofile"],
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(
            f"Error fetching contact invite list for inviter_id={inviter_id}, "
            f"inviter_userprofile_id={inviter_userprofile_id}: {str(e)}",
            exc_info=True,
        )
        raise


async def verify_invite(
    suffix_id: str | None,
    invitee_contact_id: str | None,
    invitee_userprofile_id: str | None,
) -> dict:
    """Verify an invite link or QR suffix, cloning a fresh QR invitation or backfilling invite_role as needed.

    Args:
        suffix_id: Suffix of the invitation link or QR code.
        invitee_contact_id: Contact id of the person opening the invite.
        invitee_userprofile_id: Userprofile id of the person opening the invite.

    Returns:
        The new QR invitation, the suffix's invitations, an expiry/invalid message, or {}.

    Raises:
        ValueError: If suffix_id is empty.
    """
    if not suffix_id:
        raise ValueError("Suffix id is not found")

    query = """
        SELECT
            isopponentadded,
            invitationtype
        FROM etown_invitation
        WHERE suffixid = $1
    """
    count_query = """
        SELECT
            COUNT(*) AS invitation_count
        FROM etown_invitation
        WHERE inviteuserprofile = $1
          AND suffixid = $2
          AND isopponentadded = 'true'
          AND invitationcode IN ('Requested', 'Accepted')
    """
    insert_query = """
        INSERT INTO etown_invitation (
            invitationid,
            type,
            typename,
            invitationtype,
            invitercontact,
            invitercontactname,
            inviteuserprofile,
            inviteuserprofilename,
            invite_role,
            invite_rolename,
            invitationcode,
            invitecontact,
            invitecontactname,
            receivername,
            inviteruserprofile,
            inviteruserprofilename,
            isopponentadded,
            suffixid,
            createdon
        )
        SELECT
            gen_random_uuid()::text AS invitationid,
            src.type,
            src.typename,
            src.invitationtype,
            src.invitercontact,
            src.invitercontactname,
            $1 AS inviteuserprofile,
            up.name AS inviteuserprofilename,
            src.invite_role,
            src.invite_rolename,
            'Requested' AS invitationcode,
            $2 AS invitecontact,
            c.firstname AS invitecontactname,
            c.firstname AS receivername,
            src.inviteruserprofile,
            src.inviteruserprofilename,
            'false' AS isopponentadded,
            $3 AS suffixid,
            CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata' AS createdon
        FROM (
            SELECT
                type,
                typename,
                invitationtype,
                invitercontact,
                invitercontactname,
                invite_role,
                invite_rolename,
                inviteruserprofile,
                inviteruserprofilename
            FROM etown_invitation
            WHERE suffixid = $3
            ORDER BY createdon DESC
            LIMIT 1
        ) src
        LEFT JOIN etown_userprofile up
            ON up.userprofileid = $1
        LEFT JOIN etown_contact c
            ON c.contactid = $2
        RETURNING
            invitationid,
            type,
            typename,
            invitationtype,
            invitercontact,
            invitercontactname,
            inviteuserprofile,
            inviteuserprofilename,
            invite_role,
            invite_rolename,
            invitationcode,
            invitecontact,
            invitecontactname,
            receivername,
            inviteruserprofile,
            inviteruserprofilename,
            isopponentadded,
            suffixid,
            createdon
    """
    try:
        rows = await Database.fetch(query, suffix_id)
        if not rows:
            return {}

        for row in rows:
            is_opponent_added = str(row["isopponentadded"]).lower() if row["isopponentadded"] is not None else ""
            if is_opponent_added == "true":
                invitation_type = str(row["invitationtype"]).upper() if row["invitationtype"] else ""
                if invitation_type == "QR":
                    count_rows = await Database.fetch(count_query, invitee_userprofile_id, suffix_id)
                    invitation_count = count_rows[0]["invitation_count"] if count_rows else 0
                    if invitation_count > 0:
                        return {"message": "QR code is expired"}
                    inserted = await Database.fetch(insert_query, invitee_userprofile_id, invitee_contact_id, suffix_id)
                    if not inserted:
                        return {}
                    new_row = inserted[0]
                    asyncio.create_task(sync_invitation_to_dataverse(dict(new_row)))
                    asyncio.create_task(
                        _background_fetch_invite_role(suffix_id, invitee_contact_id, invitee_userprofile_id)
                    )
                    return {
                        "Etown_invitationid": new_row["invitationid"],
                        "Etown_type": new_row["type"],
                        "Etown_typename": new_row["typename"],
                        "Etown_invitationtype": new_row["invitationtype"],
                        "Etown_invitercontact": new_row["invitercontact"],
                        "Etown_invitercontactname": new_row["invitercontactname"],
                        "Etown_inviteuserprofile": new_row["inviteuserprofile"],
                        "Etown_inviteuserprofilename": new_row["inviteuserprofilename"],
                        "Etown_invite_role": new_row["invite_role"],
                        "Etown_invite_rolename": new_row["invite_rolename"],
                        "Etown_invitationcode": new_row["invitationcode"],
                        "Etown_invitecontact": new_row["invitecontact"],
                        "Etown_invitecontactname": new_row["invitecontactname"],
                        "Etown_receivername": new_row["receivername"],
                        "Etown_inviteruserprofile": new_row["inviteruserprofile"],
                        "Etown_inviteruserprofilename": new_row["inviteruserprofilename"],
                        "Etown_isopponentadded": new_row["isopponentadded"],
                        "Etown_suffixid": new_row["suffixid"],
                        "Etown_createdon": str(new_row["createdon"]),
                    }
                else:
                    return {"message": "Link is expired"}
            else:
                invite_role_check_query = """
                    SELECT invite_role
                    FROM etown_invitation
                    WHERE suffixid = $1
                """
                invite_role_rows = await Database.fetch(invite_role_check_query, suffix_id)
                invite_role_is_null = (
                    not invite_role_rows
                    or any(r["invite_role"] is None for r in invite_role_rows)
                )
                if invite_role_is_null:
                    update_invite_role_query = """
                        UPDATE etown_invitation n
                        SET
                            invite_role = up.role,
                            invite_rolename = up.rolename,
                            modifieddate = NOW()
                        FROM etown_userprofile up
                        WHERE up.userprofileid = $2
                          AND n.suffixid = $1
                    """
                    await Database.execute(update_invite_role_query, suffix_id, invitee_userprofile_id)

                suffix_count_query = """
                    SELECT
                        COUNT(*) AS invitation_count
                    FROM etown_invitation
                    WHERE suffixid = $1
                """
                suffix_count_rows = await Database.fetch(suffix_count_query, suffix_id)
                invitation_count = suffix_count_rows[0]["invitation_count"] if suffix_count_rows else 0
                if invitation_count > 0:
                    suffix_rows = await Database.fetch(
                        "SELECT * FROM etown_invitation WHERE suffixid = $1",
                        suffix_id,
                    )
                    if invite_role_is_null:
                        for updated_row in suffix_rows:
                            asyncio.create_task(sync_invitation_to_dataverse(dict(updated_row)))
                    response = {"invitations": [dict(r) for r in suffix_rows]}
                else:
                    response = {"message": "Suffix ID Invalid"}
                asyncio.create_task(_background_fetch_invite_role(suffix_id, invitee_contact_id, invitee_userprofile_id))
                return response

        return {}
    except ValueError:
        raise
    except Exception as e:
        logger.error(
            f"Error verifying invite for suffix_id={suffix_id}: {str(e)}",
            exc_info=True,
        )
        raise


_MYCIRCLE_BUCKET_EXPR = """
    CASE
        WHEN role = 1 THEN 'provider'
        WHEN role = 2 THEN 'member'
        WHEN lower(btrim(invite_relationshiptypename)) IN ('provider', 'member')
            THEN lower(btrim(invite_relationshiptypename))
        ELSE 'member'
    END
"""


_MYCIRCLE_PROVIDER_BODY = """
        SELECT DISTINCT ON (up.userprofileid)
        pr.favorite,
        pr.favoritename,
        pr.relationshiptype,
        pr.invite_relationshiptype,
        pr.invite_relationshiptypename,
        pr.providerrelationid,

        up.role,
        up.rolename,
        up.userprofileid,

        c.firstname,
        etown_deleted_flag(c.contactid)     AS contact_deleted,
        etown_deactivated_flag(c.contactid) AS contact_deactivated,
        c.verified,
        NULL AS c_null1,
        c.email AS contact_email,
        c.rolecode,
        NULL AS c_null2,
        c.mobilephone,
        NULL AS c_null3,
        NULL AS c_null4,
        NULL AS c_null5,
        NULL AS c_null6,
        c.contactid,
        c.profileimage,
        c.address AS contact_address,
        c.latitude AS contact_latitude,
        c.longitude AS contact_longitude,
        NULL AS c_null7,
        c.city AS contact_city,
        c.statecode AS contact_statecode,
        c.createdon,
        c.connectycubeid,

        p.address AS provider_address,
        p.businessname,
        p.businesscategory,
        p.yearsinbusiness,
        p.serviceimageurl,
        p.email AS provider_email,
        p.mobilenumber,
        p.latitude AS provider_latitude,
        p.longitude AS provider_longitude,
        p.contactid AS provider_contactid,
        p.contactidname,
        p.userprofileid AS provider_userprofileid,
        p.providerid,
        p.publicsearchprovider,
        p.providertype,
        NULL AS p_null1,
        p.city AS provider_city,
        p.statecode AS provider_statecode,
        p.creationdate,
        p.address AS provider_address2

    FROM etown_providerrelation pr

    LEFT JOIN LATERAL (
        SELECT up2.*
        FROM etown_userprofile up2
        WHERE up2.userprofileid = pr.touserprofileid
           OR up2.contactid = pr.tocontactid
        ORDER BY
            (up2.userprofileid = pr.touserprofileid) DESC,
            (
                lower(btrim(COALESCE(pr.invite_relationshiptypename, ''))) =
                CASE up2.role WHEN 1 THEN 'provider' WHEN 2 THEN 'member' ELSE '' END
            ) DESC,
            up2.createdon DESC NULLS LAST,
            up2.userprofileid
        LIMIT 1
    ) up ON TRUE

    LEFT JOIN etown_contact c
        ON c.contactid = COALESCE(pr.tocontactid, up.contactid)

    LEFT JOIN LATERAL (
        SELECT p2.*
        FROM etown_provider p2
        WHERE p2.userprofileid = up.userprofileid
        ORDER BY p2.creationdate DESC NULLS LAST, p2.providerid
        LIMIT 1
    ) p ON TRUE

    WHERE
        pr.fromcontactid = $1
        AND pr.fromuserprofileid = $2
        AND c.contactid IS NOT NULL
        AND up.userprofileid IS NOT NULL
        AND NOT etown_is_deleted(c.contactid)

        AND NOT EXISTS (
            SELECT 1
            FROM etown_provider dp
            WHERE dp.disabled
              AND dp.userprofileid = up.userprofileid
        )

        ORDER BY up.userprofileid,
                 (pr.touserprofileid = up.userprofileid) DESC NULLS LAST,
                 pr.providerrelationid
"""


MYCIRCLE_PROVIDER_QUERY = f"""
    SELECT * FROM ({_MYCIRCLE_PROVIDER_BODY}) dedup
    WHERE ({_MYCIRCLE_BUCKET_EXPR}) = $3
    ORDER BY
        contactid,
        userprofileid,
        providerrelationid
    LIMIT $4 OFFSET $5
"""


MYCIRCLE_PROVIDER_COUNT_QUERY = f"""
    SELECT ({_MYCIRCLE_BUCKET_EXPR}) AS bucket, COUNT(*) AS bucket_count
    FROM ({_MYCIRCLE_PROVIDER_BODY}) dedup
    GROUP BY 1
"""


_MYCIRCLE_MEMBER_BODY = """
        SELECT DISTINCT ON (up.userprofileid)
        mr.favorite,
        mr.favoritename,
        mr.relationshiptype,
        mr.relationshiptypename,
        mr.invite_relationshiptype,
        mr.invite_relationshiptypename,
        mr.memberrelationid,

        up.role,
        up.rolename,
        up.userprofileid,

        c.firstname,
        etown_deleted_flag(c.contactid)     AS contact_deleted,
        etown_deactivated_flag(c.contactid) AS contact_deactivated,
        c.verified,
        NULL AS c_null1,
        NULL AS c_null2,
        c.email AS contact_email,
        c.rolecode,
        NULL AS c_null3,
        c.mobilephone,
        NULL AS address1_county,
        NULL AS address1_primarycontactname,
        NULL AS ets_language,
        NULL AS ets_termsandcondition,
        c.contactid,
        c.connectycubeid,
        c.address AS contact_address,
        c.profileimage AS ets_profileimage,
        c.latitude AS address1_latitude,
        c.longitude AS address1_longitude,
        c.userlogin AS ets_connectycubeid,
        c.city AS contact_city,
        c.statecode AS ets_statecode,
        c.createdon AS contact_createdon,

        p.address AS provider_address,
        p.businessname,
        p.businesscategory,
        p.yearsinbusiness,
        p.serviceimageurl,
        p.email AS provider_email,
        p.mobilenumber AS provider_mobile,
        p.latitude AS provider_latitude,
        p.longitude AS provider_longitude,
        p.contactid AS provider_contact_id,
        p.contactidname,
        p.userprofileid AS provider_userprofileid,
        p.providerid,
        p.publicsearchprovider,
        p.providertype,
        NULL AS servicecategory,
        p.city AS provider_city,
        p.statecode AS provider_statecode,
        p.creationdate AS provider_createdon

    FROM etown_memberrelation mr

    LEFT JOIN LATERAL (
        SELECT up2.*
        FROM etown_userprofile up2
        WHERE up2.userprofileid = mr.to_userprofile
           OR up2.contactid = mr.to_contactid
        ORDER BY
            (up2.userprofileid = mr.to_userprofile) DESC,
            (
                lower(btrim(COALESCE(mr.invite_relationshiptypename, ''))) =
                CASE up2.role WHEN 1 THEN 'provider' WHEN 2 THEN 'member' ELSE '' END
            ) DESC,
            up2.createdon DESC NULLS LAST,
            up2.userprofileid
        LIMIT 1
    ) up ON TRUE

    LEFT JOIN etown_contact c
        ON c.contactid = COALESCE(mr.to_contactid, up.contactid)

    LEFT JOIN LATERAL (
        SELECT p2.*
        FROM etown_provider p2
        WHERE p2.userprofileid = up.userprofileid
        ORDER BY p2.creationdate DESC NULLS LAST, p2.providerid
        LIMIT 1
    ) p ON TRUE

    WHERE
        mr.from_contactid = $1
        AND mr.from_userprofile = $2
        AND c.contactid IS NOT NULL
        AND up.userprofileid IS NOT NULL
        AND NOT etown_is_deleted(c.contactid)

        AND NOT EXISTS (
            SELECT 1
            FROM etown_provider dp
            WHERE dp.disabled
              AND dp.userprofileid = up.userprofileid
        )

        AND (
            p.providerid IS NOT NULL
            OR up.verified IS NOT TRUE
            OR EXISTS (
                SELECT 1
                FROM etown_member m2
                WHERE m2.userprofileid = up.userprofileid
            )
        )

        ORDER BY up.userprofileid,
                 (mr.to_userprofile = up.userprofileid) DESC NULLS LAST,
                 mr.memberrelationid
"""


MYCIRCLE_MEMBER_QUERY = f"""
    SELECT * FROM ({_MYCIRCLE_MEMBER_BODY}) dedup
    WHERE ({_MYCIRCLE_BUCKET_EXPR}) = $3
    ORDER BY
        contactid,
        providerid,
        userprofileid,
        memberrelationid
    LIMIT $4 OFFSET $5
"""


MYCIRCLE_MEMBER_COUNT_QUERY = f"""
    SELECT ({_MYCIRCLE_BUCKET_EXPR}) AS bucket, COUNT(*) AS bucket_count
    FROM ({_MYCIRCLE_MEMBER_BODY}) dedup
    GROUP BY 1
"""


def _mycircle_response(
    role: int,
    page_number: int,
    page_size: int,
    data: list,
    provider_page_count: int,
    count_rows: list,
) -> dict:
    """Build the My Circle page response with provider and member arrays, bucket totals and has_more.

    Args:
        role: Requesting profile's role, 1 for provider, 2 for member.
        page_number: 1-based page that produced data.
        page_size: Records requested per array for this page.
        data: Provider records followed by member records.
        provider_page_count: Number of leading provider records in data.
        count_rows: (bucket, bucket_count) rows from the bucket count query.

    Returns:
        Role and page info, has_more, provider and member totals, and both arrays.
    """
    totals = {row["bucket"]: int(row["bucket_count"]) for row in count_rows}
    provider_total = totals.get("provider", 0)
    member_total = totals.get("member", 0)

    provider_page = data[:provider_page_count]
    member_page = data[provider_page_count:]

    page_offset = (page_number - 1) * page_size
    has_more = (
        page_offset + len(provider_page) < provider_total
        or page_offset + len(member_page) < member_total
    )

    return {
        "Etown_role": role,
        "page_number": page_number,
        "page_size": page_size,
        "has_more": has_more,
        "data_count": provider_total + member_total,
        "provider_count": provider_total,
        "member_count": member_total,
        "provider": provider_page,
        "member": member_page,
    }


async def _resolve_inviter_coordinates(
    user_profile_id: str,
    inviter_latitude: str | None,
    inviter_longitude: str | None,
) -> tuple[float, float]:
    """Return the caller-supplied inviter coordinates, else the provider record's, else (0.0, 0.0)."""
    if inviter_latitude and inviter_longitude:
        return float(inviter_latitude), float(inviter_longitude)

    coord_rows = await Database.fetch(
        "SELECT latitude, longitude FROM etown_provider WHERE userprofileid = $1",
        user_profile_id,
    )
    if coord_rows and coord_rows[0]["latitude"] is not None:
        return float(coord_rows[0]["latitude"]), float(coord_rows[0]["longitude"])
    return 0.0, 0.0


async def get_mycircle(
    user_profile_id: str,
    inviter_id: str,
    inviter_latitude: str | None = None,
    inviter_longitude: str | None = None,
    page_number: int = 1,
    limit: int = 10,
) -> dict:
    """Fetch one page of My Circle providers and members from the profile's provider or member relations.

    Args:
        user_profile_id: Profile whose role picks the relation table.
        inviter_id: The inviter's contact id.
        inviter_latitude: Inviter latitude; falls back to etown_provider when empty.
        inviter_longitude: Inviter longitude; falls back to etown_provider when empty.
        page_number: 1-based page number.
        limit: Records per page in each array.

    Returns:
        The My Circle page response, or {} when the profile does not exist.

    Raises:
        ValueError: If the profile's role is neither 1 nor 2.
    """
    role_rows = await Database.fetch(
        "SELECT role FROM etown_userprofile WHERE userprofileid = $1",
        user_profile_id,
    )
    if not role_rows:
        return {}

    role = role_rows[0]["role"]
    page_number = max(page_number, 1)
    page_offset = (page_number - 1) * limit

    if role == 1:
        inv_lat, inv_lon = await _resolve_inviter_coordinates(
            user_profile_id, inviter_latitude, inviter_longitude
        )

        provider_rows, member_rows, count_rows = await asyncio.gather(
            Database.fetch(
                MYCIRCLE_PROVIDER_QUERY,
                inviter_id, user_profile_id, "provider", limit, page_offset,
            ),
            Database.fetch(
                MYCIRCLE_PROVIDER_QUERY,
                inviter_id, user_profile_id, "member", limit, page_offset,
            ),
            Database.fetch(
                MYCIRCLE_PROVIDER_COUNT_QUERY, inviter_id, user_profile_id
            ),
        )
        rows = list(provider_rows) + list(member_rows)
        badge_map = await resolve_provider_badges(row["userprofileid"] for row in rows)
        data = [
                {
                    "Etown_favoritename": row["favoritename"],
                    "Etown_favorite": row["favorite"],
                    "Etown_relationshiptypename": row["invite_relationshiptypename"],
                    "Etown_relationshiptype": row["relationshiptype"],
                    "Etown_providerrelationid": row["providerrelationid"],
                    "Etown_contactlongitude": row["contact_longitude"],
                    "Etown_contactlatitude": row["contact_latitude"],
                    "Etown_connectycubeid": row["connectycubeid"],
                    "Etown_contactaddress": row["contact_address"],
                    "Etown_contactid": row["contactid"],
                    "Etown_firstname": (
                        (row["businessname"] or row["contactidname"] or row["firstname"])
                        if row["providerid"] is not None else row["firstname"]
                    ),
                    "Etown_contactcreatedon": (
                        str(row["creationdate"]) if row["role"] == 1 and row["creationdate"]
                        else str(row["createdon"]) if row["createdon"]
                        else None
                    ),
                    "Etown_mobilenumber": row["mobilephone"],
                    "Etown_businessname": row["businessname"],
                    "Etown_provideraddress": row["provider_address"],
                    "Etown_providerlatitude": row["provider_latitude"],
                    "Etown_providerlongitude": row["provider_longitude"],
                    "Etown_providername": row["contactidname"],
                    "Etown_providercontact": row["provider_contactid"],
                    "Etown_providerid": row["providerid"],
                    "Etown_rolename": row["rolename"],
                    "Etown_role": row["role"],
                    "Etown_providercreatedon": str(row["creationdate"]) if row["creationdate"] else None,
                    "Etown_yearinbusiness": row["yearsinbusiness"],
                    "Etown_provideremail": row["provider_email"],
                    "Etown_userprofileid": row["userprofileid"],
                    "Etown_imageurl": (
                        row["profileimage"] if row["role"] == 2
                        else row["serviceimageurl"] if row["role"] == 1
                        else None
                    ),
                    "Etown_publicsearchprovider": row["publicsearchprovider"],
                    "Etown_address": (
                        row["contact_address"] if row["role"] == 2
                        else row["provider_address"] if row["role"] == 1
                        else None
                    ),
                    "Etown_city": (
                        row["contact_city"] if row["role"] == 2
                        else row["provider_city"] if row["role"] == 1
                        else None
                    ),
                    "Etown_statecode": (
                        row["contact_statecode"] if row["role"] == 2
                        else row["provider_statecode"] if row["role"] == 1
                        else None
                    ),
                    "Etown_memberdistance": (
                        111.32 * (float(row["contact_latitude"] or 0) - inv_lat) ** 2
                        + 111.32 * 0.974 * (float(row["contact_longitude"] or 0) - inv_lon) ** 2
                    ),
                    "Etown_providerdistance": (
                        111.32 * (float(row["provider_latitude"] or 0) - inv_lat) ** 2
                        + 111.32 * 0.974 * (float(row["provider_longitude"] or 0) - inv_lon) ** 2
                    ),
                    "Etown_ccUserID": None,
                    "Etown_InviteRelationShipType": row["invite_relationshiptype"],
                    "Etown_InviteRelationShipTypeName": row["invite_relationshiptypename"],
                    "Etown_userverified": row["verified"],
                    "Etown_deleted": row["contact_deleted"],
                    "Etown_deactivated": row["contact_deactivated"],
                    "Etown_providertype": (badge_map.get(row["userprofileid"]) or {}).get("provider_type"),
                    "Etown_badge_label": (badge_map.get(row["userprofileid"]) or {}).get("badge_label"),
                }
                for row in rows
            ]
        return _mycircle_response(
            role, page_number, limit, data, len(provider_rows), count_rows
        )

    if role == 2:
        inv_lat, inv_lon = await _resolve_inviter_coordinates(
            user_profile_id, inviter_latitude, inviter_longitude
        )

        provider_rows, member_rows, count_rows = await asyncio.gather(
            Database.fetch(
                MYCIRCLE_MEMBER_QUERY,
                inviter_id, user_profile_id, "provider", limit, page_offset,
            ),
            Database.fetch(
                MYCIRCLE_MEMBER_QUERY,
                inviter_id, user_profile_id, "member", limit, page_offset,
            ),
            Database.fetch(
                MYCIRCLE_MEMBER_COUNT_QUERY, inviter_id, user_profile_id
            ),
        )
        rows = list(provider_rows) + list(member_rows)
        badge_map = await resolve_provider_badges(row["userprofileid"] for row in rows)
        data = [
                {
                    "Etown_favoritename": row["favoritename"],
                    "Etown_favorite": row["favorite"],
                    "Etown_relationshiptypename": row["relationshiptypename"],
                    "Etown_relationshiptype": row["relationshiptype"],
                    "Etown_memberrelationid": row["memberrelationid"],
                    "Etown_contactlongitude": row["address1_longitude"],
                    "Etown_contactlatitude": row["address1_latitude"],
                    "Etown_connectycubeid": row["connectycubeid"],
                    "Etown_contactaddress": row["contact_address"],
                    "Etown_contactid": row["contactid"],
                    "Etown_firstname": (
                        (row["businessname"] or row["contactidname"] or row["firstname"])
                        if row["providerid"] is not None else row["firstname"]
                    ),
                    "Etown_contactcreatedon": (
                        str(row["provider_createdon"]) if row["role"] == 1 and row["provider_createdon"]
                        else str(row["contact_createdon"]) if row["contact_createdon"]
                        else None
                    ),
                    "Etown_mobilenumber": row["mobilephone"],
                    "Etown_businessname": row["businessname"],
                    "Etown_provideraddress": row["provider_address"],
                    "Etown_providerlatitude": row["provider_latitude"],
                    "Etown_providerlongitude": row["provider_longitude"],
                    "Etown_providername": row["contactidname"],
                    "Etown_providercontact": row["provider_contact_id"],
                    "Etown_providerid": row["providerid"],
                    "Etown_rolename": row["rolename"],
                    "Etown_role": row["role"],
                    "Etown_providercreatedon": str(row["provider_createdon"]) if row["provider_createdon"] else None,
                    "Etown_yearinbusiness": row["yearsinbusiness"],
                    "Etown_provideremail": row["provider_email"],
                    "Etown_userprofileid": row["userprofileid"],
                    "Etown_imageurl": (
                        row["ets_profileimage"] if row["role"] == 2
                        else row["serviceimageurl"] if row["role"] == 1
                        else None
                    ),
                    "Etown_publicsearchprovider": row["publicsearchprovider"],
                    "Etown_address": (
                        row["contact_address"] if row["role"] == 2
                        else row["provider_address"] if row["role"] == 1
                        else None
                    ),
                    "Etown_city": (
                        row["contact_city"] if row["role"] == 2
                        else row["provider_city"] if row["role"] == 1
                        else None
                    ),
                    "Etown_statecode": (
                        row["ets_statecode"] if row["role"] == 2
                        else row["provider_statecode"] if row["role"] == 1
                        else None
                    ),
                    "Etown_memberdistance": (
                        111.32 * (float(row["address1_latitude"] or 0) - inv_lat) ** 2
                        + 111.32 * 0.974 * (float(row["address1_longitude"] or 0) - inv_lon) ** 2
                    ),
                    "Etown_providerdistance": (
                        111.32 * (float(row["provider_latitude"] or 0) - inv_lat) ** 2
                        + 111.32 * 0.974 * (float(row["provider_longitude"] or 0) - inv_lon) ** 2
                    ),
                    "Etown_ccUserID": None,
                    "Etown_InviteRelationShipType": row["invite_relationshiptype"],
                    "Etown_InviteRelationShipTypeName": row["invite_relationshiptypename"],
                    "Etown_userverified": row["verified"],
                    "Etown_deleted": row["contact_deleted"],
                    "Etown_deactivated": row["contact_deactivated"],
                    "Etown_providertype": (badge_map.get(row["userprofileid"]) or {}).get("provider_type"),
                    "Etown_badge_label": (badge_map.get(row["userprofileid"]) or {}).get("badge_label"),
                }
                for row in rows
            ]
        return _mycircle_response(
            role, page_number, limit, data, len(provider_rows), count_rows
        )

    raise ValueError(f"Role {role} is invalid")


async def get_invite_notification(
    login_contact_id: str,
    user_profile_id: str,
    invitation_page_number: int = 1,
) -> list:
    """Return a 20-row page of received and sent invite notifications for a contact, with the total count.

    Args:
        login_contact_id: Contact whose invitations are listed.
        user_profile_id: That contact's userprofile id.
        invitation_page_number: 0-based page index; the offset is page times 20.

    Returns:
        Dict with the "invitations" list and "Total_count".
    """
    page_size = 20
    page_offset = invitation_page_number * page_size
    try:
        rows = await Database.fetch(
            INVITE_NOTIFICATION_QUERY,
            login_contact_id,
            user_profile_id,
            page_size,
            page_offset,
        )
        total_count = rows[0]["total_count"] if rows else 0
        badge_map = await resolve_provider_badges(
            row["etown_counterparty_userprofile"] for row in rows
        )
        return {
            "invitations": [
                {
                    "Etown_invitecontact": row["invitecontact"],
                    "Etown_invitecontactuserprofile": row["inviteuserprofile"],
                    "Etown_inviterole": row["invite_role"],
                    "Etown_invitationtype": row["invitationtype"],
                    "Etown_invitationid": str(row["invitationid"]) if row["invitationid"] else None,
                    "Etown_invitationcreatedon": str(row["createdon"] + timedelta(hours=5, minutes=30)) if row["createdon"] else None,
                    "Etown_publicsearchprovider": row["etown_publicsearchprovider"],
                    "Etown_role": row["etown_role"],
                    "Etown_invitationcode": row["invitationcode"],
                    "Etown_imageurl": row["etown_imageurl"],
                    "Etown_contactinfo": row["etown_contactinfo"],
                    "Etown_inviterolename": row["etown_rolename"],
                    "Etown_name": row["etown_name"],
                    "Etown_invitercontact": row["invitercontact"],
                    "Etown_invitercontactuserprofile": row["inviteruserprofile"],
                    "Etown_receivername": row["receivername"],
                    "Etown_inviterdeleted": row["inviter_deleted"],
                    "Etown_inviterdeactivated": row["inviter_deactivated"],
                    "Etown_inviteedeleted": row["invitee_deleted"],
                    "Etown_inviteedeactivated": row["invitee_deactivated"],
                    "Etown_providertype": (badge_map.get(row["etown_counterparty_userprofile"]) or {}).get("provider_type"),
                    "Etown_badge_label": (badge_map.get(row["etown_counterparty_userprofile"]) or {}).get("badge_label"),
                    "Etown_invitationcount": total_count,
                }
                for row in rows
            ],
            "Total_count": total_count,
        }
    except Exception as e:
        logger.error(
            f"Error fetching invite notification for login_contact_id={login_contact_id}, "
            f"user_profile_id={user_profile_id}: {str(e)}",
            exc_info=True,
        )
        raise


ACCEPT_INVITE_QUERY = """
    UPDATE etown_invitation
    SET
        new_inviterole = $1::int,
        new_inviterolename = CASE
            WHEN $1::int = 1 THEN 'Provider'
            WHEN $1::int = 2 THEN 'Member'
            ELSE NULL
        END,

        invitercontact = COALESCE(invitercontact, $2),
        invitercontactname = (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = COALESCE(etown_invitation.invitercontact, $2)
            LIMIT 1
        ),

        inviteuserprofile = COALESCE(inviteuserprofile, $3),
        inviteuserprofilename = (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = COALESCE(etown_invitation.inviteuserprofile, $3)
            LIMIT 1
        ),

        invitationcode = $4,

        invitecontact = COALESCE(invitecontact, $5),
        invitecontactname = (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = COALESCE(etown_invitation.invitecontact, $5)
            LIMIT 1
        ),

        inviteruserprofile = COALESCE(inviteruserprofile, $6),
        inviteruserprofilename = (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = COALESCE(etown_invitation.inviteruserprofile, $6)
            LIMIT 1
        ),

        modifieddate = NOW()

    WHERE invitationid = $7

    RETURNING
        type,
        typename,
        invitercontact,
        invitercontactname,
        invitecontact,
        invitecontactname,
        invitationcode;
"""

CLOSE_PAIR_INVITATIONS_QUERY = """
    UPDATE etown_invitation
    SET invitationcode = $1,
        modifieddate = NOW()
    WHERE invitationid <> $2
      AND invitercontact = $3
      AND invitecontact = $4
      AND invitationtype IN ('Internal', 'Added to MyCircle')
      AND LOWER(invitationcode) IN ('requested', 'pending')
    RETURNING *;
"""


CANCELLED_NOTIFICATION_QUERY = """
    INSERT INTO etown_notifications (
        notificationsid,
        notificationcontent,

        receivercontactid,
        receivercontactidname,
        receiveruserprofileid,
        receiveruserprofileidname,

        role,
        rolename,

        sendercontactid,
        sendercontactidname,
        senderuserprofileid,
        senderuserprofileidname,

        notificationstatus,
        statuscode,
        statusreason,
        notificationtype,
        createdon
    )
    VALUES (
        gen_random_uuid()::text,

        CONCAT(
            'Your invitation has been cancelled by ',
            COALESCE(
                NULLIF(
                    CASE
                        WHEN COALESCE(
                                 $1::int,
                                 (
                                     SELECT role
                                     FROM etown_userprofile
                                     WHERE userprofileid = $2
                                     LIMIT 1
                                 )
                             ) = 1
                        THEN (
                            SELECT businessname
                            FROM etown_providerservices
                            WHERE userprofileid = $2
                            LIMIT 1
                        )
                    END,
                    ''
                ),
                NULLIF($3::text, ''),
                NULLIF(
                    (
                        SELECT firstname
                        FROM etown_contact
                        WHERE contactid = $7
                        LIMIT 1
                    ),
                    ''
                ),
                NULLIF(
                    (
                        SELECT name
                        FROM etown_userprofile
                        WHERE userprofileid = $2
                        LIMIT 1
                    ),
                    ''
                ),
                'Someone'
            )
        ),

        $4,
        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $4
            LIMIT 1
        ),
        $5,
        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $5
            LIMIT 1
        ),

        $6,
        CASE
            WHEN $6 = 1 THEN 'Provider'
            WHEN $6 = 2 THEN 'Member'
            ELSE NULL
        END,

        $7,
        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $7
            LIMIT 1
        ),
        $2,
        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        $8,
        '2',
        'Read',
        'Invitation',
        NOW()
    )
    RETURNING notificationcontent;
"""


async def _send_invite_push_to_all_devices(
    userprofile_id: str | None,
    oauth: str,
    notification_content: str,
    data: dict,
) -> None:
    """Send an FCM invitation push to every device token registered for the userprofile."""
    device_tokens = await get_device_tokens_by_userprofile_id(userprofile_id or "")
    if not device_tokens:
        logger.info(
            f"Background: no device token found for userprofileid={userprofile_id}, skipping FCM push"
        )
        return

    async with TrackedHTTPClient() as http:
        for device_token in device_tokens:
            fcm_payload = {
                "message": {
                    "token": device_token,
                    "notification": {
                        "title": "etown",
                        "body": notification_content,
                    },
                    "data": data,
                }
            }
            response = await http.post(
                "https://fcm.googleapis.com/v1/projects/etowndev/messages:send",
                json=fcm_payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {oauth}",
                },
            )
            logger.info(
                f"Background: FCM push sent "
                f"| status={response.status_code} | device={device_token}"
            )


async def _background_insert_cancelled_notification(
    invitecontact_role: int | None,
    invite_contact_userprofile_id: str | None,
    invite_contact_firstname: str | None,
    inviter: str | None,
    inviter_userprofile_id: str | None,
    invite_role: int | None,
    invite_contact: str | None,
    invitation_code: str | None,
    oauth: str | None = None,
) -> None:
    """Insert an invitation-cancelled notification and push it to the inviter's devices when oauth is given."""
    try:
        rows = await Database.fetch(
            CANCELLED_NOTIFICATION_QUERY,
            invitecontact_role,
            invite_contact_userprofile_id,
            invite_contact_firstname,
            inviter,
            inviter_userprofile_id,
            invite_role,
            invite_contact,
            invitation_code,
        )
        notification_content = rows[0]["notificationcontent"] if rows else None
        logger.info(
            f"Background: cancelled notification inserted "
            f"| content={notification_content}"
        )

        if not oauth or not notification_content:
            logger.info(
                "Background: skipping FCM push — oauth or notificationcontent missing"
            )
            return

        await _send_invite_push_to_all_devices(
            inviter_userprofile_id,
            oauth,
            notification_content,
            {
                "type": "Invite",
                "userprofile": inviter_userprofile_id or "",
                "route": "/invite",
            },
        )

    except Exception as e:
        logger.error(
            f"Background: failed to insert cancelled notification: {str(e)}",
            exc_info=True,
        )


DECLINED_NOTIFICATION_QUERY = """
    INSERT INTO etown_notifications (
        notificationsid,
        notificationcontent,

        receivercontactid,
        receivercontactidname,
        receiveruserprofileid,
        receiveruserprofileidname,

        role,
        rolename,

        sendercontactid,
        sendercontactidname,
        senderuserprofileid,
        senderuserprofileidname,

        notificationstatus,
        statuscode,
        statusreason,
        notificationtype,
        createdon
    )
    VALUES (
        gen_random_uuid()::text,

        CASE
            WHEN $1 = 1 THEN CONCAT(
                'Your invitation has been declined by ',
                (
                    SELECT businessname
                    FROM etown_providerservices
                    WHERE userprofileid = $2
                    LIMIT 1
                )
            )
            ELSE CONCAT(
                'Your invitation has been declined by ',
                $3
            )
        END,

        $4,
        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $4
            LIMIT 1
        ),
        $5,
        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $5
            LIMIT 1
        ),

        $6,
        CASE
            WHEN $6 = 1 THEN 'Provider'
            WHEN $6 = 2 THEN 'Member'
            ELSE NULL
        END,

        $7,
        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $7
            LIMIT 1
        ),
        $2,
        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        $8,
        '2',
        'Read',
        'Invitation',
        NOW()
    )
    RETURNING notificationcontent;
"""


async def _background_insert_declined_notification(
    invitecontact_role: int | None,
    inviter_userprofile_id: str | None,
    invite_contact_firstname: str | None,
    invite_contact: str | None,
    invite_contact_userprofile_id: str | None,
    invite_role: int | None,
    inviter: str | None,
    invitation_code: str | None,
    oauth: str | None = None,
) -> None:
    """Insert an invitation-declined notification and attempt an FCM push to the invite contact's devices."""
    try:
        rows = await Database.fetch(
            DECLINED_NOTIFICATION_QUERY,
            invitecontact_role,
            inviter_userprofile_id,
            invite_contact_firstname,
            invite_contact,
            invite_contact_userprofile_id,
            invite_role,
            inviter,
            invitation_code,
        )
        notification_content = rows[0]["notificationcontent"] if rows else None
        logger.info(
            f"Background: declined notification inserted "
            f"| content={notification_content}"
        )

        if not oauth or not notification_content:
            logger.info(
                "Background: skipping FCM push — oauth or notificationcontent missing"
            )
            return

        await _send_invite_push_to_all_devices(
            invite_contact_userprofile_id,
            oauth,
            notification_content,
            {
                "type": "Invite",
                "userprofile": inviter_userprofile_id or "",
                "route": "/invite",
            },
        )

    except Exception as e:
        logger.error(
            f"Background: failed to insert declined notification: {str(e)}",
            exc_info=True,
        )


ACCEPTED_NOTIFICATION_QUERY = """
    INSERT INTO etown_notifications (
        notificationsid,
        notificationcontent,
        receivercontactid,
        receivercontactidname,
        receiveruserprofileid,
        receiveruserprofileidname,
        role,
        rolename,
        sendercontactid,
        sendercontactidname,
        senderuserprofileid,
        senderuserprofileidname,
        notificationstatus,
        notificationtype,
        createdon
    )
    VALUES (
        gen_random_uuid()::text,

        CONCAT(
            COALESCE(
                NULLIF(
                    CASE
                        WHEN COALESCE(
                                 $1::int,
                                 (
                                     SELECT role
                                     FROM etown_userprofile
                                     WHERE userprofileid = $2
                                     LIMIT 1
                                 )
                             ) = 1
                        THEN (
                            SELECT businessname
                            FROM etown_providerservices
                            WHERE userprofileid = $2
                            LIMIT 1
                        )
                    END,
                    ''
                ),
                NULLIF($3::text, ''),
                NULLIF(
                    (
                        SELECT firstname
                        FROM etown_contact
                        WHERE contactid = $7
                        LIMIT 1
                    ),
                    ''
                ),
                NULLIF(
                    (
                        SELECT name
                        FROM etown_userprofile
                        WHERE userprofileid = $2
                        LIMIT 1
                    ),
                    ''
                ),
                'Someone'
            ),
            ' has accepted your invitation to ',
            CASE
                WHEN $6::int = 1 THEN 'Provider'
                WHEN $6::int = 2 THEN 'Member'
                ELSE NULL
            END
        ),

        $4,
        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $4
            LIMIT 1
        ),
        $5,
        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $5
            LIMIT 1
        ),

        $6::int,
        CASE
            WHEN $6::int = 1 THEN 'Provider'
            WHEN $6::int = 2 THEN 'Member'
            ELSE NULL
        END,

        $7,
        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $7
            LIMIT 1
        ),
        $2,
        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        $8,
        'Invitation',
        NOW()
    )
    RETURNING notificationsid, notificationcontent;
"""


ACCEPTED_PROVIDER_RELATION_QUERY = """
    INSERT INTO etown_providerrelation (
        providerrelationid,
        fromcontactid,
        fromcontactidname,
        fromproviderid,
        fromprovideridname,
        fromuserprofileid,
        fromuserprofileidname,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        tocontactid,
        tocontactidname,
        touserprofileid,
        touserprofileidname,
        createdon
    )
    SELECT
        gen_random_uuid()::text,

        (
            SELECT contactid
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT contactidname
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT providerid
            FROM etown_provider
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT businessname
            FROM etown_provider
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        $1,

        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT role
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        (
            SELECT rolename
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        (
            SELECT role
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        (
            SELECT rolename
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        $3,

        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $3
            LIMIT 1
        ),

        $2,

        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_providerrelation
        WHERE fromuserprofileid = $1 AND touserprofileid = $2
    )
    RETURNING providerrelationid, fromcontactid, fromproviderid, invite_relationshiptype;
"""


ACCEPTED_PROVIDER_RELATION_INVITECONTACT_QUERY = """
    INSERT INTO etown_providerrelation (
        providerrelationid,
        fromcontactid,
        fromcontactidname,
        fromproviderid,
        fromprovideridname,
        fromuserprofileid,
        fromuserprofileidname,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        tocontactid,
        tocontactidname,
        touserprofileid,
        touserprofileidname,
        createdon
    )
    SELECT
        gen_random_uuid()::text,

        (
            SELECT contactid
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT contactidname
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT providerid
            FROM etown_provider
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT businessname
            FROM etown_provider
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        $1,

        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT role
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT rolename
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT role
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT rolename
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        $3,

        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $3
            LIMIT 1
        ),

        $2,

        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_providerrelation
        WHERE fromuserprofileid = $1 AND touserprofileid = $2
    )
    RETURNING providerrelationid, fromcontactid, fromproviderid, invite_relationshiptype;
"""


ACCEPTED_MEMBER_RELATION_INVITER_QUERY = """
    INSERT INTO etown_memberrelation (
        memberrelationid,
        from_contactid,
        from_firstname,
        from_memberid,
        from_memberidname,
        from_userprofile,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        to_contactid,
        to_firstname,
        to_userprofile,
        createdon
    )
    SELECT
        gen_random_uuid()::text,

        (
            SELECT contactid
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT contactidname
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT memberid
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT name
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        $1,

        '1',

        'Provider',

        1,

        'Provider',

        $2,

        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $2
            LIMIT 1
        ),

        $3,

        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_memberrelation
        WHERE from_userprofile = $1 AND to_userprofile = $3
    )
    RETURNING memberrelationid, from_contactid, from_memberid, invite_relationshiptype;
"""


ACCEPTED_MEMBER_RELATION_INVITECONTACT_QUERY = """
    INSERT INTO etown_memberrelation (
        memberrelationid,
        from_contactid,
        from_firstname,
        from_memberid,
        from_memberidname,
        from_userprofile,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        to_contactid,
        to_firstname,
        to_userprofile,
        createdon
    )
    SELECT
        gen_random_uuid()::text,

        (
            SELECT contactid
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT contactidname
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT memberid
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT name
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        $1,

        '1',

        'Provider',

        1,

        'Provider',

        $2,

        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $2
            LIMIT 1
        ),

        $3,

        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_memberrelation
        WHERE from_userprofile = $1 AND to_userprofile = $3
    )
    RETURNING memberrelationid, from_contactid, from_memberid, invite_relationshiptype;
"""


ACCEPTED_MEMBER_RELATION_NON_PROVIDER_QUERY = """
    INSERT INTO etown_memberrelation (
        memberrelationid,
        from_contactid,
        from_firstname,
        from_memberid,
        from_memberidname,
        from_userprofile,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        to_contactid,
        to_firstname,
        to_userprofile,
        createdon
    )
    SELECT
        gen_random_uuid()::text,

        (
            SELECT contactid
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT contactidname
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT memberid
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT name
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        $1,

        (
            SELECT role
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT rolename
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT role
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT rolename
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        $2,

        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $2
            LIMIT 1
        ),

        $3,

        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_memberrelation
        WHERE from_userprofile = $1 AND to_userprofile = $3
    )
    RETURNING memberrelationid, from_contactid, from_memberid, invite_relationshiptype;
"""


ACCEPTED_MEMBER_RELATION_NON_PROVIDER_INVITER_QUERY = """
    INSERT INTO etown_memberrelation (
        memberrelationid,
        from_contactid,
        from_firstname,
        from_memberid,
        from_memberidname,
        from_userprofile,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        to_contactid,
        to_firstname,
        to_userprofile,
        createdon
    )
    SELECT
        gen_random_uuid()::text,

        (
            SELECT contactid
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT contactidname
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT memberid
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT name
            FROM etown_member
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        $1,

        2,

        'Member',

        2,

        'Member',

        $2,

        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $2
            LIMIT 1
        ),

        $3,

        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_memberrelation
        WHERE from_userprofile = $1 AND to_userprofile = $3
    )
    RETURNING memberrelationid, from_contactid, from_memberid, invite_relationshiptype;
"""


ACCEPTED_PROVIDER_RELATION_NON_PROVIDER_INVITECONTACT_QUERY = """
    INSERT INTO etown_providerrelation (
        providerrelationid,
        fromcontactid,
        fromcontactidname,
        fromproviderid,
        fromprovideridname,
        fromuserprofileid,
        fromuserprofileidname,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        tocontactid,
        tocontactidname,
        touserprofileid,
        touserprofileidname,
        createdon
    )
    SELECT
        gen_random_uuid()::text,

        (
            SELECT contactid
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT contactidname
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT providerid
            FROM etown_provider
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT businessname
            FROM etown_provider
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        $1,

        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        2,

        'Member',

        2,

        'Member',

        $3,

        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $3
            LIMIT 1
        ),

        $2,

        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $2
            LIMIT 1
        ),

        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_providerrelation
        WHERE fromuserprofileid = $1 AND touserprofileid = $2
    )
    RETURNING providerrelationid, fromcontactid, fromproviderid, invite_relationshiptype;
"""


ACCEPTED_PROVIDER_RELATION_NON_PROVIDER_INVITER_QUERY = """
    INSERT INTO etown_providerrelation (
        providerrelationid,
        fromcontactid,
        fromcontactidname,
        fromproviderid,
        fromprovideridname,
        fromuserprofileid,
        fromuserprofileidname,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        tocontactid,
        tocontactidname,
        touserprofileid,
        touserprofileidname,
        createdon
    )
    SELECT
        gen_random_uuid()::text,

        (
            SELECT contactid
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT contactidname
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT providerid
            FROM etown_provider
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        (
            SELECT businessname
            FROM etown_provider
            WHERE contactid = (
                SELECT contactid
                FROM etown_userprofile
                WHERE userprofileid = $1
                LIMIT 1
            )
            AND userprofileid = $1
            LIMIT 1
        ),

        $1,

        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $1
            LIMIT 1
        ),

        2,

        'Member',

        2,

        'Member',

        $2,

        (
            SELECT firstname
            FROM etown_contact
            WHERE contactid = $2
            LIMIT 1
        ),

        $3,

        (
            SELECT name
            FROM etown_userprofile
            WHERE userprofileid = $3
            LIMIT 1
        ),

        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_providerrelation
        WHERE fromuserprofileid = $1 AND touserprofileid = $3
    )
    RETURNING providerrelationid, fromcontactid, fromproviderid, invite_relationshiptype;
"""


async def _insert_accepted_relations(
    invitecontact_role: int | None,
    invite_contact_userprofile_id: str | None,
    inviter: str | None,
    inviter_userprofile_id: str | None,
    invite_role: int | None,
    invite_contact: str | None,
) -> int | None:
    """Insert the role-dependent member and provider relation rows for an accepted invite and mirror each to Dataverse.

    Args:
        invitecontact_role: Invitee role, 1 for provider, 2 for member; looked up when None.
        invite_contact_userprofile_id: Userprofile id of the invitee.
        inviter: Contact id of the inviter.
        inviter_userprofile_id: Userprofile id of the inviter.
        invite_role: Numeric role stored on the invitation.
        invite_contact: Contact id of the invitee.

    Returns:
        The inviter's role from etown_userprofile, or None.
    """
    role_rows = await Database.fetch(
        "SELECT role FROM etown_userprofile WHERE userprofileid = $1 LIMIT 1",
        inviter_userprofile_id,
    )
    inviter_role_val = role_rows[0]["role"] if role_rows and role_rows[0]["role"] is not None else None

    if invitecontact_role is None and invite_contact_userprofile_id:
        ic_role_rows = await Database.fetch(
            "SELECT role FROM etown_userprofile WHERE userprofileid = $1 LIMIT 1",
            invite_contact_userprofile_id,
        )
        invitecontact_role = (
            ic_role_rows[0]["role"] if ic_role_rows and ic_role_rows[0]["role"] is not None else None
        )

    if invite_role == 1 and inviter_role_val == 1:
        relation_rows = await Database.fetch(
            ACCEPTED_PROVIDER_RELATION_QUERY,
            inviter_userprofile_id,
            invite_contact_userprofile_id,
            invite_contact,
        )
        if relation_rows:
            asyncio.create_task(
                sync_accepted_providerrelation_to_dataverse(
                    providerrelation_id=relation_rows[0]["providerrelationid"],
                    from_contactid=relation_rows[0]["fromcontactid"],
                    from_providerid=relation_rows[0]["fromproviderid"],
                    from_userprofileid=inviter_userprofile_id,
                    to_contactid=invite_contact,
                    to_userprofileid=invite_contact_userprofile_id,
                    invite_relationshiptype=relation_rows[0]["invite_relationshiptype"],
                )
            )
        logger.info(
            f"Background: provider relation inserted "
            f"| providerrelationid={relation_rows[0]['providerrelationid'] if relation_rows else None}"
        )

    if invite_role == 1 and invitecontact_role == 1:
        inv_contact_relation_rows = await Database.fetch(
            ACCEPTED_PROVIDER_RELATION_INVITECONTACT_QUERY,
            invite_contact_userprofile_id,
            inviter_userprofile_id,
            inviter,
        )
        if inv_contact_relation_rows:
            asyncio.create_task(
                sync_accepted_providerrelation_to_dataverse(
                    providerrelation_id=inv_contact_relation_rows[0]["providerrelationid"],
                    from_contactid=inv_contact_relation_rows[0]["fromcontactid"],
                    from_providerid=inv_contact_relation_rows[0]["fromproviderid"],
                    from_userprofileid=invite_contact_userprofile_id,
                    to_contactid=inviter,
                    to_userprofileid=inviter_userprofile_id,
                    invite_relationshiptype=inv_contact_relation_rows[0]["invite_relationshiptype"],
                )
            )
        logger.info(
            f"Background: reverse provider relation (invite contact) inserted "
            f"| providerrelationid={inv_contact_relation_rows[0]['providerrelationid'] if inv_contact_relation_rows else None}"
        )

    if invite_role == 1 and inviter_role_val == 2:
        member_relation_rows = await Database.fetch(
            ACCEPTED_MEMBER_RELATION_INVITER_QUERY,
            inviter_userprofile_id,
            invite_contact,
            invite_contact_userprofile_id,
        )
        if member_relation_rows:
            asyncio.create_task(
                sync_accepted_memberrelation_to_dataverse(
                    memberrelation_id=member_relation_rows[0]["memberrelationid"],
                    from_contactid=member_relation_rows[0]["from_contactid"],
                    from_memberid=member_relation_rows[0]["from_memberid"],
                    from_userprofileid=inviter_userprofile_id,
                    to_contactid=invite_contact,
                    to_userprofileid=invite_contact_userprofile_id,
                    invite_relationshiptype=member_relation_rows[0]["invite_relationshiptype"],
                )
            )
        logger.info(
            f"Background: member relation (inviter) inserted "
            f"| memberrelationid={member_relation_rows[0]['memberrelationid'] if member_relation_rows else None}"
        )

    if invite_role == 1 and invitecontact_role == 2:
        inv_contact_member_rows = await Database.fetch(
            ACCEPTED_MEMBER_RELATION_INVITECONTACT_QUERY,
            invite_contact_userprofile_id,
            inviter,
            inviter_userprofile_id,
        )
        if inv_contact_member_rows:
            asyncio.create_task(
                sync_accepted_memberrelation_to_dataverse(
                    memberrelation_id=inv_contact_member_rows[0]["memberrelationid"],
                    from_contactid=inv_contact_member_rows[0]["from_contactid"],
                    from_memberid=inv_contact_member_rows[0]["from_memberid"],
                    from_userprofileid=invite_contact_userprofile_id,
                    to_contactid=inviter,
                    to_userprofileid=inviter_userprofile_id,
                    invite_relationshiptype=inv_contact_member_rows[0]["invite_relationshiptype"],
                )
            )
        logger.info(
            f"Background: member relation (invite contact) inserted "
            f"| memberrelationid={inv_contact_member_rows[0]['memberrelationid'] if inv_contact_member_rows else None}"
        )

    if invite_role != 1 and invitecontact_role == 2:
        non_provider_member_rows = await Database.fetch(
            ACCEPTED_MEMBER_RELATION_NON_PROVIDER_QUERY,
            invite_contact_userprofile_id,
            inviter,
            inviter_userprofile_id,
        )
        if non_provider_member_rows:
            asyncio.create_task(
                sync_accepted_memberrelation_to_dataverse(
                    memberrelation_id=non_provider_member_rows[0]["memberrelationid"],
                    from_contactid=non_provider_member_rows[0]["from_contactid"],
                    from_memberid=non_provider_member_rows[0]["from_memberid"],
                    from_userprofileid=invite_contact_userprofile_id,
                    to_contactid=inviter,
                    to_userprofileid=inviter_userprofile_id,
                    invite_relationshiptype=non_provider_member_rows[0]["invite_relationshiptype"],
                )
            )
        logger.info(
            f"Background: member relation (non-provider invite contact) inserted "
            f"| memberrelationid={non_provider_member_rows[0]['memberrelationid'] if non_provider_member_rows else None}"
        )

    if invite_role != 1 and inviter_role_val == 2:
        non_provider_inviter_rows = await Database.fetch(
            ACCEPTED_MEMBER_RELATION_NON_PROVIDER_INVITER_QUERY,
            inviter_userprofile_id,
            invite_contact,
            invite_contact_userprofile_id,
        )
        if non_provider_inviter_rows:
            asyncio.create_task(
                sync_accepted_memberrelation_to_dataverse(
                    memberrelation_id=non_provider_inviter_rows[0]["memberrelationid"],
                    from_contactid=non_provider_inviter_rows[0]["from_contactid"],
                    from_memberid=non_provider_inviter_rows[0]["from_memberid"],
                    from_userprofileid=inviter_userprofile_id,
                    to_contactid=invite_contact,
                    to_userprofileid=invite_contact_userprofile_id,
                    invite_relationshiptype=non_provider_inviter_rows[0]["invite_relationshiptype"],
                )
            )
        logger.info(
            f"Background: member relation (non-provider inviter) inserted "
            f"| memberrelationid={non_provider_inviter_rows[0]['memberrelationid'] if non_provider_inviter_rows else None}"
        )

    if invite_role != 1 and invitecontact_role == 1:
        non_provider_invite_contact_rows = await Database.fetch(
            ACCEPTED_PROVIDER_RELATION_NON_PROVIDER_INVITECONTACT_QUERY,
            invite_contact_userprofile_id,
            inviter_userprofile_id,
            inviter,
        )
        if non_provider_invite_contact_rows:
            asyncio.create_task(
                sync_accepted_providerrelation_to_dataverse(
                    providerrelation_id=non_provider_invite_contact_rows[0]["providerrelationid"],
                    from_contactid=non_provider_invite_contact_rows[0]["fromcontactid"],
                    from_providerid=non_provider_invite_contact_rows[0]["fromproviderid"],
                    from_userprofileid=invite_contact_userprofile_id,
                    to_contactid=inviter,
                    to_userprofileid=inviter_userprofile_id,
                    invite_relationshiptype=non_provider_invite_contact_rows[0]["invite_relationshiptype"],
                )
            )
        logger.info(
            f"Background: provider relation (non-provider invite contact) inserted "
            f"| providerrelationid={non_provider_invite_contact_rows[0]['providerrelationid'] if non_provider_invite_contact_rows else None}"
        )

    if invite_role != 1 and inviter_role_val == 1:
        non_provider_inviter_provider_rows = await Database.fetch(
            ACCEPTED_PROVIDER_RELATION_NON_PROVIDER_INVITER_QUERY,
            inviter_userprofile_id,
            invite_contact,
            invite_contact_userprofile_id,
        )
        if non_provider_inviter_provider_rows:
            asyncio.create_task(
                sync_accepted_providerrelation_to_dataverse(
                    providerrelation_id=non_provider_inviter_provider_rows[0]["providerrelationid"],
                    from_contactid=non_provider_inviter_provider_rows[0]["fromcontactid"],
                    from_providerid=non_provider_inviter_provider_rows[0]["fromproviderid"],
                    from_userprofileid=inviter_userprofile_id,
                    to_contactid=invite_contact,
                    to_userprofileid=invite_contact_userprofile_id,
                    invite_relationshiptype=non_provider_inviter_provider_rows[0]["invite_relationshiptype"],
                )
            )
        logger.info(
            f"Background: provider relation (non-provider inviter) inserted "
            f"| providerrelationid={non_provider_inviter_provider_rows[0]['providerrelationid'] if non_provider_inviter_provider_rows else None}"
        )

    return inviter_role_val


async def link_invitation_on_registration(
    invite_code: str | None,
    mobilenumber: str | None,
    invite_contact: str | None,
    invite_contact_userprofile_id: str | None,
    invitecontact_role: int | None,
) -> None:
    """Link a newly registered user's originating invitation, insert the relation rows, mark it Accepted and mirror it.

    Args:
        invite_code: Invitation suffix from etown_contact.invitationcode; matched on suffixid.
        mobilenumber: Invitee mobile, matched against Requested invitations when there is no code.
        invite_contact: Contact id of the newly registered invitee.
        invite_contact_userprofile_id: Userprofile id of the newly registered invitee.
        invitecontact_role: Invitee role, 1 for provider, 2 for member.
    """
    try:
        if not invite_contact or not invite_contact_userprofile_id:
            return
        if not invite_code and not mobilenumber:
            return

        if invite_code:
            inv_rows = await Database.fetch(
                """
                SELECT invitationid, invitercontact, inviteruserprofile, invite_role
                FROM etown_invitation
                WHERE suffixid = $1
                  AND invitationtype IN ('External', 'ExternalApp_share')
                ORDER BY createdon DESC
                LIMIT 1
                """,
                invite_code,
            )
        else:
            inv_rows = await Database.fetch(
                """
                SELECT invitationid, invitercontact, inviteruserprofile, invite_role
                FROM etown_invitation
                WHERE invitecontactmobilenumber = $1
                  AND invitationcode = 'Requested'
                  AND (invitecontact IS NULL OR invitecontact = $2)
                ORDER BY createdon DESC
                LIMIT 1
                """,
                mobilenumber, invite_contact,
            )
        if not inv_rows:
            logger.info(
                "[signup_invitation] No pending invitation for suffix=%s mobile=%s — nothing to link",
                invite_code, mobilenumber,
            )
            return
        invitation_id = inv_rows[0]["invitationid"]

        inviter = inv_rows[0]["invitercontact"]
        inviter_userprofile_id = inv_rows[0]["inviteruserprofile"]
        invite_role = inv_rows[0]["invite_role"]

        if not inviter or not inviter_userprofile_id:
            logger.info(
                "[signup_invitation] Invitation %s has no inviter — skipping relation insert",
                invitation_id,
            )
            return

        await _insert_accepted_relations(
            invitecontact_role=invitecontact_role,
            invite_contact_userprofile_id=invite_contact_userprofile_id,
            inviter=inviter,
            inviter_userprofile_id=inviter_userprofile_id,
            invite_role=invite_role,
            invite_contact=invite_contact,
        )
        logger.info(
            "[signup_invitation] Relations reconciled | invitation=%s | inviter=%s | invitee=%s",
            invitation_id, inviter, invite_contact,
        )

        updated_inv = await Database.fetch(
            """
            UPDATE etown_invitation
            SET invitecontact         = $2,
                invitecontactname     = (SELECT firstname FROM etown_contact WHERE contactid = $2 LIMIT 1),
                inviteuserprofile     = $3,
                inviteuserprofilename = (SELECT name FROM etown_userprofile WHERE userprofileid = $3 LIMIT 1),
                invitationcode        = 'Accepted',
                isopponentadded       = 'true',
                modifieddate          = NOW()
            WHERE invitationid = $1
            RETURNING *
            """,
            invitation_id,
            invite_contact,
            invite_contact_userprofile_id,
        )
        if updated_inv:
            asyncio.create_task(sync_invitation_to_dataverse(dict(updated_inv[0])))
            logger.info(
                "[signup_invitation] Invitation finalized and synced | invitationid=%s",
                updated_inv[0]["invitationid"],
            )
    except Exception as e:
        logger.error(
            "[signup_invitation] Failed to link invitation for suffix=%s mobile=%s: %s",
            invite_code, mobilenumber, e, exc_info=True,
        )


async def _background_insert_accepted_notification(
    invitecontact_role: int | None,
    invite_contact_userprofile_id: str | None,
    invite_contact_firstname: str | None,
    inviter: str | None,
    inviter_userprofile_id: str | None,
    invite_role: int | None,
    invite_contact: str | None,
    invitation_code: str | None,
    oauth: str | None = None,
) -> None:
    """Insert the accepted-invite relations and notification, mirror the notification, and push it to the inviter."""
    inviter_role_val: int | None = None
    try:
        inviter_role_val = await _insert_accepted_relations(
            invitecontact_role=invitecontact_role,
            invite_contact_userprofile_id=invite_contact_userprofile_id,
            inviter=inviter,
            inviter_userprofile_id=inviter_userprofile_id,
            invite_role=invite_role,
            invite_contact=invite_contact,
        )
    except Exception as e:
        logger.error(
            f"Background: failed to insert accepted relations: {str(e)}", exc_info=True
        )
    inviter_role = str(inviter_role_val) if inviter_role_val is not None else ""

    try:
        rows = await Database.fetch(
            ACCEPTED_NOTIFICATION_QUERY,
            invitecontact_role,
            invite_contact_userprofile_id,
            invite_contact_firstname,
            inviter,
            inviter_userprofile_id,
            invite_role,
            invite_contact,
            invitation_code,
        )
        notification_content = rows[0]["notificationcontent"] if rows else None
        notifications_id = rows[0]["notificationsid"] if rows else None
        logger.info(
            f"Background: accepted notification inserted "
            f"| content={notification_content}"
        )
        if notifications_id:
            asyncio.create_task(
                sync_accepted_notification_to_dataverse(
                    notifications_id=notifications_id,
                    notification_content=notification_content,
                    role=invite_role,
                    notification_status=invitation_code,
                    notification_type="Invitation",
                    receiver_contact_id=inviter,
                    receiver_userprofile_id=inviter_userprofile_id,
                    sender_contact_id=invite_contact,
                    sender_userprofile_id=invite_contact_userprofile_id,
                )
            )

        if not oauth or not notification_content:
            logger.info(
                "Background: skipping FCM push — oauth or notificationcontent missing"
            )
            return

        await _send_invite_push_to_all_devices(
            inviter_userprofile_id,
            oauth,
            notification_content,
            {
                "type": "Invite",
                "userprofile": inviter_userprofile_id or "",
                "role": inviter_role,
                "route": "/mycircle",
                "invite_role": str(invite_role) if invite_role is not None else "",
            },
        )

    except Exception as e:
        logger.error(
            f"Background: failed to insert accepted notification: {str(e)}",
            exc_info=True,
        )


_MYCIRCLE_ACTOR_LOOKUP_QUERY = """
    SELECT
        (SELECT role        FROM etown_userprofile WHERE userprofileid = $2 LIMIT 1) AS actor_role,
        (SELECT name        FROM etown_userprofile WHERE userprofileid = $2 LIMIT 1) AS actor_profilename,
        (SELECT firstname   FROM etown_contact     WHERE contactid     = $1 LIMIT 1) AS actor_firstname,
        (SELECT businessname FROM etown_provider   WHERE userprofileid = $2 LIMIT 1) AS actor_businessname
"""

_MYCIRCLE_ADDED_PROVIDER_LOOKUP_QUERY = """
    SELECT
        (SELECT firstname    FROM etown_contact     WHERE contactid     = $1 LIMIT 1) AS prov_firstname,
        (SELECT name         FROM etown_userprofile WHERE userprofileid = $2 LIMIT 1) AS prov_profilename,
        (SELECT providertype FROM etown_provider    WHERE providerid    = $3 LIMIT 1) AS prov_type,
        (SELECT mobilenumber FROM etown_provider    WHERE providerid    = $3 LIMIT 1) AS prov_mobile,
        (SELECT businessname FROM etown_provider    WHERE providerid    = $3 LIMIT 1) AS prov_businessname
"""

_MYCIRCLE_PROVIDER_IDENTITY_QUERY = """
    SELECT
        p.contactid,
        p.userprofileid,
        EXISTS (SELECT 1 FROM etown_contact     c WHERE c.contactid     = p.contactid)     AS contact_exists,
        EXISTS (SELECT 1 FROM etown_userprofile u WHERE u.userprofileid = p.userprofileid) AS userprofile_exists
    FROM etown_provider p
    WHERE p.providerid = $1
"""

_MYCIRCLE_ADDED_MEMBER_LOOKUP_QUERY = """
    SELECT
        (SELECT firstname FROM etown_contact     WHERE contactid     = $1 LIMIT 1) AS mem_firstname,
        (SELECT verified  FROM etown_contact     WHERE contactid     = $1 LIMIT 1) AS mem_verified,
        (SELECT name      FROM etown_userprofile WHERE userprofileid = $2 LIMIT 1) AS mem_profilename
"""

_MYCIRCLE_MEMBER_IDENTITY_QUERY = """
    SELECT
        m.contactid,
        m.userprofileid,
        EXISTS (SELECT 1 FROM etown_contact     c WHERE c.contactid     = m.contactid)     AS contact_exists,
        EXISTS (SELECT 1 FROM etown_userprofile u WHERE u.userprofileid = m.userprofileid) AS userprofile_exists
    FROM etown_member m
    WHERE m.memberid = $1
"""

_MYCIRCLE_INVITATION_INSERT = """
    INSERT INTO etown_invitation (
        invitationid, type, invitationtype,
        invitercontact, invitercontactname, inviteruserprofile, inviteruserprofilename,
        invitecontact, invitecontactname, inviteuserprofile, inviteuserprofilename,
        invite_role, invite_rolename, new_inviterole,
        invitationcode, status, statuscode, isnotification, isopponentadded, createdon
    )
    SELECT
        gen_random_uuid()::text, $1, 'Added to MyCircle',
        $2, (SELECT firstname FROM etown_contact WHERE contactid = $2 LIMIT 1),
        $3, (SELECT name FROM etown_userprofile WHERE userprofileid = $3 LIMIT 1),
        $4, (SELECT firstname FROM etown_contact WHERE contactid = $4 LIMIT 1),
        $5, (SELECT name FROM etown_userprofile WHERE userprofileid = $5 LIMIT 1),
        $6::int, $7::text, $8::text,
        'Requested', 'Requested', '0', 1, 'false', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_invitation
        WHERE invitercontact = $2 AND invitecontact = $4 AND invitationtype = 'Added to MyCircle'
          AND invitationcode IN ('Requested', 'Accepted')
    )
    RETURNING *;
"""

_MYCIRCLE_NOTIFICATION_INSERT = """
    INSERT INTO etown_notifications (
        notificationsid, notificationcontent,
        receivercontactid, receivercontactidname, receiveruserprofileid, receiveruserprofileidname,
        sendercontactid, sendercontactidname, senderuserprofileid, senderuserprofileidname,
        notificationtype, createdon
    )
    VALUES (
        gen_random_uuid()::text, $1,
        $2, $3, $4, $5,
        $6, $7, $8, $9,
        'Added to MyCircle', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'
    )
    RETURNING notificationsid;
"""


_MYCIRCLE_MEMBER_RELATION_DELETE = """
    DELETE FROM etown_memberrelation
    WHERE from_userprofile = $1 AND to_userprofile = $2
    RETURNING memberrelationid;
"""

_MYCIRCLE_PROVIDER_RELATION_DELETE = """
    DELETE FROM etown_providerrelation
    WHERE fromuserprofileid = $1 AND touserprofileid = $2
    RETURNING providerrelationid;
"""

_MYCIRCLE_INVITATION_DELETE = """
    DELETE FROM etown_invitation
    WHERE invitercontact = $1
      AND invitecontact = $2
      AND (
            invitationtype = 'Added to MyCircle'
         OR (invitationtype = 'Internal'
             AND LOWER(invitationcode) IN ('requested', 'pending'))
      )
    RETURNING invitationid;
"""


async def _resolve_mycircle_provider_identity(
    providerid: str,
    provider_contactid: str | None,
    provider_userprofileid: str | None,
) -> tuple[str, str]:
    """Return the provider's (contactid, userprofileid), filling any missing id from the etown_provider row.

    Args:
        providerid: Provider record id used for the fallback lookup.
        provider_contactid: Caller-supplied contact id, or None to resolve it.
        provider_userprofileid: Caller-supplied userprofile id, or None to resolve it.

    Returns:
        The resolved (provider_contactid, provider_userprofileid) pair.

    Raises:
        ValueError: If no provider matches or the missing id is NULL or dangling.
    """
    if provider_contactid and provider_userprofileid:
        return provider_contactid, provider_userprofileid

    row = await Database.fetchrow(_MYCIRCLE_PROVIDER_IDENTITY_QUERY, providerid)
    if row is None:
        raise ValueError(f"No provider found for providerid '{providerid}'")

    if not provider_contactid and not (row["contactid"] and row["contact_exists"]):
        raise ValueError(
            f"Provider '{providerid}' has no linked contact; provider_contactid must be supplied"
        )
    if not provider_userprofileid and not (row["userprofileid"] and row["userprofile_exists"]):
        raise ValueError(
            f"Provider '{providerid}' has no linked userprofile; provider_userprofileid must be supplied"
        )

    resolved_contactid = provider_contactid or row["contactid"]
    resolved_userprofileid = provider_userprofileid or row["userprofileid"]
    logger.info(
        "[mycircle.resolve_provider] providerid=%s | contact=%s userprofile=%s (resolved: contact=%s profile=%s)",
        providerid, resolved_contactid, resolved_userprofileid,
        not provider_contactid, not provider_userprofileid,
    )
    return resolved_contactid, resolved_userprofileid


async def add_provider_to_mycircle(
    from_contactid: str,
    from_userprofileid: str,
    provider_contactid: str | None,
    provider_userprofileid: str | None,
    providerid: str,
    role: str | None = None,
    language_code: str | None = None,
    oauth: str | None = None,
) -> dict:
    """Write a Requested MyCircle invitation and in-app notification to a provider in one transaction.

    Args:
        from_contactid: Contact id of the actor requesting the add.
        from_userprofileid: Userprofile id of the actor.
        provider_contactid: Invited provider's contact id; resolved from providerid when omitted.
        provider_userprofileid: Invited provider's userprofile id; resolved from providerid when omitted.
        providerid: Provider record id of the invited provider.
        role: Actor's numeric role code, stored as the invitation type.
        language_code: Language for the unverified-provider SMS, EN or TA.
        oauth: Client FCM OAuth bearer token, passed through to delivery.

    Returns:
        The invitation row, notification id and content, and provider delivery details.

    Raises:
        ValueError: If the provider identity cannot be resolved; nothing is written.
        RuntimeError: If the database pool is not initialised.
    """
    provider_contactid, provider_userprofileid = await _resolve_mycircle_provider_identity(
        providerid, provider_contactid, provider_userprofileid,
    )

    actor_row, added_row = await asyncio.gather(
        Database.fetchrow(_MYCIRCLE_ACTOR_LOOKUP_QUERY, from_contactid, from_userprofileid),
        Database.fetchrow(
            _MYCIRCLE_ADDED_PROVIDER_LOOKUP_QUERY,
            provider_contactid, provider_userprofileid, providerid,
        ),
    )
    actor = dict(actor_row) if actor_row else {}
    added = dict(added_row) if added_row else {}

    actor_role = actor.get("actor_role")
    actor_firstname = actor.get("actor_firstname")
    actor_profilename = actor.get("actor_profilename")
    actor_businessname = actor.get("actor_businessname")

    notification_content = None
    if actor_role == 2:
        notification_content = f"{actor_firstname or ''} has requested to add you to their My Circle as Customer"
    elif actor_role == 1:
        notification_content = f"{actor_businessname or ''} has requested to add you to their My Circle as Provider"

    type_val = int(role) if role is not None and str(role).strip().lstrip("-").isdigit() else None

    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool not initialized")

    notification_id: str | None = None

    async with Database._pool.acquire() as con:
        async with con.transaction():
            invitation_rec = await con.fetchrow(
                _MYCIRCLE_INVITATION_INSERT,
                type_val, from_contactid, from_userprofileid,
                provider_contactid, provider_userprofileid,
                1, "Provider", "1",
            )
            invitation_row = dict(invitation_rec) if invitation_rec else None

            if invitation_row is not None and notification_content is not None:
                notification_id = await con.fetchval(
                    _MYCIRCLE_NOTIFICATION_INSERT,
                    notification_content,
                    provider_contactid, added.get("prov_firstname"),
                    provider_userprofileid, added.get("prov_profilename"),
                    from_contactid, actor_firstname,
                    from_userprofileid, actor_profilename,
                )

    logger.info(
        "[mycircle.add_provider] actor=%s (role=%s) provider=%s | requested=%s notif=%s",
        from_userprofileid, actor_role, provider_userprofileid,
        bool(invitation_row), notification_id,
    )

    provider_type = added.get("prov_type")
    premium_label = (
        (await resolve_provider_badges([provider_userprofileid])).get(provider_userprofileid) or {}
    ).get("premium_label")
    return {
        "created": invitation_row is not None,
        "invitation_row": invitation_row,
        "actor": {
            "contactid": from_contactid,
            "userprofileid": from_userprofileid,
            "role": actor_role,
        },
        "notification": {"id": notification_id, "content": notification_content if notification_id else None},
        "added_provider": {
            "contactid": provider_contactid,
            "userprofileid": provider_userprofileid,
            "providerid": providerid,
            "verified": (provider_type or "").strip().lower() == "etown_verified",
            "providertype": premium_display(provider_type, premium_label),
            "mobilenumber": added.get("prov_mobile"),
            "businessname": added.get("prov_businessname"),
        },
        "language_code": language_code,
        "oauth": oauth,
    }


async def mycircle_add_provider_deliver(result: dict) -> None:
    """Mirror a MyCircle provider request to Dataverse and dispatch a push if verified, else an SMS."""
    try:
        from app.etown.notifications.core import dispatch

        actor = result.get("actor", {})
        added = result.get("added_provider", {})
        notif = result.get("notification", {})

        tasks = []
        if result.get("invitation_row"):
            tasks.append(sync_invitation_to_dataverse(result["invitation_row"]))

        if notif.get("id"):
            tasks.append(sync_accepted_notification_to_dataverse(
                notifications_id=notif["id"],
                notification_content=notif.get("content"),
                role=1,
                notification_status=None,
                notification_type="Added to MyCircle",
                receiver_contact_id=added.get("contactid"),
                receiver_userprofile_id=added.get("userprofileid"),
                sender_contact_id=actor.get("contactid"),
                sender_userprofile_id=actor.get("userprofileid"),
            ))

        if notif.get("id"):
            if added.get("verified"):
                tasks.append(dispatch(NotificationRequest(
                    notification_type=NotificationType.ADDED_TO_MYCIRCLE,
                    recipient=Recipient(
                        userprofile_id=added.get("userprofileid"),
                        contact_id=added.get("contactid"),
                    ),
                    context={"body": notif.get("content") or ""},
                    push_audience=PushAudience.INDIVIDUAL,
                    oauth_token=result.get("oauth"),
                )))
            elif added.get("mobilenumber"):
                tasks.append(dispatch(NotificationRequest(
                    notification_type=NotificationType.ADDED_TO_MYCIRCLE_SMS,
                    recipient=Recipient(mobile_number=added.get("mobilenumber")),
                    context={
                        "language": result.get("language_code") or "EN",
                        "business": added.get("businessname") or "",
                    },
                )))

        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(
            "[mycircle.add_provider] delivered | provider=%s verified=%s",
            added.get("userprofileid"), added.get("verified"),
        )
    except Exception as exc:
        logger.error("[mycircle.add_provider] delivery failed: %s", exc, exc_info=True)


async def _mycircle_remove_circle_rows(
    from_contactid: str,
    from_userprofileid: str,
    to_contactid: str,
    to_userprofileid: str,
) -> dict:
    """Delete both directions of a MyCircle pair's relations and its MyCircle or live Internal invitations in one transaction.

    Args:
        from_contactid: Contact id of the actor doing the removing.
        from_userprofileid: Userprofile id of the actor.
        to_contactid: Contact id of the counterparty being removed.
        to_userprofileid: Userprofile id of the counterparty being removed.

    Returns:
        The deleted relation ids, invitation_ids, and removed, True when anything was deleted.

    Raises:
        RuntimeError: If the database pool is not initialised.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool not initialized")

    async with Database._pool.acquire() as con:
        async with con.transaction():
            forward_member_id = await con.fetchval(
                _MYCIRCLE_MEMBER_RELATION_DELETE,
                from_userprofileid, to_userprofileid,
            )
            forward_provider_id = await con.fetchval(
                _MYCIRCLE_PROVIDER_RELATION_DELETE,
                from_userprofileid, to_userprofileid,
            )

            reciprocal_member_id = await con.fetchval(
                _MYCIRCLE_MEMBER_RELATION_DELETE,
                to_userprofileid, from_userprofileid,
            )
            reciprocal_provider_id = await con.fetchval(
                _MYCIRCLE_PROVIDER_RELATION_DELETE,
                to_userprofileid, from_userprofileid,
            )

            invitation_rows = await con.fetch(
                _MYCIRCLE_INVITATION_DELETE,
                from_contactid, to_contactid,
            )
            invitation_ids = [str(r["invitationid"]) for r in invitation_rows]

    return {
        "removed": any((
            forward_member_id, forward_provider_id,
            reciprocal_member_id, reciprocal_provider_id, invitation_ids,
        )),
        "forward_member_relation_id": forward_member_id,
        "forward_provider_relation_id": forward_provider_id,
        "reciprocal_member_relation_id": reciprocal_member_id,
        "reciprocal_provider_relation_id": reciprocal_provider_id,
        "invitation_ids": invitation_ids,
    }


async def remove_provider_from_mycircle(
    from_contactid: str,
    from_userprofileid: str,
    provider_contactid: str | None,
    provider_userprofileid: str | None,
    providerid: str,
    role: str | None = None,
    language_code: str | None = None,
    oauth: str | None = None,
) -> dict:
    """Remove a provider from the actor's MyCircle by deleting the pair's relation and invitation rows.

    Args:
        from_contactid: Contact id of the actor removing the provider.
        from_userprofileid: Userprofile id of the actor.
        provider_contactid: Provider's contact id; resolved from providerid when omitted.
        provider_userprofileid: Provider's userprofile id; resolved from providerid when omitted.
        providerid: Provider record id, used only to resolve an omitted id.
        role: Unused; accepted for signature parity.
        language_code: Unused; accepted for signature parity.
        oauth: Unused; accepted for signature parity.

    Returns:
        The deleted record ids for the Dataverse delete mirror.

    Raises:
        ValueError: If the provider identity cannot be resolved; nothing is deleted.
    """
    provider_contactid, provider_userprofileid = await _resolve_mycircle_provider_identity(
        providerid, provider_contactid, provider_userprofileid,
    )

    result = await _mycircle_remove_circle_rows(
        from_contactid, from_userprofileid, provider_contactid, provider_userprofileid,
    )
    logger.info(
        "[mycircle.remove_provider] actor=%s provider=%s | removed=%s "
        "forward_member=%s forward_provider=%s recip_member=%s recip_provider=%s invitations=%s",
        from_userprofileid, provider_userprofileid, result["removed"],
        result["forward_member_relation_id"], result["forward_provider_relation_id"],
        result["reciprocal_member_relation_id"], result["reciprocal_provider_relation_id"],
        result["invitation_ids"],
    )
    return result


async def mycircle_remove_deliver(result: dict) -> None:
    """Delete from Dataverse the relation and invitation records a MyCircle removal deleted from Postgres."""
    try:
        tasks = [
            delete_record_from_dataverse(entity, result[key])
            for key, entity in (
                ("forward_member_relation_id", "ets_memberrelations"),
                ("forward_provider_relation_id", "ets_providerrelations"),
                ("reciprocal_member_relation_id", "ets_memberrelations"),
                ("reciprocal_provider_relation_id", "ets_providerrelations"),
            )
            if result.get(key)
        ]
        tasks += [
            delete_record_from_dataverse("adx_invitations", invitation_id)
            for invitation_id in result.get("invitation_ids") or []
        ]

        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[mycircle.remove] delivered | removed=%s", result.get("removed"))
    except Exception as exc:
        logger.error("[mycircle.remove] delivery failed: %s", exc, exc_info=True)


async def _resolve_mycircle_member_identity(
    memberid: str | None,
    member_contactid: str | None,
    member_userprofileid: str | None,
) -> tuple[str, str]:
    """Return the member's (contactid, userprofileid), filling any missing id from the etown_member row.

    Args:
        memberid: Member record id for the fallback lookup, or None when both ids are given.
        member_contactid: Caller-supplied contact id, or None to resolve it.
        member_userprofileid: Caller-supplied userprofile id, or None to resolve it.

    Returns:
        The resolved (member_contactid, member_userprofileid) pair.

    Raises:
        ValueError: If an id is missing without memberid, no member matches, or the id is NULL or dangling.
    """
    if member_contactid and member_userprofileid:
        return member_contactid, member_userprofileid

    if not memberid:
        raise ValueError(
            "member_contactid and member_userprofileid are required, "
            "or supply memberid to resolve the missing one"
        )

    row = await Database.fetchrow(_MYCIRCLE_MEMBER_IDENTITY_QUERY, memberid)
    if row is None:
        raise ValueError(f"No member found for memberid '{memberid}'")

    if not member_contactid and not (row["contactid"] and row["contact_exists"]):
        raise ValueError(
            f"Member '{memberid}' has no linked contact; member_contactid must be supplied"
        )
    if not member_userprofileid and not (row["userprofileid"] and row["userprofile_exists"]):
        raise ValueError(
            f"Member '{memberid}' has no linked userprofile; member_userprofileid must be supplied"
        )

    resolved_contactid = member_contactid or row["contactid"]
    resolved_userprofileid = member_userprofileid or row["userprofileid"]
    logger.info(
        "[mycircle.resolve_member] memberid=%s | contact=%s userprofile=%s (resolved: contact=%s profile=%s)",
        memberid, resolved_contactid, resolved_userprofileid,
        not member_contactid, not member_userprofileid,
    )
    return resolved_contactid, resolved_userprofileid


async def add_member_to_mycircle(
    from_contactid: str,
    from_userprofileid: str,
    member_contactid: str | None,
    member_userprofileid: str | None,
    memberid: str | None = None,
    role: str | None = None,
    oauth: str | None = None,
) -> dict:
    """Write a Requested MyCircle invitation and in-app notification to a member in one transaction.

    Args:
        from_contactid: Contact id of the actor requesting the add.
        from_userprofileid: Userprofile id of the actor.
        member_contactid: Invited member's contact id; resolved from memberid when omitted.
        member_userprofileid: Invited member's userprofile id; resolved from memberid when omitted.
        memberid: Member record id, read only to resolve an omitted id.
        role: Actor's numeric role code, stored as the invitation type.
        oauth: Client FCM OAuth bearer token, passed through to delivery.

    Returns:
        The invitation row, notification id and content, and member delivery details.

    Raises:
        ValueError: If the member identity cannot be resolved; nothing is written.
        RuntimeError: If the database pool is not initialised.
    """
    member_contactid, member_userprofileid = await _resolve_mycircle_member_identity(
        memberid, member_contactid, member_userprofileid,
    )

    actor_row, added_row = await asyncio.gather(
        Database.fetchrow(_MYCIRCLE_ACTOR_LOOKUP_QUERY, from_contactid, from_userprofileid),
        Database.fetchrow(
            _MYCIRCLE_ADDED_MEMBER_LOOKUP_QUERY, member_contactid, member_userprofileid,
        ),
    )
    actor = dict(actor_row) if actor_row else {}
    added = dict(added_row) if added_row else {}

    actor_role = actor.get("actor_role")
    actor_firstname = actor.get("actor_firstname")
    actor_profilename = actor.get("actor_profilename")
    actor_businessname = actor.get("actor_businessname")

    notification_content = None
    if actor_role == 2:
        notification_content = f"{actor_firstname or ''} has requested to add you to their My Circle as Customer"
    elif actor_role == 1:
        notification_content = f"{actor_businessname or ''} has requested to add you to their My Circle as Provider"

    type_val = int(role) if role is not None and str(role).strip().lstrip("-").isdigit() else None

    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool not initialized")

    notification_id: str | None = None

    async with Database._pool.acquire() as con:
        async with con.transaction():
            invitation_rec = await con.fetchrow(
                _MYCIRCLE_INVITATION_INSERT,
                type_val, from_contactid, from_userprofileid,
                member_contactid, member_userprofileid,
                2, "Member", "2",
            )
            invitation_row = dict(invitation_rec) if invitation_rec else None

            if invitation_row is not None and notification_content is not None:
                notification_id = await con.fetchval(
                    _MYCIRCLE_NOTIFICATION_INSERT,
                    notification_content,
                    member_contactid, added.get("mem_firstname"),
                    member_userprofileid, added.get("mem_profilename"),
                    from_contactid, actor_firstname,
                    from_userprofileid, actor_profilename,
                )

    logger.info(
        "[mycircle.add_member] actor=%s (role=%s) member=%s | requested=%s notif=%s",
        from_userprofileid, actor_role, member_userprofileid,
        bool(invitation_row), notification_id,
    )

    return {
        "created": invitation_row is not None,
        "invitation_row": invitation_row,
        "actor": {
            "contactid": from_contactid,
            "userprofileid": from_userprofileid,
            "role": actor_role,
        },
        "notification": {"id": notification_id, "content": notification_content if notification_id else None},
        "added_member": {
            "contactid": member_contactid,
            "userprofileid": member_userprofileid,
            "memberid": memberid,
            "verified": bool(added.get("mem_verified")),
        },
        "oauth": oauth,
    }


async def mycircle_add_member_deliver(result: dict) -> None:
    """Mirror a MyCircle member request to Dataverse and push the invited member when their contact is verified."""
    try:
        from app.etown.notifications.core import dispatch

        actor = result.get("actor", {})
        added = result.get("added_member", {})
        notif = result.get("notification", {})

        tasks = []
        if result.get("invitation_row"):
            tasks.append(sync_invitation_to_dataverse(result["invitation_row"]))

        if notif.get("id"):
            tasks.append(sync_accepted_notification_to_dataverse(
                notifications_id=notif["id"],
                notification_content=notif.get("content"),
                role=2,
                notification_status=None,
                notification_type="Added to MyCircle",
                receiver_contact_id=added.get("contactid"),
                receiver_userprofile_id=added.get("userprofileid"),
                sender_contact_id=actor.get("contactid"),
                sender_userprofile_id=actor.get("userprofileid"),
            ))

            if added.get("verified"):
                tasks.append(dispatch(NotificationRequest(
                    notification_type=NotificationType.ADDED_TO_MYCIRCLE,
                    recipient=Recipient(
                        userprofile_id=added.get("userprofileid"),
                        contact_id=added.get("contactid"),
                    ),
                    context={"body": notif.get("content") or ""},
                    push_audience=PushAudience.INDIVIDUAL,
                    oauth_token=result.get("oauth"),
                )))

        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(
            "[mycircle.add_member] delivered | member=%s verified=%s",
            added.get("userprofileid"), added.get("verified"),
        )
    except Exception as exc:
        logger.error("[mycircle.add_member] delivery failed: %s", exc, exc_info=True)


async def remove_member_from_mycircle(
    from_contactid: str,
    from_userprofileid: str,
    member_contactid: str | None,
    member_userprofileid: str | None,
    memberid: str | None = None,
) -> dict:
    """Remove a member from the actor's MyCircle by deleting the pair's relation and invitation rows.

    Args:
        from_contactid: Contact id of the actor removing the member.
        from_userprofileid: Userprofile id of the actor.
        member_contactid: Member's contact id; resolved from memberid when omitted.
        member_userprofileid: Member's userprofile id; resolved from memberid when omitted.
        memberid: Member record id, used only to resolve an omitted id.

    Returns:
        The deleted record ids for the Dataverse delete mirror.

    Raises:
        ValueError: If the member identity cannot be resolved; nothing is deleted.
    """
    member_contactid, member_userprofileid = await _resolve_mycircle_member_identity(
        memberid, member_contactid, member_userprofileid,
    )

    result = await _mycircle_remove_circle_rows(
        from_contactid, from_userprofileid, member_contactid, member_userprofileid,
    )
    logger.info(
        "[mycircle.remove_member] actor=%s member=%s | removed=%s "
        "forward_member=%s forward_provider=%s recip_member=%s recip_provider=%s invitations=%s",
        from_userprofileid, member_userprofileid, result["removed"],
        result["forward_member_relation_id"], result["forward_provider_relation_id"],
        result["reciprocal_member_relation_id"], result["reciprocal_provider_relation_id"],
        result["invitation_ids"],
    )
    return result


async def accept_invite(
    invite_role: int | None,
    inviter: str | None,
    invite_contact_userprofile_id: str | None,
    invitation_code: str | None,
    invite_contact: str | None,
    inviter_userprofile_id: str | None,
    invitation_row_id: str | None,
    invitecontact_role: int | None = None,
    invite_contact_firstname: str | None = None,
    oauth: str | None = None,
) -> dict:
    """Set an invitation's code, mirror it, close sibling pair invites on cancel or decline, and queue accept/decline notifications.

    Args:
        invite_role: Numeric role, 1 for provider, 2 for member; backfilled from the row.
        inviter: Inviter contact id; backfilled from the stored row when empty.
        invite_contact_userprofile_id: Invitee userprofile id; backfilled from the stored row when empty.
        invitation_code: New code, e.g. Accepted, Declined or Cancelled.
        invite_contact: Invitee contact id; backfilled from the stored row when empty.
        inviter_userprofile_id: Inviter userprofile id; backfilled from the stored row when empty.
        invitation_row_id: Primary key of the invitation to update.
        invitecontact_role: Invitee role, 1 for provider, 2 for member.
        invite_contact_firstname: Invitee first name used in the notification.
        oauth: Optional FCM OAuth token for the background push.

    Returns:
        The updated invitation fields, or {} when no row was updated.

    Raises:
        ValueError: If invitation_row_id is empty.
    """
    if not invitation_row_id:
        raise ValueError("invitation_row_id is required")

    try:
        existing_inv = await Database.fetch(
            """
            SELECT invitercontact, inviteruserprofile, invitecontact, inviteuserprofile, invite_role
            FROM etown_invitation
            WHERE invitationid = $1
            LIMIT 1
            """,
            invitation_row_id,
        )
        if existing_inv:
            e = existing_inv[0]
            inviter = inviter or e["invitercontact"]
            inviter_userprofile_id = inviter_userprofile_id or e["inviteruserprofile"]
            invite_contact = invite_contact or e["invitecontact"]
            invite_contact_userprofile_id = invite_contact_userprofile_id or e["inviteuserprofile"]
            if invite_role is None:
                invite_role = e["invite_role"]
    except Exception as _backfill_exc:
        logger.warning(
            "accept_invite: invitation backfill lookup failed for %s: %s",
            invitation_row_id, _backfill_exc,
        )

    try:
        rows = await Database.fetch(
            ACCEPT_INVITE_QUERY,
            invite_role,
            inviter,
            invite_contact_userprofile_id,
            invitation_code,
            invite_contact,
            inviter_userprofile_id,
            invitation_row_id,
        )
        if not rows:
            return {}
        row = rows[0]

        try:
            synced_inv = await Database.fetch(
                "SELECT * FROM etown_invitation WHERE invitationid = $1 LIMIT 1",
                invitation_row_id,
            )
            if synced_inv:
                asyncio.create_task(sync_invitation_to_dataverse(dict(synced_inv[0])))
        except Exception as _inv_sync_exc:
            logger.warning(
                "accept_invite: invitation Dataverse sync failed for %s: %s",
                invitation_row_id, _inv_sync_exc,
            )

        if invitation_code and invitation_code.lower() in ("cancelled", "declined"):
            try:
                closed = await Database.fetch(
                    CLOSE_PAIR_INVITATIONS_QUERY,
                    invitation_code,
                    invitation_row_id,
                    row["invitercontact"],
                    row["invitecontact"],
                )
                for closed_row in closed:
                    asyncio.create_task(sync_invitation_to_dataverse(dict(closed_row)))
                logger.info(
                    "accept_invite: %s closed %d sibling invitation(s) for %s -> %s",
                    invitation_code, len(closed), row["invitercontact"], row["invitecontact"],
                )
            except Exception as _close_exc:
                logger.warning(
                    "accept_invite: closing sibling invitations for %s failed: %s",
                    invitation_row_id, _close_exc,
                )

        response = {
            "Etown_type": row["type"],
            "Etown_typename": row["typename"],
            "Etown_invitercontact": row["invitercontact"],
            "Etown_invitercontactname": row["invitercontactname"],
            "Etown_invitecontact": row["invitecontact"],
            "Etown_invitecontactname": row["invitecontactname"],
            "Etown_invitationcode": row["invitationcode"],
        }

        if invitation_code:
            if invitation_code.lower() == "accepted":
                asyncio.create_task(
                    _background_insert_accepted_notification(
                        invitecontact_role=invitecontact_role,
                        invite_contact_userprofile_id=invite_contact_userprofile_id,
                        invite_contact_firstname=invite_contact_firstname,
                        inviter=inviter,
                        inviter_userprofile_id=inviter_userprofile_id,
                        invite_role=invite_role,
                        invite_contact=invite_contact,
                        invitation_code=invitation_code,
                        oauth=oauth,
                    )
                )
            elif invitation_code.lower() == "cancelled":
                pass
            elif invitation_code.lower() == "declined":
                asyncio.create_task(
                    _background_insert_declined_notification(
                        invitecontact_role=invitecontact_role,
                        inviter_userprofile_id=inviter_userprofile_id,
                        invite_contact_firstname=invite_contact_firstname,
                        invite_contact=invite_contact,
                        invite_contact_userprofile_id=invite_contact_userprofile_id,
                        invite_role=invite_role,
                        inviter=inviter,
                        invitation_code=invitation_code,
                        oauth=oauth,
                    )
                )

        return response
    except ValueError:
        raise
    except Exception as e:
        logger.error(
            f"Error accepting invite for invitation_row_id={invitation_row_id}: {str(e)}",
            exc_info=True,
        )
        raise

async def clear_all_invitations(contact_id: Optional[str], userprofile_id: Optional[str]) -> None:
    """Mark every invitation received by the contact and userprofile as status Read, statuscode 1."""
    query = """
        UPDATE etown_invitation
        SET
            status = 'Read',
            statuscode = '1',
            modifieddate = NOW()
        WHERE
            invitecontact = $1
            AND inviteuserprofile = $2;
    """
    try:
        await Database.execute(query, contact_id, userprofile_id)
    except Exception as e:
        logger.error(
            f"Error clearing all invitations for contact_id={contact_id}, "
            f"userprofile_id={userprofile_id}: {str(e)}",
            exc_info=True,
        )
        raise


_DYNAMIC_SEND_MEMBER_RELATION_INSERT = """
    INSERT INTO etown_memberrelation (
        memberrelationid,
        from_contactid,
        from_firstname,
        from_memberid,
        from_memberidname,
        from_userprofile,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        to_contactid,
        to_firstname,
        to_userprofile,
        createdon
    )
    SELECT
        gen_random_uuid()::text,
        $1,
        (SELECT firstname FROM etown_contact WHERE contactid = $1 LIMIT 1),
        (SELECT memberid FROM etown_member WHERE userprofileid = $2 LIMIT 1),
        (SELECT name FROM etown_member WHERE userprofileid = $2 LIMIT 1),
        $2,
        $5::text,
        $6,
        $5::text::int,
        $6,
        $3,
        (SELECT firstname FROM etown_contact WHERE contactid = $3 LIMIT 1),
        $4,
        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_memberrelation
        WHERE from_userprofile = $2 AND to_userprofile = $4
    )
    RETURNING memberrelationid, from_memberid;
"""


_DYNAMIC_SEND_PROVIDER_RELATION_INSERT = """
    INSERT INTO etown_providerrelation (
        providerrelationid,
        fromcontactid,
        fromcontactidname,
        fromproviderid,
        fromprovideridname,
        fromuserprofileid,
        fromuserprofileidname,
        invite_relationshiptype,
        invite_relationshiptypename,
        relationshiptype,
        relationshiptypename,
        tocontactid,
        tocontactidname,
        touserprofileid,
        touserprofileidname,
        createdon
    )
    SELECT
        gen_random_uuid()::text,
        $1,
        (SELECT firstname FROM etown_contact WHERE contactid = $1 LIMIT 1),
        (
            SELECT providerid FROM etown_provider WHERE userprofileid = $2
            ORDER BY creationdate DESC NULLS LAST LIMIT 1
        ),
        (
            SELECT businessname FROM etown_provider WHERE userprofileid = $2
            ORDER BY creationdate DESC NULLS LAST LIMIT 1
        ),
        $2,
        (SELECT name FROM etown_userprofile WHERE userprofileid = $2 LIMIT 1),
        $5::text,
        $6,
        $5::text::int,
        $6,
        $3,
        (SELECT firstname FROM etown_contact WHERE contactid = $3 LIMIT 1),
        $4,
        (SELECT name FROM etown_userprofile WHERE userprofileid = $4 LIMIT 1),
        NOW()
    WHERE NOT EXISTS (
        SELECT 1 FROM etown_providerrelation
        WHERE fromuserprofileid = $2 AND touserprofileid = $4
    )
    RETURNING providerrelationid, fromproviderid;
"""


async def _add_invitee_to_inviter_mycircle(
    inviter_contact_id: Optional[str],
    inviter_userprofile_id: Optional[str],
    invitee_contact_id: Optional[str],
    invitee_userprofile_id: Optional[str],
) -> Optional[dict]:
    """Insert the inviter-to-invitee relation in the table chosen by the inviter's role, bucketed by the invitee's role.

    Args:
        inviter_contact_id: Sender's contact id.
        inviter_userprofile_id: Sender's userprofile id.
        invitee_contact_id: Invitee's contact id.
        invitee_userprofile_id: Invitee's userprofile id.

    Returns:
        Entity, relation_id, from_id and invite_relationshiptype, or None when nothing was written.
    """
    if not all((inviter_contact_id, inviter_userprofile_id, invitee_contact_id, invitee_userprofile_id)):
        return None

    role_rows = await Database.fetch(
        "SELECT userprofileid, role FROM etown_userprofile WHERE userprofileid = ANY($1::text[])",
        [inviter_userprofile_id, invitee_userprofile_id],
    )
    roles = {r["userprofileid"]: r["role"] for r in role_rows}
    if inviter_userprofile_id not in roles or invitee_userprofile_id not in roles:
        logger.warning(
            "mycircle relation SKIPPED | missing userprofile row | inviter_up=%s inviter_found=%s | "
            "invitee_up=%s invitee_found=%s",
            inviter_userprofile_id, inviter_userprofile_id in roles,
            invitee_userprofile_id, invitee_userprofile_id in roles,
        )
        return None

    invitee_is_provider = roles[invitee_userprofile_id] == 1
    rel_type = "1" if invitee_is_provider else "2"
    rel_name = "Provider" if invitee_is_provider else "Member"

    inviter_is_provider = roles[inviter_userprofile_id] == 1
    statement = (
        _DYNAMIC_SEND_PROVIDER_RELATION_INSERT if inviter_is_provider
        else _DYNAMIC_SEND_MEMBER_RELATION_INSERT
    )
    entity = "etown_providerrelation" if inviter_is_provider else "etown_memberrelation"

    rows = await Database.fetch(
        statement,
        inviter_contact_id, inviter_userprofile_id,
        invitee_contact_id, invitee_userprofile_id,
        rel_type, rel_name,
    )
    if not rows:
        return None

    row = rows[0]
    relation_id = row["providerrelationid"] if inviter_is_provider else row["memberrelationid"]
    from_id = row["fromproviderid"] if inviter_is_provider else row["from_memberid"]
    logger.info(
        "mycircle relation | %s=%s | from_userprofile=%s | to_userprofile=%s | bucket=%s",
        entity, relation_id, inviter_userprofile_id, invitee_userprofile_id, rel_name,
    )
    return {
        "entity": entity,
        "relation_id": relation_id,
        "from_id": from_id,
        "invite_relationshiptype": int(rel_type),
    }


_EXISTING_INVITEE_BY_MOBILE = """
    SELECT c.contactid, c.firstname, c.address, c.profileimage, up.userprofileid
    FROM etown_contact c
    LEFT JOIN LATERAL (
        SELECT up2.userprofileid
        FROM etown_userprofile up2
        WHERE up2.contactid = c.contactid
          AND NOT EXISTS (
              SELECT 1 FROM etown_provider dp
               WHERE dp.disabled
                 AND dp.userprofileid = up2.userprofileid
          )
        ORDER BY (up2.verified IS TRUE) DESC, up2.createdon DESC NULLS LAST
        LIMIT 1
    ) up ON TRUE
    WHERE c.mobilephone = $1
    ORDER BY (c.verified IS TRUE) DESC, c.createdon DESC NULLS LAST
    LIMIT 1
"""


_EXISTING_INVITEE_BY_EMAIL = """
    SELECT c.contactid, c.firstname, c.address, c.profileimage, up.userprofileid
    FROM etown_contact c
    LEFT JOIN LATERAL (
        SELECT up2.userprofileid
        FROM etown_userprofile up2
        WHERE up2.contactid = c.contactid
          AND NOT EXISTS (
              SELECT 1 FROM etown_provider dp
               WHERE dp.disabled
                 AND dp.userprofileid = up2.userprofileid
          )
        ORDER BY (up2.verified IS TRUE) DESC, up2.createdon DESC NULLS LAST
        LIMIT 1
    ) up ON TRUE
    WHERE c.email = $1
    ORDER BY (c.verified IS TRUE) DESC, c.createdon DESC NULLS LAST
    LIMIT 1
"""


async def _resolve_existing_invitee_and_add_to_mycircle(
    inviter_contact_id: Optional[str],
    inviter_userprofile_id: Optional[str],
    mobile: Optional[str] = None,
    email: Optional[str] = None,
) -> dict:
    """Find the verified-first existing contact for a mobile or email and add it to the inviter's MyCircle.

    Args:
        inviter_contact_id: Sender's contact id.
        inviter_userprofile_id: Sender's userprofile id.
        mobile: Invitee's mobile number, checked first.
        email: Invitee's email, used when no mobile is given.

    Returns:
        The matched contact row with its userprofileid, or {} when nothing matched.
    """
    if mobile:
        rows = await Database.fetch(_EXISTING_INVITEE_BY_MOBILE, mobile)
    elif email:
        rows = await Database.fetch(_EXISTING_INVITEE_BY_EMAIL, email)
    else:
        return {}

    row = dict(rows[0]) if rows else {}
    if not row.get("userprofileid"):
        logger.warning(
            "mycircle relation SKIPPED | existing invitee has no userprofile | mobile=%s email=%s contact=%s",
            mobile, email, row.get("contactid"),
        )
        return row

    relation = await _add_invitee_to_inviter_mycircle(
        inviter_contact_id, inviter_userprofile_id, row["contactid"], row["userprofileid"],
    )
    if relation:
        asyncio.create_task(sync_dynamic_send_relation_to_dataverse(
            relation=relation,
            inviter_contact_id=inviter_contact_id,
            inviter_userprofile_id=inviter_userprofile_id,
            invitee_contact_id=row["contactid"],
            invitee_userprofile_id=row["userprofileid"],
        ))
    return row


async def dynamic_send(
    inviter: Optional[str] = None,
    invite_contact: Optional[str] = None,
    type: Optional[int] = None,
    invite_role: Optional[int] = None,
    is_opponentadded: Optional[str] = None,
    invitation_code: Optional[str] = None,
    invitation_row_id: Optional[str] = None,
    notification_row_id: Optional[str] = None,
    invitecontact_email: Optional[str] = None,
    invitecontact_mobilenumber: Optional[str] = None,
    receiver_name: Optional[str] = None,
    inviter_user_profile_id: Optional[str] = None,
    invite_contact_user_profile_id: Optional[str] = None,
    invitation_type: Optional[str] = None,
    invitation_mode: Optional[str] = None,
) -> dict:
    """Create or update an invitation, adding a placeholder member for unknown mobiles and the MyCircle relation where resolvable.

    Args:
        inviter: Inviter contact id; rejected when it is not a known contact.
        invite_contact: Invitee contact id, used on the existing-invitation update path.
        type: Inviter's role code stored on the invitation, 1 for provider.
        invite_role: Role the invitee is invited as, 1 for provider, 2 for member.
        is_opponentadded: Value stored as isopponentadded on the invitation.
        invitation_code: Code written when updating an existing invitation.
        invitation_row_id: Existing invitation to update; None creates a new one.
        notification_row_id: Unused; accepted for API compatibility.
        invitecontact_email: Invitee email, checked in Email mode.
        invitecontact_mobilenumber: Invitee mobile, checked for member and Mobile-mode invites.
        receiver_name: Invitee name for the placeholder contact and receivername.
        inviter_user_profile_id: Userprofile id of the inviter.
        invite_contact_user_profile_id: Userprofile id of the invitee, when known.
        invitation_type: Invitation type such as External, ExternalApp_share or QR.
        invitation_mode: Mobile or Email; selects the duplicate check.

    Returns:
        The Etown_-keyed invitation, a message dict, or a one-item list for User Already Exist.
    """
    count_query = """
        SELECT COUNT(*) AS ContactCount
        FROM etown_contact
        WHERE contactid = $1
    """
    count_rows = await Database.fetch(count_query, inviter)
    contact_count = count_rows[0]["contactcount"] if count_rows else 0
    if not contact_count or int(contact_count) <= 0:
        return {"message": "Contact is not valid"}

    _do_mode_switch = False

    if not invitation_row_id:
        is_valid_role = invite_role == 2
        is_valid_type = invitation_type not in ("ExternalApp_share", "QR")

        if is_valid_role and is_valid_type:
            mobile_count_query = """
                SELECT COUNT(*) AS contact_count
                FROM public.etown_contact
                WHERE mobilephone = $1
            """
            mobile_rows = await Database.fetch(mobile_count_query, invitecontact_mobilenumber)
            mobile_count = mobile_rows[0]["contact_count"] if mobile_rows else 0

            if int(mobile_count) != 0:
                row = await _resolve_existing_invitee_and_add_to_mycircle(
                    inviter, inviter_user_profile_id, mobile=invitecontact_mobilenumber,
                )
                return [
                    {
                        "message": "User Already Exist",
                        "contactid": row.get("contactid"),
                        "name": row.get("firstname"),
                        "address": row.get("address"),
                        "profile_image": row.get("profileimage"),
                    }
                ]
            else:
                insert_query = """
                    WITH new_contact AS (
                        INSERT INTO public.etown_contact
                        (
                            contactid,
                            firstname,
                            mobilephone,
                            activestate,
                            pushnotificationcheck,
                            userlogin,
                            verified,
                            createdon
                        )
                        VALUES
                        (
                            gen_random_uuid()::text,
                            $1,
                            $2,
                            'Active',
                            'true',
                            $2,
                            false,
                            NOW()
                        )
                        RETURNING contactid, firstname, mobilephone
                    ),
                    new_userprofile AS (
                        INSERT INTO public.etown_userprofile
                        (
                            userprofileid,
                            name,
                            contactid,
                            lastsignupstatus,
                            membersignup,
                            mobilenumber,
                            role,
                            rolename,
                            verified,
                            createdon
                        )
                        SELECT
                            gen_random_uuid()::text,
                            $1,
                            contactid,
                            1,
                            'true',
                            mobilephone,
                            2,
                            'Member',
                            false,
                            NOW()
                        FROM new_contact
                        RETURNING userprofileid, contactid
                    )
                    INSERT INTO public.etown_member
                    (
                        memberid,
                        userprofileid,
                        contactid,
                        dateofcreation,
                        mobilenumber,
                        role,
                        rolename,
                        name,
                        verified
                    )
                    SELECT
                        gen_random_uuid()::text,
                        up.userprofileid,
                        up.contactid,
                        NOW(),
                        $2,
                        2,
                        'Member',
                        $1,
                        false
                    FROM new_userprofile up
                """
                await Database.execute(insert_query, receiver_name, invitecontact_mobilenumber)

                new_up_rows = await Database.fetch(
                    """
                    SELECT
                        up.userprofileid,
                        c.contactid,
                        (
                            SELECT m.memberid
                            FROM public.etown_member m
                            WHERE m.contactid = c.contactid
                            ORDER BY m.dateofcreation DESC
                            LIMIT 1
                        ) AS memberid
                    FROM public.etown_userprofile up
                    JOIN public.etown_contact c ON c.contactid = up.contactid
                    WHERE c.mobilephone = $1
                    ORDER BY up.createdon DESC
                    LIMIT 1
                    """,
                    invitecontact_mobilenumber,
                )
                new_userprofile_id = new_up_rows[0]["userprofileid"] if new_up_rows else None
                new_contact_id = new_up_rows[0]["contactid"] if new_up_rows else None
                new_member_id = new_up_rows[0]["memberid"] if new_up_rows else None

                relation = await _add_invitee_to_inviter_mycircle(
                    inviter, inviter_user_profile_id, new_contact_id, new_userprofile_id,
                )

                asyncio.create_task(sync_dynamic_send_new_contact_to_dataverse(
                    contact_id=new_contact_id,
                    userprofile_id=new_userprofile_id,
                    member_id=new_member_id,
                    receiver_name=receiver_name,
                    mobilephone=invitecontact_mobilenumber,
                    role=invite_role,
                    relation=relation,
                    inviter_contact_id=inviter,
                    inviter_userprofile_id=inviter_user_profile_id,
                ))

                _chars = string.ascii_letters + string.digits
                composed_suffixid = "ETEI_" + "".join(random.choices(_chars, k=10))
                new_contact_inv_query = """
                    INSERT INTO public.etown_invitation
                    (
                        invitationid, type, invitationtype, invitercontact,
                        inviteruserprofile, invitecontactemail, invitecontactmobilenumber,
                        invite_role, receivername, suffixid, isnotification,
                        isopponentadded, createdon, invitationcode, status, statuscode,
                        invitecontact, inviteuserprofile, invitecontactname,
                        inviteuserprofilename
                    )
                    VALUES
                    (
                        gen_random_uuid()::text, $1, $2, $3, $4, $5, $6, $7, $8, $9, 0, $10,
                        NOW(), 'Requested', 'Requested', '0',
                        $11, $12, $8, $8
                    )
                    RETURNING *
                """
                new_inv_rows = await Database.fetch(
                    new_contact_inv_query,
                    type, invitation_type, inviter, inviter_user_profile_id,
                    invitecontact_email, invitecontact_mobilenumber,
                    invite_role, receiver_name, composed_suffixid, is_opponentadded,
                    new_contact_id, new_userprofile_id,
                )
                inv_row = new_inv_rows[0] if new_inv_rows else {}
                asyncio.create_task(sync_dynamic_send_invitation_to_dataverse(
                    invitation_id=inv_row.get("invitationid"),
                    inv_type=inv_row.get("type"),
                    invitation_type=inv_row.get("invitationtype"),
                    inviter_contact_id=inviter,
                    inviter_userprofile_id=inviter_user_profile_id,
                    invitecontact_email=invitecontact_email,
                    invitecontact_mobilenumber=invitecontact_mobilenumber,
                    invite_role=inv_row.get("invite_role"),
                    receiver_name=inv_row.get("receivername"),
                    suffixid=inv_row.get("suffixid"),
                    is_opponentadded=inv_row.get("isopponentadded"),
                    status=inv_row.get("status"),
                ))
                return {
                    "Etown_invitationid": inv_row.get("invitationid"),
                    "Etown_invitationtype": inv_row.get("invitationtype"),
                    "Etown_invitationSuffixId": inv_row.get("suffixid"),
                    "Etown_invitercontactId": inv_row.get("invitercontact"),
                    "Etown_inviteruserprofield": inv_row.get("inviteruserprofile"),
                    "Etown_invitername": None,
                    "Etown_inviterrole": inv_row.get("type"),
                    "Etown_inviterrolename": inv_row.get("typename"),
                    "Etown_relationshipname": inv_row.get("invite_rolename"),
                    "Etown_relationshipvalue": inv_row.get("invite_role"),
                    "Etown_invitecontactId": inv_row.get("invitecontact"),
                    "Etown_inviteuserprofield": inv_row.get("inviteuserprofile"),
                    "Etown_inviteename": inv_row.get("receivername"),
                    "Etown_invitationcode": inv_row.get("status"),
                    "Etown_inviterprofileImage": None,
                }
        else:
            _do_mode_switch = True

    if _do_mode_switch:
        _chars = string.ascii_letters + string.digits
        composed_suffixid = "ETEI_" + "".join(random.choices(_chars, k=10))

        if invitation_mode == "Mobile":
            mobile_mode_count_query = """
                SELECT COUNT(*) AS contact_count
                FROM etown_contact
                WHERE mobilephone = $1
            """
            mobile_mode_rows = await Database.fetch(mobile_mode_count_query, invitecontact_mobilenumber)
            mobile_mode_count = mobile_mode_rows[0]["contact_count"] if mobile_mode_rows else 0

            if int(mobile_mode_count) > 0:
                row = await _resolve_existing_invitee_and_add_to_mycircle(
                    inviter, inviter_user_profile_id, mobile=invitecontact_mobilenumber,
                )
                return [
                    {
                        "message": "User Already Exist",
                        "contactid": row.get("contactid"),
                        "name": row.get("firstname"),
                        "address": row.get("address"),
                        "profile_image": row.get("profileimage"),
                    }
                ]

            mobile_invitation_query = """
                INSERT INTO public.etown_invitation
                (
                    invitationid, type, invitationtype, invitercontact,
                    inviteruserprofile, invitecontactemail, invitecontactmobilenumber,
                    invite_role, receivername, suffixid, isnotification,
                    isopponentadded, createdon, invitationcode, status, statuscode
                )
                VALUES
                (
                    gen_random_uuid()::text, $1, $2, $3, $4, $5, $6, $7, $8, $9, 0, $10,
                    NOW(), 'Requested', 'Requested', '0'
                )
                RETURNING *
            """
            mobile_invitation_rows = await Database.fetch(
                mobile_invitation_query,
                type, invitation_type, inviter, inviter_user_profile_id,
                invitecontact_email, invitecontact_mobilenumber,
                invite_role, receiver_name, composed_suffixid, is_opponentadded,
            )
            row = mobile_invitation_rows[0] if mobile_invitation_rows else {}
            return {
                "Etown_invitationid": row.get("invitationid"),
                "Etown_invitationtype": row.get("invitationtype"),
                "Etown_invitationSuffixId": row.get("suffixid"),
                "Etown_invitercontactId": row.get("invitercontact"),
                "Etown_inviteruserprofield": row.get("inviteruserprofile"),
                "Etown_invitername": None,
                "Etown_inviterrole": row.get("type"),
                "Etown_inviterrolename": row.get("typename"),
                "Etown_relationshipname": row.get("invite_rolename"),
                "Etown_relationshipvalue": row.get("invite_role"),
                "Etown_invitecontactId": row.get("invitecontact"),
                "Etown_inviteuserprofield": row.get("inviteuserprofile"),
                "Etown_inviteename": row.get("receivername"),
                "Etown_invitationcode": row.get("status"),
                "Etown_inviterprofileImage": None,
            }

        elif invitation_mode == "Email":
            email_count_query = """
                SELECT COUNT(*) AS contact_count
                FROM etown_contact
                WHERE email = $1
            """
            email_mode_rows = await Database.fetch(email_count_query, invitecontact_email)
            email_mode_count = email_mode_rows[0]["contact_count"] if email_mode_rows else 0

            if int(email_mode_count) > 0:
                row = await _resolve_existing_invitee_and_add_to_mycircle(
                    inviter, inviter_user_profile_id, email=invitecontact_email,
                )
                return [
                    {
                        "message": "User Already Exist",
                        "contactid": row.get("contactid"),
                        "name": row.get("firstname"),
                        "address": row.get("address"),
                        "profile_image": row.get("profileimage"),
                    }
                ]

            email_invitation_query = """
                INSERT INTO public.etown_invitation
                (
                    invitationid, type, invitationtype, invitercontact,
                    inviteruserprofile, invitecontactemail, invitecontactmobilenumber,
                    invite_role, receivername, suffixid, isnotification,
                    isopponentadded, createdon, invitationcode, status, statuscode
                )
                VALUES
                (
                    gen_random_uuid()::text, $1, $2, $3, $4, $5, $6, $7, $8, $9, 0, $10,
                    NOW(), 'Requested', 'Requested', '0'
                )
                RETURNING *
            """
            email_invitation_rows = await Database.fetch(
                email_invitation_query,
                type, invitation_type, inviter, inviter_user_profile_id,
                invitecontact_email, invitecontact_mobilenumber,
                invite_role, receiver_name, composed_suffixid, is_opponentadded,
            )
            row = email_invitation_rows[0] if email_invitation_rows else {}
            return {
                "Etown_invitationid": row.get("invitationid"),
                "Etown_invitationtype": row.get("invitationtype"),
                "Etown_invitationSuffixId": row.get("suffixid"),
                "Etown_invitercontactId": row.get("invitercontact"),
                "Etown_inviteruserprofield": row.get("inviteruserprofile"),
                "Etown_invitername": None,
                "Etown_inviterrole": row.get("type"),
                "Etown_inviterrolename": row.get("typename"),
                "Etown_relationshipname": row.get("invite_rolename"),
                "Etown_relationshipvalue": row.get("invite_role"),
                "Etown_invitecontactId": row.get("invitecontact"),
                "Etown_inviteuserprofield": row.get("inviteuserprofile"),
                "Etown_inviteename": row.get("receivername"),
                "Etown_invitationcode": row.get("status"),
                "Etown_inviterprofileImage": None,
            }

        else:
            else_invitation_query = """
                INSERT INTO public.etown_invitation
                (
                    invitationid, type, invitationtype, invitercontact,
                    inviteruserprofile, invitecontactemail, invitecontactmobilenumber,
                    invite_role, receivername, suffixid, isnotification,
                    isopponentadded, createdon, invitationcode, status, statuscode
                )
                VALUES
                (
                    gen_random_uuid()::text, $1, $2, $3, $4, $5, $6, $7, $8, $9, 0, $10,
                    NOW(), 'Requested', 'Requested', '0'
                )
                RETURNING *
            """
            else_invitation_rows = await Database.fetch(
                else_invitation_query,
                type, invitation_type, inviter, inviter_user_profile_id,
                invitecontact_email, invitecontact_mobilenumber,
                invite_role, receiver_name, composed_suffixid, is_opponentadded,
            )
            row = else_invitation_rows[0] if else_invitation_rows else {}
            return {
                "Etown_invitationid": row.get("invitationid"),
                "Etown_invitationtype": row.get("invitationtype"),
                "Etown_invitationSuffixId": row.get("suffixid"),
                "Etown_invitercontactId": row.get("invitercontact"),
                "Etown_inviteruserprofield": row.get("inviteruserprofile"),
                "Etown_invitername": None,
                "Etown_inviterrole": row.get("type"),
                "Etown_inviterrolename": row.get("typename"),
                "Etown_relationshipname": row.get("invite_rolename"),
                "Etown_relationshipvalue": row.get("invite_role"),
                "Etown_invitecontactId": row.get("invitecontact"),
                "Etown_inviteuserprofield": row.get("inviteuserprofile"),
                "Etown_inviteename": row.get("receivername"),
                "Etown_invitationcode": row.get("status"),
                "Etown_inviterprofileImage": None,
            }

    existing_invitation_query = """
        SELECT COUNT(*) AS invitation_count
        FROM public.etown_invitation
        WHERE invitationcode = 'Requested'
        AND (
            (
                invitercontact = $1
                AND inviteruserprofile = $2
                AND invitecontact = $3
                AND inviteuserprofile = $4
            )
            OR
            (
                invitercontact = $3
                AND inviteruserprofile = $4
                AND invitecontact = $1
                AND inviteuserprofile = $2
            )
        )
    """
    existing_rows = await Database.fetch(
        existing_invitation_query,
        inviter,
        inviter_user_profile_id,
        invite_contact,
        invite_contact_user_profile_id,
    )
    existing_count = existing_rows[0]["invitation_count"] if existing_rows else 0
    if int(existing_count) > 0:
        return {"message": "Already Exist"}

    update_query = """
        UPDATE public.etown_invitation
        SET
            type = $1,
            typename = CASE
                WHEN $1 = 1 THEN 'Provider'
                ELSE 'Member'
            END,
            inviteuserprofile = $2,
            inviteuserprofilename = (
                SELECT name
                FROM public.etown_userprofile
                WHERE userprofileid = $2
                LIMIT 1
            ),
            invite_role = $3,
            invite_rolename = CASE
                WHEN $3 = 1 THEN 'Provider'
                ELSE 'Member'
            END,
            invitationcode = $4,
            invitecontact = $5,
            invitecontactname = (
                SELECT firstname
                FROM public.etown_contact
                WHERE contactid = $5
                LIMIT 1
            ),
            isnotification = 1,
            isnotificationame = 'True',
            isopponentadded = $6,
            modifieddate = NOW()
        WHERE invitationid = $7
    """
    await Database.execute(
        update_query,
        type,
        invite_contact_user_profile_id,
        invite_role,
        invitation_code,
        invite_contact,
        is_opponentadded,
        invitation_row_id,
    )

    memberrelation_count_query = """
        SELECT COUNT(*) AS memberrelation_count
        FROM public.etown_memberrelation
        WHERE (
            (
                from_contactid = $1
                AND from_userprofile = $2
                AND to_contactid = $3
                AND to_userprofile = $4
            )
            OR
            (
                from_contactid = $3
                AND from_userprofile = $4
                AND to_contactid = $1
                AND to_userprofile = $2
            )
        )
    """
    providerrelation_count_query = """
        SELECT COUNT(*) AS providerrelation_count
        FROM public.etown_providerrelation
        WHERE (
            (
                fromcontactid = $1
                AND fromuserprofileid = $2
                AND tocontactid = $3
                AND touserprofileid = $4
            )
            OR
            (
                fromcontactid = $3
                AND fromuserprofileid = $4
                AND tocontactid = $1
                AND touserprofileid = $2
            )
        )
    """
    relation_args = (
        inviter,
        inviter_user_profile_id,
        invite_contact,
        invite_contact_user_profile_id,
    )
    memberrelation_rows = await Database.fetch(memberrelation_count_query, *relation_args)
    providerrelation_rows = await Database.fetch(providerrelation_count_query, *relation_args)

    memberrelation_count = memberrelation_rows[0]["memberrelation_count"] if memberrelation_rows else 0
    providerrelation_count = providerrelation_rows[0]["providerrelation_count"] if providerrelation_rows else 0

    if int(memberrelation_count) > 0 or int(providerrelation_count) > 0:
        return {"message": "Already Invited"}

    notification_count_query = """
        SELECT COUNT(*) AS notification_count
        FROM public.etown_notifications
        WHERE (
            (
                sendercontactid = $1
                AND senderuserprofileid = $2
                AND receivercontactid = $3
                AND receiveruserprofileid = $4
                AND notificationcontent ILIKE '%invited%'
            )
            OR
            (
                sendercontactid = $3
                AND senderuserprofileid = $4
                AND receivercontactid = $1
                AND receiveruserprofileid = $2
                AND notificationcontent ILIKE '%invited%'
            )
        )
    """
    notification_rows = await Database.fetch(notification_count_query, *relation_args)
    notification_count = notification_rows[0]["notification_count"] if notification_rows else 0

    if int(notification_count) > 0:
        return {"message": "Already Requested"}

    known_relation = await _add_invitee_to_inviter_mycircle(
        inviter, inviter_user_profile_id, invite_contact, invite_contact_user_profile_id,
    )
    if known_relation:
        asyncio.create_task(sync_dynamic_send_relation_to_dataverse(
            relation=known_relation,
            inviter_contact_id=inviter,
            inviter_userprofile_id=inviter_user_profile_id,
            invitee_contact_id=invite_contact,
            invitee_userprofile_id=invite_contact_user_profile_id,
        ))

    invitation_detail_query = """
        SELECT
            i.invitationid,
            i.invitercontact,
            i.invitecontact,
            i.inviteuserprofile,
            i.inviteruserprofile,
            i.invitationtype,
            i.invitationcode,
            i.invite_role,
            i.invite_rolename,
            i.type,
            i.typename,
            i.status,
            i.suffixid,

            inviter_role.role AS inviter_role,
            inviter_role.userprofileid AS inviter_userprofileid,

            invitee_role.role AS invitee_role,
            invitee_role.userprofileid AS invitee_userprofileid,

            inviter_contact.profileimage AS inviter_profileimage,
            inviter_contact.firstname AS inviter_firstname,
            inviter_contact.contactid AS inviter_contactid,

            inviter_provider_profile.serviceimageurl AS inviter_serviceimageurl,
            inviter_provider_profile.businessname AS inviter_businessname,
            inviter_provider_profile.providerid AS inviter_providerid,

            invitee_contact.profileimage AS invitee_profileimage,
            invitee_contact.firstname AS invitee_firstname,
            invitee_contact.contactid AS invitee_contactid,

            invitee_provider_profile.serviceimageurl AS invitee_serviceimageurl,
            invitee_provider_profile.businessname AS invitee_businessname,
            invitee_provider_profile.providerid AS invitee_providerid

        FROM public.etown_invitation i

        LEFT JOIN public.etown_userprofile inviter_role
            ON inviter_role.userprofileid = i.inviteruserprofile

        LEFT JOIN public.etown_userprofile invitee_role
            ON invitee_role.userprofileid = i.inviteuserprofile

        LEFT JOIN public.etown_contact inviter_contact
            ON inviter_contact.contactid = i.invitercontact

        LEFT JOIN public.etown_provider inviter_provider_profile
            ON inviter_provider_profile.userprofileid = i.inviteruserprofile

        LEFT JOIN public.etown_contact invitee_contact
            ON invitee_contact.contactid = i.invitecontact

        LEFT JOIN public.etown_provider invitee_provider_profile
            ON invitee_provider_profile.userprofileid = i.inviteuserprofile

        WHERE i.invitationid = $1
    """
    detail_rows = await Database.fetch(invitation_detail_query, invitation_row_id)
    row = detail_rows[0] if detail_rows else {}

    inviter_role_val = row.get("inviter_role")
    invite_role_val = row.get("invite_role")

    invitername = (
        row.get("inviter_businessname") if inviter_role_val == 1
        else row.get("inviter_firstname") if inviter_role_val == 2
        else None
    )
    inviteename = (
        row.get("invitee_businessname") if invite_role_val == 1
        else row.get("invitee_firstname") if invite_role_val == 2
        else None
    )
    inviter_profile_image = (
        row.get("inviter_serviceimageurl") if inviter_role_val == 1
        else row.get("inviter_profileimage") if inviter_role_val == 2
        else None
    )

    return {
        "Etown_invitationid": row.get("invitationid"),
        "Etown_invitationtype": row.get("invitationtype"),
        "Etown_invitationSuffixId": row.get("suffixid"),
        "Etown_invitercontactId": row.get("invitercontact"),
        "Etown_inviteruserprofield": row.get("inviteruserprofile"),
        "Etown_invitername": invitername,
        "Etown_inviterrole": row.get("type"),
        "Etown_inviterrolename": row.get("typename"),
        "Etown_relationshipname": row.get("invite_rolename"),
        "Etown_relationshipvalue": row.get("invite_role"),
        "Etown_invitecontactId": row.get("invitecontact"),
        "Etown_inviteuserprofield": row.get("inviteuserprofile"),
        "Etown_inviteename": inviteename,
        "Etown_invitationcode": row.get("invitationcode"),
        "Etown_inviterprofileImage": inviter_profile_image,
    }


async def new_invite_send(
    inviter: str,
    inviter_firstname: str,
    invite_contact: str,
    invite_contact_firstname: str,
    type: int,
    invite_role: int,
    invitation_code: str,
    inviter_userprofile_id: str,
    invite_contact_userprofile_id: str,
) -> dict:
    """Reopen a cancelled or insert an Internal invitation with its notification in one CTE, mirror both, and push the invitee.

    Args:
        inviter: Contact id of the inviter.
        inviter_firstname: Inviter first name for the notification text.
        invite_contact: Contact id of the person being invited.
        invite_contact_firstname: First name of the invitee.
        type: Invitation type; 1 names the role Partner or Customer, else Provider or Member.
        invite_role: Role invited to, 1 for provider, 2 for member.
        invitation_code: Status code for the invitation, e.g. Requested.
        inviter_userprofile_id: Userprofile id of the inviter.
        invite_contact_userprofile_id: Userprofile id of the invitee.

    Returns:
        Invitation summary, or {"message": "Already requested this person"} when nothing was written.
    """
    if invite_role == 1:
        invite_role_name = "Partner" if type == 1 else "Provider"
    elif invite_role == 2:
        invite_role_name = "Customer" if type == 1 else "Member"
    else:
        invite_role_name = ""

    notification_content = (
        f"{inviter_firstname} invited you to join as {invite_role_name} role in etown."
    )

    cte_query = """
        WITH
          duplicate_check AS (
              SELECT COUNT(*) AS cnt
              FROM etown_invitation
              WHERE invitercontact = $1
                AND invitecontact = $2
                AND invitationcode IN ('Requested', 'Pending')
          ),
          reopened_invitation AS (
              UPDATE etown_invitation
              SET type              = $3,
                  invitercontactname = $4,
                  inviteruserprofile = $5,
                  invitecontactname  = $6,
                  inviteuserprofile  = $7,
                  invite_role        = $8,
                  invite_rolename    = $9,
                  invitationcode    = $10,
                  isnotification    = 1,
                  createdon         = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata',
                  modifieddate      = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'
              WHERE invitationid = (
                  SELECT invitationid
                  FROM etown_invitation
                  WHERE invitercontact = $1
                    AND invitecontact = $2
                    AND invitationcode = 'Cancelled'
                  ORDER BY COALESCE(modifieddate, createdon) DESC
                  LIMIT 1
              )
              RETURNING *
          ),
          new_invitation AS (
              INSERT INTO etown_invitation (
                  invitationid,
                  type,
                  invitationtype,
                  invitercontact,
                  invitercontactname,
                  inviteruserprofile,
                  invitecontact,
                  invitecontactname,
                  inviteuserprofile,
                  invite_role,
                  invite_rolename,
                  invitationcode,
                  isnotification,
                  isopponentadded,
                  createdon
              )
              SELECT
                  gen_random_uuid()::text,
                  $3,
                  'Internal',
                  $1,
                  $4,
                  $5,
                  $2,
                  $6,
                  $7,
                  $8,
                  $9,
                  $10,
                  1,
                  'false',
                  CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'
              WHERE (SELECT cnt FROM duplicate_check) = 0
                AND NOT EXISTS (SELECT 1 FROM reopened_invitation)
              RETURNING *
          ),
          invitation_result AS (
              SELECT * FROM reopened_invitation
              UNION ALL
              SELECT * FROM new_invitation
          ),
          new_notification AS (
              INSERT INTO etown_notifications (
                  notificationsid,
                  notificationcontent,
                  sendercontactid,
                  senderuserprofileid,
                  receivercontactid,
                  receiveruserprofileid,
                  notificationstatus,
                  notificationtype,
                  createdon
              )
              SELECT
                  gen_random_uuid()::text,
                  $11,
                  $1,
                  $5,
                  $2,
                  $7,
                  $10,
                  'Invitation',
                  CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'
              WHERE EXISTS (SELECT 1 FROM invitation_result)
              RETURNING notificationsid
          )
        SELECT ni.*, nn.notificationsid AS notification_id
          FROM invitation_result ni
        LEFT JOIN new_notification nn ON true
    """

    rows = await Database.fetch(
        cte_query,
        inviter,
        invite_contact,
        type,
        inviter_firstname,
        inviter_userprofile_id,
        invite_contact_firstname,
        invite_contact_userprofile_id,
        invite_role,
        invite_role_name,
        invitation_code,
        notification_content,
    )

    if not rows:
        return {"message": "Already requested this person"}

    row = dict(rows[0])

    asyncio.create_task(sync_invitation_to_dataverse(row))

    notification_id = row.get("notification_id")
    if notification_id:
        asyncio.create_task(sync_accepted_notification_to_dataverse(
            notifications_id=notification_id,
            notification_content=notification_content,
            role=invite_role,
            notification_status=invitation_code,
            notification_type="Invitation",
            receiver_contact_id=invite_contact,
            receiver_userprofile_id=invite_contact_userprofile_id,
            sender_contact_id=inviter,
            sender_userprofile_id=inviter_userprofile_id,
        ))

    try:
        oauth_token = await get_fcm_oauth_token()
        for token in await get_device_tokens_by_userprofile_id(invite_contact_userprofile_id):
            await send_fcm_message(
                oauth_token=oauth_token,
                payload={
                    "message": {
                        "token": token,
                        "notification": {
                            "title": "etown",
                            "body": notification_content,
                        },
                        "data": {
                            "type": "Invite",
                            "userprofile": invite_contact_userprofile_id,
                            "role": str(invite_role),
                            "route": "/notificationScreen",
                        },
                    }
                },
                device_token=token,
                label="new_invite_send FCM",
            )
    except Exception as exc:
        logger.error("new_invite_send: FCM push failed | %s", exc, exc_info=True)

    return {
        "Inviter": row.get("invitercontact"),
        "Invitecontact": row.get("invitecontact"),
        "Invitationcode": row.get("invitationcode"),
        "Invitationid": row.get("invitationid"),
        "InviterUserProfileId": row.get("inviteruserprofile"),
        "Type": row.get("type"),
    }


async def external_invite_send(
    inviter: Optional[str],
    invite_contact: Optional[str],
    type: Optional[int],
    invite_role: Optional[int],
    invitation_code: Optional[str],
    invitation_row_id: Optional[str],
    invitecontact_email: Optional[str],
    invitecontact_mobilenumber: Optional[str],
    receiver_name: Optional[str],
    inviter_user_profile_id: Optional[str],
    invite_contact_user_profile_id: Optional[str],
) -> dict:
    """Create an External invitation unless one is active for the mobile or email, or update an existing one after duplicate checks.

    Args:
        inviter: Contact id of the inviter.
        invite_contact: Invitee contact id, used on the update path.
        type: Invitation type integer.
        invite_role: Role integer for the invitation.
        invitation_code: Status code written on the update path.
        invitation_row_id: Existing invitation id; None creates a new invitation.
        invitecontact_email: Invitee email stored on new invitations.
        invitecontact_mobilenumber: Invitee mobile stored on new invitations.
        receiver_name: Display name of the invitation receiver.
        inviter_user_profile_id: Userprofile id of the inviter.
        invite_contact_user_profile_id: Userprofile id of the invitee.

    Returns:
        A status message and, when created, the generated suffix_id.
    """
    if not invitation_row_id:
        alphabet = string.ascii_letters + string.digits
        suffix_id = "ETEI_" + "".join(secrets.choice(alphabet) for _ in range(16))

        row = await Database.fetchrow(
            """
            WITH
              contact_check AS (
                SELECT firstname
                FROM etown_contact
                WHERE contactid = $1
              ),

              duplicate_check AS (
                SELECT COUNT(*) AS cnt
                FROM etown_invitation
                WHERE invitercontact = $1
                  AND LOWER(invitationcode) NOT IN ('declined', 'cancelled', 'error')
                  AND (
                    (
                      NULLIF($4, '') IS NOT NULL
                      AND RIGHT(regexp_replace(invitecontactmobilenumber, '\\D', '', 'g'), 10)
                          = RIGHT(regexp_replace($4, '\\D', '', 'g'), 10)
                    )
                    OR (
                      NULLIF($3, '') IS NOT NULL
                      AND LOWER(invitecontactemail) = LOWER($3)
                    )
                  )
              ),
              inserted AS (
                INSERT INTO etown_invitation (
                  invitationid, invitationcode, invitercontact, invitercontactname,
                  inviteruserprofile, invitecontactemail, invitecontactmobilenumber,
                  type, invitationtype, invite_role, isnotification, receivername,
                  suffixid, createdon
                )
                SELECT
                  gen_random_uuid()::text, 'Requested', $1, firstname,
                  $2, $3, $4,
                  $5, 'External', $6, 0, $7,
                  $8, CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'
                FROM contact_check
                WHERE (SELECT cnt FROM duplicate_check) = 0
                RETURNING *
              )
            SELECT
              CASE
                WHEN NOT EXISTS (SELECT 1 FROM contact_check) THEN 'invalid_contact'
                WHEN (SELECT cnt FROM duplicate_check) > 0 THEN 'duplicate'
                ELSE 'created'
              END AS _action,
              i.*
            FROM inserted i
            FULL JOIN (VALUES (1)) AS _force(n) ON TRUE
""",
            inviter,
            inviter_user_profile_id,
            invitecontact_email,
            invitecontact_mobilenumber,
            type,
            invite_role,
            receiver_name,
            suffix_id,
        )

        if not row or row["_action"] == "invalid_contact":
            return {"message": "Contact is not valid"}

        if row["_action"] == "duplicate":
            target = invitecontact_mobilenumber or invitecontact_email
            logger.info(
                f"External invite to {target} from {inviter} skipped - an active invitation "
                f"already exists."
            )
            return {"message": "Already requested this person"}

        asyncio.create_task(sync_invitation_to_dataverse(dict(row)))
        return {"message": "Invitation created successfully", "suffix_id": suffix_id}

    row = await Database.fetchrow(
        """
        WITH
          chk AS (
            SELECT
              inv.invitationcode,
              EXISTS (
                SELECT 1 FROM etown_memberrelation
                WHERE (from_contactid = $7 AND from_userprofile = $8
                       AND to_contactid = $2 AND to_userprofile = $3)
                   OR (from_contactid = $2 AND from_userprofile = $3
                       AND to_contactid = $7 AND to_userprofile = $8)
              ) AS has_member_rel,
              EXISTS (
                SELECT 1 FROM etown_providerrelation
                WHERE (fromcontactid = $7 AND fromuserprofileid = $8
                       AND tocontactid = $2 AND touserprofileid = $3)
                   OR (fromcontactid = $2 AND fromuserprofileid = $3
                       AND tocontactid = $7 AND touserprofileid = $8)
              ) AS has_prov_rel,
              EXISTS (
                SELECT 1 FROM etown_notifications
                WHERE (sendercontactid = $7 AND senderuserprofileid = $8
                       AND receivercontactid = $2 AND receiveruserprofileid = $3)
                   OR (sendercontactid = $2 AND senderuserprofileid = $3
                       AND receivercontactid = $7 AND receiveruserprofileid = $8)
              ) AS has_notif
            FROM etown_invitation inv
            WHERE inv.invitationid = $6
          ),
          decision AS (
            SELECT
              CASE
                WHEN NOT EXISTS (SELECT 1 FROM chk)
                  THEN 'already_exist'::text
                WHEN (SELECT invitationcode FROM chk LIMIT 1) = 'accepted'
                  THEN 'already_exist'
                WHEN COALESCE((SELECT has_member_rel OR has_prov_rel FROM chk LIMIT 1), false)
                  THEN 'already_invited'
                WHEN COALESCE((SELECT has_notif FROM chk LIMIT 1), false)
                  THEN 'already_requested'
                ELSE 'success'
              END AS result
          ),
          updated AS (
            UPDATE etown_invitation
            SET type              = $1,
                invitecontact     = $2,
                inviteuserprofile = $3,
                invite_role       = $4,
                invitationcode    = $5,
                modifieddate      = NOW()
            WHERE invitationid = $6
              AND (SELECT result FROM decision) IN ('already_requested', 'success')
            RETURNING *
          ),
          deleted AS (
            DELETE FROM etown_invitation
            WHERE invitationid = $6
              AND (SELECT result FROM decision) = 'already_invited'
            RETURNING invitationid
          )
        SELECT
          (SELECT result  FROM decision)  AS _action,
          (SELECT COUNT(*) FROM deleted)  AS _del_count,
          u.*
        FROM updated u
        FULL JOIN (VALUES (1)) AS _force(n) ON TRUE
        """,
        type,
        invite_contact,
        invite_contact_user_profile_id,
        invite_role,
        invitation_code,
        invitation_row_id,
        inviter,
        inviter_user_profile_id,
    )

    if not row:
        return {"message": "Already Exist"}

    action = row["_action"]
    if action == "already_exist":
        return {"message": "Already Exist"}
    if action == "already_invited":
        return {"message": "Already Invited"}
    if action == "already_requested":
        return {"message": "Already Requested"}

    asyncio.create_task(sync_invitation_to_dataverse(dict(row)))
    return {"message": "Invitation sent successfully"}


EMAIL_TEMPLATE_ID = "73ea4621-2f02-ef11-a1fe-000d3a343707"


class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML-to-plain-text converter using stdlib HTMLParser."""

    def __init__(self) -> None:
        """Initialise the parser with an empty list of text parts."""
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Collect a run of text found between tags."""
        self._parts.append(data)

    def get_text(self) -> str:
        """Return the collected text, HTML-unescaped and stripped."""
        return html.unescape("".join(self._parts)).strip()


def _html_to_text(html_content: str) -> str:
    """Return the plain text of an HTML string with tags stripped, or "" for empty input."""
    if not html_content:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(html_content)
    return parser.get_text()


async def get_invite_email_template() -> dict:
    """Return the invite email template row with a plain-text subject_text added, or {} if missing."""
    rows = await Database.fetch(
        "SELECT * FROM etown_email_templates WHERE emailtemplate = $1",
        EMAIL_TEMPLATE_ID,
    )
    if not rows:
        logger.warning("get_invite_email_template: template not found | id=%s", EMAIL_TEMPLATE_ID)
        return {}
    template = dict(rows[0])
    subject_html = template.get("subject") or ""
    template["subject_text"] = _html_to_text(subject_html)
    return template


async def mycircle_update_contact(
    contactid: str,
    userprofileid: str,
    mobilenumber_accesstoken: str,
) -> dict:
    """Store a contact's access token and mark the contact, userprofile and member rows verified.

    Args:
        contactid: Contact to update.
        userprofileid: Userprofile whose profile and member rows are verified.
        mobilenumber_accesstoken: Token stored in etown_contact.accesstoken.

    Returns:
        The updated contact_id, userprofile_id and member_ids.
    """
    row = await Database.fetchrow(
        """
        WITH upd_contact AS (
            UPDATE etown_contact
            SET accesstoken = $3, verified = TRUE, modifieddate = NOW()
            WHERE contactid = $1
            RETURNING contactid
        ),
        upd_userprofile AS (
            UPDATE etown_userprofile
            SET verified = TRUE, modifieddate = NOW()
            WHERE userprofileid = $2
            RETURNING userprofileid
        ),
        upd_member AS (
            UPDATE etown_member
            SET verified = TRUE, modifieddate = NOW()
            WHERE userprofileid = $2
            RETURNING memberid
        )
        SELECT
            (SELECT contactid FROM upd_contact)         AS contact_id,
            (SELECT userprofileid FROM upd_userprofile) AS userprofile_id,
            ARRAY(SELECT memberid FROM upd_member)      AS member_ids
        """,
        contactid,
        userprofileid,
        mobilenumber_accesstoken,
    )

    result = {
        "contact_id": str(row["contact_id"]) if row and row["contact_id"] else None,
        "userprofile_id": str(row["userprofile_id"]) if row and row["userprofile_id"] else None,
        "member_ids": [str(x) for x in row["member_ids"]] if row and row["member_ids"] else [],
    }

    if not result["contact_id"]:
        logger.warning(
            "[mycircle_update_contact] No contact matched | contactid=%s", contactid
        )
    logger.info(
        "[mycircle_update_contact] Verified in place | contactid=%s | userprofileid=%s | members=%d",
        contactid, userprofileid, len(result["member_ids"]),
    )
    return result


_FAVORITE_MAP = {"true": (1, "Yes"), "false": (2, "No")}


async def update_relation_favorite(
    login_userprofile_id: str,
    relation_guid_id: str,
    inviter_favorite: str | None,
) -> dict:
    """Set the favorite flag on a member or provider relation row, chosen by the login user's role.

    Args:
        login_userprofile_id: Userprofile id of the acting user.
        relation_guid_id: Primary key of the relation row to update.
        inviter_favorite: "true" or "false"; any other value clears the flag.

    Returns:
        A message, plus dataverse_entity, relation_id and favorite when a row was updated.
    """
    favorite_value, favorite_name = _FAVORITE_MAP.get(
        (inviter_favorite or "").strip().lower(), (None, None)
    )

    role_rows = await Database.fetch(
        "SELECT role FROM etown_userprofile WHERE userprofileid = $1",
        login_userprofile_id,
    )
    if not role_rows:
        return {"message": "User not found"}
    role = role_rows[0]["role"]

    if role == 2:
        entity = "ets_memberrelations"
        updated = await Database.fetch(
            """
            UPDATE etown_memberrelation
            SET favorite = $1, favoritename = $2, modifieddate = NOW()
            WHERE memberrelationid = $3
            RETURNING memberrelationid
            """,
            favorite_value, favorite_name, relation_guid_id,
        )
    elif role == 1:
        entity = "ets_providerrelations"
        updated = await Database.fetch(
            """
            UPDATE etown_providerrelation
            SET favorite = $1, favoritename = $2, modifieddate = NOW()
            WHERE providerrelationid = $3
            RETURNING providerrelationid
            """,
            favorite_value, favorite_name, relation_guid_id,
        )
    else:
        return {"message": "Role is invalid"}

    if not updated:
        logger.warning(
            "[update_relation_favorite] No relation matched | role=%s | relation_guid_id=%s",
            role, relation_guid_id,
        )
        return {"message": "Relation record not found"}

    return {
        "message": "Updated Successfully",
        "dataverse_entity": entity,
        "relation_id": relation_guid_id,
        "favorite": favorite_value,
    }
