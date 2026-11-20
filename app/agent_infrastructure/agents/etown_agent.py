"""Chaatie Agents - builds the member, provider, guest and automation LangGraph agents.

Agent layer for Chaatie: creates four create_agent() instances on gpt41, each with its own role-specific tool list and the dynamic_prompt and tool_loop_guard middleware, and generates the per-turn system prompt from the runtime context.

Features:
    - Role-based Agents: Separate agents for member, provider, guest, and automation workflows
    - Guest Restriction: Guests get search and general answers only; other features go through guestuser
    - Dynamic Prompt Generation: Context-aware system prompt creation with user personalization
    - Tool Integration: Comprehensive tool sets with role-specific access controls
    - Anti-repetition Logic: Prompt rules plus tool_loop_guard middleware stop repeated tool calls
    - Context Management: Reads user name, address, time, memories and role from runtime context
    - Response Optimization: search_provider text is relayed verbatim; bracketed notes stay hidden
    - Draft-then-Confirm Posting: Post and offer tools must show a draft before confirming
    - Automation Testing: Specialized agent for automated testing scenario orchestration
    - Middleware Support: dynamic_prompt and tool_loop_guard middleware on each agent
    - State Management: Structured state and context schemas for conversation tracking
    - Multi-language Support: User language is passed into the prompt template

Classes:
    No classes defined (builds four LangGraph agents via create_agent)

Functions:
    chaatie_system_prompt(): Dynamic system prompt generator for member/provider agents
    automation_system_prompt(): System prompt generator for the automation testing agent

Dependencies:
    - app.schemas.langraph_agent_states: EtownAgentState / EtownAgentContext schema definitions
    - langchain.agents: create_agent() used to build each LangGraph agent instance
    - app.agent_infrastructure.tools.tools: Tool registry and role-based tool sets
    - app.agent_infrastructure.infrastructure.llm_clients: gpt41 (Azure GPT-4.1 deployment) model client
    - langchain.agents.middleware: dynamic_prompt decorator and ModelRequest handling
    - app.agent_infrastructure.prompt_templates.templates: Base prompt template system
    - app.agent_infrastructure.agents.loop_guard: Repeated-tool-call guard middleware
    - app.core.config.settings: DATA_QUERY_MODE, which decides the own-data prompt section

Updates:
    11-Mar-2026: Added comprehensive module docstring and improved function documentation
    12-Aug-2026: Role differences are now enforced in the prompt each agent is built from.
    07-Sep-2026: Passes provider_affinity from the runtime context into the prompt template.
    23-Sep-2026: Multi-search turns get a fixed reply shape; a store-type search names its items; rewordings count as repeats.
    13-Oct-2026: Added the guest agent, the guest restriction prompt and the app install link reply.
    27-Oct-2026: Prompt selects the query_my_data section when DATA_QUERY_MODE is on for a signed-in user.

"""

from app.schemas.langraph_agent_states import EtownAgentState, EtownAgentContext
from langchain.agents import create_agent
from app.agent_infrastructure.tools.tools import member_tools, provider_tools
from app.agent_infrastructure.infrastructure.llm_clients import gpt41

from langchain.agents.middleware import dynamic_prompt, ModelRequest
from app.agent_infrastructure.prompt_templates.templates import chaatie_agent_prompt_template
from app.agent_infrastructure.tools.tools import member_tools, provider_tools, guest_tools, automation_tools
from app.agent_infrastructure.agents.loop_guard import tool_loop_guard
from app.core.config import settings

