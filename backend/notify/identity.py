"""NOTIFY-1 — per-family alert IDENTITY: what is "the same thing", and what is "a change".

THE PROBLEM THIS MODULE EXISTS TO SOLVE. `backend/alerts.py` is read-over-signals: "not a
detector and not a stored table — every alert is computed on request". Each poll therefore
returns the WHOLE standing picture, and the feed carries no id, no first_seen and no
created_at. A bot that must send each thing ONCE has to supply the identity the feed does
not have. That is this module, and it is PURE — alert in, two strings out. All I/O lives in
state.py / runner.py, matching how alerts.py itself keeps its builders pure.

TWO STRINGS, BECAUSE THERE ARE TWO QUESTIONS:

    dedup_key   "which THING is this?"        — stable for the life of the thing
    change_sig  "has anything ACTUALLY changed about it?" — changes only on a real update

Send when the key is new, or when the signature moved. Nothing else.

⛔ THE MEASUREMENT THAT DEFINES THIS MODULE. Two /api/v1/alerts polls, 65 s apart, on this
DB (2026-08-11 07:35 UTC):

    keyed by (signal_type, entity id)        ->  3 new,  0 gone   ← the truth
    keyed by (signal_type, entity id, ts)    -> 14 new, 11 gone   ← 11 phantom re-sends

The 11 are military aircraft. Their `ts` is the SNAPSHOT time, so it advances every poll
while nothing about the aircraft changes. At a 2-minute tick that is ~15,000 duplicate
messages a day from one family. So `ts` is admitted into a signature ONLY where it means
"a new event happened" (maritime, implausible) and NEVER where it means "the feed was read
again" (military, emergency). There is no whole-alert hash anywhere in this file: a generic
hash cannot tell those two meanings apart, which is precisely the bug.

⛔ SIGNATURES ARE BUILT FROM STRUCTURED FIELDS, NOT FROM PROSE — with one deliberate
exception, below. `severity_basis`, `summary`, `headline`, `why` and `caveat` are human
register, and this project edits them (the «GNSS» rewording pass touched every displayed
string). If a signature folded prose, a copy edit to alerts.py would re-blast every hull in
the ledger. Wording must never cost messages.

⛔ THE FAILURE DIRECTION IS CHOSEN PER FAMILY, BY WHAT THE MISTAKE COSTS. Fail-closed here
does not mean one fixed behaviour, it means never failing in the expensive direction:

    emergency   over-send.  Missing an active 7500 is unacceptable; a duplicate is not.
                            So its signature is allowed to be coarse and prose-derived.
    military    under-send. Presence is INFO, high-volume, and digest-routed; a missed
                            re-announcement of a loitering aircraft costs nothing.
    maritime    structured. A week-old standing picture must not re-blast on a reword.
"""

from __future__ import annotations

from typing import Any

from ..alerts import SEVERITY_BASIS

#: Every alert carries `entity_ref.id` (icao24 | mmsi | IMO/hull | NORAD). It is the
#: durable identity of the OBJECT, and it is what every key below is built on — never a
#: positional or temporal field, both of which change while the object does not.
def _entity(alert: Any) -> str:
    ref = alert.entity_ref
    return str(getattr(ref, "id", None) or "")


def _ts(alert: Any) -> str:
    return str(getattr(alert, "ts", None) or "")


# --- per-family rules ------------------------------------------------------------------
#
# Each rule is (key_fn, sig_fn). Both receive the whole Alert and return a string. Read the
# comment above each: the rule is a decision with a measured or stated reason, not a
# formatting choice.

def _key_emergency(a: Any) -> str:
    # The AIRCRAFT is the thing — one aircraft in one emergency is one situation, however
    # many polls observe it.
    return f"emergency_squawk:{_entity(a)}"


def _sig_emergency(a: Any) -> str:
    # ⚠ THE ONE PLACE PROSE IS ADMITTED, on purpose. The squawk CODE is what distinguishes
    # a radio failure from a hijack, and the Alert model does not carry it as a field — it
    # exists only inside `summary` ("squawk 7700 — general-emergency"). Keying without it
    # would let a 7500 be SUPPRESSED twenty minutes after the same aircraft squawked 7600,
    # because the key would already be in the ledger. That is the one outcome this whole
    # layer must never produce.
    #
    # Parsing the code out of the string would be the "clean" fix and is the wrong trade:
    # a parse that silently fails returns a constant, and a constant re-introduces exactly
    # the suppression above. Taking the summary WHOLE cannot fail that way — its worst case
    # is one extra message if the label is ever reworded, on a family measured at 0 alerts
    # in the live feed. Over-send is the correct failure direction here; under-send is not.
    return str(getattr(a, "summary", "") or "")


