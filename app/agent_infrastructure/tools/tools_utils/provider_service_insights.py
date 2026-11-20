"""Provider Service Insights Tool - database-only provider details for follow-up questions.

Member-agent tool behind provider_service_insights: resolves a provider from agent state or by business name, disambiguates distinct businesses, and returns profile, services, offers, ratings, Places details and distance, or an enquiry brief, with missing fields marked and no web fallback.

Features:
    - State-first Resolution: Resolves provider service ID from agent state when unambiguous
    - Business Name Lookup: Direct provider lookup by exact or partial business name
    - Rating Enrichment: Appends average rating and review count to provider details
    - Missing Field Normalization: Replaces None/empty fields with 'not available' marker
    - Ambiguity Handling: Disambiguates only distinct businesses, named with their locality
    - Distance Calculation: Computes distance from user coordinates when available
    - Active Offers: Attaches the provider's non-expired offers alongside their services
    - Plan Badges: Reports the provider's paid-tier badge label and premium display
    - No Web Fallback: Strictly DB-only resolution to avoid unreliable external data

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    _parse_jsonb(): Decode a jsonb column value that asyncpg returns as a JSON string
    _fetch_provider_details(): Fetch extended Google Places details from etown_provider_details
    _na(): Normalize empty or None values to an explicit 'not available' marker
    _resolve_from_state(): Resolve provider service ID from agent state when deterministic
    _fetch_provider_row_by_id(): Fetch full provider row from DB by service ID
    _lookup_business_matches(): Search provider SERVICE rows by business name (exact, then partial)
    _business_group_key(): Identity key for the business owning a service row
    _normalize_text(): Lowercase/whitespace-collapse a value for comparison
    _group_matches_by_business(): Collapse matched service rows into one entry per business
    _describe_business_location(): Human-readable locality used to distinguish same-name businesses
    _build_selection_options(): Build one disambiguation option per distinct business
    _build_selection_message(): Model-visible message listing the real businesses to choose from
    _fetch_all_services_for_contact(): Fetch every service row owned by a contactid
    _fetch_rating_summary(): Latest etown_ratings row by userprofileid, contactid, then business name
    _build_selected_providers_data(): Shape service rows into the selected_providers_data payload
    _compute_distance_km(): Geodesic user-to-provider distance when both coordinates are valid
    _add_detail_line(): Append a labeled fact to a list only when the value is present
    _extract_review_snippet(): Build a compact "Author (rating★): text" Google review snippet
    _build_details_summary(): Turn stored Google Places details into short sentences
    _build_natural_success_response(): Compose the full profile response for non-enquiry purposes
    _build_enquiry_opener_brief(): Grounding facts and real offerings for purpose='enquiry'
    handle_provider_service_insights(): Main entry point for provider detail retrieval

Dependencies:
    - geopy: Geographic distance calculations between coordinates
    - app.core.logging: Centralized logging for retrieval operations
    - app.db.client: Async PostgreSQL database access with connection pooling
    - app.agent_infrastructure.tools.tools_utils.search_helpers: positive-review filter and threshold
    - app.etown.offers.offers_utils: Provider offer lookup for the active-offers payload
    - app.etown.plans.plans_utils: premium_display and resolve_provider_badges for plan badges

Updates:
    26-May-2026: Added Features, Classes, Functions, Dependencies, Updates sections to complete module docstring
    25-Jun-2026: Attached extended Google Places details (etown_provider_details) to insights output and natural response
    29-Jun-2026: Surfaced per-service `serviceitems` (offering categories/items JSONB) in the services payload
    23-Jul-2026: Added an additive `has_menu` boolean to provider_profile in the implicit_output.
    06-Aug-2026: implicit_output carries the provider's non-expired offers alongside their services.
    20-Aug-2026: Verified markers report the paid tier's badge label; implicit_output exposes Etown_providertype.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    08-Sep-2026: Google aggregate rating gated on POSITIVE_REVIEW_MIN_RATING.
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from geopy.distance import geodesic

from app.core.logging import logger
from app.db.client import Database
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.agent_infrastructure.tools.tools_utils.search_helpers import (
    POSITIVE_REVIEW_MIN_RATING,
    filter_positive_reviews,
)
from app.etown.offers.offers_utils import fetch_valid_and_pending_offers
_ROWS_PER_BUSINESS_SCAN = 10
_MAX_MATCH_SCAN_ROWS = 200


def _parse_jsonb(value: Any) -> Any:
    """Decode a jsonb value that asyncpg returned as a JSON string, returning None if it does not parse."""
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            logger.debug("provider insights: a jsonb column did not decode; treating it as null")
            return None
    return value


async def _fetch_provider_details(providerid: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetch a provider's Google Places details, dropping ratings below the positive threshold, or None if absent."""
    if not providerid:
        return None
    try:
        row = await Database.fetchrow(
            """
            SELECT place_id, google_rating, google_rating_count, business_status,
                   website_uri, google_maps_uri, opening_hours, reviews, images, comments
            FROM etown_provider_details
            WHERE providerid = $1
            LIMIT 1
            """,
            str(providerid),
        )
        if not row:
            return None
        try:
            _google_rating = float(row.get("google_rating"))
        except (TypeError, ValueError):
            _google_rating = None
        _rating_ok = _google_rating is not None and _google_rating >= POSITIVE_REVIEW_MIN_RATING
        details = {
            "place_id": row.get("place_id"),
            "google_rating": row.get("google_rating") if _rating_ok else None,
            "google_rating_count": row.get("google_rating_count") if _rating_ok else None,
            "business_status": row.get("business_status"),
            "website_uri": row.get("website_uri"),
            "google_maps_uri": row.get("google_maps_uri"),
            "opening_hours": _parse_jsonb(row.get("opening_hours")),
            "reviews": filter_positive_reviews(_parse_jsonb(row.get("reviews"))),
            "images": _parse_jsonb(row.get("images")),
        }
        comments_val = _parse_jsonb(row.get("comments"))
        if comments_val:
            details["comments"] = comments_val
        return details
    except Exception as exc:
        logger.warning("Error fetching provider details for providerid %s: %s", providerid, exc)
        return None


