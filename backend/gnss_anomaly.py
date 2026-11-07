"""Pure GNSS position-quality classification — the heart of G1 (data-quality
as event). Turns a vessel fix + its neighbours into a HONEST per-position verdict
('plausible' | 'suspect' | 'implausible') plus the reasons that fired, using the
two rules verified in the G0 assessment. No I/O: the rule functions take plain
data in and return plain data out, so they are unit-testable without a DB and
deterministic (same inputs => identical outputs, always). The G2 runner owns all
I/O and stamps ``quality_computed_at``.

Two rules (independent by design — each catches a spoofing mode the other misses):

  R1 — impossible kinematics: implied speed = haversine distance / Δt over
       CONSECUTIVE fixes per MMSI (the shared geo_area haversine — NEVER the
       reported AIS SOG, which carries the 102.2/1023 'not available' sentinel and
       zeros under spoofing). A DUAL condition guards it: a speed threshold fires
       only when the jump distance is ALSO >= 500 m — G0/V3 showed this removes
       10–11% of short-Δt GPS noise in the 40–100 kn bands at <1% cost above
       100 kn. Verdicts: incoming > 60 kn => impossible (>100 and 60–100 both
       'implausible'); 40–60 kn => 'suspect' UNLESS the vessel is a fast craft
       (passenger / high-speed-craft) at <= 60 kn. Incoming AND outgoing both
       > 100 kn => additionally the teleport_return flag (the cleanest signature —
       714 positions in G0 data). Catches teleports and scatter.

  R2 — inland depth: a fix that lands inside the seven-country land mass is on
       land — impossible for a ship beyond a harbour fringe. Depth is the distance
       to the UNION boundary (geo_area.AreaOfInterest.inland_depth_km — internal
       land borders dissolved). > 5 km => 'implausible'; 2–5 km => 'suspect';
       < 2 km => clean (quay/port; ~78k legitimate rows in G0). R2 is the ONLY
       rule that catches the coherent-offset pattern (the Lebanon cell in G0/V1),
       where the track shape and all speeds are realistic but the whole track is
       displaced onto land — R1 stays silent there.

Verdict composition: position_quality is the WORST verdict any rule produced
(implausible > suspect > plausible); anomaly_flags is the UNION of all fired
flags. A fix where no rule fires is 'plausible' with empty flags — and the runner
still stamps quality_method/quality_computed_at, so "checked and clean" (verdict
'plausible', method set) is distinguishable from "not yet checked" (NULL).

FIRST FIX of a vessel in a processing window has no incoming segment, so the
kinematic rule does not apply to it — only R2 does. To compute the incoming
segment at BATCH BOUNDARIES, the G2 runner MUST reprocess with an OVERLAP window:
each batch re-includes the last prior fix per MMSI, mirroring the recompute-and-
replace discipline of the other detectors. This keeps the layer idempotent.

HONEST LIMITATION (surfaced): we detect IMPLAUSIBLE positions, not "spoofing" per
se — implausibility means something is wrong (deliberate spoof, area jamming, a
GPS glitch, or an AIS data error), not necessarily intent. A vessel showing
spoofed positions is usually a VICTIM of area jamming, not an evader, so a GNSS
verdict is NOT a suspicion ranking the way a gap/rendezvous lead is. The
union-depth metric has one documented residual blind spot: the union's outer edge
includes land borders with non-AOI neighbours (Turkey–Iran, Iraq–Saudi Arabia,
Israel–Egypt near Rafah), so a position coherently offset to within ~2 km of such
a border can read a small depth and evade R2; scatter there is still caught by R1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .geo_area import haversine_km

# --- frozen thresholds (approved in the G0 verification report) --------------

#: Implied-speed tiers (knots), from the G0 impossible-kinematics distribution.
SPEED_IMPOSSIBLE_KN = 100.0   # physically impossible for any vessel -> implausible
SPEED_HIGH_KN = 60.0          # beyond even fast ferries -> implausible
SPEED_SUSPECT_KN = 40.0       # above the merchant ceiling -> suspect
#: Passenger / high-speed craft may legitimately reach the 40–60 band; exempt them
#: there (but NOT above 60 kn).
FAST_CRAFT_ALLOWANCE_KN = 60.0
#: DUAL condition: a speed threshold fires only when the jump is also this far —
#: immunises the rule against short-Δt GPS jitter (G0/V3: kills 10–11% of the
#: 40–100 kn noise, <1% of the real >100 kn teleports).
JUMP_MIN_DISTANCE_M = 500.0

#: Inland depth tiers (km), from the on-land penetration distribution (G0/V1).
INLAND_IMPLAUSIBLE_KM = 5.0   # the 2–5 km band is near-empty; >5 km is the anomaly cloud
INLAND_SUSPECT_KM = 2.0       # harbour fringe ends here; <2 km is quay/port (clean)

#: AIS SOG 'not available' sentinel (raw 1023 -> 102.3; 1022 -> 102.2). Reported
#: SOG is NEVER an input to any rule; :func:`clean_sog` nulls sentinels wherever
#: reported SOG appears in outputs/evidence.
SOG_SENTINEL_KN = 102.2

#: Ship types that may legitimately sit in the 40–60 kn band.
FAST_CRAFT_TYPES = frozenset({"passenger", "high_speed_craft"})

METHOD = "gnss-quality-v1"

# verdicts (worst wins) --------------------------------------------------------
PLAUSIBLE, SUSPECT, IMPLAUSIBLE = "plausible", "suspect", "implausible"
_VERDICT_ORDER = {PLAUSIBLE: 0, SUSPECT: 1, IMPLAUSIBLE: 2}

# anomaly flags (closed set — must match migration 027's containment CHECK) -----
FLAG_IMPOSSIBLE_SPEED = "impossible_speed"
FLAG_ON_LAND = "on_land"
FLAG_TELEPORT_RETURN = "teleport_return"
FLAG_JAMMING_ZONE = "jamming_zone"   # reserved for G3; never emitted here


@dataclass(frozen=True, slots=True)
class Fix:
    """One vessel position fix: a timestamp (datetime) and lon/lat degrees."""

    ts: Any        # datetime (or anything supporting subtraction -> .total_seconds())
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class PositionQuality:
    """The verdict + evidence for one fix — maps 1:1 onto migration 027's columns.

    ``anomaly_flags`` is a sorted tuple (deterministic order); ``implied_speed_kn``
    is the POSITION-derived incoming speed (evidence), or None at a window's first
    fix. ``method`` is the provenance identity; the runner stamps computed_at.
    """

    position_quality: str
    anomaly_flags: tuple[str, ...]
    implied_speed_kn: float | None
    inland_km: float | None
    method: str = METHOD


def clean_sog(sog: float | None) -> float | None:
    """Reported AIS SOG with the 'not available' sentinel nulled. SOG is never a
    rule input; use this only when echoing reported SOG into outputs/evidence."""
    if sog is None or sog >= SOG_SENTINEL_KN:
        return None
    return sog


def segment_speed_kn(a: Fix, b: Fix) -> tuple[float | None, float | None]:
    """(jump_distance_m, implied_speed_kn) for the segment a -> b.

    Distance is the shared great-circle :func:`geo_area.haversine_km`; speed is
    that distance over Δt. Returns (None, None) when Δt <= 0 (duplicate or
    out-of-order timestamps) — a speed cannot be formed, so no kinematic rule can
    fire on that segment.
    """
    dt_s = (b.ts - a.ts).total_seconds()
    if dt_s <= 0:
        return None, None
    dist_m = haversine_km(a.lat, a.lon, b.lat, b.lon) * 1000.0
    kn = (dist_m / 1000.0 / 1.852) / (dt_s / 3600.0)
    return dist_m, kn


def _exceeds(kn: float | None, dist_m: float | None, threshold_kn: float) -> bool:
    """True iff the segment breaks a speed threshold AND clears the 500 m floor —
    the dual condition. A None speed/distance never exceeds."""
    return (
        kn is not None and dist_m is not None
        and dist_m >= JUMP_MIN_DISTANCE_M and kn > threshold_kn
    )


def speed_verdict(
    in_dist_m: float | None, in_kn: float | None,
    out_dist_m: float | None, out_kn: float | None,
    ship_type: str | None,
) -> tuple[str, set[str]]:
    """R1 on one fix. Verdict + flags from the INCOMING segment (kinematics do not
    apply to a window's first fix, where in_* are None); teleport_return needs both
    the incoming AND outgoing segments to be real >100 kn jumps."""
    verdict, flags = PLAUSIBLE, set()

    if _exceeds(in_kn, in_dist_m, SPEED_HIGH_KN):            # > 60 kn (covers > 100)
        verdict = IMPLAUSIBLE
        flags.add(FLAG_IMPOSSIBLE_SPEED)
    elif _exceeds(in_kn, in_dist_m, SPEED_SUSPECT_KN):      # 40–60 kn
        fast_craft_ok = ship_type in FAST_CRAFT_TYPES and (in_kn or 0) <= FAST_CRAFT_ALLOWANCE_KN
        if not fast_craft_ok:
            verdict = SUSPECT
            flags.add(FLAG_IMPOSSIBLE_SPEED)

    # teleport-and-return: jumped far AND snapped back — the cleanest signature.
    if _exceeds(in_kn, in_dist_m, SPEED_IMPOSSIBLE_KN) and _exceeds(out_kn, out_dist_m, SPEED_IMPOSSIBLE_KN):
        verdict = IMPLAUSIBLE
        flags.add(FLAG_TELEPORT_RETURN)

    return verdict, flags


def inland_verdict(inland_km: float | None) -> tuple[str, set[str]]:
    """R2 on one fix, from its inland depth (None at sea). Independent of R1 — the
    only rule that catches the coherent-offset pattern."""
    if inland_km is None:
        return PLAUSIBLE, set()
    if inland_km > INLAND_IMPLAUSIBLE_KM:
        return IMPLAUSIBLE, {FLAG_ON_LAND}
    if inland_km >= INLAND_SUSPECT_KM:
        return SUSPECT, {FLAG_ON_LAND}
    return PLAUSIBLE, set()


def _worst(a: str, b: str) -> str:
    return a if _VERDICT_ORDER[a] >= _VERDICT_ORDER[b] else b


def classify_fix(
    prev: Fix | None,
    cur: Fix,
    nxt: Fix | None,
    *,
    ship_type: str | None = None,
    inland_km: float | None = None,
) -> PositionQuality:
    """Classify one fix from its neighbours + evidence, composing R1 and R2.

    ``prev`` is None at a processing window's first fix (no incoming segment —
    kinematics skipped, only R2 applies); ``nxt`` is None at the last fix (no
    teleport_return possible). ``inland_km`` is the fix's depth inside the land
    mass (from :meth:`geo_area.AreaOfInterest.inland_depth_km`, None at sea),
    computed by the runner and passed in so this module stays DB- and asset-free.
    ``ship_type`` is the M2b static type (fast-craft allowance); passed in, never
    looked up here.
    """
    in_dist_m, in_kn = segment_speed_kn(prev, cur) if prev is not None else (None, None)
    out_dist_m, out_kn = segment_speed_kn(cur, nxt) if nxt is not None else (None, None)

    sv, sf = speed_verdict(in_dist_m, in_kn, out_dist_m, out_kn, ship_type)
    iv, if_ = inland_verdict(inland_km)

    return PositionQuality(
        position_quality=_worst(sv, iv),
        anomaly_flags=tuple(sorted(sf | if_)),
        implied_speed_kn=in_kn,            # position-derived incoming speed (evidence)
        inland_km=inland_km,
        method=METHOD,
    )


#: Human-facing caveat carried on the surface — an implausible position is a
#: data-quality event, not proof of intent, and usually marks a JAMMING VICTIM.
QUALITY_CAVEAT = (
    "Data-quality event, not proof of intent: an implausible position means the "
    "GNSS fix is wrong (deliberate spoofing, area jamming, a GPS glitch, or an AIS "
    "data error) — a vessel broadcasting spoofed positions is usually a VICTIM of "
    "area jamming, not an evader. This flags implausibility so downstream layers "
    "can exclude it; it does not accuse."
)
