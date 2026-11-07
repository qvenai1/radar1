"""
GET /api/v1/maritime/dark-vessels — GFW satellite-AIS dark-vessel endpoint.

Offline: the GfwAdapter is mocked (canned gaps in the real live shape), so the
mapping / dedup / disabled-contract are tested without a token or a DB.
"""

from __future__ import annotations

import pytest

# one gap in the exact shape find_ais_gaps returns
GAP = {
    "mmsi": "636014423", "name": None, "flag": "LBR",
    "lat": 28.68, "lon": 48.81, "start": "2024-01-01", "end": "2024-02-01",
    "duration_h": 48.0, "intentional": True, "distance_km": 10.0,
}


@pytest.mark.asyncio
async def test_dark_vessels_maps_positions_and_dedups(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from backend.routes import maritime
    from backend.app import create_app

    class FakeAdapter:
        enabled = True
        async def find_ais_gaps(self, **kw):
            return [
                GAP, dict(GAP),                                   # exact duplicate → collapses
                {"mmsi": "2", "lat": None, "lon": None, "start": "2024-03-01"},  # no fix → dropped
                {"mmsi": "353698000", "flag": "PA", "lat": 27.0, "lon": 51.0, "start": "2024-05-01"},  # ship, at sea
                # ship station (2-7) reporting an ON-LAND position (Riyadh) — a SUSPECT/
                # spoofed fix → KEPT and flagged on_land=True (surfaced, not hidden)
                {"mmsi": "412330000", "flag": "CN", "lat": 24.7, "lon": 46.7, "start": "2024-04-01"},
                # aid-to-navigation (99x) — NOT a vessel → still dropped entirely
                {"mmsi": "998244414", "flag": None, "lat": 15.5, "lon": 32.5, "start": "2024-06-01"},
            ]

    monkeypatch.setattr(maritime, "_DARK_CACHE", {})
    monkeypatch.setattr("maritime.adapters.gfw.GfwAdapter", lambda *a, **k: FakeAdapter())

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        d = (await c.get("/api/v1/maritime/dark-vessels", params={"region": "gulf"})).json()
        assert d["available"] is True and d["region"] == "gulf"
        assert "not proof" in d["caveat"].lower()
        assert d["count"] == 3                         # dup collapsed, no-fix + AtoN dropped; on-land KEPT
        assert d["suspect"] == 1                        # the one on-land (spoofed) position
        mmsis = [v["mmsi"] for v in d["vessels"]]
        # ship stations only, most-recent first; the on-land ship is kept, the AtoN is not
        assert mmsis == ["353698000", "412330000", "636014423"]
        assert "998244414" not in mmsis               # AtoN (99x) is noise, dropped
        # the on-land ship is flagged, the sea ships are not
        land = next(x for x in d["vessels"] if x["mmsi"] == "412330000")
        assert land["on_land"] is True
        sea = next(x for x in d["vessels"] if x["mmsi"] == "636014423")
        assert sea["on_land"] is False
        assert sea["flag"] == "LBR" and sea["intentional"] is True
        assert sea["lat"] == 28.68 and sea["lon"] == 48.81


@pytest.mark.asyncio
async def test_dark_vessels_disabled_without_token(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from backend.routes import maritime
    from backend.app import create_app

    class Disabled:
        enabled = False

    monkeypatch.setattr(maritime, "_DARK_CACHE", {})
    monkeypatch.setattr("maritime.adapters.gfw.GfwAdapter", lambda *a, **k: Disabled())

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        d = (await c.get("/api/v1/maritime/dark-vessels")).json()
        assert d["available"] is False and d["reason"]
        assert d["vessels"] == []


@pytest.mark.asyncio
async def test_dark_vessels_bad_region_rejected(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/maritime/dark-vessels", params={"region": "atlantic"})
        assert r.status_code == 422
