"""Near Me Utilities - Postgres logic for the distance-ordered list of circle providers.

Domain layer behind GET /api/dashboard/nearme_list: returns the requester's circle providers within 25 km of the supplied coordinates, nearest first, paginated, in the same Etown_-prefixed item shape as GET /api/invitations/mycircle.

Features:
    - Circle-sourced: role 1 reads etown_providerrelation, role 2 etown_memberrelation, NULL both
    - Providers only: entries must own an etown_provider record; disabled providers are skipped
    - 25 km radius: a SQL literal cap; providers without coordinates are excluded
    - One row per circle profile; the nearest provider record wins
    - The requester's own profile and contact are excluded
    - Two concurrent queries off the shared ``_NEARME_CTES`` block: the page and the total
    - Pagination is 1-based in pages of 10; counts cover the whole list, not the page
    - Plan Badges: Rows carry Etown_providertype and the plan's badge label

Classes:
    No classes defined (module exposes helper functions and the shared CTE query strings)

Functions:
    _round_km(): Round a query distance to 2 decimals, tolerating NULL/garbage values
    _format_row(): Map a raw DB row to the mycircle-shaped response item
    get_nearme_list(): Entry point — returns the mycircle-shaped response dict.

Dependencies:
    - asyncio: Runs the page and totals queries concurrently
    - typing: Type hints
    - app.db.client: Async PostgreSQL database access (Database class)
    - app.core.logging: Centralized application logging
    - app.etown.plans.plans_utils: premium_display and resolve_provider_badges

Updates:
    02-Jul-2026: Initial creation — NearMeListAPI migrated from PowerApps flow
    27-Jul-2026: Single query; 1-based paging; circle providers within 25 km by distance.
    28-Jul-2026: Reverted _DEFAULT_LIMIT to 10 rows per page.
    18-Aug-2026: Rows carry Etown_providertype and the subscription plan's Etown_badge_label.
    20-Aug-2026: Verified markers report the paid tier's badge label; circle rows expose Etown_providertype.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    02-Sep-2026: Disabled providers (etown_provider.disabled) are skipped, matching /mycircle.
"""

import asyncio
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.db.client import Database
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 200


_NEARME_CTES = """
WITH params AS (
    SELECT
        $1::text             AS me_userprofileid,
        $2::text             AS me_contactid,
        $3::double precision AS ref_lat,
        $4::double precision AS ref_lon
),
me AS (
    SELECT up.userprofileid, up.contactid, up.role
    FROM etown_userprofile up
    CROSS JOIN params prm
    WHERE up.userprofileid = prm.me_userprofileid
),
circle AS (

    SELECT DISTINCT ON (circle_userprofileid)
        circle_userprofileid,
        providerrelationid,
        memberrelationid,
        favorite,
        favoritename,
        relationshiptype,
        relationshiptypename,
        invite_relationshiptype,
        invite_relationshiptypename
    FROM (
        SELECT
            pr.touserprofileid AS circle_userprofileid,
            pr.providerrelationid,
            NULL::text         AS memberrelationid,
            pr.favorite,
            pr.favoritename,
            pr.relationshiptype,
            pr.relationshiptypename,
            pr.invite_relationshiptype,
            pr.invite_relationshiptypename,
            1                  AS source_rank
        FROM etown_providerrelation pr
        CROSS JOIN me
        WHERE pr.fromuserprofileid = me.userprofileid
          AND pr.fromcontactid     = me.contactid
          AND pr.touserprofileid IS NOT NULL

          AND pr.touserprofileid IS DISTINCT FROM me.userprofileid
          AND (me.role = 1 OR me.role IS NULL)
        UNION ALL
        SELECT
            mr.to_userprofile,
            NULL::text,
            mr.memberrelationid,
            mr.favorite,
            mr.favoritename,
            mr.relationshiptype,
            mr.relationshiptypename,
            mr.invite_relationshiptype,
            mr.invite_relationshiptypename,
            2
        FROM etown_memberrelation mr
        CROSS JOIN me
        WHERE mr.from_userprofile = me.userprofileid
          AND mr.from_contactid   = me.contactid
          AND mr.to_userprofile IS NOT NULL
          AND mr.to_userprofile IS DISTINCT FROM me.userprofileid
          AND (me.role = 2 OR me.role IS NULL)
    ) rel

    ORDER BY circle_userprofileid, source_rank
),
circle_provider AS (
    SELECT
        cir.providerrelationid,
        cir.memberrelationid,
        cir.favorite,
        cir.favoritename,
        cir.relationshiptype,
        cir.relationshiptypename,
        cir.invite_relationshiptype,
        cir.invite_relationshiptypename,
        p.*
    FROM circle cir
    CROSS JOIN params prm

    JOIN LATERAL (
        SELECT
            p2.providerid,
            p2.userprofileid AS provider_userprofileid,
            p2.contactid     AS provider_contactid,
            p2.contactidname,
            p2.businessname,
            p2.yearsinbusiness,
            p2.serviceimageurl,
            p2.email         AS provider_email,
            p2.mobilenumber  AS provider_mobile,
            p2.address       AS provider_address,
            p2.city          AS provider_city,
            p2.statecode     AS provider_statecode,
            p2.latitude      AS provider_latitude,
            p2.longitude     AS provider_longitude,
            p2.publicsearchprovider,
            p2.providertype,
            p2.creationdate,
            p2.role          AS provider_role,
            p2.rolename      AS provider_rolename,
            CASE
                WHEN p2.latitude IS NULL OR p2.longitude IS NULL THEN NULL
                ELSE (
                    6371 * acos(LEAST(1.0, GREATEST(-1.0,
                        cos(radians(prm.ref_lat)) * cos(radians(p2.latitude)) *
                        cos(radians(p2.longitude) - radians(prm.ref_lon)) +
                        sin(radians(prm.ref_lat)) * sin(radians(p2.latitude))
                    )))
                )
            END AS provider_distance
        FROM etown_provider p2
        WHERE p2.userprofileid = cir.circle_userprofileid

          AND p2.deleted IS DISTINCT FROM 'true'
          AND (p2.deactivated IS DISTINCT FROM 'true' OR p2.reactivation = 'true')
          AND p2.disabled IS NOT TRUE
        ORDER BY provider_distance ASC NULLS LAST, p2.providerid
        LIMIT 1
    ) p ON TRUE

    WHERE p.provider_contactid IS DISTINCT FROM prm.me_contactid

      AND p.provider_distance <= 25
)
"""

