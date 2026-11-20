"""Query My Data Tool Helper - answers a caller's question about their own Etown data with validated generated SQL.

Tool helper behind query_my_data: asks gpt-4.1 at temperature 0 for one SELECT over the caller's chaatie_ro views, validates and rewrites it, runs it on the read-only pool bound to the caller's identity, and returns the rows for the agent to phrase; every attempt is audited in etown_data_query_audit.

Features:
    - Catalogue-grounded generation: only the scope's views, columns, rules and examples reach the prompt
    - One corrective retry: a validator rejection or SQL error is fed back to the model once
    - Structured refusals: an unanswerable question returns a reason or one clarifying question
    - Fail closed: off mode, missing credentials, missing identity or the guest account answer nothing
    - Shadow mode runs and audits the whole pipeline but tells the agent the feature is not available yet
    - Rows are JSON-safe and capped; the SQL itself never reaches the user
    - Audit rows written fire-and-forget, so a logging failure never fails the turn
    - Every attempt's generated SQL, validator verdict, executed SQL and outcome stored in attempt_log

Classes:
    No classes defined (module exposes one handler plus private helpers)

Functions:
    _scope_for_role(): Map a numeric user_role to 'member' / 'provider' / None
    _parse_generation(): Parse the model's JSON reply, tolerating code fences
    _json_safe(): Convert one row value to a JSON-serialisable form
    _audit(): Insert one audit row, logging instead of raising on failure
    _generate(): Ask the model for SQL, a clarification or a refusal for one question
    handle_query_my_data(): Run the pipeline for one question and return agent-facing text and a payload

Dependencies:
    - app.agent_infrastructure.infrastructure.llm_clients: gpt41_precise for SQL generation
    - app.agent_infrastructure.data_query: catalog (prompt and allow-list) and validator
    - app.db.client: ReadOnlyDatabase for execution, Database for the audit insert
    - app.utils.ist_clock: today's IST date for relative periods
    - app.core.config.settings: DATA_QUERY_* flags and limits, guest account ids

Updates:
    27-Oct-2026: Initial query_my_data handler; audit rows carry a per-attempt attempt_log and views_used.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent_infrastructure.data_query.catalog import render_catalog
from app.agent_infrastructure.data_query.validator import validate_sql
from app.agent_infrastructure.infrastructure.llm_clients import gpt41_precise
from app.core.config import settings
from app.core.logging import logger
from app.db.client import DataQueryCostError, Database, ReadOnlyDatabase
from app.utils.ist_clock import format_ist_for_prompt

_GENERATION_TIMEOUT_SECONDS = 20
_MAX_ATTEMPTS = 2
_MAX_RESULT_CHARS = 6000
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

_SYSTEM_PROMPT = """You turn a question from an Etown {role} into ONE PostgreSQL SELECT over the views listed below.
The views are already limited to this user's own data. NEVER filter by any user, contact or profile id.

Rules:
- Use only the listed views and columns. Never reference any other table, schema or system catalog.
- Exactly one SELECT statement; a WITH clause is fine. Only ordinary aggregate, date, string and CASE expressions.
- Times are IST. Right now it is {now}. Resolve periods like "last month" or "this week" with date_trunc and CURRENT_DATE.
- Count, total and average questions: return an aggregate. Listing questions: at most 20 rows, ordered sensibly.
- Select only the columns the answer needs, with readable aliases.
- If the views cannot answer the question, set answerable to false and say what is missing in reason.
- If the question is answerable but ambiguous in a way that changes the result (for example no period given for a
  rate), you may assume the most natural reading and say so in reason; ask a clarification only when no reading is natural.

Reply with JSON only, no prose:
{{"answerable": true or false, "sql": "<one SELECT, or null>", "clarification": "<one short question, or null>", "reason": "<short note>"}}

