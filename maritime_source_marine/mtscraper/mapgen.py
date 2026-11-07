"""Render the archived vessels as a self-contained context map.

Reads an output directory (``positions.jsonl`` + optional
``vessel_identities.jsonl``) and the committed AOI coastline, and writes ONE
self-contained HTML file: inline SVG, no external tiles/fonts/scripts, so it is
CSP-safe and openable offline.

This is a **context layer, not a live chart**: the page carries a prominent
"NOT LIVE — snapshot" banner and a per-vessel last-update time, because the data
is periodic scraped snapshots, never a real-time feed.

Usage:  python -m mtscraper.mapgen <output_dir> [--out map.html]
"""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import constants as C

_PAD_DEG = 0.4
_WIDTH = 1200


@dataclass(frozen=True, slots=True)
class _Pt:
    lon: float
    lat: float
    label: str
    resolved: bool
    moving: bool


def latest_positions(path: Path) -> dict[str, dict[str, Any]]:
    """Latest record per ship_id from the time-series (last line wins)."""
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ship_id = rec.get("ship_id")
            if isinstance(ship_id, str):
                latest[ship_id] = rec
    return latest


def load_identities(path: Path) -> dict[str, dict[str, Any]]:
    ids: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return ids
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ship_id = rec.get("ship_id")
            if isinstance(ship_id, str):
                ids[ship_id] = rec
    return ids


