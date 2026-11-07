"""NOTIFY-1 — Alert -> Telegram message. PURE: alerts in, strings out.

⛔ THIS MODULE DOES NOT WRITE ARABIC ABOUT ALERTS. Every claim in a message — what was
observed, why it ranks where it does, and what it does NOT mean — is taken VERBATIM from
the `headline` / `why` / `caveat` the alert already carries. Those three fields exist
because collapsing them "is how a caveat gets dropped" (models.py), and they are separately
calibrated per family across five worked items (BE-SUMMARY-1..5): the maritime register
hedges the INFERENCE, the emergency register does not hedge the OCCURRENCE because the
aircraft reported it, the implausible register makes the OBSERVATION the subject and never
the hull, and the satellite register is future-tense because the pass has not happened.

Re-phrasing any of that here would fork a calibration this project spent five items
getting right, in a surface nobody reviews. So the only Arabic authored below is CHROME —
section labels, a severity word, a count — and none of it makes a claim about a signal.

⛔ THE CAVEAT IS NEVER DROPPED AND NEVER TRUNCATED. It is the field the model calls
mandatory, for the stated reason that "a lead read as a finding is the failure mode this
project is built to avoid". A message too long to fit is CONTINUED into a second message
(`chunk`), never trimmed — no path in this file discards content.

MEASURED against the live feed (2026-08-11 07:36 UTC), and both numbers shaped the design:

  * ONE ALERT ALWAYS FITS. Longest rendered single alert: 1,110 units of Telegram's 4,096
    (satellite, whose caveat carries the epoch clauses); maritime p50 is 757. So the instant
    path is one alert per message with ~3.7x headroom, and `chunk` is there for the digest.

  * CAVEATS COLLAPSE, WHICH IS WHAT MAKES A DIGEST HONEST AND SHORT AT THE SAME TIME.
    Distinct caveats per family: military aircraft 14 alerts -> 1, satellites 14 -> 8,
    maritime 200 -> 17. So a digest block can carry EVERY distinct caveat of the alerts it
    lists, each once, and still be short. Nothing is summarised away and nothing is
    attributed to an alert it did not come from.
"""

from __future__ import annotations

import html
from typing import Any

from .. import summaries_ar as AR
from ..alerts import SEVERITY_BASIS
from . import newsroom as NR

# --- Telegram limits -------------------------------------------------------------------

#: Telegram's per-message ceiling. Counted in UTF-16 code units, not Python characters —
#: an emoji is a surrogate pair and costs 2. `_tg_len` counts in Telegram's own unit rather
#: than approximating with a safety margin, because a margin is a guess that is either
#: wasteful or wrong and this is exact.
TELEGRAM_LIMIT = 4096


def _tg_len(s: str) -> int:
    """Length in Telegram's counting unit (UTF-16 code units)."""
    return len(s.encode("utf-16-le")) // 2


def esc(s: Any) -> str:
    """Escape for Telegram's HTML parse mode.

    ⚠ quote=False deliberately: Telegram requires only `<`, `>` and `&` to be escaped, and
    escaping quotes as well would put literal `&#x27;` into Arabic prose that contains
    apostrophes."""
    return html.escape(str(s or ""), quote=False)


# --- chrome (the ONLY authored Arabic in this file) --------------------------------------

#: Severity word. Built ON TOP of AR.RANK_AR rather than beside it, so the three shared
#: rungs cannot drift from the register the rest of the app uses.
#: `info` is absent from RANK_AR because it is not a composite rank; «معلوماتي» is the word
#: the existing military `why` already uses ("معلوماتي: الوجود مُسجَّل، لا حكم تهديد"), so
#: this reuses the established term rather than coining a second one.
SEVERITY_AR: dict[str, str] = {**AR.RANK_AR, "info": "معلوماتي"}

#: Per-severity marker. ⚠ `info` is BLUE, not a warning colour — the military families are
#: info because "presence is informational, NEVER a threat", and an alarm glyph next to
#: them would assert in an icon exactly what the text is careful not to assert.
SEVERITY_ICON: dict[str, str] = {
    "high": "🔴", "medium": "🟠", "low": "🟡", "info": "🔵",
}

