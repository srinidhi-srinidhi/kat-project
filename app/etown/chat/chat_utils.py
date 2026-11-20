"""Chat Utilities - request-specific status lines the app shows while a Chaatie turn is processing.

Business layer behind POST /api/chat/processing_texts: reads the likely activity from the prompt with the classifier's deterministic rules and keyword cues plus the conversation's latest assistant turn in etownuserchaatiehistory, then has gpt-4o-mini write four short status lines for it, falling back to per-activity templates.

Features:
    - Activity detection without an embedding call: classifier rules, keyword cues and the previous response_type
    - Follow-ups read in context: "book the second one" after a search narrates the provider, not a new search
    - Role-aware: provider accounts never get "searching for providers" lines
    - LLM lines name the service, item, place or period in the message; templates fill in a search term when known
    - Bounded latency: history read and LLM call each have a timeout, and identical prompts are cached for 10 minutes
    - Never fails: any error returns the template lines, so the app always has something to show
    - CHAATIE_PROCESSING_TEXTS_LLM=false switches to templates only

Classes:
    No classes defined (module exposes one coroutine plus private helpers)

Functions:
    _fetch_last_assistant_turn(): Latest assistant turn's text and response_type for a conversation
    _detect_activity(): The activity key and any search term a prompt most likely triggers
    _clean_lines(): Validate and normalize the model's status lines
    _llm_texts(): Cached gpt-4o-mini call returning status lines for one prompt
    get_processing_texts(): Return the status lines, activity and source for one prompt

Dependencies:
    - app.db.client.Database: fetch_once for the latest-turn read
    - app.agent_infrastructure.classification.rules: deterministic intent and search-term extraction
    - app.agent_infrastructure.infrastructure.llm_clients: gpt4o_mini for the lines
    - app.agent_infrastructure.prompt_templates.templates: processing_texts_system_template
    - async_lru.alru_cache: 10-minute cache of generated lines
    - app.core.config.settings: CHAATIE_PROCESSING_TEXTS_LLM switch

Updates:
    27-Oct-2026: Initial processing-texts helper for POST /api/chat/processing_texts.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from async_lru import alru_cache

from app.agent_infrastructure.classification import rules
from app.agent_infrastructure.infrastructure.llm_clients import gpt4o_mini
from app.agent_infrastructure.prompt_templates.templates import processing_texts_system_template
from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database

_HISTORY_TIMEOUT_SECONDS = 0.5
_LLM_TIMEOUT_SECONDS = 2.5
_MAX_LINE_CHARS = 60

_LANGUAGE_NAMES = {"en": "English", "ta": "Tamil", "hi": "Hindi"}

_MEMBER_ONLY_ACTIVITIES = {"search_provider", "booking", "provider_followup"}
_TERM_DEFAULTS = {"search_provider": "the best", "booking": "the business"}
_LISTING_RESPONSE_TYPES = {"search_provider", "recommend_products", "booknow_provider", "provider_service_insights"}

_ACTIVITIES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "search_provider": ("searching for local providers that match the request", (
        "Understanding what you need…",
        "Finding {term} options near you…",
        "Comparing ratings and distance…",
        "Picking the best matches…",
    )),
    "booking": ("finding the business and preparing a booking", (
        "Getting your booking details…",
        "Finding {term}…",
        "Checking their services…",
        "Preparing your booking…",
    )),
    "provider_followup": ("answering a follow-up about a provider already shown", (
        "Checking that provider's details…",
        "Looking at their services…",
        "Getting your answer ready…",
    )),
    "booking_status": ("looking up one of the user's bookings", (
        "Looking up your booking…",
        "Checking its latest status…",
        "Getting the details ready…",
    )),
    "requests_list": ("opening the user's list of bookings", (
        "Opening your bookings…",
        "Sorting the latest first…",
        "Getting the list ready…",
    )),
    "data_question": ("counting or totalling the user's own Etown activity", (
        "Understanding your question…",
        "Reading your account activity…",
        "Crunching the numbers…",
        "Summarising what I found…",
    )),
    "generate_image": ("designing an image the user described", (
        "Reading your idea…",
        "Sketching the design…",
        "Choosing colours and layout…",
        "Adding the finishing touches…",
    )),
    "create_post": ("drafting a post or offer for the user to review", (
        "Reading what you want to share…",
        "Drafting your post…",
        "Polishing the wording…",
        "Preparing a preview…",
    )),
    "invite": ("preparing an invitation to Etown", (
        "Getting the invite details…",
        "Preparing your invitation…",
        "Almost ready to send…",
    )),
    "web_search": ("looking up live information on the web", (
        "Understanding your question…",
        "Checking the latest sources…",
        "Summarising the answer…",
    )),
    "etown_info": ("explaining how Etown works", (
        "Looking that up for you…",
        "Checking Etown's help guide…",
        "Writing a clear answer…",
    )),
    "pending_ratings": ("checking completed bookings waiting for a rating", (
        "Checking your recent bookings…",
        "Looking for pending ratings…",
        "Getting them ready…",
    )),
    "image_question": ("looking at a photo the user attached", (
        "Looking at your photo…",
        "Working out what's in it…",
        "Finding what matches…",
        "Getting your answer ready…",
    )),
    "smalltalk": ("replying to a greeting or short remark", (
        "Thinking…",
    )),
    "general": ("understanding and answering a general request", (
        "Thinking about your request…",
        "Working on it…",
        "Getting your answer ready…",
    )),
}

_RULE_INTENT_TO_ACTIVITY = {
    "search_provider": "search_provider",
    "booknow_provider": "booking",
    "provider_service_insights": "provider_followup",
    "booking_info": "booking_status",
    "pending_ratings": "pending_ratings",
    "provide_etown_info": "etown_info",
    "web_search": "web_search",
    "greeting": "smalltalk",
    "generate_qr": "general",
    "address_confirmation": "general",
}

_KEYWORD_CUES: Tuple[Tuple[str, re.Pattern], ...] = (
    ("data_question", re.compile(
        r"\bhow\s+many\b|\bhow\s+much\s+(?:did|have)\s+i\b|\b(?:total|average|count)\b"
        r"|\b(?:most|least)\s+(?:booked|ordered|viewed)\b|\bmy\s+rating\b|\bviewed\s+my\b", re.I)),
    ("booking_status", re.compile(
        r"\b(?:cancel|track|tracking)\b|\bstatus\s+of\b|\bwhere\s+is\s+my\b", re.I)),
    ("requests_list", re.compile(
        r"\bmy\s+(?:bookings?|orders?|requests?)\b|\b(?:previous|past|recent)\s+(?:bookings?|orders?)\b", re.I)),
    ("generate_image", re.compile(
        r"\b(?:generate|create|make|design|draw)\b.{0,30}\b(?:logo|poster|image|picture|banner|flyer)\b", re.I)),
    ("create_post", re.compile(
        r"\b(?:post|publish|share)\b.{0,20}\b(?:feed|offer|update|circle)\b|\b(?:create|post)\s+(?:an?\s+)?offer\b", re.I)),
    ("invite", re.compile(r"\b(?:invite|refer)\b", re.I)),
    ("pending_ratings", re.compile(r"\b(?:rate|rating|review)\b.{0,20}\b(?:booking|service|provider)\b", re.I)),
    ("web_search", re.compile(
        r"\b(?:weather|forecast|news|score|traffic|gold\s+rate|petrol\s+price|stock\s+price)\b", re.I)),
    ("booking", re.compile(r"\b(?:book|schedule|appointment|reserve)\b", re.I)),
    ("search_provider", re.compile(
        r"\b(?:need|want|looking\s+for|find|search|near\s+me|nearby|repair|service|shop|store)\b", re.I)),
)


async def _fetch_last_assistant_turn(conversation_id: Optional[str]) -> Tuple[str, Optional[str]]:
    """Return the latest assistant turn's text and response_type, or empty values on a miss, timeout or error."""
    if not conversation_id:
        return "", None
    query = """
        SELECT content, metadata->>'response_type' AS response_type
        FROM public.etownuserchaatiehistory
        WHERE conversation_id = $1 AND role = 'assistant'
        ORDER BY created_at DESC
        LIMIT 1
    """
    try:
        rows = await asyncio.wait_for(Database.fetch_once(query, conversation_id), timeout=_HISTORY_TIMEOUT_SECONDS)
    except Exception as e:
        logger.debug(f"[processing_texts] history read skipped: {e}")
        return "", None
    if not rows:
        return "", None
    return (rows[0]["content"] or "")[:400], rows[0]["response_type"]


