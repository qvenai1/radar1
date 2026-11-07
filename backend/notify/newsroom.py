"""NEWS-3 — the newsroom register. A notify-ONLY editorial pass over the calibrated Arabic.

⛔ WHAT THIS MODULE REVERSES, STATED FIRST BECAUSE IT IS A REVERSAL. `render.py`'s header
and NOTIFY.md §4 both say the bot "authors no Arabic about any signal" and takes
`headline`/`why`/`caveat` VERBATIM. That rule held for a reason and the reason has not
gone away — the three fields are calibrated per family across five worked items
(BE-SUMMARY-1..5) and re-writing them in a surface nobody reviews would fork that
calibration. The owner asked for a newsroom register anyway (2026-08-22), and this module
is the narrowest way to give it without forking anything:

  ⛔ IT IS A TERM SUBSTITUTION, NOT A RE-COMPOSITION. Nothing here builds a sentence. The
     calibrated string arrives whole, a fixed list of jargon terms is swapped for their
     news-register equivalents, and the sentence leaves with its SUBJECT, VERB, HEDGE and
     SCOPE untouched. The claim is the same claim; only the vocabulary moved.

  ⛔ AND IT IS NOTIFY-ONLY. `summaries_ar.py` and `alerts.py` are NOT edited, so
     `/api/v1/alerts` returns exactly what it returned yesterday and the frontend renders
     exactly what it rendered yesterday. This matters beyond tidiness: «شذوذ إشارة الملاحة»
     is the outcome of RANK §11.3/§13.1 and shipped four commits ago (58b842b, 9f6e7de).
     Editing the shared file would have silently undone a decided item AND landed in a
     directory another window is actively working. The bot gets its register; nobody else
     is touched.

⛔ THE LINE THE OWNER DREW, AND IT IS THE IMPORTANT ONE. The requested spec offered
replacements that move a sentence from DESCRIPTION to INFERENCE — «تلاعب بالبيانات»
(manipulation: an act with an actor), «احتمال تشويش وتزييف إحداثيات» (a probable cause),
«التصاق … مناورة شحن/تفريغ» (a transfer the detector never measured). Rule 3 of
`summaries_ar.py` forbids exactly this, and the owner's decision was «حرّر الأسلوب، واحفظ
الوصف». So each of those three is rendered in the FLUENT register while staying an
observation, and the rejected half is recorded on the rule that replaced it. A term that
asserts a cause is not a style choice; it is a different claim.

⚠ THE HEDGES SURVIVE. «بحكم التعريف» reads as boilerplate and is replaced — but only by a
phrase carrying the SAME epistemic content («تلقائياً لأنّ الرمز نشط»), because what the
phrase does is deny that anyone judged this particular flight. Dropping it would turn a
rank into a verdict, which is the one thing the emergency family is built not to do.
"""

from __future__ import annotations

from .. import summaries_ar as AR

# ======================================================================================
# THE LEXICON — ordered, exact, single-pass.
#
# ⛔ SINGLE PASS, LONGEST MATCH AT EACH POSITION. `str.replace` in a loop would let one
# rule's OUTPUT be re-read by a later rule ("السفينة" produced by the hull rule being
# swallowed by a vessel rule, compounding into prose nobody wrote). `edit()` below scans
# left to right, takes the longest rule that matches at each index, and copies the
# replacement to the output WITHOUT re-scanning it. So the output of a rule is final, and
# the result depends only on this table — not on its iteration order at runtime.
#
# ⚠ Each entry is (jargon, news register, why it is claim-preserving). The third element is
# not decoration: it is the audit trail for a substitution that runs over prose five items
# were spent calibrating, and `test_notify_newsroom.py` asserts every entry carries one.
# ======================================================================================

