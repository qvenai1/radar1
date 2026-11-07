"""NOTIFY-2 — the tick and the fan-out, end to end against the real tables.

DB-backed and rolled back (the test_imo_consumers pattern): synthetic ticks run inside a
transaction that is never committed, so the live tables are untouched.

⛔ THE PROPERTIES THAT MUST HOLD OR THE BOT IS WORSE THAN NOTHING, detonated rather than
asserted:

  1. COLD START ENQUEUES NOTHING. The first run sees the whole standing picture — measured
     at 225 alerts, ~200 of them maritime leads days old.
  2. A FAILED DELIVERY IS NOT DISCHARGED. With a fan-out this is per RECIPIENT: one
     unreachable subscriber must not cost anyone else their message, and must not lose
     their own.
  3. THE LEDGER AND THE QUEUE ANSWER DIFFERENT QUESTIONS. An event is decided once; a
     delivery is owed per person.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from backend.models import Alert, AlertEntityRef
from backend.notify import outbox, policy, runner, subscribers as subs
from backend.notify.config import NotifySettings

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
CFG = NotifySettings(token="t", chat_id="", api_url="http://stub", window_hours=24)


def mk(signal_type: str, entity: str, *, ts=None, severity="info", family=None,
       domain="air", headline="نص", caveat="تحذير", countries=None) -> Alert:
    fam = family or {"emergency_squawk": "emergency", "military_aircraft": "military",
                     "military_satellite": "military", "maritime_composite": "maritime",
                     "implausible_position": "implausible"}[signal_type]
    return Alert(domain=domain, family=fam, signal_type=signal_type, severity=severity,
                 severity_basis="basis",
                 entity_ref=AlertEntityRef(kind="x", id=entity, label=entity),
                 ts=ts, headline=headline, caveat=caveat, countries=countries or [])


async def tick(conn, feed, when, monkeypatch):
    async def _stub(url, window_hours, session=None):
        return feed
    monkeypatch.setattr(runner, "fetch_alerts", _stub)
    return await runner.run_once(conn, CFG, now=when)


async def add_sub(conn, chat_id, when, *, countries=None, families=None):
    await subs.subscribe(conn, chat_id, when)
    if countries is not None or families is not None:
        await subs.set_filters(conn, chat_id,
                               countries=countries if countries is not None else ...,
                               families=families if families is not None else ...)
    return await subs.get(conn, chat_id)


async def queued_for(conn, chat_id):
    return [r["dedup_key"] for r in await conn.fetch(
        "SELECT dedup_key FROM notify_outbox WHERE chat_id = $1 ORDER BY id", chat_id)]


@pytest_asyncio.fixture
async def conn():
    """A connection in a transaction that is always rolled back."""
    import asyncpg
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    try:
        await c.execute("DELETE FROM notify_outbox")
        await c.execute("DELETE FROM notify_subscribers")
        await c.execute("DELETE FROM notify_deliveries")
        await c.execute("UPDATE notify_runner_state "
                        "SET baselined_at = NULL, digest_sent_at = NULL")
        yield c
    finally:
        await tr.rollback()
        await c.close()


# =========================================================================================
# COLD START
# =========================================================================================

@requires_db
@pytest.mark.asyncio
async def test_cold_start_enqueues_nothing_even_with_subscribers(conn, monkeypatch):
    """⛔ PROPERTY 1. 200 standing alerts and a waiting subscriber: the picture is recorded
    as known and NOTHING is queued. Their "what does it look like now" need is met by the
    /start summary, which reports counts rather than delivering the alerts."""
    await add_sub(conn, "u1", NOW)
    feed = [mk("maritime_composite", f"imo{i}", severity="high", domain="sea",
               ts="2026-08-04T20:26:57+00:00") for i in range(200)]

    r = await tick(conn, feed, NOW, monkeypatch)

    assert r.cold_start is True and r.baselined == 200
    assert r.enqueued == 0
    assert await outbox.depth(conn) == 0
    assert await conn.fetchval("SELECT count(*) FROM notify_deliveries") == 200


@requires_db
@pytest.mark.asyncio
async def test_unchanged_feed_enqueues_nothing(conn, monkeypatch):
    await add_sub(conn, "u1", NOW)
    feed = [mk("military_aircraft", f"ac{i}", ts="2026-08-11T07:34:55+00:00")
            for i in range(11)]
    await tick(conn, feed, NOW, monkeypatch)

    # the same aircraft, a fresh snapshot ts — the measured churn
    feed2 = [mk("military_aircraft", f"ac{i}", ts="2026-08-11T07:36:15+00:00")
             for i in range(11)]
    r = await tick(conn, feed2, NOW + timedelta(minutes=2), monkeypatch)

    assert r.suppressed == 11
    assert await outbox.depth(conn) == 0


# =========================================================================================
# FAN-OUT AND FILTERS
# =========================================================================================

@requires_db
@pytest.mark.asyncio
async def test_a_new_event_reaches_every_matching_subscriber(conn, monkeypatch):
    for u in ("u1", "u2", "u3"):
        await add_sub(conn, u, NOW)
    base = [mk("maritime_composite", "imo1", severity="high", domain="sea",
               ts="2026-08-04T20:26:57+00:00")]
    await tick(conn, base, NOW, monkeypatch)

    grown = base + [mk("maritime_composite", "imoNEW", severity="high", domain="sea",
                       ts="2026-08-11T09:00:00+00:00", headline="بدن جديد")]
    r = await tick(conn, grown, NOW + timedelta(minutes=2), monkeypatch)

    assert r.audience == 3 and r.events_instant == 1
    assert r.enqueued == 3, "one event, three subscribers, three debts"
    for u in ("u1", "u2", "u3"):
        assert await queued_for(conn, u) == ["maritime_composite:imoNEW"]


@requires_db
@pytest.mark.asyncio
async def test_country_filter_scopes_the_fan_out(conn, monkeypatch):
    """The filter uses the endpoint's own predicate: an alert answers to ANY country it
    touches, and an alert with NO country attribution answers to none."""
    await add_sub(conn, "syria_only", NOW, countries=["SY"])
    await add_sub(conn, "everything", NOW)

    await tick(conn, [mk("maritime_composite", "seed", severity="high", domain="sea",
                         ts="2026-08-01T00:00:00+00:00")], NOW, monkeypatch)

    feed = [
        mk("maritime_composite", "inSY", severity="high", domain="sea",
           ts="2026-08-11T09:00:00+00:00", countries=["SY", "LB"]),
        mk("maritime_composite", "inTR", severity="high", domain="sea",
           ts="2026-08-11T09:00:00+00:00", countries=["TR"]),
        mk("maritime_composite", "nowhere", severity="high", domain="sea",
           ts="2026-08-11T09:00:00+00:00", countries=[]),
    ]
    await tick(conn, feed, NOW + timedelta(minutes=2), monkeypatch)

    assert await queued_for(conn, "syria_only") == ["maritime_composite:inSY"]
    assert sorted(await queued_for(conn, "everything")) == [
        "maritime_composite:inSY", "maritime_composite:inTR", "maritime_composite:nowhere"]


@requires_db
@pytest.mark.asyncio
async def test_unattributed_alerts_are_excluded_by_any_country_filter(conn, monkeypatch):
    """⚠ MEASURED AT 64 % OF MILITARY AIRCRAFT. This is correct — the alert genuinely is not
    attributable to the country asked for, and it is what api._scoped does for ?country= —
    but it is not guessable, which is why commands.COUNTRY_FILTER_NOTE says it out loud the
    moment a country filter is set. The test pins BOTH the behaviour and the disclosure."""
    from backend.notify import commands

    unattributed = mk("military_aircraft", "ac1", countries=[])
    assert subs.matches(unattributed, None, None) is True
    assert subs.matches(unattributed, ["SY"], None) is False
    assert "الكل" in commands.COUNTRY_FILTER_NOTE, "the cost must be disclosed"


@requires_db
@pytest.mark.asyncio
async def test_family_filter_scopes_the_fan_out(conn, monkeypatch):
    await add_sub(conn, "emergencies", NOW, families=["emergency"])
    await add_sub(conn, "everything", NOW)
    await tick(conn, [mk("maritime_composite", "seed", severity="high", domain="sea",
                         ts="2026-08-01T00:00:00+00:00")], NOW, monkeypatch)

    feed = [mk("emergency_squawk", "abc", severity="high",
               ts="2026-08-11T09:00:00+00:00", headline="طوارئ"),
            mk("maritime_composite", "imoX", severity="high", domain="sea",
               ts="2026-08-11T09:00:00+00:00")]
    await tick(conn, feed, NOW + timedelta(minutes=2), monkeypatch)

    assert await queued_for(conn, "emergencies") == ["emergency_squawk:abc"]
    assert len(await queued_for(conn, "everything")) == 2


@requires_db
@pytest.mark.asyncio
async def test_stopped_subscribers_receive_nothing(conn, monkeypatch):
    await add_sub(conn, "gone", NOW)
    await add_sub(conn, "here", NOW)
    await subs.set_status(conn, "gone", subs.STOPPED, NOW)

    await tick(conn, [mk("maritime_composite", "seed", severity="high", domain="sea",
                         ts="2026-08-01T00:00:00+00:00")], NOW, monkeypatch)
    feed = [mk("maritime_composite", "imoX", severity="high", domain="sea",
               ts="2026-08-11T09:00:00+00:00")]
    r = await tick(conn, feed, NOW + timedelta(minutes=2), monkeypatch)

    assert r.audience == 1
    assert await queued_for(conn, "gone") == []
    assert await queued_for(conn, "here") == ["maritime_composite:imoX"]


@requires_db
@pytest.mark.asyncio
async def test_ledger_advances_with_no_subscribers(conn, monkeypatch):
    """⛔ Events are decided on the WORLD's schedule, not on whether anyone is listening.
    Otherwise the first person ever to /start would be told about everything that changed
    since the bot was built."""
    await tick(conn, [mk("maritime_composite", "seed", severity="high", domain="sea",
                         ts="2026-08-01T00:00:00+00:00")], NOW, monkeypatch)
    feed = [mk("maritime_composite", "imoX", severity="high", domain="sea",
               ts="2026-08-11T09:00:00+00:00")]
    r = await tick(conn, feed, NOW + timedelta(minutes=2), monkeypatch)

    assert r.audience == 0 and r.enqueued == 0
    assert await conn.fetchval(
        "SELECT count(*) FROM notify_deliveries WHERE dedup_key = $1",
        "maritime_composite:imoX") == 1, "the event was still decided"


@requires_db
@pytest.mark.asyncio
async def test_a_new_subscriber_does_not_receive_the_standing_picture(conn, monkeypatch):
    """The cold-start rule, applied per person. Someone joining long after the baseline gets
    only what happens next."""
    standing = [mk("maritime_composite", f"imo{i}", severity="high", domain="sea",
                   ts="2026-08-04T20:26:57+00:00") for i in range(50)]
    await tick(conn, standing, NOW, monkeypatch)                    # baseline, no audience

    await add_sub(conn, "newcomer", NOW + timedelta(minutes=1))
    r = await tick(conn, standing, NOW + timedelta(minutes=2), monkeypatch)

    assert r.audience == 1
    assert await queued_for(conn, "newcomer") == [], "no backlog on join"


# =========================================================================================
# THE OUTBOX — per-recipient delivery
# =========================================================================================

@requires_db
@pytest.mark.asyncio
async def test_a_failed_delivery_stays_owed_to_that_person_only(conn, monkeypatch):
    """⛔ PROPERTY 2. One recipient's failure must not discharge their debt, and must not
    touch anyone else's."""
    await add_sub(conn, "ok", NOW)
    await add_sub(conn, "failing", NOW)
    await tick(conn, [mk("maritime_composite", "seed", severity="high", domain="sea",
                         ts="2026-08-01T00:00:00+00:00")], NOW, monkeypatch)
    await tick(conn, [mk("maritime_composite", "imoX", severity="high", domain="sea",
                         ts="2026-08-11T09:00:00+00:00")],
               NOW + timedelta(minutes=2), monkeypatch)

    items = await outbox.due(conn, NOW + timedelta(minutes=3), 10)
    assert len(items) == 2
    ok = next(i for i in items if i.chat_id == "ok")
    bad = next(i for i in items if i.chat_id == "failing")

    await outbox.done(conn, ok.id)
    dropped = await outbox.fail(conn, bad, "boom", NOW + timedelta(minutes=3))

    assert dropped is False
    assert await queued_for(conn, "ok") == [], "delivered — debt discharged"
    assert await queued_for(conn, "failing") == ["maritime_composite:imoX"], "still owed"


