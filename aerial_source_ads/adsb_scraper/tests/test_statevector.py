"""Unit tests for the StateVector-shaped time-series producer (P1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from adsb_scraper.accumulator import Accumulator
from adsb_scraper.fetcher import (
    AdsbExchangeApiStrategy,
    BrowserEvaluateStrategy,
    InterceptionStrategy,
    OpenSkyRestStrategy,
)
from adsb_scraper.models import Aircraft, GeoBounds
from adsb_scraper.persistence import scan_jsonl
from adsb_scraper.statevector import (
    ADSBEXCHANGE_SOURCES,
    STATEVECTOR_SOURCE,
    StateVectorSink,
    is_adsbexchange_source,
    to_state_vector,
)
from adsb_scraper.validation import (
    AircraftRecordValidator,
    PositionQuality,
    ValidatedRecord,
    ValidationConfig,
    ValidationFlag,
)

from .conftest import FakeBrowser, FakeSink, FrozenClock, make_area_of_interest


def _vr(
    icao: str | None = "a8e6e2",
    *,
    altitude: float | None = 35000.0,
    ground_speed: float | None = 450.0,
    track: float | None = 91.0,
    callsign: str | None = "SVA1",
    country: str | None = "Syria",
    near: str | None = None,
    flags: set[ValidationFlag] | None = None,
    quality: PositionQuality = PositionQuality.CONFIRMED,
    distance_km: float | None = 12.3,
    source: str = "interception",
    raw: dict[str, Any] | None = None,
) -> ValidatedRecord:
    return ValidatedRecord(
        icao=icao,
        callsign=callsign,
        latitude=33.5,
        longitude=36.3,
        altitude=altitude,
        ground_speed=ground_speed,
        track=track,
        position_quality=quality,
        flags=frozenset(flags or set()),
        distance_km=distance_km,
        country=country,
        near=near,
        source=source,
        raw=MappingProxyType(raw or {}),
    )


# --------------------------------------------------------------------------- #
# Unit-exact conversions (the correctness traps)
# --------------------------------------------------------------------------- #


def test_altitude_feet_to_metres() -> None:
    sv = to_state_vector(_vr(altitude=35000.0), 1_700_000_000)
    assert sv["baro_altitude"] == pytest.approx(10668.0)  # 35000 * 0.3048


def test_velocity_knots_to_mps() -> None:
    sv = to_state_vector(_vr(ground_speed=450.0), 1_700_000_000)
    assert sv["velocity"] == pytest.approx(231.5, abs=0.001)  # 450 * 0.514444


def test_true_track_and_position_unchanged() -> None:
    sv = to_state_vector(_vr(track=272.7), 1_700_000_000)
    assert sv["true_track"] == 272.7
    assert sv["latitude"] == 33.5 and sv["longitude"] == 36.3


def test_null_telemetry_stays_null() -> None:
    sv = to_state_vector(_vr(altitude=None, ground_speed=None, track=None), 1_700_000_000)
    assert sv["baro_altitude"] is None
    assert sv["velocity"] is None
    assert sv["true_track"] is None
    assert sv["on_ground"] is False  # NOT NULL — never zero/false-filled falsely


# --------------------------------------------------------------------------- #
# "ground" sentinel
# --------------------------------------------------------------------------- #


def test_ground_sentinel_sets_on_ground_and_nulls_baro() -> None:
    # The parser nulls altitude but the "ground" sentinel survives in raw.
    sv = to_state_vector(_vr(altitude=None, raw={"alt_baro": "ground"}), 1_700_000_000)
    assert sv["on_ground"] is True
    assert sv["baro_altitude"] is None


def test_airborne_is_not_on_ground() -> None:
    sv = to_state_vector(_vr(altitude=1000.0, raw={"alt_baro": 1000}), 1_700_000_000)
    assert sv["on_ground"] is False
    assert sv["baro_altitude"] == pytest.approx(304.8)


# --------------------------------------------------------------------------- #
# snapshot_time / ts contract
# --------------------------------------------------------------------------- #


def test_snapshot_time_is_int_and_ts_matches_to_timestamp() -> None:
    snapshot_time = 1_783_858_565
    sv = to_state_vector(_vr(), snapshot_time)
    assert isinstance(sv["snapshot_time"], int)
    assert sv["snapshot_time"] == snapshot_time
    # scraplan CHECK: ts = to_timestamp(snapshot_time). Our ts is the ISO-8601 UTC
    # of exactly that integer second, so the round-trip is lossless.
    assert sv["ts"] == datetime.fromtimestamp(snapshot_time, tz=UTC).isoformat()
    assert int(datetime.fromisoformat(sv["ts"]).timestamp()) == snapshot_time


# --------------------------------------------------------------------------- #
# Geo-classification provenance carries through
# --------------------------------------------------------------------------- #


def test_geo_columns_carry_through() -> None:
    sv = to_state_vector(
        _vr(
            country=None,
            near="Lebanon",
            quality=PositionQuality.SUSPECT,
            flags={ValidationFlag.NEAR_BORDER, ValidationFlag.QUANTIZED_POSITION},
            distance_km=42.5,
        ),
        1_700_000_000,
    )
    assert sv["country"] is None
    assert sv["near_country"] == "Lebanon"
    assert sv["position_quality"] == "suspect"
    assert sv["validation_flags"] == ["near_border", "quantized_position"]  # sorted
    assert sv["distance_km"] == 42.5
    assert sv["geo_source"] == STATEVECTOR_SOURCE == "adsbexchange"
    assert sv["source"] == "adsbexchange"


def test_shape_matches_scraplan_ingest_keys() -> None:
    """Every key scraplan's ingest.parse_record reads must be present."""
    sv = to_state_vector(_vr(), 1_700_000_000)
    required = {
        "snapshot_time",
        "icao24",
        "callsign",
        "origin_country",
        "time_position",
        "last_contact",
        "longitude",
        "latitude",
        "baro_altitude",
        "geo_altitude",
        "on_ground",
        "velocity",
        "true_track",
        "vertical_rate",
        "squawk",
        "spi",
        "position_source",
        "category",
        "fetched_at",
        "source",
        "country",
        "near_country",
        "position_quality",
        "validation_flags",
        "distance_km",
        "geo_source",
    }
    assert required <= set(sv)
    assert sv["icao24"] == "a8e6e2"  # icao -> icao24


