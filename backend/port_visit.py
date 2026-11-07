"""Pure port-visit sessionization (PORT-2) — measurement and context, annotation-only.

Nothing here writes to ``maritime_events``, the composite, or the alerts feed, and
that is a MEASURED decision rather than a phase gate (see "NOT BUILT" below). Every
function is pure (no I/O), so the rules are unit-testable without a DB; the endpoint
supplies the fixes, the outage windows, and the port gazetteer entry.

WHAT THIS LAYER IS FOR. The port is **negative space** everywhere else in this base:
A2's ``port_mask``/``busy_mask`` subtract it, the zones layer subtracts anchorage
cells, M1 classifies territorial sea, and A1 down-classifies a berthed vessel's
silence to ``stationary``. Nothing emits a positive "vessel X was in port Y from t1
to t2". This layer converts a signal **A1 already discards as a confound** into a
positive measured fact. It is a measurement-and-context surface — "where was this
hull" on an existing lead — NOT a lead generator.

THE A1 RECONCILIATION (PORT-pre's sharpest finding; folded in here as PORT-3).
A berthed vessel's silence and a port visit are **ONE SILENCE SEEN TWICE**. Measured:
**55.8 %** of port sessions temporally overlap an A1 gap for the same vessel (2,849
``stationary`` + 2,400 ``outage-excluded`` + 411 ``candidate``), and 1,105 of 1,179
vessels with a port session also carry an A1 gap. From A1's side, of gaps starting
within 15 km of a port, **59.7 % are ``stationary``** and only 5.5 % ``candidate``.
So a port visit and an A1 gap on the same hull are **NOT INDEPENDENT OBSERVATIONS**
— a study that counts both as corroborating evidence is double-counting one physical
event. ``test_port_visit_a1_overlap_is_expected`` asserts the overlap so this cannot
be quietly forgotten.

THE VISIT BAR (PORT-pre's measured funnel — 61 % of raw circle-crossings are
transits with a p50 of 0.37 h, so a presence test alone measures traffic, not visits):

    raw crossings           4,907  100.0 %   any presence inside the radius
    >= 2 fixes              3,753   76.5 %
    duration >= 1 h         1,910   38.9 %
    low-speed span >= 1 h   1,795   36.6 %
    low-speed span >= 2 h   1,542   31.4 %
    + majority-low          1,464   29.8 %   <- THE VISIT BAR implemented here
    + entry AND exit seen      289    5.9 %   (a completeness state, NOT a bar)

A **visit** is a gap-split session with >= 2 fixes, duration >= 1 h, a contiguous
low-speed span >= 2 h, and a MAJORITY of speed-bearing fixes below the low-speed
threshold. That population's dwell distribution is credible (p25 4.1 h, p50 8.5 h,
p75 18.7 h, p90 37.4 h) where the raw population's is not. Everything dropped is
reported as exclusion accounting, never silently.

Boundary observation is a **completeness state, not a bar** — gating on "entry and
exit seen" would discard 80 % of real visits for a coverage reason.

SPEED: position-derived ``implied_speed_kn`` only — never reported SOG and never
``nav_status``, both crew-entered (the project's distrusted-field discipline). SOG is
carried as corroboration only, with the 102.2 kn "not available" sentinel filtered.

QUALITY FILTER — deliberately STRICTER than the standard G3 consumption rule, and
the exact form matters. G3 elsewhere drops only ``implausible`` and passes
``suspect``. Here suspect is dropped too, because **a harbour radius is exactly where
on-land decoy positions land**: PORT-pre measured 79 suspect-tier fixes across 38
sessions, concentrated at Haifa (26 of them flagged ``on_land`` INSIDE a 3 km harbour
core), in a theater where 46.2 % of ``jamming-excluded`` gaps start within 15 km of a
port. But the filter must pass **NULL**: ``position_quality`` is written by a timer
that runs every 15 minutes, so the newest fixes are ALWAYS NULL and mean "not yet
checked", not "bad". Filtering NULL would systematically truncate in-progress
sessions and manufacture false "no departure observed" — so the rule is
``quality NOT IN ('suspect','implausible')`` with NULL PASSING, i.e. in SQL
``position_quality IS DISTINCT FROM 'suspect' AND IS DISTINCT FROM 'implausible'``.
:func:`accept_fix` is the single definition of that rule and the SQL mirrors it; the
dropped count is reported per session and per port so the tightening stays visible.

FOUR FIRST-CLASS COMPLETENESS STATES. At PORT-pre's measured **19.7 % complete**, the
incomplete states are the MAJORITY of the output — the surface is designed around
that rather than apologising for it:

  * ``complete``      — entry and exit both observed;
  * ``open_ended``    — entry observed, no exit;
  * ``truncated``     — exit observed, no entry;
  * ``presence_only`` — neither boundary observed.

Each missing side carries WHY (``outage`` / ``vessel_silence`` / ``window_edge``);
PORT-pre measured ~51 % of missing entries and ~45 % of missing exits coinciding with
a wholly dead feed hour, so outage attribution reuses
:func:`backend.gap_detect.overlapping_window` — the G3-addendum machinery, imported
never reimplemented. Render "no exit observed — feed down N h", NEVER "still in port"
(the DRAUGHT-1 ``insufficient_data`` lesson).

Every session carries ``duration_is_lower_bound``; the two ENTRY-missing states
additionally carry ``arrival_ts_is_unknown``, because a lower bound on a session with
no observed arrival is a lower bound on an **unknown quantity** — a strictly weaker
claim that must not read as the same flag.

ATTRIBUTION AMBIGUITY IS DATA. 15 AOI port pairs sit within 6 km (tightest
Gebze <-> Kaba Burnu at 1.40 km), so a fix can fall inside more than one port radius.
``candidate_ports`` lists EVERY containing port — never an arbitrary first pick — so
a consumer can say "attributable to either X or Y".

MEASURED LIMITS (carried to MARITIME_INTELLIGENCE_BASE §2.11 at PORT-4):
  * Coordinates are **degrees-and-minutes by publication design** (NGA Pub 150 §3,
    ±~1 km at 33°N), with NO datum stated.
  * ``radius_basis`` is ``'assumed'`` on EVERY feature — a **search aperture, not
    evidence**. No source publishes a measured harbour extent (weaker than the zones
    layer, where 29/36 radii were ``'sourced'``). Extents are unmeasured.
  * For the 108 smallest harbours the positional uncertainty is **comparable to the
    radius itself**, so every session carries the radius and precision that produced it.
  * WPI ``harborSize`` can be STALE and the radius is scaled from it — see
    :func:`geometry_under_covers` (the Ambarlı rule).
  * The gazetteer is thin where it matters most: **Baniyas** is an
    ``amenity=ferry_terminal`` node with its offshore SPM terminal absent from OSM
    entirely; **Sayda**'s WPI point sits **7.3 km** from the OSM harbour polygon.
  * Visits are **routine commerce**: cargo 29 % · passenger 15 % · pleasure_craft
    14 % · tug 12 % · tanker 8.5 % — ~45 % ferry/leisure/port-service.
  * A berthed vessel's silence is EXPECTED and A1 already classifies it (above).
  * **43 of 180** ports produced any session over 16 days. A silent port is a
    COVERAGE fact, never "no commerce".
  * EVERY completeness figure is PARAMETER-DEPENDENT — PORT-pre measured ``complete``
    moving 20.9 % -> 54.9 % on core radius alone — so a completeness number is
    meaningless without the geometry that produced it, and the surface returns them
    together.

NOT BUILT, on measured grounds (PORT-pre), and none of it is a phase gate:
  * no stored ``event_type='port_visit'`` layer and no migration — 1,464 visits per
    16 days over 32 observed ports is ~3x the entire current non-gap
    ``maritime_events`` population, for pure commerce;
  * no composite admission — even the tightest bar flips 96 hulls from 1 to 2
    ``n_pattern_types`` and grows the composite 9 % (all raw sessions: 68 %), and
    ``composite_rank`` promotes ``n_pattern_types == 2`` to HIGH on a grey flag, so it
    would manufacture HIGH-rank hulls out of a cargo ship calling at Haifa;
  * no alerts-feed surface, and no ``suspicion_rank`` — there is nothing to rank;
  * no gap-toward-a-port signal — measured ANTI-correlated (8.2 % of A1 candidate
    gaps start within 15 km of a port against a 19.19 % null baseline; course
    consistency 26.0 % against 25.0 % chance);
  * no per-vessel "unusual port" baseline — the ZONES-4 readiness bars need >= 30 days
    and the feed span is ~16.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .gap_detect import OutageWindow, overlapping_window
from .geo_area import haversine_km

METHOD = "port-visit-v1"

# ---------------------------------------------------------------- thresholds
#: A break longer than this splits one presence into two sessions. Deliberately far
#: wider than zone_dwell's 30 min: a BERTHED vessel legitimately reports sparsely (or
#: stops), so a short split would shatter one port call into many. PORT-pre measured
#: the completeness split moving 17.3 % -> 32.3 % complete across a 3 h -> 24 h split,
#: which is exactly why the value used is returned with every response.
GAP_SPLIT_S = 6 * 3600.0

#: Berthed/anchored, on POSITION-DERIVED implied_speed_kn (never reported SOG).
LOW_SPEED_KN = 1.0

#: The visit bar (PORT-pre's funnel): >= 2 fixes, >= 1 h present, >= 2 h of
#: contiguous low-speed span, and a majority of speed-bearing fixes low.
MIN_FIXES = 2
MIN_DURATION_S = 3600.0
MIN_LOW_SPEED_SPAN_S = 2 * 3600.0
MAJORITY_LOW_FRAC = 0.5

#: An entry/exit is "observed" only if a fix OUTSIDE the radius flanks the session
#: within this long. Beyond it the boundary is unobserved, not inferred.
FLANK_S = 6 * 3600.0

#: Query-box padding beyond the radius, so flanking (entry/exit) fixes exist at all.
FLANK_PAD_KM = 10.0

#: The G0 "not available" SOG sentinel — filtered from corroboration.
SOG_SENTINEL_KN = 102.2

#: Ambarlı rule (:func:`geometry_under_covers`): vessels seen in the padded box but
#: OUTSIDE the radius must exceed the inside count by this factor, with at least this
#: many outside, before the small radius is called a geometry signal.
UNDER_COVER_RATIO = 5.0
UNDER_COVER_MIN_OUTSIDE = 20
UNDER_COVER_SIZES = ("V", "S")

# completeness states
COMPLETE = "complete"
OPEN_ENDED = "open_ended"          # entry observed, no exit
TRUNCATED = "truncated"            # exit observed, no entry
PRESENCE_ONLY = "presence_only"    # neither boundary observed
#: The two states with NO observed arrival — these carry arrival_ts_is_unknown.
ENTRY_MISSING_STATES = (TRUNCATED, PRESENCE_ONLY)

# missing-boundary reasons
R_OUTAGE = "outage"
R_VESSEL_SILENCE = "vessel_silence"
R_WINDOW_EDGE = "window_edge"

# funnel bars (exclusion accounting — ordered; a crossing is charged to the FIRST it fails)
B_SINGLE_FIX = "single_fix"
B_BELOW_DURATION = "below_min_duration"
B_BELOW_LOW_SPEED_SPAN = "below_low_speed_span"
B_NOT_MAJORITY_LOW = "not_majority_low"
FUNNEL_BARS = (B_SINGLE_FIX, B_BELOW_DURATION, B_BELOW_LOW_SPEED_SPAN, B_NOT_MAJORITY_LOW)

#: Quality tiers this layer refuses. NULL is NOT here — it means "not yet checked"
#: (the 15-min timer), and dropping it would truncate in-progress sessions.
REJECTED_QUALITY = ("suspect", "implausible")

VISIT_CAVEAT = (
    "Measurement and context, not a lead: a port visit is ROUTINE COMMERCE (measured mix "
    "cargo 29 % / passenger 15 % / pleasure 14 % / tug 12 % / tanker 8.5 %). This layer "
    "answers 'where was this hull', it does not accuse and it feeds no alert or composite. "
    "Port geometry is a POINT + ASSUMED radius -- a search aperture, not evidence; no source "
    "publishes a measured harbour extent, and WPI coordinates are degrees-and-minutes "
    "(+/- ~1 km, no datum). Duration is a LOWER BOUND under feed outage, and where no "
    "arrival was observed it is a lower bound on an UNKNOWN quantity. Only ~1 visit in 5 has "
    "both boundaries observed -- 'no exit observed' NEVER means 'still in port'. A vessel's "
    "silence while berthed is EXPECTED and A1 already classifies it as a stationary gap, so "
    "a port visit and an A1 gap on the same hull are NOT independent evidence. Ports within "
    "6 km cannot be cleanly separated (see candidate_ports). Every completeness figure "
    "depends on the radius and gap-split returned beside it."
)


# ---------------------------------------------------------------- fix quality
def accept_fix(position_quality: Any) -> bool:
    """The single definition of this layer's quality rule (the SQL mirrors it).

    Accepts ``plausible`` **and NULL**; rejects ``suspect`` and ``implausible``.

    Stricter than the standard G3 consumption rule (which passes ``suspect``) because a
    harbour radius is where on-land decoy positions land. NULL passes deliberately: it
    means "not yet scored by the 15-min quality timer", so the newest fixes are always
    NULL and rejecting them would truncate every in-progress session."""
    return position_quality not in REJECTED_QUALITY


# ---------------------------------------------------------------- data classes
@dataclass(frozen=True, slots=True)
class PortFix:
    """One position report in a port's padded vicinity box. ``position_quality`` may be
    None (not yet scored) — see :func:`accept_fix`."""
    ts: Any
    lat: float
    lon: float
    implied_speed_kn: float | None = None
    reported_sog_kn: float | None = None
    position_quality: str | None = None


@dataclass(frozen=True, slots=True)
class PortVisit:
    """One vessel's visit to one port — a gap-split session that cleared the visit bar.

    Carries the geometry that produced it (``radius_m``, ``radius_basis``,
    ``coord_precision``) because every completeness figure is parameter-dependent, and
    ``candidate_ports`` because 15 AOI pairs cannot be cleanly separated."""
    port_id: str
    port_name: str
    mmsi: str
    start_ts: Any
    end_ts: Any
    duration_s: float
    low_speed_span_s: float
    n_fixes: int
    n_low_speed_fixes: int
    frac_low_speed: float
    min_distance_m: float
    mean_implied_speed_kn: float | None
    min_implied_speed_kn: float | None
    reported_sog_kn: float | None            # corroboration only, sentinel-filtered
    lat: float                               # closest-approach fix location
    lon: float
    # completeness — the majority of the output, not an edge case
    completeness: str
    entry_ts: Any = None
    exit_ts: Any = None
    entry_missing_reason: str | None = None
    exit_missing_reason: str | None = None
    # feed-down seconds behind EACH missing boundary, kept SEPARATE: one shared field
    # would let an exit-side outage render behind a "vessel silent" arrival note.
    entry_outage_gap_s: float | None = None
    exit_outage_gap_s: float | None = None
    duration_is_lower_bound: bool = True
    arrival_ts_is_unknown: bool = False
    # geometry honesty
    radius_m: float = 0.0
    radius_basis: str = "assumed"
    coord_precision: str | None = None
    candidate_ports: tuple[str, ...] = ()    # EVERY port whose radius contains a fix
    # quality accounting
    dropped_fixes: int = 0                   # suspect/implausible inside the session span


@dataclass(slots=True)
class PortFunnel:
    """Per-port exclusion accounting — the DRAUGHT-1 lesson: distinguish "excluded by a
    bar" from "nothing observed at all", and never report a survivor count alone."""
    port_id: str
    port_name: str
    raw_crossings: int = 0
    visits: int = 0
    dropped_at: dict[str, int] = field(default_factory=dict)
    dropped_fixes_quality: int = 0
    vessels_in_radius: int = 0
    vessels_outside_radius: int = 0
    no_presence_observed: bool = False
    geometry_under_covers: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "port_id": self.port_id, "port_name": self.port_name,
            "raw_crossings": self.raw_crossings, "visits": self.visits,
            "dropped_at": {b: self.dropped_at.get(b, 0) for b in FUNNEL_BARS},
            "dropped_fixes_quality": self.dropped_fixes_quality,
            "vessels_in_radius": self.vessels_in_radius,
            "vessels_outside_radius": self.vessels_outside_radius,
            "no_presence_observed": self.no_presence_observed,
            "geometry_under_covers": self.geometry_under_covers,
        }