#: (find, replace, rationale)
LEXICON: tuple[tuple[str, str, str], ...] = (
    # ---- «بدن» → the vessel's navigational record -------------------------------------
    # A hull is the trade's word for the identity spine; to a reader it is a boat part. The
    # referent is unchanged — the same record, of the same vessel.
    ("في سجلّ هذا البدن", "في السجلّ الملاحي للسفينة",
     "same referent: the record of the same vessel; only the trade noun changes"),
    ("بسجلّ البدن", "بالسجلّ الملاحي للسفينة",
     "same referent, preposition preserved"),
    ("سجلّ البدن", "السجلّ الملاحي للسفينة", "same referent"),
    ("لهذا البدن", "لهذه السفينة", "gender agreement follows the noun swap"),
    ("هذا البدن", "هذه السفينة", "gender agreement follows the noun swap"),
    ("أبدان بحرية متعدّدة الأنماط", "سفن بأنماط ملاحية متزامنة",
     "the family label: co-occurrence is what it names, and it still does"),
    ("البدن", "السفينة", "generic fallback, reached only after the phrases above"),

    # ---- «مكوث» → presence in place ----------------------------------------------------
    # ⛔ «تمركز» of the three offered («تمركز / توقف / رسو»). «رسو» asserts anchoring and
    # «توقف» asserts a stop; the detector measures TIME INSIDE A ZONE and neither. «تمركز»
    # is the one that stays an observation.
    ("سُجّل مكوثها", "سُجّل تمركزها", "passive observation preserved; dwell = time in zone"),
    ("المكوث قرب منشأة", "التمركز قرب منشأة", "definite form of the same observation"),
    ("مكوث قرب منشأة", "تمركز قرب منشأة", "same observation"),
    ("مكوثها", "تمركزها", "generic fallback"),
    ("المكوث", "التمركز", "generic fallback"),
    ("مكوث", "تمركز", "generic fallback"),

    # ---- «شذوذ» → disturbance ----------------------------------------------------------
    # ⛔ NOT «تلاعب بالبيانات». Manipulation names an actor and an intent; this family's own
    # caveat says the vessel is USUALLY THE AFFECTED PARTY. «اضطراب» is the requested
    # register («اضطراب في إشارة …») and remains a property of the signal.
    # ⚠ «الملاحة» is kept over the spec's «التتبع»: RANK recorded that this pattern is the
    # NAVIGATION signal (constellation-agnostic), while the tracking link is what an AIS gap
    # breaks. Swapping the noun would have made two different patterns share one name.
    ("شذوذ إشارة الملاحة", "اضطراب في إشارة الملاحة", "property of the signal, unchanged"),
    ("شذوذ في إشارة الملاحة", "اضطراب في إشارة الملاحة", "property of the signal, unchanged"),
    ("شذوذ الموضع", "اضطراب الموضع", "subject stays the reading, not the vessel"),
    ("شذوذ", "اضطراب", "generic fallback"),

    # ---- «تقارب» → close approach ------------------------------------------------------
    # ⛔ THE PARENTHETICAL «(مناورة شحن/تفريغ في عرض البحر)» IS REFUSED. `RENDEZVOUS_HEAD`'s
    # own note says the detector measures PROXIMITY and "NOT any transfer"; naming a
    # ship-to-ship transfer would assert the very act the caveat spends a sentence denying.
    # «التصاق» is dropped for the same reason — it states a contact distance never measured.
    ("تقاربت مع سفينة أخرى", "اقتربت مباشرةً من سفينة أخرى", "proximity, no transfer implied"),
    ("تقارب مع سفينة أخرى في عرض البحر", "اقتراب مباشر من سفينة أخرى في عرض البحر",
     "proximity, no transfer implied"),
    ("التقارب في عرض البحر", "الاقتراب المباشر في عرض البحر", "definite form, same event"),
    ("تقارب في عرض البحر", "اقتراب مباشر في عرض البحر", "same event"),
    ("التقارب", "الاقتراب المباشر", "generic fallback"),
    ("تقارب", "اقتراب مباشر", "generic fallback"),

    # ---- «رصدات موضع غير معقولة» → a sharp disturbance in position data -----------------
    # ⛔ NOT «احتمال تشويش وتزييف إحداثيات (Spoofing)». That names a CAUSE, and the whole
    # point of this family's templates is that the OBSERVATION is the subject and the hull
    # never is. «اضطراب حادّ في بيانات الموقع» is the requested fluency with the cause left
    # where the data leaves it: unstated.
    ("رصدات موضع غير معقولة في سجلّ هذا البدن",
     "اضطراب حادّ في بيانات الموقع ضمن السجلّ الملاحي للسفينة",
     "observation remains the subject; no cause asserted"),
    ("رصدات موضع غير معقولة على السفينة", "اضطراب حادّ في بيانات موقع السفينة",
     "observation remains the subject; no cause asserted"),
    ("رصدات موضع غير معقولة", "اضطراب في بيانات الموقع", "family label; no cause asserted"),
    ("موضع غير معقول", "موضع مضطرب", "generic fallback"),

    # ---- the emergency LEDE: a pre-existing double verb, fixed here -----------------------
    # ⛔ THIS DEFECT IS OLDER THAN NEWS-3 AND IS NOT ITS DOING. `NEWS_LEDE_EMERGENCY` is
    # «طائرة تُبلغ {meaning}» and every `SQUAWK_MEANING_AR` value already opens with its own
    # verb, so the composed lede reads «طائرة تُبلغ أعلنت …» / «طائرة تُبلغ أبلغت …» — two
    # finite verbs in a row. It is visible in the shipped bot today.
    # ⛔ AND IT IS FIXED HERE RATHER THAN AT THE TEMPLATE, deliberately: `NEWS_LEDE_EMERGENCY`
    # is on the Alert model, so it also leaves through /api/v1/alerts and lands on a surface
    # another window owns. The owner scoped this item to the bot; a "while I am here" edit to
    # the shared file is exactly the scope creep that scoping was meant to prevent. Recorded
    # so the real fix can be taken upstream later as its own decision.
    # ⚠ These rules must OUTRANK the bare squawk rules below; `_RULES` sorts longest-first,
    # and `test_edit_is_single_pass_and_idempotent` is what keeps that true.
    ("طائرة تُبلغ أعلنت طارئاً عامّاً (7700)", "طائرة تُعلن حالة طوارئ عامّة (رمز 7700)",
     "one verb instead of two; the aircraft is still the declarer"),
    ("طائرة تُبلغ أبلغت فقدان الاتصال اللاسلكي (7600)",
     "طائرة تُبلغ انقطاع الاتصال اللاسلكي مع أبراج المراقبة (رمز 7600)",
     "one verb instead of two; the aircraft is still the reporter"),
    ("طائرة تُبلغ بُثّ رمز الاستيلاء غير المشروع (7500)",
     "بثّ إنذار أمني — تهديد اختطاف (رمز 7500) من طائرة",
     "PASSIVE preserved — 7500 is transmitted, and the aircraft is not asserted as its willing reporter"),

    # ---- squawk codes → the code AND its meaning, as the spec asks ----------------------
    # ⚠ «(رمز 7500)» rather than «(7500)»: a bare number beside Arabic prose reads as a
    # quantity. The digits are unchanged.
    ("أعلنت طارئاً عامّاً (7700)", "أعلنت حالة طوارئ عامّة (رمز 7700)",
     "same declaration, same code"),
    ("أبلغت فقدان الاتصال اللاسلكي (7600)",
     "أبلغت انقطاع الاتصال اللاسلكي مع أبراج المراقبة (رمز 7600)",
     "adds the counterparty the code already means; still what the aircraft reported"),
    ("بُثّ رمز الاستيلاء غير المشروع (7500)", "بُثّ إنذار أمني — تهديد اختطاف (رمز 7500)",
     "PASSIVE «بُثّ» preserved — 7500 is transmitted, never 'declared' (see EMG note)"),
    ("رمز الاستيلاء غير المشروع كما تعرّفه ICAO", "رمز التهديد الأمني (الاختطاف) كما تعرّفه ICAO",
     "the ICAO citation survives as a citation; only the rendering of the term changes"),
    ("وقوع استيلاء", "وقوع اختطاف", "the denial keeps its object"),
    ("الاستيلاء غير المشروع", "التهديد الأمني (الاختطاف)", "generic fallback"),
    ("وفقدان الاتصال اللاسلكي", "وانقطاع الاتصال اللاسلكي", "same fault, plainer verb"),
    ("فقدان الاتصال اللاسلكي", "انقطاع الاتصال اللاسلكي", "generic fallback"),

    # ---- «بحكم التعريف» → the same denial, in plain words -------------------------------
    # ⛔ THE HEDGE IS NOT DROPPED, ONLY RE-WORDED. Its job is to deny that anyone assessed
    # THIS flight; «تلقائياً لأنّ رمز الطوارئ نشط» denies exactly that, and the clause that
    # says so outright («لا تقديراً لحالة هذه الرحلة») is untouched by any rule here.
    ("الرتبة مرتفعة بحكم التعريف: رمز طوارئ نشط",
     "الرتبة مرتفعة تلقائياً لأنّ رمز الطوارئ نشط",
     "same epistemic content: the rank comes from the code class, not from a judgement"),
    ("بحكم التعريف", "تلقائياً", "generic fallback"),
)

