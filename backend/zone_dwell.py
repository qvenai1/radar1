"""Pure zone-dwell logic (ZONES-2) — two distinct signals, honest gating.

Annotation-only: nothing here writes to maritime_events or the composite (ZONES-3,
gated behind seeing the measured output first). Every function is pure (no I/O), so
the rules are unit-testable without a DB; the endpoint supplies the fixes, the masks,
and the per-vessel identity.

TWO SIGNALS, from ZONES-1's measurement that 18/29 platform 500 m cores are empty
while none of the 29 platforms is isolated — the safety zone is kept clear by law
(UNCLOS Art. 60(5)), which INVERTS the reading:

  * safety_zone_intrusion — a low-speed dwell inside a platform's 500 m radius. Rare
    by construction, therefore STRONG on its own. NOT full-type-gated: an offshore-
    support vessel working in a safety zone is plausible but still notable, and a
    cargo/tanker sitting there is a serious lead. Type is reported as CONTEXT, never
    used to suppress.
  * standoff_dwell — a dwell in the working band OUTSIDE the safety radius but near
    the structure (500 m .. STANDOFF_OUTER_M). Abundant and largely legitimate, so it
    carries the FULL gating stack (anchorage subtraction, duration, type demotion).

gas_field zones (5 km, assumed radius, a centroid not a structure) have no meaningful
safety/standoff split — a field dwell is a single, weaker class (field_dwell), gated
like standoff. Every result carries the asset's radius_basis + coord_precision so a
centroid-with-assumed-radius dwell is VISIBLY weaker evidence than a surveyed-platform
intrusion — the evidence-strength axis is explicit, not buried in a rank number.

SPEED: position-derived implied_speed_kn only (ZONES-pre: 99.5 % populated) — never
reported SOG, which is crew-entered (the nav_status/draught class the project
distrusts); SOG is carried as corroboration only, with the 102.2 kn sentinel filtered.
The caller applies the G3 rule (position_quality IS DISTINCT FROM 'implausible') in SQL.

DURATION is a LOWER BOUND under the 55 % feed outage — a gap truncates a dwell, never
fabricates one — flagged on every session (duration_is_lower_bound).

MEASURED LIMITS (ZONES-3; full text moves to MARITIME_INTELLIGENCE_BASE.md at ZONES-4):
  * Every dwell has legitimate explanations — offshore supply/standby work, drilling,
    fishing, waiting at anchor for a berth, survey, cable maintenance. A ranked LEAD
    surface, never an alarm.
  * gas_field zones are ASSUMED-radius reservoir CENTROIDS (GEM GOGET), not structures,
    ~20-35 km cross-source ambiguity — the weakest class; annotation-only, never composite-
    admitted.
  * One OSM node ("Tamar Gas Platform") is excluded as mis-sited (detectable:false, an
    upstream fixme ~100 km off); the other 28 of 29 platform nodes carry no accuracy
    warning. Real Tamar is covered by its GEM gas_field zone.
  * Duration is a lower bound under the 55 % feed outage.
  * The type-gate rests on SELF-REPORTED ship_type, so a false type declaration is a
    cloaking vector against THIS layer specifically. Measured exposure: 0 of 10 demoted
    sessions today — but the vector is STRUCTURAL; surfaced per session (cloaking_exposed)
    and counted, never silently trusted.
  * The Gulf/Iran theater and the submarine-cable and listed-port zone types are
    DOCUMENTED DEFERRALS (zones_aoi.json manifest), not coverage gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .geo_area import haversine_km
from .rendezvous import LOW_SUSPICION_TYPES

METHOD = "zone-dwell-v1"

# signal classes
SAFETY_ZONE_INTRUSION = "safety_zone_intrusion"
STANDOFF_DWELL = "standoff_dwell"
FIELD_DWELL = "field_dwell"

# thresholds
GAP_SPLIT_S = 1800.0          # 30-min break splits one dwell into two (ZONES-pre sessionization)
LOW_SPEED_KN = 2.0            # loitering vs transiting, on implied_speed_kn (position-derived)
STANDOFF_OUTER_M = 3000.0     # platform working band outer bound: ZONES-1 found 9 platforms with
                             # traffic within 3 km; 10 km sweeps in open sea. 500 m..3 km = standoff.
MIN_STANDOFF_DWELL_S = 3600.0  # a standoff/field dwell must be sustained (>=1h) to be a lead;
                             # a safety-zone intrusion has NO minimum (rare, strong on its own).
SOG_SENTINEL_KN = 102.2       # the G0 "not available" sentinel — filtered from SOG corroboration

# verdicts (nothing is silently dropped; the gated-out set stays queryable)
V_INTRUSION = "intrusion"          # safety_zone_intrusion — always a lead, type is context
V_LEAD = "lead"                    # survived the full gating stack
V_DEMOTED_TYPE = "demoted_type"    # self-reported low-suspicion type — RANKED DOWN, not deleted
V_EXCLUDED_ANCHORAGE = "excluded_anchorage"   # inside an empirical port/anchorage/busy cell
V_EXCLUDED_SHORT = "excluded_short"           # below the standoff duration threshold

# exclusion-accounting reason codes (Step 3)
R_TYPE_GATED = "type_gated"
R_IN_ANCHORAGE = "in_anchorage_mask"
R_BELOW_DURATION = "below_duration_threshold"

CANDIDATE_CAVEAT = (
    "Investigation lead, not proof: a vessel dwelt (low position-derived speed) at "
    "published offshore infrastructure. Every dwell has legitimate explanations — "
    "offshore supply/standby work, drilling, fishing, waiting at anchor for a berth, "
    "cable maintenance, survey. Zone geometry is a POINT+radius approximation with its "
    "own vintage (a gas_field is a reservoir centroid, not a structure). Duration is a "
    "LOWER BOUND under feed outage. AIS is spoofable and ship type is self-reported — "
    "a type-gate can be defeated by a false declaration (see cloaking exposure). This "
    "prioritises for review; it does not accuse."
)


@dataclass(frozen=True, slots=True)
class DwellSession:
    """One vessel's dwell at one zone — a gap-split, low-speed session. Carries the
    zone's evidence-strength axis (radius_basis/coord_precision) and the gating verdict.
    Nothing is deleted: demoted/excluded sessions are returned, just marked."""
    zone_id: str
    zone_name: str | None
    zone_type: str
    radius_basis: str
    coord_precision: str | None
    mmsi: str
    signal: str
    lat: float                         # closest-approach fix location (the event's position)
    lon: float
    start_ts: Any
    end_ts: Any
    duration_s: float
    duration_is_lower_bound: bool
    n_fixes: int
    min_distance_m: float
    mean_implied_speed_kn: float | None
    min_implied_speed_kn: float | None
    reported_sog_kn: float | None      # corroboration only, sentinel-filtered (may be None)
    ship_type: str | None              # self-reported (context/caveat)
    verdict: str
    exclusion_reasons: tuple[str, ...] = ()
    cloaking_exposed: bool = False     # demoted by a self-reported type that is unverifiable


def _clean_sog(reported: list[float | None]) -> float | None:
    """Mean reported SOG for corroboration, dropping None and the 102.2 kn sentinel."""
    vals = [s for s in reported if s is not None and abs(s - SOG_SENTINEL_KN) > 0.05]
    return round(sum(vals) / len(vals), 2) if vals else None


def zone_band(zone: dict[str, Any], distance_m: float) -> str | None:
    """The signal band a fix at ``distance_m`` from the zone centre falls in, or None
    (outside the zone's outer band). Platforms split into safety-zone / standoff;
    gas_fields are a single field band."""
    r = zone["radius_m"]
    if zone["zone_type"] == "platform":
        if distance_m <= r:
            return SAFETY_ZONE_INTRUSION
        if distance_m <= STANDOFF_OUTER_M:
            return STANDOFF_DWELL
        return None
    # gas_field (or any non-platform): single band inside the (assumed) radius
    return FIELD_DWELL if distance_m <= r else None


def _sessionize(fixes: list[tuple], gap_s: float) -> list[list[tuple]]:
    """Split ts-ascending fixes into runs broken by a > gap_s silence (mirrors the
    gap_detect grouping idiom). Each returned run is one dwell session."""
    runs: list[list[tuple]] = []
    run: list[tuple] = []
    for f in fixes:
        if run and (f[0] - run[-1][0]).total_seconds() > gap_s:
            runs.append(run)
            run = []
        run.append(f)
    if run:
        runs.append(run)
    return runs


def zone_dwell_sessions(
    zone: dict[str, Any],
    mmsi: str,
    fixes: list[tuple],                         # (ts, lat, lon, implied_speed_kn, reported_sog) for ONE vessel, ts-asc
    *,
    ship_type: str | None,
    type_unverifiable: bool,
    in_anchorage: Callable[[float, float], bool],
    low_speed_kn: float = LOW_SPEED_KN,
    gap_s: float = GAP_SPLIT_S,
    min_standoff_dwell_s: float = MIN_STANDOFF_DWELL_S,
) -> list[DwellSession]:
    """Dwell sessions for ONE vessel at ONE zone. Pure. The caller passes G3-filtered,
    ts-ascending fixes (position_quality IS DISTINCT FROM 'implausible' applied in SQL),
    the vessel's self-reported ship_type, whether that type is unverifiable (cloaking
    axis), and an anchorage predicate built from the A2 port/busy masks.

    A session is emitted only when it is a DWELL (mean implied speed <= low_speed_kn) —
    a fast transit through a zone is not a dwell. Signal class comes from the closest
    approach; gating follows Step 3."""
    # keep only in-band fixes, tagged with distance to the zone centre
    tagged: list[tuple] = []          # (ts, lat, lon, implied, sog, dist_m)
    for ts, lat, lon, implied, sog in fixes:
        d_m = haversine_km(lat, lon, zone["lat"], zone["lon"]) * 1000.0
        if zone_band(zone, d_m) is not None:
            tagged.append((ts, lat, lon, implied, sog, d_m))
    if not tagged:
        return []

    out: list[DwellSession] = []
    for run in _sessionize(tagged, gap_s):
        implied_vals = [r[3] for r in run if r[3] is not None]
        mean_impl = (sum(implied_vals) / len(implied_vals)) if implied_vals else None
        # a dwell requires low movement; treat missing implied speed as NOT low-speed
        # (do not manufacture a dwell from absent evidence)
        if mean_impl is None or mean_impl > low_speed_kn:
            continue
        min_impl = min(implied_vals)
        min_d = min(r[5] for r in run)
        closest = min(run, key=lambda r: r[5])
        signal = zone_band(zone, min_d)             # strongest band = closest approach
        if signal is None:
            # outside every band — cannot happen for fixes selected within the zone
            # radius; if it ever does, no band = no signal = no session (fail closed).
            continue
        duration_s = (run[-1][0] - run[0][0]).total_seconds()

        verdict, reasons, cloaked = _gate(
            signal=signal, duration_s=duration_s, ship_type=ship_type,
            type_unverifiable=type_unverifiable,
            in_anchorage=in_anchorage(closest[1], closest[2]),
            min_standoff_dwell_s=min_standoff_dwell_s,
        )
        out.append(DwellSession(
            zone_id=zone["zone_id"], zone_name=zone.get("zone_name"),
            zone_type=zone["zone_type"], radius_basis=zone["radius_basis"],
            coord_precision=zone.get("coord_precision"),
            mmsi=mmsi,
            signal=signal, lat=round(closest[1], 6), lon=round(closest[2], 6),
            start_ts=run[0][0], end_ts=run[-1][0],
            duration_s=round(duration_s, 1), duration_is_lower_bound=True,
            n_fixes=len(run), min_distance_m=round(min_d, 1),
            mean_implied_speed_kn=round(mean_impl, 2), min_implied_speed_kn=round(min_impl, 2),
            reported_sog_kn=_clean_sog([r[4] for r in run]),
            ship_type=ship_type, verdict=verdict, exclusion_reasons=reasons,
            cloaking_exposed=cloaked,
        ))
    return out


def admit_for_composite(session: DwellSession, *, baseline_sound: bool, is_familiar: bool) -> bool:
    """ZONES-3 admission policy — which sessions earn a maritime_events candidate row
    (and thus composite admission). Pure; the runner supplies the baseline facts.

      * safety_zone_intrusion — ALWAYS (rare, strong, surveyed platform point).
      * standoff_dwell — only a gating-survivor (verdict 'lead') that is UNFAMILIAR to
        its zone, AND only where that zone's familiarity baseline is data-sound. When the
        baseline is under-determined, admit none (deferred; stays annotation-only).
      * field_dwell, and every demoted/excluded session — NEVER (weakest class / gated).
    """
    if session.signal == SAFETY_ZONE_INTRUSION:
        return True
    if session.signal == STANDOFF_DWELL and session.verdict == V_LEAD:
        return baseline_sound and not is_familiar
    return False


def _gate(*, signal: str, duration_s: float, ship_type: str | None,
          type_unverifiable: bool, in_anchorage: bool,
          min_standoff_dwell_s: float) -> tuple[str, tuple[str, ...], bool]:
    """Step 3 gating. safety_zone_intrusion is never gated (type is context only).
    standoff/field: subtract anchorages, exclude short, DEMOTE (not delete) a
    self-reported low-suspicion type; flag cloaking exposure when that type is
    unverifiable."""
    if signal == SAFETY_ZONE_INTRUSION:
        return V_INTRUSION, (), False
    if in_anchorage:
        return V_EXCLUDED_ANCHORAGE, (R_IN_ANCHORAGE,), False
    if duration_s < min_standoff_dwell_s:
        return V_EXCLUDED_SHORT, (R_BELOW_DURATION,), False
    if ship_type in LOW_SUSPICION_TYPES:
        return V_DEMOTED_TYPE, (R_TYPE_GATED,), bool(type_unverifiable)
    return V_LEAD, (), False
