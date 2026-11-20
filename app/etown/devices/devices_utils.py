"""Devices Utilities - Postgres logic for multi-device push token registration and lookup.

Domain layer behind /api/devices: upserts one etown_contactdevices row per physical device, clears its tokens off every other row, mirrors it onto the contact's other profiles, and resolves FCM and VoIP push targets for the whole person, de-duplicated per app instance.

Features:
    - Multi-device Upsert: Per-device insert/update instead of one row per user
    - Device Matching: device_id -> FCM app instance -> whole token -> VoIP token -> fallback
    - Token Stealing: Incoming tokens and same-app-instance tokens are cleared from other rows
    - Profile Mirror: every other profile of the contact gets a row carrying the same token
    - Row Creation: only an FCM token or device_id creates a row; mirrors keep deviceguid and device_id NULL
    - Dead-row Pruning: Deletes tokenless rows, FCM-invalid rows and spent mirrors
    - Reinstall Closure: Re-registering a device closes its open app-uninstall record
    - Person-wide Reads: All devices of the contact, de-duplicated per app instance, newest first
    - Creds Update Flag: credsupdate set per contact and token, or across every row
    - Error Handling: Retry-decorated DB access with detailed error logging

Classes:
    No classes defined (module exposes SQL query constants and function-based utilities)

Functions:
    _clean(): Trim a string value, returning None when it is empty/whitespace
    fcm_app_instance(): The app-instance id of an FCM token (one physical device)
    _fetch_person_device_rows(): Every device row of the person owning a user profile
    save_device_token(): Upsert one device and return every device of the user
    get_device_tokens_by_userprofile_id(): All FCM tokens of the person behind a user profile
    get_voip_device_tokens_by_userprofile_id(): All VoIP tokens of that person
    get_device_push_targets_by_userprofile_id(): Per-device (fcm_token, voip_token) pairs
    get_device_tokens_by_contact_id(): All FCM tokens for a contact
    update_credsupdate(): Set credsupdate for one contact's device token, or on every row

Constants:
    HAS_DEVICE_SQL: Reusable "person has a registered device" SQL gate for cohort queries

Dependencies:
    - app.db.client: Database connection pool and retry decorator
    - app.core.config: UNINSTALL_DETECTION_ENABLED, UNINSTALL_PROBE_ENABLED
    - app.etown.devices.uninstall_utils: SQL closing an open uninstall on re-registration
    - app.core.logging: Centralized logging infrastructure

Updates:
    02-Jul-2026: Initial creation for PowerApps SaveDeviceToken API migration
    28-Jul-2026: save_device_token() upserts per device and writes both token columns.
    06-Aug-2026: save_device_token() populates contactidname/userprofileidname on the device row.
    15-Sep-2026: save_device_token() closes the open app-uninstall record; added HAS_DEVICE_SQL.
    14-Oct-2026: Added update_credsupdate() for POST /devices/credsupdate.
    21-Oct-2026: _fetch_person_device_rows() treats an empty profile contactid as no contact, not a shared one.
"""

import re
import time
from typing import Optional

from app.core.config import settings
from app.db.client import Database, with_retry
from app.core.logging import logger
from app.etown.devices.uninstall_utils import CLOSE_UNINSTALL_ON_REGISTER_SQL

_DEVICE_COLUMNS = """
                contactdevicesid,
                deviceguid,
                ccuserid,
                contactid,
                contactidname,
                userprofileid,
                userprofileidname,
                createdon,
                device_token,
                device_token_type,
                token_expires_at,
                last_used_at,
                voip_device_token,
                device_id,
                updatedon
"""


_RESOLVED_TOKEN = "COALESCE(NULLIF(device_token, ''), NULLIF(deviceguid, ''))"

_ROW_INSTANCE = f"NULLIF(split_part({_RESOLVED_TOKEN}, ':', 1), '')"

