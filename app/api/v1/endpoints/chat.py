"""Chat Endpoints - HTTP route for Chaatie conversational turns.

HTTP layer for POST /api/chat/chaatie: checks the shared bearer token and plan limits, sets the execution context, runs the pre-LLM classifier, invokes the member or provider LangGraph agent with Mem0 recall and chat history, and persists the turn.

Features:
    - Agent chat: LangGraph-powered conversational AI interactions
    - Multi-agent support: Member and provider specialized agents
    - Guest mode: guest ids get search and general answers only, with response_type guestuser otherwise
    - Memory management: Conversation history persistence and retrieval
    - Long-term recall: similarity-filtered Mem0 facts plus previously-booked providers in the prompt
    - Context handling: Execution context management for tool calls
    - Image continuity: the latest generate_image result is restored as restored_generated_image
    - Classification Bridge: pre-LLM intent classifier gated by CLASSIFIER_MODE
    - Plan limits: monthly provider conversation cap and daily message allowance
    - Translation support: Multi-language conversation handling
    - Token verification: Secure authenticated chat sessions
    - Tool integration: Agent tool execution and response handling
    - Appearance logging: listing turns record the providers they surfaced
    - Performance monitoring: Detailed timing of execution steps

Classes:
    No classes defined (request validation uses ChaatieRequest from app.schemas.api)

Endpoints:
    POST /chaatie: Main chat endpoint for agent interactions
    POST /processing_texts: Status lines matching the prompt, shown while /chaatie processes

Functions:
    _merge_listing_rows(): Merges a turn's bookable rows without duplicating providers
    chat(): Process chat messages through LangGraph agents
    processing_texts(): Returns request-specific status lines for the processing indicator
    chaatie_turn(): The turn chat() runs
    background_add_memory(): Adds the user's turn to the Mem0 store in the background
    _sync_memory_search(): Synchronous Mem0 search, run in a worker thread with a timeout

Dependencies:
    - fastapi: API framework and routing
    - app.agent_infrastructure: LangGraph agents, execution context and Classification Bridge
    - app.core.security: shared bearer-token verification
    - app.core.memory: Vector memory client and its retrieval tuning constants
    - app.etown.memory.memory_utils: Memory scoring, provider affinity and the memory switch
    - app.etown.plans.plans_utils: plan resolution, usage caps and provider badges
    - app.etown.search.search_appearance_utils: logs provider search appearances
    - app.services.api_activity: API call tracking
    - app.utils.blob_storage: uploads chat images to blob storage
    - app.utils.translation_utils: Multi-language support
    - app.schemas.api: Request/response validation models
    - app.etown.chat.chat_utils: status lines for the processing indicator
    - app.utils.performance: Detailed performance tracking

Updates:
    23-Feb-2026: Expanded endpoint documentation.
    03-Mar-2026: Updated background memory addition and memory search handling.
    22-Jul-2026: Wired the Classification Bridge behind settings.CLASSIFIER_MODE.
    27-Jul-2026: Removed the internal-invite confirmation short-circuit.
    18-Aug-2026: Monthly provider conversation cap (402); daily message allowance; badge_label in implicit_output.
    19-Aug-2026: Daily allowance claimed from the usage ledger.
    20-Aug-2026: Verified markers report badge label; records each turn's search terms.
    26-Aug-2026: Logged previously-silent exception handlers with tracebacks.
    01-Sep-2026: Agent time context uses the server IST clock.
    07-Sep-2026: Similarity-filtered memories; user-turn-only extraction; provider affinity; memory switch.
    08-Sep-2026: Ignores non-image image_prompt; recommend_products rows restore selected_providers.
    17-Sep-2026: Listing turns log search appearances.
    24-Sep-2026: Each turn is one LangSmith trace (chaatie_turn).
    28-Sep-2026: History restores restored_generated_image for set_profile_image_from_chat.
    29-Sep-2026: restored_generated_image carries seed and design for image refinement.
    08-Oct-2026: Removed LangSmith tracing.
    13-Oct-2026: Guest contact/userprofile ids route to the guest agent without memory or the classifier.
    27-Oct-2026: Assistant metadata records awaiting_clarification; added POST /processing_texts; history loads the newest 50 messages; an every-match-already-shown search reply is relayed verbatim.

Notes:
    Supports both member and provider agent types based on request context.
    Integrates with Mem0ai for conversation memory and Azure OpenAI for LLM.
"""
import base64
import logging
import uuid
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, status
from datetime import datetime
import json
import asyncio
import time
from typing import Optional


