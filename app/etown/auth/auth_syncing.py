"""Auth Dataverse Sync - mirrors the proven-email flag to the CRM contact.

Background Dataverse mirror for etown_contact.emailverified after auth_utils.mark_email_verified() writes Postgres; it PATCHes the contact by its Postgres GUID and swallows its own errors, so a CRM failure never blocks a login.

Features:
    - Contact PATCH: sets the emailverified boolean on the contact record
    - Fixed Target: EMAILVERIFIED_FIELD and CONTACT_ENTITY are module constants, not configuration
    - Always On: there is no kill switch; a failure is logged and swallowed instead

Classes:
    No classes defined (module exposes one background sync function and its constants)

Constants:
    CONTACT_ENTITY: Dataverse entity set for contacts
    EMAILVERIFIED_FIELD: Boolean column mirrored from etown_contact.emailverified

Functions:
    sync_emailverified_to_dataverse(): PATCH the emailverified flag onto a CRM contact

Dependencies:
    - httpx: Async Dataverse Web API client
    - app.etown.provider.provider_syncing: shared Dataverse token helper
    - app.core.config: DATAVERSE_URL

Updates:
    02-Aug-2026: Initial creation
    18-Sep-2026: Added the alternate-mobile mirror.
    23-Sep-2026: Removed the alternate-mobile mirror.
"""

import logging

import httpx

from app.core.config import settings
from app.etown.provider.provider_syncing import get_dataverse_token

logger = logging.getLogger(__name__)

CONTACT_ENTITY = "contacts"
EMAILVERIFIED_FIELD = "adx_identity_emailaddress1confirmed"


async def sync_emailverified_to_dataverse(contact_id: str) -> None:
    """PATCH the Dataverse contact's email-confirmed flag to true, logging instead of raising on failure."""
    if not contact_id:
        return

    try:
        base_url = (settings.DATAVERSE_URL or "").rstrip("/")
        if not base_url:
            logger.warning("[auth_emailverified_sync] DATAVERSE_URL is not configured; skipping sync")
            return

        token = await get_dataverse_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "If-Match": "*",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(
                f"{base_url}/api/data/v9.2/{CONTACT_ENTITY}({contact_id})",
                headers=headers,
                json={EMAILVERIFIED_FIELD: True},
            )

        if response.status_code == 204:
            logger.info(f"[auth_emailverified_sync] contact({contact_id}) PATCH success")
        else:
            logger.error(
                f"[auth_emailverified_sync] contact({contact_id}) PATCH failed "
                f"| field={EMAILVERIFIED_FIELD} | status={response.status_code} | body={response.text}"
            )

    except Exception as e:
        logger.error(
            f"[auth_emailverified_sync] Failed Dataverse sync | contact_id={contact_id} | error={e}",
            exc_info=True,
        )
