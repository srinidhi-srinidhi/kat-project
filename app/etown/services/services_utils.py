"""Services Utilities - Postgres and AI logic for provider details, geocoding and generated content.

Domain layer behind /api/services, also used by the provider and offerings routes: assembles provider details with ratings, circle counts and view tracking, geocodes addresses and towns through Google Maps, and generates categories, services, offerings and descriptions with Azure OpenAI.

Features:
    - Provider details: Comprehensive service provider information retrieval
    - Provider view tracking: Automatic tracking of provider profile views
    - Provider ratings: Individual rating retrieval from completed service requests with city and service information
    - MyCircle integration: Real-time calculation of provider circle relationships and counts
    - Service generation: AI-powered service categorization and description
    - Business enhancement: AI-driven business description improvement
    - Community post enhancement: AI rewrite of need, event and update descriptions
    - Location services: Geocoding and reverse geocoding functionality
    - Public provider data: External service provider integration
    - Town details: Geographic location information generation
    - Category generation: Automated business category classification
    - General category generation: Broad business category classification
    - Parallel data fetching: Optimized provider data aggregation
    - Feed integration: Provider social media and content feeds
    - Timezone handling: Multi-timezone business operation support
    - Background processing: Non-blocking analytics and tracking operations

Classes:
    Database: Service-specific database operations extending FeedDatabase

Functions:
    _calculate_mycircle_data(): Calculate provider circle membership and connection counts
    _fetch_provider_data(): Parallel retrieval of provider information
    _map_provider_response(): Structure provider data for API responses
    _track_provider_view_background(): Background analytics for provider views
    describe_actual_offerings_prompt(): AI prompt reporting what a business actually offers
    find_relevant_services_prompt(): AI prompt RECOMMENDING services a business could add
    generate_category_prompt(): AI prompt for category classification
    generate_general_category_prompt(): AI prompt for broad-category classification from name + place types + description
    resolve_description_word_cap(): Resolve the word cap for an enhanced business description
    resolve_description_word_floor(): Resolve the minimum word count for an enhanced description
    business_description_enhancement_prompt(): AI prompt for description improvement
    community_post_description_prompt(): AI prompt for need, event and update description rewrites
    provider_details_generation_prompt(): AI prompt for provider details
    town_description_generation_prompt(): AI prompt for town descriptions
    get_town_details(): Geocode a town to latitude/longitude (no image; see generate_town_card)
    generate_services_logic(): Recommendation endpoint logic (POST /services/generate_services)
    generate_actual_offerings_logic(): Evidence-grounded catalogue for the onboarding write path
    generate_category_logic(): Business category classification logic
    generate_general_category_logic(): General business category classification logic
    enhance_business_description_logic(): Business or community post description improvement logic
    get_provider_details_logic(): Provider information retrieval logic with view tracking
    get_web_provider_details_logic(): Public provider data logic
    get_location_details_logic(): Geocoding and location resolution logic
    get_geocoding_logic(): Address-to-coordinate conversion logic
    generate_public_provider_details_logic(): External provider integration logic
    generate_towndetails_logic(): AI-powered town description generation
    get_popular_providers_near_you_logic(): Popular/nearby providers for the All tab, shaped as mytown providers, with categories
    Database.validate_user_ids_match(): Verify a contactid/userprofileid pair belong to the same user
    Database.get_provider_details_data(): Fetch a provider's core details row, favourite status and collection id
    Database.get_provider_ratings(): Retrieve individual provider ratings with requestor/city/service info
    Database.get_provider_offerings_tree(): Fetch the provider's offering->category->item JSONB tree
    Database.get_provider_ids_by_name(): Resolve a provider's contactid+userprofileid by business name
    Database.get_provider_ids_by_provider_id(): Resolve a provider's contactid+userprofileid by providerid
    Database.add_provider_view_tracking(): Record a provider profile view, deduped per viewer
    Database.get_provider_view_count(): Count distinct views recorded for a provider
    Database.get_unrated_provider_list(): Fetch providers with a completed but unrated booking for a member

Dependencies:
    - asyncio: Asynchronous service operations and background tasks
    - pytz: Timezone handling for global operations
    - httpx: HTTP client for external API integrations
    - app.db.client: with_retry decorator applied to every Database method below
    - app.agent_infrastructure: AI services for content generation
    - app.etown.feed: Feed data integration
    - app.schemas.api: Service API response models
    - app.core: Configuration and logging infrastructure
    - app.utils.http_client: Tracked HTTP client for performance monitoring
    - app.utils.blob_storage: to_public_blob_url for popular-providers category images
    - app.etown.mytown.mytown_utils: provider formatting and collection links for popular providers, imported at call time
    - app.etown.plans.plans_utils: plan resolution and provider badges
    - app.etown.provider.provider_review_utils: review_pending_exclusion filter
    - app.etown.suggestions.suggestions_utils: load_both_circles for MyCircle data

Updates:
    23-Feb-2026: Complete module documentation with feature inventory
    02-Mar-2026: Replaced untracked httpx.AsyncClient() with TrackedHTTPClient.
    04-Mar-2026: Added provider view tracking and ratings list; collection-based favorites; computed mycircle values.
    13-Mar-2026: Added generate_general_category_logic and GeneralCategoryGenerationResponse.
    29-Jun-2026: provider_details returns per-service `serviceitems` in SubServiceDetailItem.
    10-Jul-2026: provider_details returns Etown_whatsapp in ProviderDetailItem.
    13-Jul-2026: The provider_details favourite check excludes soft-deleted collection items.
    10-Aug-2026: Added generate_actual_offerings_logic, which reports only what the evidence supports.
    18-Aug-2026: provider_details returns plan_code and badges; popular_providers reports provider_type and badge_label.
    19-Aug-2026: provider_details returns Etown_background_imageUrl; profile feed passes the real viewer.
    20-Aug-2026: popular_providers verification_status reads publicsearchprovider; Etown_providertype returned; plan badge labels.
    26-Aug-2026: Silent exception handlers now logged with tracebacks; vague log messages rewritten.
    01-Sep-2026: Town details drop the Places photo URL; self-views untracked; description word cap.
    02-Sep-2026: get_popular_providers_near_you_logic() skips disabled providers.
    07-Sep-2026: provider_details' Etown_Mycircle reports 'connected', 'invited' or null.
    17-Sep-2026: Provider view tracking stamps source='profile_details'.
    21-Sep-2026: Description word floor; popular providers drop those pending review.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    07-Oct-2026: Description enhancement rewrites need, event and update posts within 300 characters.
    08-Oct-2026: Removed LangSmith tracing.
    26-Oct-2026: popular_providers returns categories, with Etown_pinned from etown_popular_category_pin at the request's location, and paged All-tab providers formatted by mytown's _format_provider_suggestions; takes language and category_ids; favourites bypass the per-category cap; dropped mutual connections; popular providers select image_stack; provider_details adds Etown_slideshow from image_stack.

Notes:
    Integrates with Azure OpenAI gpt41 for intelligent content generation.
    Supports multiple external APIs for comprehensive provider data.
"""

from typing import List, Dict, Optional, Tuple, Any, Union
import asyncio
import pytz
import json
import re
import httpx
from app.core.logging import logger
from app.db.client import with_retry
from app.core.config import settings
from app.utils.http_client import get_http_client
from app.utils.blob_storage import to_public_blob_url
from app.etown.feed.feed_utils import Database as FeedDatabase
from app.etown.plans.plans_utils import premium_display, resolve_plan, resolve_provider_badges
from app.etown.provider.provider_review_utils import review_pending_exclusion
from app.schemas.api import (
    ProviderDetailsResponse,
    ProviderDetailItem,
    SubServiceDetailItem,
    ProviderFeedDetails,
    ProviderRatingDetails,
    ServiceGenerationResponse,
    ServiceItem,
    CategoryGenerationResponse,
    GeneralCategoryGenerationResponse,
    BusinessDescriptionResponse,
    LocationDetailsResponse,
    GeocodingResponse,
    PublicProviderDetailsResponse,
    PublicProviderItem,
    GenerateTownDetailsResponse,
    ParsedOfferingData,
)
from datetime import datetime,timezone
import time
from app.agent_infrastructure.infrastructure.llm_clients import gpt41, gpt41_precise
from app.etown.suggestions.suggestions_utils import load_both_circles


async def _calculate_mycircle_data(
    viewer_user_profile_id: Optional[str],
    provider_user_profile_id: str
) -> Tuple[Optional[str], str]:
    """Return (status, count): connected, invited or None between viewer and provider, plus the provider's circle size."""
    if not viewer_user_profile_id or not provider_user_profile_id:
        return None, "0"
    
    try:
        circle_check_query = """
        SELECT
            EXISTS (
                SELECT 1 FROM etown_memberrelation
                WHERE (from_userprofile = $1 AND to_userprofile = $2)
                   OR (from_userprofile = $2 AND to_userprofile = $1)
                UNION ALL
                SELECT 1 FROM etown_providerrelation
                WHERE (fromuserprofileid = $1 AND touserprofileid = $2)
                   OR (fromuserprofileid = $2 AND touserprofileid = $1)
            ) AS is_connected,
            EXISTS (
                SELECT 1 FROM etown_invitation
                WHERE invitationcode = 'Requested'
                  AND ((inviteruserprofile = $1 AND inviteuserprofile = $2)
                    OR (inviteruserprofile = $2 AND inviteuserprofile = $1))
            ) AS is_invited
        """
        
        count_query = """
        SELECT COUNT(DISTINCT profile_id) as total_count FROM (
            SELECT to_userprofile AS profile_id FROM etown_memberrelation WHERE from_userprofile = $1
            UNION ALL
            SELECT from_userprofile AS profile_id FROM etown_memberrelation WHERE to_userprofile = $1
            UNION ALL
            SELECT touserprofileid AS profile_id FROM etown_providerrelation WHERE fromuserprofileid = $1
            UNION ALL
            SELECT fromuserprofileid AS profile_id FROM etown_providerrelation WHERE touserprofileid = $1
        ) all_connections
        """
        
        connection_result, count_result = await asyncio.gather(
            Database.fetchrow(circle_check_query, viewer_user_profile_id, provider_user_profile_id),
            Database.fetchrow(count_query, provider_user_profile_id)
        )
        
        is_connected = bool(connection_result and connection_result.get('is_connected'))
        is_invited = bool(connection_result and connection_result.get('is_invited'))
        total_count = count_result.get('total_count', 0) if count_result else 0
        
        circle_status = "connected" if is_connected else ("invited" if is_invited else None)
        
        return circle_status, str(total_count)
        
    except Exception as e:
        logger.error(f"Error calculating mycircle data for provider {provider_user_profile_id}, viewer {viewer_user_profile_id}: {e}", exc_info=True)
        return None, "0"

