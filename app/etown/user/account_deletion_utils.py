"""Account Lifecycle Utilities - soft delete, deactivate and reactivate a contact in Postgres.

Domain layer behind /api/users/delete_account and the account-purge worker: flags a contact and its rows across the 43 tables in _FLAGGED_TABLES in one statement, never physically deleting anything, then retires sessions on delete and records the plan transition.

Features:
    - Soft Only: rows are stamped, never physically deleted, so every transition is reversible
    - Three Modes: delete sets deleted, deactivate sets deactivated, reactivate sets reactivation and clears deactivated
    - Shared Rows Excluded: the two-party tables in _DERIVED_TABLES carry no flag and derive from etown_retired_identity at read time
    - Single Statement: one data-modifying CTE checks liveness, runs all 43 updates and returns the result
    - Consistent Timestamp: now() is transaction-stable, so every table gets the identical IST stamp
    - Shared Scope: the contact's userprofile/member/provider ids gathered once in a `scope` CTE
    - Parameterized: contact_id is bound, never interpolated (injection-safe)
    - Idempotent: a repeat request flags nothing and reports found=False
    - Session Retirement: delete revokes refresh tokens and burns open challenges
    - Plan Transition: every mode records the subscription change; delete closes it as expired
    - Contact Return: returns email and firstname for the confirmation email step

Classes:
    No classes defined (module exposes async functions for flag-based account lifecycle transitions)

Functions:
    _build_flag_sql(): Compose the single-statement flag-and-return SQL for one lifecycle mode
    _record_plan_transition(): Record the transition against the contact's subscriptions
    warn_if_hard_delete_cascade_installed(): Log loudly if the retired hard-delete cascade functions are still installed
    _flag_account(): Run a lifecycle flag statement and normalise its single result row
    delete_account_from_db(): Flag a contact and its role rows as deleted
    deactivate_account_in_db(): Flag a contact and its role rows as deactivated
    reactivate_account_in_db(): Bring a deactivated contact and all its flagged rows back

Dependencies:
    - app.db.client: Database class with async connection pool
    - app.core.logging: Centralized application logging
    - app.etown.auth.auth_utils: refresh-token and challenge retirement on delete
    - app.etown.plans.plans_utils: record_plan_lifecycle_event() for the subscription transition

Requires:
    - Lifecycle columns on identity tables: scripts/2026_08_account_lifecycle_columns.sql
    - Lifecycle columns on the other tables: scripts/add_deleted_column_account_deletion_tables.sql
    - Columns kept: scripts/2026_08_account_drop_shared_lifecycle_columns.sql is withdrawn and aborts
    - Cascade functions dropped: scripts/2026_08_account_drop_cascade_functions.sql
    - etown_retired_identity view for _DERIVED_TABLES: scripts/2026_08_account_retired_identity_view.sql

Updates:
    06-Jul-2026: Created for the account deletion migration, using one connection and transaction.
    09-Jul-2026: The 57-statement cascade replaced by one delete_account_cascade() call.
    12-Aug-2026: Added deactivate_account_in_db(); dropped the cascade functions for identity-table flags.
    13-Aug-2026: Flagging extended to all 41 lifecycle tables, including etown_providerservices and its copy.
    19-Aug-2026: Dataverse mirrors these flags again via account_lifecycle_syncing.py.
    01-Sep-2026: All three modes record the subscription transition; delete returns providerids to mirror.
    07-Sep-2026: etown_memory_settings and etown_memory_decisions join _FLAGGED_TABLES.
"""

from typing import Any, Optional

from app.core.logging import logger
from app.db.client import Database
from app.etown.auth import auth_utils
from app.etown.plans import plans_utils

