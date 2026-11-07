"""DASH-BOARD-2 — the four maritime question contracts (ب · ج · د · هـ).

⛔ EVERY TEST HERE CHANGES THE SOURCE AND ASSERTS THE CONTRACT FOLLOWED. Two lessons paid for
in this project make that the only acceptable shape:
  · the GNSS endpoint tests asserted `count == 805` and passed for 13 days BECAUSE the
    detector was dead — a frozen expectation is satisfied by a silent answer;
  · DASH-BOARD-0's own first output reported `observed_fraction = 0.0` for five streams and
    would have passed any test that hard-coded 0.0.
So the endpoints are driven against a FAKE POOL whose rows the test controls: change the rows,
the contract must change with them. No production data, no DSN needed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


class FakePool:
    """Returns whatever the test says the database contains.

    `fetch` is routed by a substring of the SQL, so a test states its fixture in terms of the
    question being asked rather than the query text."""

    def __init__(self, routes: dict[str, list[dict]], row: dict | None = None):
        self.routes = routes
        self.row = row or {"a": _utc(2026, 7, 11), "b": _utc(2026, 8, 4), "n": 0}

    async def fetch(self, sql, *args):
        for needle, rows in self.routes.items():
            if needle in sql:
                return rows
        return []

    async def fetchrow(self, sql, *args):
        return self.row

    async def fetchval(self, sql, *args):
        return None


class FakeDB:
    def __init__(self, pool):
        self._pool = pool

    async def pool(self):
        return self._pool

    async def close(self):
        return None


def _app(pool):
    app = create_app()
    app.state.db = FakeDB(pool)
    return app


async def _get(pool, path):
    app = _app(pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(path)
        assert r.status_code == 200, r.text
        return r.json()


# ------------------------------------------------------- ب — AIS-gap frequency

GAPS_SQL = "event_type = 'ais_gap'"


@pytest.mark.asyncio
async def test_ais_gap_series_follows_its_rows():
    """⛔ SOURCE-FOLLOWING: change the rows, the buckets and totals change with them."""
    first = await _get(FakePool({GAPS_SQL: [
        {"bk": _utc(2026, 7, 20), "classification": "candidate", "n": 3},
        {"bk": _utc(2026, 7, 20), "classification": "outage-excluded", "n": 5},
    ]}), "/api/v1/maritime/series/ais-gaps?bucket=day")
    assert first["totals"] == {"candidate": 3, "outage-excluded": 5}

    second = await _get(FakePool({GAPS_SQL: [
        {"bk": _utc(2026, 7, 20), "classification": "candidate", "n": 3},
        {"bk": _utc(2026, 7, 21), "classification": "candidate", "n": 9},
    ]}), "/api/v1/maritime/series/ais-gaps?bucket=day")
    assert second["totals"] == {"candidate": 12}
    assert len(second["buckets"]) == 2


@pytest.mark.asyncio
async def test_ais_gap_series_never_collapses_the_classification():
    """⛔ THE HONESTY AXIS. `outage-excluded` and `jamming-excluded` are OUR downtime, not
    vessel behaviour — a single summed line would plot our outages as ships going dark. The
    contract must keep every class as its own keyed bucket and declare the split."""
    d = await _get(FakePool({GAPS_SQL: [
        {"bk": _utc(2026, 7, 20), "classification": "candidate", "n": 3},
        {"bk": _utc(2026, 7, 20), "classification": "outage-excluded", "n": 5},
        {"bk": _utc(2026, 7, 20), "classification": "jamming-excluded", "n": 1},
    ]}), "/api/v1/maritime/series/ais-gaps?bucket=day")
    assert d["split_by"] == "classification"
    assert {b["key"] for b in d["buckets"]} == {"candidate", "outage-excluded", "jamming-excluded"}
    # every bucket is keyed — nothing is an unlabelled total
    assert all(b["key"] for b in d["buckets"])
    assert "انقطاعاتنا نحن" in d["split_note"]


# ------------------------------------------------------ د — multi-pattern hulls

MULTI_SQL = "maritime_hull_events"


@pytest.mark.asyncio
async def test_multi_pattern_series_follows_its_rows():
    d = await _get(FakePool({MULTI_SQL: [
        {"bk": _utc(2026, 7, 20), "multi": 5, "total": 464},
        {"bk": _utc(2026, 7, 27), "multi": 159, "total": 1481},
    ]}), "/api/v1/maritime/series/multi-pattern?bucket=week")
    assert d["totals"] == {"multi_pattern": 164, "hulls_with_any_event": 1945}

    d2 = await _get(FakePool({MULTI_SQL: [
        {"bk": _utc(2026, 7, 20), "multi": 1, "total": 2},
    ]}), "/api/v1/maritime/series/multi-pattern?bucket=week")
    assert d2["totals"] == {"multi_pattern": 1, "hulls_with_any_event": 2}


@pytest.mark.asyncio
async def test_multi_pattern_reads_the_existing_hull_view_not_a_second_definition():
    """⛔ A time-bucketed view of an EXISTING contract. If this ever stops reading the view
    `/maritime/composite` ranks from, "how many" could disagree with "which ones"."""
    d = await _get(FakePool({MULTI_SQL: []}), "/api/v1/maritime/series/multi-pattern")
    assert "maritime_hull_events" in d["source"]
    assert d["method"] == "hull-composite-v1"
    assert "detector" in d["split_note"] or "الكواشف" in d["split_note"]


# -------------------------------------------------------- ج — rendezvous points

RV_SQL = "event_type = 'rendezvous'"


def _rv(n, day=20):
    return [{"mmsi": f"1000000{i:02d}", "start_ts": _utc(2026, 7, day, i % 24),
             "end_ts": _utc(2026, 7, day + 1, i % 24), "duration_s": 3600.0,
             "start_lat": 34.0 + i / 100, "start_lon": 35.0 + i / 100,
             "end_lat": 34.1, "end_lon": 35.1, "classification": "candidate"}
            for i in range(n)]


@pytest.mark.asyncio
async def test_rendezvous_returns_points_and_refuses_a_density_surface():
    """⛔ N = 47 over 24 days does not support a density surface. The refusal is stated IN THE
    PAYLOAD, not in a comment, so a client cannot bin behind the contract's back."""
    d = await _get(FakePool({RV_SQL: _rv(5)}), "/api/v1/maritime/series/rendezvous-points")
    assert d["count"] == 5
    assert len(d["points"]) == 5
    assert d["density_refused"]
    assert "كثافة" in d["density_refused"]
    assert all(p["classification"] == "candidate" for p in d["points"])


