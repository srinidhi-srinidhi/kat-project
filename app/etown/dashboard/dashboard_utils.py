"""Dashboard Utilities - Postgres aggregation for provider and member dashboard screens.

Domain layer behind /api/dashboard: assembles a provider's profile, stats, plan badges, towns, offers, feed posts and social insights totals, plus a member's name and pending invite count, returning formatted dicts with safe fallbacks on error.

Features:
    - Provider Details Aggregation: profile, services, ratings, booking stats and plan badges
    - Provider Towns: linked towns with provider, need and new-need counts, home town first, capped at plan max_towns
    - Offers Retrieval: Fetches provider's active and historical offers list
    - Feed Details Retrieval: Fetches provider feed posts with like and comment counts
    - Member Dashboard Summary: a member's display name and pending invite request count
    - Social Insights Totals: latest Instagram + Facebook engagement totals and connection flags
    - Date Formatting: ISO 8601 datetime string normalization for all timestamp fields
    - Error Handling: Graceful error logging with safe fallback return values

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    _fmt_dt(): Format a datetime value to ISO 8601 string or return None
    fetch_provider_details(): Fetch aggregated provider profile details and stats
    fetch_provider_towns(): Fetch the towns a provider is listed in, home town first
    fetch_offers(): Fetch provider's offers list with status and metadata
    fetch_feed_details(): Fetch provider feed posts with like and comment counts
    fetch_member_dashboard(): Fetch a member's name and pending invite request count
    fetch_social_insights_totals(): Aggregate a provider's social engagement and connection status

Dependencies:
    - asyncio: concurrent sub-queries
    - json: JSON data serialization for structured response fields
    - datetime: Datetime formatting and ISO string conversion
    - typing: Type hints for complex data structures
    - app.core.logging: Centralized application logging
    - app.db.client: Async PostgreSQL database access
    - app.etown.plans.plans_utils: plan resolution, max_towns and badge labels

Updates:
    26-May-2026: Added full module title, Features, Classes, Dependencies, Updates sections to complete module docstring
    28-Jul-2026: Deduplicated circle rows from the provider_relation device join.
    20-Aug-2026: Verified markers report the paid tier's badge label.
    21-Aug-2026: provider_relation drops deleted counterparties from Etown_mycirclecount.
    25-Aug-2026: Added fetch_provider_towns for the dashboard town list; drops towns with no providers.
    01-Sep-2026: Etown_viewcount drops self, anonymous and deleted viewers; fetch_provider_towns falls back to own town.
    02-Sep-2026: fetch_provider_towns capped at the plan's max_towns; counts skip inactive providers.
    07-Oct-2026: fetch_provider_towns adds each town's need_count and new_need_count since the last community visit.
    20-Oct-2026: new_need_count counts from the last community visit to needs.
"""

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.db.client import Database
from app.etown.plans.plans_utils import premium_display, resolve_plan, resolve_provider_badges


