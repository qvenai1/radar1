"""Arabic summary TEMPLATES for the maritime family (BE-SUMMARY-1).

WHY A SEPARATE RESOURCE. Every Arabic sentence the API emits lives here, in one readable
file, so the wording can be reviewed AS LANGUAGE by someone who is not reading control
flow. Templates scattered through `alerts.py` cannot be audited that way, and this is
generated prose about vessels — the one place in this project where a careless verb
asserts something the data never said.

⛔ THE FIVE RULES THIS FILE EXISTS TO ENFORCE
 1. FIXED TEMPLATES FILLED FROM FACTS — never free text. `_GAP_HEAD` is a constant with
    slots; nothing composes a sentence at runtime. One source of truth, no linguistic
    drift, and a reviewer can read all possible outputs from this file alone.
 2. THREE FIELDS, NOT ONE.
      headline — the occurrence AS OBSERVED: what, where, when. No interpretation.
      why      — why it ranks where it does. Carries the existing `severity_basis` in
                 readable Arabic; it never invents a new verdict.
      caveat   — what this does NOT mean. Mandatory, not optional: it is "blindness, not
                 absence" translated for a non-specialist.
 3. DESCRIPTION, NEVER INFERENCE. «انقطع بثّها» (its broadcast stopped) — an observation.
    NOT «أخفت موقعها» (it hid its position) — an intent. Every verb below was checked
    against one question: does this describe a measurement, or attribute a motive?
 4. THE FLAG IS AN ADMINISTRATIVE ATTRIBUTE. Its template says «علم التسجيل» (flag of
    registration) explicitly — never «مسجّلة في» and nothing implying location or
    allegiance. Flag is whose it is, not where it is.
 5. NO SECOND DERIVATION. Slots are filled from fields that already exist on
    `CompositeHull`. Nothing here recomputes a count, a rank, or a country.

⚠ WHAT IS DELIBERATELY ABSENT. Two of the five maritime patterns have NO count field on
`CompositeHull`: `gnss_anomaly` and `zone_dwell` (measured — 164 and 24 events in real
timelines, but no `n_*` column). Their templates therefore state PRESENCE and carry no
number. Inventing a count for them would break rule 5, and rounding it to "several" would
be worse: a vague number reads as a withheld precise one.
"""

from __future__ import annotations

# --------------------------------------------------------------------------------------
# Arabic AOI country names.
#
# ⚠ Keyed by ISO on purpose, so this cannot drift from `country_iso.AOI_NAME_TO_ISO` — the
# single source for which seven countries exist and what their codes are. Adding a country
# there without adding it here yields its bare code in the text (see `waters_phrase`),
# which is visibly incomplete rather than silently wrong.
#
# ⚠ The FRONTEND also holds Arabic names (`T.country.names`) for its own chrome. Two maps
# in two layers is a real duplication risk; ISO is the join key and both must be updated
# together. Recorded rather than hidden.
# --------------------------------------------------------------------------------------
AOI_ISO_TO_NAME_AR: dict[str, str] = {
    "SY": "سوريا",
    "TR": "تركيا",
    "IQ": "العراق",
    "JO": "الأردن",
    "LB": "لبنان",
    "IL": "إسرائيل",
    "CY": "قبرص",
}

#: pattern label, for the composite headline's list
#:
#: ⚠ RANK-NAME: `gnss_anomaly` reads «شذوذ إشارة الملاحة» (was «شذوذ موضع GNSS»).
#:
#: ⛔ WHY NOT «GPS», which was the other candidate and is the word more people know. GPS is ONE
#: constellation; `gnss_anomaly.py` does not distinguish it from GLONASS, Galileo or BeiDou, and
#: the AIS record carries no constellation field at all. Naming GPS would therefore claim a
#: specificity the data does not hold — the same defect that kept «خطأ رصد» out of the family
#: label. «الملاحة» is constellation-agnostic and carries no abbreviation.
#:
#: ✅ It also matches what this pattern actually is. CAVEAT_GNSS below says «شذوذ الموضع يعني أن
#: الإشارة نفسها غير موثوقة» — the SIGNAL. That is what separates it from the implausible
#: family («شذوذ في الرصد»), which is about the recorded POSITION, and it resolves §11.3's
#: reservation: the two terms no longer differ by a bare noun, and no distinguishing token is
#: dropped.
PATTERN_AR: dict[str, str] = {
    "ais_gap": "انقطاع بثّ",
    "rendezvous": "تقارب في عرض البحر",
    "identity_change": "تغيّر بيانات الهويّة",
    "gnss_anomaly": "شذوذ إشارة الملاحة",
    "zone_dwell": "مكوث قرب منشأة",
}

RANK_AR: dict[str, str] = {"high": "مرتفعة", "medium": "متوسّطة", "low": "منخفضة"}


# --------------------------------------------------------------------------------------
# Arabic number agreement. Arabic inflects the DUAL separately from the plural, and 11+
# returns to the singular — so `f"{n} مرات"` is wrong for 1, 2 and 11.
# --------------------------------------------------------------------------------------
def times(n: int) -> str:
    """«مرة» / «مرتين» / «{n} مرات» / «{n} مرة» — correct for 1, 2, 3–10 and 11+."""
    if n == 1:
        return "مرة"
    if n == 2:
        return "مرتين"
    if 3 <= n <= 10:
        return f"{n} مرات"
    return f"{n} مرة"


# --------------------------------------------------------------------------------------
# BIDI ISOLATION for machine-shaped values inside Arabic prose (BE-FIX-1).
#
# ⛔ THE DEFECT THIS FIXES, measured not assumed. In «ممرّ متوقَّع يبدأ 2026-08-03 23:49Z» the
# two number groups render in the WRONG ORDER: measured in the real UI with a Range over the
# rendered text, the date sat at x=1224 and the time at x=1190 — the date to the RIGHT of the
# time, read as «23.49Z 03-08-2026».
#
# WHY. A date or time is a run of European Numbers. Two such runs separated by a space do not
# merge into one left-to-right run the way Latin WORDS do, so the RTL paragraph orders them
# right-to-left. `FIRST STRONG ISOLATE` … `POP DIRECTIONAL ISOLATE` makes the whole value one
# isolated run whose internal order is its own.
#
# ⛔ THIS IS ISOLATION, NOT FORMATTING. No Arabic date format is imposed: date formatting is a
# DISPLAY decision and belongs to whoever renders, not to whoever generates. The bytes of the
# value are untouched; two invisible marks are placed around it.
#
# ⚠ WHICH VALUES ACTUALLY NEED IT — measured in a browser (the authoritative UBA), each value
# placed inside a real Arabic sentence and its first/last glyph positions compared:
#     2026-08-03 23:49Z     REVERSED   → needs isolation
#     2026-08-01            REVERSED   → needs isolation  (⚠ found by measuring, not reasoning)
#     1,234 · 325 · 10,972 · 32.3h     fine bare
#     NAVSTAR 65 (USA 213) · Boeing 737 · AFR1234   fine bare
# Latin runs are strong-L and absorb adjacent digits, which is why object names and callsigns
# were never affected. Wrapping them anyway would scatter invisible characters for no gain.
#
# ⚠ NOT FOR MACHINE FIELDS. `Alert.ts`, `vintage`, `snapshot_time` are parsed by consumers;
# these marks belong only in prose a human reads. Applying them to a machine field would break
# `datetime.fromisoformat` at the other end.
# --------------------------------------------------------------------------------------
FSI = "\u2068"  #: FIRST STRONG ISOLATE
PDI = "\u2069"  #: POP DIRECTIONAL ISOLATE


