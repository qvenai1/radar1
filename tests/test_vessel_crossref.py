"""
AIS × MarineTraffic correlation core tests (pure, offline).

Pins the name normalization, the haversine, the dimension check, the confidence
tiers, and the one-to-one resolution (name-collision + mmsi-uniqueness) against
synthetic tracks — no DB, no clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.vessel_crossref import (
    Obs,
    correlate,
    dimensions_match,
    haversine_km,
    normalize_name,
)

T0 = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def _mt(key, name, lat, lon, dt_min=0, length=None, beam=None):
    return Obs(key, name, lat, lon, T0 + timedelta(minutes=dt_min), length, beam)


# --------------------------- primitives ---------------------------

def test_normalize_name():
    assert normalize_name("EVER GIVEN@@@") == "EVERGIVEN"
    assert normalize_name("msc  eleonora-III") == "MSCELEONORAIII"
    assert normalize_name("  ") is None
    assert normalize_name("AB") is None          # too short
    assert normalize_name(None) is None


def test_haversine_known_and_identity():
    assert haversine_km(35.0, 35.0, 35.0, 35.0) < 0.001   # ~0 within float precision
    d = haversine_km(35.0, 35.0, 35.0, 36.0)     # ~1° lon at 35°N ≈ 91 km
    assert 88 < d < 93


def test_dimensions_match():
    assert dimensions_match(300, 48, 305, 47) is True     # within 15%
    assert dimensions_match(300, 48, 200, 48) is False    # length conflict
    assert dimensions_match(300, None, None, 48) is None   # insufficient
    assert dimensions_match(None, None, None, None) is None


# --------------------------- confidence tiers ---------------------------

def test_direct_co_observation_is_strong():
    mt = [_mt("S1", "DEEP BLUE", 34.50, 35.70, 0)]
    ais = [_mt("111", "DEEP BLUE", 34.50, 35.70, 5)]      # same place, 5 min apart
    m = correlate(mt, ais)
    assert len(m) == 1
    assert m[0].ship_id == "S1" and m[0].mmsi == "111"
    assert m[0].confidence == "strong" and "direct" in m[0].reason


def test_name_only_noncontradictory_is_probable():
    # 100 km apart, 10 h apart, no dimensions → name anchors it but position can't
    # confirm (over 10 h that separation is plausible, so not weak) → probable.
    mt = [_mt("S1", "OTTOMANA", 34.0, 34.0, 0)]
    ais = [_mt("222", "OTTOMANA", 34.9, 34.0, 600)]
    m = correlate(mt, ais)
    assert m[0].confidence == "probable" and "non-contradictory" in m[0].reason


def test_dimensions_promote_to_strong():
    mt = [_mt("S1", "OTTOMANA", 34.0, 34.0, 0, length=200, beam=30)]
    ais = [_mt("222", "OTTOMANA", 34.9, 34.0, 600, length=203, beam=29)]
    m = correlate(mt, ais)
    assert m[0].confidence == "strong" and m[0].dim_match is True


def test_kinematically_impossible_is_weak():
    # 200 km in 30 min → 400 km/h → impossible for a ship → weak
    mt = [_mt("S1", "GHOST", 34.0, 34.0, 0)]
    ais = [_mt("333", "GHOST", 35.8, 34.0, 30)]
    m = correlate(mt, ais)
    assert m[0].confidence == "weak" and "inconsistent" in m[0].reason


def test_dimension_conflict_far_apart_is_weak():
    # names + conflicting dims, 200 km apart over 10 h (not impossible) → most
    # likely two different hulls sharing a name → weak collision.
    mt = [_mt("S1", "TWINS", 34.0, 34.0, 0, length=300, beam=48)]
    ais = [_mt("444", "TWINS", 35.8, 34.0, 600, length=120, beam=20)]
    m = correlate(mt, ais)
    assert m[0].confidence == "weak" and m[0].dim_match is False
    assert "collision" in m[0].reason


def test_colocation_overrides_dimension_conflict():
    # same name + SAME point (0 km) but conflicting dims → still the same hull
    # (can't share a point); dim conflict is a flagged discrepancy, not a veto.
    mt = [_mt("S1", "ENERGEAN", 34.5, 35.7, 0, length=300, beam=48)]
    ais = [_mt("444", "ENERGEAN", 34.5, 35.7, 5, length=120, beam=20)]
    m = correlate(mt, ais)
    assert m[0].confidence == "strong" and m[0].dim_match is False
    assert "discrepancy" in m[0].reason


# --------------------------- resolution ---------------------------

def test_no_name_no_match():
    mt = [_mt("S1", None, 34.5, 35.7, 0)]
    ais = [_mt("111", None, 34.5, 35.7, 0)]
    assert correlate(mt, ais) == []


def test_name_collision_picks_best_not_ambiguous():
    # one MT name matches two MMSIs; one co-located (best), one far/inconsistent
    mt = [_mt("S1", "TALI", 34.5, 35.7, 0)]
    ais = [
        _mt("111", "TALI", 34.5, 35.7, 5),       # co-located → strong (best)
        _mt("222", "TALI", 30.0, 34.0, 20),      # far, 20 min → impossible → weak
    ]
    m = correlate(mt, ais)
    assert len(m) == 1 and m[0].mmsi == "111"
    assert m[0].ambiguous is False


def test_true_tie_is_flagged_ambiguous():
    # two MMSIs equally co-located with the same name → cannot disambiguate
    mt = [_mt("S1", "PROTI", 34.5, 35.7, 0)]
    ais = [
        _mt("111", "PROTI", 34.5, 35.7, 5),
        _mt("222", "PROTI", 34.5, 35.7, 5),
    ]
    m = correlate(mt, ais)
    assert len(m) == 1 and m[0].ambiguous is True


def test_mmsi_uniqueness_keeps_higher():
    # two ship_ids share a name and both match one MMSI → only the stronger link kept
    mt = [
        _mt("S1", "CASTOR", 34.5, 35.7, 0),      # co-located → strong
        _mt("S2", "CASTOR", 34.0, 34.0, 0),      # far → weaker
    ]
    ais = [_mt("999", "CASTOR", 34.5, 35.7, 5, )]
    m = correlate(mt, ais)
    mmsis = [x.mmsi for x in m]
    assert mmsis.count("999") == 1               # the hull is not linked twice
    assert m[0].ship_id == "S1"
