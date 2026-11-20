"""Auth Utilities - Postgres queries for auth challenges, refresh tokens and identity resolution.

Data-access layer behind auth_service: resolves which live contact and profile own an email or mobile, stores and consumes challenges in etown_auth_challenge, throttles sends, manages hashed refresh tokens in etown_auth_refresh_token and sets etown_contact.emailverified.

Features:
    - Identity Resolution: verified-first email/mobile owner lookup across contact and userprofile
    - Deleted Exclusion: rows with deleted = 'true' never resolve as an owner
    - Alternate Mobile: a contact's second number matches, ranked behind any primary-number match
    - Challenge Store: create, look up, attempt-count and atomically consume challenges
    - Send Throttling: per-identifier cooldown and hourly send counts
    - Refresh Tokens: store, resolve, rotate, revoke (single and contact-wide)
    - Email Verification Flag: set etown_contact.emailverified once ownership is proven

Classes:
    No classes defined (module exposes plain async query functions)

Functions:
    normalize_identifier(): Reduce a raw email/mobile to its lookup form
    fetch_identifier_owner(): Resolve the contact + default profile that owns an identifier
    fetch_mobile_holder(): The live contact holding a mobile number, if it is not the caller's
    fetch_contact_identity(): Re-resolve a contact's current profile and role
    create_challenge(): Insert a challenge row and return it
    _challenge_to_dict(): Normalize a challenge asyncpg Record into plain Python types
    fetch_challenge_by_id(): Load a challenge by primary key
    fetch_live_challenge_by_secret(): Load an unconsumed challenge by its secret digest
    fetch_latest_live_challenge(): Load the newest unconsumed OTP challenge for an identifier
    register_attempt(): Increment the attempt counter and return the new value
    consume_challenge(): Atomically mark a challenge consumed; False if already used
    fetch_send_throttle(): Last send time and hourly send count for an identifier
    store_refresh_token(): Persist a hashed refresh token
    fetch_refresh_token(): Load a refresh token row by its digest
    rotate_refresh_token(): Point a spent token at its successor
    revoke_refresh_token(): Revoke a single refresh token by digest
    revoke_contact_refresh_tokens(): Revoke every live refresh token for a contact
    consume_contact_challenges(): Burn every unconsumed challenge for a contact
    mark_email_verified(): Set etown_contact.emailverified

Dependencies:
    - app.db.client: Shared asyncpg pool (Database) and @with_retry

Updates:
    02-Aug-2026: Initial creation
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    18-Sep-2026: Mobile owner lookup also matches a proven alternatemobile, ranked below primary.
    23-Sep-2026: Dropped alternatemobileverified; lookups match any alternatemobile; removed alternate get/set/prove/clear helpers.
"""

import datetime
import logging
import re
import uuid
from typing import Any, Optional

from app.db.client import Database, with_retry

logger = logging.getLogger(__name__)


_DEFAULT_PROFILE_LATERAL = """
LEFT JOIN LATERAL (
    SELECT userprofileid, role
    FROM etown_userprofile
    WHERE contactid = {contact_ref}
      AND deleted IS DISTINCT FROM 'true'
    ORDER BY (role = 2) DESC, createdon ASC NULLS LAST, userprofileid
    LIMIT 1
) p ON TRUE
"""

_EMAIL_OWNER_SQL = f"""
WITH matched AS (
    SELECT c.contactid, c.verified, c.createdon
    FROM etown_contact c
    WHERE lower(btrim(c.email)) = $1
      AND c.deleted IS DISTINCT FROM 'true'
    UNION
    SELECT c.contactid, c.verified, c.createdon
    FROM etown_userprofile u
    JOIN etown_contact c ON c.contactid = u.contactid
    WHERE lower(btrim(u.email)) = $1
      AND u.deleted IS DISTINCT FROM 'true'
      AND c.deleted IS DISTINCT FROM 'true'
)
SELECT m.contactid, m.verified, p.userprofileid, p.role
FROM matched m
{_DEFAULT_PROFILE_LATERAL.format(contact_ref="m.contactid")}
ORDER BY m.verified DESC NULLS LAST, m.createdon ASC NULLS LAST, m.contactid
LIMIT 1
"""

