"""Tool State Schemas - Pydantic argument models for the Chaatie LangGraph tools.

Schema module defining one Pydantic args model per agent tool in tools.py, with field descriptions the LLM reads and injected tool-call id and runtime config fields for LangGraph.

Features:
    - Input Validation: Strict type checking and field validation for all tool inputs
    - Schema Documentation: Comprehensive field descriptions for agent understanding
    - State Injection: Integration with LangGraph state management system
    - Tool Call Tracking: Automatic tool call ID injection for execution tracing
    - Configuration Propagation: Runtime configuration passing through tool calls
    - Default Values: Sensible defaults for optional parameters
    - Field Constraints: Validation rules for numeric ranges, string patterns, and enums
    - Multilingual Support: Text field support for Tamil, Hindi, and English content

Classes:
    SearchProvider: Schema for business/service/product search operations
    RecommendProducts: Schema for hybrid product-recommendation operations
    RequestProvider: Schema for service/product request creation with confirmation
    BookNowProvider: Schema for direct business booking by exact name
    ProviderServiceInsights: Schema for detail/pricing/distance lookups on a known provider service
    AddBookingInstructions: Schema for attaching free-text instructions to an already-placed booking
    EnquireProvider: Schema for sending a member's free-text enquiry to a provider's chat thread
    ConnectSupportAgent: Schema for customer support connection requests
    GetProviderRequestList: Schema for provider request list retrieval with filtering
    GetMemberRequestList: Schema for member request history with tracking ID support
    ProvideEtownInfo: Schema for platform information and help queries
    BookingInfo: Schema for booking detail retrieval by tracking ID
    CancelBooking: Schema for cancelling a booking by tracking ID with an optional reason
    InternalInvite: Schema for inviting existing Etown platform users
    WebSearch: Schema for real-time information retrieval using web search
    ExternalInviteMembers: Schema for sending invitations to new member users
    ExternalInviteProviders: Schema for sending invitations to new provider users
    CreateOfferFromChat: Schema for provider offer creation via chat conversation
    SetProfileImageFromChat: Schema for setting the last generated image as the business image
    CreatePostFromChat: Schema for feed post creation via chat conversation (members and providers)
    GenerateImage: Schema for AI image generation with image_type, refine_previous and size/steps/seed controls
    OnboardProvider: Schema for onboarding a new provider business from chat-collected fields
    RememberPreference: Schema for storing one third-person fact about the user in memory
    ForgetPreference: Schema for forgetting a remembered detail described in the user's words
    QueryMyData: Schema for a counting or ranking question about the caller's own Etown activity

Functions:
    No standalone functions (pure schema definition module)

Dependencies:
    - pydantic: Data validation and settings management using Python type hints
    - typing: Type hints for complex data structures and optional fields
    - langchain_core: LangGraph integration for configuration and state management
    - langgraph: Tool call ID injection and state propagation

Updates:
    27-Feb-2026: Rewrote the module documentation.
    12-Mar-2026: Added search_scope to InternalInvite; added WebSearch schema.
    23-Jun-2026: Added CreatePostFromChat schema.
    20-Jul-2026: Added RecommendProducts schema.
    06-Aug-2026: Added the EnquireProvider schema for the off-catalog Enquire branch.
    28-Sep-2026: Added SetProfileImageFromChat schema.
    29-Sep-2026: GenerateImage gains refine_previous.
    27-Oct-2026: Added QueryMyData schema.
"""

from pydantic import BaseModel, Field
from typing import Annotated, List, Literal, Optional, Union

from langchain_core.runnables import RunnableConfig
from langchain_core.tools.base import InjectedToolCallId
from langgraph.prebuilt import InjectedState


