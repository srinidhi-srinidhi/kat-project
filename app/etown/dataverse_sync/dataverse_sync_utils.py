"""Dataverse Sync Utilities - Postgres fan-out writes behind the /dataverse-sync endpoints.

Domain layer behind /api/dataverse-sync: applies provider verification, address, mobile, business name, source, soft-delete, review-approval and town-image-review changes to every table that caches them, each in one transaction, and re-embeds the affected rows.

Features:
    - Provider Verification Toggle: One call verifies or un-verifies a provider end to end
    - Verification Values: writes etown_verified/verified or etown_nonverified/non-verified, as search filters expect
    - Address Columns: only columns a table has; etown_userprofile and etown_member hold no address
    - Address Fan-out: One address change applied to every table that caches it for a contact
    - Partial Updates: Omitted address fields keep their current values (COALESCE)
    - Single Transaction: Each cascade commits or rolls back as one unit
    - Retry-decorated DB Access: Transient pool/connection failures are retried
    - Detailed Result: Per-table row counts returned for logging and API responses
    - Soft Delete: retiring a provider flags deleted = 'true' plus an IST deletiondate
    - Review Approval: applies a back-office review decision across the affected tables
    - Town Image Review: an approved etown_townreview image becomes the town profile's townimage
    - Embedding Refresh: re-embeds rows whose address or business name changed

Functions:
    set_provider_verification(): Verify or un-verify a provider, its services, profile and contact
    update_address(): Apply an address change to every table that caches it for a contact
    _affected_rows(): Parse the row count out of an asyncpg status string like "UPDATE 3"
    delete_provider_by_mobile_and_business(): Soft-delete a provider by providerid or mobile + name
    _last_10_digits(): Reduce a mobile number to its last 10 digits for tolerant matching
    find_providers(): Read up to 25 non-verified providers' business name, mobile number, and state
    update_provider_mobilenumber(): Move a provider's mobile number across every table caching it
    link_provider_to_member(): Re-point a listing at the member account owning a mobile number
    update_provider_businessname(): Rename a provider across every table caching its business name
    update_provider_source(): Rewrite a provider's source across the three tables holding it
    apply_provider_review_approval(): Apply a worked review row across the tables it touches
    apply_town_review_status(): Set a town review's status; on approval write imageurl to the town profile's townimage
    _embedding_field_mapping(): Lazily import the /embeddings endpoint's table-to-fields mapping
    refresh_content_vectors(): Regenerate content_vector for specific rows
    _existing_columns(): Check which of a table's mapped embedding columns actually exist
    _refresh_one_content_vector(): Rebuild and store one row's content_vector embedding
    refresh_address_embeddings(): Re-embed the rows an address change actually affected
    refresh_businessname_embeddings(): Re-embed the rows a business-name change affected

Dependencies:
    - app.db.client: Database connection pool (Database) and retry decorator (with_retry)
    - app.core.logging: Centralized logging infrastructure
    - app.utils.blob_storage: to_relative_blob_url() normalising an approved town image
    - hashlib: content_vector_hash digests
    - app.utils.embeddings: generate_embedding() and dimension validation, imported lazily
    - app.api.v1.endpoints.embeddings: TABLE_FIELD_MAPPING, imported lazily

Updates:
    04-Aug-2026: Initial creation: verification toggle, address fan-out, embedding refresh, provider delete.
    06-Aug-2026: Added find_providers(), update_provider_mobilenumber(), link_provider_to_member().
    11-Aug-2026: Added update_provider_businessname().
    26-Aug-2026: Logged silent exception handlers with tracebacks.
    16-Sep-2026: Added update_provider_source().
    17-Sep-2026: delete_provider_by_mobile_and_business() is a soft delete.
    18-Sep-2026: set_provider_verification() reads contactid from the provider row; added update_contact flag.
    21-Sep-2026: delete_provider_by_mobile_and_business() also selects by providerid.
    22-Sep-2026: Added apply_provider_review_approval() for POST /dataverse-sync/provider_review_approval.
    30-Sep-2026: apply_provider_review_approval() writes interested to the review row and etown_provider.
    08-Oct-2026: Added apply_town_review_status() for POST /dataverse-sync/townreview, returning the updated review row.
"""

import hashlib
from typing import Any, Dict, List, Optional

from app.db.client import Database, with_retry
from app.core.logging import logger
from app.utils.blob_storage import to_relative_blob_url

_LOG = "SET_PROVIDER_VERIFICATION"
_LOG_ADDRESS = "UPDATE_ADDRESS"
_LOG_EMBEDDING = "REFRESH_CONTENT_VECTOR"
_LOG_DELETE = "DELETE_PROVIDER"
_LOG_LINKED = "LINKED_UNVERIFIED"
_LOG_MOBILE = "UPDATE_PROVIDER_MOBILE"
_LOG_LINK_MEMBER = "LINK_PROVIDER_TO_MEMBER"
_LOG_BUSINESSNAME = "UPDATE_PROVIDER_BUSINESSNAME"
_LOG_SOURCE = "UPDATE_PROVIDER_SOURCE"
_LOG_REVIEW_APPROVAL = "PROVIDER_REVIEW_APPROVAL"
_LOG_TOWN_REVIEW = "TOWN_REVIEW_STATUS"

VERIFIED_PROVIDERTYPE = "etown_verified"
VERIFIED_PUBLICSEARCHPROVIDER = "verified"
NONVERIFIED_PROVIDERTYPE = "etown_nonverified"
NONVERIFIED_PUBLICSEARCHPROVIDER = "non-verified"

FIND_PROVIDERS_LIMIT = 25

_IST_NOW = "now() AT TIME ZONE 'Asia/Kolkata'"

_PROVIDER_SOFT_DELETE_TABLES = (
    ("etown_providerservices_copy", "providerservices_copy_deleted"),
    ("etown_providerservices",      "providerservices_deleted"),
    ("etown_etownprovider",         "etownprovider_deleted"),
    ("etown_provider_details",      "provider_details_deleted"),
    ("etown_provider",              "provider_deleted"),
)


