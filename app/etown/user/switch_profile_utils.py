"""Switch Profile Utilities - Postgres logic for adding a second member or provider profile.

Domain layer behind /api/dashboard/switch_profile: lets an existing contact add a member profile by inserting etown_userprofile and etown_member rows through the shared user_utils helpers, or a provider profile through the full ProviderService onboarding pipeline.

Features:
    - Duplicate Detection: Checks if a member (role=2) profile already exists for a contact
    - Member Profile Creation: Atomically inserts etown_userprofile (role=2) + etown_member
    - Provider Profile Creation: runs the full ProviderService.onboard_provider_in_db pipeline
    - Plan Limits: the provider path can refuse with PlanLimitError
    - No SQL Duplication: member INSERT logic uses shared helpers from user_utils

Classes:
    No classes defined (module exposes async DB-operation functions)

Functions:
    check_member_profile_exists(): Returns True if a member profile exists for a contact
    create_member_profile(): Creates member userprofile + member record, returns profile data
    create_provider_profile(): Full provider onboarding via onboard_provider_in_db, returns profile data

Dependencies:
    - app.db.client: Database class
    - app.etown.user.user_utils: Shared inner DB INSERT helpers (_insert_userprofile_record, _insert_member_record)
    - app.etown.provider.provider_utils: ProviderService.onboard_provider_in_db (lazy import inside function)
    - fastapi: HTTPException for 409 handling
    - app.core.logging: Application logger

Updates:
    02-Jul-2026: Initial creation (migrated from PowerApps SwitchProfileAPI)
    03-Jul-2026: Returns detail record IDs; atomic duplicate-member guard; member and provider paths use shared helpers.
    18-Aug-2026: The provider path can now refuse with PlanLimitError.
"""

from typing import Optional

from fastapi import HTTPException

from app.db.client import Database
from app.core.logging import logger
from app.etown.user.user_utils import (
    _insert_userprofile_record,
    _insert_member_record,
)


async def check_member_profile_exists(contact_id: str) -> bool:
    """Return True if a member (role=2) userprofile already exists for this contact."""
    result = await Database.fetchval(
        "SELECT 1 FROM etown_userprofile WHERE contactid = $1 AND role = 2 LIMIT 1",
        contact_id,
    )
    return result is not None


async def create_member_profile(
    contact_id: str,
    firstname: str,
    email: Optional[str],
    mobilenumber: Optional[str],
) -> list[dict]:
    """Create a member userprofile and etown_member row for an existing contact in one transaction.

    Args:
        contact_id: Existing contact UUID in etown_contact.
        firstname: Contact's first name, used for the name columns.
        email: Email address stored on both profile and member records.
        mobilenumber: Mobile number stored on both records.

    Returns:
        Single-item list of ets_-prefixed keys, or [] if a member profile already exists.
    """
    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        async with con.transaction():
            up_row = await _insert_userprofile_record(
                con, contact_id, firstname, 2, mobilenumber, email,
                guard_duplicate=True,
            )
            if not up_row:
                logger.warning(
                    "[switch_profile] create_member_profile: profile already exists | contact_id=%s", contact_id
                )
                return []

            userprofile_id = str(up_row["userprofileid"])
            mem_row = await _insert_member_record(
                con, userprofile_id, contact_id, firstname, mobilenumber, email,
            )

            return [{
                "ets_userprofileid": userprofile_id,
                "ets_contact": str(up_row["contactid"]),
                "ets_role": up_row["role"],
                "ets_email": up_row.get("email"),
                "ets_mobilenumber": up_row.get("mobilenumber"),
                "ets_lastsignupstatus": up_row.get("lastsignupstatus"),
                "ets_memberid": str(mem_row["memberid"]),
            }]


async def create_provider_profile(
    contact_id: str,
    firstname: str,
    email: Optional[str],
    mobilenumber: Optional[str],
) -> list[dict]:
    """Onboard a provider profile for an existing contact through ProviderService.onboard_provider_in_db.

    Args:
        contact_id: Existing contact UUID in etown_contact.
        firstname: Used as both first_name and business_name.
        email: Email address stored on profile and provider records.
        mobilenumber: Mobile number, passed as the phone number.

    Returns:
        Single-item list of ets_-prefixed keys, or [] on a 409 or empty onboard result.

    Raises:
        PlanLimitError: When the contact already holds every provider its pack allows.
    """
    from app.etown.provider.provider_utils import ProviderService as _ProviderService

    try:
        result = await _ProviderService.onboard_provider_in_db(
            contact_id=contact_id,
            phone_number=mobilenumber or "",
            business_name=firstname,
            email=email,
            first_name=firstname,
        )
    except HTTPException as http_exc:
        if http_exc.status_code == 409:
            logger.info(
                "[switch_profile] create_provider_profile: provider already exists | contact_id=%s", contact_id
            )
            return []
        raise

    if not result:
        logger.warning(
            "[switch_profile] create_provider_profile: onboard returned no data | contact_id=%s", contact_id
        )
        return []

    userprofile_id = str(result["userprofile_info"]["userprofileid"])
    provider_id = str(result["provider_info"]["providerid"])

    return [{
        "ets_userprofileid": userprofile_id,
        "ets_contact": contact_id,
        "ets_role": result["userprofile_info"].get("role", 1),
        "ets_email": email,
        "ets_mobilenumber": mobilenumber,
        "ets_lastsignupstatus": None,
        "ets_providerid": provider_id,
    }]