def iso_run(value: str) -> str:
    """Wrap a machine-shaped value so an RTL paragraph cannot reorder its parts.

    Idempotent-safe for empty input, and deliberately NOT applied to Arabic text: isolating
    an Arabic run inside Arabic prose would suppress the very reordering that is correct
    there. Callers pass only date/time-shaped values (see the measurement table above).
    """
    if not value:
        return value
    return f"{FSI}{value}{PDI}"


def patterns_count(n: int) -> str:
    """«نمط واحد» / «نمطين» / «{n} أنماط» / «{n} نمطاً» — the same dual/plural rule as
    `times`, for the pattern count in the `why` line. A separate function rather than
    string-surgery on `times()`: the words differ, and substituting one inside the other
    is how a sentence quietly becomes ungrammatical."""
    if n == 1:
        return "نمط واحد"
    if n == 2:
        return "نمطين"
    if 3 <= n <= 10:
        return f"{n} أنماط"
    return f"{n} نمطاً"


def waters_phrase(isos: list[str]) -> str:
    """« قرب مياه سوريا» / « قرب مياه سوريا وتركيا» / «» when no water is attributed.

    ⚠ Singular vs plural is respected («مياه دولة» reads the same, but the conjunction
    must not appear for one country), and an unmapped code falls through as itself rather
    than being dropped — a visible gap beats a silent one.
    """
    names = [AOI_ISO_TO_NAME_AR.get(i, i) for i in isos if i]
    if not names:
        return ""
    if len(names) == 1:
        return f" قرب مياه {names[0]}"
    if len(names) == 2:
        return f" قرب مياه {names[0]} و{names[1]}"
    return f" قرب مياه {'، '.join(names[:-1])} و{names[-1]}"


# --------------------------------------------------------------------------------------
# HEADLINES — the occurrence as observed. Read each verb against rule 3.
# --------------------------------------------------------------------------------------
#: «انقطع بثّها» = its broadcast stopped. The AIS signal is the subject; the vessel is not
#: given an intention. NOT «أخفت موقعها».
GAP_HEAD = "انقطع بثّها {times}{waters}"

#: «تقاربت مع سفينة أخرى» = it came close to another vessel. Proximity is what the detector
#: measures. NOT «التقت» (a meeting, which implies purpose) and NOT any transfer.
RENDEZVOUS_HEAD = "تقاربت مع سفينة أخرى {times} في عرض البحر{waters}"

#: «تغيّرت بيانات هويّتها المُبثَّة» = its broadcast identity data changed. States the record
#: change. NOT «تنكّرت» / «بدّلت هويّتها لتعمية».
IDENTITY_HEAD = "تغيّرت بيانات هويّتها المُبثَّة {times}"

#: ⚠ PRESENCE ONLY — no count field exists (see the module note). «ورد في سجلّها» = its
#: record contains. The anomaly is a property of the SIGNAL, not an act by the vessel.
#:
#: ⚠ RANK-NAME: reads «شذوذ في إشارة الملاحة» (was «شذوذ في موضع GNSS»). Two changes, both
#: fixing a real inconsistency rather than only dropping the abbreviation: `PATTERN_AR` for the
#: same pattern already says «إشارة», so «موضع» here described one thing with two nouns — and
#: SIGNAL is the correct one, exactly as the comment above and `CAVEAT_GNSS` below both state.
#: The POSITION noun belongs to the implausible family («شذوذ في الرصد»), not to this pattern.
GNSS_HEAD = "ورد في سجلّها شذوذ في إشارة الملاحة{waters}"

#: ⚠ PRESENCE ONLY. «سُجّل مكوثها» = its dwell was recorded — passive, an observation log.
ZONE_DWELL_HEAD = "سُجّل مكوثها داخل نطاق منشأة{waters}"

#: the composite headline names the co-occurrence and lists the patterns, nothing more.
#: ⚠ `{waters}` sits BEFORE the colon, attached to «سجلّ هذا البدن» (this hull's record) —
#: NOT after the pattern list. Measured on a real 3-water hull, the earlier ordering
#: produced «… شذوذ موضع GNSS قرب مياه قبرص، إسرائيل وتركيا», which reads as though the
#: waters qualify the LAST pattern rather than the whole record. Generated prose has to be
#: unambiguous about what a phrase modifies; the reader cannot ask.
COMPOSITE_HEAD = "تزامن {n} أنماط في سجلّ هذا البدن{waters}: {patterns}"
COMPOSITE_HEAD_DUAL = "تزامن نمطين في سجلّ هذا البدن{waters}: {patterns}"

#: rule 4 — the flag, said as the administrative fact it is.
FLAG_LINE = "علم التسجيل: {country}"

# --------------------------------------------------------------------------------------
# WHY — the rank's reason, carrying the existing severity_basis into readable Arabic.
# It restates; it does not judge.
# --------------------------------------------------------------------------------------
# ⛔ RANK-S2 — ONE TEMPLATE PER RANK REASON, NOT ONE SENTENCE FOR ALL OF THEM.
#
# The old pair was `WHY_COMPOSITE = "الرتبة {rank} لتزامن {patterns_n}"` and
# `WHY_SINGLE = "الرتبة {rank} لنمط واحد مرصود"`. The «ل» in them is CAUSAL — "the rank is
# HIGH *because of* two co-occurring patterns" — and that was false on 172 of 200 live
# maritime alerts: two pattern types alone rank MEDIUM (composite.rank_with_reason), so what
# actually lifted those hulls was the grey/sanctions flag or a high underlying event. The
# sentence credited an insufficient condition, which is judging rather than restating and
# breaks rule 2 above.
#
# ✅ THE FIX IS TO NAME THE CONDITION THAT FIRED. Keyed by `composite.rank_with_reason`'s
# reason code, so every sentence's named cause IS the sufficient one for that branch. Where
# two conditions were each sufficient, both are named — «مع» is a CONJUNCTION, and the
# conjunction is the real rule, not either half of it.
#
# ⚠ NO SECOND DERIVATION (rule 5): nothing here re-tests `involves_grey` or the ordinal. The
# reason arrives on the hull as a field and this map only looks it up.
#
# ⚠ THE SINGLE-EVENT BRANCHES SHARE ONE SENTENCE on purpose. Their rank IS the underlying
# event's own rank, so naming that relationship is both honest and shorter than repeating the
# same rank word twice («الرتبة مرتفعة: حدث رتبته مرتفعة» says one thing in two breaths).
WHY_BY_REASON: dict[str, str] = {
    # the pattern count IS sufficient at three or more — crediting it is correct here
    "three_patterns": "الرتبة {rank}: تزامن {patterns_n}",
    # ⚠ and it is sufficient at two ONLY for MEDIUM, which is exactly what this says
    "two_patterns": "الرتبة {rank}: تزامن {patterns_n}",
    "two_patterns_grey": "الرتبة {rank}: تزامن {patterns_n} مع علم مدرَج على قوائم رقابية",
    "two_patterns_high_event": "الرتبة {rank}: تزامن {patterns_n} مع حدث رتبته مرتفعة",
    "two_patterns_grey_and_high_event":
        "الرتبة {rank}: تزامن {patterns_n} مع علم مدرَج على قوائم رقابية وحدث رتبته مرتفعة",
    "single_high_event": "الرتبة {rank}: هي رتبة الحدث الوحيد المرصود",
    "single_medium_event": "الرتبة {rank}: هي رتبة الحدث الوحيد المرصود",
    "single_low_event": "الرتبة {rank}: هي رتبة الحدث الوحيد المرصود",
}

