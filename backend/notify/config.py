"""NOTIFY-1 — bot configuration. Environment only, .env aware (same shape as backend/config.py).

Kept in the notify package rather than folded into backend.config.Settings on purpose: the
bot is a separate PROCESS with a separate lifecycle, and the API server has no business
failing to start because a bot token is malformed.

⛔ THE TOKEN IS NEVER LOGGED AND NEVER DEFAULTED. `enabled` is the explicit opt-in the rest
of this project already uses for credentialed adapters (see EQUASIS_ENABLE in .env.example):
with no token and chat id the bot is OFF, and it says so rather than half-running.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class NotifySettings:
    token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())

    #: ⚠ NOTIFY-2: no longer the audience. The audience is `notify_subscribers`, filled by
    #: /start. This is now an OPTIONAL operator chat, seeded as the first subscriber on a
    #: fresh install so an upgrade from the single-recipient bot keeps working without
    #: anyone having to re-subscribe by hand.
    chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", "").strip())

    #: Where to read the feed. The bot consumes the SAME public endpoint the dashboard
    #: does — deliberately. Importing backend.alerts directly would mean re-implementing
    #: the endpoint's ~200 lines of orchestration (live snapshot, four heterogeneous
    #: sources, per-family scoping and capping), which is a fork of a frozen contract that
    #: would drift the first time either side changed. One definition of an alert, read
    #: over HTTP.
    api_url: str = field(default_factory=lambda: os.environ.get(
        "NOTIFY_API_URL", "http://127.0.0.1:8000/api/v1/alerts").strip())

    #: Feed window. Matches the dashboard default so the bot and the UI answer the same
    #: question — a bot on a different window would report leads the operator cannot find.
    window_hours: int = field(default_factory=lambda: _i("NOTIFY_WINDOW_HOURS", 24))

    #: Client-side floor between sends; the 429 handler is the real limiter.
    min_interval_s: float = field(default_factory=lambda: _f("NOTIFY_MIN_INTERVAL_S", 1.0))

    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))

    @property
    def enabled(self) -> bool:
        """⚠ NOTIFY-2: the TOKEN alone enables the bot. It used to also require a chat id,
        because there was exactly one recipient; now recipients arrive by /start, and a bot
        with zero subscribers is a correct, running state — not a misconfiguration."""
        return bool(self.token)


settings = NotifySettings()
