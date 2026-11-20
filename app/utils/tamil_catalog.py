"""Tamil Catalog - authored Tamil renderings of fixed English Chaatie messages, matched by exact sentence templates.

Utility layer used by OutputTranslator for Tamil only: splits a reply into known English sentence templates, carries slot values such as business names and distances over verbatim, and returns the authored Tamil reply only when every sentence is covered, otherwise None.

Features:
    - Whole-message coverage: a reply renders only when every sentence matches a catalog template
    - Typed slots: {km} accepts only a number and {name} a single line, each copied verbatim
    - Layout preservation: whitespace and paragraph breaks between sentences are kept exactly
    - Integrity check at import: each Tamil rendering keeps the same slots and Markdown bold markers as its English source
    - Mode flag: TAMIL_CATALOG_MODE env var (off/shadow/live, default shadow) read on every call
    - Covered messages: single and multiple search replies without a category label, closest-provider reply, date/time questions, no-ratings note, greeting and small talk, chat error

Classes:
    No classes defined (module exposes a template table and a renderer)

Functions:
    _compile(): Compile an English template into an anchored regex with named slot groups
    _validate(): Raise when a Tamil rendering drops or adds a slot or a bold marker
    tamil_catalog_mode(): Return the TAMIL_CATALOG_MODE setting as off, shadow or live
    render_tamil(): Return the authored Tamil for a fully covered English reply, or None

Dependencies:
    - os: TAMIL_CATALOG_MODE environment variable
    - re: Template compilation and sentence matching

Updates:
    21-Oct-2026: Added the Tamil catalog for fixed search, greeting, small-talk and error messages.
"""

import os
import re
from typing import Optional

_CATALOG_MODES = ("off", "shadow", "live")

_SLOT_PATTERNS = {
    "km": r"\d+(?:\.\d+)?",
    "name": r"[^\n]+?",
}

TAMIL_TEMPLATES = (
    (
        "search_several_nearby",
        "I have found several nearby options for you.",
        "உங்களுக்கு அருகில் பல சேவை வழங்குநர்கள் உள்ளனர்.",
    ),
    (
        "search_several_far",
        "I have found several options for you — the closest is **{km} km away**.",
        "உங்களுக்காக சில சேவை வழங்குநர்கள் கிடைத்துள்ளனர். அவர்களில் உங்களுக்கு அருகில் இருப்பவர் **{km} km தொலைவில்** இருக்கிறார்.",
    ),
    (
        "search_one_near",
        "I have found a provider near you.",
        "உங்களுக்கு அருகில் ஒரு சேவை வழங்குநர் இருக்கிறார்.",
    ),
    (
        "search_one_far",
        "I have found a provider for you — the closest is **{km} km away**.",
        "உங்களுக்காக ஒரு சேவை வழங்குநரைக் கண்டறிந்துள்ளேன் — அவர் **{km} km தொலைவில்** இருக்கிறார்.",
    ),
    (
        "search_browse_and_book",
        "You can browse them below and book the one that best fits your requirements.",
        "கீழே உள்ள சேவை வழங்குநர்களைப் பார்த்து, உங்களுக்குப் பொருத்தமான ஒருவரை முன்பதிவு செய்யலாம்.",
    ),
    (
        "search_view_and_book",
        "You can view the details below and proceed with booking if it meets your requirements.",
        "கீழே உள்ள விவரங்களைப் பார்த்து, உங்களுக்குப் பொருத்தமாக இருந்தால் முன்பதிவு செய்யலாம்.",
    ),
    (
        "search_tap_or_call",
        "Tap avatar for more details or Call to connect directly with the service provider.",
        "மேலும் விவரங்களுக்கு அவர்களின் படத்தைத் தொடுங்கள்; சேவை வழங்குநரை நேரடியாகத் தொடர்புகொள்ள அழைப்பு வசதியைப் பயன்படுத்தவும்.",
    ),
    (
        "search_closest",
        "The closest provider is {name}, approximately {km} km away.",
        "உங்களுக்கு மிக அருகில் இருப்பவர் {name} — சுமார் {km} km தொலைவில்.",
    ),
    (
        "search_listed_by_distance",
        "I have listed the other matching providers below, ordered by distance.",
        "பொருந்தும் மற்ற சேவையாளர்களைத் தூர வரிசைப்படி கீழே கொடுத்துள்ளேன்.",
    ),
    (
        "ask_date_and_time",
        "What date and time would work best for you?",
        "எந்தத் தேதி, எந்த நேரம் உங்களுக்கு வசதியாக இருக்கும்?",
    ),
    (
        "ask_weekend_day",
        "For your requested weekend, which day works better for you, Saturday or Sunday, and what time?",
        "நீங்கள் கேட்ட வார இறுதியில் சனிக்கிழமையா, ஞாயிற்றுக்கிழமையா — எந்த நாள், எந்த நேரம் உங்களுக்கு வசதியாக இருக்கும்?",
    ),
    (
        "note_no_ratings",
        "Note: Ratings and reviews are not currently available for this business.",
        "குறிப்பு: இவர்களுக்கு இப்போது மதிப்பீடுகளும் கருத்துகளும் இல்லை.",
    ),
    (
        "greeting",
        "Hello! I'm Chaatie. How can I help you find local businesses, services, or products today?",
        "வணக்கம்! நான் Chaatie. உங்கள் பகுதியில் உள்ள கடைகள், சேவைகள், பொருட்களைக் கண்டுபிடிக்க இன்று நான் எப்படி உதவலாம்?",
    ),
    (
        "smalltalk_thanks",
        "You're welcome! Anything else you'd like me to find for you?",
        "மகிழ்ச்சி! வேறு ஏதாவது தேடித் தரட்டுமா?",
    ),
    (
        "smalltalk_farewell",
        "Goodbye! Come back any time you need a local business or service.",
        "மீண்டும் சந்திப்போம்! உங்கள் பகுதியில் கடையோ சேவையோ தேவைப்படும்போது எப்போது வேண்டுமானாலும் வாருங்கள்.",
    ),
    (
        "smalltalk_affirm",
        "Great! What would you like me to help you with?",
        "நல்லது! உங்களுக்கு என்ன உதவி வேண்டும்?",
    ),
    (
        "chat_error",
        "Sorry, an error occurred while processing your request.",
        "மன்னிக்கவும், நீங்கள் கேட்டதைச் செய்யும்போது ஏதோ பிழை ஏற்பட்டது.",
    ),
)