Views:
{catalog}
"""

_RESULT_HEADER = (
    "[DATA QUERY RESULT - for you, not the user. Answer the user's question from these rows only, "
    "in plain conversational words. Never show SQL, internal ids or column names. If there are no rows, "
    "say nothing matched. When you report a money total, say it is based on listed prices.]"
)
_UNAVAILABLE_TEXT = (
    "[DATA QUERY NOT AVAILABLE - for you, not the user.] Tell the user plainly that you can't work that "
    "out from their account data yet, and offer what you can do instead, such as showing their booking list."
)


def _scope_for_role(user_role: Any) -> Optional[str]:
    """Return 'member' for role 2, 'provider' for role 1, else None."""
    try:
        role = int(user_role)
    except (TypeError, ValueError):
        return None
    return {2: "member", 1: "provider"}.get(role)


def _parse_generation(raw: str) -> Dict[str, Any]:
    """Parse the model's JSON reply into a dict, stripping code fences, or return an empty dict when unparseable."""
    text = _FENCE_RE.sub("", raw or "").strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_safe(value: Any) -> Any:
    """Return a JSON-serialisable form of one row value: Decimal to float, dates to ISO text, others unchanged."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ", timespec="minutes") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def _audit(record: Dict[str, Any]) -> None:
    """Insert one etown_data_query_audit row from a record dict, logging any failure as a warning."""
    try:
        await Database.execute(
            """
            INSERT INTO public.etown_data_query_audit
                (mode, conversation_id, contact_id, user_profile_id, user_role, question,
                 generated_sql, executed_sql, status, detail, row_count, plan_cost, latency_ms, attempts,
                 attempt_log, views_used)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb, $16::text[])
            """,
            settings.DATA_QUERY_MODE,
            record.get("conversation_id"),
            record.get("contact_id"),
            record.get("user_profile_id"),
            record.get("user_role"),
            record.get("question"),
            record.get("generated_sql"),
            record.get("executed_sql"),
            record.get("status"),
            (record.get("detail") or "")[:1000] or None,
            record.get("row_count"),
            record.get("plan_cost"),
            record.get("latency_ms"),
            record.get("attempts"),
            json.dumps(record.get("attempt_log") or [], ensure_ascii=False, default=str),
            list(record.get("views_used") or []) or None,
        )
    except Exception as e:
        logger.warning(f"[data_query] audit insert failed: {e}")


async def _generate(messages: List[Any]) -> Tuple[str, Dict[str, Any]]:
    """Ask gpt-4.1 for SQL, a clarification or a refusal and return the raw reply with its parsed JSON.

    Args:
        messages: The system prompt, the question and any earlier attempt with its correction.

    Returns:
        The raw reply text and the parsed dict (empty when the reply was not JSON).
    """
    response = await asyncio.wait_for(gpt41_precise.ainvoke(messages), timeout=_GENERATION_TIMEOUT_SECONDS)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return raw, _parse_generation(raw)


async def handle_query_my_data(
    question: str,
    *,
    contact_id: Optional[str],
    user_profile_id: Optional[str],
    user_role: Any,
    conversation_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Answer one question about the caller's own data and return agent-facing text plus a status payload.

    Args:
        question: The user's question, in English, as the agent understood it.
        contact_id: The caller's contact id from the execution context.
        user_profile_id: The caller's profile id from the execution context.
        user_role: 1 for a provider account, 2 for a member account.
        conversation_id: The conversation, recorded in the audit row.

    Returns:
        A (text, payload) pair; payload carries status, rows, columns and views.
    """
    started = time.perf_counter()
    scope = _scope_for_role(user_role)
    record: Dict[str, Any] = {
        "conversation_id": conversation_id,
        "contact_id": contact_id,
        "user_profile_id": user_profile_id,
        "user_role": int(user_role) if scope else None,
        "question": (question or "").strip()[:1000],
        "attempts": 0,
        "attempt_log": [],
    }

    def finish(status: str, text: str, payload: Optional[Dict[str, Any]] = None, **audit_fields) -> Tuple[str, Dict[str, Any]]:
        record.update(audit_fields, status=status, latency_ms=int((time.perf_counter() - started) * 1000))
        asyncio.create_task(_audit(dict(record)))
        logger.info(f"[data_query] status={status} attempts={record['attempts']} ms={record['latency_ms']}")
        return text, {"status": status, **(payload or {})}

    is_guest = bool(
        (settings.GUEST_CONTACT_ID and contact_id == settings.GUEST_CONTACT_ID)
        or (settings.GUEST_USER_PROFILE_ID and user_profile_id == settings.GUEST_USER_PROFILE_ID)
    )
    if settings.DATA_QUERY_MODE == "off" or not ReadOnlyDatabase.is_configured():
        return _UNAVAILABLE_TEXT, {"status": "disabled"}
    if not record["question"]:
        return finish("rejected", "[DATA QUERY - no question was given. Ask the user what they want to know.]")
    if scope is None or is_guest or not contact_id or not user_profile_id:
        return finish("no_identity", _UNAVAILABLE_TEXT)

    messages: List[Any] = [
        SystemMessage(content=_SYSTEM_PROMPT.format(
            role=scope, now=format_ist_for_prompt(), catalog=render_catalog(scope)
        )),
        HumanMessage(content=record["question"]),
    ]
    last_problem = ""
    while record["attempts"] < _MAX_ATTEMPTS:
        record["attempts"] += 1
        entry: Dict[str, Any] = {"attempt": record["attempts"]}
        record["attempt_log"].append(entry)
        try:
            raw, parsed = await _generate(messages)
        except asyncio.TimeoutError:
            entry["outcome"] = "generation_timeout"
            return finish("error", _UNAVAILABLE_TEXT, detail="generation timed out")
        except Exception as e:
            logger.error(f"[data_query] generation failed: {e}", exc_info=True)
            entry["outcome"] = "generation_error"
            return finish("error", _UNAVAILABLE_TEXT, detail=f"generation failed: {e}")

        if not parsed:
            last_problem = "The reply was not the requested JSON object."
            entry.update(outcome="not_json", raw_reply=raw[:1000])
        elif not parsed.get("answerable"):
            clarification = (parsed.get("clarification") or "").strip()
            reason = (parsed.get("reason") or "").strip()
            entry.update(outcome="clarification" if clarification else "unanswerable",
                         reason=reason or None, clarification=clarification or None)
            if clarification:
                text = _UNAVAILABLE_TEXT if settings.DATA_QUERY_MODE != "live" else (
                    "[DATA QUERY NEEDS CLARIFICATION - for you, not the user.] Ask the user exactly this, "
                    f"then call query_my_data again with their answer folded in: {clarification}"
                )
                return finish("clarification", text, detail=clarification)
            text = _UNAVAILABLE_TEXT if settings.DATA_QUERY_MODE != "live" else (
                "[DATA QUERY CANNOT ANSWER - for you, not the user.] Tell the user plainly that their account "
                f"data can't answer this ({reason or 'not covered'}), and offer what you can do instead."
            )
            return finish("unanswerable", text, detail=reason)
        else:
            generated = (parsed.get("sql") or "").strip()
            record["generated_sql"] = generated[:4000]
            entry["generated_sql"] = generated[:4000]
            verdict = validate_sql(generated, scope, settings.DATA_QUERY_MAX_ROWS)
            if not verdict.ok:
                last_problem = verdict.reason
                entry.update(outcome="validator_rejected", reason=verdict.reason)
                logger.warning(f"[data_query] validator rejected attempt {record['attempts']}: {verdict.reason}")
            else:
                record["executed_sql"] = verdict.sql
                record["views_used"] = list(verdict.views)
                entry.update(executed_sql=verdict.sql, views=list(verdict.views))
                try:
                    rows, cost = await ReadOnlyDatabase.fetch_scoped(
                        verdict.sql,
                        contact_id=contact_id,
                        user_profile_id=user_profile_id,
                        timeout_ms=settings.DATA_QUERY_STATEMENT_TIMEOUT_MS,
                        max_cost=settings.DATA_QUERY_MAX_PLAN_COST,
                    )
                except DataQueryCostError as e:
                    entry.update(outcome="too_costly", reason=str(e))
                    return finish("too_costly", _UNAVAILABLE_TEXT, detail=str(e))
                except asyncpg.exceptions.QueryCanceledError as e:
                    entry.update(outcome="timeout", reason=str(e))
                    return finish("timeout", _UNAVAILABLE_TEXT, detail=str(e))
                except asyncpg.PostgresError as e:
                    last_problem = f"PostgreSQL rejected the query: {str(e)[:300]}"
                    entry.update(outcome="db_error", reason=str(e)[:300])
                    logger.warning(f"[data_query] execution error on attempt {record['attempts']}: {e}")
                except Exception as e:
                    logger.error(f"[data_query] execution failed: {e}", exc_info=True)
                    entry.update(outcome="execution_error", reason=str(e)[:300])
                    return finish("error", _UNAVAILABLE_TEXT, detail=f"execution failed: {e}")
                else:
                    entry.update(outcome="executed", row_count=len(rows), plan_cost=cost)
                    columns = list(rows[0].keys()) if rows else []
                    safe_rows = [[_json_safe(row[c]) for c in columns] for row in rows]
                    payload = {"rows": safe_rows, "columns": columns, "views": list(verdict.views)}
                    audit = {"row_count": len(rows), "plan_cost": cost}
                    if settings.DATA_QUERY_MODE != "live":
                        return finish("shadow_ok", _UNAVAILABLE_TEXT, payload, **audit)
                    body = json.dumps(safe_rows, ensure_ascii=False, default=str)
                    if len(body) > _MAX_RESULT_CHARS:
                        body = body[:_MAX_RESULT_CHARS] + " ...(truncated)"
                    text = (
                        f"{_RESULT_HEADER}\nQuestion: {record['question']}\n"
                        f"Columns: {', '.join(columns) or '(none)'}\n"
                        f"Rows ({len(rows)}, capped at {settings.DATA_QUERY_MAX_ROWS}): {body}"
                    )
                    return finish("ok", text, payload, **audit)

        messages.append(AIMessage(content=raw))
        messages.append(HumanMessage(content=(
            f"That was not usable: {last_problem} Return corrected JSON in the same format."
        )))

    return finish("rejected", _UNAVAILABLE_TEXT, detail=last_problem)
