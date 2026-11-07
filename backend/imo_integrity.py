"""Pure IMO-integrity classification — the heart of IMO-1 (identity-quality as a
per-identity verdict). Turns a stored IMO (and, for a shared IMO, the set of MMSIs
that carry it) into an HONEST verdict ('valid' | 'checksum_fail' | 'malformed' |
'contested') plus the reasons that fired. No I/O: the rule functions take plain data
in and return plain data out, so they are unit-testable without a DB and
deterministic (same inputs => identical outputs, always). The backfill runner owns
all I/O and stamps ``imo_computed_at``.

The IMO number is the spine the composite (migrations 026/029/036) stitches evidence
on — COALESCE(e.imo, vi.imo, e.mmsi) — but it is SELF-REPORTED AIS static (type 5),
therefore spoofable. THE HONEST LIMIT: a valid checksum does NOT prove the vessel
owns that IMO. Without an authoritative registry — and closed commercial sources are
excluded by project policy — this layer verifies INTERNAL CONSISTENCY
(well-formedness, non-conflicting attribution, dimensional coherence), NEVER
OWNERSHIP. A fabricated IMO with a correct check digit passes. This is the
lead-not-proof tradition; confirming true identity would need the unbuilt M2c
external-registry layer.

Provenance (IMO-1 Step-0, measured in the immutable JSONL archive BEFORE our parser):
malformed IMO values arrive ALREADY malformed from upstream — a vessel persistently
broadcasts e.g. 0x0A000000 for days. They are RECEIVED attributes, not our decode
bug; we record and flag them (the parser stays honest, IMO-1 Step 4), never null them
at ingest.

TWO independent axes (a caller composes them; worst verdict wins):

  VALUE — the IMO string alone. Well-formedness (exactly 7 digits, numeric, in
    [1000000, 9999999], not a placeholder) then the ISO check digit: the first six
    digits weighted 7,6,5,4,3,2, summed; the last digit of that sum must equal the
    seventh digit. malformed > checksum_fail > valid.

  SHARED — one IMO carried by >1 MMSI (the caller passes each member's time range +
    name + dimensions; this module stays DB-free):
      * a CLEAN temporal switch (no overlap beyond the AIS-slop tolerance) =>
        'reflag_clean', verdict VALID. This is the composite's INTENDED stitching
        (a sold/re-flagged hull keeps its IMO under a new MMSI); A3 already records
        it as an identity_change. It is a benign annotation, not an anomaly.
      * TIME-OVERLAPPING MMSIs on one IMO => 'mmsi_collision', verdict CONTESTED —
        two hulls claiming one IMO at once, the false-attribution signature A3
        STRUCTURALLY SILENCES (identity_change.py rejects heavy overlap as "a likely
        IMO collision, not a re-flag" and emits nothing). This is the signal this
        layer uniquely adds.
      * dimension disagreement beyond the threshold => 'dimension_conflict', but
        CORROBORATION ONLY, on the collision path — never fires alone (IMO-pre: 34%
        dimension coverage, ZERO observed contradictions, threshold under-determined,
        so it can strengthen a collision but must not manufacture one).

SIZING (IMO-pre + IMO-1 Step-0 archive scan): 46/2,896 IMOs are questionable (36
decode-artifact malformed + 10 checksum-fail); 14 shared IMOs, ALL clean switches
(0 collisions); the immutable archive shows 0 MMSIs broadcasting >1 IMO. So
'mmsi_collision' and 'mmsi_multi_imo' are LATENT guards — real code, zero current
instances — carried like G1's jamming_zone was before G3 emitted it.

Verdict composition (worst wins): CONTESTED > MALFORMED > CHECKSUM_FAIL > VALID;
flags are the UNION. A checked-and-clean IMO is VALID with empty flags and the
runner still stamps imo_method (so "checked clean" != "not yet checked"). Empty
flags serialise to NULL (the migration-035 storage rule). imo_computed_at is stamped
by the runner, not this module (the G1 precedent).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

METHOD = "imo-integrity-v1"

# --- value axis: well-formedness + check digit --------------------------------

#: A valid IMO is exactly 7 significant digits — the numeric range below.
IMO_MIN, IMO_MAX = 1_000_000, 9_999_999
IMO_LENGTH = 7

#: Known placeholder / sequence patterns that are structurally 7-digit but carry no
#: real hull identity (sequences + repeated-digit; the check-digit would even accept
#: some). Extensible, mirroring identity_change.is_placeholder_mmsi.
PLACEHOLDER_IMOS = frozenset({"1234567", "0000000", "1111111", "1234560", "7654321"})

# --- shared axis: re-flag vs collision ---------------------------------------

#: A re-flag transition may show at most this much backward overlap (hours) between
#: the old MMSI's last fix and the new MMSI's first and still count as a CLEAN switch
#: (AIS timing slop). Heavier overlap => a collision (two hulls at once). Kept
#: identical to identity_change.REFLAG_MAX_OVERLAP_H so the two layers agree on what
#: "clean" means.
REFLAG_MAX_OVERLAP_H = 6.0

#: Length disagreement (metres) above which two members are plausibly DIFFERENT
#: hulls. UNDER-DETERMINED by data (IMO-pre: 3 comparable shared-IMO pairs, all
#: Δ=0 m) — so this is a conservative floor used ONLY to corroborate a collision,
#: never to raise one. AIS dimensions are integer metres (reference-point offsets),
#: so single-metre spreads are reporting noise; 20 m separates noise from two ships.
DIMENSION_CONFLICT_M = 20.0

# verdicts (worst wins) --------------------------------------------------------
VALID, CHECKSUM_FAIL, MALFORMED, CONTESTED = "valid", "checksum_fail", "malformed", "contested"
_VERDICT_ORDER = {VALID: 0, CHECKSUM_FAIL: 1, MALFORMED: 2, CONTESTED: 3}

# flags (closed set — must match migration 035's containment CHECK) ------------
FLAG_CHECKSUM_FAIL = "checksum_fail"
FLAG_WRONG_LENGTH = "wrong_length"
FLAG_NON_NUMERIC = "non_numeric"
FLAG_PLACEHOLDER = "placeholder"
FLAG_REFLAG_CLEAN = "reflag_clean"        # benign: a clean re-flag (verdict stays VALID)
FLAG_MMSI_COLLISION = "mmsi_collision"    # latent (0 today)
FLAG_MMSI_MULTI_IMO = "mmsi_multi_imo"    # latent (0 today)
FLAG_DIMENSION_CONFLICT = "dimension_conflict"

#: The only flag a VALID verdict may carry (mirrors migration 035's consistency CHECK).
_BENIGN_FLAGS = frozenset({FLAG_REFLAG_CLEAN})


@dataclass(frozen=True, slots=True)
class Member:
    """One MMSI that carries a given IMO, with the evidence the shared-axis needs.

    ``first_ts`` / ``last_ts`` bound this MMSI's activity (datetimes, from
    vessel_states); ``length_m`` / ``beam_m`` are the M2b static dimensions (None or
    <= 0 = unusable). The module never does I/O — the runner fills these in.
    """

    mmsi: str
    first_ts: Any = None
    last_ts: Any = None
    name: str | None = None
    length_m: float | None = None
    beam_m: float | None = None


@dataclass(frozen=True, slots=True)
class ImoIntegrity:
    """The verdict + evidence for one identity — maps 1:1 onto migration 035's
    columns. ``imo_flags`` is a sorted tuple (deterministic order); ``imo_quality``
    is None for a NULL/absent IMO (nothing to check). ``method`` is the provenance
    identity; the runner stamps computed_at."""

    imo_quality: str | None
    imo_flags: tuple[str, ...]
    method: str | None = METHOD


def _worst(a: str, b: str) -> str:
    return a if _VERDICT_ORDER[a] >= _VERDICT_ORDER[b] else b


def worst_quality(qualities) -> str | None:
    """The most-severe non-None imo_quality among several (a hull's identities), or
    None if none are checked. Used by consumers to annotate a hull's spine."""
    best = None
    for q in qualities:
        if q is None:
            continue
        if best is None or _VERDICT_ORDER.get(q, 0) > _VERDICT_ORDER.get(best, 0):
            best = q
    return best


# --- value axis ---------------------------------------------------------------

def wellformed_flags(imo: str) -> set[str]:
    """The malformed flags for an IMO STRING (empty set => well-formed 7-digit in
    range). Exactly one defect is reported, most-structural first: non-numeric >
    wrong-length/range > placeholder."""
    if not imo.isdigit():
        return {FLAG_NON_NUMERIC}
    if len(imo) != IMO_LENGTH or not (IMO_MIN <= int(imo) <= IMO_MAX):
        return {FLAG_WRONG_LENGTH}
    if imo in PLACEHOLDER_IMOS or len(set(imo)) == 1:
        return {FLAG_PLACEHOLDER}
    return set()


def checksum_ok(imo: str) -> bool:
    """ISO IMO check digit: first six digits * weights 7,6,5,4,3,2, summed; the last
    digit of that sum must equal the seventh digit. Precondition: a well-formed
    7-digit numeric string (see :func:`wellformed_flags`)."""
    d = [int(c) for c in imo]
    return sum(d[i] * (IMO_LENGTH - i) for i in range(6)) % 10 == d[6]


def classify_value(imo: str | None) -> tuple[str | None, set[str]]:
    """Verdict + flags for the IMO STRING alone. None => (None, empty): a NULL/absent
    IMO is not-checked, never 'valid' (there is nothing to verify)."""
    if imo is None or imo == "":
        return None, set()
    flags = wellformed_flags(imo)
    if flags:
        return MALFORMED, flags
    if not checksum_ok(imo):
        return CHECKSUM_FAIL, {FLAG_CHECKSUM_FAIL}
    return VALID, set()


# --- shared axis --------------------------------------------------------------

def _any_time_overlap(members: list[Member]) -> bool:
    """True if any two members' [first_ts, last_ts] windows overlap by more than the
    AIS-slop tolerance (=> a collision, not a clean switch). Members lacking a time
    range are ignored (cannot prove overlap => assume clean)."""
    iv = sorted((m.first_ts, m.last_ts) for m in members if m.first_ts and m.last_ts)
    for i in range(len(iv) - 1):
        overlap_h = (iv[i][1] - iv[i + 1][0]).total_seconds() / 3600.0
        if overlap_h > REFLAG_MAX_OVERLAP_H:
            return True
    return False


def _dimensions_conflict(members: list[Member]) -> bool:
    """True if the members' usable lengths spread beyond the threshold — evidence of
    two different hulls. Fewer than two usable lengths => cannot judge => False."""
    lens = [m.length_m for m in members if m.length_m and m.length_m > 0]
    if len(lens) < 2:
        return False
    return max(lens) - min(lens) > DIMENSION_CONFLICT_M


def collision_window(members: list[Member]):
    """The (start, end) of the FIRST time-overlap between two members' windows (the
    collision window), or None if there is no overlap beyond the slop. Members
    without a time range are ignored. Deterministic (sorted by first_ts)."""
    timed = sorted(((m.first_ts, m.last_ts) for m in members if m.first_ts and m.last_ts))
    for i in range(len(timed) - 1):
        f_i, l_i = timed[i]
        f_j, l_j = timed[i + 1]
        if (l_i - f_j).total_seconds() / 3600.0 > REFLAG_MAX_OVERLAP_H:
            return f_j, min(l_i, l_j)          # overlap starts at the later first fix
    return None


def build_collision_event(imo: str, members: list[Member]) -> dict | None:
    """If ``members`` form a genuine mmsi_collision, return the maritime_events row
    FIELDS for an event_type='imo_integrity' row (classification='candidate'; the
    subtype + conflicting MMSIs + overlap window + dimension evidence in detail);
    else None. Pure + deterministic — the runner stamps computed_at and writes with
    ON CONFLICT DO UPDATE. mmsi is the lexicographically-first colliding MMSI (a
    stable PK representative); the event keys on the contested IMO, so it attaches to
    that (contested) hull in the composite as an imo_integrity pattern type."""
    verdict, flags = classify_shared(members)
    if verdict != CONTESTED or FLAG_MMSI_COLLISION not in flags:
        return None
    win = collision_window(members)
    if win is None:
        return None
    start, end = win
    mmsis = sorted(m.mmsi for m in members)
    lengths = [m.length_m for m in members if m.length_m and m.length_m > 0]
    return {
        "event_type": "imo_integrity",
        "mmsi": mmsis[0],
        "imo": imo,
        "start_ts": start,
        "end_ts": end,
        "classification": "candidate",
        "method": METHOD,
        "detail": {
            "integrity_type": "mmsi_collision",
            "conflicting_mmsis": mmsis,
            "overlap_start": start.isoformat() if hasattr(start, "isoformat") else start,
            "overlap_end": end.isoformat() if hasattr(end, "isoformat") else end,
            "dimension_conflict": FLAG_DIMENSION_CONFLICT in flags,
            "lengths_m": lengths,
        },
    }


def classify_shared(members: list[Member]) -> tuple[str | None, set[str]]:
    """Classify one IMO carried by a SET of MMSIs. A single-member (or empty) group
    has nothing to say => (None, empty). Overlapping windows => collision (CONTESTED,
    + dimension_conflict corroboration if the dims also disagree); otherwise a clean
    re-flag (VALID + the benign reflag_clean annotation)."""
    if len(members) < 2:
        return None, set()
    if _any_time_overlap(members):
        flags = {FLAG_MMSI_COLLISION}
        if _dimensions_conflict(members):
            flags.add(FLAG_DIMENSION_CONFLICT)   # corroboration only, on the collision path
        return CONTESTED, flags
    return VALID, {FLAG_REFLAG_CLEAN}


# --- composition (the runner's per-identity entry point) ----------------------

def classify_identity(
    imo: str | None,
    *,
    shared: list[Member] | None = None,
    mmsi_multi_imo: bool = False,
) -> ImoIntegrity:
    """Compose the full verdict for one identity. ``shared`` is the members of this
    IMO's group when it is carried by >1 MMSI (else None/single). ``mmsi_multi_imo``
    is the latent guard the runner sets when this MMSI has broadcast >1 distinct IMO
    (0 in all data measured). Worst verdict wins; flags are the union.

    A NULL/absent IMO returns quality=None, flags=(), method=None — "nothing to
    check" (mirrors 027's NULL-position rows), so the runner leaves imo_method NULL
    for it, keeping imo_method IS NULL <=> imo IS NULL."""
    if imo is None or imo == "":
        return ImoIntegrity(imo_quality=None, imo_flags=(), method=None)

    verdict, flags = classify_value(imo)
    assert verdict is not None  # imo is present on this path; classify_value(None) is the only None case

    if shared and len(shared) >= 2:
        sv, sf = classify_shared(shared)
        if sv is not None:
            verdict = _worst(verdict, sv)
            flags |= sf

    if mmsi_multi_imo:
        verdict = _worst(verdict, CONTESTED)
        flags.add(FLAG_MMSI_MULTI_IMO)

    # a VALID verdict may keep only benign flags (a stray anomaly flag would mean a
    # non-valid verdict already won); this guard makes the invariant explicit.
    if verdict == VALID:
        flags &= _BENIGN_FLAGS

    return ImoIntegrity(imo_quality=verdict, imo_flags=tuple(sorted(flags)), method=METHOD)


#: Human-facing caveat carried on the surface (IMO-2 consumers) — an integrity flag
#: is internal consistency, NOT ownership: a valid check digit does not prove the
#: vessel owns the IMO, and a re-flag is usually legal.
INTEGRITY_CAVEAT = (
    "Internal-consistency check, not ownership: a valid IMO check digit proves the "
    "number is well-formed, NOT that this vessel owns it (a fabricated IMO with a "
    "correct check digit passes). Confirming true identity needs an authoritative "
    "registry, which we do not have. A shared IMO under a clean switch is a routine "
    "re-flag; only a time-overlap collision is an attribution anomaly. Flags prioritise "
    "for review; they do not accuse."
)
