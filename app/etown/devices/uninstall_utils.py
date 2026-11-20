"""App Uninstall Utilities - Postgres logic that records, reconciles and reads dead-token uninstall signals.

Domain layer for app uninstall detection: records dead FCM tokens from live pushes, daily silent probes and the first-sweep baseline into etown_app_uninstalls, reconciles reinstalls, selects probe candidates from etown_contactdevices and reads the log for the API.

Features:
    - Sources: live_push and probe are uninstalls; baseline rows are kept but excluded from reads and etown_app_uninstalled_users
    - Records a dead device from the token alone, resolving the owner from etown_contactdevices
    - Baseline source for the first sweep, kept on record but excluded from every uninstall read
    - Stores a token PREFIX, never the registration token, which is a credential
    - One open uninstall per device identity, upserted on repeat detections
    - Reinstall reconciliation keyed on device_id / FCM app instance
    - Probe candidate selection that serves never-probed devices first
    - Feature-flagged: every writer is a no-op until UNINSTALL_DETECTION_ENABLED is on

Classes:
    No classes defined (module exposes SQL constants and function-based utilities)

Functions:
    record_device_uninstall(): Persist one dead-token detection
    fetch_probe_candidates(): Device tokens due for a silent probe, de-duplicated per handset
    mark_devices_probed(): Stamp last_probed_at on every row carrying a probed token
    fetch_uninstalls(): Read the uninstall log for the API

Dependencies:
    - app.db.client: Database connection pool
    - app.core.config: UNINSTALL_DETECTION_ENABLED
    - app.core.logging: Centralized logging infrastructure
    - public.etown_app_uninstalls: Where detections are recorded
    - public.etown_contactdevices: Where the owner and the probe bookkeeping live

Updates:
    15-Sep-2026: Created. Dead-token uninstall records, reinstall reconciliation, baseline source.
"""

from typing import Any, Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database

_RESOLVED_TOKEN = "COALESCE(NULLIF(device_token, ''), NULLIF(deviceguid, ''))"

_RECORD_UNINSTALL_SQL = f"""
INSERT INTO etown_app_uninstalls (
    contactdevicesid, contactid, userprofileid, contactidname,
    device_id, app_instance_id, device_token_prefix, platform,
    detection_source, fcm_status_code, fcm_error, last_seen_at
)
SELECT d.contactdevicesid,
       NULLIF(d.contactid, ''), NULLIF(d.userprofileid, ''), NULLIF(d.contactidname, ''),
       NULLIF(d.device_id, ''),
       NULLIF(split_part({_RESOLVED_TOKEN}, ':', 1), ''),
       left($1::text, 12),
       CASE WHEN lower(d.device_id) LIKE 'android%' THEN 'android'
            WHEN lower(d.device_id) LIKE 'ios%'     THEN 'ios'
            WHEN NULLIF(d.voip_device_token, '') IS NOT NULL THEN 'ios'
            ELSE 'unknown' END,
       $2::text, $3::int, left($4::text, 2000),
       COALESCE(d.last_used_at, d.updatedon::timestamptz, d.createdon::timestamptz)
  FROM etown_contactdevices d
 WHERE (d.device_token = $1::text OR d.deviceguid = $1::text)
   AND (NULLIF(d.device_id, '') IS NOT NULL
        OR NULLIF(d.deviceguid, '') IS NOT NULL
        OR COALESCE(NULLIF(d.ccuserid, ''), '0') <> '0')
 ORDER BY (NULLIF(d.device_id, '') IS NULL), d.updatedon DESC NULLS LAST
 LIMIT 1
ON CONFLICT (device_identity) WHERE reinstalled_at IS NULL
DO UPDATE SET detected_at      = NOW(),
              detection_source = EXCLUDED.detection_source,
              fcm_status_code  = EXCLUDED.fcm_status_code,
              fcm_error        = EXCLUDED.fcm_error,
              updated_at       = NOW()
RETURNING uninstall_id, contactid
"""

CLOSE_UNINSTALL_ON_REGISTER_SQL = """
UPDATE etown_app_uninstalls
   SET reinstalled_at = NOW(),
       updated_at     = NOW()
 WHERE reinstalled_at IS NULL
   AND (   ($1::text IS NOT NULL AND device_id        = $1::text)
        OR ($2::text IS NOT NULL AND app_instance_id  = $2::text)
        OR ($3::text IS NOT NULL AND contactdevicesid = $3::text))
"""


