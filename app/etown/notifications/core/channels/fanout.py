"""Notification Fanout Helpers - resolves which mobile numbers one SMS or WhatsApp notification reaches.

Notification-core helper used by the SMS and WhatsApp adapters: returns the primary number first, then the recipient's etown_contact.alternatemobile or else their business's etown_provider.alternatemobile, skipping OTP templates and falling back to the primary alone when the lookup fails.

Features:
    - Alternate Resolution: contact's second number, else the business's own
    - Secret Suppression: templates that carry a secret are never fanned out
    - Digit Deduplication: two spellings of one number are one destination

Classes:
    No classes defined (module exposes the resolver and its template deny-list)

Functions:
    _digits(): Reduces a number to its last ten digits
    _fetch_alternate(): Reads the alternate number to also deliver to
    delivery_numbers(): The numbers one notification should be delivered to, in order

Dependencies:
    - app.core.logging: warns when the alternate lookup fails
    - app.db.client: Shared asyncpg pool (Database) and @with_retry
    - app.etown.notifications.core.models: Recipient, for the addressing it reads

Updates:
    18-Sep-2026: Initial creation, with the alternate mobile number.
"""

from __future__ import annotations

import re
from typing import List, Optional

from app.core.logging import logger
from app.db.client import Database, with_retry
from app.etown.notifications.core.models import Recipient

SECRET_TEMPLATE_KEYS = frozenset({"otp_sms", "otp_whatsapp"})

_DIGITS_RE = re.compile(r"\D+")


def _digits(number: Optional[str]) -> str:
    """Return the last ten digits of a phone number for comparison."""
    return _DIGITS_RE.sub("", number or "")[-10:]


_ALTERNATE_SQL = """
    SELECT COALESCE(
               (SELECT c.alternatemobile
                  FROM etown_contact c
                 WHERE c.contactid = $1
                   AND c.deleted IS DISTINCT FROM 'true'),
               (SELECT p.alternatemobile
                  FROM etown_provider p
                 WHERE p.contactid = $1
                   AND p.alternatemobile IS NOT NULL
                   AND p.deleted IS DISTINCT FROM 'true'
                 ORDER BY p.creationdate ASC NULLS LAST, p.providerid
                 LIMIT 1)
           ) AS alternatemobile
"""


@with_retry()
async def _fetch_alternate(contact_id: str) -> Optional[str]:
    """Return the contact's alternate mobile, falling back to its oldest provider's, or None."""
    row = await Database.fetchrow(_ALTERNATE_SQL, contact_id)
    return row["alternatemobile"] if row else None


async def delivery_numbers(
    recipient: Recipient,
    template_key: Optional[str],
    *,
    primary: Optional[str] = None,
) -> List[str]:
    """Return the primary number plus any distinct alternate, primary only for secret templates or on errors.

    Args:
        recipient: Notification addressing; its contact_id is used to find the alternate.
        template_key: Template key; one in SECRET_TEMPLATE_KEYS skips the alternate.
        primary: Number to deliver to first; defaults to recipient.mobile_number.

    Returns:
        The primary then the alternate, or an empty list when there is no primary.
    """
    first = primary if primary is not None else recipient.mobile_number
    if not first:
        return []
    if template_key in SECRET_TEMPLATE_KEYS or not recipient.contact_id:
        return [first]

    try:
        alternate = await _fetch_alternate(recipient.contact_id)
    except Exception as exc:
        logger.warning(
            "notifications: alternate lookup failed for contact=%s, delivering to the "
            "primary only | %s",
            recipient.contact_id, exc, exc_info=True,
        )
        return [first]

    if not alternate or _digits(alternate) == _digits(first):
        return [first]
    return [first, alternate]
