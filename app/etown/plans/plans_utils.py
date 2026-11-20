"""Plans Utilities - Postgres business logic for subscription plan resolution, limits and lifecycle.

Domain layer behind /api/plans: resolves each provider's effective plan from the plan columns on etown_provider, enforces point-in-time and cumulative caps against etown_subscription_plan, assigns, promotes and disables plans across a contact's providers, and emails plan payment submissions to support.

Features:
    - Single-statement plan resolution with expiry handling and a free-tier fallback
    - A batch badge lookup so a list of providers costs one statement, not one per row
    - Point-in-time caps (listings, towns, active offers) counted against live rows
    - A per-contact provider-onboarding cap, from the best active pack the contact holds
    - Plan payment submissions emailed to support, screenshot in blob, payer resolved by mobile
    - One subscription per contact, shared by every provider they own and moved together
    - Deactivation reads as the free tier from the one shared expression
    - Cumulative caps claimed with an atomic increment; periods roll monthly, or daily IST for Chaatie
    - A 60-second in-process plan cache, invalidated on every plan write
    - Refused writes recorded in notifications.etown_plan_refusal
    - One etown_subscription_history row per paid subscription, keyed by contact and mobile number
    - Near-limit usage nudges pushed once per provider, metric, period and threshold
    - Staged expiry alerts on get_plan_status(), dismissible and re-armed per stage
    - Plans keyed by userprofileid; resolve_plan() also returns providerid for providerid-only tables
    - Limit convention: NULL means unlimited and 0 means not permitted
    - Enforcement fails open: an unresolvable plan allows the write and logs the failure

Classes:
    PlanLimitError(): Raised when a write would exceed the provider's plan allowance.

Functions:
    _plan_alert_stage(): Map remaining_days to an expiry-notice stage, or None.
    invalidate_plan_cache(): Drop one cached plan, or all of them.
    resolve_plan(): Return the provider's effective plan row plus their providerid.
    resolve_contact_userprofileid(): Return the userprofileid of a contact's oldest live provider.
    resolve_member_plan(): Return the subscription on etown_member, a read-only fallback.
    resolve_provider_onboard(): Return a contact's provider-onboarding allowance and usage.
    check_provider_onboard_cap(): Enforce that allowance before a provider is onboarded.
    customer_whatsapp_allowed(): Whether a provider's plan lets their customers get WhatsApp.
    check_current_usage(): Enforce a point-in-time cap before a create.
    check_and_consume(): Atomically claim one unit of a monthly or daily allowance.
    get_plan_status(): Return the plan, feature flags and per-limit usage for a client.
    release_consumed(): Hand back a claimed unit when the write fails afterwards.
    premium_display(): Swap a verified marker for the paid tier's badge label.
    resolve_provider_badges(): Batch-resolve providertype, badge_label and tier_rank for a page of rows.
    assign_plan(): Move a contact's providers onto a plan_code and record each transition.
    record_plan_lifecycle_event(): Record a delete/deactivate/reactivate; a delete closes the plan.
    promote_contact(): Resolve a contactid to its providers, or member row, then assign_plan().
    disable_provider(): Set disabled = true on the provider a contactid owns as a userprofileid.
    enable_providers_for_contact(): Clear disabled on every live provider a contactid owns.
    _rearm_plan_alert(): Re-enable a contact's expiry alert once its dismissed stage has passed.
    dismiss_plan_alert(): Set alert = false on every live provider a contactid owns.
    record_refusal(): Log a cap refusal to etown_plan_refusal; never raises.
    update_subscription_plan(): Patch a plan's catalogue row, creating it if it doesn't exist.
    _maybe_nudge_usage(): Push a one-time near-limit or exhausted-allowance notice; never raises.
    route_plan_payment_request(): Resolve the payer, store the screenshot, email SUPPORT_EMAIL.
    _payment_field(): Render one payment field, or a placeholder when missing.
    _payment_phone_html(): Render the submitted number as a tel: link.
    _store_payment_screenshot(): Upload a base64 screenshot to blob and return its URL.
    _fetch_contact_name(): Read etown_contact.firstname for the payment email.
    _payment_name_html(): Render the name row, telling a nameless account from an unknown number.
    _payment_screenshot_html(): Render the screenshot row: link, not provided, or upload failure.

Dependencies:
    - app.db.client.Database: Shared asyncpg pool.
    - public.etown_subscription_plan: Plan catalogue (limits and feature flags).
    - public.etown_provider: Each provider's plan columns and the lifecycle flags the plan expression reads.
    - public.etown_member: The person's copy of the four plan columns, read only by resolve_member_plan.
    - notifications.etown_plan_change: Every plan transition, lifecycle ones included.
    - public.etown_subscription_history: One row per paid subscription, active or expired.
    - notifications.etown_provider_plan_usage: Monthly and daily counters.
    - notifications.etown_plan_refusal: One row per refused write.
    - app.utils.phone.normalize_indian_mobile: Reduces a number to its 10-digit national form.
    - app.etown.user.user_utils.fetch_phone_owner: Verified-first mobile owner lookup (lazy import).
    - app.etown.mytown.mytown_utils.prune_coverage_towns_to_plan: Trims towns to max_towns (lazy import).
    - app.etown.notifications.core.dispatcher: Usage nudge push delivery (lazy import).
    - app.utils.blob_storage: Shared base64 decode, MIME sniff and Azure Blob upload.
    - app.services.email_service.send_email: Microsoft Graph sender.
    - app.services.email_templates: Branded HTML shell.

Updates:
    18-Aug-2026: Created. Plan link keyed on userprofileid; enforcement fails open.
    21-Aug-2026: resolve_provider_badges() also returns tier_rank.
    24-Aug-2026: get_plan_status features carry support_email; one subscription per contact.
    26-Aug-2026: Logged silent exception handlers with tracebacks.
    31-Aug-2026: Added route_plan_payment_request() for POST /plans/payment_request.
    01-Sep-2026: Payment requests to SUPPORT_EMAIL; added disable_provider(); promote_contact() replaces promote_provider().
    02-Sep-2026: remaining_days and plan alerts in get_plan_status(); added dismiss_plan_alert().
    03-Sep-2026: Payment mail copies PLAN_PAYMENT_EXTRA_EMAILS; assign_plan() prunes coverage towns to max_towns.
    17-Sep-2026: Member plan columns mirrored; member-only contacts promotable; get_plan_status() takes contact_id.
    24-Sep-2026: get_plan_status() skips the expiry alert for _PLAN_ALERT_EXEMPT_CODES.
    27-Oct-2026: assign_plan() and contact deletes write etown_subscription_history rows.
"""

from __future__ import annotations

import html
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Tuple
import asyncpg

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.services.email_service import send_email
from app.services.email_templates import paragraph, render_email
from app.utils.phone import normalize_indian_mobile
from app.utils.blob_storage import (
    current_timestamp_prefix,
    detect_mime_type,
    is_stored_media_url,
    normalize_container_name,
    to_bytes,
    to_public_blob_url,
    upload_blob_bytes,
)

_IST = timezone(timedelta(hours=5, minutes=30))
_PAYMENT_LINK_COLOUR = "#1a56db"

_EFFECTIVE_PLAN_CODE = """
    CASE
        WHEN lower(COALESCE({a}.deactivated, 'false')) = 'true'
        THEN 'free'
        WHEN {a}.subscription_status = 'active'
         AND ({a}.plan_expires_on IS NULL OR {a}.plan_expires_on >= current_date)
        THEN {a}.plan_code
        ELSE 'free'
    END
"""

VERIFIED_PROVIDER_MARKERS = frozenset({"verified", "etown_verified"})


def premium_display(value: Optional[str], premium_label: Optional[str]) -> Optional[str]:
    """Return premium_label in place of a verified providertype marker, otherwise value unchanged.

    Args:
        value: Raw providertype value, e.g. etown_verified or verified.
        premium_label: premium_label from resolve_provider_badges, or None.

    Returns:
        The badge label when both apply, otherwise value unchanged.
    """
    if premium_label and (value or "").strip().lower() in VERIFIED_PROVIDER_MARKERS:
        return premium_label
    return value


MONTHLY_LIMITS: Dict[str, str] = {
    "offers_created": "max_offers_per_month",
    "chaatie_generations": "max_chaatie_generations_month",
    "posts_created": "max_posts_per_month",
    "whatsapp_sends": "whatsapp_quota_month",
    "chattie_conversation": "chattie_conversation",
}