_IS_DEVICE_ROW = """(NULLIF(deviceguid, '') IS NOT NULL
                     OR NULLIF(device_id, '') IS NOT NULL
                     OR COALESCE(NULLIF(ccuserid, ''), '0') <> '0')"""

_CONTACT_NAME_SQL = """
    COALESCE(
        (SELECT NULLIF(c.firstname, '') FROM etown_contact c WHERE c.contactid = $1::text),
        (SELECT NULLIF(up.contactidname, '') FROM etown_userprofile up
          WHERE up.userprofileid = $2::text)
    )
"""

_PROFILE_NAME_SQL = """
    COALESCE(
        (SELECT COALESCE(NULLIF(up.name, ''), NULLIF(up.contactidname, ''))
           FROM etown_userprofile up WHERE up.userprofileid = $2::text),
        (SELECT NULLIF(c.firstname, '') FROM etown_contact c WHERE c.contactid = $1::text)
    )
"""


_MATCH_DEVICE_SQL = f"""
    SELECT contactdevicesid, contactid, userprofileid FROM (
        SELECT contactdevicesid, contactid, userprofileid, 1 AS rnk, 0 AS pref, createdon, updatedon
        FROM etown_contactdevices
        WHERE $5::text IS NOT NULL AND device_id = $5::text
        UNION ALL
        SELECT contactdevicesid, contactid, userprofileid, 2 AS rnk, 0 AS pref, createdon, updatedon
        FROM etown_contactdevices
        WHERE $6::text IS NOT NULL AND {_ROW_INSTANCE} = $6::text
          AND {_IS_DEVICE_ROW}
        UNION ALL
        SELECT contactdevicesid, contactid, userprofileid, 3 AS rnk, 0 AS pref, createdon, updatedon
        FROM etown_contactdevices
        WHERE $3::text IS NOT NULL
          AND (deviceguid = $3::text OR device_token = $3::text)
          AND {_IS_DEVICE_ROW}
        UNION ALL
        SELECT contactdevicesid, contactid, userprofileid, 4 AS rnk, 0 AS pref, createdon, updatedon
        FROM etown_contactdevices
        WHERE $4::text IS NOT NULL AND voip_device_token = $4::text
          AND {_IS_DEVICE_ROW}
        UNION ALL
        SELECT contactdevicesid, contactid, userprofileid, 5 AS rnk,
               CASE WHEN $4::text IS NOT NULL AND NULLIF(voip_device_token, '') IS NOT NULL
                    THEN 1 ELSE 0 END AS pref,
               createdon, updatedon
        FROM etown_contactdevices
        WHERE $3::text IS NULL AND $5::text IS NULL
          AND contactid = $1::text AND userprofileid = $2::text
          AND {_IS_DEVICE_ROW}
    ) m
    ORDER BY rnk, pref, updatedon DESC NULLS LAST, createdon DESC NULLS LAST, contactdevicesid DESC
    LIMIT 1
"""


_STEAL_TOKENS_SQL = """
    UPDATE etown_contactdevices
    SET deviceguid        = CASE WHEN deviceguid   = $2::text
                                   OR ($5::text IS NOT NULL
                                       AND NULLIF(split_part(deviceguid, ':', 1), '') = $5::text)
                                 THEN NULL ELSE deviceguid END,
        device_token      = CASE WHEN device_token = $2::text
                                   OR ($5::text IS NOT NULL
                                       AND NULLIF(split_part(device_token, ':', 1), '') = $5::text)
                                 THEN NULL ELSE device_token END,
        voip_device_token = CASE WHEN voip_device_token = $3::text THEN NULL ELSE voip_device_token END,
        device_id         = CASE WHEN device_id         = $4::text THEN NULL ELSE device_id         END,
        updatedon         = NOW(),
        modifieddate      = NOW()
    WHERE contactdevicesid <> COALESCE($1::text, '')
      AND (
            deviceguid        = $2::text
         OR device_token      = $2::text
         OR voip_device_token = $3::text
         OR device_id         = $4::text
         OR ($5::text IS NOT NULL
             AND (NULLIF(split_part(deviceguid, ':', 1), '')   = $5::text
               OR NULLIF(split_part(device_token, ':', 1), '') = $5::text))
      )
"""

