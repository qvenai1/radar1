"""Fold validated vessels into the persisted archives.

The accumulator owns the collection *policy*: which vessels count as "in area",
whether out-of-area vessels are persisted, and how a vessel becomes a catalogue
entry versus a time-series point. It consumes :class:`ValidatedRecord`s (identity
normalised, area classified, provenance attached) and knows nothing about how
they were produced. It owns no I/O mechanics — those live in the sinks.
"""

from __future__ import annotations

from dataclasses import dataclass

from .logger import get_logger
from .models import VesselRecord
from .persistence import CatalogueSink, PositionSink
from .validation import ValidatedRecord

_log = get_logger("accumulator")


@dataclass(frozen=True, slots=True)
class AccumulationResult:
    received: int
    in_area: int
    new_vessels: int
    duplicates: int
    out_of_area: int
    invalid_identity: int
    total_known: int
    new_ids: frozenset[str]
    #: Observations actually appended to the time-series. Lower than the number
    #: routed to the sink whenever a report merely restates the previous one; the
    #: difference is the redundancy the sink dropped, not data lost.
    positions_written: int = 0


class Accumulator:
    """Route validated vessels into the catalogue (dedup) and position sinks."""

    def __init__(
        self,
        catalogue: CatalogueSink,
        positions: PositionSink | None,
        *,
        persist_out_of_area: bool = False,
    ) -> None:
        self._catalogue = catalogue
        self._positions = positions
        self._persist_out_of_area = persist_out_of_area

    @property
    def unique_count(self) -> int:
        return self._catalogue.known_count

    def known_ids(self) -> set[str]:
        """Every catalogued SHIP_ID — the enrichment pending-queue draws from this."""
        return self._catalogue.known_ids()

    def accept(self, records: list[ValidatedRecord], fetched_at: str) -> AccumulationResult:
        received = len(records)
        in_area = new_vessels = duplicates = out_of_area = invalid_identity = 0
        positions_written = 0
        new_ids: set[str] = set()

        for validated in self._dedupe_latest(records):
            if validated.ship_id is None:  # invalid identity — cannot catalogue
                invalid_identity += 1
                continue

            inside = validated.in_area
            if inside:
                in_area += 1
            else:
                out_of_area += 1
                if not self._persist_out_of_area:
                    continue

            record = VesselRecord(
                ship_id=validated.ship_id,
                fetched_at=fetched_at,
                source=validated.source,
                data=validated.data_dict(),
                distance_km=validated.distance_km,
                in_area=inside,
                position_quality=validated.position_quality.value,
                flags=tuple(sorted(f.value for f in validated.flags)),
                country=validated.country,
                near=validated.near,
            )
            if self._catalogue.add(record):
                new_vessels += 1
                new_ids.add(validated.ship_id)
            else:
                duplicates += 1
            if self._positions is not None and self._positions.add(record):
                positions_written += 1

        return AccumulationResult(
            received=received,
            in_area=in_area,
            new_vessels=new_vessels,
            duplicates=duplicates,
            out_of_area=out_of_area,
            invalid_identity=invalid_identity,
            total_known=self._catalogue.known_count,
            new_ids=frozenset(new_ids),
            positions_written=positions_written,
        )

    @staticmethod
    def _dedupe_latest(records: list[ValidatedRecord]) -> list[ValidatedRecord]:
        """Collapse duplicate SHIP_IDs within one cycle (overlapping tiles).

        The last occurrence wins. Records with an invalid identity have no key,
        so each is kept as its own entry (counted, never catalogued).
        """
        latest: dict[str, ValidatedRecord] = {}
        anonymous: list[ValidatedRecord] = []
        for record in records:
            if record.ship_id is None:
                anonymous.append(record)
            else:
                latest[record.ship_id] = record
        return [*latest.values(), *anonymous]
