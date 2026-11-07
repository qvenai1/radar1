"""E3 — targeted adsbdb gap-fill + refresh: adsbdb parse mirror.

Pure-function tests for parse_adsbdb_aircraft (kept in sync with
backend.enrich._fetch_aircraft's adsbdb mapping). The DB-level guarantees
(targeting is in-region-only, per-field precedence adsbdb>csv>empty, non-clobber
on null, negative cache, idempotency/resume) are exercised against the live DB in
the E3 verification gauntlet, matching this suite's no-DB convention.
"""

from scripts.backfill_from_adsbdb import parse_adsbdb_aircraft as parse

# verbatim-shape adsbdb /v0/aircraft response for a known airframe
ADSBDB_FOUND = {
    "response": {
        "aircraft": {
            "type": "Airbus A320 214", "icao_type": "A320",
            "manufacturer": "Airbus", "registration": "G-EZTE",
            "registered_owner": "easyJet",
            "registered_owner_country_iso_name": "GB",
            "registered_owner_country_name": "United Kingdom",
            "url_photo": None, "url_photo_thumbnail": None,
        }
    }
}


def test_parse_found_maps_all_fields():
    f = parse(ADSBDB_FOUND)
    assert f == {
        "registration": "G-EZTE",
        "aircraft_type": "Airbus A320 214",
        "icao_type": "A320",
        "manufacturer": "Airbus",
        "operator": "easyJet",                 # <- registered_owner
        "operator_country": "United Kingdom",   # <- registered_owner_country_name
    }


def test_parse_unknown_aircraft_string_is_none():
    # adsbdb returns a plain string for unknown hexes
    assert parse({"response": "unknown aircraft"}) is None


def test_parse_missing_or_empty_is_none():
    assert parse({}) is None
    assert parse(None) is None
    assert parse({"response": {}}) is None
    assert parse({"response": {"aircraft": {}}}) is None  # no usable values


def test_parse_partial_keeps_present_nulls_absent():
    data = {"response": {"aircraft": {
        "type": "Boeing 737", "registration": "TC-XYZ",
        "manufacturer": None, "registered_owner": "",
    }}}
    f = parse(data)
    assert f["aircraft_type"] == "Boeing 737"
    assert f["registration"] == "TC-XYZ"
    assert f["manufacturer"] is None          # null stays null (no value)
    assert f["operator"] is None              # empty string -> None
    assert f["icao_type"] is None


def test_parse_empty_strings_trimmed_to_none():
    data = {"response": {"aircraft": {"registration": "   ", "type": "A321"}}}
    f = parse(data)
    assert f["registration"] is None and f["aircraft_type"] == "A321"


def test_parse_all_empty_is_none():
    data = {"response": {"aircraft": {"registration": "", "type": "  ", "manufacturer": None}}}
    assert parse(data) is None
