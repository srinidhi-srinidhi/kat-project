"""API Activity Service - daily per-endpoint call counters for members and providers.

Background bookkeeping service that upserts one dated row per audience in etown_member_tracking or etown_provider_tracking, routed by the caller's profile role, and claims distinct active actors in the etown_tracking_seen ledger; track_api_call() queues each write after the response is sent.

Features:
    - Off the request path: queued as a BackgroundTask, run after the response is sent
    - One row per day per audience: every endpoint's count is a column on the same dated row
    - Role-routed: one statement, one round trip, and only the matching table is written
    - Never double-counts: the role is resolved once, and a profile has one role
    - Distinct-actor counting: active_member/active_provider deduped once per day per actor
    - Actor identity on the ledger: each claim stores the actor's userprofileid and contactid
    - Fails open: any error is logged and dropped, never surfaced to the caller
    - Fixed column allowlist: an unmapped endpoint path is logged and dropped, never formatted
    - Ungated endpoints: /api/users/exists and /api/conversations/etown_lookup write the same total to both tables
    - Id-less calls: a gated endpoint called with neither contact nor userprofile id is skipped
    - Distinct-actor sources: active_member from mytown_suggestions, active_provider from provider_dashboard
    - Ledger authority: etown_tracking_seen wins when a counter disagrees, and nothing prunes it yet
    - Offers columns: offers_created and offers_viewed count calls, not rows in etown_offers or etown_offer_views
    - Server-clock day: hit_date is CURRENT_DATE, the same bucketing as etown_activeusers
    - New endpoint: needs an ALTER TABLE column in each table it feeds plus an entry in the column dict

Functions:
    _upsert(): One table's dated upsert SQL
    _select_for_role(): Role-filtered SELECT feeding one table's upsert
    _gated_sql(): Builds the role-routed statement for one endpoint
    _ungated_sql(): Builds the both-tables statement for an identity-less endpoint
    track_api_call(): Queue the increment on a request's BackgroundTasks (call this)
    record_api_call(): Perform the upsert; the coroutine the background task runs

Dependencies:
    - fastapi: BackgroundTasks, the queue the increment is handed to
    - app.db.client.Database: Shared asyncpg pool
    - public.etown_member_tracking / public.etown_provider_tracking: The counter tables
    - public.etown_tracking_seen: Per-actor-per-day dedupe ledger and the only store of actor ids
    - public.etown_userprofile: Supplies the role and ids that route each call
    - scripts/migrations/2026_09_api_tracking.sql: creates all three tables; keep in step with the dicts

Updates:
    04-Sep-2026: Created as member_activity.
    07-Sep-2026: Added active_member; renamed to api_activity with a parallel etown_provider_tracking table.
    08-Sep-2026: The ledger stores contactid beside userprofileid.
"""

import logging
from typing import Dict, Optional

from fastapi import BackgroundTasks

from app.db.client import Database

logger = logging.getLogger(__name__)


_MEMBER_TABLE = "etown_member_tracking"
_PROVIDER_TABLE = "etown_provider_tracking"
_SEEN_TABLE = "etown_tracking_seen"

_ROLE_PROVIDER = 1
_ROLE_MEMBER = 2


_MEMBER_COLUMNS: Dict[str, str] = {
    "/api/feeds": "feeds",
    "/api/mytown/mytown_suggestions": "mytown_suggestions",
    "/api/search/ai_search_v2": "ai_search_v2",
    "/api/chat/chaatie": "chaatie",
    "/api/conversations/prompt_suggestion": "prompt_suggestion",
    "/api/dashboard/nearme_list": "nearme_list",
    "/api/dashboard/member_dashboard": "member_dashboard",
    "/api/dashboard/pending_invitation": "pending_invitation",
    "/api/users/exists": "user_exists",
    "/api/conversations/etown_lookup": "etown_lookup",
    "/api/mytown/collections": "collections",
    "/api/groups/groups": "groups",
    "/api/offers/viewed": "offers_viewed",
    "/api/notifications/notification_count": "notification_count",
    "/api/invitations/invite_notification": "invite_notification",
    "/api/invitations/mycircle": "mycircle",
    "/api/message/inbox": "inbox",
    "/api/book/request_list": "request_list",
}

