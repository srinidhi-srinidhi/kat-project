"""MyTown Dataverse Sync - mirrors collections, favourite towns, towns, provider links and community posts to the CRM.

Background Dataverse mirror for MyTown writes: upserts and deletes collections and items, favourite towns, town profiles, images and image reviews, provider-town links, community needs, updates, events, likes and comments, and categories and category pins; Postgres stays authoritative and every function returns a bool and logs instead of raising.

Features:
    - Collection Sync: upsert/delete collections and items, cascading deletes to child items
    - Favourite Town Sync: upsert a user's favourite/unfavourite town record (ets_favouritetowns)
    - Town Profile Sync: upserts ets_townprofiles records after inhouse town creation
    - Town Card Image Sync: PATCHes ets_townimage alone, leaving other CRM town fields intact
    - Town Image Sync: upserts uploaded town images (ets_townimages) with caption and lookups
    - Town Image Verification Sync: PATCHes ets_verification alone after moderation
    - Provider-Town Link Sync: upserts and deletes ets_etownproviderses provider-town links
    - Community Post Sync: one idempotent whole-record PATCH per need, update or event onto ets_need, ets_update or ets_event
    - Community View Count Sync: a counted view PATCHes ets_viewcount alone onto an existing ets_update or ets_event
    - Community Like Sync: one PATCH per like row onto ets_communitylike
    - Community Comment Sync: add and edit PATCH the comment, a delete PATCHes ets_deleted alone, onto ets_communitycomment
    - Community Text Capped: strings trimmed to each attribute's MaxLength, with the cut logged
    - Community Bind Fallback: a PATCH refused for a missing lookup target is retried without binds
    - Town Review Sync: one whole-record PATCH per town image request onto ets_townreview, ets_review as Pending 0, Approved 1, Rejected 2
    - Category Sync: one PATCH per etown_category row onto ets_category, categoryname as ets_name
    - Category Pin Sync: one PATCH per pin or unpin onto ets_categorypin, ets_ispinned as the text "true" or "false", after upserting its category

Community Need Mirror:
    Field mapping, taken from the live ``ets_need`` metadata on 02-Sep-2026
    (``EntityDefinitions(LogicalName='ets_need')/Attributes``) rather than guessed;
    ``budget -> ets_budget String(100)`` and ``location_name -> ets_locationname String(100)``
    were added on 05-Oct-2026:

    needid         -> ets_needid            Uniqueidentifier  the record id, URL only
    title          -> ets_title             String(200)       also copied to ets_name (850)
    description    -> ets_description       String(2000)
    category       -> ets_category          String(100)
    townname       -> ets_townname          String(100)
    latitude       -> ets_latitude          Decimal(6)
    longitude      -> ets_longitude         Decimal(6)
    needed_at      -> ets_neededat          DateTime (UserLocal)
    attachments    -> ets_attachments       Memo(2000)        compact JSON, see below
    status         -> ets_status            String(100)
    need_status    -> ets_needstatus        String            open or closed; added 06-Oct-2026
    deleted       -> ets_deleted           Picklist          0 = False, 1 = True
    townprofileid  -> ets_TownProfileID     Lookup -> ets_townprofile
    userprofileid  -> ets_UserProfileID     Lookup -> ets_userprofile
    contactid      -> ets_ContactID         Lookup -> contact

    ``ets_needid`` is IsValidForUpdate=False, so it travels in the URL and is deliberately absent
    from the payload; sending it would 400 every update. ``created_at``/``updated_at`` have no
    writable counterpart - Dataverse stamps its own ``createdon``/``modifiedon`` - so they are
    not sent either.

    ``_MAX_LEN`` below is a copy of the live MaxLengths, because the mirror trims before it
    PATCHes rather than after a 400. It has to be re-copied whenever a Dataverse column changes
    width or the mirror silently keeps trimming to the old ceiling, so a TRUNCATED line means
    either a genuinely oversized value or that this map has gone stale. ``ets_title`` (200) and
    ``ets_description`` (2000) were widened on 02-Sep-2026 to clear the API's own 200/300
    ceilings, so nothing of normal length is trimmed on the way into the CRM.

    The three lookups are bound by navigation property name (``ets_TownProfileID`` and friends),
    not by writing the raw attribute: a bare lookup attribute is dropped silently and the PATCH
    still reports success, which is the failure mode that hides broken links for months. A bind
    whose target does not exist in Dataverse yet - common, because the CRM->PG mirror lags on
    contact and userprofile rows - 404s the whole PATCH, so a failed attempt is retried once
    with the binds stripped. Landing the need with no links beats losing the need.

    Attachments are mirrored as compact JSON with one object per file: ``image_url`` or
    ``video_url`` (``file_url`` for an older PDF or Word file) and ``thumbnail_url`` when a
    thumbnail was sent, each the relative blob path ('/container/<upid>/needs/<file>') as
    Postgres stores it, never the storage account's host, so a storage-account move changes
    nothing here. Five videos with thumbnails fit the 2000-character Memo comfortably.

Community Update, Event, Like And Comment Mirrors:
    Mapped from the live metadata on 05-Oct-2026. Updates and events carry the need's shared
    columns (title, description, category, townname, location_name, coordinates, needed-at date,
    attachments, status, deleted and the three lookups) with these differences:

    - ``ets_title`` is String(100) on both, so a title over 100 characters is trimmed there; ``ets_name`` (850) keeps it whole.
    - ``ets_description`` is Memo(2000) on both; status, attachments and the date are mirrored as on a need.
    - ``ets_event`` names its date ``ets_needat``; ``ets_startdate``/``ets_enddate`` are DateOnly and ``ets_starttime``/``ets_endtime`` are UserLocal DateTimes, sent as the event's date plus clock time in IST (an end time with no end date falls on the start date). ``entry_info`` goes to ``ets_eventinfo`` Memo(4000).
    - ``view_count`` goes to ``ets_viewcount``, a String(100) on both, as decimal text; a need does not carry it.
    - ``ets_review`` (Pending 0, Approved 1, Rejected 2) is left null by the API and set by an admin in Dataverse, so it is never sent and a re-PATCH on publish cannot undo an approval.

    ``ets_communitylike`` takes ``ets_postid``, ``ets_posttype``, ``ets_likemode`` (Integer) and
    ``ets_likemodename``; ``ets_communitycomment`` takes ``ets_postid``, ``ets_posttype``,
    ``ets_comment`` (Memo 2000) and the ``ets_deleted`` Picklist. Both bind ``ets_UserProfileID``
    and ``ets_ContactID``; neither has a lookup to the post itself, only the id as text.

Classes:
    No classes defined (module uses function-based sync utilities)

Functions:
    sync_collection_to_dataverse(): Upsert a collection record in Dataverse (ets_collections)
    sync_collection_delete_from_dataverse(): Delete a collection and its items from Dataverse
    sync_collectionitem_to_dataverse(): Upsert a collection-item record in Dataverse (ets_collectionitemses)
    sync_favouritetown_to_dataverse(): Upsert a favourite-town record in Dataverse (ets_favouritetowns)
    sync_town_profile_to_dataverse(): Upsert a town profile record in Dataverse (ets_townprofiles)
    sync_town_profile_image_to_dataverse(): PATCH only ets_townimage after a town card is regenerated
    sync_town_image_to_dataverse(): Upsert a town image record in Dataverse (ets_townimages)
    sync_town_image_verification_to_dataverse(): PATCH only ets_verification after moderation
    sync_etownprovider_to_dataverse(): Upsert a provider-town link record in Dataverse (ets_etownproviderses)
    sync_etownprovider_delete_from_dataverse(): Delete one provider-town link record from Dataverse
    _to_int(): Best-effort cast of a text value to int for Dataverse Int32 columns
    _verification_to_int(): Map the town-image verification text to its Dataverse choice value
    _favourite_to_int(): Map the Postgres isfavourite text to the Dataverse ets_isfavourite choice value
    sync_post_to_dataverse(): PATCH one community need, update or event onto its CRM record
    sync_post_viewcounts_to_dataverse(): PATCH only ets_viewcount on each update or event whose view was counted
    sync_community_like_to_dataverse(): PATCH one community like onto its ets_communitylike record
    sync_community_comment_to_dataverse(): PATCH one community comment onto its ets_communitycomment record
    sync_town_review_to_dataverse(): PATCH one town image request onto its ets_townreview record
    sync_category_to_dataverse(): PATCH one etown_category row onto its ets_category record
    sync_category_pin_to_dataverse(): PATCH one etown_category_pin row onto its ets_categorypin record
    _as_datetime_str(): Render a needed_at value as the ISO 8601 form ets_neededat takes
    _as_date(): Read a date or YYYY-MM-DD string for a DateOnly column
    _as_time(): Read a time or HH:MM string for an event's start or end time
    _event_moment(): Combine an event date and clock time into an IST ISO 8601 string
    _as_float(): Best-effort cast of a coordinate for a Dataverse Decimal column
    _attachments_json(): Serialise a post's attachment list for the ets_attachments Memo
    _viewcount_text(): Render a post's view_count as the text ets_viewcount stores
    _deleted_choice(): Map the Postgres deleted text column onto the ets_deleted Picklist
    _capped(): Trim every over-long string in a community payload to its MaxLength, logging what was cut
    _bind_people(): Add the userprofile and contact lookup binds to a community payload
    _patch_with_bind_fallback(): PATCH a community record, retrying once without lookup binds

Dependencies:
    - uuid: Validating that a townprofileid is a GUID before addressing a CRM record
    - httpx: Async HTTP client used to list a collection's child items before cascading the delete
    - json: Serialising a need's attachment list into the ets_attachments Memo
    - datetime: Reading a naive needed_at as UTC, and combining an event's date and time
    - app.utils.ist_clock: IST, the zone an event's start and end clock times are given in
    - app.utils.blob_storage: to_relative_blob_url() keeping the attachment paths sent to ets_attachments host-free
    - app.core.logging: Centralized application logging
    - app.etown.provider.provider_syncing: Shared Dataverse token, context builder, upsert and delete helpers

Updates:
    06-Jul-2026: Initial creation for inhouse provider town assignment Dataverse sync
    13-Jul-2026: Added sync_collectionitem_to_dataverse for the CrudCollectionItems migration
    14-Jul-2026: Added sync_favouritetown_to_dataverse for the AddUpdateFavouriteTown migration
    18-Aug-2026: Added sync_etownprovider_delete_from_dataverse so a dropped coverage town clears the CRM link.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    28-Aug-2026: Added upload mirroring for etown_townimages.
    31-Aug-2026: Town-image mirror binds its three lookups and maps the new Rejected verification choice.
    01-Sep-2026: Added targeted ets_townimage and ets_verification mirrors.
    11-Sep-2026: Added ets_caption to uploaded town-image Dataverse upserts.
    15-Sep-2026: Absorbed needs_syncing's ets_need mirror for community needs.
    05-Oct-2026: Mirrored community updates, events, likes and comments; needs now send budget and location name.
    06-Oct-2026: Needs send need_status as ets_needstatus.
    07-Oct-2026: ets_attachments carries relative image_url or video_url and thumbnail_url paths, without filename.
    08-Oct-2026: Added sync_town_review_to_dataverse for town image requests and their review status.
    20-Oct-2026: Updates and events send view_count as ets_viewcount; added sync_post_viewcounts_to_dataverse; added sync_category_to_dataverse and sync_category_pin_to_dataverse.
"""