async def _fetch_provider_data(
    contact_id: str, 
    user_profile_id: str, 
    feed_page: int, 
    feed_limit: int,
    viewer_contact_id: Optional[str] = None,
    viewer_user_profile_id: Optional[str] = None
) -> Tuple[Optional[Dict], List[Dict], List[Dict], List[Dict], bool, Optional[str], List[Dict]]:
    """Fetch a provider's details, services, viewer-visible feeds, ratings, favourite status and offerings tree in parallel."""
    details_task = Database.get_provider_details_data(contact_id, user_profile_id, viewer_contact_id, viewer_user_profile_id)
    feeds_task = Database.get_feeds(
        current_contact_id=viewer_contact_id,
        current_user_profile_id=user_profile_id,
        limit=feed_limit,
        offset=feed_page,
        mode="Public",
        isMyProfile=True
    )
    ratings_task = Database.get_provider_ratings(user_profile_id)
    offerings_task = Database.get_provider_offerings_tree(user_profile_id)

    (provider_data, subservices_data, is_favorite_in_collection, collection_id), (feed_rows, _), rating_rows, offerings_tree = await asyncio.gather(
        details_task, feeds_task, ratings_task, offerings_task
    )
    return provider_data, subservices_data, feed_rows, rating_rows, is_favorite_in_collection, collection_id, offerings_tree

def _map_provider_response(
    provider_data: Dict, 
    subservices_data: List[Dict], 
    feed_rows: List[Dict],
    rating_rows: List[Dict],
    is_favorite_in_collection: bool = False,
    mycircle_data: Tuple[Optional[str], str] = ("", "0"),
    offerings_tree: Optional[List[Dict]] = None,
    collection_id: Optional[str] = None,
    premium_label: Optional[str] = None
) -> ProviderDetailsResponse:
    """Map raw provider query results to ProviderDetailsResponse, falling back to Google reviews for unverified providers."""
    
    def fmt_date(d):
        if isinstance(d, datetime):
            return d.strftime("%m/%d/%Y")
        return str(d) if d else None

    def fmt_datetime(d):
        import pytz
        IST = pytz.timezone('Asia/Kolkata')
        if d and isinstance(d, datetime):
            utc_time = d.replace(tzinfo=pytz.utc)
            ist_time = utc_time.astimezone(IST)
            return ist_time.strftime("%m/%d/%Y %I:%M %p")
        return str(d) if d else None

    is_favorite = "true" if is_favorite_in_collection else "false"
    
    mycircle_status, mycircle_count = mycircle_data

    _providertype = (provider_data.get("publicsearchprovider") or "").lower().strip()
    _unverified_msg = None if _providertype in ("verified", "etown_verified") else "This is not a verified provider"

    provider_item = ProviderDetailItem(
        Etown_ProviderId=provider_data.get("providerid"),
        Etown_businessname=provider_data.get("businessname"),
        Etown_address=provider_data.get("additionaladdressinfo"),
        Etown_fetchaddress=provider_data.get("address"),
        Etown_description=provider_data.get("description"),
        Etown_availability=provider_data.get("availability"),
        Etown_latitude=str(provider_data.get("latitude")) if provider_data.get("latitude") is not None else None,
        Etown_longitude=str(provider_data.get("longitude")) if provider_data.get("longitude") is not None else None,
        Etown_serviceimageurl=provider_data.get("serviceimageurl"),
        Etown_background_imageUrl=provider_data.get("background_image"),
        Etown_slideshow=list(provider_data.get("image_stack") or []),
        Etown_city=provider_data.get("city"),
        Etown_areaname=provider_data.get("areaname"),
        Etown_statecode=provider_data.get("statecode"),
        Etown_publicsearchprovider=provider_data.get("publicsearchprovider"),
        Etown_providertype=premium_display(provider_data.get("providertype"), premium_label),
        Etown_contactid=provider_data.get("contactid"),
        Etown_userprofileid=provider_data.get("userprofileid"),
        Etown_mobile=provider_data.get("mobilenumber"),
        Etown_alternatemobile=provider_data.get("alternatemobile"),
        Etown_whatsapp=provider_data.get("whatsapp", True),
        Etown_createdon=fmt_date(provider_data.get("creationdate")),
        Etown_overallrating=str(provider_data.get("overallrating")) if provider_data.get("overallrating") is not None else None,
        Etown_ratingcount=str(provider_data.get("memberbookingcount")) if provider_data.get("memberbookingcount") is not None else None,
        Etown_completedservices=str(provider_data.get("completedservices")),
        Etown_emailaddress=provider_data.get("email"),
        Etown_favouritecheck=is_favorite,
        Etown_Mycircle=mycircle_status,
        Etown_MyCircleCount=mycircle_count,
        Etown_CollectionId=collection_id or "",
        Etown_unverified_msg=_unverified_msg,
        Etown_servicetype=provider_data.get("servicetype")
    )

    subservice_items = []
    for svc in subservices_data:
        raw_items = svc.get("serviceitems")
        if isinstance(raw_items, str):
            try:
                raw_items = json.loads(raw_items)
            except (ValueError, TypeError):
                logger.warning("services: serviceitems is not valid JSON for a subservice; treating it as null", exc_info=True)
                raw_items = None

        item = SubServiceDetailItem(
            Etown_providerserviceid=svc.get("providerservicesid"),
            Etown_servicename=svc.get("services"),
            Etown_businesscategory=svc.get("businesscategory"),
            Etown_servicecategory=svc.get("servicecategory"),
            Etown_providerid=svc.get("providerid"),
            Etown_serviceimage1=svc.get("serviceimageurl1"),
            Etown_serviceimage2=svc.get("serviceimageurl2"),
            Etown_serviceimage3=svc.get("serviceimageurl3"),
            Etown_address=svc.get("address"),
            Etown_additionaladdressinfo=svc.get("additionaladdressinfo"),
            Etown_pricerange=svc.get("pricerange"),
            Etown_servicecreatedon=fmt_datetime(svc.get("createdon")),
            Etown_servicedescription=svc.get("servicedescription"),
            Etown_quantity=svc.get("quantity"),
            Etown_serviceitems=raw_items if isinstance(raw_items, list) else None,
        )
        subservice_items.append(item)
        
    feed_items = []
    for feed in feed_rows:
        feed_item = ProviderFeedDetails(
            Etown_feedid=feed.get("feedid"),
            Etown_feedfilelocationurl=feed.get("feedfilelocationurl"),
            Etown_feedfilelocationurl2=feed.get("feedfilelocation2"),
            Etown_feedfilelocationurl3=feed.get("feedfilelocation3"),
            Etown_feedfilelocationurl4=feed.get("feedfilelocation4"),
            Etown_feedfilelocationurl5=feed.get("feedfilelocation5"),
            Etown_feedvideofilelocation=feed.get("feedvideofilelocation"),
            Etown_thumbnailfileurl=feed.get("thumbnailfileurl"),
            Etown_feedtext=feed.get("feedtext"),
            Etown_commentscount=str(feed.get("comments_count", 0)),
            Etown_feedlikecount=str(feed.get("likes_count", 0)),
            Etown_feedlikecontactperson=feed.get("feedlikecontactperson", ""),
            Etown_createdon=fmt_datetime(feed.get("createdon")),
            Etown_modifiedon=fmt_datetime(feed.get("createdon"))
        )
        feed_items.append(feed_item)

    rating_items = []
    for rating in rating_rows:
        rating_item = ProviderRatingDetails(
            Etown_requestorname=rating.get("requestorname"),
            Etown_ratingvalue=str(rating.get("ratingvalue")) if rating.get("ratingvalue") is not None else None,
            Etown_ratingdate=fmt_date(rating.get("bookingcompletedtime")),
            Etown_profileimage=rating.get("profileimage"),
            Etown_city=rating.get("city"),
            Etown_statecode=rating.get("statecode"),
            Etown_servicebooked=rating.get("service_name"),
            Etown_servicedescription=rating.get("servicedescription"),
            Etown_source="database"
        )
        rating_items.append(rating_item)

    if not rating_items and _providertype not in ("verified", "etown_verified"):
        raw_google_reviews = provider_data.get("google_reviews")
        if isinstance(raw_google_reviews, str):
            try:
                raw_google_reviews = json.loads(raw_google_reviews)
            except (ValueError, TypeError):
                logger.warning("services: google_reviews is not valid JSON; treating it as null", exc_info=True)
                raw_google_reviews = None
        if isinstance(raw_google_reviews, list):
            for review in raw_google_reviews:
                author = review.get("authorAttribution") or {}
                publish_time = review.get("publishTime")
                review_date = None
                if publish_time:
                    try:
                        dt = datetime.fromisoformat(publish_time.replace("Z", "+00:00"))
                        review_date = fmt_date(dt)
                    except (ValueError, TypeError):
                        logger.debug("services: review publishTime %r is not ISO; omitting the date", publish_time)
                        review_date = None
                rating_items.append(ProviderRatingDetails(
                    Etown_requestorname=author.get("displayName"),
                    Etown_ratingvalue=str(review.get("rating")) if review.get("rating") is not None else None,
                    Etown_ratingdate=review_date,
                    Etown_profileimage=author.get("photoUri"),
                    Etown_city=provider_data.get("city"),
                    Etown_statecode=provider_data.get("statecode"),
                    Etown_servicebooked=None,
                    Etown_source="Google"
                ))

    _svc_name_to_id: Dict[str, str] = {
        (svc.get("services") or "").strip().lower(): svc["providerservicesid"]
        for svc in subservices_data
        if svc.get("providerservicesid") and svc.get("services")
    }
    offering_data = []
    for off in (offerings_tree or []):
        if not isinstance(off, dict):
            continue
        parsed = ParsedOfferingData.model_validate(off)
        if parsed.offering_name and parsed.providerservices_id is None:
            parsed.providerservices_id = _svc_name_to_id.get(
                parsed.offering_name.strip().lower()
            )
        offering_data.append(parsed)

    return ProviderDetailsResponse(
        Providerdetails=[provider_item],
        Subservicedetails=subservice_items,
        Feeddetails=feed_items,
        Ratingdetails=rating_items,
        offering_data=offering_data
    )


