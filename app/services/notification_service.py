"""Call Push Service - rings and declines voice calls over APNs VoIP and Firebase Cloud Messaging.

Service behind /api/call: builds the call session with the voice call service, looks up every callee device, rings iOS devices over APNs VoIP with FCM as fallback and all others over FCM, and sends decline pushes to each opponent device.

Features:
    - FCM Integration: Direct Firebase Cloud Messaging API calls for push notifications
    - Call Session Generation: Integration with voice call service for JWT tokens and room creation
    - Device Token Lookup: Query user device tokens from etown_contactdevices table
    - Multi-device Ringing: Every callee device rings in one session; VoIP for iOS, FCM otherwise
    - VoIP Fallback: A device whose APNs VoIP send fails is retried over FCM
    - OAuth Integration: Support for OAuth token-based FCM authentication
    - Error Handling: Typed FCM/APNs exceptions with parsed FCM error responses
    - Session Management: Generate complete call session data for notification payload
    - JWT Token Generation: Reuse existing voice call service for participant tokens
    - Room Management: Create temporary room IDs and session identifiers for calls
    - Notification Formatting: Builds FCM call/decline payloads and minimal VoIP payloads
    - Decline Notifications: Decline push to every opponent device; succeeds if any device succeeds

Classes:
    FCMError(): Base exception for Firebase Cloud Messaging failures (carries status_code/response_text)
    FCMAuthenticationError(): FCM 401/403 authentication or authorization failure
    FCMInvalidTokenError(): FCM 400 invalid, expired, or malformed device token
    FCMQuotaExceededError(): FCM 429 rate-limit exceeded
    FCMNetworkError(): Network-level failure reaching the FCM endpoint
    APNsError(): Base exception for Apple Push Notification service (VoIP) failures
    APNsAuthenticationError(): APNs certificate/authentication failure
    APNsInvalidTokenError(): Invalid VoIP device token
    APNsNetworkError(): Network-level failure reaching APNs
    NotificationService(): Sends call and decline push notifications — VoIP to iOS, FCM to the rest

Functions:
    No functions defined (the push flow lives in NotificationService methods)

Dependencies:
    - aiohttp: HTTP client error types for FCM API communication
    - aioapns: Optional APNs VoIP delivery; FCM is used when unavailable
    - datetime: Timestamp and expiration handling
    - uuid: Unique identifier generation for rooms, participants, and CallKit session IDs
    - app.services.voice_call_service: JWT token generation and call session management
    - app.etown.devices.devices_utils: Multi-device (FCM + VoIP) token lookup from database
    - app.core.config: FCM/APNs settings — endpoint, project id, certificate path, bundle id
    - app.core.logging: Centralized logging infrastructure
    - app.schemas.api: Notification request/response models
    - app.utils.http_client: Shared async HTTP client used for the FCM send call

Updates:
    25-Mar-2026: Initial creation with FCM integration and call session generation
    28-Jul-2026: Call notifications ring and cancel on every callee device; send_voip_push_notification() takes voip_session_id.
    26-Aug-2026: Logged silent exception handlers with tracebacks; rewrote vague log messages.
"""

import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, TYPE_CHECKING
import uuid
import json
import ssl
from pathlib import Path

from app.services.voice_call_service import VoiceCallService
from app.etown.devices.devices_utils import (
    get_device_push_targets_by_userprofile_id,
    get_device_tokens_by_userprofile_id,
)
from app.schemas.api import (
    PushNotificationRequest,
    CallSessionResponse,
    DeclineNotificationRequest,
    DeclineNotificationResponse,
    FCMMessage,
    NotificationPayload,
    NotificationData
)
from app.core.logging import logger
from app.core.config import settings
from app.utils.http_client import get_http_client

if TYPE_CHECKING:
    from aioapns import APNs, NotificationRequest
try:
    from aioapns import APNs, PushType
    import ssl
    APNS_AVAILABLE = True
    logger.info("aioapns library loaded successfully - VoIP push notifications enabled")
except ImportError:
    logger.info("aioapns library not available - VoIP push notifications disabled. FCM notifications will be used as fallback.")
    APNS_AVAILABLE = False
    APNs = None
    PushType = None