_UPDATE_DEVICE_SQL = f"""
    UPDATE etown_contactdevices
    SET contactid         = COALESCE($1::text, contactid),
        contactidname     = COALESCE({_CONTACT_NAME_SQL}, contactidname),
        userprofileid     = COALESCE($2::text, userprofileid),
        userprofileidname = COALESCE({_PROFILE_NAME_SQL}, userprofileidname),
        deviceguid        = COALESCE($3::text, deviceguid),
        device_token      = COALESCE($3::text, device_token),
        device_token_type = CASE WHEN $3::text IS NOT NULL THEN 'fcm' ELSE device_token_type END,
        ccuserid          = COALESCE($4::text, ccuserid),
        voip_device_token = COALESCE($5::text, voip_device_token),
        device_id         = COALESCE($6::text, device_id),
        updatedon         = NOW(),
        modifieddate      = NOW()
    WHERE contactdevicesid = $7::text
    RETURNING {_DEVICE_COLUMNS}
"""

_INSERT_DEVICE_SQL = f"""
    INSERT INTO etown_contactdevices
        (contactdevicesid, contactid, contactidname, userprofileid, userprofileidname,
         deviceguid, device_token, device_token_type, ccuserid, voip_device_token, device_id,
         createdon, updatedon)
    VALUES
        (gen_random_uuid()::text, $1::text, {_CONTACT_NAME_SQL}, $2::text, {_PROFILE_NAME_SQL},
         $3::text, $3::text,
         CASE WHEN $3::text IS NOT NULL THEN 'fcm' END, $4::text, $5::text, $6::text, NOW(), NOW())
    RETURNING {_DEVICE_COLUMNS}
"""

_PRUNE_DEAD_SQL = """
    DELETE FROM etown_contactdevices
    WHERE contactid = $1::text
      AND userprofileid = $2::text
      AND contactdevicesid <> $3::text
      AND NULLIF(deviceguid, '') IS NULL
      AND NULLIF(device_token, '') IS NULL
      AND NULLIF(voip_device_token, '') IS NULL
      AND COALESCE(NULLIF(ccuserid, ''), '0') = '0'
      AND (NULLIF(device_id, '') IS NULL OR device_token_type = 'invalid')
"""

_CLEAR_SPENT_MIRRORS_SQL = """
    DELETE FROM etown_contactdevices
    WHERE contactid = $1::text
      AND contactdevicesid <> $2::text
      AND NULLIF(deviceguid, '') IS NULL
      AND NULLIF(device_id, '') IS NULL
      AND NULLIF(device_token, '') IS NULL
      AND NULLIF(voip_device_token, '') IS NULL
      AND COALESCE(NULLIF(ccuserid, ''), '0') = '0'
"""

_MIRROR_TO_PROFILES_SQL = """
    INSERT INTO etown_contactdevices
        (contactdevicesid, contactid, contactidname, userprofileid, userprofileidname,
         device_token, device_token_type, voip_device_token, createdon, updatedon)
    SELECT gen_random_uuid()::text, src.contactid,
           COALESCE(NULLIF(src.contactidname, ''), NULLIF(other.contactidname, '')),
           other.userprofileid,
           COALESCE(NULLIF(other.name, ''), NULLIF(other.contactidname, '')),
           COALESCE(NULLIF(src.device_token, ''), NULLIF(src.deviceguid, '')),
           'fcm', NULLIF(src.voip_device_token, ''), NOW(), NOW()
    FROM etown_contactdevices src
    JOIN etown_userprofile other
      ON other.contactid = src.contactid
     AND other.userprofileid IS DISTINCT FROM src.userprofileid
    WHERE src.contactdevicesid = $1::text
      AND COALESCE(NULLIF(src.device_token, ''), NULLIF(src.deviceguid, '')) IS NOT NULL
      AND NOT EXISTS (
            SELECT 1 FROM etown_contactdevices mine
            WHERE mine.userprofileid = other.userprofileid
              AND NULLIF(split_part(
                      COALESCE(NULLIF(mine.device_token, ''), NULLIF(mine.deviceguid, '')), ':', 1), '')
                = NULLIF(split_part(
                      COALESCE(NULLIF(src.device_token, ''), NULLIF(src.deviceguid, '')), ':', 1), '')
      )
"""

