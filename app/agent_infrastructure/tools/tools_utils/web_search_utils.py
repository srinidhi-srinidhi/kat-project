"""Web Search Helpers - Google Places lookups and deduplicated insertion of web providers.

Shared helpers for the search and request tools: GoogleMapsService queries Places through RapidAPI with a native Google fallback, and process_and_insert_web_provider gates results to India, dedups them, geocodes and writes them straight into the provider tables, falling back to Power Automate.

Features:
    - Google Maps Integration: RapidAPI text-search with a native Google Maps API fallback
    - Contact Enrichment: a capped, cached Place Details lookup fills in fallback phone numbers
    - India Gating: is_india_place() drops non-Indian Places results before any further work
    - Direct-DB Insertion: Places results go straight into provider tables; Power Automate on failure
    - Existence Dedup: skips a stored result by place id, business name or mobile, locked per business
    - State Code Mapping: Indian state name to standardized 2-letter code lookup
    - Synchronous Processing: semaphores cap insertion at 2 and background enhancement at 1
    - Location Enrichment: reverse-geocodes coordinates via get_location_details_logic()
    - Orphan Cleanup: cleanup_orphaned_web_provider() removes a partial insert plus its Dataverse mirror
    - Error Handling: Robust error handling for external API failures

Classes:
    GoogleMapsService(): RapidAPI Places text-search with a Google Maps fallback, one result shape

Functions:
    places_category(): The canonical category a Places result belongs to, or None
    informative_place_type(): A readable trade, dropping Google's umbrella types
    normalize_business_name(): Comparison key for same-business checks; mirrors its SQL counterpart
    _acquire_name_lock(): Per-business lock so concurrent inserts cannot race the check
    _web_provider_already_exists(): True when a Places result is already stored
    _fetch_place_contact_details(): Cached Place Details lookup for one place's phone/website
    is_india_place(): True when a Google Places result is an Indian (+91) business
    handle_external_providers_data_insertion(): POSTs a provider to the legacy Power Automate endpoint
    _run_enhancement_guarded(): Runs background enhancements under the enhancement semaphore
    process_and_insert_web_provider(): Dedups, geocodes and inserts one Google Places result
    cleanup_orphaned_web_provider(): Removes a partial web provider's rows plus its Dataverse mirror

Dependencies:
    - requests: HTTP client for the Places calls and the Power Automate insertion POST
    - asyncio: semaphore-gated concurrency and executor calls wrapping blocking requests
    - async_lru.alru_cache: caches Place Details lookups
    - app.core.config: RapidAPI/Google Maps keys and the Power Automate insertion URL
    - app.core.logging: Centralized logging for API operations
    - app.db.client: Existence checks before inserting or cleaning up a web provider
    - app.etown.services.services_utils: get_location_details_logic() reverse geocoding
    - app.etown.provider.provider_utils: ProviderService insert, details upsert and enhancement
    - app.etown.provider.provider_syncing: sync_provider_delete_from_dataverse() for orphan cleanup
    - app.logs.enhanced_logging: api_call_monitor around external calls
    - app.utils.phone: has_india_country_code() and normalize_indian_mobile()

Updates:
    27-Feb-2026: Updated the module documentation.
    09-Jun-2026: Early-exit existence check; added _insertion_semaphore and _enhancement_semaphore around DB work.
    10-Aug-2026: process_and_insert_web_provider returns an outcome dict.
    14-Aug-2026: Added is_india_place() and a 'not_india' early exit before geocoding.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    08-Sep-2026: google_maps fallback enriched via Place Details; web providers deduped by place id, name or mobile.
    09-Oct-2026: RapidAPI key read from settings (dev Key Vault).
"""

import re
import unicodedata
import uuid
import requests
import json
import asyncio
from typing import Dict, Any, List, Optional
from async_lru import alru_cache
from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.services.services_utils import get_location_details_logic
from app.etown.provider.provider_utils import ProviderService
from app.etown.provider.provider_syncing import sync_provider_delete_from_dataverse
from app.logs.enhanced_logging import api_call_monitor
from app.utils.phone import has_india_country_code, normalize_indian_mobile
_NAME_NOISE_RE = re.compile(r"[^a-z0-9]+")

def normalize_business_name(name: Optional[str]) -> str:
    """Return the casefolded latin-alphanumeric dedup key for a business name, "" when none remains."""
    folded = unicodedata.normalize("NFKC", name or "").casefold()
    return _NAME_NOISE_RE.sub(" ", folded).strip()
_NAME_LOCK_MAX_KEYS = 512
_name_locks: Dict[str, asyncio.Lock] = {}
_name_locks_guard = asyncio.Lock()


async def _acquire_name_lock(key: str) -> asyncio.Lock:
    """Return the lock for one business-name key, creating it and pruning idle locks when full."""
    async with _name_locks_guard:
        lock = _name_locks.get(key)
        if lock is None:
            if len(_name_locks) >= _NAME_LOCK_MAX_KEYS:
                for stale_key, stale_lock in list(_name_locks.items()):
                    if not stale_lock.locked():
                        _name_locks.pop(stale_key, None)
            lock = _name_locks[key] = asyncio.Lock()
        return lock

