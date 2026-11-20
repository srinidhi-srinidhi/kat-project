"""Request Provider Tool - books services and orders products from Chaatie after confirmation.

Member-agent tool handler that resolves each provider item, applies distance, self-booking, eligibility and date guards, submits one request through BookingService.add_booking, and schedules the Dataverse mirror, service lists and support email and SMS in the background.

Features:
    - Provider Item Resolution: UUID, then agent-state lookup, lazily persisting web providers
    - Distance Guardrails: Blocks bookings beyond 25 km; implausible distances ask for an address
    - Self-booking Prevention: Blocks a provider from booking their own listed service
    - Provider Eligibility: Refuses disabled providers and those pending review approval
    - Date Validation: Requires a future, readable service_datetime, resolving relative phrases in IST
    - Multi-item Requests: Validates each provider_item_id, then submits one services[] array
    - Native Booking: BookingService.add_booking, with Dataverse sync and service lists in background
    - Support Alerts: Emails and SMS-alerts the support team about each new booking
    - Performance Timing: Records agent_time_ms / booking_time_ms via record_custom_timing

Classes:
    No classes defined (module uses function-based utilities)

Functions:
    _find_public_web_provider_in_state(): Locate a public web provider in agent state by id
    _materialize_web_provider_from_state(): Persist a web provider from state so it is bookable
    _resolve_provider_item_id(): Resolve a provider id via UUID, state, materialization, exact text
    _get_provider_location_from_db(): Fetch a provider's coordinates, falling back to the legacy table
    _is_today_without_a_time(): True when a request names today but no clock time
    _resolve_relative_datetime(): Resolve relative phrases like "tomorrow at 11 AM" against IST
    _is_product_only(): True when a listing's businesscategory is a product and not a service
    _listing_category(): Read one listing's own 'product'/'service' category from the DB
    handle_request_provider(): Main handler; validates items, applies guards, submits the request
    _submit_provider_request(): Submits via BookingService.add_booking and schedules background work
    _run_post_booking_background_work(): Background Dataverse sync, service lists and support email
    _send_booking_created_email(): Emails and SMS-alerts support with the new booking's details

Dependencies:
    - uuid: Validates/generates provider ids, including the pscopy uuid5 for web providers
    - geopy: Geodesic distance between user and provider coordinates for the service-radius check
    - dateutil.parser: Parses the free-form service_datetime string for the past-date check
    - app.utils.performance: record_custom_timing for agent_time_ms / booking_time_ms instrumentation
    - app.utils.ist_clock: IST wall-clock helpers for date validation
    - app.core.logging: Centralized logging for request operations
    - app.core.config: settings for the support booking email recipients
    - app.db.client: Database operations for provider/location/self-booking/boosted lookups
    - app.etown.plans.plans_utils: premium_display and resolve_provider_badges
    - app.etown.provider.provider_review_utils: review_pending_exclusion eligibility filter
    - app.etown.booking.booking_syncing: insert_allrequest_to_dataverse background mirror
    - app.etown.booking.booking_utils: BookingService native booking path
    - app.etown.notifications.booking_alert_recipients: DB-toggled booking alert SMS recipients and excluded booker mobiles
    - app.services.email_service: send_email for the support booking email
    - app.utils.sms: send_booking_alert_sms for the support booking SMS
    - app.agent_infrastructure.tools.tools_utils.web_search_utils: process_and_insert_web_provider

Updates:
    27-Feb-2026: Updated the module documentation.
    09-Mar-2026: Booking ID continuity with context-aware provider resolution; removed fuzzy fallback; confirmation workflow reworked.
    05-Jun-2026: Added timing instrumentation via record_custom_timing; Dataverse sync and service list moved to background task.
    14-Aug-2026: Lazy web-provider insert forwards internationalphonenumber.
    20-Aug-2026: Verified markers report the paid tier's badge label; implicit_output exposes Etown_providertype.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    01-Sep-2026: Past-date check uses the IST wall clock.
    02-Sep-2026: Bookings against disabled providers are refused.
    03-Sep-2026: Service/product split read from each listing's businesscategory.
    04-Sep-2026: Date and time required for every booking; yearless dates resolve to current IST year; fixed date-only detection.
    08-Sep-2026: Dot-separated minutes parsed; "today" without time asks for one; relative phrasing resolved against IST.
    21-Sep-2026: Bookings against providers with an unapproved etown_provider_review row are refused.
    24-Sep-2026: Support-team booking SMS uses send_booking_alert_sms and honours BOOKING_EMAIL_EXCLUDED_MOBILE_NUMBERS.
    28-Sep-2026: Booking alert SMS recipients and excluded booker mobiles read from notifications tables with env fallback.
"""

import asyncio
import html
import re
import time
import uuid
from typing import Dict, Any, Optional, Tuple, List
from uuid import UUID
from datetime import datetime
from app.utils.performance import record_custom_timing
from app.utils.ist_clock import now_ist_naive, to_ist_naive
from dateutil import parser as dateutil_parser
from geopy.distance import geodesic

from app.core.logging import logger
from app.core.config import settings
from app.db.client import Database
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.etown.provider.provider_review_utils import review_pending_exclusion
from app.etown.booking.booking_syncing import insert_allrequest_to_dataverse
from app.etown.booking.booking_utils import BookingService
from app.etown.notifications.booking_alert_recipients import (
    get_booking_alert_recipients,
    is_booking_alert_excluded,
)
from app.services.email_service import send_email
from app.utils.sms import send_booking_alert_sms