def _detect_activity(prompt: str, last_response_type: Optional[str], has_image: bool, user_role: int) -> Tuple[str, str]:
    """Return the activity key a prompt most likely triggers and any search term the rules extracted.

    Args:
        prompt: The user's message.
        last_response_type: response_type of the conversation's latest assistant turn.
        has_image: True when the turn carries an image.
        user_role: 1 for a provider account, 2 for a member account.

    Returns:
        (activity key from _ACTIVITIES, search term or empty string).
    """
    activity, term = "general", ""
    rule = rules.match(prompt)
    if has_image:
        activity = "image_question"
    elif rule is not None:
        activity = _RULE_INTENT_TO_ACTIVITY.get(rule.intent, "general")
        term = (rule.args or {}).get("search_term") or (rule.args or {}).get("business_name") or ""
    elif rules.is_context_dependent(prompt) and last_response_type in _LISTING_RESPONSE_TYPES:
        activity = "provider_followup"
    else:
        for key, pattern in _KEYWORD_CUES:
            if pattern.search(prompt):
                activity = key
                break
    if user_role == 1 and activity in _MEMBER_ONLY_ACTIVITIES:
        activity, term = "general", ""
    return activity, term


def _clean_lines(lines: Any) -> List[str]:
    """Return three to five trimmed status lines ending in an ellipsis, or an empty list when the input is unusable."""
    if not isinstance(lines, list):
        return []
    cleaned: List[str] = []
    for line in lines:
        if not isinstance(line, str):
            continue
        text = re.sub(r"\s+", " ", line).strip().strip('"').replace("...", "…").rstrip(".!")
        if not text or len(text) > _MAX_LINE_CHARS:
            continue
        cleaned.append(text if text.endswith("…") else text + "…")
    return cleaned[:5] if len(cleaned) >= 3 else []