#: ⛔ THE HONEST DEGRADE — an absent or unrecognised reason states the rank and NO cause.
#: A wrong cause is worse than a missing one, and nothing is lost by declining: the English
#: `severity_basis` still travels on every alert and stays authoritative.
WHY_RANK_ONLY = "الرتبة {rank}"

#: ⛔ Appended ONLY when the grey flag did NOT determine the rank (a three-pattern hull, or a
#: lone event). Where it IS the determinant its own template already names it, and appending
#: this too would state the same flag twice in one sentence — see `composite.GREY_IS_THE_REASON`.
WHY_GREY = "؛ وعلمها مدرَج على قوائم رقابية"
#: IMO-2's spine caveat, in the why line where the rank's reliability belongs
WHY_SPINE = {
    "checksum_fail": "؛ ⚠ رقم IMO لا يعبر اختبار الرقم المرجعي — الهويّة الجامعة لهذا البدن أقلّ موثوقية",
    "malformed": "؛ ⚠ رقم IMO مشوّه شكلياً — الهويّة الجامعة لهذا البدن أقلّ موثوقية",
    "contested": "؛ ⚠ رقم IMO متنازع بين مصادر — الهويّة الجامعة لهذا البدن أقلّ موثوقية",
}

# --------------------------------------------------------------------------------------
# CAVEATS — what this does NOT mean. Mandatory. Each names the innocent explanations
# first, because the whole point is to stop a lead being read as a finding.
# --------------------------------------------------------------------------------------
CAVEAT_GAP = (
    "الانقطاع وحده لا يدلّ على إخفاء؛ قد يكون عطلاً في الجهاز أو تشويشاً على الإشارة "
    "أو خروجاً عن نطاق تغطية المستقبِلات. ما نرصده هو غياب الإشارة عنّا لا غياب السفينة."
)
CAVEAT_RENDEZVOUS = (
    "التقارب في عرض البحر شائع ومشروع — تبادل أطقم، إمداد، انتظار مرسى، أو مجرّد تجاور "
    "في ممرّ ملاحي. لا يدلّ وحده على نقل غير معلن."
)
CAVEAT_IDENTITY = (
    "تغيير الاسم أو العلم أو النداء إجراء إداري معتاد عند البيع أو إعادة التسجيل أو تغيير "
    "المشغّل. لا يدلّ وحده على تعمية."
)
CAVEAT_GNSS = (
    "شذوذ الموضع يعني أن الإشارة نفسها غير موثوقة في تلك اللحظة — والسفينة في الغالب "
    "متضرّرة من تشويش أو خداع لا فاعلة له. لا يُقرأ كسلوك من السفينة."
)
CAVEAT_ZONE_DWELL = (
    "المكوث قرب منشأة قد يكون انتظار مرسى أو صيداً أو عبوراً مشروعاً؛ ونطاقات المنشآت "
    "حدود إدارية لا مناطق محظورة بالضرورة."
)
CAVEAT_COMPOSITE = (
    "تزامن الأنماط دليل أقوى — وليس إثباتاً. لكلّ نمط بمفرده تفسيرات مشروعة، واجتماعها "
    "يرفع أولويّة المراجعة لا أكثر."
)
#: appended when the hull's waters span more than one country
CAVEAT_MULTI_WATERS = (
    " والمياه المذكورة هي مواضع الأحداث المرصودة، لا مواضع السفينة الآن."
)


# ======================================================================================
# BE-SUMMARY-2 — the IMPLAUSIBLE family.
#
# ⛔ WHY THIS FAMILY IS CALIBRATED SEPARATELY, AND NOT REUSED FROM THE MARITIME SET.
# A maritime composite reports BEHAVIOUR of a hull (it stopped broadcasting, it came
# close to another vessel). An implausible position is not behaviour at all: it is a
# near-certain DATA DEFECT — a fix that is physically impossible. The subject of every
# sentence below is therefore the OBSERVATION, never the vessel.
#
#   ⛔ NOT «سلكت السفينة مساراً مستحيلاً» — that makes the hull the actor of something
#      it did not do; the receiver, the decoder or the GNSS signal did.
#   ✅ «ورد موضع…» / «سُجّل موضع…» — a position was received / recorded. Passive by
#      construction, because what we hold is a record, not a movement.
#
# ⛔ AND NOTHING HERE MAY IMPLY DELIBERATE DECEPTION. The existing G rules separate the
# VICTIM from the ACTOR, and a single alert decides neither — a jammed vessel and a
# spoofing one produce the same implausible fix. The caveat therefore names the mundane
# causes explicitly (decode error, jamming, spoofed position, sensor fault) and stops.
# Severity is already LOW for exactly this reason (`alerts.SEVERITY_BASIS`).
#
# ⚠ THE THREE FLAGS ARE DISTINCT OBSERVATIONS, so each gets its own sentence rather than
# one vague "implausible position" line. Measured over 7,006 implausible fixes:
# on_land 4,249 · impossible_speed 3,530 · teleport_return 1,190. Their meanings come
# from `gnss_anomaly.py` R1/R2 and are described, not re-derived:
#   on_land          — the fix lands >5 km inside the landmass
#   impossible_speed — reaching it from the previous fix needs >60 kn over ≥500 m
#   teleport_return  — incoming AND outgoing both >100 kn: a lone outlier the track
#                      immediately returns from
# --------------------------------------------------------------------------------------

#: «سُجّل موضع» = a position was recorded. The record is the subject.
IMPL_HEAD_ON_LAND = "سُجّل موضع داخل اليابسة {times}"

#: «ورد موضع يستحيل بلوغه» = a position arrived that is impossible to reach. The
#: impossibility is a property of the GEOMETRY between two fixes, not of the vessel.
IMPL_HEAD_SPEED = "ورد موضع يستحيل بلوغه من الرصدة السابقة {times}"

#: the out-and-back signature, described as the shape it is.
IMPL_HEAD_TELEPORT = "ورد موضع شاذّ منفرد يعود المسار بعده مباشرة إلى موضعه {times}"

#: the lead clause when several kinds appear together — it counts RECORDS, not events,
#: and says so. ⚠ «رصدات» (observations) never «مخالفات» / «حوادث».
IMPL_HEAD_LEAD = "رصدات موضع غير معقولة في سجلّ هذا البدن{waters}: {kinds}"

