"""Geographic primitives: great-circle distance and area membership.

Pure functions over ``(lat, lon)`` — no dependency on any vessel/aircraft
notion, so the same code serves any point source.
"""

from __future__ import annotations

import math

from .constants import EARTH_RADIUS_KM
from .models import GeoBounds


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bounds_from_center(
    lat: float, lon: float, half_height_km: float, half_width_km: float
) -> GeoBounds:
    """A lat/lon rectangle centred on a point, sized in kilometres.

    A degree of latitude is ~111 km everywhere; a degree of longitude shrinks by
    ``cos(lat)``. Values are clamped to valid ranges by ``GeoBounds`` itself.
    """
    dlat = half_height_km / 111.0
    dlon = half_width_km / (111.0 * max(0.01, math.cos(math.radians(lat))))
    return GeoBounds(
        north=min(90.0, lat + dlat),
        south=max(-90.0, lat - dlat),
        east=min(180.0, lon + dlon),
        west=max(-180.0, lon - dlon),
    )
