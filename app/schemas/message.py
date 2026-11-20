"""Message Schemas - Pydantic request and response models for the direct messaging routes.

Schema layer for /api/message: validates and documents conversation lookup, creation and detail, sending, editing and replying to messages, read and delivery receipts, the inbox with unread counts, and per-side message and conversation deletes.

Features:
    - Conversation existence checks: Request/response models for duplicate prevention
    - Conversation detail retrieval: Models for metadata and paginated message lists
    - Conversation creation: Input validation for creating direct conversations
    - Read-status updates: Models for mark-as-read operations
    - Participant typing: Strict enum validation for member/provider types
    - Send message: Request/response for sending to an existing conversation
    - Inbox listing: Conversation list with unread counts and last message preview
    - Unread count: Total unread message count across all conversations
    - Per-side message delete: Request/response for clearing messages for one side only
    - Edit message: Request/response for editing a sent message's text/caption
    - Reply / quote: Optional reply reference plus a lightweight quoted-message preview
    - Delivery receipts: delivered_at plus a derived sent/delivered/read status per message
    - Per-user conversation delete: Response for clearing a conversation for one participant

Classes:
    ConversationParticipantType: Supported participant type values
    MessageType: Supported message type values
    MessageStatus: Derived delivery status values (sent/delivered/read)
    ReplyPreview: Lightweight preview of a quoted/replied-to message
    MessageItem: Single message payload model
    ConversationMetadata: Conversation metadata model
    ConversationExistsResponse: Response for existence lookup endpoint
    ConversationDetailsResponse: Response for conversation detail endpoint (paginated)
    CreateConversationRequest: Request model for create conversation endpoint
    CreateConversationResponse: Response model for create conversation endpoint
    MarkConversationReadRequest: Request model for mark-read endpoint
    MarkConversationReadResponse: Response model for mark-read endpoint
    MarkConversationDeliveredRequest: Request model for mark-delivered endpoint
    MarkConversationDeliveredResponse: Response model for mark-delivered endpoint
    SendMessageRequest: Request model for send message to existing conversation
    SendMessageResponse: Response model for send message endpoint
    EditMessageRequest: Request model for edit message endpoint
    EditMessageResponse: Response model for edit message endpoint
    ConversationInboxItem: Single item in inbox list with unread count and preview
    ConversationInboxResponse: Response model for inbox list endpoint
    UnreadCountResponse: Response model for unread count endpoint
    MessageDeleteItem: Single {message_id} entry in a delete request
    DeleteMessageRequest: Request model for the per-side message delete endpoint (list of messages)
    DeleteMessageStatus: Batch outcome values for the message delete endpoint (all/partial)
    DeleteMessageResponse: Batch summary response for the per-side message delete endpoint
    ConversationDeleteItem: Single {conversation_id} entry in a delete request
    DeleteConversationRequest: Request model for per-user conversation delete (list of conversations)
    DeleteConversationStatus: Batch outcome values for conversation delete (all/partial/none)
    DeleteConversationResponse: Response model for per-user conversation delete endpoint

Dependencies:
    - pydantic: Input/output validation for endpoint contracts
    - typing: Literal and Optional typing support
    - datetime: Timestamp typing for response fields

Updates:
    24-May-2026: Initial creation of direct messaging schemas
    27-May-2026: Added read_at, send/inbox/unread-count/delete schemas and has_more; file_url renamed media_file.
    10-Jul-2026: Added MessageStatus, ReplyPreview and the edit, delivered and delete schemas.
    27-Jul-2026: Added MessageDeleteItem/DeleteMessageRequest; DeleteMessageResponse became a per-side batch summary.
    18-Aug-2026: ConversationInboxItem gains the opponent's provider_type and badge_label.
    19-Aug-2026: Added 'document' message_type; media_file carries plain text when message_type is 'text'.
    20-Aug-2026: ConversationInboxItem carries Etown_providertype for the opponent.
    02-Sep-2026: ConversationInboxItem carries Etown_disabled for the opponent.
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


ConversationParticipantType = Literal["member", "provider"]
MessageType = Literal["text", "image", "file", "audio", "video", "document"]
MessageStatus = Literal["sent", "delivered", "read"]


class ReplyPreview(BaseModel):
    """Lightweight preview of the message a reply quotes."""

    id: str
    sender_id: Optional[str] = None
    sender_type: Optional[ConversationParticipantType] = None
    message_type: Optional[MessageType] = None
    message_text: Optional[str] = None
    file_url: Optional[str] = None
    is_deleted: bool = False


class MessageItem(BaseModel):
    """Single message model for conversation detail responses."""

    id: str
    conversation_id: str
    sender_id: str
    receiver_id: str
    is_read: bool
    read_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    edited_at: Optional[datetime] = None
    message_type: Optional[MessageType] = None
    sender_type: ConversationParticipantType
    receiver_type: ConversationParticipantType
    file_url: Optional[str] = None
    message_text: Optional[str] = None
    sender_name: Optional[str] = None
    receiver_name: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    reply_to: Optional[ReplyPreview] = None
    status: MessageStatus = "sent"


class ConversationMetadata(BaseModel):
    """Conversation metadata model."""

    id: str
    participant_1_id: str
    participant_1_type: ConversationParticipantType
    participant_2_id: str
    participant_2_type: ConversationParticipantType
    last_message_id: Optional[str] = None
    last_activity: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ConversationExistsResponse(BaseModel):
    """Response model for conversation existence checks."""

    exists: bool
    conversation_id: Optional[str] = None


class ConversationDetailsResponse(BaseModel):
    """Response model for conversation detail retrieval with optional cursor pagination."""

    conversation: ConversationMetadata
    messages: List[MessageItem]
    has_more: bool = False


class CreateConversationRequest(BaseModel):
    """Request model for creating a direct conversation."""

    userprofile_id: str = Field(..., description="Current user profile ID")
    opponent_id: str = Field(..., description="Opponent user profile ID")
    opponent_type: ConversationParticipantType = Field(..., description="Opponent type")
    initial_message: Optional[str] = Field(None, description="Optional initial message text")
    message_type: MessageType = Field(default="text", description="Initial message type: 'text', 'image', 'video', 'document', 'audio' or 'file'")
    media_file: Optional[str] = Field(None, description="Initial message payload: base64 (or data-URL) for a media type, plain text when message_type is 'text'")

    @model_validator(mode="after")
    def normalize_message_content(self) -> "CreateConversationRequest":
        """Move a text-typed media_file into initial_message and clear it so only media is uploaded."""
        if self.message_type == "text" and self.media_file:
            if not self.initial_message:
                self.initial_message = self.media_file
            self.media_file = None
        return self


class CreateConversationResponse(BaseModel):
    """Response model for conversation creation endpoint."""

    conversation: ConversationMetadata
    initial_message_id: Optional[str] = None
    created: bool


class MarkConversationReadRequest(BaseModel):
    """Request model for mark conversation read endpoint."""

    userprofile_id: str = Field(..., description="Current user profile ID")
    opponent_id: str = Field(..., description="Opponent user profile ID")
    opponent_type: ConversationParticipantType = Field(..., description="Opponent type")


class MarkConversationReadResponse(BaseModel):
    """Response model for mark conversation read endpoint."""

    conversation_id: str
    updated_count: int


class MarkConversationDeliveredRequest(BaseModel):
    """Request model for mark conversation delivered endpoint."""

    userprofile_id: str = Field(..., description="Current user profile ID (the receiver acking delivery)")
    opponent_id: str = Field(..., description="Opponent user profile ID")
    opponent_type: ConversationParticipantType = Field(..., description="Opponent type")


class MarkConversationDeliveredResponse(BaseModel):
    """Response model for mark conversation delivered endpoint."""

    conversation_id: str
    updated_count: int


class SendMessageRequest(BaseModel):
    """Request model for sending a message to an existing conversation.

    Supports text-only, media-only, or combined text+media in a single message.
    When sending combined: set message_type to the media type and provide both
    message_text (caption) and media_file (base64-encoded attachment).

    media_file is the content channel for every type: base64 (or a data-URL) for
    'image', 'video', 'document', 'audio' and 'file', and plain text for 'text'.
    Text-typed content is folded into message_text and never uploaded.
    """

    conversation_id: str = Field(..., description="Existing conversation ID")
    userprofile_id: str = Field(..., description="Sender user profile ID")
    message_text: Optional[str] = Field(None, description="Text content or caption")
    message_type: MessageType = Field(default="text", description="Message type: 'text', 'image', 'video', 'document', 'audio' or 'file'")
    media_file: Optional[str] = Field(None, description="Message payload: base64 (or data-URL) for a media type, plain text when message_type is 'text'")
    reply_to_message_id: Optional[str] = Field(None, description="Optional ID of a message in the same conversation this message replies to")
    oauth_token: Optional[str] = Field(None, description="Google OAuth token for FCM push notification")

    @model_validator(mode="after")
    def validate_message_content(self) -> "SendMessageRequest":
        """Move a text-typed media_file into message_text, then require message_text or media_file.

        Raises:
            ValueError: When neither message_text nor media_file is provided.
        """
        if self.message_type == "text" and self.media_file:
            if not self.message_text:
                self.message_text = self.media_file
            self.media_file = None
        if not self.message_text and not self.media_file:
            raise ValueError("Either message_text or media_file must be provided")
        return self


class SendMessageResponse(BaseModel):
    """Response model for send message endpoint."""

    message: MessageItem


class EditMessageRequest(BaseModel):
    """Request model for editing a sent message's text/caption.

    Only the original sender may edit. There is no time limit. For a media
    message the text acts as the caption; for a text message it must be non-empty.
    """

    userprofile_id: str = Field(..., description="Sender's user profile ID (must own the message)")
    message_text: str = Field(..., description="New text/caption for the message")


class EditMessageResponse(BaseModel):
    """Response model for edit message endpoint."""

    id: str
    message_text: Optional[str] = None
    edited_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ConversationInboxItem(BaseModel):
    """Single conversation item in the inbox list."""

    id: str
    participant_1_id: str
    participant_1_type: ConversationParticipantType
    participant_2_id: str
    participant_2_type: ConversationParticipantType
    last_message_id: Optional[str] = None
    last_activity: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    unread_count: int = 0
    last_message_text: Optional[str] = None
    last_message_type: Optional[str] = None
    sender_name: Optional[str] = None
    sender_image: Optional[str] = None
    sender_deleted: Optional[str] = None
    sender_deactivated: Optional[str] = None
    opponent_userprofile_id: Optional[str] = None
    opponent_contact_id: Optional[str] = None
    opponent_deleted: Optional[str] = None
    opponent_deactivated: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_disabled: Optional[bool] = Field(
        None,
        description=(
            "etown_provider.disabled of the opponent's provider profile; null when the "
            "opponent is a member or has no provider row"
        ),
    )
    opponent_provider_type: Optional[str] = None
    opponent_badge_label: Optional[str] = None


class ConversationInboxResponse(BaseModel):
    """Response model for inbox list endpoint."""

    conversations: List[ConversationInboxItem]
    total: int


class UnreadCountResponse(BaseModel):
    """Response model for total unread message count endpoint."""

    unread_count: int


class MessageDeleteItem(BaseModel):
    """Single message reference in a delete request."""

    message_id: str = Field(..., description="Message ID to delete for the requesting side")


class DeleteMessageRequest(BaseModel):
    """Request model for deleting one or more messages for the requesting side only.

    The caller may list their own messages, the opponent's, or both — whichever side they
    are on, the messages disappear from their view alone and the other participant keeps
    seeing them.

    Mirrors ``DeleteConversationRequest``: pass a single-item ``messages`` list to delete
    one message or several entries to delete a batch. IDs that don't exist, or whose
    conversation the caller isn't part of, are skipped and reported in the response's
    ``not_deleted_ids`` rather than failing the request.
    """

    userprofile_id: str = Field(
        ..., description="Requesting participant's user profile ID (sender or receiver)"
    )
    messages: List[MessageDeleteItem] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Messages to delete for the requesting side (1-100; single-item list for one)",
    )

    @model_validator(mode="after")
    def normalize_messages(self) -> "DeleteMessageRequest":
        """Strip blanks and de-duplicate by message_id (order preserved); require at least one."""
        seen: set[str] = set()
        cleaned: List[MessageDeleteItem] = []
        for item in self.messages:
            mid = (item.message_id or "").strip()
            if mid and mid not in seen:
                seen.add(mid)
                cleaned.append(MessageDeleteItem(message_id=mid))
        if not cleaned:
            raise ValueError("messages must contain at least one non-empty message_id")
        self.messages = cleaned
        return self


DeleteMessageStatus = Literal["all", "partial"]


class DeleteMessageResponse(BaseModel):
    """Response model for the per-side message delete endpoint (one or many IDs).

    ``status`` summarises the batch outcome: ``all`` (every requested message
    deleted for the caller) or ``partial`` (some deleted, some skipped).
    ``deleted_ids`` / ``not_deleted_ids`` give the per-message breakdown and are the
    authoritative result the client should drive its UI from. IDs that don't exist, or
    whose conversation the caller isn't part of, are reported in ``not_deleted_ids``
    rather than failing the whole request; if *none* could be deleted the endpoint
    returns 403. Re-deleting a message the caller already cleared counts as deleted,
    so retries stay idempotent.
    """

    status: DeleteMessageStatus
    requested_count: int
    deleted_count: int
    deleted_ids: List[str]
    not_deleted_ids: List[str]


class ConversationDeleteItem(BaseModel):
    """Single conversation reference in a delete request."""

    conversation_id: str = Field(..., description="Conversation ID to clear for the requesting user")


class DeleteConversationRequest(BaseModel):
    """Request model for clearing one or more conversations for one participant.

    Pass a single-item ``conversations`` list to clear one conversation or several
    entries to clear a batch. IDs that don't exist or that the user isn't a participant
    of are skipped and reported in the response's ``not_deleted_ids`` rather than
    failing the request.
    """

    userprofile_id: str = Field(..., description="Requesting participant's user profile ID")
    conversations: List[ConversationDeleteItem] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Conversations to clear (1-100; single-item list for one)",
    )

    @model_validator(mode="after")
    def normalize_conversations(self) -> "DeleteConversationRequest":
        """Strip blanks and de-duplicate by conversation_id (order preserved); require at least one."""
        seen: set[str] = set()
        cleaned: List[ConversationDeleteItem] = []
        for item in self.conversations:
            cid = (item.conversation_id or "").strip()
            if cid and cid not in seen:
                seen.add(cid)
                cleaned.append(ConversationDeleteItem(conversation_id=cid))
        if not cleaned:
            raise ValueError("conversations must contain at least one non-empty conversation_id")
        self.conversations = cleaned
        return self


DeleteConversationStatus = Literal["all", "partial", "none"]


class DeleteConversationResponse(BaseModel):
    """Response model for the per-user conversation delete endpoint.

    ``status`` summarises the batch outcome: ``all`` (every requested conversation
    cleared), ``partial`` (some cleared, some skipped), or ``none`` (nothing cleared).
    ``deleted_ids`` / ``not_deleted_ids`` give the per-conversation breakdown and are
    the authoritative result the client should drive its UI from.
    """

    status: DeleteConversationStatus
    requested_count: int
    deleted_count: int
    deleted_ids: List[str]
    not_deleted_ids: List[str]