IMPL_KIND_AR: dict[str, str] = {
    "on_land": "موضع داخل اليابسة",
    "impossible_speed": "موضع يستحيل بلوغه",
    "teleport_return": "قفزة وعودة",
}

# --------------------------------------------------------------------------------------
# WHY — restates the existing LOW rank and its recorded basis. It does NOT re-judge.
# ⚠ It states the rank is low BECAUSE the vessel is usually the affected party — which is
# `SEVERITY_BASIS["implausible_position"]` in Arabic, not a new opinion.
# --------------------------------------------------------------------------------------
IMPL_WHY = (
    "الرتبة منخفضة: هذه رصدات يُرجَّح أنها عيب في البيانات لا سلوك من السفينة، "
    "والسفينة في الغالب طرف متضرّر لا فاعل."
)
#: appended when the hull has more than one kind of implausible fix — still not a verdict
IMPL_WHY_MULTI = " وتعدّد أنواع الخلل يرجّح مشكلة في سلسلة الرصد نفسها."

# --------------------------------------------------------------------------------------
# CAVEAT — mandatory. Names the likely causes OUTRIGHT, in the order that matters:
# the mundane ones first, and the deliberate one only as one possibility among four,
# with the victim/actor ambiguity stated rather than implied.
#
# ⛔ RANK-NAME touched ONE WORD here and nothing else: «إشارة GNSS» → «إشارة الملاحة». This is
# the honesty register §3.4 of docs/RANK_PRE.md exists to protect — it is the text that keeps
# the victim/actor question open by naming FOUR causes and refusing to choose. All four are
# still named, in the same order, with the same refusal. Terminology changed; the commitment
# did not. ⚠ Any future edit here is a change to a documented commitment, not to a label.
# --------------------------------------------------------------------------------------
IMPL_CAVEAT = (
    "الأسباب المرجّحة لهذه الرصدات تقنية: خطأ في فكّ ترميز رسالة AIS، أو تشويش على إشارة "
    "الملاحة، أو موضع منتحَل، أو خلل في المستشعر. ⛔ لا يدلّ هذا على سلوك متعمّد من السفينة: "
    "التشويش والانتحال يقعان على السفن كما يقعان منها، والرصدة وحدها لا تفصل بين الطرفين. "
    "ما نصفه هنا هو جودة الموضع المسجَّل، لا موقع السفينة الحقيقي ولا نيّتها."
)
#: appended for on-land fixes — the specific, well-understood displacement pattern
IMPL_CAVEAT_ON_LAND = (
    " والموضع على اليابسة نمط معروف للإزاحة المتّسقة: يُنقَل المسار كاملاً عن موضعه "
    "الحقيقي مع بقاء شكله وسرعاته واقعية، فلا يدلّ على أن السفينة كانت هناك."
)


# ======================================================================================
# BE-SUMMARY-3 — the EMERGENCY (squawk) family.
#
# ⛔ THE CALIBRATION HERE IS THE INVERSE OF THE OTHER TWO, which is why it is its own item.
# A maritime pattern and an implausible fix are INFERRED from behaviour or from data quality.
# A squawk is neither: 7500/7600/7700 is a code the AIRCRAFT ITSELF SET AND BROADCAST. The
# occurrence is reported, not deduced.
#
#   ⛔ SO THE «X alone does not indicate Y» PATTERN IS WRONG HERE. It protected the other two
#      families; here it would WEAKEN a true statement. «أعلنت الطائرة» (the aircraft
#      declared) is correct in a way «أخفت السفينة» (the vessel hid) never was — the
#      difference is that the aircraft is the REPORTER, not the subject of an inference.
#
#   ✅ THE RESERVATION MOVES, IT DOES NOT DISAPPEAR. It shifts from «هل حدث؟» (did it
#      happen — settled: the code was set and transmitted) to «ما مدى ما حدث؟» (how much
#      does it mean). Two reasons, both mandatory in the caveat:
#        1. the code names a CATEGORY, not a severity — a 7700 covers a minor technical
#           fault and a critical one alike, and the code does not distinguish them;
#        2. mis-setting is COMMON — pilot oversight, a keying error, a transient code while
#           changing frequencies.
#
# ⛔ AND NO CAUSE IS EVER IMPLIED. The aircraft declared; we do not know why. «أعلنت
# طارئاً» is what happened. «تعرّضت لمشكلة فنّية» would be invention.
#
# ⚠ 7500 IS THE MOST SERIOUS CODE IN THE SYSTEM and gets the least prose, deliberately: the
# fact of the code, ICAO's own definition, and the bounds of what it does not mean. No
# narrative, no likelihood, no dramatising verb. Its headline is PASSIVE («بُثّ رمز…» — the
# code was transmitted) rather than «أعلنت اختطافاً», because what is observed is a
# transmitted code, and the active voice would put a claim in the crew's mouth.
#
# ⚠ DURATION IS NOT IN THE CONTRACT. `build_emergency_alerts` reads squawk / icao24 /
# callsign / position, and `ts` is the SNAPSHOT's vintage — not when the code was set, and
# not how long it has been held. So the caveat STATES that absence rather than hinting at
# it; nothing here says or implies "briefly" or "sustained".
# --------------------------------------------------------------------------------------

#: code -> what it MEANS, in readable Arabic. The number alone is not a statement.
#: ⚠ 7700/7600 are what the aircraft DECLARED (active — it is the reporter). 7500 is what
#: was TRANSMITTED (passive — see the note above).
SQUAWK_MEANING_AR: dict[str, str] = {
    "7700": "أعلنت طارئاً عامّاً (7700)",
    "7600": "أبلغت فقدان الاتصال اللاسلكي (7600)",
    "7500": "بُثّ رمز الاستيلاء غير المشروع (7500)",
}

#: the aircraft, named as it actually arrived. ⚠ A callsign is often blank on the live
#: feed, so the ICAO24 is the fallback and is LABELLED as an identifier rather than
#: printed bare — a bare hex string reads as noise to a non-specialist.
EMG_WHO_CALLSIGN = "الطائرة {callsign}"
EMG_WHO_ICAO = "الطائرة ذات المُعرّف {icao24}"

#: «في أجواء …» — airspace, the air analogue of `waters_phrase`. Same conjunction rules.
def airspace_phrase(isos: list[str]) -> str:
    """« في أجواء سوريا» / « في أجواء سوريا وتركيا» / «» when no country is attributed.

    ⚠ Empty is an HONEST absence: the live feed is served archive-first and its rows carry
    no geo columns (measured — `country` is 0/567 on /live), so an unattributed alert means
    "not classified here", never "nowhere".
    """
    names = [AOI_ISO_TO_NAME_AR.get(i, i) for i in isos if i]
    if not names:
        return ""
    if len(names) == 1:
        return f" في أجواء {names[0]}"
    if len(names) == 2:
        return f" في أجواء {names[0]} و{names[1]}"
    return f" في أجواء {'، '.join(names[:-1])} و{names[-1]}"


#: the whole headline: WHAT was declared, by WHOM, WHERE. Nothing else.
EMG_HEAD = "{meaning} — {who}{where}"