import json
import uuid
import httpx
from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import logger
from app.utils.ist_clock import IST
from app.utils.blob_storage import to_relative_blob_url
from app.etown.provider.provider_syncing import (
    get_dataverse_token,
    _build_dataverse_context,
    _upsert_record,
    _delete_record,
)

_ENTITY_TOWNPROFILES = "ets_townprofiles"
_ENTITY_TOWNIMAGES = "ets_townimages"
_ENTITY_ETOWNPROVIDERS = "ets_etownproviderses"
_ENTITY_PROVIDERS = "ets_providers"
_ENTITY_COLLECTIONS = "ets_collections"
_ENTITY_COLLECTIONITEMS = "ets_collectionitemses"
_ENTITY_FAVOURITETOWNS = "ets_favouritetowns"
_ENTITY_CONTACTS = "contacts"
_ENTITY_USERPROFILES = "ets_userprofiles"


def _to_int(value: Any) -> Optional[int]:
    """Return a value cast to int, or None when it is None or not an integer."""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        logger.debug("mytown mirror: %r is not an integer; storing null", value)
        return None


def _verification_to_int(value: Any) -> Optional[int]:
    """Return the Dataverse verification choice (pending 0, approved 1, rejected 2) for a text or numeric value."""
    if value is None:
        return None
    values = {"pending": 0, "approved": 1, "rejected": 2}
    normalized = str(value).strip().lower()
    if normalized in values:
        return values[normalized]
    return _to_int(value)


