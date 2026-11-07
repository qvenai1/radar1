"""BASELINE-1 — "usual" as a measurement, and the ways it must refuse to answer.

⛔ THE DEFECT THIS LAYER GUARDS AGAINST IS NOT NOISE, IT IS INVENTED NORMALITY. The easy
version of this feature prints «ضمن النمط المعتاد» whenever it has nothing to say, and a
system that asserts normality it never measured is worse than the fourteen-line digest it
replaced. So most of these tests are about what the classifier REFUSES to conclude.

The measurements behind the constants are in migration 041 and backend/pass_baseline.py:
`satellite_passes` keeps one distinct `computed_at` (no history) and `satellite_elements`
1-2 epochs per satellite (no element history), which is why the baseline is back-propagated
rather than observed.
"""

from __future__ import annotations

import pytest

from backend.models import Alert, AlertEntityRef
from backend.notify import render
from backend.pass_baseline import (
    MIN_SAMPLE, NOTABLE, ROUTINE, UNKNOWN, Baseline, aggregate, classify,
)


def bl(n=20, p10=150.0, median=500.0, countries=("TR", "IQ"), days=14) -> Baseline:
    return Baseline(n_passes=n, window_days=days, passes_per_day=n / days,
                    closest_p10=p10, closest_median=median, closest_min=40.0,
                    usual_countries=tuple(countries))


# =========================================================================================
# WHAT IT REFUSES TO CONCLUDE
# =========================================================================================

def test_no_baseline_is_unknown_never_routine():
    """⛔ THE CENTRAL GUARD. A satellite with no baseline row must not be reported as
    normal. Absence of evidence is not evidence of normality, and this is the one mistake
    that would make the whole feature dishonest."""
    v = classify(300.0, ["TR"], None)
    assert v.verdict == UNKNOWN and v.reason == "no_baseline"
    assert v.verdict != ROUTINE


def test_a_thin_baseline_is_unknown():
    """A distribution needs a sample. A p10 computed from three passes is noise wearing a
    statistic's clothes — the floor is read from the module, so retuning it cannot leave
    this test asserting the old rule while passing."""
    assert classify(300.0, ["TR"], bl(n=MIN_SAMPLE - 1)).reason == "thin_baseline"
    assert classify(300.0, ["TR"], bl(n=MIN_SAMPLE)).verdict != UNKNOWN


def test_a_missing_distance_is_unknown_not_normal():
    """A pass carrying no ground-track distance cannot be compared on that axis. "No
    distance" must not collapse into "not closer than usual"."""
    v = classify(None, ["TR"], bl())
    assert v.verdict == UNKNOWN and v.reason == "no_distance"


def test_unknown_carries_no_baseline_caveat():
    """The provenance note about «المعتاد» being back-propagated is appended only when a
    comparison actually happened — an UNKNOWN verdict must not cite a baseline it did not
    use."""
    from backend.alerts import _satellite_ar
    # ⚠ FOUR values since NEWS-1 added the lede — unpacking three silently became a
    # ValueError the moment the news layout shipped.
    _h, _w, caveat, _lede = _satellite_ar("X", ["TR"], "2026-08-16T05:38:00+00:00",
                                   "2026-08-15T00:00:00+00:00", 300.0, 800.0,
                                   classify(300.0, ["TR"], None))
    from backend import summaries_ar as AR
    assert AR.SAT_CAVEAT_BASELINE not in caveat


# =========================================================================================
# WHAT IT DOES CONCLUDE
# =========================================================================================

def test_within_the_usual_spread_is_routine():
    v = classify(600.0, ["TR"], bl(p10=150.0))
    assert v.verdict == ROUTINE and v.reason == "within_usual"
    assert v.usual_closest_km == 500.0, "the comparison value travels, so a message can "\
                                        "state what usual IS, not only that this differs"


def test_inside_its_own_tenth_percentile_is_notable():
    """"Closer than usual" is defined against THE OBJECT'S OWN distribution, not a fixed
    distance: 40 km is unremarkable for a LEO recon satellite and impossible for a GEO."""
    assert classify(120.0, ["TR"], bl(p10=150.0)).reason == "closer_than_usual"
    assert classify(150.0, ["TR"], bl(p10=150.0)).reason == "closer_than_usual", "boundary"
    assert classify(151.0, ["TR"], bl(p10=150.0)).reason == "within_usual"