_IMPLAUSIBLE_DISTANCE_KM = 1000.0

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_PART_OF_DAY = {"morning": 9, "noon": 12, "afternoon": 14, "evening": 18, "night": 20}
_RELATIVE_DAY_RE = re.compile(
    r"\b(day\s+after\s+tomorrow|day\s+before\s+yesterday|tomorrow|yesterday|tonight|today"
    r"|this\s+weekend|next\s+weekend"
    r"|(?:next|this|coming|last)\s+(?P<wd1>" + "|".join(_WEEKDAYS) + r")"
    r"|this\s+(?:morning|afternoon|evening|night)"
    r"|\b(?P<wd2>" + "|".join(_WEEKDAYS) + r"))\b",
    re.IGNORECASE,
)
_CLOCK_RE = re.compile(
    r"(?<![\d.:])(?:at\s+)?(?P<h>[01]?\d|2[0-3])(?:[:.](?P<m>[0-5]\d))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)"
    r"|(?<![\d.:])(?:at\s+)(?P<h24>[01]?\d|2[0-3])[:.](?P<m24>[0-5]\d)\b",
    re.IGNORECASE,
)
_PART_OF_DAY_RE = re.compile(r"\b(morning|afternoon|evening|night|noon)\b", re.IGNORECASE)


def _resolve_relative_datetime(text: str, now: datetime) -> Optional[Tuple[datetime, bool]]:
    """Resolve relative day, clock time and part-of-day phrases against now.

    Args:
        text: Free-text date/time such as "Saturday 4 pm" or "this evening".
        now: Current IST datetime to resolve against.

    Returns:
        (resolved datetime, date_only flag), or None when no relative day is named.
    """
    day_match = _RELATIVE_DAY_RE.search(text or "")
    if not day_match:
        return None
    phrase = day_match.group(1).lower()
    phrase = re.sub(r"\s+", " ", phrase)
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)

    weekday_name = day_match.group("wd1") or day_match.group("wd2")
    if weekday_name:
        target = _WEEKDAYS[weekday_name.lower()]
        if phrase.startswith("last"):
            behind = (base.weekday() - target) % 7 or 7
            day = base.fromordinal(base.toordinal() - behind)
        else:
            ahead = (target - base.weekday()) % 7
            if ahead == 0 and phrase.startswith("next"):
                ahead = 7
            day = base.fromordinal(base.toordinal() + ahead)
    elif phrase == "tomorrow":
        day = base.fromordinal(base.toordinal() + 1)
    elif phrase == "yesterday":
        day = base.fromordinal(base.toordinal() - 1)
    elif phrase == "day after tomorrow":
        day = base.fromordinal(base.toordinal() + 2)
    elif phrase == "day before yesterday":
        day = base.fromordinal(base.toordinal() - 2)
    elif phrase in ("today", "tonight", "this morning", "this afternoon", "this evening",
                    "this night"):
        day = base
    elif phrase in ("this weekend", "next weekend"):
        ahead = (5 - base.weekday()) % 7 or (0 if phrase == "this weekend" else 7)
        day = base.fromordinal(base.toordinal() + (ahead + (7 if phrase == "next weekend" and ahead < 7 else 0)))
    else:
        return None

    clock = _CLOCK_RE.search(text)
    if clock:
        if clock.group("h24") is not None:
            hour, minute = int(clock.group("h24")), int(clock.group("m24"))
        else:
            hour, minute = int(clock.group("h")), int(clock.group("m") or 0)
            meridiem = clock.group("ap").replace(".", "").lower()
            if meridiem == "pm" and hour != 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
        return day.replace(hour=hour, minute=minute), False

    part = _PART_OF_DAY_RE.search(text)
    if part:
        return day.replace(hour=_PART_OF_DAY[part.group(1).lower()]), False
    if phrase == "tonight":
        return day.replace(hour=_PART_OF_DAY["night"]), False
    return day, True


def _is_today_without_a_time(requested_dt: datetime, date_only: bool, now: datetime) -> bool:
    """Return True when the request is date-only and falls on today."""
    return date_only and requested_dt.date() == now.date()


def _find_public_web_provider_in_state(
    provider_item_id: str, state: Optional[dict]
) -> Optional[Dict[str, Any]]:
    """Return the public_web provider in state.selected_providers whose providerservicesid or id matches, else None."""
    if not state or not provider_item_id:
        return None
    selected_providers = state.get('selected_providers') or []
    if not isinstance(selected_providers, list):
        return None
    for prov in selected_providers:
        if not isinstance(prov, dict) or prov.get('source') != 'public_web':
            continue
        if str(prov.get('providerservicesid') or prov.get('id') or '') == str(provider_item_id):
            return prov
    return None


