"""Generate Image Tool - LLM design planning, FLUX.2-klein generation, exact typesetting and blob storage.

Agent tool handler that plans a text-free scene plus grounded text with gpt-4o-mini, generates it via the aimodel gateway, rejects stray lettering, typesets in one of four layouts and uploads to Azure Blob; mytown_utils reuses it for town cards.

Pipeline:
    1. Plan: logo/offer/feed/general get a JSON design (scene, headline, subline, footer, accent, font, layout) from gpt-4o-mini, with provider details, brand colour and any previous design; town keeps the plain enhancer.
    2. Generate: GET /v1/images/generations with response_format=png and a seed that refinements reuse.
    3. Check: gpt-4o-mini vision looks for stray lettering and one regeneration with a new seed and plain-surface wording follows when it finds some.
    4. Typeset: logos are recomposed on pure white under the name; posters blur and darken the layout's text area, then add text and a footer badge.
    5. Watermark: stamp app/assets/watermark.png bottom-right (top-centre for town images) and embed AI-generated metadata.
    6. Store: upload to Azure Blob and return its URL, or return an inline base64 data URL when store is off or the upload fails.

Features:
    - Design Planning: the image model garbles letters, so every word is typeset afterwards and the scene stays text-free and affirmative
    - Business Context: providers' business name, category and phone feed the plan so logos and posters carry the real name
    - Grounded Text: segments with placeholders, or words, numbers, phones or months absent from the request, business values and previous text, are dropped and logged
    - Text On Request: general images carry a headline only when the request asks for text, a caption, a title or quotes words
    - Layouts: top, bottom, center or side text areas picked from the seed and aspect ratio (refinements keep theirs) and given to the planner
    - Stray Text Check: every designed image gets a vision check that regenerates once when the picture draws its own lettering
    - Footer Names: an all-lowercase business name in an offer or feed footer is capitalised like a logo name
    - Provider Branding: offer and feed posters carry the provider's profile image as a round avatar and, when fresh, its dominant colour as the accent
    - Phone On Request: the provider's profile number reaches the planner only when the request asks for a number
    - Refinement: a previous design and seed are reused so an edit keeps the composition; visual edits rewrite the scene itself
    - Exact Typesetting: Poppins, DM Serif Display and Noto Sans for nine Indic scripts from app/assets/fonts, switched per script run
    - Logo Cleanup: near-white backgrounds flattened to pure white and the emblem re-centred above the name
    - Image Generation: 256-1280px sides (multiples of 16), optional seed and steps override
    - Web Compression: optional jpeg/webp re-encode and downscale before upload (~8-16x smaller)
    - Blob Offloading: keeps chat state/history small by returning a URL, not base64; store=False returns inline instead
    - Watermarking: semi-transparent Etown mark bottom-right (top-centre for town images or on request), text whitened on dark corners; IMAGE_WATERMARK_ENABLED and the per-call watermark flag toggle it
    - AI Provenance: PNG text, EXIF and IPTC trainedAlgorithmicMedia XMP tags mark every file as AI-generated
    - Graceful Degradation: planning, typesetting, upload and generation each fail soft
    - Raw Prompt Mode: enhance=False skips every gpt-4o-mini call and sends the caller's prompt to the image model unchanged
    - Structured Output: consistent (message, implicit_output) tuple, the message listing exactly the typeset text

Classes:
    No classes defined (module exposes async helper and entry-point functions)

Functions:
    _clamp_side(): Clamp a dimension to 256-1280 and snap it to a multiple of 16
    enhance_image_prompt(): Expand a raw user request into a detailed image prompt
    plan_image_design(): Ask gpt-4o-mini for a JSON design of text-free scene plus exact text fields
    _layout_text(): Split text into font runs and wrap it to fit a width, shrinking the size as needed
    _draw_layout(): Draw a laid-out text block centred or left-aligned and return its bottom edge
    _typeset_design(): Typeset a design's text onto the generated image in its layout, recomposing logos on white
    _has_stray_text(): Ask gpt-4o-mini vision whether the untypeset picture shows lettering
    _apply_watermark(): Stamp the Etown mark bottom-right or top-centre and tag the PNG as AI-generated
    _encode_for_web(): Re-encode the model PNG as jpeg/webp (optionally downscaled) with AI metadata
    _store_png(): Encode and upload to Azure Blob; return (full_url, blob_path) or (None, None)
    handle_generate_image(): Main entry point — plan, generate, typeset, store, and format

Dependencies:
    - httpx: Async HTTP client for the image model call
    - Pillow: lazily imported for typesetting, the watermark composite and the jpeg/webp re-encode (raqm shapes Indic text)
    - app/assets/watermark.png: trimmed Etown logo used as the watermark
    - app/assets/fonts: OFL fonts used for typesetting
    - app.db.client: provider business details and profile image path from etown_provider
    - app.agent_infrastructure.infrastructure.llm_clients: gpt-4o-mini creative client for planning, gpt-4o-mini for the vision check
    - app.agent_infrastructure.prompt_templates.templates: logo/offer/feed/general design prompts and the town prompt
    - app.utils.blob_storage: Azure Blob upload + base URL helpers
    - app.core.config: image endpoint URL, timeout, aimodel gateway key and IMAGE_WATERMARK_ENABLED
    - app.core.logging: centralized logging

Updates:
    09-Jul-2026: Initial creation - prompt enhancement plus Z-Image-Turbo generation with blob offloading.
    20-Jul-2026: Added the 'town' enhancement style for the town listing-card endpoint.
    22-Jul-2026: Callers may pass an explicit enhance_system_prompt overriding the image_type mapping.
    31-Aug-2026: Retargeted at /v1/images/generations (FLUX.2-klein); JPEG/WebP storage options.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    28-Sep-2026: Logo negative prompt drops checkerboards; Etown watermark behind IMAGE_WATERMARK_ENABLED; AI-generated metadata on every image.
    29-Sep-2026: JSON design planning with exact typeset text; provider business context; seed-keeping refinement; logo white cleanup and name capitals; negative prompts dropped.
    30-Sep-2026: Restored design planning and typesetting; ungrounded text segments dropped; general text only on request; profile phone only on request; four layouts; stray-text check; provider avatar and brand colour; footer names capitalised; per-call watermark and store flags.
    08-Oct-2026: Removed LangSmith tracing.
    12-Oct-2026: Town images carry the watermark top-centre.
    26-Oct-2026: Added watermark_top_center for a top-centre watermark; enhance=False sends the raw prompt without planning or stray-text check; store_prefix picks the blob folder.
"""

