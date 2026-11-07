"""P3 — the shared, source-neutral geo classifier (backend/geo_area.py).

These tests prove the lift from adsb_scraper/area.py preserved behaviour on
canonical points, that classification is deterministic (safe to re-run at
ingest), and that a missing asset fails fast.
"""

import json

import pytest

from backend.geo_area import (
    DEFAULT_TERRITORIAL_SEA_KM,
    AreaState,
    GeoAssetError,
    get_area,
    load_area_of_interest,
    territorial_sea_km,
)

AREA = get_area()


@pytest.mark.parametrize(
    "lat, lon, country",
    [
        (33.5138, 36.2765, "Syria"),    # Damascus
        (35.1856, 33.3823, "Cyprus"),   # Nicosia
        (33.3152, 44.3661, "Iraq"),     # Baghdad
        (33.8938, 35.5018, "Lebanon"),  # Beirut
        (31.7683, 35.2137, "Israel"),   # Jerusalem
        (39.9334, 32.8597, "Turkey"),   # Ankara
        (31.9539, 35.9106, "Jordan"),   # Amman
    ],
)
def test_inside_each_country(lat, lon, country):
    result = AREA.classify(lat, lon)
    assert result.state is AreaState.INSIDE
    assert result.country == country
    assert result.near is None


def test_far_point_is_outside():
    result = AREA.classify(55.9607, -30.7313)  # mid-Atlantic
    assert result.state is AreaState.OUTSIDE
    assert result.country is None and result.near is None


def test_near_border_within_buffer():
    # A point just outside every country but within the 50 km border buffer
    # classifies NEAR, naming the nearest country (not INSIDE, not OUTSIDE).
    result = AREA.classify(42.3, 35.0)  # just north of the Turkey coast
    assert result.state is AreaState.NEAR
    assert result.near in AREA.country_names
    assert result.country is None


def test_classify_columns_mapping():
    inside = AREA.classify_columns(33.5138, 36.2765)  # Damascus
    assert inside.country == "Syria" and inside.near_country is None
    assert isinstance(inside.distance_km, float) and inside.distance_km > 0
    outside = AREA.classify_columns(55.9607, -30.7313)
    assert outside.country is None and outside.near_country is None
    assert isinstance(outside.distance_km, float)


def test_classification_is_deterministic():
    # same point → same result every call (idempotent re-classification at ingest).
    a = AREA.classify_columns(33.5138, 36.2765)
    b = AREA.classify_columns(33.5138, 36.2765)
    assert a == b


def test_missing_asset_fails_fast(tmp_path):
    with pytest.raises(GeoAssetError):
        load_area_of_interest(tmp_path / "does_not_exist.geojson", 50.0)


def test_incomplete_asset_fails_fast(tmp_path):
    # an asset missing one of the seven countries is rejected.
    asset = tmp_path / "partial.geojson"
    asset.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "Syria"},
            "geometry": {"type": "Polygon",
                         "coordinates": [[[35, 32], [42, 32], [42, 37], [35, 37], [35, 32]]]},
        }],
    }))
    with pytest.raises(GeoAssetError):
        load_area_of_interest(asset, 50.0)


def test_negative_buffer_rejected(tmp_path):
    with pytest.raises(GeoAssetError):
        load_area_of_interest(tmp_path / "x.geojson", -1.0)


# --------------------------- maritime layer (M1) ---------------------------
# The vessel geo classifier reuses the SAME AEQD distance machinery as the
# aircraft classifier, with the 12 NM territorial-sea band as the maritime
# "near a country" threshold. distance_km here is the TRUE km to the nearest
# country's coast (not the aircraft envelope-centre provenance).


def test_territorial_sea_default_is_12nm():
    # 12 NM * 1.852 km/NM = 22.224 km, the UNCLOS Art. 3 territorial-sea limit.
    assert round(DEFAULT_TERRITORIAL_SEA_KM, 3) == 22.224
    assert territorial_sea_km() == DEFAULT_TERRITORIAL_SEA_KM