def _favourite_to_int(value: Any) -> Optional[int]:
    """Return 0 for 'unfavourite' in any case, None for None, and 1 for anything else."""
    if value is None:
        return None
    return 0 if str(value).strip().lower() == "unfavourite" else 1


async def sync_collection_to_dataverse(
    *,
    collectionid: str,
    collectionname: Optional[str] = None,
    collectiontype: Optional[str] = None,
    collectionunicid: Optional[str] = None,
    ismytown: Optional[str] = None,
    usercontactid: Optional[str] = None,
    useruserprofileid: Optional[str] = None,
) -> bool:
    """Upsert an ets_collections record keyed by collectionid, sending only non-None fields.

    Args:
        collectionid: Collection UUID, also the Dataverse record key.
        collectionname: Display name of the collection.
        collectiontype: Collection type, cast to int.
        collectionunicid: Human-readable unique code such as ETCL123456.
        ismytown: MyTown flag, cast to int.
        usercontactid: Owning contact UUID, bound as a lookup.
        useruserprofileid: Owning user-profile UUID, bound as a lookup.

    Returns:
        True on success, False on failure.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_COLLECTION_SYNC | COLLECTION | FAILED_TOKEN | id=%s | %s", collectionid, exc, exc_info=True)
        return False

    base_url, headers = _build_dataverse_context(access_token)

    payload = {
        "ets_collectionname": collectionname,
        "ets_collectiontype": _to_int(collectiontype),
        "ets_collectionunicid": collectionunicid,
        "ets_ismytown": _to_int(ismytown),
        "ets_usercontactid@odata.bind": f"/{_ENTITY_CONTACTS}({usercontactid})",
        "ets_useruserprofileid@odata.bind": f"/{_ENTITY_USERPROFILES}({useruserprofileid})",
    }

    url = f"{base_url}/{_ENTITY_COLLECTIONS}({collectionid})"
    return await _upsert_record(headers, url, payload, "COLLECTION", collectionid)


async def sync_collection_delete_from_dataverse(collectionid: str) -> None:
    """Delete a collection's ets_collectionitemses rows and then its ets_collections record from Dataverse."""
    if not collectionid:
        logger.warning("DATAVERSE_COLLECTION_SYNC | DELETE | SKIPPED | collectionid is None")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_COLLECTION_SYNC | DELETE | FAILED_TOKEN | id=%s | %s", collectionid, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    try:
        filter_url = (
            f"{base_url}/{_ENTITY_COLLECTIONITEMS}"
            f"?$filter=_ets_collectionid_value eq {collectionid}"
            f"&$select=ets_collectionitemsid"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(filter_url, headers=headers)
        if resp.status_code == 200:
            item_ids = [r["ets_collectionitemsid"] for r in resp.json().get("value", [])]
            for item_id in item_ids:
                await _delete_record(
                    headers,
                    f"{base_url}/{_ENTITY_COLLECTIONITEMS}({item_id})",
                    "COLLECTIONITEM",
                    item_id,
                )
        else:
            logger.error(
                "DATAVERSE_COLLECTION_SYNC | COLLECTIONITEM_QUERY | FAILED | collectionid=%s | status=%s | body=%s",
                collectionid, resp.status_code, resp.text,
            )
    except Exception as exc:
        logger.error(
            "DATAVERSE_COLLECTION_SYNC | COLLECTIONITEM_QUERY | ERROR | collectionid=%s | %s",
            collectionid, exc, exc_info=True,
        )

    await _delete_record(
        headers,
        f"{base_url}/{_ENTITY_COLLECTIONS}({collectionid})",
        "COLLECTION",
        collectionid,
    )


async def sync_collectionitem_to_dataverse(
    *,
    collectionitemsid: str,
    isfavourite: Optional[str] = None,
    collectionid: Optional[str] = None,
    providerid: Optional[str] = None,
    usercontactid: Optional[str] = None,
    useruserprofileid: Optional[str] = None,
) -> bool:
    """Upsert an ets_collectionitemses record keyed by collectionitemsid, sending only non-None fields.

    Args:
        collectionitemsid: Collection item UUID, also the Dataverse record key.
        isfavourite: 'favourite' or 'unfavourite', mapped to the choice value 1 or 0.
        collectionid: Parent collection UUID, bound as a lookup.
        providerid: Provider UUID, bound as a lookup.
        usercontactid: Owning contact UUID, bound as a lookup.
        useruserprofileid: Owning user-profile UUID, bound as a lookup.

    Returns:
        True on success, False on failure.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_COLLECTIONITEM_SYNC | COLLECTIONITEM | FAILED_TOKEN | id=%s | %s", collectionitemsid, exc, exc_info=True)
        return False

    base_url, headers = _build_dataverse_context(access_token)

    payload = {
        "ets_isfavourite": _favourite_to_int(isfavourite),
        "ets_collectionid@odata.bind": f"/{_ENTITY_COLLECTIONS}({collectionid})",
        "ets_providerid@odata.bind": f"/{_ENTITY_PROVIDERS}({providerid})",
        "ets_usercontactid@odata.bind": f"/{_ENTITY_CONTACTS}({usercontactid})",
        "ets_useruserprofileid@odata.bind": f"/{_ENTITY_USERPROFILES}({useruserprofileid})",
    }

    url = f"{base_url}/{_ENTITY_COLLECTIONITEMS}({collectionitemsid})"
    return await _upsert_record(headers, url, payload, "COLLECTIONITEM", collectionitemsid)


async def sync_favouritetown_to_dataverse(
    *,
    favouritetownid: str,
    isfavourite: Optional[str] = None,
    townname: Optional[str] = None,
    usercontactid: Optional[str] = None,
    useruserprofileid: Optional[str] = None,
) -> bool:
    """Upsert an ets_favouritetowns record keyed by favouritetownid, sending only non-None fields.

    Args:
        favouritetownid: Favourite-town UUID, also the Dataverse record key.
        isfavourite: 'Favourite' or 'unfavourite', mapped to the choice value 1 or 0.
        townname: Town name.
        usercontactid: Owning contact UUID, bound as a lookup.
        useruserprofileid: Owning user-profile UUID, bound as a lookup.

    Returns:
        True on success, False on failure.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_FAVOURITETOWN_SYNC | FAVOURITETOWN | FAILED_TOKEN | id=%s | %s", favouritetownid, exc, exc_info=True)
        return False

    base_url, headers = _build_dataverse_context(access_token)

    payload = {
        "ets_isfavourite": _favourite_to_int(isfavourite),
        "ets_townname": townname,
        "ets_usercontactid@odata.bind": f"/{_ENTITY_CONTACTS}({usercontactid})",
        "ets_useruserprofileid@odata.bind": f"/{_ENTITY_USERPROFILES}({useruserprofileid})",
    }

    url = f"{base_url}/{_ENTITY_FAVOURITETOWNS}({favouritetownid})"
    return await _upsert_record(headers, url, payload, "FAVOURITETOWN", favouritetownid)


async def sync_town_profile_to_dataverse(
    *,
    townprofileid: str,
    townname: str,
    cityname: str,
    selfreferencingid: str,
    towndescription: Optional[str],
    townlatitude: float,
    townlongitude: float,
    townimage: Optional[str],
    townstatus: str = "2",
) -> bool:
    """Upsert an ets_townprofiles record keyed by townprofileid, sending coordinates as text.

    Args:
        townprofileid: Town profile UUID, also the Dataverse record key.
        townname: Name of the town.
        cityname: Name of the city this town belongs to.
        selfreferencingid: The admin-level-3 root townprofileid, or its own id for a root.
        towndescription: Town description.
        townlatitude: Latitude of the town center.
        townlongitude: Longitude of the town center.
        townimage: Optional image URL for the town.
        townstatus: Status code string, cast to int, defaulting to 2.

    Returns:
        True on success, False on failure.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_TOWN_SYNC | TOWN_PROFILE | FAILED_TOKEN | id=%s | %s", townprofileid, exc, exc_info=True)
        return False

    base_url, headers = _build_dataverse_context(access_token)

    payload = {
        "ets_townname": townname,
        "ets_cityname": cityname,
        "ets_selfreferencingid": selfreferencingid,
        "ets_towndescription": towndescription,
        "ets_townlatitude": str(townlatitude) if townlatitude is not None else None,
        "ets_townlongitude": str(townlongitude) if townlongitude is not None else None,
        "ets_townimage": townimage,
        "ets_townstatus": int(townstatus) if townstatus else 2,
    }

    url = f"{base_url}/{_ENTITY_TOWNPROFILES}({townprofileid})"
    return await _upsert_record(headers, url, payload, "TOWN_PROFILE", townprofileid)


async def sync_town_profile_image_to_dataverse(townprofileid: str, townimage: str) -> bool:
    """PATCH only ets_townimage on a town profile, skipping non-GUID ids, and return True on success."""
    try:
        uuid.UUID(str(townprofileid))
    except (ValueError, AttributeError, TypeError):
        logger.info(
            "DATAVERSE_TOWN_SYNC | TOWN_PROFILE_IMAGE | SKIPPED_NON_GUID | id=%s", townprofileid,
        )
        return False

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_TOWN_SYNC | TOWN_PROFILE_IMAGE | FAILED_TOKEN | id=%s | %s",
            townprofileid, exc, exc_info=True,
        )
        return False

    base_url, headers = _build_dataverse_context(access_token)
    url = f"{base_url}/{_ENTITY_TOWNPROFILES}({townprofileid})"
    return await _upsert_record(headers, url, {"ets_townimage": townimage}, "TOWN_PROFILE_IMAGE", townprofileid)


async def sync_town_image_to_dataverse(
    *,
    townimageid: str,
    townprofileid: Optional[str] = None,
    townimageurl: Optional[str] = None,
    userprofileid: Optional[str] = None,
    contactid: Optional[str] = None,
    verification: Optional[str] = None,
    name: Optional[str] = None,
    caption: Optional[str] = None,
) -> bool:
    """Upsert an ets_townimages record keyed by townimageid, with town, profile and contact lookups."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_TOWN_SYNC | TOWN_IMAGE | FAILED_TOKEN | id=%s | %s", townimageid, exc, exc_info=True)
        return False

    base_url, headers = _build_dataverse_context(access_token)

    payload = {
        "ets_townimageurl": townimageurl,
        "ets_verification": _verification_to_int(verification),
        "ets_name": name,
        "ets_caption": caption,
        "ets_TownProfileID@odata.bind": f"/{_ENTITY_TOWNPROFILES}({townprofileid})",
        "ets_UserProfileID@odata.bind": f"/{_ENTITY_USERPROFILES}({userprofileid})",
        "ets_ContactID@odata.bind": f"/{_ENTITY_CONTACTS}({contactid})",
    }

    url = f"{base_url}/{_ENTITY_TOWNIMAGES}({townimageid})"
    return await _upsert_record(headers, url, payload, "TOWN_IMAGE", townimageid)


