from __future__ import annotations

import math

from mtscraper.geo import bounds_from_center, haversine_km


def test_haversine_zero() -> None:
    assert haversine_km(33.2, 35.2, 33.2, 35.2) == 0.0


def test_haversine_known_distance() -> None:
    # ~1 degree of latitude is ~111 km.
    d = haversine_km(33.0, 35.0, 34.0, 35.0)
    assert math.isclose(d, 111.19, rel_tol=0.02)


def test_bounds_from_center_contains_center() -> None:
    b = bounds_from_center(33.2, 35.2, half_height_km=100, half_width_km=100)
    assert b.contains(33.2, 35.2)
    assert b.north > 33.2 > b.south
    assert b.east > 35.2 > b.west


def test_bounds_longitude_wider_than_latitude_span() -> None:
    # At lat 33, a km of longitude spans more degrees than a km of latitude.
    b = bounds_from_center(33.2, 35.2, half_height_km=100, half_width_km=100)
    assert (b.east - b.west) > (b.north - b.south)