from app.utils.blob_storage import (
    current_timestamp_prefix,
    detect_mime_type,
    normalize_container_name,
    to_bytes,
    upload_blob_bytes,
)

from app.agent_infrastructure.agents.etown_agent import (
    etown_guest_chaatie_agent,
    etown_member_chaatie_agent,
    etown_provider_chaatie_agent,
)
from app.core.security import verify_token
from app.etown.conversations.conversation_utils import Database
from app.etown.plans.plans_utils import (
    PlanLimitError,
    check_and_consume,
    premium_display,
    release_consumed,
    resolve_plan,
    resolve_provider_badges,
)
from app.schemas.api import ChaatieProcessingTextsRequest, ChaatieProcessingTextsResponse, ChaatieRequest
from app.etown.chat.chat_utils import get_processing_texts
from app.services.api_activity import track_api_call
from app.core.config import settings
from app.core.memory import (
    get_memory_client,
    MEMORY_SEARCH_LIMIT,
    MEMORY_SEARCH_TIMEOUT_SECONDS,
    MEMORY_SIMILARITY_FLOOR,
)
from app.etown.memory.memory_utils import (
    build_candidate_log,
    fetch_provider_affinity,
    format_affinity_for_prompt,
    format_memories_for_prompt,
    is_memory_enabled,
    log_memory_decision,
    match_affinity_mention,
    record_extraction_outcome,
    score_memories,
)
from app.agent_infrastructure.tools.tools_utils.search_helpers import normalize_search_text
from app.etown.search.search_appearance_utils import log_provider_search_appearances
from app.utils.ist_clock import format_ist_for_prompt
from app.utils.translation_utils import OutputTranslator
from app.agent_infrastructure.tools.context_manager import set_execution_context, clear_execution_context
from app.agent_infrastructure.classification.bridge import classify_and_maybe_handle
from app.utils.performance import PerformanceTimer, PerformanceCollector

logger = logging.getLogger(__name__)

router = APIRouter()

_SEARCH_TERM_TOOLS = {"search_provider", "recommend_products", "booknow_provider"}
_LISTING_RESPONSE_TYPES = {"search_provider", "recommend_products", "booknow_provider"}

_MEMORY_TOOLS = {"remember_preference", "forget_preference"}
_ALL_ALREADY_SHOWN_NOTE = "[EVERY MATCH WAS ALREADY SHOWN"


def _merge_listing_rows(*payloads) -> list:
    """Merge bookable rows returned by a turn without duplicating providers."""
    merged = []
    seen = set()
    for payload in payloads:
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = item.get("providerservicesid") or item.get("id")
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            merged.append(item)
    return merged


def _strip_model_notes(text: str) -> str:
    paragraphs = text.split("\n\n")
    while paragraphs and paragraphs[0].strip().startswith("[") and paragraphs[0].strip().endswith("]"):
        paragraphs.pop(0)
    return "\n\n".join(paragraphs).strip()


def _single_search_reply(messages: list) -> Optional[str]:
    turn_start = 0
    for index, msg in enumerate(messages):
        if getattr(msg, "type", None) == "human":
            turn_start = index + 1
    turn = messages[turn_start:]

    calls = [
        tool_call
        for msg in turn
        for tool_call in (getattr(msg, "tool_calls", None) or [])
    ]
    if len(calls) != 1 or calls[0].get("name") != "search_provider":
        return None

    call_id = calls[0].get("id")
    for msg in turn:
        if getattr(msg, "type", None) == "tool" and getattr(msg, "tool_call_id", None) == call_id:
            content = msg.content if isinstance(msg.content, str) else ""
            return _strip_model_notes(content) or None
    return None