_FLAGGED_TABLES = {
    "etown_call_events":       "t.user_id = ANY(s.upids)",
    "etown_call_participants": "t.user_id = ANY(s.upids)",
    "etown_call_tokens":       "t.user_id = ANY(s.upids)",
    "etown_call_sessions":     "t.initiator_user_id = ANY(s.upids) "
                               "OR t.target_user_id = ANY(s.upids)",

    "etown_feedcomments":              "t.commentedbycontactid = s.contactid "
                                       "OR t.commentedbyuserprofileid = ANY(s.upids)",
    "etown_feedlikes":                 "t.likedbycontactid = s.contactid "
                                       "OR t.likesbyuserprofileid = ANY(s.upids)",
    "etown_feed":                      "t.contactid = s.contactid "
                                       "OR t.userprofileid = ANY(s.upids)",
    "etown_facebook_posts":            "t.contactid = s.contactid "
                                       "OR t.userprofileid = ANY(s.upids)",
    "etown_instagram_posts":           "t.contactid = s.contactid "
                                       "OR t.userprofileid = ANY(s.upids)",
    "etown_social_insights_snapshots": "t.contactid = s.contactid "
                                       "OR t.userprofileid = ANY(s.upids)",

    "etown_collectionitems": "t.usercontactid = s.contactid "
                             "OR t.useruserprofileid = ANY(s.upids) "
                             "OR t.providerid = ANY(s.pids)",
    "etown_collections":     "t.usercontactid = s.contactid "
                             "OR t.useruserprofileid = ANY(s.upids)",

    "etown_activeusers": "t.contactid = s.contactid "
                         "OR (s.mobile IS NOT NULL AND t.mobilenumber = s.mobile)",

    "etown_chatmessages": "t.contactid = s.contactid OR t.userprofileid = ANY(s.upids) "
                          "OR t.memberid = ANY(s.mids) OR t.providerid = ANY(s.pids)",

    "etown_providerservices_copy": "t.contactid = s.contactid "
                                   "OR t.userprofileid = ANY(s.upids) "
                                   "OR t.providerid = ANY(s.pids)",
    "etown_providerservices":      "t.contactid = s.contactid "
                                   "OR t.userprofileid = ANY(s.upids) "
                                   "OR t.providerid = ANY(s.pids)",
    "etown_providerservicesadf":   "t.contact_id = s.contactid "
                                   "OR t.user_profile_id = ANY(s.upids)",
    "etown_etownprovider":         "t.providerid = ANY(s.pids)",
    "etown_provider_details":      "t.providerid = ANY(s.pids)",
    "etown_ratings":               "t.providercontactid = s.contactid "
                                   "OR t.provideruserprofileid = ANY(s.upids)",

    "etown_offers":               "t.contactid = s.contactid "
                                  "OR t.userprofileid = ANY(s.upids) "
                                  "OR t.vieweduserprofileid = ANY(s.upids)",
    "etown_offer_views":          "t.userprofileid = ANY(s.upids)",
    "etown_offerings":            "t.userprofileid = ANY(s.upids)",
    "etown_favouritetown":        "t.usercontactid = s.contactid "
                                  "OR t.useruserprofileid = ANY(s.upids)",
    "etown_town_view_tracking":   "t.userprofileid = ANY(s.upids)",
    "etown_groups":               "t.user_contactid = s.contactid "
                                  "OR t.user_userprofileid = ANY(s.upids)",
    "etown_groupitems":           "t.user_contactid = s.contactid "
                                  "OR t.userprofile_id = ANY(s.upids) "
                                  "OR t.user_userprofileid = ANY(s.upids)",
    "etown_facebookconnections":  "t.contactid = s.contactid "
                                  "OR t.userprofileid = ANY(s.upids)",
    "etown_instagramconnections": "t.contactid = s.contactid "
                                  "OR t.userprofileid = ANY(s.upids)",
    "etown_mem0_memories":        "t.userprofileid = ANY(s.upids)",
    "etown_memory_settings":       "t.user_id = ANY(s.upids)",
    "etown_memory_decisions":      "t.user_id = ANY(s.upids)",
    "etown_application_logs":     "t.contact_id = s.contactid "
                                  "OR t.userprofile_id = ANY(s.upids)",
    "etown_connectycubedetails":  "t.contactid = s.contactid "
                                  "OR t.userprofileid = ANY(s.upids) "
                                  "OR t.memberprofile = ANY(s.mids) "
                                  "OR t.providerprofile = ANY(s.pids)",
    "etown_contactdevices":       "t.contactid = s.contactid "
                                  "OR t.userprofileid = ANY(s.upids)",
    "etown_userlocationdetails":  "t.contactid = s.contactid",
    "etown_mobileverification":   "s.mobile IS NOT NULL AND t.mobilenumber = s.mobile",
    "etown_franchise":            "(s.mobile IS NOT NULL AND t.phone = s.mobile) "
                                  "OR (s.email IS NOT NULL AND t.email = s.email)",
    "etowntitlegenerationlogs":   "t.contact_id = s.contactid "
                                  "OR t.user_profile_id = ANY(s.upids)",

    "etown_member":      "t.contactid = s.contactid OR t.userprofileid = ANY(s.upids)",
    "etown_provider":    "t.contactid = s.contactid OR t.userprofileid = ANY(s.upids)",
    "etown_userprofile": "t.contactid = s.contactid",
    "etown_contact":     "t.contactid = s.contactid",
}

_DERIVED_TABLES = (
    "etown_messages", "etown_conversations", "etown_chathistory", "etown_notifications",
    "etown_invitation", "etown_memberrelation", "etown_providerrelation",
    "etown_allrequests", "etown_requestservicelists", "etown_provider_views",
    "etown_subscriptions", "etown_townprofile",
)