async def sync_town_image_verification_to_dataverse(townimageid: str, verification: str) -> bool:
    """PATCH only ets_verification on a town image, skipping non-GUID ids, and return True on success."""
    try:
        uuid.UUID(str(townimageid))
    except (ValueError, AttributeError, TypeError):
        logger.info("DATAVERSE_TOWN_SYNC | TOWN_IMAGE_VERIFICATION | SKIPPED_NON_GUID | id=%s", townimageid)
        return False

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error(
            "DATAVERSE_TOWN_SYNC | TOWN_IMAGE_VERIFICATION | FAILED_TOKEN | id=%s | %s",
            townimageid, exc, exc_info=True,
        )
        return False

    base_url, headers = _build_dataverse_context(access_token)
    url = f"{base_url}/{_ENTITY_TOWNIMAGES}({townimageid})"
    payload = {"ets_verification": _verification_to_int(verification)}
    return await _upsert_record(headers, url, payload, "TOWN_IMAGE_VERIFICATION", townimageid)


async def sync_etownprovider_to_dataverse(
    *,
    etownproviderid: str,
    provider_id: str,
    business_name: str,
    townprofileid: str,
) -> bool:
    """Upsert an ets_etownproviderses provider-town link with its provider and town lookups.

    Args:
        etownproviderid: Link record UUID, also the Dataverse record key.
        provider_id: UUID of the provider in ets_providers.
        business_name: Provider business name stored in ets_name.
        townprofileid: UUID of the town profile in ets_townprofiles.

    Returns:
        True on success, False on failure.
    """
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_TOWN_SYNC | ETOWNPROVIDER | FAILED_TOKEN | id=%s | %s", etownproviderid, exc, exc_info=True)
        return False

    base_url, headers = _build_dataverse_context(access_token)

    payload = {
        "ets_name": business_name,
        "ets_providerid@odata.bind": f"/{_ENTITY_PROVIDERS}({provider_id})",
        "ets_townprofileid@odata.bind": f"/{_ENTITY_TOWNPROFILES}({townprofileid})",
    }

    url = f"{base_url}/{_ENTITY_ETOWNPROVIDERS}({etownproviderid})"
    return await _upsert_record(headers, url, payload, "ETOWNPROVIDER", etownproviderid)