_PROBE_CANDIDATES_SQL = f"""
SELECT contactdevicesid, {_RESOLVED_TOKEN} AS token
  FROM etown_contactdevices
 WHERE {_RESOLVED_TOKEN} IS NOT NULL
   AND COALESCE(device_token_type, 'fcm') <> 'invalid'
   AND (last_probed_at IS NULL OR last_probed_at < $1::timestamptz)
 ORDER BY last_probed_at ASC NULLS FIRST
 LIMIT $2::int
"""

_MARK_PROBED_SQL = f"""
UPDATE etown_contactdevices
   SET last_probed_at = NOW()
 WHERE {_RESOLVED_TOKEN} = ANY($1::text[])
"""


async def record_device_uninstall(
    device_token: str,
    *,
    detection_source: str = "live_push",
    status_code: Optional[int] = None,
    error_text: Optional[str] = None,
) -> bool:
    """Record an uninstall for the device owning an FCM-rejected token, returning True on write; never raises.

    Args:
        device_token: The FCM registration token FCM rejected.
        detection_source: 'live_push' for a real notification, 'probe' for the detection worker.
        status_code: HTTP status FCM answered with, stored as evidence.
        error_text: FCM's error body, truncated on write.

    Returns:
        True when a row was written or refreshed; False if disabled, unmatched or failed.
    """
    if not device_token or not getattr(settings, "UNINSTALL_DETECTION_ENABLED", False):
        return False

    try:
        row = await Database.fetchrow(
            _RECORD_UNINSTALL_SQL,
            device_token,
            detection_source,
            status_code,
            error_text,
        )
    except Exception as exc:
        logger.error(
            "Uninstall detection: could not record dead token (prefix=%s): %s",
            device_token[:10], exc, exc_info=True,
        )
        return False

    if not row:
        logger.info(
            "Uninstall detection: dead token matched no device row (prefix=%s)", device_token[:10]
        )
        return False

    logger.info(
        "Uninstall detection: recorded %s uninstall | contact=%s token_prefix=%s status=%s",
        detection_source, row["contactid"] or "-", device_token[:10], status_code,
    )
    return True


async def fetch_probe_candidates(*, probed_before, limit: int) -> list[dict]:
    """Return device tokens due for a silent probe, de-duplicated to one entry per FCM app instance.

    Args:
        probed_before: Absolute cutoff; only devices last probed before this, or never.
        limit: Maximum rows to read before de-duplication.

    Returns:
        List of dicts with contactdevicesid and token.
    """
    from app.etown.devices.devices_utils import fcm_app_instance

    rows = await Database.fetch_once(_PROBE_CANDIDATES_SQL, probed_before, limit)

    candidates: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        instance = fcm_app_instance(row["token"])
        if not instance or instance in seen:
            continue
        seen.add(instance)
        candidates.append({"contactdevicesid": row["contactdevicesid"], "token": row["token"]})
    return candidates


async def mark_devices_probed(tokens: list[str]) -> None:
    """Stamp ``last_probed_at`` on every device row carrying one of *tokens*."""
    if not tokens:
        return
    await Database.execute(_MARK_PROBED_SQL, tokens)


async def fetch_uninstalls(
    *,
    contact_id: Optional[str] = None,
    include_reinstalled: bool = False,
    include_baseline: bool = False,
    since_days: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return a page of the uninstall log, newest detection first.

    Args:
        contact_id: Restrict to one contact's devices.
        include_reinstalled: Keep rows that have since re-registered.
        include_baseline: Keep rows from the first baseline sweep.
        since_days: Only detections within this many days.
        limit: Page size.
        offset: Page offset.

    Returns:
        List of dicts, one per device detection.
    """
    query = """
        SELECT uninstall_id, contactdevicesid, contactid, userprofileid, contactidname,
               device_id, app_instance_id, device_token_prefix, platform,
               detected_at, detection_source, fcm_status_code, last_seen_at, reinstalled_at
          FROM etown_app_uninstalls
         WHERE ($1::text IS NULL OR contactid = $1::text)
           AND ($2::bool OR reinstalled_at IS NULL)
           AND ($3::bool OR detection_source <> 'baseline')
           AND ($4::int IS NULL OR detected_at >= NOW() - make_interval(days => $4::int))
         ORDER BY detected_at DESC
         LIMIT $5::int OFFSET $6::int
    """
    rows = await Database.fetch_once(
        query, contact_id, include_reinstalled, include_baseline, since_days, limit, offset
    )
    return [dict(row) for row in rows]