_NEARME_QUERY = _NEARME_CTES + """
SELECT
    me.role AS requester_role,

    n.providerid,
    n.provider_userprofileid,
    n.provider_contactid,
    n.contactidname,
    n.businessname,
    n.yearsinbusiness,
    n.serviceimageurl,
    n.provider_email,
    n.provider_mobile,
    n.provider_address,
    n.provider_city,
    n.provider_statecode,
    n.provider_latitude,
    n.provider_longitude,
    n.publicsearchprovider,
    n.providertype,
    n.creationdate,
    n.provider_role,
    n.provider_rolename,
    n.provider_distance,

    up.role          AS target_role,
    up.rolename      AS target_rolename,
    up.userprofileid AS target_userprofileid,

    c.contactid,
    c.firstname,
    c.mobilephone,
    c.profileimage,
    c.connectycubeid,
    c.address   AS contact_address,
    c.city      AS contact_city,
    c.statecode AS contact_statecode,
    c.latitude  AS contact_latitude,
    c.longitude AS contact_longitude,
    c.createdon AS contact_createdon,
    c.verified  AS contact_verified,

    CASE
        WHEN c.latitude IS NULL OR c.longitude IS NULL THEN NULL
        ELSE (
            6371 * acos(LEAST(1.0, GREATEST(-1.0,
                cos(radians(prm.ref_lat)) * cos(radians(c.latitude)) *
                cos(radians(c.longitude) - radians(prm.ref_lon)) +
                sin(radians(prm.ref_lat)) * sin(radians(c.latitude))
            )))
        )
    END AS member_distance,

    cd.ccuserid,

    n.providerrelationid,
    n.memberrelationid,
    n.favorite,
    n.favoritename,
    n.relationshiptype,
    n.relationshiptypename,
    n.invite_relationshiptype,
    n.invite_relationshiptypename
FROM circle_provider n
CROSS JOIN params prm
LEFT JOIN me ON TRUE
LEFT JOIN etown_userprofile up
    ON up.userprofileid = n.provider_userprofileid
LEFT JOIN etown_contact c
    ON c.contactid = COALESCE(up.contactid, n.provider_contactid)
LEFT JOIN LATERAL (
    SELECT cd2.ccuserid
    FROM etown_contactdevices cd2
    WHERE cd2.userprofileid = n.provider_userprofileid
    ORDER BY (cd2.ccuserid IS NOT NULL) DESC, cd2.contactdevicesid
    LIMIT 1
) cd ON TRUE

ORDER BY n.provider_distance ASC, n.providerid
LIMIT $5::int
OFFSET $6::int
"""

_NEARME_COUNT_QUERY = _NEARME_CTES + """
SELECT
    (SELECT role FROM me) AS requester_role,
    COUNT(*)              AS total_count
FROM circle_provider
"""


def _round_km(value: Any) -> Optional[float]:
    """Round a kilometre distance to 2 decimals, returning None when it is absent or unparseable."""
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        logger.debug("nearme: distance %r is not a number; returning null", value)
        return None


