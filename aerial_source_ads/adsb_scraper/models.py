"""Immutable domain value objects.

These types are the *lingua franca* passed between layers. They contain no I/O
and only self-consistency validation, which keeps the domain testable in
isolation and prevents malformed remote data from propagating inward.
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

    Invariants are checked at construction, so any ``GeoBounds`` in the system
    is guaranteed well-formed and downstream code need not re-validate.
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
        return (
            self.south + (self.north - self.south) / 2.0,
            self.west + (self.east - self.west) / 2.0,
        )

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east

    def to_dict(self) -> dict[str, float]:
        return {
            "north": self.north,
            "south": self.south,
            "east": self.east,
            "west": self.west,
        }


@dataclass(frozen=True, slots=True)
class Aircraft:
    """A single, *validated* aircraft observation.

    Only ``icao`` is guaranteed present; every other field is optional because
    real feeds are sparse (an aircraft with no position fix still appears). The
    untouched source object is retained in ``raw`` so no upstream data is lost.
    """

    icao: str
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    track: float | None = None
    ground_speed: float | None = None
    callsign: str | None = None
    raw: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "icao": self.icao,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "track": self.track,
            "ground_speed": self.ground_speed,
            "callsign": self.callsign,
        }


@dataclass(frozen=True, slots=True)
class RawResponse:
    """A transport-agnostic snapshot of a fetched response.

    Produced by the browser/fetch layer and consumed by the parser. Crucially it
    carries the *bytes*, not a pre-decoded object: deciding how to decode is the
    parser's job, which is exactly the separation the old code lacked.
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
class AircraftRecord:
    """A persisted observation: an aircraft plus collection metadata.

    The provenance fields (``position_quality``, ``flags``, ``distance_km``,
    ``provenance_source``) are an ADDITIVE extension produced by the validation
    layer. They serialise into a nested ``validation`` block only when present,
    so records written before this layer existed remain byte-for-byte valid and
    any reader that ignores the block is unaffected.
    """

    icao: str
    fetched_at: str
    source: str
    data: dict[str, Any]
    position_quality: str | None = None
    flags: tuple[str, ...] = ()
    distance_km: float | None = None
    country: str | None = None
    near: str | None = None
    provenance_source: str | None = None

    @property
    def has_provenance(self) -> bool:
        return self.position_quality is not None

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "icao": self.icao,
            "fetched_at": self.fetched_at,
            "source": self.source,
            "data": self.data,
        }
        if self.has_provenance:
            record["validation"] = {
                "position_quality": self.position_quality,
                "flags": list(self.flags),
                "distance_km": self.distance_km,
                "country": self.country,
                "near": self.near,
                "source": self.provenance_source,
            }
        return record


@dataclass(frozen=True, slots=True)
class CycleReport:
    """Structured outcome of one collection cycle (the unit of observability)."""

    cycle: int
    source: str | None
    http_status: int | None
    content_type: str | None
    response_bytes: int
    aircraft_received: int
    aircraft_in_area: int
    near_border: int
    new_icaos: int
    duplicates: int
    out_of_area: int
    invalid_identity: int
    parse_failed: bool
    processing_seconds: float
    memory_mb: float | None
    browser_healthy: bool
    total_known: int

    def as_log_fields(self) -> dict[str, Any]:
        """Flat mapping suitable for structured (key=value / JSON) logging."""
        return {
            "cycle": self.cycle,
            "source": self.source,
            "status": self.http_status,
            "content_type": self.content_type,
            "bytes": self.response_bytes,
            "received": self.aircraft_received,
            "in_area": self.aircraft_in_area,
            "near_border": self.near_border,
            "new": self.new_icaos,
            "duplicates": self.duplicates,
            "out_of_area": self.out_of_area,
            "invalid_identity": self.invalid_identity,
            "parse_failed": self.parse_failed,
            "proc_s": round(self.processing_seconds, 4),
            "mem_mb": None if self.memory_mb is None else round(self.memory_mb, 1),
            "browser_ok": self.browser_healthy,
            "total_known": self.total_known,
        }


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Aggregate runtime metrics accumulated over the whole process lifetime."""

    uptime_seconds: float
    total_cycles: int
    total_requests: int
    total_failures: int
    total_retries: int
    browser_restarts: int
    page_reloads: int
    total_aircraft_seen: int
    unique_aircraft: int
    avg_cycle_seconds: float
    avg_response_bytes: float
    avg_aircraft_per_cycle: float

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "uptime_s": round(self.uptime_seconds, 1),
            "cycles": self.total_cycles,
            "requests": self.total_requests,
            "failures": self.total_failures,
            "retries": self.total_retries,
            "browser_restarts": self.browser_restarts,
            "page_reloads": self.page_reloads,
            "unique_aircraft": self.unique_aircraft,
            "avg_cycle_s": round(self.avg_cycle_seconds, 4),
            "avg_bytes": round(self.avg_response_bytes, 1),
            "avg_ac_per_cycle": round(self.avg_aircraft_per_cycle, 2),
        }
