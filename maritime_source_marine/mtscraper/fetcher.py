"""Acquire vessel-tile payloads via interchangeable strategies.

A cycle needs *all* the tiles covering the area, so — unlike a single-response
fetcher — each strategy returns a *list* of :class:`RawResponse`. :class:`TileFetcher`
tries the primary (interception) strategy first and falls back to the direct
strategy only if interception yielded nothing decodable this cycle.

* :class:`InterceptionTileStrategy` — harvest the tiles the map fetched itself.
* :class:`DirectTileStrategy` — compute the tiles covering the area and fetch
  each from inside the page session (reuses the browser's cookies/Cloudflare
  clearance), for when the map's own viewport did not cover everything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from . import constants as C
from .exceptions import MalformedPayloadError
from .logger import get_logger
from .models import GeoBounds, RawResponse
from .parser import ResponseDecoder
from .tiles import tiles_covering

if TYPE_CHECKING:
    from .browser import BrowserPort

_log = get_logger("fetcher")

_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """A single accepted tile response together with its decoded payload."""

    response: RawResponse
    payload: Any


@runtime_checkable
class TileStrategy(Protocol):
    """One way of obtaining a batch of vessel-tile responses."""

    @property
    def name(self) -> str: ...

    async def fetch(self) -> list[RawResponse]: ...


class InterceptionTileStrategy:
    """Drain the tiles the live map fetched on its own."""

    __slots__ = ("_browser",)

    def __init__(self, browser: BrowserPort) -> None:
        self._browser = browser

    @property
    def name(self) -> str:
        return "interception"

    async def fetch(self) -> list[RawResponse]:
        return await self._browser.drain_intercepted()


class DirectTileStrategy:
    """Fetch every tile covering the area directly from the page session."""

    __slots__ = ("_base_url", "_bounds", "_browser", "_max_tiles", "_zoom")

    def __init__(
        self,
        browser: BrowserPort,
        bounds: GeoBounds,
        zoom: int,
        base_url: str = C.DEFAULT_BASE_URL,
        max_tiles: int = 64,
    ) -> None:
        self._browser = browser
        self._bounds = bounds
        self._zoom = zoom
        self._base_url = base_url
        self._max_tiles = max_tiles

    @property
    def name(self) -> str:
        return "direct_tile"

    async def fetch(self) -> list[RawResponse]:
        tiles = tiles_covering(self._bounds, self._zoom)
        if len(tiles) > self._max_tiles:
            _log.warning(
                "area needs %d tiles at zoom %d; capping at %d (raise zoom or shrink area)",
                len(tiles),
                self._zoom,
                self._max_tiles,
            )
            tiles = tiles[: self._max_tiles]
        out: list[RawResponse] = []
        for tile in tiles:
            path = C.TILE_DATA_PATH.format(z=tile.z, x=tile.x, y=tile.y)
            resp = await self._browser.evaluate_fetch(f"{self._base_url}{path}")
            if resp is not None:
                out.append(resp)
        return out


class TileFetcher:
    """Try strategies in order; return the decoded payloads of the first that
    yields any usable (2xx, decodable) tile response."""

    def __init__(
        self, strategies: Sequence[TileStrategy], decoder: ResponseDecoder | None = None
    ) -> None:
        if not strategies:
            raise ValueError("TileFetcher requires at least one strategy")
        self._strategies = list(strategies)
        self._decoder = decoder or ResponseDecoder()

    @property
    def strategy_names(self) -> list[str]:
        return [s.name for s in self._strategies]

    @staticmethod
    def _status_ok(status: int) -> bool:
        return _HTTP_OK_MIN <= status < _HTTP_OK_MAX

    async def fetch(self) -> list[FetchOutcome]:
        """Return decoded outcomes from the first strategy that produces any."""
        for strategy in self._strategies:
            responses = await strategy.fetch()
            outcomes: list[FetchOutcome] = []
            for response in responses:
                if not response.body or not self._status_ok(response.status):
                    continue
                try:
                    payload = self._decoder.decode(response)
                except MalformedPayloadError as exc:
                    _log.debug("tile %s undecodable: %s", response.url, exc)
                    continue
                outcomes.append(FetchOutcome(response=response, payload=payload))
            if outcomes:
                _log.debug("strategy %s produced %d tiles", strategy.name, len(outcomes))
                return outcomes
        return []