def test_a_country_it_does_not_usually_cross_is_notable():
    """A change of geometry, not of degree — and it outranks the distance test, because a
    new country is notable even at a typical distance."""
    v = classify(900.0, ["SY"], bl(countries=("TR", "IQ")))
    assert v.verdict == NOTABLE and v.reason == "new_country"


def test_geostationary_is_routine_by_construction_not_unknown():
    """⛔ MEASURED ON THE FIRST BASELINE RUN: 7 of 13 objects returned n_passes = 1 — every
    one geosync (SKYNET 4C, SYRACUSE 3A/4A, RADUGA-1M 3, ATHENA-FIDUS, COSMOS 2520, TIBA-1).
    `classify_passes` emits ONE persistent row for a geosync object because its sub-point is
    ~fixed: it is STATIONED over the band, not transiting it.

    The sample floor would have called all seven "not enough history", which is wrong twice:
    a satellite parked over the region is the most routine thing in the catalogue, and its
    distance cannot vary the way a transit's does."""
    v = classify(None, ["IQ"], None, is_persistent=True)
    assert v.verdict == ROUTINE and v.reason == "geostationary"
    # and it must not depend on having a baseline at all
    assert classify(400.0, ["IQ"], bl(n=1), is_persistent=True).verdict == ROUTINE


# =========================================================================================
# THE AGGREGATOR
# =========================================================================================

def test_quantiles_are_real_observed_values():
    """Nearest-rank, not interpolation. With samples of 8-30 an interpolated p10 invents a
    distance that lies between two real passes and therefore describes none of them."""
    vals = [100.0, 200.0, 300.0, 400.0, 500.0]
    agg = aggregate(vals, [["TR"]] * 5, 14)
    assert agg["closest_min"] in vals and agg["closest_p10"] in vals
    assert agg["closest_median"] in vals


def test_usual_countries_is_the_union_not_the_intersection():
    """An object alternating between Turkey and Iraq usually crosses both; intersecting
    would make every pass look like a new country."""
    agg = aggregate([100.0, 200.0], [["TR"], ["IQ"]], 14)
    assert set(agg["usual_countries"]) == {"TR", "IQ"}


def test_passes_per_day_is_derived_from_the_window():
    agg = aggregate([1.0] * 28, [["TR"]] * 28, 14)
    assert agg["n_passes"] == 28 and agg["passes_per_day"] == 2.0


# =========================================================================================
# THE PAYOFF — routine collapses, everything else does not
# =========================================================================================

# ⚠ NEWS-4 — THE NEEDLE MOVED, THE SUBJECT DID NOT. A bulletin item used to be the alert's
# `lede`/`headline`; it is now the composed dispatch sentence («مرور مداري متوقَّع للجسم X»)
# with the object's label emphasised. These tests are about the baseline COLLAPSE and the
# verbosity CAPS, not about wording, so they now look for the label as a LISTED item —
# `<b>LABEL</b>`, which the collapse line cannot produce because it prints names unemphasised.
# That makes the needle a sharper discriminator than the old headline substring was.
def _listed(label: str) -> str:
    return f"<b>{label}</b>"


def _sat(label: str, verdict: str | None) -> Alert:
    return Alert(domain="space", family="military", signal_type="military_satellite",
                 severity="info", severity_basis="b",
                 entity_ref=AlertEntityRef(kind="satellite", id=label, label=label),
                 headline=f"ممرّ {label}", caveat="تحذير", baseline_verdict=verdict)


def test_routine_passes_are_counted_not_listed():
    """The whole point of the baseline: a crossing that matches its object's measured norm
    stops costing a line. Measured effect on the live feed — the satellite digest went from
    six full alerts to 250 units."""
    body = render.render_digest([_sat(f"SAT{i}", "routine") for i in range(6)])
    assert "ضمن النمط المعتاد لأجسامها" in body
    for i in range(6):
        assert _listed(f"SAT{i}") not in body, "a routine pass must not be listed"
    assert "<b>6</b>" in body, "but the count must still be reported"