#: ⛔ THE GUARD, AND IT IS WHAT MAKES THE TABLE ABOVE CHECKABLE. A term that reaches a
#: public channel after being banned is the failure this module exists to prevent, and a
#: lexicon is only as good as its coverage. The tests render REAL alert fixtures and assert
#: none of these survives — so adding a term here without adding its rule fails the suite.
#: ⚠ `تقارب` is listed as a bare stem on purpose: it catches conjugations the table's
#: phrases miss, which is how the audit stays ahead of new templates upstream.
BANNED: tuple[str, ...] = (
    "مكوث", "شذوذ", "بدن", "يلامس", "غير معقول", "تقارب",
    "الاستيلاء", "فقدان الاتصال", "بحكم التعريف",
)

#: ⛔ AND THE OTHER DIRECTION — terms this module must never PRODUCE. The owner's decision
#: was «حرّر الأسلوب، واحفظ الوصف»; these are the words that would break it by asserting a
#: cause, an intent or an act nobody measured. The test asserts no rendered brief contains
#: one, which guards against a future "small" lexicon addition quietly crossing the line.
FORBIDDEN_INFERENCE: tuple[str, ...] = (
    "تلاعب", "تزييف", "spoofing", "Spoofing", "التصاق",
    "مناورة شحن", "نشاط غير مشروع", "تهريب", "عملية إخفاء",
)