import asyncio
import base64
import json
import os
import random
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.agent_infrastructure.infrastructure.llm_clients import gpt4o_mini, gpt4o_mini_creative
from app.agent_infrastructure.prompt_templates.templates import (
    BUSINESS_LOGO_ENHANCE_PROMPT,
    OFFER_POST_PROMPT,
    FEED_POST_PROMPT,
    GENERAL_IMAGE_DESIGN_PROMPT,
    IMAGE_TEXT_CHECK_PROMPT,
    TOWN_CARD_ENHANCE_PROMPT,
)
from app.utils.blob_storage import (
    upload_blob_bytes,
    normalize_container_name,
    current_timestamp_prefix,
    get_blob_base_url,
)

_GENERAL_ENHANCE_PROMPT = """You are an expert prompt engineer for text-to-image diffusion models.

Transform the user's short request into a single, vivid, detailed image-generation prompt with helpful specifics about subject, composition, lighting, style, color palette, and mood, while staying faithful to the user's intent.

Use only what the user explicitly provided or clearly implied. Write only in the affirmative, describing what the image contains, and describe surfaces as plain and unlettered.

Return only the enhanced image generation prompt. Do not include explanations, headings, markdown, or multiple prompt variations."""

_MAX_SIDE = 1280

_ASSETS_DIR = Path(__file__).resolve().parents[3] / "assets"
_WATERMARK_PATH = _ASSETS_DIR / "watermark.png"
_FONTS_DIR = _ASSETS_DIR / "fonts"
_WATERMARK_WIDTH_RATIO = 0.16
_WATERMARK_MARGIN_RATIO = 0.03
_WATERMARK_TOP_OFFSET_RATIO = 0.08
_WATERMARK_OPACITY = 0.85
_WATERMARK_SHADOW_OPACITY = 0.45
_WATERMARK_DARK_LUMA = 128

_AI_DESCRIPTION = "AI-generated image created with Etown"
_AI_SOFTWARE = "Etown AI"
_AI_XMP = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
    'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia" '
    f'xmp:CreatorTool="{_AI_SOFTWARE}">'
    f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{_AI_DESCRIPTION}</rdf:li></rdf:Alt></dc:description>'
    '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
)

_DESIGN_PROMPTS = {
    "logo": BUSINESS_LOGO_ENHANCE_PROMPT,
    "offer": OFFER_POST_PROMPT,
    "feed": FEED_POST_PROMPT,
    "general": GENERAL_IMAGE_DESIGN_PROMPT,
}

_TEXT_LIMITS = {"headline": 60, "subline": 90, "footer": 110}
_HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
_PHONE_NUMBER = re.compile(r"\+?\d[\d\s()-]{5,}\d")
_PLACEHOLDER = re.compile(r"[\[\]{}<>]|\byour (business|shop|store|brand|company)\b|\bbusiness name\b|\bphone number\b|\bx{3,}\b", re.IGNORECASE)
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+)?{_MONTH}(?!\w)|\b{_MONTH}\s*(\d{{1,2}})(?:st|nd|rd|th)?\b|\b(\d{{1,2}})[/.-](\d{{1,2}})(?:[/.-](\d{{2,4}}))?\b",
    re.IGNORECASE,
)
_ASKS_FOR_PHONE = re.compile(r"\b(phone|number|mobile|contact|call|whatsapp|reach)\b|नंबर|फोन|எண்", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_][^\s\d.,!?:;·&()\[\]{}'\"“”‘’/+%#*@|-]*")
_GENERIC_WORDS = frozenset("""
a an the and or of for on in at to with from by is are be this that these it its our us we you your all every any
offer offers off sale sales flat discount discounts deal deals save special exclusive limited only now new grand
opening open opened launch launching introducing announcing announcement join get buy free extra more best top
great amazing big mega hurry visit call book order shop available coming soon welcome celebrate celebration
celebrations festive season happy wishes greetings enjoy discover explore try fresh quality premium valid till
until upto up per each plus here there shop store starting start starts started back returns presenting presents
introduces introducing inviting invite
jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july august september
october november december
""".split())
_LAYOUTS = ("top", "bottom", "center", "side")
_ASKS_FOR_TEXT = re.compile(r"[\"'“‘]|\b(caption|captioned|title|titled|text|words?|written|write|writing|saying|says|quote|headline|lettering)\b", re.IGNORECASE)
_PLAIN_SURFACES = " Every sign, banner, poster, screen, label, bottle, box, package and wall in the picture is plain, blank and unlettered."

_LATIN_FONTS = {
    "heavy": "Poppins-ExtraBold.ttf",
    "bold": "Poppins-Bold.ttf",
    "semibold": "Poppins-SemiBold.ttf",
    "medium": "Poppins-Medium.ttf",
    "serif": "DMSerifDisplay-Regular.ttf",
}
_SCRIPT_FONTS = (
    (0x0900, 0x097F, "NotoSansDevanagari-Bold.ttf", "NotoSansDevanagari-SemiBold.ttf"),
    (0x0980, 0x09FF, "NotoSansBengali-Bold.ttf", "NotoSansBengali-SemiBold.ttf"),
    (0x0A00, 0x0A7F, "NotoSansGurmukhi-Bold.ttf", "NotoSansGurmukhi-SemiBold.ttf"),
    (0x0A80, 0x0AFF, "NotoSansGujarati-Bold.ttf", "NotoSansGujarati-SemiBold.ttf"),
    (0x0B00, 0x0B7F, "NotoSansOriya-Bold.ttf", "NotoSansOriya-Bold.ttf"),
    (0x0B80, 0x0BFF, "NotoSansTamil-Bold.ttf", "NotoSansTamil-SemiBold.ttf"),
    (0x0C00, 0x0C7F, "NotoSansTelugu-Bold.ttf", "NotoSansTelugu-SemiBold.ttf"),
    (0x0C80, 0x0CFF, "NotoSansKannada-Bold.ttf", "NotoSansKannada-SemiBold.ttf"),
    (0x0D00, 0x0D7F, "NotoSansMalayalam-Bold.ttf", "NotoSansMalayalam-SemiBold.ttf"),
)
_FONT_CACHE: Dict[Tuple[str, int], Any] = {}

_BUSINESS_QUERY = """
    SELECT businessname, businesscategory, mobilenumber, serviceimageurl
      FROM etown_provider
     WHERE userprofileid = $1
       AND lower(coalesce(deleted, 'false')) <> 'true'
       AND lower(coalesce(deactivated, 'false')) <> 'true'
     ORDER BY modifieddate DESC NULLS LAST, providerid
     LIMIT 1
"""


def _clamp_side(value: int) -> int:
    """Clamp an image side to 256-1280 pixels and round it down to a multiple of 16."""
    side = max(256, min(_MAX_SIDE, int(value)))
    return side - (side % 16)


async def enhance_image_prompt(
    raw_prompt: str,
    image_type: str = "general",
    system_prompt: Optional[str] = None,
) -> str:
    """Expand a short image request into a detailed prompt with gpt-4o-mini, returning the original on failure.

    Args:
        raw_prompt: The user's raw image description.
        image_type: town selects the town card prompt; anything else uses the plain general prompt.
        system_prompt: Non-blank value overrides the image_type mapping.

    Returns:
        The enhanced prompt, or the stripped original prompt on failure.
    """
    cleaned = (raw_prompt or "").strip()
    if not cleaned:
        return cleaned

    system_prompt = (system_prompt or "").strip() or (
        TOWN_CARD_ENHANCE_PROMPT if image_type == "town" else _GENERAL_ENHANCE_PROMPT
    )

    try:
        response = await gpt4o_mini_creative.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": cleaned},
            ]
        )
        content = getattr(response, "content", None)
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        enhanced = (content or "").strip().strip('"').strip()
        if enhanced:
            logger.info(
                "IMAGE_PROMPT_ENHANCED | original='%s' | enhanced='%s'",
                cleaned[:120],
                enhanced[:200],
            )
            return enhanced
        logger.warning("IMAGE_PROMPT_ENHANCE_EMPTY | falling back to raw prompt")
    except Exception as exc:
        logger.warning("IMAGE_PROMPT_ENHANCE_FAILED | error=%s | falling back to raw prompt", exc)

    return cleaned


