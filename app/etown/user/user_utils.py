"""User Utilities - Postgres business logic for registration, phone ownership, profiles and locations.

Domain layer behind /api/users: registers members and providers, checks whether a user exists and who owns a mobile number, maintains profiles, consents, addresses and saved locations, aggregates the service list, tracks logins, and schedules the Dataverse mirror and welcome WhatsApp after registration.

Features:
    - User Registration: Complete registration workflow — new user and unverified-user update paths
    - Email Uniqueness Check: Rejects a new signup only when the email belongs to a verified contact
    - Profile Image Upload: base64 → Azure Blob Storage → contact.profileimage URL
    - Role Branching: role=1 inserts into etown_provider, role=2 into etown_member
    - Existing Unverified User: Updates contact + creates missing profile/detail when verified=False
    - Shared Inner DB Helpers: userprofile/member/provider insert helpers reused by switch_profile_utils
    - Existence Checking: Mobile number / email-based user existence validation, with a device token's credsupdate flag
    - Dataverse Sync: Background sync after registration via sync_registration_to_dataverse
    - Welcome Notification: WhatsApp welcome message after successful registration
    - Profile Editing: update_member_profile_in_db() propagates name/email/mobile edits to related rows
    - Location Management: add/edit/delete/fetch a contact's saved etown_userlocationdetails rows
    - Consent & Address Updates: partial-field updates to aisearch/chattie consent and address/geo columns
    - Service List Aggregation: fetch_service_list() builds the profile, circle and invitation view
    - Business Lookup: fetch_provider_business_list() returns the live businesses on a mobile number
    - Login/Activity Tracking: Last-login, signup-flag reset and active-user tracking
    - Phone Ownership: Verified-first owner resolution across primary and alternate mobiles
    - Staff Flags: Etown_staff and Etown_FOS read from etown_staff_numbers, plus a rostered number's captures today
    - Alternate Mobile Check: check_alternate_mobile_status() classifies an alternate number

Classes:
    UserService(): Static-method façade for registration, existence checks and format validators

Functions:
    _fetch_phone_rows(): Every live contact on a number, primary or alternate, in owner order
    fetch_phone_owner(): Deterministic, verified-first resolution of the contact owning a mobile number
    fetch_phone_contact_ids(): Every live contact ID on a mobile number, in canonical owner order
    _parse_dateofbirth(): Convert DD/MM/YYYY string to Python date
    _upload_profile_image(): Upload base64 profile image to Azure Blob Storage
    _insert_userprofile_record(): Shared inner INSERT for etown_userprofile (used by registration + switch_profile)
    _insert_member_record(): Shared inner INSERT for etown_member
    _insert_provider_record(): Shared inner INSERT for etown_provider
    _build_registration_response(): Build the shared ContactInfo/UserprofileInfo response dict from DB rows
    register_user_in_db(): Full new-user registration (contact + userprofile + member/provider)
    update_unverified_user_in_db(): Update existing unverified contact and create missing profile records
    _fetch_unverified_onboarded(): True when a field-captured provider on the number is still awaiting approval
    STAFF_ROLE_FOS: The etown_staff_numbers role a field agent is rostered under
    has_etown_staff_role(): True when a number holds one named active etown_staff_numbers role
    resolve_capture_source(): The source a field capture is stamped with - the capturer's roster role, else 'FOS'
    _fetch_etown_staff_flags(): Etown_staff / Etown_FOS and unverified_onboarded_today for a number
    check_user_exists_in_db(): Existence check by mobile number and/or email
    update_last_triggered_time(): Background lasttriggeredtime + active-users update after /users/exists
    register_new_user(): Module-level convenience wrapper around UserService.register_new_user()
    update_last_login(): Update lastsignupstatus/membersignup on a userprofile after login
    reset_other_profiles_after_lastlogin(): Background reset of signup flags on a contact's other profiles
    promote_member_to_verified(): In-place unverified->verified promotion of a contact's member records
    fetch_active_users_by_date(): firstname->mobilenumber map of users active on a given date
    update_consent(): Update aisearch_consent/chattie_consent flags on a contact
    update_contact_address(): Update address/geo fields on a contact
    fetch_user_location_details(): Fetch all saved location records for a contact
    manage_user_location_details(): Add/edit/delete a user location record
    fetch_service_list(): Fetch a profile's service-list view (own-profile, by-mobile, or other-profile shape)
    fetch_provider_business_list(): Fetch the live businesses registered against a mobile number
    check_alternate_mobile_status(): Whether an alternate number is a separate account, another number's alternate, or free to use
    upsert_etown_staff_member(): Create or edit an etown_staff_numbers row found by mobile number
    fetch_etown_staff_members(): Fetch a mobile number's etown_staff_numbers rows
    _parse_birthdate_flexible(): Parse a birthdate string accepting DD/MM/YYYY or YYYY-MM-DD
    update_member_profile_in_db(): Update a contact and propagate name/email/mobile edits to related records

Dependencies:
    - app.db.client: Database connection pool access for EtownDB operations
    - app.schemas.api: Pydantic models for request/response validation
    - app.core.config: Application settings (Azure Blob container name)
    - app.core.logging: Centralized logging infrastructure
    - app.utils.blob_storage: Azure Blob Storage upload helpers
    - app.etown.user.users_syncing: Dataverse sync functions
    - app.etown.user.verification_promotion: promote_verified_number() after a claimed registration
    - app.etown.feed.feed_utils: propagate_display_name_to_feed() for profile-edit name changes
    - app.etown.invitations.invitation_utils / app.etown.notifications.*: deferred post-registration imports
    - app.etown.provider.provider_syncing: sync_provider_claim_rename_to_dataverse on claim
    - app.etown.provider.provider_review_utils: review_pending_exclusion SQL filter
    - app.etown.plans.plans_utils: contact plan columns inherited by new provider rows
    - time: High-resolution timing for DB acquire/query performance logging
    - uuid: Pre-generation of contact UUID for blob path alignment

Updates:
    09-Mar-2026: Initial creation with user registration and existence check operations
    11-Mar-2026: Optimized user existence lookup into one query; moved to the shared main DB pool.
    05-Jun-2026: fetch_active_users_by_date queries etown_activeusers joined with etown_contact.
    03-Jul-2026: Added _parse_birthdate_flexible() and update_member_profile_in_db() for /user/edit_profile.
    13-Jul-2026: Added _upload_profile_image(); registration stores the blob URL on the contact.
    14-Jul-2026: Claiming an unverified contact sets verified; verified-aware email check; added promote_member_to_verified().
    15-Jul-2026: check_user_exists_in_db() requires a role=2 member profile first.
    23-Jul-2026: register_new_user() schedules the external-invite relation insert after registration.
    29-Jul-2026: Added fetch_phone_owner()/fetch_phone_contact_ids() for verified-first number ownership.
    05-Aug-2026: Externally-invited members claimed on registration; member and userprofile inserts set verified inline.
    06-Aug-2026: Registration stores from_socialmedia and mirrors it as the ets_fromsocialmedia choice.
    12-Aug-2026: update_member_profile_in_db() returns the normalised column values in contact_payload.
    13-Aug-2026: Registration no longer treats a deleted account's number or email as taken.
    20-Aug-2026: Service list and user-exists carry Etown_providertype; provider profile names kept.
    24-Aug-2026: New provider rows inherit the contact's active subscription instead of defaulting to free.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    02-Sep-2026: Service list hides disabled providers; added fetch_provider_business_list().
    16-Sep-2026: /users/exists reports unverified_onboarded and Etown_staff.
    18-Sep-2026: Phone ownership also covers a contact's alternatemobile, ranked behind primary matches.
    21-Sep-2026: fetch_service_list() hides providers with an unapproved review row; unverified_onboarded ignores parked captures.
    22-Sep-2026: Reverted the 21-Sep review exclusion in _fetch_unverified_onboarded().
    23-Sep-2026: Etown_staff from etown_staff_numbers; added Etown_FOS, staff-role and alternate-mobile checks.
    24-Sep-2026: Service list drops deleted providers; unverified-onboarded skips unapproved reviews.
    25-Sep-2026: Added upsert_etown_staff_member() and fetch_etown_staff_members(); service list hides only pending 'create' reviews.
    01-Oct-2026: /users/exists reports unverified_onboarded_today for staff, CSR and FOS numbers.
    14-Oct-2026: check_user_exists_in_db() reports etown_contactdevices credsupdate for an optional devicetoken.
"""

import asyncio
import re
import uuid as _uuid_module
import asyncpg
from typing import Any, Dict, List, Optional
import time
import datetime
from fastapi import HTTPException

from app.db.client import Database, with_retry
from app.core.config import settings
from app.utils.blob_storage import (
    upload_blob_bytes,
    to_bytes,
    current_timestamp_prefix,
    detect_mime_type,
    normalize_container_name,
)
from app.etown.user.users_syncing import (
    sync_activeuserslog_to_dataverse,
    sync_contact_lasttriggeredtime_to_dataverse,
    sync_registration_to_dataverse,
    sync_userprofile_lastlogin_to_dataverse,
    to_fromsocialmedia_choice,
)
from app.etown.user.verification_promotion import promote_verified_number
from app.etown.provider.provider_syncing import sync_provider_claim_rename_to_dataverse
from app.etown.feed.feed_utils import propagate_display_name_to_feed
from app.etown.provider.provider_review_utils import review_pending_exclusion
from app.etown.plans.plans_utils import (
    CONTACT_PLAN_COLUMNS,
    CONTACT_PLAN_CTE,
    CONTACT_PLAN_VALUES,
)
from app.schemas.api import (
    UserRegistrationRequest,
    UserRegistrationResponse,
    UserExistsDetailedResponse
)
from app.core.logging import logger


_EXCLUDE_REVIEW_PENDING_P = "AND " + review_pending_exclusion("p.providerid", create_only=True)

_EXCLUDE_DELETED_P = "AND lower(COALESCE(p.deleted, 'false')) <> 'true'"


_PHONE_OWNER_ORDER_BY = (
    "ORDER BY match_rank ASC, verified DESC NULLS LAST, createdon ASC NULLS LAST, contactid ASC"
)


async def _fetch_phone_rows(mobilenumber: str, con: Optional[Any]) -> List[Any]:
    """Fetch live contacts holding a number as primary or alternate mobile, primaries first, then verified-first, oldest-first.

    Args:
        mobilenumber: Mobile number matched against mobilephone and alternatemobile.
        con: Connection to reuse inside a transaction, or None to use the pool.

    Returns:
        Rows of (contactid, verified, createdon, match_rank).

    Raises:
        RuntimeError: If no connection is given and the pool is not initialized.
    """
    sql = (
        "SELECT contactid, verified, createdon, 0 AS match_rank FROM etown_contact "
        "WHERE mobilephone = $1 AND deleted IS DISTINCT FROM 'true' "
        "UNION ALL "
        "SELECT contactid, verified, createdon, 1 AS match_rank FROM etown_contact "
        "WHERE alternatemobile = $1 "
        "AND mobilephone IS DISTINCT FROM $1 AND deleted IS DISTINCT FROM 'true' "
        f"{_PHONE_OWNER_ORDER_BY}"
    )
    if con is not None:
        return await con.fetch(sql, mobilenumber)

    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")
    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as pool_con:
        return await pool_con.fetch(sql, mobilenumber)


