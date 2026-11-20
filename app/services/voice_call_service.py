"""Voice Call Service - 100ms.live room, token and call session management for voice and video calls.

Service behind the voice_calls endpoints: creates 100ms rooms from voice or video templates, signs management and client JWTs with PyJWT, and persists sessions, participants, tokens and lifecycle events in the etown_call_* tables through a shared singleton.

Features:
    - Room Management: Create, retrieve, and manage voice/video call rooms via 100ms APIs
    - Token Generation: Generate JWT auth tokens for client SDK authentication
    - Session Tracking: Database integration for call session persistence and analytics
    - User Management: Handle call participants, permissions, and user relationships
    - Call Events: Track call lifecycle events for audit and analytics
    - Error Handling: Robust error handling with retry mechanisms and proper logging
    - Rate Limit Config: exposes max_calls_per_hour; enforcement lives in the voice_calls endpoint
    - Security: Secure token handling and room access control
    - Async Operations: Full async/await support for non-blocking operations
    - Video Support: video calls with configured quality and screen-share settings
    - Template Management: Separate room templates for voice and video calls

Classes:
    CallStatus: Enum of call session states (created/ringing/active/ended/cancelled/failed)
    ParticipantStatus: Enum of participant join states (invited/joined/left/failed)
    CallEventType: Enum of call lifecycle event types recorded to etown_call_events
    VoiceCallService: 100ms integration service - rooms, JWT auth tokens, session/event persistence

Functions:
    No functions defined (module also builds the shared `voice_call_service` singleton)

Dependencies:
    - PyJWT: Signs the management and client auth tokens (jwt.encode) required by 100ms
    - uuid: Unique identifier generation for sessions and events
    - app.utils.http_client: get_http_client(), the tracked client used for every 100ms API call
    - app.core.config: Application configuration and 100ms credentials
    - app.db.client: Database for call session persistence and the with_retry decorator
    - logging (stdlib): Module-level logger via logging.getLogger(__name__)

Updates:
    18-Mar-2026: Initial creation for 100ms voice calling integration
    19-Mar-2026: Added video call support.
    27-Mar-2026: Audio calls use the speaker role.
    26-Aug-2026: Logged previously-silent exception handlers; rewrote vague log messages.
"""

import asyncio
import httpx
import jwt
import uuid
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from enum import Enum

from app.core.config import settings
from app.db.client import Database, with_retry
from app.utils.http_client import get_http_client
import logging

logger = logging.getLogger(__name__)


class CallStatus(str, Enum):
    """Call session status enumeration."""
    CREATED = "created"
    RINGING = "ringing"
    ACTIVE = "active"
    ENDED = "ended"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ParticipantStatus(str, Enum):
    """Call participant status enumeration."""
    INVITED = "invited"
    JOINED = "joined"
    LEFT = "left"
    FAILED = "failed"


class CallEventType(str, Enum):
    """Call event type enumeration."""
    CALL_CREATED = "call_created"
    USER_INVITED = "user_invited"
    USER_JOINED = "user_joined"
    USER_LEFT = "user_left"
    CALL_ENDED = "call_ended"
    CALL_FAILED = "call_failed"
    TOKEN_GENERATED = "token_generated"
    QUALITY_UPDATE = "quality_update"