async def sync_etownprovider_delete_from_dataverse(etownproviderid: str) -> None:
    """Delete one ets_etownproviderses provider-town link record from Dataverse."""
    if not etownproviderid:
        logger.warning("DATAVERSE_TOWN_SYNC | ETOWNPROVIDER | DELETE_SKIPPED | id is None")
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_TOWN_SYNC | ETOWNPROVIDER | FAILED_TOKEN | id=%s | %s", etownproviderid, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)

    await _delete_record(
        headers,
        f"{base_url}/{_ENTITY_ETOWNPROVIDERS}({etownproviderid})",
        "ETOWNPROVIDER",
        etownproviderid,
    )


_POST_ENTITIES: Dict[str, Tuple[str, str]] = {
    "need": ("ets_needs", "needid"),
    "update": ("ets_updates", "updateid"),
    "event": ("ets_events", "eventid"),
}
_VIEWCOUNT_TYPES = ("update", "event")
_ENTITY_COMMUNITYLIKES = "ets_communitylikes"
_ENTITY_COMMUNITYCOMMENTS = "ets_communitycomments"
_ENTITY_TOWNREVIEWS = "ets_townreviews"
_ENTITY_CATEGORIES = "ets_categories"
_ENTITY_CATEGORYPINS = "ets_categorypins"
_LOG = "DATAVERSE_COMMUNITY"

