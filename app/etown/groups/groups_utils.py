"""Groups Utilities - Postgres business logic for listing, creating, updating and deleting groups.

Domain layer behind /api/groups: lists a user's groups from etown_groups with their etown_groupitems members, role-based images and plan badges, and creates, updates or deletes a group and its members atomically, returning the ids the Dataverse mirror needs.

Features:
    - Group discovery: Retrieve groups by user credentials or specific group ID/unique ID
    - Member management: Access group member lists and details
    - Group metadata: Name, image, creation details and member count per group
    - Bulk operations: One query fetches the members of every listed group
    - Pagination support: limit/offset paging of a user's group list
    - Creation date sorting: Groups and members sorted by creation date (latest first)
    - Role-based image sourcing: Provider images from etown_provider, member images from etown_contact
    - Member role identification: Automatic detection of provider vs member status
    - Plan badges: Members carry the paid tier's badge label and Etown_providertype
    - Disabled exclusion: Disabled-provider members are dropped from groups and member counts
    - Group Creation: Inserts a group and members atomically, with blob image upload and ETGC id
    - Group Update: Updates group details, replaces the member list, or both
    - Group Deletion: Deletes a group and its members atomically, returning ids for the Dataverse mirror

Classes:
    Database(): Group read queries (list/metadata/bulk-member fetch) extending BaseDatabase

Functions:
    handle_groups(): Main handler for group requests with member details
    get_user_groups_organized(): Data organization utility for user groups
    _organize_groups_data(): Format response structure following collections pattern
    _upload_group_image(): Upload a base64/data-URL group image to blob storage
    _resolve_owner_names(): Resolve the owner's contact firstname and userprofile name
    _generate_unique_groupunicid(): Mint a unique ETGC-prefixed group id, re-rolling on collision
    _insert_group_items(): Insert group member rows on an open connection/transaction
    create_group_workflow(): Create a group + its members (POST /groups)
    update_group_workflow(): Update group details and/or member list (POST /groups/update)
    delete_group_workflow(): Delete a group and all its members (POST /groups/delete)

Database Methods:
    fetch_groups_list(): Get user's groups with metadata
    fetch_group_items_bulk(): Bulk fetch group members with provider join
    fetch_group_metadata(): Get single group details
    fetch_group_id_by_unique_id(): Resolve unique ID to group ID

Dependencies:
    - asyncio: Asynchronous operations for database queries
    - random: Candidate id generation inside _generate_unique_groupunicid()
    - app.db.client: Database connection pool access
    - app.core.config: settings.AZURE_BLOB_CONTAINER for the group image upload
    - app.core.logging: Structured logging for operations
    - app.utils.blob_storage: Base64/data-URL decoding and Azure Blob upload for group images
    - app.etown.plans.plans_utils: premium_display() and resolve_provider_badges() for member markers
    - collections.defaultdict: Efficient data organization
    - datetime: Time zone handling for timestamps
    - typing: Type annotations for better code clarity

Updates:
    16-Mar-2026: Initial creation following exact collections pattern
    24-Mar-2026: Sorted groups and items by creation date DESC; added role-based fields and Etown_role.
    13-Jul-2026: Added the create/update/delete write layer with blob upload and Dataverse sync.
    20-Aug-2026: Member markers report the paid tier's badge label; members carry Etown_providertype.
    02-Sep-2026: Disabled-provider members are excluded from groups and Etown_membercount.

Notes:
    Mirrors collections implementation for consistency with existing codebase.
    Uses bulk queries to avoid per-group member lookups.
    A direct groupid/groupuniqueid lookup returns the group without an ownership check.
    Role detection reads etown_userprofile.role (1=Provider, 2=Member), else etown_provider presence.
"""

import asyncio
import random
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Union, Tuple

from app.db.client import Database as BaseDatabase, with_retry
from app.core.config import settings
from app.core.logging import logger
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.utils.blob_storage import (
    normalize_container_name,
    to_bytes,
    upload_blob_bytes,
    current_timestamp_prefix,
    is_stored_media_url,
)


