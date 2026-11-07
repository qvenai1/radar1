"""Shared, source-neutral geographic classifier for scraplan.

ONE classifier, owned by scraplan, used by EVERY ingest path (opensky,
airplanes_live, adsbexchange) — so geo classification is a property of the
POSITION, not of whichever feed supplied the row. Lifted near-verbatim from
adsb_scraper/area.py (the proven seven-country + true-kilometric-border-buffer
implementation) so there is a single implementation and a single asset, never
two divergent copies.

Classification reads ONLY latitude/longitude in degrees. It is UNAFFECTED by
scraplan's metric altitude/velocity: altitude and speed never enter classify().

Buffering is done in a local azimuthal-equidistant (AEQD) projection centred on
the region, so ``buffer_km`` is a real kilometric distance from the border, not
a degree offset. Point-in-country tests stay in lon/lat (topology is
projection-invariant), so the common in-country case skips the projection.

The boundary asset is loaded ONCE and NEVER fetched at runtime; a missing,
unreadable, or incomplete asset fails fast with :class:`GeoAssetError`.
"""

from __future__ import annotations

import enum
import json
import logging
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep

logger = logging.getLogger("scraplan.geo_area")

#: The seven-country neighborhood (Natural Earth names as they appear in the asset).
AOI_COUNTRIES: tuple[str, ...] = ("Syria", "Turkey", "Iraq", "Jordan", "Lebanon", "Israel", "Cyprus")

EARTH_RADIUS_KM = 6371.0088
_MIN_LAT, _MAX_LAT, _MIN_LON, _MAX_LON = -90.0, 90.0, -180.0, 180.0
#: Small extra pad (degrees) on the envelope whose centre anchors distance provenance.
_BBOX_PAD_DEG = 0.1

DEFAULT_BUFFER_KM = 50.0
DEFAULT_ASSET = Path(__file__).resolve().parent / "data" / "aoi_countries.geojson"

#: Nautical mile → kilometre (exact, by definition: 1 NM = 1852 m).
NM_TO_KM = 1.852
#: The maritime "near a country" band is the TERRITORIAL SEA limit, not the
#: aircraft 50 km land buffer. UNCLOS Art. 3 sets the territorial sea at 12
#: nautical miles from the baseline — a standard, citable, legally-meaningful
#: threshold ("vessel within a country's territorial waters"). 50 km would mark
#: nearly every coastal vessel "near" and lose analytical meaning, so the
#: maritime layer deliberately uses 12 NM. Configurable via env (see
#: :func:`territorial_sea_km`) so it can be tuned without code changes.
TERRITORIAL_SEA_NM = 12.0
DEFAULT_TERRITORIAL_SEA_KM = TERRITORIAL_SEA_NM * NM_TO_KM  # 22.224 km

#: Provenance identity written to state_vectors.geo_source for EVERY row scraplan
#: classifies. Deliberately distinct from the natural-key ``source`` column: a
#: row records its feed (source) and its classifier (geo_source) independently,
#: so all feeds show the SAME classifier once P3 owns the geo columns.
GEO_SOURCE = "scraplan-aoi"


class GeoAssetError(RuntimeError):
    """The AOI boundary asset is missing, unreadable, or incomplete (fail fast)."""


class AreaState(enum.Enum):
    """Where a position sits relative to the seven-country neighborhood."""

    INSIDE = "inside"    # within a country polygon
    NEAR = "near"        # outside all, but within buffer_km of a border
    OUTSIDE = "outside"  # beyond the buffer entirely


@dataclass(frozen=True, slots=True)
class AreaClass:
    """Result of classifying a position against the area of interest."""

    state: AreaState
    country: str | None = None  # set iff INSIDE
    near: str | None = None     # nearest country, set iff NEAR


@dataclass(frozen=True, slots=True)
class GeoColumns:
    """Flat classification mapped to scraplan's state_vectors geo columns."""

    country: str | None
    near_country: str | None
    distance_km: float | None


