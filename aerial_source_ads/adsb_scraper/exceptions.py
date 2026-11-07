"""Custom exception hierarchy.

Every failure the scraper can raise descends from :class:`ScraperError`. This
lets the orchestrator distinguish *our* well-understood failures (which have a
defined recovery action) from genuinely unexpected ones (which must be logged
with a full traceback and, depending on policy, aborted).

The golden rule enforced across the codebase: **never** ``except Exception:
pass``. If an exception is caught it is either handled meaningfully or
re-raised, and it is always logged with context.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import RawResponse


class ScraperError(Exception):
    """Base class for every error raised intentionally by this package."""


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class ConfigError(ScraperError):
    """Configuration was missing, malformed, or failed validation."""


# --------------------------------------------------------------------------- #
# Browser layer
# --------------------------------------------------------------------------- #
class BrowserError(ScraperError):
    """Base class for browser-layer failures."""


class BrowserLaunchError(BrowserError):
    """The browser could not be started or navigated to the target site."""


class BrowserCrashError(BrowserError):
    """The browser or its page became unusable and must be restarted."""


class PageTimeoutError(BrowserError):
    """A page operation exceeded its allotted time."""


class CloudflareChallengeError(BrowserError):
    """A Cloudflare interstitial was detected and not cleared."""


# --------------------------------------------------------------------------- #
# Network / fetch layer
# --------------------------------------------------------------------------- #
class FetchError(ScraperError):
    """Base class for data-acquisition failures."""


class NoDataSourceError(FetchError):
    """Every configured fetch strategy failed to return usable data.

    Carries the responses that were rejected (e.g. a 403 HTML page) so the
    orchestrator can archive them for diagnostics — but only once the *whole*
    fallback chain has failed, never when a later strategy succeeds.
    """

    def __init__(self, message: str, responses: Sequence[RawResponse] = ()) -> None:
        super().__init__(message)
        self.responses: tuple[RawResponse, ...] = tuple(responses)


class ResponseDecodeError(FetchError):
    """A response body could not be decoded by any known method."""


# --------------------------------------------------------------------------- #
# Parser layer
# --------------------------------------------------------------------------- #
class ParserError(ScraperError):
    """Base class for parsing failures."""


class AircraftArrayNotFoundError(ParserError):
    """No aircraft array could be located in an otherwise-valid payload."""


class MalformedPayloadError(ParserError):
    """The payload was structurally unusable (e.g. not JSON at all)."""


class BinCraftError(ParserError):
    """A readsb/tar1090 binCraft binary buffer could not be decoded."""


# --------------------------------------------------------------------------- #
# Persistence layer
# --------------------------------------------------------------------------- #
class PersistenceError(ScraperError):
    """Base class for storage failures."""


class WriteError(PersistenceError):
    """A record could not be durably written."""


class CorruptionError(PersistenceError):
    """An existing data file failed an integrity check."""


# --------------------------------------------------------------------------- #
# Recovery layer
# --------------------------------------------------------------------------- #
class RecoveryError(ScraperError):
    """An automated recovery action failed to restore a healthy state."""