_DELETED_TRUE = 1
_DELETED_FALSE = 0

_MAX_LEN: Dict[str, Dict[str, int]] = {
    "need": {
        "ets_attachments": 2000,
        "ets_budget": 100,
        "ets_category": 100,
        "ets_description": 2000,
        "ets_locationname": 100,
        "ets_name": 850,
        "ets_status": 100,
        "ets_title": 200,
        "ets_townname": 100,
    },
    "update": {
        "ets_attachments": 2000,
        "ets_category": 100,
        "ets_description": 2000,
        "ets_locationname": 100,
        "ets_name": 850,
        "ets_status": 100,
        "ets_title": 100,
        "ets_townname": 100,
        "ets_viewcount": 100,
    },
    "event": {
        "ets_attachments": 2000,
        "ets_category": 100,
        "ets_description": 2000,
        "ets_eventinfo": 4000,
        "ets_locationname": 100,
        "ets_name": 850,
        "ets_organizername": 100,
        "ets_organizernumber": 100,
        "ets_status": 100,
        "ets_title": 100,
        "ets_townname": 100,
        "ets_venue": 100,
        "ets_viewcount": 100,
    },
    "like": {
        "ets_likemodename": 100,
        "ets_name": 850,
        "ets_postid": 100,
        "ets_posttype": 100,
    },
    "comment": {
        "ets_comment": 2000,
        "ets_name": 850,
        "ets_postid": 100,
        "ets_posttype": 100,
    },
    "townreview": {
        "ets_contactname": 100,
        "ets_description": 5000,
        "ets_imageurl": 2000,
        "ets_name": 850,
        "ets_townname": 100,
    },
    "category": {
        "ets_categoryimage": 500,
        "ets_displayname": 100,
        "ets_name": 850,
    },
    "categorypin": {
        "ets_areaname": 100,
        "ets_cityname": 100,
        "ets_ispinned": 100,
        "ets_name": 850,
    },
}

_BIND_KEYS = (
    "ets_TownProfileID@odata.bind",
    "ets_UserProfileID@odata.bind",
    "ets_ContactID@odata.bind",
    "ets_CategoryID@odata.bind",
)


def _as_datetime_str(value: Any) -> Optional[str]:
    """Return a datetime as ISO 8601, treating naive values as UTC, a string stripped, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.isoformat()
    text = str(value).strip()
    return text or None


def _as_date(value: Any) -> Optional[date]:
    """Return a date from a date or YYYY-MM-DD string, or None when empty or unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        logger.debug("%s | %r is not a date; storing null", _LOG, value)
        return None


def _as_time(value: Any) -> Optional[time]:
    """Return a time from a time or HH:MM[:SS] string, or None when empty or unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(str(value).strip())
    except ValueError:
        logger.debug("%s | %r is not a time; storing null", _LOG, value)
        return None


def _event_moment(day: Any, clock: Any) -> Optional[str]:
    """Return an event date plus clock time as an IST ISO 8601 string, or None when either part is missing."""
    on, at = _as_date(day), _as_time(clock)
    if on is None or at is None:
        return None
    return datetime.combine(on, at.replace(tzinfo=None), tzinfo=IST).isoformat()


def _as_float(value: Any) -> Optional[float]:
    """Return a value as a float, or None when it is empty or unparseable."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.debug("%s | %r is not a number; storing null", _LOG, value)
        return None


def _attachments_json(value: Any) -> Optional[str]:
    """Return compact JSON of each attachment's relative image_url or video_url and thumbnail_url, or None when empty or unreadable."""
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            logger.warning("%s | BAD_ATTACHMENTS_JSON | leaving ets_attachments unset", _LOG)
            return None
    if not raw:
        return None
    slim: List[Dict[str, Any]] = []
    for a in raw:
        content_type = str(a.get("content_type") or "")
        key = (
            "video_url" if content_type.startswith("video/")
            else "image_url" if content_type.startswith("image/")
            else "file_url"
        )
        entry = {key: to_relative_blob_url(a.get("path"))}
        if a.get("thumbnail_path"):
            entry["thumbnail_url"] = to_relative_blob_url(a["thumbnail_path"])
        slim.append(entry)
    return json.dumps(slim, separators=(",", ":"))


def _viewcount_text(post: Dict[str, Any]) -> Optional[str]:
    """Return a post's view_count as the text ets_viewcount stores, or None when the post carries none."""
    value = post.get("view_count")
    if value is None or value == "":
        return None
    try:
        return str(int(value))
    except (TypeError, ValueError):
        logger.debug("%s | %r is not a view count; leaving ets_viewcount unset", _LOG, value)
        return None


def _deleted_choice(value: Any) -> int:
    """Return 1 when the deleted value is True or 'true', '1' or 'yes', otherwise 0."""
    if isinstance(value, bool):
        return _DELETED_TRUE if value else _DELETED_FALSE
    return (
        _DELETED_TRUE
        if str(value).strip().lower() in ("true", "1", "yes")
        else _DELETED_FALSE
    )


