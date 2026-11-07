"""Data validity and normalization — a pipeline stage between parse and persist.

This layer turns a parsed :class:`~adsb_scraper.models.Aircraft` into a
:class:`ValidatedRecord`: it normalises identity, classifies position quality,
raises typed flags for radius and plausibility, and computes the great-circle
distance from the configured centre. It is deliberately *non-destructive* —
nothing is dropped or fabricated here. Every observation becomes a record
carrying the reasons (flags) a downstream consumer needs to decide inclusion.

Two collaborators:

* :class:`AircraftRecordValidator` — the pure per-record validator (a
  :class:`RecordValidator`). No I/O, no wall clock; fully unit-testable.
* :class:`CollectionGapDetector` — a purely *observational* detector that
  records collection gaps (e.g. an operator power loss) to a durable,
  append-only journal so downstream event-detection can tell a real aircraft
  "signal lost" apart from a gap on our side. It never triggers recovery.
"""

from __future__ import annotations

import asyncio
import enum
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from .area import AreaOfInterest, AreaState
from .config import ValidationConfig
from .geo import GeoService
from .logger import get_logger
from .models import Aircraft

_log = get_logger("validation")

_ICAO_RE = re.compile(r"[0-9a-f]{6}")

#: A coordinate is treated as quantized when it sits within this tolerance of its
#: one-decimal rounding (e.g. 35.700001 -> 35.7). Genuine GPS fixes carry several
#: honest decimals and fall well outside it.
_QUANTIZE_TOLERANCE = 1e-4

#: An altitude of exactly 0 combined with airborne speed above this is suspect.
_ZERO_ALT_MIN_GS_KT = 50.0

#: Quantized coordinates only matter when the aircraft is actually moving fast.
_QUANTIZE_MIN_GS_KT = 100.0


class PositionQuality(enum.Enum):
    """How much to trust a record's position fix."""

    CONFIRMED = "confirmed"
    NO_POSITION = "no_position"
    SUSPECT = "suspect"


class ValidationFlag(enum.Enum):
    """A reason attached to a record. Flags never cause a drop by themselves."""

    OUTSIDE_AREA = "outside_area"
    NEAR_BORDER = "near_border"
    INVALID_IDENTITY = "invalid_identity"
    PLACEHOLDER_CALLSIGN = "placeholder_callsign"
    ZERO_ALT_AIRBORNE = "zero_alt_airborne"
    QUANTIZED_POSITION = "quantized_position"
    IMPLAUSIBLE_KINEMATICS = "implausible_kinematics"


#: The subset of flags that describe a doubtful *position* (=> SUSPECT quality).
_PLAUSIBILITY_FLAGS = frozenset(
    {
        ValidationFlag.ZERO_ALT_AIRBORNE,
        ValidationFlag.QUANTIZED_POSITION,
        ValidationFlag.IMPLAUSIBLE_KINEMATICS,
    }
)


@dataclass(frozen=True, slots=True)
class ValidatedRecord:
    """A normalized observation plus the provenance of every decision made."""

    icao: str | None  # None only when identity is invalid
    callsign: str | None
    latitude: float | None
    longitude: float | None
    altitude: float | None
    ground_speed: float | None
    track: float | None
    position_quality: PositionQuality
    flags: frozenset[ValidationFlag]
    distance_km: float | None
    country: str | None  # the AOI country CONTAINING the point (INSIDE), else None
    near: str | None  # nearest AOI country when NEAR a border, else None
    source: str
    raw: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def has_valid_identity(self) -> bool:
        return ValidationFlag.INVALID_IDENTITY not in self.flags

    @property
    def outside_area(self) -> bool:
        return ValidationFlag.OUTSIDE_AREA in self.flags

    @property
    def is_near(self) -> bool:
        return ValidationFlag.NEAR_BORDER in self.flags

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def in_area(self) -> bool:
        """A positioned record in the neighborhood: INSIDE a country or NEAR one."""
        return self.has_position and not self.outside_area

    def data_dict(self) -> dict[str, Any]:
        """Normalized aircraft fields (same shape as the existing record ``data``)."""
        return {
            "icao": self.icao,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "track": self.track,
            "ground_speed": self.ground_speed,
            "callsign": self.callsign,
        }

    def provenance(self) -> dict[str, Any]:
        """The additive ``validation`` block persisted alongside each record."""
        return {
            "position_quality": self.position_quality.value,
            "flags": sorted(flag.value for flag in self.flags),
            "distance_km": self.distance_km,
            "country": self.country,
            "near": self.near,
            "source": self.source,
        }