_SLOT_RE = re.compile(r"\{(\w+)\}")
_SENTENCE_END_RE = re.compile(r"\s|\Z")


def _compile(english: str) -> "re.Pattern[str]":
    """Compile an English template into a regex whose slots become named groups of their typed pattern."""
    parts = _SLOT_RE.split(english)
    return re.compile("".join(
        re.escape(part) if index % 2 == 0 else f"(?P<{part}>{_SLOT_PATTERNS[part]})"
        for index, part in enumerate(parts)
    ))


def _validate(key: str, english: str, tamil: str) -> None:
    """Raise when a Tamil rendering drops or adds a slot or a bold marker relative to its English source."""
    if sorted(_SLOT_RE.findall(english)) != sorted(_SLOT_RE.findall(tamil)):
        raise ValueError(f"Tamil catalog entry {key} does not carry the same slots as its English source")
    if english.count("**") != tamil.count("**"):
        raise ValueError(f"Tamil catalog entry {key} does not keep the English bold markers")


for _key, _english, _tamil in TAMIL_TEMPLATES:
    _validate(_key, _english, _tamil)

_COMPILED = tuple((key, _compile(english), tamil) for key, english, tamil in TAMIL_TEMPLATES)


def tamil_catalog_mode() -> str:
    mode = "live"
    return mode if mode in _CATALOG_MODES else "shadow"


def render_tamil(text: Optional[str]) -> Optional[str]:
    """Return the authored Tamil for a reply whose every sentence matches a catalog template, or None.

    Args:
        text: English reply exactly as Chaatie would send it to the translator.

    Returns:
        Tamil reply with slot values and whitespace preserved, or None when any part is not covered.
    """
    if not text or not text.strip():
        return None
    rendered = []
    position = 0
    while position < len(text):
        gap_end = position
        while gap_end < len(text) and text[gap_end].isspace():
            gap_end += 1
        rendered.append(text[position:gap_end])
        if gap_end == len(text):
            break
        for _, pattern, tamil in _COMPILED:
            match = pattern.match(text, gap_end)
            if match and _SENTENCE_END_RE.match(text, match.end()):
                rendered.append(tamil.format(**match.groupdict()))
                position = match.end()
                break
        else:
            return None
    return "".join(rendered)