DOMAIN_ICON: dict[str, str] = {"air": "✈️", "sea": "🚢", "space": "🛰️"}

#: Section labels, keyed by signal_type. Grouping is by SIGNAL, not family, because the
#: military family holds two things an operator reads differently — aircraft observed now,
#: and satellite passes PREDICTED for later.
#: ⚠ Each label names the OBSERVATION, never a threat: "military aviation", not "military
#: activity"; "implausible position readings", not "spoofed vessels".
SIGNAL_AR: dict[str, str] = {
    "emergency_squawk":     "طوارئ جوّية",
    "military_aircraft":    "طيران عسكري",
    "military_satellite":   "ممرّات أقمار عسكرية",
    "maritime_composite":   "أبدان بحرية متعدّدة الأنماط",
    "maritime_single":      "رصد بحري مفرد (MarineTraffic)",
    "implausible_position": "رصدات موضع غير معقولة",
}

#: The order sections appear in a digest — the same honest ordering the feed uses
#: (most-severe family first), so the digest cannot imply a different priority from the
#: dashboard's.
SECTION_ORDER = ("emergency_squawk", "maritime_composite", "maritime_single", "implausible_position",
                 "military_aircraft", "military_satellite")

_LBL_WHY = "لِمَ هذه الرتبة"
_LBL_SOURCE = "المصدر"
_LBL_VINTAGE = "حداثة البيانات"

# ======================================================================================
# NOTIFY-3 — VERBOSITY. Presentation density, and nothing else.
#
# ⛔ IT NEVER CHANGES WHAT IS ADMITTED. Whether something is an event is decided globally
# (notify_deliveries); whether it belongs to this person is decided by their filters. This
# only decides how densely what remains is written, and the INSTANT path is identical at
# every level — "quiet" is a request for less noise, never a request to hear about an
# emergency late.
#
# ⛔ AND NOTHING IS DROPPED, ONLY SUMMARISED. Every cap below prints what it stands for
# («و N أخرى»), so a collapsed line still reports its own true count. A level that silently
# shortened a list would make the digest's numbers disagree with its content — the same
# defect the per-family cap in the alerts endpoint is careful to avoid.
# ======================================================================================
QUIET, BALANCED, DETAILED = "quiet", "balanced", "detailed"

#: Items listed individually per section, per level. None = no cap.
#:
#: ⛔ THE CAP APPLIES TO THE SIGNIFICANT ITEMS, and `quiet` was 0 in the first draft — which
#: was backwards and the live render showed it: at quiet the ONE pass flagged closer-than-
#: usual was suppressed into «و1 أخرى» while the five routine ones still got their count
#: line. "Quiet" means only what matters, not everything except what matters. Routine
#: collapsing is what makes quiet quiet; the notable items are precisely what it exists to
#: still deliver.
DIGEST_CAPS: dict[str, int | None] = {QUIET: 3, BALANCED: 5, DETAILED: None}

#: ⛔ At `quiet`, a digest whose every section is routine is NOT SENT AT ALL. Someone who
#: chose quiet does not want an hourly "6 routine passes, nothing new" — that message is the
#: definition of the noise they opted out of. They still receive anything notable, unknown,
#: or high-severity. The runner reads this via `digest_is_worth_sending`.
QUIET_NEEDS_SIGNAL = True

#: ⚠ `detailed` opts OUT of the routine collapse, deliberately. The collapse exists because
#: a crossing matching its own measured norm is not news — but "detailed" is a subscriber
#: saying they want the rows anyway, and honouring that is the whole point of the setting.
COLLAPSE_ROUTINE: dict[str, bool] = {QUIET: True, BALANCED: True, DETAILED: False}

VERBOSITY_AR: dict[str, str] = {
    QUIET: "هادئ — المهمّ فقط",
    BALANCED: "متوازن",
    DETAILED: "تفصيلي — كلّ العناصر",
}