def _na(value: Any) -> Any:
    """Return 'not available' for None or blank strings, otherwise the value unchanged."""
    if value is None:
        return "not available"
    if isinstance(value, str) and not value.strip():
        return "not available"
    return value


async def _resolve_from_state(state: Optional[dict]) -> Optional[str]:
    """Return the providerservicesid from state's selected_providers when exactly one unique id is present."""
    if not state:
        return None
    selected_providers = state.get("selected_providers") or []
    if not isinstance(selected_providers, list) or not selected_providers:
        return None

    ids = [
        str(item.get("providerservicesid", "")).strip()
        for item in selected_providers
        if isinstance(item, dict) and str(item.get("providerservicesid", "")).strip()
    ]
    unique_ids = list(dict.fromkeys(ids))
    if len(unique_ids) == 1:
        return unique_ids[0]
    return None


async def _fetch_provider_row_by_id(providerservicesid: str) -> Optional[Dict[str, Any]]:
    """Fetch one etown_providerservices_copy row by providerservicesid, or None."""
    query = """
        SELECT providerservicesid, businessname, businesscategory, address, availability,
               city, contactid, contactidname, email, mobilenumber, pricerange,
               providerid, providertype, publicsearchprovider, servicecategory,
               servicedescription, serviceimageurl, serviceimageurl1, serviceimageurl2,
               serviceimageurl3, services, statecode, userprofileid, userprofileidname,
               websiteurl, yearsinbusiness, createdon, additionaladdressinfo, areaname,
               latitude, longitude, quantity, serviceitems
        FROM etown_providerservices_copy
        WHERE providerservicesid = $1
        LIMIT 1
    """
    row = await Database.fetchrow(query, providerservicesid)
    return dict(row) if row else None


