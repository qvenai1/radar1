"""
Pure AISStream.io message parsing — no I/O, no sockets, fully unit-testable.
Kept separate from the adapter (which owns the WebSocket) exactly as the
aviation side keeps StateVector.from_array separate from the HTTP client.

AISStream envelope (as of v0):
    {
      "MessageType": "PositionReport" | "ShipStaticData",
      "MetaData": {"MMSI": 123456789, "ShipName": "...", "time_utc": "..."},
      "Message": { "<MessageType>": { ...fields... } }
    }

AIS "not available" sentinels are normalized to None here so no downstream
code has to know them:
    SOG 1023 (0.1-kt units → 102.3 kt) = unavailable
    COG 3600 (0.1-deg units → 360.0)   = unavailable
    heading 511                         = unavailable
    lat 91 / lon 181                    = unavailable
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..schema import ship_type_text


def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _clean_str(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    # AIS pads names with '@' (null) and spaces
    s = v.replace("@", " ").strip()
    return s or None


def _meta_timestamp(meta: dict[str, Any]) -> str:
    """MetaData.time_utc → UTC ISO 8601; fall back to receive time."""
    raw = meta.get("time_utc")
    if isinstance(raw, str) and raw:
        # AISStream sends e.g. "2024-01-01 12:00:00.000000000 +0000 UTC"
        cleaned = raw.replace(" +0000 UTC", "+00:00").replace(" UTC", "")
        cleaned = cleaned.split(" +")[0] if " +" in cleaned else cleaned
        try:
            # trim fractional seconds to microseconds for fromisoformat
            if "." in cleaned:
                head, frac = cleaned.split(".", 1)
                frac = "".join(ch for ch in frac if ch.isdigit())[:6]
                cleaned = f"{head}.{frac}"
            dt = datetime.fromisoformat(cleaned.strip())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def _mmsi(meta: dict[str, Any], msg: dict[str, Any]) -> str | None:
    m = meta.get("MMSI") or msg.get("UserID")
    if isinstance(m, (int, str)) and str(m).strip():
        return str(m).strip()
    return None


def parse_position_report(envelope: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """→ (mmsi, position-field dict for Fleet.apply_position) or None."""
    meta = envelope.get("MetaData") or {}
    body = (envelope.get("Message") or {}).get("PositionReport") or {}
    mmsi = _mmsi(meta, body)
    if mmsi is None:
        return None

    lat = _num(body.get("Latitude"))
    lon = _num(body.get("Longitude"))
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
        return None  # no usable fix

    sog = _num(body.get("Sog"))
    cog = _num(body.get("Cog"))
    hdg = _num(body.get("TrueHeading"))
    return mmsi, {
        "lat": lat,
        "lon": lon,
        "speed": None if sog is None or sog >= 102.3 else sog,
        "course": None if cog is None or cog >= 360.0 else cog,
        "heading": None if hdg is None or hdg == 511 else hdg,
        "nav_status": body.get("NavigationalStatus")
        if isinstance(body.get("NavigationalStatus"), int) else None,
        "timestamp": _meta_timestamp(meta),
    }


def parse_static_data(envelope: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """→ (mmsi, static-field dict for Fleet.apply_static) or None."""
    meta = envelope.get("MetaData") or {}
    body = (envelope.get("Message") or {}).get("ShipStaticData") or {}
    mmsi = _mmsi(meta, body)
    if mmsi is None:
        return None

    imo_raw = body.get("ImoNumber")
    imo = str(imo_raw) if isinstance(imo_raw, int) and imo_raw > 0 else None
    type_code = body.get("Type") if isinstance(body.get("Type"), int) else None
    draught = _num(body.get("MaximumStaticDraught"))
    # Dimensions (M2b): AIS reference-point offsets to bow(A)/stern(B)/port(C)/
    # starboard(D), integer metres. length = A+B, beam = C+D. All-zero means
    # "not set" → None (project rule: nulls, never zero-filled). These were
    # already in the message and merely dropped before M2b.
    dim = body.get("Dimension") or {}
    a, b = _num(dim.get("A")), _num(dim.get("B"))
    c, d = _num(dim.get("C")), _num(dim.get("D"))
    length = a + b if (a is not None and b is not None) else None
    beam = c + d if (c is not None and d is not None) else None
    return mmsi, {
        "imo": imo,
        "name": _clean_str(body.get("Name")) or _clean_str(meta.get("ShipName")),
        "callsign": _clean_str(body.get("CallSign")),
        "ship_type_code": type_code,
        "ship_type": ship_type_text(type_code),
        "destination": _clean_str(body.get("Destination")),
        "draught": draught if draught else None,
        "length_m": length if length else None,
        "beam_m": beam if beam else None,
    }


def parse_envelope(envelope: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """Dispatch on MessageType. → (kind, mmsi, fields) where kind is
    "position" | "static", or None if unhandled/unparseable."""
    mtype = envelope.get("MessageType")
    if mtype == "PositionReport":
        r = parse_position_report(envelope)
        return ("position", r[0], r[1]) if r else None
    if mtype == "ShipStaticData":
        r = parse_static_data(envelope)
        return ("static", r[0], r[1]) if r else None
    return None
