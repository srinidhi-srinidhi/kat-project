"""Execution Context Helpers - request-scoped identity and state for Chaatie agent tools.

Agent infrastructure helpers that hold per-request context in a contextvar set by chat.py, and resolve tool values from that context, tool arguments, LangGraph config and state, with identity keys always taken from the execution context first.

Features:
    - Request-scoped Context: Thread-safe context isolation using contextvars
    - Multi-source Value Resolution: Fallback priority system for value retrieval
    - Identity-first Resolution: IDENTITY_KEYS read the execution context before tool arguments
    - LangGraph Integration: Seamless integration with LangGraph state and configuration
    - Thread Safety: Concurrent request handling with proper context isolation
    - Context Lifecycle: Proper context setup, access, and cleanup mechanisms
    - Alternative Key Support: Flexible key lookup with fallback key alternatives
    - State Propagation: Maintains user and execution state across tool calls
    - Configuration Access: Easy access to LangGraph configuration parameters

Classes:
    No classes defined (module uses function-based context management)

Functions:
    set_execution_context(): Sets the execution context for the current request
    get_execution_context(): Retrieves the current execution context dictionary
    clear_execution_context(): Clears and resets the execution context
    get_context_value(): Multi-source value retrieval with fallback priority system

Dependencies:
    - contextvars: Python standard library for request-scoped context variables
    - typing: Type hints for function parameters and return values

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    20-Aug-2026: Identity keys resolve from the execution context first, tool arguments last.
"""

import contextvars
from typing import Any, Dict, Optional, List

execution_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("execution_context", default={})

def set_execution_context(context: Dict[str, Any]) -> None:
    """Set the request-scoped execution context read by agent tools."""
    execution_context.set(context)

def get_execution_context() -> Dict[str, Any]:
    """Return the current request's execution context dict."""
    return execution_context.get()

def clear_execution_context() -> None:
    """Reset the execution context to an empty dict."""
    execution_context.set({})

IDENTITY_KEYS = frozenset({
    "contact_id", "contactid",
    "user_profile_id", "userprofile_id", "userprofileid",
    "provider_id", "providerid",
})


def get_context_value(
    key: str,
    current_value: Any,
    config: Optional[dict],
    state: Optional[dict],
    alt_keys: Optional[List[str]] = None
) -> Any:
    """Resolve a tool value from argument, config, state and execution context, reading context first for identity keys.

    Args:
        key: Primary key to look for.
        current_value: Tool argument value; wins for non-identity keys when non-empty.
        config: LangGraph config dict with a configurable key.
        state: LangGraph state dict.
        alt_keys: Alternative keys tried in state and context.

    Returns:
        Value from the highest-priority source, or current_value if none has it.
    """
    def check_dict(d: Dict[str, Any]) -> Any:
        keys_to_try = [key] + (alt_keys or [])
        for k in keys_to_try:
            val = d.get(k)
            if val is not None:
                return val
        return None

    def from_config() -> Any:
        if config and "configurable" in config:
            configurable = config.get("configurable", {})
            if isinstance(configurable, dict):
                return configurable.get(key)
        return None

    if key in IDENTITY_KEYS:
        ctx = get_execution_context()
        if ctx:
            val = check_dict(ctx)
            if val is not None:
                return val
        val = from_config()
        if val is not None:
            return val
        if state and isinstance(state, dict):
            val = check_dict(state)
            if val is not None:
                return val
        return current_value

    if current_value is not None and current_value != "":
        return current_value

    val = from_config()
    if val is not None:
        return val

    if state and isinstance(state, dict):
        val = check_dict(state)
        if val is not None:
            return val

    ctx = get_execution_context()
    if ctx:
        val = check_dict(ctx)
        if val is not None:
            return val

    return current_value