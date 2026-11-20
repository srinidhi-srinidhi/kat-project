"""Account Purge Worker - flags accounts deactivated for over 90 days and never reactivated as deleted.

Background asyncio loop started from the FastAPI lifespan that finds stale deactivations in batches, soft-deletes each contact through delete_account_from_db() exactly as a manual deletion does, and mirrors the flags to Dataverse; nothing is physically removed.

Features:
    - 90-Day Window: Only deactivations older than PURGE_AFTER_DAYS are promoted
    - IST-Consistent Clock: compares against naive-IST deactivationdate using an IST clock
    - Reactivation-Aware: accounts reactivated within the window are skipped
    - One Definition: flagging delegates to delete_account_from_db(), same 41 tables as a manual delete
    - Dataverse Mirror: each promotion stamps the same flags on the contact's CRM records
    - Per-Contact Atomicity: one statement per contact; unreached contacts are due next cycle
    - Idempotent: already-deleted contacts are excluded, so a re-run finds nothing
    - Batched: LIMIT PURGE_BATCH_SIZE per cycle

Classes:
    No classes defined (module exposes background-worker functions and the purge SQL query)

Functions:
    run_purge_job(): Execute one purge cycle and return the contacts promoted
    _purge_loop(): Background loop calling run_purge_job() every PURGE_INTERVAL_SECONDS when enabled
    start_account_purge_worker(): Start the background purge worker task
    stop_account_purge_worker(): Stop and cancel the running purge worker task

Dependencies:
    - asyncio: Background task scheduling and sleep between cycles
    - app.core.config: settings.ACCOUNT_PURGE_AFTER_DAYS and the ACCOUNT_PURGE_ENABLED kill switch
    - app.core.logging: Centralized logging for purge operations
    - app.db.client: Async PostgreSQL database access
    - app.etown.user.account_deletion_utils: delete_account_from_db(), the shared deletion flagging
    - app.etown.user.account_lifecycle_syncing: the Dataverse half of the same promotion

Requires:
    - scripts/2026_08_account_display_name_function.sql (STEP 1) for the 6 lifecycle columns
    - scripts/2026_08_account_purge_index.sql for the partial index this query needs

Updates:
    12-Aug-2026: Initial creation - 90-day deactivated-to-deleted promotion
    13-Aug-2026: Also flags etown_providerservices and its copy; uses per-contact delete_account_from_db().
    19-Aug-2026: Each promotion mirrors its flags to Dataverse.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.user.account_deletion_utils import delete_account_from_db
from app.etown.user.account_lifecycle_syncing import (
    MODE_DELETE,
    sync_account_lifecycle_to_dataverse,
)

PURGE_AFTER_DAYS = settings.ACCOUNT_PURGE_AFTER_DAYS

PURGE_INTERVAL_SECONDS = 3600

PURGE_BATCH_SIZE = 500

_purge_task: asyncio.Task | None = None

_DUE_SQL = f"""
    SELECT contactid, email, firstname, mobilephone
      FROM etown_contact
     WHERE deactivated = 'true'
       AND reactivation IS DISTINCT FROM 'true'
       AND deleted IS DISTINCT FROM 'true'
       AND deactivationdate IS NOT NULL
       AND deactivationdate
             <= (now() AT TIME ZONE 'Asia/Kolkata') - INTERVAL '{PURGE_AFTER_DAYS} days'
     ORDER BY deactivationdate
     LIMIT {PURGE_BATCH_SIZE}
"""


async def run_purge_job() -> list[dict[str, Any]]:
    """Delete each due deactivated account one by one, mirror it to Dataverse, and skip failures.

    Returns:
        One {"contact_id", "email", "firstname"} dict per promoted contact; empty when none were due.
    """
    due = await Database.fetch(_DUE_SQL)

    promoted: list[dict[str, Any]] = []
    for row in due:
        contact_id = row["contactid"]
        try:
            result = await delete_account_from_db(contact_id, row["mobilephone"])
        except Exception as exc:
            logger.error(
                "ACCOUNT_PURGE | FLAG_FAILED | contact_id=%s | %s",
                contact_id, exc, exc_info=True,
            )
            continue

        if not result.get("found"):
            logger.info(
                "ACCOUNT_PURGE | SKIPPED | contact_id=%s | no longer eligible", contact_id
            )
            continue

        try:
            await sync_account_lifecycle_to_dataverse(
                contact_id, MODE_DELETE, row["mobilephone"]
            )
        except Exception as exc:
            logger.error(
                "ACCOUNT_PURGE | DATAVERSE_SYNC_FAILED | contact_id=%s | %s",
                contact_id, exc, exc_info=True,
            )

        promoted.append({
            "contact_id": contact_id,
            "email": row["email"],
            "firstname": row["firstname"],
        })

    return promoted


async def _purge_loop() -> None:
    """Run the purge job every interval while ACCOUNT_PURGE_ENABLED is on, logging disable and batch-limit states."""
    disabled_logged = False
    while True:
        try:
            if not settings.ACCOUNT_PURGE_ENABLED:
                if not disabled_logged:
                    logger.warning(
                        "ACCOUNT_PURGE | disabled via ACCOUNT_PURGE_ENABLED - no accounts "
                        "will be promoted to deleted while this is off"
                    )
                    disabled_logged = True
                await asyncio.sleep(PURGE_INTERVAL_SECONDS)
                continue
            if disabled_logged:
                logger.info("ACCOUNT_PURGE | re-enabled, resuming")
                disabled_logged = False

            purged = await run_purge_job()
            if purged:
                logger.info(
                    "ACCOUNT_PURGE | promoted %d deactivated account(s) to deleted "
                    "after %d days | contact_ids=%s",
                    len(purged), PURGE_AFTER_DAYS,
                    ",".join(p["contact_id"] for p in purged),
                )
                if len(purged) == PURGE_BATCH_SIZE:
                    logger.warning(
                        "ACCOUNT_PURGE | batch limit %d reached, more may be due; "
                        "the remainder drains on the next cycle",
                        PURGE_BATCH_SIZE,
                    )
        except Exception as exc:
            logger.error("ACCOUNT_PURGE | worker error: %s", exc, exc_info=True)
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)


async def start_account_purge_worker() -> None:
    """Start the account purge background worker task; the loop itself honours the feature flag."""
    global _purge_task
    _purge_task = asyncio.create_task(_purge_loop())
    logger.info(
        "Account purge background worker started (enabled=%s, after %d days, interval: %ds)",
        settings.ACCOUNT_PURGE_ENABLED, PURGE_AFTER_DAYS, PURGE_INTERVAL_SECONDS,
    )


async def stop_account_purge_worker() -> None:
    """Cancel and await the account purge background worker task."""
    global _purge_task
    if _purge_task and not _purge_task.done():
        _purge_task.cancel()
        try:
            await _purge_task
        except asyncio.CancelledError:
            logger.debug("account purge worker cancelled during shutdown")
    _purge_task = None
    logger.info("Account purge background worker stopped")
