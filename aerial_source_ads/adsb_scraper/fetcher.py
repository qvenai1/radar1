"""Data acquisition via interchangeable strategies.

The scraper must never be wedded to a single data source. Each way of obtaining
aircraft bytes is a :class:`FetchStrategy`; :class:`Fetcher` holds an *ordered*
list of them and returns the first non-empty :class:`RawResponse`, so a new
provider is added by dropping a class into the list at the composition root —
no existing code changes.

Provided strategies:

* :class:`BrowserEvaluateStrategy` — ``fetch()`` inside the page (primary; this
  is the path that actually defeats Cloudflare).
* :class:`InterceptionStrategy` — reuse a response the page fetched on its own.
* :class:`OpenSkyRestStrategy` — a completely different transport (HTTP/JSON via
  aiohttp) proving the abstraction holds across providers.
* :class:`AdsbExchangeApiStrategy` — the paid REST API, wired and ready.

Every strategy yields the *same* ``RawResponse`` contract, so the decoder and
parser downstream neither know nor care where the bytes came from.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .exceptions import MalformedPayloadError, NoDataSourceError, ScraperError
from .logger import get_logger
from .models import GeoBounds, RawResponse
from .parser import ResponseDecoder

if TYPE_CHECKING:
    from .browser import BrowserPort

_log = get_logger("fetcher")

_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """A response that passed acceptance, plus its already-decoded payload.

    Decoding happens inside the fetch fallback so that an undecodable response
    (a 403 HTML page, a Cloudflare interstitial) is treated as a *failed* source
    and the next strategy is tried within the same cycle — rather than being
    handed downstream as if it were data.
    """

    response: RawResponse
    payload: Any


@runtime_checkable
class FetchStrategy(Protocol):
    """One way of obtaining raw aircraft bytes."""

    @property
    def name(self) -> str: ...

    async def is_available(self) -> bool:
        """Cheap check for whether this strategy can run right now."""

    async def fetch(self) -> RawResponse | None:
        """Return a response, or ``None`` if this source had nothing."""


# --------------------------------------------------------------------------- #
# Browser-backed strategies
# --------------------------------------------------------------------------- #


class BrowserEvaluateStrategy:
    """Fetch the aircraft endpoint from inside the page's JS context."""

    __slots__ = ("_browser", "_endpoint")

    def __init__(self, browser: BrowserPort, endpoint: str) -> None:
        self._browser = browser
        self._endpoint = endpoint

    @property
    def name(self) -> str:
        return "browser_evaluate"

    async def is_available(self) -> bool:
        return True

    async def fetch(self) -> RawResponse | None:
        return await self._browser.evaluate_fetch(self._endpoint)


class InterceptionStrategy:
    """Reuse the most recent aircraft response the page fetched on its own."""

    __slots__ = ("_browser",)

    def __init__(self, browser: BrowserPort) -> None:
        self._browser = browser

    @property
    def name(self) -> str:
        return "interception"

    async def is_available(self) -> bool:
        return True

    async def fetch(self) -> RawResponse | None:
        return await self._browser.latest_intercepted()


# --------------------------------------------------------------------------- #
# HTTP/REST provider strategies (off by default; wired at the composition root)
# --------------------------------------------------------------------------- #


class OpenSkyRestStrategy:
    """Anonymous OpenSky Network REST provider.

    OpenSky returns positional ``states`` arrays; this strategy maps them into
    the object shape the adaptive parser already understands (``icao24``,
    ``latitude``, ...), so provider-specific layout knowledge stays here and the
    parser stays generic.
    """

    _STATE_FIELDS = (
        "icao24",
        "callsign",
        "origin_country",
        "time_position",
        "last_contact",
        "longitude",
        "latitude",
        "baro_altitude",
        "on_ground",
        "velocity",
        "true_track",
        "vertical_rate",
        "sensors",
        "geo_altitude",
        "squawk",
    )

    __slots__ = ("_base_url", "_bounds", "_timeout")

    def __init__(
        self,
        bounds: GeoBounds,
        base_url: str = "https://opensky-network.org/api/states/all",
        timeout_s: float = 15.0,
    ) -> None:
        self._bounds = bounds
        self._base_url = base_url
        self._timeout = timeout_s

    @property
    def name(self) -> str:
        return "opensky_rest"

    async def is_available(self) -> bool:
        return True

    async def fetch(self) -> RawResponse | None:
        import aiohttp

        params = {
            "lamin": self._bounds.south,
            "lamax": self._bounds.north,
            "lomin": self._bounds.west,
            "lomax": self._bounds.east,
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(self._base_url, params=params) as resp,
        ):
            raw = await resp.read()
            status = resp.status
            headers = {k.lower(): v for k, v in resp.headers.items()}
        body = self._reshape(raw)
        return RawResponse(
            url=self._base_url, status=status, headers=headers, body=body, source=self.name
        )

    def _reshape(self, raw: bytes) -> bytes:
        """Convert OpenSky positional states into aircraft objects."""
        try:
            payload = json.loads(raw)
            states = payload.get("states") or []
        except (json.JSONDecodeError, AttributeError):
            return raw  # let the decoder/parser surface the problem downstream
        objects = [
            dict(zip(self._STATE_FIELDS, state, strict=False))
            for state in states
            if isinstance(state, list)
        ]
        return json.dumps({"states": objects}).encode("utf-8")


