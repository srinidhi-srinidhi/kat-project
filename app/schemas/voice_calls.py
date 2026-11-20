"""Voice Call Schemas - Pydantic request and response models for the voice call routes.

Models for the /api/call lifecycle of creating, joining, checking, ending and listing calls, with enum-backed statuses, participant media settings, field validators and a structured error response.

Features:
    - Call Session Models: Request and response models for the create/join/status/end lifecycle
    - Enum-Backed State: Status, participant status, call type and role enums constrain fields
    - Participant & Media Controls: Per-participant status plus audio/video/bitrate preferences
    - Field Validation: Rejects self-calls and placeholder ended_by_user_id values
    - Lifecycle Flags: deleted/deactivated markers on participants and session info
    - Pagination: CallListRequest/CallListResponse limit/offset paging with status and date filters
    - Schema Examples: request/response models carry json_schema_extra examples for OpenAPI
    - Structured Errors: VoiceCallErrorResponse standardises error_code/message/details

Classes:
    CallStatusEnum: Enum of call lifecycle states (created/ringing/active/ended/cancelled/failed)
    ParticipantStatusEnum: Enum of participant join states (invited/joined/left/failed)
    CallTypeEnum: Enum distinguishing voice vs video calls
    ParticipantRoleEnum: Enum of participant roles (host/guest/speaker)
    VideoPreferences: Video quality/bitrate/screen-share/camera/microphone settings for video calls
    CreateCallRequest: Request model for creating a new voice or video call session
    MediaControls: Optional camera/microphone enabled overrides when joining a call
    JoinCallRequest: Request model for joining an existing call session
    CallStatusRequest: Request model for checking a call session's status
    EndCallRequest: Request model for ending an active call session
    CallListRequest: Request model for retrieving paginated call history
    CallParticipantInfo: Per-participant status, media flags, and deleted/deactivated markers
    CreateCallResponse: Response model with room details and per-participant auth tokens
    JoinCallResponse: Response model with a join token and participant/role info
    CallStatusResponse: Response model with current call state, participants, and quality metrics
    EndCallResponse: Response model confirming call termination and duration
    CallSessionInfo: Full call session detail, including initiator/target deleted/deactivated flags
    CallListResponse: Paginated call history response model
    VoiceCallErrorResponse: Structured error response for voice-calling API failures

Functions:
    No functions defined (validation lives in field_validator classmethods on the models)

Dependencies:
    - pydantic: Data validation and serialization framework
    - typing: Type hint support for complex data structures
    - datetime: Date and time handling for call timestamps
    - enum: Enumeration support for call status and event types

Updates:
    18-Mar-2026: Initial creation for 100ms voice calling integration
"""

from pydantic import BaseModel, Field, validator
from pydantic import field_validator, ConfigDict
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from enum import Enum


class CallStatusEnum(str, Enum):
    """Enumeration of call status values."""
    CREATED = "created"
    RINGING = "ringing" 
    ACTIVE = "active"
    ENDED = "ended"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ParticipantStatusEnum(str, Enum):
    """Enumeration of participant status values."""
    INVITED = "invited"
    JOINED = "joined"
    LEFT = "left"
    FAILED = "failed"


class CallTypeEnum(str, Enum):
    """Enumeration of call types."""
    VOICE = "voice"
    VIDEO = "video"


class ParticipantRoleEnum(str, Enum):
    """Enumeration of participant roles."""
    HOST = "host"
    GUEST = "guest"
    SPEAKER = "speaker"


class VideoPreferences(BaseModel):
    """Video quality and preference settings for video calls."""
    
    quality: Optional[Literal["low", "medium", "high"]] = Field(default="medium", description="Video quality preference")
    max_bitrate: Optional[int] = Field(default=1000, description="Maximum video bitrate in kbps", ge=100, le=5000)
    enable_screen_share: Optional[bool] = Field(default=True, description="Allow screen sharing")
    camera_default: Optional[bool] = Field(default=True, description="Camera enabled by default")
    microphone_default: Optional[bool] = Field(default=True, description="Microphone enabled by default")


