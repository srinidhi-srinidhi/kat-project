"""Search Helpers Tool - text normalization, relevance filtering and enrichment for Chaatie provider search.

Shared helpers behind the Chaatie search tools: normalize multilingual query text, keep only listings that lexically match the request (with a guarded cross-script vector fallback and an optional gpt4o_mini KEEP/REMOVE pass), score item affinity, enrich profiles from etown_ratings, suppress already-shown ids and generate fallback terms.

Features:
    - Text Normalization: NFKC, script-safe cleanup and letter collapsing for Tamil, Devanagari, Latin
    - Romanized-Tamil Mapping: Romanized Tamil trade words map to Tamil script
    - Lexical-first Filtering: User words, satisfied trade or primary category keep a row
    - Cross-script Fallback: A guarded 2-result vector pool on a genuine cross-script miss
    - Item Affinity Band: Scores how directly a listing deals in the named item
    - Positive-review Filtering: Strips Google reviews below the positive-display threshold
    - Rating Enrichment: Adds booking/rating counts and average rating from etown_ratings
    - Per-term Shown-ID Suppression: Tracks already-shown ids per search term
    - LLM Relevance Filter: Batch KEEP/REMOVE judgment against coincidental keyword matches

Classes:
    No classes defined (module uses function-based helper utilities)

Functions:
    normalize_search_text(): NFKC-normalizes, lowercases, strips symbols and collapses repeats
    contains_tamil_script(): True if the text contains any Tamil unicode codepoint
    map_romanized_tamil_terms(): Maps romanized Tamil trade words to Tamil script
    _extract_keywords(): Normalized, stopword-filtered tokens extracted from a text
    _stem(): Reduces an ASCII word to a comparison stem for morphology-tolerant matching
    _token_matches(): Token/stem-level match of a query word against a listing's tokens
    _listing_index(): Indexes a provider's text fields into tokens, stems and bigrams
    _shares_long_prefix(): True when a listing token and an item term share a long prefix
    _item_terms_present(): True when any item term appears in the given listing fields
    _item_affinity_band(): 2/1/0 for how directly a listing deals in the thing named
    filter_positive_reviews(): Drops Google reviews rated below the positive-display threshold
    enrich_profiles_with_rating_data(): Adds rating and booking statistics to provider profiles
    shown_ids_for_search(): Already-shown ids from previous runs of a matching search term
    _satisfied_trade(): First resolved trade fully present in a listing's primary fields
    filter_irrelevant_results(): Lexical-first relevance filter with guarded cross-script fallback
    llm_batch_relevance_filter(): LLM KEEP/REMOVE pass judging relevance to the user's intent
    validate_search_context(): Keyword-overlap heuristic for whether results match search intent
    generate_fallback_terms(): Alternative search-term suffixes when a search returns nothing

Dependencies:
    - re: Token extraction, bigram detection, and script pattern matching
    - unicodedata: NFKC normalization for script-safe text comparison
    - app.db.client: Database.fetch for rating aggregation queries
    - app.agent_infrastructure.infrastructure.llm_clients: gpt4o_mini for llm_batch_relevance_filter

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    07-Apr-2026: Added cross-script normalization, guarded vector fallback; two-pass lexical-first filtering.
    22-Jul-2026: normalize_search_text preserves Devanagari as well as Tamil combining marks.
    10-Aug-2026: filter_irrelevant_results scores normalized_category; removed vector-rank grace pool.
    20-Aug-2026: Trade or primary category match keeps a row; LLM filter counts selling as relevance.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    07-Sep-2026: Added query function words to SEARCH_STOPWORDS.
    08-Sep-2026: Added per-row _item_affinity band; multi-word trades must match close together.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
"""

import json
import logging
import re
import unicodedata
from typing import List, Dict, Any
from app.db.client import Database
from app.agent_infrastructure.infrastructure.llm_clients import gpt4o_mini

logger = logging.getLogger(__name__)


ROMANIZED_TAMIL_TERM_MAP = {
    "parotta": "பரோட்டா",
    "porotta": "பரோட்டா",
    "kadai": "கடை",
    "shop": "கடை",
    "tea": "டீ",
    "saloon": "சலூன்",
    "salon": "சலூன்",
    "hotel": "ஹோட்டல்",
    "mess": "மெஸ்",
}


