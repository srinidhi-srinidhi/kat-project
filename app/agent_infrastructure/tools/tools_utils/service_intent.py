"""Service Intent Tool Helper - maps a user's stated need onto the trades and categories that satisfy it.

Search helper that resolves a query with one cached gpt4o_mini call into trade terms, listing vocabulary, canonical categories and item terms, which the provider search uses as extra query variants, relevance keywords and affinity signals.

Features:
    - No hardcoded problem->trade mappings: resolution is semantic, so unseen requests resolve too
    - Semantic Need->Trade Resolution: "assemble a wardrobe" -> ["carpenter", "furniture assembly"]
    - Purchase vs Job Branching: "wall clock" -> ["clock shop"], not ["clock repair"]
    - Match Vocabulary: listing words the resolved trades plausibly use, for relevance scoring
    - Canonical Category Resolution: maps the request onto normalized_category values
    - Item-Specific Detection: flags requests about one specific item
    - Multi-Need Detection: flags a request that names two or more different needs
    - Item Vocabulary: the named item plus its listing synonyms, for affinity ranking
    - Result Caching: identical queries resolve once per hour across all users
    - Fail-Safe: any LLM/parse failure yields an unresolved intent

Classes:
    ServiceIntent(): Immutable resolution result with trade, match, category and item terms

Functions:
    resolve_service_intent(): Resolve a search query into a ServiceIntent (cached)
    _resolve_cached(): TTL/LRU-cached LLM resolution call; parses the JSON payload and never raises
    _clean_terms(): Coerce an LLM-returned list into a clean, deduplicated, lower-cased term list
    _clean_categories(): Validate LLM-returned categories against CANONICAL_CATEGORIES
    _clean_item_terms(): Keep only item terms that can discriminate inside one category
    _meaningful_words(): Flatten terms to individual words, dropping stopwords and 1-char tokens
    clear_service_intent_cache(): Invalidate the resolution cache

Dependencies:
    - json / re: LLM response parsing
    - async_lru: TTL cache for resolutions
    - app.agent_infrastructure.infrastructure.llm_clients: gpt4o_mini for resolution
    - app.agent_infrastructure.tools.tools_utils.search_helpers: shared stopwords/normalizer
    - app.core.logging: Centralized logging

Updates:
    29-Jul-2026: Initial creation; replaces classify_item_specific_repair_intent.
    10-Aug-2026: Added category resolution against CANONICAL_CATEGORIES.
    20-Aug-2026: Purchases resolve to shops selling the item.
    07-Sep-2026: Added sporting_goods category.
    08-Sep-2026: Reports multiple_needs; added five new categories; food resolves to sellers; returns item_terms.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.

Notes:
    Resolution runs on the ENGLISH search term. handle_search_provider translates non-English
    queries before calling in, so Tamil/Hindi requests resolve against the same trade
    vocabulary the provider listings are written in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Tuple

from async_lru import alru_cache

from app.core.logging import logger
from app.agent_infrastructure.infrastructure.llm_clients import gpt4o_mini
from app.agent_infrastructure.tools.tools_utils.search_helpers import (
    GENERIC_TRADE_WORDS,
    SEARCH_STOPWORDS,
    normalize_search_text,
)

MAX_TRADE_TERMS = 3
MAX_MATCH_TERMS = 20
MAX_CATEGORIES = 4
MAX_ITEM_TERMS = 10

_CACHE_MAXSIZE = 512
_CACHE_TTL_SECONDS = 3600

CANONICAL_CATEGORIES: frozenset[str] = frozenset({
    "accounting_services", "agricultural_services", "appliance_repair", "astrology_services",
    "automotive_parts", "automotive_services", "ayurveda_services", "bakery",
    "beauty_services", "cafe", "catering_services", "cleaning_services", "clothing_store",
    "construction_services", "consulting_services", "courier_services", "driving_school",
    "education_services", "electrical_services", "electronics_repair", "electronics_store",
    "engineering_services", "entertainment_services", "event_management", "financial_services",
    "fitness_services", "florist", "footwear_store", "furniture_store", "grocery_store",
    "hardware_store",
    "healthcare_services", "homeopathy_services", "hostel", "hotel", "interior_design",
    "it_services", "jewelry_store", "legal_services", "locksmith_services", "manufacturing",
    "marketing_services", "mobile_phones", "optical_services", "other", "pharmacy",
    "photography_services", "plumbing_services", "printing_services", "real_estate",
    "religious_services", "repair_services", "restaurant", "security_services",
    "sporting_goods", "stationery_store", "taxi_service", "toy_store", "travel_services",
    "watches_services", "water_supply",
})


@dataclass(frozen=True)
class ServiceIntent:
    """Resolved intent for a provider-search query.

    Attributes:
        trade_terms: Business/trade types that would satisfy the request, most likely first.
        match_terms: Words plausibly appearing in such a provider's listing text.
        categories: Canonical ``normalized_category`` values that would carry such a
            provider, most likely first. Used as an exact retrieval key by search.
        item_terms: The specific thing the request is about, plus the words a listing would use for that same thing. Used only to RANK a provider that deals in the thing above one that merely shares its trade — never to keep or drop a row.
        is_item_specific: True when the request concerns one specific physical item.
        multiple_needs: True when the text asks for two or more different things that
            different businesses would serve. Consumed by the classification bridge, which
            declines to short-circuit such a turn: its fast path runs exactly one search and
            would silently drop every need but the first, so only the LLM (which searches
            once per need) can answer it. It backstops rules.needs_clarification's
            conjunction test, which is closed-set and so cannot see that "a plumber and a
            carpenter" names two trades. Never a reason to refuse a search on the tool path.
        resolved: False when resolution failed and callers should use the query as-is.
    """

    trade_terms: Tuple[str, ...] = ()
    match_terms: Tuple[str, ...] = ()
    categories: Tuple[str, ...] = ()
    item_terms: Tuple[str, ...] = ()
    is_item_specific: bool = False
    multiple_needs: bool = False
    needs_clarification: bool = False
    clarification_question: str = ""
    resolved: bool = False


_UNRESOLVED = ServiceIntent()

_CATEGORY_HINTS = {
    "product": (
        "\nThe interface the user searched from is a PRODUCT search, so treat this as a "
        "PURCHASE unless the words plainly describe work to be done.\n"
    ),
    "service": (
        "\nThe interface the user searched from is a SERVICE search, so treat this as a JOB "
        "unless the words plainly describe an item to be bought.\n"
    ),
}

_RESOLUTION_PROMPT = """A user of a local services marketplace typed this request:

"{query}"
{category_hint}
Provider listings on this marketplace are written as the business's TRADE or business type
(for example "carpenter", "plumber", "AC service and repair", "bakery"), never as the
customer's problem. Your job is to bridge that gap.

Return ONLY this JSON object, no markdown and no explanation:
{{"trades": [...], "keywords": [...], "categories": [...], "items": [...], "item_specific": true or false, "multiple_needs": true or false, "needs_clarification": true or false, "clarification_question": "..."}}

FIRST decide whether the text expresses a real need for a product or service at all.
Greetings, chit-chat, random characters and gibberish do NOT. If it does not, return every
list EMPTY — {{"trades": [], "keywords": [], "categories": [], "items": [], "item_specific": false, "multiple_needs": false}} — and
stop. Never guess a plausible-sounding trade for text you cannot actually interpret: the
lists you return are used to retrieve providers, so inventing one makes the marketplace
confidently show a customer businesses that have nothing to do with what they typed.
Returning empty is always the correct answer for uninterpretable input.

FIRST classify the request as a PURCHASE or a JOB, because the two resolve in opposite
directions and getting this backwards is the single most damaging mistake you can make here.

- A PURCHASE is the user wanting to OBTAIN a physical item ("tempered glass for my phone",
  "rice and vegetables", "microwave oven", "wall clock", "notebooks and pen", "camera lens").
  The trades are the SHOPS THAT SELL IT, and never a service that merely works with the same
  material or subject. Someone buying tempered glass wants a mobile accessory shop, not a
  glazier; rice and vegetables means a grocery store, not a caterer; a wall clock means a
  clock shop, not a clock repairer; a camera lens means a camera or electronics shop, not an
  optician; photocopying means a xerox or printing shop, not a photography studio. Food a
  person wants to eat now — a hot samosa, a pizza, dinner — means the restaurant, snack shop
  or bakery that sells it, never a caterer: catering is booked for an event, not a craving.