_FETCH_USER_DEVICES_SQL = f"""
    SELECT {_DEVICE_COLUMNS}
    FROM etown_contactdevices
    WHERE contactid = $1::text AND userprofileid = $2::text
    ORDER BY updatedon DESC NULLS LAST, createdon DESC NULLS LAST, contactdevicesid
"""

_FETCH_PERSON_DEVICES_SQL = f"""
    WITH target AS (
        SELECT NULLIF(contactid, '') AS contactid FROM etown_userprofile WHERE userprofileid = $1::text
    ),
    device_rows AS (
        SELECT * FROM etown_contactdevices WHERE userprofileid = $1::text
        UNION
        SELECT * FROM etown_contactdevices
        WHERE contactid = (SELECT contactid FROM target)
        UNION
        SELECT * FROM etown_contactdevices
        WHERE userprofileid IN (
            SELECT userprofileid FROM etown_userprofile
            WHERE contactid = (SELECT contactid FROM target)
        )
    )
    SELECT contactdevicesid,
           {_RESOLVED_TOKEN} AS token,
           NULLIF(voip_device_token, '') AS voip_token
    FROM device_rows
    ORDER BY updatedon DESC NULLS LAST, createdon DESC NULLS LAST, contactdevicesid
"""

HAS_DEVICE_SQL = """EXISTS (
         SELECT 1 FROM public.etown_contactdevices cd
         WHERE cd.contactid = {contact} OR cd.userprofileid = {profile}
       )"""

def _clean(value: Optional[str]) -> Optional[str]:
    """Return the trimmed value, or None when it is empty/whitespace."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def fcm_app_instance(token: Optional[str]) -> Optional[str]:
    """Return the app-instance id (the part before ':') of an FCM token, or None when empty."""
    if not token:
        return None
    return token.split(":", 1)[0] if ":" in token else token


async def _fetch_person_device_rows(userprofile_id: str) -> list[dict]:
    """Return every device row across the contact owning a userprofile, newest first.

    Raises:
        RuntimeError: If the database pool is not initialized or closed.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        rows = await con.fetch(_FETCH_PERSON_DEVICES_SQL, userprofile_id)
    return [dict(row) for row in rows]