# --------------------------------------------------------------------------------------
# WHY — restates the existing basis («active emergency squawk — high by definition»).
# ⚠ «بحكم التعريف» is the point: the rank does not come from anyone's assessment of this
# flight, it comes from the code class. That is what stops the badge reading as a verdict.
# --------------------------------------------------------------------------------------
EMG_WHY = "الرتبة مرتفعة بحكم التعريف: رمز طوارئ نشط، لا تقديراً لحالة هذه الرحلة."

# --------------------------------------------------------------------------------------
# CAVEATS — ⛔ NOT about whether it happened. About how much it means.
# --------------------------------------------------------------------------------------
#: shared by all three codes.
EMG_CAVEAT_BASE = (
    "الرمز مُبلَّغ من الطائرة نفسها، فوقوعه ليس محلّ شكّ — لكنّه يحدّد فئة الحالة لا "
    "خطورتها: تحته يقع العطل الفنّي البسيط والحالة الحرجة سواءً، ولا يفرّق بينهما. "
    "والضبط الخاطئ وارد وشائع: سهو من الطاقم، أو خطأ في الإدخال، أو رمز عابر أثناء "
    "تغيير الترددات. ولا يحمل هذا التنبيه مدّة الرمز ولا استمراريته، فلا يُستدلّ منه "
    "على طول بقائه."
)
#: 7500 only — ICAO's definition, stated and bounded. No narrative is added.
EMG_CAVEAT_7500 = (
    " و7500 هو رمز الاستيلاء غير المشروع كما تعرّفه ICAO، مُبلَّغاً من المُرمِّز على متن "
    "الطائرة. هذا كلّ ما تحمله الواقعة: لا يصف ما يجري على متنها، ولا يؤكّد وقوع استيلاء، "
    "ولا يُقرأ ترجيحاً لأيّ رواية."
)
#: 7600 only — the one code whose own meaning bounds it usefully.
EMG_CAVEAT_7600 = (
    " وفقدان الاتصال اللاسلكي عطل في وصلة الاتصال، وقد يكون في جهاز الطائرة أو في المحطة "
    "الأرضية أو في التغطية — ولا يدلّ وحده على خلل في الطائرة أو في مسارها."
)


# ======================================================================================
# BE-SUMMARY-4 — the MILITARY AIRCRAFT family.
#
# ⛔ THE MOST DELICATE CALIBRATION OF THE FIVE, and the reason is a commitment this project
# has written down three times: MILITARY PRESENCE IS INFORMATION, NEVER A THREAT.
# `SEVERITY_ORDER` puts this family at INFO *below* LOW deliberately, and DASH-2b proved
# with 19 gates that its badges do not move. Any Arabic verb here risks breaking that
# commitment in prose while the badge still says INFO — a contradiction the reader would
# resolve in favour of the sentence, not the badge.
#
# ⛔ VERBS REFUSED, AND WHY. Every one of these attributes INTENT, and each would read as a
# finding even though the underlying fact is a bare position:
#     «توغّلت» (penetrated)   — asserts trespass; the airspace may be its own or international
#     «اقتربت من» (approached) — asserts a target; we hold a coordinate, not an intention
#     «حلّقت قرب» (flew near)  — same, plus «قرب» is a PROXIMITY JUDGEMENT we never computed
#     «راقبت» / «تجسّست» / «استطلعت» (watched / spied / reconnoitred) — assign a MISSION;
#                               the source carries no mission field at all
#     «تمركزت» (positioned itself) — asserts deliberate placement over a snapshot
#     «رصدنا» (we detected)   — makes us the actor of an interception; we read a public feed
# ✅ SAFE FORM — A RECORDED OBSERVATION, with the RECORD as the subject and no motion verb:
#     «سُجّل وجود طائرة عسكرية …» (the presence of a military aircraft was recorded)
#   the same construction as «سُجّل مكوثها» (maritime) and «سُجّل موضع» (implausible).
#
# ⚠ NO PROXIMITY, NO DESCRIPTION, ONLY NUMBERS. Altitude is transferred as the bare figure
# the contract holds — «على ارتفاع 10,363 متراً» — never «منخفضاً» / «على علوّ شاهق», which
# are assessments. Metres, because that is the unit the record carries: `tracker_airplanes`
# normalises airplanes.live's feet to the project's metric contract at ingestion, so
# converting back here would be a second derivation of a number we already hold.
# --------------------------------------------------------------------------------------

#: how the aircraft is named — whatever actually arrived. Callsigns are frequently blank on
#: this overlay (measured 1 of 4 live), so the ICAO24 is a LABELLED fallback; a bare hex
#: string reads as noise.
MIL_WHO_CALLSIGN = "نداؤها {callsign}"
MIL_WHO_ICAO = "مُعرّفها {icao24}"

#: the type, only when the feed supplied it. ⚠ `acdesc` is the upstream's own description —
#: copied verbatim, never paraphrased into a role («ناقلة جند», «طائرة تجسّس») which would
#: be a mission claim dressed as a type.
MIL_TYPE = "، من طراز {type}"

#: altitude — the bare recorded figure, no adjective.
MIL_ALT = "، على ارتفاع {alt} متراً"

#: the whole headline. ⛔ No motion verb anywhere in it.
MIL_AIR_HEAD = "سُجّل وجود طائرة عسكرية{where} — {who}{type}{alt}"

#: ⚠ Stated, never silent: this overlay bypasses the geo classifier (measured — `country`
#: is None on every row), so an unattributed alert says so rather than implying "nowhere".
MIL_NO_GEO = " دون إسناد جغرافي في هذه اللقطة"

# --------------------------------------------------------------------------------------
# WHY — ⛔ it must NOT read "low because the danger is small". The rank is INFORMATIONAL
# because presence is not on the threat axis AT ALL; it is a different kind of statement,
# not a mild one.
#
# ⚠ RANK-NAME: opens with «لا تهديد», the label the badge now prints for this rank (was
# «معلوماتي» — an adjective from a different word-class than the three degrees above it, and
# the one term end users reported as unreadable). ⛔ The sentence was REWRITTEN, not
# token-swapped: «لا حكم تهديد» had to go, or the line would say «تهديد» twice in one breath.
# «الوجود مُسجَّل فقط» carries the same bound — a record, and nothing inferred from it.
# --------------------------------------------------------------------------------------
MIL_AIR_WHY = (
    "لا تهديد: الوجود مُسجَّل فقط. الرتبة ليست منخفضة لضعف الخطر — "
    "هذه الفئة خارج محور الخطر أصلاً."
)

# --------------------------------------------------------------------------------------
# CAVEAT — three mandatory elements.
# --------------------------------------------------------------------------------------
#: (1) the identification is the UPSTREAM FEED'S, named exactly. ⚠ Measured from
#: `tracker_airplanes.py`: the collector polls airplanes.live's **/v2/mil** endpoint, which
#: is itself the military filter, and then narrows to the bbox locally. This project runs NO
#: hull list, NO callsign pattern and NO identifier rule of its own — saying otherwise would
#: describe a mechanism that does not exist here.
MIL_CAVEAT_ID = (
    "التعريف «عسكرية» ليس حكماً لنا: يأتي كما هو من تغذية airplanes.live عبر مسارها "
    "المخصّص للطيران العسكري (/v2/mil)، ونحن نقصره على النطاق الجغرافي فحسب. وأي تصنيف "
    "من هذا النوع استدلالي وقد يخطئ — طائرة مدنية بعقد حكومي أو تسجيل قديم قد ترد فيه."
)
#: (2) presence is lawful by default — said outright, not left to inference.
MIL_CAVEAT_LAWFUL = (
    " والوجود في المجال الجوّي الدولي أو داخل حدود الدولة نفسها أمر مشروع بطبعه، "
    "والتنبيه لا يقول غير ذلك."
)
#: (3) the bounds of the record: a position and an identifier, and nothing else.
MIL_CAVEAT_NO_MISSION = (
    " ولا يُستنتج من هذا مهمّة ولا غرض ولا جهة آمرة: المصدر يحمل موضعاً ومُعرّفاً فقط."
)


