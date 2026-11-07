"""G4a — GNSS-anomaly EVENT logic (backend/gnss_anomaly_events).

Pure tests of episode grouping and the Step-0 three-way classification, each case
tied to a frozen-set archetype: ELSEC_TEST (glitch, no plausible track), the Amman
decoy (area-jamming, many vessels/cell), and a Cyprus-tug-style coherent track
punctuated by teleports (individual). Plus the JammingZone area threshold and the
affected-zone outage subtraction (a feed-outage silence must not read as a jam).
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.gnss_anomaly import FLAG_IMPOSSIBLE_SPEED, FLAG_ON_LAND, FLAG_TELEPORT_RETURN
from backend.gnss_anomaly_events import (
    AREA_JAMMING,
    AREA_MIN_VESSELS,
    CANDIDATE,
    CANDIDATE_CAVEAT,
    EPISODE_GAP_TOL_S,
    GLITCH,
    METHOD,
    TYPE_AREA,
    TYPE_GLITCH,
    TYPE_INDIVIDUAL,
    Fix,
    JammingZone,
    affected_zone_cells,
    classify_episode,
    episodes,
    window_key,
    zone_cell,
)

UTC = timezone.utc
BASE = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
AMMAN = (31.72, 35.99)          # the decoy cell (on land)
SEA = (34.30, 32.10)            # open E-Med, an isolated cell


def _f(sec, lat, lon, implausible, flags=(), kn=None, inland=None):
    return Fix(BASE + timedelta(seconds=sec), lat, lon, implausible, tuple(flags), kn, inland)


def _zone_with(cell_latlon, ts, n_vessels):
    """A JammingZone where `n_vessels` distinct MMSIs sit in one cell x hour."""
    lat, lon = cell_latlon
    return JammingZone.from_positions(
        [(f"{900000000 + i}", ts, lat, lon) for i in range(n_vessels)])


# --- constants / caveat -------------------------------------------------------

def test_constants_and_caveat():
    assert METHOD == "gnss-anomaly-detector-v1"
    assert AREA_MIN_VESSELS == 10 and EPISODE_GAP_TOL_S == 3600.0
    assert (CANDIDATE, AREA_JAMMING, GLITCH) == ("candidate", "area-jamming", "glitch")
    assert "victim" in CANDIDATE_CAVEAT.lower() and "not proof" in CANDIDATE_CAVEAT.lower()


# --- episode grouping ---------------------------------------------------------

def test_episode_grouping_breaks_on_plausible_and_gap():
    fixes = [
        _f(0, *SEA, True), _f(60, *SEA, True),           # episode 1 (2 fixes)
        _f(120, *SEA, False),                            # recovery -> breaks
        _f(180, *SEA, True),                             # episode 2 starts
        _f(180 + int(EPISODE_GAP_TOL_S) + 60, *SEA, True),  # >gap_tol later -> episode 3
    ]
    eps = episodes(fixes)
    assert len(eps) == 3
    assert eps[0].n_fixes == 2 and eps[1].n_fixes == 1 and eps[2].n_fixes == 1
    assert eps[0].start_ts == fixes[0].ts and eps[0].end_ts == fixes[1].ts


def test_episode_carries_flag_union_and_evidence_maxima():
    fixes = [_f(0, *SEA, True, [FLAG_IMPOSSIBLE_SPEED], kn=300.0),
             _f(60, *SEA, True, [FLAG_TELEPORT_RETURN], kn=900.0, inland=None)]
    ep = episodes(fixes)[0]
    assert set(ep.flags) == {FLAG_IMPOSSIBLE_SPEED, FLAG_TELEPORT_RETURN}
    assert ep.implied_speed_max == 900.0


# --- the three-way classification ---------------------------------------------

def test_glitch_when_source_has_no_plausible_track():
    # ELSEC_TEST archetype: all fixes implausible, never a real track.
    ep = episodes([_f(0, *AMMAN, True, [FLAG_ON_LAND], inland=90.0)])[0]
    zone = _zone_with(AMMAN, window_key(BASE), 50)   # even in a dense cell...
    cls, gtype, detail = classify_episode(ep, zone, has_plausible_track=False)
    assert cls == GLITCH and gtype == TYPE_GLITCH   # ...glitch wins (no real track)
    assert detail["decoy_match"] is False


def test_area_when_episode_in_dense_cell():
    # Amman decoy archetype: a real vessel jammed into a cell shared by many.
    ep = episodes([_f(0, *AMMAN, True, [FLAG_ON_LAND, FLAG_TELEPORT_RETURN], inland=90.0)])[0]
    zone = _zone_with(AMMAN, window_key(BASE), 112)
    cls, gtype, detail = classify_episode(ep, zone, has_plausible_track=True)
    assert cls == AREA_JAMMING and gtype == TYPE_AREA
    assert detail["neighbours_normal"] is False
    assert detail["decoy_match"] is True            # on-land + area cell = decoy
    assert detail["distinct_vessels_in_cell"] == 112
    assert detail["cell"] == list(zone_cell(*AMMAN))


def test_individual_when_isolated_coherent_track():
    # Cyprus-tug archetype: coherent track, an isolated teleport, few neighbours.
    ep = episodes([_f(0, *SEA, True, [FLAG_TELEPORT_RETURN], kn=3000.0)])[0]
    zone = _zone_with(SEA, window_key(BASE), 1)      # only this vessel here
    cls, gtype, detail = classify_episode(ep, zone, has_plausible_track=True)
    assert cls == CANDIDATE and gtype == TYPE_INDIVIDUAL
    assert detail["neighbours_normal"] is True and detail["decoy_match"] is False


@pytest.mark.parametrize("n,expected", [(AREA_MIN_VESSELS, AREA_JAMMING),
                                        (AREA_MIN_VESSELS - 1, CANDIDATE)])
def test_area_threshold_boundary(n, expected):
    ep = episodes([_f(0, *SEA, True, [FLAG_TELEPORT_RETURN])])[0]
    zone = _zone_with(SEA, window_key(BASE), n)
    cls, _, _ = classify_episode(ep, zone, has_plausible_track=True)
    assert cls == expected


# --- JammingZone --------------------------------------------------------------

def test_jamming_zone_counts_distinct_vessels_per_cell_window():
    z = _zone_with(SEA, window_key(BASE), 12)
    cw = (*zone_cell(*SEA), window_key(BASE))
    assert z.vessels(cw) == 12 and z.is_area(cw) is True
    other = (*zone_cell(*AMMAN), window_key(BASE))
    assert z.vessels(other) == 0 and z.is_area(other) is False


# --- affected-zone outage subtraction (the binding requirement) ---------------

def test_affected_zone_subtracts_outage_windows():
    # one origin inside a feed-outage window (a silence, NOT a jam) must be dropped;
    # one outside must be counted. Models the frozen 33h+ open window.
    in_outage = BASE
    out_of_outage = BASE + timedelta(hours=6)
    outage = [(BASE - timedelta(hours=1), BASE + timedelta(hours=1))]  # covers in_outage
    origins = [("111", in_outage, *SEA), ("222", out_of_outage, *AMMAN)]
    cells = affected_zone_cells(origins, outage)
    assert (*zone_cell(*SEA), window_key(in_outage)) not in cells      # subtracted
    assert cells[(*zone_cell(*AMMAN), window_key(out_of_outage))] == 1  # kept
    # with NO outage windows, both are kept
    assert len(affected_zone_cells(origins, [])) == 2
