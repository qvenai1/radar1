"""Pure identity-change logic — A3 (a vessel's broadcast identity changing).

The pattern the M2b IMO spine was built to enable: IMO is the permanent hull id,
while name / flag / MMSI can change. Two change types:
  * RE-FLAG — same IMO under different MMSIs (MMSI+flag change, IMO persists).
    The shadow-fleet signature; confidence comes from the IMO linkage + a CLEAN
    temporal switch, not from row count.
  * RENAME / RETYPE — the name or ship_type broadcast under ONE MMSI changing
    over time (IMO stable).

The value, like A1/A2, is CONFOUND REMOVAL + HONEST PRIORITISATION. Raw name
variation is mostly NOISE — corrupted statics, single-message blips, spacing/typo
variants, placeholder MMSIs. A change is only CONFIRMED when the new value is
SUSTAINED, is NOT a near-duplicate of the old, and is NOT garbage. And a rename /
re-flag is usually LEGAL (ships are sold/renamed/re-flagged routinely) — so we
RANK (grey/sanctions flag, correlation with A1/A2, multiple changes → higher),
never accuse.

Everything here is pure (no I/O) and unit-testable.
"""

from __future__ import annotations

import re
from typing import Any

from .rendezvous import FLAGS_OF_CONVENIENCE

#: A same-MMSI rename/retype is CONFIRMED only if the new value is carried by at
#: least this many archived rows. From the assessment: real renames carry >=20
#: rows (EUROSAILOR 238 / ROSA 138), while corruption/blips carry 1-2 (COSMO SAIL
#: 2, "ONE ECPEJP  P D" 1). 10 sits cleanly between.
MIN_SUSTAINED_ROWS = 10
#: A re-flag uses a LOWER row floor — its confidence is the IMO match + the clean
#: switch, not row count (assessment re-flag IMO 9232931 had 3/4 rows). Require
#: >=2 fixes per identity to reject a single-fix ghost.
MIN_REFLAG_ROWS = 2
#: Normalized-name similarity at/above which two names are the SAME (drop spacing/
#: typo noise like "EL QAHER I" / "EL QAHERI").
NAME_SIMILARITY = 0.85
#: A re-flag transition may show at most this much backward overlap (hours) and
#: still count as a clean switch (AIS timing slop); heavier overlap is a likely
#: IMO collision (two different vessels), not a re-flag.
REFLAG_MAX_OVERLAP_H = 6.0

METHOD = "identity-change-detector-v1"
HIGH, MEDIUM, LOW = "high", "medium", "low"

CHANGE_REFLAG, CHANGE_RENAME, CHANGE_RETYPE = "reflag", "rename", "retype"

#: Sanctions-associated registries — extensible. A flag change into/out of these
#: (or a flag-of-convenience) escalates a re-flag. A prior, not a judgement.
SANCTIONS_FLAGS = frozenset({"Russia"})
GREY_FLAGS = FLAGS_OF_CONVENIENCE | SANCTIONS_FLAGS


def _norm(s: str | None) -> str:
    """Normalise a name for comparison: alphanumerics only, uppercase."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def near_duplicate(a: str | None, b: str | None, threshold: float = NAME_SIMILARITY) -> bool:
    """True if two names are effectively the SAME (spacing/punctuation only, or a
    typo within (1-threshold) normalized edit distance) — i.e. NOT a real change."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return True                       # empty/garbage side — not a real change
    if na == nb:
        return True                       # spacing/punctuation only
    ratio = 1.0 - _levenshtein(na, nb) / max(len(na), len(nb))
    return ratio >= threshold


def is_garbage_name(s: str | None) -> bool:
    """True for an obviously corrupt/non-name string (control chars, mostly
    symbols, or too short after normalisation)."""
    if not s or not s.strip():
        return True
    if any(ord(ch) < 32 for ch in s):
        return True
    if re.search(r"[^A-Za-z0-9 ]{3,}", s):  # a run of >=3 symbols => corruption (e.g. "E,=_)")
        return True
    letters = sum(ch.isalnum() or ch.isspace() for ch in s)
    if letters / len(s) < 0.7:            # >30% symbols overall
        return True
    return len(_norm(s)) < 2


def is_placeholder_mmsi(mmsi: str | None) -> bool:
    """Generic/placeholder MMSIs carry shipyard/test identities, not a real vessel
    identity (e.g. 270000000 = MID + 000000, or a single repeated digit)."""
    if not mmsi or not mmsi.isdigit() or len(mmsi) != 9:
        return True
    return mmsi[3:] == "000000" or len(set(mmsi)) <= 1


def is_real_name_change(old: str | None, new: str | None) -> bool:
    """A confirmed, meaningful name change: both sides non-garbage and not a
    near-duplicate of each other."""
    if is_garbage_name(old) or is_garbage_name(new):
        return False
    return not near_duplicate(old, new)


def identity_suspicion_rank(*, change_type: str, old_flag: str | None,
                            new_flag: str | None, correlated: bool,
                            multiple: bool) -> str:
    """Rank an identity change high|medium|low. PRIORITISATION, never accusation.

    HIGH  — correlates with an A1 gap / A2 rendezvous (same hull), OR multiple
            changes, OR a RE-FLAG involving a grey / flag-of-convenience /
            sanctions-associated registry (the shadow-fleet signature).
    MEDIUM — a re-flag between non-grey registries.
    LOW   — a same-MMSI rename / retype with no other signal (likely a legitimate
            sale/rename).
    """
    if correlated or multiple:
        return HIGH
    if change_type == CHANGE_REFLAG:
        flag_grey = (old_flag in GREY_FLAGS) or (new_flag in GREY_FLAGS)
        return HIGH if flag_grey else MEDIUM
    return LOW


def clean_switch_gap_hours(old_last_ts: Any, new_first_ts: Any) -> float:
    """Gap (hours) between the old identity's last fix and the new identity's
    first fix. Negative = overlap. A re-flag requires this >= -REFLAG_MAX_OVERLAP_H
    (clean switch, not a two-vessel IMO collision)."""
    return (new_first_ts - old_last_ts).total_seconds() / 3600.0


def is_clean_switch(old_last_ts: Any, new_first_ts: Any) -> bool:
    return clean_switch_gap_hours(old_last_ts, new_first_ts) >= -REFLAG_MAX_OVERLAP_H


#: Human-facing caveat — a documented change for investigation, NEVER proven evasion.
CANDIDATE_CAVEAT = (
    "Documented identity change, not proof of evasion: a vessel broadcast a "
    "different name/flag/MMSI over time. Renaming, sale, and re-flagging are "
    "USUALLY LEGAL and routine. AIS static is spoofable and error-prone. Ranking "
    "prioritises leads (grey/sanctions flags, correlation with gaps/rendezvous); "
    "it does not accuse."
)