async def _lookup_business_matches(business_name: str, match_limit: int) -> List[Dict[str, Any]]:
    """Fetch service rows whose business name matches exactly (case-insensitive), falling back to a substring match."""
    trimmed = " ".join(business_name.strip().split())
    if not trimmed:
        return []

    scan_limit = min(max(int(match_limit or 5), 1) * _ROWS_PER_BUSINESS_SCAN, _MAX_MATCH_SCAN_ROWS)

    query = """
        SELECT providerservicesid, businessname, services, contactid, city, address,
               areaname, latitude, longitude, mobilenumber, email, websiteurl, providertype
        FROM etown_providerservices_copy
        WHERE TRIM(businessname) ILIKE $1
        ORDER BY businessname, providerservicesid
        LIMIT $2
    """

    rows = await Database.fetch(query, trimmed, scan_limit)
    if not rows:
        rows = await Database.fetch(query, f"%{trimmed}%", scan_limit)

    return [dict(row) for row in rows]


def _business_group_key(row: Dict[str, Any]) -> str:
    """Return the business identity key for a service row: its contactid, else normalized name, city and address."""
    contactid = str(row.get("contactid") or "").strip().lower()
    if contactid:
        return f"contact:{contactid}"
    name = _normalize_text(row.get("businessname"))
    city = _normalize_text(row.get("city"))
    address = _normalize_text(row.get("address"))
    return f"name:{name}|{city}|{address}"


def _normalize_text(value: Any) -> str:
    """Return the value lowercased with whitespace collapsed, or an empty string for falsy input."""
    if not value:
        return ""
    return " ".join(str(value).lower().split())


def _group_matches_by_business(
    matches: List[Dict[str, Any]],
    business_name: str = "",
) -> List[Dict[str, Any]]:
    """Group service rows into one entry per business, with exact-name matches first.

    Args:
        matches: Service rows from the name lookup; duplicate ids are skipped.
        business_name: Requested name, used to flag and prefer exact matches.

    Returns:
        List of dicts with key, rows, representative and exact.
    """
    requested = _normalize_text(business_name)
    grouped: Dict[str, Dict[str, Any]] = {}
    seen_ids: set = set()

    for row in matches:
        svc_id = str(row.get("providerservicesid", "")).strip()
        if not svc_id or svc_id in seen_ids:
            continue
        seen_ids.add(svc_id)

        key = _business_group_key(row)
        group = grouped.get(key)
        if group is None:
            group = {"key": key, "rows": [], "representative": row, "exact": False}
            grouped[key] = group
        group["rows"].append(row)

        is_exact = bool(requested) and _normalize_text(row.get("businessname")) == requested
        if is_exact and not group["exact"]:
            group["exact"] = True
            group["representative"] = row

    groups = list(grouped.values())
    groups.sort(key=lambda g: 0 if g["exact"] else 1)
    return groups


def _describe_business_location(row: Dict[str, Any]) -> str:
    """Describe a provider row by area, city or address, else by its services, else 'location not listed'."""
    for field in ("areaname", "city", "address"):
        value = row.get(field)
        if value and str(value).strip() and str(value).strip().lower() != "not available":
            return " ".join(str(value).split())

    services = row.get("services")
    if services and str(services).strip() and str(services).strip().lower() != "not available":
        return f"offers {' '.join(str(services).split())}"

    return "location not listed"


