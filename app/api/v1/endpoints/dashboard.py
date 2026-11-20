"""Dashboard Endpoints - HTTP routes for provider and member dashboards, invitations, near-me and profile switching.

HTTP layer for /api/dashboard: checks the shared bearer token and delegates provider and member dashboard aggregation, pending invitations, the near-me circle list and profile switching to dashboard_utils, pending_invite_utils, nearme_utils and switch_profile_utils.

Features:
    - Provider Dashboard Aggregation: concurrent details, feed, offers, insights, boost status and towns
    - Member Dashboard: Aggregated invite/request counts and details for a member
    - Pending Invitations: Internal invitations awaiting the authenticated user's response
    - Near Me List: Paginated, 25 km-capped list of the requester's circle providers by distance
    - Switch Profile: creates a secondary member or provider profile, synced to Dataverse in background
    - Plan Limits: /switch_profile returns 402 when a plan cap refuses the new profile
    - Performance Tracking: PerformanceCollector/PerformanceTimer instrumentation on /pending_invitation
    - API Activity: every endpoint records its call via track_api_call
    - Bearer Auth: Endpoints are gated by the shared verify_token dependency

Classes:
    No classes defined (SwitchProfileRequest is imported from app.schemas.api)

Endpoints:
    GET /provider_dashboard: Retrieve provider dashboard data
    GET /pending_invitation: Fetch all pending Internal invitations for the authenticated user
    GET /member_dashboard: Retrieve member dashboard data
    GET /nearme_list: List the requester's circle providers within 25 km, nearest first
    POST /switch_profile: Create a secondary member or provider profile for an existing contact

Functions:
    get_provider_dashboard(): Handle GET /provider_dashboard endpoint
    get_pending_invitations(): Handle GET /pending_invitation endpoint
    get_member_dashboard(): Handle GET /member_dashboard endpoint
    nearme_list(): Handle GET /nearme_list endpoint
    switch_profile(): Handle POST /switch_profile endpoint

Dependencies:
    - fastapi: Web framework for API endpoint definition and validation
    - app.etown.dashboard.dashboard_utils: Business logic for dashboard data
    - app.etown.dashboard.pending_invite_utils: fetch_internal_invitations() for pending invites
    - app.etown.dashboard.nearme_utils: Near Me List geographic query logic
    - app.etown.offers.offers_utils: is_provider_boosted() feeding the Offervalidity flag
    - app.etown.user.switch_profile_utils: DB logic for creating secondary profiles
    - app.etown.user.users_syncing: Dataverse background sync for switch profile
    - app.utils.performance: PerformanceCollector/PerformanceTimer for endpoint timing metrics
    - app.etown.plans.plans_utils: PlanLimitError raised by profile creation
    - app.services.api_activity: track_api_call() activity recording
    - app.core.security: verify_token bearer dependency
    - app.schemas.api: Request/response Pydantic models
    - app.core.logging: Centralized logging infrastructure

Updates:
    08-May-2026: Initial creation with provider_dashboard endpoint
    02-Jul-2026: Added GET /nearme_list and POST /switch_profile (migrated from PowerApps).
    27-Jul-2026: /nearme_list lists the requester's circle providers within 25 km by distance; 1-based pagination; default limit 100.
    28-Jul-2026: /nearme_list default `limit` reduced to 10.
    20-Aug-2026: /pending_invitation records include Etown_providertype.
    25-Aug-2026: /provider_dashboard returns a `towns` list flagged by is_primary.
    01-Sep-2026: /provider_dashboard `towns` includes the own town during /onboard.
    02-Sep-2026: /provider_dashboard `towns` capped at the plan's max_towns.
    07-Oct-2026: /provider_dashboard `towns` entries carry need_count and new_need_count since the last community visit.
"""

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Depends
from fastapi.responses import JSONResponse

from app.etown.dashboard.dashboard_utils import fetch_feed_details, fetch_offers, fetch_provider_details, fetch_provider_towns, fetch_member_dashboard, fetch_social_insights_totals
from app.etown.offers.offers_utils import is_provider_boosted
from app.etown.dashboard.pending_invite_utils import fetch_internal_invitations
from app.etown.dashboard.nearme_utils import get_nearme_list
from app.etown.user.switch_profile_utils import create_member_profile, create_provider_profile
from app.etown.plans.plans_utils import PlanLimitError
from app.etown.user.users_syncing import sync_switch_profile_to_dataverse
from app.schemas.api import SwitchProfileRequest
from app.services.api_activity import track_api_call
from app.utils.performance import PerformanceCollector, PerformanceTimer
from app.core.security import verify_token
from app.core.logging import logger

