"""
Integration: API Contract v1 shape checks against the RUNNING stack.
Skips cleanly (module-level) when the server isn't reachable — unit tests
stay green without docker/systemd.
"""

import pytest

httpx = pytest.importorskip("httpx")

BASE = "http://127.0.0.1:8000/api/v1"


def _alive() -> bool:
    try:
        return httpx.get(f"{BASE}/health", timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _alive(), reason="stack not running")


def get(path: str, **params):
    r = httpx.get(f"{BASE}{path}", params=params, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


def test_live_shape():
    d = get("/live")
    assert set(d) >= {"snapshot_time", "ts", "bbox", "count", "source", "states"}
    assert d["source"] in ("db", "archive")
    if d["states"]:
        sv = d["states"][0]
        assert set(sv) >= {"icao24", "callsign", "on_ground", "snapshot_time",
                           "baro_altitude", "velocity", "true_track", "squawk"}


def test_history_shape_and_order():
    live = get("/live")
    if not live["states"]:
        pytest.skip("no live states")
    icao = live["states"][0]["icao24"]
    d = get("/history", icao24=icao)
    assert d["icao24"] == icao and isinstance(d["has_more"], bool)
    times = [p["snapshot_time"] for p in d["points"]]
    assert times == sorted(times)


def test_snapshots_and_replay_roundtrip():
    snaps = get("/snapshots")
    if not snaps["snapshots"]:
        pytest.skip("no snapshots")
    st = snaps["snapshots"][-1]["snapshot_time"]
    d = get("/replay", at=st)
    # contract: resolves to the nearest COMPLETE snapshot at/before `at`
    assert d["requested_at"] == st
    assert d["snapshot_time"] <= st


def test_stats_from_aggregate():
    d = get("/stats", bucket="1h")
    assert d["bucket"] == "1h"
    for b in d["buckets"]:
        assert b["airborne_count"] + b["ground_count"] == b["state_count"]


def test_range_cap_422():
    r = httpx.get(f"{BASE}/history", params={"icao24": "4b1617",
                  "from": 0, "to": 10**9}, timeout=15)
    assert r.status_code == 422


def test_replay_before_archive_404():
    r = httpx.get(f"{BASE}/replay", params={"at": 1000}, timeout=15)
    assert r.status_code == 404


def test_aircraft_enrichment_shape():
    # NB: even tiny hexes can be real airframes (000001 is a Colombian AF
    # C-295M!) — so assert the nullable CONTRACT shape, not specific nulls.
    d = get("/aircraft/000001")
    assert d["icao24"] == "000001"
    assert set(d) == {"icao24", "registration", "aircraft_type", "operator",
                      "operator_country", "photo", "route"}
    if d["photo"] is not None:
        assert "thumb" in d["photo"]
    if d["route"] is not None:
        assert {"origin", "destination"} <= set(d["route"])


def test_health_honest():
    d = get("/health")
    assert set(d) == {"ok", "db", "archive", "ingestion"}
    assert isinstance(d["db"]["connected"], bool)


def test_military_overlay_shape():
    d = get("/military")
    assert set(d) >= {"available", "count", "states"}
    if d["states"]:
        s = d["states"][0]
        assert s["source"] == "airplanes_live"
        assert {"actype", "acdesc", "reg"} <= set(s)


def test_source_filter_history():
    # a military hex, if any, has a trail under source=airplanes_live and
    # (almost always) nothing under the default opensky source
    mil = get("/military")
    if not mil["states"]:
        pytest.skip("no military traffic right now")
    hexid = mil["states"][0]["icao24"]
    d = get("/history", icao24=hexid, source="airplanes_live")
    assert d["count"] >= 1


def test_stats_source_default_is_opensky():
    # frozen contract: /stats without source returns OpenSky rollups only
    a = get("/stats", bucket="1h")
    b = get("/stats", bucket="1h", source="opensky")
    assert a["buckets"] == b["buckets"]
