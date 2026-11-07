"""/aircraft/{icao24} and /vessel/{mmsi} through the Enricher injection seam.

create_app(enricher=...) is the DI hook (arch review finding 10); these are the
first tests to substitute it, covering the response mapping and the identifier
guards at wire level, fully offline.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app


class FakePool:
    async def fetchrow(self, sql, *args): return None
    async def execute(self, sql, *args): return None


class FakeDB:
    async def pool(self): return FakePool()


class FakeEnricher:
    async def get_aircraft(self, pool, icao24):
        return {"found": True, "registration": "YK-ASA", "aircraft_type": "A320",
                "operator": "Test Air", "operator_country": "SY",
                "photo_thumb": "http://img/t.jpg", "photo_page": "http://img/p",
                "photographer": "someone"}
    async def get_route(self, pool, callsign):
        return {"found": True, "airline_name": "Test Air",
                "origin_icao": "OSDI", "dest_icao": "OMDB"}
    async def get_vessel_photo(self, pool, mmsi, imo, name):
        return {"photo_thumb": "http://img/v.jpg", "photo_page": "http://c/p",
                "photographer": "ph", "license": "CC-BY-SA-4.0"}
    async def close(self): return None


class NotFoundEnricher(FakeEnricher):
    async def get_aircraft(self, pool, icao24):
        return {"icao24": icao24, "found": False}


def _client(enricher):
    app = create_app(enricher=enricher)
    app.state.db = FakeDB()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_aircraft_maps_photo_and_route(monkeypatch):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    async with _client(FakeEnricher()) as c:
        r = await c.get("/api/v1/aircraft/4009f9", params={"callsign": "RB501"})
    assert r.status_code == 200
    body = r.json()
    assert body["registration"] == "YK-ASA"
    assert body["photo"]["thumb"] == "http://img/t.jpg"
    assert body["photo"]["photographer"] == "someone"     # attribution required
    assert body["route"]["airline"] == "Test Air"
    assert body["route"]["origin"]["icao"] == "OSDI"


@pytest.mark.asyncio
async def test_aircraft_not_found_degrades_to_nulls_not_errors(monkeypatch):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    async with _client(NotFoundEnricher()) as c:
        r = await c.get("/api/v1/aircraft/4009f9")
    assert r.status_code == 200
    body = r.json()
    assert body["registration"] is None and body["photo"] is None and body["route"] is None


@pytest.mark.asyncio
async def test_identifier_guards_fire_at_wire_level(monkeypatch):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    async with _client(FakeEnricher()) as c:
        assert (await c.get("/api/v1/aircraft/zzzz!")).status_code == 422       # non-hex
        assert (await c.get("/api/v1/aircraft/4009f9",
                            params={"callsign": "A/B"})).status_code == 422     # path chars
        assert (await c.get("/api/v1/vessel/12ab45")).status_code == 422        # non-digit


@pytest.mark.asyncio
async def test_vessel_photo_with_attribution(monkeypatch):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    async with _client(FakeEnricher()) as c:
        r = await c.get("/api/v1/vessel/477995018")
    assert r.status_code == 200
    body = r.json()
    assert body["photo"]["thumb"] == "http://img/v.jpg"
    assert body["license"] == "CC-BY-SA-4.0"