_PROVIDER_COLUMNS: Dict[str, str] = {
    "/api/feeds": "feeds",
    "/api/mytown/mytown_suggestions": "mytown_suggestions",
    "/api/search/ai_search_v2": "ai_search_v2",
    "/api/chat/chaatie": "chaatie",
    "/api/conversations/prompt_suggestion": "prompt_suggestion",
    "/api/dashboard/nearme_list": "nearme_list",
    "/api/dashboard/provider_dashboard": "provider_dashboard",
    "/api/provider/rangesummary": "rangesummary",
    "/api/dashboard/pending_invitation": "pending_invitation",
    "/api/users/exists": "user_exists",
    "/api/conversations/etown_lookup": "etown_lookup",
    "/api/mytown/collections": "collections",
    "/api/groups/groups": "groups",
    "/api/offers/create": "offers_created",
    "/api/notifications/notification_count": "notification_count",
    "/api/invitations/invite_notification": "invite_notification",
    "/api/invitations/mycircle": "mycircle",
    "/api/message/inbox": "inbox",
    "/api/book/request_list": "request_list",
}

TRACKED_ENDPOINTS = frozenset(_MEMBER_COLUMNS) | frozenset(_PROVIDER_COLUMNS)

_UNGATED_ENDPOINTS = frozenset({
    "/api/users/exists",
    "/api/conversations/etown_lookup",
})

_UNIQUE_COLUMNS: Dict[str, tuple] = {
    "/api/mytown/mytown_suggestions": ("active_member", None),
    "/api/dashboard/provider_dashboard": (None, "active_provider"),
}


def _upsert(table: str, column: str, unique_column: Optional[str], source: str) -> str:
    """Build an INSERT ... ON CONFLICT (hit_date) upsert that increments a table's daily counter column from a source SELECT."""
    cols = f"hit_date, {column}"
    sets = f"{column} = {table}.{column} + 1"

    if unique_column:
        cols += f", {unique_column}"
        sets += (
            f",\n                  {unique_column} = {table}.{unique_column}"
            f" + excluded.{unique_column}"
        )

    return (
        f"INSERT INTO public.{table} ({cols})\n"
        f"    {source}\n"
        f"    ON CONFLICT (hit_date)\n"
        f"    DO UPDATE SET {sets},\n"
        f"                  modifieddate = NOW()"
    )


def _select_for_role(role: int, unique_column: Optional[str]) -> str:
    """Build the SELECT that feeds one table's upsert from the prof CTE, filtered to a role."""
    counts = "CURRENT_DATE, 1"
    if unique_column:
        counts += ", (SELECT COUNT(*) FROM claimed)"
    return f"SELECT {counts}\n    FROM prof WHERE prof.role = {role}"


