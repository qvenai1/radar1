"""NOTIFY-2 — the Telegram client. Both directions, one place.

In NOTIFY-1 this was send-only and bound to a single chat. A public subscription bot has to
READ (commands) and to send to MANY chats, so the class is now bound to the BOT rather than
to a chat, and `send` takes the recipient.

⛔ THE RATE LIMIT IS OBEYED, NOT GUESSED. Telegram publishes soft limits (~30 messages/second
for a bot overall, ~1/second to one chat) but the authoritative signal is the 429 it returns,
carrying `parameters.retry_after` — the exact number of seconds the server wants. Two
mechanisms, not redundant: a client-side floor that keeps normal operation away from the
limit, and full obedience to retry_after when we reach it anyway. With N subscribers a
single event is N messages, so this stopped being theoretical the moment the bot went public.

⛔ A BLOCKED USER IS A DIFFERENT OUTCOME FROM A FAILED SEND, and conflating them is how a
public bot degrades. When someone blocks the bot or deletes their account, Telegram answers
403 forever — retrying it burns rate limit that live subscribers are queued behind, and it
never succeeds. `TelegramBlocked` is raised for exactly those cases so the caller can
unsubscribe them instead of retrying, and it is deliberately NOT a subclass of
`TelegramError`: "this person is gone" must never be caught by a handler written for "the
network hiccuped".
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger("scraplan.notify.telegram")

API = "https://api.telegram.org"

#: Client-side floor between sends. Below Telegram's ~30/s bot ceiling with room to spare;
#: the 429 handler is the real limiter.
MIN_INTERVAL_S = 0.05

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2.0

#: A 429 asking for longer than this is not slept through — the message stays queued in the
#: outbox and is retried later, so one throttled send cannot stall the whole drain.
MAX_SLEEP_S = 15.0

REQUEST_TIMEOUT_S = 30.0

#: Long-poll duration for getUpdates. Telegram holds the connection open until an update
#: arrives or this elapses, so the listener costs one idle socket rather than a poll loop.
LONG_POLL_S = 30

#: 403 descriptions that mean "this recipient is permanently unreachable". Matched as
#: substrings because Telegram's wording varies by cause and has changed over time; the
#: STATUS is the real signal and these only classify which 403 it is.
_GONE_MARKERS = ("bot was blocked", "user is deactivated", "chat not found",
                 "bot was kicked", "peer_id_invalid", "user is deleted")


class TelegramError(RuntimeError):
    """A send that did not demonstrably succeed, and might succeed later."""


class TelegramBlocked(Exception):
    """The recipient is permanently unreachable — blocked, deactivated, or gone.

    ⚠ NOT a TelegramError. Retrying this is guaranteed to fail, so it must not be handled by
    retry logic; the caller unsubscribes instead."""

    def __init__(self, chat_id: str, description: str) -> None:
        super().__init__(f"chat {chat_id} unreachable: {description}")
        self.chat_id = chat_id
        self.description = description


class Telegram:
    """A Telegram bot client. Bound to the BOT, not to a chat."""

    def __init__(self, token: str, *, min_interval_s: float = MIN_INTERVAL_S,
                 session: aiohttp.ClientSession | None = None) -> None:
        if not token:
            # Fail at construction, not at the first send: a misconfigured bot must be loud
            # immediately, not after it has silently skipped an emergency.
            raise ValueError("telegram bot token is required")
        self._token = token
        self._min_interval = min_interval_s
        self._session = session
        self._owns_session = session is None
        self._last_send: float = 0.0
        self.sent = 0

    async def __aenter__(self) -> "Telegram":
        if self._session is None:
            self._session = aiohttp.ClientSession(
                # ⚠ The total timeout must exceed LONG_POLL_S or every long poll would be
                # cancelled by the client just before the server answers.
                timeout=aiohttp.ClientTimeout(total=LONG_POLL_S + REQUEST_TIMEOUT_S))
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _url(self, method: str) -> str:
        return f"{API}/bot{self._token}/{method}"

    async def _call(self, method: str, payload: dict, *, timeout: float | None = None) -> Any:
        """One API call. Returns `result`, or raises."""
        assert self._session is not None, "use Telegram as an async context manager"
        kw: dict[str, Any] = {"json": payload}
        if timeout is not None:
            kw["timeout"] = aiohttp.ClientTimeout(total=timeout)
        async with self._session.post(self._url(method), **kw) as resp:
            body = await resp.json(content_type=None)
            if resp.status == 200 and isinstance(body, dict) and body.get("ok"):
                return body.get("result")
            desc = (body or {}).get("description", str(body))
            raise _classify(resp.status, desc, body, payload.get("chat_id"))

    async def _pace(self) -> None:
        loop = asyncio.get_running_loop()
        wait = self._min_interval - (loop.time() - self._last_send)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send = loop.time()

    async def send(self, chat_id: str, text: str) -> None:
        """Send one message to one chat. Returns on confirmed delivery.

        ⚠ `disable_web_page_preview` is set because alert text carries vessel and aircraft
        identifiers that Telegram will happily turn into link previews, attaching an
        unrelated web page to an intelligence message."""
        if not text.strip():
            return
        payload = {"chat_id": str(chat_id), "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        last_err = "no attempt made"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._pace()
            try:
                await self._call("sendMessage", payload)
                self.sent += 1
                return
            except TelegramBlocked:
                raise                       # never retried — the caller unsubscribes
            except _RateLimited as exc:
                if exc.retry_after > MAX_SLEEP_S:
                    raise TelegramError(
                        f"rate limited for {exc.retry_after:.0f}s — deferred") from None
                logger.warning("429 — sleeping %.1fs as instructed", exc.retry_after)
                await asyncio.sleep(exc.retry_after)
                last_err = f"429 retry_after={exc.retry_after}"
                continue
            except TelegramError as exc:
                if not getattr(exc, "retryable", False):
                    raise               # 4xx we caused: bad HTML, revoked token. Fail now.
                last_err = str(exc)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(BACKOFF_BASE_S * (2 ** (attempt - 1)))
        raise TelegramError(f"gave up after {MAX_ATTEMPTS} attempts — {last_err}")

    async def get_updates(self, offset: int | None, *, timeout: int = LONG_POLL_S) -> list[dict]:
        """Long-poll for inbound updates.

        ⚠ `allowed_updates=["message"]` narrows what Telegram sends us to the only thing
        this bot acts on. It is a real reduction in exposure, not an optimisation: the bot
        never processes edited messages, channel posts, reactions, or inline queries, so it
        should not receive them."""
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        try:
            return await self._call("getUpdates", payload,
                                    timeout=timeout + REQUEST_TIMEOUT_S) or []
        except _RateLimited as exc:
            await asyncio.sleep(min(exc.retry_after, MAX_SLEEP_S))
            return []

    async def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
        """Register the command menu Telegram shows in the chat UI. Best-effort: a failure
        here costs discoverability, never delivery, so it must not stop the gateway."""
        try:
            await self._call("setMyCommands", {
                "commands": [{"command": c, "description": d} for c, d in commands]})
        except Exception as exc:              # noqa: BLE001
            logger.warning("setMyCommands failed (menu only, not fatal): %s", exc)


class _RateLimited(TelegramError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after
        self.retryable = True


def _classify(status: int, desc: str, body: Any, chat_id: Any) -> Exception:
    """HTTP status + description -> the right exception type.

    The classification is the point: `TelegramBlocked` ends a subscription, `_RateLimited`
    waits exactly as long as told, a 5xx retries, and any other 4xx is our own bug and fails
    immediately rather than burning attempts on something that cannot succeed."""
    low = (desc or "").lower()
    if status == 403 or any(m in low for m in _GONE_MARKERS):
        return TelegramBlocked(str(chat_id), desc)
    if status == 429:
        return _RateLimited(float((body or {}).get("parameters", {}).get("retry_after", 1)))
    err = TelegramError(f"http {status}: {desc}")
    err.retryable = status >= 500          # type: ignore[attr-defined]
    return err
