"""Chaatie Issue Fix Tests - regression tests for deterministic halves of tester-reported Chaatie fixes.

Unit tests needing no database, Places API or LLM: they cover the multi-need listing merge, clarification route, umbrella-type filter, local-business router, booking date formatter and booknow directive, with issue numbers referring to issues/Chaatie Issue.docx.

Functions:
    test_split_multi_need_results_remain_available_for_follow_up_selection(): _merge_listing_rows keeps every need's rows
    test_reported_cases_take_the_expected_classification_route(): needs_clarification routes reported prompts
    test_umbrella_place_types_are_dropped(): umbrella Places types are filtered out
    test_informative_place_types_survive_and_are_humanized(): specific Places types are humanized
    test_place_types_tolerates_junk(): non-list input yields an empty list
    test_local_business_queries_route_to_places(): local-business queries route to Places
    test_informational_queries_stay_on_the_prose_search(): informational queries stay on prose search
    test_service_datetime_renders_date_and_time(): booking datetime renders date and time
    test_service_datetime_omits_a_midnight_time(): midnight renders as date only
    test_service_datetime_returns_empty_for_unparseable_input(): unparseable input renders empty
    test_supplied_booking_details_are_pinned_for_the_agent(): booknow directive embeds supplied details
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.api.v1.endpoints.chat import _merge_listing_rows, _single_search_reply, _strip_model_notes
from app.agent_infrastructure.classification.rules import needs_clarification
from app.agent_infrastructure.tools.tools_utils.search_provider import (
    _describable_terms,
    _dominant_result_label,
    _humanize_place_types,
    _named_day,
)
from app.agent_infrastructure.tools.tools_utils.service_intent import ServiceIntent
from app.agent_infrastructure.tools.tools_utils.booknow_provider import _DETAILS_SUPPLIED_DIRECTIVE
from app.etown.booking.booking_utils import _format_service_datetime
from app.utils.web_search_quality_rules import is_local_business_query


def test_split_multi_need_results_remain_available_for_follow_up_selection():
    """Assert _merge_listing_rows keeps every need's rows once, in order."""
    first_need = [{"providerservicesid": "electrician-1"}]
    second_need = [{"providerservicesid": "cleaner-1"}, {"id": "cleaner-2"}]

    assert _merge_listing_rows(first_need, second_need, first_need) == first_need + second_need


@pytest.mark.parametrize("query, expected", [
    ("Need help with a washing machine", True),
    ("Mosquitoes are getting unbearable at home", False),
    ("Need an electrician and someone to clean our sofa this weekend", True),
    ("I want to buy a washing machine, not repair my old one", True),
])
def test_reported_cases_take_the_expected_classification_route(query, expected):
    """Assert needs_clarification routes each reported prompt as expected."""
    assert needs_clarification(query) is expected


def test_umbrella_place_types_are_dropped():
    """Assert umbrella Places types such as point_of_interest, service and establishment are dropped."""
    assert _humanize_place_types(["point_of_interest", "service", "establishment"]) == []


def test_informative_place_types_survive_and_are_humanized():
    """Assert specific Places types survive and are humanized while umbrella types drop."""
    assert _humanize_place_types([
        "furniture_store", "home_improvement_store", "home_goods_store",
        "point_of_interest", "store", "establishment",
    ]) == ["Furniture store", "Home improvement store", "Home goods store"]


@pytest.mark.parametrize("value", [None, [], "not-a-list", 42])
def test_place_types_tolerates_junk(value):
    """Assert non-list or empty input yields an empty list."""
    assert _humanize_place_types(value) == []


@pytest.mark.parametrize("query", [
    "office chair repair services near Gandhi Nagar, Kovilpatti, Tamil Nadu",
    "office chair repair near me",
    "plumber in my area",
    "sofa repair shop",
    "best dry clean near me",
])
def test_local_business_queries_route_to_places(query):
    """Assert local-business queries are detected as such."""
    assert is_local_business_query(query) is True


@pytest.mark.parametrize("query", [
    "what is the weather today in Kovilpatti",
    "gold rate now",
    "who is the chief minister of Tamil Nadu",
    "UV index in Chennai",
    "latest news on cricket score",
])
def test_informational_queries_stay_on_the_prose_search(query):
    """Assert informational queries are not treated as local-business queries."""
    assert is_local_business_query(query) is False


def test_service_datetime_renders_date_and_time():
    """Assert a booking datetime renders as date plus 12-hour time."""
    assert _format_service_datetime("2026-08-10T08:00:00") == "10 Aug 2026, 8:00 AM"