def _capped(payload: Dict[str, Any], kind: str, record_id: str) -> Dict[str, Any]:
    """Return a copy of the payload with strings trimmed to kind's Dataverse MaxLength, logging each cut."""
    trimmed = dict(payload)
    for key, limit in _MAX_LEN[kind].items():
        value = trimmed.get(key)
        if isinstance(value, str) and len(value) > limit:
            logger.warning(
                "%s | TRUNCATED | kind=%s | id=%s | field=%s | %d chars -> %d (Dataverse MaxLength)",
                _LOG, kind, record_id, key, len(value), limit,
            )
            trimmed[key] = value[:limit]
    return trimmed


def _bind_people(payload: Dict[str, Any], record: Dict[str, Any]) -> None:
    """Add the userprofile and contact lookup binds for whichever of the record's ids are set."""
    userprofileid = str(record.get("userprofileid") or "").strip()
    contactid = str(record.get("contactid") or "").strip()
    if userprofileid:
        payload["ets_UserProfileID@odata.bind"] = f"/{_ENTITY_USERPROFILES}({userprofileid})"
    if contactid:
        payload["ets_ContactID@odata.bind"] = f"/{_ENTITY_CONTACTS}({contactid})"


async def _patch_with_bind_fallback(entity: str, kind: str, record_id: str, payload: Dict[str, Any]) -> None:
    """PATCH one community record, retrying once without lookup binds; failures are logged and swallowed."""
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("%s | FAILED_TOKEN | kind=%s | id=%s | %s", _LOG, kind, record_id, exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)
    url = f"{base_url}/{entity}({record_id})"
    capped = _capped(payload, kind, record_id)
    label = kind.upper()

    if await _upsert_record(headers, url, capped, label, record_id, log_prefix=_LOG):
        return

    bound = [k for k in _BIND_KEYS if k in capped]
    if not bound:
        return

    logger.warning(
        "%s | RETRY_WITHOUT_LINKS | kind=%s | id=%s | dropping %s | a bind target is missing "
        "from Dataverse, so the record is mirrored unlinked rather than lost",
        _LOG, kind, record_id, ", ".join(b.split("@")[0] for b in bound),
    )
    for key in bound:
        capped.pop(key, None)
    await _upsert_record(headers, url, capped, f"{label}_UNLINKED", record_id, log_prefix=_LOG)


async def sync_post_to_dataverse(post: Dict[str, Any]) -> None:
    """PATCH a need, update or event onto its ets_needs, ets_updates or ets_events record; failures are logged and swallowed."""
    post_type = str(post.get("post_type") or "need").strip().lower()
    if post_type not in _POST_ENTITIES:
        logger.warning("%s | SKIPPED | unknown post_type=%s", _LOG, post_type)
        return
    entity, id_key = _POST_ENTITIES[post_type]
    postid = str(post.get(id_key) or "").strip()
    if not postid:
        logger.warning("%s | SKIPPED | %s has no %s", _LOG, post_type, id_key)
        return
    title = post.get("title")
    payload: Dict[str, Any] = {
        "ets_name": title,
        "ets_title": title,
        "ets_description": post.get("description"),
        "ets_category": post.get("category"),
        "ets_townname": post.get("townname"),
        "ets_locationname": post.get("location_name"),
        "ets_latitude": _as_float(post.get("latitude")),
        "ets_longitude": _as_float(post.get("longitude")),
        "ets_attachments": _attachments_json(post.get("attachments")),
        "ets_status": post.get("status"),
        "ets_deleted": _deleted_choice(post.get("deleted")),
    }
    needed_at = _as_datetime_str(post.get("needed_at"))
    if post_type == "need":
        payload.update({
            "ets_neededat": needed_at,
            "ets_budget": post.get("budget"),
            "ets_needstatus": post.get("need_status"),
        })
    elif post_type == "update":
        payload["ets_neededat"] = needed_at
        payload["ets_viewcount"] = _viewcount_text(post)
    else:
        start_date = _as_date(post.get("start_date"))
        end_date = _as_date(post.get("end_date"))
        payload.update({
            "ets_needat": needed_at,
            "ets_startdate": start_date.isoformat() if start_date else None,
            "ets_enddate": end_date.isoformat() if end_date else None,
            "ets_starttime": _event_moment(start_date, post.get("start_time")),
            "ets_endtime": _event_moment(end_date or start_date, post.get("end_time")),
            "ets_venue": post.get("venue"),
            "ets_organizername": post.get("organizer_name"),
            "ets_organizernumber": post.get("organizer_number"),
            "ets_eventinfo": post.get("entry_info"),
            "ets_viewcount": _viewcount_text(post),
        })

    townprofileid = str(post.get("townprofileid") or "").strip()
    if townprofileid:
        payload["ets_TownProfileID@odata.bind"] = f"/{_ENTITY_TOWNPROFILES}({townprofileid})"
    _bind_people(payload, post)

    await _patch_with_bind_fallback(entity, post_type, postid, payload)