@dynamic_prompt
def chaatie_system_prompt(request: ModelRequest) -> str:
    """Build the Chaatie system prompt from runtime context, adding role-specific rules for providers or members."""
    user_name = request.runtime.context.get("user_name", "User")
    address = request.runtime.context.get("address", "")
    time = request.runtime.context.get("time", "")
    language = request.runtime.context.get("language", "en")
    memories = request.runtime.context.get("memories", "")
    provider_affinity = request.runtime.context.get("provider_affinity", "")
    user_role = request.runtime.context.get("user_role", 2)
    data_query_enabled = settings.DATA_QUERY_MODE != "off" and not request.runtime.context.get("is_guest")
    base_prompt = chaatie_agent_prompt_template(
        user_name, address, time, language, memories, provider_affinity, user_role, data_query_enabled
    )

    provider_booking_constraint = ""
    if user_role == 1:
        provider_booking_constraint = (
            "\n\n## PROVIDER ACCOUNT RESTRICTION"
            "\nYou are assisting a PROVIDER account. Providers CANNOT search for other providers, browse services, or place bookings."
            "\nIf the user asks to search, find, browse, book, order, or request any service or product from another provider, "
            "you MUST decline and inform them: 'Searching for services and placing bookings is only available for member accounts. "
            "Please switch to your member account to search or book a service.'"
            "\nDo NOT initiate any search, booking workflow, address confirmation, or scheduling steps for a provider account."
            "\nAnswer that decline directly, with NO tool call. There is no tool in your list that can "
            "search or book, so no tool call can help — reaching for the closest-looking one only makes "
            "the user wait."
            "\n\n## POSTING OFFERS"
            "\nWhen the provider wants to advertise, promote, post, or share an offer or deal, use the 'create_offer_from_chat' tool."
            "\n1. First, make sure you have the offer DESCRIPTION (the deal/promotion text). Ask for it if the provider hasn't given it. An optional short title is welcome."
            "\n2. Call the tool with action='draft' to assemble a preview. Show the preview to the provider and ask them to confirm."
            "\n3. ONLY after the provider explicitly approves (e.g. 'yes', 'post it', 'confirm'), call the tool again with action='confirm' to post it."
            "\nNEVER call action='confirm' without first showing a draft the provider approved. The offer is shared with the provider's Circle. Do not invent the business name or offer details."
            "\n\n## OFFER EDITING"
            "\nEtown does NOT support editing or updating an offer once it has been published. If the provider asks to edit, modify, update, or change a posted offer:"
            "\n- Inform them clearly that editing posted offers is not supported on the platform."
            "\n- Explain that they can post a new offer with the corrected details once their 24-hour posting window resets."
            "\n- Use the 'create_offer_from_chat' tool (action='draft') if they want to create the corrected offer right now and their rate limit allows it."
            "\n- NEVER say that editing is possible or suggest workarounds that involve editing. Keep the message honest and helpful."
        )
    
    post_creation_instruction = (
        "\n\n## CREATING POSTS"
        "\nWhen the user wants to create, write, share, or publish a post on their feed, use the 'create_post_from_chat' tool."
        "\n1. First, make sure you have the POST TEXT. Ask for it if the user hasn't provided it."
        "\n2. Call the tool with action='draft' to assemble a preview. Show the preview to the user and ask them to confirm."
        "\n3. ONLY after the user explicitly approves (e.g. 'yes', 'post it', 'confirm'), call the tool again with action='confirm' to publish."
        "\nNEVER call action='confirm' without first showing a draft the user approved. The post is shared with the user's Circle. Do not invent post content."
    )

    constraint = (
        "\n\nIMPORTANT OPERATIONAL RULES:"
        "\n1. NO REPEATED SEARCHES: If you have already performed a search (e.g., 'search_provider') in this conversation "
        "and found results, DO NOT use the search tool again for the same query unless the user explicitly changes the criteria. "
        "Use the existing results from the history to answer or proceed to booking. "
        "This forbids repeating ONE query, not searching for a second, different thing: a message "
        "naming two needs gets one search per need in the same turn. Rewording a need you already "
        "searched ('cake shop' then 'bakery', 'decoration' then 'balloon decoration') is the same "
        "query, not a new need."
        "\n2. TRUST YOUR TOOLS: If a tool successfully returned results in a previous turn, those results are valid. "
        "Do not re-verify them by searching again."
    )
    if user_role == 1:
        constraint = (
            "\n\nIMPORTANT OPERATIONAL RULES:"
            "\n1. NEVER REPEAT A TOOL CALL: If you have already called a tool with the same arguments in "
            "this turn, do NOT call it again. The result will be identical. Answer the user with what you "
            "have, or tell them plainly that you cannot do what they asked."
            "\n2. TRUST YOUR TOOLS: If a tool returned a result, that result is valid. Do not re-verify it "
            "by calling the tool again."
        )

    search_provider_constraint = "" if user_role == 1 else (
        "\n\nSEARCH PROVIDER TOOL RESPONSE HANDLING:"
        "\n- FIRST COUNT the search_provider calls you made this turn. The count decides how you reply."
        "\n- ONE CALL: use the tool's text response exactly as provided, without reformatting or added "
        "explanation. It is already a complete, user-friendly reply for a single list."
        "\n- TWO OR MORE CALLS: the tool texts are NOT relayed at all; follow 'TWO OR MORE SEARCHES IN "
        "ONE TURN' below."
        "\n- SQUARE-BRACKETED NOTES ARE FOR YOU, NOT THE USER: a search_provider response may open "
        "with a note in square brackets (for example '[NEVER state a search radius...]' or '[NONE "
        "of these listings mentions ...]'). Those are instructions addressed to you. Obey them, "
        "and reproduce NO part of them in your reply \u2014 'relay the text exactly' means the "
        "user-facing sentences that follow the note, never the note itself. The user must never "
        "see a square-bracketed note."
        "\n- TWO OR MORE SEARCHES IN ONE TURN (two needs, or a plan): relay NONE "
        "of the tool texts, in any wording. Each one closes with 'You can browse them below and "
        "book the one that best fits your requirements. Tap avatar...', written for a reply that "
        "holds one list; repeated under a label it tells the user nothing. Write the reply in this "
        "shape instead:"
        "\n    1. One opening line naming the user's goal or needs, with the details they gave (day, headcount)."
        "\n    2. One line per need: the bold label, then what that search found in the tool's own "
        "terms (e.g. 'several nearby options', 'the closest is 12 km away'), or plainly that "
        "nothing was found for it and a web search is available."
        "\n    3. 'Tap avatar for more details or Call to connect directly with the service provider.' ONCE."
        "\n    4. ONE closing question: which need to start with. Never ask for a date, time or "
        "address for all of the needs at once \u2014 those are collected for the single booking the "
        "user picks next."
        "\n  Add no provider that no search returned."
        "\n- ONE SEARCH COVERING SEVERAL ITEMS: when a single store-type search stands in for several "
        "goods the user listed (one 'grocery store' search for rice and milk), open with ONE line "
        "saying that kind of store should have all of them, naming the items, then give the tool "
        "text. Never claim a particular store stocks an item the tool did not report."
        "\n\nPROVIDER SERVICE INSIGHTS FOLLOW-UP HANDLING:"
        "\n- For provider_service_insights results, provide natural-language responses in the response field using available context (service details, pricing, availability, rating, contact)."
        "\n- For follow-up questions, do not return raw JSON; answer directly and concisely in conversational language."
        "\n- For specific provider/business contact-detail queries (known business name or already identified provider), prefer provider_service_insights over search_provider."
        "\n- For specific provider/business distance queries (for example 'how far is it from my location?'), prefer provider_service_insights over search_provider or direct no-tool answers."
        "\n- EXCEPTION — ENQUIRY OPENERS: when provider_service_insights was called only to resolve a provider for an enquiry "
        "(for example 'I want to enquire about <business> services. Please provide more details.'), do NOT present its profile, "
        "service list, or contact block. Use it as evidence for ONE grounding line, then ask what the user is looking for and give "
        "ONE example requirement built from that provider's real offerings. See 'Sending an Enquiry to a Provider'."
        "\n\n## GENERAL SEARCH EXECUTION"
        "\n- Use the minimum number of tool calls needed to satisfy the request."
        "\n- Do not perform additional verification when a successful tool already provides sufficient information."
        "\n- Do not invent search results."
    )
    
    if request.runtime.context.get("is_guest"):
        guest_constraint = (
            "\n\n## GUEST USER RESTRICTION"
            "\nYou are assisting a GUEST who is not signed in. A guest may ONLY search for providers "
            "with 'search_provider', ask about Etown with 'provide_etown_info', or get a general answer from you."
            "\nFor ANY other request - booking or ordering, enquiring with a provider, generating an image, "
            "creating a post or offer, invites, QR codes, support tickets, ratings, their bookings or requests, "
            "onboarding a business, or remembering details - call the 'guestuser' tool with the feature they "
            "asked for, then relay its message. Never start, draft or confirm any of those flows, and never "
            "ask for a date, time or address to book."
            "\nAfter search results, do NOT offer to book; say they can sign in to book."
            "\nIf the guest asks how to install, download or get the Etown app, answer directly with no tool "
            "call: say it is available on the Google Play Store and the Apple App Store, and include this "
            "link in the reply: https://www.etown.com/openapp"
        )
        return base_prompt + constraint + search_provider_constraint + guest_constraint

    return base_prompt + provider_booking_constraint + post_creation_instruction + constraint + search_provider_constraint

