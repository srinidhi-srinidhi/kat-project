"""AI Onboarding Redirection Tool - sends Etown staff who ask to onboard a provider to the AI onboarding screen.

Member- and provider-agent handler behind aionboarding_redirection: reads the caller's etown_contact.mobilephone, looks for an active etown_staff_numbers row with that number, and returns a redirect payload for staff or a continue-as-usual message otherwise.

Features:
    - Staff Check: One query matches the contact's 10-digit mobile against active etown_staff_numbers rows
    - Redirect Payload: Staff get an "aionboarding_redirection" UI trigger with their role
    - Fail-open: No contact, no match or a query error keeps the normal onboard_provider flow

Classes:
    No classes defined (module exposes a single handler)

Functions:
    handle_aionboarding_redirection(): Return the redirect or continue message and payload for a contact

Dependencies:
    - app.db.client: Database.fetchrow for the contact and staff lookup

Updates:
    28-Sep-2026: Added handle_aionboarding_redirection() checking the caller against etown_staff_numbers.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from app.db.client import Database

logger = logging.getLogger(__name__)

_STAFF_FOR_CONTACT_SQL = """
    SELECT s.mobilenumber, s.role
      FROM etown_contact c
      JOIN etown_staff_numbers s
        ON s.mobilenumber = right(regexp_replace(c.mobilephone, '\\D', '', 'g'), 10)
     WHERE c.contactid = $1
       AND s.is_active IS TRUE
     ORDER BY s.createdon ASC
     LIMIT 1
"""

_CONTINUE_MESSAGE = (
    "The user is not an Etown staff member. Continue the normal provider onboarding flow: "
    "collect the details one question at a time and call onboard_provider."
)

_REDIRECT_MESSAGE = (
    "The user is an Etown staff member, so the app is opening the AI onboarding screen. "
    "Tell the user in one short sentence that they are being taken to AI onboarding. "
    "Do NOT ask for business details and do NOT call onboard_provider."
)


async def handle_aionboarding_redirection(contact_id: Optional[str]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Return the redirect message and payload when the contact's mobile is an active staff number, else the continue message and None.

    Args:
        contact_id: The caller's etown_contact id from the execution context.
    """
    if not contact_id:
        return _CONTINUE_MESSAGE, None

    try:
        row = await Database.fetchrow(_STAFF_FOR_CONTACT_SQL, str(contact_id))
    except Exception as e:
        logger.error("[aionboarding_redirection] Staff lookup failed for contact %s: %s", contact_id, e, exc_info=True)
        return _CONTINUE_MESSAGE, None

    if not row:
        logger.info("[aionboarding_redirection] contact %s is not staff; normal onboarding", contact_id)
        return _CONTINUE_MESSAGE, None

    logger.info("[aionboarding_redirection] contact %s is staff (%s); redirecting", contact_id, row["role"])
    return _REDIRECT_MESSAGE, {
        "status": "redirect",
        "ui_trigger": "aionboarding_redirection",
        "mobilenumber": row["mobilenumber"],
        "role": row["role"],
    }