# --------------------------------------------------------------------------- #
# Source restriction: OpenSky is NEVER emitted
# --------------------------------------------------------------------------- #


def test_source_whitelist_matches_strategy_names() -> None:
    """Guards against drift between the whitelist and the fetch strategy names."""
    browser = FakeBrowser()
    bounds = GeoBounds(1, 0, 1, 0)
    adsbx = {
        BrowserEvaluateStrategy(browser, "/x").name,
        InterceptionStrategy(browser).name,
        AdsbExchangeApiStrategy(bounds, "key").name,
    }
    assert adsbx == ADSBEXCHANGE_SOURCES
    assert OpenSkyRestStrategy(bounds).name not in ADSBEXCHANGE_SOURCES
    assert not is_adsbexchange_source(OpenSkyRestStrategy(bounds).name)


async def test_opensky_record_not_written_adsbx_is(tmp_path: Path) -> None:
    path = tmp_path / "state_vectors.jsonl"
    async with StateVectorSink(path, batch_size=1, clock=FrozenClock()) as sink:
        written = await sink.write_snapshot(
            [
                _vr("aaaaaa", source="opensky_rest"),  # must NOT be written
                _vr("bbbbbb", source="interception"),  # must be written
                _vr("cccccc", source="adsbx_api"),  # must be written
            ],
            1_700_000_000,
        )
    assert written == 2
    icaos = [json.loads(line)["icao24"] for line in path.read_text().splitlines()]
    assert icaos == ["bbbbbb", "cccccc"]
    assert "aaaaaa" not in icaos


