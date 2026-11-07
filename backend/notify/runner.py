"""NOTIFY-2 — the tick. Fetch the feed, decide what is an event, enqueue it to subscribers.

⛔ THIS PROCESS NO LONGER TALKS TO TELEGRAM AT ALL. In NOTIFY-1 it sent; now it only decides
and enqueues, and `gateway.py` owns every outbound byte. The reason is not tidiness:
Telegram's rate limits are per-BOT, so two processes sending concurrently would each respect
a client-side floor and together break the real ceiling, with neither able to see the
other's 429s. One paced sender is the only arrangement in which the limit is actually
obeyed. The side benefit is that this file became pure DB + HTTP, so nothing here can fail
because of Telegram.

ONE ONESHOT PER TICK, driven by a systemd timer. §6.10 of PROJECT_STATE records memory as
this host's tightest resource; a oneshot holds nothing between runs, and every tick is
independently crash-safe because all state is in the ledger and the outbox.

THE ORDER OF OPERATIONS, and why each step sits where it does:

  1. fetch + identify        the feed is the only source of what exists
  2. cold start?             baseline and STOP — never enqueue the standing picture
  3. decide                  per alert, against the GLOBAL ledger (is this an event?)
  4. touch_seen(ALL)         before anything else: absence must stay measurable even if a
                             later step fails, or a bad tick would expire live sessions
  5. fan out                 event x audience, filtered per subscriber, into the outbox
  6. account                 the global ledger records the event as decided
  7. digest                  if due, per distinct filter set
  8. prune                   bounded ledger

⛔ TWO QUESTIONS, TWO STATES, AND THEY ARE NOT THE SAME QUESTION.
    notify_deliveries  "is this an EVENT?"        global, decided once, unchanged from 039
    notify_outbox      "who still needs telling?" per recipient, retried per recipient
Conflating them is what breaks a fan-out: with one recipient the ledger could be written
after the send, but with many there is no single send to key that write on.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp

from ..models import Alert
from . import outbox, policy, render, state, subscribers as subs
from .config import NotifySettings, settings as default_settings
from .identity import identify

logger = logging.getLogger("scraplan.notify.runner")

FETCH_TIMEOUT_S = 60.0


@dataclass
class TickResult:
    """What one tick did — returned for logging and asserted on in tests."""
    fetched: int = 0
    cold_start: bool = False
    baselined: int = 0
    audience: int = 0
    events_instant: int = 0
    spilled: int = 0
    digest_sent: bool = False
    digest_items: int = 0
    enqueued: int = 0
    suppressed: int = 0
    pruned: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


async def fetch_alerts(url: str, window_hours: int,
                       session: aiohttp.ClientSession | None = None) -> list[Alert]:
    """Read the feed and parse it through the REAL Alert model.

    ⚠ Parsing into backend.models.Alert rather than handling raw dicts is a contract check,
    not a convenience: if the feed changes shape this raises here — loudly, in a oneshot
    systemd will report — instead of quietly producing messages with missing fields."""
    own = session is None
    session = session or aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S))
    try:
        async with session.get(url, params={"window_hours": window_hours}) as resp:
            resp.raise_for_status()
            body = await resp.json()
    finally:
        if own:
            await session.close()
    return [Alert(**a) for a in body.get("alerts", [])]


def _entries(alerts: list[Alert]) -> list[tuple[str, str, str, str]]:
    """(key, family, signal_type, sig) for the ledger."""
    out = []
    for a in alerts:
        key, sig = identify(a)
        out.append((key, a.family, a.signal_type, sig))
    return out


def fan_out(alerts: list[Alert], audience: list[subs.Subscriber],
            now: datetime) -> list[tuple[str, str, str]]:
    """(chat_id, dedup_key, body) for every subscriber each alert belongs to. PURE.

    ⚠ THE BODY IS RENDERED ONCE PER ALERT, NOT ONCE PER SUBSCRIBER. Filters decide WHO
    receives an alert, never WHAT it says — two subscribers who both qualify get identical
    text — so rendering per recipient would be pure waste at fan-out scale. (The digest is
    the exception and is handled separately: its content genuinely differs, because it is a
    summary OF the subscriber's own filtered set.)

    ⛔ The cursor check is the mid-tick race guard: someone who subscribed after this tick's
    clock was taken has not yet joined as far as this tick is concerned, and enqueuing to
    them would deliver an event decided before their subscription existed."""
    rows: list[tuple[str, str, str]] = []
    for a in alerts:
        key, _ = identify(a)
        body = render.render_alert(a)
        for sub in audience:
            if sub.cursor_at > now:
                continue
            if subs.matches(a, sub.countries, sub.families):
                rows.append((sub.chat_id, key, body))
    return rows


def fan_out_digest(alerts: list[Alert], audience: list[subs.Subscriber], now: datetime,
                   *, key: str, since: str | None = None,
                   spilled: int = 0) -> list[tuple[str, str, str]]:
    """A digest per subscriber, rendered once per DISTINCT FILTER SET.

    Unlike a single alert, a digest's content depends on the recipient — it summarises the
    subset THEY asked for. But two subscribers with the same filters get byte-identical
    text, so the render is memoised on the filter key rather than repeated per person.

    A subscriber whose filters admit nothing this period gets NO digest row at all: an empty
    digest is noise, and sending "nothing matched your filters" every hour is how a public
    bot earns a mute."""
    rendered: dict[tuple, str] = {}
    rows: list[tuple[str, str, str]] = []
    for sub in audience:
        if sub.cursor_at > now:
            continue
        # ⚠ The memo key includes VERBOSITY (subs.filter_key), because the level changes
        # the rendered text — sharing a body across levels would serve one subscriber the
        # other's density.
        fk = subs.filter_key(sub)
        if fk not in rendered:
            mine = [a for a in alerts if subs.matches(a, sub.countries, sub.families)]
            # ⛔ `quiet` declines a digest whose every item is routine by its own measured
            # baseline — that message is exactly the noise the level opts out of. Checked
            # here, per filter set, so it is decided on what THIS subscriber would see and
            # not on the unfiltered feed.
            rendered[fk] = (render.render_digest(mine, since=since, spilled=spilled,
                                                 verbosity=sub.verbosity)
                            if render.digest_is_worth_sending(mine, sub.verbosity) else "")
        if rendered[fk]:
            rows.append((sub.chat_id, key, rendered[fk]))
    return rows


async def run_once(pool: Any, cfg: NotifySettings, *, now: datetime | None = None,
                   session: aiohttp.ClientSession | None = None) -> TickResult:
    """One tick. Returns what it did."""
    now = now or datetime.now(timezone.utc)
    result = TickResult()

    alerts = await fetch_alerts(cfg.api_url, cfg.window_hours, session)
    result.fetched = len(alerts)
    if not alerts:
        logger.info("feed empty — nothing to do")
        return result

    keys = [identify(a)[0] for a in alerts]
    rs = await state.load_runner_state(pool)

    # --- 2. cold start: record the picture, enqueue nothing ---------------------------
    # ⛔ The whole standing picture is known-but-never-sent. Measured at 225 alerts, ~200 of
    # them maritime hulls whose newest event was already days old (0 inside 72 h, 90 inside
    # 168 h). A new subscriber's own "what does it look like right now" need is met by the
    # /start summary in gateway.py, which reports COUNTS rather than delivering the alerts.
    if rs.baselined_at is None:
        result.cold_start = True
        result.baselined = await state.baseline(pool, _entries(alerts), now)
        await state.mark_baselined(pool, now)
        await state.mark_digest_sent(pool, now)
        logger.info("cold start — baselined %d alerts, enqueued nothing", result.baselined)
        return result

    # --- 3. decide (GLOBAL: is this an event?) -----------------------------------------
    priors = await state.load_priors(pool, keys)
    instant: list[Alert] = []
    digest_routed: list[Alert] = []
    for a in alerts:
        key, _ = identify(a)
        d = policy.decide(a, priors.get(key), now)
        result.reasons[d.reason] = result.reasons.get(d.reason, 0) + 1
        if d.route == policy.INSTANT:
            instant.append(a)
        elif d.route == policy.DIGEST:
            digest_routed.append(a)
        else:
            result.suppressed += 1

    # --- 4. absence bookkeeping, BEFORE anything can fail ------------------------------
    # ⛔ Every observed key, including suppressed ones. If a suppressed key stopped being
    # touched, its presence session would expire while the thing sat plainly in the feed,
    # and the next tick would re-announce it — the phantom re-send by another door.
    await state.touch_seen(pool, keys, now)

    audience = await subs.active(pool)
    result.audience = len(audience)
    if not audience:
        # Nobody is listening. The ledger is STILL advanced: events must be decided on the
        # world's schedule, not on whether anyone happened to be subscribed, or the first
        # person to /start would be told about everything that changed since the bot was
        # built. Their /start summary covers "what does it look like now".
        for a in instant + digest_routed:
            k, s = identify(a)
            await state.record_sent(pool, key=k, family=a.family,
                                    signal_type=a.signal_type, change_sig=s, now=now)
        if policy.digest_due(rs.digest_sent_at, now):
            await state.mark_digest_sent(pool, now)
        result.pruned = await state.prune(pool, now)
        logger.info("tick: fetched=%d, no active subscribers — ledger advanced only",
                    result.fetched)
        return result

    # --- 5. fan out the instant events -------------------------------------------------
    send_now, spilled = policy.apply_burst_cap(instant)
    result.events_instant, result.spilled = len(send_now), len(spilled)

    rows = fan_out(send_now, audience, now)

    # The burst overflow becomes ONE digest per subscriber, enqueued in the SAME tick —
    # the cap exists to make a flood readable, not to delay it.
    if spilled:
        rows += fan_out_digest(spilled, audience, now,
                               key=f"spill:{now.isoformat(timespec='seconds')}",
                               spilled=len(spilled))

    # --- 6. account every decided event in the GLOBAL ledger ----------------------------
    # ⛔ Recorded here, NOT after a send — there is no single send to wait for. The debt to
    # each individual now lives in the outbox, which is retried per recipient.
    result.enqueued += await outbox.enqueue(pool, rows, now)
    for a in send_now + spilled + digest_routed:
        k, s = identify(a)
        await state.record_sent(pool, key=k, family=a.family, signal_type=a.signal_type,
                                change_sig=s, now=now)

    # --- 7. the periodic digest ---------------------------------------------------------
    if policy.digest_due(rs.digest_sent_at, now):
        fresh = await state.sent_since(pool, rs.digest_sent_at)
        current = dict(zip(keys, alerts, strict=True))
        items = [a for k, a in current.items()
                 if k in fresh and policy.route_for(a) == policy.DIGEST]
        if items:
            drows = fan_out_digest(
                items, audience, now,
                key=f"digest:{now.isoformat(timespec='seconds')}",
                since=rs.digest_sent_at.isoformat() if rs.digest_sent_at else None)
            result.enqueued += await outbox.enqueue(pool, drows, now)
            result.digest_items = len(items)
            result.digest_sent = bool(drows)
        await state.mark_digest_sent(pool, now)

    # --- 8. bounded ledger ---------------------------------------------------------------
    result.pruned = await state.prune(pool, now)

    logger.info(
        "tick: fetched=%d audience=%d instant=%d spilled=%d digest=%s(%d) enqueued=%d "
        "suppressed=%d pruned=%d reasons=%s",
        result.fetched, result.audience, result.events_instant, result.spilled,
        result.digest_sent, result.digest_items, result.enqueued, result.suppressed,
        result.pruned, result.reasons)
    return result


async def main() -> int:
    """Entrypoint for the systemd oneshot."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = default_settings
    if not cfg.database_url:
        logger.error("DATABASE_URL is not set — the delivery ledger is unreachable.")
        return 2

    import asyncpg
    pool = await asyncpg.create_pool(cfg.database_url, min_size=1, max_size=2,
                                     command_timeout=30)
    try:
        await run_once(pool, cfg)
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