async def _materialize_web_provider_from_state(
    provider_item_id: str,
    state: Optional[dict],
    contact_id: Optional[str],
    user_profile_id: Optional[str],
    user_name: Optional[str],
) -> Optional[str]:
    """Insert a public web provider from agent state and return its providerservicesid once the row exists.

    Args:
        provider_item_id: The providerservicesid the agent selected for booking.
        state: Agent state holding selected_providers from the last search.
        contact_id: Booking user's contact id, passed to the web-provider insert.
        user_profile_id: Booking user's profile id, passed to the web-provider insert.
        user_name: Booking user's name, passed to the web-provider insert.

    Returns:
        The uuid5 pscopy providerservicesid when the copy row exists, else None.
    """
    match = _find_public_web_provider_in_state(provider_item_id, state)
    if not match:
        return None
    place_id = match.get('placeid')
    if not place_id:
        return None

    primary = match.get('primarytype')
    if isinstance(primary, list):
        primary = primary[0] if primary else None
    servicedescription = match.get('servicedescription')
    place_data = {
        "id": place_id,
        "displayName": {"text": match.get('businessname')},
        "location": {"latitude": match.get('latitude'), "longitude": match.get('longitude')},
        "nationalPhoneNumber": str(match.get('mobilenumber')) if match.get('mobilenumber') else None,
        "internationalPhoneNumber": match.get('internationalphonenumber'),
        "formattedAddress": match.get('address'),
        "rating": match.get('rating'),
        "userRatingCount": match.get('user_ratings_total'),
        "websiteUri": match.get('websiteurl'),
        "googleMapsUri": match.get('place_uri'),
        "primaryType": primary,
        "editorialSummary": {"text": servicedescription} if servicedescription else None,
    }

    try:
        from app.agent_infrastructure.tools.tools_utils.web_search_utils import (
            process_and_insert_web_provider,
        )
        await process_and_insert_web_provider(
            contact_id or "", user_profile_id or "", user_name or "User", place_data
        )
    except Exception as e:
        logger.error(f"Lazy web-provider insert failed for place_id={place_id}: {e}", exc_info=True)
        return None

    pscopy_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pscopy:{place_id}"))
    try:
        exists = await Database.fetchval(
            "SELECT 1 FROM etown_providerservices_copy WHERE providerservicesid = $1 LIMIT 1",
            pscopy_id,
        )
    except Exception as e:
        logger.error(f"Post-insert verification failed for {pscopy_id}: {e}", exc_info=True)
        return None

    if exists:
        logger.info(f"Materialized public provider '{match.get('businessname')}' -> {pscopy_id}")
        return pscopy_id
    logger.warning(
        f"Public provider '{match.get('businessname')}' still absent from "
        f"etown_providerservices_copy after lazy insert (place_id={place_id})"
    )
    return None


async def _resolve_provider_item_id(
    provider_item_id: str,
    detailed_request: str = "",
    state: Optional[dict] = None,
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None,
    user_name: Optional[str] = None,
) -> str:
    """Resolve an id or name to a providerservicesid via DB id columns, agent state, web-provider insert, or exact text."""
    resolved_id = None
    logger.info(f"Resolving provider item identifier: '{provider_item_id}'")
    
    is_uuid = False
    try:
        UUID(provider_item_id)
        is_uuid = True
    except ValueError:
        logger.debug("request_provider: %r is not a UUID; resolving it as a name", provider_item_id)
        is_uuid = False

    if is_uuid:
        try:
            query = """
                SELECT providerservicesid 
                FROM etown_providerservices_copy 
                WHERE providerservicesid = $1 
                   OR providerid = $1 
                   OR contactid = $1 
                   OR userprofileid = $1
                LIMIT 1
            """
            record = await Database.fetchrow(query, provider_item_id)
            if record:
                resolved_id = str(record['providerservicesid'])
                if resolved_id != provider_item_id:
                     logger.info(f"Resolved input UUID '{provider_item_id}' to Service ID '{resolved_id}'.")
                return resolved_id
            else:
                logger.info(f"UUID not in _copy table, checking etown_providerservices for '{provider_item_id}'")
                query_ps = """
                    SELECT providerservicesid 
                    FROM etown_providerservices 
                    WHERE providerservicesid = $1
                    LIMIT 1
                """
                record_ps = await Database.fetchrow(query_ps, provider_item_id)
                if record_ps:
                    resolved_id = str(record_ps['providerservicesid'])
                    logger.info(f"Resolved UUID '{provider_item_id}' from etown_providerservices.")
                    return resolved_id
                logger.warning(f"Input '{provider_item_id}' is a valid UUID but not found in provider tables.")
                materialized = await _materialize_web_provider_from_state(
                    provider_item_id, state, contact_id, user_profile_id, user_name
                )
                if materialized:
                    return materialized
                if _find_public_web_provider_in_state(provider_item_id, state):
                    logger.warning(
                        f"Public provider '{provider_item_id}' could not be materialized; "
                        f"refusing to return an unbacked id."
                    )
                    return None
        except Exception as e:
            logger.error(f"Error resolving UUID provider ID: {e}", exc_info=True)

    if not resolved_id and state:
        selected_providers = state.get('selected_providers', [])
        if selected_providers and isinstance(selected_providers, list):
            logger.info(f"Checking persistent state with {len(selected_providers)} providers")
            
            for provider in selected_providers:
                if isinstance(provider, dict):
                    provider_service_id = provider.get('providerservicesid') or provider.get('id')
                    if provider_service_id and provider_service_id == provider_item_id:
                        if provider.get('source') == 'public_web':
                            return await _materialize_web_provider_from_state(
                                str(provider_service_id), state, contact_id, user_profile_id, user_name
                            )
                        resolved_id = str(provider_service_id)
                        logger.info(f"Found exact provider ID match in persistent state: {resolved_id}")
                        return resolved_id
                    for sub in provider.get('subservices', []) or []:
                        if isinstance(sub, dict):
                            sub_id = sub.get('id') or sub.get('providerservicesid')
                            if sub_id and sub_id == provider_item_id:
                                resolved_id = str(sub_id)
                                logger.info(f"Found subservice ID match in persistent state: {resolved_id}")
                                return resolved_id
            
            normalized_input = provider_item_id.replace("-", " ").replace("_", " ").strip().lower()
            for provider in selected_providers:
                if isinstance(provider, dict):
                    business_name = str(provider.get('businessname', '')).lower().strip()
                    if business_name and (normalized_input in business_name or business_name in normalized_input):
                        resolved_id = provider.get('providerservicesid') or provider.get('id')
                        if resolved_id:
                            if provider.get('source') == 'public_web':
                                return await _materialize_web_provider_from_state(
                                    str(resolved_id), state, contact_id, user_profile_id, user_name
                                )
                            logger.info(f"Matched '{provider_item_id}' to provider '{business_name}' in state: {resolved_id}")
                            return resolved_id
    
    if not resolved_id:
        normalized_input = provider_item_id.replace("-", " ").replace("_", " ").strip()
        normalized_input = " ".join(normalized_input.split())
        
        if len(normalized_input) < 3:
            logger.warning(f"Input too short for text search: '{normalized_input}'. Refusing fallback recovery.")
            return None
        
        if not is_uuid:
            logger.info(f"Attempting conservative DB lookup with normalized term: '{normalized_input}'")

        try:
            query = """
                SELECT providerservicesid, businessname, services
                FROM etown_providerservices_copy 
                WHERE 
                    (LENGTH(businessname) > 3 AND LOWER(businessname) = $1) OR 
                    (LENGTH(services) > 3 AND LOWER(services) = $1)
                ORDER BY LENGTH(businessname) ASC
                LIMIT 1
            """
            record = await Database.fetchrow(query, normalized_input.lower())
            if record:
                resolved_id = str(record['providerservicesid'])
                logger.info(f"Exact text match found for '{provider_item_id}' -> ID '{resolved_id}' (Match: {record.get('businessname')})")
                return resolved_id
            else:
                logger.warning(f"No exact text match found for '{normalized_input}'. Refusing fuzzy fallback recovery.")
        except Exception as e:
            logger.error(f"Error in conservative text resolution: {e}", exc_info=True)

    if not resolved_id:
        logger.error(f"Failed to resolve provider ID '{provider_item_id}'. No fallback recovery attempted.")
    
    return resolved_id if resolved_id else None

