"""Behavioural analysis A1 — AIS-gap classification + coverage mask.

Proves the confound removal that is the WHOLE point of A1: a gap during a feed
outage, a stationary vessel's sparse gap, and a gap at the coverage edge are all
classified honestly (not 'candidate'); only an underway vessel in dense coverage
outside any outage is a candidate. Also proves the coverage mask is data-driven
(a Cyprus mid-sea cell is dense) — NOT naive coast-distance.
"""

from datetime import datetime, timezone


from backend.coverage_mask import CELL_DEG, CoverageMask
from backend.gap_detect import (
    CANDIDATE,
    CANDIDATE_CAVEAT,
    EDGE,
    GAP_THRESHOLD_S,
    JAMMING_EXCLUDED,
    METHOD,
    OUTAGE_EXCLUDED,
    STATIONARY,
    OutageWindow,
    classify_gap,
    is_underway,
    overlapping_window,
    overlaps_outage,
    reconstruct_outage_windows,
)

UTC = timezone.utc


def _cell(lat, lon):
    import math
    return (math.floor(lat / CELL_DEG), math.floor(lon / CELL_DEG))


# A mask where a Cyprus MID-SEA cell (far from any mainland) is dense, a busy
# coastal cell is dense, and a near-coast low-traffic cell + open sea are sparse.
CYPRUS_MIDSEA = (34.80, 32.55)     # ~mid-sea SW of Cyprus, far from mainland
COAST_DENSE = (36.60, 34.60)       # off Mersin, busy
COAST_SPARSE = (35.90, 35.95)      # a quiet near-coast cell
OPEN_SEA = (33.20, 30.10)          # open E-Med, sparse
MASK = CoverageMask({
    _cell(*CYPRUS_MIDSEA): 800,    # dense despite being far offshore
    _cell(*COAST_DENSE): 1200,
    _cell(*COAST_SPARSE): 12,      # near coast but low count -> sparse
    _cell(*OPEN_SEA): 4,
}, threshold=100)


def test_coverage_mask_is_data_driven_not_coast_distance():
    # The Cyprus mid-sea cell is DENSE even though it is far from any mainland —
    # proving coverage reflects real receivers (Cyprus), not distance-to-coast.
    assert MASK.is_dense(*CYPRUS_MIDSEA) is True
    assert MASK.is_dense(*COAST_DENSE) is True
    # A cell CLOSE to the coast but low-traffic is sparse — distance would call it
    # dense; density correctly calls it sparse.
    assert MASK.is_dense(*COAST_SPARSE) is False
    assert MASK.is_dense(*OPEN_SEA) is False
    assert MASK.is_dense(None, None) is False


def test_coverage_mask_density_and_params():
    assert MASK.cell_density(*COAST_DENSE) == 1200
    assert MASK.cell_density(*OPEN_SEA) == 4
    assert MASK.cell_density(None, None) == 0
    assert MASK.params["threshold"] == 100 and MASK.dense_cells == 2


def test_reconstruct_outage_windows():
    t = lambda h: datetime(2026, 7, 15, h, 0, tzinfo=UTC)
    events = [
        (t(1), "feed_connect"),
        (t(3), "feed_disconnect"),   # recorded window 3->5
        (t(5), "feed_connect"),
        (t(9), "feed_disconnect"),   # still down at run time -> ongoing outage to `now`
    ]
    w = reconstruct_outage_windows(events, now=t(12))
    assert w == [
        OutageWindow(t(3), t(5), inferred=False, basis="recorded"),
        OutageWindow(t(9), t(12), inferred=False, basis="recorded-open"),  # Finding A
    ]


def test_overlaps_outage():
    t = lambda h: datetime(2026, 7, 15, h, 0, tzinfo=UTC)
    windows = [OutageWindow(t(3), t(5))]
    assert overlaps_outage(t(2), t(4), windows) is True    # straddles start
    assert overlaps_outage(t(4), t(6), windows) is True    # straddles end
    assert overlaps_outage(t(1), t(2), windows) is False   # before
    assert overlaps_outage(t(6), t(8), windows) is False   # after
    assert overlaps_outage(t(2), None, windows) is True    # open gap reaching the outage
    assert overlapping_window(t(2), t(4), windows).basis == "recorded"  # provenance surfaced
    assert overlapping_window(t(6), t(8), windows) is None


