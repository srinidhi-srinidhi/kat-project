"""Loop Guard Middleware - blocks repeated identical tool calls within one agent invocation.

Agent-layer wrap_tool_call middleware on the member and provider Chaatie agents: it fingerprints each tool call's arguments and refuses a repeat of an identical call in the same invocation, returning a terminal ToolMessage that tells the model to answer with what it already has.

Features:
    - Duplicate Detection: (tool name, canonicalised arguments) identity across the invocation
    - Order-independent Fingerprints: JSON dump with sorted keys so key order cannot hide a repeat
    - Parallel-call Safety: counts only preceding calls, so one of a parallel identical pair runs
    - Terminal Feedback: blocked calls return an error ToolMessage telling the model to reply now
    - Observability: every block is logged at warning level with the tool name and arguments
    - Fail-safe Shapes: unexpected state/argument shapes degrade to repr() instead of raising

Constants:
    MAX_IDENTICAL_CALLS: Executions allowed per (name, args) pair per invocation (1)

Classes:
    No classes defined (module exposes middleware and a helper function)

Functions:
    _args_fingerprint(): Canonicalise tool arguments into a comparable string
    tool_loop_guard(): wrap_tool_call middleware that blocks duplicate tool calls

Dependencies:
    - langchain.agents.middleware: wrap_tool_call decorator and ToolCallRequest shape
    - langchain_core.messages: ToolMessage returned in place of a blocked execution
    - app.core.logging: Application logger for blocked-call warnings

Updates:
    12-Aug-2026: Module created to block a repeated tool call within one agent invocation.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import json

from langchain.agents.middleware import wrap_tool_call, ToolCallRequest
from langchain_core.messages import ToolMessage

from app.core.logging import logger

MAX_IDENTICAL_CALLS = 1


def _args_fingerprint(args) -> str:
    """Return a key-order-independent JSON fingerprint of tool arguments, falling back to repr()."""
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except Exception:
        logger.debug("loop_guard: tool args are not JSON-serialisable; fingerprinting with repr()")
        return repr(args)


@wrap_tool_call
async def tool_loop_guard(request: ToolCallRequest, handler):
    """Run the tool, or return an error ToolMessage when an identical call already ran in this invocation."""
    tool_call = request.tool_call or {}
    name = tool_call.get("name")
    fingerprint = _args_fingerprint(tool_call.get("args") or {})

    state = request.state if isinstance(request.state, dict) else {}
    messages = state.get("messages") or []

    current_id = tool_call.get("id")
    preceding = 0
    reached_current = False
    for message in messages:
        for prior in getattr(message, "tool_calls", None) or []:
            if prior.get("id") == current_id:
                reached_current = True
                break
            if prior.get("name") == name and _args_fingerprint(prior.get("args") or {}) == fingerprint:
                preceding += 1
        if reached_current:
            break
    if not reached_current:
        preceding = max(0, preceding - 1)

    if preceding >= MAX_IDENTICAL_CALLS:
        logger.warning(
            f"[loop_guard] blocked repeat call to '{name}' "
            f"({preceding} identical call(s) already made this invocation) "
            f"args={fingerprint[:200]}"
        )
        return ToolMessage(
            content=(
                f"STOP — you have already called '{name}' with these exact arguments in this "
                "turn and it returned the same result. Calling it again cannot produce new "
                "information. Reply to the user now using what you already have. If their "
                "request is something you have no tool for, say so plainly and tell them what "
                "you can do instead. Do NOT call any tool again for this request."
            ),
            tool_call_id=tool_call.get("id"),
            name=name,
            status="error",
        )

    return await handler(request)
