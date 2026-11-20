"""Agent Tools Registry - LangGraph tool definitions and the member, provider, guest and automation tool lists.

Agent layer for Chaatie: defines every @tool, gates calls behind the pending-rating guardrail, reads caller identity from the execution context and delegates to the tools_utils handlers, then exposes the member_tools, provider_tools, guest_tools and automation_tools registries.

Features:
    - Tool Registration: Central registry for all agent-accessible tools
    - Context Management: User context propagation across tool executions
    - Safety Guardrails: Pending ratings checks and execution blocking
    - Schema Validation: Pydantic-based input/output validation for all tools
    - Business Logic Integration: Connects agents to service provider search, booking, and management
    - Multi-agent Support: Separate tool sets for member and provider agents
    - Error Handling: Structured error responses and logging
    - Performance Tracking: Execution timing and metrics collection
    - Enhanced Logging: Comprehensive business operation and agent decision logging
    - Agent Interaction Tracking: Automatic logging of agent decisions, tool usage, and business context
    - Business Operation Monitoring: Automatic capture of provider searches, bookings, and requests
    - Performance Metrics: Database query monitoring and external API call tracking

Classes:
    No classes defined (member_tools / provider_tools / guest_tools / automation_tools are lists of @tool objects)

Functions:
    _check_pending_rating_guardrail(): Blocks a tool call while a completed booking awaits a rating
    _build_pending_rating_command(): Builds the Command that surfaces or re-blocks a pending rating
    _get_last_user_message(): Returns the raw text of the most recent human turn from agent state
    search_provider(): Searches for businesses, services, or products on the Etown platform
    recommend_products(): Recommends related/complementary catalog products for a search term
    request_provider(): Creates one or more service/product booking requests after confirmation
    add_booking_instructions(): Appends user-supplied instructions to an already-placed booking
    enquire_provider(): Sends a member's free-text enquiry to a provider's chat thread (no booking)
    booknow_provider(): Looks up one specific business by name for the immediate "book now" flow
    provider_service_insights(): Retrieves a known provider's profile, services, contact and distance
    address_confirmation(): Validates and confirms the user's address for service delivery
    generate_qr(): Generates a QR code for the user
    create_post(): Opens the app's own post composer (UI trigger only — publishes nothing)
    booking_info(): Retrieves the real status/details of one of the user's own bookings
    cancel_booking(): Cancels one of the user's own bookings after confirmation
    connect_support_agent(): Files a support ticket and returns its reference to the user
    get_provider_requests_list(): Fetches the booking requests a provider has received
    get_member_requests_list(): Fetches a member's own booking/request history
    provide_etown_info(): Answers platform questions from the Etown knowledge base
    internal_invite(): Searches for and invites existing Etown users (two-step search/send)
    external_invite_for_members(): Sends an email/SMS invite to someone not yet on Etown (member)
    external_invite_for_providers(): Sends an email/SMS invite to someone not yet on Etown (provider)
    pending_ratings(): Checks for completed bookings still awaiting the user's rating
    web_search(): Retrieves real-time information using Gemini Flash web search
    _handle_onboard_provider(): Inserts a new provider and starts background AI profile enhancements
    onboard_provider(): Registers a new business/provider on Etown from conversation
    aionboarding_redirection(): Sends Etown staff asking to onboard a provider to the AI onboarding screen
    _resolve_attached_image(): Resolves the image (explicit URL or last generated) for an offer/post
    create_offer_from_chat(): Drafts/publishes a provider's promotional offer to their Circle
    create_post_from_chat(): Drafts/publishes a feed post to the user's Circle
    generate_image(): Generates an AI image from the user's description via the image pipeline
    set_profile_image_from_chat(): Sets the last generated image as the provider's business profile image
    remember_preference(): Stores a fact the user explicitly asked Chaatie to remember
    forget_preference(): Deletes a stored fact the user asked Chaatie to forget
    guestuser(): Tells a guest user a requested feature needs a signed-in account
    query_my_data(): Answers counting and ranking questions about the caller's own data from curated views

Dependencies:
    - langchain / langchain_core: @tool decoration, RunnableConfig, InjectedToolCallId, ToolMessage
    - langgraph: InjectedState and Command for state injection and control-flow updates
    - app.logs.enhanced_logging: business_operation, api_call_monitor and log_agent_decision observability
    - app.agent_infrastructure.tools.context_manager: get_context_value, get_execution_context
    - app.agent_infrastructure.tools.tools_utils.*: business-logic handlers backing each tool
    - app.schemas.langraph_tool_states: Pydantic args_schema classes for each tool's arguments
    - app.etown.provider.provider_utils.ProviderService: onboarding and background enhancement pipeline
    - app.db.client.Database: direct DB calls (e.g. provider-view tracking in search_provider)

Updates:
    27-Feb-2026: Rewrote the module documentation.
    11-Mar-2026: Converted function docstrings to Google style.
    12-Mar-2026: Added search_scope to internal_invite; added web_search tool via Gemini Flash.
    13-Mar-2026: Added business operation, agent interaction and performance logging.
    23-Jun-2026: Added create_post_from_chat tool - DB-backed feed post creation for both roles.
    20-Jul-2026: Added recommend_products tool (member) - product-catalog hybrid search.
    06-Aug-2026: Added enquire_provider tool (member) - the Enquire branch of the off-catalog flow.
    17-Aug-2026: Narrowed create_post's description to avoid create_post_from_chat.
    24-Aug-2026: connect_support_agent is async and files a real support ticket.
    26-Aug-2026: Guardrail trace moved to debug; location log names its coordinates.
    07-Sep-2026: Added remember_preference and forget_preference tools.
    08-Sep-2026: web_search reports response_type 'web_search'; onboard_provider passes plan_code to run_background_enhancements.
    17-Sep-2026: search_provider's view tracking stamps source='chaatie_search'.
    28-Sep-2026: Added set_profile_image_from_chat tool (provider) and aionboarding_redirection tool (both roles).
    29-Sep-2026: generate_image refines the previous image, passes provider business context, stores non-logos as JPEG.
    30-Sep-2026: generate_image passes provider business context again and reports the exact typeset text.
    13-Oct-2026: Added guestuser tool and the guest_tools registry.
    16-Oct-2026: create_offer_from_chat and create_post_from_chat attach an image generated in an earlier turn.
    27-Oct-2026: Added query_my_data tool, registered for members and providers unless DATA_QUERY_MODE is off.
"""

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools.base import InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from langchain_core.messages import ToolMessage
from typing import Annotated, List, Optional, Literal, Union
import logging
import json
import asyncio

from app.logs.enhanced_logging import (
    business_operation, api_call_monitor, log_agent_decision
)


from app.agent_infrastructure.tools.context_manager import get_context_value, get_execution_context
from app.agent_infrastructure.tools.tools_utils.search_helpers import shown_ids_for_search
from app.agent_infrastructure.tools.tools_utils.automation_tester import automation_tester
from app.agent_infrastructure.tools.tools_utils.create_post import handle_create_post
from app.agent_infrastructure.tools.tools_utils.create_post_from_chat import handle_create_post_from_chat
from app.agent_infrastructure.tools.tools_utils.post_offer import handle_create_offer_from_chat
from app.agent_infrastructure.tools.tools_utils.generate_qr import handle_generate_qr
from app.agent_infrastructure.tools.tools_utils.generate_image import handle_generate_image
from app.agent_infrastructure.tools.tools_utils.set_profile_image import handle_set_profile_image_from_chat
from app.agent_infrastructure.tools.tools_utils.address_confirmation import handle_address_confirmation
from app.agent_infrastructure.tools.tools_utils.booking_info import handle_booking_info
from app.agent_infrastructure.tools.tools_utils.add_booking_instructions import handle_add_booking_instructions
from app.agent_infrastructure.tools.tools_utils.search_provider import (
    handle_search_provider,
    _requests_closest_provider,
    _requests_date_and_time,
    _requests_weekend_choice,
    _named_day,
)
from app.agent_infrastructure.tools.tools_utils.recommend_products import handle_recommend_products
from app.agent_infrastructure.tools.tools_utils.customer_support import handle_connect_support_agent
from app.agent_infrastructure.tools.tools_utils.provider_request_list import handle_provider_request_getlist
from app.agent_infrastructure.tools.tools_utils.member_request_list import handle_member_request_getlist
from app.agent_infrastructure.tools.tools_utils.etown_info import handle_provide_etown_info
from app.agent_infrastructure.tools.tools_utils.external_invite_for_members import (
    handle_external_email_invite,
    handle_external_mobile_invite
)
from app.agent_infrastructure.tools.tools_utils.external_invite_for_providers import (
    handle_external_email_invite_provider,
    handle_external_mobile_invite_provider
)
from app.agent_infrastructure.tools.tools_utils.internal_invite import handle_internal_invite
from app.agent_infrastructure.tools.tools_utils.web_search_tool import handle_web_search
from app.agent_infrastructure.tools.tools_utils.aionboarding_redirection import handle_aionboarding_redirection
from app.etown.provider.provider_utils import ProviderService
from app.db.client import Database

