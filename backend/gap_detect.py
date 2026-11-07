"""Pure AIS-gap classification — the heart of A1 (confound removal).

A raw long gap is NOT "going dark". This module turns a gap (a consecutive-
position interval for one vessel) into one of four HONEST classifications by
removing the three confounds found in the data assessment:

  * outage-excluded — the gap overlaps a feed_disconnect window (the collector /
    receiver was down; EVERY vessel gaps then — not vessel behaviour). The #1
    confound: ~50h of total outage in the assessment window.
  * jamming-excluded — the gap is a GNSS-anomaly artifact (G4b, the fifth confound):
    the vessel transmitted implausible/spoofed positions DURING the gap (so the
    plausible-stream silence is not real going-dark), and/or the gap sits in an
    ACTIVE area-jamming zone x window (the G4a affected-zone product). Signal-
    integrity, not vessel choice — an external denial like outage, so it excludes
    high in the waterfall. A distinct honest label (NOT outage-excluded, whose detail
    means a feed outage): a study must tell a jammed vessel from a feed drop.
  * stationary     — the vessel was NOT underway at the last fix (nav_status != 0
    or speed <= UNDERWAY_MIN_SPEED). vessel_states stores a moved-vessel delta
    (identical reports deduped), so an anchored/moored vessel legitimately
    reports sparsely — ~46% of rows are near-stationary.
  * edge           — the vessel was underway but the gap STARTED outside dense
    coverage (per the data-driven CoverageMask, which handles Cyprus/mid-sea
    receivers — not naive coast-distance).
  * candidate      — underway, in dense coverage, not during an outage: an
    INVESTIGATION LEAD ("underway vessel in good coverage stopped reporting for
    >= the threshold"), NOT proven intent.

Priority when several apply: outage-excluded > jamming-excluded > stationary > edge
> candidate — a data-collection fact and a GNSS-denial fact (both "the signal was
externally interrupted, not the vessel's choice") trump a vessel-state fact, which
trumps a coverage fact.

All functions are pure (no I/O) so the rules are unit-testable without a DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Gap threshold — from the assessment percentiles (routine cadence ends between
#: p95≈33 min and p99≈4.5h; 1h sits above the noise, below the outage-dominated
#: >6h tail).
GAP_THRESHOLD_S = 3600.0

#: "Underway" at the last fix: AIS nav_status 0 (under way using engine) AND a
#: real speed. Anything else is treated as stationary for gap purposes.
UNDERWAY_NAV_STATUS = 0
UNDERWAY_MIN_SPEED = 1.0

#: An unpaired feed_connect (silent session death, Finding B) is treated as an
#: outage ONLY if NO position was archived for at least this long before it —
#: otherwise the feed kept delivering right up to the reconnect (a SEAMLESS
#: reconnect), and inferring an outage would wrongly exclude genuinely-dark vessels
#: whose >=1 h gap merely SPANS the reconnect instant. The real 2026-07-19 unpaired
#: connects were all seamless (~1-2 s data gaps), so this infers ZERO outage for
#: them (verified). 5 min is far above normal inter-position spacing across the
#: fleet (sub-minute), so we UNDER-infer outages, never over-trust one.
MIN_INFERRED_OUTAGE_S = 300.0

CANDIDATE = "candidate"
OUTAGE_EXCLUDED = "outage-excluded"
JAMMING_EXCLUDED = "jamming-excluded"   # G4b fifth confound (GNSS-anomaly artifact)
EDGE = "edge"
STATIONARY = "stationary"

METHOD = "ais-gap-detector-v1"


@dataclass(frozen=True, slots=True)
class OutageWindow:
    """A feed-outage interval [start, end] with provenance.

    ``inferred`` marks a window we RECONSTRUCTED rather than read verbatim from a
    recorded disconnect->connect pair; ``basis`` names how it was derived, so A1's
    exclusion consumes recorded and inferred windows uniformly while a study can
    still distinguish their provenance:
      * 'recorded'         — a recorded feed_disconnect closed by a feed_connect.
      * 'recorded-open'    — a recorded feed_disconnect with NO following connect;
                             the feed is still down, so the outage runs to the run
                             timestamp (Finding A). Recorded, not inferred.
      * 'unpaired-connect' — a feed_connect arriving while already connected implies
                             a session that died WITHOUT a recorded disconnect; the
                             outage is inferred to end at that connect (Finding B).
    """

    start: Any
    end: Any
    inferred: bool = False
    basis: str = "recorded"


def reconstruct_outage_windows(
    events: list[tuple[Any, str]],
    now: Any,
    last_position_before: dict[Any, Any] | None = None,
    min_inferred_outage_s: float = MIN_INFERRED_OUTAGE_S,
) -> list[OutageWindow]:
    """Reconstruct feed-outage windows from ordered (ts, kind) signal events.

    `events` is (ts, kind) sorted ascending, from signal_events (one feed/source).
    `now` is the reconstruction/run timestamp — an OPEN trailing disconnect is an
    ONGOING outage up to `now` (Finding A), never dropped. `last_position_before`
    maps a feed_connect ts -> the last archived position ts strictly before it (for
    the source), used to bound an inferred window's start (Finding B).

    Two integrity guarantees beyond naive disconnect->connect pairing:

      * Finding A — open window: a feed_disconnect with no following feed_connect
        (the feed is still down) extends the outage to `now`. Naive pairing dropped
        it, so gaps spanning an ongoing outage were wrongly left as candidates.

      * Finding B — unpaired connect (silent session death): a feed_connect while
        already connected (no intervening feed_disconnect) means the prior session
        died without a recorded disconnect. We INFER the missing outage: it ends at
        the new connect; its start is the LATEST evidence of liveness before it —
        max(last recorded feed_connect, last archived position ts). Using the latest
        liveness makes the inferred window an UPPER bound on connected time / LOWER
        bound on outage: we may UNDER-exclude, never over-trust (positions flowed
        until the silent death, so the true outage is only from there to reconnect).
        A window is inferred ONLY when that data gap is a real outage
        (>= `min_inferred_outage_s`); a seamless reconnect (data flowing until
        seconds before the connect) infers nothing, so a long gap that merely spans
        the reconnect instant is never excluded by a feed blip.
    """
    pos_before = last_position_before or {}
    windows: list[OutageWindow] = []
    state: str | None = None          # None (initial) | 'connected' | 'disconnected'
    open_disc: Any = None             # ts of the disconnect that opened the outage
    last_connect: Any = None          # ts of the current open connect session
    for ts, kind in events:
        if kind == "feed_disconnect":
            # Keep the LATEST disconnect: a second disconnect with no connect between
            # implies an unrecorded reconnect, so the KNOWN outage is only from the
            # latest disconnect onward (conservative — smallest outage, never over-trust).
            open_disc = ts
            state = "disconnected"
        elif kind == "feed_connect":
            if state == "disconnected":
                windows.append(OutageWindow(open_disc, ts, inferred=False, basis="recorded"))
                open_disc = None
            elif state == "connected":
                # Finding B: a connect while already connected implies a silent session
                # death. Infer the outage ONLY if data actually stopped for a real
                # period: start = latest liveness before ts (max of last connect and
                # last archived position), and emit only when ts - start is a genuine
                # outage (>= min_inferred_outage_s). A seamless reconnect (positions
                # flowing until ~seconds before ts) infers NOTHING, so a >=1 h gap that
                # merely spans the reconnect instant stays a candidate — never excluded
                # by a feed blip (the honest direction).
                candidates = [x for x in (last_connect, pos_before.get(ts)) if x is not None]
                start = max(candidates) if candidates else ts
                if (ts - start).total_seconds() >= min_inferred_outage_s:
                    windows.append(OutageWindow(start, ts, inferred=True, basis="unpaired-connect"))
            # (state is None: the very first connect — taken at face value, no window)
            last_connect = ts
            state = "connected"
    if state == "disconnected" and open_disc is not None:
        # Finding A: still down — ongoing outage up to the run timestamp.
        windows.append(OutageWindow(open_disc, now, inferred=False, basis="recorded-open"))
    return windows


def overlapping_window(gap_start: Any, gap_end: Any, windows: list[OutageWindow]) -> OutageWindow | None:
    """The first outage window intersecting the gap [gap_start, gap_end], or None.
    An OPEN gap (gap_end None) extends to +inf, so an outage that began during it
    still counts."""
    for w in windows:
        if w.start < (gap_end if gap_end is not None else w.end) and w.end > gap_start:
            return w
    return None


def overlaps_outage(gap_start: Any, gap_end: Any, windows: list[OutageWindow]) -> bool:
    """True iff the gap [gap_start, gap_end] intersects any outage window."""
    return overlapping_window(gap_start, gap_end, windows) is not None


def is_underway(nav_status: Any, speed: Any) -> bool:
    return nav_status == UNDERWAY_NAV_STATUS and (speed or 0) > UNDERWAY_MIN_SPEED


def classify_gap(
    *,
    gap_start: Any,
    gap_end: Any,
    start_lat: float | None,
    start_lon: float | None,
    nav_status: Any,
    speed: Any,
    mask: Any,                       # CoverageMask (duck-typed: .is_dense)
    outage_windows: list[OutageWindow],
    in_jamming_zone: bool = False,   # G4b fifth confound (computed by the runner)
) -> str:
    """Classify a gap. Priority: outage > jamming > stationary > edge > candidate.

    ``in_jamming_zone`` (the fifth confound, G4b) is True when the gap is a GNSS-
    anomaly artifact — the vessel transmitted implausible positions during it, and/or
    it sits in an active area-jamming zone. It excludes right after outage (both are
    external signal interruptions, not the vessel's choice). The runner computes it
    from the affected-zone product + a spanned-implausible check and records the basis
    in detail; the tier is a distinct honest label, never conflated with outage."""
    if overlaps_outage(gap_start, gap_end, outage_windows):
        return OUTAGE_EXCLUDED
    if in_jamming_zone:
        return JAMMING_EXCLUDED
    if not is_underway(nav_status, speed):
        return STATIONARY
    if not mask.is_dense(start_lat, start_lon):
        return EDGE
    return CANDIDATE


#: Human-facing caveat carried on the surface — a candidate is NEVER a proven
#: "went dark". Stated wherever candidates are exposed.
CANDIDATE_CAVEAT = (
    "Investigation lead, not proof: an underway vessel in good AIS coverage "
    "stopped reporting for >= the gap threshold, not explained by a receiver/"
    "feed outage. Terrestrial AIS is spoofable and coverage is imperfect — "
    "intent cannot be proven from a gap alone."
)
