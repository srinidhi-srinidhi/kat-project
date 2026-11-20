"""Voice Calls Endpoints - HTTP routes for 100ms.live calls, media controls and call push notifications.

HTTP layer for /api/call: checks the shared bearer token, validates participants and a per-initiator rate limit, and delegates call creation, joining, status, ending, history and media controls to voice_call_service, with VoIP and FCM invitation and decline pushes through notification_service.

Features:
    - Call Creation: Initiate new voice or video call sessions between users, rate-limited per initiator
    - Call Joining: Allow users to join existing call sessions with proper tokens
    - Call Management: Get call status, participant information, and session details
    - Call History: Retrieve paginated, status-filterable call history for a user
    - Media Controls: Toggle camera and microphone during active calls, and read the current state
    - Push Notifications: VoIP (iOS) and FCM (Android) call invitations carrying a ready-to-join session
    - Call Decline Notifications: FCM push telling the original caller their call was declined
    - Authentication: shared bearer token via verify_token
    - Error Handling: Comprehensive error responses with proper HTTP status codes
    - Rate Limiting: Built-in protection against call spam and abuse
    - Async Operations: Full async/await support for high performance
    - Token Management: 100ms SDK auth tokens issued through voice_call_service

Endpoints:
    POST /create: Create new voice call session
    POST /join: Join existing call session
    POST /status: Get call session status
    POST /end: End active call session
    GET /history: Get user's call history with pagination
    POST /media-controls: Update camera/microphone state
    GET /media-controls: Get current media state for a participant (session_id + user_id query params)
    POST /notification: Send VoIP/FCM push notification for call invitations
    POST /decline-notification: Send FCM push notification when a call is declined
    (This router is mounted at prefix /call by app.api.v1.router)

Classes:
    No classes defined (module exposes router endpoints plus permission/rate-limit helpers)

Functions:
    check_user_call_rate_limit(): Count calls a user has initiated within a trailing time window
    validate_users_exist(): Confirm both the initiator and target user profile ids exist
    validate_user_can_join_call(): Confirm a user is the initiator or target before letting them join
    validate_user_can_end_call(): Confirm a user is a participant or call member before letting them end a call
    background_track_call_analytics(): Log a call lifecycle event as a background task
    create_voice_call(): Create new call session with room and tokens
    join_voice_call(): Generate join token for existing call session
    get_call_status(): Retrieve current call status and participant info
    end_voice_call(): Terminate active call session
    get_call_history(): Retrieve paginated call history for user
    validate_user_can_control_media(): Confirm a user is a participant before letting them read/change media state
    update_media_controls(): Update participant media settings during active calls
    get_media_controls(): Retrieve current media state for a participant
    send_call_notification(): Send VoIP/FCM push notification for call invitations
    send_decline_notification(): Notify the original caller that their call invitation was declined

Dependencies:
    - fastapi: API framework and routing with dependency injection
    - app.services.voice_call_service: 100ms integration service layer
    - app.services.notification_service: FCM (Android) and APNs VoIP (iOS) push notification service
    - app.core.security: verify_token shared bearer authentication
    - app.schemas.voice_calls: Pydantic request/response validation models
    - app.schemas.api: Notification request/response models
    - app.db.client: Database operations with retry mechanisms
    - app.core.config: Application configuration and settings

Updates:
    18-Mar-2026: Initial creation for 100ms voice calling integration
    19-Mar-2026: Consolidated video control endpoints for unified call management
    25-Mar-2026: Added FCM push notification endpoint for call invitations
    27-Mar-2026: Fixed role handling in join_call endpoint to support call-type-specific roles
    26-Aug-2026: Logged previously-silent exception handlers; clarified vague log messages.
"""

import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, status
from fastapi.responses import JSONResponse

from app.core.security import verify_token
from app.services.voice_call_service import voice_call_service, CallStatus, ParticipantStatus
from app.schemas.voice_calls import (
    CreateCallRequest, CreateCallResponse,
    JoinCallRequest, JoinCallResponse,
    CallStatusRequest, CallStatusResponse,
    EndCallRequest, EndCallResponse,
    CallListRequest, CallListResponse,
    VoiceCallErrorResponse,
    CallParticipantInfo, CallSessionInfo
)
from app.schemas.api import (
    PushNotificationRequest,
    CallSessionResponse,
    DeclineNotificationRequest,
    DeclineNotificationResponse
)
from app.services.notification_service import (
    NotificationService,
    FCMError,
    FCMAuthenticationError,
    FCMInvalidTokenError,
    FCMQuotaExceededError,
    FCMNetworkError,
    APNsError,
    APNsAuthenticationError,
    APNsInvalidTokenError,
    APNsNetworkError
)
from app.db.client import Database
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


async def check_user_call_rate_limit(user_id: str, time_window_hours: int = 1) -> int:
    """Return how many calls a user initiated in the last time_window_hours, or 0 on error."""
    try:
        from datetime import timedelta
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=time_window_hours)
        
        result = await Database.fetchrow(
            """
            SELECT COUNT(*) as call_count
            FROM etown_call_sessions 
            WHERE initiator_user_id = $1 AND created_at >= $2
            """,
            user_id, cutoff_time
        )
        
        return result["call_count"] if result else 0
        
    except Exception as e:
        logger.error(f"Failed to check rate limit for user {user_id}: {e}", exc_info=True)
        return 0


