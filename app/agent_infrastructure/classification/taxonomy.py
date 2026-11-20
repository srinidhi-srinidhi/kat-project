"""Intent Taxonomy - canonical intent vocabulary, agent scopes and dispatch allow-list for the bridge.

Classification constants module that names every intent, folds label aliases onto one spelling, maps each intent to its member, provider or shared scope, and lists the read-only intents the bridge may answer without the LLM.

Notes:
    Only read-only intents belong in DISPATCHABLE_INTENTS; RULE_ONLY_DISPATCH ones dispatch only on a rule match.
    ``booking_info`` is short-circuited only when a tracking id is confidently extracted.
    ``booknow_provider`` is read-only: it looks a business up and lists bookable services.
    The booking write is ``request_provider``, which stays off the dispatchable list.

Features:
    - Alias folding, so one intent has one name whatever produced the label
    - Per-intent agent scope (member, provider or both), enforced by the bridge
    - The dispatchable allow-list and its rule-only subset
    - The raw handler path per dispatchable intent, for the dispatcher

Classes:
    No classes defined (module exposes constants and predicate helpers)

Functions:
    canonicalize_label(): Fold a raw label onto its canonical spelling
    is_known_intent(): True when the intent is part of the vocabulary
    is_dispatchable(): True when the bridge may answer the intent itself
    agent_scope_for(): The scope an intent belongs to
    scope_matches(): True when an intent is reachable by the calling agent

Dependencies:
    - typing: Optional return annotations only; this module imports no app code

Updates:
    22-Jul-2026: Initial taxonomy; canonicalizes search_providers, book_service, external_invite_* and pending_rating drift.
    10-Aug-2026: Registered enquire_provider, folded provider_menu in, and widened DISPATCHABLE_INTENTS with two intents.
    04-Sep-2026: Six read-only intents made dispatchable; added RULE_ONLY_DISPATCH; pending_ratings corrected to member scope.
    28-Sep-2026: Registered set_profile_image_from_chat as a provider intent.
    27-Oct-2026: Registered query_my_data, remember/forget_preference, aionboarding_redirection and guestuser.
"""

from __future__ import annotations

from typing import Optional

GREETING = "greeting"
NO_TOOL = "no_tool"
OUT_OF_SCOPE = "out_of_scope"

INTENT_SCOPE: dict[str, str] = {
    "search_provider": "member",
    "recommend_products": "member",
    "request_provider": "member",
    "add_booking_instructions": "member",
    "enquire_provider": "member",
    "booknow_provider": "member",
    "provider_service_insights": "member",
    "get_member_requests_list": "member",
    "external_invite_members": "member",
    "get_provider_requests_list": "provider",
    "external_invite_providers": "provider",
    "create_offer_from_chat": "provider",
    "set_profile_image_from_chat": "provider",
    "address_confirmation": "both",
    "generate_qr": "both",
    "generate_image": "both",
    "create_post": "both",
    "create_post_from_chat": "both",
    "booking_info": "both",
    "cancel_booking": "both",
    "connect_support_agent": "both",
    "provide_etown_info": "both",
    "internal_invite": "both",
    "onboard_provider": "both",
    "web_search": "both",
    "query_my_data": "both",
    "remember_preference": "both",
    "forget_preference": "both",
    "aionboarding_redirection": "both",
    "guestuser": "both",
    "pending_ratings": "member",
    GREETING: "both",
    NO_TOOL: "both",
    OUT_OF_SCOPE: "both",
}

CANONICAL_INTENTS: frozenset[str] = frozenset(INTENT_SCOPE.keys())

DISPATCHABLE_INTENTS: frozenset[str] = frozenset({
    "search_provider",
    "provide_etown_info",
    "booking_info",
    "provider_service_insights",
    "booknow_provider",
    "get_member_requests_list",
    "get_provider_requests_list",
    "pending_ratings",
    "address_confirmation",
    "generate_qr",
    "web_search",
    GREETING,
})