from app.agent_infrastructure.tools.tools_utils.request_provider import handle_request_provider
from app.agent_infrastructure.tools.tools_utils.booknow_provider import handle_booknow_provider
from app.agent_infrastructure.tools.tools_utils.provider_service_insights import handle_provider_service_insights
from app.agent_infrastructure.tools.tools_utils.enquire_provider import handle_enquire_provider
from app.agent_infrastructure.tools.tools_utils.pending_ratings import check_pending_ratings

from app.agent_infrastructure.tools.tools_utils.cancel_booking import handle_cancel_booking
from app.agent_infrastructure.tools.tools_utils.remember_preference import (
    handle_forget_preference,
    handle_remember_preference
)

from app.schemas.langraph_tool_states import (
    BookingInfo,
    CancelBooking,
    SearchProvider,
    RecommendProducts,
    RequestProvider,
    BookNowProvider,
    ProviderServiceInsights,
    ConnectSupportAgent,
    GetProviderRequestList,
    GetMemberRequestList,
    ProvideEtownInfo,
    InternalInvite,
    WebSearch,
    ExternalInviteMembers,
    OnboardProvider,
    ExternalInviteProviders,
    CreateOfferFromChat,
    CreatePostFromChat,
    GenerateImage,
    SetProfileImageFromChat,
    AddBookingInstructions,
    EnquireProvider,
    RememberPreference,
    ForgetPreference,
    QueryMyData
)
from app.agent_infrastructure.tools.tools_utils.query_my_data import handle_query_my_data
from app.core.config import settings

logger = logging.getLogger(__name__)
_rating_guardrail_lock = asyncio.Lock()
_RATING_GUARDRAIL_CACHE_KEY = "_pending_rating_guardrail"

async def _check_pending_rating_guardrail(
    user_profile_id: str, 
    config: Optional[RunnableConfig], 
    state: Optional[dict], 
    tool_call_id: str
) -> Optional[Command]:
    """Return a blocking Command when the user has a pending rating, checking the DB at most once per turn.

    Args:
        user_profile_id: Profile to check for pending ratings.
        config: LangGraph run config carrying context values.
        state: Current agent state.
        tool_call_id: Id of the tool call being guarded.

    Returns:
        A pending-rating Command, or None when execution may proceed.
    """
    if not user_profile_id:
        return None

    val_from_ctx = get_context_value("prompt_ratings_checked", None, config, state)
    
    val_from_config = config.get("configurable", {}).get("prompt_ratings_checked") if config else None
    val_from_state = state.get("prompt_ratings_checked") if state else None
    
    logger.debug(
        "[GUARDRAIL] resolving prompt_ratings_checked | context=%s config=%s state=%s",
        val_from_ctx, val_from_config, val_from_state,
    )

    db_already_checked = val_from_ctx or val_from_config or val_from_state
    
    if db_already_checked:
        logger.info("[GUARDRAIL] Skipping check because db_already_checked is True.")
        return None
    if state and "messages" in state:
        for msg in reversed(state["messages"]):
            if isinstance(msg, ToolMessage):
                if msg.name == "pending_ratings" or "pending_rating" in str(msg.content):
                    logger.info("[GUARDRAIL] Skipping check because 'pending_ratings' found in recent history.")
                    return None

    ctx = get_execution_context()
    if not isinstance(ctx, dict) or not ctx:
        logger.info(f"[GUARDRAIL] No execution context; direct DB check for profile {user_profile_id}...")
        rating_result = await check_pending_ratings(user_profile_id)
        return _build_pending_rating_command(rating_result, tool_call_id, mark_emitted=None)

    async with _rating_guardrail_lock:
        cached = ctx.get(_RATING_GUARDRAIL_CACHE_KEY)
        is_first = cached is None
        if is_first:
            cached = {"result": None, "emitted": False, "ready": asyncio.Event()}
            ctx[_RATING_GUARDRAIL_CACHE_KEY] = cached

    if is_first:
        logger.info(f"[GUARDRAIL] Performing DB check for pending ratings for profile {user_profile_id}...")
        cached["result"] = await check_pending_ratings(user_profile_id)
        cached["ready"].set()
    else:
        logger.info("[GUARDRAIL] Reusing pending-rating decision already made this turn.")
        await cached["ready"].wait()

    rating_result = cached["result"] or {}
    if rating_result.get("status") != "pending_rating":
        logger.info("[GUARDRAIL] No pending rating found. Proceeding.")
        return None

    return _build_pending_rating_command(rating_result, tool_call_id, mark_emitted=cached)


def _build_pending_rating_command(
    rating_result: dict,
    tool_call_id: str,
    mark_emitted: Optional[dict],
) -> Optional[Command]:
    """Build the pending-rating blocking Command, emitting the details once per turn and a silent block afterwards."""
    if rating_result.get("status") != "pending_rating":
        return None

    already_emitted = mark_emitted is not None and mark_emitted.get("emitted")
    if already_emitted:
        logger.info("[GUARDRAIL] Pending rating already surfaced this turn; blocking silently.")
        return Command(
            update={
                "messages": [ToolMessage(
                    content=json.dumps({"status": "pending_rating_already_prompted"}),
                    tool_call_id=tool_call_id,
                    name="pending_ratings"
                )]
            }
        )

    if mark_emitted is not None:
        mark_emitted["emitted"] = True

    logger.info("[GUARDRAIL] Pending rating found! Blocking tool execution.")
    final_implicit_output = {
        "pending_rating_details": rating_result,
        "response_type": "pending_rating"
    }
    return Command(
        update={
            "messages": [ToolMessage(
                content=json.dumps(rating_result),
                tool_call_id=tool_call_id,
                name="pending_ratings"
            )],
            "implicit_output": final_implicit_output,
            "response_type": "pending_rating"
        }
    )
    logger.info("[GUARDRAIL] No pending rating found. Proceeding.")
    return None


def _get_last_user_message(state: Optional[dict]) -> Optional[str]:
    """Return the text of the most recent human message in the agent state, or None."""
    if not state:
        return None
    messages = state.get("messages") if isinstance(state, dict) else None
    if not messages:
        return None

    for msg in reversed(messages):
        if isinstance(msg, dict):
            role = msg.get("role") or msg.get("type")
            content = msg.get("content")
        else:
            role = getattr(msg, "type", None) or getattr(msg, "role", None)
            content = getattr(msg, "content", None)

        if role not in ("human", "user"):
            continue
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
            if text:
                return text
    return None