router = APIRouter()


@router.get(
    "/provider_dashboard",
    summary="Get Provider Dashboard",
    description="Retrieve dashboard data for an authenticated provider",
    responses={
        200: {
            "description": "Provider dashboard data retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "Providerdetails": {},
                        "Feeddetails": {},
                        "Offers": {},
                        "towns": []
                    }
                }
            }
        },
        401: {
            "description": "Unauthorized - invalid or missing access token",
            "content": {
                "application/json": {
                    "example": {"detail": "Unauthorized"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve provider dashboard"}
                }
            }
        }
    }
)
async def get_provider_dashboard(
    contactid: str = Query(..., description="The provider's contact ID"),
    userprofileid: str = Query(..., description="The provider's user profile ID"),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/dashboard/provider_dashboard: return a provider's details, feed, offers, social insights and towns."""
    track_api_call(
        background_tasks, "/api/dashboard/provider_dashboard", contactid, userprofileid
    )

    try:
        logger.info(f"Provider dashboard request | contactid={contactid} | userprofileid={userprofileid}")

        provider_details, offers, feed_details, social_insights, boosted, towns = await asyncio.gather(
            fetch_provider_details(contactid, userprofileid),
            fetch_offers(userprofileid),
            fetch_feed_details(contactid, userprofileid),
            fetch_social_insights_totals(contactid, userprofileid),
            is_provider_boosted(userprofileid),
            fetch_provider_towns(contactid, userprofileid),
        )

        return JSONResponse(
            status_code=200,
            content={
                "Providerdetails": provider_details,
                "Feeddetails": feed_details,
                "Offers": offers,
                "SocialInsights": social_insights,
                "towns": towns,
                "Offervalidity": "true" if (offers and not boosted) else "false",
            }
        )

    except Exception as e:
        logger.error(f"Provider dashboard failed | contactid={contactid} | error={e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve provider dashboard"
        )

@router.get(
    "/pending_invitation",
    summary="Get Pending Invitations",
    description="Fetch all pending Internal invitations for the authenticated user",
    responses={
        200: {
            "description": "Pending invitations retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "data": [
                            {
                                "Etown_invitecontact": "70efbbb9-6753-f111-bec7-000d3a592dc2",
                                "Etown_invitecontactuserprofile": "79a913b6-6753-f111-bec7-000d3a5926f8",
                                "Etown_inviterole": "2",
                                "Etown_invitationtype": "Internal",
                                "Etown_invitationid": "0840ec41-6554-f111-bec7-000d3a592dc2",
                                "Etown_invitationcreatedon": "2026-05-20T16:02:14",
                                "Etown_publicsearchprovider": "verified",
                                "Etown_providertype": "etown_verified",
                                "Etown_role": 1,
                                "Etown_invitationcode": "Accepted",
                                "Etown_imageurl": None,
                                "Etown_contactinfo": "8281530034",
                                "Etown_inviterolename": None,
                                "Etown_name": "puma",
                                "Etown_invitercontact": "7b0ea305-5d54-f111-bec6-70a8a59c99ae",
                                "Etown_invitercontactuserprofile": "b1e03f5d-6354-f111-bec7-000d3a592dc2",
                                "Etown_receivername": "praveen",
                                "Etown_invitationcount": 1
                            },
                            {
                                "Etown_invitationid": "54360200-7154-f111-bec6-70a8a59c99ae",
                                "Etown_Invitationtype": "External",
                                "Etown_invitationSuffixId": "ETEI_CyVunSFFGCkiVKuT",
                                "Etown_invitercontactId": "7b0ea305-5d54-f111-bec6-70a8a59c99ae",
                                "Etown_inviteruserprofileId": "2aec6404-5d54-f111-bec7-000d3a592dc2",
                                "Etown_invitername": "Asiff",
                                "Etown_inviterrole": 2,
                                "Etown_inviterrolename": "Member",
                                "Etown_relationshipname": "Member",
                                "Etown_relationshipvalue": 2,
                                "Etown_invitecontactId": "0cbbe26c-4954-f111-bec7-000d3a5926f8",
                                "Etown_inviteuserprofileId": "5c2cd96e-4954-f111-bec7-000d3a37bee9",
                                "Etown_inviteename": "VK",
                                "Etown_invitationcode": "Requested",
                                "Etown_inviterprofileImage": None
                            }
                        ],
                        "performance": {
                            "response_time_seconds": 0.378,
                            "database_time_ms": 372.4,
                            "external_api_time_ms": 0,
                            "cache_hit": False,
                            "timestamp": "2026-05-20T18:33:51.166913+00:00",
                            "endpoint_name": "dashboard_pending_invitation"
                        }
                    }
                }
            }
        },
        401: {
            "description": "Unauthorized - invalid or missing access token",
            "content": {
                "application/json": {
                    "example": {"detail": "Unauthorized"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve pending invitations"}
                }
            }
        }
    }
)
async def get_pending_invitations(
    contactid: str = Query(..., description="The user's contact ID"),
    userprofileid: str = Query(..., description="The user's user profile ID"),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/dashboard/pending_invitation: return internal invitations where the user is invitee, with timing metrics."""
    collector = PerformanceCollector("dashboard_pending_invitation")
    collector.start_timing()

    track_api_call(
        background_tasks, "/api/dashboard/pending_invitation", contactid, userprofileid
    )

    try:
        logger.info(f"Pending invitations request | contactid={contactid} | userprofileid={userprofileid}")

        async with PerformanceTimer("fetch_pending_invitations", category="database"):
            internal_result = await fetch_internal_invitations(contactid, userprofileid)

        metrics = collector.get_metrics()

        return JSONResponse(
            status_code=200,
            content={
                "data": internal_result,
                "performance": metrics,
            }
        )

    except Exception as e:
        logger.error(f"Pending invitations failed | contactid={contactid} | error={e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve pending invitations"
        )


@router.get(
    "/member_dashboard",
    summary="Member Dashboard Invite Requested Count",
    description="Etown - Mobile API - Member - Member Dashboard Invite Requested Count API",
    responses={
        200: {
            "description": "Member dashboard data retrieved successfully",
            "content": {
                "application/json": {
                    "example": {}
                }
            }
        },
        401: {
            "description": "Unauthorized - invalid or missing access token",
            "content": {
                "application/json": {
                    "example": {"detail": "Unauthorized"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve member dashboard"}
                }
            }
        }
    }
)
async def get_member_dashboard(
    contact_id: str = Query(..., description="The member's contact ID"),
    userprofile_id: str = Query(..., description="The member's user profile ID"),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/dashboard/member_dashboard: return a member's dashboard invite-requested counts."""
    track_api_call(
        background_tasks, "/api/dashboard/member_dashboard", contact_id, userprofile_id
    )

    try:
        logger.info(f"Member dashboard request | contact_id={contact_id} | userprofile_id={userprofile_id}")

        result = await fetch_member_dashboard(contact_id, userprofile_id)

        return JSONResponse(
            status_code=200,
            content=result
        )

    except Exception as e:
        logger.error(f"Member dashboard failed | contact_id={contact_id} | error={e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve member dashboard"
        )


@router.get(
    "/nearme_list",
    summary="Near Me List",
    description=(
        "List the providers in the requesting user's circle that sit within 25 km of the "
        "supplied coordinates, nearest first. Circle providers beyond 25 km, and those "
        "with no coordinates on record, are not returned. Response items use the same "
        "Etown_-prefixed field set as GET /api/invitations/mycircle. Paginated 10 per "
        "page starting at page=1 (page=1 -> first 10, page=2 -> next 10)."
    ),
    responses={
        200: {"description": "Circle providers with Haversine distances, in the mycircle response shape"},
        400: {"description": "Invalid latitude or longitude value"},
        401: {"description": "Unauthorized — invalid or missing Bearer token"},
        500: {"description": "Internal server error"},
    },
)
async def nearme_list(
    inviter_latitude: str = Query(..., description="Requesting user's current latitude"),
    inviter_longitude: str = Query(..., description="Requesting user's current longitude"),
    inviter_id: str = Query(..., description="Contact ID of the requesting user"),
    inviteruserprofile_id: str = Query(..., description="User profile ID of the requesting user"),
    page: int = Query(1, ge=1, description="Page number for pagination (starts from 1)."),
    limit: int = Query(10, ge=1, le=200, description="Rows per page (default 10, max 200)"),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/dashboard/nearme_list: return a page of the user's circle providers within 25 km, nearest first.

    Raises:
        HTTPException: 400 when the coordinates do not parse, 500 on other failures.
    """
    logger.info(
        f"NearMeList request | inviter_id={inviter_id} "
        f"| userprofile_id={inviteruserprofile_id} "
        f"| lat={inviter_latitude} | lon={inviter_longitude} "
        f"| page={page} | limit={limit}"
    )
    track_api_call(
        background_tasks, "/api/dashboard/nearme_list", inviter_id, inviteruserprofile_id
    )
    try:
        result = await get_nearme_list(
            inviter_id=inviter_id,
            inviteruserprofile_id=inviteruserprofile_id,
            inviter_latitude=inviter_latitude,
            inviter_longitude=inviter_longitude,
            page=page,
            limit=limit,
        )
        return JSONResponse(status_code=200, content=result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"NearMeList failed | inviter_id={inviter_id} | error={exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve near me list")

@router.post(
    "/switch_profile",
    summary="Switch Profile",
    description="Create a secondary member or provider profile for an existing contact. Migrated from PowerApps SwitchProfileAPI.",
    responses={
        200: {"description": "Profile created successfully or member profile already exists"},
        400: {"description": "Invalid role value"},
        401: {"description": "Unauthorized — invalid or missing Bearer token"},
        402: {"description": "Role 1 only — the contact has used up its pack's provider_onboard allowance"},
        500: {"description": "Internal server error"},
    },
)
async def switch_profile(
    request: SwitchProfileRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/dashboard/switch_profile: create a secondary member or provider profile and mirror it to Dataverse.

    Raises:
        HTTPException: 402 when a provider profile exceeds the plan cap, 500 on other failures.
    """
    logger.info(
        f"SwitchProfile request | contact_id={request.contact_id} "
        f"| role={request.role} | email={request.email}"
    )
    _SYNC_ONLY_KEYS = {"ets_memberid", "ets_providerid"}
    try:
        if request.role == 2:
            userprofile_data = await create_member_profile(
                contact_id=request.contact_id,
                firstname=request.firstname,
                email=request.email,
                mobilenumber=request.mobilenumber,
            )
            if not userprofile_data:
                logger.info(f"SwitchProfile | member profile already exists | contact_id={request.contact_id}")
                return JSONResponse(status_code=200, content={"message": "Member Already Exist"})
        else:
            userprofile_data = await create_provider_profile(
                contact_id=request.contact_id,
                firstname=request.firstname,
                email=request.email,
                mobilenumber=request.mobilenumber,
            )
            if not userprofile_data:
                raise RuntimeError("Provider profile creation returned no data")

        userprofile_id = userprofile_data[0].get("ets_userprofileid", "")
        detail_id = (
            userprofile_data[0].get("ets_memberid")
            or userprofile_data[0].get("ets_providerid")
            or ""
        )
        background_tasks.add_task(
            sync_switch_profile_to_dataverse,
            contact_id=request.contact_id,
            userprofile_id=userprofile_id,
            detail_id=detail_id,
            role=request.role,
            firstname=request.firstname,
            email=request.email,
            mobilenumber=request.mobilenumber,
        )

        logger.info(
            f"SwitchProfile success | contact_id={request.contact_id} "
            f"| role={request.role} | userprofile_id={userprofile_id}"
        )
        response_data = [{k: v for k, v in row.items() if k not in _SYNC_ONLY_KEYS} for row in userprofile_data]
        return JSONResponse(status_code=200, content={"UserprofileInfo": response_data})

    except PlanLimitError as exc:
        logger.info(
            f"SwitchProfile refused by plan cap | contact_id={request.contact_id} | {exc}"
        )
        raise HTTPException(status_code=402, detail=str(exc))
    except Exception as exc:
        logger.error(
            f"SwitchProfile failed | contact_id={request.contact_id} | error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to switch profile")