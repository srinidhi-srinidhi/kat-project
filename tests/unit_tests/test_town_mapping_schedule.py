"""Town Mapping Schedule Tests - unit tests for the weekly town-mapping worker and its SQL shape.

Database-free tests for app.services.town_mapping: the Sunday 04:00 IST schedule, the sweep and candidate SQL keeping its tiebreak keys and AS MATERIALIZED barrier, and the mapping loop and mirror retry surviving failures on schedule.

Classes:
    _LoopHarness(): Drives _mapping_loop for a bounded number of iterations, then cancels it

Functions:
    _ist(): Build an IST-aware datetime
    test_configured_for_sunday_4am(): Schedule is Sunday 04:00 IST
    test_next_run_is_always_the_configured_window_in_the_future(): Next run is the future window
    test_exactly_on_the_window_schedules_next_week_not_now(): On-window calls schedule next week
    test_window_falls_in_the_saturday_night_utc_quiet_period(): Window falls in the UTC quiet period
    test_successive_runs_are_exactly_one_week_apart(): Successive runs are one week apart
    test_sweep_sql_keeps_the_materialized_barrier(): Sweep SQL keeps AS MATERIALIZED
    test_sweep_sql_keeps_every_tiebreak_key_in_order(): Sweep SQL keeps every tiebreak key in order
    test_sweep_sql_excludes_test_towns(): Sweep SQL excludes test towns
    test_sweep_sql_never_creates_a_town(): Sweep SQL never inserts a town
    test_sweep_insert_is_idempotent_and_keys_the_link_to_the_provider(): Link insert is idempotent per provider
    test_candidate_sql_is_a_forward_keyset_over_providerid(): Candidate SQL pages by providerid keyset
    test_chunking_constants_cover_the_measured_candidate_set(): Chunk constants cover the candidate set
    test_link_reach_code_default_is_the_absorb_radius(): Link reach defaults to the absorb radius
    test_link_reach_prefers_the_environment_setting(): Link reach prefers the environment setting
    test_sweep_reports_which_tier_resolved_each_link(): Sweep reports the resolving tier per link
    test_mirror_retry_is_bounded_and_backs_off(): Mirror retry is bounded with backoff
    test_mirror_retry_recovers_after_a_failed_attempt(): Mirror retry recovers after a failure
    test_mirror_retry_gives_up_after_the_attempt_ceiling(): Mirror retry stops at the attempt ceiling
    test_mirror_retry_survives_a_raising_mirror(): Mirror retry survives a raising mirror
    test_mirror_retry_propagates_cancellation(): Mirror retry propagates cancellation
    test_mapping_loop_sweeps_then_mirrors_then_waits_again(): Loop sweeps, mirrors, then waits
    test_mapping_loop_survives_a_failing_pass_and_keeps_its_schedule(): Loop survives a failing pass
    test_mapping_loop_mirrors_nothing_when_a_pass_finds_nothing(): Empty pass mirrors nothing
    test_town_population_counts_providers_not_link_rows(): Town population counts providers, not links
"""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.services import town_mapping as tm


def _ist(y, m, d, hh=0, mm=0):
    """Return an IST-aware datetime."""
    return datetime(y, m, d, hh, mm, tzinfo=tm.IST)


def test_configured_for_sunday_4am():
    """Check the mapping window is Sunday 04:00 IST."""
    assert tm.TOWN_MAPPING_WEEKDAY == 6, "Sunday, matching the agreed weekend window"
    assert tm.TOWN_MAPPING_HOUR == 4
    assert tm.IST.utcoffset(None) == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize(
    "now",
    [
        _ist(2026, 9, 4, 17, 30),
        _ist(2026, 9, 6, 3, 59),
        _ist(2026, 9, 6, 4, 0),
        _ist(2026, 9, 6, 4, 30),
        _ist(2026, 9, 7, 0, 1),
        _ist(2026, 12, 31, 23, 59),
        _ist(2027, 2, 28, 12, 0),
        _ist(2028, 2, 29, 12, 0),
    ],
)
def test_next_run_is_always_the_configured_window_in_the_future(now):
    """Check the next run lands on the configured window, strictly in the future, within a week."""
    target, delay = tm._next_run_at(now)

    assert target.weekday() == tm.TOWN_MAPPING_WEEKDAY
    assert target.hour == tm.TOWN_MAPPING_HOUR
    assert (target.minute, target.second, target.microsecond) == (0, 0, 0)
    assert target > now, "a non-future target would make the loop spin"
    assert delay == pytest.approx((target - now).total_seconds())
    assert 0 < delay <= 7 * 24 * 3600