@with_retry()
async def save_device_token(
    contact_id: str,
    userprofile_id: str,
    devicetoken: Optional[str],
    cc_user_id: Optional[str],
    voip_device_token: Optional[str],
    device_id: Optional[str] = None,
) -> list[dict]:
    """Upsert one device for a contact/userprofile pair, prune dead rows and mirror it to sibling profiles.

    Args:
        contact_id: Contact GUID.
        userprofile_id: Userprofile GUID.
        devicetoken: FCM token; None leaves the stored value unchanged.
        cc_user_id: Cloud Communications user id; None leaves it unchanged.
        voip_device_token: VoIP APNs token; None leaves it unchanged.
        device_id: Stable installation id, the preferred match key when supplied.

    Returns:
        Every device row of the pair after the upsert, newest first.

    Raises:
        RuntimeError: If the database pool is not initialised or closed.
    """
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialised or is closed.")

    devicetoken = _clean(devicetoken)
    cc_user_id = _clean(cc_user_id)
    voip_device_token = _clean(voip_device_token)
    device_id = _clean(device_id)
    instance_id = fcm_app_instance(devicetoken)

    acquire_start = time.perf_counter()
    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        logger.info(
            f"save_device_token - DB acquire: {time.perf_counter() - acquire_start:.4f}s "
            f"contact_id={contact_id} userprofile_id={userprofile_id} "
            f"device_id={device_id or '-'} has_fcm={bool(devicetoken)} has_voip={bool(voip_device_token)}"
        )

        async with con.transaction():
            await con.execute("SELECT pg_advisory_xact_lock(hashtext($1::text))", contact_id or "")

            match = await con.fetchrow(
                _MATCH_DEVICE_SQL,
                contact_id,
                userprofile_id,
                devicetoken,
                voip_device_token,
                device_id,
                instance_id,
            )
            target_id = match["contactdevicesid"] if match else None

            if devicetoken or voip_device_token or device_id:
                await con.execute(
                    _STEAL_TOKENS_SQL,
                    target_id,
                    devicetoken,
                    voip_device_token,
                    device_id,
                    instance_id,
                )

            if target_id:
                if match["contactid"] != contact_id or match["userprofileid"] != userprofile_id:
                    logger.info(
                        f"save_device_token - reassigning device {target_id} from "
                        f"contact={match['contactid']}/userprofile={match['userprofileid']} to "
                        f"contact={contact_id}/userprofile={userprofile_id}"
                    )
                row = await con.fetchrow(
                    _UPDATE_DEVICE_SQL,
                    contact_id,
                    userprofile_id,
                    devicetoken,
                    cc_user_id,
                    voip_device_token,
                    device_id,
                    target_id,
                )
            else:
                row = await con.fetchrow(
                    _INSERT_DEVICE_SQL,
                    contact_id,
                    userprofile_id,
                    devicetoken,
                    cc_user_id,
                    voip_device_token,
                    device_id,
                )

            mirrored = 0
            if row:
                await con.execute(
                    _PRUNE_DEAD_SQL, contact_id, userprofile_id, row["contactdevicesid"]
                )
                await con.execute(
                    _CLEAR_SPENT_MIRRORS_SQL, contact_id, row["contactdevicesid"]
                )
                status = await con.execute(
                    _MIRROR_TO_PROFILES_SQL, row["contactdevicesid"]
                )
                mirrored = int(status.rsplit(" ", 1)[-1] or 0)
                if getattr(settings, "UNINSTALL_DETECTION_ENABLED", False):
                    await con.execute(
                        CLOSE_UNINSTALL_ON_REGISTER_SQL,
                        device_id,
                        instance_id,
                        row["contactdevicesid"],
                    )

            records = await con.fetch(_FETCH_USER_DEVICES_SQL, contact_id, userprofile_id)

        logger.info(
            f"save_device_token - {'updated' if target_id else 'inserted'} device "
            f"contact_id={contact_id} userprofile_id={userprofile_id} devices={len(records)} "
            f"profile_mirrors={mirrored}"
        )
        return [dict(record) for record in records]


@with_retry()
async def get_device_tokens_by_userprofile_id(userprofile_id: str) -> list[str]:
    """Return one FCM token per app installation across the contact owning a userprofile, newest first."""
    if not userprofile_id:
        return []

    rows = await _fetch_person_device_rows(userprofile_id)

    tokens: list[str] = []
    seen: set[str] = set()
    for row in rows:
        token = row["token"]
        instance = fcm_app_instance(token)
        if not instance or instance in seen:
            continue
        seen.add(instance)
        tokens.append(token)

    if not tokens:
        logger.warning(f"No device token found for userprofile_id: {userprofile_id}")
    return tokens


@with_retry()
async def get_voip_device_tokens_by_userprofile_id(userprofile_id: str) -> list[str]:
    """Return the distinct VoIP APNs tokens across the contact owning a userprofile, newest first."""
    if not userprofile_id:
        return []

    rows = await _fetch_person_device_rows(userprofile_id)
    return list(dict.fromkeys(row["voip_token"] for row in rows if row["voip_token"]))