def test_notable_and_unknown_are_listed_in_full():
    """⛔ ONLY `routine` COLLAPSES. Folding `unknown` into the routine count would assert
    the exact normality the baseline exists to avoid asserting."""
    body = render.render_digest([_sat("ROUTINE1", "routine"), _sat("NOTABLE1", "notable"),
                                 _sat("UNSURE1", "unknown")])
    assert _listed("NOTABLE1") in body
    assert _listed("UNSURE1") in body, "unknown is not 'fine' and must not be collapsed"
    assert _listed("ROUTINE1") not in body


def test_a_family_without_a_baseline_still_lists_normally():
    """Null verdict means "not assessed" — every non-satellite family — and must not be
    treated as routine."""
    a = Alert(domain="sea", family="maritime", signal_type="maritime_composite",
              severity="high", severity_basis="b",
              entity_ref=AlertEntityRef(kind="vessel", id="9", label="HULL"),
              headline="نصّ البدن", caveat="تحذير")
    assert a.baseline_verdict is None
    # ⚠ UPDATED BY NEWS-3, AND THE ASSERTION MOVED ON PURPOSE. This used to demand the
    # headline back BYTE-IDENTICAL, which encoded render.py's old "never re-written" rule
    # rather than this test's own subject. The bot now applies a newsroom register on the
    # way out (`notify/newsroom.py`), so «البدن» leaves as «السفينة» — the same claim in the
    # public vocabulary. What this test is actually about is that a null verdict is LISTED
    # and not collapsed as routine, so it asserts the item's presence through the register
    # instead of around it.
    assert "نصّ السفينة" in render.render_digest([a])


@pytest.mark.parametrize("verdict,expected_ar", [
    ("within_usual", "ضمن النمط المعتاد"),
    ("closer_than_usual", "أقرب من المعتاد"),
    ("geostationary", "ثابت بالنسبة للأرض"),
    ("no_baseline", "لا سند كافٍ"),
])
def test_every_reason_has_its_own_arabic_sentence(verdict, expected_ar):
    """A reason without a sentence would ship an empty comparison — the drift this
    project's registers are gated against everywhere else."""
    from backend import summaries_ar as AR
    assert expected_ar in AR.SAT_BASELINE_BY_REASON[verdict]


# =========================================================================================
# NOTIFY-3 — VERBOSITY. Presentation density only.
# =========================================================================================

def _mil(label: str, countries=None) -> Alert:
    return Alert(domain="air", family="military", signal_type="military_aircraft",
                 severity="info", severity_basis="b",
                 entity_ref=AlertEntityRef(kind="aircraft", id=label, label=label),
                 headline=f"طائرة {label}", caveat="تحذير", countries=countries or [])


def test_quiet_keeps_what_matters_and_collapses_what_does_not():
    """⛔ FOUND IN A LIVE RENDER, NOT IN REVIEW. `quiet` was first written with a cap of 0,
    which suppressed the ONE pass flagged closer-than-usual into «و1 أخرى» while the five
    routine ones kept their count line. Exactly backwards: "quiet" means only what matters,
    not everything except what matters."""
    mixed = [_sat("NOTABLE1", "notable")] + [_sat(f"R{i}", "routine") for i in range(5)]
    body = render.render_digest(mixed, verbosity=render.QUIET)
    assert _listed("NOTABLE1") in body, "quiet must still deliver the notable item"
    assert _listed("R0") not in body, "routine still collapses"
    assert "<b>5</b> ضمن النمط المعتاد" in body, "and its count is still reported"


def test_quiet_lists_fewer_than_balanced():
    items = [_mil(f"AC{i}") for i in range(9)]
    q = render.render_digest(items, verbosity=render.QUIET)
    b = render.render_digest(items, verbosity=render.BALANCED)
    item = "▪️ رُصدت طائرة عسكرية"
    assert q.count(item) == render.DIGEST_CAPS[render.QUIET]
    assert b.count(item) == render.DIGEST_CAPS[render.BALANCED]
    assert render._tg_len(q) < render._tg_len(b)