async def _get_provider_location_from_db(provider_id: str) -> Tuple[Optional[float], Optional[float]]:
    """Return a listing's latitude and longitude from the copy table, else etown_providerservices, else (None, None)."""
    def _extract_coords(record) -> Tuple[Optional[float], Optional[float]]:
        try:
            lat = float(record['latitude']) if record['latitude'] else None
            lon = float(record['longitude']) if record['longitude'] else None
            return lat, lon
        except (ValueError, TypeError):
            logger.warning(f"Invalid coordinate format in DB for provider {provider_id}")
            return None, None

    try:
        query = "SELECT latitude, longitude FROM etown_providerservices_copy WHERE providerservicesid = $1"
        record = await Database.fetchrow(query, provider_id)
        if record:
            return _extract_coords(record)
        query_ps = "SELECT latitude, longitude FROM etown_providerservices WHERE providerservicesid = $1"
        record_ps = await Database.fetchrow(query_ps, provider_id)
        if record_ps:
            logger.info(f"Retrieved location from etown_providerservices (fallback) for {provider_id}")
            return _extract_coords(record_ps)

        return None, None
    except Exception as e:
        logger.error(f"Error fetching provider location from DB: {e}", exc_info=True)
        return None, None


_PRODUCT_CATEGORY = "product"


def _is_product_only(business_category: Optional[str]) -> bool:
    """Return True when businesscategory mentions product and does not mention service."""
    cat = str(business_category or "").strip().lower()
    return "product" in cat and "service" not in cat


async def _listing_category(providerservicesid: str) -> Optional[str]:
    """Return 'product' or 'service' for one listing, or None when the row cannot be read."""
    try:
        record = await Database.fetchrow(
            """
            SELECT businesscategory
            FROM etown_providerservices_copy
            WHERE providerservicesid = $1
            LIMIT 1
            """,
            str(providerservicesid),
        )
    except Exception as e:
        logger.debug(f"Could not read businesscategory for {providerservicesid}: {e}")
        return None
    if not record or not str(record.get("businesscategory") or "").strip():
        return None
    return _PRODUCT_CATEGORY if _is_product_only(record.get("businesscategory")) else "service"


