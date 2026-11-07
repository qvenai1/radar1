"""ZONES-2 — pure zone-dwell logic (two signals, gating, cloaking). No DB, no events.

Synthetic fixes exercise the rules the endpoint relies on: the safety-zone/standoff/
field bands, gap-split sessionization, low-speed dwell (transit excluded), the Step-3
gating stack (anchorage subtraction, duration, type DEMOTION not deletion), and the
cloaking-exposure flag. Every function under test is pure.
"""

import math
from datetime import datetime, timedelta, timezone

from backend import zone_dwell as zd

T0 = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
PLATFORM = {"zone_id": "p1", "zone_name": "P1", "zone_type": "platform", "lat": 33.0,
            "lon": 34.0, "radius_m": 500.0, "radius_basis": "sourced", "coord_precision": "surveyed"}
FIELD = {"zone_id": "f1", "zone_name": "F1", "zone_type": "gas_field", "lat": 33.0,
         "lon": 34.0, "radius_m": 5000.0, "radius_basis": "assumed", "coord_precision": "exact"}
NO_ANCHORAGE = lambda la, lo: False          # noqa: E731
ANCHORAGE = lambda la, lo: True              # noqa: E731


def _east(zone, meters):
    """A lon east of the zone centre placing a fix ~meters away (small-angle)."""
    return zone["lon"] + meters / (111320.0 * math.cos(math.radians(zone["lat"])))


def _fixes(zone, meters, n, dt_s, speed, sog=None, start=T0):
    return [(start + timedelta(seconds=i * dt_s), zone["lat"], _east(zone, meters), speed, sog)
            for i in range(n)]


def test_zone_band_splits_platform_and_field():
    assert zd.zone_band(PLATFORM, 400) == zd.SAFETY_ZONE_INTRUSION
    assert zd.zone_band(PLATFORM, 1500) == zd.STANDOFF_DWELL
    assert zd.zone_band(PLATFORM, 4000) is None            # beyond the 3 km standoff bound
    assert zd.zone_band(FIELD, 400) == zd.FIELD_DWELL
    assert zd.zone_band(FIELD, 4000) == zd.FIELD_DWELL
    assert zd.zone_band(FIELD, 6000) is None


def test_sessionize_splits_on_gap():
    a = _fixes(PLATFORM, 100, 3, 300, 0.1)                 # 3 fixes 5 min apart
    b = _fixes(PLATFORM, 100, 2, 300, 0.1, start=T0 + timedelta(hours=2))  # after a 2h gap
    runs = zd._sessionize(a + b, zd.GAP_SPLIT_S)
    assert [len(r) for r in runs] == [3, 2]


def test_safety_zone_intrusion_is_strong_and_not_type_suppressed():
    # a tug (LOW_SUSPICION) dwelling inside the 500 m core is STILL an intrusion —
    # type is context, never used to suppress the safety-zone signal
    fixes = _fixes(PLATFORM, 300, 6, 300, 0.2)             # 25 min low-speed inside 500 m
    s = zd.zone_dwell_sessions(PLATFORM, "111", fixes, ship_type="tug",
                               type_unverifiable=True, in_anchorage=NO_ANCHORAGE)
    assert len(s) == 1
    assert s[0].signal == zd.SAFETY_ZONE_INTRUSION
    assert s[0].verdict == zd.V_INTRUSION and s[0].exclusion_reasons == ()
    assert s[0].cloaking_exposed is False                  # intrusion isn't type-gated
    assert s[0].radius_basis == "sourced" and s[0].duration_is_lower_bound is True


