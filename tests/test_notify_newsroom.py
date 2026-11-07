"""NEWS-3 — the newsroom register, and the two lines it is not allowed to cross.

⛔ WHAT THESE TESTS EXIST TO CATCH. The register is a TERM SUBSTITUTION over prose that
five items were spent calibrating (BE-SUMMARY-1..5), applied in a surface — a public
Telegram channel — that nobody reviews before it sends. Two failure modes follow from that
and each has a gate below:

  1. A banned term SURVIVES, because a template upstream changed and the lexicon never
     learned about it. Caught by rendering real templates and auditing the output.
  2. A term the owner REFUSED appears, because a later "small" lexicon addition crossed
     from description into inference. Caught by `inference_audit` over the same renders.

⛔ AND THE THIRD, WHICH IS THE ONE THAT MATTERS MOST: the substitution silently DROPS
something. The caveat is the field this project calls mandatory, so it gets its own test
that survives the edit non-empty, and `edit` gets a test that it never returns "" for
non-empty input.

⚠ These gates are proven to be able to FAIL — `test_audit_detonates` and
`test_inference_audit_detonates` feed them the banned text directly. This project has been
bitten three times by a check that could only pass.
"""

from __future__ import annotations

import pytest

from backend import summaries_ar as AR
from backend.models import Alert, AlertEntityRef
from backend.notify import commands, newsroom as NR, render

# ---- every calibrated template the lexicon has to cover, named so a failure says which ----
TEMPLATES: dict[str, str] = {
    "GNSS_HEAD": AR.GNSS_HEAD,
    "ZONE_DWELL_HEAD": AR.ZONE_DWELL_HEAD,
    "RENDEZVOUS_HEAD": AR.RENDEZVOUS_HEAD,
    "COMPOSITE_HEAD": AR.COMPOSITE_HEAD,
    "COMPOSITE_HEAD_DUAL": AR.COMPOSITE_HEAD_DUAL,
    "IMPL_HEAD_LEAD": AR.IMPL_HEAD_LEAD,
    "NEWS_LEDE_IMPL": AR.NEWS_LEDE_IMPL,
    "EMG_WHY": AR.EMG_WHY,
    "CAVEAT_GNSS": AR.CAVEAT_GNSS,
    "CAVEAT_RENDEZVOUS": AR.CAVEAT_RENDEZVOUS,
    "CAVEAT_ZONE_DWELL": AR.CAVEAT_ZONE_DWELL,
    "EMG_CAVEAT_7500": AR.EMG_CAVEAT_7500,
    "EMG_CAVEAT_7600": AR.EMG_CAVEAT_7600,
    "MT_WHY": AR.MT_WHY,
    "MT_CAVEAT": AR.MT_CAVEAT,
    **{f"SQUAWK_{k}": v for k, v in AR.SQUAWK_MEANING_AR.items()},
    **{f"PATTERN_{k}": v for k, v in AR.PATTERN_AR.items()},
    **{f"MT_PATTERN_{k}": v for k, v in AR.MT_PATTERN_AR.items()},
    **{f"SIGNAL_{k}": v for k, v in render.SIGNAL_AR.items()},
    **{f"SHORT_CAVEAT_{k}": v for k, v in render.SHORT_CAVEAT.items()},
}


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_no_banned_term_survives_the_edit(name):
    """Every shipped Arabic template, run through the register, carries no banned term.

    ⚠ Parametrised by NAME so a failure names the template that outgrew the lexicon rather
    than only the word that leaked."""
    out = NR.edit(TEMPLATES[name])
    assert NR.audit(out) == [], f"{name} still carries {NR.audit(out)}: {out!r}"


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_edit_never_asserts_a_cause(name):
    """⛔ THE OWNER'S LINE. «حرّر الأسلوب، واحفظ الوصف» — the register may not introduce a
    word that names a cause, an intent or an act the detector never measured."""
    out = NR.edit(TEMPLATES[name])
    assert NR.inference_audit(out) == [], f"{name} asserts {NR.inference_audit(out)}: {out!r}"


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_edit_never_empties_a_field(name):
    """Nothing is dropped. A substitution that returned "" would silently delete a caveat."""
    src = TEMPLATES[name]
    if src.strip():
        assert NR.edit(src).strip(), f"{name} was emptied by the register"


