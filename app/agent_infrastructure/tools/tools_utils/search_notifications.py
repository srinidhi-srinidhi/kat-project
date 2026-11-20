"""Search Notifications Tool - WhatsApp nudges to unverified providers surfaced in member searches.

Background helper for the search tools: sends a WhatsApp nudge to the top unverified database providers in a member's results, enforces a per-mobile cooldown, retries retryable failures in-process and through a durable sweeper worker, and logs every candidate to notifications.etown_unverified_provider_wa_nudge.

Features:
    - Cooldown Enforcement: 15-day per-provider (per mobile number) cooldown check
    - Top-5 Cap: At most 5 unverified providers are nudged per search (top by relevance/distance)
    - Bounded Concurrency: Sends are throttled by a semaphore to avoid self-inflicted rate limits
    - In-Process Retry: Retryable send failures are retried with exponential backoff
    - Durable Retry Sweeper: Periodic worker re-attempts retryable failures across restarts
    - Background Execution: Safe for use with asyncio.create_task() — never raises
    - DB-only Filtering: Only processes database-sourced results (not Google Maps web results)
    - Unverified Filter: Only notifies providers whose Etown_providertype is not verified/etown_verified
    - Full-Funnel Logging: One tracking row per candidate: sent, failed, or a skipped_* status
    - Blocked-nudge Fallback: Re-sends a utility template when Meta blocks the marketing nudge
    - Mobile Guard: Providers with no mobile number on record are filtered out before _attempt_send

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    notify_unverified_providers_background(): Entry point for the per-search background task
    handle_blocked_nudge_fallback(): Webhook-triggered utility re-send when Meta blocks the nudge
    start_unverified_retry_worker(): Start the durable retry sweeper (call on app startup)
    stop_unverified_retry_worker(): Stop the durable retry sweeper (call on app shutdown)
    _send_one(): Builds send args for a single search-result candidate (parallel worker)
    _attempt_send(): Normalise + cooldown-check + send-with-retry + log for one mobile
    _send_with_retry(): Bounded-concurrency send with exponential-backoff retry + classification
    _classify_error(): Map an exception to (retryable, reason) for retry/logging decisions
    _backoff_seconds(): Exponential backoff with jitter for in-process retries
    _retry_sweep_once(): Single durable retry pass over retryable failures
    _retry_sweep_loop(): Long-lived loop running _retry_sweep_once() on an interval until cancelled
    _is_in_cooldown(): Checks if a provider was notified within the last 15 days
    _log_wa_nudge(): Inserts a send record into the tracking table
    _extract_error_codes(): Pull integer error codes from a Meta status event's errors list

Dependencies:
    - asyncio: Async execution context, semaphore, sweeper task
    - random: Backoff jitter
    - logging: Diagnostic logging
    - app.db.client: Database access (Database.fetch, Database.fetchrow, Database.execute)
    - app.utils.whatsapp: unverified-provider nudge and fallback senders, error classes, normalize_msisdn
    - search_provider: is_provider_verified() utility

Updates:
    25-Jun-2026: Initial creation — search-triggered WhatsApp nudge for unverified providers
    29-Jun-2026: Resilient delivery with retry and a durable sweeper; full-funnel audit logging with Meta wamid.
    01-Jul-2026: Added handle_blocked_nudge_fallback(), re-sending a utility template when Meta blocks delivery.
    20-Aug-2026: The unverified filter reads Etown_providertype, matching the renamed search results.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from app.db.client import Database
from app.utils.whatsapp import (
    WhatsAppAuthError,
    WhatsAppError,
    WhatsAppInvalidRecipientError,
    WhatsAppRateLimitError,
    normalize_msisdn,
    send_unverified_provider_fallback_whatsapp_notification_raising,
    send_unverified_provider_whatsapp_notification_raising,
)

logger = logging.getLogger(__name__)

_COOLDOWN_DAYS = 15
_MAX_CANDIDATES_PER_SEARCH = 5

_SEND_CONCURRENCY = 2
_MAX_INPROCESS_RETRIES = 2
_BACKOFF_BASE_SECONDS = 1.0
_send_semaphore = asyncio.Semaphore(_SEND_CONCURRENCY)

_SWEEP_INTERVAL_SECONDS = 600
_RETRY_WINDOW_HOURS = 12
_MAX_RETRYABLE_ATTEMPTS = 4
_sweeper_task: asyncio.Task | None = None

_FALLBACK_TRIGGER_CODES = {131049, 131050, 131047}

_PERMANENT_UNDELIVERABLE_CODES = {131026}

_COOLDOWN_SQL = """
    SELECT 1
    FROM notifications.etown_unverified_provider_wa_nudge
    WHERE mobilenumber = $1
      AND sent_at > NOW() - INTERVAL '15 days'
      AND status = 'sent'
    LIMIT 1
