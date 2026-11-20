"""Offerings Utilities - image extraction, catalogue normalisation and the single offerings write path.

Domain layer for a provider's offering catalogue: extracts items from business images with Gemini Vision, folds any payload into one deduped offering-category-item tree in etown_offerings, and mirrors every write onto the serviceitems rows that search, insights and provider details read.

Features:
    - Image-based Extraction: Gemini Vision pulls business details and items, preserving original scripts
    - Compact-prompt Fallback: Truncated responses retry with VISION_PROMPT_COMPACT, then partial-JSON rescue
    - Canonical Tree Merge: normalize_offering_tree() folds any item payload into one deduped tree
    - Stable Node IDs: Every offering/category/item gets a UUID for targeted add/remove/update
    - AI Catalogue Description: gpt41 writes a bulleted item description, rejecting invented prices
    - Services-to-Offerings Conversion: Staging-table preview-then-confirm jobs build trees from service rows
    - Single Mirror Point: mirror_offering_tree_to_serviceitems() copies tree writes onto serviceitems

Classes:
    No classes defined (module exposes extraction, tree-merge, and persistence functions)

Functions:
    extract_json(): Safely parse JSON out of raw AI response text, tolerant of markdown fences.
    _rescue_partial_json(): Recover partial data from a truncated JSON response via bracket repair or regex.
    validate_structure(): Check AI output has the expected top-level keys.
    _is_response_truncated(): Heuristically detect a truncated AI response string.
    _extract_business_name_from_partial(): Regex-pull a business_name out of an unparsable raw response.
    _normalize_compact_response(): Map VISION_PROMPT_COMPACT JSON into the full structured_data shape.
    resize_image_if_needed(): Downscale an oversized image before Gemini submission.
    format_file_size(): Convert a byte count to a human-readable size string.
    clean_extracted_offerings(): Dedupe and quality-filter extracted items by name/confidence.
    extract_offerings_from_image(): Call Gemini Vision with retry logic; returns raw response text.
    normalize_structured_data(): Parse/validate an AI response into normalized structured data.
    extract_and_normalize(): Full extraction pipeline: resize, full prompt, compact fallback, rescue.
    _norm_field(): Normalise a raw LLM field into a {value, confidence} dict.
    build_provider_profile(): Shape structured data into the API's provider_profile response payload.
    price_key(): Normalise a price value into a comparable string key.
    build_items_listing(): Group flat items by category into prompt text, literal listing, and price set.
    generate_items_description(): Generate a bulleted AI description of stored items, rejecting invented prices.
    _merge_offering_tree(): (superseded by normalize_offering_tree) merge one offering tree into another in place.
    _flatten_tree_items(): Flatten an offering tree into flat item dicts for generate_items_description.
    assign_node_ids(): Assign a stable UUID to every offering/category/item node lacking one.
    apply_item_operations(): Apply add/remove/update operations to a flat items list.
    apply_category_operations(): Apply add/remove/update operations to a categories list (recurses into items).
    apply_offering_operations(): Apply add/remove/update operations to an offerings list (recurses into categories).
    _norm_key(): Case-insensitive, stripped grouping key for a name.
    _map_offering_item(): Map a raw item record (flat or nested field names) to the canonical item shape.
    normalize_offering_tree(): Fold any flat/nested/hybrid items payload into one canonical, deduped tree.
    mirror_offering_tree_to_serviceitems(): Write an offering tree onto the provider's serviceitems rows.
    save_parsed_offerings_to_db(): Upsert business_details + offering tree into etown_offerings and mirror it.
    _build_offerings_gen_prompt(): Build the LLM prompt that turns service rows into offering nodes.
    _extract_offerings_json_array(): Parse a JSON array from LLM text, tolerant of truncated brackets.
    _normalize_service_rows_with_llm(): Turn service rows into offering nodes via the LLM, with retry/validation.
    generate_and_store_offerings_from_provider(): Auto-generate and persist an offerings row for a Public Insert provider.
    build_service_conversion_preview(): Build a preview offerings tree from a provider's real service rows.
    _parse_preview(): Decode a staging-row preview jsonb column into a dict.
    create_conversion_jobs(): Insert one staging row per provider for a convert-services batch.
    fetch_conversion_batch(): Return every staging job for a batch, preview decoded.
    set_conversion_job_status(): Update a staging job's status/preview/reason.
    claim_conversion_job(): Atomically move a job from 'ready' to 'confirmed'.
    record_conversion_confirmed(): Record the item count persisted for a confirmed job.

Dependencies:
    - app.agent_infrastructure.infrastructure.llm_clients: gemini_2_5_flash_vision and gpt41
    - app.db.client: Async PostgreSQL client for offerings, service rows and conversion jobs
    - app.utils.performance: PerformanceTimer around the Gemini Vision call
    - app.core.logging: Centralised logging infrastructure
    - PIL (Pillow): Image resizing before Gemini submission

Updates:
    08-Jun-2026: Created; single-row JSONB storage; added generate_items_description(); lowered confidence thresholds.
    09-Jun-2026: Added VISION_PROMPT_COMPACT, extract_and_normalize() and resize_image_if_needed().
    15-Jun-2026: build_provider_profile() restructured with semantically-prefixed fields and flattened categories.
    17-Jun-2026: Schema v1.1 - offerings[], media_analysis, overall_confidence and enriched item fields.
    30-Jun-2026: Added save_parsed_offerings_to_db(), storing business_details and merging the offering tree.
    01-Jul-2026: Added stable UUID ids on every tree node plus the operation appliers.
    02-Jul-2026: generate_items_description() switched to gpt41; added the Public Insert offerings generator.
    16-Jul-2026: Added convert_services_to_offerings(), build_service_conversion_preview() and conversion-job accessors.
    29-Jul-2026: Added mirror_offering_tree_to_serviceitems(), which every offerings write now ends in.
    12-Aug-2026: The save_parsed_offerings_to_db() insert leg is now ON CONFLICT DO UPDATE.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
"""

import asyncio
import re
import base64
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image as PILImage

from app.agent_infrastructure.infrastructure.llm_clients import gemini_2_5_flash_vision, gpt41
from app.core.logging import logger
from app.db.client import Database
from app.utils.performance import PerformanceTimer

VISION_PROMPT = """Analyze this business image and extract ALL visible offerings. The image could be a restaurant menu, retail store, service board, price list, market stall, or any type of business.

MULTILINGUAL PROCESSING:
- Read and extract text in ANY language (English, Hindi, Tamil, Telugu, Bangla, Urdu, Arabic, Chinese, Japanese, Spanish, French, etc.)
- Preserve all names in their ORIGINAL language and script — do NOT translate names
- Write all descriptions, summaries, and AI-generated fields in ENGLISH

STEP-BY-STEP ANALYSIS:
1. Determine the PRIMARY ANALYSIS METHOD:
   - "ocr": image is primarily text-based (printed menu, price list, catalog with visible prices and item names)
   - "object_detection": image is primarily visual (shop front, shelf, market stall — prices not printed, items identified by sight)
   - "hybrid": both OCR text AND visual object detection used together
2. Extract ALL distinct text strings visible anywhere in the image into ocr_extracted_text — every brand name, label, sign, slogan, price, header. Even if the image is primarily visual, list every readable word/phrase you can see.
3. List ALL visually identifiable objects (products, packaging, produce, equipment, bottles, bags, etc.) into detected_objects — even if they also appear as offerings.
4. Identify the business name, category, and any contact info visible in the image.
5. MANDATORY: Group ALL items into offerings[] with categories[]. Every product/service you can see — whether identified by OCR text or by visual detection — MUST appear as an item inside offerings[]. detected_objects in media_analysis and items in offerings[] are NOT mutually exclusive: a "Pepsi Bottle" must appear in BOTH detected_objects AND as an item under offerings[].categories[].items[].

CRITICAL RULES:
- NEVER leave offerings[] empty if you can identify any products or services in the image. If you can see products on shelves, produce on a stall, items in a store — they MUST be listed in offerings[].
- suggestion_source per offering: "ocr" if items have visible printed prices/names in text, "object_detection" if prices not visible and items identified by sight only, "hybrid" if both apply
- item source field: "ocr" if price/name readable from printed text, "object_detection" if visually identified only, "hybrid" if both
- offering_type: ONLY "product" or "service"
- All confidence scores: actual float between 0.0 and 1.0 based on clarity
- business_description_extracted: copy the EXACT visible text of any slogan, tagline, or business description sign visible in the image (e.g. "PYAAS BUJHAYE YAAR"). If no slogan/tagline is visible, return null.
- business_name, business_description_extracted, business_description_ai_generated, business_category: each MUST be a {"value": ..., "confidence": 0.0–1.0} object. For business_description_ai_generated value: ALWAYS write 2-3 sentences describing the business as a named entity, starting with its name or type (e.g. "Biryani's is a restaurant specialising in..."). NEVER start value with 'This is a menu', 'This is a store', or 'This is a catalog'. Never leave value null or empty.
- overall_confidence: reflect how clearly you can identify the business and its offerings (0.0–1.0). For a clearly visible store with identifiable products, this should be at least 0.7.

Return ONLY this JSON (no markdown, no code fences):
{
  "business_details": {
    "business_name": {"value": "exact name from image in original script, or null if not visible", "confidence": 0.95},
    "business_description_extracted": {"value": "exact visible slogan/tagline text copied from image, or null if no slogan visible", "confidence": 0.90},
    "business_description_ai_generated": {"value": "2-3 sentence English description of the business as a named entity — start with the business name or type, describe what it specialises in and who it serves. NEVER start with 'This is a menu', 'This is a store', or 'This is a catalog'. ALWAYS populate this.", "confidence": 0.95},
    "business_category": {"value": "one of: Restaurant, Convenience Store, Retail Shop, Market Stall, Service Centre, Auto Parts, Vegetable Market, Pharmacy, Electronics, Clothing, Plumbing & Sanitary, Footwear, or best-fit category", "confidence": 0.95},
    "business_confidence": 0.85,
    "contact_info": {
      "phone_number": {"value": "phone if visible, or null", "confidence": 0.95},
      "email": {"value": "email if visible, or null", "confidence": 0.95},
      "website": {"value": "website if visible, or null", "confidence": 0.95},
      "address": {"value": "address if visible, or null", "confidence": 0.95}
    }
  },
  "media_analysis": {
    "primary_analysis_method": "ocr OR object_detection OR hybrid",
    "ocr_extracted_text": ["every readable text string, brand, label, sign, price visible in image"],
    "detected_objects": [
      {"object_name": "Pepsi Bottle", "object_type": "Product", "confidence_score": 0.96}
    ]
  },
  "offerings": [
    {
      "offering_name": "short group name in English (e.g. Beverages, Fresh Produce, Biryani, Spare Parts, Footwear)",
      "offering_description": "English description of this offering group",
      "offering_type": "product OR service",
      "suggestion_source": "ocr OR object_detection OR hybrid",
      "confidence_score": 0.85,
      "categories": [
        {
          "category_name": "category name in original language/script",
          "items": [
            {
              "item_name": "item name in original language/script",
              "item_price": null,
              "item_currency": "currency symbol as visible in image or null",
              "item_description_extracted": "exact text from image for this item, or null",
              "item_description_ai_generated": "brief English description of the item",
              "source": "ocr OR object_detection OR hybrid",
              "confidence_score": 0.85
            }
          ]
        }
      ]
    }
  ],
  "overall_confidence": 0.85
}

IMPORTANT: If more than 60 items are visible, extract the 60 most clearly identifiable ones. Always return valid JSON ending with }."""