# ======================================================================================
# SHORT CAVEATS — the reservation kept, the repetition dropped.
#
# ⛔ MEASURED, and it is why this exists: the caveat was 66-77 % OF EVERY MESSAGE
# (maritime 454 of 679 chars, military air 424 of 627, satellite 740 of 956), while the
# actual observation was 109-167. And it is the SAME text every time — 200 maritime alerts
# carry only 17 distinct caveats between them.
#
# ⚠ THIS IS NOT A RELAXATION OF THE HONESTY RULE, IT IS WHAT MAKES IT WORK. A reservation
# restated at full length in every one of hundreds of messages is not read — it is scrolled
# past, and it trains the reader to scroll past the whole message. The full text is stated
# ONCE where a newcomer actually reads it (commands.CAVEAT_BLOCK, shown at /start before any
# alert can arrive, and repeated in /help), and each message keeps a short line that names
# the same limit. Nothing is dropped; the same claim is made where it lands.
#
# ⚠ EVERY LINE IS LIFTED FROM THE FAMILY'S OWN REGISTER, not newly worded — «دليل أقوى وليس
# إثباتاً», «لا حكم تهديد», «محسوب لا مرصود» are the existing caveats' own phrases. No family
# is given a weaker reservation than the one it already carried.
# ======================================================================================
SHORT_CAVEAT: dict[str, str] = {
    "emergency_squawk":     "رمز أبلغت به الطائرة — لا سبب ولا حالة مستنتجة",
    "military_aircraft":    "وجود مرصود — معلوماتي، لا حكم تهديد",
    "military_satellite":   "ممرّ محسوب لا مرصود — تنبّؤ مداري",
    "maritime_composite":   "دليل أقوى — وليس إثباتاً",
    "implausible_position": "خلل في الرصد — لا سلوك مُتّهَم",
    "maritime_single": "نمط واحد من مصدر واحد — ليس مؤشّراً مركّباً",
}

#: ⚠ Drift gate, the idiom alerts.py uses for its own registers. A family reaching the bot
#: without a short caveat still ships its FULL text (the fallback errs toward more
#: reservation, never none) — but it must be a DELIBERATE omission, not an unnoticed one.
assert set(SEVERITY_BASIS) <= set(SHORT_CAVEAT), (
    "an alert family has no short caveat: "
    f"{set(SEVERITY_BASIS) - set(SHORT_CAVEAT)}. Give it one, lifted from its own existing "
    "caveat — never let a family reach a public channel with a reservation nobody shortened "
    "and therefore nobody reads"
)

_LBL_CAVEAT = "⚠️"


def _label(alert: Any) -> str:
    return NR.edit(SIGNAL_AR.get(alert.signal_type, alert.signal_type))


#: ⛔ THE AOI'S OWN CLOCK, not the host's. This box runs EDT (measured: `date` returns
#: -0400), which is meaningless to every reader of this bot. Six of the seven AOI countries
#: — Syria, Iraq, Jordan, Lebanon, Israel, Cyprus — and Turkey all sit at UTC+3 in the
#: window this project serves, so one offset covers the whole area of interest.
#: Asia/Damascus is used as its name because Syria is the project's centre and has held a
#: fixed UTC+3 since abolishing DST in 2022 — no seasonal shift to track.
#:
#: ⚠ UTC IS NEVER DROPPED, only joined. Every underlying record is UTC, so a message that
#: showed local time alone would be unquotable against the data it came from: an operator
#: reading «6:34 م» cannot find that row. Both are shown, local first because that is the
#: one a human acts on.
LOCAL_TZ = "Asia/Damascus"
LOCAL_TZ_LABEL = "بتوقيت دمشق"


def _local_pair(iso_ts: str | None) -> str:
    """«18:34 بتوقيت دمشق · 15:34Z» — the local reading first, the quotable one beside it.

    Degrades to UTC alone if the timestamp cannot be parsed or the zone is unavailable: a
    missing local rendering must never cost the reader the time itself."""
    if not iso_ts:
        return ""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(str(iso_ts))
        if dt.tzinfo is None:
            return _stamp(iso_ts)
        loc = dt.astimezone(ZoneInfo(LOCAL_TZ))
        # ⚠ Both runs bidi-isolated: BE-FIX-1 measured digit groups reordering inside
        # Arabic paragraphs, and this line puts two of them in one sentence.
        return (f"{AR.iso_run(loc.strftime('%H:%M'))} {LOCAL_TZ_LABEL}"
                f" · {AR.iso_run(dt.astimezone(ZoneInfo('UTC')).strftime('%H:%M') + 'Z')}")
    except Exception:                      # noqa: BLE001 — never lose the time to a tz lookup
        return _stamp(iso_ts)