# --- longest-first, resolved once at import ----------------------------------------------
# ⚠ Sorted HERE rather than relied upon in the table: the table is grouped by concern so it
# can be read as language, and a reader reordering it for clarity must not change behaviour.
_RULES: tuple[tuple[str, str], ...] = tuple(
    (find, repl) for find, repl, _why in sorted(LEXICON, key=lambda r: -len(r[0]))
)


def edit(text: str) -> str:
    """Apply the newsroom lexicon. Single pass, longest match, output never re-scanned.

    ⚠ Returns the input unchanged when it is empty or carries no jargon — this is called on
    every field of every message, and a no-op must cost nothing and change nothing."""
    if not text:
        return text
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        hit = None
        # longest match wins; `_RULES` is already sorted, so the first match IS the longest
        for find, repl in _RULES:
            if len(find) <= n - i and text.startswith(find, i):
                hit = (find, repl)
                break
        if hit:
            out.append(hit[1])
            i += len(hit[0])
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def audit(text: str) -> list[str]:
    """Banned terms still present. Empty list = clean. Used by the tests, not by the wire."""
    return [b for b in BANNED if b in (text or "")]


def inference_audit(text: str) -> list[str]:
    """Cause/intent words present. Empty list = the description survived the edit."""
    return [b for b in FORBIDDEN_INFERENCE if b in (text or "")]


