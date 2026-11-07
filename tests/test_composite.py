"""Cross-pattern composite — the IMO-centric intelligence ranking.

Proves the composite ranking: multi-pattern hulls and grey/sanctions involvement
are elevated (never accused), a lone benign event stays low. The IMO-stitching
itself is a SQL view (migration 026) verified live in the gauntlet.
"""

import pytest

from backend.composite import (
    CANDIDATE_CAVEAT,
    HIGH,
    LOW,
    MEDIUM,
    METHOD,
    GREY_IS_THE_REASON,
    RANK_OF_REASON,
    composite_rank,
    involves_grey_flag,
    rank_with_reason,
    underlying_rank_ord,
)


def test_three_pattern_types_is_high():
    # a hull with all three patterns -> top tier regardless of flag
    assert composite_rank(3, involves_grey=False, max_underlying_ord=1) == HIGH
    assert composite_rank(3, involves_grey=True, max_underlying_ord=3) == HIGH


@pytest.mark.parametrize("grey, max_ord, want", [
    (True, 1, HIGH),      # two types + grey/sanctions flag -> HIGH
    (False, 3, HIGH),     # two types + a high underlying event -> HIGH
    (False, 2, MEDIUM),   # two types, no grey, no high event -> MEDIUM
    (False, 1, MEDIUM),   # two benign types -> MEDIUM (still elevated over a single)
])
def test_two_pattern_types(grey, max_ord, want):
    assert composite_rank(2, involves_grey=grey, max_underlying_ord=max_ord) == want


@pytest.mark.parametrize("max_ord, want", [
    (3, HIGH),            # a lone high event (e.g. a Russia re-flag) still surfaces high
    (2, MEDIUM),          # a lone candidate gap -> medium
    (1, LOW),             # a lone low event -> low (not elevated)
    (0, LOW),
])
def test_single_pattern_type_carries_underlying(max_ord, want):
    assert composite_rank(1, involves_grey=False, max_underlying_ord=max_ord) == want


def test_underlying_rank_ord():
    # rendezvous / identity carry a suspicion_rank
    assert underlying_rank_ord("rendezvous", {"suspicion_rank": "high"}) == 3
    assert underlying_rank_ord("identity_change", {"suspicion_rank": "low"}) == 1
    # a candidate ais_gap has no suspicion_rank but is a medium baseline signal
    assert underlying_rank_ord("ais_gap", {}) == 2
    # a gap that somehow carried a high rank keeps it
    assert underlying_rank_ord("ais_gap", {"suspicion_rank": "high"}) == 3


def test_involves_grey_flag():
    assert involves_grey_flag(["Greece", "Panama"]) is True        # Panama = FoC
    assert involves_grey_flag(["Russia"]) is True                   # sanctions-associated
    assert involves_grey_flag(["Cameroon", None]) is True           # grey
    assert involves_grey_flag(["Greece", "Germany", None]) is False
    assert involves_grey_flag([]) is False


def test_constants_and_caveat():
    assert METHOD == "hull-composite-v1"
    c = CANDIDATE_CAVEAT.lower()
    assert "not proof" in c and "stronger lead" in c


# --- RANK-S2: the rank's REASON, alongside the rank -------------------------
#
# The Arabic `why` line used to blame the pattern count for every HIGH hull, which is
# false: two pattern types ALONE are MEDIUM. These gates pin the branch that actually
# fired, so the prose layer can name a sufficient condition instead of inferring one.

def test_rank_with_reason_never_disagrees_with_composite_rank():
    """⛔ The load-bearing gate: `composite_rank` is a wrapper, so the rule is written
    once. If these two ever diverge, the severity axis has moved — which is exactly what
    RANK-S2 promised not to do."""
    for n in range(0, 7):
        for grey in (False, True):
            for o in range(0, 5):
                rank, reason = rank_with_reason(n, grey, o)
                assert rank == composite_rank(n, grey, o)
                assert RANK_OF_REASON[reason] == rank, (n, grey, o, reason)


@pytest.mark.parametrize("n, grey, max_ord, want_rank, want_reason", [
    # three patterns are sufficient alone — a grey flag here is an extra fact, not the cause
    (3, False, 1, HIGH, "three_patterns"),
    (3, True, 3, HIGH, "three_patterns"),
    # two patterns alone are MEDIUM — the whole point of the fix
    (2, False, 2, MEDIUM, "two_patterns"),
    (2, False, 1, MEDIUM, "two_patterns"),
    # ...and what lifts them is named explicitly
    (2, True, 1, HIGH, "two_patterns_grey"),
    (2, False, 3, HIGH, "two_patterns_high_event"),
    # ⛔ BOTH sufficient conditions present -> BOTH named, never just the first matched
    (2, True, 3, HIGH, "two_patterns_grey_and_high_event"),
    # a lone event carries its own rank; grey does NOT elevate it, so it is not the reason
    (1, False, 3, HIGH, "single_high_event"),
    (1, True, 3, HIGH, "single_high_event"),
    (1, True, 2, MEDIUM, "single_medium_event"),
    (1, True, 1, LOW, "single_low_event"),
    (1, False, 0, LOW, "single_low_event"),
])
def test_rank_reason_names_the_condition_that_fired(n, grey, max_ord, want_rank, want_reason):
    assert rank_with_reason(n, grey, max_ord) == (want_rank, want_reason)


def test_grey_is_the_reason_holds_exactly_the_reasons_that_name_the_flag():
    """A consumer appending its own "and its flag is listed" clause must be able to tell
    when the reason already said it. Over-inclusion would DROP a true fact; under-inclusion
    would state the same flag twice."""
    for n in range(0, 7):
        for o in range(0, 5):
            _, reason = rank_with_reason(n, involves_grey=True, max_underlying_ord=o)
            named = reason in GREY_IS_THE_REASON
            # the flag is a determinant only at exactly two pattern types
            assert named is (n == 2), (n, o, reason)
        for o in range(0, 5):
            _, reason = rank_with_reason(n, involves_grey=False, max_underlying_ord=o)
            assert reason not in GREY_IS_THE_REASON
