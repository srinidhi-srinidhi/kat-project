"""Testing Context Manager - request-scoped Flutter message storage for the automation testing agent.

Agent-layer helper that the testing endpoint seeds with the raw Flutter message and the automation_tester tool reads back, using its own ContextVar so test-only state never mixes with a real user's execution context.

Features:
    - Request-scoped Storage: A single ContextVar holds the current test's flutter_data dict.
    - Full-message Passthrough: key="full_message" returns the entire stored dict unchanged.
    - Safe Key Lookup: Missing keys or a non-dict payload return the caller's default.

Classes:
    No classes defined (module uses function-based context management)

Functions:
    set_test_context(): Store the current test's flutter_data dict in the context variable.
    get_test_context_value(): Read one field (or the whole payload) from the stored test context.

Dependencies:
    - contextvars: Python standard library for request-scoped context isolation.

Updates:
    08-Jan-2026: Module created to isolate the Automation Testing Agent's execution context.
"""
from contextvars import ContextVar
from typing import Optional, Any, Dict

_flutter_message_ctx = ContextVar("flutter_message", default={})

def set_test_context(data: Dict[str, Any]):
    """Store the current test's flutter_data dict in the request-scoped context variable."""
    _flutter_message_ctx.set(data)

def get_test_context_value(
    key: str, 
    default: Any = None, 
    config: Any = None
) -> Any:
    """Return one field of the stored test context, the whole dict for "full_message", or default."""
    flutter_data = _flutter_message_ctx.get()
    
    if not flutter_data:
        return default

    if key == "full_message":
        return flutter_data
    
    if isinstance(flutter_data, dict):
        return flutter_data.get(key, default)
            
    return default