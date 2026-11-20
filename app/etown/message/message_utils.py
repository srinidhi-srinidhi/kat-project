"""Message Utilities - Postgres business logic for direct conversations, messages and receipts.

Domain layer behind /api/message: manages etown_conversations and etown_messages with AES-256-GCM encrypted text, Azure Blob media uploads, replies, edits, delivery and read receipts, per-side deletes and inbox listing, and sends a governance-checked push for each new message.

Features:
    - Conversation lookup: Detect existing 1:1 conversation between participants
    - Conversation retrieval: Load conversation metadata with cursor-based paginated messages
    - Conversation creation: Create direct conversation with optional first message
    - Read status updates: Mark unread messages as read for current receiver
    - Participant validation: Validate user/opponent type combinations against profile role
    - Media upload: Base64 attachments go to Azure Blob Storage; document formats are sniffed
    - Encryption at rest: message_text is AES-256-GCM encrypted on write, decrypted on read
    - Send message: Insert a new message into an existing conversation
    - Reply / quote: Validate and attach a reply reference; expose a quoted-message preview
    - Edit message: Update a sent message's text/caption (sender only), stamping edited_at
    - Delivery receipts: Mark messages delivered for the receiver; derive sent/delivered/read status
    - Inbox listing: Retrieve all conversations for a user with unread counts and last message preview
    - Unread count: Total unread messages across all user conversations
    - Per-side message delete: Clear messages for the requesting side only, keeping the rows
    - Per-user conversation delete: Clear one or many conversations for one participant (reappear-empty semantics)
    - Push notification: Fire-and-forget governance-checked push on new message

Classes:
    MessageDatabase(): Database operations for conversations/messages tables
    MessageService(): Validation and orchestration layer for message APIs

Functions:
    derive_message_status(): Derive 'read'/'delivered'/'sent' from a message's receipt timestamps
    _media_type_from_data_url(): Extract the declared MIME type from a data-URL prefix, if present
    _sniff_document_format(): Resolve (extension, content_type) for a document/file from its magic bytes
    _resolve_document_format(): Resolve (extension, content_type) via a data-URL hint or magic-byte sniffing
    upload_message_media(): Upload a base64-encoded message attachment to Azure Blob Storage
    _display_name_subquery(): Build the SQL subquery resolving a userprofile's display name for INSERTs

Dependencies:
    - fastapi: HTTPException and status code handling
    - app.db.client: Base database client and retry decorator
    - app.schemas.message: Messaging API request and response models
    - app.core.config: Application settings (AZURE_BLOB_CONTAINER)
    - app.utils.blob_storage: Azure Blob upload helpers for media attachments
    - app.utils.message_crypto: AES-256-GCM encryption/decryption of message_text at rest
    - app.etown.plans.plans_utils: resolve_provider_badges() for inbox badge labels
    - app.etown.notifications.core.models: NotificationRequest, Recipient, PushAudience
    - app.etown.notifications.core.registry: NotificationType key used in push governance requests
    - app.etown.notifications.service (lazy import): Governance-checked dispatch for new-message pushes
    - app.core.logging: Centralized logger

Updates:
    24-May-2026: Initial implementation of direct messaging utilities
    27-May-2026: Added message DB/service methods, FCM helper, read_at, pagination and upload_message_media().
    28-May-2026: Added structured debug/info/warning logging across service methods and upload utility
    10-Jul-2026: Added edit, reply/quote, delivery receipts and per-user conversation delete.
    27-Jul-2026: delete_messages takes a list and deletes per-side.
    28-Jul-2026: get_inbox returns opponent identifiers; added get_fcm_device_tokens().
    10-Aug-2026: Stores sender_name/receiver_name; message_text encrypted at rest.
    18-Aug-2026: Inbox rows carry the opponent's provider_type and subscription badge_label.
    19-Aug-2026: send_message captures bookings/instructions from trigger phrases in the background.
    20-Aug-2026: get_inbox() rows carry Etown_providertype, the opponent's etown_provider.providertype.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    02-Sep-2026: get_inbox() rows carry Etown_disabled for the opponent.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional, Tuple

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database as BaseDatabase, with_retry
from app.schemas.message import (
    ConversationMetadata,
    ConversationParticipantType,
    CreateConversationRequest,
    SendMessageRequest,
)
from app.utils.blob_storage import (
    detect_mime_type as _detect_mime_type,
    is_stored_media_url as _is_stored_media_url,
    normalize_container_name as _normalize_container_name,
    to_bytes as _to_bytes,
    upload_blob_bytes as _upload_blob_bytes,
    current_timestamp_prefix as _current_timestamp_prefix,
)
from app.utils.message_crypto import (
    decrypt_fields as _decrypt_fields,
    encrypt_text as _encrypt_text,
)
from app.etown.plans.plans_utils import resolve_provider_badges
from app.etown.notifications.core.models import NotificationRequest, Recipient, PushAudience
from app.etown.notifications.core.registry import NotificationType


_MESSAGE_MEDIA_CONFIG: dict[str, tuple[str, str]] = {
    "image":    ("MessageImg.png", "image/png"),
    "video":    ("MessageVideo.mp4", "video/mp4"),
    "audio":    ("MessageAudio.mp3", "audio/mpeg"),
    "file":     ("MessageFile.bin", "application/octet-stream"),
    "document": ("MessageDoc.bin", "application/octet-stream"),
}

_SNIFFED_MEDIA_TYPES = frozenset({"file", "document"})

_MIME_EXTENSIONS: dict[str, str] = {
    "application/pdf": "pdf",
    "application/zip": "zip",
    "application/rtf": "rtf",
    "application/json": "json",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "text/plain": "txt",
    "text/csv": "csv",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
}

_OOXML_MARKERS: tuple[tuple[bytes, str], ...] = (
    (b"word/", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    (b"xl/", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    (b"ppt/", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
)

_REPLY_PREVIEW_MAX_CHARS = 200


def _display_name_subquery(param: str) -> str:
    """Return a SQL subquery giving the provider businessname for role 1 or contactidname for role 2, else NULL."""
    return f"""(
                    SELECT CASE
                               WHEN up.role = 1 THEN pr.businessname
                               WHEN up.role = 2 THEN COALESCE(up.contactidname, up.name)
                               ELSE NULL
                           END
                    FROM public.etown_userprofile up
                    LEFT JOIN public.etown_provider pr
                        ON LOWER(pr.userprofileid) = LOWER(up.userprofileid)
                    WHERE LOWER(up.userprofileid) = LOWER({param})
                    LIMIT 1
                )"""


def derive_message_status(read_at: Any, delivered_at: Any) -> str:
    """Return 'read', 'delivered' or 'sent' from a message's receipt timestamps."""
    if read_at:
        return "read"
    if delivered_at:
        return "delivered"
    return "sent"


def _media_type_from_data_url(media_file: str) -> Optional[str]:
    """Return the lower-cased MIME type from a data-URL prefix, or None for bare base64."""
    head = media_file[:200].lstrip()
    if not head[:5].lower() == "data:":
        return None
    marker = head.lower().find("base64,")
    if marker == -1:
        return None
    declared = head[5:marker].split(";", 1)[0].strip().lower()
    return declared or None