# ---------------------------------------------------------------- helpers
def _clean_sog(reported: Sequence[float | None]) -> float | None:
    """Mean reported SOG for CORROBORATION only, dropping None and the 102.2 sentinel."""
    vals = [s for s in reported if s is not None and abs(s - SOG_SENTINEL_KN) > 0.05]
    return round(sum(vals) / len(vals), 2) if vals else None


def _split(tagged: list[tuple], gap_s: float) -> list[list[tuple]]:
    """Split the ts-ascending FULL fix stream into runs of consecutive IN-RADIUS fixes.

    A run ends at either boundary condition, and both matter:

      * a ``> gap_s`` silence — the gap_detect / zone_dwell grouping idiom; and
      * an OUT-of-radius fix — the vessel demonstrably left.

    Breaking on the excursion is not cosmetic. Splitting on time alone (i.e. filtering to
    in-radius fixes first and then grouping) merges a depart-and-return into one session
    and then reports it wrongly in three ways: ``duration_s`` spans the excursion and
    overstates time in port; the interval BRIDGING the excursion is credited to
    ``low_speed_span_s`` whenever the returning fix is slow, so a fast transit outside the
    radius is counted as dwell and can push a non-visit over the visit bar; and the
    excursion's own fixes then sit INSIDE ``[start, end]`` where they cannot flank it, so
    an observed arrival/departure pair is lost and ``complete`` is under-reported.
    Measured on the live surface, that last effect alone halved ``complete`` (12.4 % vs
    18.7 % over the same six WPI ports).

    Fragmentation from positional noise is not a concern in practice: a berthed vessel is
    stationary, so it does not re-cross the boundary, and any short fragment a real
    manoeuvre produces is dropped by the visit bar (duration >= 1 h, low-speed span
    >= 2 h) rather than reported."""
    runs: list[list[tuple]] = []
    run: list[tuple] = []
    for f in tagged:
        if not f[6]:                                   # outside the radius: presence ended
            if run:
                runs.append(run)
                run = []
            continue
        if run and (f[0] - run[-1][0]).total_seconds() > gap_s:
            runs.append(run)
            run = []
        run.append(f)
    if run:
        runs.append(run)
    return runs


