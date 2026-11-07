"""Tile arithmetic — pinned against a live MarineTraffic capture.

The live map, opened at ``centerx:35.2/centery:33.2/zoom:7``, fetched the tile
grid X in {36..39}, Y in {24..26}. The centre tile is therefore X38/Y25, which
these tests lock in.
"""

from __future__ import annotations

import pytest

from mtscraper.exceptions import ConfigError
from mtscraper.models import GeoBounds
from mtscraper.tiles import Tile, tile_for_point, tiles_covering


def test_center_tile_matches_live_capture() -> None:
    tile = tile_for_point(lon=35.2, lat=33.2, mt_zoom=7)
    assert tile == Tile(z=7, x=38, y=25)


def test_zoom_offset_is_slippy_minus_one() -> None:
    # Their z:7 == slippy z6; halving the world at z6 puts lon 35.2 in column 38.
    assert tile_for_point(35.2, 33.2, 7).x == 38


def test_tiles_covering_returns_observed_grid() -> None:
    # A box spanning the captured extent should include the observed tiles.
    bounds = GeoBounds(north=40.0, south=28.0, east=44.0, west=24.0)
    tiles = tiles_covering(bounds, mt_zoom=7)
    xs = {t.x for t in tiles}
    ys = {t.y for t in tiles}
    assert {36, 37, 38, 39} <= xs
    assert {24, 25, 26} <= ys
    # deterministic row-major ordering
    assert tiles == sorted(tiles, key=lambda t: (t.y, t.x))


def test_tiles_covering_small_box_is_single_tile() -> None:
    bounds = GeoBounds(north=33.3, south=33.1, east=35.3, west=35.1)
    tiles = tiles_covering(bounds, mt_zoom=7)
    assert tiles == [Tile(z=7, x=38, y=25)]


def test_zoom_must_exceed_offset() -> None:
    bounds = GeoBounds(north=1.0, south=0.0, east=1.0, west=0.0)
    with pytest.raises(ConfigError):
        tiles_covering(bounds, mt_zoom=1)
