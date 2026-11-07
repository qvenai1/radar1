"""D5 alert feed — the unified multi-domain classification signal.

Proves the CONTRACT that makes the feed honest: the four families map to the
right domain/signal_type, severity is per-family (emergency HIGH, military INFO,
maritime = its own composite rank, implausible LOW), the ordering sinks
informational presence below a low-but-real anomaly, and `matched_query` is the
additive seam (always null today). Pure over the builders — no DB, deterministic;
the DB endpoint's live counts are proven in the gauntlet.
"""

from types import SimpleNamespace

from backend.alerts import (
    CAVEAT,
    EMERGENCY_SQUAWKS,
    HIGH,
    INFO,
    LOW,
    build_emergency_alerts,
    build_implausible_alerts,
    build_maritime_alerts,
    build_military_air_alerts,
    build_military_sat_alerts,
    sort_alerts,
)


# --- emergency (air, HIGH) -------------------------------------------------

def test_emergency_only_fires_on_emergency_squawks():
    states = [
        {"icao24": "abc123", "callsign": "SAW14 ", "squawk": "7700"},
        {"icao24": "def456", "callsign": "ELY001", "squawk": "1200"},   # normal — ignored
        {"icao24": "ghi789", "callsign": "", "squawk": "7500"},
    ]
    out = build_emergency_alerts(states, vintage="2026-07-22T10:00:00+00:00", live=True)
    assert len(out) == 2
    assert {a.entity_ref.id for a in out} == {"abc123", "ghi789"}
    for a in out:
        assert a.domain == "air" and a.family == "emergency"
        assert a.signal_type == "emergency_squawk" and a.severity == HIGH
        assert a.entity_ref.kind == "aircraft"
        assert a.matched_query is None            # additive seam — null today
    # the meaning is surfaced (hijack / general-emergency), and liveness travels
    hijack = next(a for a in out if a.entity_ref.id == "ghi789")
    assert EMERGENCY_SQUAWKS["7500"] in hijack.summary
    assert hijack.live is True


def test_emergency_carries_feed_liveness_honestly():
    states = [{"icao24": "aaa", "callsign": "X", "squawk": "7600"}]
    stale = build_emergency_alerts(states, vintage="2026-07-22T06:00:00+00:00", live=False)
    assert stale[0].live is False               # a stale air feed reads as stale
    assert stale[0].vintage == "2026-07-22T06:00:00+00:00"


# --- military (INFO — presence, never a threat) ----------------------------

def test_military_air_is_info_presence():
    states = [{"icao24": "43c0aa", "callsign": "RRR21", "aircraft_type": "A400", "country": "Iraq"}]
    out = build_military_air_alerts(states, vintage="2026-07-22T10:00:00+00:00", live=True)
    assert len(out) == 1
    a = out[0]
    assert a.domain == "air" and a.family == "military"
    assert a.signal_type == "military_aircraft" and a.severity == INFO
    assert "not a threat" in a.severity_basis     # the honesty is explicit


def test_military_sat_is_info_and_not_live():
    rows = [{
        "norad_id": 42907, "object_name": "COSMOS 2520",
        "start_time": "2026-07-22T12:00:00+00:00", "closest_km": 730.8,
        "countries": ["Iraq"], "tle_epoch": "2026-07-22T04:00:00+00:00",
        "users": "Military/Commercial", "operator_country": "Russia",
    }]
    out = build_military_sat_alerts(rows)
    a = out[0]
    assert a.domain == "space" and a.family == "military"
    assert a.signal_type == "military_satellite" and a.severity == INFO
    assert a.live is False                        # a pass is a forward SGP4 prediction
    assert "Iraq" in a.summary and "SGP4" in a.severity_basis


# --- maritime (severity IS the hull's own composite rank) ------------------