async def fetch_phone_owner(mobilenumber: str, con: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Return the contactid and verified flag of the live contact that owns a mobile number, or None."""
    rows = await _fetch_phone_rows(mobilenumber, con)
    if not rows:
        return None
    return {"contactid": str(rows[0]["contactid"]), "verified": bool(rows[0]["verified"])}


async def fetch_phone_contact_ids(mobilenumber: str, con: Optional[Any] = None) -> List[str]:
    """Return every live contact id on a mobile number, in canonical owner order."""
    return [str(row["contactid"]) for row in await _fetch_phone_rows(mobilenumber, con)]


def _parse_dateofbirth(dob_str: Optional[str]) -> Optional[datetime.date]:
    """Parse a DD/MM/YYYY string into a date, or return None when empty or invalid."""
    if not dob_str:
        return None
    try:
        return datetime.datetime.strptime(dob_str, "%d/%m/%Y").date()
    except ValueError:
        logger.warning("[register] Invalid dateofbirth format | value=%s", dob_str)
        return None


async def _upload_profile_image(contact_id: str, base64_data: str) -> str:
    """Upload a base64 profile image under the contact's blob prefix and return its path, or "" on failure."""
    try:
        raw = to_bytes(base64_data)
        if not raw:
            return ""
        mime = detect_mime_type(raw)
        ext = "jpg" if "jpeg" in mime else mime.split("/")[-1]
        container = normalize_container_name(settings.AZURE_BLOB_CONTAINER or "etowndev")
        blob_name = f"{contact_id}/{current_timestamp_prefix()}_ProfileImg.{ext}"
        return await upload_blob_bytes(container, blob_name, raw, mime)
    except Exception as exc:
        logger.warning("[register] Profile image upload failed | contact_id=%s | error=%s", contact_id, exc)
        return ""


async def _insert_userprofile_record(
    con: Any,
    contact_id: str,
    firstname: str,
    role: int,
    mobilenumber: Optional[str],
    email: Optional[str],
    membersignup: Optional[str] = None,
    lastsignupstatus: Optional[int] = None,
    guard_duplicate: bool = False,
) -> Optional[Any]:
    """Insert a verified etown_userprofile row on an open transaction and return it.

    Args:
        con: Connection already inside a transaction.
        contact_id: Parent contact id.
        firstname: Written to contactidname and name.
        role: 1 for provider, 2 for member.
        mobilenumber: Mobile number.
        email: Email address, if any.
        membersignup: Signup status string, if any.
        lastsignupstatus: Signup status integer, if any.
        guard_duplicate: Skip the insert when the contact already has a profile for this role.

    Returns:
        The inserted row, or None when guard_duplicate skipped the insert.
    """
    rolename = "Provider" if role == 1 else "member"
    if guard_duplicate:
        sql = """
            INSERT INTO etown_userprofile (
                userprofileid, contactid, contactidname, name,
                role, rolename, email, mobilenumber,
                membersignup, lastsignupstatus, createdon, verified
            )
            SELECT gen_random_uuid(), $1, $2, $2,
                   $3, $4, $5, $6,
                   $7, $8, NOW(), TRUE
            WHERE NOT EXISTS (
                SELECT 1 FROM etown_userprofile WHERE contactid = $1 AND role = $3
            )
            RETURNING userprofileid, contactid, role, rolename, email, mobilenumber,
                      membersignup, lastsignupstatus
        """
    else:
        sql = """
            INSERT INTO etown_userprofile (
                userprofileid, contactid, contactidname, name,
                role, rolename, email, mobilenumber,
                membersignup, lastsignupstatus, createdon, verified
            ) VALUES (
                gen_random_uuid(), $1, $2, $2,
                $3, $4, $5, $6,
                $7, $8, NOW(), TRUE
            )
            RETURNING userprofileid, contactid, role, rolename, email, mobilenumber,
                      membersignup, lastsignupstatus
        """
    return await con.fetchrow(
        sql, contact_id, firstname, role, rolename, email, mobilenumber, membersignup, lastsignupstatus
    )


async def _insert_member_record(
    con: Any,
    userprofile_id: str,
    contact_id: str,
    firstname: str,
    mobilenumber: Optional[str],
    email: Optional[str],
) -> Any:
    """Insert an etown_member row seeded with the contact's best active plan and return its ids.

    Args:
        con: Connection already inside a transaction.
        userprofile_id: Parent userprofile id.
        contact_id: Parent contact id.
        firstname: Written to the name columns.
        mobilenumber: Mobile number.
        email: Email address, if any.

    Returns:
        Row with memberid, userprofileid and contactid.
    """
    return await con.fetchrow(
        f"""
        WITH {CONTACT_PLAN_CTE.format(contact='$3')}
        INSERT INTO etown_member (
            memberid, userprofileid, userprofileidname,
            contactid, contactidname, dateofcreation,
            mobilenumber, email, role, rolename, verified,
            {CONTACT_PLAN_COLUMNS}
        ) VALUES (
            gen_random_uuid(), $1, $2,
            $3, $2, NOW(),
            $4, $5, 2, 'member', TRUE,
            {CONTACT_PLAN_VALUES}
        )
        RETURNING memberid, userprofileid, contactid
        """,
        userprofile_id, firstname, contact_id, mobilenumber, email,
    )


async def _insert_provider_record(
    con: Any,
    userprofile_id: str,
    contact_id: str,
    firstname: str,
    mobilenumber: Optional[str],
    email: Optional[str],
    businessname: Optional[str] = None,
    description: Optional[str] = None,
) -> Any:
    """Insert a verified etown_provider row seeded with the contact's best active plan and return its ids.

    Args:
        con: Connection already inside a transaction.
        userprofile_id: Parent userprofile id.
        contact_id: Parent contact id.
        firstname: Written to the name and firstname columns.
        mobilenumber: Mobile number.
        email: Email address, if any.
        businessname: Business name, if any.
        description: Business description, if any.

    Returns:
        Row with providerid, userprofileid and contactid.
    """
    return await con.fetchrow(
        f"""
        WITH {CONTACT_PLAN_CTE.format(contact='$1')}
        INSERT INTO etown_provider (
            providerid, contactid, userprofileid,
            contactidname, userprofileidname, firstname,
            email, mobilenumber, role, rolename, creationdate,
            businessname, description,
            providertype, publicsearchprovider,
            {CONTACT_PLAN_COLUMNS}
        ) VALUES (
            gen_random_uuid(), $1, $2,
            $3, $3, $3,
            $4, $5, 1, 'Provider', NOW(),
            $6, $7,
            'etown_verified', 'verified',
            {CONTACT_PLAN_VALUES}
        )
        RETURNING providerid, userprofileid, contactid
        """,
        contact_id, userprofile_id, firstname, email, mobilenumber, businessname, description,
    )


def _build_registration_response(contact_row: Any, up_row: Any, contact_id: str, firstname: str) -> dict:
    """Build the ContactInfo and UserprofileInfo registration response from contact and userprofile rows."""
    return {
        "ContactInfo": [{
            "mobilephone": contact_row["mobilephone"],
            "ets_mobilenumberaccesstoken": contact_row["accesstoken"],
            "fullname": contact_row["firstname"],
            "contactid": contact_id,
            "createdon": contact_row["createdon"].isoformat() + "Z",
            "ets_userlogin": contact_row["userlogin"],
            "firstname": contact_row["firstname"],
            "emailaddress1": contact_row.get("email"),
            "address1_city": contact_row.get("city"),
            "ets_statecode": contact_row.get("statecode"),
            "address1_latitude": contact_row.get("latitude"),
            "address1_longitude": contact_row.get("longitude"),
            "ets_profileimage": contact_row.get("profileimage"),
            "ets_is_active": "true",
            "ets_verified": str(contact_row.get("verified", True)).lower(),
        }],
        "UserprofileInfo": [{
            "ets_contact_value": contact_id,
            "ets_role": up_row["role"],
            "ets_email": up_row.get("email"),
            "ets_mobilenumber": up_row.get("mobilenumber"),
            "ets_membersignup": up_row.get("membersignup"),
            "ets_lastsignupstatus": up_row.get("lastsignupstatus"),
            "ets_userprofileid": str(up_row["userprofileid"]),
            "contact.firstname": firstname,
        }],
    }


@with_retry()
async def register_user_in_db(
    user_data: UserRegistrationRequest,
    profile_image_url: Optional[str],
    contact_id: str,
) -> dict:
    """Insert a contact, userprofile and member or provider row in one transaction and return the registration response.

    Args:
        user_data: Validated registration request.
        profile_image_url: Uploaded profile image path, or None.
        contact_id: Pre-generated id for the new contact.

    Returns:
        ContactInfo and UserprofileInfo plus _contact_id, _userprofile_id and _detail_id keys.

    Raises:
        ValueError: "mobile_exists" or "email_exists" when a live contact already holds them.
        RuntimeError: If the database pool is not initialized.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")

    mobilenumber_str = str(user_data.mobilenumber)
    dateofbirth = _parse_dateofbirth(user_data.dateofbirth)
    rolecodename = "provider" if user_data.role == 1 else "member"

    acquire_start = time.perf_counter()
    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        acquire_ms = (time.perf_counter() - acquire_start) * 1000
        logger.info("[users_register] EtownDB acquire_time_ms=%.2f", acquire_ms)
        async with con.transaction():
            try:
                if await con.fetchval(
                    "SELECT 1 FROM etown_contact WHERE mobilephone = $1 "
                    "AND deleted IS DISTINCT FROM 'true' LIMIT 1",
                    mobilenumber_str,
                ):
                    raise ValueError("mobile_exists")

                if user_data.email and await con.fetchval(
                    "SELECT 1 FROM etown_contact WHERE email = $1 AND verified = true "
                    "AND deleted IS DISTINCT FROM 'true' LIMIT 1",
                    user_data.email,
                ):
                    raise ValueError("email_exists")

                contact_row = await con.fetchrow(
                    """
                    INSERT INTO etown_contact (
                        contactid, firstname, mobilephone, email, userlogin,
                        createdon, accesstoken, rolecode, rolecodename,
                        city, statecode, latitude, longitude, address,
                        additionaladdressinfo, connectycubeid, userregistrationtype,
                        invitationcode, profileimage, birthdate, from_socialmedia,
                        pushnotificationcheck, verified, activestate
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        NOW(), $6, $7, $8,
                        $9, $10, $11, $12, $13,
                        $14, $15, $16,
                        $17, $18, $19, $20,
                        true, true, true
                    )
                    RETURNING contactid, firstname, mobilephone, email, accesstoken,
                              createdon, userlogin, city, statecode, latitude,
                              longitude, address, profileimage, verified
                    """,
                    contact_id,
                    user_data.firstname,
                    mobilenumber_str,
                    user_data.email,
                    user_data.user_login,
                    user_data.MobilenumberAccesstoken,
                    user_data.role,
                    rolecodename,
                    user_data.City,
                    user_data.state_code,
                    user_data.latitude,
                    user_data.longitude,
                    user_data.address,
                    user_data.additionaladdressinfo,
                    user_data.Connectycube_id,
                    user_data.userregistration_type,
                    user_data.invite_code,
                    profile_image_url or None,
                    dateofbirth,
                    user_data.from_socialmedia,
                )

                stored_contact_id = str(contact_row["contactid"])

                up_row = await _insert_userprofile_record(
                    con, stored_contact_id, user_data.firstname, user_data.role,
                    mobilenumber_str, user_data.email,
                    user_data.membersignup_status, user_data.lastsignup_status,
                    guard_duplicate=False,
                )
                userprofile_id = str(up_row["userprofileid"])

                if user_data.role == 1:
                    detail_row = await _insert_provider_record(
                        con, userprofile_id, stored_contact_id, user_data.firstname,
                        mobilenumber_str, user_data.email,
                        user_data.companyname, user_data.companydescription,
                    )
                    detail_id = str(detail_row["providerid"])
                else:
                    detail_row = await _insert_member_record(
                        con, userprofile_id, stored_contact_id, user_data.firstname,
                        mobilenumber_str, user_data.email,
                    )
                    detail_id = str(detail_row["memberid"])

                query_ms = (time.perf_counter() - acquire_start) * 1000
                logger.info("[users_register] EtownDB total_time_ms=%.2f", query_ms)

                result = _build_registration_response(contact_row, up_row, stored_contact_id, user_data.firstname)
                result["_contact_id"] = stored_contact_id
                result["_userprofile_id"] = userprofile_id
                result["_detail_id"] = detail_id
                return result

            except Exception as e:
                logger.error("User registration transaction failed: %s", e, exc_info=True)
                raise


async def update_unverified_user_in_db(
    user_data: UserRegistrationRequest,
    profile_image_url: Optional[str],
) -> dict:
    """Claim an unverified contact: verify it, rename its linked rows, and create or update the role's profile.

    Args:
        user_data: Validated registration request.
        profile_image_url: Uploaded profile image path, or None.

    Returns:
        Registration response plus _contact_id, _userprofile_id, _detail_id, _claimed and renamed-id keys.

    Raises:
        ValueError: "already_verified" when the number is unknown or its owner is already verified.
        RuntimeError: If the database pool is not initialized.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")

    mobilenumber_str = str(user_data.mobilenumber)
    dateofbirth = _parse_dateofbirth(user_data.dateofbirth)

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            contact_check = await fetch_phone_owner(mobilenumber_str, con)
            if not contact_check or contact_check["verified"]:
                raise ValueError("already_verified")

            contact_id = contact_check["contactid"]

            set_parts: List[str] = []
            params: List[Any] = []

            def _add(col: str, val: Any) -> None:
                params.append(val)
                set_parts.append(f"{col} = ${len(params)}")

            _add("userlogin", user_data.user_login)
            _add("accesstoken", user_data.MobilenumberAccesstoken)
            _add("rolecode", user_data.role)
            _add("rolecodename", "provider" if user_data.role == 1 else "member")
            _add("firstname", user_data.firstname)
            _add("from_socialmedia", user_data.from_socialmedia)
            if user_data.email is not None:
                _add("email", user_data.email)
            if user_data.City is not None:
                _add("city", user_data.City)
            if user_data.state_code is not None:
                _add("statecode", user_data.state_code)
            if user_data.latitude is not None:
                _add("latitude", user_data.latitude)
            if user_data.longitude is not None:
                _add("longitude", user_data.longitude)
            if user_data.address is not None:
                _add("address", user_data.address)
            if user_data.additionaladdressinfo is not None:
                _add("additionaladdressinfo", user_data.additionaladdressinfo)
            if user_data.Connectycube_id is not None:
                _add("connectycubeid", user_data.Connectycube_id)
            if user_data.userregistration_type is not None:
                _add("userregistrationtype", user_data.userregistration_type)
            if user_data.invite_code is not None:
                _add("invitationcode", user_data.invite_code)
            if dateofbirth is not None:
                _add("birthdate", dateofbirth)
            if profile_image_url:
                _add("profileimage", profile_image_url)

            set_parts.append("verified = true")
            set_parts.append("modifieddate = NOW()")
            params.append(contact_id)
            await con.execute(
                f"UPDATE etown_contact SET {', '.join(set_parts)} WHERE contactid = ${len(params)}",
                *params,
            )

            renamed = await con.fetchrow(
                """
                WITH upd_member AS (
                    UPDATE etown_member
                    SET contactidname = $2,
                        userprofileidname = $2,
                        name = $2,
                        mobilenumber = COALESCE(mobilenumber, $3),
                        email = COALESCE($4, email),
                        modifieddate = NOW()
                    WHERE contactid = $1
                ),
                upd_member_profile AS (
                    UPDATE etown_userprofile
                    SET contactidname = $2,
                        name = $2,
                        mobilenumber = COALESCE(mobilenumber, $3),
                        email = COALESCE($4, email),
                        modifieddate = NOW()
                    WHERE contactid = $1 AND role = 2
                ),
                upd_provider_profile AS (
                    UPDATE etown_userprofile
                    SET contactidname = $2, name = $2, modifieddate = NOW()
                    WHERE contactid = $1 AND role = 1
                    RETURNING userprofileid
                ),
                upd_provider AS (
                    UPDATE etown_provider
                    SET contactidname = $2, userprofileidname = $2, firstname = $2,
                        modifieddate = NOW()
                    WHERE contactid = $1
                    RETURNING providerid
                ),
                upd_services AS (
                    UPDATE etown_providerservices
                    SET contactidname = $2, userprofileidname = $2, modifieddate = NOW()
                    WHERE contactid = $1
                ),
                upd_services_copy AS (
                    UPDATE etown_providerservices_copy
                    SET contactidname = $2, userprofileidname = $2, modifieddate = NOW()
                    WHERE contactid = $1
                )
                SELECT
                    ARRAY(SELECT userprofileid FROM upd_provider_profile) AS provider_userprofile_ids,
                    ARRAY(SELECT providerid FROM upd_provider)            AS provider_ids
                """,
                contact_id, user_data.firstname, mobilenumber_str, user_data.email,
            )

            up_row = await _insert_userprofile_record(
                con, contact_id, user_data.firstname, user_data.role,
                mobilenumber_str, user_data.email,
                user_data.membersignup_status, user_data.lastsignup_status,
                guard_duplicate=True,
            )

            detail_id: Optional[str] = None
            if up_row:
                userprofile_id = str(up_row["userprofileid"])
                if user_data.role == 1:
                    detail_row = await _insert_provider_record(
                        con, userprofile_id, contact_id, user_data.firstname,
                        mobilenumber_str, user_data.email,
                        user_data.companyname, user_data.companydescription,
                    )
                    detail_id = str(detail_row["providerid"])
                else:
                    detail_row = await _insert_member_record(
                        con, userprofile_id, contact_id, user_data.firstname,
                        mobilenumber_str, user_data.email,
                    )
                    detail_id = str(detail_row["memberid"])
            else:
                up_row = await con.fetchrow(
                    """
                    UPDATE etown_userprofile
                    SET membersignup      = $3,
                        lastsignupstatus  = $4,
                        modifieddate      = NOW()
                    WHERE userprofileid = (
                        SELECT userprofileid FROM etown_userprofile
                        WHERE contactid = $1 AND role = $2
                        ORDER BY createdon ASC NULLS LAST, userprofileid
                        LIMIT 1
                    )
                    RETURNING userprofileid, role, email, mobilenumber, membersignup, lastsignupstatus
                    """,
                    contact_id, user_data.role,
                    user_data.membersignup_status, user_data.lastsignup_status,
                )
                userprofile_id = str(up_row["userprofileid"]) if up_row else ""

                if userprofile_id:
                    if user_data.role == 1:
                        detail_sql = """
                            SELECT providerid FROM etown_provider
                            WHERE contactid = $1 AND userprofileid = $2
                            ORDER BY creationdate DESC NULLS LAST
                            LIMIT 1
                        """
                    else:
                        detail_sql = """
                            SELECT memberid FROM etown_member
                            WHERE contactid = $1 AND userprofileid = $2
                            ORDER BY dateofcreation DESC NULLS LAST
                            LIMIT 1
                        """
                    existing_detail = await con.fetchval(detail_sql, contact_id, userprofile_id)
                    detail_id = str(existing_detail) if existing_detail else None

            contact_row = await con.fetchrow(
                """
                SELECT contactid, firstname, mobilephone, email, accesstoken,
                       createdon, userlogin, city, statecode, latitude,
                       longitude, address, profileimage, verified
                FROM etown_contact WHERE contactid = $1
                """,
                contact_id,
            )

            result = _build_registration_response(contact_row, up_row, contact_id, user_data.firstname)
            result["_contact_id"] = contact_id
            result["_userprofile_id"] = userprofile_id
            result["_detail_id"] = detail_id
            result["_claimed"] = True
            result["_renamed_provider_userprofile_ids"] = [
                str(x) for x in (renamed["provider_userprofile_ids"] if renamed else [])
            ]
            result["_renamed_provider_ids"] = [
                str(x) for x in (renamed["provider_ids"] if renamed else [])
            ]
            return result

STAFF_ROLE_FOS = "fos"


async def has_etown_staff_role(
    mobilenumber: Optional[str],
    role: str,
    con: Optional[Any] = None,
) -> bool:
    """Return True when the number's last ten digits hold an active etown_staff_numbers row for the role.

    Args:
        mobilenumber: Number in any format; only its last ten digits are used.
        role: Role to test, matched case-insensitively ('staff', 'csr', 'fos').
        con: Connection to reuse inside a transaction, or None to use the pool.

    Returns:
        True for an active match; False for a blank number, no match or a failed lookup.
    """
    digits = re.sub(r"\D", "", str(mobilenumber or ""))[-10:]
    wanted = str(role or "").strip().lower()
    if not digits or not wanted:
        return False

    sql = (
        "SELECT 1 FROM etown_staff_numbers "
        "WHERE mobilenumber = $1 AND role = $2 AND is_active LIMIT 1"
    )
    try:
        if con is not None:
            return await con.fetchval(sql, digits, wanted) is not None
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as pool_con:
            return await pool_con.fetchval(sql, digits, wanted) is not None
    except Exception as e:
        logger.warning(
            f"etown_staff_numbers lookup failed; reporting role {wanted!r} not held: {e}"
        )
        return False


CAPTURE_SOURCE_DEFAULT = "FOS"


async def resolve_capture_source(
    mobilenumber: Optional[str],
    con: Optional[Any] = None,
) -> str:
    """Return the capturer's earliest active roster role uppercased, or 'FOS' for a blank, unknown or failed lookup.

    Args:
        mobilenumber: Capturing agent's number; only its last ten digits are used.
        con: Connection to reuse inside a transaction, or None to use the pool.

    Returns:
        Uppercased role such as 'FOS', 'STAFF' or 'CSR'.
    """
    digits = re.sub(r"\D", "", str(mobilenumber or ""))[-10:]
    if not digits:
        return CAPTURE_SOURCE_DEFAULT

    sql = (
        "SELECT role FROM etown_staff_numbers "
        "WHERE mobilenumber = $1 AND is_active "
        "ORDER BY createdon ASC, role ASC LIMIT 1"
    )
    try:
        if con is not None:
            role = await con.fetchval(sql, digits)
        else:
            if Database._pool is None or Database._pool._closed:
                raise RuntimeError("Database pool is not initialized or is closed.")
            async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as pool_con:
                role = await pool_con.fetchval(sql, digits)
    except Exception as e:
        logger.warning(
            f"etown_staff_numbers lookup failed; stamping source {CAPTURE_SOURCE_DEFAULT!r}: {e}"
        )
        return CAPTURE_SOURCE_DEFAULT
    return str(role).strip().upper() if role else CAPTURE_SOURCE_DEFAULT


async def _fetch_etown_staff_flags(con, mobilenumber: Optional[str]) -> Dict[str, Any]:
    """Return Etown_staff, Etown_FOS and, for a rostered number, its unverified providers captured today (IST).

    Args:
        con: Open connection to run the lookup on.
        mobilenumber: Number in any format; only its last ten digits are used.

    Returns:
        {"Etown_staff": bool, "Etown_FOS": bool, "unverified_onboarded_today": int}, False/0 when a lookup fails.
    """
    digits = re.sub(r"\D", "", str(mobilenumber or ""))[-10:]
    roles: set = set()
    if digits:
        try:
            rows = await con.fetch(
                """
                SELECT DISTINCT role
                  FROM etown_staff_numbers
                 WHERE mobilenumber = $1
                   AND is_active
                """,
                digits,
            )
            roles = {row["role"] for row in rows}
        except Exception as e:
            logger.warning(f"etown_staff_numbers lookup failed; reporting not staff/FOS: {e}")

    onboarded_today = 0
    if roles & {"staff", "csr", "fos"}:
        try:
            onboarded_today = await con.fetchval(
                """
                SELECT count(*)
                  FROM etown_provider p
                 WHERE p.marketing_mobilenumber = $1
                   AND upper(COALESCE(p.source, '')) IN ('FOS', 'CSR', 'STAFF')
                   AND lower(COALESCE(p.deleted, 'false')) <> 'true'
                   AND p.creationdate >= (date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata')
                                          AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'UTC'
                """,
                digits,
            )
        except Exception as e:
            logger.warning(f"Today's unverified onboarded count failed for *****{digits[-4:]}; reporting 0: {e}")
    return {
        "Etown_staff": bool(roles & {"staff", "csr"}),
        "Etown_FOS": "fos" in roles,
        "unverified_onboarded_today": int(onboarded_today or 0),
    }


@with_retry()
async def _fetch_unverified_onboarded(
    con,
    mobilenumber: Optional[str] = None,
    contactid: Optional[str] = None,
) -> bool:
    """Return True when a live FOS, CSR or STAFF provider on this number or contact is unapproved and not review-pending.

    Args:
        con: Open connection to run the lookup on.
        mobilenumber: Number in any format; matched on its last ten digits.
        contactid: Contact resolved by the existence check, if any.

    Returns:
        True when such a provider exists; False otherwise or with no usable number or contact.
    """
    digits = re.sub(r"\D+", "", mobilenumber or "")
    national = digits[-10:] if len(digits) >= 10 else None
    if not national and not contactid:
        return False

    return bool(await con.fetchval(
        f"""
        SELECT EXISTS (
            SELECT 1
              FROM etown_provider p
             WHERE lower(COALESCE(p.deleted, 'false')) <> 'true'
               AND upper(COALESCE(p.source, '')) IN ('FOS', 'CSR', 'STAFF')
               AND p.approve IS NOT TRUE
               AND {review_pending_exclusion('p.providerid')}
               AND (
                   ($1::text IS NOT NULL
                    AND RIGHT(regexp_replace(COALESCE(p.mobilenumber, ''), '[^0-9]', '', 'g'), 10) = $1)
                OR ($2::text IS NOT NULL AND p.contactid = $2)
               )
        )
        """,
        national, contactid,
    ))


async def check_user_exists_in_db(
    mobilenumber: Optional[str] = None,
    email: Optional[str] = None,
    devicetoken: Optional[str] = None,
) -> dict:
    """Look up a live member or verified contact by mobile or email and return its contact, profiles, consent and staff flags.

    Args:
        mobilenumber: Mobile number matched against profile, primary and alternate mobile.
        email: Email matched against profile and contact email.
        devicetoken: Push token whose etown_contactdevices credsupdate is reported for the matched contact.

    Returns:
        Existence payload with ContactInfo, UserprofileInfo, unverified_onboarded, staff flags and credsupdate.

    Raises:
        RuntimeError: If the database pool is not initialized.
        ValueError: If neither mobilenumber nor email is given.
    """
    Database.assert_available()
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        try:
            if not mobilenumber and not email:
                raise ValueError("At least one search parameter (mobile or email) must be provided")
            
            conditions = []
            params = []
            param_count = 0
            alternate_rank = "0"

            if mobilenumber:
                param_count += 1
                conditions.append(
                    f"(u.mobilenumber = ${param_count} OR c.mobilephone = ${param_count} "
                    f"OR c.alternatemobile = ${param_count})"
                )
                alternate_rank = (
                    f"CASE WHEN u.mobilenumber IS DISTINCT FROM ${param_count} "
                    f"AND c.mobilephone IS DISTINCT FROM ${param_count} "
                    f"AND c.alternatemobile = ${param_count} THEN 1 ELSE 0 END"
                )
                params.append(mobilenumber)

            if email:
                param_count += 1
                conditions.append(f"(u.email = ${param_count} OR c.email = ${param_count})")
                params.append(email)

            member_query = f"""
                SELECT c.contactid
                FROM etown_userprofile u
                JOIN etown_contact c ON c.contactid = u.contactid
                WHERE ((u.role = 2 AND c.verified IS NOT FALSE) OR c.verified IS TRUE)
                  AND c.deleted IS DISTINCT FROM 'true'
                  AND ({' OR '.join(conditions)})
                ORDER BY {alternate_rank} ASC, c.verified DESC NULLS LAST, (u.role = 2) DESC,
                         c.createdon DESC NULLS LAST, c.contactid
                LIMIT 1
            """
            member_row = await con.fetchrow(member_query, *params)
            unverified_onboarded = await _fetch_unverified_onboarded(
                con,
                mobilenumber=mobilenumber,
                contactid=str(member_row['contactid']) if member_row else None,
            )
            if not member_row:
                logger.info(
                    f"No live contact-linked member profile (role=2) or verified contact found "
                    f"for mobile={mobilenumber}, email={email} — user not found "
                    f"(deleted contacts are excluded)"
                )
                return {
                    "ContactInfo": [],
                    "UserprofileInfo": [],
                    "MyTownInfo": "true",
                    "Userverified": None,
                    "unverified_onboarded": unverified_onboarded,
                    **await _fetch_etown_staff_flags(con, mobilenumber),
                }

            contact_query = """
                SELECT
                    c.contactid,
                    c.mobilephone,
                    c.alternatemobile,
                    c.firstname,
                    c.createdon,
                    c.additionaladdressinfo,
                    c.pushnotificationcheck,
                    c.latitude,
                    c.city,
                    c.accesstoken,
                    c.addresscomp,
                    c.longitude,
                    c.userlogin,
                    c.statecode,
                    c.email,
                    c.activestate,
                    c.verified,
                    c.aisearch_consent,
                    c.chattie_consent,
                    c.birthdate,
                    c.profileimage,

                    CASE
                        WHEN c.deactivated = 'true'
                         AND c.reactivation IS DISTINCT FROM 'true'
                        THEN 'true'
                        ELSE 'false'
                    END AS deactivated
                FROM etown_contact c
                WHERE c.contactid = $1
"""
            contact_result = await con.fetchrow(contact_query, member_row['contactid'])
            if contact_result:
                userprofile_query = """
                    SELECT
                        u.userprofileid,
                        u.role,
                        u.rolename,
                        u.mobilenumber,
                        u.membersignup,
                        u.lastsignupstatus,
                        u.lastsignupstatusname,
                        u.contactid,
                        u.verified,
                        (
                            SELECT p.providertype
                            FROM etown_provider p
                            WHERE p.userprofileid = u.userprofileid
                            ORDER BY (p.providertype IS NULL), p.providerid
                            LIMIT 1
                        ) AS providertype
                    FROM etown_userprofile u
                    WHERE u.contactid = $1
                    ORDER BY u.role, u.userprofileid
                """
                userprofile_results = await con.fetch(userprofile_query, contact_result['contactid'])
                logger.info(f"Found {len(userprofile_results)} profile(s) for contact {contact_result['contactid']}")

                birthdate_str = None
                if contact_result.get('birthdate'):
                    birthdate = contact_result.get('birthdate')
                    if hasattr(birthdate, 'strftime'):
                        birthdate_str = birthdate.strftime('%d/%m/%Y')
                    else:
                        parsed_date = datetime.datetime.fromisoformat(str(birthdate).split('T')[0])
                        birthdate_str = parsed_date.strftime('%d/%m/%Y')
                
                contact_info = {
                    "ets_additionaladdressinfo": contact_result.get('additionaladdressinfo'),
                    "mobilephone": contact_result.get('mobilephone'),
                    "ets_alternatemobile": contact_result.get('alternatemobile'),
                    "ets_pushnotificationcheck": contact_result.get('pushnotificationcheck'),
                    "ets_is_active": contact_result.get('activestate'),
                    "address1_latitude": contact_result.get('latitude'),
                    "address1_city": contact_result.get('city'),
                    "ets_mobilenumberaccesstoken": contact_result.get('accesstoken'),
                    "address1_composite": contact_result.get('addresscomp'),
                    "fullname": contact_result.get('firstname'),
                    "email": contact_result.get('email'),
                    "contactid": contact_result.get('contactid'),
                    "address1_longitude": contact_result.get('longitude'),
                    "createdon": contact_result.get('createdon').isoformat() + "Z" if contact_result.get('createdon') else None,
                    "ets_userlogin": contact_result.get('userlogin'),
                    "firstname": contact_result.get('firstname'),
                    "ets_statecode": contact_result.get('statecode'),
                    "ets_verified": str(contact_result.get('verified')) if contact_result.get('verified') is not None else None,
                    "dateofbirth": birthdate_str,
                    "ets_profileimage": contact_result.get('profileimage'),
                    "ets_deactivated": contact_result.get('deactivated'),
                }

                userprofile_info = []
                for profile in userprofile_results:
                    profile_data = {
                        "_ets_contact_value": profile.get('contactid'),
                        "ets_role": profile.get('role'),
                        "ets_rolename": profile.get('rolename'),
                        "ets_mobilenumber": profile.get('mobilenumber'),
                        "ets_membersignup": profile.get('membersignup'),
                        "ets_lastsignupstatus": profile.get('lastsignupstatus'),
                        "ets_lastsignupstatusname": profile.get('lastsignupstatusname'),
                        "ets_userprofileid": profile.get('userprofileid'),
                        "ets_verified": str(profile.get('verified')) if profile.get('verified') is not None else None,
                        "contact.firstname": contact_result.get('firstname'),
                        "Etown_providertype": profile.get('providertype'),
                    }
                    userprofile_info.append(profile_data)
                    logger.debug(f"Added profile: role={profile.get('role')}, userprofileid={profile.get('userprofileid')}")
                
                logger.info(
                    f"Returning {len(userprofile_info)} profile(s) in UserprofileInfo array "
                    f"(deactivated={contact_result.get('deactivated')})"
                )

                credsupdate = None
                token = (devicetoken or "").strip()
                if token:
                    try:
                        credsupdate = await con.fetchval(
                            """
                            SELECT bool_and(d.credsupdate)
                              FROM etown_contactdevices d
                             WHERE (d.device_token = $2 OR d.deviceguid = $2)
                               AND (d.contactid = $1 OR d.userprofileid = ANY($3::text[]))
                            """,
                            str(contact_result['contactid']),
                            token,
                            [str(p['userprofileid']) for p in userprofile_results],
                        )
                    except Exception as e:
                        logger.error(
                            f"credsupdate lookup failed for contact {contact_result['contactid']}; reporting null: {e}",
                            exc_info=True,
                        )

                return {
                    "ContactInfo": [contact_info],
                    "UserprofileInfo": userprofile_info,
                    "MyTownInfo": "true",
                    "Userverified": contact_result.get('verified'),
                    "AisearchConsent": contact_result.get('aisearch_consent'),
                    "ChattieConsent": contact_result.get('chattie_consent'),
                    "Deactivated": contact_result.get('deactivated'),
                    "unverified_onboarded": unverified_onboarded,
                    "credsupdate": credsupdate,
                    **await _fetch_etown_staff_flags(
                        con, mobilenumber or contact_result.get('mobilephone')
                    ),
                }
            else:
                return {
                    "ContactInfo": [],
                    "UserprofileInfo": [],
                    "MyTownInfo": "true",
                    "Userverified": None,
                    "unverified_onboarded": unverified_onboarded,
                    **await _fetch_etown_staff_flags(con, mobilenumber),
                }
                
        except Exception as e:
            logger.error(f"User existence check failed: {e}", exc_info=True)
            raise


async def update_last_triggered_time(contact_id: str, mobilenumber: Optional[str] = None) -> None:
    """Stamp a contact's lasttriggeredtime, upsert today's active-user row, and mirror both to Dataverse, logging failures.

    Args:
        contact_id: Contact whose lasttriggeredtime is updated.
        mobilenumber: Number used to resolve the contact for the active-users upsert, if any.
    """
    try:
        row = await Database.fetchrow(
            "UPDATE etown_contact SET lasttriggeredtime = NOW(), modifieddate = NOW() "
            "WHERE contactid = $1 RETURNING lasttriggeredtime",
            contact_id,
        )
        if row is None:
            logger.warning(f"[users_exists] No contact found for lasttriggeredtime update | contact_id={contact_id}")
            return

        last_triggered_time = row.get("lasttriggeredtime")
        logger.info(f"[users_exists] lasttriggeredtime updated | contact_id={contact_id}")

        if mobilenumber:
            contact_row = await Database.fetchrow(
                "SELECT contactid FROM etown_contact WHERE mobilephone = $1 LIMIT 1",
                mobilenumber,
            )
            if contact_row and contact_row.get("contactid"):
                active_contact_id = str(contact_row.get("contactid"))
                active_row = await Database.fetchrow(
                    """
                    WITH updated AS (
                        UPDATE etown_activeusers
                        SET contactid = $1, mobilenumber = $2, createdon = NOW()
                        WHERE mobilenumber = $2
                          AND createdon::date = CURRENT_DATE
                        RETURNING contactid, createdon
                    ),
                    inserted AS (
                        INSERT INTO etown_activeusers (contactid, mobilenumber, createdon)
                        SELECT $1, $2, NOW()
                        WHERE NOT EXISTS (SELECT 1 FROM updated)
                        RETURNING contactid, createdon
                    )
                    SELECT 'updated' AS action, createdon FROM updated
                    UNION ALL
                    SELECT 'inserted' AS action, createdon FROM inserted
                    LIMIT 1
                    """,
                    active_contact_id,
                    mobilenumber,
                )
                action = active_row.get("action") if active_row else "inserted"
                active_createdon = active_row.get("createdon") if active_row else None
                logger.info(
                    f"[users_exists] active user {action} | contact_id={active_contact_id} | mobilenumber={mobilenumber}"
                )

                await sync_activeuserslog_to_dataverse(
                    contact_id=active_contact_id,
                    mobilenumber=mobilenumber,
                    createdon=active_createdon,
                    action=action,
                )
            else:
                logger.warning(
                    f"[users_exists] No contact found for active user insert | mobilenumber={mobilenumber}"
                )

        await sync_contact_lasttriggeredtime_to_dataverse(
            contact_id=contact_id,
            last_triggered_time=last_triggered_time,
        )
    except Exception as e:
        logger.error(f"[users_exists] Failed to update lasttriggeredtime | contact_id={contact_id} | error={e}", exc_info=True)


class UserService:
    """Service class for user management operations in EtownDB."""
    
    @staticmethod
    async def register_new_user(user_data: UserRegistrationRequest) -> UserRegistrationResponse:
        """Register or claim a user, then schedule the Dataverse mirror, invite linking and welcome WhatsApp.

        Args:
            user_data: Validated registration request.

        Returns:
            Registration response with contact and profile info.

        Raises:
            HTTPException: 409 when the mobile or email is taken, 500 on other failures.
        """
        mobilenumber_str = str(user_data.mobilenumber)

        if Database._pool is None or Database._pool._closed:
            await Database.init()

        contact_id_pre = str(_uuid_module.uuid4())

        profile_image_url: Optional[str] = None
        if user_data.profileImage:
            profile_image_url = await _upload_profile_image(contact_id_pre, user_data.profileImage)
        elif user_data.profileImageURL:
            profile_image_url = user_data.profileImageURL

        try:
            result = await register_user_in_db(user_data, profile_image_url, contact_id_pre)
        except ValueError as e:
            e_str = str(e)
            if e_str == "mobile_exists":
                try:
                    result = await update_unverified_user_in_db(user_data, profile_image_url)
                except ValueError:
                    raise HTTPException(
                        status_code=409,
                        detail=f"User with mobile number {mobilenumber_str} already exists",
                    )
            elif e_str == "email_exists":
                raise HTTPException(status_code=409, detail="Email Already Exist")
            else:
                logger.error("Unexpected registration error: %s", e, exc_info=True)
                raise HTTPException(status_code=500, detail="Registration failed due to internal server error")
        except Exception as e:
            logger.error("User registration failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Registration failed due to internal server error")

        logger.info("User registration completed successfully for mobile: %s", mobilenumber_str)

        contact_id = result.get("_contact_id", "")
        userprofile_id = result.get("_userprofile_id", "")
        detail_id = result.get("_detail_id")
        claimed = bool(result.get("_claimed"))
        sync_task = asyncio.create_task(
            sync_registration_to_dataverse(
                contact_id=contact_id,
                userprofile_id=userprofile_id,
                detail_id=detail_id,
                claimed=claimed,
                role=user_data.role,
                firstname=user_data.firstname,
                email=user_data.email,
                mobilenumber=mobilenumber_str,
                contact_fields={
                    "mobilephone": mobilenumber_str,
                    "firstname": user_data.firstname,
                    "emailaddress1": user_data.email,
                    "ets_mobilenumberaccesstoken": user_data.MobilenumberAccesstoken,
                    "ets_userlogin": user_data.user_login,
                    "address1_city": user_data.City,
                    "ets_statecode": user_data.state_code,
                    "address1_latitude": user_data.latitude,
                    "address1_longitude": user_data.longitude,
                    "address1_primarycontactname": user_data.address,
                    "ets_additionaladdressinfo": user_data.additionaladdressinfo,
                    "ets_connectycubeid": user_data.Connectycube_id,
                    "ets_userregistrationtype": user_data.userregistration_type,
                    "ets_invitecode": user_data.invite_code,
                    "ets_fromsocialmedia": to_fromsocialmedia_choice(user_data.from_socialmedia),
                    "ets_is_active": "true",
                    "ets_verified": True,
                    "ets_pushnotificationcheck": "True",
                    "accountrolecode": user_data.role,
                },
            )
        )

        if claimed:
            asyncio.create_task(
                promote_verified_number(
                    mobilenumber=mobilenumber_str,
                    contact_id=contact_id,
                    wait_for=sync_task,
                )
            )
            renamed_up_ids = result.get("_renamed_provider_userprofile_ids") or []
            renamed_provider_ids = result.get("_renamed_provider_ids") or []
            if renamed_up_ids or renamed_provider_ids:
                asyncio.create_task(
                    sync_provider_claim_rename_to_dataverse(
                        firstname=user_data.firstname,
                        userprofile_ids=renamed_up_ids,
                        provider_ids=renamed_provider_ids,
                        wait_for=sync_task,
                    )
                )

        from app.etown.invitations.invitation_utils import link_invitation_on_registration
        asyncio.create_task(
            link_invitation_on_registration(
                invite_code=user_data.invite_code,
                mobilenumber=mobilenumber_str,
                invite_contact=contact_id,
                invite_contact_userprofile_id=userprofile_id,
                invitecontact_role=user_data.role,
            )
        )
        from app.etown.notifications.core.registry import NotificationType
        from app.etown.notifications.service import GovernanceRequest, send as gov_send
        asyncio.create_task(
            gov_send(GovernanceRequest(
                userprofileid=str(userprofile_id),
                contactid=str(contact_id),
                mobile_number=user_data.country_code + mobilenumber_str,
                notification_type=NotificationType.WELCOME_WHATSAPP,
                ctx={
                    "name": user_data.firstname,
                    "language": user_data.language,
                },
                feature="registration",
            ))
        )

        return UserRegistrationResponse(**{k: v for k, v in result.items() if not k.startswith("_")})
    
    @staticmethod
    async def check_user_existence(
        mobile: Optional[int] = None,
        email: Optional[str] = None,
        devicetoken: Optional[str] = None,
    ) -> UserExistsDetailedResponse:
        """Return the detailed existence response for a mobile number or email.

        Args:
            mobile: Mobile number to check.
            email: Email address to check.
            devicetoken: Push token whose credsupdate flag is reported.

        Returns:
            Detailed user existence response.

        Raises:
            HTTPException: 400 when neither is given, 500 on lookup failure.
        """
        try:
            if not mobile and not email:
                raise HTTPException(
                    status_code=400,
                    detail="At least one of mobile number or email must be provided"
                )
            
            Database.assert_available()
            if Database._pool is None or Database._pool._closed:
                await Database.init()

            mobilenumber_str = str(mobile) if mobile else None
            
            result = await check_user_exists_in_db(
                mobilenumber=mobilenumber_str, email=email, devicetoken=devicetoken
            )
            
            logger.info(f"User existence check completed for mobile: {mobilenumber_str}, email: {email}")
            return UserExistsDetailedResponse(**result)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"User existence check failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="User existence check failed due to internal server error"
            )
    
    @staticmethod
    def validate_mobile_number_format(mobilenumber: int) -> bool:
        """Return True when the mobile number is 8 to 15 digits."""
        mobile_str = str(mobilenumber)
        return 8 <= len(mobile_str) <= 15 and mobile_str.isdigit()
    
    @staticmethod
    def validate_user_role(role: int) -> bool:
        """Return True when the role is 1 (provider) or 2 (member)."""
        return role in [1, 2]