# ======================================================================================
# THE KICKER — the classification line the requested templates open with.
#
# ⚠ THIS IS CHROME, and it is the only Arabic AUTHORED in this module. It names the FAMILY
# and the DOMAIN — both fields the alert already carries — and makes no claim about the
# signal. That is the same licence `render.py` has always had for section labels.
#
# ⛔ «عاجل» IS ON THE EMERGENCY FAMILY ALONE. It is a claim about URGENCY, and this project
# routes urgency through the severity axis, not through a word: military presence is INFO
# below LOW by an explicit commitment, and a «عاجل» over an informational pass would assert
# in the kicker exactly what the body is careful not to assert. Maritime opens «متابعات»
# (a follow-up), which is what a composite indicator is.
# ======================================================================================
#: ⚠ NEWS-4 SHORTENED THESE to the owner's two supplied templates — «عاجل | طوارئ جوية»
#: and «رصد بحري». The longer NEWS-3 kickers («متابعات بحرية | نشاط ملاحي غير اعتيادي»)
#: characterised the event in the header AND again in the body, which is exactly the
#: duplication the brief removes. The kicker now classifies; the body reports.
KICKER: dict[str, str] = {
    "emergency_squawk":     "عاجل | طوارئ جوية",
    "maritime_composite":   "رصد بحري",
    "maritime_single":      "رصد بحري",
    "implausible_position": "رصد بحري",
    "military_aircraft":    "رصد جوي",
    "military_satellite":   "رصد فضائي",
}

#: The periodic bulletin's masthead (spec template 3).
DIGEST_MASTHEAD = "موجز الرادار الملاحي والفضائي الدوري"


def kicker(signal_type: str) -> str:
    """The brief's classification line, or "" for a family that has none yet.

    ⚠ Empty rather than invented: an unknown family reaches the operator with no kicker,
    which is visibly incomplete — never with a guessed one, which is silently wrong."""
    return KICKER.get(signal_type, "")


# ======================================================================================
# EMPHASIS — bold on the names and places, per the spec's formatting rule.
#
# ⛔ APPLIED AFTER HTML ESCAPING, ON THE FIELD TEXT ALONE, and both halves matter. After
# escaping, because inserting `<b>` before it would have the escaper turn the tag into
# visible `&lt;b&gt;`. On the field text alone — never on an assembled line — because a
# needle must not be searched inside markup `render.py` added around it.
#
# ⚠ The needles are AOI country names (`AOI_ISO_TO_NAME_AR`, the same seven ISO-keyed names
# the prose is built from) and the entity's own label. Nothing else is emphasised: bolding
# a verb is editorial weight on a claim, which is not this module's to add.
# ======================================================================================
_COUNTRIES: tuple[str, ...] = tuple(
    sorted(AR.AOI_ISO_TO_NAME_AR.values(), key=len, reverse=True)
)


def emphasise(escaped: str, *extra: str) -> str:
    """Bold the AOI country names (and any extra needle) inside ALREADY-ESCAPED text.

    ⚠ Single pass for the same reason `edit` is: a second pass would find the `<b>` it just
    wrote and nest tags around a country named twice in one sentence."""
    if not escaped:
        return escaped
    needles = [n for n in (*extra, *_COUNTRIES) if n]
    needles.sort(key=len, reverse=True)
    out: list[str] = []
    i, n = 0, len(escaped)
    while i < n:
        hit = next((x for x in needles if escaped.startswith(x, i)), None)
        if hit:
            out.append(f"<b>{hit}</b>")
            i += len(hit)
        else:
            out.append(escaped[i])
            i += 1
    return "".join(out)