async def validate_users_exist(initiator_id: str, target_id: str) -> bool:
    """Return True if both userprofile ids exist, False otherwise or on error."""
    try:
        result = await Database.fetchrow(
            """
            SELECT 
                COUNT(CASE WHEN userprofileid = $1 THEN 1 END) as initiator_exists,
                COUNT(CASE WHEN userprofileid = $2 THEN 1 END) as target_exists
            FROM etown_userprofile 
            WHERE userprofileid IN ($1, $2)
            """,
            initiator_id, target_id
        )
        
        if not result:
            return False
            
        return result["initiator_exists"] > 0 and result["target_exists"] > 0
        
    except Exception as e:
        logger.error(f"User validation failed for {initiator_id}, {target_id}: {e}", exc_info=True)
        return False


async def validate_user_can_join_call(user_id: str, session_id: str) -> bool:
    """Return True if the user is the session's initiator or target, False otherwise or on error."""
    try:
        result = await Database.fetchrow(
            """
            SELECT initiator_user_id, target_user_id
            FROM etown_call_sessions 
            WHERE session_id = $1
            """,
            session_id
        )
        
        if not result:
            return False
            
        return user_id in [result["initiator_user_id"], result["target_user_id"]]
        
    except Exception as e:
        logger.error(f"Join validation failed for user {user_id}, session {session_id}: {e}", exc_info=True)
        return False


async def validate_user_can_end_call(user_id: str, session_id: str) -> bool:
    """Return True if the user is a participant row or the session's initiator or target, False otherwise or on error."""
    try:
        result = await Database.fetchrow(
            """
            SELECT COUNT(*) as participant_count, 
                   STRING_AGG(join_status, ',') as statuses,
                   STRING_AGG(role, ',') as roles
            FROM etown_call_participants 
            WHERE session_id = $1 AND user_id = $2
            """,
            session_id, user_id
        )
        
        is_participant = result["participant_count"] > 0 if result else False
        
        session_result = await Database.fetchrow(
            """
            SELECT initiator_user_id, target_user_id, call_status
            FROM etown_call_sessions
            WHERE session_id = $1
            """,
            session_id
        )
        
        is_call_member = False
        if session_result:
            is_call_member = user_id in [session_result["initiator_user_id"], session_result["target_user_id"]]
        
        can_end = is_participant or is_call_member
        
        logger.debug(f"End call validation for user {user_id}, session {session_id}: can_end={can_end}")
        
        return can_end
        
    except Exception as e:
        logger.error(f"End call validation failed for user {user_id}, session {session_id}: {e}", exc_info=True)
        return False


def background_track_call_analytics(session_id: str, event_type: str, user_id: str, metadata: Dict[str, Any]):
    """Log a call analytics event for a session and user; metadata is accepted but not recorded."""
    try:
        logger.info(f"Call analytics: {event_type} for session {session_id} by user {user_id}")
    except Exception as e:
        logger.error(f"Background analytics tracking failed: {str(e)}", exc_info=True)


@router.post("/create", response_model=CreateCallResponse)
async def create_voice_call(
    request: CreateCallRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token)
) -> CreateCallResponse:
    """Handle POST /api/call/create: rate-limit the initiator, then create a 100ms room session with tokens for both users.

    Raises:
        HTTPException: 429 over the hourly limit, 400 on validation errors, 500 on failures.
    """
    try:
        logger.info(f"Creating {request.call_type} call from {request.initiator_user_id} to {request.target_user_id}")
        
        current_hour_calls = await check_user_call_rate_limit(request.initiator_user_id)
        max_calls_per_hour = getattr(settings, 'HMS_MAX_CALLS_PER_HOUR_PER_USER', 10)
        
        if current_hour_calls >= max_calls_per_hour:
            raise HTTPException(
                status_code=429, 
                detail=f"Rate limit exceeded. Maximum {max_calls_per_hour} calls per hour allowed"
            )
        
        if not await validate_users_exist(request.initiator_user_id, request.target_user_id):
            logger.error(f"Failed to create {request.call_type} call: One or both users not found in system | initiator={request.initiator_user_id}, target={request.target_user_id}")
            error_response = VoiceCallErrorResponse(
                error_code="USER_NOT_FOUND",
                error_message="One or both users not found in system",
                error_details={"initiator_user_id": request.initiator_user_id, "target_user_id": request.target_user_id},
                session_id=None,
                timestamp=datetime.now(timezone.utc).isoformat()
            )
            return JSONResponse(
                status_code=400,
                content=error_response.model_dump()
            )
        
        session_data = await voice_call_service.create_call_session(
            request.initiator_user_id, 
            request.target_user_id,
            request.call_type.value
        )
        
        response = CreateCallResponse(
            session_id=session_data["session_id"],
            room_id=session_data["room_id"],
            room_name=session_data["room_name"],
            initiator_token=session_data["initiator_token"],
            target_token=session_data["target_token"],
            initiator_participant_id=session_data["initiator_participant_id"],
            target_participant_id=session_data["target_participant_id"],
            status=session_data["status"],
            expires_at=session_data["expires_at"],
            call_url=None
        )
        
        background_tasks.add_task(
            background_track_call_analytics,
            session_data["session_id"],
            "call_created",
            request.initiator_user_id,
            {"target_user_id": request.target_user_id, "call_type": request.call_type}
        )
        
        logger.info(f"Successfully created call session {session_data['session_id']}")
        return response
        
    except ValueError as ve:
        logger.error(f"Validation error creating call: {ve}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to create {request.call_type} call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error creating {request.call_type} call")