- A JOB is the user wanting WORK DONE. Name the profession that does the work, not the object
  involved: someone who wants a wardrobe assembled needs a carpenter, not a furniture shop;
    a leaking washing machine needs an appliance repair technician, not a plumber. Appliance
    symptoms are also repair jobs even when the word "repair" is absent: "my AC isn't cooling",
    "air conditioner blowing warm air", "my fridge is not cold", "washing machine won't
    start", and "my washing machine stopped working" must resolve to the relevant
    repair/service provider, never to a shop selling that
    appliance. The same rule applies to vehicles: "my bike needs servicing", "my car is making
    a strange noise", and "my scooter won't start" are vehicle service/repair jobs, not requests
    to buy a bike, car, or scooter. For AC cooling or warm-air symptoms, prefer an AC/appliance
    repair trade and the ``appliance_repair`` or directly matching service category; for vehicle
    servicing or repair, prefer an automotive service/repair trade and ``automotive_services``.
- **Broad-category provider discovery:** Distinguish between a broad industry/category search and 
    a specific service, trade, or provider request. For broad searches, dynamically identify distinct 
    provider types supported by the marketplace and include their relevant `trades`, listing `keywords`, 
    and directly applicable `categories`. Set `multiple_needs` to `true` only when the request genuinely 
    involves different provider types that may require separate businesses. Apply broad discovery only 
    when the user's intent is to explore an industry or category, not merely because the query contains 
    a broad or related term. For specific requests, prioritize the explicitly requested trade or service 
    and do not expand into neighboring trades, related industries, or categories based only on shared keywords 
    or associations. Use actual marketplace vocabulary, preserve relevant provider types through retrieval, 
    and never include unrelated services or invent unsupported provider types.
    
- "trades": up to {max_trades} business or trade types that can directly satisfy this
  request, most likely first, in English. Include additional trades only when they are
  genuine alternatives that can independently fulfill the same user intent. Do not include
  trades merely because they are related to, associated with, or commonly used alongside
  the requested product, service, or activity. Do not include broader industry or category
  trades when a more specific trade directly matches the requested service or activity.
  For a specific activity, return only business types that directly provide that activity,
  and do not return businesses that merely arrange, promote, support, or facilitate it. If the request 
  already names a trade, return that trade first and prefer it over broader or related trades. If one trade 
  clearly satisfies a specific service request, return only that trade. For a broad-industry or broad-category 
  request, identify the distinct provider types that directly belong to the requested industry and include all 
  relevant supported types, rather than limiting the result to one trade. When determining whether a trade directly satisfies the
  request, consider the full meaning and context of the request rather than matching individual 
  words or concepts. Do not infer an additional trade solely from a shared term, unless that trade is a
  genuine provider of the requested service or activity. The number of returned trades should depend on the
  actual ambiguity of the request, not on the maximum allowed count. Name the KIND OF
  BUSINESS, not the item: for a purchase the trade should be the type of business where
  the user would obtain the requested product, not a business that merely uses, prepares,
  repairs, or works with that product. Write trades as plain words separated by spaces
  ("grocery store"), never with underscores.
- "keywords": up to {max_keywords} single words, in English, that plausibly appear in the
  listing text of those trades (their craft, materials, and the services they advertise).
  Do not include generic marketplace filler such as "service", "provider", "shop" or "best".
- "categories": up to {max_categories} values chosen ONLY from this fixed list, most likely
  first. Select categories by actual business relevance to the user's intent, not by keyword overlap
  or broad associations with the words in the query. A category is valid only when a typical provider 
  classified under that category would directly satisfy the user's request. For a specific service request, 
  return only the directly applicable category unless multiple categories genuinely satisfy the request. 
  For a broad-industry or broad-category request, include the distinct supported categories that 
  directly represent relevant provider types within that industry. Do not add unrelated categories 
  merely to increase provider coverage. Do not add other categories merely because they are related 
  to the activity, location, industry, audience, or a possible way the provider might be classified.
  If multiple categories are genuinely capable of satisfying the same request, return those categories in 
  relevance order. The number of returned categories must therefore depend on the actual ambiguity of the 
  request, not on the maximum allowed count. Do NOT expand a specific activity into broader or neighboring 
  categories. Broad categories such as "construction_services", "repair_services", "travel_services", 
  or similar broad categories should be selected only when the user's actual intent is broad enough 
  to justify that category. Never return a category as an additional fallback just to increase provider 
  coverage. Relevance is more important than category coverage. Categories are matched exactly and bypass 
  keyword checking, so an incorrectly selected category can retrieve an entire group of unrelated providers.
  When uncertain whether a category directly satisfies the request, leave that category out.
  Allowed values: {category_vocabulary}