@with_retry()
async def get_device_push_targets_by_userprofile_id(userprofile_id: str) -> list[dict]:
    """Return contactdevicesid, fcm_token and voip_token for each distinct device of a userprofile's contact."""
    if not userprofile_id:
        return []

    rows = await _fetch_person_device_rows(userprofile_id)

    targets: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = fcm_app_instance(row["token"]) or row["voip_token"]
        if not key or key in seen:
            continue
        seen.add(key)
        targets.append(
            {
                "contactdevicesid": row["contactdevicesid"],
                "fcm_token": row["token"],
                "voip_token": row["voip_token"],
            }
        )
    return targets


@with_retry()
async def get_device_tokens_by_contact_id(contact_id: str) -> list[str]:
    """Return one FCM token per app installation of a contact, newest device first.

    Raises:
        RuntimeError: If the database pool is not initialized or closed.
    """
    if not contact_id:
        return []
    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")

    query = f"""
        SELECT {_RESOLVED_TOKEN} AS token
        FROM etown_contactdevices
        WHERE contactid = $1
          AND {_RESOLVED_TOKEN} IS NOT NULL
        ORDER BY updatedon DESC NULLS LAST, createdon DESC NULLS LAST, contactdevicesid
    """
    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        rows = await con.fetch(query, contact_id)

    tokens: list[str] = []
    seen: set[str] = set()
    for row in rows:
        instance = fcm_app_instance(row["token"])
        if not instance or instance in seen:
            continue
        seen.add(instance)
        tokens.append(row["token"])
    return tokens


async def update_credsupdate(
    action_mode: str,
    credsupdate: bool,
    devicetoken: Optional[str] = None,
    contactid: Optional[str] = None,
    mobilenumber: Optional[str] = None,
) -> int:
    """Set credsupdate on one contact's rows for a device token, or on every etown_contactdevices row in powerapps mode.

    Args:
        action_mode: 'frontend' for one token of one contact, 'powerapps' for every row.
        credsupdate: Value to write.
        devicetoken: Token matched against device_token and deviceguid in frontend mode.
        contactid: Contact owning the token in frontend mode.
        mobilenumber: Mobile number of that contact in frontend mode; its last ten digits are matched.

    Returns:
        Rows matched in frontend mode, rows changed in powerapps mode.
    """
    if action_mode == "powerapps":
        status = await Database.execute(
            """
            UPDATE etown_contactdevices
               SET credsupdate = $1
             WHERE credsupdate IS DISTINCT FROM $1
            """,
            credsupdate,
        )
        return int(status.split()[-1])

    params: list = [credsupdate, _clean(devicetoken)]
    contact_filters: list[str] = []
    profile_filters: list[str] = []
    if _clean(contactid):
        params.append(_clean(contactid))
        contact_filters.append(f"c.contactid = ${len(params)}")
        profile_filters.append(f"u.contactid = ${len(params)}")
    digits = re.sub(r"\D", "", mobilenumber or "")[-10:]
    if digits:
        params.append(digits)
        contact_filters.append(f"c.mobilephone = ${len(params)}")
        profile_filters.append(f"u.mobilenumber = ${len(params)}")
    if not params[1] or not contact_filters:
        return 0

    status = await Database.execute(
        f"""
        WITH people AS (
            SELECT c.contactid FROM etown_contact c
             WHERE {' AND '.join(contact_filters)}
            UNION
            SELECT u.contactid FROM etown_userprofile u
             WHERE {' AND '.join(profile_filters)}
        )
        UPDATE etown_contactdevices d
           SET credsupdate = $1
         WHERE (d.device_token = $2 OR d.deviceguid = $2)
           AND (d.contactid IN (SELECT contactid FROM people)
                OR d.userprofileid IN (
                    SELECT up.userprofileid FROM etown_userprofile up
                     WHERE up.contactid IN (SELECT contactid FROM people)
                ))
        """,
        *params,
    )
    return int(status.split()[-1])
