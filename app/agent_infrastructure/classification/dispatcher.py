"""Classification Dispatcher - calls the tool handler for a confident, dispatchable intent.

Classifier layer behind bridge.py: maps an intent to its raw handle_* function rather than the @tool wrapper, using rule-extracted arguments when present and otherwise distilling them from the query, and returns a normalized result for the shaper. It never raises; any error, empty result or unsupported intent returns None.

Features:
    - One private wrapper per dispatchable intent, each returning None to fall through
    - Search falls through on a multi-need request, and when no result deals in the named item
    - Rule-extracted arguments preferred over distilled ones
    - Already-shown provider suppression, scoped to a repeat of the same search term
    - Machine directives stripped from the text, so a dispatched reply is user-facing

Classes:
    No classes defined (module exposes dispatch() plus one wrapper per intent)

Functions:
    _extract_booking_id(): Return a confidently present booking id, else None
    _shown_ids(): Ids to suppress, scoped to a repeat of the same search
    _arg(): Return a rule-extracted handler argument, or None
    _search_provider(): Dispatch a provider search, falling through on multi-need or unmatched item
    _recommend_products(): Dispatch a product recommendation search
    _provide_etown_info(): Dispatch an Etown platform info question
    _provider_service_insights(): Dispatch provider insights by explicit id or business name
    _booknow_provider(): Dispatch the read-only book-now provider lookup
    _booking_info(): Dispatch a booking lookup for a confidently extracted id
    _own_requests_list(): Dispatch the caller's own member or provider request list, unfiltered
    _pending_ratings(): Dispatch the pending-ratings check for the calling profile
    _address_confirmation(): Dispatch address confirmation with the request's own address
    _generate_qr(): Dispatch the QR UI trigger
    _web_search(): Dispatch a web search with the raw prompt as the query
    dispatch(): Call the wrapper for a dispatchable intent and normalize the result

Dependencies:
    - app.agent_infrastructure.tools.tools_utils.*: the raw handlers listed above
    - app.agent_infrastructure.classification.preprocessor: query distillation
    - app.agent_infrastructure.tools.tools_utils.service_intent: trade resolution for search dispatch
    - app.agent_infrastructure.tools.tools_utils.search_helpers: per-term already-shown ids
    - app.core.logging: structured logging

Updates:
    22-Jul-2026: Initial dispatcher - search_provider, recommend_products and provide_etown_info wired.
    24-Jul-2026: search_provider and recommend_products now distill the dispatch query.
    10-Aug-2026: Handlers prefer rule-extracted arguments; added provider_service_insights and booknow_provider.
    20-Aug-2026: Already-shown suppression is scoped to the same search term, matching the tool path.
    04-Sep-2026: Added six read-only branches: own-request lists, pending ratings, address, QR, web search.
    07-Sep-2026: Dispatch distils the preprocessed text instead of the expanded text.
    08-Sep-2026: search_provider dispatches the resolved trade, else falls through; web_search reports its own response_type.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from langchain_core.messages import SystemMessage

from app.core.logging import logger
from app.agent_infrastructure.classification.preprocessor import distill_query

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
_BOOKING_ID_RE = re.compile(
    r"(?:booking|order|tracking|reference|ref|request)\s*"
    r"(?:id|no\.?|number|#|:)?\s*([A-Za-z0-9][A-Za-z0-9\-]{3,})",
    re.IGNORECASE,
)
_MACHINE_DIRECTIVE_RE = re.compile(r"\A\s*\[.*?\](?:\s*\n\s*\n|\s*\Z)", re.DOTALL)

_ITEM_MISMATCH_MARKER = "NONE of these listings mentions"


_ETOWN_INFO_MISS_SENTINELS = {
    "no relevant results found",
    "no embedding could be generated for the search query",
    "an error occurred during the query",
}


def _extract_booking_id(text: str) -> Optional[str]:
    """Return a UUID or digit-bearing booking id found in explicit booking-id context, else None."""
    if not text:
        return None
    m = _UUID_RE.search(text)
    if m:
        return m.group(0)
    m = _BOOKING_ID_RE.search(text)
    if m:
        candidate = m.group(1).strip("-")
        if any(ch.isdigit() for ch in candidate):
            return candidate
    return None


def _shown_ids(state: Optional[dict], search_term: str = "") -> list:
    """Return provider ids already shown for this search term, falling back to the conversation-wide set."""
    from app.agent_infrastructure.tools.tools_utils.search_helpers import shown_ids_for_search
    return list(shown_ids_for_search(
        (state or {}).get("shown_provider_ids_by_term"),
        search_term,
        (state or {}).get("shown_provider_ids"),
    ))


def _arg(decision, name: str) -> Optional[str]:
    """Return a rule-extracted handler argument, or None when the rules did not supply one."""
    value = (getattr(decision, "dispatch_args", None) or {}).get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


async def _search_provider(decision, request, state) -> Optional[Dict[str, Any]]:
    """Dispatch a provider search, returning None on multi-need, unresolved trade, no results or item mismatch."""
    from app.agent_infrastructure.tools.tools_utils.search_provider import handle_search_provider
    from app.agent_infrastructure.tools.tools_utils.search_provider import (
        _requests_closest_provider,
        _requests_date_and_time,
        _requests_weekend_choice,
    )
    from app.agent_infrastructure.tools.tools_utils.service_intent import resolve_service_intent
    query = _arg(decision, "search_term") or distill_query(decision.preprocessed_text)
    if not query or not query.strip():
        return None
    try:
        resolved = await resolve_service_intent(query, "business")
        if resolved.needs_clarification:
            logger.info(f"[classification] {query!r} needs clarification before provider search")
            return {
                "text": resolved.clarification_question,
                "implicit_output": None,
                "results": [],
                "response_type": "clarification",
                "pending_clarification": True,
                "original_query": query,
            }
        if resolved.multiple_needs:
            needs = list(resolved.trade_terms)[:3]
            if state is not None and needs:
                state.setdefault("messages", []).append(SystemMessage(content=(
                    "The user's latest message resolves to these provider types: "
                    f"{', '.join(needs)}. Call search_provider for each distinct need in this "
                    "turn, before replying, following the TWO OR MORE NEEDS rules; do not ask "
                    "a clarifying question before searching."
                )))
            logger.info(f"[classification] {query!r} carries more than one need {needs}; "
                        f"falling through to the LLM, which searches once per need")
            return None
        if not resolved.trade_terms and not resolved.categories:
            logger.info(f"[classification] {query!r} resolves to no trade or category; "
                        f"falling through to the LLM")
            return None
    except Exception as e:
        logger.warning(f"[classification] trade resolution failed for {query!r}; "
                       f"dispatching it as given: {e}")
    results, text = await handle_search_provider(
        query,
        user_latitude=request.latitude,
        user_longitude=request.longitude,
        contact_id=request.contact_id,
        user_profile_id=request.user_profile_id,
        user_name=request.first_name or "Etown User",
        shown_provider_ids=_shown_ids(state, query),
        prioritize_closest=_requests_closest_provider(request.prompt),
        request_date_and_time=_requests_date_and_time(request.prompt),
        request_weekend_choice=_requests_weekend_choice(request.prompt),
    )
    if not results:
        return None
    if _ITEM_MISMATCH_MARKER in (text or ""):
        logger.info(f"[classification] no result for {query!r} deals in the item itself; "
                    f"falling through so the LLM can qualify the answer")
        return None
    return {"text": text, "implicit_output": results, "results": results,
            "response_type": "search_provider", "search_term": query}


async def _recommend_products(decision, request, state) -> Optional[Dict[str, Any]]:
    """Dispatch a product recommendation search, returning None when it finds nothing."""
    from app.agent_infrastructure.tools.tools_utils.recommend_products import handle_recommend_products
    query = _arg(decision, "search_term") or distill_query(decision.preprocessed_text)
    results, text = await handle_recommend_products(
        search_term=query,
        user_latitude=request.latitude,
        user_longitude=request.longitude,
        contact_id=request.contact_id,
        shown_product_ids=_shown_ids(state, query),
    )
    if not results:
        return None
    return {"text": text, "implicit_output": results, "results": results,
            "response_type": "recommend_products", "search_term": query}


async def _provide_etown_info(decision, request) -> Optional[Dict[str, Any]]:
    """Dispatch an Etown platform info question, returning None on an empty or miss-sentinel answer."""
    from app.agent_infrastructure.tools.tools_utils.etown_info import handle_provide_etown_info
    text = await handle_provide_etown_info(request.prompt or "")
    if not text or not text.strip():
        return None
    if text.strip().rstrip(".").lower() in _ETOWN_INFO_MISS_SENTINELS:
        return None
    return {"text": text, "implicit_output": text, "results": None,
            "response_type": "provide_etown_info"}


async def _provider_service_insights(decision, request, state) -> Optional[Dict[str, Any]]:
    """Dispatch provider insights by rule-extracted id or business name, returning None unless it succeeds."""
    from app.agent_infrastructure.tools.tools_utils.provider_service_insights import (
        handle_provider_service_insights,
    )
    providerservicesid = _arg(decision, "providerservicesid") or ""
    business_name = _arg(decision, "business_name") or ""
    if not providerservicesid and not business_name:
        return None
    text, implicit = await handle_provider_service_insights(
        providerservicesid=providerservicesid,
        business_name=business_name,
        user_latitude=request.latitude,
        user_longitude=request.longitude,
        state=state,
    )
    if not isinstance(implicit, dict) or implicit.get("status") != "success":
        return None
    return {"text": text, "implicit_output": implicit,
            "results": implicit.get("selected_providers_data"),
            "response_type": "provider_service_insights"}


async def _booknow_provider(decision, request, state) -> Optional[Dict[str, Any]]:
    """Dispatch the read-only book-now provider lookup, returning None when it yields no list."""
    from app.agent_infrastructure.tools.tools_utils.booknow_provider import handle_booknow_provider
    search_term = _arg(decision, "search_term") or distill_query(decision.preprocessed_text)
    if not search_term:
        return None
    text, results = await handle_booknow_provider(
        search_term=search_term,
        user_role=getattr(request, "user_role", 2) or 2,
        user_latitude=request.latitude,
        user_longitude=request.longitude,
    )
    if not results or not isinstance(results, list):
        return None
    return {"text": text, "implicit_output": results, "results": results,
            "response_type": "booknow_provider", "search_term": search_term}


async def _booking_info(decision, request) -> Optional[Dict[str, Any]]:
    """Dispatch a booking lookup for a rule-extracted or confidently parsed booking id."""
    from app.agent_infrastructure.tools.tools_utils.booking_info import handle_booking_info
    booking_id = _arg(decision, "booking_id") or _extract_booking_id(
        getattr(request, "prompt", "") or ""
    )
    if not booking_id:
        return None
    text, implicit = await handle_booking_info(
        booking_id,
        contact_id=request.contact_id,
        user_profile_id=request.user_profile_id,
    )
    if not isinstance(implicit, dict) or implicit.get("status") != "success":
        return None
    return {"text": text, "implicit_output": implicit, "results": None,
            "response_type": "booking_info"}


async def _own_requests_list(intent: str, request) -> Optional[Dict[str, Any]]:
    """Dispatch the caller's own member or provider request list, unfiltered, or None without ids and token.

    Args:
        intent: get_member_requests_list or get_provider_requests_list.
        request: The ChaatieRequest carrying the caller's ids and access token.

    Returns:
        Normalized dispatcher output, or None to fall through.
    """
    contact_id = getattr(request, "contact_id", None)
    user_profile_id = getattr(request, "user_profile_id", None)
    token = getattr(request, "mobilenumber_accesstoken", None)
    if not contact_id or not user_profile_id or not token:
        return None

    if intent == "get_member_requests_list":
        from app.agent_infrastructure.tools.tools_utils.member_request_list import (
            handle_member_request_getlist,
        )
        text, data = await handle_member_request_getlist(
            contact_id=contact_id, user_profile_id=user_profile_id,
            mobilenumber_accesstoken=token,
        )
    else:
        from app.agent_infrastructure.tools.tools_utils.provider_request_list import (
            handle_provider_request_getlist,
        )
        text, data = await handle_provider_request_getlist(
            contact_id=contact_id, user_profile_id=user_profile_id,
            mobilenumber_accesstoken=token,
        )
    if not text:
        return None
    return {"text": text, "implicit_output": data, "results": None, "response_type": intent}


async def _pending_ratings(request) -> Optional[Dict[str, Any]]:
    """Dispatch the pending-ratings check for the calling profile, normalizing tuple, dict or text results."""
    user_profile_id = getattr(request, "user_profile_id", None)
    if not user_profile_id:
        return None
    from app.agent_infrastructure.tools.tools_utils.pending_ratings import check_pending_ratings

    result = await check_pending_ratings(user_profile_id)
    if not result:
        return None
    if isinstance(result, tuple):
        text, data = result[0], result[1] if len(result) > 1 else None
    elif isinstance(result, dict):
        text, data = result.get("message") or result.get("text"), result
    else:
        text, data = str(result), None
    if not text:
        return None
    return {"text": text, "implicit_output": data, "results": None,
            "response_type": "pending_ratings"}


async def _address_confirmation(request) -> Optional[Dict[str, Any]]:
    """Dispatch the address-confirmation handler with the request's own address."""
    from app.agent_infrastructure.tools.tools_utils.address_confirmation import (
        handle_address_confirmation,
    )

    text, data = await handle_address_confirmation(getattr(request, "address", None))
    if not text:
        return None
    return {"text": text, "implicit_output": data, "results": None,
            "response_type": "address_confirmation"}


