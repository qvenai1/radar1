"""Composition root and command-line entry point.

The *only* module that wires concrete implementations together. Every other
module depends on abstractions; here we choose the actual browser, the strategy
order, the area filter, the sinks, and translate CLI/env into a :class:`Config`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from pathlib import Path

from . import constants as C
from .accumulator import Accumulator
from .area import AreaFilter, BboxArea, load_area_of_interest
from .browser import BrowserManager
from .config import Config, build_config
from .diagnostics import DiagnosticsWriter
from .enrichment import IdentityStore, VesselDetailEnricher
from .fetcher import DirectTileStrategy, InterceptionTileStrategy, TileFetcher
from .geo import bounds_from_center
from .logger import configure_logging, get_logger
from .metrics import MetricsTracker
from .orchestrator import Orchestrator
from .parser import ResponseDecoder, VesselParser
from .persistence import CatalogueSink, PositionSink
from .recovery import RecoveryPolicy, StateRecovery
from .session import SessionRotator, build_sessions, load_proxies
from .validation import CollectionGapDetector, JsonlGapJournal, VesselRecordValidator

_log = get_logger("main")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mtscraper",
        description="Standalone MarineTraffic live-map vessel scraper (browser interception).",
    )
    # Default centre matches the URL supplied: centerx(lon):35.2, centery(lat):33.2.
    p.add_argument("--lat", type=float, default=33.2, help="map centre latitude (default 33.2)")
    p.add_argument("--lon", type=float, default=35.2, help="map centre longitude (default 35.2)")
    p.add_argument("--zoom", type=int, default=7, help="MarineTraffic map zoom (default 7)")
    p.add_argument(
        "--area", choices=("polygon", "bbox"), default="polygon", help="area filter"
    )
    p.add_argument("--buffer-km", type=float, default=None, help="polygon coast buffer km")
    p.add_argument("--half-height-km", type=float, default=250.0, help="bbox half-height km")
    p.add_argument("--half-width-km", type=float, default=250.0, help="bbox half-width km")
    p.add_argument("--output", type=Path, default=Path("mt_output"), help="output dir")
    p.add_argument("--interval", type=float, default=None, help="seconds between cycles")
    p.add_argument("--max-cycles", type=int, default=None, help="stop after N cycles")
    p.add_argument("--settle", type=float, default=6.0, help="secs to wait for tiles")
    headless = p.add_mutually_exclusive_group()
    headless.add_argument("--headful", dest="headless", action="store_false", help="visible")
    headless.add_argument("--headless", dest="headless", action="store_true", help="headless")
    p.set_defaults(headless=None)
    p.add_argument("--persist-out-of-area", action="store_true", help="keep out-of-area vessels")
    p.add_argument("--no-positions", action="store_true", help="disable the time-series archive")
    p.add_argument(
        "--position-heartbeat",
        type=float,
        default=None,
        metavar="SECS",
        help="re-write an unchanged vessel this often (0 = store every observation)",
    )
    p.add_argument("--proxy", type=str, default=None, help="proxy server URL")
    p.add_argument("--enrich", action="store_true", help="resolve MMSI/IMO per vessel (details)")
    p.add_argument("--enrich-per-cycle", type=int, default=None, help="max identities/cycle (25)")
    p.add_argument("--enrich-throttle", type=float, default=None, help="secs between detail calls")
    p.add_argument("--profile-dir", type=str, default=None, help="persistent browser profile dir")
    p.add_argument("--passive", action="store_true", help="harvest map's own cadence (stealthier)")
    p.add_argument("--proxies", type=Path, default=None, help="proxy list file (one per line)")
    p.add_argument("--profile-count", type=int, default=None, help="rotating session count (1)")
    p.add_argument("--log-level", default="INFO", help="logging level (default INFO)")
    return p.parse_args(argv)


def _build_rotator(cfg: Config) -> SessionRotator | None:
    """Build the session ring when rotation is configured (proxies/profiles).

    Returns ``None`` for the plain single-session case, so the browser falls back
    to the static proxy/profile in ``cfg.browser``.
    """
    proxies = load_proxies(cfg.proxies_file)
    if cfg.profile_count <= 1 and not proxies:
        return None
    sessions = build_sessions(
        proxies=proxies,
        profile_base=cfg.output_dir / ".profiles",
        profile_count=cfg.profile_count,
        single_proxy=cfg.browser.proxy,
        single_profile=cfg.browser.user_data_dir,
    )
    _log.info("session rotation enabled: %d identities", len(sessions))
    return SessionRotator(sessions)


def _build_area(cfg: Config) -> AreaFilter:
    if cfg.area_mode == "polygon":
        return load_area_of_interest(cfg.aoi_asset_path, cfg.area_buffer_km)
    bounds = bounds_from_center(
        cfg.center_lat, cfg.center_lon, cfg.half_height_km, cfg.half_width_km
    )
    return BboxArea(bounds)


async def _run(cfg: Config) -> None:
    area = _build_area(cfg)
    bounds = area.bounding_box()

    # Resume: prefer the fast ship-id cache, else rebuild from the archive.
    state_recovery = StateRecovery(
        cfg.output_dir / C.CATALOGUE_FILENAME, cfg.output_dir / C.COMPLETED_CACHE_FILENAME
    )
    known_ids = await state_recovery.load_completed()

    with contextlib.ExitStack() as stack:
        catalogue = stack.enter_context(
            contextlib.closing(CatalogueSink(cfg.output_dir / C.CATALOGUE_FILENAME, known_ids))
        )
        positions = (
            stack.enter_context(
                contextlib.closing(
                    PositionSink(
                        cfg.output_dir / C.POSITIONS_FILENAME,
                        heartbeat_s=cfg.position_heartbeat_s,
                    )
                )
            )
            if cfg.positions_enabled
            else None
        )

        rotator = _build_rotator(cfg)
        browser = BrowserManager(cfg.browser, cfg.map_url, rotator)
        fetcher = TileFetcher(
            strategies=[
                InterceptionTileStrategy(browser),
                DirectTileStrategy(browser, bounds, cfg.zoom, cfg.browser.base_url),
            ],
            decoder=ResponseDecoder(),
        )
        gap_detector = CollectionGapDetector(
            JsonlGapJournal(
                cfg.output_dir / C.COLLECTION_GAPS_FILENAME,
                cfg.output_dir / C.COLLECTION_STATE_FILENAME,
            ),
            cfg.validation.gap_threshold_s,
        )

        enricher: VesselDetailEnricher | None = None
        identity_store: IdentityStore | None = None
        if cfg.enrich_enabled:
            enricher = VesselDetailEnricher(browser, cfg.browser.base_url)
            identity_store = stack.enter_context(
                contextlib.closing(IdentityStore(cfg.output_dir / C.IDENTITIES_FILENAME))
            )

        orchestrator = Orchestrator(
            cfg,
            browser,
            fetcher,
            VesselParser(),
            VesselRecordValidator(area=area, config=cfg.validation),
            Accumulator(catalogue, positions, persist_out_of_area=cfg.persist_out_of_area),
            DiagnosticsWriter(cfg.output_dir),
            RecoveryPolicy(cfg.recovery),
            state_recovery,
            gap_detector,
            MetricsTracker(),
            enricher,
            identity_store,
            rotator,
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, orchestrator.request_stop)

        _log.info(
            "starting: centre=(%.4f, %.4f) zoom=%d area=%s fetch_box=%s output=%s",
            cfg.center_lat,
            cfg.center_lon,
            cfg.zoom,
            cfg.area_mode,
            bounds.to_dict(),
            cfg.output_dir,
        )
        await orchestrator.run()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    # Enrichment needs a stable Cloudflare clearance; default to a persistent
    # profile under the output dir unless the user pointed one elsewhere.
    profile_dir = args.profile_dir
    if args.enrich and profile_dir is None:
        profile_dir = str(args.output / ".browser_profile")
    cfg = build_config(
        center_lat=args.lat,
        center_lon=args.lon,
        zoom=args.zoom,
        output_dir=args.output,
        half_height_km=args.half_height_km,
        half_width_km=args.half_width_km,
        area_mode=args.area,
        area_buffer_km=args.buffer_km,
        poll_interval_s=args.interval,
        settle_s=args.settle,
        max_cycles=args.max_cycles,
        headless=args.headless,
        persist_out_of_area=True if args.persist_out_of_area else None,
        positions_enabled=False if args.no_positions else None,
        position_heartbeat_s=args.position_heartbeat,
        proxy=args.proxy,
        enrich_enabled=args.enrich,
        enrich_max_per_cycle=args.enrich_per_cycle,
        enrich_throttle_s=args.enrich_throttle,
        profile_dir=profile_dir,
        collection_mode="passive" if args.passive else "active",
        profile_count=args.profile_count,
        proxies_file=args.proxies,
    )
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        log_file=cfg.output_dir / C.LOG_FILENAME,
    )
    try:
        asyncio.run(_run(cfg))
    except KeyboardInterrupt:  # pragma: no cover - interactive
        _log.info("interrupted; shutting down")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
