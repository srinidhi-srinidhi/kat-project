"""Memory Recall Tests - unit tests for Chaatie memory scoring, provider affinity and prompt shaping.

Unit tests for memory_utils: mem0 cosine distances become relevance with a floor and inject cap, match_affinity_mention() resolves booked provider names from real booking-history shapes but never bare trade words, and prompt sections render only when there is something to recall.

Functions:
    _row(): Build one booking-affinity row fixture
    test_distance_is_inverted_so_the_best_match_ranks_first(): Distance becomes relevance, best first
    test_unrelated_memories_are_dropped_not_padded(): Below-floor memories are dropped
    test_the_floor_is_only_safe_because_the_corpus_is_clean(): Pins the floor to the clean-corpus assumption
    test_inject_limit_is_capped(): Scored memories never exceed MEMORY_INJECT_LIMIT
    test_scoring_degrades_to_empty_rather_than_raising(): Malformed results score to an empty list
    test_a_bare_list_result_is_accepted(): A plain list result is accepted and scored
    test_memory_lines_carry_no_score_to_the_model(): Prompt memory lines omit the score
    test_named_provider_resolves(): Real booked provider names resolve
    test_trade_words_and_empty_turns_do_not_resolve(): Bare trade words and empty turns resolve to nothing
    test_a_provider_the_member_never_booked_does_not_resolve(): Unbooked providers never resolve
    test_a_name_made_only_of_trade_words_can_never_resolve(): Trade-word-only names are unresolvable
    test_identifier_is_the_head_of_the_name(): _identifier_tokens takes the name's head
    test_most_booked_provider_wins_a_tie(): Ties go to the most-booked provider
    test_affinity_lines_carry_the_id_the_booking_tools_need(): Affinity lines include providerservicesid and counts
    test_a_resolved_mention_is_stated_explicitly(): A resolved mention is spelled out in the prompt
    test_empty_affinity_renders_nothing(): No affinity rows render an empty string
    test_prompt_omits_both_sections_when_there_is_nothing_to_recall(): Empty recall adds no prompt sections
    test_prompt_carries_both_sections_for_either_role(): Both sections render for member and provider

Run:
    pytest tests/unit_tests/test_memory_recall.py -v
"""

import pytest

from app.core.memory import MEMORY_INJECT_LIMIT, MEMORY_SIMILARITY_FLOOR
from app.etown.memory.memory_utils import (
    _identifier_tokens,
    format_affinity_for_prompt,
    format_memories_for_prompt,
    match_affinity_mention,
    score_memories,
)


def _row(businessname, services, providerservicesid, bookingcount=1):
    """Return a booking-affinity row dict for the fixtures."""
    return {
        "businessname": businessname,
        "services": services,
        "providerservicesid": providerservicesid,
        "bookingcount": bookingcount,
    }


PROD_BOOKINGS = [
    _row("INIYAN CABS & ACTING DRIVERS", "Cab Services, Acting Drivers", "ps-iniyan", 2),
    _row("Medavakkam Packers and Movers", "Packing and Unpacking Services", "ps-medav", 2),
    _row("THIRU COMPUTERS", "Jeevan pramaan certificate", "ps-thiru"),
    _row("Vel Siddha Ayurveda Medicals", "", "ps-vel"),
    _row("MSP Cushion works", "cushion and sofa upholstery services", "ps-msp"),
]

DEV_BOOKINGS = [
    _row("A.M.R cushion work", "cushion work", "ps-amr"),
    _row("COSMO CABS, OMR siruseri", "taxi transportation", "ps-cosmo"),
    _row(
        "K THANTHONI TREE CUTTING SERVICE | Tree Cuting Service in Chennai",
        "tree cutting service, coconut tree cutting service",
        "ps-kt",
    ),
]

ADVERSARIAL_BOOKINGS = [
    _row("Cushion World", "cushion work and upholstery", "ps-cw"),
    _row("Sri Balaji Electricals", "electrical repair", "ps-balaji"),
    _row("Sri Bala Ji Plumbing", "plumbing", "ps-bala"),
    _row("Perfect Cabs", "cab service", "ps-perfect"),
]


DOOR_LOCK_SEARCH = {
    "results": [
        {"memory": "Wants to repair door lock", "score": 0.000},
        {"memory": "Needs someone to assemble a wardrobe", "score": 0.361},
        {"memory": "Wants a paragliding experience", "score": 0.387},
        {"memory": "Fridge is not working", "score": 0.397},
        {"memory": "Looking for headphone repair services nearby", "score": 0.398},
        {"memory": "User wants to cancel the washing machine repair booking", "score": 0.398},
    ]
}

EAT_OUT_SEARCH = {
    "results": [
        {"memory": "Is vegetarian", "score": 0.414},
        {"memory": "Never eats onion", "score": 0.431},
        {"memory": "Has two young children", "score": 0.473},
        {"memory": "Office address is 4th Cross, Gandhi Nagar", "score": 0.539},
    ]
}