@with_retry()
async def set_provider_verification(
    providerid: str,
    verified: bool,
    update_contact: bool = True,
) -> Dict[str, Any]:
    """Set a provider's verified or non-verified values on provider, service, userprofile and optionally contact rows in one transaction.

    Args:
        providerid: Provider GUID (etown_provider.providerid).
        verified: True writes the verified values; False writes the non-verified ones.
        update_contact: False leaves etown_contact.verified untouched.

    Returns:
        Dict of found flag, resolved ids, written values, per-table row counts and providerservices_ids.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    providertype = VERIFIED_PROVIDERTYPE if verified else NONVERIFIED_PROVIDERTYPE
    publicsearchprovider = (
        VERIFIED_PUBLICSEARCHPROVIDER if verified else NONVERIFIED_PUBLICSEARCHPROVIDER
    )

    result: Dict[str, Any] = {
        "found": False,
        "providerid": providerid,
        "verified": verified,
        "providertype": providertype,
        "publicsearchprovider": publicsearchprovider,
        "userprofileid": None,
        "contactid": None,
        "provider_updated": 0,
        "providerservices_updated": 0,
        "providerservices_copy_updated": 0,
        "userprofile_updated": 0,
        "contact_updated": 0,
        "providerservices_ids": [],
    }

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            provider_row = await con.fetchrow(
                """
                UPDATE etown_provider
                SET publicsearchprovider = $2::text,
                    providertype         = $3::text,
                    modifieddate         = NOW()
                WHERE providerid = $1::text
                RETURNING userprofileid, contactid
                """,
                providerid,
                publicsearchprovider,
                providertype,
            )

            if provider_row is None:
                logger.warning(f"{_LOG} | NOT_FOUND | providerid={providerid}")
                return result

            result["found"] = True
            result["provider_updated"] = 1
            userprofileid: Optional[str] = provider_row["userprofileid"]
            result["userprofileid"] = userprofileid
            result["contactid"] = provider_row["contactid"]

            services_rows = await con.fetch(
                """
                UPDATE etown_providerservices
                SET publicsearchprovider = $2::text,
                    providertype         = $3::text,
                    modifieddate         = NOW()
                WHERE providerid = $1::text
                RETURNING providerservicesid
                """,
                providerid,
                publicsearchprovider,
                providertype,
            )
            result["providerservices_updated"] = len(services_rows)
            result["providerservices_ids"] = [
                row["providerservicesid"] for row in services_rows if row["providerservicesid"]
            ]

            services_copy_status = await con.execute(
                """
                UPDATE etown_providerservices_copy
                SET publicsearchprovider = $2::text,
                    providertype         = $3::text,
                    modifieddate         = NOW()
                WHERE providerid = $1::text
                """,
                providerid,
                publicsearchprovider,
                providertype,
            )
            result["providerservices_copy_updated"] = _affected_rows(services_copy_status)

            if userprofileid:
                userprofile_row = await con.fetchrow(
                    """
                    UPDATE etown_userprofile
                    SET verified = $2::boolean,
                        modifieddate = NOW()
                    WHERE userprofileid = $1::text
                    RETURNING contactid
                    """,
                    userprofileid,
                    verified,
                )
                if userprofile_row is not None:
                    result["userprofile_updated"] = 1
                    if not result["contactid"]:
                        result["contactid"] = userprofile_row["contactid"]
                else:
                    logger.warning(
                        f"{_LOG} | USERPROFILE_MISSING | providerid={providerid} "
                        f"| userprofileid={userprofileid}"
                    )
            else:
                logger.warning(f"{_LOG} | NO_USERPROFILEID | providerid={providerid}")

            contactid: Optional[str] = result["contactid"]
            if not update_contact:
                logger.info(
                    f"{_LOG} | CONTACT_SKIPPED | providerid={providerid} "
                    f"| contactid={contactid or '-'}"
                )
            elif contactid:
                contact_status = await con.execute(
                    """
                    UPDATE etown_contact
                    SET verified = $2::boolean,
                        modifieddate = NOW()
                    WHERE contactid = $1::text
                    """,
                    contactid,
                    verified,
                )
                result["contact_updated"] = _affected_rows(contact_status)
                if not result["contact_updated"]:
                    logger.warning(
                        f"{_LOG} | CONTACT_MISSING | providerid={providerid} "
                        f"| contactid={contactid}"
                    )
            else:
                logger.warning(f"{_LOG} | NO_CONTACTID | providerid={providerid}")

    logger.info(
        f"{_LOG} | SUCCESS | providerid={providerid} | verified={verified} "
        f"| providertype={providertype} | publicsearchprovider={publicsearchprovider} "
        f"| userprofileid={result['userprofileid'] or '-'} "
        f"| contactid={result['contactid'] or '-'} "
        f"| services={result['providerservices_updated']} "
        f"| services_copy={result['providerservices_copy_updated']} "
        f"| userprofile={result['userprofile_updated']} "
        f"| contact={result['contact_updated'] if update_contact else 'skipped'}"
    )
    return result


@with_retry()
async def update_address(
    contactid: str,
    address: Optional[str] = None,
    city: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    areaname: Optional[str] = None,
    townname: Optional[str] = None,
) -> Dict[str, Any]:
    """Write the supplied address fields to every contact, provider and service table that caches them, in one transaction.

    Args:
        contactid: Contact GUID whose address changes.
        address: Street address, or None to keep it.
        city: City name, or None to keep it.
        latitude: Latitude in degrees, or None to keep it.
        longitude: Longitude in degrees, or None to keep it.
        areaname: Area or locality name, or None to keep it.
        townname: Town name, or None to keep it.

    Returns:
        Dict of found flag, fields written, per-table row counts, touched ids and skipped tables.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    supplied = {
        "address": address,
        "city": city,
        "latitude": latitude,
        "longitude": longitude,
        "areaname": areaname,
        "townname": townname,
    }
    fields = [name for name, value in supplied.items() if value is not None]

    skipped: List[str] = ["etown_userprofile", "etown_member"]

    result: Dict[str, Any] = {
        "found": False,
        "contactid": contactid,
        "fields": fields,
        "contact_updated": 0,
        "provider_updated": 0,
        "providerservices_updated": 0,
        "providerservices_copy_updated": 0,
        "etownprovider_updated": 0,
        "provider_ids": [],
        "providerservices_ids": [],
        "providerservices_copy_ids": [],
        "skipped": skipped,
    }

    if not fields:
        logger.warning(f"{_LOG_ADDRESS} | NO_FIELDS | contactid={contactid}")
        return result

    lat_text = str(latitude) if latitude is not None else None
    lng_text = str(longitude) if longitude is not None else None

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            contact_row = await con.fetchrow(
                """
                UPDATE etown_contact
                SET address   = COALESCE($2::text, address),
                    city      = COALESCE($3::text, city),
                    latitude  = COALESCE($4::double precision, latitude),
                    longitude = COALESCE($5::double precision, longitude),
                    modifieddate = NOW()
                WHERE contactid = $1::text
                RETURNING contactid
                """,
                contactid,
                address,
                city,
                latitude,
                longitude,
            )

            if contact_row is None:
                logger.warning(f"{_LOG_ADDRESS} | NOT_FOUND | contactid={contactid}")
                return result

            result["found"] = True
            result["contact_updated"] = 1

            provider_rows = await con.fetch(
                """
                UPDATE etown_provider
                SET address   = COALESCE($2::text, address),
                    city      = COALESCE($3::text, city),
                    latitude  = COALESCE($4::double precision, latitude),
                    longitude = COALESCE($5::double precision, longitude),
                    areaname  = COALESCE($6::text, areaname),
                    townname  = COALESCE($7::text, townname),
                    modifieddate = NOW()
                WHERE contactid = $1::text
                RETURNING providerid
                """,
                contactid,
                address,
                city,
                latitude,
                longitude,
                areaname,
                townname,
            )
            result["provider_updated"] = len(provider_rows)
            provider_ids = [row["providerid"] for row in provider_rows if row["providerid"]]
            result["provider_ids"] = provider_ids

            if any(f in fields for f in ("address", "city", "latitude", "longitude", "areaname")):
                services_rows = await con.fetch(
                    """
                    UPDATE etown_providerservices
                    SET address   = COALESCE($2::text, address),
                        city      = COALESCE($3::text, city),
                        latitude  = COALESCE($4::text, latitude),
                        longitude = COALESCE($5::text, longitude),
                        areaname  = COALESCE($6::text, areaname),
                        modifieddate = NOW()
                    WHERE contactid = $1::text OR providerid = ANY($7::text[])
                    RETURNING providerservicesid
                    """,
                    contactid,
                    address,
                    city,
                    lat_text,
                    lng_text,
                    areaname,
                    provider_ids,
                )
                result["providerservices_updated"] = len(services_rows)
                result["providerservices_ids"] = [
                    row["providerservicesid"] for row in services_rows if row["providerservicesid"]
                ]

                services_copy_rows = await con.fetch(
                    """
                    UPDATE etown_providerservices_copy
                    SET address   = COALESCE($2::text, address),
                        city      = COALESCE($3::text, city),
                        latitude  = COALESCE($4::double precision, latitude),
                        longitude = COALESCE($5::double precision, longitude),
                        areaname  = COALESCE($6::text, areaname),
                        modifieddate = NOW()
                    WHERE contactid = $1::text OR providerid = ANY($7::text[])
                    RETURNING providerservicesid
                    """,
                    contactid,
                    address,
                    city,
                    latitude,
                    longitude,
                    areaname,
                    provider_ids,
                )
                result["providerservices_copy_updated"] = len(services_copy_rows)
                result["providerservices_copy_ids"] = [
                    row["providerservicesid"]
                    for row in services_copy_rows
                    if row["providerservicesid"]
                ]
            else:
                skipped.extend(["etown_providerservices", "etown_providerservices_copy"])

            if provider_ids and ("areaname" in fields or "townname" in fields):
                etownprovider_status = await con.execute(
                    """
                    UPDATE etown_etownprovider
                    SET areaname = COALESCE($2::text, areaname),
                        townname = COALESCE($3::text, townname),
                        modifieddate = NOW()
                    WHERE providerid = ANY($1::text[])
                    """,
                    provider_ids,
                    areaname,
                    townname,
                )
                result["etownprovider_updated"] = _affected_rows(etownprovider_status)
            elif "areaname" not in fields and "townname" not in fields:
                skipped.append("etown_etownprovider")

    logger.info(
        f"{_LOG_ADDRESS} | SUCCESS | contactid={contactid} | fields={','.join(fields)} "
        f"| contact={result['contact_updated']} | provider={result['provider_updated']} "
        f"| services={result['providerservices_updated']} "
        f"| services_copy={result['providerservices_copy_updated']} "
        f"| etownprovider={result['etownprovider_updated']}"
    )
    return result