# ======================================================================================
# BE-SUMMARY-5 — the SPACE family (military satellite AOI passes). The fifth and last.
#
# ⛔ THE ONLY FAMILY CARRYING TWO CONSTRAINTS AT ONCE, which is why it was left until last.
#
# CONSTRAINT 1 — THE TENSE IS FUTURE. The pass HAS NOT HAPPENED YET (measured: `ts` is the
# pass start and sits ~a day ahead). So «رُصد» and «سُجّل» — true of all four earlier
# families — are FALSE here. ⛔ No past verb about a computed pass, and no present one that
# would suggest a live sighting.
# ⚠ THIS ALSO INVERTS THE «THE OBSERVATION IS THE SUBJECT» METHOD that governed the other
# four. There is no observation at all: there is a COMPUTATION. The subject is the
# calculation, never a sensor and never the object's own intent.
#
# CONSTRAINT 2 — A THIRD KIND OF CAVEAT, absent from every other family. The pass is the
# output of a MODEL (SGP4) run on an ORBITAL ELEMENT (a TLE), and its accuracy DECAYS WITH
# THE ELEMENT'S AGE. Four mandatory elements, below.
#
# ⛔ AND IT INHERITS THE MILITARY COMMITMENT WHOLE (BE-SUMMARY-4): severity stays INFO,
# presence is information and never a threat, and that item's refused-verb list applies here
# verbatim — plus the orbital variants: «يراقب» · «يستطلع» · «يتجسّس» · «يمسح» · and
# «يحلّق فوق» used to mean purpose. The catalogue holds an orbit and a tag, NOT a mission.
# --------------------------------------------------------------------------------------

#: ⛔ Object names are NEVER translated — «NAVSTAR 56 (USA 180)» is a catalogue designator,
#: not prose. Rendering it in Arabic would invent an identity no catalogue carries.
SAT_HEAD = "يمرّ {object} فوق {countries} — ممرّ متوقَّع يبدأ {start}"
#: no AOI country resolved for the pass — stated, never silently dropped
SAT_HEAD_NO_COUNTRY = "يمرّ {object} ضمن نطاق الاهتمام — ممرّ متوقَّع يبدأ {start}"
#: the closest-approach figure, as the bare computed number. ⛔ No adjective: «قريب» /
#: «على مسافة خطرة» would be assessments of a number the record simply holds.
#: ⛔ REWRITTEN — THE OLD LINE WAS MISLEADING BY A FACTOR OF ~490, and measurement is the
#: only reason we know. It read «أقرب مسافة محسوبة {km} كم», which any reader takes as "the
#: object came within X km". It is not that. `passes_derive.closest_km` is a GROUND-TRACK
#: distance: how close the satellite's SUB-POINT (its projection on the Earth's surface)
#: came to the AOI. The object's own distance is dominated by its ALTITUDE.
#:
#: Measured on this DB, from `satellite_orbit`:
#:     NAVSTAR 80 (USA 309)  perigee 19,982 km / apogee 20,382 km  -> shown as «41 كم»
#:     SAR-LUPE 2            perigee    385 km / apogee    395 km  -> shown as «569 كم»
#: The SAME field meant "≈20,000 km away" for the first and "≈500 km away" for the second,
#: and the sentence presented them identically. For the GNSS half of the family (measured:
#: 53 % of the objects in the live alert window) it was always the misleading case.
#:
#: So the distance is now NAMED for what it measures, and the altitude travels beside it —
#: two numbers that cannot be collapsed into a false proximity. Altitude is stated from the
#: hull's own `satellite_orbit` row; when it is absent the clause degrades to the ground
#: track alone rather than inventing a height.
#: ⚠ FUTURE TENSE, AND IT WAS GOT WRONG ONCE ALREADY. The first draft of this line read
#: «ومرّ أثره الأرضي» — "and its ground track PASSED" — inside a sentence whose own subject
#: is «ممرّ متوقَّع» (a predicted pass that has not happened). The family's rule is stated at
#: the top of its section in alerts.py: FUTURE TENSE THROUGHOUT, «رُصد»/«سُجّل» are false
#: here. «يبلغ» keeps the whole clause in the same predicted tense as the sentence it joins.
SAT_CLOSEST = "، ويبلغ أقرب اقتراب لأثره الأرضي {km} كم من النطاق"
SAT_CLOSEST_ALT = ("، ويبلغ أقرب اقتراب لأثره الأرضي {km} كم من النطاق "
                   "(والجسم نفسه على ارتفاع {alt} كم)")

#: «مُصنَّف» — the tag is the CATALOGUE'S, not our judgement (same construction as the
#: aircraft caveat naming /v2/mil rather than claiming a rule of our own).
SAT_OBJECT_MIL = "جسم عسكري مُصنَّف ({name})"

# --------------------------------------------------------------------------------------
# WHY — identical in form to BE-SUMMARY-4, with «محسوب» in place of «مُسجَّل», because the
# thing being ranked is a computation rather than a record.
# --------------------------------------------------------------------------------------
# ======================================================================================
# BASELINE-1 — how this pass compares with THIS OBJECT'S OWN usual geometry.
#
# ⛔ EVERY ONE OF THESE IS A COMPARISON, NEVER A THREAT CLAIM. The severity of a satellite
# pass stays INFO whatever the verdict: «أقرب من المعتاد» says the ground track came nearer
# than this object's own 10th percentile, and nothing whatever about intent, mission or
# risk. The verbs refused by BE-SUMMARY-5 (يراقب/يستطلع/يتجسّس/يمسح) remain refused here.
#
# ⛔ AND «غير معروف» IS PRINTED, NOT SWALLOWED. A missing or thin baseline must reach the
# reader as itself — silently omitting the line would let absence read as normality, which
# is the exact invention this whole layer was built to avoid.
# ======================================================================================
#: ⛔ NO MARKUP IN THIS MODULE. The first draft wrote «و<b>أقرب من المعتاد</b>» and it
#: rendered as literal `&lt;b&gt;` in Telegram, because the digest HTML-escapes the headline
#: — correctly, since a vessel name may contain `<`. The deeper reason is that this register
#: is shared: the React frontend consumes the same strings, where a tag would either print
#: literally or force dangerouslySetInnerHTML. Emphasis is a PRESENTATION decision and
#: belongs to whoever renders; this file supplies the words only.
SAT_BASELINE_ROUTINE = "، وهذا ضمن النمط المعتاد لهذا الجسم (وسيط اقترابه {median} كم)"
SAT_BASELINE_CLOSER = ("، وهو أقرب من المعتاد لهذا الجسم — وسيط اقترابه {median} كم، "
                       "وهذا الممرّ ضمن أقرب عُشر ممرّاته")
