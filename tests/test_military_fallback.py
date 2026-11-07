"""/military archive-first + DB-fallback ladder (arch review finding 11).

The one overlay that had NO fallback now mirrors /live's model. Detonation
of every rung: fresh file serves without touching the DB; a stale file falls
through to a fresh DB snapshot (with the airframe extras); stale+stale keeps
the OLD contract (file verdict, honestly aged); DB trouble never becomes a
500 — "never an error" is this endpoint's documented contract.
"""

from __future__ import annotations

import json
import os
import time

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from backend.routes import air

def _state(**over):
    base = {"icao24": "ae01ce", "callsign": "RCH4136", "origin_country": "United States",
            "longitude": 35.5, "latitude": 33.9, "on_ground": False,
            "snapshot_time": int(time.time()), "source": "airplanes_live",
            "actype": "C17", "acdesc": "Boeing C-17A", "reg": "07-7172"}
    base.update(over)
    return base


class FakeSettings:
    def __init__(self, output_dir):
        self.output_dir = output_dir


class FakeDB:
    def __init__(self, latest=None, rows=(), raises=False):
        self.latest, self.rows, self.raises = latest, rows, raises
        self.touched = False

    async def latest_source_snapshot(self, source):
        self.touched = True
        if self.raises:
            raise ConnectionError("db down")
        assert source == "airplanes_live"
        return self.latest

    async def military_states_at(self, ts):
        return list(self.rows)


def _write(tmp, age_s=0.0, states=None):
    states = [_state()] if states is None else states
    p = tmp / "military.json"
    p.write_text(json.dumps({"snapshot_time": int(time.time() - age_s),
                             "count": len(states), "states": states}))
    if age_s:
        os.utime(p, (time.time() - age_s, time.time() - age_s))
    return p


def _client(monkeypatch, tmp, db):
    monkeypatch.setattr(air, "settings", FakeSettings(tmp))
    app = create_app()
    app.state.db = db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_fresh_file_serves_and_db_is_never_touched(monkeypatch, tmp_path):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    db = FakeDB(raises=True)                       # would explode if touched
    _write(tmp_path)
    async with _client(monkeypatch, tmp_path, db) as c:
        r = (await c.get("/api/v1/military")).json()
    assert r["available"] is True and r["source"] == "archive"
    assert db.touched is False


@pytest.mark.asyncio
async def test_stale_file_falls_through_to_fresh_db_with_extras(monkeypatch, tmp_path):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    _write(tmp_path, age_s=3600)                   # stale archive
    row = _state(snapshot_time=int(time.time() - 30), fetched_at=None)
    db = FakeDB(latest=int(time.time() - 30), rows=[row])
    async with _client(monkeypatch, tmp_path, db) as c:
        r = (await c.get("/api/v1/military")).json()
    assert r["available"] is True and r["source"] == "db" and r["count"] == 1
    assert r["states"][0]["actype"] == "C17" and r["states"][0]["reg"] == "07-7172"


@pytest.mark.asyncio
async def test_stale_file_and_stale_db_keeps_the_old_contract(monkeypatch, tmp_path):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    _write(tmp_path, age_s=3600)
    db = FakeDB(latest=int(time.time() - 7200))    # DB even older
    async with _client(monkeypatch, tmp_path, db) as c:
        r = (await c.get("/api/v1/military")).json()
    assert r["available"] is False and r["source"] == "archive"
    assert r["age_seconds"] and r["age_seconds"] > 3000


@pytest.mark.asyncio
async def test_no_file_and_fresh_db_serves_from_db(monkeypatch, tmp_path):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    db = FakeDB(latest=int(time.time() - 10), rows=[])
    async with _client(monkeypatch, tmp_path, db) as c:
        r = (await c.get("/api/v1/military")).json()
    assert r["available"] is True and r["source"] == "db" and r["count"] == 0


@pytest.mark.asyncio
async def test_db_trouble_is_never_a_500(monkeypatch, tmp_path):
    monkeypatch.delenv("ATHEER_API_KEY", raising=False)
    db = FakeDB(raises=True)                       # no file, DB down
    async with _client(monkeypatch, tmp_path, db) as c:
        resp = await c.get("/api/v1/military")
    assert resp.status_code == 200
    assert resp.json()["available"] is False