@router.post("/join", response_model=JoinCallResponse)
async def join_voice_call(
    request: JoinCallRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token)
) -> JoinCallResponse:
    """Handle POST /api/call/join: issue a fresh 100ms token for a call member and mark them joined.

    Raises:
        HTTPException: 404 unknown session, 400 unjoinable status, 403 not a member, 500 on failures.
    """
    try:
        logger.info(f"User {request.user_id} joining call session {request.session_id}")
        
        session = await voice_call_service.get_call_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Call session not found")
        
        if session["call_status"] not in [CallStatus.CREATED.value, CallStatus.RINGING.value, CallStatus.ACTIVE.value]:
            raise HTTPException(status_code=400, detail=f"Cannot join call in status: {session['call_status']}")
        
        if not await validate_user_can_join_call(request.user_id, request.session_id):
            raise HTTPException(
                status_code=403, 
                detail="User does not have permission to join this call"
            )
        
        room_id = session["hms_room_id"]
        
        call_type = session["call_type"]
        initiator_role, target_role = voice_call_service._get_call_roles(call_type)
        
        if request.user_id == session["target_user_id"]:
            user_role = target_role
        elif request.user_id == session["initiator_user_id"]:
            user_role = initiator_role
        else:
            user_role = target_role
        
        auth_token = voice_call_service.generate_auth_token(room_id, request.user_id, user_role)
        
        await Database.execute(
            """
            UPDATE etown_call_participants 
            SET join_status = $1, joined_at = $2, updated_at = $3
            WHERE session_id = $4 AND user_id = $5
            """,
            ParticipantStatus.JOINED.value, datetime.now(timezone.utc), datetime.now(timezone.utc),
            request.session_id, request.user_id
        )
        
        participant_result = await Database.fetchrow(
            "SELECT participant_id FROM etown_call_participants WHERE session_id = $1 AND user_id = $2",
            request.session_id, request.user_id
        )
        participant_id = participant_result["participant_id"] if participant_result else "unknown"
        
        response = JoinCallResponse(
            session_id=request.session_id,
            room_id=room_id,
            user_token=auth_token,
            participant_id=participant_id,
            role=user_role,
            call_url=None,
            expires_at=(datetime.now(timezone.utc).isoformat() + "Z")
        )
        
        background_tasks.add_task(
            background_track_call_analytics,
            request.session_id,
            "user_joined",
            request.user_id,
            {"role": user_role}
        )
        
        logger.info(f"User {request.user_id} successfully joined call session {request.session_id}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to join voice call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error joining call")