def _local_only(iso_ts: str | None) -> str:
    """«18:34 بتوقيت دمشق» — the dispatch's clock. Degrades to the UTC pair, never to nothing:
    a timestamp that cannot be localised is still the only time the reader has."""
    if not iso_ts:
        return ""
    pair = _local_pair(iso_ts)
    return pair.split(" · ")[0] if " · " in pair else pair


def _stamp(iso_ts: str | None) -> str:
    """A timestamp for prose: trimmed to the minute and BIDI-ISOLATED.

    ⚠ The isolation is not cosmetic. BE-FIX-1 MEASURED a date and time rendering in the
    wrong order inside Arabic paragraphs — two runs of European numerals do not merge into
    one left-to-right run the way Latin words do. `AR.iso_run` is the project's fix and is
    reused rather than reimplemented. The value's bytes are unchanged."""
    if not iso_ts:
        return ""
    return AR.iso_run(str(iso_ts)[:16].replace("T", " ") + "Z")


# --- the instant message (one alert) ------------------------------------------------------

def render_alert(alert: Any) -> str:
    """One alert -> one Telegram message, in the owner's NEWS-4 dispatch template.

    ⛔ NEWS-4 STRIPPED THIS SURFACE, and the removals are the item. Gone from the wire:
    the platform name (`المصدر: OpenSky`), the data-nature and vintage lines, the
    navigational code, the rank justification (`التصنيف: … — الرتبة مرتفعة تلقائياً`), and
    the per-message reservation. Gone too is the DUPLICATION the old layout had by
    construction — it printed the lede and then the headline, which restate the same fact —
    so the kicker now carries the CLASSIFICATION only and the body carries the event once.

    ⛔ THE RESERVATION NO LONGER RIDES ON THE MESSAGE. That is the owner's decision
    (2026-08-22) and it moves the whole honesty chain onto `commands.CAVEAT_BLOCK` at
    /start and /help. `newsroom` documents the cost; the tests assert that block is present
    and non-empty, because it is now the only place a reservation is stated at all.

    ⚠ THE FALLBACK IS NOT DEAD CODE. `newsroom.dispatch` returns None when the alert cannot
    fill the template without a guess — an unknown family, an unparsable squawk, a maritime
    alert naming no known pattern. Such an alert still reaches the operator, in the NEWS-3
    brief, rather than being silently dropped or rendered with a hole in it."""
    sev = getattr(alert, "severity", "") or ""
    icon = SEVERITY_ICON.get(sev, "⚪")
    sig = getattr(alert, "signal_type", "") or ""

    body = NR.dispatch(alert)
    if body is None:
        return _render_brief_fallback(alert)

    label = getattr(alert.entity_ref, "label", None) or getattr(alert.entity_ref, "id", "")
    kick = NR.kicker(sig)
    lines = [f"{icon} <b>{esc(kick)}</b>" if kick else f"{icon} <b>{esc(_label(alert))}</b>"]
    # ⚠ Emphasis on the entity and the AOI country names only — the spec's formatting rule.
    # Applied to the escaped sentence, never to the assembled line (see newsroom.emphasise).
    lines.append(NR.emphasise(esc(body), esc(label)))

    if NR.SHOW_LEVEL.get(sig, False):
        lvl = NR.level_line(sev)
        if lvl:
            lines += ["", lvl]

    ts = getattr(alert, "ts", None)
    if ts:
        # ⛔ LOCAL ONLY ON THIS PATH, AND IT IS A REAL DEPARTURE. `_local_pair` exists because
        # "a message that showed local time alone would be unquotable against the data it
        # came from" — written for an OPERATOR who needs to find the row. NEWS-4 re-aimed
        # this surface at «القارئ العادي», the owner's words, and the owner's template shows
        # Damascus alone. The UTC stamp is unchanged everywhere it serves that older purpose:
        # the digest, and the fallback brief below both still print the pair.
        lines.append(f"🕒 {_local_only(ts)}")

    return "\n".join(lines).strip()