async def register_new_user(user_data: UserRegistrationRequest) -> UserRegistrationResponse:
    """Register a user through UserService.register_new_user."""
    return await UserService.register_new_user(user_data)


async def update_last_login(
    contact_id: Optional[str],
    user_profile_id: str,
    lastsignup_status: Optional[int],
    membersignup_status: Optional[str],
) -> Dict[str, Any]:
    """Set membersignup on a userprofile, or else reset it and set lastsignupstatus, and return the row.

    Args:
        contact_id: Contact id, used only for logging.
        user_profile_id: Userprofile to update.
        lastsignup_status: Status written when membersignup_status is None.
        membersignup_status: Value written to membersignup alone when not None.

    Returns:
        Etown_-prefixed fields of the updated userprofile, or {} when no row matched.
    """
    logger.info(
        f"[lastlogin] Updating last login | contact_id={contact_id} "
        f"| user_profile_id={user_profile_id} | lastsignup_status={lastsignup_status} "
        f"| membersignup_status={membersignup_status}"
    )

    result: Optional[Any] = None

    if membersignup_status is not None:
        query = """
            UPDATE etown_userprofile
            SET
                membersignup = $1,
                modifieddate = NOW()
            WHERE
                userprofileid = $2
            RETURNING
                contactid,
                contactidname,
                role,
                rolename,
                email,
                mobilenumber,
                userprofileid,
                lastsignupstatus,
                lastsignupstatusname,
                membersignup
        """
        result = await Database.fetchrow(query, membersignup_status, user_profile_id)
        logger.info(f"[lastlogin] membersignup updated | user_profile_id={user_profile_id}")
    else:
        query = """
            UPDATE etown_userprofile
            SET
                membersignup = 'False',
                lastsignupstatus = $1,
                lastsignupstatusname = CASE
                    WHEN $1 = 1 THEN 'True'
                    WHEN $1 = 0 THEN 'False'
                    ELSE NULL
                END,
                modifieddate = NOW()
            WHERE
                userprofileid = $2
            RETURNING
                contactid,
                contactidname,
                role,
                rolename,
                email,
                mobilenumber,
                userprofileid,
                lastsignupstatus,
                lastsignupstatusname,
                membersignup
        """
        result = await Database.fetchrow(query, lastsignup_status, user_profile_id)
        logger.info(f"[lastlogin] membersignup_status is null, updated membersignup/lastsignup fields | user_profile_id={user_profile_id}")

    if result is None:
        return {}

    return {
        "Etown_contactid": str(result["contactid"]) if result["contactid"] else None,
        "Etown_contactidname": result["contactidname"],
        "Etown_role": result["role"],
        "Etown_rolename": result["rolename"],
        "Etown_email": result["email"],
        "Etown_mobilenumber": result["mobilenumber"],
        "Etown_userprofileid": str(result["userprofileid"]) if result["userprofileid"] else None,
        "Etown_lastsignupstatus": result["lastsignupstatus"],
        "Etown_lastsignupstatusname": result["lastsignupstatusname"],
        "Etown_membersignup": result["membersignup"],
    }