@router.post("/status", response_model=CallStatusResponse)
async def get_call_status(
    request: CallStatusRequest,
    token: str = Depends(verify_token)
) -> CallStatusResponse:
    """Handle POST /api/call/status: return a call session's status, participants, duration and quality metrics.

    Raises:
        HTTPException: 404 for an unknown session, 500 on failures.
    """
    try:
        session_id = request.session_id
        logger.info(f"Getting status for call session {session_id}")
        
        session = await voice_call_service.get_call_session(session_id) 
        if not session:
            raise HTTPException(status_code=404, detail="Call session not found")
        
        
        participants = []
        participant_data = session.get("participants", [])
        
        if participant_data and not isinstance(participant_data, list):
            participant_data = [participant_data]
            
        for p in participant_data:
            if p and isinstance(p, dict):
                try:
                    joined_at = None
                    left_at = None
                    
                    if p.get("joined_at"):
                        try:
                            joined_at = datetime.fromisoformat(str(p["joined_at"]).replace('Z', '+00:00'))
                        except (ValueError, AttributeError):
                            logger.debug("call status: participant joined_at is not an ISO timestamp")
                            joined_at = None
                    
                    if p.get("left_at"):
                        try:
                            left_at = datetime.fromisoformat(str(p["left_at"]).replace('Z', '+00:00'))
                        except (ValueError, AttributeError):
                            logger.debug("call status: participant left_at is not an ISO timestamp")
                            left_at = None
                    
                    participant_info = CallParticipantInfo(
                        participant_id=str(p.get("participant_id", "")),
                        user_id=str(p.get("user_id", "")),
                        role=str(p.get("role", "guest")),
                        join_status=str(p.get("join_status", "unknown")),
                        joined_at=joined_at,
                        left_at=left_at,
                        audio_enabled=True,
                        video_enabled=False,
                        connection_quality=None
                    )
                    participants.append(participant_info)
                except Exception as e:
                    logger.warning(f"Failed to parse participant data {p}: {e}")
        
        duration_seconds = None
        started_at = None
        ended_at = None
        
        if session.get("started_at"):
            try:
                started_at = datetime.fromisoformat(session["started_at"].replace('Z', '+00:00'))
            except (ValueError, AttributeError) as e:
                logger.warning(f"Failed to parse started_at '{session.get('started_at')}': {e}")
        
        if session.get("ended_at"):
            try:
                ended_at = datetime.fromisoformat(session["ended_at"].replace('Z', '+00:00'))
            except (ValueError, AttributeError) as e:
                logger.warning(f"Failed to parse ended_at '{session.get('ended_at')}': {e}")
        
        if started_at and ended_at:
            duration_seconds = int((ended_at - started_at).total_seconds())
        elif started_at:
            duration_seconds = int((datetime.now(timezone.utc) - started_at).total_seconds())
        if session.get("call_status") == "ended" and duration_seconds is None:
            duration_seconds = 0
        
        created_at = None
        if session.get("created_at"):
            try:
                created_at = datetime.fromisoformat(session["created_at"].replace('Z', '+00:00'))
            except (ValueError, AttributeError) as e:
                logger.warning(f"Failed to parse created_at '{session.get('created_at')}': {e}")
                created_at = datetime.now(timezone.utc)
        
        quality_metrics = None
        if session.get("call_quality"):
            try:
                quality_data = session["call_quality"]
                if isinstance(quality_data, str):
                    import json
                    quality_metrics = json.loads(quality_data) if quality_data.strip() != '{}' else None
                elif isinstance(quality_data, dict):
                    quality_metrics = quality_data
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse quality_metrics '{session.get('call_quality')}': {e}")
                quality_metrics = None
        
        response = CallStatusResponse(
            session_id=session["session_id"],
            room_id=session.get("hms_room_id"),
            status=session.get("call_status", "unknown"),
            call_type=session.get("call_type", "voice"),
            participants=participants,
            created_at=created_at or datetime.now(timezone.utc),
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration_seconds,
            quality_metrics=quality_metrics
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get call status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error getting call status")


@router.post("/end", response_model=EndCallResponse)
async def end_voice_call(
    request: EndCallRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token)
) -> EndCallResponse:
    """Handle POST /api/call/end: let a call member end an open session and return its final status and duration.

    Raises:
        HTTPException: 400 missing user or already ended, 404 unknown session, 403 not a member, 500 on failures.
    """
    try:
        session_id = request.session_id
        logger.info(f"Ending call session {session_id} by user {request.ended_by_user_id}")
        
        if not request.ended_by_user_id or request.ended_by_user_id.strip() == "":
            logger.error(f"Invalid ended_by_user_id: '{request.ended_by_user_id}'")
            raise HTTPException(status_code=400, detail="ended_by_user_id is required and cannot be empty")
        
        session = await voice_call_service.get_call_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Call session not found")
        
        if session["call_status"] in [CallStatus.ENDED.value, CallStatus.CANCELLED.value]:
            raise HTTPException(status_code=400, detail=f"Call already {session['call_status']}")
        
        if not await validate_user_can_end_call(request.ended_by_user_id, session_id):
            logger.error(f"Authorization failed - User {request.ended_by_user_id} cannot end call {session_id}")
            logger.error(
                "end_call denied: %s is neither party | session=%s initiator=%s target=%s",
                request.ended_by_user_id, session_id,
                session.get("initiator_user_id"), session.get("target_user_id"),
            )
            raise HTTPException(
                status_code=403, 
                detail="User does not have permission to end this call"
            )
        
        end_result = await voice_call_service.end_call_session(session_id, request.ended_by_user_id)
        
        duration_seconds = None
        if session.get("started_at") and end_result.get("ended_at"):
            try:
                start_time = datetime.fromisoformat(session["started_at"].replace('Z', '+00:00'))
                end_time = datetime.fromisoformat(end_result["ended_at"].replace('Z', '+00:00'))
                duration_seconds = int((end_time - start_time).total_seconds())
            except Exception as e:
                logger.warning(f"Failed to calculate call duration: {e}")
        if end_result.get("status") == "ended" and duration_seconds is None:
            duration_seconds = 0
        
        response = EndCallResponse(
            session_id=session_id,
            status=end_result["status"],
            ended_at=datetime.fromisoformat(end_result["ended_at"].replace('Z', '+00:00')),
            duration_seconds=duration_seconds,
            ended_by=request.ended_by_user_id
        )
        
        background_tasks.add_task(
            background_track_call_analytics,
            session_id,
            "call_ended",
            request.ended_by_user_id,
            {"duration_seconds": duration_seconds, "reason": request.reason}
        )
        
        logger.info(f"Successfully ended call session {session_id}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to end voice call: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error ending call")