# --- G3 addendum: outage-window integrity (real 2026-07-19/20 incident shapes) ---

def test_finding_a_open_disconnect_is_ongoing_outage_to_now():
    # The real incident: feed_disconnect at 2026-07-20 00:04:01, no following
    # feed_connect (upstream TLS cert expiry). Naive pairing DROPPED it; the fix
    # treats it as an ongoing outage to the run timestamp.
    conn = datetime(2026, 7, 19, 17, 46, 38, tzinfo=UTC)
    disc = datetime(2026, 7, 20, 0, 4, 1, tzinfo=UTC)
    now = datetime(2026, 7, 20, 13, 30, 0, tzinfo=UTC)   # 13h later, still down
    windows = reconstruct_outage_windows([(conn, "feed_connect"), (disc, "feed_disconnect")], now)
    assert windows == [OutageWindow(disc, now, inferred=False, basis="recorded-open")]
    # a would-be candidate (underway, dense coverage) that spans the ongoing outage
    # is correctly outage-excluded, not a candidate.
    cls = classify_gap(
        gap_start=datetime(2026, 7, 20, 0, 3, tzinfo=UTC),
        gap_end=datetime(2026, 7, 20, 6, 0, tzinfo=UTC),
        start_lat=CYPRUS_MIDSEA[0], start_lon=CYPRUS_MIDSEA[1],
        nav_status=0, speed=10.0, mask=MASK, outage_windows=windows)
    assert cls == OUTAGE_EXCLUDED


def test_finding_b_unpaired_connect_infers_outage_bounded_by_liveness():
    # The real incident: feed_connect 08:46 then a second feed_connect 13:48 with NO
    # intervening feed_disconnect (silent session death). Whether an outage is
    # inferred depends on whether data actually stopped before the reconnect.
    c1 = datetime(2026, 7, 19, 8, 46, 22, tzinfo=UTC)
    c2 = datetime(2026, 7, 19, 13, 48, 9, tzinfo=UTC)
    now = datetime(2026, 7, 19, 18, 0, tzinfo=UTC)
    events = [(c1, "feed_connect"), (c2, "feed_connect")]

    # (a) THE REAL INCIDENT: positions flowed until ~2 s before the reconnect — a
    # seamless reconnect, not an outage. NO window is inferred, so a vessel that went
    # dark across the reconnect is still a genuine lead (never excluded by a feed blip).
    live = datetime(2026, 7, 19, 13, 48, 7, tzinfo=UTC)   # 2 s before c2
    w_live = reconstruct_outage_windows(events, now, {c2: live})
    assert w_live == []
    distant_gap = classify_gap(
        gap_start=datetime(2026, 7, 19, 10, 0, tzinfo=UTC),
        gap_end=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        start_lat=CYPRUS_MIDSEA[0], start_lon=CYPRUS_MIDSEA[1],
        nav_status=0, speed=10.0, mask=MASK, outage_windows=w_live)
    assert distant_gap == CANDIDATE

    # (b) a GENUINE silent outage: no position archived for hours before the reconnect
    # (data stopped at 09:00). Now an outage IS inferred, from the last liveness to the
    # reconnect, and a gap inside it is outage-excluded.
    stale = datetime(2026, 7, 19, 9, 0, tzinfo=UTC)
    w_out = reconstruct_outage_windows(events, now, {c2: stale})
    assert w_out == [OutageWindow(stale, c2, inferred=True, basis="unpaired-connect")]
    covered_gap = classify_gap(
        gap_start=datetime(2026, 7, 19, 10, 0, tzinfo=UTC),
        gap_end=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        start_lat=CYPRUS_MIDSEA[0], start_lon=CYPRUS_MIDSEA[1],
        nav_status=0, speed=10.0, mask=MASK, outage_windows=w_out)
    assert covered_gap == OUTAGE_EXCLUDED

    # (c) no liveness data at all -> fall back to the prior connect (widest window).
    w_fb = reconstruct_outage_windows(events, now)
    assert w_fb == [OutageWindow(c1, c2, inferred=True, basis="unpaired-connect")]


def test_is_underway():
    assert is_underway(0, 8.0) is True
    assert is_underway(0, 0.5) is False    # too slow
    assert is_underway(1, 8.0) is False    # at anchor
    assert is_underway(None, 8.0) is False
    assert is_underway(0, None) is False


