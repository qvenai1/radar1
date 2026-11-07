"""Unit tests for the binCraft binary decoder.

Validated against real payloads captured from the live ADS-B Exchange globe
(``fixtures/*.zst`` — zstd-compressed binCraft), plus synthetic buffers for the
edge cases (stale-record skipping, malformed input) that are hard to capture.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
import zstandard

from adsb_scraper.exceptions import BinCraftError
from adsb_scraper.models import RawResponse
from adsb_scraper.parser import AdaptiveAircraftParser, BinCraftDecoder, ResponseDecoder

FIXTURES = Path(__file__).parent / "fixtures"
STRIDE = 112

decoder = BinCraftDecoder()


def _fixture_bytes(name: str) -> bytes:
    return zstandard.ZstdDecompressor().decompress((FIXTURES / name).read_bytes())


# --------------------------------------------------------------------------- #
# Real captured payload
# --------------------------------------------------------------------------- #


def test_decodes_expected_aircraft_count() -> None:
    result = decoder.decode(_fixture_bytes("bincraft_riyadh_box.zst"))
    assert result["stride"] == STRIDE
    assert len(result["aircraft"]) == 361


def test_first_record_field_values() -> None:
    """Spot-check known field values (offsets/scales verified vs readsb source)."""
    first = decoder.decode(_fixture_bytes("bincraft_riyadh_box.zst"))["aircraft"][0]
    assert first["hex"] == "a8e6e2"
    assert first["alt_baro"] == 7425
    assert first["gs"] == 272.0
    assert first["track"] == pytest.approx(129.78, abs=0.01)
    assert first["squawk"] == "6511"
    assert first["flight"] == "PDT5980"
    assert first["r"] == "N673AE"
    assert first["rssi"] == pytest.approx(-43.1, abs=0.1)


def test_positions_fall_inside_reported_box() -> None:
    result = decoder.decode(_fixture_bytes("bincraft_riyadh_box.zst"))
    box = result["box"]
    for ac in result["aircraft"]:
        assert box["south"] - 1 <= ac["lat"] <= box["north"] + 1
        assert box["west"] - 1 <= ac["lon"] <= box["east"] + 1


def test_second_fixture_also_decodes() -> None:
    result = decoder.decode(_fixture_bytes("bincraft_sample2.zst"))
    assert result["stride"] == STRIDE
    assert len(result["aircraft"]) > 0


def test_end_to_end_via_response_decoder() -> None:
    """zstd + ?binCraft URL -> decode -> AdaptiveAircraftParser -> Aircraft."""
    body = (FIXTURES / "bincraft_riyadh_box.zst").read_bytes()
    response = RawResponse(
        url="https://globe.adsbexchange.com/re-api/?binCraft&zstd&box=39,42,-76,-71",
        status=200,
        headers={"content-type": "application/zstd"},
        body=body,
        source="interception",
    )
    payload = ResponseDecoder().decode(response)
    parsed = AdaptiveAircraftParser().parse(payload)
    assert parsed.count == 361
    assert parsed.aircraft[0].icao == "a8e6e2"
    assert parsed.aircraft[0].callsign == "PDT5980"


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def test_looks_like_via_url_param() -> None:
    assert decoder.looks_like(b"anything", "https://x/re-api/?binCraft&zstd")


def test_looks_like_via_header_signature() -> None:
    buf = _make_buffer([_make_record("abc123", 24.7, 46.6)])
    assert decoder.looks_like(buf, "https://x/re-api/?opaque")


def test_looks_like_rejects_json() -> None:
    assert not decoder.looks_like(b'{"aircraft": []}', "https://x/data.json")


# --------------------------------------------------------------------------- #
# Synthetic buffers: skipping stale records and malformed input
# --------------------------------------------------------------------------- #


def _make_record(hex_id: str, lat: float, lon: float, *, position_valid: bool = True) -> bytes:
    rec = bytearray(STRIDE)
    struct.pack_into("<I", rec, 0, int(hex_id, 16))
    struct.pack_into("<i", rec, 8, round(lon * 1e6))
    struct.pack_into("<i", rec, 12, round(lat * 1e6))
    struct.pack_into("<h", rec, 20, 400)  # baro_alt raw -> 10000 ft
    struct.pack_into("<h", rec, 34, 2500)  # gs raw -> 250 kt
    struct.pack_into("<h", rec, 40, 9000)  # track raw -> 100 deg
    valid1 = 0
    if position_valid:
        valid1 |= 1 << 6  # position_valid
    valid1 |= 1 << 4  # baro_alt_valid
    valid1 |= 1 << 7  # gs_valid
    rec[73] = valid1
    rec[74] = 1 << 3  # track_valid
    return bytes(rec)


def _make_buffer(records: list[bytes]) -> bytes:
    header = bytearray(STRIDE)
    struct.pack_into("<I", header, 8, STRIDE)  # stride
    struct.pack_into("<I", header, 12, len(records))  # count
    struct.pack_into("<4h", header, 20, 24, 46, 25, 47)  # S, W, N, E
    return bytes(header) + b"".join(records)


def test_skips_records_without_valid_position() -> None:
    buf = _make_buffer(
        [
            _make_record("aaaaaa", 24.7, 46.6, position_valid=True),
            _make_record("bbbbbb", 0.0, 0.0, position_valid=False),  # stale -> skipped
            _make_record("cccccc", 24.8, 46.7, position_valid=True),
        ]
    )
    result = decoder.decode(buf)
    assert [ac["hex"] for ac in result["aircraft"]] == ["aaaaaa", "cccccc"]


def test_synthetic_record_scales() -> None:
    result = decoder.decode(_make_buffer([_make_record("abc123", 24.71, 46.67)]))
    ac = result["aircraft"][0]
    assert ac["hex"] == "abc123"
    assert ac["lat"] == pytest.approx(24.71, abs=1e-6)
    assert ac["lon"] == pytest.approx(46.67, abs=1e-6)
    assert ac["alt_baro"] == 10000
    assert ac["gs"] == 250.0
    assert ac["track"] == 100.0


def test_malformed_stride_raises() -> None:
    bad = struct.pack("<4I", 0, 0, 7, 1) + bytes(50)  # stride=7 (implausible)
    with pytest.raises(BinCraftError):
        decoder.decode(bad)


def test_truncated_buffer_raises() -> None:
    with pytest.raises(BinCraftError):
        decoder.decode(b"\x00\x00")


def test_length_not_multiple_of_stride_raises() -> None:
    buf = _make_buffer([_make_record("aaaaaa", 24.7, 46.6)]) + b"\x00\x00\x00"
    with pytest.raises(BinCraftError):
        decoder.decode(buf)