@runtime_checkable
class RecordValidator(Protocol):
    """Turns one parsed aircraft into a validated, normalized record."""

    def validate(self, aircraft: Aircraft, *, source: str) -> ValidatedRecord: ...


class AircraftRecordValidator:
    """Concrete :class:`RecordValidator`. Pure: no I/O, no wall clock.

    The active area filter is three-state containment against the injected
    :class:`~adsb_scraper.area.AreaOfInterest`: INSIDE a country, NEAR a border
    (within the buffer), or OUTSIDE. ``distance_km`` is retained as provenance
    only (great-circle distance from the AOI envelope centre).
    """

    __slots__ = ("_area", "_center_lat", "_center_lon", "_cfg", "_geo")

    def __init__(
        self,
        *,
        area: AreaOfInterest,
        config: ValidationConfig,
        geo: GeoService | None = None,
    ) -> None:
        self._area = area
        self._center_lat, self._center_lon = area.center
        self._cfg = config
        self._geo = geo or GeoService()

    def validate(self, aircraft: Aircraft, *, source: str) -> ValidatedRecord:
        flags: set[ValidationFlag] = set()

        icao = self._normalize_icao(aircraft.icao)
        if icao is None:
            flags.add(ValidationFlag.INVALID_IDENTITY)

        callsign = self._normalize_callsign(aircraft.callsign, flags)

        lat, lon = aircraft.latitude, aircraft.longitude
        alt, gs = aircraft.altitude, aircraft.ground_speed
        has_position = lat is not None and lon is not None

        distance_km: float | None = None
        country: str | None = None
        near: str | None = None
        if has_position:
            assert lat is not None and lon is not None  # narrowing for the type checker
            distance_km = round(
                self._geo.haversine_km(self._center_lat, self._center_lon, lat, lon), 3
            )
            classification = self._area.classify(lat, lon)
            if classification.state is AreaState.INSIDE:
                country = classification.country
            elif classification.state is AreaState.NEAR:
                near = classification.near
                flags.add(ValidationFlag.NEAR_BORDER)
            else:  # OUTSIDE
                flags.add(ValidationFlag.OUTSIDE_AREA)

        self._add_plausibility_flags(flags, lat, lon, alt, gs, has_position=has_position)
        quality = self._classify_position(flags, has_position=has_position)

        return ValidatedRecord(
            icao=icao,
            callsign=callsign,
            latitude=lat,
            longitude=lon,
            altitude=alt,
            ground_speed=gs,
            track=aircraft.track,
            position_quality=quality,
            flags=frozenset(flags),
            distance_km=distance_km,
            country=country,
            near=near,
            source=source,
            raw=aircraft.raw,
        )

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _normalize_icao(icao: str | None) -> str | None:
        """Return the lowercased 24-bit ICAO hex address, or ``None`` if invalid."""
        if not icao:
            return None
        candidate = icao.strip().lower()
        return candidate if _ICAO_RE.fullmatch(candidate) else None

    @staticmethod
    def _normalize_callsign(callsign: str | None, flags: set[ValidationFlag]) -> str | None:
        """Null-out placeholder callsigns (all ``@``, empty, whitespace)."""
        if callsign is None:
            return None
        stripped = callsign.strip()
        if not stripped or set(stripped) == {"@"}:
            flags.add(ValidationFlag.PLACEHOLDER_CALLSIGN)
            return None
        return stripped

    def _add_plausibility_flags(
        self,
        flags: set[ValidationFlag],
        lat: float | None,
        lon: float | None,
        alt: float | None,
        gs: float | None,
        *,
        has_position: bool,
    ) -> None:
        if alt is not None and gs is not None and alt == 0 and gs > _ZERO_ALT_MIN_GS_KT:
            flags.add(ValidationFlag.ZERO_ALT_AIRBORNE)
        if (
            has_position
            and gs is not None
            and gs > _QUANTIZE_MIN_GS_KT
            and self._is_quantized(lat)
            and self._is_quantized(lon)
        ):
            flags.add(ValidationFlag.QUANTIZED_POSITION)
        if (gs is not None and gs > self._cfg.plausibility_max_gs_kt) or (
            alt is not None and alt > self._cfg.plausibility_max_alt_ft
        ):
            flags.add(ValidationFlag.IMPLAUSIBLE_KINEMATICS)

    @staticmethod
    def _is_quantized(value: float | None) -> bool:
        if value is None:
            return False
        return abs(value - round(value, 1)) <= _QUANTIZE_TOLERANCE

    @staticmethod
    def _classify_position(flags: set[ValidationFlag], *, has_position: bool) -> PositionQuality:
        if not has_position:
            return PositionQuality.NO_POSITION
        if flags & _PLAUSIBILITY_FLAGS:
            return PositionQuality.SUSPECT
        return PositionQuality.CONFIRMED


