"""Services Endpoints - HTTP routes for AI business content, provider details and geocoding.

HTTP layer for /api/services: validates requests and delegates to services_utils for LLM service, category and description generation, mobile and web provider details, Google Maps geocoding, town descriptions and the popular-providers feed, the only route here gated by the shared bearer token.

Features:
    - Service Generation: AI-powered service/product suggestions from a business description
    - Category Classification: Business type + subcategory, and a broader general category, via LLM
    - Business Enhancement: AI-driven description rewrite, sized against the description given
    - Provider Details: Aggregated provider, sub-service and feed data for mobile and web clients
    - Location Services: Geocoding and reverse geocoding via the Google Maps API
    - Public Provider Integration: Google Place ID -> AI-generated service/product listing
    - Town Details: AI-generated short description for a town/city/state
    - Popular Providers Feed: Category-paginated, distance-filtered discovery with social proof
    - Data Validation: Input verification with structured error responses

Endpoints:
    POST /generate_services: AI-powered service suggestion from business description
    POST /generate_category: Automated business category classification
    POST /generate_generalcategory: Broad, generic (1-2 word) business category classification
    POST /enhance_business_description: AI-driven description improvement
    GET /provider_details: Comprehensive provider information retrieval (mobile)
    GET /web_provider_details: Comprehensive provider information retrieval (web, lookup by name/ID)
    GET /location_details: Reverse geocoding for coordinate resolution
    GET /geocode_address: Address to coordinate conversion
    POST /generate_public_provider_details: External provider integration via Google Place ID
    GET /generate_towndetails: AI-powered town description generation
    GET /popular_providers: Nearby popular providers for the All tab plus the categories list, auth required

Classes:
    No classes defined (module exposes FastAPI router endpoints only)

Functions:
    generate_services(): Process a business description for AI-suggested services/products
    generate_category(): Classify a business description into business_type + subcategory via LLM
    generate_general_category(): Classify a business description into a broad general category
    enhance_business_description(): Improve a business description with AI, as a full paragraph
    get_mobile_provider_details(): Retrieve comprehensive provider information (mobile clients)
    get_web_provider_details(): Retrieve comprehensive provider information (web, by name/ID/contact)
    location_details(): Convert coordinates to location information
    geocode_address(): Transform addresses to coordinates
    generate_public_provider_details(): Integrate external provider data from a Google Place ID
    generate_towndetails(): Create AI-powered town descriptions
    get_popular_providers_near_you(): Nearby popular providers for the All tab plus the categories list

Dependencies:
    - fastapi: API framework, query parameters and HTTP exception handling
    - app.core.security: verify_token bearer-token dependency (used by /popular_providers)
    - app.etown.services.services_utils: Business logic and AI integration behind every endpoint
    - app.schemas.api: Service API request/response models and validation

Updates:
    23-Feb-2026: Enhanced documentation with complete service feature set
    04-Mar-2026: Update provider details endpoint to include viewer relationship checks
    13-Mar-2026: Added generate_generalcategory endpoint for broad business category classification
    21-Sep-2026: enhance_business_description accepts business_name/place_types/reviews; length scales with input.
    07-Oct-2026: enhance_business_description takes type: business_description, need, event or update.
    26-Oct-2026: GET /popular_providers returns categories, with Etown_pinned for the request's location, and the All tab's providers shaped as /mytown_suggestions providers; takes language and category_id; page and limit page providers.

Notes:
    Integrates with Azure OpenAI GPT-4 for intelligent content generation.
    Supports multiple external APIs for comprehensive provider data aggregation.
    Includes comprehensive location services with geocoding capabilities.
"""

import logging
from typing import List, Literal, Optional
from uuid import UUID
from fastapi import APIRouter, HTTPException, status, Query, Depends
from app.core.security import verify_token

from app.schemas.api import (
    PopularProvidersResponse,
    ServiceGenerationValidation,
    ServiceGenerationResponse,
    CategoryGenerationRequest,
    CategoryGenerationResponse,
    GeneralCategoryGenerationRequest,
    GeneralCategoryGenerationResponse,
    BusinessDescriptionRequest,
    BusinessDescriptionResponse,
    ProviderDetailsResponse,
    LocationDetailsResponse,
    GeocodingResponse,
    PublicProviderDetailsGeneratorRequest,
    PublicProviderDetailsResponse,
    GenerateTownDetailsResponse,
)
from app.etown.services.services_utils import (
    generate_public_provider_details_logic,
    generate_services_logic,
    generate_category_logic,
    generate_general_category_logic,
    enhance_business_description_logic,
    resolve_description_word_cap,
    resolve_description_word_floor,
    get_provider_details_logic,
    get_web_provider_details_logic,
    get_location_details_logic,
    get_geocoding_logic,
    generate_towndetails_logic,
    get_popular_providers_near_you_logic,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["services"])


