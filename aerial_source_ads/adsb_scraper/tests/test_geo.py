"""Unit tests for the pure geographic service."""

from __future__ import annotations

import pytest

from adsb_scraper.exceptions import ConfigError
from adsb_scraper.geo import GeoService
from adsb_scraper.models import GeoBounds

geo = GeoService()


def test_haversine_known_distance() -> None:
    # Helsinki -> London Heathrow, ~1840 km (±15 km tolerance).
    dist = geo.haversine_km(60.17, 24.94, 51.47, -0.45)
    assert 1825 <= dist <= 1855


def test_haversine_jfk_to_lax() -> None:
    # New York JFK -> Los Angeles LAX, ~3970-3985 km reference great-circle.
    dist = geo.haversine_km(40.6413, -73.7781, 33.9416, -118.4085)
    assert 3960 <= dist <= 3990


def test_haversine_symmetric() -> None:
    a = geo.haversine_km(24.7136, 46.6753, 25.6, 47.0)
    b = geo.haversine_km(25.6, 47.0, 24.7136, 46.6753)
    assert a == pytest.approx(b)


def test_haversine_zero() -> None:
    assert geo.haversine_km(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0)


def test_bounds_from_radius_contains_center() -> None:
    bounds = geo.bounds_from_radius(60.17, 24.94, 300.0)
    lat, lon = bounds.center
    assert bounds.contains(lat, lon)
    assert bounds.north > bounds.south
    assert bounds.east > bounds.west


def test_bounds_from_radius_clamps_at_pole() -> None:
    bounds = geo.bounds_from_radius(89.9, 0.0, 500.0)
    assert bounds.north <= 90.0
    assert bounds.west == -180.0 and bounds.east == 180.0


def test_bounds_from_radius_rejects_nonpositive() -> None:
    with pytest.raises(ValueError):
        geo.bounds_from_radius(0.0, 0.0, 0.0)


def test_split_tiles_cover_area_without_gaps() -> None:
    bounds = GeoBounds(north=10.0, south=0.0, east=10.0, west=0.0)
    cells = geo.split(bounds, 2, 5)
    assert len(cells) == 10
    total = sum((c.north - c.south) * (c.east - c.west) for c in cells)
    assert total == pytest.approx((10.0) * (10.0))


def test_split_rejects_invalid_dims() -> None:
    with pytest.raises(ValueError):
        geo.split(GeoBounds(1, 0, 1, 0), 0, 1)


def test_contains_is_total_over_floats() -> None:
    bounds = GeoBounds(north=1.0, south=-1.0, east=1.0, west=-1.0)
    assert geo.contains(bounds, 0.0, 0.0)
    assert not geo.contains(bounds, 2.0, 0.0)
    assert geo.contains(bounds, 1.0, 1.0)  # inclusive edge


def test_estimate_zoom_monotonic() -> None:
    wide = geo.estimate_zoom(GeoBounds(north=80, south=-80, east=170, west=-170))
    narrow = geo.estimate_zoom(GeoBounds(north=0.1, south=-0.1, east=0.1, west=-0.1))
    assert wide < narrow


def test_geobounds_validation() -> None:
    with pytest.raises(ConfigError):
        GeoBounds(north=0.0, south=10.0, east=1.0, west=0.0)  # south > north
    with pytest.raises(ConfigError):
        GeoBounds(north=100.0, south=0.0, east=1.0, west=0.0)  # out of range
