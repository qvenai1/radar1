#!/usr/bin/env python3
"""Build the trimmed maritime area-of-interest GeoJSON asset (dev-time only).

Intentionally OUTSIDE the runtime package (``mtscraper/``) and NEVER imported by
it — it exists only to regenerate the committed asset reproducibly. Boundaries
are never downloaded at runtime.

Source: Natural Earth 1:10m Admin-0 Countries (public domain), nvkelso mirror.

For a *maritime* AOI we keep the eastern-Mediterranean coastal countries whose
shores bound the default map view (centre 33.2N/35.2E). Ships sit on water, so
"inside a land polygon" is rare; the useful bucket is NEAR — within a kilometric
buffer of one of these coasts (see mtscraper/area.py).

Usage:  python scripts/build_aoi_asset.py
Produces mtscraper/data/aoi_countries.geojson
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

SOURCE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_10m_admin_0_countries.geojson"
)
OUTPUT = Path(__file__).resolve().parent.parent / "mtscraper" / "data" / "aoi_countries.geojson"

# Canonical maritime AOI country -> Natural Earth admin names composing it.
COUNTRY_ALIASES: dict[str, set[str]] = {
    "Turkey": {"Turkey", "Turkiye", "Türkiye", "Republic of Turkey"},
    "Syria": {"Syria", "Syrian Arab Republic"},
    "Lebanon": {"Lebanon"},
    "Israel": {"Israel"},
    "Egypt": {"Egypt", "Arab Republic of Egypt"},
    "Cyprus": {
        "Cyprus",
        "Northern Cyprus",
        "N. Cyprus",
        "Cyprus U.N. Buffer Zone",
        "Cyprus No Mans Area",
        "Dhekelia Sovereign Base Area",
        "Akrotiri Sovereign Base Area",
    },
}

_NAME_PROPS = ("ADMIN", "NAME", "NAME_EN", "NAME_LONG", "SOVEREIGNT", "GEOUNIT")


def _feature_names(props: dict[str, object]) -> set[str]:
    return {str(props[k]) for k in _NAME_PROPS if props.get(k)}


def main() -> int:
    print(f"downloading {SOURCE_URL}")
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "mt-aoi-builder/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        source = json.loads(resp.read())

    alias_to_country = {
        alias.casefold(): country
        for country, aliases in COUNTRY_ALIASES.items()
        for alias in aliases
    }

    parts: dict[str, list[object]] = {c: [] for c in COUNTRY_ALIASES}
    for feature in source["features"]:
        names = {n.casefold() for n in _feature_names(feature["properties"])}
        for country in {alias_to_country[n] for n in names if n in alias_to_country}:
            parts[country].append(shape(feature["geometry"]))

    features = []
    for country, geoms in parts.items():
        if not geoms:
            print(f"ERROR: no source feature matched {country!r}", file=sys.stderr)
            return 1
        merged = unary_union(geoms)
        if not merged.is_valid:
            merged = merged.buffer(0)
        features.append(
            {"type": "Feature", "properties": {"name": country}, "geometry": mapping(merged)}
        )
        print(f"  {country:8s} parts={len(geoms):2d} bounds={tuple(round(b, 2) for b in merged.bounds)}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    collection = {"type": "FeatureCollection", "features": features}
    OUTPUT.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KiB, {len(features)} countries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
