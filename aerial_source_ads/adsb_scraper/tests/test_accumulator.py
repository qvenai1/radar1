"""Unit tests for the deduplicating accumulator (consumes ValidatedRecords)."""

from __future__ import annotations

from adsb_scraper.accumulator import Accumulator
from adsb_scraper.validation import PositionQuality, ValidatedRecord, ValidationFlag

from .conftest import FakeSink, FrozenClock


def _vr(
    icao: str | None,
    *,
    flags: set[ValidationFlag] | None = None,
    quality: PositionQuality = PositionQuality.CONFIRMED,
    lat: float | None = 33.5,
    lon: float | None = 36.3,
    distance_km: float | None = 10.0,
    country: str | None = "Syria",
    near: str | None = None,
    source: str = "interception",
) -> ValidatedRecord:
    return ValidatedRecord(
        icao=icao,
        callsign="SVA1",
        latitude=lat,
        longitude=lon,
        altitude=30000.0,
        ground_speed=400.0,
        track=90.0,
        position_quality=quality,
        flags=frozenset(flags or set()),
        distance_km=distance_km,
        country=country,
        near=near,
        source=source,
    )


async def test_dedup_within_and_across_batches() -> None:
    sink = FakeSink()
    acc = Accumulator(sink, set(), clock=FrozenClock())
    r1 = await acc.process([_vr("aaaaaa"), _vr("bbbbbb"), _vr("aaaaaa")])
    r2 = await acc.process([_vr("bbbbbb"), _vr("cccccc")])
    assert (r1.added, r1.duplicates) == (2, 1)
    assert (r2.added, r2.duplicates) == (1, 1)
    assert acc.unique_count == 3
    assert sink.total_written == 3


async def test_identity_is_icao_not_callsign() -> None:
    """Two records with the same ICAO but different callsigns are one airframe."""
    sink = FakeSink()
    acc = Accumulator(sink, set(), clock=FrozenClock())
    a = _vr("abc123")
    b = ValidatedRecord(
        icao="abc123",
        callsign="DIFFERENT",
        latitude=33.5,
        longitude=36.3,
        altitude=0.0,
        ground_speed=0.0,
        track=0.0,
        position_quality=PositionQuality.CONFIRMED,
        flags=frozenset(),
        distance_km=5.0,
        country="Syria",
        near=None,
        source="interception",
    )
    result = await acc.process([a, b])
    assert result.added == 1 and result.duplicates == 1


async def test_near_record_persisted_by_default() -> None:
    """NEAR records are in the neighborhood — kept even under default policy."""
    sink = FakeSink()
    acc = Accumulator(sink, set(), clock=FrozenClock())  # persist_out_of_area False
    result = await acc.process(
        [_vr("aaaaaa", flags={ValidationFlag.NEAR_BORDER}, country=None, near="Lebanon")]
    )
    assert result.added == 1
    assert result.near_border == 1
    assert result.in_area == 1
    assert result.skipped_out_of_area == 0
    assert sink.records[0].to_dict()["validation"]["near"] == "Lebanon"


async def test_invalid_identity_never_persisted_or_keyed() -> None:
    sink = FakeSink()
    acc = Accumulator(sink, set(), clock=FrozenClock())
    result = await acc.process([_vr(None, flags={ValidationFlag.INVALID_IDENTITY}), _vr("aaaaaa")])
    assert result.skipped_invalid_identity == 1
    assert result.added == 1
    assert sink.total_written == 1
    assert acc.unique_count == 1  # invalid-identity record not keyed


async def test_out_of_area_skipped_by_default() -> None:
    sink = FakeSink()
    acc = Accumulator(sink, set(), clock=FrozenClock())  # persist_out_of_area defaults False
    result = await acc.process(
        [
            _vr("aaaaaa", flags={ValidationFlag.OUTSIDE_AREA}, country=None),
            _vr("bbbbbb"),
        ]
    )
    assert result.skipped_out_of_area == 1
    assert result.in_area == 1
    assert sink.total_written == 1


async def test_out_of_area_persisted_when_policy_enabled() -> None:
    sink = FakeSink()
    acc = Accumulator(sink, set(), persist_out_of_area=True, clock=FrozenClock())
    result = await acc.process([_vr("aaaaaa", flags={ValidationFlag.OUTSIDE_AREA}, country=None)])
    assert result.skipped_out_of_area == 0
    assert result.added == 1
    assert sink.total_written == 1


async def test_provenance_is_persisted() -> None:
    sink = FakeSink()
    acc = Accumulator(sink, set(), clock=FrozenClock())
    await acc.process(
        [
            _vr(
                "abc123",
                flags={ValidationFlag.PLACEHOLDER_CALLSIGN},
                quality=PositionQuality.SUSPECT,
                distance_km=42.5,
                country="Syria",
            )
        ]
    )
    record = sink.records[0]
    assert record.position_quality == "suspect"
    assert record.flags == ("placeholder_callsign",)
    assert record.distance_km == 42.5
    assert record.country == "Syria"
    assert record.provenance_source == "interception"
    dumped = record.to_dict()
    assert dumped["validation"]["position_quality"] == "suspect"
    assert dumped["validation"]["distance_km"] == 42.5
    assert dumped["validation"]["country"] == "Syria"


async def test_no_position_record_is_kept_with_flag() -> None:
    """A position-less airframe is kept (flagged), never silently dropped."""
    sink = FakeSink()
    acc = Accumulator(sink, set(), clock=FrozenClock())
    result = await acc.process(
        [
            _vr(
                "abc123",
                quality=PositionQuality.NO_POSITION,
                lat=None,
                lon=None,
                distance_km=None,
                country=None,
            )
        ]
    )
    assert result.added == 1
    assert result.in_area == 0  # unknown position is not "in area"
    assert sink.records[0].to_dict()["validation"]["position_quality"] == "no_position"


async def test_on_new_hook_invoked_with_new_icaos() -> None:
    sink = FakeSink()
    seen: list[frozenset[str]] = []

    async def hook(icaos: frozenset[str]) -> None:
        seen.append(icaos)

    acc = Accumulator(sink, set(), clock=FrozenClock(), on_new=hook)
    await acc.process([_vr("aaaaaa"), _vr("bbbbbb")])
    await acc.process([_vr("aaaaaa")])  # all duplicates -> no hook call
    assert seen == [frozenset({"aaaaaa", "bbbbbb"})]