def _render_brief_fallback(alert: Any) -> str:
    """The NEWS-3 brief, kept for alerts NEWS-4's template cannot state.

    ⛔ IT KEEPS ITS RESERVATION. This path exists because the dispatch could not be composed
    from structured facts — which is precisely the case where the reader has least context —
    so stripping the caveat here as well would remove it exactly where it is worth most."""
    sev = getattr(alert, "severity", "") or ""
    icon = SEVERITY_ICON.get(sev, "⚪")
    dom = DOMAIN_ICON.get(getattr(alert, "domain", ""), "")
    label = getattr(alert.entity_ref, "label", None) or getattr(alert.entity_ref, "id", "")
    lede = NR.edit(str(getattr(alert, "lede", "") or label))
    # ⛔ NOT emphasised. The whole line is already <b>, and running the emphasiser inside it
    # produced `<b>… <b>تركيا</b></b>` — nested bold on any lede naming an AOI country.
    # Emphasis marks a name INSIDE running text; a heading has no running text to mark.
    lines = [f"{icon} {dom} <b>{esc(lede)}</b>", ""]

    body = NR.edit(getattr(alert, "headline", "") or getattr(alert, "summary", ""))
    if body:
        lines += [NR.emphasise(esc(body), esc(label)), ""]

    tail = (SHORT_CAVEAT.get(getattr(alert, "signal_type", "")) or NR.edit(getattr(alert, "caveat", ""))
            or getattr(alert, "severity_basis", ""))
    if tail:
        lines += [f"⚠️ <i>{esc(NR.edit(tail))}</i>", ""]

    ts = getattr(alert, "ts", None)
    if ts:
        lines.append(f"<i>🕒 {_local_pair(ts)}</i>")
    return "\n".join(lines).replace("\n\n\n", "\n\n").strip()


# --- the digest (many alerts) -------------------------------------------------------------

def _country_breakdown(group: list) -> str:
    """«تركيا 8 · العراق 3 · بلا إسناد 4» — a COUNT of a field the alerts already carry.

    ⚠ An alert answers to EVERY country it touches, so these counts sum to more than the
    section total for multi-country alerts (a hull spanning two AOI waters, a pass crossing
    four borders). That is the same one-to-many the feed itself models, and flattening it to
    a single country would drop observed facts — so the counts are labelled as touches, not
    presented as a partition.

    ⚠ Alerts with NO country are counted as «بلا إسناد جغرافي» rather than omitted —
    measured at 64 % of military aircraft, so silently dropping them would make the busiest
    part of a section invisible."""
    counts: dict[str, int] = {}
    none_n = 0
    for a in group:
        cs = getattr(a, "countries", None) or []
        if not cs:
            none_n += 1
        for c in cs:
            counts[c] = counts.get(c, 0) + 1
    if not counts and not none_n:
        return ""
    parts = [f"{AR.AOI_ISO_TO_NAME_AR.get(c, c)} {n}"
             for c, n in sorted(counts.items(), key=lambda kv: -kv[1])[:4]]
    if none_n:
        parts.append(f"بلا إسناد جغرافي {none_n}")
    return "النطاق الجغرافي: " + " · ".join(parts)