def _hull(rank, n_types=2, grey=False, name="EUROSAILOR", imo="9895915"):
    ident = SimpleNamespace(name=name, flag_country="Malta", ship_type="tanker")
    return SimpleNamespace(
        hull=imo, imo=imo, identities=[ident], composite_rank=rank,
        n_pattern_types=n_types, n_gaps=2, n_rendezvous=2, n_identity_changes=1,
        involves_grey=grey, aoi_waters=["Israel", "Turkey"],
        last_event_ts="2026-07-19T15:35:39+00:00",
    )


def test_maritime_severity_is_the_composite_rank():
    for rank in (HIGH, "medium", LOW):
        out = build_maritime_alerts([_hull(rank)])
        assert out[0].severity == rank            # carried through, never overridden
        assert out[0].signal_type == "maritime_composite" and out[0].domain == "sea"
    high = build_maritime_alerts([_hull(HIGH, n_types=4)])[0]
    assert high.entity_ref.id == "9895915" and high.entity_ref.label == "EUROSAILOR"
    assert high.vintage == "2026-07-19T15:35:39+00:00"   # a 3-day-old lead reads its age
    assert "still not proof" in high.severity_basis


def test_maritime_grey_flag_noted_in_basis():
    out = build_maritime_alerts([_hull(HIGH, grey=True)])
    assert "grey/sanctions" in out[0].severity_basis


# --- implausible (sea, LOW — usually a victim) -----------------------------

def test_implausible_is_low_victim_leaning():
    rows = [{"mmsi": "215936000", "n_fixes": 3, "last_ts": "2026-07-22T07:37:49+00:00",
             "name": "JOAV I", "flag_country": "Saint Kitts and Nevis"}]
    out = build_implausible_alerts(rows, live=True)
    a = out[0]
    assert a.domain == "sea" and a.family == "implausible"
    assert a.signal_type == "implausible_position" and a.severity == LOW
    assert "victim" in a.severity_basis
    assert a.live is True and "3 implausible" in a.summary


# --- ordering: most-severe first; INFO presence sinks below a real LOW -----

def test_sort_puts_high_first_and_info_last():
    mil = build_military_air_alerts([{"icao24": "m", "callsign": "MIL"}],
                                    vintage="2026-07-22T10:00:00+00:00", live=True)
    imp = build_implausible_alerts([{"mmsi": "1", "n_fixes": 1,
                                     "last_ts": "2026-07-22T10:00:00+00:00", "name": "V"}], live=True)
    emg = build_emergency_alerts([{"icao24": "e", "callsign": "E", "squawk": "7700"}],
                                 vintage="2026-07-22T10:00:00+00:00", live=True)
    order = [a.severity for a in sort_alerts(mil + imp + emg)]
    assert order == [HIGH, LOW, INFO]            # emergency > implausible > military-presence


def test_caveat_states_the_honest_severity_mapping():
    assert "no stored alert table" in CAVEAT and "not a threat" in CAVEAT


# --- countries: the geographic axis (DASH-1 step 1) ------------------------
#
# `countries` is WHERE the alert is, as ISO alpha-2, and it must never be confused
# with a registration (`flag_country` / `origin_country` / `operator_country`),
# which is WHOSE it is. Every family carries it, it is a LIST because two families
# are genuinely multi-country, and empty means "not attributable to the seven" —
# not "unknown".

def test_emergency_carries_the_classifier_stamped_country() -> None:
    """/live states are stamped at ingest, so this is a read, not a computation."""
    a = build_emergency_alerts(
        [{"icao24": "abc123", "squawk": "7700", "country": "Syria"}], "t", True)[0]
    assert a.countries == ["SY"]


def test_emergency_prefers_containing_country_then_near_border() -> None:
    a = build_emergency_alerts(
        [{"icao24": "a", "squawk": "7500", "country": "Iraq", "near_country": "Turkey"}],
        "t", True)[0]
    assert a.countries == ["IQ", "TR"], "containing country first, near-border second"


def test_military_air_classifies_from_position_because_the_overlay_has_no_country() -> None:
    """The airplanes.live overlay bypasses the geo classifier (measured: its states
    carry no country/near_country), so the family must classify its own position —
    through the SAME shared classifier, not a second definition of 'in Turkey'."""
    inside_turkey = {"icao24": "4b82ff", "latitude": 38.74, "longitude": 28.68}
    a = build_military_air_alerts([inside_turkey], "t", True)[0]
    assert a.countries == ["TR"]


