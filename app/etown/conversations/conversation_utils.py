"""Conversation Utilities - Postgres logic for Chaatie history, AI titles and prompts, and mobile lookup.

Domain layer behind /api/conversations and /api/chat: stores and pages turns in etownuserchaatiehistory, generates titles and follow-up prompt suggestions with gpt-4.1, resolves bulk mobile lookups for members and providers, and records per-conversation Chaatie plan charges.

Features:
    - Chat History: Store, fetch, paginate and delete conversation turns and full threads
    - Title Generation: LLM-generated titles, lazily created and cached per conversation
    - Prompt Suggestions: Stage-aware follow-ups from recent history, filtering placeholders and duplicates
    - Mobile Lookup: Bulk member verification, or provider business details with the onboarding flag
    - Chaatie Cap Ledger: Per-conversation charge claims backing the monthly plan limit
    - JSON Serialization: Date/datetime-safe serializer for metadata stored as jsonb

Classes:
    Database(): Conversation-specific database operations with retry, subclassing the shared Database
    ConversationService(): Business logic for titles, prompts, history and mobile lookup

Functions:
    json_serial(): JSON serializer for date/datetime objects
    _strip_binary_payloads(): Replace data URIs and oversized strings in a metadata tree
    _contains_unfilled_placeholder(): Detect a template placeholder left in a generated suggestion
    _condense_suggestion(): Fit a suggestion to the chip width, trimming filler before truncating

Dependencies:
    - fastapi: HTTPException and status codes for the API-facing error contract
    - asyncio: Concurrent title generation and fire-and-forget duplicate cleanup
    - app.agent_infrastructure.infrastructure.llm_clients: gpt41 client for titles/prompts
    - app.agent_infrastructure.prompt_templates.templates: system/user prompt builders
    - app.utils.ist_clock: format_ist_for_prompt() anchors date/time suggestions to the user's clock
    - app.logs.enhanced_logging: llm_call_monitor() decorator on prompt-suggestion calls
    - app.db.client: Shared Database base class and @with_retry
    - app.etown.plans.plans_utils: premium_display() and resolve_provider_badges() for verified markers
    - app.schemas.api: Request/response models for conversations and mobile lookup

Updates:
    23-Feb-2026: Added comprehensive module documentation
    06-Aug-2026: lookup_mobile_numbers honours an optional onboarding flag returning business details.
    07-Aug-2026: The onboarding lookup no longer filters on publicsearchprovider.
    18-Aug-2026: Added claim/release_conversation_charge for the Chaatie cap.
    20-Aug-2026: Verified markers report the paid tier's badge label; ProviderBusinessDetail carries Etown_providertype.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    04-Sep-2026: save_conversation_message strips data URIs and oversized strings from metadata.
    07-Sep-2026: insert_conversation dedups against recent rows and rejects echoed assistant replies.
    21-Sep-2026: generate_prompt_suggestions anchored to the IST clock; over-length suggestions drop filler words first.
    24-Sep-2026: Suggestion and title generators are LangSmith-traced; their request parameter is renamed payload.
    08-Oct-2026: Removed LangSmith tracing.

Notes:
    Uses Azure OpenAI GPT-4 for intelligent title generation and prompt
    suggestions. Includes mobile phone verification for user authentication.
"""

from typing import List, Optional
import json
import logging
import re
import asyncio
from datetime import datetime, date
from fastapi import HTTPException, status
from app.core.logging import logger
from app.db.client import with_retry, Database as BaseDatabase
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.schemas.api import (
    TitleGenerationRequest,
    PromptSuggestionRequest,
    LogRequest,
    MobileLookupRequest,
    MobileLookupResponse,
    ProviderBusinessDetail
)
from app.agent_infrastructure.infrastructure.llm_clients import gpt41
from app.logs.enhanced_logging import llm_call_monitor
from app.agent_infrastructure.prompt_templates.templates import (
    prompt_suggestion_system_template,
    prompt_suggestion_user_template,
    title_generation_system_template,
    title_generation_user_template
)
from app.utils.ist_clock import format_ist_for_prompt