_MOBILE_OWNER_SQL = f"""
WITH matched AS (
    SELECT c.contactid, c.verified, c.createdon, 0 AS match_rank
    FROM etown_contact c
    WHERE right(regexp_replace(c.mobilephone, '\\D', '', 'g'), 10) = $1
      AND c.deleted IS DISTINCT FROM 'true'
    UNION
    SELECT c.contactid, c.verified, c.createdon, 0 AS match_rank
    FROM etown_userprofile u
    JOIN etown_contact c ON c.contactid = u.contactid
    WHERE right(regexp_replace(u.mobilenumber, '\\D', '', 'g'), 10) = $1
      AND u.deleted IS DISTINCT FROM 'true'
      AND c.deleted IS DISTINCT FROM 'true'
    UNION
    SELECT c.contactid, c.verified, c.createdon, 1 AS match_rank
    FROM etown_contact c
    WHERE right(regexp_replace(c.alternatemobile, '\\D', '', 'g'), 10) = $1
      AND c.deleted IS DISTINCT FROM 'true'
)
SELECT m.contactid, m.verified, p.userprofileid, p.role
FROM matched m
{_DEFAULT_PROFILE_LATERAL.format(contact_ref="m.contactid")}
ORDER BY m.match_rank ASC, m.verified DESC NULLS LAST, m.createdon ASC NULLS LAST, m.contactid
LIMIT 1
"""

_OWNER_SQL_BY_CHANNEL = {"email": _EMAIL_OWNER_SQL, "sms": _MOBILE_OWNER_SQL}

MOBILE_HELD_BY_OTHER_SQL = """
    SELECT contactid
    FROM etown_contact
    WHERE $1 <> ''
      AND (right(regexp_replace(COALESCE(mobilephone, ''), '\\D', '', 'g'), 10) = $1
           OR right(regexp_replace(COALESCE(alternatemobile, ''), '\\D', '', 'g'), 10) = $1)
      AND deleted IS DISTINCT FROM 'true'
      AND ($2::text IS NULL OR contactid <> $2)
    LIMIT 1
"""

_CONTACT_IDENTITY_SQL = f"""
SELECT c.contactid, c.email, p.userprofileid, p.role
FROM etown_contact c
{_DEFAULT_PROFILE_LATERAL.format(contact_ref="c.contactid")}
WHERE c.contactid = $1
  AND c.deleted IS DISTINCT FROM 'true'
"""


def normalize_identifier(identifier: str, channel: str) -> str:
    """Return the last 10 digits of an sms identifier, or the trimmed lower-cased email for any other channel."""
    if channel == "sms":
        return re.sub(r"\D", "", identifier or "")[-10:]
    return (identifier or "").strip().lower()


@with_retry()
async def fetch_identifier_owner(identifier: str, channel: str) -> Optional[dict]:
    """Return contactid, userprofileid, role and verified for the owner of a normalized identifier, or None.

    Raises:
        ValueError: When channel is not a supported lookup channel.
    """
    sql = _OWNER_SQL_BY_CHANNEL.get(channel)
    if sql is None:
        raise ValueError(f"Unsupported identity channel: {channel}")
    row = await Database.fetchrow(sql, identifier)
    if row is None:
        return None
    return {
        "contactid": str(row["contactid"]),
        "userprofileid": str(row["userprofileid"]) if row["userprofileid"] else None,
        "role": row["role"],
        "verified": bool(row["verified"]),
    }


@with_retry()
async def fetch_mobile_holder(
    mobilenumber: str,
    exclude_contactid: Optional[str] = None,
) -> Optional[str]:
    """Return the id of another live contact holding a normalized mobile number, or None when it is free."""
    row = await Database.fetchrow(MOBILE_HELD_BY_OTHER_SQL, mobilenumber, exclude_contactid)
    return str(row["contactid"]) if row else None


@with_retry()
async def fetch_contact_identity(contactid: str) -> Optional[dict]:
    """Return a contact's current email identifier, userprofileid and role, or None when missing or deleted."""
    row = await Database.fetchrow(_CONTACT_IDENTITY_SQL, contactid)
    if row is None:
        return None
    return {
        "identifier": (row["email"] or "").strip().lower(),
        "userprofileid": str(row["userprofileid"]) if row["userprofileid"] else None,
        "role": row["role"],
    }


