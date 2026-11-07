from __future__ import annotations

from pathlib import Path

from mtscraper.accumulator import Accumulator
from mtscraper.area import BboxArea
from mtscraper.config import ValidationConfig
from mtscraper.models import GeoBounds, Vessel
from mtscraper.persistence import CatalogueSink, PositionSink
from mtscraper.validation import ValidatedRecord, VesselRecordValidator

# A tight box around the default centre.
BOUNDS = GeoBounds(north=34.0, south=32.4, east=36.0, west=34.4)
_VALIDATOR = VesselRecordValidator(area=BboxArea(BOUNDS), config=ValidationConfig())


def _acc(tmp_path: Path, *, persist_out: bool = False, positions: bool = True) -> Accumulator:
    cat = CatalogueSink(tmp_path / "vessels.jsonl")
    pos = PositionSink(tmp_path / "positions.jsonl") if positions else None
    return Accumulator(cat, pos, persist_out_of_area=persist_out)


def _rec(ship_id: str, lat: float | None, lon: float | None) -> ValidatedRecord:
    vessel = Vessel(ship_id=ship_id, latitude=lat, longitude=lon, shipname=f"S{ship_id}")
    return _VALIDATOR.validate(vessel, source="interception")


def test_in_area_vessel_is_catalogued(tmp_path: Path) -> None:
    res = _acc(tmp_path).accept([_rec("1", 33.2, 35.2)], "t")
    assert res.in_area == 1
    assert res.new_vessels == 1
    assert res.out_of_area == 0


def test_out_of_area_dropped_by_default(tmp_path: Path) -> None:
    res = _acc(tmp_path).accept([_rec("1", 10.0, 10.0)], "t")
    assert res.out_of_area == 1
    assert res.new_vessels == 0
    assert (tmp_path / "vessels.jsonl").read_text() == ""


def test_out_of_area_persisted_when_enabled(tmp_path: Path) -> None:
    res = _acc(tmp_path, persist_out=True).accept([_rec("1", 10.0, 10.0)], "t")
    assert res.out_of_area == 1
    assert res.new_vessels == 1
    assert len((tmp_path / "vessels.jsonl").read_text().splitlines()) == 1


def test_duplicate_ship_id_across_cycles(tmp_path: Path) -> None:
    acc = _acc(tmp_path)
    acc.accept([_rec("1", 33.2, 35.2)], "t1")
    res = acc.accept([_rec("1", 33.25, 35.25)], "t2")
    assert res.new_vessels == 0
    assert res.duplicates == 1
    assert len((tmp_path / "vessels.jsonl").read_text().splitlines()) == 1
    assert len((tmp_path / "positions.jsonl").read_text().splitlines()) == 2


def test_overlapping_tiles_dedupe_within_cycle(tmp_path: Path) -> None:
    res = _acc(tmp_path).accept([_rec("1", 33.2, 35.2), _rec("1", 33.2, 35.2)], "t")
    assert res.new_vessels == 1
    assert len((tmp_path / "positions.jsonl").read_text().splitlines()) == 1


def test_invalid_identity_counted_not_catalogued(tmp_path: Path) -> None:
    res = _acc(tmp_path).accept([_rec("0", 33.2, 35.2)], "t")  # "0" is not a valid ship id
    assert res.invalid_identity == 1
    assert res.new_vessels == 0


def test_persisted_record_has_validation_block(tmp_path: Path) -> None:
    import json

    _acc(tmp_path).accept([_rec("1", 33.2, 35.2)], "t")
    rec = json.loads((tmp_path / "vessels.jsonl").read_text().splitlines()[0])
    assert rec["validation"]["position_quality"] == "confirmed"
    assert "distance_km" in rec