def describe_actual_offerings_prompt(
    business_name: str,
    place_types: Optional[List[str]] = None,
    reviews: Optional[List[str]] = None,
    description: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Build the system prompt asking the model for only evidence-backed offerings, allowing an empty list."""
    evidence = [f"Business name: {business_name}"]
    if place_types:
        evidence.append(f"Business categories: {', '.join(place_types)}")
    if description and description.strip():
        evidence.append(f"Existing description: {description.strip()}")
    if reviews:
        joined = " | ".join(r.strip() for r in reviews if r and r.strip())[:2000]
        if joined:
            evidence.append(f"Customer reviews: {joined}")

    return [{
        "role": "system",
        "content": f"""
You are cataloguing a real local business for a marketplace listing. Report ONLY what this
business actually sells or does, based strictly on the evidence below.

**Evidence:**
{chr(10).join(f"- {line}" for line in evidence)}

**Rules:**
- The business NAME is usually the strongest signal. "MSP Cushion works" makes cushions;
  "Aadhil Arms Gun Shop" sells firearms; "AKR Healthy Foods" sells food.
- Customer reviews describe the real trade — trust them over a generic category label.
- Do NOT suggest services the business could add, expand into, or that similar businesses
  offer. You are recording what they already do, not advising them.
- Do NOT invent modern or aspirational offerings (subscriptions, concierge services, virtual
  consultations, app-based booking) unless the evidence explicitly mentions them.
- Prefer the plain trade term a local customer would search for.
- If the evidence is too thin to say what this business actually does, return an EMPTY
  `items` list. An empty list is a correct answer and is strongly preferred over a guess.

**Output Format (JSON only):**
{{
    "reasoning": "what the evidence tells you about this business's actual trade",
    "confident": true or false,
    "items": [
        {{
            "type": "service|product",
            "category": "plain trade category, e.g. furniture, bakery, plumbing",
            "name": "what they actually sell or do, in plain words",
            "description": "one factual sentence grounded in the evidence"
        }}
    ]
}}

Generate at most 3 items. Return `"items": []` and `"confident": false` when unsure.
"""
    }]


def find_relevant_services_prompt(business_description: str) -> List[Dict[str, str]]:
    """Build the system prompt asking the model to recommend one relevant service or product for a business."""
    return [{
        "role": "system",
        "content": f"""
You are an expert business analyst specialized in identifying and recommending relevant services or products based on business descriptions. Your task is to analyze the provided business description and generate a comprehensive list of services or products that would be pertinent and valuable for that type of business.

**Your Task:**
Analyze the business description and identify relevant services or products that:
1. Align with the business type and industry
2. Could be offered by similar businesses
3. Are commonly associated with this business category
4. Would be valuable additions to their service/product portfolio

**Business Description:**
{business_description}

**Guidelines for Service/Product Generation:**
- **Industry Analysis**: Consider the business type, target market, and industry standards
- **Comprehensive Coverage**: Include both primary and secondary services/products
- **Market Relevance**: Focus on services/products that are in demand for this business type
- **Categorization**: Properly classify each item as either a 'service' or 'product'
- **Detailed Descriptions**: Provide clear, engaging descriptions that highlight value propositions

**Output Format:**
Generate the output in the following JSON format with your thought process:

{{
    "Thought": "Analyze the business description and identify the business type, industry, and potential service/product offerings",
    "Action": "find_relevant_services", 
    "Action Input": {{
        "business_analysis": "Brief analysis of the business type and industry",
        "recommended_items": [
            {{
                "type": "service|product",
                "category": "industry category (e.g., food, technology, beauty, automotive)",
                "name": "specific service or product name",
                "description": "detailed description highlighting features, benefits, and value proposition"
            }}
        ]
    }}
}}

**Important:**
- Generate 1 relevant item based on the business description
- Ensure each item is distinct and valuable
- Focus on realistic, market-relevant offerings
- Provide detailed, actionable descriptions

Begin analysis:
"""
    }]

def generate_category_prompt(description: str) -> List[Dict[str, str]]:
    """Build the system prompt asking for a description's business_type and a 1-3 word subcategory as JSON."""
    return [{
        "role": "system",
        "content": f"""
You are an expert business analyst. Your task is to analyze the given business description and provide a concise classification.

**Instructions:**
1.  **Determine Business Type**: First, classify the business offering.
    - If it only performs actions for customers, the type is "service".
    - If it only sells tangible items, the type is "product".
    - If it does both (e.g., a car repair shop that also sells parts), the type is "product,service".
2.  **Generate Subcategory**: Create a concise, specific **subcategory** (1-3 words) that describes the business's primary focus. Write it in English, whatever language the description is written in.
3.  **Format Output**: Your response MUST be a valid JSON object with exactly two keys: "business_type" and "subcategory". Do not add any extra text.

**Business Description:**
"{description}"

**Example 1:**
- **Description**: "We are expert plumbers providing 24/7 emergency services for leaky pipes and drain cleaning."
- **Output**:
{{
    "business_type": "service",
    "subcategory": "Plumbing Services"
}}

**Example 2:**
- **Description**: "Our auto shop offers full-service car repairs and also sells a wide range of certified spare parts and accessories."
- **Output**:
{{
    "business_type": "product,service",
    "subcategory": "Auto Repair & Parts"
}}

Provide only the JSON object as your response.
"""
    }]


from typing import List, Dict

def generate_general_category_prompt(
    description: str,
    business_name: Optional[str] = None,
    place_types: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Build the system prompt classifying a business into a broad generalcategory from its name, types and description."""
    evidence = []
    if business_name and business_name.strip():
        evidence.append(f"Business name: {business_name.strip()}")
    if place_types:
        evidence.append(f"Business categories: {', '.join(place_types)}")

    return [{
        "role": "system",
        "content": f"""
You are a careful and consistent **business classification assistant**.

Your task is to determine a **broad business category** from the evidence.

You must rely **ONLY on the evidence given** — the business name, the business categories,
and the description below. All three are evidence: a trade stated in the name or the
category is as usable as one stated in the description.
{chr(10) + chr(10).join(f"- {line}" for line in evidence) if evidence else ""}

-----------------------
ANTI-HALLUCINATION RULES
-----------------------
- Do NOT guess or assume information not present.
- Do NOT invent services or industries.
- When the evidence conflicts, the business name wins: it is self-declared, while the
  category is an imprecise Google label. A firm named "Best Packers and Movers" categorised
  "Manufacturer" is classified from the name, not the label.
- If NONE of the name, categories or description indicates what the business does,
  return:

{{
  "generalcategory": "Unknown"
}}

-----------------------
CLASSIFICATION PRINCIPLES
-----------------------
- The service description confirms what the business actually does.
- Create **broad categories that group similar businesses together**.
- Think: **"What would customers search for to find businesses like this?"**
- Be specific enough to be useful, but broad enough to group similar services.
- Do NOT split categories by gender, niche, or sub-specialty.
- Use **one unified category for similar business types**.
- Be **consistent**: similar businesses must always receive the same category.

-----------------------
CATEGORY FORMAT RULES
-----------------------
- Always in **English**, whatever language the evidence is written in
- Maximum **1–2 words**
- Use **lowercase**
- Use **underscore for multi-word categories**

Examples:
watch_store
electrical_services
car_repair
digital_marketing

Never: category_watch_store, women's_salon, high_end_italian_restaurant

Correct:salon, restaurant, plumbing, cafe, marketing  

-----------------------
OUTPUT FORMAT (STRICT)
-----------------------
Return EXACTLY this JSON structure:

{{
  "generalcategory": "<category>"
}}

Rules:
- No explanations
- No extra text
- No additional keys
- Must be valid JSON

-----------------------
EXAMPLES
-----------------------

Description:
"We are expert plumbers providing 24/7 emergency services for leaky pipes and drain cleaning."

Output:
{{
  "generalcategory": "plumbing"
}}

Description:
"Our restaurant serves authentic Italian cuisine with fresh ingredients and traditional recipes."

Output:
{{
  "generalcategory": "restaurant"
}}

Description:
"We provide SEO, social media marketing, and online advertising services."

Output:
{{
  "generalcategory": "digital_marketing"
}}

Description:
"Healthy Habit offers freshly squeezed fruit juices made from premium fruits in a clean environment."

Output:
{{
  "generalcategory": "cafe"
}}

Description:
"A company focused on improving lifestyle solutions."

Output:
{{
  "generalcategory": "Unknown"
}}

-----------------------
BUSINESS DESCRIPTION
-----------------------
{description}

Return ONLY the JSON object.
"""
    }]

DESCRIPTION_WORD_CAP = 50
DESCRIPTION_WORD_CAP_MAX = 150
DESCRIPTION_WORD_FLOOR = 45
COMMUNITY_POST_DESCRIPTION_MAX_CHARS = 300
COMMUNITY_POST_GUIDANCE = {
    "need": (
        "a Need: a resident asking their town for a service, product or help",
        "Say plainly what is needed, then any specifics the original gives: quantity, size, "
        "when, where and budget. Write it as the poster asking, so a provider or neighbour "
        "can tell at once whether they can help.",
    ),
    "event": (
        "an Event: an announcement inviting the town to something happening",
        "Say what the event is and who it is for, then what happens there and any date, time, "
        "venue, entry or organizer details the original gives. Keep an inviting tone.",
    ),
    "update": (
        "an Update: news or information shared with the town",
        "Say what happened or is changing first, then who it affects and anything readers "
        "should do, keeping only what the original states.",
    ),
}


def resolve_description_word_cap(
    description: Optional[str],
    floor: int = DESCRIPTION_WORD_CAP,
    ceiling: int = DESCRIPTION_WORD_CAP_MAX,
) -> int:
    """Return the rewrite word budget: the description's word count clamped between floor and ceiling."""
    written = len((description or "").split())
    return max(floor, min(ceiling, written))


def resolve_description_word_floor(
    description: Optional[str],
    cap: int,
    floor: int = DESCRIPTION_WORD_FLOOR,
) -> int:
    """Return the minimum rewrite length: 90% of the description's words, at least floor and never above cap."""
    written = len((description or "").split())
    return min(cap, max(floor, int(written * 0.9)))


def business_description_enhancement_prompt(
    original_description: str,
    business_name: Optional[str] = None,
    place_types: Optional[List[str]] = None,
    reviews: Optional[List[str]] = None,
    word_cap: int = DESCRIPTION_WORD_CAP,
    min_words: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Build the system prompt for an evidence-only business description that names the trade first.

    Args:
        original_description: Any existing description text.
        business_name: The trading name.
        place_types: Google place types, umbrella entries already removed.
        reviews: Google review texts.
        word_cap: Word budget for the rewrite.
        min_words: When set, require a 3-5 sentence paragraph of at least this many words.
    """
    evidence = []
    if business_name and business_name.strip():
        evidence.append(f"Business name: {business_name.strip()}")
    if place_types:
        evidence.append(f"Business categories: {', '.join(place_types)}")
    if original_description and original_description.strip():
        evidence.append(f"Existing description: {original_description.strip()}")
    if reviews:
        joined = " | ".join(r.strip() for r in reviews if r and r.strip())[:2000]
        if joined:
            evidence.append(f"Customer reviews: {joined}")

    if min_words and min_words > 0:
        length_rule = (
            f"- Write {min(min_words, word_cap)}-{word_cap} words as 3-5 complete sentences that "
            f"read as a single paragraph. One sentence is not an acceptable answer at this "
            f"length.\n"
            f"- Cover, in this order: what the business is and the trade it works in; what it "
            f"offers, naming every service, product and speciality the evidence carries; and who "
            f"or where it serves. Drop any of these the evidence does not support rather than "
            f"inventing it."
        )
        thin_rule = (
            "- When the evidence is thin, take the length from the trade itself: the work that "
            "trade ordinarily does, what a customer comes to it for, and what the existing "
            "description already says, each in plain terms. Never reach for specifics the "
            "evidence does not carry, and never pad with adjectives."
        )
    else:
        length_rule = f"- Under {word_cap} words."
        thin_rule = (
            "- When the evidence is thin, write one plain factual sentence built from the name "
            "and category rather than padding it with adjectives. Short and accurate beats long "
            "and empty."
        )

    return [{
        "role": "system",
        "content": f"""You are writing the description for a real local business in a marketplace
listing. It must tell a customer what this business actually does, using ONLY the evidence below.

**Evidence:**
{chr(10).join(f"- {line}" for line in evidence) if evidence else "- (none)"}

**Requirements:**
- Write in the same language as the existing description. Tamil in means Tamil out: natural,
  simple, everyday Tamil a local customer reads easily. English in means English out. When it
  mixes languages, write in the language most of it is written in. Never translate it into
  another language. When there is no existing description, write in English.
- Keep business names, brand names, products, services, specialities and places exactly as
  written, and keep the original meaning.
- Name the trade or offering explicitly, in the first clause. A reader must learn what the
  business does from the first few words.
{length_rule}
- Plain and professional prose. No markdown, headings, bullets or labels.
- Where the existing description already carries real detail, keep that detail — every service,
  product, speciality and place it names must survive the rewrite. Rewriting is for clarity and
  structure, not for cutting the business down.
- Use ONLY the evidence. Do NOT invent years in business, awards, certifications, prices,
  staff, coverage areas, opening hours, or services not evidenced above.
- When the evidence conflicts, the business name wins: it is self-declared, while the
  category is an imprecise Google label. A firm named "Best Packers and Movers" categorised
  "Manufacturer" is a packers and movers. Never describe a business as the conflicting
  category, and never write that it "is listed as" one — describe the trade its name states.
- Do NOT write generic filler. Descriptions of the form "Experience reliable, professional
  services tailored to your needs" are rejected: they would fit any business, so they say
  nothing about this one.
{thin_rule}

Provide only the description, nothing else:"""
    }]


def community_post_description_prompt(original_description: str, post_type: str) -> List[Dict[str, str]]:
    """Build the system prompt that rewrites a community need, event or update description within the post length limit.

    Args:
        original_description: The description the poster wrote.
        post_type: need, event or update.
    """
    kind, focus = COMMUNITY_POST_GUIDANCE[post_type]
    return [{
        "role": "system",
        "content": f"""You are polishing the description of a post in a local town community feed.
The post is {kind}. Rewrite the poster's text so it is clear, friendly and easy to act on, using
ONLY what the original says.

**Original description:**
{original_description.strip()}

**Requirements:**
- {focus}
- At most {COMMUNITY_POST_DESCRIPTION_MAX_CHARS} characters, as 1-3 complete sentences.
- Plain prose. No markdown, headings, bullets, hashtags, emojis or labels.
- Keep every concrete detail the original gives: names, items, quantities, dates, times,
  places, prices and contact details must survive the rewrite.
- Do NOT invent details the original does not state: no dates, times, venues, prices,
  quantities, names or contact details that are not there.
- Fix spelling and grammar, and keep the poster's language.
- Do NOT write generic filler that would fit any post.

Provide only the description, nothing else:"""
    }]


def provider_details_generation_prompt(business_data_str: str) -> List[Dict[str, str]]:
    """Build the system prompt asking for a separate described item per service or product in the business data."""
    return [{
        "role": "system",
        "content": f"""
You are an expert in generating descriptive items based on the business name, the services or products they offer, reviews, and images. Your task is to thoroughly analyze the provided data (business name, services/products, reviews, and images) and create clear, concise, and highly specific descriptions for each service or product offered. Even if there are multiple products or services being mentioned or implied in the reviews or images, ensure each item is treated separately, with its own distinct and detailed description.

**Input Data:**
{business_data_str}

**Your Task:**
- Process the business name, services/products, reviews, and images provided.
- If the business offers multiple services or products, identify each one separately and create a unique, explicit description for each offering. Pay attention to any additional items suggested in reviews or images that may not be explicitly mentioned as part of the business name.
- For each identified item (service or product), generate a detailed, engaging description that highlights its specific features, benefits, and value, based on the information gathered from the reviews and images.
- Ensure the descriptions are explicit, comprehensive, and tailored specifically to the individual service or product.

**Guidelines for Description Generation:**
- **Use Reviews**: Review the feedback for specific mentions of features, benefits, customer experiences, or other useful details that can help describe the service/product in detail.
- **Use Images**: If images are provided, describe any visual features like design, color, shape, or any other distinguishing characteristics of the service/product.
- **Multiple Items**: If the business offers multiple products or services, or if reviews/images suggest multiple offerings, treat each as a separate item. Do not combine descriptions of different items into a single entry.
- **Explicit and Detailed Descriptions**: Every description should focus solely on the specific service or product, highlighting its key features, selling points, and other details that set it apart.

**Output Format:**
Generate the output in the following JSON format. Each item should be described with:
- `type`: Whether it's a "service" or a "product".
- `category`: The industry or type classification (e.g., "food", "technology", "beauty").
- `name`: The name of the specific service or product.
- `description`: A detailed, engaging, and specific description, based on the business name, service/product, reviews, and images.

**JSON Schema for Output:**

{{
    "items": [
        {{
            "type": "string",
            "category": "string",
            "name": "string",
            "description": "string"
        }}
    ]
}}
"""
    }]

def town_description_generation_prompt(cityname: str) -> List[Dict[str, str]]:
    """Build the messages asking for a town description under 25 words that starts with the town name."""
    return [{
        "role": "system",
        "content": f"You are a travel guide. Your task is to generate a brief, engaging description for the town or area {cityname}. The description MUST start with the town name '{cityname}' (title case) and be strictly under 25 words. Do NOT include city or state. Provide only the description, nothing else."
    }, {
        "role": "user",
        "content": f"Generate a brief, engaging description for {cityname} under 25 words, starting with the town name only."
    }]

async def get_town_details(cityname: str, statecode: str) -> Dict[str, Union[float, str]]:
    """Geocode a town and return its latitude, longitude and an empty image, or an empty response when not found.

    Raises:
        ValueError: When the Google Maps API key is not configured.
        Exception: When the geocoding request returns a non-200 status.
    """
    api_key = settings.google_maps_api_key
    if not api_key:
        raise ValueError("Google Maps API Key is not configured.")

    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    geocode_params = {"address": f"{cityname}, {statecode}", "key": api_key}

    async with get_http_client(timeout=30.0) as client:
        response = await client.get(geocode_url, params=geocode_params)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch location details. Status: {response.status_code}")

    data = response.json()
    if data.get("status") != "OK" or not data.get("results"):
        logger.warning(f"No results found for {cityname}, {statecode}")
        return GenerateTownDetailsResponse()

    result = data["results"][0]["geometry"]["location"]

    return {
        "latitude": result["lat"],
        "longitude": result["lng"],
        "image": "",
    }


async def generate_services_logic(business_description: str) -> ServiceGenerationResponse:
    """Recommend services for a business description with gpt41, tagging each with the generated general category.

    Raises:
        ValueError: If the LLM response contains no parseable JSON.
    """
    logger.info(f"Generating services for business description: {business_description[:100]}...")
    
    system_prompt = find_relevant_services_prompt(business_description)
    
    response = gpt41.invoke(system_prompt)
    
    response_content = response.content
    
    logger.info(f"LLM response received: {response_content[:200]}...")
    
    try:
        generated_data = json.loads(response_content)
    except json.JSONDecodeError:
        json_match = re.search(r'\{.*\}', response_content, re.DOTALL)
        if json_match:
            generated_data = json.loads(json_match.group())
        else:
            raise ValueError("Could not extract JSON from LLM response")
    
    action_input = generated_data.get("Action Input", {})
    recommended_items = action_input.get("recommended_items", [])
    
    try:
        general_category_response = await generate_general_category_logic(business_description)
        general_category = general_category_response.generalcategory
    except Exception as e:
        logger.warning(f"Failed to generate general category: {str(e)}")
        general_category = "unknown"

    service_items = [
        ServiceItem(
            type=item.get("type", "service"),
            category=item.get("category", "general"),
            name=item.get("name", ""),
            description=item.get("description", ""),
            generalcategory=general_category
        )
        for item in recommended_items
    ]
    
    logger.info(f"Successfully generated {len(service_items)} service recommendations")
    
    return ServiceGenerationResponse(
        status="success",
        business_description=business_description,
        analysis=action_input.get("business_analysis", ""),
        recommended_services=service_items,
        thought_process=generated_data.get("Thought", ""),
        total_recommendations=len(service_items)
    )

async def generate_actual_offerings_logic(
    business_name: str,
    place_types: Optional[List[str]] = None,
    reviews: Optional[List[str]] = None,
    description: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Return evidence-grounded {type, category, name, description} offerings, or [] when the model is not confident."""
    if not business_name or not business_name.strip():
        return []

    system_prompt = describe_actual_offerings_prompt(
        business_name, place_types, reviews, description
    )
    response = await gpt41_precise.ainvoke(system_prompt)
    response_content = response.content

    try:
        data = json.loads(response_content)
    except json.JSONDecodeError:
        json_match = re.search(r'\{.*\}', response_content, re.DOTALL)
        if not json_match:
            logger.warning(
                "Could not extract JSON from offerings response for '%s'", business_name
            )
            return []
        data = json.loads(json_match.group())

    if not data.get("confident", False):
        logger.info(
            "Offerings generation not confident for '%s' — leaving the catalogue empty "
            "(reasoning: %s)", business_name, str(data.get("reasoning"))[:200],
        )
        return []

    items = [
        {
            "type": item.get("type", "service"),
            "category": item.get("category", "general"),
            "name": (item.get("name") or "").strip(),
            "description": (item.get("description") or "").strip(),
        }
        for item in (data.get("items") or [])
        if (item.get("name") or "").strip()
    ]
    logger.info(
        "Generated %d evidence-grounded offering(s) for '%s'", len(items), business_name
    )
    return items


async def _general_category_or_none(description: str) -> Optional[str]:
    """Return the general category for a description, or None when generation fails or comes back blank."""
    try:
        general_category_response = await generate_general_category_logic(description)
    except Exception as e:
        logger.warning(f"Failed to generate general category: {str(e)}")
        return None
    return general_category_response.generalcategory or None


async def generate_category_logic(
    description: str,
    include_general: bool = True,
) -> CategoryGenerationResponse:
    """Classify a business description into business type and subcategory, optionally adding the general category.

    Args:
        description: The business description to analyze.
        include_general: Whether to also generate the general category.

    Returns:
        CategoryGenerationResponse with business type, subcategory, and general category.

    Raises:
        ValueError: If the LLM response contains no parseable JSON.
    """
    logger.info(f"Generating category for description: {description[:100]}...")
    
    system_prompt = generate_category_prompt(description)

    general_task = (
        asyncio.create_task(_general_category_or_none(description))
        if include_general
        else None
    )
    try:
        response = await gpt41_precise.ainvoke(system_prompt)
    except BaseException:
        if general_task:
            general_task.cancel()
        raise

    response_content = response.content

    logger.info(f"LLM response received: {response_content[:200]}...")

    try:
        classification_data = json.loads(response_content)
    except json.JSONDecodeError:
        json_match = re.search(r'\{.*\}', response_content, re.DOTALL)
        if json_match:
            classification_data = json.loads(json_match.group())
        else:
            if general_task:
                general_task.cancel()
            raise ValueError("Could not extract JSON from LLM response")

    return CategoryGenerationResponse(
        business_type=classification_data.get("business_type", "unknown").strip(),
        subcategory=classification_data.get("subcategory", "unknown").strip(),
        generalcategory=await general_task if general_task else None,
    )

async def generate_general_category_logic(
    description: str,
    business_name: Optional[str] = None,
    place_types: Optional[List[str]] = None,
) -> GeneralCategoryGenerationResponse:
    """Classify a business into a broad general category from its description, name and place types.

    Raises:
        ValueError: If the LLM response contains no parseable JSON.
    """
    logger.info(f"Generating general category for description: {description[:100]}...")

    system_prompt = generate_general_category_prompt(description, business_name, place_types)
    
    response = await gpt41_precise.ainvoke(system_prompt)
    
    response_content = response.content
    
    logger.info(f"LLM response received: {response_content[:200]}...")
    
    try:
        classification_data = json.loads(response_content)
    except json.JSONDecodeError:            
        json_match = re.search(r'\{.*\}', response_content, re.DOTALL)
        if json_match:
            classification_data = json.loads(json_match.group())
        else:
            raise ValueError("Could not extract JSON from LLM response")

    return GeneralCategoryGenerationResponse(
        generalcategory=classification_data.get("generalcategory", "unknown").strip()
    )

async def enhance_business_description_logic(
    description: str,
    business_name: Optional[str] = None,
    place_types: Optional[List[str]] = None,
    reviews: Optional[List[str]] = None,
    word_cap: Optional[int] = None,
    min_words: Optional[int] = None,
    description_type: str = "business_description",
) -> BusinessDescriptionResponse:
    """Rewrite a business or community post description with gpt41, clipping it to its word or character cap at a sentence end.

    Args:
        description: The original description.
        business_name: The trading name.
        place_types: Google place types, umbrella entries already removed.
        reviews: Google review texts.
        word_cap: Word budget; defaults to DESCRIPTION_WORD_CAP.
        min_words: Shortest rewrite to ask for, capped at word_cap.
        description_type: business_description, or need, event or update for a community post.
    """
    if description_type in COMMUNITY_POST_GUIDANCE:
        logger.info(f"Enhancing community {description_type} description: {description[:100]}...")
        response = await gpt41.ainvoke(community_post_description_prompt(description, description_type))
        enhanced_description = response.content.strip()
        if len(enhanced_description) > COMMUNITY_POST_DESCRIPTION_MAX_CHARS:
            clipped = enhanced_description[:COMMUNITY_POST_DESCRIPTION_MAX_CHARS]
            sentence_end = max(clipped.rfind('.'), clipped.rfind('!'), clipped.rfind('?'))
            enhanced_description = (
                clipped[:sentence_end + 1] if sentence_end > 0 else clipped.rsplit(' ', 1)[0]
            ).strip()
        return BusinessDescriptionResponse(
            original_description=description,
            enhanced_description=enhanced_description,
            word_count=len(enhanced_description.split()),
            enhancement_applied=enhanced_description.lower() != description.lower().strip(),
            message="Enhancement completed successfully."
        )

    cap = word_cap if word_cap and word_cap > 0 else DESCRIPTION_WORD_CAP
    floor = min(min_words, cap) if min_words and min_words > 0 else None
    logger.info(
        f"Enhancing business description (target {floor or 0}-{cap} words): {description[:100]}..."
    )

    system_prompt = business_description_enhancement_prompt(
        description, business_name, place_types, reviews, word_cap=cap, min_words=floor
    )

    response = await gpt41.ainvoke(system_prompt)

    enhanced_description = response.content.strip()

    words = enhanced_description.split()
    if len(words) > cap:
        clipped = ' '.join(words[:cap])
        if clipped[-1] not in '.!?':
            sentence_end = max(clipped.rfind('. '), clipped.rfind('! '), clipped.rfind('? '))
            if sentence_end > 0:
                clipped = clipped[:sentence_end + 1]
        enhanced_description = clipped

    enhancement_applied = enhanced_description.lower().strip() != description.lower().strip()
    word_count = len(enhanced_description.split())
    if floor and word_count < floor:
        logger.warning(
            f"Enhanced description ran short: {word_count} words against a {floor}-word floor"
        )
    
    logger.info(f"Successfully enhanced description (Applied: {enhancement_applied})")
    
    return BusinessDescriptionResponse(
        original_description=description,
        enhanced_description=enhanced_description,
        word_count=word_count,
        enhancement_applied=enhancement_applied,
        message="Enhancement completed successfully."
    )

async def _track_provider_view_background(provider_user_profile_id: str, viewer_user_profile_id: str):
    """Record a provider profile view, logging the outcome and swallowing any error."""
    try:
        success, is_new_view = await Database.add_provider_view_tracking(
            provider_user_profile_id, viewer_user_profile_id
        )
        if success and is_new_view:
            logger.info(f"New provider view tracked: provider={provider_user_profile_id}, viewer={viewer_user_profile_id}")
        elif success:
            logger.debug(f"Duplicate provider view ignored: provider={provider_user_profile_id}, viewer={viewer_user_profile_id}")
        else:
            logger.warning(f"Failed to track provider view: provider={provider_user_profile_id}, viewer={viewer_user_profile_id}")
    except Exception as e:
        logger.error(f"Error in background provider view tracking: {str(e)}", exc_info=True)

async def get_provider_details_logic(
    contact_id: str,
    user_profile_id: str,
    feed_page: int = 1,
    feed_limit: int = 10,
    viewer_contact_id: Optional[str] = None,
    viewer_user_profile_id: Optional[str] = None
) -> ProviderDetailsResponse:
    """Return a provider's full profile with circle status and plan badge, tracking the view for another viewer."""
    logger.info(f"Fetching provider details for {user_profile_id}")
    
    if viewer_user_profile_id and user_profile_id and viewer_user_profile_id != user_profile_id:
        asyncio.create_task(_track_provider_view_background(user_profile_id, viewer_user_profile_id))
    
    provider_data, subservices_data, feed_rows, rating_rows, is_favorite_in_collection, collection_id, offerings_tree = await _fetch_provider_data(
        contact_id, user_profile_id, feed_page, feed_limit, viewer_contact_id, viewer_user_profile_id
    )

    if not provider_data:
        logger.warning(f"Provider not found: {user_profile_id}")
        return ProviderDetailsResponse(Providerdetails=[], Subservicedetails=[], Feeddetails=[], Ratingdetails=[])

    mycircle_data = await _calculate_mycircle_data(viewer_user_profile_id, user_profile_id)

    plan = await resolve_plan(user_profile_id)
    premium_label = plan.get("badge_label") if plan and plan["plan_code"] != "free" else None

    response = _map_provider_response(
        provider_data, subservices_data, feed_rows, rating_rows, is_favorite_in_collection,
        mycircle_data, offerings_tree, collection_id, premium_label,
    )

    if plan:
        response.plan_code = plan["plan_code"]
        response.badge_label = plan.get("badge_label")
        response.verified_badge = bool(plan.get("verified_badge"))
    return response

async def get_web_provider_details_logic(
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None,
    provider_name: Optional[str] = None,
    provider_id: Optional[str] = None,
    feed_page: int = 1,
    feed_limit: int = 10
) -> ProviderDetailsResponse:
    """Return a provider's public profile resolved by provider_id, URL-style business name, or contact and profile IDs.

    Raises:
        ValueError: If no provider_id, provider_name or ID pair is supplied.
    """
    if provider_id:
        logger.info("web_provider_details: resolving by provider_id | provider_id=%s", provider_id)
        provider_ids = await Database.get_provider_ids_by_provider_id(provider_id)
        
        if not provider_ids:
            logger.warning(f"Provider not found with provider_id: {provider_id}")
            return ProviderDetailsResponse(Providerdetails=[], Subservicedetails=[], Feeddetails=[])
            
        contact_id = provider_ids["contactid"]
        user_profile_id = provider_ids["userprofileid"]

    elif provider_name:
        logger.info("web_provider_details: resolving by name | raw_name=%r", provider_name)
        clean_name = provider_name.replace("-", " ").replace("_", " ").replace("%20", " ")
        clean_name = " ".join(clean_name.split())
        
        logger.info("web_provider_details: name normalised for lookup | raw=%r clean=%r", provider_name, clean_name)

        provider_ids = await Database.get_provider_ids_by_name(clean_name)
        
        if not provider_ids:
            logger.warning(f"Provider not found with business name: {clean_name}")
            return ProviderDetailsResponse(Providerdetails=[], Subservicedetails=[], Feeddetails=[])
        
        contact_id = provider_ids["contactid"]
        user_profile_id = provider_ids["userprofileid"]
        
    elif contact_id and user_profile_id:
        logger.info(f"Using provided IDs - contact_id: {contact_id}, user_profile_id: {user_profile_id}")
        
    else:
        raise ValueError("Either provider_id, provider_name, or both contact_id and user_profile_id must be provided")
    
    logger.info(f"Fetching provider details for {user_profile_id}")
    
    provider_data, subservices_data, feed_rows, rating_rows, *_ = await _fetch_provider_data(
        contact_id, user_profile_id, feed_page, feed_limit
    )
    
    if not provider_data:
        logger.warning(f"Provider not found: {user_profile_id}")
        return ProviderDetailsResponse(Providerdetails=[], Subservicedetails=[], Feeddetails=[], Ratingdetails=[])

    premium_label = (
        (await resolve_provider_badges([user_profile_id])).get(user_profile_id) or {}
    ).get("premium_label")

    return _map_provider_response(
        provider_data, subservices_data, feed_rows, rating_rows, premium_label=premium_label
    )

async def get_location_details_logic(latitude: float, longitude: float) -> LocationDetailsResponse:
    """Reverse-geocode coordinates with Google and return the first result's address components by type.

    Raises:
        ValueError: When the Google Maps API key is not configured.
        Exception: When the geocoding request returns a non-200 status.
    """
    api_key = settings.google_maps_api_key
    if not api_key:
        raise ValueError("Google Maps API Key is not configured.")
        
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "latlng": f"{latitude},{longitude}",
        "key": api_key,
        "language": "en"
    }
    
    async with get_http_client() as client:
        response = await client.get(url, params=params)
        
    if response.status_code != 200:
        raise Exception(f"Failed to fetch location details from Google. Status code: {response.status_code}")
        
    data = response.json()
    
    if data.get("status") != "OK" or not data.get("results"):
         logger.warning(f"No results found for coordinates: {latitude}, {longitude}")
         return LocationDetailsResponse(parsed_components={}, raw=data)

    result = data["results"][0]
    address_components = result.get("address_components", [])
    
    parsed_components = {}
    for component in address_components:
        long_name = component.get("long_name")
        types = component.get("types", [])
        
        for type_name in types:
            if type_name == "political":
                continue
            parsed_components[type_name] = long_name
            
    return LocationDetailsResponse(
        parsed_components=parsed_components,
        raw=result
    )

async def get_geocoding_logic(address: str) -> GeocodingResponse:
    """Geocode an address with Google and return its latitude, longitude and formatted address.

    Raises:
        ValueError: If the API key is missing, nothing is found, or the location is incomplete.
        Exception: If the geocoding request returns a non-200 status.
    """
    api_key = settings.google_maps_api_key
    if not api_key:
        raise ValueError("Google Maps API Key is not configured.")
        
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": api_key
    }
    
    async with get_http_client() as client:
        response = await client.get(url, params=params)
        
    if response.status_code != 200:
        raise Exception(f"Failed to geocode address. Status code: {response.status_code}")
        
    data = response.json()
    
    if data.get("status") != "OK" or not data.get("results"):
         logger.warning(f"No results found for address: {address}")
         raise ValueError(f"Could not geocode address: {address}")
    result = data["results"][0]
    location = result.get("geometry", {}).get("location", {})
    formatted_address = result.get("formatted_address", address)
    
    if "lat" not in location or "lng" not in location:
        raise ValueError("Incomplete location data received from Geocoding API")
            
    return GeocodingResponse(
        latitude=location.get("lat"),
        longitude=location.get("lng"),
        formatted_address=formatted_address
    )

async def generate_public_provider_details_logic(place_id: str) -> PublicProviderDetailsResponse:
    """Fetch Google place details and generate one described item with a general category, recovering malformed JSON.

    Raises:
        ValueError: When the API key is missing, the place has no details, or no JSON is recoverable.
        Exception: When the place details request returns a non-200 status.
    """
    api_key = settings.google_maps_api_key
    if not api_key:
        raise ValueError("Google Maps API Key is not configured.")
    fields = "name,types,reviews,photos"
    url = f"https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": fields,
        "key": api_key
    }

    async with get_http_client() as client:
        response = await client.get(url, params=params)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch place details. Status: {response.status_code}")

    data = response.json()
    result = data.get("result", {})

    if not result:
        raise ValueError(f"No details found for place_id: {place_id}")
    business_name = result.get("name", "Unknown Business")
    types = result.get("types", [])
    generic_types = {'point_of_interest', 'store', 'establishment'}
    filtered_types = [t for t in types if t not in generic_types]
    
    reviews = result.get("reviews", [])
    reviews_text = " ".join([r.get("text", "") for r in reviews]) if reviews else "No reviews available"
    image_links = []
    photos = result.get("photos", [])[:5]
    for photo in photos:
        ref = photo.get("photo_reference")
        if ref:
            img_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=400&photoreference={ref}&key={api_key}"
            image_links.append(img_url)

    data_summary = f"""
    Name of the business: {business_name}
    Items they offer (Types): {', '.join(filtered_types)}
    Reviews: {reviews_text}
    """
    messages = provider_details_generation_prompt(data_summary)
    user_content = "Generate detailed items for this business."
    if image_links:
        user_content += f"\nReference Image URLs: {', '.join(image_links)}"

    messages.append({"role": "user", "content": user_content})
    logger.info(f"Generating public provider details for {business_name}")
    response = gpt41.invoke(messages)
    content = response.content
    logger.debug(f"LLM raw response: {content}")
    try:
        json_data = json.loads(content)
        logger.debug(f"Parsed JSON data: {json_data}")
        items_list = json_data.get("data", {}).get("items", [])
        if not items_list:
            items_list = json_data.get("items", [])
        items_list = items_list[:1]

        try:

            business_description = f"{business_name} - A business that provides {', '.join(filtered_types)} services. {reviews_text[:200].strip()}..."
            general_category_response = await generate_general_category_logic(business_description)
            general_category = general_category_response.generalcategory

            if general_category.lower() == "unknown" and items_list:
                first_item_category = items_list[0].get("category", "").strip().lower()
                if first_item_category and first_item_category not in ["general", "other", "misc", "unknown", ""]:
                    general_category = first_item_category.replace(" ", "_")
                    logger.info(f"Used item category as fallback: {general_category}")
                    
        except Exception as e:
            logger.warning(f"Failed to generate general category for {business_name}: {str(e)}")
            if items_list:
                first_item_category = items_list[0].get("category", "").strip().lower()
                general_category = first_item_category.replace(" ", "_") if first_item_category and first_item_category not in ["general", "other", "misc", "unknown", ""] else "unknown"
            else:
                general_category = "unknown"
        
        mapped_items = [
            PublicProviderItem(
                type=i.get("type", "service"),
                category=i.get("category", "general"),
                name=i.get("name", "Unknown"),
                description=i.get("description", ""),
                generalcategory=general_category
            ) for i in items_list
        ]
        return PublicProviderDetailsResponse(data={"items": mapped_items})

    except json.JSONDecodeError as e:
        logger.error(f"JSONDecodeError for {place_id}: {e}\nRaw LLM output: {content}", exc_info=True)
        import re
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            json_str = json_match.group()
            json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
            try:
                json_data = json.loads(json_str)
                logger.debug(f"Recovered JSON data: {json_data}")
                items_list = json_data.get("data", {}).get("items", [])
                if not items_list:
                    items_list = json_data.get("items", [])
                logger.debug(f"Items from recovered JSON: {items_list}")
                items_list = items_list[:1]

                try:
                    business_description = f"{business_name} - A business that provides {', '.join(filtered_types)} services. {reviews_text[:200].strip()}..."
                    general_category_response = await generate_general_category_logic(business_description)
                    general_category = general_category_response.generalcategory
                    if general_category.lower() == "unknown" and items_list:
                        first_item_category = items_list[0].get("category", "").strip().lower()
                        if first_item_category and first_item_category not in ["general", "other", "misc", "unknown", ""]:
                            general_category = first_item_category.replace(" ", "_")
                            logger.info(f"Used item category as fallback: {general_category}")
                            
                except Exception as e:
                    logger.warning(f"Failed to generate general category for {business_name}: {str(e)}")
                    if items_list:
                        first_item_category = items_list[0].get("category", "").strip().lower()
                        general_category = first_item_category.replace(" ", "_") if first_item_category and first_item_category not in ["general", "other", "misc", "unknown", ""] else "unknown"
                    else:
                        general_category = "unknown"
                
                mapped_items = [
                    PublicProviderItem(
                        type=i.get("type", "service"),
                        category=i.get("category", "general"),
                        name=i.get("name", "Unknown"),
                        description=i.get("description", ""),
                        generalcategory=general_category
                    ) for i in items_list
                ]
                logger.info(f"Successfully recovered and mapped {len(mapped_items)} items for {business_name}")
                return PublicProviderDetailsResponse(data={"items": mapped_items})
            except Exception as e2:
                logger.error(f"Second JSON parse attempt failed for {place_id}: {e2}\nExtracted: {json_str}", exc_info=True)
                items_match = re.search(r'"items"\s*:\s*\[(.*)', json_str, re.DOTALL)
                if items_match:
                    items_content = items_match.group(1)
                    raw_items = re.split(r'\},\s*\{', items_content)
                    parsed_items = []
                    for idx, raw_item in enumerate(raw_items):
                        if not raw_item.strip():
                            continue
                        if not raw_item.strip().startswith('{'):
                            raw_item = '{' + raw_item
                        if not raw_item.strip().endswith('}'):
                            raw_item = raw_item + '}'
                        try:
                            item = json.loads(raw_item)
                            try:
                                business_description = f"{business_name} - A business that provides {', '.join(filtered_types)} services. {reviews_text[:200].strip()}..."
                                general_category_response = await generate_general_category_logic(business_description)
                                general_category = general_category_response.generalcategory
                                if general_category.lower() == "unknown":
                                    item_category = item.get("category", "").strip().lower()
                                    if item_category and item_category not in ["general", "other", "misc", "unknown", ""]:
                                        general_category = item_category.replace(" ", "_")
                                        logger.info(f"Used item category as fallback: {general_category}")
                                        
                            except Exception as e:
                                logger.warning(f"Failed to generate general category for {business_name}: {str(e)}")
                                item_category = item.get("category", "").strip().lower()
                                general_category = item_category.replace(" ", "_") if item_category and item_category not in ["general", "other", "misc", "unknown", ""] else "unknown"
                            
                            parsed_items.append(PublicProviderItem(
                                type=item.get("type", "service"),
                                category=item.get("category", "general"),
                                name=item.get("name", "Unknown"),
                                description=item.get("description", ""),
                                generalcategory=general_category
                            ))
                        except Exception as e3:
                            logger.error(f"Failed to parse item {idx} in partial recovery: {e3}\nRaw: {raw_item}", exc_info=True)
                    if parsed_items:
                        logger.warning(f"Returning {min(len(parsed_items), 1)} partial item due to incomplete/truncated JSON from LLM for {place_id}.")
                        return PublicProviderDetailsResponse(data={"items": parsed_items[:1]})
        raise ValueError(f"Failed to generate valid JSON structure from AI response. Raw output: {content}")