def _low_speed_span_s(run: list[tuple], low_speed_kn: float) -> float:
    """Contiguous seconds the vessel spent below ``low_speed_kn``, summed over the run.

    An interval counts only when its LATER fix is low-speed (implied_speed_kn is the
    speed derived arriving AT that fix), and a None implied speed never counts — absent
    evidence must not manufacture dwell."""
    total = 0.0
    for a, b in zip(run, run[1:], strict=False):  # pairwise — lengths differ by design
        s = b[3]
        if s is not None and s < low_speed_kn:
            total += (b[0] - a[0]).total_seconds()
    return total


def _boundary_reason(
    edge_ts: Any,
    *,
    looking_back: bool,
    window_start: Any,
    window_end: Any,
    outage_windows: list[OutageWindow],
    flank_s: float,
) -> tuple[str, float | None]:
    """Why a boundary was not observed: ``window_edge`` (the flank interval runs past the
    queried window — we never had the chance to see it), ``outage`` (a feed-outage window
    intersects the flank interval, with the overlap seconds), else ``vessel_silence``.

    window_edge is checked FIRST: a boundary we could not have observed must not be
    blamed on the vessel or on an outage that merely happens to be nearby."""
    from datetime import timedelta

    if looking_back:
        lo, hi = edge_ts - timedelta(seconds=flank_s), edge_ts
        if lo < window_start:
            return R_WINDOW_EDGE, None
    else:
        lo, hi = edge_ts, edge_ts + timedelta(seconds=flank_s)
        if hi > window_end:
            return R_WINDOW_EDGE, None
    w = overlapping_window(lo, hi, outage_windows)
    if w is not None:
        overlap = (min(hi, w.end) - max(lo, w.start)).total_seconds()
        return R_OUTAGE, round(max(overlap, 0.0), 1)
    return R_VESSEL_SILENCE, None


