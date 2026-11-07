"""The collection loop coordinator.

The orchestrator owns *control flow*, not *work*. Every substantive step —
fetching, decoding, parsing, filtering, accumulating, recording — is delegated
to an injected collaborator, so this class stays a readable script of "what
happens each cycle" and each collaborator stays independently testable.

All dependencies arrive through the constructor (dependency injection); nothing
is constructed here. That is what lets a test drive a whole cycle with fakes for
the browser, fetcher, and sink.
"""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from dataclasses import dataclass

from .accumulator import Accumulator
from .browser import BrowserPort
from .config import AppConfig
from .diagnostics import DiagnosticsRecorder, MetricsCollector
from .exceptions import (
    BrowserError,
    NoDataSourceError,
    ParserError,
    RecoveryError,
    ScraperError,
)
from .fetcher import Fetcher, FetchOutcome
from .geo import GeoService
from .logger import get_logger
from .models import Aircraft, CycleReport, GeoBounds, RawResponse
from .parser import AdaptiveAircraftParser
from .recovery import RecoveryAction, RecoveryPolicy
from .statevector import StateVectorProducer
from .utils import BackoffPolicy, Clock, MemoryProbe, SystemClock
from .validation import CollectionGapDetector, RecordValidator

_log = get_logger("orchestrator")


@dataclass(slots=True)
class _RegionOutcome:
    """Internal per-region collection result aggregated into a CycleReport."""

    response: RawResponse | None
    received: int
    aircraft: list[Aircraft]  # ALL parsed aircraft; radius filtering is the validator's job
    parse_failed: bool
    actual_box: dict[str, float] | None = None  # server-reported box, if any