def test_military_air_outside_the_aoi_is_empty_not_guessed() -> None:
    """A positioned aircraft far outside the seven yields [] — 'not attributable',
    which is different from 'no position' and must not be filled in."""
    aegean = {"icao24": "06a256", "latitude": 34.99, "longitude": 24.66}
    assert build_military_air_alerts([aegean], "t", True)[0].countries == []


def test_military_air_without_a_position_is_empty_never_dropped() -> None:
    a = build_military_air_alerts([{"icao24": "nopos"}], "t", True)
    assert len(a) == 1 and a[0].countries == []


def test_military_satellite_carries_countries_structurally_not_only_as_prose() -> None:
    """These were already computed into satellite_passes and then thrown away in the
    summary string. A pass crosses several borders — hence a list, and hence
    near_countries counts too."""
    a = build_military_sat_alerts([{
        "norad_id": 1, "object_name": "X", "countries": ["Iraq"],
        "near_countries": ["Turkey"], "closest_km": 300.0,
    }])[0]
    assert a.countries == ["IQ", "TR"]
    assert "Iraq" in a.summary, "the prose stays; the structure is additive"


def test_maritime_uses_aoi_waters_and_never_the_flag() -> None:
    """The load-bearing separation: aoi_waters is geography, flag_country is
    registration. A UK-flagged hull in Israeli waters is IL, not GB."""
    hull = SimpleNamespace(
        hull="9999999", imo="9999999", identities=[
            SimpleNamespace(name="X", flag_country="United Kingdom")],
        n_gaps=0, n_rendezvous=11, n_identity_changes=0, n_pattern_types=2,
        composite_rank="high", involves_grey=False, aoi_waters=["Israel"],
        last_event_ts="t", imo_quality=None)
    a = build_maritime_alerts([hull])[0]
    assert a.countries == ["IL"]
    assert "United Kingdom" in a.summary and "GB" not in a.countries


def test_maritime_touching_no_aoi_waters_is_empty() -> None:
    hull = SimpleNamespace(
        hull="1", imo="1", identities=[], n_gaps=1, n_rendezvous=0,
        n_identity_changes=0, n_pattern_types=2, composite_rank="low",
        involves_grey=False, aoi_waters=[], last_event_ts="t", imo_quality=None)
    assert build_maritime_alerts([hull])[0].countries == []


def test_implausible_aggregates_waters_and_ignores_the_flag() -> None:
    """A drifting spoofed track can cross waters, so the query aggregates a set."""
    a = build_implausible_alerts([{
        "mmsi": "1", "name": "V", "n_fixes": 3, "last_ts": "t",
        "flag_country": "Saint Kitts and Nevis",
        "countries": ["Lebanon"], "near_countries": ["Cyprus"],
    }], live=True)[0]
    assert a.countries == ["LB", "CY"]
    assert "Saint Kitts" in a.summary


def test_a_non_aoi_country_survives_verbatim_rather_than_vanishing() -> None:
    """flag-never-delete: a country we cannot code is still one we observed."""
    hull = SimpleNamespace(
        hull="1", imo="1", identities=[], n_gaps=0, n_rendezvous=2,
        n_identity_changes=0, n_pattern_types=2, composite_rank="low",
        involves_grey=False, aoi_waters=["Israel", "Egypt"], last_event_ts="t",
        imo_quality=None)
    assert build_maritime_alerts([hull])[0].countries == ["IL", "Egypt"]


def test_countries_are_deduped_preserving_order() -> None:
    a = build_military_sat_alerts([{
        "norad_id": 1, "object_name": "X",
        "countries": ["Syria", "Turkey"], "near_countries": ["Turkey", "Iraq"],
    }])[0]
    assert a.countries == ["SY", "TR", "IQ"]


