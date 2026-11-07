"""ZONES-1 — offshore-infrastructure zone gazetteer + loader (asset only).

Pure, no DB (mirrors test_airport_search.test_gazetteer_loads_and_resolves): the
committed asset is loaded in-process and its invariants + the loader's spatial
queries are asserted against known zones. No detector, no zone_dwell events, no
composite — those are ZONES-2/3.
"""

import json

import pytest

from backend import zones
from backend.zones import ZoneAssetError, _load

_REQUIRED = {"zone_id", "zone_type", "lat", "lon", "radius_m", "radius_basis",
             "source", "license", "source_ref", "captured_at"}


def test_asset_loads_and_every_feature_validates():
    """Committed asset — no DB, always runs (like the airports gazetteer test)."""
    rows = zones._zones()
    assert len(rows) == 36                                  # 7 gas_field + 29 platform
    ids = [z["zone_id"] for z in rows]
    assert len(ids) == len(set(ids)), "zone_id must be unique"
    for z in rows:
        assert _REQUIRED <= set(z)                          # required fields present
        assert z["zone_type"] in zones.ZONE_TYPES
        assert z["radius_basis"] in zones.RADIUS_BASES      # radius classified
        assert z["license"]                                 # licence present per feature
        assert z["source_ref"].startswith("http")           # per-feature provenance link
        assert z["radius_m"] > 0
    # both zone types present, with the geometry-honesty split intact:
    # every platform is 'sourced', every gas_field is 'assumed'
    assert {z["zone_type"] for z in rows} == {"gas_field", "platform"}
    assert all(z["radius_basis"] == "sourced" for z in rows if z["zone_type"] == "platform")
    assert all(z["radius_basis"] == "assumed" for z in rows if z["zone_type"] == "gas_field")


def test_manifest_documents_sources_and_deferred_zone_types():
    raw = json.loads(zones.DEFAULT_ASSET.read_text(encoding="utf-8"))
    man = raw["manifest"]
    assert {"GOGET", "OSM"} <= set(man["sources"])
    assert "odbl_obligation" in man["sources"]["OSM"]        # ODbL obligation stated
    # the three deferred zone types are documented decisions, not omissions
    deferred = man["deferred"]
    assert {"gulf_iran_theater", "submarine_cables", "listed_ports"} <= set(deferred)
    # Gulf deferral framed as intelligence SCOPE, not a coverage gap
    assert "scope" in deferred["gulf_iran_theater"]["reason"].lower()
    # cable buffer spec is recorded ready; Egypt-platforms clarification present
    assert deferred["submarine_cables"]["buffer_spec_ready"]["half_width_m"] == 1852
    assert man["egypt_clarification"]


def test_resolution_by_id_and_name():
    assert any(z["zone_id"] == "gasfield-leviathan" for z in zones.resolve("leviathan"))  # name
    assert any(z["zone_id"] == "gasfield-karish" for z in zones.resolve("gasfield-karish"))  # id
    assert zones.resolve("zznope") == []
    assert zones.resolve("") == []


def test_detectability_flag_honoured():
    # the mis-sited "Tamar Gas Platform" node (OSM fixme, ~100 km off) is kept as a
    # faithful record but marked undetectable with a reason
    tamar = next(z for z in zones._zones() if z["zone_id"] == "platform-osm-way-215263078")
    assert tamar["detectable"] is False and tamar["detectable_reason"]
    assert zones.is_detectable(tamar) is False
    # all_zones keeps it (full record); detectable_zones drops it
    assert len(zones.all_zones()) == 36
    assert len(zones.detectable_zones()) == 35
    assert all(zones.is_detectable(z) for z in zones.detectable_zones())
    # spatial queries exclude it by default, include it on request
    at = (tamar["lat"], tamar["lon"])
    assert zones.zones_containing(*at) == []
    got = zones.zones_containing(*at, include_undetectable=True)
    assert any(z["zone_id"] == tamar["zone_id"] for z in got)


def test_cached_singleton():
    assert zones._zones() is zones._zones()                 # lru_cache identity


def test_spatial_queries_at_known_coords():
    # a field centroid falls inside its own 5 km circle (distance 0)
    c = zones.zones_containing(33.2283, 34.289)             # Karish centre
    assert any(z["zone_id"] == "gasfield-karish" for z in c)
    assert c[0]["distance_m"] == 0.0
    # a fix ~3.3 km from the Karish centre is inside the 5 km field (proximity search)
    near = zones.zones_near(33.20, 34.28, radius_km=10)
    assert any(z["zone_id"] == "gasfield-karish" for z in near)
    assert all("distance_km" in z for z in near)
    # a bbox over the Egyptian Nile Delta returns platform zones
    bb = zones.zones_in_bbox(31.4, 31.0, 32.2, 32.9)
    assert bb and all(z["zone_type"] == "platform" for z in bb)
    # open sea NW of the theater sits inside no zone (not an error)
    assert zones.zones_containing(35.5, 30.0) == []
    # the platform 500 m circle is tight: a point 2 km from a platform is NOT inside it,
    # while the same point can still be inside a 5 km field — the sourced/assumed contrast
    plat = next(z for z in zones._zones() if z["zone_id"] == "platform-osm-node-8553898152")
    assert zones.zones_containing(plat["lat"], plat["lon"])[0]["zone_id"] == plat["zone_id"]


def test_fail_fast_on_malformed_asset(tmp_path):
    feat = {"zone_id": "x", "zone_type": "platform", "lat": 33.0, "lon": 34.0,
            "radius_m": 500, "radius_basis": "sourced", "source": "s",
            "license": "L", "source_ref": "http://x", "captured_at": "2026-07-26"}
    with pytest.raises(ZoneAssetError):                     # missing file
        _load(tmp_path / "nope.json")
    with pytest.raises(ZoneAssetError):                     # empty zones list
        (tmp_path / "a.json").write_text(json.dumps({"zones": []}))
        _load(tmp_path / "a.json")
    with pytest.raises(ZoneAssetError):                     # missing required field
        (tmp_path / "b.json").write_text(json.dumps({"zones": [{"zone_id": "x", "zone_type": "platform"}]}))
        _load(tmp_path / "b.json")
    with pytest.raises(ZoneAssetError):                     # bad zone_type
        (tmp_path / "c.json").write_text(json.dumps({"zones": [dict(feat, zone_type="WRONG")]}))
        _load(tmp_path / "c.json")
    with pytest.raises(ZoneAssetError):                     # bad radius_basis
        (tmp_path / "d.json").write_text(json.dumps({"zones": [dict(feat, radius_basis="guessed")]}))
        _load(tmp_path / "d.json")
    with pytest.raises(ZoneAssetError):                     # duplicate zone_id
        (tmp_path / "e.json").write_text(json.dumps({"zones": [dict(feat), dict(feat)]}))
        _load(tmp_path / "e.json")
    with pytest.raises(ZoneAssetError):                     # detectable=false without a reason
        (tmp_path / "f.json").write_text(json.dumps({"zones": [dict(feat, detectable=False)]}))
        _load(tmp_path / "f.json")
