"""NOTIFY-2 — the public surface: commands, the subscription lifecycle, and blocked users.

The command layer is where strangers meet this project, so the tests here are as much about
what the bot SAYS as about what it stores. Two things are pinned deliberately:

  * the honesty block reaches a newcomer BEFORE any alert can. An analyst infers that a
    composite is a lead; someone who just typed /start does not, and the per-alert caveats
    assume a reader who already knows what they are reading.
  * a blocked user is a DIFFERENT outcome from a failed send. Retrying a 403 forever burns
    the rate limit that live subscribers are queued behind, and can never succeed.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
# ⚠ BEFORE reading DATABASE_URL below. The DB tests here silently SKIPPED until this was
# added: `DSN` is read at import time, and nothing else in this module happened to import a
# package that calls load_dotenv() first. A skipped test reports green, so the whole
# blocked-user and lifecycle surface was passing without ever running.
from dotenv import load_dotenv

load_dotenv()

from backend.country_iso import iso_codes  # noqa: E402
from backend.models import Alert, AlertEntityRef  # noqa: E402
from backend.notify import commands, outbox, subscribers as subs  # noqa: E402
from backend.notify.telegram import TelegramBlocked, TelegramError, _classify  # noqa: E402

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)


def mk(family: str, *, countries=None, severity="info") -> Alert:
    return Alert(domain="air", family=family, signal_type="x", severity=severity,
                 severity_basis="b",
                 entity_ref=AlertEntityRef(kind="x", id="1"), countries=countries or [])


@pytest_asyncio.fixture
async def conn():
    import asyncpg
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    try:
        await c.execute("DELETE FROM notify_outbox")
        await c.execute("DELETE FROM notify_subscribers")
        yield c
    finally:
        await tr.rollback()
        await c.close()


# =========================================================================================
# PARSING (pure)
# =========================================================================================

def test_group_command_suffix_is_stripped():
    """Telegram appends @botname to every command sent in a GROUP. Without stripping it the
    bot would ignore its own commands the moment someone added it to one."""
    assert commands.parse("/start@AtheerRadarBot") == ("start", [])
    assert commands.parse("/countries@Bot SY TR") == ("countries", ["SY", "TR"])


def test_arabic_country_names_are_accepted():
    """⛔ FOUND BY SMOKE TEST, NOT BY REVIEW. `country_iso` holds the ENGLISH names — it is
    the filtering layer. On an Arabic-facing public bot, «تركيا» is the DEFAULT way a
    subscriber names a country, and the parser rejected it while passing every test written
    in ISO codes."""
    codes, bad = commands.parse_countries(["تركيا", "العراق", "عراق", "SY"])
    assert not bad
    assert set(codes) == {"TR", "IQ", "SY"}


def test_every_aoi_country_is_typeable_in_arabic():
    """Derived from the project's own seven rather than hand-listed, so a country added to
    country_iso without an Arabic label fails here instead of silently becoming
    unselectable."""
    for iso in iso_codes():
        codes, bad = commands.parse_countries([iso])
        assert codes == [iso] and not bad
    for name in commands._AR_COUNTRY:
        codes, bad = commands.parse_countries([name])
        assert codes and not bad, f"{name} should resolve"


def test_all_clears_the_filter():
    assert commands.parse_countries(["الكل"]) == (None, [])
    assert commands.parse_families(["all"]) == (None, [])


def test_unknown_tokens_are_reported_not_ignored():
    """A subscriber who names a country we do not cover must be TOLD, or they will read the
    resulting silence as coverage."""
    codes, bad = commands.parse_countries(["SY", "Narnia"])
    assert bad == ["Narnia"] and codes == ["SY"]


def test_every_selectable_family_has_an_arabic_label():
    assert set(commands.FAMILY_AR) == set(subs.FAMILIES)


# =========================================================================================
# THE FILTER PREDICATE (pure)
# =========================================================================================

def test_no_filter_means_everything():
    a = mk("military", countries=["SY"])
    assert subs.matches(a, None, None) is True
    assert subs.matches(a, [], []) is True


def test_filters_are_anded_not_ored():
    """Each added filter must REDUCE traffic. The union reading would make selecting a
    filter increase it, which is the opposite of what choosing one means."""
    a = mk("military", countries=["SY"])
    assert subs.matches(a, ["SY"], ["military"]) is True
    assert subs.matches(a, ["SY"], ["emergency"]) is False
    assert subs.matches(a, ["TR"], ["military"]) is False


def test_an_alert_answers_to_any_country_it_touches():
    """A hull's events can span several AOI waters; collapsing that to one country would
    drop observed facts. Same rule as api._scoped."""
    a = mk("maritime", countries=["CY", "IL"])
    assert subs.matches(a, ["IL"], None) is True
    assert subs.matches(a, ["CY"], None) is True
    assert subs.matches(a, ["SY"], None) is False


def test_identical_filters_share_one_render_key():
    """The fan-out memoises the digest render on this key, so two subscribers asking for the
    same thing must produce the same key regardless of the order they typed it."""
    a = subs.Subscriber("a", subs.ACTIVE, NOW, ["SY", "TR"], ["military"])
    b = subs.Subscriber("b", subs.ACTIVE, NOW, ["TR", "SY"], ["military"])
    assert subs.filter_key(a) == subs.filter_key(b)


# =========================================================================================
# THE HONESTY BLOCK
# =========================================================================================

def test_a_newcomer_is_told_what_the_alerts_are_not():
    """⛔ The three standing commitments, in plain language, BEFORE the first alert. The
    per-alert caveats assume a reader who already knows what a lead is."""
    for text in (commands.WELCOME, commands.HELP):
        assert "معلوماتي وليس حكم تهديد" in text, "military presence is not a threat"
        assert "وليس إثباتاً" in text, "a composite is a lead, not proof"
        assert "انتحال" in text, "AIS/ADS-B are spoofable"


def test_help_lists_every_command_in_the_menu():
    """The registered menu and the help text must not drift — a command Telegram advertises
    but /help never explains is a command nobody will use correctly."""
    for name, _ in commands.COMMAND_MENU:
        assert f"/{name}" in commands.HELP


# =========================================================================================
# LIFECYCLE
# =========================================================================================

@requires_db
@pytest.mark.asyncio
async def test_start_subscribes_and_welcomes(conn):
    out = await commands.handle(conn, "u1", "/start", NOW)
    assert commands.WELCOME in out
    sub = await subs.get(conn, "u1")
    assert sub is not None and sub.active and sub.cursor_at == NOW


@requires_db
@pytest.mark.asyncio
async def test_start_twice_does_not_move_the_cursor(conn):
    """⛔ Tapping /start again is common. Advancing the cursor would silently mark
    everything currently owed to them as pre-join and drop it."""
    await commands.handle(conn, "u1", "/start", NOW)
    out = await commands.handle(conn, "u1", "/start", NOW + timedelta(hours=1))
    assert out == [commands.ALREADY]
    assert (await subs.get(conn, "u1")).cursor_at == NOW


@requires_db
@pytest.mark.asyncio
async def test_stop_keeps_filters_and_start_restores_them(conn):
    """The only reason a stopped row is retained: /start must not reset what they chose."""
    await commands.handle(conn, "u1", "/start", NOW)
    await commands.handle(conn, "u1", "/countries SY TR", NOW)
    await commands.handle(conn, "u1", "/stop", NOW)

    assert (await subs.get(conn, "u1")).status == subs.STOPPED
    await commands.handle(conn, "u1", "/start", NOW + timedelta(hours=2))
    back = await subs.get(conn, "u1")
    assert back.active and back.countries == ["SY", "TR"]
    assert back.cursor_at == NOW + timedelta(hours=2), "no backlog from while they were away"


@requires_db
@pytest.mark.asyncio
async def test_delete_erases_everything(conn):
    """The whole erasure: nothing else in the schema stores anything about a subscriber."""
    await commands.handle(conn, "u1", "/start", NOW)
    await outbox.enqueue(conn, [("u1", "k", "نص")], NOW)

    out = await commands.handle(conn, "u1", "/delete", NOW)
    assert out == [commands.DELETED]
    assert await subs.get(conn, "u1") is None
    assert await outbox.depth(conn) == 0
    assert await conn.fetchval(
        "SELECT count(*) FROM notify_subscribers WHERE chat_id='u1'") == 0


@requires_db
@pytest.mark.asyncio
async def test_setting_a_country_filter_discloses_what_it_costs(conn):
    """⚠ 64 % of military aircraft carry no country attribution, so a country filter removes
    most of that family. Correct, but not guessable — so it is said at the moment the filter
    is set, not buried in /help."""
    await commands.handle(conn, "u1", "/start", NOW)
    out = await commands.handle(conn, "u1", "/countries SY", NOW)
    assert commands.COUNTRY_FILTER_NOTE in out[0]

    cleared = await commands.handle(conn, "u1", "/countries الكل", NOW)
    assert commands.COUNTRY_FILTER_NOTE not in cleared[0], "no warning when there is no filter"


@requires_db
@pytest.mark.asyncio
async def test_an_unrecognised_country_changes_nothing(conn):
    """All-or-nothing: a partly-understood filter would leave the subscriber believing they
    subscribed to something they did not."""
    await commands.handle(conn, "u1", "/start", NOW)
    await commands.handle(conn, "u1", "/countries SY", NOW)
    await commands.handle(conn, "u1", "/countries TR Narnia", NOW)
    assert (await subs.get(conn, "u1")).countries == ["SY"]


@requires_db
@pytest.mark.asyncio
async def test_commands_before_subscribing_are_answered(conn):
    for cmd in ("/status", "/stop", "/countries SY"):
        assert await commands.handle(conn, "ghost", cmd, NOW) == [commands.NOT_SUBSCRIBED]


@requires_db
@pytest.mark.asyncio
async def test_unknown_input_gets_help_not_silence(conn):
    """A public bot that ignores input reads as broken, and the person has no way to
    discover what it does understand."""
    assert await commands.handle(conn, "u1", "/wat", NOW) == [commands.HELP]


@requires_db
@pytest.mark.asyncio
async def test_start_survives_a_feed_outage(conn):
    """The current-picture summary is a nicety; the SUBSCRIPTION is not. A feed failure must
    degrade /start to a plain welcome, never fail it."""
    async def boom(_sub):
        raise RuntimeError("feed down")

    out = await commands.handle(conn, "u1", "/start", NOW, summary_fn=boom)
    assert out == [commands.WELCOME]
    assert (await subs.get(conn, "u1")).active


# =========================================================================================
# BLOCKED USERS
# =========================================================================================

def test_a_blocked_user_is_classified_apart_from_a_transient_failure():
    """⛔ NOT a TelegramError. Retrying a 403 is guaranteed to fail and burns the rate limit
    that live subscribers are queued behind, so it must never be caught by retry logic."""
    blocked = _classify(403, "Forbidden: bot was blocked by the user", {}, "u1")
    assert isinstance(blocked, TelegramBlocked)
    assert not isinstance(blocked, TelegramError)

    for desc in ("Forbidden: user is deactivated", "Bad Request: chat not found"):
        assert isinstance(_classify(400, desc, {}, "u1"), TelegramBlocked)


def test_transient_and_permanent_failures_are_distinguished():
    assert getattr(_classify(502, "Bad Gateway", {}, "u1"), "retryable", False) is True
    assert getattr(_classify(400, "can't parse entities", {}, "u1"), "retryable", False) is False


@pytest.mark.asyncio
async def test_an_oversized_queued_body_is_split_not_rejected():
    """⛔ REGRESSION — THIS REACHED PRODUCTION. `listen` chunked its replies; `drain` sent
    `item.body` raw. The first hourly digest was 12,404 characters against Telegram's 4,096
    limit, failed with `400: Bad Request: message is too long`, and sat in the outbox
    burning retries while two subscribers got nothing.

    Both paths now go through `gateway.send_message`, so the split cannot be forgotten on
    one of them. The test asserts the TRANSPORT never sees an oversized part — which is the
    property Telegram actually enforces."""
    from backend.notify import gateway, render

    sent: list[str] = []

    class _Tg:
        async def send(self, chat_id, text):
            assert render._tg_len(text) <= render.TELEGRAM_LIMIT, (
                f"a {render._tg_len(text)}-unit part reached the transport — "
                "this is the 400 that hit production")
            sent.append(text)

    body = "\n".join(f"• سطر رقم {i} — نصّ عربي طويل بما يكفي ليجاوز الحدّ الأقصى"
                     for i in range(300))
    assert render._tg_len(body) > render.TELEGRAM_LIMIT, "the body must actually be oversized"

    await gateway.send_message(_Tg(), "u1", body)

    assert len(sent) > 1, "it must have been split"
    rejoined = "\n".join(sent)
    for i in range(300):
        assert f"سطر رقم {i} " in rejoined, "no line may be dropped by the split"


@requires_db
@pytest.mark.asyncio
async def test_blocking_the_bot_unsubscribes_and_drops_the_queue(conn):
    """Marked `blocked`, never `stopped`: the user did not tell us to stop — the API told us
    they are unreachable, and an operator must be able to tell those apart."""
    from backend.notify.gateway import _handle_blocked

    await commands.handle(conn, "u1", "/start", NOW)
    await outbox.enqueue(conn, [("u1", "k1", "a"), ("u1", "k2", "b")], NOW)

    await _handle_blocked(conn, "u1")

    sub = await subs.get(conn, "u1")
    assert sub is not None and sub.status == subs.BLOCKED
    assert sub.status != subs.STOPPED
    assert await outbox.depth(conn) == 0
    assert await subs.active(conn) == []