async def handle_request_provider(
    contact_id: str,
    user_profile_id: str,
    provider_item_ids: List[str],
    quantities: List[int],
    service_datetime: Optional[str],
    user_address: str,
    detailed_request: str,
    first_name: str,
    provider_type: str = "verified",
    category: str = "service",
    user_latitude: Optional[float] = None,
    user_longitude: Optional[float] = None,
    mobilenumber_accesstoken: Optional[str] = None,
    oauth_token: Optional[str] = None,
    user_role: int = 2,
    distance_confirmed: bool = False,
    state: Optional[dict] = None,
    config: Optional[dict] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Validate the date, each provider and its distance, then submit one booking and build the confirmation.

    Args:
        contact_id: Member's contact ID.
        user_profile_id: Member's profile ID.
        provider_item_ids: Provider service or product item IDs or names to book.
        quantities: Quantity per item; missing entries default to 1.
        service_datetime: Requested date/time; missing, unparseable or past values block the booking.
        user_address: Service address.
        detailed_request: Free-text request details.
        first_name: Member's first name.
        provider_type: 'verified' or 'non_verified', deciding whether distance is checked.
        category: 'service' or 'product'; overridden by the listing's businesscategory.
        user_latitude: Member's latitude for the distance check.
        user_longitude: Member's longitude for the distance check.
        mobilenumber_accesstoken: Mobile number access token passed to the booking.
        oauth_token: OAuth token passed to the booking.
        user_role: 1 for provider, which is refused; 2 for member.
        distance_confirmed: True lets a booking beyond 25 km or implausibly far proceed.
        state: Agent state used to resolve providers from the last search.
        config: Run config; not used.

    Returns:
        Response message and grouped provider outputs, or a message and None when blocked.
    """
    try:
        logger.info(f"Processing provider request for user {user_profile_id}, {len(provider_item_ids)} item(s)")

        if user_role == 1:
            return "Please switch to your member account to proceed with booking.", None

        _missing_datetime = (
            "When would you like the service? Please share your preferred date and time."
        )

        if not (service_datetime and str(service_datetime).strip()):
            logger.info("Blocking booking: service_datetime missing. Asking user for date and time.")
            return _missing_datetime, None

        try:
            _s = str(service_datetime).strip()
            now = now_ist_naive()
            _relative = _resolve_relative_datetime(_s, now)
            if _relative is not None:
                requested_dt, date_only = _relative
                year_missing = False
                logger.info(
                    "service_datetime '%s' is relative; read against IST now %s as %s.",
                    _s, now, requested_dt,
                )
            else:
                requested_dt = dateutil_parser.parse(
                    _s, default=datetime(2000, 1, 1, 0, 0, 0), dayfirst=False)
                requested_alt = dateutil_parser.parse(
                    _s, default=datetime(2001, 1, 1, 1, 2, 3), dayfirst=False)
                date_only = (
                    requested_dt.hour != requested_alt.hour
                    and requested_dt.minute != requested_alt.minute
                    and requested_dt.second != requested_alt.second
                )
                year_missing = requested_dt.year != requested_alt.year
                requested_dt = to_ist_naive(requested_dt)
            if year_missing:
                requested_dt = requested_dt.replace(year=now.year)
                already_gone = (
                    requested_dt.date() < now.date() if date_only else requested_dt < now
                )
                if already_gone:
                    requested_dt = requested_dt.replace(year=now.year + 1)
                logger.info(
                    "service_datetime '%s' carried no year; read as %s.", _s, requested_dt,
                )
            if _is_today_without_a_time(requested_dt, date_only, now):
                logger.info(
                    "service_datetime '%s' names today with no time; asking for one rather "
                    "than booking midnight (IST now %s).", _s, now,
                )
                return (
                    "When would you like the service? Please share your preferred date "
                    "and time.",
                    None,
                )
            is_past = requested_dt.date() < now.date() if date_only else requested_dt < now
            if is_past:
                logger.info(
                    "Blocking booking: service_datetime '%s' resolved to %s, before IST now %s.",
                    _s, requested_dt, now,
                )
                return (
                    f"The requested service date and time ({requested_dt.strftime('%d %b %Y, %I:%M %p')}) "
                    f"has already passed - it is currently {now.strftime('%d %b %Y, %I:%M %p')} IST. "
                    "Please provide a future date and time.",
                    None,
                )
            service_datetime = requested_dt.isoformat()
        except (ValueError, OverflowError) as e:
            logger.warning(
                f"Blocking booking: service_datetime '{service_datetime}' could not be "
                f"parsed, so it is treated as missing: {e}"
            )
            return _missing_datetime, None

        items = [
            (pid, quantities[i] if i < len(quantities) else 1)
            for i, pid in enumerate(provider_item_ids)
        ]

        validated_services: List[Dict[str, str]] = []
        resolved_ids: List[str] = []

        for idx, (pid, qty) in enumerate(items):
            logger.info(f"Validating item {idx + 1}/{len(items)}: '{pid}' qty={qty}")

            resolved_id = await _resolve_provider_item_id(
                pid, detailed_request, state, contact_id, user_profile_id, first_name
            )
            if not resolved_id:
                return (
                    f"Unable to find the provider '{pid}' that you selected. "
                    "Please search for providers again and select one from the results, "
                    "or provide a more specific provider name.",
                    None,
                )

            try:
                self_check_query = """
                    SELECT epc.contactid,
                           epc.userprofileid,
                           COALESCE(ep.disabled, false) AS disabled,
                           NOT (""" + review_pending_exclusion("epc.providerid") + """) AS review_pending
                    FROM etown_providerservices_copy epc
                    LEFT JOIN etown_provider ep ON ep.providerid = epc.providerid
                    WHERE epc.providerservicesid = $1
                    LIMIT 1
                """
                provider_owner = await Database.fetchrow(self_check_query, resolved_id)
                if provider_owner:
                    provider_contact = str(provider_owner.get("contactid") or "")
                    provider_profile = str(provider_owner.get("userprofileid") or "")
                    if (contact_id and provider_contact and provider_contact == str(contact_id)) or (
                        user_profile_id and provider_profile and provider_profile == str(user_profile_id)
                    ):
                        return "You cannot book your own services.", None
                    if provider_owner.get("disabled"):
                        logger.info(
                            "request_provider: refused booking against disabled provider | "
                            "providerservicesid=%s", resolved_id,
                        )
                        return (
                            "That business is not taking bookings at the moment. "
                            "Please search again and pick another provider.",
                            None,
                        )
                    if provider_owner.get("review_pending"):
                        logger.info(
                            "request_provider: refused booking against a provider awaiting "
                            "review | providerservicesid=%s", resolved_id,
                        )
                        return (
                            "That business is not taking bookings at the moment. "
                            "Please search again and pick another provider.",
                            None,
                        )
            except Exception as e:
                logger.warning(f"Self-booking check failed for provider {resolved_id}: {e}")

            logger.info(f"Proceeding with booking for resolved provider ID: {resolved_id}")
            item_category = await _listing_category(resolved_id) or category
            if item_category.lower() != category.lower():
                logger.info(
                    "Corrected provider category from %r to %r for %s (listing businesscategory)",
                    category, item_category, resolved_id,
                )

            db_lat, db_lon = await _get_provider_location_from_db(resolved_id)
            p_lat, p_lon = db_lat, db_lon
            if p_lat is None or p_lon is None:
                logger.warning(f"Could not retrieve provider location from DB for {resolved_id}.")
            else:
                logger.info(f"Retrieved location from DB: {p_lat}, {p_lon}")

            is_boosted = False
            try:
                boosted_query = """
                    SELECT COALESCE(ep.boosted, false) AS boosted
                    FROM etown_providerservices_copy epsc
                    LEFT JOIN etown_provider ep ON ep.providerid = epsc.providerid
                    WHERE epsc.providerservicesid = $1
                    LIMIT 1
                """
                boosted_record = await Database.fetchrow(boosted_query, resolved_id)
                if boosted_record:
                    is_boosted = bool(boosted_record.get('boosted', False))
                    if is_boosted:
                        logger.info(f"Provider {resolved_id} is boosted — skipping distance restriction")
            except Exception as e:
                logger.warning(f"Could not check boosted status for provider {resolved_id}: {e}")
            needs_distance_check = not is_boosted and item_category.lower() != "product" and (
                provider_type.lower() == "non_verified"
                or (provider_type.lower() == "verified" and item_category.lower() == "service")
            )
            if needs_distance_check:
                shown_distance_km = None
                for prov in (state.get('selected_providers') or []) if isinstance(state, dict) else []:
                    if not isinstance(prov, dict):
                        continue
                    prov_ids = {str(prov.get('providerservicesid') or ''), str(prov.get('id') or '')}
                    prov_ids.update(
                        str(sub.get('id') or sub.get('providerservicesid') or '')
                        for sub in (prov.get('subservices') or []) if isinstance(sub, dict)
                    )
                    if str(resolved_id) in prov_ids and isinstance(prov.get('distance_km'), (int, float)):
                        shown_distance_km = float(prov['distance_km'])
                        break

                computed_distance = None
                if p_lat is not None and p_lon is not None and user_latitude is not None and user_longitude is not None:
                    try:
                        computed_distance = geodesic((user_latitude, user_longitude), (p_lat, p_lon)).km
                    except Exception as e:
                        logger.error(f"Distance calculation error: {e}", exc_info=True)
                actual_distance = computed_distance
                if shown_distance_km is not None:
                    if computed_distance is None or abs(computed_distance - shown_distance_km) > max(
                        10.0, shown_distance_km * 0.5
                    ):
                        if computed_distance is not None:
                            logger.warning(
                                "Distance mismatch for provider %s — recomputed %.1f km from the "
                                "request location but the search showed %.1f km; using the shown "
                                "distance.",
                                resolved_id, computed_distance, shown_distance_km,
                            )
                        actual_distance = shown_distance_km

                if actual_distance is None:
                    if p_lat is None or p_lon is None:
                        return "Provider location is missing in the system. Cannot calculate distance to verify service area.", None
                    return "Your location is missing. Cannot calculate distance for service request.", None
                if actual_distance > _IMPLAUSIBLE_DISTANCE_KM and not distance_confirmed:
                    logger.error(
                        "Implausible booking distance for provider %s: %.1f km "
                        "(user=%s,%s provider=%s,%s). Asking the member to confirm the service address.",
                        resolved_id, actual_distance, user_latitude, user_longitude, p_lat, p_lon,
                    )
                    return (
                        "[IMPLAUSIBLE DISTANCE — a location-data problem, not a service-range "
                        "one. Do NOT state the distance. Ask for the address ONCE. When the "
                        "member answers with an address (or says to use their profile address), "
                        "call this tool again with that address AND distance_confirmed=true — "
                        "the stored coordinates will still look wrong, so without that flag the "
                        "booking can never complete. Do NOT ask again.]\n\n"
                        "I could not confirm where this service should be delivered — the "
                        "location on your profile is a long way from this provider. Which "
                        "address should I book this for?",
                        None,
                    )

                if actual_distance > 25 and not distance_confirmed:
                    logger.info(
                        f"Provider {resolved_id} is {actual_distance:.1f} km away "
                        f"(> 25 km). Awaiting user confirmation before booking."
                    )
                    return (
                        f"This provider is {actual_distance:.1f} km away from your location, "
                        f"which is beyond our standard 25 km range. "
                        f"Do you want to confirm your booking with them despite the distance?",
                        None,
                    )
                if actual_distance > 25 and distance_confirmed:
                    logger.info(
                        f"Provider {resolved_id} is {actual_distance:.1f} km away "
                        f"(> 25 km). Proceeding — user confirmed the distance."
                    )

            validated_services.append({"service_id": resolved_id, "quantity": str(qty)})
            resolved_ids.append(resolved_id)

        logger.info(f"All {len(validated_services)} item(s) validated. Submitting request.")

        _agent_time = time.monotonic()
        tracking_id = await _submit_provider_request(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            services=validated_services,
            service_datetime=service_datetime,
            user_address=user_address,
            first_name=first_name,
            detailed_request=detailed_request,
            user_latitude=user_latitude,
            user_longitude=user_longitude,
            mobilenumber_accesstoken=mobilenumber_accesstoken,
            oauth_token=oauth_token,
        )
        _agent_elapsed_ms = (time.monotonic() - _agent_time) * 1000
        record_custom_timing("agent_time_ms", _agent_elapsed_ms)
        logger.info("[TIMING] agent_time=%.1fms", _agent_elapsed_ms)

        _SERVICETYPE_LABEL = {
            "1": "Home Delivery",
            "2": "Store delivery",
            "3": "Both",
            "home delivery": "Home Delivery",
            "store delivery": "Store delivery",
            "both": "Both",
        }
        _SERVICETYPE_MSG = {
            "Home Delivery": (
                "This provider offers **Home Delivery** — they will come to your location. "
                "You can connect with them for further details."
            ),
            "Store delivery": (
                "For this service, you'll need to **visit the provider's location**. "
            ),
            "Both": (
                "This provider offers **both Home Delivery and in-store visits** — "
                "choose whichever works best for you. You can connect with them to confirm."
            ),
        }

        if tracking_id:
            provider_groups: Dict[str, Any] = {}
            for rid, svc in zip(resolved_ids, validated_services):
                qty = svc["quantity"]
                try:
                    query = """
                        SELECT psc.providerid, psc.businessname, psc.services,
                               psc.providertype, psc.serviceimageurl,
                               psc.userprofileid, ep.servicetype
                        FROM etown_providerservices_copy psc
                        LEFT JOIN etown_provider ep ON ep.providerid = psc.providerid
                        WHERE psc.providerservicesid = $1
                        LIMIT 1
                    """
                    row = await Database.fetchrow(query, rid)
                except Exception as e:
                    logger.warning(f"Could not fetch provider details for implicit_output: {e}")
                    row = None

                provider_key = str(row["providerid"]) if row and row.get("providerid") else rid
                service_name = row["services"] if row and row.get("services") else None

                raw_st = (row["servicetype"] or "").strip().lower() if row and row.get("servicetype") else ""
                servicetype_label = _SERVICETYPE_LABEL.get(raw_st) or _SERVICETYPE_LABEL.get(raw_st.split()[0] if raw_st else "")

                if provider_key not in provider_groups:
                    premium_label = None
                    if row and row.get("userprofileid"):
                        premium_label = (
                            (await resolve_provider_badges([row["userprofileid"]]))
                            .get(row["userprofileid"]) or {}
                        ).get("premium_label")
                    provider_groups[provider_key] = {
                        "ets_requestid": tracking_id if tracking_id != "REQUEST_SUBMITTED_NO_ID" else None,
                        "status": "submitted",
                        "businessName": row["businessname"] if row and row.get("businessname") else None,
                        "services": [],
                        "service_image": row["serviceimageurl"] if row and row.get("serviceimageurl") else None,
                        "Etown_providertype": premium_display(
                            row["providertype"] if row and row.get("providertype") else None,
                            premium_label,
                        ),
                        "servicetype": servicetype_label,
                    }

                provider_groups[provider_key]["services"].append({
                    "service": service_name,
                    "quantity": qty,
                })

            implicit_outputs = list(provider_groups.values())

            servicetype_suffix = ""
            for grp in implicit_outputs:
                st_label = grp.get("servicetype")
                if st_label and st_label in _SERVICETYPE_MSG:
                    servicetype_suffix = " " + _SERVICETYPE_MSG[st_label]
                    break

            if tracking_id == "REQUEST_SUBMITTED_NO_ID":
                response = (
                    "Your request has been submitted successfully. "
                    "To know more about the request, visit the request tab."
                    + servicetype_suffix
                )
            else:
                response = (
                    f"Your request has been submitted successfully. Here is your tracking ID: {tracking_id}. "
                    "To know more about the request, visit the request tab."
                    + servicetype_suffix
                )
            return response, implicit_outputs if implicit_outputs else None
        else:
            response = "We are unable to process your request at the moment. Please try again later."
            return response, None

    except Exception as e:
        logger.error(f"Error in handle_request_provider: {str(e)}", exc_info=True)
        response = "We encountered an error while submitting your request. Please try again later."
        return response, None


async def _submit_provider_request(
    contact_id: str,
    user_profile_id: str,
    services: List[Dict[str, str]],
    service_datetime: str,
    user_address: str,
    first_name: str,
    detailed_request: str,
    user_latitude: Optional[float],
    user_longitude: Optional[float],
    mobilenumber_accesstoken: Optional[str],
    oauth_token: Optional[str],
) -> Optional[str]:
    """Create the booking via BookingService, schedule post-booking work, and return its tracking id.

    Args:
        contact_id: Requesting member's contact ID.
        user_profile_id: Requesting member's profile ID.
        services: Dicts with service_id and quantity strings.
        service_datetime: Requested service date/time.
        user_address: Service address.
        first_name: Requesting member's first name.
        detailed_request: Free-text request details.
        user_latitude: Optional member latitude.
        user_longitude: Optional member longitude.
        mobilenumber_accesstoken: Optional mobile-number access token.
        oauth_token: Optional OAuth token for notifications.

    Returns:
        The request id, "REQUEST_SUBMITTED_NO_ID" when only a row id exists, or None.
    """
    try:
        payload = {
            "requestor_Id": str(contact_id) if contact_id else "",
            "requestor_request_status": "Requested",
            "request_Status": "Requested",
            "requestor_Address": str(user_address) if user_address else "",
            "requestor_name": str(first_name) if first_name else "",
            "mobilenumber_accesstoken": str(mobilenumber_accesstoken) if mobilenumber_accesstoken else "",
            "requestor_latitude": str(user_latitude) if user_latitude is not None else "",
            "requestor_longitude": str(user_longitude) if user_longitude is not None else "",
            "request_details": str(detailed_request) if detailed_request else "",
            "Oauth": str(oauth_token) if oauth_token else "",
            "requestor_userprofileid": str(user_profile_id) if user_profile_id else "",
            "requestor_from_date": str(service_datetime) if service_datetime else "",
            "requestor_to_date": str(service_datetime) if service_datetime else "",
            "requestor_service_datetime": str(service_datetime) if service_datetime else "",
            "requestor_role": "2",
            "language_code": "en",
            "services": services
        }

        logger.info("Submitting provider request via BookingService.add_booking")
        _booking_time = time.monotonic()
        booking_result = await BookingService.add_booking(payload)
        _booking_elapsed_ms = (time.monotonic() - _booking_time) * 1000
        record_custom_timing("booking_time_ms", _booking_elapsed_ms)
        logger.info("[TIMING] booking_time=%.1fms", _booking_elapsed_ms)

        if not isinstance(booking_result, dict):
            logger.error("BookingService.add_booking returned unexpected response type")
            return None

        booking_id = booking_result.get("allrequestid")
        if booking_id:
            request_status = payload.get("request_Status") or payload.get("requestor_request_status") or "Requested"
            language_code = payload.get("language_code") or "en"
            asyncio.create_task(
                _run_post_booking_background_work(
                    booking_result=booking_result,
                    services=services,
                    contact_id=contact_id,
                    user_profile_id=user_profile_id,
                    request_status=request_status,
                    oauth_token=oauth_token,
                    language_code=language_code,
                )
            )
            logger.info(
                "Scheduled background post-booking work | booking_id=%s",
                booking_id,
            )

        tracking_id = booking_result.get("requestid") or booking_result.get("ets_requestid")
        if tracking_id:
            logger.info(f"Provider request submitted successfully. Tracking ID: {tracking_id}")
            return tracking_id

        if booking_id:
            logger.info("Provider request submitted successfully, but no tracking ID returned")
            return "REQUEST_SUBMITTED_NO_ID"

        logger.error("Booking submission returned no booking identifiers")
        return None
    except Exception as e:
        logger.error(f"Error submitting provider request: {str(e)}", exc_info=True)
        return None


async def _run_post_booking_background_work(
    booking_result: Dict[str, Any],
    services: List[Dict[str, str]],
    contact_id: str,
    user_profile_id: str,
    request_status: str,
    oauth_token: Optional[str],
    language_code: str,
) -> None:
    """Mirror the booking to Dataverse, create its service list rows, and send the booking alert email and SMS."""
    try:
        booking_id = booking_result.get("allrequestid")
        if not booking_id:
            return

        try:
            await insert_allrequest_to_dataverse(dict(booking_result))
        except Exception as sync_error:
            logger.error(
                f"Dataverse sync failed for allrequest row {booking_id}: {sync_error}",
                exc_info=True,
            )

        if services:
            try:
                await BookingService.create_request_service_lists(
                    booking_id,
                    services,
                    contact_id,
                    user_profile_id,
                    request_status,
                    oauth_token,
                    language_code,
                )
            except Exception as service_error:
                logger.error(
                    f"Failed to create request service lists for booking {booking_id}: {service_error}",
                    exc_info=True,
                )

        await _send_booking_created_email(booking_result, services, contact_id)
    except Exception as bg_error:
        logger.error(
            f"Unexpected background booking task error: {bg_error}",
            exc_info=True,
        )


async def _send_booking_created_email(
    booking_result: Dict[str, Any],
    services: List[Dict[str, str]],
    contact_id: str,
) -> None:
    """Send the booking alert SMS and support email with requestor and provider details, unless the mobile is excluded."""
    try:
        contact_row = await Database.fetchrow(
            "SELECT mobilephone FROM etown_contact WHERE contactid = $1 LIMIT 1",
            contact_id,
        )
        creator_mobile = (
            contact_row.get("mobilephone") if contact_row else None
        ) or booking_result.get("requestorcontactnumber")
        if await is_booking_alert_excluded(creator_mobile):
            logger.info(
                "Booking creation email and SMS skipped for excluded mobile | booking_id=%s",
                booking_result.get("allrequestid"),
            )
            return

        stored_service_rows = await Database.fetch(
            """
            SELECT rsl.*, ec.mobilephone AS provider_mobile
            FROM etown_requestservicelists rsl
            LEFT JOIN etown_contact ec ON ec.contactid = rsl.serviceprovidercontactid
            WHERE rsl.bookingid = $1
            """,
            booking_result.get("allrequestid"),
        )
        provider_row = dict(stored_service_rows[0]) if stored_service_rows else {}
        customer_name = booking_result.get("requestorname") or "A customer"
        provider_name = provider_row.get("servicename") or "A provider"
        provider_mobile = provider_row.get("provider_mobile") or "an unavailable number"
        booking_reference = booking_result.get("requestid") or booking_result.get("allrequestid") or "unknown"
        alert_recipients = await get_booking_alert_recipients()
        if alert_recipients:
            try:
                sent = await send_booking_alert_sms(
                    alert_recipients,
                    booking_ref=booking_reference,
                    businessname=provider_row.get("servicename") or "",
                )
                if not sent:
                    logger.warning(
                        "Booking alert SMS was not accepted | booking_id=%s",
                        booking_result.get("allrequestid"),
                    )
            except Exception as sms_error:
                logger.error(
                    "Failed to send booking alert SMS | booking_id=%s | error=%s",
                    booking_result.get("allrequestid"),
                    sms_error,
                    exc_info=True,
                )
        else:
            logger.warning(
                "Booking alert SMS skipped - no enabled recipients | booking_id=%s",
                booking_result.get("allrequestid"),
            )
        await send_email(
            to=[settings.SUPPORT_EMAIL, *settings.PLAN_PAYMENT_EXTRA_EMAILS],
            subject=f"New Chaatie booking: {booking_reference}",
            body=(
                "<h2>New booking created via Chaatie</h2>"
                f"<p><strong>Booking reference:</strong> {html.escape(str(booking_reference))}</p>"
                "<p>"
                f"<strong>Requestor name:</strong> {html.escape(str(customer_name))}<br>"
                f"<strong>Requestor mobilenumber:</strong> {html.escape(str(creator_mobile or 'an unavailable number'))}<br>"
                f"<strong>Providername:</strong> {html.escape(str(provider_name))}<br>"
                f"<strong>Provider mobilenumber:</strong> {html.escape(str(provider_mobile))}"
                "</p>"
            ),
        )
        logger.info("Booking creation email sent | booking_id=%s", booking_reference)
    except Exception as email_error:
        logger.error(
            "Failed to send booking creation email | booking_id=%s | error=%s",
            booking_result.get("allrequestid"),
            email_error,
            exc_info=True,
        )