async def plan_image_design(
    raw_prompt: str,
    image_type: str,
    business: Optional[Dict[str, Any]] = None,
    previous: Optional[Dict[str, Any]] = None,
    layout: Optional[str] = None,
) -> Dict[str, str]:
    """Ask gpt-4o-mini for a JSON design of a text-free scene plus exact text, falling back to the raw prompt with no text.

    Args:
        raw_prompt: The user's raw image description.
        image_type: logo, offer, feed or general; selects the design prompt.
        business: Optional provider business details with businessname, businesscategory, mobilenumber and brand_colour.
        previous: Optional previous image with enhanced_prompt and design, for a refinement.
        layout: Optional text layout the scene is composed around; it overrides the planner's choice.

    Returns:
        Dict with scene, headline, subline, footer, accent and font.
    """
    fallback = {"scene": raw_prompt, "headline": "", "subline": "", "footer": "", "accent": "", "font": "sans", "layout": layout if layout in _LAYOUTS else "top"}
    sections = [f"User request: {raw_prompt}"]
    facts = [raw_prompt]
    if business:
        details = [
            (label, key, str(business.get(key)).strip())
            for label, key in (
                ("Business name", "businessname"),
                ("Business category", "businesscategory"),
                ("Phone", "mobilenumber"),
                ("Brand colour", "brand_colour"),
            )
            if business.get(key) and str(business.get(key)).strip()
            and (key != "mobilenumber" or _ASKS_FOR_PHONE.search(raw_prompt))
        ]
        facts.extend(value for _label, key, value in details if key != "brand_colour")
        if details:
            sections.append("Business details from the provider's profile:\n" + "\n".join(f"{label}: {value}" for label, _key, value in details))
    if previous and previous.get("enhanced_prompt"):
        prior = {"scene": previous.get("enhanced_prompt"), **(previous.get("design") or {})}
        sections.append("Previous design the user wants changed:\n" + json.dumps(prior, ensure_ascii=False))
        facts.extend(str((previous.get("design") or {}).get(key) or "") for key in ("headline", "subline", "footer"))
    if layout in _LAYOUTS:
        sections.append(f"Layout: {layout}")

    try:
        response = await gpt4o_mini_creative.bind(response_format={"type": "json_object"}).ainvoke(
            [
                {"role": "system", "content": _DESIGN_PROMPTS.get(image_type, GENERAL_IMAGE_DESIGN_PROMPT)},
                {"role": "user", "content": "\n\n".join(sections)},
            ]
        )
        content = getattr(response, "content", "") or ""
        if isinstance(content, list):
            content = " ".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        parsed = json.loads(content[content.find("{"): content.rfind("}") + 1])
    except Exception as exc:
        logger.warning("IMAGE_DESIGN_FAILED | error=%s | falling back to raw prompt without text", exc)
        return fallback

    scene = str(parsed.get("scene") or "").strip()
    if not scene:
        logger.warning("IMAGE_DESIGN_EMPTY_SCENE | falling back to raw prompt without text")
        return fallback
    design = {"scene": scene}
    source = re.sub(r"(?<=\d),(?=\d)", "", " ".join(facts))
    known_digits = re.sub(r"\D", "", source)
    known_numbers = {number.lstrip("0") for number in re.findall(r"\d+", source)}
    known_months = {match.group(1).lower() for match in re.finditer(_MONTH, source, re.IGNORECASE)}
    known_words = {word.lower() for word in _WORD.findall(source)}
    dropped: List[str] = []
    for key, limit in _TEXT_LIMITS.items():
        kept: List[str] = []
        for part in (segment.strip() for segment in " ".join(str(parsed.get(key) or "").split()).split("·")):
            if not part:
                continue
            normalised = re.sub(r"(?<=\d),(?=\d)", "", part)
            phones = [number for number in _PHONE_NUMBER.findall(normalised) if len(re.sub(r"\D", "", number)) >= 8]
            rest = normalised
            for number in phones:
                rest = rest.replace(number, " ")
            unknown = [
                word for word in (token.lower() for token in _WORD.findall(rest))
                if len(word) > 2 and word not in _GENERIC_WORDS and word not in known_words
                and not any(
                    min(len(word), len(known)) >= 4
                    and len(os.path.commonprefix([word, known])) >= min(5, len(word), len(known))
                    for known in known_words | _GENERIC_WORDS
                )
            ]
            if _PLACEHOLDER.search(part):
                reason = "placeholder"
            elif any(re.sub(r"\D", "", number)[-10:] not in known_digits for number in phones):
                reason = "phone"
            elif any(value.lstrip("0") not in known_numbers for value in re.findall(r"\d+", rest)):
                reason = "number"
            elif any(
                month.lower() not in known_months
                for date in _DATE.finditer(rest)
                for month in re.findall(_MONTH, date.group(0), re.IGNORECASE)
            ):
                reason = "date"
            elif unknown:
                reason = "words " + ",".join(unknown)
            else:
                kept.append(part)
                continue
            dropped.append(f"{key}='{part}' ({reason})")
        text = ""
        for part in kept:
            candidate = f"{text} · {part}" if text else part
            if len(candidate) <= limit:
                text = candidate
            elif not text:
                text = part[:limit].rsplit(" ", 1)[0]
        design[key] = text
    if dropped:
        logger.info("IMAGE_TEXT_DROPPED | type=%s | %s", image_type, " | ".join(dropped))
    if image_type == "general":
        design.update(headline=design["headline"] if _ASKS_FOR_TEXT.search(raw_prompt) else "", subline="", footer="")
    if image_type == "logo" and design["headline"] == design["headline"].lower():
        design["headline"] = " ".join(word[:1].upper() + word[1:] for word in design["headline"].split())
    if image_type in ("offer", "feed") and design["footer"]:
        segments = design["footer"].split(" · ")
        if segments[0] == segments[0].lower():
            segments[0] = " ".join(word[:1].upper() + word[1:] for word in segments[0].split())
        name = str((business or {}).get("businessname") or "").strip()
        if name and name == name.lower():
            proper = " ".join(word[:1].upper() + word[1:] for word in name.split())
            segments = [re.sub(re.escape(name), proper, segment, flags=re.IGNORECASE) for segment in segments]
        design["footer"] = " · ".join(segments)
    accent = str(parsed.get("accent") or "").strip()
    design["accent"] = accent if _HEX_COLOUR.match(accent) else ""
    design["font"] = "serif" if str(parsed.get("font")).lower() == "serif" else "sans"
    planned_layout = str(parsed.get("layout") or "").lower()
    design["layout"] = layout if layout in _LAYOUTS else planned_layout if planned_layout in _LAYOUTS else "top"
    logger.info(
        "IMAGE_DESIGN_PLANNED | type=%s | headline='%s' | subline='%s' | footer='%s' | scene='%s'",
        image_type, design["headline"], design["subline"], design["footer"], scene[:200],
    )
    return design


