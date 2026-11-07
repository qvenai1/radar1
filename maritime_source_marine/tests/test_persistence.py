from __future__ import annotations

import json
from pathlib import Path

from mtscraper.models import VesselRecord
from mtscraper.persistence import CatalogueSink, DurableJsonlWriter, PositionSink


def _rec(
    ship_id: str,
    *,
    at: str = "2026-07-21T00:00:00+00:00",
    lat: float = 33.2,
    lon: float = 35.2,
    **data: object,
) -> VesselRecord:
    return VesselRecord(
        ship_id=ship_id,
        fetched_at=at,
        source="interception",
        data={"ship_id": ship_id, "latitude": lat, "longitude": lon, **data},
        distance_km=1.5,
        in_area=True,
    )


def test_durable_writer_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    with DurableJsonlWriter(path) as w:
        w.write({"a": 1})
        w.write({"b": 2})
    lines = path.read_text().splitlines()
    assert [json.loads(x) for x in lines] == [{"a": 1}, {"b": 2}]


def test_durable_writer_repairs_truncated_tail(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    # Simulate a crash mid-write: a complete line then a partial one, no newline.
    path.write_text('{"a":1}\n{"b":2')
    with DurableJsonlWriter(path) as w:
        w.write({"c": 3})
    objs = [json.loads(x) for x in path.read_text().splitlines()]
    assert objs == [{"a": 1}, {"c": 3}]


def test_catalogue_dedups_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "vessels.jsonl"
    sink = CatalogueSink(path)
    assert sink.add(_rec("100")) is True
    assert sink.add(_rec("100")) is False  # duplicate ship id
    assert sink.add(_rec("200")) is True
    assert sink.known_count == 2
    sink.close()
    assert len(path.read_text().splitlines()) == 2


def test_catalogue_reloads_known_ids_on_restart(tmp_path: Path) -> None:
    path = tmp_path / "vessels.jsonl"
    s1 = CatalogueSink(path)
    s1.add(_rec("100"))
    s1.close()
    s2 = CatalogueSink(path)
    assert s2.is_known("100")
    assert s2.add(_rec("100")) is False


def test_position_sink_suppresses_an_unchanged_report(tmp_path: Path) -> None:
    path = tmp_path / "positions.jsonl"
    sink = PositionSink(path)
    assert sink.add(_rec("100")) is True
    # Same fix re-served two minutes later: restates the line above, so no write.
    assert sink.add(_rec("100", at="2026-07-21T00:02:00+00:00")) is False
    sink.close()
    assert len(path.read_text().splitlines()) == 1
    assert (sink.written_count, sink.suppressed_count) == (1, 1)


def test_position_sink_writes_when_the_vessel_moves(tmp_path: Path) -> None:
    path = tmp_path / "positions.jsonl"
    sink = PositionSink(path)
    sink.add(_rec("100"))
    sink.add(_rec("100", at="2026-07-21T00:09:00+00:00", lat=33.3))
    sink.close()
    assert len(path.read_text().splitlines()) == 2


def test_position_sink_writes_when_only_the_verdict_changes(tmp_path: Path) -> None:
    """A vessel going stale is an event, even though its coordinates did not move."""
    path = tmp_path / "positions.jsonl"
    sink = PositionSink(path)
    fresh = _rec("100")
    stale = VesselRecord(
        ship_id="100",
        fetched_at="2026-07-21T00:05:00+00:00",
        source="interception",
        data=dict(fresh.data),
        distance_km=1.5,
        in_area=True,
        position_quality="suspect",
        flags=("stale_position",),
    )
    sink.add(fresh)
    assert sink.add(stale) is True
    sink.close()
    assert len(path.read_text().splitlines()) == 2


def test_position_sink_ignores_the_ageing_elapsed_counter(tmp_path: Path) -> None:
    """elapsed_min ticks up on every poll; treating it as new would defeat the point."""
    path = tmp_path / "positions.jsonl"
    sink = PositionSink(path)
    sink.add(_rec("100", elapsed_min=2.0))
    assert sink.add(_rec("100", at="2026-07-21T00:02:00+00:00", elapsed_min=4.0)) is False
    sink.close()
    assert len(path.read_text().splitlines()) == 1


def test_position_sink_heartbeats_an_idle_vessel(tmp_path: Path) -> None:
    """A vessel moored for hours must keep leaving evidence that it is still there."""
    path = tmp_path / "positions.jsonl"
    sink = PositionSink(path, heartbeat_s=3600.0)
    sink.add(_rec("100"))
    assert sink.add(_rec("100", at="2026-07-21T00:30:00+00:00")) is False
    assert sink.add(_rec("100", at="2026-07-21T01:00:00+00:00")) is True
    sink.close()
    assert len(path.read_text().splitlines()) == 2


def test_position_sink_heartbeat_zero_stores_every_observation(tmp_path: Path) -> None:
    path = tmp_path / "positions.jsonl"
    sink = PositionSink(path, heartbeat_s=0.0)
    sink.add(_rec("100"))
    sink.add(_rec("100", at="2026-07-21T00:02:00+00:00"))
    sink.close()
    assert len(path.read_text().splitlines()) == 2


def test_position_sink_tracks_vessels_independently(tmp_path: Path) -> None:
    path = tmp_path / "positions.jsonl"
    sink = PositionSink(path)
    sink.add(_rec("100"))
    sink.add(_rec("200"))
    assert sink.add(_rec("100", at="2026-07-21T00:02:00+00:00")) is False
    assert sink.add(_rec("200", at="2026-07-21T00:02:00+00:00", lat=34.0)) is True
    sink.close()
    assert len(path.read_text().splitlines()) == 3


def test_position_sink_writes_records_with_unusable_timestamps(tmp_path: Path) -> None:
    """No parseable time means no heartbeat window, so never suppress on a guess."""
    path = tmp_path / "positions.jsonl"
    sink = PositionSink(path)
    assert sink.add(_rec("100", at="not-a-timestamp")) is True
    assert sink.add(_rec("100", at="not-a-timestamp")) is True
    sink.close()
    assert len(path.read_text().splitlines()) == 2
