"""Lifecycle Plan State Tests - unit tests for deleted, deactivated and reactivated provider plans.

Text-level unit tests over plans_utils and provider_lifecycle_utils SQL constants, with no database or network: deactivated providers resolve to the free tier, per-business flags never touch identity tables, and only expiry and delete clear the tier and plan dates.

Functions:
    test_effective_plan_forces_free_when_deactivated(): Deactivated providers resolve to free
    test_effective_plan_expression_is_alias_safe(): The plan expression works under any table alias
    test_lifecycle_history_records_the_effective_move(): History records the effective tier change
    test_only_delete_writes_subscription_status(): Only delete sets subscription_status
    test_lifecycle_event_requires_exactly_one_scope(): Lifecycle events reject zero or two scopes
    test_provider_flag_covers_the_six_business_tables(): Per-business flag covers six business tables
    test_provider_flag_never_touches_an_identity_table(): Per-business flag skips identity tables
    test_provider_flag_matches_offers_on_owner_only(): Offers are matched on their owner only
    test_provider_flag_resets_the_opposite_flag(): Each flag mode resets the opposite flag
    test_provider_flag_guards_are_opposites_and_skip_deleted(): Guards are opposites and skip deleted rows
    test_deactivation_never_moves_the_paid_term(): Deactivation leaves plan dates untouched
    test_retirement_clears_the_tier_and_both_dates(): Retirement clears the tier and both dates
"""

import pytest

from app.etown.plans import plans_utils
from app.etown.provider import provider_lifecycle_utils as plu


def test_effective_plan_forces_free_when_deactivated():
    """Check the effective-plan expression tests deactivation before subscription status and expiry."""
    expr = plans_utils._EFFECTIVE_PLAN_CODE.format(a="p")
    assert "lower(COALESCE(p.deactivated, 'false')) = 'true'" in expr
    assert expr.index("deactivated") < expr.index("subscription_status"), (
        "the deactivated branch must come first, or an active unexpired plan wins over it"
    )


@pytest.mark.parametrize("alias", ["cp", "pr", "t", "etown_provider"])
def test_effective_plan_expression_is_alias_safe(alias):
    """Check the effective-plan expression qualifies every column with the given alias."""
    expr = plans_utils._EFFECTIVE_PLAN_CODE.format(a=alias)
    assert "{a}" not in expr
    for column in ("deactivated", "subscription_status", "plan_expires_on", "plan_code"):
        assert f"{alias}.{column}" in expr


def test_lifecycle_history_records_the_effective_move():
    """Check lifecycle history records plan to free on deactivate, the reverse on reactivate, and expired on delete."""
    from_plan, to_plan, from_status, to_status = plans_utils._LIFECYCLE_HISTORY["deactivate"]
    assert (from_plan, to_plan) == ("t.plan_code", "'free'")
    assert from_status == to_status, "deactivation must not move subscription_status"

    from_plan, to_plan, _, _ = plans_utils._LIFECYCLE_HISTORY["reactivate"]
    assert from_plan == "'free'"
    assert "deactivated" in to_plan, "the tier that comes back is the effective one"

    from_plan, to_plan, _, to_status = plans_utils._LIFECYCLE_HISTORY["delete"]
    assert (from_plan, to_plan) == ("t.plan_code", "'free'")
    assert to_status == "'expired'"


def test_only_delete_writes_subscription_status():
    """Check only the delete mode changes subscription_status."""
    assert "subscription_status = 'expired'" in plans_utils._LIFECYCLE_RETIRE_CTE
    for mode in ("deactivate", "reactivate"):
        _, _, from_status, to_status = plans_utils._LIFECYCLE_HISTORY[mode]
        assert from_status == to_status


@pytest.mark.asyncio
async def test_lifecycle_event_requires_exactly_one_scope():
    """Check record_plan_lifecycle_event rejects no scope, both scopes, and an unknown mode."""
    with pytest.raises(ValueError):
        await plans_utils.record_plan_lifecycle_event("deactivate")
    with pytest.raises(ValueError):
        await plans_utils.record_plan_lifecycle_event(
            "deactivate", contact_id="c", userprofile_id="u"
        )
    with pytest.raises(ValueError):
        await plans_utils.record_plan_lifecycle_event("suspend", contact_id="c")


def test_provider_flag_covers_the_six_business_tables():
    """Check the per-business flag targets exactly the six business tables."""
    assert set(plu._PROVIDER_FLAGGED_TABLES) == {
        "etown_provider",
        "etown_providerservices",
        "etown_providerservices_copy",
        "etown_offers",
        "etown_offerings",
        "etown_etownprovider",
    }


@pytest.mark.parametrize("identity_table", ["etown_contact", "etown_userprofile", "etown_member"])
def test_provider_flag_never_touches_an_identity_table(identity_table):
    """Check the per-business flag SQL never updates an identity table."""
    assert identity_table not in plu._PROVIDER_FLAGGED_TABLES
    for sql in (plu._DEACTIVATE_SQL, plu._REACTIVATE_SQL):
        assert f"UPDATE {identity_table} " not in sql


def test_provider_flag_matches_offers_on_owner_only():
    """Check offers are matched on their owner's userprofileid, never vieweduserprofileid."""
    assert plu._PROVIDER_FLAGGED_TABLES["etown_offers"] == "t.userprofileid = s.upid"
    for sql in (plu._DEACTIVATE_SQL, plu._REACTIVATE_SQL):
        assert "vieweduserprofileid" not in sql


def test_provider_flag_resets_the_opposite_flag():
    """Check deactivation resets reactivation and reactivation resets deactivated."""
    assert "reactivation = 'false'" in plu._DEACTIVATE_SQL
    assert "deactivated = 'false'" in plu._REACTIVATE_SQL


def test_provider_flag_guards_are_opposites_and_skip_deleted():
    """Check the deactivate and reactivate guards are opposites and both skip deleted providers."""
    assert "lower(COALESCE(p.deactivated, 'false')) <> 'true'" in plu._DEACTIVATE_SQL
    assert "lower(COALESCE(p.deactivated, 'false')) = 'true'" in plu._REACTIVATE_SQL
    for sql in (plu._DEACTIVATE_SQL, plu._REACTIVATE_SQL):
        assert "lower(COALESCE(p.deleted, 'false')) <> 'true'" in sql


def test_deactivation_never_moves_the_paid_term():
    """Check deactivation and reactivation never change the plan code or plan dates."""
    for sql in (plu._DEACTIVATE_SQL, plu._REACTIVATE_SQL):
        assert "plan_expires_on =" not in sql
        assert "plan_started_on =" not in sql
        assert "plan_code =" not in sql


def test_retirement_clears_the_tier_and_both_dates():
    """Check both retirement paths set the plan to free, null both dates, and mark the subscription expired."""
    from app.services import plan_expiry

    for sql in (plans_utils._LIFECYCLE_RETIRE_CTE, plan_expiry._RETIRE_SQL):
        assert "plan_code = 'free'" in sql
        assert "plan_started_on = NULL" in sql
        assert "plan_expires_on = NULL" in sql
        assert "subscription_status = 'expired'" in sql

    assert "plan_code, 'free'," in plan_expiry._RETIRE_SQL
    assert "'plan_expiry_worker', 'plan_expires_on passed'" in plan_expiry._RETIRE_SQL
