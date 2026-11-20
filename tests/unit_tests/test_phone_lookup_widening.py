"""Phone Lookup Widening Tests - pin the phone-number search SQL and its guards in aisearch v1 and v2.

Unit tests with no database or network: they check the phone normalizer, phone-query detection, the lookup legs and index expressions of phone_lookup_search's SQL, the fall-through on an empty lookup, and that a phone query never reaches the web fallback.

Features:
    - Normalizer: every country-code form of a number reduces to the same ten digits; empty input stays empty
    - Detection: phone-shaped queries are recognised and text queries are not
    - Lookup legs: primary numbers on etown_contact, etown_userprofile, etown_member and etown_provider, plus alternate numbers on etown_contact and etown_provider
    - Index expressions: each predicate matches its index verbatim ('\\D' with COALESCE, '[^0-9]' on etown_provider)
    - Barrier: the candidate CTE stays AS MATERIALIZED
    - Fall-through: an empty phone lookup degrades to normal search; v1 never sends a phone query to the web fallback

Functions:
    lookup_sql(): Rebuild the full lookup_query string phone_lookup_search sends, resolving module constants
    test_every_country_code_form_normalizes_to_the_same_ten_digits(): Every country-code form normalizes to the same ten digits
    test_normalizer_is_empty_safe(): Empty and None inputs normalize to an empty string
    test_phone_shaped_queries_are_detected(): Phone-shaped queries are detected as phone numbers
    test_text_queries_are_not_treated_as_phone_numbers(): Text queries are not detected as phone numbers
    test_lookup_searches_every_table_a_number_can_live_on(): The lookup reads every table a number can live on
    test_lookup_has_one_leg_per_number_column(): The lookup has one leg per primary and alternate number column
    test_lookup_keeps_the_optimisation_barrier(): The candidate CTE stays AS MATERIALIZED
    test_lookup_matches_the_indexed_expressions(): Each predicate matches its index expression verbatim
    test_empty_phone_lookup_falls_through_instead_of_returning(): An empty phone lookup falls through to normal search
    test_web_fallback_never_fires_for_a_phone_query(): v1 never sends a phone query to the web fallback

Dependencies:
    - app.etown.search.aisearch_utils / aisearch_v2_utils: the v1 and v2 search modules under test
    - ast, inspect: reading phone_lookup_search's SQL from source
    - pytest: parametrization

Updates:
    31-Aug-2026: Added phone lookup widening tests.
    25-Sep-2026: lookup_sql resolves concatenated module constants; expects the two alternate-number legs.
"""

import ast
import inspect

import pytest

from app.etown.search import aisearch_utils as v1
from app.etown.search import aisearch_v2_utils as v2

VERSIONS = pytest.mark.parametrize("mod", [v1, v2], ids=["v1", "v2"])

EXPECTED_LEGS = [
    "etown_contact",
    "etown_userprofile",
    "etown_member",
    "etown_provider",
]

EXPECTED_NUMBER_COLUMNS = 6


def lookup_sql(mod) -> str:
    """Rebuild the full lookup_query string phone_lookup_search sends, resolving concatenated module constants."""
    def resolve(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and isinstance(getattr(mod, node.id, None), str):
            return getattr(mod, node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = resolve(node.left), resolve(node.right)
            return None if left is None or right is None else left + right
        return None

    tree = ast.parse(inspect.getsource(mod.phone_lookup_search))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "lookup_query":
            sql = resolve(node.value)
            if sql is not None:
                return sql
    raise AssertionError(f"lookup_query not found in {mod.__name__}")


@VERSIONS
@pytest.mark.parametrize(
    "raw",
    [
        "8438010512",
        "+918438010512",
        "918438010512",
        "0918438010512",
        "+91 84380 10512",
        "+91-84380-10512",
        "(+91) 8438010512",
        "08438010512",
    ],
)
def test_every_country_code_form_normalizes_to_the_same_ten_digits(mod, raw):
    """Assert every country-code form of a number normalizes to the same ten digits the SQL matches on."""
    assert mod.normalize_phone_number(raw) == "8438010512"


@VERSIONS
def test_normalizer_is_empty_safe(mod):
    """Assert empty and None inputs normalize to an empty string."""
    assert mod.normalize_phone_number("") == ""
    assert mod.normalize_phone_number(None) == ""


@VERSIONS
@pytest.mark.parametrize("raw", ["8438010512", "+91 8438010512", "918438010512"])
def test_phone_shaped_queries_are_detected(mod, raw):
    """Assert phone-shaped queries are detected as phone numbers."""
    assert mod.is_phone_number_query(raw) is True


@VERSIONS
@pytest.mark.parametrize("raw", ["", "electrician", "shop 5", "12345", "AC repair 24x7"])
def test_text_queries_are_not_treated_as_phone_numbers(mod, raw):
    """Assert text queries are not detected as phone numbers."""
    assert mod.is_phone_number_query(raw) is False


@VERSIONS
@pytest.mark.parametrize("table", EXPECTED_LEGS)
def test_lookup_searches_every_table_a_number_can_live_on(mod, table):
    """Assert the lookup still reads the given table, so numbers stored only there stay searchable."""
    sql = lookup_sql(mod)
    assert f"FROM {table} " in sql, f"{mod.__name__} no longer searches {table}"


@VERSIONS
def test_lookup_has_one_leg_per_number_column(mod):
    """Assert one leg per primary number column plus the contact and provider alternate numbers."""
    assert lookup_sql(mod).count("= $1") == EXPECTED_NUMBER_COLUMNS


@VERSIONS
def test_lookup_keeps_the_optimisation_barrier(mod):
    """Assert the candidate CTE stays AS MATERIALIZED as an optimisation barrier."""
    assert "phone_contacts AS MATERIALIZED" in lookup_sql(mod)


@VERSIONS
def test_lookup_matches_the_indexed_expressions(mod):
    """Assert each predicate matches its index expression verbatim so the planner can use the index."""
    sql = lookup_sql(mod)
    assert sql.count(r"right(regexp_replace(COALESCE") == 4
    assert r"right(regexp_replace(pv.mobilenumber, '[^0-9]', '', 'g'), 10)" in sql
    assert r"right(regexp_replace(pv2.alternatemobile, '[^0-9]', '', 'g'), 10)" in sql


@VERSIONS
def test_empty_phone_lookup_falls_through_instead_of_returning(mod):
    """Assert the phone payload is returned only when the lookup found somebody."""
    src = inspect.getsource(mod.aisearch)
    assert "if members or providers:" in src, "phone branch returns unconditionally again"
    assert "PHONE LOOKUP EMPTY" in src


def test_web_fallback_never_fires_for_a_phone_query():
    """Assert v1 never lets a bare phone number reach the Google Places web fallback."""
    src = inspect.getsource(v1.aisearch)
    trigger = src[src.index("ENABLE_AISEARCH_WEB_FALLBACK"):]
    condition = trigger[: trigger.index("):")]
    assert "not is_phone_number_query(search_query)" in condition
