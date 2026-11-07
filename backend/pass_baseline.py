"""BASELINE-1 — is this predicted pass USUAL for this object? PURE: numbers in, verdict out.

⛔ THE POINT IS TO MAKE "UNUSUAL" A MEASUREMENT INSTEAD OF A WORD. A reviewer asked the bot
to say «ضمن النمط المعتاد» rather than list fourteen passes — right about the symptom, and
the sentence was unsupportable: measured before building, `satellite_passes` keeps no
history (one distinct `computed_at`) and `satellite_elements` keeps no element history
(1-2 epochs per satellite). "Usual" had no referent in this database at all. Migration 041
and `scripts/build_pass_baseline.py` create one by back-propagating the current element;
this module is the only place that turns it into a judgement.

⛔ `UNKNOWN` IS A REAL VERDICT AND MUST REACH THE READER AS ITSELF. The failure mode this
module exists to prevent is a missing or thin baseline quietly rendering as "routine" —
that is how a system starts asserting normality it never measured, which is worse than the
noise it was meant to fix. Every path that cannot support a verdict returns UNKNOWN, and
`ROUTINE` is returned only when there is enough sample to earn it.

⚠ THIS IS A GEOMETRY JUDGEMENT, NOT A THREAT JUDGEMENT, and it is deliberately a THIRD
axis, separate from both existing ones. `severity` answers "how dangerous is this claim?"
and stays INFO for every satellite pass — the standing commitment that presence is
"informational, NEVER a threat" is untouched by anything here. `importance` orders a
country card. This answers only "is this pass typical for THIS object's orbit?", and a
pass being atypical is not a pass being dangerous. Do not colour by it and do not sum it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --- verdicts ---------------------------------------------------------------------------
UNKNOWN, ROUTINE, NOTABLE = "unknown", "routine", "notable"

#: Minimum back-propagated passes before "routine" may be claimed at all.
#: ⛔ A floor, not a tuning knob. Two passes is not a distribution, and a p10 computed from
#: it is noise wearing a statistic's clothes. Below this the verdict is UNKNOWN — the
#: honest answer is "not enough of this object's own history to compare against", and the
#: message says exactly that.
#: 8 ≈ four days of a twice-daily LEO crossing, the least that makes a 10th percentile mean
#: anything at all.
MIN_SAMPLE = 8

#: How much closer than its own p10 a pass must be before it is called NOTABLE.
#: 1.0 = "at or inside the 10th percentile of its own passes". Stated as a ratio rather
#: than a distance because the scale differs by orders of magnitude across the catalogue:
#: a 40 km approach is unremarkable for a LEO recon satellite and impossible for a GEO.
NOTABLE_RATIO = 1.0

#: A pass crossing an AOI country ABSENT from the object's usual set is notable in a
#: different way from a merely closer one — a change of geometry rather than of degree.
FLAG_NEW_COUNTRY = True


@dataclass(frozen=True, slots=True)
class Baseline:
    """One satellite's back-propagated AOI-crossing geometry (migration 041)."""
    n_passes: int
    window_days: int
    passes_per_day: float
    closest_p10: float | None = None
    closest_median: float | None = None
    closest_min: float | None = None
    usual_countries: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """Enough sample to support any verdict other than UNKNOWN."""
        return self.n_passes >= MIN_SAMPLE


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str
    #: Machine-readable cause — tests assert this, and the renderer picks a sentence from
    #: it rather than re-deriving the reasoning in prose.
    reason: str
    #: What "usual" IS for this object, when known — so a message can state the comparison
    #: rather than only its outcome. None when the baseline cannot support it.
    usual_closest_km: float | None = None


def classify(closest_km: float | None, countries: list[str] | None,
             baseline: Baseline | None, is_persistent: bool = False) -> Verdict:
    """Is this pass typical for this object?

    ⚠ Ordered so that the WEAKEST evidence can never overrule the strongest absence: every
    branch that lacks its input returns UNKNOWN before any comparison is attempted. A
    missing `closest_km` with a usable baseline is still UNKNOWN on the distance axis — it
    is not "not closer than usual"."""
    # ⛔ GEOSTATIONARY IS ROUTINE BY CONSTRUCTION, AND MUST NOT READ AS "UNKNOWN".
    # `passes_derive.classify_passes` emits AT MOST ONE persistent row per window for a
    # geosync object, because its sub-point is ~fixed in longitude — it is STATIONED over
    # the band, not transiting it. Measured on the first baseline run: 7 of 13 objects came
    # back with n_passes = 1 (SKYNET 4C, SYRACUSE 3A/4A, RADUGA-1M 3, ATHENA-FIDUS, COSMOS
    # 2520, TIBA-1) — every one of them geosync, and every one would have been reported as
    # "not enough history to compare" by the sample floor below.
    #
    # That would have been the wrong answer twice: a comms satellite parked over the region
    # is the most routine thing in the catalogue, and its distance CANNOT vary from pass to
    # pass the way a LEO transit's does, so there is nothing for a distribution to say. The
    # honest verdict is "stationed", and it is decided before any sample counting.
    if is_persistent:
        return Verdict(ROUTINE, "geostationary")

    if baseline is None:
        return Verdict(UNKNOWN, "no_baseline")
    if not baseline.usable:
        return Verdict(UNKNOWN, "thin_baseline")

    # A country its track does not usually cross — a change in geometry, not degree.
    if FLAG_NEW_COUNTRY and countries and baseline.usual_countries:
        new = [c for c in countries if c not in baseline.usual_countries]
        if new:
            return Verdict(NOTABLE, "new_country", baseline.closest_median)

    if closest_km is None or baseline.closest_p10 is None:
        # The object is known and its crossings are routine in count, but this particular
        # pass carries no distance to compare. Say so; do not infer either way.
        return Verdict(UNKNOWN, "no_distance", baseline.closest_median)

    if closest_km <= baseline.closest_p10 * NOTABLE_RATIO:
        return Verdict(NOTABLE, "closer_than_usual", baseline.closest_median)

    return Verdict(ROUTINE, "within_usual", baseline.closest_median)


def aggregate(closest_values: list[float], countries: list[list[str]],
              window_days: int) -> dict[str, Any]:
    """Back-propagated passes -> the row migration 041 stores. PURE.

    ⚠ Quantiles by NEAREST-RANK on the sorted sample, not by interpolation. With samples
    this small (often 8-30) an interpolated p10 invents a value that lies between two real
    passes and therefore describes none of them; nearest-rank always returns a distance
    this object's track actually reached.

    ⚠ `usual_countries` is the UNION over the window, not the intersection: an object whose
    track alternates between Turkey and Iraq usually crosses both, and intersecting would
    make every pass look like a new country."""
    n = len(closest_values)
    vals = sorted(v for v in closest_values if v is not None)

    def q(p: float) -> float | None:
        if not vals:
            return None
        i = max(0, min(len(vals) - 1, int(round(p * (len(vals) - 1)))))
        return round(vals[i], 1)

    usual: list[str] = []
    for cs in countries:
        for c in (cs or []):
            if c and c not in usual:
                usual.append(c)

    return {
        "window_days": window_days,
        "n_passes": n,
        "passes_per_day": round(n / window_days, 3) if window_days else 0.0,
        "closest_min": q(0.0),
        "closest_p10": q(0.10),
        "closest_median": q(0.50),
        "usual_countries": sorted(usual),
    }
