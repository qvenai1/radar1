"""Pure draught-delta classification — DRAUGHT-1 (corroborating annotation on an A2
rendezvous, NOT a standalone detector). Turns one encounter participant's
draught-bearing fixes + the encounter window into an HONEST three-state verdict on
whether its draught changed across the meeting. No I/O: the rule functions take plain
data in and return plain data out, so they are unit-testable without a DB and
deterministic (same inputs => identical outputs). The A2 runner owns all I/O and
writes the result into the existing rendezvous `detail` (no migration — the
established JSONB-subtype axis, as A3 did with `change_type`).

THE VALUE (DRAUGHT-pre): a draught change tied to a rendezvous is transfer-shaped —
two vessels meet at sea and one rides higher / the other lower afterwards, what a
ship-to-ship transfer looks like in the data. Neither signal alone is strong; the
conjunction is. This is corroboration on an existing lead, never a new filter and
never a re-ranking of A2.

THREE STATES, NEVER TWO — the failure this layer must not commit. With 76% of vessels
never updating draught and 52.4% of the timeline in outage, MOST encounters are
`insufficient_data`. Reporting those as `unchanged` would silently assert "no
transfer" about cases we are BLIND to. So:
  * changed          — a real reading before AND after, delta beyond the noise floor.
  * unchanged        — a real reading before AND after, no meaningful change.
  * insufficient_data— we cannot tell, with the reason:
      - no_draught_reported : the vessel never broadcast a usable draught.
      - stale_single_value  : the vessel broadcast exactly ONE distinct draught for
                              its whole history — it CANNOT evidence a change (the
                              false-negative floor). Treated as insufficient, never
                              `unchanged`.
      - no_before / no_after: no usable reading within the window on that side.

WINDOW = ±48 h (module constant), chosen from the measured tradeoff (DRAUGHT-pre):
coverage was 17/35 at ±24 h and only 22/35 at ±7 d — loosening buys ~five encounters
while DESTROYING attributability (a draught change over a week is as likely a port
call as the encounter). ±48 h brackets a multi-hour at-sea encounter (STS takes
hours) plus its approach/departure, without a week of intervening port calls.

NOISE FLOOR = 0.5 m. Draught is crew-entered at 0.1 m (decimetre) resolution, so a
sub-0.5 m delta is within rounding / manual-entry noise; 0.5 m = 5× the resolution
(the DRAUGHT-pre threshold). `transfer_shaped` requires OPPOSITE-SIGN deltas on the
two participants, BOTH beyond the floor — one rides higher, the other lower. NOTE:
DRAUGHT-pre's single live case (HIGH FREEDOM +0.6 / MAPLE CASTOR −2.0, 2026-07-12) is
MARGINAL on one side (+0.6 m is just above the floor and the pair is not mass-balanced
— a real transfer conserves cargo, so the two draught shifts should be comparable).
It is used as a test fixture; the floor is NOT tuned to make it pass. Under this
honest floor it survives, so the current live yield is ~1/35 — a guard that will
rarely fire, documented as such.

THE HONEST LIMIT (carried on every surface). Draught is SELF-REPORTED and crew-entered
— the same class of manually-reported field the project already distrusts for
nav_status ("don't trust it; verify actual SOG"). A change proves nothing alone, and
even conjoined with a rendezvous it is a LEAD, NOT PROOF of transfer (legitimate
explanations: ballast operations, bunkering, a crew belatedly updating a stale field,
reporting error). Two structural blind spots measured in DRAUGHT-pre and unliftable
from AIS alone: 76% of vessels never update draught (a real transfer on a
stale-draught vessel is invisible — a false-negative floor), and the feed's 52.4%
outage fragmentation prevents reliable before/after bracketing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

METHOD = "draught-delta-v1"

#: ±window (hours) for the nearest before/after reading — see the module docstring.
WINDOW_H = 48.0
#: Minimum |delta| (metres) for a change to be real, not decimetre-entry noise.
NOISE_FLOOR_M = 0.5

# states -----------------------------------------------------------------------
CHANGED, UNCHANGED, INSUFFICIENT = "changed", "unchanged", "insufficient_data"
# insufficient_data reasons ----------------------------------------------------
NO_DRAUGHT = "no_draught_reported"
STALE_SINGLE = "stale_single_value"
NO_BEFORE = "no_before"
NO_AFTER = "no_after"


@dataclass(frozen=True, slots=True)
class DraughtObs:
    """One usable draught reading: a timestamp + a positive draught (metres)."""

    ts: Any        # datetime (supports subtraction → .total_seconds())
    draught: float


@dataclass(frozen=True, slots=True)
class ParticipantDraught:
    """One participant's verdict across the encounter — maps onto the `detail` shape.

    ``state`` is changed | unchanged | insufficient_data; ``reason`` is set only for
    insufficient_data. ``before``/``after`` are the nearest usable readings (metres,
    or None when that side is missing); ``delta`` = after − before (None unless both
    sides exist AND the vessel can evidence a change).
    """

    state: str
    reason: str | None
    before: float | None
    after: float | None
    delta: float | None


def _nearest_before(obs: list[DraughtObs], ts: Any, window_h: float) -> DraughtObs | None:
    """The latest reading at/before ts and within window_h hours (obs sorted by ts)."""
    lo = None
    for o in obs:                                  # obs is small per vessel; linear is fine
        if o.ts <= ts and (ts - o.ts).total_seconds() <= window_h * 3600.0:
            if lo is None or o.ts > lo.ts:
                lo = o
    return lo


def _nearest_after(obs: list[DraughtObs], ts: Any, window_h: float) -> DraughtObs | None:
    """The earliest reading at/after ts and within window_h hours."""
    hi = None
    for o in obs:
        if o.ts >= ts and (o.ts - ts).total_seconds() <= window_h * 3600.0:
            if hi is None or o.ts < hi.ts:
                hi = o
    return hi


def classify_participant(
    obs: list[DraughtObs],
    start_ts: Any,
    end_ts: Any,
    *,
    window_h: float = WINDOW_H,
    floor_m: float = NOISE_FLOOR_M,
) -> ParticipantDraught:
    """Classify one participant across [start_ts, end_ts]. ``obs`` is that vessel's
    usable (draught > 0) readings over its WHOLE history — needed for the
    stale-single-value test. Order is deliberate: a vessel that never reports, or only
    ever reports one value, is insufficient BEFORE window availability is considered."""
    usable = [o for o in obs if o.draught and o.draught > 0]
    if not usable:
        return ParticipantDraught(INSUFFICIENT, NO_DRAUGHT, None, None, None)

    # a vessel with exactly one distinct draught over its whole history cannot
    # evidence a change — treat as insufficient (NEVER as unchanged).
    if len({round(o.draught, 1) for o in usable}) == 1:
        val = round(usable[0].draught, 1)
        return ParticipantDraught(INSUFFICIENT, STALE_SINGLE, val, val, None)

    before = _nearest_before(usable, start_ts, window_h)
    after = _nearest_after(usable, end_ts, window_h)
    if before is None:
        return ParticipantDraught(INSUFFICIENT, NO_BEFORE, None,
                                  round(after.draught, 1) if after else None, None)
    if after is None:
        return ParticipantDraught(INSUFFICIENT, NO_AFTER, round(before.draught, 1), None, None)

    delta = round(after.draught - before.draught, 1)
    b, a = round(before.draught, 1), round(after.draught, 1)
    if abs(delta) < floor_m:
        return ParticipantDraught(UNCHANGED, None, b, a, delta)
    return ParticipantDraught(CHANGED, None, b, a, delta)


def is_transfer_shaped(primary: ParticipantDraught, partner: ParticipantDraught) -> bool:
    """True when BOTH participants CHANGED with OPPOSITE-SIGN deltas — one rode higher,
    the other lower. Same-sign changes (both loading at port) are NOT transfer-shaped.
    Either side insufficient/unchanged => not transfer-shaped (never asserted on a
    side we are blind to)."""
    if primary.state != CHANGED or partner.state != CHANGED:
        return False
    dp, dq = primary.delta, partner.delta
    return dp is not None and dq is not None and dp * dq < 0


def _obs_dict(p: ParticipantDraught) -> dict[str, Any]:
    """A participant verdict as the JSONB `detail.draught.{primary,partner}` shape."""
    return {"state": p.state, "reason": p.reason,
            "before": p.before, "after": p.after, "delta": p.delta}


def annotation(primary: ParticipantDraught, partner: ParticipantDraught) -> dict[str, Any]:
    """The full `detail.draught` sub-object the A2 runner writes — both participants'
    verdicts + the top-level transfer_shaped (only when both sides are evidenced) +
    provenance. Deterministic; the runner adds nothing."""
    return {
        "primary": _obs_dict(primary),
        "partner": _obs_dict(partner),
        "transfer_shaped": is_transfer_shaped(primary, partner),
        "window_h": WINDOW_H,
        "noise_floor_m": NOISE_FLOOR_M,
        "method": METHOD,
    }


#: Human-facing caveat carried on the surface — corroboration, never proof.
DRAUGHT_CAVEAT = (
    "Draught is crew-entered and self-reported (the class the project distrusts for "
    "nav_status). A change proves nothing alone; conjoined with a rendezvous it is a "
    "LEAD, not proof of transfer (ballast, bunkering, a crew updating a stale field, "
    "reporting error). Blind spots: 76% of vessels never update draught (a real "
    "transfer on a stale-draught vessel is invisible), and feed-outage fragmentation "
    "breaks before/after bracketing. 'draught not evidenced' means we cannot tell — "
    "NOT that no change occurred."
)
