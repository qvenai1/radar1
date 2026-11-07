"""
Unified vessel schema — every adapter normalizes into this shape, exactly
like the aviation side normalizes everything into the StateVector contract.

Required minimum (per project spec): mmsi, imo, name, lat, lon, speed,
course, timestamp (UTC ISO 8601), ship_type, source, data_origin.

Conventions:
  * speed  = SOG in KNOTS (maritime native unit — deliberately NOT metric;
    the aviation layer is metric because ADS-B is, AIS is knots by nature).
  * course = COG in degrees true, 0 = north.
  * timestamp = last position fix, UTC, ISO 8601.
  * data_origin ∈ {"terrestrial", "satellite"} — free AIS is terrestrial
    only (~40–60 NM off shore); satellite rows can only come from paid
    sources or GFW historical data.
  * AIS sentinels are normalized to None at the adapter boundary
    (SOG 102.3 = unavailable, COG 360 = unavailable, heading 511 = unavailable).
  * Nulls mean "not reported" — never zero-filled (project-wide rule).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# AIS ship-type code → coarse category (ITU-R M.1371 table 53).
# First-digit classes; special singles resolved first.
_TYPE_RANGES: list[tuple[range, str]] = [
    (range(20, 30), "wing_in_ground"),
    (range(30, 31), "fishing"),
    (range(31, 33), "towing"),
    (range(33, 34), "dredging"),
    (range(34, 35), "diving_ops"),
    (range(35, 36), "military_ops"),
    (range(36, 37), "sailing"),
    (range(37, 38), "pleasure_craft"),
    (range(40, 50), "high_speed_craft"),
    (range(50, 51), "pilot"),
    (range(51, 52), "search_and_rescue"),
    (range(52, 53), "tug"),
    (range(53, 54), "port_tender"),
    (range(54, 55), "anti_pollution"),
    (range(55, 56), "law_enforcement"),
    (range(58, 59), "medical_transport"),
    (range(60, 70), "passenger"),
    (range(70, 80), "cargo"),
    (range(80, 90), "tanker"),
    (range(90, 100), "other"),
]


def ship_type_text(code: int | None) -> str | None:
    if code is None:
        return None
    for rng, label in _TYPE_RANGES:
        if code in rng:
            return label
    return "unknown"


@dataclass(slots=True)
class VesselState:
    """One vessel's current merged state (position + static identity).

    Identity is source-shaped: AIS sources key on ``mmsi``; MarineTraffic keys
    on its internal ``ship_id`` and usually has no ``mmsi`` at all. Both fields
    are therefore optional, and ``identity_key`` returns whichever this vessel
    actually has — the one stable handle the Fleet accumulates under.
    """

    mmsi: str | None = None
    ship_id: str | None = None          # MarineTraffic internal id (mmsi-less sources)
    # --- position report ---
    lat: float | None = None
    lon: float | None = None
    speed: float | None = None          # SOG, knots
    course: float | None = None         # COG, degrees true
    heading: float | None = None        # degrees true; AIS 511 → None
    nav_status: int | None = None       # ITU navigational status code
    timestamp: str | None = None        # last position fix, UTC ISO 8601
    # --- static data ---
    imo: str | None = None
    name: str | None = None
    callsign: str | None = None
    ship_type_code: int | None = None
    ship_type: str | None = None
    destination: str | None = None
    draught: float | None = None        # meters
    length_m: float | None = None       # A+B from AIS Dimension (identity, slow-changing)
    beam_m: float | None = None         # C+D from AIS Dimension
    flag: str | None = None             # source-REPORTED flag (MarineTraffic). AIS leaves
                                        # this None and derives flag via MID (vessel_identity).
    # --- provenance ---
    source: str = "aisstream"
    data_origin: str = "terrestrial"    # free AIS is terrestrial-only

    @property
    def identity_key(self) -> str | None:
        """The stable handle this vessel is accumulated under: its MMSI when it
        has one, otherwise its source-native ship_id. None only for a vessel
        with neither (never happens — the Fleet always keys on a real value)."""
        return self.mmsi or self.ship_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def merge_static(self, static: dict[str, Any]) -> None:
        """Fold a ShipStaticData-derived dict into this state (position kept)."""
        for key in ("imo", "name", "callsign", "ship_type_code", "ship_type",
                    "destination", "draught", "length_m", "beam_m", "flag"):
            val = static.get(key)
            if val is not None:
                setattr(self, key, val)

    def merge_position(self, pos: dict[str, Any]) -> None:
        """Fold a PositionReport-derived dict into this state (identity kept)."""
        for key in ("lat", "lon", "speed", "course", "heading",
                    "nav_status", "timestamp"):
            if key in pos:
                setattr(self, key, pos[key])
