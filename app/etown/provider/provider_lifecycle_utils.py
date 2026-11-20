"""Provider Lifecycle Utilities - Postgres soft deactivation and reactivation of a single business.

Domain layer behind the provider deactivate and reactivate routes: flags one provider's rows in etown_provider, etown_providerservices, etown_providerservices_copy, etown_offers, etown_offerings and etown_etownprovider, leaving the owner's contact, profile and other businesses untouched, and records the plan transition.

Features:
    - Single Statement: one data-modifying CTE checks eligibility, runs all six updates and projects
    - Shared Scope: the provider's ids are resolved once in a `scope` CTE and reused
    - Consistent Timestamp: now() is transaction-stable, so all six tables get one IST stamp
    - Idempotent: a repeat request flags nothing and reports found=False
    - Plan Transition Recorded: the drop to free and the return are written to etown_plan_change
    - Soft Only: nothing is physically deleted; deactivation also resets reactivation to 'false'
    - One Flag, Two Effects: retired userprofileid and providerid hide it; the effective plan drops to free
    - Owner Deactivated: an already-flagged business reports found=False; reactivating it leaves the account hidden

Classes:
    No classes defined (module exposes async functions for per-provider lifecycle transitions)

Functions:
    _build_provider_flag_sql(): Compose the single-statement flag-and-return SQL for one mode
    _flag_provider(): Run one mode's statement, record the plan transition, normalise the row
    deactivate_provider_in_db(): Flag one business and its owned rows as deactivated
    reactivate_provider_in_db(): Clear that flag and bring the business back

Dependencies:
    - app.db.client: Database class with async connection pool
    - app.core.logging: Centralized application logging
    - app.etown.plans.plans_utils: records the transition and drops the cached plan

Requires:
    - The 6 lifecycle columns on all six tables

Updates:
    01-Sep-2026: Created. Per-business deactivate/reactivate; free tier and hidden listings both follow from the one flag.
"""

from typing import Any

from app.core.logging import logger
from app.db.client import Database
from app.etown.plans import plans_utils

_PROVIDER_FLAGGED_TABLES = {
    "etown_provider":               "t.providerid = s.pid",
    "etown_providerservices":       "t.providerid = s.pid OR t.userprofileid = s.upid",
    "etown_providerservices_copy":  "t.providerid = s.pid OR t.userprofileid = s.upid",
    "etown_offers":                 "t.userprofileid = s.upid",
    "etown_offerings":              "t.userprofileid = s.upid",
    "etown_etownprovider":          "t.providerid = s.pid",
}

_IST_NOW = "now() AT TIME ZONE 'Asia/Kolkata'"


def _build_provider_flag_sql(set_sql: str, guard_sql: str) -> str:
    """Build one SQL statement that applies a SET list to every flagged provider table via UPDATE CTEs.

    Args:
        set_sql: SET list applied to each flagged table.
        guard_sql: Extra predicate on the provider row deciding eligibility for this mode.

    Returns:
        SQL taking $1 providerid, returning found, ids, businessname and rows_flagged.
    """
    aliases = [f"f{i:02d}" for i in range(len(_PROVIDER_FLAGGED_TABLES))]
    updates = ",\n        ".join(
        f"{alias} AS (UPDATE {table} t SET {set_sql} FROM scope s "
        f"WHERE {predicate} RETURNING 1)"
        for alias, (table, predicate) in zip(aliases, _PROVIDER_FLAGGED_TABLES.items())
    )
    counts = "\n                     + ".join(
        f"(SELECT count(*) FROM {alias})" for alias in aliases
    )
    return f"""
        WITH scope AS (
            SELECT p.providerid     AS pid,
                   p.userprofileid  AS upid,
                   p.contactid      AS cid,
                   p.businessname   AS businessname
              FROM etown_provider p
             WHERE p.providerid = $1
               AND lower(COALESCE(p.deleted, 'false')) <> 'true'
               AND {guard_sql}
        ),
        {updates}
        SELECT EXISTS (SELECT 1 FROM scope)      AS found,
               (SELECT pid FROM scope)           AS providerid,
               (SELECT upid FROM scope)          AS userprofileid,
               (SELECT cid FROM scope)           AS contactid,
               (SELECT businessname FROM scope)  AS businessname,
               ({counts})::int                   AS rows_flagged
    """


_DEACTIVATE_SQL = _build_provider_flag_sql(
    set_sql=(
        f"deactivated = 'true', deactivationdate = {_IST_NOW}, "
        f"reactivation = 'false'"
    ),
    guard_sql="lower(COALESCE(p.deactivated, 'false')) <> 'true'",
)

_REACTIVATE_SQL = _build_provider_flag_sql(
    set_sql=(
        f"reactivation = 'true', reactivationdate = {_IST_NOW}, "
        f"deactivated = 'false'"
    ),
    guard_sql="lower(COALESCE(p.deactivated, 'false')) = 'true'",
)


async def _flag_provider(
    sql: str,
    provider_id: str,
    mode: str,
    log_prefix: str,
) -> dict[str, Any]:
    """Run a lifecycle flag statement, record the plan event best effort, and return the normalised result.

    Args:
        sql: _DEACTIVATE_SQL or _REACTIVATE_SQL.
        provider_id: The providerid (PK of etown_provider) to flag.
        mode: "deactivate" or "reactivate", passed through to the plan history.
        log_prefix: Log label identifying the mode.

    Returns:
        Dict with found, providerid, userprofileid, contactid, businessname and rows_flagged.
    """
    logger.info("%s | START | provider_id=%s", log_prefix, provider_id)

    row = await Database.fetchrow(sql, provider_id)
    found = bool(row and row["found"])
    result: dict[str, Any] = {
        "found": found,
        "providerid": row["providerid"] if row else None,
        "userprofileid": row["userprofileid"] if row else None,
        "contactid": row["contactid"] if row else None,
        "businessname": row["businessname"] if row else None,
        "rows_flagged": (row["rows_flagged"] or 0) if row else 0,
    }

    if not found:
        logger.info(
            "%s | NOT_ELIGIBLE | provider_id=%s | unknown, deleted, or already in that state",
            log_prefix, provider_id,
        )
        return result

    if result["userprofileid"]:
        try:
            await plans_utils.record_plan_lifecycle_event(
                mode,
                userprofile_id=result["userprofileid"],
                changed_by=f"provider_{mode}",
                reason=f"provider {mode}d",
            )
        except Exception as exc:
            logger.error(
                "%s | PLAN_RECORD_FAILED | provider_id=%s | %s",
                log_prefix, provider_id, exc, exc_info=True,
            )

    logger.info(
        "%s | DONE | provider_id=%s | business=%s | rows=%d",
        log_prefix, provider_id, result["businessname"], result["rows_flagged"],
    )
    return result


async def deactivate_provider_in_db(provider_id: str) -> dict[str, Any]:
    """Mark one active business and its owned rows deactivated, returning the _flag_provider result dict."""
    return await _flag_provider(
        _DEACTIVATE_SQL, provider_id, "deactivate", "DB_PROVIDER_DEACTIVATE"
    )


async def reactivate_provider_in_db(provider_id: str) -> dict[str, Any]:
    """Reactivate one deactivated business and its owned rows, returning the _flag_provider result dict."""
    return await _flag_provider(
        _REACTIVATE_SQL, provider_id, "reactivate", "DB_PROVIDER_REACTIVATE"
    )