def _fmt_dt(value: Any) -> Optional[str]:
    """Return a datetime as an ISO string with a Z suffix, other values as str, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    return str(value)


async def fetch_provider_details(contactid: str, userprofileid: str) -> Dict[str, Any]:
    """Return the provider's dashboard details, ratings, request tallies, circle members and pending invitations as Etown_ fields."""
    provider_query = """
        WITH

        provider_details AS (
            SELECT
                p.providerid,
                p.contactid,
                p.userprofileid,
                p.businessname,
                p.additionaladdressinfo,
                p.address,
                p.availability,
                p.mobilenumber,
                p.description,
                p.latitude,
                p.servicetype,
                p.longitude,
                p.statecode,
                p.providertype,
                p.publicsearchprovider,
                p.city,
                p.creationdate,
                p.serviceimageurl,
                r.ratingid,
                r.overallrating,
                r.memberbookingcount
            FROM etown_provider p
            LEFT OUTER JOIN etown_ratings r
                ON r.provideruserprofileid = p.userprofileid
            WHERE
                p.contactid     = $1
                AND p.userprofileid = $2
        ),

        service_counts AS (
            SELECT
                rsl.serviceprovideruserprofileid,
                COUNT(CASE WHEN requeststatus = 'completed' THEN 1 END) AS completedservices,
                COUNT(CASE WHEN requeststatus = 'accepted'  THEN 1 END) AS inprogressservices,
                COUNT(CASE WHEN requeststatus = 'requested' THEN 1 END) AS requestedservices
            FROM etown_allrequests ar
   inner join public.etown_requestservicelists rsl
   on rsl.bookingid=ar.allrequestid
            WHERE
                rsl.serviceprovideruserprofileid = (SELECT userprofileid FROM provider_details LIMIT 1)
                AND requeststatus IN ('completed', 'accepted', 'requested')
            GROUP BY rsl.serviceprovideruserprofileid
        ),

        provider_relation AS (
            SELECT
                pr.favorite,
                pr.relationshiptype,
                pr.providerrelationid,
                up.role,
                up.userprofileid,
                c.firstname,
                c.email,
                c.rolecode,
                c.mobilephone,
                c.addresscomp,
                c.additionaladdressinfo,
                c.contactid,
                c.profileimage,
                c.latitude,
                c.longitude,
                c.city,
                c.statecode,
                c.createdon,
                p.address               AS provider_address,
                p.businessname          AS provider_businessname,
                p.businesscategory      AS provider_businesscategory,
                p.yearsinbusiness       AS provider_yearsinbusiness,
                p.serviceimageurl       AS provider_serviceimageurl,
                p.email                 AS provider_email,
                p.mobilenumber          AS provider_mobile,
                p.latitude              AS provider_latitude,
                p.longitude             AS provider_longitude,
                p.contactid             AS provider_contact_id,
                p.userprofileid         AS provider_userprofileid,
                p.providerid            AS provider_providerid,
                p.publicsearchprovider  AS provider_publicsearchprovider,
                p.city                  AS provider_city,
                p.statecode             AS provider_statecode,
                p.creationdate          AS provider_createdon,
                cd.ccuserid             AS contact_devices_ccuserid
            FROM etown_providerrelation pr
            INNER JOIN etown_userprofile up
                ON up.userprofileid = pr.touserprofileid
            INNER JOIN etown_contact c
                ON c.contactid = up.contactid
            LEFT OUTER JOIN etown_provider p
                ON p.userprofileid = up.userprofileid

            LEFT JOIN LATERAL (
                SELECT cd2.ccuserid
                FROM etown_contactdevices cd2
                WHERE cd2.userprofileid = up.userprofileid
                  AND cd2.ccuserid IS NOT NULL
                ORDER BY cd2.updatedon DESC NULLS LAST, cd2.createdon DESC NULLS LAST, cd2.contactdevicesid
                LIMIT 1
            ) cd ON TRUE
            WHERE
                pr.fromcontactid        = $1
                AND pr.fromuserprofileid = $2
                AND NOT etown_is_deleted(c.contactid)
        ),

        circle_count AS (
            SELECT COUNT(*) AS mycirclecount
            FROM provider_relation
        ),

        invitation_data AS (
            SELECT
                ei.invitationid,
                ei.invitercontact,
                ei.invitecontact,
                ei.invitationcode,
                ei.invite_role,
                ei.invitecontactmobilenumber,
                ei.invitecontactemail,
                ei.invitationtype,
                ei.inviteruserprofile,
                ei.isnotification,
                ei.inviteuserprofile,
                ei.createdon,
                ec.contactid,
                ec.firstname,
                ec.email,
                ec.mobilephone,
                ec.city,
                ec.profileimage,
                ec.publicsearchstatus,
                ec.createdon            AS contact_createdon,
                eup.userprofileid,
                eup.role,
                eup.rolename,
                eup.name                AS profilename,
                eup.email               AS profileemail,
                ep.providerid,
                ep.businessname,
                ep.businesscategory,
                ep.address,
                ep.city                 AS providercity,
                ep.statecode,
                ep.yearsinbusiness,
                ep.serviceimageurl,
                ep.email                AS provideremail,
                ep.mobilenumber,
                ep.latitude,
                ep.longitude,
                ep.publicsearchprovider,
                ep.creationdate         AS provider_createdon
            FROM etown_invitation ei
            INNER JOIN etown_contact ec
                ON ei.invitercontact = ec.contactid
            INNER JOIN etown_userprofile eup
                ON ei.inviteruserprofile = eup.userprofileid
            LEFT JOIN etown_provider ep
                ON eup.userprofileid = ep.userprofileid
            WHERE
                ei.invitecontact        = $1
                AND ei.inviteuserprofile = $2
                AND ei.invitecontact    IS NOT NULL
                AND ei.invitationcode   = 'Requested'
        ),

        invite_count AS (
            SELECT COUNT(*) AS invitecount
            FROM invitation_data
        ),

        view_count AS (
            SELECT COUNT(DISTINCT v.userprofileid) AS viewcount
            FROM etown_provider_views v
            LEFT JOIN etown_userprofile up ON up.userprofileid = v.userprofileid
            WHERE v.providerprofileid = (SELECT userprofileid FROM provider_details LIMIT 1)
              AND v.userprofileid IS NOT NULL
              AND v.userprofileid <> v.providerprofileid
              AND NOT etown_is_deleted(v.userprofileid)
              AND NOT etown_is_deleted(up.contactid)
        )

        SELECT
            pd.providerid               AS etown_providerid,
            pd.businessname             AS etown_businessname,
            pd.additionaladdressinfo    AS etown_address,
            pd.address                  AS etown_fetchaddress,
            pd.description              AS etown_description,
            pd.availability             AS etown_availability,
            pd.latitude                 AS etown_latitude,
            pd.longitude                AS etown_longitude,
            pd.serviceimageurl          AS etown_serviceimageurl,
            pd.city                     AS etown_city,
            pd.statecode                AS etown_statecode,
            pd.publicsearchprovider     AS etown_publicsearchprovider,
            pd.providertype             AS etown_providertype,
            pd.servicetype              AS etown_servicetype,
            pd.contactid                AS etown_contactid,
            pd.userprofileid            AS etown_userprofileid,
            pd.mobilenumber             AS etown_mobile,
            pd.creationdate             AS etown_createdon,
            pd.overallrating            AS etown_overallrating,
            pd.memberbookingcount       AS etown_ratingcount,
            COALESCE(sc.completedservices,  0) AS etown_completedservices,
            COALESCE(sc.requestedservices,  0) AS etown_receivedrequests,
            COALESCE(sc.inprogressservices, 0) AS etown_inprogressrequests,
            COALESCE(cc.mycirclecount,      0) AS etown_mycirclecount,
            COALESCE(ic.invitecount,        0) AS etown_invitecount,
            COALESCE(vc.viewcount,          0) AS etown_viewcount,
            JSON_AGG(
                DISTINCT JSONB_BUILD_OBJECT(
                    'favorite',                 pr.favorite,
                    'relationshiptype',         pr.relationshiptype,
                    'providerrelationid',       pr.providerrelationid,
                    'role',                     pr.role,
                    'userprofileid',            pr.userprofileid,
                    'firstname',                pr.firstname,
                    'email',                    pr.email,
                    'rolecode',                 pr.rolecode,
                    'mobilephone',              pr.mobilephone,
                    'addresscomp',              pr.addresscomp,
                    'additionaladdressinfo',    pr.additionaladdressinfo,
                    'contactid',                pr.contactid,
                    'profileimage',             pr.profileimage,
                    'latitude',                 pr.latitude,
                    'longitude',                pr.longitude,
                    'city',                     pr.city,
                    'statecode',                pr.statecode,
                    'createdon',                pr.createdon,
                    'ccuserid',                 pr.contact_devices_ccuserid,
                    'provider_address',         pr.provider_address,
                    'provider_businessname',    pr.provider_businessname,
                    'provider_serviceimageurl', pr.provider_serviceimageurl,
                    'provider_email',           pr.provider_email,
                    'provider_mobile',          pr.provider_mobile,
                    'provider_latitude',        pr.provider_latitude,
                    'provider_longitude',       pr.provider_longitude,
                    'provider_city',            pr.provider_city,
                    'provider_statecode',       pr.provider_statecode
                )
            )                                   AS etown_circledata,
            JSON_AGG(
                DISTINCT JSONB_BUILD_OBJECT(
                    'invitationid',             id.invitationid,
                    'invitercontact',           id.invitercontact,
                    'invitecontact',            id.invitecontact,
                    'invitationcode',           id.invitationcode,
                    'invite_role',              id.invite_role,
                    'invitecontactmobile',      id.invitecontactmobilenumber,
                    'invitecontactemail',       id.invitecontactemail,
                    'invitationtype',           id.invitationtype,
                    'isnotification',           id.isnotification,
                    'createdon',                id.createdon,
                    'contactid',                id.contactid,
                    'firstname',                id.firstname,
                    'email',                    id.email,
                    'mobilephone',              id.mobilephone,
                    'city',                     id.city,
                    'profileimage',             id.profileimage,
                    'publicsearchstatus',       id.publicsearchstatus,
                    'userprofileid',            id.userprofileid,
                    'role',                     id.role,
                    'rolename',                 id.rolename,
                    'profilename',              id.profilename,
                    'profileemail',             id.profileemail,
                    'providerid',               id.providerid,
                    'businessname',             id.businessname,
                    'businesscategory',         id.businesscategory,
                    'provider_address',         id.address,
                    'providercity',             id.providercity,
                    'statecode',                id.statecode,
                    'serviceimageurl',          id.serviceimageurl,
                    'provideremail',            id.provideremail,
                    'mobilenumber',             id.mobilenumber,
                    'provider_latitude',        id.latitude,
                    'provider_longitude',       id.longitude,
                    'publicsearchprovider',     id.publicsearchprovider
                )
            )                                   AS etown_invitationdata
        FROM provider_details pd
        LEFT JOIN service_counts sc
            ON sc.serviceprovideruserprofileid = pd.userprofileid
        CROSS JOIN circle_count cc
        CROSS JOIN invite_count ic
        LEFT JOIN provider_relation pr ON TRUE
        LEFT JOIN invitation_data id ON TRUE
        CROSS JOIN view_count vc
        GROUP BY
            pd.providerid, pd.businessname, pd.additionaladdressinfo, pd.address,
            pd.description, pd.availability, pd.latitude, pd.longitude,
            pd.serviceimageurl, pd.city, pd.statecode, pd.publicsearchprovider,
            pd.providertype, pd.servicetype, pd.contactid, pd.userprofileid, pd.mobilenumber,
            pd.creationdate, pd.overallrating, pd.memberbookingcount,
            sc.completedservices, sc.requestedservices, sc.inprogressservices,
            cc.mycirclecount, ic.invitecount, vc.viewcount
"""

    try:
        row = await Database.fetchrow(provider_query, contactid, userprofileid)
        if not row:
            return {}

        lat = row.get("etown_latitude")
        lon = row.get("etown_longitude")
        invitation_data = json.loads(row.get("etown_invitationdata") or "[]")
        badge_map = await resolve_provider_badges(
            [row.get("etown_userprofileid")]
            + [e.get("userprofileid") for e in invitation_data if isinstance(e, dict)]
        )
        premium_label = (badge_map.get(row.get("etown_userprofileid")) or {}).get("premium_label")

        return {
            "Etown_ProviderId": row.get("etown_providerid"),
            "Etown_businessname": row.get("etown_businessname"),
            "Etown_address": row.get("etown_address"),
            "Etown_fetchaddress": row.get("etown_fetchaddress"),
            "Etown_description": row.get("etown_description"),
            "Etown_availability": row.get("etown_availability"),
            "Etown_latitude": str(lat) if lat is not None else None,
            "Etown_longitude": str(lon) if lon is not None else None,
            "Etown_serviceimageurl": row.get("etown_serviceimageurl"),
            "Etown_city": row.get("etown_city"),
            "Etown_statecode": row.get("etown_statecode"),
            "Etown_publicsearchprovider": row.get("etown_publicsearchprovider"),
            "Etown_providertype": premium_display(row.get("etown_providertype"), premium_label),
            "Etown_servicetype": row.get("etown_servicetype"),
            "Etown_contactid": row.get("etown_contactid"),
            "Etown_userprofileid": row.get("etown_userprofileid"),
            "Etown_mobile": row.get("etown_mobile"),
            "Etown_createdon": _fmt_dt(row.get("etown_createdon")),
            "Etown_overallrating": str(row.get("etown_overallrating")) if row.get("etown_overallrating") is not None else None,
            "Etown_ratingcount": str(row.get("etown_ratingcount")) if row.get("etown_ratingcount") is not None else None,
            "Etown_completedservices": str(row.get("etown_completedservices", 0)),
            "Etown_receivedrequests": str(row.get("etown_receivedrequests", 0)),
            "Etown_inprogressrequests": str(row.get("etown_inprogressrequests", 0)),
            "Etown_mycirclecount": str(row.get("etown_mycirclecount", 0)),
            "Etown_invitecount": str(row.get("etown_invitecount", 0)),
            "Etown_viewcount": str(row.get("etown_viewcount", 0)),
            "Etown_circledata": json.loads(row.get("etown_circledata") or "[]"),
            "Etown_invitationdata": invitation_data,
        }

    except Exception as e:
        logger.error(f"fetch_provider_details failed | contactid={contactid} | error={e}", exc_info=True)
        return {}