class Database(BaseDatabase):
    """Database operations for groups and group members."""

    @classmethod
    @with_retry()
    async def fetch_groups_list(cls, contact_id: str, user_profile_id: str, limit: int = 20, offset: int = 0) -> List[Dict]:
        """Fetch a page of groups owned by a contact and userprofile, newest first."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        query = """
            SELECT groupid, groupunicid, groupname, groupimageurl, user_contactid, user_contactidname, user_userprofileid, user_userprofileidname, createdon
            FROM etown_groups 
            WHERE user_contactid = $1 AND user_userprofileid = $2
            ORDER BY createdon DESC
            LIMIT $3 OFFSET $4
        """
        results = await cls.fetch(query, contact_id, user_profile_id, limit, offset)
        return [dict(r) for r in results]

    @classmethod
    @with_retry()
    async def fetch_group_items_bulk(cls, group_ids: List[str]) -> List[Dict]:
        """Fetch member rows with contact, role and provider details for the given groups, excluding disabled providers."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        if not group_ids: return []
        query = """
            SELECT
                gi.groupid, gi.groupitemsid, gi.userprofile_id, gi.username,
                gi.user_contactid, gi.user_contactidname, gi.user_userprofileid, gi.user_userprofileidname,
                gi.createdon as groupitem_createdon,
                c.contactid, c.firstname, c.email, c.mobilephone, c.city, c.statecode,
                c.latitude, c.longitude, c.createdon,
                CASE WHEN r.is_provider THEN 'Provider' ELSE 'Member' END AS member_role,
                CASE WHEN r.is_provider THEN COALESCE(p.provider_profileimage, c.profileimage)
                     ELSE c.profileimage END AS profileimage,
                CASE WHEN r.is_provider THEN COALESCE(p.publicsearchprovider, p.providertype)
                     END AS publicsearchprovider,
                CASE WHEN r.is_provider THEN p.providertype END AS providertype,
                c.verified AS user_verified
            FROM etown_groupitems gi
            LEFT JOIN etown_userprofile up ON gi.userprofile_id = up.userprofileid
            LEFT JOIN etown_contact c ON up.contactid = c.contactid
            LEFT JOIN LATERAL (
                SELECT providerid, serviceimageurl AS provider_profileimage,
                       publicsearchprovider, providertype
                FROM etown_provider
                WHERE userprofileid = up.userprofileid
                ORDER BY (providertype = 'etown_verified') DESC NULLS LAST,
                         (serviceimageurl IS NULL), serviceimageurl
                LIMIT 1
            ) p ON TRUE
            LEFT JOIN LATERAL (
                SELECT CASE
                    WHEN up.role = 1 THEN TRUE
                    WHEN up.role = 2 THEN FALSE
                    WHEN lower(up.rolename) = 'provider' THEN TRUE
                    WHEN lower(up.rolename) = 'member' THEN FALSE
                    ELSE p.providerid IS NOT NULL
                END AS is_provider
            ) r ON TRUE
            WHERE gi.groupid = ANY($1::text[])
              AND NOT EXISTS (
                  SELECT 1 FROM etown_provider dp
                   WHERE dp.disabled
                     AND dp.userprofileid = up.userprofileid
              )
            ORDER BY gi.createdon DESC
        """
        results = await cls.fetch(query, group_ids)
        return [dict(r) for r in results]

    @classmethod
    @with_retry()
    async def fetch_group_metadata(cls, group_id: str) -> Optional[Dict]:
        """Return one etown_groups row as a dict, or None."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        query = """
            SELECT groupid, groupunicid, groupname, groupimageurl, user_contactid, user_contactidname, user_userprofileid, user_userprofileidname
            FROM etown_groups WHERE groupid = $1
        """
        result = await cls.fetchrow(query, group_id)
        return dict(result) if result else None

    @classmethod
    @with_retry()
    async def fetch_group_id_by_unique_id(cls, group_unique_id: str) -> Optional[str]:
        """Return the groupid for a groupunicid, or None."""
        if cls._pool is None or cls._pool._closed: raise RuntimeError("Database pool not initialized")
        query = "SELECT groupid FROM etown_groups WHERE groupunicid = $1"
        result = await cls.fetchrow(query, group_unique_id)
        return result['groupid'] if result else None


async def handle_groups(
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None,
    groupid: Optional[str] = None,
    groupuniqueid: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """Return {"groups": [...]} for one group by id or unique id, or a page of the user's groups.

    Args:
        contact_id: Owner contact id, required for the list mode.
        user_profile_id: Owner userprofile id, required for the list mode.
        groupid: Fetch this single group.
        groupuniqueid: Resolve to a groupid and fetch that single group.
        limit: Page size for the list mode.
        offset: Rows to skip for the list mode.

    Returns:
        A dict with a groups list; empty when nothing matches or ids are missing.
    """
    logger.info(f"Handler: Groups. User={user_profile_id}, GroupID={groupid}, GroupUniqueID={groupuniqueid}")

    if groupuniqueid:
        resolved_group_id = await Database.fetch_group_id_by_unique_id(groupuniqueid)
        if resolved_group_id:
            groupid = resolved_group_id
        else:
            logger.warning(f"Group unique ID {groupuniqueid} not found.")
            return {"groups": []}

    if groupid:
        meta = await Database.fetch_group_metadata(groupid)
        if not meta: 
            return {"groups": []}
        
        items = await Database.fetch_group_items_bulk([groupid])
        result_list = _organize_groups_data(
            [meta], items, await resolve_provider_badges(i.get('userprofile_id') for i in items)
        )
        return {"groups": result_list}

    if not contact_id or not user_profile_id:
        logger.warning("Missing contact_id or user_profile_id for groups list")
        return {"groups": []}
    
    groups = await Database.fetch_groups_list(contact_id, user_profile_id, limit, offset)
    if not groups: 
        return {"groups": []}
    
    group_ids = [g['groupid'] for g in groups]
    members = await Database.fetch_group_items_bulk(group_ids)
    
    organized_groups = _organize_groups_data(
        groups, members, await resolve_provider_badges(m.get('userprofile_id') for m in members)
    )
    return {"groups": organized_groups}


async def get_user_groups_organized(
    contact_id: str, 
    user_profile_id: str, 
    limit: int = 20, 
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Return a page of the user's groups with their members and premium badges."""
    groups = await Database.fetch_groups_list(contact_id, user_profile_id, limit, offset)
    if not groups: 
        return []
    
    group_ids = [g['groupid'] for g in groups]
    members = await Database.fetch_group_items_bulk(group_ids)
    
    return _organize_groups_data(
        groups, members, await resolve_provider_badges(m.get('userprofile_id') for m in members)
    )