def geometry_under_covers(
    port: dict[str, Any], vessels_in_radius: int, vessels_outside_radius: int,
) -> bool:
    """The **Ambarlı rule** (PORT-1's measured finding, generalised — measurement, not an
    allowlist).

    WPI ``harborSize`` can be stale and the radius is scaled from it, so a small radius at
    a genuinely busy port UNDER-COVERS it: WPI rates Ambarlı ``'V'`` (very small, the
    1.5 km floor) though it sits in the collector's densest 1° cell — PORT-pre found 1,046
    vessels in its 0.25° vicinity while the radius captures 26.

    True when a V/S-classified port has far more vessels in the padded box OUTSIDE its
    radius than inside. Such a port's low in-radius count is a **GEOMETRY signal, not a
    traffic signal**, and must be flagged rather than reported as a small number silently.
    Deliberately keyed on the measured ratio and the SIZE CLASS — never on a port name."""
    if (port.get("harbor_size") or "") not in UNDER_COVER_SIZES:
        return False
    if vessels_outside_radius < UNDER_COVER_MIN_OUTSIDE:
        return False
    return vessels_outside_radius >= UNDER_COVER_RATIO * max(vessels_in_radius, 1)


# ---------------------------------------------------------------- the sessionizer
def port_visits(
    port: dict[str, Any],
    mmsi: str,
    fixes: Sequence[PortFix],                # ONE vessel, ts-ascending, padded-box scope
    *,
    window_start: Any,
    window_end: Any,
    outage_windows: list[OutageWindow] | None = None,
    containing_ports: Any = None,            # (lat, lon) -> list of port dicts
    low_speed_kn: float = LOW_SPEED_KN,
    gap_s: float = GAP_SPLIT_S,
    min_fixes: int = MIN_FIXES,
    min_duration_s: float = MIN_DURATION_S,
    min_low_speed_span_s: float = MIN_LOW_SPEED_SPAN_S,
    majority_low_frac: float = MAJORITY_LOW_FRAC,
    flank_s: float = FLANK_S,
) -> tuple[list[PortVisit], dict[str, int], int]:
    """Visits for ONE vessel at ONE port. Pure.

    Returns ``(visits, dropped_at, dropped_fixes)`` — the survivors, the funnel counts
    charged to the FIRST bar each raw crossing failed, and the number of
    suspect/implausible fixes removed inside session spans.

    ``fixes`` covers the port's PADDED box (radius + FLANK_PAD_KM) so that fixes outside
    the radius are available to observe entry/exit. Quality is filtered here, not by the
    caller, so :func:`accept_fix` is the one definition and the dropped count is
    attributable per session."""
    outage_windows = outage_windows or []
    dropped_at: dict[str, int] = {}
    r_m = float(port["radius_m"])

    # quality filter (this layer's stricter rule) — keep the rejects to attribute later
    kept: list[PortFix] = []
    rejected: list[PortFix] = []
    for f in fixes:
        (kept if accept_fix(f.position_quality) else rejected).append(f)

    # tag by distance; inside == within the port's own (assumed) radius
    tagged: list[tuple] = []      # (ts, lat, lon, implied, sog, dist_m, inside)
    for f in kept:
        d_m = haversine_km(f.lat, f.lon, port["lat"], port["lon"]) * 1000.0
        tagged.append((f.ts, f.lat, f.lon, f.implied_speed_kn, f.reported_sog_kn,
                       d_m, d_m <= r_m))
    if not any(t[6] for t in tagged):
        return [], dropped_at, 0

    out: list[PortVisit] = []
    dropped_fixes_total = 0
    for run in _split(tagged, gap_s):
        start_ts, end_ts = run[0][0], run[-1][0]
        duration_s = (end_ts - start_ts).total_seconds()
        implied = [r[3] for r in run if r[3] is not None]
        low = [s for s in implied if s < low_speed_kn]
        frac_low = (len(low) / len(implied)) if implied else 0.0
        span_s = _low_speed_span_s(run, low_speed_kn)

        # ---- the visit bar, ordered; charge each failure to the FIRST bar it misses
        if len(run) < min_fixes:
            dropped_at[B_SINGLE_FIX] = dropped_at.get(B_SINGLE_FIX, 0) + 1
            continue
        if duration_s < min_duration_s:
            dropped_at[B_BELOW_DURATION] = dropped_at.get(B_BELOW_DURATION, 0) + 1
            continue
        if span_s < min_low_speed_span_s:
            dropped_at[B_BELOW_LOW_SPEED_SPAN] = dropped_at.get(B_BELOW_LOW_SPEED_SPAN, 0) + 1
            continue
        if frac_low <= majority_low_frac:
            dropped_at[B_NOT_MAJORITY_LOW] = dropped_at.get(B_NOT_MAJORITY_LOW, 0) + 1
            continue

        # ---- boundaries: an OUTSIDE fix flanking the run within flank_s
        entry = next((t for t in reversed(tagged)
                      if not t[6] and 0 <= (start_ts - t[0]).total_seconds() <= flank_s), None)
        exit_ = next((t for t in tagged
                      if not t[6] and 0 <= (t[0] - end_ts).total_seconds() <= flank_s), None)
        if entry and exit_:
            completeness = COMPLETE
        elif entry:
            completeness = OPEN_ENDED
        elif exit_:
            completeness = TRUNCATED
        else:
            completeness = PRESENCE_ONLY

        entry_reason = exit_reason = None
        entry_gap_s = exit_gap_s = None
        if entry is None:
            entry_reason, entry_gap_s = _boundary_reason(
                start_ts, looking_back=True, window_start=window_start,
                window_end=window_end, outage_windows=outage_windows, flank_s=flank_s)
        if exit_ is None:
            exit_reason, exit_gap_s = _boundary_reason(
                end_ts, looking_back=False, window_start=window_start,
                window_end=window_end, outage_windows=outage_windows, flank_s=flank_s)

        # ---- attribution: EVERY port containing a fix, never an arbitrary first
        cands: list[str] = [port["port_id"]]
        if containing_ports is not None:
            seen = {port["port_id"]}
            for t in run:
                for p in containing_ports(t[1], t[2]):
                    if p["port_id"] not in seen:
                        seen.add(p["port_id"])
                        cands.append(p["port_id"])

        closest = min(run, key=lambda r: r[5])
        n_dropped = sum(1 for f in rejected if start_ts <= f.ts <= end_ts)
        dropped_fixes_total += n_dropped
        out.append(PortVisit(
            port_id=port["port_id"], port_name=port["port_name"], mmsi=mmsi,
            start_ts=start_ts, end_ts=end_ts,
            duration_s=round(duration_s, 1), low_speed_span_s=round(span_s, 1),
            n_fixes=len(run), n_low_speed_fixes=len(low), frac_low_speed=round(frac_low, 3),
            min_distance_m=round(closest[5], 1),
            mean_implied_speed_kn=round(sum(implied) / len(implied), 2) if implied else None,
            min_implied_speed_kn=round(min(implied), 2) if implied else None,
            reported_sog_kn=_clean_sog([r[4] for r in run]),
            lat=round(closest[1], 6), lon=round(closest[2], 6),
            completeness=completeness,
            entry_ts=entry[0] if entry else None,
            exit_ts=exit_[0] if exit_ else None,
            entry_missing_reason=entry_reason, exit_missing_reason=exit_reason,
            entry_outage_gap_s=entry_gap_s, exit_outage_gap_s=exit_gap_s,
            duration_is_lower_bound=True,
            arrival_ts_is_unknown=completeness in ENTRY_MISSING_STATES,
            radius_m=r_m, radius_basis=port["radius_basis"],
            coord_precision=port.get("coord_precision"),
            candidate_ports=tuple(cands),
            dropped_fixes=n_dropped,
        ))
    return out, dropped_at, dropped_fixes_total