def _key_military_air(a: Any) -> str:
    return f"military_aircraft:{_entity(a)}"


def _sig_military_air(a: Any) -> str:
    # CONSTANT, and this is the module's central finding made concrete. Presence is the
    # entire claim the family makes ("military aircraft presence — informational, not a
    # threat"), and presence does not change while the aircraft is present. Every field
    # that DOES move poll-to-poll — ts, altitude, position — describes the reading, not
    # the claim, and folding any of them in reproduces the 11-phantom-resend measurement.
    #
    # ⚠ `countries` is deliberately excluded too, though it is structured. An aircraft
    # tracking a border re-enters and re-leaves a polygon repeatedly, so admitting it would
    # make one flight re-announce itself for the length of the border. A country change is
    # visible in the digest's current picture either way, so nothing is lost.
    #
    # Re-announcement is handled by ABSENCE instead (policy.session_expired): the aircraft
    # has to actually leave and stay gone before it is news again.
    return "presence"


def _key_military_sat(a: Any) -> str:
    # ⛔ THE PASS IS THE THING, AND ITS IDENTITY IS satellite_passes' OWN PRIMARY KEY:
    # (norad_id, start_time). `ts` on this family IS start_time, so this key is that PK.
    #
    # The tempting alternative — "same pass if within N minutes" — is UNSAFE HERE, and
    # measurement is the only reason we know. The minimum observed gap between consecutive
    # passes of one satellite is 2 MINUTES (norad 26407, 16:43:40 -> 16:45:40, both over
    # Turkey: one physical transit split where the AOI mask breaks). Any proximity window
    # wide enough to absorb recompute drift would swallow that second row — suppressing a
    # real pass to prevent a duplicate, which is the wrong trade in both directions.
    #
    # ⚠ ACCEPTED AND STATED, NOT HIDDEN: satellite_passes is fully recomputed each run
    # (computed_at had exactly 1 distinct value over all 349 rows), and start_time sits on
    # a 60 s propagation grid. A recompute that moves a pass by one grid step reads as a new
    # key and re-announces it once. Bounded, rare, and INFO-severity — and the honest cost
    # of refusing a heuristic that would drop real passes.
    return f"military_satellite:{_entity(a)}:{_ts(a)}"


def _sig_military_sat(a: Any) -> str:
    # Constant: the pass start is already in the key, so a signature that repeated it would
    # only ever change together with the key — dead machinery pretending to be a check.
    return "predicted"


def _key_maritime(a: Any) -> str:
    # The HULL. `entity_ref.id` is the IMO where one exists, which is chosen precisely
    # because it "survives a re-flag and is what stitches a hull's history together" — so a
    # vessel that changes MMSI stays one thing in the ledger, as it does everywhere else.
    return f"maritime_composite:{_entity(a)}"


def _sig_maritime(a: Any) -> str:
    # `ts` here is the hull's LAST EVENT time, not a read time — so it advances exactly when
    # a detector recorded something new about this hull, which is the definition of an
    # update for a standing picture. `severity` is the hull's own composite_rank, so an
    # escalation re-announces even with no newer event.
    #
    # ⚠ These two are sufficient, and the third candidate is deliberately left out. The
    # pattern COUNT lives only in prose (`severity_basis`: "composite of 3 pattern types…"),
    # and admitting prose would re-blast all 200 capped hulls on a reword. It is also
    # redundant: n_pattern_types counts DISTINCT event types over the hull's events, so a
    # hull cannot gain a pattern type without gaining an event — and gaining an event moves
    # last_event_ts, which is already here.
    return f"{_ts(a)}|{getattr(a, 'severity', '')}"


def _key_mt_single(a: Any) -> str:
    # The MarineTraffic vessel + the pattern observed. ⛔ NOT the ship alone: one ship_id can
    # legitimately produce a rendezvous AND a zone_dwell, and they are different claims that
    # must not suppress one another the way a single per-ship key would.
    return f"maritime_single:{_entity(a)}:{getattr(a, 'summary', '')}"


def _sig_mt_single(a: Any) -> str:
    # `ts` is the event's own start — it moves only when a NEW event of that pattern is
    # detected for that ship, which is the definition of an update here. Structured, so a
    # reword of the register cannot re-blast the family (the maritime rule, same reasoning).
    return _ts(a)


def _key_implausible(a: Any) -> str:
    # Per HULL, matching the feed: the endpoint aggregates a vessel's implausible fixes in
    # the window into one alert (GROUP BY vs.mmsi), so one row is one vessel's situation.
    return f"implausible_position:{_entity(a)}"


