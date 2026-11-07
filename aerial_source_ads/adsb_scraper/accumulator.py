"""Deduplication and record production.

The accumulator is the one place that owns the "have I seen this airframe?"
question. It consumes :class:`~adsb_scraper.validation.ValidatedRecord`s (the
output of the validation layer), deduplicates strictly by **ICAO** — the
physical airframe address, never the mutable callsign — and writes new records to
the sink carrying their full provenance.

Persistence policy lives here, not in the validator: a record flagged
``OUTSIDE_RADIUS`` is persisted only when ``persist_out_of_radius`` is set, and a
record with ``INVALID_IDENTITY`` is never keyed (it cannot dedup) — both are
*counted and reported*, never silently dropped. The raw capture is untouched;
this only governs what enters the derived, ICAO-keyed archive.

It depends only on the :class:`AircraftSink` Protocol and an injected
:class:`Clock`, so it is unit-testable with an in-memory sink and no wall clock.
State mutation happens between ``await`` points on the single orchestrator
coroutine, so no lock is required.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from .logger import get_logger
from .models import AircraftRecord
from .persistence import AircraftSink
from .utils import Clock, SystemClock
from .validation import ValidatedRecord

_log = get_logger("accumulator")


@dataclass(frozen=True, slots=True)
class AccumulationResult:
    """Outcome of processing one batch of validated records."""

    added: int
    duplicates: int
    in_area: int
    near_border: int
    skipped_out_of_area: int
    skipped_invalid_identity: int
    new_icaos: frozenset[str]


class Accumulator:
    """Deduplicates validated records by ICAO and writes new ones to the sink."""

    __slots__ = ("_clock", "_known", "_on_new", "_persist_out_of_area", "_sink", "_source")

    def __init__(
        self,
        sink: AircraftSink,
        known_icaos: set[str],
        *,
        source: str = "adsbexchange",
        persist_out_of_area: bool = False,
        clock: Clock | None = None,
        on_new: Callable[[frozenset[str]], Awaitable[None]] | None = None,
    ) -> None:
        self._sink = sink
        self._known = known_icaos
        self._source = source
        self._persist_out_of_area = persist_out_of_area
        self._clock: Clock = clock or SystemClock()
        # Optional hook (e.g. persist the ICAO cache) invoked with new ICAOs.
        self._on_new = on_new

    @property
    def unique_count(self) -> int:
        return len(self._known)

    async def process(self, records: Iterable[ValidatedRecord]) -> AccumulationResult:
        added = duplicates = in_area = near_border = 0
        skipped_out_of_area = skipped_invalid_identity = 0
        new_icaos: set[str] = set()
        timestamp = self._clock.now().isoformat()

        for record in records:
            # Identity is ICAO, never callsign. No identity => cannot be keyed.
            if not record.has_valid_identity or record.icao is None:
                skipped_invalid_identity += 1
                continue
            # Area is a policy decision, made here — not a drop in the validator.
            # NEAR records carry NEAR_BORDER (not OUTSIDE_AREA), so they are kept
            # by default: they are the point of the neighborhood.
            if record.outside_area and not self._persist_out_of_area:
                skipped_out_of_area += 1
                continue
            if record.in_area:
                in_area += 1
            if record.is_near:
                near_border += 1

            icao = record.icao
            if icao in self._known:
                duplicates += 1
                continue
            self._known.add(icao)
            new_icaos.add(icao)
            await self._sink.write(self._to_persisted(record, timestamp))
            added += 1

        if new_icaos and self._on_new is not None:
            await self._on_new(frozenset(new_icaos))
        if added or skipped_invalid_identity or skipped_out_of_area:
            _log.debug(
                "accumulated %d new (%d dup, %d out-of-area skipped, %d invalid-identity skipped)",
                added,
                duplicates,
                skipped_out_of_area,
                skipped_invalid_identity,
            )
        return AccumulationResult(
            added=added,
            duplicates=duplicates,
            in_area=in_area,
            near_border=near_border,
            skipped_out_of_area=skipped_out_of_area,
            skipped_invalid_identity=skipped_invalid_identity,
            new_icaos=frozenset(new_icaos),
        )

    def _to_persisted(self, record: ValidatedRecord, timestamp: str) -> AircraftRecord:
        assert record.icao is not None  # guaranteed by has_valid_identity gate
        return AircraftRecord(
            icao=record.icao,
            fetched_at=timestamp,
            source=self._source,
            data=record.data_dict(),
            position_quality=record.position_quality.value,
            flags=tuple(sorted(flag.value for flag in record.flags)),
            distance_km=record.distance_km,
            country=record.country,
            near=record.near,
            provenance_source=record.source,
        )