async def _generate_qr() -> Optional[Dict[str, Any]]:
    """Dispatch the QR handler, a UI trigger that publishes nothing."""
    from app.agent_infrastructure.tools.tools_utils.generate_qr import handle_generate_qr

    text, data = await handle_generate_qr()
    if not text:
        return None
    return {"text": text, "implicit_output": data, "results": None,
            "response_type": "generate_qr"}


async def _web_search(decision, request, state) -> Optional[Dict[str, Any]]:
    """Dispatch a web search with the raw prompt as the query and request context filled into state.

    Args:
        decision: The classifier Decision, unused.
        request: The ChaatieRequest carrying the prompt, ids and coordinates.
        state: LangGraph state, copied and filled with location, ids and language.

    Returns:
        Normalized dispatcher output, or None to fall through.
    """
    query = (getattr(request, "prompt", "") or "").strip()
    if not query:
        return None
    from app.agent_infrastructure.tools.tools_utils.web_search_tool import handle_web_search

    search_state = dict(state or {})
    search_state.setdefault("user_latitude", getattr(request, "latitude", None))
    search_state.setdefault("user_longitude", getattr(request, "longitude", None))
    search_state.setdefault("user_profile_id", getattr(request, "user_profile_id", None))
    search_state.setdefault("contact_id", getattr(request, "contact_id", None))
    search_state.setdefault("user_language", getattr(request, "language", "en") or "en")

    result = await handle_web_search(query, search_state)
    if not isinstance(result, dict):
        return None
    text = result.get("user_message") or result.get("content")
    if not text:
        return None
    return {"text": text, "implicit_output": result, "results": None,
            "response_type": "web_search"}


