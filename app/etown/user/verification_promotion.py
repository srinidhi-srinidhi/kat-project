"""Verification Promotion Utilities - promotes unverified records once a mobile number is proven.

Domain helper shared by the login OTP, registration and provider onboarding flows: promotes every unverified provider and member record on a proven mobile number to verified and fires the matching Dataverse mirrors, logging and swallowing any failure.

Features:
    - Single promotion entry point shared by the OTP, registration and onboarding flows
    - Phone-keyed: every unverified contact on the number is promoted, not just one
    - Optional wait_for lets a caller's Dataverse registration sync land first
    - Best-effort: errors are logged and swallowed, and one contact's failure never stops the rest

Classes:
    No classes defined (module exposes a single async promotion entry point)

Functions:
    promote_verified_number(): Promote every unverified provider/member record on a number

Dependencies:
    - app.etown.user.user_utils: fetch_phone_contact_ids, promote_member_to_verified
    - app.etown.provider.provider_utils: ProviderService.promote_provider_to_verified
    - app.etown.provider.provider_syncing / app.etown.user.users_syncing: Dataverse mirrors

Updates:
    29-Jul-2026: Initial creation
    26-Aug-2026: Silent exception handlers now logged with tracebacks.

Notes:
    The promotion and sync helpers are imported inside the function on purpose: user_utils and
    provider_utils both import this module, so module-level imports would be circular.
"""

import logging
from typing import Any, Awaitable, List, Optional

logger = logging.getLogger(__name__)


async def promote_verified_number(
    mobilenumber: Optional[str] = None,
    contact_id: Optional[str] = None,
    wait_for: Optional[Awaitable[Any]] = None,
) -> List[str]:
    """Promote unverified provider and member records for a proven number's contacts, mirroring each promotion to Dataverse.

    Args:
        mobilenumber: Proven number whose contacts are all promoted.
        contact_id: Extra contact to promote alongside the number's contacts.
        wait_for: Awaitable to finish first; its errors are ignored.

    Returns:
        Contact ids that had at least one record promoted.
    """
    if wait_for is not None:
        try:
            await wait_for
        except Exception as e:
            logger.debug("[promote] wait_for failed, promoting anyway: %s", e)

    from app.etown.provider.provider_utils import ProviderService
    from app.etown.provider.provider_syncing import sync_provider_promotion_to_dataverse
    from app.etown.user.user_utils import fetch_phone_contact_ids, promote_member_to_verified
    from app.etown.user.users_syncing import sync_member_promotion_to_dataverse

    contact_ids: List[str] = []
    try:
        if mobilenumber:
            contact_ids.extend(await fetch_phone_contact_ids(str(mobilenumber)))
        if contact_id and str(contact_id) not in contact_ids:
            contact_ids.append(str(contact_id))
    except Exception as e:
        logger.error(
            "[promote] Failed to resolve contacts for mobilenumber=%s: %s", mobilenumber, e,
            exc_info=True,
        )
        if not contact_id:
            return []
        contact_ids = [str(contact_id)]

    promoted: List[str] = []
    for cid in contact_ids:
        try:
            promoted_any = False

            promoted_provider = await ProviderService.promote_provider_to_verified(cid)
            if promoted_provider:
                promoted_any = True
                logger.info("[promote] Promoted unverified provider(s) | contact_id=%s", cid)
                try:
                    await sync_provider_promotion_to_dataverse(
                        contact_id=promoted_provider["contact_id"],
                        userprofile_ids=promoted_provider["userprofile_ids"],
                        provider_ids=promoted_provider["provider_ids"],
                        providerservices_ids=promoted_provider["providerservices_ids"],
                    )
                except Exception as e:
                    logger.error("[promote] Provider Dataverse mirror failed | contact_id=%s: %s", cid, e, exc_info=True)

            promoted_member = await promote_member_to_verified(cid)
            if promoted_member:
                promoted_any = True
                logger.info("[promote] Promoted unverified member | contact_id=%s", cid)
                try:
                    await sync_member_promotion_to_dataverse(
                        contact_id=promoted_member["contact_id"],
                        userprofile_ids=promoted_member["userprofile_ids"],
                        member_ids=promoted_member["member_ids"],
                    )
                except Exception as e:
                    logger.error("[promote] Member Dataverse mirror failed | contact_id=%s: %s", cid, e, exc_info=True)

            if promoted_any:
                promoted.append(cid)
        except Exception as e:
            logger.error("[promote] Promotion failed for contact_id=%s: %s", cid, e, exc_info=True)

    return promoted
