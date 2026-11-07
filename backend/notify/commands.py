"""NOTIFY-2 — the public command surface: /start /stop /delete /status /countries /families /help.

Parsing is PURE (`parse`, `parse_countries`, `parse_families`) and the replies are fixed
templates. Only `handle` touches the database, and it performs no Telegram I/O — it returns
the messages to send, so the whole conversational surface is testable without a network.

⛔ THIS IS THE PROJECT'S FIRST PUBLIC-FACING TEXT, AND THE AUDIENCE CHANGED. Every message
the bot sends already carries its own caveat, calibrated for an analyst who knows that a
composite is a lead. A stranger who typed /start does not know that, and will not infer it.
So /start states the three commitments in plain language BEFORE the first alert arrives —
military presence is not a threat judgement, a maritime composite is not proof, and AIS/ADS-B
are spoofable — and /help repeats them. This is the same honesty the alert `caveat` fields
carry, moved to the one place a newcomer is guaranteed to read.

⚠ FILTERS ARE DISCLOSED, NOT SILENT. Setting a country filter excludes alerts with no
country attribution — measured at 64 % of military aircraft. That is correct (they are
genuinely not attributable to the country asked for) but nobody would guess it, so the
confirmation says so at the moment the filter is set.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .. import summaries_ar as AR
from ..country_iso import iso_codes, normalise
from . import subscribers as subs
from . import render
from .render import esc

#: What Telegram shows in the command menu (registered via setMyCommands).
COMMAND_MENU: list[tuple[str, str]] = [
    ("start", "الاشتراك في التنبيهات"),
    ("status", "حالة اشتراكي وفلاتري"),
    ("countries", "تحديد الدول التي تهمّني"),
    ("families", "تحديد أنواع التنبيهات"),
    ("level", "مستوى التفصيل: هادئ / متوازن / تفصيلي"),
    ("help", "شرح البوت والتنبيهات"),
    ("stop", "إيقاف التنبيهات"),
    ("delete", "حذف بياناتي نهائياً"),
]

#: Family key -> Arabic label. Gated against subscribers.FAMILIES so a family cannot become
#: selectable without a label, or labelled without being selectable.
FAMILY_AR: dict[str, str] = {
    "emergency": "طوارئ جوّية",
    "maritime": "سفن بأنماط ملاحية متزامنة",
    "implausible": "اضطراب في بيانات الموقع",
    "military": "وجود عسكري (طيران وأقمار)",
}
assert set(FAMILY_AR) == set(subs.FAMILIES), (
    "every selectable family needs an Arabic label: "
    f"{set(FAMILY_AR) ^ set(subs.FAMILIES)} has none"
)

#: Arabic aliases a subscriber might type instead of the key.
_FAMILY_ALIASES: dict[str, str] = {
    "طوارئ": "emergency", "طوارىء": "emergency", "emergency": "emergency",
    "بحري": "maritime", "بحرية": "maritime", "maritime": "maritime",
    "مواضع": "implausible", "شذوذ": "implausible", "implausible": "implausible",
    # NEWS-3 vocabulary, added ALONGSIDE the old words — never in place of them.
    "اضطراب": "implausible", "موقع": "implausible",
    "سفن": "maritime", "ملاحي": "maritime",
    "عسكري": "military", "عسكرية": "military", "military": "military",
}

# --- the honesty block, shown at /start and /help ----------------------------------------
# Deliberately BEFORE any alert can arrive. It is the same three commitments the per-alert
# caveats carry, stated once for someone who has never seen this project.
CAVEAT_BLOCK = (
    "⚠️ <b>ما الذي تستلمه بالضبط</b>\n"
    "• التنبيهات قراءة مباشرة فوق مصادر مفتوحة (ADS-B وAIS وعناصر مدارية)، "
    "وليست رصداً بمستشعر ولا تقريراً استخبارياً.\n"
    "• <b>الوجود العسكري معلوماتي وليس حكم تهديد.</b> وجود طائرة عسكرية في مجال جوّي "
    "أمر مشروع بطبعه، والتنبيه لا يقول غير ذلك.\n"
    "• <b>المؤشّر البحري المركّب دليل أقوى — وليس إثباتاً.</b> لكلّ نمط بمفرده تفسيرات "
    "مشروعة، واجتماعها يرفع أولويّة المراجعة لا أكثر.\n"
    "• <b>إشارات AIS وADS-B قابلة للانتحال والتشويش.</b> ما نرصده هو الإشارة، لا الجسم.\n"
    "• كلّ تنبيه يصلك يحمل تحذيره الخاصّ به. اقرأه — فهو جزء من التنبيه لا زينة."
)

WELCOME = (
    "🟢 <b>Atheer RADAR — بوت التنبيهات</b>\n\n"
    "تمّ اشتراكك. ستصلك <b>التحديثات فقط</b>: ما هو جديد أو ما تغيّر فعلاً — "
    "لا تُعاد عليك الصورة القائمة في كلّ مرّة.\n\n"
    "• التنبيهات ذات الرتبة المرتفعة تصل فور رصدها.\n"
    "• ما دونها يصل في خلاصة دورية كل ساعة.\n\n"
    "🎛 <b>للتخصيص:</b> /countries لاختيار الدول · /families لاختيار الأنواع\n"
    "⏹ /stop لإيقاف التنبيهات · /delete لحذف بياناتك نهائياً\n\n"
    + CAVEAT_BLOCK
)

ALREADY = "أنت مشترك بالفعل ✅\n\nاستعمل /status لعرض فلاترك، أو /stop للإيقاف."

STOPPED = (
    "⏹ تمّ إيقاف التنبيهات.\n\n"
    "احتفظنا بتفضيلاتك فقط كي يعيدك /start إلى ما كنت عليه دون إعادة ضبط. "
    "إن أردت محو بياناتك كلّياً الآن فأرسل /delete."
)

DELETED = (
    "🗑 حُذفت بياناتك نهائياً — لم يبقَ لدينا أيّ سجلّ عنك.\n\n"
    "يمكنك العودة في أيّ وقت بـ /start."
)

NOT_SUBSCRIBED = "لست مشتركاً حالياً. أرسل /start للاشتراك."

HELP = (
    "🛰 <b>Atheer RADAR — بوت التنبيهات</b>\n\n"
    "يرسل هذا البوت تنبيهات جوّية وبحرية وفضائية في نطاق شرق المتوسّط "
    "(سوريا، تركيا، العراق، الأردن، لبنان، إسرائيل، قبرص).\n\n"
    "<b>الأوامر</b>\n"
    "/start — الاشتراك\n"
    "/status — حالة اشتراكك وفلاترك\n"
    "/countries — الدول التي تهمّك · مثال: <code>/countries SY TR</code> "
    "أو <code>/countries الكل</code>\n"
    "/families — أنواع التنبيهات · مثال: <code>/families طوارئ بحري</code> "
    "أو <code>/families الكل</code>\n"
    "/level — مستوى التفصيل · مثال: <code>/level هادئ</code>\n"
    "/help — عرض هذه الرسالة\n"
    "/stop — إيقاف التنبيهات\n"
    "/delete — حذف بياناتك نهائياً\n\n"
    + CAVEAT_BLOCK
)

#: ⚠ Said at the moment a country filter is set, never buried in /help. 64 % of military
#: aircraft carry no country attribution (measured), so a country filter removes most of
#: that family — correct, but not guessable.
COUNTRY_FILTER_NOTE = (
    "\n\n⚠️ <i>ملاحظة مهمّة: فلتر الدولة يستبعد التنبيهات غير المنسوبة جغرافياً — "
    "وهي حالياً أغلب الطيران العسكري (طائرات خارج حدود الدول السبع أو فوق المياه "
    "الدولية). إن أردت كلّ الطيران العسكري فأرسل <code>/countries الكل</code>.</i>"
)

_ALL_WORDS = {"الكل", "all", "*", "كل"}

#: NOTIFY-3 — what a subscriber may type for a verbosity level. Keyed to render's own
#: constants so the two cannot drift; the Arabic words come first because that is what an
#: Arabic-speaking subscriber will actually type.
_LEVEL_ALIASES: dict[str, str] = {
    "هادئ": render.QUIET, "هادىء": render.QUIET, "quiet": render.QUIET,
    "متوازن": render.BALANCED, "balanced": render.BALANCED,
    "تفصيلي": render.DETAILED, "مفصل": render.DETAILED, "مفصّل": render.DETAILED,
    "detailed": render.DETAILED,
}


def parse_level(args: list[str]) -> str | None:
    for a in args:
        key = _LEVEL_ALIASES.get(a.lower()) or _LEVEL_ALIASES.get(a)
        if key:
            return key
    return None


def _level_menu() -> str:
    return ("🎚 <b>مستوى التفصيل</b>\n"
            "• <code>هادئ</code> — المهمّ فقط. لا تصلك خلاصة إن كان كلّ ما فيها "
            "ضمن النمط المعتاد.\n"
            "• <code>متوازن</code> — عناصر مختارة لكلّ فئة مع العدّ الكامل. <i>(الافتراضي)</i>\n"
            "• <code>تفصيلي</code> — كلّ العناصر، بلا طيّ.\n\n"
            "أرسل مثلاً <code>/level هادئ</code>.\n\n"
            "⚠️ <i>المستوى يغيّر كثافة العرض فقط. التنبيهات المرتفعة تصل فوراً في "
            "المستويات الثلاثة — «هادئ» طلبُ تقليل ضجيج، لا تأخيرُ طارئ.</i>")

#: ⛔ ARABIC COUNTRY NAMES -> ISO, and the reason this map has to exist here. `country_iso`
#: is the single source for which seven countries exist, but its `AOI_NAME_TO_ISO` holds the
#: ENGLISH names — it is the filtering layer, not a display one. The Arabic names live in
#: `summaries_ar.AOI_ISO_TO_NAME_AR`, keyed by ISO.
#:
#: Found by smoke-testing the parser, not by reading it: `/countries تركيا` was rejected as
#: an unknown country. On an Arabic-facing public bot that is the DEFAULT way a subscriber
#: would name a country, so the parser would have failed most real input while passing every
#: test written in ISO codes.
#:
#: ⚠ INVERTED FROM the existing map rather than retyped, so it cannot drift from the seven
#: and a country added there becomes typeable here for free. The bare form is accepted
#: alongside the definite article because «العراق» and «عراق» are both natural to type.
#:
#: ⛔ NO ALIASES BEYOND THE PROJECT'S OWN LABELS. It is tempting to add colloquial or
#: alternative names for the AOI countries, but every such addition is this bot asserting
#: that two names denote the same place — a claim with political weight that nobody asked
#: this layer to make. The seven labels come from `AOI_ISO_TO_NAME_AR` unchanged, the menu
#: shows exactly those, and an unrecognised name gets the menu rather than a guess.
_AR_COUNTRY: dict[str, str] = {}
for _iso, _ar in AR.AOI_ISO_TO_NAME_AR.items():
    _AR_COUNTRY[_ar] = _iso
    if _ar.startswith("ال"):
        _AR_COUNTRY[_ar[2:]] = _iso            # «العراق» -> «عراق»


def parse(text: str) -> tuple[str, list[str]]:
    """'/countries@AtheerBot SY TR' -> ('countries', ['SY', 'TR']).

    ⚠ The @botname suffix is stripped: Telegram appends it to every command sent in a
    GROUP, so without this the bot would ignore its own commands the moment someone added
    it to one."""
    parts = (text or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        return "", []
    cmd = parts[0][1:].split("@", 1)[0].lower()
    return cmd, parts[1:]


def parse_countries(args: list[str]) -> tuple[list[str] | None, list[str]]:
    """(codes, unrecognised). None for codes means "all" — i.e. no filter.

    Unrecognised tokens are RETURNED, not ignored: a subscriber who names a country outside
    the seven must be told, or they will believe they are subscribed to it and read the
    resulting silence as coverage."""
    if any(a in _ALL_WORDS for a in args):
        return None, []
    codes: list[str] = []
    bad: list[str] = []
    for a in args:
        # ISO code / English name first (the project's filtering vocabulary), then the
        # Arabic display names — which is what an Arabic-speaking subscriber will type.
        iso = normalise(a) or normalise(a.upper()) or _AR_COUNTRY.get(a)
        if iso:
            if iso not in codes:
                codes.append(iso)
        else:
            bad.append(a)
    return (codes or None), bad


def parse_families(args: list[str]) -> tuple[list[str] | None, list[str]]:
    if any(a in _ALL_WORDS for a in args):
        return None, []
    fams: list[str] = []
    bad: list[str] = []
    for a in args:
        key = _FAMILY_ALIASES.get(a.lower()) or _FAMILY_ALIASES.get(a)
        if key:
            if key not in fams:
                fams.append(key)
        else:
            bad.append(a)
    return (fams or None), bad


def describe(sub: subs.Subscriber) -> str:
    """The subscriber's own settings, in their words."""
    names = ("، ".join(AR.AOI_ISO_TO_NAME_AR.get(c, c) for c in sub.countries)
             if sub.countries else "كل الدول السبع")
    fams = ("، ".join(FAMILY_AR.get(f, f) for f in sub.families)
            if sub.families else "كل الأنواع")
    state = {"active": "مُفعَّل ✅", "stopped": "متوقّف ⏹",
             "blocked": "محظور — البوت محجوب من طرفك"}.get(sub.status, sub.status)
    lvl = render.VERBOSITY_AR.get(sub.verbosity, sub.verbosity)
    return (f"<b>حالة الاشتراك:</b> {state}\n"
            f"🌍 <b>الدول:</b> {esc(names)}\n"
            f"🔔 <b>الأنواع:</b> {esc(fams)}\n"
            f"🎚 <b>مستوى التفصيل:</b> {esc(lvl)}")


