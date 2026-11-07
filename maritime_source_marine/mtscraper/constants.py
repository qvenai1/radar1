"""Immutable, dependency-free constants for the MarineTraffic scraper.

Only literals live here. Anything needing logic, validation, or environment
awareness belongs in :mod:`mtscraper.config`. Keeping the two apart means a
constant can never accidentally depend on runtime state, and this module can be
imported from anywhere without side effects.

This project is a standalone sibling of the ADS-B ``plandata`` scraper and shares
*no* code with it — the two never import from each other.
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------- #
# Geography
# --------------------------------------------------------------------------- #
EARTH_RADIUS_KM: Final = 6371.0088
"""Mean Earth radius (IUGG), in kilometres."""

MIN_LAT: Final = -90.0
MAX_LAT: Final = 90.0
MIN_LON: Final = -180.0
MAX_LON: Final = 180.0

# --------------------------------------------------------------------------- #
# Data source (MarineTraffic live-map front-end)
# --------------------------------------------------------------------------- #
DEFAULT_BASE_URL: Final = "https://www.marinetraffic.com"

#: The live-map SPA. ``centerx`` is LONGITUDE and ``centery`` is LATITUDE in
#: MarineTraffic's URL scheme (x before y, lon before lat).
DEFAULT_MAP_PATH: Final = "/en/ais/home/centerx:{lon}/centery:{lat}/zoom:{zoom}"

#: The tile data endpoint the map calls for vessel positions. Verified live:
#: returns ``{"type":1,"data":{"rows":[...],"areaShips":N}}``. ``station:0``
#: means "all stations" (not filtered to one receiver).
TILE_DATA_PATH: Final = "/getData/get_data_json_4/z:{z}/X:{x}/Y:{y}/station:0"

#: Per-vessel identity endpoint (enrichment). Verified live: returns
#: ``{"shipId":…,"name":…,"imo":…,"mmsi":…,"callsign":…,"country":…,…}``. The
#: tile feed carries only SHIP_ID, so this is the ONLY way to resolve the
#: standard maritime identity (MMSI/IMO). Fetched from inside the warmed session.
VESSEL_DETAIL_PATH: Final = "/en/vessels/{ship_id}/general"

#: The detail endpoint returns 400/404 HTML unless this header marks the request
#: as an in-app XHR. Verified live: without it -> 400; with it -> 200 + JSON.
AJAX_HEADERS: Final[dict[str, str]] = {"X-Requested-With": "XMLHttpRequest"}

#: MarineTraffic's tile zoom is offset by one from standard slippy/OSM tiles:
#: their ``z:N`` addresses the same grid as slippy zoom ``N - 1``. Verified
#: against a live capture (their z:7 center tile == slippy z6 X38/Y25).
TILE_ZOOM_OFFSET: Final = 1

#: Substrings that mark a network response as carrying vessel-tile data. Used by
#: the interception strategy to decide which responses are interesting. Kept
#: narrow to ``get_data_json`` so sibling ``/getData/`` calls (e.g. the bare
#: ``get_total_ships`` count) are not mistaken for tiles.
VESSEL_URL_HINTS: Final = ("get_data_json",)

#: CSS selectors that indicate the live map has mounted (any one is enough).
MAP_READY_SELECTORS: Final = ("#map_canvas", ".leaflet-container", "canvas", "#mainMap")

#: Cloudflare *interstitial* title markers (the site is Cloudflare-fronted).
#: Only the blocking-page <title> is matched — NOT the background
#: ``challenge-platform`` telemetry script, which loads on every Cloudflare page
#: even when nothing is blocked and would otherwise cause false positives.
#: Headless Chromium reliably trips a real block; headful does not — see README.
CLOUDFLARE_TITLE_MARKERS: Final = (
    "just a moment",
    "checking your browser",
    "attention required",
)
#: A body token present only on the genuine challenge/interstitial page.
CLOUDFLARE_BODY_TOKEN: Final = "_cf_chl_opt"

# --------------------------------------------------------------------------- #
# Vessel-tile row schema (all values arrive as JSON strings)
# --------------------------------------------------------------------------- #
#: Canonical field -> the source key(s) seen in ``get_data_json_4`` rows. The
#: parser reads by these keys but tolerates any missing (feeds are sparse).
VESSEL_FIELD_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "ship_id": ("SHIP_ID",),
    "mmsi": ("MMSI",),
    "latitude": ("LAT",),
    "longitude": ("LON",),
    "speed": ("SPEED",),
    "course": ("COURSE",),
    "heading": ("HEADING",),
    "elapsed": ("ELAPSED",),
    "destination": ("DESTINATION",),
    "flag": ("FLAG",),
    "length": ("LENGTH",),
    "width": ("WIDTH",),
    "rot": ("ROT",),
    "shipname": ("SHIPNAME",),
    "shiptype": ("SHIPTYPE",),
    "dwt": ("DWT",),
    "gt_shiptype": ("GT_SHIPTYPE",),
}

#: MarineTraffic reports SPEED in tenths of a knot (e.g. "123" -> 12.3 kn) and
#: COURSE/HEADING in whole degrees. HEADING == 511 means "not available".
SPEED_SCALE: Final = 0.1
HEADING_UNAVAILABLE: Final = 511

# --------------------------------------------------------------------------- #
# Output layout
# --------------------------------------------------------------------------- #
#: SHIP_ID-deduplicated discovery catalogue (one line per distinct vessel).
CATALOGUE_FILENAME: Final = "vessels.jsonl"
#: Position time-series (one line per vessel per *changed* report, plus a
#: periodic heartbeat — see POSITION_HEARTBEAT_S).
POSITIONS_FILENAME: Final = "positions.jsonl"
#: How often an unchanged vessel is re-written to the time-series anyway, so the
#: archive keeps positive evidence of continued presence. Measured upstream
#: refresh for a moving vessel is ~9 min, so an hour is well clear of ordinary
#: reporting cadence: a heartbeat line means "still here", not "just moved".
POSITION_HEARTBEAT_S: Final = 3600.0
#: Resolved identity catalogue (ship_id -> MMSI/IMO/callsign/…); a join table
#: for the positions, one line per distinct resolved vessel.
IDENTITIES_FILENAME: Final = "vessel_identities.jsonl"
#: Fast resume cache: the set of SHIP_IDs already catalogued (one per line).
COMPLETED_CACHE_FILENAME: Final = ".completed_ship_cache.txt"
#: Append-only, fsync-durable journal of collection gaps (power loss, downtime).
COLLECTION_GAPS_FILENAME: Final = "collection_gaps.jsonl"
#: Mutable operational cursor: wall-clock time of the last cycle, to detect a
#: gap that spanned a process restart. NOT part of the archive.
COLLECTION_STATE_FILENAME: Final = ".collection_state.json"
LOG_FILENAME: Final = "mtscraper.log"
DIAGNOSTICS_DIRNAME: Final = "diagnostics"

DIAG_LAST_TILE_JSON: Final = "last_tile.json"
DIAG_LAST_HTML: Final = "last_page.html"
DIAG_LAST_EXCEPTION_LOG: Final = "last_exception.log"
DIAG_UNKNOWN_PAYLOAD_DIR: Final = "unknown_payloads"

# --------------------------------------------------------------------------- #
# Area of interest — the eastern-Mediterranean coastal countries whose shores
# bound the default view. Ships are classified by proximity to these coasts.
# --------------------------------------------------------------------------- #
AOI_COUNTRIES: Final = ("Turkey", "Syria", "Lebanon", "Israel", "Egypt", "Cyprus")
#: Static boundary asset (trimmed Natural Earth) shipped inside the package;
#: regenerate via scripts/build_aoi_asset.py (dev-time only, never at runtime).
AOI_DATA_DIRNAME: Final = "data"
AOI_ASSET_FILENAME: Final = "aoi_countries.geojson"

# --------------------------------------------------------------------------- #
# Environment configuration
# --------------------------------------------------------------------------- #
ENV_PREFIX: Final = "MT_"

DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
