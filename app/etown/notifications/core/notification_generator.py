"""Notification Copy Generator - LLM-written copy pool for the scheduled digest pushes.

Notification service that generates digest title and body variants with the creative gpt-4o-mini client, stores them in etown_notification_pool, and picks one per user at send time without an LLM call, falling back to the hardcoded line on any failure.

Features:
    - Opt-in: the whole pool is gated by NOTIFICATIONS_LLM_DIGEST_ENABLED
    - Rotating copy pool: Pre-generated LLM variants stored in Postgres and served without LLM calls at send time
    - Morning, midday and evening digests: Separate per-daypart copy briefs and type keys
    - Curiosity-first copy: Zomato-style teasers with optional {n} offer count token substitution
    - Graceful degradation: Falls back to hardcoded line on any pool or LLM failure
    - Pool refresh: Weekly regeneration job replaces the active pool and prunes old variants
    - Variant selection: Per-user deterministic rotation by hashing (userprofile_id + date)

Classes:
    No classes defined (module contains module-level constants and utility functions only)

Functions:
    ensure_copy_pool_table(): Create etown_notification_pool table and index if absent
    _build_prompt(): Fill the digest-copy prompt template with count/daypart/brief
    _parse_variants(): Best-effort parse of the LLM output into [{title, body}, ...]
    generate_digest_copy_variants(): Call gpt-4o-mini to generate a list of copy variants
    refresh_copy_pool(): Replace the active pool with freshly generated LLM variants
    get_active_pool(): Retrieve all active copy variants for a given type key from DB
    select_variant(): Pick one variant for a user via deterministic hash rotation

Dependencies:
    - json: Variant list parsing from LLM JSON output
    - zlib: Deterministic hash for per-user variant selection
    - datetime: Date-based variant rotation
    - app.core.config: NOTIFICATIONS_LLM_DIGEST_ENABLED and copy pool size settings
    - app.core.logging: Debug/info/warning logging
    - app.db.client: Database operations for pool table DDL and variant CRUD
    - app.agent_infrastructure.infrastructure.llm_clients: gpt4o_mini_creative, imported lazily

Updates:
    05-Jun-2026: Initial LLM copy pool; added evening digest type key and per-daypart briefs.
    25-Jun-2026: Added midday digest type key and "midday" daypart brief.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
"""

from __future__ import annotations

import json
import zlib
from datetime import date
from typing import Dict, List, Optional

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database

MORNING_DIGEST = "morning_digest"
EVENING_DIGEST = "evening_digest"
MIDDAY_DIGEST = "midday_digest"
RATING_REMINDER = "rating_reminder"
FEED_NO_POST_NUDGE = "feed_no_post_nudge"

_POOL_DDL = """
CREATE TABLE IF NOT EXISTS etown_notification_pool (
    id          bigserial   PRIMARY KEY,
    type_key    text        NOT NULL,
    title       text        NOT NULL,
    body        text        NOT NULL,
    is_active   boolean     NOT NULL DEFAULT true,
    created_on  timestamptz NOT NULL DEFAULT now()
)
"""

_POOL_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_notification_pool_type_active
    ON etown_notification_pool (type_key, is_active)