def _sniff_document_format(content: bytes) -> Tuple[str, str]:
    """Return (extension, content_type) sniffed from magic bytes, defaulting to ('bin', 'application/octet-stream')."""
    if content[:4] == b"PK\x03\x04":
        head = content[:4096]
        for marker, mime in _OOXML_MARKERS:
            if marker in head:
                return _MIME_EXTENSIONS[mime], mime
        return "zip", "application/zip"
    content_type = _detect_mime_type(content)
    return _MIME_EXTENSIONS.get(content_type, "bin"), content_type


def _resolve_document_format(media_file: str, content: bytes) -> Tuple[str, str]:
    """Return (extension, content_type) from a mappable data-URL MIME type, else from the sniffed magic bytes."""
    declared = _media_type_from_data_url(media_file)
    if declared and declared in _MIME_EXTENSIONS:
        return _MIME_EXTENSIONS[declared], declared
    return _sniff_document_format(content)


async def upload_message_media(
    userprofile_id: str,
    media_file: Optional[str],
    message_type: str,
) -> Optional[str]:
    """Upload a base64 message attachment to blob storage and return its path, passing stored paths through.

    Args:
        userprofile_id: Sender's profile ID, used as the blob folder.
        media_file: Base64 or data-URL payload, or an already-stored blob path.
        message_type: Media type selecting suffix and MIME type; other types skip upload.

    Returns:
        The blob path, the unchanged stored path, or None when skipped.

    Raises:
        RuntimeError: When AZURE_BLOB_CONTAINER is not configured.
    """
    if not media_file or message_type not in _MESSAGE_MEDIA_CONFIG:
        logger.debug(
            "Message media upload skipped | user=%s type=%s has_file=%s",
            userprofile_id, message_type, bool(media_file),
        )
        return None

    if _is_stored_media_url(media_file):
        logger.debug(
            "Message media already stored — pass-through | user=%s type=%s",
            userprofile_id, message_type,
        )
        return media_file

    content = _to_bytes(media_file)
    if not content:
        logger.debug(
            "Message media upload skipped — empty bytes | user=%s type=%s",
            userprofile_id, message_type,
        )
        return None

    container_name = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
    if not container_name:
        raise RuntimeError("AZURE_BLOB_CONTAINER is not configured")

    suffix, content_type = _MESSAGE_MEDIA_CONFIG[message_type]
    if message_type in _SNIFFED_MEDIA_TYPES:
        extension, content_type = _resolve_document_format(media_file, content)
        suffix = f"{suffix.rsplit('.', 1)[0]}.{extension}"
    blob_name = f"{userprofile_id}/{_current_timestamp_prefix()}_{suffix}"
    logger.debug(
        "Uploading message media | user=%s blob=%s size_bytes=%d",
        userprofile_id, blob_name, len(content),
    )
    blob_path = await _upload_blob_bytes(container_name, blob_name, content, content_type)
    logger.info("Message media uploaded | user=%s blob=%s", userprofile_id, blob_path)
    return blob_path