@tool(
    name_or_callable="search_provider",
    description="SEARCH DIRECTORY: Discovers businesses, services, or products (e.g., food, repairs, items) available on the Etown platform when user is browsing or provider name is unknown. Returns business details including ratings, reviews, contact info, and prices. Use this for discovery queries like 'find bakeries near me' or 'show plumbers'.",
    args_schema=SearchProvider
)
@business_operation('provider_search', capture_args=True, capture_result=True)
async def search_provider(
    search_term: str,
    search_category: str="business",
    years_in_business: int=0,
    limit: int=10,
    offset: int=0,
    max_distance_meters: int=25000,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None

):
    """Search Etown providers for the term, track views, and return a Command with results as selected_providers."""
    
    user_latitude = get_context_value("user_latitude", None, config, state, ["latitude"])
    user_longitude = get_context_value("user_longitude", None, config, state, ["longitude"])
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    user_name = get_context_value("user_name", "Etown User", config, state)
    shown_provider_ids = get_context_value("shown_provider_ids", None, config, state) or []
    shown_provider_ids_by_term = get_context_value(
        "shown_provider_ids_by_term", None, config, state
    ) or {}
    if shown_provider_ids:
        logger.info(f"[SEARCH_PROVIDER] Excluding {len(shown_provider_ids)} previously shown providers")
    guardrail_response = await _check_pending_rating_guardrail(user_profile_id, config, state, tool_call_id)
    if guardrail_response:
        return guardrail_response
    
    if user_latitude and user_longitude:
        logger.info("search_provider: using caller coordinates | lat=%s lon=%s", user_latitude, user_longitude)

    import re
    normalized_term = search_term.strip()
    raw_user_prompt = _get_last_user_message(state)
    match = re.search(r"['\"]([^'\"]+)['\"]", normalized_term)
    if match:
        extracted = match.group(1).strip()
        if extracted:
            normalized_term = extracted
    if not normalized_term:
        return Command(
            update={
                "messages": [ToolMessage("Please provide a business, service, or product name to search.", tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "search_provider"
            }
        )
    results, text_description = await handle_search_provider(
        normalized_term,
        search_category=search_category,
        years_in_business=years_in_business,
        limit=limit,
        offset=offset,
        max_distance_meters=max_distance_meters,
        user_latitude=user_latitude,
        user_longitude=user_longitude,
        contact_id=contact_id,
        user_profile_id=user_profile_id,
        user_name=user_name,
        shown_provider_ids=shown_provider_ids,
        shown_provider_ids_by_term=shown_provider_ids_by_term,
        prioritize_closest=_requests_closest_provider(raw_user_prompt),
        request_date_and_time=_requests_date_and_time(raw_user_prompt),
        request_weekend_choice=_requests_weekend_choice(raw_user_prompt),
        request_named_day=_named_day(raw_user_prompt),
    )
    contact_keywords = ['contact', 'phone', 'number', 'email', 'call', 'reach']
    is_contact_request = any(keyword in search_term.lower() for keyword in contact_keywords)

    if is_contact_request and results:
        business_result = results[0]
        business_name = business_result.get('businessname', 'this business')
        phone = business_result.get('mobilenumber')
        email = business_result.get('email')
        contact_parts = []
        if phone:
            contact_parts.append(f"Phone: {phone}")
        if email:
            contact_parts.append(f"Email: {email}")
        
        if contact_parts:
            text_description = f"Here are the contact details for {business_name}:\n{' | '.join(contact_parts)}\n\nIf you need any further information or want to proceed with a booking, please let me know!"
        else:
            text_description = f"Sorry, I could find {business_name} but contact details are not available at the moment."
    
    log_agent_decision(
        'member_agent', 
        'provider_search',
        {
            'search_term': normalized_term,
            'search_category': search_category,
            'user_location': {'lat': user_latitude, 'lon': user_longitude},
            'filters': {
                'years_in_business': years_in_business,
                'max_distance_meters': max_distance_meters
            },
            'result_count': len(results) if results else 0,
            'user_context': {
                'contact_id': contact_id,
                'user_profile_id': user_profile_id,
                'user_name': user_name
            }
        }
    )
    
    if results and user_profile_id:
        _db_provider_upis = [
            r["userprofileid"] for r in results
            if isinstance(r, dict) and r.get("userprofileid") and r.get("source") == "database"
        ]
        if _db_provider_upis:
            async def _track_search_provider_views(provider_upis: list, viewer_upi: str):
                for pupi in provider_upis:
                    try:
                        await Database.fetchval(
                            "SELECT add_provider_view($1, $2, 'chaatie_search')",
                            pupi,
                            viewer_upi
                        )
                    except Exception as _e:
                        logger.warning(f"[SEARCH_PROVIDER] Failed to track view for provider {pupi}: {_e}")
            asyncio.create_task(_track_search_provider_views(_db_provider_upis, user_profile_id))

    return Command(
        update={
            "messages": [ToolMessage(text_description, tool_call_id=tool_call_id)],
            "implicit_output": results,
            "selected_providers": results,
            "response_type": "search_provider"
        }
    )

@tool(
    name_or_callable="recommend_products",
    description=(
        "PRODUCT RECOMMENDATIONS: Suggests actual PRODUCTS available on Etown related to what the "
        "user wants to buy — the item itself PLUS complementary/related products. Use this whenever "
        "the user asks to 'suggest products', 'recommend products', 'related products', 'what products "
        "should I buy', 'show me products for X', or wants product ideas/suggestions — especially as a "
        "follow-up after a provider search (e.g. after searching 'I want to buy ac' the user says "
        "'suggest me some related products'). Returns individual product listings (with provider, price, "
        "image), NOT a list of businesses. Do NOT use search_provider for product-suggestion requests — "
        "it returns providers again. Each returned product can then be ordered with request_provider "
        "using its providerservicesid."
    ),
    args_schema=RecommendProducts
)
@business_operation('product_recommendation', capture_args=True, capture_result=True)
async def recommend_products(
    search_term: str,
    limit: int = 10,
    max_distance_meters: int = 25000,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Recommend nearby Etown catalogue products related to the term and return them in a Command as selected_providers."""
    user_latitude = get_context_value("user_latitude", None, config, state, ["latitude"])
    user_longitude = get_context_value("user_longitude", None, config, state, ["longitude"])
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    shown_provider_ids = get_context_value("shown_provider_ids", None, config, state) or []
    shown_provider_ids_by_term = get_context_value(
        "shown_provider_ids_by_term", None, config, state
    ) or {}
    shown_provider_ids = list(
        shown_ids_for_search(shown_provider_ids_by_term, search_term, shown_provider_ids)
    )

    guardrail_response = await _check_pending_rating_guardrail(user_profile_id, config, state, tool_call_id)
    if guardrail_response:
        return guardrail_response

    if not search_term or not search_term.strip():
        return Command(
            update={
                "messages": [ToolMessage("Please tell me which product you're looking for.", tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "recommend_products"
            }
        )

    try:
        results, text_description = await handle_recommend_products(
            search_term=search_term.strip(),
            limit=limit,
            max_distance_meters=max_distance_meters,
            user_latitude=user_latitude,
            user_longitude=user_longitude,
            contact_id=contact_id,
            shown_product_ids=shown_provider_ids,
        )
    except Exception as e:
        logger.error(f"[recommend_products] failed for '{search_term}': {e}", exc_info=True)
        return Command(
            update={
                "messages": [ToolMessage(
                    "I ran into a problem finding product recommendations just now. Please try again in a moment.",
                    tool_call_id=tool_call_id
                )],
                "implicit_output": None,
                "response_type": None
            }
        )

    log_agent_decision(
        'member_agent',
        'product_recommendation',
        {
            'search_term': search_term,
            'user_location': {'lat': user_latitude, 'lon': user_longitude},
            'result_count': len(results) if results else 0,
            'user_context': {
                'contact_id': contact_id,
                'user_profile_id': user_profile_id,
            }
        }
    )

    return Command(
        update={
            "messages": [ToolMessage(text_description, tool_call_id=tool_call_id)],
            "implicit_output": results,
            "selected_providers": results,
            "response_type": "recommend_products"
        }
    )

@tool(
    name_or_callable="request_provider",
    description="Requests a specific provider for service. Use this tool when a user wants to request a service from a particular provider.",
    args_schema=RequestProvider
)
@business_operation('provider_request', capture_args=True, capture_result=True)
async def request_provider(
    provider_item_id: str,
    user_address: str,
    detailed_request: str,
    first_name: str,
    service_datetime: Optional[str] = None,
    provider_type: str="verified",
    provider_category: str="service",
    quantity: int = 1,
    provider_item_ids: Optional[List[str]] = None,
    quantities: Optional[List[int]] = None,
    distance_confirmed: bool = False,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Book one or more provider items for the member after the rating guardrail and return the booking Command."""
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    user_latitude = get_context_value("user_latitude", None, config, state, ["latitude"])
    user_longitude = get_context_value("user_longitude", None, config, state, ["longitude"])
    mobilenumber_accesstoken = get_context_value("mobilenumber_accesstoken", None, config, state)
    oauth_token = get_context_value("oauth_token", None, config, state)
    user_role = get_context_value("user_role", None, config, state) or 2
    user_address = get_context_value("address", user_address, config, state) or ""
    guardrail_response = await _check_pending_rating_guardrail(user_profile_id, config, state, tool_call_id)
    if guardrail_response:
        return guardrail_response

    shared_kwargs = dict(
        contact_id=contact_id,
        user_profile_id=user_profile_id,
        service_datetime=service_datetime,
        user_address=user_address,
        detailed_request=detailed_request,
        first_name=first_name,
        provider_type=provider_type,
        category=provider_category,
        user_latitude=user_latitude,
        user_longitude=user_longitude,
        mobilenumber_accesstoken=mobilenumber_accesstoken,
        oauth_token=oauth_token,
        user_role=user_role,
        distance_confirmed=distance_confirmed,
        state=state,
        config=config,
    )

    item_ids = provider_item_ids if (provider_item_ids and len(provider_item_ids) > 0) else [provider_item_id]
    item_qtys = [
        (quantities[i] if quantities and i < len(quantities) else quantity)
        for i in range(len(item_ids))
    ]
    result_text, implicit_output = await handle_request_provider(
        provider_item_ids=item_ids,
        quantities=item_qtys,
        **shared_kwargs
    )

    log_agent_decision(
        'member_agent', 
        'provider_request',
        {
            'provider_item_id': provider_item_id,
            'provider_item_ids': provider_item_ids,
            'quantities': quantities,
            'service_datetime': service_datetime,
            'detailed_request': detailed_request,
            'provider_type': provider_type,
            'quantity': quantity,
            'user_context': {
                'contact_id': contact_id,
                'user_profile_id': user_profile_id,
                'first_name': first_name,
                'user_address': user_address[:100] if user_address else None
            },
            'success': implicit_output is not None
        }
    )

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "response_type": "request_provider"
        }
    )


@tool(
    name_or_callable="add_booking_instructions",
    description=(
        "Attach extra free-text instructions to a booking the user has ALREADY placed. "
        "Use this ONLY when, after a booking is submitted, the user volunteers additional "
        "instructions for the provider (e.g. 'tell them to call before arriving', 'bring a "
        "ladder', 'leave it with the security guard'). Never ask the user for instructions "
        "while taking the booking itself. New instructions are appended to any that already "
        "exist on the booking. Leave booking_reference empty to target the user's most recent booking."
    ),
    args_schema=AddBookingInstructions
)
@business_operation('booking_instructions', capture_args=True, capture_result=True)
async def add_booking_instructions(
    instructions: List[str],
    booking_reference: Optional[str] = None,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Append user instructions to the referenced or most recent booking and return the confirmation Command."""
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)

    result_text, implicit_output = await handle_add_booking_instructions(
        instructions=instructions,
        contact_id=contact_id,
        user_profile_id=user_profile_id,
        booking_reference=booking_reference,
    )

    log_agent_decision(
        'member_agent',
        'booking_instructions',
        {
            'instructions': instructions,
            'booking_reference': booking_reference,
            'user_context': {
                'contact_id': contact_id,
                'user_profile_id': user_profile_id,
            },
            'success': implicit_output is not None,
        }
    )

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "response_type": None
        }
    )


@tool(
    name_or_callable="enquire_provider",
    description=(
        "Sends the member's free-text enquiry to a provider as a chat message. Use this ONLY when "
        "the member chose to ENQUIRE rather than book — typically because the provider does not list "
        "what they asked for and the member picked 'Enquire' over 'Book with instructions'. This does "
        "NOT create a booking: it opens (or reuses) the member's chat with that provider and posts "
        "their message, so the provider replies in chat. Never call it to place, confirm, or chase a "
        "booking, and never call it as a fallback after a booking was declined."
    ),
    args_schema=EnquireProvider
)
@business_operation('provider_enquiry', capture_args=True, capture_result=True)
async def enquire_provider(
    message: str,
    provider_identifier: str = "",
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Post the member's enquiry into their chat with a provider, without booking, and return the conversation Command."""
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    oauth_token = get_context_value("oauth_token", None, config, state)

    result_text, implicit_output = await handle_enquire_provider(
        message=message,
        provider_identifier=provider_identifier,
        user_profile_id=user_profile_id,
        oauth_token=oauth_token,
        state=state,
    )

    log_agent_decision(
        'member_agent',
        'provider_enquiry',
        {
            'provider_identifier': provider_identifier,
            'message': message[:200] if message else None,
            'user_context': {
                'user_profile_id': user_profile_id,
            },
            'success': implicit_output is not None,
        }
    )

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "response_type": "enquire_provider" if implicit_output else None
        }
    )


