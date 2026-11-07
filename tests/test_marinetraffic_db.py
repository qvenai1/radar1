"""
MarineTraffic DB row-builder tests (pure, offline — no database).

The geo classifier is scraplan's local AEQD machinery (same asset the AIS
vessel_row tests use), so these run without a DB or network. They pin the MT
row shape to migration 032's column order and the xref rule to migration 033.
"""

from __future__ import annotations

from datetime import datetime

from maritime.adapters.marinetraffic_translate import parse_mt_record, to_vessel_dict
from maritime.db import mt_vessel_row, mt_xref_row, parse_mt_lines

MT_CARGO = {
    "ship_id": "2786", "fetched_at": "2026-07-21T11:07:54.543602+00:00",
    "source": "interception", "in_area": True, "distance_km": 158.535,
    "data": {"ship_id": "2786", "mmsi": None, "latitude": 31.874666,
             "longitude": 34.576546, "speed_knots": 9.3, "course": 98.0,
             "heading": 100.0, "shipname": "VALENCE", "shiptype": "7",
             "flag": "MT", "destination": "ILASH", "length_m": 299.0,
             "width_m": 48.0, "elapsed_min": 2.0},
}


def _vessel(**overrides):
    rec = {**MT_CARGO, "data": {**MT_CARGO["data"], **overrides}}
    return to_vessel_dict(parse_mt_record(rec))


# --------------------------- mt_vessel_row ---------------------------

def test_mt_row_shape_and_types():
    row = mt_vessel_row(_vessel())
    assert row is not None and len(row) == 25          # matches migration 032
    assert isinstance(row[0], datetime) and row[0].tzinfo is not None  # ts tz-aware
    assert row[1] == "2786"                            # ship_id (the key)
    assert row[2] == "marinetraffic"                   # source
    assert row[3] == "terrestrial"                     # data_origin
    assert row[4] is None                              # mmsi absent (not faked)
    assert row[7] == 9.3                               # speed (knots, not normalized)
    assert row[12] == "VALENCE"                        # name
    assert row[18] == 299.0 and row[19] == 48.0        # length_m, beam_m (width→beam)
    assert row[20] == "MT"                             # flag preserved


def test_mt_row_mmsi_enrichment_when_present():
    row = mt_vessel_row(_vessel(mmsi="636092297"))
    assert row[4] == "636092297"                       # enrichment column populated


def test_mt_row_geo_in_syrian_territorial_waters():
    # ~3.6 km off Latakia — inside Syria's 12 NM territorial sea (same fixture
    # coordinate the AIS geo test uses, proving one shared classifier).
    row = mt_vessel_row(_vessel(latitude=35.55, longitude=35.70))
    assert row[21] == "Syria"                          # country
    assert row[22] is None                             # near_country None inside the band
    assert row[23] <= 22.224                           # distance_km within 12 NM
    assert row[24] == "scraplan-aoi"                   # geo_source: shared classifier


def test_mt_row_rejects_unkeyable():
    assert mt_vessel_row({"ship_id": None, "timestamp": "2026-07-21T11:00:00+00:00",
                          "lat": 1, "lon": 2}) is None
    assert mt_vessel_row({"ship_id": "1", "timestamp": None, "lat": 1, "lon": 2}) is None
    assert mt_vessel_row({"ship_id": "1", "timestamp": "2026-07-21T11:00:00+00:00",
                          "lat": None, "lon": None}) is None


# --------------------------- mt_xref_row ---------------------------

def test_xref_row_only_when_mmsi_present():
    assert mt_xref_row(_vessel()) is None              # no MMSI → not bridged
    x = mt_xref_row(_vessel(mmsi="636092297"))
    assert x == ("2786", "636092297", "VALENCE", "MT")


# --------------------------- parse_mt_lines ---------------------------

def test_parse_mt_lines_rows_xrefs_and_invalid():
    import json
    lines = [
        json.dumps(MT_CARGO),                                          # valid, no mmsi
        json.dumps({**MT_CARGO, "data": {**MT_CARGO["data"], "mmsi": "636092297"}}),  # valid + link
        "{bad json",                                                   # invalid
        json.dumps({**MT_CARGO, "data": {**MT_CARGO["data"],
                                         "latitude": None, "longitude": None}}),  # no fix
        "",                                                            # blank → ignored
    ]
    rows, xrefs, invalid = parse_mt_lines(lines)
    assert len(rows) == 2          # the two positioned records
    assert len(xrefs) == 1         # only the one carrying a real MMSI
    assert invalid == 2            # bad json + no-fix


# --- identity bridge (vessel_identities.jsonl -> vessel_xref) ---------------
# Pure row-builder tests, pinned to migration 033's columns. The DB write path
# is exercised operationally, not here: these fix the mapping rules.

MT_IDENTITY = {
    "fetched_at": "2026-08-11T12:46:47.602998+00:00",
    "ship_id": "10020697", "mmsi": "563307600", "imo": "1013353", "eni": None,
    "callsign": "9V9709", "name": "MAERSK FINISTERRE",
    "country": "SINGAPORE", "country_code": "SG",
    "ship_type": "Cargo - Hazard D (Recognizable)", "subtype": "Container Ship",
    "length_m": 265.0, "width_m": 38.0, "transponder_class": "Class A",
}


def test_identity_xref_maps_a_resolved_vessel() -> None:
    from maritime.db import mt_identity_xref
    assert mt_identity_xref(MT_IDENTITY) == (
        "10020697", "563307600", "MAERSK FINISTERRE", "SG")


def test_identity_xref_uses_country_code_not_country_name() -> None:
    """flag must stay in the tile feed's 2-letter space so both feeds compare."""
    from maritime.db import mt_identity_xref
    assert mt_identity_xref(MT_IDENTITY)[3] == "SG"          # not "SINGAPORE"


def test_identity_xref_skips_a_record_without_mmsi() -> None:
    """No MMSI is not a link. vessel_xref records links, never placeholders."""
    from maritime.db import mt_identity_xref
    assert mt_identity_xref({**MT_IDENTITY, "mmsi": None}) is None
    assert mt_identity_xref({**MT_IDENTITY, "mmsi": "  "}) is None


def test_identity_xref_skips_a_record_without_ship_id() -> None:
    from maritime.db import mt_identity_xref
    assert mt_identity_xref({**MT_IDENTITY, "ship_id": None}) is None


def test_identity_xref_tolerates_absent_optional_identity() -> None:
    """country_code is absent for ~1% of resolved vessels; the link still stands."""
    from maritime.db import mt_identity_xref
    row = mt_identity_xref({**MT_IDENTITY, "country_code": None, "name": None})
    assert row == ("10020697", "563307600", None, None)


def test_correlation_never_overwrites_a_fed_link() -> None:
    """Migration 033's precedence: a derived link must yield to either fed source."""
    from backend.vessel_crossref import _XREF_CORR_UPSERT
    assert "NOT IN ('marinetraffic-feed', 'marinetraffic-detail')" in _XREF_CORR_UPSERT