@requires_db
@pytest.mark.asyncio
async def test_a_failing_row_backs_off_then_is_given_up_on_visibly(conn, monkeypatch):
    """Retries are bounded. Giving up drops the row, and the error is on it until then —
    a queue that retries forever is a queue that never drains."""
    await add_sub(conn, "u1", NOW)
    await tick(conn, [mk("maritime_composite", "seed", severity="high", domain="sea",
                         ts="2026-08-01T00:00:00+00:00")], NOW, monkeypatch)
    await tick(conn, [mk("maritime_composite", "imoX", severity="high", domain="sea",
                         ts="2026-08-11T09:00:00+00:00")],
               NOW + timedelta(minutes=2), monkeypatch)

    t = NOW + timedelta(minutes=3)
    dropped = False
    for _ in range(outbox.MAX_ATTEMPTS):
        due = await outbox.due(conn, t, 10)
        if not due:
            t += timedelta(hours=1)
            continue
        dropped = await outbox.fail(conn, due[0], "boom", t)
        t += timedelta(hours=1)
    assert dropped is True
    assert await outbox.depth(conn) == 0


@requires_db
@pytest.mark.asyncio
async def test_requeueing_the_same_event_refreshes_rather_than_duplicates(conn, monkeypatch):
    """⛔ A subscriber unreachable for several ticks must receive the CURRENT state ONCE,
    not a stack of superseded versions of the same alert."""
    await add_sub(conn, "u1", NOW)
    await outbox.enqueue(conn, [("u1", "k1", "نسخة أولى")], NOW)
    await outbox.enqueue(conn, [("u1", "k1", "نسخة ثانية")], NOW + timedelta(minutes=2))

    rows = await conn.fetch("SELECT body FROM notify_outbox WHERE chat_id = 'u1'")
    assert len(rows) == 1 and rows[0]["body"] == "نسخة ثانية"