async def background_add_memory(
    messages_to_store: list, user_id: str, metadata: dict, turn_id: str = None
):
    """Add the user's turn to mem0 in a worker thread and record the extraction outcome for the turn."""
    events = []
    outcome = "error"
    try:
        result = await asyncio.to_thread(
            get_memory_client().add,
            messages=messages_to_store,
            user_id=user_id,
            metadata=metadata,
        )
        events = result.get("results", []) if isinstance(result, dict) else []
        outcome = "ok"
        logger.info(
            f"Memory add for user {user_id}: {len(events)} change(s) "
            f"{[e.get('event') for e in events if isinstance(e, dict)]}"
        )
    except Exception as e:
        logger.error(f"Background memory addition failed: {str(e)}", exc_info=True)

    if turn_id:
        facts = [
            e.get("memory") for e in events
            if isinstance(e, dict) and e.get("memory")
        ]
        await record_extraction_outcome(turn_id, outcome, facts, events)


def _sync_memory_search(query: str, user_id: str):
    """Search mem0 for a user's memories without a threshold, returning an empty dict on failure."""
    try:
        memory_client = get_memory_client()
        return memory_client.search(
            query=query,
            user_id=user_id,
            limit=MEMORY_SEARCH_LIMIT,
        )
    except Exception as mem_err:
        logger.error(f"Memory retrieval failed: {str(mem_err)}", exc_info=True)
        return {}


@router.post("/chaatie")
async def chat(
            request: ChaatieRequest,
            background_tasks: BackgroundTasks,
            token: str = Depends(verify_token)
            ):
    """Handle POST /api/chat/chaatie: run one Chaatie conversation turn."""
    return await chaatie_turn(request, background_tasks)


