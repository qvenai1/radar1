#!/usr/bin/env python3
"""
ADSBExchange Area Scraper – Production-Ready v2.1
==================================================
Fixed: Changed wait_until from 'networkidle' to 'domcontentloaded'
to avoid timeout on live-polling map sites.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import signal
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiofiles

# ==============================================================================
# Logging
# ==============================================================================
logger = logging.getLogger("adsb_solid")


def _setup_logging(output_dir: Path, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    output_dir.mkdir(parents=True, exist_ok=True)
    console_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"
    )
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    handlers[0].setFormatter(console_fmt)
    fh = logging.FileHandler(output_dir / "scraper.log", encoding="utf-8", mode="a")
    fh.setFormatter(file_fmt)
    handlers.append(fh)
    logging.basicConfig(level=level, handlers=handlers, force=True)


# ==============================================================================
# Domain Models
# ==============================================================================

@dataclass(frozen=True, slots=True)
class GeoBounds:
    north: float
    south: float
    east: float
    west: float

    def __post_init__(self) -> None:
        assert -90 <= self.south <= self.north <= 90
        assert -180 <= self.west <= self.east <= 180

    def to_dict(self) -> dict[str, float]:
        return {"north": self.north, "south": self.south, "east": self.east, "west": self.west}

    def center(self) -> tuple[float, float]:
        return (
            self.south + (self.north - self.south) / 2,
            self.west + (self.east - self.west) / 2,
        )


@dataclass(frozen=True, slots=True)
class AircraftRecord:
    icao: str
    fetched_at: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"icao": self.icao, "fetched_at": self.fetched_at, "data": self.data}


# ==============================================================================
# Configuration
# ==============================================================================

@dataclass(frozen=True, slots=True)
class GridConfig:
    center_lat: float = 60.17
    center_lon: float = 24.94
    radius_km: float = 300.0
    rows: int = 1
    cols: int = 1

    def __post_init__(self) -> None:
        if not -90 <= self.center_lat <= 90:
            raise ValueError("center_lat must be between -90 and 90")
        if not -180 <= self.center_lon <= 180:
            raise ValueError("center_lon must be between -180 and 180")
        if self.radius_km <= 0:
            raise ValueError("radius_km must be > 0")
        if self.rows < 1 or self.cols < 1:
            raise ValueError("rows and cols must be >= 1")


@dataclass(frozen=True, slots=True)
class TimingConfig:
    fetch_interval: float = 15.0
    max_retries: int = 4
    map_load_wait: float = 8.0

    def __post_init__(self) -> None:
        if self.fetch_interval < 1.0:
            raise ValueError("fetch_interval must be >= 1 second")
        if self.max_retries < 1:
            raise ValueError("max_retries must be >= 1")


@dataclass(frozen=True, slots=True)
class InfrastructureConfig:
    output_dir: Path = field(default_factory=lambda: Path("adsb_output"))
    headless: bool = True
    proxy: str | None = None
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

    @property
    def results_file(self) -> Path:
        return self.output_dir / "aircraft.jsonl"

    @property
    def completed_cache_file(self) -> Path:
        return self.output_dir / ".completed_icao_cache.txt"


@dataclass(frozen=True, slots=True)
class Config:
    grid: GridConfig
    timing: TimingConfig
    infra: InfrastructureConfig


# ==============================================================================
# Constants
# ==============================================================================
EARTH_RADIUS_KM = 6371.0


# ==============================================================================
# Utilities
# ==============================================================================

class BackoffPolicy:
    """Exponential backoff with jitter."""

    def __init__(self, max_wait: float = 30.0, jitter: float = 1.0) -> None:
        self.max_wait = max_wait
        self.jitter = jitter

    def calculate(self, attempt: int) -> float:
        base = min(2 ** attempt, self.max_wait)
        return base + random.uniform(0, self.jitter)


# ==============================================================================
# Geo Service
# ==============================================================================

class GeoService:
    """Pure geographic functions."""

    @staticmethod
    def compute_bounds_from_radius(lat: float, lon: float, radius_km: float) -> GeoBounds:
        lat_delta = math.degrees(radius_km / EARTH_RADIUS_KM)
        lon_delta = math.degrees(radius_km / (EARTH_RADIUS_KM * math.cos(math.radians(lat))))
        return GeoBounds(
            north=min(90.0, lat + lat_delta),
            south=max(-90.0, lat - lat_delta),
            east=min(180.0, lon + lon_delta),
            west=max(-180.0, lon - lon_delta),
        )

    @staticmethod
    def split_bounds(bounds: GeoBounds, rows: int, cols: int) -> list[GeoBounds]:
        lat_step = (bounds.north - bounds.south) / rows
        lon_step = (bounds.east - bounds.west) / cols
        cells: list[GeoBounds] = []
        for r in range(rows):
            for c in range(cols):
                cells.append(GeoBounds(
                    north=bounds.north - r * lat_step,
                    south=bounds.north - (r + 1) * lat_step,
                    west=bounds.west + c * lon_step,
                    east=bounds.west + (c + 1) * lon_step,
                ))
        return cells

    @staticmethod
    def point_in_bounds(lat: float, lon: float, bounds: GeoBounds) -> bool:
        return bounds.south <= lat <= bounds.north and bounds.west <= lon <= bounds.east

    @staticmethod
    def bounds_to_zoom(bounds: GeoBounds) -> int:
        """Estimate appropriate zoom level for the given bounds."""
        lat_diff = bounds.north - bounds.south
        lon_diff = bounds.east - bounds.west
        max_diff = max(lat_diff, lon_diff)
        if max_diff > 90: return 2
        elif max_diff > 45: return 3
        elif max_diff > 20: return 4
        elif max_diff > 10: return 5
        elif max_diff > 5: return 6
        elif max_diff > 2: return 7
        elif max_diff > 1: return 8
        elif max_diff > 0.5: return 9
        else: return 10


# ==============================================================================
# File I/O
# ==============================================================================

class JsonlWriter:
    """Batched JSONL writer with persistent file handle."""

    def __init__(self, path: Path, batch_size: int = 50, flush_interval: float = 5.0) -> None:
        self.path = path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._buffer: list[str] = []
        self._lock = asyncio.Lock()
        self._last_flush = time.monotonic()
        self._total_written = 0
        self._file_handle = None

    async def __aenter__(self) -> JsonlWriter:
        self._file_handle = await aiofiles.open(self.path, "a", encoding="utf-8")
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def write(self, record: AircraftRecord) -> None:
        line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
        async with self._lock:
            self._buffer.append(line)
            should_flush = (
                len(self._buffer) >= self.batch_size
                or (time.monotonic() - self._last_flush) >= self.flush_interval
            )
            if should_flush:
                await self._flush_unlocked()

    async def _flush_unlocked(self) -> None:
        if not self._buffer or self._file_handle is None:
            return
        data = "".join(self._buffer)
        await self._file_handle.write(data)
        await self._file_handle.flush()
        self._total_written += len(self._buffer)
        self._buffer.clear()
        self._last_flush = time.monotonic()

    async def close(self) -> None:
        async with self._lock:
            await self._flush_unlocked()
            if self._file_handle:
                await self._file_handle.close()
                self._file_handle = None

    @property
    def total_written(self) -> int:
        return self._total_written


# ==============================================================================
# State Recovery
# ==============================================================================

class StateRecovery:
    @staticmethod
    async def load_completed_icaos(infra: InfrastructureConfig) -> set[str]:
        cache_file = infra.completed_cache_file
        results_file = infra.results_file

        if cache_file.exists() and results_file.exists():
            if cache_file.stat().st_mtime >= results_file.stat().st_mtime:
                try:
                    text = await asyncio.to_thread(cache_file.read_text, encoding="utf-8")
                    return set(line.strip() for line in text.splitlines() if line.strip())
                except OSError:
                    pass

        if not results_file.exists():
            return set()

        def _read() -> set[str]:
            done: set[str] = set()
            try:
                with results_file.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            if isinstance(obj, dict) and "icao" in obj and isinstance(obj["icao"], str):
                                done.add(obj["icao"])
                        except (ValueError, KeyError, TypeError):
                            continue
            except OSError:
                pass
            return done

        completed = await asyncio.to_thread(_read)
        try:
            await asyncio.to_thread(
                cache_file.write_text, "\n".join(sorted(completed)), encoding="utf-8"
            )
        except OSError:
            logger.debug("Could not write completed ICAO cache")
        return completed

    @staticmethod
    async def update_cache(infra: InfrastructureConfig, new_icaos: set[str]) -> None:
        cache_file = infra.completed_cache_file
        existing = set()
        if cache_file.exists():
            try:
                text = await asyncio.to_thread(cache_file.read_text, encoding="utf-8")
                existing = set(line.strip() for line in text.splitlines() if line.strip())
            except OSError:
                pass
        existing.update(new_icaos)
        try:
            await asyncio.to_thread(
                cache_file.write_text, "\n".join(sorted(existing)), encoding="utf-8"
            )
        except OSError:
            logger.debug("Could not update completed ICAO cache")


# ==============================================================================
# Browser Manager (Playwright) - FIXED v2.1
# ==============================================================================

class BrowserManager:
    """Manages Playwright browser lifecycle."""

    def __init__(self, infra: InfrastructureConfig) -> None:
        self.infra = infra
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def page(self):
        if self._page is None:
            raise RuntimeError("Browser not launched")
        return self._page

    async def launch(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.infra.headless)
        ctx_opts = {
            "user_agent": self.infra.user_agent,
            "viewport": {"width": 1920, "height": 1080},
        }
        if self.infra.proxy:
            ctx_opts["proxy"] = {"server": self.infra.proxy}
        self._context = await self._browser.new_context(**ctx_opts)
        self._page = await self._context.new_page()
        
        # ✅ FIX: Use domcontentloaded instead of networkidle
        # Live map sites never reach networkidle because they poll continuously
        await self._page.goto(
            "https://globe.adsbexchange.com/",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        
        # ✅ Wait for map container to appear
        try:
            await self._page.wait_for_selector("#map_canvas, .leaflet-container", timeout=30000)
            logger.info("Map container detected, waiting for initial data load...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.warning(f"Could not detect map container: {e}")
            await asyncio.sleep(10)
            
        logger.info("Browser session established (Cloudflare bypass)")

    async def shutdown(self) -> None:
        for res in [self._page, self._context, self._browser, self._pw]:
            if res:
                try:
                    if res == self._pw:
                        await res.stop()
                    else:
                        await res.close()
                except Exception:
                    pass
        self._page = self._context = self._browser = self._pw = None


# ==============================================================================
# ADSB Data Fetcher (HTTP Interception + Browser Evaluate)
# ==============================================================================

class ADSBDataFetcher:
    """Intercepts HTTP responses and uses browser evaluate to fetch aircraft data."""

    def __init__(self, page, timing: TimingConfig, geo: GeoService) -> None:
        self.page = page
        self.timing = timing
        self.geo = geo
        self._latest_data: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._data_event = asyncio.Event()

    async def start_interception(self) -> None:
        """Install HTTP response listener."""
        async def on_response(response):
            url = response.url
            if any(keyword in url for keyword in ["aircraft.json", "globe_", "data/"]):
                try:
                    content_type = response.headers.get("content-type", "")
                    if "json" in content_type:
                        data = await response.json()
                        await self._process_data(data, url)
                    else:
                        logger.debug(f"Received binary response from {url}")
                except Exception as e:
                    logger.debug(f"Failed to process response from {url}: {e}")

        self.page.on("response", on_response)

    async def _process_data(self, data: Any, source_url: str) -> None:
        """Extract aircraft list from JSON data."""
        aircraft_list = []
        if isinstance(data, dict):
            for key in ("aircraft", "planes", "acList", "flights"):
                if key in data:
                    aircraft_list = data[key]
                    break
        if isinstance(data, list):
            aircraft_list = data

        if aircraft_list:
            async with self._lock:
                for ac in aircraft_list:
                    icao = ac.get("hex")
                    if icao:
                        self._latest_data[icao] = ac
                self._data_event.set()
                logger.info(f"Updated aircraft list from HTTP ({source_url}): {len(aircraft_list)} entries")

    async def fetch_via_browser_evaluate(self, bounds: GeoBounds) -> list[dict[str, Any]]:
        """Fetch aircraft data using browser's fetch API to bypass Cloudflare."""
        try:
            data = await self.page.evaluate("""
                async () => {
                    try {
                        const response = await fetch('/data/aircraft.json');
                        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                        return await response.json();
                    } catch (e) {
                        return null;
                    }
                }
            """)
            if data and isinstance(data, dict):
                aircraft_list = data.get("aircraft", [])
                if aircraft_list:
                    logger.info(f"Fetched {len(aircraft_list)} aircraft via browser evaluate")
                    return aircraft_list
        except Exception as e:
            logger.debug(f"Browser evaluate fetch failed: {e}")
        return []

    async def pan_map_to_bounds(self, bounds: GeoBounds) -> None:
        """Programmatically pan the map to cover the given bounds."""
        center_lat, center_lon = bounds.center()
        zoom = self.geo.bounds_to_zoom(bounds)
        try:
            await self.page.evaluate(f"""
                () => {{
                    if (window.tr && window.tr.map) {{
                        window.tr.map.setView([{center_lat}, {center_lon}], {zoom});
                    }}
                }}
            """)
            logger.debug(f"Panned map to {center_lat:.4f}, {center_lon:.4f} (zoom {zoom})")
            await asyncio.sleep(self.timing.map_load_wait)
        except Exception as e:
            logger.warning(f"Failed to pan map: {e}")

    async def get_latest_aircraft(self, timeout: float = 15.0) -> list[dict[str, Any]]:
        """Return latest aircraft list from intercepted data."""
        try:
            await asyncio.wait_for(self._data_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        async with self._lock:
            self._data_event.clear()
            return list(self._latest_data.values())


# ==============================================================================
# Accumulation Service
# ==============================================================================

class AccumulatorService:
    """Receives aircraft dicts, deduplicates by ICAO, writes new records."""

    def __init__(self, writer: JsonlWriter, known_icaos: set[str], infra: InfrastructureConfig) -> None:
        self.writer = writer
        self.known_icaos = known_icaos
        self.infra = infra

    async def process_batch(self, aircraft_batch: list[dict[str, Any]]) -> int:
        added = 0
        new_icaos = set()
        for ac in aircraft_batch:
            icao = ac.get("hex", "").strip()
            if not icao or icao in self.known_icaos:
                continue
            self.known_icaos.add(icao)
            new_icaos.add(icao)
            record = AircraftRecord(
                icao=icao,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                data=ac,
            )
            await self.writer.write(record)
            added += 1
        if new_icaos:
            await StateRecovery.update_cache(self.infra, new_icaos)
        return added


# ==============================================================================
# Main Orchestrator
# ==============================================================================

class ADSBOrchestrator:
    """Coordinates continuous fetching, filtering, and writing."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.stop_event = asyncio.Event()
        self.geo = GeoService()

    async def run(self) -> int:
        infra = self.config.infra
        infra.output_dir.mkdir(parents=True, exist_ok=True)
        self._install_signal_handlers()

        main_bounds = self.geo.compute_bounds_from_radius(
            self.config.grid.center_lat,
            self.config.grid.center_lon,
            self.config.grid.radius_km,
        )
        cells = self.geo.split_bounds(main_bounds, self.config.grid.rows, self.config.grid.cols)
        logger.info("Covering area with %d cells (%.2f km radius)", len(cells), self.config.grid.radius_km)

        known_icaos = await StateRecovery.load_completed_icaos(infra)
        logger.info("Known ICAOs loaded: %d", len(known_icaos))

        browser = BrowserManager(infra)
        try:
            await browser.launch()
            await asyncio.sleep(self.config.timing.map_load_wait)

            fetcher = ADSBDataFetcher(browser.page, self.config.timing, self.geo)
            await fetcher.start_interception()

            async with JsonlWriter(infra.results_file) as writer:
                accumulator = AccumulatorService(writer, known_icaos, infra)

                cycle_count = 0
                while not self.stop_event.is_set():
                    cycle_count += 1
                    total_added = 0

                    # Try fetching via browser evaluate first
                    aircraft = await fetcher.fetch_via_browser_evaluate(main_bounds)
                    
                    # If that fails, try panning map and waiting for HTTP interception
                    if not aircraft:
                        for cell_idx, cell in enumerate(cells, 1):
                            if self.stop_event.is_set():
                                break
                            logger.debug("Panning to cell %d/%d: %s", cell_idx, len(cells), cell.to_dict())
                            await fetcher.pan_map_to_bounds(cell)
                            
                            cell_aircraft = await fetcher.get_latest_aircraft(timeout=self.config.timing.fetch_interval)
                            if cell_aircraft:
                                filtered = [
                                    ac for ac in cell_aircraft
                                    if isinstance(ac, dict) and self.geo.point_in_bounds(
                                        ac.get("lat"), ac.get("lon"), cell
                                    )
                                ]
                                if filtered:
                                    added = await accumulator.process_batch(filtered)
                                    total_added += added
                                    logger.info("Cell %d: %d new aircraft (total %d in area)",
                                                cell_idx, added, len(known_icaos))
                    else:
                        # Filter aircraft by main bounds
                        filtered = [
                            ac for ac in aircraft
                            if isinstance(ac, dict) and self.geo.point_in_bounds(
                                ac.get("lat"), ac.get("lon"), main_bounds
                            )
                        ]
                        if filtered:
                            added = await accumulator.process_batch(filtered)
                            total_added += added
                            logger.info("Cycle %d: %d new aircraft (total known %d)",
                                        cycle_count, added, len(known_icaos))

                    logger.info("Cycle %d complete. New aircraft this cycle: %d. Total known: %d",
                                cycle_count, total_added, len(known_icaos))
                    
                    try:
                        await asyncio.wait_for(
                            self.stop_event.wait(),
                            timeout=self.config.timing.fetch_interval,
                        )
                    except asyncio.TimeoutError:
                        continue

        finally:
            await browser.shutdown()
        logger.info("Orchestrator stopped gracefully.")
        return 0

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, self._request_stop)

    def _request_stop(self) -> None:
        if not self.stop_event.is_set():
            logger.warning("Stop signal received — finishing current cycle...")
            self.stop_event.set()


# ==============================================================================
# CLI
# ==============================================================================

def parse_args(argv: list[str] | None = None) -> Config:
    p = argparse.ArgumentParser(
        description="ADSBExchange area scraper — production edition v2.1.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--lat", type=float, default=60.17, help="Centre latitude")
    p.add_argument("--lon", type=float, default=24.94, help="Centre longitude")
    p.add_argument("--radius", type=float, default=300.0, help="Radius in km")
    p.add_argument("--rows", type=int, default=1, help="Grid rows (for sub-queries)")
    p.add_argument("--cols", type=int, default=1, help="Grid columns")
    p.add_argument("--interval", type=float, default=15.0, help="Fetch interval in seconds")
    p.add_argument("--retries", type=int, default=4, help="Max HTTP retries")
    p.add_argument("--output", type=Path, default=Path("adsb_output"), help="Output directory")
    p.add_argument("--no-headless", action="store_true", help="Show browser window")
    p.add_argument("--proxy", type=str, default=None, help="Proxy e.g. http://user:pass@host:port")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    a = p.parse_args(argv)
    a.output.mkdir(parents=True, exist_ok=True)
    _setup_logging(a.output, a.verbose)

    return Config(
        grid=GridConfig(
            center_lat=a.lat, center_lon=a.lon,
            radius_km=a.radius, rows=a.rows, cols=a.cols,
        ),
        timing=TimingConfig(
            fetch_interval=a.interval, max_retries=a.retries,
        ),
        infra=InfrastructureConfig(
            output_dir=a.output, headless=not a.no_headless, proxy=a.proxy
        ),
    )


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    orchestrator = ADSBOrchestrator(cfg)
    try:
        return asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.error("Fatal error: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())