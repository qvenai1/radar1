"""Data validity and normalization — a pipeline stage between parse and persist.

Turns a parsed :class:`~mtscraper.models.Vessel` into a :class:`ValidatedRecord`:
it normalises identity, classifies position quality against the area of
interest, raises typed flags for area membership and plausibility, and computes
the great-circle distance from the AOI centre. It is deliberately
*non-destructive* — nothing is dropped or fabricated here; every observation
becomes a record carrying the reasons (flags) a consumer needs to decide
inclusion.

Two collaborators:

* :class:`VesselRecordValidator` — the pure per-record validator. No I/O, no
  wall clock; fully unit-testable.
* :class:`CollectionGapDetector` — a purely *observational* detector that
  records collection gaps (e.g. a power loss) to a durable, append-only journal
  so downstream analysis can tell a real vessel "signal lost" apart from a gap
  on our side. It never triggers recovery.
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

from .area import AreaFilter, AreaState
from .config import ValidationConfig
from .geo import haversine_km
from .logger import get_logger
from .models import Vessel

_log = get_logger("validation")

#: A valid MarineTraffic SHIP_ID is a positive integer string.
_SHIP_ID_RE = re.compile(r"[1-9][0-9]*")

#: A coordinate is treated as quantized when within this tolerance of its
#: one-decimal rounding (e.g. 34.700001 -> 34.7). Genuine GPS fixes carry several
#: honest decimals and fall well outside it.
_QUANTIZE_TOLERANCE = 1e-4

#: Quantized coordinates only matter when the vessel is actually moving.
_QUANTIZE_MIN_SPEED_KT = 3.0


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
    PLACEHOLDER_NAME = "placeholder_name"
    QUANTIZED_POSITION = "quantized_position"
    IMPLAUSIBLE_KINEMATICS = "implausible_kinematics"
    STALE_POSITION = "stale_position"


#: The subset of flags describing a doubtful *position* (=> SUSPECT quality).
_PLAUSIBILITY_FLAGS = frozenset(
    {
        ValidationFlag.QUANTIZED_POSITION,
        ValidationFlag.IMPLAUSIBLE_KINEMATICS,
        ValidationFlag.STALE_POSITION,
    }
)

_PLACEHOLDER_NAMES = frozenset({"", "unknown", "[sat-ais]", "sat-ais", "no name", "unnamed"})


@dataclass(frozen=True, slots=True)
class ValidatedRecord:
    """A normalized observation plus the provenance of every decision made."""

    ship_id: str | None  # None only when identity is invalid
    mmsi: str | None
    latitude: float | None
    longitude: float | None
    speed_knots: float | None
    course: float | None
    heading: float | None
    shipname: str | None
    shiptype: str | None
    flag: str | None
    destination: str | None
    length_m: float | None
    width_m: float | None
    elapsed_min: float | None
    position_quality: PositionQuality
    flags: frozenset[ValidationFlag]
    distance_km: float | None
    country: str | None  # the AOI country CONTAINING the point (INSIDE), else None
    near: str | None  # nearest AOI country when NEAR a coast, else None
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
        """A positioned record in the neighbourhood: INSIDE a coast or NEAR one."""
        return self.has_position and not self.outside_area

    def data_dict(self) -> dict[str, Any]:
        """Normalized vessel fields (same shape as ``Vessel.to_dict``)."""
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
    """Turns one parsed vessel into a validated, normalized record."""

    def validate(self, vessel: Vessel, *, source: str) -> ValidatedRecord: ...


class VesselRecordValidator:
    """Concrete :class:`RecordValidator`. Pure: no I/O, no wall clock.

    The active area filter is three-state containment against the injected
    :class:`~mtscraper.area.AreaFilter`: INSIDE a coast, NEAR one (within the
    buffer), or OUTSIDE. ``distance_km`` is retained as provenance only
    (great-circle distance from the area centre).
    """

    __slots__ = ("_area", "_center_lat", "_center_lon", "_cfg")

    def __init__(self, *, area: AreaFilter, config: ValidationConfig) -> None:
        self._area = area
        self._center_lat, self._center_lon = area.center
        self._cfg = config

    def validate(self, vessel: Vessel, *, source: str) -> ValidatedRecord:
        flags: set[ValidationFlag] = set()

        ship_id = self._normalize_ship_id(vessel.ship_id)
        if ship_id is None:
            flags.add(ValidationFlag.INVALID_IDENTITY)

        shipname = self._normalize_name(vessel.shipname, flags)

        lat, lon = vessel.latitude, vessel.longitude
        has_position = lat is not None and lon is not None

        distance_km: float | None = None
        country: str | None = None
        near: str | None = None
        if has_position:
            assert lat is not None and lon is not None  # narrowing for the type checker
            distance_km = round(haversine_km(self._center_lat, self._center_lon, lat, lon), 3)
            classification = self._area.classify(lat, lon)
            if classification.state is AreaState.INSIDE:
                country = classification.country
            elif classification.state is AreaState.NEAR:
                near = classification.near
                flags.add(ValidationFlag.NEAR_BORDER)
            else:  # OUTSIDE
                flags.add(ValidationFlag.OUTSIDE_AREA)

        self._add_plausibility_flags(flags, vessel, has_position=has_position)
        quality = self._classify_position(flags, has_position=has_position)

        return ValidatedRecord(
            ship_id=ship_id,
            mmsi=vessel.mmsi,
            latitude=lat,
            longitude=lon,
            speed_knots=vessel.speed_knots,
            course=vessel.course,
            heading=vessel.heading,
            shipname=shipname,
            shiptype=vessel.shiptype,
            flag=vessel.flag,
            destination=vessel.destination,
            length_m=vessel.length_m,
            width_m=vessel.width_m,
            elapsed_min=vessel.elapsed_min,
            position_quality=quality,
            flags=frozenset(flags),
            distance_km=distance_km,
            country=country,
            near=near,
            source=source,
            raw=vessel.raw,
        )

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _normalize_ship_id(ship_id: str | None) -> str | None:
        if not ship_id:
            return None
        candidate = ship_id.strip()
        return candidate if _SHIP_ID_RE.fullmatch(candidate) else None

    @staticmethod
    def _normalize_name(name: str | None, flags: set[ValidationFlag]) -> str | None:
        if name is None:
            return None
        stripped = name.strip()
        if stripped.casefold() in _PLACEHOLDER_NAMES:
            flags.add(ValidationFlag.PLACEHOLDER_NAME)
            return None
        return stripped

    def _add_plausibility_flags(
        self, flags: set[ValidationFlag], vessel: Vessel, *, has_position: bool
    ) -> None:
        speed, course, heading = vessel.speed_knots, vessel.course, vessel.heading
        if (
            (speed is not None and (speed < 0 or speed > self._cfg.plausibility_max_speed_kn))
            or (course is not None and not 0.0 <= course <= 360.0)
            or (heading is not None and not 0.0 <= heading <= 360.0)
        ):
            flags.add(ValidationFlag.IMPLAUSIBLE_KINEMATICS)
        if (
            has_position
            and speed is not None
            and speed > _QUANTIZE_MIN_SPEED_KT
            and self._is_quantized(vessel.latitude)
            and self._is_quantized(vessel.longitude)
        ):
            flags.add(ValidationFlag.QUANTIZED_POSITION)
        if vessel.elapsed_min is not None and vessel.elapsed_min > self._cfg.stale_after_min:
            flags.add(ValidationFlag.STALE_POSITION)

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

    Holds only the decision logic; all durability is delegated to an injected
    :class:`GapJournal`, so it is testable with an in-memory fake. It NEVER
    triggers recovery or any behavioural change — a gap is data, not a fault.
    """

    __slots__ = ("_journal", "_last", "_threshold_s")

    def __init__(self, journal: GapJournal, threshold_s: float) -> None:
        self._journal = journal
        self._threshold_s = threshold_s
        # Seed from the durable cursor so a gap spanning a restart is still seen.
        self._last = journal.last_cycle_at()

    async def observe(self, now: datetime) -> CollectionGap | None:
        """Note a cycle at ``now``; record a gap if one preceded it."""
        gap: CollectionGap | None = None
        if self._last is not None:
            elapsed = (now - self._last).total_seconds()
            if elapsed > self._threshold_s:
                gap = CollectionGap(
                    started_at=self._last.isoformat(),
                    ended_at=now.isoformat(),
                    duration_s=elapsed,
                )
                _log.info("collection_gap detected: %.0fs", elapsed)
                await self._journal.record_gap(gap)
        self._last = now
        await self._journal.record_cycle(now)
        return gap


class JsonlGapJournal:
    """Filesystem :class:`GapJournal`: append-only gaps + a mutable cursor file."""

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
