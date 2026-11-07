"""Fetcher/strategy tests with a fake browser (no Playwright needed)."""

from __future__ import annotations

import json

from mtscraper.fetcher import (
    DirectTileStrategy,
    InterceptionTileStrategy,
    TileFetcher,
)
from mtscraper.models import GeoBounds, RawResponse

BOUNDS = GeoBounds(north=33.3, south=33.1, east=35.3, west=35.1)  # single tile

_TILE = {"data": {"rows": [{"SHIP_ID": "1", "LAT": "33.2", "LON": "35.2"}]}}
TILE_BODY = json.dumps(_TILE).encode()


def _resp(url: str, body: bytes = TILE_BODY, status: int = 200) -> RawResponse:
    return RawResponse(
        url=url,
        status=status,
        headers={"content-type": "application/json"},
        body=body,
        source="test",
    )


class FakeBrowser:
    """Minimal BrowserPort for exercising the fetcher."""

    def __init__(
        self, intercepted: list[RawResponse] | None = None, direct: RawResponse | None = None
    ) -> None:
        self._intercepted = intercepted or []
        self._direct = direct
        self.evaluate_calls: list[str] = []

    async def drain_intercepted(self) -> list[RawResponse]:
        drained, self._intercepted = self._intercepted, []
        return drained

    async def evaluate_fetch(
        self, endpoint: str, headers: dict[str, str] | None = None
    ) -> RawResponse | None:
        self.evaluate_calls.append(endpoint)
        return self._direct


async def test_interception_preferred_when_present() -> None:
    browser = FakeBrowser(intercepted=[_resp("t/z:7/X:38/Y:25")])
    fetcher = TileFetcher([InterceptionTileStrategy(browser)])
    outcomes = await fetcher.fetch()
    assert len(outcomes) == 1
    assert outcomes[0].payload["data"]["rows"][0]["SHIP_ID"] == "1"


async def test_falls_back_to_direct_when_interception_empty() -> None:
    browser = FakeBrowser(intercepted=[], direct=_resp("direct-tile"))
    fetcher = TileFetcher(
        [InterceptionTileStrategy(browser), DirectTileStrategy(browser, BOUNDS, 7)]
    )
    outcomes = await fetcher.fetch()
    assert len(outcomes) == 1
    assert browser.evaluate_calls  # direct strategy actually fetched a tile
    assert "get_data_json_4/z:7/X:38/Y:25" in browser.evaluate_calls[0]


async def test_non_2xx_tiles_are_skipped() -> None:
    browser = FakeBrowser(intercepted=[_resp("bad", status=403)])
    fetcher = TileFetcher([InterceptionTileStrategy(browser)])
    assert await fetcher.fetch() == []


async def test_empty_fetcher_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least one"):
        TileFetcher([])
