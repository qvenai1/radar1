# Dev-time scripts (not part of the runtime package)

These helpers are **not** imported by `adsb_scraper` and are **not** required at
runtime. They regenerate committed assets so those assets stay reproducible.

## `build_aoi_asset.py` — area-of-interest boundaries

Regenerates `adsb_scraper/data/aoi_countries.geojson`, the static boundary asset
loaded once at startup by `adsb_scraper.area`. Boundaries are **never** fetched
at runtime.

- **Source:** Natural Earth 1:10m Admin-0 Countries (public domain), nvkelso mirror:
  `https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson`
- **Extraction:** keeps only the seven AOI countries — Syria, Turkey, Iraq,
  Jordan, Lebanon, Israel, Cyprus — unioning the constituent Natural Earth
  features per country. The whole island of Cyprus (Republic of Cyprus +
  Northern Cyprus + UN buffer zone + the UK Sovereign Base Areas) is unioned
  into a single `Cyprus` feature so no island polygon is dropped.

```bash
python scripts/build_aoi_asset.py   # requires shapely + network (dev-time only)
```

The committed result is ~135 KiB; each feature carries `{"name": <country>}`.