etown_member_chaatie_agent = create_agent(
    model=gpt41,
    tools=member_tools,
    middleware=[chaatie_system_prompt, tool_loop_guard],
    state_schema=EtownAgentState,
    context_schema=EtownAgentContext,
)

etown_provider_chaatie_agent = create_agent(
    model=gpt41,
    tools=provider_tools,
    middleware=[chaatie_system_prompt, tool_loop_guard],
    state_schema=EtownAgentState,
    context_schema=EtownAgentContext,
)

etown_guest_chaatie_agent = create_agent(
    model=gpt41,
    tools=guest_tools,
    middleware=[chaatie_system_prompt, tool_loop_guard],
    state_schema=EtownAgentState,
    context_schema=EtownAgentContext,
)

@dynamic_prompt
def automation_system_prompt(request: ModelRequest) -> str:
    """Return the fixed automation-agent prompt that maps queries to automation_tester scenarios."""
    return (
        "You are an Automation Testing Orchestrator. "
        "Your task is to map natural language queries to the 'automation_tester' tool. "
        "\n\n"
        "RULES:"
        "\n1. If the user mentions 'login', 'signin', or 'authentication', set test_scenario='login_flow'."
        "\n2. If the user mentions 'book', 'search', 'provider', or 'service', set test_scenario='booking_flow'."
        "\n3. If the user mentions 'invite', 'referral', or 'friend', set test_scenario='invite_flow'."
        "\n4. CRITICAL: If the user query does not fit these strictly (e.g., 'fly to moon', 'make coffee'), "
        "pass the raw query phrase as the 'test_scenario' (e.g., 'fly to moon'). "
        "DO NOT guess or force it into a 'booking' scenario."
    )

etown_automation_agent = create_agent(
    model=gpt41,
    tools=automation_tools,
    middleware=[automation_system_prompt],
    state_schema=EtownAgentState,
    context_schema=EtownAgentContext,
)