# ======================================================================================
# NEWS-4 — THE DISPATCH. The owner's standard template, composed from structured fields.
#
# ⛔ WHAT THIS CHANGES, AND IT IS BIGGER THAN NEWS-3. NEWS-3 substituted terms inside a
# calibrated sentence; this COMPOSES the sentence. The owner's brief (2026-08-22) is
# explicit — a dispatch carries the event, the entity, the place and the time, and nothing
# else: no platform name, no navigational code, no rank justification, no per-message
# reservation, and no sentence repeated between the kicker and the body.
#
# ⛔ THE ONE THING THE OWNER SHOULD KNOW IT COSTS, RECORDED HERE RATHER THAN ARGUED. The
# per-message reservation is gone from the wire. This project called that field mandatory
# ("a lead read as a finding is the failure mode this project is built to avoid"), so the
# honesty chain now rests ENTIRELY on `commands.CAVEAT_BLOCK`, stated at /start and repeated
# at /help. That block is asserted present and non-empty by the tests, because it is now
# load-bearing in a way it was not when a caveat also rode on every message.
#
# ⛔ AND HOW IT AVOIDS INVENTING ANYTHING. Every slot is filled from a field the alert
# already carries — `entity_ref.label`, `countries`, `ts`, `severity` — or from a CLOSED-SET
# lookup over a machine-readable field (`summary` starts «squawk 7700 — …» for the emergency
# family; `alerts.py` writes it). Where a fact is genuinely absent, the dispatch DEGRADES:
# an unmatched family or a missing event falls back to the NEWS-3 brief rather than emitting
# a sentence with a hole or a guess in it. `dispatch()` returning None IS that fallback.
#
# ⚠ THE TWO-VESSEL FORM IN THE OWNER'S TEMPLATE CANNOT BE RENDERED TODAY, and this is stated
# rather than faked. «رصد تمركز السفينتين X وY» needs the rendezvous COUNTERPART; the pairing
# exists in the database (`detail ->> 'partner_mmsi'`, surfaced by /api/v1/rendezvous) but it
# is NOT a field on `Alert`, so a message cannot reach it. The singular form is rendered
# instead. `_DUAL_VESSEL` below is where the dual form goes the day the counterpart lands on
# the model — one branch, no restructuring. Joining two vessels that merely appear in the
# same digest would assert a rendezvous nobody detected, which is the worst available answer.
# ======================================================================================

#: ⛔ MASCULINE, because the noun changed. The existing `RANK_AR` agrees with «الرتبة»
#: (feminine); this line reads «مستوى التنبيه», so «مرتفعة» would be a grammar error on
#: every high alert. A new set rather than a reused one, for that reason alone.
LEVEL_AR: dict[str, str] = {
    "high": "مرتفع", "medium": "متوسط", "low": "منخفض", "info": "معلوماتي",
}

#: ⛔ THE LEVEL LINE IS ON AIR AND NOT ON SEA, because that is what the two templates the
#: owner supplied say. It is recorded as a per-family switch rather than an `if domain ==`
#: so the decision is visible and reversible in one line — the severity ICON already carries
#: the same information on every message, which is why the sea template can omit the words.
SHOW_LEVEL: dict[str, bool] = {
    "emergency_squawk": True, "military_aircraft": True, "military_satellite": True,
    "maritime_composite": False, "maritime_single": False, "implausible_position": False,
}

#: ⛔ THE CODE IS DROPPED, THE MEANING IS NOT. The owner's brief removes «رمز 7700» from the
#: wire; what the aircraft actually reported has to survive that, so each code maps to the
#: verb it was already stated with plus a bare verbal description.
#: ⚠ 7500 keeps a PASSIVE verb. 7700/7600 are DECLARED by the aircraft; 7500 is TRANSMITTED,
#: and `summaries_ar.py` draws that line deliberately — the aircraft is not asserted to be
#: the willing reporter of a hijack code. Collapsing all three to «أعلنت» would erase it.
SQUAWK_PLAIN: dict[str, tuple[str, str]] = {
    "7700": ("أعلنت", "حالة طوارئ عامة"),
    "7600": ("أبلغت", "انقطاع الاتصال اللاسلكي مع أبراج المراقبة"),
    "7500": ("", "تهديد اختطاف"),          # rendered passively — see `_air_event`
}