async def sync_post_viewcounts_to_dataverse(posts: List[Dict[str, Any]]) -> None:
    """PATCH only ets_viewcount on each counted update or event that already exists in Dataverse; failures are logged and swallowed."""
    targets = [
        p for p in posts
        if p.get("counted") and p.get("view_count") is not None and p.get("type") in _VIEWCOUNT_TYPES
    ]
    if not targets:
        return
    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("%s | VIEWCOUNT | FAILED_TOKEN | posts=%d | %s", _LOG, len(targets), exc, exc_info=True)
        return

    base_url, headers = _build_dataverse_context(access_token)
    headers = {**headers, "If-Match": "*"}
    for p in targets:
        entity, _ = _POST_ENTITIES[p["type"]]
        await _upsert_record(
            headers,
            f"{base_url}/{entity}({p['id']})",
            {"ets_viewcount": str(int(p["view_count"]))},
            f"{p['type'].upper()}_VIEWCOUNT",
            p["id"],
            log_prefix=_LOG,
        )


async def sync_community_like_to_dataverse(like: Dict[str, Any]) -> None:
    """PATCH one like or dislike onto its ets_communitylikes record; failures are logged and swallowed."""
    likeid = str(like.get("likeid") or "").strip()
    if not likeid:
        logger.warning("%s | SKIPPED | like has no likeid", _LOG)
        return

    post_type = like.get("post_type")
    likemodename = like.get("likemodename")
    payload: Dict[str, Any] = {
        "ets_name": f"{likemodename} on {post_type}" if likemodename and post_type else likemodename,
        "ets_postid": like.get("postid"),
        "ets_posttype": post_type,
        "ets_likemode": _to_int(like.get("likemode")),
        "ets_likemodename": likemodename,
    }
    _bind_people(payload, like)

    await _patch_with_bind_fallback(_ENTITY_COMMUNITYLIKES, "like", likeid, payload)


async def sync_community_comment_to_dataverse(comment: Dict[str, Any]) -> None:
    """PATCH one comment onto its ets_communitycomments record, or only ets_deleted for a soft delete; failures are logged and swallowed."""
    commentid = str(comment.get("commentid") or "").strip()
    if not commentid:
        logger.warning("%s | SKIPPED | comment has no commentid", _LOG)
        return

    text = comment.get("comment")
    payload: Dict[str, Any] = {
        "ets_name": text,
        "ets_comment": text,
        "ets_postid": comment.get("postid"),
        "ets_posttype": comment.get("post_type"),
        "ets_deleted": _deleted_choice(comment.get("deleted")),
    }
    _bind_people(payload, comment)

    await _patch_with_bind_fallback(_ENTITY_COMMUNITYCOMMENTS, "comment", commentid, payload)


async def sync_town_review_to_dataverse(review: Dict[str, Any]) -> None:
    """PATCH one town image request onto its ets_townreviews record; failures are logged and swallowed."""
    townreviewid = str(review.get("townreviewid") or "").strip()
    if not townreviewid:
        logger.warning("%s | SKIPPED | town review has no townreviewid", _LOG)
        return

    townname = review.get("townname")
    payload: Dict[str, Any] = {
        "ets_name": townname,
        "ets_townname": townname,
        "ets_contactname": review.get("contactidname"),
        "ets_imageurl": to_relative_blob_url(review.get("imageurl")),
        "ets_description": review.get("description"),
        "ets_review": _verification_to_int(review.get("status")),
    }
    townprofileid = str(review.get("townprofileid") or "").strip()
    if townprofileid:
        payload["ets_TownProfileID@odata.bind"] = f"/{_ENTITY_TOWNPROFILES}({townprofileid})"
    _bind_people(payload, review)

    await _patch_with_bind_fallback(_ENTITY_TOWNREVIEWS, "townreview", townreviewid, payload)


async def sync_category_to_dataverse(category: Dict[str, Any]) -> None:
    """PATCH one etown_category row onto its ets_categories record; a missing categoryimage leaves the CRM image as is."""
    categoryid = str(category.get("categoryid") or "").strip()
    if not categoryid:
        logger.warning("%s | SKIPPED | category has no categoryid", _LOG)
        return

    payload: Dict[str, Any] = {
        "ets_name": category.get("categoryname"),
        "ets_displayname": category.get("displayname"),
        "ets_categoryimage": to_relative_blob_url(category.get("categoryimage")) if category.get("categoryimage") else None,
    }

    await _patch_with_bind_fallback(_ENTITY_CATEGORIES, "category", categoryid, payload)


async def sync_category_pin_to_dataverse(pin: Dict[str, Any]) -> None:
    """PATCH one pin or unpin onto its ets_categorypins record, upserting the parent ets_categories record first so the bind lands; failures are logged and swallowed."""
    categorypinid = str(pin.get("categorypinid") or "").strip()
    if not categorypinid:
        logger.warning("%s | SKIPPED | category pin has no categorypinid", _LOG)
        return

    categoryid = str(pin.get("category_id") or "").strip()
    if categoryid:
        await sync_category_to_dataverse({
            "categoryid": categoryid,
            "categoryname": pin.get("categoryname"),
            "displayname": pin.get("displayname"),
        })

    payload: Dict[str, Any] = {
        "ets_name": pin.get("displayname") or pin.get("categoryname"),
        "ets_cityname": pin.get("cityname"),
        "ets_areaname": pin.get("areaname"),
        "ets_ispinned": "true" if pin.get("ispinned") else "false",
    }
    if categoryid:
        payload["ets_CategoryID@odata.bind"] = f"/{_ENTITY_CATEGORIES}({categoryid})"
    _bind_people(payload, pin)

    await _patch_with_bind_fallback(_ENTITY_CATEGORYPINS, "categorypin", categorypinid, payload)
