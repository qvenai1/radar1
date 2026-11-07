"""Browser encapsulation.

Playwright lives *entirely* behind :class:`BrowserManager`. No raw ``Page``,
``Response`` or ``Browser`` object ever crosses this module's boundary — the
rest of the system depends only on :class:`BrowserPort`, a small high-level
Protocol. That is what makes the browser swappable for a fake in tests and what
lets the orchestrator reason about "the browser" without knowing Playwright
exists.

The manager also owns the operational muscle a 24/7 process needs:
health-checking, page reloads, full restarts, Cloudflare detection, and turning
intercepted network responses into transport-agnostic :class:`RawResponse`
value objects.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from . import constants as C
from .config import BrowserConfig
from .exceptions import BrowserCrashError, BrowserLaunchError, PageTimeoutError
from .logger import get_logger
from .models import RawResponse

if TYPE_CHECKING:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        Response,
    )

_log = get_logger("browser")

# JS that fetches an endpoint and returns status, headers, and base64 body. Base64
# keeps binary/compressed payloads intact across the JS→Python boundary, so the
# decoder — not the browser — decides how to interpret the bytes.
_FETCH_JS = """
async (endpoint) => {
    const resp = await fetch(endpoint, { cache: 'no-store', credentials: 'include' });
    const buffer = new Uint8Array(await resp.arrayBuffer());
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < buffer.length; i += chunk) {
        binary += String.fromCharCode.apply(null, buffer.subarray(i, i + chunk));
    }
    const headers = {};
    resp.headers.forEach((value, key) => { headers[key] = value; });
    return { status: resp.status, url: resp.url, headers: headers, bodyB64: btoa(binary) };
}
"""

_CLOUDFLARE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-chalScript",
    "cf_chl_opt",
    "challenge-platform",
    "attention required",
)


@runtime_checkable
class BrowserPort(Protocol):
    """The high-level browser surface the rest of the system relies on."""

    async def launch(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def reload(self) -> None: ...
    async def restart(self) -> None: ...
    async def is_healthy(self) -> bool: ...
    async def evaluate_fetch(self, endpoint: str) -> RawResponse | None: ...
    async def latest_intercepted(self) -> RawResponse | None: ...
    async def pan_to(self, lat: float, lon: float, zoom: int) -> bool: ...
    async def capture_html(self) -> str | None: ...
    async def detect_cloudflare(self) -> bool: ...


class BrowserManager:
    """Concrete :class:`BrowserPort` backed by Playwright/Chromium."""

    def __init__(
        self, cfg: BrowserConfig, initial_view: tuple[float, float, int] | None = None
    ) -> None:
        self._cfg = cfg
        # (lat, lon, zoom) applied via URL params on load so the very first
        # data fetch is already over the target area (see _target_url).
        self._initial_view = initial_view
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._latest: RawResponse | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle --------------------------------------------------------- #

    async def launch(self) -> None:
        """Start Chromium, open the globe, and wait for the map to mount."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - environment issue
            raise BrowserLaunchError("playwright is not installed") from exc

        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self._cfg.headless)
            self._context = await self._browser.new_context(
                user_agent=self._cfg.user_agent,
                viewport={
                    "width": self._cfg.viewport_width,
                    "height": self._cfg.viewport_height,
                },
                proxy={"server": self._cfg.proxy} if self._cfg.proxy else None,
            )
            self._page = await self._context.new_page()
            self._page.on("response", self._on_response)
            await self._page.goto(
                self._target_url(),
                wait_until="domcontentloaded",
                timeout=int(self._cfg.nav_timeout_s * 1000),
            )
        except Exception as exc:
            raise BrowserLaunchError(f"failed to launch browser: {exc}") from exc

        await self._wait_for_map()
        if await self.detect_cloudflare():
            _log.warning(
                "Cloudflare interstitial detected after launch; "
                "data fetches may fail until it clears"
            )
        _log.info("browser session established (headless=%s)", self._cfg.headless)

    async def shutdown(self) -> None:
        """Tear down every Playwright resource, tolerating partial state."""
        for closer, resource in (
            ("page", self._page),
            ("context", self._context),
            ("browser", self._browser),
        ):
            if resource is None:
                continue
            try:
                await resource.close()
            except Exception as exc:  # noqa: BLE001 - best-effort teardown
                _log.debug("error closing %s: %s", closer, exc)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as exc:  # noqa: BLE001
                _log.debug("error stopping playwright: %s", exc)
        self._pw = self._browser = self._context = self._page = None
        self._latest = None

    async def reload(self) -> None:
        page = self._require_page()
        try:
            await page.reload(
                wait_until="domcontentloaded",
                timeout=int(self._cfg.nav_timeout_s * 1000),
            )
        except Exception as exc:
            raise PageTimeoutError(f"page reload failed: {exc}") from exc
        await self._wait_for_map()
        _log.info("page reloaded")

    async def restart(self) -> None:
        _log.warning("restarting browser")
        await self.shutdown()
        await self.launch()

    # -- health ------------------------------------------------------------ #

    async def is_healthy(self) -> bool:
        """A liveness probe: can the page still evaluate trivial JS in time?"""
        if self._page is None or self._page.is_closed():
            return False
        try:
            result = await asyncio.wait_for(self._page.evaluate("1 + 1"), timeout=5.0)
        except Exception as exc:  # noqa: BLE001 - any failure = unhealthy (CancelledError still propagates)
            _log.debug("health probe failed: %s", exc)
            return False
        return bool(result == 2)

    async def detect_cloudflare(self) -> bool:
        page = self._page
        if page is None or page.is_closed():
            return False
        try:
            title = (await page.title()).lower()
            content = (await page.content()).lower()
        except Exception as exc:  # noqa: BLE001
            _log.debug("cloudflare probe failed: %s", exc)
            return False
        return any(
            marker.lower() in title or marker.lower() in content for marker in _CLOUDFLARE_MARKERS
        )

    # -- data operations --------------------------------------------------- #

    async def evaluate_fetch(self, endpoint: str) -> RawResponse | None:
        """Run ``fetch(endpoint)`` inside the page and capture the raw bytes."""
        page = self._require_page()
        try:
            result = await asyncio.wait_for(
                page.evaluate(_FETCH_JS, endpoint),
                timeout=self._cfg.nav_timeout_s,
            )
        except TimeoutError as exc:
            raise PageTimeoutError(f"evaluate_fetch timed out for {endpoint}") from exc
        except Exception as exc:
            raise BrowserCrashError(f"evaluate_fetch failed for {endpoint}: {exc}") from exc

        if not isinstance(result, dict) or "bodyB64" not in result:
            _log.debug("evaluate_fetch returned no usable payload for %s", endpoint)
            return None
        try:
            body = base64.b64decode(result["bodyB64"])
        except (ValueError, TypeError) as exc:
            _log.warning("evaluate_fetch base64 decode failed: %s", exc)
            return None
        headers = {str(k).lower(): str(v) for k, v in dict(result.get("headers", {})).items()}
        return RawResponse(
            url=str(result.get("url", endpoint)),
            status=int(result.get("status", 0)),
            headers=headers,
            body=body,
            source="browser_evaluate",
        )

    async def latest_intercepted(self) -> RawResponse | None:
        """Return (and consume) the most recent intercepted aircraft response."""
        async with self._lock:
            latest, self._latest = self._latest, None
            return latest

    async def pan_to(self, lat: float, lon: float, zoom: int) -> bool:
        """Recentre the tar1090 (OpenLayers) map on ``lat``/``lon`` at ``zoom``.

        tar1090 declares its map as ``let OLMap`` — a *lexical* global, so it is
        NOT a property of ``window``; it must be referenced by bare name. The
        ``typeof`` guard avoids a ReferenceError if the map hasn't mounted yet.
        Returns whether the recenter was applied.
        """
        page = self._require_page()
        script = (
            "([lat, lon, zoom]) => {"
            "  try {"
            "    const proj = (typeof ol !== 'undefined' ? ol : window.ol);"
            "    if (typeof OLMap !== 'undefined' && OLMap && OLMap.getView) {"
            "      OLMap.getView().setCenter(proj.proj.fromLonLat([lon, lat]));"
            "      OLMap.getView().setZoom(zoom); return true;"
            "    }"
            "    if (window.OLMap && window.OLMap.getView) {"
            "      window.OLMap.getView().setCenter(proj.proj.fromLonLat([lon, lat]));"
            "      window.OLMap.getView().setZoom(zoom); return true;"
            "    }"
            "  } catch (e) { return false; }"
            "  return false;"
            "}"
        )
        try:
            applied = bool(await page.evaluate(script, [lat, lon, zoom]))
        except Exception as exc:  # noqa: BLE001 - panning is best-effort
            _log.debug("pan_to failed (non-fatal): %s", exc)
            return False
        if not applied:
            _log.debug("pan_to: map object not ready; relying on URL view params")
        return applied

    async def capture_html(self) -> str | None:
        page = self._page
        if page is None or page.is_closed():
            return None
        try:
            return await page.content()
        except Exception as exc:  # noqa: BLE001
            _log.debug("capture_html failed: %s", exc)
            return None

    # -- internals --------------------------------------------------------- #

    async def _on_response(self, response: Response) -> None:
        """Playwright response hook: keep the latest aircraft-like payload.

        Converts the Playwright object into a :class:`RawResponse` here so that
        no framework type escapes the module.
        """
        url = response.url
        if not any(hint in url for hint in C.AIRCRAFT_URL_HINTS):
            return
        try:
            body = await response.body()
            headers = await response.all_headers()
        except Exception as exc:  # noqa: BLE001 - body may be unavailable; log, don't crash
            _log.debug("could not read intercepted body from %s: %s", url, exc)
            return
        snapshot = RawResponse(
            url=url,
            status=response.status,
            headers={k.lower(): v for k, v in headers.items()},
            body=body,
            source="interception",
        )
        async with self._lock:
            self._latest = snapshot

    async def _wait_for_map(self) -> None:
        page = self._require_page()
        selector = ", ".join(C.MAP_READY_SELECTORS)
        try:
            await page.wait_for_selector(selector, timeout=int(self._cfg.nav_timeout_s * 1000))
            _log.info("map container detected")
        except Exception as exc:  # noqa: BLE001 - proceed; fetch paths may still work
            _log.warning("map container not detected (%s); continuing", exc)

    def _target_url(self) -> str:
        """Globe URL with ``?lat=&lon=&zoom=`` so tar1090 opens over the target.

        tar1090 reads these query params on load, which is the most reliable way
        to set the initial view (it does not depend on any JS internals).
        """
        if self._initial_view is None:
            return self._cfg.globe_url
        lat, lon, zoom = self._initial_view
        separator = "&" if "?" in self._cfg.globe_url else "?"
        return f"{self._cfg.globe_url}{separator}lat={lat:.5f}&lon={lon:.5f}&zoom={zoom}"

    def _require_page(self) -> Page:
        if self._page is None or self._page.is_closed():
            raise BrowserCrashError("browser page is not available")
        return self._page
