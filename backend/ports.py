"""Port gazetteer + in-process loader (PORT-1 — ASSET + LOADER ONLY).

A committed, dated reference gazetteer of PORTS for the maritime collector bbox.
Same committed-asset discipline as :mod:`backend.airports` / :mod:`backend.zones` /
:mod:`backend.mmsi_flag` / :mod:`backend.geo_area`: the asset
(``backend/data/ports_aoi.json``) is loaded once in-process and **NEVER fetched at
runtime**; a missing/malformed asset fails fast with :class:`PortAssetError`.

PORT-1 is ASSET + LOADER ONLY — there is no sessionization, no ``port_visit``
event, and no composite wiring. This module exposes exactly what the PORT-2
on-query sessionizer needs and nothing more: load, resolve by name/alias/UN-LOCODE,
and the three spatial queries.

TWO SOURCES, ONE HOLE THE SUPPLEMENT EXISTS TO FILL. The spine is the **NGA World
Port Index** (Pub 150, **US-Government public domain** — "NO COPYRIGHT CLAIMED
UNDER TITLE 17 U.S.C."; no attribution obligation, no share-alike, so materially
cleaner than this project's ODbL assets), 180 features in the bbox. But WPI carries
**zero Syrian ports**, **one** Lebanese entry (Sayda, "very small" open roadstead)
and **no Beirut** — and this base's AOI centres on Syria. So a **targeted** OSM
supplement (ODbL, attribution + share-alike carried PER FEATURE) adds the 10
harbours WPI omits, giving Syria 3 and Lebanon 6.

GEOMETRY HONESTY, weaker here than in the zones layer — and stated, not buried:

  * ``radius_basis`` is ``'assumed'`` on **EVERY** feature. No source in this asset
    publishes a measured harbour extent. (zones_aoi.json had 29 of 36 ``'sourced'``,
    on the UNCLOS Art. 60(5) 500 m safety zone.) A port radius here is a **search
    aperture, not evidence**.
  * ``coord_precision`` — WPI positions are **degrees and minutes by publication
    design** (Pub 150 §3), i.e. ±~0.93 km lat / ±0.78 km lon at 33°N. For the 108
    smallest harbours the positional error **exceeds the harbour**, so their radius
    is an uncertainty budget rather than an extent. OSM features carry their own
    (``polygon_centroid`` / ``node``).
  * ``merges_with`` — 15 AOI pairs sit within 6 km (tightest 1.40 km). Any usable
    radius merges distinct terminals, so :func:`ports_containing` returns **every**
    match, never an arbitrary first, and PORT-2 can say "attributable to either X
    or Y" instead of silently picking one.

ONE geo implementation: circular containment/proximity reuse
:func:`backend.geo_area.haversine_km` — the same great-circle function
:mod:`backend.rendezvous` and :mod:`backend.zones` use — never a second geo stack.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .geo_area import haversine_km

DEFAULT_ASSET = Path(__file__).resolve().parent / "data" / "ports_aoi.json"

SOURCES = ("WPI", "OSM")
#: Only 'assumed' is legal: nothing in this asset has a measured extent (unlike
#: zones_aoi.json, whose platform radii are 'sourced'). A closed set of one, so a
#: future 'sourced' feature has to be a deliberate, reviewed change.
RADIUS_BASES = ("assumed",)
COORD_PRECISIONS = ("arcminute", "arcsecond", "polygon_centroid", "node")

#: Default radius for :func:`ports_near` — "what ports are near this fix". Not a
#: detection threshold (that is PORT-2); just a query default.
DEFAULT_NEAR_KM = 25.0

#: Per-feature keys the asset MUST carry (validated at load, fail-fast).
_REQUIRED = ("port_id", "port_name", "country", "lat", "lon", "radius_m",
             "radius_basis", "coord_precision", "source", "license", "source_ref",
             "captured_at", "merges_with")

SEARCH_CAVEAT = (
    "Port geometry is a POINT + ASSUMED radius. No source publishes a measured harbour "
    "extent, so radius_m is a search aperture, not evidence -- and every feature says so "
    "(radius_basis='assumed'). WPI coordinates are degrees-and-minutes by publication design "
    "(+/- ~1 km); for the smallest harbours that error EXCEEDS the harbour. Ports within 6 km "
    "of each other cannot be cleanly separated -- see merges_with, and expect more than one "
    "containing port. A port with no traffic may be outside AIS coverage, not mis-sited: "
    "PORT-pre measured only 43 of 180 ports producing any session over 16 days."
)


class PortAssetError(RuntimeError):
    """The ports gazetteer is missing, unreadable, or malformed (fail fast)."""


def _load(asset_path: Path) -> list[dict[str, Any]]:
    if not asset_path.exists():
        raise PortAssetError(f"ports gazetteer not found: {asset_path} (committed, never fetched)")
    try:
        raw = json.loads(asset_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PortAssetError(f"ports gazetteer unreadable: {asset_path}: {exc}") from exc
    ports = raw.get("ports") if isinstance(raw, dict) else None
    if not isinstance(ports, list) or not ports:
        raise PortAssetError(f"ports gazetteer has no 'ports' list: {asset_path}")
    seen: set[str] = set()
    for p in ports:
        if not isinstance(p, dict) or any(p.get(k) in (None, "") for k in _REQUIRED
                                          if k != "merges_with"):
            raise PortAssetError(f"port row missing required field(s) {_REQUIRED}: {p!r}")
        if p["source"] not in SOURCES:
            raise PortAssetError(f"port {p['port_id']}: bad source {p['source']!r}")
        if p["radius_basis"] not in RADIUS_BASES:
            raise PortAssetError(
                f"port {p['port_id']}: radius_basis must be one of {RADIUS_BASES} "
                "(nothing in this asset has a measured extent)")
        if p["coord_precision"] not in COORD_PRECISIONS:
            raise PortAssetError(
                f"port {p['port_id']}: coord_precision must be one of {COORD_PRECISIONS}")
        if not (isinstance(p["lat"], (int, float)) and isinstance(p["lon"], (int, float))
                and -90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180):
            raise PortAssetError(f"port {p['port_id']}: bad lat/lon {p['lat']},{p['lon']}")
        if not (isinstance(p["radius_m"], (int, float)) and p["radius_m"] > 0):
            raise PortAssetError(f"port {p['port_id']}: radius_m must be > 0")
        if not isinstance(p["merges_with"], list):
            raise PortAssetError(f"port {p['port_id']}: merges_with must be a list")
        if p["port_id"] in seen:
            raise PortAssetError(f"duplicate port_id: {p['port_id']}")
        seen.add(p["port_id"])
    # merges_with must reference real ports and be SYMMETRIC — an asymmetric entry
    # would let PORT-2 report the ambiguity from one side only.
    by_id = {p["port_id"]: p for p in ports}
    for p in ports:
        for other in p["merges_with"]:
            if other not in by_id:
                raise PortAssetError(
                    f"port {p['port_id']}: merges_with references unknown port {other!r}")
            if p["port_id"] not in by_id[other]["merges_with"]:
                raise PortAssetError(
                    f"merges_with is asymmetric: {p['port_id']} -> {other} but not back")
    return ports


@lru_cache(maxsize=1)
def _ports() -> list[dict[str, Any]]:
    """Process-wide load-once singleton (fail-fast on first use)."""
    return _load(DEFAULT_ASSET)


def all_ports() -> list[dict[str, Any]]:
    """Every port (shallow copies, safe to annotate)."""
    return [dict(p) for p in _ports()]


def resolve(q: str, limit: int = 25) -> list[dict[str, Any]]:
    """Ports matching q by ``port_id`` / ``unlocode`` (exact) or ``port_name`` /
    ``alternate_name`` (case-insensitive substring). In-process against the
    committed asset — NO DB (like :func:`backend.airports.resolve` and
    :func:`backend.zones.resolve`). Exact id/UN-LOCODE matches rank first.

    ``alternate_name`` is searched deliberately: WPI files Port Said as
    *"Bur Sa'id"* with the searched-for name only in ``alternateName``, so a
    name-only search misses it entirely."""
    qs = q.strip()
    if not qs:
        return []
    low = qs.lower()
    # UN-LOCODE is written "IL HFA"; accept "ilhfa" / "il hfa" / "IL-HFA" alike
    squashed = "".join(low.split()).replace("-", "")
    scored: list[tuple[int, dict[str, Any]]] = []
    for p in _ports():
        unlo = (p.get("unlocode") or "")
        name = p.get("port_name") or ""
        alt = p.get("alternate_name") or ""
        if p["port_id"].lower() == low or (unlo and "".join(unlo.lower().split()) == squashed):
            scored.append((0, p))
        elif low in name.lower():
            scored.append((1, p))
        elif alt and low in alt.lower():
            scored.append((2, p))
        elif low in p["port_id"].lower():
            scored.append((3, p))
    scored.sort(key=lambda s: (s[0], s[1]["port_name"], s[1]["port_id"]))
    return [dict(p) for _, p in scored[:limit]]


def ports_containing(lat: float, lon: float) -> list[dict[str, Any]]:
    """**Every** port whose own circle (centre, ``radius_m``) contains the point —
    the sessionization primitive ("which port is this fix sitting in"). Each result
    carries ``distance_m``; sorted nearest-first.

    Returns ALL matches, never an arbitrary first: 15 AOI pairs sit within 6 km, so
    overlapping radii are expected and the ambiguity is the caller's to report (see
    ``merges_with``). A caller that wants one port should say so explicitly and own
    the choice — this function will not make it silently.

    True geodesic test via :func:`backend.geo_area.haversine_km`; no projection is
    needed for a point-in-circle test at these scales."""
    out: list[dict[str, Any]] = []
    for p in _ports():
        d_m = haversine_km(lat, lon, p["lat"], p["lon"]) * 1000.0
        if d_m <= p["radius_m"]:
            out.append({**p, "distance_m": round(d_m, 1)})
    out.sort(key=lambda p: p["distance_m"])
    return out


def ports_near(lat: float, lon: float, radius_km: float = DEFAULT_NEAR_KM) -> list[dict[str, Any]]:
    """Ports whose CENTRE is within ``radius_km`` of the point — a proximity search
    ("what ports are near this fix"), independent of each port's own radius. Each
    result carries ``distance_km``; sorted nearest-first."""
    if radius_km < 0:
        raise ValueError("radius_km must be >= 0")
    out: list[dict[str, Any]] = []
    for p in _ports():
        d_km = haversine_km(lat, lon, p["lat"], p["lon"])
        if d_km <= radius_km:
            out.append({**p, "distance_km": round(d_km, 3)})
    out.sort(key=lambda p: p["distance_km"])
    return out


def ports_in_bbox(lamin: float, lomin: float, lamax: float,
                  lomax: float) -> list[dict[str, Any]]:
    """Ports whose CENTRE falls inside the lat/lon box — the cheap bbox prefilter for
    a "draw a box → ports inside it" query (PORT-2 then does the true circular test
    per fix). Pure lon/lat comparison."""
    return [dict(p) for p in _ports()
            if lamin <= p["lat"] <= lamax and lomin <= p["lon"] <= lomax]


def vicinity_box(port: dict[str, Any], pad_km: float = 0.0) -> tuple[float, float, float, float]:
    """``(lamin, lomin, lamax, lomax)`` covering the port's circle plus ``pad_km`` —
    the SQL prefilter box a PORT-2 query hands to the ``vessel_states`` GiST index
    before the exact circular test.

    A GUARANTEED SUPERSET of the circle, derived from the SAME sphere
    :func:`backend.geo_area.haversine_km` measures on (no second set of earth
    constants). The longitude half-width uses the exact geodesic-circle bound
    ``asin(sin(r/R) / cos(lat))`` — the circle's easternmost point lies slightly
    poleward of the centre latitude, so the flat ``r / (R·cos lat)`` approximation
    is a few metres SHORT and would silently drop fixes on the circle's edge."""
    if pad_km < 0:
        raise ValueError("pad_km must be >= 0")
    import math

    from .geo_area import EARTH_RADIUS_KM

    reach_km = port["radius_m"] / 1000.0 + pad_km
    ang = reach_km / EARTH_RADIUS_KM                      # angular radius, radians
    dlat = math.degrees(ang)
    cos_lat = math.cos(math.radians(port["lat"]))
    if cos_lat <= 1e-9 or math.sin(ang) >= cos_lat:       # circle spans a pole
        dlon = 180.0
    else:
        dlon = math.degrees(math.asin(math.sin(ang) / cos_lat))
    return (port["lat"] - dlat, port["lon"] - dlon, port["lat"] + dlat, port["lon"] + dlon)