async def test_invalid_identity_not_written(tmp_path: Path) -> None:
    path = tmp_path / "state_vectors.jsonl"
    async with StateVectorSink(path, batch_size=1, clock=FrozenClock()) as sink:
        written = await sink.write_snapshot(
            [_vr(None, flags={ValidationFlag.INVALID_IDENTITY}), _vr("bbbbbb")], 1_700_000_000
        )
    assert written == 1
    assert scan_jsonl(path) == (1, 0)


# --------------------------------------------------------------------------- #
# No dedup here (time-series) vs dedup in aircraft.jsonl (catalogue)
# --------------------------------------------------------------------------- #


async def test_timeseries_no_dedup_while_catalogue_dedups(tmp_path: Path) -> None:
    """The same airframe over two cycles: catalogue keeps 1 (dedup), the
    time-series keeps every observation (no dedup) — proven side by side."""
    catalogue = FakeSink()
    accumulator = Accumulator(catalogue, set(), clock=FrozenClock())
    sv_path = tmp_path / "state_vectors.jsonl"

    async with StateVectorSink(sv_path, batch_size=1, clock=FrozenClock()) as ts_sink:
        rec = _vr("abcdef")
        for snapshot_time in (1_700_000_000, 1_700_000_015, 1_700_000_030):
            await accumulator.process([rec])  # discovery catalogue (dedups)
            await ts_sink.write_snapshot([rec], snapshot_time)  # time-series (no dedup)

    assert catalogue.total_written == 1  # first-seen-wins
    assert scan_jsonl(sv_path) == (3, 0)  # one line per cycle


async def test_one_line_per_aircraft_per_cycle(tmp_path: Path) -> None:
    path = tmp_path / "state_vectors.jsonl"
    async with StateVectorSink(path, batch_size=100, clock=FrozenClock()) as sink:
        batch = [_vr(f"00000{i}") for i in range(5)]
        await sink.write_snapshot(batch, 1_700_000_000)
        await sink.write_snapshot(batch, 1_700_000_015)  # same 5 aircraft, next cycle
    assert scan_jsonl(path) == (10, 0)  # 5 aircraft x 2 cycles, no dedup


# --------------------------------------------------------------------------- #
# Durability parity with the existing sink
# --------------------------------------------------------------------------- #


async def test_crash_repair_of_trailing_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "state_vectors.jsonl"
    async with StateVectorSink(path, batch_size=1, clock=FrozenClock()) as sink:
        await sink.write_snapshot([_vr("aaaaaa")], 1_700_000_000)
    # Simulate a crash mid-append (no trailing newline, invalid JSON tail).
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"icao24": "partia')
    async with StateVectorSink(path, batch_size=1, clock=FrozenClock()) as sink:
        await sink.write_snapshot([_vr("bbbbbb")], 1_700_000_015)
    assert scan_jsonl(path) == (2, 0)  # partial repaired, both good lines intact


# --------------------------------------------------------------------------- #
# Sampling frame — the time-series is bounded to the AOI (DISK-2)
#
# These go through the REAL validator and the REAL committed AOI asset, so the
# INSIDE / NEAR / OUTSIDE decision is the production one, never a hand-set flag.
# Reference points are the same three test_validation.py uses.
# --------------------------------------------------------------------------- #

_FRAME_AOI = make_area_of_interest(buffer_km=50.0)
_DAMASCUS = (33.5123, 36.2912)   # INSIDE Syria
_NEAR_LEBANON = (33.90, 35.10)   # ~34 km off the coast -> NEAR
_CAIRO = (30.04, 31.24)          # far OUTSIDE, but inside the fetch envelope


def _frame_validator() -> AircraftRecordValidator:
    return AircraftRecordValidator(area=_FRAME_AOI, config=ValidationConfig())


def _validated_at(icao: str, point: tuple[float, float] | None) -> ValidatedRecord:
    """Validate one aircraft at a known position through the production path."""
    kwargs: dict[str, Any] = {"icao": icao}
    if point is not None:
        kwargs["latitude"], kwargs["longitude"] = point
    return _frame_validator().validate(Aircraft(**kwargs), source="interception")