class CreateCallRequest(BaseModel):
    """Request model for creating a new voice or video call session."""
    
    initiator_user_id: str = Field(..., description="User ID of the call initiator", min_length=1, max_length=255)
    target_user_id: str = Field(..., description="User ID of the call target", min_length=1, max_length=255) 
    call_type: CallTypeEnum = Field(default=CallTypeEnum.VOICE, description="Type of call (voice or video)")
    video_preferences: Optional[VideoPreferences] = Field(default=None, description="Video quality preferences (for video calls only)")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional call metadata")
    
    @field_validator('target_user_id')
    @classmethod
    def validate_different_users(cls, v, info):
        """Reject a target_user_id equal to initiator_user_id."""
        if hasattr(info, 'data') and info.data.get('initiator_user_id') and v == info.data['initiator_user_id']:
            raise ValueError("Target user cannot be the same as initiator user")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "initiator_user_id": "user_123", 
                "target_user_id": "user_456",
                "call_type": "voice/video",
                "video_preferences": {
                    "quality": "high",
                    "max_bitrate": 2000,
                    "enable_screen_share": True,
                    "camera_default": True,
                    "microphone_default": True
                },
                "metadata": {"context": "service_inquiry"}
            }
        }
    )


class MediaControls(BaseModel):
    """Media control settings for call participants."""
    
    camera_enabled: Optional[bool] = Field(default=None, description="Camera enabled status (None = use call default)")
    microphone_enabled: Optional[bool] = Field(default=None, description="Microphone enabled status (None = use call default)")


class JoinCallRequest(BaseModel):
    """Request model for joining an existing call session."""
    
    session_id: str = Field(..., description="Unique call session identifier", min_length=1, max_length=255)
    user_id: str = Field(..., description="User ID of the person joining the call", min_length=1, max_length=255)
    media_controls: Optional[MediaControls] = Field(default=None, description="Media control preferences when joining")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "call_20260318_123456_abc123",
                "user_id": "user_456"
            }
        }
    )


class CallStatusRequest(BaseModel):
    """Request model for checking call session status."""
    
    session_id: str = Field(..., description="Unique call session identifier", min_length=1, max_length=255)
    user_id: Optional[str] = Field(default=None, description="Optional user ID for permission check", max_length=255)
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "call_20260318_123456_abc123",
                "user_id": "user_123"
            }
        }
    )


class EndCallRequest(BaseModel):
    """Request model for ending an active call session."""
    
    session_id: str = Field(..., description="Unique call session identifier", min_length=1, max_length=255)
    ended_by_user_id: str = Field(..., description="User ID of person ending the call", min_length=1, max_length=255)
    reason: Optional[str] = Field(default=None, description="Optional reason for ending call", max_length=500)
    
    @field_validator('ended_by_user_id')
    @classmethod
    def validate_ended_by_user_id(cls, v):
        """Reject an empty or placeholder ended_by_user_id and return it stripped."""
        if not v or v.strip() == "":
            raise ValueError("ended_by_user_id cannot be empty")
        invalid_values = {"string", "test", "null", "undefined", "user", "userid"}
        if v.lower().strip() in invalid_values:
            raise ValueError(f"ended_by_user_id cannot be '{v}' - must be a valid user identifier")
        return v.strip()

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "call_20260318_123456_abc123", 
                "ended_by_user_id": "user_123",
                "reason": "Call completed successfully"
            }
        }
    )


class CallListRequest(BaseModel):
    """Request model for retrieving call history with pagination."""
    
    user_id: str = Field(..., description="User ID to get call history for", min_length=1, max_length=255)
    limit: int = Field(default=10, description="Maximum number of calls to return", ge=1, le=100)
    offset: int = Field(default=0, description="Number of calls to skip for pagination", ge=0)
    status_filter: Optional[List[CallStatusEnum]] = Field(default=None, description="Filter by call status")
    from_date: Optional[datetime] = Field(default=None, description="Filter calls from this date")
    to_date: Optional[datetime] = Field(default=None, description="Filter calls to this date")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "user_123",
                "limit": 20,
                "offset": 0,
                "status_filter": ["ended", "active"],
                "from_date": "2026-03-01T00:00:00Z",
                "to_date": "2026-03-18T23:59:59Z"
            }
        }
    )