@dataclass(frozen=True, slots=True)
class MaritimeGeoColumns:
    """Maritime classification mapped to vessel_states' geo columns.

    Mirrors :class:`GeoColumns` (the aircraft flat mapping) but adapts the
    semantics for the sea, and — unlike the aircraft mapping — ``distance_km``
    is the TRUE distance to the nearest country's coast, not the envelope-centre
    provenance anchor. See :meth:`AreaOfInterest.classify_maritime_columns`.
    """

    country: str | None       # territorial-sea country iff within the band, else None
    near_country: str | None  # nearest coast iff OUTSIDE the band, else None
    distance_km: float | None  # true km to the nearest of the seven countries' coast


@dataclass(frozen=True, slots=True)
class CountryArea:
    """One country: a prepared lon/lat polygon plus its AEQD-projected geometry."""

    name: str
    prepared: Any  # shapely PreparedGeometry in lon/lat (point-in-polygon)
    geom_m: Any    # shapely geometry in AEQD metres (distance-to-border)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class AreaOfInterest:
    """The seven-country neighborhood: country polygons + a border buffer."""

    __slots__ = ("_buffered_prep", "_center", "_countries", "_forward", "_union_boundary_m")

    def __init__(
        self,
        countries: tuple[CountryArea, ...],
        buffered_prep: Any,
        forward: Transformer,
        center: tuple[float, float],
        union_boundary_m: Any,
    ) -> None:
        self._countries = countries
        self._buffered_prep = buffered_prep  # prepared AEQD buffered union
        self._forward = forward              # lon/lat -> AEQD metres
        self._center = center                # (lat, lon) — distance-provenance anchor
        self._union_boundary_m = union_boundary_m  # AEQD boundary of the 7-country
        #: UNION (internal land borders dissolved) — the reference for inland depth.

    def _nearest(self, projected: Any) -> tuple[CountryArea, float]:
        """Nearest country polygon to a projected point, and its metric distance.

        ONE true-kilometric distance computation, shared by the aircraft NEAR
        test (:meth:`classify`) and the maritime territorial-sea test
        (:meth:`nearest_country_km`) — the same AEQD ``geom_m.distance``
        machinery, never a second geo implementation. ``distance`` is 0.0 when
        the point lies on/inside a polygon.
        """
        best: CountryArea | None = None
        best_m: float | None = None
        for country in self._countries:
            d = country.geom_m.distance(projected)
            if best_m is None or d < best_m:
                best, best_m = country, d
        assert best is not None and best_m is not None  # _countries is non-empty (asset validated)
        return best, float(best_m)

    def classify(self, lat: float, lon: float) -> AreaClass:
        """Classify a position as INSIDE / NEAR / OUTSIDE the neighborhood."""
        point = Point(lon, lat)
        for country in self._countries:
            if country.prepared.contains(point):
                return AreaClass(AreaState.INSIDE, country=country.name)

        projected = Point(*self._to_metres(lon, lat))
        if not self._buffered_prep.contains(projected):
            return AreaClass(AreaState.OUTSIDE)
        nearest, _ = self._nearest(projected)
        return AreaClass(AreaState.NEAR, near=nearest.name)

    def nearest_country_km(self, lat: float, lon: float) -> tuple[str, float]:
        """(nearest of the seven countries, true kilometres to its coast).

        The distance is the projected AEQD distance to that country's polygon —
        the coast for a vessel at sea, and 0.0 when the point lies on/inside a
        polygon (a harbour, strait, or internal waters). This is the exact
        machinery :meth:`classify` uses for its NEAR test, exposed as a real
        kilometric distance for the maritime layer.
        """
        nearest, dist_m = self._nearest(Point(*self._to_metres(lon, lat)))
        return nearest.name, dist_m / 1000.0

    def inland_depth_km(self, lat: float, lon: float) -> float | None:
        """Depth of a position INSIDE the seven-country land mass (km), or ``None``
        if the position is at sea (outside every country polygon).

        The GNSS position-quality layer's R2 evidence: a vessel fix that lands
        inside a country polygon is on land — impossible for a ship beyond a
        harbour fringe. "Depth" is the distance from the point to the boundary of
        the UNARY UNION of the seven countries (AEQD projected), NOT to the
        individual country's border. The union dissolves the countries' shared
        INTERNAL land borders, so depth measures distance to the OUTER edge of the
        whole landmass (the coast, or the external frontier with a non-AOI
        neighbour) — the analytically meaningful "how far inland" a reported
        position is. 0.0 on the boundary itself.

        Reuses the SAME point-in-polygon (lon/lat) + AEQD projected-distance
        machinery as :meth:`classify` / :meth:`nearest_country_km`; the union
        boundary is precomputed once at load, never a second geo implementation.

        DOCUMENTED RESIDUAL BLIND SPOT: the union's outer edge includes land
        borders with non-AOI neighbours (Turkey–Iran, Iraq–Saudi Arabia,
        Israel–Egypt near Rafah). A position coherently offset to within ~2 km of
        such an external land border reads a small depth and can evade the R2
        depth test; scatter near those borders is still caught kinematically by
        R1. Accepted and documented at this stage, not solved.
        """
        point = Point(lon, lat)
        if not any(country.prepared.contains(point) for country in self._countries):
            return None
        projected = Point(*self._to_metres(lon, lat))
        return self._union_boundary_m.distance(projected) / 1000.0

    def classify_maritime_columns(
        self, lat: float, lon: float,
        territorial_sea_km: float = DEFAULT_TERRITORIAL_SEA_KM,
    ) -> MaritimeGeoColumns:
        """Maritime geo columns for a vessel position.

        Mirrors the aircraft geo layer's INSIDE/NEAR structure, adapted for the
        sea, where "INSIDE a country polygon" almost never applies to a ship:

          * ``distance_km`` is the TRUE kilometric distance to the nearest of
            the seven countries' coast (0.0 on/inside a polygon). Unlike the
            aircraft layer's envelope-centre provenance distance, for a vessel
            the distance to the coast is the analytically meaningful quantity —
            it is precisely what the 12 NM territorial-sea test is computed from.
          * A vessel within ``territorial_sea_km`` of a coast is inside that
            country's TERRITORIAL SEA (UNCLOS Art. 3, 12 NM) → ``country`` names
            it, ``near_country`` is None. This is the sea analogue of the
            aircraft INSIDE case: the vessel is within a country's sovereign
            maritime jurisdiction.
          * Otherwise ``country`` is None and ``near_country`` names the nearest
            coast (the analogue of aircraft NEAR); ``distance_km`` says how far.

        ``country`` and ``near_country`` are therefore MUTUALLY EXCLUSIVE for any
        positioned row, exactly like the aircraft layer, and
        ``country is not None`` iff ``distance_km <= territorial_sea_km``.
        """
        name, dist_km = self.nearest_country_km(lat, lon)
        dist_km = round(dist_km, 3)
        if dist_km <= territorial_sea_km:
            return MaritimeGeoColumns(country=name, near_country=None, distance_km=dist_km)
        return MaritimeGeoColumns(country=None, near_country=name, distance_km=dist_km)

    def classify_columns(self, lat: float, lon: float) -> GeoColumns:
        """classify() + distance provenance, mapped to scraplan's geo columns.

        distance_km is the great-circle distance from the AOI envelope centre —
        provenance only, identical anchor for every source.
        """
        result = self.classify(lat, lon)
        distance_km = round(haversine_km(self._center[0], self._center[1], lat, lon), 3)
        if result.state is AreaState.INSIDE:
            return GeoColumns(country=result.country, near_country=None, distance_km=distance_km)
        if result.state is AreaState.NEAR:
            return GeoColumns(country=None, near_country=result.near, distance_km=distance_km)
        return GeoColumns(country=None, near_country=None, distance_km=distance_km)

    @property
    def center(self) -> tuple[float, float]:
        """Envelope centre (lat, lon) — the reference for distance provenance."""
        return self._center

    @property
    def country_names(self) -> tuple[str, ...]:
        return tuple(country.name for country in self._countries)

    def _to_metres(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = self._forward.transform(lon, lat)
        return float(x), float(y)


def load_area_of_interest(
    asset_path: Path = DEFAULT_ASSET, buffer_km: float = DEFAULT_BUFFER_KM
) -> AreaOfInterest:
    """Load and validate the boundary asset ONCE, building the buffered geometry.

    Fails fast with :class:`GeoAssetError` if buffer_km is negative or the asset
    is missing, malformed, or does not contain all seven countries.
    """
    if buffer_km < 0:
        raise GeoAssetError("buffer_km must be >= 0")
    if not asset_path.exists():
        raise GeoAssetError(
            f"AOI boundary asset not found: {asset_path} "
            "(it is a committed asset, never downloaded at runtime)"
        )
    try:
        raw = json.loads(asset_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GeoAssetError(f"AOI asset is unreadable: {asset_path}: {exc}") from exc

    features = raw.get("features") if isinstance(raw, dict) else None
    if not isinstance(features, list) or not features:
        raise GeoAssetError(f"AOI asset contains no features: {asset_path}")

    geometries: dict[str, Any] = {}
    for feature in features:
        try:
            name = feature["properties"]["name"]
            geometry = shape(feature["geometry"])
        except (KeyError, TypeError, AttributeError) as exc:
            raise GeoAssetError(f"AOI asset feature is malformed ({exc})") from exc
        if not geometry.is_valid:  # repair minor self-intersections deterministically
            geometry = geometry.buffer(0)
        geometries[str(name)] = geometry

    missing = [country for country in AOI_COUNTRIES if country not in geometries]
    if missing:
        raise GeoAssetError(f"AOI asset is missing required countries: {missing}")

    # AEQD centred on the union's centroid gives true metres near the region.
    union = unary_union([geometries[c] for c in AOI_COUNTRIES])
    centre = union.centroid
    aeqd = f"+proj=aeqd +lat_0={centre.y} +lon_0={centre.x} +units=m +datum=WGS84 +no_defs"
    forward = Transformer.from_crs("EPSG:4326", aeqd, always_xy=True)
    inverse = Transformer.from_crs(aeqd, "EPSG:4326", always_xy=True)

    countries: list[CountryArea] = []
    projected: list[Any] = []
    for name in AOI_COUNTRIES:
        geom_m = transform(forward.transform, geometries[name])
        countries.append(CountryArea(name=name, prepared=prep(geometries[name]), geom_m=geom_m))
        projected.append(geom_m)

    union_m = unary_union(projected)          # AEQD union; internal borders dissolved
    buffered_m = union_m.buffer(buffer_km * 1000.0)
    center = _envelope_center(transform(inverse.transform, buffered_m))

    logger.info(
        "AOI loaded: %d countries (%s); border buffer=%.0fkm; centre(lat,lon)=%s",
        len(countries), ", ".join(AOI_COUNTRIES), buffer_km, center,
    )
    return AreaOfInterest(tuple(countries), prep(buffered_m), forward, center, union_m.boundary)


def _envelope_center(buffered_wgs: Any) -> tuple[float, float]:
    """Centre (lat, lon) of the buffered geometry's padded, clamped envelope."""
    min_lon, min_lat, max_lon, max_lat = buffered_wgs.bounds
    north = min(_MAX_LAT, max_lat + _BBOX_PAD_DEG)
    south = max(_MIN_LAT, min_lat - _BBOX_PAD_DEG)
    east = min(_MAX_LON, max_lon + _BBOX_PAD_DEG)
    west = max(_MIN_LON, min_lon - _BBOX_PAD_DEG)
    return (south + (north - south) / 2.0, west + (east - west) / 2.0)


@lru_cache(maxsize=1)
def get_area() -> AreaOfInterest:
    """Process-wide load-once singleton (fail-fast on first use).

    buffer_km and asset path are configurable via env, defaulting to 50 km and
    the committed asset — matching adsb_scraper's discipline.
    """
    asset = Path(os.environ.get("SCRAPLAN_AOI_ASSET", str(DEFAULT_ASSET)))
    buffer_km = float(os.environ.get("SCRAPLAN_AOI_BUFFER_KM", str(DEFAULT_BUFFER_KM)))
    return load_area_of_interest(asset, buffer_km)


def territorial_sea_km() -> float:
    """Maritime territorial-sea band in km, default 12 NM (22.224 km).

    Configurable via ``SCRAPLAN_TERRITORIAL_SEA_KM`` so the band can be tuned
    (e.g. a contiguous-zone study at 24 NM) without touching code — read on each
    call, never cached, so tests/ops can override per process. Deliberately NOT
    the aircraft :data:`DEFAULT_BUFFER_KM` (50 km land buffer): see
    :data:`DEFAULT_TERRITORIAL_SEA_KM`.
    """
    return float(os.environ.get("SCRAPLAN_TERRITORIAL_SEA_KM", str(DEFAULT_TERRITORIAL_SEA_KM)))
