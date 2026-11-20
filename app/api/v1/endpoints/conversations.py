"""Conversations Endpoints - HTTP routes for Chaatie history, titles, prompt suggestions and mobile lookup.

HTTP layer for /api/conversations: checks the chaatie access_key header or the shared bearer token and delegates history listing, turn insertion, title generation, prompt suggestions, conversation reads and deletes, and mobile lookup to ConversationService.

Features:
    - Conversation history: Chat message retrieval and pagination
    - Title generation: AI-powered conversation title creation
    - Prompt suggestions: Context-aware prompt recommendations
    - Mobile lookup: User verification and contact management
    - Conversation CRUD: Create, read and delete operations
    - User management: Multi-user conversation tracking
    - Access control: access_key header check and bearer token on prompt suggestions
    - Activity tracking: API calls recorded via track_api_call

Classes:
    No classes defined (request/response models are imported from app.schemas.api)

Endpoints:
    POST /prompt_suggestion: Generate contextual chat prompts
    POST /conversation_list: Retrieve paginated conversation history
    POST /title_generation: Create AI-generated conversation titles
    POST /insert_conversation: Save new conversation messages
    GET /conversations/{conversation_id}: Fetch specific conversation
    GET /users/{user_id}/conversations: Get user's conversation list
    DELETE /conversations/{conversation_id}: Remove conversation
    POST /etown_lookup: Verify mobile numbers; onboarding=true also returns business details

Functions:
    verify_access_key(): Validate API access credentials
    prompt_suggestion(): Generate intelligent conversation prompts
    conversation_list(): Retrieve paginated conversation history
    title_generation(): Create AI-powered conversation titles
    insert_conversation(): Save conversation messages to database
    get_conversation(): Fetch individual conversation details
    get_user_conversations(): Get all conversations for a user
    delete_conversation(): Remove conversation from database
    etown_lookup(): Verify and lookup user mobile information

Dependencies:
    - fastapi: HTTP framework and exception handling
    - app.core.security: Shared bearer token verification
    - app.core.config: chaatie_access_key setting
    - app.etown.conversations.conversation_utils: ConversationService business logic
    - app.services.api_activity: track_api_call activity logging
    - app.schemas.api: Request/response validation models

Updates:
    23-Feb-2026: Enhanced documentation with complete endpoint inventory
    06-Aug-2026: /etown_lookup accepts an optional onboarding flag returning business details.
    07-Aug-2026: onboarding=true no longer filters on publicsearchprovider.
    18-Aug-2026: Conversation read/delete routes require a bearer token.
    20-Aug-2026: Reverted the 18-Aug token requirement; onboarding lookup returns Etown_providertype.

Notes:
    Uses Azure OpenAI GPT-4.1 for intelligent title and prompt generation.
    Includes mobile phone verification for user authentication and security.
"""
import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException, status, Header, Depends
from app.schemas.api import ( 
    TitleGenerationRequest,
    PromptSuggestionRequest,
    LogRequest,
    MobileLookupRequest,
    MobileLookupResponse,
    ConversationListRequest,
    ConversationListResponse,
    )
from app.core.config import settings
from app.core.security import verify_token
from app.etown.conversations.conversation_utils import ConversationService
from app.services.api_activity import track_api_call

logger = logging.getLogger(__name__)
router = APIRouter()


async def verify_access_key(access_key: str = Header(default=None)):
    """Return the access_key header, or raise 403 when it does not match the Chaatie access key."""
    if access_key != settings.chaatie_access_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid access key")
    return access_key

@router.post("/prompt_suggestion")
async def prompt_suggestion(
    config: PromptSuggestionRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token)
):
    """Handle POST /api/conversations/prompt_suggestion: generate LLM prompt suggestions from conversation context."""
    track_api_call(
        background_tasks,
        "/api/conversations/prompt_suggestion",
        config.contact_id,
        config.user_profile_id,
    )
    try:
        return await ConversationService.generate_prompt_suggestions(config)
    except Exception as e:
        logger.error(f"Error generating prompt suggestions: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate prompt suggestions: {str(e)}"
        )

@router.post("/conversation_list", response_model=ConversationListResponse)
async def conversation_list(
    request: ConversationListRequest,
    access_key: str = Depends(verify_access_key)
):
    """Handle POST /api/conversations/conversation_list: return a page of a user's conversations with cached titles."""
    try:
        return await ConversationService.get_conversation_list_with_titles(
            contact_id=request.contact_id,
            user_profile_id=request.user_profile_id,
            page=request.page,
            limit=request.limit
        )
    except Exception as e:
        logger.error(f"Error fetching conversation list: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch conversation list: {str(e)}"
        )

@router.post("/title_generation")
async def title_generation(
    config: TitleGenerationRequest,
    access_key: str = Depends(verify_access_key)
):
    """Handle POST /api/conversations/title_generation: generate a conversation title with the LLM."""
    try:
        return await ConversationService.generate_conversation_title(config)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating title: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate title: {str(e)}"
        )


@router.post("/insert_conversation")
async def insert_conversation(
    log_request: LogRequest,
    access_key: str = Depends(verify_access_key)
):
    """Handle POST /api/conversations/insert_conversation: store a conversation message and return its id."""
    try:
        return await ConversationService.insert_conversation_message(log_request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error inserting conversation: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to insert conversation: {str(e)}"
        )


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, limit: int = 50):
    """Handle GET /api/conversations/conversations/{conversation_id}: return up to limit messages of a conversation."""
    try:
        return await ConversationService.get_conversation(conversation_id, limit)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving conversation: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve conversation: {str(e)}"
        )


@router.get("/users/{user_id}/conversations")
async def get_user_conversations(user_id: str, limit: int = 10):
    """Handle GET /api/conversations/users/{user_id}/conversations: return up to limit conversation summaries for a user."""
    try:
        return await ConversationService.get_user_conversations(user_id, limit)
    except Exception as e:
        logger.error(f"Error retrieving user conversations: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve user conversations: {str(e)}"
        )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Handle DELETE /api/conversations/conversations/{conversation_id}: delete an entire conversation."""
    try:
        return await ConversationService.delete_conversation(conversation_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting conversation: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete conversation: {str(e)}"
        )

@router.post("/etown_lookup", response_model=MobileLookupResponse)
async def etown_lookup(
    request: MobileLookupRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token)
):
    """Handle POST /api/conversations/etown_lookup: return registered numbers from a list, plus provider business details when onboarding."""
    track_api_call(background_tasks, "/api/conversations/etown_lookup")
    try:
        return await ConversationService.lookup_mobile_numbers(request)
    except Exception as e:
        logger.error(f"Error in bulk mobile lookup: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Bulk lookup failed: {str(e)}")