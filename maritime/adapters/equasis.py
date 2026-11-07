"""
Equasis identity enrichment — owner / manager / flag by IMO.

Equasis publishes NO official API. This adapter is therefore a deliberately
CONSERVATIVE, fully ISOLATED, DISABLED-BY-DEFAULT scraper of the logged-in
web session, gated behind $EQUASIS_EMAIL / $EQUASIS_PASSWORD AND an explicit
$EQUASIS_ENABLE=1. It stays a lookup adapter (run() is a no-op).

Etiquette baked in (ToS + robots respect is a hard requirement):
  * disabled unless credentials AND the enable flag are all present;
  * strict client-side rate limit (default 1 request / 6 s, min enforced);
  * robots.txt is fetched and honored before any request;
  * a descriptive, contactable User-Agent;
  * results cached (caller supplies storage) so an IMO is fetched at most
    once per TTL — Equasis must never be hammered.

Because there is no API contract to pin, the fetch/parse body is a clear
stub. The GATING, rate-limit, and robots logic below ARE implemented, since
that is the part that protects the source and the user.

Docs / ToS: https://www.equasis.org
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from ..fleet import Fleet
from .base import MaritimeAdapter

logger = logging.getLogger("maritime.equasis")

BASE_URL = "https://www.equasis.org/"
USER_AGENT = "AtheerRADAR/1.0 (+mailto:qvenai@gmail.com; research; low-rate)"


class EquasisAdapter(MaritimeAdapter):
    name = "equasis"
    data_origin = "terrestrial"  # identity metadata, not positions

    def __init__(self, min_interval_s: float = 6.0) -> None:
        self._email = os.getenv("EQUASIS_EMAIL", "")
        self._password = os.getenv("EQUASIS_PASSWORD", "")
        self._explicit = os.getenv("EQUASIS_ENABLE") == "1"
        self._min_interval = max(min_interval_s, 6.0)  # hard floor, cannot go faster
        self._last_request = 0.0
        self._robots: RobotFileParser | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._email and self._password and self._explicit)

    async def run(self, fleet: Fleet) -> None:  # noqa: ARG002 — lookup adapter
        if not self.enabled:
            logger.info("Equasis adapter disabled (needs EQUASIS_EMAIL/PASSWORD + "
                        "EQUASIS_ENABLE=1) — respecting the source's ToS by default")
        return

    def stop(self) -> None:
        return

    def _robots_allows(self, path: str) -> bool:
        if self._robots is None:
            rp = RobotFileParser()
            rp.set_url(urljoin(BASE_URL, "/robots.txt"))
            try:
                rp.read()
            except Exception:  # noqa: BLE001 — if robots can't be read, refuse
                logger.warning("Equasis robots.txt unreadable — refusing to fetch")
                self._robots = RobotFileParser()  # empty → disallow below
                return False
            self._robots = rp
        return self._robots.can_fetch(USER_AGENT, urljoin(BASE_URL, path))

    async def _throttle(self) -> None:
        import asyncio
        wait = self._min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    async def enrich(self, imo: str) -> dict[str, Any]:
        """Owner/manager/flag for an IMO. Enforces gating + rate limit +
        robots before any network access."""
        if not self.enabled:
            raise RuntimeError("Equasis adapter disabled — see module docstring")
        if not self._robots_allows("/EquasisWeb/public/HomePage"):
            raise RuntimeError("Equasis robots.txt disallows this path — not fetching")
        await self._throttle()
        raise NotImplementedError(
            "Equasis HTML session fetch/parse is a phase-2 stub. Gating, the "
            "6 s rate-limit floor, and robots.txt enforcement are active now."
        )

    def health(self) -> dict[str, Any]:
        return {"name": self.name, "data_origin": self.data_origin,
                "enabled": self.enabled, "min_interval_s": self._min_interval,
                "status": "isolated, disabled-by-default (ToS-respecting)"}