@tool(
    name_or_callable="booknow_provider",
    description="Looks up a specific business by name to find its available services/products. Use this tool when a user says 'book now' or 'get now' with a specific business name. IMPORTANT: This tool only FINDS the provider — to actually complete the booking, you must then call request_provider with the provider's ID from the results.",
    args_schema=BookNowProvider
)
@business_operation('provider_booknow_lookup', capture_args=True, capture_result=True)
async def booknow_provider(
    search_term: str,
    additional_details: str="",
    name_confirmed: bool = False,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Look up a business by name for booking and return its items in a Command as selected_providers."""
    user_role = get_context_value("user_role", None, config, state) or 2
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    user_latitude = get_context_value("user_latitude", None, config, state, ["latitude"])
    user_longitude = get_context_value("user_longitude", None, config, state, ["longitude"])
    guardrail_response = await _check_pending_rating_guardrail(user_profile_id, config, state, tool_call_id)
    if guardrail_response:
        return guardrail_response

    result_text, implicit_output = await handle_booknow_provider(
        search_term=search_term,
        user_role=user_role,
        additional_details=additional_details,
        user_latitude=user_latitude,
        user_longitude=user_longitude,
        name_confirmed=name_confirmed,
    )
    
    log_agent_decision(
        'member_agent', 
        'booknow_provider_lookup',
        {
            'search_term': search_term,
            'user_role': user_role,
            'additional_details': additional_details,
            'result_count': len(implicit_output) if implicit_output else 0,
            'user_context': {
                'user_profile_id': get_context_value("user_profile_id", None, config, state)
            }
        }
    )
    
    selected_providers_data = implicit_output if implicit_output and isinstance(implicit_output, list) else None
    
    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "selected_providers": selected_providers_data,
            "response_type": "booknow_provider"
        }
    )

@tool(
    name_or_callable="provider_service_insights",
    description="Gets detailed provider, service, contact, and distance information (phone, email, website, distance from user location) from DB for known/specific providers. Use this for follow-up questions after search results and for direct provider-name queries like contact details, phone number, website, availability, pricing, ratings, 'how far', and distance from my location.",
    args_schema=ProviderServiceInsights
)
@business_operation('provider_service_insights', capture_args=True, capture_result=True)
async def provider_service_insights(
    providerservicesid: str = "",
    business_name: str = "",
    match_limit: int = 5,
    purpose: str = "",
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Return a Command with profile, contact and distance insights for a known provider, by id or name."""
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    user_latitude = get_context_value("user_latitude", None, config, state, ["latitude"])
    user_longitude = get_context_value("user_longitude", None, config, state, ["longitude"])
    guardrail_response = await _check_pending_rating_guardrail(user_profile_id, config, state, tool_call_id)
    if guardrail_response:
        return guardrail_response

    result_text, implicit_output = await handle_provider_service_insights(
        providerservicesid=providerservicesid,
        business_name=business_name,
        match_limit=match_limit,
        user_latitude=user_latitude,
        user_longitude=user_longitude,
        state=state,
        purpose=purpose,
    )

    selected_providers_data = None
    if isinstance(implicit_output, dict):
        selected_providers_data = implicit_output.get("selected_providers_data") or None

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "selected_providers": selected_providers_data,
            "response_type": "provider_service_insights"
        }
    )

@tool(
    name_or_callable="address_confirmation",
    description="Confirms the user's address for service delivery. Use this tool when a user needs to verify or update their address information.",
)
async def address_confirmation(
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Return a Command asking the user to confirm the address held in the execution context."""
    user_address = get_context_value("address", None, config, state)
    
    result_text, implicit_output = await handle_address_confirmation(user_address=user_address)

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "response_type": "address_confirmation"
        }
    )

@tool(
    name_or_callable="generate_qr",
    description="Generates a QR code for the user. Use this tool when a user requests a QR code for identification, access, or other purposes.",
)
async def generate_qr(
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Return a Command carrying the generate_qr UI trigger from handle_generate_qr."""
    result_text, implicit_output = await handle_generate_qr()

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "response_type": "generate_qr"
        }
    )

@tool(
    name_or_callable="create_post",
    description=(
        "Opens the mobile app's own post-composer screen so the user can write and publish a post "
        "MANUALLY themselves. This tool publishes NOTHING — it only signals the app to open its "
        "composer UI. Use it ONLY when the user explicitly asks to open the composer / post screen, "
        "or says they want to write the post themselves (e.g. 'open the post screen', 'let me write "
        "it myself', 'take me to create post'). "
        "DO NOT use this when the user wants YOU to compose or publish a post for them — that is "
        "create_post_from_chat, which actually writes the post to their feed. If in doubt between "
        "the two, choose create_post_from_chat."
    ),
)
async def create_post(
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Return a Command that tells the app to open its post composer; publishes nothing."""
    result_text, implicit_output = await handle_create_post()

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "response_type": "create_post"
        }
    )


@tool(
    name_or_callable="booking_info",
    description="Retrieves the real status and details of one of the user's own bookings (tracking a booking). Use when the user asks to track a booking, check its status, or see its details. Pass the tracking ID if they gave one, otherwise leave booking_id empty to get their most recent booking. The tool reports when no such booking exists — relay that instead of confirming a booking you cannot see.",
    args_schema=BookingInfo
)
async def booking_info(
    booking_id: Optional[str] = None,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Return a Command with the status and details of the user's referenced or latest booking, or not-found."""
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    
    result_text, implicit_output = await handle_booking_info(
        booking_id=booking_id,
        contact_id=contact_id,
        user_profile_id=user_profile_id
    )
    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "response_type": "booking_info"
        }
    )