_IST_NOW = "now() AT TIME ZONE 'Asia/Kolkata'"


def _build_flag_sql(set_sql: str, guard_sql: str) -> str:
    """Build one SQL statement that updates every _FLAGGED_TABLES entry for a contact, with one UPDATE CTE per table.

    Args:
        set_sql: SET list applied to each flagged table.
        guard_sql: Predicate on etown_contact deciding whether the contact is eligible.

    Returns:
        SQL taking $1 contactid and $2 mobile, returning found, email, firstname, rows_flagged.
    """
    aliases = [f"f{i:02d}" for i in range(len(_FLAGGED_TABLES))]
    updates = ",\n        ".join(
        f"{alias} AS (UPDATE {table} t SET {set_sql} FROM scope s "
        f"WHERE {predicate} RETURNING 1)"
        for alias, (table, predicate) in zip(aliases, _FLAGGED_TABLES.items())
    )
    counts = "\n                     + ".join(
        f"(SELECT count(*) FROM {alias})" for alias in aliases
    )
    return f"""
        WITH live AS (
            SELECT contactid, email, firstname, mobilephone
              FROM etown_contact
             WHERE contactid = $1
               AND {guard_sql}
        ),
        scope AS (
            SELECT l.contactid,
                   COALESCE($2::text, l.mobilephone)                    AS mobile,
                   l.email                                             AS email,
                   COALESCE((SELECT array_agg(u.userprofileid)
                               FROM etown_userprofile u
                              WHERE u.contactid = l.contactid
                                AND u.userprofileid IS NOT NULL), '{{}}'::text[]) AS upids,
                   COALESCE((SELECT array_agg(m.memberid)
                               FROM etown_member m
                              WHERE m.contactid = l.contactid
                                AND m.memberid IS NOT NULL), '{{}}'::text[])      AS mids,
                   COALESCE((SELECT array_agg(p.providerid)
                               FROM etown_provider p
                              WHERE p.contactid = l.contactid
                                AND p.providerid IS NOT NULL), '{{}}'::text[])    AS pids
              FROM live l
        ),
        {updates}
        SELECT (SELECT count(*) FROM live) > 0        AS found,
               (SELECT email     FROM live)           AS email,
               (SELECT firstname FROM live)           AS firstname,
               {counts}
                     AS rows_flagged
    """


_DELETE_SQL = _build_flag_sql(
    set_sql=f"deleted = 'true', deletiondate = {_IST_NOW}",
    guard_sql="deleted IS DISTINCT FROM 'true'",
)

_DEACTIVATE_SQL = _build_flag_sql(
    set_sql=(
        f"deactivated = 'true', deactivationdate = {_IST_NOW}, "
        f"reactivation = 'false'"
    ),
    guard_sql=(
        "deleted IS DISTINCT FROM 'true' "
        "AND deactivated IS DISTINCT FROM 'true'"
    ),
)

_REACTIVATE_SQL = _build_flag_sql(
    set_sql=(
        f"reactivation = 'true', reactivationdate = {_IST_NOW}, "
        f"deactivated = 'false'"
    ),
    guard_sql=(
        "deleted IS DISTINCT FROM 'true' "
        "AND deactivated = 'true'"
    ),
)


_CASCADE_FUNCTIONS = ("delete_account_cascade", "deactivate_account_cascade")

_CASCADE_PRESENT_SQL = """
    SELECT p.proname
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname = ANY($1::text[])
"""


async def warn_if_hard_delete_cascade_installed() -> list[str]:
    """Log an error if the hard-delete cascade functions exist in Postgres and return their names, never raising."""
    try:
        rows = await Database.fetch(_CASCADE_PRESENT_SQL, list(_CASCADE_FUNCTIONS))
    except Exception as exc:
        logger.warning("ACCOUNT_LIFECYCLE | CASCADE_CHECK_FAILED | %s", exc)
        return []

    found = [row["proname"] for row in rows]
    if found:
        logger.error(
            "ACCOUNT_LIFECYCLE | HARD_DELETE_CASCADE_STILL_INSTALLED | functions=%s | "
            "These issue physical DELETEs over the account tables (etown_provider, "
            "etown_providerservices, etown_memberrelation, etown_providerrelation, "
            "etown_contact and more). This application never calls them, but an older "
            "deployment or a direct SQL/Power Automate call will, and the rows will be "
            "destroyed instead of flagged. Run "
            "scripts/2026_08_account_drop_cascade_functions.sql to remove them.",
            ",".join(found),
        )
    else:
        logger.info("ACCOUNT_LIFECYCLE | cascade functions absent - soft delete is the only path")
    return found