async def reset_other_profiles_after_lastlogin(contact_id: str, user_profile_id: str) -> None:
    """Reset signup flags on the contact's other userprofiles and mirror each to Dataverse, logging failures."""
    try:
        query = """
            WITH records_to_update AS (
                SELECT userprofileid
                FROM etown_userprofile
                WHERE
                    contactid = $1
                    AND userprofileid <> $2
                    AND userprofileid IS NOT NULL
            )
            UPDATE etown_userprofile up
            SET
                lastsignupstatus = 0,
                lastsignupstatusname = 'False',
                membersignup = 'False',
                modifieddate = NOW()
            FROM records_to_update r
            WHERE up.userprofileid = r.userprofileid
            RETURNING
                up.userprofileid,
                up.lastsignupstatus,
                up.lastsignupstatusname,
                up.membersignup
        """
        updated_rows = await Database.fetch(query, contact_id, user_profile_id)

        for row in updated_rows:
            await sync_userprofile_lastlogin_to_dataverse(
                user_profile_id=str(row["userprofileid"]) if row["userprofileid"] else "",
                lastsignup_status=row["lastsignupstatus"],
                membersignup_status=row["membersignup"],
            )

        updated_ids = [str(row["userprofileid"]) for row in updated_rows if row["userprofileid"]]
        logger.info(
            f"[lastlogin] Background reset completed for other profiles | "
            f"contact_id={contact_id} | user_profile_id={user_profile_id} | "
            f"updated_profiles={len(updated_rows)} | updated_userprofile_ids={updated_ids}"
        )
    except Exception as e:
        logger.error(
            f"[lastlogin] Background reset failed for other profiles | "
            f"contact_id={contact_id} | user_profile_id={user_profile_id} | error={e}",
            exc_info=True,
        )