SAT_BASELINE_NEW_COUNTRY = "، ويعبر نطاقاً لا يعبره عادةً"
#: ⚠ Stationed, not passing — a geosync object's sub-point is ~fixed, so it does not
#: transit the band at all. Saying "within its usual pattern" would imply a pattern of
#: crossings it does not have.
SAT_BASELINE_GEO = "، وهو جسم ثابت بالنسبة للأرض ومستقرّ فوق هذا النطاق دوماً"
SAT_BASELINE_UNKNOWN = "، ولا سند كافٍ بعد لمقارنته بنمطه المعتاد"

#: verdict/reason -> template. Keyed on the REASON so each cause gets its own sentence and
#: an unmapped reason falls back to the honest "no basis yet" rather than to silence.
SAT_BASELINE_BY_REASON: dict[str, str] = {
    "within_usual": SAT_BASELINE_ROUTINE,
    "closer_than_usual": SAT_BASELINE_CLOSER,
    "new_country": SAT_BASELINE_NEW_COUNTRY,
    "geostationary": SAT_BASELINE_GEO,
    "no_baseline": SAT_BASELINE_UNKNOWN,
    "thin_baseline": SAT_BASELINE_UNKNOWN,
    "no_distance": SAT_BASELINE_UNKNOWN,
}

#: ⛔ The provenance of the comparison itself, appended to the caveat whenever a baseline
#: verdict is stated. The reader must not take «المعتاد» for observed history: it is the
#: same SGP4 computation as the forward prediction, run over the preceding days. Omitting
#: this would make a derived distribution look like a record of what happened.
SAT_CAVEAT_BASELINE = (
    " و«المعتاد» هنا محسوب لا مرصود: قُدّر بتشغيل النموذج المداري نفسه على الأيام "
    "السابقة، فهو وصف لهندسة المدار لا سجلّ لما جرى."
)

SAT_WHY = (
    "لا تهديد: الممرّ محسوب فقط. الرتبة ليست منخفضة لضعف الخطر — "
    "هذه الفئة خارج محور الخطر أصلاً."
)

# --------------------------------------------------------------------------------------
# CAVEAT — four mandatory elements, then the bound on what cannot be inferred.
# --------------------------------------------------------------------------------------
#: (1) computed, not observed. The first sentence, because it reframes everything after it.
SAT_CAVEAT_COMPUTED = (
    "هذا الممرّ محسوب لا مرصود: لم يره مستشعر، وإنما نتج عن تشغيل نموذج SGP4 على "
    "عنصر مداري (TLE)."
)
#: (2) the element's age — filled from the epoch the alert already carries (`vintage`).
#: ⚠ The span is start_time − tle_epoch: BOTH are carried fields, so this is arithmetic on
#: the record, not a new measurement. See the no-epoch variant below.
SAT_CAVEAT_EPOCH = (
    " والعنصر المستعمل مؤرَّخ {epoch}، أي أن النموذج يمتدّ به {span} حتى بداية الممرّ."
)
#: ⛔ the honest absence, when the contract does not carry the epoch (BE-SUMMARY-3's lesson
#: about squawk duration: state the gap, never estimate it).
SAT_CAVEAT_NO_EPOCH = " ولا يحمل هذا التنبيه تاريخ العنصر المداري المستعمل."
#: (3) decay with age — ⛔ no invented error figure.
SAT_CAVEAT_DECAY = (
    " ودقّة هذا النوع من الحساب تتحلّل كلّما تقادم العنصر، والهامش يتّسع للأجسام المنخفضة "
    "سريعة الاضطراب. لا يحمل التنبيه رقم خطأ، فلا يُفترض له واحد."
)
#: (4) the catalogue has no country — the pass-over is COMPUTED, not a property of the
#: object. Mirrors the DASH-3 warning: "objects with a pass over the country in the window",
#: never "satellites tracked".
SAT_CAVEAT_NO_COUNTRY = (
    " والكتالوج المداري بلا دولة: «فوق {country}» وصف لهندسة الممرّ المحسوب، وليس صفة "
    "للجسم ولا إسناداً له إلى تلك الدولة."
)
SAT_CAVEAT_NO_COUNTRY_GENERIC = (
    " والكتالوج المداري بلا دولة: الممرّ فوق دولة نتيجة حساب هندسي، لا صفة للجسم."
)
#: the bound — what the source simply does not contain.
SAT_CAVEAT_NO_MISSION = (
    " ولا يُستنتج من هذا مهمّة ولا حمولة ولا قدرة استشعار ولا غرض: المصدر يحمل مداراً "
    "وتصنيفاً كتالوجياً فقط."
)


#: ⚠ THE CLASSIFICATION SOURCE, NAMED EXACTLY — the BE-SUMMARY-4 lesson applied. There, the
#: aircraft "military" tag turned out to be airplanes.live's own /v2/mil path rather than any
#: rule of ours. Here, measured the same way: the tag is the **UCS (Union of Concerned
#: Scientists) satellite database `users` field**, joined from `satellite_strategic`, and the
#: alert's own `severity_basis` already records that this snapshot is **frozen 2023**. The
#: orbit itself is a CelesTrak TLE. Two different sources with two different vintages, so
#: both are named rather than blurred into "our data".
SAT_CAVEAT_TAG = (
    " والتصنيف «عسكري» ليس حكماً لنا: هو حقل الاستخدام (users) في قاعدة UCS، ولقطتها "
    "مُجمَّدة عند 2023 — فقد لا تعكس وضعاً تغيّر بعدها. والمدار من CelesTrak، أي أن "
    "المصدرين مختلفان وتاريخاهما مختلفان."
)


