"""Behavioural analysis A2 — rendezvous logic + masks.

Proves the rendezvous SIGNATURE (close + slow + sustained), the port/busy
confound EXCLUSION, and the ship-type/flag PRIORITISATION (rank, never
auto-label): tanker+tanker / flags-of-convenience → high; offshore-support /
ferry / service → low.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.coverage_mask import CellSet, PROX_CELL_DEG
from backend.rendezvous import (
    CANDIDATE_CAVEAT,
    HIGH,
    LOW,
    MEDIUM,
    METHOD,
    RDV_MAX_DIST_M,
    RDV_MIN_DURATION_S,
    encounters,
    suspicion_rank,
    time_matched_distances,
)

UTC = timezone.utc


def _series(start_min, n, step_min, dist_deg, spd_a, spd_b):
    """Two vessels dist_deg apart (in latitude), n fixes step_min apart, from
    start_min. Returns (fixes_a, fixes_b) as (ts, lat, lon, speed)."""
    base = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
    fa, fb = [], []
    for i in range(n):
        t = base + timedelta(minutes=start_min + i * step_min)
        fa.append((t, 34.0, 32.0, spd_a))
        fb.append((t, 34.0 + dist_deg, 32.0, spd_b))
    return fa, fb


# --------------------------- suspicion ranking ---------------------------

@pytest.mark.parametrize("ta, tb, fa, fb, want", [
    ("tanker", "tanker", "Panama", "Liberia", HIGH),        # classic STS
    ("tanker", "tanker", "Greece", "Italy", HIGH),          # tanker+tanker high regardless of flag
    ("cargo", "cargo", "Panama", "Marshall Islands", HIGH), # FoC cargo pair
    ("cargo", "cargo", "Germany", "France", MEDIUM),        # non-FoC cargo pair
    ("tanker", "cargo", "Greece", "Italy", MEDIUM),         # mixed non-FoC
    ("other", "other", "Bahamas", "Liberia", LOW),          # offshore support/drillships
    ("passenger", "passenger", "Greece", "Greece", LOW),    # ferries
    ("tug", "tanker", "Panama", "Liberia", LOW),            # a service craft present -> low
    ("cargo", "unknown", "Panama", "Egypt", MEDIUM),        # insufficient signal
])
def test_suspicion_rank(ta, tb, fa, fb, want):
    assert suspicion_rank(ta, tb, fa, fb) == want


def test_suspicion_rank_is_symmetric():
    assert suspicion_rank("tanker", "tug", "x", "y") == suspicion_rank("tug", "tanker", "y", "x")


# --------------------------- distance + encounters ---------------------------

def test_time_matched_distances_pairs_and_skips():
    fa, fb = _series(0, 4, 15, 0.002, 1.0, 1.0)   # ~222 m apart
    series = time_matched_distances(fa, fb)
    assert len(series) == 4
    assert all(150 < d < 300 for _, d, _, _ in series)   # ~222 m
    # a B stream in a disjoint time window (> 5-min tol from every A fix) matches nothing
    shifted = [(t + timedelta(hours=3), la, lo, s) for t, la, lo, s in fb]
    assert time_matched_distances(fa, shifted) == []


def test_genuine_rendezvous_is_an_encounter():
    # ~222 m apart, both 1 kn, 7 fixes x 15 min = 90 min sustained -> one encounter
    fa, fb = _series(0, 7, 15, 0.002, 1.0, 1.0)
    encs = encounters(time_matched_distances(fa, fb))
    assert len(encs) == 1
    e = encs[0]
    assert e["duration_s"] >= RDV_MIN_DURATION_S
    assert e["min_distance_m"] < RDV_MAX_DIST_M
    assert e["both_max_speed"] <= 3.0


def test_too_far_apart_is_not_a_rendezvous():
    # ~1.1 km apart (> 500 m) -> no close fixes -> no encounter
    fa, fb = _series(0, 7, 15, 0.01, 1.0, 1.0)
    assert encounters(time_matched_distances(fa, fb)) == []


def test_fast_vessels_crossing_is_not_a_rendezvous():
    # close, but both 10 kn (crossing, not loitering) -> not "close" -> no encounter
    fa, fb = _series(0, 7, 15, 0.002, 10.0, 10.0)
    assert encounters(time_matched_distances(fa, fb)) == []


def test_brief_close_pass_is_not_a_rendezvous():
    # close + slow but only 30 min (< 1h) -> dropped
    fa, fb = _series(0, 3, 15, 0.002, 1.0, 1.0)   # 30 min
    assert encounters(time_matched_distances(fa, fb)) == []


def test_a_gap_splits_one_encounter_into_two():
    # two 90-min close windows separated by a >30-min break -> the break splits it;
    # each window is its own >=1h encounter.
    fa1, fb1 = _series(0, 7, 15, 0.002, 1.0, 1.0)      # 0..90 min
    fa2, fb2 = _series(180, 7, 15, 0.002, 1.0, 1.0)    # 180..270 min (90-min break)
    encs = encounters(time_matched_distances(fa1 + fa2, fb1 + fb2))
    assert len(encs) == 2


# --------------------------- masks ---------------------------

def test_cellset_excludes_its_cells():
    import math
    port_pt = (34.66, 33.04)     # a Cyprus anchorage cell
    open_pt = (35.50, 30.00)     # open sea
    cell = lambda la, lo: (math.floor(la / PROX_CELL_DEG), math.floor(lo / PROX_CELL_DEG))
    mask = CellSet({cell(*port_pt)})
    assert mask.contains(*port_pt) is True
    assert mask.contains(*open_pt) is False
    assert mask.contains(None, None) is False


def test_constants_and_caveat():
    assert METHOD == "rendezvous-detector-v1"
    assert "not proof" in CANDIDATE_CAVEAT.lower()
    assert RDV_MAX_DIST_M == 500.0 and RDV_MIN_DURATION_S == 3600.0