# --- the four classifications (priority: outage > stationary > edge > candidate) ---

def _t(h):
    return datetime(2026, 7, 15, h, 0, tzinfo=UTC)

OUTAGE = [OutageWindow(_t(3), _t(6))]


def test_gap_during_feed_outage_is_outage_excluded():
    # #1 confound: a gap overlapping a feed_disconnect window is NOT a candidate,
    # even for an underway vessel in dense coverage.
    cls = classify_gap(gap_start=_t(2), gap_end=_t(7), start_lat=COAST_DENSE[0],
                       start_lon=COAST_DENSE[1], nav_status=0, speed=10.0,
                       mask=MASK, outage_windows=OUTAGE)
    assert cls == OUTAGE_EXCLUDED


def test_stationary_vessel_gap_is_stationary():
    # anchored/moored (or ~0 speed) -> sparse rows expected, not going dark.
    for ns, spd in [(1, 8.0), (5, 8.0), (0, 0.2)]:
        cls = classify_gap(gap_start=_t(10), gap_end=_t(12), start_lat=COAST_DENSE[0],
                           start_lon=COAST_DENSE[1], nav_status=ns, speed=spd,
                           mask=MASK, outage_windows=OUTAGE)
        assert cls == STATIONARY


def test_edge_gap_is_edge():
    # underway but the gap starts OUTSIDE dense coverage -> natural coverage edge.
    cls = classify_gap(gap_start=_t(10), gap_end=_t(12), start_lat=OPEN_SEA[0],
                       start_lon=OPEN_SEA[1], nav_status=0, speed=10.0,
                       mask=MASK, outage_windows=OUTAGE)
    assert cls == EDGE


def test_genuine_candidate():
    # underway, dense coverage, outside any outage -> candidate (investigation lead).
    cls = classify_gap(gap_start=_t(10), gap_end=_t(14), start_lat=CYPRUS_MIDSEA[0],
                       start_lon=CYPRUS_MIDSEA[1], nav_status=0, speed=9.0,
                       mask=MASK, outage_windows=OUTAGE)
    assert cls == CANDIDATE


def test_priority_outage_beats_stationary_and_edge():
    # a gap that is stationary AND at the edge AND during an outage -> outage wins.
    cls = classify_gap(gap_start=_t(4), gap_end=_t(5), start_lat=OPEN_SEA[0],
                       start_lon=OPEN_SEA[1], nav_status=1, speed=0.0,
                       mask=MASK, outage_windows=OUTAGE)
    assert cls == OUTAGE_EXCLUDED


def test_fifth_confound_jamming_waterfall_position():
    # G4b: a would-be candidate in a jamming zone (or spanning implausible) is
    # jamming-excluded; priority outage > jamming > stationary > edge > candidate.
    common = dict(start_lat=CYPRUS_MIDSEA[0], start_lon=CYPRUS_MIDSEA[1], mask=MASK,
                  outage_windows=OUTAGE)
    # underway + dense + not-outage, but in a jamming zone -> jamming-excluded
    assert classify_gap(gap_start=_t(10), gap_end=_t(14), nav_status=0, speed=9.0,
                        in_jamming_zone=True, **common) == JAMMING_EXCLUDED
    # outage BEATS jamming (data-collection fact first)
    assert classify_gap(gap_start=_t(4), gap_end=_t(5), nav_status=0, speed=9.0,
                        in_jamming_zone=True, **common) == OUTAGE_EXCLUDED
    # jamming BEATS stationary (a jammed anchored vessel is a GNSS artifact, not sparse)
    assert classify_gap(gap_start=_t(10), gap_end=_t(12), nav_status=1, speed=0.0,
                        in_jamming_zone=True, **common) == JAMMING_EXCLUDED
    # not in a jamming zone -> unchanged (still a candidate)
    assert classify_gap(gap_start=_t(10), gap_end=_t(14), nav_status=0, speed=9.0,
                        in_jamming_zone=False, **common) == CANDIDATE


def test_constants():
    assert GAP_THRESHOLD_S == 3600.0
    assert METHOD == "ais-gap-detector-v1"
    assert "not proof" in CANDIDATE_CAVEAT.lower() or "not proven" in CANDIDATE_CAVEAT.lower()
