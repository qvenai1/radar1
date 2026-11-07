"""Unit tests for the durable JSONL sink."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adsb_scraper.exceptions import WriteError
from adsb_scraper.models import AircraftRecord
from adsb_scraper.persistence import JsonlSink, scan_jsonl
from adsb_scraper.recovery import StateRecovery

from .conftest import FrozenClock


def _record(i: int) -> AircraftRecord:
    return AircraftRecord(icao=f"ac{i}", fetched_at="t", source="s", data={"i": i})


async def test_write_and_flush(tmp_path: Path) -> None:
    path = tmp_path / "aircraft.jsonl"
    async with JsonlSink(path, batch_size=100, flush_interval=999) as sink:
        for i in range(5):
            await sink.write(_record(i))
        assert sink.buffered == 5  # not yet flushed (batch not reached)
        await sink.flush()
        assert sink.total_written == 5
    valid, invalid = scan_jsonl(path)
    assert (valid, invalid) == (5, 0)


async def test_batch_autoflush(tmp_path: Path) -> None:
    path = tmp_path / "aircraft.jsonl"
    async with JsonlSink(path, batch_size=3, flush_interval=999) as sink:
        for i in range(3):
            await sink.write(_record(i))
        assert sink.buffered == 0
        assert sink.total_written == 3


async def test_close_flushes_remaining(tmp_path: Path) -> None:
    path = tmp_path / "aircraft.jsonl"
    sink = JsonlSink(path, batch_size=100, flush_interval=999)
    await sink.open()
    await sink.write(_record(1))
    await sink.aclose()
    assert scan_jsonl(path) == (1, 0)


# --------------------------------------------------------------------------- #
# Additive provenance schema (Requirement 5)
# --------------------------------------------------------------------------- #


async def test_provenance_round_trips_through_persistence(tmp_path: Path) -> None:
    path = tmp_path / "aircraft.jsonl"
    record = AircraftRecord(
        icao="a8e6e2",
        fetched_at="2026-07-14T00:00:00+00:00",
        source="adsbexchange",
        data={"icao": "a8e6e2", "latitude": 33.9, "longitude": 35.1},
        position_quality="confirmed",
        flags=("near_border",),
        distance_km=211.42,
        country=None,
        near="Lebanon",
        provenance_source="interception",
    )
    async with JsonlSink(path, batch_size=1) as sink:
        await sink.write(record)

    obj = json.loads(path.read_text().splitlines()[0])
    # Original fields untouched.
    assert obj["icao"] == "a8e6e2"
    assert obj["source"] == "adsbexchange"
    assert obj["data"]["latitude"] == 33.9
    # Provenance present under the additive block (now including country + near).
    assert obj["validation"] == {
        "position_quality": "confirmed",
        "flags": ["near_border"],
        "distance_km": 211.42,
        "country": None,
        "near": "Lebanon",
        "source": "interception",
    }


async def test_legacy_record_without_provenance_still_reads(tmp_path: Path) -> None:
    """A record written before this layer (no validation block) must stay valid
    and resumable — the additive extension breaks no existing reader."""
    path = tmp_path / "aircraft.jsonl"
    legacy = (
        '{"icao": "407e63", "fetched_at": "2026-07-12T16:29:14+00:00", '
        '"source": "adsbexchange", "data": {"icao": "407e63", "latitude": 34.6}}\n'
    )
    path.write_text(legacy, encoding="utf-8")

    assert scan_jsonl(path) == (1, 0)  # valid JSON
    obj = json.loads(path.read_text().splitlines()[0])
    assert "validation" not in obj  # legacy shape preserved
    # The resume reader keys on icao and is unaffected by the missing block.
    known = await StateRecovery(path, tmp_path / ".cache").load_completed()
    assert known == {"407e63"}


def test_record_without_provenance_omits_validation_block() -> None:
    dumped = AircraftRecord("aa", "t", "s", {"x": 1}).to_dict()
    assert "validation" not in dumped


async def test_write_after_close_raises(tmp_path: Path) -> None:
    sink = JsonlSink(tmp_path / "a.jsonl")
    await sink.open()
    await sink.aclose()
    with pytest.raises(WriteError):
        await sink.write(_record(1))


async def test_crash_repair_truncates_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "aircraft.jsonl"
    async with JsonlSink(path, batch_size=1) as sink:
        await sink.write(_record(0))
    # Simulate a crash mid-append (no trailing newline, invalid JSON).
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"icao": "partia')
    async with JsonlSink(path, batch_size=1):
        pass  # opening repairs
    assert scan_jsonl(path) == (1, 0)


async def test_flush_timer(tmp_path: Path) -> None:
    """The background timer flushes buffered records without a batch trigger."""
    import asyncio

    clock = FrozenClock()
    path = tmp_path / "aircraft.jsonl"
    async with JsonlSink(path, batch_size=100, flush_interval=0.05, clock=clock) as sink:
        await sink.write(_record(0))
        clock.advance(1.0)  # make the idle check exceed flush_interval
        for _ in range(20):
            if sink.total_written == 1:
                break
            await asyncio.sleep(0.02)
    assert sink.total_written == 1
