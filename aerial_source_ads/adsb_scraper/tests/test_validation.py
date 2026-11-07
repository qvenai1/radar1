"""Unit tests for the data validity / normalization layer."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adsb_scraper.config import ValidationConfig
from adsb_scraper.models import Aircraft
from adsb_scraper.validation import (
    AircraftRecordValidator,
    CollectionGap,
    CollectionGapDetector,
    JsonlGapJournal,
    PositionQuality,
    ValidationFlag,
)

from .conftest import FakeGapJournal, make_area_of_interest

_AOI = make_area_of_interest(buffer_km=50.0)

# Damascus (INSIDE Syria); a sea point ~34 km off Lebanon (NEAR); Cairo (far
# OUTSIDE, yet still inside the fetch box — the fetch/filter distinction).
IN_AREA = (33.5123, 36.2912)
NEAR_PT = (33.90, 35.10)
OUT_OF_AREA = (30.04, 31.24)


def _validator(**overrides: float | bool) -> AircraftRecordValidator:
    return AircraftRecordValidator(
        area=_AOI,
        config=ValidationConfig(**overrides),  # type: ignore[arg-type]
    )


def _ac(**kwargs: object) -> Aircraft:
    # Default position is inside the AOI so identity/callsign tests aren't
    # perturbed by an OUTSIDE_AREA flag.
    base: dict[str, object] = {"icao": "a8e6e2", "latitude": IN_AREA[0], "longitude": IN_AREA[1]}
    base.update(kwargs)
    return Aircraft(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Requirement 2 — ICAO identity
# --------------------------------------------------------------------------- #


def test_valid_icao_is_lowercased_and_kept() -> None:
    rec = _validator().validate(_ac(icao="A8E6E2"), source="s")
    assert rec.icao == "a8e6e2"
    assert rec.has_valid_identity
    assert ValidationFlag.INVALID_IDENTITY not in rec.flags


@pytest.mark.parametrize("bad", ["a8e6e", "a8e6e22", "zzzzzz", "abc12g", "  ", ""])
def test_malformed_icao_flags_invalid_identity(bad: str) -> None:
    rec = _validator().validate(_ac(icao=bad, latitude=24.8, longitude=46.7), source="s")
    assert rec.icao is None
    assert not rec.has_valid_identity
    assert ValidationFlag.INVALID_IDENTITY in rec.flags


# --------------------------------------------------------------------------- #
# Requirement 2 — placeholder callsigns
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("placeholder", ["@@@@@@@@", "@", "   ", ""])
def test_placeholder_callsign_normalized_to_null(placeholder: str) -> None:
    rec = _validator().validate(_ac(callsign=placeholder), source="s")
    assert rec.callsign is None
    assert ValidationFlag.PLACEHOLDER_CALLSIGN in rec.flags


def test_real_callsign_is_kept_and_trimmed() -> None:
    rec = _validator().validate(_ac(callsign="SVA123  "), source="s")
    assert rec.callsign == "SVA123"
    assert ValidationFlag.PLACEHOLDER_CALLSIGN not in rec.flags


def test_missing_callsign_is_not_a_placeholder() -> None:
    rec = _validator().validate(_ac(callsign=None), source="s")
    assert rec.callsign is None
    assert ValidationFlag.PLACEHOLDER_CALLSIGN not in rec.flags


# --------------------------------------------------------------------------- #
# Requirement 1 — three-state area filter (INSIDE / NEAR / OUTSIDE)
# --------------------------------------------------------------------------- #


def test_inside_country_labelled_with_country() -> None:
    lat, lon = IN_AREA
    rec = _validator().validate(_ac(latitude=lat, longitude=lon), source="s")
    assert rec.country == "Syria"
    assert rec.near is None
    assert not (rec.flags & {ValidationFlag.OUTSIDE_AREA, ValidationFlag.NEAR_BORDER})
    assert rec.in_area


def test_near_border_flagged_with_nearest_country_and_kept() -> None:
    lat, lon = NEAR_PT
    rec = _validator().validate(_ac(latitude=lat, longitude=lon), source="s")
    assert ValidationFlag.NEAR_BORDER in rec.flags
    assert ValidationFlag.OUTSIDE_AREA not in rec.flags
    assert rec.country is None
    assert rec.near == "Lebanon"
    assert rec.is_near
    assert rec.in_area  # NEAR is part of the neighborhood -> persisted by default


def test_outside_flagged_not_dropped() -> None:
    lat, lon = OUT_OF_AREA
    rec = _validator().validate(_ac(latitude=lat, longitude=lon), source="s")
    assert rec.country is None and rec.near is None
    assert ValidationFlag.OUTSIDE_AREA in rec.flags
    assert not rec.in_area
    # Data is preserved, only flagged; distance provenance still computed.
    assert rec.latitude == lat and rec.longitude == lon
    assert rec.distance_km is not None


def test_fetch_box_contains_outside_point_proving_box_is_not_the_filter() -> None:
    """Cairo sits inside the buffered fetch box yet beyond the border buffer, so
    it is fetched but flagged OUTSIDE_AREA — the box is not the interest filter."""
    lat, lon = OUT_OF_AREA
    box = _AOI.bounding_box()
    assert box.south <= lat <= box.north and box.west <= lon <= box.east  # in fetch box
    rec = _validator().validate(_ac(latitude=lat, longitude=lon), source="s")
    assert ValidationFlag.OUTSIDE_AREA in rec.flags


# --------------------------------------------------------------------------- #
# Requirement 3 — position quality classification
# --------------------------------------------------------------------------- #


def test_quality_confirmed() -> None:
    lat, lon = IN_AREA
    rec = _validator().validate(
        _ac(latitude=lat, longitude=lon, altitude=35000, ground_speed=450), source="s"
    )
    assert rec.position_quality is PositionQuality.CONFIRMED
    assert rec.flags == frozenset()  # in-area, no plausibility issues


def test_quality_no_position() -> None:
    rec = _validator().validate(_ac(latitude=None, longitude=None), source="s")
    assert rec.position_quality is PositionQuality.NO_POSITION
    assert rec.distance_km is None
    assert rec.country is None


def test_quality_suspect_when_a_plausibility_flag_fires() -> None:
    lat, lon = IN_AREA
    rec = _validator().validate(
        _ac(latitude=lat, longitude=lon, altitude=0, ground_speed=300), source="s"
    )
    assert rec.position_quality is PositionQuality.SUSPECT


# --------------------------------------------------------------------------- #
# Requirement 3 — plausibility flags (both directions)
# --------------------------------------------------------------------------- #

_CLEAN_LAT, _CLEAN_LON = IN_AREA  # multi-decimal, inside Syria
_QUANTIZED_LAT, _QUANTIZED_LON = 34.0, 36.0  # one-decimal, still inside Syria


def test_zero_alt_airborne_fires_and_clean_does_not() -> None:
    fires = _validator().validate(
        _ac(latitude=_CLEAN_LAT, longitude=_CLEAN_LON, altitude=0, ground_speed=300), source="s"
    )
    assert ValidationFlag.ZERO_ALT_AIRBORNE in fires.flags
    clean = _validator().validate(
        _ac(latitude=_CLEAN_LAT, longitude=_CLEAN_LON, altitude=35000, ground_speed=300), source="s"
    )
    assert ValidationFlag.ZERO_ALT_AIRBORNE not in clean.flags


def test_quantized_position_fires_and_clean_does_not() -> None:
    fires = _validator().validate(
        _ac(latitude=_QUANTIZED_LAT, longitude=_QUANTIZED_LON, altitude=30000, ground_speed=371),
        source="s",
    )
    assert ValidationFlag.QUANTIZED_POSITION in fires.flags
    # Genuine multi-decimal fix at the same speed is not quantized.
    clean = _validator().validate(
        _ac(latitude=_CLEAN_LAT, longitude=_CLEAN_LON, altitude=30000, ground_speed=371), source="s"
    )
    assert ValidationFlag.QUANTIZED_POSITION not in clean.flags
    # Quantized but slow is not flagged (parked/taxiing coarse position is fine).
    slow = _validator().validate(
        _ac(latitude=_QUANTIZED_LAT, longitude=_QUANTIZED_LON, altitude=0, ground_speed=5),
        source="s",
    )
    assert ValidationFlag.QUANTIZED_POSITION not in slow.flags


def test_implausible_kinematics_fires_and_clean_does_not() -> None:
    fast = _validator().validate(
        _ac(latitude=_CLEAN_LAT, longitude=_CLEAN_LON, ground_speed=900), source="s"
    )
    assert ValidationFlag.IMPLAUSIBLE_KINEMATICS in fast.flags
    high = _validator().validate(
        _ac(latitude=_CLEAN_LAT, longitude=_CLEAN_LON, altitude=70000), source="s"
    )
    assert ValidationFlag.IMPLAUSIBLE_KINEMATICS in high.flags
    clean = _validator().validate(
        _ac(latitude=_CLEAN_LAT, longitude=_CLEAN_LON, altitude=38000, ground_speed=480), source="s"
    )
    assert ValidationFlag.IMPLAUSIBLE_KINEMATICS not in clean.flags


def test_configurable_plausibility_thresholds() -> None:
    strict = _validator(plausibility_max_gs_kt=400.0)
    rec = strict.validate(
        _ac(latitude=_CLEAN_LAT, longitude=_CLEAN_LON, ground_speed=450), source="s"
    )
    assert ValidationFlag.IMPLAUSIBLE_KINEMATICS in rec.flags


# --------------------------------------------------------------------------- #
# Requirement 5 — provenance
# --------------------------------------------------------------------------- #


def test_provenance_carries_country_near_source_flags_distance() -> None:
    lat, lon = OUT_OF_AREA
    rec = _validator().validate(
        _ac(latitude=lat, longitude=lon, callsign="@@@@"), source="interception"
    )
    prov = rec.provenance()
    assert prov["source"] == "interception"
    assert prov["country"] is None and prov["near"] is None
    assert "outside_area" in prov["flags"]
    assert "placeholder_callsign" in prov["flags"]
    assert isinstance(prov["distance_km"], float)


def test_provenance_country_set_for_inside_record() -> None:
    lat, lon = IN_AREA
    rec = _validator().validate(_ac(latitude=lat, longitude=lon), source="interception")
    prov = rec.provenance()
    assert prov["country"] == "Syria" and prov["near"] is None


def test_provenance_near_set_for_near_record() -> None:
    lat, lon = NEAR_PT
    rec = _validator().validate(_ac(latitude=lat, longitude=lon), source="interception")
    prov = rec.provenance()
    assert prov["country"] is None and prov["near"] == "Lebanon"
    assert "near_border" in prov["flags"]


# --------------------------------------------------------------------------- #
# Requirement 4 — collection gap detection (observational)
# --------------------------------------------------------------------------- #


async def test_gap_detector_records_gap_above_threshold() -> None:
    journal = FakeGapJournal(seed=datetime(2026, 7, 12, 20, 0, tzinfo=UTC))
    detector = CollectionGapDetector(journal, threshold_s=1800.0)
    # ~13h later — the confirmed power-outage gap.
    now = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)
    gap = await detector.observe(now)
    assert gap is not None
    assert gap.duration_s == pytest.approx(13 * 3600, abs=1)
    assert len(journal.gaps) == 1
    assert journal.gaps[0].to_dict()["type"] == "collection_gap"


async def test_gap_detector_silent_below_threshold() -> None:
    start = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    journal = FakeGapJournal(seed=start)
    detector = CollectionGapDetector(journal, threshold_s=1800.0)
    gap = await detector.observe(start + timedelta(seconds=20))  # a normal cycle
    assert gap is None
    assert journal.gaps == []  # no marker written below threshold
    assert len(journal.cycles) == 1  # but the cursor still advances


async def test_gap_detector_first_cycle_no_previous_is_silent() -> None:
    journal = FakeGapJournal(seed=None)  # cold start, no prior cursor
    detector = CollectionGapDetector(journal, threshold_s=1800.0)
    gap = await detector.observe(datetime(2026, 7, 14, 12, 0, tzinfo=UTC))
    assert gap is None
    assert journal.gaps == []


async def test_jsonl_gap_journal_persists_marker_and_cursor_across_restart(tmp_path: Path) -> None:
    """The gap journal is durable and detects a gap that spanned a restart."""
    gaps_path = tmp_path / "collection_gaps.jsonl"
    cursor_path = tmp_path / ".collection_state.json"

    # Run 1: a cycle happens, writing the cursor. No gaps yet.
    journal1 = JsonlGapJournal(gaps_path, cursor_path, fsync=True)
    det1 = CollectionGapDetector(journal1, threshold_s=1800.0)
    before_outage = datetime(2026, 7, 12, 20, 0, tzinfo=UTC)
    assert await det1.observe(before_outage) is None
    assert cursor_path.exists()
    assert not gaps_path.exists()  # nothing recorded below threshold / first cycle

    # Simulated power loss, then Run 2 (fresh detector) resumes 13h later.
    journal2 = JsonlGapJournal(gaps_path, cursor_path, fsync=True)
    det2 = CollectionGapDetector(journal2, threshold_s=1800.0)
    after_restart = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)
    gap = await det2.observe(after_restart)

    assert gap is not None  # the restart-spanning gap is detected from the cursor
    lines = [json.loads(line) for line in gaps_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["type"] == "collection_gap"
    assert lines[0]["start"] == before_outage.isoformat()
    assert lines[0]["end"] == after_restart.isoformat()
    assert lines[0]["duration_s"] == pytest.approx(13 * 3600, abs=1)


def test_collection_gap_to_dict_shape() -> None:
    gap = CollectionGap(
        started_at="2026-07-12T20:00:00+00:00",
        ended_at="2026-07-13T09:00:00+00:00",
        duration_s=46800.0,
    )
    d = gap.to_dict()
    assert d == {
        "type": "collection_gap",
        "start": "2026-07-12T20:00:00+00:00",
        "end": "2026-07-13T09:00:00+00:00",
        "duration_s": 46800.0,
    }