async def _flag_account(
    sql: str,
    contact_id: str,
    log_prefix: str,
    mobilenumber: Optional[str] = None,
) -> dict[str, Any]:
    """Run a lifecycle flag statement and return found, email, firstname, contact_id and rows_flagged.

    Args:
        sql: One of _DELETE_SQL, _DEACTIVATE_SQL or _REACTIVATE_SQL.
        contact_id: Contact to flag.
        log_prefix: Log label identifying the mode.
        mobilenumber: Phone for phone-keyed tables; None falls back to etown_contact.mobilephone.

    Raises:
        RuntimeError: When the database pool is not initialised or is closed.
    """
    logger.info("%s | START | contact_id=%s", log_prefix, contact_id)

    if Database._pool is None or Database._pool._closed:
        raise RuntimeError("Database pool is not initialized or is closed.")

    async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
        row = await con.fetchrow(sql, contact_id, mobilenumber)

    if row is None or not row["found"]:
        logger.warning("%s | NOT_FOUND | contact_id=%s", log_prefix, contact_id)
        return {"found": False, "email": None, "firstname": None,
                "contact_id": contact_id, "rows_flagged": 0}

    logger.info(
        "%s | COMPLETE | contact_id=%s | rows=%d",
        log_prefix, contact_id, row["rows_flagged"],
    )
    return {
        "found": True,
        "email": row["email"],
        "firstname": row["firstname"],
        "contact_id": contact_id,
        "rows_flagged": row["rows_flagged"],
    }


async def _record_plan_transition(mode: str, contact_id: str) -> list[str]:
    """Record a lifecycle event on the contact's subscriptions and return the affected providerids, or [] on failure."""
    try:
        recorded = await plans_utils.record_plan_lifecycle_event(
            mode, contact_id=contact_id, changed_by=f"account_{mode}"
        )
        return recorded["providerids"]
    except Exception as exc:
        logger.error(
            "DB_ACCOUNT_PLAN | FAILED | contact_id=%s | mode=%s | %s",
            contact_id, mode, exc, exc_info=True,
        )
        return []


async def delete_account_from_db(
    contact_id: str,
    mobilenumber: Optional[str] = None,
) -> dict[str, Any]:
    """Soft-delete a contact across _FLAGGED_TABLES, revoke its auth tokens and challenges, and record the plan transition.

    Args:
        contact_id: Contact to delete.
        mobilenumber: Phone for phone-keyed tables; None falls back to etown_contact.mobilephone.

    Returns:
        dict with found, email, firstname, contact_id, tables_deleted and plan_providerids when found.
    """
    result = await _flag_account(
        _DELETE_SQL, contact_id, "DB_ACCOUNT_DELETE", mobilenumber
    )
    if result["found"]:
        revoked = await auth_utils.revoke_contact_refresh_tokens(contact_id)
        burned = await auth_utils.consume_contact_challenges(contact_id)
        logger.info(
            "DB_ACCOUNT_DELETE | AUTH_RETIRED | contact_id=%s | refresh_tokens=%d | challenges=%d",
            contact_id, revoked, burned,
        )
        result["plan_providerids"] = await _record_plan_transition("delete", contact_id)
    result["tables_deleted"] = result.pop("rows_flagged")
    return result


async def deactivate_account_in_db(
    contact_id: str,
    mobilenumber: Optional[str] = None,
) -> dict[str, Any]:
    """Flag a live contact as deactivated across _FLAGGED_TABLES and record the plan transition.

    Args:
        contact_id: Contact to deactivate.
        mobilenumber: Phone for phone-keyed tables; None falls back to etown_contact.mobilephone.

    Returns:
        dict with found, email, firstname, contact_id, tables_deactivated and plan_providerids when found.
    """
    result = await _flag_account(
        _DEACTIVATE_SQL, contact_id, "DB_ACCOUNT_DEACTIVATE", mobilenumber
    )
    if result["found"]:
        result["plan_providerids"] = await _record_plan_transition("deactivate", contact_id)
    result["tables_deactivated"] = result.pop("rows_flagged")
    return result


async def reactivate_account_in_db(
    contact_id: str,
    mobilenumber: Optional[str] = None,
) -> dict[str, Any]:
    """Reactivate a deactivated, undeleted contact across _FLAGGED_TABLES and record the plan transition.

    Args:
        contact_id: Contact to reactivate.
        mobilenumber: Phone for phone-keyed tables; None falls back to etown_contact.mobilephone.

    Returns:
        dict with found, email, firstname, contact_id, tables_reactivated and plan_providerids when found.
    """
    result = await _flag_account(
        _REACTIVATE_SQL, contact_id, "DB_ACCOUNT_REACTIVATE", mobilenumber
    )
    if result["found"]:
        result["plan_providerids"] = await _record_plan_transition("reactivate", contact_id)
    result["tables_reactivated"] = result.pop("rows_flagged")
    return result