async def fetch_provider_towns(contactid: str, userprofileid: str) -> List[Dict[str, Any]]:
    """Return the provider's linked towns with provider counts and distance, home town first, capped at the plan's max_towns."""
    towns_query = r"""
        WITH prov AS (
            SELECT providerid, latitude, longitude,
                   townname AS prov_townname,
                   city     AS prov_city,
                   areaname AS prov_areaname
            FROM etown_provider
            WHERE contactid = $1 AND userprofileid = $2
            LIMIT 1
        ),

        linked AS (
            SELECT DISTINCT ON (LOWER(TRIM(COALESCE(tp.townname, ep.townname))))
                COALESCE(tp.townprofileid, ep.townprofileid) AS townprofileid,
                COALESCE(tp.townname, ep.townname)           AS townname,
                tp.townimage,
                tp.towndescription,
                tp.cityname,
                COALESCE(tp.apihit, 0)                       AS api_hit_count,
                ep.areaname,
                ep.createdon,
                ep.is_primary,
                CASE WHEN tp.townlatitude ~ E'^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$'
                     THEN CAST(tp.townlatitude AS DOUBLE PRECISION) END  AS town_lat,
                CASE WHEN tp.townlongitude ~ E'^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$'
                     THEN CAST(tp.townlongitude AS DOUBLE PRECISION) END AS town_lon
            FROM etown_etownprovider ep
            JOIN prov ON prov.providerid = ep.providerid
            LEFT JOIN etown_townprofile tp ON tp.townprofileid = ep.townprofileid
            WHERE COALESCE(ep.deleted, 'false') <> 'true'
              AND COALESCE(tp.townname, ep.townname) IS NOT NULL
              AND TRIM(COALESCE(tp.townname, ep.townname)) <> ''
            ORDER BY
                LOWER(TRIM(COALESCE(tp.townname, ep.townname))),
                ep.is_primary DESC,
                ep.createdon ASC NULLS LAST
        ),

        pending AS (
            SELECT DISTINCT ON (LOWER(TRIM(tp.townname)))
                tp.townprofileid,
                tp.townname,
                tp.townimage,
                tp.towndescription,
                tp.cityname,
                COALESCE(tp.apihit, 0)  AS api_hit_count,
                prov.prov_areaname      AS areaname,
                NULL::timestamp         AS createdon,
                TRUE                    AS is_primary,
                CASE WHEN tp.townlatitude ~ E'^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$'
                     THEN CAST(tp.townlatitude AS DOUBLE PRECISION) END  AS town_lat,
                CASE WHEN tp.townlongitude ~ E'^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$'
                     THEN CAST(tp.townlongitude AS DOUBLE PRECISION) END AS town_lon
            FROM prov
            JOIN etown_townprofile tp
              ON LOWER(TRIM(tp.townname)) = LOWER(TRIM(
                     COALESCE(NULLIF(TRIM(prov.prov_townname), ''), prov.prov_city)
                 ))
            WHERE NOT EXISTS (SELECT 1 FROM linked)
            ORDER BY LOWER(TRIM(tp.townname))
        ),

        links AS (
            SELECT townprofileid, townname, townimage, towndescription, cityname,
                   api_hit_count, areaname, createdon, is_primary, town_lat, town_lon
            FROM linked
            UNION ALL
            SELECT townprofileid, townname, townimage, towndescription, cityname,
                   api_hit_count, areaname, createdon, is_primary, town_lat, town_lon
            FROM pending
        ),

        mapped_providers AS (
            SELECT DISTINCT p.providerid, l.townprofileid, p.providertype
            FROM links l
            JOIN etown_etownprovider ep2 ON ep2.townprofileid = l.townprofileid
            JOIN etown_provider p ON p.providerid = ep2.providerid
            WHERE p.deleted IS DISTINCT FROM 'true'
              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
              AND p.disabled IS NOT TRUE

            UNION

            SELECT DISTINCT p.providerid, l.townprofileid, p.providertype
            FROM links l
            JOIN etown_provider p ON LOWER(TRIM(p.city)) = LOWER(TRIM(l.townname))
            WHERE NOT EXISTS (
                SELECT 1 FROM etown_etownprovider et
                WHERE et.providerid = p.providerid
                  AND COALESCE(et.townprofileid, '') <> ''
            )
              AND p.deleted IS DISTINCT FROM 'true'
              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
              AND p.disabled IS NOT TRUE
        ),

        provider_counts AS (
            SELECT
                townprofileid,
                COUNT(DISTINCT providerid)::int AS provider_count,
                COUNT(DISTINCT CASE WHEN providertype = 'etown_verified' THEN providerid END)::int AS verified_count,
                COUNT(DISTINCT CASE WHEN providertype != 'etown_verified' OR providertype IS NULL THEN providerid END)::int AS unverified_count
            FROM mapped_providers
            GROUP BY townprofileid
        )

        SELECT
            l.townprofileid,
            l.townname,
            l.areaname,
            l.town_lat,
            l.town_lon,
            l.townimage,
            l.towndescription,
            l.cityname,
            l.createdon,
            l.api_hit_count,
            l.is_primary,
            CASE WHEN l.town_lat IS NOT NULL AND l.town_lon IS NOT NULL
                      AND prov.latitude IS NOT NULL AND prov.longitude IS NOT NULL
                THEN 6371 * acos(LEAST(1.0, GREATEST(-1.0,
                        cos(radians(prov.latitude)) * cos(radians(l.town_lat))
                        * cos(radians(l.town_lon) - radians(prov.longitude))
                        + sin(radians(prov.latitude)) * sin(radians(l.town_lat))
                     )))
            END AS distance_km,
            COALESCE(pc.provider_count, 0)   AS provider_count,
            COALESCE(pc.verified_count, 0)   AS verified_count,
            COALESCE(pc.unverified_count, 0) AS unverified_count,
            COALESCE(get_town_view_count(l.townprofileid), 0) AS view_count
        FROM links l
        CROSS JOIN prov
        LEFT JOIN provider_counts pc ON pc.townprofileid = l.townprofileid
        WHERE COALESCE(pc.provider_count, 0) > 0
        ORDER BY l.is_primary DESC, distance_km ASC NULLS LAST, l.townname ASC
    """

    try:
        rows = await Database.fetch(towns_query, contactid, userprofileid)

        towns: List[Dict[str, Any]] = []
        for row in rows:
            distance = row.get("distance_km")
            towns.append({
                "townprofileid": row.get("townprofileid"),
                "name": (row.get("townname") or "").strip(),
                "latitude": str(row["town_lat"]) if row.get("town_lat") is not None else None,
                "cityname": row.get("cityname"),
                "areaname": row.get("areaname"),
                "longitude": str(row["town_lon"]) if row.get("town_lon") is not None else None,
                "image": row.get("townimage") or "",
                "distance_km": round(float(distance), 2) if distance is not None else None,
                "provider_count": int(row.get("provider_count") or 0),
                "verified_count": int(row.get("verified_count") or 0),
                "unverified_count": int(row.get("unverified_count") or 0),
                "createdon": _fmt_dt(row.get("createdon")),
                "description": row.get("towndescription") or "",
                "view_count": int(row.get("view_count") or 0),
                "api_hit_count": int(row.get("api_hit_count") or 0),
                "is_primary": bool(row.get("is_primary")),
            })

        plan = await resolve_plan(userprofileid)
        town_cap = plan.get("max_towns") if plan else None
        if town_cap is not None and len(towns) > town_cap:
            kept = sorted(sorted(
                range(len(towns)),
                key=lambda i: (not towns[i]["is_primary"], towns[i]["createdon"] or ""),
            )[:town_cap])
            logger.info(
                "fetch_provider_towns capped | contactid=%s | plan=%s | max_towns=%s | linked=%d",
                contactid, plan.get("plan_code"), town_cap, len(towns),
            )
            towns = [towns[i] for i in kept]

        need_counts: Dict[str, Any] = {}
        try:
            town_ids = list(dict.fromkeys(t["townprofileid"] for t in towns if t["townprofileid"]))
            if town_ids:
                count_rows = await Database.fetch(
                    """
                    WITH since AS (
                        SELECT COALESCE((SELECT v.need_visited_at FROM public.etown_community_lastvisit v
                                         WHERE v.userprofileid = $1), '-infinity'::timestamptz) AS at
                    )
                    SELECT t.townprofileid,
                           c.need_count,
                           c.new_need_count
                    FROM   unnest($2::text[]) AS t(townprofileid)
                    CROSS  JOIN since s
                    CROSS  JOIN LATERAL (
                        SELECT count(*)::int AS need_count,
                               count(*) FILTER (WHERE n.userprofileid <> $1
                                                  AND n.created_at > s.at)::int AS new_need_count
                        FROM   public.etown_need n
                        WHERE  n.townprofileid = t.townprofileid
                          AND  n.status = 'posted'
                          AND  COALESCE(n.deleted, 'false') <> 'true'
                          AND  NOT EXISTS (SELECT 1 FROM public.etown_contact dc
                                           WHERE dc.contactid = n.contactid
                                             AND lower(btrim(COALESCE(dc.deleted, ''))) = 'true')
                    ) c
                    """,
                    userprofileid, town_ids,
                )
                need_counts = {r["townprofileid"]: r for r in count_rows}
        except Exception as exc:
            logger.error(
                "fetch_provider_towns need counts failed | userprofileid=%s | %s",
                userprofileid, exc, exc_info=True,
            )
        for town in towns:
            counted = need_counts.get(town["townprofileid"])
            town["need_count"] = counted["need_count"] if counted else 0
            town["new_need_count"] = counted["new_need_count"] if counted else 0
        return towns

    except Exception as e:
        logger.error(f"fetch_provider_towns failed | contactid={contactid} | error={e}", exc_info=True)
        return []