def test_the_arabic_register_contains_no_markup():
    """⛔ FOUND IN A LIVE RENDER: «و<b>أقرب من المعتاد</b>» printed as literal `&lt;b&gt;`,
    because the digest HTML-escapes headlines — correctly, since a vessel name may contain
    `<`. And the register is shared with the React frontend, where a tag would print
    literally or force dangerouslySetInnerHTML. Emphasis belongs to whoever renders."""
    from backend import summaries_ar as AR
    for name in dir(AR):
        if not name.startswith("SAT_BASELINE"):
            continue
        val = getattr(AR, name)
        for text in (val.values() if isinstance(val, dict) else [val]):
            if isinstance(text, str):
                assert "<" not in text and ">" not in text, f"{name} carries markup"


def test_balanced_caps_and_states_the_remainder():
    """⛔ A cap that hid its own effect would make the section header's count disagree with
    the lines beneath it — the same defect the endpoint's per-family cap avoids."""
    n = render.DIGEST_CAPS[render.BALANCED] + 4
    body = render.render_digest([_mil(f"AC{i}") for i in range(n)],
                                verbosity=render.BALANCED)
    assert body.count("▪️ رُصدت طائرة عسكرية") == render.DIGEST_CAPS[render.BALANCED]
    assert "و4 أخرى" in body, "the hidden remainder must be stated"


def test_detailed_lists_everything_including_routine():
    """`detailed` opts OUT of the routine collapse — that is what the level means."""
    sats = [_sat(f"S{i}", "routine") for i in range(6)]
    body = render.render_digest(sats, verbosity=render.DETAILED)
    for i in range(6):
        assert _listed(f"S{i}") in body
    assert "ضمن النمط المعتاد لأجسامها" not in body, "no collapse at detailed"


def test_quiet_declines_an_all_routine_digest():
    """⛔ The point of the level: no hourly "6 routine passes, nothing new"."""
    routine = [_sat(f"S{i}", "routine") for i in range(6)]
    assert render.digest_is_worth_sending(routine, render.QUIET) is False
    assert render.digest_is_worth_sending(routine, render.BALANCED) is True


def test_quiet_still_sends_when_anything_is_unknown_or_notable():
    """⚠ `unknown` counts as signal. "No basis to compare this yet" is not "this is fine",
    and letting quiet swallow it would hide exactly the passes the baseline cannot vouch
    for."""
    for v in ("notable", "unknown", None):
        mixed = [_sat("R", "routine"), _sat("X", v)]
        assert render.digest_is_worth_sending(mixed, render.QUIET) is True, v


def test_verbosity_never_touches_the_instant_path():
    """⛔ "Quiet" is a request for less noise, never to be told late about an emergency.
    render_alert takes no verbosity at all — the property is structural, not a rule someone
    has to remember."""
    import inspect
    assert "verbosity" not in inspect.signature(render.render_alert).parameters


def test_the_country_breakdown_counts_touches_not_a_partition():
    """An alert answers to EVERY country it touches, so these counts legitimately exceed the
    section total. Unattributed alerts are counted explicitly — at 64 % of military aircraft,
    dropping them would make the busiest part of a section invisible."""
    body = render.render_digest(
        [_mil("A", ["SY", "TR"]), _mil("B", ["SY"]), _mil("C", [])],
        verbosity=render.BALANCED)
    assert "سوريا 2" in body and "تركيا 1" in body
    assert "بلا إسناد جغرافي 1" in body


def test_verbosity_is_part_of_the_render_memo_key():
    """Two subscribers with identical filters but different levels must not share a
    memoised digest body."""
    from backend.notify import subscribers as S
    import datetime as dt
    now = dt.datetime(2026, 8, 15, tzinfo=dt.timezone.utc)
    a = S.Subscriber("a", S.ACTIVE, now, None, None, verbosity=render.QUIET)
    b = S.Subscriber("b", S.ACTIVE, now, None, None, verbosity=render.DETAILED)
    assert S.filter_key(a) != S.filter_key(b)