"""


async def ensure_copy_pool_table() -> None:
    """Create the copy-pool table and its index if they do not exist."""
    await Database.execute(_POOL_DDL)
    await Database.execute(_POOL_INDEX_DDL)


_DAYPART_BRIEF = {
    "morning": (
        "Goal: get the user to open the app and explore today's new offers from "
        "their circle.\n"
        "Tone: bright, fresh-start energy — the user just woke up. A cheerful "
        "'kick off your day by seeing what's new' nudge.\n"
        "Counts: MOST variants must NOT mention a number. In a FEW, you MAY put the "
        "literal token {n} exactly where a count of new offers would go (e.g. "
        "\"{n} fresh finds waiting\"). Use {n} sparingly."
    ),
    "evening": (
        "Goal: get the user to reopen the app and rediscover what etown can do — "
        "its features: browsing local services & businesses, the community feed, "
        "booking a service, chatting with providers, and staying connected with "
        "their circle. Spotlight the features and the fun of exploring the "
        "community. Do NOT focus on offers, deals, discounts or prices.\n"
        "Tone: relaxed, wind-down evening energy — a gentle 'you've got a minute, "
        "see what else etown can do for you' nudge.\n"
        "Counts: do NOT mention any number or count — this digest goes to everyone, "
        "so no per-user figure is available. Keep it about curiosity and discovery."
    ),
    "midday": (
        "Goal: get the user to take a short break from whatever they are doing and "
        "explore what etown offers — its features: browsing local services & businesses, "
        "the community feed, booking a service, chatting with providers, and staying "
        "connected with their circle. Spotlight the features and the joy of discovering "
        "the local community. Do NOT focus on offers, deals, discounts or prices.\n"
        "Tone: fresh, upbeat, curious, light — a friendly 'you've got a minute, see what "
        "etown can do for you right now' nudge.\n"
        "Time of day: this push can land at any hour, so the copy must be completely "
        "time-neutral. NEVER mention or hint at a time of day or a meal — no 'lunch', "
        "'lunchtime', 'lunch break', 'midday', 'noon', 'afternoon', 'morning', 'evening', "
        "'night', 'tonight', 'breakfast', 'brunch' or 'dinner', and no wording that only "
        "makes sense at one hour (e.g. 'take a breather over your meal').\n"
        "Counts: do NOT mention any number or count — this digest goes to everyone, "
        "so no per-user figure is available. Keep it about curiosity and discovery."
    ),
    "rating_reminder": (
        "Goal: prompt the user to rate a service they booked yesterday.\n"
        "Personalisation: use the EXACT token {service_name} where the type of service goes "
        "(e.g. \"plumber\", \"electrician\", \"cleaning\") and the EXACT token {provider_name} "
        "where the provider's business name goes. These tokens will be replaced with real "
        "values at send time — preserve them verbatim in the JSON output.\n"
        "Tone: warm, appreciative, community-first. The user's honest feedback helps their "
        "neighbours find great local services. A gentle nudge, not a demand.\n"
        "Counts: no numbers. Do NOT mention any count, price, or discount."
    ),
    "feed_no_post_nudge": (
        "Goal: the user just opened and browsed the community feed but has not posted "
        "anything. Nudge them to share an update, photo, or thought with their local "
        "community — turn a lurker into a poster.\n"
        "Tone: friendly, encouraging, low-pressure — 'you've got something worth sharing'. "
        "Make posting feel easy, quick and fun, not a chore.\n"
        "Counts: no numbers. Do NOT mention any count, price, or discount."
    ),
}

_DAYPART_BANNED: Dict[str, tuple] = {
    "midday": (
        "lunch", "midday", "mid-day", "noon", "afternoon", "brunch", "breakfast",
        "dinner", "supper", "morning", "evening", "tonight", "night", "siesta",
    ),
}
_DAYPART_LABEL = {"midday": "anytime"}

_DIGEST_COPY_PROMPT = """You are a witty growth-marketing copywriter for "etown", a \
hyperlocal community app where people discover local services and businesses, browse and \
book services, follow a community feed, chat with providers, and stay connected with \
their circle.

Write __COUNT__ DISTINCT __DAYPART__ push-notification variants for the etown app.

__BRIEF__

Style (think Zomato / Swiggy notifications):
- Curiosity-first: tease, don't tell. Make them want to tap to find out.
- Punchy, playful, warm. Light wit and wordplay welcome.
- At most ONE emoji per variant, only when it genuinely adds punch.
- Title: max ~40 characters. Body: max ~120 characters.
- No spammy ALL CAPS, no "!!!", no fake urgency, no lies.
- Do NOT invent specific discounts, prices or brand names.

Return ONLY a JSON array of exactly __COUNT__ objects, each {"title": "...", "body": "..."}.
No prose, no markdown, no code fences."""


def _build_prompt(count: int, daypart: str = "morning") -> str:
    """Fill the digest copy prompt with the variant count, daypart label and daypart brief."""
    brief = _DAYPART_BRIEF.get(daypart, _DAYPART_BRIEF["morning"])
    return (
        _DIGEST_COPY_PROMPT
        .replace("__COUNT__", str(count))
        .replace("__DAYPART__", _DAYPART_LABEL.get(daypart, daypart))
        .replace("__BRIEF__", brief)
    )


def _parse_variants(raw: str) -> List[Dict[str, str]]:
    """Slice the JSON array out of model output into truncated title/body dicts, or return [] on any problem."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        logger.warning("notification generator: model output was not a JSON array; returning no variants", exc_info=True)
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        body = str(item.get("body", "")).strip()
        if title and body:
            out.append({"title": title[:120], "body": body[:200]})
    return out


