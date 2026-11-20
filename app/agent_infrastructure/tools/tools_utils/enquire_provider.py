"""Enquire Provider Tool - posts a member's free-text enquiry to a provider's chat thread.

Member-agent tool behind the Enquire branch of the off-catalog flow: resolves the provider by id, name or agent state, opens or reuses the member-provider conversation through MessageService, and sends the text as a message without creating a booking.

Features:
    - Provider Resolution: Accepts a providerservicesid or business name, falling back to agent state
    - Ambiguity Handling: Names the real matching businesses instead of messaging the wrong one
    - Diagnosable Failures: Every path that declines to send logs why, with the identifier it had
    - Thread Reuse: create_conversation returns the existing member-provider thread
    - Every Enquiry Posts: the text always goes through send_message, which pushes it to the provider
    - Booking-Free: Sends a message only; no request row is written and no booking is implied

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    handle_enquire_provider(): Resolve the provider and post the member's enquiry to their thread

Dependencies:
    - uuid: Provider identifier UUID detection
    - fastapi: HTTPException raised by the messaging service on validation failures
    - app.core.logging: Centralized logging
    - app.etown.message.message_utils: MessageService/MessageDatabase conversation + message writes
    - app.schemas.message: CreateConversationRequest / SendMessageRequest payload models
    - provider_service_insights: Reused provider lookup and state-resolution helpers

Updates:
    06-Aug-2026: Initial Enquire branch of the off-catalog flow (FR-7.1-7.3); posts via send_message; agent-state provider fallback.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from app.core.logging import logger
from app.etown.message.message_utils import MessageDatabase, MessageService
from app.schemas.message import CreateConversationRequest, SendMessageRequest
from app.agent_infrastructure.tools.tools_utils.provider_service_insights import (
    _describe_business_location,
    _fetch_provider_row_by_id,
    _group_matches_by_business,
    _lookup_business_matches,
    _resolve_from_state,
)

_RETRY_BY_NAME_DIRECTIVE = (
    "[UNKNOWN PROVIDER ID — \"{identifier}\" matches no provider in the directory, so nothing "
    "has been sent yet. Do NOT tell the user the business could not be found and do NOT offer "
    "to search for alternatives. Call enquire_provider again NOW with provider_identifier set "
    "to the provider's BUSINESS NAME exactly as it appears earlier in this conversation, "
    "keeping the same message. Never pass an id you have not seen in a tool result.]"
)


async def handle_enquire_provider(
    message: str,
    provider_identifier: str = "",
    user_profile_id: Optional[str] = None,
    oauth_token: Optional[str] = None,
    match_limit: int = 5,
    state: Optional[dict] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Resolve a provider by id, name or agent state and post the member's enquiry into their conversation.

    Args:
        message: The member's enquiry text, in their own words.
        provider_identifier: A providerservicesid or business name; agent state is used when empty.
        user_profile_id: The enquiring member's user profile id.
        oauth_token: Optional Google OAuth token used to push the message to the provider.
        match_limit: Max disambiguation options for an ambiguous business-name lookup.
        state: LangGraph agent state carrying selected_providers from the prior search.

    Returns:
        Tuple of (assistant message, implicit_output); implicit_output is None when nothing was sent.
    """
    if not user_profile_id:
        return "Please log in to send an enquiry to this provider.", None

    enquiry = (message or "").strip()
    if not enquiry:
        return "Sure — what would you like me to ask this provider?", None

    identifier = (provider_identifier or "").strip()
    resolved_id: Optional[str] = None
    identifier_was_uuid = False

    if identifier:
        try:
            uuid.UUID(identifier)
            resolved_id = identifier
            identifier_was_uuid = True
        except (ValueError, AttributeError, TypeError):
            matches = await _lookup_business_matches(identifier, match_limit)
            groups = _group_matches_by_business(matches, identifier)

            if len(groups) > 1:
                offered = groups[:match_limit]
                listing = "; ".join(
                    f"{index}. {group['representative'].get('businessname') or identifier}"
                    f" — {_describe_business_location(group['representative'])}"
                    for index, group in enumerate(offered, start=1)
                )
                return (
                    f"There are {len(offered)} different businesses matching '{identifier}': {listing}. "
                    "Ask the user which one they mean and offer EXACTLY these options as written above. "
                    "Do not invent any other branch, area, or location.",
                    None,
                )
            if groups:
                resolved_id = str(groups[0]["representative"].get("providerservicesid", "")).strip()
            else:
                logger.info(
                    "enquire_provider: no business match for %r — falling back to agent state",
                    identifier,
                )

    if not resolved_id:
        resolved_id = await _resolve_from_state(state)

    if not resolved_id:
        logger.info("enquire_provider: could not resolve a provider | identifier=%r", identifier)
        return (
            "Which provider would you like me to send this enquiry to? Please name one from the results.",
            None,
        )

    provider_row = await _fetch_provider_row_by_id(resolved_id)
    if not provider_row:
        state_id = await _resolve_from_state(state)
        if state_id and state_id != resolved_id:
            logger.info(
                "enquire_provider: id %r matched no provider — falling back to agent state (%s)",
                resolved_id, state_id,
            )
            resolved_id = state_id
            provider_row = await _fetch_provider_row_by_id(resolved_id)

    if not provider_row:
        logger.warning("enquire_provider: no provider row for resolved id %r", resolved_id)
        if identifier_was_uuid:
            return _RETRY_BY_NAME_DIRECTIVE.format(identifier=identifier), None
        return "I couldn't find that provider in our directory.", None

    provider_userprofileid = str(provider_row.get("userprofileid") or "").strip()
    businessname = provider_row.get("businessname") or "this provider"
    if not provider_userprofileid:
        logger.warning(
            "enquire_provider: provider %r (%s) has no userprofileid to message",
            businessname, resolved_id,
        )
        return (
            f"{businessname} doesn't have a messaging profile yet, so I can't send an enquiry. "
            "You can still place a booking with your instructions instead.",
            None,
        )

    try:
        opponent_type = await MessageDatabase.get_user_participant_type(provider_userprofileid)
        thread = await MessageService.create_conversation(
            CreateConversationRequest(
                userprofile_id=str(user_profile_id),
                opponent_id=provider_userprofileid,
                opponent_type=opponent_type or "provider",
            )
        )
        conversation = thread.get("conversation")
        conversation_id = str(getattr(conversation, "id", "") or "")
        sent = await MessageService.send_message(
            SendMessageRequest(
                conversation_id=conversation_id,
                userprofile_id=str(user_profile_id),
                message_text=enquiry,
                oauth_token=oauth_token,
            )
        )
    except HTTPException as http_exc:
        logger.error(
            "Enquiry to provider failed | provider=%s | detail=%s",
            provider_userprofileid,
            http_exc.detail,
         exc_info=True)
        return (
            f"Sorry, I couldn't send your enquiry to {businessname} right now. Please try again.",
            None,
        )
    except Exception as exc:
        logger.error(
            "Enquiry to provider failed | provider=%s | error=%s",
            provider_userprofileid,
            exc,
            exc_info=True,
        )
        return (
            f"Sorry, I couldn't send your enquiry to {businessname} right now. Please try again.",
            None,
        )

    logger.info(
        "Enquiry sent | provider=%s | conversation=%s | new_thread=%s",
        provider_userprofileid,
        conversation_id,
        thread.get("created"),
    )

    implicit_output = {
        "status": "success",
        "conversation_id": conversation_id,
        "message_id": (sent.get("message") or {}).get("id"),
        "created": thread.get("created"),
        "provider": {
            "providerservicesid": str(resolved_id),
            "userprofileid": provider_userprofileid,
            "businessname": provider_row.get("businessname"),
        },
        "message": enquiry,
    }

    return (
        f"I've sent your enquiry to {businessname}. They'll reply in your chat, "
        "and you can carry on the conversation there.",
        implicit_output,
    )
