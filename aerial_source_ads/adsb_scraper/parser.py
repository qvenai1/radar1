"""Decoding raw bytes and adaptively extracting aircraft.

This module is the direct answer to the two failure modes that made the
original scraper silently collect nothing:

1. **Rigid decoding.** The old code required ``Content-Type: application/json``
   and dropped everything else — but ADS-B Exchange serves the body gzip-encoded
   with a non-JSON content type. :class:`ResponseDecoder` instead tries a ladder
   of strategies (raw JSON → decompress → text → alternative encodings) and only
   gives up, loudly, when none work.

2. **Rigid structure.** The old code hunted a fixed set of keys
   (``aircraft``/``planes``/``acList``/``flights``). :class:`AdaptiveAircraftParser`
   inspects the JSON tree, scores candidate arrays by how "aircraft-like" their
   elements are, and normalises via a field-alias table — so a renamed field or
   a brand-new feed layout degrades gracefully instead of returning zero.

Neither class ever swallows an unrecognised payload: the caller is handed an
exception carrying enough context to archive the bytes for offline analysis.
"""

from __future__ import annotations

import gzip
import io
import json
import struct
import zlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import zstandard

from . import constants as C
from .exceptions import AircraftArrayNotFoundError, BinCraftError, MalformedPayloadError
from .logger import get_logger
from .models import Aircraft, RawResponse
from .utils import coerce_float, coerce_str

_log = get_logger("parser")

_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "latin-1")
_MAX_SEARCH_DEPTH = 6
_ARRAY_SAMPLE = 25
_ARRAY_QUALIFY_RATIO = 0.6


class _BrotliUnavailable(Exception):
    """Raised when a payload looks like brotli but the codec is not installed."""


#: Exceptions any decompressor may raise on malformed / wrong-codec input.
_DECOMP_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    EOFError,
    ValueError,
    zlib.error,
    zstandard.ZstdError,
    _BrotliUnavailable,
)


# --------------------------------------------------------------------------- #
# binCraft binary decoding
# --------------------------------------------------------------------------- #

#: ADS-B address-type nibble (readsb ``addrtype_t``) -> readsb JSON ``type`` value.
_ADDRTYPES: tuple[str, ...] = (
    "adsb_icao",
    "adsb_icao_nt",
    "adsr_icao",
    "tisb_icao",
    "adsc",
    "mlat",
    "other",
    "mode_s",
    "adsb_other",
    "adsr_other",
    "tisb_trackfile",
    "tisb_other",
    "mode_ac",
)