def _build_selection_options(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build one disambiguation option per business group from its representative row and service count."""
    options: List[Dict[str, Any]] = []
    for group in groups:
        item = group["representative"]
        options.append(
            {
                "providerservicesid": item.get("providerservicesid"),
                "businessname": _na(item.get("businessname")),
                "services": _na(item.get("services")),
                "areaname": _na(item.get("areaname")),
                "city": _na(item.get("city")),
                "address": _na(item.get("address")),
                "service_count": len(group["rows"]),
            }
        )
    return options


def _build_selection_message(business_name: str, groups: List[Dict[str, Any]]) -> str:
    """Build the model-facing message listing each matching business and telling it to offer only those options."""
    lines = []
    for index, group in enumerate(groups, start=1):
        item = group["representative"]
        name = item.get("businessname") or business_name
        lines.append(f"{index}. {name} — {_describe_business_location(item)}")
    listing = "; ".join(lines)
    return (
        f"There are {len(groups)} different businesses matching '{business_name}': {listing}. "
        "Ask the user which one they mean and offer EXACTLY these options as written above. "
        "Do not invent any other branch, area, or location."
    )


async def _fetch_all_services_for_contact(contactid: str, service_limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch up to service_limit service rows owned by a contact, ordered by business name and service."""
    query = """
        SELECT providerservicesid, businessname, services, servicedescription,
               pricerange, availability, quantity, servicecategory,
               serviceimageurl, serviceimageurl1, serviceimageurl2, serviceimageurl3,
               providertype, address, city, latitude, longitude, contactid,
               mobilenumber, email, websiteurl, serviceitems
        FROM etown_providerservices_copy
        WHERE LOWER(TRIM(contactid)) = LOWER(TRIM($1))
        ORDER BY businessname, services
        LIMIT $2
    """
    rows = await Database.fetch(query, contactid, service_limit)
    return [dict(row) for row in rows]


async def _fetch_rating_summary(userprofileid: Optional[str], contactid: Optional[str], businessname: Optional[str]) -> Dict[str, Any]:
    """Fetch the latest etown_ratings row by userprofile, then contact, then business name, or an empty dict."""
    if userprofileid:
        row = await Database.fetchrow(
            """
            SELECT overallrating, ratingcount, provideruserprofileid, providercontactid,
                   providerbusinessname, memberbookingcount, createdon
            FROM etown_ratings
            WHERE LOWER(TRIM(provideruserprofileid)) = LOWER(TRIM($1))
            ORDER BY createdon DESC NULLS LAST
            LIMIT 1
            """,
            userprofileid,
        )
        if row:
            return dict(row)

    if contactid:
        row = await Database.fetchrow(
            """
            SELECT overallrating, ratingcount, provideruserprofileid, providercontactid,
                   providerbusinessname, memberbookingcount, createdon
            FROM etown_ratings
            WHERE LOWER(TRIM(providercontactid)) = LOWER(TRIM($1))
            ORDER BY createdon DESC NULLS LAST
            LIMIT 1
            """,
            contactid,
        )
        if row:
            return dict(row)

    if businessname:
        row = await Database.fetchrow(
            """
            SELECT overallrating, ratingcount, provideruserprofileid, providercontactid,
                   providerbusinessname, memberbookingcount, createdon
            FROM etown_ratings
            WHERE LOWER(TRIM(providerbusinessname)) = LOWER(TRIM($1))
            ORDER BY createdon DESC NULLS LAST
            LIMIT 1
            """,
            businessname,
        )
        if row:
            return dict(row)

    return {}


async def _build_selected_providers_data(services: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build the selected_providers payload for service rows, with plan-derived provider type labels."""
    badge_map = await resolve_provider_badges(item.get("userprofileid") for item in services)
    selected_providers_data: List[Dict[str, Any]] = []
    for item in services:
        selected_providers_data.append(
            {
                "providerservicesid": _na(item.get("providerservicesid")),
                "businessname": _na(item.get("businessname")),
                "services": _na(item.get("services")),
                "address": _na(item.get("address")),
                "city": _na(item.get("city")),
                "latitude": _na(item.get("latitude")),
                "longitude": _na(item.get("longitude")),
                "Etown_providertype": _na(premium_display(
                    item.get("providertype"),
                    (badge_map.get(item.get("userprofileid")) or {}).get("premium_label"),
                )),
                "mobilenumber": _na(item.get("mobilenumber")),
                "email": _na(item.get("email")),
                "contactid": _na(item.get("contactid")),
                "websiteurl": _na(item.get("websiteurl")),
            }
        )
    return selected_providers_data


def _compute_distance_km(
    user_latitude: Optional[float],
    user_longitude: Optional[float],
    provider_latitude: Any,
    provider_longitude: Any,
) -> Optional[float]:
    """Return the geodesic user-to-provider distance in km rounded to 2 places, or None if coordinates are unusable."""
    if user_latitude is None or user_longitude is None:
        return None
    if provider_latitude in (None, "", "not available") or provider_longitude in (None, "", "not available"):
        return None
    try:
        distance_km = geodesic(
            (float(user_latitude), float(user_longitude)),
            (float(provider_latitude), float(provider_longitude)),
        ).km
        return round(distance_km, 2)
    except Exception:
        logger.debug("provider insights: distance could not be computed from the supplied coordinates", exc_info=True)
        return None

def _add_detail_line(parts: List[str], label: str, value: Any) -> None:
    """Append 'label value' to parts when the value is truthy and not 'not available'."""
    if value and value != "not available":
        parts.append(f"{label} {value}")


def _extract_review_snippet(review: Any, max_chars: int = 200) -> Optional[str]:
    """Build an 'Author rating: "text"' snippet from a Google review, truncated to max_chars, or None without text."""
    if not isinstance(review, dict):
        return None
    text_field = review.get("text")
    if isinstance(text_field, dict):
        text = text_field.get("text")
    else:
        text = text_field
    if not text or not str(text).strip():
        text_field = review.get("originalText")
        if isinstance(text_field, dict):
            text = text_field.get("text")
        else:
            text = text_field
    if not text or not str(text).strip():
        return None
    text = " ".join(str(text).split())
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"

    author = (review.get("authorAttribution") or {}).get("displayName")
    rating = review.get("rating")
    prefix_bits = []
    if author:
        prefix_bits.append(str(author))
    if rating:
        prefix_bits.append(f"{rating}★")
    prefix = f"{' '.join(prefix_bits)}: " if prefix_bits else ""
    return f"{prefix}\"{text}\""


def _build_details_summary(details: Dict[str, Any]) -> List[str]:
    """Return sentences for weekday opening hours, up to two review snippets and the Google Maps link."""
    if not isinstance(details, dict) or not details:
        return []

    out: List[str] = []

    opening_hours = details.get("opening_hours") or {}
    if isinstance(opening_hours, dict):
        weekday = opening_hours.get("weekdayDescriptions")
        if isinstance(weekday, list) and weekday:
            out.append("Opening hours — " + "; ".join(str(w) for w in weekday) + ".")

    reviews = details.get("reviews")
    if isinstance(reviews, list) and reviews:
        snippets = []
        for review in reviews[:2]:
            snippet = _extract_review_snippet(review)
            if snippet:
                snippets.append(snippet)
        if snippets:
            out.append("Recent reviews: " + " | ".join(snippets))

    maps_uri = details.get("google_maps_uri")
    if maps_uri:
        out.append(f"Google Maps: {maps_uri}")

    return out

def _build_natural_success_response(implicit_output: Dict[str, Any]) -> str:
    """Build the model-facing provider summary: services, price, rating, offers, hours, contact, distance and reviews."""
    provider_profile = implicit_output.get("provider_profile", {})
    ratings = implicit_output.get("ratings_summary", {})
    highlighted = implicit_output.get("highlighted_service", {})
    services = implicit_output.get("services", [])
    details = implicit_output.get("details") or {}

    businessname = provider_profile.get("businessname", "this provider")
    service_count = len(services)
    noun = "service item" if service_count == 1 else "service items"

    sentence_parts: List[str] = [
        f'I found details for "{businessname}" offering {service_count} {noun}.'
    ]

    top_service = highlighted.get("services")
    if top_service and top_service != "not available":
        sentence_parts.append(f'Their highlighted offering is "{top_service}".')

    detail_parts: List[str] = []
    _add_detail_line(detail_parts, "Price range:", highlighted.get("pricerange"))
    _add_detail_line(detail_parts, "Availability:", highlighted.get("availability"))

    overall = ratings.get("overallrating")
    count = ratings.get("ratingcount")
    if overall and overall != "not available":
        if count and count != "not available":
            detail_parts.append(f"Rating: {overall} ({count} reviews)")
        else:
            detail_parts.append(f"Rating: {overall}")
    else:
        g_rating = details.get("google_rating")
        g_count = details.get("google_rating_count")
        if g_rating:
            if g_count:
                detail_parts.append(f"Google rating: {g_rating} ({g_count} reviews)")
            else:
                detail_parts.append(f"Google rating: {g_rating}")

    if detail_parts:
        sentence_parts.append(" ".join(detail_parts) + ".")

    offer_descriptions = [
        str(offer.get("Etown_offerdescription"))
        for offer in (implicit_output.get("offers") or [])[:3]
        if offer.get("Etown_offerdescription") and offer.get("Etown_offerdescription") != "null"
    ]
    if offer_descriptions:
        sentence_parts.append("Active offers: " + "; ".join(offer_descriptions) + ".")

    opening_hours = details.get("opening_hours") or {}
    if isinstance(opening_hours, dict) and isinstance(opening_hours.get("openNow"), bool):
        sentence_parts.append("They are open now." if opening_hours["openNow"] else "They are currently closed.")

    business_status = details.get("business_status")
    if business_status == "CLOSED_TEMPORARILY":
        sentence_parts.append("Note: this business is temporarily closed.")
    elif business_status == "CLOSED_PERMANENTLY":
        sentence_parts.append("Note: this business is permanently closed.")

    contact_parts: List[str] = []
    _add_detail_line(contact_parts, "Phone:", provider_profile.get("mobilenumber"))
    _add_detail_line(contact_parts, "Website:", provider_profile.get("websiteurl") or details.get("website_uri"))
    if contact_parts:
        sentence_parts.append(" ".join(contact_parts) + ".")

    distance_km = implicit_output.get("distance_km")
    if isinstance(distance_km, (int, float)):
        sentence_parts.append(f"It is approximately {distance_km} km from your location.")

    sentence_parts.extend(_build_details_summary(details))

    sentence_parts.append(
        "Let me know if you'd like to see the specifics of their offerings or proceed with an order."
    )
    return " ".join(sentence_parts)

def _build_enquiry_opener_brief(implicit_output: Dict[str, Any]) -> str:
    """Build the model-facing enquiry-opener brief with grounding facts, real offerings and instructions to ask what to send."""
    profile = implicit_output.get("provider_profile", {})
    highlighted = implicit_output.get("highlighted_service", {})
    ratings = implicit_output.get("ratings_summary", {})
    details = implicit_output.get("details") or {}
    services = implicit_output.get("services") or []

    businessname = profile.get("businessname") or "this provider"
    normalized_name = _normalize_text(businessname)

    facts: List[str] = []
    category = highlighted.get("servicecategory")
    if not category or category == "not available":
        category = profile.get("businesscategory")
    if category and category != "not available":
        facts.append(f"what they are: {category}")
    for field in ("areaname", "city"):
        value = profile.get(field)
        if value and value != "not available":
            facts.append(f"area: {value}")
            break
    overall = ratings.get("overallrating")
    if overall and overall != "not available":
        count = ratings.get("ratingcount")
        facts.append(f"rating: {overall}" + (f" ({count} reviews)" if count and count != "not available" else ""))
    elif details.get("google_rating"):
        facts.append(f"rating: {details['google_rating']}")
    price = highlighted.get("pricerange")
    if price and price != "not available":
        facts.append(f"price range: {price}")
    opening_hours = details.get("opening_hours") or {}
    if isinstance(opening_hours, dict) and isinstance(opening_hours.get("openNow"), bool):
        facts.append("open now" if opening_hours["openNow"] else "currently closed")

    labels: List[str] = []
    for service in services:
        raw_groups = service.get("serviceitems")
        groups = raw_groups if isinstance(raw_groups, list) else ([raw_groups] if isinstance(raw_groups, dict) else [])
        for group in groups:
            if not isinstance(group, dict):
                continue
            items = group.get("items")
            candidates = items if isinstance(items, list) else [group]
            for item in candidates:
                name = item.get("item_name") if isinstance(item, dict) else None
                if name and _normalize_text(name) != normalized_name and name not in labels:
                    labels.append(str(name))
    if not labels:
        for service in services:
            name = service.get("services")
            if name and name != "not available" and _normalize_text(name) != normalized_name and name not in labels:
                labels.append(str(name))
    labels = labels[:6]

    parts = [
        f"ENQUIRY OPENER for {businessname} — the member has NOT said what they want yet, so there is nothing "
        "to send and nothing to answer. Do NOT call enquire_provider on this turn.",
        f"Facts you may use, and nothing beyond them: {'; '.join(facts) if facts else 'none listed'}.",
    ]
    if labels:
        parts.append(f"Their real listed offerings: {'; '.join(labels)}.")
    else:
        parts.append("They list no usable offerings, so ask openly and give NO example.")
    parts.append(
        f"Write ONE short freshly-worded message: a single grounding sentence about {businessname} drawn from "
        "those facts, that you can send their enquiry to it, then ask what they are looking for"
        + (", ending with exactly ONE example requirement built from one of those offerings — one example, "
           "never two joined by 'or'." if labels else ".")
    )
    parts.append(
        "Leave OUT the distance, phone number, website, opening hours, review text, the list of services, and any "
        "closing line that repeats the ask. Then wait for the member's next message — that is what gets sent."
    )
    resolved_reference = (implicit_output.get("provider_reference") or {}).get("providerservicesid")
    if resolved_reference and resolved_reference != "not available":
        parts.append(
            f"When they reply, send it with enquire_provider using provider_identifier=\"{resolved_reference}\" "
            "— copy that id exactly from this line and never invent one."
        )
    return " ".join(parts)


async def handle_provider_service_insights(
    providerservicesid: str = "",
    business_name: str = "",
    match_limit: int = 5,
    user_latitude: Optional[float] = None,
    user_longitude: Optional[float] = None,
    state: Optional[dict] = None,
    purpose: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """Resolve a provider by id, business name or state and return its details, or a disambiguation list.

    Args:
        providerservicesid: Explicit service row id; takes priority.
        business_name: Name to look up when no id is given.
        match_limit: Maximum businesses offered when the name is ambiguous.
        user_latitude: User latitude for the distance calculation.
        user_longitude: User longitude for the distance calculation.
        state: Agent state; its single selected provider is the last-resort reference.
        purpose: 'enquiry' returns the enquiry-opener brief instead of the full summary.

    Returns:
        Tuple of (model-facing text, implicit output dict with a status field).
    """
    try:
        resolved_id = (providerservicesid or "").strip()
        resolved_by = "providerservicesid"

        if not resolved_id and business_name.strip():
            matches = await _lookup_business_matches(business_name, match_limit)
            if not matches:
                return (
                    f"No providers found for '{business_name}'.",
                    {
                        "status": "not_found",
                        "message": f"No providers found for '{business_name}'.",
                        "matches": [],
                        "selected_providers_data": [],
                    },
                )

            groups = _group_matches_by_business(matches, business_name)
            if not groups:
                return (
                    f"No providers found for '{business_name}'.",
                    {
                        "status": "not_found",
                        "message": f"No providers found for '{business_name}'.",
                        "matches": [],
                        "selected_providers_data": [],
                    },
                )

            if len(groups) > 1:
                offered = groups[:match_limit]
                return (
                    _build_selection_message(business_name, offered),
                    {
                        "status": "requires_selection",
                        "message": "Multiple provider matches found.",
                        "selection_options": _build_selection_options(offered),
                        "selected_providers_data": await _build_selected_providers_data(
                            [group["representative"] for group in offered]
                        ),
                    },
                )

            resolved_id = str(groups[0]["representative"].get("providerservicesid", "")).strip()
            resolved_by = "business_name"

        if not resolved_id:
            resolved_id = await _resolve_from_state(state)
            resolved_by = "state"

        if not resolved_id:
            return (
                "I need a specific provider reference. Please share the provider ID or business name from the previous results.",
                {
                    "status": "needs_reference",
                    "message": "Missing provider reference. Provide providerservicesid or business_name.",
                    "selected_providers_data": [],
                },
            )

        provider_row = await _fetch_provider_row_by_id(resolved_id)
        if not provider_row:
            return (
                "I could not find that provider in the database.",
                {
                    "status": "not_found",
                    "message": "Provider not found for the supplied identifier.",
                    "resolved_id": resolved_id,
                    "selected_providers_data": [],
                },
            )

        contactid = provider_row.get("contactid")
        businessname = provider_row.get("businessname")
        userprofileid = provider_row.get("userprofileid")
        premium_label = (
            (await resolve_provider_badges([userprofileid])).get(userprofileid) or {}
        ).get("premium_label")

        all_services = await _fetch_all_services_for_contact(contactid) if contactid else [provider_row]
        ratings = await _fetch_rating_summary(userprofileid, contactid, businessname)
        provider_details = await _fetch_provider_details(provider_row.get("providerid"))

        offers_payload: List[Dict[str, Any]] = []
        if userprofileid:
            try:
                offers_payload = [
                    offer
                    for offer in await fetch_valid_and_pending_offers(str(userprofileid))
                    if str(offer.get("Etown_validity") or "").strip().lower() != "expired"
                ]
            except Exception as exc:
                logger.warning("Error fetching offers for userprofileid %s: %s", userprofileid, exc)
        distance_km = _compute_distance_km(
            user_latitude=user_latitude,
            user_longitude=user_longitude,
            provider_latitude=provider_row.get("latitude"),
            provider_longitude=provider_row.get("longitude"),
        )

        services_payload = []
        highlighted_service = None
        for service in all_services:
            item = {
                "providerservicesid": _na(service.get("providerservicesid")),
                "services": _na(service.get("services")),
                "servicecategory": _na(service.get("servicecategory")),
                "servicedescription": _na(service.get("servicedescription")),
                "pricerange": _na(service.get("pricerange")),
                "availability": _na(service.get("availability")),
                "quantity": _na(service.get("quantity")),
                "serviceimageurl": _na(service.get("serviceimageurl")),
                "serviceimageurl1": _na(service.get("serviceimageurl1")),
                "serviceimageurl2": _na(service.get("serviceimageurl2")),
                "serviceimageurl3": _na(service.get("serviceimageurl3")),
                "serviceitems": _parse_jsonb(service.get("serviceitems")),
            }
            services_payload.append(item)
            if str(service.get("providerservicesid", "")).strip() == resolved_id:
                highlighted_service = item

        if not highlighted_service and services_payload:
            highlighted_service = services_payload[0]

        implicit_output = {
            "status": "success",
            "resolved_by": resolved_by,
            "distance_km": distance_km,
            "distance_meters": int(distance_km * 1000) if isinstance(distance_km, (int, float)) else "not available",
            "provider_reference": {
                "providerservicesid": _na(provider_row.get("providerservicesid")),
                "contactid": _na(contactid),
                "businessname": _na(businessname),
            },
            "provider_profile": {
                "businessname": _na(provider_row.get("businessname")),
                "businesscategory": _na(provider_row.get("businesscategory")),
                "Etown_providertype": _na(premium_display(provider_row.get("providertype"), premium_label)),
                "yearsinbusiness": _na(provider_row.get("yearsinbusiness")),
                "address": _na(provider_row.get("address")),
                "additionaladdressinfo": _na(provider_row.get("additionaladdressinfo")),
                "areaname": _na(provider_row.get("areaname")),
                "city": _na(provider_row.get("city")),
                "statecode": _na(provider_row.get("statecode")),
                "latitude": _na(provider_row.get("latitude")),
                "longitude": _na(provider_row.get("longitude")),
                "contactid": _na(provider_row.get("contactid")),
                "contactidname": _na(provider_row.get("contactidname")),
                "mobilenumber": _na(provider_row.get("mobilenumber")),
                "email": _na(provider_row.get("email")),
                "websiteurl": _na(provider_row.get("websiteurl")),
                "availability": _na(provider_row.get("availability")),
                "pricerange": _na(provider_row.get("pricerange")),
                "publicsearchprovider": _na(provider_row.get("publicsearchprovider")),
            },
            "ratings_summary": {
                "overallrating": _na(ratings.get("overallrating")),
                "ratingcount": _na(ratings.get("ratingcount")),
                "memberbookingcount": _na(ratings.get("memberbookingcount")),
                "source_provideruserprofileid": _na(ratings.get("provideruserprofileid")),
                "source_providercontactid": _na(ratings.get("providercontactid")),
            },
            "highlighted_service": highlighted_service or {},
            "services": services_payload,
            "offers": offers_payload,
            "details": provider_details,
            "selected_providers_data": await _build_selected_providers_data(all_services),
        }

        natural_response = (
            _build_enquiry_opener_brief(implicit_output)
            if (purpose or "").strip().lower() == "enquiry"
            else _build_natural_success_response(implicit_output)
        )
        return (
            natural_response,
            implicit_output,
        )
    except Exception as exc:
        logger.error("Error in handle_provider_service_insights: %s", exc, exc_info=True)
        return (
            "An error occurred while retrieving provider details.",
            {
                "status": "error",
                "message": "Failed to retrieve provider details from database.",
                "selected_providers_data": [],
            },
        )
