from __future__ import annotations

from pathlib import Path

import pytest

from mtscraper.config import build_config
from mtscraper.exceptions import ConfigError
from mtscraper.models import GeoBounds, Vessel, VesselRecord


def test_geobounds_rejects_inverted_latitude() -> None:
    with pytest.raises(ConfigError):
        GeoBounds(north=10.0, south=20.0, east=1.0, west=0.0)


def test_geobounds_center() -> None:
    b = GeoBounds(north=34.0, south=32.0, east=36.0, west=34.0)
    assert b.center == (33.0, 35.0)


def test_vessel_to_dict_omits_raw() -> None:
    v = Vessel(ship_id="1", latitude=33.2, longitude=35.2, raw={"SHIP_ID": "1"})
    d = v.to_dict()
    assert d["ship_id"] == "1"
    assert "raw" not in d


def test_vessel_record_serialization_includes_distance() -> None:
    rec = VesselRecord(
        ship_id="1",
        fetched_at="2026-07-21T00:00:00+00:00",
        source="interception",
        data={"ship_id": "1"},
        distance_km=2.0,
        in_area=True,
    )
    d = rec.to_dict()
    assert d["distance_km"] == 2.0
    assert d["in_area"] is True
    assert d["data"]["ship_id"] == "1"


def test_build_config_map_url_uses_lon_then_lat() -> None:
    cfg = build_config(
        center_lat=33.2,
        center_lon=35.2,
        zoom=7,
        output_dir=Path("out"),
        half_height_km=100,
        half_width_km=100,
        max_cycles=1,
    )
    # MarineTraffic URL: centerx == longitude, centery == latitude.
    assert "centerx:35.2" in cfg.map_url
    assert "centery:33.2" in cfg.map_url
    assert "zoom:7" in cfg.map_url


def test_build_config_rejects_low_zoom() -> None:
    with pytest.raises(ConfigError):
        build_config(
            center_lat=0.0,
            center_lon=0.0,
            zoom=1,
            output_dir=Path("out"),
            half_height_km=1,
            half_width_km=1,
        )


def _cfg(**kw: object):
    base: dict[str, object] = {
        "center_lat": 33.2,
        "center_lon": 35.2,
        "zoom": 7,
        "output_dir": Path("out"),
        "half_height_km": 100,
        "half_width_km": 100,
    }
    base.update(kw)
    return build_config(**base)  # type: ignore[arg-type]


def test_passive_mode_config() -> None:
    cfg = _cfg(collection_mode="passive")
    assert cfg.collection_mode == "passive"


def test_rejects_bad_collection_mode() -> None:
    with pytest.raises(ConfigError):
        _cfg(collection_mode="sideways")


def test_rejects_zero_profile_count() -> None:
    with pytest.raises(ConfigError):
        _cfg(profile_count=0)


def test_enrich_defaults_off() -> None:
    cfg = _cfg()
    assert cfg.enrich_enabled is False
    assert cfg.collection_mode == "active"
    assert cfg.profile_count == 1