class AdsbExchangeApiStrategy:
    """Official ADS-B Exchange REST API (RapidAPI), keyed and ready to wire."""

    __slots__ = ("_api_key", "_bounds", "_host", "_timeout")

    def __init__(
        self,
        bounds: GeoBounds,
        api_key: str,
        host: str = "adsbexchange-com1.p.rapidapi.com",
        timeout_s: float = 15.0,
    ) -> None:
        self._bounds = bounds
        self._api_key = api_key
        self._host = host
        self._timeout = timeout_s

    @property
    def name(self) -> str:
        return "adsbx_api"

    async def is_available(self) -> bool:
        return bool(self._api_key)

    async def fetch(self) -> RawResponse | None:
        import aiohttp

        lat, lon = self._bounds.center
        # Radius the API understands (nautical miles), derived from the box height.
        radius_nm = max(1, int((self._bounds.north - self._bounds.south) * 60 / 2))
        url = f"https://{self._host}/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}/"
        headers = {"X-RapidAPI-Key": self._api_key, "X-RapidAPI-Host": self._host}
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url, headers=headers) as resp,
        ):
            body = await resp.read()
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        return RawResponse(
            url=url, status=status, headers=resp_headers, body=body, source=self.name
        )


# --------------------------------------------------------------------------- #
# Fetcher — ordered fallback over strategies
# --------------------------------------------------------------------------- #


class Fetcher:
    """Tries each strategy in order and returns the first *usable* payload.

    "Usable" means: the strategy returned a non-empty body, with a 2xx status,
    whose bytes decode to JSON. A source that fails any of these (a 403, an HTML
    error page, undecodable bytes) is skipped and the next strategy is tried
    immediately — the whole fallback happens within a single call, so one bad
    source never blocks a good one behind it.
    """

    def __init__(
        self, strategies: Sequence[FetchStrategy], decoder: ResponseDecoder | None = None
    ) -> None:
        if not strategies:
            raise ValueError("Fetcher requires at least one strategy")
        self._strategies: list[FetchStrategy] = list(strategies)
        self._decoder = decoder or ResponseDecoder()

    @property
    def strategy_names(self) -> list[str]:
        return [s.name for s in self._strategies]

    @staticmethod
    def _status_ok(status: int) -> bool:
        return _HTTP_OK_MIN <= status < _HTTP_OK_MAX

    async def fetch(self) -> FetchOutcome:
        """Return the first strategy that yields a decodable 2xx payload.

        Raises :class:`NoDataSourceError` (carrying every rejected response, for
        diagnostics) if no strategy produces usable data. Unexpected exceptions
        are *not* caught — they indicate a bug, not a data outage.
        """
        failures: list[str] = []
        rejected: list[RawResponse] = []
        for strategy in self._strategies:
            try:
                if not await strategy.is_available():
                    failures.append(f"{strategy.name}: unavailable")
                    continue
                response = await strategy.fetch()
            except ScraperError as exc:
                _log.warning("strategy %s failed: %s", strategy.name, exc)
                failures.append(f"{strategy.name}: {exc}")
                continue

            if response is None or not response.body:
                failures.append(f"{strategy.name}: empty")
                continue
            if not self._status_ok(response.status):
                _log.warning(
                    "strategy %s returned HTTP %d; falling through", strategy.name, response.status
                )
                failures.append(f"{strategy.name}: HTTP {response.status}")
                rejected.append(response)
                continue
            try:
                payload = self._decoder.decode(response)
            except MalformedPayloadError as exc:
                _log.warning(
                    "strategy %s payload undecodable; falling through: %s", strategy.name, exc
                )
                failures.append(f"{strategy.name}: undecodable")
                rejected.append(response)
                continue

            _log.debug(
                "strategy %s accepted (%d bytes, HTTP %d)",
                strategy.name,
                response.size,
                response.status,
            )
            return FetchOutcome(response=response, payload=payload)

        raise NoDataSourceError(f"all strategies failed: {failures}", responses=rejected)