def days_span(seconds: float) -> str:
    """A duration in Arabic, for the TLE-to-pass span: «يوماً واحداً» / «يومين» /
    «{n} أيام» / «{n} يوماً», with «أقلّ من يوم» below 24 h.

    Same dual/plural discipline as `times()` and `patterns_count()` — a separate function
    rather than string surgery, because the words differ and substituting one inside another
    is how a sentence quietly becomes ungrammatical.
    """
    d = int(seconds // 86400)
    if d < 1:
        return "أقلّ من يوم"
    if d == 1:
        return "يوماً واحداً"
    if d == 2:
        return "يومين"
    if 3 <= d <= 10:
        return f"{d} أيام"
    return f"{d} يوماً"

# ======================================================================================
# MT-DETECT-3 — the MarineTraffic single-source register.
#
# ⛔ SEPARATELY CALIBRATED, AND DELIBERATELY WEAKER THAN THE COMPOSITE'S. The maritime
# composite says "≥2 patterns coincide on one hull" — a stronger lead built on a STITCHED
# identity. None of that is available here: an MT event carries ONE pattern, from ONE
# source, on a `ship_id` that cannot be joined to an IMO or MMSI (measured 100 % NULL), so
# it can never be corroborated against the hull's own history.
#
# ⛔ SO THE SENTENCES MUST NOT BORROW THE COMPOSITE'S WEIGHT. No «تزامن», no «دليل أقوى» —
# those words mean cross-pattern corroboration and there is none. The claim is exactly:
# one pattern was observed once, by one source, on a vessel we cannot follow across time.
#
# ⚠ AND THE SOURCE'S OWN NOISE IS STATED, because it is measured and material: MT flags
# implausible positions at ~2x the AIS rate (1.87 % vs 0.93 %) and `suspect` at ~107x.
# ======================================================================================

MT_PATTERN_AR: dict[str, str] = {
    "rendezvous": "تقارب مع سفينة أخرى في عرض البحر",
    "zone_dwell": "مكوث قرب منشأة",
    "gnss_anomaly": "شذوذ في إشارة الملاحة",
}

MT_HEAD = "رُصد {pattern} — سفينة {who}{waters}"
MT_WHO_NAME = "{name}"
MT_WHO_ID = "بمُعرّف MarineTraffic ⁨{ship_id}⁩"

MT_WHY = (
    "الرتبة منخفضة: نمط واحد من مصدر واحد، بلا تعاضد. "
    "هذه ليست مؤشّراً مركّباً — لم يُقارن بسجلّ البدن لأنّه غير متاح."
)

#: ⛔ MANDATORY. Three limits, each of them measured, and none of them optional.
MT_CAVEAT = (
    "هذا رصد من كاشط MarineTraffic وحده. ثلاثة قيود: "
    "(١) نمط واحد لا تزامن — لا يرقى إلى المؤشّر المركّب ولا يحمل وزنه. "
    "(٢) هويّة السفينة غير قابلة للربط: المصدر لا يحمل MMSI ولا IMO، فلا يمكن وصل هذا "
    "الرصد بتاريخ البدن ولا معرفة إن كانت السفينة نفسها التي رُصدت في مصدر آخر. "
    "(٣) مواضع هذا المصدر أكثر اضطراباً: نسبة المواضع غير المعقولة فيه ضعف نظيرتها في AIS "
    "(١٫٨٧٪ مقابل ٠٫٩٣٪). "
    "وللنمط نفسه تفسيرات مشروعة، كما في كلّ رصد من هذا النوع."
)


# ======================================================================================
# NEWS-1 — the news-brief register: a LEDE for each family, plus the sourcing block.
#
# The operator asked for messages that read as a news item rather than a data line. The
# structure is journalistic — headline, body, classification, reservation, sourcing — but
# EVERY CLAIM STILL COMES FROM A FIELD THE ALERT ALREADY CARRIES. A lede is composed from
# the same facts the body states; nothing new is asserted to make the sentence flow.
#
# ⛔ TWO PHRASES FROM THE REQUESTED SAMPLE ARE DELIBERATELY NOT USED, and both refusals
# matter more than the format:
#
#  1. «مؤشرات غير اعتيادية» ("unusual indicators"). THE SYSTEM CANNOT SUPPORT "unusual" FOR
#     A VESSEL. "Unusual" is a comparison against a norm, and BASELINE-1 exists precisely
#     because that claim needs a measured baseline — which was built for SATELLITES only
#     (migration 041, back-propagated per object). No maritime baseline exists: the
#     composite says «تزامن N أنماط» — N pattern types COINCIDED — which is a count, not a
#     judgement about how normal that is for this hull. Writing «غير اعتيادية» would assert
#     exactly the thing the whole baseline layer was built to stop us asserting for free.
#
#  2. «لا تشكل دليلاً قاطعاً على وجود نشاط غير مشروع» ("not conclusive proof of ILLICIT
#     ACTIVITY"). ⛔ DENYING A CRIME NAMES IT. The existing caveats never mention an
#     offence — they say «دليل أقوى وليس إثباتاً» and «لكلّ نمط تفسيرات مشروعة». Introducing
#     «نشاط غير مشروع» in order to deny it plants the hypothesis in the reader's mind, which
#     is the precise opposite of what a reservation is for. The reservation stays about the
#     EVIDENCE, never about a suspected offence.
# ======================================================================================

#: Arabic month names for a news-style dateline. Levantine/MSA dual naming, as the request
#: showed («يوليو/تموز»).
NEWS_MONTHS_AR: tuple[str, ...] = (
    "يناير/كانون الثاني", "فبراير/شباط", "مارس/آذار", "أبريل/نيسان",
    "مايو/أيار", "يونيو/حزيران", "يوليو/تموز", "أغسطس/آب",
    "سبتمبر/أيلول", "أكتوبر/تشرين الأول", "نوفمبر/تشرين الثاني", "ديسمبر/كانون الأول",
)

#: ⚠ The lede states WHAT WAS OBSERVED and WHERE. It never characterises the observation
#: («لافتة», «مثيرة للقلق») — an adjective is a judgement, and the severity badge beside it
#: is the only ranking this layer is entitled to show.
NEWS_LEDE_MARITIME = "رصد تزامن {patterns} على السفينة {vessel}{waters}"
NEWS_LEDE_MARITIME_ONE = "رصد {pattern} على السفينة {vessel}{waters}"
NEWS_LEDE_EMERGENCY = "طائرة تُبلغ {meaning}{where}"
#: ⛔ THE SUBJECT MUST BE NAMED. The first drafts read «رصد طائرة عسكرية{where}» and
#: «مرور مداري متوقَّع لجسم ذي تصنيف عسكري{where}» — no callsign, no object name. In a
#: bulletin listing five satellite passes that produced five near-identical lines a reader
#: could not tell apart, which defeats the point of a per-item lede. Seen immediately in a
#: rendered digest; invisible when reading one alert at a time.
NEWS_LEDE_MIL_AIR = "رصد طائرة عسكرية {who}{where}"
NEWS_LEDE_MIL_SAT = "مرور مداري متوقَّع للجسم {object}{where}"
NEWS_LEDE_IMPL = "رصدات موضع غير معقولة على السفينة {vessel}{waters}"
NEWS_LEDE_MT = "رصد {pattern} على سفينة {vessel}{waters} (مصدر منفرد)"

#: The sourcing block's labels — the part that makes a brief attributable.
NEWS_LBL_CLASS = "التصنيف"
NEWS_LBL_SOURCE = "المصدر"
NEWS_LBL_VINTAGE = "آخر تحديث للبيانات"
NEWS_LBL_NATURE = "طبيعة البيانات"
NEWS_NATURE_INDIRECT = "غير مباشرة"
NEWS_NATURE_LIVE = "مباشرة"


def news_date(iso_ts: str | None) -> str:
    """«16 أغسطس/آب 2026 — 07:57Z» — a news dateline from an ISO timestamp.

    ⚠ BIDI-ISOLATED per run. BE-FIX-1 measured digit groups reordering inside Arabic
    paragraphs, and this line puts a day, a year and a time in one sentence — three separate
    European-number runs, which is the exact case that reverses."""
    if not iso_ts:
        return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(iso_ts))
    except ValueError:
        return iso_run(str(iso_ts)[:16])
    month = NEWS_MONTHS_AR[dt.month - 1]
    return (f"{iso_run(str(dt.day))} {month} {iso_run(str(dt.year))}"
            f" — {iso_run(dt.strftime('%H:%M') + 'Z')}")