def describe_boundary(visit: PortVisit) -> str:
    """Human-facing boundary phrasing — the DRAUGHT-1 lesson made concrete. A missing
    exit renders as what was NOT observed plus why; it must never read as
    "still in port"."""
    if visit.completeness == COMPLETE:
        return "arrival and departure both observed"
    bits: list[str] = []
    if visit.entry_missing_reason:
        bits.append(_phrase("no arrival observed", visit.entry_missing_reason,
                            visit.entry_outage_gap_s))
    if visit.exit_missing_reason:
        bits.append(_phrase("no departure observed", visit.exit_missing_reason,
                            visit.exit_outage_gap_s))
    return "; ".join(bits)


def _phrase(head: str, reason: str, outage_gap_s: float | None) -> str:
    if reason == R_OUTAGE:
        return f"{head} — feed down{_dur(outage_gap_s)}"
    if reason == R_WINDOW_EDGE:
        return f"{head} — outside the queried window"
    return f"{head} — vessel silent, feed up"


def _dur(seconds: float | None) -> str:
    """" for N h" / " for N min", or "" when the overlap is negligible — never
    " for 0.0 h", which reads as a contradiction of the outage it is explaining."""
    if not seconds or seconds < 60:
        return ""
    if seconds < 3600:
        return f" for {seconds / 60:.0f} min"
    return f" for {seconds / 3600.0:.1f} h"