VISION_PROMPT_COMPACT = """Extract all visible business offerings from this image.
Return ONLY a raw JSON object — no markdown, no code fences, no explanation.

If more than 50 items are visible, extract the 50 most clearly readable ones.

Required JSON structure:
{
  "business_name": "exact name from image, or null",
  "business_category": "best-fit category in English",
  "business_type": "product or service",
  "primary_analysis_method": "ocr OR object_detection OR hybrid",
  "language": "primary language of the text in the image",
  "offerings": [
    {
      "offering_name": "short group name in English",
      "offering_type": "product or service",
      "suggestion_source": "ocr OR object_detection OR hybrid",
      "categories": [
        {
          "category_name": "section or category name (original language)",
          "items": [
            {"item_name": "item name (original language/script)", "item_price": 99.0, "source": "ocr"}
          ]
        }
      ]
    }
  ]
}

Rules:
- item_price must be a number or null if not visible
- Preserve original language and script for all names
- offering_type must be exactly \"product\" or \"service\"
- suggestion_source / source: "ocr" if price visible, "object_detection" if visually identified only, "hybrid" if both
- Return ONLY the JSON object, starting with { and ending with }"""


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Return the first balanced JSON object in an AI response, stripping code fences, or None."""
    try:
        cleaned_text = text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text[3:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]

        cleaned_text = cleaned_text.strip()

        start_idx = cleaned_text.find("{")
        if start_idx != -1:
            brace_count = 0
            for i, char in enumerate(cleaned_text[start_idx:], start_idx):
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        json_text = cleaned_text[start_idx : i + 1]
                        return json.loads(json_text)

        if cleaned_text.startswith("{"):
            return json.loads(cleaned_text)
    except Exception:
        logger.debug("offerings: no JSON object could be extracted from the model output", exc_info=True)

    return None


def _rescue_partial_json(text: str) -> Optional[Dict[str, Any]]:
    """Recover data from truncated JSON by closing it structurally, else by regex field extraction, or None."""
    if not text:
        return None
    cleaned = text.strip()
    start = cleaned.find("{")
    if start == -1:
        return None
    fragment = cleaned[start:]

    depth = 0
    in_string = False
    escape_next = False
    last_depth1_pos = -1

    for i, ch in enumerate(fragment):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 1:
                last_depth1_pos = i

    if last_depth1_pos > 0:
        candidate = fragment[:last_depth1_pos + 1]
        open_brackets = candidate.count("[") - candidate.count("]")
        open_braces = candidate.count("{") - candidate.count("}")
        closing = "]" * max(0, open_brackets) + "}" * max(0, open_braces)
        try:
            result = json.loads(candidate + closing)
            if isinstance(result, dict) and ("business_details" in result or "offerings" in result):
                logger.info("Partial JSON rescue succeeded (recovered %d chars)", last_depth1_pos)
                return result
        except Exception:
            logger.debug("offerings: partial JSON rescue attempt failed")

    result: Dict[str, Any] = {
        "business_details": {},
        "media_analysis": {"primary_analysis_method": None, "ocr_extracted_text": [], "detected_objects": []},
        "offerings": [],
        "overall_confidence": 0.0,
    }
    rescued_anything = False

    m = re.search(r'"business_name"\s*:\s*(?:"([^"]*)"|(null))', text)
    if m:
        result["business_details"]["business_name"] = m.group(1) if m.group(1) else None
        rescued_anything = True

    for field in ("business_description_extracted", "business_description_ai_generated", "business_category"):
        m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', text)
        if m:
            result["business_details"][field] = m.group(1)
            rescued_anything = True

    m = re.search(r'"business_confidence"\s*:\s*([\d.]+)', text)
    if m:
        result["business_details"]["business_confidence"] = float(m.group(1))

    m = re.search(r'"primary_analysis_method"\s*:\s*"([^"]+)"', text)
    if m:
        result["media_analysis"]["primary_analysis_method"] = m.group(1)

    m = re.search(r'"ocr_extracted_text"\s*:\s*\[([^\]]+)\]', text, re.DOTALL)
    if m:
        try:
            result["media_analysis"]["ocr_extracted_text"] = json.loads("[" + m.group(1) + "]")
        except Exception:
            logger.debug("offerings: ocr_extracted_text fragment is not valid JSON; skipping it")

    m = re.search(r'"overall_confidence"\s*:\s*([\d.]+)', text)
    if m:
        result["overall_confidence"] = float(m.group(1))

    return result if rescued_anything else None


def validate_structure(data: Dict[str, Any]) -> bool:
    """Return True when extracted data is a dict with a dict business_details and a list offerings."""
    if not isinstance(data, dict):
        return False

    if "business_details" in data and not isinstance(data.get("business_details"), dict):
        return False

    offerings = data.get("offerings")
    if offerings is not None and not isinstance(offerings, list):
        return False

    return True


def _is_response_truncated(response: str) -> bool:
    """Return True if the AI response was likely cut off before valid JSON could be parsed."""
    if not response or response.startswith("AI processing failed"):
        return False
    s = response.strip()
    ends_cleanly = s.endswith("}") or s.endswith("```")
    if not ends_cleanly:
        return True
    return extract_json(response) is None


def _extract_business_name_from_partial(raw: str) -> str | None:
    """Return the business_name found in partial JSON text, or None when missing or a placeholder."""
    if not raw:
        return None
    match = re.search(r'"business_name"\s*:\s*"([^"]+)"', raw)
    if not match:
        return None
    name = match.group(1).strip()
    if not name or name.lower() in ("unknown business", "unknown", ""):
        return None
    return name


def _normalize_compact_response(ai_response: str) -> Dict[str, Any]:
    """Expand compact-prompt JSON into the full structured_data shape, defaulting item confidence to 0.8."""
    data = extract_json(ai_response)
    if not data or "offerings" not in data:
        return {"business_details": {}, "media_analysis": {}, "offerings": [], "overall_confidence": 0.0}

    primary_method = data.get("primary_analysis_method", "hybrid")
    return {
        "business_details": {
            "business_name": data.get("business_name") or None,
            "business_description_extracted": None,
            "business_description_ai_generated": None,
            "business_category": data.get("business_category"),
            "business_confidence": 0.7,
            "contact_info": {"phone_number": None, "email": None, "website": None, "address": None},
        },
        "media_analysis": {
            "primary_analysis_method": primary_method,
            "ocr_extracted_text": [],
            "detected_objects": [],
        },
        "offerings": [
            {
                "offering_name": o.get("offering_name") or data.get("business_category", "Offerings"),
                "offering_description": None,
                "offering_type": o.get("offering_type") or data.get("business_type", "product"),
                "suggestion_source": o.get("suggestion_source", primary_method),
                "confidence_score": 0.7,
                "categories": [
                    {
                        "category_name": cat.get("category_name") or cat.get("name", "General"),
                        "items": [
                            {
                                "item_name": item.get("item_name") or item.get("name", ""),
                                "item_price": item.get("item_price") or item.get("price"),
                                "item_currency": item.get("item_currency"),
                                "item_description_extracted": None,
                                "item_description_ai_generated": None,
                                "source": item.get("source", primary_method),
                                "confidence_score": item.get("confidence_score", 0.8),
                            }
                            for item in cat.get("items", [])
                        ],
                    }
                    for cat in o.get("categories", [])
                ],
            }
            for o in data.get("offerings", [])
        ],
        "overall_confidence": 0.7,
    }


_GEMINI_MAX_PX = 1024
_RESIZE_THRESHOLD_BYTES = 800 * 1024


def resize_image_if_needed(
    image_data: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    """Downscale an image over 800 KB and 1024 px to a 1024 px JPEG, returning bytes and MIME type."""
    if len(image_data) <= _RESIZE_THRESHOLD_BYTES:
        return image_data, content_type

    try:
        img = PILImage.open(io.BytesIO(image_data))
        w, h = img.size
        if max(w, h) <= _GEMINI_MAX_PX:
            return image_data, content_type

        if w >= h:
            new_w, new_h = _GEMINI_MAX_PX, int(h * _GEMINI_MAX_PX / w)
        else:
            new_w, new_h = int(w * _GEMINI_MAX_PX / h), _GEMINI_MAX_PX

        img = img.convert("RGB").resize((new_w, new_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        resized = buf.getvalue()
        logger.info(
            "Image resized %dx%d → %dx%d — %d KB → %d KB",
            w, h, new_w, new_h,
            len(image_data) // 1024,
            len(resized) // 1024,
        )
        return resized, "image/jpeg"
    except Exception as exc:
        logger.warning("Image resize failed (%s) — using original", exc)
        return image_data, content_type


def format_file_size(size_bytes: int) -> Dict[str, Any]:
    """Return the byte count and a human-readable size string."""
    size_mb = size_bytes / (1024 * 1024)
    if size_mb >= 1:
        formatted = f"{size_mb:.1f} MB"
    else:
        size_kb = size_bytes / 1024
        formatted = f"{size_kb:.1f} KB" if size_kb >= 1 else f"{size_bytes} bytes"

    return {
        "file_size_bytes": size_bytes,
        "file_size_formatted": formatted,
    }


def clean_extracted_offerings(structured_data: Dict[str, Any]) -> Dict[str, Any]:
    """Drop short, low-confidence and duplicate items, then empty categories and offerings."""
    if not structured_data or not structured_data.get("offerings"):
        return structured_data

    seen_names: set = set()
    cleaned_offerings = []

    for offering in structured_data.get("offerings", []):
        cleaned_categories = []
        for category in offering.get("categories", []):
            cleaned_items = []
            for item in category.get("items", []):
                name = item.get("item_name", "").strip()
                confidence = item.get("confidence_score", 0.0)

                if name and len(name) > 2 and confidence >= 0.4:
                    name_lower = name.lower().strip()
                    if name_lower not in seen_names:
                        seen_names.add(name_lower)
                        cleaned_items.append(item)

            if cleaned_items:
                category["items"] = cleaned_items
                cleaned_categories.append(category)

        if cleaned_categories:
            offering["categories"] = cleaned_categories
            cleaned_offerings.append(offering)

    structured_data["offerings"] = cleaned_offerings
    return structured_data


async def extract_offerings_from_image(
    image_content_type: str,
    image_base64: str,
    prompt: str | None = None,
) -> str:
    """Run Gemini Vision on an image with one retry and return the raw text or an "AI processing failed" message."""
    ai_response = None
    active_prompt = prompt or VISION_PROMPT

    for attempt in range(2):
        try:
            async with PerformanceTimer(
                operation_name=f"gemini_vision_extraction_attempt_{attempt + 1}",
                category="external_api",
                warn_threshold_ms=8000,
            ) as timer:
                response = await gemini_2_5_flash_vision.ainvoke(
                    [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": active_prompt,
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{image_content_type};base64,{image_base64}"
                                    },
                                },
                            ],
                        }
                    ]
                )

            ai_response = getattr(response, "content", None) or str(response)
            logger.info(
                "Gemini Vision processed image successfully on attempt %s in %.1fms",
                attempt + 1,
                timer.duration_ms,
            )

            if not ai_response or len(str(ai_response).strip()) < 10:
                raise Exception(f"Response too short: {len(str(ai_response))} chars")

            break

        except Exception as e:
            logger.warning("Attempt %s failed: %s", attempt + 1, str(e))
            if attempt == 1:
                ai_response = f"AI processing failed: {str(e)}"

    return ai_response or "AI processing failed: empty response"


def normalize_structured_data(ai_response: str) -> Dict[str, Any]:
    """Parse, validate and clean an AI response into structured data, falling back to an empty structure."""
    structured_data = None

    if isinstance(ai_response, str) and not ai_response.startswith("AI processing failed"):
        response_str = ai_response.strip()
        if response_str and not response_str.endswith("}"):
            logger.warning(
                "AI response appears truncated. Length: %s chars. Ends with: '%s'",
                len(response_str),
                response_str[-30:] if len(response_str) > 30 else response_str,
            )

        if '"' in response_str and response_str.count('"') % 2 != 0:
            logger.warning("AI response has unmatched quotes - likely truncated")

        structured_data = extract_json(ai_response)

        if structured_data:
            offerings = structured_data.get("offerings", [])
            total_items = sum(
                len(cat.get("items", []))
                for o in offerings
                for cat in o.get("categories", [])
            )
            logger.info(
                "Successfully extracted JSON: %s offerings, %s items",
                len(offerings),
                total_items,
            )
        else:
            logger.warning("Failed to extract valid JSON. Response preview: %s...", ai_response[:300])
    else:
        logger.info("No valid AI response - returning empty structure")

    if not structured_data or not validate_structure(structured_data):
        if structured_data:
            logger.warning("AI response has invalid structure - using fallback")
        structured_data = {
            "business_details": {},
            "media_analysis": {},
            "offerings": [],
            "overall_confidence": 0.0,
        }

    structured_data = clean_extracted_offerings(structured_data)
    total = sum(len(cat.get("items", [])) for o in structured_data.get("offerings", []) for cat in o.get("categories", []))
    logger.info("Final result: %s offerings, %s items", len(structured_data.get("offerings", [])), total)
    return structured_data


async def extract_and_normalize(
    image_content_type: str,
    image_base64: str,
) -> Dict[str, Any]:
    """Extract offerings from an image, retrying truncated output with the compact prompt, then partial-JSON rescue."""
    raw_bytes = base64.b64decode(image_base64)
    resized_bytes, effective_type = resize_image_if_needed(raw_bytes, image_content_type)
    if resized_bytes is not raw_bytes:
        image_base64 = base64.b64encode(resized_bytes).decode("utf-8")
        image_content_type = effective_type

    ai_response = await extract_offerings_from_image(image_content_type, image_base64)
    structured_data = normalize_structured_data(ai_response)

    if structured_data.get("offerings"):
        biz = structured_data.setdefault("business_details", {})
        if not biz.get("business_name"):
            rescued = _extract_business_name_from_partial(ai_response)
            if rescued:
                biz["business_name"] = rescued
                logger.info("Business name rescued from full-prompt response: %s", rescued)
        return structured_data

    if _is_response_truncated(ai_response):
        logger.info("Full prompt response truncated — retrying with compact prompt")
        compact_response = await extract_offerings_from_image(
            image_content_type, image_base64, prompt=VISION_PROMPT_COMPACT
        )
        compact_data = _normalize_compact_response(compact_response)
        compact_data = clean_extracted_offerings(compact_data)
        compact_offerings = compact_data.get("offerings", [])
        if compact_offerings:
            total = sum(len(cat.get("items", [])) for o in compact_offerings for cat in o.get("categories", []))
            biz = compact_data.setdefault("business_details", {})
            if not biz.get("business_name"):
                rescued = _extract_business_name_from_partial(ai_response)
                if rescued:
                    biz["business_name"] = rescued
                    logger.info("Business name rescued from truncated response: %s", rescued)
            logger.info(
                "Compact prompt recovered %d offerings, %d items",
                len(compact_offerings),
                total,
            )
            return compact_data
        logger.warning("Compact prompt also returned 0 offerings")

        longer_raw = ai_response if len(ai_response) >= len(compact_response) else compact_response
        rescued_data = _rescue_partial_json(longer_raw)
        if rescued_data:
            rescued_data.setdefault("offerings", [])
            rescued_data.setdefault("overall_confidence", 0.0)
            rescued_data = clean_extracted_offerings(rescued_data)
            logger.info(
                "Partial JSON rescue: %d offerings from %d chars",
                len(rescued_data.get("offerings", [])),
                len(longer_raw),
            )
            return rescued_data
        logger.warning("Partial JSON rescue also failed")

    return structured_data


def _norm_field(raw: Any) -> Dict[str, Any]:
    """Return a raw LLM field (dict, string or None) as a {value, confidence} dict."""
    if isinstance(raw, dict):
        return {"value": raw.get("value"), "confidence": raw.get("confidence")}
    if isinstance(raw, str) and raw.strip():
        return {"value": raw.strip(), "confidence": None}
    return {"value": None, "confidence": None}


def build_provider_profile(structured_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the schema v1.1 provider-profile response, splitting OCR offerings from AI suggestions."""
    business_details = structured_data.get("business_details", {})
    media_analysis = structured_data.get("media_analysis", {})
    offerings = structured_data.get("offerings", [])
    overall_confidence = structured_data.get("overall_confidence", 0.0)

    total_items = sum(
        len(cat.get("items", []))
        for o in offerings
        for cat in o.get("categories", [])
    )

    extraction_failed = (
        total_items == 0
        and overall_confidence < 0.15
    )

    _empty_field = {"value": None, "confidence": None}
    default_contact = {
        "phone_number": _empty_field,
        "email":        _empty_field,
        "website":      _empty_field,
        "address":      _empty_field,
    }

    if extraction_failed:
        return {
            "business_details": {
                "business_name": _empty_field,
                "business_description_extracted": _empty_field,
                "business_description_ai_generated": _empty_field,
                "business_category": _empty_field,
                "business_confidence": 0.0,
                "contact_info": default_contact,
            },
            "media_analysis": {
                "primary_analysis_method": media_analysis.get("primary_analysis_method"),
                "ocr_extracted_text": media_analysis.get("ocr_extracted_text", []),
                "detected_objects": media_analysis.get("detected_objects", []),
            },
            "offering_data": [],
            "offering_suggestions": [],
            "selected_offerings": [],
            "processing_metadata": {
                "overall_confidence": 0.0,
                "requires_user_confirmation": True,
                "schema_version": "1.1",
            },
        }

    raw_contact = business_details.get("contact_info") or {}
    contact_info = {
        "phone_number": _norm_field(raw_contact.get("phone_number")),
        "email":        _norm_field(raw_contact.get("email")),
        "website":      _norm_field(raw_contact.get("website")),
        "address":      _norm_field(raw_contact.get("address")),
    }

    offering_data = [o for o in offerings if o.get("suggestion_source") == "ocr"]
    offering_suggestions = [o for o in offerings if o.get("suggestion_source") != "ocr"]

    return {
        "business_details": {
            "business_name": _norm_field(business_details.get("business_name")),
            "business_description_extracted": _norm_field(business_details.get("business_description_extracted")),
            "business_description_ai_generated": _norm_field(business_details.get("business_description_ai_generated")),
            "business_category": _norm_field(business_details.get("business_category")),
            "business_confidence": business_details.get("business_confidence", round(overall_confidence, 2)),
            "contact_info": contact_info,
        },
        "media_analysis": {
            "primary_analysis_method": media_analysis.get("primary_analysis_method"),
            "ocr_extracted_text": media_analysis.get("ocr_extracted_text", []),
            "detected_objects": media_analysis.get("detected_objects", []),
        },
        "offering_data": offering_data,
        "offering_suggestions": offering_suggestions,
        "selected_offerings": [],
        "processing_metadata": {
            "overall_confidence": round(overall_confidence, 2),
            "requires_user_confirmation": True,
            "schema_version": "1.1",
        },
    }


