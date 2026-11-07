"""Pure geographic calculations.

Every function here is stateless and side-effect free, which makes the module
trivially unit-testable (see ``tests/test_geo.py``) and safe to call from any
thread or coroutine. This is where the original crash lived: the old code
passed ``ac.get("lat")`` — possibly ``None`` — straight into a ``<=`` chain.
Here, coordinates are validated numerics by the time they reach us, and
:meth:`GeoService.contains` is total over its typed inputs.
"""

from __future__ import annotations

import math

from .constants import EARTH_RADIUS_KM, MAX_LAT, MAX_LON, MIN_LAT, MIN_LON
from .models import GeoBounds


class GeoService:
    """A namespace of pure geographic operations.

    Implemented as a class (rather than loose functions) so it can be injected
    and, if ever needed, subclassed for an alternative projection without
    touching call sites.
    """

    __slots__ = ()

    @staticmethod
    def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Great-circle distance between two points, in kilometres."""
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))

    @staticmethod
    def bounds_from_radius(lat: float, lon: float, radius_km: float) -> GeoBounds:
        """Axis-aligned bounding box enclosing a circle of ``radius_km``.

        The longitude delta is scaled by ``1/cos(lat)`` so the box stays roughly
        circular away from the equator. Near the poles ``cos(lat)`` collapses,
        so the longitude span is clamped to the full ``[-180, 180]`` range.
        """
        if radius_km <= 0:
            raise ValueError("radius_km must be > 0")
        lat_delta = math.degrees(radius_km / EARTH_RADIUS_KM)
        cos_lat = math.cos(math.radians(lat))
        if cos_lat <= 1e-6:
            lon_delta = 360.0  # degenerate near the poles → whole longitude band
        else:
            lon_delta = math.degrees(radius_km / (EARTH_RADIUS_KM * cos_lat))
        return GeoBounds(
            north=min(MAX_LAT, lat + lat_delta),
            south=max(MIN_LAT, lat - lat_delta),
            east=min(MAX_LON, lon + lon_delta),
            west=max(MIN_LON, lon - lon_delta),
        )

    @staticmethod
    def split(bounds: GeoBounds, rows: int, cols: int) -> list[GeoBounds]:
        """Tile ``bounds`` into a ``rows`` x ``cols`` grid of sub-rectangles."""
        if rows < 1 or cols < 1:
            raise ValueError("rows and cols must be >= 1")
        lat_step = (bounds.north - bounds.south) / rows
        lon_step = (bounds.east - bounds.west) / cols
        cells: list[GeoBounds] = []
        for r in range(rows):
            north = bounds.north - r * lat_step
            south = bounds.north - (r + 1) * lat_step
            for c in range(cols):
                west = bounds.west + c * lon_step
                east = bounds.west + (c + 1) * lon_step
                cells.append(GeoBounds(north=north, south=south, east=east, west=west))
        return cells

    @staticmethod
    def contains(bounds: GeoBounds, lat: float, lon: float) -> bool:
        """Whether a point lies within (inclusive) the bounds."""
        return bounds.contains(lat, lon)

    @staticmethod
    def estimate_zoom(bounds: GeoBounds) -> int:
        """Heuristic web-map zoom level for the widest bounds dimension."""
        max_diff = max(bounds.north - bounds.south, bounds.east - bounds.west)
        thresholds = (
            (90, 2),
            (45, 3),
            (20, 4),
            (10, 5),
            (5, 6),
            (2, 7),
            (1, 8),
            (0.5, 9),
        )
        for limit, zoom in thresholds:
            if max_diff > limit:
                return zoom
        return 10