UNINFORMATIVE_PLACE_TYPES = frozenset({
    "point_of_interest", "establishment", "store", "service", "premise", "geocode",
    "political", "food",
})


def informative_place_type(place: Dict[str, Any]) -> Optional[str]:
    """Return a readable trade for a Places result, or None when it only has umbrella types."""
    primary = str((place or {}).get("primaryType") or "").strip().lower()
    if primary and primary not in UNINFORMATIVE_PLACE_TYPES:
        display = str(((place or {}).get("primaryTypeDisplayName") or {}).get("text")
                      or "").strip()
        return display or primary.replace("_", " ").title()
    for candidate in (place or {}).get("types") or []:
        token = str(candidate).strip().lower()
        if token and token not in UNINFORMATIVE_PLACE_TYPES:
            return token.replace("_", " ").title()
    return None


PLACES_TYPE_TO_CATEGORY = {
    "car_repair": "automotive_services", "car_wash": "automotive_services",
    "car_dealer": "automotive_services", "auto_parts_store": "automotive_parts",
    "electrician": "electrical_services", "plumber": "plumbing_services",
    "locksmith": "locksmith_services", "painter": "construction_services",
    "roofing_contractor": "construction_services", "general_contractor": "construction_services",
    "moving_company": "courier_services", "storage": "courier_services",
    "laundry": "cleaning_services", "cleaning_service": "cleaning_services",
    "restaurant": "restaurant", "meal_takeaway": "restaurant",
    "meal_delivery": "restaurant", "fast_food_restaurant": "restaurant",
    "pizza_restaurant": "restaurant", "indian_restaurant": "restaurant",
    "cafe": "cafe", "coffee_shop": "cafe", "tea_house": "cafe",
    "ice_cream_shop": "cafe", "juice_shop": "cafe",
    "bakery": "bakery", "dessert_shop": "bakery", "candy_store": "bakery",
    "catering_service": "catering_services",
    "grocery_store": "grocery_store", "supermarket": "grocery_store",
    "convenience_store": "grocery_store", "wholesaler": "grocery_store",
    "hypermarket": "grocery_store", "market": "grocery_store",
    "electronics_store": "electronics_store", "home_goods_store": "furniture_store",
    "furniture_store": "furniture_store", "hardware_store": "hardware_store",
    "clothing_store": "clothing_store", "womens_clothing_store": "clothing_store",
    "mens_clothing_store": "clothing_store", "shoe_store": "footwear_store",
    "jewelry_store": "jewelry_store", "book_store": "stationery_store",
    "toy_store": "toy_store", "florist": "florist", "pharmacy": "pharmacy",
    "drugstore": "pharmacy", "sporting_goods_store": "sporting_goods",
    "cell_phone_store": "mobile_phones", "bicycle_store": "sporting_goods",
    "beauty_salon": "beauty_services", "hair_salon": "beauty_services",
    "spa": "beauty_services", "barber_shop": "beauty_services",
    "gym": "fitness_services", "fitness_center": "fitness_services",
    "yoga_studio": "fitness_services",
    "hospital": "healthcare_services", "doctor": "healthcare_services",
    "dentist": "healthcare_services", "physiotherapist": "healthcare_services",
    "veterinary_care": "other",
    "school": "education_services", "primary_school": "education_services",
    "secondary_school": "education_services", "university": "education_services",
    "bank": "financial_services", "atm": "financial_services",
    "insurance_agency": "financial_services", "accounting": "accounting_services",
    "lawyer": "legal_services", "real_estate_agency": "real_estate",
    "travel_agency": "travel_services", "taxi_stand": "taxi_service",
    "courier_service": "courier_services", "post_office": "courier_services",
    "photographer": "photography_services", "photography_studio": "photography_services",
    "printing_service": "printing_services", "hotel": "hotel", "lodging": "hotel",
    "hostel": "hostel", "event_venue": "event_management",
    "wedding_venue": "event_management", "optician": "optical_services",
    "watch_repair": "watches_services", "tailor": "clothing_store",
}


def places_category(place: Dict[str, Any]) -> Optional[str]:
    """Return the canonical category for a Places result from primaryType then types, or None."""
    primary = str((place or {}).get("primaryType") or "").strip().lower()
    mapped = PLACES_TYPE_TO_CATEGORY.get(primary)
    if mapped:
        return mapped
    for candidate in (place or {}).get("types") or []:
        mapped = PLACES_TYPE_TO_CATEGORY.get(str(candidate).strip().lower())
        if mapped:
            return mapped
    return None


PLACE_DETAILS_ENDPOINT = "https://maps.googleapis.com/maps/api/place/details/json"
PLACE_DETAILS_FIELDS = "formatted_phone_number,international_phone_number,website"
PLACE_DETAILS_MAX_LOOKUPS = 10
PLACE_DETAILS_CACHE_TTL_SECONDS = 86400
PLACE_DETAILS_TIMEOUT_SECONDS = 8


