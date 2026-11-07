"""
Pure MarineTraffic-record → VesselState translation — no I/O, fully
unit-testable. Kept separate from the adapter (which owns the file tail)
exactly as the AISStream side keeps ``aisstream_parse`` separate from the
socket — and exactly as the aviation side keeps StateVector.from_array
separate from its HTTP client.

INPUT is one line of mtscraper's ``positions.jsonl`` — the DOCUMENTED contract
between the two projects. mtscraper stays a black box: we import none of its
code, only consume its output. The line shape (verified against a live capture):

    {"ship_id":"2786","fetched_at":"2026-07-21T11:07:54.543+00:00",
     "source":"interception","in_area":true,"distance_km":158.5,
     "data":{"ship_id":"2786","mmsi":null,"latitude":31.87,"longitude":34.57,
             "speed_knots":9.3,"course":98.0,"heading":100.0,
             "shipname":"VALENCE","shiptype":"7","flag":"MT",
             "destination":"ILASH","length_m":299.0,"width_m":48.0,
             "elapsed_min":2.0},
     "validation":{...optional...}}

The ``data`` block is ALREADY normalized by mtscraper (speed in knots, heading
511→null, values coerced) so this layer only re-shapes into scraplan's unified
``VesselState`` vocabulary — it never re-implements mtscraper's parsing.

Two impedance mismatches this layer resolves, and why:

  * IDENTITY — MarineTraffic's key is ``ship_id`` (its internal id); ``mmsi`` is
    usually null in the tile feed. ``ship_id`` is therefore carried as a
    first-class field (never faked into the mmsi slot), and ``mmsi`` passes
    through only when genuinely present.
  * FIX TIME — ``fetched_at`` is the COLLECTION time; ``elapsed_min`` is the age
    of the vessel's last report at that instant. The true position-fix time
    (what ``vessel_states.ts`` means, project-wide) is therefore
    ``fetched_at - elapsed_min``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..schema import VesselState


@dataclass(frozen=True, slots=True)
class MtParsed:
    """One MarineTraffic observation, split into the two dicts the shared
    Fleet merges (position + static) — mirroring the AISStream parser's
    (kind, mmsi, fields) contract, but self-contained because each MT line
    already carries position AND identity together."""

    ship_id: str
    mmsi: str | None
    position: dict[str, Any]
    static: dict[str, Any]
    flag: str | None = None            # MT-reported flag (2-letter); provenance only
    in_area: bool = True
    distance_km: float | None = None
    validation: dict[str, Any] = field(default_factory=dict)


def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _fix_timestamp(fetched_at: Any, elapsed_min: Any) -> str | None:
    """The vessel's own position-fix time = fetched_at − elapsed_min, UTC ISO
    8601. ``fetched_at`` is mtscraper's collection instant; ``elapsed_min`` is
    how stale the report already was then. Falls back to fetched_at unchanged
    when elapsed is absent, and to None only when there is no usable stamp."""
    raw = _str(fetched_at)
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return raw  # keep whatever mtscraper wrote; downstream ISO parse can retry
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    elapsed = _num(elapsed_min)
    if elapsed is not None and elapsed > 0:
        dt = dt - timedelta(minutes=elapsed)
    return dt.isoformat()


# MarineTraffic's tile ``SHIPTYPE`` is a COARSE, PROPRIETARY 1-digit summary —
# NOT the AIS type code. Verified against live data: digit 3 covers drillships /
# pipelayers (STENA FORTH, CASTORONE), NOT AIS "3x = fishing"; digit 2 covers
# anchor-handling / supply craft (AHS NIKOLAS), NOT AIS "2x = wing-in-ground". So
# a naive digit×10 → AIS-decade mapping is WRONG for the minority categories. We
# therefore label ONLY the high-confidence buckets (6/7/8, which match both the
# names and AIS), map 0/1 (VTS / navigation markers) to "not a vessel type", and
# put every other real vessel in the honest coarse bucket "other" rather than a
# specific-but-wrong label. ship_type_code stays NULL — a precise ITU code was
# never provided. (A future refinement could use MarineTraffic's GT_SHIPTYPE or a
# second source to recover the granular type.)
_MT_SHIPTYPE_SUMMARY: dict[str, str | None] = {
    "0": None,        # unspecified / VTS station
    "1": None,        # field marker / aid to navigation
    "6": "passenger",
    "7": "cargo",
    "8": "tanker",
}


def _ship_type(raw: Any) -> tuple[int | None, str | None]:
    """MarineTraffic ``shiptype`` → (ship_type_code, ship_type text).

    ship_type_code is always None (the tile feed carries no precise ITU code).
    The text is a high-confidence label for 6/7/8, None for unspecified/aids
    (0/1), and the honest coarse bucket "other" for any other real vessel."""
    text = _str(raw)
    if text is None:
        return None, None
    if text in _MT_SHIPTYPE_SUMMARY:
        return None, _MT_SHIPTYPE_SUMMARY[text]
    try:
        code = int(float(text))
    except ValueError:
        return None, None          # non-numeric junk
    # A known vessel of an uncertain MarineTraffic category (digits 2,3,4,5,9):
    # honest coarse bucket, never a specific-but-wrong type. Out-of-range → None.
    return (None, "other") if 2 <= code <= 9 else (None, None)


def _record_data(rec: dict[str, Any]) -> dict[str, Any]:
    """The vessel fields live under ``data``; tolerate a flat record too."""
    data = rec.get("data")
    return data if isinstance(data, dict) else rec


def parse_mt_record(rec: Any) -> MtParsed | None:
    """One positions.jsonl record → MtParsed, or None if it cannot be keyed or
    carries no usable position. Mirrors parse_position_report's rejection rule:
    a row without a fix is not an archivable observation."""
    if not isinstance(rec, dict):
        return None
    data = _record_data(rec)
    ship_id = _str(data.get("ship_id")) or _str(rec.get("ship_id"))
    if ship_id is None:
        return None

    lat = _num(data.get("latitude"))
    lon = _num(data.get("longitude"))
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
        return None  # no usable fix

    ts = _fix_timestamp(rec.get("fetched_at"), data.get("elapsed_min"))
    code, type_text = _ship_type(data.get("shiptype"))
    length = _num(data.get("length_m"))
    beam = _num(data.get("width_m"))

    position = {
        "lat": lat,
        "lon": lon,
        "speed": _num(data.get("speed_knots")),
        "course": _num(data.get("course")),
        "heading": _num(data.get("heading")),   # mtscraper already maps 511→null
        "nav_status": None,                       # not in the MarineTraffic tile feed
        "timestamp": ts,
    }
    static = {
        "imo": _str(data.get("imo")),             # tile feed rarely carries it
        "name": _str(data.get("shipname")),
        "callsign": _str(data.get("callsign")),
        "ship_type_code": code,
        "ship_type": type_text,
        "destination": _str(data.get("destination")),
        "draught": _num(data.get("draught")),
        "length_m": length,
        "beam_m": beam,
        "flag": _str(data.get("flag")),           # MarineTraffic-reported flag state
    }
    validation = rec.get("validation")
    return MtParsed(
        ship_id=ship_id,
        mmsi=_str(data.get("mmsi")),
        position=position,
        static=static,
        flag=_str(data.get("flag")),
        in_area=bool(rec.get("in_area", True)),
        distance_km=_num(rec.get("distance_km")),
        validation=validation if isinstance(validation, dict) else {},
    )


def to_vessel_dict(parsed: MtParsed, *, source: str = "marinetraffic",
                   data_origin: str = "terrestrial") -> dict[str, Any]:
    """Flatten an MtParsed into a VesselState dict — the SAME shape the live
    Fleet emits per event, so the DB row builder has one input contract whether
    a row arrives live (a Fleet event) or via backfill (a raw JSONL line)."""
    v = VesselState(mmsi=parsed.mmsi, ship_id=parsed.ship_id,
                    source=source, data_origin=data_origin)
    v.merge_static(parsed.static)
    v.merge_position(parsed.position)
    return v.to_dict()
