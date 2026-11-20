"""Suggestions Utilities - Postgres business logic for member and provider connection suggestions.

Domain layer behind /api/suggestions: loads the caller's circle from memberrelation and providerrelation, ranks member and provider suggestions while excluding existing connections, pending invites and ineligible providers, and adds mutual-connection, circle and plan-badge fields to each result.

Features:
    - Circle loading: First-degree circle from memberrelation and providerrelation
    - Member suggestions: N-degree ranked, with an oldest-profile fallback
    - Provider suggestions: Distance-sorted when coordinates are supplied
    - Exclusions: Pending invites, MyCircle, deleted, disabled and review-pending providers
    - Mutual connections: Viewer-only and shared circles, plus booking links, briefly cached
    - Connection flags: Etown_AlreadyCircle / Etown_AlreadyConnected per result
    - Plan badges: Provider rows carry Etown_providertype and badge label
    - Formatting and deduplication: JSON response shaping deduped by profile or contact id

Classes:
    No classes defined (module exposes async/sync utility functions only)

Functions:
    _relation_keys(): Normalise a relation row's (contactid, userprofileid) for cross-source comparison
    load_both_circles(): Load a user's first-degree circle from both memberrelation and providerrelation tables
    load_booking_connections(): People who booked a provider, treated as mutual connections in their own right
    fetch_connections_for_profiles(): Retrieve connection data for a list of profile dicts, concurrently
    fetch_connections_for_profile_ids(): Bulk-fetch the "other side" of relations for many profile ids in one round trip
    fetch_all_profiles_sorted_by_oldest(): Fallback query for oldest-registered profiles, excluding a given id set
    filter_by_provider_status(): Drop role=1 (provider) profiles from a member candidate list
    check_invitations_and_filter(): Drop profiles the caller already has a pending invitation to
    fetch_details_for_relations(): Bulk fetch full member/contact details for a set of profile ids
    fetch_mutual_connection_details(): Hydrate mutual-connection rows the same way Popular Providers does
    format_user_contact_details_to_json(): Structure member/contact data for API responses (JSON string)
    deduplicate_by_user_profile_id(): Remove duplicate formatted profiles by userprofileid
    mutual_connection_status(): Calculate shared connections between users and set circle/connected flags
    fetch_provider_details_by_user_profile_ids(): Bulk provider detail retrieval by userprofileid
    format_provider_details_to_json(): Structure provider data for API responses (JSON string)
    deduplicate_users_by_contact_id(): Remove duplicate formatted profiles by contactid
    _filter_by_provider_status_for_mutual(): Drop unverified providers with no business name from a relation list
    _drop_disabled_provider_relations(): Drop relations pointing at disabled providers
    member_relations_only(): Keep only relation rows sourced from etown_memberrelation
    split_viewer_circle(): Split a viewer's circle into exclusive-of-caller and shared-with-caller buckets
    merge_shared_relations(): Append extra shared relations to a primary list, deduped by contact/profile id
    get_exclusive_viewer_relations(): Thin wrapper over split_viewer_circle returning only the exclusive bucket
    get_shared_circle_relations(): Thin wrapper over split_viewer_circle returning only the shared bucket
    get_provider_filtered_relations(): Orchestrates circle loading, booking legs and invite/provider filtering
    handle_get_mutual_connections(): Main mutual connection endpoint logic, with a short-lived result cache
    get_search_suggestions_paginated(): Paginated member (N-degree ranked) and provider (distance-sorted) suggestions

Dependencies:
    - asyncio: Asynchronous suggestion processing
    - datetime: Time-based filtering and sorting
    - app.db.client: Database operations for relationship data
    - app.core.logging: Suggestion interaction logging
    - json / time: JSON formatting and cache/timing bookkeeping
    - app.etown.plans.plans_utils: premium_display and resolve_provider_badges
    - app.etown.provider.provider_review_utils: review_pending_exclusion filter

Updates:
    23-Feb-2026: Enhanced documentation with function inventory.
    26-Feb-2026: Added member contact fields and standardized field naming.
    06-Mar-2026: fetch_details_for_relations accepts profile dicts and ID strings.
    28-Jul-2026: get_search_suggestions_paginated excludes MyCircle records from both lists.
    19-Aug-2026: get_search_suggestions_paginated excludes deleted members and providers.
    20-Aug-2026: Provider rows carry Etown_providertype; verified markers report the paid tier's badge label.
    02-Sep-2026: Disabled providers excluded from suggestion lists; added _drop_disabled_provider_relations() for /mutual_connection.
    17-Sep-2026: Providers awaiting review held out of both suggestion lists.

Notes:
    Combines member and provider relationship tables for complete social graphs.
    Optimized for large-scale social network analysis.
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable, Coroutine, Union
from app.db.client import Database
from app.core.logging import logger
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.etown.provider.provider_review_utils import review_pending_exclusion

_EXCLUDE_REVIEW_PENDING_P_UPPER = "AND " + review_pending_exclusion("P.providerid")
_EXCLUDE_REVIEW_PENDING_P = "AND " + review_pending_exclusion("p.providerid")

_SUGGESTION_CACHE: Dict[Tuple, Dict[str, Any]] = {}


async def load_both_circles(
    current_contact_id: str,
    current_user_profile_id: str,
    owner_contact_id: str,
    owner_user_profile_id: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Return the current profile's member and provider relations and the distinct connected profile IDs; owner IDs are unused."""
    database = Database()
    
    combined_query = """
    SELECT
        to_userprofile AS profile_id,
        to_contactid AS contact_id,
        'member' AS source
    FROM etown_memberrelation
    WHERE from_userprofile = $1
    UNION ALL
    SELECT
        from_userprofile AS profile_id,
        from_contactid AS contact_id,
        'member' AS source
    FROM etown_memberrelation
    WHERE to_userprofile = $1
    UNION ALL
    SELECT
        touserprofileid AS profile_id,
        tocontactid AS contact_id,
        'provider' AS source
    FROM etown_providerrelation
    WHERE fromuserprofileid = $1
    UNION ALL
    SELECT
        fromuserprofileid AS profile_id,
        fromcontactid AS contact_id,
        'provider' AS source
    FROM etown_providerrelation
    WHERE touserprofileid = $1;
    """
    
    t0 = time.perf_counter()
    combined_results = await database.fetch(combined_query, current_user_profile_id)
    t1 = time.perf_counter()
    
    relations = []
    circle_profile_ids = set()
    
    for row in combined_results:
        profile_id = row.get('profile_id')
        contact_id = row.get('contact_id')
        if profile_id and contact_id:
            relations.append({
                "from_userprofile": profile_id,
                "from_contactid": contact_id,
                "source": row.get("source"),
            })
            circle_profile_ids.add(profile_id)
    
    logger.info(
        "load_both_circles: total=%.1fms rows=%d results=%d unique_profile_ids=%d",
        (t1 - t0) * 1000,
        len(combined_results),
        len(relations),
        len(circle_profile_ids),
    )
    
    return relations, list(circle_profile_ids)


BOOKED_REQUEST_STATUSES = ('completed', 'confirmed', 'requested', 'accepted')


async def load_booking_connections(
    target_user_profile_id: str,
) -> List[Dict[str, Any]]:
    """Return relation-shaped rows, profile ID only, for everyone who booked the given provider profile."""
    if not target_user_profile_id:
        return []

    database = Database()

    query = """
    SELECT DISTINCT ar.requestoruserprofileid AS profile_id
      FROM etown_allrequests ar
      JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid
     WHERE rsl.serviceprovideruserprofileid = $1
       AND ar.requestoruserprofileid IS NOT NULL
       AND ar.requeststatus = ANY($2::text[]);
    """

    t0 = time.perf_counter()
    try:
        rows = await database.fetch(query, target_user_profile_id, list(BOOKED_REQUEST_STATUSES))
    except Exception as e:
        logger.error(f"load_booking_connections failed for {target_user_profile_id}: {e}", exc_info=True)
        return []
    t1 = time.perf_counter()

    relations = [
        {"from_userprofile": row.get("profile_id"), "from_contactid": None}
        for row in rows
        if row.get("profile_id")
    ]

    logger.info(
        "load_booking_connections: query=%.1fms bookers=%d",
        (t1 - t0) * 1000,
        len(relations),
    )
    return relations


