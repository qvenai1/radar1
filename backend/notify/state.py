"""NOTIFY-1 — the delivery ledger (I/O). Everything durable the bot knows.

Backed by `notify_deliveries` + `notify_runner_state` (migration 039). Postgres rather than
a local file for three reasons that are all about failure, not preference: the ledger
survives a restart and a redeploy; it rides the existing backup timer (scraplan-backup) so
a restore brings the bot's memory back with the data; and it is queryable, so "why did the
channel go quiet" is answered with SQL instead of a guess.

⛔ THE WRITE ORDER IS SEND-THEN-RECORD, AND IT IS A DELIBERATE CHOICE OF WHICH FAILURE TO
HAVE. Recording before sending would mean a crash between the two marks an alert delivered
that nobody received — silently, permanently, and invisibly, because the key is now in the
ledger and will never be reconsidered. Recording after sending means the same crash re-sends
one message. This layer is AT-LEAST-ONCE on purpose: a duplicate is noticed and forgiven, a
hole is neither. `runner.py` holds that order and this module never records on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .policy import Prior

#: How long a key survives after it was last SEEN in a poll. A hull that vanishes for a
#: month and returns is genuinely news again, so expiry here is not merely housekeeping —
#: it is what stops the ledger from asserting "already told you" about something the
#: operator last heard of a month ago. Rows for anything currently present are refreshed
#: every tick and never expire.
RETENTION = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class RunnerState:
    baselined_at: datetime | None
    digest_sent_at: datetime | None


async def load_runner_state(pool: Any) -> RunnerState:
    row = await pool.fetchrow(
        "SELECT baselined_at, digest_sent_at FROM notify_runner_state WHERE id IS TRUE")
    if row is None:
        # The migration seeds this row; its absence means someone truncated the table.
        # Re-seed rather than crash — but return an UNBASELINED state, so the runner
        # re-baselines instead of treating the whole standing picture as new. Failing
        # toward silence is right here: the alternative is a 225-message flood.
        await pool.execute(
            "INSERT INTO notify_runner_state (id) VALUES (TRUE) ON CONFLICT DO NOTHING")
        return RunnerState(None, None)
    return RunnerState(row["baselined_at"], row["digest_sent_at"])


async def mark_baselined(pool: Any, now: datetime) -> None:
    await pool.execute(
        "UPDATE notify_runner_state SET baselined_at = $1 WHERE id IS TRUE", now)


async def mark_digest_sent(pool: Any, now: datetime) -> None:
    await pool.execute(
        "UPDATE notify_runner_state SET digest_sent_at = $1 WHERE id IS TRUE", now)


async def load_priors(pool: Any, keys: list[str]) -> dict[str, Prior]:
    """What the ledger knows about exactly these keys.

    One query for the whole tick rather than one per alert: a poll carries ~225 keys and
    the same shape of N+1 was already avoided in the alerts endpoint's live_key resolution
    ("for every hull in ONE query rather than N")."""
    if not keys:
        return {}
    rows = await pool.fetch(
        "SELECT dedup_key, change_sig, sent_at, last_seen_at FROM notify_deliveries "
        "WHERE dedup_key = ANY($1::text[])", keys)
    return {r["dedup_key"]: Prior(change_sig=r["change_sig"], sent_at=r["sent_at"],
                                  last_seen_at=r["last_seen_at"]) for r in rows}


async def touch_seen(pool: Any, keys: list[str], now: datetime) -> None:
    """Mark these keys as OBSERVED in this poll, whether or not anything was sent.

    ⛔ This is what makes absence measurable, and it must run for every key in the feed —
    including suppressed ones. `last_seen_at` is the only evidence that a presence session
    is still open; if a suppressed key stopped being touched, its session would appear to
    expire while the aircraft was still plainly in the feed, and the next poll would
    re-announce it. That is the phantom re-send arriving by a different door."""
    if not keys:
        return
    await pool.execute(
        "UPDATE notify_deliveries SET last_seen_at = $2 WHERE dedup_key = ANY($1::text[])",
        keys, now)


async def record_sent(pool: Any, *, key: str, family: str, signal_type: str,
                      change_sig: str, now: datetime) -> None:
    """Record that a message about `key` HAS BEEN SENT. Called only after the send returned.

    Idempotent by upsert: a re-send after a crash updates the row rather than failing on the
    primary key, so recovery never needs the ledger cleaned by hand."""
    await pool.execute(
        """
        INSERT INTO notify_deliveries
               (dedup_key, family, signal_type, change_sig, sent_at, last_seen_at, send_count)
        VALUES ($1, $2, $3, $4, $5, $5, 1)
        ON CONFLICT (dedup_key) DO UPDATE
           SET change_sig  = EXCLUDED.change_sig,
               sent_at     = EXCLUDED.sent_at,
               last_seen_at= EXCLUDED.last_seen_at,
               family      = EXCLUDED.family,
               signal_type = EXCLUDED.signal_type,
               send_count  = notify_deliveries.send_count + 1
        """,
        key, family, signal_type, change_sig, now)


async def baseline(pool: Any, entries: list[tuple[str, str, str, str]], now: datetime) -> int:
    """COLD START — record the whole current picture as known, and send NOTHING.

    `entries` are (key, family, signal_type, change_sig).

    ⛔ WHY THIS EXISTS. The first run ever sees the entire standing picture: measured at 225
    alerts, of which 200 were maritime hulls whose newest event was already 3-7 days old (0
    hulls with an event inside 72 h; 90 inside 168 h). Delivering that as "updates" would
    open the channel with 200 messages of week-old news and teach the operator to mute it
    on day one.

    It is the same rule backend/stream.py's Broadcaster already applies on connect
    ("Baseline only: ... pushing it again is noise"), and it is why the ledger separates
    "recorded" from "sent": these rows get sent_at = now with send_count = 0, which reads
    honestly in the table as known-but-never-messaged."""
    if not entries:
        return 0
    await pool.executemany(
        """
        INSERT INTO notify_deliveries
               (dedup_key, family, signal_type, change_sig, sent_at, last_seen_at, send_count)
        VALUES ($1, $2, $3, $4, $5, $5, 0)
        ON CONFLICT (dedup_key) DO NOTHING
        """,
        [(k, f, s, c, now) for k, f, s, c in entries])
    return len(entries)


async def sent_since(pool: Any, since: datetime | None) -> set[str]:
    """Keys messaged-or-recorded since `since` — the digest's "what is new this period".

    None means no digest has ever run, in which case everything currently known counts as
    new to the first digest."""
    if since is None:
        rows = await pool.fetch("SELECT dedup_key FROM notify_deliveries")
    else:
        rows = await pool.fetch(
            "SELECT dedup_key FROM notify_deliveries WHERE sent_at > $1", since)
    return {r["dedup_key"] for r in rows}


async def prune(pool: Any, now: datetime, retention: timedelta = RETENTION) -> int:
    """Drop keys not seen for `retention`. Returns how many went."""
    return int(await pool.fetchval(
        "WITH d AS (DELETE FROM notify_deliveries WHERE last_seen_at < $1 RETURNING 1) "
        "SELECT count(*) FROM d", now - retention) or 0)