class CallParticipantInfo(BaseModel):
    """Model for call participant information."""
    
    participant_id: str = Field(..., description="Unique participant identifier")
    user_id: str = Field(..., description="User ID of the participant") 
    role: ParticipantRoleEnum = Field(..., description="Participant role in the call")
    join_status: ParticipantStatusEnum = Field(..., description="Current join status")
    joined_at: Optional[datetime] = Field(default=None, description="Timestamp when user joined")
    left_at: Optional[datetime] = Field(default=None, description="Timestamp when user left")
    audio_enabled: bool = Field(default=True, description="Whether audio is enabled")
    video_enabled: bool = Field(default=False, description="Whether video is enabled")
    connection_quality: Optional[Dict[str, Any]] = Field(default=None, description="Connection quality metrics")
    deleted: Optional[str] = Field(
        default="false",
        description="'true' when this participant's account has been deleted",
    )
    deactivated: Optional[str] = Field(
        default="false",
        description=(
            "'true' when this participant's account is deactivated and has not since "
            "been reactivated"
        ),
    )


class CreateCallResponse(BaseModel):
    """Response model for successful call creation."""
    
    session_id: str = Field(..., description="Unique call session identifier")
    room_id: str = Field(..., description="100ms room identifier")
    room_name: str = Field(..., description="Human-readable room name")
    initiator_token: str = Field(..., description="JWT auth token for call initiator")
    target_token: str = Field(..., description="JWT auth token for call target")
    initiator_participant_id: str = Field(..., description="Participant ID for initiator")
    target_participant_id: str = Field(..., description="Participant ID for target")
    status: CallStatusEnum = Field(..., description="Current call status")
    expires_at: str = Field(..., description="Token expiration timestamp")
    call_url: Optional[str] = Field(default=None, description="Direct call URL for frontend")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "call_20260318_123456_abc123",
                "room_id": "6408f2b7f8456a8b90a67c12",
                "room_name": "Voice Call - call_20260318_123456_abc123",
                "initiator_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "target_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...", 
                "initiator_participant_id": "part_123456",
                "target_participant_id": "part_789012",
                "status": "created",
                "expires_at": "2026-03-19T12:34:56Z",
                "call_url": "https://etownservices.com/call/6408f2b7f8456a8b90a67c12"
            }
        }
    )


class JoinCallResponse(BaseModel):
    """Response model for successful call join."""
    
    session_id: str = Field(..., description="Unique call session identifier")
    room_id: str = Field(..., description="100ms room identifier") 
    user_token: str = Field(..., description="JWT auth token for joining user")
    participant_id: str = Field(..., description="Participant ID for joining user")
    role: ParticipantRoleEnum = Field(..., description="User role in the call")
    call_url: Optional[str] = Field(default=None, description="Optional direct call URL for frontend")
    expires_at: str = Field(..., description="Token expiration timestamp")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "call_20260318_123456_abc123",
                "room_id": "6408f2b7f8456a8b90a67c12",
                "user_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "participant_id": "part_789012", 
                "role": "guest",
                "call_url": "https://etownservices.com/call/6408f2b7f8456a8b90a67c12?token=...",
                "expires_at": "2026-03-19T12:34:56Z"
            }
        }
    )


class CallStatusResponse(BaseModel):
    """Response model for call status information."""
    
    session_id: str = Field(..., description="Unique call session identifier")
    room_id: str = Field(..., description="100ms room identifier")
    status: CallStatusEnum = Field(..., description="Current call status")
    call_type: CallTypeEnum = Field(..., description="Type of call")
    participants: List[CallParticipantInfo] = Field(..., description="List of call participants")
    created_at: datetime = Field(..., description="Call creation timestamp") 
    started_at: Optional[datetime] = Field(default=None, description="Call start timestamp")
    ended_at: Optional[datetime] = Field(default=None, description="Call end timestamp")
    duration_seconds: Optional[int] = Field(default=None, description="Call duration in seconds")
    quality_metrics: Optional[Dict[str, Any]] = Field(default=None, description="Call quality metrics")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "call_20260318_123456_abc123",
                "room_id": "6408f2b7f8456a8b90a67c12", 
                "status": "active",
                "call_type": "voice",
                "participants": [
                    {
                        "participant_id": "part_123456",
                        "user_id": "user_123",
                        "role": "host", 
                        "join_status": "joined",
                        "joined_at": "2026-03-18T10:30:00Z",
                        "audio_enabled": True,
                        "video_enabled": False
                    }
                ],
                "created_at": "2026-03-18T10:29:45Z",
                "started_at": "2026-03-18T10:30:00Z", 
                "duration_seconds": 120
            }
        }
    )