DAILY_METRICS = frozenset({"chattie_conversation"})
_PERIOD_MONTH = "date_trunc('month', current_date)::date"
_PERIOD_IST_DAY = "(now() AT TIME ZONE 'Asia/Kolkata')::date"

_LIVE = "COALESCE(deleted, 'false') <> 'true'"

_PLAN_ALERT_STAGES: Tuple[Tuple[str, int, int], ...] = (
    ("final", 1, 1),
    ("week", 2, 7),
)

_PLAN_ALERT_EXEMPT_CODES = frozenset({"free", "unlimited"})


def _plan_alert_stage(remaining_days: Optional[int]) -> Optional[str]:
    """Return the expiry-notice stage ("final" or "week") for remaining_days, or None outside every stage."""
    if remaining_days is None:
        return None
    for stage, low, high in _PLAN_ALERT_STAGES:
        if low <= remaining_days <= high:
            return stage
    return None

POINT_IN_TIME_LIMITS: Dict[str, Dict[str, str]] = {
    "max_listings": {
        "key": "userprofileid",
        "label": "listings",
        "sql": f"SELECT count(*) FROM etown_providerservices WHERE userprofileid = $1 AND {_LIVE}",
    },
    "max_towns": {
        "key": "providerid",
        "label": "towns",
        "sql": f"SELECT count(DISTINCT townprofileid) FROM etown_etownprovider WHERE providerid = $1 AND {_LIVE}",
    },
    "max_active_offers": {
        "key": "userprofileid",
        "label": "active offers",
        "sql": (
            "SELECT count(*) FROM etown_offers WHERE userprofileid = $1 "
            f"AND {_LIVE} AND COALESCE(isactive, 'true') <> 'false'"
        ),
    },
}
CONTACT_PLAN_CTE = f"""
    contact_plan AS (
        SELECT cp.plan_code, cp.subscription_status, cp.plan_started_on, cp.plan_expires_on
          FROM (
                SELECT plan_code, subscription_status, plan_started_on, plan_expires_on,
                       deactivated, deleted, contactid
                  FROM etown_provider
                 UNION ALL
                SELECT plan_code, subscription_status, plan_started_on, plan_expires_on,
                       deactivated, deleted, contactid
                  FROM etown_member
               ) cp
          JOIN public.etown_subscription_plan cpl
            ON cpl.plan_code = {_EFFECTIVE_PLAN_CODE.format(a='cp')}
         WHERE cp.contactid = {{contact}}
           AND COALESCE(cp.deleted, 'false') <> 'true'
           AND {_EFFECTIVE_PLAN_CODE.format(a='cp')} <> 'free'
         ORDER BY cpl.display_order DESC, cp.plan_expires_on DESC NULLS FIRST
         LIMIT 1
    )
"""

_CONTACT_MOBILE_SQL = r"""
    NULLIF(right(regexp_replace(COALESCE(
        (SELECT c.mobilephone FROM etown_contact c
          WHERE c.contactid = {contact} AND btrim(COALESCE(c.mobilephone, '')) <> ''
          LIMIT 1),
        (SELECT pr.mobilenumber FROM etown_provider pr
          WHERE pr.contactid = {contact} AND btrim(COALESCE(pr.mobilenumber, '')) <> ''
          LIMIT 1),
        (SELECT mb.mobilenumber FROM etown_member mb
          WHERE mb.contactid = {contact} AND btrim(COALESCE(mb.mobilenumber, '')) <> ''
          LIMIT 1),
        ''), '\D', '', 'g'), 10), '')
"""

_SAME_ACTIVE_SUBSCRIPTION_SQL = """
    SELECT id
      FROM public.etown_subscription_history
     WHERE contactid = $1
       AND status = 'active'
       AND plan_code = $2
       AND plan_started_on IS NOT DISTINCT FROM $3::date
       AND plan_expires_on IS NOT DISTINCT FROM $4::date
     LIMIT 1
"""

_EXPIRE_CONTACT_SUBSCRIPTION_SQL = """
    UPDATE public.etown_subscription_history
       SET status = 'expired', updated_at = now()
     WHERE contactid = $1
       AND status = 'active'
       AND id IS DISTINCT FROM $2::uuid
"""

_INSERT_SUBSCRIPTION_SQL = f"""
    INSERT INTO public.etown_subscription_history
        (contactid, userprofileid, mobilenumber, plan_code, plan_started_on,
         plan_expires_on, status, changed_by, reason)
    VALUES ($1::text, $2::text, {_CONTACT_MOBILE_SQL.format(contact='$1::text')},
            $3::text, $4::date, $5::date, 'active', $6::text, $7::text)
"""

CONTACT_PLAN_COLUMNS = "plan_code, subscription_status, plan_started_on, plan_expires_on"
CONTACT_PLAN_VALUES = (
    "COALESCE((SELECT plan_code FROM contact_plan), 'free'), "
    "COALESCE((SELECT subscription_status FROM contact_plan), 'active'), "
    "(SELECT plan_started_on FROM contact_plan), "
    "(SELECT plan_expires_on FROM contact_plan)"
)


class PlanLimitError(Exception):
    """Raised when a write would exceed the provider's plan allowance."""

    def __init__(
        self,
        message: str,
        *,
        plan_code: str,
        limit: int,
        used: int,
        userprofile_id: Optional[str] = None,
        limit_column: Optional[str] = None,
        contact_id: Optional[str] = None,
    ):
        """Store the refusal message plus the plan, limit, usage and subject it applies to."""
        super().__init__(message)
        self.plan_code = plan_code
        self.limit = limit
        self.used = used
        self.userprofile_id = userprofile_id
        self.limit_column = limit_column
        self.contact_id = contact_id


_PLAN_CACHE_TTL_SECONDS = 60
_PLAN_CACHE_MAX_ENTRIES = 5000
_plan_cache: Dict[str, Any] = {}


def invalidate_plan_cache(userprofile_id: Optional[str] = None) -> None:
    """Drop one userprofile's cached plan, or clear the whole plan cache when no id is given."""
    if userprofile_id is None:
        _plan_cache.clear()
    else:
        _plan_cache.pop(userprofile_id, None)


async def resolve_plan(userprofile_id: str) -> Optional[Dict[str, Any]]:
    """Return the live provider's effective plan row with provider, expiry and alert fields, cached 60s; None fails open."""
    if not userprofile_id:
        return None

    cached = _plan_cache.get(userprofile_id)
    if cached is not None:
        expires_at, plan = cached
        if expires_at > time.monotonic():
            return dict(plan)
        del _plan_cache[userprofile_id]

    try:
        row = await Database.fetchrow(
            f"""
            SELECT pl.*,
                   pr.providerid,
                   pr.contactid,
                   pr.plan_code AS subscribed_plan_code,
                   pr.subscription_status,
                   pr.plan_started_on,
                   pr.plan_expires_on,
                   CASE
                       WHEN pr.plan_expires_on IS NULL THEN NULL::int
                       ELSE GREATEST(0, pr.plan_expires_on - current_date)
                   END AS plan_remaining_days,
                   pr.alert AS provider_alert,
                   CASE
                       WHEN pr.alert_dismissed_on IS NULL
                         OR pr.plan_expires_on IS NULL THEN NULL::int
                       ELSE GREATEST(0, pr.plan_expires_on - pr.alert_dismissed_on)
                   END AS alert_dismissed_at_days,
                   pr.deactivated AS provider_deactivated
            FROM etown_provider pr
            JOIN public.etown_subscription_plan pl
              ON pl.plan_code = {_EFFECTIVE_PLAN_CODE.format(a='pr')}
            WHERE pr.userprofileid = $1
              AND COALESCE(pr.deleted, 'false') <> 'true'
            LIMIT 1
            """,
            userprofile_id,
        )
        if row is None:
            return None
        plan = dict(row)
        plan["account_state"] = (
            "deactivated"
            if str(plan.pop("provider_deactivated", None) or "false").strip().lower() == "true"
            else "active"
        )
        if len(_plan_cache) >= _PLAN_CACHE_MAX_ENTRIES:
            _plan_cache.clear()
        _plan_cache[userprofile_id] = (
            time.monotonic() + _PLAN_CACHE_TTL_SECONDS, dict(plan)
        )
        return plan
    except Exception as exc:
        logger.warning("resolve_plan failed for userprofileid=%s: %s", userprofile_id, exc)
        return None