def _organize_groups_data(groups: List[Dict], members: List[Dict], badge_map: Dict[str, Dict[str, Optional[str]]] = None) -> List[Dict]:
    """Shape group rows and member rows into Etown_-keyed group dicts with nested members and premium labels."""
    members_map = defaultdict(list)
    for member in members:
        group_id = member.get('groupid')
        if group_id:
            groupitem_created = member.get('groupitem_createdon')
            contact_created = member.get('createdon')
            created = groupitem_created if groupitem_created else contact_created
            created_iso = created.isoformat() + 'Z' if isinstance(created, datetime) else str(created) if created else None

            member_data = {
                "Etown_groupid": group_id,
                "Etown_groupitemsid": member.get('groupitemsid'),
                "Etown_contact_id": member.get('contactid'),
                "Etown_userprofile_id": member.get('userprofile_id'),
                "Etown_username": member.get('username'),
                "Etown_user_contactid": member.get('user_contactid'),
                "Etown_user_contactidname": member.get('user_contactidname'),
                "Etown_user_userprofileid": member.get('user_userprofileid'),
                "Etown_user_userprofileidname": member.get('user_userprofileidname'),
                "Etown_firstname": member.get('username'),
                "Etown_email": member.get('email'),
                "Etown_mobilephone": member.get('mobilephone'),
                "Etown_profileimage": member.get('profileimage'),
                "Etown_role": member.get('member_role'),
                "Etown_publicsearchprovider": member.get('publicsearchprovider'),
                "Etown_providertype": premium_display(
                    member.get('providertype'),
                    ((badge_map or {}).get(member.get('userprofile_id')) or {}).get("premium_label"),
                ),
                "Etown_userverified": member.get('user_verified'),
                "Etown_city": member.get('city'),
                "Etown_statecode": member.get('statecode'),
                "Etown_latitude": str(member.get('latitude')) if member.get('latitude') else None,
                "Etown_longitude": str(member.get('longitude')) if member.get('longitude') else None,
                "Etown_createdon": created_iso
            }
            members_map[group_id].append(member_data)

    result = []
    for group in groups:
        group_id = group.get('groupid')
        group_members = members_map.get(group_id, [])
        
        result.append({
            "Etown_groupid": group_id,
            "Etown_groupunicid": group.get('groupunicid'),
            "Etown_groupname": group.get('groupname'),
            "Etown_groupimageurl": group.get('groupimageurl'),
            "Etown_user_contactid": group.get('user_contactid'),
            "Etown_user_contactidname": group.get('user_contactidname'),
            "Etown_user_userprofileid": group.get('user_userprofileid'),
            "Etown_user_userprofileidname": group.get('user_userprofileidname'),
            "Etown_membercount": len(group_members),
            "members": group_members
        })

    return result


