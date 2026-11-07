"""Browser encapsulation.

Playwright lives *entirely* behind :class:`BrowserManager`; no raw ``Page`` or
``Response`` ever crosses this boundary. The rest of the system depends only on
:class:`BrowserPort`, a small high-level Protocol — which is what makes the
browser swappable for a fake in tests.

The manager loads the MarineTraffic live map (which establishes the Cloudflare
session), then harvests the ``get_data_json_4`` tile responses the page fetches
on its own. Headful is the default because headless Chromium reliably trips
Cloudflare's "attention required" interstitial on this site.
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
from .session import SessionRotator

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright, Response

_log = get_logger("browser")

# Fetch an endpoint from inside the page and return status/headers/base64 body.
# Base64 preserves binary/compressed payloads across the JS->Python boundary.
# ``reqHeaders`` lets the caller add request headers — the vessel-details
# endpoint, for one, returns a 400/404 unless ``X-Requested-With`` is present.
_FETCH_JS = """
async ({ endpoint, reqHeaders }) => {
    const resp = await fetch(endpoint, {
        cache: 'no-store', credentials: 'include', headers: reqHeaders || {},
    });
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


@runtime_checkable
class BrowserPort(Protocol):
    """The high-level browser surface the rest of the system relies on."""

    async def launch(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def reload(self) -> None: ...
    async def restart(self) -> None: ...
    async def is_healthy(self) -> bool: ...
    async def drain_intercepted(self) -> list[RawResponse]: ...
    async def evaluate_fetch(
        self, endpoint: str, headers: dict[str, str] | None = None
    ) -> RawResponse | None: ...
    async def capture_html(self) -> str | None: ...
    async def detect_cloudflare(self) -> bool: ...


class BrowserManager:
    """Concrete :class:`BrowserPort` backed by Playwright/Chromium."""

    def __init__(
        self, cfg: BrowserConfig, map_url: str, rotator: SessionRotator | None = None
    ) -> None:
        self._cfg = cfg
        self._map_url = map_url
        # When present, the rotator supplies the proxy + profile at launch time,
        # so a restart after a rotate() comes up as a fresh identity.
        self._rotator = rotator
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        # Latest tile response per URL, collected since the last drain.
        self._tiles: dict[str, RawResponse] = {}
        self._lock = asyncio.Lock()

    # -- lifecycle --------------------------------------------------------- #

    async def launch(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - environment issue
            raise BrowserLaunchError("playwright is not installed") from exc

        args = ["--disable-blink-features=AutomationControlled"]
        # Effective identity: the rotator (if any) overrides the static config.
        proxy = self._cfg.proxy
        profile = self._cfg.user_data_dir
        if self._rotator is not None:
            session = self._rotator.current()
            proxy = session.proxy
            profile = session.profile_dir
            _log.info("using session %s (proxy=%s)", session.label, "yes" if proxy else "none")
        try:
            self._pw = await async_playwright().start()
            if profile:
                # A persistent profile keeps the Cloudflare cf_clearance cookie, so
                # the challenge is solved once and reused across runs. There is no
                # separate Browser object in this mode — the context owns it.
                self._context = await self._pw.chromium.launch_persistent_context(
                    profile,
                    headless=self._cfg.headless,
                    args=args,
                    user_agent=self._cfg.user_agent,
                    viewport={
                        "width": self._cfg.viewport_width,
                        "height": self._cfg.viewport_height,
                    },
                    locale=self._cfg.locale,
                    timezone_id=self._cfg.timezone_id,
                    proxy={"server": proxy} if proxy else None,
                )
                self._browser = None
                self._page = self._context.pages[0] if self._context.pages else (
                    await self._context.new_page()
                )
            else:
                self._browser = await self._pw.chromium.launch(
                    headless=self._cfg.headless, args=args
                )
                self._context = await self._browser.new_context(
                    user_agent=self._cfg.user_agent,
                    viewport={
                        "width": self._cfg.viewport_width,
                        "height": self._cfg.viewport_height,
                    },
                    locale=self._cfg.locale,
                    timezone_id=self._cfg.timezone_id,
                    proxy={"server": proxy} if proxy else None,
                )
                self._page = await self._context.new_page()
            self._page.on("response", self._on_response)
            await self._page.goto(
                self._map_url,
                wait_until="domcontentloaded",
                timeout=int(self._cfg.nav_timeout_s * 1000),
            )
        except Exception as exc:
            raise BrowserLaunchError(f"failed to launch browser: {exc}") from exc

        await self._wait_for_map()
        await self._wait_challenge_clear("launch")
        _log.info(
            "browser session established (headless=%s, persistent=%s)",
            self._cfg.headless,
            bool(profile),
        )

    async def shutdown(self) -> None:
        for label, resource in (
            ("page", self._page),
            ("context", self._context),
            ("browser", self._browser),
        ):
            if resource is None:
                continue
            try:
                await resource.close()
            except Exception as exc:
                _log.debug("error closing %s: %s", label, exc)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as exc:
                _log.debug("error stopping playwright: %s", exc)
        self._pw = self._browser = self._context = self._page = None
        self._tiles.clear()

    async def reload(self) -> None:
        page = self._require_page()
        try:
            await page.reload(
                wait_until="domcontentloaded", timeout=int(self._cfg.nav_timeout_s * 1000)
            )
        except Exception as exc:
            raise PageTimeoutError(f"page reload failed: {exc}") from exc
        await self._wait_for_map()
        await self._wait_challenge_clear("reload")
        _log.info("page reloaded")

    async def restart(self) -> None:
        _log.warning("restarting browser")
        await self.shutdown()
        await self.launch()

    # -- health ------------------------------------------------------------ #

    async def is_healthy(self) -> bool:
        if self._page is None or self._page.is_closed():
            return False
        try:
            result = await asyncio.wait_for(self._page.evaluate("1 + 1"), timeout=5.0)
        except Exception as exc:
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
        except Exception as exc:
            _log.debug("cloudflare probe failed: %s", exc)
            return False
        # A genuine block shows a Cloudflare title AND/OR the challenge bootstrap
        # object in the body. The background ``challenge-platform`` telemetry
        # script is deliberately NOT treated as a block — it loads on every
        # Cloudflare-fronted page, working or not.
        if any(m in title for m in C.CLOUDFLARE_TITLE_MARKERS):
            return True
        return C.CLOUDFLARE_BODY_TOKEN in content

    # -- data operations --------------------------------------------------- #

    async def drain_intercepted(self) -> list[RawResponse]:
        """Return (and clear) every tile response collected since last drain."""
        async with self._lock:
            drained = list(self._tiles.values())
            self._tiles.clear()
            return drained

    async def evaluate_fetch(
        self, endpoint: str, headers: dict[str, str] | None = None
    ) -> RawResponse | None:
        """Run ``fetch(endpoint)`` inside the page and capture the raw bytes."""
        page = self._require_page()
        try:
            result = await asyncio.wait_for(
                page.evaluate(_FETCH_JS, {"endpoint": endpoint, "reqHeaders": headers or {}}),
                timeout=self._cfg.nav_timeout_s,
            )
        except TimeoutError as exc:
            raise PageTimeoutError(f"evaluate_fetch timed out for {endpoint}") from exc
        except Exception as exc:
            raise BrowserCrashError(f"evaluate_fetch failed for {endpoint}: {exc}") from exc

        if not isinstance(result, dict) or "bodyB64" not in result:
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
            source="direct_tile",
        )

    async def capture_html(self) -> str | None:
        page = self._page
        if page is None or page.is_closed():
            return None
        try:
            return await page.content()
        except Exception as exc:
            _log.debug("capture_html failed: %s", exc)
            return None

    # -- internals --------------------------------------------------------- #

    async def _on_response(self, response: Response) -> None:
        url = response.url
        if not any(hint in url for hint in C.VESSEL_URL_HINTS):
            return
        try:
            body = await response.body()
            headers = await response.all_headers()
        except Exception as exc:
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
            # Key by the tile path (drop query) so a re-fetch of the same tile
            # within a cycle overwrites rather than duplicates.
            self._tiles[url.split("?")[0]] = snapshot

    async def _wait_challenge_clear(self, label: str) -> None:
        """Poll until a Cloudflare interstitial auto-clears (headful solves it).

        Non-fatal: if it does not clear within the budget, we log and proceed —
        a later cycle (or the recovery ladder) will deal with the empty result.
        """
        deadline = self._cfg.challenge_wait_s
        waited = 0.0
        step = 3.0
        while waited < deadline:
            if not await self.detect_cloudflare():
                if waited:
                    _log.info("Cloudflare challenge cleared after %.0fs (%s)", waited, label)
                return
            await asyncio.sleep(step)
            waited += step
        if await self.detect_cloudflare():
            _log.warning(
                "Cloudflare interstitial still present after %.0fs (%s); "
                "data may be blocked this cycle (persistent profile + headful help)",
                deadline,
                label,
            )

    async def _wait_for_map(self) -> None:
        page = self._require_page()
        selector = ", ".join(C.MAP_READY_SELECTORS)
        try:
            await page.wait_for_selector(selector, timeout=int(self._cfg.nav_timeout_s * 1000))
            _log.info("map container detected")
        except Exception as exc:
            _log.warning("map container not detected (%s); continuing", exc)

    def _require_page(self) -> Page:
        if self._page is None or self._page.is_closed():
            raise BrowserCrashError("browser page is not available")
        return self._page