@tool(
    name_or_callable="cancel_booking",
    description="Cancels one of the user's own bookings. Use ONLY after the user has explicitly confirmed they want to cancel. Pass the tracking ID if they gave one, otherwise leave booking_reference empty to cancel their most recent booking. The tool reports when a booking does not exist or can no longer be cancelled — relay that outcome truthfully and NEVER claim a booking was cancelled unless this tool returned success.",
    args_schema=CancelBooking
)
async def cancel_booking(
    booking_reference: Optional[str] = None,
    cancel_reason: Optional[str] = None,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Cancel the user's referenced or most recent booking and return a Command with the outcome."""
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)

    result_text, implicit_output = await handle_cancel_booking(
        booking_reference=booking_reference,
        cancel_reason=cancel_reason,
        contact_id=contact_id,
        user_profile_id=user_profile_id,
    )
    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "response_type": "cancel_booking"
        }
    )


@tool(
    name_or_callable="connect_support_agent",
    description="Connects the user to a human support agent for further assistance.",
    args_schema=ConnectSupportAgent
)
async def connect_support_agent(
    reason_for_contact: str,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """File a support ticket for the user and return a Command with its reference and promised response time."""
    result = await handle_connect_support_agent(
        reason_for_contact,
        contact_id=get_context_value("contact_id", None, config, state),
        user_profile_id=get_context_value("user_profile_id", None, config, state),
    )
    return Command(
        update={
            "messages": [ToolMessage(str(result), tool_call_id=tool_call_id)],
            "implicit_output": result,
            "response_type": "connect_support_agent"
        }
    )


@tool(
    name_or_callable="get_provider_requests_list",
    description="PROVIDER TOOL ONLY: Retrieves the list of service/product requests received by a provider. Use this ONLY when providers want to see their OWN received booking requests/history. NEVER use for business reviews, ratings, prices, or customer information searches - use search_provider instead.",
    args_schema=GetProviderRequestList
)
async def get_provider_requests_list(
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    date: Optional[str] = None,
    first_count: Optional[int] = None,
    last_count: Optional[int] = None,
    page_number: int = 0,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Return a Command with the provider's received requests, filtered by time or count and paginated; providers only."""
    
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    mobilenumber_accesstoken = get_context_value("mobilenumber_accesstoken", None, config, state)
    user_role = get_context_value("user_role", None, config, state) or 2
    if user_role != 1:
        error_msg = "This tool is only available for provider accounts. For business reviews or information, please use the search function."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "get_provider_requests_list"
            }
        )
    
    if not contact_id or not user_profile_id:
        error_msg = "Provider information is missing. Please ensure you are logged in as a provider."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "get_provider_requests_list"
            }
        )
    
    if not mobilenumber_accesstoken:
        error_msg = "Authentication token is missing. Please log in again."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "get_provider_requests_list"
            }
        )
    
    result_text, result_data = await handle_provider_request_getlist(
        contact_id=contact_id,
        user_profile_id=user_profile_id,
        mobilenumber_accesstoken=mobilenumber_accesstoken,
        start_time=start_time,
        end_time=end_time,
        date=date,
        first_count=first_count,
        last_count=last_count,
        page_number=page_number
    )
    
    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": result_data,
            "response_type": "get_provider_requests_list"
        }
    )


@tool(
    name_or_callable="get_member_requests_list",
    description="MEMBER TOOL ONLY: HISTORY CHECK: Retrieves member request history. Use recent_only=True for 'recent booking' queries, recent_only=False for 'list of bookings/orders/history'. Use this ONLY when user asks about their OWN past orders/requests/bookings. NEVER use for business reviews, ratings, prices, or provider information searches - use search_provider instead.",
    args_schema=GetMemberRequestList
)
async def get_member_requests_list(
    request_tracking_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    date: Optional[str] = None,
    first_count: Optional[int] = None,
    last_count: Optional[int] = None,
    page_number: int = 0,
    recent_only: bool = False,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Return a Command with the member's request history, by tracking id, time, count or most recent; members only."""
    
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    mobilenumber_accesstoken = get_context_value("mobilenumber_accesstoken", None, config, state)
    user_role = get_context_value("user_role", None, config, state) or 2
    if user_role != 2:
        error_msg = "This tool is only available for member accounts. Providers should use the provider request list tool."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "get_member_requests_list"
            }
        )
    
    if not contact_id or not user_profile_id:
        error_msg = "Member information is missing. Please ensure you are logged in."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "get_member_requests_list"
            }
        )
    
    if not mobilenumber_accesstoken:
        error_msg = "Authentication token is missing. Please log in again."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "get_member_requests_list"
            }
        )
    
    result_text, result_data = await handle_member_request_getlist(
        contact_id=contact_id,
        user_profile_id=user_profile_id,
        mobilenumber_accesstoken=mobilenumber_accesstoken,
        request_tracking_id=request_tracking_id,
        start_time=start_time,
        end_time=end_time,
        date=date,
        first_count=first_count,
        last_count=last_count,
        page_number=page_number,
        recent_only=recent_only
    )
    
    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": result_data,
            "response_type": "get_member_requests_list"
        }
    )


@tool(
    name_or_callable="provide_etown_info",
    description="Provides information about Etown features, services, policies, and general platform information. Use this when users ask questions about how Etown works, what features are available, or general platform inquiries.",
    args_schema=ProvideEtownInfo
)
async def provide_etown_info(
    etown_query: str,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Answer a question about the Etown platform from its knowledge base and return the answer in a Command."""
    
    result_text = await handle_provide_etown_info(etown_query)
    
    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": result_text,
            "response_type": "provide_etown_info"
        }
    )


@tool(
    name_or_callable="internal_invite",
    description="""
    Internal Invite Tool - Two-Step Workflow:

    **STEP 1 - SEARCH USER:**
    1. PRIMARY USE: Use this tool FIRST when the user says something like 'Invite [Name]'.
    2. ACTION: Set action='search' to find the user.
    3. SEARCH LOGIC: Search by NAME first.
    4. RESULT: If the tool finds a unique user, it will ask for confirmation. If it finds multiple, it will return a list for selection.

    **STEP 2 - SEND INVITE:**
    5. AFTER SEARCH: When user confirms they want to send the invite (says "yes", "send it", "go ahead", "confirm", etc.), use action='send' with the same search_term. ALWAYS call this tool for that reply - the tool resolves the confirmation against the people it already found and sends the invite; do not answer a confirmation yourself.
    6. SEND ACTION: Set action='send' to actually send the invitation to the found user.
    7. DECLINE: If the user says "no"/"cancel" right after an invite question, still call this tool with the same search_term - it cancels the pending invite cleanly.

    **SPECIAL CASES:**
    8. PHONEBOOK/CONTACTS: If the user mentions 'my contacts', 'my phonebook', 'phone book', 'contact list', or wants to invite from their device contacts, set search_scope='phonebook_only'.
    9. DEFAULT SCOPE: For all other invite requests (user provides a name), keep search_scope='all' (default).
    10. MULTIPLE NAMES: If the user asks to invite SEVERAL people at once (e.g. "invite A, B and C"), pass ALL the names as a list in `search_term` in a SINGLE tool call. Do NOT call this tool separately once per name - each separate call overwrites the previous call's results and only the last person will be shown to the user.
    """,
    args_schema=InternalInvite
)
async def internal_invite(
    search_term: Union[str, List[str]],
    role: Literal["Member", "Provider"] = "Member",
    action: Literal["search", "send"] = "search",
    search_scope: Literal["all", "phonebook_only"] = "all",
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Search for existing Etown users by one or more names, or send them a role invite, and return the Command."""
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    user_name = get_context_value("user_name", "User", config, state)
    oauth_token = get_context_value("oauth_token", "", config, state)
    mobilenumber_accesstoken = get_context_value("mobilenumber_accesstoken", None, config, state)
    language = get_context_value("language", "en", config, state)
    user_role = get_context_value("user_role", None, config, state) or 2
    conversation_id = get_context_value("conversation_id", None, config, state)

    if not contact_id or not user_profile_id:
         error_msg = "User information is missing. Please ensure you are logged in."
         return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "internal_invite"
            }
        )

    user_prompt = _get_last_user_message(state)

    result_text, implicit_output = await handle_internal_invite(
        search_term=search_term,
        role=role,
        contact_id=contact_id,
        user_profile_id=user_profile_id,
        user_name=user_name,
        oauth_token=oauth_token,
        mobilenumber_accesstoken=mobilenumber_accesstoken,
        language=language,
        search_scope=search_scope,
        action=action,
        user_role=user_role,
        conversation_id=conversation_id,
        user_prompt=user_prompt
    )

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "response_type": "internal_invite"
        }
    )