def _affected_rows(status: str) -> int:
    """Return the row count from an asyncpg status string such as UPDATE 3, or 0."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        logger.warning("dataverse sync: could not read a row count out of status %r", status)
        return 0


@with_retry()
async def delete_provider_by_mobile_and_business(
    mobilenumber: Optional[str] = None,
    businessname: Optional[str] = None,
    providerid: Optional[str] = None,
) -> Dict[str, Any]:
    """Soft-delete live provider listings and their service, town, details and unreferenced userprofile rows in one transaction.

    Args:
        mobilenumber: Mobile number matched on its last 10 digits; ignored when providerid is given.
        businessname: Business name matched case-insensitively; ignored when providerid is given.
        providerid: Provider GUID selecting one listing; takes precedence over the pair.

    Returns:
        Dict of found flag, deletion stamp, touched ids, per-table flagged counts and kept userprofiles.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    digits = "".join(ch for ch in str(mobilenumber or "") if ch.isdigit())[-10:]
    business = (businessname or "").strip()
    pid = (providerid or "").strip()

    result: Dict[str, Any] = {
        "found": False,
        "mobilenumber": mobilenumber,
        "businessname": businessname,
        "providerid": pid or None,
        "deletiondate": None,
        "provider_ids": [],
        "userprofile_ids": [],
        "contact_ids": [],
        "providerservices_ids": [],
        "providerservices_copy_deleted": 0,
        "providerservices_deleted": 0,
        "etownprovider_deleted": 0,
        "provider_details_deleted": 0,
        "provider_deleted": 0,
        "userprofile_deleted": 0,
        "userprofile_kept": [],
    }

    if not pid and (len(digits) < 10 or not business):
        logger.warning(
            f"{_LOG_DELETE} | BAD_INPUT | mobile_digits={len(digits)} | business={bool(business)}"
        )
        return result

    match_label = f"providerid={pid}" if pid else f"mobile=***{digits[-4:]} | business='{business}'"

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            if pid:
                provider_rows = await con.fetch(
                    """
                    SELECT providerid, userprofileid, contactid, businessname, mobilenumber
                    FROM etown_provider
                    WHERE providerid = $1::text
                      AND deleted IS DISTINCT FROM 'true'
                    FOR UPDATE
                    """,
                    pid,
                )
            else:
                provider_rows = await con.fetch(
                    """
                    SELECT providerid, userprofileid, contactid, businessname, mobilenumber
                    FROM etown_provider
                    WHERE right(regexp_replace(COALESCE(mobilenumber, ''), '\\D', '', 'g'), 10) = $1::text
                      AND lower(btrim(COALESCE(businessname, ''))) = lower(btrim($2::text))
                      AND deleted IS DISTINCT FROM 'true'
                    FOR UPDATE
                    """,
                    digits,
                    business,
                )

            if not provider_rows:
                logger.warning(f"{_LOG_DELETE} | NOT_FOUND | {match_label}")
                return result

            result["found"] = True
            if pid:
                result["mobilenumber"] = provider_rows[0]["mobilenumber"]
                result["businessname"] = provider_rows[0]["businessname"]
                business = (provider_rows[0]["businessname"] or "").strip()
                match_label = f"providerid={pid} | business='{business}'"
            provider_ids = [r["providerid"] for r in provider_rows if r["providerid"]]
            userprofile_ids = [r["userprofileid"] for r in provider_rows if r["userprofileid"]]
            result["provider_ids"] = provider_ids
            result["userprofile_ids"] = userprofile_ids
            result["contact_ids"] = sorted({r["contactid"] for r in provider_rows if r["contactid"]})

            stamp = await con.fetchval(f"SELECT {_IST_NOW}")
            result["deletiondate"] = stamp

            service_rows = await con.fetch(
                """
                SELECT providerservicesid FROM etown_providerservices
                WHERE providerid = ANY($1::text[])
                  AND deleted IS DISTINCT FROM 'true'
                """,
                provider_ids,
            )
            result["providerservices_ids"] = [
                r["providerservicesid"] for r in service_rows if r["providerservicesid"]
            ]

            for table, count_key in _PROVIDER_SOFT_DELETE_TABLES:
                status = await con.execute(
                    f"""
                    UPDATE {table}
                       SET deleted = 'true', deletiondate = $2::timestamp
                     WHERE providerid = ANY($1::text[])
                       AND deleted IS DISTINCT FROM 'true'
                    """,
                    provider_ids,
                    stamp,
                )
                result[count_key] = _affected_rows(status)

            if userprofile_ids:
                flagged_profiles = await con.fetch(
                    """
                    UPDATE etown_userprofile up
                       SET deleted = 'true', deletiondate = $2::timestamp
                     WHERE up.userprofileid = ANY($1::text[])
                       AND up.deleted IS DISTINCT FROM 'true'
                       AND NOT EXISTS (
                           SELECT 1 FROM etown_provider p
                            WHERE p.userprofileid = up.userprofileid
                              AND p.deleted IS DISTINCT FROM 'true'
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM etown_member m
                            WHERE m.userprofileid = up.userprofileid
                              AND m.deleted IS DISTINCT FROM 'true'
                       )
                    RETURNING up.userprofileid
                    """,
                    userprofile_ids,
                    stamp,
                )
                flagged = {r["userprofileid"] for r in flagged_profiles}
                result["userprofile_deleted"] = len(flagged)
                result["userprofile_kept"] = [u for u in userprofile_ids if u not in flagged]
                for kept in result["userprofile_kept"]:
                    logger.info(
                        f"{_LOG_DELETE} | USERPROFILE_KEPT | userprofileid={kept} "
                        f"| still referenced by another provider or a member row"
                    )

    logger.info(
        f"{_LOG_DELETE} | SUCCESS | {match_label} | providers={result['provider_deleted']} "
        f"| services={result['providerservices_deleted']} "
        f"| services_copy={result['providerservices_copy_deleted']} "
        f"| etownprovider={result['etownprovider_deleted']} "
        f"| provider_details={result['provider_details_deleted']} "
        f"| userprofiles={result['userprofile_deleted']} "
        f"| contacts_kept={','.join(result['contact_ids']) or '-'}"
    )
    return result


def _last_10_digits(mobilenumber: Optional[str]) -> str:
    """Return the last 10 digits of a mobile number."""
    return "".join(ch for ch in str(mobilenumber or "") if ch.isdigit())[-10:]


