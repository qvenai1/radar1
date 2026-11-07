"""NOTIFY-2 — the Telegram gateway. The ONLY process that talks to Telegram, both ways.

Two concurrent tasks in one resident service:

    listen()  long-polls getUpdates and answers commands
    drain()   sends what the outbox owes, paced against the rate limit

⛔ WHY THESE TWO LIVE TOGETHER AND THE RUNNER NO LONGER SENDS. Telegram's limits are
per-BOT, not per-process: two processes sending concurrently would each obey a client-side
floor and together exceed the real ceiling, and neither could see the other's 429s. Putting
every outbound byte behind one paced client is the only arrangement in which the limit is
actually respected. It also gives the split its clean shape — `runner.py` became pure DB +
HTTP (decide events, enqueue) with no Telegram dependency at all, and everything that can
fail because of Telegram fails here.

⚠ RESIDENT, WHERE THE REST OF THE PROJECT IS ONESHOT TIMERS — a deliberate exception with a
measured justification. A oneshot cannot long-poll, so commands would only be answered once
per tick: a stranger sending /start would wait up to two minutes for any reply and conclude
the bot is dead. Cost measured before choosing it: 8.5 GiB available on this host, against
roughly 50 MB for one idle Python process holding a socket. §6.10's memory pressure is real
but this is not what threatens it.

⛔ DELIVERY IS AT-LEAST-ONCE, PER RECIPIENT. An outbox row is deleted only after that
person's send returns successfully (outbox.done). A crash mid-drain re-sends one message to
one subscriber; the alternative loses it silently for exactly the people it failed on.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import Any

from . import commands, outbox, render, subscribers as subs
from .config import NotifySettings, settings as default_settings
from .telegram import Telegram, TelegramBlocked, TelegramError

logger = logging.getLogger("scraplan.notify.gateway")

#: Messages pulled from the outbox per drain pass. Bounded so one enormous fan-out cannot
#: hold the loop for minutes without re-checking for new commands.
DRAIN_BATCH = 100

#: Pause between drain passes when the queue came back empty.
IDLE_SLEEP_S = 2.0


async def _summary_for(pool: Any, cfg: NotifySettings, sub: subs.Subscriber) -> str | None:
    """The "current picture" block sent right after /start.

    ⛔ A SUMMARY, NOT A BACKLOG. It reports COUNTS plus the highest-severity headline the
    subscriber's filters admit — it does not deliver the standing alerts themselves. The
    distinction is the whole cold-start rule applied to one person: the live feed carries
    ~225 alerts, ~200 of them maritime leads days old, and delivering those on /start would
    open every new subscription with a flood of old news.

    Filtered exactly as their feed will be, so the numbers a subscriber sees here are the
    numbers their subscription will actually produce."""
    from .runner import fetch_alerts

    alerts = await fetch_alerts(cfg.api_url, cfg.window_hours)
    mine = [a for a in alerts if subs.matches(a, sub.countries, sub.families)]
    if not mine:
        return ("📊 <b>الوضع الحالي</b>\n\nلا تنبيهات مطابقة لفلاترك في الوقت الحالي. "
                "سيصلك أوّل تحديث فور رصده.")

    by_family: dict[str, int] = {}
    for a in mine:
        by_family[a.family] = by_family.get(a.family, 0) + 1
    lines = [f"• {commands.FAMILY_AR.get(f, f)}: <b>{n}</b>"
             for f, n in sorted(by_family.items(), key=lambda kv: -kv[1])]

    top = sorted(mine, key=lambda a: {"high": 0, "medium": 1, "low": 2}.get(a.severity, 3))[0]
    head = render.esc(getattr(top, "headline", "") or getattr(top, "summary", ""))

    return ("📊 <b>الوضع الحالي</b> <i>(للعلم فقط — لن تُرسَل هذه التنبيهات إليك)</i>\n"
            + "\n".join(lines)
            + f"\n\n<b>أبرزها:</b> {head}"
            + "\n\n<i>من الآن ستصلك التحديثات الجديدة فقط.</i>")


async def listen(pool: Any, tg: Telegram, cfg: NotifySettings,
                 stop: asyncio.Event) -> None:
    """Long-poll for commands and answer them."""
    offset: int | None = await pool.fetchval(
        "SELECT update_offset FROM notify_runner_state WHERE id IS TRUE")
    await tg.set_my_commands(commands.COMMAND_MENU)
    logger.info("listening for commands (offset=%s)", offset)

    while not stop.is_set():
        try:
            updates = await tg.get_updates(offset)
        except Exception as exc:                      # noqa: BLE001
            logger.warning("getUpdates failed, retrying: %s", exc)
            await asyncio.sleep(5)
            continue

        for u in updates:
            offset = int(u["update_id"]) + 1
            msg = u.get("message") or {}
            chat = (msg.get("chat") or {}).get("id")
            text = msg.get("text") or ""
            if chat is None or not text.startswith("/"):
                # Not a command. Ignored deliberately: this bot has no conversational
                # surface beyond its commands, and inventing one would be a second
                # interface nobody specified.
                continue
            now = datetime.now(timezone.utc)
            try:
                replies = await commands.handle(
                    pool, str(chat), text, now,
                    summary_fn=lambda s: _summary_for(pool, cfg, s))
            except Exception as exc:                  # noqa: BLE001
                logger.exception("command %r from %s failed: %s", text, chat, exc)
                replies = ["حدث خطأ غير متوقّع. حاول مرّة أخرى، أو أرسل /help."]
            for body in replies:
                try:
                    await send_message(tg, str(chat), body)
                except TelegramBlocked:
                    # They blocked the bot between sending a command and our reply.
                    await _handle_blocked(pool, str(chat))
                    break
                except TelegramError as exc:
                    logger.warning("reply to %s failed: %s", chat, exc)
                    break

        if updates:
            # ⛔ Persisted AFTER the batch is handled. Telegram treats a new offset as an
            # acknowledgement, so saving it earlier would discard commands we had not yet
            # answered — a subscriber's /start silently lost to a restart.
            await pool.execute(
                "UPDATE notify_runner_state SET update_offset = $1 WHERE id IS TRUE", offset)


async def send_message(tg: Telegram, chat_id: str, body: str) -> None:
    """Send one logical message, split into as many Telegram messages as it needs.

    ⛔ THE ONLY WAY EITHER PATH IS ALLOWED TO SEND. Both the command replies and the outbox
    drain go through here, because they diverged once and it reached production: `listen`
    chunked, `drain` sent `item.body` raw, and the first hourly digest — 12,404 characters
    against Telegram's 4,096 limit — failed with `400: message is too long` and sat in the
    queue burning retries. The bodies are built by the same renderer and are bounded by
    nothing, so ANY path that sends one must chunk it; making that structurally impossible
    to forget is the fix, not remembering harder.

    ⚠ Chunked here rather than at enqueue time, deliberately. One outbox row stays one
    logical message, so its parts are always delivered in order and a retry re-sends the
    whole thing. That can duplicate earlier parts on a mid-message failure, which is exactly
    the trade this layer already declares: at-least-once, because "a duplicate is noticed
    and forgiven; a hole is neither". Splitting into independently-retried rows would fix
    the duplicate and break the ordering, which is the worse of the two for a reader."""
    for part in render.chunk(body):
        await tg.send(chat_id, part)


async def _handle_blocked(pool: Any, chat_id: str) -> None:
    """Telegram says this recipient is gone. Stop owing them anything.

    Marked `blocked`, never `stopped`: the user did not tell us to stop, the API told us
    they are unreachable, and an operator reading the table must be able to tell those
    apart. Their queue is discarded because retrying a 403 burns rate limit that live
    subscribers are waiting behind, and it can never succeed."""
    now = datetime.now(timezone.utc)
    dropped = await outbox.drop_for(pool, chat_id)
    await subs.set_status(pool, chat_id, subs.BLOCKED, now,
                          error="telegram: recipient unreachable (403)")
    logger.info("chat %s blocked the bot — unsubscribed, %d queued message(s) dropped",
                chat_id, dropped)


async def drain(pool: Any, tg: Telegram, stop: asyncio.Event) -> None:
    """Send what the outbox owes."""
    while not stop.is_set():
        now = datetime.now(timezone.utc)
        try:
            items = await outbox.due(pool, now, DRAIN_BATCH)
        except Exception as exc:                      # noqa: BLE001
            logger.warning("outbox read failed: %s", exc)
            await asyncio.sleep(IDLE_SLEEP_S)
            continue

        if not items:
            await asyncio.sleep(IDLE_SLEEP_S)
            continue

        for item in items:
            if stop.is_set():
                break
            try:
                await send_message(tg, item.chat_id, item.body)
            except TelegramBlocked:
                await _handle_blocked(pool, item.chat_id)
                continue
            except TelegramError as exc:
                # ⛔ NOT deleted — the debt stands and retries on its own backoff.
                gone = await outbox.fail(pool, item, str(exc), datetime.now(timezone.utc))
                if gone:
                    logger.error("giving up on %s for %s after %d attempts: %s",
                                 item.dedup_key, item.chat_id, outbox.MAX_ATTEMPTS, exc)
                continue
            await outbox.done(pool, item.id)
            await subs.note_sent(pool, item.chat_id, datetime.now(timezone.utc))


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = default_settings
    if not cfg.token:
        logger.error("TELEGRAM_BOT_TOKEN is not set — see .env.example. Doing nothing.")
        return 2
    if not cfg.database_url:
        logger.error("DATABASE_URL is not set — the subscriber store is unreachable.")
        return 2

    import asyncpg
    pool = await asyncpg.create_pool(cfg.database_url, min_size=1, max_size=4,
                                     command_timeout=30)
    stop = asyncio.Event()
    try:
        async with Telegram(cfg.token, min_interval_s=cfg.min_interval_s) as tg:
            # ⚠ UPGRADE PATH from the single-recipient bot (NOTIFY-1). TELEGRAM_CHAT_ID used
            # to BE the audience; it is now just an operator chat. Seeding it as a
            # subscriber means an existing install keeps receiving after the upgrade
            # without anyone having to notice they must now send /start.
            # Idempotent: subscribe() leaves an already-active row untouched, including its
            # cursor, so a restart cannot resurrect someone who chose /stop.
            if cfg.chat_id:
                existing = await subs.get(pool, cfg.chat_id)
                if existing is None:
                    await subs.subscribe(pool, cfg.chat_id, datetime.now(timezone.utc))
                    logger.info("seeded TELEGRAM_CHAT_ID %s as a subscriber (upgrade path)",
                                cfg.chat_id)

            counts = await subs.counts(pool)
            logger.info("gateway up — subscribers: %s, queue depth: %d",
                        counts or "none yet", await outbox.depth(pool))
            tasks = [asyncio.create_task(listen(pool, tg, cfg, stop), name="listen"),
                     asyncio.create_task(drain(pool, tg, stop), name="drain")]
            # If either task dies the service must exit and let systemd restart it, rather
            # than run half-alive — a gateway that drains but stops listening looks healthy
            # while silently refusing every new subscription.
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            stop.set()
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            for t in done:
                exc = t.exception()
                if exc:
                    logger.error("task %s died: %s", t.get_name(), exc)
                    return 1
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
