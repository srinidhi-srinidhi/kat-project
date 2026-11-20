"""Message Endpoints - HTTP routes for direct one-to-one conversations and messages.

HTTP layer for /api/message: checks the shared bearer token and participant rules, then delegates conversation creation, message sends and edits, read and delivery receipts, inbox, unread counts and per-side deletes to MessageService over etown_conversations and etown_messages.

Features:
    - Conversation existence check: Detect existing conversation by participant pair
    - Conversation detail retrieval: Fetch metadata and paginated messages by conversation ID
    - Conversation creation: Create conversation with optional initial message
    - Read-status update: Mark unread conversation messages as read for receiver
    - Delivery-status update: Mark received messages as delivered (two-tick receipts)
    - Send message: Post a new message (optionally a reply) with optional FCM push
    - Edit message: Sender edits their own message's text/caption
    - Inbox listing: Retrieve all conversations for a user with unread counts and last message
    - Unread count: Return total unread message count for a user
    - Per-side message delete: Either participant clears messages by ID list from their side only
    - Delete conversation(s): Clear a list of one or more conversations for the requesting participant
    - Access control: Enforce bearer token and participant-level authorization rules

Endpoints:
    GET /exists: Check whether conversation exists between user and opponent
    GET /messagelist: Fetch conversation and message list (paginated)
    POST /new: Create conversation and optional first message
    PUT /read: Mark receiver-side unread messages as read
    PUT /delivered: Mark receiver-side undelivered messages as delivered
    POST /send: Send a text/image/video/document message to an existing conversation (optionally a reply)
    PATCH /{message_id}: Edit a message's text/caption (sender only)
    GET /inbox: List all conversations for a user with unread counts
    GET /unread-count: Return total unread message count for a user
    DELETE /conversation: Clear a list of one or more conversations for the requesting user
    DELETE /id: Delete a list of one or more messages for the requesting side only

Classes:
    No classes defined (module uses request/response models imported from app.schemas.message)

Functions:
    check_conversation_exists(): Handle GET /exists
    get_conversation_details(): Handle GET /messagelist
    create_conversation(): Handle POST /new
    mark_conversation_read(): Handle PUT /read
    mark_conversation_delivered(): Handle PUT /delivered
    send_message(): Handle POST /send
    edit_message(): Handle PATCH /{message_id}
    get_inbox(): Handle GET /inbox
    get_unread_count(): Handle GET /unread-count
    delete_conversation(): Handle DELETE /conversation
    delete_messages(): Handle DELETE /id

Dependencies:
    - fastapi: APIRouter, query/path params, HTTP exception handling
    - app.core.security: Bearer token validation
    - app.schemas.message: Request/response model validation
    - app.etown.message.message_utils: Messaging business logic and DB integration
    - app.services.api_activity: track_api_call records inbox activity

Updates:
    27-May-2026: Created messaging endpoints: POST /send, GET /inbox, GET /unread-count, DELETE, pagination.
    10-Jul-2026: Added PATCH edit, PUT /delivered receipts and per-user conversation delete.
    27-Jul-2026: Added batch DELETE /id with IDs in the body, as a per-side delete.
    28-Jul-2026: GET /inbox items now include opponent_userprofile_id and opponent_contact_id
    19-Aug-2026: POST /send and POST /new accept message_type 'document'; media_file carries the text body for 'text'.
    20-Aug-2026: GET /inbox items include Etown_providertype for the opponent.
    02-Sep-2026: GET /inbox items include Etown_disabled for the opponent.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, status

from app.core.security import verify_token
from app.etown.message.message_utils import MessageService
from app.schemas.message import (
    ConversationDetailsResponse,
    ConversationExistsResponse,
    ConversationInboxResponse,
    ConversationParticipantType,
    CreateConversationRequest,
    CreateConversationResponse,
    DeleteConversationRequest,
    DeleteConversationResponse,
    DeleteMessageRequest,
    DeleteMessageResponse,
    EditMessageRequest,
    EditMessageResponse,
    MarkConversationDeliveredRequest,
    MarkConversationDeliveredResponse,
    MarkConversationReadRequest,
    MarkConversationReadResponse,
    SendMessageRequest,
    SendMessageResponse,
    UnreadCountResponse,
)
from app.services.api_activity import track_api_call

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/exists", response_model=ConversationExistsResponse)
async def check_conversation_exists(
    userprofile_id: str = Query(..., description="Current participant user profile ID"),
    opponent_id: str = Query(..., description="Opponent participant user profile ID"),
    opponent_type: ConversationParticipantType = Query(..., description="Opponent participant type"),
    token: str = Depends(verify_token),
) -> ConversationExistsResponse:
    """Handle GET /api/message/exists: report whether a conversation exists for the participant pair and its id."""
    try:
        conversation_id = await MessageService.check_conversation_exists(
            userprofile_id=userprofile_id,
            opponent_id=opponent_id,
            opponent_type=opponent_type,
        )
        return ConversationExistsResponse(
            exists=conversation_id is not None,
            conversation_id=conversation_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to check conversation existence", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check conversation existence: {str(exc)}",
        )


@router.get("/messagelist", response_model=ConversationDetailsResponse)
async def get_conversation_details(
    conversation_id: str = Query(..., description="Conversation ID"),
    userprofile_id: str = Query(..., description="Current participant user profile ID"),
    opponent_id: str = Query(..., description="Opponent participant user profile ID"),
    opponent_type: ConversationParticipantType = Query(..., description="Opponent participant type"),
    page: int = Query(default=1, ge=1, description="Page number (1-based). Each page returns up to 100 messages, newest first."),
    token: str = Depends(verify_token),
) -> ConversationDetailsResponse:
    """Handle GET /api/message/messagelist: return conversation details and a page of up to 100 messages, newest first."""
    try:
        payload = await MessageService.get_conversation_details(
            conversation_id=conversation_id,
            userprofile_id=userprofile_id,
            opponent_id=opponent_id,
            opponent_type=opponent_type,
            page=page,
        )
        return ConversationDetailsResponse(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch conversation details", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch conversation details: {str(exc)}",
        )


@router.post("/new", response_model=CreateConversationResponse)
async def create_conversation(
    request: CreateConversationRequest,
    token: str = Depends(verify_token),
) -> CreateConversationResponse:
    """Handle POST /api/message/new: create a conversation with an optional initial message."""
    try:
        payload = await MessageService.create_conversation(request)
        return CreateConversationResponse(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to create conversation", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create conversation: {str(exc)}",
        )


@router.put("/read", response_model=MarkConversationReadResponse)
async def mark_conversation_read(
    request: MarkConversationReadRequest,
    conversation_id: str = Query(..., description="Conversation ID"),
    token: str = Depends(verify_token),
) -> MarkConversationReadResponse:
    """Handle PUT /api/message/read: mark the receiver's unread messages in a conversation as read."""
    try:
        updated_count = await MessageService.mark_conversation_as_read(
            conversation_id=conversation_id,
            userprofile_id=request.userprofile_id,
            opponent_id=request.opponent_id,
            opponent_type=request.opponent_type,
        )
        return MarkConversationReadResponse(
            conversation_id=conversation_id,
            updated_count=updated_count,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to mark conversation read", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to mark conversation read: {str(exc)}",
        )


