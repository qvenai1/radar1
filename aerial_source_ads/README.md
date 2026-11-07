# adsb-scraper

A production-grade, long-running collector for aircraft observed on the
[ADS-B Exchange](https://globe.adsbexchange.com/) globe. Designed to run
unattended for weeks: it recovers from browser crashes, adapts to changing
payload formats, filters to a precise multi-country area of interest, never
silently drops data, and records enough diagnostics to debug an incident days
after it happened.

This is a ground-up redesign of an earlier single-file script that — as its own
logs showed — collected **zero** aircraft: the globe delivers its data
Zstandard-compressed with a non-JSON `Content-Type` (and, on the interception
path, as readsb/tar1090 **binCraft** binary), all of which the old parser
discarded. The rewrite decodes it correctly and filters it geographically.

## Area of interest

The collector watches the airspace of seven countries — **Syria, Turkey, Iraq,
Jordan, Lebanon, Israel, Cyprus** — plus a border buffer around them.

Each aircraft position classifies into exactly one of three states:

* `INSIDE`  — within a country polygon; the record's `country` is set.
* `NEAR`    — outside all polygons but within `--area-buffer-km` (default 50) of
  a border; flagged `NEAR_BORDER`, with `near` naming the nearest country. NEAR
  is part of the neighborhood and is persisted by default.
* `OUTSIDE` — beyond the buffer entirely; flagged `OUTSIDE_AREA` (flagged, not
  dropped; persisted only with `--persist-out-of-area`).

The buffer is a **true kilometric buffer of the polygons themselves**, computed
in a local azimuthal-equidistant projection — not a degree offset and not a flat
margin on a bounding box (which, at these latitudes, would sweep in
Athens/Cairo/Tehran/Black-Sea traffic that is nowhere near the seven countries).

Fetch and filter stay separate: the upstream source only accepts a bounding box
(`box=`), so the collector fetches one wide box derived from the **envelope of
the buffered geometry**. That box is a fetch mechanism; the polygons-plus-buffer
are the interest filter. A point can sit inside the box yet classify `OUTSIDE`.

Boundaries come from a **static GeoJSON asset committed to the repo**
(`adsb_scraper/data/aoi_countries.geojson`, trimmed from Natural Earth 10m),
loaded once at startup and **never fetched at runtime**. If it is missing or
malformed the process fails fast with a clear error at startup, never mid-run.
Regenerate it (dev-time only, requires network + `shapely`) with:

```bash
python scripts/build_aoi_asset.py   # see scripts/README.md for the source URL
```

## Why it's built this way

The code follows a small Clean Architecture. Dependencies point inward and cross
module boundaries through `Protocol`s, so every adapter (browser, sink, fetch
strategy, validator, clock) can be replaced with a fake in tests.

| Module | Responsibility |
| --- | --- |
| `constants` | Dependency-free literals |
| `exceptions` | One typed error hierarchy (no bare `except`) |
| `models` | Frozen domain value objects |
| `utils` | `Clock`, `BackoffPolicy`, `MovingAverage`, `MemoryProbe`, safe coercion |
| `config` | Layered config: defaults → file → env → CLI, validated once |
| `logger` | Human or JSON structured logging |
| `geo` | Pure geographic math incl. `haversine_km` (fully unit-tested) |
| `area` | `AreaOfInterest` — three-state polygon+buffer classification (shapely + pyproj AEQD) |
| `diagnostics` | `DiagnosticsRecorder` (never lose a payload) + `MetricsCollector` |
| `parser` | `ResponseDecoder` (json→gzip/zlib/deflate/zstd/brotli→text) + `BinCraftDecoder` + `AdaptiveAircraftParser` |
| `browser` | Playwright hidden behind the `BrowserPort` Protocol |
| `fetcher` | `Fetcher` + `FetchStrategy` strategy pattern (browser, interception, OpenSky, ADSBX) |
| `validation` | `AircraftRecordValidator` (identity, area, quality, plausibility) + `CollectionGapDetector` |
| `persistence` | Buffered, `fsync`-durable, crash-repairing JSONL sink |
| `recovery` | Resume state + a **pure** `RecoveryPolicy` decision core |
| `accumulator` | Dedup by ICAO, apply persistence policy, produce records |
| `orchestrator` | Coordinates one cycle; owns control flow, not work |
| `main` | Composition root — the only place concrete classes are wired |

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[rest,metrics,dev]"   # 'rest' enables OpenSky/ADSBX; 'metrics' adds psutil
playwright install chromium
```

`shapely`, `pyproj` and `zstandard` are core dependencies (installed by
`pip install -e .`); `brotli` is optional and only attempted if present.

## Run

The area of interest is baked into the committed boundary asset, so no
coordinates are needed:

```bash
# Collect the seven-country neighborhood into a fresh directory, verbose, headed
python -m adsb_scraper --output ./data_aoi --no-headless -v

# Headless with structured JSON logs
python -m adsb_scraper --output ./data_aoi --structured-logs
```

`--no-headless` is optional (headless is the default) but tends to be more
reliable against Cloudflare. Widen or narrow the neighborhood with
`--area-buffer-km KM`; keep far `OUTSIDE` traffic with `--persist-out-of-area`;
point at a different boundary asset with `--area-asset PATH`.

Configuration precedence is **defaults < file < environment < CLI**:

```bash
export ADSB_AREA_BUFFER_KM=80
export ADSB_PERSISTENCE_FSYNC=false
python -m adsb_scraper --config config.toml --interval 20
```

```toml
# config.toml
output_dir = "data_aoi"
[area]
buffer_km = 50.0
persist_out_of_area = false
[timing]
fetch_interval = 15.0
```

Optional fallback providers (off unless configured):

```bash
export ADSB_ENABLE_OPENSKY=1          # anonymous OpenSky REST fallback
export ADSB_ADSBX_API_KEY=<rapidapi>  # official ADS-B Exchange API
```

## Output

```
data_aoi/
├── aircraft.jsonl              # one JSON record per unique aircraft (ICAO-keyed),
│                               #   incl. an additive "validation" provenance block:
│                               #   {position_quality, flags, distance_km, country, near, source}
├── .completed_icao_cache.txt   # fast-resume index
├── collection_gaps.jsonl       # append-only markers for collection gaps (e.g. power loss)
├── .collection_state.json      # last-cycle cursor (cross-restart gap detection)
├── scraper.log
└── diagnostics/
    ├── last_response.json / .txt
    ├── last_headers.json
    ├── last_status.txt
    ├── response_size.txt
    ├── last_html.html
    ├── last_exception.log
    └── unknown_payloads/       # anything the parser could not interpret
```

The raw archive is the immutable ground truth. Filtering and validation are
derived decisions layered on top — records are flagged, never mutated in place,
and the `validation` block is added additively so records written by earlier
versions remain readable.

## Recovery behaviour

* 5 consecutive empty cycles → reload the page.
* 10 consecutive empty cycles → restart the browser.
* Repeated parse failures → archive the payload and escalate to a reload.
* Repeated failed restarts → abort with a non-zero exit code.
* Collection gaps (a wall-clock gap between cycles beyond `--gap-threshold-s`,
  e.g. a power outage) are **recorded, never acted on** — they are expected and
  benign, and exist so downstream analysis can tell a real "signal lost" event
  apart from a gap on the collector's side.

## Development

```bash
ruff check adsb_scraper && ruff format --check adsb_scraper
mypy adsb_scraper          # strict
pytest                     # no network/browser required
```
