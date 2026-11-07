"""MarineTraffic tile arithmetic.

The vessel-data endpoint is addressed by ``z/X/Y`` tiles. MarineTraffic's grid
is the standard Web-Mercator ("slippy") scheme but shifted one zoom level: their
``z:N`` is slippy zoom ``N - TILE_ZOOM_OFFSET``. Verified live — their ``z:7``
centre tile for lon 35.2/lat 33.2 is slippy ``z6 X38/Y25``.

Everything here is pure math with no I/O, so it is trivially testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .constants import TILE_ZOOM_OFFSET
from .exceptions import ConfigError
from .models import GeoBounds


@dataclass(frozen=True, slots=True)
class Tile:
    """A single MarineTraffic data tile in *their* zoom addressing."""

    z: int
    x: int
    y: int


def _slippy_zoom(mt_zoom: int) -> int:
    return mt_zoom - TILE_ZOOM_OFFSET


def _lonlat_to_slippy(lon: float, lat: float, slippy_zoom: int) -> tuple[int, int]:
    """Standard OSM lon/lat -> tile (x, y) at a given slippy zoom."""
    n = 2**slippy_zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return x, y


def tile_for_point(lon: float, lat: float, mt_zoom: int) -> Tile:
    """The MarineTraffic tile containing a point at their zoom ``mt_zoom``."""
    sx, sy = _lonlat_to_slippy(lon, lat, _slippy_zoom(mt_zoom))
    return Tile(z=mt_zoom, x=sx, y=sy)


def tiles_covering(bounds: GeoBounds, mt_zoom: int) -> list[Tile]:
    """Every MarineTraffic tile whose extent overlaps ``bounds``.

    Returned in a stable row-major order so callers (and tests) are deterministic.
    """
    if mt_zoom <= TILE_ZOOM_OFFSET:
        raise ConfigError(f"mt_zoom must be > {TILE_ZOOM_OFFSET}, got {mt_zoom}")
    sz = _slippy_zoom(mt_zoom)
    # Note the Y axis is inverted (north has the smaller tile index).
    x0, y_north = _lonlat_to_slippy(bounds.west, bounds.north, sz)
    x1, y_south = _lonlat_to_slippy(bounds.east, bounds.south, sz)
    xmin, xmax = sorted((x0, x1))
    ymin, ymax = sorted((y_north, y_south))
    return [
        Tile(z=mt_zoom, x=x, y=y)
        for y in range(ymin, ymax + 1)
        for x in range(xmin, xmax + 1)
    ]