def test_distance_is_inverted_so_the_best_match_ranks_first():
    """Check distance becomes similarity with the exact match first and results sorted descending."""
    scored = score_memories(DOOR_LOCK_SEARCH)
    assert scored, "the exact match must survive the floor"
    assert scored[0]["memory"] == "Wants to repair door lock"
    assert scored[0]["similarity"] == pytest.approx(1.0)
    assert [s["similarity"] for s in scored] == sorted(
        (s["similarity"] for s in scored), reverse=True
    )


def test_unrelated_memories_are_dropped_not_padded():
    """Check the similarity floor keeps the diet facts and drops unrelated ones."""
    scored = score_memories(EAT_OUT_SEARCH)
    kept = {s["memory"] for s in scored}
    assert "Is vegetarian" in kept
    assert "Never eats onion" in kept
    assert "Has two young children" not in kept
    assert "Office address is 4th Cross, Gandhi Nagar" not in kept
    assert len(scored) < len(EAT_OUT_SEARCH["results"])
    assert all(s["similarity"] >= MEMORY_SIMILARITY_FLOOR for s in scored)


def test_the_floor_is_only_safe_because_the_corpus_is_clean():
    """Check the floor admits intent-log noise but keeps exactly two facts from the clean corpus."""
    junk = score_memories(DOOR_LOCK_SEARCH)
    junk_kept = {item["memory"] for item in junk}
    assert "Wants a paragliding experience" in junk_kept, (
        "The old intent-log corpus scored its noise above the current floor. If this no longer "
        "holds the fixture changed; if the corpus regresses to storing intents, re-tune "
        "MEMORY_SIMILARITY_FLOOR with scripts/memory/evaluate_memory_retrieval.py --judge."
    )
    clean = score_memories(EAT_OUT_SEARCH)
    assert len(clean) == 2


def test_inject_limit_is_capped():
    """Check scoring returns at most MEMORY_INJECT_LIMIT memories."""
    many = {"results": [{"memory": f"fact {i}", "score": 0.01} for i in range(30)]}
    assert len(score_memories(many)) == MEMORY_INJECT_LIMIT


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        [],
        {"results": []},
        {"results": [{"memory": "x", "score": None}]},
        {"results": [{"memory": "x"}]},
        {"results": [{"memory": "   ", "score": 0.0}]},
        {"results": ["not a dict"]},
        {"results": [{"memory": "x", "score": "abc"}]},
    ],
)
def test_scoring_degrades_to_empty_rather_than_raising(raw):
    """Check malformed search results score to an empty list instead of raising."""
    assert score_memories(raw) == []


def test_a_bare_list_result_is_accepted():
    """Check a bare list of results is scored like a results dict."""
    assert score_memories([{"memory": "Lives in Kovilpatti", "score": 0.10}])


def test_memory_lines_carry_no_score_to_the_model():
    """Check rendered memory lines are bullets without scores, and empty input renders nothing."""
    rendered = format_memories_for_prompt(score_memories(DOOR_LOCK_SEARCH))
    assert rendered.startswith("- ")
    assert "0." not in rendered and "similarity" not in rendered
    assert format_memories_for_prompt([]) == ""


@pytest.mark.parametrize(
    "turn, bookings, expected",
    [
        ("can you confirm Indian cab is available today?", PROD_BOOKINGS, "ps-iniyan"),
        ("I meant Inian cabs", PROD_BOOKINGS, "ps-iniyan"),
        ("book iniyan cabs for tomorrow", PROD_BOOKINGS, "ps-iniyan"),
        ("INIYAN CABS & ACTING DRIVERS please", PROD_BOOKINGS, "ps-iniyan"),
        ("book thiru computers again", PROD_BOOKINGS, "ps-thiru"),
        ("I want medavakkam packers", PROD_BOOKINGS, "ps-medav"),
        ("get me msp cushion", PROD_BOOKINGS, "ps-msp"),
        ("vel siddha please", PROD_BOOKINGS, "ps-vel"),
        ("book cosmo cabs to the airport", DEV_BOOKINGS, "ps-cosmo"),
        ("amr cushion work again", DEV_BOOKINGS, "ps-amr"),
        ("thanthoni tree cutting", DEV_BOOKINGS, "ps-kt"),
        ("book perfect cabs", ADVERSARIAL_BOOKINGS, "ps-perfect"),
        ("sri balaji electricals", ADVERSARIAL_BOOKINGS, "ps-balaji"),
    ],
)
def test_named_provider_resolves(turn, bookings, expected):
    """Check a turn naming a booked provider, even misspelt, resolves to its service id."""
    matched = match_affinity_mention(turn, bookings)
    assert matched is not None, f"{turn!r} should resolve"
    assert matched["providerservicesid"] == expected


