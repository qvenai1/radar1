"""NOTIFY-1 — the bot's contract, and the measurements that produced it.

⛔ THESE TESTS DETONATE THE BUG RATHER THAN ASSERT THE FIX. The naive implementation of
"send updates only" is dedup-by-alert-content, and it fails in a way that looks fine in
review and floods in production. So the central tests below construct the exact input that
breaks it, assert the naive key WOULD have re-sent, and only then assert that ours does
not. A test that checked our behaviour alone would pass just as well against a rule that
suppressed everything — including a hijack squawk.

Each test states the measurement it encodes. The measurements were taken on this DB and
this feed on 2026-08-11 07:35-07:42 UTC and are cited in migration 039 and in
backend/notify/identity.py, so any of them can be re-measured against the same commands.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


from backend.alerts import SEVERITY_BASIS
from backend.models import Alert, AlertEntityRef
from backend.notify import identity, policy, render

NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)


def mk(signal_type: str, entity: str, *, ts: str | None = None, severity: str = "info",
       family: str | None = None, domain: str = "air", summary: str = "",
       headline: str = "", why: str = "", caveat: str = "",
       severity_basis: str = "basis") -> Alert:
    fam = family or {"emergency_squawk": "emergency", "military_aircraft": "military",
                     "military_satellite": "military",
                     "maritime_composite": "maritime",
                     "maritime_single": "maritime",
                     "implausible_position": "implausible"}[signal_type]
    return Alert(domain=domain, family=fam, signal_type=signal_type, severity=severity,
                 severity_basis=severity_basis,
                 entity_ref=AlertEntityRef(kind="x", id=entity, label=entity),
                 ts=ts, summary=summary, headline=headline, why=why, caveat=caveat)


def prior(sig: str, *, sent_ago: timedelta = timedelta(minutes=1),
          seen_ago: timedelta = timedelta(minutes=1)) -> policy.Prior:
    return policy.Prior(change_sig=sig, sent_at=NOW - sent_ago, last_seen_at=NOW - seen_ago)


# =========================================================================================
# THE CENTRAL MEASUREMENT — 11 phantom re-sends in 65 seconds.
# =========================================================================================

def test_military_snapshot_ts_churn_does_not_resend():
    """MEASURED: two polls 65 s apart produced 14 "new" alerts keyed by (type, entity, ts)
    and only 3 keyed by (type, entity). The 11 extra were military aircraft whose `ts` is
    the SNAPSHOT time — it advances every poll while the aircraft is unchanged. At a
    2-minute tick that is ~15,000 duplicate messages a day from one family."""
    poll1 = mk("military_aircraft", "46807b", ts="2026-08-11T07:34:55+00:00")
    poll2 = mk("military_aircraft", "46807b", ts="2026-08-11T07:36:15+00:00")

    # The bug, proven present in the naive scheme — otherwise this test proves nothing.
    naive1 = (poll1.signal_type, poll1.entity_ref.id, poll1.ts)
    naive2 = (poll2.signal_type, poll2.entity_ref.id, poll2.ts)
    assert naive1 != naive2, "the naive key must differ, or this test is not detonating it"

    k1, s1 = identity.identify(poll1)
    k2, s2 = identity.identify(poll2)
    assert k1 == k2 and s1 == s2

    d = policy.decide(poll2, prior(s1), NOW)
    assert d.route == policy.SUPPRESS and d.reason == "unchanged"


def test_military_position_and_altitude_churn_also_ignored():
    """Same defect by a different field: the overlay's altitude and country attribution
    move while the aircraft is simply still there. Presence is the whole claim."""
    a = mk("military_aircraft", "46807b", ts="2026-08-11T07:34:55+00:00",
           summary="military aircraft — C-27J, TR")
    b = mk("military_aircraft", "46807b", ts="2026-08-11T07:44:55+00:00",
           summary="military aircraft — C-27J, SY")
    b.countries = ["SY"]
    assert identity.identify(a)[1] == identity.identify(b)[1]
    assert policy.decide(b, prior(identity.identify(a)[1]), NOW).route == policy.SUPPRESS


def test_an_outage_does_not_reblast_the_standing_picture():
    """⛔ THE FLOOD THIS LAYER EXISTS TO PREVENT, ARRIVING BY THE ABSENCE DOOR.

    FOUND BY DRY RUN, NOT BY REVIEW. Running the real runner against the live feed with a
    63-minute gap between ticks re-announced ALL 220 standing alerts — because absence was
    allowed to re-arm every family, not just the presence ones.

    It never fires at the 2-minute production tick, which is what makes it dangerous: it
    fires only when the BOT has been down longer than the session window — a reboot, an
    outage, a full disk — so every recovery would have opened with a 200-message burst.

    A maritime hull unseen for any length of time is still the same standing picture. Only
    its SIGNATURE may make it news."""
    stale = prior("2026-08-04T20:26:57+00:00|high",
                  seen_ago=policy.SESSION_EXPIRY * 10, sent_ago=policy.SESSION_EXPIRY * 10)
    for st, sev, dom in (("maritime_composite", "high", "sea"),
                         ("implausible_position", "low", "sea"),
                         ("military_satellite", "info", "space")):
        a = mk(st, "e1", severity=sev, domain=dom, ts="2026-08-04T20:26:57+00:00")
        p = policy.Prior(change_sig=identity.change_sig(a),
                         sent_at=stale.sent_at, last_seen_at=stale.last_seen_at)
        assert policy.session_expired(p, NOW), "the staleness precondition must hold"
        assert not policy.can_rearm_on_absence(a), f"{st} must not re-arm on absence"
        d = policy.decide(a, p, NOW)
        assert d.route == policy.SUPPRESS and d.reason == "unchanged", \
            f"{st} re-announced itself after an outage"


def test_only_constant_signature_families_rearm_on_absence():
    """A family may be re-armed by absence ONLY if it has no other way to become news.
    Derived from the signature rule rather than hand-listed, so the two cannot drift."""
    for st in identity.PRESENCE_SIGNALS:
        a = mk(st, "x", ts="2026-08-11T07:00:00+00:00")
        b = mk(st, "x", ts="2026-08-11T09:00:00+00:00")
        assert identity.change_sig(a) == identity.change_sig(b), \
            f"{st} is treated as a presence family but its signature is not constant"


def test_military_aircraft_returns_after_a_real_absence():
    """A CONSTANT signature can only be re-armed by absence, so absence must work — else a
    military aircraft that leaves and comes back an hour later is suppressed forever.

    The window is checked against its own constant rather than a literal, so tuning
    SESSION_EXPIRY cannot silently break the behaviour it exists to provide."""
    a = mk("military_aircraft", "46807b", ts="2026-08-11T07:34:55+00:00")
    _, sig = identity.identify(a)

    still_here = prior(sig, seen_ago=policy.SESSION_EXPIRY - timedelta(seconds=1))
    assert policy.decide(a, still_here, NOW).route == policy.SUPPRESS

    long_gone = prior(sig, seen_ago=policy.SESSION_EXPIRY + timedelta(seconds=1))
    d = policy.decide(a, long_gone, NOW)
    assert d.route != policy.SUPPRESS and d.reason == "returned"


# =========================================================================================
# SATELLITE — the 2-minute gap that forbids a proximity heuristic.
# =========================================================================================

def test_two_passes_two_minutes_apart_are_distinct_alerts():
    """MEASURED on satellite_passes: norad 26407 has consecutive passes at 16:43:40 and
    16:45:40, both over Turkey — one physical transit split where the AOI mask breaks. It
    is the MINIMUM observed inter-pass gap, so any "same pass if within N minutes"
    heuristic with N >= 2 min would suppress a real pass."""
    p1 = mk("military_satellite", "26407", ts="2026-08-11T16:43:40+00:00", domain="space")
    p2 = mk("military_satellite", "26407", ts="2026-08-11T16:45:40+00:00", domain="space")
    assert identity.dedup_key(p1) != identity.dedup_key(p2)
    # and with no prior for the second, it is genuinely new
    assert policy.decide(p2, None, NOW).reason == "new"


def test_same_predicted_pass_is_one_alert():
    """The complement: an unchanged prediction re-read is not news."""
    p = mk("military_satellite", "26407", ts="2026-08-11T16:43:40+00:00", domain="space")
    _, sig = identity.identify(p)
    assert policy.decide(p, prior(sig), NOW).route == policy.SUPPRESS


# =========================================================================================
# EMERGENCY — the one family where a suppression would be unacceptable.
# =========================================================================================

def test_squawk_escalation_on_the_same_aircraft_is_not_suppressed():
    """⛔ THE OUTCOME THIS LAYER MUST NEVER PRODUCE. One aircraft squawks 7600, then 7700
    twenty minutes later. Keyed on the aircraft alone with a constant signature, the second
    would be suppressed as "already told you about this aircraft" — a hijack code silenced
    by an earlier radio failure. The squawk code lives only in `summary`, which is why
    _sig_emergency admits it."""
    radio = mk("emergency_squawk", "abc123", severity="high",
               summary="squawk 7600 — radio-failure", ts="2026-08-11T07:00:00+00:00")
    hijack = mk("emergency_squawk", "abc123", severity="high",
                summary="squawk 7500 — hijack", ts="2026-08-11T07:20:00+00:00")

    assert identity.dedup_key(radio) == identity.dedup_key(hijack), "same aircraft, one key"
    assert identity.change_sig(radio) != identity.change_sig(hijack), "different emergency"

    d = policy.decide(hijack, prior(identity.change_sig(radio)), NOW)
    assert d.route == policy.INSTANT and d.reason == "changed"


def test_emergency_has_no_cooldown():
    """A cooldown on the high-by-definition family would delay the one thing that must
    never be delayed."""
    assert policy.cooldown_for("emergency") == timedelta(0)


# =========================================================================================
# MARITIME — a standing picture must not re-blast on a reword.
# =========================================================================================

def test_maritime_reword_does_not_resend():
    """MEASURED: 200 maritime alerts stand in the feed at once. Their `severity_basis`,
    `headline` and `caveat` are prose this project actively edits — the «GNSS» rename
    touched every displayed string. If prose entered the signature, one copy edit would
    re-blast all 200. So the signature is built from structured fields only."""
    before = mk("maritime_composite", "9920291", severity="high", domain="sea",
                ts="2026-08-04T20:26:57+00:00",
                severity_basis="composite of 3 pattern types",
                headline="شذوذ موضع GNSS", caveat="caveat text v1")
    after = mk("maritime_composite", "9920291", severity="high", domain="sea",
               ts="2026-08-04T20:26:57+00:00",
               severity_basis="composite of 3 pattern types — reworded",
               headline="شذوذ إشارة الملاحة", caveat="caveat text v2 entirely rewritten")

    assert identity.identify(before) == identity.identify(after)
    assert policy.decide(after, prior(identity.change_sig(before)), NOW).route == policy.SUPPRESS


def test_maritime_new_event_resends():
    """The complement: last_event_ts advancing IS a new event, by definition of the field."""
    old = mk("maritime_composite", "9920291", severity="high", domain="sea",
             ts="2026-08-04T20:26:57+00:00")
    new = mk("maritime_composite", "9920291", severity="high", domain="sea",
             ts="2026-08-09T11:02:13+00:00")
    d = policy.decide(new, prior(identity.change_sig(old)), NOW)
    assert d.route == policy.INSTANT and d.reason == "changed"


def test_maritime_rank_escalation_resends():
    """A hull promoted to high is news even with no newer event."""
    med = mk("maritime_composite", "9920291", severity="medium", domain="sea",
             ts="2026-08-04T20:26:57+00:00")
    high = mk("maritime_composite", "9920291", severity="high", domain="sea",
              ts="2026-08-04T20:26:57+00:00")
    assert identity.change_sig(med) != identity.change_sig(high)
    assert policy.decide(high, prior(identity.change_sig(med)), NOW).route == policy.INSTANT


def test_reflagged_hull_stays_one_thing():
    """entity_ref.id is the IMO, chosen because it "survives a re-flag". A hull that changes
    MMSI must not re-announce itself as a new vessel."""
    a = mk("maritime_composite", "9920291", severity="high", domain="sea",
           ts="2026-08-04T20:26:57+00:00")
    b = mk("maritime_composite", "9920291", severity="high", domain="sea",
           ts="2026-08-04T20:26:57+00:00")
    b.entity_ref.live_key = "999999999"   # new MMSI, same hull
    assert identity.dedup_key(a) == identity.dedup_key(b)


# =========================================================================================
# IMPLAUSIBLE — the bursty family, and why the cooldown is policy and not identity.
# =========================================================================================

def test_implausible_change_is_real_but_rate_limited():
    """Its signature is the last implausible FIX, which advances as fast as the feed during
    a jamming wave. Identity must still SEE the change (it is a real new observation);
    policy is what withholds the message. Conflating the two would mean a tuned cooldown
    silently redefined what a change is."""
    a = mk("implausible_position", "563323800", severity="low", domain="sea",
           ts="2026-08-11T07:00:00+00:00")
    b = mk("implausible_position", "563323800", severity="low", domain="sea",
           ts="2026-08-11T07:05:00+00:00")
    assert identity.change_sig(a) != identity.change_sig(b), "identity sees the change"

    cd = policy.cooldown_for("implausible")
    assert cd > timedelta(0)

    inside = policy.decide(b, prior(identity.change_sig(a), sent_ago=cd - timedelta(minutes=1)), NOW)
    assert inside.route == policy.SUPPRESS and inside.reason == "cooldown"

    outside = policy.decide(b, prior(identity.change_sig(a), sent_ago=cd + timedelta(minutes=1)), NOW)
    assert outside.route == policy.DIGEST and outside.reason == "changed"


# =========================================================================================
# ROUTING — the commitments this layer inherits.
# =========================================================================================

def test_info_severity_never_interrupts():
    """⛔ THE STANDING COMMITMENT: military presence is "informational, NEVER a threat". An
    instant message IS a risk assertion, so info must never route to instant — for any
    family, present or future."""
    assert "info" not in policy.INSTANT_SEVERITIES
    for st in ("military_aircraft", "military_satellite"):
        assert policy.route_for(mk(st, "x", severity="info")) == policy.DIGEST


def test_only_high_interrupts():
    """The operator's choice, encoded once. Reading INSTANT_SEVERITIES rather than
    hardcoding "high" means retuning the policy cannot leave this test asserting the old
    rule while passing."""
    for sev in ("high", "medium", "low", "info"):
        expected = policy.INSTANT if sev in policy.INSTANT_SEVERITIES else policy.DIGEST
        assert policy.route_for(mk("maritime_composite", "h", severity=sev)) == expected


def test_burst_cap_spills_and_never_drops():
    """MEASURED risk: 0 maritime hulls have an event inside 72 h but 90 inside 168 h, so a
    single detector catch-up could rank a large batch high at once. The cap turns that into
    a readable digest — and must not lose anyone in the process."""
    batch = [mk("maritime_composite", f"imo{i}", severity="high", domain="sea") for i in range(90)]
    send_now, spilled = policy.apply_burst_cap(batch)
    assert len(send_now) == policy.INSTANT_BURST_CAP
    assert send_now + spilled == batch, "every alert survives the cap, in order"


def test_first_digest_fires_immediately_then_on_interval():
    assert policy.digest_due(None, NOW) is True
    assert policy.digest_due(NOW - policy.DIGEST_INTERVAL + timedelta(minutes=1), NOW) is False
    assert policy.digest_due(NOW - policy.DIGEST_INTERVAL, NOW) is True


# =========================================================================================
# DRIFT GATE — a new family cannot reach the operator unreviewed.
# =========================================================================================

def test_every_declared_family_has_an_identity_rule():
    """alerts.SEVERITY_BASIS is the endpoint's own per-signal taxonomy. Reading it here
    means adding a family there without an identity rule fails at import — the same idiom
    alerts.py uses for its Arabic registers."""
    assert set(SEVERITY_BASIS) <= set(identity._RULES)


def test_unknown_family_falls_back_noisily_not_silently():
    """The fallback must be able to RE-SEND and must never SUPPRESS: a duplicate is
    noticed, a permanently-swallowed family is not."""
    a = mk("maritime_composite", "z", ts="2026-08-11T07:00:00+00:00")
    a.signal_type = "some_future_family"
    b = mk("maritime_composite", "z", ts="2026-08-11T08:00:00+00:00")
    b.signal_type = "some_future_family"
    assert identity.dedup_key(a) != identity.dedup_key(b) or \
        identity.change_sig(a) != identity.change_sig(b)


# =========================================================================================
# RENDER — the caveat is the contract.
# =========================================================================================

def test_the_reservation_has_exactly_one_home():
    """The caveat is the field models.py calls mandatory, for the stated reason that "a lead
    read as a finding is the failure mode this project is built to avoid".

    ⛔ NEWS-4 REVERSED THIS AND THE ASSERTIONS BELOW ARE THE NEW CONTRACT, not a relaxation
    of the old one. The owner's brief (2026-08-22) removed the per-message reservation from
    the wire along with the sources, the codes and the rank justification. The chain now
    rests on ONE place, and this test is what keeps that place load-bearing:

      1. `commands.CAVEAT_BLOCK` exists, is substantial, and is shown at /start AND /help.
      2. The FALLBACK brief still carries its short caveat — that path renders an alert the
         dispatch template could not state, which is exactly where the reader has least
         context and the reservation is worth most.

    ⚠ `SHORT_CAVEAT` therefore stays populated and stays gated by render.py's assertion. A
    family reaching the fallback with no shortened reservation is still a defect."""
    from backend.notify import commands

    # 1 — the one place a reservation is now stated
    assert len(commands.CAVEAT_BLOCK) > 200, "the standing reservation must be substantial"
    for name, surface in (("WELCOME", commands.WELCOME), ("HELP", commands.HELP)):
        assert commands.CAVEAT_BLOCK in surface, f"/{name} lost the standing reservation"

    # 2 — and the fallback path has not lost its own
    for st in render.SHORT_CAVEAT:
        msg = render._render_brief_fallback(
            mk(st, "e1", severity="high", headline="H", caveat="FULL"))
        assert render.SHORT_CAVEAT[st] in msg, f"{st} fallback shipped without its reservation"
        assert "FULL" not in msg, f"{st} fallback still carries the long form"


def test_an_unknown_family_falls_back_to_the_FULL_caveat():
    """⛔ The fallback errs toward MORE reservation, never none. A family with no short line
    ships its full Arabic caveat; with neither, the always-populated severity_basis."""
    a = mk("maritime_composite", "x", severity="high", domain="sea",
           caveat="النصّ الكامل للتحذير")
    a.signal_type = "some_future_family"
    assert "النصّ الكامل للتحذير" in render.render_alert(a)

    b = mk("maritime_composite", "x", severity="high", domain="sea",
           severity_basis="a stronger lead, still not proof")
    b.signal_type = "some_future_family"
    assert "a stronger lead, still not proof" in render.render_alert(b)


def test_no_family_reaches_a_public_channel_without_a_short_caveat():
    """Drift gate, mirroring the import-time assertion: SEVERITY_BASIS is alerts.py's own
    taxonomy, so a new family cannot ship unshortened without this failing."""
    from backend.alerts import SEVERITY_BASIS
    assert set(SEVERITY_BASIS) <= set(render.SHORT_CAVEAT)


def test_info_gets_no_alarm_marker():
    """The commitment rendered: an info alert must not carry a red marker, which would
    assert in an icon exactly what the text refuses to assert."""
    msg = render.render_alert(mk("military_aircraft", "46807b", severity="info"))
    assert render.SEVERITY_ICON["info"] in msg
    assert render.SEVERITY_ICON["high"] not in msg


def test_non_live_signals_say_so():
    """Liveness is mixed and per-alert; a predicted pass must not read as happening now.

    ⛔ THE CHANNEL THAT CARRIES THIS CHANGED IN NEWS-4, AND IT GOT STRONGER. The old proof
    was the sourcing line «طبيعة البيانات: غير مباشرة» — a LABEL beside the message. That
    block is gone with the rest of the technical furniture, and the guarantee now lives in
    the sentence's own verb: «مرور مداري **متوقَّع**». A reader cannot skip a tense the way
    they skip a footer, so this is a better place for the claim, not a weaker one."""
    a = mk("military_satellite", "41019", severity="info", domain="space")
    a.live = False
    msg = render.render_alert(a)
    assert "متوقَّع" in msg, "a predicted pass must not read as happening now"
    assert "طبيعة البيانات" not in msg, "the technical sourcing block is gone (NEWS-4)"


def test_html_is_escaped():
    """A vessel name containing markup must not break the message or inject tags."""
    a = mk("maritime_composite", "x", severity="high", domain="sea")
    a.entity_ref.label = "M/V <b>ACME</b> & CO"
    msg = render.render_alert(a)
    assert "&lt;b&gt;ACME&lt;/b&gt; &amp; CO" in msg


def test_chunk_never_loses_a_line():
    """⛔ THE INVARIANT: nothing in render.py truncates. A body too long to send is
    CONTINUED, and every line survives."""
    body = "\n".join(f"سطر رقم {i} — نص عربي طويل بما يكفي ليتجاوز الحدّ" for i in range(400))
    parts = render.chunk(body)
    assert len(parts) > 1, "this body must actually exceed the limit or the test is vacuous"
    for p in parts:
        assert render._tg_len(p) <= render.TELEGRAM_LIMIT
    rejoined = "\n".join(parts)
    for line in body.split("\n"):
        assert line in rejoined


def test_chunk_hard_splits_an_unbreakable_line_rather_than_dropping_it():
    """No natural boundary exists inside one enormous line. It is split, never discarded."""
    body = "ط" * (render.TELEGRAM_LIMIT * 2 + 17)
    parts = render.chunk(body)
    assert all(render._tg_len(p) <= render.TELEGRAM_LIMIT for p in parts)
    assert "".join(parts) == body


def test_digest_states_its_reservation_once_per_section():
    """⛔ REGRESSION ON A LIVE FAILURE. The first hourly digest was 12,404 characters — four
    Telegram messages, mostly repeated warnings — because every distinct FULL caveat was
    listed. One short line per section now carries the same limit: measured 12,404 -> 3,430
    chars, 4 messages -> 1.

    ⚠ NOTIFY-3 CHANGED WHAT "no item may be dropped" MEANS, deliberately. A verbosity level
    now caps how many items are LISTED — but a cap that hid its own effect would make the
    section header's count disagree with the lines beneath it, so the remainder is always
    STATED. The invariant is "nothing is dropped SILENTLY", and `detailed` still lists every
    one."""
    alerts = [mk("maritime_composite", f"h{i}", severity="high", domain="sea",
                 headline=f"بدن {i}", caveat="نصّ طويل جداً " * 40) for i in range(20)]
    body = render.render_digest(alerts)

    # ⛔ NEWS-4 — the per-section reservation is gone from the bulletin with the per-message
    # one. What this test still guards is the half that never depended on it: no long caveat
    # ever reaches the digest, the capped remainder is always stated, and it fits one message.
    assert render.SHORT_CAVEAT["maritime_composite"] not in body, "no per-section reservation"
    assert "نصّ طويل جداً" not in body, "the long form must not reach the digest"
    assert "و15 أخرى" in body, "the capped remainder must be stated, never silently dropped"
    assert render._tg_len(body) < render.TELEGRAM_LIMIT, "one message"

    full = render.render_digest(alerts, verbosity=render.DETAILED)
    for i in range(20):
        assert f"بدن {i}" in full, "detailed lists every item"


def test_each_section_is_still_its_own_section():
    """⛔ WHAT THIS TEST USED TO PROVE IS GONE; WHAT REPLACED IT IS THE REASON IT EXISTED.
    It asserted that two families in one digest never share one warning — the real subject
    being that the families stay SEPARATE and one family's framing never covers another's.
    NEWS-4 removed the per-section reservation, so the separation is now asserted directly:
    two sections, two headers, and no reservation belonging to either."""
    body = render.render_digest([
        mk("maritime_composite", "h1", severity="high", domain="sea", headline="بدن"),
        mk("military_aircraft", "a1", headline="طائرة")])
    from backend.notify import newsroom as NR
    assert NR.edit(render.SIGNAL_AR["maritime_composite"]) in body
    assert NR.edit(render.SIGNAL_AR["military_aircraft"]) in body
    assert render.SHORT_CAVEAT["maritime_composite"] not in body
    assert render.SHORT_CAVEAT["military_aircraft"] not in body


def test_digest_states_the_spill():
    """The burst valve must be visible in the channel, or the cap silently shapes what the
    operator believes they saw."""
    alerts = [mk("maritime_composite", f"h{i}", severity="high", domain="sea") for i in range(3)]
    assert "3" in render.render_digest(alerts, spilled=3)


def test_digest_sections_follow_the_feed_ordering():
    """A digest must not imply a different priority from the dashboard."""
    alerts = [mk("military_aircraft", "a", severity="info"),
              mk("emergency_squawk", "b", severity="high", summary="squawk 7700 — x")]
    body = render.render_digest(alerts)
    assert body.index(render.SIGNAL_AR["emergency_squawk"]) < \
        body.index(render.SIGNAL_AR["military_aircraft"])


# =========================================================================================
# NEWS-1 — the news-brief format, and the two claims it refuses to make
# =========================================================================================

def test_the_register_never_calls_a_vessel_observation_unusual():
    """⛔ THE REQUESTED SAMPLE SAID «مؤشرات غير اعتيادية», AND THE SYSTEM CANNOT SUPPORT IT.
    "Unusual" is a comparison against a norm, and BASELINE-1 exists precisely because that
    claim needs a measured baseline — built for SATELLITES only (migration 041). No maritime
    baseline exists: the composite reports that N pattern types COINCIDED, which is a count,
    not a judgement about how normal that is for this hull.

    Writing it would assert for free exactly what an entire layer was built to measure."""
    from backend import summaries_ar as AR
    maritime_text = " ".join([
        AR.NEWS_LEDE_MARITIME, AR.NEWS_LEDE_MARITIME_ONE, AR.NEWS_LEDE_IMPL,
        AR.NEWS_LEDE_MT, AR.MT_WHY, AR.MT_CAVEAT, AR.CAVEAT_COMPOSITE,
    ])
    assert "غير اعتيادي" not in maritime_text, \
        "only the satellite family may compare against a norm — it has a baseline"


def test_no_reservation_names_an_offence():
    """⛔ THE OTHER REFUSAL, AND IT IS SUBTLER. The sample's reservation read "not conclusive
    proof of ILLICIT ACTIVITY". Denying a crime NAMES it: the reader is handed the hypothesis
    by the very sentence meant to withhold it. Every existing caveat is about the EVIDENCE
    («دليل أقوى وليس إثباتاً», «تفسيرات مشروعة») and never about a suspected offence."""
    from backend import summaries_ar as AR
    for name in dir(AR):
        if not (name.startswith("CAVEAT") or name.startswith("MT_CAVEAT")
                or name.startswith("NEWS_")):
            continue
        val = getattr(AR, name)
        for text in (val.values() if isinstance(val, dict) else [val]):
            if isinstance(text, str):
                for banned in ("غير مشروع", "غير قانوني", "تهريب", "جريمة"):
                    assert banned not in text, f"{name} names an offence: {banned}"


def test_a_dispatch_is_classification_event_and_time_and_nothing_else():
    """⛔ NEWS-4 — THE OWNER'S TEMPLATE, ASSERTED BY WHAT IT NO LONGER CONTAINS. The old test
    demanded five parts (lede, body, classification, reservation, sourcing); the brief that
    replaced them is four lines and the removals ARE the item, so they are what this asserts.

    ⚠ The negative assertions are the load-bearing half. A layout test that only checked the
    parts present would still pass with the whole sourcing block silently restored beneath."""
    from backend import summaries_ar as AR
    a = mk("maritime_composite", "9920291", severity="high", domain="sea",
           headline=f"تزامن نمطين: {AR.PATTERN_AR['zone_dwell']}، {AR.PATTERN_AR['ais_gap']}",
           why="سبب الرتبة", caveat="تحفّظ")
    a.lede = "عنوان الخبر"
    a.source = "maritime_hull_composite"
    a.vintage = "2026-07-19T21:28:00+00:00"
    a.countries = ["SY"]
    a.ts = "2026-08-22T09:12:00+00:00"
    msg = render.render_alert(a)

    # present: the classification, the event with its entity and place, the local clock
    assert "رصد بحري" in msg, "the kicker classifies"
    assert "9920291" in msg and "سوريا" in msg, "the body names the entity and the place"
    assert "بتوقيت دمشق" in msg

    # ⛔ absent: everything the brief removes
    assert "maritime_hull_composite" not in msg, "no platform or source name"
    assert AR.NEWS_LBL_SOURCE not in msg and AR.NEWS_LBL_NATURE not in msg
    assert AR.NEWS_LBL_VINTAGE not in msg and "يوليو/تموز" not in msg
    assert AR.NEWS_LBL_CLASS not in msg and "سبب الرتبة" not in msg, "no rank justification"
    assert "تحفّظ" not in msg and "⚠️" not in msg, "no per-message reservation"
    assert "عنوان الخبر" not in msg, "the lede is not repeated beside the body"
    assert "Z" not in msg, "the UTC stamp is an operator's tool, not a reader's"


def test_the_lede_never_characterises_the_observation():
    """A lede states WHAT and WHERE. An adjective of concern («لافتة», «مقلقة») is a
    judgement, and the severity badge beside it is the only ranking this layer may show."""
    from backend import summaries_ar as AR
    ledes = [getattr(AR, n) for n in dir(AR) if n.startswith("NEWS_LEDE_")]
    for t in ledes:
        for banned in ("لافت", "مقلق", "خطير", "مريب"):
            assert banned not in t, f"a lede must not characterise: {banned}"


def test_a_family_without_a_lede_falls_back_and_invents_nothing():
    """Empty `lede` means "no news title yet" — the renderer uses the entity label rather
    than composing prose of its own."""
    a = mk("military_aircraft", "46807b", severity="info", headline="نصّ")
    assert a.lede == ""
    assert "46807b" in render.render_alert(a)


# =========================================================================================
# NEWS-2 — the digest as a bulletin
# =========================================================================================

def _news(signal_type: str, entity: str, lede: str, headline: str = "",
          severity: str = "info", domain: str = "air"):
    a = mk(signal_type, entity, severity=severity, domain=domain,
           headline=headline or f"متن {entity}", caveat="تحذير")
    a.lede = lede
    return a


def test_a_bulletin_item_is_a_sentence_not_the_body():
    """⛔ NEWS-2. A bulletin item is a news SENTENCE, and the lede already is one. It is also
    SHORTER — measured on the live feed, lede averages 57 characters against headline 167, a
    66 % reduction — which is why making the digest more journalistic did not re-inflate it.
    The 12,404-character first digest is why that mattered.

    ⛔ NEWS-4 MOVED THE ITEM ONE STEP FURTHER, and this test moved with it. The item was the
    `lede`; it is now the composed DISPATCH sentence, which is shorter still and is the same
    sentence the instant message carries — so a reader meets one phrasing, not two. What the
    test guards is unchanged: the item is a sentence, and the long body is not in it."""
    body = render.render_digest([_news("military_aircraft", "AC1", "عنوان خبري للطائرة",
                                       "متن مفصّل جداً عن الطائرة")])
    assert "رُصدت طائرة عسكرية" in body and "AC1" in body, "the item is the dispatch sentence"
    assert "متن مفصّل جداً" not in body, "balanced shows the item, not the body"


def test_detailed_adds_the_body_beneath_the_lede():
    body = render.render_digest(
        [_news("military_aircraft", "AC1", "عنوان قصير",
               "متن أطول بكثير يضيف تفاصيل حقيقية لم يذكرها العنوان إطلاقاً")],
        verbosity=render.DETAILED)
    # ⚠ The item is the dispatch now (NEWS-4); `detailed` still adds the fuller body under it.
    assert "رُصدت طائرة عسكرية" in body and "متن أطول بكثير" in body


def test_a_detail_line_that_adds_nothing_is_not_printed():
    """⚠ SEEN IN A RENDERED DIGEST, NOT IN REVIEW. For maritime_single the lede and the
    headline state the same fact in the same breath, so `detailed` printed it twice. A detail
    line exists to ADD information; repetition is what teaches a reader to skim."""
    body = render.render_digest(
        [_news("maritime_single", "S1", "لِيد طويل جدّاً يقول كلّ شيء عن السفينة",
               "متن أقصر")], verbosity=render.DETAILED)
    assert body.count("متن أقصر") == 0, "a body no longer than its lede adds nothing"


def test_every_lede_names_its_subject():
    """⛔ FOUND BY RENDERING A DIGEST. The satellite and aircraft ledes first read «مرور
    مداري متوقَّع لجسم ذي تصنيف عسكري{where}» — no object name — so five passes produced five
    near-identical lines a reader could not tell apart. Invisible when reading one alert;
    obvious the moment they are listed together."""
    from backend import summaries_ar as AR
    for name in ("NEWS_LEDE_MARITIME", "NEWS_LEDE_MARITIME_ONE", "NEWS_LEDE_IMPL",
                 "NEWS_LEDE_MT"):
        assert "{vessel}" in getattr(AR, name), f"{name} must name the vessel"
    assert "{object}" in AR.NEWS_LEDE_MIL_SAT, "the satellite lede must name the object"
    assert "{who}" in AR.NEWS_LEDE_MIL_AIR, "the aircraft lede must name the aircraft"


def test_the_bulletin_still_collapses_routine_and_states_its_counts():
    """The NEWS-2 layout must not undo BASELINE-1's collapse or the cap's honesty."""
    items = ([_news("military_satellite", f"S{i}", f"لِيد {i}", domain="space")
              for i in range(3)]
             + [_news("military_satellite", f"R{i}", f"روتيني {i}", domain="space")
                for i in range(4)])
    for a in items[3:]:
        a.baseline_verdict = "routine"
    body = render.render_digest(items)
    assert "ضمن النمط المعتاد لأجسامها" in body
    assert "<b>4</b>" in body
    assert "روتيني 0" not in body