@router.put("/delivered", response_model=MarkConversationDeliveredResponse)
async def mark_conversation_delivered(
    request: MarkConversationDeliveredRequest,
    conversation_id: str = Query(..., description="Conversation ID"),
    token: str = Depends(verify_token),
) -> MarkConversationDeliveredResponse:
    """Handle PUT /api/message/delivered: mark the receiver's messages in a conversation as delivered."""
    try:
        updated_count = await MessageService.mark_conversation_as_delivered(
            conversation_id=conversation_id,
            userprofile_id=request.userprofile_id,
            opponent_id=request.opponent_id,
            opponent_type=request.opponent_type,
        )
        return MarkConversationDeliveredResponse(
            conversation_id=conversation_id,
            updated_count=updated_count,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to mark conversation delivered", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to mark conversation delivered: {str(exc)}",
        )


@router.post("/send", response_model=SendMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    request: SendMessageRequest,
    token: str = Depends(verify_token),
) -> SendMessageResponse:
    """Handle POST /api/message/send: send a text or media message to an existing conversation."""
    try:
        payload = await MessageService.send_message(request)
        return SendMessageResponse(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to send message", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send message: {str(exc)}",
        )


@router.patch("/{message_id}", response_model=EditMessageResponse)
async def edit_message(
    request: EditMessageRequest,
    message_id: str = Path(..., description="Message ID to edit"),
    token: str = Depends(verify_token),
) -> EditMessageResponse:
    """Handle PATCH /api/message/{message_id}: edit a message's text or caption on behalf of its sender."""
    try:
        updated = await MessageService.edit_message(
            message_id=message_id,
            userprofile_id=request.userprofile_id,
            message_text=request.message_text,
        )
        return EditMessageResponse(**updated)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to edit message", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to edit message: {str(exc)}",
        )


@router.get("/inbox", response_model=ConversationInboxResponse)
async def get_inbox(
    userprofile_id: str = Query(..., description="Current user profile ID"),
    page: int = Query(default=1, ge=1, description="Page number (1-based). Each page returns up to 100 conversations, newest first."),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> ConversationInboxResponse:
    """Handle GET /api/message/inbox: list a user's conversations, 100 per page newest first, and track the call."""
    track_api_call(
        background_tasks, "/api/message/inbox", userprofile_id=userprofile_id
    )
    try:
        payload = await MessageService.get_inbox(
            userprofile_id=userprofile_id,
            page=page,
        )
        return ConversationInboxResponse(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch inbox", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch inbox: {str(exc)}",
        )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    userprofile_id: str = Query(..., description="Current user profile ID"),
    token: str = Depends(verify_token),
) -> UnreadCountResponse:
    """Handle GET /api/message/unread-count: return a user's total unread messages across all conversations."""
    try:
        count = await MessageService.get_unread_count(userprofile_id)
        return UnreadCountResponse(unread_count=count)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch unread count", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch unread count: {str(exc)}",
        )


@router.delete("/conversation", response_model=DeleteConversationResponse)
async def delete_conversation(
    request: DeleteConversationRequest,
    token: str = Depends(verify_token),
) -> DeleteConversationResponse:
    """Handle DELETE /api/message/conversation: soft-delete listed conversations for the requesting participant only."""
    conversation_ids = [item.conversation_id for item in request.conversations]
    try:
        result = await MessageService.delete_conversations(
            conversation_ids=conversation_ids,
            userprofile_id=request.userprofile_id,
        )
        return DeleteConversationResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to delete conversation", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete conversation: {str(exc)}",
        )


@router.delete("/id", response_model=DeleteMessageResponse)
async def delete_messages(
    request: DeleteMessageRequest,
    token: str = Depends(verify_token),
) -> DeleteMessageResponse:
    """Handle DELETE /api/message/id: delete the listed messages from the requesting participant's side only."""
    message_ids = [item.message_id for item in request.messages]
    try:
        result = await MessageService.delete_messages(
            message_ids=message_ids,
            userprofile_id=request.userprofile_id,
        )
        return DeleteMessageResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to delete messages", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete messages: {str(exc)}",
        )