def test_audit_detonates():
    """⛔ THE GATE CAN FAIL. Fed the pre-edit text, the audit must report it — otherwise
    every green run above proves only that the audit is blind."""
    assert NR.audit(AR.GNSS_HEAD), "audit passed text that still says «شذوذ»"
    assert NR.audit(AR.IMPL_HEAD_LEAD), "audit passed «غير معقول» and «بدن»"
    assert NR.audit(AR.EMG_WHY), "audit passed «بحكم التعريف»"


def test_inference_audit_detonates():
    """The refused half of the requested spec, fed in directly."""
    assert NR.inference_audit("تلاعب بالبيانات وتزييف إحداثيات (Spoofing)")
    assert NR.inference_audit("التصاق بسفينة أخرى (مناورة شحن/تفريغ)")


def test_lexicon_entries_all_carry_a_rationale():
    """The third column is the audit trail for editing calibrated prose; it is not optional."""
    for find, repl, why in NR.LEXICON:
        assert find and repl and why.strip(), f"rule {find!r} has no rationale"
        assert find != repl, f"rule {find!r} is a no-op"


def test_edit_is_single_pass_and_idempotent():
    """⛔ A rule's OUTPUT must never be re-read by another rule. Editing twice must equal
    editing once — if it does not, the substitutions are compounding into prose nobody
    wrote, which is precisely what a `str.replace` loop would have done here."""
    for src in TEMPLATES.values():
        once = NR.edit(src)
        assert NR.edit(once) == once, f"not idempotent: {src!r} -> {once!r}"


def test_emphasis_does_not_nest_or_escape():
    """Bold is applied post-escape, in one pass: a country named twice gets two flat tags,
    and the tag itself is never re-emphasised."""
    out = NR.emphasise("سوريا وتركيا وسوريا")
    assert out.count("<b>") == 3 and out.count("</b>") == 3
    assert "<b><b>" not in out
    # a needle that is not a country or the label is left alone
    assert NR.emphasise("سفينة مجهولة") == "سفينة مجهولة"


# =========================================================================================
# END TO END — a rendered brief, which is what actually reaches a subscriber.
# =========================================================================================

def _alert(**kw) -> Alert:
    base = dict(domain="sea", family="maritime", signal_type="maritime_composite",
                severity="high", severity_basis="basis",
                entity_ref=AlertEntityRef(kind="vessel", id="1", label="M/V TEST"),
                ts="2026-08-22T09:00:00+00:00")
    base.update(kw)
    return Alert(**base)


RENDERED_CASES = [
    ("maritime", _alert(
        headline=AR.COMPOSITE_HEAD.format(n=3, waters=" قرب مياه سوريا", patterns="…"),
        why="الرتبة مرتفعة", caveat=AR.CAVEAT_GNSS,
        lede="رصد تزامن ثلاثة أنماط على السفينة M/V TEST قرب مياه سوريا")),
    ("emergency", _alert(
        domain="air", family="emergency", signal_type="emergency_squawk",
        # ⚠ the machine-readable field `build_emergency_alerts` writes; without it the
        # dispatch cannot know the code and correctly degrades to the fallback brief
        summary="squawk 7500 — unlawful interference", countries=["TR"],
        headline=AR.EMG_HEAD.format(meaning=AR.SQUAWK_MEANING_AR["7500"],
                                    who="الطائرة ABC123", where=" في أجواء تركيا"),
        why=AR.EMG_WHY, caveat=AR.EMG_CAVEAT_BASE + AR.EMG_CAVEAT_7500,
        lede=AR.NEWS_LEDE_EMERGENCY.format(meaning=AR.SQUAWK_MEANING_AR["7500"],
                                           where=" في أجواء تركيا"))),
    ("implausible", _alert(
        family="implausible", signal_type="implausible_position", severity="low",
        headline=AR.IMPL_HEAD_LEAD.format(waters=" قرب مياه قبرص", kinds="قفزة وعودة"),
        why="الرتبة منخفضة", caveat=AR.IMPL_CAVEAT,
        lede=AR.NEWS_LEDE_IMPL.format(vessel="M/V TEST", waters=" قرب مياه قبرص"))),
]


@pytest.mark.parametrize("name,alert", RENDERED_CASES, ids=[c[0] for c in RENDERED_CASES])
def test_rendered_brief_is_clean(name, alert):
    """The whole message, as it goes on the wire."""
    msg = render.render_alert(alert)
    assert NR.audit(msg) == [], f"{name} brief leaked {NR.audit(msg)}"
    assert NR.inference_audit(msg) == [], f"{name} brief asserts {NR.inference_audit(msg)}"


