"""Area of interest: a maritime three-state filter.

The interest area is the union of the eastern-Mediterranean coastal country
polygons (Natural Earth), loaded once at startup from a static asset — never
fetched at runtime. Around that union sits a **border buffer**: a vessel outside
every landmass but within ``buffer_km`` of a coast is still part of the
neighbourhood.

Because vessels sit on *water*, the INSIDE state (point within a land polygon) is
rare — it catches ships in ports/estuaries. The meaningful maritime bucket is
NEAR: within a kilometric buffer of a coast, attributed to the nearest country.

Buffering is done in a local **azimuthal-equidistant (AEQD) projection** centred
on the region, so ``buffer_km`` is a true kilometric distance, not a degree
offset (which distorts badly at these latitudes). Point-in-country tests stay in
lon/lat (topology is projection-invariant).

Two concerns kept separate:

* **Interest filter** — :meth:`classify` returns INSIDE / NEAR / OUTSIDE.
* **Fetch envelope** — :meth:`bounding_box` is the envelope of the buffered
  geometry, sized to the buffer rather than an arbitrary flat margin.

A shared :class:`AreaFilter` Protocol lets a plain rectangle stand in for the
polygon AOI (``--area bbox``) and keeps the validator source-agnostic.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .constants import AOI_COUNTRIES, MAX_LAT, MAX_LON, MIN_LAT, MIN_LON
from .exceptions import ConfigError
from .logger import get_logger
from .models import GeoBounds

_log = get_logger("area")

_BBOX_PAD_DEG = 0.1


class AreaState(enum.Enum):
    """Where a vessel sits relative to the coastal neighbourhood."""

    INSIDE = "inside"  # within a land polygon (in port / estuary) — rare for ships
    NEAR = "near"  # on water within buffer_km of a coast
    OUTSIDE = "outside"  # open sea beyond the buffer


@dataclass(frozen=True, slots=True)
class AreaClass:
    """Result of classifying a position against the area of interest."""

    state: AreaState
    country: str | None = None  # set iff INSIDE
    near: str | None = None  # nearest country, set iff NEAR


@runtime_checkable
class AreaFilter(Protocol):
    """The area surface the validator depends on (polygon or rectangle)."""

    def classify(self, lat: float, lon: float) -> AreaClass: ...
    def bounding_box(self) -> GeoBounds: ...
    @property
    def center(self) -> tuple[float, float]: ...


# --------------------------------------------------------------------------- #
# Simple rectangular filter (no shapely) — the ``--area bbox`` mode
# --------------------------------------------------------------------------- #


class BboxArea:
    """A plain lat/lon rectangle as an :class:`AreaFilter`.

    INSIDE when the point is in the rectangle, OUTSIDE otherwise; NEAR is never
    produced (a rectangle has no border-buffer notion).
    """

    __slots__ = ("_bounds",)

    def __init__(self, bounds: GeoBounds) -> None:
        self._bounds = bounds

    def classify(self, lat: float, lon: float) -> AreaClass:
        if self._bounds.contains(lat, lon):
            return AreaClass(AreaState.INSIDE)
        return AreaClass(AreaState.OUTSIDE)

    def bounding_box(self) -> GeoBounds:
        return self._bounds

    @property
    def center(self) -> tuple[float, float]:
        return self._bounds.center


# --------------------------------------------------------------------------- #
# Polygon area of interest (shapely + pyproj) — the default
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CountryArea:
    """One country: a prepared lon/lat polygon plus its AEQD-projected geometry."""

    name: str
    prepared: Any  # shapely PreparedGeometry in lon/lat (point-in-polygon)
    geom_m: Any  # shapely geometry in AEQD metres (distance-to-border)


class AreaOfInterest:
    """The coastal neighbourhood: country polygons + a kilometric border buffer."""

    __slots__ = ("_bbox", "_buffered_prep", "_countries", "_forward")

    def __init__(
        self,
        countries: tuple[CountryArea, ...],
        buffered_prep: Any,
        forward: Any,
        bounding_box: GeoBounds,
    ) -> None:
        self._countries = countries
        self._buffered_prep = buffered_prep  # prepared AEQD buffered union
        self._forward = forward  # lon/lat -> AEQD metres
        self._bbox = bounding_box

    def classify(self, lat: float, lon: float) -> AreaClass:
        from shapely.geometry import Point

        point = Point(lon, lat)
        for country in self._countries:
            if country.prepared.contains(point):
                return AreaClass(AreaState.INSIDE, country=country.name)

        projected = Point(*self._to_metres(lon, lat))
        if not self._buffered_prep.contains(projected):
            return AreaClass(AreaState.OUTSIDE)
        nearest = min(self._countries, key=lambda c: c.geom_m.distance(projected))
        return AreaClass(AreaState.NEAR, near=nearest.name)

    def bounding_box(self) -> GeoBounds:
        return self._bbox

    @property
    def center(self) -> tuple[float, float]:
        return self._bbox.center

    @property
    def country_names(self) -> tuple[str, ...]:
        return tuple(country.name for country in self._countries)

    def _to_metres(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = self._forward.transform(lon, lat)
        return float(x), float(y)


def load_area_of_interest(asset_path: Path, buffer_km: float) -> AreaOfInterest:
    """Load and validate the boundary asset ONCE, building the buffered geometry.

    Fails fast with :class:`ConfigError` if ``buffer_km`` is negative or the asset
    is missing, malformed, or missing a required country.
    """
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform, unary_union
    from shapely.prepared import prep

    if buffer_km < 0:
        raise ConfigError("area buffer_km must be >= 0")
    if not asset_path.exists():
        raise ConfigError(
            f"AOI boundary asset not found: {asset_path} "
            "(regenerate it with scripts/build_aoi_asset.py; boundaries are never "
            "downloaded at runtime)"
        )
    try:
        raw = json.loads(asset_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"AOI asset is unreadable: {asset_path}: {exc}") from exc

    features = raw.get("features") if isinstance(raw, dict) else None
    if not isinstance(features, list) or not features:
        raise ConfigError(f"AOI asset contains no features: {asset_path}")

    geometries: dict[str, Any] = {}
    for feature in features:
        try:
            name = feature["properties"]["name"]
            geometry = shape(feature["geometry"])
        except (KeyError, TypeError, AttributeError) as exc:
            raise ConfigError(f"AOI asset feature is malformed ({exc})") from exc
        if not geometry.is_valid:  # repair minor self-intersections deterministically
            geometry = geometry.buffer(0)
        geometries[str(name)] = geometry

    missing = [country for country in AOI_COUNTRIES if country not in geometries]
    if missing:
        raise ConfigError(f"AOI asset is missing required countries: {missing}")

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

    buffered_m = unary_union(projected).buffer(buffer_km * 1000.0)
    bbox = _envelope(transform(inverse.transform, buffered_m))

    _log.info(
        "AOI loaded: %d countries (%s); border buffer=%.0fkm; fetch box=%s",
        len(countries),
        ", ".join(AOI_COUNTRIES),
        buffer_km,
        bbox.to_dict(),
    )
    return AreaOfInterest(tuple(countries), prep(buffered_m), forward, bbox)


def _envelope(buffered_wgs: Any) -> GeoBounds:
    min_lon, min_lat, max_lon, max_lat = buffered_wgs.bounds
    return GeoBounds(
        north=min(MAX_LAT, max_lat + _BBOX_PAD_DEG),
        south=max(MIN_LAT, min_lat - _BBOX_PAD_DEG),
        east=min(MAX_LON, max_lon + _BBOX_PAD_DEG),
        west=max(MIN_LON, min_lon - _BBOX_PAD_DEG),
    )