async def fetch_connections_for_profiles(
    profiles: List[Dict[str, Any]],
    loader_func: Callable[[str, str, str, str], Coroutine[Any, Any, Tuple[List[Dict[str, Any]], List[str]]]]
) -> List[Dict[str, Any]]:
    """Run the loader for each profile concurrently and return all their connections, skipping failures."""
    if not profiles:
        return []
    
    tasks = []
    for profile in profiles:
        profile_id = profile.get("from_userprofile") or profile.get("fromuserprofileid")
        contact_id = profile.get("from_contactid") or profile.get("fromcontactid")
        
        if profile_id and contact_id:
            task = loader_func(contact_id, profile_id, contact_id, profile_id)
            tasks.append(task)
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_connections = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Error fetching connections: {result}")
            continue
        
        if isinstance(result, tuple) and len(result) >= 1:
            connections, _ = result
            all_connections.extend(connections)
    
    logger.info(f"fetch_connections_for_profiles: Fetched {len(all_connections)} total connections from {len(profiles)} profiles")
    
    return all_connections


async def fetch_connections_for_profile_ids(
    profile_ids: List[str]
) -> List[Dict[str, Any]]:
    """Return the other side of every member and provider relation touching the given profile IDs, in two bulk queries."""
    if not profile_ids:
        return []

    database = Database()
    unique_ids = list({pid for pid in profile_ids if pid})
    if not unique_ids:
        return []

    member_query = """
    SELECT
        CASE
            WHEN from_userprofile = ANY($1::text[]) THEN to_userprofile
            ELSE from_userprofile
        END AS from_userprofile,
        CASE
            WHEN from_userprofile = ANY($1::text[]) THEN to_contactid
            ELSE from_contactid
        END AS from_contactid
    FROM etown_memberrelation
    WHERE from_userprofile = ANY($1::text[])
       OR to_userprofile = ANY($1::text[]);
    """

    provider_query = """
    SELECT
        CASE
            WHEN fromuserprofileid = ANY($1::text[]) THEN touserprofileid
            ELSE fromuserprofileid
        END AS fromuserprofile,
        CASE
            WHEN fromuserprofileid = ANY($1::text[]) THEN tocontactid
            ELSE fromcontactid
        END AS fromcontactid
    FROM etown_providerrelation
    WHERE fromuserprofileid = ANY($1::text[])
       OR touserprofileid = ANY($1::text[]);
    """

    t0 = time.perf_counter()
    m_task = database.fetch(member_query, unique_ids)
    p_task = database.fetch(provider_query, unique_ids)
    member_results, provider_results = await asyncio.gather(m_task, p_task)
    t1 = time.perf_counter()

    combined = []
    for row in member_results:
        profile_id = row.get("from_userprofile")
        contact_id = row.get("from_contactid")
        if profile_id and contact_id:
            combined.append({
                "from_userprofile": profile_id,
                "from_contactid": contact_id
            })

    for row in provider_results:
        profile_id = row.get("fromuserprofile")
        contact_id = row.get("fromcontactid")
        if profile_id and contact_id:
            combined.append({
                "from_userprofile": profile_id,
                "from_contactid": contact_id
            })

    logger.info(
        "fetch_connections_for_profile_ids: total=%.1fms member_rows=%d provider_rows=%d profiles=%d",
        (t1 - t0) * 1000,
        len(member_results),
        len(provider_results),
        len(unique_ids),
    )
    return combined


async def fetch_all_profiles_sorted_by_oldest(
    excluded_ids: set,
    limit: int
) -> List[Dict[str, Any]]:
    """Return up to limit of the oldest live non-provider profiles not in excluded_ids."""
    database = Database()
    
    excluded_list = list(excluded_ids)
    
    query = """
    SELECT
        up.userprofileid AS from_userprofile,
        c.contactid AS from_contactid,
        up.createdon
    FROM etown_userprofile up
    JOIN etown_contact c ON up.contactid = c.contactid
    WHERE up.userprofileid != ALL($1::text[])
    AND up.role != 1

    AND up.deleted IS DISTINCT FROM 'true'
    AND (up.deactivated IS DISTINCT FROM 'true' OR up.reactivation = 'true')
    AND c.deleted IS DISTINCT FROM 'true'
    AND (c.deactivated IS DISTINCT FROM 'true' OR c.reactivation = 'true')
    ORDER BY up.createdon ASC
    LIMIT $2;
"""
    
    t0 = time.perf_counter()
    results = await database.fetch(query, excluded_list, limit)
    t1 = time.perf_counter()
    
    profiles = []
    for row in results:
        profiles.append({
            "from_userprofile": row.get('from_userprofile'),
            "from_contactid": row.get('from_contactid')
        })
    
    logger.info(
        "fetch_all_profiles_sorted_by_oldest: query=%.1fms results=%d limit=%d",
        (t1 - t0) * 1000,
        len(profiles),
        limit,
    )
    
    return profiles