def normalize_search_text(text: str) -> str:
    """Return NFKC-lowercased text with punctuation spaced out, Tamil and Devanagari kept, and repeated letters capped at two."""
    if not text:
        return ""

    normalized = unicodedata.normalize("NFKC", str(text)).lower()
    normalized = re.sub(r"[^\w\s\u0B80-\u0BFF\u0900-\u097F]", " ", normalized)
    normalized = re.sub(r"([a-z])\1{2,}", r"\1\1", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def contains_tamil_script(text: str) -> bool:
    """Return True if the text contains Tamil unicode codepoints."""
    if not text:
        return False
    return any("\u0B80" <= char <= "\u0BFF" for char in str(text))


def map_romanized_tamil_terms(text: str) -> str:
    """Return normalized text with each romanized Tamil token replaced by its Tamil-script word from ROMANIZED_TAMIL_TERM_MAP."""
    normalized = normalize_search_text(text)
    if not normalized:
        return ""

    tokens = normalized.split()
    mapped_tokens = [ROMANIZED_TAMIL_TERM_MAP.get(token, token) for token in tokens]
    return " ".join(mapped_tokens)


def _extract_keywords(text: str, stopwords: set[str]) -> List[str]:
    """Return normalized tokens of at least two characters that are not in the given stopwords."""
    tokens = normalize_search_text(text).split()
    return [token for token in tokens if len(token) >= 2 and token not in stopwords]


SEARCH_STOPWORDS: set[str] = {
    "near", "me", "stores", "store", "shop", "shops", "around",
    "in", "at", "for", "find", "looking", "show", "some", "any",
    "nearby", "local", "best", "good", "want", "need", "get", "i",
    "my", "the", "a", "an", "to", "help", "please", "can", "will",
    "business", "service", "services", "provider", "product", "products",
    "kadai", "கடை", "கடைகள்",
    "and", "or", "of", "with", "by", "on", "is", "are", "it", "that", "this",
    "someone", "somebody", "anyone", "anybody", "place", "places", "needs", "needed",
    "wanted", "know", "do", "does", "you", "who", "whom", "where", "what", "how",
    "there", "here", "am", "was", "were", "be", "been", "very", "really", "new",
    "custom", "customized", "customised", "customer", "personalized", "personalised",
    "premium", "quality", "professional", "comprehensive", "advanced", "assorted",
    "specialty", "solution", "solutions", "package", "packages", "plan", "plans",
    "consultation", "verified",
}
GENERIC_TRADE_WORDS: set[str] = {
    "repair", "repairs", "repairing", "service", "services", "servicing", "shop", "store",
    "delivery", "deliveries", "supply", "supplies", "supplier", "technician", "mechanic",
    "installation", "install", "maintenance", "fix", "fixing", "fitting", "diagnostic",
    "diagnostics", "spare", "parts", "work", "works", "job", "jobs", "home", "doorstep",
    "centre", "center", "agency", "dealer", "shopping", "outlet", "best", "cheap", "fast",
    "food", "item", "items", "goods", "product", "products", "order", "online",
}

_STEM_MIN_LEN = 3
_MAX_STEM_PASSES = 2
_STEM_SUFFIXES = (
    "ications", "ication", "ings", "ing", "ers", "er", "ians", "ian",
    "ists", "ist", "ions", "ion", "ries", "ry", "ies", "y", "als", "al",
    "es", "s", "ed",
)


def _stem(word: str) -> str:
    """Return an ASCII word with up to two suffixes and a trailing e stripped, passing non-ASCII words through."""
    if not word.isascii():
        return word
    stemmed = word
    for _ in range(_MAX_STEM_PASSES):
        for suffix in _STEM_SUFFIXES:
            if stemmed.endswith(suffix) and len(stemmed) - len(suffix) >= _STEM_MIN_LEN:
                stemmed = stemmed[: -len(suffix)]
                break
        else:
            break
    if len(stemmed) > _STEM_MIN_LEN and stemmed.endswith("e"):
        stemmed = stemmed[:-1]
    return stemmed

DESCRIPTION_ONLY_MATCH_WEIGHT = 0.5
MIN_TRADE_SET_DENOMINATOR = 2

def _token_matches(word: str, tokens: set[str], stems: set[str]) -> bool:
    """Return True if a word matches a listing token exactly, by stem, or by substring for non-ASCII scripts."""
    if word in tokens:
        return True
    if not word.isascii():
        return any(
            len(token) >= _STEM_MIN_LEN and (word in token or token in word)
            for token in tokens
            if not token.isascii()
        )
    return _stem(word) in stems


_ITEM_PREFIX_MIN_LEN = 6


def _shares_long_prefix(term: str, tokens: set[str]) -> bool:
    """Return True when a listing token and the item term share a prefix of at least six characters."""
    if len(term) < _ITEM_PREFIX_MIN_LEN:
        return False
    return any(
        len(token) >= _ITEM_PREFIX_MIN_LEN
        and (token.startswith(term[:_ITEM_PREFIX_MIN_LEN]) and (
            token.startswith(term) or term.startswith(token)
            or token[:_ITEM_PREFIX_MIN_LEN + 2] == term[:_ITEM_PREFIX_MIN_LEN + 2]
        ))
        for token in tokens
    )


def _item_terms_present(terms: List[str], fields: List[str]) -> bool:
    """Return True when any item term appears in the listing fields as a bigram, token, stem or long prefix."""
    if not terms:
        return False
    tokens, stems, bigrams = _listing_index(fields)
    return any(
        term in bigrams
        if " " in term
        else (_token_matches(term, tokens, stems) or _shares_long_prefix(term, tokens))
        for term in terms
    )


def _item_affinity_band(
    terms: List[str], primary_fields: List[str], description_fields: List[str]
) -> int:
    """Return 2 if item terms appear in primary fields, 1 if only in description fields, else 0."""
    if _item_terms_present(terms, primary_fields):
        return 2
    if _item_terms_present(terms, description_fields):
        return 1
    return 0


def _listing_index(fields: List[str]) -> tuple[set[str], set[str], set[str]]:
    """Return the tokens, stems and within-field bigrams of a listing's fields, including romanized-Tamil mappings."""
    tokens: set[str] = set()
    bigrams: set[str] = set()
    for field in fields:
        field_tokens = field.split()
        tokens.update(field_tokens)
        mapped_tokens = [ROMANIZED_TAMIL_TERM_MAP.get(t, t) for t in field_tokens]
        tokens.update(mapped_tokens)
        for sequence in (field_tokens, mapped_tokens):
            for i in range(len(sequence) - 1):
                bigrams.add(f"{sequence[i]} {sequence[i + 1]}")
    stems = {_stem(token) for token in tokens}
    return tokens, stems, bigrams

POSITIVE_REVIEW_MIN_RATING = 4
def filter_positive_reviews(reviews: Any) -> Any:
    """Return only reviews rated POSITIVE_REVIEW_MIN_RATING or higher, dropping unrated ones and passing non-lists through."""
    if not isinstance(reviews, list):
        return reviews
    kept: List[Any] = []
    for review in reviews:
        if not isinstance(review, dict):
            continue
        try:
            rating = float(review.get("rating"))
        except (TypeError, ValueError):
            logger.debug("search_helpers: review has a non-numeric rating; skipping it")
            continue
        if rating >= POSITIVE_REVIEW_MIN_RATING:
            kept.append(review)
    return kept


async def enrich_profiles_with_rating_data(profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Set booking count, rating count and average rating from etown_ratings on each profile in place, defaulting to zero."""
    if not profiles:
        return profiles
        
    provider_ids = set()
    for p in profiles:
        upid = p.get('userprofileid')
        if upid:
            provider_ids.add(upid.strip())
            
    if not provider_ids:
        return profiles
        
    try:
        query = """
            SELECT 
                provideruserprofileid, 
                SUM(memberbookingcount) as total_bookings,
                SUM(ratingcount) as total_ratings,
                AVG(overallrating) as avg_overall_rating
            FROM etown_ratings
            WHERE provideruserprofileid = ANY($1::text[])
            GROUP BY provideruserprofileid
        """
        rows = await Database.fetch(query, list(provider_ids))
        
        rating_map = {
            r['provideruserprofileid']: {
                'bookings': r['total_bookings'] or 0,
                'ratings': r['total_ratings'] or 0,
                'overall': float(r['avg_overall_rating'] or 0.0)
            } for r in rows
        }
        
        for p in profiles:
            upid = p.get('userprofileid', '').strip()
            data = rating_map.get(upid, {'bookings': 0, 'ratings': 0, 'overall': 0.0})
            p['memberbookingcount'] = data['bookings']
            p['ratingcount'] = data['ratings']
            p['overallrating'] = data['overall']
            
    except Exception as e:
        logger.error(f"Enrichment failed: {e}", exc_info=True)
        for p in profiles:
            p.setdefault('memberbookingcount', 0)
            p.setdefault('ratingcount', 0)
            p.setdefault('overallrating', 0.0)
            
    return profiles

def shown_ids_for_search(
    shown_by_term: Dict[str, Any] | None,
    search_term: str,
    flat_shown_ids: Any = None,
) -> set:
    """Return ids already shown by earlier searches whose content words contain or are contained by this search's.

    Args:
        shown_by_term: Map of search term to the ids that search showed.
        search_term: The query being run now.
        flat_shown_ids: Conversation-wide shown ids, used only when shown_by_term is empty.

    Returns:
        Set of ids to exclude from this search's results.
    """
    if not shown_by_term:
        return {str(i) for i in (flat_shown_ids or [])}

    def _content_words(text: str) -> set:
        return {
            word for word in normalize_search_text(text or "").split()
            if len(word) >= 2 and word not in SEARCH_STOPWORDS
        }

    current = _content_words(search_term)
    if not current:
        return set()

    ids: set = set()
    for term, term_ids in shown_by_term.items():
        recorded = _content_words(term)
        if recorded and (recorded <= current or current <= recorded):
            ids.update(str(i) for i in (term_ids or []))
    return ids


TRADE_PROXIMITY_WINDOW = 4


def _satisfied_trade(trade_terms: List[str], trade_fields: List[str]) -> str | None:
    """Return the first trade whose content words all appear within one field's proximity window, or None.

    Args:
        trade_terms: Trades resolved by service_intent, most likely first.
        trade_fields: Normalized name, services and servicecategory text, one string per field.

    Returns:
        The satisfied trade term, or None when none is fully present.
    """
    tokens, stems, _ = _listing_index(trade_fields)
    field_stem_sequences = [
        [_stem(ROMANIZED_TAMIL_TERM_MAP.get(token, token)) for token in field.split()]
        for field in trade_fields
        if field
    ]
    for trade in trade_terms:
        words = [
            word for word in normalize_search_text(trade).split()
            if len(word) >= 2 and word not in SEARCH_STOPWORDS
        ]
        if not words:
            continue
        if len(words) == 1:
            if _token_matches(words[0], tokens, stems):
                return trade
            continue
        wanted = {_stem(word) for word in words}
        window = max(TRADE_PROXIMITY_WINDOW, len(words) + 1)
        for sequence in field_stem_sequences:
            if any(
                wanted <= set(sequence[start:start + window])
                for start in range(max(1, len(sequence) - window + 1))
            ):
                return trade
    return None


async def filter_irrelevant_results(
    search_query: str,
    results: List[Dict[str, Any]],
    query_variants: List[str] | None = None,
    extra_keyword_sets: List[set[str]] | None = None,
    is_item_specific: bool = False,
    trade_terms: List[str] | None = None,
    accepted_categories: List[str] | None = None,
    item_terms: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """Keep results matching by words, trade or primary category, ranked and capped at 20, else up to 2 cross-script fallbacks.

    Args:
        search_query: The translated query whose words drive strict scoring.
        results: Candidate provider rows from the vector search.
        query_variants: Alternate phrasings of the query, used only for script detection.
        extra_keyword_sets: Extra accepted vocabularies, typically resolved trades, scored like user words.
        is_item_specific: Whether the request targets one physical item; raises the relevance bar.
        trade_terms: Resolved trades; a satisfied trade keeps a row regardless of word overlap.
        accepted_categories: Resolved canonical categories; the first keeps a row when no trade resolved or its description names one.
        item_terms: Item terms used to tag each row's _item_affinity band 0 to 2 for ranking.

    Returns:
        The relevant subset of results, or an empty list on error.
    """
    if not results:
        return []

    try:
        stopwords = SEARCH_STOPWORDS
        strict_query_candidates = [search_query]
        query_candidates = [search_query]
        if query_variants:
            query_candidates.extend([variant for variant in query_variants if variant])

        variant_keyword_sets = []
        query_has_tamil = False
        query_has_latin = False
        for query_candidate in strict_query_candidates:
            keyword_set = set(_extract_keywords(query_candidate, stopwords))
            if keyword_set:
                variant_keyword_sets.append(keyword_set)
        user_keyword_set_count = len(variant_keyword_sets)
        for extra_set in extra_keyword_sets or []:
            cleaned = {w for w in extra_set if len(w) >= 2 and w not in stopwords}
            if cleaned:
                variant_keyword_sets.append(cleaned)

        for query_candidate in query_candidates:
            query_has_tamil = query_has_tamil or contains_tamil_script(query_candidate)
            query_has_latin = query_has_latin or bool(re.search(r"[a-z]", normalize_search_text(query_candidate)))

        if not variant_keyword_sets:
            return results[:20]

        raw_words = normalize_search_text(search_query).split()
        cross_script_query = query_has_tamil and query_has_latin
        resolved_trades = [t for t in (trade_terms or []) if t and t.strip()]
        resolved_items = [t for t in (item_terms or []) if t and t.strip()]
        primary_category = next(
            (c for c in (accepted_categories or []) if c and c.strip()), None
        )

        is_item_specific_repair = is_item_specific and user_keyword_set_count > 0

        strict_matches = []
        fallback_candidates = []

        for r in results:
            trade_fields = [
                normalize_search_text(r.get('businessname', '') or ''),
                normalize_search_text(r.get('services', '') or ''),
                normalize_search_text(r.get('servicecategory', '') or ''),
            ]
            matched_trade = _satisfied_trade(resolved_trades, trade_fields)
            primary_fields = [
                normalize_search_text(r.get('businessname', '') or ''),
                normalize_search_text(r.get('services', '') or ''),
                normalize_search_text(r.get('businesscategory', '') or ''),
                normalize_search_text(r.get('servicecategory', '') or ''),
            ]
            if matched_trade or not resolved_trades:
                primary_fields.append( normalize_search_text((r.get('normalized_category', '') or '').replace('_', ' ')))
            description_fields = [normalize_search_text(r.get('servicedescription', '') or '')]
            primary_tokens, primary_stems, primary_bigrams = _listing_index(primary_fields)
            desc_tokens, desc_stems, desc_bigrams = _listing_index(description_fields)
            listing_bigrams = primary_bigrams | desc_bigrams
            matched_category = bool(
                primary_category
                and str(r.get('normalized_category', '') or '').strip().lower() == primary_category
            )
            r["_item_affinity"] = _item_affinity_band(
                resolved_items, trade_fields, description_fields
            )
            row_category = str(r.get('normalized_category', '') or '').strip().lower()
            r["_category_conflict"] = bool(
                primary_category
                and row_category
                and row_category != 'other'
                and row_category != primary_category
                and not r["_item_affinity"]
            )
            result_has_tamil = any(
                contains_tamil_script(field) for field in primary_fields + description_fields
            )

            def _match_weight(word: str) -> float:
                """1.0 for a match in a primary field, less for description-only, 0 for none."""
                mapped_word = map_romanized_tamil_terms(word)
                if (
                    _token_matches(word, primary_tokens, primary_stems)
                    or (mapped_word != word and _token_matches(mapped_word, primary_tokens, primary_stems))
                ):
                    return 1.0
                if (
                    _token_matches(word, desc_tokens, desc_stems)
                    or (mapped_word != word and _token_matches(mapped_word, desc_tokens, desc_stems))
                ):
                    return DESCRIPTION_ONLY_MATCH_WEIGHT
                return 0.0

            phrase_matches = 0
            if len(raw_words) >= 2:
                for i in range(len(raw_words) - 1):
                    word1, word2 = raw_words[i], raw_words[i + 1]
                    if len(word1) > 2 and len(word2) > 2 and word1 not in stopwords and word2 not in stopwords:
                        phrase = f"{word1} {word2}"
                        if phrase in listing_bigrams or map_romanized_tamil_terms(phrase) in listing_bigrams:
                            phrase_matches += 1

            best_total_relevance = 0.0
            best_phrase_matches = 0
            total_searchable_words = 0
            for set_index, variant_words_set in enumerate(variant_keyword_sets):
                variant_matches = sum(_match_weight(word) for word in variant_words_set)

                current_total_words = len(variant_words_set)
                if current_total_words == 0:
                    continue
                score_denominator = current_total_words
                if set_index >= user_keyword_set_count:
                    score_denominator = max(score_denominator, MIN_TRADE_SET_DENOMINATOR)

                word_relevance = variant_matches / score_denominator
                phrase_bonus = phrase_matches * 0.4
                total_relevance = word_relevance + phrase_bonus

                if total_relevance > best_total_relevance or total_searchable_words == 0:
                    best_total_relevance = total_relevance
                    best_phrase_matches = phrase_matches
                    total_searchable_words = current_total_words
            
            r["_matched_category"] = matched_category
            r["_matched_trade"] = matched_trade or ""

            if total_searchable_words == 0:
                strict_matches.append(r)
                continue

            if (
                matched_trade
                or (matched_category and not resolved_trades)
                or (matched_category and _satisfied_trade(resolved_trades, description_fields))
            ):
                r["_text_relevance"] = round(best_total_relevance, 4)
                strict_matches.append(r)
                continue

            if best_phrase_matches > 0:
                relevance_threshold = 0.8
            elif total_searchable_words == 2:
                if is_item_specific_repair:
                    relevance_threshold = 0.8
                else:
                    relevance_threshold = 0.75
            elif total_searchable_words > 2:
                if is_item_specific_repair:
                    relevance_threshold = 0.8
                else:
                    relevance_threshold = 0.5
            else:
                relevance_threshold = 0.6

            r["_text_relevance"] = round(best_total_relevance, 4)

            if best_total_relevance >= relevance_threshold:
                strict_matches.append(r)
                continue

            try:
                vector_rank = int(r.get("_vector_rank", 9999))
            except Exception:
                logger.debug("search_helpers: _vector_rank is not an integer; defaulting to 9999")
                vector_rank = 9999

            fallback_allowed = cross_script_query or (query_has_latin and result_has_tamil)
            if fallback_allowed and vector_rank == 1:
                r["_kept_by_cross_script_fallback"] = True
                fallback_candidates.append(r)

        if strict_matches:
            strict_matches.sort(
                key=lambda item: (
                    1 if item.get("_matched_trade") else 0,
                    item.get("_text_relevance", 0.0),
                    1 if item.get("_matched_category") else 0,
                    -int(item.get("_variant_priority", 999)),
                    -int(item.get("_vector_rank", 9999)),
                ),
                reverse=True,
            )
            logger.debug(
                "Lexical filtering kept %s of %s results; fallback not used",
                len(strict_matches),
                len(results),
            )
            return strict_matches[:20]

        if fallback_candidates:
            fallback_candidates.sort(
                key=lambda item: (
                    int(item.get("_variant_priority", 999)),
                    int(item.get("_vector_rank", 9999)),
                    item.get("distance_km", 9999),
                )
            )
            logger.debug(
                "Strict lexical filtering found no results; using %s fallback candidates",
                min(len(fallback_candidates), 2),
            )
            return fallback_candidates[:2]

        logger.debug("Strict lexical filtering removed all %s candidates", len(results))
        return []
        
    except Exception as e:
        logger.error(f"Filtering failed: {e}", exc_info=True)
        return []

async def llm_batch_relevance_filter(
    search_query: str,
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Ask gpt4o_mini to KEEP or REMOVE each result, returning all results if the reply is unusable or keeps none."""
    if not results or len(results) <= 1:
        return results
    try:
        snippet_lines = []
        for i, r in enumerate(results):
            business = (r.get("businessname") or "").strip()
            offering = (r.get("services") or "").strip()
            cat = (
                r.get("normalized_category")
                or r.get("servicecategory")
                or r.get("businesscategory")
                or ""
            ).strip()
            desc = (r.get("servicedescription") or "")[:120].strip()
            snippet_lines.append(
                f'[{i}] business="{business}" offering="{offering}" category="{cat}" desc="{desc}"'
            )
        snippets_text = "\n".join(snippet_lines)
        prompt = (
            f'User searched for: "{search_query}"\n\n'
            f"For each numbered result decide if it is GENUINELY relevant to the user's intent.\n"
            f"First determine what the user is actually looking for from the search query. "
            f"Then evaluate whether each provider genuinely matches that intent.\n"
            f"For a specific service request, a broad category match alone is not sufficient.\n"
            f"The provider must show evidence that it actually offers the requested service.\n"
            f"For service or professional-service searches, the provider must actually "
            f"offer the requested service or be a clearly matching provider type. "
            f"Do not consider a provider relevant merely because it belongs to the same "
            f"broad category as the requested service.\n"
            f"For product searches, a business that is clearly the appropriate type of "
            f"seller can be relevant even when the exact product is not explicitly listed, "
            f"because businesses may not list every product they sell.\n"
            f"Use the user's intent and the available provider information together. "
            f"Do not treat a broad category match alone as sufficient when the user's "
            f"query identifies a more specific service or provider type.\n"
            f'Judge mainly on "business" and "category". The "offering" field is auto-generated '
            f"and often names only one arbitrary item from the business's range, so a mismatch "
            f"there is not evidence against a business whose name and category do fit — a shop "
            f'named "... TAILORS" in category clothing_store serves someone looking for shirts '
            f'even if its offering reads "Accessories Line".\n'
            f"A result is NOT relevant if it only shares a keyword by coincidence — for example:\n"
            f"  - 'mobile' in 'Mobile Puncture Repair' when the user wants mobile app development\n"
            f"  - 'development' in a construction description when the user wants software development\n"
            f"  - 'mobile app' mentioned once in a description for a completely different service\n\n"
            f"Results:\n{snippets_text}\n\n"
            f'Respond ONLY with valid JSON: {{"decisions": ["KEEP" or "REMOVE", ...]}}\n'
            f"One entry per result in the same order. No explanation, no markdown."
        )
        response = await gpt4o_mini.ainvoke(prompt)
        content = response.content.strip()
        json_match = re.search(r'\{.*?"decisions".*?\}', content, re.DOTALL)
        if not json_match:
            logger.warning(
                "LLM batch filter: no valid JSON in response for query '%s', keeping all results",
                search_query,
            )
            return results
        data = json.loads(json_match.group())
        decisions = data.get("decisions", [])
        if len(decisions) != len(results):
            logger.warning(
                "LLM batch filter: decision count %s != result count %s for query '%s', keeping all",
                len(decisions),
                len(results),
                search_query,
            )
            return results
        kept = [r for r, d in zip(results, decisions) if str(d).strip().upper() == "KEEP"]
        removed_count = len(results) - len(kept)
        if removed_count:
            removed_names = [
                results[i].get("services") or results[i].get("businessname", "?")
                for i, d in enumerate(decisions)
                if str(d).strip().upper() != "KEEP"
            ]
            logger.info(
                "LLM batch filter removed %s result(s) for query '%s': %s",
                removed_count,
                search_query,
                removed_names,
            )
        return kept if kept else results
    except Exception as e:
        logger.error("LLM batch relevance filter failed for query '%s': %s", search_query, e, exc_info=True)
        return results

async def validate_search_context(search_query: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score keyword overlap between the query and the top five results, flagging relevance above 0.2."""
    if not results:
        return {"is_contextually_relevant": False}
        
    try:
        search_words = set(search_query.lower().split())
        total_relevance = 0
        
        for r in results[:5]:
            business_name = (r.get('businessname', '') or '').lower()
            category = (r.get('businesscategory', '') or '').lower()
            services = (r.get('services', '') or '').lower()
            
            result_text = f"{business_name} {category} {services}"
            matches = sum(1 for word in search_words if len(word) > 2 and word in result_text)
            
            if matches > 0:
                total_relevance += matches / len(search_words)
        
        avg_relevance = total_relevance / min(len(results), 5) if results else 0
        is_relevant = avg_relevance > 0.2
        
        return {
            "is_contextually_relevant": is_relevant,
            "relevance_score": round(avg_relevance, 2)
        }
        
    except Exception as e:
        logger.error(f"Context validation failed: {e}", exc_info=True)
        return {"is_contextually_relevant": True}  

async def generate_fallback_terms(query: str, failed_results: List[Any] = []) -> List[str]:
    """Return up to five fallback terms by suffixing each query word longer than two characters with service, provider or business."""
    try:
        original_words = query.lower().split()
        fallback_terms = []
        
        unique_terms = []
        for term in fallback_terms:
            if term.lower() != query.lower() and term not in unique_terms:
                unique_terms.append(term)
        
        if not unique_terms:
            for word in original_words:
                if len(word) > 2:
                    unique_terms.extend([f"{word} service", f"{word} provider", f"{word} business"])
        
        return unique_terms[:5]
        
    except Exception as e:
        logger.error(f"Fallback generation failed: {e}", exc_info=True)
        return [f"{query} service"]