async def generate_towndetails_logic(townname: str, cityname: str, statecode: str) -> GenerateTownDetailsResponse:
    """Geocode a town and generate its short LLM description in parallel, returning the combined town details.

    Raises:
        Exception: When geocoding or description generation fails.
    """
    try:
        system_prompt = town_description_generation_prompt(townname.title())
        full_location = f"{townname}, {cityname}, {statecode}"
        town_details_task = get_town_details(full_location, statecode)
        description_task = gpt41.ainvoke(system_prompt)
        town_details, ai_response = await asyncio.gather(town_details_task, description_task)
        description = ai_response.content.strip()
        expected_prefix = townname.title()
        if not description or not description.lower().startswith(expected_prefix.lower()):
            description = f"{expected_prefix} is a notable location."
        else:
            import re
            description = re.sub(rf"^{re.escape(expected_prefix)}[^a-zA-Z]+", f"{expected_prefix} ", description, flags=re.IGNORECASE)
        return GenerateTownDetailsResponse(
            townname=townname.title(),
            cityname=cityname.title(),
            description=description.strip(),
            latitude=town_details.get("latitude", 0.0),
            longitude=town_details.get("longitude", 0.0),
            image=town_details.get("image", "")
        )
    except Exception as e:
        logger.error(f"Failed to generate town details for {townname}, {cityname}: {str(e)}", exc_info=True)
        raise Exception(f"Error generating town details: {str(e)}")
    
