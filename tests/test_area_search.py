"""D4 area-search — the native-point GiST index + the /search/area endpoint.

In-process ASGI against the live DB (skips without DATABASE_URL). The CORE proof is
that the point<@box air query hits the migration-031 GiST (a bitmap index scan),
not a full seq-scan of the time-window — that is what removed the ~3.5 s cold cliff.
Air is live, so window queries use `now`-relative bounds.
"""

import os
import time
from contextlib import asynccontextmanager

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


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
async def test_geo_gist_index_is_used_and_fast():
    """THE D4 proof: the point<@box air query is an index scan on the GiST, and it's
    sub-second (the cold seq-scan cliff is gone)."""
    async with _app() as (_c, pool):
        rows = await pool.fetch(
            "EXPLAIN (ANALYZE, TIMING OFF) "
            "SELECT icao24, ts FROM state_vectors "
            "WHERE point(longitude, latitude) <@ box(point(35.0,34.0), point(35.5,34.5)) "
            "AND ts >= now() - interval '24 hours'")
        plan = "\n".join(r["QUERY PLAN"] for r in rows)
        # the native-point GiST (migration 031) is chosen — a bitmap index scan on
        # the live uncompressed chunks (compressed chunks columnar-scan; that's fine)
        assert "state_vectors_geo_gist" in plan, plan
        assert "Index Scan" in plan, plan
        exec_ms = float(plan.rsplit("Execution Time:", 1)[1].split("ms")[0])
        assert exec_ms < 1000, f"box query {exec_ms:.0f} ms — the cliff should be gone"


@requires_db
@pytest.mark.asyncio
async def test_area_endpoint_multidomain():
    async with _app() as (c, _pool):
        # a wide AOI box; default (2 h) window — reliably has live aircraft
        t0 = time.time()
        d = (await c.get("/api/v1/search/area", params={
            "lat1": 28.0, "lon1": 32.0, "lat2": 38.0, "lon2": 40.0,
        })).json()
        dt = time.time() - t0
        assert d["caveat"] and len(d["box"]) == 4
        assert d["box"] == [28.0, 32.0, 38.0, 40.0]        # normalised [lat_min,lon_min,lat_max,lon_max]
        for dom in ("air", "sea", "space"):
            assert isinstance(d[dom], list)
        # air is live → the default (2 h) box has aircraft; shape check
        assert d["air"], "a wide live box should have aircraft in the last 2 h"
        e = d["air"][0]
        assert {"id", "label", "sublabel", "lat", "lon", "ts"} <= set(e)
        assert dt < 3.0, f"default-window area query took {dt:.2f}s"


@requires_db
@pytest.mark.asyncio
async def test_area_domain_filter_and_reuse():
    async with _app() as (c, _pool):
        box = {"lat1": 28.0, "lon1": 32.0, "lat2": 38.0, "lon2": 40.0}
        # domain filter: air only → sea + space empty
        a = (await c.get("/api/v1/search/area", params={**box, "domains": "air"})).json()
        assert a["air"] is not None and a["sea"] == [] and a["space"] == []
        # a longer window catches a satellite pass; the space id reuses E3 by-id
        sp = (await c.get("/api/v1/search/area", params={
            **box, "domains": "space", "since": "2026-07-22T00:00:00", "until": "2026-07-22T06:00:00"})).json()
        if sp["space"]:
            nid = sp["space"][0]["id"]
            r = (await c.get("/api/v1/search/satellite", params={"q": nid})).json()
            assert r["count"] >= 1                          # area space id → E3 detail reachable


@requires_db
@pytest.mark.asyncio
async def test_area_reuse_air_by_id():
    async with _app() as (c, _pool):
        d = (await c.get("/api/v1/search/area", params={
            "lat1": 28.0, "lon1": 32.0, "lat2": 38.0, "lon2": 40.0, "domains": "air"})).json()
        if not d["air"]:
            pytest.skip("no live aircraft in the box right now")
        icao = d["air"][0]["id"]
        # an area air id resolves via the E2 by-id endpoint → its detail + /history track
        r = (await c.get("/api/v1/search/aircraft", params={"q": icao})).json()
        assert r["count"] >= 1