@with_retry()
async def create_challenge(
    *,
    identifier: str,
    method: str,
    secret_hash: str,
    expires_at: datetime.datetime,
    contactid: Optional[str],
    userprofileid: Optional[str],
    role: Optional[int],
    max_attempts: int,
    language_code: Optional[str] = None,
    device_id: Optional[str] = None,
    request_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    channel: str = "email",
    purpose: str = "login",
) -> dict:
    """Insert an authentication challenge and return its id and expires_at.

    Args:
        identifier: Normalized identifier the challenge was issued for.
        method: 'otp', 'magiclink' or 'exchange'.
        secret_hash: Peppered digest of the issued secret.
        expires_at: Absolute expiry instant.
        contactid: Resolved owning contact.
        userprofileid: Resolved default profile, when one exists.
        role: Profile role code.
        max_attempts: Verification attempts permitted before lockout.
        language_code: Delivery language.
        device_id: Optional client device identifier.
        request_ip: Caller IP, for audit.
        user_agent: Caller user agent, for audit.
        channel: Delivery channel, 'email' by default.
        purpose: 'login' or 'verify', 'login' by default.

    Returns:
        Dict with the new challenge's id and expires_at.
    """
    row = await Database.fetchrow(
        """
        INSERT INTO etown_auth_challenge (
            channel, method, purpose, identifier, contactid, userprofileid, role,
            secret_hash, expires_at, max_attempts, language_code, device_id,
            request_ip, user_agent
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        RETURNING id, expires_at
        """,
        channel, method, purpose, identifier, contactid, userprofileid, role,
        secret_hash, expires_at, max_attempts, language_code, device_id,
        request_ip, user_agent,
    )
    return {"id": str(row["id"]), "expires_at": row["expires_at"]}


_CHALLENGE_COLUMNS = (
    "id, channel, method, purpose, identifier, contactid, userprofileid, role, "
    "secret_hash, expires_at, consumed_at, attempts, max_attempts, language_code, device_id"
)


def _challenge_to_dict(row: Any) -> dict:
    """Convert a challenge record selected with _CHALLENGE_COLUMNS into a dict with the id as a string."""
    return {
        "id": str(row["id"]),
        "channel": row["channel"],
        "method": row["method"],
        "purpose": row["purpose"],
        "identifier": row["identifier"],
        "contactid": row["contactid"],
        "userprofileid": row["userprofileid"],
        "role": row["role"],
        "secret_hash": row["secret_hash"],
        "expires_at": row["expires_at"],
        "consumed_at": row["consumed_at"],
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "language_code": row["language_code"],
        "device_id": row["device_id"],
    }


@with_retry()
async def fetch_challenge_by_id(challenge_id: str) -> Optional[dict]:
    """Return a challenge by id, or None when the id is unknown or not a UUID."""
    try:
        uuid.UUID(str(challenge_id))
    except (ValueError, AttributeError, TypeError):
        logger.debug("auth: challenge id %r is not a UUID", challenge_id)
        return None
    row = await Database.fetchrow(
        f"SELECT {_CHALLENGE_COLUMNS} FROM etown_auth_challenge WHERE id = $1::uuid",
        challenge_id,
    )
    return _challenge_to_dict(row) if row else None


@with_retry()
async def fetch_live_challenge_by_secret(secret_hash: str, method: str) -> Optional[dict]:
    """Return the unconsumed challenge matching a secret digest and method, or None."""
    row = await Database.fetchrow(
        f"""
        SELECT {_CHALLENGE_COLUMNS} FROM etown_auth_challenge
        WHERE secret_hash = $1 AND method = $2 AND consumed_at IS NULL
        """,
        secret_hash, method,
    )
    return _challenge_to_dict(row) if row else None


@with_retry()
async def fetch_latest_live_challenge(identifier: str) -> Optional[dict]:
    """Return the newest unconsumed OTP challenge for a normalized identifier, or None."""
    row = await Database.fetchrow(
        f"""
        SELECT {_CHALLENGE_COLUMNS} FROM etown_auth_challenge
        WHERE identifier = $1 AND method = 'otp' AND consumed_at IS NULL
        ORDER BY createdon DESC LIMIT 1
        """,
        identifier,
    )
    return _challenge_to_dict(row) if row else None


@with_retry()
async def register_attempt(challenge_id: str) -> int:
    """Increment a challenge's attempt counter and return the new count, or 0 when the challenge is missing."""
    row = await Database.fetchrow(
        """
        UPDATE etown_auth_challenge SET attempts = attempts + 1
        WHERE id = $1::uuid RETURNING attempts
        """,
        challenge_id,
    )
    return int(row["attempts"]) if row else 0