"""
_INSERT_SQL = """
    INSERT INTO notifications.etown_unverified_provider_wa_nudge
        (providerservicesid, mobilenumber, businessname, status, error_detail, wamid, triggered_by_contactid)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
"""
_RETRY_CANDIDATES_SQL = """
    WITH latest AS (
        SELECT DISTINCT ON (mobilenumber)
            id, mobilenumber, providerservicesid, businessname, status, error_detail, sent_at,
            triggered_by_contactid
        FROM notifications.etown_unverified_provider_wa_nudge
        WHERE sent_at > NOW() - INTERVAL '12 hours'
          AND status IN ('sent', 'failed')
        ORDER BY mobilenumber, sent_at DESC
    )
    SELECT
        l.id,
        l.mobilenumber,
        l.providerservicesid,
        l.businessname,
        l.triggered_by_contactid,
        (
            SELECT COUNT(*)
            FROM notifications.etown_unverified_provider_wa_nudge c
            WHERE c.mobilenumber = l.mobilenumber
              AND c.status = 'failed'
              AND c.error_detail LIKE 'retryable:%'
              AND c.sent_at > NOW() - INTERVAL '12 hours'
        ) AS retryable_attempts
    FROM latest l
    WHERE l.status = 'failed'
      AND l.error_detail LIKE 'retryable:%'
      AND NOT EXISTS (
            SELECT 1
            FROM notifications.etown_unverified_provider_wa_nudge s
            WHERE s.mobilenumber = l.mobilenumber
              AND s.status = 'sent'
              AND s.sent_at > NOW() - INTERVAL '15 days'
      )
"""
_ABANDON_SQL = """
    UPDATE notifications.etown_unverified_provider_wa_nudge
    SET error_detail = 'permanent:exhausted'
    WHERE id = $1
"""
_MARK_UNDELIVERABLE_SQL = """
    UPDATE notifications.etown_unverified_provider_wa_nudge
    SET status = 'failed', error_detail = 'permanent:undeliverable'
    WHERE wamid = $1 AND status = 'sent'
"""

_LOOKUP_NUDGE_BY_WAMID_SQL = """
    SELECT providerservicesid, mobilenumber, businessname, triggered_by_contactid
    FROM notifications.etown_unverified_provider_wa_nudge
    WHERE wamid = $1 AND status = 'sent'
    ORDER BY sent_at DESC
    LIMIT 1
"""
_CLAIM_FALLBACK_SQL = """
    INSERT INTO notifications.etown_unverified_provider_wa_nudge
        (providerservicesid, mobilenumber, businessname, status, fallback_for_wamid, triggered_by_contactid)
    VALUES ($1, $2, $3, 'fallback_pending', $4, $5)
    ON CONFLICT (fallback_for_wamid) WHERE fallback_for_wamid IS NOT NULL DO NOTHING
    RETURNING id
"""
_UPDATE_FALLBACK_ROW_SQL = """
    UPDATE notifications.etown_unverified_provider_wa_nudge
    SET status = $1, error_detail = $2, wamid = $3
    WHERE id = $4
