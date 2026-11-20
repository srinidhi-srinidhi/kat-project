"""Bridge Response Shaper - builds agent-shaped responses for turns the Classification Bridge answers.

Classification helper that wraps dispatched tool output, greetings and small talk in the same dict shape the LangGraph agent returns, so chat.py extracts, translates and persists the reply unchanged. Message text stays untranslated for chat.py's OutputTranslator.

Features:
    - Tool-intent responses carrying synthetic tool_calls and a matching response_type
    - Templated greeting and small-talk replies that use no tool and render no widget
    - Search terms echoed back on the tool call, so chat.py can attribute shown providers
    - The synthetic tool_calls entry is required: chat.py drops response_type unless the tool appears in tools_used

Classes:
    No classes defined (module exposes two response builders)

Functions:
    shape(): Build the agent-shaped dict for a dispatched tool intent
    shape_greeting(): Build a templated greeting or small-talk response

Dependencies:
    - langchain_core.messages.AIMessage: the message type chat.py expects

Updates:
    22-Jul-2026: Initial shaper (T3.2). Synthetic tool_calls so response_type survives.
    20-Aug-2026: Synthetic tool_calls carry the dispatched search_term so chat.py can attribute shown providers.
    04-Sep-2026: shape_greeting takes a kind, so thanks/farewell/affirm get their own reply.
"""

from __future__ import annotations

from typing import Any, Dict

from langchain_core.messages import AIMessage

_GREETING_TEXT = "Hello! I'm Chaatie. How can I help you find local businesses, services, or products today?"
_SMALLTALK_TEXT = {
    "thanks": "You're welcome! Anything else you'd like me to find for you?",
    "farewell": "Goodbye! Come back any time you need a local business or service.",
    "affirm": "Great! What would you like me to help you with?",
}


def shape(intent: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Build an agent-shaped response with a synthetic tool call for a dispatched intent.

    Args:
        intent: Dispatched tool name, used as the default response_type.
        result: Normalized dispatcher output with text, implicit_output, results and search_term.

    Returns:
        Dict with messages, implicit_output, response_type, and selected_providers when results exist.
    """
    text = result.get("text") or ""
    response_type = result.get("response_type") or intent
    dispatched_args = (
        {"search_term": result["search_term"]} if result.get("search_term") else {}
    )
    message = AIMessage(
        content=text,
        tool_calls=[{
            "name": intent,
            "args": dispatched_args,
            "id": f"bridge-{intent}",
            "type": "tool_call",
        }],
    )
    shaped: Dict[str, Any] = {
        "messages": [message],
        "implicit_output": result.get("implicit_output"),
        "response_type": response_type,
    }
    if result.get("results") is not None:
        shaped["selected_providers"] = result["results"]
    return shaped


def shape_greeting(language: str = "en", kind: str = "greeting") -> Dict[str, Any]:
    """Build a templated greeting or small-talk response with no tool call.

    Args:
        language: Caller's language; unused here because chat.py localizes the text.
        kind: Small-talk kind ('thanks', 'farewell', 'affirm'); anything else gives the greeting.

    Returns:
        Dict with messages, implicit_output None and response_type None.
    """
    return {
        "messages": [AIMessage(content=_SMALLTALK_TEXT.get(kind, _GREETING_TEXT))],
        "implicit_output": None,
        "response_type": None,
    }
