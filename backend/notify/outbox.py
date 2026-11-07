"""NOTIFY-2 — the delivery queue. One row per (recipient, event) still owed.

⛔ WHY AN OUTBOX AND NOT A LOOP OVER SUBSCRIBERS. Migration 039's send-then-record ordering
works because there is exactly one send to key the record on. With a fan-out there is not,
and the two obvious repairs both fail:

    record after the FIRST success  -> everyone who failed loses the event, permanently
    record after the LAST success   -> one unreachable subscriber re-sends to everybody

The fix is not more care inside the loop; it is moving delivery into its own state. An
event is decided ONCE (notify_deliveries, unchanged from 039) and then OWED to each
recipient independently. A row here is a debt: it survives restarts, it retries on its own
schedule, and it is deleted only when that specific person has actually received it.

That also makes the rate limit tractable. Telegram allows roughly 30 messages/second for a
bot overall; with N subscribers a single event is N messages, so delivery has to be PACED
and RESUMABLE rather than attempted all at once inside one tick. A queue is both for free.

⚠ THE BODY IS RENDERED AT ENQUEUE TIME, NOT AT SEND TIME. What is queued is what will be
delivered, so an operator inspecting the queue sees exactly what the subscriber will get.
The upsert refreshes a still-pending body rather than queueing a second message, so someone
unreachable for several ticks receives the CURRENT state once instead of a stack of
superseded versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

#: Attempts before a row is given up on and dropped. Generous, because the common failures
#: are transient (a phone offline, a Telegram blip) and the expensive failure mode —
#: a user who blocked the bot — is detected explicitly by its 403 and does not consume
#: attempts at all.
MAX_ATTEMPTS = 6

#: Retry backoff by attempt number. Bounded at ~30 min: beyond that an alert is stale enough
#: that delivering it late is worth less than the queue space, and MAX_ATTEMPTS ends it.
_BACKOFF = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=5),
            timedelta(minutes=15), timedelta(minutes=30), timedelta(minutes=30))


def backoff_for(attempts: int) -> timedelta:
    return _BACKOFF[min(attempts, len(_BACKOFF) - 1)]


@dataclass(frozen=True, slots=True)
class Item:
    id: int
    chat_id: str
    dedup_key: str
    body: str
    attempts: int


async def enqueue(pool: Any, rows: list[tuple[str, str, str]], now: datetime) -> int:
    """Queue (chat_id, dedup_key, body) triples. Returns how many were written.

    One executemany for the whole tick's fan-out: with N subscribers x E events this is the
    difference between one round trip and N*E of them."""
    if not rows:
        return 0
    await pool.executemany(
        """
        INSERT INTO notify_outbox
               (chat_id, dedup_key, body, queued_at, next_attempt_at)
        VALUES ($1, $2, $3, $4, $4)
        ON CONFLICT (chat_id, dedup_key) DO UPDATE
           SET body = EXCLUDED.body,
               queued_at = EXCLUDED.queued_at,
               -- ⚠ next_attempt_at is NOT reset. A recipient already in backoff must not be
               -- pulled back to the front of the queue every tick just because the event
               -- was re-rendered — that would turn a slow recipient into a hot loop.
               last_error = NULL
        """,
        [(c, k, b, now) for c, k, b in rows])
    return len(rows)


async def due(pool: Any, now: datetime, limit: int) -> list[Item]:
    """The next `limit` messages owed, oldest first.

    ⚠ Ordered by (next_attempt_at, id) so a retry never starves a fresh message and a fresh
    message never starves a retry: both live on the same clock."""
    rows = await pool.fetch(
        "SELECT id, chat_id, dedup_key, body, attempts FROM notify_outbox "
        "WHERE next_attempt_at <= $1 ORDER BY next_attempt_at, id LIMIT $2", now, limit)
    return [Item(id=r["id"], chat_id=r["chat_id"], dedup_key=r["dedup_key"],
                 body=r["body"], attempts=r["attempts"]) for r in rows]


async def done(pool: Any, item_id: int) -> None:
    """Delivered. The debt is discharged, so the row goes."""
    await pool.execute("DELETE FROM notify_outbox WHERE id = $1", item_id)


async def fail(pool: Any, item: Item, error: str, now: datetime) -> bool:
    """Record a failed attempt. Returns True if the row was given up on and dropped.

    Giving up is bounded and VISIBLE (the error is kept on the row until it goes, and the
    drop is logged) rather than silent — a queue that quietly discards is worse than one
    that visibly gives up."""
    attempts = item.attempts + 1
    if attempts >= MAX_ATTEMPTS:
        await pool.execute("DELETE FROM notify_outbox WHERE id = $1", item.id)
        return True
    await pool.execute(
        "UPDATE notify_outbox SET attempts = $2, next_attempt_at = $3, last_error = $4 "
        "WHERE id = $1", item.id, attempts, now + backoff_for(attempts), error[:500])
    return False


async def drop_for(pool: Any, chat_id: str) -> int:
    """Discard everything owed to one recipient — used when Telegram reports they blocked
    the bot. Continuing to retry a 403 would burn the rate limit that live subscribers are
    waiting on."""
    return int(await pool.fetchval(
        "WITH d AS (DELETE FROM notify_outbox WHERE chat_id = $1 RETURNING 1) "
        "SELECT count(*) FROM d", str(chat_id)) or 0)


async def depth(pool: Any) -> int:
    return int(await pool.fetchval("SELECT count(*) FROM notify_outbox") or 0)
