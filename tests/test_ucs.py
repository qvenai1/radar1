"""Sat-UCS — UCS Satellite Database row → strategic mapping + the NORAD/INTLDES
join. Pure-function tests (mirrors test_satcat.py). Coverage, distributions, and
DB idempotency are exercised against the live DB in the Sat-UCS gauntlet.
"""

from datetime import date

from scripts.backfill_from_ucs import (
    _num_ucs, map_ucs_row, match_ucs, norm_cospar, strategic_row,
)

# A UCS record in canonical keys (as load_ucs produces from the real file).
REC = {
    "norad": "25544", "cospar": "1998-067A",
    "users": "Government", "purpose": "Space Science",
    "detailed_purpose": "Space station", "operator": "NASA / Multinational",
    "operator_country": "Multinational", "registry_country": "United States",
    "orbit_class": "LEO", "orbit_type": "Non-Polar Inclined",
    "launch_mass": "419,600", "power": "", "expected_lifetime": "30",
    "contractor": "Boeing / RSC Energia", "launch_site": "Baikonur Cosmodrome",
}


# ---- numeric parsing -------------------------------------------------------

def test_num_ucs_strips_commas_and_units():
    assert _num_ucs("1,250") == 1250.0
    assert _num_ucs("419,600") == 419600.0
    assert _num_ucs("15 yrs") == 15.0
    assert _num_ucs("3.5") == 3.5


def test_num_ucs_blank_and_na_become_none():
    assert _num_ucs("") is None
    assert _num_ucs("NA") is None
    assert _num_ucs("N/A") is None
    assert _num_ucs(None) is None
    assert _num_ucs("unknown") is None


def test_norm_cospar():
    assert norm_cospar("1998-067A") == "1998-067A"
    assert norm_cospar(" 1998-067a ") == "1998-067A"
    assert norm_cospar("") is None


# ---- mapping ---------------------------------------------------------------

def test_map_ucs_row_full():
    m = map_ucs_row(REC)
    assert m["users"] == "Government"
    assert m["purpose"] == "Space Science"
    assert m["detailed_purpose"] == "Space station"
    assert m["operator"] == "NASA / Multinational"
    assert m["operator_country"] == "Multinational"
    assert m["registry_country"] == "United States"
    assert m["orbit_class"] == "LEO"
    assert m["orbit_type"] == "Non-Polar Inclined"
    assert m["launch_mass_kg"] == 419600.0
    assert m["power_watts"] is None          # blank → None (not zero-filled)
    assert m["expected_lifetime_yrs"] == 30.0
    assert m["contractor"] == "Boeing / RSC Energia"
    assert m["launch_site"] == "Baikonur Cosmodrome"


def test_map_blank_fields_become_none():
    rec = dict(REC, users="", purpose="   ", launch_mass="")
    m = map_ucs_row(rec)
    assert m["users"] is None and m["purpose"] is None and m["launch_mass_kg"] is None


# ---- join path -------------------------------------------------------------

def test_match_via_norad_direct():
    by_norad = {25544: REC}
    rec, via = match_ucs(25544, None, by_norad, {})
    assert rec is REC and via == "norad"


def test_match_via_intldes_fallback():
    # NORAD not in UCS, but the international designator matches a COSPAR entry
    by_cospar = {"1998-067A": REC}
    rec, via = match_ucs(25544, "1998-067A", {}, by_cospar)
    assert rec is REC and via == "intldes"


def test_match_norad_preferred_over_intldes():
    norad_rec = dict(REC, users="Military")
    cospar_rec = dict(REC, users="Commercial")
    rec, via = match_ucs(25544, "1998-067A", {25544: norad_rec}, {"1998-067A": cospar_rec})
    assert via == "norad" and rec["users"] == "Military"


def test_unmatched_object_returns_none_no_guess():
    # an object absent from UCS (e.g. post-2023) stays unclassified
    rec, via = match_ucs(99999, "2025-123A", {25544: REC}, {"1998-067A": REC})
    assert rec is None and via is None


# ---- provenance / row tuple ------------------------------------------------

def test_strategic_row_shape_and_provenance():
    row = strategic_row(25544, map_ucs_row(REC), "norad", date(2023, 5, 1))
    assert len(row) == 17
    assert row[0] == 25544              # norad_id first
    assert row[1] == "Government"       # users
    assert row[14] == "ucs"            # source
    assert row[15] == date(2023, 5, 1) # ucs_snapshot_date
    assert row[16] == "norad"         # joined_via
