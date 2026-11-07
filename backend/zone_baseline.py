"""Per-zone familiarity baseline (ZONES-3) — suppress the routine by MEASUREMENT.

ZONES-2 recommended allowlisting the busiest zone (a "probable loading terminal");
ZONES-3 REJECTS that. On principle: this project flags and never deletes, filters at
consumption not storage, and keeps the excluded set queryable — an allowlist silences a
whole location a priori. On risk: "probably a legitimate loading terminal" is an
inference, not a fact, and a loading point is exactly where an undeclared transfer would
occur — allowlisting it would create a permanent blind spot at the single highest-
activity site in the layer. Instead, suppress the routine STATISTICALLY, as
coverage_mask.py suppresses gaps in thin-coverage cells rather than by an exception list:
derive from each zone's own history what "normal" looks like, and rank a session by its
DEPARTURE from that pattern rather than by its mere existence.

An empirical, rebuilt-in-process derived baseline (the coverage_mask.py precedent) —
NOT a stored table. Per zone: the vessels seen, the declared-type mix, and the dwell-
duration distribution. A session is UNFAMILIAR if it involves a vessel new to the zone,
a declared type outside the zone's normal mix, or a duration well outside its
distribution (> p90).

DATA SUFFICIENCY (must be checked before use). A per-zone baseline needs enough history
that "new vessel" is informative and the type/duration distributions are stable. With
too little, the first-ever visitor to every zone reads as "unfamiliar" — worse than none
(the same reason roadmap priority 6 defers the statistical grid until ~90 days of
accumulation). The caller MUST consult :meth:`ZoneBaseline.is_sound` / :func:`verdict`
and fall back (admit NO standoff_dwell) when the baseline is under-determined.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

# --- sufficiency thresholds (basis stated) ----------------------------------
#: A zone's baseline is sound only with enough history on all three axes. Bases:
#:  * >= 30 days so a duration/type distribution is not a single week's weather; a
#:    FLOOR well below priority 6's ~90-day target, deliberately generous so the layer
#:    can graduate zones as data accrues rather than waiting for the full grid.
#:  * >= 10 distinct vessels so "new to the zone" is informative (a zone that has only
#:    ever seen 3 vessels calls its 4th visitor "unfamiliar" — noise, not signal).
#:  * >= 20 sessions to characterise the type mix and the duration distribution.
#: ZONES-3 measured (full 30-day window): only 1 of 17 trafficked zones reaches the
#: vessel+session bars and NONE reaches 30 days — hence the under-determined verdict.
MIN_BASELINE_DAYS = 30.0
MIN_BASELINE_VESSELS = 10
MIN_BASELINE_SESSIONS = 20

#: A duration is "well outside" the zone's pattern past this percentile of its history.
DURATION_OUTLIER_PCTL = 0.90


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    lo = sorted_vals[f]
    hi = sorted_vals[min(f + 1, len(sorted_vals) - 1)]
    return lo + (hi - lo) * (k - f)


@dataclass(frozen=True, slots=True)
class ZoneBaseline:
    """One zone's empirical 'normal', derived from its historical dwell sessions."""
    zone_id: str
    vessels: frozenset[str]
    type_counts: dict[str, int]
    durations: tuple[float, ...]        # sorted ascending
    n_sessions: int
    span_days: float

    def is_sound(self) -> bool:
        """Enough history for the familiarity test to be meaningful on all three axes."""
        return (self.n_sessions >= MIN_BASELINE_SESSIONS
                and len(self.vessels) >= MIN_BASELINE_VESSELS
                and self.span_days >= MIN_BASELINE_DAYS)

    def duration_p90(self) -> float:
        return _percentile(list(self.durations), DURATION_OUTLIER_PCTL)


@dataclass(frozen=True, slots=True)
class Familiarity:
    familiar: bool
    reasons: tuple[str, ...]            # subset of: vessel_new, type_novel, duration_outlier


def build_baselines(sessions: Iterable[Any]) -> dict[str, ZoneBaseline]:
    """Build per-zone baselines from historical dwell sessions (duck-typed: each has
    .zone_id, .mmsi, .ship_type, .duration_s, .start_ts). Rebuilt in-process each run,
    NOT stored — the coverage_mask.py discipline."""
    vessels: dict[str, set[str]] = {}
    types: dict[str, Counter] = {}
    durs: dict[str, list[float]] = {}
    first: dict[str, Any] = {}
    last: dict[str, Any] = {}
    n: Counter = Counter()
    for s in sessions:
        z = s.zone_id
        vessels.setdefault(z, set()).add(s.mmsi)
        types.setdefault(z, Counter())[s.ship_type] += 1
        durs.setdefault(z, []).append(s.duration_s)
        n[z] += 1
        if z not in first or s.start_ts < first[z]:
            first[z] = s.start_ts
        if z not in last or s.start_ts > last[z]:
            last[z] = s.start_ts
    out: dict[str, ZoneBaseline] = {}
    for z in n:
        span_days = (last[z] - first[z]).total_seconds() / 86400.0 if n[z] > 1 else 0.0
        out[z] = ZoneBaseline(
            zone_id=z, vessels=frozenset(vessels[z]), type_counts=dict(types[z]),
            durations=tuple(sorted(durs[z])), n_sessions=n[z], span_days=span_days,
        )
    return out


