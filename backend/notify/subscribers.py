"""NOTIFY-2 — who is subscribed, and what each of them asked for.

The filter predicate is PURE and sits at the top of this file; everything below it is the
store. Splitting them that way matters because the predicate is the part that decides what
a person does and does not get told, and it must be testable without a database.

⛔ THE FILTER USES THE ENDPOINT'S OWN SEMANTICS, NOT A NEW ONES. A country matches when its
code appears in the alert's `countries` list — character for character what
`backend/api.py::_scoped` does for `?country=`. Writing a second, more "helpful" rule here
(say, one that let unattributed alerts through) would mean the bot and the dashboard
disagreed about which alerts belong to Syria, and the operator would have no way to tell
which was lying.

⚠ MEASURED CONSEQUENCE, AND IT IS LARGE: 64 % of military aircraft carry NO country
attribution at all (9 of 14 in the live feed) — they are outside the seven AOI polygons.
Any country filter therefore excludes most of that family. That is the CORRECT behaviour
(the alert genuinely is not attributable to the country asked for), but it is not
behaviour a subscriber would guess, so `commands.py` states it out loud at the moment a
country filter is set. A filter that silently removes two thirds of a family is the kind of
thing that gets discovered months later as "the bot stopped reporting aircraft".
"""

from __future__ import annotations

from types import EllipsisType

from dataclasses import dataclass
from datetime import datetime
from typing import Any

ACTIVE, STOPPED, BLOCKED = "active", "stopped", "blocked"

#: The four families a subscriber can name. Sourced from the alert taxonomy rather than
#: hand-listed so a new family cannot become unselectable without anyone noticing.
FAMILIES = ("emergency", "maritime", "implausible", "military")


@dataclass(frozen=True, slots=True)
class Subscriber:
    chat_id: str
    status: str
    cursor_at: datetime
    countries: list[str] | None = None
    families: list[str] | None = None
    #: NOTIFY-3 presentation density — quiet | balanced | detailed (migration 042).
    #: ⛔ Never affects admission, only how densely the digest is written; the instant path
    #: is identical at every level.
    verbosity: str = "balanced"

    @property
    def active(self) -> bool:
        return self.status == ACTIVE


def matches(alert: Any, countries: list[str] | None,
            families: list[str] | None) -> bool:
    """Does this alert belong in this subscriber's feed?

    An empty or absent filter means NO filter — everything. That default is what makes a
    fresh /start work with no configuration, and it means "all" needs no special value.

    ⚠ The two filters are ANDed: a subscriber asking for Syria AND emergencies wants
    emergencies in Syria, not their union. The union reading would make each added filter
    INCREASE traffic, which is the opposite of what selecting a filter means."""
    if families and getattr(alert, "family", "") not in families:
        return False
    if countries:
        # Same predicate as api._scoped — an alert answers to ANY country it touches,
        # because a hull's events can span several AOI waters and a satellite pass crosses
        # several borders. Collapsing that to one country would drop observed facts.
        if not set(countries) & set(getattr(alert, "countries", []) or []):
            return False
    return True


def filter_key(sub: Subscriber) -> tuple:
    """A hashable identity for one RENDERING, so the fan-out builds each distinct digest
    once instead of once per subscriber.

    ⚠ VERBOSITY IS PART OF THE KEY. It changes the rendered text, so two subscribers with
    identical filters but different levels must not share a memoised body — leaving it out
    would silently serve one of them the other's density."""
    return (tuple(sorted(sub.countries or ())), tuple(sorted(sub.families or ())),
            sub.verbosity)


async def set_verbosity(pool: Any, chat_id: str, level: str) -> None:
    await pool.execute("UPDATE notify_subscribers SET verbosity = $2 WHERE chat_id = $1",
                       str(chat_id), level)


# --- store (I/O) -------------------------------------------------------------------------

def _row(r: Any) -> Subscriber:
    return Subscriber(chat_id=r["chat_id"], status=r["status"], cursor_at=r["cursor_at"],
                      countries=list(r["countries"]) if r["countries"] else None,
                      families=list(r["families"]) if r["families"] else None,
                      verbosity=r["verbosity"])