def _rings(geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """Flatten a GeoJSON Polygon/MultiPolygon to a list of (lon,lat) rings."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    rings: list[list[tuple[float, float]]] = []
    if gtype == "Polygon":
        polys = [coords]
    elif gtype == "MultiPolygon":
        polys = coords
    else:
        return rings
    for poly in polys:
        for ring in poly:
            rings.append([(float(x), float(y)) for x, y in ring])
    return rings


def load_coast(geojson_path: Path) -> list[list[tuple[float, float]]]:
    if not geojson_path.exists():
        return []
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    rings: list[list[tuple[float, float]]] = []
    for feature in data.get("features", []):
        rings.extend(_rings(feature.get("geometry", {})))
    return rings


def _bounds(
    points: list[_Pt], rings: list[list[tuple[float, float]]]
) -> tuple[float, float, float, float]:
    lons = [p.lon for p in points] + [x for r in rings for x, _ in r]
    lats = [p.lat for p in points] + [y for r in rings for _, y in r]
    if not lons or not lats:
        return (-10.0, 40.0, 30.0, 50.0)  # harmless default
    return (
        min(lons) - _PAD_DEG,
        max(lons) + _PAD_DEG,
        min(lats) - _PAD_DEG,
        max(lats) + _PAD_DEG,
    )


def render_html(
    positions: dict[str, dict[str, Any]],
    identities: dict[str, dict[str, Any]],
    rings: list[list[tuple[float, float]]],
    *,
    latest_fetch: str | None,
) -> str:
    points: list[_Pt] = []
    for ship_id, rec in positions.items():
        data = rec.get("data", {})
        lat, lon = data.get("latitude"), data.get("longitude")
        if lat is None or lon is None:
            continue
        ident = identities.get(ship_id, {})
        name = ident.get("name") or data.get("shipname") or "—"
        mmsi = ident.get("mmsi")
        speed = data.get("speed_knots")
        label = (
            f"{name}  |  mmsi={mmsi or '—'}  imo={ident.get('imo') or '—'}  "
            f"ship_id={ship_id}  speed={speed if speed is not None else '—'}kn  "
            f"updated={rec.get('fetched_at', '—')}"
        )
        points.append(
            _Pt(
                lon=float(lon),
                lat=float(lat),
                label=label,
                resolved=bool(mmsi),
                moving=bool(speed and speed > 0.5),
            )
        )

    west, east, south, north = _bounds(points, rings)
    span_lon = max(1e-6, east - west)
    span_lat = max(1e-6, north - south)
    height = int(_WIDTH * span_lat / span_lon)

    def px(lon: float, lat: float) -> tuple[float, float]:
        x = (lon - west) / span_lon * _WIDTH
        y = (north - lat) / span_lat * height
        return round(x, 1), round(y, 1)

    coast_paths = []
    for ring in rings:
        if len(ring) < 3:
            continue
        d = "M" + " L".join(f"{px(lon, lat)[0]},{px(lon, lat)[1]}" for lon, lat in ring) + "Z"
        coast_paths.append(f'<path d="{d}" class="coast"/>')

    dots = []
    for p in points:
        x, y = px(p.lon, p.lat)
        cls = "v resolved" if p.resolved else "v"
        if p.moving:
            cls += " moving"
        tip = html.escape(p.label)
        dots.append(
            f'<circle cx="{x}" cy="{y}" r="2.6" class="{cls}"><title>{tip}</title></circle>'
        )

    resolved_n = sum(1 for p in points if p.resolved)
    return _PAGE.format(
        width=_WIDTH,
        height=height,
        coast="".join(coast_paths),
        dots="".join(dots),
        total=len(points),
        resolved=resolved_n,
        fetched=html.escape(latest_fetch or "unknown"),
    )


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MarineTraffic archive — vessel context map (NOT LIVE)</title>
<style>
  html,body {{ margin:0; background:#0b1622; color:#e6edf3;
    font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  header {{ padding:12px 16px; background:#3a1010; border-bottom:2px solid #b23b3b; }}
  header b {{ color:#ffb4b4; }}
  .meta {{ opacity:.8; font-size:12px; margin-top:4px; }}
  .wrap {{ overflow:auto; padding:10px; }}
  svg {{ background:#0d2233; max-width:100%; height:auto; display:block; }}
  .coast {{ fill:#26323b; stroke:#4a5b66; stroke-width:.6; }}
  circle.v {{ fill:#8a97a3; opacity:.85; }}
  circle.v.resolved {{ fill:#3fb6a8; }}
  circle.v.moving {{ stroke:#ffd27f; stroke-width:1.1; }}
  .legend {{ padding:8px 16px; font-size:12px; opacity:.9; }}
  .legend span {{ margin-right:16px; }} .sw {{ display:inline-block; width:10px; height:10px;
    border-radius:50%; vertical-align:middle; margin-right:5px; }}
</style></head><body>
<header>
  <b>⚠ NOT LIVE</b> — periodic scraped snapshots, not a real-time feed.
  <div class="meta">Latest snapshot: <b>{fetched}</b> · {total} vessels · {resolved} with MMSI</div>
</header>
<div class="legend">
  <span><i class="sw" style="background:#3fb6a8"></i>MMSI resolved</span>
  <span><i class="sw" style="background:#8a97a3"></i>position only (ship_id)</span>
  <span><i class="sw" style="background:#0d2233;border:1px solid #ffd27f"></i>moving</span>
  <span>hover a dot for details</span>
</div>
<div class="wrap">
<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img"
     aria-label="Vessel positions over the eastern Mediterranean (not live)">
  <g>{coast}</g>
  <g>{dots}</g>
</svg>
</div>
</body></html>"""


def build_map(output_dir: Path, out_path: Path, aoi_path: Path | None = None) -> Path:
    aoi = aoi_path or (
        Path(__file__).resolve().parent / C.AOI_DATA_DIRNAME / C.AOI_ASSET_FILENAME
    )
    positions = latest_positions(output_dir / C.POSITIONS_FILENAME)
    if not positions:  # fall back to the catalogue if no time-series
        positions = latest_positions(output_dir / C.CATALOGUE_FILENAME)
    identities = load_identities(output_dir / C.IDENTITIES_FILENAME)
    rings = load_coast(aoi)
    latest_fetch = max(
        (r.get("fetched_at", "") for r in positions.values()), default=None
    ) or None
    out_path.write_text(
        render_html(positions, identities, rings, latest_fetch=latest_fetch), encoding="utf-8"
    )
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mtscraper.mapgen", description=__doc__)
    p.add_argument("output_dir", type=Path, help="a mtscraper output directory")
    p.add_argument("--out", type=Path, default=None, help="HTML to write (default <dir>/map.html)")
    p.add_argument("--aoi", type=Path, default=None, help="override AOI geojson path")
    args = p.parse_args(argv)
    out = args.out or (args.output_dir / "map.html")
    written = build_map(args.output_dir, out, args.aoi)
    print(f"wrote {written} ({written.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