@with_retry()
async def consume_challenge(challenge_id: str) -> bool:
    """Atomically mark a challenge consumed, returning False when it was already consumed."""
    row = await Database.fetchrow(
        """
        UPDATE etown_auth_challenge SET consumed_at = now()
        WHERE id = $1::uuid AND consumed_at IS NULL
        RETURNING id
        """,
        challenge_id,
    )
    return row is not None


@with_retry()
async def fetch_send_throttle(identifier: str) -> dict:
    """Return last_sent_at and sends_last_hour for an identifier's non-exchange challenges in the past hour."""
    row = await Database.fetchrow(
        """
        SELECT max(last_sent_at) AS last_sent_at, count(*) AS sends_last_hour
        FROM etown_auth_challenge
        WHERE identifier = $1
          AND method <> 'exchange'
          AND last_sent_at > now() - interval '1 hour'
        """,
        identifier,
    )
    return {
        "last_sent_at": row["last_sent_at"] if row else None,
        "sends_last_hour": int(row["sends_last_hour"]) if row and row["sends_last_hour"] else 0,
    }


@with_retry()
async def store_refresh_token(
    *,
    contactid: str,
    token_hash: str,
    expires_at: datetime.datetime,
    device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> str:
    """Insert a hashed refresh token for a contact and return its id."""
    row = await Database.fetchrow(
        """
        INSERT INTO etown_auth_refresh_token (contactid, token_hash, expires_at, device_id, user_agent)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        contactid, token_hash, expires_at, device_id, user_agent,
    )
    return str(row["id"])


@with_retry()
async def fetch_refresh_token(token_hash: str) -> Optional[dict]:
    """Return the refresh token row matching a digest, or None when unknown."""
    row = await Database.fetchrow(
        """
        SELECT id, contactid, expires_at, revoked_at, rotated_to, device_id
        FROM etown_auth_refresh_token WHERE token_hash = $1
        """,
        token_hash,
    )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "contactid": row["contactid"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
        "rotated_to": str(row["rotated_to"]) if row["rotated_to"] else None,
        "device_id": row["device_id"],
    }


@with_retry()
async def rotate_refresh_token(old_id: str, new_id: str) -> bool:
    """Revoke a refresh token and point it at its successor, returning False when it was already revoked."""
    row = await Database.fetchrow(
        """
        UPDATE etown_auth_refresh_token
        SET rotated_to = $2::uuid, revoked_at = now()
        WHERE id = $1::uuid AND revoked_at IS NULL
        RETURNING id
        """,
        old_id, new_id,
    )
    return row is not None


@with_retry()
async def revoke_refresh_token(token_hash: str) -> bool:
    """Revoke the live refresh token matching a digest and return True when one was revoked."""
    row = await Database.fetchrow(
        """
        UPDATE etown_auth_refresh_token SET revoked_at = now()
        WHERE token_hash = $1 AND revoked_at IS NULL
        RETURNING id
        """,
        token_hash,
    )
    return row is not None


@with_retry()
async def revoke_contact_refresh_tokens(contactid: str) -> int:
    """Revoke every live refresh token for a contact and return how many were revoked."""
    rows = await Database.fetch(
        """
        UPDATE etown_auth_refresh_token SET revoked_at = now()
        WHERE contactid = $1 AND revoked_at IS NULL
        RETURNING id
        """,
        contactid,
    )
    return len(rows)


@with_retry()
async def consume_contact_challenges(contactid: str) -> int:
    """Consume every unconsumed challenge belonging to a contact and return how many were consumed."""
    rows = await Database.fetch(
        """
        UPDATE etown_auth_challenge SET consumed_at = now()
        WHERE contactid = $1 AND consumed_at IS NULL
        RETURNING id
        """,
        contactid,
    )
    return len(rows)


@with_retry()
async def mark_email_verified(contactid: str) -> bool:
    """Set emailverified on a contact, returning False when it was already set or the contact is absent."""
    row = await Database.fetchrow(
        """
        UPDATE etown_contact SET emailverified = TRUE, modifieddate = NOW()
        WHERE contactid = $1 AND emailverified IS DISTINCT FROM TRUE
        RETURNING contactid
        """,
        contactid,
    )
    return row is not None