#: Maritime pattern -> the verbal event, in the dispatch register. Keyed by the SAME closed
#: set `summaries_ar.PATTERN_AR` uses, so a new pattern upstream fails the coverage test
#: below rather than silently rendering as a generic «نشاط».
#: ⛔ A FULL CLAUSE PER PATTERN, NOT A NOUN. The first draft stored bare nouns and glued the
#: vessel on after them, which produced «رصد تمركز قرب منشآت وانقطاع بثّ التتبع للسفينة
#: ALYA» — the vessel stranded at the end, three constructs deep. Arabic puts the subject
#: next to its verb, and the owner's own template does exactly that («رصد تمركز السفينتين X
#: وY قرب منشآت»). Each pattern therefore owns its whole clause and says where {v} sits in it.
PATTERN_PLAIN: dict[str, str] = {
    "zone_dwell":      "رصد تمركز السفينة {v} قرب منشآت",
    "rendezvous":      "رصد اقتراب السفينة {v} المباشر من سفينة أخرى",
    "ais_gap":         "رصد انقطاع بثّ التتبع عن السفينة {v}",
    "identity_change": "رصد تغيّر بيانات الهويّة المُبثَّة للسفينة {v}",
    "gnss_anomaly":    "رصد اضطراب في إشارة الملاحة لدى السفينة {v}",
}

#: The dual-vessel branch, reserved and deliberately unreachable. See the block note above.
_DUAL_VESSEL = "رصد تمركز السفينتين {a} و{b} قرب منشآت{where}."


def level_line(severity: str) -> str:
    """«مستوى التنبيه: مرتفع» — or "" for a severity with no word (never a bare code)."""
    word = LEVEL_AR.get(severity or "", "")
    return f"مستوى التنبيه: {word}" if word else ""


def where_phrase(alert) -> str:
    """The place, in the register the DOMAIN calls for. "" when the alert names no country.

    ⛔ EMPTY IS AN HONEST ABSENCE and is rendered as absence — no «موقع غير محدّد», no
    «المنطقة». `models.Alert.countries` documents empty as "not attributable to an AOI
    country", and a dispatch that filled that gap with a word would be asserting a location
    the classifier declined to give.
    ⚠ The conjunction rules mirror `summaries_ar.waters_phrase` exactly (one country: no
    «و»; three or more: «، » between all but the last). Same convention, different preposition."""
    names = [AR.AOI_ISO_TO_NAME_AR.get(i, i) for i in (getattr(alert, "countries", None) or []) if i]
    if not names:
        return ""
    if len(names) == 1:
        joined = names[0]
    elif len(names) == 2:
        joined = f"{names[0]} و{names[1]}"
    else:
        joined = f"{'، '.join(names[:-1])} و{names[-1]}"
    domain = getattr(alert, "domain", "")
    if domain == "air":
        return f" في أجواء {joined}"
    if domain == "sea":
        return f" قبالة سواحل {joined}"
    return f" فوق {joined}"


def _squawk_of(alert) -> str:
    """The code, from the machine-readable `summary` «squawk 7700 — …» that alerts.py writes.

    ⛔ THIS IS A LOOKUP, NOT PROSE PARSING. It reads a fixed English prefix on a field built
    by `build_emergency_alerts`, never the Arabic. An unrecognised shape returns "" and the
    caller degrades to the NEWS-3 brief — it never guesses a code."""
    summary = str(getattr(alert, "summary", "") or "")
    if not summary.startswith("squawk "):
        return ""
    code = summary[7:11]
    return code if code in SQUAWK_PLAIN else ""


