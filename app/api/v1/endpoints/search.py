"""Search Endpoints - HTTP routes for AI provider search, typeahead, search events and history.

HTTP layer for /api/search: runs v1 vector search and the semaphore-guarded single-statement v2 search, serves typeahead suggestions, records impressions and clicks, and returns search history; every route except v1 /aisearch checks the shared bearer token.

Features:
    - Vector search: Gemini-embedding similarity (v1) and single-statement semantic + exact-name (v2)
    - Auto-correction: v1 corrects misspelled business/service names before searching
    - Load shedding: v2 caps concurrent searches with a semaphore and returns 503 when saturated
    - Geographic sorting: location-based ordering; v2 falls back to the caller's stored coordinates
    - Typeahead suggestions: /suggest returns fast, cheap suggestions for the search box
    - Search analytics: /event logs impressions/clicks for later CTR-based ranking
    - Appearance logging: v1 and v2 record which providers each result page surfaced
    - Search history: queries are logged in the background and read back via /history
    - Performance monitoring: Search execution time tracking and cache-hit reporting
    - Social filtering: MyCircle-based result filtering

Endpoints:
    GET /aisearch: AI-powered semantic search with vector similarity (v1)
    GET /ai_search_v2: v2 search — single-statement semantic + exact-name search, auth required
    GET /suggest: Typeahead suggestions for the search box, auth required
    POST /event: Record search impressions/clicks for later CTR-based ranking, auth required
    GET /history: Return the caller's recent distinct searches, newest first, auth required

Classes:
    SearchEventItem(): One result a user saw or clicked (providerid/userprofileid/position)
    SearchEventRequest(): Impressions or clicks for one result page, posted to /event

Functions:
    _build_aisearch_payload(): Build the response envelope (status/query/correction/results/performance) shared by v1 and v2
    _run_aisearch_endpoint(): Time and run the v1 aisearch call, wrapping the result in the response envelope
    _run_aisearch_v2_endpoint(): Run the v2 aisearch call behind the concurrency-limited admission semaphore
    aisearch_endpoint(): Main AI search with vector and fallback capabilities
    ai_search_v2_endpoint(): v2 search with load shedding and truthful degradation reporting
    suggest_endpoint(): Fast typeahead suggestions from one indexed statement plus an in-memory gazetteer
    search_event_endpoint(): Record a page of impressions or clicks; logging failures never fail the caller
    search_history_endpoint(): Return the caller's recent searches, newest first

Dependencies:
    - fastapi: API framework, query parameters, JSON responses and BackgroundTasks
    - asyncio: Semaphore-based admission control and timeout for v2 concurrency limiting
    - pydantic: BaseModel/Field models backing the /event request payload
    - app.core.config: v2 admission-control settings (max concurrent searches, admission wait)
    - app.core.security: Bearer-token dependency for v2, /suggest, /event and /history
    - app.etown.search.aisearch_utils: v1 AI search implementation
    - app.etown.search.aisearch_v2_utils: v2 search, query validation, suggest, and event logging
    - app.etown.search.search_appearance_utils: provider search-appearance logging
    - app.etown.search.search_history_utils: recent-search logging and the /history read
    - app.schemas.api: Search response models and metrics
    - app.services.api_activity: track_api_call() activity recording

Updates:
    23-Feb-2026: Enhanced documentation with AI search capabilities
    03-Aug-2026: v2 reworked - real coordinates, bearer token, capped concurrency, validated ranges.
    04-Sep-2026: Search-history logging moved from a bare asyncio task to BackgroundTasks.
    17-Sep-2026: v1 and v2 log provider search appearances.

Notes:
    Uses minimum similarity threshold of 0.15 for vector search accuracy.
    Supports geographic bounding for location-aware search optimization.
"""
import asyncio
import logging
import time
from fastapi import APIRouter, BackgroundTasks, Depends, Query, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import verify_token
from app.etown.search.aisearch_utils import aisearch
from app.etown.search.aisearch_v2_utils import (
    validate_query, aisearch as aisearch_v2, log_search_events, suggest,
)
from app.etown.search.search_appearance_utils import log_provider_search_appearances
from app.etown.search.search_history_utils import (
    get_search_history,
    log_search_history,
)
from app.schemas.api import AISearchResponse
from app.services.api_activity import track_api_call

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])

_v2_admission = asyncio.Semaphore(settings.AISEARCH_MAX_CONCURRENT_SEARCHES)


