"""PORT-2 — pure port-visit sessionization.

No DB, no endpoint: the rules are asserted directly on the pure module, so the visit
bar, the four completeness states, the stricter-than-G3 quality filter and the
attribution/geometry honesty are testable without live data. Annotation-only — nothing
here writes events or touches the composite.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend import port_visit as pv
from backend.gap_detect import OutageWindow

T0 = datetime(2026, 7, 20, 6, 0, 0, tzinfo=timezone.utc)
WIN_START = T0 - timedelta(days=2)
WIN_END = T0 + timedelta(days=2)

#: A stand-in for a WPI 'M' gazetteer entry (3.5 km assumed radius, arcminute point).
PORT = {
    "port_id": "wpi-test", "port_name": "Testport", "lat": 32.82, "lon": 35.00,
    "radius_m": 3500.0, "radius_basis": "assumed", "coord_precision": "arcminute",
    "harbor_size": "M", "harbor_type": "CB", "country": "Israel",
}
#: ~9 km east of PORT's centre — comfortably outside the radius, inside a padded box.
OUTSIDE = (32.82, 35.096)


def fix(minutes, *, speed=0.1, inside=True, quality=None, sog=None):
    lat, lon = (PORT["lat"], PORT["lon"]) if inside else OUTSIDE
    return pv.PortFix(ts=T0 + timedelta(minutes=minutes), lat=lat, lon=lon,
                      implied_speed_kn=speed, reported_sog_kn=sog, position_quality=quality)


def dwell(n=19, step=10, *, speed=0.1, start=0, quality=None):
    """A berthed run: n fixes `step` min apart at `speed` kn. Default 19x10 min = 3 h,
    clearing the 2 h low-speed span bar."""
    return [fix(start + i * step, speed=speed, quality=quality) for i in range(n)]


def run(fixes, **kw):
    kw.setdefault("window_start", WIN_START)
    kw.setdefault("window_end", WIN_END)
    return pv.port_visits(PORT, "123456789", fixes, **kw)


# --------------------------------------------------------------- the visit bar
def test_visit_bar_accepts_a_berthed_call():
    visits, dropped, _ = run(dwell())
    assert len(visits) == 1 and not dropped
    v = visits[0]
    assert v.n_fixes == 19
    assert v.duration_s == 10800.0                     # 3 h present
    assert v.low_speed_span_s == 10800.0               # and all of it low-speed
    assert v.frac_low_speed == 1.0
    assert v.duration_is_lower_bound is True
    assert v.min_distance_m == 0.0                     # the centre fix
    assert v.mean_implied_speed_kn == 0.1


def test_transit_bar_excludes_a_two_minute_crossing():
    """PORT-pre: 61 % of raw circle-crossings are transits (p50 0.37 h). A presence test
    alone measures traffic, not visits."""
    crossing = [fix(0, speed=12.0), fix(1, speed=12.0), fix(2, speed=12.0)]
    visits, dropped, _ = run(crossing)
    assert visits == []
    assert dropped == {pv.B_BELOW_DURATION: 1}         # charged to the FIRST bar it fails


def test_funnel_charges_each_crossing_to_its_first_failed_bar():
    # a single in-radius fix -> single_fix
    v1, d1, _ = run([fix(0)])
    assert v1 == [] and d1 == {pv.B_SINGLE_FIX: 1}
    # 90 min present but only 30 min of low-speed span -> below_low_speed_span
    slow_then_fast = [fix(0, speed=0.1), fix(30, speed=0.1)] + \
                     [fix(60, speed=8.0), fix(90, speed=8.0)]
    v2, d2, _ = run(slow_then_fast)
    assert v2 == [] and d2 == {pv.B_BELOW_LOW_SPEED_SPAN: 1}
    # 3 h present, >2 h low span, but a MINORITY of fixes low -> not_majority_low
    mixed = ([fix(0, speed=0.1)]
             + [fix(150, speed=0.1)]                    # one 2.5 h low interval
             + [fix(160 + i * 5, speed=9.0) for i in range(6)])
    v3, d3, _ = run(mixed)
    assert v3 == [] and d3 == {pv.B_NOT_MAJORITY_LOW: 1}
    assert set(d3) <= set(pv.FUNNEL_BARS)


def test_gap_split_separates_two_calls():
    """A break wider than the 6 h split is two visits, not one long one."""
    first = dwell()                                     # 0..180 min
    second = dwell(start=180 + 8 * 60)                  # +8 h later
    visits, _, _ = run(first + second)
    assert len(visits) == 2
    assert (visits[1].start_ts - visits[0].end_ts).total_seconds() > pv.GAP_SPLIT_S


def test_absent_implied_speed_never_manufactures_a_visit():
    """None implied speed is absent evidence, not slow movement."""
    visits, dropped, _ = run(dwell(speed=None))
    assert visits == []
    assert dropped == {pv.B_BELOW_LOW_SPEED_SPAN: 1}


# --------------------------------------------------------------- quality filter
def test_quality_filter_passes_null_and_drops_suspect():
    """THE SUBTLE ONE. Stricter than G3 (which passes suspect) because on-land decoys
    land inside harbour radii — but NULL must PASS: it means 'not yet scored by the
    15-min quality timer', so the newest fixes are always NULL and dropping them would
    truncate every in-progress session and manufacture false 'no departure observed'."""
    assert pv.accept_fix(None) is True                  # not yet checked -> keep
    assert pv.accept_fix("plausible") is True
    assert pv.accept_fix("suspect") is False            # stricter than G3
    assert pv.accept_fix("implausible") is False
    assert "suspect" in pv.REJECTED_QUALITY and None not in pv.REJECTED_QUALITY

    # a NULL-quality run still produces a visit
    visits, _, _ = run(dwell(quality=None))
    assert len(visits) == 1
    # ...and an all-suspect run produces none
    none_visits, _, _ = run(dwell(quality="suspect"))
    assert none_visits == []


def test_dropped_fixes_are_counted_per_session_so_the_tightening_is_visible():
    fixes = dwell()
    # three decoy-ish fixes inside the session span, at the harbour centre
    fixes += [fix(45, quality="suspect"), fix(75, quality="suspect"),
              fix(105, quality="implausible")]
    fixes.sort(key=lambda f: f.ts)
    visits, _, total = run(fixes)
    assert len(visits) == 1
    assert visits[0].dropped_fixes == 3                 # attributed to the session span
    assert total == 3
    # and they did not inflate the session
    assert visits[0].n_fixes == 19


# --------------------------------------------------------------- completeness
def test_four_completeness_states_and_their_reasons():
    body = dwell()                                      # in-radius 0..180 min

    # complete — an outside fix on each side, inside the flank
    v = run([fix(-30, inside=False, speed=6.0)] + body +
            [fix(210, inside=False, speed=6.0)])[0][0]
    assert v.completeness == pv.COMPLETE
    assert v.entry_ts and v.exit_ts
    assert v.entry_missing_reason is None and v.exit_missing_reason is None
    assert v.arrival_ts_is_unknown is False

    # open_ended — entry seen, no exit; feed was up, so vessel_silence
    v = run([fix(-30, inside=False, speed=6.0)] + body)[0][0]
    assert v.completeness == pv.OPEN_ENDED
    assert v.entry_ts and v.exit_ts is None
    assert v.exit_missing_reason == pv.R_VESSEL_SILENCE
    assert v.arrival_ts_is_unknown is False             # arrival WAS observed

    # truncated — exit seen, no entry
    v = run(body + [fix(210, inside=False, speed=6.0)])[0][0]
    assert v.completeness == pv.TRUNCATED
    assert v.entry_ts is None and v.exit_ts
    assert v.entry_missing_reason == pv.R_VESSEL_SILENCE
    assert v.arrival_ts_is_unknown is True

    # presence_only — neither boundary
    v = run(body)[0][0]
    assert v.completeness == pv.PRESENCE_ONLY
    assert v.entry_ts is None and v.exit_ts is None
    assert v.entry_missing_reason and v.exit_missing_reason
    assert v.arrival_ts_is_unknown is True


def test_arrival_unknown_is_set_only_on_the_entry_missing_states():
    assert set(pv.ENTRY_MISSING_STATES) == {pv.TRUNCATED, pv.PRESENCE_ONLY}
    body = dwell()
    cases = {
        pv.COMPLETE: [fix(-30, inside=False, speed=6.0)] + body + [fix(210, inside=False, speed=6.0)],
        pv.OPEN_ENDED: [fix(-30, inside=False, speed=6.0)] + body,
        pv.TRUNCATED: body + [fix(210, inside=False, speed=6.0)],
        pv.PRESENCE_ONLY: body,
    }
    for state, fixes in cases.items():
        v = run(fixes)[0][0]
        assert v.completeness == state
        # a lower bound on a session with NO observed arrival is a lower bound on an
        # UNKNOWN quantity — a strictly weaker claim, flagged distinctly
        assert v.arrival_ts_is_unknown is (state in pv.ENTRY_MISSING_STATES)
        assert v.duration_is_lower_bound is True        # always, in every state


def test_missing_boundary_attributed_to_outage_when_the_feed_was_down():
    """~51 % of missing entries and ~45 % of missing exits coincide with a dead feed
    hour, so the reason is determined against reconstructed outage windows (imported
    from gap_detect, never reimplemented)."""
    body = dwell()
    end = body[-1].ts
    win = [OutageWindow(end + timedelta(minutes=10), end + timedelta(hours=3),
                        inferred=False, basis="recorded")]
    v = run(body, outage_windows=win)[0][0]
    assert v.exit_missing_reason == pv.R_OUTAGE
    assert v.exit_outage_gap_s and v.exit_outage_gap_s > 0
    # the two sides are attributed SEPARATELY: this entry was vessel silence, and the
    # exit-side outage must not leak into the arrival explanation
    assert v.entry_missing_reason == pv.R_VESSEL_SILENCE
    assert v.entry_outage_gap_s is None
    assert "no arrival observed — vessel silent" in pv.describe_boundary(v)
    assert "feed down" in pv.describe_boundary(v).split(";")[1]
    # with no outage the same shape is vessel silence — the distinction is real
    v2 = run(body)[0][0]
    assert v2.exit_missing_reason == pv.R_VESSEL_SILENCE
    assert v2.exit_outage_gap_s is None


def test_negligible_outage_overlap_never_renders_as_feed_down_for_zero():
    """A sub-minute overlap must not print "feed down for 0.0 h", which reads as a
    contradiction of the outage it is explaining."""
    body = dwell()
    end = body[-1].ts
    # an outage window that barely clips the flank interval
    tiny = [OutageWindow(end + timedelta(hours=5, minutes=59, seconds=40),
                         end + timedelta(hours=9), inferred=False, basis="recorded")]
    v = run(body, outage_windows=tiny)[0][0]
    assert v.exit_missing_reason == pv.R_OUTAGE
    assert v.exit_outage_gap_s is not None and v.exit_outage_gap_s < 60
    note = pv.describe_boundary(v)
    assert "feed down" in note and "0.0 h" not in note
    # a minutes-scale overlap reads in minutes, not as 0.0 h
    mins = [OutageWindow(end + timedelta(hours=5, minutes=30),
                         end + timedelta(hours=9), inferred=False, basis="recorded")]
    assert "min" in pv.describe_boundary(run(body, outage_windows=mins)[0][0])


def test_window_edge_beats_outage_and_silence():
    """A boundary we never had the chance to observe must not be blamed on the vessel."""
    body = dwell()
    # window ends right after the run: the exit flank runs past it
    v = run(body, window_end=body[-1].ts + timedelta(minutes=5))[0][0]
    assert v.exit_missing_reason == pv.R_WINDOW_EDGE
    # ...and the same on the entry side
    v2 = run(body, window_start=body[0].ts - timedelta(minutes=5))[0][0]
    assert v2.entry_missing_reason == pv.R_WINDOW_EDGE


def test_describe_boundary_never_says_still_in_port():
    """The DRAUGHT-1 insufficient_data lesson, made concrete in the rendered phrasing."""
    body = dwell()
    end = body[-1].ts
    win = [OutageWindow(end, end + timedelta(hours=4), inferred=False, basis="recorded")]
    outage_v = run(body, outage_windows=win)[0][0]
    silent_v = run(body)[0][0]
    complete_v = run([fix(-30, inside=False, speed=6.0)] + body +
                     [fix(210, inside=False, speed=6.0)])[0][0]
    for v in (outage_v, silent_v, complete_v):
        text = pv.describe_boundary(v)
        assert "still in port" not in text.lower()
        assert text
    assert "feed down" in pv.describe_boundary(outage_v)
    assert "no departure observed" in pv.describe_boundary(silent_v)
    assert "vessel silent" in pv.describe_boundary(silent_v)
    assert pv.describe_boundary(complete_v) == "arrival and departure both observed"


# --------------------------------------------------------------- attribution + geometry
def test_multi_port_attribution_returns_all_candidates_never_a_first_pick():
    """15 AOI pairs sit within 6 km, so a fix can fall inside two radii. The session must
    report EVERY candidate port."""
    neighbour = {**PORT, "port_id": "wpi-neighbour", "port_name": "Neighbourport"}

    def containing(lat, lon):
        return [PORT, neighbour]                        # both radii cover the fix

    v = run(dwell(), containing_ports=containing)[0][0]
    assert set(v.candidate_ports) == {"wpi-test", "wpi-neighbour"}
    assert v.candidate_ports[0] == "wpi-test"           # the queried port leads
    # with no ambiguity resolver, only the queried port is claimed
    assert run(dwell())[0][0].candidate_ports == ("wpi-test",)


def test_every_session_carries_the_geometry_that_produced_it():
    """Every completeness figure is parameter-dependent (PORT-pre: complete moves
    20.9 % -> 54.9 % on core radius alone), so the geometry rides on the session."""
    v = run(dwell())[0][0]
    assert v.radius_m == 3500.0
    assert v.radius_basis == "assumed"                  # a search aperture, not evidence
    assert v.coord_precision == "arcminute"             # +/- ~1 km, no datum


def test_ambarli_rule_flags_geometry_not_traffic():
    """PORT-1's finding generalised: WPI harborSize can be stale and the radius is scaled
    from it, so a small radius at a busy port under-covers. Keyed on the measured ratio
    and the SIZE CLASS — never on a port name."""
    small = {**PORT, "harbor_size": "V"}
    # Ambarlı's shape: a handful inside, a crowd just outside
    assert pv.geometry_under_covers(small, 26, 1046) is True
    # a well-covered port of the same class is not flagged
    assert pv.geometry_under_covers(small, 100, 110) is False
    # thin traffic alone is not a geometry signal
    assert pv.geometry_under_covers(small, 0, 3) is False
    # a large-class port is out of scope for the rule
    assert pv.geometry_under_covers({**PORT, "harbor_size": "L"}, 26, 1046) is False


def test_reported_sog_is_corroboration_only_and_sentinel_filtered():
    """Speed decisions use position-derived implied_speed_kn; SOG is carried for
    corroboration with the 102.2 kn 'not available' sentinel removed."""
    fixes = [fix(i * 10, speed=0.1, sog=(pv.SOG_SENTINEL_KN if i % 2 else 0.2))
             for i in range(19)]
    v = run(fixes)[0][0]
    assert v.reported_sog_kn == 0.2                     # sentinel excluded from the mean
    # an all-sentinel run yields None rather than a fabricated 102.2
    all_sentinel = [fix(i * 10, speed=0.1, sog=pv.SOG_SENTINEL_KN) for i in range(19)]
    assert run(all_sentinel)[0][0].reported_sog_kn is None


def test_caveat_states_the_measured_limits():
    # case-insensitive: the caveat capitalises for emphasis, and the assertion is about
    # the claim being present, not about its casing
    c = pv.VISIT_CAVEAT.lower()
    for token in ("routine commerce", "search aperture", "lower bound",
                  "still in port", "not independent", "candidate_ports",
                  "a1", "radius and gap-split"):
        assert token in c, token


# --------------------------------------------------------------- determinism
def test_idempotent_and_pure():
    fixes = ([fix(-30, inside=False, speed=6.0)] + dwell()
             + [fix(45, quality="suspect")] + [fix(210, inside=False, speed=6.0)])
    fixes.sort(key=lambda f: f.ts)
    a = run(fixes)
    b = run(fixes)
    assert a == b                                        # frozen dataclasses compare equal
    assert a[0][0].completeness == pv.COMPLETE
    # the input sequence is not mutated
    assert len(fixes) == 22


@pytest.mark.parametrize("gap_h,expect", [(6, 1), (2, 2)])
def test_gap_split_is_a_reported_parameter_not_a_constant(gap_h, expect):
    """The split changes the answer (PORT-pre: complete moves 17.3 % -> 32.3 % across
    3 h -> 24 h), which is why the endpoint returns the value it used."""
    fixes = dwell() + dwell(start=180 + 3 * 60)          # a 3 h break between runs
    visits, _, _ = run(fixes, gap_s=gap_h * 3600.0)
    assert len(visits) == expect


def test_excursion_splits_the_call_and_is_not_counted_as_dwell():
    """REGRESSION (found while verifying figures for PORT-4). A vessel that leaves the
    radius and returns inside the gap-split window must be TWO calls, not one merged
    session.

    Splitting on time alone — filtering to in-radius fixes and then grouping — got all
    three of these wrong: duration spanned the 4 h excursion, the interval BRIDGING it was
    credited as low-speed dwell (a 8 kn transit counted as berthed, which can push a
    non-visit over the visit bar), and the excursion's fixes sat inside [start, end] where
    they could not flank the session, losing an observed arrival/departure pair."""
    fixes = ([fix(-30, speed=6.0, inside=False)]
             + dwell()                                            # 0..180 in, 3 h
             + [fix(180 + i * 30, speed=8.0, inside=False) for i in range(1, 8)]  # 4 h out
             + dwell(start=420)                                   # 420..600 in, 3 h
             + [fix(630, speed=6.0, inside=False)])
    visits, dropped, _ = run(fixes)
    assert len(visits) == 2, "an excursion outside the radius ends the call"
    for v in visits:
        assert v.duration_s == 10800.0                            # 3 h, NOT 10 h
        assert v.low_speed_span_s == 10800.0                      # the 8 kn leg excluded
        assert v.n_fixes == 19
        assert v.completeness == pv.COMPLETE                      # both boundaries visible
    # the excursion is a real break: the second call starts after the first ends
    assert visits[1].start_ts > visits[0].end_ts
    assert not dropped


def test_a_brief_boundary_crossing_is_dropped_by_the_bar_not_reported():
    """The flip side of splitting on excursions: fragments are not reported as visits, they
    fall to the visit bar. So splitting cannot invent short 'visits'."""
    fixes = []
    for block in range(4):                       # four 20-min touches, each too short
        base = block * 120
        fixes += [fix(base + i * 10) for i in range(3)]
        fixes += [fix(base + 40, speed=7.0, inside=False)]
    fixes.sort(key=lambda f: f.ts)
    visits, dropped, _ = run(fixes)
    assert visits == []
    assert sum(dropped.values()) == 4            # each fragment charged to a bar
    assert set(dropped) <= set(pv.FUNNEL_BARS)