class ScraperOrchestrator:
    """Coordinates continuous collection with health checks and recovery."""

    def __init__(
        self,
        *,
        config: AppConfig,
        browser: BrowserPort,
        fetcher: Fetcher,
        parser: AdaptiveAircraftParser,
        validator: RecordValidator,
        accumulator: Accumulator,
        gap_detector: CollectionGapDetector,
        geo: GeoService,
        diagnostics: DiagnosticsRecorder,
        metrics: MetricsCollector,
        recovery: RecoveryPolicy,
        main_bounds: GeoBounds,
        cells: list[GeoBounds],
        state_vector_sink: StateVectorProducer | None = None,
        backoff: BackoffPolicy | None = None,
        clock: Clock | None = None,
        memory: MemoryProbe | None = None,
    ) -> None:
        self._cfg = config
        self._browser = browser
        self._fetcher = fetcher
        self._parser = parser
        self._validator = validator
        self._accumulator = accumulator
        self._gap_detector = gap_detector
        # Optional SECOND archive: the scraplan StateVector time-series. Written
        # alongside (never instead of) the accumulator's discovery catalogue.
        self._state_vector_sink = state_vector_sink
        self._geo = geo
        self._diag = diagnostics
        self._metrics = metrics
        self._recovery = recovery
        self._main_bounds = main_bounds
        self._cells = cells
        self._backoff = backoff or BackoffPolicy(max_wait=config.timing.fetch_interval)
        self._clock: Clock = clock or SystemClock()
        self._memory = memory or MemoryProbe()
        self._stop = asyncio.Event()
        self._metrics.bind_unique_supplier(lambda: self._accumulator.unique_count)

    # -- public API -------------------------------------------------------- #

    async def run(self) -> int:
        """Run until stopped. Returns a process exit code (0 = clean)."""
        self._install_signal_handlers()
        _log.info(
            "starting: fetch_box=%s cells=%d strategies=%s interval=%.0fs",
            self._main_bounds.to_dict(),
            len(self._cells),
            self._fetcher.strategy_names,
            self._cfg.timing.fetch_interval,
        )
        try:
            await self._browser.launch()
            await self._establish_view()
        except BrowserError as exc:
            self._diag.record_exception("launch", exc)
            _log.critical("could not launch browser: %s", exc)
            return 1

        cycle = 0
        exit_code = 0
        try:
            while not self._stop.is_set():
                cycle += 1
                try:
                    report = await self._run_cycle(cycle)
                except RecoveryError as exc:
                    _log.critical("unrecoverable: %s", exc)
                    exit_code = 1
                    break
                except ScraperError as exc:
                    # Known fault: log with context, count it, keep running.
                    self._diag.record_exception(f"cycle-{cycle}", exc)
                    self._metrics.record_failure()
                    _log.error("cycle %d failed: %s", cycle, exc)
                else:
                    _log.info("cycle complete", extra=report.as_log_fields())
                    if cycle % 20 == 0:
                        _log.info("metrics", extra=self._metrics.snapshot().as_log_fields())
                await self._interruptible_sleep(self._cfg.timing.fetch_interval)
        finally:
            await self._shutdown()
        _log.info("final metrics", extra=self._metrics.snapshot().as_log_fields())
        return exit_code

    def request_stop(self) -> None:
        if not self._stop.is_set():
            _log.warning("stop requested; finishing current cycle")
            self._stop.set()

    # -- one cycle --------------------------------------------------------- #

    async def _run_cycle(self, cycle: int) -> CycleReport:
        started = self._clock.monotonic()
        cycle_now = self._clock.now()
        # One coherent snapshot second for the whole cycle, so every state vector
        # written this cycle shares it (scraplan's ts = to_timestamp(snapshot_time)).
        snapshot_time = int(cycle_now.timestamp())
        # Observational only: record (never react to) a gap since the last cycle.
        await self._gap_detector.observe(cycle_now)
        await self._ensure_browser_healthy()

        received = in_area_total = near_total = added = duplicates = 0
        out_of_area = invalid_identity = 0
        parse_failed = False
        last_response: RawResponse | None = None
        actual_box: dict[str, float] | None = None

        multi = len(self._cells) > 1
        for cell in self._cells:
            if self._stop.is_set():
                break
            if multi:
                await self._pan_to(cell)
            outcome = await self._collect_region()
            if outcome.response is not None:
                last_response = outcome.response
            if outcome.actual_box is not None:
                actual_box = outcome.actual_box
            received += outcome.received
            parse_failed = parse_failed or outcome.parse_failed
            if outcome.aircraft:
                # Between parse and accumulate: validate via the injected validator.
                # The orchestrator holds NO validation logic — it only delegates.
                source = outcome.response.source if outcome.response else "unknown"
                validated = [
                    self._validator.validate(craft, source=source) for craft in outcome.aircraft
                ]
                # SECOND archive: the full per-cycle time-series (no dedup). The
                # producer itself filters out OpenSky-sourced observations.
                if self._state_vector_sink is not None:
                    await self._state_vector_sink.write_snapshot(validated, snapshot_time)
                result = await self._accumulator.process(validated)
                in_area_total += result.in_area
                near_total += result.near_border
                added += result.added
                duplicates += result.duplicates
                out_of_area += result.skipped_out_of_area
                invalid_identity += result.skipped_invalid_identity

        self._log_viewport(actual_box)
        healthy = await self._browser.is_healthy()
        report = CycleReport(
            cycle=cycle,
            source=last_response.source if last_response else None,
            http_status=last_response.status if last_response else None,
            content_type=last_response.content_type if last_response else None,
            response_bytes=last_response.size if last_response else 0,
            aircraft_received=received,
            aircraft_in_area=in_area_total,
            near_border=near_total,
            new_icaos=added,
            duplicates=duplicates,
            out_of_area=out_of_area,
            invalid_identity=invalid_identity,
            parse_failed=parse_failed,
            processing_seconds=self._clock.monotonic() - started,
            memory_mb=self._memory.rss_mb(),
            browser_healthy=healthy,
            total_known=self._accumulator.unique_count,
        )
        self._metrics.record_cycle(
            duration_s=report.processing_seconds,
            response_bytes=report.response_bytes,
            aircraft_seen=received,
        )
        await self._decide_recovery(report)
        return report

    async def _collect_region(self) -> _RegionOutcome:
        """Fetch (with fallback + retries) and parse the current region.

        The fetcher has already run the whole strategy fallback chain and
        returned a decodable 2xx payload; a 403 or HTML page from one source has
        already been rejected in favour of the next. Geographic filtering is NOT
        done here — the validation layer flags radius per-record so the archive
        stays complete.
        """
        outcome = await self._fetch_with_retries()
        if outcome is None:
            # Every source failed this cycle (diagnostics already archived below).
            return _RegionOutcome(None, 0, [], parse_failed=False)

        response, payload = outcome.response, outcome.payload
        # Keep the latest good response on disk for post-mortem debugging.
        await asyncio.to_thread(self._diag.record_response, response, payload)

        try:
            result = self._parser.parse(payload)
        except ParserError as exc:
            _log.warning("parse failed: %s", exc)
            await self._archive(response, str(exc), "parse")
            return _RegionOutcome(response, 0, [], parse_failed=True)

        box = payload.get("box") if isinstance(payload, dict) else None
        actual_box = box if isinstance(box, dict) else None
        return _RegionOutcome(
            response, result.count, list(result.aircraft), parse_failed=False, actual_box=actual_box
        )

    async def _fetch_with_retries(self) -> FetchOutcome | None:
        """Run the fallback chain, retrying it (not a single strategy) on total
        failure. Only when every attempt fails do we archive for diagnostics."""
        attempt = 0
        last_failure: NoDataSourceError | None = None
        while attempt <= self._cfg.timing.max_retries:
            if self._stop.is_set():
                return None
            self._metrics.record_request()
            try:
                return await self._fetcher.fetch()
            except NoDataSourceError as exc:
                last_failure = exc
                attempt += 1
                if attempt > self._cfg.timing.max_retries:
                    break
                self._metrics.record_retry()
                delay = self._backoff.delay_for(attempt)
                _log.debug("fetch retry %d in %.1fs (%s)", attempt, delay, exc)
                await self._interruptible_sleep(delay)
        if last_failure is not None:
            _log.warning(
                "all sources failed after %d retries: %s",
                self._cfg.timing.max_retries,
                last_failure,
            )
            await self._archive_fetch_failure(last_failure)
        return None

    # -- recovery orchestration ------------------------------------------- #

    async def _decide_recovery(self, report: CycleReport) -> None:
        if report.parse_failed:
            action = self._recovery.on_parser_failure()
        elif report.aircraft_received == 0:
            action = self._recovery.on_empty_cycle()
        else:
            self._recovery.on_productive_cycle()
            action = RecoveryAction.NONE
        await self._apply_action(action)

    async def _apply_action(self, action: RecoveryAction) -> None:
        if action is RecoveryAction.NONE or action is RecoveryAction.SAVE_PAYLOAD:
            return
        if action is RecoveryAction.RELOAD:
            await self._reload()
        elif action is RecoveryAction.RESTART:
            await self._restart()
        elif action is RecoveryAction.ABORT:
            raise RecoveryError("recovery policy requested abort")

    async def _ensure_browser_healthy(self) -> None:
        if not await self._browser.is_healthy():
            _log.warning("browser unhealthy at cycle start; restarting")
            await self._restart()

    async def _reload(self) -> None:
        try:
            await self._browser.reload()
            self._metrics.record_page_reload()
        except BrowserError as exc:
            self._diag.record_exception("reload", exc)
            _log.error("reload failed: %s", exc)
            await self._restart()
            return
        await self._establish_view()  # a reload resets the map to its default view

    async def _restart(self) -> None:
        try:
            await self._browser.restart()
        except BrowserError as exc:
            self._diag.record_exception("restart", exc)
            action = self._recovery.on_restart_failed()
            _log.error("restart failed: %s (action=%s)", exc, action.name)
            if action is RecoveryAction.ABORT:
                raise RecoveryError("too many failed restarts") from exc
            return
        self._metrics.record_browser_restart()
        self._recovery.on_restart_succeeded()
        await self._establish_view()  # re-point the fresh browser at the target

    async def _establish_view(self) -> None:
        """Point the map at the configured target so the intercepted box matches.

        Needed for single-cell runs (which never pan per-cell) and after any
        reload/restart, which resets the map to tar1090's default view.
        """
        await self._pan_to(self._main_bounds)

    async def _pan_to(self, bounds: GeoBounds) -> None:
        lat, lon = bounds.center
        applied = await self._browser.pan_to(lat, lon, self._geo.estimate_zoom(bounds))
        _log.info(
            "pan to lat=%.4f lon=%.4f zoom=%d (js_applied=%s)",
            lat,
            lon,
            self._geo.estimate_zoom(bounds),
            applied,
        )
        await self._interruptible_sleep(self._cfg.timing.pan_settle_wait)

    def _log_viewport(self, actual_box: dict[str, float] | None) -> None:
        """Log the requested area vs. the server-reported box (visibility for B)."""
        b = self._main_bounds
        target = f"S={b.south:.2f} N={b.north:.2f} W={b.west:.2f} E={b.east:.2f}"
        if actual_box is None:
            _log.info("viewport target[%s] actual[unavailable]", target)
            return
        actual = (
            f"S={actual_box['south']:.2f} N={actual_box['north']:.2f} "
            f"W={actual_box['west']:.2f} E={actual_box['east']:.2f}"
        )
        center_lat, center_lon = b.center
        on_target = (
            actual_box["south"] <= center_lat <= actual_box["north"]
            and actual_box["west"] <= center_lon <= actual_box["east"]
        )
        _log.info("viewport target[%s] actual[%s] on_target=%s", target, actual, on_target)

    async def _archive(self, response: RawResponse, reason: str, context: str) -> None:
        """Accumulate a decodable-but-unparseable payload for later study."""
        await asyncio.to_thread(self._diag.save_unknown_payload, response, f"{context}: {reason}")
        html = await self._browser.capture_html()
        if html is not None:
            await asyncio.to_thread(self._diag.record_html, html)

    async def _archive_fetch_failure(self, failure: NoDataSourceError) -> None:
        """Snapshot a total fetch failure (e.g. every source 403'd).

        Uses the overwrite-style ``record_response`` (not the accumulating
        ``save_unknown_payload``) because a persistent 403 recurs every cycle and
        we only need the latest, plus the page HTML and the exception.
        """
        self._diag.record_exception("fetch", failure)
        for response in failure.responses:
            await asyncio.to_thread(self._diag.record_response, response, None)
        if self._cfg.diagnostics.dump_raw:
            timestamp = self._clock.now().strftime("%Y%m%d_%H%M%S_%f")
            for response in failure.responses:
                await asyncio.to_thread(self._diag.dump_raw_payload, response, timestamp)
        html = await self._browser.capture_html()
        if html is not None:
            await asyncio.to_thread(self._diag.record_html, html)

    # -- lifecycle helpers ------------------------------------------------- #

    async def _interruptible_sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def _shutdown(self) -> None:
        _log.info("shutting down")
        try:
            await self._browser.shutdown()
        except Exception as exc:  # noqa: BLE001 - teardown must complete regardless
            self._diag.record_exception("shutdown", exc)
            _log.error("error during browser shutdown: %s", exc)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.request_stop)