def _format_row(row: Any, badge_map: Dict[str, Dict[str, Optional[str]]]) -> Dict[str, Any]:
    """Map a near-me row to the mycircle Etown_ item shape, preferring provider columns and applying its badge."""
    userprofileid = row["target_userprofileid"] or row["provider_userprofileid"]
    badge = badge_map.get(userprofileid) or {}
    premium = badge.get("premium_label")
    created_on = row["creationdate"] or row["contact_createdon"]

    return {
        "Etown_favoritename": row["favoritename"],
        "Etown_favorite": row["favorite"],
        "Etown_relationshiptypename": row["invite_relationshiptypename"] or row["relationshiptypename"],
        "Etown_relationshiptype": row["relationshiptype"],
        "Etown_providerrelationid": row["providerrelationid"],
        "Etown_memberrelationid": row["memberrelationid"],
        "Etown_contactlongitude": row["contact_longitude"],
        "Etown_contactlatitude": row["contact_latitude"],
        "Etown_connectycubeid": row["connectycubeid"],
        "Etown_contactaddress": row["contact_address"],
        "Etown_contactid": row["contactid"] or row["provider_contactid"],
        "Etown_firstname": row["businessname"] or row["contactidname"] or row["firstname"],
        "Etown_contactcreatedon": str(created_on) if created_on else None,
        "Etown_mobilenumber": row["mobilephone"] or row["provider_mobile"],
        "Etown_businessname": row["businessname"],
        "Etown_provideraddress": row["provider_address"],
        "Etown_providerlatitude": row["provider_latitude"],
        "Etown_providerlongitude": row["provider_longitude"],
        "Etown_providername": row["contactidname"],
        "Etown_providercontact": row["provider_contactid"],
        "Etown_providerid": row["providerid"],
        "Etown_rolename": row["target_rolename"] or row["provider_rolename"] or "Provider",
        "Etown_role": row["target_role"] if row["target_role"] is not None else (row["provider_role"] or 1),
        "Etown_providercreatedon": str(row["creationdate"]) if row["creationdate"] else None,
        "Etown_yearinbusiness": row["yearsinbusiness"],
        "Etown_provideremail": row["provider_email"],
        "Etown_userprofileid": userprofileid,
        "Etown_imageurl": row["serviceimageurl"] or row["profileimage"],
        "Etown_publicsearchprovider": row["publicsearchprovider"] or row["providertype"],
        "Etown_address": row["provider_address"] or row["contact_address"],
        "Etown_city": row["provider_city"] or row["contact_city"],
        "Etown_statecode": row["provider_statecode"] or row["contact_statecode"],
        "Etown_memberdistance": _round_km(row["member_distance"]),
        "Etown_providerdistance": _round_km(row["provider_distance"]),
        "Etown_ccUserID": row["ccuserid"],
        "Etown_InviteRelationShipType": row["invite_relationshiptype"],
        "Etown_InviteRelationShipTypeName": row["invite_relationshiptypename"],
        "Etown_userverified": row["contact_verified"],
        "Etown_providertype": premium_display(row["providertype"], premium),
        "Etown_badge_label": badge.get("badge_label"),
    }


async def get_nearme_list(
    inviter_id: str,
    inviteruserprofile_id: str,
    inviter_latitude: str,
    inviter_longitude: str,
    page: int = 1,
    limit: int = _DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Return one page of the requester's circle providers within 25 km, nearest first, with the total count.

    Args:
        inviter_id: Contact ID of the requesting user.
        inviteruserprofile_id: User profile ID of the requesting user.
        inviter_latitude: Requester's latitude as a string.
        inviter_longitude: Requester's longitude as a string.
        page: 1-based page number; values below 1 clamp to 1.
        limit: Rows per page, clamped to 1.._MAX_LIMIT.

    Returns:
        Dict with page, page_size, has_more, data_count, provider_count and data.

    Raises:
        ValueError: If inviter_latitude or inviter_longitude cannot be parsed as floats.
    """
    try:
        ref_lat = float(inviter_latitude)
        ref_lon = float(inviter_longitude)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid coordinates: latitude={inviter_latitude!r}, longitude={inviter_longitude!r}"
        ) from exc

    page_size = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    page_number = max(1, int(page or 1))
    offset = (page_number - 1) * page_size

    rows, count_rows = await asyncio.gather(
        Database.fetch(
            _NEARME_QUERY,
            inviteruserprofile_id,
            inviter_id,
            ref_lat,
            ref_lon,
            page_size,
            offset,
        ),
        Database.fetch(
            _NEARME_COUNT_QUERY,
            inviteruserprofile_id,
            inviter_id,
            ref_lat,
            ref_lon,
        ),
    )

    total_count = int(count_rows[0]["total_count"]) if count_rows else 0
    requester_role = count_rows[0]["requester_role"] if count_rows else None

    badge_map = await resolve_provider_badges(
        row["target_userprofileid"] or row["provider_userprofileid"] for row in rows
    )
    data: List[Dict[str, Any]] = [_format_row(row, badge_map) for row in rows]
    has_more = offset + len(data) < total_count

    logger.info(
        f"NearMeList | userprofileid={inviteruserprofile_id} | role={requester_role} "
        f"| page={page_number} | limit={page_size} | offset={offset} "
        f"| results={len(data)} | total={total_count} | has_more={has_more}"
    )

    return {
        "page": page_number,
        "page_size": page_size,
        "has_more": has_more,
        "data_count": total_count,
        "provider_count": total_count,
        "data": data,
    }