- "items": up to {max_items} words naming the SPECIFIC thing the request is about, together
  with the words a provider's own listing would use for that same thing. "fridge not working"
  gives ["fridge", "refrigerator", "refrigeration"]; "hot samosa" gives ["samosa", "snacks",
  "savouries"]; "pepperoni pizza" gives ["pizza", "pepperoni"]; "20 litre water can" gives
  ["water can", "drinking water", "mineral water"]. These decide only which of several
  providers of the SAME trade is shown first, so name the thing and its synonyms and nothing
  else: no trade names, no generic craft or marketplace words ("repair", "service", "shop",
  "delivery", "best", "technician"), and nothing the request did not actually mention.
  Give BOTH the short form and the full name whenever the thing has both, because a listing
  may use either: air conditioner AND ac, television AND tv, refrigerator AND fridge.
  Name the thing whenever the request identifies one, even when the words also read as a
  trade: "best AC" gives ["ac", "air conditioner"], because a listing for washing-machine
  repair shares that provider's trade without dealing in the thing asked for. Return an
  empty list only when no specific thing is named at all, as in "plumber near me" or
  "looking for a good salon".
- "item_specific": true only when the request is a JOB on one specific physical item the user
  already owns — repairing or servicing an appliance, a vehicle, a piece of furniture, a
  device. A PURCHASE is never item_specific, however precisely the item is named: "microwave
  oven", "camera lens" and "wall clock" are all false.
