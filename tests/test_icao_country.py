"""E1 — country of registration from the ICAO 24-bit address allocation.

Verifies the lift is faithful (spot checks vs the published table), interval
logic is exact (boundaries, not a loose prefix match), nested territories win,
unallocated addresses return None, and the derivation is deterministic.
"""

import pytest

from backend.icao_country import (
    REGISTRATION_COUNTRY_SOURCE,
    icao24_to_registration_country as reg,
)


@pytest.mark.parametrize("hex_addr, country", [
    ("896abc", "United Arab Emirates"),  # 896000-896FFF
    ("4bcdef", "Turkey"),                # 4B8000-4BFFFF
    ("73a1b2", "Israel"),                # 738000-73FFFF
    ("744000", "Jordan"),                # 740000-747FFF
    ("778abc", "Syria"),                 # 778000-77FFFF
    ("a1b2c3", "United States"),         # A00000-AFFFFF
    ("3c6789", "Germany"),               # 3C0000-3FFFFF
    ("43c000", "United Kingdom"),        # 400000-43FFFF (outside carve-outs)
    ("06a800", "Qatar"),                 # 06A000-06AFFF
    ("710abc", "Saudi Arabia"),          # 710000-717FFF
])
def test_known_allocations(hex_addr, country):
    assert reg(hex_addr) == country


@pytest.mark.parametrize("hex_addr, country", [
    # first and last hex of a range map to that country ...
    ("738000", "Israel"), ("73ffff", "Israel"),
    ("740000", "Jordan"), ("747fff", "Jordan"),
    # ... and one below / above the range maps to the neighbour, proving it is
    # interval containment, not a prefix match:
    ("737fff", "Iran"),        # just below Israel -> Iran (730000-737FFF)
    ("748000", "Lebanon"),     # just above Jordan -> Lebanon (748000-74FFFF)
])
def test_boundaries_are_exact(hex_addr, country):
    assert reg(hex_addr) == country


@pytest.mark.parametrize("hex_addr, country", [
    # nested territory carve-outs win over the enclosing State block
    ("400000", "Bermuda"),          # first hex of the UK block is a Bermuda carve-out
    ("424300", "Bermuda"),          # 424200-4246FF Bermuda, inside 400000-43FFFF UK
    ("424b80", "Isle of Man"),      # 424B00-424BFF Isle of Man, inside UK
    ("43ec00", "Guernsey"),         # 43EAFE-43EEFF Guernsey, inside UK
    ("401000", "United Kingdom"),   # UK block, outside every carve-out
    ("789500", "Hong Kong"),        # 789000-789FFF Hong Kong, inside China 780000-7BFFFF
    ("785000", "China"),            # China block, outside Hong Kong
])
def test_nested_territories_resolve_to_narrowest(hex_addr, country):
    assert reg(hex_addr) == country


@pytest.mark.parametrize("hex_addr", [
    "000001",  # below the first allocation (starts 004000)
    "0e0000",  # gap between Venezuela (…0DFFFF) and Russia (100000…)
    "203000",  # gap after Eritrea (…2027FF) before Italy (300000…)
    "f08000",  # gap between the two ICAO reserved blocks
])
def test_unallocated_is_none_not_a_guess(hex_addr):
    assert reg(hex_addr) is None


@pytest.mark.parametrize("bad", [None, "", "   ", "zzz", "1234567", 0x896000])
def test_invalid_input_is_none(bad):
    # None/empty/non-hex; "1234567" = 0x1234567 > 0xFFFFFF (out of 24-bit range);
    # an int (0x896000) is not a hex string. All -> None, never raises.
    assert reg(bad) is None


def test_short_hex_is_interpreted_numerically():
    # a valid hex shorter than 6 chars is the numeric address (leading zeros):
    # "12345" == 0x012345, inside Egypt's block (010000-017FFF).
    assert reg("12345") == "Egypt"


def test_case_insensitive_and_whitespace():
    assert reg("896ABC") == reg("896abc") == reg("  896abc  ") == "United Arab Emirates"


def test_deterministic_idempotent():
    a = [reg(h) for h in ("896abc", "4bcdef", "778abc", "000001")]
    b = [reg(h) for h in ("896abc", "4bcdef", "778abc", "000001")]
    assert a == b == ["United Arab Emirates", "Turkey", "Syria", None]


def test_reserved_blocks_labelled_not_a_country():
    # ICAO-reserved blocks are represented faithfully, not forced to a country.
    assert reg("f00001") == "ICAO (temporary allocation)"


def test_source_label_constant():
    assert REGISTRATION_COUNTRY_SOURCE == "icao-24bit-allocation"