@with_retry()
async def promote_member_to_verified(contact_id: str) -> Optional[Dict[str, Any]]:
    """Mark a member contact's contact, role-2 userprofile and member rows verified when any of them is not.

    Args:
        contact_id: Contact whose member records are promoted.

    Returns:
        contact_id, userprofile_ids and member_ids that changed, or None when nothing needed promoting.

    Raises:
        RuntimeError: If the database pool is not initialized.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")

    row = await Database.fetchrow(
        """
        WITH target AS (
            SELECT $1::text AS contactid
            WHERE EXISTS (SELECT 1 FROM etown_member WHERE contactid = $1)
              AND (
                  EXISTS (
                      SELECT 1 FROM etown_member
                      WHERE contactid = $1 AND verified IS DISTINCT FROM TRUE
                  )
                  OR EXISTS (
                      SELECT 1 FROM etown_userprofile
                      WHERE contactid = $1 AND role = 2 AND verified IS DISTINCT FROM TRUE
                  )
                  OR EXISTS (
                      SELECT 1 FROM etown_contact
                      WHERE contactid = $1 AND verified IS DISTINCT FROM TRUE
                  )
              )
        ),
        upd_contact AS (
            UPDATE etown_contact c
            SET verified = TRUE, modifieddate = NOW()
            FROM target t WHERE c.contactid = t.contactid
            RETURNING c.contactid
        ),
        upd_userprofile AS (
            UPDATE etown_userprofile up
            SET verified = TRUE, modifieddate = NOW()
            FROM target t WHERE up.contactid = t.contactid AND up.role = 2
            RETURNING up.userprofileid
        ),
        upd_member AS (
            UPDATE etown_member m
            SET verified = TRUE, modifieddate = NOW()
            FROM target t WHERE m.contactid = t.contactid
            RETURNING m.memberid
        )
        SELECT
            (SELECT contactid FROM target)                  AS contact_id,
            ARRAY(SELECT userprofileid FROM upd_userprofile) AS userprofile_ids,
            ARRAY(SELECT memberid FROM upd_member)           AS member_ids,
            (SELECT count(*) FROM upd_contact)               AS contact_rows
        """,
        contact_id,
    )

    if not row or row["contact_id"] is None:
        return None

    if not row["contact_rows"]:
        logger.warning(
            f"[member_promote] No etown_contact row for contact_id={contact_id}; "
            "member records promoted without a contact flip"
        )

    return {
        "contact_id": str(row["contact_id"]),
        "userprofile_ids": [str(x) for x in row["userprofile_ids"]],
        "member_ids": [str(x) for x in row["member_ids"]],
    }


async def fetch_active_users_by_date(date: str) -> Dict[str, str]:
    """Return a firstname-to-mobilenumber map of users active on a yyyy-mm-dd date."""
    logger.info(f"[active_users] Fetching active users | date={date}")

    parsed_date = datetime.date.fromisoformat(date)
    rows = await Database.fetch(
        """
        SELECT
            c.firstname,
            a.mobilenumber
        FROM etown_activeusers a
        JOIN etown_contact c ON c.contactid::text = a.contactid
        WHERE a.createdon::date = $1
        """,
        parsed_date,
    )

    result: Dict[str, str] = {row["firstname"]: row["mobilenumber"] for row in rows}

    logger.info(f"[active_users] Found {len(result)} active user(s) | date={date}")
    return result


_USER_LOCATION_QUERY = """
    SELECT
        userlocationdetailsid,
        additionaladdressinfo,
        addresstype,
        homeaddress,
        workaddress,
        landmark,
        latitude,
        longitude,
        statecode,
        city,
        contactid,
        contactidname,
        createdon
    FROM etown_userlocationdetails
    WHERE contactid = $1
    ORDER BY createdon DESC
"""


async def update_consent(
    contactid: str,
    aisearch_consent: Optional[str] = None,
    chattie_consent: Optional[str] = None,
) -> Dict[str, Any]:
    """Store the given lowercased AI search and Chaatie consent flags on a contact and return the stored values.

    Args:
        contactid: Contact to update.
        aisearch_consent: AI search consent value, if changing.
        chattie_consent: Chaatie consent value, if changing.

    Returns:
        Dict with contactid, aisearch_consent and chattie_consent.

    Raises:
        ValueError: If neither consent is given or no contact matches.
    """
    if aisearch_consent is None and chattie_consent is None:
        raise ValueError("At least one of aisearch_consent or chattie_consent must be provided")

    logger.info(
        f"[consent] Updating consent | contactid={contactid} "
        f"| aisearch_consent={aisearch_consent} | chattie_consent={chattie_consent}"
    )

    set_clauses = []
    params: List[Any] = []

    if aisearch_consent is not None:
        params.append(aisearch_consent.lower())
        set_clauses.append(f"aisearch_consent = ${len(params)}")

    if chattie_consent is not None:
        params.append(chattie_consent.lower())
        set_clauses.append(f"chattie_consent = ${len(params)}")

    set_clauses.append("modifieddate = NOW()")
    params.append(contactid)
    where_param = f"${len(params)}"

    query = f"""
        UPDATE etown_contact
        SET {', '.join(set_clauses)}
        WHERE contactid = {where_param}
        RETURNING contactid, aisearch_consent, chattie_consent
    """

    row = await Database.fetchrow(query, *params)

    if row is None:
        raise ValueError(f"No contact found with contactid={contactid}")

    result = {
        "contactid": str(row["contactid"]),
        "aisearch_consent": row["aisearch_consent"],
        "chattie_consent": row["chattie_consent"],
    }
    logger.info(f"[consent] Consent updated | contactid={contactid}")
    return result


async def update_contact_address(
    contact_id: str,
    address: Optional[str] = None,
    additionaladdressinfo: Optional[str] = None,
    latitude: Optional[str] = None,
    longitude: Optional[str] = None,
    statecode: Optional[str] = None,
    city: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update the supplied address fields on a contact and return the JSON-safe row, or None when not found.

    Args:
        contact_id: Contact to update.
        address: Street address, written to both address and addresscomp.
        additionaladdressinfo: Additional address information.
        latitude: Latitude as a string, cast to float.
        longitude: Longitude as a string, cast to float.
        statecode: State code.
        city: City name.

    Returns:
        All columns of the updated row, or None when no contact matched.

    Raises:
        ValueError: If contact_id is missing or a coordinate is not a number.
    """
    if not contact_id:
        raise ValueError("contact_id is required")

    logger.info(f"[update_address] Updating address | contact_id={contact_id}")

    def _to_float(value: Optional[str], field: str) -> Optional[float]:
        if value is None:
            return None
        value = value.strip()
        if value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid {field}: must be a number")

    query = """
        UPDATE etown_contact
        SET address = COALESCE($1, address),
            addresscomp = COALESCE($1, addresscomp),
            additionaladdressinfo = COALESCE($2, additionaladdressinfo),
            latitude = COALESCE($3, latitude),
            longitude = COALESCE($4, longitude),
            statecode = COALESCE($5, statecode),
            city = COALESCE($6, city),
            modifieddate = NOW()
        WHERE contactid = $7
        RETURNING *
    """

    row = await Database.fetchrow(
        query,
        address,
        additionaladdressinfo,
        _to_float(latitude, "latitude"),
        _to_float(longitude, "longitude"),
        statecode,
        city,
        contact_id,
    )

    if row is None:
        logger.info(f"[update_address] No contact found | contact_id={contact_id}")
        return None

    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (datetime.datetime, datetime.date)):
            return value.isoformat()
        return str(value)

    result = {key: _json_safe(val) for key, val in dict(row).items()}
    logger.info(f"[update_address] Address updated | contact_id={contact_id}")
    return result


