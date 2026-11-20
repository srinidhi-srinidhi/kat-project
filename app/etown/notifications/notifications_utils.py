"""Notification Utilities - Postgres logic for the in-app notification inbox.

Domain layer behind /api/notifications: counts unread notifications and invitations, returns a paginated inbox merging notification rows with My Circle offers and feed posts, and marks one notification or a whole inbox as read.

Features:
    - Notification Count: Unread notifications plus invitations, skipping deleted parties.
    - Merged Notification Feed: Notifications, My Circle offers and feed posts in one paginated list.
    - Disabled-sender Filter: get_notifications() drops rows sent by a disabled provider.
    - Sender Badges: Rows report the sender's providertype and plan badge_label.
    - Read-state Mutations: Mark a single notification or a user's entire inbox as Inactive/Read.

Classes:
    No classes defined (module exposes notification query and update functions)

Functions:
    get_notification_count(): Query the DB for a user's unread notification + invitation count.
    get_notifications(): Fetch a paginated notification feed merged with My Circle offers and feed posts.
    update_notification_status(): Mark a single notification as Inactive/Read by notificationsid.
    clear_all_notifications(): Mark every notification for a receiver as Inactive/Read.

Dependencies:
    - app.db.client: Async database client
    - app.core.logging: Centralised logging infrastructure
    - app.etown.plans.plans_utils: resolve_provider_badges() for sender badges
    - app.etown.offers.offers_utils: fetch_mycircle_offers() (lazy import, merged into the feed)
    - app.etown.feed.feed_utils: Database.get_feeds() (lazy import, merged into the feed)

Updates:
    13-May-2026: Initial creation with notification count query; added update_notification_status.
    18-Aug-2026: Notification, offer and feed rows report the sender's providertype and badge_label.
    19-Aug-2026: get_notification_count() skips notifications and invitations naming a deleted party.
    02-Sep-2026: get_notifications() drops rows sent by a disabled provider.
"""

from typing import Optional

from app.core.logging import logger
from app.db.client import Database
from app.etown.plans.plans_utils import resolve_provider_badges


async def get_notification_count(contact_id: Optional[str], userprofile_id: Optional[str]) -> int:
    """Return a user's unread notification count plus pending invitation count, skipping deleted parties.

    Raises:
        Exception: Re-raised after logging when either count query fails.
    """
    notification_query = """
        SELECT
            COUNT(*) AS notification_count
        FROM
            etown_notifications en
        INNER JOIN etown_userprofile eup
            ON en.senderuserprofileid = eup.userprofileid
        INNER JOIN etown_contact ec
            ON eup.contactid = ec.contactid
        LEFT JOIN etown_provider ep
            ON eup.userprofileid = ep.userprofileid
        WHERE
            en.receivercontactid = $1
            AND en.receiveruserprofileid = $2
            AND en.statuscode = '1'

            AND NOT etown_is_deleted(en.senderuserprofileid)
"""
    invitation_query = """
        SELECT COUNT(*) AS invitation_count
        FROM etown_invitation inv
        WHERE

        NOT etown_is_deleted(inv.invitercontact)
        AND NOT etown_is_deleted(inv.inviteruserprofile)
        AND NOT etown_is_deleted(inv.invitecontact)
        AND NOT etown_is_deleted(inv.inviteuserprofile)
        AND (
        (

            inv.invitecontact = $1
            AND inv.inviteuserprofile = $2
            AND inv.invitecontact IS NOT NULL
            AND inv.invitationcode = 'Requested'
        )
        OR
        (

            inv.invitercontact = $1
            AND inv.inviteruserprofile = $2
            AND inv.invitationtype IN ('External', 'ExternalApp_share')
            AND inv.invitecontact IS NULL
        )
        OR
        (

            inv.invitercontact = $1
            AND inv.inviteruserprofile = $2
            AND inv.invitecontact IS NOT NULL
            AND inv.invitationtype <> 'ExternalPublic_provider'
            AND inv.invitationcode = 'Requested'
        )
        )
"""
    try:
        notification_row = await Database.fetchrow(notification_query, contact_id, userprofile_id)
        notification_count = int(notification_row["notification_count"]) if notification_row else 0

        invitation_row = await Database.fetchrow(invitation_query, contact_id, userprofile_id)
        invitation_count = int(invitation_row["invitation_count"]) if invitation_row else 0

        return notification_count + invitation_count
    except Exception as e:
        logger.error(f"Error fetching notification count for contact_id={contact_id}, userprofile_id={userprofile_id}: {str(e)}", exc_info=True)
        raise