@router.post("/generate_services", response_model=ServiceGenerationResponse)
async def generate_services(config: ServiceGenerationValidation):
    """Handle POST /api/services/generate_services: recommend services or products for a business description via the LLM.

    Raises:
        HTTPException: 422 when the LLM response cannot be parsed, 500 on any other failure.
    """
    try:
        return await generate_services_logic(config.business_description)
        
    except ValueError as e:
        logger.error(f"Failed to parse LLM response: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "error",
                "message": "Failed to parse LLM response",
                "details": str(e)
            }
        )
    except Exception as e:
        logger.error(f"Service generation failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Service generation failed",
                "details": str(e)
            }
        )

@router.post("/generate_category", response_model=CategoryGenerationResponse)
async def generate_category(config: CategoryGenerationRequest):
    """Handle POST /api/services/generate_category: classify a description's business type and subcategory via the LLM.

    Raises:
        HTTPException: 422 when the LLM response cannot be parsed, 500 on any other failure.
    """
    try:
        return await generate_category_logic(config.description)

    except ValueError as e:
        logger.error(f"Failed to parse LLM response: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "error",
                "message": "Failed to parse LLM response",
                "details": str(e)
            }
        )
    except Exception as e:
        logger.error(f"Category generation failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Category generation failed",
                "details": str(e)
            }
        )


@router.post("/generate_generalcategory", response_model=GeneralCategoryGenerationResponse)
async def generate_general_category(config: GeneralCategoryGenerationRequest):
    """Handle POST /api/services/generate_generalcategory: derive a broad one-to-two-word category for a description via the LLM.

    Raises:
        HTTPException: 422 when the LLM response cannot be parsed, 500 on any other failure.
    """
    try:
        return await generate_general_category_logic(config.description)

    except ValueError as e:
        logger.error(f"Failed to parse LLM response for general category: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "error",
                "message": "Failed to parse LLM response for general category",
                "details": str(e)
            }
        )

    except Exception as e:
        logger.error(f"General category generation failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "General category generation failed",
                "details": str(e)
            }
        )


@router.post("/enhance_business_description", response_model=BusinessDescriptionResponse)
async def enhance_business_description(config: BusinessDescriptionRequest):
    """Handle POST /api/services/enhance_business_description: rewrite a business, need, event or update description via the LLM.

    Raises:
        HTTPException: 500 when enhancement fails.
    """
    try:
        word_cap = resolve_description_word_cap(config.description)
        return await enhance_business_description_logic(
            config.description,
            business_name=config.business_name,
            place_types=config.place_types,
            reviews=config.reviews,
            word_cap=word_cap,
            min_words=resolve_description_word_floor(config.description, word_cap),
            description_type=config.type,
        )
        
    except Exception as e:
        logger.error(f"Business description enhancement failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Enhancement failed",
                "details": str(e)
            }
        )

@router.get("/provider_details", response_model=ProviderDetailsResponse)
async def get_mobile_provider_details(
    contactid: str = Query(..., description="The provider's contact ID"),
    userprofileid: str = Query(..., description="The provider's user profile ID"),
    viewercontactid: Optional[str] = Query(None, description="The viewer's contact ID for relationship checks"),
    vieweruserprofileid: Optional[str] = Query(None, description="The viewer's user profile ID for relationship checks"),
    feedpage: int = Query(1, ge=1, description="Page number for feeds"),
    feedlimit: int = Query(10, ge=1, description="Number of feeds per page")
):
    """Handle GET /api/services/provider_details: return a provider's profile, sub-services and paged feeds for an optional viewer."""
    try:
        return await get_provider_details_logic(
            contactid, userprofileid, feedpage, feedlimit, viewercontactid, vieweruserprofileid
        )

    except Exception as e:
        logger.error(f"Error in get_provider_details: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch provider details: {str(e)}"
        )

