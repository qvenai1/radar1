"""Offshore-infrastructure zone gazetteer (ZONES-1 — asset + loader only).

A committed, dated reference gazetteer of offshore hydrocarbon ZONES (gas fields
+ platforms) for the East Mediterranean theater. Two committed-asset discipline
(mirrors backend.airports / backend.mmsi_flag / backend.geo_area): the asset
(backend/data/zones_aoi.json) is loaded once in-process and NEVER fetched at
runtime; a missing/malformed asset fails fast with :class:`ZoneAssetError`.

ZONES-1 is ASSET + LOADER ONLY — there is no detector, no ``zone_dwell`` event,
and no composite wiring here (those are ZONES-2/3). This module exposes exactly
what a future on-query dwell detector needs and nothing more: load all zones,
resolve by id/name, and spatial queries (zones near a point / zones a point
falls inside / zones in a bbox).

GEOMETRY HONESTY (carried from the asset, surfaced by the loader). A zone is a
POINT + ``radius_m`` circle, and the two zone types differ in evidential weight:

  * ``platform``  — a SURVEYED point structure (OSM). ``radius_basis='sourced'``
    (500 m, the UNCLOS Art. 60(5) installation safety zone). A dwell here is over
    a real structure.
  * ``gas_field`` — a reservoir CENTROID (GEM GOGET), not a field polygon, with
    ~20-35 km cross-source centroid ambiguity. ``radius_basis='assumed'`` (a
    conservative 5 km near-field default). A dwell here is WEAKER evidence — the
    centre is approximate and a field can span tens of km.

``radius_basis`` rides on every zone so the ZONES-2 dwell output can say which.

ONE geo implementation: circular containment/proximity reuse
:func:`backend.geo_area.haversine_km` — the same great-circle function
:mod:`backend.rendezvous` uses for pairwise vessel distance — never a second geo
stack. (Polygon/line zones, if ever added, extend via geo_area's AEQD+shapely
machinery; ZONES-1 has only Point+radius, for which haversine is exact and cheap.)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .geo_area import haversine_km

DEFAULT_ASSET = Path(__file__).resolve().parent / "data" / "zones_aoi.json"

ZONE_TYPES = ("gas_field", "platform")
RADIUS_BASES = ("sourced", "assumed")

#: Default search radius for :func:`zones_near` — "what infrastructure is near
#: this fix". Not a detection threshold (that is ZONES-2); just a query default.
DEFAULT_NEAR_KM = 25.0

#: Per-feature keys the asset MUST carry (validated at load, fail-fast).
_REQUIRED = ("zone_id", "zone_type", "lat", "lon", "radius_m", "radius_basis",
             "source", "license", "source_ref", "captured_at")

SEARCH_CAVEAT = (
    "Zone geometry is a POINT + radius approximation from published sources with "
    "their own vintage. A gas_field is a reservoir CENTROID (radius_basis='assumed', "
    "centre approximate to ~20-35 km, field spans tens of km) -- dwell over it is "
    "WEAKER evidence than dwell over a 'sourced' surveyed platform point. A zone with "
    "no vessel traffic may be undeveloped or in a coverage hole, not mis-sited."
)


class ZoneAssetError(RuntimeError):
    """The zones gazetteer is missing, unreadable, or malformed (fail fast)."""


def _load(asset_path: Path) -> list[dict[str, Any]]:
    if not asset_path.exists():
        raise ZoneAssetError(f"zones gazetteer not found: {asset_path} (committed, never fetched)")
    try:
        raw = json.loads(asset_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ZoneAssetError(f"zones gazetteer unreadable: {asset_path}: {exc}") from exc
    zones = raw.get("zones") if isinstance(raw, dict) else None
    if not isinstance(zones, list) or not zones:
        raise ZoneAssetError(f"zones gazetteer has no 'zones' list: {asset_path}")
    seen: set[str] = set()
    for z in zones:
        if not isinstance(z, dict) or any(z.get(k) in (None, "") for k in _REQUIRED):
            raise ZoneAssetError(f"zone row missing required field(s) {_REQUIRED}: {z!r}")
        if z["zone_type"] not in ZONE_TYPES:
            raise ZoneAssetError(f"zone {z['zone_id']}: bad zone_type {z['zone_type']!r}")
        if z["radius_basis"] not in RADIUS_BASES:
            raise ZoneAssetError(f"zone {z['zone_id']}: radius_basis must be sourced|assumed")
        if "detectable" in z:
            if not isinstance(z["detectable"], bool):
                raise ZoneAssetError(f"zone {z['zone_id']}: detectable must be bool")
            if z["detectable"] is False and not z.get("detectable_reason"):
                raise ZoneAssetError(f"zone {z['zone_id']}: detectable=false needs detectable_reason")
        if not (isinstance(z["lat"], (int, float)) and isinstance(z["lon"], (int, float))
                and -90 <= z["lat"] <= 90 and -180 <= z["lon"] <= 180):
            raise ZoneAssetError(f"zone {z['zone_id']}: bad lat/lon {z['lat']},{z['lon']}")
        if not (isinstance(z["radius_m"], (int, float)) and z["radius_m"] > 0):
            raise ZoneAssetError(f"zone {z['zone_id']}: radius_m must be > 0")
        if z["zone_id"] in seen:
            raise ZoneAssetError(f"duplicate zone_id: {z['zone_id']}")
        seen.add(z["zone_id"])
    return zones


@lru_cache(maxsize=1)
def _zones() -> list[dict[str, Any]]:
    """Process-wide load-once singleton (fail-fast on first use)."""
    return _load(DEFAULT_ASSET)


def is_detectable(z: dict[str, Any]) -> bool:
    """A zone is detectable unless the asset explicitly marks ``detectable: false``
    (a mis-sited / position-flagged feature kept as a faithful record but excluded
    from dwell — a zone over open water with no structure beneath it would only
    manufacture fabricated evidence). Absent → True."""
    return z.get("detectable", True) is not False


def all_zones() -> list[dict[str, Any]]:
    """Every zone, INCLUDING undetectable ones (shallow copies, safe to annotate).
    The faithful full record; the ZONES-2 detector iterates :func:`detectable_zones`."""
    return [dict(z) for z in _zones()]


def detectable_zones() -> list[dict[str, Any]]:
    """Only the zones the dwell detector should act on (``detectable`` not false)."""
    return [dict(z) for z in _zones() if is_detectable(z)]


def resolve(q: str, limit: int = 25) -> list[dict[str, Any]]:
    """Zones matching q by zone_id (exact) or zone_name (case-insensitive
    substring). In-process against the committed asset — NO DB (like
    :func:`backend.airports.resolve`). Exact id matches rank first."""
    qs = q.strip()
    if not qs:
        return []
    low = qs.lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for z in _zones():
        name = (z.get("zone_name") or "")
        if z["zone_id"].lower() == low:
            scored.append((0, z))
        elif low in z["zone_id"].lower() or (name and low in name.lower()):
            scored.append((1, z))
    scored.sort(key=lambda s: (s[0], s[1]["zone_id"]))
    return [dict(z) for _, z in scored[:limit]]


def zones_containing(lat: float, lon: float,
                     include_undetectable: bool = False) -> list[dict[str, Any]]:
    """Zones whose OWN circle (centre, radius_m) contains the point — the dwell
    primitive ("which infrastructure is this fix sitting on"). Each result carries
    ``distance_m`` (great-circle metres to the zone centre). Sorted nearest-first.
    Undetectable zones are excluded unless ``include_undetectable``.

    True geodesic test via :func:`backend.geo_area.haversine_km`; no projection is
    needed for a point-in-circle test at these scales."""
    out: list[dict[str, Any]] = []
    for z in _zones():
        if not (include_undetectable or is_detectable(z)):
            continue
        d_m = haversine_km(lat, lon, z["lat"], z["lon"]) * 1000.0
        if d_m <= z["radius_m"]:
            out.append({**z, "distance_m": round(d_m, 1)})
    out.sort(key=lambda z: z["distance_m"])
    return out


def zones_near(lat: float, lon: float, radius_km: float = DEFAULT_NEAR_KM,
               include_undetectable: bool = False) -> list[dict[str, Any]]:
    """Zones whose centre is within ``radius_km`` of the point — a proximity search
    ("what infrastructure is near this fix"), independent of each zone's own radius.
    Each result carries ``distance_km``; sorted nearest-first. Undetectable zones are
    excluded unless ``include_undetectable``."""
    if radius_km < 0:
        raise ValueError("radius_km must be >= 0")
    out: list[dict[str, Any]] = []
    for z in _zones():
        if not (include_undetectable or is_detectable(z)):
            continue
        d_km = haversine_km(lat, lon, z["lat"], z["lon"])
        if d_km <= radius_km:
            out.append({**z, "distance_km": round(d_km, 3)})
    out.sort(key=lambda z: z["distance_km"])
    return out


def zones_in_bbox(lamin: float, lomin: float, lamax: float, lomax: float,
                  include_undetectable: bool = False) -> list[dict[str, Any]]:
    """Zones whose CENTRE falls inside the lat/lon box — the cheap bbox prefilter
    for a "draw a box → zones inside it" query (the ZONES-2 detector then does the
    true circular test per fix). Undetectable zones excluded unless
    ``include_undetectable``. Pure lon/lat comparison."""
    return [dict(z) for z in _zones()
            if (include_undetectable or is_detectable(z))
            and lamin <= z["lat"] <= lamax and lomin <= z["lon"] <= lomax]