@with_retry()
async def find_providers(
    mobilenumber: Optional[str] = None,
    businessname: Optional[str] = None,
    limit: int = FIND_PROVIDERS_LIMIT,
) -> Dict[str, Any]:
    """List up to limit non-verified providers, optionally filtered by mobile last-10 digits and business name.

    Args:
        mobilenumber: Optional mobile number; ignored as a filter when under 10 digits.
        businessname: Optional business name, compared case-insensitively.
        limit: Maximum rows; values below 1 fall back to FIND_PROVIDERS_LIMIT.

    Returns:
        Dict of found flag, filters applied, partial_mobile flag and the provider rows.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    digits = _last_10_digits(mobilenumber)
    business = (businessname or "").strip() or None
    row_limit = limit if limit and limit > 0 else FIND_PROVIDERS_LIMIT

    partial_mobile = bool(str(mobilenumber or "").strip()) and len(digits) < 10

    result: Dict[str, Any] = {
        "found": False,
        "mobilenumber": mobilenumber,
        "digits": digits,
        "businessname": business,
        "limit": row_limit,
        "partial_mobile": partial_mobile,
        "providers": [],
    }

    if partial_mobile:
        logger.warning(f"{_LOG_LINKED} | BAD_INPUT | mobile_digits={len(digits)}")
        return result

    rows = await Database.fetch(
        """
        SELECT providerid, businessname, publicsearchprovider, mobilenumber,
               providertype, contactid, userprofileid
        FROM etown_provider
        WHERE lower(btrim(COALESCE(publicsearchprovider, ''))) = $1::text
          AND ($2::text IS NULL
               OR right(regexp_replace(COALESCE(mobilenumber, ''), '\\D', '', 'g'), 10) = $2::text)
          AND ($3::text IS NULL
               OR lower(btrim(COALESCE(businessname, ''))) = lower(btrim($3::text)))
        ORDER BY lower(btrim(COALESCE(businessname, ''))), providerid
        LIMIT $4::int
        """,
        NONVERIFIED_PUBLICSEARCHPROVIDER,
        digits or None,
        business,
        row_limit,
    )

    result["providers"] = [
        {
            "providerid": row["providerid"],
            "businessname": row["businessname"],
            "publicsearchprovider": row["publicsearchprovider"],
            "mobilenumber": row["mobilenumber"],
            "providertype": row["providertype"],
            "contactid": row["contactid"],
            "userprofileid": row["userprofileid"],
        }
        for row in rows
    ]
    result["found"] = bool(result["providers"])

    mobile_log = f"***{digits[-4:]}" if digits else "-"
    if not result["found"]:
        logger.warning(
            f"{_LOG_LINKED} | NOT_FOUND | mobile={mobile_log} | business='{business or '-'}'"
        )
    else:
        logger.info(
            f"{_LOG_LINKED} | FOUND | mobile={mobile_log} | business='{business or '-'}' "
            f"| providers={len(result['providers'])} | limit={row_limit}"
        )
    return result


@with_retry()
async def update_provider_mobilenumber(
    businessname: str,
    mobilenumber: str,
    providerid: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a new mobile number to the matched listings' provider, userprofile, contact and service rows in one transaction.

    Args:
        businessname: Business name identifying the listings, compared case-insensitively.
        mobilenumber: The new mobile number; must contain at least 10 digits.
        providerid: Optional provider GUID narrowing the update to one listing.

    Returns:
        Dict of found and bad_mobile flags, touched ids, previous numbers and per-table row counts.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    business = (businessname or "").strip()
    new_mobile = str(mobilenumber or "").strip()
    new_digits = _last_10_digits(new_mobile)

    result: Dict[str, Any] = {
        "found": False,
        "bad_mobile": False,
        "businessname": business,
        "mobilenumber": new_mobile,
        "provider_ids": [],
        "userprofile_ids": [],
        "contact_ids": [],
        "providerservices_ids": [],
        "previous_mobilenumbers": [],
        "provider_updated": 0,
        "userprofile_updated": 0,
        "contact_updated": 0,
        "contact_userlogin_updated": 0,
        "providerservices_updated": 0,
        "providerservices_copy_updated": 0,
    }

    if len(new_digits) < 10:
        result["bad_mobile"] = True
        logger.warning(f"{_LOG_MOBILE} | BAD_MOBILE | digits={len(new_digits)}")
        return result

    if not business:
        logger.warning(f"{_LOG_MOBILE} | NO_BUSINESSNAME")
        return result

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            provider_rows = await con.fetch(
                """
                SELECT providerid, userprofileid, contactid, mobilenumber
                FROM etown_provider
                WHERE lower(btrim(COALESCE(businessname, ''))) = lower(btrim($1::text))
                  AND ($2::text IS NULL OR providerid = $2::text)
                FOR UPDATE
                """,
                business,
                providerid,
            )

            if not provider_rows:
                logger.warning(
                    f"{_LOG_MOBILE} | NOT_FOUND | business='{business}' "
                    f"| providerid={providerid or '-'}"
                )
                return result

            result["found"] = True
            provider_ids = [r["providerid"] for r in provider_rows if r["providerid"]]
            userprofile_ids = sorted({r["userprofileid"] for r in provider_rows if r["userprofileid"]})
            contact_ids = sorted({r["contactid"] for r in provider_rows if r["contactid"]})
            old_numbers = sorted({r["mobilenumber"] for r in provider_rows if r["mobilenumber"]})
            old_digits = sorted({_last_10_digits(n) for n in old_numbers if _last_10_digits(n)})

            result["provider_ids"] = provider_ids
            result["userprofile_ids"] = userprofile_ids
            result["contact_ids"] = contact_ids
            result["previous_mobilenumbers"] = old_numbers

            if len(provider_ids) > 1:
                logger.warning(
                    f"{_LOG_MOBILE} | MULTIPLE_MATCHES | business='{business}' "
                    f"| providers={len(provider_ids)} | all of them are being updated"
                )

            provider_status = await con.execute(
                "UPDATE etown_provider SET mobilenumber = $2::text, modifieddate = NOW() "
                "WHERE providerid = ANY($1::text[])",
                provider_ids,
                new_mobile,
            )
            result["provider_updated"] = _affected_rows(provider_status)

            if userprofile_ids:
                userprofile_status = await con.execute(
                    """
                    UPDATE etown_userprofile
                    SET mobilenumber = $2::text,
                        modifieddate = NOW()
                    WHERE userprofileid = ANY($1::text[])
                    """,
                    userprofile_ids,
                    new_mobile,
                )
                result["userprofile_updated"] = _affected_rows(userprofile_status)
            else:
                logger.warning(f"{_LOG_MOBILE} | NO_USERPROFILEID | business='{business}'")

            if contact_ids:
                contact_status = await con.execute(
                    "UPDATE etown_contact SET mobilephone = $2::text, modifieddate = NOW() "
                    "WHERE contactid = ANY($1::text[])",
                    contact_ids,
                    new_mobile,
                )
                result["contact_updated"] = _affected_rows(contact_status)

                if old_digits:
                    userlogin_status = await con.execute(
                        """
                        UPDATE etown_contact
                        SET userlogin = $2::text,
                            modifieddate = NOW()
                        WHERE contactid = ANY($1::text[])
                          AND right(regexp_replace(COALESCE(userlogin, ''), '\\D', '', 'g'), 10)
                              = ANY($3::text[])
                        """,
                        contact_ids,
                        new_mobile,
                        old_digits,
                    )
                    result["contact_userlogin_updated"] = _affected_rows(userlogin_status)
            else:
                logger.warning(f"{_LOG_MOBILE} | NO_CONTACTID | business='{business}'")

            services_rows = await con.fetch(
                """
                UPDATE etown_providerservices
                SET mobilenumber = $2::text,
                    modifieddate = NOW()
                WHERE providerid = ANY($1::text[])
                RETURNING providerservicesid
                """,
                provider_ids,
                new_mobile,
            )
            result["providerservices_updated"] = len(services_rows)
            result["providerservices_ids"] = [
                r["providerservicesid"] for r in services_rows if r["providerservicesid"]
            ]

            services_copy_status = await con.execute(
                """
                UPDATE etown_providerservices_copy
                SET mobilenumber = $2::text,
                    modifieddate = NOW()
                WHERE providerid = ANY($1::text[])
                """,
                provider_ids,
                new_mobile,
            )
            result["providerservices_copy_updated"] = _affected_rows(services_copy_status)

    logger.info(
        f"{_LOG_MOBILE} | SUCCESS | business='{business}' | mobile=***{new_digits[-4:]} "
        f"| provider={result['provider_updated']} "
        f"| userprofile={result['userprofile_updated']} "
        f"| contact={result['contact_updated']} "
        f"| userlogin={result['contact_userlogin_updated']} "
        f"| services={result['providerservices_updated']} "
        f"| services_copy={result['providerservices_copy_updated']}"
    )
    return result


@with_retry()
async def link_provider_to_member(
    businessname: str,
    mobilenumber: str,
    providerid: Optional[str] = None,
) -> Dict[str, Any]:
    """Point the matched listings' mobilenumber and contactid at the single member contact owning a mobile number.

    Args:
        businessname: Business name identifying the listings, compared case-insensitively.
        mobilenumber: The member's mobile number; must contain at least 10 digits.
        providerid: Optional provider GUID narrowing the change to one listing.

    Returns:
        Dict of found, member and ambiguity flags, resolved and previous ids, and per-table row counts.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    business = (businessname or "").strip()
    new_mobile = str(mobilenumber or "").strip()
    new_digits = _last_10_digits(new_mobile)

    result: Dict[str, Any] = {
        "found": False,
        "member_found": False,
        "ambiguous_member": False,
        "bad_mobile": False,
        "businessname": business,
        "mobilenumber": new_mobile,
        "member_contactid": None,
        "member_userprofile_ids": [],
        "member_contactids": [],
        "provider_ids": [],
        "providerservices_ids": [],
        "previous_contactids": [],
        "previous_mobilenumbers": [],
        "provider_userprofile_ids": [],
        "userprofile_contactid_mismatch": False,
        "provider_updated": 0,
        "providerservices_updated": 0,
        "providerservices_copy_updated": 0,
    }

    if len(new_digits) < 10:
        result["bad_mobile"] = True
        logger.warning(f"{_LOG_LINK_MEMBER} | BAD_MOBILE | digits={len(new_digits)}")
        return result

    if not business:
        logger.warning(f"{_LOG_LINK_MEMBER} | NO_BUSINESSNAME")
        return result

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            member_rows = await con.fetch(
                """
                SELECT userprofileid, contactid
                FROM etown_userprofile
                WHERE right(regexp_replace(COALESCE(mobilenumber, ''), '\\D', '', 'g'), 10)
                      = $1::text
                  AND contactid IS NOT NULL
                """,
                new_digits,
            )

            member_contactids = sorted({r["contactid"] for r in member_rows if r["contactid"]})
            result["member_contactids"] = member_contactids
            result["member_userprofile_ids"] = sorted(
                {r["userprofileid"] for r in member_rows if r["userprofileid"]}
            )

            if not member_contactids:
                logger.warning(
                    f"{_LOG_LINK_MEMBER} | MEMBER_NOT_FOUND | mobile=***{new_digits[-4:]}"
                )
                return result

            if len(member_contactids) > 1:
                result["member_found"] = True
                result["ambiguous_member"] = True
                logger.warning(
                    f"{_LOG_LINK_MEMBER} | AMBIGUOUS_MEMBER | mobile=***{new_digits[-4:]} "
                    f"| contacts={len(member_contactids)} | nothing written"
                )
                return result

            result["member_found"] = True
            member_contactid = member_contactids[0]
            result["member_contactid"] = member_contactid

            provider_rows = await con.fetch(
                """
                SELECT providerid, contactid, userprofileid, mobilenumber
                FROM etown_provider
                WHERE lower(btrim(COALESCE(businessname, ''))) = lower(btrim($1::text))
                  AND ($2::text IS NULL OR providerid = $2::text)
                FOR UPDATE
                """,
                business,
                providerid,
            )

            if not provider_rows:
                logger.warning(
                    f"{_LOG_LINK_MEMBER} | PROVIDER_NOT_FOUND | business='{business}' "
                    f"| providerid={providerid or '-'}"
                )
                return result

            result["found"] = True
            provider_ids = [r["providerid"] for r in provider_rows if r["providerid"]]
            result["provider_ids"] = provider_ids
            result["previous_contactids"] = sorted(
                {r["contactid"] for r in provider_rows if r["contactid"]}
            )
            result["previous_mobilenumbers"] = sorted(
                {r["mobilenumber"] for r in provider_rows if r["mobilenumber"]}
            )
            provider_userprofile_ids = sorted(
                {r["userprofileid"] for r in provider_rows if r["userprofileid"]}
            )
            result["provider_userprofile_ids"] = provider_userprofile_ids

            if len(provider_ids) > 1:
                logger.warning(
                    f"{_LOG_LINK_MEMBER} | MULTIPLE_MATCHES | business='{business}' "
                    f"| providers={len(provider_ids)} | all of them are being re-linked"
                )

            if provider_userprofile_ids:
                mismatch_row = await con.fetchrow(
                    """
                    SELECT count(*) AS n
                    FROM etown_userprofile
                    WHERE userprofileid = ANY($1::text[])
                      AND COALESCE(contactid, '') <> $2::text
                    """,
                    provider_userprofile_ids,
                    member_contactid,
                )
                result["userprofile_contactid_mismatch"] = bool(
                    mismatch_row and mismatch_row["n"]
                )
                if result["userprofile_contactid_mismatch"]:
                    logger.warning(
                        f"{_LOG_LINK_MEMBER} | USERPROFILE_CONTACT_MISMATCH "
                        f"| business='{business}' | provider.contactid is now {member_contactid} "
                        f"| its userprofile(s) still belong to another contact"
                    )

            provider_status = await con.execute(
                """
                UPDATE etown_provider
                SET mobilenumber = $2::text,
                    contactid    = $3::text,
                    modifieddate = NOW()
                WHERE providerid = ANY($1::text[])
                """,
                provider_ids,
                new_mobile,
                member_contactid,
            )
            result["provider_updated"] = _affected_rows(provider_status)

            services_rows = await con.fetch(
                """
                UPDATE etown_providerservices
                SET mobilenumber = $2::text,
                    contactid    = $3::text,
                    modifieddate = NOW()
                WHERE providerid = ANY($1::text[])
                RETURNING providerservicesid
                """,
                provider_ids,
                new_mobile,
                member_contactid,
            )
            result["providerservices_updated"] = len(services_rows)
            result["providerservices_ids"] = [
                r["providerservicesid"] for r in services_rows if r["providerservicesid"]
            ]

            services_copy_status = await con.execute(
                """
                UPDATE etown_providerservices_copy
                SET mobilenumber = $2::text,
                    contactid    = $3::text,
                    modifieddate = NOW()
                WHERE providerid = ANY($1::text[])
                """,
                provider_ids,
                new_mobile,
                member_contactid,
            )
            result["providerservices_copy_updated"] = _affected_rows(services_copy_status)

    logger.info(
        f"{_LOG_LINK_MEMBER} | SUCCESS | business='{business}' | mobile=***{new_digits[-4:]} "
        f"| contactid={result['member_contactid']} "
        f"| provider={result['provider_updated']} "
        f"| services={result['providerservices_updated']} "
        f"| services_copy={result['providerservices_copy_updated']} "
        f"| userprofile_mismatch={result['userprofile_contactid_mismatch']}"
    )
    return result