# --- importance: the second, ORTHOGONAL axis (DASH-1 step 3) ---------------
#
# The point of the axis is that both statements are true of one alert at once:
# military presence is INFO (not a threat) AND rank 1 (the salient fact of a country
# watch). If a change ever makes one imply the other, these fail.

def test_severity_order_is_untouched_by_dash1() -> None:
    """The honesty commitment: INFO still sinks below LOW on the severity axis."""
    from backend.alerts import SEVERITY_ORDER
    assert SEVERITY_ORDER == {"high": 0, "medium": 1, "low": 2, "info": 3}


def test_military_is_info_and_important_at_the_same_time() -> None:
    a = build_military_air_alerts([{"icao24": "a", "latitude": 38.74, "longitude": 28.68}],
                                  "t", True)[0]
    assert a.severity == INFO, "must stay informational — never a threat"
    assert a.importance == 1, "and still rank near the top of a country watch"


def test_importance_lifts_military_above_maritime_and_implausible() -> None:
    from backend.alerts import importance_of
    assert importance_of("emergency") < importance_of("military")
    assert importance_of("military") < importance_of("maritime")
    assert importance_of("maritime") < importance_of("implausible")


def test_an_unknown_family_sorts_last_never_first() -> None:
    from backend.alerts import IMPORTANCE_UNKNOWN, importance_of
    assert importance_of("something_new") == IMPORTANCE_UNKNOWN
    assert IMPORTANCE_UNKNOWN > max(
        importance_of(f) for f in ("emergency", "military", "maritime", "implausible"))


def test_the_two_sorters_disagree_which_is_the_whole_point() -> None:
    """sort_alerts puts the LOW anomaly above INFO presence; sort_by_importance puts
    the military presence first. Same list, two orders, both honest."""
    from backend.alerts import sort_by_importance
    mil = build_military_air_alerts(
        [{"icao24": "m", "latitude": 38.74, "longitude": 28.68}], "2026-01-01", True)[0]
    imp = build_implausible_alerts(
        [{"mmsi": "1", "name": "V", "n_fixes": 1, "last_ts": "2026-01-02"}], live=True)[0]
    assert [a.family for a in sort_alerts([mil, imp])] == ["implausible", "military"]
    assert [a.family for a in sort_by_importance([mil, imp])] == ["military", "implausible"]


# --- live_key: IMO -> the MMSI that opens the live payload (DASH-1 step 4) --
#
# `id` stays the IMO (durable across a re-flag, it stitches the history). But every
# live vessel contract is MMSI-keyed, and IMO -> MMSI is one-to-many, so the ref must
# say WHICH MMSI is current without discarding the others.

def _keyed_hull(imo="9241346", mmsis=("a", "b")):
    return SimpleNamespace(
        hull=imo, imo=imo,
        identities=[SimpleNamespace(mmsi=m, name="V", flag_country=None) for m in mmsis],
        n_gaps=1, n_rendezvous=1, n_identity_changes=0, n_pattern_types=2,
        composite_rank="low", involves_grey=False, aoi_waters=["Syria"],
        last_event_ts="t", imo_quality=None)


def test_id_stays_the_imo_and_live_key_is_the_mmsi() -> None:
    """The durable identity and the addressable key are different things."""
    a = build_maritime_alerts([_keyed_hull()], {"9241346": ["314163009", "351049000"]})[0]
    assert a.entity_ref.id == "9241346", "id must remain the durable IMO"
    assert a.entity_ref.live_key == "314163009"


def test_the_other_mmsis_survive_as_history_not_dropped() -> None:
    a = build_maritime_alerts([_keyed_hull()], {"9241346": ["314163009", "351049000"]})[0]
    assert a.entity_ref.alt_keys == ["351049000"]


def test_recency_order_from_the_endpoint_is_respected_verbatim() -> None:
    """The resolver ranks by LAST FIX, not by fix count — a re-flagged hull's old
    MMSI can have far more history and still not be the one transmitting now."""
    a = build_maritime_alerts([_keyed_hull()], {"9241346": ["351049000", "314163009"]})[0]
    assert a.entity_ref.live_key == "351049000"
    assert a.entity_ref.alt_keys == ["314163009"]