@router.get("/history", response_model=CallListResponse)
async def get_call_history(
    user_id: str = Query(..., description="User ID to get call history for"),
    limit: int = Query(default=10, ge=1, le=100, description="Maximum calls to return"),
    offset: int = Query(default=0, ge=0, description="Number of calls to skip"),
    status_filter: Optional[str] = Query(default=None, description="Filter by call status (comma-separated)"),
    token: str = Depends(verify_token)
) -> CallListResponse:
    """Handle GET /api/call/history: page through a user's calls, newest first, optionally filtered by comma-separated statuses.

    Raises:
        HTTPException: 500 on failures.
    """
    try:
        logger.info(f"Getting call history for user {user_id} (limit={limit}, offset={offset})")
        
        
        status_list = None
        if status_filter:
            status_list = [s.strip() for s in status_filter.split(",") if s.strip()]
        
        conditions = ["(cs.initiator_user_id = $1 OR cs.target_user_id = $1)"]
        params = [user_id]
        
        if status_list:
            conditions.append(f"cs.call_status = ANY(${len(params) + 1})")
            params.append(status_list)
        
        where_clause = " AND ".join(conditions)
        
        count_query = f"""
            SELECT COUNT(*) as total
            FROM etown_call_sessions cs
            WHERE {where_clause}
        """
        total_result = await Database.fetchrow(count_query, *params)
        total_count = total_result["total"] if total_result else 0
        
        limit_param_pos = len(params) + 1
        offset_param_pos = len(params) + 2
        params.extend([limit, offset])
        
        query = f"""
            SELECT cs.*,
                   etown_deleted_flag_ci(cs.initiator_user_id)     AS initiator_deleted,
                   etown_deactivated_flag_ci(cs.initiator_user_id) AS initiator_deactivated,
                   etown_deleted_flag_ci(cs.target_user_id)        AS target_deleted,
                   etown_deactivated_flag_ci(cs.target_user_id)    AS target_deactivated,
                   array_agg(
                       CASE WHEN cp.participant_id IS NOT NULL THEN
                           json_build_object(
                               'participant_id', cp.participant_id,
                               'user_id', cp.user_id,
                               'role', cp.role,
                               'join_status', cp.join_status,
                               'joined_at', cp.joined_at,
                               'left_at', cp.left_at,
                               'deleted', etown_deleted_flag_ci(cp.user_id),
                               'deactivated', etown_deactivated_flag_ci(cp.user_id)
                           )
                       END
                   ) FILTER (WHERE cp.participant_id IS NOT NULL) as participants
            FROM etown_call_sessions cs
            LEFT JOIN etown_call_participants cp ON cs.session_id = cp.session_id
            WHERE {where_clause}
            GROUP BY cs.id
            ORDER BY cs.created_at DESC
            LIMIT ${limit_param_pos} OFFSET ${offset_param_pos}
        """
        
        results = await Database.fetch(query, *params)
        
        calls = []
        for row in results:
            participants = []
            participant_data = row.get("participants")
            if participant_data and participant_data != [None]:
                for p in participant_data:
                    if p and isinstance(p, dict):
                        try:
                            participant_info = CallParticipantInfo(
                                participant_id=str(p.get("participant_id", "")),
                                user_id=str(p.get("user_id", "")),
                                role=str(p.get("role", "guest")),
                                join_status=str(p.get("join_status", "unknown")),
                                joined_at=p.get("joined_at"),
                                left_at=p.get("left_at"),
                                audio_enabled=True,
                                video_enabled=False,
                                connection_quality=None,
                                deleted=str(p.get("deleted", "false")),
                                deactivated=str(p.get("deactivated", "false")),
                            )
                            participants.append(participant_info)
                        except Exception as e:
                            logger.warning(f"Failed to parse participant {p}: {e}")
            
            duration_seconds = None
            if row.get("started_at") and row.get("ended_at"):
                try:
                    duration_seconds = int((row["ended_at"] - row["started_at"]).total_seconds())
                except Exception as e:
                    logger.warning(f"Failed to calculate call history duration: {e}")
            if row.get("call_status") == "ended" and duration_seconds is None:
                duration_seconds = 0
            
            quality_metrics = None
            call_quality_data = row.get("call_quality")
            if call_quality_data:
                try:
                    if isinstance(call_quality_data, str):
                        import json
                        quality_metrics = json.loads(call_quality_data) if call_quality_data.strip() not in ('{}', '') else None
                    elif isinstance(call_quality_data, dict):
                        quality_metrics = call_quality_data
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Failed to parse call_quality '{call_quality_data}': {e}")
                    quality_metrics = None
            
            metadata = None
            call_metadata_data = row.get("call_metadata")
            if call_metadata_data:
                try:
                    if isinstance(call_metadata_data, str):
                        import json
                        metadata = json.loads(call_metadata_data) if call_metadata_data.strip() not in ('{}', '') else None
                    elif isinstance(call_metadata_data, dict):
                        metadata = call_metadata_data
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Failed to parse call_metadata '{call_metadata_data}': {e}")
                    metadata = None
            
            call_info = CallSessionInfo(
                session_id=str(row.get("session_id", "")),
                room_id=str(row.get("hms_room_id", "")),
                room_name=str(row.get("hms_room_name", "")),
                initiator_user_id=str(row.get("initiator_user_id", "")),
                target_user_id=str(row.get("target_user_id", "")),
                status=str(row.get("call_status", "unknown")),
                call_type=str(row.get("call_type", "voice")),
                participants=participants,
                created_at=row.get("created_at"),
                started_at=row.get("started_at"),
                ended_at=row.get("ended_at"),
                duration_seconds=duration_seconds,
                quality_metrics=quality_metrics,
                metadata=metadata,
                initiator_deleted=str(row.get("initiator_deleted", "false")),
                initiator_deactivated=str(row.get("initiator_deactivated", "false")),
                target_deleted=str(row.get("target_deleted", "false")),
                target_deactivated=str(row.get("target_deactivated", "false")),
            )
            calls.append(call_info)
        
        response = CallListResponse(
            calls=calls,
            total_count=total_count,
            limit=limit,
            offset=offset,
            has_more=(offset + limit < total_count)
        )
        
        logger.info(f"Retrieved {len(calls)} calls for user {user_id}")
        return response
        
    except Exception as e:
        logger.error(f"Failed to get call history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error getting call history")


async def validate_user_can_control_media(user_id: str, session_id: str) -> bool:
    """Return True if the user has a participant row in the session, False otherwise or on error."""
    try:
        result = await Database.fetchrow(
            """
            SELECT COUNT(*) as participant_count
            FROM etown_call_participants 
            WHERE session_id = $1 AND user_id = $2
            """,
            session_id, user_id
        )
        
        return result["participant_count"] > 0 if result else False
        
    except Exception as e:
        logger.error(f"Media control validation failed for user {user_id}, session {session_id}: {e}", exc_info=True)
        return False