@with_retry()
async def update_provider_businessname(
    providerid: str,
    businessname: str,
) -> Dict[str, Any]:
    """Rename one listing's businessname on its provider and two service tables in one transaction.

    Args:
        providerid: Provider GUID identifying the listing to rename.
        businessname: The new business name; stored trimmed and must not be blank.

    Returns:
        Dict of found and blank flags, previous name, touched ids, row counts and skipped tables.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    provider_id = str(providerid or "").strip()
    new_name = str(businessname or "").strip()

    skipped: List[str] = [
        "etown_contact",
        "etown_userprofile",
        "etown_etownprovider",
        "etown_offers",
        "etown_offerings",
        "etown_ratings",
    ]

    result: Dict[str, Any] = {
        "found": False,
        "blank_businessname": False,
        "providerid": provider_id,
        "businessname": new_name,
        "previous_businessname": None,
        "contactid": None,
        "userprofileid": None,
        "providerservices_ids": [],
        "providerservices_copy_ids": [],
        "provider_updated": 0,
        "providerservices_updated": 0,
        "providerservices_copy_updated": 0,
        "skipped": skipped,
    }

    if not new_name:
        result["blank_businessname"] = True
        logger.warning(f"{_LOG_BUSINESSNAME} | BLANK_BUSINESSNAME | providerid={provider_id}")
        return result

    if not provider_id:
        logger.warning(f"{_LOG_BUSINESSNAME} | NO_PROVIDERID")
        return result

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            provider_row = await con.fetchrow(
                """
                SELECT businessname, contactid, userprofileid
                FROM etown_provider
                WHERE providerid = $1::text
                FOR UPDATE
                """,
                provider_id,
            )

            if provider_row is None:
                logger.warning(f"{_LOG_BUSINESSNAME} | NOT_FOUND | providerid={provider_id}")
                return result

            result["found"] = True
            result["previous_businessname"] = provider_row["businessname"]
            result["contactid"] = provider_row["contactid"]
            result["userprofileid"] = provider_row["userprofileid"]

            provider_status = await con.execute(
                """
                UPDATE etown_provider
                SET businessname = $2::text,
                    modifieddate = NOW()
                WHERE providerid = $1::text
                """,
                provider_id,
                new_name,
            )
            result["provider_updated"] = _affected_rows(provider_status)

            services_rows = await con.fetch(
                """
                UPDATE etown_providerservices
                SET businessname = $2::text,
                    modifieddate = NOW()
                WHERE providerid = $1::text
                RETURNING providerservicesid
                """,
                provider_id,
                new_name,
            )
            result["providerservices_updated"] = len(services_rows)
            result["providerservices_ids"] = [
                r["providerservicesid"] for r in services_rows if r["providerservicesid"]
            ]

            services_copy_rows = await con.fetch(
                """
                UPDATE etown_providerservices_copy
                SET businessname = $2::text,
                    modifieddate = NOW()
                WHERE providerid = $1::text
                RETURNING providerservicesid
                """,
                provider_id,
                new_name,
            )
            result["providerservices_copy_updated"] = len(services_copy_rows)
            result["providerservices_copy_ids"] = [
                r["providerservicesid"] for r in services_copy_rows if r["providerservicesid"]
            ]

    logger.info(
        f"{_LOG_BUSINESSNAME} | SUCCESS | providerid={provider_id} | business='{new_name}' "
        f"| previous='{result['previous_businessname'] or '-'}' "
        f"| provider={result['provider_updated']} "
        f"| services={result['providerservices_updated']} "
        f"| services_copy={result['providerservices_copy_updated']}"
    )
    return result


@with_retry()
async def update_provider_source(
    mobilenumber: str,
    source: str,
) -> Dict[str, Any]:
    """Set source on every listing matching a mobile number's last 10 digits across provider and service tables.

    Args:
        mobilenumber: Provider mobile number; only its last 10 digits are matched.
        source: The new source value; stored trimmed and must not be blank.

    Returns:
        Dict of validation flags, matched names and ids, previous sources, row counts and skipped tables.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    digits = _last_10_digits(mobilenumber)
    new_source = str(source or "").strip()

    skipped: List[str] = [
        "etown_contact",
        "etown_userprofile",
        "etown_etownprovider",
    ]

    result: Dict[str, Any] = {
        "found": False,
        "bad_mobile": False,
        "blank_source": False,
        "mobilenumber": mobilenumber,
        "digits": digits,
        "source": new_source,
        "businessnames": [],
        "provider_ids": [],
        "previous_sources": [],
        "providerservices_ids": [],
        "providerservices_copy_ids": [],
        "provider_updated": 0,
        "providerservices_updated": 0,
        "providerservices_copy_updated": 0,
        "skipped": skipped,
    }

    if not new_source:
        result["blank_source"] = True
        logger.warning(f"{_LOG_SOURCE} | BLANK_SOURCE | mobile_digits={len(digits)}")
        return result

    if len(digits) < 10:
        result["bad_mobile"] = True
        logger.warning(f"{_LOG_SOURCE} | BAD_MOBILE | digits={len(digits)}")
        return result

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            provider_rows = await con.fetch(
                """
                SELECT providerid, businessname, source
                FROM etown_provider
                WHERE right(regexp_replace(COALESCE(mobilenumber, ''), '\\D', '', 'g'), 10) = $1::text
                FOR UPDATE
                """,
                digits,
            )

            if not provider_rows:
                logger.warning(f"{_LOG_SOURCE} | NOT_FOUND | mobile=***{digits[-4:]}")
                return result

            result["found"] = True
            provider_ids = [r["providerid"] for r in provider_rows if r["providerid"]]
            result["provider_ids"] = provider_ids
            result["businessnames"] = sorted({r["businessname"] for r in provider_rows if r["businessname"]})
            result["previous_sources"] = sorted({r["source"] for r in provider_rows if r["source"]})

            if len(provider_ids) > 1:
                logger.warning(
                    f"{_LOG_SOURCE} | MULTIPLE_MATCHES | mobile=***{digits[-4:]} "
                    f"| providers={len(provider_ids)} | all of them are being updated"
                )

            provider_status = await con.execute(
                """
                UPDATE etown_provider
                SET source = $2::text,
                    modifieddate = NOW()
                WHERE providerid = ANY($1::text[])
                """,
                provider_ids,
                new_source,
            )
            result["provider_updated"] = _affected_rows(provider_status)

            services_rows = await con.fetch(
                """
                UPDATE etown_providerservices
                SET source = $2::text,
                    modifieddate = NOW()
                WHERE providerid = ANY($1::text[])
                RETURNING providerservicesid
                """,
                provider_ids,
                new_source,
            )
            result["providerservices_updated"] = len(services_rows)
            result["providerservices_ids"] = [
                r["providerservicesid"] for r in services_rows if r["providerservicesid"]
            ]

            services_copy_rows = await con.fetch(
                """
                UPDATE etown_providerservices_copy
                SET source = $2::text,
                    modifieddate = NOW()
                WHERE providerid = ANY($1::text[])
                RETURNING providerservicesid
                """,
                provider_ids,
                new_source,
            )
            result["providerservices_copy_updated"] = len(services_copy_rows)
            result["providerservices_copy_ids"] = [
                r["providerservicesid"] for r in services_copy_rows if r["providerservicesid"]
            ]

    logger.info(
        f"{_LOG_SOURCE} | SUCCESS | mobile=***{digits[-4:]} | source='{new_source}' "
        f"| previous={','.join(result['previous_sources']) or '-'} "
        f"| provider={result['provider_updated']} "
        f"| services={result['providerservices_updated']} "
        f"| services_copy={result['providerservices_copy_updated']}"
    )
    return result


