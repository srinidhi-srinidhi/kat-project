"""Suggestions Endpoints - HTTP routes for people suggestions and mutual connections.

HTTP layer for /api/suggestions: delegates paginated member and provider suggestions, which exclude the caller's MyCircle, and mutual-connection analysis to suggestions_utils; only /mutual_connection checks the shared bearer token.

Features:
    - Paginated Suggestions: 20 members + 20 providers per page, providers sorted by distance
    - MyCircle Exclusion: Users/providers already in the caller's MyCircle are excluded
    - Mutual Connection Analysis: Viewed-profile-only connections plus connections both users share
    - Connection Status Enrichment: Etown_AlreadyCircle / Etown_AlreadyConnected flags per result
    - Performance Instrumentation: Server-Timing header with per-stage timings and cache-hit status

Endpoints:
    GET /search: Paginated search suggestions with geographic filtering
    GET /mutual_connection: Mutual connection analysis between users (suggestions + mutual_connections)

Classes:
    No classes defined (module exposes FastAPI router endpoints only)

Functions:
    search_suggestion(): Handle GET /search — paginated member+provider suggestions with MyCircle exclusion and geo sorting
    get_mutual_connections(): Handle GET /mutual_connection — mutual and viewer-only connections between two profiles

Dependencies:
    - fastapi: API framework, query parameters and JSON responses
    - app.core.security: verify_token bearer-token dependency (required on /mutual_connection)
    - app.core.logging: Centralized application logger
    - app.etown.suggestions.suggestions_utils: mutual_connection_status, handle_get_mutual_connections, get_search_suggestions_paginated

Updates:
    23-Feb-2026: Enhanced documentation with social network features

Notes:
    Combines member and provider relationship tables for complete social graphs.
    Implements intelligent filtering to avoid duplicates and respect privacy.
    Optimized for large-scale social network analysis with efficient algorithms.
"""

import time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, status, Depends
from fastapi.responses import JSONResponse

from app.core.security import verify_token
from app.core.logging import logger
from app.etown.suggestions.suggestions_utils import (
    mutual_connection_status,
    handle_get_mutual_connections,
    get_search_suggestions_paginated,
)

router = APIRouter(tags=["suggestions"])


@router.get("/search")
async def search_suggestion(
    current_contact_id: str = Query(..., description="The contact ID of the user performing the search."),
    current_user_profile_id: str = Query(..., description="The user profile ID of the user performing the search."),
    page: int = Query(1, ge=1, description="Page number for pagination (starts from 1)."),
    latitude: Optional[float] = Query(None, description="Latitude for distance-based provider sorting."),
    longitude: Optional[float] = Query(None, description="Longitude for distance-based provider sorting."),
):
    """Handle GET /api/suggestions/search: return a page of suggested members and nearby providers with connection flags and Server-Timing."""
    try:
        if latitude is not None and longitude is not None:
            if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid latitude: {latitude} or longitude: {longitude}. Latitude must be between -90 and 90, longitude must be between -180 and 180."
                )
        elif (latitude is None) != (longitude is None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Both latitude and longitude must be provided together, or both omitted."
            )
        
        logger.info(f"Search suggestion request - contact_id: {current_contact_id}, page: {page}")
        
        suggestions = await get_search_suggestions_paginated(
            current_contact_id,
            current_user_profile_id,
            page,
            latitude,
            longitude
        )
        
        timings = suggestions.pop("_timings", {})
        status_ready = suggestions.pop("_status_ready", False)
        if status_ready:
            suggestions_with_status = suggestions
        else:
            suggestions_with_status = await mutual_connection_status(
                suggestions,
                current_contact_id,
                current_user_profile_id
            )
        
        members = suggestions_with_status.get("members", [])
        providers = suggestions_with_status.get("providers", [])
        
        logger.info(f"Search suggestions returned - members: {len(members)}, providers: {len(providers)}")
        
        response_payload = {
            "members": members,
            "providers": providers
        }

        if timings:
            parts = []
            if timings.get("cache_hit"):
                parts.append("cache;desc=hit")
            for key in ("first_deg", "second_deg", "third_deg", "fallback", "filter", "member_details", "provider_query", "total"):
                if key in timings:
                    parts.append(f"{key};dur={timings[key]:.1f}")
            headers = {"Server-Timing": ", ".join(parts)}
            return JSONResponse(status_code=200, content=response_payload, headers=headers)

        return response_payload
        
    except Exception as e:
        logger.error(f"Error in search_suggestion: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch search suggestions: {str(e)}"
        )


@router.get("/mutual_connection", response_model=Dict[str, List[Dict[str, Any]]])
async def get_mutual_connections(
    current_contact_id: str = Query(..., description="The contact ID of the current user."),
    current_user_profile_id: str = Query(..., description="The user profile ID of the current user."),
    viewer_contact_id: str = Query(..., description="The contact ID of the user whose profile is being viewed."),
    viewer_user_profile_id: str = Query(..., description="The user profile ID of the user whose profile is being viewed."),
    token: str = Depends(verify_token),
):
    """Handle GET /api/suggestions/mutual_connection: return the viewed user's circle not yet in mine, plus our shared connections."""
    t0 = time.time()
    try:
        suggestions_result = await handle_get_mutual_connections(
            current_contact_id,
            current_user_profile_id,
            viewer_contact_id,
            viewer_user_profile_id,
        )
        
        duration_ms = (time.time() - t0) * 1000
        if duration_ms > 2000:
            logger.warning(f"Slow /getsuggestions query: {duration_ms:.1f}ms for user {current_contact_id}")
        else:
            logger.info(f"/getsuggestions query completed in {duration_ms:.1f}ms for user {current_contact_id}")
            
        return {
            "suggestions": suggestions_result.get("suggestions", []),
            "mutual_connections": suggestions_result.get("mutual_connections", []),
        }

    except Exception as e:
        logger.error(f"Mutual suggestions query failed for user {current_contact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="An error occurred while fetching mutual suggestions."
        )
