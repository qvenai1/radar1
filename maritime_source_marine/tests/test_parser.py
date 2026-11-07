"""Parser tests, anchored on a real captured tile row."""

from __future__ import annotations

import gzip
import json

from mtscraper.models import RawResponse
from mtscraper.parser import ResponseDecoder, VesselParser

# Verbatim first row from a live get_data_json_4 capture (X36/Y24, zoom 7).
REAL_ROW = {
    "LAT": "40.895706",
    "LON": "27.469486",
    "SPEED": "123",
    "COURSE": "277",
    "HEADING": "200",
    "ELAPSED": "2",
    "DESTINATION": "TRTEK",
    "FLAG": "LR",
    "LENGTH": "399",
    "ROT": "0",
    "SHIPNAME": "MSC AMELIA",
    "SHIPTYPE": "7",
    "SHIP_ID": "6663445",
    "WIDTH": "61",
    "L_FORE": "128",
    "W_LEFT": "34",
    "DWT": "228406",
    "GT_SHIPTYPE": "11",
}

TILE_PAYLOAD = {"type": 1, "data": {"rows": [REAL_ROW], "areaShips": 1}}


def _resp(body: bytes, headers: dict[str, str] | None = None) -> RawResponse:
    return RawResponse(
        url="https://www.marinetraffic.com/getData/get_data_json_4/z:7/X:36/Y:24/station:0",
        status=200,
        headers=headers or {"content-type": "application/json"},
        body=body,
        source="interception",
    )


def test_parse_real_row() -> None:
    vessels = VesselParser().parse(TILE_PAYLOAD)
    assert len(vessels) == 1
    v = vessels[0]
    assert v.ship_id == "6663445"
    assert v.latitude == 40.895706
    assert v.longitude == 27.469486
    assert v.speed_knots == 12.3  # SPEED "123" -> tenths of a knot
    assert v.course == 277.0
    assert v.heading == 200.0
    assert v.shipname == "MSC AMELIA"
    assert v.flag == "LR"
    assert v.destination == "TRTEK"
    assert v.length_m == 399.0
    assert v.width_m == 61.0
    assert v.has_position


def test_heading_511_becomes_none() -> None:
    row = dict(REAL_ROW, HEADING="511")
    v = VesselParser().parse({"data": {"rows": [row]}})[0]
    assert v.heading is None


def test_row_without_ship_id_is_dropped() -> None:
    row = {k: val for k, val in REAL_ROW.items() if k != "SHIP_ID"}
    assert VesselParser().parse({"data": {"rows": [row]}}) == []


def test_bare_list_payload() -> None:
    assert len(VesselParser().parse([REAL_ROW])) == 1


def test_empty_rows() -> None:
    assert VesselParser().parse({"data": {"rows": [], "areaShips": 0}}) == []


def test_decoder_plain_json() -> None:
    body = json.dumps(TILE_PAYLOAD).encode()
    assert ResponseDecoder().decode(_resp(body)) == TILE_PAYLOAD


def test_decoder_gzip_by_header() -> None:
    body = gzip.compress(json.dumps(TILE_PAYLOAD).encode())
    resp = _resp(body, {"content-type": "application/json", "content-encoding": "gzip"})
    assert ResponseDecoder().decode(resp) == TILE_PAYLOAD


def test_decoder_gzip_without_header_falls_through() -> None:
    body = gzip.compress(json.dumps(TILE_PAYLOAD).encode())
    assert ResponseDecoder().decode(_resp(body)) == TILE_PAYLOAD
