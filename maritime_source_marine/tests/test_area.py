"""Area-of-interest tests against mtscraper's own committed AOI asset."""

from __future__ import annotations

from pathlib import Path

import pytest

from mtscraper.area import AreaOfInterest, AreaState, BboxArea, load_area_of_interest
from mtscraper.exceptions import ConfigError
from mtscraper.models import GeoBounds

ASSET = Path(__file__).resolve().parent.parent / "mtscraper" / "data" / "aoi_countries.geojson"


@pytest.fixture(scope="module")
def aoi() -> AreaOfInterest:
    return load_area_of_interest(ASSET, buffer_km=100.0)


def test_asset_present() -> None:
    assert ASSET.exists(), "run scripts/build_aoi_asset.py to generate the AOI asset"


def test_point_on_beirut_is_inside(aoi: AreaOfInterest) -> None:
    # Beirut, Lebanon (on land) -> INSIDE a country polygon.
    cls = aoi.classify(lat=33.89, lon=35.50)
    assert cls.state is AreaState.INSIDE
    assert cls.country == "Lebanon"


def test_ship_off_lebanon_coast_is_near(aoi: AreaOfInterest) -> None:
    # ~40 km west of Beirut, on water within the 100 km buffer.
    cls = aoi.classify(lat=33.9, lon=35.05)
    assert cls.state is AreaState.NEAR
    assert cls.near in {"Lebanon", "Cyprus", "Israel"}


def test_open_sea_is_outside(aoi: AreaOfInterest) -> None:
    # Deep central Mediterranean, far from any AOI coast.
    cls = aoi.classify(lat=34.0, lon=20.0)
    assert cls.state is AreaState.OUTSIDE


def test_bounding_box_covers_region(aoi: AreaOfInterest) -> None:
    box = aoi.bounding_box()
    assert box.contains(33.2, 35.2)  # the default centre
    assert box.north > box.south and box.east > box.west


def test_negative_buffer_rejected() -> None:
    with pytest.raises(ConfigError):
        load_area_of_interest(ASSET, buffer_km=-1.0)


def test_missing_asset_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_area_of_interest(tmp_path / "nope.geojson", buffer_km=50.0)


def test_bbox_area_classify() -> None:
    area = BboxArea(GeoBounds(north=34.0, south=32.0, east=36.0, west=34.0))
    assert area.classify(33.0, 35.0).state is AreaState.INSIDE
    assert area.classify(10.0, 10.0).state is AreaState.OUTSIDE
    assert area.center == (33.0, 35.0)