def _build_aisearch_payload(
    *,
    query: str,
    original_query_input: str,
    results: dict,
    response_time: float,
    endpoint_name: str,
) -> dict:
    """Pop correction fields off the search results and wrap them in the AI search response payload with performance data."""
    correction_applied = results.pop("correction_applied", False)
    correction_confidence = results.pop("correction_confidence", 0.0)
    results_original_query = results.pop("original_query", query)
    metrics = results.get("performance_metrics") or {}

    if correction_applied:
        final_used_query = results_original_query
        user_original_query = original_query_input
    else:
        final_used_query = query
        user_original_query = original_query_input

    return {
        "status": "success",
        "query": final_used_query,
        "original_query": user_original_query,
        "translation_applied": bool(results.get("query_was_translated", False)),
        "detected_language": "en",
        "correction_applied": correction_applied,
        "correction_confidence": correction_confidence,
        "results": results,
        "performance": {
            "response_time_seconds": round(response_time, 3),
            "endpoint_name": endpoint_name,
            "cache_hit": bool(metrics.get("embedding_cache_hit", False)),
            "degraded": bool(metrics.get("degraded", False)),
        },
    }


async def _run_aisearch_endpoint(
    *,
    background_tasks: BackgroundTasks,
    query: str,
    contact_id: str,
    user_profile_id: str,
    mycircle: bool,
    limit: int,
    offset: int,
    latitude: Optional[float],
    longitude: Optional[float],
    accept_corrections: bool,
    include_core: bool,
    include_meta: bool,
    include_enrichment: bool,
    resolve_missing_coordinates: bool,
    endpoint_name: str,
) -> JSONResponse:
    """Run v1 AI search, schedule history and appearance logging, and return the JSON payload; 500 on failure."""
    start_time = time.time()
    try:
        results = await aisearch(
            search_query=query,
            limit=limit,
            offset=offset,
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            mycircle=mycircle,
            latitude=latitude,
            longitude=longitude,
            enable_auto_correct=accept_corrections,
            include_core=include_core,
            include_meta=include_meta,
            include_enrichment=include_enrichment,
            resolve_missing_coordinates=resolve_missing_coordinates,
        )
        background_tasks.add_task(
            log_search_history,
            contact_id=contact_id,
            query=query,
        )
        background_tasks.add_task(
            log_provider_search_appearances,
            results.get("provider"),
            query,
            contact_id,
            "aisearch",
            offset,
        )

        response_payload = _build_aisearch_payload(
            query=query,
            original_query_input=query,
            results=results,
            response_time=time.time() - start_time,
            endpoint_name=endpoint_name,
        )
        return JSONResponse(
            content=jsonable_encoder(response_payload),
            headers={"X-Performance-Injected": "1"},
        )
    except Exception as e:
        response_time = time.time() - start_time
        logger.error("%s error after %.3fs: %s", endpoint_name, response_time, str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


async def _run_aisearch_v2_endpoint(
    *,
    background_tasks: BackgroundTasks,
    query: str,
    contact_id: str,
    user_profile_id: str,
    mycircle: bool,
    limit: int,
    offset: int,
    latitude: Optional[float],
    longitude: Optional[float],
    endpoint_name: str,
) -> JSONResponse:
    """Run v2 AI search under the admission semaphore, returning 503 when busy, else the logged JSON payload."""
    start_time = time.time()

    try:
        await asyncio.wait_for(
            _v2_admission.acquire(),
            timeout=settings.AISEARCH_ADMISSION_WAIT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "%s shed load — %d concurrent searches already running",
            endpoint_name,
            settings.AISEARCH_MAX_CONCURRENT_SEARCHES,
        )
        return JSONResponse(
            content={"status": "busy", "detail": "Search is busy, retry shortly."},
            status_code=503,
            headers={"Retry-After": "1"},
        )

    try:
        results = await aisearch_v2(
            search_query=query,
            limit=limit,
            offset=offset,
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            mycircle=mycircle,
            latitude=latitude,
            longitude=longitude,
        )
        background_tasks.add_task(
            log_search_history,
            contact_id=contact_id,
            query=query,
        )
        background_tasks.add_task(
            log_provider_search_appearances,
            results.get("provider"),
            query,
            contact_id,
            "ai_search_v2",
            offset,
        )

        response_payload = _build_aisearch_payload(
            query=query,
            original_query_input=query,
            results=results,
            response_time=time.time() - start_time,
            endpoint_name=endpoint_name,
        )
        return JSONResponse(
            content=jsonable_encoder(response_payload),
            status_code=200,
            headers={"X-Response-Time": str(round(time.time() - start_time, 3))}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI search v2 endpoint error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
    finally:
        _v2_admission.release()


@router.get("/aisearch", response_model=AISearchResponse)
async def aisearch_endpoint(
    background_tasks: BackgroundTasks,
    query: str = Query(..., description="Search query"),
    contact_id: str = Query(..., description="Current user's contact ID"),
    user_profile_id: str = Query(..., description="Current user's profile ID"),
    mycircle: bool = Query(False, description="Filter results to only show users in My Circle"),
    limit: int = Query(10, description="Maximum number of results"),
    offset: int = Query(0, description="Number of results to skip"),
    latitude: float = Query(None, description="User's latitude for distance-based provider sorting"),
    longitude: float = Query(None, description="User's longitude for distance-based provider sorting"),
    accept_corrections: bool = Query(True, description="Enable auto-correction for misspelled queries"),
):
    """Handle GET /api/search/aisearch: run v1 AI search with optional auto-correction over members and providers."""
    return await _run_aisearch_endpoint(
        background_tasks=background_tasks,
        query=query,
        contact_id=contact_id,
        user_profile_id=user_profile_id,
        mycircle=mycircle,
        limit=limit,
        offset=offset,
        latitude=latitude,
        longitude=longitude,
        accept_corrections=accept_corrections,
        include_core=True,
        include_meta=True,
        include_enrichment=True,
        resolve_missing_coordinates=True,
        endpoint_name="search_aisearch",
    )


@router.get("/ai_search_v2", response_model=AISearchResponse)
async def ai_search_v2_endpoint(
    background_tasks: BackgroundTasks,
    query: str = Query(..., description="Search query"),
    contact_id: str = Query(..., description="Current user's contact ID"),
    user_profile_id: str = Query(..., description="Current user's profile ID"),
    mycircle: bool = Query(False, description="Filter results to only show users in My Circle"),
    limit: int = Query(10, ge=1, le=50, description="Maximum providers and maximum members to return"),
    offset: int = Query(0, ge=0, description="Number of ranked candidates to skip"),
    latitude: Optional[float] = Query(None, ge=-90, le=90, description="User's latitude; resolved from the profile when omitted"),
    longitude: Optional[float] = Query(None, ge=-180, le=180, description="User's longitude; resolved from the profile when omitted"),
    token: str = Depends(verify_token),
):
    """Handle GET /api/search/ai_search_v2: validate the query, track the call, and run v2 semantic search."""
    query = validate_query(query)
    track_api_call(background_tasks, "/api/search/ai_search_v2", contact_id, user_profile_id)

    return await _run_aisearch_v2_endpoint(
        background_tasks=background_tasks,
        query=query,
        contact_id=contact_id,
        user_profile_id=user_profile_id,
        mycircle=mycircle,
        limit=limit,
        offset=offset,
        latitude=latitude,
        longitude=longitude,
        endpoint_name="search_ai_search_v2",
    )


@router.get("/suggest", dependencies=[Depends(verify_token)])
async def suggest_endpoint(
    q: str = Query(..., min_length=1, description="Partial query typed so far"),
    limit: int = Query(8, ge=1, le=20, description="Maximum suggestions"),
):
    """Handle GET /api/search/suggest: return typeahead suggestions for a partial query."""
    started = time.time()
    items = await suggest(q, limit)
    return JSONResponse(
        content={"suggestions": items},
        headers={"X-Response-Time": str(round(time.time() - started, 3))},
    )


class SearchEventItem(BaseModel):
    """One result a user saw or clicked."""
    providerid: Optional[str] = None
    userprofileid: Optional[str] = None
    position: Optional[int] = Field(default=None, ge=0)


class SearchEventRequest(BaseModel):
    """Impressions or clicks for one result page."""
    event_type: str = Field(..., pattern="^(impression|click)$")
    query: str = Field(..., min_length=1)
    items: List[SearchEventItem] = Field(..., min_length=1, max_length=100)
    contact_id: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)


@router.post("/event", dependencies=[Depends(verify_token)])
async def search_event_endpoint(payload: SearchEventRequest):
    """Handle POST /api/search/event: record a page of search impressions or clicks and return the count."""
    recorded = await log_search_events(
        payload.event_type,
        payload.query,
        [item.model_dump() for item in payload.items],
        contact_id=payload.contact_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    return JSONResponse(content={"recorded": recorded})


@router.get("/history", dependencies=[Depends(verify_token)])
async def search_history_endpoint(
    contact_id: str = Query(..., description="Current user's contact ID"),
    user_profile_id: str = Query(..., description="Current user's profile ID"),
    limit: int = Query(10, ge=1, le=10, description="Number of recent searches to return"),
):
    """Handle GET /api/search/history: return up to 10 recent searches for the user, newest first."""
    history = await get_search_history(
        contact_id=contact_id,
        user_profile_id=user_profile_id,
        limit=limit,
    )
    return JSONResponse(content={"history": history})