"""


async def _is_in_cooldown(normalised_mobile: str) -> bool:
    """Return True when this normalised mobile was successfully nudged within the cooldown window."""
    row = await Database.fetchrow(_COOLDOWN_SQL, normalised_mobile)
    return row is not None


async def _log_wa_nudge(
    providerservicesid: str,
    normalised_mobile: str,
    businessname: str,
    status: str,
    error_detail: str | None = None,
    wamid: str | None = None,
    triggered_by_contactid: str | None = None,
) -> None:
    """Insert a nudge funnel row, logging a warning instead of raising on failure.

    Args:
        providerservicesid: Provider service record id.
        normalised_mobile: E.164 mobile, or the raw value for skipped_invalid_mobile.
        businessname: Provider business name.
        status: sent, failed, or a skipped_* status.
        error_detail: 'retryable:<reason>' or 'permanent:<reason>' on failure, else None.
        wamid: Meta message id on a successful send, else None.
        triggered_by_contactid: Member whose search triggered the nudge, used by the failsafe.
    """
    try:
        await Database.execute(
            _INSERT_SQL,
            providerservicesid,
            normalised_mobile,
            businessname or "",
            status,
            error_detail,
            wamid,
            triggered_by_contactid,
        )
    except Exception as exc:
        logger.warning(
            "[SearchNotif] Failed to log nudge for %s: %s",
            normalised_mobile,
            exc,
        )


def _classify_error(exc: BaseException) -> tuple[bool, str]:
    """Map a WhatsApp send exception to (retryable, reason token)."""
    if isinstance(exc, WhatsAppRateLimitError):
        return True, "rate_limit"
    if isinstance(exc, WhatsAppInvalidRecipientError):
        return False, "invalid_recipient"
    if isinstance(exc, WhatsAppAuthError):
        return False, "auth"
    if isinstance(exc, WhatsAppError):
        return True, "api_or_network"
    return True, "unexpected"


def _backoff_seconds(attempt: int) -> float:
    """Return the jittered exponential backoff in seconds after the given zero-based failed attempt."""
    return _BACKOFF_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)


async def _send_with_retry(
    raw_mobile: str, businessname: str
) -> tuple[str, str | None, str | None]:
    """Send one nudge under the semaphore, retrying retryable errors with backoff.

    Args:
        raw_mobile: Provider mobile, normalised again downstream.
        businessname: Provider business name for the template.

    Returns:
        Tuple of status ('sent', 'skipped', 'failed'), error_detail tag, and wamid.
    """
    last_reason = "unexpected"
    for attempt in range(_MAX_INPROCESS_RETRIES + 1):
        try:
            async with _send_semaphore:
                sent, wamid = await send_unverified_provider_whatsapp_notification_raising(
                    mobile_number=raw_mobile,
                    business_name=businessname,
                )
            if sent:
                return "sent", None, wamid
            return "skipped", None, None
        except Exception as exc:
            retryable, reason = _classify_error(exc)
            last_reason = reason
            logger.warning(
                "[SearchNotif] WA nudge attempt %d/%d failed for %s (reason=%s, retryable=%s): %s",
                attempt + 1,
                _MAX_INPROCESS_RETRIES + 1,
                raw_mobile,
                reason,
                retryable,
                exc,
            )
            if not retryable:
                return "failed", f"permanent:{reason}", None
            if attempt < _MAX_INPROCESS_RETRIES:
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            return "failed", f"retryable:{reason}", None
    return "failed", f"retryable:{last_reason}", None


async def _attempt_send(
    raw_mobile: str,
    businessname: str,
    providerservicesid: str,
    triggered_by_contactid: str | None = None,
) -> None:
    """Normalise the mobile, apply the cooldown, send with retry, and log the outcome row."""
    try:
        normalised = normalize_msisdn(raw_mobile)
    except ValueError as exc:
        logger.debug(
            "[SearchNotif] Skipping %s — invalid mobile %r: %s",
            businessname,
            raw_mobile,
            exc,
        )
        await _log_wa_nudge(
            providerservicesid=providerservicesid,
            normalised_mobile=raw_mobile,
            businessname=businessname,
            status="skipped_invalid_mobile",
            error_detail=str(exc),
            triggered_by_contactid=triggered_by_contactid,
        )
        return

    if await _is_in_cooldown(normalised):
        logger.debug(
            "[SearchNotif] Skipping %s (%s) — within %d-day cooldown",
            businessname,
            normalised,
            _COOLDOWN_DAYS,
        )
        await _log_wa_nudge(
            providerservicesid=providerservicesid,
            normalised_mobile=normalised,
            businessname=businessname,
            status="skipped_cooldown",
            triggered_by_contactid=triggered_by_contactid,
        )
        return

    logger.info(
        "[SearchNotif] Sending WA nudge to unverified provider: %s (%s)",
        businessname,
        normalised,
    )

    status, error_detail, wamid = await _send_with_retry(raw_mobile, businessname)

    if status == "skipped":
        await _log_wa_nudge(
            providerservicesid=providerservicesid,
            normalised_mobile=normalised,
            businessname=businessname,
            status="skipped_template_disabled",
            triggered_by_contactid=triggered_by_contactid,
        )
        return

    await _log_wa_nudge(
        providerservicesid=providerservicesid,
        normalised_mobile=normalised,
        businessname=businessname,
        status=status,
        error_detail=error_detail,
        wamid=wamid,
        triggered_by_contactid=triggered_by_contactid,
    )


async def _send_one(
    provider: dict[str, Any],
    triggered_by_contactid: str | None = None,
) -> None:
    """Attempt the nudge for one search-result provider, logging a permanent failure row on unexpected errors."""
    raw_mobile: str = provider["mobilenumber"]
    businessname: str = provider.get("businessname") or ""
    providerservicesid: str = provider.get("providerservicesid") or provider.get("id") or ""
    try:
        await _attempt_send(raw_mobile, businessname, providerservicesid, triggered_by_contactid)
    except Exception as exc:
        logger.exception(
            "[SearchNotif] Unexpected exception sending WA nudge for %s (%s): %s",
            businessname,
            raw_mobile,
            exc,
        )
        try:
            await _log_wa_nudge(
                providerservicesid=providerservicesid,
                normalised_mobile=raw_mobile,
                businessname=businessname,
                status="failed",
                error_detail=f"permanent:unexpected:{type(exc).__name__}",
                triggered_by_contactid=triggered_by_contactid,
            )
        except Exception as log_exc:
            logger.warning(
                "[SearchNotif] Failed to log unexpected send failure for %s (%s): %s",
                businessname,
                raw_mobile,
                log_exc,
            )


async def notify_unverified_providers_background(
    results: list[dict[str, Any]],
    triggered_by_contactid: str | None = None,
) -> None:
    """Nudge up to five unverified database providers from search results over WhatsApp, sequentially with a 10s gap.

    Args:
        results: Final search result list from handle_search_provider.
        triggered_by_contactid: Member who ran the search, stored on each funnel row.
    """
    try:
        from app.agent_infrastructure.tools.tools_utils.search_provider import (
            is_provider_verified,
        )
        candidates = [
            r for r in results
            if r.get("source") == "database"
            and not is_provider_verified(r.get("Etown_providertype", ""))
            and r.get("mobilenumber")
        ]

        if not candidates:
            return

        candidates = candidates[:_MAX_CANDIDATES_PER_SEARCH]
        logger.debug(
                "[SearchNotif] %d unverified database provider(s) to nudge (sequential with 10s delay)",
            len(candidates),
        )

        results = []
        for i, p in enumerate(candidates):
            if i > 0:
                await asyncio.sleep(10)
            try:
                await _send_one(p, triggered_by_contactid)
                results.append(None)
            except Exception as e:
                logger.warning("unverified-provider nudge failed for one candidate", exc_info=True)
                results.append(e)

        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                provider = candidates[idx]
                logger.exception(
                    "[SearchNotif] WA nudge task for %s (%s) failed unexpectedly: %s",
                    provider.get("businessname"),
                    provider.get("mobilenumber"),
                    result,
                )

    except Exception as exc:
        logger.exception("[SearchNotif] Unhandled error in nudge background task: %s", exc)


async def _retry_sweep_once() -> None:
    """Retry recent retryable nudge failures once, marking mobiles over the attempt budget as exhausted."""
    try:
        rows = await Database.fetch(_RETRY_CANDIDATES_SQL)
    except Exception as exc:
        logger.warning("[SearchNotif] retry sweep candidate query failed: %s", exc)
        return

    if not rows:
        return

    logger.info("[SearchNotif] retry sweep: %d retryable candidate(s)", len(rows))
    for row in rows:
        mobile = row["mobilenumber"]
        attempts = row["retryable_attempts"] or 0
        if attempts >= _MAX_RETRYABLE_ATTEMPTS:
            try:
                await Database.execute(_ABANDON_SQL, row["id"])
            except Exception as exc:
                logger.warning("[SearchNotif] failed to mark %s exhausted: %s", mobile, exc)
            else:
                logger.info(
                    "[SearchNotif] abandoning %s after %d retryable attempt(s)",
                    mobile,
                    attempts,
                )
            continue

        await _attempt_send(
            raw_mobile=mobile,
            businessname=row["businessname"] or "",
            providerservicesid=str(row["providerservicesid"]),
            triggered_by_contactid=row["triggered_by_contactid"],
        )


async def _retry_sweep_loop() -> None:
    """Run the retry sweep on a fixed interval until cancelled."""
    logger.info(
        "[SearchNotif] retry sweeper loop running (interval=%ds, window=%dh, cap=%d)",
        _SWEEP_INTERVAL_SECONDS,
        _RETRY_WINDOW_HOURS,
        _MAX_RETRYABLE_ATTEMPTS,
    )
    while True:
        try:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            await _retry_sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[SearchNotif] retry sweep pass failed: %s", exc)


async def start_unverified_retry_worker() -> None:
    """Start the retry sweeper task unless one is already running."""
    global _sweeper_task
    if _sweeper_task is None or _sweeper_task.done():
        _sweeper_task = asyncio.create_task(_retry_sweep_loop())
        logger.info("[SearchNotif] Unverified-provider retry sweeper started")


async def stop_unverified_retry_worker() -> None:
    """Cancel and await the retry sweeper task if it is running."""
    global _sweeper_task
    if _sweeper_task is not None:
        _sweeper_task.cancel()
        try:
            await _sweeper_task
        except asyncio.CancelledError:
            logger.debug("unverified-provider retry sweeper cancelled during shutdown")
        except Exception as exc:
            logger.debug("[SearchNotif] retry sweeper stop raised: %s", exc)
        _sweeper_task = None
        logger.info("[SearchNotif] Unverified-provider retry sweeper stopped")


def _extract_error_codes(errors: list[dict[str, Any]] | None) -> set[int]:
    """Return the integer error codes found in a Meta status event's errors list."""
    codes: set[int] = set()
    for err in errors or []:
        if not isinstance(err, dict):
            continue
        try:
            codes.add(int(err.get("code")))
        except (TypeError, ValueError):
            logger.debug("WhatsApp status event carried a non-integer error code; skipping it")
            continue
    return codes