class VoiceCallService:
    """Main service class for 100ms voice calling integration."""
    
    def __init__(self):
        """Load 100ms settings, raising ValueError when the access key, secret or voice template is missing."""
        self.app_access_key = settings.HMS_APP_ACCESS_KEY
        self.app_secret = settings.HMS_APP_SECRET
        self.base_url = settings.HMS_BASE_URL
        self.voice_template_id = settings.HMS_TEMPLATE_ID
        self.video_template_id = settings.HMS_VIDEO_TEMPLATE_ID
        self.region = settings.HMS_REGION
        self.call_timeout_hours = settings.HMS_CALL_TIMEOUT_HOURS
        self.max_calls_per_hour = settings.HMS_MAX_CALLS_PER_HOUR_PER_USER
        
        self.max_video_bitrate = settings.HMS_MAX_VIDEO_BITRATE
        self.enable_screen_share = settings.HMS_ENABLE_SCREEN_SHARE
        self.default_video_quality = settings.HMS_DEFAULT_VIDEO_QUALITY
        
        if not all([self.app_access_key, self.app_secret, self.voice_template_id]):
            logger.error("Voice call service initialization failed: Missing required 100ms credentials")
            raise ValueError("HMS_APP_ACCESS_KEY, HMS_APP_SECRET, and HMS_TEMPLATE_ID must be configured")
        
        logger.info("Voice call service initialized with 100ms configuration")
        logger.info(
            "Voice call service: video template resolved | template_id=%s",
            self.video_template_id or "unset (falling back to the voice template)",
        )

    def _generate_session_id(self) -> str:
        """Return a call session id built from a timestamp and a short random suffix."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        return f"call_{timestamp}_{unique_id}"
    
    @staticmethod
    def _get_call_roles(call_type: str) -> tuple[str, str]:
        """Return (initiator_role, target_role): speaker/speaker for voice calls, host/guest otherwise."""
        if call_type == "voice":
            return ("speaker", "speaker")
        else:
            return ("host", "guest")
    
    def _generate_management_token(self) -> str:
        """Return a 24-hour HS256 management JWT for 100ms server-side API calls."""
        now = datetime.now(timezone.utc)
        exp_time = now + timedelta(hours=24)
        
        payload = {
            'access_key': self.app_access_key,
            'type': 'management',
            'version': 2,
            'iat': int(now.timestamp()),
            'nbf': int(now.timestamp()),
            'exp': int(exp_time.timestamp()),
            'jti': str(uuid.uuid4())
        }
        
        return jwt.encode(
            payload,
            self.app_secret,
            algorithm='HS256'
        )
    
    def generate_auth_token(self, room_id: str, user_id: str, role: str = "guest") -> str:
        """Return a 24-hour HS256 app JWT letting a user join a 100ms room with a role."""
        now = datetime.now(timezone.utc)
        exp_time = now + timedelta(hours=24)
        
        payload = {
            'access_key': self.app_access_key,
            'room_id': room_id,
            'user_id': user_id,
            'role': role,
            'type': 'app',
            'version': 2,
            'iat': int(now.timestamp()),
            'nbf': int(now.timestamp()),
            'exp': int(exp_time.timestamp()),
            'jti': str(uuid.uuid4())
        }
        
        token = jwt.encode(
            payload,
            self.app_secret,
            algorithm='HS256'
        )
        
        logger.info(f"Generated authentication token for user {user_id} with role {role}")
        return token
    
    @with_retry()
    async def _make_hms_api_call(self, method: str, endpoint: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        """Call a 100ms API endpoint with a management token and return the JSON response.

        Args:
            method: HTTP method: GET, POST, PUT or DELETE.
            endpoint: API endpoint path without the base URL.
            payload: JSON body for POST and PUT requests.

        Returns:
            The decoded JSON response.

        Raises:
            ValueError: When the HTTP method is unsupported.
            Exception: When the response status is not 200 or 201.
        """
        management_token = self._generate_management_token()
        
        headers = {
            "Authorization": f"Bearer {management_token}",
            "Content-Type": "application/json"
        }
        
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        
        async with get_http_client() as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers)
            elif method.upper() == "POST":
                response = await client.post(url, headers=headers, json=payload or {})
            elif method.upper() == "PUT":
                response = await client.put(url, headers=headers, json=payload or {})
            elif method.upper() == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            if response.status_code not in [200, 201]:
                error_msg = f"100ms API call failed: {response.status_code} - {response.text}"
                logger.error(error_msg)
                raise Exception(error_msg)
                
            logger.debug(f"100ms API {method} call to {endpoint} completed successfully")
            return response.json()
    
    @with_retry()
    async def create_room(self, session_id: str, initiator_user_id: str, target_user_id: str, call_type: str = "voice") -> Dict[str, Any]:
        """Create a 100ms room from the voice or video template and return the API response."""
        if call_type == "video":
            room_name = f"Video Call - {session_id}"
            description = f"Video call between {initiator_user_id} and {target_user_id}"
            template_id = self.video_template_id or self.voice_template_id
        else:
            room_name = f"Voice Call - {session_id}"
            description = f"Voice call between {initiator_user_id} and {target_user_id}"
            template_id = self.voice_template_id
        
        payload = {
            "name": room_name,
            "description": description,
            "template_id": template_id,
            "region": self.region
        }

        logger.info(f"Creating {call_type} call room for session {session_id}")
        response = await self._make_hms_api_call("POST", "rooms", payload)
        
        logger.info(f"{call_type.title()} call room {response.get('id')} created for session {session_id}")
        return response
    
    @with_retry()
    async def get_room_details(self, room_id: str) -> Dict[str, Any]:
        """Return a 100ms room's details from the API."""
        response = await self._make_hms_api_call("GET", f"rooms/{room_id}")
        return response
    
    @with_retry()
    async def disable_room(self, room_id: str) -> Dict[str, Any]:
        """Disable a 100ms room, falling back to POST disable on a 405 and skipping if that also fails."""
        try:
            payload = {"enabled": False}
            response = await self._make_hms_api_call("PUT", f"rooms/{room_id}", payload)
            logger.info(f"Voice call room {room_id} disabled successfully")
            return response
            
        except Exception as e:
            if "405" in str(e) or "method not allowed" in str(e).lower():
                logger.warning(f"Standard room disable method unavailable, attempting alternative for room {room_id}")
                
                try:
                    response = await self._make_hms_api_call("POST", f"rooms/{room_id}/disable")
                    logger.info(f"Voice call room {room_id} disabled via alternative method")
                    return response
                except Exception as e2:
                    logger.warning(f"Room disable failed for {room_id}, proceeding with call termination: {e2}")
                    return {"status": "room_disable_skipped", "reason": "api_unavailable"}
            else:
                raise
    
    @with_retry()
    async def create_call_session(self, initiator_user_id: str, target_user_id: str, call_type: str = "voice") -> Dict[str, Any]:
        """Create a room, store the session, participants and auth tokens, and return them, disabling the room on failure."""
        session_id = self._generate_session_id()
        
        try:
            room_response = await self.create_room(session_id, initiator_user_id, target_user_id, call_type)
            room_id = room_response["id"]
            room_name = room_response["name"]
            
            await Database.execute(
                """
                INSERT INTO etown_call_sessions 
                (session_id, hms_room_id, hms_room_name, initiator_user_id, target_user_id, 
                 call_status, call_type, hms_region, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                session_id, room_id, room_name, initiator_user_id, target_user_id,
                CallStatus.CREATED.value, call_type, self.region, 
                datetime.now(timezone.utc), datetime.now(timezone.utc)
            )
            
            initiator_role, target_role = VoiceCallService._get_call_roles(call_type)
            
            initiator_participant_id = str(uuid.uuid4())
            target_participant_id = str(uuid.uuid4())
            
            await Database.execute(
                """
                INSERT INTO etown_call_participants 
                (participant_id, session_id, user_id, role, join_status, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                initiator_participant_id, session_id, initiator_user_id, initiator_role, 
                ParticipantStatus.INVITED.value, datetime.now(timezone.utc), datetime.now(timezone.utc)
            )
            
            await Database.execute(
                """
                INSERT INTO etown_call_participants 
                (participant_id, session_id, user_id, role, join_status, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                target_participant_id, session_id, target_user_id, target_role, 
                ParticipantStatus.INVITED.value, datetime.now(timezone.utc), datetime.now(timezone.utc)
            )
            
            initiator_token = self.generate_auth_token(room_id, initiator_user_id, initiator_role)
            target_token = self.generate_auth_token(room_id, target_user_id, target_role)
            
            token_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
            
            await Database.execute(
                """
                INSERT INTO etown_call_tokens 
                (token_id, session_id, user_id, hms_auth_token, token_type, expires_at, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                str(uuid.uuid4()), session_id, initiator_user_id, initiator_token, 
                "auth", token_expiry, datetime.now(timezone.utc)
            )
            
            await Database.execute(
                """
                INSERT INTO etown_call_tokens 
                (token_id, session_id, user_id, hms_auth_token, token_type, expires_at, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                str(uuid.uuid4()), session_id, target_user_id, target_token, 
                "auth", token_expiry, datetime.now(timezone.utc)
            )
            
            await self._track_call_event(
                session_id, initiator_user_id, CallEventType.CALL_CREATED, 
                {"room_id": room_id, "target_user_id": target_user_id}
            )
            
            logger.info(f"Voice call session {session_id} created successfully with room {room_id}")
            
            return {
                "session_id": session_id,
                "room_id": room_id,
                "room_name": room_name,
                "initiator_token": initiator_token,
                "target_token": target_token,
                "initiator_participant_id": initiator_participant_id,
                "target_participant_id": target_participant_id,
                "status": CallStatus.CREATED.value,
                "expires_at": token_expiry.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to create call session: {e}", exc_info=True)
            if 'room_id' in locals():
                try:
                    await self.disable_room(room_id)
                except Exception:
                    logger.warning("voice call: failed to disable room %s after a session-creation failure", room_id, exc_info=True)
            raise
    
    @with_retry()
    async def get_call_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return a call session with its participants and deleted/deactivated flags, or None when not found."""
        result = await Database.fetchrow(
            """
            SELECT cs.*,

                   etown_deleted_flag_ci(cs.initiator_user_id)     AS initiator_deleted,
                   etown_deactivated_flag_ci(cs.initiator_user_id) AS initiator_deactivated,
                   etown_deleted_flag_ci(cs.target_user_id)        AS target_deleted,
                   etown_deactivated_flag_ci(cs.target_user_id)    AS target_deactivated,
                   array_agg(
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
                   ) as participants
            FROM etown_call_sessions cs
            LEFT JOIN etown_call_participants cp ON cs.session_id = cp.session_id
            WHERE cs.session_id = $1
            GROUP BY cs.id
""",
            session_id
        )
        
        if result:
            session_data = dict(result)
            for field in ['created_at', 'updated_at', 'started_at', 'ended_at']:
                if session_data.get(field):
                    session_data[field] = session_data[field].isoformat()
            return session_data
        return None
    
    @with_retry()
    async def end_call_session(self, session_id: str, ended_by_user_id: str) -> Dict[str, Any]:
        """Disable the session's room, mark the session ended and record a call_ended event.

        Args:
            session_id: Call session identifier.
            ended_by_user_id: User who ended the call.

        Returns:
            Dict with session_id, status and ended_at.

        Raises:
            Exception: When the session does not exist.
        """
        session = await self.get_call_session(session_id)
        if not session:
            raise Exception(f"Call session {session_id} not found")
        
        room_id = session["hms_room_id"]
        
        await self.disable_room(room_id)
        
        end_time = datetime.now(timezone.utc)
        
        await Database.execute(
            """
            UPDATE etown_call_sessions 
            SET call_status = $1, ended_at = $2, updated_at = $3
            WHERE session_id = $4
            """,
            CallStatus.ENDED.value, end_time, end_time, session_id
        )
        
        await self._track_call_event(
            session_id, ended_by_user_id, CallEventType.CALL_ENDED, 
            {"room_id": room_id, "ended_at": end_time.isoformat()}
        )
        
        logger.info(f"Voice call session {session_id} ended successfully")
        return {"session_id": session_id, "status": CallStatus.ENDED.value, "ended_at": end_time.isoformat()}
    
    @with_retry()
    async def _track_call_event(self, session_id: str, user_id: Optional[str], 
                               event_type: CallEventType, event_data: Dict[str, Any]) -> None:
        """Insert a call lifecycle event with its JSON data into etown_call_events."""
        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc)
        
        await Database.execute(
            """
            INSERT INTO etown_call_events 
            (event_id, session_id, user_id, event_type, event_data, timestamp_utc, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            event_id, session_id, user_id, event_type.value, 
            json.dumps(event_data), timestamp, timestamp
        )


voice_call_service = VoiceCallService()