async def resolve_member_plan(
    userprofile_id: Optional[str] = None,
    contact_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the plan recorded on the live etown_member row, shaped like resolve_plan's row, or None.

    Args:
        userprofile_id: Profile asked about; its own member row wins, else its contact's.
        contact_id: Contact used when the profile does not resolve to one.

    Returns:
        The plan row with the member's plan columns and userprofileid, or None.
    """
    if not userprofile_id and not contact_id:
        return None
    try:
        row = await Database.fetchrow(
            f"""
            WITH scope AS (
                SELECT $1::text AS upid,
                       COALESCE(
                           (SELECT up.contactid
                              FROM etown_userprofile up
                             WHERE up.userprofileid = $1::text),
                           $2::text
                       ) AS cid
            )
            SELECT pl.*,
                   m.userprofileid,
                   NULL::text AS providerid,
                   m.contactid,
                   m.plan_code AS subscribed_plan_code,
                   m.subscription_status,
                   m.plan_started_on,
                   m.plan_expires_on,
                   CASE
                       WHEN m.plan_expires_on IS NULL THEN NULL::int
                       ELSE GREATEST(0, m.plan_expires_on - current_date)
                   END AS plan_remaining_days,
                   true AS provider_alert,
                   NULL::int AS alert_dismissed_at_days,
                   m.deactivated AS provider_deactivated
            FROM scope s
            JOIN etown_member m
              ON (s.upid IS NOT NULL AND m.userprofileid = s.upid)
              OR (s.cid IS NOT NULL AND m.contactid = s.cid)
            JOIN public.etown_subscription_plan pl
              ON pl.plan_code = {_EFFECTIVE_PLAN_CODE.format(a='m')}
            WHERE COALESCE(m.deleted, 'false') <> 'true'
            ORDER BY (m.userprofileid = s.upid) DESC NULLS LAST,
                     m.dateofcreation ASC NULLS LAST, m.memberid ASC
            LIMIT 1
            """,
            userprofile_id, contact_id,
        )
    except Exception as exc:
        logger.warning(
            "resolve_member_plan failed for userprofileid=%s contactid=%s: %s",
            userprofile_id, contact_id, exc,
        )
        return None

    if row is None:
        return None
    plan = dict(row)
    plan["account_state"] = (
        "deactivated"
        if str(plan.pop("provider_deactivated", None) or "false").strip().lower() == "true"
        else "active"
    )
    return plan


async def resolve_provider_onboard(contact_id: str) -> Optional[Dict[str, Any]]:
    """Return the contact's provider-onboarding allowance from their best plan, or None to fail open.

    Returns:
        Dict of plan_code, plan_name, limit (None if unlimited) and used live providers.
    """
    if not contact_id:
        return None
    try:
        row = await Database.fetchrow(
            f"""
            WITH owned AS (
                SELECT userprofileid,
                       {_EFFECTIVE_PLAN_CODE.format(a='etown_provider')} AS effective_plan
                  FROM etown_provider
                 WHERE contactid = $1
                   AND {_LIVE}
            ),
            best AS (
                SELECT o.effective_plan
                  FROM owned o
                  JOIN public.etown_subscription_plan p ON p.plan_code = o.effective_plan
                 ORDER BY p.display_order DESC
                 LIMIT 1
            )
            SELECT pl.plan_code,
                   pl.plan_name,
                   pl.provider_onboard AS cap,
                   (SELECT count(*) FROM owned) AS used
              FROM public.etown_subscription_plan pl
             WHERE pl.plan_code = COALESCE((SELECT effective_plan FROM best), 'free')
            """,
            contact_id,
        )
    except Exception as exc:
        logger.warning("resolve_provider_onboard failed for contactid=%s: %s", contact_id, exc)
        return None

    if row is None:
        return None
    return {
        "plan_code": row["plan_code"],
        "plan_name": row["plan_name"],
        "limit": row["cap"],
        "used": row["used"] or 0,
    }


async def check_provider_onboard_cap(
    contact_id: str, business_name: Optional[str] = None
) -> None:
    """Raise and record PlanLimitError when the contact is at the onboarding cap, unless business_name repeats one they own.

    Raises:
        PlanLimitError: The contact already holds as many live providers as the plan allows.
    """
    allowance = await resolve_provider_onboard(contact_id)
    if allowance is None or allowance["limit"] is None:
        return

    cap, used = allowance["limit"], allowance["used"]
    if used < cap:
        return

    if business_name and business_name.strip():
        try:
            repeat = await Database.fetchval(
                f"""
                SELECT 1 FROM etown_provider
                 WHERE contactid = $1
                   AND lower(btrim(businessname)) = lower(btrim($2))
                   AND {_LIVE}
                 LIMIT 1
                """,
                contact_id, business_name,
            )
        except Exception as exc:
            logger.warning(
                "provider_onboard repeat check failed for contactid=%s: %s", contact_id, exc
            )
            repeat = None
        if repeat:
            return

    err = PlanLimitError(
        f"Your {allowance['plan_name']} plan covers {cap} business(es). "
        f"You have already onboarded {used}, so this one cannot be added.",
        plan_code=allowance["plan_code"], limit=cap, used=used,
        contact_id=contact_id, limit_column="provider_onboard",
    )
    await record_refusal(err)
    raise err


async def customer_whatsapp_allowed(userprofile_id: str) -> bool:
    """Return whether the provider's plan lets their customers receive WhatsApp; True when the plan is unresolvable."""
    plan = await resolve_plan(userprofile_id)
    if plan is None:
        return True
    return plan.get("whatsapp_quota_month") != 0


async def check_current_usage(userprofile_id: str, limit_column: str) -> None:
    """Raise and record PlanLimitError when a live count of the source rows already meets a point-in-time cap.

    Raises:
        PlanLimitError: The provider is at or over the limit_column cap.
    """
    spec = POINT_IN_TIME_LIMITS[limit_column]
    plan = await resolve_plan(userprofile_id)
    if plan is None:
        return

    limit = plan.get(limit_column)
    if limit is None:
        return

    key_value = plan.get("providerid") if spec["key"] == "providerid" else userprofile_id
    if not key_value:
        logger.warning(
            "check_current_usage: no %s for userprofileid=%s, allowing %s",
            spec["key"], userprofile_id, limit_column,
        )
        return

    try:
        used = await Database.fetchval(spec["sql"], key_value) or 0
    except Exception as exc:
        logger.warning("check_current_usage query failed for %s: %s", limit_column, exc)
        return

    if used >= limit:
        err = PlanLimitError(
            f"Your {plan['plan_name']} plan allows {limit} {spec['label']}.",
            plan_code=plan["plan_code"], limit=limit, used=used,
            userprofile_id=userprofile_id, limit_column=limit_column,
        )
        await record_refusal(err)
        raise err


async def check_and_consume(userprofile_id: str, metric: str) -> None:
    """Atomically claim one unit of a monthly or IST-daily metered allowance, releasing it and raising when over.

    Raises:
        PlanLimitError: The plan excludes the feature or the claim exceeds the limit.
    """
    limit_column = MONTHLY_LIMITS[metric]
    period = _PERIOD_IST_DAY if metric in DAILY_METRICS else _PERIOD_MONTH
    plan = await resolve_plan(userprofile_id)
    if plan is None:
        return

    limit = plan.get(limit_column)
    if limit is None:
        return

    if limit == 0:
        err = PlanLimitError(
            f"Your {plan['plan_name']} plan does not include this feature.",
            plan_code=plan["plan_code"], limit=0, used=0,
            userprofile_id=userprofile_id, limit_column=limit_column,
        )
        await record_refusal(err)
        raise err

    try:
        used = await Database.fetchval(
            f"""
            INSERT INTO notifications.etown_provider_plan_usage
                (userprofileid, metric, period_start, used_count)
            VALUES ($1, $2, {period}, 1)
            ON CONFLICT (userprofileid, metric, period_start)
            DO UPDATE SET used_count = etown_provider_plan_usage.used_count + 1,
                          updated_at = now()
            RETURNING used_count
            """,
            userprofile_id, metric,
        )
    except Exception as exc:
        logger.warning("check_and_consume failed for %s/%s: %s", userprofile_id, metric, exc)
        return

    if used is not None and used <= limit:
        await _maybe_nudge_usage(userprofile_id, metric, limit, used, plan)
        return

    if used is not None and used > limit:
        await release_consumed(userprofile_id, metric)
        err = PlanLimitError(
            f"Your {plan['plan_name']} plan allows {limit} per "
            f"{'day' if metric in DAILY_METRICS else 'month'}.",
            plan_code=plan["plan_code"], limit=limit, used=used - 1,
            userprofile_id=userprofile_id, limit_column=limit_column,
        )
        await record_refusal(err)
        raise err


async def resolve_contact_userprofileid(contact_id: str) -> Optional[str]:
    """Return the userprofileid of the contact's oldest live provider, or None when none exists or the lookup fails."""
    if not contact_id:
        return None
    try:
        row = await Database.fetchrow(
            f"""
            SELECT userprofileid
              FROM etown_provider
             WHERE contactid = $1
               AND userprofileid IS NOT NULL
               AND {_LIVE}
             ORDER BY creationdate ASC NULLS LAST, providerid ASC
             LIMIT 1
            """,
            contact_id,
        )
    except Exception as exc:
        logger.warning("resolve_contact_userprofileid failed for contactid=%s: %s", contact_id, exc)
        return None
    return row["userprofileid"] if row else None


async def get_plan_status(
    userprofile_id: Optional[str] = None,
    contact_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the plan, features, per-limit usage, remaining days and expiry alert, read via provider then member row.

    Args:
        userprofile_id: Business to read through; defaults to the contact's oldest provider.
        contact_id: Owner contact, checked against userprofile_id when both are given.

    Returns:
        The plan status dict, or None when neither id reaches a live provider or member.

    Raises:
        ValueError: Neither id is given, or the profile belongs to a different contact.
    """
    if not userprofile_id and not contact_id:
        raise ValueError("get_plan_status needs a userprofile_id or a contact_id")

    plan_source = "provider"
    plan = None
    if userprofile_id:
        plan = await resolve_plan(userprofile_id)
    else:
        seed = await resolve_contact_userprofileid(contact_id)
        if seed:
            plan = await resolve_plan(seed)
            if plan is not None:
                userprofile_id = seed

    if plan is None:
        plan = await resolve_member_plan(userprofile_id, contact_id)
        if plan is None:
            return None
        plan_source = "member"
        userprofile_id = plan.pop("userprofileid", None) or userprofile_id

    if contact_id and plan.get("contactid") and plan["contactid"] != contact_id:
        raise ValueError(
            f"userprofileid {userprofile_id!r} belongs to contactid "
            f"{plan['contactid']!r}, not {contact_id!r}"
        )

    remaining_days = plan.get("plan_remaining_days")
    alert_armed = bool(plan.get("provider_alert"))
    contact_id = plan.get("contactid") or contact_id
    alert_exempt = (plan.get("plan_code") or "").lower() in _PLAN_ALERT_EXEMPT_CODES
    alert_stage = None if alert_exempt else _plan_alert_stage(remaining_days)
    if contact_id and not alert_armed and not alert_exempt:
        dismissed_stage = _plan_alert_stage(plan.get("alert_dismissed_at_days"))
        if alert_stage is None or alert_stage != dismissed_stage:
            alert_armed = await _rearm_plan_alert(contact_id)

    limits: Dict[str, Dict[str, Any]] = {}
    provider_id = plan.get("providerid")
    selects = [
        f"({spec['sql'].replace('$1', '$2' if spec['key'] == 'providerid' else '$1')})"
        f" AS {column}"
        for column, spec in POINT_IN_TIME_LIMITS.items()
    ]
    counted: Dict[str, Any] = {}
    try:
        row = await Database.fetchrow("SELECT " + ", ".join(selects), userprofile_id, provider_id)
        counted = dict(row) if row else {}
    except Exception as exc:
        logger.warning("get_plan_status counts failed for %s: %s", userprofile_id, exc)

    for column, spec in POINT_IN_TIME_LIMITS.items():
        if spec["key"] == "providerid" and not provider_id:
            used = None
        else:
            used = counted.get(column)
        limits[column] = {"limit": plan.get(column), "used": used}

    try:
        rows = await Database.fetch(
            f"""
            SELECT metric, used_count
            FROM notifications.etown_provider_plan_usage
            WHERE userprofileid = $1
              AND period_start = CASE WHEN metric = ANY($2::text[])
                                      THEN {_PERIOD_IST_DAY}
                                      ELSE {_PERIOD_MONTH} END
            """,
            userprofile_id, sorted(DAILY_METRICS),
        )
        consumed = {row["metric"]: row["used_count"] for row in rows}
    except Exception as exc:
        logger.warning("get_plan_status usage read failed for %s: %s", userprofile_id, exc)
        consumed = {}

    for metric, column in MONTHLY_LIMITS.items():
        limits[column] = {"limit": plan.get(column), "used": consumed.get(metric, 0)}

    limits["max_gallery_photos"] = {"limit": plan.get("max_gallery_photos"), "used": None}

    if contact_id:
        onboard = await resolve_provider_onboard(contact_id)
        if onboard is not None:
            limits["provider_onboard"] = {"limit": onboard["limit"], "used": onboard["used"]}

    for entry in limits.values():
        entry["unlimited"] = entry["limit"] is None
        entry["remaining"] = (
            None if entry["limit"] is None or entry["used"] is None
            else max(0, entry["limit"] - entry["used"])
        )

    return {
        "userprofileid": userprofile_id,
        "contactid": contact_id,
        "plan_source": plan_source,
        "plan_code": plan["plan_code"],
        "plan_name": plan["plan_name"],
        "badge_label": plan.get("badge_label"),
        "subscribed_plan_code": plan.get("subscribed_plan_code"),
        "status": plan.get("subscription_status") or "active",
        "account_state": plan.get("account_state") or "active",
        "started_on": plan.get("plan_started_on"),
        "expires_on": plan.get("plan_expires_on"),
        "remaining_days": remaining_days,
        "alert": alert_armed and alert_stage is not None,
        "features": {
            "verified_badge": plan.get("verified_badge"),
            "full_business_profile": plan.get("full_business_profile"),
            "post_audience_targeting": plan.get("post_audience_targeting"),
            "booking_insights": plan.get("booking_insights"),
            "profile_view_insights": plan.get("profile_view_insights"),
            "support_channel": plan.get("support_channel"),
            "support_response_hours": plan.get("support_response_hours"),
            "support_email": settings.SUPPORT_EMAIL,
        },
        "limits": limits,
    }


async def _rearm_plan_alert(contact_id: str) -> bool:
    """Re-arm the expiry alert and clear the dismissal date on every live provider of a contact; never raises.

    Returns:
        True if any row was re-armed, False if none matched or the write failed.
    """
    try:
        rearmed = await Database.fetch(
            f"""
            UPDATE etown_provider
               SET alert = true,
                   alert_dismissed_on = NULL
             WHERE contactid = $1
               AND {_LIVE}
            RETURNING userprofileid
            """,
            contact_id,
        )
    except Exception as exc:
        logger.warning("_rearm_plan_alert failed for contactid=%s: %s", contact_id, exc)
        return False
    for row in rearmed:
        invalidate_plan_cache(row["userprofileid"])
    return bool(rearmed)


_DISMISS_PLAN_ALERT_SQL = f"""
    WITH target AS (
        SELECT userprofileid, providerid, businessname, alert AS was_armed
          FROM etown_provider
         WHERE contactid = $1
           AND {_LIVE}
    ),
    updated AS (
        UPDATE etown_provider p
           SET alert = false,
               alert_dismissed_on = current_date
          FROM target t
         WHERE p.providerid = t.providerid
           AND (p.alert IS DISTINCT FROM false
                OR p.alert_dismissed_on IS DISTINCT FROM current_date)
        RETURNING p.providerid
    )
    SELECT userprofileid, providerid, businessname, was_armed
      FROM target
     ORDER BY providerid
"""


async def dismiss_plan_alert(contact_id: str) -> Dict[str, Any]:
    """Set alert false and stamp alert_dismissed_on today on every live provider a contact owns.

    Returns:
        Dict of found, contactid, providers (with was_armed) and alerts_dismissed count.
    """
    rows = await Database.fetch(_DISMISS_PLAN_ALERT_SQL, contact_id)
    providers = [
        {
            "providerid": r["providerid"],
            "businessname": r["businessname"],
            "was_armed": bool(r["was_armed"]),
        }
        for r in rows
    ]
    for r in rows:
        invalidate_plan_cache(r["userprofileid"])
    result: Dict[str, Any] = {
        "found": bool(providers),
        "contactid": contact_id,
        "providers": providers,
        "alerts_dismissed": sum(1 for p in providers if p["was_armed"]),
    }
    logger.info(
        "dismiss_plan_alert | contactid=%s | matched=%d | dismissed=%d",
        contact_id, len(providers), result["alerts_dismissed"],
    )
    return result


async def record_refusal(err: "PlanLimitError") -> None:
    """Insert a plan-refusal row for a PlanLimitError that names a subject and limit column; never raises."""
    if not err.limit_column or not (err.userprofile_id or err.contact_id):
        return
    try:
        await Database.execute(
            """
            INSERT INTO notifications.etown_plan_refusal
                (userprofileid, contactid, plan_code, limit_column, limit_value, used_value)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            err.userprofile_id, err.contact_id, err.plan_code,
            err.limit_column, err.limit, err.used,
        )
    except Exception as exc:
        logger.warning("record_refusal failed for %s/%s: %s",
                       err.userprofile_id or err.contact_id, err.limit_column, exc)


async def assign_plan(
    userprofile_id: str,
    plan_code: str,
    *,
    status: str = "active",
    started_on=None,
    expires_on=None,
    changed_by: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Move the contact's live provider and member rows onto plan_code, log history, record the subscription and prune towns.

    Args:
        userprofile_id: Provider or member profile that seeds the contact to move.
        plan_code: Target plan in etown_subscription_plan.
        status: New subscription_status: active, cancelled, expired or suspended.
        started_on: Start date; defaults to the existing start, or today for a new paid plan.
        expires_on: Expiry date; None means the plan never lapses.
        changed_by: Operator recorded on the history rows.
        reason: Reason recorded on the history rows.

    Returns:
        Dict of userprofileid, contactid, subject, from, to, providers_updated and providers.

    Raises:
        ValueError: Unknown plan_code or status, or no live provider or member row.
    """
    plan_exists = await Database.fetchval(
        "SELECT 1 FROM public.etown_subscription_plan WHERE plan_code = $1", plan_code)
    if not plan_exists:
        raise ValueError(f"{plan_code!r} is not a plan in etown_subscription_plan")
    if status not in ("active", "cancelled", "expired", "suspended"):
        raise ValueError(f"{status!r} is not an accepted subscription_status")

    before = await Database.fetchrow(
        f"""
        SELECT userprofileid, contactid, plan_code, subscription_status,
               plan_started_on, plan_expires_on, subject
          FROM (
                SELECT userprofileid, contactid, plan_code, subscription_status,
                       plan_started_on, plan_expires_on, 1 AS pref, 'provider' AS subject
                  FROM etown_provider
                 WHERE userprofileid = $1 AND {_LIVE}
                 UNION ALL
                SELECT userprofileid, contactid, plan_code, subscription_status,
                       plan_started_on, plan_expires_on, 2 AS pref, 'member' AS subject
                  FROM etown_member
                 WHERE userprofileid = $1 AND {_LIVE}
               ) s
         ORDER BY pref
         LIMIT 1
        """,
        userprofile_id,
    )
    if before is None:
        raise ValueError(
            f"no live provider or member row for userprofileid {userprofile_id!r}"
        )

    resolved_start = started_on
    if resolved_start is None:
        resolved_start = (
            before["plan_started_on"]
            if before["plan_started_on"] or plan_code == "free"
            else date.today()
        )

    moved = await Database.fetch(
        f"""
        WITH targets AS (
            SELECT userprofileid, providerid, contactid, plan_code, subscription_status
              FROM etown_provider
             WHERE CASE WHEN $2::text IS NULL THEN userprofileid = $1 ELSE contactid = $2 END
               AND {_LIVE}
        ),
        members AS (
            UPDATE etown_member m
               SET plan_code = $3, subscription_status = $4,
                   plan_started_on = $5, plan_expires_on = $6
             WHERE CASE WHEN $2::text IS NULL THEN m.userprofileid = $1 ELSE m.contactid = $2 END
               AND COALESCE(m.deleted, 'false') <> 'true'
            RETURNING m.memberid
        ),
        updated AS (
            UPDATE etown_provider p
               SET plan_code = $3, subscription_status = $4,
                   plan_started_on = $5, plan_expires_on = $6,
                   alert = true, alert_dismissed_on = NULL
              FROM targets t
             WHERE p.userprofileid = t.userprofileid
               AND COALESCE(p.deleted, 'false') <> 'true'
            RETURNING t.userprofileid
        )
        INSERT INTO notifications.etown_plan_change
            (userprofileid, providerid, contactid, from_plan_code, to_plan_code,
             from_status, to_status, plan_started_on, plan_expires_on, changed_by, reason)
        SELECT t.userprofileid, t.providerid, t.contactid, t.plan_code, $3,
               t.subscription_status, $4, $5, $6, $7, $8
          FROM targets t
          JOIN updated u ON u.userprofileid = t.userprofileid
        RETURNING userprofileid, providerid, from_plan_code, from_status
        """,
        userprofile_id, before["contactid"], plan_code, status,
        resolved_start, expires_on, changed_by, reason,
    )

    for row in moved:
        invalidate_plan_cache(row["userprofileid"])

    if before["contactid"]:
        opens_subscription = plan_code != "free" and status == "active"
        try:
            async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
                async with con.transaction():
                    keep_id = None
                    if opens_subscription:
                        keep_id = await con.fetchval(
                            _SAME_ACTIVE_SUBSCRIPTION_SQL,
                            before["contactid"], plan_code, resolved_start, expires_on,
                        )
                    await con.execute(
                        _EXPIRE_CONTACT_SUBSCRIPTION_SQL, before["contactid"], keep_id
                    )
                    if opens_subscription and keep_id is None:
                        await con.execute(
                            _INSERT_SUBSCRIPTION_SQL,
                            before["contactid"], userprofile_id, plan_code,
                            resolved_start, expires_on, changed_by, reason,
                        )
        except Exception as exc:
            logger.error(
                "assign_plan | subscription history write failed | contact=%s | plan=%s | %s",
                before["contactid"], plan_code, exc, exc_info=True,
            )

    from app.etown.mytown.mytown_utils import prune_coverage_towns_to_plan
    for row in moved:
        try:
            await prune_coverage_towns_to_plan(row["providerid"], row["userprofileid"])
        except Exception as exc:
            logger.error(
                "assign_plan | coverage-town prune failed | provider=%s | plan=%s | %s",
                row["providerid"], plan_code, exc, exc_info=True,
            )

    logger.info(
        "assign_plan | %s | subject=%s | %s/%s -> %s/%s | expires=%s | by=%s | providers=%d",
        userprofile_id, before["subject"], before["plan_code"],
        before["subscription_status"], plan_code, status, expires_on, changed_by, len(moved),
    )
    return {
        "userprofileid": userprofile_id,
        "contactid": before["contactid"],
        "subject": before["subject"],
        "from": {"plan_code": before["plan_code"], "status": before["subscription_status"]},
        "to": {"plan_code": plan_code, "status": status,
               "started_on": resolved_start, "expires_on": expires_on},
        "providers_updated": len(moved),
        "providers": [
            {
                "userprofileid": row["userprofileid"],
                "providerid": row["providerid"],
                "from_plan_code": row["from_plan_code"],
                "from_status": row["from_status"],
            }
            for row in moved
        ],
    }


_LIFECYCLE_HISTORY: Dict[str, Tuple[str, str, str, str]] = {
    "deactivate": ("t.plan_code", "'free'",
                   "t.subscription_status", "t.subscription_status"),
    "reactivate": ("'free'", _EFFECTIVE_PLAN_CODE.format(a="t"),
                   "t.subscription_status", "t.subscription_status"),
    "delete":     ("t.plan_code", "'free'",
                   "t.subscription_status", "'expired'"),
}

_LIFECYCLE_REASON = {
    "deactivate": "account deactivated",
    "reactivate": "account reactivated",
    "delete": "account deleted",
}

_LIFECYCLE_RETIRE_CTE = """,
        updated AS (
            UPDATE etown_provider p
               SET subscription_status = 'expired',
                   plan_code = 'free',
                   plan_started_on = NULL,
                   plan_expires_on = NULL
              FROM targets t
             WHERE p.userprofileid = t.userprofileid
            RETURNING t.userprofileid
        ),
        retired_members AS (
            UPDATE etown_member m
               SET subscription_status = 'expired',
                   plan_code = 'free',
                   plan_started_on = NULL,
                   plan_expires_on = NULL
             WHERE m.{scope} = $1
               AND m.plan_code <> 'free'
            RETURNING m.memberid
        )"""
_LIFECYCLE_RETIRE_JOIN = "JOIN updated u ON u.userprofileid = t.userprofileid"


async def record_plan_lifecycle_event(
    mode: str,
    *,
    contact_id: Optional[str] = None,
    userprofile_id: Optional[str] = None,
    changed_by: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Write plan-change history for paid providers in a lifecycle event; a delete retires the plan and contact subscription.

    Args:
        mode: "delete", "deactivate" or "reactivate".
        contact_id: Scope to every provider this contact owns.
        userprofile_id: Scope to one provider; give exactly one of the two scopes.
        changed_by: Attribution on the history row; defaults to account_<mode>.
        reason: Free-text reason; defaults to the wording for mode.

    Returns:
        Dict of mode, rows, providerids recorded and userprofileids in scope.

    Raises:
        ValueError: Unknown mode, or neither or both scopes given.
    """
    if mode not in _LIFECYCLE_HISTORY:
        raise ValueError(f"{mode!r} is not a lifecycle mode")
    if bool(contact_id) == bool(userprofile_id):
        raise ValueError(
            "record_plan_lifecycle_event takes exactly one of contact_id / userprofile_id"
        )

    scope_column = "contactid" if contact_id else "userprofileid"
    subject = contact_id or userprofile_id
    from_plan, to_plan, from_status, to_status = _LIFECYCLE_HISTORY[mode]
    retire = mode == "delete"

    in_scope = await Database.fetch(
        f"SELECT userprofileid FROM etown_provider WHERE {scope_column} = $1", subject
    )
    for row in in_scope:
        invalidate_plan_cache(row["userprofileid"])

    moved = await Database.fetch(
        f"""
        WITH targets AS (
            SELECT userprofileid, providerid, contactid, plan_code, subscription_status,
                   plan_started_on, plan_expires_on, deactivated
              FROM etown_provider
             WHERE {scope_column} = $1
               AND plan_code <> 'free'
               {"AND subscription_status <> 'expired'" if retire else ""}
        ){_LIFECYCLE_RETIRE_CTE.format(scope=scope_column) if retire else ""}
        INSERT INTO notifications.etown_plan_change
            (userprofileid, providerid, contactid, from_plan_code, to_plan_code,
             from_status, to_status, plan_started_on, plan_expires_on, changed_by, reason)
        SELECT t.userprofileid, t.providerid, t.contactid, {from_plan}, {to_plan},
               {from_status}, {to_status}, t.plan_started_on, t.plan_expires_on, $2, $3
          FROM targets t
          {_LIFECYCLE_RETIRE_JOIN if retire else ""}
        RETURNING userprofileid, providerid
        """,
        subject,
        changed_by or f"account_{mode}",
        reason or _LIFECYCLE_REASON[mode],
    )

    if retire and contact_id:
        try:
            await Database.execute(_EXPIRE_CONTACT_SUBSCRIPTION_SQL, contact_id, None)
        except Exception as exc:
            logger.error(
                "record_plan_lifecycle_event | subscription history expire failed | contact=%s | %s",
                contact_id, exc, exc_info=True,
            )

    logger.info(
        "record_plan_lifecycle_event | %s | %s=%s | in_scope=%d | recorded=%d",
        mode, scope_column, subject, len(in_scope), len(moved),
    )
    return {
        "mode": mode,
        "rows": len(moved),
        "providerids": [r["providerid"] for r in moved if r["providerid"]],
        "userprofileids": [r["userprofileid"] for r in in_scope if r["userprofileid"]],
    }


async def promote_contact(
    contact_id: Optional[str],
    plan_code: str,
    *,
    provider_id: Optional[str] = None,
    status: str = "active",
    started_on=None,
    expires_on=None,
    changed_by: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Seed assign_plan from the provider_id, else the contact's oldest live provider or member row, and return its result.

    Returns:
        The assign_plan result with contactid set to the promoted contact.

    Raises:
        ValueError: Missing or mismatched ids, no live row, or plan_code/status rejected.
    """
    if not contact_id and not provider_id:
        raise ValueError("promote_contact needs a contact_id or a provider_id")

    if provider_id:
        named = await Database.fetchrow(
            f"""
            SELECT userprofileid, contactid
              FROM etown_provider
             WHERE providerid = $1 AND {_LIVE}
            """,
            provider_id,
        )
        if named is None or not named["userprofileid"]:
            raise ValueError(f"no live provider row for providerid {provider_id!r}")
        if contact_id and named["contactid"] and named["contactid"] != contact_id:
            raise ValueError(
                f"providerid {provider_id!r} belongs to contactid "
                f"{named['contactid']!r}, not {contact_id!r}"
            )
        seed_userprofileid = named["userprofileid"]
        contact_id = contact_id or named["contactid"]
    else:
        rows = await Database.fetch(
            f"""
            SELECT userprofileid, providerid
              FROM etown_provider
             WHERE contactid = $1 AND {_LIVE}
             ORDER BY creationdate ASC NULLS LAST, providerid ASC
            """,
            contact_id,
        )
        owned = [r for r in rows if r["userprofileid"]]
        if owned:
            seed_userprofileid = owned[0]["userprofileid"]
        else:
            seed_userprofileid = await Database.fetchval(
                f"""
                SELECT userprofileid
                  FROM etown_member
                 WHERE contactid = $1
                   AND userprofileid IS NOT NULL
                   AND {_LIVE}
                 ORDER BY dateofcreation ASC NULLS LAST, memberid ASC
                 LIMIT 1
                """,
                contact_id,
            )
            if not seed_userprofileid:
                raise ValueError(
                    f"no live provider or member row for contactid {contact_id!r}"
                )

    result = await assign_plan(
        seed_userprofileid, plan_code,
        status=status, started_on=started_on, expires_on=expires_on,
        changed_by=changed_by, reason=reason,
    )
    result["contactid"] = contact_id or result.get("contactid")
    return result

_DISABLE_PROVIDER_SQL = f"""
    WITH target AS (
        SELECT providerid, businessname, disabled AS was_disabled
          FROM etown_provider
         WHERE contactid = $1
           AND userprofileid = $2
           AND {_LIVE}
    ),
    updated AS (
        UPDATE etown_provider p
           SET disabled = true
          FROM target t
         WHERE p.providerid = t.providerid
           AND p.disabled IS DISTINCT FROM true
        RETURNING p.providerid
    )
    SELECT providerid, businessname, was_disabled
      FROM target
     ORDER BY providerid
"""


async def disable_provider(contact_id: str, userprofile_id: str) -> Dict[str, Any]:
    """Set disabled true on the live provider matching both contact_id and userprofile_id.

    Returns:
        Dict of found, the echoed ids, providers (with already_disabled) and providers_disabled.
    """
    rows = await Database.fetch(_DISABLE_PROVIDER_SQL, contact_id, userprofile_id)
    providers = [
        {
            "providerid": r["providerid"],
            "businessname": r["businessname"],
            "already_disabled": bool(r["was_disabled"]),
        }
        for r in rows
    ]
    result: Dict[str, Any] = {
        "found": bool(providers),
        "contactid": contact_id,
        "userprofileid": userprofile_id,
        "providers": providers,
        "providers_disabled": sum(1 for p in providers if not p["already_disabled"]),
    }
    logger.info(
        "disable_provider | contactid=%s | userprofileid=%s | matched=%d | disabled=%d",
        contact_id, userprofile_id, len(providers), result["providers_disabled"],
    )
    return result


_ENABLE_PROVIDERS_SQL = f"""
    WITH target AS (
        SELECT providerid, businessname, disabled AS was_disabled
          FROM etown_provider
         WHERE contactid = $1
           AND {_LIVE}
    ),
    updated AS (
        UPDATE etown_provider p
           SET disabled = false
          FROM target t
         WHERE p.providerid = t.providerid
           AND p.disabled IS DISTINCT FROM false
        RETURNING p.providerid
    )
    SELECT providerid, businessname, was_disabled
      FROM target
     ORDER BY providerid
"""


async def enable_providers_for_contact(contact_id: str) -> Dict[str, Any]:
    """Set disabled false on every live provider a contact owns.

    Returns:
        Dict of contactid, providers (with was_disabled) and providers_enabled count.
    """
    rows = await Database.fetch(_ENABLE_PROVIDERS_SQL, contact_id)
    providers = [
        {
            "providerid": r["providerid"],
            "businessname": r["businessname"],
            "was_disabled": bool(r["was_disabled"]),
        }
        for r in rows
    ]
    result: Dict[str, Any] = {
        "contactid": contact_id,
        "providers": providers,
        "providers_enabled": sum(1 for p in providers if p["was_disabled"]),
    }
    logger.info(
        "enable_providers_for_contact | contactid=%s | matched=%d | enabled=%d",
        contact_id, len(providers), result["providers_enabled"],
    )
    return result


_PLAN_CATALOGUE_COLUMNS = frozenset({
    "plan_name", "badge_label", "display_order", "is_active",
    "max_gallery_photos", "max_listings", "max_towns", "max_active_offers",
    "max_offers_per_month", "max_chaatie_generations_month", "max_posts_per_month",
    "whatsapp_quota_month", "verified_badge", "full_business_profile",
    "post_audience_targeting", "booking_insights", "profile_view_insights",
    "support_channel", "support_response_hours", "price_amount", "price_currency",
    "billing_period_months", "provider_onboard", "chattie_conversation",
    "post_public_feed",
})


async def update_subscription_plan(
    plan_code: str, updates: Dict[str, Any], *, changed_by: Optional[str] = None
) -> "tuple[Dict[str, Any], bool]":
    """Update plan_code's etown_subscription_plan row, inserting it when absent, and clear the plan cache.

    Returns:
        Tuple of the catalogue row after the write and whether it was created.

    Raises:
        ValueError: Empty or unknown fields, no plan_name for a create, or a rejected value.
    """
    if not updates:
        raise ValueError("no fields to update")
    unknown = set(updates) - _PLAN_CATALOGUE_COLUMNS
    if unknown:
        raise ValueError(f"not a plan catalogue column: {sorted(unknown)}")

    columns = list(updates)
    values = [updates[col] for col in columns]
    set_clause = ", ".join(f"{col} = ${i}" for i, col in enumerate(columns, start=2))
    try:
        row = await Database.fetchrow(
            f"""
            UPDATE public.etown_subscription_plan
               SET {set_clause}, updated_at = now()
             WHERE plan_code = $1
            RETURNING *
            """,
            plan_code, *values,
        )
    except asyncpg.PostgresError as exc:
        raise ValueError(f"{plan_code!r} update rejected: {exc}") from exc

    created = False
    if row is None:
        if not (updates.get("plan_name") or "").strip():
            raise ValueError(
                f"{plan_code!r} is not a plan in etown_subscription_plan yet; "
                "plan_name is required to create it"
            )
        insert_columns = ["plan_code", *columns]
        placeholders = ", ".join(f"${i}" for i in range(1, len(insert_columns) + 1))
        try:
            row = await Database.fetchrow(
                f"""
                INSERT INTO public.etown_subscription_plan ({", ".join(insert_columns)})
                VALUES ({placeholders})
                RETURNING *
                """,
                plan_code, *values,
            )
            created = True
        except asyncpg.UniqueViolationError:
            logger.info("plans: %s already exists; updating the existing row instead", plan_code)
            row = await Database.fetchrow(
                f"""
                UPDATE public.etown_subscription_plan
                   SET {set_clause}, updated_at = now()
                 WHERE plan_code = $1
                RETURNING *
                """,
                plan_code, *values,
            )
        except asyncpg.PostgresError as exc:
            raise ValueError(f"{plan_code!r} create rejected: {exc}") from exc

    invalidate_plan_cache()
    logger.info(
        "update_subscription_plan | %s | %s | %s | by=%s",
        plan_code, "created" if created else "updated", sorted(columns), changed_by,
    )
    return dict(row), created

_NUDGE_REMAINING_THRESHOLDS = (2, 0)


async def _maybe_nudge_usage(
    userprofile_id: str, metric: str, limit: int, used: int, plan: Dict[str, Any]
) -> None:
    """Dispatch a deduped plan-usage warning when remaining lands on 2 or 0, if PLAN_USAGE_NUDGE_ENABLED; never raises."""
    if not getattr(settings, "PLAN_USAGE_NUDGE_ENABLED", False):
        return
    remaining = limit - used
    if remaining not in _NUDGE_REMAINING_THRESHOLDS:
        return
    try:
        from app.etown.notifications.core.dispatcher import dispatch
        from app.etown.notifications.core.models import NotificationRequest, Recipient
        from app.etown.notifications.core.registry import NotificationType

        period = "today" if metric in DAILY_METRICS else "this month"
        await dispatch(NotificationRequest(
            notification_type=NotificationType.PLAN_USAGE_WARNING,
            recipient=Recipient(userprofile_id=userprofile_id),
            context={
                "plan_name": plan.get("plan_name"),
                "metric": metric,
                "limit": limit,
                "used": used,
                "remaining": remaining,
                "period": period,
            },
            dedupe_key=f"plan_usage:{userprofile_id}:{metric}:"
                       f"{'day' if metric in DAILY_METRICS else 'month'}:{remaining}",
        ))
    except Exception as exc:
        logger.warning("usage nudge failed for %s/%s: %s", userprofile_id, metric, exc)


async def release_consumed(userprofile_id: str, metric: str) -> None:
    """Hand back one claimed unit of a metered allowance for the current period; never raises."""
    period = _PERIOD_IST_DAY if metric in DAILY_METRICS else _PERIOD_MONTH
    try:
        await Database.execute(
            f"""
            UPDATE notifications.etown_provider_plan_usage
               SET used_count = used_count - 1, updated_at = now()
             WHERE userprofileid = $1
               AND metric = $2
               AND period_start = {period}
               AND used_count > 0
            """,
            userprofile_id, metric,
        )
    except Exception as exc:
        logger.warning("release_consumed failed for %s/%s: %s", userprofile_id, metric, exc)


async def resolve_provider_badges(
    userprofile_ids: Iterable[Optional[str]],
) -> Dict[str, Dict[str, Optional[str]]]:
    """Return provider_type, badge_label, premium_label and tier_rank per live provider userprofileid; empty map on failure."""
    ids = list({uid for uid in userprofile_ids if uid})
    if not ids:
        return {}
    try:
        rows = await Database.fetch_once(
            f"""
            SELECT DISTINCT ON (pr.userprofileid)
                   pr.userprofileid,
                   pr.providertype,
                   CASE
                       WHEN LOWER(TRIM(COALESCE(pr.providertype, '')))
                            IN ('verified', 'etown_verified')
                       THEN pl.badge_label
                   END AS badge_label,
                   CASE
                       WHEN LOWER(TRIM(COALESCE(pr.providertype, '')))
                            IN ('verified', 'etown_verified')
                        AND {_EFFECTIVE_PLAN_CODE.format(a='pr')} <> 'free'
                       THEN pl.badge_label
                   END AS premium_label,
                   CASE
                       WHEN LOWER(TRIM(COALESCE(pr.providertype, '')))
                            IN ('verified', 'etown_verified')
                        AND {_EFFECTIVE_PLAN_CODE.format(a='pr')} <> 'free'
                       THEN pl.display_order
                   END AS tier_rank
              FROM etown_provider pr
              LEFT JOIN public.etown_subscription_plan pl
                     ON pl.plan_code = {_EFFECTIVE_PLAN_CODE.format(a='pr')}
             WHERE pr.userprofileid = ANY($1::text[])
               AND COALESCE(pr.deleted, 'false') <> 'true'
             ORDER BY pr.userprofileid, pr.creationdate DESC NULLS LAST, pr.providerid
            """,
            ids,
        )
    except Exception as exc:
        logger.warning("resolve_provider_badges failed for %d ids: %s", len(ids), exc)
        return {}

    return {
        row["userprofileid"]: {
            "provider_type": premium_display(row["providertype"], row["premium_label"]),
            "badge_label": row["badge_label"],
            "premium_label": row["premium_label"],
            "tier_rank": row["tier_rank"],
        }
        for row in rows
    }


def _payment_field(value: Optional[str], *, bold: bool = False) -> str:
    """Return a payment field as escaped HTML, optionally bold, or an italic "not provided" placeholder."""
    if not value:
        return "<em>not provided</em>"
    escaped = html.escape(value)
    return f"<strong>{escaped}</strong>" if bold else escaped


def _payment_phone_html(mobilenumber: Optional[str]) -> str:
    """Return the mobile number as a tel: link HTML snippet showing it as submitted, or a placeholder."""
    if not mobilenumber:
        return "<em>not provided</em>"
    escaped = html.escape(mobilenumber)
    digits = re.sub(r"[^\d+]", "", mobilenumber)
    if not digits:
        return escaped
    return (
        f'<a href="tel:{html.escape(digits)}" '
        f'style="color:{_PAYMENT_LINK_COLOUR};text-decoration:none;">{escaped}</a>'
    )


async def _store_payment_screenshot(
    screenshot: Optional[str], contactid: Optional[str], reference: str
) -> Tuple[Optional[str], bool]:
    """Upload a base64 payment screenshot to Azure Blob, or pass a stored URL through; never raises.

    Args:
        screenshot: Base64 or data-URL screenshot, an already-stored URL, or None.
        contactid: Blob path folder; "unidentified" when None.
        reference: Submission reference used in log lines.

    Returns:
        Tuple of public URL (None if absent or failed) and whether bytes were uploaded.
    """
    if not screenshot:
        return None, False

    if is_stored_media_url(screenshot):
        return to_public_blob_url(screenshot), False

    try:
        raw = to_bytes(screenshot)
        if not raw:
            logger.warning("PLAN_PAYMENT | SCREENSHOT_EMPTY | ref=%s", reference)
            return None, False

        mime = detect_mime_type(raw)
        extension = "jpg" if "jpeg" in mime else mime.split("/")[-1]
        container = normalize_container_name(settings.AZURE_BLOB_CONTAINER or "etowndev")
        blob_name = (
            f"plan_payments/{contactid or 'unidentified'}/"
            f"{current_timestamp_prefix()}_PlanPayment.{extension}"
        )
        blob_path = await upload_blob_bytes(container, blob_name, raw, mime)
        if not blob_path:
            logger.warning("PLAN_PAYMENT | SCREENSHOT_UPLOAD_EMPTY | ref=%s", reference)
            return None, False

        logger.info(
            "PLAN_PAYMENT | SCREENSHOT_STORED | ref=%s | path=%s | bytes=%s | mime=%s",
            reference, blob_path, len(raw), mime,
        )
        return to_public_blob_url(blob_path), True
    except Exception as exc:
        logger.error(
            "PLAN_PAYMENT | SCREENSHOT_UPLOAD_FAILED | ref=%s | contactid=%s | %s",
            reference, contactid, exc, exc_info=True,
        )
        return None, False


async def _fetch_contact_name(contactid: str) -> Optional[str]:
    """Return the contact's non-empty etown_contact.firstname, or None when blank or the lookup fails."""
    try:
        return await Database.fetchval(
            "SELECT NULLIF(firstname, '') FROM etown_contact WHERE contactid = $1",
            str(contactid),
        )
    except Exception as exc:
        logger.error(
            "PLAN_PAYMENT | CONTACT_NAME_LOOKUP_FAILED | contactid=%s | %s",
            contactid, exc, exc_info=True,
        )
        return None


def _payment_name_html(contactidname: Optional[str], contactid: Optional[str]) -> str:
    """Return the name row HTML: the bold name, a nameless-account note, or a no-account note."""
    if contactidname:
        return _payment_field(contactidname, bold=True)
    if contactid:
        return "<em>no name on this account</em>"
    return "<em>no Etown account on this number</em>"


def _payment_screenshot_html(screenshot_url: Optional[str], submitted: bool) -> str:
    """Return the screenshot row HTML: a link with inline preview, "not provided", or an upload-failure note."""
    if not screenshot_url:
        if submitted:
            return (
                "<strong>Submitted, but could not be stored.</strong> Ask the payer to "
                "resend it - the upload failed and the image is not saved anywhere."
            )
        return "<em>not provided</em>"

    url = html.escape(screenshot_url, quote=True)
    return (
        f'<a href="{url}" style="color:{_PAYMENT_LINK_COLOUR};">{url}</a>'
        f'<br><a href="{url}"><img src="{url}" alt="Payment screenshot" '
        f'style="margin-top:12px;max-width:100%;border-radius:6px;"></a>'
    )


async def route_plan_payment_request(
    mobilenumber: str,
    screenshot: Optional[str] = None,
    paymentid: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve the payer from their mobile, store the screenshot, and email the payment to support plus extra recipients.

    Args:
        mobilenumber: Payer's mobile, normalised to 10 digits for the owner lookup.
        screenshot: Base64 or data-URL screenshot, an already-stored blob URL, or None.
        paymentid: Payer's own transaction reference, carried into the email as submitted.

    Returns:
        Dict of reference, routed_to, copied_to, contactid, contactidname, paymentid and screenshot fields.

    Raises:
        Exception: Whatever send_email raises; a failed send is not swallowed.
    """
    submitted_at = datetime.now(_IST)
    reference = submitted_at.strftime("%Y%m%d-%H%M%S")

    contactid: Optional[str] = None
    from app.etown.user.user_utils import fetch_phone_owner
    try:
        national = normalize_indian_mobile(mobilenumber)
        owner = await fetch_phone_owner(national) if national else None
        contactid = owner["contactid"] if owner else None
        logger.info(
            "PLAN_PAYMENT | CONTACT_RESOLVED | ref=%s | mobilenumber=%s | contactid=%s",
            reference, mobilenumber, contactid or "no match",
        )
    except Exception as exc:
        logger.error(
            "PLAN_PAYMENT | CONTACT_LOOKUP_FAILED | ref=%s | mobilenumber=%s | %s",
            reference, mobilenumber, exc, exc_info=True,
        )

    contactidname = await _fetch_contact_name(contactid) if contactid else None
    logger.info(
        "PLAN_PAYMENT | CONTACT_NAME_RESOLVED | ref=%s | contactid=%s | contactidname=%s",
        reference, contactid or "no match", contactidname or "none",
    )

    screenshot_url, uploaded = await _store_payment_screenshot(screenshot, contactid, reference)
    identifier = contactidname or mobilenumber
    contact_html = (
        _payment_field(contactid) if contactid
        else "<em>no Etown account on this number</em>"
    )

    recipients = [settings.SUPPORT_EMAIL, *settings.PLAN_PAYMENT_EXTRA_EMAILS]

    await send_email(
        to=recipients,
        subject=f"Plan payment request: {identifier} [{reference}]",
        body=render_email(
            heading="Plan payment request",
            preheader=f"{identifier} - reference {reference}",
            content=(
                paragraph(
                    f"Name: {_payment_name_html(contactidname, contactid)}<br>"
                    f"Mobile number: {_payment_phone_html(mobilenumber)}<br>"
                    f"contactid: {contact_html}<br>"
                    f"Payment id: {_payment_field(paymentid)}"
                )
                + paragraph(
                    "Payment screenshot: "
                    + _payment_screenshot_html(screenshot_url, bool(screenshot))
                )
                + paragraph(
                    f"Reference: <strong>{reference}</strong><br>"
                    f"Submitted: {submitted_at:%d %b %Y, %I:%M %p} IST",
                    muted=True,
                )
            ),
            footer="Routed automatically by the Etown API.",
        ),
    )

    logger.info(
        "PLAN_PAYMENT | ROUTED | ref=%s | to=%s | contactid=%s | contactidname=%s | "
        "paymentid=%s | screenshot=%s",
        reference, ", ".join(recipients), contactid, contactidname or "none",
        paymentid or "none", screenshot_url or "none",
    )
    return {
        "reference": reference,
        "routed_to": settings.SUPPORT_EMAIL,
        "copied_to": settings.PLAN_PAYMENT_EXTRA_EMAILS,
        "contactid": contactid,
        "contactidname": contactidname,
        "paymentid": paymentid,
        "screenshot_url": screenshot_url,
        "screenshot_uploaded": uploaded,
    }