class BinCraftDecoder:
    """Decodes readsb/tar1090 ``binCraft`` binary aircraft buffers.

    The wire format (readsb ``struct binCraft`` in ``aircraft.h``): the first
    ``stride`` bytes are a header, then each subsequent ``stride``-byte block is
    one aircraft. Everything is little-endian and packed. All offsets and scale
    factors below were taken from the readsb source (``toBinCraft``) and verified
    byte-for-byte against real captured payloads — not guessed.

    The output is a ``{"aircraft": [...]}`` dict using readsb JSON field names, so
    the existing :class:`AdaptiveAircraftParser` consumes it unchanged.
    """

    __slots__ = ()

    # Plausible record size (multiple of 4; readsb is ~112). Used for detection.
    _MIN_STRIDE = 88
    _MAX_STRIDE = 256

    # Per-record byte offsets (readsb struct binCraft).
    _O_HEX = 0  # uint32; low 24 bits = ICAO address
    _O_SEEN = 4  # int32; (now - seen)/100  -> value/10 == seconds
    _O_LON = 8  # int32; / 1e6
    _O_LAT = 12  # int32; / 1e6
    _O_BARO_RATE = 16  # int16; * 8   (ft/min)
    _O_GEOM_RATE = 18  # int16; * 8
    _O_BARO_ALT = 20  # int16; * 25  (ft)
    _O_GEOM_ALT = 22  # int16; * 25
    _O_NAV_QNH = 28  # int16; / 10
    _O_NAV_HEADING = 30  # int16; / 90
    _O_SQUAWK = 32  # uint16; BCD hex
    _O_GS = 34  # int16; / 10  (knots)
    _O_MACH = 36  # int16; / 1000
    _O_TRACK = 40  # int16; / 90  (degrees)
    _O_MESSAGES = 62  # uint16
    _O_CATEGORY = 64  # uint8
    _O_NIC = 65  # uint8
    _O_ADDRTYPE = 67  # high nibble = addrtype
    _O_AIRGROUND = 68  # low nibble = air/ground
    _O_VALID1 = 73  # bit6 position_valid, bit4 baro_alt_valid, bit5 geom_alt_valid, ...
    _O_VALID2 = 74  # bit3 track_valid
    _O_CALLSIGN = 78  # char[8]
    _O_DBFLAGS = 86  # uint16
    _O_TYPECODE = 88  # char[4]
    _O_REGISTRATION = 92  # char[12]
    _O_RECEIVERCOUNT = 104  # uint8
    _O_SIGNAL = 105  # uint8; RSSI = raw*(50/255) - 50  (dBFS)
    _O_SEEN_POS = 108  # int32; (now - seenPos)/100 -> value/10 == seconds

    def looks_like(self, data: bytes, url: str) -> bool:
        """Detect binCraft via the ``?binCraft`` URL param or a header signature."""
        if "bincraft" in url.lower():
            return True
        if len(data) < 12:
            return False
        stride = int.from_bytes(data[8:12], "little")
        return (
            self._MIN_STRIDE <= stride <= self._MAX_STRIDE
            and stride % 4 == 0
            and len(data) % stride == 0
            and len(data) > stride
        )

    def decode(self, data: bytes) -> dict[str, Any]:
        """Decode a binCraft buffer into ``{"aircraft": [...]}`` (readsb JSON keys)."""
        if len(data) < 24:
            raise BinCraftError(f"binCraft buffer too small: {len(data)} bytes")
        now_lo, now_hi, stride, ac_count = struct.unpack_from("<4I", data, 0)
        if not (self._MIN_STRIDE <= stride <= self._MAX_STRIDE) or stride % 4:
            raise BinCraftError(f"implausible binCraft stride: {stride}")
        if len(data) % stride:
            raise BinCraftError(f"binCraft length {len(data)} is not a multiple of stride {stride}")
        south, west, north, east = struct.unpack_from("<4h", data, 20)

        aircraft: list[dict[str, Any]] = []
        for off in range(stride, len(data), stride):
            record = self._decode_record(data, off)
            if record is not None:
                aircraft.append(record)

        return {
            "now": now_lo / 1000 + now_hi * 4294967.296,
            "stride": stride,
            "global_ac_count_withpos": ac_count,
            "box": {"south": south, "west": west, "north": north, "east": east},
            "aircraft": aircraft,
        }

    def _decode_record(self, data: bytes, off: int) -> dict[str, Any] | None:
        valid1 = data[off + self._O_VALID1]
        # Skip stale/empty records: no valid position means nothing useful here.
        if not (valid1 >> 6) & 1:
            return None

        hex_id = f"{struct.unpack_from('<I', data, off + self._O_HEX)[0] & 0xFFFFFF:06x}"
        lon = struct.unpack_from("<i", data, off + self._O_LON)[0] / 1e6
        lat = struct.unpack_from("<i", data, off + self._O_LAT)[0] / 1e6

        baro_alt_valid = (valid1 >> 4) & 1
        geom_alt_valid = (valid1 >> 5) & 1
        gs_valid = (valid1 >> 7) & 1
        track_valid = (data[off + self._O_VALID2] >> 3) & 1
        airground = data[off + self._O_AIRGROUND] & 0x0F

        alt_baro: float | str | None
        if airground == 1:
            alt_baro = "ground"
        elif baro_alt_valid:
            alt_baro = struct.unpack_from("<h", data, off + self._O_BARO_ALT)[0] * 25
        else:
            alt_baro = None

        squawk_raw = struct.unpack_from("<H", data, off + self._O_SQUAWK)[0]
        signal_raw = data[off + self._O_SIGNAL]
        addrtype = (data[off + self._O_ADDRTYPE] >> 4) & 0x0F
        seen = struct.unpack_from("<i", data, off + self._O_SEEN)[0] / 10.0
        seen_pos = struct.unpack_from("<i", data, off + self._O_SEEN_POS)[0] / 10.0

        record: dict[str, Any] = {
            "hex": hex_id,
            "type": _ADDRTYPES[addrtype] if addrtype < len(_ADDRTYPES) else "unknown",
            "lat": lat,
            "lon": lon,
            "alt_baro": alt_baro,
            "seen": seen,
            "seen_pos": seen_pos,
            "messages": struct.unpack_from("<H", data, off + self._O_MESSAGES)[0],
            "rssi": round(signal_raw * (50.0 / 255.0) - 50.0, 1),
        }
        if geom_alt_valid:
            record["alt_geom"] = struct.unpack_from("<h", data, off + self._O_GEOM_ALT)[0] * 25
        if gs_valid:
            record["gs"] = struct.unpack_from("<h", data, off + self._O_GS)[0] / 10.0
        if track_valid:
            record["track"] = struct.unpack_from("<h", data, off + self._O_TRACK)[0] / 90.0
        if squawk_raw:
            record["squawk"] = f"{squawk_raw:04x}"
        flight = self._read_string(data, off + self._O_CALLSIGN, 8)
        if flight:
            record["flight"] = flight
        registration = self._read_string(data, off + self._O_REGISTRATION, 12)
        if registration:
            record["r"] = registration
        type_code = self._read_string(data, off + self._O_TYPECODE, 4)
        if type_code:
            record["t"] = type_code
        category = data[off + self._O_CATEGORY]
        if category:
            record["category"] = f"{category:02X}"
        return record

    @staticmethod
    def _read_string(data: bytes, start: int, length: int) -> str:
        chunk = data[start : start + length]
        end = chunk.find(0)
        if end != -1:
            chunk = chunk[:end]
        return chunk.decode("ascii", "ignore").strip()