@pytest.mark.parametrize(
    "lat, lon, country",
    [
        (35.55, 35.70, "Syria"),    # off Latakia (~3.6 km)
        (33.90, 35.40, "Lebanon"),  # off Beirut (~6.5 km)
        (32.83, 34.90, "Israel"),   # off Haifa (~4.8 km)
        (34.60, 33.00, "Cyprus"),   # south of Cyprus (on/inside the polygon)
        (36.60, 34.60, "Turkey"),   # off Mersin (~16 km)
        (29.40, 34.95, "Jordan"),   # Gulf of Aqaba (~1.5 km)
        (42.20, 35.00, "Turkey"),   # Black Sea off Turkey (~12 km)
    ],
)
def test_vessel_in_territorial_waters(lat, lon, country):
    # A vessel within 12 NM of a country's coast is in that country's
    # territorial sea: country set, near_country None, distance within the band.
    m = AREA.classify_maritime_columns(lat, lon)
    assert m.country == country
    assert m.near_country is None
    assert m.distance_km is not None and m.distance_km <= DEFAULT_TERRITORIAL_SEA_KM


@pytest.mark.parametrize(
    "lat, lon",
    [
        (34.00, 32.00),   # mid Eastern Mediterranean
        (25.50, 55.00),   # Persian Gulf near the UAE (not one of the seven)
        (21.50, 39.00),   # Red Sea near Jeddah (not one of the seven)
        (31.25, 29.90),   # off Alexandria — EGYPT is NOT in the seven-country AOI
    ],
)
def test_vessel_open_sea_is_outside(lat, lon):
    # Beyond 12 NM of every one of the seven countries → OUTSIDE: country None,
    # near_country names the nearest of the seven, distance beyond the band.
    m = AREA.classify_maritime_columns(lat, lon)
    assert m.country is None
    assert m.near_country in AREA.country_names
    assert m.distance_km is not None and m.distance_km > DEFAULT_TERRITORIAL_SEA_KM


def test_country_and_near_country_mutually_exclusive():
    # For any positioned vessel exactly one of {country, near_country} is set —
    # the aircraft INSIDE/NEAR invariant, adapted to the sea.
    for lat, lon in [(35.55, 35.70), (34.00, 32.00), (34.60, 33.00), (25.50, 55.00)]:
        m = AREA.classify_maritime_columns(lat, lon)
        assert (m.country is None) != (m.near_country is None)
        assert (m.country is not None) == (m.distance_km <= DEFAULT_TERRITORIAL_SEA_KM)


def test_boundary_is_real_kilometres():
    # Prove the threshold is a TRUE kilometric distance, not a degree offset:
    # for a fixed point, flipping the band above/below the point's real distance
    # flips its territorial-sea membership — while distance_km itself is unchanged.
    lat, lon = 36.60, 34.60  # ~16 km off Mersin, Turkey
    d = AREA.classify_maritime_columns(lat, lon).distance_km
    assert 15.0 < d < 18.0
    inside = AREA.classify_maritime_columns(lat, lon, territorial_sea_km=d + 0.5)
    outside = AREA.classify_maritime_columns(lat, lon, territorial_sea_km=d - 0.5)
    assert inside.country == "Turkey" and inside.near_country is None
    assert outside.country is None and outside.near_country == "Turkey"
    assert inside.distance_km == outside.distance_km == d  # distance is the true value


def test_default_12nm_band_boundary_case():
    # ~23.8 km off Umm Qasr (Iraq) sits just BEYOND the 12 NM band (22.224 km),
    # so it is OUTSIDE Iraq's territorial sea at the default band — a real
    # kilometric boundary case, nearest coast still named.
    m = AREA.classify_maritime_columns(29.90, 48.80)
    assert m.country is None and m.near_country == "Iraq"
    assert 22.224 < m.distance_km < 26.0


def test_maritime_classification_is_deterministic():
    # same point → same result every call (idempotent re-classification at ingest
    # and re-backfill).
    a = AREA.classify_maritime_columns(35.55, 35.70)
    b = AREA.classify_maritime_columns(35.55, 35.70)
    assert a == b


def test_aircraft_distance_semantics_unchanged():
    # The aircraft classify_columns distance is the envelope-centre provenance
    # anchor (a large, point-invariant-ish number), DISTINCT from the maritime
    # distance-to-coast — proving the maritime layer did not perturb it.
    air = AREA.classify_columns(35.55, 35.70)      # off Latakia
    sea = AREA.classify_maritime_columns(35.55, 35.70)
    assert air.distance_km > 100                    # distance from AOI centre
    assert sea.distance_km < 10                      # distance to Syrian coast
    assert air.distance_km != sea.distance_km