async def filter_by_provider_status(
    profiles: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Drop profiles whose userprofile role is 1 (provider)."""
    if not profiles:
        return []
    
    database = Database()
    
    profile_ids = [p.get("from_userprofile") or p.get("fromuserprofileid") for p in profiles if p.get("from_userprofile") or p.get("fromuserprofileid")]
    
    if not profile_ids:
        return profiles
    
    query = """
    SELECT userprofileid, role
    FROM etown_userprofile
    WHERE userprofileid = ANY($1::text[]);
    """
    
    t0 = time.perf_counter()
    results = await database.fetch(query, profile_ids)
    t1 = time.perf_counter()
    
    provider_ids = {row['userprofileid'] for row in results if row.get('role') == 1}
    
    filtered_profiles = [
        p for p in profiles
        if (p.get("from_userprofile") or p.get("fromuserprofileid")) not in provider_ids
    ]
    
    logger.info(
        "filter_by_provider_status: query=%.1fms filtered_out=%d remaining=%d",
        (t1 - t0) * 1000,
        len(profiles) - len(filtered_profiles),
        len(filtered_profiles),
    )
    
    return filtered_profiles


async def check_invitations_and_filter(
    profiles: List[Dict[str, Any]],
    current_user_profile_id: str
) -> List[Dict[str, Any]]:
    """Drop profiles whose contact already has a Requested invitation from the current user."""
    if not profiles:
        return []
    
    database = Database()
    
    invitation_query = """
    SELECT invitecontact FROM etown_invitation
    WHERE inviteruserprofile = $1 AND invitationcode = 'Requested';
    """
    
    t2 = time.perf_counter()
    invitation_results = await database.fetch(invitation_query, current_user_profile_id)
    t3 = time.perf_counter()
    pending_receiver_ids = {row['invitecontact'] for row in invitation_results}
    
    filtered_profiles = [
        p for p in profiles
        if (p.get("from_contactid") or p.get("fromcontactid")) not in pending_receiver_ids
    ]
    
    logger.info(
        "check_invitations_and_filter: invite_query=%.1fms filtered_out=%d remaining=%d",
        (t3 - t2) * 1000,
        len(profiles) - len(filtered_profiles),
        len(filtered_profiles),
    )
    
    return filtered_profiles


async def fetch_details_for_relations(
    profiles: List[Union[Dict[str, Any], str]],
    current_user_profile_id: Optional[str] = None,
    current_contact_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return live member and contact details for the profiles, with circle flags and invite filtering when a viewer is given."""
    if not profiles:
        return []
    
    database = Database()
    
    if profiles and isinstance(profiles[0], str):
        profile_ids = profiles
    else:
        profile_ids = [p.get("from_userprofile") or p.get("fromuserprofileid") for p in profiles if p.get("from_userprofile") or p.get("fromuserprofileid")]
        
        if not profile_ids:
            return []

    profile_ids = [
        p.strip().lower() for p in profile_ids
        if isinstance(p, str) and p.strip()
    ]
    if not profile_ids:
        return []
    
    if current_user_profile_id and current_contact_id:
        query = """
        SELECT
            m.userprofileid,
            m.role,
            m.dateofcreation AS createdon,
            c.contactid,
            c.firstname,

            c.email,
            c.mobilephone AS phonenumber,
            c.profileimage,
            c.activestate,
            c.address,
            c.statecode,
            c.city,
            c.latitude,
            c.longitude,
            (
                EXISTS (
                    SELECT 1
                    FROM etown_memberrelation r
                    WHERE ((r.from_userprofile = $2 AND r.to_userprofile = m.userprofileid)
                        OR (r.to_userprofile = $2 AND r.from_userprofile = m.userprofileid))
                )
                OR EXISTS (
                    SELECT 1
                    FROM etown_providerrelation pr
                    WHERE ((pr.fromuserprofileid = $2 AND pr.touserprofileid = m.userprofileid)
                        OR (pr.touserprofileid = $2 AND pr.fromuserprofileid = m.userprofileid))
                )
            ) AS already_circle,
            (
                EXISTS (
                    SELECT 1
                    FROM etown_memberrelation r
                    WHERE ((r.from_contactid = $3 AND r.to_contactid = c.contactid)
                        OR (r.to_contactid = $3 AND r.from_contactid = c.contactid))
                )
                OR EXISTS (
                    SELECT 1
                    FROM etown_providerrelation pr
                    WHERE ((pr.fromcontactid = $3 AND pr.tocontactid = c.contactid)
                        OR (pr.tocontactid = $3 AND pr.fromcontactid = c.contactid))
                )
            ) AS already_connected,
            COALESCE(c.deleted, 'false') AS deleted,
            CASE
                WHEN c.deactivated = 'true' AND c.reactivation IS DISTINCT FROM 'true'
                THEN 'true' ELSE 'false'
            END AS deactivated
        FROM etown_member m
        JOIN etown_contact c ON m.contactid = c.contactid
        WHERE LOWER(TRIM(m.userprofileid)) = ANY($1::text[])

          AND c.deleted IS DISTINCT FROM 'true'

          AND (c.deactivated IS DISTINCT FROM 'true' OR c.reactivation = 'true')
          AND NOT EXISTS (
              SELECT 1
              FROM etown_invitation i
              WHERE i.inviteruserprofile = $2
                AND i.invitecontact = c.contactid
                AND i.invitationcode = 'Requested'
          );
"""
        t0 = time.perf_counter()
        results = await database.fetch(query, profile_ids, current_user_profile_id, current_contact_id)
    else:
        query = """
        SELECT
            m.userprofileid,
            m.role,
            m.dateofcreation AS createdon,
            c.contactid,
            c.firstname,

            c.email,
            c.mobilephone AS phonenumber,
            c.profileimage,
            c.activestate,
            c.address,
            c.statecode,
            c.city,
            c.latitude,
            c.longitude,
            COALESCE(c.deleted, 'false') AS deleted,
            CASE
                WHEN c.deactivated = 'true' AND c.reactivation IS DISTINCT FROM 'true'
                THEN 'true' ELSE 'false'
            END AS deactivated
        FROM etown_member m
        JOIN etown_contact c ON m.contactid = c.contactid
        WHERE LOWER(TRIM(m.userprofileid)) = ANY($1::text[])

          AND c.deleted IS DISTINCT FROM 'true'
          AND (c.deactivated IS DISTINCT FROM 'true' OR c.reactivation = 'true');
"""
        t0 = time.perf_counter()
        results = await database.fetch(query, profile_ids)
    t1 = time.perf_counter()
    
    detailed_profiles = []
    for row in results:
        detailed_profiles.append({
            "userprofileid": row.get('userprofileid'),
            "role": row.get('role'),
            "contactid": row.get('contactid'),
            "firstname": row.get('firstname'),
            "email": row.get('email'),
            "phonenumber": row.get('phonenumber'),
            "profileimage": row.get('profileimage'),
            "createdon": row.get('createdon'),
            "already_circle": row.get('already_circle'),
            "already_connected": row.get('already_connected'),
            "activestate": row.get('activestate'),
            "address": row.get('address'),
            "statecode": row.get('statecode'),
            "city": row.get('city'),
            "latitude": row.get('latitude'),
            "longitude": row.get('longitude'),
            "deleted": row.get('deleted'),
            "deactivated": row.get('deactivated')
        })
    
    logger.info(
        "fetch_details_for_relations: query=%.1fms results=%d",
        (t1 - t0) * 1000,
        len(detailed_profiles),
    )
    
    return detailed_profiles


async def fetch_mutual_connection_details(
    relations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Hydrate mutual connections by contactid from live contacts, left-joining etown_member for role and creation date."""
    if not relations:
        return []

    database = Database()

    by_contact: Dict[str, str] = {}
    for item in relations:
        cid, upid = _relation_keys(item)
        if cid and cid not in by_contact:
            by_contact[cid] = upid or ""

    if not by_contact:
        logger.warning("fetch_mutual_connection_details: %d relations carried no contactid", len(relations))
        return []

    query = """
    SELECT DISTINCT ON (LOWER(TRIM(c.contactid)))
        c.contactid,
        c.firstname,
        c.email,
        c.mobilephone AS phonenumber,
        c.profileimage,
        c.activestate,
        c.address,
        c.statecode,
        c.city,
        c.latitude,
        c.longitude,
        COALESCE(c.deleted, 'false') AS deleted,
        CASE
            WHEN c.deactivated = 'true' AND c.reactivation IS DISTINCT FROM 'true'
            THEN 'true' ELSE 'false'
        END AS deactivated,
        m.userprofileid,
        m.role,
        m.dateofcreation AS createdon
    FROM etown_contact c
    LEFT JOIN etown_member m
           ON LOWER(TRIM(m.contactid)) = LOWER(TRIM(c.contactid))
    WHERE LOWER(TRIM(c.contactid)) = ANY($1::text[])

      AND c.deleted IS DISTINCT FROM 'true'
      AND (c.deactivated IS DISTINCT FROM 'true' OR c.reactivation = 'true')
    ORDER BY LOWER(TRIM(c.contactid)), m.dateofcreation NULLS LAST;
"""

    t0 = time.perf_counter()
    records = await database.fetch(query, list(by_contact))
    t1 = time.perf_counter()

    rows = []
    for r in records:
        row = dict(r)
        cid = str(row.get("contactid") or "").strip().lower()
        row["userprofileid"] = by_contact.get(cid) or row.get("userprofileid")
        if row.get("role") is None:
            row["role"] = 2
        rows.append(row)

    logger.info(
        "fetch_mutual_connection_details: query=%.1fms requested=%d hydrated=%d",
        (t1 - t0) * 1000, len(by_contact), len(rows),
    )
    return rows


def format_user_contact_details_to_json(
    profiles: List[Dict[str, Any]]
) -> str:
    """Serialise member profile details to a JSON string of Etown_-prefixed items."""
    formatted_profiles = []
    
    for profile in profiles:
        created_on = profile.get('createdon')
        created_iso = created_on.isoformat() + 'Z' if isinstance(created_on, datetime) else None
        
        already_circle = bool(profile.get("already_circle")) if profile.get("already_circle") is not None else False
        already_connected = bool(profile.get("already_connected")) if profile.get("already_connected") is not None else False
        
        role = profile.get("role", 2)
        role_name = "Member" if role == 2 else "Provider" if role == 1 else "Unknown"
        
        formatted = {
            "Etown_userprofileid": profile.get("userprofileid"),
            "Etown_rolename": role_name,
            "Etown_firstname": profile.get("firstname"),
            "Etown_role": role,
            "Etown_contactid": profile.get("contactid"),
            "Etown_status": bool(profile.get("activestate")) if profile.get("activestate") is not None else False,
            "Etown_email": profile.get("email"),
            "Etown_mobilenumber": profile.get("phonenumber"),
            "Etown_profileimage": profile.get("profileimage"),
            "Etown_createdon": created_iso,
            "Etown_address": profile.get("address"),
            "Etown_statecode": profile.get("statecode"),
            "Etown_city": profile.get("city"),
            "Etown_latitude": float(profile.get("latitude")) if profile.get("latitude") is not None else None,
            "Etown_longitude": float(profile.get("longitude")) if profile.get("longitude") is not None else None,
            "Etown_isconnected": already_circle or already_connected,
            "Etown_AlreadyCircle": already_circle,
            "Etown_AlreadyConnected": already_connected,
            "Etown_deleted": profile.get("deleted") or "false",
            "Etown_deactivated": profile.get("deactivated") or "false",
            "Etown_resultType": 2
        }
        formatted_profiles.append(formatted)
    
    return json.dumps(formatted_profiles)


def deduplicate_by_user_profile_id(
    profiles: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Remove profiles with a repeated or missing Etown_userprofileid, keeping the first occurrence."""
    seen = set()
    deduplicated = []
    
    for profile in profiles:
        profile_id = profile.get("Etown_userprofileid")
        if profile_id and profile_id not in seen:
            seen.add(profile_id)
            deduplicated.append(profile)
    
    logger.info(f"deduplicate_by_user_profile_id: Removed {len(profiles) - len(deduplicated)} duplicates, {len(deduplicated)} remaining")
    
    return deduplicated


async def mutual_connection_status(
    suggestions: Dict[str, List[Dict[str, Any]]],
    current_contact_id: str,
    current_user_profile_id: str
) -> Dict[str, List[Dict[str, Any]]]:
    """Set the circle and connection flags on every member, provider and people entry in place and return the suggestions."""
    database = Database()
    
    combined_list = suggestions.get("members", []) + suggestions.get("providers", [])
    if not combined_list:
        combined_list = suggestions.get("people", [])
    if not combined_list:
        combined_list = suggestions.get("suggestions", [])

    members = suggestions.get("members", [])
    providers = suggestions.get("providers", [])
    people = suggestions.get("people", [])
    
    all_profiles = members + providers + people
    if not all_profiles:
        return suggestions
    
    all_profile_ids = [p.get("Etown_userprofileid") for p in all_profiles if p.get("Etown_userprofileid")]
    all_contact_ids = [p.get("Etown_contactid") for p in all_profiles if p.get("Etown_contactid")]
    
    circle_profile_ids = set()
    if all_profile_ids:
        member_circle_query = """
        SELECT
            CASE
                WHEN from_userprofile = $1 THEN to_userprofile
                ELSE from_userprofile
            END AS circle_id
        FROM etown_memberrelation
        WHERE (from_userprofile = $1 AND to_userprofile = ANY($2::text[]))
           OR (to_userprofile = $1 AND from_userprofile = ANY($2::text[]));
        """
        provider_circle_query = """
        SELECT
            CASE
                WHEN fromuserprofileid = $1 THEN touserprofileid
                ELSE fromuserprofileid
            END AS circle_id
        FROM etown_providerrelation
        WHERE (fromuserprofileid = $1 AND touserprofileid = ANY($2::text[]))
           OR (touserprofileid = $1 AND fromuserprofileid = ANY($2::text[]));
        """
        
        t0 = time.perf_counter()
        m_circle_task = database.fetch(member_circle_query, current_user_profile_id, all_profile_ids)
        p_circle_task = database.fetch(provider_circle_query, current_user_profile_id, all_profile_ids)
        
        m_circle_results, p_circle_results = await asyncio.gather(m_circle_task, p_circle_task)
        t1 = time.perf_counter()
        
        circle_profile_ids.update(row['circle_id'] for row in m_circle_results)
        circle_profile_ids.update(row['circle_id'] for row in p_circle_results)

    connected_contact_ids = set()
    if all_contact_ids:
        connection_query = """
        (SELECT
            CASE
                WHEN from_contactid = $1 THEN to_contactid
                ELSE from_contactid
            END AS connected_id
        FROM etown_memberrelation
        WHERE (from_contactid = $1 AND to_contactid = ANY($2::text[]))
           OR (to_contactid = $1 AND from_contactid = ANY($2::text[]))
        )
        UNION
        (SELECT
            CASE
                WHEN fromcontactid = $1 THEN tocontactid
                ELSE fromcontactid
            END AS connected_id
        FROM etown_providerrelation
        WHERE (fromcontactid = $1 AND tocontactid = ANY($2::text[]))
           OR (tocontactid = $1 AND fromcontactid = ANY($2::text[]))
        );
        """
        t2 = time.perf_counter()
        connection_results = await database.fetch(connection_query, current_contact_id, all_contact_ids)
        t3 = time.perf_counter()
        connected_contact_ids = {row['connected_id'] for row in connection_results}
    
    for profile in all_profiles:
        profile_id = profile.get("Etown_userprofileid")
        contact_id = profile.get("Etown_contactid")
        
        profile["Etown_AlreadyCircle"] = profile_id in circle_profile_ids
        profile["Etown_AlreadyConnected"] = contact_id in connected_contact_ids
        profile["Etown_isconnected"] = profile["Etown_AlreadyCircle"] or profile["Etown_AlreadyConnected"]
    
    logger.info(
        "mutual_connection_status: circle_queries=%.1fms connection_query=%.1fms profiles=%d",
        (t1 - t0) * 1000 if "t1" in locals() else 0.0,
        (t3 - t2) * 1000 if "t3" in locals() else 0.0,
        len(all_profiles),
    )
    
    return suggestions


async def fetch_provider_details_by_user_profile_ids(
    user_profile_ids: List[str]
) -> List[Dict[str, Any]]:
    """Return live, enabled, review-cleared provider details with contact info for the given profile IDs."""
    if not user_profile_ids:
        return []

    database = Database()
    cleaned_ids = [s.strip().lower() for s in set(user_profile_ids)]
    
    query = """
    SELECT
      P.contactid,
      P.userprofileid,
      P.businessname AS firstname,
      P.publicsearchprovider,
      P.providertype,
      P.serviceimageurl AS profileimage,
      P.role AS rolecode,
      P.creationdate AS createdon,
      P.statecode,
      P.city,
      C.mobilephone AS mobilenumber,
      C.email,
      P.address,

      P.latitude,
      P.longitude,
      C.activestate,

      CASE WHEN COALESCE(P.deleted, 'false') = 'true'
             OR COALESCE(C.deleted, 'false') = 'true'
           THEN 'true' ELSE 'false' END AS lifecycle_deleted,
      CASE WHEN (P.deactivated = 'true' AND P.reactivation IS DISTINCT FROM 'true')
             OR (C.deactivated = 'true' AND C.reactivation IS DISTINCT FROM 'true')
           THEN 'true' ELSE 'false' END AS lifecycle_deactivated
    FROM etown_provider P
    LEFT JOIN etown_contact C ON P.contactid = C.contactid
    WHERE LOWER(TRIM(P.userprofileid)) = ANY($1::text[])

      AND P.deleted IS DISTINCT FROM 'true'
      AND (P.deactivated IS DISTINCT FROM 'true' OR P.reactivation = 'true')
      AND P.disabled IS NOT TRUE
      AND C.deleted IS DISTINCT FROM 'true'
      AND (C.deactivated IS DISTINCT FROM 'true' OR C.reactivation = 'true')
      """ + _EXCLUDE_REVIEW_PENDING_P_UPPER + """;
"""
    t0 = time.perf_counter()
    results = await database.fetch(query, cleaned_ids)
    t1 = time.perf_counter()
    logger.info(
        "fetch_provider_details_by_user_profile_ids: query=%.1fms results=%d",
        (t1 - t0) * 1000,
        len(results),
    )
    return results


def format_provider_details_to_json(
    provider_details: List[Dict[str, Any]],
) -> str:
    """Serialise provider details to an indented JSON string of Etown_-prefixed items."""
    role_map = {1: "Provider", 2: "Member"}
    formatted_output = []

    for detail in provider_details:
        contact_id = detail.get('contactid')
        provider_user_profile_id = detail.get('userprofileid')
        role_code = detail.get('rolecode')

        created = detail.get('createdon')
        created_iso = None
        if isinstance(created, datetime):
            created_iso = created.isoformat(timespec='seconds') + 'Z'
        elif isinstance(created, str):
            created_iso = created + ('Z' if not created.endswith('Z') else '')

        latitude = detail.get('latitude')
        longitude = detail.get('longitude')

        formatted_output.append({
            "Etown_userprofileid": provider_user_profile_id,
            "Etown_role": role_code,
            "Etown_rolename": role_map.get(role_code),
            "Etown_firstname": detail.get('firstname'),
            "Etown_publicsearchprovider": detail.get('publicsearchprovider'),
            "Etown_providertype": detail.get('providertype'),
            "Etown_contactid": contact_id,
            "Etown_status": bool(detail.get('activestate')) if detail.get('activestate') is not None else False,
            "Etown_email": detail.get('email'),
            "Etown_mobilenumber": detail.get('mobilenumber'),
            "Etown_profileimage": detail.get('profileimage'),
            "Etown_createdon": created_iso,
            "Etown_address": detail.get('address'),
            "Etown_statecode": detail.get('statecode'),
            "Etown_city": detail.get('city'),
            "Etown_latitude": float(latitude) if latitude is not None else None,
            "Etown_longitude": float(longitude) if longitude is not None else None,
            "Etown_isconnected": False,
            "Etown_AlreadyCircle": False,
            "Etown_AlreadyConnected": False,
            "Etown_deleted": detail.get('lifecycle_deleted') or "false",
            "Etown_deactivated": detail.get('lifecycle_deactivated') or "false",
            "Etown_resultType": 1,
        })
    return json.dumps(formatted_output, indent=4)


def deduplicate_users_by_contact_id(
    users: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Remove profiles with a repeated or missing Etown_contactid, keeping the first occurrence."""
    seen = set()
    result = []
    for user in users:
        cid = user.get("Etown_contactid")
        if cid and cid not in seen:
            seen.add(cid)
            result.append(user)
    return result


async def _filter_by_provider_status_for_mutual(
    relations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Drop relations whose contact owns a non-verified provider with no business name."""
    if not relations:
        return []

    database = Database()
    contact_ids = {
        item.get("from_contactid") or item.get("fromcontactid")
        for item in relations
        if item.get("from_contactid") or item.get("fromcontactid")
    }
    if not contact_ids:
        return relations

    query = """
    SELECT contactid, publicsearchprovider, businessname
      FROM etown_provider
     WHERE contactid = ANY($1::text[])
       AND publicsearchprovider IS NOT NULL;
    """
    t0 = time.perf_counter()
    records = await database.fetch(query, list(contact_ids))
    t1 = time.perf_counter()

    non_verified_contacts = set()
    for r in records:
        if r["publicsearchprovider"] == "non-verified" and (r["businessname"] is None or r["businessname"] == ""):
            non_verified_contacts.add(r["contactid"].lower())

    filtered_relations = []
    for item in relations:
        contact_id = item.get("from_contactid") or item.get("fromcontactid")
        if contact_id and contact_id.lower() not in non_verified_contacts:
            filtered_relations.append(item)
        elif not contact_id: 
            filtered_relations.append(item)

    logger.info(
        "_filter_by_provider_status_for_mutual: query=%.1fms filtered_out=%d remaining=%d",
        (t1 - t0) * 1000,
        len(relations) - len(filtered_relations),
        len(filtered_relations),
    )
    return filtered_relations


async def _drop_disabled_provider_relations(
    relations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Drop circle relations whose counterparty userprofileid is a disabled provider profile."""
    if not relations:
        return []

    database = Database()
    profile_ids = {upid for _, upid in map(_relation_keys, relations) if upid}
    if not profile_ids:
        return relations

    query = """
    SELECT userprofileid
      FROM etown_provider
     WHERE disabled
       AND LOWER(TRIM(userprofileid)) = ANY($1::text[]);
    """
    t0 = time.perf_counter()
    records = await database.fetch(query, list(profile_ids))
    t1 = time.perf_counter()

    disabled_profiles = {
        str(r["userprofileid"]).strip().lower() for r in records if r["userprofileid"]
    }
    if not disabled_profiles:
        return relations

    filtered_relations = [
        item for item in relations
        if (_relation_keys(item)[1] or "") not in disabled_profiles
    ]

    logger.info(
        "_drop_disabled_provider_relations: query=%.1fms filtered_out=%d remaining=%d",
        (t1 - t0) * 1000,
        len(relations) - len(filtered_relations),
        len(filtered_relations),
    )
    return filtered_relations


def _relation_keys(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return a relation row's (contactid, userprofileid), stripped and lower-cased, None when blank."""
    cid = item.get("from_contactid") or item.get("fromcontactid")
    upid = item.get("from_userprofile") or item.get("fromuserprofileid")
    return (
        cid.strip().lower() if isinstance(cid, str) and cid.strip() else None,
        upid.strip().lower() if isinstance(upid, str) and upid.strip() else None,
    )


MEMBER_RELATION_SOURCE = "member"


def member_relations_only(relations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep relations sourced from etown_memberrelation, plus any row without a source tag."""
    return [
        r for r in relations
        if r.get("source") is None or r.get("source") == MEMBER_RELATION_SOURCE
    ]


def split_viewer_circle(
    viewer_circle: List[Dict[str, Any]],
    current_circle: List[Dict[str, Any]],
    mutual_circle: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split the viewer's circle into (exclusive, shared): shared by profile ID in mutual_circle, exclusive when outside current_circle.

    Args:
        viewer_circle: The viewer's relation rows.
        current_circle: The current user's full circle, used for exclusion by profile or contact ID.
        mutual_circle: Rows defining the shared bucket; defaults to current_circle.
    """
    if mutual_circle is None:
        mutual_circle = current_circle

    current_profile_ids = set()
    current_contact_ids = set()
    for item in current_circle:
        cid, upid = _relation_keys(item)
        if upid:
            current_profile_ids.add(upid)
        if cid:
            current_contact_ids.add(cid)

    mutual_rows_by_pid: Dict[str, Dict[str, Any]] = {}
    for item in mutual_circle:
        _, upid = _relation_keys(item)
        if upid and upid not in mutual_rows_by_pid:
            mutual_rows_by_pid[upid] = item
    mutual_profile_ids = set(mutual_rows_by_pid)

    exclusive: List[Dict[str, Any]] = []
    shared: List[Dict[str, Any]] = []
    seen_shared = set()
    seen_exclusive = set()
    neither = 0

    for item in viewer_circle:
        cid, upid = _relation_keys(item)
        if not cid and not upid:
            continue

        if upid and upid in mutual_profile_ids:
            if upid not in seen_shared:
                seen_shared.add(upid)
                shared.append(mutual_rows_by_pid[upid])
        elif (upid and upid in current_profile_ids) or (cid and cid in current_contact_ids):
            neither += 1
        else:
            key = (cid, upid)
            if key not in seen_exclusive:
                seen_exclusive.add(key)
                exclusive.append(item)

    logger.info(
        "split_viewer_circle: viewer=%d current=%d mutual_circle=%d -> "
        "exclusive=%d shared=%d in_circle_but_not_member_mutual=%d",
        len(viewer_circle), len(current_circle), len(mutual_circle),
        len(exclusive), len(shared), neither,
    )
    return exclusive, shared


def merge_shared_relations(
    primary: List[Dict[str, Any]],
    extra: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Append extra shared relations to primary, skipping any whose profile or contact ID is already present."""
    seen_profiles = set()
    seen_contacts = set()
    for item in primary:
        cid, upid = _relation_keys(item)
        if upid:
            seen_profiles.add(upid)
        if cid:
            seen_contacts.add(cid)

    merged = list(primary)
    added = 0
    for item in extra:
        cid, upid = _relation_keys(item)
        if (upid and upid in seen_profiles) or (cid and cid in seen_contacts):
            continue
        if upid:
            seen_profiles.add(upid)
        if cid:
            seen_contacts.add(cid)
        merged.append(item)
        added += 1

    logger.info("merge_shared_relations: %d primary + %d added = %d shared", len(primary), added, len(merged))
    return merged


async def get_exclusive_viewer_relations(
    viewer_circle: List[Dict[str, Any]],
    current_circle: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return the viewer's relations outside the current user's circle, via split_viewer_circle."""
    exclusive, _ = split_viewer_circle(viewer_circle, current_circle)
    return exclusive


async def get_shared_circle_relations(
    viewer_circle: List[Dict[str, Any]],
    current_circle: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return the relations present in both circles, via split_viewer_circle."""
    _, shared = split_viewer_circle(viewer_circle, current_circle)
    return shared


async def get_provider_filtered_relations(
    current_contact_id: str,
    current_user_profile_id: str,
    viewer_contact_id: str,
    viewer_user_profile_id: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (exclusive, shared) viewer relations, with bookers counted as shared and provider, invite and disabled filters applied."""
    t0 = time.perf_counter()

    cur_task = load_both_circles(current_contact_id, current_user_profile_id, current_contact_id, current_user_profile_id)
    vw_task = load_both_circles(viewer_contact_id, viewer_user_profile_id, viewer_contact_id, viewer_user_profile_id)
    booking_task = load_booking_connections(viewer_user_profile_id)
    (cur_relations, _), (vw_relations, _), booking_relations = await asyncio.gather(
        cur_task, vw_task, booking_task
    )

    cur_member_relations = member_relations_only(cur_relations)
    exclusive_relations, shared_relations = split_viewer_circle(
        vw_relations, cur_relations, cur_member_relations
    )

    if booking_relations:
        _, booked_shared = split_viewer_circle(
            booking_relations, cur_relations, cur_member_relations
        )
        shared_relations = merge_shared_relations(shared_relations, booked_shared)

    invite_filtered_relations = await check_invitations_and_filter(
        exclusive_relations,
        current_user_profile_id
    )
    
    final_filtered_relations, final_shared_relations = await asyncio.gather(
        _filter_by_provider_status_for_mutual(invite_filtered_relations),
        _filter_by_provider_status_for_mutual(shared_relations),
    )

    final_filtered_relations, final_shared_relations = await asyncio.gather(
        _drop_disabled_provider_relations(final_filtered_relations),
        _drop_disabled_provider_relations(final_shared_relations),
    )

    logger.info(
        f"get_provider_filtered_relations completed in {(time.perf_counter() - t0) * 1000:.1f} ms "
        f"(exclusive={len(final_filtered_relations)}, shared={len(final_shared_relations)})"
    )
    return final_filtered_relations, final_shared_relations
_MUTUAL_SUGGESTION_CACHE = {}

async def handle_get_mutual_connections(
    current_contact_id: str,
    current_user_profile_id: str,
    viewer_contact_id: str,
    viewer_user_profile_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return formatted suggestions and mutual_connections between two users, excluding both of them and caching for 60 seconds."""
    t0 = time.perf_counter()

    cache_key = (current_contact_id, current_user_profile_id, viewer_contact_id, viewer_user_profile_id)
    now = time.time()
    if cache_key in _MUTUAL_SUGGESTION_CACHE:
        entry = _MUTUAL_SUGGESTION_CACHE[cache_key]
        if now - entry['timestamp'] < 60:
            logger.info("Cache hit for mutual connections")
            return {
                "suggestions": list(entry['data'].get("suggestions", [])),
                "mutual_connections": list(entry['data'].get("mutual_connections", [])),
            }
        else:
            del _MUTUAL_SUGGESTION_CACHE[cache_key]

    empty_result = {"suggestions": [], "mutual_connections": []}

    filtered_relations, shared_relations = await get_provider_filtered_relations(
        current_contact_id, current_user_profile_id,
        viewer_contact_id, viewer_user_profile_id,
    )
    t1 = time.perf_counter()
    logger.info(f"Mutual suggestions step 1 (filtering) complete in {(t1 - t0) * 1000:.1f} ms")

    if not filtered_relations and not shared_relations:
        return empty_result

    def _profile_ids(relations: List[Dict[str, Any]]) -> set:
        return {upid for _, upid in map(_relation_keys, relations) if upid}

    suggestion_profile_ids = _profile_ids(filtered_relations)
    mutual_profile_ids = _profile_ids(shared_relations)

    if not suggestion_profile_ids and not shared_relations:
        return empty_result

    task_users = asyncio.create_task(fetch_details_for_relations(list(suggestion_profile_ids)))
    task_providers = asyncio.create_task(fetch_provider_details_by_user_profile_ids(list(suggestion_profile_ids)))
    task_mutual = asyncio.create_task(fetch_mutual_connection_details(shared_relations))

    users, providers, mutual_rows = await asyncio.gather(task_users, task_providers, task_mutual)
    t2 = time.perf_counter()
    logger.info(f"Mutual suggestions step 2 (fetch details) complete in {(t2 - t1) * 1000:.1f} ms. Found {len(users)} members and {len(providers)} providers.")

    def _key(value: Any) -> Optional[str]:
        """Normalise a formatted profile's id the same way _relation_keys does."""
        return value.strip().lower() if isinstance(value, str) and value.strip() else None

    try:
        users_json = json.loads(format_user_contact_details_to_json(users)) if users else []
        providers_json = json.loads(format_provider_details_to_json(providers)) if providers else []
        provider_badges = await resolve_provider_badges(
            p.get("Etown_userprofileid") for p in providers_json
        )
        for provider in providers_json:
            provider["Etown_providertype"] = premium_display(
                provider.get("Etown_providertype"),
                (provider_badges.get(provider.get("Etown_userprofileid")) or {}).get("premium_label"),
            )

        mutual_json = json.loads(format_user_contact_details_to_json(mutual_rows)) if mutual_rows else []

        combined = users_json + providers_json
        deduplicated_profiles = deduplicate_users_by_contact_id(combined)

        t3 = time.perf_counter()

        contact_ids_to_exclude = {_key(current_contact_id), _key(viewer_contact_id)} - {None}
        user_profile_ids_to_exclude = {_key(current_user_profile_id), _key(viewer_user_profile_id)} - {None}

        final_suggestions: List[Dict[str, Any]] = []
        final_mutual_connections: List[Dict[str, Any]] = []

        def _is_self(item: Dict[str, Any]) -> bool:
            profile_key = _key(item.get("Etown_userprofileid"))
            contact_key = _key(item.get("Etown_contactid"))
            return bool(
                (contact_key and contact_key in contact_ids_to_exclude) or
                (profile_key and profile_key in user_profile_ids_to_exclude)
            )

        for item in mutual_json:
            if _is_self(item):
                continue
            final_mutual_connections.append(
                {**item, "Etown_isconnected": True, "Etown_AlreadyCircle": True}
            )

        mutual_contact_keys = {_key(i.get("Etown_contactid")) for i in final_mutual_connections} - {None}

        for item in deduplicated_profiles:
            if _is_self(item):
                continue
            if _key(item.get("Etown_contactid")) in mutual_contact_keys:
                continue
            final_suggestions.append(item)
        
        logger.info(f"Mutual suggestions step 3 (format/dedupe) complete in {(t3 - t2) * 1000:.1f} ms")
        logger.info(
            f"handle_get_mutual_connections complete. Total time: {(t3 - t0) * 1000:.1f} ms. "
            f"Returning {len(final_suggestions)} suggestions and {len(final_mutual_connections)} mutual connections."
        )
        
        result = {
            "suggestions": final_suggestions,
            "mutual_connections": final_mutual_connections,
        }

        _MUTUAL_SUGGESTION_CACHE[cache_key] = {
            'timestamp': time.time(),
            'data': {
                "suggestions": list(final_suggestions),
                "mutual_connections": list(final_mutual_connections),
            }
        }
        
        return result

    except Exception as e:
        logger.error(f"Error formatting/deduplicating mutual suggestions: {e}", exc_info=True)
        return {"suggestions": [], "mutual_connections": []}


async def get_search_suggestions_paginated(
    current_contact_id: str, 
    current_user_profile_id: str,
    page: int = 1,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None
):
    """Return one page of up to 20 member and 20 distance-sorted provider suggestions outside the caller's circle, cached 60 seconds."""
    t0 = time.perf_counter()
    
    lat_key = f"{latitude:.3f}" if latitude is not None else None
    lon_key = f"{longitude:.3f}" if longitude is not None else None
    cache_key = (current_contact_id, current_user_profile_id, page, lat_key, lon_key)
    now = time.time()
    if cache_key in _SUGGESTION_CACHE:
        entry = _SUGGESTION_CACHE[cache_key]
        if now - entry['timestamp'] < 60:
            logger.info("Cache hit for suggestion search")
            res = dict(entry['data'])
            res["_timings"] = {"cache_hit": True, "total": 0.0}
            return res
        else:
            del _SUGGESTION_CACHE[cache_key]
            
    limit = 20
    offset = (page - 1) * limit
    
    logger.info(f"[PAGINATED SUGGESTION LOG] Page {page}, fetching {limit} members and {limit} providers with offset {offset}")
    
    async def get_members_task():
        t1_start = time.perf_counter()
        master_exclusion_set = {current_user_profile_id}

        first_degree_relations, _ = await load_both_circles(current_contact_id, current_user_profile_id, current_contact_id, current_user_profile_id)
        for rel in first_degree_relations:
            upid = rel.get("from_userprofile") or rel.get("fromuserprofileid")
            if upid:
                master_exclusion_set.add(upid)
        t_first = time.perf_counter()

        first_degree_profile_ids = [
            rel.get("from_userprofile") or rel.get("fromuserprofileid")
            for rel in first_degree_relations
        ]
        second_degree_relations = await fetch_connections_for_profile_ids(first_degree_profile_ids)
        t_second = time.perf_counter()

        SAMPLE_SIZE_FOR_3RD_DEGREE = 50
        second_degree_sample_ids = [
            rel.get("from_userprofile") or rel.get("fromuserprofileid")
            for rel in second_degree_relations[:SAMPLE_SIZE_FOR_3RD_DEGREE]
        ]
        third_degree_relations = await fetch_connections_for_profile_ids(second_degree_sample_ids)
        t_third = time.perf_counter()

        ranked_relations = []
        seen_ranked_upids = set()

        for relation in second_degree_relations + third_degree_relations:
            suggestion_upid = relation.get("from_userprofile") or relation.get("fromuserprofileid")
            if suggestion_upid and suggestion_upid not in seen_ranked_upids and suggestion_upid not in master_exclusion_set:
                ranked_relations.append(relation)
                seen_ranked_upids.add(suggestion_upid)

        master_exclusion_set.update(seen_ranked_upids)
        needed = offset + limit

        collected_members = []
        seen_member_upids = set()

        async def ingest(relations):
            """Fetch details for `relations`, keep only valid members, append in order (deduped)."""
            if not relations:
                return
            def _order_key(value: Any) -> Optional[str]:
                return value.strip().lower() if isinstance(value, str) and value.strip() else None

            order_map = {
                _order_key(rel.get("from_userprofile") or rel.get("fromuserprofileid")): i
                for i, rel in enumerate(relations)
            }
            detailed_profiles = await fetch_details_for_relations(
                relations,
                current_user_profile_id=current_user_profile_id,
                current_contact_id=current_contact_id
            )
            detailed_profiles.sort(key=lambda p: order_map.get(_order_key(p.get('userprofileid')), float('inf')))
            formatted_profiles = json.loads(format_user_contact_details_to_json(detailed_profiles))
            for m in deduplicate_by_user_profile_id(formatted_profiles):
                upid = m.get("Etown_userprofileid")
                if (upid and m.get("Etown_firstname") and
                        m.get("Etown_role") == 2 and
                        upid != current_user_profile_id and
                        m.get("Etown_contactid") != current_contact_id and
                        not m.get("Etown_AlreadyCircle") and
                        not m.get("Etown_AlreadyConnected") and
                        upid not in seen_member_upids):
                    seen_member_upids.add(upid)
                    collected_members.append(m)

        DETAIL_CHUNK = max(needed + limit, 60)
        idx = 0
        while idx < len(ranked_relations) and len(collected_members) < needed:
            await ingest(ranked_relations[idx:idx + DETAIL_CHUNK])
            idx += DETAIL_CHUNK

        fallback_exclusion = set(master_exclusion_set)
        while len(collected_members) < needed:
            batch = min(1000, max(200, (needed - len(collected_members)) * 3))
            fallback = await fetch_all_profiles_sorted_by_oldest(
                excluded_ids=fallback_exclusion,
                limit=batch
            )
            if not fallback:
                break
            for rel in fallback:
                uid = rel.get("from_userprofile") or rel.get("fromuserprofileid")
                if uid:
                    fallback_exclusion.add(uid)
            before = len(collected_members)
            await ingest(fallback)
            if len(fallback) < batch and len(collected_members) == before:
                break
        t_fallback = time.perf_counter()

        members_list = collected_members[offset:offset + limit]
        t_details = time.perf_counter()
        t_filter = t_fallback
        return members_list, (t1_start, t_first, t_second, t_third, t_fallback, t_filter, t_details)

    async def get_providers_task():
        providers_list = []
        database = Database()
        t_prov_start = time.perf_counter()
        
        if latitude is not None and longitude is not None:
            provider_query = """
            WITH user_circle_profiles AS (
                SELECT to_userprofile AS profile_id FROM etown_memberrelation WHERE from_userprofile = $3
                UNION SELECT from_userprofile FROM etown_memberrelation WHERE to_userprofile = $3
                UNION SELECT touserprofileid FROM etown_providerrelation WHERE fromuserprofileid = $3
                UNION SELECT fromuserprofileid FROM etown_providerrelation WHERE touserprofileid = $3
            ),
            user_connected_contacts AS (
                SELECT to_contactid AS contact_id FROM etown_memberrelation WHERE from_contactid = $4
                UNION SELECT from_contactid FROM etown_memberrelation WHERE to_contactid = $4
                UNION SELECT tocontactid FROM etown_providerrelation WHERE fromcontactid = $4
                UNION SELECT fromcontactid FROM etown_providerrelation WHERE tocontactid = $4
            ),
            pending_invites AS (
                SELECT invitecontact FROM etown_invitation
                WHERE inviteruserprofile = $3 AND invitationcode = 'Requested'
                AND invitecontact IS NOT NULL
            )
            SELECT
                p.*,
                c.mobilephone,
                c.email,
                COALESCE(r.total_bookings, 0) AS memberbookingcount,
                COALESCE(r.total_ratings, 0) AS ratingcount,
                COALESCE(r.avg_overall_rating, 0.0) AS overallrating,
                (ucp.profile_id IS NOT NULL) AS already_circle,
                (ucc.contact_id IS NOT NULL) AS already_connected,

                CASE WHEN COALESCE(p.deleted, 'false') = 'true'
                       OR COALESCE(c.deleted, 'false') = 'true'
                     THEN 'true' ELSE 'false' END AS lifecycle_deleted,
                CASE WHEN (p.deactivated = 'true' AND p.reactivation IS DISTINCT FROM 'true')
                       OR (c.deactivated = 'true' AND c.reactivation IS DISTINCT FROM 'true')
                     THEN 'true' ELSE 'false' END AS lifecycle_deactivated,
                (
                    6371 * acos(
                        LEAST(GREATEST(
                            cos(radians($1))
                            * cos(radians(p.latitude))
                            * cos(radians(p.longitude) - radians($2))
                            + sin(radians($1)) * sin(radians(p.latitude))
                        , -1), 1)
                    )
                ) AS distance_km
            FROM public.etown_provider p
            LEFT JOIN public.etown_contact c ON p.contactid = c.contactid
            LEFT JOIN (
                SELECT
                    provideruserprofileid,
                    SUM(memberbookingcount) as total_bookings,
                    SUM(ratingcount) as total_ratings,
                    AVG(overallrating) as avg_overall_rating
                FROM etown_ratings
                GROUP BY provideruserprofileid
            ) r ON p.userprofileid = r.provideruserprofileid
            LEFT JOIN user_circle_profiles ucp ON ucp.profile_id = p.userprofileid
            LEFT JOIN user_connected_contacts ucc ON ucc.contact_id = p.contactid
            WHERE p.latitude IS NOT NULL AND p.longitude IS NOT NULL
              AND p.userprofileid != $3
              AND p.contactid != $4

              AND p.deleted IS DISTINCT FROM 'true'
              AND c.deleted IS DISTINCT FROM 'true'

              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
              AND (c.deactivated IS DISTINCT FROM 'true' OR c.reactivation = 'true')
              AND p.disabled IS NOT TRUE
              """ + _EXCLUDE_REVIEW_PENDING_P + """
              AND p.contactid NOT IN (SELECT invitecontact FROM pending_invites)

              AND ucp.profile_id IS NULL
              AND ucc.contact_id IS NULL
            ORDER BY distance_km ASC
            LIMIT $5 OFFSET $6;
"""
            
            try:
                provider_results = await database.fetch(
                    provider_query, 
                    latitude, 
                    longitude, 
                    current_user_profile_id,
                    current_contact_id,
                    limit, 
                    offset
                )
                
                badge_map = await resolve_provider_badges(
                    row.get('userprofileid') for row in provider_results
                )
                for row in provider_results:
                    created_on = row.get('creationdate')
                    created_iso = created_on.isoformat() + 'Z' if isinstance(created_on, datetime) else None

                    already_circle = bool(row.get("already_circle")) if row.get("already_circle") is not None else False
                    already_connected = bool(row.get("already_connected")) if row.get("already_connected") is not None else False
                    formatted_provider = {
                        "Etown_userprofileid": row.get('userprofileid'),
                        "Etown_role": 1,
                        "Etown_rolename": "Provider",
                        "Etown_firstname": row.get('businessname'),
                        "Etown_contactid": row.get('contactid'),
                        "Etown_isconnected": already_circle or already_connected, 
                        "Etown_AlreadyCircle": already_circle,
                        "Etown_AlreadyConnected": already_connected,
                        "Etown_mobilenumber": row.get('mobilephone'),
                        "Etown_email": row.get('email'),
                        "Etown_profileimage": row.get('serviceimageurl'),
                        "Etown_createdon": created_iso,
                        "Etown_address": row.get('address'),
                        "Etown_statecode": row.get('statecode'),
                        "Etown_city": row.get('city'),
                        "Etown_latitude": str(row.get('latitude')) if row.get('latitude') is not None else None,
                        "Etown_longitude": str(row.get('longitude')) if row.get('longitude') is not None else None,
                        "Etown_publicsearchprovider": row.get('publicsearchprovider'),
                        "Etown_providertype": premium_display(
                            row.get('providertype'),
                            (badge_map.get(row.get('userprofileid')) or {}).get("premium_label"),
                        ),
                        "Etown_memberbookingcount": int(row.get('memberbookingcount', 0)),
                        "Etown_ratingcount": int(row.get('ratingcount', 0)),
                        "Etown_overallrating": float(row.get('overallrating', 0.0)),
                        "Etown_distance_km": float(row.get('distance_km')) if row.get('distance_km') is not None else None,
                        "Etown_deleted": row.get('lifecycle_deleted') or "false",
                        "Etown_deactivated": row.get('lifecycle_deactivated') or "false",
                        'Etown_resultType': 1,
                    }
                    providers_list.append(formatted_provider)
                
                logger.info(f"[PAGINATED SUGGESTION LOG] Found {len(providers_list)} local distance-sorted providers")
            except Exception as e:
                logger.error(f"Error fetching distance-based providers: {e}", exc_info=True)
                providers_list = []
        else:
            try:
                provider_fallback_query = """
                WITH user_circle_profiles AS (
                    SELECT to_userprofile AS profile_id FROM etown_memberrelation WHERE from_userprofile = $3
                    UNION SELECT from_userprofile FROM etown_memberrelation WHERE to_userprofile = $3
                    UNION SELECT touserprofileid FROM etown_providerrelation WHERE fromuserprofileid = $3
                    UNION SELECT fromuserprofileid FROM etown_providerrelation WHERE touserprofileid = $3
                ),
                user_connected_contacts AS (
                    SELECT to_contactid AS contact_id FROM etown_memberrelation WHERE from_contactid = $4
                    UNION SELECT from_contactid FROM etown_memberrelation WHERE to_contactid = $4
                    UNION SELECT tocontactid FROM etown_providerrelation WHERE fromcontactid = $4
                    UNION SELECT fromcontactid FROM etown_providerrelation WHERE tocontactid = $4
                ),
                pending_invites AS (
                    SELECT invitecontact FROM etown_invitation
                    WHERE inviteruserprofile = $3 AND invitationcode = 'Requested'
                    AND invitecontact IS NOT NULL
                )
                SELECT
                    p.*,
                    c.mobilephone,
                    c.email,
                    COALESCE(r.total_bookings, 0) AS memberbookingcount,
                    COALESCE(r.total_ratings, 0) AS ratingcount,
                    COALESCE(r.avg_overall_rating, 0.0) AS overallrating,
                    (ucp.profile_id IS NOT NULL) AS already_circle,
                    (ucc.contact_id IS NOT NULL) AS already_connected,

                    CASE WHEN COALESCE(p.deleted, 'false') = 'true'
                           OR COALESCE(c.deleted, 'false') = 'true'
                         THEN 'true' ELSE 'false' END AS lifecycle_deleted,
                    CASE WHEN (p.deactivated = 'true' AND p.reactivation IS DISTINCT FROM 'true')
                           OR (c.deactivated = 'true' AND c.reactivation IS DISTINCT FROM 'true')
                         THEN 'true' ELSE 'false' END AS lifecycle_deactivated
                FROM public.etown_provider p
                LEFT JOIN public.etown_contact c ON p.contactid = c.contactid
                LEFT JOIN (
                    SELECT
                        provideruserprofileid,
                        SUM(memberbookingcount) as total_bookings,
                        SUM(ratingcount) as total_ratings,
                        AVG(overallrating) as avg_overall_rating
                    FROM etown_ratings
                    GROUP BY provideruserprofileid
                ) r ON p.userprofileid = r.provideruserprofileid
                LEFT JOIN user_circle_profiles ucp ON ucp.profile_id = p.userprofileid
                LEFT JOIN user_connected_contacts ucc ON ucc.contact_id = p.contactid
                WHERE p.contactid != $4
                  AND p.userprofileid != $3

                  AND p.deleted IS DISTINCT FROM 'true'
                  AND c.deleted IS DISTINCT FROM 'true'
                  AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
                  AND (c.deactivated IS DISTINCT FROM 'true' OR c.reactivation = 'true')
                  AND p.disabled IS NOT TRUE
                  """ + _EXCLUDE_REVIEW_PENDING_P + """
                  AND p.contactid NOT IN (SELECT invitecontact FROM pending_invites)

                  AND ucp.profile_id IS NULL
                  AND ucc.contact_id IS NULL
                ORDER BY p.creationdate DESC NULLS LAST
                LIMIT $1 OFFSET $2;
"""
                
                provider_results = await database.fetch(
                    provider_fallback_query,
                    limit,
                    offset,
                    current_user_profile_id,
                    current_contact_id
                )
                
                badge_map = await resolve_provider_badges(
                    row.get('userprofileid') for row in provider_results
                )
                for row in provider_results:
                    created_on = row.get('creationdate')
                    created_iso = created_on.isoformat() + 'Z' if isinstance(created_on, datetime) else None

                    already_circle = bool(row.get("already_circle")) if row.get("already_circle") is not None else False
                    already_connected = bool(row.get("already_connected")) if row.get("already_connected") is not None else False
                    formatted_provider = {
                        "Etown_userprofileid": row.get('userprofileid'),
                        "Etown_role": 1,
                        "Etown_rolename": "Provider",
                        "Etown_firstname": row.get('businessname'),
                        "Etown_contactid": row.get('contactid'),
                        "Etown_isconnected": already_circle or already_connected,
                        "Etown_AlreadyCircle": already_circle,
                        "Etown_AlreadyConnected": already_connected,
                        "Etown_mobilenumber": row.get('mobilephone'),
                        "Etown_email": row.get('email'),
                        "Etown_profileimage": row.get('serviceimageurl'),
                        "Etown_createdon": created_iso,
                        "Etown_address": row.get('address'),
                        "Etown_statecode": row.get('statecode'),
                        "Etown_city": row.get('city'),
                        "Etown_latitude": str(row.get('latitude')) if row.get('latitude') is not None else None,
                        "Etown_longitude": str(row.get('longitude')) if row.get('longitude') is not None else None,
                        "Etown_publicsearchprovider": row.get('publicsearchprovider'),
                        "Etown_providertype": premium_display(
                            row.get('providertype'),
                            (badge_map.get(row.get('userprofileid')) or {}).get("premium_label"),
                        ),
                        "Etown_memberbookingcount": int(row.get('memberbookingcount', 0)),
                        "Etown_ratingcount": int(row.get('ratingcount', 0)),
                        "Etown_overallrating": float(row.get('overallrating', 0.0)),
                        "Etown_distance_km": None,
                        "Etown_deleted": row.get('lifecycle_deleted') or "false",
                        "Etown_deactivated": row.get('lifecycle_deactivated') or "false",
                        'Etown_resultType': 1,
                    }
                    providers_list.append(formatted_provider)
                    
                logger.info(f"[PAGINATED SUGGESTION LOG] Found {len(providers_list)} fallback providers")
                
            except Exception as e:
                logger.error(f"Error fetching fallback providers: {e}", exc_info=True)
                providers_list = []
        return providers_list, time.perf_counter() - t_prov_start

    members_res, providers_res = await asyncio.gather(get_members_task(), get_providers_task())
    members_list, member_timings = members_res
    providers_list, provider_duration = providers_res

    if len(member_timings) == 7:
        t1_start, t_first, t_second, t_third, t_fallback, t_filter, t_details = member_timings
        first_deg_time = t_first - t1_start
        second_deg_time = t_second - t_first
        third_deg_time = t_third - t_second
        fallback_time = t_fallback - t_third
        filter_time = t_filter - t_fallback
        details_time = t_details - t_filter
    else:
        first_deg_time, second_deg_time, third_deg_time, fallback_time, filter_time, details_time = 0, 0, 0, 0, 0, 0

    total_ms = (time.perf_counter() - t0) * 1000
    
    timings = {
        "first_deg": first_deg_time * 1000,
        "second_deg": second_deg_time * 1000,
        "third_deg": third_deg_time * 1000,
        "fallback": fallback_time * 1000,
        "filter": filter_time * 1000,
        "member_details": details_time * 1000,
        "provider_query": provider_duration * 1000,
        "total": total_ms
    }

    final_result = {
        "members": members_list, 
        "providers": providers_list, 
        "_status_ready": True,
        "_timings": timings
    }

    _SUGGESTION_CACHE[cache_key] = {
        'timestamp': time.time(),
        'data': {"members": members_list, "providers": providers_list, "_status_ready": True}
    }

    logger.info(
        "get_search_suggestions_paginated timings ms: "
        "first_deg=%.1f second_deg=%.1f third_deg=%.1f fallback=%.1f "
        "filter=%.1f member_details=%.1f provider_query=%.1f total=%.1f",
        timings["first_deg"],
        timings["second_deg"],
        timings["third_deg"],
        timings["fallback"],
        timings["filter"],
        timings["member_details"],
        timings["provider_query"],
        total_ms,
    )
    logger.info(f"Page {page} counts: {len(members_list)} members, {len(providers_list)} providers.")
    
    return final_result
