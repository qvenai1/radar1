"""Unit tests for the fetch strategy chain."""

from __future__ import annotations

import json

import pytest

from adsb_scraper.exceptions import BrowserCrashError, NoDataSourceError
from adsb_scraper.fetcher import Fetcher, OpenSkyRestStrategy
from adsb_scraper.models import GeoBounds, RawResponse

_JSON_BODY = b'{"aircraft":[]}'


class _Strategy:
    """A scriptable strategy. ``result`` is either a RawResponse, ``None``
    (empty), an Exception (raised), or the sentinel ``"return"`` for a good
    200 JSON response."""

    def __init__(self, name: str, *, available: bool = True, result: object = "return"):
        self._name = name
        self._available = available
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return self._available

    async def fetch(self) -> RawResponse | None:
        if isinstance(self._result, Exception):
            raise self._result
        if self._result == "return":
            return RawResponse(
                "u", 200, {"content-type": "application/json"}, _JSON_BODY, self._name
            )
        if isinstance(self._result, RawResponse):
            return self._result
        return None


def _resp(status: int, body: bytes, *, source: str, ctype: str = "text/html") -> RawResponse:
    return RawResponse("u", status, {"content-type": ctype}, body, source)


async def test_first_strategy_wins() -> None:
    fetcher = Fetcher([_Strategy("primary"), _Strategy("secondary")])
    outcome = await fetcher.fetch()
    assert outcome.response.source == "primary"
    assert outcome.payload == {"aircraft": []}


async def test_falls_through_on_error_and_empty() -> None:
    fetcher = Fetcher(
        [
            _Strategy("crashes", result=BrowserCrashError("boom")),
            _Strategy("empty", result=None),
            _Strategy("good"),
        ]
    )
    outcome = await fetcher.fetch()
    assert outcome.response.source == "good"


async def test_non_200_response_is_rejected_and_falls_through() -> None:
    """A 403 HTML page must not be accepted over a later good source."""
    bad = _Strategy("browser", result=_resp(403, b"Request forbidden", source="browser"))
    fetcher = Fetcher([bad, _Strategy("interception")])
    outcome = await fetcher.fetch()
    assert outcome.response.source == "interception"


async def test_undecodable_200_is_rejected_and_falls_through() -> None:
    """A 200 HTML page (decode failure) must fall through to the next source."""
    html = _Strategy("browser", result=_resp(200, b"<html>not json</html>", source="browser"))
    fetcher = Fetcher([html, _Strategy("interception")])
    outcome = await fetcher.fetch()
    assert outcome.response.source == "interception"


async def test_skips_unavailable() -> None:
    fetcher = Fetcher([_Strategy("off", available=False), _Strategy("on")])
    assert (await fetcher.fetch()).response.source == "on"


async def test_all_fail_raises_and_carries_rejected_responses() -> None:
    forbidden = _resp(403, b"Request forbidden by administrative rules.", source="browser")
    fetcher = Fetcher(
        [_Strategy("browser", result=forbidden), _Strategy("interception", result=None)]
    )
    with pytest.raises(NoDataSourceError) as info:
        await fetcher.fetch()
    # The 403 is preserved on the exception for the orchestrator to archive.
    assert any(r.status == 403 for r in info.value.responses)


async def test_unexpected_exception_not_swallowed() -> None:
    fetcher = Fetcher([_Strategy("boom", result=RuntimeError("bug"))])
    with pytest.raises(RuntimeError):
        await fetcher.fetch()


def test_empty_strategy_list_rejected() -> None:
    with pytest.raises(ValueError):
        Fetcher([])


def test_opensky_reshape_maps_positional_states() -> None:
    strategy = OpenSkyRestStrategy(GeoBounds(1, 0, 1, 0))
    raw = json.dumps(
        {"states": [["abcd12", "SWR22  ", "CH", 1, 1, 8.5, 47.1, 9000, False, 230, 180]]}
    ).encode()
    reshaped = json.loads(strategy._reshape(raw))
    state = reshaped["states"][0]
    assert state["icao24"] == "abcd12"
    assert state["latitude"] == 47.1 and state["longitude"] == 8.5