# --------------------------------------------------------------------------- #
# Collection gap detection (observational only)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CollectionGap:
    """A wall-clock interval during which no collection happened on our side."""

    started_at: str
    ended_at: str
    duration_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "collection_gap",
            "start": self.started_at,
            "end": self.ended_at,
            "duration_s": round(self.duration_s, 3),
        }


@runtime_checkable
class GapJournal(Protocol):
    """Durable store for gap markers plus the last-cycle cursor."""

    def last_cycle_at(self) -> datetime | None: ...
    async def record_cycle(self, when: datetime) -> None: ...
    async def record_gap(self, gap: CollectionGap) -> None: ...


class CollectionGapDetector:
    """Records gaps between consecutive cycles. Purely observational.

    The detector holds only the decision logic; all durability is delegated to an
    injected :class:`GapJournal`, so it can be tested with an in-memory fake and
    no filesystem. It NEVER triggers recovery, alerting, or any behavioural
    change — a gap is data, not a fault.
    """

    __slots__ = ("_journal", "_last", "_threshold_s")

    def __init__(self, journal: GapJournal, threshold_s: float) -> None:
        self._journal = journal
        self._threshold_s = threshold_s
        # Seed from the durable cursor so a gap spanning a restart is still seen.
        self._last = journal.last_cycle_at()

    async def observe(self, now: datetime) -> CollectionGap | None:
        """Note that a cycle occurred at ``now``; record a gap if one preceded it."""
        gap: CollectionGap | None = None
        if self._last is not None:
            elapsed = (now - self._last).total_seconds()
            if elapsed > self._threshold_s:
                gap = CollectionGap(
                    started_at=self._last.isoformat(),
                    ended_at=now.isoformat(),
                    duration_s=elapsed,
                )
                _log.info("collection_gap", extra=gap.to_dict())
                await self._journal.record_gap(gap)
        self._last = now
        await self._journal.record_cycle(now)
        return gap


class JsonlGapJournal:
    """Filesystem :class:`GapJournal`: append-only gaps + a mutable cursor file.

    The gaps file follows the archive's immutability discipline (append-only,
    fsync-durable). The cursor file is explicitly *mutable* operational state —
    the last cycle time — atomically overwritten each cycle so a restart can
    still detect a gap that spanned the downtime.
    """

    __slots__ = ("_cursor_path", "_fsync", "_gaps_path")

    def __init__(self, gaps_path: Path, cursor_path: Path, *, fsync: bool = True) -> None:
        self._gaps_path = gaps_path
        self._cursor_path = cursor_path
        self._fsync = fsync

    def last_cycle_at(self) -> datetime | None:
        try:
            raw = self._cursor_path.read_text(encoding="utf-8")
            value = json.loads(raw).get("last_cycle_at")
        except (OSError, ValueError):
            return None
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    async def record_cycle(self, when: datetime) -> None:
        await asyncio.to_thread(self._write_cursor, when)

    async def record_gap(self, gap: CollectionGap) -> None:
        self._gaps_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._append_gap, gap)

    def _write_cursor(self, when: datetime) -> None:
        self._cursor_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cursor_path.with_suffix(self._cursor_path.suffix + ".tmp")
        payload = json.dumps({"last_cycle_at": when.isoformat()}).encode("utf-8")
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            if self._fsync:
                os.fsync(handle.fileno())
        os.replace(tmp, self._cursor_path)

    def _append_gap(self, gap: CollectionGap) -> None:
        line = json.dumps(gap.to_dict(), ensure_ascii=False) + "\n"
        with open(self._gaps_path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            if self._fsync:
                os.fsync(handle.fileno())