def _layout_text(text: str, weight: str, max_width: float, size: int, max_lines: int) -> Dict[str, Any]:
    """Split text into per-script font runs and wrap it into at most max_lines lines within max_width, shrinking as needed.

    Args:
        text: Text to lay out.
        weight: heavy, bold, semibold, medium or serif.
        max_width: Widest a line may be, in pixels.
        size: Starting font size in pixels.
        max_lines: Most lines allowed.

    Returns:
        Dict with lines (each a list of (run_text, font) pairs with a width), line_height and ascent; no line is ever dropped.
    """
    from PIL import ImageFont

    def font_for(char: str, px: int):
        code = ord(char)
        script = next((fonts for start, end, *fonts in _SCRIPT_FONTS if start <= code <= end), None)
        if script:
            name = script[0 if weight in ("heavy", "bold", "serif") else 1]
        elif weight == "serif" and (char.isascii() or char in "’‘“”–—·…&"):
            name = _LATIN_FONTS["serif"]
        else:
            name = _LATIN_FONTS["bold" if weight == "serif" else weight]
        key = (name, px)
        if key not in _FONT_CACHE:
            _FONT_CACHE[key] = ImageFont.truetype(str(_FONTS_DIR / name), px)
        return _FONT_CACHE[key]

    def runs_for(line: str, px: int):
        runs: List[List[Any]] = []
        for char in line:
            font = runs[-1][1] if runs and (char == " " or char.isdigit()) else font_for(char, px)
            if runs and runs[-1][1] is font:
                runs[-1][0] += char
            else:
                runs.append([char, font])
        return [(run, font) for run, font in runs], sum(font.getlength(run) for run, font in runs)

    words = text.split()
    px = size
    min_px = max(10, int(size * 0.5))
    while True:
        lines: List[str] = []
        for word in words:
            candidate = f"{lines[-1]} {word}" if lines else word
            if lines and runs_for(candidate, px)[1] <= max_width:
                lines[-1] = candidate
            else:
                lines.append(word)
        laid = [runs_for(line, px) for line in lines]
        if (len(laid) <= max_lines and all(width <= max_width for _, width in laid)) or px <= min_px:
            break
        px = max(min_px, int(px * 0.92))
    ascent = max((font.getmetrics()[0] for runs, _ in laid for _, font in runs), default=px)
    return {"lines": laid, "line_height": int(px * 1.22), "ascent": ascent, "size": px}


def _draw_layout(draw: Any, layout: Dict[str, Any], x: float, top: float, fill: Any, align: str = "center") -> float:
    """Draw a laid-out text block with its first line's top at top and return the block's bottom edge.

    Args:
        draw: ImageDraw.Draw to paint on.
        layout: Result of _layout_text.
        x: Centre x for align='center', left x for align='left'.
        top: Top edge of the block in pixels.
        fill: Text colour.
        align: center or left.

    Returns:
        The y coordinate just below the last line.
    """
    y = top
    for runs, width in layout["lines"]:
        cursor = x - width / 2 if align == "center" else x
        baseline = y + layout["ascent"]
        for run, font in runs:
            draw.text((cursor, baseline), run, font=font, fill=fill, anchor="ls")
            cursor += font.getlength(run)
        y += layout["line_height"]
    return y