@tool(
    name_or_callable="external_invite_members",
    description="""
    Send an external invite to a user OUTSIDE Etown via email or mobile.
    CRITICAL CONSTRAINT: Do NOT use this tool if the user only provides a NAME.
    1. If user says 'Invite Praveen', use internal_invite first.
    2. Only use this tool if 'internal_invite' fails OR if the user explicitly provides an external contact (email/phone) knowing they are not on Etown.
    """,
    args_schema=ExternalInviteMembers
)
async def external_invite_for_members(
    invitee_name: str,
    invitee_email: str,
    invitee_mobile_number: str,
    invitee_role: Literal["Member", "Provider"] = "Member",
    invite_confirmation: str = "Not Confirmed",
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Send a member's external invite by email, else by mobile, and return the outcome Command."""
    
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    mobilenumber_accesstoken = get_context_value("mobilenumber_accesstoken", None, config, state)
    
    if not contact_id or not user_profile_id:
        error_msg = "User information is missing. Please ensure you are logged in."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "external_invite_members"
            }
        )
    
    if not mobilenumber_accesstoken:
        error_msg = "Authentication token is missing. Please log in again."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "external_invite_members"
            }
        )
    
    if not invitee_email and not invitee_mobile_number:
        error_msg = "Please provide either an email address or mobile number to send the invite."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "external_invite_members"
            }
        )
    
    result_text = None
    result_data = None
    
    if invitee_email:
        result_text, result_data = await handle_external_email_invite(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            receiver_name=invitee_name,
            inviter_type=2,
            invite_role=invitee_role,
            invitecontact_email=invitee_email,
            mobilenumber_accesstoken=mobilenumber_accesstoken
        )
    elif invitee_mobile_number:
        result_text, result_data = await handle_external_mobile_invite(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            receiver_name=invitee_name,
            inviter_type=2,
            invite_role=invitee_role,
            invitecontact_mobilenumber=invitee_mobile_number,
            mobilenumber_accesstoken=mobilenumber_accesstoken,
            language=get_context_value("language", "en", config, state),
        )
    
    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": result_data,
            "response_type": "external_invite_members"
        }
    )


@tool(
    name_or_callable="external_invite_providers",
    description="""
    Send an external invite to a user OUTSIDE Etown via email or mobile.
    CRITICAL CONSTRAINT: Do NOT use this tool if the user only provides a NAME.
    1. If user says 'Invite Praveen', use internal_invite first.
    2. Only use this tool if 'internal_invite' fails OR if the user explicitly provides an external contact (email/phone) knowing they are not on Etown.
    """,
    args_schema=ExternalInviteProviders
)
async def external_invite_for_providers(
    invitee_name: str,
    invitee_email: str,
    invitee_mobile_number: str,
    invitee_role: Literal["Member", "Provider"] = "Provider",
    invite_confirmation: str = "Not Confirmed",
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Send a provider's external invite by email, else by mobile, and return the outcome Command."""
    
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    mobilenumber_accesstoken = get_context_value("mobilenumber_accesstoken", None, config, state)
    
    if not contact_id or not user_profile_id:
        error_msg = "User information is missing. Please ensure you are logged in."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "external_invite_providers"
            }
        )
    
    if not mobilenumber_accesstoken:
        error_msg = "Authentication token is missing. Please log in again."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "external_invite_providers"
            }
        )
    
    if not invitee_email and not invitee_mobile_number:
        error_msg = "Please provide either an email address or mobile number to send the invite."
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": "external_invite_providers"
            }
        )
    
    result_text = None
    result_data = None
    
    if invitee_email:
        result_text, result_data = await handle_external_email_invite_provider(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            receiver_name=invitee_name,
            invite_role=invitee_role,
            invitecontact_email=invitee_email,
            mobilenumber_accesstoken=mobilenumber_accesstoken
        )
    elif invitee_mobile_number:
        result_text, result_data = await handle_external_mobile_invite_provider(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            receiver_name=invitee_name,
            invite_role=invitee_role,
            invitecontact_mobilenumber=invitee_mobile_number,
            mobilenumber_accesstoken=mobilenumber_accesstoken,
            language=get_context_value("language", "en", config, state),
        )
    
    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": result_data,
            "response_type": "external_invite_providers"
        }
    )