def test_real_aoi_classifies_the_three_reference_points() -> None:
    """Guard the premise: if these drift, the frame tests below mean nothing."""
    inside = _validated_at("a00001", _DAMASCUS)
    near = _validated_at("a00002", _NEAR_LEBANON)
    outside = _validated_at("a00003", _CAIRO)
    assert inside.country == "Syria" and not inside.outside_area
    assert near.is_near and not near.outside_area
    assert outside.outside_area and outside.country is None


async def test_outside_area_is_never_written_to_the_time_series(tmp_path: Path) -> None:
    path = tmp_path / "state_vectors.jsonl"
    async with StateVectorSink(path, batch_size=1, clock=FrozenClock()) as sink:
        written = await sink.write_snapshot(
            [
                _validated_at("a00001", _DAMASCUS),
                _validated_at("a00002", _NEAR_LEBANON),
                _validated_at("a00003", _CAIRO),
            ],
            1_700_000_000,
        )
        assert written == 2, "INSIDE + NEAR are written, OUTSIDE is not"
        assert sink.skipped_out_of_area == 1
    assert scan_jsonl(path) == (2, 0)
    icaos = {json.loads(line)["icao24"] for line in path.read_text().splitlines()}
    assert icaos == {"a00001", "a00002"}, "Cairo must be absent"


async def test_near_border_is_kept_the_neighborhood_is_the_point(tmp_path: Path) -> None:
    path = tmp_path / "state_vectors.jsonl"
    async with StateVectorSink(path, batch_size=1, clock=FrozenClock()) as sink:
        written = await sink.write_snapshot([_validated_at("a00002", _NEAR_LEBANON)], 1_700_000_000)
    assert written == 1
    assert scan_jsonl(path) == (1, 0)


async def test_positionless_record_is_kept_unclassified_is_not_outside(tmp_path: Path) -> None:
    """Parity with the catalogue: no position means unclassified, not excluded."""
    path = tmp_path / "state_vectors.jsonl"
    rec = _validated_at("a00004", None)
    assert not rec.outside_area and not rec.has_position
    async with StateVectorSink(path, batch_size=1, clock=FrozenClock()) as sink:
        written = await sink.write_snapshot([rec], 1_700_000_000)
    assert written == 1


async def test_persist_out_of_area_restores_the_global_time_series(tmp_path: Path) -> None:
    """The override is the documented escape hatch, and it must still work."""
    path = tmp_path / "state_vectors.jsonl"
    async with StateVectorSink(
        path, batch_size=1, persist_out_of_area=True, clock=FrozenClock()
    ) as sink:
        written = await sink.write_snapshot(
            [_validated_at("a00001", _DAMASCUS), _validated_at("a00003", _CAIRO)],
            1_700_000_000,
        )
        assert written == 2
        assert sink.skipped_out_of_area == 0
    assert scan_jsonl(path) == (2, 0)


async def test_frame_matches_the_catalogue_policy(tmp_path: Path) -> None:
    """The sink and the accumulator must agree on the SAME record set.

    This is the invariant that makes the change a MOVE of one policy, not a
    second policy that can drift away from the first.
    """
    batch = [
        _validated_at("a00001", _DAMASCUS),
        _validated_at("a00002", _NEAR_LEBANON),
        _validated_at("a00003", _CAIRO),
    ]
    cat_sink = FakeSink()
    accumulator = Accumulator(cat_sink, set(), clock=FrozenClock())
    result = await accumulator.process(batch)

    path = tmp_path / "state_vectors.jsonl"
    async with StateVectorSink(path, batch_size=1, clock=FrozenClock()) as sink:
        written = await sink.write_snapshot(batch, 1_700_000_000)

    assert result.skipped_out_of_area == sink.skipped_out_of_area == 1
    assert result.added == written == 2