@pytest.mark.parametrize("name,alert", RENDERED_CASES, ids=[c[0] for c in RENDERED_CASES])
def test_the_fallback_brief_keeps_its_reservation(name, alert):
    """⛔ NEWS-4 TOOK THE RESERVATION OFF THE DISPATCH, NOT OFF THE FALLBACK. That path
    renders an alert the template could not state from structured facts — the case where the
    reader has least context — so it is the one place a per-message caveat still earns its
    line. Asserted per family so a future family cannot reach it bare."""
    assert "⚠️" in render._render_brief_fallback(alert), f"{name} fallback lost its reservation"


def test_brief_opens_with_its_kicker():
    """The owner's classification line, on the family it belongs to."""
    msg = render.render_alert(RENDERED_CASES[1][1])
    assert msg.splitlines()[0].endswith("<b>عاجل | طوارئ جوية</b>")


# =========================================================================================
# NEWS-4 — the dispatch template.
# =========================================================================================

def test_the_dispatch_carries_no_source_code_or_justification():
    """⛔ THE FOUR REMOVALS, ASSERTED AS REMOVALS. Each was a visible part of the message
    before this item, so each is a real regression risk if a later change restores it."""
    for _name, a in RENDERED_CASES:
        msg = render.render_alert(a)
        if msg.startswith("🔴 <b>") or msg.startswith("🟠 <b>") or msg.startswith("🟡 <b>"):
            for code in ("7500", "7600", "7700"):
                assert code not in msg, f"a navigational code reached the wire: {code}"
            assert "المصدر" not in msg and "OpenSky" not in msg and "latest.json" not in msg
            assert "طبيعة البيانات" not in msg and "آخر تحديث" not in msg
            assert "التصنيف" not in msg


def test_the_kicker_and_the_body_do_not_repeat_each_other():
    """«منع تكرار المعلومة بين العنوان الرئيسي والمتن» — the kicker classifies, the body
    reports. Neither may contain the other."""
    for _name, a in RENDERED_CASES:
        msg = render.render_alert(a)
        lines = [l for l in msg.splitlines() if l.strip()]
        kick = NR.kicker(a.signal_type)
        if not kick or len(lines) < 2:
            continue
        assert kick not in lines[1], "the body repeats the kicker"


def test_a_missing_fact_degrades_instead_of_being_invented():
    """⛔ THE RULE THAT KEEPS THE TEMPLATE HONEST. A maritime alert naming no known pattern
    cannot be stated in the owner's sentence, so `dispatch` returns None and the message
    falls back — it never emits «رصد نشاط» or any other filler for the missing event."""
    a = _alert(headline="نصّ لا يذكر أيّ نمط معروف", caveat="تحفّظ", lede="ليد")
    assert NR.dispatch(a) is None
    msg = render.render_alert(a)
    assert "⚠️" in msg, "the fallback brief is what renders it"


def test_an_unattributed_alert_states_no_place():
    """⛔ `countries` EMPTY MEANS 'not attributable', and models.py says so outright. The
    dispatch must render that as ABSENCE — never «المنطقة» or «موقع غير محدّد»."""
    a = _alert(headline=f"X {AR.PATTERN_AR['zone_dwell']}", countries=[])
    body = NR.dispatch(a)
    assert body and body.endswith("."), "a sentence still renders"
    assert "قبالة سواحل" not in body and "غير محدّد" not in body


def test_the_hijack_code_keeps_its_passive_verb():
    """⛔ 7500 IS TRANSMITTED, NEVER DECLARED. `summaries_ar` draws that line deliberately —
    the aircraft is not asserted to be the willing reporter of a hijack code — and dropping
    the number must not quietly drop the distinction with it."""
    a = _alert(domain="air", family="emergency", signal_type="emergency_squawk",
               summary="squawk 7500 — unlawful interference", countries=["TR"])
    assert NR.dispatch(a).startswith("بُثّ ")
    b = _alert(domain="air", family="emergency", signal_type="emergency_squawk",
               summary="squawk 7700 — general emergency", countries=["TR"])
    assert NR.dispatch(b).startswith("أعلنت ")


def test_an_unparsable_squawk_does_not_guess_a_code():
    """The lookup reads a fixed English prefix alerts.py writes. Anything else -> fallback."""
    a = _alert(domain="air", family="emergency", signal_type="emergency_squawk",
               summary="something else entirely", countries=["TR"])
    assert NR.dispatch(a) is None