@tool(
    name_or_callable="pending_ratings",
    description="Checks database for any completed bookings that haven't been rated by the user yet. Use this tool BEFORE making a new booking search to see if we should ask the user for a rating.",
)
async def pending_ratings(
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Return a Command with the user's completed bookings that still await a rating."""
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    
    result = await check_pending_ratings(user_profile_id)
    
    return Command(
        update={
            "messages": [ToolMessage(str(result), tool_call_id=tool_call_id, name="pending_ratings")],
            "implicit_output": result,
            "response_type": "pending_rating" 
        }
    )


@tool(
    name_or_callable="web_search",
    description="REAL-TIME INFORMATION: Searches the web for current and recent dynamic information such as weather (including rainfall in past windows), UV index by location, market prices (including now vs prior timestamp requests), news, traffic, and sports fixtures/openers/venues. Use whenever user asks for live or changing information and ground the response in web results.",
    args_schema=WebSearch
)
async def web_search(
    search_query: str,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Run a real-time web search after the rating guardrail and return the answer in a Command."""
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    pending_check = await _check_pending_rating_guardrail(user_profile_id, config, state, tool_call_id)
    if pending_check:
        return pending_check

    result = await handle_web_search(search_query, state, config)
    
    user_message = result.get("user_message", result.get("content", "Web search completed."))
    
    return Command(
        update={
            "messages": [ToolMessage(
                content=user_message, 
                tool_call_id=tool_call_id, 
                name="web_search"
            )],
            "implicit_output": result,
            "response_type": "web_search"
        }
    )


@tool(
    name_or_callable="query_my_data",
    description=(
        "ACCOUNT DATA QUESTIONS: answers counts, totals, trends and rankings about the user's OWN Etown "
        "activity that no other tool covers, e.g. 'how many bookings did I make last month', 'which "
        "business have I booked most', 'how many requests did I get this week', 'how many people viewed "
        "my profile', 'which of my offers got the most views', 'what is my rating', 'how many plumbers are "
        "on Etown in Madurai'. Do NOT use it to show the booking list (request list tools), to track one "
        "booking (booking_info), to find or book providers (search tools), or for anything on the web."
    ),
    args_schema=QueryMyData,
)
async def query_my_data(
    question: str,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None,
):
    """Answer a question about the caller's own Etown data from curated views and return the rows in a Command."""
    text, _ = await handle_query_my_data(
        question,
        contact_id=get_context_value("contact_id", None, config, state),
        user_profile_id=get_context_value("user_profile_id", None, config, state),
        user_role=get_context_value("user_role", None, config, state),
        conversation_id=get_context_value("conversation_id", None, config, state),
    )
    return Command(
        update={
            "messages": [ToolMessage(content=text, tool_call_id=tool_call_id, name="query_my_data")],
        }
    )


async def _handle_onboard_provider(
    businessname: str,
    mobilenumber: str,
    description,
    city,
    area_location,
    provider_state,
    business_category,
    service_category,
) -> dict:
    """Insert the provider into the DB, start background AI enhancements, and return the success payload."""
    result = await ProviderService.onboard_provider_in_db(
        business_name=businessname,
        phone_number=mobilenumber,
        description=description or "",
        city=city or "",
        area_location=area_location,
        state=provider_state,
        business_category_input=business_category,
        service_category_input=service_category,
        contact_id=None,
    )
    provider_id = result["provider_info"]["providerid"]
    new_contact = result["contact_info"]["contactid"]
    new_userprofile = result.get("userprofile_info", {}).get("userprofileid")
    psi = result.get("providerservice_info", {})

    import asyncio
    _bg_task = asyncio.create_task(ProviderService.run_background_enhancements(
        provider_id=provider_id,
        providerservice_copy_id=psi.get("providerservicesid"),
        providerservice_orig_id=psi.get("providerservicesid_orig"),
        contact_id=new_contact,
        userprofile_id=new_userprofile,
        business_name=businessname,
        description=description or "",
        business_category_input=business_category,
        service_category_input=service_category,
        latitude=None,
        longitude=None,
        businesslogo=None,
        phone_number=mobilenumber,
        email=None,
        city=city,
        first_name=businessname,
        formatted_address=city or "",
        area_location=area_location,
        state=provider_state,
        additionaladdressinfo=None,
        plan_code=result["provider_info"].get("plan_code"),
    ))
    _bg_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    return {
        "success": True,
        "provider_id": provider_id,
        "contact_id": new_contact,
        "message": (
            f"Successfully registered **{businessname}** on Etown! "
            f"Provider ID: `{provider_id}`. "
            "Your AI-enhanced profile (description, category, and search keywords) will be ready in a few seconds."
        ),
    }


@tool(
    name_or_callable="onboard_provider",
    description=(
        "PROVIDER ONBOARDING: Register any business/service provider on Etown. "
        "Use this when a user wants to register a business — this can be their OWN business "
        "OR a DIFFERENT provider/business they are recommending or registering on behalf of "
        "(e.g. a neighbour's shop, a friend's business, any third-party provider). "
        "COLLECT through natural conversation (one question at a time): "
        "1) Business name (REQUIRED) "
        "2) Mobile number of the PROVIDER/BUSINESS — 8-15 digits (REQUIRED — "
        "this is the business contact number, NOT necessarily the user's own number) "
        "3) Description — what services/products they offer (recommended) "
        "4) City, area, and state (optional — improves local search visibility) "
        "5) Business category (optional — e.g. Plumbing, Catering, Tailoring). "
        "Call this tool ONLY after you have businessname and mobilenumber. "
        "Do NOT call if the tool returns a conflict — inform the user instead."
    ),
    args_schema=OnboardProvider,
)
async def onboard_provider(
    businessname: str,
    mobilenumber: str,
    description: Optional[str] = None,
    city: Optional[str] = None,
    area_location: Optional[str] = None,
    provider_state: Optional[str] = None,
    business_category: Optional[str] = None,
    service_category: Optional[str] = None,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None,
):
    """Register a business on Etown and return a Command with the result or the conflict message."""
    try:
        result = await _handle_onboard_provider(
            businessname=businessname,
            mobilenumber=mobilenumber,
            description=description,
            city=city,
            area_location=area_location,
            provider_state=provider_state,
            business_category=business_category,
            service_category=service_category,
        )
        message       = result["message"]
        response_type = "onboard_provider"
        implicit_out  = result
    except Exception as e:
        err = str(e)
        logger.error(f"[onboard_provider] Failed for '{businessname}': {e}", exc_info=True)
        if "already" in err.lower() or "exists" in err.lower() or "conflict" in err.lower():
            message = err
        else:
            message = "Provider registration failed. Please try again later."
        response_type = None
        implicit_out  = None

    return Command(
        update={
            "messages": [ToolMessage(message, tool_call_id=tool_call_id)],
            "implicit_output": implicit_out,
            "response_type": response_type,
        }
    )


@tool(
    name_or_callable="aionboarding_redirection",
    description=(
        "STAFF CHECK BEFORE ONBOARDING: Call this FIRST, before asking any question, whenever the user "
        "wants to onboard, register or add a provider/business on Etown. It checks whether the user is "
        "an Etown staff member. If it says the user is staff, tell them they are being taken to AI "
        "onboarding and stop. Otherwise continue the normal onboarding flow with onboard_provider. "
        "Call it once per onboarding request, not on every follow-up answer."
    ),
)
async def aionboarding_redirection(
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None,
):
    """Return a Command redirecting staff to AI onboarding, or telling the agent to continue normal onboarding."""
    message, implicit_output = await handle_aionboarding_redirection(
        get_context_value("contact_id", None, config, state)
    )
    update = {"messages": [ToolMessage(message, tool_call_id=tool_call_id)]}
    if implicit_output:
        update["implicit_output"] = implicit_output
        update["response_type"] = "aionboarding_redirection"
    return Command(update=update)


def _resolve_attached_image(
    explicit_image_url: Optional[str],
    config: Optional[RunnableConfig],
    state: Optional[dict],
) -> Optional[str]:
    """Return the explicit image URL, else this or an earlier turn's generated image blob path or URL, else None."""
    if explicit_image_url and explicit_image_url.strip():
        return explicit_image_url.strip()
    last = (
        get_context_value("last_generated_image", None, config, state)
        or get_context_value("restored_generated_image", None, config, state)
    )
    if isinstance(last, dict):
        return last.get("blob_path") or last.get("image_url")
    return None


@tool(
    name_or_callable="create_offer_from_chat",
    description=(
        "PROVIDER TOOL ONLY: Create and post a text offer/promotion to the provider's Circle "
        "directly from the conversation. Use when a provider wants to advertise, promote, post, "
        "or share an offer or deal (e.g. 'post an offer', 'advertise 20% off this weekend'). "
        "TWO-STEP FLOW: (1) Collect the offer description from the provider, then call with "
        "action='draft' to show a preview. (2) ONLY after the provider explicitly approves the "
        "preview, call again with action='confirm' to post it. Never confirm without an approved draft. "
        "The offer is shared with the provider's Circle (default audience). If the provider generated "
        "an image for this offer with generate_image (image_type='offer') earlier, it is attached automatically on confirm."
    ),
    args_schema=CreateOfferFromChat
)
async def create_offer_from_chat(
    description: str,
    title: Optional[str] = None,
    validity: Optional[str] = None,
    scheduled_time: Optional[str] = None,
    image_url: Optional[str] = None,
    action: Literal["draft", "confirm"] = "draft",
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Preview (draft) or post (confirm) a provider's offer to their Circle, attaching any generated image; providers only."""
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    oauth_token = get_context_value("oauth_token", None, config, state)
    user_role = get_context_value("user_role", None, config, state) or 2

    if user_role != 1:
        error_msg = (
            "Posting offers is only available for provider accounts. "
            "Please switch to your provider account to post an offer."
        )
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": None
            }
        )

    resolved_image_url = _resolve_attached_image(image_url, config, state)

    result_text, implicit_output, response_type = await handle_create_offer_from_chat(
        contact_id=contact_id,
        userprofile_id=user_profile_id,
        oauth_token=oauth_token,
        description=description,
        title=title,
        validity=validity,
        scheduled_time=scheduled_time,
        image_url=resolved_image_url,
        action=action,
    )

    log_agent_decision(
        'provider_agent',
        'create_offer_from_chat',
        {
            'action': action,
            'has_title': bool(title),
            'has_image': bool(resolved_image_url),
            'success': response_type is not None and action == "confirm",
            'user_context': {
                'contact_id': contact_id,
                'user_profile_id': user_profile_id,
            }
        }
    )

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "response_type": response_type
        }
    )


@tool(
    name_or_callable="create_post_from_chat",
    description=(
        "Create and publish a feed post to the user's Circle directly from the conversation. "
        "Use when a user (member or provider) wants to create, write, share, or publish a post. "
        "TWO-STEP FLOW: (1) Collect the post text from the user, then call with action='draft' to "
        "show a preview. (2) ONLY after the user explicitly approves, call again with action='confirm' "
        "to publish. Never confirm without an approved draft. If the user generated an image for this "
        "post with generate_image (image_type='feed') earlier, it is attached automatically on confirm. "
        "The post is shared with the user's Circle only — it is NOT published to Instagram, Facebook or "
        "any other external social network, so never tell the user it will appear there."
    ),
    args_schema=CreatePostFromChat
)
async def create_post_from_chat(
    post_text: str,
    image_url: Optional[str] = None,
    action: Literal["draft", "confirm"] = "draft",
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Preview (draft) or publish (confirm) a feed post to the user's Circle, attaching any generated image."""
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    user_role = get_context_value("user_role", None, config, state) or 2
    user_name = get_context_value("user_name", "User", config, state)
    oauth_token = get_context_value("oauth_token", None, config, state)

    resolved_image_url = _resolve_attached_image(image_url, config, state)

    result_text, implicit_output, response_type = await handle_create_post_from_chat(
        contact_id=contact_id,
        userprofile_id=user_profile_id,
        role=user_role,
        user_name=user_name,
        oauth_token=oauth_token,
        post_text=post_text,
        image_url=resolved_image_url,
        action=action,
    )

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": implicit_output,
            "response_type": response_type
        }
    )