@router.post("/media-controls", response_model=Dict[str, Any])
async def update_media_controls(
    session_id: str = Query(..., description="Call session ID"),
    user_id: str = Query(..., description="User ID"),
    camera_enabled: Optional[bool] = Query(default=None, description="Enable/disable camera"),
    microphone_enabled: Optional[bool] = Query(default=None, description="Enable/disable microphone"),
    token: str = Depends(verify_token)
) -> Dict[str, Any]:
    """Handle POST /api/call/media-controls: set a participant's camera and microphone flags in an active or ringing call.

    Raises:
        HTTPException: 403 not a participant, 404 unknown session, 400 bad status or no change, 500 on failures.
    """
    try:
        logger.info(f"Updating media controls for user {user_id} in session {session_id}")
        
        if not await validate_user_can_control_media(user_id, session_id):
            raise HTTPException(
                status_code=403, 
                detail="User does not have permission to control media for this call"
            )
        
        session = await voice_call_service.get_call_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Call session not found")
        
        if session["call_status"] not in [CallStatus.ACTIVE.value, CallStatus.RINGING.value]:
            raise HTTPException(status_code=400, detail=f"Cannot update media in call status: {session['call_status']}")
        
        update_fields = []
        update_params = []
        param_count = 1
        
        if camera_enabled is not None:
            update_fields.append(f"video_enabled = ${param_count}")
            update_params.append(camera_enabled)
            param_count += 1
            
        if microphone_enabled is not None:
            update_fields.append(f"audio_enabled = ${param_count}")
            update_params.append(microphone_enabled)
            param_count += 1
        
        if not update_fields:
            raise HTTPException(status_code=400, detail="No media controls specified for update")
        
        update_fields.append(f"updated_at = ${param_count}")
        update_params.append(datetime.now(timezone.utc))
        param_count += 1
        
        update_params.extend([session_id, user_id])
        
        update_query = f"""
            UPDATE etown_call_participants 
            SET {', '.join(update_fields)}
            WHERE session_id = ${param_count} AND user_id = ${param_count + 1}
            RETURNING video_enabled, audio_enabled, updated_at
        """
        
        result = await Database.fetchrow(update_query, *update_params)
        
        if not result:
            raise HTTPException(status_code=404, detail="Participant not found in call session")
        
        logger.info(f"Updated media controls for user {user_id}: camera={result['video_enabled']}, mic={result['audio_enabled']}")
        
        return {
            "success": True,
            "session_id": session_id,
            "user_id": user_id,
            "camera_enabled": result["video_enabled"],
            "microphone_enabled": result["audio_enabled"],
            "updated_at": result["updated_at"].isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update media controls: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error updating media controls")


@router.get("/media-controls", response_model=Dict[str, Any])
async def get_media_controls(
    session_id: str,
    user_id: str,
    token: str = Depends(verify_token)
) -> Dict[str, Any]:
    """Handle GET /api/call/media-controls: return a participant's camera, microphone and join state with the call status.

    Raises:
        HTTPException: 403 not a participant, 404 participant not found, 500 on failures.
    """
    try:
        logger.info(f"Getting media controls for user {user_id} in session {session_id}")
        
        if not await validate_user_can_control_media(user_id, session_id):
            raise HTTPException(
                status_code=403, 
                detail="User does not have permission to view media controls for this call"
            )
        
        result = await Database.fetchrow(
            """
            SELECT p.video_enabled, p.audio_enabled, p.join_status, p.joined_at, p.updated_at,
                   s.call_status, s.call_type
            FROM etown_call_participants p
            JOIN etown_call_sessions s ON p.session_id = s.session_id
            WHERE p.session_id = $1 AND p.user_id = $2
            """,
            session_id, user_id
        )
        
        if not result:
            raise HTTPException(status_code=404, detail="Participant not found in call session")
        
        return {
            "session_id": session_id,
            "user_id": user_id,
            "camera_enabled": result["video_enabled"],
            "microphone_enabled": result["audio_enabled"],
            "join_status": result["join_status"],
            "call_status": result["call_status"],
            "call_type": result["call_type"],
            "joined_at": result["joined_at"].isoformat() if result["joined_at"] else None,
            "updated_at": result["updated_at"].isoformat() if result["updated_at"] else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get media controls: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error getting media controls")


@router.post(
    "/notification",
    response_model=CallSessionResponse,
    status_code=200,
    summary="Send Firebase Cloud Messaging push notification for voice/video call invitations",
    description="""Send FCM push notification to initiate or join a voice/video call between users.

This endpoint:
- Sends Firebase Cloud Messaging notification to target user's device
- If room/session/token fields are provided, allows joining an existing call room (reuses provided session/tokens)
- Otherwise, generates a new call session with 100ms.live integration and new JWT tokens
- Returns ready-to-use room ID, session ID, and authentication tokens
- Supports both voice and video call types
- Compatible with PowerApps FCM integration format

The notification payload includes all necessary data for the mobile app to directly join the call when tapped, either in a new or existing room.""",
    responses={
        200: {
            "description": "Notification sent successfully with call session data",
            "content": {
                "application/json": {
                    "example": {
                        "call_url": "https://etownservices.com/call/6408f2b7f8456a8b90a67c12",
                        "expires_at": "2026-03-19T12:34:56Z",
                        "initiator_participant_id": "part_123456",
                        "initiator_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTYiLCJyb2xlIjoiaG9zdCJ9...",
                        "inviter_profile_img": "https://cdn.etownservices.com/profile/12345.jpg",
                        "room_id": "6408f2b7f8456a8b90a67c12",
                        "room_name": "Voice Call - call_20260318_123456_abc123",
                        "session_id": "call_20260318_123456_abc123",
                        "status": "created",
                        "target_participant_id": "part_789012",
                        "target_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2Nzg5MCIsInJvbGUiOiJndWVzdCJ9..."
                    }
                }
            }
        },
        400: {
            "description": "Bad request - Device token not found, invalid parameters, or user validation failed",
            "content": {
                "application/json": {
                    "examples": {
                        "device_token_not_found": {
                            "summary": "Device token not found",
                            "value": {
                                "detail": "Device token not found for user: 67890",
                                "performance": {
                                    "response_time_seconds": 0.255,
                                    "database_time_ms": 250.3,
                                    "external_api_time_ms": 0,
                                    "cache_hit": False,
                                    "timestamp": "2026-03-25T08:06:15.901090+00:00",
                                    "endpoint_name": "call_notification"
                                }
                            }
                        },
                        "fcm_invalid_token": {
                            "summary": "FCM invalid device token",
                            "value": {
                                "error_type": "fcm_invalid_token",
                                "message": "Invalid device token for push notification",
                                "details": "The provided device token is invalid or expired"
                            }
                        },
                        "invalid_user": {
                            "summary": "Invalid user profile ID",
                            "value": {
                                "detail": "One or both user profiles not found in system"
                            }
                        }
                    }
                }
            }
        },
        422: {
            "description": "Validation error - Missing or invalid request fields",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "type": "missing",
                                "loc": ["body", "oauth_token"],
                                "msg": "Field required",
                                "input": {}
                            }
                        ]
                    }
                }
            }
        },
        429: {
            "description": "Too Many Requests - FCM quota exceeded",
            "content": {
                "application/json": {
                    "example": {
                        "error_type": "fcm_quota_exceeded",
                        "message": "Push notification quota exceeded. Please try again later",
                        "details": "Quota exceeded for Firebase Cloud Messaging (FCM) API"
                    }
                }
            }
        },
        500: {
            "description": "Internal server error - FCM API failure or system error",
            "content": {
                "application/json": {
                    "examples": {
                        "fcm_error": {
                            "summary": "FCM API error",
                            "value": {
                                "error_type": "fcm_error",
                                "message": "Failed to send push notification",
                                "details": "Firebase Cloud Messaging API returned error code 500"
                            }
                        },
                        "unexpected_error": {
                            "summary": "Unexpected system error",
                            "value": {
                                "error_type": "unexpected_error",
                                "message": "Unexpected error while sending notification",
                                "details": "Call session generation failed due to internal server error"
                            }
                        }
                    }
                }
            }
        },
        503: {
            "description": "Service Unavailable - FCM authentication or network error",
            "content": {
                "application/json": {
                    "examples": {
                        "fcm_authentication_error": {
                            "summary": "FCM authentication failure",
                            "value": {
                                "error_type": "fcm_authentication_error",
                                "message": "Push notification service authentication failed",
                                "details": "Failed to authenticate with Firebase Cloud Messaging API"
                            }
                        },
                        "fcm_network_error": {
                            "summary": "FCM network error",
                            "value": {
                                "error_type": "fcm_network_error",
                                "message": "Network error sending push notification",
                                "details": "Connection timeout to Firebase Cloud Messaging API"
                            }
                        }
                    }
                }
            }
        }
    }
)
async def send_call_notification(request: PushNotificationRequest) -> CallSessionResponse:
    """Handle POST /api/call/notification: push a call invitation via APNs VoIP or FCM and return the call session.

    Raises:
        HTTPException: 400 bad token or input, 429 FCM quota, 503 push auth or network, 500 otherwise.
    """
    logger.info(f"Processing call notification from {request.initiator_userprofile_id} to {request.target_userprofile_id}")
    
    try:
        notification_service = NotificationService()
        call_session = await notification_service.send_call_notification(request)
        
        logger.info(f"Call notification sent successfully, session ID: {call_session.session_id}")
        return call_session
        
    except APNsAuthenticationError as e:
        logger.error(f"APNs authentication failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_type": "apns_authentication_error",
                "message": "VoIP push notification service authentication failed",
                "details": str(e)
            }
        )
    except APNsInvalidTokenError as e:
        logger.error(f"APNs invalid token: {e}", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": "apns_invalid_token",
                "message": "Invalid VoIP device token",
                "details": str(e)
            }
        )
    except APNsNetworkError as e:
        logger.error(f"APNs network error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_type": "apns_network_error",
                "message": "Network error sending VoIP push notification",
                "details": str(e)
            }
        )
    except APNsError as e:
        logger.error(f"APNs general error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_type": "apns_error",
                "message": "Failed to send VoIP push notification",
                "details": str(e)
            }
        )
    except FCMAuthenticationError as e:
        logger.error(f"FCM authentication failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_type": "fcm_authentication_error",
                "message": "Push notification service authentication failed",
                "details": str(e)
            }
        )
    except FCMInvalidTokenError as e:
        logger.error(f"FCM invalid token: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_type": "fcm_invalid_token",
                "message": "Invalid device token for push notification",
                "details": str(e)
            }
        )
    except FCMQuotaExceededError as e:
        logger.error(f"FCM quota exceeded: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_type": "fcm_quota_exceeded",
                "message": "Push notification quota exceeded. Please try again later",
                "details": str(e)
            }
        )
    except FCMNetworkError as e:
        logger.error(f"FCM network error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_type": "fcm_network_error",
                "message": "Network error sending push notification",
                "details": str(e)
            }
        )
    except FCMError as e:
        logger.error(f"FCM general error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_type": "fcm_error",
                "message": "Failed to send push notification",
                "details": str(e)
            }
        )
    except ValueError as e:
        logger.error(f"Validation error in call notification: {e}", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error during call notification: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_type": "unexpected_error",
                "message": "Unexpected error while sending notification",
                "details": str(e)
            }
        )