async def fetch_offers(userprofileid: str) -> List[Dict[str, Any]]:
    """Return the provider's offers as Etown_ dicts, ordered Valid, Pending, Expired, then newest first."""
    query = """
        SELECT
            eo.offersid,
            eo.contactid,
            eo.fontfamily,
            eo.fontsize,
            eo.fontstyle,
            eo.colorcode,
            eo.latitude,
            eo.longitude,
            eo.userprofileid,
            eo.thumbnailfileurl,
            eo.businessname,
            eo.description,
            eo.offerfile1,
            eo.offerfile2,
            eo.offerfile3,
            eo.offerfile4,
            eo.offerfile5,
            eo.offervideofile,
            eo.createdtime,
            eo.scheduledtime,
            eo.scheduledendtime,
            eo.offertype,
            eo.validity,
            eo.createdon,
            COALESCE(ov.view_count, 0) AS view_count,
            CASE
                WHEN eo.validity = 'Valid'    THEN 1
                WHEN eo.validity = 'Pending'  THEN 2
                WHEN eo.validity = 'Expired'  THEN 3
                ELSE 4
            END AS validitysortorder
        FROM etown_offers eo
        INNER JOIN etown_provider ep
            ON eo.userprofileid = ep.userprofileid
        LEFT JOIN (
            SELECT offersid, COUNT(DISTINCT userprofileid) AS view_count
            FROM etown_offer_views
            GROUP BY offersid
        ) ov ON ov.offersid = eo.offersid
        WHERE
            eo.userprofileid = $1
        ORDER BY
            validitysortorder,
            eo.createdon DESC
    """

    try:
        rows = await Database.fetch(query, userprofileid)
        result = []
        for row in rows:
            result.append({
                "Etown_offersid": row.get("offersid"),
                "Etown_contactid": row.get("contactid"),
                "Etown_businessname": row.get("businessname"),
                "Etown_offerdescription": row.get("description"),
                "Etown_createdtime": _fmt_dt(row.get("createdtime")),
                "Etown_scheduledtime": _fmt_dt(row.get("scheduledtime")),
                "Etown_scheduleendtime": _fmt_dt(row.get("scheduledendtime")),
                "Etown_offertype": row.get("offertype"),
                "Etown_validity": row.get("validity"),
                "Etown_offerfile1": row.get("offerfile1"),
                "Etown_offerfile2": row.get("offerfile2"),
                "Etown_offerfile3": row.get("offerfile3"),
                "Etown_offerfile4": row.get("offerfile4"),
                "Etown_offerfile5": row.get("offerfile5"),
                "Etown_offervideofile": row.get("offervideofile"),
                "Etown_createdon": _fmt_dt(row.get("createdon")),
                "Etown_latitude": row.get("latitude"),
                "Etown_longitude": row.get("longitude"),
                "Etown_fontfamily": row.get("fontfamily"),
                "Etown_fontsize": row.get("fontsize"),
                "Etown_fontstyle": row.get("fontstyle"),
                "Etown_colorcode": row.get("colorcode"),
                "Etown_thumbnailfileurl": row.get("thumbnailfileurl"),
                "Etown_viewcount": row.get("view_count"),
            })
        return result

    except Exception as e:
        logger.error(f"fetch_offers failed | userprofileid={userprofileid} | error={e}", exc_info=True)
        return []


