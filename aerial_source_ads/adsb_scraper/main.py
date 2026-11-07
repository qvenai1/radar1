"""Composition root and process entry point.

This is the *only* module that knows the concrete implementations of every
abstraction and wires them together. Keeping construction in one place means the
rest of the codebase depends on Protocols, not classes, and the entire object
graph can be swapped (e.g. a fake browser in tests) without touching business
logic. Nothing here contains domain logic — it is pure assembly.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Sequence
from contextlib import AsyncExitStack

from .accumulator import Accumulator
from .area import AreaOfInterest, load_area_of_interest
from .browser import BrowserManager, BrowserPort
from .config import AppConfig, load_config
from .constants import ENV_PREFIX
from .diagnostics import DiagnosticsRecorder, MetricsCollector
from .exceptions import ConfigError
from .fetcher import (
    AdsbExchangeApiStrategy,
    BrowserEvaluateStrategy,
    Fetcher,
    FetchStrategy,
    InterceptionStrategy,
    OpenSkyRestStrategy,
)
from .geo import GeoService
from .logger import configure_logging, get_logger
from .models import GeoBounds
from .orchestrator import ScraperOrchestrator
from .parser import AdaptiveAircraftParser, ResponseDecoder
from .persistence import JsonlSink
from .recovery import RecoveryPolicy, StateRecovery
from .statevector import StateVectorSink
from .utils import BackoffPolicy, MemoryProbe, SystemClock
from .validation import AircraftRecordValidator, CollectionGapDetector, JsonlGapJournal

_log = get_logger("main")


def _build_strategies(
    cfg: AppConfig, browser: BrowserPort, main_bounds: GeoBounds
) -> list[FetchStrategy]:
    """Assemble the ordered strategy chain.

    Browser-backed strategies come first (they defeat Cloudflare). Optional REST
    providers are appended only when credentials/opt-in are present in the
    environment — this is the extension point for new data sources.
    """
    strategies: list[FetchStrategy] = [
        BrowserEvaluateStrategy(browser, cfg.browser.aircraft_endpoint),
        InterceptionStrategy(browser),
    ]
    if os.environ.get(f"{ENV_PREFIX}ENABLE_OPENSKY", "").lower() in ("1", "true", "yes"):
        strategies.append(OpenSkyRestStrategy(main_bounds))
        _log.info("OpenSky fallback strategy enabled")
    adsbx_key = os.environ.get(f"{ENV_PREFIX}ADSBX_API_KEY")
    if adsbx_key:
        strategies.append(AdsbExchangeApiStrategy(main_bounds, adsbx_key))
        _log.info("ADS-B Exchange API strategy enabled")
    return strategies


async def _run(cfg: AppConfig, area: AreaOfInterest) -> int:
    """Wire the object graph and hand control to the orchestrator."""
    clock = SystemClock()
    geo = GeoService()
    # Fetch envelope (a wide box) is derived from the polygons; the polygons are
    # the interest filter. The box is the fetch mechanism, not the AOI.
    main_bounds = area.bounding_box()
    cells = [main_bounds]  # one wide box encloses all seven countries
    center_lat, center_lon = area.center

    diagnostics = DiagnosticsRecorder(cfg.diagnostics, cfg.diagnostics_dir)
    metrics = MetricsCollector(clock=clock)
    state = StateRecovery(cfg.results_file, cfg.completed_cache_file)
    known_icaos = await state.load_completed()

    validator = AircraftRecordValidator(area=area, config=cfg.validation, geo=geo)
    gap_detector = CollectionGapDetector(
        journal=JsonlGapJournal(
            cfg.collection_gaps_file, cfg.collection_state_file, fsync=cfg.persistence.fsync
        ),
        threshold_s=cfg.validation.gap_threshold_s,
    )

    # Open the globe over the envelope centre so the first fetch already spans it.
    initial_view = (center_lat, center_lon, geo.estimate_zoom(main_bounds))
    browser = BrowserManager(cfg.browser, initial_view=initial_view)
    fetcher = Fetcher(_build_strategies(cfg, browser, main_bounds), decoder=ResponseDecoder())

    async with AsyncExitStack() as stack:
        sink = await stack.enter_async_context(
            JsonlSink(
                cfg.results_file,
                batch_size=cfg.persistence.batch_size,
                flush_interval=cfg.persistence.flush_interval,
                fsync=cfg.persistence.fsync,
                clock=clock,
            )
        )
        accumulator = Accumulator(
            sink,
            known_icaos,
            persist_out_of_area=cfg.area.persist_out_of_area,
            clock=clock,
            on_new=state.append_to_cache,
        )

        # Optional SECOND archive: the scraplan StateVector position time-series.
        state_vector_sink: StateVectorSink | None = None
        if cfg.state_vectors.enabled:
            state_vector_sink = await stack.enter_async_context(
                StateVectorSink(
                    cfg.state_vectors_file,
                    batch_size=cfg.persistence.batch_size,
                    flush_interval=cfg.persistence.flush_interval,
                    fsync=cfg.persistence.fsync,
                    # Same sampling frame as the catalogue (Accumulator above):
                    # one policy, one config field, two consumers.
                    persist_out_of_area=cfg.area.persist_out_of_area,
                    clock=clock,
                )
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
            diagnostics=diagnostics,
            metrics=metrics,
            recovery=RecoveryPolicy(cfg.recovery),
            main_bounds=main_bounds,
            cells=cells,
            state_vector_sink=state_vector_sink,
            backoff=BackoffPolicy(max_wait=cfg.timing.fetch_interval),
            clock=clock,
            memory=MemoryProbe(),
        )
        return await orchestrator.run()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        cfg = load_config(argv)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(cfg.logging, cfg.log_file)

    # Boundaries load ONCE, at startup, from a static asset — fail fast (never
    # mid-run) if the asset is missing or malformed.
    try:
        area = load_area_of_interest(cfg.area.asset_path, cfg.area.buffer_km)
    except ConfigError as exc:
        print(f"area configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(_run(cfg, area))
    except KeyboardInterrupt:
        _log.warning("interrupted by user")
        return 130
    except Exception:  # noqa: BLE001 - top-level guard: log full traceback, never crash silent
        _log.critical("fatal error", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