def test_falls_back_to_the_hulls_own_mmsis_without_a_resolution() -> None:
    """Pure-module fallback: usable, just not recency-resolved."""
    a = build_maritime_alerts([_keyed_hull(mmsis=("x", "y"))])[0]
    assert a.entity_ref.live_key == "x" and a.entity_ref.alt_keys == ["y"]


def test_a_hull_with_no_mmsi_has_no_live_key_rather_than_a_fake_one() -> None:
    a = build_maritime_alerts([_keyed_hull(mmsis=())])[0]
    assert a.entity_ref.live_key is None and a.entity_ref.alt_keys == []


def test_the_other_families_are_already_addressable_by_their_id() -> None:
    """aircraft/satellite/implausible ids ARE their live keys — stated, not left null."""
    em = build_emergency_alerts([{"icao24": "abc", "squawk": "7700"}], "t", True)[0]
    sat = build_military_sat_alerts([{"norad_id": 5, "object_name": "S"}])[0]
    imp = build_implausible_alerts(
        [{"mmsi": "1", "name": "V", "n_fixes": 1, "last_ts": "t"}], live=True)[0]
    assert em.entity_ref.live_key == "abc"
    assert sat.entity_ref.live_key == "5"
    assert imp.entity_ref.live_key == "1"


# --- the archive/DB stamping split (DASH-3 step 2) -------------------------
#
# /live is served ARCHIVE-FIRST from latest.json, which the collector writes with
# NO geo columns — the classifier runs on the ingest.py DB path. A comment here once
# claimed the opposite ("classifier-stamped at ingest, so the country is a read"),
# which silently left every emergency alert with countries=[]. These pin the real
# behaviour so the claim cannot drift back.

def test_emergency_classifies_its_position_when_live_carries_no_country() -> None:
    """The archive path has lat/lon and nothing else — the country must be derived."""
    over_damascus = {"icao24": "a", "squawk": "7700", "latitude": 33.51, "longitude": 36.29}
    assert build_emergency_alerts([over_damascus], "t", True)[0].countries == ["SY"]


def test_emergency_still_prefers_a_stamped_country_when_one_is_present() -> None:
    """The DB-backed shape is honoured first, for the day /live is served from it —
    a read beats a recomputation when the read is available."""
    stamped = {"icao24": "a", "squawk": "7700", "country": "Iraq",
               "latitude": 33.51, "longitude": 36.29}   # position says Syria
    assert build_emergency_alerts([stamped], "t", True)[0].countries == ["IQ"]


def test_emergency_outside_the_aoi_stays_honestly_empty() -> None:
    aegean = {"icao24": "a", "squawk": "7500", "latitude": 34.99, "longitude": 24.66}
    assert build_emergency_alerts([aegean], "t", True)[0].countries == []


# --- RANK-S2: the Arabic `why` names a SUFFICIENT cause ----------------------
#
# ⛔ THE DEFECT THESE PIN, measured on the live feed before the fix: 172 of 200 maritime
# alerts read «الرتبة مرتفعة لتزامن نمطين» — the rank is HIGH *because of* two co-occurring
# patterns. Two pattern types alone are MEDIUM, so the sentence credited a condition that
# could not have produced the rank it was explaining. A further 1 of 200 said «لنمط واحد
# مرصود» on a hull the feed admits only at n_pattern_types >= 2, because the `why` read the
# timeline while the rank came from the view's count.

def _s2_hull(**kw):
    """A composite hull with RANK-S2's fields, defaulting to the common shape."""
    base = dict(
        hull="9999999", imo="9999999",
        identities=[SimpleNamespace(name="X", flag_country="Panama")],
        n_gaps=1, n_rendezvous=1, n_identity_changes=0, n_pattern_types=2,
        composite_rank="high", rank_reason="two_patterns_grey", involves_grey=True,
        aoi_waters=["Syria"], last_event_ts="t", imo_quality=None,
        timeline=[SimpleNamespace(event_type="ais_gap"),
                  SimpleNamespace(event_type="rendezvous")])
    base.update(kw)
    return SimpleNamespace(**base)