@tool(
    name_or_callable="generate_image",
    description=(
        "GENERATE IMAGE: Creates an AI-generated image from a user's text description. "
        "Use this whenever the user asks to generate, create, draw, make, or design a "
        "picture, image, illustration, logo, poster, or artwork (e.g. 'generate an image "
        "of a sunset over the hills', 'make me a logo for my bakery', 'draw a cat astronaut'). "
        "Pass the user's request as `prompt` in their own words — the tool automatically "
        "enhances it into a rich, detailed prompt before generating. ALWAYS set `image_type` "
        "to match intent: 'logo' for a business/brand logo, 'offer' for a promotional/deal/sale "
        "poster, 'feed' for a social-media feed/announcement post, or 'general' for anything "
        "else. Set refine_previous=true when the user asks to change the image just generated. "
        "Optionally set width/height for aspect ratio (512-1280px). Do NOT use this for "
        "searching existing businesses/products or for QR codes. The result lists the exact text "
        "printed on the image; describe the image only from that."
    ),
    args_schema=GenerateImage
)
async def generate_image(
    prompt: str,
    image_type: Literal["logo", "offer", "feed", "general"] = "general",
    width: int = 1024,
    height: int = 1024,
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    refine_previous: bool = False,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Generate or refine an AI image from the user's description and return a Command that also records last_generated_image."""
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    guardrail_response = await _check_pending_rating_guardrail(user_profile_id, config, state, tool_call_id)
    if guardrail_response:
        return guardrail_response

    previous = None
    if refine_previous:
        previous = (
            get_context_value("last_generated_image", None, config, state)
            or get_context_value("restored_generated_image", None, config, state)
        )
    is_provider = str(get_context_value("user_role", None, config, state)) == "1"

    result_text, implicit_output = await handle_generate_image(
        prompt=prompt,
        image_type=image_type,
        width=width,
        height=height,
        steps=steps,
        seed=seed,
        store_format="png" if image_type == "logo" else "jpeg",
        store_quality=88,
        user_profile_id=user_profile_id if is_provider else None,
        previous=previous if isinstance(previous, dict) else None,
    )

    log_agent_decision(
        'member_agent',
        'generate_image',
        {
            'original_prompt': prompt[:200] if prompt else None,
            'image_type': image_type,
            'width': width,
            'height': height,
            'steps': steps,
            'refine_previous': refine_previous,
            'success': implicit_output is not None,
            'user_context': {'user_profile_id': user_profile_id},
        }
    )

    update: dict = {
        "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
        "implicit_output": implicit_output,
        "response_type": "generate_image" if implicit_output else None
    }
    if implicit_output:
        update["last_generated_image"] = {
            "image_url": implicit_output.get("image_url"),
            "blob_path": implicit_output.get("blob_path"),
            "image_type": implicit_output.get("image_type"),
            "original_prompt": implicit_output.get("original_prompt"),
            "enhanced_prompt": implicit_output.get("enhanced_prompt"),
            "seed": implicit_output.get("seed"),
            "design": implicit_output.get("design"),
        }

    return Command(update=update)


@tool(
    name_or_callable="set_profile_image_from_chat",
    description=(
        "PROVIDER TOOL ONLY: Set the image the provider most recently generated with generate_image "
        "in this conversation as their BUSINESS profile image. There is no image argument: the most "
        "recent generated image is used automatically, and the tool reports when there is none. "
        "Call it ONLY after the provider explicitly agrees to use the image; it updates the business "
        "profile image immediately, so never call it just to ask. If the tool reports it could not "
        "set the image, tell the provider that; never claim the profile image changed."
    ),
    args_schema=SetProfileImageFromChat
)
async def set_profile_image_from_chat(
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Set the last generated image as the provider's business profile image after explicit approval; providers only.

    Args:
        tool_call_id: LangGraph injected tool execution identifier.
        state: LangGraph injected agent state carrying the generated image.
        config: LangGraph configuration with execution parameters.

    Returns:
        Command: LangGraph command with the result message and no UI payload.
    """
    contact_id = get_context_value("contact_id", None, config, state)
    user_profile_id = get_context_value("user_profile_id", None, config, state)
    user_role = get_context_value("user_role", None, config, state) or 2

    if user_role != 1:
        error_msg = (
            "Setting a business profile image is only available for provider accounts. "
            "Please switch to your provider account to change your business image."
        )
        return Command(
            update={
                "messages": [ToolMessage(error_msg, tool_call_id=tool_call_id)],
                "implicit_output": None,
                "response_type": None
            }
        )

    last_image = (
        get_context_value("last_generated_image", None, config, state)
        or get_context_value("restored_generated_image", None, config, state)
    )

    result_text, updated = await handle_set_profile_image_from_chat(
        contact_id=contact_id,
        userprofile_id=user_profile_id,
        last_image=last_image,
    )

    log_agent_decision(
        'provider_agent',
        'set_profile_image_from_chat',
        {
            'has_image': isinstance(last_image, dict) and bool(last_image.get("blob_path")),
            'success': updated,
            'user_context': {
                'contact_id': contact_id,
                'user_profile_id': user_profile_id,
            }
        }
    )

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
            "implicit_output": None,
            "response_type": None
        }
    )


@tool(
    name_or_callable="remember_preference",
    description=(
        "Save a lasting fact about the user so it is available in future conversations. Use "
        "this ONLY when the user asks to be remembered ('remember that...', 'note that...', "
        "'save this for next time'), or states a standing preference or constraint that will "
        "still be true weeks from now: diet, allergies, household, a labelled address, an "
        "appliance or vehicle they own, a provider they always use, a recurring need, how they "
        "want to be contacted. NEVER use it for anything about right now - the current date, a "
        "single booking's time or route, what they are searching for or craving, or a question "
        "they just asked. Ordinary conversation is already remembered automatically, so calling "
        "this for a passing remark clutters the user's memory with noise. Pass the fact itself, "
        "not the request: 'Is vegetarian', never 'Wants to be remembered as vegetarian'. Call it "
        "once per fact rather than bundling several into one sentence."
    ),
    args_schema=RememberPreference
)
async def remember_preference(
    fact: str,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Store a lasting fact about the user in Chaatie memory and return the confirmation Command."""
    result = await handle_remember_preference(
        fact,
        contact_id=get_context_value("contact_id", None, config, state),
        user_profile_id=get_context_value("user_profile_id", None, config, state),
        conversation_id=get_context_value("conversation_id", None, config, state),
        user_name=get_context_value("user_name", None, config, state),
        language=get_context_value("language", None, config, state),
    )
    return Command(
        update={
            "messages": [ToolMessage(str(result), tool_call_id=tool_call_id)],
            "implicit_output": result,
            "response_type": "remember_preference"
        }
    )


@tool(
    name_or_callable="forget_preference",
    description=(
        "Delete something Chaatie has remembered about the user. Use this when the user asks to "
        "forget, remove or correct a stored detail ('forget that I...', 'I don't live there "
        "anymore', 'stop remembering...'). Pass their own wording of the detail. When the user "
        "is correcting a fact rather than only removing it, call this first and then "
        "remember_preference with the corrected version. If the result comes back with "
        "needs_confirmation, do NOT call this tool again on your own - put the candidate to the "
        "user and wait for their answer, then call it again with the candidate's exact wording."
    ),
    args_schema=ForgetPreference
)
async def forget_preference(
    description: str,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None
):
    """Delete a stored Chaatie memory matching the user's description and return the outcome Command."""
    result = await handle_forget_preference(
        description,
        contact_id=get_context_value("contact_id", None, config, state),
        user_profile_id=get_context_value("user_profile_id", None, config, state),
    )
    return Command(
        update={
            "messages": [ToolMessage(str(result), tool_call_id=tool_call_id)],
            "implicit_output": result,
            "response_type": "forget_preference"
        }
    )


@tool(
    name_or_callable="guestuser",
    description=(
        "GUEST ACCOUNT GATE: Call this whenever a guest user asks for anything beyond searching "
        "providers or a general answer - booking or ordering, enquiring with a provider, generating "
        "an image, creating a post or offer, invites, QR codes, support tickets, ratings, their "
        "bookings or requests, onboarding a business, or remembering details. Pass the feature they "
        "asked for in a few words."
    ),
)
async def guestuser(
    feature: str,
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = None,
    state: Annotated[Optional[dict], InjectedState] = None,
    config: Optional[RunnableConfig] = None,
):
    """Return a Command telling a guest that the requested feature needs a signed-in Etown account."""
    logger.info(f"[GUEST_USER] Blocked guest request for feature: {feature}")
    return Command(
        update={
            "messages": [ToolMessage(
                f"'{feature}' is not available to guest users. Tell the user in one or two friendly "
                "sentences that they need to sign in or register on Etown to use it, and that they "
                "can still search for providers as a guest.",
                tool_call_id=tool_call_id,
            )],
            "implicit_output": {"feature": feature},
            "response_type": "guestuser"
        }
    )


member_tools = [
    search_provider,
    recommend_products,
    request_provider,
    add_booking_instructions,
    enquire_provider,
    booknow_provider,
    provider_service_insights,
    address_confirmation,
    generate_qr,
    generate_image,
    create_post,
    create_post_from_chat,
    booking_info,
    cancel_booking,
    connect_support_agent,
    get_member_requests_list,
    provide_etown_info,
    internal_invite,
    external_invite_for_members,
    web_search,
    aionboarding_redirection,
    onboard_provider,
    pending_ratings,
    remember_preference,
    forget_preference,
    *([query_my_data] if settings.DATA_QUERY_MODE != "off" else []),
]

provider_tools = [
    address_confirmation,
    generate_qr,
    generate_image,
    create_post,
    create_post_from_chat,
    booking_info,
    cancel_booking,
    connect_support_agent,
    get_provider_requests_list,
    provide_etown_info,
    internal_invite,
    external_invite_for_providers,
    aionboarding_redirection,
    onboard_provider,
    create_offer_from_chat,
    set_profile_image_from_chat,
    web_search,
    remember_preference,
    forget_preference,
    *([query_my_data] if settings.DATA_QUERY_MODE != "off" else []),
]

guest_tools = [
    search_provider,
    provide_etown_info,
    guestuser
]

automation_tools = [
    automation_tester
]