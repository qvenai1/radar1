"""Shared test fakes and fixtures.

The doubles here are the payoff of the Protocol-first design: the browser,
sink, and clock are all replaced with tiny in-memory fakes so services can be
tested without Playwright, a filesystem, or wall-clock time.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
import zstandard

from adsb_scraper.area import AreaOfInterest, load_area_of_interest
from adsb_scraper.config import AreaConfig
from adsb_scraper.models import AircraftRecord, RawResponse
from adsb_scraper.validation import CollectionGap


def make_area_of_interest(buffer_km: float = 50.0) -> AreaOfInterest:
    """Load the real committed AOI asset (7 countries) for tests."""
    return load_area_of_interest(AreaConfig().asset_path, buffer_km)


class FrozenClock:
    """Deterministic :class:`~adsb_scraper.utils.Clock`."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start
        self._now = datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self._t

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._t += seconds


class FakeSink:
    """In-memory :class:`~adsb_scraper.persistence.AircraftSink`."""

    def __init__(self) -> None:
        self.records: list[AircraftRecord] = []
        self.flushes = 0
        self.closed = False

    async def write(self, record: AircraftRecord) -> None:
        self.records.append(record)

    async def flush(self) -> None:
        self.flushes += 1

    async def aclose(self) -> None:
        self.closed = True

    @property
    def total_written(self) -> int:
        return len(self.records)


class FakeBrowser:
    """Scriptable :class:`~adsb_scraper.browser.BrowserPort` double."""

    def __init__(
        self, payload: Mapping[str, object] | None = None, *, gzip_body: bool = True
    ) -> None:
        self.launched = False
        self.reloads = 0
        self.restarts = 0
        self.healthy = True
        self.shutdown_called = False
        self._payload = payload if payload is not None else {"aircraft": []}
        self._gzip = gzip_body
        self.fail_launch = False
        self.crash_on_fetch = False
        self.raw_override: bytes | None = None  # force a non-decodable body
        # Scriptable evaluate_fetch outcome and a separate interception source.
        self.evaluate_status = 200
        self.intercepted: Mapping[str, object] | None = None
        self.intercept_zstd = False  # serve interception body as application/zstd
        self.evaluate_calls = 0
        self.intercept_calls = 0
        self.pan_calls = 0
        self.panned_to: tuple[float, float, int] | None = None

    def set_payload(self, payload: Mapping[str, object]) -> None:
        self._payload = payload

    def _gzip_response(self, payload: Mapping[str, object], source: str) -> RawResponse:
        raw = json.dumps(payload).encode("utf-8")
        body = gzip.compress(raw) if self._gzip else raw
        headers = {"content-type": "application/octet-stream"}
        if self._gzip:
            headers["content-encoding"] = "gzip"
        return RawResponse(url="fake", status=200, headers=headers, body=body, source=source)

    @staticmethod
    def _zstd_response(payload: Mapping[str, object], source: str) -> RawResponse:
        body = zstandard.ZstdCompressor().compress(json.dumps(payload).encode("utf-8"))
        return RawResponse(
            url="fake",
            status=200,
            headers={"content-type": "application/zstd"},
            body=body,
            source=source,
        )

    async def launch(self) -> None:
        if self.fail_launch:
            from adsb_scraper.exceptions import BrowserLaunchError

            raise BrowserLaunchError("scripted launch failure")
        self.launched = True

    async def shutdown(self) -> None:
        self.shutdown_called = True
        self.launched = False

    async def reload(self) -> None:
        self.reloads += 1

    async def restart(self) -> None:
        self.restarts += 1
        self.healthy = True

    async def is_healthy(self) -> bool:
        return self.healthy

    async def evaluate_fetch(self, endpoint: str) -> RawResponse | None:
        self.evaluate_calls += 1
        if self.crash_on_fetch:
            from adsb_scraper.exceptions import BrowserCrashError

            raise BrowserCrashError("scripted crash")
        if self.raw_override is not None:
            # Non-200 and/or non-JSON body (e.g. a 403 HTML error page).
            return RawResponse(
                url=endpoint,
                status=self.evaluate_status,
                headers={"content-type": "text/html"},
                body=self.raw_override,
                source="browser_evaluate",
            )
        return self._gzip_response(self._payload, "browser_evaluate")

    async def latest_intercepted(self) -> RawResponse | None:
        self.intercept_calls += 1
        if self.intercepted is None:
            return None
        if self.intercept_zstd:
            return self._zstd_response(self.intercepted, "interception")
        return self._gzip_response(self.intercepted, "interception")

    async def pan_to(self, lat: float, lon: float, zoom: int) -> bool:
        self.pan_calls += 1
        self.panned_to = (lat, lon, zoom)
        return True

    async def capture_html(self) -> str | None:
        return "<html></html>"

    async def detect_cloudflare(self) -> bool:
        return False


class FakeGapJournal:
    """In-memory :class:`~adsb_scraper.validation.GapJournal` for tests."""

    def __init__(self, seed: datetime | None = None) -> None:
        self._seed = seed
        self.cycles: list[datetime] = []
        self.gaps: list[CollectionGap] = []

    def last_cycle_at(self) -> datetime | None:
        return self._seed

    async def record_cycle(self, when: datetime) -> None:
        self.cycles.append(when)

    async def record_gap(self, gap: CollectionGap) -> None:
        self.gaps.append(gap)


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def fake_sink() -> FakeSink:
    return FakeSink()


@pytest.fixture
def fake_browser() -> FakeBrowser:
    return FakeBrowser()
