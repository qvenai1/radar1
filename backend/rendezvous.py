"""Pure rendezvous logic — A2 (two vessels loitering together in open sea).

The value, like A1, is CONFOUND REMOVAL plus HONEST PRIORITISATION. The naive
"two slow vessels close together" signal is ~99.9% ports/anchorages, and even the
open-sea survivors are MOSTLY LEGITIMATE (offshore-energy drillship+supply
clusters, ferries). So this module:
  * defines the rendezvous SIGNATURE (both <= RDV_MAX_SPEED_KN, true distance
    <= RDV_MAX_DIST_M, sustained >= RDV_MIN_DURATION_S), and
  * ranks (never auto-labels) by ship type + flag: service/offshore/ferry -> low;
    tanker+tanker or flags-of-convenience cargo/tanker -> high; else medium.

The port/anchorage + busy masks and the open-sea floor live in coverage_mask.py;
the detector applies them. Everything here is pure (no I/O) and unit-testable.

HONEST LIMITATION (surfaced): a candidate = "two low-speed vessels loitered close,
away from port, in open sea" — an INVESTIGATION LEAD, not proof of a transfer.
Many candidates are legitimate offshore work or scheduled traffic; AIS is
spoofable and identity is point-in-time.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Any

from .geo_area import haversine_km

#: Rendezvous signature thresholds (from the A2 assessment).
RDV_MAX_DIST_M = 500.0        # true pairwise distance (alongside/close-drift)
RDV_MAX_SPEED_KN = 3.0        # both vessels slow (loitering, not crossing)
RDV_MIN_DURATION_S = 3600.0   # sustained >= 1h (STS takes hours); longer = stronger
RDV_TIME_MATCH_S = 300.0      # match two vessels' fixes within +/- 5 min
RDV_GAP_TOLERANCE_S = 1800.0  # a >30 min break splits one encounter into two
PROX_BUCKET_S = 600           # 10-min pre-filter bucket (matches the SQL pre-filter)

METHOD = "rendezvous-detector-v1"

HIGH, MEDIUM, LOW = "high", "medium", "low"

#: Ship types that make a meeting LIKELY LEGITIMATE (de-prioritised, never
#: excluded): service craft, ferries, and AIS "other" (90-99 special craft —
#: offshore supply vessels / drillships report here, the dominant clean-candidate
#: confound in the assessment).
LOW_SUSPICION_TYPES = frozenset({
    "passenger", "high_speed_craft",
    "tug", "pilot", "port_tender", "dredging", "diving_ops", "towing",
    "anti_pollution", "search_and_rescue", "law_enforcement", "medical_transport",
    "military_ops", "sailing", "pleasure_craft", "fishing", "wing_in_ground",
    "other",
})

#: Open registries / grey-fleet flags — a cargo/tanker pair BOTH under these is
#: escalated. A prioritisation prior, not a judgement.
FLAGS_OF_CONVENIENCE = frozenset({
    "Panama", "Liberia", "Marshall Islands", "Bahamas", "Malta", "Cyprus",
    "Antigua and Barbuda", "Comoros", "Togolese Republic", "Palau",
    "Saint Kitts and Nevis", "Cook Islands", "Sierra Leone", "Cameroon",
    "Tanzania", "Vanuatu", "Belize", "Moldova", "Gabonese Republic",
    "Sao Tome and Principe", "Barbados",
})


def suspicion_rank(type_a: str | None, type_b: str | None,
                   flag_a: str | None, flag_b: str | None) -> str:
    """Rank a candidate 'high' | 'medium' | 'low' from both ship types + flags.

    PRIORITISATION, not classification — every clean candidate is stored; this
    only orders them so an analyst sees tanker-tanker STS profiles before the
    offshore-energy / ferry noise. NEVER an assertion of wrongdoing.
    """
    if type_a in LOW_SUSPICION_TYPES or type_b in LOW_SUSPICION_TYPES:
        return LOW                                   # offshore support / ferry / service
    if type_a == "tanker" and type_b == "tanker":
        return HIGH                                  # classic ship-to-ship transfer
    both_cargo_tanker = {type_a, type_b} <= {"cargo", "tanker"}
    if both_cargo_tanker and flag_a in FLAGS_OF_CONVENIENCE and flag_b in FLAGS_OF_CONVENIENCE:
        return HIGH                                  # FoC cargo/tanker pair
    return MEDIUM                                    # cargo/unknown pairs — insufficient signal to rank


def time_matched_distances(
    fixes_a: list[tuple[Any, float, float, float | None]],
    fixes_b: list[tuple[Any, float, float, float | None]],
    tol_s: float = RDV_TIME_MATCH_S,
) -> list[tuple[Any, float, float | None, float | None]]:
    """For each fix of A, pair it with the NEAREST-in-time fix of B within tol_s,
    and return (ts, distance_m, speed_a, speed_b) rows sorted by ts.

    Both fix lists are (ts, lat, lon, speed) sorted ascending by ts. O(n_a log n_b)
    via binary search — never an all-pairs product.
    """
    if not fixes_a or not fixes_b:
        return []
    b_ts = [f[0] for f in fixes_b]
    out: list[tuple[Any, float, float | None, float | None]] = []
    for ts_a, la, lo, spd_a in fixes_a:
        i = bisect_left(b_ts, ts_a)
        best = None
        for j in (i - 1, i):                         # the two B fixes bracketing ts_a
            if 0 <= j < len(fixes_b):
                dt = abs((b_ts[j] - ts_a).total_seconds())
                if dt <= tol_s and (best is None or dt < best[0]):
                    best = (dt, fixes_b[j])
        if best is not None:
            _, (ts_b, lb, ob, spd_b) = best
            dist_m = haversine_km(la, lo, lb, ob) * 1000.0
            out.append((ts_a, dist_m, spd_a, spd_b))
    return out


def encounters(
    series: list[tuple[Any, float, float | None, float | None]],
    max_dist_m: float = RDV_MAX_DIST_M,
    max_speed_kn: float = RDV_MAX_SPEED_KN,
    gap_tol_s: float = RDV_GAP_TOLERANCE_S,
    min_dur_s: float = RDV_MIN_DURATION_S,
) -> list[dict[str, Any]]:
    """Group time-matched fixes into sustained close-and-slow encounters.

    A fix is "close" when distance <= max_dist_m AND BOTH speeds <= max_speed_kn.
    Consecutive close fixes with <= gap_tol_s between them form one encounter;
    only encounters lasting >= min_dur_s are returned. Each is
    {start_ts, end_ts, duration_s, min_distance_m, both_max_speed, n_fixes}.
    """
    def is_close(row: tuple[Any, float, float | None, float | None]) -> bool:
        _, d, sa, sb = row
        return (d <= max_dist_m and (sa or 0) <= max_speed_kn and (sb or 0) <= max_speed_kn)

    out: list[dict[str, Any]] = []
    run: list[tuple[Any, float, float | None, float | None]] = []

    def flush() -> None:
        if len(run) < 2:
            return
        dur = (run[-1][0] - run[0][0]).total_seconds()
        if dur >= min_dur_s:
            out.append({
                "start_ts": run[0][0], "end_ts": run[-1][0], "duration_s": dur,
                "min_distance_m": round(min(r[1] for r in run), 1),
                "both_max_speed": round(max(max((r[2] or 0), (r[3] or 0)) for r in run), 2),
                "n_fixes": len(run),
            })

    for row in series:
        if not is_close(row):
            continue
        if run and (row[0] - run[-1][0]).total_seconds() > gap_tol_s:
            flush()
            run = []
        run.append(row)
    flush()
    return out


#: Human-facing caveat carried on the surface — a candidate is NEVER proof of a
#: transfer, and most candidates are legitimate.
CANDIDATE_CAVEAT = (
    "Investigation lead, not proof: two low-speed vessels loitered close together, "
    "away from port, in open sea. This does NOT prove a transfer occurred — many "
    "candidates are legitimate offshore work (supply/drilling) or scheduled "
    "traffic (ferries). AIS is spoofable and identity is point-in-time. "
    "Ranking prioritises, it does not accuse."
)