def test_why_never_credits_two_patterns_alone_for_a_high_rank():
    """The 172/200 defect. Two patterns + a grey flag is HIGH; the sentence must name the
    flag as part of the cause, not the pattern count on its own."""
    why = build_maritime_alerts([_s2_hull()])[0].why
    assert why == "الرتبة مرتفعة: تزامن نمطين مع علم مدرَج على قوائم رقابية"
    assert "لتزامن" not in why, "the causal «ل» is what made the old sentence false"


def test_two_patterns_alone_are_medium_and_say_so():
    """The same count, honestly attributed: with no grey flag and no high event it IS the
    reason — and it produces MEDIUM, which is what the sentence now claims."""
    a = build_maritime_alerts([_s2_hull(
        composite_rank="medium", rank_reason="two_patterns", involves_grey=False,
        identities=[SimpleNamespace(name="X", flag_country="Greece")])])[0]
    assert a.why == "الرتبة متوسّطة: تزامن نمطين"


def test_why_names_both_sufficient_conditions_when_both_hold():
    a = build_maritime_alerts([_s2_hull(rank_reason="two_patterns_grey_and_high_event")])[0]
    assert a.why == ("الرتبة مرتفعة: تزامن نمطين مع علم مدرَج على قوائم رقابية "
                     "وحدث رتبته مرتفعة")


def test_the_listed_flag_is_never_stated_twice():
    """⛔ When the reason itself names the flag, the separate grey clause must NOT be
    appended — and when it does not (three patterns), it must still be."""
    two = build_maritime_alerts([_s2_hull()])[0].why
    assert two.count("قوائم رقابية") == 1
    three = build_maritime_alerts([_s2_hull(
        n_pattern_types=3, rank_reason="three_patterns",
        timeline=[SimpleNamespace(event_type="ais_gap"),
                  SimpleNamespace(event_type="rendezvous"),
                  SimpleNamespace(event_type="identity_change")])])[0].why
    assert three == "الرتبة مرتفعة: تزامن 3 أنماط؛ وعلمها مدرَج على قوائم رقابية"
    assert three.count("قوائم رقابية") == 1


def test_why_reads_the_field_the_rank_came_from_not_the_timeline():
    """The 1/200 defect. This hull's timeline surfaced ONE event type while the view counted
    two — the count the rank was computed from. The `why` must explain the rank with the
    latter, or it explains a rank its own number could not produce. The HEADLINE still lists
    the timeline, which is authoritative for WHICH patterns were seen."""
    a = build_maritime_alerts([_s2_hull(
        timeline=[SimpleNamespace(event_type="ais_gap")])])[0]
    assert "نمطين" in a.why and "الحدث الوحيد" not in a.why


def test_an_unknown_reason_states_the_rank_and_no_cause():
    """⛔ Degrade to a MISSING cause, never a wrong one — the English severity_basis still
    travels on the alert and stays authoritative."""
    a = build_maritime_alerts([_s2_hull(rank_reason="", involves_grey=False,
                                     identities=[])])[0]
    assert a.why == "الرتبة مرتفعة"
    assert "a stronger lead, still not proof" in a.severity_basis


def test_every_why_opens_with_the_rank_word_matching_its_own_severity():
    """The badge and its ⓘ must not name two different ranks — the mismatch RANK-pre §5
    measured across 221 of 221 live alerts under a rename with no rewrite."""
    RANK_AR = {"high": "مرتفعة", "medium": "متوسّطة", "low": "منخفضة"}
    for rank, reason in (("high", "three_patterns"), ("medium", "two_patterns"),
                         ("high", "two_patterns_high_event"), ("low", "single_low_event")):
        a = build_maritime_alerts([_s2_hull(composite_rank=rank, rank_reason=reason,
                                         involves_grey=False, identities=[])])[0]
        assert a.why.startswith(f"الرتبة {RANK_AR[rank]}"), a.why
        assert a.severity == rank, "⛔ severity itself must not move"