class Database(FeedDatabase):
    @classmethod
    @with_retry()
    async def validate_user_ids_match(cls, contact_id: str, user_profile_id: str) -> bool:
        """Return whether the user profile belongs to the contact.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
            
        query = """
            SELECT COUNT(*) as count
            FROM public.etown_userprofile up
            INNER JOIN public.etown_contact c ON up.contactid = c.contactid
            WHERE c.contactid = $1 AND up.userprofileid = $2
        """
        
        try:
            async with cls._pool.acquire() as con:
                row = await con.fetchrow(query, contact_id, user_profile_id)
                return row["count"] > 0 if row else False
        except Exception as e:
            logger.error(f"Error validating user IDs {contact_id}, {user_profile_id}: {str(e)}", exc_info=True)
            raise

    @classmethod
    @with_retry()
    async def get_provider_details_data(cls, contact_id: str, user_profile_id: str, viewer_contact_id: Optional[str] = None, viewer_user_profile_id: Optional[str] = None) -> tuple[Optional[dict], List[dict], bool, Optional[str]]:
        """Return (provider, services, is_favourite, collection_id) for a live provider and the viewer's collection.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
            
        provider_query = """
            SELECT
                p.providerid,
                p.businessname,
                p.additionaladdressinfo,
                p.address,
                p.description,
                p.availability,
                p.latitude,
                p.longitude,
                p.serviceimageurl,
                p.background_image,
                p.image_stack,
                p.city,
                p.areaname,
                p.statecode,
                p.publicsearchprovider,
                p.providertype,
                p.contactid,
                p.userprofileid,
                p.mobilenumber,
                p.alternatemobile,
                p.whatsapp,
                p.email,
                p.creationdate,
                p.servicetype,
                r.overallrating,
                r.memberbookingcount,
                COALESCE((
                    SELECT COUNT(DISTINCT ar.allrequestid)
                    FROM public.etown_allrequests ar
                    JOIN public.etown_requestservicelists rsl ON ar.allrequestid = rsl.bookingid
                    WHERE rsl.serviceprovideruserprofileid = p.userprofileid
                    AND ar.requeststatus = 'completed'
                ), 0) AS completedservices,
                pd.reviews AS google_reviews
            FROM public.etown_provider p
            LEFT JOIN public.etown_ratings r ON p.userprofileid = r.provideruserprofileid
            LEFT JOIN public.etown_provider_details pd ON p.providerid = pd.providerid
            WHERE p.contactid = $1 AND p.userprofileid = $2

              AND p.deleted IS DISTINCT FROM 'true'
              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
"""
        
        subservices_query = """
            SELECT
                providerservicesid,
                services,
                businesscategory,
                servicecategory,
                providerid,
                serviceimageurl1,
                serviceimageurl2,
                serviceimageurl3,
                address,
                additionaladdressinfo,
                pricerange,
                createdon,
                servicedescription,
                quantity,
                serviceitems
            FROM public.etown_providerservices
            WHERE userprofileid = $1
        """
        
        collection_query = """
            SELECT c.collectionid
            FROM public.etown_collections c
            INNER JOIN public.etown_collectionitems ci ON c.collectionid = ci.collectionid
            WHERE c.usercontactid = $1 AND c.useruserprofileid = $2
            AND ci.providerid = $3
            AND (ci.isfavourite IS NULL OR LOWER(ci.isfavourite) != 'unfavourite')
            LIMIT 1
        """
        
        try:
            async with cls._pool.acquire() as con:
                provider_row = await con.fetchrow(provider_query, contact_id, user_profile_id)
                provider_details = dict(provider_row) if provider_row else None
                
                subservices_rows = await con.fetch(subservices_query, user_profile_id)
                subservices_details = [dict(row) for row in subservices_rows]
                
                is_favorite_in_collection = False
                collection_id = None
                if viewer_contact_id and viewer_user_profile_id and provider_details:
                    collection_row = await con.fetchrow(
                        collection_query,
                        viewer_contact_id,
                        viewer_user_profile_id,
                        provider_details.get("providerid")
                    )
                    if collection_row:
                        collection_id = collection_row["collectionid"]
                        is_favorite_in_collection = bool(collection_id)

                return provider_details, subservices_details, is_favorite_in_collection, collection_id
                
        except Exception as e:
            logger.error(f"Error fetching provider details for {user_profile_id}: {str(e)}", exc_info=True)
            raise

    @classmethod
    @with_retry()
    async def get_provider_ratings(cls, provider_user_profile_id: str) -> List[Dict]:
        """Return one rating per completed, rated booking of the provider, newest first, with rater and service details.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
            
        query = """
            SELECT * FROM (
                SELECT DISTINCT ON (ar.allrequestid)
                    ar.requestorname,
                    ar.ratingvalue,
                    ar.bookingcompletedtime,
                    c.city,
                    c.profileimage,
                    c.statecode,
                    ps.services AS service_name,
                    ps.servicedescription
                FROM public.etown_allrequests ar
                JOIN public.etown_requestservicelists rsl ON ar.allrequestid = rsl.bookingid
                LEFT JOIN public.etown_providerservices ps ON rsl.providerservices = ps.providerservicesid
                LEFT JOIN public.etown_userprofile up ON ar.requestoruserprofileid = up.userprofileid
                LEFT JOIN public.etown_contact c ON up.contactid = c.contactid
                WHERE
                    rsl.serviceprovideruserprofileid = $1
                    AND ar."isRating" != 'false'
                    AND ar.ratingvalue IS NOT NULL
                    AND ar.requeststatus = 'completed'
                ORDER BY ar.allrequestid, ps.services ASC NULLS LAST
            ) dedup
            ORDER BY dedup.bookingcompletedtime DESC
        """
        
        try:
            async with cls._pool.acquire() as con:
                rating_rows = await con.fetch(query, provider_user_profile_id)
                return [dict(row) for row in rating_rows]
        except Exception as e:
            logger.error(f"Error fetching provider ratings for {provider_user_profile_id}: {str(e)}", exc_info=True)
            raise

    @classmethod
    @with_retry()
    async def get_provider_offerings_tree(cls, user_profile_id: str) -> List[Dict[str, Any]]:
        """Return the provider's normalised etown_offerings item tree, or [] when there is no row.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        try:
            async with cls._pool.acquire() as con:
                row = await con.fetchrow(
                    "SELECT items FROM public.etown_offerings WHERE userprofileid = $1",
                    user_profile_id,
                )
            if not row:
                return []
            raw = row["items"]
            tree = json.loads(raw) if isinstance(raw, str) else (raw or [])
            from app.etown.offers.offerings_utils import normalize_offering_tree
            return normalize_offering_tree(tree)
        except Exception as e:
            logger.error(f"Error fetching offerings tree for {user_profile_id}: {str(e)}", exc_info=True)
            raise

    @classmethod
    @with_retry()
    async def get_provider_ids_by_name(cls, provider_name: str) -> Optional[dict]:
        """Return the contactid and userprofileid of a provider matched case-insensitively by business name, or None.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")            
        query = """
            SELECT contactid, userprofileid
            FROM public.etown_provider
            WHERE businessname ILIKE $1
            LIMIT 1
        """        
        try:
            async with cls._pool.acquire() as con:
                row = await con.fetchrow(query, provider_name)
                return dict(row) if row else None                
        except Exception as e:
            logger.error(f"Error fetching provider IDs for name '{provider_name}': {str(e)}", exc_info=True)
            raise
    
    @classmethod
    @with_retry()
    async def get_provider_ids_by_provider_id(cls, provider_id: str) -> Optional[dict]:
        """Return the contactid and userprofileid of the provider with this providerid, or None.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        query = """
            SELECT contactid, userprofileid
            FROM public.etown_provider
            WHERE providerid = $1
            LIMIT 1
        """
        try:
            async with cls._pool.acquire() as con:
                row = await con.fetchrow(query, provider_id)
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error fetching provider IDs for provider_id '{provider_id}': {str(e)}", exc_info=True)
            raise

    @classmethod
    @with_retry()
    async def add_provider_view_tracking(cls, provider_user_profile_id: str, viewer_user_profile_id: str) -> tuple[bool, bool]:
        """Record a profile view through add_provider_view() and return (success, is_new_view).

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
            
        try:
            async with cls._pool.acquire() as con:
                result = await con.fetchval(
                    "SELECT add_provider_view($1, $2, 'profile_details')",
                    provider_user_profile_id,
                    viewer_user_profile_id
                )
                is_new_view = bool(result)
                return True, is_new_view
                
        except Exception as e:
            logger.error(f"Error adding provider view tracking: {str(e)}", exc_info=True)
            return False, False

    @classmethod
    @with_retry()
    async def get_provider_view_count(cls, provider_user_profile_id: str) -> int:
        """Return the provider's unique viewer count from get_provider_view_count(), or 0 on error.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
            
        try:
            async with cls._pool.acquire() as con:
                result = await con.fetchval(
                    "SELECT get_provider_view_count($1)",
                    provider_user_profile_id
                )
                return int(result) if result is not None else 0
                
        except Exception as e:
            logger.error(f"Error getting provider view count: {str(e)}", exc_info=True)
            return 0

    @classmethod
    @with_retry()
    async def get_unrated_provider_list(cls, contact_id: str, userprofile_id: str) -> List[Dict]:
        """Return the requestor's completed but unrated bookings with provider and service details, newest first.

        Raises:
            RuntimeError: When the database pool is not initialised.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT 
    rsl.serviceprovidercontactid,
    rsl.serviceprovideruserprofileid,
    rsl.providerservices,
    ar.requestid,
    p.businessname AS provider_businessname,
    p.creationdate AS provider_createdon,
    p.serviceimageurl,
    p.contactid,
    p.userprofileid,
    ps.businessname AS service_businessname,
    ps.createdon AS service_createdon
FROM etown_requestservicelists rsl
JOIN etown_allrequests ar
    ON ar.allrequestid = rsl.bookingid
JOIN etown_provider p
    ON p.userprofileid = rsl.serviceprovideruserprofileid
JOIN etown_providerservices_copy ps
    ON ps.providerservicesid = rsl.providerservices
            WHERE 
                ar.requestorid = $1
                AND ar.requestoruserprofileid = $2
                AND ar.requeststatus = 'completed'
                AND ar."isRating" = 'False'

            ORDER BY ar.createdon DESC
        """

        try:
            async with cls._pool.acquire() as con:
                rows = await con.fetch(query, contact_id, userprofile_id)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching unrated provider list for contact_id={contact_id}: {str(e)}", exc_info=True)
            raise


