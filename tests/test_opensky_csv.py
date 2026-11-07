"""E2 — OpenSky aircraftDatabase.csv row -> aircraft_info mapping.

Pure-function tests for the CSV->columns mapping and its robustness. The
DB-level guarantees (precedence: found=true adsbdb rows not overwritten; E1
registration_country untouched; idempotency) are exercised against the live DB
in the E2 verification gauntlet, matching this suite's no-DB convention.
"""

from scripts.backfill_from_opensky_csv import map_row

# A real OpenSky row (icao24 80078e), header keys as in aircraftDatabase.csv.
BASE = {
    "icao24": "80078e", "registration": "CB-8001",
    "manufacturericao": "MCDONNELL DOUGLAS", "manufacturername": "Boeing",
    "model": "C-17A Globemaster III", "typecode": "C17",
    "operator": "Indian Air Force", "owner": "Indian Air Force",
}


def test_map_row_full():
    icao24, registration, aircraft_type, icao_type, manufacturer, operator = map_row(BASE)
    assert icao24 == "80078e"
    assert registration == "CB-8001"
    assert aircraft_type == "C-17A Globemaster III"   # <- model
    assert icao_type == "C17"                          # <- typecode
    assert manufacturer == "Boeing"                    # <- manufacturername (human)
    assert operator == "Indian Air Force"


def test_manufacturer_uses_human_name_not_icao():
    row = dict(BASE, manufacturername="Airbus", manufacturericao="AIRBUS")
    assert map_row(row)[4] == "Airbus"


def test_operator_falls_back_to_owner_when_empty():
    row = dict(BASE, operator="", owner="Some Leasing Co")
    assert map_row(row)[5] == "Some Leasing Co"


def test_operator_preferred_over_owner():
    row = dict(BASE, operator="Airline X", owner="Leasing Y")
    assert map_row(row)[5] == "Airline X"


def test_icao24_lowercased():
    assert map_row(dict(BASE, icao24="80078E"))[0] == "80078e"


def test_empty_strings_become_none():
    row = dict(BASE, registration="", typecode="   ", model="A320", manufacturername="", operator="", owner="")
    icao24, registration, aircraft_type, icao_type, manufacturer, operator = map_row(row)
    assert registration is None and icao_type is None and manufacturer is None and operator is None
    assert aircraft_type == "A320"   # the one non-empty fact keeps the row usable


def test_bad_icao24_skipped():
    assert map_row(dict(BASE, icao24="ZZZ")) is None      # non-hex
    assert map_row(dict(BASE, icao24="80078")) is None    # 5 chars
    assert map_row(dict(BASE, icao24="80078ef")) is None  # 7 chars
    assert map_row({"icao24": ""}) is None


def test_row_with_no_facts_skipped():
    # a bare identity row with no airframe facts is not worth a fill
    assert map_row({"icao24": "abc123", "registration": "", "model": "",
                    "typecode": "", "manufacturername": "", "operator": "", "owner": ""}) is None


def test_missing_columns_do_not_crash():
    # a partial/short row (missing keys) maps on what's present, never raises
    assert map_row({"icao24": "3c6789", "model": "A359"}) == (
        "3c6789", None, "A359", None, None, None)