async def handle_blocked_nudge_fallback(
    wamid: str | None,
    errors: list[dict[str, Any]] | None,
) -> None:
    """Handle a failed nudge status: mark undeliverable codes, or claim and send the utility failsafe once per wamid.

    Args:
        wamid: Message id from the failed status event.
        errors: The status event's errors list of dicts with a code.
    """
    try:
        if not wamid:
            return
        error_codes = _extract_error_codes(errors)
        if error_codes & _PERMANENT_UNDELIVERABLE_CODES:
            updated = await Database.execute(_MARK_UNDELIVERABLE_SQL, wamid)
            logger.info(
                "[SearchNotif] Nudge wamid=%s marked permanent:undeliverable (codes=%s, result=%s)",
                wamid,
                error_codes & _PERMANENT_UNDELIVERABLE_CODES,
                updated,
            )
            return

        if not (error_codes & _FALLBACK_TRIGGER_CODES):
            return

        row = await Database.fetchrow(_LOOKUP_NUDGE_BY_WAMID_SQL, wamid)
        if row is None:
            return

        providerservicesid = row["providerservicesid"]
        mobile = row["mobilenumber"]
        businessname = row["businessname"] or ""
        profile_id = row["triggered_by_contactid"] or str(providerservicesid)

        claim = await Database.fetchrow(
            _CLAIM_FALLBACK_SQL, providerservicesid, mobile, businessname, wamid, row["triggered_by_contactid"]
        )
        if claim is None:
            logger.debug("[SearchNotif] Fallback already claimed for wamid=%s — skipping", wamid)
            return
        claim_id = claim["id"]

        logger.info(
            "[SearchNotif] Marketing nudge blocked (wamid=%s) — sending utility failsafe to %s",
            wamid,
            mobile,
        )

        try:
            async with _send_semaphore:
                sent, new_wamid = await send_unverified_provider_fallback_whatsapp_notification_raising(
                    mobile_number=mobile,
                    business_name=businessname,
                    profile_id=profile_id,
                )
        except Exception as exc:
            retryable, reason = _classify_error(exc)
            tag = "retryable" if retryable else "permanent"
            logger.warning(
                "[SearchNotif] Utility failsafe send failed for %s (reason=%s): %s",
                mobile,
                reason,
                exc,
            )
            await Database.execute(
                _UPDATE_FALLBACK_ROW_SQL, "fallback_failed", f"{tag}:{reason}", None, claim_id
            )
            return

        if sent:
            await Database.execute(
                _UPDATE_FALLBACK_ROW_SQL, "fallback_sent", None, new_wamid, claim_id
            )
            logger.info("[SearchNotif] Utility failsafe sent to %s (wamid=%s)", mobile, new_wamid)
        else:
            await Database.execute(
                _UPDATE_FALLBACK_ROW_SQL, "fallback_skipped_template_disabled", None, None, claim_id
            )
    except Exception as exc:
        logger.exception("[SearchNotif] handle_blocked_nudge_fallback failed (wamid=%s): %s", wamid, exc)