CURRENCY_SYMBOLS: Dict[str, str] = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}

_PRICE_TOKEN_RE = re.compile(
    r"(?:[₹$€£]|\b(?:INR|USD|EUR|GBP|Rs)\.?)\s*(\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)


def price_key(value: Any) -> str:
    """Return a comparable key for a price so 500, 500.0 and "500.00" match."""
    try:
        return f"{float(str(value).replace(',', '')):.4f}"
    except (TypeError, ValueError):
        logger.debug("offerings: price %r is not numeric; comparing it as text", value)
        return str(value).strip().lower()


def build_items_listing(items: list) -> Tuple[str, str, set]:
    """Group flat items by category into prompt text, a literal bulleted listing and the set of price keys."""
    categories: Dict[str, list] = {}
    allowed_prices: set = set()
    for item in items or []:
        cat = item.get("category_name") or item.get("categoryname") or "General"
        name = item.get("item_name") or item.get("itemname", "")
        price = item.get("item_price")
        if price is None:
            price = item.get("itemprice")
        if not name:
            continue
        if price is None:
            entry = name
        else:
            code = str(item.get("item_currency") or "INR").upper()
            entry = f"{name} ({CURRENCY_SYMBOLS.get(code, code + ' ')}{price})"
            allowed_prices.add(price_key(price))
        categories.setdefault(cat, []).append(entry)

    if not categories:
        return "", "", set()

    lines: List[str] = []
    for cat, cat_items in categories.items():
        lines.append(f"{cat}:")
        lines.extend(f"  - {it}" for it in cat_items)

    literal_listing = "\n".join(
        "\n".join([f"• {cat}"] + [f"    - {it}" for it in cat_items])
        for cat, cat_items in categories.items()
    )
    return "\n".join(lines), literal_listing, allowed_prices


async def generate_items_description(items: list, llm: Any = None) -> str:
    """Return an LLM bulleted catalogue summary, or the literal listing when the LLM fails or invents a price.

    Args:
        items: Flat item dicts with category_name, item_name, item_price and item_currency.
        llm: Chat client to use instead of the shared gpt41 instance.

    Returns:
        Bulleted description text, or "" when there is nothing to describe.
    """
    if not items:
        return ""

    items_text, literal_listing, allowed_prices = build_items_listing(items)
    if not items_text:
        return ""

    price_rules = (
        "- NEVER invent, estimate, infer or convert a price. Reproduce ONLY a price shown in "
        "parentheses next to an item, exactly as written, keeping its currency symbol\n"
        "- An item with no price in parentheses MUST be listed by name alone\n"
    )
    if not allowed_prices:
        price_rules += (
            "- NONE of these items has a price. Do not mention any price, cost, rate, fee, "
            "range or currency anywhere in your answer\n"
        )

    prompt = (
        "You are summarizing a business catalogue. Based on the items listed below, "
        "write a concise bulleted description that:\n"
        "- Uses a '•' bullet for each category heading\n"
        "- Lists individual items under each category with a '-' bullet\n"
        f"{price_rules}"
        "- Gives a clear, scannable overview of the full catalogue\n"
        "- Is ALWAYS written in English regardless of the item name languages\n\n"
        f"Items:\n{items_text}\n\n"
        "Write only the bulleted description. No introduction or closing remarks."
    )

    try:
        response = await (llm or gpt41).ainvoke([{"role": "user", "content": prompt}])
        description = (getattr(response, "content", None) or str(response)).strip()
    except Exception as e:
        logger.warning("Failed to generate items description: %s", str(e))
        return literal_listing

    invented = [
        amount for amount in _PRICE_TOKEN_RE.findall(description)
        if price_key(amount) not in allowed_prices
    ]
    if invented:
        logger.warning(
            "generate_items_description: reply quoted price(s) %s absent from the catalogue - "
            "using the literal listing instead", invented[:5],
        )
        return literal_listing

    return description or literal_listing


def _merge_offering_tree(
    existing_tree: List[Dict[str, Any]],
    incoming_tree: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Merge an incoming offering tree into the existing one in place by name, returning (tree, items_added)."""
    added = 0
    by_off = {
        (off.get("offering_name") or "").strip().lower(): off
        for off in existing_tree
    }
    for new_off in incoming_tree or []:
        okey = (new_off.get("offering_name") or "").strip().lower()
        target_off = by_off.get(okey)
        if target_off is None:
            existing_tree.append(new_off)
            by_off[okey] = new_off
            added += sum(len(c.get("items") or []) for c in (new_off.get("categories") or []))
            continue

        target_cats = target_off.setdefault("categories", [])
        by_cat = {
            (c.get("category_name") or "").strip().lower(): c for c in target_cats
        }
        for new_cat in new_off.get("categories") or []:
            ckey = (new_cat.get("category_name") or "").strip().lower()
            target_cat = by_cat.get(ckey)
            if target_cat is None:
                target_cats.append(new_cat)
                by_cat[ckey] = new_cat
                added += len(new_cat.get("items") or [])
                continue

            target_items = target_cat.setdefault("items", [])
            seen_items = {
                (it.get("item_name") or "").strip().lower()
                for it in target_items
                if it.get("item_name")
            }
            for new_item in new_cat.get("items") or []:
                ikey = (new_item.get("item_name") or "").strip().lower()
                if ikey and ikey in seen_items:
                    continue
                target_items.append(new_item)
                if ikey:
                    seen_items.add(ikey)
                added += 1
    return existing_tree, added


def _flatten_tree_items(tree: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten an offering tree into category_name, item_name, item_price and item_currency dicts."""
    flat: List[Dict[str, Any]] = []
    for off in tree or []:
        for cat in off.get("categories") or []:
            category_name = cat.get("category_name")
            for item in cat.get("items") or []:
                flat.append({
                    "category_name": category_name,
                    "item_name": item.get("item_name"),
                    "item_price": item.get("item_price"),
                    "item_currency": item.get("item_currency"),
                })
    return flat


def assign_node_ids(tree: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Give every offering, category and item node lacking an id a new UUID, in place, and return the tree."""
    for offering in tree or []:
        if not isinstance(offering, dict):
            continue
        if not offering.get("offering_id"):
            offering["offering_id"] = str(uuid.uuid4())
        for category in offering.get("categories") or []:
            if not isinstance(category, dict):
                continue
            if not category.get("category_id"):
                category["category_id"] = str(uuid.uuid4())
            for item in category.get("items") or []:
                if not isinstance(item, dict):
                    continue
                if not item.get("item_id"):
                    item["item_id"] = str(uuid.uuid4())
    return tree


def apply_item_operations(
    items: List[Dict[str, Any]],
    ops: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply add, remove and update operations to a flat items list by item_id and return the result."""
    for op in ops or []:
        kind = op.get("op")
        item_id = op.get("item_id")
        data: Dict[str, Any] = op.get("data") or {}

        if kind == "add":
            new_item = dict(data)
            if not new_item.get("item_id"):
                new_item["item_id"] = str(uuid.uuid4())
            items.append(new_item)

        elif kind == "remove":
            if not item_id:
                logger.warning("apply_item_operations: remove op missing item_id — skipped")
                continue
            items = [it for it in items if it.get("item_id") != item_id]

        elif kind == "update":
            if not item_id:
                logger.warning("apply_item_operations: update op missing item_id — skipped")
                continue
            matched = False
            for it in items:
                if it.get("item_id") == item_id:
                    for k, v in data.items():
                        if v is not None:
                            it[k] = v
                    matched = True
                    break
            if not matched:
                logger.warning("apply_item_operations: item_id=%s not found — skipped", item_id)

    return items


def apply_category_operations(
    categories: List[Dict[str, Any]],
    ops: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply add, remove and update operations, including nested item operations, to categories and return the result."""
    for op in ops or []:
        kind = op.get("op")
        category_id = op.get("category_id")
        data: Dict[str, Any] = op.get("data") or {}
        item_ops: List[Dict[str, Any]] = op.get("item_operations") or []

        if kind == "add":
            new_cat = dict(data)
            if not new_cat.get("category_id"):
                new_cat["category_id"] = str(uuid.uuid4())
            for item in new_cat.get("items") or []:
                if isinstance(item, dict) and not item.get("item_id"):
                    item["item_id"] = str(uuid.uuid4())
            categories.append(new_cat)

        elif kind == "remove":
            if not category_id:
                logger.warning("apply_category_operations: remove op missing category_id — skipped")
                continue
            categories = [c for c in categories if c.get("category_id") != category_id]

        elif kind == "update":
            if not category_id:
                logger.warning("apply_category_operations: update op missing category_id — skipped")
                continue
            matched = False
            for cat in categories:
                if cat.get("category_id") == category_id:
                    for k, v in data.items():
                        if v is not None and k != "items":
                            cat[k] = v
                    if item_ops:
                        cat["items"] = apply_item_operations(
                            cat.get("items") or [], item_ops
                        )
                    matched = True
                    break
            if not matched:
                logger.warning("apply_category_operations: category_id=%s not found — skipped", category_id)

    return categories


def apply_offering_operations(
    offerings: List[Dict[str, Any]],
    ops: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply add, remove and update operations, including nested category operations, to offerings and return the result."""
    for op in ops or []:
        kind = op.get("op")
        offering_id = op.get("offering_id")
        cat_ops: List[Dict[str, Any]] = op.get("category_operations") or []

        if kind == "add":
            new_offering: Dict[str, Any] = {
                "offering_id": offering_id or str(uuid.uuid4()),
                "offering_name": op.get("offering_name"),
                "offering_type": op.get("offering_type"),
                "offering_image": op.get("offering_image"),
                "offering_description": op.get("offering_description"),
                "categories": [],
            }
            if cat_ops:
                new_offering["categories"] = apply_category_operations([], cat_ops)
            offerings.append(new_offering)

        elif kind == "remove":
            if not offering_id:
                logger.warning("apply_offering_operations: remove op missing offering_id — skipped")
                continue
            offerings = [o for o in offerings if o.get("offering_id") != offering_id]

        elif kind == "update":
            if not offering_id:
                logger.warning("apply_offering_operations: update op missing offering_id — skipped")
                continue
            matched = False
            for offering in offerings:
                if offering.get("offering_id") == offering_id:
                    for field in ("offering_name", "offering_type", "offering_image", "offering_description"):
                        val = op.get(field)
                        if val is not None:
                            offering[field] = val
                    if cat_ops:
                        offering["categories"] = apply_category_operations(
                            offering.get("categories") or [], cat_ops
                        )
                    matched = True
                    break
            if not matched:
                logger.warning("apply_offering_operations: offering_id=%s not found — skipped", offering_id)

    return offerings


def _norm_key(value: Optional[str]) -> str:
    """Return a stripped, lower-cased grouping key for a name."""
    return (value or "").strip().lower()


def _map_offering_item(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a flat OCR or canonical item record to the ParsedOfferingItem shape, or None without a name."""
    name = rec.get("item_name") or rec.get("itemname")
    if not name or not str(name).strip():
        return None
    confidence = rec.get("item_confidence")
    if confidence is None:
        confidence = rec.get("confidence_score")
    return {
        "item_id": rec.get("item_id") or None,
        "item_name": str(name).strip(),
        "item_type": rec.get("item_type"),
        "item_image": rec.get("item_image") or None,
        "item_description": rec.get("item_description") or rec.get("item_description_extracted"),
        "item_description_ai_generated": rec.get("item_description_ai_generated"),
        "item_price": rec.get("item_price"),
        "item_currency": rec.get("item_currency"),
        "item_confidence": confidence if confidence is not None else 0.0,
        "source_type": rec.get("source_type") or rec.get("source"),
    }


def normalize_offering_tree(raw_items: Any) -> List[Dict[str, Any]]:
    """Fold flat, nested or hybrid offering records into one deduplicated offering-category-item tree, dropping empty offerings."""
    if not isinstance(raw_items, list):
        return []

    offerings: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def get_offering(rec: Dict[str, Any]) -> Dict[str, Any]:
        okey = _norm_key(rec.get("offering_name"))
        off = offerings.get(okey)
        if off is None:
            off = {
                "offering_id": rec.get("offering_id") or None,
                "offering_name": (rec.get("offering_name") or None),
                "offering_type": rec.get("offering_type") or "product",
                "offering_image": rec.get("offering_image") or None,
                "offering_description": rec.get("offering_description") or None,
                "offering_description_ai_generated": rec.get("offering_description_ai_generated") or None,
                "offering_confidence": rec.get("offering_confidence") if rec.get("offering_confidence") is not None else 0.0,
                "source_type": rec.get("source_type") or None,
                "categories": [],
                "_cat_index": {},
            }
            offerings[okey] = off
            order.append(okey)
        else:
            if not off.get("offering_id") and rec.get("offering_id"):
                off["offering_id"] = rec.get("offering_id")
            if not off.get("offering_image") and rec.get("offering_image"):
                off["offering_image"] = rec.get("offering_image")
            if not off.get("offering_description") and rec.get("offering_description"):
                off["offering_description"] = rec.get("offering_description")
            if not off.get("offering_description_ai_generated") and rec.get("offering_description_ai_generated"):
                off["offering_description_ai_generated"] = rec.get("offering_description_ai_generated")
        return off

    def get_category(off: Dict[str, Any], src: Dict[str, Any], name: Optional[str]) -> Dict[str, Any]:
        ckey = _norm_key(name)
        cat = off["_cat_index"].get(ckey)
        if cat is None:
            cat = {
                "category_id": src.get("category_id") or None,
                "category_name": (name or None),
                "category_image": src.get("category_image") or None,
                "category_description": src.get("category_description") or None,
                "category_description_ai_generated": src.get("category_description_ai_generated") or None,
                "category_confidence": src.get("category_confidence") if src.get("category_confidence") is not None else 0.0,
                "source_type": src.get("source_type") or src.get("source") or None,
                "items": [],
                "_item_keys": set(),
            }
            off["_cat_index"][ckey] = cat
            off["categories"].append(cat)
        else:
            if not cat.get("category_id") and src.get("category_id"):
                cat["category_id"] = src.get("category_id")
            if not cat.get("category_image") and src.get("category_image"):
                cat["category_image"] = src.get("category_image")
            if not cat.get("category_description") and src.get("category_description"):
                cat["category_description"] = src.get("category_description")
        return cat

    def add_item(cat: Dict[str, Any], item: Optional[Dict[str, Any]]) -> None:
        if not item:
            return
        ikey = _norm_key(item.get("item_name"))
        if ikey and ikey in cat["_item_keys"]:
            if item.get("item_id"):
                for existing_item in cat["items"]:
                    if _norm_key(existing_item.get("item_name")) == ikey and not existing_item.get("item_id"):
                        existing_item["item_id"] = item["item_id"]
                        break
            return
        cat["items"].append(item)
        if ikey:
            cat["_item_keys"].add(ikey)

    for rec in raw_items:
        if not isinstance(rec, dict):
            continue
        off = get_offering(rec)

        flat_item = _map_offering_item(rec)
        if flat_item:
            cat = get_category(off, rec, rec.get("category_name"))
            add_item(cat, flat_item)

        for cat_rec in (rec.get("categories") or []):
            if not isinstance(cat_rec, dict):
                continue
            cat = get_category(off, cat_rec, cat_rec.get("category_name"))
            for it_rec in (cat_rec.get("items") or []):
                if isinstance(it_rec, dict):
                    add_item(cat, _map_offering_item(it_rec))

    result: List[Dict[str, Any]] = []
    for okey in order:
        off = offerings[okey]
        clean_cats = []
        for cat in off["categories"]:
            cat.pop("_item_keys", None)
            if cat["items"]:
                clean_cats.append(cat)
        off.pop("_cat_index", None)
        off["categories"] = clean_cats
        if clean_cats:
            result.append(off)
    return result


async def mirror_offering_tree_to_serviceitems(
    userprofile_id: str,
    tree: List[Dict[str, Any]],
    psid_hints: Optional[Dict[str, str]] = None,
    sole_row_fallback: bool = False,
) -> Dict[str, List[Dict[str, Any]]]:
    """Write each offering's categories onto the matching service row's serviceitems in both service tables.

    Args:
        userprofile_id: Provider's userprofileid, which scopes the service rows.
        tree: The persisted offering tree.
        psid_hints: Normalized offering_name to providerservicesid; wins over name matching.
        sole_row_fallback: Assign unresolved offerings to the provider's only service row.

    Returns:
        providerservicesid mapped to the categories written onto it.
    """
    service_rows = await Database.fetch(
        "SELECT providerservicesid, providerid, services, servicedescription "
        "FROM etown_providerservices WHERE userprofileid = $1",
        userprofile_id,
    )
    if not service_rows:
        logger.warning(
            "mirror_offering_tree: no etown_providerservices rows for userprofileid=%s", userprofile_id
        )
        return {}

    rows_by_psid = {str(r["providerservicesid"]): r for r in service_rows}
    psid_by_name: Dict[str, str] = {
        _norm_key(r["services"]): str(r["providerservicesid"])
        for r in service_rows
        if r["services"]
    }
    hints = {_norm_key(k): str(v) for k, v in (psid_hints or {}).items() if v}
    sole_psid = str(service_rows[0]["providerservicesid"]) if len(service_rows) == 1 else None

    categories_by_psid: Dict[str, List[Dict[str, Any]]] = {}
    unresolved: List[str] = []
    for offering in tree or []:
        if not isinstance(offering, dict):
            continue
        nkey = _norm_key(offering.get("offering_name"))
        psid = hints.get(nkey) or psid_by_name.get(nkey)
        if not psid and sole_row_fallback:
            psid = sole_psid
        if not psid or psid not in rows_by_psid:
            unresolved.append(offering.get("offering_name") or "<unnamed>")
            continue
        categories_by_psid.setdefault(psid, []).extend(offering.get("categories") or [])

    for psid, categories in categories_by_psid.items():
        si_json = json.dumps(categories)
        await Database.execute(
            "UPDATE etown_providerservices SET serviceitems = $1::jsonb, modifieddate = NOW() "
            "WHERE providerservicesid = $2",
            si_json, psid,
        )
        copy_status = await Database.execute(
            "UPDATE etown_providerservices_copy SET serviceitems = $1::jsonb, modifieddate = NOW() "
            "WHERE providerservicesid = $2",
            si_json, psid,
        )
        if isinstance(copy_status, str) and copy_status.strip().endswith(" 0"):
            row = rows_by_psid[psid]
            await Database.execute(
                "UPDATE etown_providerservices_copy SET serviceitems = $1::jsonb, modifieddate = NOW() "
                "WHERE providerid = $2 "
                "AND services IS NOT DISTINCT FROM $3 "
                "AND servicedescription IS NOT DISTINCT FROM $4",
                si_json, row["providerid"], row["services"], row["servicedescription"],
            )

    logger.info(
        "mirror_offering_tree: userprofileid=%s mirrored %d/%d offering(s) onto %d service row(s)%s",
        userprofile_id, len(tree or []) - len(unresolved), len(tree or []),
        len(categories_by_psid),
        f" — unresolved: {unresolved}" if unresolved else "",
    )
    return categories_by_psid


async def save_parsed_offerings_to_db(
    business_details: Dict[str, Any],
    offering_data: List[Dict[str, Any]],
    sole_row_fallback: bool = False,
    llm: Any = None,
) -> int:
    """Merge an offering tree into the provider's etown_offerings row, regenerate its description, and mirror serviceitems.

    Args:
        business_details: Parsed business fields; must carry userprofileid.
        offering_data: Incoming offering tree to merge in.
        sole_row_fallback: Passed to the serviceitems mirror.
        llm: Chat client for the catalogue description instead of the shared gpt41.

    Returns:
        Number of new items added, 0 on no-op or error.
    """
    user_profile_id = (business_details or {}).get("userprofileid")
    if not user_profile_id:
        logger.warning("save_parsed_offerings_to_db: missing userprofileid in business_details")
        return 0

    try:
        existing_row = await Database.fetchrow(
            "SELECT items FROM etown_offerings WHERE userprofileid = $1",
            user_profile_id,
        )

        business_name = business_details.get("business_name") or "Unknown Business"
        first_offering = (offering_data[0] if offering_data else {}) or {}
        business_type = first_offering.get("offering_type") or "product"
        business_subtype = business_details.get("business_category") or "product"
        business_description = (
            business_details.get("business_description_ai_generated")
            or business_details.get("business_description")
            or ""
        )

        if existing_row:
            raw_items = existing_row["items"]
            existing_tree = json.loads(raw_items) if isinstance(raw_items, str) else (raw_items or [])
            if not isinstance(existing_tree, list):
                existing_tree = []
        else:
            existing_tree = []

        before_count = len(_flatten_tree_items(normalize_offering_tree(existing_tree)))
        merged_tree = normalize_offering_tree((offering_data or []) + existing_tree)
        assign_node_ids(merged_tree)
        flat_items = _flatten_tree_items(merged_tree)
        added = max(0, len(flat_items) - before_count)

        description = await generate_items_description(flat_items, llm=llm) if flat_items else None

        if existing_row:
            await Database.execute(
                """
                UPDATE etown_offerings
                SET items = $1::jsonb,
                    business_details = $2::jsonb,
                    businessname = $3,
                    businesstype = $4,
                    businesssubtype = $5,
                    businessdescription = $6,
                    description = $7,
                    modifieddate = NOW()
                WHERE userprofileid = $8
                """,
                json.dumps(merged_tree),
                json.dumps(business_details),
                business_name,
                business_type,
                business_subtype,
                business_description,
                description or None,
                user_profile_id,
            )
        else:
            await Database.execute(
                """
                INSERT INTO etown_offerings (
                    offeringsid, userprofileid, businessname, businesstype,
                    businesssubtype, businessdescription, items, business_details,
                    description, createdon
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, CURRENT_TIMESTAMP)
                ON CONFLICT (userprofileid) DO UPDATE
                SET items               = EXCLUDED.items,
                    business_details    = EXCLUDED.business_details,
                    businessname        = EXCLUDED.businessname,
                    businesstype        = EXCLUDED.businesstype,
                    businesssubtype     = EXCLUDED.businesssubtype,
                    businessdescription = EXCLUDED.businessdescription,
                    description         = EXCLUDED.description,
                    modifieddate        = NOW()
                """,
                str(uuid.uuid4()),
                user_profile_id,
                business_name,
                business_type,
                business_subtype,
                business_description,
                json.dumps(merged_tree),
                json.dumps(business_details),
                description or None,
            )

        await mirror_offering_tree_to_serviceitems(
            user_profile_id,
            merged_tree,
            psid_hints={
                (o or {}).get("offering_name"): (o or {}).get("providerservices_id")
                for o in (offering_data or [])
                if isinstance(o, dict) and o.get("providerservices_id")
            },
            sole_row_fallback=sole_row_fallback,
        )

        logger.info(
            "save_parsed_offerings_to_db: %s %d new item(s) for user %s (%s row)",
            "appended" if existing_row else "inserted",
            added,
            user_profile_id,
            "existing" if existing_row else "new",
        )
        return added

    except Exception as e:
        logger.error(
            "save_parsed_offerings_to_db error for user %s: %s", user_profile_id, str(e), exc_info=True
        )
        return 0


_OFFERINGS_GEN_PROMPT = """\
You are a business data normaliser. Convert each service row into exactly one \
offering node. Output a COMPACT JSON ARRAY (no whitespace between tokens).

Output schema — one object per input row, SAME ORDER as input:
{{"offering_name":<string: service_name verbatim>,"offering_type":<"product"|"service">,\
"offering_description":<English max 15 words>,"offering_description_ai_generated":\
<English max 30 words>,"offering_confidence":0.9,"source_type":"auto_generated",\
"offering_image":<first non-null image URL or null>,"categories":[\
{{"category_name":<string>,"category_description":<English max 12 words or null>,\
"category_description_ai_generated":null,"category_confidence":0.9,"source_type":"auto_generated",\
"category_image":null,"items":[{{"item_name":<string: PRESERVE original language/script>,\
"item_type":<"product"|"service">,"item_image":null,"item_description":<raw text or null>,\
"item_description_ai_generated":<English max 8 words>,"item_price":<number or null>,\
"item_currency":<code or "INR">,"item_confidence":0.9,"source_type":"auto_generated"}}]}}]}}

Rules:
1. If serviceitems is provided: map those categories/items verbatim to the schema above, \
   preserving every item_name and item_price exactly. Add item_description_ai_generated \
   (English) for each item.
2. If serviceitems is null or empty: derive categories and items ONLY from words that \
   actually appear in business_name, service_name, service_description, service_category \
   and business_category. Do NOT introduce any product, category or item from a domain \
   that is NOT present in those fields — never output plumbing, rental, repair, \
   installation, maintenance, or any service concept unless that exact word appears in the \
   source text. Treat business_name as the STRONGEST domain signal: when service_name / \
   service_description are generic (e.g. "Comprehensive Handyman Solutions", "General \
   Services") but business_name names a specific brand or domain, derive the offering from \
   the business_name's real domain and ignore the generic service text's implied domain. \
   For example: business_name "Indane" (an LPG / gas brand) must yield gas-cylinder booking \
   or refill offerings, NOT plumbing/handyman; a hardware / power-tools shop must yield \
   tools and hardware products, NOT rental services. If the source signal is genuinely thin, \
   output exactly ONE category named after business_name (or service_name) with ONE item \
   named after it — prefer fewer, safe entries over invented ones. Never invent prices \
   (null). For anything synthesised under this rule, set offering_confidence and \
   category_confidence to 0.5 so the reviewer can flag it.
3. offering_type and item_type ("product" vs "service") — decide from business_category and \
   service_name together: use "product" for goods / retail / trade language (store, shop, \
   mart, retail, goods, hardware, tools, paints, electricals, electronics, building \
   materials, grocery, stationery, pharmacy, LPG, gas, cylinder, agency, distributor, \
   dealer, traders, supplies, spare parts); use "service" for work performed for a customer \
   (repair, service, rental, hire, installation, maintenance, salon, spa, clinic, tuition, \
   consultancy, cleaning, plumbing, electrician, catering, transport). When both or neither \
   apply, default to "product" for retail/agency/distributor/dealer language, else "service".
4. item_description_ai_generated must always be in English.
5. Output ONLY the compact JSON array. No markdown. No explanation.

Input:
{rows_json}"""


def _build_offerings_gen_prompt(service_rows: list) -> str:
    """Return the offerings-generation prompt filled with compact JSON of the service rows."""
    compact = []
    for r in service_rows:
        compact.append({
            "business_name": r.get("business_name"),
            "service_name": r.get("services"),
            "service_description": r.get("servicedescription"),
            "business_category": r.get("businesscategory"),
            "service_category": r.get("servicecategory"),
            "image1": r.get("serviceimageurl1"),
            "image2": r.get("serviceimageurl2"),
            "image3": r.get("serviceimageurl3"),
            "serviceitems": r.get("serviceitems"),
        })
    return _OFFERINGS_GEN_PROMPT.format(rows_json=json.dumps(compact, ensure_ascii=False))


def _extract_offerings_json_array(text: str) -> Optional[list]:
    """Parse a JSON array from LLM text, falling back to its complete top-level objects, or None."""
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start: end + 1])
        except json.JSONDecodeError:
            logger.debug("offerings: the outer JSON array did not parse; falling back to object extraction")
    objects: list = []
    depth = 0
    obj_start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    objects.append(json.loads(text[obj_start: i + 1]))
                except json.JSONDecodeError:
                    logger.debug("offerings: skipping a catalogue object that did not parse")
                obj_start = None
    return objects if objects else None


async def _normalize_service_rows_with_llm(
    service_rows: List[Dict[str, Any]],
    log_label: str = "",
    max_retries: int = 3,
    llm: Any = None,
) -> Optional[List[Dict[str, Any]]]:
    """Convert service rows into one validated LLM offering node per row, retrying with backoff; None on failure.

    Args:
        service_rows: etown_providerservices-shaped rows.
        log_label: Identifier included in warning and error logs.
        max_retries: Maximum LLM attempts before giving up.
        llm: Optional chat model override; defaults to gpt41.

    Returns:
        One offering node per row in order, [] for no rows, or None when all attempts fail.
    """
    if not service_rows:
        return []

    client = llm or gpt41
    prompt = _build_offerings_gen_prompt(service_rows)
    backoff_base = 2
    suffix = f" for {log_label}" if log_label else ""

    for attempt in range(1, max_retries + 1):
        try:
            response = await client.ainvoke([{"role": "user", "content": prompt}])
            raw = (getattr(response, "content", None) or str(response)).strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                raw = raw.rsplit("```", 1)[0].strip()

            parsed = _extract_offerings_json_array(raw)

            if not isinstance(parsed, list):
                raise ValueError(
                    f"LLM returned non-list ({type(parsed).__name__}). "
                    f"Raw (first 300 chars): {raw[:300]}"
                )
            if len(parsed) != len(service_rows):
                raise ValueError(
                    f"Count mismatch: expected {len(service_rows)}, got {len(parsed)}"
                )
            for idx, node in enumerate(parsed):
                if not isinstance(node, dict):
                    raise ValueError(f"Node {idx} is not a dict")
                if not node.get("offering_name"):
                    for alt in ("name", "service_name", "serviceName", "title"):
                        if node.get(alt):
                            node["offering_name"] = node[alt]
                            break
                if not node.get("categories"):
                    for alt in ("category", "items", "services"):
                        if isinstance(node.get(alt), list):
                            node["categories"] = node[alt]
                            break
                if not node.get("offering_name"):
                    node["offering_name"] = service_rows[idx].get("services") or "Service"
                if not node.get("offering_name"):
                    raise ValueError(f"Node {idx} missing offering_name")
                if not isinstance(node.get("categories"), list):
                    raise ValueError(f"Node {idx} has no categories list")

            return parsed

        except BaseException as exc:
            if attempt < max_retries:
                wait = backoff_base ** attempt
                logger.warning(
                    "[normalize_services] Attempt %d/%d failed%s — retry in %ds: %s",
                    attempt, max_retries, suffix, wait, exc,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "[normalize_services] All %d retries exhausted%s: %s",
                    max_retries, suffix, exc,
                 exc_info=True)

    return None


async def generate_and_store_offerings_from_provider(
    userprofile_id: str,
    provider_id: str,
    contact_id: str,
    business_name: str,
    business_category: str,
    service_category: str,
    description: Optional[str],
    services: Optional[str],
    serviceimageurl: Optional[str],
    address: Optional[str],
    phone_number: Optional[str],
    email: Optional[str],
) -> bool:
    """Generate an offering tree for a Public Insert provider via the LLM, save it, and return True on success."""
    try:
        service_row: Dict[str, Any] = {
            "business_name":      business_name,
            "services":        services or business_name,
            "servicedescription": description or business_name,
            "businesscategory":   business_category,
            "servicecategory":    service_category,
            "serviceimageurl1":   serviceimageurl,
            "serviceimageurl2":   None,
            "serviceimageurl3":   None,
            "serviceitems":       None,
        }
        service_rows = [service_row]

        offering_nodes = await _normalize_service_rows_with_llm(
            service_rows, log_label=userprofile_id
        )
        if offering_nodes is None:
            return False

        assign_node_ids(offering_nodes)

        business_details: Dict[str, Any] = {
            "providerid":                        provider_id,
            "userprofileid":                     userprofile_id,
            "contactid":                         contact_id,
            "business_name":                     business_name,
            "business_category":                 business_category,
            "business_description":              description,
            "business_description_ai_generated": None,
            "business_image":                    serviceimageurl,
            "phone_number":                      phone_number,
            "email":                             email,
            "website":                           None,
            "address":                           address,
            "confidence_score":                  0.9,
            "business_category_confidence":      0.9,
        }

        await save_parsed_offerings_to_db(
            business_details, offering_nodes, sole_row_fallback=True
        )
        logger.info(
            "[generate_offerings] Stored offerings for provider %s (%s)",
            userprofile_id, business_name,
        )
        return True

    except Exception as exc:
        logger.error(
            "[generate_offerings] Failed for provider %s (%s): %s",
            userprofile_id, business_name, exc, exc_info=True,
        )
        return False


async def build_service_conversion_preview(userprofile_id: str, force: bool = False) -> Dict[str, Any]:
    """Build an unsaved offerings preview from a provider's service rows, keeping existing node ids; returns a status dict."""
    if not userprofile_id or not str(userprofile_id).strip():
        return {"userprofileid": userprofile_id, "status": "failed", "reason": "missing userprofileid"}

    userprofile_id = str(userprofile_id).strip()

    try:
        profile = await Database.fetchrow(
            "SELECT role, rolename FROM etown_userprofile WHERE userprofileid = $1 LIMIT 1",
            userprofile_id,
        )
        if profile is not None and (
            profile["role"] == 2
            or (profile["rolename"] or "").strip().lower() == "member"
        ):
            return {
                "userprofileid": userprofile_id,
                "status": "member_account",
                "reason": "userprofileid belongs to a member account, not a provider",
            }

        existing = await Database.fetchrow(
            "SELECT offeringsid, items FROM etown_offerings WHERE userprofileid = $1",
            userprofile_id,
        )
        if existing and not force:
            return {"userprofileid": userprofile_id, "status": "skipped", "reason": "offerings already exist"}

        svc_records = await Database.fetch(
            """
            SELECT providerservicesid, services, servicedescription, businesscategory, servicecategory,
                   pricerange, serviceimageurl1, serviceimageurl2, serviceimageurl3,
                   serviceitems
            FROM   etown_providerservices
            WHERE  userprofileid = $1
            ORDER  BY createdon
            """,
            userprofile_id,
        )
        if not svc_records:
            return {"userprofileid": userprofile_id, "status": "skipped", "reason": "no services"}

        provider_rec = await Database.fetchrow(
            """
            SELECT providerid, contactid, businessname, businesscategory,
                   description, serviceimageurl, mobilenumber, email, address
            FROM   etown_provider
            WHERE  userprofileid = $1
            LIMIT  1
            """,
            userprofile_id,
        )
        prov = dict(provider_rec) if provider_rec else {}

        existing_by_name: Dict[str, Dict[str, Any]] = {}
        if existing:
            raw_tree = existing["items"]
            existing_tree = json.loads(raw_tree) if isinstance(raw_tree, str) else (raw_tree or [])
            if isinstance(existing_tree, list):
                for off in existing_tree:
                    if not isinstance(off, dict) or not off.get("categories"):
                        continue
                    entry = existing_by_name.setdefault(
                        _norm_key(off.get("offering_name")),
                        {"offering_id": off.get("offering_id"), "categories": []},
                    )
                    entry["categories"].extend(off["categories"])

        service_rows: List[Dict[str, Any]] = []
        for s in svc_records:
            d = dict(s)
            d["business_name"] = prov.get("businessname")
            raw_si = d.get("serviceitems")
            if isinstance(raw_si, str):
                try:
                    d["serviceitems"] = json.loads(raw_si)
                except (ValueError, TypeError):
                    logger.warning("offerings: serviceitems is not valid JSON for a service row; treating it as null", exc_info=True)
                    d["serviceitems"] = None
            has_items = any(
                c.get("items") for c in (d.get("serviceitems") or []) if isinstance(c, dict)
            )
            if not has_items and existing_by_name:
                entry = existing_by_name.get(_norm_key(d.get("services")))
                if entry is None and len(svc_records) == 1 and len(existing_by_name) == 1:
                    entry = next(iter(existing_by_name.values()))
                if entry and entry["categories"]:
                    d["serviceitems"] = entry["categories"]
                    logger.info(
                        "[convert_services] Seeded serviceitems for preview from the offerings tree "
                        "(userprofileid=%s, services=%r, %d category/ies)",
                        userprofile_id, d.get("services"), len(entry["categories"]),
                    )
            service_rows.append(d)

        offering_nodes = await _normalize_service_rows_with_llm(
            service_rows, log_label=userprofile_id
        )
        if offering_nodes is None:
            return {"userprofileid": userprofile_id, "status": "failed", "reason": "llm normalisation failed"}

        for node, src in zip(offering_nodes, service_rows):
            if not isinstance(node, dict):
                continue
            node["providerservices_id"] = src.get("providerservicesid")
            prior = existing_by_name.get(_norm_key(node.get("offering_name")))
            if prior and prior.get("offering_id"):
                node["offering_id"] = prior["offering_id"]
            src_by_cat = {
                _norm_key(c.get("category_name")): c
                for c in (src.get("serviceitems") or [])
                if isinstance(c, dict)
            }
            for cat in node.get("categories") or []:
                if not isinstance(cat, dict):
                    continue
                src_cat = src_by_cat.get(_norm_key(cat.get("category_name")))
                if not src_cat:
                    continue
                if src_cat.get("category_id"):
                    cat["category_id"] = src_cat["category_id"]
                src_by_item = {
                    _norm_key(i.get("item_name")): i
                    for i in (src_cat.get("items") or [])
                    if isinstance(i, dict)
                }
                for item in cat.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    src_item = src_by_item.get(_norm_key(item.get("item_name")))
                    if src_item and src_item.get("item_id"):
                        item["item_id"] = src_item["item_id"]

        assign_node_ids(offering_nodes)

        business_details: Dict[str, Any] = {
            "providerid":                        prov.get("providerid", ""),
            "userprofileid":                     userprofile_id,
            "contactid":                         prov.get("contactid", ""),
            "business_name":                     prov.get("businessname") or "Unknown Business",
            "business_category":                 prov.get("businesscategory") or "service",
            "business_description":              prov.get("description"),
            "business_description_ai_generated": None,
            "business_image":                    prov.get("serviceimageurl"),
            "phone_number":                      prov.get("mobilenumber"),
            "email":                             prov.get("email"),
            "website":                           None,
            "address":                           prov.get("address"),
            "confidence_score":                  0.9,
            "business_category_confidence":      0.9,
        }

        logger.info(
            "[convert_services] Preview built for user %s: %d service row(s) -> %d offering(s)%s",
            userprofile_id, len(service_rows), len(offering_nodes),
            " (force)" if (existing and force) else "",
        )
        return {
            "userprofileid": userprofile_id,
            "status": "ready",
            "services_processed": len(service_rows),
            "preview": {
                "business_details": business_details,
                "offering_data": offering_nodes,
            },
        }

    except Exception as exc:
        logger.error(
            "[convert_services] Preview build failed for user %s: %s", userprofile_id, exc, exc_info=True
        )
        return {"userprofileid": userprofile_id, "status": "failed", "reason": str(exc)[:300]}


def _parse_preview(raw: Any) -> Optional[Dict[str, Any]]:
    """Return a staging-row preview column decoded into a dict, or None."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("offerings: staging preview column is not valid JSON; treating it as null", exc_info=True)
            return None
    return raw if isinstance(raw, dict) else None


async def create_conversion_jobs(
    batch_id: str,
    queued_userprofileids: List[str],
    member_userprofileids: List[str],
    force: bool,
) -> None:
    """Insert staging rows for a convert-services batch: queued ids as 'pending', members as 'member_account'."""
    jobids: List[str] = []
    uids: List[str] = []
    statuses: List[str] = []
    reasons: List[Optional[str]] = []
    for uid in queued_userprofileids:
        jobids.append(str(uuid.uuid4()))
        uids.append(uid)
        statuses.append("pending")
        reasons.append(None)
    for uid in member_userprofileids:
        jobids.append(str(uuid.uuid4()))
        uids.append(uid)
        statuses.append("member_account")
        reasons.append("member account, not a provider")

    if not uids:
        return

    await Database.execute(
        """
        INSERT INTO etown_offerings_conversion_jobs
            (jobid, batchid, userprofileid, status, force, reason)
        SELECT t.j, $2, t.u, t.s, $5, t.r
        FROM unnest($1::text[], $3::text[], $4::text[], $6::text[]) AS t(j, u, s, r)
        ON CONFLICT (batchid, userprofileid) DO NOTHING
        """,
        jobids, batch_id, uids, statuses, force, reasons,
    )


async def fetch_conversion_batch(batch_id: str) -> List[Dict[str, Any]]:
    """Return every staging job for a batch with its preview decoded, in creation order."""
    rows = await Database.fetch(
        """
        SELECT userprofileid, status, reason, preview, items_added
        FROM   etown_offerings_conversion_jobs
        WHERE  batchid = $1
        ORDER  BY createdon
        """,
        batch_id,
    )
    result: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["preview"] = _parse_preview(d.get("preview"))
        result.append(d)
    return result


async def set_conversion_job_status(
    batch_id: str,
    userprofile_id: str,
    status: str,
    preview: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> None:
    """Set a staging job's status and reason, and its preview unless preview is None."""
    await Database.execute(
        """
        UPDATE etown_offerings_conversion_jobs
        SET status = $3,
            preview = COALESCE($4::jsonb, preview),
            reason = $5,
            updatedon = CURRENT_TIMESTAMP
        WHERE batchid = $1 AND userprofileid = $2
        """,
        batch_id,
        userprofile_id,
        status,
        json.dumps(preview) if preview is not None else None,
        reason,
    )


async def claim_conversion_job(batch_id: str, userprofile_id: str) -> Dict[str, Any]:
    """Atomically move a job from 'ready' to 'confirmed' and return ok, force and a not_found or not_ready reason."""
    row = await Database.fetchrow(
        """
        UPDATE etown_offerings_conversion_jobs
        SET status = 'confirmed', confirmedon = CURRENT_TIMESTAMP, updatedon = CURRENT_TIMESTAMP
        WHERE batchid = $1 AND userprofileid = $2 AND status = 'ready'
        RETURNING force
        """,
        batch_id,
        userprofile_id,
    )
    if row is not None:
        return {"ok": True, "force": bool(row["force"]), "reason": None}

    current = await Database.fetchrow(
        "SELECT status FROM etown_offerings_conversion_jobs WHERE batchid = $1 AND userprofileid = $2",
        batch_id,
        userprofile_id,
    )
    if current is None:
        return {"ok": False, "force": False, "reason": "not_found"}
    return {"ok": False, "force": False, "reason": f"not_ready ({current['status']})"}


async def record_conversion_confirmed(batch_id: str, userprofile_id: str, items_added: int) -> None:
    """Record the number of items persisted for a confirmed staging job."""
    await Database.execute(
        """
        UPDATE etown_offerings_conversion_jobs
        SET items_added = $3, updatedon = CURRENT_TIMESTAMP
        WHERE batchid = $1 AND userprofileid = $2
        """,
        batch_id,
        userprofile_id,
        items_added,
    )