def test_standoff_lead_vs_demoted_vs_excluded():
    # long (>1h) low-speed dwell at ~800 m: a cargo → LEAD
    long_fixes = _fixes(PLATFORM, 800, 20, 300, 0.3)       # 20 fixes * 5 min = 95 min
    lead = zd.zone_dwell_sessions(PLATFORM, "1", long_fixes, ship_type="cargo",
                                  type_unverifiable=False, in_anchorage=NO_ANCHORAGE)[0]
    assert lead.signal == zd.STANDOFF_DWELL and lead.verdict == zd.V_LEAD

    # same, but a tug → DEMOTED (not deleted), and cloaking-exposed if unverifiable
    dem = zd.zone_dwell_sessions(PLATFORM, "2", long_fixes, ship_type="tug",
                                 type_unverifiable=True, in_anchorage=NO_ANCHORAGE)[0]
    assert dem.verdict == zd.V_DEMOTED_TYPE and zd.R_TYPE_GATED in dem.exclusion_reasons
    assert dem.cloaking_exposed is True

    # same tug but verifiable identity → demoted, NOT cloaking-exposed
    dem2 = zd.zone_dwell_sessions(PLATFORM, "3", long_fixes, ship_type="tug",
                                  type_unverifiable=False, in_anchorage=NO_ANCHORAGE)[0]
    assert dem2.verdict == zd.V_DEMOTED_TYPE and dem2.cloaking_exposed is False

    # in an anchorage cell → excluded (routine subtracted), still returned
    anch = zd.zone_dwell_sessions(PLATFORM, "4", long_fixes, ship_type="cargo",
                                  type_unverifiable=False, in_anchorage=ANCHORAGE)[0]
    assert anch.verdict == zd.V_EXCLUDED_ANCHORAGE and zd.R_IN_ANCHORAGE in anch.exclusion_reasons

    # short (<1h) standoff → excluded_short
    short = _fixes(PLATFORM, 800, 6, 300, 0.3)             # 25 min
    exs = zd.zone_dwell_sessions(PLATFORM, "5", short, ship_type="cargo",
                                 type_unverifiable=False, in_anchorage=NO_ANCHORAGE)[0]
    assert exs.verdict == zd.V_EXCLUDED_SHORT and zd.R_BELOW_DURATION in exs.exclusion_reasons


def test_transit_is_not_a_dwell():
    # fast pass-through (12 kn) inside the band → NOT emitted (dwell needs low speed)
    fast = _fixes(PLATFORM, 300, 6, 60, 12.0)
    assert zd.zone_dwell_sessions(PLATFORM, "1", fast, ship_type="cargo",
                                  type_unverifiable=False, in_anchorage=NO_ANCHORAGE) == []


def test_field_dwell_is_single_weaker_class_and_gated():
    fixes = _fixes(FIELD, 2000, 20, 300, 0.3)              # ~95 min low-speed, 2 km from centroid
    s = zd.zone_dwell_sessions(FIELD, "1", fixes, ship_type="cargo",
                               type_unverifiable=False, in_anchorage=NO_ANCHORAGE)[0]
    assert s.signal == zd.FIELD_DWELL and s.verdict == zd.V_LEAD
    assert s.radius_basis == "assumed" and s.coord_precision == "exact"   # evidence-strength axis


def _session(signal, verdict):
    return zd.DwellSession(
        zone_id="z", zone_name=None, zone_type="platform", radius_basis="sourced",
        coord_precision="surveyed", mmsi="1", signal=signal, lat=0.0, lon=0.0,
        start_ts=T0, end_ts=T0, duration_s=3600.0, duration_is_lower_bound=True,
        n_fixes=5, min_distance_m=100.0, mean_implied_speed_kn=0.5,
        min_implied_speed_kn=0.1, reported_sog_kn=None, ship_type="cargo", verdict=verdict)


def test_admit_for_composite_policy():
    # safety_zone_intrusion is ALWAYS admitted (rare, strong), even with no baseline
    intr = _session(zd.SAFETY_ZONE_INTRUSION, zd.V_INTRUSION)
    assert zd.admit_for_composite(intr, baseline_sound=False, is_familiar=True)
    # field_dwell is NEVER admitted (weakest class)
    assert not zd.admit_for_composite(_session(zd.FIELD_DWELL, zd.V_LEAD),
                                      baseline_sound=True, is_familiar=False)
    # standoff LEAD: only when the baseline is sound AND the session is unfamiliar
    lead = _session(zd.STANDOFF_DWELL, zd.V_LEAD)
    assert not zd.admit_for_composite(lead, baseline_sound=False, is_familiar=False)  # under-determined
    assert not zd.admit_for_composite(lead, baseline_sound=True, is_familiar=True)    # familiar
    assert zd.admit_for_composite(lead, baseline_sound=True, is_familiar=False)       # sound + unfamiliar
    # a DEMOTED / EXCLUDED standoff is never admitted (gated-out stays annotation-only)
    assert not zd.admit_for_composite(_session(zd.STANDOFF_DWELL, zd.V_DEMOTED_TYPE),
                                      baseline_sound=True, is_familiar=False)


def test_sog_sentinel_filtered_from_corroboration():
    fixes = _fixes(PLATFORM, 300, 6, 300, 0.2, sog=zd.SOG_SENTINEL_KN)    # all SOG = sentinel
    s = zd.zone_dwell_sessions(PLATFORM, "1", fixes, ship_type="cargo",
                               type_unverifiable=False, in_anchorage=NO_ANCHORAGE)[0]
    assert s.reported_sog_kn is None                       # sentinel removed → no corroboration