async def _upload_group_image(user_contactid: str, groupimageurl: Optional[str]) -> Optional[str]:
    """Upload a base64 group image to Azure Blob under the owner's folder and return its stored path.

    Args:
        user_contactid: Owner contact id, used as the blob folder.
        groupimageurl: Base64/data-URL image, an already-stored blob path or URL, or null.

    Returns:
        The stored blob path, the input if already stored, or None when there is nothing to upload.

    Raises:
        RuntimeError: If AZURE_BLOB_CONTAINER is not configured.
    """
    if not groupimageurl or groupimageurl.strip().lower() == "null":
        return None
    if is_stored_media_url(groupimageurl):
        return groupimageurl.strip()
    container = normalize_container_name(settings.AZURE_BLOB_CONTAINER)
    if not container:
        raise RuntimeError("AZURE_BLOB_CONTAINER is not configured")
    decoded = to_bytes(groupimageurl)
    if not decoded:
        return None
    blob_name = f"{user_contactid}/{current_timestamp_prefix()}_GroupProfileImg.png"
    return await upload_blob_bytes(container, blob_name, decoded, "image/png")


async def _resolve_owner_names(user_contactid: str, user_userprofileid: str) -> Tuple[Optional[str], Optional[str]]:
    """Return the owner's contact firstname and userprofile name, each None when the row is missing."""
    row = await Database.fetchrow(
        """
        SELECT
            (SELECT firstname FROM etown_contact WHERE contactid = $1)   AS contactname,
            (SELECT name FROM etown_userprofile WHERE userprofileid = $2) AS profilename
        """,
        user_contactid, user_userprofileid,
    )
    if not row:
        return (None, None)
    return (row["contactname"], row["profilename"])


async def _generate_unique_groupunicid(max_attempts: int = 10) -> str:
    """Return a random ETGC-prefixed groupunicid not in etown_groups, appending a timestamp if every attempt collides."""
    for _ in range(max_attempts):
        candidate = f"ETGC{random.randint(1, 999999)}"
        exists = await Database.fetchval("SELECT 1 FROM etown_groups WHERE groupunicid = $1", candidate)
        if not exists:
            return candidate
    return f"ETGC{random.randint(1, 999999)}{current_timestamp_prefix()}"