@router.get("/web_provider_details", response_model=ProviderDetailsResponse)
async def get_web_provider_details(
    contact_id: str = Query(None, description="The provider's contact ID"),
    user_profile_id: str = Query(None, description="The provider's user profile ID"),
    provider_name: str = Query(None, description="The provider's business name"),
    provider_id: str = Query(None, description="The provider's unique ID"),
    feed_page: int = Query(1, ge=1, description="Page number for feeds"),
    feed_limit: int = Query(10, ge=1, description="Number of feeds per page")
):
    """Handle GET /api/services/web_provider_details: return provider details looked up by ids, provider id or business name."""
    try:
        return await get_web_provider_details_logic(
            contact_id=contact_id, 
            user_profile_id=user_profile_id, 
            provider_name=provider_name, 
            provider_id=provider_id,
            feed_page=feed_page, 
            feed_limit=feed_limit
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"Error in get_provider_details: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch provider details: {str(e)}"
        )

@router.get("/location_details", response_model=LocationDetailsResponse)
async def location_details(
    latitude: float = Query(..., description="Location latitude"),
    longitude: float = Query(..., description="Location longitude")
):
    """Handle GET /api/services/location_details: reverse-geocode coordinates into address components and raw Google data."""
    try:
        return await get_location_details_logic(latitude, longitude)
    except Exception as e:
        logger.error(f"Error fetching location details: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=str(e)
        )

@router.get("/geocode_address", response_model=GeocodingResponse)
async def geocode_address(
    address: str = Query(..., description="Address to geocode", min_length=1)
):
    """Handle GET /api/services/geocode_address: resolve an address to coordinates and a formatted address."""
    try:
        return await get_geocoding_logic(address)
    except ValueError as e:
        logger.warning(f"Geocoding validation error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error geocoding address: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Internal server error during geocoding"
        )

@router.post("/generate_public_provider_details", response_model=PublicProviderDetailsResponse)
async def generate_public_provider_details(
    config: PublicProviderDetailsGeneratorRequest
):
    """Handle POST /api/services/generate_public_provider_details: generate offerings for a Google place id from its place details."""
    try:
        return await generate_public_provider_details_logic(config.place_id)
    except ValueError as e:
        logger.warning(f"Validation error in generate_public_provider_details: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error in generate_public_provider_details: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate provider details"
        )
    
@router.get("/generate_towndetails", response_model=GenerateTownDetailsResponse)
async def generate_towndetails(
    townname: str = Query(..., description="Town name"),
    cityname: str = Query(..., description="City name"),
    statecode: str = Query(..., description="State code")
):
    """Handle GET /api/services/generate_towndetails: return town details with a short AI-generated description."""
    try:
        return await generate_towndetails_logic(townname, cityname, statecode)
    except Exception as e:
        logger.error(f"Error fetching town details: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=str(e)
        )
    
@router.get("/popular_providers", response_model=PopularProvidersResponse)
async def get_popular_providers_near_you(
    contact_id: str = Query(..., description="User's contact ID"),
    user_profile_id: str = Query(..., description="User's profile ID"),
    latitude: float = Query(..., description="User's latitude", ge=-90, le=90),
    longitude: float = Query(..., description="User's longitude", ge=-180, le=180),
    page: int = Query(1, description="Page number for providers", ge=1),
    limit: int = Query(20, description="Number of providers per page", ge=1, le=100),
    max_distance_km: float = Query(25.0, description="Maximum distance in kilometers", gt=0, le=25),
    max_providers_per_category: int = Query(20, description="Maximum providers per category", ge=1, le=20),
    language: Literal["en", "ta"] = Query("en", description="en or ta; ta returns each category's Tamil display name, falling back to English when none is stored."),
    category_id: Optional[List[str]] = Query(None, description="etown_category ids, comma-separated or repeated; when given, only providers in any of those categories are returned and categories is empty."),
    token: str = Depends(verify_token)

):
    """Handle GET /api/services/popular_providers: return the categories list and the All tab's nearby providers, or only providers in category_id.

    Raises:
        HTTPException: 400 on validation errors, 422 on a malformed category_id, 500 on any other failure.
    """
    category_ids: List[str] = []
    for raw in category_id or []:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                parsed = str(UUID(part))
            except ValueError:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid category_id: {part}")
            if parsed not in category_ids:
                category_ids.append(parsed)

    try:
        logger.info(f"Popular providers request for user {user_profile_id} at ({latitude}, {longitude})")
        
        result = await get_popular_providers_near_you_logic(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            latitude=latitude,
            longitude=longitude,
            page=page,
            limit=limit,
            max_distance_km=max_distance_km,
            max_providers_per_category=max_providers_per_category,
            language=language,
            category_ids=category_ids or None
        )
        
        return result
        
    except ValueError as e:
        logger.warning(f"Validation error in popular_providers: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error in popular_providers endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch popular providers"
        )
