"""
GET /api/v1/maritime/crossref — the AIS×MarineTraffic cross-view endpoint.

In-process (ASGI) against the live DB; skips without DATABASE_URL. Asserts the
summary shape, the confidence filter, and that every returned link carries an
MMSI + confidence + the honest caveat.
"""

from __future__ import annotations

import os

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@requires_db
@pytest.mark.asyncio
async def test_crossref_shape_and_filter():
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app
    from backend.db import Database

    app = create_app()
    app.state.db = Database(DSN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/maritime/crossref")
            assert r.status_code == 200
            d = r.json()
            assert d["caveat"] and "not proof" in d["caveat"].lower()
            if not d["available"]:
                pytest.skip("no cross-ref computed in this DB")

            s = d["summary"]
            assert {"matched", "strong", "probable", "weak", "ambiguous",
                    "discrepancies", "mt_vessels", "mt_matched", "mt_only"} <= set(s)
            assert s["matched"] == s["strong"] + s["probable"]
            assert s["mt_matched"] + s["mt_only"] == s["mt_vessels"]

            for m in d["matches"]:
                assert m["mmsi"]                       # a link always has an MMSI
                assert m["confidence"] in ("strong", "probable", "weak", None)
                assert "ship_id" in m and "link_source" in m

            # confidence floor: strong-only must not return any weak/probable
            rs = await c.get("/api/v1/maritime/crossref", params={"min_confidence": "strong"})
            for m in rs.json()["matches"]:
                if m["link_source"] == "ais-correlation":
                    assert m["confidence"] == "strong"
    finally:
        await app.state.db.close()
