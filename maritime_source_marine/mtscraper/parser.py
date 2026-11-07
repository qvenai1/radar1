"""Turn raw tile bytes into :class:`Vessel` value objects.

Two responsibilities, kept separate:

* :class:`ResponseDecoder` — bytes -> Python object. Content-aware: it honours
  ``Content-Encoding``/``Content-Type`` then falls back through a codec chain
  (json -> gzip -> zlib -> deflate -> text), so a gzipped or oddly-served body
  never blocks a good response.
* :class:`VesselParser` — decoded object -> ``list[Vessel]``. It reads the
  verified ``get_data_json_4`` shape ``{"data": {"rows": [...]}}`` but tolerates
  a bare list or a differently-nested array, and coerces the all-string row
  values into typed fields.
"""

from __future__ import annotations

import gzip
import json
import zlib
from typing import Any

from . import constants as C
from .exceptions import MalformedPayloadError
from .logger import get_logger
from .models import RawResponse, Vessel

_log = get_logger("parser")


class ResponseDecoder:
    """Decode response bytes into a Python object, codec-agnostically."""

    def decode(self, response: RawResponse) -> Any:
        body = response.body
        if not body:
            raise MalformedPayloadError("empty body")

        # Try plain JSON FIRST. Playwright's ``response.body()`` (and an in-page
        # ``fetch``) already transparently decompress the payload, yet the
        # ``content-encoding: gzip`` header is retained — so trusting that header
        # and gunzipping would fail on an already-plain body. Decompression is
        # therefore only a fallback, for a raw source that is genuinely encoded.
        obj = self._try_json(body)
        if obj is not None:
            return obj
        for transform in (self._gunzip, self._inflate):
            try:
                candidate = transform(body)
            except (OSError, zlib.error):
                continue
            obj = self._try_json(candidate)
            if obj is not None:
                return obj
        raise MalformedPayloadError(
            f"undecodable payload ({len(response.body)} bytes, ct={response.content_type!r})"
        )

    @staticmethod
    def _try_json(body: bytes) -> Any:
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    @staticmethod
    def _gunzip(body: bytes) -> bytes:
        return gzip.decompress(body)

    @staticmethod
    def _inflate(body: bytes) -> bytes:
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)


class VesselParser:
    """Extract vessel rows from a decoded tile payload."""

    def parse(self, payload: Any) -> list[Vessel]:
        rows = self._locate_rows(payload)
        vessels: list[Vessel] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            vessel = self._row_to_vessel(row)
            if vessel is not None:
                vessels.append(vessel)
        return vessels

    # -- row location ------------------------------------------------------ #

    def _locate_rows(self, payload: Any) -> list[Any]:
        """Find the array of vessel rows within a variety of shapes."""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                rows = data.get("rows")
                if isinstance(rows, list):
                    return rows
            top_rows = payload.get("rows")
            if isinstance(top_rows, list):
                return top_rows
            # last resort: the first list-of-dicts value anywhere shallow
            for value in payload.values():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value
        _log.debug("no vessel rows located in payload of type %s", type(payload).__name__)
        return []

    # -- row -> Vessel ----------------------------------------------------- #

    def _row_to_vessel(self, row: dict[str, Any]) -> Vessel | None:
        ship_id = self._first_str(row, C.VESSEL_FIELD_KEYS["ship_id"])
        if not ship_id:
            return None
        lat = self._first_float(row, C.VESSEL_FIELD_KEYS["latitude"])
        lon = self._first_float(row, C.VESSEL_FIELD_KEYS["longitude"])
        speed_raw = self._first_float(row, C.VESSEL_FIELD_KEYS["speed"])
        heading = self._first_float(row, C.VESSEL_FIELD_KEYS["heading"])
        if heading == C.HEADING_UNAVAILABLE:
            heading = None
        return Vessel(
            ship_id=ship_id,
            latitude=lat,
            longitude=lon,
            speed_knots=None if speed_raw is None else round(speed_raw * C.SPEED_SCALE, 3),
            course=self._first_float(row, C.VESSEL_FIELD_KEYS["course"]),
            heading=heading,
            shipname=self._first_str(row, C.VESSEL_FIELD_KEYS["shipname"]),
            shiptype=self._first_str(row, C.VESSEL_FIELD_KEYS["shiptype"]),
            flag=self._first_str(row, C.VESSEL_FIELD_KEYS["flag"]),
            destination=self._first_str(row, C.VESSEL_FIELD_KEYS["destination"]),
            length_m=self._first_float(row, C.VESSEL_FIELD_KEYS["length"]),
            width_m=self._first_float(row, C.VESSEL_FIELD_KEYS["width"]),
            mmsi=self._first_str(row, C.VESSEL_FIELD_KEYS["mmsi"]),
            elapsed_min=self._first_float(row, C.VESSEL_FIELD_KEYS["elapsed"]),
            raw=dict(row),
        )

    @staticmethod
    def _first_str(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip() != "":
                return str(value).strip()
        return None

    @classmethod
    def _first_float(cls, row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        text = cls._first_str(row, keys)
        if text is None:
            return None
        try:
            return float(text)
        except ValueError:
            return None