class MessageDatabase(BaseDatabase):
    """Database operations for direct conversation messaging."""

    ROLE_TO_PARTICIPANT_TYPE = {
        1: "provider",
        2: "member",
    }

    @classmethod
    @with_retry()
    async def get_user_participant_type(cls, userprofile_id: str) -> Optional[ConversationParticipantType]:
        """Return participant type derived from etown_userprofile.role."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT up.role,
                   EXISTS (
                       SELECT 1 FROM public.etown_provider p
                       WHERE LOWER(p.userprofileid) = LOWER(up.userprofileid)
                   ) AS has_provider_row
            FROM public.etown_userprofile up
            WHERE lower(userprofileid) = lower($1)
            LIMIT 1
        """

        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, userprofile_id)
            if not row:
                return None
            if row["has_provider_row"]:
                return "provider"
            role_type = cls.ROLE_TO_PARTICIPANT_TYPE.get(row["role"])
            if role_type == "provider":
                return "member"
            return role_type

    @classmethod
    @with_retry()
    async def find_existing_conversation(
        cls,
        userprofile_id: str,
        user_type: ConversationParticipantType,
        opponent_id: str,
        opponent_type: ConversationParticipantType,
    ) -> Optional[dict[str, Any]]:
        """Find existing conversation irrespective of participant order."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT *
            FROM public.etown_conversations
            WHERE (
                participant_1_id = $1
                AND participant_1_type = $2
                AND participant_2_id = $3
                AND participant_2_type = $4
            ) OR (
                participant_1_id = $3
                AND participant_1_type = $4
                AND participant_2_id = $1
                AND participant_2_type = $2
            )
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 1
        """

        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, userprofile_id, user_type, opponent_id, opponent_type)
            return dict(row) if row else None

    @classmethod
    @with_retry()
    async def get_conversation_by_id(cls, conversation_id: str) -> Optional[dict[str, Any]]:
        """Return the etown_conversations row for an id as a dict, or None."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = "SELECT * FROM public.etown_conversations WHERE id = $1 LIMIT 1"
        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, conversation_id)
            return dict(row) if row else None

    @classmethod
    @with_retry()
    async def get_conversation_messages(
        cls,
        conversation_id: str,
        userprofile_id: str,
        limit: int = 101,
        offset: int = 0,
        since: Any = None,
    ) -> list[dict[str, Any]]:
        """Return a page of a conversation's messages newest first, hiding rows deleted for this caller.

        Args:
            conversation_id: UUID of the conversation.
            userprofile_id: Requesting participant, deciding which per-side deletions hide rows.
            limit: Maximum messages to return.
            offset: Rows to skip for pagination.
            since: Optional cutoff; only messages created after it are returned.

        Returns:
            Message dicts with reply_* prefixed quote fields.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                m.id, m.conversation_id, m.sender_id, m.receiver_id, m.is_read, m.read_at,
                m.delivered_at, m.edited_at, m.created_at, m.updated_at, m.message_type,
                m.sender_type, m.file_url, m.receiver_type, m.message_text, m.reply_to_message_id,
                COALESCE(
                    NULLIF(m.sender_name, ''),
                    CASE WHEN m.sender_type = 'member' THEN up.name
                         ELSE COALESCE(pr.businessname, up.name) END
                ) AS sender_name,
                m.receiver_name,

                etown_deleted_flag_ci(m.sender_id)         AS sender_deleted,
                etown_deactivated_flag_ci(m.sender_id)     AS sender_deactivated,
                etown_deleted_flag_ci(m.receiver_id)       AS receiver_deleted,
                etown_deactivated_flag_ci(m.receiver_id)   AS receiver_deactivated,
                rm.id AS reply_id,
                rm.sender_id AS reply_sender_id,
                rm.sender_type AS reply_sender_type,
                rm.message_type AS reply_message_type,
                rm.message_text AS reply_message_text,
                rm.file_url AS reply_file_url,
                (
                    rm.is_deleted
                    OR (LOWER(rm.sender_id) = LOWER($5) AND rm.sender_deleted_at IS NOT NULL)
                    OR (LOWER(rm.receiver_id) = LOWER($5) AND rm.receiver_deleted_at IS NOT NULL)
                ) AS reply_is_deleted
            FROM public.etown_messages m
            LEFT JOIN public.etown_userprofile up ON LOWER(up.userprofileid) = LOWER(m.sender_id)
            LEFT JOIN public.etown_provider pr ON LOWER(pr.providerid) = LOWER(m.sender_id)
            LEFT JOIN public.etown_messages rm ON rm.id = m.reply_to_message_id
            WHERE m.conversation_id = $1
              AND m.is_deleted = FALSE
              AND NOT (LOWER(m.sender_id) = LOWER($5) AND m.sender_deleted_at IS NOT NULL)
              AND NOT (LOWER(m.receiver_id) = LOWER($5) AND m.receiver_deleted_at IS NOT NULL)
              AND ($4::timestamp IS NULL OR m.created_at > $4)
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT $2 OFFSET $3
"""
        async with cls._pool.acquire() as con:
            rows = await con.fetch(query, conversation_id, limit, offset, since, userprofile_id)
        return [
            _decrypt_fields(dict(row), "message_text", "reply_message_text")
            for row in rows
        ]

    @classmethod
    @with_retry()
    async def create_conversation_with_optional_message(
        cls,
        userprofile_id: str,
        user_type: ConversationParticipantType,
        opponent_id: str,
        opponent_type: ConversationParticipantType,
        initial_message: Optional[str],
        message_type: str,
        file_url: Optional[str],
    ) -> Tuple[dict[str, Any], Optional[str], bool]:
        """Create conversation and optional initial message in a single transaction."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        existing = await cls.find_existing_conversation(
            userprofile_id=userprofile_id,
            user_type=user_type,
            opponent_id=opponent_id,
            opponent_type=opponent_type,
        )
        if existing:
            return existing, None, False

        participant_1_id = userprofile_id
        participant_1_type = user_type
        participant_2_id = opponent_id
        participant_2_type = opponent_type

        insert_conversation_query = """
            INSERT INTO public.etown_conversations (
                participant_1_id,
                participant_2_id,
                participant_1_type,
                participant_2_type,
                created_at,
                updated_at,
                last_activity
            )
            VALUES ($1, $2, $3, $4, NOW(), NOW(), NOW())
            RETURNING *
        """

        insert_message_query = f"""
            INSERT INTO public.etown_messages (
                conversation_id,
                sender_id,
                receiver_id,
                is_read,
                created_at,
                updated_at,
                message_type,
                sender_type,
                file_url,
                receiver_type,
                message_text,
                sender_name,
                receiver_name
            )
            VALUES (
                $1, $2, $3, FALSE, NOW(), NOW(), $4, $5, $6, $7, $8,
                {_display_name_subquery("$2")},
                {_display_name_subquery("$3")}
            )
            RETURNING id
        """

        update_last_message_query = """
            UPDATE public.etown_conversations
            SET last_message_id = $2,
                last_activity = NOW(),
                updated_at = NOW(),
                modifieddate = NOW()
            WHERE id = $1
            RETURNING *
        """

        async with cls._pool.acquire() as con:
            async with con.transaction():
                conversation_row = await con.fetchrow(
                    insert_conversation_query,
                    participant_1_id,
                    participant_2_id,
                    participant_1_type,
                    participant_2_type,
                )
                conversation = dict(conversation_row)

                created_message_id: Optional[str] = None
                if initial_message:
                    message_row = await con.fetchrow(
                        insert_message_query,
                        str(conversation["id"]),
                        userprofile_id,
                        opponent_id,
                        message_type,
                        user_type,
                        file_url,
                        opponent_type,
                        _encrypt_text(initial_message),
                    )
                    raw_message_id = message_row["id"] if message_row else None
                    created_message_id = str(raw_message_id) if raw_message_id is not None else None
                    if raw_message_id is not None:
                        updated_conv_row = await con.fetchrow(
                            update_last_message_query,
                            str(conversation["id"]),
                            raw_message_id,
                        )
                        conversation = dict(updated_conv_row)

        return conversation, created_message_id, True

    @classmethod
    @with_retry()
    async def mark_conversation_read(cls, conversation_id: str, receiver_id: str) -> int:
        """Mark the receiver's unread messages read, back-filling delivered_at where missing."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            UPDATE public.etown_messages
            SET is_read = TRUE,
                read_at = NOW(),
                delivered_at = COALESCE(delivered_at, NOW()),
                updated_at = NOW()
            WHERE conversation_id = $1
              AND receiver_id = $2
              AND is_read = FALSE
        """

        async with cls._pool.acquire() as con:
            result = await con.execute(query, conversation_id, receiver_id)
            return int(result.split()[-1])

    @classmethod
    @with_retry()
    async def mark_conversation_delivered(cls, conversation_id: str, receiver_id: str) -> int:
        """Mark the receiver's undelivered messages delivered and return how many changed."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            UPDATE public.etown_messages
            SET delivered_at = NOW(),
                updated_at = NOW()
            WHERE conversation_id = $1
              AND receiver_id = $2
              AND delivered_at IS NULL
        """

        async with cls._pool.acquire() as con:
            result = await con.execute(query, conversation_id, receiver_id)
            return int(result.split()[-1])

    @classmethod
    @with_retry()
    async def insert_message(
        cls,
        conversation_id: str,
        sender_id: str,
        receiver_id: str,
        sender_type: ConversationParticipantType,
        receiver_type: ConversationParticipantType,
        message_text: Optional[str],
        message_type: str,
        file_url: Optional[str],
        reply_to_message_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Insert a new message and update the conversation's last_message_id/last_activity.

        Args:
            conversation_id: UUID of the target conversation.
            sender_id: Sender's userprofile_id.
            receiver_id: Receiver's userprofile_id.
            sender_type: Participant type of the sender.
            receiver_type: Participant type of the receiver.
            message_text: Text content of the message.
            message_type: Message type (text, image, file, audio, video).
            file_url: Optional URL for media messages.
            reply_to_message_id: Optional ID of the message this one replies to.

        Returns:
            dict: Inserted message row as a dictionary.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        insert_query = f"""
            INSERT INTO public.etown_messages (
                conversation_id, sender_id, receiver_id, is_read,
                created_at, updated_at, message_type, sender_type,
                file_url, receiver_type, message_text, reply_to_message_id,
                sender_name, receiver_name
            )
            VALUES (
                $1, $2, $3, FALSE, NOW(), NOW(), $4, $5, $6, $7, $8, $9,
                {_display_name_subquery("$2")},
                {_display_name_subquery("$3")}
            )
            RETURNING
                id, conversation_id, sender_id, receiver_id, is_read, read_at,
                delivered_at, edited_at, created_at, updated_at, message_type, sender_type,
                file_url, receiver_type, message_text, reply_to_message_id,
                sender_name,
                receiver_name,
                etown_deleted_flag_ci(sender_id)         AS sender_deleted,
                etown_deactivated_flag_ci(sender_id)     AS sender_deactivated,
                etown_deleted_flag_ci(receiver_id)       AS receiver_deleted,
                etown_deactivated_flag_ci(receiver_id)   AS receiver_deactivated
        """

        update_conv_query = """
            UPDATE public.etown_conversations
            SET last_message_id = $2,
                last_activity = NOW(),
                updated_at = NOW(),
                modifieddate = NOW()
            WHERE id = $1
        """

        async with cls._pool.acquire() as con:
            async with con.transaction():
                msg_row = await con.fetchrow(
                    insert_query,
                    conversation_id,
                    sender_id,
                    receiver_id,
                    message_type,
                    sender_type,
                    file_url,
                    receiver_type,
                    _encrypt_text(message_text),
                    reply_to_message_id,
                )
                await con.execute(update_conv_query, conversation_id, msg_row["id"])
        return _decrypt_fields(dict(msg_row), "message_text")

    @classmethod
    @with_retry()
    async def get_message_by_id(cls, message_id: str) -> Optional[dict[str, Any]]:
        """Return a message row by id, including deleted rows, or None."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                id, conversation_id, sender_id, receiver_id, is_read, read_at,
                delivered_at, edited_at, created_at, updated_at, message_type,
                sender_type, file_url, receiver_type, message_text, reply_to_message_id,
                sender_name,
                receiver_name,
                etown_deleted_flag_ci(sender_id)         AS sender_deleted,
                etown_deactivated_flag_ci(sender_id)     AS sender_deactivated,
                etown_deleted_flag_ci(receiver_id)       AS receiver_deleted,
                etown_deactivated_flag_ci(receiver_id)   AS receiver_deactivated,
                is_deleted, sender_deleted_at, receiver_deleted_at
            FROM public.etown_messages
            WHERE id = $1
            LIMIT 1
        """
        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, message_id)
            return _decrypt_fields(dict(row), "message_text") if row else None

    @classmethod
    @with_retry()
    async def update_message_text(
        cls,
        message_id: str,
        sender_id: str,
        message_text: str,
    ) -> Optional[dict[str, Any]]:
        """Update a message's text for its sender unless deleted, returning the updated fields or None."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            UPDATE public.etown_messages
            SET message_text = $3,
                edited_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
              AND LOWER(sender_id) = LOWER($2)
              AND is_deleted = FALSE
              AND sender_deleted_at IS NULL
            RETURNING id, message_text, edited_at, updated_at
        """
        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, message_id, sender_id, _encrypt_text(message_text))
            return _decrypt_fields(dict(row), "message_text") if row else None

    @classmethod
    @with_retry()
    async def delete_conversations_for_user(
        cls, conversation_ids: list[str], userprofile_id: str
    ) -> list[str]:
        """Stamp the caller's participant deleted_at on their conversations and return the cleared ids."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            UPDATE public.etown_conversations
            SET participant_1_deleted_at = CASE
                    WHEN LOWER(participant_1_id) = LOWER($2) THEN NOW()
                    ELSE participant_1_deleted_at END,
                participant_2_deleted_at = CASE
                    WHEN LOWER(participant_2_id) = LOWER($2) THEN NOW()
                    ELSE participant_2_deleted_at END,
                modifieddate = NOW()
            WHERE id = ANY($1::uuid[])
              AND (LOWER(participant_1_id) = LOWER($2) OR LOWER(participant_2_id) = LOWER($2))
            RETURNING id
        """
        async with cls._pool.acquire() as con:
            rows = await con.fetch(query, conversation_ids, userprofile_id)
            return [str(row["id"]) for row in rows]

    @classmethod
    @with_retry()
    async def get_sender_profile(cls, userprofile_id: str) -> tuple[str, str]:
        """Return (display_name, profile_image) by role from profile, contact or provider, with empty-string fallbacks."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                CASE
                    WHEN up.role = 2 THEN COALESCE(up.name, up.contactidname, '')
                    ELSE COALESCE(pr.businessname, up.name, up.contactidname, '')
                END AS display_name,
                CASE
                    WHEN up.role = 2 THEN COALESCE(oc.profileimage, '')
                    ELSE COALESCE(pr.serviceimageurl, '')
                END AS profile_image
            FROM public.etown_userprofile up
            LEFT JOIN public.etown_contact oc ON LOWER(oc.contactid) = LOWER(up.contactid)
            LEFT JOIN public.etown_provider pr ON LOWER(pr.userprofileid) = LOWER(up.userprofileid)
            WHERE LOWER(up.userprofileid) = LOWER($1)
            LIMIT 1
        """
        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, userprofile_id)
            if row:
                return (row["display_name"] or "", row["profile_image"] or "")
            return ("", "")

    @classmethod
    @with_retry()
    async def get_inbox(
        cls,
        userprofile_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a page of the user's conversations with unread count, last message preview and opponent ids."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                c.id,
                c.participant_1_id,
                c.participant_1_type,
                c.participant_2_id,
                c.participant_2_type,
                c.last_message_id,
                c.last_activity,
                c.created_at,
                c.updated_at,
                COALESCE(unread.cnt, 0) AS unread_count,
                lm.message_text AS last_message_text,
                lm.message_type AS last_message_type,
                CASE
                    WHEN LOWER(c.participant_1_id) = LOWER($1) THEN
                        CASE WHEN c.participant_2_type = 'member' THEN up2.name
                             ELSE COALESCE(pr2.businessname, up2.name) END
                    ELSE
                        CASE WHEN c.participant_1_type = 'member' THEN up1.name
                             ELSE COALESCE(pr1.businessname, up1.name) END
                END AS sender_name,

                CASE WHEN EXISTS (
                    SELECT 1 FROM public.etown_retired_identity r
                     WHERE (lower(r.identity_id) = lower(opp.upid)
                         OR lower(r.identity_id) = lower(opp.cid))
                       AND r.deleted = 'true'
                ) THEN 'true' ELSE 'false' END AS sender_deleted,
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.etown_retired_identity r
                     WHERE (lower(r.identity_id) = lower(opp.upid)
                         OR lower(r.identity_id) = lower(opp.cid))
                       AND r.deactivated = 'true'
                       AND r.reactivation IS DISTINCT FROM 'true'
                ) THEN 'true' ELSE 'false' END AS sender_deactivated,

                CASE WHEN EXISTS (
                    SELECT 1 FROM public.etown_retired_identity r
                     WHERE (lower(r.identity_id) = lower(opp.upid)
                         OR lower(r.identity_id) = lower(opp.cid))
                       AND r.deleted = 'true'
                ) THEN 'true' ELSE 'false' END AS opponent_deleted,
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.etown_retired_identity r
                     WHERE (lower(r.identity_id) = lower(opp.upid)
                         OR lower(r.identity_id) = lower(opp.cid))
                       AND r.deactivated = 'true'
                       AND r.reactivation IS DISTINCT FROM 'true'
                ) THEN 'true' ELSE 'false' END AS opponent_deactivated,
                CASE
                    WHEN LOWER(c.participant_1_id) = LOWER($1) THEN
                        CASE WHEN c.participant_2_type = 'member' THEN oc2.profileimage
                             ELSE pr2.serviceimageurl END
                    ELSE
                        CASE WHEN c.participant_1_type = 'member' THEN oc1.profileimage
                             ELSE pr1.serviceimageurl END
                END AS sender_image,
                CASE
                    WHEN LOWER(c.participant_1_id) = LOWER($1) THEN pr2.providertype
                    ELSE pr1.providertype
                END AS "Etown_providertype",
                CASE
                    WHEN LOWER(c.participant_1_id) = LOWER($1) THEN pr2.disabled
                    ELSE pr1.disabled
                END AS "Etown_disabled",
                opp.upid AS opponent_userprofile_id,
                opp.cid  AS opponent_contact_id
            FROM public.etown_conversations c
            LEFT JOIN (
                SELECT m.conversation_id, COUNT(*) AS cnt
                FROM public.etown_messages m
                JOIN public.etown_conversations cc ON cc.id = m.conversation_id
                WHERE m.receiver_id = $1
                  AND m.is_read = FALSE
                  AND m.is_deleted = FALSE
                  AND m.receiver_deleted_at IS NULL

                  AND NOT etown_is_deleted_ci(m.sender_id)
                  AND m.created_at > COALESCE(
                        CASE WHEN LOWER(cc.participant_1_id) = LOWER($1)
                             THEN cc.participant_1_deleted_at
                             ELSE cc.participant_2_deleted_at END,
                        '-infinity'::timestamp)
                GROUP BY m.conversation_id
            ) unread ON unread.conversation_id = c.id
            LEFT JOIN public.etown_messages lm
                ON lm.id = c.last_message_id
               AND lm.is_deleted = FALSE
               AND NOT (LOWER(lm.sender_id) = LOWER($1) AND lm.sender_deleted_at IS NOT NULL)
               AND NOT (LOWER(lm.receiver_id) = LOWER($1) AND lm.receiver_deleted_at IS NOT NULL)
            LEFT JOIN public.etown_userprofile up1 ON LOWER(up1.userprofileid) = LOWER(c.participant_1_id)
            LEFT JOIN public.etown_userprofile up2 ON LOWER(up2.userprofileid) = LOWER(c.participant_2_id)
            LEFT JOIN public.etown_contact oc1 ON LOWER(oc1.contactid) = LOWER(up1.contactid)
            LEFT JOIN public.etown_contact oc2 ON LOWER(oc2.contactid) = LOWER(up2.contactid)
            LEFT JOIN public.etown_provider pr1 ON LOWER(pr1.userprofileid) = LOWER(c.participant_1_id)
            LEFT JOIN public.etown_provider pr2 ON LOWER(pr2.userprofileid) = LOWER(c.participant_2_id)

            CROSS JOIN LATERAL (
                SELECT
                    CASE WHEN LOWER(c.participant_1_id) = LOWER($1)
                         THEN c.participant_2_id ELSE c.participant_1_id END AS upid,
                    CASE WHEN LOWER(c.participant_1_id) = LOWER($1)
                         THEN up2.contactid ELSE up1.contactid END AS cid
            ) opp
            WHERE (
                LOWER(c.participant_1_id) = LOWER($1)
                OR LOWER(c.participant_2_id) = LOWER($1)
            )

            AND NOT etown_is_deleted_ci(opp.upid)
            AND NOT etown_is_deleted_ci(opp.cid)
            AND c.last_activity > COALESCE(
                CASE WHEN LOWER(c.participant_1_id) = LOWER($1)
                     THEN c.participant_1_deleted_at
                     ELSE c.participant_2_deleted_at END,
                '-infinity'::timestamp)
            ORDER BY c.last_activity DESC NULLS LAST, c.created_at DESC
            LIMIT $2 OFFSET $3
