"""
GET /api/v1/maritime?source= — the additive source selector (migration 032).

In-process (ASGI transport) against the live DB — skips cleanly without
DATABASE_URL. The two live-fleet files are redirected to temp files so the test
is hermetic; it proves the default stays aisstream, that marinetraffic serves
ship_id-keyed (mmsi-less) vessels WITHOUT a validation error, and that 'all'
merges both.
"""

from __future__ import annotations

import json
import os

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

AIS_STATE = {"mmsi": "636092297", "lat": 40.7, "lon": 28.2, "speed": 0.8,
             "source": "aisstream", "data_origin": "terrestrial", "name": "MIAMI LIGHT"}
MT_STATE = {"ship_id": "2786", "mmsi": None, "lat": 31.87, "lon": 34.57, "speed": 9.3,
            "source": "marinetraffic", "data_origin": "terrestrial",
            "name": "VALENCE", "ship_type": "cargo", "flag": "MT"}


def _write_fleet(path, states):
    path.write_text(json.dumps({"snapshot_time": 1783801411, "count": len(states),
                                "states": states}), encoding="utf-8")


@requires_db
@pytest.mark.asyncio
async def test_maritime_source_selector(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from backend import api
    from backend.app import create_app
    from backend.db import Database

    ais = tmp_path / "ais.json"
    mt = tmp_path / "mt.json"
    _write_fleet(ais, [AIS_STATE])
    _write_fleet(mt, [MT_STATE])
    monkeypatch.setitem(api._MARITIME_SOURCES, "aisstream", (lambda: ais, "vessel_states"))
    monkeypatch.setitem(api._MARITIME_SOURCES, "marinetraffic", (lambda: mt, "mt_vessel_states"))

    app = create_app()
    app.state.db = Database(DSN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # default = aisstream (frozen behavior): only the mmsi vessel
            r = await c.get("/api/v1/maritime")
            assert r.status_code == 200
            d = r.json()
            assert d["available"] is True and d["count"] == 1
            assert d["states"][0]["mmsi"] == "636092297"
            assert d["states"][0]["source"] == "aisstream"

            # marinetraffic: a ship_id-keyed, mmsi-less vessel must validate & return
            rm = await c.get("/api/v1/maritime", params={"source": "marinetraffic"})
            assert rm.status_code == 200
            dm = rm.json()
            assert dm["available"] is True and dm["count"] == 1
            v = dm["states"][0]
            assert v["ship_id"] == "2786" and v["mmsi"] is None
            assert v["source"] == "marinetraffic" and v["flag"] == "MT"

            # all: both feeds merged, each carrying its own source
            ra = await c.get("/api/v1/maritime", params={"source": "all"})
            da = ra.json()
            assert da["count"] == 2
            assert {s["source"] for s in da["states"]} == {"aisstream", "marinetraffic"}

            # unknown source rejected by the pattern (additive, closed set)
            rb = await c.get("/api/v1/maritime", params={"source": "bogus"})
            assert rb.status_code == 422
    finally:
        await app.state.db.close()


@requires_db
@pytest.mark.asyncio
async def test_maritime_per_source_vintage(tmp_path, monkeypatch):
    """MT-FRESH: a blended source=all must not over-claim freshness. `sources[]` carries
    per-source last_fix/age/rows/stale, and `data_vintage` is the OLDEST contributing
    source's vintage (never the freshest); single-source is unchanged; each row carries
    its own `source`."""
    from httpx import ASGITransport, AsyncClient

    from backend import api
    from backend.app import create_app
    from backend.db import Database

    ais = tmp_path / "ais.json"
    mt = tmp_path / "mt.json"
    _write_fleet(ais, [AIS_STATE])
    _write_fleet(mt, [MT_STATE])
    monkeypatch.setitem(api._MARITIME_SOURCES, "aisstream", (lambda: ais, "vessel_states"))
    monkeypatch.setitem(api._MARITIME_SOURCES, "marinetraffic", (lambda: mt, "mt_vessel_states"))

    app = create_app()
    app.state.db = Database(DSN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # single-source: one sources[] entry; data_vintage == that source's last_fix (unchanged)
            d1 = (await c.get("/api/v1/maritime")).json()
            assert [s["source"] for s in d1["sources"]] == ["aisstream"]
            assert {"source", "last_fix", "age_seconds", "rows", "stale"} <= set(d1["sources"][0])
            assert d1["data_vintage"] == d1["sources"][0]["last_fix"]

            # blend: both sources reported; data_vintage is the OLDEST contributing last_fix
            da = (await c.get("/api/v1/maritime", params={"source": "all"})).json()
            by = {s["source"]: s for s in da["sources"]}
            assert set(by) == {"aisstream", "marinetraffic"}
            contributing = [s["last_fix"] for s in da["sources"] if s["rows"] > 0 and s["last_fix"]]
            if contributing:
                assert da["data_vintage"] == min(contributing)   # oldest, NOT max/freshest — the fix
            # ⛔ THIS IS THE 6↔7 OSCILLATION, AND IT WAS NEVER TEST ISOLATION. §11.3 filed
            # the wandering failure count as cross-file state sharing (`app.state.db`). It is
            # a RACE AGAINST A LIVE CLOCK, in one assertion: this read
            #     assert by["marinetraffic"]["stale"] is True
            # under the premise "the un-serviced MT store". MT IS serviced now — measured
            # 2026-08-24, `max(ts)` in `mt_vessel_states` was 205 s old against 11 s for
            # `vessel_states` — and `stale` is `age > MARITIME_STALE_S` with the threshold at
            # 180 s (api.py:1041, 1141). A store refreshed on a timer crosses 180 s over and
            # over, so the flag flips between runs with nothing in the test changing. Run
            # order only ever changed WHEN the clock was read.
            #
            # ⇒ Assert the CONTRACT the endpoint actually owes: the `stale` it publishes
            # agrees with the `age_seconds` it publishes, for every source. A staleness rule
            # that disagreed with its own age — the real defect here — still fails this,
            # and no wall-clock instant can make it flake.
            from backend.api import MARITIME_STALE_S

            for src, s in by.items():
                if s["age_seconds"] is None:
                    assert s["stale"] is False, f"{src}: no age, so it cannot be called stale"
                    continue
                assert s["stale"] is (s["age_seconds"] > MARITIME_STALE_S), (
                    f"{src}: stale={s['stale']} disagrees with age={s['age_seconds']}s "
                    f"against the {MARITIME_STALE_S}s threshold")
            # ⚠ NOT asserted: that MT is behind AIS. It is structurally expected — AIS is a
            # continuous feed, MT a periodic scrape — but it is not a guarantee the code
            # makes, and pinning an instantaneous ordering between two independently-timed
            # writers is the same race in a second costume.
            # per-row: every returned vessel carries its own source (age derivable from its timestamp)
            assert all("source" in s for s in da["states"])
    finally:
        await app.state.db.close()


@requires_db
@pytest.mark.asyncio
async def test_marinetraffic_db_fallback_when_no_live_file(tmp_path, monkeypatch):
    """MarineTraffic is periodic: with no fresh scrape file, the endpoint must
    fall back to the stored fleet (latest fix per ship_id) so the source stays
    visible — unlike AIS, which goes empty when stale."""
    from httpx import ASGITransport, AsyncClient

    from backend import api
    from backend.app import create_app
    from backend.db import Database

    # point marinetraffic at a NONEXISTENT file → forces the DB fallback path
    monkeypatch.setitem(api._MARITIME_SOURCES, "marinetraffic",
                        (lambda: tmp_path / "absent.json", "mt_vessel_states"))
    app = create_app()
    app.state.db = Database(DSN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            d = (await c.get("/api/v1/maritime", params={"source": "marinetraffic"})).json()
            if d["count"] == 0:
                pytest.skip("no mt_vessel_states rows in this DB")
            assert d["available"] is True
            assert all(s["source"] == "marinetraffic" for s in d["states"])
            assert all(s["ship_id"] for s in d["states"])   # ship_id-keyed
            assert all(s["lat"] is not None and s["lon"] is not None for s in d["states"])
    finally:
        await app.state.db.close()
