"""Immutable domain value objects — the lingua franca between layers.

These types carry no I/O and only self-consistency validation, which keeps the
domain testable in isolation and stops malformed remote data from propagating
inward.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .constants import MAX_LAT, MAX_LON, MIN_LAT, MIN_LON
from .exceptions import ConfigError


@dataclass(frozen=True, slots=True)
class GeoBounds:
    """An axis-aligned latitude/longitude rectangle.

    Invariants are checked at construction, so any ``GeoBounds`` in the system is
    guaranteed well-formed and downstream code need not re-validate.
    """

    north: float
    south: float
    east: float
    west: float

    def __post_init__(self) -> None:
        if not (MIN_LAT <= self.south <= self.north <= MAX_LAT):
            raise ConfigError(f"invalid latitude span: south={self.south} north={self.north}")
        if not (MIN_LON <= self.west <= self.east <= MAX_LON):
            raise ConfigError(f"invalid longitude span: west={self.west} east={self.east}")

    @property
    def center(self) -> tuple[float, float]:
        """(lat, lon) of the rectangle centre."""
        return (
            self.south + (self.north - self.south) / 2.0,
            self.west + (self.east - self.west) / 2.0,
        )

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east

    def to_dict(self) -> dict[str, float]:
        return {"north": self.north, "south": self.south, "east": self.east, "west": self.west}


@dataclass(frozen=True, slots=True)
class RawResponse:
    """A transport-agnostic snapshot of a fetched response.

    Produced by the browser/fetch layer and consumed by the parser. It carries
    the *bytes*, not a pre-decoded object: deciding how to interpret them is the
    parser's job.
    """

    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    source: str

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    @property
    def size(self) -> int:
        return len(self.body)


@dataclass(frozen=True, slots=True)
class Vessel:
    """A single vessel observation parsed from a tile row.

    Identity is ``ship_id`` — MarineTraffic's internal vessel id, which is the
    only stable key present in the tile feed (MMSI is usually absent there).
    Every other field is optional because the feed is sparse. Numeric fields are
    coerced from their source strings; the untouched source row is kept in
    ``raw`` so nothing upstream is lost.
    """

    ship_id: str
    latitude: float | None = None
    longitude: float | None = None
    speed_knots: float | None = None
    course: float | None = None
    heading: float | None = None
    shipname: str | None = None
    shiptype: str | None = None
    flag: str | None = None
    destination: str | None = None
    length_m: float | None = None
    width_m: float | None = None
    mmsi: str | None = None
    elapsed_min: float | None = None
    raw: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ship_id": self.ship_id,
            "mmsi": self.mmsi,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "speed_knots": self.speed_knots,
            "course": self.course,
            "heading": self.heading,
            "shipname": self.shipname,
            "shiptype": self.shiptype,
            "flag": self.flag,
            "destination": self.destination,
            "length_m": self.length_m,
            "width_m": self.width_m,
            "elapsed_min": self.elapsed_min,
        }


@dataclass(frozen=True, slots=True)
class VesselRecord:
    """A persisted observation: a vessel plus collection metadata.

    The provenance fields (``position_quality``, ``flags``, ``country``,
    ``near``) are an ADDITIVE extension produced by the validation layer. They
    serialise into a nested ``validation`` block only when present, so records
    written without validation stay valid and any reader that ignores the block
    is unaffected.
    """

    ship_id: str
    fetched_at: str
    source: str
    data: dict[str, Any]
    distance_km: float | None = None
    in_area: bool = True
    position_quality: str | None = None
    flags: tuple[str, ...] = ()
    country: str | None = None
    near: str | None = None

    @property
    def has_provenance(self) -> bool:
        return self.position_quality is not None

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ship_id": self.ship_id,
            "fetched_at": self.fetched_at,
            "source": self.source,
            "in_area": self.in_area,
            "data": self.data,
        }
        if self.distance_km is not None:
            record["distance_km"] = self.distance_km
        if self.has_provenance:
            record["validation"] = {
                "position_quality": self.position_quality,
                "flags": list(self.flags),
                "country": self.country,
                "near": self.near,
            }
        return record


@dataclass(frozen=True, slots=True)
class CycleReport:
    """Structured outcome of one collection cycle (the unit of observability)."""

    cycle: int
    source: str | None
    tiles_seen: int
    vessels_received: int
    vessels_in_area: int
    near_border: int
    new_vessels: int
    duplicates: int
    out_of_area: int
    invalid_identity: int
    suspect: int
    enriched: int
    parse_failed: bool
    processing_seconds: float
    browser_healthy: bool
    total_known: int
    #: Lines actually appended to the time-series this cycle. Well below
    #: ``vessels_in_area`` in steady state, because most observations restate the
    #: previous one — see :class:`~mtscraper.persistence.PositionSink`.
    positions_written: int = 0

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "source": self.source,
            "tiles": self.tiles_seen,
            "received": self.vessels_received,
            "in_area": self.vessels_in_area,
            "near_border": self.near_border,
            "new": self.new_vessels,
            "duplicates": self.duplicates,
            "out_of_area": self.out_of_area,
            "invalid_identity": self.invalid_identity,
            "suspect": self.suspect,
            "positions_written": self.positions_written,
            "enriched": self.enriched,
            "parse_failed": self.parse_failed,
            "proc_s": round(self.processing_seconds, 4),
            "browser_ok": self.browser_healthy,
            "total_known": self.total_known,
        }


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Aggregate runtime metrics accumulated over the whole process lifetime."""

    uptime_seconds: float
    total_cycles: int
    total_failures: int
    browser_restarts: int
    page_reloads: int
    collection_gaps: int
    total_vessels_seen: int
    unique_vessels: int
    identities_resolved: int
    avg_cycle_seconds: float
    avg_tiles_per_cycle: float
    avg_vessels_per_cycle: float

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "uptime_s": round(self.uptime_seconds, 1),
            "cycles": self.total_cycles,
            "failures": self.total_failures,
            "browser_restarts": self.browser_restarts,
            "page_reloads": self.page_reloads,
            "gaps": self.collection_gaps,
            "unique_vessels": self.unique_vessels,
            "identities_resolved": self.identities_resolved,
            "avg_cycle_s": round(self.avg_cycle_seconds, 4),
            "avg_tiles": round(self.avg_tiles_per_cycle, 2),
            "avg_vessels": round(self.avg_vessels_per_cycle, 2),
        }