async def fetch_user_location_details(contact_id: str) -> List[Dict[str, Any]]:
    """Return a contact's saved locations, newest first, as Etown_-prefixed dicts."""
    logger.info(f"[userlocation_details] Fetching location details | contact_id={contact_id}")

    rows = await Database.fetch(_USER_LOCATION_QUERY, contact_id)

    results: List[Dict[str, Any]] = []
    for row in rows:
        createdon = row["createdon"]
        if createdon is not None:
            createdon_str = createdon.isoformat() if hasattr(createdon, "isoformat") else str(createdon)
        else:
            createdon_str = None

        results.append({
            "Etown_contactid": str(row["contactid"]) if row["contactid"] else None,
            "Etown_latitude": str(row["latitude"]) if row["latitude"] is not None else None,
            "Etown_longitude": str(row["longitude"]) if row["longitude"] is not None else None,
            "Etown_type": row["addresstype"],
            "Etown_homeaddress": row["homeaddress"],
            "Etown_workaddress": row["workaddress"],
            "Etown_landmark": row["landmark"],
            "Etown_statecode": row["statecode"],
            "Etown_city": row["city"],
            "Etown_createdon": createdon_str,
            "Etown_userlocationid": str(row["userlocationdetailsid"]) if row["userlocationdetailsid"] else None,
            "Etown_additionaladdressinfo": row["additionaladdressinfo"],
        })

    logger.info(f"[userlocation_details] Found {len(results)} record(s) | contact_id={contact_id}")
    return results


