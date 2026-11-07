"""Behavioural analysis A3 — identity-change logic.

Proves the noise filter (sustained/near-duplicate/garbage/placeholder), the clean-
switch test, and the suspicious-vs-routine ranking (grey/sanctions flag,
correlation with A1/A2, multiple changes → higher; a single same-flag rename →
lower) — prioritisation that never accuses.
"""

from datetime import datetime, timezone

import pytest

from backend.identity_change import (
    CANDIDATE_CAVEAT,
    CHANGE_REFLAG,
    CHANGE_RENAME,
    GREY_FLAGS,
    HIGH,
    LOW,
    MEDIUM,
    METHOD,
    SANCTIONS_FLAGS,
    clean_switch_gap_hours,
    identity_suspicion_rank,
    is_clean_switch,
    is_garbage_name,
    is_placeholder_mmsi,
    is_real_name_change,
    near_duplicate,
)

UTC = timezone.utc


# --------------------------- noise filter ---------------------------

@pytest.mark.parametrize("a, b, dup", [
    ("EL QAHER I", "EL QAHERI", True),        # spacing/typo — same name
    ("EVER GIVEN", "EVER  GIVEN", True),       # extra space
    ("rosa", "ROSA", True),                    # case only
    ("EUROSAILOR", "ROSA", False),             # genuinely different
    ("SANDHYA", "VYCHEGDA", False),
    ("TURKON RIZE", "GISELE A", False),
])
def test_near_duplicate(a, b, dup):
    assert near_duplicate(a, b) is dup


@pytest.mark.parametrize("s, garbage", [
    (None, True), ("", True), ("   ", True),
    ("ALISA    E,=_)UUUW9", True),             # >30% symbols
    ("ONE\x01FORCE", True),                    # control char
    ("A", True),                               # too short
    ("EUROSAILOR", False), ("EL QAHER I", False), ("GISELE A", False),
])
def test_is_garbage_name(s, garbage):
    assert is_garbage_name(s) is garbage


@pytest.mark.parametrize("mmsi, placeholder", [
    ("270000000", True),                       # MID + 000000
    ("000000000", True), ("111111111", True),
    ("12345", True), (None, True), ("abcdefghi", True),
    ("271053122", False), ("636021705", False),
])
def test_is_placeholder_mmsi(mmsi, placeholder):
    assert is_placeholder_mmsi(mmsi) is placeholder


def test_is_real_name_change():
    assert is_real_name_change("EUROSAILOR", "ROSA") is True
    assert is_real_name_change("EL QAHER I", "EL QAHERI") is False   # near-dup
    assert is_real_name_change("ONE FORCE", "ONE\x01ECPEJP") is False  # garbage side


# --------------------------- clean switch ---------------------------

def test_clean_switch():
    t = lambda h: datetime(2026, 7, 15, h, 0, tzinfo=UTC)
    assert is_clean_switch(t(4), t(10)) is True                    # 6h gap
    assert round(clean_switch_gap_hours(t(4), t(10)), 0) == 6
    assert is_clean_switch(t(10), t(9)) is True                    # 1h overlap tolerated
    assert is_clean_switch(t(20), t(2)) is False                   # 18h overlap => collision


# --------------------------- ranking ---------------------------

def test_reflag_to_grey_or_sanctions_is_high():
    # Sierra Leone -> Russia (assessment IMO 9289752) — grey/sanctions -> HIGH
    assert identity_suspicion_rank(change_type=CHANGE_REFLAG, old_flag="Sierra Leone",
                                   new_flag="Russia", correlated=False, multiple=False) == HIGH
    # Cameroon -> Russia
    assert identity_suspicion_rank(change_type=CHANGE_REFLAG, old_flag="Cameroon",
                                   new_flag="Russia", correlated=False, multiple=False) == HIGH
    # Malta -> Palau (both flags of convenience)
    assert identity_suspicion_rank(change_type=CHANGE_REFLAG, old_flag="Malta",
                                   new_flag="Palau", correlated=False, multiple=False) == HIGH


def test_reflag_between_non_grey_is_medium():
    # Hong Kong -> Singapore (assessment IMO 9801720) — neither grey -> MEDIUM
    assert identity_suspicion_rank(change_type=CHANGE_REFLAG, old_flag="Hong Kong",
                                   new_flag="Singapore", correlated=False, multiple=False) == MEDIUM


def test_correlation_or_multiple_forces_high():
    # a plain rename correlated with an A1/A2 event -> HIGH (the intel multiplier)
    assert identity_suspicion_rank(change_type=CHANGE_RENAME, old_flag=None, new_flag=None,
                                   correlated=True, multiple=False) == HIGH
    # multiple changes -> HIGH
    assert identity_suspicion_rank(change_type=CHANGE_REFLAG, old_flag="Greece", new_flag="Italy",
                                   correlated=False, multiple=True) == HIGH


def test_plain_rename_is_low():
    # a single same-flag rename with no other signal -> LOW (likely legitimate sale)
    assert identity_suspicion_rank(change_type=CHANGE_RENAME, old_flag=None, new_flag=None,
                                   correlated=False, multiple=False) == LOW


def test_lists_and_constants():
    assert "Russia" in SANCTIONS_FLAGS and "Russia" in GREY_FLAGS
    assert "Panama" in GREY_FLAGS and "Cameroon" in GREY_FLAGS   # FoC + grey included
    assert METHOD == "identity-change-detector-v1"
    assert "not proof" in CANDIDATE_CAVEAT.lower() and "legal" in CANDIDATE_CAVEAT.lower()