def classify_familiarity(baseline: ZoneBaseline | None, *, mmsi: str,
                         ship_type: str | None, duration_s: float) -> Familiarity:
    """Familiar vs unfamiliar relative to the zone's own pattern. With NO baseline (a
    zone never seen before) everything is unfamiliar by definition — which is exactly
    why the caller must gate on sufficiency first, not treat this as signal on thin data."""
    if baseline is None:
        return Familiarity(False, ("vessel_new",))
    reasons: list[str] = []
    if mmsi not in baseline.vessels:
        reasons.append("vessel_new")
    if ship_type not in baseline.type_counts:
        reasons.append("type_novel")
    if duration_s > baseline.duration_p90():
        reasons.append("duration_outlier")
    return Familiarity(not reasons, tuple(reasons))


def verdict(baselines: dict[str, ZoneBaseline]) -> dict[str, Any]:
    """Overall data-sufficiency verdict across all zones — reported before use, and the
    gate the runner consults to decide whether standoff_dwell may be admitted."""
    sound = [z for z, b in baselines.items() if b.is_sound()]
    max_days = max((b.span_days for b in baselines.values()), default=0.0)
    return {
        "zones_with_history": len(baselines),
        "zones_sound": len(sound),
        "sound_zone_ids": sound,
        "max_span_days": round(max_days, 1),
        "thresholds": {"min_days": MIN_BASELINE_DAYS, "min_vessels": MIN_BASELINE_VESSELS,
                       "min_sessions": MIN_BASELINE_SESSIONS},
        "sufficient": bool(sound),
    }


def readiness(baselines: dict[str, ZoneBaseline], window_hours: int | None = None) -> dict[str, Any]:
    """Baseline-READINESS report — the dormancy fix (ZONES-4). standoff_dwell admission is
    deferred while no zone is sound; this makes that a VISIBLE state, not a forgotten one:
    per zone the current span/vessel/session counts, and overall which BAR binds and by how
    much — so the deferral announces itself the moment the bars are met, rather than staying
    silently dormant with no wake-up trigger."""
    zs = list(baselines.values())
    n_days = sum(1 for b in zs if b.span_days >= MIN_BASELINE_DAYS)
    n_vess = sum(1 for b in zs if len(b.vessels) >= MIN_BASELINE_VESSELS)
    n_sess = sum(1 for b in zs if b.n_sessions >= MIN_BASELINE_SESSIONS)
    max_days = max((b.span_days for b in zs), default=0.0)
    max_vess = max((len(b.vessels) for b in zs), default=0)
    max_sess = max((b.n_sessions for b in zs), default=0)
    sound = [b.zone_id for b in zs if b.is_sound()]
    # the binding bar = the one met by the FEWEST zones (systemic blocker); tie-break on the
    # smallest best-observed value relative to its threshold (furthest from being met).
    bars = [("days", n_days, max_days, MIN_BASELINE_DAYS),
            ("vessels", n_vess, float(max_vess), MIN_BASELINE_VESSELS),
            ("sessions", n_sess, float(max_sess), MIN_BASELINE_SESSIONS)]
    binding = min(bars, key=lambda x: (x[1], x[2] / x[3]))
    return {
        "baseline_ready": bool(sound),
        "window_hours": window_hours,
        "zones_with_history": len(zs),
        "zones_sound": len(sound),
        "bars": {"min_days": MIN_BASELINE_DAYS, "min_vessels": MIN_BASELINE_VESSELS,
                 "min_sessions": MIN_BASELINE_SESSIONS},
        "zones_meeting": {"days": n_days, "vessels": n_vess, "sessions": n_sess},
        "best_observed": {"span_days": round(max_days, 1), "vessels": max_vess, "sessions": max_sess},
        "binding_bar": binding[0],
        "gap_to_binding": round(binding[3] - binding[2], 1),
        "per_zone": sorted(
            [{"zone_id": b.zone_id, "span_days": round(b.span_days, 1), "vessels": len(b.vessels),
              "sessions": b.n_sessions, "sound": b.is_sound()} for b in zs],
            key=lambda d: -d["sessions"]),
    }