class FCMError(Exception):
    """Base FCM error class."""
    def __init__(self, message: str, status_code: int = None, response_text: str = None):
        """Store the message plus the optional HTTP status code and response text."""
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class FCMAuthenticationError(FCMError):
    """FCM authentication/authorization error (401/403)."""
    pass


class FCMInvalidTokenError(FCMError):
    """Invalid device token error (400)."""
    pass


class FCMQuotaExceededError(FCMError):
    """FCM quota exceeded error (429)."""
    pass


class FCMNetworkError(FCMError):
    """Network connection error."""
    pass


class APNsError(Exception):
    """Base APNs error class."""
    def __init__(self, message: str, status_code: int = None, response_text: str = None):
        """Store the message plus the optional HTTP status code and response text."""
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class APNsAuthenticationError(APNsError):
    """APNs certificate authentication error."""
    pass


class APNsInvalidTokenError(APNsError):
    """Invalid VoIP device token error."""
    pass


class APNsNetworkError(APNsError):
    """Network connection error to APNs."""
    pass


class NotificationService:
    """Service for sending push notifications via Firebase Cloud Messaging."""
    
    def __init__(self):
        """Set up the voice call service, FCM settings and, when configured, the APNs client."""
        self.voice_call_service = VoiceCallService()
        self.fcm_endpoint = settings.FCM_ENDPOINT
        self.fcm_project_id = settings.FCM_PROJECT_ID
        
        self.apns_client = None
        if APNS_AVAILABLE and settings.APNS_CERTIFICATE_PATH and settings.APNS_BUNDLE_ID:
            try:
                cert_path = Path(settings.APNS_CERTIFICATE_PATH)
                if cert_path.exists():
                    use_sandbox = settings.APNS_ENVIRONMENT.lower() == "sandbox"
                    apns_kwargs = {
                        "client_cert": str(cert_path),
                        "use_sandbox": use_sandbox
                    }
                    if settings.APNS_CERTIFICATE_PASSPHRASE:
                        apns_kwargs["password"] = settings.APNS_CERTIFICATE_PASSPHRASE
                    
                    self.apns_client = APNs(**apns_kwargs)
                    logger.info(f"aioapns client initialized for {settings.APNS_ENVIRONMENT} environment")
                else:
                    logger.warning(f"APNs certificate not found at: {settings.APNS_CERTIFICATE_PATH}")
            except Exception as e:
                logger.error(f"Failed to initialize aioapns client: {str(e)}", exc_info=True)
        else:
            logger.info("APNs not configured. VoIP push notifications will not be available.")
    
    async def send_call_notification(
        self, 
        request: PushNotificationRequest
    ) -> CallSessionResponse:
        """Ring every target device, VoIP first with FCM fallback, sharing one call session, and return it.

        Raises:
            ValueError: When the target user has no device token.
            Exception: The last delivery error when no device was reached.
        """
        logger.info(f"Sending call notification from {request.initiator_userprofile_id} to {request.target_userprofile_id}")

        targets = await get_device_push_targets_by_userprofile_id(request.target_userprofile_id)
        voip_targets = [t for t in targets if t["voip_token"]]
        fcm_tokens = [t["fcm_token"] for t in targets if t["fcm_token"] and not t["voip_token"]]

        if not targets:
            raise ValueError(f"Device token not found for user: {request.target_userprofile_id}")

        call_session: Optional[CallSessionResponse] = None
        delivered = 0
        last_error: Optional[Exception] = None

        if voip_targets and self.apns_client:
            logger.info(
                f"Found {len(voip_targets)} VoIP device(s) for user {request.target_userprofile_id}, "
                f"sending VoIP push notification"
            )
            for target in voip_targets:
                voip_request = (
                    request if call_session is None
                    else request.model_copy(update=self._session_overrides(call_session))
                )
                try:
                    session = await self.send_voip_push_notification(
                        voip_request,
                        target["voip_token"],
                        voip_session_id=call_session.session_id if call_session else None,
                    )
                    delivered += 1
                    if call_session is None:
                        call_session = session
                except Exception as e:
                    last_error = e
                    logger.error(f"VoIP push notification failed, falling back to FCM: {str(e)}", exc_info=True)
                    if target["fcm_token"]:
                        fcm_tokens.append(target["fcm_token"])
        elif voip_targets:
            fcm_tokens.extend(t["fcm_token"] for t in voip_targets if t["fcm_token"])

        fcm_tokens = list(dict.fromkeys(fcm_tokens))

        if fcm_tokens:
            if call_session is None:
                if all([
                    request.room_id,
                    request.room_name,
                    request.session_id,
                    request.initiator_token,
                    request.target_token,
                    request.initiator_participant_id,
                    request.target_participant_id
                ]):
                    call_session = CallSessionResponse(
                        call_url=f"https://etownservices.com/call/{request.room_id}",
                        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace('+00:00', 'Z'),
                        initiator_participant_id=request.initiator_participant_id,
                        initiator_token=request.initiator_token,
                        room_id=request.room_id,
                        room_name=request.room_name,
                        session_id=request.session_id,
                        status="created",
                        target_participant_id=request.target_participant_id,
                        target_token=request.target_token,
                        target_role=request.target_role,
                        inviter_profile_img=request.inviter_profile_img
                    )
                else:
                    call_session = await self._create_call_session(
                        request.initiator_userprofile_id,
                        request.target_userprofile_id,
                        request.is_video,
                        target_role=request.target_role
                    )
                    call_session = call_session.model_copy(
                        update={
                            "inviter_profile_img": request.inviter_profile_img,
                            "target_role": request.target_role
                        }
                    )

            for device_token in fcm_tokens:
                fcm_message = await self._format_fcm_payload(
                    device_token=device_token,
                    title=request.title,
                    body=request.body,
                    is_video=request.is_video,
                    initiator_userprofile_id=request.initiator_userprofile_id,
                    target_userprofile_id=request.target_userprofile_id,
                    call_session=call_session,
                    target_role=request.target_role,
                    inviter_profile_img=request.inviter_profile_img
                )

                try:
                    await self._send_fcm_notification(request.oauth_token, fcm_message)
                    delivered += 1
                except Exception as e:
                    last_error = e
                    logger.error(
                        f"FCM call notification failed for token {device_token[:10]}...: {str(e)}"
                    , exc_info=True)

        if delivered == 0 or call_session is None:
            if last_error:
                raise last_error
            raise ValueError(f"Device token not found for user: {request.target_userprofile_id}")

        logger.info(
            f"Call notification sent successfully to {delivered} device(s) "
            f"for session: {call_session.session_id}"
        )
        return call_session

    @staticmethod
    def _session_overrides(call_session: CallSessionResponse) -> Dict[str, Any]:
        """Return the room, session, token and participant fields that pin a request to an existing call session."""
        return {
            "room_id": call_session.room_id,
            "room_name": call_session.room_name,
            "session_id": call_session.session_id,
            "initiator_token": call_session.initiator_token,
            "target_token": call_session.target_token,
            "initiator_participant_id": call_session.initiator_participant_id,
            "target_participant_id": call_session.target_participant_id,
        }
    
    async def send_voip_push_notification(
        self,
        request: PushNotificationRequest,
        voip_token: str,
        voip_session_id: Optional[str] = None
    ) -> CallSessionResponse:
        """Send an APNs VoIP push for a call and return the call session carrying the CallKit id.

        Args:
            request: Push notification request with call details.
            voip_token: Target device's VoIP token, 64 hex characters.
            voip_session_id: CallKit id of an in-flight call; a new UUID when omitted.

        Returns:
            The call session, with session_id set to the CallKit id.

        Raises:
            APNsError: When the APNs send fails.
            ValueError: When the token is malformed or the APNs client is not initialized.
        """
        if not self.apns_client:
            raise ValueError("APNs client not initialized. Check certificate configuration.")
        
        logger.info(f"Sending VoIP push notification to token: {voip_token[:8]}...")
        cleaned_token = voip_token.replace(" ", "").replace("-", "").lower()
        if len(cleaned_token) != 64:
            logger.error(f"Invalid VoIP device token length: {len(cleaned_token)}, expected 64 hex characters")
            raise ValueError(f"Invalid VoIP device token format. Length: {len(cleaned_token)}")
        try:
            int(cleaned_token, 16)
        except ValueError:
            logger.error(f"VoIP device token contains invalid hex characters", exc_info=True)
            raise ValueError("VoIP device token must be valid hexadecimal")
        logger.info(f"Using cleaned VoIP token: {cleaned_token[:8]}... (length: {len(cleaned_token)})")
        
        if all([
            request.room_id,
            request.room_name,
            request.session_id,
            request.initiator_token,
            request.target_token,
            request.initiator_participant_id,
            request.target_participant_id
        ]):
            call_session = CallSessionResponse(
                call_url=f"https://etown.100ms.live/meeting/{request.room_name}",
                expires_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                initiator_participant_id=request.initiator_participant_id,
                initiator_token=request.initiator_token,
                room_id=request.room_id,
                room_name=request.room_name,
                session_id=request.session_id,
                status="active",
                target_participant_id=request.target_participant_id,
                target_token=request.target_token,
                target_role=request.target_role or "guest",
                inviter_profile_img=request.inviter_profile_img
            )
        else:
            call_session = await self.voice_call_service.create_call_session(
                request.initiator_userprofile_id,
                request.target_userprofile_id,
                request.is_video,
                target_role=request.target_role
            )
            call_session = call_session.model_copy(
                update={
                    "inviter_profile_img": request.inviter_profile_img,
                    "target_role": request.target_role
                }
            )
        
        try:
            if not APNS_AVAILABLE:
                raise APNsError("APNs library (pyapns2) is not available. Cannot send VoIP push notifications.")
            
            from aioapns import NotificationRequest
            voip_session_id = voip_session_id or str(uuid.uuid4())
            caller_name = request.body.strip() if hasattr(request, 'body') and request.body else "Unknown Caller"
            payload_data = {
                "aps": {
                    "content-available": 1
                },
                "id": voip_session_id,
                "nameCaller": caller_name,
                "handle": request.initiator_userprofile_id,
                "isVideo": request.is_video.lower() == "true"
            }
            
            call_session.session_id = voip_session_id
            logger.info(
                "VoIP push: dispatching to APNs | token=%s... topic=%s session=%s caller=%s is_video=%s",
                cleaned_token[:8], settings.APNS_BUNDLE_ID, voip_session_id,
                caller_name, request.is_video,
            )
            
            notification_request = NotificationRequest(
                device_token=cleaned_token,
                message=payload_data,
                push_type=PushType.VOIP,
                apns_topic=settings.APNS_BUNDLE_ID
            )
            
            await self._send_apns_notification(notification_request)
            
            logger.info(f"VoIP push notification sent successfully for session: {call_session.session_id}")
            return call_session
            
        except Exception as e:
            logger.error(f"Failed to send VoIP push notification: {str(e)}", exc_info=True)
            raise APNsError(f"VoIP push notification failed: {str(e)}")
    
    def _format_minimal_voip_payload(
        self,
        caller_name: str,
        caller_handle: str,
        is_video: bool
    ) -> Dict[str, Any]:
        """Return a minimal VoIP push payload with a new call id, caller name, handle and video flag."""
        return {
            "aps": {
                "content-available": 1
            },
            "id": str(uuid.uuid4()),
            "nameCaller": caller_name,
            "handle": caller_handle,
            "isVideo": is_video
        }
    
    async def _send_apns_notification(self, notification_request: "NotificationRequest") -> None:
        """Send an aioapns notification request to APNs.

        Raises:
            APNsAuthenticationError: On a certificate error.
            APNsNetworkError: On a network or connection error.
            APNsError: For any other APNs failure, including rejected tokens.
        """
        if not self.apns_client:
            raise APNsError("APNs client not initialized")
        
        try:
            response = await self.apns_client.send_notification(notification_request)
            
            if response.is_successful:
                logger.info(f"VoIP push sent successfully to {notification_request.device_token[:8]}...")
            else:
                error_msg = f"aioapns error: {response.description or 'Unknown error'}"
                logger.error(f"APNs error details - Status: {response.status}, Description: {response.description}, Device token: {notification_request.device_token[:8]}...")
                
                if "BadDeviceToken" in str(response.description):
                    logger.error("BadDeviceToken suggests: 1) Wrong certificate type (need VoIP cert), 2) Token/cert environment mismatch, 3) Bundle ID mismatch")
                
                logger.error(error_msg)
                
                if response.status == 400:
                    raise APNsInvalidTokenError(error_msg)
                elif response.status in [401, 403]:
                    raise APNsAuthenticationError(error_msg)
                else:
                    raise APNsError(error_msg)
                    
        except Exception as e:
            logger.error(f"Unexpected error sending VoIP push: {str(e)}", exc_info=True)
            if "certificate" in str(e).lower():
                raise APNsAuthenticationError(f"Certificate error: {str(e)}")
            elif "network" in str(e).lower() or "connection" in str(e).lower():
                raise APNsNetworkError(f"Network error: {str(e)}")
            else:
                raise APNsError(f"APNs error: {str(e)}")

    async def send_decline_notification(
        self,
        request: DeclineNotificationRequest
    ) -> DeclineNotificationResponse:
        """Send the call-decline FCM push to every opponent device and return the data that was sent.

        Raises:
            ValueError: When the opponent has no device token.
            Exception: The last FCM error when no device was reached.
        """
        logger.info(f"Sending decline notification from {request.userprofile_id} to {request.opponent_userprofile_id}")

        device_tokens = await get_device_tokens_by_userprofile_id(request.opponent_userprofile_id)
        if not device_tokens:
            raise ValueError(f"Device token not found for user: {request.opponent_userprofile_id}")

        await self._send_decline_to_devices(request, device_tokens)

        if all([
            request.room_id,
            request.room_name,
            request.session_id,
            request.initiator_token,
            request.target_token,
            request.initiator_participant_id,
            request.target_participant_id
        ]):
            return DeclineNotificationResponse(
                success=True,
                message="Decline notification sent successfully",
                data={
                    "type": "decline_call",
                    "route": "/call",
                    "isVideo": request.is_video,
                    "userprofile": request.userprofile_id,
                    "opponentuserprofile": request.opponent_userprofile_id,
                    "room_id": request.room_id,
                    "room_name": request.room_name,
                    "session_id": request.session_id,
                    "initiator_token": request.initiator_token,
                    "target_token": request.target_token,
                    "initiator_participant_id": request.initiator_participant_id,
                    "target_participant_id": request.target_participant_id
                }
            )
        else:
            return DeclineNotificationResponse(
                success=True,
                message="Decline notification sent successfully",
                data={
                    "type": "decline_call",
                    "route": "/call",
                    "isVideo": request.is_video,
                    "userprofile": request.userprofile_id,
                    "opponentuserprofile": request.opponent_userprofile_id,
                    "room_id": request.room_id,
                    "room_name": request.room_name,
                    "session_id": request.session_id
                }
            )

    async def _send_decline_to_devices(
        self,
        request: DeclineNotificationRequest,
        device_tokens: list[str]
    ) -> None:
        """Send the decline push to each opponent FCM token.

        Raises:
            Exception: The last FCM error, only when no device was reached.
        """
        delivered = 0
        last_error: Optional[Exception] = None
        for device_token in device_tokens:
            fcm_message = await self._format_decline_fcm_payload(
                device_token=device_token,
                title=request.title,
                body=request.body,
                is_video=request.is_video,
                userprofile_id=request.userprofile_id,
                opponent_userprofile_id=request.opponent_userprofile_id,
                room_id=request.room_id,
                room_name=request.room_name,
                session_id=request.session_id
            )
            try:
                await self._send_fcm_notification(request.oauth_token, fcm_message)
                delivered += 1
            except Exception as e:
                last_error = e
                logger.error(f"Decline notification failed for token {device_token[:10]}...: {str(e)}", exc_info=True)

        if delivered == 0 and last_error:
            raise last_error

        logger.info(
            f"Decline notification sent successfully to {delivered} device(s) "
            f"for session: {request.session_id}"
        )

    async def _create_call_session(
        self,
        initiator_userprofile_id: str,
        target_userprofile_id: str,
        is_video: str,
        target_role: Optional[str] = None
    ) -> CallSessionResponse:
        """Build a new call session with room id, host and guest tokens, and a one-hour expiry."""
        session_id = self.voice_call_service._generate_session_id()
        room_id = str(uuid.uuid4())
        
        initiator_participant_id = f"part_{str(uuid.uuid4())[:6]}"
        target_participant_id = f"part_{str(uuid.uuid4())[:6]}"
        
        initiator_token = self.voice_call_service.generate_auth_token(
            room_id=room_id,
            user_id=initiator_userprofile_id,
            role="host"
        )
        
        target_token = self.voice_call_service.generate_auth_token(
            room_id=room_id,
            user_id=target_userprofile_id,
            role="guest"
        )
        
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace('+00:00', 'Z')
        
        call_url = f"https://etownservices.com/call/{room_id}"
        
        call_type = "Video" if is_video.lower() == "true" else "Voice"
        room_name = f"{call_type} Call - {session_id}"
        
        return CallSessionResponse(
            call_url=call_url,
            expires_at=expires_at,
            initiator_participant_id=initiator_participant_id,
            initiator_token=initiator_token,
            room_id=room_id,
            room_name=room_name,
            session_id=session_id,
            status="created",
            target_participant_id=target_participant_id,
            target_token=target_token,
            target_role=target_role
        )
    
    async def _format_fcm_payload(
        self,
        device_token: str,
        title: str,
        body: str,
        is_video: str,
        initiator_userprofile_id: str,
        target_userprofile_id: str,
        call_session: CallSessionResponse,
        target_role: Optional[str] = None,
        inviter_profile_img: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return an FCM call message with notification text and the call session in its data payload."""
        return {
            "message": {
                "token": device_token,
                "notification": {
                    "title": title,
                    "body": body
                },
                "data": {
                    "type": "call",
                    "route": "/call",
                    "isVideo": is_video,
                    "userprofile": initiator_userprofile_id,
                    "opponentuserprofile": target_userprofile_id,
                    "call_url": call_session.call_url,
                    "expires_at": call_session.expires_at,
                    "initiator_participant_id": call_session.initiator_participant_id,
                    "initiator_token": call_session.initiator_token,
                    "room_id": call_session.room_id,
                    "room_name": call_session.room_name,
                    "session_id": call_session.session_id,
                    "status": call_session.status,
                    "target_participant_id": call_session.target_participant_id,
                    "target_token": call_session.target_token,
                    "target_role": target_role or "",
                    "inviter_profile_img": inviter_profile_img or ""
                }
            }
        }
    
    async def _send_fcm_notification(self, oauth_token: str, payload: Dict[str, Any]) -> None:
        """POST a message to the FCM API, mapping failures to typed FCM errors.

        Raises:
            FCMAuthenticationError: On 401 or 403.
            FCMInvalidTokenError: On a 400 naming an invalid token.
            FCMQuotaExceededError: On 429.
            FCMNetworkError: When the connection fails.
            FCMError: For any other failure.
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {oauth_token}"
        }
        
        async with get_http_client() as session:
            try:
                async with session.post(
                    self.fcm_endpoint,
                    json=payload,
                    headers=headers,
                    timeout=30
                ) as response:
                    
                    if hasattr(response, 'text') and callable(response.text):
                        response_text = await response.text()
                        status_code = response.status
                    else:
                        response_text = response.text
                        status_code = response.status_code
                    
                    if status_code == 200:
                        logger.info(f"FCM notification sent successfully: {status_code}")
                        return
                    
                    error_detail = self._parse_fcm_error_response(response_text)
                    logger.error(f"FCM notification failed: {status_code} - {response_text}")
                    
                    if status_code == 401:
                        raise FCMAuthenticationError(
                            f"FCM authentication failed: OAuth token is invalid or expired. {error_detail}",
                            status_code=401,
                            response_text=response_text
                        )
                    elif status_code == 403:
                        raise FCMAuthenticationError(
                            f"FCM authorization failed: Insufficient permissions or project mismatch. {error_detail}",
                            status_code=403,
                            response_text=response_text
                        )
                    elif status_code == 400:
                        if "registration-token" in response_text.lower() or "invalid" in response_text.lower():
                            raise FCMInvalidTokenError(
                                f"FCM invalid device token: The device token may be expired, malformed, or unregistered. {error_detail}",
                                status_code=400,
                                response_text=response_text
                            )
                        else:
                            raise FCMError(
                                f"FCM bad request: Invalid payload format or missing required fields. {error_detail}",
                                status_code=400,
                                response_text=response_text
                            )
                    elif status_code == 429:
                        raise FCMQuotaExceededError(
                            f"FCM quota exceeded: Rate limit reached. Please retry after some time. {error_detail}",
                            status_code=429,
                            response_text=response_text
                        )
                    elif status_code >= 500:
                        raise FCMError(
                            f"FCM server error: Firebase service is temporarily unavailable. Please retry later. {error_detail}",
                            status_code=status_code,
                            response_text=response_text
                        )
                    else:
                        raise FCMError(
                            f"FCM unexpected error: {error_detail}",
                            status_code=status_code,
                            response_text=response_text
                        )
                        
            except aiohttp.ClientError as e:
                logger.error(f"FCM API connection error: {str(e)}", exc_info=True)
                raise FCMNetworkError(
                    f"FCM connection failed: Unable to reach Firebase servers. Check network connectivity and FCM endpoint URL.",
                    response_text=str(e)
                )
            except (FCMError, FCMAuthenticationError, FCMInvalidTokenError, FCMQuotaExceededError, FCMNetworkError):
                raise
            except Exception as e:
                logger.error(f"FCM notification unexpected error: {str(e)}", exc_info=True)
                raise FCMError(f"FCM notification failed: {str(e)}")
    
    def _parse_fcm_error_response(self, response_text: str) -> str:
        """Return a readable summary of an FCM error response, falling back to the truncated raw text."""
        try:
            error_data = json.loads(response_text)
            
            if "error" in error_data:
                error_info = error_data["error"]
                
                if isinstance(error_info, dict):
                    error_code = error_info.get("code", "UNKNOWN")
                    error_message = error_info.get("message", "No details provided")
                    error_status = error_info.get("status", "UNKNOWN_STATUS")
                    
                    return f"Error Code: {error_code}, Status: {error_status}, Message: {error_message}"
                else:
                    return f"Error: {error_info}"
                    
            if "details" in error_data and error_data["details"]:
                details = error_data["details"][0] if isinstance(error_data["details"], list) else error_data["details"]
                return f"Validation Error: {details}"
                
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Could not parse FCM error response: {e}")
            
        return f"Raw Error: {response_text[:200]}..." if len(response_text) > 200 else f"Raw Error: {response_text}"
    
    async def _format_decline_fcm_payload(
        self,
        device_token: str,
        title: str,
        body: str,
        is_video: str,
        userprofile_id: str,
        opponent_userprofile_id: str,
        room_id: str,
        room_name: str,
        session_id: str
    ) -> Dict[str, Any]:
        """Return an FCM decline_call message carrying the original call's room and session ids."""
        return {
            "message": {
                "token": device_token,
                "notification": {
                    "title": title,
                    "body": body
                },
                "data": {
                    "type": "decline_call",
                    "route": "/call",
                    "isVideo": is_video,
                    "userprofile": userprofile_id,
                    "opponentuserprofile": opponent_userprofile_id,
                    "room_id": room_id,
                    "room_name": room_name,
                    "session_id": session_id
                }
            }
        }
    
    async def generate_call_session_data(
        self,
        initiator_userprofile_id: str,
        target_userprofile_id: str,
        is_video: str = "false",
        target_role: Optional[str] = None
    ) -> CallSessionResponse:
        """Create and return call session data without sending any notification."""
        return await self._create_call_session(
            initiator_userprofile_id,
            target_userprofile_id,
            is_video,
            target_role=target_role
        )