async def fetch_feed_details(contactid: str, userprofileid: str) -> List[Dict[str, Any]]:
    """Return the provider's own mycircle and public feed posts with comment, like and self-like data as Etown_ dicts."""
    query = """
        WITH

        feed_details AS (
            SELECT
                f.feedid,
                f.contactid,
                f.userprofileid,
                f.feedtext,
                f.feedfilelocationurl,
                f.thumbnailfileurl,
                f.audience,
                f.createdon,
                f.modifiedon
            FROM etown_feed f
            WHERE
                f.contactid     = $1
                AND f.userprofileid = $2
                AND f.audience IN (1, 2)
            ORDER BY f.createdon DESC
        ),

        comments_count AS (
            SELECT
                fc.feedid                AS feedid,
                COUNT(fc.feedcommentsid) AS feedcommentscount
            FROM etown_feedcomments fc
            WHERE fc.feedid IN (SELECT feedid FROM feed_details)
            GROUP BY fc.feedid
        ),

        likes_count AS (
            SELECT
                fl.feedid            AS feedid,
                COUNT(fl.feedlikesid) AS feedlikecount
            FROM etown_feedlikes fl
            WHERE
                fl.likemode = 1
                AND fl.feedid IN (SELECT feedid FROM feed_details)
            GROUP BY fl.feedid
        ),

        likes_by_user AS (
            SELECT
                fl.feedid   AS feedid,
                fl.likemode AS feedstatusdata
            FROM etown_feedlikes fl
            WHERE
                fl.likesbyuserprofileid = $2
                AND fl.likemode = 1
                AND fl.feedid IN (SELECT feedid FROM feed_details)
        )

        SELECT
            fd.feedid                           AS etown_feedid,
            fd.feedfilelocationurl              AS etown_feedfilelocationurl,
            fd.thumbnailfileurl                 AS etown_thumbnailfileurl,
            fd.feedtext                         AS etown_feedtext,
            fd.createdon                        AS etown_createdon,
            fd.modifiedon                       AS etown_modifiedon,
            COALESCE(cc.feedcommentscount, 0)   AS etown_commentscount,
            COALESCE(lc.feedlikecount, 0)       AS etown_feedlikecount,
            CASE
                WHEN lu.feedid IS NOT NULL THEN true
                ELSE false
            END                                 AS etown_feedlikecontactperson
        FROM feed_details fd
        LEFT JOIN comments_count cc ON cc.feedid = fd.feedid
        LEFT JOIN likes_count lc    ON lc.feedid = fd.feedid
        LEFT JOIN likes_by_user lu  ON lu.feedid = fd.feedid
        ORDER BY fd.createdon DESC
    """

    try:
        rows = await Database.fetch(query, contactid, userprofileid)
        result = []
        for row in rows:
            result.append({
                "Etown_feedid": row.get("etown_feedid"),
                "Etown_feedfilelocationurl": row.get("etown_feedfilelocationurl"),
                "Etown_thumbnailfileurl": row.get("etown_thumbnailfileurl"),
                "Etown_feedtext": row.get("etown_feedtext"),
                "Etown_commentscount": str(row.get("etown_commentscount", 0)),
                "Etown_feedlikecount": str(row.get("etown_feedlikecount", 0)),
                "Etown_feedlikecontactperson": row.get("etown_feedlikecontactperson"),
                "Etown_createdon": _fmt_dt(row.get("etown_createdon")),
                "Etown_modifiedon": _fmt_dt(row.get("etown_modifiedon")),
            })
        return result

    except Exception as e:
        logger.error(f"fetch_feed_details failed | userprofileid={userprofileid} | error={e}", exc_info=True)
        return []


