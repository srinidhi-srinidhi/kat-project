"""Agent State Schemas - LangGraph state and context types for the Chaatie agents.

TypedDict state and context schemas used as state_schema by the member, provider and automation agents, with reducers that accumulate messages, merge a turn's tool list payloads and preserve selected providers and the last generated image across turns.

Features:
    - Agent State Management: Core state schemas for EtownAgent with message and context tracking
    - Message History: Integrated message accumulation using LangGraph's add_messages reducer
    - State Reducers: Custom reducer functions for intelligent state update strategies
    - Result Persistence: Preserve selected providers and the last generated image across turns
    - Context Preservation: Maintain user and session context throughout agent interactions
    - Type Safety: Strongly typed state definitions using TypedDict for validation
    - Implicit Output Merging: Accumulates a turn's tool list payloads for the UI, deduped by id
    - Response Type Management: Control output format and response structure
    - Provider Selection Continuity: Maintain provider choices between search and booking operations

Classes:
    EtownAgentState: Main agent state with message history and persistent data fields
    EtownAgentContext: User context information including contact identification

Functions:
    replace_value(): State reducer implementing 'last write wins' strategy for simple updates
    merge_turn_results(): State reducer accumulating the list payloads produced in one turn
    preserve_results(): State reducer preserving existing values unless new data is provided

Dependencies:
    - typing: Type annotations for state structure validation
    - langchain_core.messages: Message handling and conversation history management
    - langgraph.graph.message: LangGraph message accumulation and state management

Updates:
    12-Mar-2026: Added comprehensive module docstring with detailed feature documentation
    20-Aug-2026: preserve_results treats [] as a real "found nothing", so a zero-result search clears stale providers.
    08-Sep-2026: implicit_output accumulates a turn's list payloads, so answering two needs in one turn no longer drops one.

Notes:
    Uses LangGraph's native state management with custom reducers for sophisticated
    conversation state handling. The preserve_results reducer is critical for 
    maintaining provider selection continuity between search and booking operations.
"""

from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def replace_value(existing: Any, new: Any) -> Any:
    """Return the new value, a last-write-wins state reducer."""
    return new


def merge_turn_results(existing: Any, new: Any) -> Any:
    """Concatenate two list payloads, skipping items whose providerservicesid or id repeats; otherwise return the new value."""
    if not isinstance(existing, list) or not isinstance(new, list):
        return new
    merged = list(existing)
    seen = {
        item.get("providerservicesid") or item.get("id")
        for item in merged if isinstance(item, dict)
    }
    for item in new:
        key = item.get("providerservicesid") or item.get("id") if isinstance(item, dict) else None
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        merged.append(item)
    return merged


def preserve_results(existing: Any, new: Any) -> Any:
    """Keep the existing value when the new one is None; otherwise return the new value, even an empty list."""
    if new is None:
        return existing
    return new


class EtownAgentState(TypedDict):
    """State schema for the Etown agent."""
    messages: Annotated[list[AnyMessage], add_messages]
    implicit_output: Annotated[Any, merge_turn_results] = None
    response_type: Annotated[Optional[str], replace_value] = None
    prompt_ratings_checked: Annotated[Optional[bool], replace_value] = None
    selected_providers: Annotated[Any, preserve_results] = None
    last_generated_image: Annotated[Any, preserve_results] = None

class EtownAgentContext(TypedDict):
    contact_id: str
    user_profile_id: str
    conversation_id: str 
    user_name: str = "User"
    address: str = ""
    user_role: str = "user"
    mobilenumber_accesstoken: str = None
    oauth: dict = None
    language: str = "en"
    latitude: float = None
    longitude: float = None
    time_now: Optional[str] = None