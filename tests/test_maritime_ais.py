"""
Maritime tests:
  * AIS message parsing (PositionReport, ShipStaticData, sentinels, junk)
  * Fleet merge / staleness eviction / dirty-delta
  * AISStream adapter reconnect-with-backoff and gap logging, driven with a
    fake WebSocket + injected sleep so no real socket/network is touched.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from maritime.adapters.aisstream import AisStreamAdapter, BACKOFF_MIN
from maritime.adapters.aisstream_parse import (
    parse_envelope,
    parse_position_report,
    parse_static_data,
)
from maritime.db import (
    SPINE_FIELDS,
    identity_spine_fields,
    parse_lines as db_parse_lines,
    vessel_row,
)
from maritime.fleet import Fleet
from maritime.schema import ship_type_text


# --------------------------- parsing ---------------------------

POS = {
    "MessageType": "PositionReport",
    "MetaData": {"MMSI": 636092297, "time_utc": "2026-07-11 12:00:00.000000000 +0000 UTC"},
    "Message": {"PositionReport": {
        "Latitude": 25.301, "Longitude": 55.42, "Sog": 12.3, "Cog": 187.5,
        "TrueHeading": 189, "NavigationalStatus": 0,
    }},
}

STATIC = {
    "MessageType": "ShipStaticData",
    "MetaData": {"MMSI": 636092297},
    "Message": {"ShipStaticData": {
        "ImoNumber": 9321483, "Name": "EVER GIVEN@@@", "CallSign": "H3RC",
        "Type": 70, "Destination": "JEBEL ALI", "MaximumStaticDraught": 14.5,
    }},
}


def test_position_parsed():
    kind, mmsi, f = parse_envelope(POS)
    assert kind == "position"
    assert mmsi == "636092297"
    assert f["lat"] == 25.301 and f["lon"] == 55.42
    assert f["speed"] == 12.3 and f["course"] == 187.5 and f["heading"] == 189
    assert f["timestamp"].endswith("+00:00")  # UTC ISO 8601


def test_static_parsed_and_name_cleaned():
    kind, mmsi, f = parse_envelope(STATIC)
    assert kind == "static"
    assert f["imo"] == "9321483"
    assert f["name"] == "EVER GIVEN"          # '@' padding stripped
    assert f["ship_type"] == "cargo"          # 70 → cargo


def test_ais_sentinels_become_none():
    env = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 1},
        "Message": {"PositionReport": {
            "Latitude": 10.0, "Longitude": 20.0,
            "Sog": 102.3, "Cog": 360.0, "TrueHeading": 511,
        }},
    }
    _, _, f = parse_envelope(env)
    assert f["speed"] is None and f["course"] is None and f["heading"] is None


def test_invalid_position_rejected():
    env = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 1},
        "Message": {"PositionReport": {"Latitude": 91.0, "Longitude": 200.0}},
    }
    assert parse_position_report(env) is None


def test_unknown_message_type_ignored():
    assert parse_envelope({"MessageType": "AidsToNavigationReport"}) is None


def test_missing_mmsi_rejected():
    assert parse_static_data({"MessageType": "ShipStaticData",
                              "MetaData": {}, "Message": {"ShipStaticData": {}}}) is None


def test_ship_type_ranges():
    assert ship_type_text(30) == "fishing"
    assert ship_type_text(52) == "tug"
    assert ship_type_text(80) == "tanker"
    assert ship_type_text(None) is None
    assert ship_type_text(5) == "unknown"


# --------------------------- fleet ---------------------------

def test_fleet_merges_position_and_static():
    fleet = Fleet()
    _, mmsi, pos = parse_envelope(POS)
    fleet.apply_position(mmsi, pos, "aisstream")
    _, _, static = parse_envelope(STATIC)
    fleet.apply_static(mmsi, static, "aisstream")
    snap = fleet.snapshot(max_age_s=10**9)
    assert len(snap) == 1
    v = snap[0]
    assert v["mmsi"] == "636092297" and v["name"] == "EVER GIVEN"
    assert v["speed"] == 12.3 and v["ship_type"] == "cargo"
    assert v["data_origin"] == "terrestrial" and v["source"] == "aisstream"


def test_fleet_evicts_stale():
    fleet = Fleet()
    fleet.apply_position("1", {"lat": 1.0, "lon": 2.0,
                               "timestamp": "2000-01-01T00:00:00+00:00"}, "aisstream")
    assert fleet.snapshot(max_age_s=60) == []      # ancient fix → evicted
    assert len(fleet) == 0


def test_fleet_drains_every_new_signal():
    fleet = Fleet()
    _, mmsi, pos = parse_envelope(POS)
    assert fleet.apply_position(mmsi, pos, "aisstream") is True     # first fix = new
    _, _, static = parse_envelope(STATIC)
    assert fleet.apply_static(mmsi, static, "aisstream") is True    # identity changed
    events = fleet.drain_events()
    assert [e["kind"] for e in events] == ["position", "static"]
    assert fleet.drain_events() == []                               # cleared after drain


def test_fleet_drops_identical_repeats():
    fleet = Fleet()
    _, mmsi, pos = parse_envelope(POS)
    fleet.apply_position(mmsi, pos, "aisstream")
    fleet.drain_events()
    # exact same report again carries no new information → not buffered
    assert fleet.apply_position(mmsi, pos, "aisstream") is False
    assert fleet.drain_events() == []
    # a moved report IS a new signal
    moved = dict(pos, lat=pos["lat"] + 0.01)
    assert fleet.apply_position(mmsi, moved, "aisstream") is True
    assert len(fleet.drain_events()) == 1


# --------------------------- DB row builder ---------------------------

def _merged_vessel() -> dict:
    fleet = Fleet()
    _, mmsi, pos = parse_envelope(POS)
    fleet.apply_position(mmsi, pos, "aisstream")
    _, _, static = parse_envelope(STATIC)
    fleet.apply_static(mmsi, static, "aisstream")
    return fleet.snapshot(max_age_s=10**9)[0]


def test_vessel_row_shape_and_types():
    row = vessel_row(_merged_vessel())
    assert row is not None
    from datetime import datetime
    assert isinstance(row[0], datetime) and row[0].tzinfo is not None  # ts tz-aware
    assert row[1] == "636092297"       # mmsi
    assert row[2] == "aisstream"       # source
    assert row[3] == "terrestrial"     # data_origin
    assert row[6] == 12.3              # speed (knots, not normalized)
    assert row[11] == "EVER GIVEN"     # name


def test_vessel_row_rejects_unkeyable():
    assert vessel_row({"mmsi": "1", "timestamp": None, "lat": 1, "lon": 2}) is None
    assert vessel_row({"mmsi": "", "timestamp": "2026-07-11T00:00:00+00:00"}) is None
    # no position → cannot archive
    assert vessel_row({"mmsi": "1", "timestamp": "2026-07-11T00:00:00+00:00",
                       "lat": None, "lon": None}) is None


def test_vessel_row_preserves_nulls():
    v = _merged_vessel()
    v["speed"] = None
    v["imo"] = None
    row = vessel_row(v)
    assert row[6] is None and row[10] is None   # speed, imo stay null


def test_db_parse_lines_skips_junk():
    good = json.dumps(_merged_vessel())
    rows, invalid = db_parse_lines([good, "{not json", "", "{}"])
    assert len(rows) == 1 and invalid == 2   # junk + empty-dict(no mmsi); blank ignored


# ----------------------- geo classification (M1) -----------------------
# vessel_row appends the four geo columns (country, near_country, distance_km,
# geo_source) at indices 17..20, so the base-column indices above are unchanged.

def test_vessel_row_carries_geo_classification():
    # The EVER GIVEN fixture is at 25.301N 55.42E (Persian Gulf, near the UAE) —
    # beyond every one of the seven countries' territorial seas → OUTSIDE.
    row = vessel_row(_merged_vessel())
    assert row[17] is None                                  # country: no territorial-sea match
    assert row[18] is not None                               # near_country: nearest of the seven
    assert isinstance(row[19], float) and row[19] > 22.224   # distance_km: beyond 12 NM
    assert row[20] == "scraplan-aoi"                         # geo_source: classifier identity


def test_vessel_row_geo_in_territorial_waters():
    # Move the vessel to ~3.6 km off Latakia — inside Syria's territorial sea.
    v = _merged_vessel()
    v["lat"], v["lon"] = 35.55, 35.70
    row = vessel_row(v)
    assert row[17] == "Syria"          # country: territorial-sea match
    assert row[18] is None             # near_country: None when inside the band
    assert row[19] <= 22.224           # distance_km within 12 NM
    assert row[20] == "scraplan-aoi"


# ----------------------- identity spine (M2b) -----------------------
# Static identity is promoted into the vessel_identity SPINE: slow-changing
# identity only (name/type/imo/callsign/dimensions), never voyage state.

STATIC_WITH_DIMS = {
    "MessageType": "ShipStaticData",
    "MetaData": {"MMSI": 636092297},
    "Message": {"ShipStaticData": {
        "ImoNumber": 9321483, "Name": "EVER GIVEN", "CallSign": "H3RC",
        "Type": 70, "Destination": "JEBEL ALI", "MaximumStaticDraught": 14.5,
        "Dimension": {"A": 200, "B": 200, "C": 30, "D": 29},   # length 400, beam 59
    }},
}


def test_static_parse_extracts_dimensions():
    _, _, f = parse_envelope(STATIC_WITH_DIMS)
    assert f["length_m"] == 400 and f["beam_m"] == 59      # A+B, C+D
    assert f["imo"] == "9321483" and f["name"] == "EVER GIVEN"


@pytest.mark.parametrize("dim, length, beam", [
    ({"A": 0, "B": 0, "C": 0, "D": 0}, None, None),   # all zero = "not set" -> None
    ({"A": 100, "B": 50, "C": 0, "D": 0}, 150, None),  # beam not set -> None, length kept
    ({"A": 0, "B": 120, "C": 5, "D": 5}, 120, 10),     # bow-referenced (A=0) still valid
])
def test_static_dimensions_zero_is_none(dim, length, beam):
    env = dict(STATIC_WITH_DIMS)
    env["Message"] = {"ShipStaticData": dict(
        STATIC_WITH_DIMS["Message"]["ShipStaticData"], Dimension=dim)}
    _, _, f = parse_envelope(env)
    assert f["length_m"] == length and f["beam_m"] == beam


def test_static_missing_dimension_is_none():
    # STATIC (no Dimension key at all) -> length/beam None, not an error.
    _, _, f = parse_envelope(STATIC)
    assert f["length_m"] is None and f["beam_m"] is None


def test_fleet_merges_dimensions_into_state():
    fleet = Fleet()
    _, mmsi, static = parse_envelope(STATIC_WITH_DIMS)
    fleet.apply_static(mmsi, static, "aisstream")
    v = fleet._vessels[mmsi]
    assert v.length_m == 400 and v.beam_m == 59


def test_identity_spine_includes_identity_excludes_voyage():
    # The spine carries slow-changing identity ONLY — voyage state stays out.
    rec = _merged_vessel()
    rec["length_m"], rec["beam_m"] = 400, 59
    spine = identity_spine_fields(rec)
    assert set(spine) == set(SPINE_FIELDS)
    assert spine["name"] == "EVER GIVEN" and spine["imo"] == "9321483"
    assert spine["ship_type"] == "cargo" and spine["callsign"] == "H3RC"
    assert spine["length_m"] == 400 and spine["beam_m"] == 59
    # voyage state is deliberately NOT part of the spine
    assert "destination" not in spine and "draught" not in spine and "eta" not in spine


def test_identity_spine_none_when_no_identity():
    # A position-only vessel (no static merged yet) has nothing to promote.
    assert identity_spine_fields({"mmsi": "1", "lat": 1.0, "lon": 2.0}) is None


def test_identity_spine_no_imo_stays_none():
    # A vessel that never broadcast an IMO stores NULL, never a guess.
    rec = _merged_vessel()
    rec["imo"] = None
    spine = identity_spine_fields(rec)
    assert spine is not None            # still has name/type/callsign
    assert spine["imo"] is None


# --------------------------- reconnect / backoff ---------------------------

class FakeWS:
    """Minimal async-iterable WebSocket double."""
    def __init__(self, messages, fail_after_iter=False):
        self._messages = list(messages)
        self._fail = fail_after_iter
        self.sent = []
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self._messages:
            yield m
        if self._fail:
            raise ConnectionError("stream dropped")


@pytest.mark.asyncio
async def test_subscribe_message_shape():
    ws = FakeWS([])

    async def fake_connect(_url):
        return ws

    async def fake_sleep(_s):
        await asyncio.sleep(0)

    adapter = AisStreamAdapter(
        api_key="KEY", bounding_boxes=[[[-90, -180], [90, 180]]],
        connect=fake_connect, sleep=fake_sleep,
    )
    fleet = Fleet()
    # stop as soon as the (empty) stream ends cleanly
    task = asyncio.create_task(adapter.run(fleet))
    await asyncio.sleep(0.05)
    adapter.stop()
    await asyncio.wait_for(task, timeout=2)

    import json
    sub = json.loads(ws.sent[0])
    assert sub["APIKey"] == "KEY"
    assert sub["FilterMessageTypes"] == ["PositionReport", "ShipStaticData"]
    assert sub["BoundingBoxes"] == [[[-90, -180], [90, 180]]]


@pytest.mark.asyncio
async def test_messages_reach_fleet():
    import json
    ws = FakeWS([json.dumps(POS), json.dumps(STATIC)])

    async def fake_connect(_url):
        return ws

    adapter = AisStreamAdapter(api_key="KEY", connect=fake_connect)
    fleet = Fleet()
    task = asyncio.create_task(adapter.run(fleet))
    await asyncio.sleep(0.05)
    adapter.stop()
    await asyncio.wait_for(task, timeout=2)

    assert adapter.messages == 2
    snap = fleet.snapshot(max_age_s=10**9)
    assert len(snap) == 1 and snap[0]["name"] == "EVER GIVEN"


@pytest.mark.asyncio
async def test_reconnect_backoff_and_gap_signals(tmp_path):
    """A dropped session must reconnect with growing backoff and buffer a
    feed_disconnect signal (with session duration) for the collector to log."""
    attempts = {"n": 0}
    backoffs: list[float] = []

    async def flaky_connect(_url):
        attempts["n"] += 1
        # first two sessions drop after yielding once; then stop
        return FakeWS([json.dumps(POS)], fail_after_iter=attempts["n"] <= 2)

    async def fake_sleep(s):
        backoffs.append(s)
        await asyncio.sleep(0)   # yield so the stop timer can fire

    adapter = AisStreamAdapter(api_key="KEY", connect=flaky_connect, sleep=fake_sleep)
    fleet = Fleet()
    task = asyncio.create_task(adapter.run(fleet))
    await asyncio.sleep(0.1)
    adapter.stop()
    await asyncio.wait_for(task, timeout=2)

    assert adapter.reconnects >= 2
    # exponential: second backoff strictly greater than the first
    assert len(backoffs) >= 2 and backoffs[1] > backoffs[0] >= BACKOFF_MIN
    # signals buffered for the collector: feed_connect + feed_disconnect(s)
    signals = adapter.drain_signals()
    kinds = [k for k, _ in signals]
    assert "feed_disconnect" in kinds and "feed_connect" in kinds
    disc = next(d for k, d in signals if k == "feed_disconnect")
    assert "session_seconds" in disc and "reason" in disc
    assert adapter.drain_signals() == []   # cleared after drain


@pytest.mark.asyncio
async def test_clean_session_end_emits_feed_disconnect():
    """G3 Finding B (collector hardening): a session that ends by a CLEAN closure
    (async-for exhaustion, no exception) must STILL emit feed_disconnect, so it can
    never leave an unpaired feed_connect for the next reconnect. Before the fix only
    the exception path emitted one, so clean closures died silently."""
    import json
    async def clean_connect(_url):
        return FakeWS([json.dumps(POS)])   # ends cleanly — NO fail_after_iter

    async def fake_sleep(_s):
        await asyncio.sleep(0)

    adapter = AisStreamAdapter(api_key="KEY", connect=clean_connect, sleep=fake_sleep)
    fleet = Fleet()
    task = asyncio.create_task(adapter.run(fleet))
    await asyncio.sleep(0.02)
    adapter.stop()
    await asyncio.wait_for(task, timeout=2)

    kinds = [k for k, _ in adapter.drain_signals()]
    # every clean session end is paired — connects and disconnects balance exactly
    assert kinds.count("feed_connect") == kinds.count("feed_disconnect") >= 1
