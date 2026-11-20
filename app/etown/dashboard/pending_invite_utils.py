"""Pending Invite Utilities - Postgres reads of a user's dashboard invitations.

Domain layer behind /api/dashboard: reads etown_invitation joined to contact, userprofile and provider for Internal (My Circle) invitations a user received and External invitations a user sent, returning Etown_-prefixed records and an empty list on error.

Features:
    - Internal Invitation Retrieval: Fetches My Circle invitations received, with inviter details
    - Disabled Provider Filter: Internal invitations from disabled providers are dropped
    - External Invitation Retrieval: Fetches External invitations sent by a user
    - Data Enrichment: Joins with etown_contact, etown_userprofile, and etown_provider tables
    - Provider Badges: Etown_providertype carries the inviter's paid-tier badge label
    - Structured Responses: Returns records with Etown_ prefixed keys for frontend consumption
    - Error Handling: Graceful error logging with empty list fallback

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    _fmt_dt(): Format datetime values to ISO 8601 string
    fetch_internal_invitations(): Fetch Internal invitations received by a specific user
    fetch_external_invitations(): Fetch External invitations sent by a specific user

Dependencies:
    - json: JSON serialization utilities
    - datetime: Datetime formatting helpers
    - typing: Type hints for data structures
    - app.core.logging: Centralized application logging
    - app.db.client: Async PostgreSQL database access
    - app.etown.plans.plans_utils: Provider badge resolution and premium display

Updates:
    26-May-2026: Added module docstring following project documentation standards
    20-Aug-2026: Inviter markers report the paid tier's badge label; invitation rows carry Etown_providertype.
    02-Sep-2026: fetch_internal_invitations() drops invitations from disabled providers.
"""

import asyncio

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.logging import logger
from app.db.client import Database
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges


def _fmt_dt(value: Any) -> Optional[str]:
    """Return a datetime as an ISO string, any other value as str, and None as None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def fetch_internal_invitations(contactid: str, userprofileid: str) -> List[Dict[str, Any]]:
    """Return Internal invitations received by a user from non-disabled inviters as Etown_ keyed dicts, or [] on error."""
    query = """
        SELECT
            ei.invitecontact,
            ei.inviteuserprofile,
            ei.new_inviterole,
            ei.invitationtype,
            ei.invitationid,
            ei.createdon,
            ep.publicsearchprovider,
            ep.providertype,
            eup.role,
            ei.invitationcode,
            ec.profileimage         AS imageurl,
            ec.mobilephone          AS contactinfo,
            ei.invite_rolename,
            COALESCE(ep.businessname, ec.firstname, ei.invitercontactname)   AS name,
            ei.invitercontact,
            ei.inviteruserprofile,
            ei.invitecontactname    AS receivername,
            COUNT(*) OVER()         AS invitationcount
        FROM etown_invitation ei
        LEFT JOIN etown_contact ec
            ON ei.invitercontact = ec.contactid
        LEFT JOIN etown_userprofile eup
            ON ei.inviteruserprofile = eup.userprofileid
        LEFT JOIN etown_provider ep
            ON eup.userprofileid = ep.userprofileid
        WHERE
            ei.invitecontact     = $1
            AND ei.inviteuserprofile = $2
            AND ei.invitationtype   = 'Internal'
            AND NOT EXISTS (
                SELECT 1 FROM etown_provider dp
                 WHERE dp.disabled
                   AND dp.userprofileid = ei.inviteruserprofile
            )
        ORDER BY ei.createdon DESC
    """

    try:
        rows = await Database.fetch(query, contactid, userprofileid)
        badge_map = await resolve_provider_badges(row.get("inviteruserprofile") for row in rows)
        result = []
        for row in rows:
            result.append({
                "Etown_invitecontact":             row.get("invitecontact"),
                "Etown_invitecontactuserprofile":  row.get("inviteuserprofile"),
                "Etown_inviterole":                row.get("new_inviterole"),
                "Etown_invitationtype":            row.get("invitationtype"),
                "Etown_invitationid":              row.get("invitationid"),
                "Etown_invitationcreatedon":       _fmt_dt(row.get("createdon")),
                "Etown_publicsearchprovider":      row.get("publicsearchprovider"),
                "Etown_providertype":              premium_display(
                    row.get("providertype"),
                    (badge_map.get(row.get("inviteruserprofile")) or {}).get("premium_label"),
                ),
                "Etown_role":                      row.get("role"),
                "Etown_invitationcode":            row.get("invitationcode"),
                "Etown_imageurl":                  row.get("imageurl"),
                "Etown_contactinfo":               row.get("contactinfo"),
                "Etown_inviterolename":            row.get("invite_rolename"),
                "Etown_name":                      row.get("name"),
                "Etown_invitercontact":            row.get("invitercontact"),
                "Etown_invitercontactuserprofile": row.get("inviteruserprofile"),
                "Etown_receivername":              row.get("receivername"),
                "Etown_invitationcount":           row.get("invitationcount"),
            })
        return result

    except Exception as e:
        logger.error(f"fetch_internal_invitations failed | contactid={contactid} | error={e}", exc_info=True)
        return []


async def fetch_external_invitations(contactid: str, userprofileid: str) -> List[Dict[str, Any]]:
    """Return External and ExternalApp_share invitations sent by a user as Etown_ keyed dicts, or [] on error."""
    query = """
        SELECT
            ei.invitationid,
            ei.invitationtype,
            ei.suffixid,
            ei.invitercontact,
            ei.inviteruserprofile,
            COALESCE(ep.businessname, ec.firstname, ei.invitercontactname) AS invitername,
            eup.role                AS inviterrole,
            eup.rolename            AS inviterrolename,
            ei.invite_rolename      AS relationshipname,
            ei.invite_role          AS relationshipvalue,
            ei.invitecontact,
            ei.inviteuserprofile,
            ei.invitecontactname    AS inviteename,
            ei.invitationcode,
            ec.profileimage         AS inviterprofileimage
        FROM etown_invitation ei
        LEFT JOIN etown_contact ec
            ON ei.invitercontact = ec.contactid
        LEFT JOIN etown_userprofile eup
            ON ei.inviteruserprofile = eup.userprofileid
        LEFT JOIN etown_provider ep
            ON eup.userprofileid = ep.userprofileid
        WHERE
            ei.invitercontact     = $1
            AND ei.inviteruserprofile = $2
            AND ei.invitationtype   IN ('External', 'ExternalApp_share')
        ORDER BY ei.createdon DESC
    """

    try:
        rows = await Database.fetch(query, contactid, userprofileid)
        result = []
        for row in rows:
            result.append({
                "Etown_invitationid":           row.get("invitationid"),
                "Etown_Invitationtype":          row.get("invitationtype"),
                "Etown_invitationSuffixId":      row.get("suffixid"),
                "Etown_invitercontactId":        row.get("invitercontact"),
                "Etown_inviteruserprofileId":    row.get("inviteruserprofile"),
                "Etown_invitername":             row.get("invitername"),
                "Etown_inviterrole":             row.get("inviterrole"),
                "Etown_inviterrolename":         row.get("inviterrolename"),
                "Etown_relationshipname":        row.get("relationshipname"),
                "Etown_relationshipvalue":       row.get("relationshipvalue"),
                "Etown_invitecontactId":         row.get("invitecontact"),
                "Etown_inviteuserprofileId":     row.get("inviteuserprofile"),
                "Etown_inviteename":             row.get("inviteename"),
                "Etown_invitationcode":          row.get("invitationcode"),
                "Etown_inviterprofileImage":     row.get("inviterprofileimage"),
            })
        return result

    except Exception as e:
        logger.error(f"fetch_external_invitations failed | contactid={contactid} | error={e}", exc_info=True)
        return []