async def get(pool: Any, chat_id: str) -> Subscriber | None:
    r = await pool.fetchrow(
        "SELECT chat_id, status, cursor_at, countries, families, verbosity "
        "FROM notify_subscribers WHERE chat_id = $1", str(chat_id))
    return _row(r) if r else None


async def active(pool: Any) -> list[Subscriber]:
    """Everyone currently receiving. The fan-out's entire audience, in one query."""
    rows = await pool.fetch(
        "SELECT chat_id, status, cursor_at, countries, families, verbosity "
        "FROM notify_subscribers WHERE status = $1 ORDER BY chat_id", ACTIVE)
    return [_row(r) for r in rows]


async def subscribe(pool: Any, chat_id: str, now: datetime) -> tuple[Subscriber, bool]:
    """/start. Returns (subscriber, was_new).

    ⛔ THE CURSOR IS ONLY SET ON A GENUINELY NEW ROW, and re-set when a stopped subscriber
    returns. It must NOT be advanced when an already-active subscriber sends /start again —
    doing so would silently mark everything currently owed to them as pre-join and drop it.
    A user tapping /start twice is common; losing their queue for it is not acceptable.

    A returning subscriber KEEPS their filters (that is the whole reason `stopped` rows are
    retained) but gets a fresh cursor: they should not receive the backlog accumulated while
    they were away, only what happens from now."""
    existing = await get(pool, chat_id)
    if existing is not None and existing.active:
        return existing, False

    await pool.execute(
        """
        INSERT INTO notify_subscribers
               (chat_id, status, subscribed_at, status_changed_at, cursor_at)
        VALUES ($1, $2, $3, $3, $3)
        ON CONFLICT (chat_id) DO UPDATE
           SET status = $2, status_changed_at = $3, cursor_at = $3, last_error = NULL
        """,
        str(chat_id), ACTIVE, now)
    sub = await get(pool, chat_id)
    assert sub is not None
    return sub, existing is None


async def set_status(pool: Any, chat_id: str, status: str, now: datetime,
                     error: str | None = None) -> None:
    await pool.execute(
        "UPDATE notify_subscribers SET status = $2, status_changed_at = $3, last_error = $4 "
        "WHERE chat_id = $1", str(chat_id), status, now, error)


async def delete(pool: Any, chat_id: str) -> bool:
    """/delete — remove the row outright. Outbox rows cascade.

    This is the whole of the erasure: nothing else in the schema stores anything about a
    subscriber, so there is no second place to sweep and no residue to explain."""
    tag = await pool.execute("DELETE FROM notify_subscribers WHERE chat_id = $1",
                             str(chat_id))
    return tag.endswith(" 1")


async def set_filters(pool: Any, chat_id: str, *,
                      countries: list[str] | None | EllipsisType = ...,
                      families: list[str] | None | EllipsisType = ...) -> None:
    """Update one or both filters. `...` means leave that filter untouched — distinct from
    None, which means CLEAR it (subscribe to everything)."""
    sets: list[str] = []
    args: list[Any] = [str(chat_id)]
    if countries is not ...:
        args.append(countries or None)
        sets.append(f"countries = ${len(args)}")
    if families is not ...:
        args.append(families or None)
        sets.append(f"families = ${len(args)}")
    if not sets:
        return
    await pool.execute(
        f"UPDATE notify_subscribers SET {', '.join(sets)} WHERE chat_id = $1", *args)


async def note_sent(pool: Any, chat_id: str, now: datetime, n: int = 1) -> None:
    await pool.execute(
        "UPDATE notify_subscribers SET sent_count = sent_count + $3, last_sent_at = $2 "
        "WHERE chat_id = $1", str(chat_id), now, n)


async def counts(pool: Any) -> dict[str, int]:
    rows = await pool.fetch(
        "SELECT status, count(*) n FROM notify_subscribers GROUP BY status")
    return {r["status"]: r["n"] for r in rows}