def test_service_datetime_omits_a_midnight_time():
    """Assert a midnight booking time renders as the date alone."""
    assert _format_service_datetime("2026-08-10T00:00:00") == "10 Aug 2026"


@pytest.mark.parametrize("value", [None, "", "garbage", 0])
def test_service_datetime_returns_empty_for_unparseable_input(value):
    """Assert unparseable booking datetimes render as an empty string."""
    assert _format_service_datetime(value) == ""


def test_supplied_booking_details_are_pinned_for_the_agent():
    """Assert the booknow directive embeds supplied details and tells the agent not to re-ask."""
    details = ("Wednesday of this week to go from Kovilpatti to samithoppu "
               "and to kurangani and return. starting early morning")
    directive = _DETAILS_SUPPLIED_DIRECTIVE.format(details=details)

    assert details in directive
    assert "what do you need today" in directive.lower()
    assert "do not ask the user to repeat" in directive.lower()


def _clothing_intent():
    return ServiceIntent(
        trade_terms=("clothing store",),
        match_terms=("clothing", "women", "formal", "blazers", "trousers", "apparel"),
        item_terms=("blazers", "trousers", "women s dress"),
        resolved=True,
    )


def test_audience_words_never_become_the_result_label():
    results = [
        {"services": "Women's clothing, formal wear, blazers"},
        {"services": "Women's clothing and ethnic wear"},
        {"services": "Clothing for women and kids"},
    ]

    label = _dominant_result_label(results, allowed_terms=_describable_terms(_clothing_intent()))

    assert label == "clothing"


def test_describable_terms_keep_trade_words_and_item_head_nouns_only():
    terms = _describable_terms(_clothing_intent())

    assert "women" not in terms
    assert "formal" not in terms
    assert {"clothing", "blazers", "trousers", "dress"} <= terms


def test_unresolved_intent_describes_nothing():
    assert _describable_terms(ServiceIntent()) == set()


def test_model_notes_are_stripped_from_a_relayed_search_reply():
    text = (
        "[NONE of these listings mentions 'blazers' itself.]\n\n"
        "[NEVER state a search radius to the user.]\n\n"
        "I have found several nearby clothing options for you."
    )

    assert _strip_model_notes(text) == "I have found several nearby clothing options for you."


def _turn(*tool_calls, tool_text="I have found several nearby clothing options for you."):
    messages = [HumanMessage("I want to get dress like in picture")]
    messages.append(AIMessage(content="", tool_calls=list(tool_calls)))
    for call in tool_calls:
        messages.append(ToolMessage(tool_text, tool_call_id=call["id"]))
    messages.append(AIMessage(
        "1. **Option 1**: [Provider details not available in this example but will be "
        "presented in the platform.]"
    ))
    return messages


def test_a_single_search_turn_relays_the_tool_text_instead_of_the_model_rewrite():
    messages = _turn({"name": "search_provider", "args": {"search_term": "blazer"}, "id": "c1"})

    assert _single_search_reply(messages) == "I have found several nearby clothing options for you."


def test_a_two_search_turn_keeps_the_model_reply():
    messages = _turn(
        {"name": "search_provider", "args": {"search_term": "electrician"}, "id": "c1"},
        {"name": "search_provider", "args": {"search_term": "sofa cleaning"}, "id": "c2"},
    )

    assert _single_search_reply(messages) is None


def test_a_turn_mixing_search_with_another_tool_keeps_the_model_reply():
    messages = _turn(
        {"name": "search_provider", "args": {"search_term": "bakery"}, "id": "c1"},
        {"name": "perform_web_search", "args": {"query": "cake ideas"}, "id": "c2"},
    )

    assert _single_search_reply(messages) is None


def test_only_the_current_turn_is_considered():
    earlier = _turn({"name": "search_provider", "args": {"search_term": "plumber"}, "id": "old"})
    messages = earlier + [HumanMessage("thanks"), AIMessage("You're welcome!")]

    assert _single_search_reply(messages) is None


@pytest.mark.parametrize("prompt, expected", [
    ("birthday cake for tomorrow", "tomorrow"),
    ("need a plumber tonight", "tonight"),
    ("book a haircut on Friday", "on Friday"),
    ("cake delivery on 12/10", "on 12/10"),
    ("electrician this weekend", None),
    ("plumber next week", None),
    ("sun protection cream", None),
    ("I need a plumber", None),
])
def test_named_day_is_read_from_the_prompt(prompt, expected):
    assert _named_day(prompt) == expected