def _sig_implausible(a: Any) -> str:
    # `ts` is the most recent implausible FIX — a genuine new observation each time it
    # moves, so unlike the military families this ts belongs in the signature.
    #
    # ⚠ But it moves as fast as the feed does during a jamming wave, and the family is
    # LOW ("usually a victim ... not an actor"). Signature alone would therefore be a
    # per-vessel firehose. The rate limit is NOT solved here — identity states what changed,
    # policy decides whether that earns a message (policy.COOLDOWN_S). Keeping those two
    # separate is what lets the cooldown be tuned without redefining what a change is.
    return _ts(a)


#: ⛔ PRESENCE FAMILIES — the ONLY ones an ABSENCE may re-arm, and the set is deliberately
#: as small as the facts allow. A family belongs here iff its signature is CONSTANT BY
#: DESIGN, which leaves it no other way to become news again.
#:
#: `military_aircraft` qualifies: presence is its whole claim, so the signature cannot move
#: while the aircraft is there, and an aircraft that leaves the AOI and returns an hour
#: later is genuinely a new observation.
#:
#: ⚠ EVERY OTHER FAMILY IS EXCLUDED, AND THE COST OF GETTING THIS WRONG WAS MEASURED, not
#: reasoned about. A dry run against the live feed with a 63-minute gap between ticks
#: re-announced ALL 220 standing alerts — every maritime hull included — because absence
#: alone was allowed to re-arm anything. In production the tick is 2 minutes so `last_seen_at`
#: never goes stale; the flood only appears after the bot itself has been DOWN for longer
#: than the session window (a reboot, an outage, a full disk), which is exactly the moment a
#: 200-message burst is least welcome and least likely to be caught in review.
#:
#: `military_satellite` is excluded even though its signature is also constant: its KEY
#: already contains the pass start time, so a given pass is unique and a "return" would only
#: ever re-announce the same predicted pass.
#: `maritime_composite`, `implausible_position` and `emergency_squawk` all carry meaningful
#: signatures — their own change is what makes them news, and absence adds nothing.
PRESENCE_SIGNALS: frozenset[str] = frozenset({"military_aircraft"})

#: signal_type -> (dedup_key, change_sig). The ONLY place a family's identity is decided.
_RULES: dict[str, tuple[Any, Any]] = {
    "emergency_squawk":     (_key_emergency,     _sig_emergency),
    "military_aircraft":    (_key_military_air,  _sig_military_air),
    "military_satellite":   (_key_military_sat,  _sig_military_sat),
    "maritime_composite":   (_key_maritime,      _sig_maritime),
    "implausible_position": (_key_implausible,   _sig_implausible),
    "maritime_single":      (_key_mt_single,     _sig_mt_single),
}

# ⚠ DRIFT GATE, in the idiom alerts.py already uses for its Arabic registers (the
# SQUAWK_MEANING_AR and _MIL_SIGNAL_TYPES assertions). SEVERITY_BASIS is alerts.py's own
# per-signal taxonomy, so reading it here means a NEW family cannot reach the bot without
# an identity rule — it fails LOUDLY at import instead of shipping a family that silently
# re-sends itself forever on the generic fallback below.
#
# maritime_composite is legitimately absent from SEVERITY_BASIS (its basis is composed
# per-hull in _maritime_basis, not looked up), hence subset rather than equality.
assert set(SEVERITY_BASIS) <= set(_RULES), (
    "an alert family has no identity rule: "
    f"{set(SEVERITY_BASIS) - set(_RULES)}. Give it a (key, sig) rule in _RULES and state "
    "the reason — never let it fall through to the generic identity, which cannot tell a "
    "new observation from a re-read and will either spam or suppress it"
)


def dedup_key(alert: Any) -> str:
    """The stable identity of the THING this alert is about.

    Falls back to (signal_type, entity, ts) for an unknown family. That fallback is
    deliberately the SAFE-BUT-NOISY one: it can re-send, and it can never suppress. A
    fallback that dropped the ts would silently swallow a family's every update after the
    first, which is unrecoverable and invisible; a duplicate is neither. The import gate
    above means this is only ever reachable by a family that skipped review."""
    rule = _RULES.get(alert.signal_type)
    if rule is None:
        return f"{alert.signal_type}:{_entity(alert)}:{_ts(alert)}"
    return rule[0](alert)


def change_sig(alert: Any) -> str:
    """What must move before this alert counts as an UPDATE rather than a re-read."""
    rule = _RULES.get(alert.signal_type)
    if rule is None:
        return _ts(alert)
    return rule[1](alert)


def identify(alert: Any) -> tuple[str, str]:
    """(dedup_key, change_sig) — the pair the ledger stores."""
    return dedup_key(alert), change_sig(alert)