def test_every_pattern_has_a_dispatch_clause():
    """⛔ COVERAGE, GATED. A pattern added upstream without a clause here would silently
    render its hull through the fallback forever — visible only as a message that never
    modernised. The sets must match exactly."""
    assert set(NR.PATTERN_PLAIN) == set(AR.PATTERN_AR)
    for pat, tmpl in NR.PATTERN_PLAIN.items():
        assert "{v}" in tmpl, f"{pat} has nowhere to put the vessel"


def test_the_level_word_agrees_with_its_noun():
    """⛔ «مستوى» IS MASCULINE. Reusing `RANK_AR` («مرتفعة», written for «الرتبة») would put
    a grammar error on every high alert, which is why LEVEL_AR exists as its own set."""
    assert NR.level_line("high") == "مستوى التنبيه: مرتفع"
    assert set(NR.LEVEL_AR) >= {"high", "medium", "low", "info"}
    assert NR.level_line("nonsense") == "", "an unknown severity states no level"


def test_urgency_word_is_on_the_emergency_family_alone():
    """⛔ «عاجل» IS A CLAIM ABOUT URGENCY. Military presence is INFO below LOW by an explicit
    commitment; a «عاجل» over it would assert in the kicker what the body refuses to."""
    for sig, text in NR.KICKER.items():
        if sig != "emergency_squawk":
            assert "عاجل" not in text, f"{sig} claims urgency in its kicker"


def test_digest_is_clean_and_carries_no_reservation():
    """⛔ NEWS-4 — the per-section reservation went with the per-message one. The masthead and
    the register still hold, and the count is now asserted at ZERO so a restored line fails."""
    body = render.render_digest([c[1] for c in RENDERED_CASES], since="2026-08-22T08:00:00+00:00")
    assert NR.audit(body) == [], f"digest leaked {NR.audit(body)}"
    assert NR.inference_audit(body) == []
    assert NR.DIGEST_MASTHEAD in body
    assert body.count("⚠️") == 0, "the bulletin states no per-section reservation"


def test_subscriber_facing_family_labels_are_clean():
    """/filters and /status print these; they reach a subscriber like any brief does."""
    for key, label in commands.FAMILY_AR.items():
        assert NR.audit(label) == [], f"family label {key} carries {NR.audit(label)}"


def test_old_filter_words_still_work():
    """⛔ AN ALIAS IS INPUT, NOT OUTPUT. NEWS-3 governs what the bot PRINTS; deleting «شذوذ»
    would break the filter for anyone who learned the word from a message this bot sent."""
    assert commands._FAMILY_ALIASES["شذوذ"] == "implausible"
    assert commands._FAMILY_ALIASES["اضطراب"] == "implausible"


def test_no_dispatch_ever_names_a_platform_or_a_file():
    """⛔ THE STANDING GUARD, over EVERY family rather than the three fixtures above. The
    owner's brief names these by example — «OpenSky»، «MarineTraffic»، «latest.json» — and
    `alerts.py` writes them into `Alert.source` for real («opensky (latest.json)»,
    «celestrak+ucs»). The field still exists and the API still serves it; what this asserts
    is that no path in the bot puts it on the wire.

    ⚠ It renders each family through BOTH paths — the dispatch and the fallback — because
    the fallback is where a restored sourcing block would be easiest to miss."""
    banned = ("OpenSky", "opensky", "MarineTraffic", "marinetraffic", "latest.json",
              "celestrak", "aisstream", "AISStream", "vessel_states")
    for sig in render.SIGNAL_AR:
        a = _alert(signal_type=sig, source="opensky (latest.json)",
                   summary="squawk 7700 — general emergency",
                   headline=f"X {AR.PATTERN_AR['zone_dwell']}", caveat="تحفّظ", lede="ليد")
        for msg in (render.render_alert(a), render._render_brief_fallback(a)):
            for b in banned:
                assert b not in msg, f"{sig} put «{b}» on the wire"


def test_no_message_nests_bold():
    """`<b><b>…</b></b>` renders as a single weight and is a marker that two layers each
    thought they owned the emphasis. Caught once on a lede naming an AOI country."""
    for _name, a in RENDERED_CASES:
        for msg in (render.render_alert(a), render._render_brief_fallback(a)):
            assert "<b><b>" not in msg and "</b></b>" not in msg
