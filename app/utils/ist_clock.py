"""IST Clock Helpers - current IST time for user-facing date reasoning and prompts.

Utility helpers that return the current time in IST whatever the server timezone, as an aware or naive datetime, convert datetimes to naive IST, and render a labelled clock string for injection into agent prompts.

Features:
    - Single source of truth for "now" in IST (UTC+05:30), whatever the server timezone
    - A labelled, human-readable rendering of that clock for prompt injection
    - Naive IST wall-clock time for comparing against the naive datetimes users supply

Classes:
    No classes defined (module exposes clock helpers only)

Functions:
    now_ist(): Timezone-aware current time in IST
    to_ist_naive(): Convert a datetime to naive IST; naive input is returned unchanged
    now_ist_naive(): Current IST wall-clock time as a naive datetime
    format_ist_for_prompt(): Render an IST instant like "Monday, 01 September 2026, 01:04 AM IST"

Dependencies:
    - datetime: timezone/timedelta arithmetic

Notes:
    A fixed +05:30 offset replaces a tz database lookup; IST has no daylight saving.

Updates:
    01-Sep-2026: Initial creation of IST-aware booking date/time checks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

PROMPT_DATETIME_FORMAT = "%A, %d %B %Y, %I:%M %p IST"


def now_ist() -> datetime:
    """Return the current time as a timezone-aware IST datetime."""
    return datetime.now(IST)


def to_ist_naive(value: datetime) -> datetime:
    """Return value as naive IST wall-clock time, converting aware datetimes and passing naive ones through."""
    if value.tzinfo is not None:
        return value.astimezone(IST).replace(tzinfo=None)
    return value


def now_ist_naive() -> datetime:
    """Return the current IST wall-clock time as a naive datetime."""
    return now_ist().replace(tzinfo=None)


def format_ist_for_prompt(value: datetime | None = None) -> str:
    """Format a datetime, or now, in IST with weekday and IST label for prompt injection."""
    value = now_ist() if value is None else value
    if value.tzinfo is not None:
        value = value.astimezone(IST)
    return value.strftime(PROMPT_DATETIME_FORMAT)