async def get_notifications(
    receiver_userprofile_id: Optional[str],
    receiver_id: Optional[str],
    page_number: int = 0,
    limit: int = 20,
) -> list:
    """Return one page of a user's notifications merged with circle offers and feed posts, newest first, with sender badges.

    Args:
        receiver_userprofile_id: Receiver's user profile ID; offers and feeds merge only when set.
        receiver_id: Receiver's contact ID.
        page_number: Zero-based page number.
        limit: Items per page.

    Returns:
        List of notification dicts with Etown_providertype and Etown_badge_label.
    """
    page_offset = page_number * limit
    fetch_count = page_offset + limit

    query = """
        SELECT
    n.receivercontactid,
    n.receiveruserprofileid,
    n.sendercontactidname,
    etown_deleted_flag(n.senderuserprofileid)     AS sender_deleted,
    etown_deactivated_flag(n.senderuserprofileid) AS sender_deactivated,
    n.senderuserprofileid,
    n.notificationcontent,
    n.notificationstatus,
    n.notificationtype,
    n.role,
    n.statuscode,
    n.statusreason,
    n.createdon,
    n.notificationsid,

    up.userprofileid,
    up.contactid,
    up.role,
    up.rolename,

    c.contactid,
    c.profileimage,

    ps.address AS provider_address,
    ps.businessname,
    ps.businesscategory,
    ps.yearsinbusiness,
    ps.serviceimageurl,
    ps.email AS provider_email,
    ps.mobilenumber AS provider_mobile,
    ps.latitude,
    ps.longitude,
    ps.contactid AS provider_contactid,
    ps.userprofileid AS provider_userprofileid,
    ps.publicsearchprovider,
    ps.providerid,

    CASE
        WHEN up.role = 2 THEN c.profileimage
        WHEN up.role = 1 THEN ps.serviceimageurl
        ELSE NULL
    END AS Etown_imageurl

FROM etown_notifications n

INNER JOIN etown_userprofile up
    ON up.userprofileid = n.senderuserprofileid

INNER JOIN etown_contact c
    ON c.contactid = up.contactid

LEFT JOIN etown_provider ps
    ON ps.userprofileid = up.userprofileid

WHERE
    n.receivercontactid = $1
    AND n.receiveruserprofileid = $2

    AND NOT etown_is_deleted(n.senderuserprofileid)
    AND ps.disabled IS NOT TRUE

ORDER BY
    n.createdon DESC,
    c.contactid ASC,
    ps.providerid ASC,
    up.userprofileid ASC
"""

    params = [receiver_id, receiver_userprofile_id]

    params.append(fetch_count)
    query += f" LIMIT ${len(params)} OFFSET 0"

    items: list = []
    try:
        rows = await Database.fetch(query, *params)
        for row in rows:
            created = row["createdon"]
            items.append({
                "Etown_status": row["notificationstatus"],
                "Etown_rolename": row["rolename"],
                "Etown_role": row["role"],
                "Etown_notificationcontent": row["notificationcontent"],
                "Etown_imageurl": row["etown_imageurl"],
                "Etown_sendername": row["sendercontactidname"],
                "Etown_createdon": created.isoformat() + "Z" if created else None,
                "Etown_notification_type": row["notificationtype"],
                "Etown_notification_id": row["notificationsid"],
                "Etown_notification_status": row["statusreason"],
                "_sort_key": created.isoformat() if created else "",
                "_sender_userprofileid": row["senderuserprofileid"],
            })
    except Exception as e:
        logger.error(
            f"Error fetching notifications for receiver_id={receiver_id}, "
            f"receiver_userprofile_id={receiver_userprofile_id}: {str(e)}",
            exc_info=True,
        )
        raise

    if receiver_userprofile_id:
        try:
            from app.etown.offers.offers_utils import fetch_mycircle_offers
            offers = await fetch_mycircle_offers(
                contact_id=receiver_id,
                user_profile_id=receiver_userprofile_id,
                limit=fetch_count,
                offset=0,
            )
            def _clean(v):
                return v if v and v != "null" else None

            for offer in offers:
                if offer.get("Etown_is_expired"):
                    continue

                image = (
                    _clean(offer.get("Etown_thumbnailfileurl"))
                    or _clean(offer.get("Etown_offerfile1"))
                    or _clean(offer.get("Etown_businessprofileimageurl"))
                )
                businessname = _clean(offer.get("Etown_businessname"))
                content = (
                    f"You have received a new offer from {businessname}"
                    if businessname
                    else "You have received a new offer"
                )
                created = offer.get("Etown_createdon")
                items.append({
                    "Etown_status": offer.get("Etown_validity"),
                    "Etown_rolename": None,
                    "Etown_role": 1,
                    "Etown_notificationcontent": content,
                    "Etown_imageurl": image,
                    "Etown_sendername": offer.get("Etown_businessname"),
                    "Etown_createdon": (created + "Z") if (created and not created.endswith("Z")) else created,
                    "Etown_notification_type": "offer",
                    "Etown_notification_id": offer.get("Etown_offersid"),
                    "Etown_notification_status": None,
                    "_sort_key": created or "",
                    "_sender_userprofileid": offer.get("Etown_userprofileid"),
                })
        except Exception as e:
            logger.error(
                f"Error merging circle offers into notifications for "
                f"receiver_userprofile_id={receiver_userprofile_id}: {str(e)}",
                exc_info=True,
            )

        try:
            from app.etown.feed.feed_utils import Database as FeedDatabase
            feeds, _ = await FeedDatabase.get_feeds(
                current_contact_id=receiver_id or "",
                current_user_profile_id=receiver_userprofile_id,
                limit=fetch_count,
                offset=1,
                mode="My Circle",
                isMyProfile=False
            )

            for feed in feeds:
                if feed.get("userprofileid") == receiver_userprofile_id:
                    continue 
                image = (
                    _clean(feed.get("thumbnailfileurl"))
                    or _clean(feed.get("author_profileimage"))
                )
                author_name = _clean(feed.get("author_name")) or "someone"
                content = f"New post from {author_name}"
                
                created = feed.get("createdon")
                if created and hasattr(created, "isoformat"):
                    created = created.isoformat()

                items.append({
                    "Etown_status": None,
                    "Etown_rolename": None,
                    "Etown_role": feed.get("role"),
                    "Etown_notificationcontent": content,
                    "Etown_imageurl": image,
                    "Etown_sendername": author_name,
                    "Etown_createdon": (created + "Z") if (created and not created.endswith("Z")) else created,
                    "Etown_notification_type": "feed",
                    "Etown_notification_id": feed.get("feedid"),
                    "Etown_notification_status": None,
                    "_sort_key": created or "",
                    "_sender_userprofileid": feed.get("userprofileid"),
                })
        except Exception as e:
            logger.error(
                f"Error merging circle feeds into notifications for "
                f"receiver_userprofile_id={receiver_userprofile_id}: {str(e)}",
                exc_info=True,
            )

    items.sort(key=lambda x: x.get("_sort_key") or "", reverse=True)
    page_items = items[page_offset:page_offset + limit]
    badge_map = await resolve_provider_badges(
        item.get("_sender_userprofileid") for item in page_items
    )
    for item in page_items:
        item.pop("_sort_key", None)
        badge = badge_map.get(item.pop("_sender_userprofileid", None)) or {}
        item["Etown_providertype"] = badge.get("provider_type")
        item["Etown_badge_label"] = badge.get("badge_label")
    return page_items

