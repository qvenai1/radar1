"""Airport gazetteer + landing-candidate derivation (E4 / dashboard D3).

The airport is the one entity with NO stored "landed at" fact — it must be
DERIVED from state_vectors. Two committed, dated assets discipline (mirrors
backend.mmsi_flag / backend.geo_area): the gazetteer
(backend/data/airports_aoi.json, OurAirports Public Domain, filtered to the
collector bbox) is loaded once in-process and NEVER fetched at runtime.

Landing detection is ON-QUERY (no stored layer, no timer — E4-pre's
recommendation): resolve the airport from the asset, then run one constrained
query over its vicinity. Two signals, both CANDIDATES (never proven landings —
the project's lead-not-proof discipline):

  * PRIMARY  — an on_ground=true fix inside the vicinity box (a reported surface
    contact; abundant where terrestrial ADS-B reaches the ground);
  * FALLBACK — an ELEVATION-RELATIVE near-ground approach: the track descends and
    terminates low RELATIVE TO FIELD ELEVATION (E4-pre proved an ABSOLUTE ceiling
    finds nothing at Queen Alia, field 730 m MSL), then drops below the feed floor.
    Descent is inferred from the baro_altitude SEQUENCE (vertical_rate is 0 %
    populated — unusable).

Quality is COVERAGE-BOUNDED: a well-covered field yields dense candidates, a
poorly-covered field few or none — a sparse result is correct, not a bug.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_ASSET = Path(__file__).resolve().parent / "data" / "airports_aoi.json"

FT_TO_M = 0.3048

# vicinity + elevation-relative altitude bands (E4-pre Q2/Q3)
VICINITY_DEG = 0.05          # ~5.5 km half-width box around the field
APPROACH_CEIL_M = 600.0      # query altitude bound, RELATIVE to field elevation
TERMINAL_M = 450.0           # an approach must TERMINATE within this of the field
MIN_NET_DESCENT_M = 50.0     # net drop required (excludes level overflights/climb-outs)

DEFAULT_WINDOW_HOURS = 24    # on-query window; air is live, so this is "the recent day"
MAX_WINDOW_HOURS = 168
MAX_CANDIDATES = 300

SEARCH_CAVEAT = (
    "Landing CANDIDATES, not proven landings (the project's lead-not-proof "
    "discipline): a 'grounded' candidate is an aircraft reported on the surface in "
    "the airport vicinity (could be an arrival, a departure, or parked); an "
    "'approach' candidate descended to near field-elevation and its track ended in "
    "the vicinity (inferred from the altitude sequence — vertical_rate is not "
    "broadcast). Quality is COVERAGE-BOUNDED: a well-covered field is dense, a "
    "poorly-covered one is sparse or empty — a sparse result is correct, not a bug."
)


class AirportAssetError(RuntimeError):
    """The gazetteer asset is missing, unreadable, or malformed (fail fast)."""


def _load(asset_path: Path) -> list[dict[str, Any]]:
    if not asset_path.exists():
        raise AirportAssetError(f"airport gazetteer not found: {asset_path} (committed, never fetched)")
    try:
        raw = json.loads(asset_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AirportAssetError(f"airport gazetteer unreadable: {asset_path}: {exc}") from exc
    airports = raw.get("airports") if isinstance(raw, dict) else None
    if not isinstance(airports, list) or not airports:
        raise AirportAssetError(f"airport gazetteer has no 'airports' list: {asset_path}")
    for a in airports:
        if not (isinstance(a.get("icao"), str) and isinstance(a.get("lat"), (int, float))
                and isinstance(a.get("lon"), (int, float))):
            raise AirportAssetError(f"malformed airport row: {a!r}")
    return airports


@lru_cache(maxsize=1)
def _airports() -> list[dict[str, Any]]:
    """Process-wide load-once singleton (fail-fast on first use)."""
    return _load(DEFAULT_ASSET)


def resolve(q: str, limit: int = 25) -> list[dict[str, Any]]:
    """Airports matching q by ICAO (exact) / IATA (exact) / name or municipality
    (case-insensitive substring). In-process against the committed asset — NO DB
    (unlike vessel/aircraft/satellite identity). Exact code matches rank first."""
    qs = q.strip()
    if not qs:
        return []
    up = qs.upper()
    low = qs.lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for a in _airports():
        icao = a["icao"].upper()
        iata = (a.get("iata") or "").upper()
        name = (a.get("name") or "")
        muni = (a.get("municipality") or "")
        if icao == up or iata == up:
            scored.append((0, a))
        elif low in name.lower() or (muni and low in muni.lower()):
            scored.append((1, a))
    scored.sort(key=lambda s: (s[0], s[1]["icao"]))
    return [a for _, a in scored[:limit]]


def elevation_m(airport: dict[str, Any]) -> float:
    ft = airport.get("elevation_ft")
    return float(ft) * FT_TO_M if isinstance(ft, (int, float)) else 0.0


def landing_candidate(fixes: list[dict[str, Any]], elev_m: float) -> dict[str, Any] | None:
    """Classify ONE aircraft's vicinity fixes (ts-ascending) into a landing
    candidate, or None. Pure — no I/O. `fixes` each carry ts (ISO str), on_ground,
    baro_altitude, callsign, reg, source.

      * any on_ground fix  -> 'grounded' (surface contact in the vicinity);
      * else a descending track terminating within TERMINAL_M of the field, with
        at least MIN_NET_DESCENT_M net drop -> 'approach' (elevation-relative);
      * else None (a level overflight / climb-out / high transit — excluded).
    """
    if not fixes:
        return None
    last = fixes[-1]
    callsign = next((f["callsign"].strip() for f in reversed(fixes)
                     if (f.get("callsign") or "").strip()), None)
    reg = next((f.get("reg") for f in reversed(fixes) if f.get("reg")), None)
    base = {
        "icao24": last["icao24"], "callsign": callsign, "registration": reg,
        "source": last["source"], "n_fixes": len(fixes),
    }

    grounded = [f for f in fixes if f["on_ground"]]
    if grounded:
        return {**base, "signal": "grounded", "landing_ts": grounded[0]["ts"].isoformat(),
                "min_altitude_m": None}

    alts = [(f["baro_altitude"], f["ts"]) for f in fixes if f["baro_altitude"] is not None]
    if len(alts) < 2:
        return None
    net_descent = alts[0][0] - alts[-1][0]         # +ve = ended lower than it started
    terminal_alt, terminal_ts = alts[-1]
    min_alt = min(a for a, _ in alts)
    if net_descent >= MIN_NET_DESCENT_M and terminal_alt <= elev_m + TERMINAL_M:
        return {**base, "signal": "approach", "landing_ts": terminal_ts.isoformat(),
                "min_altitude_m": round(min_alt)}
    return None