@requires_db
@pytest.mark.asyncio
async def test_backoff_does_not_reset_when_an_event_is_re_rendered(conn, monkeypatch):
    """A recipient already in backoff must not be pulled to the front of the queue every
    tick — that would turn a slow recipient into a hot loop."""
    await add_sub(conn, "u1", NOW)
    await outbox.enqueue(conn, [("u1", "k1", "أولى")], NOW)
    item = (await outbox.due(conn, NOW, 10))[0]
    await outbox.fail(conn, item, "boom", NOW)
    after_fail = await conn.fetchval(
        "SELECT next_attempt_at FROM notify_outbox WHERE chat_id='u1'")

    await outbox.enqueue(conn, [("u1", "k1", "ثانية")], NOW + timedelta(minutes=2))
    assert await conn.fetchval(
        "SELECT next_attempt_at FROM notify_outbox WHERE chat_id='u1'") == after_fail


@requires_db
@pytest.mark.asyncio
async def test_deleting_a_subscriber_discards_what_was_owed(conn, monkeypatch):
    """/delete is a full erasure: the cascade means there is no second place to sweep."""
    await add_sub(conn, "u1", NOW)
    await outbox.enqueue(conn, [("u1", "k1", "نص")], NOW)
    assert await outbox.depth(conn) == 1

    assert await subs.delete(conn, "u1") is True
    assert await outbox.depth(conn) == 0