@pytest.mark.parametrize(
    "turn, bookings",
    [
        ("book a cab", DEV_BOOKINGS),
        ("book a cab for tomorrow", PROD_BOOKINGS),
        ("I need a cushion repaired", PROD_BOOKINGS),
        ("cushion work", PROD_BOOKINGS),
        ("find me a computer shop", PROD_BOOKINGS),
        ("computer", PROD_BOOKINGS),
        ("tree cutting", DEV_BOOKINGS),
        ("i need a plumber", PROD_BOOKINGS),
        ("find me a cab", ADVERSARIAL_BOOKINGS),
        ("i need a cushion redone", ADVERSARIAL_BOOKINGS),
        ("book iniyan cabs", []),
        ("", PROD_BOOKINGS),
        ("hi", PROD_BOOKINGS),
        ("what is the weather today", PROD_BOOKINGS),
    ],
)
def test_trade_words_and_empty_turns_do_not_resolve(turn, bookings):
    """Check trade words, empty turns and empty bookings resolve to no provider."""
    assert match_affinity_mention(turn, bookings) is None


@pytest.mark.parametrize(
    "turn, bookings",
    [
        ("book a.m.r cushion work", PROD_BOOKINGS),
        ("book msp cushion works", DEV_BOOKINGS),
    ],
)
def test_a_provider_the_member_never_booked_does_not_resolve(turn, bookings):
    """Check a provider absent from the member's bookings does not resolve."""
    assert match_affinity_mention(turn, bookings) is None


def test_a_name_made_only_of_trade_words_can_never_resolve():
    """Check a business name made only of trade words yields no identifier tokens and no match."""
    assert _identifier_tokens("Cushion World", "cushion work and upholstery") == []
    assert match_affinity_mention("cushion world", [ADVERSARIAL_BOOKINGS[0]]) is None


@pytest.mark.parametrize(
    "businessname, services, expected_first",
    [
        ("INIYAN CABS & ACTING DRIVERS", "Cab Services, Acting Drivers", "iniyan"),
        ("A.M.R cushion work", "cushion work", "amr"),
        ("K THANTHONI TREE CUTTING SERVICE", "tree cutting service", "thanthoni"),
        ("MSP Cushion works", "cushion and sofa upholstery", "msp"),
        ("COSMO CABS, OMR siruseri", "taxi transportation", "cosmo"),
    ],
)
def test_identifier_is_the_head_of_the_name(businessname, services, expected_first):
    """Check the first identifier token is the distinctive head of the business name."""
    tokens = _identifier_tokens(businessname, services)
    assert tokens and tokens[0] == expected_first


def test_most_booked_provider_wins_a_tie():
    """Check the most-booked row wins when two rows match equally."""
    tied = [
        _row("Anna Cabs", "cab service", "ps-low", 1),
        _row("Anna Cabs", "cab service", "ps-high", 9),
    ]
    assert match_affinity_mention("book anna cabs", tied[::-1])["providerservicesid"] == "ps-high"


def test_affinity_lines_carry_the_id_the_booking_tools_need():
    """Check affinity lines include the service id and booking counts, without RESOLVED."""
    rendered = format_affinity_for_prompt(PROD_BOOKINGS)
    assert "ps-iniyan" in rendered
    assert "booked 2 times" in rendered
    assert "booked once" in rendered
    assert "RESOLVED" not in rendered


def test_a_resolved_mention_is_stated_explicitly():
    """Check a resolved mention is rendered as RESOLVED with its name and service id."""
    matched = match_affinity_mention("is Indian cab free today?", PROD_BOOKINGS)
    rendered = format_affinity_for_prompt(PROD_BOOKINGS, matched)
    assert "RESOLVED" in rendered
    assert "INIYAN CABS & ACTING DRIVERS" in rendered
    assert "ps-iniyan" in rendered


def test_empty_affinity_renders_nothing():
    """Check empty or nameless affinity rows render an empty string."""
    assert format_affinity_for_prompt([]) == ""
    assert format_affinity_for_prompt([_row("", "", "ps-x")]) == ""


def test_prompt_omits_both_sections_when_there_is_nothing_to_recall():
    """Check the agent prompt leaves out the memory and affinity sections when both are empty."""
    from app.agent_infrastructure.prompt_templates.templates import (
        chaatie_agent_prompt_template,
    )

    empty = chaatie_agent_prompt_template("A", "Chennai", "t", "en", "", "", 2)
    assert "Long-term Memories" not in empty
    assert "booked before" not in empty
    assert "No long-term memories available" not in empty


@pytest.mark.parametrize("user_role", [1, 2])
def test_prompt_carries_both_sections_for_either_role(user_role):
    """Check the agent prompt includes both sections for member and provider roles."""
    from app.agent_infrastructure.prompt_templates.templates import (
        chaatie_agent_prompt_template,
    )

    filled = chaatie_agent_prompt_template(
        "A",
        "Chennai",
        "t",
        "en",
        format_memories_for_prompt(score_memories(DOOR_LOCK_SEARCH)),
        format_affinity_for_prompt(PROD_BOOKINGS),
        user_role,
    )
    assert "## Long-term Memories about the user" in filled
    assert "## Providers this user has booked before" in filled
    assert "ps-iniyan" in filled
