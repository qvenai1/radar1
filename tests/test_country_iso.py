"""The AOI country name ⇄ ISO map — and the guard that keeps it honest.

The load-bearing test is `test_map_matches_the_boundary_asset_exactly`: the map is
hand-written, the boundary asset is the source of truth, and the two drifting apart
would silently un-code a country. That test fails the moment they disagree in either
direction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import country_iso as ci

ASSET = Path(__file__).resolve().parents[1] / "backend" / "data" / "aoi_countries.geojson"


def _asset_names() -> list[str]:
    return [f["properties"]["name"] for f in json.loads(ASSET.read_text())["features"]]


def test_map_matches_the_boundary_asset_exactly() -> None:
    """No country in the asset is uncoded, and no code exists for a country we do
    not actually classify. Both directions — a one-way check would let the map grow
    entries the AOI has no boundaries for."""
    assert sorted(ci.names()) == sorted(_asset_names())


def test_seven_countries() -> None:
    assert len(ci.AOI_NAME_TO_ISO) == 7
    assert len(ci.iso_codes()) == 7
    assert len(set(ci.iso_codes())) == 7, "codes must be unique"


def test_reverse_map_is_derived_not_duplicated() -> None:
    """Round-trips for all seven — the reverse map is built from the forward one, so
    they cannot drift."""
    for name, code in ci.AOI_NAME_TO_ISO.items():
        assert ci.to_iso(name) == code
        assert ci.to_name(code) == name


@pytest.mark.parametrize("spelling", ["Syria", "syria", "SYRIA", "SY", "sy", " Syria ", " sy "])
def test_normalise_accepts_name_or_code_any_case(spelling: str) -> None:
    assert ci.normalise(spelling) == "SY"


def test_normalise_rejects_a_non_aoi_country() -> None:
    """None means "not one of the seven" — the caller owns the HTTP semantics."""
    assert ci.normalise("United Kingdom") is None
    assert ci.normalise("GB") is None
    assert ci.normalise("") is None
    assert ci.normalise(None) is None


def test_to_iso_passes_an_unknown_name_through_verbatim() -> None:
    """Flag-never-delete: a country we cannot code is still one we observed, so it
    must not vanish from a `countries` list."""
    assert ci.to_iso("United Kingdom") == "United Kingdom"
    assert ci.to_iso("  Malta  ") == "Malta"
    assert ci.to_iso(None) is None
    assert ci.to_iso("") is None
