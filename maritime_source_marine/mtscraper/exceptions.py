"""Exception hierarchy.

Every error the scraper raises deliberately descends from :class:`ScraperError`,
so a caller can distinguish "something we anticipated" from a genuine bug (which
is left to propagate as its native type).
"""

from __future__ import annotations


class ScraperError(Exception):
    """Base class for every error this package raises on purpose."""


class ConfigError(ScraperError):
    """Invalid or inconsistent configuration."""


class BrowserError(ScraperError):
    """Base for browser-lifecycle failures."""


class BrowserLaunchError(BrowserError):
    """Chromium could not be started or the page could not be opened."""


class BrowserCrashError(BrowserError):
    """The page/browser became unusable mid-operation."""


class PageTimeoutError(BrowserError):
    """A navigation or in-page evaluation exceeded its deadline."""


class CloudflareBlockedError(BrowserError):
    """A Cloudflare interstitial is standing between us and the data."""


class MalformedPayloadError(ScraperError):
    """Bytes arrived but could not be decoded/parsed into vessel rows."""


class NoDataSourceError(ScraperError):
    """No fetch strategy produced usable vessel data this cycle."""