async def apply_provider_review_approval(
    reviewid: str,
    providerid: str,
    review: Optional[str] = None,
    rejectedreason: Optional[str] = None,
    action_mode: Optional[str] = None,
    mobilenumber: Optional[str] = None,
    alternatemobile: Optional[str] = None,
    businessname: Optional[str] = None,
    businesscategory: Optional[str] = None,
    servicecategory: Optional[str] = None,
    servicetype: Optional[str] = None,
    description: Optional[str] = None,
    services: Optional[str] = None,
    servicedetails: Optional[str] = None,
    types: Optional[str] = None,
    language: Optional[str] = None,
    marketing_person_name: Optional[str] = None,
    businesslogo: Optional[str] = None,
    email: Optional[str] = None,
    formattedaddress: Optional[str] = None,
    additionaladdressinfo: Optional[str] = None,
    arealocation: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    interested: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a worked review's decision and non-None values to the review, provider, service and contact rows in one transaction.

    Args:
        reviewid: etown_provider_review.reviewid of the worked row.
        providerid: Provider GUID the review names.
        review: The approver's decision, written verbatim.
        rejectedreason: The approver's reason, written verbatim.
        action_mode: 'create' or 'edit', as captured.
        mobilenumber: Provider mobile number.
        alternatemobile: Secondary mobile number.
        businessname: Business name.
        businesscategory: Business category.
        servicecategory: Service category, written to the two service tables only.
        servicetype: Service type.
        description: Business description; servicedescription on the service tables.
        services: Services blob, written to the two service tables only.
        servicedetails: Service details blob, written to the review row only.
        types: Place types, written to the review row only.
        language: Language, written to the review row only.
        marketing_person_name: Field agent who captured it.
        businesslogo: Logo URL; also written to serviceimageurl and serviceimageurl1.
        email: Email address.
        formattedaddress: Street address.
        additionaladdressinfo: Second address line.
        arealocation: Area or locality; areaname on the provider tables.
        city: City name; also written to etown_provider.townname.
        state: State; statecode on the provider tables.
        latitude: Latitude in degrees, already range-checked.
        longitude: Longitude in degrees, already range-checked.
        interested: 'true' or 'false', written to the review row and etown_provider only.

    Returns:
        Dict of found flag, resolved contactid, per-table row counts and touched service ids.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    lat_text = str(latitude) if latitude is not None else None
    lng_text = str(longitude) if longitude is not None else None

    result: Dict[str, Any] = {
        "found": False,
        "reviewid": reviewid,
        "providerid": providerid,
        "contactid": None,
        "review_updated": 0,
        "provider_updated": 0,
        "providerservices_updated": 0,
        "providerservices_copy_updated": 0,
        "contact_updated": 0,
        "providerservices_ids": [],
        "providerservices_copy_ids": [],
    }

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            review_row = await con.fetchrow(
                """
                UPDATE etown_provider_review
                SET review                = COALESCE($2::text, review),
                    rejectedreason        = COALESCE($3::text, rejectedreason),
                    action_mode           = COALESCE($4::text, action_mode),
                    mobilenumber          = COALESCE($5::text, mobilenumber),
                    alternatemobile       = COALESCE($6::text, alternatemobile),
                    businessname          = COALESCE($7::text, businessname),
                    business_category     = COALESCE($8::text, business_category),
                    service_category      = COALESCE($9::text, service_category),
                    servicetype           = COALESCE($10::text, servicetype),
                    description           = COALESCE($11::text, description),
                    services              = COALESCE($12::text, services),
                    servicedetails        = COALESCE($13::text, servicedetails),
                    types                 = COALESCE($14::text, types),
                    language              = COALESCE($15::text, language),
                    marketing_person_name = COALESCE($16::text, marketing_person_name),
                    businesslogo          = COALESCE($17::text, businesslogo),
                    email                 = COALESCE($18::text, email),
                    formattedaddress      = COALESCE($19::text, formattedaddress),
                    additionaladdressinfo = COALESCE($20::text, additionaladdressinfo),
                    area_location         = COALESCE($21::text, area_location),
                    city                  = COALESCE($22::text, city),
                    state                 = COALESCE($23::text, state),
                    latitude              = COALESCE($24::text, latitude),
                    longitude             = COALESCE($25::text, longitude),
                    providerid            = COALESCE($26::text, providerid),
                    interested            = COALESCE($27::text, interested)
                WHERE reviewid = $1::text
                RETURNING reviewid
                """,
                reviewid,
                review,
                rejectedreason,
                action_mode,
                mobilenumber,
                alternatemobile,
                businessname,
                businesscategory,
                servicecategory,
                servicetype,
                description,
                services,
                servicedetails,
                types,
                language,
                marketing_person_name,
                businesslogo,
                email,
                formattedaddress,
                additionaladdressinfo,
                arealocation,
                city,
                state,
                lat_text,
                lng_text,
                providerid,
                interested,
            )
            result["review_updated"] = 1 if review_row else 0

            provider_row = await con.fetchrow(
                """
                UPDATE etown_provider
                SET boosted               = FALSE,
                    whatsapp              = TRUE,
                    disabled              = FALSE,
                    alert                 = FALSE,
                    businessname          = COALESCE($2::text, businessname),
                    businesscategory      = COALESCE($3::text, businesscategory),
                    servicetype           = COALESCE($4::text, servicetype),
                    description           = COALESCE($5::text, description),
                    marketing_person_name = COALESCE($6::text, marketing_person_name),
                    serviceimageurl       = COALESCE($7::text, serviceimageurl),
                    email                 = COALESCE($8::text, email),
                    mobilenumber          = COALESCE($9::text, mobilenumber),
                    alternatemobile       = COALESCE($10::text, alternatemobile),
                    address               = COALESCE($11::text, address),
                    additionaladdressinfo = COALESCE($12::text, additionaladdressinfo),
                    areaname              = COALESCE($13::text, areaname),
                    city                  = COALESCE($14::text, city),
                    townname              = COALESCE($14::text, townname),
                    statecode             = COALESCE($15::text, statecode),
                    latitude              = COALESCE($16::double precision, latitude),
                    longitude             = COALESCE($17::double precision, longitude),
                    interested            = COALESCE($18::text, interested),
                    modifieddate = NOW()
                WHERE providerid = $1::text
                RETURNING providerid, contactid
                """,
                providerid,
                businessname,
                businesscategory,
                servicetype,
                description,
                marketing_person_name,
                businesslogo,
                email,
                mobilenumber,
                alternatemobile,
                formattedaddress,
                additionaladdressinfo,
                arealocation,
                city,
                state,
                latitude,
                longitude,
                interested,
            )

            if provider_row is None:
                logger.warning(
                    f"{_LOG_REVIEW_APPROVAL} | PROVIDER_NOT_FOUND | reviewid={reviewid} "
                    f"| providerid={providerid} | review_updated={result['review_updated']}"
                )
                return result

            result["found"] = True
            result["provider_updated"] = 1
            result["contactid"] = provider_row["contactid"]

            services_rows = await con.fetch(
                """
                UPDATE etown_providerservices
                SET businessname          = COALESCE($2::text, businessname),
                    businesscategory      = COALESCE($3::text, businesscategory),
                    servicecategory       = COALESCE($4::text, servicecategory),
                    servicedescription    = COALESCE($5::text, servicedescription),
                    services              = COALESCE($6::text, services),
                    serviceimageurl       = COALESCE($7::text, serviceimageurl),
                    serviceimageurl1      = COALESCE($7::text, serviceimageurl1),
                    email                 = COALESCE($8::text, email),
                    mobilenumber          = COALESCE($9::text, mobilenumber),
                    address               = COALESCE($10::text, address),
                    additionaladdressinfo = COALESCE($11::text, additionaladdressinfo),
                    areaname              = COALESCE($12::text, areaname),
                    city                  = COALESCE($13::text, city),
                    statecode             = COALESCE($14::text, statecode),
                    latitude              = COALESCE($15::text, latitude),
                    longitude             = COALESCE($16::text, longitude),
                    modifieddate = NOW()
                WHERE providerid = $1::text
                RETURNING providerservicesid
                """,
                providerid,
                businessname,
                businesscategory,
                servicecategory,
                description,
                services,
                businesslogo,
                email,
                mobilenumber,
                formattedaddress,
                additionaladdressinfo,
                arealocation,
                city,
                state,
                lat_text,
                lng_text,
            )
            result["providerservices_updated"] = len(services_rows)
            result["providerservices_ids"] = [
                r["providerservicesid"] for r in services_rows if r["providerservicesid"]
            ]

            services_copy_rows = await con.fetch(
                """
                UPDATE etown_providerservices_copy
                SET businessname          = COALESCE($2::text, businessname),
                    businesscategory      = COALESCE($3::text, businesscategory),
                    servicecategory       = COALESCE($4::text, servicecategory),
                    servicedescription    = COALESCE($5::text, servicedescription),
                    services              = COALESCE($6::text, services),
                    serviceimageurl       = COALESCE($7::text, serviceimageurl),
                    serviceimageurl1      = COALESCE($7::text, serviceimageurl1),
                    email                 = COALESCE($8::text, email),
                    mobilenumber          = COALESCE($9::text, mobilenumber),
                    address               = COALESCE($10::text, address),
                    additionaladdressinfo = COALESCE($11::text, additionaladdressinfo),
                    areaname              = COALESCE($12::text, areaname),
                    city                  = COALESCE($13::text, city),
                    statecode             = COALESCE($14::text, statecode),
                    latitude              = COALESCE($15::double precision, latitude),
                    longitude             = COALESCE($16::double precision, longitude),
                    modifieddate = NOW()
                WHERE providerid = $1::text
                RETURNING providerservicesid
                """,
                providerid,
                businessname,
                businesscategory,
                servicecategory,
                description,
                services,
                businesslogo,
                email,
                mobilenumber,
                formattedaddress,
                additionaladdressinfo,
                arealocation,
                city,
                state,
                latitude,
                longitude,
            )
            result["providerservices_copy_updated"] = len(services_copy_rows)
            result["providerservices_copy_ids"] = [
                r["providerservicesid"] for r in services_copy_rows if r["providerservicesid"]
            ]

            if result["contactid"]:
                contact_row = await con.fetchrow(
                    """
                    UPDATE etown_contact
                    SET email                 = COALESCE($2::text, email),
                        address               = COALESCE($3::text, address),
                        additionaladdressinfo = COALESCE($4::text, additionaladdressinfo),
                        city                  = COALESCE($5::text, city),
                        statecode             = COALESCE($6::text, statecode),
                        latitude              = COALESCE($7::double precision, latitude),
                        longitude             = COALESCE($8::double precision, longitude),
                        mobilephone           = COALESCE($9::text, mobilephone),
                        alternatemobile       = COALESCE($10::text, alternatemobile),
                        userlogin = CASE
                            WHEN $9::text IS NOT NULL
                             AND right(regexp_replace(COALESCE(userlogin, ''), '\\D', '', 'g'), 10)
                                 = right(regexp_replace(COALESCE(mobilephone, ''), '\\D', '', 'g'), 10)
                            THEN $9::text
                            ELSE userlogin
                        END,
                        modifieddate = NOW()
                    WHERE contactid = $1::text
                    RETURNING contactid
                    """,
                    result["contactid"],
                    email,
                    formattedaddress,
                    additionaladdressinfo,
                    city,
                    state,
                    latitude,
                    longitude,
                    mobilenumber,
                    alternatemobile,
                )
                result["contact_updated"] = 1 if contact_row else 0

    logger.info(
        f"{_LOG_REVIEW_APPROVAL} | SUCCESS | reviewid={reviewid} | providerid={providerid} "
        f"| provider={result['provider_updated']} "
        f"| services={result['providerservices_updated']} "
        f"| services_copy={result['providerservices_copy_updated']} "
        f"| contact={result['contact_updated']}"
    )
    return result


@with_retry()
async def apply_town_review_status(
    townreviewid: str,
    townprofileid: str,
    imageurl: str,
    status: str,
) -> Dict[str, Any]:
    """Set a town review's status and, when approved, write imageurl to etown_townprofile.townimage in one transaction.

    Args:
        townreviewid: GUID of the etown_townreview row.
        townprofileid: Town profile whose townimage is set on approval.
        imageurl: Image written to the town's townimage when approved.
        status: New status, stored lowercased; only approved writes the image.

    Returns:
        Dict of found flags, previous status, image written, per-table row counts and the updated review row.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    review_id = str(townreviewid or "").strip()
    town_id = str(townprofileid or "").strip()
    new_status = str(status or "").strip().lower()

    result: Dict[str, Any] = {
        "found": False,
        "town_found": False,
        "townreviewid": review_id,
        "townprofileid": town_id,
        "status": new_status,
        "previous_status": None,
        "imageurl": None,
        "townreview_updated": 0,
        "townprofile_updated": 0,
        "review": None,
    }

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            review_row = await con.fetchrow(
                """
                SELECT status
                FROM public.etown_townreview
                WHERE townreviewid = $1::uuid
                FOR UPDATE
                """,
                review_id,
            )
            if not review_row:
                logger.warning(f"{_LOG_TOWN_REVIEW} | REVIEW_NOT_FOUND | townreviewid={review_id}")
                return result
            result["found"] = True
            result["previous_status"] = review_row["status"]

            if new_status == "approved":
                image = to_relative_blob_url(str(imageurl or "").strip())
                town_exists = await con.fetchval(
                    "SELECT 1 FROM public.etown_townprofile WHERE townprofileid = $1 FOR UPDATE",
                    town_id,
                )
                if not town_exists:
                    logger.warning(f"{_LOG_TOWN_REVIEW} | TOWN_NOT_FOUND | townprofileid={town_id}")
                    return result
                result["town_found"] = True
                result["imageurl"] = image
                result["townprofile_updated"] = _affected_rows(await con.execute(
                    "UPDATE public.etown_townprofile SET townimage = $2 WHERE townprofileid = $1",
                    town_id, image,
                ))

            review = await con.fetchrow(
                """
                UPDATE public.etown_townreview
                SET status = $2, modifiedon = now() AT TIME ZONE 'utc'
                WHERE townreviewid = $1::uuid
                RETURNING townreviewid::text AS townreviewid, townprofileid, townname, contactid,
                          contactidname, userprofileid, imageurl, description, status
                """,
                review_id, new_status,
            )
            result["townreview_updated"] = 1 if review else 0
            result["review"] = dict(review) if review else None

    logger.info(
        f"{_LOG_TOWN_REVIEW} | APPLIED | townreviewid={review_id} | townprofileid={town_id} "
        f"| status={result['previous_status']}->{new_status} "
        f"| townreview={result['townreview_updated']} | townprofile={result['townprofile_updated']}"
    )
    return result


_EMBEDDING_ID_COLUMNS = {
    "etown_contact": "contactid",
    "etown_provider": "providerid",
    "etown_providerservices_copy": "providerservicesid",
    "etown_member": "memberid",
    "etown_townprofile": "townprofileid",
}

_ADDRESS_EMBEDDING_FIELDS = {"address", "city"}

_HASH_COLUMN = "content_vector_hash"


def _embedding_field_mapping() -> Dict[str, List[str]]:
    """Lazily import and return the embeddings endpoint's TABLE_FIELD_MAPPING."""
    from app.api.v1.endpoints.embeddings import TABLE_FIELD_MAPPING

    return TABLE_FIELD_MAPPING


async def refresh_content_vectors(
    targets: Dict[str, List[str]],
) -> Dict[str, Any]:
    """Re-embed the given rows from their mapped fields, skipping unmapped tables and missing columns, without raising.

    Args:
        targets: Mapping of table name to row ids to re-embed.

    Returns:
        Dict of updated and failed counts, skipped tables and missing columns.
    """
    mapping = _embedding_field_mapping()
    summary: Dict[str, Any] = {
        "updated": 0,
        "failed": 0,
        "skipped_tables": [],
        "missing_columns": [],
    }

    for table, row_ids in targets.items():
        row_ids = [rid for rid in (row_ids or []) if rid]
        if not row_ids:
            continue

        fields = mapping.get(table)
        id_column = _EMBEDDING_ID_COLUMNS.get(table)
        if not fields or not id_column:
            summary["skipped_tables"].append(table)
            logger.warning(f"{_LOG_EMBEDDING} | SKIP_TABLE | table={table} | no field mapping")
            continue

        try:
            present = await _existing_columns(table, fields)
        except Exception as exc:
            summary["failed"] += len(row_ids)
            logger.error(
                f"{_LOG_EMBEDDING} | COLUMN_LOOKUP_FAILED | table={table} | {exc}", exc_info=True
            )
            continue

        usable = [f for f in fields if f in present]
        for missing in (f for f in fields if f not in present):
            summary["missing_columns"].append(f"{table}.{missing}")
            logger.warning(
                f"{_LOG_EMBEDDING} | MISSING_COLUMN | table={table} | column={missing} "
                f"| excluded from the embedding text"
            )
        if not usable:
            summary["skipped_tables"].append(table)
            continue

        for row_id in row_ids:
            try:
                if await _refresh_one_content_vector(table, id_column, usable, row_id):
                    summary["updated"] += 1
            except Exception as exc:
                summary["failed"] += 1
                logger.error(
                    f"{_LOG_EMBEDDING} | FAILED | table={table} | id={row_id} | {exc}",
                    exc_info=True,
                )

    logger.info(
        f"{_LOG_EMBEDDING} | COMPLETE | updated={summary['updated']} | failed={summary['failed']}"
    )
    return summary


@with_retry()
async def _existing_columns(table: str, columns: List[str]) -> set:
    """Return the subset of columns that exist on the table per information_schema."""
    rows = await Database.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1 AND column_name = ANY($2::text[])
        """,
        table,
        columns,
    )
    return {row["column_name"] for row in rows}


async def _refresh_one_content_vector(
    table: str, id_column: str, fields: List[str], row_id: str
) -> bool:
    """Rebuild and store one row's content_vector, returning False when there was nothing to embed."""
    from app.utils.embeddings import generate_embedding, validate_embedding_dimensions

    columns = ", ".join(fields)
    record = await Database.fetchrow(
        f"SELECT {columns} FROM {table} WHERE {id_column} = $1", row_id
    )
    if record is None:
        logger.warning(f"{_LOG_EMBEDDING} | ROW_MISSING | table={table} | id={row_id}")
        return False

    text_parts = []
    for field in fields:
        value = record.get(field)
        if value is None:
            continue
        text = " ".join(str(value).split())
        if text:
            text_parts.append(f"{field}: {text}")

    if not text_parts:
        logger.warning(f"{_LOG_EMBEDDING} | NO_TEXT | table={table} | id={row_id}")
        return False

    text_data = " | ".join(text_parts)
    embedding = await generate_embedding(text_data)
    if not validate_embedding_dimensions(embedding):
        logger.error(
            f"{_LOG_EMBEDDING} | BAD_DIMENSIONS | table={table} | id={row_id} "
            f"| dimensions={len(embedding)}"
        )
        return False

    vector_literal = "[" + ",".join(map(str, embedding)) + "]"
    if await _existing_columns(table, [_HASH_COLUMN]):
        await Database.execute(
            f"UPDATE {table} SET content_vector = $1::vector, {_HASH_COLUMN} = $2 "
            f"WHERE {id_column} = $3",
            vector_literal,
            hashlib.sha256(text_data.encode("utf-8")).hexdigest(),
            row_id,
        )
    else:
        await Database.execute(
            f"UPDATE {table} SET content_vector = $1::vector WHERE {id_column} = $2",
            vector_literal,
            row_id,
        )
    logger.info(f"{_LOG_EMBEDDING} | SUCCESS | table={table} | id={row_id}")
    return True


async def refresh_address_embeddings(
    contactid: str,
    fields: List[str],
    provider_ids: Optional[List[str]] = None,
    providerservices_copy_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Re-embed contact, provider and copy rows when address or city changed; otherwise return a zeroed summary.

    Args:
        contactid: Contact whose row may need re-embedding.
        fields: Address fields the caller supplied.
        provider_ids: Provider rows that were updated.
        providerservices_copy_ids: etown_providerservices_copy rows that were updated.

    Returns:
        The refresh_content_vectors summary, or a zeroed one when nothing applied.
    """
    changed = _ADDRESS_EMBEDDING_FIELDS.intersection(fields or [])
    if not changed:
        logger.info(
            f"{_LOG_EMBEDDING} | NOOP | contactid={contactid} "
            f"| no embedded field changed (fields={','.join(fields or []) or '-'})"
        )
        return {"updated": 0, "failed": 0, "skipped_tables": [], "missing_columns": []}

    targets: Dict[str, List[str]] = {}
    if "city" in changed:
        targets["etown_contact"] = [contactid]
    if changed:
        targets["etown_provider"] = list(provider_ids or [])
    if "address" in changed:
        targets["etown_providerservices_copy"] = list(providerservices_copy_ids or [])

    return await refresh_content_vectors(targets)


async def refresh_businessname_embeddings(
    provider_ids: Optional[List[str]] = None,
    providerservices_copy_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Re-embed renamed provider and providerservices_copy rows and return the refresh summary."""
    return await refresh_content_vectors({
        "etown_provider": list(provider_ids or []),
        "etown_providerservices_copy": list(providerservices_copy_ids or []),
    })
