"""E3 — the satellite entity-search endpoint (/search/satellite).

In-process (ASGI transport) against the live DB — skips cleanly without DATABASE_URL.
The conceptual difference: a satellite has NO stored fixes, so the summary composes
the satellite LAYERS whose vintages DIFFER and must stay distinct (identity SATCAT ~
2026 / orbit derived-SGP4 live / strategic UCS FROZEN 2023), and the TLE travels with
the summary for CLIENT-side ground-track propagation. Verifies:

  * resolution by NORAD (exact) and object_name (substring) — ISS, NORAD 25544;
  * the three vintages are present AND distinct (strategic is a 2023 snapshot);
  * the TLE (the ground-track propagation input) is carried;
  * a GEO satellite is flagged is_geostationary (the honest GEO display case);
  * edges — empty q → 422, no-match → count 0.
"""

import os
from contextlib import asynccontextmanager

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

ISS = 25544


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


@requires_db
@pytest.mark.asyncio
async def test_search_satellite_iss_three_distinct_vintages_and_tle():
    async with _app() as (c, _pool):
        r = await c.get("/api/v1/search/satellite", params={"q": str(ISS)})
        assert r.status_code == 200
        d = r.json()
        assert d["entity"] == "satellite" and d["caveat"] and d["count"] >= 1
        m = next(x for x in d["matches"] if x["norad_id"] == ISS)

        # identity (SATCAT) + orbit (derived-SGP4) + strategic (UCS) all present
        assert m["object_name"] and m["identity_vintage"]
        assert m["orbit_class"] == "LEO" and m["is_geostationary"] is False
        assert m["inclination_deg"] and 50 < m["inclination_deg"] < 53   # ISS ~51.6°
        assert m["orbit_vintage"]
        assert m["users"] and m["strategic_vintage"]

        # the THREE vintages are distinct, and strategic is the frozen 2023 snapshot
        assert m["strategic_vintage"].startswith("2023")
        assert m["identity_vintage"] != m["strategic_vintage"]
        assert m["orbit_vintage"][:4] != "2023"          # orbit is current, not the 2023 snapshot

        # the TLE travels with the summary — the client's ground-track input
        assert m["tle1"] and m["tle2"] and m["tle1"].startswith("1 ") and m["tle2"].startswith("2 ")

        # AOI passes: forward-window prediction — if any, the window is stated
        if m["n_passes"] > 0:
            assert m["pass_window_start"] and m["pass_window_end"]


@requires_db
@pytest.mark.asyncio
async def test_search_satellite_by_name():
    async with _app() as (c, _pool):
        d = (await c.get("/api/v1/search/satellite", params={"q": "ISS"})).json()
        assert d["count"] >= 1 and any(x["norad_id"] == ISS for x in d["matches"])


@requires_db
@pytest.mark.asyncio
async def test_search_satellite_geo_flagged():
    async with _app() as (c, pool):
        row = await pool.fetchrow(
            "SELECT o.norad_id FROM satellite_orbit o "
            "WHERE o.is_geostationary AND EXISTS ("
            "  SELECT 1 FROM satellite_elements e WHERE e.norad_id = o.norad_id) LIMIT 1"
        )
        if row is None:
            pytest.skip("no GEO satellite with a TLE in the catalog")
        norad = row["norad_id"]
        d = (await c.get("/api/v1/search/satellite", params={"q": str(norad)})).json()
        m = next(x for x in d["matches"] if x["norad_id"] == norad)
        # the honest GEO display case: flagged so the ground-track renders as a
        # stationed band, not a moving track
        assert m["is_geostationary"] is True
        assert m["period_min"] and m["period_min"] > 1400   # ~sidereal day


@requires_db
@pytest.mark.asyncio
async def test_search_satellite_edges():
    async with _app() as (c, _pool):
        assert (await c.get("/api/v1/search/satellite", params={"q": ""})).status_code == 422
        d = (await c.get("/api/v1/search/satellite", params={"q": "zznomatch999"})).json()
        assert d["count"] == 0 and d["matches"] == []