def render_digest(alerts: list, *, since: str | None = None, spilled: int = 0,
                  verbosity: str = BALANCED) -> str:
    """Many alerts -> one digest body (chunk() splits it for the wire).

    Sections by signal_type in SECTION_ORDER. Each section lists every alert's `headline`
    verbatim, then its DISTINCT caveats, each once.

    ⛔ DEDUPLICATION IS OVER THE EXACT STRING, WHICH IS WHY IT IS SAFE. Two alerts share a
    caveat line only when their caveats are character-identical, so no caveat is ever
    attributed to an alert that did not carry it. Measured collapse: 200 maritime alerts ->
    17 caveat lines, 14 military aircraft -> 1. Every distinct caveat still ships."""
    if not alerts:
        return ""

    # NEWS-2: a bulletin masthead, matching the brief's register.
    head = [f"📰 <b>{NR.DIGEST_MASTHEAD}</b>"]
    if since:
        head.append(f"<i>منذ {_stamp(since)}</i>")
    head.append(f"<b>{len(alerts)}</b> تحديثاً")
    out = [" · ".join(head)]

    by_signal: dict[str, list] = {}
    for a in alerts:
        by_signal.setdefault(a.signal_type, []).append(a)

    # Any signal_type not named in SECTION_ORDER still prints, after the known ones — a new
    # family reaches the operator looking unstyled rather than not at all.
    ordered = [s for s in SECTION_ORDER if s in by_signal]
    ordered += [s for s in by_signal if s not in SECTION_ORDER]

    for sig in ordered:
        group = by_signal[sig]
        sev = getattr(group[0], "severity", "") or ""
        icon = SEVERITY_ICON.get(sev, "⚪")
        dom = DOMAIN_ICON.get(getattr(group[0], "domain", ""), "")
        out.append("")
        out.append(f"{icon} {dom} <b>{esc(NR.edit(SIGNAL_AR.get(sig, sig)))}</b> ({len(group)})")

        # ⛔ BASELINE-1 — ROUTINE ITEMS ARE COUNTED, NOT LISTED. A satellite whose crossing
        # matches its own measured norm is not news, and printing fourteen such lines every
        # hour is what made the digest read as a database dump. They are still REPORTED —
        # the count, and which objects — so nothing is hidden.
        #
        # ⚠ ONLY `routine` COLLAPSES. `unknown` is listed alongside `notable`, because "we
        # have no basis to compare this yet" is not "this is fine"; folding it into a
        # routine count would assert exactly the normality the baseline exists to avoid.
        collapse = COLLAPSE_ROUTINE.get(verbosity, True)
        routine = ([a for a in group if getattr(a, "baseline_verdict", None) == "routine"]
                   if collapse else [])
        listed = [a for a in group if a not in routine]

        # A geographic breakdown — a COUNT of a field each alert already carries, never an
        # inference. It is what turns "14 aircraft" into something an operator can act on,
        # and it is the aggregation that costs nothing in honesty.
        line = _country_breakdown(group)
        if line:
            out.append(f"  <i>{line}</i>")

        cap = DIGEST_CAPS.get(verbosity, 5)
        shown = listed if cap is None else listed[:cap]
        for a in shown:
            # ⛔ NEWS-2 — THE ITEM IS THE LEDE, NOT «label — headline». A bulletin item is a
            # news SENTENCE, and the lede already is one: it names the vessel, what was
            # observed and where, in the same register as the instant brief.
            #
            # ⚠ It is also SHORTER, which is why this makes the digest more journalistic
            # without re-inflating it. Measured on the live feed: lede 57 chars mean against
            # headline 167 — a 66 % reduction. The 12,404-character first digest is the
            # reason that mattered.
            # ⛔ NEWS-4 — THE BULLETIN ITEM IS THE DISPATCH SENTENCE. It was the lede, which
            # is composed upstream and still carries the register the brief replaced. Where a
            # dispatch cannot be composed the lede remains the fallback, exactly as on the
            # instant path — a bulletin never drops an item it cannot phrase.
            lbl = esc(getattr(a.entity_ref, "label", None) or getattr(a.entity_ref, "id", ""))
            lede = NR.dispatch(a) or NR.edit(getattr(a, "lede", "") or "")
            body = NR.edit(getattr(a, "headline", "") or getattr(a, "summary", ""))
            if lede:
                out.append(f"▪️ {NR.emphasise(esc(lede), lbl)}")
            elif body:
                out.append(f"▪️ <b>{lbl}</b> — {esc(body)}")
            else:
                out.append(f"▪️ <b>{lbl}</b>")
            # `detailed` is a subscriber asking for the full text, so the body follows the
            # lede rather than replacing it — the bulletin keeps its shape at every level.
            #
            # ⚠ UNLESS THE BODY ADDS NOTHING. For maritime_single the headline and the lede
            # state the same fact in the same breath («رصد مكوث ... على سفينة X قرب مياه Y»
            # twice), so printing both was pure duplication in the rendered digest. A detail
            # line exists to ADD information; if it is no longer than the lede it has none to
            # add, and repetition is what teaches a reader to skim past the text.
            if verbosity == DETAILED and lede and body and len(body) > len(lede):
                out.append(f"   <i>{esc(body)}</i>")

        # ⛔ The remainder is STATED, never silently dropped — a cap that hid its own effect
        # would make the section header's count disagree with the lines beneath it.
        hidden = len(listed) - len(shown)
        if hidden > 0:
            out.append(f"• <i>و{hidden} أخرى في هذه الفئة</i>")

        if routine:
            names = "، ".join(
                esc(getattr(a.entity_ref, "label", None) or getattr(a.entity_ref, "id", ""))
                for a in routine[:4])
            more = f" و{len(routine) - 4} غيرها" if len(routine) > 4 else ""
            out.append(f"• <b>{len(routine)}</b> ضمن النمط المعتاد لأجسامها "
                       f"<i>({names}{more})</i>")

        # ⛔ NEWS-4 REMOVED THE PER-SECTION RESERVATION, and this comment is the record of
        # what stood here. It was one short caveat per section — the mechanism that kept the
        # first live digest honest at 3,430 characters instead of 12,404. The owner's brief
        # removes «الجمل التحفظية … المكررة» from the wire, and a line repeated under every
        # section on every hourly bulletin is the clearest case of that.
        # ⛔ THE RESERVATION NOW LIVES IN EXACTLY ONE PLACE: `commands.CAVEAT_BLOCK`, shown at
        # /start and repeated at /help. `SHORT_CAVEAT` is still populated and still gated by
        # the assertion above, because the FALLBACK brief prints it — do not delete it.

    if spilled:
        # ⚠ The burst valve, made VISIBLE. policy.apply_burst_cap spills overflow here
        # rather than dropping it; saying so is what keeps the cap from silently shaping
        # what the operator believes they saw.
        out.append("")
        out.append(f"<i>↪ {spilled} تنبيهاً تجاوز حدّ الدفعة الفورية وأُدرج في هذه الخلاصة.</i>")

    return "\n".join(out)