async def update_notification_status(notification_id: str) -> None:
    """Mark one notification as Inactive and Read by notificationsid, re-raising on failure."""
    query = """
        UPDATE etown_notifications
        SET
            statecodename = 'Inactive',
            statecode = '1',
            statusreason = 'Read',
            statuscode = '2',
            modifieddate = NOW()
        WHERE
            notificationsid = $1;
    """
    try:
        await Database.execute(query, notification_id)
    except Exception as e:
        logger.error(f"Error updating notification status for notification_id={notification_id}: {str(e)}", exc_info=True)
        raise


async def clear_all_notifications(contact_id: Optional[str], userprofile_id: Optional[str]) -> None:
    """Mark all of a receiver's notifications as Inactive and Read, re-raising on failure."""
    query = """
        UPDATE etown_notifications
        SET
            statecodename = 'Inactive',
            statecode = '1',
            statusreason = 'Read',
            statuscode = '2',
            modifieddate = NOW()
        WHERE
            receivercontactid = $1
            AND receiveruserprofileid = $2;
    """
    try:
        await Database.execute(query, contact_id, userprofile_id)
    except Exception as e:
        logger.error(
            f"Error clearing all notifications for contact_id={contact_id}, "
            f"userprofile_id={userprofile_id}: {str(e)}",
            exc_info=True,
        )
        raise