async def generate_digest_copy_variants(count: int, daypart: str = "morning") -> List[Dict[str, str]]:
    """Generate digest copy variants with the LLM, dropping daypart-banned wording; return [] on failure."""
    from app.agent_infrastructure.infrastructure.llm_clients import gpt4o_mini_creative

    try:
        resp = await gpt4o_mini_creative.ainvoke(_build_prompt(count, daypart))
    except Exception as exc:
        logger.error("%s digest copy generation failed: %s", daypart, exc, exc_info=True)
        return []
    variants = _parse_variants(getattr(resp, "content", "") or "")
    banned = _DAYPART_BANNED.get(daypart)
    if banned:
        kept = [v for v in variants
                if not any(w in f"{v['title']} {v['body']}".lower() for w in banned)]
        if len(kept) < len(variants):
            logger.info("%s digest copy generation: dropped %d variant(s) with banned wording",
                        daypart, len(variants) - len(kept))
        variants = kept
    logger.info("%s digest copy generation: parsed %d/%d variant(s)", daypart, len(variants), count)
    return variants


async def refresh_copy_pool(
    type_key: str = MORNING_DIGEST,
    count: Optional[int] = None,
    daypart: Optional[str] = None,
) -> int:
    """Generate and activate a fresh variant pool for a digest type, prune old inactive rows, and return the count."""
    await ensure_copy_pool_table()
    if count is None:
        count = int(getattr(settings, "MORNING_DIGEST_COPY_POOL_SIZE", 30))
    if daypart is None:
        if type_key == EVENING_DIGEST:
            daypart = "evening"
        elif type_key == MIDDAY_DIGEST:
            daypart = "midday"
        else:
            daypart = "morning"

    variants = await generate_digest_copy_variants(count, daypart)
    if variants and len(variants) < count:
        top_up = await generate_digest_copy_variants(count - len(variants), daypart)
        seen = {v["title"].lower() for v in variants}
        variants += [v for v in top_up if v["title"].lower() not in seen]
    if not variants:
        logger.warning(
            "refresh_copy_pool(%s): no variants generated; keeping existing pool", type_key
        )
        return 0

    await Database.execute(
        "UPDATE etown_notification_pool SET is_active = false, modifieddate = NOW() "
        "WHERE type_key = $1 AND is_active = true",
        type_key,
    )
    for v in variants:
        await Database.execute(
            "INSERT INTO etown_notification_pool (type_key, title, body) "
            "VALUES ($1, $2, $3)",
            type_key, v["title"], v["body"],
        )
    logger.info("refresh_copy_pool(%s): activated %d fresh variant(s)", type_key, len(variants))
    retention_days = int(getattr(settings, "MORNING_DIGEST_COPY_RETENTION_DAYS", 30))
    if retention_days > 0:
        try:
            status = await Database.execute(
                "DELETE FROM etown_notification_pool "
                "WHERE type_key = $1 AND is_active = false "
                "AND created_on < now() - (($2::int) * INTERVAL '1 day')",
                type_key, retention_days,
            )
            logger.info(
                "refresh_copy_pool(%s): pruned inactive rows older than %d day(s) (%s)",
                type_key, retention_days, status,
            )
        except Exception as exc:
            logger.warning("refresh_copy_pool(%s): prune failed: %s", type_key, exc)

    return len(variants)


async def get_active_pool(type_key: str = MORNING_DIGEST) -> List[Dict[str, str]]:
    """Return the active title/body variants for a type ordered by id, or [] on error."""
    try:
        rows = await Database.fetch(
            "SELECT title, body FROM etown_notification_pool "
            "WHERE type_key = $1 AND is_active = true ORDER BY id",
            type_key,
        )
    except Exception as exc:
        logger.warning("get_active_pool(%s) failed: %s", type_key, exc)
        return []
    return [{"title": r["title"], "body": r["body"]} for r in rows]


def select_variant(
    pool: List[Dict[str, str]],
    *,
    key: str,
    run_date: date,
    n: Optional[int] = None,
) -> Optional[Dict[str, str]]:
    """Pick a variant by crc32 of recipient key and day, substituting the {n} token with the count."""
    if not pool:
        return None
    seed = f"{key}:{run_date.toordinal()}".encode("utf-8")
    idx = zlib.crc32(seed) % len(pool)
    chosen = pool[idx]
    title, body = chosen["title"], chosen["body"]
    if "{n}" in title or "{n}" in body:
        n_str = str(n) if n is not None else ""
        title = title.replace("{n}", n_str)
        body = body.replace("{n}", n_str)
    return {"title": title, "body": body}
