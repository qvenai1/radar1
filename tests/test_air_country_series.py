"""DASH-BOARD-3 — air country × time, and the rules it must not break.

⛔ Driven against a fake pool whose rows the test controls, for the reason DASH-BOARD-2
records: a frozen expectation is satisfied by a silent answer. Change the rows, the contract
must change with them.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from tests.test_maritime_series import FakeDB, FakePool

AIR_SQL = "FROM state_vectors"
PATH = "/api/v1/air/series/country"


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


def _rows(*triples):
    # ⛔ `distinct_hulls`, not `n`: the shared definition in backend.air_daily.agg_sql names
    # the column, and this fixture must speak the same contract the endpoint reads.
    return [{"bk": _utc(2026, 7, d), "country": c, "source": s, "distinct_hulls": n,
             "rows": n * 20} for d, c, s, n in triples]


async def _get(pool, path=PATH):
    app = create_app()
    app.state.db = FakeDB(pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(path)
        assert r.status_code == 200, r.text
        return r.json()


@pytest.mark.asyncio
async def test_the_series_follows_its_rows():
    """⛔ SOURCE-FOLLOWING: two different row sets must give two different answers."""
    a = await _get(FakePool({AIR_SQL: _rows((20, "Syria", "opensky", 403))}))
    assert a["totals"] == {"Syria|opensky": 403}
    b = await _get(FakePool({AIR_SQL: _rows(
        (20, "Syria", "opensky", 403), (21, "Syria", "opensky", 500),
        (20, "Turkey", "opensky", 2307))}))
    assert b["totals"] == {"Syria|opensky": 903, "Turkey|opensky": 2307}
    assert len(b["buckets"]) == 3


@pytest.mark.asyncio
async def test_populations_are_never_folded_into_one_number():
    """⛔ opensky / adsbexchange / airplanes_live are different communities with measured
    coverage of 74.46 / 41.19 / 70.88 %. A total per COUNTRY alone would add an airframe seen
    by two feeds twice, and would merge populations that were never one."""
    d = await _get(FakePool({AIR_SQL: _rows(
        (20, "Syria", "opensky", 403), (20, "Syria", "adsbexchange", 118))}))
    assert d["split_by"] == "country × source"
    assert set(d["totals"]) == {"Syria|opensky", "Syria|adsbexchange"}
    assert "Syria" not in d["totals"]                       # no country-only total exists
    assert sorted(d["populations"]) == ["adsbexchange", "opensky"]
    assert "74.46" in d["population_note"] and "41.19" in d["population_note"]


@pytest.mark.asyncio
async def test_the_unit_is_airframes_and_says_so():
    """⛔ Air counts DISTINCT AIRFRAMES, not position reports, events or passes — the naming
    is what makes a cross-domain total visibly wrong."""
    d = await _get(FakePool({AIR_SQL: []}))
    assert "airframe" in d["unit"] and "icao24" in d["unit"]


@pytest.mark.asyncio
async def test_the_air_contract_DOES_carry_the_ratio_cutoff():
    """⛔ Unlike every maritime contract, the frame boundary applies here: absolute airframe
    counts may cross 2026-07-29T13:55:36Z, ratios may not."""
    d = await _get(FakePool({AIR_SQL: []}))
    cov = d["coverage"]
    assert cov["ratio_cutoff"] == "2026-07-29T13:55:36Z"
    assert cov["ratio_cutoff_note"] and "RATIOS MAY NOT" in cov["ratio_cutoff_note"]


@pytest.mark.asyncio
async def test_the_contract_admits_its_gaps_cannot_be_reconstructed():
    """⛔ THE HONESTY THAT MATTERS MOST HERE. `signal_events` records feed_connect/disconnect
    for aisstream ONLY, so an air outage that did not coincide with a host shutdown appears in
    NO gap list. The contract must say that rather than look complete."""
    d = await _get(FakePool({AIR_SQL: []}))
    assert "UNAVAILABLE" in d["coverage"]["gap_basis"]


@pytest.mark.asyncio
async def test_coverage_is_embedded_not_recomputed():
    d = await _get(FakePool({AIR_SQL: []}))
    cov = d["coverage"]
    assert cov["stream"] == "air_states" and cov["domain"] == "air"
    assert cov["host_gap_basis"] and "host_gaps" in cov and "gaps" in cov


@pytest.mark.asyncio
async def test_the_window_is_bounded_and_reported_not_silently_truncated():
    """The default is the largest window measured under ~3.3 s; a longer one is answered, and
    `cost_note` states what it will cost rather than trimming the request behind the caller."""
    d = await _get(FakePool({AIR_SQL: []}))
    assert d["window_hours"] == 720
    big = await _get(FakePool({AIR_SQL: []}), PATH + "?hours=8760")
    assert big["window_hours"] == 8760            # honoured, not clamped
    assert "11.5s" in big["cost_note"]


@pytest.mark.asyncio
async def test_a_nonsense_window_is_refused():
    app = create_app()
    app.state.db = FakeDB(FakePool({}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get(PATH + "?hours=0")).status_code == 422
        assert (await c.get(PATH + "?bucket=fortnight")).status_code == 422


@pytest.mark.asyncio
async def test_the_storage_decision_is_carried_in_the_payload():
    """⛔ The reason no cagg was built travels with the contract: whoever reads this later
    should not have to re-derive that `count(distinct icao24)` is not decomposable."""
    d = await _get(FakePool({AIR_SQL: []}))
    assert "count(distinct icao24)" in d["storage_note"]
    assert "hyperloglog" in d["storage_note"]
