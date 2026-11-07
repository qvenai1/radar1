"""P2 — ingest of the adsb_scraper state_vectors.jsonl archive.

adsb_scraper is an EXTERNAL producer; scraplan consumes its archive through the
SAME idempotent path as its own feeds (parse_record -> INSERT_SQL, natural key
(icao24, ts, source)). These tests lock the record contract, the crash-repair
tailing discipline, and the third-source wiring — WITHOUT a DB (DB idempotency /
mixed-source integrity are proven in the live verification gauntlet).
"""

import json

from ingest import (
    ARCHIVE_PREFIX,
    SOURCES,
    TailState,
    _read_new_complete_lines,
    parse_lines,
    parse_record,
)

# A real line emitted by adsb_scraper's P1 producer (StateVector shape,
# source=adsbexchange, all six P0 geo columns present).
ADSB_LINE = {
    "snapshot_time": 1784107235,
    "ts": "2026-07-15T09:20:35+00:00",
    "icao24": "ae4d67",
    "callsign": "MCFAMC",
    "origin_country": "",
    "time_position": None,
    "last_contact": None,
    "longitude": -30.731335,
    "latitude": 55.960751,
    "baro_altitude": 9144.0,
    "geo_altitude": None,
    "on_ground": False,
    "velocity": None,
    "true_track": 266.0111111111111,
    "vertical_rate": None,
    "squawk": "",
    "spi": False,
    "position_source": None,
    "category": None,
    "fetched_at": "2026-07-15T09:20:35+00:00",
    "source": "adsbexchange",
    "country": None,
    "near_country": None,
    "position_quality": "confirmed",
    "validation_flags": ["outside_area"],
    "distance_km": 5465.006,
    "geo_source": "adsbexchange",
}


def test_adsb_line_maps_all_columns():
    # index map: 0 snapshot_time .. 19 source .. 23 country, 24 near_country,
    # 25 position_quality, 26 validation_flags, 27 distance_km, 28 geo_source.
    # P3: the geo columns are RE-CLASSIFIED by scraplan, not carried. ADSB_LINE
    # is over the mid-Atlantic → OUTSIDE: country/near NULL, geo_source is
    # scraplan's identity, distance recomputed. validation_flags preserved.
    row = parse_record(ADSB_LINE)
    assert row is not None
    assert row[0] == 1784107235
    assert row[1] == "ae4d67"
    assert row[6] == -30.731335 and row[7] == 55.960751   # lon/lat preserved
    assert row[19] == "adsbexchange"                       # self-describing source
    assert row[23] is None                                 # country (OUTSIDE)
    assert row[24] is None                                 # near_country
    assert row[25] == "confirmed"                          # incoming confirmed kept
    assert row[26] == ["outside_area"]                     # validation_flags preserved (TEXT[])
    assert isinstance(row[27], float) and row[27] > 0      # distance recomputed by scraplan
    assert row[28] == "scraplan-aoi"                       # scraplan classified it, not the feed


def test_adsb_source_wins_over_caller_default():
    # even if the tailer passes default_source, the record's own source wins.
    row = parse_record(ADSB_LINE, default_source="opensky")
    assert row is not None and row[19] == "adsbexchange"


def test_geo_source_always_scraplan_regardless_of_incoming():
    # P3 provenance: geo_source is ALWAYS scraplan's classifier, whatever the
    # incoming feed or its claimed geo_source; the natural-key source is intact.
    rec = dict(ADSB_LINE, source="opensky", geo_source="adsbexchange")
    row = parse_record(rec)
    assert row is not None and row[19] == "opensky" and row[28] == "scraplan-aoi"


def test_adsb_and_opensky_agree_at_same_coordinates():
    # the same position classified via two feeds yields identical country/near/
    # distance/geo_source — geo is a property of POSITION, not the feed.
    dam = {"snapshot_time": 1784107235, "on_ground": False,
           "latitude": 33.5138, "longitude": 36.2765}
    o = parse_record(dict(dam, icao24="aaaa01", source="opensky"))
    a = parse_record(dict(dam, icao24="aaaa01", source="adsbexchange"))
    assert o is not None and a is not None
    assert o[23] == a[23] == "Syria"      # country
    assert o[24] == a[24]                  # near_country
    assert o[27] == a[27]                  # distance_km
    assert o[28] == a[28] == "scraplan-aoi"


def test_coexist_same_icao_ts_different_source():
    # opensky and adsbexchange for the same aircraft at the same second differ
    # ONLY in source (natural-key index 19) -> no PK collision at the DB.
    o = parse_record(dict(ADSB_LINE, source="opensky"))
    a = parse_record(ADSB_LINE)
    assert o is not None and a is not None
    assert (o[1], o[0]) == (a[1], a[0])   # same icao24, same snapshot_time
    assert o[19] != a[19]                  # different source -> different PK


def test_malformed_and_partial_lines_skipped_not_crashing():
    good = json.dumps(ADSB_LINE)
    lines = [good, "{not json", "", "{}", '{"icao24": "abcd12"}']  # last: no snapshot_time
    rows, invalid = parse_lines(lines, "adsbexchange")
    assert len(rows) == 1 and rows[0][1] == "ae4d67"
    assert invalid == 3   # junk + {} + missing-snapshot_time (blank line ignored)


def test_tailer_leaves_trailing_partial_line(tmp_path):
    # crash-repair discipline: a line without a closing newline (producer mid
    # -flush) is NOT consumed until it completes -> no torn JSON reaches parse.
    archive = tmp_path / "state_vectors.jsonl"
    complete = json.dumps(ADSB_LINE) + "\n"
    archive.write_text(complete + '{"icao24":"partial","snapshot_ti')  # torn tail

    state = TailState(archive, state_dir=tmp_path / "state")
    lines, new_offset = _read_new_complete_lines(archive, state)
    assert len(lines) == 1                       # only the complete line
    assert new_offset == len(complete.encode())  # offset stops at the newline
    # once the torn line completes, the next poll picks it up.
    archive.write_text(complete + json.dumps(dict(ADSB_LINE, icao24="bbbb22")) + "\n")
    state.offset = new_offset
    lines2, _ = _read_new_complete_lines(archive, state)
    assert len(lines2) == 1 and json.loads(lines2[0])["icao24"] == "bbbb22"


def test_state_dir_keeps_offset_out_of_archive_dir(tmp_path):
    # scraplan must not deposit its offset in the external adsb output dir.
    archive_dir = tmp_path / "adsb_output"
    archive_dir.mkdir()
    archive = archive_dir / "state_vectors.jsonl"
    archive.write_text(json.dumps(ADSB_LINE) + "\n")
    own = tmp_path / "scraplan_state"
    state = TailState(archive, state_dir=own)
    state.save()
    assert (own / ".ingest_state.json").exists()
    assert not (archive_dir / ".ingest_state.json").exists()


def test_adsbexchange_is_a_registered_source():
    assert "adsbexchange" in SOURCES
    assert ARCHIVE_PREFIX["adsbexchange"] == "state_vectors"
