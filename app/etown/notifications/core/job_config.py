"""Scheduler Job Config - cached reads of scheduler.etown_job_config.

Notification scheduler helper that loads every job row into an in-process TTL cache and tells the scheduler which jobs are enabled and with what JSONB params; config_reload.py calls invalidate() to force a fresh read on every worker.

Features:
    - Long-TTL in-process cache: one query per TTL window instead of per scheduler tick
    - Enabled-row filtering: get()/get_all_enabled() treat a missing or disabled row as absent
    - JSONB params parsing: get_params() tolerates a string-encoded params column
    - Cross-worker invalidation hook: invalidate() resets the TTL for config_reload.py to call
    - TTL setting: SCHEDULER_JOB_CONFIG_TTL_SECONDS, default 7200, floored at 60

Classes:
    No classes defined (module exposes cache-read helpers and module-level cache state)

Functions:
    _load(): Fetch every row from scheduler.etown_job_config, keyed by job_name
    _refresh(): Reload the cache and reset its TTL expiry, retrying sooner on error
    _ensure_fresh(): Refresh the cache under a lock if its TTL has expired
    invalidate(): Reset the cache's TTL expiry so the next read re-queries
    get(): Config row for job_name if it exists and is enabled, else None
    get_all_enabled(): All enabled job rows keyed by job_name
    get_params(): JSONB params dict for job_name, or {} if missing/disabled

Dependencies:
    - app.db.client.Database: fetch() for scheduler.etown_job_config (imported lazily)
    - app.core.logging: logs cache refresh failures

Updates:
    17-Jun-2026: Module created to back the scheduler with a DB-driven job schedule.
    27-Jul-2026: Cache TTL raised from 60s to a 2-hour default, paired with config_reload.py.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

import asyncio
import os
import time as _time
from typing import Any, Dict, Optional

from app.core.logging import logger

_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_EXP: float = 0.0
_CACHE_TTL = max(60, int(os.getenv("SCHEDULER_JOB_CONFIG_TTL_SECONDS", "7200")))
_ERROR_RETRY_SECONDS = 15
_LOCK = asyncio.Lock()


async def _load() -> Dict[str, Dict[str, Any]]:
    """Fetch every scheduler.etown_job_config row keyed by job_name."""
    from app.db.client import Database
    rows = await Database.fetch("SELECT * FROM scheduler.etown_job_config WHERE true")
    return {r["job_name"]: dict(r) for r in rows}


async def _refresh() -> None:
    """Reload the job config cache, scheduling a short retry instead when the load fails."""
    global _CACHE, _CACHE_EXP
    try:
        _CACHE = await _load()
        _CACHE_EXP = _time.monotonic() + _CACHE_TTL
    except Exception as exc:
        _CACHE_EXP = _time.monotonic() + _ERROR_RETRY_SECONDS
        logger.error("job_config cache refresh failed: %s", exc, exc_info=True)


async def _ensure_fresh() -> None:
    """Refresh the job config cache under the lock once it has expired."""
    if _time.monotonic() < _CACHE_EXP:
        return
    async with _LOCK:
        if _time.monotonic() >= _CACHE_EXP:
            await _refresh()


def invalidate() -> None:
    """Expire the job config cache so the next read reloads it."""
    global _CACHE_EXP
    _CACHE_EXP = 0.0


async def get(job_name: str) -> Optional[Dict[str, Any]]:
    """Return the config row for *job_name* if it exists and is enabled, else None."""
    await _ensure_fresh()
    row = _CACHE.get(job_name)
    if not row or not row.get("enabled"):
        return None
    return row


async def get_all_enabled() -> Dict[str, Dict[str, Any]]:
    """Return all enabled job rows keyed by job_name."""
    await _ensure_fresh()
    return {name: row for name, row in _CACHE.items() if row.get("enabled")}


async def get_params(job_name: str) -> Dict[str, Any]:
    """Return the parsed params dict for an enabled job, or {} when missing, disabled or invalid JSON."""
    row = await get(job_name)
    if row is None:
        return {}
    params = row.get("params") or {}
    if isinstance(params, str):
        import json
        try:
            params = json.loads(params)
        except (ValueError, TypeError):
            logger.warning("job config: params column is not valid JSON; using empty params", exc_info=True)
            params = {}
    return params