def test_exactly_on_the_window_schedules_next_week_not_now():
    """Check a time exactly on the window schedules the following week."""
    on_window = _ist(2026, 9, 6, 4, 0)
    target, delay = tm._next_run_at(on_window)

    assert target == on_window + timedelta(days=7)
    assert delay == pytest.approx(7 * 24 * 3600)


def test_window_falls_in_the_saturday_night_utc_quiet_period():
    """Check the window is Saturday 22:30 UTC."""
    from datetime import timezone

    target, _ = tm._next_run_at(_ist(2026, 9, 4, 17, 30))
    utc = target.astimezone(timezone.utc)

    assert (utc.weekday(), utc.hour, utc.minute) == (5, 22, 30), "Sat 22:30 UTC"


def test_successive_runs_are_exactly_one_week_apart():
    """Check consecutive scheduled runs are seven days apart."""
    first, _ = tm._next_run_at(_ist(2026, 9, 4, 12, 0))
    second, _ = tm._next_run_at(first)

    assert second - first == timedelta(days=7)


def test_sweep_sql_keeps_the_materialized_barrier():
    """Check the sweep SQL keeps the town_pop MATERIALIZED CTE."""
    assert "WITH town_pop AS MATERIALIZED" in tm._SWEEP_SQL, (
        "inlined, the planner re-runs the aggregate once per candidate: 15x plan cost"
    )


def test_sweep_sql_keeps_every_tiebreak_key_in_order():
    """Check the tie-break orders by population, then createdon NULLS LAST, then townprofileid."""
    order_by = tm._SWEEP_SQL.split("ORDER BY 3 ASC,")[1].split("LIMIT 1")[0]
    flat = " ".join(order_by.split())

    assert flat.index("COALESCE(pop.n, 0) DESC") < flat.index("tp.createdon"), (
        "population must outrank createdon, or a district root takes its child's providers"
    )
    assert flat.index("tp.createdon") < flat.index("tp.townprofileid")
    assert "NULLS LAST" in flat, "a NULL createdon must not win the tie"


def test_sweep_sql_excludes_test_towns():
    """Check test towns are excluded by both townname and cityname."""
    assert tm._SWEEP_SQL.count("NOT ILIKE 'test%'") == 2, "both townname and cityname"


def test_sweep_sql_never_creates_a_town():
    """Check the sweep SQL never writes etown_townprofile and has a single INSERT."""
    body = tm._SWEEP_SQL.upper()

    assert "INSERT INTO ETOWN_TOWNPROFILE" not in body
    assert "UPDATE ETOWN_TOWNPROFILE" not in body
    assert "DELETE FROM ETOWN_TOWNPROFILE" not in body
    assert body.count("INSERT INTO") == 1


def test_sweep_insert_is_idempotent_and_keys_the_link_to_the_provider():
    """Check the insert uses ON CONFLICT DO NOTHING and sets etownproviderid to providerid."""
    assert "ON CONFLICT DO NOTHING" in tm._SWEEP_SQL
    assert "SELECT providerid, providerid," in tm._SWEEP_SQL, (
        "etownproviderid = providerid, so a later proper onboard updates this row"
    )


def test_candidate_sql_is_a_forward_keyset_over_providerid():
    """Check the candidate SQL pages forward by providerid over unlinked providers only."""
    assert "p.providerid > $1" in tm._CANDIDATE_SQL
    assert "ORDER BY p.providerid" in tm._CANDIDATE_SQL
    assert "LIMIT $2" in tm._CANDIDATE_SQL
    assert "random()" not in tm._CANDIDATE_SQL, (
        "a full pass walks the set once; random ordering would break the cursor"
    )
    assert "NOT EXISTS" in tm._CANDIDATE_SQL, "only providers holding no link are candidates"


def test_chunking_constants_cover_the_measured_candidate_set():
    """Check chunk size times max chunks covers at least 45,000 candidates."""
    assert tm.TOWN_MAPPING_CHUNK * tm.TOWN_MAPPING_MAX_CHUNKS >= 45_000, (
        "production held 44,954 candidates on 04-Sep-2026; the fuse must not cut a pass short"
    )