@router.post("/processing_texts", response_model=ChaatieProcessingTextsResponse)
async def processing_texts(
    request: ChaatieProcessingTextsRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/chat/processing_texts: return status lines matching the prompt for the app to show while /chaatie runs."""
    return await get_processing_texts(
        prompt=request.prompt,
        user_role=request.user_role,
        language=request.language,
        conversation_id=request.conversation_id,
        has_image=request.has_image,
    )


async def chaatie_turn(request: ChaatieRequest, background_tasks: BackgroundTasks):
    """Run a Chaatie turn: charge the plan, load context, classify or invoke the role agent, translate and save the reply."""

    collector = PerformanceCollector("chat_chaatie")
    collector.start_timing()

    track_api_call(
        background_tasks, "/api/chat/chaatie", request.contact_id, request.user_profile_id
    )

    try:
        conv_id = request.conversation_id or str(uuid.uuid4())
        turn_id = str(uuid.uuid4())
        user_id = request.user_profile_id
        if not user_id:
             user_id = request.contact_id
        user_name = request.first_name or "User"
        is_guest = bool(
            (settings.GUEST_CONTACT_ID and request.contact_id == settings.GUEST_CONTACT_ID)
            or (settings.GUEST_USER_PROFILE_ID and request.user_profile_id == settings.GUEST_USER_PROFILE_ID)
        )

        plan_bound = await resolve_plan(request.user_profile_id) is not None
        if plan_bound:
            try:
                await check_and_consume(request.user_profile_id, "chattie_conversation")
            except PlanLimitError as plan_err:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=(
                        f"{plan_err} You've used every Chaatie message your plan allows "
                        "today. Your allowance resets tomorrow, or you can upgrade your "
                        "plan for more."
                    ),
                )

        image_blob_url = None
        image_data_url = None
        if request.image_prompt:
            async with PerformanceTimer("chat_image_upload", category="external_api"):
                try:
                    image_bytes = to_bytes(request.image_prompt)
                    mime = detect_mime_type(image_bytes) if image_bytes else ""
                    if not image_bytes:
                        logger.warning(f"image_prompt decoded to empty bytes for {user_id}")
                    elif not mime.startswith("image/"):
                        logger.warning(
                            f"image_prompt for {user_id} is not an image ({mime}, "
                            f"{len(image_bytes)} bytes); ignoring it for this turn"
                        )
                    else:
                        image_data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
                        container = normalize_container_name(settings.AZURE_BLOB_CONTAINER)
                        blob_name = f"{user_id}/{current_timestamp_prefix()}_ChaatiePrompt.png"
                        image_blob_url = await upload_blob_bytes(
                            container, blob_name, image_bytes, mime
                        )
                        logger.info(f"Chaatie image stored for {user_id}: {image_blob_url}")
                except Exception as img_err:
                    logger.error(f"Failed to store Chaatie image: {img_err}", exc_info=True)

        async def task_history():
            async with PerformanceTimer("history_loading", category="database"):
                try:
                    return await Database.get_recent_conversation_history(conv_id, limit=50)
                except Exception as e:
                    logger.error(f"Error loading conversation history: {str(e)}", exc_info=True)
                    return []

        async def task_save_message():
            async with PerformanceTimer("save_user_message", category="database"):
                return await Database.save_conversation_message(
                    conversation_id=conv_id,
                    user_id=user_id,
                    role="user",
                    content=request.prompt,
                    user_name=user_name,
                    user_address=request.address or "",
                    language=request.language or "en",
                    metadata={"image_url": image_blob_url} if image_blob_url else None,
                )

        async def task_memory_retrieval():
            """Return (raw_result, outcome, elapsed_ms) so the decision log can tell a timeout
            from an empty corpus -- both used to look identical, which is why 30 days of logs
            could not answer whether recall had ever worked."""
            if is_guest:
                return {}, "disabled", 0
            async with PerformanceTimer("memory_retrieval", category="external_api"):
                started = time.perf_counter()
                try:
                    raw = await asyncio.wait_for(
                        asyncio.to_thread(_sync_memory_search, request.prompt, user_id),
                        timeout=MEMORY_SEARCH_TIMEOUT_SECONDS
                    )
                    return raw, "ok", int((time.perf_counter() - started) * 1000)
                except asyncio.TimeoutError:
                    logger.warning(f"Memory retrieval timed out for user {user_id}. Proceeding without memory context.")
                    return {}, "timeout", int((time.perf_counter() - started) * 1000)
                except Exception as e:
                    logger.error(f"Memory retrieval failed: {str(e)}", exc_info=True)
                    return {}, "error", int((time.perf_counter() - started) * 1000)

        async def task_provider_affinity():
            if is_guest:
                return []
            async with PerformanceTimer("provider_affinity", category="database"):
                try:
                    return await fetch_provider_affinity(
                        request.contact_id or "", request.user_profile_id or ""
                    )
                except Exception as e:
                    logger.error(f"Provider affinity lookup failed: {str(e)}", exc_info=True)
                    return []

        new_conversation = plan_bound and await Database.claim_conversation_charge(
            conv_id, request.user_profile_id
        )
        if new_conversation:
            try:
                await check_and_consume(request.user_profile_id, "chaatie_generations")
            except PlanLimitError as plan_err:
                await Database.release_conversation_charge(conv_id, request.user_profile_id)
                await release_consumed(request.user_profile_id, "chattie_conversation")
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=(
                        f"{plan_err} You've started all the Chaatie conversations your plan "
                        "allows this month. Upgrade your plan to start more, or continue one "
                        "of your existing conversations."
                    ),
                )

        async def task_memory_enabled():
            if is_guest:
                return False
            try:
                return await is_memory_enabled(user_id)
            except Exception as e:
                logger.error(f"Memory switch lookup failed: {str(e)}", exc_info=True)
                return True
        (
            history_records,
            saved_message_id,
            retrieval_result,
            affinity_rows,
            memory_enabled,
        ) = await asyncio.gather(
            task_history(),
            task_save_message(),
            task_memory_retrieval(),
            task_provider_affinity(),
            task_memory_enabled()
        )

        relevant_memories, retrieval_outcome, retrieval_ms = retrieval_result
        if not memory_enabled:
            relevant_memories, retrieval_outcome = {}, "disabled"

        if saved_message_id is None and plan_bound:
            await release_consumed(request.user_profile_id, "chattie_conversation")
            if new_conversation:
                await release_consumed(request.user_profile_id, "chaatie_generations")
                await Database.release_conversation_charge(conv_id, request.user_profile_id)

        history = []
        has_checked_ratings = False
        shown_provider_ids: set = set()
        shown_ids_by_term: dict = {}
        restored_providers: list = []
        restored_image = None
        for record in history_records:
            content = record['content']
            if record['role'] == 'assistant' and record.get('metadata'):
                try:
                    meta = record['metadata'] if isinstance(record['metadata'], dict) else json.loads(record['metadata'])
                    implicit = meta.get('implicit_output')
                    if meta.get('response_type') == 'generate_image' and isinstance(implicit, dict):
                        restored_image = {
                            key: implicit.get(key)
                            for key in ("image_url", "blob_path", "image_type", "original_prompt", "enhanced_prompt", "seed", "design")
                        }
                    record_terms = [
                        normalize_search_text(term)
                        for term in (meta.get('search_terms') or [])
                        if isinstance(term, str) and term.strip()
                    ]
                    if (
                        meta.get('empty_listing') in _LISTING_RESPONSE_TYPES
                        or (
                            isinstance(implicit, list)
                            and not implicit
                            and meta.get('response_type') in _LISTING_RESPONSE_TYPES
                        )
                    ):
                        restored_providers = []
                    if implicit and isinstance(implicit, list):
                        if meta.get('response_type') in _LISTING_RESPONSE_TYPES:
                            bookable = [
                                item for item in implicit
                                if isinstance(item, dict)
                                and (item.get('providerservicesid') or item.get('id'))
                            ]
                            if bookable:
                                restored_providers = bookable
                        provider_refs = []
                        for item in implicit:
                            if isinstance(item, dict):
                                provider_id = item.get('id') or item.get('providerservicesid')
                                contact_id_val = item.get('contactid')
                                if provider_id:
                                    shown_provider_ids.add(str(provider_id))
                                    for term in record_terms:
                                        shown_ids_by_term.setdefault(term, set()).add(str(provider_id))
                                    name = item.get('name') or item.get('business_name') or item.get('businessname', '')
                                    details = item.get('details') or {}
                                    google_rating = details.get('google_rating')
                                    google_rating_count = details.get('google_rating_count')
                                    overall_rating = item.get('overallrating')
                                    rating_count = item.get('ratingcount')
                                    if google_rating:
                                        rating_note = f", google_rating: {google_rating} ({google_rating_count or 0} reviews)"
                                    elif overall_rating:
                                        rating_note = f", etown_rating: {overall_rating} ({rating_count or 0} reviews)"
                                    else:
                                        rating_note = ", rating: not available"
                                    provider_refs.append(f"providerservicesid: {provider_id} ({name}{rating_note})")
                                if contact_id_val:
                                    shown_provider_ids.add(str(contact_id_val))
                                    for term in record_terms:
                                        shown_ids_by_term.setdefault(term, set()).add(str(contact_id_val))
                                for sub in item.get('subservices', []) or []:
                                    if isinstance(sub, dict):
                                        sub_id = sub.get('id') or sub.get('providerservicesid')
                                        if sub_id:
                                            sub_name = sub.get('name') or sub.get('services', '')
                                            provider_refs.append(f"providerservicesid: {sub_id} ({sub_name})")
                        if provider_refs:
                            content += "\n\n[Provider IDs from results: " + ", ".join(provider_refs) + "]"
                        searched = [
                            term.strip() for term in (meta.get('search_terms') or [])
                            if isinstance(term, str) and term.strip()
                        ]
                        if searched and provider_refs:
                            content += "\n\n[Searched for: " + ", ".join(searched) + "]"
                except Exception:
                    logger.debug("chat: could not append provider IDs from tool results to the prompt", exc_info=True)
            
            history.append({
                "role": record['role'],
                "content": content
            })
            val = record.get('promptratings')
            if val: 
                has_checked_ratings = True
                logger.info(f"Found checked rating flag in history msg {record.get('id')}: {val}")
        logger.info(f"Loaded {len(history)} previous messages. Ratings checked previously: {has_checked_ratings}. Previously shown providers: {len(shown_provider_ids)}")

        if image_data_url:
            user_turn_content = [
                {"type": "text", "text": request.prompt or "Please look at this image and help me."},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
        else:
            user_turn_content = request.prompt
        messages = history + [{"role": "user", "content": user_turn_content}]

        scored_memories = score_memories(relevant_memories)
        memory_text = format_memories_for_prompt(scored_memories)
        matched_provider = match_affinity_mention(request.prompt, affinity_rows)
        affinity_text = format_affinity_for_prompt(affinity_rows, matched_provider)
        candidates = build_candidate_log(relevant_memories)
        logger.info(
            f"Memory context for {user_id}: {len(scored_memories)} of "
            f"{len(candidates)} memories cleared the floor"
            + (f" (best {scored_memories[0]['similarity']:.2f})" if scored_memories else "")
            + f"; {len(affinity_rows)} previously-booked provider(s)"
            + (f"; resolved mention -> {matched_provider['businessname']}" if matched_provider else "")
        )

        background_tasks.add_task(
            log_memory_decision,
            {
                "turn_id": turn_id,
                "conversation_id": conv_id,
                "user_id": user_id,
                "user_role": request.user_role,
                "raw_prompt": request.prompt,
                "memory_enabled": memory_enabled,
                "retrieval_outcome": retrieval_outcome,
                "retrieved_count": len(candidates),
                "injected_count": len(scored_memories),
                "best_similarity": candidates[0]["similarity"] if candidates else None,
                "similarity_floor": round(MEMORY_SIMILARITY_FLOOR, 4),
                "candidates": candidates,
                "retrieval_ms": retrieval_ms,
                "affinity_count": len(affinity_rows),
                "affinity_matched": matched_provider.get("businessname") if matched_provider else None,
                "affinity_score": matched_provider.get("matchscore") if matched_provider else None,
            },
        )

        context = {
            "contact_id": request.contact_id,
            "user_profile_id": request.user_profile_id,
            "conversation_id": conv_id,
            "user_name": request.first_name or "User",
            "user_role": request.user_role,
            "is_guest": is_guest,
            "language": request.language or "en",
            "address": request.address or "",
            "user_latitude": request.latitude,
            "user_longitude": request.longitude,
            "time": format_ist_for_prompt(),
            "prompt_ratings_checked": has_checked_ratings,
            "memories": memory_text,
            "provider_affinity": affinity_text,
            "shown_provider_ids": list(shown_provider_ids),
            "shown_provider_ids_by_term": {
                term: list(ids) for term, ids in shown_ids_by_term.items()
            }
        }

        config = {
                "recursion_limit": settings.LANGGRAPH_RECURSION_LIMIT,
                "configurable": context
            }

        state = {
            "messages": messages,
            "implicit_output": None,
            "response_type": None,
            "contact_id": request.contact_id,
            "user_profile_id": request.user_profile_id,
            "conversation_id": conv_id,
            "user_name": request.first_name or "User",
            "user_role": request.user_role,
            "language": request.language or "en",
            "address": request.address or "",
            "latitude": request.latitude,
            "longitude": request.longitude,
            "user_latitude": request.latitude,
            "user_longitude": request.longitude,
            "mobilenumber_accesstoken": request.mobilenumber_accesstoken,
            "oauth_token": request.oauth_token,
            "time": format_ist_for_prompt(),
            "prompt_ratings_checked": has_checked_ratings,
            "shown_provider_ids": list(shown_provider_ids),
            "shown_provider_ids_by_term": {
                term: list(ids) for term, ids in shown_ids_by_term.items()
            },
            "selected_providers": restored_providers,
            "restored_generated_image": restored_image,
        }

        set_execution_context(state)
        response = None

        if settings.CLASSIFIER_MODE != "off" and not is_guest:
            try:
                bridge_response = await classify_and_maybe_handle(request, state, history_records)
            except Exception:
                logger.exception("Classification bridge failed; falling through to LLM")
                bridge_response = None
            if bridge_response is not None:
                response = bridge_response

        async with PerformanceTimer("agent_invocation", category="external_api"):
            try:
                if response is None:
                    if is_guest:
                        response = await etown_guest_chaatie_agent.ainvoke(
                            input=state,
                            config=config,
                            context=context
                        )
                    elif request.user_role == 2:
                        response = await etown_member_chaatie_agent.ainvoke(
                            input=state,
                            config=config,
                            context=context

                        )
                    elif request.user_role == 1:
                        response = await etown_provider_chaatie_agent.ainvoke(
                            input=state,
                            config=config,
                            context=context
                        )
                    else:
                         response = {"messages": []}
            finally:
                clear_execution_context()

        response_messages = response.get("messages", []) if response else []
        assistant_response = response_messages[-1].content if response_messages else "No response generated"
        
        tools_used = []
        turn_search_terms = []
        final_implicit_output = response.get("implicit_output") if response else None
        final_response_type = response.get("response_type") if response else None
        awaiting_clarification = final_response_type == "clarification"
        if final_response_type in _LISTING_RESPONSE_TYPES:
            final_implicit_output = _merge_listing_rows(
                final_implicit_output,
                response.get("selected_providers") if response else None,
            )
        promptratings_flag = False
        if final_response_type == "pending_rating":
            promptratings_flag = True

        for msg in response_messages:
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    tool_name = tool_call.get('name', 'unknown_tool')
                    tools_used.append(tool_name)
                    if tool_name in _SEARCH_TERM_TOOLS:
                        searched = (tool_call.get('args') or {}).get('search_term')
                        if isinstance(searched, str) and searched.strip():
                            turn_search_terms.append(searched.strip())
                    if tool_name in ['pending_ratings', 'check_pending_ratings']:
                        promptratings_flag = True
            is_tool_message = (getattr(msg, 'type', None) == 'tool') or (getattr(msg, 'role', None) == 'tool')
            if is_tool_message and msg.name in ['pending_ratings', 'check_pending_ratings']:
                try:
                    tool_output = json.loads(msg.content)
                    if tool_output.get('status') == 'pending_rating':
                        final_response_type = "pending_rating"
                        if not final_implicit_output:
                            final_implicit_output = {
                                "pending_rating_details": tool_output,
                                "response_type": "pending_rating"
                            }
                        promptratings_flag = True 
                    elif tool_output.get('status') == 'no_pending_ratings':
                        promptratings_flag = True 
                except Exception:
                    logger.debug("chat: pending-ratings tool output was unreadable; suppressing the ratings prompt", exc_info=True)
                    promptratings_flag = True
        
        async with PerformanceTimer("memory_addition_dispatch", category="general"):
            try:
                explicit_memory_tools = _MEMORY_TOOLS.intersection(tools_used)
                if not memory_enabled:
                    logger.info(f"Memory is off for user {user_id}; not storing this turn")
                elif explicit_memory_tools:
                    logger.info(
                        f"Skipping automatic extraction for {user_id}: the turn already stored "
                        f"its fact through {sorted(explicit_memory_tools)}"
                    )
                    background_tasks.add_task(
                        record_extraction_outcome, turn_id, "skipped_explicit"
                    )
                else:
                    messages_to_store = [{"role": "user", "content": request.prompt}]

                    metadata = {
                        "user_id": user_id,
                        "user_name": user_name,
                        "conversation_id": conv_id,
                        "language": request.language or "en",
                        "timestamp": datetime.now().isoformat()
                    }

                    background_tasks.add_task(
                        background_add_memory,
                        messages_to_store,
                        user_id,
                        metadata,
                        turn_id,
                    )

            except Exception as mem_err:
                logger.error(f"Failed to dispatch background memory task: {str(mem_err)}", exc_info=True)

        if final_response_type and final_response_type != "pending_rating":
            if final_response_type not in tools_used:
                logger.info(f"Cleaning up response_type '{final_response_type}' because it was not in tools_used: {tools_used}")
                final_response_type = None
                final_implicit_output = None
        emptied_listing_type = None
        if final_response_type in _LISTING_RESPONSE_TYPES and not final_implicit_output:
            logger.info(
                f"Clearing response_type '{final_response_type}' because the turn listed no rows."
            )
            emptied_listing_type = final_response_type
            final_response_type = None
            final_implicit_output = None
        if emptied_listing_type == "search_provider":
            tool_texts = [
                msg.content for msg in response_messages
                if getattr(msg, "type", None) == "tool" and isinstance(msg.content, str)
            ]
            relayed = _single_search_reply(response_messages)
            if relayed and tool_texts and tool_texts[-1].lstrip().startswith(_ALL_ALREADY_SHOWN_NOTE):
                logger.info("Relaying the every-match-already-shown reply in place of the model's rewrite")
                assistant_response = relayed
        if final_response_type == "search_provider":
            relayed = _single_search_reply(response_messages)
            if relayed and relayed != assistant_response:
                logger.info("Relaying the single search_provider reply in place of the model's rewrite")
                assistant_response = relayed
        if final_response_type == "search_provider" and assistant_response:
            booking_indicators = [
                "date and time",
                "time and date",
            ]
            presented = final_implicit_output if isinstance(final_implicit_output, list) else []
            lower_response = assistant_response.lower()
            if len(presented) <= 1 and any(indicator in lower_response for indicator in booking_indicators):
                logger.info("Suppressing search_provider widget because Agent is asking for booking details.")
                final_response_type = None
                final_implicit_output = None
        
        invitation_status = None
        if final_response_type == "internal_invite" and isinstance(final_implicit_output, dict):
            raw_invitation_code = (
                final_implicit_output.get("invitationcode")
                or final_implicit_output.get("Invitationcode")
            )
            if raw_invitation_code in ("Requested", "Cancelled", "Accepted"):
                invitation_status = raw_invitation_code

        user_language = request.language or "en"
        
        async with PerformanceTimer("output_translation", category="external_api"):
            if user_language != "en" and assistant_response:
                try:
                    logger.info(f"Translating response from English to {user_language}")
                    translated_response = await OutputTranslator.translate_response(
                        assistant_response, 
                        user_language
                    )
                    if translated_response and translated_response != assistant_response:
                        logger.info(f"Response translated successfully")
                        assistant_response = translated_response
                    else:
                        logger.info(f"No translation needed or translation failed, using original")
                except Exception as translate_error:
                    logger.error(f"Response translation failed: {str(translate_error)}", exc_info=True)

        if isinstance(final_implicit_output, list):
            provider_items = [
                item for item in final_implicit_output
                if isinstance(item, dict) and item.get("userprofileid")
            ]
            if provider_items:
                badge_map = await resolve_provider_badges(
                    item["userprofileid"] for item in provider_items
                )
                for item in provider_items:
                    badge = badge_map.get(item["userprofileid"]) or {}
                    item["provider_type"] = badge.get("provider_type") or item.get("provider_type")
                    item["badge_label"] = badge.get("badge_label")
                    if "providertype" in item:
                        item["providertype"] = premium_display(item["providertype"], badge.get("premium_label"))

        async with PerformanceTimer("save_assistant_message", category="database"):
            await Database.save_conversation_message(
                conversation_id=conv_id,
                user_id=user_id,
                role="assistant",
                content=assistant_response,
                user_name=user_name,
                user_address=request.address or "",
                language=request.language or "en",
                tools_used=tools_used,
                promptratings=promptratings_flag,
                metadata={
                    "implicit_output": final_implicit_output,
                    "response_type": final_response_type,
                    "search_terms": turn_search_terms,
                    "empty_listing": emptied_listing_type,
                    "awaiting_clarification": awaiting_clarification,
                }
            )
        if turn_search_terms and _SEARCH_TERM_TOOLS.intersection(tools_used):
            background_tasks.add_task(
                log_provider_search_appearances,
                final_implicit_output,
                turn_search_terms[0],
                request.contact_id,
                "chaatie",
            )
        
        return {
            "response": assistant_response,
            "implicit_output": final_implicit_output,
            "response_type": final_response_type,
            "invitation_status": invitation_status,
            "conversation_id": conv_id,
            "tools_used": tools_used,
            "timestamp": datetime.now().isoformat(),
            "performance": collector.get_metrics()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing chat request: {str(e)}", exc_info=True)
        
        error_message = "Sorry, an error occurred while processing your request."
        user_language = getattr(request, 'language', 'en') if 'request' in locals() else 'en'
        
        if user_language != "en":
            try:
                translated_error = await OutputTranslator.translate_response(
                    error_message, 
                    user_language
                )
                if translated_error:
                    error_message = translated_error
            except Exception as translate_error:
                logger.error(f"Error message translation failed: {str(translate_error)}", exc_info=True)
        
        return {
            "response": error_message,
            "implicit_output": None,
            "response_type": None,
            "invitation_status": None,
            "conversation_id": getattr(request, 'conversation_id', None) if 'request' in locals() else None,
            "performance": collector.get_metrics() if 'collector' in locals() else None
        }