@router.post(
    "/decline-notification",
    response_model=DeclineNotificationResponse,
    status_code=200,
    summary="Send Firebase Cloud Messaging push notification for call decline",
    description="""Send FCM push notification when a user declines an incoming call invitation.

This endpoint:
- Notifies original caller that their call was declined
- Uses original call session information (room ID, session ID)
- Sends PowerApps-compatible FCM decline notification
- Provides confirmation of notification delivery

The notification includes the original call context to help the receiving app handle the decline properly.""",
    responses={
        200: {
            "description": "Decline notification sent successfully",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "message": "Decline notification sent successfully",
                        "data": {
                            "type": "decline_call",
                            "route": "/call",
                            "isVideo": "false",
                            "userprofile": "67890",
                            "opponentuserprofile": "12345",
                            "room_id": "6408f2b7f8456a8b90a67c12",
                            "room_name": "Voice Call - call_20260318_123456_abc123",
                            "session_id": "call_20260318_123456_abc123"
                        }
                    }
                }
            }
        },
        400: {
            "description": "Bad request - Device token not found or invalid parameters",
            "content": {
                "application/json": {
                    "examples": {
                        "device_token_not_found": {
                            "summary": "Device token not found for opponent",
                            "value": {
                                "detail": "Device token not found for user: 12345"
                            }
                        },
                        "fcm_invalid_token": {
                            "summary": "FCM invalid device token",
                            "value": {
                                "error_type": "fcm_invalid_token",
                                "message": "Invalid device token for decline notification",
                                "details": "The provided device token is invalid or expired"
                            }
                        },
                        "invalid_session": {
                            "summary": "Invalid session information",
                            "value": {
                                "detail": "Room ID or session ID is invalid"
                            }
                        }
                    }
                }
            }
        },
        429: {
            "description": "Too Many Requests - FCM quota exceeded",
            "content": {
                "application/json": {
                    "example": {
                        "error_type": "fcm_quota_exceeded",
                        "message": "Push notification quota exceeded. Please try again later",
                        "details": "Quota exceeded for Firebase Cloud Messaging (FCM) API"
                    }
                }
            }
        },
        500: {
            "description": "Internal server error - FCM API failure",
            "content": {
                "application/json": {
                    "examples": {
                        "fcm_error": {
                            "summary": "FCM API error",
                            "value": {
                                "error_type": "fcm_error",
                                "message": "Failed to send decline notification",
                                "details": "Firebase Cloud Messaging API returned error code 500"
                            }
                        },
                        "unexpected_error": {
                            "summary": "Unexpected system error",
                            "value": {
                                "error_type": "unexpected_error",
                                "message": "Unexpected error while sending decline notification",
                                "details": "Internal server error occurred during processing"
                            }
                        }
                    }
                }
            }
        },
        503: {
            "description": "Service Unavailable - FCM authentication or network error",
            "content": {
                "application/json": {
                    "examples": {
                        "fcm_authentication_error": {
                            "summary": "FCM authentication failure",
                            "value": {
                                "error_type": "fcm_authentication_error",
                                "message": "Push notification service authentication failed",
                                "details": "Failed to authenticate with Firebase Cloud Messaging API"
                            }
                        },
                        "fcm_network_error": {
                            "summary": "FCM network error",
                            "value": {
                                "error_type": "fcm_network_error",
                                "message": "Network error sending decline notification",
                                "details": "Connection timeout to Firebase Cloud Messaging API"
                            }
                        }
                    }
                }
            }
        }
    }
)
async def send_decline_notification(request: DeclineNotificationRequest) -> DeclineNotificationResponse:
    """Handle POST /api/call/decline-notification: push a call-declined FCM notification to the original caller.

    Raises:
        HTTPException: 400 bad token or input, 429 FCM quota, 503 FCM auth or network, 500 otherwise.
    """
    logger.info(f"Processing decline notification from {request.userprofile_id} to {request.opponent_userprofile_id}")
    
    try:
        notification_service = NotificationService()
        result = await notification_service.send_decline_notification(request)
        
        logger.info(f"Decline notification sent successfully for session: {request.session_id}")
        return result
        
    except FCMAuthenticationError as e:
        logger.error(f"FCM authentication failed for decline notification: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_type": "fcm_authentication_error",
                "message": "Push notification service authentication failed",
                "details": str(e)
            }
        )
    except FCMInvalidTokenError as e:
        logger.error(f"FCM invalid token for decline notification: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_type": "fcm_invalid_token",
                "message": "Invalid device token for decline notification",
                "details": str(e)
            }
        )
    except FCMQuotaExceededError as e:
        logger.error(f"FCM quota exceeded for decline notification: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_type": "fcm_quota_exceeded",
                "message": "Push notification quota exceeded. Please try again later",
                "details": str(e)
            }
        )
    except FCMNetworkError as e:
        logger.error(f"FCM network error for decline notification: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_type": "fcm_network_error",
                "message": "Network error sending decline notification",
                "details": str(e)
            }
        )
    except FCMError as e:
        logger.error(f"FCM general error for decline notification: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_type": "fcm_error",
                "message": "Failed to send decline notification",
                "details": str(e)
            }
        )
    except ValueError as e:
        logger.error(f"Validation error in decline notification: {e}", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error during decline notification: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_type": "unexpected_error",
                "message": "Unexpected error while sending decline notification",
                "details": str(e)
            }
        )