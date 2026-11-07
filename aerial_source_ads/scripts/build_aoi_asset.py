#!/usr/bin/env python3
"""Build the trimmed area-of-interest GeoJSON asset (ONE-OFF, dev-time only).

This script is intentionally OUTSIDE the runtime package (``adsb_scraper/``) and
is NEVER imported by it. It exists only to regenerate the committed asset so the
result is reproducible; boundaries are never downloaded at runtime.

Source
------
Natural Earth 1:10m Admin-0 Countries (public domain), via the nvkelso mirror::

    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson

Extraction
----------
Keeps ONLY the seven AOI countries, unioning the constituent Natural Earth
features per country (e.g. the whole island of Cyprus = Republic of Cyprus +
Northern Cyprus + the UN buffer zone + the UK Sovereign Base Areas), and writes
a compact ``FeatureCollection`` where each feature carries ``{"name": <country>}``.

Usage
-----
    python scripts/build_aoi_asset.py

Produces ``adsb_scraper/data/aoi_countries.geojson``.
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
OUTPUT = Path(__file__).resolve().parent.parent / "adsb_scraper" / "data" / "aoi_countries.geojson"

# Canonical AOI country -> the set of Natural Earth admin names that compose it.
# Multiple NE features are unioned so islands/enclaves are never dropped.
COUNTRY_ALIASES: dict[str, set[str]] = {
    "Syria": {"Syria", "Syrian Arab Republic"},
    "Turkey": {"Turkey", "Turkiye", "Türkiye", "Republic of Turkey"},
    "Iraq": {"Iraq"},
    "Jordan": {"Jordan", "Hashemite Kingdom of Jordan"},
    "Lebanon": {"Lebanon"},
    "Israel": {"Israel"},
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
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "adsb-aoi-builder/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        source = json.loads(resp.read())

    # Reverse lookup: NE admin name -> canonical AOI country.
    alias_to_country = {
        alias.casefold(): country
        for country, aliases in COUNTRY_ALIASES.items()
        for alias in aliases
    }

    parts: dict[str, list[object]] = {c: [] for c in COUNTRY_ALIASES}
    for feature in source["features"]:
        names = {n.casefold() for n in _feature_names(feature["properties"])}
        matched = {alias_to_country[n] for n in names if n in alias_to_country}
        for country in matched:
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
            {
                "type": "Feature",
                "properties": {"name": country},
                "geometry": mapping(merged),
            }
        )
        print(
            f"  {country:8s} parts={len(geoms):2d} bounds={tuple(round(b, 2) for b in merged.bounds)}"
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    collection = {"type": "FeatureCollection", "features": features}
    OUTPUT.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KiB, {len(features)} countries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