class EndCallResponse(BaseModel):
    """Response model for successful call termination."""
    
    session_id: str = Field(..., description="Unique call session identifier")
    status: CallStatusEnum = Field(..., description="Final call status")
    ended_at: datetime = Field(..., description="Call end timestamp")
    duration_seconds: Optional[int] = Field(default=None, description="Total call duration in seconds")
    ended_by: str = Field(..., description="User ID who ended the call")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "call_20260318_123456_abc123",
                "status": "ended",
                "ended_at": "2026-03-18T10:45:30Z",
                "duration_seconds": 930,
                "ended_by": "user_123"
            }
        }
    )


class CallSessionInfo(BaseModel):
    """Model for detailed call session information."""
    
    session_id: str = Field(..., description="Unique call session identifier")
    room_id: str = Field(..., description="100ms room identifier")
    room_name: str = Field(..., description="Human-readable room name")
    initiator_user_id: str = Field(..., description="Call initiator user ID")
    target_user_id: str = Field(..., description="Call target user ID")
    status: CallStatusEnum = Field(..., description="Current call status")
    call_type: CallTypeEnum = Field(..., description="Type of call")
    participants: List[CallParticipantInfo] = Field(..., description="List of participants")
    created_at: datetime = Field(..., description="Call creation timestamp")
    started_at: Optional[datetime] = Field(default=None, description="Call start timestamp")
    ended_at: Optional[datetime] = Field(default=None, description="Call end timestamp") 
    duration_seconds: Optional[int] = Field(default=None, description="Call duration in seconds")
    quality_metrics: Optional[Dict[str, Any]] = Field(default=None, description="Call quality data")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional call metadata")
    initiator_deleted: Optional[str] = Field(
        default="false", description="'true' when the initiator's account has been deleted"
    )
    initiator_deactivated: Optional[str] = Field(
        default="false",
        description="'true' when the initiator is deactivated and not since reactivated",
    )
    target_deleted: Optional[str] = Field(
        default="false", description="'true' when the target's account has been deleted"
    )
    target_deactivated: Optional[str] = Field(
        default="false",
        description="'true' when the target is deactivated and not since reactivated",
    )


class CallListResponse(BaseModel):
    """Response model for paginated call history."""
    
    calls: List[CallSessionInfo] = Field(..., description="List of call sessions")
    total_count: int = Field(..., description="Total number of calls matching criteria")
    limit: int = Field(..., description="Maximum calls per page")
    offset: int = Field(..., description="Number of calls skipped")
    has_more: bool = Field(..., description="Whether there are more calls available")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "calls": [
                    {
                        "session_id": "call_20260318_123456_abc123",
                        "room_id": "6408f2b7f8456a8b90a67c12",
                        "room_name": "Voice Call - call_20260318_123456_abc123",
                        "initiator_user_id": "user_123",
                        "target_user_id": "user_456",
                        "status": "ended",
                        "call_type": "voice",
                        "participants": [],
                        "created_at": "2026-03-18T10:29:45Z",
                        "started_at": "2026-03-18T10:30:00Z",
                        "ended_at": "2026-03-18T10:45:30Z",
                        "duration_seconds": 930
                    }
                ],
                "total_count": 1,
                "limit": 10, 
                "offset": 0,
                "has_more": False
            }
        }
    )


class VoiceCallErrorResponse(BaseModel):
    """Error response model for voice calling operations."""
    
    error_code: str = Field(..., description="Unique error code identifier")
    error_message: str = Field(..., description="Human-readable error message")
    error_details: Optional[Dict[str, Any]] = Field(default=None, description="Additional error context")
    session_id: Optional[str] = Field(default=None, description="Related session ID if applicable")
    timestamp: str = Field(..., description="Error occurrence timestamp in ISO format")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error_code": "CALL_SESSION_NOT_FOUND",
                "error_message": "The specified call session could not be found",
                "error_details": {"session_id": "call_20260318_123456_invalid"},
                "session_id": "call_20260318_123456_invalid",
                "timestamp": "2026-03-18T10:30:00Z"
            }
        }
    )