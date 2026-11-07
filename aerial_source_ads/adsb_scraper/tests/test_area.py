"""Unit tests for the multi-country area-of-interest model (border buffer)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adsb_scraper.area import AreaState, load_area_of_interest
from adsb_scraper.constants import AOI_COUNTRIES
from adsb_scraper.exceptions import ConfigError

from .conftest import make_area_of_interest

AOI = make_area_of_interest(buffer_km=50.0)

# A representative interior point per country (lat, lon).
CAPITALS = {
    "Syria": (33.51, 36.29),  # Damascus
    "Turkey": (39.93, 32.86),  # Ankara
    "Iraq": (33.31, 44.36),  # Baghdad
    "Jordan": (31.95, 35.93),  # Amman
    "Lebanon": (33.89, 35.50),  # Beirut
    "Israel": (32.08, 34.78),  # Tel Aviv
    "Cyprus": (35.17, 33.36),  # Nicosia
}

# Far outside the buffer (hundreds of km from any border).
FAR = {
    "Cairo": (30.04, 31.24),
    "Tehran": (35.69, 51.39),
    "Athens": (37.98, 23.73),
    "central Saudi": (24.71, 46.67),
}


@pytest.mark.parametrize(("country", "point"), list(CAPITALS.items()))
def test_inside_a_country(country: str, point: tuple[float, float]) -> None:
    lat, lon = point
    result = AOI.classify(lat, lon)
    assert result.state is AreaState.INSIDE
    assert result.country == country
    assert result.near is None


@pytest.mark.parametrize(("label", "point"), list(FAR.items()))
def test_far_points_are_outside(label: str, point: tuple[float, float]) -> None:
    lat, lon = point
    result = AOI.classify(lat, lon)
    assert result.state is AreaState.OUTSIDE
    assert result.country is None and result.near is None


def test_near_points_name_the_nearest_country() -> None:
    # Points just off a border, within 50 km, with the expected nearest country.
    cases = [
        ((33.90, 35.10), "Lebanon"),  # ~34 km west of the Lebanese coast (sea)
        ((34.30, 33.30), "Cyprus"),  # ~39 km south of Cyprus (sea)
        ((33.30, 46.20), "Iraq"),  # just east of the Iraq border (into Iran)
        ((32.00, 34.20), "Israel"),  # ~49 km west of the Israeli coast (sea)
    ]
    for (lat, lon), expected in cases:
        result = AOI.classify(lat, lon)
        assert result.state is AreaState.NEAR, (lat, lon, result)
        assert result.near == expected
        assert result.country is None


def test_cyprus_island_inside() -> None:
    """Guards against dropping the island polygon (disconnected from mainland)."""
    for lat, lon in [(35.17, 33.36), (34.71, 33.02), (34.77, 32.42), (35.30, 33.90)]:
        assert AOI.classify(lat, lon).state is AreaState.INSIDE
        assert AOI.classify(lat, lon).country == "Cyprus"


def test_all_seven_countries_present() -> None:
    assert set(AOI.country_names) == set(AOI_COUNTRIES)


# --------------------------------------------------------------------------- #
# Buffer distance is a REAL kilometric distance (not degree-based)
# --------------------------------------------------------------------------- #


def test_buffer_boundary_is_kilometric() -> None:
    """A point just inside buffer_km of a border is NEAR; just past it, OUTSIDE.

    Uses a 50 km buffer and a due-south transect from the Cyprus south coast
    (~34.56N, 33.0E). 1 deg latitude ~= 111 km, so ~0.40 deg south (~45 km) is
    NEAR and ~0.55 deg south (~61 km) is OUTSIDE — proving real distance, since a
    degree-based buffer of "50" would misbehave wildly at this latitude.
    """
    aoi = make_area_of_interest(buffer_km=50.0)
    inside_buffer = aoi.classify(34.56 - 0.40, 33.0)  # ~45 km off the coast
    outside_buffer = aoi.classify(34.56 - 0.55, 33.0)  # ~61 km off the coast
    assert inside_buffer.state is AreaState.NEAR and inside_buffer.near == "Cyprus"
    assert outside_buffer.state is AreaState.OUTSIDE


def test_buffer_width_scales_with_config() -> None:
    """A point ~90 km off a border is OUTSIDE at 50 km buffer, NEAR at 150 km."""
    lat, lon = 34.56 - 0.81, 33.0  # ~90 km south of Cyprus
    assert make_area_of_interest(buffer_km=50.0).classify(lat, lon).state is AreaState.OUTSIDE
    wide = make_area_of_interest(buffer_km=150.0).classify(lat, lon)
    assert wide.state is AreaState.NEAR and wide.near == "Cyprus"


def test_near_country_is_the_truly_nearest() -> None:
    """For every NEAR point, ``near`` must equal the geometrically nearest of the
    seven countries — verified independently against the projected distances."""
    from shapely.geometry import Point

    for lat, lon in [(33.90, 35.10), (34.30, 33.30), (33.30, 46.20), (32.00, 34.20)]:
        result = AOI.classify(lat, lon)
        assert result.state is AreaState.NEAR
        projected = Point(*AOI._to_metres(lon, lat))
        truly_nearest = min(AOI._countries, key=lambda c: c.geom_m.distance(projected)).name
        assert result.near == truly_nearest


def test_bounding_box_encloses_buffered_geometry() -> None:
    box = AOI.bounding_box()
    # Capitals sit inside the fetch envelope.
    for lat, lon in CAPITALS.values():
        assert box.south <= lat <= box.north and box.west <= lon <= box.east
    # The buffered envelope extends ~50 km beyond the raw union extent
    # (union was W25.66 E48.56 S29.06 N42.10).
    assert box.west < 25.66 and box.east > 48.56
    assert box.south < 29.06 and box.north > 42.10


# --------------------------------------------------------------------------- #
# Fail-fast loading
# --------------------------------------------------------------------------- #


def test_missing_asset_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_area_of_interest(tmp_path / "nope.geojson", buffer_km=50.0)


def test_negative_buffer_fails_fast() -> None:
    with pytest.raises(ConfigError, match="buffer_km"):
        make_area_of_interest(buffer_km=-1.0)


def test_malformed_asset_fails_fast(tmp_path: Path) -> None:
    bad = tmp_path / "bad.geojson"
    bad.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="unreadable"):
        load_area_of_interest(bad, buffer_km=50.0)


def test_missing_country_fails_fast(tmp_path: Path) -> None:
    from adsb_scraper.config import AreaConfig

    source = json.loads(AreaConfig().asset_path.read_text(encoding="utf-8"))
    source["features"] = [f for f in source["features"] if f["properties"]["name"] != "Cyprus"]
    trimmed = tmp_path / "no_cyprus.geojson"
    trimmed.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ConfigError, match="missing required countries"):
        load_area_of_interest(trimmed, buffer_km=50.0)