@alru_cache(maxsize=2048, ttl=PLACE_DETAILS_CACHE_TTL_SECONDS)
async def _fetch_place_contact_details(place_id: str, api_key: str) -> Dict[str, Any]:
    """Return a place's phone and website from Place Details, or {} on any failure."""
    def _get_request():
        return requests.get(
            PLACE_DETAILS_ENDPOINT,
            params={"place_id": place_id, "key": api_key, "fields": PLACE_DETAILS_FIELDS},
            timeout=PLACE_DETAILS_TIMEOUT_SECONDS,
        )

    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, _get_request)
        if response.status_code != 200:
            logger.warning(
                "Place Details HTTP %s for place_id=%s: %s",
                response.status_code, place_id, response.text[:200],
            )
            return {}
        body = response.json()
        if body.get("status") != "OK":
            logger.warning(
                "Place Details status=%s for place_id=%s: %s",
                body.get("status"), place_id, body.get("error_message"),
            )
            return {}
        result = body.get("result") or {}
        return {
            "nationalPhoneNumber": result.get("formatted_phone_number"),
            "internationalPhoneNumber": result.get("international_phone_number"),
            "websiteUri": result.get("website"),
        }
    except Exception as e:
        logger.warning("Place Details lookup failed for place_id=%s: %s", place_id, e)
        return {}


_insertion_semaphore = asyncio.Semaphore(2)

_enhancement_semaphore = asyncio.Semaphore(1)
_web_bg_tasks: set = set()

STATE_CODE_MAP = {
    "Andaman and Nicobar Islands": "AN",
    "Andhra Pradesh": "AP",
    "Arunachal Pradesh": "AR",
    "Assam": "AS",
    "Bihar": "BR",
    "Chandigarh": "CH",
    "Chhattisgarh": "CG",
    "Dadra and Nagar Haveli and Daman and Diu": "DN",
    "Delhi": "DL",
    "Goa": "GA",
    "Gujarat": "GJ",
    "Haryana": "HR",
    "Himachal Pradesh": "HP",
    "Jammu and Kashmir": "JK",
    "Jharkhand": "JH",
    "Karnataka": "KA",
    "Kerala": "KL",
    "Ladakh": "LA",
    "Lakshadweep": "LD",
    "Madhya Pradesh": "MP",
    "Maharashtra": "MH",
    "Manipur": "MN",
    "Meghalaya": "ML",
    "Mizoram": "MZ",
    "Nagaland": "NL",
    "Odisha": "OR",
    "Puducherry": "PY",
    "Punjab": "PB",
    "Rajasthan": "RJ",
    "Sikkim": "SK",
    "Tamil Nadu": "TN",
    "Telangana": "TG",
    "Tripura": "TR",
    "Uttar Pradesh": "UP",
    "Uttarakhand": "UT",
    "West Bengal": "WB"
}


def is_india_place(place: Dict[str, Any]) -> bool:
    """Return True when a Places result has a +91 international number or, lacking one, an Indian address."""
    international_phone = (place or {}).get('internationalPhoneNumber')
    if international_phone:
        return has_india_country_code(international_phone)

    for component in (place or {}).get('addressComponents') or []:
        if 'country' in (component.get('types') or []):
            return (
                (component.get('shortText') or '').strip().upper() == 'IN'
                or (component.get('longText') or '').strip().lower() == 'india'
            )

    formatted_address = ((place or {}).get('formattedAddress') or '').strip().lower()
    return formatted_address.endswith('india')


