"""Integration tests driving whole cycles with in-memory fakes.

No Playwright, no network, no wall clock — the Protocol boundaries let the full
fetch→decode→parse→filter→accumulate pipeline run against doubles. These tests
also lock in the strategy-fallback contract: a bad primary source (403 / HTML /
undecodable) must fall through to interception *within the same cycle*, and only
a total failure of both sources may trigger diagnostics and recovery.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adsb_scraper.accumulator import Accumulator
from adsb_scraper.config import AppConfig, DiagnosticsConfig, TimingConfig
from adsb_scraper.diagnostics import DiagnosticsRecorder, MetricsCollector
from adsb_scraper.fetcher import BrowserEvaluateStrategy, Fetcher, InterceptionStrategy
from adsb_scraper.geo import GeoService
from adsb_scraper.orchestrator import ScraperOrchestrator
from adsb_scraper.parser import AdaptiveAircraftParser
from adsb_scraper.persistence import scan_jsonl
from adsb_scraper.recovery import RecoveryPolicy
from adsb_scraper.statevector import StateVectorSink
from adsb_scraper.utils import BackoffPolicy, MemoryProbe
from adsb_scraper.validation import AircraftRecordValidator, CollectionGapDetector

from .conftest import (
    FakeBrowser,
    FakeGapJournal,
    FakeSink,
    FrozenClock,
    make_area_of_interest,
)

# An aircraft over Damascus (inside Syria); a valid 6-hex ICAO, multi-decimal
# coordinates so it is a CONFIRMED (not quantized) fix.
IN_AREA = {"hex": "aa0001", "lat": 33.5123, "lon": 36.2912, "track": 10, "gs": 400}
_AOI = make_area_of_interest(buffer_km=50.0)


def _make_orchestrator(
    browser: FakeBrowser,
    sink: FakeSink,
    tmp_path: Path,
    state_vector_sink: StateVectorSink | None = None,
) -> tuple[ScraperOrchestrator, RecoveryPolicy, MetricsCollector]:
    geo = GeoService()
    cfg = AppConfig(
        output_dir=tmp_path,
        timing=TimingConfig(fetch_interval=1.0, max_retries=1),
        diagnostics=DiagnosticsConfig(enabled=True),
    )
    main_bounds = _AOI.bounding_box()
    cells = [main_bounds]
    clock = FrozenClock()
    recovery = RecoveryPolicy(cfg.recovery)
    metrics = MetricsCollector(clock=clock)
    accumulator = Accumulator(sink, set(), clock=clock)
    validator = AircraftRecordValidator(area=_AOI, config=cfg.validation, geo=geo)
    gap_detector = CollectionGapDetector(
        FakeGapJournal(), threshold_s=cfg.validation.gap_threshold_s
    )
    # Production-shaped chain: browser evaluate first, interception as fallback.
    fetcher = Fetcher(
        [
            BrowserEvaluateStrategy(browser, "/data/aircraft.json"),
            InterceptionStrategy(browser),
        ]
    )
    orchestrator = ScraperOrchestrator(
        config=cfg,
        browser=browser,
        fetcher=fetcher,
        parser=AdaptiveAircraftParser(),
        validator=validator,
        accumulator=accumulator,
        gap_detector=gap_detector,
        geo=geo,
        diagnostics=DiagnosticsRecorder(cfg.diagnostics, cfg.diagnostics_dir),
        metrics=metrics,
        recovery=recovery,
        main_bounds=main_bounds,
        cells=cells,
        state_vector_sink=state_vector_sink,
        backoff=BackoffPolicy(base=1.01, max_wait=0.01, jitter=0.0),  # fast retries
        clock=clock,
        memory=MemoryProbe(),
    )
    return orchestrator, recovery, metrics


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


async def test_productive_cycle_collects_in_area(tmp_path: Path) -> None:
    payload = {
        "aircraft": [
            IN_AREA,  # INSIDE Syria
            {"hex": "aa0002", "lat": 30.04, "lon": 31.24, "track": 20},  # Cairo: OUTSIDE
            {"hex": "aa0003", "track": 30},  # no position
            {"hex": "aa0004", "lat": 33.90, "lon": 35.10, "track": 40, "gs": 300},  # NEAR Lebanon
        ]
    }
    browser = FakeBrowser(payload)
    sink = FakeSink()
    orch, recovery, metrics = _make_orchestrator(browser, sink, tmp_path)

    report = await orch._run_cycle(1)

    assert report.aircraft_received == 4
    assert report.aircraft_in_area == 2  # aa0001 (INSIDE) + aa0004 (NEAR)
    assert report.near_border == 1  # aa0004
    assert report.out_of_area == 1  # aa0002 (Cairo) flagged + skipped (default policy)
    # Kept: aa0001 (INSIDE), aa0003 (no position, valid ICAO), aa0004 (NEAR). aa0002 skipped.
    assert report.new_icaos == 3
    assert sink.total_written == 3
    assert {r.icao for r in sink.records} == {"aa0001", "aa0003", "aa0004"}
    assert recovery.consecutive_empty == 0
    assert metrics.total_requests == 1


async def test_dual_sink_catalogue_dedups_timeseries_does_not(tmp_path: Path) -> None:
    """Wired end-to-end: the same aircraft over two cycles writes ONE catalogue
    row (accumulator dedup) but TWO state_vectors lines (per-cycle time-series)."""
    browser = FakeBrowser({"aircraft": [IN_AREA]})
    sink = FakeSink()
    sv_path = tmp_path / "state_vectors.jsonl"
    async with StateVectorSink(sv_path, batch_size=1, clock=FrozenClock()) as sv_sink:
        orch, _, _ = _make_orchestrator(browser, sink, tmp_path, state_vector_sink=sv_sink)
        await orch._run_cycle(1)
        await orch._run_cycle(2)

    assert sink.total_written == 1  # discovery catalogue: first-seen-wins
    valid, invalid = scan_jsonl(sv_path)  # time-series: one line per cycle
    assert (valid, invalid) == (2, 0)
    line = json.loads(sv_path.read_text().splitlines()[0])
    assert line["icao24"] == "aa0001" and line["source"] == "adsbexchange"
    assert isinstance(line["snapshot_time"], int)


async def test_browser_success_does_not_consult_interception(tmp_path: Path) -> None:
    browser = FakeBrowser({"aircraft": [IN_AREA]})
    browser.intercepted = {"aircraft": [IN_AREA]}  # available but must be unused
    sink = FakeSink()
    orch, _, _ = _make_orchestrator(browser, sink, tmp_path)

    report = await orch._run_cycle(1)

    assert report.source == "browser_evaluate"
    assert browser.evaluate_calls == 1
    assert browser.intercept_calls == 0  # short-circuited on first success
    assert sink.total_written == 1


# --------------------------------------------------------------------------- #
# Strategy fallback within a single cycle
# --------------------------------------------------------------------------- #


async def test_403_evaluate_falls_back_to_interception(tmp_path: Path) -> None:
    """The exact production failure: browser_evaluate 403s, interception has data."""
    browser = FakeBrowser()
    browser.evaluate_status = 403
    browser.raw_override = b"Request forbidden by administrative rules."
    browser.intercepted = {"aircraft": [IN_AREA]}
    sink = FakeSink()
    orch, recovery, _ = _make_orchestrator(browser, sink, tmp_path)

    report = await orch._run_cycle(1)

    assert report.source == "interception"  # fell through to the good source
    assert report.aircraft_in_area == 1
    assert sink.total_written == 1
    assert browser.evaluate_calls == 1 and browser.intercept_calls == 1
    assert recovery.consecutive_empty == 0  # productive: no recovery
    assert not (tmp_path / "diagnostics" / "unknown_payloads").exists()  # nothing archived


async def test_html_200_evaluate_falls_back_to_interception(tmp_path: Path) -> None:
    """A 200 HTML page (undecodable) must also fall through to interception."""
    browser = FakeBrowser()
    browser.evaluate_status = 200
    browser.raw_override = b"<html><body>maintenance</body></html>"
    browser.intercepted = {"aircraft": [IN_AREA]}
    sink = FakeSink()
    orch, recovery, _ = _make_orchestrator(browser, sink, tmp_path)

    report = await orch._run_cycle(1)

    assert report.source == "interception"
    assert sink.total_written == 1
    assert recovery.consecutive_empty == 0


async def test_403_evaluate_zstd_interception_is_decoded(tmp_path: Path) -> None:
    """The real production shape: evaluate 403s, interception returns a
    Zstandard-compressed (application/zstd) payload that must decode and parse."""
    browser = FakeBrowser()
    browser.evaluate_status = 403
    browser.raw_override = b"Request forbidden by administrative rules."
    browser.intercepted = {"aircraft": [IN_AREA]}
    browser.intercept_zstd = True  # <- application/zstd body
    sink = FakeSink()
    orch, recovery, _ = _make_orchestrator(browser, sink, tmp_path)

    report = await orch._run_cycle(1)

    assert report.source == "interception"
    assert report.content_type == "application/zstd"
    assert report.aircraft_in_area == 1
    assert sink.records[0].icao == "aa0001"
    assert recovery.consecutive_empty == 0
    assert not (tmp_path / "diagnostics" / "unknown_payloads").exists()


# --------------------------------------------------------------------------- #
# Total failure -> diagnostics + recovery
# --------------------------------------------------------------------------- #


async def test_both_sources_fail_triggers_recovery_and_diagnostics(tmp_path: Path) -> None:
    browser = FakeBrowser()
    browser.evaluate_status = 403
    browser.raw_override = b"Request forbidden by administrative rules."
    browser.intercepted = None  # interception empty too
    sink = FakeSink()
    orch, recovery, _ = _make_orchestrator(browser, sink, tmp_path)

    report = await orch._run_cycle(1)

    assert report.aircraft_received == 0
    assert report.parse_failed is False  # a fetch failure, not a parse failure
    assert recovery.consecutive_empty == 1  # empty-cycle recovery engaged
    diag = tmp_path / "diagnostics"
    assert (diag / "last_exception.log").exists()
    assert (diag / "last_response.txt").read_text().startswith("Request forbidden")
    assert not (diag / "unknown_payloads").exists()  # 403 uses overwrite, not accumulate


async def test_unparseable_json_archived_to_unknown_payloads(tmp_path: Path) -> None:
    """Decodable JSON with no aircraft array is a *parse* failure: accumulate it."""
    browser = FakeBrowser({"status": "ok", "items": [1, 2, 3]})
    sink = FakeSink()
    orch, _, _ = _make_orchestrator(browser, sink, tmp_path)

    report = await orch._run_cycle(1)

    assert report.parse_failed is True
    archive = tmp_path / "diagnostics" / "unknown_payloads"
    assert archive.exists() and any(archive.iterdir())


# --------------------------------------------------------------------------- #
# Recovery + lifecycle
# --------------------------------------------------------------------------- #


async def test_empty_cycle_increments_recovery(tmp_path: Path) -> None:
    browser = FakeBrowser({"aircraft": []})
    sink = FakeSink()
    orch, recovery, _ = _make_orchestrator(browser, sink, tmp_path)

    report = await orch._run_cycle(1)

    assert report.aircraft_received == 0
    assert recovery.consecutive_empty == 1
    assert sink.total_written == 0


async def test_unhealthy_browser_triggers_restart(tmp_path: Path) -> None:
    browser = FakeBrowser({"aircraft": []})
    browser.healthy = False
    sink = FakeSink()
    orch, _, metrics = _make_orchestrator(browser, sink, tmp_path)

    await orch._run_cycle(1)

    assert browser.restarts >= 1
    assert metrics.browser_restarts >= 1


async def test_dedup_across_cycles(tmp_path: Path) -> None:
    browser = FakeBrowser({"aircraft": [{"hex": "dddddd", "lat": 33.5123, "lon": 36.2912}]})
    sink = FakeSink()
    orch, _, _ = _make_orchestrator(browser, sink, tmp_path)

    await orch._run_cycle(1)
    report2 = await orch._run_cycle(2)

    assert report2.duplicates == 1
    assert report2.new_icaos == 0
    assert sink.total_written == 1


async def test_run_returns_error_when_browser_cannot_launch(tmp_path: Path) -> None:
    browser = FakeBrowser()
    browser.fail_launch = True
    sink = FakeSink()
    orch, _, _ = _make_orchestrator(browser, sink, tmp_path)

    assert await orch.run() == 1


async def test_run_stops_gracefully(tmp_path: Path) -> None:
    browser = FakeBrowser({"aircraft": [IN_AREA]})
    sink = FakeSink()
    orch, _, _ = _make_orchestrator(browser, sink, tmp_path)

    orch.request_stop()  # stop before the first cycle even starts
    assert await orch.run() == 0
    assert browser.shutdown_called


async def test_single_cell_run_establishes_view_on_target(tmp_path: Path) -> None:
    """Regression for issue B: a single-cell run must pan to the target center
    (previously pan only happened when there was more than one cell)."""
    browser = FakeBrowser({"aircraft": [IN_AREA]})
    sink = FakeSink()
    orch, _, _ = _make_orchestrator(browser, sink, tmp_path)

    orch.request_stop()
    await orch.run()

    assert browser.pan_calls >= 1
    assert browser.panned_to is not None
    lat, lon, _zoom = browser.panned_to
    # Panned to the AOI envelope centre, not a hand-typed coordinate.
    center_lat, center_lon = _AOI.center
    assert lat == pytest.approx(center_lat, abs=0.01)
    assert lon == pytest.approx(center_lon, abs=0.01)
