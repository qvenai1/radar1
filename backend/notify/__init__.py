"""NOTIFY — the Telegram update bot. Updates-only, and (since NOTIFY-2) publicly subscribable.

WHAT THIS LAYER IS FOR. `backend/alerts.py` serves a STANDING PICTURE: it is
read-over-signals, "not a detector and not a stored table — every alert is computed on
request", and every poll returns everything currently true. That is exactly right for a
dashboard, which re-reads the whole board each time. A bot cannot work that way — it must
say each thing ONCE — and the feed carries no alert id, no first_seen and no created_at
with which to tell a new thing from a re-read.

So this package is the missing EVENT LAYER, and nothing more. It adds no detector, no
severity judgement, no Arabic about any signal, and no second definition of what an alert
is. It answers one question the feed cannot answer about itself — "have I already said
this?" — and one the operator asked — "does it deserve an interruption?".

    identity.py   what is the same thing, and what counts as a change      PURE
    policy.py     does that change earn a message now, later, or never     PURE
    render.py     Alert -> Telegram text, reusing the existing Arabic      PURE
    subscribers.py who is subscribed and what they asked for              PURE predicate + store
    commands.py   /start /stop /delete /status /countries /families       PURE parse + templates
    state.py      the EVENT ledger (migration 039)                         I/O
    outbox.py     the per-recipient DELIVERY queue (migration 040)         I/O
    telegram.py   the API client: 429-obedient, classifies a blocked user  I/O
    runner.py     the tick — decides events and enqueues; no Telegram      I/O
    gateway.py    the ONLY process that talks to Telegram, both ways       I/O, resident
    config.py     environment only; no token, no bot

THE MEASUREMENT THE WHOLE DESIGN RESTS ON (this DB, 2026-08-11 07:35 UTC). Two polls of
/api/v1/alerts, 65 seconds apart, returned 225 and 228 alerts:

    keyed by (signal_type, entity)        ->  3 new,  0 gone   ← the truth
    keyed by (signal_type, entity, ts)    -> 14 new, 11 gone   ← 11 phantom re-sends

The 11 are military aircraft, whose `ts` is the snapshot time: it advances every poll while
nothing about the aircraft changes. At a 2-minute tick that is ~15,000 duplicate messages a
day from one family, and it is why identity is decided PER FAMILY in identity.py rather than
by hashing the alert.

THE THREE COMMITMENTS THIS LAYER INHERITS AND MUST NOT BREAK:
  * Military presence is INFO — informational, NEVER a threat. It is digest-routed and
    carries a blue marker, never an alarm.
  * A maritime composite is a STRONGER LEAD, still not proof; its caveat ships with every
    message and is never truncated.
  * Liveness is MIXED and per-alert. Every message states its source and vintage, and says
    outright when the signal is not live.
  * (NOTIFY-2) The audience is now strangers. Those three commitments are stated in plain
    language at /start and /help, BEFORE the first alert can arrive — an analyst infers
    "a composite is a lead"; someone who just typed /start does not.

NOTIFY-2 — GOING PUBLIC SPLIT ONE QUESTION INTO TWO:

    notify_deliveries (039)  "is this an EVENT?"        global, decided once
    notify_outbox     (040)  "who still needs telling?" per recipient, retried per recipient

They only look like the same question while there is one recipient. With a fan-out there is
no single send to key the ledger write on: recording after the FIRST success drops the event
for everyone who failed, recording after the LAST re-sends to everybody whenever one person
is unreachable. Delivery had to become its own state — see migration 040's header.
"""

from __future__ import annotations

from .identity import change_sig, dedup_key, identify
from .policy import DIGEST, INSTANT, SUPPRESS, Decision, Prior, decide, route_for
from .render import chunk, render_alert, render_digest
from .subscribers import Subscriber, matches

__all__ = [
    "identify", "dedup_key", "change_sig",
    "decide", "route_for", "Decision", "Prior", "INSTANT", "DIGEST", "SUPPRESS",
    "render_alert", "render_digest", "chunk",
    "Subscriber", "matches",
]