"""
        async with cls._pool.acquire() as con:
            rows = await con.fetch(query, userprofile_id, limit, offset)
            return [
                _decrypt_fields(dict(row), "last_message_text") for row in rows
            ]

    @classmethod
    @with_retry()
    async def get_unread_count(cls, userprofile_id: str) -> int:
        """Return the user's unread received messages across conversations, excluding ones deleted or cleared on their side."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT COUNT(*) AS cnt
            FROM public.etown_messages m
            JOIN public.etown_conversations c ON c.id = m.conversation_id
            WHERE m.receiver_id = $1
              AND m.is_read = FALSE
              AND m.is_deleted = FALSE
              AND m.receiver_deleted_at IS NULL

              AND NOT etown_is_deleted_ci(m.sender_id)
              AND m.created_at > COALESCE(
                    CASE WHEN LOWER(c.participant_1_id) = LOWER($1)
                         THEN c.participant_1_deleted_at
                         ELSE c.participant_2_deleted_at END,
                    '-infinity'::timestamp)
"""
        async with cls._pool.acquire() as con:
            row = await con.fetchrow(query, userprofile_id)
            return int(row["cnt"]) if row else 0

    @classmethod
    @with_retry()
    async def delete_messages_for_user(
        cls, message_ids: list[str], userprofile_id: str
    ) -> list[str]:
        """Stamp the caller's sender or receiver deleted_at on the messages and return the ids now hidden for them."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            UPDATE public.etown_messages
            SET sender_deleted_at = CASE
                    WHEN LOWER(sender_id) = LOWER($2)
                        THEN COALESCE(sender_deleted_at, NOW())
                    ELSE sender_deleted_at END,
                receiver_deleted_at = CASE
                    WHEN LOWER(receiver_id) = LOWER($2)
                        THEN COALESCE(receiver_deleted_at, NOW())
                    ELSE receiver_deleted_at END,
                updated_at = NOW()
            WHERE id = ANY($1::uuid[])
              AND (LOWER(sender_id) = LOWER($2) OR LOWER(receiver_id) = LOWER($2))
            RETURNING id
        """
        async with cls._pool.acquire() as con:
            rows = await con.fetch(query, message_ids, userprofile_id)
            return [str(row["id"]) for row in rows]


class MessageService:
    """Business rules and orchestration for direct message endpoints."""

    @staticmethod
    def _normalize_conversation_record(conversation: dict[str, Any]) -> dict[str, Any]:
        """Convert UUID fields from asyncpg records to strings for schema validation."""
        normalized = dict(conversation)
        if normalized.get("id") is not None:
            normalized["id"] = str(normalized["id"])
        if normalized.get("last_message_id") is not None:
            normalized["last_message_id"] = str(normalized["last_message_id"])
        return normalized

    @staticmethod
    def _build_reply_preview(normalized: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Pop a row's reply_* columns into a ReplyPreview dict, blanking deleted quotes, or return None."""
        reply_id = normalized.pop("reply_id", None)
        raw_text = normalized.pop("reply_message_text", None)
        raw_file = normalized.pop("reply_file_url", None)
        raw_sender = normalized.pop("reply_sender_id", None)
        raw_sender_type = normalized.pop("reply_sender_type", None)
        raw_msg_type = normalized.pop("reply_message_type", None)
        is_deleted = bool(normalized.pop("reply_is_deleted", False))
        if reply_id is None:
            return None
        preview_text = None if is_deleted else raw_text
        if preview_text and len(preview_text) > _REPLY_PREVIEW_MAX_CHARS:
            preview_text = preview_text[:_REPLY_PREVIEW_MAX_CHARS]
        return {
            "id": str(reply_id),
            "sender_id": raw_sender,
            "sender_type": raw_sender_type,
            "message_type": raw_msg_type,
            "message_text": preview_text,
            "file_url": None if is_deleted else raw_file,
            "is_deleted": is_deleted,
        }

    @staticmethod
    def _reply_preview_from_message(replied: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """Build a ReplyPreview dict from the replied-to message row, or return None when absent."""
        if not replied:
            return None
        text = replied.get("message_text")
        if text and len(text) > _REPLY_PREVIEW_MAX_CHARS:
            text = text[:_REPLY_PREVIEW_MAX_CHARS]
        return {
            "id": str(replied["id"]),
            "sender_id": replied.get("sender_id"),
            "sender_type": replied.get("sender_type"),
            "message_type": replied.get("message_type"),
            "message_text": text,
            "file_url": replied.get("file_url"),
            "is_deleted": False,
        }

    @staticmethod
    def _normalize_message_records(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert UUID message fields to strings and fold in reply preview + status."""
        normalized_messages: list[dict[str, Any]] = []
        for message in messages:
            normalized = dict(message)
            if normalized.get("id") is not None:
                normalized["id"] = str(normalized["id"])
            if normalized.get("conversation_id") is not None:
                normalized["conversation_id"] = str(normalized["conversation_id"])
            if normalized.get("reply_to_message_id") is not None:
                normalized["reply_to_message_id"] = str(normalized["reply_to_message_id"])
            normalized["reply_to"] = MessageService._build_reply_preview(normalized)
            normalized["status"] = derive_message_status(
                normalized.get("read_at"), normalized.get("delivered_at")
            )
            normalized_messages.append(normalized)
        return normalized_messages

    @staticmethod
    def _is_deleted_for_user(message: dict[str, Any], userprofile_id: str) -> bool:
        """Return True when the user's own sender or receiver side of the message is deleted."""
        current = userprofile_id.strip().lower()
        if str(message.get("sender_id", "")).strip().lower() == current:
            return message.get("sender_deleted_at") is not None
        if str(message.get("receiver_id", "")).strip().lower() == current:
            return message.get("receiver_deleted_at") is not None
        return False

    @staticmethod
    def _validate_not_self(userprofile_id: str, opponent_id: str) -> None:
        """Raise HTTP 400 when the opponent id equals the caller's userprofile id."""
        if userprofile_id.strip().lower() == opponent_id.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="opponent_id must be different from userprofile_id",
            )

    @staticmethod
    async def _resolve_user_type_or_404(userprofile_id: str) -> ConversationParticipantType:
        """Return the profile's participant type, raising HTTP 404 when the profile is unknown."""
        user_type = await MessageDatabase.get_user_participant_type(userprofile_id)
        if user_type is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User profile not found: {userprofile_id}",
            )
        return user_type

    @staticmethod
    def _assert_user_type_matches_request(
        resolved_type: ConversationParticipantType,
        expected_type: ConversationParticipantType,
        opponent_id: str,
    ) -> None:
        """Raise HTTP 400 when the opponent's resolved type differs from the requested type."""
        if resolved_type != expected_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"opponent_type mismatch for opponent_id={opponent_id}: "
                    f"expected '{expected_type}' but actual role is '{resolved_type}'"
                ),
            )

    @staticmethod
    def _assert_conversation_membership(
        conversation: dict[str, Any],
        userprofile_id: str,
    ) -> None:
        """Raise HTTP 403 when the user is not one of the conversation's two participants."""
        participants = {
            str(conversation["participant_1_id"]).strip().lower(),
            str(conversation["participant_2_id"]).strip().lower(),
        }
        if userprofile_id.strip().lower() not in participants:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authenticated user is not a participant of this conversation",
            )

    @staticmethod
    def _assert_opponent_matches_conversation(
        conversation: dict[str, Any],
        userprofile_id: str,
        opponent_id: str,
        opponent_type: ConversationParticipantType,
    ) -> None:
        """Raise HTTP 403 unless the conversation pairs the user with this opponent id and type."""
        p1_id =str(conversation["participant_1_id"]).strip().lower()
        p2_id = str(conversation["participant_2_id"]).strip().lower()
        p1_type = str(conversation["participant_1_type"]).strip().lower()
        p2_type = str(conversation["participant_2_type"]).strip().lower()
        current_id = userprofile_id.strip().lower()
        expected_opp_id = opponent_id.strip().lower()

        valid = (
            p1_id == current_id and p2_id == expected_opp_id and p2_type == opponent_type
        ) or (
            p2_id == current_id and p1_id == expected_opp_id and p1_type == opponent_type
        )
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Conversation does not match opponent_id/opponent_type for this user",
            )

    @staticmethod
    async def check_conversation_exists(
        userprofile_id: str,
        opponent_id: str,
        opponent_type: ConversationParticipantType,
    ) -> Optional[str]:
        """Return conversation ID if one exists, else None."""
        MessageService._validate_not_self(userprofile_id, opponent_id)
        user_type = await MessageService._resolve_user_type_or_404(userprofile_id)
        opponent_actual_type = await MessageService._resolve_user_type_or_404(opponent_id)
        MessageService._assert_user_type_matches_request(opponent_actual_type, opponent_type, opponent_id)

        conversation = await MessageDatabase.find_existing_conversation(
            userprofile_id=userprofile_id,
            user_type=user_type,
            opponent_id=opponent_id,
            opponent_type=opponent_type,
        )
        return str(conversation["id"]) if conversation else None

    @staticmethod
    async def get_conversation_details(
        conversation_id: str,
        userprofile_id: str,
        opponent_id: str,
        opponent_type: ConversationParticipantType,
        page: int = 1,
    ) -> dict[str, Any]:
        """Return conversation metadata and a 100-message page, newest first, after participant and opponent checks.

        Args:
            conversation_id: UUID of the conversation.
            userprofile_id: Current user's profile ID; must be a participant.
            opponent_id: The other participant's profile ID.
            opponent_type: Expected participant type of the opponent.
            page: One-based page number.

        Returns:
            Dict of conversation metadata, messages and has_more flag.
        """
        PAGE_SIZE = 100
        MessageService._validate_not_self(userprofile_id, opponent_id)

        conversation = await MessageDatabase.get_conversation_by_id(conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation not found: {conversation_id}",
            )

        MessageService._assert_conversation_membership(conversation, userprofile_id)
        MessageService._assert_opponent_matches_conversation(
            conversation,
            userprofile_id,
            opponent_id,
            opponent_type,
        )

        current_id = userprofile_id.strip().lower()
        if str(conversation["participant_1_id"]).strip().lower() == current_id:
            since = conversation.get("participant_1_deleted_at")
        else:
            since = conversation.get("participant_2_deleted_at")

        offset = (page - 1) * PAGE_SIZE
        messages = await MessageDatabase.get_conversation_messages(
            conversation_id,
            userprofile_id=userprofile_id,
            limit=PAGE_SIZE + 1,
            offset=offset,
            since=since,
        )
        has_more = len(messages) > PAGE_SIZE
        if has_more:
            messages = messages[:PAGE_SIZE]

        logger.debug(
            "Conversation details fetched | conv=%s user=%s page=%d messages=%d has_more=%s",
            conversation_id, userprofile_id, page, len(messages), has_more,
        )

        conversation = MessageService._normalize_conversation_record(conversation)
        messages = MessageService._normalize_message_records(messages)
        return {
            "conversation": ConversationMetadata(**conversation),
            "messages": messages,
            "has_more": has_more,
        }

    @staticmethod
    async def create_conversation(payload: CreateConversationRequest) -> dict[str, Any]:
        """Create a new conversation or return an existing one for the same pair."""
        MessageService._validate_not_self(payload.userprofile_id, payload.opponent_id)

        user_type = await MessageService._resolve_user_type_or_404(payload.userprofile_id)
        opponent_actual_type = await MessageService._resolve_user_type_or_404(payload.opponent_id)
        MessageService._assert_user_type_matches_request(
            opponent_actual_type,
            payload.opponent_type,
            payload.opponent_id,
        )

        file_url = await upload_message_media(
            payload.userprofile_id, payload.media_file, payload.message_type
        )

        conversation, initial_message_id, created = await MessageDatabase.create_conversation_with_optional_message(
            userprofile_id=payload.userprofile_id,
            user_type=user_type,
            opponent_id=payload.opponent_id,
            opponent_type=payload.opponent_type,
            initial_message=payload.initial_message,
            message_type=payload.message_type,
            file_url=file_url,
        )
        conversation = MessageService._normalize_conversation_record(conversation)

        if created:
            logger.info(
                "Conversation created | conv=%s user=%s opponent=%s msg_id=%s",
                conversation["id"], payload.userprofile_id, payload.opponent_id, initial_message_id,
            )
        else:
            logger.debug(
                "Conversation already exists | conv=%s user=%s opponent=%s",
                conversation["id"], payload.userprofile_id, payload.opponent_id,
            )

        return {
            "conversation": ConversationMetadata(**conversation),
            "initial_message_id": initial_message_id,
            "created": created,
        }

    @staticmethod
    async def mark_conversation_as_read(
        conversation_id: str,
        userprofile_id: str,
        opponent_id: str,
        opponent_type: ConversationParticipantType,
    ) -> int:
        """Mark unread messages as read after membership and opponent checks."""
        MessageService._validate_not_self(userprofile_id, opponent_id)

        conversation = await MessageDatabase.get_conversation_by_id(conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation not found: {conversation_id}",
            )

        MessageService._assert_conversation_membership(conversation, userprofile_id)
        MessageService._assert_opponent_matches_conversation(
            conversation,
            userprofile_id,
            opponent_id,
            opponent_type,
        )

        updated_count = await MessageDatabase.mark_conversation_read(
            conversation_id=conversation_id,
            receiver_id=userprofile_id,
        )
        logger.debug(
            "Conversation marked read | conv=%s user=%s updated=%d",
            conversation_id, userprofile_id, updated_count,
        )
        return updated_count


    @staticmethod
    async def send_message(payload: SendMessageRequest) -> dict[str, Any]:
        """Validate, upload media for and insert a message into an existing conversation, then notify the receiver.

        Returns:
            Inserted MessageItem data for the API response.

        Raises:
            HTTPException: 404 for an unknown conversation, 403 for a non-participant, 400 for bad content.
        """
        conversation = await MessageDatabase.get_conversation_by_id(payload.conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation not found: {payload.conversation_id}",
            )

        MessageService._assert_conversation_membership(conversation, payload.userprofile_id)

        p1_id = str(conversation["participant_1_id"]).strip().lower()
        sender_lower = payload.userprofile_id.strip().lower()
        if p1_id == sender_lower:
            sender_type = str(conversation["participant_1_type"])
            receiver_id = str(conversation["participant_2_id"])
            receiver_type = str(conversation["participant_2_type"])
        else:
            sender_type = str(conversation["participant_2_type"])
            receiver_id = str(conversation["participant_1_id"])
            receiver_type = str(conversation["participant_1_type"])

        replied_msg: Optional[dict[str, Any]] = None
        if payload.reply_to_message_id:
            replied_msg = await MessageDatabase.get_message_by_id(payload.reply_to_message_id)
            conv_id_lower = str(conversation["id"]).strip().lower()
            if (
                not replied_msg
                or replied_msg.get("is_deleted")
                or MessageService._is_deleted_for_user(replied_msg, payload.userprofile_id)
                or str(replied_msg.get("conversation_id")).strip().lower() != conv_id_lower
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="reply_to_message_id must reference an existing, non-deleted message in this conversation",
                )

        file_url = await upload_message_media(
            payload.userprofile_id, payload.media_file, payload.message_type
        )

        msg_row = await MessageDatabase.insert_message(
            conversation_id=str(conversation["id"]),
            sender_id=payload.userprofile_id,
            receiver_id=receiver_id,
            sender_type=sender_type,
            receiver_type=receiver_type,
            message_text=payload.message_text,
            message_type=payload.message_type,
            file_url=file_url,
            reply_to_message_id=payload.reply_to_message_id,
        )

        msg_row["id"] = str(msg_row["id"])
        msg_row["conversation_id"] = str(msg_row["conversation_id"])
        if msg_row.get("reply_to_message_id") is not None:
            msg_row["reply_to_message_id"] = str(msg_row["reply_to_message_id"])
        msg_row["reply_to"] = MessageService._reply_preview_from_message(replied_msg)
        msg_row["status"] = derive_message_status(
            msg_row.get("read_at"), msg_row.get("delivered_at")
        )

        logger.info(
            "Message sent | msg=%s conv=%s sender=%s type=%s has_media=%s",
            msg_row["id"], msg_row["conversation_id"],
            payload.userprofile_id, payload.message_type, bool(file_url),
        )

        if payload.oauth_token:
            asyncio.create_task(
                MessageService._send_message_notification_safe(
                    oauth_token=payload.oauth_token,
                    receiver_id=receiver_id,
                    sender_id=payload.userprofile_id,
                    conversation_id=str(conversation["id"]),
                    preview=(payload.message_text or "")[:100] or "Sent an attachment",
                    rolename=sender_type,
                    message_id=msg_row["id"],
                )
            )
        else:
            logger.debug("FCM push skipped — no oauth_token | conv=%s", str(conversation["id"]))

        return {"message": msg_row}

    @staticmethod
    async def _send_message_notification_safe(
        oauth_token: str,
        receiver_id: str,
        sender_id: str,
        conversation_id: str,
        preview: str,
        rolename: str = "",
        message_id: Optional[str] = None,
    ) -> None:
        """Send the receiver a direct-message push keyed on the message id, logging instead of raising."""
        try:
            sender_name, sender_image = await MessageDatabase.get_sender_profile(sender_id)
            from app.etown.notifications.service import GovernanceRequest, send as gov_send
            await gov_send(GovernanceRequest(
                userprofileid=receiver_id,
                notification_type=NotificationType.DIRECT_MESSAGE,
                ctx={
                    "sender_name": sender_name or "New Message",
                    "preview": preview,
                    "conversation_id": conversation_id,
                    "sender_id": sender_id,
                    "recipient_id": receiver_id,
                    "sender_image": sender_image,
                    "rolename": rolename,
                },
                oauth_token=oauth_token,
                feature="direct_message",
                dedupe_key=(f"direct-message:{message_id}" if message_id else None),
            ))
        except Exception as exc:
            logger.warning("Background DM notification failed: %s", exc)

    @staticmethod
    async def get_inbox(
        userprofile_id: str,
        page: int = 1,
    ) -> dict[str, Any]:
        """Return a 100-conversation inbox page with unread counts, last message previews and opponent ids."""
        PAGE_SIZE = 100
        offset = (page - 1) * PAGE_SIZE
        rows = await MessageDatabase.get_inbox(
            userprofile_id=userprofile_id,
            limit=PAGE_SIZE,
            offset=offset,
        )
        conversations = []
        for row in rows:
            normalized = dict(row)
            normalized["id"] = str(normalized["id"])
            if normalized.get("last_message_id") is not None:
                normalized["last_message_id"] = str(normalized["last_message_id"])
            conversations.append(normalized)
        badge_map = await resolve_provider_badges(
            c.get("opponent_userprofile_id") for c in conversations
        )
        for conversation in conversations:
            badge = badge_map.get(conversation.get("opponent_userprofile_id")) or {}
            conversation["opponent_provider_type"] = badge.get("provider_type")
            conversation["opponent_badge_label"] = badge.get("badge_label")
        logger.debug(
            "Inbox fetched | user=%s count=%d offset=%d",
            userprofile_id, len(conversations), offset,
        )
        return {"conversations": conversations, "total": len(conversations)}

    @staticmethod
    async def get_unread_count(userprofile_id: str) -> int:
        """Return the total count of unread messages for a user."""
        count = await MessageDatabase.get_unread_count(userprofile_id)
        logger.debug("Unread count | user=%s count=%d", userprofile_id, count)
        return count

    @staticmethod
    async def delete_messages(
        message_ids: list[str], userprofile_id: str
    ) -> dict[str, Any]:
        """Delete de-duplicated messages from the caller's side only and summarise which were deleted.

        Returns:
            Dict of status, requested and deleted counts, deleted_ids and not_deleted_ids.

        Raises:
            HTTPException: 403 when none of the messages could be deleted.
        """
        seen: set[str] = set()
        requested: list[str] = []
        for mid in message_ids:
            mid = (mid or "").strip()
            if mid and mid not in seen:
                seen.add(mid)
                requested.append(mid)

        canonical_of: dict[str, Optional[str]] = {}
        valid_ids: list[str] = []
        for mid in requested:
            try:
                canonical = str(uuid.UUID(mid))
            except (ValueError, AttributeError, TypeError):
                logger.debug("messages: %r is not a message UUID; reporting it as not found", mid)
                canonical_of[mid] = None
                continue
            canonical_of[mid] = canonical
            valid_ids.append(canonical)

        deleted_ids: list[str] = []
        if valid_ids:
            deleted_ids = await MessageDatabase.delete_messages_for_user(
                message_ids=valid_ids,
                userprofile_id=userprofile_id,
            )

        deleted_set = {d.lower() for d in deleted_ids}
        not_deleted_ids = [
            mid
            for mid in requested
            if canonical_of[mid] is None or canonical_of[mid].lower() not in deleted_set
        ]

        if not deleted_ids:
            logger.warning(
                "Message delete denied — not a participant or not found | msgs=%s user=%s",
                requested, userprofile_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Messages not found or you are not a participant of their conversation",
            )

        logger.info(
            "Messages deleted for one side | user=%s requested=%d deleted=%d",
            userprofile_id,
            len(requested),
            len(deleted_ids),
        )
        status_summary = "all" if len(deleted_ids) == len(requested) else "partial"
        return {
            "status": status_summary,
            "requested_count": len(requested),
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "not_deleted_ids": not_deleted_ids,
        }

    @staticmethod
    async def edit_message(
        message_id: str,
        userprofile_id: str,
        message_text: str,
    ) -> dict[str, Any]:
        """Edit a message's text or caption as its sender and return id, text and edit timestamps.

        Raises:
            HTTPException: 404 not found, 403 not the sender, 400 deleted or emptied text message.
        """
        existing = await MessageDatabase.get_message_by_id(message_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message not found: {message_id}",
            )
        if str(existing.get("sender_id", "")).strip().lower() != userprofile_id.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the sender can edit this message",
            )
        if existing.get("is_deleted") or MessageService._is_deleted_for_user(
            existing, userprofile_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot edit a deleted message",
            )
        if existing.get("message_type") == "text" and not message_text.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A text message cannot be edited to empty; delete it instead",
            )

        updated = await MessageDatabase.update_message_text(
            message_id=message_id,
            sender_id=userprofile_id,
            message_text=message_text,
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Message could not be edited",
            )
        updated["id"] = str(updated["id"])
        logger.info("Message edited | msg=%s user=%s", updated["id"], userprofile_id)
        return updated

    @staticmethod
    async def mark_conversation_as_delivered(
        conversation_id: str,
        userprofile_id: str,
        opponent_id: str,
        opponent_type: ConversationParticipantType,
    ) -> int:
        """Mark undelivered messages delivered after membership and opponent checks."""
        MessageService._validate_not_self(userprofile_id, opponent_id)

        conversation = await MessageDatabase.get_conversation_by_id(conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation not found: {conversation_id}",
            )

        MessageService._assert_conversation_membership(conversation, userprofile_id)
        MessageService._assert_opponent_matches_conversation(
            conversation,
            userprofile_id,
            opponent_id,
            opponent_type,
        )

        updated_count = await MessageDatabase.mark_conversation_delivered(
            conversation_id=conversation_id,
            receiver_id=userprofile_id,
        )
        logger.debug(
            "Conversation marked delivered | conv=%s user=%s updated=%d",
            conversation_id, userprofile_id, updated_count,
        )
        return updated_count

    @staticmethod
    async def delete_conversations(
        conversation_ids: list[str], userprofile_id: str
    ) -> dict[str, Any]:
        """Clear de-duplicated conversations from the user's side and return a status, counts and cleared ids summary."""
        seen: set[str] = set()
        requested: list[str] = []
        for cid in conversation_ids:
            cid = (cid or "").strip()
            if cid and cid not in seen:
                seen.add(cid)
                requested.append(cid)
                
        canonical_of: dict[str, Optional[str]] = {}
        valid_ids: list[str] = []
        for cid in requested:
            try:
                canonical = str(uuid.UUID(cid))
            except (ValueError, AttributeError, TypeError):
                logger.debug("messages: %r is not a conversation UUID; reporting it as not found", cid)
                canonical_of[cid] = None
                continue
            canonical_of[cid] = canonical
            valid_ids.append(canonical)

        deleted_ids: list[str] = []
        if valid_ids:
            deleted_ids = await MessageDatabase.delete_conversations_for_user(
                conversation_ids=valid_ids,
                userprofile_id=userprofile_id,
            )

        deleted_set = {d.lower() for d in deleted_ids}
        not_deleted_ids = [
            cid
            for cid in requested
            if canonical_of[cid] is None or canonical_of[cid].lower() not in deleted_set
        ]

        logger.info(
            "Conversation clear | user=%s requested=%d deleted=%d",
            userprofile_id,
            len(requested),
            len(deleted_ids),
        )
        if len(deleted_ids) == len(requested):
            status_summary = "all"
        elif deleted_ids:
            status_summary = "partial"
        else:
            status_summary = "none"
        return {
            "status": status_summary,
            "requested_count": len(requested),
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "not_deleted_ids": not_deleted_ids,
        }