def _patterns_of(alert) -> list[str]:
    """Which maritime patterns this alert states, by CLOSED-SET membership on the headline.

    ⛔ WHY THE HEADLINE AND NOT `summary`. `summary` counts only three of the five patterns —
    `gnss_anomaly` and `zone_dwell` have no count column on `CompositeHull` (documented in
    summaries_ar's module note), so a summary-only reading would be BLIND to exactly the
    pattern the owner's own sea template is about («تمركز قرب منشآت» IS zone_dwell).
    ⛔ AND IT IS STILL NOT PROSE PARSING: `AR.PATTERN_AR` is a closed set of five fixed
    labels that `alerts.py` interpolates verbatim, so this is substring membership over
    strings this repository owns — not an attempt to understand a sentence.
    ⚠ Order follows PATTERN_PLAIN, so the rendered event is stable for a given hull rather
    than depending on how the labels happened to be joined."""
    head = str(getattr(alert, "headline", "") or "")
    if not head:
        return []
    return [p for p in PATTERN_PLAIN if p in AR.PATTERN_AR and AR.PATTERN_AR[p] in head]


def _air_event(alert) -> str:
    """«أعلنت الطائرة THY1846 حالة طوارئ عامة» — the sentence's subject and verb."""
    label = (getattr(alert.entity_ref, "label", None)
             or getattr(alert.entity_ref, "id", "") or "")
    sig = getattr(alert, "signal_type", "")
    if sig == "emergency_squawk":
        code = _squawk_of(alert)
        if not code:
            return ""
        verb, what = SQUAWK_PLAIN[code]
        if not verb:                       # 7500 — transmitted, never "declared"
            return f"بُثّ {what} من الطائرة {label}"
        return f"{verb} الطائرة {label} {what}"
    if sig == "military_aircraft":
        return f"رُصدت طائرة عسكرية {label}"
    if sig == "military_satellite":
        return f"مرور مداري متوقَّع للجسم {label}"
    return ""


def _sea_event(alert) -> str:
    """«رصد تمركز السفينة ALYA قرب منشآت» — the owner's sea template, singular form."""
    label = (getattr(alert.entity_ref, "label", None)
             or getattr(alert.entity_ref, "id", "") or "")
    sig = getattr(alert, "signal_type", "")
    if sig == "implausible_position":
        return f"رصد اضطراب حادّ في بيانات موقع السفينة {label}"
    pats = _patterns_of(alert)
    if not pats:
        return ""
    # ⚠ TWO OR MORE PATTERNS ARE COUNTED, NEVER LISTED. A dispatch is one sentence, and
    # chaining five clauses into it is the density the brief exists to remove. «تزامن نمطين»
    # is a COUNT of a fact the alert states — the same count `COMPOSITE_HEAD` reports — and
    # characterises nothing. The patterns themselves remain in the app and the API.
    if len(pats) == 1:
        return PATTERN_PLAIN[pats[0]].format(v=label)
    # ⚠ NO ADJECTIVE. «نمطين ملاحيَّين» and «ثلاثة أنماط ملاحية» take DIFFERENT agreement,
    # and one template cannot carry both — the first draft shipped «نمطين ملاحية», which is
    # simply wrong Arabic. `COMPOSITE_HEAD` states the same fact with no adjective either.
    return f"رصد تزامن {AR.patterns_count(len(pats))} على السفينة {label}"


def dispatch(alert) -> str | None:
    """The dispatch BODY — one sentence — or None when a fact is missing.

    ⛔ None IS THE HONEST ANSWER, not a failure. It means this alert cannot be stated in the
    owner's template without inventing something, and `render.render_alert` falls back to the
    NEWS-3 brief. A renderer that always produced a sentence would have to fill the gap."""
    domain = getattr(alert, "domain", "")
    event = _air_event(alert) if domain in ("air", "space") else _sea_event(alert)
    if not event:
        return None
    return f"{event}{where_phrase(alert)}."