async def _insert_group_items(
    con,
    groupid: str,
    user_contactid: str,
    user_contactidname: Optional[str],
    user_userprofileid: str,
    user_userprofileidname: Optional[str],
    groupitems: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Insert member rows on the caller's connection, skipping items without a userprofile_id, and return them with ids."""
    inserted: List[Dict[str, Any]] = []
    for item in groupitems:
        member_profile_id = item.get("userprofile_id")
        if not member_profile_id:
            continue
        row = await con.fetchrow(
            """
            INSERT INTO etown_groupitems (
                groupitemsid, groupid, userprofile_id, username,
                user_contactid, user_contactidname,
                user_userprofileid, user_userprofileidname, createdon
            ) VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5, $6, $7, NOW())
            RETURNING groupitemsid
            """,
            groupid, member_profile_id, item.get("name"),
            user_contactid, user_contactidname,
            user_userprofileid, user_userprofileidname,
        )
        inserted.append({
            "groupitemsid": row["groupitemsid"],
            "userprofile_id": member_profile_id,
            "name": item.get("name"),
        })
    return inserted


async def create_group_workflow(
    user_contactid: str,
    user_userprofileid: str,
    groupname: Optional[str],
    groupimageurl: Optional[str],
    groupitems: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Upload the image, mint a groupunicid, and insert the group and its members in one transaction.

    Args:
        user_contactid: Owner contact id.
        user_userprofileid: Owner userprofile id.
        groupname: Group name, optional.
        groupimageurl: Base64/data-URL image, stored URL, or null.
        groupitems: Members to add, each with userprofile_id and name.

    Returns:
        The created group and inserted members, with the fields the Dataverse mirror needs.
    """
    image_path, (contactname, profilename), groupunicid = await asyncio.gather(
        _upload_group_image(user_contactid, groupimageurl),
        _resolve_owner_names(user_contactid, user_userprofileid),
        _generate_unique_groupunicid(),
    )

    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool not initialized")

    async with Database._pool.acquire() as con:
        async with con.transaction():
            group_row = await con.fetchrow(
                """
                INSERT INTO etown_groups (
                    groupid, groupunicid, groupname, groupimageurl,
                    user_contactid, user_contactidname,
                    user_userprofileid, user_userprofileidname, createdon
                ) VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5, $6, $7, NOW())
                RETURNING groupid, groupname, groupunicid, groupimageurl
                """,
                groupunicid, groupname, image_path,
                user_contactid, contactname,
                user_userprofileid, profilename,
            )
            groupid = group_row["groupid"]
            inserted_items = await _insert_group_items(
                con, groupid, user_contactid, contactname,
                user_userprofileid, profilename, groupitems,
            )

    logger.info(f"[groups.create] Created group {groupid} ({groupunicid}) with {len(inserted_items)} member(s)")
    return {
        "groupid": groupid,
        "groupname": group_row["groupname"],
        "groupunicid": group_row["groupunicid"],
        "groupimageurl": group_row["groupimageurl"],
        "user_contactid": user_contactid,
        "user_userprofileid": user_userprofileid,
        "items": inserted_items,
        "items_replaced": True,
    }


async def update_group_workflow(
    groupid: str,
    groupupdatecondition: str,
    user_contactid: str,
    user_userprofileid: str,
    groupname: Optional[str],
    groupimageurl: Optional[str],
    groupitems: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Update a group's row, replace its member list, or both, depending on the update condition.

    Args:
        groupid: Group to update.
        groupupdatecondition: groupupdate, groupitemsupdate or completegroupupdate.
        user_contactid: Owner contact id, re-bound on the group row.
        user_userprofileid: Owner userprofile id, re-bound on the group row.
        groupname: New name; None keeps the existing one.
        groupimageurl: New image, stored URL, or null to keep the existing image.
        groupitems: Replacement members, each with userprofile_id and name.

    Returns:
        The updated group plus fields for the Dataverse mirror, or None if the group is missing.
    """
    current = await Database.fetch_group_metadata(groupid)
    if not current:
        return None

    updates_group = groupupdatecondition in ("groupupdate", "completegroupupdate")
    updates_items = groupupdatecondition in ("groupitemsupdate", "completegroupupdate")

    contactname, profilename = await _resolve_owner_names(user_contactid, user_userprofileid)
    image_path = current.get("groupimageurl")
    if updates_group:
        if not groupimageurl or groupimageurl.strip().lower() == "null":
            image_path = current.get("groupimageurl")
        else:
            image_path = await _upload_group_image(user_contactid, groupimageurl)

    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool not initialized")

    inserted_items: List[Dict[str, Any]] = []
    result_groupname = current.get("groupname")
    async with Database._pool.acquire() as con:
        async with con.transaction():
            if updates_group:
                updated = await con.fetchrow(
                    """
                    UPDATE etown_groups
                    SET groupname = COALESCE($1, groupname),
                        groupimageurl = $2,
                        user_contactid = $3,
                        user_contactidname = COALESCE($4, user_contactidname),
                        user_userprofileid = $5,
                        user_userprofileidname = COALESCE($6, user_userprofileidname),
                        modifieddate = NOW()
                    WHERE groupid = $7
                    RETURNING groupid, groupname, groupunicid, groupimageurl
                    """,
                    groupname, image_path, user_contactid, contactname,
                    user_userprofileid, profilename, groupid,
                )
                if updated:
                    result_groupname = updated["groupname"]
                    image_path = updated["groupimageurl"]

            if updates_items:
                await con.execute("DELETE FROM etown_groupitems WHERE groupid = $1", groupid)
                inserted_items = await _insert_group_items(
                    con, groupid, user_contactid, contactname,
                    user_userprofileid, profilename, groupitems,
                )

    logger.info(
        f"[groups.update] Updated group {groupid} "
        f"(condition={groupupdatecondition}, group={updates_group}, items={updates_items})"
    )
    return {
        "groupid": groupid,
        "groupname": result_groupname,
        "groupunicid": current.get("groupunicid"),
        "groupimageurl": image_path,
        "user_contactid": user_contactid,
        "user_userprofileid": user_userprofileid,
        "condition": groupupdatecondition,
        "items": inserted_items,
        "items_replaced": updates_items,
    }


async def delete_group_workflow(groupid: str) -> Dict[str, Any]:
    """Delete a group and its member rows in one transaction and return the ids for the Dataverse mirror.

    Returns:
        Dict with groupid, deleted item_ids and an existed flag.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool not initialized")

    async with Database._pool.acquire() as con:
        async with con.transaction():
            item_rows = await con.fetch(
                "SELECT groupitemsid FROM etown_groupitems WHERE groupid = $1", groupid
            )
            item_ids = [r["groupitemsid"] for r in item_rows]
            await con.execute("DELETE FROM etown_groupitems WHERE groupid = $1", groupid)
            deleted = await con.execute("DELETE FROM etown_groups WHERE groupid = $1", groupid)

    existed = deleted != "DELETE 0"
    logger.info(f"[groups.delete] Deleted group {groupid} (existed={existed}, members={len(item_ids)})")
    return {"groupid": groupid, "item_ids": item_ids, "existed": existed}