def json_serial(obj):
    """Serialise a date or datetime to ISO format for json.dumps, raising TypeError for anything else."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]|<[^<>]*>|\$\{?\w+")


def _contains_unfilled_placeholder(text: str) -> bool:
    """Return True when a generated suggestion still contains a template placeholder."""
    return bool(text) and bool(_PLACEHOLDER_RE.search(text))

_SUGGESTION_MAX_CHARS = 40

_SUGGESTION_CONDENSERS = (
    (", at my ", ", my "),
    (", at a ", ", a "),
    (", my ", ", "),
    (", at ", ", "),
    (" at my ", " my "),
)


def _condense_suggestion(text: str, limit: int = _SUGGESTION_MAX_CHARS) -> str:
    """Fit a suggestion to the chip width by dropping filler phrases, truncating with an ellipsis only as a last resort."""
    for phrase, replacement in _SUGGESTION_CONDENSERS:
        if len(text) <= limit:
            break
        text = text.replace(phrase, replacement, 1)
    if len(text) > limit:
        text = text[:limit - 3] + "..."
    return text

_MAX_METADATA_VALUE_CHARS = 20000


def _strip_binary_payloads(value, _depth: int = 0):
    """Replace base64 data URIs and oversized strings in a metadata tree with markers.

    Args:
        value: Any node of a metadata tree.
        _depth: Recursion guard.

    Returns:
        The same structure with payload strings replaced by markers.
    """
    if _depth > 6:
        return value
    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value[:64]:
            kind = value[5:value.index(";base64,")] or "unknown"
            return f"<stripped {kind} data URI, {len(value)} chars — not stored in chat history>"
        if len(value) > _MAX_METADATA_VALUE_CHARS:
            return value[:_MAX_METADATA_VALUE_CHARS] + f"<truncated from {len(value)} chars>"
        return value
    if isinstance(value, dict):
        return {k: _strip_binary_payloads(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strip_binary_payloads(v, _depth + 1) for v in value]
    return value


class Database(BaseDatabase):
    """Database class for managing conversation history."""
    
    @classmethod
    @with_retry()
    async def get_conversation_history(
        cls,
        conversation_id: str,
        limit: int = 50
    ) -> List[dict]:
        """Return the oldest `limit` messages of a conversation in chronological order, or [] on error."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                id, conversation_id, user_id, role, content,
                user_name, user_address, language, tools_used, promptratings,
                metadata,
                created_at
            FROM public.etownuserchaatiehistory
            WHERE conversation_id = $1
            ORDER BY created_at ASC
            LIMIT $2
        """
        
        try:
            async with cls._pool.acquire() as con:
                rows = await con.fetch(query, conversation_id, limit)
                messages = [dict(row) for row in rows]
                debug_ratings = [m.get('promptratings') for m in messages if m.get('promptratings')]
                logger.info(f"Retrieved {len(messages)} messages for {conversation_id}. Found promptratings values: {debug_ratings}")
                return messages
        except Exception as e:
            logger.error(f"Error fetching conversation history for {conversation_id}: {str(e)}", exc_info=True)
            return []

    @classmethod
    @with_retry()
    async def get_recent_conversation_history(
        cls,
        conversation_id: str,
        limit: int = 10
    ) -> List[dict]:
        """Return the newest `limit` messages of a conversation in chronological order, or [] on error."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT id, conversation_id, user_id, role, content,
                   user_name, user_address, language, tools_used, promptratings,
                   metadata, created_at
            FROM (
                SELECT id, conversation_id, user_id, role, content,
                       user_name, user_address, language, tools_used, promptratings,
                       metadata, created_at
                FROM public.etownuserchaatiehistory
                WHERE conversation_id = $1
                ORDER BY created_at DESC
                LIMIT $2
            ) recent
            ORDER BY created_at ASC
        """

        try:
            async with cls._pool.acquire() as con:
                rows = await con.fetch(query, conversation_id, limit)
                messages = [dict(row) for row in rows]
                logger.info(f"Retrieved {len(messages)} recent messages for {conversation_id}")
                return messages
        except Exception as e:
            logger.error(f"Error fetching recent conversation history for {conversation_id}: {str(e)}", exc_info=True)
            return []

    ECHO_MIN_LENGTH = 40

    @classmethod
    @with_retry()
    async def get_latest_message(cls, conversation_id: str) -> Optional[dict]:
        """Return the id, role and content of a conversation's latest message, or None."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
            
        query = """
            SELECT id, role, content 
            FROM public.etownuserchaatiehistory
            WHERE conversation_id = $1
            ORDER BY created_at DESC
            LIMIT 1
        """
        try:
            async with cls._pool.acquire() as con:
                row = await con.fetchrow(query, conversation_id)
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error fetching latest message for {conversation_id}: {str(e)}", exc_info=True)
            return None
    
    @classmethod
    async def find_duplicate_message(
        cls, conversation_id: str, role: str, content: str
    ) -> Optional[dict]:
        """Return the row an insert would duplicate as a 5-second replay or 10-minute assistant echo, or None.

        Args:
            conversation_id: Conversation the insert targets.
            role: Role the insert claims.
            content: Message text.

        Returns:
            Dict of id, role and reason for the duplicated row, or None when new.
        """
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT id, role, 'replay' AS reason
            FROM public.etownuserchaatiehistory
            WHERE conversation_id = $1 AND role = $2 AND content = $3
              AND created_at > now() - interval '5 seconds'
            UNION ALL
            SELECT id, role, 'assistant_echo' AS reason
            FROM public.etownuserchaatiehistory
            WHERE conversation_id = $1 AND role = 'assistant' AND content = $3
              AND $2 = 'user' AND length($3) >= $4
              AND created_at > now() - interval '10 minutes'
            LIMIT 1
        """
        try:
            async with cls._pool.acquire() as con:
                row = await con.fetchrow(
                    query, conversation_id, role, content, cls.ECHO_MIN_LENGTH
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Duplicate check failed for {conversation_id}: {str(e)}", exc_info=True
            )
            return None

    @classmethod
    @with_retry()
    async def save_conversation_message(
        cls,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        user_name: str = "User",
        user_address: str = "",
        language: str = "en",
        tools_used: Optional[List[str]] = None,
        promptratings: bool = False,
        metadata: Optional[dict] = None
    ) -> Optional[int]:
        """Insert a chat history row with binary payloads stripped from metadata and return its id, or None."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        
        query = """
            INSERT INTO public.etownuserchaatiehistory (
                conversation_id, user_id, role, content, user_name, 
                user_address, language, tools_used, metadata, promptratings, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
            RETURNING id
        """
        
        try:
            tools_json = json.dumps(tools_used or [])
            safe_metadata = _strip_binary_payloads(metadata or {})
            metadata_json = json.dumps(safe_metadata, default=json_serial)
            if len(metadata_json) > _MAX_METADATA_VALUE_CHARS:
                logger.warning(
                    "Conversation %s metadata is %d chars after stripping binaries; "
                    "something is persisting a payload rather than a reference",
                    conversation_id, len(metadata_json),
                )

            async with cls._pool.acquire() as con:
                result = await con.fetchrow(
                    query,
                    conversation_id, user_id, role, content, user_name,
                    user_address, language, tools_json, metadata_json, promptratings
                )
                message_id = result['id'] if result else None
                logger.info(f"Saved {role} message {message_id} to conversation {conversation_id}. PromptRatings saved as: {promptratings}")
                return message_id
        except Exception as e:
            logger.error(f"Error saving message to conversation {conversation_id}: {str(e)}", exc_info=True)
            return None
    
    @classmethod
    @with_retry()
    async def claim_conversation_charge(cls, conversation_id: str, userprofile_id: str) -> bool:
        """Claim a provider's conversation for the Chaatie cap; return True only when this insert won it."""
        if not conversation_id or not userprofile_id:
            return False
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            INSERT INTO notifications.etown_provider_plan_conversation
                (userprofileid, conversation_id)
            VALUES ($1, $2)
            ON CONFLICT (userprofileid, conversation_id) DO NOTHING
            RETURNING TRUE
        """
        try:
            async with cls._pool.acquire() as con:
                return await con.fetchval(query, userprofile_id, conversation_id) is True
        except Exception as e:
            logger.error(
                f"Error claiming conversation charge for {conversation_id}: {str(e)}",
                exc_info=True,
            )
            return False

    @classmethod
    @with_retry()
    async def release_conversation_charge(cls, conversation_id: str, userprofile_id: str) -> None:
        """Hand back a conversation claim when the turn it paid for never happened."""
        if not conversation_id or not userprofile_id:
            return
        if cls._pool is None or cls._pool._closed:
            return

        query = """
            DELETE FROM notifications.etown_provider_plan_conversation
             WHERE userprofileid = $1
               AND conversation_id = $2
        """
        try:
            async with cls._pool.acquire() as con:
                await con.execute(query, userprofile_id, conversation_id)
        except Exception as e:
            logger.error(
                f"Error releasing conversation charge for {conversation_id}: {str(e)}",
                exc_info=True,
            )

    @classmethod
    @with_retry()
    async def delete_messages(cls, message_ids: List[int]) -> bool:
        """Delete chat history rows by id and return True on success."""
        if not message_ids:
            return False
        if cls._pool is None or cls._pool._closed:
            return False
            
        query = "DELETE FROM public.etownuserchaatiehistory WHERE id = ANY($1::int[])"
        
        try:
            async with cls._pool.acquire() as con:
                await con.execute(query, message_ids)
                logger.info(f"Deleted {len(message_ids)} duplicate messages: {message_ids}")
                return True
        except Exception as e:
            logger.error(f"Error deleting messages {message_ids}: {str(e)}", exc_info=True)
            return False

    @classmethod
    @with_retry()
    async def delete_conversation(
        cls,
        conversation_id: str,
        user_id: str = None
    ) -> bool:
        """Delete a conversation's messages, optionally scoped to a user, and return True if any were deleted."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        
        if user_id:
            query = "DELETE FROM public.etownuserchaatiehistory WHERE conversation_id = $1 AND user_id = $2 RETURNING id"
            args = (conversation_id, user_id)
        else:
            query = "DELETE FROM public.etownuserchaatiehistory WHERE conversation_id = $1 RETURNING id"
            args = (conversation_id,)

        try:
            async with cls._pool.acquire() as con:
                result = await con.fetch(query, *args)
                deleted_count = len(result)
                if deleted_count > 0:
                    logger.info(f"Deleted {deleted_count} messages from conversation {conversation_id}")
                    return True
                return False
        except Exception as e:
            logger.error(f"Error deleting conversation {conversation_id}: {str(e)}", exc_info=True)
            raise
    
    @classmethod
    @with_retry()
    async def get_user_conversations(
        cls,
        user_id: str,
        limit: int = 20
    ) -> List[dict]:
        """Return a user's conversations with their last message, newest first, or [] on error."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        
        query = """
            SELECT * FROM (
                SELECT DISTINCT ON (conversation_id)
                    conversation_id,
                    user_id,
                    user_name,
                    language,
                    content as last_message,
                    created_at as last_message_at,
                    role as last_message_role
                FROM public.etownuserchaatiehistory
                WHERE user_id = $1
                ORDER BY conversation_id, created_at DESC
            ) subquery
            ORDER BY last_message_at DESC
            LIMIT $2
        """
        
        try:
            async with cls._pool.acquire() as con:
                rows = await con.fetch(query, user_id, limit)
                conversations = [dict(row) for row in rows]
                logger.info(f"Retrieved {len(conversations)} conversations for user {user_id}")
                return conversations
        except Exception as e:
            logger.error(f"Error fetching conversations for user {user_id}: {str(e)}", exc_info=True)
            return []

    @classmethod
    @with_retry()
    async def get_user_conversation_ids_paginated(
        cls,
        user_id: str,
        limit: int = 10,
        offset: int = 0
    ) -> tuple[List[dict], int]:
        """Return a page of a user's conversation ids with last_message_at, newest first, plus the total count."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        query = """
            SELECT 
                conversation_id,
                MAX(created_at) as last_message_at
            FROM public.etownuserchaatiehistory
            WHERE user_id = $1
            GROUP BY conversation_id
            ORDER BY last_message_at DESC
            LIMIT $2 OFFSET $3
        """
        count_query = """
            SELECT COUNT(DISTINCT conversation_id) 
            FROM public.etownuserchaatiehistory 
            WHERE user_id = $1
        """
        try:
            async with cls._pool.acquire() as con:
                total_count = await con.fetchval(count_query, user_id)
                rows = await con.fetch(query, user_id, limit, offset)
                result = [dict(row) for row in rows]
                return result, total_count
        except Exception as e:
            logger.error(f"Error fetching conversation IDs for user {user_id}: {str(e)}", exc_info=True)
            return [], 0

    @classmethod
    @with_retry()
    async def get_providers_by_mobile_last10(cls, mobile_last10: List[str]) -> List[dict]:
        """Fetch non-deleted provider business details, verified or not, matching any of the last-10-digit mobile numbers."""
        if not mobile_last10:
            return []
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            SELECT
                right(regexp_replace(mobilenumber, '[^0-9]', '', 'g'), 10) AS mobile_last10,
                providerid, contactid, userprofileid,
                businessname, businesscategory, firstname, description,
                address, additionaladdressinfo, areaname, city, townname, statecode,
                latitude, longitude,
                mobilenumber, email, websiteurl,
                availability, servicetype, providertype, publicsearchprovider,
                serviceimageurl, yearsinbusiness, role, rolename, whatsapp, creationdate
            FROM public.etown_provider
            WHERE right(regexp_replace(mobilenumber, '[^0-9]', '', 'g'), 10) = ANY($1::text[])

              AND deleted IS DISTINCT FROM 'true'
            ORDER BY businessname, providerid
"""

        try:
            async with cls._pool.acquire() as con:
                rows = await con.fetch(query, mobile_last10)
                logger.info(
                    f"Provider business lookup matched {len(rows)} rows for "
                    f"{len(mobile_last10)} mobile number(s)"
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching provider business details by mobile: {str(e)}", exc_info=True)
            return []

    @classmethod
    @with_retry()
    async def get_titles_batch(cls, conversation_ids: List[str]) -> List[dict]:
        """Fetch logged titles for a list of conversation ids, or [] on error."""
        if not conversation_ids:
            return []
            
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        query = """
            SELECT conversation_id, response, date 
            FROM public.etowntitlegenerationlogs 
            WHERE conversation_id = ANY($1::text[])
        """
        try:
            async with cls._pool.acquire() as con:
                rows = await con.fetch(query, conversation_ids)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching titles batch: {str(e)}", exc_info=True)
            return []

    @classmethod
    @with_retry()
    async def save_title_log(cls, data: dict) -> bool:
        """Insert a generated title with its token usage into etowntitlegenerationlogs; return True on success."""
        if cls._pool is None or cls._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")

        query = """
            INSERT INTO public.etowntitlegenerationlogs (
                contact_id, conversation_id, user_profile_id, 
                input_text, response, model, 
                prompt_tokens, completion_tokens, total_tokens,
                last_message_time, date
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
        """

        try:
            async with cls._pool.acquire() as con:
                await con.execute(
                    query,
                    data['contact_id'],
                    data['conversation_id'],
                    data['user_profile_id'],
                    data['input_text'],
                    data['response'],
                    data['model'],
                    data.get('prompt_tokens', 0),
                    data.get('completion_tokens', 0),
                    data.get('total_tokens', 0),
                    data.get('last_message_time')
                )
                return True
        except Exception as e:
            logger.error(f"Error saving title log for {data.get('conversation_id')}: {str(e)}", exc_info=True)
            return False


class ConversationService:
    """Service class containing all conversation-related business logic."""
    
    @staticmethod
    @llm_call_monitor('gpt-4.1', 'prompt_suggestion')
    async def generate_prompt_suggestions(payload: PromptSuggestionRequest) -> dict:
        """Generate up to three follow-up prompt chips from recent history, tools used and the detected conversation stage."""
        
        raw_history = await Database.get_recent_conversation_history(
            payload.conversation_id, limit=10
        )
        history = []
        last_content = None
        for record in raw_history:
            current_content = record.get('content', '')
            if current_content == last_content:
                continue
            history.append(record)
            last_content = current_content
        
        latest_user_message = payload.latest_user_message
        last_assistant_message = None
        all_tools_used = set()
        conversation_summary_parts = []

        if history:
            for record in history[-6:]:
                role = record.get('role', '')
                content = record.get('content', '')
                if content:
                    truncated = content[:500] + "..." if len(content) > 500 else content
                    conversation_summary_parts.append(f"{role.capitalize()}: {truncated}")
            for record in history[-4:]:
                tools_val = record.get('tools_used')
                if isinstance(tools_val, str):
                    try:
                        tools_list = json.loads(tools_val)
                        if isinstance(tools_list, list):
                            all_tools_used.update(tools_list)
                    except (json.JSONDecodeError, TypeError):
                        logger.debug("conversation: tools_used column is not valid JSON; skipping that turn")
                elif isinstance(tools_val, list):
                    all_tools_used.update(tools_val)
            
            if not latest_user_message:
                for record in reversed(history):
                    if record.get('role') == 'user' and latest_user_message is None:
                        latest_user_message = record.get('content', '')
                    elif record.get('role') == 'assistant' and last_assistant_message is None:
                        last_assistant_message = record.get('content', '')
                    if latest_user_message and last_assistant_message:
                        break
            else:
                for record in reversed(history):
                    if record.get('role') == 'assistant':
                        last_assistant_message = record.get('content', '')
                        break

        if not latest_user_message:
            return {
                "success": True,
                "conversation_id": payload.conversation_id,
                "suggested_prompts": [],
            }

        tools_list = list(all_tools_used)
        conversation_stage = ConversationService._detect_conversation_stage(
            tools_list, history or []
        )
        
        conversation_summary = "\n".join(conversation_summary_parts) if conversation_summary_parts else None

        user_profile_lines = []
        if payload.name and str(payload.name).strip() and str(payload.name).lower() != "none":
            user_profile_lines.append(f"- Name: {payload.name}")
        if payload.address and str(payload.address).strip() and str(payload.address).lower() != "none":
            user_profile_lines.append(f"- Address: {payload.address}")
        
        user_profile_context = "\n".join(user_profile_lines)
        if user_profile_context:
            user_profile_context = f"User Context:\n{user_profile_context}"

        system_prompt = prompt_suggestion_system_template(
            user_profile_context=user_profile_context,
            language=payload.language,
            conversation_stage=conversation_stage,
            tools_used=list(all_tools_used),
            current_time=format_ist_for_prompt()
        )
        user_prompt = prompt_suggestion_user_template(
            latest_user_message,
            last_assistant_message,
            conversation_summary
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        response = await gpt41.ainvoke(messages)
        response_text = response.content if hasattr(response, 'content') else str(response)
        
        cleaned_text = response_text.replace("```json", "").replace("```", "").strip()
        
        json_match = re.search(r'\[.*\]', cleaned_text, re.DOTALL)
        
        suggested_prompts = []
        if json_match:
            try:
                suggested_prompts = json.loads(json_match.group())
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse prompt suggestions JSON for {payload.conversation_id}")

        if not suggested_prompts:
            list_matches = re.findall(r'\d+\.\s*"?([^"\n]+)"?', cleaned_text)
            if list_matches:
                suggested_prompts = list_matches
        final_prompts = []
        if isinstance(suggested_prompts, list):
            for s in suggested_prompts:
                if isinstance(s, str):
                    clean_s = s.strip()
                    if _contains_unfilled_placeholder(clean_s):
                        logger.warning(
                            f"Dropped suggestion with unfilled placeholder for "
                            f"{payload.conversation_id}: {clean_s!r}"
                        )
                        continue
                    clean_s = _condense_suggestion(clean_s)
                    if clean_s:
                        final_prompts.append(clean_s)
                    if len(final_prompts) == 3:
                        break
        suggested_prompts = final_prompts

        return {
            "success": True,
            "conversation_id": payload.conversation_id,
            "suggested_prompts": suggested_prompts,
        }

    @staticmethod
    def _detect_conversation_stage(tools_used: list, history: list) -> str:
        """Classify the conversation stage, such as discovery or booking_flow, from tools used and recent message text."""
        if not history:
            return "new_conversation"

        recent_assistant_content = ""
        recent_assistant_meta: dict = {}
        for record in reversed(history):
            if record.get('role') == 'assistant':
                recent_assistant_content = (record.get('content', '') or '').lower()
                meta_val = record.get('metadata')
                if isinstance(meta_val, str):
                    try:
                        recent_assistant_meta = json.loads(meta_val) or {}
                    except (json.JSONDecodeError, TypeError):
                        logger.debug("conversation: assistant metadata is not valid JSON; treating it as empty")
                        recent_assistant_meta = {}
                elif isinstance(meta_val, dict):
                    recent_assistant_meta = meta_val or {}
                break

        booking_completed_by_meta = (
            recent_assistant_meta.get('response_type') == 'request_provider'
            and bool(recent_assistant_meta.get('implicit_output'))
        )

        booking_tools = {'request_provider', 'book_service'}
        search_tools = {'search_providers', 'booknow_provider', 'provider_service_insights'}
        general_tools = {'web_search'}
        offer_creation_tools = {'create_offer_from_chat', 'create_post_from_chat'}
        
        tools_set = set(tools_used)
        has_booking_tools = bool(tools_set & booking_tools)
        has_search_tools = bool(tools_set & search_tools)
        has_general_tools = bool(tools_set & general_tools)
        has_offer_creation_tools = bool(tools_set & offer_creation_tools)
        
        booking_confirmation_signals = [
            'booked successfully', 'booking confirmed', 'request has been sent',
            'booking has been', 'successfully booked', 'order placed',
            'request sent', 'service has been booked',
            'submitted successfully', 'successfully submitted',
            'request has been submitted', 'has been submitted', 'tracking id'
        ]
        if booking_completed_by_meta or (
            has_booking_tools
            and any(signal in recent_assistant_content for signal in booking_confirmation_signals)
        ):
            return "post_booking"
        
        if has_booking_tools:
            return "booking_flow"
    
        offer_publication_signals = [
            'is live', 'has been published', 'shared with your circle',
            'published and shared', 'your offer', 'your greeting has been published',
            'your post', 'been shared'
        ]
        if has_offer_creation_tools and any(signal in recent_assistant_content for signal in offer_publication_signals):
            return "post_offer_creation"

        booking_detail_request_signals = [
            'date and time', 'time and date', 'date & time',
            'delivery address', 'service address', 'shipping address',
            'preferred date', 'preferred time', 'preferred address',
            'what date', 'what time', 'which address', 'what address',
            'when should', 'when would you like', 'profile address',
            'address should we use', 'address would you like',
            'like it delivered', 'like your delivery',
        ]
        if any(signal in recent_assistant_content for signal in booking_detail_request_signals):
            return "booking_flow"

        if has_search_tools:
            booking_intent_signals = [
                'book', 'proceed', 'confirm', 'yes', 'go ahead', 'schedule',
                'address', 'date', 'time', 'tomorrow', 'today'
            ]
            last_user_content = ""
            for record in reversed(history):
                if record.get('role') == 'user':
                    last_user_content = (record.get('content', '') or '').lower()
                    break
            
            if any(signal in last_user_content for signal in booking_intent_signals):
                return "booking_flow"
            
            return "discovery"
        
        if has_general_tools or not tools_used:
            if len(history) <= 2 and not tools_used:
                return "new_conversation"
            return "general_chat"
        
        return "general_chat"
    
    @staticmethod
    async def generate_conversation_title(payload: TitleGenerationRequest) -> dict:
        """Generate an 80-character-max title for a conversation with gpt41, raising 404 when it has no history."""
        history = await Database.get_conversation_history(payload.conversation_id, limit=10)
        
        if not history:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation {payload.conversation_id} not found"
            )
        
        conversation_summary = ""
        detected_language = "en"
        
        for record in history[:5]:
            conversation_summary += f"{record['role'].capitalize()}: {record['content']}\n"
            if record.get('language'):
                detected_language = record['language']
        
        system_prompt = title_generation_system_template(detected_language)
        user_prompt = title_generation_user_template(conversation_summary)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        response = await gpt41.ainvoke(messages)
        response_text = response.content if hasattr(response, 'content') else str(response)
        
        suggested_title = response_text.strip().strip('"').strip("'")
        suggested_title = re.sub(r'^#+\s*', '', suggested_title)
        suggested_title = re.sub(r'\*\*(.+?)\*\*', r'\1', suggested_title)
        
        if len(suggested_title) > 80:
            suggested_title = suggested_title[:77] + "..."
        
        return {
            "success": True,
            "conversation_id": payload.conversation_id,
            "suggested_title": suggested_title,
            "message_count": len(history)
        }
    
    @staticmethod
    async def insert_conversation_message(log_request: LogRequest) -> dict:
        """Save a logged chat turn unless it duplicates a recent row, raising 500 when the insert fails."""
        metadata = log_request.metadata or {}
        if log_request.response_type:
            metadata["response_type"] = log_request.response_type
        if log_request.new is not None:
            metadata["new"] = log_request.new
        if log_request.audio is not None:
            metadata["audio"] = log_request.audio
        if log_request.audio_file:
            metadata["audio_file"] = log_request.audio_file
        promptratings = False

        duplicate = await Database.find_duplicate_message(
            log_request.conversation_id, log_request.role, log_request.conversation
        )
        if duplicate:
            logger.info(
                "Skipping %s for conversation %s (matches message %s, role %s)",
                duplicate["reason"], log_request.conversation_id,
                duplicate["id"], duplicate["role"],
            )
            return {
                "success": True,
                "message_id": duplicate["id"],
                "conversation_id": log_request.conversation_id,
                "message": f"Duplicate message skipped ({duplicate['reason']})"
            }
        
        message_id = await Database.save_conversation_message(
            conversation_id=log_request.conversation_id,
            user_id=log_request.user_profile_id,
            role=log_request.role,
            content=log_request.conversation,
            user_name=log_request.user_name,
            user_address=log_request.address,
            language=log_request.language,
            tools_used=log_request.tools_used,
            metadata=metadata,
            promptratings=promptratings
        )
        
        if message_id:
            return {
                "success": True,
                "message_id": message_id,
                "conversation_id": log_request.conversation_id,
                "message": "Conversation message inserted successfully"
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to insert conversation message"
            )
    
    @staticmethod
    async def get_conversation(conversation_id: str, limit: int = 50) -> dict:
        """Return a conversation's messages with consecutive duplicates removed and deleted in the background, or raise 404."""
        history = await Database.get_conversation_history(conversation_id, limit=limit)
        
        if not history:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation {conversation_id} not found"
            )
        
        messages = []
        duplicate_ids = []
        last_role = None
        last_content = None

        for record in history:
            current_role = record['role']
            current_content = record['content']
            if last_role is not None and last_content is not None:
                if current_content == last_content:
                    duplicate_ids.append(record['id'])
                    continue
            last_role = current_role
            last_content = current_content
            tools_val = record.get('tools_used')
            tools_parsed = []
            if isinstance(tools_val, str):
                try:
                    tools_parsed = json.loads(tools_val)
                except json.JSONDecodeError:
                    logger.debug("conversation: tools_used column is not valid JSON; returning an empty list")
                    tools_parsed = []
            elif isinstance(tools_val, list):
                tools_parsed = tools_val
            
            meta_val = record.get('metadata')
            meta_parsed = {}
            if isinstance(meta_val, str):
                try:
                    meta_parsed = json.loads(meta_val)
                except json.JSONDecodeError:
                    logger.debug("conversation: metadata column is not valid JSON; returning an empty dict")
                    meta_parsed = {}
            elif isinstance(meta_val, dict):
                meta_parsed = meta_val or {}
                
            messages.append({
                "id": record['id'],
                "conversation_id": str(record['conversation_id']),
                "user_id": record['user_id'],
                "user_name": record['user_name'] or "User",
                "role": record['role'],
                "response": record['content'],
                "tools_used": tools_parsed,
                "created_at": record['created_at'].isoformat(),
                "response_type": meta_parsed.get("response_type"),
                "implicit_output": meta_parsed.get("implicit_output")
            })
        
        if duplicate_ids:
            asyncio.create_task(Database.delete_messages(duplicate_ids))
        
        return {
            "conversation_id": conversation_id,
            "message_count": len(messages),
            "messages": messages
        }
    
    @staticmethod
    async def get_user_conversations(user_id: str, limit: int = 10) -> dict:
        """Return summaries of a user's most recent conversations."""
        conversations = await Database.get_user_conversations(user_id, limit=limit)
        
        summaries = []
        for record in conversations:
            summaries.append({
                "conversation_id": str(record['conversation_id']),
                "user_id": record['user_id'],
                "user_name": record['user_name'] or "User",
                "message_count": record.get('message_count', 0),
                "last_message_at": record['last_message_at'].isoformat()
            })
        
        return {
            "user_id": user_id,
            "conversation_count": len(summaries),
            "conversations": summaries
        }
    
    @staticmethod
    async def delete_conversation(conversation_id: str) -> dict:
        """Delete all messages of a conversation, raising 404 when none existed."""
        deleted = await Database.delete_conversation(conversation_id)
        
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation {conversation_id} not found"
            )
        
        return {
            "conversation_id": conversation_id,
            "deleted": True,
            "message": "Conversation successfully deleted"
        }
    
    @staticmethod
    async def lookup_mobile_numbers(request: MobileLookupRequest) -> MobileLookupResponse:
        """Return which numbers belong to members, or with onboarding set, the matching providers' business details."""
        onboarding = bool(request.onboarding)
        mobile_numbers = request.mobile_numbers
        if not mobile_numbers:
            return MobileLookupResponse(
                etown_mobile_numbers=[], total_found=0, total_searched=0,
                onboarding=onboarding,
                business_details=[] if onboarding else None
            )

        cleaned_numbers = []
        for number in mobile_numbers:
            if number and str(number).strip():
                cleaned = ''.join(filter(str.isdigit, str(number)))
                if cleaned:
                    cleaned_numbers.append(cleaned)

        cleaned_numbers = list(set(cleaned_numbers))

        if not cleaned_numbers:
            return MobileLookupResponse(
                etown_mobile_numbers=[], total_found=0, total_searched=0,
                onboarding=onboarding,
                business_details=[] if onboarding else None
            )

        if onboarding:
            return await ConversationService._lookup_provider_business_details(cleaned_numbers)

        placeholders = ','.join([f"${i+1}" for i in range(len(cleaned_numbers))])

        userprofile_query = f"""
            SELECT DISTINCT mobilenumber
            FROM etown_userprofile
            WHERE mobilenumber IN ({placeholders})
              AND role = 2
              AND deleted IS DISTINCT FROM 'true'
        """

        results = await Database.fetch(userprofile_query, *cleaned_numbers)

        etown_mobile_numbers = [
            result.get('mobilenumber') for result in results if result.get('mobilenumber')
        ]

        return MobileLookupResponse(
            etown_mobile_numbers=etown_mobile_numbers,
            total_found=len(etown_mobile_numbers),
            total_searched=len(cleaned_numbers),
            onboarding=False,
            business_details=None
        )

    @staticmethod
    async def _lookup_provider_business_details(cleaned_numbers: List[str]) -> MobileLookupResponse:
        """Resolve digits-only numbers to business details of all matching providers by their last 10 digits."""
        last10_to_input: dict = {}
        for number in cleaned_numbers:
            key = number[-10:]
            existing = last10_to_input.get(key)
            if existing is None or len(number) < len(existing):
                last10_to_input[key] = number

        rows = await Database.get_providers_by_mobile_last10(
            list(last10_to_input.keys())
        )

        badge_map = await resolve_provider_badges(row.get('userprofileid') for row in rows)
        business_details = []
        matched_inputs = []
        for row in rows:
            supplied = last10_to_input.get(row.get('mobile_last10'))
            if supplied and supplied not in matched_inputs:
                matched_inputs.append(supplied)
            premium_label = (badge_map.get(row.get('userprofileid')) or {}).get("premium_label")
            business_details.append(ProviderBusinessDetail(
                mobile_number=supplied,
                providerid=row.get('providerid'),
                contactid=row.get('contactid'),
                userprofileid=row.get('userprofileid'),
                businessname=row.get('businessname'),
                businesscategory=row.get('businesscategory'),
                firstname=row.get('firstname'),
                description=row.get('description'),
                address=row.get('address'),
                additionaladdressinfo=row.get('additionaladdressinfo'),
                areaname=row.get('areaname'),
                city=row.get('city'),
                townname=row.get('townname'),
                statecode=row.get('statecode'),
                latitude=row.get('latitude'),
                longitude=row.get('longitude'),
                mobilenumber=row.get('mobilenumber'),
                email=row.get('email'),
                websiteurl=row.get('websiteurl'),
                availability=row.get('availability'),
                servicetype=row.get('servicetype'),
                Etown_providertype=premium_display(row.get('providertype'), premium_label),
                publicsearchprovider=row.get('publicsearchprovider'),
                serviceimageurl=row.get('serviceimageurl'),
                yearsinbusiness=row.get('yearsinbusiness'),
                role=row.get('role'),
                rolename=row.get('rolename'),
                whatsapp=row.get('whatsapp'),
                creationdate=row.get('creationdate'),
            ))

        return MobileLookupResponse(
            etown_mobile_numbers=matched_inputs,
            total_found=len(matched_inputs),
            total_searched=len(cleaned_numbers),
            onboarding=True,
            business_details=business_details
        )
    
    @staticmethod
    async def get_conversation_list_with_titles(
        contact_id: str,
        user_profile_id: str,
        page: int = 1,
        limit: int = 10
    ) -> dict:
        """Return a page of a user's conversations with titles, generating and logging any missing titles concurrently."""
        offset = (page - 1) * limit
        conversations, total_count = await Database.get_user_conversation_ids_paginated(
            user_profile_id, limit, offset
        )
        if not conversations:
             return {
                "titles": [],
                "total_count": 0,
                "page": page,
                "limit": limit
            }
        conv_ids = [c['conversation_id'] for c in conversations]
        conv_map = {c['conversation_id']: c for c in conversations}
        existing_titles = await Database.get_titles_batch(conv_ids)
        title_map = {t['conversation_id']: t for t in existing_titles}
        
        results_array = [None] * len(conv_ids)
        coros = []
        coro_indices = []
        
        async def generate_and_save_title(conv_id):
            try:
                history = await Database.get_conversation_history(conv_id, limit=5)
                if not history:
                    return None
                conversation_summary = ""
                detected_language = "en"
                for record in history:
                    conversation_summary += f"{record['role'].capitalize()}: {record['content']}\n"
                    if record.get('language'):
                        detected_language = record['language']
                system_prompt = title_generation_system_template(detected_language)
                user_prompt = title_generation_user_template(conversation_summary)
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
                response = await gpt41.ainvoke(messages)
                generated_title = response.content if hasattr(response, 'content') else str(response)
                generated_title = generated_title.strip().strip('"').strip("'")
                generated_title = re.sub(r'^#+\s*', '', generated_title)
                generated_title = re.sub(r'\*\*(.+?)\*\*', r'\1', generated_title)
                if len(generated_title) > 80:
                    generated_title = generated_title[:77] + "..."
                token_usage = response.response_metadata.get("token_usage", {}) if hasattr(response, 'response_metadata') else {}
                prompt_tokens = token_usage.get("prompt_tokens", 0)
                completion_tokens = token_usage.get("completion_tokens", 0)
                total_tokens = token_usage.get("total_tokens", 0)
                model_name = response.response_metadata.get("model_name", "gpt-4o") if hasattr(response, 'response_metadata') else "gpt-4o"
                last_msg_time = conv_map[conv_id].get('last_message_at')
                log_data = {
                    "contact_id": contact_id,
                    "conversation_id": conv_id,
                    "user_profile_id": user_profile_id,
                    "input_text": conversation_summary,
                    "response": generated_title,
                    "model": model_name,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "last_message_time": last_msg_time
                }
                await Database.save_title_log(log_data)
                return {
                    "conversation_id": conv_id,
                    "title": generated_title,
                    "date": datetime.now().isoformat()
                }
            except Exception as e:
                logger.error(f"Failed to generate title for {conv_id}: {str(e)}", exc_info=True)
                return {
                    "conversation_id": conv_id,
                    "title": "Conversation",
                    "date": datetime.now().isoformat()
                }

        for i, conv_id in enumerate(conv_ids):
            title_entry = title_map.get(conv_id)
            if title_entry:
                results_array[i] = {
                    "conversation_id": conv_id,
                    "title": title_entry['response'],
                    "date": title_entry['date'].isoformat() if isinstance(title_entry['date'], (datetime, date)) else str(title_entry['date'])
                }
            else:
                coros.append(generate_and_save_title(conv_id))
                coro_indices.append(i)
                
        if coros:
            gathered_results = await asyncio.gather(*coros)
            for idx, res in zip(coro_indices, gathered_results):
                results_array[idx] = res

        final_list = [r for r in results_array if r is not None]
        return {
            "titles": final_list,
            "total_count": total_count,
            "page": page,
            "limit": limit
        }