"""Classification Rules - deterministic matching of template prompts ahead of any embedding call.

Classifier stage that matches frontend chip templates and closed-set pleasantries in the raw prompt with anchored regexes, extracts the handler arguments verbatim, and lets the classifier skip the embedding and k-NN vote for those turns.

Features:
    - Raw-prompt matching: anchored rules, most specific first; scope stays with bridge.py, so a match is not a dispatch permit
    - Never matched: slot-filling continuations, role-dependent "my ..." prompts and mid-booking answers
    - Frontend template matching with verbatim argument extraction, no entity guessing
    - Closed-set basics: greetings, thanks, farewell, acknowledgement, help, QR, pending ratings, address question
    - Follow-up guard: an anaphoric prompt never matches
    - Answerability guard: vague, negated, price-only or hard-bounded prompts never match

Classes:
    RuleMatch: One matched rule - intent, extracted handler args, and the rule name

Functions:
    is_context_dependent(): True when a prompt only means something as a follow-up
    needs_clarification(): True when the prompt cannot be answered without asking first
    _clean_name(): Strip quotes and trailing politeness from a captured business name
    _clean_term(): Reduce a captured phrase to the term the handler should receive
    _usable_term(): True when a cleaned term is substantive enough to search on
    match(): Match a raw prompt and return the intent plus extracted arguments

Dependencies:
    - app.agent_infrastructure.classification.taxonomy: canonical intent names
    - re, unicodedata: anchored matching and NFKC normalisation
    - dataclasses: the frozen RuleMatch record

Updates:
    10-Aug-2026: Initial rule layer for exact frontend templates.
    04-Sep-2026: Dropped "i want to book" search verb; guarded own-account objects; added closed-set basics and SMS greetings.
    07-Sep-2026: _clean_term() strips proximity words; added tpl_get_now and a web_search guard; tpl_enquire remapped.
    08-Sep-2026: Added needs_clarification(); near_me keeps text after proximity phrase; chips try quoted-name pattern first.
    27-Oct-2026: Follow-up guard ignores "this week", "a shop that sells", "is there", "more than" and upper-case IT.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.agent_infrastructure.classification import taxonomy

_QUOTES = "'\"‘’“”«»"
_Q = f"[{re.escape(_QUOTES)}]"
_QNAME = f"(?P<name>[^{re.escape(_QUOTES)}]+)"

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

_TRAILING_DETAIL_RE = re.compile(
    r"[,;.]?\s*(?:and\s+)?(?:please\s+)?give\s+(?:me\s+)?(?:the\s+)?(?:full\s+|more\s+|complete\s+)?details?\s*[.?!]*$",
    re.IGNORECASE,
)
_TRAILING_POLITE_RE = re.compile(
    r"[,;.]?\s*(?:please\s+help\s+me\s+with\s+the\s+booking\s+process|please\s+provide\s+more\s+details|"
    r"please|thanks|thank\s+you)\s*[.?!]*$",
    re.IGNORECASE,
)
_TRAILING_URGENCY_RE = re.compile(
    r"[,;.]?\s*\b(?:urgent(?:ly)?|asap|immediately|right\s+now|today|tonight|now)\b\s*[.?!]*$",
    re.IGNORECASE,
)
_LEAD_NEED_RE = re.compile(
    r"^(?:i\s+)?(?:need|needed|want|wanted|require|looking\s+for|searching\s+for)\s+"
    r"(?:a\s+|an\s+|the\s+|some\s+|to\s+)?",
    re.IGNORECASE,
)

_FOLLOWUP_RE = re.compile(
    r"\b(?:they|them|their|theirs|its|those|these|he|him|she|his|her|"
    r"same|above|previous|last\s+one|another|else|instead|again)\b"
    r"|\bmore\b(?!\s+than)"
    r"|\bother\b(?!\s+than)"
    r"|(?<!\bis\s)(?<!\bare\s)\bthere\b(?!\s+(?:is|are|any|a|an|some)\b)"
    r"|\b(?:this|that)\b(?!\s+(?:week|weekend|month|year|morning|afternoon|evening|night|time"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)"
    r"(?!\s+(?:sells?|repairs?|fix(?:es)?|does|do|can|could|will|is|are|has|have|offers?"
    r"|provides?|delivers?|makes?|serves?|accepts?|opens?|comes?|works?|stocks?|specialises?"
    r"|specializes?)\b)",
    re.IGNORECASE,
)
_IT_RE = re.compile(r"\bit\b", re.IGNORECASE)

_VAGUE_SUBJECT_RE = re.compile(
    r"\b(?:something|anything|some\s+(?:work|stuff|things?|help|jobs?)|a\s+few\s+things"
    r"|stuff|odd\s+jobs?|bits\s+and\s+pieces)\b",
    re.IGNORECASE,
)
_HELP_WITH_RE = re.compile(r"\b(?:help|assistance|support)\s+(?:me\s+)?with\b", re.IGNORECASE)
_ACTION_VERB_RE = re.compile(
    r"\b(?:repair|repairing|fix|fixing|servic(?:e|ing)|install(?:ing|ation)?|clean(?:ing)?"
    r"|buy|buying|purchase|sell|selling|rent|renting|book|booking|deliver|delivery"
    r"|find|get|replace|replacing|paint(?:ing)?|paste|fitting|assembl(?:e|y|ing))\b",
    re.IGNORECASE,
)
_EXCLUSION_RE = re.compile(
    r"\bnot\s+(?:a\s+|an\s+|the\s+|to\s+|for\s+)?"
    r"(?:repair|repairing|fix|fixing|buy|buying|purchase|book|booking|rent|renting"
    r"|sell|selling|servic(?:e|ing)|install(?:ing|ation)?|hire|hiring)\b"
    r"|\b(?:do\s+not|don'?t|dont|no\s+need\s+to|please\s+don'?t)\s+"
    r"(?:book|call|contact|order|buy|send|hire|schedule|arrange)\b"
    r"|\b(?:instead\s+of|rather\s+than|as\s+opposed\s+to)\b",
    re.IGNORECASE,
)
_INFO_ONLY_RE = re.compile(
    r"\bjust\s+(?:checking|asking|browsing|curious|want(?:ed|ing)?\s+to\s+know)\b"
    r"|\bonly\s+(?:checking|asking)\b"
    r"|\bhow\s+much\s+(?:does|do|is|are|will|would|it|they)\b"
    r"|\bwhat(?:'?s|\s+is|\s+are)?\s+the\s+"
    r"(?:charges?|price|prices|pricing|cost|costs|rate|rates|fee|fees)\b",
    re.IGNORECASE,
)
_UNHONOURABLE_CONSTRAINT_RE = re.compile(
    r"\bwithin\s+(?:the\s+next\s+)?\d+\s*(?:min|mins|minute|minutes|hour|hours|hr|hrs)\b"
    r"|\bin\s+(?:the\s+next\s+)?\d+\s*(?:min|mins|minute|minutes)\b"
    r"|\b(?:under|below|less\s+than|within)\s+(?:rs\.?\s*|₹\s*)?\d{2,}\b"
    r"|\bbudget\s+of\s+(?:rs\.?\s*|₹\s*)?\d+\b",
    re.IGNORECASE,
)
_NEED_MARKER_RE = re.compile(
    r"\b(?:need|needs|want|wants|looking\s+for|someone|somebody|book|find|get|arrange"
    r"|clean|cleaning|repair|fix|install|service|servicing|paint|painting)\b",
    re.IGNORECASE,
)
_CONJUNCTION_RE = re.compile(r"\s+(?:and|&|plus|along\s+with)\s+|\s*\+\s*", re.IGNORECASE)

_GREETINGS = {
    "hi", "hii", "hiii", "hey", "heyy", "hello", "helo", "hallo", "yo", "hiya",
    "hi there", "hello there", "hey there", "hi chaatie", "hello chaatie", "hey chaatie",
    "good morning", "good afternoon", "good evening", "good day", "greetings",
    "namaste", "namaskar", "namaskaram", "vanakkam", "vanakam", "hi vanakkam",
    "वणक्कम्", "நமஸ்காரம்",
    "வணக்கம்", "नमस्ते", "हैलो",
    "start", "hi!", "hello!",
    "hai", "haii", "helloo", "hellooo", "hiiii", "hlo", "hii there",
    "gm", "gud morning", "gud evening", "gud afternoon", "good noon",
    "hey chaatie hi", "hello hi",
}

_THANKS = {
    "thanks", "thank you", "thank u", "thanks a lot", "thank you so much",
    "thanks chaatie", "thank you chaatie", "thankyou", "tq", "ty", "nandri",
    "romba nandri", "dhanyavad", "dhanyavaad", "shukriya", "நன்றி", "धन्यवाद",
    "great thanks", "ok thanks", "okay thanks", "thanks a ton", "much appreciated",
}

_FAREWELL = {
    "bye", "byee", "bye bye", "goodbye", "good bye", "see you", "see ya",
    "good night", "gn", "tata", "ok bye", "okay bye", "thanks bye", "poitu varen",
    "வணக்கம் போய்ட்டு வரேன்", "अलविदा",
}

_AFFIRM = {
    "ok", "okay", "okey", "k", "kk", "fine", "sure", "alright", "all right",
    "got it", "understood", "cool", "nice", "good", "great", "super", "perfect",
    "sari", "seri", "theek hai", "ha", "haan",
}

_HELP_RE = re.compile(
    r"^\s*(?:"
    r"help|help\s+me|i\s+need\s+help|what\s+all\s+can\s+you\s+do"
    r"|how\s+can\s+you\s+help(?:\s+me)?|what\s+are\s+your\s+features"
    r"|what\s+services\s+do\s+you\s+(?:offer|provide)"
    r"|show\s+me\s+what\s+you\s+can\s+do"
    r")\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_QR_RE = re.compile(
    r"^\s*(?:"
    r"(?:show|give|generate|create|make|get)\s+(?:me\s+)?(?:my\s+|a\s+|the\s+)?qr(?:\s+code)?"
    r"|my\s+qr(?:\s+code)?"
    r"|qr(?:\s+code)?"
    r")\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_PENDING_RATINGS_RE = re.compile(
    r"^\s*(?:"
    r"(?:do\s+i\s+have\s+any\s+|any\s+|show\s+(?:me\s+)?(?:my\s+)?|my\s+)?pending\s+"
    r"(?:ratings?|reviews?|feedbacks?)"
    r"|(?:what|which)\s+(?:bookings?|services?|orders?)\s+(?:are\s+)?(?:waiting|pending)\s+"
    r"(?:for\s+)?(?:my\s+)?(?:rating|review|feedback)"
    r"|do\s+i\s+(?:need|have)\s+to\s+rate\s+(?:anything|something)"
    r"|anything\s+(?:left\s+)?to\s+rate"
    r")\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_MY_ADDRESS_RE = re.compile(
    r"^\s*(?:"
    r"what(?:'?s| is)\s+my\s+(?:address|delivery\s+address|profile\s+address)"
    r"|(?:show|confirm|check)\s+(?:me\s+)?my\s+(?:address|delivery\s+address|profile\s+address)"
    r"|is\s+my\s+(?:profile\s+)?address\s+correct"
    r")\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_ETOWN_INFO_RE = re.compile(
    r"^(?:"
    r"what(?:'?s| is)\s+etown"
    r"|who\s+(?:are\s+you|is\s+chaatie)"
    r"|what\s+(?:can|do)\s+you\s+do"
    r"|how\s+(?:does\s+etown\s+work|do(?:es)?\s+(?:i|this)\s+work)"
    r"|how\s+(?:do|can)\s+i\s+(?:become|register\s+as|sign\s+up\s+as)\s+(?:a\s+)?(?:provider|seller|vendor|business)"
    r"|tell\s+me\s+about\s+etown"
    r"|about\s+etown"
    r"|etown\s+(?:about|info|information|details)"
    r")\b.{0,40}$",
    re.IGNORECASE,
)

_TPL_BOOK_RE = re.compile(
    rf"^\s*i\s+want\s+to\s+book\s+{_Q}?(?P<name>.+?){_Q}?\s+services?\s+right\s+now\b",
    re.IGNORECASE,
)
_TPL_BOOK_QUOTED_RE = re.compile(
    rf"^\s*i\s+want\s+to\s+book\s+{_Q}(?P<name>.+?){_Q}\s+services?\b",
    re.IGNORECASE,
)
_TPL_GET_NOW_QUOTED_RE = re.compile(
    rf"^\s*(?:get|book)\s+now\s+{_Q}{_QNAME}{_Q}\s+service\s*/\s*product\b",
    re.IGNORECASE,
)
_TPL_GET_NOW_RE = re.compile(
    rf"^\s*(?:get|book)\s+now\s+{_Q}?(?P<name>.+?){_Q}?\s+service\s*/\s*product\b",
    re.IGNORECASE,
)
_TPL_ENQUIRE_QUOTED_RE = re.compile(
    rf"^\s*i\s+want\s+to\s+enquire\s+about\s+{_Q}{_QNAME}{_Q}\s+services?\b",
    re.IGNORECASE,
)
_TPL_ENQUIRE_RE = re.compile(
    rf"^\s*i\s+want\s+to\s+enquire\s+about\s+{_Q}?(?P<name>.+?){_Q}?\s+services?\b",
    re.IGNORECASE,
)
_WEB_SEARCH_RE = re.compile(
    r"\b(?:web\s*search|search\s+(?:the\s+|in\s+|on\s+)?web|google\s+(?:it|this|that))\b",
    re.IGNORECASE,
)
_TPL_MENU_ID_QUOTED_RE = re.compile(
    rf"^\s*(?:show\s+me\s+)?the\s+menu\s+for\s+{_Q}{_QNAME}{_Q}\s*,\s*"
    rf"providerservicesid\s+(?P<psid>{_UUID})\b",
    re.IGNORECASE,
)
_TPL_MENU_ID_RE = re.compile(
    rf"^\s*(?:show\s+me\s+)?the\s+menu\s+for\s+{_Q}?(?P<name>.+?){_Q}?\s*,\s*"
    rf"providerservicesid\s+(?P<psid>{_UUID})\b",
    re.IGNORECASE,
)
_TPL_MENU_ID_ALT_RE = re.compile(
    rf"^\s*show\s+me\s+the\s+menu\s+for\s+{_Q}?(?P<name>.+?){_Q}?\s*,\s*"
    rf"providerservicesid\s+(?P<psid>{_UUID})\b",
    re.IGNORECASE,
)

_TPL_BOOKING_ID_RE = re.compile(
    rf"(?:{_UUID})|(?:\b(?:booking|order|tracking|reference|ref|request)\s*"
    r"(?:id|no\.?|number|#|:)?\s*(?P<id>[A-Za-z0-9][A-Za-z0-9\-]{3,}))",
    re.IGNORECASE,
)
_BOOKING_CONTEXT_RE = re.compile(
    r"\b(?:booking|order|tracking|appointment)\b.{0,30}\b(?:status|detail|details|info|"
    r"information|update|where|track)\b|\b(?:status|track)\b.{0,30}\b(?:booking|order)\b",
    re.IGNORECASE,
)

_SEARCH_VERB_RE = re.compile(
    r"^\s*(?:"
    r"(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:find|show|get|suggest|recommend)\s+(?:me\s+)?"
    r"|please\s+(?:find|show|get|suggest|recommend)\s+(?:me\s+)?"
    r"|(?:i\s+(?:want|need|wanna)\s+(?:to\s+)?(?:find|hire)\s+)"
    r"|(?:i\s+(?:am|'m|m)\s+)?(?:looking|searching)\s+for\s+"
    r"|help\s+me\s+(?:find|get)\s+"
    r"|(?:find|show|search|get|suggest|recommend)\s+(?:me\s+)?"
    r")(?P<term>.+)$",
    re.IGNORECASE,
)
_NEAR_ME_RE = re.compile(
    r"^\s*(?P<term>.+?)\s+(?:near|around|close\s+to|nearby)\s+"
    r"(?:me|here|my\s+(?:place|location|area))\b(?P<tail>.*)$",
    re.IGNORECASE,
)
_MIN_TERM_LEN = 2
_TERM_STOPWORDS = {
    "me", "a", "an", "the", "some", "any", "one", "it", "this", "that", "here",
    "please", "now", "yes", "no", "ok", "okay",
}

_OWN_ACCOUNT_RE = re.compile(
    r"^(?:my|our)\s+(?:"
    r"booking|bookings|booking\s+requests?|requests?|orders?|order\s+history|history"
    r"|profile|account|details|posts?|feed|offers?|ratings?|reviews?|invites?"
    r"|invitations?|circle|favourites?|favorites?|collections?|towns?|plan|subscription"
    r")\b",
    re.IGNORECASE,
)


def is_context_dependent(text: Optional[str]) -> bool:
    """Return True when the prompt refers back to the previous turn, ignoring temporal, relative and existential uses and upper-case IT."""
    if not text:
        return False
    if _FOLLOWUP_RE.search(text):
        return True
    return any(m.group(0) != "IT" for m in _IT_RE.finditer(text))


def needs_clarification(text: Optional[str]) -> bool:
    """Return True when the prompt is vague, exclusionary, info-only, over-constrained or multi-need and needs a clarifying question."""
    if not text:
        return False
    if _VAGUE_SUBJECT_RE.search(text):
        return True
    if _HELP_WITH_RE.search(text) and not _ACTION_VERB_RE.search(text):
        return True
    if _EXCLUSION_RE.search(text):
        return True
    if _INFO_ONLY_RE.search(text):
        return True
    if _UNHONOURABLE_CONSTRAINT_RE.search(text):
        return True
    parts = [p for p in _CONJUNCTION_RE.split(text) if p and p.strip()]
    return len(parts) > 1 and sum(bool(_NEED_MARKER_RE.search(p)) for p in parts) > 1


@dataclass(frozen=True)
class RuleMatch:
    """One deterministic rule match.

    Attributes:
        intent: Canonical intent (see taxonomy) the rule recognised.
        args: Handler arguments extracted verbatim from the prompt. Consumed by
            ``dispatcher.dispatch`` in preference to its own heuristics; an empty dict
            means "dispatch as usual, the rule only settled the intent".
        rule: Rule name, recorded in the decision log as ``reason='rule:<name>'``.
    """

    intent: str
    args: Dict[str, Any] = field(default_factory=dict)
    rule: str = ""


def _clean_name(raw: Optional[str]) -> str:
    """Strip quotes, trailing politeness and punctuation from a captured business name."""
    if not raw:
        return ""
    name = unicodedata.normalize("NFKC", raw).strip()
    name = name.strip(_QUOTES + " \t")
    name = _TRAILING_POLITE_RE.sub("", name).strip()
    return name.strip(_QUOTES + " ,.;:!?").strip()


def _clean_term(raw: Optional[str]) -> str:
    """Strip trailing detail, politeness, urgency, proximity words, lead-in needs and articles from a captured search phrase."""
    if not raw:
        return ""
    term = unicodedata.normalize("NFKC", raw).strip()
    term = _TRAILING_DETAIL_RE.sub("", term).strip()
    term = _TRAILING_POLITE_RE.sub("", term).strip()
    term = _TRAILING_URGENCY_RE.sub("", term).strip()
    term = re.sub(
        r"\s*\b(?:near|around|close\s+to|nearby)(?:\s+(?:me|here|us|my\s+\w+))?\s*$",
        "", term, flags=re.IGNORECASE).strip()
    term = re.sub(r"^(?:near|nearby|around|local|closest|nearest)\s+", "", term,
                  flags=re.IGNORECASE).strip()
    term = _LEAD_NEED_RE.sub("", term, count=1).strip()
    term = term.strip(_QUOTES + " ,.;:!?").strip()
    term = re.sub(r"^(?:a|an|the|some|any)\s+", "", term, flags=re.IGNORECASE).strip()
    return term


def _usable_term(term: str) -> bool:
    """Return True if a cleaned term meets the minimum length and contains a non-stopword."""
    if len(term) < _MIN_TERM_LEN:
        return False
    words = [w for w in re.split(r"\s+", term.lower()) if w]
    return bool(words) and any(w not in _TERM_STOPWORDS for w in words)


def match(text: Optional[str]) -> Optional[RuleMatch]:
    """Return the first frontend-template, closed-set or search-shape rule that matches a raw prompt, or None."""
    if not text or not text.strip():
        return None
    raw = text.strip()

    for pattern in (_TPL_BOOK_QUOTED_RE, _TPL_BOOK_RE):
        m = pattern.match(raw)
        if m:
            name = _clean_name(m.group("name"))
            if name:
                return RuleMatch("booknow_provider", {"search_term": name}, "tpl_book")

    for pattern in (_TPL_GET_NOW_QUOTED_RE, _TPL_GET_NOW_RE):
        m = pattern.match(raw)
        if m:
            name = _clean_name(m.group("name"))
            if name:
                return RuleMatch("booknow_provider", {"search_term": name}, "tpl_get_now")

    for pattern in (_TPL_MENU_ID_QUOTED_RE, _TPL_MENU_ID_RE, _TPL_MENU_ID_ALT_RE):
        m = pattern.match(raw)
        if m:
            return RuleMatch(
                "provider_service_insights",
                {"providerservicesid": m.group("psid"), "business_name": _clean_name(m.group("name"))},
                "tpl_menu",
            )

    for pattern in (_TPL_ENQUIRE_QUOTED_RE, _TPL_ENQUIRE_RE):
        m = pattern.match(raw)
        if m:
            name = _clean_name(m.group("name"))
            if name:
                return RuleMatch(
                    "provider_service_insights", {"business_name": name}, "tpl_enquire"
                )

    collapsed = re.sub(r"\s+", " ", raw.lower()).strip()
    bare = collapsed.rstrip("!.,?")
    if bare in _GREETINGS or collapsed in _GREETINGS:
        return RuleMatch(taxonomy.GREETING, {}, "greeting")
    if bare in _THANKS:
        return RuleMatch(taxonomy.GREETING, {"smalltalk": "thanks"}, "thanks")
    if bare in _FAREWELL:
        return RuleMatch(taxonomy.GREETING, {"smalltalk": "farewell"}, "farewell")
    if bare in _AFFIRM:
        return RuleMatch(taxonomy.GREETING, {"smalltalk": "affirm"}, "affirm")

    if _ETOWN_INFO_RE.match(raw) or _HELP_RE.match(raw):
        return RuleMatch("provide_etown_info", {}, "etown_info")

    if _QR_RE.match(raw):
        return RuleMatch("generate_qr", {}, "qr")

    if _PENDING_RATINGS_RE.match(raw):
        return RuleMatch("pending_ratings", {}, "pending_ratings")

    if _MY_ADDRESS_RE.match(raw):
        return RuleMatch("address_confirmation", {}, "my_address")

    if _BOOKING_CONTEXT_RE.search(raw):
        m = _TPL_BOOKING_ID_RE.search(raw)
        if m:
            booking_id = (m.group("id") or m.group(0) or "").strip("-")
            if booking_id and any(ch.isdigit() for ch in booking_id):
                return RuleMatch("booking_info", {"booking_id": booking_id}, "booking_id")

    if is_context_dependent(raw):
        return None

    if _WEB_SEARCH_RE.search(raw):
        return RuleMatch("web_search", {}, "web_search")

    if needs_clarification(raw):
        return None

    for pattern, intent, rule_name in (
        (_SEARCH_VERB_RE, "search_provider", "search_verb"),
        (_NEAR_ME_RE, "search_provider", "near_me"),
    ):
        m = pattern.match(raw)
        if not m:
            continue
        tail = (m.groupdict().get("tail") or "").strip()
        term = _clean_term(f"{m.group('term')} {tail}".strip() if tail else m.group("term"))
        if _OWN_ACCOUNT_RE.match(term):
            return None
        if _usable_term(term):
            return RuleMatch(intent, {"search_term": term}, rule_name)

    return None