def _gated_sql(endpoint: str) -> str:
    """Build an endpoint's statement that resolves the caller's profile and increments only the table matching its role."""
    member_column = _MEMBER_COLUMNS.get(endpoint)
    provider_column = _PROVIDER_COLUMNS.get(endpoint)
    member_unique, provider_unique = _UNIQUE_COLUMNS.get(endpoint, (None, None))

    if member_column and provider_column:
        role_filter = f"up.role IN ({_ROLE_PROVIDER}, {_ROLE_MEMBER})"
    elif member_column:
        role_filter = f"up.role = {_ROLE_MEMBER}"
    else:
        role_filter = f"up.role = {_ROLE_PROVIDER}"

    parts = [
        "WITH prof AS (\n"
        "        SELECT up.userprofileid, up.contactid, up.role\n"
        "        FROM public.etown_userprofile up\n"
        f"        WHERE {role_filter}\n"
        "          AND ($1 = '' OR up.contactid = $1)\n"
        "          AND ($2 = '' OR up.userprofileid = $2)\n"
        "        ORDER BY up.userprofileid\n"
        "        LIMIT 1\n"
        "    )"
    ]

    if member_unique or provider_unique:
        if member_unique and provider_unique:
            metric = (
                f"CASE WHEN prof.role = {_ROLE_MEMBER} THEN '{member_unique}'\n"
                f"                    ELSE '{provider_unique}' END"
            )
            claim_roles = f"({_ROLE_PROVIDER}, {_ROLE_MEMBER})"
        elif member_unique:
            metric = f"'{member_unique}'"
            claim_roles = f"({_ROLE_MEMBER})"
        else:
            metric = f"'{provider_unique}'"
            claim_roles = f"({_ROLE_PROVIDER})"

        parts.append(
            "claimed AS (\n"
            f"        INSERT INTO public.{_SEEN_TABLE}\n"
            "            (hit_date, metric, userprofileid, contactid)\n"
            f"        SELECT CURRENT_DATE, {metric}, prof.userprofileid, prof.contactid\n"
            f"        FROM prof WHERE prof.role IN {claim_roles}\n"
            "        ON CONFLICT (hit_date, metric, userprofileid) DO NOTHING\n"
            "        RETURNING userprofileid\n"
            "    )"
        )

    upserts = []
    if member_column:
        upserts.append(
            _upsert(
                _MEMBER_TABLE, member_column, member_unique,
                _select_for_role(_ROLE_MEMBER, member_unique),
            )
        )
    if provider_column:
        upserts.append(
            _upsert(
                _PROVIDER_TABLE, provider_column, provider_unique,
                _select_for_role(_ROLE_PROVIDER, provider_unique),
            )
        )

    for index, upsert in enumerate(upserts[:-1]):
        parts.append(f"upsert_{index} AS (\n    {upsert}\n    RETURNING 1\n    )")

    return "\n    " + ",\n    ".join(parts) + "\n    " + upserts[-1] + "\n    "


def _ungated_sql(endpoint: str) -> str:
    """Build an endpoint's statement that increments its counter in both member and provider tables unconditionally."""
    source = "VALUES (CURRENT_DATE, 1)"
    member = _upsert(_MEMBER_TABLE, _MEMBER_COLUMNS[endpoint], None, source)
    provider = _upsert(_PROVIDER_TABLE, _PROVIDER_COLUMNS[endpoint], None, source)
    return (
        f"\n    WITH member_upsert AS (\n    {member}\n    RETURNING 1\n    )\n"
        f"    {provider}\n    "
    )


_UNGATED_SQL: Dict[str, str] = {
    endpoint: _ungated_sql(endpoint) for endpoint in sorted(_UNGATED_ENDPOINTS)
}
_GATED_SQL: Dict[str, str] = {
    endpoint: _gated_sql(endpoint)
    for endpoint in sorted(TRACKED_ENDPOINTS - _UNGATED_ENDPOINTS)
}


def track_api_call(
    background_tasks: BackgroundTasks,
    endpoint: str,
    contact_id: Optional[str] = None,
    userprofile_id: Optional[str] = None,
) -> None:
    """Queue record_api_call as a background task so the endpoint's counter is incremented after the response."""
    background_tasks.add_task(record_api_call, endpoint, contact_id, userprofile_id)


async def record_api_call(
    endpoint: str,
    contact_id: Optional[str] = None,
    userprofile_id: Optional[str] = None,
) -> None:
    """Increment today's counter for a tracked endpoint in the table the caller's role selects, logging instead of raising."""
    if endpoint not in TRACKED_ENDPOINTS:
        logger.warning(
            "record_api_call: no tracking column is mapped for %r, so this call cannot be "
            "counted -- add the column and its _MEMBER_COLUMNS/_PROVIDER_COLUMNS entry",
            endpoint,
        )
        return

    cid = (contact_id or "").strip()
    upid = (userprofile_id or "").strip()

    if endpoint not in _UNGATED_ENDPOINTS and not cid and not upid:
        logger.debug("record_api_call: %s called with no contact or profile id, not counted", endpoint)
        return

    try:
        if endpoint in _UNGATED_ENDPOINTS:
            await Database.execute(_UNGATED_SQL[endpoint])
        else:
            await Database.execute(_GATED_SQL[endpoint], cid, upid)
    except Exception:
        logger.warning(
            "record_api_call failed for endpoint=%s contactid=%s userprofileid=%s",
            endpoint, cid or "-", upid or "-", exc_info=True,
        )