# --------------------------------------------------------------------------- #
# Response decoding
# --------------------------------------------------------------------------- #


class ResponseDecoder:
    """Turns arbitrary response bytes into a Python object, or fails loudly.

    Decoding is content-aware first: if a ``Content-Encoding`` or
    ``Content-Type`` header names a codec (``application/zstd``, ``gzip``, ...)
    the matching decompressor is applied directly. Only when no header helps do
    we fall back to trying every codec in turn. Every attempt — successful or
    not — is recorded so a failure message names exactly what was tried.

    After a codec yields bytes, they are *interpreted*: readsb/tar1090 binCraft
    binary (detected by the ``?binCraft`` URL param or a header signature) is
    routed to :class:`BinCraftDecoder`; everything else is parsed as JSON.
    """

    __slots__ = ("_bincraft",)

    #: Order used by the try-everything fallback when no header identifies a codec.
    _CHAIN: tuple[str, ...] = ("gzip", "zlib", "deflate", "zstd", "brotli")

    def __init__(self, bincraft: BinCraftDecoder | None = None) -> None:
        self._bincraft = bincraft or BinCraftDecoder()

    def decode(self, response: RawResponse) -> Any:
        """Decode ``response.body`` to a JSON-compatible object.

        Raises :class:`MalformedPayloadError` if every strategy fails; the
        exception message names each attempt so the log tells the full story.
        """
        body = response.body
        if not body:
            raise MalformedPayloadError(f"empty body from {response.source} ({response.url})")

        attempts: list[str] = []

        # 1. Content-aware fast path: a header tells us the codec, so use it
        #    directly instead of blindly trying every method.
        codec = self._codec_from_headers(response)
        if codec is not None:
            obj = self._decode_with_codec(codec, body, response, attempts, label=f"header:{codec}")
            if obj is not _SENTINEL:
                return obj

        # 2. Straight bytes — JSON, or binCraft if the shape/URL says so.
        obj = self._interpret(body, response, attempts, "raw")
        if obj is not _SENTINEL:
            return obj

        # 3. Try-everything decompression chain, then interpret:
        #    gzip -> zlib -> deflate -> zstd -> brotli.
        for name in self._CHAIN:
            if name == codec:
                continue  # already attempted via the content-aware fast path
            obj = self._decode_with_codec(name, body, response, attempts, label=name)
            if obj is not _SENTINEL:
                return obj

        # 4. Text decoding with several encodings, then JSON.
        for encoding in _TEXT_ENCODINGS:
            try:
                text = body.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                attempts.append(f"text:{encoding}=decode-error")
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                attempts.append(f"text:{encoding}=json-error({exc.msg})")

        raise MalformedPayloadError(
            f"could not decode {response.size}B from {response.source} "
            f"({response.content_type or 'no content-type'}); attempts: {attempts}"
        )

    @staticmethod
    def _codec_from_headers(response: RawResponse) -> str | None:
        """Pick a codec from the response headers, or ``None`` if none apply."""
        # Content-Encoding is authoritative; the outermost encoding is applied
        # last on the wire, so it is what we must undo first.
        tokens = [
            token.strip()
            for token in response.headers.get("content-encoding", "").lower().split(",")
            if token.strip()
        ]
        for token in reversed(tokens):
            if token == "zstd":
                return "zstd"
            if token in ("gzip", "x-gzip"):
                return "gzip"
            if token == "br":
                return "brotli"
            if token == "deflate":
                return "deflate"
        # Otherwise fall back to hints in the Content-Type (e.g. application/zstd).
        content_type = response.headers.get("content-type", "").lower()
        if "zstd" in content_type:
            return "zstd"
        if "gzip" in content_type:
            return "gzip"
        if "brotli" in content_type:
            return "brotli"
        return None

    def _decode_with_codec(
        self, codec: str, body: bytes, response: RawResponse, attempts: list[str], label: str
    ) -> Any:
        """Decompress with ``codec`` then interpret; ``_SENTINEL`` on failure."""
        decompressed = self._decompress(codec, body, attempts, label)
        if decompressed is None:
            return _SENTINEL
        return self._interpret(decompressed, response, attempts, label)

    def _interpret(
        self, data: bytes, response: RawResponse, attempts: list[str], label: str
    ) -> Any:
        """Turn decoded bytes into an object: binCraft binary, else JSON."""
        if self._bincraft.looks_like(data, response.url):
            try:
                return self._bincraft.decode(data)
            except BinCraftError as exc:
                attempts.append(f"{label}:bincraft=error({exc})")
                return _SENTINEL
        return self._try_json_bytes(data, attempts, label=label)

    @staticmethod
    def _decompress(codec: str, body: bytes, attempts: list[str], label: str) -> bytes | None:
        try:
            if codec == "gzip":
                return gzip.decompress(body)
            if codec == "zlib":
                return zlib.decompress(body, zlib.MAX_WBITS)
            if codec == "deflate":
                return zlib.decompress(body, -zlib.MAX_WBITS)
            if codec == "zstd":
                return ResponseDecoder._zstd_decompress(body)
            if codec == "brotli":
                return ResponseDecoder._brotli_decompress(body)
        except _DECOMP_ERRORS as exc:
            attempts.append(f"{label}=error({exc})")
            return None
        attempts.append(f"{label}=unknown-codec")
        return None

    @staticmethod
    def _zstd_decompress(body: bytes) -> bytes:
        """Decompress a Zstandard payload.

        ``decompress`` works when the frame embeds its content size; feeds that
        aren't self-terminating (streaming frames) omit the size, so we fall back
        to a streaming reader which does not require it.
        """
        dctx = zstandard.ZstdDecompressor()
        try:
            return dctx.decompress(body)
        except zstandard.ZstdError:
            with dctx.stream_reader(io.BytesIO(body)) as reader:
                return reader.read()

    @staticmethod
    def _brotli_decompress(body: bytes) -> bytes:
        try:
            import brotli
        except ImportError as exc:  # optional codec; only attempted, never required
            raise _BrotliUnavailable("brotli codec not installed") from exc
        try:
            result: bytes = brotli.decompress(body)
        except Exception as exc:  # brotli.error is unimportable here; normalise it
            raise ValueError(f"brotli decode failed: {exc}") from exc
        return result

    @staticmethod
    def _try_json_bytes(data: bytes, attempts: list[str], label: str = "raw") -> Any:
        try:
            return json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            attempts.append(f"json:{label}=error({exc})")
            return _SENTINEL