@pytest.mark.asyncio
async def test_rendezvous_count_and_rate_follow_the_rows():
    """The count and per-day rate are computed from what was returned, not stated."""
    a = await _get(FakePool({RV_SQL: _rv(4)}), "/api/v1/maritime/series/rendezvous-points")
    b = await _get(FakePool({RV_SQL: _rv(40)}), "/api/v1/maritime/series/rendezvous-points")
    assert a["count"] == 4 and b["count"] == 40
    assert str(a["count"]) in a["density_refused"]
    assert str(b["count"]) in b["density_refused"]


@pytest.mark.asyncio
async def test_rendezvous_with_no_rows_is_empty_not_invented():
    d = await _get(FakePool({RV_SQL: []}), "/api/v1/maritime/series/rendezvous-points")
    assert d["count"] == 0 and d["points"] == [] and d["span_days"] is None


# ------------------------------------------------------------- هـ — flag shares

FLAG_SQL = "maritime_hull_composite"


@pytest.mark.asyncio
async def test_flag_shares_follow_the_rows_and_carry_their_denominator():
    d = await _get(FakePool({FLAG_SQL: [
        {"flag": "Liberia", "n": 41}, {"flag": "Panama", "n": 38}, {"flag": None, "n": 1},
    ]}), "/api/v1/maritime/flags/multi-pattern")
    assert d["denominator"] == 80
    assert d["attributed"] == 79 and d["unattributed"] == 1
    liberia = next(s for s in d["slices"] if s["flag"] == "Liberia")
    assert liberia["hulls"] == 41
    assert liberia["pct"] == round(100 * 41 / 80, 2)
    # ⛔ the denominator is stated, and its precision limit with it
    assert "80" in d["denominator_note"]


@pytest.mark.asyncio
async def test_the_unattributed_slice_is_emitted_even_at_full_attribution():
    """⛔ Dropping a zero-sized «غير منسوب» slice teaches a reader that attribution is total.
    It is not — the population changes, and the slice is where that will show."""
    d = await _get(FakePool({FLAG_SQL: [{"flag": "Malta", "n": 7}]}),
                   "/api/v1/maritime/flags/multi-pattern")
    assert d["unattributed"] == 0
    un = [s for s in d["slices"] if not s["attributed"]]
    assert len(un) == 1 and un[0]["hulls"] == 0 and un[0]["flag"] == "غير منسوب"


@pytest.mark.asyncio
async def test_the_flag_derivation_is_named_and_not_confused_with_the_other_one():
    """⛔ Two derivations disagree by ~6 points; neither may be presented as the other."""
    d = await _get(FakePool({FLAG_SQL: [{"flag": "Malta", "n": 7}]}),
                   "/api/v1/maritime/flags/multi-pattern")
    assert d["attribution_field"] == "vessel_identity.flag_country"
    assert "flag_iso" in d["attribution_note"] and "MID" in d["attribution_note"]


# ----------------------------------------------- rules that bind all four

ALL_FOUR = [
    ("/api/v1/maritime/series/ais-gaps", GAPS_SQL),
    ("/api/v1/maritime/series/multi-pattern", MULTI_SQL),
    ("/api/v1/maritime/series/rendezvous-points", RV_SQL),
    ("/api/v1/maritime/flags/multi-pattern", FLAG_SQL),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,needle", ALL_FOUR)
async def test_every_contract_names_its_unit_and_a_real_source(path, needle):
    """⛔ No cross-domain total is possible if each series says what it counts, and no number
    is anonymous if each names its upstream rather than «our data»."""
    d = await _get(FakePool({needle: []}), path)
    assert d["unit"] and "our data" not in d["source"].lower()
    assert d["source"] and d["method"] and d["caveat"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,needle", ALL_FOUR)
async def test_every_contract_embeds_its_coverage_rather_than_recomputing_it(path, needle):
    d = await _get(FakePool({needle: []}), path)
    cov = d["coverage"]
    assert cov is not None
    assert cov["stream"] and cov["unit"] and cov["host_gap_basis"]
    assert "series_start" in cov and "host_gaps" in cov and "gaps" in cov


@pytest.mark.asyncio
@pytest.mark.parametrize("path,needle", ALL_FOUR)
async def test_no_maritime_contract_carries_the_air_ratio_cutoff(path, needle):
    """⛔ `ratio_cutoff` is the AIR sampling-frame narrowing. No maritime stream is sourced
    from that frame, so stamping it here would invent a discontinuity that never happened."""
    d = await _get(FakePool({needle: []}), path)
    assert d["coverage"]["ratio_cutoff"] is None


@pytest.mark.asyncio
async def test_an_unknown_bucket_is_refused_not_silently_defaulted():
    app = _app(FakePool({}))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/maritime/series/ais-gaps?bucket=fortnight")
        assert r.status_code == 422