def _typeset_design(png_bytes: bytes, image_type: str, design: Dict[str, str], brand_logo: Optional[bytes] = None) -> bytes:
    """Typeset a design's text onto the generated image in its layout; logos are recomposed on pure white.

    Args:
        png_bytes: PNG image bytes from the model.
        image_type: logo recomposes emblem plus name; others get a blurred, darkened text area, a headline and a footer badge.
        design: Output of plan_image_design; layout is top, bottom, center or side.
        brand_logo: Optional provider profile image drawn as a round avatar in the footer badge.

    Returns:
        PNG bytes of the typeset image, the same size as the input.
    """
    from io import BytesIO

    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

    image = Image.open(BytesIO(png_bytes)).convert("RGB")
    width, height = image.size
    unit = min(width, height)
    headline = design.get("headline") or ""
    subline = design.get("subline") or ""
    footer = design.get("footer") or ""
    accent = design.get("accent") or ""
    rgb_accent = tuple(int(accent[i:i + 2], 16) for i in (1, 3, 5)) if accent else None

    def luma(colour):
        return 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]

    if image_type == "logo":
        red, green, blue = ImageChops.invert(image).split()
        darkness = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        alpha = darkness.point(lambda v: 0 if v <= 18 else 255 if v >= 48 else (v - 18) * 255 // 30)
        white = Image.new("RGB", image.size, (255, 255, 255))
        clean = Image.composite(image, white, alpha)
        if not headline:
            out = clean
        else:
            bbox = alpha.point(lambda v: 255 if v > 128 else 0).filter(ImageFilter.MedianFilter(5)).getbbox()
            if bbox:
                pad = int(min(width, height) * 0.02)
                bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(width, bbox[2] + pad), min(height, bbox[3] + pad))
                emblem = clean.crop(bbox)
                emblem_alpha = alpha.crop(bbox)
            else:
                emblem, emblem_alpha = None, None
            name_colour = tuple(int(c * min(1.0, 105 / max(1.0, luma(rgb_accent)))) for c in rgb_accent) if rgb_accent else (31, 41, 55)
            name = _layout_text(headline, "serif" if design.get("font") == "serif" else "bold", width * 0.84, int(unit * 0.115), 2)
            tagline = _layout_text(subline, "medium", width * 0.8, int(unit * 0.042), 1) if subline else None
            name_height = name["line_height"] * len(name["lines"])
            tag_height = tagline["line_height"] if tagline else 0
            emblem_box = (width * 0.62, height * (0.5 if tagline else 0.54))
            if emblem is not None:
                scale = min(emblem_box[0] / emblem.width, emblem_box[1] / emblem.height, 1.6)
                emblem_size = (max(1, int(emblem.width * scale)), max(1, int(emblem.height * scale)))
                emblem = emblem.resize(emblem_size, Image.LANCZOS)
                emblem_alpha = emblem_alpha.resize(emblem_size, Image.LANCZOS)
            emblem_height = emblem.height if emblem is not None else 0
            gap = int(height * 0.05) if emblem is not None else 0
            total = emblem_height + gap + name_height + (int(height * 0.015) + tag_height if tagline else 0)
            top = max(int(height * 0.04), int((height - total) / 2 - height * 0.03))
            out = Image.new("RGB", image.size, (255, 255, 255))
            if emblem is not None:
                out.paste(emblem, (int((width - emblem.width) / 2), top), emblem_alpha)
            draw = ImageDraw.Draw(out)
            bottom = _draw_layout(draw, name, width / 2, top + emblem_height + gap, name_colour)
            if tagline:
                _draw_layout(draw, tagline, width / 2, bottom + int(height * 0.015), (75, 85, 99))
    else:
        out = image.convert("RGBA")
        layout = design.get("layout") if design.get("layout") in _LAYOUTS else "top"
        if layout == "side" and width < height:
            layout = "top"
        if headline or subline:
            side = layout == "side"
            column = width * (0.44 if side else 0.86)
            head = _layout_text(headline, "serif" if design.get("font") == "serif" else "heavy", column, int(unit * (0.1 if side else 0.12)), 3 if side else 2) if headline else None
            sub = _layout_text(subline, "semibold", column, int(unit * 0.048), 3 if side else 2) if subline else None
            gap = int(unit * 0.012)
            block = (head["line_height"] * len(head["lines"]) if head else 0) + ((gap if head else 0) + sub["line_height"] * len(sub["lines"]) if sub else 0)
            if layout == "top":
                top = int(height * 0.055)
                region = (0, 0, width, min(height, int(top + block + height * 0.12)))
            elif layout == "bottom":
                top = int(height * 0.83) - block
                region = (0, max(0, int(top - height * 0.12)), width, height)
            elif layout == "center":
                top = int((height - block) / 2)
                region = (0, max(0, int(top - height * 0.07)), width, min(height, int(top + block + height * 0.07)))
            else:
                top = int((height - block) / 2)
                region = (0, 0, int(width * 0.56), height)
            size = (region[2] - region[0], region[3] - region[1])
            if layout == "center":
                fade = Image.new("L", size, 255)
                band = Image.new("L", out.size, 0)
                band.paste(fade, region[:2])
                mask = band.filter(ImageFilter.GaussianBlur(max(4, height // 30)))
            else:
                ramp = Image.linear_gradient("L")
                if layout == "side":
                    ramp = ramp.transpose(Image.Transpose.TRANSPOSE)
                if layout in ("top", "side"):
                    ramp = ImageOps.invert(ramp)
                mask = Image.new("L", out.size, 0)
                mask.paste(ramp.resize(size), region[:2])
            blurred = out.filter(ImageFilter.GaussianBlur(max(4, width // 70)))
            out = Image.composite(blurred, out, mask.point(lambda v: min(255, v * 2)))
            black = Image.new("RGBA", out.size, (0, 0, 0, 255))
            black.putalpha(mask.point(lambda v: int(v * (0.6 if layout == "center" else 0.74))))
            out.alpha_composite(black)
            text_layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(text_layer)
            x, align = (width * 0.06, "left") if side else (width / 2, "center")
            bottom = top
            if head:
                bottom = _draw_layout(draw, head, x, bottom, (255, 255, 255, 255), align)
            if sub:
                bottom = _draw_layout(draw, sub, x, bottom + (gap if head else 0), (243, 244, 246, 255), align)
            shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
            shadow.putalpha(text_layer.getchannel("A").filter(ImageFilter.GaussianBlur(max(2, width // 220))).point(lambda v: int(v * 0.6)))
            out.alpha_composite(shadow, (0, max(1, height // 400)))
            out.alpha_composite(text_layer)
        if footer or brand_logo:
            badge = rgb_accent or (185, 28, 28)
            footer_layout = _layout_text(footer, "semibold", width * 0.66, int(unit * 0.036), 2) if footer else None
            pad_x, pad_y = int(width * 0.025), int(unit * 0.014)
            text_width = int(max(line_width for _, line_width in footer_layout["lines"])) if footer_layout else 0
            text_height = footer_layout["line_height"] * len(footer_layout["lines"]) if footer_layout else int(unit * 0.06)
            ring = max(2, unit // 256)
            avatar = text_height + 2 * pad_y - 2 * ring if brand_logo else 0
            text_left = (ring + avatar + pad_x // 2 if brand_logo else pad_x)
            box_width = text_left + (text_width + pad_x if footer_layout else ring)
            left = int(width * 0.045)
            box_top = int(height * 0.045) if layout == "bottom" else int(height * 0.955) - text_height - 2 * pad_y
            box = (left, box_top, left + box_width, box_top + text_height + 2 * pad_y)
            badge_layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
            badge_draw = ImageDraw.Draw(badge_layer)
            badge_draw.rounded_rectangle(box, radius=min(int(height * 0.03), (box[3] - box[1]) // 2), fill=(*badge, 235))
            if brand_logo:
                logo = Image.open(BytesIO(brand_logo)).convert("RGBA")
                flat = Image.new("RGBA", logo.size, (255, 255, 255, 255))
                flat.alpha_composite(logo)
                disc = ImageOps.fit(flat.convert("RGB"), (avatar, avatar), Image.LANCZOS)
                disc_mask = Image.new("L", (avatar * 4, avatar * 4), 0)
                ImageDraw.Draw(disc_mask).ellipse((0, 0, avatar * 4 - 1, avatar * 4 - 1), fill=255)
                badge_layer.paste(disc, (box[0] + ring, box[1] + ring), disc_mask.resize((avatar, avatar), Image.LANCZOS))
            if footer_layout:
                text_colour = (255, 255, 255, 255) if luma(badge) < 150 else (17, 24, 39, 255)
                _draw_layout(badge_draw, footer_layout, box[0] + text_left, box[1] + pad_y, text_colour, align="left")
            out.alpha_composite(badge_layer)
        out = out.convert("RGB")

    buffer = BytesIO()
    out.save(buffer, "PNG")
    return buffer.getvalue()


async def _has_stray_text(png_bytes: bytes) -> bool:
    """Ask gpt-4o-mini vision whether the untypeset picture shows lettering; any failure counts as clean.

    Args:
        png_bytes: PNG image bytes from the model, before typesetting.

    Returns:
        True when the picture shows letters, words or letter-like marks.
    """
    try:
        from io import BytesIO

        from PIL import Image

        preview = Image.open(BytesIO(png_bytes)).convert("RGB")
        preview.thumbnail((640, 640))
        buffer = BytesIO()
        preview.save(buffer, "JPEG", quality=85)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        response = await gpt4o_mini.bind(response_format={"type": "json_object"}).ainvoke(
            [
                {"role": "system", "content": IMAGE_TEXT_CHECK_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "Does this picture show any lettering?"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ]
        )
        content = getattr(response, "content", "") or ""
        if isinstance(content, list):
            content = " ".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        return bool(json.loads(content[content.find("{"): content.rfind("}") + 1]).get("has_text"))
    except Exception as exc:
        logger.warning("IMAGE_TEXT_CHECK_FAILED | error=%s | treating the image as clean", exc)
        return False


def _apply_watermark(png_bytes: bytes, stamp: bool = True, top_center: bool = False) -> bytes:
    """Stamp the Etown mark bottom-right or top-centre when asked and IMAGE_WATERMARK_ENABLED, and always tag the PNG as AI-generated.

    Args:
        png_bytes: PNG image bytes from the model.
        stamp: False skips the visible mark while keeping the AI-generated metadata.
        top_center: True places the mark top-centre instead of bottom-right.

    Returns:
        PNG bytes, stamped when enabled, always carrying AI-generated text and XMP metadata.
    """
    from io import BytesIO

    from PIL import Image, ImageFilter, ImageStat
    from PIL.PngImagePlugin import PngInfo

    image = Image.open(BytesIO(png_bytes)).convert("RGBA")

    if stamp and settings.IMAGE_WATERMARK_ENABLED:
        short_side = min(image.size)
        mark = Image.open(_WATERMARK_PATH).convert("RGBA")
        mark_width = max(64, int(short_side * _WATERMARK_WIDTH_RATIO))
        mark = mark.resize((mark_width, max(1, round(mark.height * mark_width / mark.width))), Image.LANCZOS)
        margin = max(8, int(short_side * _WATERMARK_MARGIN_RATIO))
        if top_center:
            x, y = (image.width - mark.width) // 2, max(margin, int(short_side * _WATERMARK_TOP_OFFSET_RATIO))
        else:
            x, y = image.width - mark.width - margin, image.height - mark.height - margin

        corner = image.crop((x, y, x + mark.width, y + mark.height)).convert("L")
        dark_corner = ImageStat.Stat(corner).mean[0] < _WATERMARK_DARK_LUMA
        alpha = mark.getchannel("A")
        if dark_corner:
            neutral = mark.convert("RGB").convert("HSV").getchannel("S").point(lambda s: 255 if s < 90 else 0)
            mark = Image.composite(Image.new("RGB", mark.size, (255, 255, 255)), mark.convert("RGB"), neutral)
            mark.putalpha(alpha)

        shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(max(1, mark_width / 60)))
        shadow = Image.new("RGBA", mark.size, (0, 0, 0, 0) if dark_corner else (255, 255, 255, 0))
        shadow.putalpha(shadow_alpha.point(lambda v: int(v * _WATERMARK_SHADOW_OPACITY)))
        mark.putalpha(alpha.point(lambda v: int(v * _WATERMARK_OPACITY)))

        image.alpha_composite(shadow, (x, y))
        image.alpha_composite(mark, (x, y))

    info = PngInfo()
    info.add_text("Software", _AI_SOFTWARE)
    info.add_text("Description", _AI_DESCRIPTION)
    info.add_text("ai_generated", "true")
    info.add_itxt("XML:com.adobe.xmp", _AI_XMP)
    buffer = BytesIO()
    image.convert("RGB").save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def _encode_for_web(png_bytes: bytes, store_format: str, quality: int, max_side: Optional[int]):
    """Re-encode PNG bytes as png, jpeg or webp, optionally downscaled, returning (bytes, extension, content_type).

    Args:
        png_bytes: PNG image bytes from the model.
        store_format: png passes bytes through; jpeg, jpg or webp re-encode.
        quality: Encoder quality for jpeg and webp.
        max_side: Optional cap on the longest side, snapped to a multiple of 16.

    Returns:
        Tuple of encoded bytes, file extension and content type.

    Raises:
        ValueError: If store_format is not png, jpeg, jpg or webp.
    """
    fmt = (store_format or "png").lower()
    if fmt == "png":
        return png_bytes, "png", "image/png"

    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(png_bytes))
    image.load()
    if max_side and max(image.size) > max_side:
        scale = max_side / max(image.size)
        target = tuple(max(16, int(round(side * scale / 16)) * 16) for side in image.size)
        image = image.resize(target, Image.LANCZOS)

    exif = Image.Exif()
    exif[0x010E] = _AI_DESCRIPTION
    exif[0x0131] = _AI_SOFTWARE
    ai_tags = {"exif": exif.tobytes(), "xmp": _AI_XMP.encode("utf-8")}

    buffer = BytesIO()
    if fmt in ("jpeg", "jpg"):
        image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True, progressive=True, **ai_tags)
        return buffer.getvalue(), "jpg", "image/jpeg"
    if fmt == "webp":
        image.convert("RGB").save(buffer, "WEBP", quality=quality, method=6, **ai_tags)
        return buffer.getvalue(), "webp", "image/webp"
    raise ValueError(f"unsupported store_format {store_format!r}; use png, jpeg or webp")


async def _store_png(
    png_bytes: bytes,
    store_format: str = "png",
    quality: int = 85,
    max_side: Optional[int] = None,
    prefix: str = "ai_generated",
) -> Tuple[Optional[str], Optional[str]]:
    """Encode an image and upload it to Azure Blob under prefix, returning (full_url, blob_path) or (None, None) on failure."""
    container = normalize_container_name(settings.AZURE_BLOB_CONTAINER)
    if not container:
        logger.warning("[generate_image] AZURE_BLOB_CONTAINER not configured — using inline image fallback")
        return None, None

    try:
        try:
            body, ext, content_type = _encode_for_web(png_bytes, store_format, quality, max_side)
        except Exception as exc:
            logger.warning(
                "[generate_image] %s re-encode failed (%s); storing the original PNG", store_format, exc,
            )
            body, ext, content_type = png_bytes, "png", "image/png"
        if len(body) != len(png_bytes):
            logger.info(
                "IMAGE_COMPRESSED | %s q%d | %dKB -> %dKB (%.1fx)",
                ext, quality, len(png_bytes) // 1024, len(body) // 1024,
                len(png_bytes) / max(1, len(body)),
            )

        blob_name = f"{prefix}/{current_timestamp_prefix()}_{uuid.uuid4().hex[:8]}.{ext}"
        blob_path = await upload_blob_bytes(container, blob_name, body, content_type)
        if not blob_path:
            return None, None
        base_url = get_blob_base_url()
        full_url = f"{base_url}{blob_path}" if base_url else blob_path
        return full_url, blob_path
    except Exception as exc:
        logger.error("[generate_image] Blob upload failed: %s", exc, exc_info=True)
        return None, None


async def handle_generate_image(
    prompt: str,
    image_type: str = "general",
    width: int = 1024,
    height: int = 1024,
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    enhance_system_prompt: Optional[str] = None,
    store_format: str = "png",
    store_quality: int = 85,
    store_max_side: Optional[int] = None,
    user_profile_id: Optional[str] = None,
    previous: Optional[Dict[str, Any]] = None,
    watermark: bool = True,
    store: bool = True,
    watermark_top_center: bool = False,
    enhance: bool = True,
    store_prefix: str = "ai_generated",
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Plan a design, generate the scene via the model server, typeset its text, store it in Blob, and build the tool response.

    Args:
        prompt: The user's raw image description.
        image_type: logo, offer, feed, general or town; town and an explicit enhance_system_prompt skip design planning.
        width: Output width, clamped to 256-1280 and snapped to a multiple of 16.
        height: Output height, clamped the same way as width.
        steps: Optional diffusion steps clamped to 1-50; None uses the server default.
        seed: Optional integer seed; None reuses the previous image's seed or picks a random one.
        enhance_system_prompt: Optional enhancement system prompt overriding the image_type mapping.
        store_format: Stored encoding: png, jpeg or webp.
        store_quality: Encoder quality for jpeg and webp.
        store_max_side: Optional cap on the longest stored side.
        user_profile_id: Provider userprofileid whose business details inform the design; None skips the lookup.
        previous: Previous generated image (enhanced_prompt, design, seed) to refine; None starts fresh.
        watermark: False stores the image without the visible Etown mark; AI-generated metadata is always embedded.
        store: False skips Azure Blob and returns the image inline as a base64 data URL in store_format.
        watermark_top_center: True places the mark top-centre as on town images; town images always use top-centre.
        enhance: False sends the prompt to the image model as given, skipping gpt-4o-mini planning, enhancement and the stray-text check.
        store_prefix: Blob folder the stored image is uploaded under.

    Returns:
        Tuple of user-facing message and implicit_output dict, which is None on failure.
    """
    raw_prompt = (prompt or "").strip()
    if not raw_prompt:
        return "Please describe the image you'd like me to generate.", None

    width = _clamp_side(width)
    height = _clamp_side(height)
    if seed is None:
        previous_seed = (previous or {}).get("seed")
        seed = int(previous_seed) if isinstance(previous_seed, (int, str)) and str(previous_seed).isdigit() else random.randint(0, 2**31 - 1)

    design: Optional[Dict[str, str]] = None
    brand_logo: Optional[bytes] = None
    if not enhance:
        enhanced_prompt = raw_prompt
    elif image_type in _DESIGN_PROMPTS and not (enhance_system_prompt or "").strip():
        business = None
        if user_profile_id:
            try:
                row = await Database.fetchrow(_BUSINESS_QUERY, user_profile_id)
                business = dict(row) if row else None
            except Exception as exc:
                logger.warning("IMAGE_BUSINESS_CONTEXT_FAILED | user_profile_id=%s | error=%s", user_profile_id, exc)
        logo_path = str((business or {}).get("serviceimageurl") or "").strip()
        if logo_path and image_type in ("offer", "feed"):
            try:
                import httpx
                from io import BytesIO

                from PIL import Image

                logo_url = logo_path if logo_path.startswith("http") else get_blob_base_url() + "/" + logo_path.lstrip("/")
                async with httpx.AsyncClient(timeout=8) as client:
                    logo_response = await client.get(logo_url)
                logo_response.raise_for_status()
                logo = Image.open(BytesIO(logo_response.content)).convert("RGB")
                vivid = [
                    (count, colour)
                    for count, colour in logo.resize((48, 48)).quantize(8).convert("RGB").getcolors(48 * 48)
                    if 40 < 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2] < 220 and max(colour) - min(colour) > 60
                ]
                if vivid:
                    business["brand_colour"] = "#%02X%02X%02X" % max(vivid)[1]
                brand_logo = logo_response.content
            except Exception as exc:
                logger.warning("IMAGE_BRAND_LOGO_FAILED | path='%s' | error=%s", logo_path, exc)
        previous_layout = ((previous or {}).get("design") or {}).get("layout")
        if previous_layout in _LAYOUTS:
            layout = previous_layout
        elif image_type in ("offer", "feed"):
            layout = random.Random(seed).choice(("top", "bottom", "center", "side") if width >= height else ("top", "bottom", "center"))
        else:
            layout = random.Random(seed).choice(("top", "bottom"))
        design = await plan_image_design(raw_prompt, image_type, business, previous, None if image_type == "logo" else layout)
        if (business or {}).get("brand_colour") and image_type in ("offer", "feed") and not previous:
            design["accent"] = business["brand_colour"]
        enhanced_prompt = design["scene"]
    else:
        enhanced_prompt = await enhance_image_prompt(raw_prompt, image_type, enhance_system_prompt)

    params: Dict[str, Any] = {
        "prompt": enhanced_prompt,
        "width": width,
        "height": height,
        "response_format": "png",
        "seed": seed,
    }
    if steps is not None:
        params["num_inference_steps"] = max(1, min(50, int(steps)))

    api_key = settings.madlad_api_key or ""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    png_bytes = b""
    first_seed = seed
    text_check: Optional[Dict[str, Any]] = None
    for attempt in range(2):
        params["seed"] = seed
        failure = None
        try:
            import httpx

            logger.info(
                "IMAGE_GENERATE_ATTEMPT | url='%s' | steps=%s | size=%dx%d | seed=%d | attempt=%d | key_loaded=%s",
                settings.image_generate_url, params.get("num_inference_steps", "server-default"),
                width, height, seed, attempt + 1, bool(api_key),
            )
            async with httpx.AsyncClient(timeout=settings.image_generate_timeout) as client:
                response = await client.get(
                    settings.image_generate_url,
                    params=params,
                    headers=headers,
                )

            if response.status_code != 200:
                logger.warning(
                    "IMAGE_GENERATE_ERROR | status=%d | body='%s'",
                    response.status_code, response.text[:300],
                )
                failure = "Sorry, I couldn't generate the image right now. Please try again in a moment."
            elif not response.content:
                logger.warning("IMAGE_GENERATE_EMPTY | model returned no image bytes")
                failure = "Sorry, the image generation returned an empty result. Please try again."
        except Exception as exc:
            logger.error("IMAGE_GENERATE_FAILED | error=%s", exc, exc_info=True)
            failure = "Sorry, the image service is temporarily unavailable. Please try again shortly."

        if failure:
            if not attempt:
                return failure, None
            logger.warning("IMAGE_TEXT_RETRY_FAILED | keeping the first image with seed=%d", first_seed)
            seed = first_seed
            break
        png_bytes = response.content
        if design is None:
            break
        stray = await _has_stray_text(png_bytes)
        text_check = {"stray_text": stray, "attempts": attempt + 1}
        if not stray or attempt:
            break
        logger.warning("IMAGE_STRAY_TEXT | seed=%d | regenerating once with a new seed and plain surfaces", seed)
        first_seed = seed
        seed = random.randint(0, 2**31 - 1)
        params["prompt"] = enhanced_prompt + _PLAIN_SURFACES
    enhanced_prompt = params["prompt"]
    if text_check and text_check["stray_text"] and text_check["attempts"] == 2:
        logger.warning("IMAGE_STRAY_TEXT_PERSISTS | seed=%d | storing the second image", seed)

    text_on_image: List[str] = []
    if design and (image_type == "logo" or brand_logo or design["headline"] or design["subline"] or design["footer"]):
        try:
            png_bytes = await asyncio.to_thread(_typeset_design, png_bytes, image_type, design, brand_logo)
            text_on_image = [design[key] for key in ("headline", "subline", "footer") if design[key]]
        except Exception as exc:
            logger.error("IMAGE_TYPESET_FAILED | error=%s | storing the untypeset image", exc, exc_info=True)

    try:
        png_bytes = await asyncio.to_thread(_apply_watermark, png_bytes, watermark, watermark_top_center or image_type == "town")
    except Exception as exc:
        logger.error("IMAGE_WATERMARK_FAILED | error=%s | storing unwatermarked image", exc, exc_info=True)

    image_url, blob_path = (None, None)
    if store:
        image_url, blob_path = await _store_png(png_bytes, store_format, store_quality, store_max_side, store_prefix)
    if not image_url:
        try:
            body, _extension, content_type = await asyncio.to_thread(_encode_for_web, png_bytes, store_format, store_quality, store_max_side)
        except Exception as exc:
            logger.warning("IMAGE_INLINE_ENCODE_FAILED | format=%s | error=%s | returning the PNG", store_format, exc)
            body, content_type = png_bytes, "image/png"
        image_url = f"data:{content_type};base64," + base64.b64encode(body).decode("ascii")

    result_text = f"Here's the image I generated for you based on: \"{raw_prompt}\"."
    if design is not None:
        if text_on_image:
            quoted = ", ".join(f'"{line}"' for line in text_on_image)
            result_text += f" Text printed on the image, exactly: {quoted}. The picture itself is AI-drawn."
        else:
            result_text += " The image has no text on it. The picture itself is AI-drawn."

    implicit_output: Dict[str, Any] = {
        "status": "success",
        "image_url": image_url,
        "blob_path": blob_path,
        "image_type": image_type,
        "original_prompt": raw_prompt,
        "enhanced_prompt": enhanced_prompt,
        "design": {key: design[key] for key in ("headline", "subline", "footer", "accent", "font", "layout")} if design else None,
        "text_check": text_check,
        "brand_logo": bool(brand_logo),
        "watermarked": bool(watermark and settings.IMAGE_WATERMARK_ENABLED),
        "stored": bool(blob_path),
        "text_on_image": text_on_image,
        "width": width,
        "height": height,
        "steps": params.get("num_inference_steps"),
        "seed": seed,
        "store_format": store_format,
    }
    return result_text, implicit_output
