"""Cross-pattern composite ranking — the IMO-centric intelligence picture.

An AGGREGATION over the existing behavioural events (A1 gap, A2 rendezvous, A3
identity_change), NOT a new detector. The SQL views (migration 026) stitch a
hull's events by IMO; this module ranks a hull by how many DISTINCT pattern types
it exhibits, elevating multi-pattern hulls and grey/sanctions-flag involvement.

The honest limitation COMPOUNDS but does not change kind: each underlying event is
already "investigation lead, not proof". A composite of leads is a STRONGER lead
— a hull that went dark AND met another AND changed identity has more reasons to
investigate — but it is STILL NOT proof. Legitimate explanations exist (a sold +
re-flagged tanker that anchored in sparse coverage and did offshore supply work
could trip all three innocently). This PRIORITISES; it never accuses.
"""

from __future__ import annotations

from typing import Any

from .identity_change import GREY_FLAGS  # FLAGS_OF_CONVENIENCE + SANCTIONS_FLAGS(Russia)

METHOD = "hull-composite-v1"
HIGH, MEDIUM, LOW = "high", "medium", "low"

_ORD = {HIGH: 3, MEDIUM: 2, LOW: 1}


def underlying_rank_ord(event_type: str, detail: dict[str, Any]) -> int:
    """Ordinal (3/2/1/0) of one underlying event's own rank. rendezvous /
    identity_change carry a suspicion_rank; a CANDIDATE ais_gap has none but is a
    meaningful (medium) baseline signal (it already survived confound removal).

    A gnss_anomaly (only the 'individual' tier reaches the composite) gets a LOW
    baseline (1) — deliberately BELOW ais_gap's 2. An individual GNSS episode is a
    real vessel spoofed/jammed: a genuine behavioural signal that SHOULD contribute
    to n_pattern_types and multi-pattern ranking, but is VICTIM-leaning (the vessel
    may be a target, not an actor), so on its own it must not inflate a hull to high
    rank. Flag, don't accuse — consistent with the victim/actor distinction."""
    o = _ORD.get(detail.get("suspicion_rank") or "", 0)
    if event_type == "ais_gap" and o == 0:
        o = 2
    if event_type == "gnss_anomaly" and o == 0:
        o = 1
    return o


def involves_grey_flag(flags: list[str | None]) -> bool:
    """True if any of the hull's flags (current + old/new re-flag + partner) is a
    grey / flag-of-convenience / sanctions-associated registry."""
    return any(f in GREY_FLAGS for f in flags if f)


# --------------------------------------------------------------------------------------
# RANK-S2 — WHICH CONDITION ACTUALLY DETERMINED THE RANK.
#
# ⛔ THE DEFECT THIS FIXES, measured on the live feed rather than reasoned. The Arabic `why`
# line read «الرتبة مرتفعة لتزامن نمطين» — "the rank is HIGH DUE TO two co-occurring
# patterns". But two pattern types ALONE are MEDIUM (see the table below): what lifts such a
# hull to HIGH is the grey/sanctions flag or a high underlying event. So the sentence blamed
# an INSUFFICIENT condition — measured on 172 of 200 live maritime alerts.
#
# The English `severity_basis` never carried this bug because it LISTS facts
# ("composite of 2 pattern types …; involves a grey/sanctions flag") without asserting which
# one caused the rank. The Arabic added a causal «ل» the English does not have, which breaks
# this project's own rule for the field: `why` RESTATES, it does not judge (summaries_ar
# rule 2).
#
# ⚠ WHY A REASON CODE RATHER THAN RE-DERIVING IT AT THE PROSE LAYER. `summaries_ar` rule 5
# forbids a second derivation: nothing there may recompute a rank. Re-testing
# `involves_grey or max_ord >= 3` in the Arabic layer would fork the rule and let the two
# copies drift. So the branch that fired is reported HERE, by the one function that owns the
# rule, and the prose only looks it up.
#
# ⛔ THE REASON NAMES EVERY SUFFICIENT CONDITION, not the first one matched. When a
# two-pattern hull carries BOTH a grey flag AND a high underlying event, either alone would
# have produced HIGH — so both are named. Reporting one would be a partial truth about why
# the row sits where it does, the same class of defect as the original.
# --------------------------------------------------------------------------------------
THREE_PATTERNS = "three_patterns"
TWO_PATTERNS_GREY = "two_patterns_grey"
TWO_PATTERNS_HIGH_EVENT = "two_patterns_high_event"
TWO_PATTERNS_GREY_AND_HIGH_EVENT = "two_patterns_grey_and_high_event"
TWO_PATTERNS = "two_patterns"
SINGLE_HIGH_EVENT = "single_high_event"
SINGLE_MEDIUM_EVENT = "single_medium_event"
SINGLE_LOW_EVENT = "single_low_event"