async def get_popular_providers_near_you_logic(
    contact_id: str,
    user_profile_id: str,
    latitude: float,
    longitude: float,
    page: int = 1,
    limit: int = 20,
    max_distance_km: float = 25.0,
    max_providers_per_category: int = 20,
    language: str = "en",
    category_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Return the All tab's nearby popular providers, formatted as /mytown_suggestions providers, and the categories list with the caller's pins.

    Args:
        contact_id: Requesting user's contact ID, validated against user_profile_id.
        user_profile_id: Requesting user's profile ID.
        latitude: Requester's latitude.
        longitude: Requester's longitude.
        page: 1-based provider page.
        limit: Providers per page.
        max_distance_km: Search radius in kilometres.
        max_providers_per_category: Cap on providers ranked into each category; favourites are always kept.
        language: en or ta; ta returns each category's Tamil display name when stored.
        category_ids: etown_category ids; when given, only providers in any of them are returned and categories is empty.

    Raises:
        ValueError: When the IDs are malformed, do not match, or validation fails.
    """
    from app.etown.mytown.mytown_utils import _fetch_provider_collection_map, _format_provider_suggestions

    if (not contact_id or not user_profile_id or
        len(contact_id.strip()) < 10 or len(user_profile_id.strip()) < 10 or
        contact_id.strip().lower() in {'test', 'invalid', 'dummy', 'fake', 'null', 'none'} or
        user_profile_id.strip().lower() in {'test', 'invalid', 'dummy', 'fake', 'null', 'none'}):
        logger.warning(f"Invalid contact_id ({contact_id}) or user_profile_id ({user_profile_id}) - obviously invalid format")
        raise ValueError("Invalid contact_id or user_profile_id")

    try:
        validation_start = time.time()

        validation_query = """
            SELECT 1
            FROM public.etown_userprofile
            WHERE lower(contactid) = lower($1) AND lower(userprofileid) = lower($2)
            LIMIT 1
        """

        database = Database()
        is_valid = await database.fetchval(validation_query, contact_id, user_profile_id)

        if not is_valid:
            logger.warning(f"ID validation failed: contact_id {contact_id} and user_profile_id {user_profile_id} do not exist or do not belong to the same user")
            raise ValueError("Invalid contact_id or user_profile_id")

        validation_end = time.time()
        logger.info(f" SAFE VALIDATION: Completed in {validation_end - validation_start:.3f}s")

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Database validation error: {e}", exc_info=True)
        raise ValueError("Database validation failed")

    start_time = time.time()
    logger.info(
        "popular_providers_near_you: starting | userprofile=%s contact=%s lat=%s lon=%s radius_km=%s per_category=%s category_ids=%s",
        user_profile_id, contact_id, latitude, longitude, max_distance_km, max_providers_per_category, category_ids,
    )

    try:
        single_query = """
        WITH provider_views_today AS (
            SELECT v.providerprofileid, COUNT(DISTINCT v.userprofileid) AS views_today
            FROM etown_provider_views v
            LEFT JOIN etown_userprofile up ON up.userprofileid = v.userprofileid
            WHERE v.created_on >= (date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata')) AT TIME ZONE 'Asia/Kolkata'
              AND v.created_on <  (date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') + interval '1 day') AT TIME ZONE 'Asia/Kolkata'
              AND v.userprofileid IS NOT NULL
              AND v.userprofileid <> v.providerprofileid
              AND NOT etown_is_deleted(v.userprofileid)
              AND NOT etown_is_deleted(up.contactid)
            GROUP BY v.providerprofileid
        ),

        providers_with_distance AS (
            SELECT
                ps.userprofileid,
                ps.contactid,
                ps.businessname,
                COALESCE(NULLIF(BTRIM(ps.normalized_category), ''), NULLIF(BTRIM(ps.servicecategory), ''), 'other') AS category,
                ps.address,
                ps.latitude,
                ps.longitude,
                COALESCE(p.serviceimageurl, ps.serviceimageurl) AS serviceimageurl,
                ps.serviceimageurl1 AS etown_offerimageurl_source,
                p.providertype,
                COALESCE(NULLIF(BTRIM(p.publicsearchprovider), ''), ps.publicsearchprovider) AS publicsearchprovider,
                p.providerid,
                p.description,
                p.statecode,
                p.creationdate,
                p.background_image,
                p.image_stack,
                p.email,
                COALESCE(ep.townname, p.city) AS city,
                COALESCE(ep.areaname, p.areaname) AS areaname,
                c.mobilephone,
                COALESCE(r.memberbookingcount, 0) AS memberbookingcount,
                COALESCE(r.ratingcount, 0) AS ratingcount,
                COALESCE(r.overallrating, 0.0) AS overallrating,
                COALESCE(pvt.views_today, 0) AS views_today,
                collection_info.collectionid AS collection_id,

                CASE WHEN LOWER(BTRIM(COALESCE(NULLIF(BTRIM(p.publicsearchprovider), ''), ps.publicsearchprovider, ''))) IN ('verified','etown_verified') THEN TRUE ELSE FALSE END AS is_verified,

                6371 * acos(GREATEST(-1, LEAST(1,
                    cos(radians($3)) * cos(radians(ps.latitude)) *
                    cos(radians(ps.longitude) - radians($4)) +
                    sin(radians($3)) * sin(radians(ps.latitude))
                ))) AS distance_km,

                ROW_NUMBER() OVER (
                    PARTITION BY ps.userprofileid
                    ORDER BY
                        CASE WHEN LOWER(BTRIM(COALESCE(NULLIF(BTRIM(p.publicsearchprovider), ''), ps.publicsearchprovider, ''))) IN ('verified','etown_verified') THEN 0 ELSE 1 END,
                        COALESCE(r.memberbookingcount, 0) DESC,
                        COALESCE(r.overallrating, 0.0) DESC
                ) as provider_rank

            FROM etown_providerservices_copy ps
            JOIN etown_provider p ON ps.userprofileid = p.userprofileid
            JOIN etown_contact c ON ps.contactid = c.contactid
            LEFT JOIN etown_ratings r ON ps.userprofileid = r.provideruserprofileid
            LEFT JOIN LATERAL (
                SELECT townname, areaname
                FROM public.etown_etownprovider
                WHERE providerid = p.providerid
                ORDER BY is_primary DESC
                LIMIT 1
            ) ep ON TRUE
            LEFT JOIN (
                SELECT DISTINCT ON (ci.providerid)
                    ci.providerid,
                    col.collectionid
                FROM etown_collectionitems ci
                JOIN etown_collections col ON ci.collectionid = col.collectionid
                WHERE col.usercontactid = $1 AND col.useruserprofileid = $2
                    AND (ci.isfavourite IS NULL OR LOWER(ci.isfavourite) != 'unfavourite')
                ORDER BY ci.providerid, ci.createdon DESC
            ) collection_info ON collection_info.providerid = p.providerid
            LEFT JOIN provider_views_today pvt ON pvt.providerprofileid = ps.userprofileid
            WHERE ps.latitude IS NOT NULL
              AND ps.longitude IS NOT NULL
              AND ps.userprofileid != $2
              AND ps.contactid != $1
              AND ps.latitude BETWEEN ($3 - 0.45) AND ($3 + 0.45)
              AND ps.longitude BETWEEN ($4 - 0.45) AND ($4 + 0.45)

              AND p.deleted IS DISTINCT FROM 'true'
              AND (p.deactivated IS DISTINCT FROM 'true' OR p.reactivation = 'true')
              AND p.disabled IS NOT TRUE
              AND """ + review_pending_exclusion("p.providerid") + """
              AND c.deleted IS DISTINCT FROM 'true'
              AND (c.deactivated IS DISTINCT FROM 'true' OR c.reactivation = 'true')
              AND ($7::uuid[] IS NULL OR EXISTS (
                  SELECT 1 FROM public.etown_category cf
                  WHERE cf.categoryid = ANY($7::uuid[]) AND (
                      cf.categoryname = 'all'
                      OR (cf.categoryname = 'favourites' AND collection_info.collectionid IS NOT NULL)
                      OR cf.categoryname = COALESCE(NULLIF(BTRIM(ps.normalized_category), ''), NULLIF(BTRIM(ps.servicecategory), ''), 'other')
                  )
              ))
        ),

        unique_providers AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY category
                    ORDER BY
                        CASE WHEN is_verified THEN 0 ELSE 1 END,
                        views_today DESC,
                        distance_km ASC
                ) as category_rank
            FROM providers_with_distance
            WHERE provider_rank = 1
              AND distance_km <= $5
        )

        SELECT
            up.*,
            up.category AS etown_category,
            (
                SELECT COUNT(*)::int
                FROM etown_allrequests ar
                JOIN etown_requestservicelists rsl ON rsl.bookingid = ar.allrequestid
                WHERE ar.requeststatus = 'completed'
                  AND rsl.serviceprovideruserprofileid = up.userprofileid
            ) AS total_members
        FROM unique_providers up
        WHERE up.category_rank <= $6 OR up.collection_id IS NOT NULL
"""

        query_start = time.time()
        results = await database.fetch(
            single_query,
            contact_id,
            user_profile_id,
            latitude,
            longitude,
            max_distance_km,
            max_providers_per_category,
            category_ids or None
        )
        logger.info(
            "popular_providers_near_you: query returned %d provider rows in %.3fs | userprofile=%s",
            len(results), time.time() - query_start, user_profile_id,
        )

        rows = sorted(
            (dict(row) for row in results),
            key=lambda r: (0 if r["is_verified"] else 1, -int(r["views_today"] or 0), float(r["distance_km"])),
        )

        category_counts: Dict[str, int] = {}
        if not category_ids:
            per_category: Dict[str, int] = {}
            for r in rows:
                if r["category"] not in ("all", "favourites"):
                    per_category[r["category"]] = per_category.get(r["category"], 0) + 1
            category_counts = {
                "all": len(rows),
                "favourites": sum(1 for r in rows if r["collection_id"] is not None),
                **dict(sorted(per_category.items(), key=lambda item: (-item[1], item[0]))),
            }
        category_rows = {}
        pinned_ids = set()
        if category_counts:
            try:
                category_rows = {
                    row["categorykey"]: row
                    for row in await database.fetch(
                        """
                        SELECT
                            lower(btrim(c.categoryname)) AS categorykey,
                            c.categoryid::text AS categoryid,
                            c.displayname,
                            c.displayname_ta,
                            c.categoryimage
                        FROM public.etown_category c
                        WHERE lower(btrim(c.categoryname)) = ANY($1::text[])
                        """,
                        [name.strip().lower() for name in category_counts],
                    )
                }
            except Exception as e:
                logger.error(
                    "popular_providers_near_you: category lookup failed, returning categories without ids | "
                    "userprofile=%s | %s: %s",
                    user_profile_id, type(e).__name__, e, exc_info=True,
                )
        if category_rows:
            try:
                pinned_ids = {
                    row["categoryid"]
                    for row in await database.fetch(
                        """
                        SELECT categoryid::text AS categoryid
                        FROM public.etown_popular_category_pin
                        WHERE userprofileid = $1
                          AND latitude = round($2::float8::numeric, 4)
                          AND longitude = round($3::float8::numeric, 4)
                          AND ispinned
                        """,
                        user_profile_id, latitude, longitude,
                    )
                }
            except Exception as e:
                logger.error(
                    "popular_providers_near_you: pin lookup failed, returning categories unpinned | "
                    "userprofile=%s | %s: %s",
                    user_profile_id, type(e).__name__, e, exc_info=True,
                )
        categories = []
        for name, count in category_counts.items():
            info = category_rows.get(name.strip().lower()) or {}
            categories.append({
                "Etown_categoryid": info.get("categoryid"),
                "Etown_categoryname": name,
                "Etown_displayname": (language == "ta" and info.get("displayname_ta")) or info.get("displayname") or (
                    "Other Services" if name == "other" else name.replace("_", " ").title()
                ),
                "Etown_categoryimage": to_public_blob_url(info.get("categoryimage")),
                "Etown_providercount": count,
                "Etown_pinned": info.get("categoryid") in pinned_ids,
            })

        offset = (page - 1) * limit
        page_rows = rows[offset:offset + limit]
        collection_map, badge_map = await asyncio.gather(
            _fetch_provider_collection_map(contact_id, user_profile_id, page_rows),
            resolve_provider_badges([r["userprofileid"] for r in page_rows]),
        )
        providers = _format_provider_suggestions(page_rows, collection_map, badge_map)

        logger.info(
            "popular_providers_near_you: completed in %.3fs | userprofile=%s categories=%d providers=%d of %d",
            time.time() - start_time, user_profile_id, len(categories), len(providers), len(rows),
        )

        return {
            "categories": categories,
            "providers": providers,
        }

    except Exception as e:
        error_time = time.time()
        logger.error(
            "popular_providers_near_you: failed after %.3fs | userprofile=%s lat=%s lon=%s | %s: %s",
            error_time - start_time, user_profile_id, latitude, longitude, type(e).__name__, e,
            exc_info=True,
        )
        raise e