RULE_ONLY_DISPATCH: frozenset[str] = frozenset({
    "address_confirmation",
    "generate_qr",
})

DIRECT_HANDLER: dict[str, Optional[str]] = {
    "search_provider": "app.agent_infrastructure.tools.tools_utils.search_provider.handle_search_provider",
    "recommend_products": "app.agent_infrastructure.tools.tools_utils.recommend_products.handle_recommend_products",
    "provide_etown_info": "app.agent_infrastructure.tools.tools_utils.etown_info.handle_provide_etown_info",
    "booking_info": "app.agent_infrastructure.tools.tools_utils.booking_info.handle_booking_info",
    "provider_service_insights": "app.agent_infrastructure.tools.tools_utils.provider_service_insights.handle_provider_service_insights",
    "booknow_provider": "app.agent_infrastructure.tools.tools_utils.booknow_provider.handle_booknow_provider",
    "get_member_requests_list": "app.agent_infrastructure.tools.tools_utils.member_request_list.handle_member_request_getlist",
    "get_provider_requests_list": "app.agent_infrastructure.tools.tools_utils.provider_request_list.handle_provider_request_getlist",
    "pending_ratings": "app.agent_infrastructure.tools.tools_utils.pending_ratings.check_pending_ratings",
    "address_confirmation": "app.agent_infrastructure.tools.tools_utils.address_confirmation.handle_address_confirmation",
    "generate_qr": "app.agent_infrastructure.tools.tools_utils.generate_qr.handle_generate_qr",
    "web_search": "app.agent_infrastructure.tools.tools_utils.web_search_tool.handle_web_search",
    GREETING: None,
}

LABEL_NORMALIZATION_MAP: dict[str, str] = {
    "search_providers": "search_provider",
    "book_service": "request_provider",
    "external_invite_for_members": "external_invite_members",
    "external_invite_for_provider": "external_invite_providers",
    "external_invite_for_providers": "external_invite_providers",
    "pending_rating": "pending_ratings",
    "check_pending_ratings": "pending_ratings",
    "provider_menu": "provider_service_insights",
    "provider_menu_items": "provider_service_insights",
    "service_insights": "provider_service_insights",
    "smalltalk": GREETING,
    "small_talk": GREETING,
    "greetings": GREETING,
    "greet": GREETING,
    "chitchat": GREETING,
    "none": NO_TOOL,
    "no_tool": NO_TOOL,
    "unknown_tool": NO_TOOL,
}


def canonicalize_label(raw: Optional[str]) -> Optional[str]:
    """Fold a raw log label onto a canonical intent, else return it lower-cased, or None when empty."""
    if raw is None:
        return None
    cleaned = str(raw).strip().lower()
    if not cleaned:
        return None
    if cleaned in LABEL_NORMALIZATION_MAP:
        return LABEL_NORMALIZATION_MAP[cleaned]
    if cleaned in CANONICAL_INTENTS:
        return cleaned
    return cleaned


def is_known_intent(intent: Optional[str]) -> bool:
    """Return True if ``intent`` is a recognised canonical intent."""
    return intent in CANONICAL_INTENTS


def is_dispatchable(intent: Optional[str]) -> bool:
    """Return True if ``intent`` is in the read-only direct-dispatch set."""
    return intent in DISPATCHABLE_INTENTS


def agent_scope_for(intent: Optional[str]) -> str:
    """Return the agent scope ('member', 'provider' or 'both') for ``intent``, defaulting to 'both'."""
    return INTENT_SCOPE.get(intent, "both") if intent else "both"


def scope_matches(intent: Optional[str], agent_scope: str) -> bool:
    """Return True when the intent's scope is 'both' or equals the requesting ``agent_scope``."""
    intent_scope = agent_scope_for(intent)
    return intent_scope == "both" or intent_scope == agent_scope