class SearchProvider(BaseModel):
    search_term: str = Field(
        description=(
            "What to search for, in the fewest words that name it: the trade, service, item or "
            "business. Pass ONLY what the user wants - never words they ruled out ('not "
            "repair', 'not catering'), and never two needs joined together ('electrician and "
            "sofa cleaning'), which matches no listing. One need per call; call the tool again "
            "for the next need."
        )
    )
    search_category: Literal["service", "product", "business"] = Field(
        default="business",
        description=(
            "Restricts the search to listings the PROVIDER tagged as a service or a product. "
            "It is a blunt filter, not an intent signal: most providers pick only one tag even "
            "when they do both, so a wrong guess hides half the businesses on Etown. Set "
            "'product' or 'service' ONLY when the user's words leave no doubt ('I want to buy a "
            "washing machine' -> product; 'washing machine is leaking' -> service). Otherwise "
            "leave it on 'business', which applies no filter. To steer buying vs. repair, word "
            "the search_term ('buy washing machine' vs 'washing machine repair') - that is what "
            "actually decides which trade is retrieved."
        ),
    )
    years_in_business: int = Field(
        default=0,
        description="The minimum experience of providers to include in the results.", 
        ge=0
        )
    limit: int = Field(
        default=10,
        description="The maximum number of results to return.",
        ge=1
        )
    offset: int = Field(
        default=0,
        description="The number of results to skip before starting to collect the result set. Increase by 10 each time.",
        ge=0
    )
    max_distance_meters: float = Field(
        default=25000,
        description="The maximum distance (in meters) from the user's location for service searches. Optional and applicable only for services."
        )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class RecommendProducts(BaseModel):
    search_term: str = Field(
        description="The product or item the user wants to buy or get recommendations for (e.g. 'ac', 'air conditioner', 'running shoes'). Pass the core item the user mentioned in their own words — the tool automatically expands it into related and complementary product types before searching the catalog. Do NOT pass a business/provider name here."
    )
    limit: int = Field(
        default=10,
        description="The maximum number of product recommendations to return.",
        ge=1,
        le=25
    )
    max_distance_meters: float = Field(
        default=25000,
        description="The maximum distance (in meters) from the user's location to source products from."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class RequestProvider(BaseModel):
    provider_item_id: str = Field(description="The unique identifier (UUID) of the service or product. CRITICAL: Extract the 'providerservicesid' field from the search results shown to the user. When user says 'book the first one' or 'book provider X', find that provider in the previous search results and use its exact 'providerservicesid' value. Never generate, guess, or hallucinate this ID - it must match exactly what was shown in search results. For MULTI-ITEM orders (using provider_item_ids), set this to the FIRST UUID from provider_item_ids — it satisfies the required field constraint while actual per-item bookings are driven by provider_item_ids.")
    service_datetime: Optional[str] = Field(
        default=None,
        description="When the user wants this, as 'YYYY-MM-DD HH:MM' (24-hour), any time after the current time. MANDATORY for EVERY booking — a product order needs one exactly as much as a service call, because the provider has a slot to meet either way. Pass ONLY a date and time the user has actually stated in this conversation. NEVER invent one, never infer one from the phrasing, and never copy the 'Current Date & Time (IST)' shown in your context — that is the clock, not their preference. When they have not said when they want it, leave this out and ask them; the booking is refused without it, so a guessed value is worse than none.",
    )
    user_address: str = Field(description="The user's address where service should be provided")
    detailed_request: str = Field(description="General notes or instructions for the service request. Do NOT include product/service names or quantities here — use provider_item_ids and quantities fields for those.")
    first_name: str = Field(description="User's first name")
    provider_type: Literal["verified", "non_verified"] = Field(
        default="verified",
        description="Type of provider: 'verified' or 'non_verified'"
    )
    provider_category: Literal["service", "product"] = Field(
        default="service",
        description="Category of the request: 'service' or 'product'. Copy it from the chosen listing's own `category`/`businesscategory` field in the search, recommend_products or booknow results; leave the 'service' default when the results do not say. Do NOT infer it from the phrasing of the request — a message listing items with quantities is not evidence of a product. This field has NO bearing on whether a date and time are needed: every booking needs `service_datetime`, product orders included, so 'product' will never get a booking through without the date and time the user asked for."
    )
    quantity: int = Field(
        default=1,
        description="Number of units for SINGLE-service requests only. LEAVE AT 1 for multi-item orders — the real per-item quantities must go in the `quantities` list instead. Only set this above 1 when there is exactly one item being ordered (provider_item_ids is empty)."
    )
    provider_item_ids: Optional[List[str]] = Field(
        default=None,
        description="List of provider service UUIDs when the user selects multiple services at once. Each ID must be a valid 'providerservicesid' from search results. Use together with the quantities field to specify a quantity for each service. Leave empty for single-service requests (use provider_item_id instead)."
    )
    quantities: Optional[List[int]] = Field(
        default=None,
        description="Per-item quantities parallel to provider_item_ids. Index 0 is the quantity for provider_item_ids[0], index 1 for provider_item_ids[1], and so on. Must be the same length as provider_item_ids. Example: provider_item_ids=['uuid-A','uuid-B'], quantities=[3,2] means 3 of service A and 2 of service B. Only used with provider_item_ids (multi-service requests)."
    )
    distance_confirmed: bool = Field(
        default=False,
        description="Set to True ONLY after the user has explicitly confirmed they still want to book a provider that is beyond the 25 km service radius. Leave False (the default) on the first booking attempt. When the tool reports that a provider is beyond 25 km, relay the exact distance to the user and ask them to confirm; if they say yes, call request_provider again with the same details and distance_confirmed=True to complete the booking. Never set this to True without an explicit user confirmation."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class BookNowProvider(BaseModel):
    search_term: str = Field(description="The EXACT name of the business as provided by the user, including any regional language text (Tamil, Hindi, etc.). DO NOT translate or modify the business name. Use the original text exactly as the user wrote it.")
    additional_details: str = Field(
        default="",
        description="Everything else the user already told you about this booking, in their own words — what they want done, dates, times, pickup/drop locations, quantities, and any constraints. ALWAYS pass this when the user's message carried any of it (e.g. 'Wednesday of this week, Kovilpatti to samithoppu and kurangani and return, starting early morning'). Leaving it empty makes the assistant ask the user to repeat what they already said."
    )
    name_confirmed: bool = Field(
        default=False,
        description="Set to true ONLY when you previously asked 'Did you mean <business>?' and the user has since said yes. It tells the tool the identity question is already settled, so it returns that business ready to book instead of asking again. Without it the same question repeats every turn and the booking can never complete."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class ProviderServiceInsights(BaseModel):
    providerservicesid: str = Field(
        default="",
        description="The provider service ID from previous search/booknow results. Prefer this exact ID for follow-up detail queries like 'tell me more about this provider'. Never generate or guess IDs."
    )
    business_name: str = Field(
        default="",
        description="Fallback business name for direct detail lookup when providerservicesid is not available."
    )
    match_limit: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of matches to return when business name lookup is ambiguous."
    )
    purpose: str = Field(
        default="",
        description="Set to 'enquiry' when you are resolving this provider only to OPEN an enquiry (e.g. the member said 'I want to enquire about <business> services'). The tool then returns a short brief for asking the member what they want, instead of the provider's full details. Leave empty for every normal detail, contact, pricing or distance question."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class AddBookingInstructions(BaseModel):
    instructions: List[str] = Field(
        description="One or more free-text instructions the user wants attached to a booking they have ALREADY placed (e.g. 'ring the doorbell twice', 'bring a ladder', 'call before arriving'). Put each distinct instruction as a separate string in the list. Only use this AFTER a booking has been submitted and the user volunteers extra instructions — never ask the user for instructions while taking the booking."
    )
    booking_reference: Optional[str] = Field(
        default=None,
        description="Optional booking tracking ID (e.g. 'ETRQ12345') that was shown to the user when the booking was placed. Provide it when the user clearly refers to a specific booking. Leave empty to attach the instructions to the user's most recent booking."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class EnquireProvider(BaseModel):
    message: str = Field(
        description="The member's enquiry, in their own words, sent to the provider as a chat message (e.g. 'Do you make eggless black forest cakes for 15 people?'). Include the specifics they gave — what they want, quantity, and any timing they mentioned. Do NOT invent details the member did not state."
    )
    provider_identifier: str = Field(
        default="",
        description="The provider to send the enquiry to — pass the exact 'providerservicesid' (UUID) from the previous results whenever it is available. Never generate, guess, or hallucinate this ID. Only if no ID is available, pass the provider's business name EXACTLY as shown and nothing else: no service words, no 'at home', no description of the question (e.g. 'Chen Dental Clinic', never 'Chen Dental Clinic teeth cleaning at home'). Leave empty when the conversation is already about a single provider."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class ConnectSupportAgent(BaseModel):
    reason_for_contact: str = Field(description="The reason for connecting to a support agent.")


class GetProviderRequestList(BaseModel):
    start_time: Optional[str] = Field(default=None, description="Start time for filtering requests (format: HH:MM)")
    end_time: Optional[str] = Field(default=None, description="End time for filtering requests (format: HH:MM)")
    date: Optional[str] = Field(default=None, description="Date for filtering requests (format: YYYY-MM-DD)")
    first_count: Optional[int] = Field(default=None, description="First count for pagination", ge=1)
    last_count: Optional[int] = Field(default=None, description="Last count for pagination", ge=1)
    page_number: int = Field(default=0, description="Page number for pagination (0-indexed)", ge=0)
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class GetMemberRequestList(BaseModel):
    request_tracking_id: Optional[str] = Field(default=None, description="Tracking ID to filter specific request")
    start_time: Optional[str] = Field(default=None, description="Start time for filtering requests (format: HH:MM)")
    end_time: Optional[str] = Field(default=None, description="End time for filtering requests (format: HH:MM)")
    date: Optional[str] = Field(default=None, description="Date for filtering requests (format: YYYY-MM-DD)")
    first_count: Optional[int] = Field(default=None, description="First count for pagination", ge=1)
    last_count: Optional[int] = Field(default=None, description="Last count for pagination", ge=1)
    page_number: int = Field(default=0, description="Page number for pagination (0-indexed)", ge=0)
    recent_only: bool = Field(default=False, description="Set to True for 'recent booking' queries, False for 'list of bookings'")
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class ProvideEtownInfo(BaseModel):
    etown_query: str = Field(description="The user's question about Etown features, services, or information")
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class BookingInfo(BaseModel):
    booking_id: Optional[str] = Field(
        default=None,
        description="The tracking ID (e.g. 'ETRQ12345') or internal booking ID of the booking to retrieve. Leave empty to retrieve the user's most recent booking. NEVER invent or guess this value — pass only a reference the user actually gave you, otherwise leave it empty."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class CancelBooking(BaseModel):
    booking_reference: Optional[str] = Field(
        default=None,
        description="The tracking ID (e.g. 'ETRQ12345') or booking ID of the booking to cancel. Leave empty to cancel the user's most recent booking. NEVER invent this value — pass only a reference the user actually gave you."
    )
    cancel_reason: Optional[str] = Field(
        default=None,
        description="The reason the user gave for cancelling, in their own words. Leave empty if they did not give one — do not invent a reason."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class InternalInvite(BaseModel):
    search_term: Union[str, List[str]] = Field(
        description="Name of the person to invite. If the user asks to invite MULTIPLE people in one request, pass a list of all the names here in a single call instead of calling the tool once per name."
    )
    role: Literal["Member", "Provider"] = Field(
        default="Member",
        description="Role assigned to the invitee: 'Member' or 'Provider'."
        )
    action: Literal["search", "send"] = Field(
        default="search", 
        description="Action to perform: 'search' to find a user, 'send' to send the invite."
    )
    search_scope: Literal["all", "phonebook_only"] = Field(
        default="all",
        description="Search scope: 'all' to search Etown database, 'phonebook_only' when user wants to invite from their phone contacts/phonebook."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class WebSearch(BaseModel):
    search_query: str = Field(description="Search query for real-time information retrieval. Use this for current events, recent developments, weather updates, and other live information not available in the agent's training data.")
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class ExternalInviteMembers(BaseModel):
    invitee_name: str = Field(description="Name of the invited user. This is a mandatory field.")
    invitee_email: str = Field(description="The email address to send the invite. Either email or mobile_number must be provided.")
    invitee_mobile_number: str = Field(description="The mobile number to send the invite. Either email or mobile_number must be provided.")
    invitee_role: Literal["Member", "Provider"] = Field(
        default="Member",
        description="Role assigned to the invitee: 'Member' or 'Provider'."
        )
    invite_confirmation : str = Field(
        default="Not Confirmed",
        description="User confirmation to proceed with sending the invite. This is a mandatory field."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)

class ExternalInviteProviders(BaseModel):
    invitee_name: str = Field(description="Name of the invited user. This is a mandatory field.")
    invitee_email: str = Field(description="The email address to send the invite. Either email or mobile_number must be provided.")
    invitee_mobile_number: str = Field(description="The mobile number to send the invite. Either email or mobile_number must be provided.")
    invitee_role: Literal["Member", "Provider"] = Field(
        default="Provider",
        description="Role assigned to the invitee: 'Member' or 'Provider'."
        )
    invite_confirmation : str = Field(
        default="Not Confirmed",
        description="User confirmation to proceed with sending the invite. This is a mandatory field."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class CreateOfferFromChat(BaseModel):
    description: str = Field(
        description="The offer body text describing the promotion/deal (e.g. '20% off all cakes this weekend'). REQUIRED — collect from the provider before calling this tool. Use the provider's own wording; do not invent details."
    )
    title: Optional[str] = Field(
        default=None,
        description="Optional short headline for the offer (e.g. 'Weekend Cake Sale')."
    )
    validity: Optional[str] = Field(
        default=None,
        description="Optional validity status for the offer. Leave empty to default to 'Valid'."
    )
    scheduled_time: Optional[str] = Field(
        default=None,
        description="Optional ISO 8601 datetime for when the offer should go live. Leave empty to post immediately."
    )
    image_url: Optional[str] = Field(
        default=None,
        description="Usually leave EMPTY. If the user generated an image for this offer with generate_image (image_type='offer') earlier in the conversation, the most recent generated image is attached automatically on confirm — you do NOT need to set this. Only pass an explicit stored image URL/path here to override that automatic attachment."
    )
    action: Literal["draft", "confirm"] = Field(
        default="draft",
        description="Two-step flow: use 'draft' FIRST to assemble the offer and show it to the provider for review. Only use 'confirm' AFTER the provider explicitly approves (says 'yes', 'post it', 'confirm', etc.). Never use 'confirm' without an assembled draft the provider has approved."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class SetProfileImageFromChat(BaseModel):
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class CreatePostFromChat(BaseModel):
    post_text: str = Field(
        description="The text content of the post (e.g. 'Excited to announce our new service!'). REQUIRED — collect from the user before calling this tool. Use the user's own wording; do not invent details."
    )
    image_url: Optional[str] = Field(
        default=None,
        description="Usually leave EMPTY. If the user generated an image for this post with generate_image (image_type='feed') earlier in the conversation, the most recent generated image is attached automatically on confirm — you do NOT need to set this. Only pass an explicit stored image URL/path here to override that automatic attachment."
    )
    action: Literal["draft", "confirm"] = Field(
        default="draft",
        description="Two-step flow: use 'draft' FIRST to show the user a preview of their post. Only use 'confirm' AFTER the user explicitly approves (says 'yes', 'post it', 'confirm', etc.). Never use 'confirm' without showing a draft the user has approved."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class GenerateImage(BaseModel):
    prompt: str = Field(
        description="The user's description of the image they want generated, in their own words (e.g. 'a cat riding a bicycle', 'a modern logo for my bakery'). Pass the raw user request — the tool automatically enhances it into a rich, detailed image-generation prompt before calling the model. Do NOT pre-expand or embellish it yourself."
    )
    image_type: Literal["logo", "offer", "feed", "general"] = Field(
        default="general",
        description=(
            "The kind of image the user wants, used to pick the right enhancement style. "
            "Choose 'logo' for a business/brand logo (e.g. 'make a logo for my bakery'). "
            "Choose 'offer' for a promotional/deal/sale advertisement poster (e.g. 'create an offer post for 20% off'). "
            "Choose 'feed' for a general social-media feed/announcement post (e.g. 'make a post announcing our new branch'). "
            "Choose 'general' for any other image that is not a logo, offer, or feed post (e.g. 'draw a sunset', 'a cat astronaut'). "
            "Infer this from the user's request and conversation context."
        )
    )
    width: int = Field(
        default=1024,
        ge=512,
        le=1280,
        description="Output image width in pixels (512-1280). Defaults to 1024. Use a larger width than height for landscape/wide images, and vice versa for portrait."
    )
    height: int = Field(
        default=1024,
        ge=512,
        le=1280,
        description="Output image height in pixels (512-1280). Defaults to 1024."
    )
    steps: Optional[int] = Field(
        default=None,
        ge=1,
        le=50,
        description="Number of diffusion steps. Leave empty — the model server applies the value tuned for the loaded model, and overriding it usually makes the image worse, not better. Only set it if the user explicitly asks for more or fewer steps."
    )
    seed: Optional[int] = Field(
        default=None,
        description="Optional integer seed for reproducible results. Leave empty for a random image each time. Only set if the user explicitly asks to reproduce or vary a previous generation."
    )
    refine_previous: bool = Field(
        default=False,
        description=(
            "True when the user asks to change the image just generated (add, remove, recolour, fix spelling, "
            "adjust style): the tool keeps that design and seed and applies only the change. False for a new "
            "image or when the user asks for more or different options."
        )
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)


class OnboardProvider(BaseModel):
    businessname: str = Field(
        description="The business or shop name. REQUIRED — collect from user before calling this tool."
    )
    mobilenumber: str = Field(
        description="Provider's mobile phone number (8-15 digits). REQUIRED — collect from user before calling this tool."
    )
    description: Optional[str] = Field(
        default=None,
        description="Short description of the business and services offered."
    )
    city: Optional[str] = Field(
        default=None,
        description="City where the business operates."
    )
    area_location: Optional[str] = Field(
        default=None,
        description="Area or locality name within the city (e.g. Anna Nagar, Banjara Hills)."
    )
    provider_state: Optional[str] = Field(
        default=None,
        description="Indian state where the business is located (e.g. Tamil Nadu, Maharashtra)."
    )
    business_category: Optional[str] = Field(
        default=None,
        description="Primary business category (e.g. Plumbing, Electrical, Catering, Tailoring, Salon)."
    )
    service_category: Optional[str] = Field(
        default=None,
        description="Sub-category or specific service type within the business category."
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)

class RememberPreference(BaseModel):
    fact: str = Field(
        description=(
            "The fact itself, phrased as a short third-person statement about the user with no "
            "dates in it. State what is TRUE of them, never the fact that they asked you to "
            "remember it: 'Is vegetarian', NOT 'Wants to be remembered as vegetarian'; 'Has two "
            "children', NOT 'Asked to note they have two children'. Never begin with 'Wants to "
            "be remembered', 'Asked to remember' or 'Would like noted' -- that phrasing buries "
            "the fact and it stops being findable later. One fact per call: for 'remember I am "
            "vegetarian and I never eat onion', call this twice, with 'Is vegetarian' and "
            "'Never eats onion'. More examples: 'Home address is 12 Second Street, Anytown', "
            "'Prefers appointments before 10am', 'Has a 1.5 ton split AC'."
        )
    )


class ForgetPreference(BaseModel):
    description: str = Field(
        description="The detail the user wants forgotten, in their own words."
    )


class QueryMyData(BaseModel):
    question: str = Field(
        description=(
            "The user's question about their OWN Etown activity, restated in English as one "
            "self-contained question that keeps every period, status, business or category they "
            "mentioned, including details from earlier turns when they are following up. "
            "Example: 'How many bookings did I complete in September 2026?'"
        )
    )
    tool_call_id: Annotated[Optional[str], InjectedToolCallId] = Field(default=None)
    state: Annotated[Optional[dict], InjectedState] = Field(default=None)
    config: Optional[RunnableConfig] = Field(default=None)