async def dispatch(intent: str, decision, request, state) -> Optional[Dict[str, Any]]:
    """Call the wrapper for a dispatchable intent and strip leading machine directives from its text.

    Args:
        intent: Canonical intent already confirmed dispatchable by the bridge.
        decision: The classifier Decision carrying preprocessed text and rule arguments.
        request: The ChaatieRequest with coordinates, ids, name and prompt.
        state: The LangGraph state dict with shown provider ids.

    Returns:
        Dict with text, implicit_output, results and response_type, or None to fall through.
    """
    try:
        if intent == "search_provider":
            result = await _search_provider(decision, request, state)
        elif intent == "recommend_products":
            result = await _recommend_products(decision, request, state)
        elif intent == "provide_etown_info":
            result = await _provide_etown_info(decision, request)
        elif intent == "provider_service_insights":
            result = await _provider_service_insights(decision, request, state)
        elif intent == "booknow_provider":
            result = await _booknow_provider(decision, request, state)
        elif intent == "booking_info":
            result = await _booking_info(decision, request)
        elif intent in ("get_member_requests_list", "get_provider_requests_list"):
            result = await _own_requests_list(intent, request)
        elif intent == "pending_ratings":
            result = await _pending_ratings(request)
        elif intent == "address_confirmation":
            result = await _address_confirmation(request)
        elif intent == "generate_qr":
            result = await _generate_qr()
        elif intent == "web_search":
            result = await _web_search(decision, request, state)
        else:
            return None

        if not result:
            return None

        text = result.get("text") or ""
        while True:
            trimmed = _MACHINE_DIRECTIVE_RE.sub("", text, count=1)
            if trimmed == text:
                break
            text = trimmed
        text = text.strip()
        if not text:
            logger.warning(
                f"[classification] dispatch({intent}) returned only machine directives; "
                f"falling through to the LLM"
            )
            return None
        result["text"] = text
        return result
    except Exception as e:
        logger.warning(f"[classification] dispatch({intent}) failed; falling through: {e}")
        return None
