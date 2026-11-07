"""ZONES-2 — the on-query /maritime/zone-dwell endpoint (annotation-only).

In-process ASGI against the LIVE DB (skips without DATABASE_URL), mirroring the
airport endpoint test: asserts SHAPE + INVARIANTS (signal↔verdict↔radius_basis
consistency, gating filters, exclusion accounting) rather than frozen counts, which
drift with the live feed. No events are written.
"""

import os
from contextlib import asynccontextmanager

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@asynccontextmanager
async def _client():
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app
    from backend.db import Database

    app = create_app()
    app.state.db = Database(DSN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c
    finally:
        await app.state.db.close()


@requires_db
@pytest.mark.asyncio
async def test_zone_dwell_shape_and_invariants():
    async with _client() as c:
        d = (await c.get("/api/v1/maritime/zone-dwell", params={"hours": 168})).json()
        assert d["available"] and d["method"] == "zone-dwell-v1"
        # caveat names the legitimate explanations (lead-not-proof discipline)
        assert "anchor" in d["caveat"].lower() and "fishing" in d["caveat"].lower()
        assert d["zones_queried"] == 35              # detectable zones (36 − 1 undetectable)
        for s in d["sessions"]:
            assert {"zone_id", "signal", "verdict", "radius_basis", "min_distance_m"} <= set(s)
            assert s["radius_basis"] in ("sourced", "assumed")
            assert s["duration_is_lower_bound"] is True
            assert s["ship_type_caveat"]             # the self-reported-type caveat rides on every row
            # dwell = low position-derived speed (never reported SOG)
            assert s["mean_implied_speed_kn"] is None or s["mean_implied_speed_kn"] <= 2.0 + 1e-6
            # signal ↔ verdict ↔ evidence-strength consistency
            if s["signal"] == "safety_zone_intrusion":
                assert s["verdict"] == "intrusion" and s["zone_type"] == "platform"
                assert s["radius_basis"] == "sourced"
            if s["signal"] == "field_dwell":
                assert s["zone_type"] == "gas_field" and s["radius_basis"] == "assumed"
            if s["cloaking_exposed"]:
                assert s["verdict"] == "demoted_type"   # only demotions can be cloaking-exposed
        # summary internal consistency: every emitted session is in exactly one verdict bucket
        su = d["summary"]
        assert (su["leads"] + su["demoted"] + su["excluded"]
                == su["safety_zone_intrusion"] + su["standoff_dwell"] + su["field_dwell"])
        # accounting: a zone with no sessions is flagged no_dwell_observed (not "type-gated")
        for a in d["accounting"]:
            assert a["no_dwell_observed"] == (a["sessions_total"] == 0)
        # baseline-readiness surfaced (ZONES-4 dormancy fix): the deferral is a VISIBLE state
        r = d["readiness"]
        assert "baseline_ready" in r and isinstance(r["per_zone"], list)
        assert r["binding_bar"] in (None, "days", "vessels", "sessions")
        assert r["window_hours"] == 168


@requires_db
@pytest.mark.asyncio
async def test_zone_dwell_gating_filters_and_single_zone():
    async with _client() as c:
        # include_gated=false hides the EXCLUDED set (anchorage/short) but not demotions
        ng = (await c.get("/api/v1/maritime/zone-dwell",
                          params={"hours": 168, "include_gated": "false"})).json()
        assert all(s["verdict"] not in ("excluded_anchorage", "excluded_short") for s in ng["sessions"])
        # signal filter
        fd = (await c.get("/api/v1/maritime/zone-dwell",
                          params={"hours": 168, "signal": "field_dwell"})).json()
        assert all(s["signal"] == "field_dwell" for s in fd["sessions"])
        # a single zone
        one = (await c.get("/api/v1/maritime/zone-dwell",
                           params={"hours": 168, "zone_id": "gasfield-karish"})).json()
        assert one["zones_queried"] == 1
        assert all(s["zone_id"] == "gasfield-karish" for s in one["sessions"])
        # an UNDETECTABLE zone id resolves to nothing (the mis-sited Tamar node)
        none = (await c.get("/api/v1/maritime/zone-dwell",
                            params={"hours": 168, "zone_id": "platform-osm-way-215263078"})).json()
        assert none["zones_queried"] == 0 and none["sessions"] == []
