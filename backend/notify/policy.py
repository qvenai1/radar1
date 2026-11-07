"""NOTIFY-1 — ROUTING: does this earn a message now, a line in the digest, or silence?

identity.py answers "is this new or changed?". This module answers the separate question
"and does that earn an interruption?". They are kept apart because they fail differently: a
wrong identity SILENTLY LOSES an alert forever, a wrong route merely makes the channel too
loud or too quiet, and only the second is safe to tune. PURE — ledger row + alert + clock
in, a Decision out. No I/O, no `now()` of its own.

⛔ ROUTING READS THE EXISTING `severity` AXIS. IT DOES NOT INVENT A THIRD ONE.
The project already carries two orthogonal axes and warns, at length, against conflating
them: `severity` ("HOW DANGEROUS is this claim?") and `importance` ("how much does this
deserve the analyst's eye in a country window?" — a DISPLAY order, and alerts.py is
explicit: "Do not colour a card by it, do not sum it, and never present it as risk").

"Should this interrupt a person?" is a dangerousness question, so it routes on SEVERITY.
Routing on `importance` would have been easy and wrong twice over: it would push military
presence to instant — against both the operator's choice and the standing commitment that
presence is "informational, NEVER a threat" — and an interrupt IS a risk assertion, which
is the one use importance forbids. So a bot message inherits the honest severity that
alerts.py already computed, and this module adds no judgement of its own.

WHAT THAT YIELDS, against the live feed measured 2026-08-11 07:35 UTC:

    emergency    HIGH  -> INSTANT   0 alerts now; the only high-by-definition family
    maritime     high  -> INSTANT   200 standing, but 0 with an event inside 72 h, so
                                    genuinely-new hulls are rare (90 inside 168 h)
    maritime  med/low  -> DIGEST
    military     INFO  -> DIGEST    25 now, and every one of them churns each poll
    implausible   LOW  -> DIGEST    0 now, but bursty — a jamming wave lands at once
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# --- routes ----------------------------------------------------------------------------
INSTANT, DIGEST, SUPPRESS = "instant", "digest", "suppress"

#: Severities that interrupt. `high` only — the operator's choice, and it lines up with the
#: one family alerts.py calls high BY DEFINITION plus the hull composites that carry their
#: own high rank. Everything else accumulates into the periodic digest.
#: ⚠ `info` is NOT here and must not be added: the military families are info precisely
#: because presence is not a threat, and an instant message asserts that it is.
INSTANT_SEVERITIES = frozenset({"high"})

# --- clocks (POLICY constants — stated, not measured) ------------------------------------
#
# ⚠ These four are DECISIONS, not measurements, and are labelled as such because this
# project's rule is to state the method beside the number. Nothing in the DB tells us how
# long a military aircraft should be gone before it is news again — that is a judgement
# about the operator's attention, not a fact about the world. What IS measured is quoted
# beside each, where a measurement constrains the choice.

#: How long a key must go UNSEEN before the same thing counts as news again.
#: Constrained from below by the feed, not chosen freely: military.json is treated as stale
#: after MILITARY_STALE_S = 120 s (backend/api.py), and the family vanishes from the feed
#: entirely while its file is stale. A session window at or near 120 s would therefore
#: close on a collector hiccup and re-announce the same loitering aircraft. 30 minutes is
#: 15x that staleness bound and 15x the 2-minute tick, so it takes a sustained real absence
#: — not one missed poll and not a brief collector stall — to re-arm.
SESSION_EXPIRY = timedelta(minutes=30)

#: Minimum gap between two messages about the SAME key, per family. Identity decides that
#: something changed; this decides whether the change has earned an interruption yet.
#:
#: implausible is the only family that needs one, and it needs it structurally: its
#: signature is the last implausible FIX, which advances as fast as the feed during a
#: jamming wave. Without a floor, one spoofed vessel becomes a per-poll firehose about a
#: hull that is, in the project's own words, "usually a VICTIM ... not an actor".
#:
#: The others are 0 for reasons, not by default: emergency must never be delayed; the
#: military signatures are constant so they cannot repeat at all; maritime's signature only
#: moves when a detector records a new event, measured at 0 occurrences inside 72 h.
COOLDOWN: dict[str, timedelta] = {
    "implausible": timedelta(hours=6),
}
_NO_COOLDOWN = timedelta(0)

#: How often the digest goes out.
DIGEST_INTERVAL = timedelta(hours=1)

#: ⛔ THE BURST VALVE. A cap on INSTANT messages per tick, with the overflow SPILLED INTO
#: THE DIGEST — never dropped. It exists because the instant path is severity-gated, not
#: volume-gated, and one backfill can make a rare family sudden: the maritime detectors
#: currently show 0 new events in 72 h but 90 hulls inside 168 h, so a single catch-up run
#: could rank a large batch high at once. Ten interruptions is a notification; ninety is an
#: outage of the operator's attention, and the ninety-first is the one they miss.
#:
#: ⚠ Spill, not drop. An overflowed alert is still delivered, in the next digest, and the
#: digest says how many spilled — so the cap is visible in the channel rather than silently
#: shaping what the operator sees.
INSTANT_BURST_CAP = 10


@dataclass(frozen=True, slots=True)
class Prior:
    """What the ledger already knows about a key. None means never sent."""
    change_sig: str
    sent_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class Decision:
    route: str
    #: Machine-readable cause, carried so the runner can log WHY without re-deriving it,
    #: and so tests assert the reason rather than only the outcome.
    reason: str


def cooldown_for(family: str) -> timedelta:
    return COOLDOWN.get(family, _NO_COOLDOWN)


def route_for(alert: Any) -> str:
    """INSTANT or DIGEST for an alert that has been decided worth sending."""
    return INSTANT if getattr(alert, "severity", "") in INSTANT_SEVERITIES else DIGEST


def session_expired(prior: Prior, now: datetime) -> bool:
    """Has this key been UNSEEN long enough that its return would be news again?

    ⚠ A pure staleness test. Whether staleness may RE-ARM anything is decided by
    `can_rearm_on_absence`, because for most families it may not."""
    return (now - prior.last_seen_at) >= SESSION_EXPIRY


def can_rearm_on_absence(alert: Any) -> bool:
    """May an ABSENCE make this alert news again?

    ⛔ ONLY for the presence families (identity.PRESENCE_SIGNALS) — the ones whose signature
    is constant by design and which therefore have no other way to become news.

    ⚠ MEASURED CONSEQUENCE OF THE ALTERNATIVE. A dry run against the live feed with a
    63-minute gap between ticks re-announced ALL 220 standing alerts, because absence was
    allowed to re-arm every family. At the 2-minute production tick `last_seen_at` never
    goes stale, so this never fires in normal running — it fires precisely when the BOT has
    been down longer than the session window, turning every restart after an outage into a
    200-message flood. The one moment the operator most needs a quiet channel.

    Excluding the other families costs nothing: each carries a meaningful signature, so
    anything that genuinely changed while the bot was down is still delivered on the first
    tick back."""
    from .identity import PRESENCE_SIGNALS
    return getattr(alert, "signal_type", "") in PRESENCE_SIGNALS


def decide(alert: Any, prior: Prior | None, now: datetime) -> Decision:
    """Route one alert against what the ledger already knows.

    The four outcomes, in the order they are tested:

      new         never seen                      -> route by severity
      returned    seen, unchanged, but ABSENT long enough to re-arm -> route by severity
      changed     signature moved                 -> route by severity, unless cooling down
      unchanged   still the same, still present   -> SUPPRESS

    ⚠ `returned` is tested BEFORE `changed` on purpose. A returning military aircraft has a
    CONSTANT signature by design (identity.py), so it can only ever be recognised by its
    absence; testing `changed` first would classify it as unchanged and suppress it forever.
    """
    from .identity import change_sig

    sig = change_sig(alert)

    if prior is None:
        return Decision(route_for(alert), "new")

    if sig == prior.change_sig and can_rearm_on_absence(alert) and session_expired(prior, now):
        return Decision(route_for(alert), "returned")

    if sig == prior.change_sig:
        return Decision(SUPPRESS, "unchanged")

    # The signature moved — a real update. The only thing that can still hold it back is
    # this family's cooldown, and never for a family that has none.
    cd = cooldown_for(getattr(alert, "family", ""))
    if cd > _NO_COOLDOWN and (now - prior.sent_at) < cd:
        # ⚠ SUPPRESSED, NOT QUEUED — and the distinction matters. The next poll re-derives
        # this alert from the feed with whatever its signature is by then, so nothing is
        # lost or stale-queued: once the cooldown lapses the CURRENT state is sent, not the
        # one that happened to trip the limit. That is the right behaviour for a family
        # whose updates are "another implausible fix landed".
        return Decision(SUPPRESS, "cooldown")

    return Decision(route_for(alert), "changed")


def apply_burst_cap(instant: list, cap: int = INSTANT_BURST_CAP) -> tuple[list, list]:
    """(send_now, spilled_to_digest) — the valve, applied to one tick's instant batch.

    Most-severe-first order is already established by alerts.sort_alerts upstream, and it
    is preserved here rather than re-sorted: if a batch must be cut, what survives should
    be the top of the ordering the project already calls honest, not a second opinion
    formed in the notify layer."""
    if cap < 0 or len(instant) <= cap:
        return instant, []
    return instant[:cap], instant[cap:]


def digest_due(digest_sent_at: datetime | None, now: datetime,
               interval: timedelta = DIGEST_INTERVAL) -> bool:
    """Is the periodic digest due?

    True when it has never run, so a fresh install emits its first digest on the first tick
    rather than staying silent for an hour with a filling backlog."""
    return digest_sent_at is None or (now - digest_sent_at) >= interval