@alru_cache(maxsize=1024, ttl=600)
async def _llm_texts(prompt: str, previous_reply: str, activity_hint: str, language_name: str, user_role: int) -> Tuple[str, ...]:
    """Ask gpt-4o-mini for status lines for one prompt, cached for ten minutes per identical input.

    Args:
        prompt: The user's message, whitespace-normalized.
        previous_reply: Start of the latest assistant reply, for follow-ups.
        activity_hint: Plain-words description of the likely activity.
        language_name: Language to write the lines in.
        user_role: 1 for a provider account, 2 for a member account.

    Returns:
        The cleaned lines, or an empty tuple when the reply was unusable.
    """
    user_content = f"User message: {prompt}"
    if previous_reply:
        user_content += f"\n\nChaatie's previous reply, for context only: {previous_reply}"
    response = await asyncio.wait_for(
        gpt4o_mini.ainvoke([
            {"role": "system", "content": processing_texts_system_template(language_name, user_role, activity_hint)},
            {"role": "user", "content": user_content},
        ]),
        timeout=_LLM_TIMEOUT_SECONDS,
    )
    raw = response.content if isinstance(response.content, str) else str(response.content)
    match = re.search(r"\[.*\]", raw.replace("```json", "").replace("```", ""), re.DOTALL)
    if not match:
        return ()
    try:
        return tuple(_clean_lines(json.loads(match.group(0))))
    except json.JSONDecodeError:
        return ()


async def get_processing_texts(
    prompt: str,
    user_role: int = 2,
    language: str = "en",
    conversation_id: Optional[str] = None,
    has_image: bool = False,
) -> Dict[str, Any]:
    """Return the status lines to show while a Chaatie turn processes, with their activity and source.

    Args:
        prompt: The prompt being sent to /api/chat/chaatie.
        user_role: 1 for a provider account, 2 for a member account.
        language: Language code for the lines.
        conversation_id: Conversation id, used to read a follow-up in context.
        has_image: True when the turn carries an image.

    Returns:
        Dict with texts (list of str), intent (activity key) and source ('llm' or 'template').
    """
    text = re.sub(r"\s+", " ", prompt or "").strip()[:500]
    previous_reply, last_response_type = await _fetch_last_assistant_turn(conversation_id)
    activity, term = _detect_activity(text, last_response_type, has_image, user_role)
    hint, templates = _ACTIVITIES[activity]
    fallback = [line.format(term=term or _TERM_DEFAULTS.get(activity, "it")) for line in templates]

    if not settings.CHAATIE_PROCESSING_TEXTS_LLM or activity == "smalltalk" or not text:
        return {"texts": fallback, "intent": activity, "source": "template"}

    language_name = _LANGUAGE_NAMES.get((language or "en").lower(), language or "English")
    try:
        lines = await _llm_texts(text, previous_reply, hint, language_name, int(user_role))
    except asyncio.TimeoutError:
        logger.info("[processing_texts] gpt-4o-mini timed out; using templates")
        lines = ()
    except Exception as e:
        logger.warning(f"[processing_texts] generation failed; using templates: {e}")
        lines = ()
    if lines:
        return {"texts": list(lines), "intent": activity, "source": "llm"}
    return {"texts": fallback, "intent": activity, "source": "template"}