def test_link_reach_code_default_is_the_absorb_radius():
    """Check the code-default link reach is None, falling back to each provider's absorb radius."""
    assert tm.TOWN_MAPPING_LINK_MAX_KM is None, (
        "widening to the ladder's 50km adds ~33,900 links at up to 45km on production; "
        "the code default must stay narrow so an undeployed env cannot inherit it"
    )
    assert "COALESCE($5::double precision, c.radius_km)" in tm._SWEEP_SQL, (
        "a NULL cutoff must fall back to each provider's own absorb radius"
    )


def test_link_reach_prefers_the_environment_setting(monkeypatch):
    """Check link reach reads the setting, and falls back to the code default when absent."""
    monkeypatch.setattr(tm.settings, "TOWN_MAPPING_LINK_MAX_KM", 50.0, raising=False)
    assert tm._link_reach_km() == 50.0

    monkeypatch.setattr(tm.settings, "TOWN_MAPPING_LINK_MAX_KM", None, raising=False)
    assert tm._link_reach_km() is None, "unset must mean the absorb radius, not zero"

    monkeypatch.delattr(tm.settings, "TOWN_MAPPING_LINK_MAX_KM", raising=False)
    assert tm._link_reach_km() is tm.TOWN_MAPPING_LINK_MAX_KM, (
        "a settings object without the attribute must fall back to the code default"
    )


def test_sweep_reports_which_tier_resolved_each_link():
    """Check the sweep returns whether each link was covered and its distance."""
    assert "(t.distance_km <= c.radius_km) AS covered" in tm._SWEEP_SQL
    assert "r.covered, r.distance_km" in tm._SWEEP_SQL, (
        "the caller needs to distinguish a covering link from a widened-reach one"
    )


def test_mirror_retry_is_bounded_and_backs_off():
    """Check mirror retries are bounded with positive, non-shrinking backoff."""
    assert tm.TOWN_MAPPING_MIRROR_ATTEMPTS >= 2
    assert len(tm.TOWN_MAPPING_MIRROR_BACKOFF_SECONDS) >= 1
    assert all(d > 0 for d in tm.TOWN_MAPPING_MIRROR_BACKOFF_SECONDS)
    assert list(tm.TOWN_MAPPING_MIRROR_BACKOFF_SECONDS) == sorted(
        tm.TOWN_MAPPING_MIRROR_BACKOFF_SECONDS
    ), "backoff must not shrink"


_ROW = {
    "providerid": "p1",
    "providerbusinessname": "Test Business",
    "townprofileid": "t1",
    "townname": "Testville",
}


@pytest.mark.asyncio
async def test_mirror_retry_recovers_after_a_failed_attempt(monkeypatch):
    """Check the mirror retry stops at the first success and passes the row's ids."""
    monkeypatch.setattr(tm, "TOWN_MAPPING_MIRROR_BACKOFF_SECONDS", (0.0,))
    calls = []

    async def flaky(**kwargs):
        calls.append(kwargs)
        return len(calls) >= 2

    assert await tm._mirror_with_retry(flaky, _ROW) is True
    assert len(calls) == 2, "should stop as soon as it succeeds"
    assert calls[0]["etownproviderid"] == calls[0]["provider_id"] == "p1"
    assert calls[0]["townprofileid"] == "t1"


@pytest.mark.asyncio
async def test_mirror_retry_gives_up_after_the_attempt_ceiling(monkeypatch):
    """Check the mirror retry returns False after the configured number of attempts."""
    monkeypatch.setattr(tm, "TOWN_MAPPING_MIRROR_BACKOFF_SECONDS", (0.0,))
    calls = []

    async def always_false(**kwargs):
        calls.append(kwargs)
        return False

    assert await tm._mirror_with_retry(always_false, _ROW) is False
    assert len(calls) == tm.TOWN_MAPPING_MIRROR_ATTEMPTS