# =========================================================================================
# DIGEST
# =========================================================================================

@requires_db
@pytest.mark.asyncio
async def test_digest_is_rendered_per_filter_set(conn, monkeypatch):
    """Two subscribers with different filters get different digests; a subscriber whose
    filters admit nothing gets no digest row at all."""
    await add_sub(conn, "sy", NOW, countries=["SY"])
    await add_sub(conn, "all", NOW)
    await add_sub(conn, "cy", NOW, countries=["CY"])
    await conn.execute("UPDATE notify_runner_state SET baselined_at = $1, "
                       "digest_sent_at = $1", NOW)

    feed = [mk("military_aircraft", "ac1", ts="2026-08-11T07:00:00+00:00",
               countries=["SY"], headline="طائرة فوق سوريا"),
            mk("military_aircraft", "ac2", ts="2026-08-11T07:00:00+00:00",
               countries=["TR"], headline="طائرة فوق تركيا")]

    await tick(conn, feed, NOW + timedelta(minutes=2), monkeypatch)
    r = await tick(conn, feed, NOW + policy.DIGEST_INTERVAL + timedelta(minutes=1),
                   monkeypatch)

    assert r.digest_sent is True
    sy = await conn.fetchval("SELECT body FROM notify_outbox WHERE chat_id='sy'")
    all_ = await conn.fetchval("SELECT body FROM notify_outbox WHERE chat_id='all'")
    assert "سوريا" in sy and "تركيا" not in sy
    assert "سوريا" in all_ and "تركيا" in all_
    assert await queued_for(conn, "cy") == [], "no digest when nothing matched"