# A unique sentinel distinct from ``None`` (a valid JSON value).
_SENTINEL: Any = object()


# --------------------------------------------------------------------------- #
# Adaptive aircraft extraction
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Outcome of parsing one payload."""

    aircraft: list[Aircraft]
    source_path: str
    skipped: int

    @property
    def count(self) -> int:
        return len(self.aircraft)


class AdaptiveAircraftParser:
    """Locates and normalises aircraft records from an arbitrary JSON object."""

    __slots__ = ()

    def parse(self, payload: Any) -> ParseResult:
        """Extract aircraft from ``payload``.

        Raises :class:`AircraftArrayNotFoundError` when no plausible aircraft
        array exists — the caller is expected to archive the payload rather than
        treat it as "zero aircraft".
        """
        array, path = self._locate_array(payload)
        aircraft: list[Aircraft] = []
        skipped = 0
        for element in array:
            record = self._normalise(element)
            if record is None:
                skipped += 1
            else:
                aircraft.append(record)
        if skipped:
            _log.debug("normalisation skipped %d/%d elements at %s", skipped, len(array), path)
        return ParseResult(aircraft=aircraft, source_path=path, skipped=skipped)

    # -- array location ---------------------------------------------------- #

    def _locate_array(self, payload: Any) -> tuple[list[Any], str]:
        # A top-level list is itself the candidate.
        if isinstance(payload, list):
            if self._qualifies(payload) or not payload:
                return payload, "$"
            raise AircraftArrayNotFoundError("top-level list does not look like aircraft data")

        if not isinstance(payload, dict):
            raise AircraftArrayNotFoundError(
                f"payload is neither list nor object (got {type(payload).__name__})"
            )

        # Fast path: a conventionally named key holding a list is authoritative,
        # even when empty (a legitimate "no aircraft right now" response).
        for key in C.LIKELY_ARRAY_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value, f"$.{key}"

        # General case: search the whole tree for the best-scoring array.
        best_list, best_path, best_score = self._search(payload, "$", 0)
        if best_list is None:
            raise AircraftArrayNotFoundError(
                f"no aircraft array found in payload (top-level keys: {sorted(payload)[:12]})"
            )
        _log.info("adaptive parser selected array at %s (score=%d)", best_path, best_score)
        return best_list, best_path

    def _search(self, node: Any, path: str, depth: int) -> tuple[list[Any] | None, str, int]:
        """Depth-first hunt for the list with the most aircraft-like elements."""
        best: tuple[list[Any] | None, str, int] = (None, path, 0)
        if depth > _MAX_SEARCH_DEPTH:
            return best
        if isinstance(node, list):
            score = self._array_score(node)
            if score > best[2]:
                best = (node, path, score)
            # Also descend into list elements (arrays of objects that contain arrays).
            for index, item in enumerate(node[:_ARRAY_SAMPLE]):
                if isinstance(item, (list, dict)):
                    candidate = self._search(item, f"{path}[{index}]", depth + 1)
                    if candidate[2] > best[2]:
                        best = candidate
        elif isinstance(node, dict):
            for key, value in node.items():
                candidate = self._search(value, f"{path}.{key}", depth + 1)
                if candidate[2] > best[2]:
                    best = candidate
        return best

    def _array_score(self, array: list[Any]) -> int:
        """How many sampled elements look like aircraft (0 if the array doesn't)."""
        if not array:
            return 0
        sample = array[:_ARRAY_SAMPLE]
        qualifying = sum(
            1 for element in sample if self._element_score(element) >= C.MIN_AIRCRAFT_MATCH_SCORE
        )
        if qualifying / len(sample) < _ARRAY_QUALIFY_RATIO:
            return 0
        # Score = qualifying element count, so the *largest* good array wins.
        return qualifying * len(array) // len(sample) + qualifying

    @staticmethod
    def _element_score(element: Any) -> int:
        """Weighted count of aircraft *concepts* present on an object."""
        if not isinstance(element, dict):
            return 0
        score = 0
        for concept, aliases in C.AIRCRAFT_FIELD_ALIASES.items():
            if any(alias in element for alias in aliases):
                score += 2 if concept in C.STRONG_IDENTIFYING_CONCEPTS else 1
        return score

    def _qualifies(self, array: list[Any]) -> bool:
        return self._array_score(array) > 0

    # -- normalisation ----------------------------------------------------- #

    @staticmethod
    def _resolve(obj: dict[str, Any], concept: str) -> Any:
        for alias in C.AIRCRAFT_FIELD_ALIASES[concept]:
            if alias in obj and obj[alias] is not None:
                return obj[alias]
        return None

    def _normalise(self, element: Any) -> Aircraft | None:
        """Convert one raw element into a validated :class:`Aircraft`.

        Returns ``None`` (a *skip*, not a silent drop — the caller counts them)
        when the element lacks a usable ICAO identifier.
        """
        if not isinstance(element, dict):
            return None
        icao = coerce_str(self._resolve(element, "icao"))
        if not icao:
            return None
        icao = icao.lower()

        lat = coerce_float(self._resolve(element, "latitude"))
        lon = coerce_float(self._resolve(element, "longitude"))
        if lat is not None and not (C.MIN_LAT <= lat <= C.MAX_LAT):
            lat = None
        if lon is not None and not (C.MIN_LON <= lon <= C.MAX_LON):
            lon = None

        return Aircraft(
            icao=icao,
            latitude=lat,
            longitude=lon,
            altitude=coerce_float(self._resolve(element, "altitude")),
            track=coerce_float(self._resolve(element, "track")),
            ground_speed=coerce_float(self._resolve(element, "ground_speed")),
            callsign=coerce_str(self._resolve(element, "callsign")),
            raw=MappingProxyType(dict(element)),
        )