class GoogleMapsService:
    def __init__(self):
        """Load the RapidAPI and Google Maps keys and endpoints."""
        self.rapidapi_key = settings.rapidapi_key
        self.google_maps_api_key = settings.google_maps_api_key
        self.searchText_endpoint = "https://google-map-places-new-v2.p.rapidapi.com/v1/places:searchText"
        self.api_host = "google-map-places-new-v2.p.rapidapi.com"
        self.google_maps_text_search_endpoint = "https://maps.googleapis.com/maps/api/place/textsearch/json"

    @staticmethod
    def _extract_location_bias(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return the latitude, longitude and radius from a payload's locationBias circle."""
        circle = ((payload or {}).get("locationBias") or {}).get("circle") or {}
        center = circle.get("center") or {}
        return {
            "latitude": center.get("latitude"),
            "longitude": center.get("longitude"),
            "radius": circle.get("radius"),
        }

    @staticmethod
    def _build_google_maps_uri(place_id: Optional[str]) -> Optional[str]:
        """Return a Google Maps URL for a place id, or None when there is no id."""
        if not place_id:
            return None
        return f"https://www.google.com/maps/place/?q=place_id:{place_id}"

    @staticmethod
    def _normalize_google_maps_places(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert legacy Google Maps text-search results into the Places v1 result shape."""
        normalized: List[Dict[str, Any]] = []
        for result in results or []:
            geometry = result.get("geometry") or {}
            location = geometry.get("location") or {}
            place_id = result.get("place_id")
            maps_uri = GoogleMapsService._build_google_maps_uri(place_id)
            place_types = result.get("types") or []
            primary_type = place_types[0] if place_types else None

            normalized.append(
                {
                    "id": place_id,
                    "displayName": {"text": result.get("name")},
                    "formattedAddress": result.get("formatted_address"),
                    "location": {
                        "latitude": location.get("lat"),
                        "longitude": location.get("lng"),
                    },
                    "nationalPhoneNumber": result.get("formatted_phone_number"),
                    "internationalPhoneNumber": result.get("international_phone_number"),
                    "rating": result.get("rating"),
                    "userRatingCount": result.get("user_ratings_total"),
                    "websiteUri": result.get("website"),
                    "googleMapsUri": maps_uri,
                    "primaryType": primary_type,
                    "types": place_types,
                    "regularOpeningHours": result.get("opening_hours"),
                    "editorialSummary": None,
                    "primaryTypeDisplayName": {"text": primary_type.replace("_", " ").title()} if primary_type else None,
                    "reviews": [],
                }
            )
        return normalized

    @api_call_monitor('google_maps', 'place_search')
    async def place_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run a Places text search through RapidAPI and return places plus status and error fields."""
        if not self.rapidapi_key:
            logger.warning("RAPIDAPI_KEY not configured. Skipping RapidAPI search.")
            return {
                "provider": "rapidapi",
                "places": [],
                "status_code": None,
                "error": "missing_rapidapi_key",
            }
            
        headers = {
            "x-rapidapi-key": self.rapidapi_key,
            "x-rapidapi-host": self.api_host,
            "Content-Type": "application/json",
            "X-Goog-FieldMask": "*"
        }
        
        def _post_request():
            return requests.post(self.searchText_endpoint, headers=headers, json=payload, timeout=10)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, _post_request)
            if response.status_code == 200:
                body = response.json()
                return {
                    "provider": "rapidapi",
                    "places": body.get("places", []),
                    "status_code": response.status_code,
                    "raw": body,
                }

            logger.warning(
                "RapidAPI place search failed with status=%s, body=%s",
                response.status_code,
                response.text,
            )
            return {
                "provider": "rapidapi",
                "places": [],
                "status_code": response.status_code,
                "error": "http_error",
                "error_body": response.text,
            }
        except Exception as e:
            logger.warning("RapidAPI place search exception: %s", e)
            return {
                "provider": "rapidapi",
                "places": [],
                "status_code": None,
                "error": "exception",
                "error_body": str(e),
            }

    @api_call_monitor('google_maps', 'native_place_search')
    async def native_place_search(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run a legacy Google Maps text search, normalize the results and fill missing phone numbers."""
        if not self.google_maps_api_key:
            logger.warning("GOOGLE_MAPS_API_KEY not configured. Skipping native Google Maps fallback.")
            return {
                "provider": "google_maps",
                "places": [],
                "status_code": None,
                "error": "missing_google_maps_api_key",
            }

        location_bias = self._extract_location_bias(payload)
        params: Dict[str, Any] = {
            "query": (payload or {}).get("textQuery", ""),
            "key": self.google_maps_api_key,
        }
        if location_bias.get("latitude") is not None and location_bias.get("longitude") is not None:
            params["location"] = f"{location_bias['latitude']},{location_bias['longitude']}"
        if location_bias.get("radius") is not None:
            params["radius"] = int(location_bias["radius"])

        def _get_request():
            return requests.get(self.google_maps_text_search_endpoint, params=params, timeout=8)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, _get_request)
            if response.status_code == 200:
                body = response.json()
                normalized_places = self._normalize_google_maps_places(body.get("results", []))
                normalized_places = await self._attach_contact_details(
                    normalized_places,
                    min(
                        int((payload or {}).get("pageSize") or PLACE_DETAILS_MAX_LOOKUPS),
                        PLACE_DETAILS_MAX_LOOKUPS,
                    ),
                )
                return {
                    "provider": "google_maps",
                    "places": normalized_places,
                    "status_code": response.status_code,
                    "raw": body,
                }

            logger.warning(
                "Google Maps fallback search failed with status=%s, body=%s",
                response.status_code,
                response.text,
            )
            return {
                "provider": "google_maps",
                "places": [],
                "status_code": response.status_code,
                "error": "http_error",
                "error_body": response.text,
            }
        except Exception as e:
            logger.warning("Google Maps fallback search exception: %s", e)
            return {
                "provider": "google_maps",
                "places": [],
                "status_code": None,
                "error": "exception",
                "error_body": str(e),
            }

    async def _attach_contact_details(
        self, places: List[Dict[str, Any]], max_lookups: int
    ) -> List[Dict[str, Any]]:
        """Fill phone and website fields via Place Details for up to max_lookups places lacking a phone."""
        if not self.google_maps_api_key or not places:
            return places

        needing = [p for p in places if p.get("id") and not p.get("nationalPhoneNumber")]
        needing = needing[: max(0, max_lookups)]
        if not needing:
            return places

        details = await asyncio.gather(
            *(
                _fetch_place_contact_details(place["id"], self.google_maps_api_key)
                for place in needing
            ),
            return_exceptions=True,
        )

        filled = 0
        for place, detail in zip(needing, details):
            if isinstance(detail, BaseException) or not isinstance(detail, dict):
                continue
            for field, value in detail.items():
                if value and not place.get(field):
                    place[field] = value
            if place.get("nationalPhoneNumber"):
                filled += 1

        logger.info(
            "Place Details supplied a phone number for %s of %s fallback place(s)",
            filled, len(needing),
        )
        return places

    @staticmethod
    def _should_fallback(rapidapi_response: Dict[str, Any]) -> bool:
        """Return True when a RapidAPI response has no places and an error or non-200 status."""
        places = (rapidapi_response or {}).get("places") or []
        if places:
            return False

        status_code = rapidapi_response.get("status_code")
        error = rapidapi_response.get("error")
        return bool(error) or status_code != 200

    async def place_search_with_fallback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Search via RapidAPI, falling back to native Google Maps when it yields no usable places."""
        rapidapi_response = await self.place_search(payload)
        if not self._should_fallback(rapidapi_response):
            rapidapi_response["fallback_used"] = False
            rapidapi_response["effective_provider"] = "rapidapi"
            return rapidapi_response

        logger.warning(
            "RapidAPI search returned no usable places (status=%s, error=%s). Triggering Google Maps fallback.",
            rapidapi_response.get("status_code"),
            rapidapi_response.get("error"),
        )
        fallback_response = await self.native_place_search(payload)
        fallback_response["fallback_used"] = True
        fallback_response["effective_provider"] = "google_maps"
        fallback_response["rapidapi_failure"] = {
            "status_code": rapidapi_response.get("status_code"),
            "error": rapidapi_response.get("error"),
            "error_body": rapidapi_response.get("error_body"),
        }
        return fallback_response
        
google_map_service = GoogleMapsService()

async def _web_provider_already_exists(
    place_id: Optional[str],
    business_name: Optional[str],
    name_key: str,
    phone_key: Optional[str] = None,
) -> bool:
    """Return True when a provider already exists by place_id-derived id, name key or mobile; False on errors."""
    if place_id:
        try:
            if await Database.fetchval(
                """SELECT 1 FROM etown_provider WHERE providerid = $1
                   UNION ALL
                   SELECT 1 FROM etown_providerservices_copy WHERE providerservicesid = $2
                   LIMIT 1""",
                str(uuid.uuid5(uuid.NAMESPACE_DNS, f"provider:{place_id}")),
                str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pscopy:{place_id}")),
            ):
                logger.debug(
                    "[web_provider_insert] Provider already exists, skipping insert for place_id=%s",
                    place_id,
                )
                return True
        except Exception as e:
            logger.warning(
                "[web_provider_insert] Existence check failed for place_id=%s: %s", place_id, e
            )

    if name_key:
        try:
            if await Database.fetchval(
                "SELECT 1 FROM etown_provider "
                "WHERE btrim(regexp_replace(lower(businessname), '[^a-z0-9]+', ' ', 'g')) = $1 "
                "LIMIT 1",
                name_key,
            ):
                logger.debug(
                    "[web_provider_insert] Provider already exists by name '%s' (key=%r), "
                    "skipping insert for place_id=%s", business_name, name_key, place_id,
                )
                return True
        except Exception as e:
            logger.warning(
                "[web_provider_insert] Name existence check failed for '%s': %s",
                business_name, e,
            )

    if phone_key:
        try:
            if await Database.fetchval(
                "SELECT 1 FROM etown_provider "
                "WHERE right(regexp_replace(coalesce(mobilenumber, ''), '[^0-9]', '', 'g'), 10)"
                " = $1 LIMIT 1",
                phone_key,
            ):
                logger.debug(
                    "[web_provider_insert] Provider already exists by mobile %s ('%s'), "
                    "skipping insert for place_id=%s", phone_key, business_name, place_id,
                )
                return True
        except Exception as e:
            logger.warning(
                "[web_provider_insert] Phone existence check failed for '%s': %s",
                business_name, e,
            )
    return False


async def handle_external_providers_data_insertion(
    contact_id: str, 
    user_profile_id: str, 
    user_name: str, 
    business_name: str, 
    mobile_number: int = None, 
    address: str = None, 
    latitude: str = None, 
    longitude: str = None, 
    city: str = None, 
    areaname: str = None, 
    state: str = None, 
    directionsUri: str = None, 
    placeUri: str = None, 
    photosUri: str = None, 
    place_id: str = None, 
    deliveryOptions: list = None, 
    paymentOptions: list = None, 
    primaryType: str = None, 
    rating: float = None, 
    currentOpeningHours: dict = None, 
    reviews: list = None,
    servicedescription: str = None,
) -> Optional[Dict[str, Any]]:
    """POST a scraped provider to the Power Automate insertion flow and return its JSON reply, or None on failure."""
    async with _insertion_semaphore:
        url = settings.external_providers_data_insertion_url
        if not url:
            logger.error("External providers data insertion URL is not configured.")
            return None

        def safe_str(val):
            return str(val) if val is not None else ""

        for sep in ['-', '|','(',',','[']:
            if sep in business_name:
                business_name = business_name.split(sep)[0].strip()
        payload = {
            "etown_link": "https://etownapp.page.link",
            "inviter": contact_id,
            "InviterUserProfileId": user_profile_id,
            "name": user_name,
            "businessname": business_name,
            "formattedAddress": safe_str(address),
            "latitude": safe_str(latitude),
            "longitude": safe_str(longitude),
            "city": safe_str(city),
            "areaname": safe_str(areaname),
            "state": safe_str(state),
            "directionsUri": safe_str(directionsUri),
            "placeUri": safe_str(placeUri),
            "photosUri": safe_str(photosUri),
            "place_id": safe_str(place_id),
            "deliveryOptions": str(deliveryOptions),
            "paymentOptions": str(paymentOptions),
            "primaryType": safe_str(primaryType),
            "rating": str(rating),
            "currentOpeningHours": str(currentOpeningHours),
            "reviews": str(reviews),
            "servicedescription": safe_str(servicedescription),
            "providertype": "etown_nonverified",
            "publicsearchprovider": "non-verified",
        }

        if mobile_number:
            payload["mobilenumber"] = mobile_number

        headers = {
            'Content-Type': 'application/json',
            'accessKey': settings.powerapps_accesskey
        }

        def _post_request():
            return requests.post(url, headers=headers, json=payload, timeout=8)

        def _post_request_without_service_description():
            fallback_payload = dict(payload)
            fallback_payload.pop("servicedescription", None)
            return requests.post(url, headers=headers, json=fallback_payload, timeout=8)

        try:
            logger.info(f"Processing external provider data insertion to {url} for business: {business_name}")
            loop = asyncio.get_running_loop()
            
            response = await loop.run_in_executor(None, _post_request)
            if response.status_code != 200:
                logger.warning(
                    "Insertion failed with servicedescription for business: %s (status=%s). Retrying without servicedescription.",
                    business_name,
                    response.status_code,
                )
                response = await loop.run_in_executor(None, _post_request_without_service_description)
            
            if response.status_code == 200:
                logger.info(f"External provider data inserted successfully for business: {business_name}")
                try:
                    result = response.json()
                    logger.info(f"API acknowledged insertion for business: {business_name}")
                    return result
                except requests.exceptions.JSONDecodeError:
                    logger.info(f"API acknowledged insertion for business: {business_name} (Empty Response)")
                    return {"status": "success", "message": "Inserted successfully (Empty Response)"}
            else:
                logger.error(f"Data insertion failed for business: {business_name} - Status {response.status_code}: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Exception in external_providers_data_insertion for business: {business_name} - {str(e)}", exc_info=True)
            return None

async def _run_enhancement_guarded(**kwargs):
    """Run provider background enhancements one at a time under the enhancement semaphore."""
    async with _enhancement_semaphore:
        await ProviderService.run_background_enhancements(**kwargs)

async def process_and_insert_web_provider(
    contact_id: str, 
    user_profile_id: str, 
    user_name: str, 
    place_data: Dict[str, Any]
):
    """Geocode, dedupe and insert one Places result as a provider, falling back to Power Automate.

    Returns:
        Dict whose status is exists, not_india, no_city, no_phone or inserted.
    """
    p = place_data
    _place_id_early = p.get('id')
    _business_name_early = (p.get('displayName') or {}).get('text')
    _name_key = normalize_business_name(_business_name_early)
    _phone_key = normalize_indian_mobile(
        p.get('nationalPhoneNumber') or p.get('internationalPhoneNumber')
    )

    if not is_india_place(p):
        logger.info(
            "[web_provider_insert] Skipping non-India provider '%s' (place_id=%s, phone=%s)",
            (p.get('displayName') or {}).get('text'), _place_id_early,
            p.get('internationalPhoneNumber') or p.get('nationalPhoneNumber'),
        )
        return {"status": "not_india", "place_id": _place_id_early}

    _lock_key = _phone_key or _name_key
    _insert_lock = await _acquire_name_lock(_lock_key) if _lock_key else None
    if _insert_lock:
        await _insert_lock.acquire()
    try:
        if await _web_provider_already_exists(
            _place_id_early, _business_name_early, _name_key, _phone_key
        ):
            return {"status": "exists", "place_id": _place_id_early}

        lat = p.get('location', {}).get('latitude')
        lon = p.get('location', {}).get('longitude')
    
        city = None
        areaname = None
        state_full = None
        state_code = None

        if lat and lon:
            try:
                loc_details = await get_location_details_logic(float(lat), float(lon))
                comps = loc_details.parsed_components
            
                city = comps.get("locality")
            
                areaname = comps.get("sublocality_level_1")
                if not areaname:
                    areaname = comps.get("sublocality_level_2")
                if not areaname:
                    areaname = comps.get("sublocality_level_3")
                if not areaname:
                    areaname = comps.get("locality")

                state_full = comps.get("administrative_area_level_1")
            
            except Exception as e:
                logger.error(f"Geo-enrichment failed for web provider '{p.get('displayName', {}).get('text')}': {e}", exc_info=True)

        if not city:
            logger.warning(
                "Skipping insertion for web provider '%s' (place_id=%s) - city information not available",
                (p.get('displayName') or {}).get('text'), _place_id_early,
            )
            return {"status": "no_city", "place_id": _place_id_early}

        if state_full:
            state_code = STATE_CODE_MAP.get(state_full.strip(), state_full)
    
        raw_mobile = p.get('nationalPhoneNumber', '')
        mobile_number = int(''.join(filter(str.isdigit, raw_mobile))) if raw_mobile else None
    
        if not mobile_number:
            logger.warning(
                "Skipping insertion for web provider '%s' (place_id=%s) - mobile number not available",
                (p.get('displayName') or {}).get('text'), _place_id_early,
            )
            return {"status": "no_phone", "place_id": _place_id_early}
    
        payment_options = p.get('paymentOptions', {})
        payment_list = [k for k, v in payment_options.items() if v] if payment_options else []
    
        delivery_list = []
        if p.get('delivery'): delivery_list.append('delivery')
        if p.get('takeout'): delivery_list.append('takeout')
        if p.get('dineIn'): delivery_list.append('dineIn')

        photos = p.get('photos', [])
        photos_uri = photos[0].get('name') if photos else None
        editorial_summary = (p.get('editorialSummary') or {}).get('text')
        primary_type_display = (p.get('primaryTypeDisplayName') or {}).get('text')
        servicedescription = editorial_summary or primary_type_display

        place_id = p.get('id')
        business_name_str = p.get('displayName', {}).get('text')
        primary_type = informative_place_type(p)

        db_insert_succeeded = False
        async with _insertion_semaphore:
            if business_name_str:
                try:
                    _name_holder = await Database.fetchval(
                        "SELECT providerid FROM etown_provider "
                        "WHERE LOWER(TRIM(businessname)) = LOWER(TRIM($1)) AND providerid <> $2 LIMIT 1",
                        business_name_str,
                        str(uuid.uuid5(uuid.NAMESPACE_DNS, f"provider:{place_id}")) if place_id else "",
                    )
                    if _name_holder:
                        logger.info(
                            "[web_provider_insert] '%s' (place_id=%s) already exists as providerid=%s, "
                            "skipping duplicate insert", business_name_str, place_id, _name_holder,
                        )
                        return {"status": "exists", "place_id": place_id}
                except Exception as _rex:
                    logger.warning(
                        "[web_provider_insert] Pre-insert name re-check failed for '%s': %s",
                        business_name_str, _rex,
                    )

            if place_id and city:
                try:
                    db_result = await ProviderService.insert_web_provider_to_db(
                        place_id=place_id,
                        business_name=business_name_str,
                        city=city,
                        description=servicedescription,
                        lat=float(lat) if lat else None,
                        lon=float(lon) if lon else None,
                        state=state_code,
                        areaname=areaname,
                        formatted_address=p.get('formattedAddress'),
                        phone_number=str(mobile_number) if mobile_number else None,
                        business_category=primary_type,
                    )
                    if db_result:
                        db_insert_succeeded = True
                        try:
                            await ProviderService.upsert_provider_details(
                                provider_id=db_result['provider_info']['providerid'],
                                place_id=place_id,
                                google_rating=p.get('rating'),
                                google_rating_count=p.get('userRatingCount'),
                                business_status=p.get('businessStatus'),
                                website_uri=p.get('websiteUri'),
                                google_maps_uri=p.get('googleMapsUri'),
                                opening_hours=p.get('regularOpeningHours') or p.get('currentOpeningHours'),
                                reviews=p.get('reviews'),
                                images=p.get('photos'),
                            )
                        except Exception as _dex:
                            logger.warning(
                                "[web_provider_insert] Failed to upsert provider_details for place_id=%s: %s",
                                place_id, _dex,
                            )
                        _task = asyncio.create_task(
                            _run_enhancement_guarded(
                                provider_id=db_result['provider_info']['providerid'],
                                providerservice_copy_id=db_result['providerservice_info']['providerservicesid'],
                                providerservice_orig_id=db_result['providerservice_info']['providerservicesid_orig'],
                                contact_id=db_result['contact_info']['contactid'],
                                userprofile_id=db_result['userprofile_info']['userprofileid'],
                                business_name=business_name_str,
                                description=servicedescription or '',
                                business_category_input=primary_type,
                                service_category_input=None,
                                latitude=float(lat) if lat else None,
                                longitude=float(lon) if lon else None,
                                businesslogo=None,
                                phone_number=str(mobile_number) if mobile_number else '',
                                email=None,
                                city=city,
                                first_name=business_name_str,
                                formatted_address=p.get('formattedAddress'),
                                area_location=areaname,
                                state=state_code,
                                additionaladdressinfo=None,
                                google_photos=photos,
                                providertype='etown_nonverified',
                                place_types=p.get('types') or [],
                                google_reviews=p.get('reviews') or [],
                                plan_code=db_result['provider_info'].get('plan_code'),
                            )
                        )
                        _web_bg_tasks.add(_task)
                        _task.add_done_callback(_web_bg_tasks.discard)
                except Exception as exc:
                    logger.warning(
                        "[web_provider_insert] Direct DB insert failed for place_id=%s business=%s — falling back to Power Automate: %s",
                        place_id, business_name_str, exc,
                    )
                    if place_id:
                        try:
                            _committed_pscopy_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pscopy:{place_id}"))
                            _committed = await Database.fetchval(
                                "SELECT 1 FROM etown_providerservices_copy WHERE providerservicesid = $1 LIMIT 1",
                                _committed_pscopy_id,
                            )
                            if _committed:
                                db_insert_succeeded = True
                                logger.info(
                                    "[web_provider_insert] DB transaction committed despite exception for place_id=%s, skipping Power Automate fallback",
                                    place_id,
                                )
                        except Exception:
                            logger.warning("[web_provider_insert] could not confirm the DB transaction for place_id=%s", place_id, exc_info=True)

            if not db_insert_succeeded:
                await handle_external_providers_data_insertion(
                    contact_id=contact_id,
                    user_profile_id=user_profile_id,
                    user_name=user_name or "Unknown",
                    business_name=business_name_str,
                    mobile_number=mobile_number,
                    address=p.get('formattedAddress'),
                    latitude=str(lat) if lat else None,
                    longitude=str(lon) if lon else None,
                    city=city,
                    areaname=areaname,
                    state=state_code,
                    directionsUri=p.get('googleMapsUri'),
                    placeUri=p.get('googleMapsUri'),
                    photosUri=photos_uri,
                    place_id=place_id,
                    deliveryOptions=delivery_list,
                    paymentOptions=payment_list,
                    primaryType=primary_type,
                    rating=p.get('rating'),
                    currentOpeningHours=p.get('regularOpeningHours'),
                    reviews=p.get('reviews'),
                    servicedescription=servicedescription,
                )

        return {"status": "inserted", "place_id": place_id, "city": city}
    finally:
        if _insert_lock:
            _insert_lock.release()

async def cleanup_orphaned_web_provider(place_id: str, business_name: str = "") -> None:
    """Delete a web provider's place_id-derived rows from six Postgres tables, then from Dataverse.

    Args:
        place_id: Google place id that derives every UUID5 record id.
        business_name: Used only in log messages.
    """
    contact_id_str     = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"contact:{place_id}"))
    userprofile_id_str = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"userprofile:{place_id}"))
    provider_id_str    = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"provider:{place_id}"))
    pscopy_id_str      = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pscopy:{place_id}"))

    try:
        exists = await Database.fetchval(
            """SELECT 1 FROM etown_contact WHERE contactid = $1
               UNION ALL SELECT 1 FROM etown_provider WHERE providerid = $2
               UNION ALL SELECT 1 FROM etown_providerservices_copy WHERE providerservicesid = $3
               LIMIT 1""",
            contact_id_str, provider_id_str, pscopy_id_str,
        )
    except Exception as exc:
        logger.warning("[web_cleanup] Existence check failed for place_id=%s: %s", place_id, exc)
        return

    if not exists:
        logger.debug("[web_cleanup] No orphaned records for place_id=%s ('%s')", place_id, business_name)
        return

    logger.info("[web_cleanup] Removing orphaned records for place_id=%s business='%s'", place_id, business_name)

    _del_queries = [
        ("etown_provider_details",      "providerid",         provider_id_str),
        ("etown_providerservices",      "providerservicesid", pscopy_id_str),
        ("etown_providerservices_copy", "providerservicesid", pscopy_id_str),
        ("etown_provider",              "providerid",         provider_id_str),
        ("etown_userprofile",           "userprofileid",      userprofile_id_str),
        ("etown_contact",               "contactid",          contact_id_str),
    ]
    for table, col, val in _del_queries:
        try:
            await Database.execute(f"DELETE FROM {table} WHERE {col} = $1", val)
        except Exception as exc:
            logger.warning("[web_cleanup] DELETE from %s failed for place_id=%s: %s", table, place_id, exc)
    try:
        await sync_provider_delete_from_dataverse(
            contact_id=contact_id_str,
            userprofile_id=userprofile_id_str,
            provider_id=provider_id_str,
        )
    except Exception as exc:
        logger.warning("[web_cleanup] Dataverse cleanup failed for place_id=%s: %s", place_id, exc)