def _country_menu() -> str:
    pairs = "  ·  ".join(f"<code>{c}</code> {AR.AOI_ISO_TO_NAME_AR.get(c, c)}"
                         for c in iso_codes())
    return ("🌍 <b>الدول المتاحة</b>\n" + pairs +
            "\n\nأرسل مثلاً <code>/countries SY LB</code> — أو "
            "<code>/countries الكل</code> لإلغاء الفلتر.")


def _family_menu() -> str:
    lines = "\n".join(f"• <code>{k}</code> — {v}" for k, v in FAMILY_AR.items())
    return ("🔔 <b>أنواع التنبيهات</b>\n" + lines +
            "\n\nأرسل مثلاً <code>/families emergency maritime</code> — أو "
            "<code>/families الكل</code> لإلغاء الفلتر.")


async def handle(pool: Any, chat_id: str, text: str, now: datetime,
                 summary_fn: Any = None) -> list[str]:
    """Run one command. Returns the messages to send, in order. No Telegram I/O.

    `summary_fn` is an optional async callable returning the "current picture" block shown
    after /start. It is INJECTED rather than imported so this module stays free of the feed
    and testable offline — and so a feed outage degrades /start to a plain welcome instead
    of failing the subscription itself."""
    cmd, args = parse(text)

    if cmd == "start":
        sub: subs.Subscriber | None
        sub, was_new = await subs.subscribe(pool, chat_id, now)
        if not was_new and sub.active:
            return [ALREADY]
        out = [WELCOME]
        if summary_fn is not None:
            try:
                block = await summary_fn(sub)
                if block:
                    out.append(block)
            except Exception:          # noqa: BLE001 — a feed failure must not fail /start
                pass
        return out

    if cmd == "stop":
        sub = await subs.get(pool, chat_id)
        if sub is None or not sub.active:
            return [NOT_SUBSCRIBED]
        await subs.set_status(pool, chat_id, subs.STOPPED, now)
        return [STOPPED]

    if cmd == "delete":
        return [DELETED if await subs.delete(pool, chat_id) else NOT_SUBSCRIBED]

    if cmd == "status":
        sub = await subs.get(pool, chat_id)
        return [describe(sub) if sub else NOT_SUBSCRIBED]

    if cmd in ("countries", "families"):
        sub = await subs.get(pool, chat_id)
        if sub is None:
            return [NOT_SUBSCRIBED]
        if not args:
            return [_country_menu() if cmd == "countries" else _family_menu(),
                    describe(sub)]
        if cmd == "countries":
            codes, bad = parse_countries(args)
            if bad:
                return [f"لم أتعرّف على: {esc('، '.join(bad))}\n\n" + _country_menu()]
            await subs.set_filters(pool, chat_id, countries=codes)
            note = COUNTRY_FILTER_NOTE if codes else ""
        else:
            fams, bad = parse_families(args)
            if bad:
                return [f"لم أتعرّف على: {esc('، '.join(bad))}\n\n" + _family_menu()]
            await subs.set_filters(pool, chat_id, families=fams)
            note = ""
        updated = await subs.get(pool, chat_id)
        assert updated is not None
        return ["✅ حُدّثت تفضيلاتك.\n\n" + describe(updated) + note]

    if cmd == "level":
        sub = await subs.get(pool, chat_id)
        if sub is None:
            return [NOT_SUBSCRIBED]
        if not args:
            return [_level_menu(), describe(sub)]
        lvl = parse_level(args)
        if lvl is None:
            return [f"لم أتعرّف على: {esc('، '.join(args))}\n\n" + _level_menu()]
        await subs.set_verbosity(pool, chat_id, lvl)
        updated = await subs.get(pool, chat_id)
        assert updated is not None
        return ["✅ حُدّث مستوى التفصيل.\n\n" + describe(updated)]

    if cmd == "help":
        return [HELP]

    # ⚠ An unknown command gets help, not silence. A public bot that ignores input reads as
    # broken, and the person has no way to discover what it does understand.
    return [HELP] if cmd else []