def digest_is_worth_sending(alerts: list, verbosity: str = BALANCED) -> bool:
    """Should this digest go out at all?

    ⛔ ONLY `quiet` CAN DECLINE, and only when EVERY item is routine by its own measured
    baseline. Someone who chose quiet did so to stop receiving "6 routine passes, nothing
    new" every hour — that message is precisely the noise they opted out of, and sending it
    anyway makes the setting cosmetic.

    ⚠ `unknown` COUNTS AS SIGNAL and keeps the digest alive. "No basis to compare this yet"
    is not "this is fine", and treating it as routine here would let the quiet level quietly
    swallow exactly the passes the baseline cannot vouch for."""
    if not alerts:
        return False
    if verbosity != QUIET or not QUIET_NEEDS_SIGNAL:
        return True
    return any(getattr(a, "baseline_verdict", None) != "routine" for a in alerts)


# --- the wire ------------------------------------------------------------------------------

def chunk(body: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split a body into messages that fit, WITHOUT discarding anything.

    Splits on line boundaries so a sentence is never cut mid-word. A single line longer
    than the limit (no natural boundary to use) is hard-split rather than dropped — the
    only case where a break can land mid-sentence, and still preferable to losing the text.

    ⛔ THE INVARIANT, and it is asserted in the tests: the concatenation of the returned
    parts contains every line of the input. Nothing in this module truncates."""
    if _tg_len(body) <= limit:
        return [body] if body else []

    parts: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def flush() -> None:
        nonlocal cur, cur_len
        if cur:
            parts.append("\n".join(cur))
            cur, cur_len = [], 0

    for line in body.split("\n"):
        ln = _tg_len(line)
        if ln > limit:
            # No boundary available. Emit what we have, then hard-split this line.
            flush()
            buf = line
            while _tg_len(buf) > limit:
                # Walk back from the limit in characters; UTF-16 length is >= character
                # count, so slicing by characters can only UNDER-fill, never overflow.
                cut = limit
                while cut > 0 and _tg_len(buf[:cut]) > limit:
                    cut -= 1
                parts.append(buf[:cut])
                buf = buf[cut:]
            if buf:
                cur, cur_len = [buf], _tg_len(buf)
            continue
        # +1 for the newline that will rejoin it
        if cur and cur_len + 1 + ln > limit:
            flush()
        cur.append(line)
        cur_len += (1 if cur_len else 0) + ln

    flush()
    return parts