@pytest.mark.asyncio
async def test_mirror_retry_survives_a_raising_mirror(monkeypatch):
    """Check a raising mirror is retried like a False result and ends in False."""
    monkeypatch.setattr(tm, "TOWN_MAPPING_MIRROR_BACKOFF_SECONDS", (0.0,))
    calls = []

    async def boom(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("dataverse exploded")

    assert await tm._mirror_with_retry(boom, _ROW) is False
    assert len(calls) == tm.TOWN_MAPPING_MIRROR_ATTEMPTS, (
        "an exception must be retried like a False, not abort the pass"
    )


@pytest.mark.asyncio
async def test_mirror_retry_propagates_cancellation(monkeypatch):
    """Check the mirror retry re-raises CancelledError."""
    monkeypatch.setattr(tm, "TOWN_MAPPING_MIRROR_BACKOFF_SECONDS", (0.0,))

    async def cancelled(**kwargs):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await tm._mirror_with_retry(cancelled, _ROW)


class _LoopHarness:
    """Drives _mapping_loop for a bounded number of iterations, then cancels it.

    _mapping_loop is the glue nothing else covers: sleep until the window, sweep, mirror each
    row, count, loop. It is patched down to zero delay so a full iteration runs in-process.
    """

    def __init__(self, monkeypatch, rows, sweep_error=None):
        """Patch the schedule, sleep, sweep and Dataverse mirror with in-process fakes."""
        self.rows = rows
        self.sweep_error = sweep_error
        self.sweeps = 0
        self.mirrored = []
        self.slept = []
        monkeypatch.setattr(tm, "_next_run_at", lambda now=None: (_ist(2026, 9, 6, 4), 0.0))
        monkeypatch.setattr(tm.asyncio, "sleep", self._sleep)
        monkeypatch.setattr(tm, "sweep_unlinked_providers", self._sweep)
        monkeypatch.setattr(
            "app.etown.mytown.mytown_syncing.sync_etownprovider_to_dataverse", self._mirror
        )

    async def _sleep(self, delay):
        """Record the delay and cancel the loop after the third sleep."""
        self.slept.append(delay)
        if len(self.slept) > 2:
            raise asyncio.CancelledError()

    async def _sweep(self, *a, **kw):
        """Count the sweep and return the rows, or raise the configured error."""
        self.sweeps += 1
        if self.sweep_error:
            raise self.sweep_error
        return self.rows

    async def _mirror(self, **kwargs):
        """Record the mirror call and report success."""
        self.mirrored.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_mapping_loop_sweeps_then_mirrors_then_waits_again(monkeypatch):
    """Check the loop waits, sweeps, mirrors each row in order, and waits again."""
    rows = [
        {"providerid": "p1", "providerbusinessname": "A", "townprofileid": "t1",
         "townname": "Alpha", "covered": True, "distance_km": 1.0},
        {"providerid": "p2", "providerbusinessname": "B", "townprofileid": "t1",
         "townname": "Alpha", "covered": False, "distance_km": 30.0},
    ]
    h = _LoopHarness(monkeypatch, rows)

    with pytest.raises(asyncio.CancelledError):
        await tm._mapping_loop()

    assert h.sweeps >= 1, "the loop must actually run a pass"
    assert [m["provider_id"] for m in h.mirrored[:2]] == ["p1", "p2"]
    assert h.mirrored[0]["townprofileid"] == "t1"
    assert h.mirrored[0]["business_name"] == "A"
    assert h.slept[0] == 0.0, "it waits for the window before the first pass"
    assert len(h.slept) > 1, "it must loop and wait again, not exit after one pass"


@pytest.mark.asyncio
async def test_mapping_loop_survives_a_failing_pass_and_keeps_its_schedule(monkeypatch):
    """Check a failing sweep does not stop the loop from running later passes."""
    h = _LoopHarness(monkeypatch, [], sweep_error=RuntimeError("database gone"))

    with pytest.raises(asyncio.CancelledError):
        await tm._mapping_loop()

    assert h.sweeps >= 2, "a failed pass must not end the worker"
    assert not h.mirrored


@pytest.mark.asyncio
async def test_mapping_loop_mirrors_nothing_when_a_pass_finds_nothing(monkeypatch):
    """Check an empty sweep mirrors nothing."""
    h = _LoopHarness(monkeypatch, [])

    with pytest.raises(asyncio.CancelledError):
        await tm._mapping_loop()

    assert h.sweeps >= 1
    assert h.mirrored == [], "the steady state writes and mirrors nothing"


def test_town_population_counts_providers_not_link_rows():
    """Check town population counts live, non-null-provider links without DISTINCT."""
    pop = tm._SWEEP_SQL.split("WITH town_pop AS MATERIALIZED (")[1].split("),")[0]

    assert "providerid IS NOT NULL" in pop, (
        "a NULL-providerid orphan would inflate a town and skew the tie-break; dev holds 19"
    )
    assert "count(DISTINCT" not in pop, (
        "uq_etownprovider_provider_town already makes count(*) per-provider, so DISTINCT "
        "only buys a sort"
    )
    assert "COALESCE(deleted, 'false') <> 'true'" in pop, "soft-deleted links are not members"
