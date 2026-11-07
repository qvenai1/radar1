"""Immutable, dependency-free constants.

This module intentionally contains *only* literals. Anything that needs logic,
validation, or environment awareness belongs in :mod:`adsb_scraper.config`.
Keeping the two apart means a constant can never accidentally depend on runtime
state, and the whole file can be imported from anywhere without side effects.
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
# Data source (ADS-B Exchange globe front-end)
# --------------------------------------------------------------------------- #
DEFAULT_GLOBE_URL: Final = "https://globe.adsbexchange.com/"
DEFAULT_AIRCRAFT_ENDPOINT: Final = "/data/aircraft.json"

#: Substrings that mark a network response as *possibly* carrying aircraft data.
#: Used by the interception strategy to decide which responses are interesting.
AIRCRAFT_URL_HINTS: Final = ("aircraft.json", "globe_", "/data/", "/re-api/")

#: CSS selectors that indicate the interactive map has mounted.
MAP_READY_SELECTORS: Final = ("#map_canvas", ".leaflet-container", "#map")

# --------------------------------------------------------------------------- #
# Adaptive parser — vocabulary used to recognise an "aircraft" object
# --------------------------------------------------------------------------- #
#: Canonical field names, mapped to the set of aliases seen across ADS-B feeds
#: (readsb/tar1090, VRS ``acList``, dump1090, OpenSky, ...). The adaptive parser
#: scores candidate objects by how many of these *concepts* they carry, so a
#: schema change in any single field name does not blind the scraper.
AIRCRAFT_FIELD_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "icao": ("hex", "icao", "Icao", "icao24", "addr"),
    "latitude": ("lat", "Lat", "latitude"),
    "longitude": ("lon", "lng", "Long", "longitude"),
    "altitude": ("alt_baro", "alt", "Alt", "altitude", "baro_altitude", "galt"),
    "track": ("track", "Trak", "trak", "heading", "true_track"),
    "callsign": ("flight", "call", "Call", "callsign"),
    "ground_speed": ("gs", "Spd", "spd", "speed", "velocity", "ground_speed"),
}

#: A concept is "strongly identifying" if its presence is good evidence that an
#: object is an aircraft. ``icao`` + a coordinate is essentially conclusive.
STRONG_IDENTIFYING_CONCEPTS: Final = ("icao", "latitude", "longitude")

#: Minimum heuristic score (see parser) for an object to be treated as aircraft.
MIN_AIRCRAFT_MATCH_SCORE: Final = 3

#: Keys whose *values* are worth descending into when hunting for the array.
#: Purely an optimisation hint; the parser still walks unknown keys.
LIKELY_ARRAY_KEYS: Final = ("aircraft", "planes", "acList", "flights", "states", "ac")

# --------------------------------------------------------------------------- #
# Diagnostics — filenames written on failure so no debugging info is ever lost
# --------------------------------------------------------------------------- #
DIAG_LAST_RESPONSE_JSON: Final = "last_response.json"
DIAG_LAST_RESPONSE_TXT: Final = "last_response.txt"
DIAG_LAST_HEADERS_JSON: Final = "last_headers.json"
DIAG_LAST_STATUS_TXT: Final = "last_status.txt"
DIAG_LAST_HTML: Final = "last_html.html"
DIAG_LAST_EXCEPTION_LOG: Final = "last_exception.log"
DIAG_RESPONSE_SIZE_TXT: Final = "response_size.txt"
DIAG_UNKNOWN_PAYLOAD_DIR: Final = "unknown_payloads"

# --------------------------------------------------------------------------- #
# Output layout
# --------------------------------------------------------------------------- #
RESULTS_FILENAME: Final = "aircraft.jsonl"
#: scraplan-shaped position TIME-SERIES (one line per aircraft per cycle, no dedup).
STATE_VECTORS_FILENAME: Final = "state_vectors.jsonl"
COMPLETED_CACHE_FILENAME: Final = ".completed_icao_cache.txt"
DIAGNOSTICS_DIRNAME: Final = "diagnostics"
LOG_FILENAME: Final = "scraper.log"

#: Area of interest — the seven countries whose airspace we collect. The order
#: is also the containment-test order in :mod:`adsb_scraper.area`.
AOI_COUNTRIES: Final = ("Syria", "Turkey", "Iraq", "Jordan", "Lebanon", "Israel", "Cyprus")

#: Static boundary asset (trimmed Natural Earth) shipped inside the package.
AOI_DATA_DIRNAME: Final = "data"
AOI_ASSET_FILENAME: Final = "aoi_countries.geojson"

#: Append-only, fsync-durable journal of collection gaps (power loss etc.).
COLLECTION_GAPS_FILENAME: Final = "collection_gaps.jsonl"
#: Mutable operational cursor: the wall-clock time of the last collection cycle,
#: used to detect gaps that span a process restart. NOT part of the archive.
COLLECTION_STATE_FILENAME: Final = ".collection_state.json"

# --------------------------------------------------------------------------- #
# Environment configuration
# --------------------------------------------------------------------------- #
ENV_PREFIX: Final = "ADSB_"

DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
