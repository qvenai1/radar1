"""Unit tests: ingest.parse_record — null preservation, invalid rejection."""

from ingest import parse_record


BASE = {
    "icao24": "4b1617",
    "callsign": "SWR27G  ",
    "origin_country": "Switzerland",
    "time_position": 1783509251,
    "last_contact": 1783509251,
    "longitude": 8.5492,
    "latitude": 47.4612,
    "baro_altitude": 6134.1,
    "geo_altitude": 6484.6,
    "on_ground": False,
    "velocity": 160.7,
    "true_track": 267.4,
    "vertical_rate": -4.2,
    "squawk": "1000",
    "spi": False,
    "position_source": 0,
    "category": None,
    "snapshot_time": 1783509251,
    "fetched_at": "2026-07-08T11:14:15.926535+00:00",
}


def test_valid_record_roundtrip():
    row = parse_record(BASE)
    assert row is not None
    assert row[0] == 1783509251          # snapshot_time first (drives ts)
    assert row[1] == "4b1617"
    assert row[2] == "SWR27G  "          # stored verbatim, no trimming


def test_nulls_stay_nulls():
    rec = dict(BASE, baro_altitude=None, velocity=None, true_track=None, category=None)
    row = parse_record(rec)
    assert row is not None
    assert row[8] is None and row[11] is None and row[12] is None and row[17] is None


def test_icao24_normalized_lowercase():
    row = parse_record(dict(BASE, icao24="  4B1617 "))
    assert row is not None and row[1] == "4b1617"


def test_missing_icao24_rejected():
    assert parse_record(dict(BASE, icao24=None)) is None
    assert parse_record(dict(BASE, icao24="ab")) is None


def test_bad_snapshot_time_rejected():
    assert parse_record(dict(BASE, snapshot_time=None)) is None
    assert parse_record(dict(BASE, snapshot_time="oops")) is None
    assert parse_record(dict(BASE, snapshot_time=-5)) is None


def test_string_altitude_rejected_not_coerced():
    # "don't silently coerce": wrong types invalidate the record entirely
    assert parse_record(dict(BASE, baro_altitude="9000")) is None


def test_empty_callsign_kept_empty():
    row = parse_record(dict(BASE, callsign=""))
    assert row is not None and row[2] == ""


def test_default_source_opensky():
    # OpenSky records have no `source` key → default; military extras NULL
    row = parse_record(BASE)
    assert row is not None
    assert row[19] == "opensky"                    # source
    assert row[20] is None and row[21] is None and row[22] is None


def test_military_record_self_describes():
    mil = dict(BASE, source="airplanes_live", actype="C17",
               acdesc="Boeing C-17A Globemaster III", reg="CB-8011")
    row = parse_record(mil)
    assert row is not None
    assert row[19] == "airplanes_live"
    assert row[20] == "C17" and row[22] == "CB-8011"


def test_default_source_override():
    # a source-less record picks up the caller's default (backfill --source)
    row = parse_record(BASE, default_source="airplanes_live")
    assert row is not None and row[19] == "airplanes_live"


def test_geo_classified_at_ingest_outside():
    # P3: parse_record classifies from lat/lon for EVERY source. BASE is over
    # Switzerland → OUTSIDE the seven-country AOI: country/near NULL, but the
    # position IS classified (confirmed) with distance provenance + scraplan's
    # geo_source. validation_flags stays NULL (BASE supplies none).
    row = parse_record(BASE)
    assert row is not None
    assert row[23] is None                                # country (OUTSIDE)
    assert row[24] is None                                # near_country
    assert row[25] == "confirmed"                         # has a position fix
    assert row[26] is None                                # validation_flags (none)
    assert isinstance(row[27], float) and row[27] > 0     # distance_km provenance
    assert row[28] == "scraplan-aoi"                      # scraplan classified it


def test_geo_inside_country_at_ingest():
    # Damascus → Syria, computed at ingest from lat/lon regardless of feed.
    row = parse_record(dict(BASE, latitude=33.5138, longitude=36.2765))
    assert row is not None
    assert row[23] == "Syria" and row[24] is None and row[25] == "confirmed"
    assert row[28] == "scraplan-aoi"


def test_incoming_geo_is_reclassified_not_trusted():
    # P3: incoming country/near/distance/geo_source are IGNORED and recomputed.
    # A record CLAIMING Syria but positioned over Switzerland is classified
    # OUTSIDE (country NULL), with geo_source = scraplan's identity.
    rec = dict(
        BASE, country="Syria", near_country="Turkey",
        position_quality="confirmed", distance_km=12.5, geo_source="adsbexchange",
    )
    row = parse_record(rec)
    assert row is not None
    assert row[23] is None                 # NOT "Syria" — re-classified from lat/lon
    assert row[24] is None                 # NOT "Turkey"
    assert row[27] != 12.5                  # distance recomputed by scraplan
    assert row[28] == "scraplan-aoi"       # NOT "adsbexchange"


def test_incoming_suspect_is_preserved():
    # scraplan owns confirmed-vs-no_position; an incoming 'suspect' (a kinematic
    # verdict the feed computed in native units) is preserved, not upgraded.
    row = parse_record(dict(BASE, latitude=33.5138, longitude=36.2765,
                            position_quality="suspect"))
    assert row is not None
    assert row[25] == "suspect" and row[23] == "Syria"


def test_no_position_row_is_no_position():
    rec = dict(BASE, latitude=None, longitude=None)
    row = parse_record(rec)
    assert row is not None
    assert row[6] is None and row[7] is None    # lon/lat
    assert row[23] is None and row[24] is None  # country/near
    assert row[25] == "no_position"
    assert row[27] is None                       # no distance without a fix
    assert row[28] == "scraplan-aoi"


def test_geo_flags_reject_wrong_type():
    # a non-array validation_flags invalidates the record (no silent coercion)
    assert parse_record(dict(BASE, validation_flags="in_area")) is None
