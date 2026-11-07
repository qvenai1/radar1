"""E2 — the aircraft entity-search endpoint (/search/aircraft).

In-process (ASGI transport) against the LIVE DB — no running server needed, skips
cleanly without DATABASE_URL. Unlike the frozen maritime data, the air feeds are
live, so these tests pick a CURRENTLY-PRESENT aircraft at run time (and skip if the
feed is down) rather than asserting a fixed fixture. They verify:

  * resolution by icao24 (exact), callsign (substring, recent window) and
    registration (substring, aircraft_info);
  * the PRESENCE summary shape (identity + last-seen fix + AOI status + first_seen);
  * the track is reachable via the existing /history for the matched airframe;
  * edges — empty q → 422, a no-match query → count 0.
"""

import os
from contextlib import asynccontextmanager

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

SUMMARY_KEYS = {
    "icao24", "callsign", "registration", "aircraft_type", "icao_type",
    "operator", "operator_country", "origin_icao", "dest_icao",
    "last_ts", "last_lat", "last_lon", "baro_altitude", "on_ground",
    "velocity", "country", "near_country", "position_quality", "source", "first_seen",
}


@asynccontextmanager
async def _app():
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app
    from backend.db import Database

    app = create_app()
    app.state.db = Database(DSN)
    try:
        pool = await app.state.db.pool()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c, pool
    finally:
        await app.state.db.close()


async def _live_probe(pool):
    """A currently-present aircraft (recent fix, has callsign + position), or None."""
    return await pool.fetchrow(
        "SELECT icao24, callsign, source FROM state_vectors "
        "WHERE ts >= now() - interval '10 min' AND callsign <> '' AND latitude IS NOT NULL "
        "LIMIT 1"
    )


@requires_db
@pytest.mark.asyncio
async def test_search_aircraft_by_icao24_summary_and_track():
    async with _app() as (c, pool):
        probe = await _live_probe(pool)
        if probe is None:
            pytest.skip("no live aircraft in the last 10 min (air feed down)")
        icao = probe["icao24"]
        print(f"[E2 live fixture] icao24={icao} callsign={probe['callsign'].strip()} source={probe['source']}")

        r = await c.get("/api/v1/search/aircraft", params={"q": icao})
        assert r.status_code == 200
        d = r.json()
        assert d["entity"] == "aircraft" and d["caveat"] and d["count"] >= 1
        m = next(x for x in d["matches"] if x["icao24"] == icao)
        assert SUMMARY_KEYS <= set(m)
        # a presence summary: it has a last-seen fix and a "tracked since"
        assert m["last_ts"] and m["last_lat"] is not None and m["last_lon"] is not None
        assert m["first_seen"] and m["source"]

        # the track is reachable via the existing /history for this airframe
        h = await c.get("/api/v1/history", params={"icao24": icao, "source": m["source"]})
        assert h.status_code == 200 and "points" in h.json()


@requires_db
@pytest.mark.asyncio
async def test_search_aircraft_by_callsign_recent_window():
    async with _app() as (c, pool):
        probe = await _live_probe(pool)
        if probe is None:
            pytest.skip("no live aircraft (air feed down)")
        icao, cs = probe["icao24"], probe["callsign"].strip()

        # exact callsign resolves to the airframe (via the recent-window scan)
        d = (await c.get("/api/v1/search/aircraft", params={"q": cs})).json()
        assert any(x["icao24"] == icao for x in d["matches"])
        # a broader prefix returns at least that aircraft too
        if len(cs) >= 3:
            dp = (await c.get("/api/v1/search/aircraft", params={"q": cs[:3]})).json()
            assert dp["count"] >= 1


@requires_db
@pytest.mark.asyncio
async def test_search_aircraft_by_registration():
    async with _app() as (c, pool):
        row = await pool.fetchrow(
            "SELECT ai.icao24, ai.registration FROM aircraft_info ai "
            "WHERE ai.registration IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM state_vectors sv WHERE sv.icao24 = ai.icao24 "
            "  AND sv.ts >= now() - interval '30 min') LIMIT 1"
        )
        if row is None:
            pytest.skip("no recently-tracked aircraft with a registration")
        reg, icao = row["registration"], row["icao24"]
        print(f"[E2 reg fixture] registration={reg} icao24={icao}")
        d = (await c.get("/api/v1/search/aircraft", params={"q": reg})).json()
        m = next((x for x in d["matches"] if x["icao24"] == icao), None)
        assert m is not None and m["registration"] == reg


@requires_db
@pytest.mark.asyncio
async def test_search_aircraft_edges():
    async with _app() as (c, _pool):
        # empty q → 422 (min_length), never a whole-table scan
        assert (await c.get("/api/v1/search/aircraft", params={"q": ""})).status_code == 422
        # a query matching no icao24/registration/recent-callsign → count 0 (not an error)
        d = (await c.get("/api/v1/search/aircraft", params={"q": "zzq7xnomatch"})).json()
        assert d["count"] == 0 and d["matches"] == []
