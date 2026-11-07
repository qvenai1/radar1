from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from mtscraper.area import BboxArea
from mtscraper.config import ValidationConfig
from mtscraper.models import GeoBounds, Vessel
from mtscraper.validation import (
    CollectionGap,
    CollectionGapDetector,
    JsonlGapJournal,
    PositionQuality,
    ValidationFlag,
    VesselRecordValidator,
)

BOUNDS = GeoBounds(north=34.0, south=32.4, east=36.0, west=34.4)
CFG = ValidationConfig()


def _validator() -> VesselRecordValidator:
    return VesselRecordValidator(area=BboxArea(BOUNDS), config=CFG)


def _v(**kw: object) -> Vessel:
    # A realistic multi-decimal fix (NOT on a 0.1 grid) so it is not "quantized".
    base: dict[str, object] = {"ship_id": "123", "latitude": 33.234, "longitude": 35.187}
    base.update(kw)
    return Vessel(**base)  # type: ignore[arg-type]


def test_confirmed_in_area() -> None:
    rec = _validator().validate(_v(speed_knots=12.0), source="interception")
    assert rec.position_quality is PositionQuality.CONFIRMED
    assert rec.in_area
    assert rec.flags == frozenset()
    assert rec.distance_km is not None


def test_no_position() -> None:
    rec = _validator().validate(_v(latitude=None, longitude=None), source="s")
    assert rec.position_quality is PositionQuality.NO_POSITION
    assert not rec.in_area


def test_invalid_identity() -> None:
    rec = _validator().validate(_v(ship_id="0"), source="s")
    assert rec.ship_id is None
    assert ValidationFlag.INVALID_IDENTITY in rec.flags


def test_placeholder_name_nulled() -> None:
    rec = _validator().validate(_v(shipname="[SAT-AIS]"), source="s")
    assert rec.shipname is None
    assert ValidationFlag.PLACEHOLDER_NAME in rec.flags


def test_implausible_speed_is_suspect() -> None:
    rec = _validator().validate(_v(speed_knots=250.0), source="s")
    assert ValidationFlag.IMPLAUSIBLE_KINEMATICS in rec.flags
    assert rec.position_quality is PositionQuality.SUSPECT


def test_out_of_range_course_is_implausible() -> None:
    rec = _validator().validate(_v(course=999.0), source="s")
    assert ValidationFlag.IMPLAUSIBLE_KINEMATICS in rec.flags


def test_stale_position_flag() -> None:
    rec = _validator().validate(_v(elapsed_min=999.0), source="s")
    assert ValidationFlag.STALE_POSITION in rec.flags
    assert rec.position_quality is PositionQuality.SUSPECT


def test_quantized_position_while_moving() -> None:
    rec = _validator().validate(_v(latitude=33.7, longitude=35.2, speed_knots=10.0), source="s")
    assert ValidationFlag.QUANTIZED_POSITION in rec.flags


def test_out_of_area_flag() -> None:
    rec = _validator().validate(_v(latitude=10.0, longitude=10.0), source="s")
    assert ValidationFlag.OUTSIDE_AREA in rec.flags
    assert not rec.in_area


def test_provenance_serialization() -> None:
    rec = _validator().validate(_v(), source="interception")
    prov = rec.provenance()
    assert prov["position_quality"] == "confirmed"
    assert prov["source"] == "interception"
    assert isinstance(prov["flags"], list)


# -- gap detection ------------------------------------------------------- #


class FakeJournal:
    def __init__(self, last: datetime | None = None) -> None:
        self._last = last
        self.cycles: list[datetime] = []
        self.gaps: list[CollectionGap] = []

    def last_cycle_at(self) -> datetime | None:
        return self._last

    async def record_cycle(self, when: datetime) -> None:
        self.cycles.append(when)

    async def record_gap(self, gap: CollectionGap) -> None:
        self.gaps.append(gap)


async def test_no_gap_on_first_observe() -> None:
    j = FakeJournal(last=None)
    det = CollectionGapDetector(j, threshold_s=1800)
    assert await det.observe(datetime.now(UTC)) is None
    assert len(j.cycles) == 1
    assert j.gaps == []


async def test_gap_recorded_when_threshold_exceeded() -> None:
    t0 = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
    j = FakeJournal(last=t0)
    det = CollectionGapDetector(j, threshold_s=1800)
    gap = await det.observe(t0 + timedelta(hours=1))
    assert gap is not None
    assert gap.duration_s == 3600.0
    assert len(j.gaps) == 1


async def test_no_gap_within_threshold() -> None:
    t0 = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
    j = FakeJournal(last=t0)
    det = CollectionGapDetector(j, threshold_s=1800)
    assert await det.observe(t0 + timedelta(seconds=60)) is None
    assert j.gaps == []


def test_jsonl_gap_journal_cursor_roundtrip(tmp_path: Path) -> None:
    import asyncio

    journal = JsonlGapJournal(tmp_path / "gaps.jsonl", tmp_path / ".state.json")
    assert journal.last_cycle_at() is None
    when = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    asyncio.run(journal.record_cycle(when))
    assert journal.last_cycle_at() == when