async def fetch_member_dashboard(contact_id: str, userprofile_id: str) -> Dict[str, Any]:
    """Return the member's ids, name and count of Requested invitations, with zero on failure."""
    query = """
        SELECT
            up.contactidname,
            COUNT(i.invitationid) AS invitecount
        FROM etown_userprofile up
        LEFT JOIN etown_invitation i
            ON i.invitecontact = $1
            AND i.inviteuserprofile = up.userprofileid
            AND i.invitationcode = 'Requested'
        WHERE up.userprofileid = $2
            AND up.role = 2
        GROUP BY up.contactidname
    """

    try:
        rows = await Database.fetch(query, contact_id, userprofile_id)
        row = rows[0] if rows else {}
        return {
            "Etown_contactid": contact_id,
            "Etown_userprofileid": userprofile_id,
            "Etown_firstname": row.get("contactidname"),
            "Etown_invitecount": row.get("invitecount", 0),
        }

    except Exception as e:
        logger.error(f"fetch_member_dashboard failed | contact_id={contact_id} | error={e}", exc_info=True)
        return {
            "Etown_contactid": contact_id,
            "Etown_userprofileid": userprofile_id,
            "Etown_firstname": None,
            "Etown_invitecount": 0,
        }


async def fetch_social_insights_totals(contactid: str, userprofileid: str) -> List[Dict[str, Any]]:
    """Return a one-item list of summed social likes, comments, views and connection flags, or [] when nothing social exists."""
    query = """
        SELECT
            (
                EXISTS (
                    SELECT 1 FROM etown_instagram_posts
                    WHERE contactid = $1 AND userprofileid = $2
                )
                OR EXISTS (
                    SELECT 1 FROM etown_facebook_posts
                    WHERE contactid = $1 AND userprofileid = $2
                )
            ) AS has_posts,
            EXISTS (
                SELECT 1 FROM etown_instagramconnections
                WHERE contactid = $1 AND userprofileid = $2 AND is_active = TRUE
            ) AS instagram_connection,
            EXISTS (
                SELECT 1 FROM etown_facebookconnections
                WHERE contactid = $1 AND userprofileid = $2 AND is_active = TRUE
            ) AS facebook_connection,
            t.likes, t.comments, t.views
        FROM (
            SELECT
                COALESCE(SUM(COALESCE(likes, 0)), 0)                              AS likes,
                COALESCE(SUM(COALESCE(comments, 0)), 0)                           AS comments,
                COALESCE(SUM(COALESCE(views, 0) + COALESCE(video_views, 0)), 0)   AS views
            FROM etown_social_insights_snapshots
            WHERE contactid = $1 AND userprofileid = $2

              AND period_days = (SELECT MAX(period_days) FROM etown_social_insights_snapshots)
        ) t
"""
    try:
        row = await Database.fetchrow(query, contactid, userprofileid)
        if not row:
            return []
        instagram_connection = bool(row["instagram_connection"])
        facebook_connection = bool(row["facebook_connection"])
        if not (row["has_posts"] or instagram_connection or facebook_connection):
            return []
        return [{
            "likes": int(row["likes"] or 0),
            "comments": int(row["comments"] or 0),
            "views": int(row["views"] or 0),
            "instagram_connection": instagram_connection,
            "facebook_connection": facebook_connection,
        }]
    except Exception as e:
        logger.error(
            f"fetch_social_insights_totals failed | contactid={contactid} "
            f"| userprofileid={userprofileid} | error={e}",
            exc_info=True,
        )
        return []
