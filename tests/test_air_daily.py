"""DASH-BOARD-3b — the daily air summary table, its unit, and its silences.

⛔ The headline test is the EQUIVALENCE test: the summary table and the live endpoint must
agree, literally, on the same day. They share one SQL definition (`air_daily.agg_sql`) so they
cannot drift — this pins that promise to a real day of real data, the way DASH-4 pinned
`/maritime/vessel` to `_read_snapshot`.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from backend.air_daily import BASE_WHERE, agg_sql, compute_day
from backend.app import create_app
from tests.test_maritime_series import FakeDB, FakePool

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")
DAILY_SQL = "FROM air_country_daily"
PATH = "/api/v1/air/series/country-daily"


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


async def _get(pool, path=PATH):
    app = create_app()
    app.state.db = FakeDB(pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(path)
        assert r.status_code == 200, r.text
        return r.json()


# ------------------------------------------------------------ one definition

def test_both_readers_render_the_same_aggregate_definition():
    """⛔ The live series and the nightly job must not have two SQL texts to drift between."""
    live = agg_sql("day", [BASE_WHERE, "ts >= $1"], "$2")
    job = agg_sql("day", [BASE_WHERE, "ts >= $1", "ts < $2"], "$3")
    for sql in (live, job):
        assert "count(DISTINCT icao24)" in sql
        assert "country IS NOT NULL" in sql
        assert "GROUP BY 1, 2, 3" in sql          # day × country × source, never fewer


def test_the_bucket_is_the_only_thing_that_varies():
    for b in ("hour", "day", "week", "month"):
        assert f"date_trunc('{b}', ts)" in agg_sql(b, [BASE_WHERE], "$1")


# ------------------------------------------------------------- ⛔ EQUIVALENCE

@requires_db
@pytest.mark.asyncio
async def test_the_table_and_the_live_endpoint_agree_on_a_real_day():
    """⛔ THE GATE. Recompute a day through the shared definition and compare it, row for row,
    with what the summary table stored for that day. A mismatch means the two readers have
    drifted — which is the whole failure this design exists to prevent."""
    import asyncpg
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    try:
        stored = await pool.fetch(
            "SELECT day, country, source, distinct_hulls, rows FROM air_country_daily "
            "WHERE day = (SELECT max(day) FROM air_country_daily) ORDER BY country, source")
        assert stored, "the summary table is empty — backfill first"
        day: date = stored[0]["day"]
        live = await compute_day(pool, day)
        live_map = {(r["country"], r["source"]): (r["distinct_hulls"], r["rows"]) for r in live}
        stored_map = {(r["country"], r["source"]): (r["distinct_hulls"], r["rows"])
                      for r in stored}
        assert stored_map == live_map, f"table and live definition disagree on {day}"
    finally:
        await pool.close()


@requires_db
@pytest.mark.asyncio
async def test_the_host_outage_days_are_absent_never_zero():
    """⛔ A zero reads «no aircraft flew»; an absent row reads «we did not observe». The four
    measured host shutdowns must have NO row, and no row anywhere may be zero."""
    import asyncpg
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    try:
        for d in ("2026-07-17", "2026-07-23", "2026-07-24", "2026-07-28"):
            n = await pool.fetchval(
                "SELECT count(*) FROM air_country_daily WHERE day = $1", date.fromisoformat(d))
            assert n == 0, f"{d} has {n} rows — a host-outage day must stay absent"
        zeros = await pool.fetchval(
            "SELECT count(*) FROM air_country_daily WHERE distinct_hulls = 0")
        assert zeros == 0
    finally:
        await pool.close()


@requires_db
@pytest.mark.asyncio
async def test_recomputing_a_day_does_not_duplicate_rows():
    """⛔ UPSERT, not INSERT — a missed run is made up by the next one without duplicating."""
    import asyncpg
    from backend.air_daily import upsert_day
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    try:
        day = await pool.fetchval("SELECT max(day) FROM air_country_daily")
        before = await pool.fetchval(
            "SELECT count(*) FROM air_country_daily WHERE day = $1", day)
        await upsert_day(pool, day)
        await upsert_day(pool, day)
        after = await pool.fetchval(
            "SELECT count(*) FROM air_country_daily WHERE day = $1", day)
        assert after == before
    finally:
        await pool.close()


# ------------------------------------------------------------- the contract

def _rows(*t):
    return [{"day": _utc(2026, 7, d), "country": c, "source": s,
             "distinct_hulls": n, "rows": n * 20} for d, c, s, n in t]


@pytest.mark.asyncio
async def test_the_contract_follows_its_rows():
    a = await _get(FakePool({DAILY_SQL: _rows((20, "Syria", "opensky", 300))}))
    assert a["totals"] == {"Syria|opensky": 300}
    b = await _get(FakePool({DAILY_SQL: _rows(
        (20, "Syria", "opensky", 300), (21, "Syria", "opensky", 400))}))
    # ⛔ 700 is HULL-DAYS, not 700 distinct airframes
    assert b["totals"] == {"Syria|opensky": 700}
    assert b["days_present"] == 2


@pytest.mark.asyncio
async def test_the_unit_is_named_hull_days_and_warned_about():
    """⛔ The number must never be readable as «distinct airframes»."""
    d = await _get(FakePool({DAILY_SQL: _rows((20, "Syria", "opensky", 300))}))
    assert "hull-days" in d["unit"] and "NOT distinct airframes" in d["unit"]
    assert "بدن-يوم" in d["unit_warning"]
    assert "/air/series/country" in d["unit_warning"]     # names the other unit explicitly


@pytest.mark.asyncio
async def test_populations_are_never_folded_together():
    d = await _get(FakePool({DAILY_SQL: _rows(
        (20, "Syria", "opensky", 300), (20, "Syria", "adsbexchange", 90))}))
    assert set(d["totals"]) == {"Syria|opensky", "Syria|adsbexchange"}
    assert "Syria" not in d["totals"]


@pytest.mark.asyncio
async def test_absent_days_are_declared_and_the_air_rules_still_hold():
    d = await _get(FakePool({DAILY_SQL: []}))
    assert "2026-07-17" in d["days_absent_note"]
    cov = d["coverage"]
    assert cov["ratio_cutoff"] == "2026-07-29T13:55:36Z"      # air: applies
    assert "UNAVAILABLE" in cov["gap_basis"]                  # air: gaps unreconstructable
