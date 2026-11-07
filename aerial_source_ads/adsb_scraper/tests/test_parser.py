"""Unit tests for response decoding and adaptive aircraft extraction."""

from __future__ import annotations

import gzip
import io
import json
from collections.abc import Mapping

import pytest
import zstandard

from adsb_scraper.exceptions import AircraftArrayNotFoundError, MalformedPayloadError
from adsb_scraper.models import RawResponse
from adsb_scraper.parser import AdaptiveAircraftParser, ResponseDecoder

decoder = ResponseDecoder()
parser = AdaptiveAircraftParser()


def _resp(
    body: bytes, content_type: str = "application/octet-stream", encoding: str | None = None
) -> RawResponse:
    headers = {"content-type": content_type}
    if encoding:
        headers["content-encoding"] = encoding
    return RawResponse(url="u", status=200, headers=headers, body=body, source="t")


# -- decoder ---------------------------------------------------------------- #


def test_decode_plain_json() -> None:
    assert decoder.decode(_resp(b'{"a": 1}')) == {"a": 1}


def test_decode_gzip_with_wrong_content_type() -> None:
    """The exact production failure: gzip body, non-JSON content-type."""
    payload = {"aircraft": [{"hex": "abc", "lat": 1.0, "lon": 2.0}]}
    body = gzip.compress(json.dumps(payload).encode())
    assert decoder.decode(_resp(body, "application/octet-stream", "gzip")) == payload


def test_decode_gzip_by_magic_without_header() -> None:
    body = gzip.compress(b'{"ok": true}')
    assert decoder.decode(_resp(body, "text/html")) == {"ok": True}


# -- zstandard (the real ADS-B Exchange interception format) ---------------- #

_ZSTD_PAYLOAD = {"aircraft": [{"hex": "abc", "lat": 24.7, "lon": 46.6, "gs": 400}]}


def _zstd_embedded(payload: Mapping[str, object]) -> bytes:
    """A zstd frame that embeds its content size (single-shot ``compress``)."""
    return zstandard.ZstdCompressor().compress(json.dumps(payload).encode())


def _zstd_streaming(payload: Mapping[str, object]) -> bytes:
    """A zstd frame WITHOUT an embedded content size (streaming writer)."""
    buffer = io.BytesIO()
    with zstandard.ZstdCompressor().stream_writer(buffer, closefd=False) as writer:
        writer.write(json.dumps(payload).encode())
    return buffer.getvalue()


def test_decode_zstd_content_type_header() -> None:
    """The exact production case: application/zstd, embedded-size frame."""
    body = _zstd_embedded(_ZSTD_PAYLOAD)
    assert body[:4] == b"\x28\xb5\x2f\xfd"  # zstd magic
    assert decoder.decode(_resp(body, "application/zstd")) == _ZSTD_PAYLOAD


def test_decode_zstd_content_encoding_header() -> None:
    body = _zstd_embedded(_ZSTD_PAYLOAD)
    assert decoder.decode(_resp(body, "application/octet-stream", "zstd")) == _ZSTD_PAYLOAD


def test_decode_zstd_streaming_frame_without_size() -> None:
    """Streaming frames omit the content size; the stream_reader fallback wins."""
    body = _zstd_streaming(_ZSTD_PAYLOAD)
    # Sanity: single-shot decompress cannot handle this frame on its own.
    with pytest.raises(zstandard.ZstdError):
        zstandard.ZstdDecompressor().decompress(body)
    assert decoder.decode(_resp(body, "application/zstd")) == _ZSTD_PAYLOAD


def test_decode_zstd_without_header_via_chain() -> None:
    """Even with no codec header, the try-everything chain reaches zstd."""
    body = _zstd_embedded(_ZSTD_PAYLOAD)
    assert decoder.decode(_resp(body, "application/octet-stream")) == _ZSTD_PAYLOAD


def test_undecodable_lists_every_attempt_including_zstd() -> None:
    with pytest.raises(MalformedPayloadError) as info:
        decoder.decode(_resp(b"<html>not json</html>", "text/html"))
    message = str(info.value)
    assert "zstd" in message and "brotli" in message and "attempts" in message


def test_decode_text_fallback_latin1() -> None:
    body = '{"name": "café"}'.encode("latin-1")
    assert decoder.decode(_resp(body)) == {"name": "café"}


def test_decode_empty_raises() -> None:
    with pytest.raises(MalformedPayloadError):
        decoder.decode(_resp(b""))


def test_decode_garbage_raises_with_context() -> None:
    with pytest.raises(MalformedPayloadError) as info:
        decoder.decode(_resp(b"<html>not json</html>", "text/html"))
    assert "attempts" in str(info.value)


# -- adaptive parser -------------------------------------------------------- #


def test_parse_readsb() -> None:
    payload = {
        "now": 1.0,
        "aircraft": [
            {
                "hex": "a1",
                "lat": 60.1,
                "lon": 24.9,
                "alt_baro": 35000,
                "track": 270,
                "flight": "FIN1 ",
                "gs": 450,
            }
        ],
    }
    result = parser.parse(payload)
    assert result.count == 1
    ac = result.aircraft[0]
    assert ac.icao == "a1" and ac.callsign == "FIN1" and ac.ground_speed == 450


def test_parse_vrs_aclist_pascalcase() -> None:
    payload = {"acList": [{"Icao": "DEAD01", "Lat": 51.5, "Long": -0.1, "Trak": 90}]}
    result = parser.parse(payload)
    assert result.aircraft[0].icao == "dead01"
    assert result.aircraft[0].longitude == -0.1


def test_parse_opensky_dicts() -> None:
    payload = {
        "states": [
            {
                "icao24": "4b1806",
                "latitude": 47.1,
                "longitude": 8.5,
                "baro_altitude": 9000,
                "true_track": 180,
                "velocity": 230,
            }
        ]
    }
    result = parser.parse(payload)
    ac = result.aircraft[0]
    assert ac.icao == "4b1806" and ac.altitude == 9000 and ac.ground_speed == 230


def test_parse_finds_array_at_unusual_path() -> None:
    payload = {
        "meta": {"v": 1},
        "data": {
            "vehicles": [
                {"hex": "aa", "lat": 1.0, "lon": 2.0, "track": 5},
                {"hex": "bb", "lat": 3.0, "lon": 4.0, "gs": 6},
            ]
        },
    }
    result = parser.parse(payload)
    assert result.count == 2
    assert "vehicles" in result.source_path


def test_parse_empty_known_key_returns_zero_not_error() -> None:
    result = parser.parse({"aircraft": []})
    assert result.count == 0


def test_parse_unknown_structure_raises() -> None:
    with pytest.raises(AircraftArrayNotFoundError):
        parser.parse({"status": "ok", "items": [1, 2, 3]})


def test_parse_invalid_coordinates_dropped() -> None:
    result = parser.parse({"aircraft": [{"hex": "z", "lat": 999.0, "lon": -0.1}]})
    ac = result.aircraft[0]
    assert ac.latitude is None and ac.longitude == -0.1
    assert not ac.has_position


def test_parse_skips_elements_without_icao() -> None:
    result = parser.parse(
        {"aircraft": [{"lat": 1.0, "lon": 2.0}, {"hex": "ok", "lat": 3.0, "lon": 4.0}]}
    )
    assert result.count == 1
    assert result.skipped == 1


def test_parse_top_level_list() -> None:
    result = parser.parse([{"hex": "aa", "lat": 1.0, "lon": 2.0}])
    assert result.count == 1 and result.source_path == "$"