- "multiple_needs": true only when the text asks for two or more DIFFERENT things that
  different businesses would serve — "an electrician and someone to clean our sofa", "cake
  and flowers for Sunday", "a plumber and a carpenter". Everything else is FALSE, however the
  request is phrased. A trade whose own name contains "and" ("AC service and repair",
  "packers and movers") is one need. A list of items that one shop sells ("rice, milk and
  eggs", "notebooks and pens") is one need. One need carrying extra detail ("a plumber for a
  leaking tap this evening") is one need. Two guesses at what one ambiguous request means is
  also one need. Answer false whenever you are unsure: this flag only decides whether the
  request needs a second search, and claiming two needs where there is one wastes the user's
  time.
- "needs_clarification": Set to `true` only when the user's goal or problem is too vague to 
  reliably identify any relevant provider type. Consider the complete request, including the 
  intended outcome and context. If relevant services can reasonably be inferred, set it to `false` 
  and populate `trades`, `keywords`, and `categories` with the relevant provider types. Infer services 
  from problems and symptoms even when the user asks, "What should I do?" or does not explicitly request 
  a provider. For example, a vehicle that will not start can indicate a mechanic or vehicle repair service. 
  For broad projects involving multiple services, infer up to three essential provider categories instead 
  of asking the user to specify them. Do not ask for clarification about missing optional details, merely 
  because the user has not named a provider or trade, or when the request contains multiple identifiable needs. 
  Do not guess when the goal genuinely remains ambiguous. Never use this flag for clear service requests, normal 
  product searches, or multi-need requests.
- "clarification_question": When `needs_clarification` is `true`, return ONE short, natural question asking 
  only for the essential missing information needed to understand the user's goal. Avoid lengthy explanations, 
  multiple questions, unnecessary examples, repeated acknowledgements, and asking the user to choose a provider 
  category. Use existing conversation context and never ask for information already provided. Do not call 
  `search_providers` while `needs_clarification` is `true`. When `needs_clarification` is `false`, return an empty string."""

def _clean_terms(raw: object, limit: int) -> List[str]:
    """Return up to limit normalized, deduplicated terms from an LLM list, with underscores and hyphens turned into spaces."""
    if not isinstance(raw, list):
        return []
    cleaned: List[str] = []
    seen: set[str] = set()
    for item in raw:
        term = normalize_search_text(str(item).replace("_", " ").replace("-", " "))
        if not term or term in seen:
            continue
        seen.add(term)
        cleaned.append(term)
        if len(cleaned) >= limit:
            break
    return cleaned


def _clean_categories(raw: object) -> List[str]:
    """Return up to MAX_CATEGORIES canonical categories from an LLM list in order, excluding 'other'."""
    if not isinstance(raw, list):
        return []
    cleaned: List[str] = []
    for item in raw:
        value = str(item).strip().lower().replace(" ", "_").replace("-", "_")
        if value in CANONICAL_CATEGORIES and value != "other" and value not in cleaned:
            cleaned.append(value)
        if len(cleaned) >= MAX_CATEGORIES:
            break
    return cleaned


def _clean_item_terms(
    raw: object, trades: List[str], categories: List[str]
) -> List[str]:
    """Return up to MAX_ITEM_TERMS item terms, dropping any made only of category words, generic trade words or stopwords."""
    if not isinstance(raw, list):
        return []
    band_words = {w for category in categories for w in category.split("_")}
    cleaned: List[str] = []
    seen: set[str] = set()
    for item in raw:
        term = normalize_search_text(str(item).replace("_", " ").replace("-", " "))
        words = [w for w in term.split() if len(w) >= 2 and w not in SEARCH_STOPWORDS]
        if not words or term in seen:
            continue
        if all(w in band_words or w in GENERIC_TRADE_WORDS for w in words):
            continue
        seen.add(term)
        cleaned.append(term)
        if len(cleaned) >= MAX_ITEM_TERMS:
            break
    return cleaned


def _meaningful_words(terms: List[str], limit: int) -> List[str]:
    """Return up to limit unique words from the terms, dropping stopwords and 1-character tokens."""
    words: List[str] = []
    seen: set[str] = set()
    for term in terms:
        for word in term.split():
            if len(word) < 2 or word in SEARCH_STOPWORDS or word in seen:
                continue
            seen.add(word)
            words.append(word)
            if len(words) >= limit:
                return words
    return words


@alru_cache(maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL_SECONDS)
async def _resolve_cached(normalized_query: str, search_category: str) -> ServiceIntent:
    """Resolve a normalized query into a ServiceIntent via gpt4o_mini, cached and returning unresolved on any failure."""
    try:
        prompt = _RESOLUTION_PROMPT.format(
            query=normalized_query,
            max_trades=MAX_TRADE_TERMS,
            max_keywords=MAX_MATCH_TERMS,
            max_categories=MAX_CATEGORIES,
            max_items=MAX_ITEM_TERMS,
            category_vocabulary=", ".join(sorted(CANONICAL_CATEGORIES)),
            category_hint=_CATEGORY_HINTS.get(search_category, ""),
        )
        response = await gpt4o_mini.ainvoke(prompt)
        content = (response.content or "").strip()
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            logger.warning(
                "[service_intent] no JSON object in resolution for '%s'", normalized_query
            )
            return _UNRESOLVED

        payload = json.loads(match.group())
        trades = _clean_terms(payload.get("trades"), MAX_TRADE_TERMS)
        keywords = _clean_terms(payload.get("keywords"), MAX_MATCH_TERMS)
        categories = _clean_categories(payload.get("categories"))
        items = _clean_item_terms(payload.get("items"), trades, categories)

        match_terms = _meaningful_words(trades + keywords, MAX_MATCH_TERMS)

        needs_clarification = bool(payload.get("needs_clarification"))
        clarification_question = (
            normalize_search_text(str(payload.get("clarification_question") or ""))
            if needs_clarification
            else ""
        )
        if not trades and not match_terms and not categories and not needs_clarification:
            return _UNRESOLVED

        intent = ServiceIntent(
            trade_terms=tuple(trades),
            match_terms=tuple(match_terms),
            categories=tuple(categories),
            item_terms=tuple(items),
            is_item_specific=bool(payload.get("item_specific")),
            multiple_needs=bool(payload.get("multiple_needs")),
            needs_clarification=needs_clarification,
            clarification_question=clarification_question,
            resolved=True,
        )
        logger.info(
            "[service_intent] '%s' -> trades=%s categories=%s items=%s "
            "item_specific=%s multiple_needs=%s needs_clarification=%s",
            normalized_query, list(intent.trade_terms), list(intent.categories),
            list(intent.item_terms), intent.is_item_specific, intent.multiple_needs, intent.needs_clarification,
        )
        return intent
    except Exception as e:
        logger.warning("[service_intent] resolution failed for '%s': %s", normalized_query, e)
        return _UNRESOLVED


async def resolve_service_intent(
    search_term: str, search_category: str = ""
) -> ServiceIntent:
    """Resolve a search query into trades, categories and item terms, or an unresolved intent when empty or failed.

    Args:
        search_term: The user's search query, already translated to English.
        search_category: The "product", "service" or "business" discriminator; part of the cache key.

    Returns:
        ServiceIntent, with resolved=False when the query is empty or resolution failed.
    """
    normalized = normalize_search_text(search_term or "")
    if not normalized:
        return _UNRESOLVED
    return await _resolve_cached(normalized, (search_category or "").strip().lower())


def clear_service_intent_cache() -> None:
    """Clear the cached service intent resolutions."""
    _resolve_cached.cache_clear()