async def manage_user_location_details(
    requiredaction: str,
    contact_id: Optional[str] = None,
    latitude: Optional[str] = None,
    longitude: Optional[str] = None,
    additionaladdressinfo: Optional[str] = None,
    type: Optional[str] = None,
    homeaddress: Optional[str] = None,
    workaddress: Optional[str] = None,
    city: Optional[str] = None,
    statecode: Optional[str] = None,
    landmark: Optional[str] = None,
    editlocationrow_id: Optional[str] = None,
    deletelocationrow_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Add, edit or delete an etown_userlocationdetails row according to requiredaction.

    Args:
        requiredaction: 'add', 'edit' or 'delete', case-insensitive.
        contact_id: Owning contact for add and edit.
        latitude: Latitude as a string, cast to float.
        longitude: Longitude as a string, cast to float.
        additionaladdressinfo: Additional address information.
        type: Address type, such as home or work.
        homeaddress: Home address.
        workaddress: Work address.
        city: City name.
        statecode: State code.
        landmark: Nearby landmark.
        editlocationrow_id: Row to update; required for edit.
        deletelocationrow_id: Row to delete; required for delete.

    Returns:
        The added or edited row, {} when none matched, or a message for delete.

    Raises:
        ValueError: If the action is unknown or the needed row id is missing.
    """
    action = requiredaction.lower().strip()
    logger.info(f"[locationdetails] action={action} | contact_id={contact_id}")

    lat = float(latitude) if latitude is not None else None
    lon = float(longitude) if longitude is not None else None

    if action == "add":
        query = """
            INSERT INTO etown_userlocationdetails (
                userlocationdetailsid,
                additionaladdressinfo,
                addresstype,
                homeaddress,
                workaddress,
                landmark,
                latitude,
                longitude,
                statecode,
                city,
                contactid,
                contactidname,
                createdon
            )
            VALUES (
                gen_random_uuid()::text,
                $1,
                $2,
                $3,
                $4,
                $5,
                $6::double precision,
                $7::double precision,
                $8,
                $9,
                $10,
                (
                    SELECT firstname
                    FROM etown_contact
                    WHERE contactid = $10
                    LIMIT 1
                ),
                NOW()
            )
            RETURNING *;
        """
        row = await Database.fetchrow(
            query,
            additionaladdressinfo,
            type,
            homeaddress,
            workaddress,
            landmark,
            lat,
            lon,
            statecode,
            city,
            contact_id,
        )
        if row is None:
            return {}
        result = dict(row)
        for key, value in result.items():
            if hasattr(value, "isoformat"):
                result[key] = value.isoformat()
        logger.info(f"[locationdetails] add completed | userlocationdetailsid={result.get('userlocationdetailsid')}")
        return result

    elif action == "edit":
        if not editlocationrow_id:
            raise ValueError("editlocationrow_id is required for edit action")
        query = """
            UPDATE etown_userlocationdetails
            SET
                additionaladdressinfo = $1,
                addresstype = $2,
                homeaddress = $3,
                workaddress = $4,
                landmark = $5,
                latitude = $6::double precision,
                longitude = $7::double precision,
                statecode = $8,
                city = $9,
                contactid = $10,
                contactidname = (
                    SELECT firstname
                    FROM etown_contact
                    WHERE contactid = $10
                    LIMIT 1
                ),
                modifieddate = NOW()
            WHERE userlocationdetailsid = $11
            RETURNING *;
        """
        row = await Database.fetchrow(
            query,
            additionaladdressinfo,
            type,
            homeaddress,
            workaddress,
            landmark,
            lat,
            lon,
            statecode,
            city,
            contact_id,
            editlocationrow_id,
        )
        if row is None:
            return {}
        result = dict(row)
        for key, value in result.items():
            if hasattr(value, "isoformat"):
                result[key] = value.isoformat()
        logger.info(f"[locationdetails] edit completed | userlocationdetailsid={editlocationrow_id}")
        return result

    elif action == "delete":
        if not deletelocationrow_id:
            raise ValueError("deletelocationrow_id is required for delete action")
        query = """
            DELETE FROM etown_userlocationdetails
            WHERE userlocationdetailsid = $1;
        """
        await Database.execute(query, deletelocationrow_id)
        logger.info(f"[locationdetails] delete completed | userlocationdetailsid={deletelocationrow_id}")
        return {"message": "Deleted successfully"}

    else:
        raise ValueError(f"Invalid requiredaction: '{requiredaction}'. Must be one of: add, edit, delete")


async def fetch_service_list(
    getcontact_id: Optional[str],
    contact_id: Optional[str],
    userprofile_id: Optional[str],
    is_sameuser: Optional[str],
    mobilenumber: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return a contact's member and provider profiles, excluding deleted and review-pending providers.

    Args:
        getcontact_id: Contact whose profiles are listed.
        contact_id: Requesting contact, or the target when getcontact_id is absent.
        userprofile_id: Requesting userprofile.
        is_sameuser: "true" when the requester owns the profiles; disabled providers show only then.
        mobilenumber: Number that selects the mobile-number branch.

    Returns:
        Service list records with Etown_-prefixed keys.

    Raises:
        ValueError: If the ids required for the chosen branch are missing.
    """
    logger.info(
        f"[service_list] Fetching service list | getcontact_id={getcontact_id} "
        f"| contact_id={contact_id} | userprofile_id={userprofile_id} "
        f"| is_sameuser={is_sameuser} | mobilenumber={mobilenumber}"
    )

    same_user = bool(is_sameuser) and is_sameuser.lower() == "true"
    has_sameuser_flag = bool(is_sameuser) and is_sameuser.lower() in ("true", "false")

    if same_user and (getcontact_id or contact_id):
        lookup_contact_id = getcontact_id or contact_id

        query = """
SELECT
    up.userprofileid,
    up.contactid,
    up.email                        AS up_email,
    up.mobilenumber                 AS up_mobilenumber,
    up.lastsignupstatus,
    up.lastsignupstatusname,
    up.membersignup,
    up.role                         AS up_role,
    up.rolename                     AS up_rolename,
    up.createdon                    AS up_createdon,
    up.verified                     AS up_verified,
    CASE
        WHEN up.role = 2 THEN c.profileimage
        WHEN up.role = 1 THEN p.serviceimageurl
        ELSE NULL
    END AS "Etown_imageurl",
    CASE
        WHEN up.role = 2 THEN c.firstname
        WHEN up.role = 1 THEN p.businessname
        ELSE NULL
    END AS "Etown_name",
    CASE
        WHEN up.role = 2 THEN c.city
        WHEN up.role = 1 THEN p.city
        ELSE NULL
    END AS "Etown_city",
    CASE
        WHEN up.role = 2 THEN c.statecode
        WHEN up.role = 1 THEN p.statecode
        ELSE NULL
    END AS "Etown_statecode",
    CASE
        WHEN up.role = 2 THEN c.address
        WHEN up.role = 1 THEN p.address
        ELSE NULL
    END AS "Etown_Address",
    CASE
        WHEN up.role = 2 THEN c.latitude
        WHEN up.role = 1 THEN p.latitude
        ELSE NULL
    END AS "Etown_latitude",
    CASE
        WHEN up.role = 2 THEN c.longitude
        WHEN up.role = 1 THEN p.longitude
        ELSE NULL
    END AS "Etown_longitude",
    CASE
        WHEN up.role = 2 THEN c.createdon
        WHEN up.role = 1 THEN p.creationdate
        ELSE NULL
    END AS "Etown_createdon",
    p.providerid,
    p.businessname,
    p.businesscategory,
    p.address                       AS p_address,
    p.city                          AS p_city,
    p.statecode                     AS p_statecode,
    p.latitude                      AS p_latitude,
    p.longitude                     AS p_longitude,
    p.email                         AS p_email,
    p.mobilenumber                  AS p_mobilenumber,
    p.serviceimageurl,
    p.yearsinbusiness,
    p.publicsearchprovider,
    p.providertype,
    p.disabled,
    p.trusted,
    p.boosted,
    p.creationdate                  AS p_createdon,
    c.contactid                     AS c_contactid,
    c.firstname,
    c.profileimage,
    c.city                          AS c_city,
    c.statecode                     AS c_statecode,
    c.latitude                      AS c_latitude,
    c.longitude                     AS c_longitude,
    c.createdon                     AS c_createdon
FROM
    public.etown_userprofile up
LEFT OUTER JOIN
    public.etown_provider p
    ON p.userprofileid = up.userprofileid
LEFT OUTER JOIN
    public.etown_contact c
    ON c.contactid = up.contactid
WHERE
    up.contactid = $1
    """ + _EXCLUDE_DELETED_P + """
    """ + _EXCLUDE_REVIEW_PENDING_P + """
ORDER BY
    up.userprofileid ASC,
    p.providerid ASC,
    c.contactid ASC
"""
        rows = await Database.fetch(query, lookup_contact_id)

        def _str(val) -> Optional[str]:
            return str(val) if val is not None else None

        def _iso(val) -> Optional[str]:
            if val is None:
                return None
            return val.isoformat() if hasattr(val, "isoformat") else str(val)

        results: List[Dict[str, Any]] = [
            {
                "Etown_membersignup": row["membersignup"],
                "Etown_userprofileid": _str(row["userprofileid"]),
                "Etown_role": row["up_role"],
                "Etown_contactid": _str(row["c_contactid"]),
                "Etown_lastsignupstatus": row["lastsignupstatus"],
                "Etown_imageurl": row["Etown_imageurl"],
                "Etown_name": row["Etown_name"],
                "Etown_city": row["Etown_city"],
                "Etown_statecode": row["Etown_statecode"],
                "Etown_Address": row["Etown_Address"],
                "Etown_latitude": _str(row["Etown_latitude"]),
                "Etown_longitude": _str(row["Etown_longitude"]),
                "Etown_createdon": _iso(row["Etown_createdon"]),
                "Etown_providertype": row["providertype"],
                "Etown_disabled": row["disabled"],
                "Etown_verified": row["up_verified"],
            }
            for row in rows
        ]

        logger.info(f"[service_list] Found {len(results)} record(s) | contact_id={lookup_contact_id}")
        return results

    elif mobilenumber and (has_sameuser_flag or not getcontact_id):
        if not contact_id or not userprofile_id:
            logger.info(
                "[service_list] contact_id/userprofile_id not supplied — returning "
                f"profile without mycircle/invitation context | mobilenumber={mobilenumber}"
            )

        query = """
WITH

userprofile_data AS (
    SELECT
        up.userprofileid,
        up.contactid,
        up.email                            AS up_email,
        up.mobilenumber                     AS up_mobilenumber,
        up.lastsignupstatus,
        up.lastsignupstatusname,
        up.membersignup,
        up.role                             AS up_role,
        up.rolename                         AS up_rolename,
        up.createdon                        AS up_createdon,
        up.verified                         AS up_verified,

        p.providerid,
        p.businessname,
        p.providertype,
        p.disabled,
        p.serviceimageurl,
        p.city                              AS p_city,
        p.statecode                         AS p_statecode,
        p.address                           AS p_address,
        p.latitude                          AS p_latitude,
        p.longitude                         AS p_longitude,
        p.creationdate                      AS p_createdon,

        c.contactid                         AS c_contactid,
        c.firstname,
        c.profileimage,
        c.city                              AS c_city,
        c.statecode                         AS c_statecode,
        c.address                           AS c_primarycontactname,
        c.latitude                          AS c_latitude,
        c.longitude                         AS c_longitude,
        c.createdon                         AS c_createdon

    FROM public.etown_userprofile up
    LEFT JOIN public.etown_provider p ON p.userprofileid = up.userprofileid
    LEFT JOIN public.etown_contact c  ON c.contactid = up.contactid

    WHERE up.contactid IN (
        SELECT contactid
        FROM etown_contact
        WHERE mobilephone = $1
          AND (rolecode = 2 OR verified IS TRUE)
    )
      AND ($4::boolean OR p.disabled IS DISTINCT FROM true)
      """ + _EXCLUDE_DELETED_P + """
      """ + _EXCLUDE_REVIEW_PENDING_P + """
),

member_relation AS (
    SELECT
        mr.from_userprofile                 AS fromuserprofile,
        mr.to_userprofile                   AS touserprofile,
        'Mycircle'                          AS status,
        mr.relationshiptype::text           AS role

    FROM public.etown_memberrelation mr

    WHERE
        (mr.from_contactid = $2 AND mr.from_userprofile = $3)
        OR
        (mr.to_contactid   = $2 AND mr.to_userprofile   = $3)

    GROUP BY
        mr.from_userprofile,
        mr.to_userprofile,
        mr.relationshiptype
),

provider_relation AS (
    SELECT
        pr.fromuserprofileid                AS fromuserprofile,
        pr.touserprofileid                  AS touserprofile,
        'Mycircle'                          AS status,
        pr.relationshiptype::text           AS role

    FROM public.etown_providerrelation pr

    WHERE
        (pr.fromcontactid = $2 AND pr.fromuserprofileid = $3)
        OR
        (pr.tocontactid   = $2 AND pr.touserprofileid   = $3)

    GROUP BY
        pr.fromuserprofileid,
        pr.touserprofileid,
        pr.relationshiptype
),

mycircle_data AS (
    SELECT
        fromuserprofile,
        touserprofile,
        status,
        role
    FROM member_relation

    UNION

    SELECT
        fromuserprofile,
        touserprofile,
        status,
        role
    FROM provider_relation

    GROUP BY
        fromuserprofile,
        touserprofile,
        status,
        role
),

invitation_data AS (
    SELECT
        i.inviteruserprofile                AS fromuserprofile,
        i.inviteuserprofile                 AS touserprofile,
        'Invitation'                        AS invitestatus

    FROM public.etown_invitation i

    WHERE
        (
            (i.invitercontact = $2 AND i.inviteruserprofile = $3)
            OR
            (i.invitecontact  = $2 AND i.inviteuserprofile  = $3)
        )
        AND i.invitationcode = 'Requested'

    GROUP BY
        i.inviteruserprofile,
        i.inviteuserprofile
)

SELECT
    up.userprofileid,
    up.contactid,
    up.up_email,
    up.up_mobilenumber,
    up.lastsignupstatus,
    up.lastsignupstatusname,
    up.membersignup,
    up.up_role,
    up.up_rolename,
    up.up_createdon,

    CASE
        WHEN up.up_role = 2 THEN up.profileimage
        WHEN up.up_role = 1 THEN up.serviceimageurl
        ELSE NULL
    END AS "Etown_imageurl",

    CASE
        WHEN up.up_role = 2 THEN up.firstname
        WHEN up.up_role = 1 THEN up.businessname
        ELSE NULL
    END AS "Etown_name",

    CASE
        WHEN up.up_role = 2 THEN up.c_city
        WHEN up.up_role = 1 THEN up.p_city
        ELSE NULL
    END AS "Etown_city",

    CASE
        WHEN up.up_role = 2 THEN up.c_statecode
        WHEN up.up_role = 1 THEN up.p_statecode
        ELSE NULL
    END AS "Etown_statecode",

    CASE
        WHEN up.up_role = 2 THEN up.c_primarycontactname
        WHEN up.up_role = 1 THEN up.p_address
        ELSE NULL
    END AS "Etown_Address",

    CASE
        WHEN up.up_role = 2 THEN up.c_latitude
        WHEN up.up_role = 1 THEN up.p_latitude
        ELSE NULL
    END AS "Etown_latitude",

    CASE
        WHEN up.up_role = 2 THEN up.c_longitude
        WHEN up.up_role = 1 THEN up.p_longitude
        ELSE NULL
    END AS "Etown_longitude",

    CASE
        WHEN up.up_role = 2 THEN up.c_createdon
        WHEN up.up_role = 1 THEN up.p_createdon
        ELSE NULL
    END AS "Etown_createdon",

    up.up_verified,
    up.providertype,
    up.disabled,

    mc.status                               AS "Etown_mycircle_status",
    mc.role                                 AS "Etown_userrelationshipname",
    inv.invitestatus                        AS "Etown_InvitationStatus"

FROM userprofile_data up

LEFT JOIN mycircle_data mc
    ON mc.fromuserprofile = up.userprofileid
    OR mc.touserprofile   = up.userprofileid

LEFT JOIN invitation_data inv
    ON inv.fromuserprofile = up.userprofileid
    OR inv.touserprofile   = up.userprofileid

GROUP BY
    up.userprofileid,
    up.contactid,
    up.up_email,
    up.up_mobilenumber,
    up.lastsignupstatus,
    up.lastsignupstatusname,
    up.membersignup,
    up.up_role,
    up.up_rolename,
    up.up_createdon,
    up.profileimage,
    up.serviceimageurl,
    up.firstname,
    up.businessname,
    up.c_city,
    up.p_city,
    up.c_statecode,
    up.p_statecode,
    up.c_primarycontactname,
    up.p_address,
    up.c_latitude,
    up.p_latitude,
    up.c_longitude,
    up.p_longitude,
    up.c_createdon,
    up.p_createdon,
    up.up_verified,
    up.providertype,
    up.disabled,
    mc.status,
    mc.role,
    inv.invitestatus;
"""
        rows = await Database.fetch(query, mobilenumber, contact_id, userprofile_id, same_user)

        def _str(val) -> Optional[str]:
            return str(val) if val is not None else None

        def _iso(val) -> Optional[str]:
            if val is None:
                return None
            return val.isoformat() if hasattr(val, "isoformat") else str(val)

        results: List[Dict[str, Any]] = [
            {
                "Etown_membersignup": row["membersignup"],
                "Etown_userprofileid": _str(row["userprofileid"]),
                "Etown_role": row["up_role"],
                "Etown_contactid": _str(row["contactid"]),
                "Etown_lastsignupstatus": row["lastsignupstatus"],
                "Etown_profileimage": row["Etown_imageurl"],
                "Etown_firstname": row["Etown_name"],
                "Etown_city": row["Etown_city"],
                "Etown_statecode": row["Etown_statecode"],
                "Etown_Address": row["Etown_Address"],
                "Etown_latitude": _str(row["Etown_latitude"]),
                "Etown_longitude": _str(row["Etown_longitude"]),
                "Etown_createdon": _iso(row["Etown_createdon"]),
                "Etown_MycircleStatus": row["Etown_mycircle_status"],
                "Etown_userrelationshipname": row["Etown_userrelationshipname"],
                "Etown_InvitationStatus": row["Etown_InvitationStatus"],
                "Etown_providertype": row["providertype"],
                "Etown_disabled": row["disabled"],
                "Etown_verified": row["up_verified"],
            }
            for row in rows
        ]

        logger.info(f"[service_list] Found {len(results)} record(s) | mobilenumber={mobilenumber}")
        return results

    elif same_user:
        raise ValueError("getcontact_id or contact_id is required when is_sameuser is True")

    else:
        if not getcontact_id or not contact_id or not userprofile_id:
            raise ValueError("getcontact_id, contact_id and userprofile_id are required when is_sameuser is not True")

        query = """
WITH

userprofile_data AS (
    SELECT
        up.userprofileid,
        up.contactid,
        up.email                            AS up_email,
        up.mobilenumber                     AS up_mobilenumber,
        up.lastsignupstatus,
        up.lastsignupstatusname,
        up.membersignup,
        up.role                             AS up_role,
        up.rolename                         AS up_rolename,
        up.createdon                        AS up_createdon,
        up.verified                         AS up_verified,

        p.providerid,
        p.businessname,
        p.providertype,
        p.disabled,
        p.serviceimageurl,
        p.city                              AS p_city,
        p.statecode                         AS p_statecode,
        p.address                           AS p_address,
        p.latitude                          AS p_latitude,
        p.longitude                         AS p_longitude,
        p.creationdate                      AS p_createdon,

        c.contactid                         AS c_contactid,
        c.firstname,
        c.profileimage,
        c.city                              AS c_city,
        c.statecode                         AS c_statecode,
        c.address                           AS c_primarycontactname,
        c.latitude                          AS c_latitude,
        c.longitude                         AS c_longitude,
        c.createdon                         AS c_createdon

    FROM public.etown_userprofile up
    LEFT JOIN public.etown_provider p ON p.userprofileid = up.userprofileid
    LEFT JOIN public.etown_contact c  ON c.contactid = up.contactid

    WHERE up.contactid = $1
      AND p.disabled IS DISTINCT FROM true
      """ + _EXCLUDE_DELETED_P + """
      """ + _EXCLUDE_REVIEW_PENDING_P + """
),

member_relation AS (
    SELECT
        mr.from_userprofile                 AS fromuserprofile,
        mr.to_userprofile                   AS touserprofile,
        'Mycircle'                          AS status,
        mr.relationshiptype::text           AS role

    FROM public.etown_memberrelation mr

    WHERE
        (mr.from_contactid = $2 AND mr.from_userprofile = $3)
        OR
        (mr.to_contactid   = $2 AND mr.to_userprofile   = $3)

    GROUP BY
        mr.from_userprofile,
        mr.to_userprofile,
        mr.relationshiptype
),

provider_relation AS (
    SELECT
        pr.fromuserprofileid                AS fromuserprofile,
        pr.touserprofileid                  AS touserprofile,
        'Mycircle'                          AS status,
        pr.relationshiptype::text           AS role

    FROM public.etown_providerrelation pr

    WHERE
        (pr.fromcontactid = $2 AND pr.fromuserprofileid = $3)
        OR
        (pr.tocontactid   = $2 AND pr.touserprofileid   = $3)

    GROUP BY
        pr.fromuserprofileid,
        pr.touserprofileid,
        pr.relationshiptype
),

mycircle_data AS (
    SELECT
        fromuserprofile,
        touserprofile,
        status,
        role
    FROM member_relation

    UNION

    SELECT
        fromuserprofile,
        touserprofile,
        status,
        role
    FROM provider_relation

    GROUP BY
        fromuserprofile,
        touserprofile,
        status,
        role
),

invitation_data AS (
    SELECT
        i.inviteruserprofile                AS fromuserprofile,
        i.inviteuserprofile                 AS touserprofile,
        'Invitation'                        AS invitestatus

    FROM public.etown_invitation i

    WHERE
        (
            (i.invitercontact = $2 AND i.inviteruserprofile = $3)
            OR
            (i.invitecontact  = $2 AND i.inviteuserprofile  = $3)
        )
        AND i.invitationcode = 'Requested'

    GROUP BY
        i.inviteruserprofile,
        i.inviteuserprofile
)

SELECT
    up.userprofileid,
    up.contactid,
    up.up_email,
    up.up_mobilenumber,
    up.lastsignupstatus,
    up.lastsignupstatusname,
    up.membersignup,
    up.up_role,
    up.up_rolename,
    up.up_createdon,

    CASE
        WHEN up.up_role = 2 THEN up.profileimage
        WHEN up.up_role = 1 THEN up.serviceimageurl
        ELSE NULL
    END AS "Etown_imageurl",

    CASE
        WHEN up.up_role = 2 THEN up.firstname
        WHEN up.up_role = 1 THEN up.businessname
        ELSE NULL
    END AS "Etown_name",

    CASE
        WHEN up.up_role = 2 THEN up.c_city
        WHEN up.up_role = 1 THEN up.p_city
        ELSE NULL
    END AS "Etown_city",

    CASE
        WHEN up.up_role = 2 THEN up.c_statecode
        WHEN up.up_role = 1 THEN up.p_statecode
        ELSE NULL
    END AS "Etown_statecode",

    CASE
        WHEN up.up_role = 2 THEN up.c_primarycontactname
        WHEN up.up_role = 1 THEN up.p_address
        ELSE NULL
    END AS "Etown_Address",

    CASE
        WHEN up.up_role = 2 THEN up.c_latitude
        WHEN up.up_role = 1 THEN up.p_latitude
        ELSE NULL
    END AS "Etown_latitude",

    CASE
        WHEN up.up_role = 2 THEN up.c_longitude
        WHEN up.up_role = 1 THEN up.p_longitude
        ELSE NULL
    END AS "Etown_longitude",

    CASE
        WHEN up.up_role = 2 THEN up.c_createdon
        WHEN up.up_role = 1 THEN up.p_createdon
        ELSE NULL
    END AS "Etown_createdon",

    up.up_verified,
    up.providertype,
    up.disabled,

    mc.status                               AS "Etown_mycircle_status",
    mc.role                                 AS "Etown_userrelationshipname",
    inv.invitestatus                        AS "Etown_InvitationStatus"

FROM userprofile_data up

LEFT JOIN mycircle_data mc
    ON mc.fromuserprofile = up.userprofileid
    OR mc.touserprofile   = up.userprofileid

LEFT JOIN invitation_data inv
    ON inv.fromuserprofile = up.userprofileid
    OR inv.touserprofile   = up.userprofileid

GROUP BY
    up.userprofileid,
    up.contactid,
    up.up_email,
    up.up_mobilenumber,
    up.lastsignupstatus,
    up.lastsignupstatusname,
    up.membersignup,
    up.up_role,
    up.up_rolename,
    up.up_createdon,
    up.profileimage,
    up.serviceimageurl,
    up.firstname,
    up.businessname,
    up.c_city,
    up.p_city,
    up.c_statecode,
    up.p_statecode,
    up.c_primarycontactname,
    up.p_address,
    up.c_latitude,
    up.p_latitude,
    up.c_longitude,
    up.p_longitude,
    up.c_createdon,
    up.p_createdon,
    up.up_verified,
    up.providertype,
    up.disabled,
    mc.status,
    mc.role,
    inv.invitestatus;
"""
        rows = await Database.fetch(query, getcontact_id, contact_id, userprofile_id)

        def _str(val) -> Optional[str]:
            return str(val) if val is not None else None

        def _iso(val) -> Optional[str]:
            if val is None:
                return None
            return val.isoformat() if hasattr(val, "isoformat") else str(val)

        results: List[Dict[str, Any]] = [
            {
                "Etown_membersignup": row["membersignup"],
                "Etown_userprofileid": _str(row["userprofileid"]),
                "Etown_role": row["up_role"],
                "Etown_contactid": _str(row["contactid"]),
                "Etown_lastsignupstatus": row["lastsignupstatus"],
                "Etown_profileimage": row["Etown_imageurl"],
                "Etown_firstname": row["Etown_name"],
                "Etown_city": row["Etown_city"],
                "Etown_statecode": row["Etown_statecode"],
                "Etown_Address": row["Etown_Address"],
                "Etown_latitude": _str(row["Etown_latitude"]),
                "Etown_longitude": _str(row["Etown_longitude"]),
                "Etown_createdon": _iso(row["Etown_createdon"]),
                "Etown_MycircleStatus": row["Etown_mycircle_status"],
                "Etown_userrelationshipname": row["Etown_userrelationshipname"],
                "Etown_InvitationStatus": row["Etown_InvitationStatus"],
                "Etown_providertype": row["providertype"],
                "Etown_disabled": row["disabled"],
                "Etown_verified": row["up_verified"],
            }
            for row in rows
        ]

        logger.info(f"[service_list] Found {len(results)} record(s) | contact_id={contact_id}")
        return results


def _parse_birthdate_flexible(value: Optional[str]) -> Optional[datetime.date]:
    """Parse a DD/MM/YYYY or YYYY-MM-DD birthdate, or return None when empty or unrecognised."""
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    logger.warning("[edit_member] Unrecognised birthdate format | value=%s", value)
    return None


async def update_member_profile_in_db(
    contact_id: str,
    updates: dict,
) -> Optional[dict]:
    """Update a member's contact fields and image, then propagate name, email and mobile to profiles, members and feed.

    Args:
        contact_id: Contact to update.
        updates: Fields set on the request, excluding contact_id.

    Returns:
        contact_payload, userprofile_ids, member_ids and updated_row, or None when the contact is missing.
    """
    exists = await Database.fetchval(
        "SELECT 1 FROM etown_contact WHERE contactid = $1", contact_id
    )
    if not exists:
        return None

    profile_image_raw = updates.pop("profile_image", None)
    resolved_image: Optional[str] = None
    image_should_update = False

    if profile_image_raw == "no image":
        image_should_update = True
        resolved_image = None
    elif profile_image_raw and isinstance(profile_image_raw, str):
        image_should_update = True
        resolved_image = await _upload_profile_image(contact_id, profile_image_raw)

    _CONTACT_COL_MAP = {
        "firstname": "firstname",
        "email": "email",
        "mobilenumber": "mobilephone",
        "additionaladdressinfo": "additionaladdressinfo",
        "address": "address",
        "latitude": "latitude",
        "longitude": "longitude",
        "statecode": "statecode",
        "city": "city",
        "connectycube_id": "connectycubeid",
        "birthdate": "birthdate",
    }

    set_parts: list = []
    params: list = [contact_id]
    normalised: dict = {}

    for field, column in _CONTACT_COL_MAP.items():
        if field not in updates:
            continue
        value = updates[field]
        if field == "mobilenumber" and value is not None:
            value = str(value)
        elif field == "birthdate":
            value = _parse_birthdate_flexible(value)
        normalised[field] = value
        params.append(value)
        set_parts.append(f"{column} = ${len(params)}")

    if image_should_update:
        params.append(resolved_image)
        set_parts.append(f"profileimage = ${len(params)}")

    if set_parts:
        set_parts.append("modifieddate = NOW()")
        await Database.execute(
            f"UPDATE etown_contact SET {', '.join(set_parts)} WHERE contactid = $1",
            *params,
        )

    userprofile_rows = await Database.fetch(
        "SELECT userprofileid, role FROM etown_userprofile WHERE contactid = $1", contact_id
    )
    member_rows = await Database.fetch(
        "SELECT memberid FROM etown_member WHERE contactid = $1", contact_id
    )

    userprofile_ids = [str(r["userprofileid"]) for r in userprofile_rows]
    member_userprofile_ids = [str(r["userprofileid"]) for r in userprofile_rows if r["role"] == 2]
    member_ids = [str(r["memberid"]) for r in member_rows]

    new_name = updates.get("firstname")
    new_email = updates.get("email")
    new_mobile = str(updates["mobilenumber"]) if updates.get("mobilenumber") is not None else None

    related_tasks = []

    for up_row in userprofile_rows:
        uid = str(up_row["userprofileid"])
        up_set: list = []
        up_params: list = [uid]
        if new_name is not None and up_row["role"] == 2:
            up_params.append(new_name)
            up_set.append(f"name = ${len(up_params)}")
            up_set.append(f"contactidname = ${len(up_params)}")
        if new_email is not None:
            up_params.append(new_email)
            up_set.append(f"email = ${len(up_params)}")
        if new_mobile is not None:
            up_params.append(new_mobile)
            up_set.append(f"mobilenumber = ${len(up_params)}")
        if up_set:
            up_set.append("modifieddate = NOW()")
            related_tasks.append(
                Database.execute(
                    f"UPDATE etown_userprofile SET {', '.join(up_set)} WHERE userprofileid = $1",
                    *up_params,
                )
            )

    for mid in member_ids:
        m_set: list = []
        m_params: list = [mid]
        if new_name is not None:
            m_params.append(new_name)
            m_set.append(f"name = ${len(m_params)}")
            m_set.append(f"contactidname = ${len(m_params)}")
            m_set.append(f"userprofileidname = ${len(m_params)}")
        if new_email is not None:
            m_params.append(new_email)
            m_set.append(f"email = ${len(m_params)}")
        if new_mobile is not None:
            m_params.append(new_mobile)
            m_set.append(f"mobilenumber = ${len(m_params)}")
        if m_set:
            m_set.append("modifieddate = NOW()")
            related_tasks.append(
                Database.execute(
                    f"UPDATE etown_member SET {', '.join(m_set)} WHERE memberid = $1",
                    *m_params,
                )
            )

    if new_name is not None:
        related_tasks.append(
            propagate_display_name_to_feed(
                contact_id=contact_id,
                userprofile_ids=member_userprofile_ids,
                new_name=new_name,
                role=2,
            )
        )

    if related_tasks:
        results = await asyncio.gather(*related_tasks, return_exceptions=True)
        for exc in results:
            if isinstance(exc, Exception):
                logger.error(
                    "[edit_member] Related record update failed | contact_id=%s | error=%s",
                    contact_id, exc,
                )

    updated_row = await Database.fetchrow(
        "SELECT firstname, email, address, additionaladdressinfo, profileimage FROM etown_contact WHERE contactid = $1", contact_id
    )

    return {
        "contact_payload": {**updates, **normalised, "resolved_profile_image": resolved_image, "image_updated": image_should_update},
        "userprofile_ids": userprofile_ids,
        "member_ids": member_ids,
        "updated_row": dict(updated_row) if updated_row else {},
    }


async def fetch_provider_business_list(mobilenumber: str) -> List[Dict[str, Any]]:
    """Return businessname, serviceimageurl and disabled for non-deleted, non-deactivated providers on a mobile number, by name."""
    logger.info(f"[business_list] Fetching businesses | mobilenumber={mobilenumber}")

    query = """
SELECT
    p.businessname,
    p.serviceimageurl,
    p.disabled
FROM
    public.etown_provider p
WHERE
    p.mobilenumber = $1
    AND lower(COALESCE(p.deleted, 'false')) <> 'true'
    AND NOT (
        lower(COALESCE(p.deactivated, 'false')) = 'true'
        AND lower(COALESCE(p.reactivation, 'false')) <> 'true'
    )
ORDER BY
    p.businessname ASC
"""

    rows = await Database.fetch(query, mobilenumber)

    results: List[Dict[str, Any]] = [
        {
            "businessname": row["businessname"],
            "serviceimageurl": row["serviceimageurl"],
            "disabled": row["disabled"],
        }
        for row in rows
    ]

    logger.info(
        f"[business_list] Found {len(results)} business(es) | mobilenumber={mobilenumber}"
    )
    return results


_ALTERNATE_MOBILE_STATUS_SQL = """
SELECT
    EXISTS (
        SELECT 1 FROM etown_contact
        WHERE right(regexp_replace(mobilephone, '\\D', '', 'g'), 10) = $2
          AND deleted IS DISTINCT FROM 'true'
    ) AS has_account,
    EXISTS (
        SELECT 1 FROM etown_contact
        WHERE right(regexp_replace(alternatemobile, '\\D', '', 'g'), 10) = $2
          AND right(regexp_replace(COALESCE(mobilephone, ''), '\\D', '', 'g'), 10) <> $1
          AND deleted IS DISTINCT FROM 'true'
    ) AS linked_to_another
"""


@with_retry()
async def check_alternate_mobile_status(mobilenumber: str, alternatemobile: str) -> str:
    """Classify an alternate mobile as 'separate_account', 'linked_to_another' or 'no_record' among live contacts.

    Args:
        mobilenumber: Original number, normalised to 10 digits.
        alternatemobile: Alternate number, normalised to 10 digits.

    Returns:
        'separate_account', 'linked_to_another' or 'no_record'.
    """
    row = await Database.fetchrow(_ALTERNATE_MOBILE_STATUS_SQL, mobilenumber, alternatemobile)
    if row["has_account"]:
        return "separate_account"
    if row["linked_to_another"]:
        return "linked_to_another"
    return "no_record"


_STAFF_MEMBER_COLUMNS = "id, mobilenumber, role, name, is_active, createdon, modifiedon"

_STAFF_CONTACT_FIRSTNAME_SQL = """
    SELECT firstname
      FROM etown_contact
     WHERE right(regexp_replace(mobilephone, '\\D', '', 'g'), 10) = $1
       AND deleted IS DISTINCT FROM 'true'
     ORDER BY createdon DESC NULLS LAST
     LIMIT 1
"""


@with_retry()
async def upsert_etown_staff_member(
    mobilenumber: str,
    action_mode: str = "edit",
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create or edit a staff roster row by mobile number, naming it after the newest live contact on that number.

    Args:
        mobilenumber: Staff member's number, normalised to 10 digits.
        action_mode: 'create' or 'edit'.
        role: Role for a new row, or on edit the row to target or role to set.
        is_active: New active flag, or None to keep it; new rows default to true.

    Returns:
        The written row's columns.

    Raises:
        RuntimeError: If the database pool is not initialized.
        ValueError: 'not_found', 'role_ambiguous' or 'role_exists'.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")

    try:
        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            async with con.transaction():
                name = await con.fetchval(_STAFF_CONTACT_FIRSTNAME_SQL, mobilenumber)

                if action_mode == "create":
                    row = await con.fetchrow(
                        f"""
                        INSERT INTO etown_staff_numbers
                            (id, mobilenumber, role, name, is_active, createdon, modifiedon)
                        VALUES (gen_random_uuid(), $1, $2, $3, COALESCE($4, true), now(), now())
                        RETURNING {_STAFF_MEMBER_COLUMNS}
                        """,
                        mobilenumber, role, name, is_active,
                    )
                    return dict(row)

                rows = await con.fetch(
                    "SELECT id, role FROM etown_staff_numbers WHERE mobilenumber = $1 FOR UPDATE",
                    mobilenumber,
                )
                if not rows:
                    raise ValueError("not_found")
                target = next((r for r in rows if r["role"] == role), None)
                if target is None:
                    if len(rows) > 1:
                        raise ValueError("role_ambiguous")
                    target = rows[0]

                row = await con.fetchrow(
                    f"""
                    UPDATE etown_staff_numbers
                       SET role = COALESCE($2, role),
                           name = COALESCE($3, name),
                           is_active = COALESCE($4, is_active),
                           modifiedon = now()
                     WHERE id = $1
                    RETURNING {_STAFF_MEMBER_COLUMNS}
                    """,
                    target["id"], role, name, is_active,
                )
                return dict(row)
    except asyncpg.UniqueViolationError:
        raise ValueError("role_exists")


@with_retry()
async def fetch_etown_staff_members(
    mobilenumber: str,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Return a mobile number's staff roster rows, oldest first, optionally filtered by role and active flag."""
    rows = await Database.fetch(
        f"""
        SELECT {_STAFF_MEMBER_COLUMNS}
          FROM etown_staff_numbers
         WHERE mobilenumber = $1
           AND ($2::text IS NULL OR role = $2)
           AND ($3::boolean IS NULL OR is_active = $3)
         ORDER BY createdon ASC, role ASC
        """,
        mobilenumber, role, is_active,
    )
    return [dict(r) for r in rows]