#: which rank each reason yields — the authority for a consumer that needs to know whether a
#: reason is a high-tier one WITHOUT re-testing the branch conditions itself.
RANK_OF_REASON: dict[str, str] = {
    THREE_PATTERNS: HIGH,
    TWO_PATTERNS_GREY: HIGH,
    TWO_PATTERNS_HIGH_EVENT: HIGH,
    TWO_PATTERNS_GREY_AND_HIGH_EVENT: HIGH,
    TWO_PATTERNS: MEDIUM,
    SINGLE_HIGH_EVENT: HIGH,
    SINGLE_MEDIUM_EVENT: MEDIUM,
    SINGLE_LOW_EVENT: LOW,
}

#: reasons whose own text names the grey/sanctions flag as a DETERMINANT — so a caller that
#: also appends a separate "and its flag is listed" clause would state it twice.
GREY_IS_THE_REASON = frozenset({TWO_PATTERNS_GREY, TWO_PATTERNS_GREY_AND_HIGH_EVENT})


def rank_with_reason(
    n_pattern_types: int, involves_grey: bool, max_underlying_ord: int,
) -> tuple[str, str]:
    """(rank, reason) for one hull — the single source of the ranking rule.

    HIGH   — all THREE pattern types; OR two types with a grey/sanctions flag or a
             high underlying event (the intel-meaningful combos: identity-change +
             gap, rendezvous + gap, …).
    MEDIUM — two pattern types with no grey flag / high event; OR a lone
             medium-tier event (e.g. a single candidate gap).
    LOW    — a lone low-tier event.

    ⚠ `involves_grey` is deliberately NOT consulted below two pattern types, matching the
    rank rule: a grey flag does not on its own elevate a lone event, so the reason must not
    imply that it did. The flag stays a stated fact for those hulls, never their reason.
    """
    if n_pattern_types >= 3:
        # three patterns are sufficient alone; a grey flag here is an extra fact, not the cause
        return HIGH, THREE_PATTERNS
    if n_pattern_types == 2:
        high_event = max_underlying_ord >= 3
        if involves_grey and high_event:
            return HIGH, TWO_PATTERNS_GREY_AND_HIGH_EVENT
        if involves_grey:
            return HIGH, TWO_PATTERNS_GREY
        if high_event:
            return HIGH, TWO_PATTERNS_HIGH_EVENT
        return MEDIUM, TWO_PATTERNS
    # a single pattern type — carry the underlying event's own rank
    if max_underlying_ord >= 3:
        return HIGH, SINGLE_HIGH_EVENT
    if max_underlying_ord >= 2:
        return MEDIUM, SINGLE_MEDIUM_EVENT
    return LOW, SINGLE_LOW_EVENT


def composite_rank(n_pattern_types: int, involves_grey: bool, max_underlying_ord: int) -> str:
    """Rank a hull high|medium|low. PRIORITISATION, never accusation.

    ⚠ A thin wrapper over `rank_with_reason` on purpose — the rule is written ONCE. Kept as
    the public entry point because every existing caller and gate reads it.
    """
    return rank_with_reason(n_pattern_types, involves_grey, max_underlying_ord)[0]


#: Compounding, human-facing caveat — a composite of leads is a stronger lead,
#: still NOT proof.
CANDIDATE_CAVEAT = (
    "Composite of investigation leads — a STRONGER lead, still NOT proof. Each "
    "underlying event (gap, rendezvous, identity change) is already only a lead; "
    "a hull exhibiting several has more reasons to investigate, but legitimate "
    "explanations exist (a sold/re-flagged vessel that anchored in sparse coverage "
    "and did offshore supply work could trip all three innocently). AIS is "
    "spoofable. This prioritises for review; it does not accuse."
)
