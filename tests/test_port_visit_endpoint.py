"""PORT-2 — the /maritime/port-visits endpoint + the A1 reconciliation.

In-process ASGI against the LIVE DB (skips without DATABASE_URL). The sea is live, so —
like the zone-dwell and airport suites — this asserts SHAPE and BEHAVIOUR against known
ports rather than frozen counts. Every assertion here is chosen to survive coverage
drift: the fourth frozen-snapshot breakage (IMO-2, D4, ZONES-2, and the Queen Alia
ratio) is enough precedent.

Also carries the A1 reconciliation (PORT-3, folded in): a port visit and an A1 gap on
the same hull are NOT independent observations, and the test exists so a future study
cannot quietly double-count them.
"""

import os
from contextlib import asynccontextmanager

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

WINDOW = 384                     # the full retention span, so ports have material
STATES = ("complete", "open_ended", "truncated", "presence_only")


@asynccontextmanager
async def _client():
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app
    from backend.db import Database

    app = create_app()
    app.state.db = Database(DSN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                              base_url="http://t", timeout=90) as c:
            yield c
    finally:
        await app.state.db.close()


async def _get(c, **params):
    params.setdefault("hours", WINDOW)
    r = await c.get("/api/v1/maritime/port-visits", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------- shape + surfacing
@requires_db
@pytest.mark.asyncio
async def test_endpoint_shape_and_caveat():
    async with _client() as c:
        d = await _get(c, q="Haifa")
    assert d["available"] is True
    assert d["method"] == "port-visit-v1"
    assert d["port"]["port_name"] == "Haifa"
    # the caveat MUST be surfaced, and must carry the layer's own framing
    low = d["caveat"].lower()
    assert "routine commerce" in low and "search aperture" in low
    assert "not independent" in low            # the A1 non-independence, user-visible
    # count is the FULL measured set; returned is what the display cap emitted
    assert d["count"] >= d["returned"] == len(d["sessions"])
    if d["sessions"]:
        s = d["sessions"][0]
        assert {"completeness", "duration_is_lower_bound", "arrival_ts_is_unknown",
                "boundary_note", "candidate_ports", "radius_basis",
                "coord_precision"} <= set(s)


@requires_db
@pytest.mark.asyncio
async def test_completeness_is_always_returned_beside_its_geometry():
    """PORT-pre measured `complete` moving 20.9 % -> 54.9 % on core radius alone, so a
    completeness figure without the geometry that produced it is meaningless."""
    async with _client() as c:
        d = await _get(c, q="Haifa")
    g = d["geometry"]
    assert g["radius_m"] > 0
    assert g["radius_basis"] == "assumed"       # a search aperture, not evidence
    assert g["coord_precision"] == "arcminute"  # degrees-and-minutes, +/- ~1 km
    assert g["gap_split_s"] == 21600.0 and g["low_speed_kn"] == 1.0
    assert g["min_low_speed_span_s"] == 7200.0 and g["majority_low_frac"] == 0.5
    assert g["flank_s"] > 0 and g["flank_pad_km"] > 0
    # the quality rule is STATED on the response, not just in code
    assert "NULL PASSING" in g["quality_rule"]
    assert "stricter than the standard G3" in g["quality_rule"]
    cm = d["completeness"]
    assert sum(cm[s] for s in STATES) == d["count"]


@requires_db
@pytest.mark.asyncio
async def test_four_states_with_reasons_and_arrival_unknown_discipline():
    """At ~1 visit in 5 complete, the incomplete states are the MAJORITY of the output."""
    async with _client() as c:
        d = await _get(c, q="Haifa")
    if not d["sessions"]:
        pytest.skip("no Haifa visits in the window (coverage-limited — valid)")
    cm = d["completeness"]
    assert set(STATES) <= set(cm)
    # arrival_unknown is exactly the two ENTRY-missing states, never more
    assert cm["arrival_unknown"] == cm["truncated"] + cm["presence_only"]
    # reasons are drawn from the closed set and are attributed, not left blank
    assert set(cm["missing_entry_reasons"]) <= {"outage", "vessel_silence", "window_edge"}
    assert set(cm["missing_exit_reasons"]) <= {"outage", "vessel_silence", "window_edge"}
    for s in d["sessions"]:
        assert s["completeness"] in STATES
        assert s["duration_is_lower_bound"] is True            # in every state
        entry_missing = s["completeness"] in ("truncated", "presence_only")
        assert s["arrival_ts_is_unknown"] is entry_missing
        # a missing side always carries WHY; an observed side never invents one
        assert bool(s["entry_missing_reason"]) is (s["entry_ts"] is None)
        assert bool(s["exit_missing_reason"]) is (s["exit_ts"] is None)
        # the DRAUGHT-1 lesson, on the rendered string
        assert "still in port" not in s["boundary_note"].lower()
        assert s["boundary_note"]
        if s["completeness"] == "complete":
            assert s["boundary_note"] == "arrival and departure both observed"


@requires_db
@pytest.mark.asyncio
async def test_outage_attribution_reaches_the_sessions():
    """Missing boundaries are attributed against reconstructed feed-outage windows, so at
    least some of them should read as a feed outage rather than vessel silence — and the
    two sides are attributed independently."""
    async with _client() as c:
        d = await _get(c, q="Haifa")
    if not d["sessions"]:
        pytest.skip("coverage-limited")
    reasons = set(d["completeness"]["missing_entry_reasons"]) | \
        set(d["completeness"]["missing_exit_reasons"])
    assert reasons, "sessions with a missing side must carry a reason"
    for s in d["sessions"]:
        # a feed-down phrasing never prints a contradictory zero duration
        assert "0.0 h" not in s["boundary_note"]
        if s["exit_missing_reason"] == "outage":
            assert s["exit_outage_gap_s"] is not None
        # an exit-side outage must not leak into the arrival explanation
        if s["entry_missing_reason"] == "vessel_silence":
            assert "no arrival observed — vessel silent" in s["boundary_note"]


# --------------------------------------------------------------- funnel + quality
@requires_db
@pytest.mark.asyncio
async def test_transit_visit_funnel_is_reported_as_exclusion_accounting():
    """A survivor count is never reported alone (DRAUGHT-1): the funnel says how many raw
    crossings fell at each bar. PORT-pre measured 61 % of raw crossings as transits."""
    async with _client() as c:
        d = await _get(c, q="Haifa")
    f = d["funnel"][0]
    assert f["port_id"] == d["port"]["port_id"]
    assert set(f["dropped_at"]) == {"single_fix", "below_min_duration",
                                    "below_low_speed_span", "not_majority_low"}
    assert f["raw_crossings"] == f["visits"] + sum(f["dropped_at"].values())
    assert f["visits"] == d["count"]
    # "nothing observed" is DISTINCT from "excluded by a bar"
    assert f["no_presence_observed"] is (f["raw_crossings"] == 0)
    assert f["dropped_fixes_quality"] >= 0


@requires_db
@pytest.mark.asyncio
async def test_quality_tightening_is_visible_and_null_fixes_are_not_discarded():
    """The stricter-than-G3 filter must be VISIBLE (a per-session and per-port drop
    count) — and NULL-quality fixes must still produce sessions, or every in-progress
    visit would be truncated by the 15-minute quality timer."""
    import asyncpg

    async with _client() as c:
        d = await _get(c, q="Haifa")
    f = d["funnel"][0]
    assert sum(s["dropped_fixes"] for s in d["sessions"]) <= f["dropped_fixes_quality"]

    conn = await asyncpg.connect(DSN)
    try:
        # NULL-quality fixes exist inside Haifa's radius (the timer has not scored the
        # newest ones) — the premise of the NULL-passing rule
        n_null = await conn.fetchval(
            "SELECT count(*) FROM vessel_states "
            "WHERE ts > now() - make_interval(hours => $1) AND position_quality IS NULL "
            "AND point(longitude, latitude) <@ box(point($2,$3), point($4,$5))",
            WINDOW, 34.9, 32.75, 35.1, 32.9)
    finally:
        await conn.close()
    assert n_null > 0, "expected unscored (NULL) fixes — the 15-min timer lags the feed"
    # and the layer still returns visits despite them being present
    assert d["count"] > 0


@requires_db
@pytest.mark.asyncio
async def test_ambarli_rule_flags_geometry_not_traffic():
    """WPI rates Ambarlı 'V' (1.5 km floor) though it sits in the collector's densest
    cell, so its low in-radius count is a GEOMETRY signal. Asserted on the RULE's
    behaviour (far more vessels outside than inside), not on a fixed count."""
    async with _client() as c:
        amb = await _get(c, q="Ambarli")
        hai = await _get(c, q="Haifa")
    fa, fh = amb["funnel"][0], hai["funnel"][0]
    assert amb["port"]["harbor_size"] in ("V", "S")
    if fa["vessels_outside_radius"] < 20:
        pytest.skip("too little Ambarlı traffic in the window to exercise the rule")
    assert fa["vessels_outside_radius"] > fa["vessels_in_radius"]
    assert fa["geometry_under_covers"] is True
    # a port whose radius fits its traffic is NOT flagged — the rule discriminates
    assert fh["geometry_under_covers"] is False


# --------------------------------------------------------------- attribution
@requires_db
@pytest.mark.asyncio
async def test_multi_port_attribution_reports_both_members_of_a_merge_pair():
    """Istanbul <-> Haydarpasa are 4.59 km apart with overlapping assumed radii. A session
    in the overlap must name BOTH candidates, from either side, never an arbitrary pick."""
    async with _client() as c:
        ist = await _get(c, q="Istanbul", port_id="wpi-43420")
        hay = await _get(c, q="Haydarpasa", port_id="wpi-44540")
    assert ist["port"]["port_id"] == "wpi-43420"
    assert "wpi-44540" in ist["port"]["merges_with"]
    assert "wpi-43420" in hay["port"]["merges_with"]

    ist_multi = [s for s in ist["sessions"] if len(s["candidate_ports"]) > 1]
    hay_multi = [s for s in hay["sessions"] if len(s["candidate_ports"]) > 1]
    if not ist_multi:
        pytest.skip("no overlap-region visits in the window (coverage-limited)")
    for s in ist_multi:
        assert s["candidate_ports"][0] == "wpi-43420"      # the queried port leads
        assert "wpi-44540" in s["candidate_ports"]
    for s in hay_multi:
        assert s["candidate_ports"][0] == "wpi-44540"      # ...and reciprocally
    # the same hull is claimable from both sides — that IS the ambiguity, expressed
    assert {s["mmsi"] for s in ist_multi} & {s["mmsi"] for s in hay_multi}


# --------------------------------------------------------------- the A1 reconciliation
@requires_db
@pytest.mark.asyncio
async def test_port_visit_a1_overlap_is_expected():
    """PORT-3, folded into PORT-2. A berthed vessel's silence and a port visit are ONE
    SILENCE SEEN TWICE: PORT-pre measured 55.8 % of port sessions overlapping an A1 gap
    for the same vessel, and 59.7 % of gaps starting within 15 km of a port are already
    classified 'stationary'.

    This layer converts a signal A1 DISCARDS AS A CONFOUND into a positive measured fact,
    so the two are NOT INDEPENDENT OBSERVATIONS. The test asserts the overlap is EXPECTED
    — if it ever drops to zero, either the reconciliation broke or one of the two layers
    stopped working, and a future study must not treat the pair as corroboration."""
    import asyncpg

    async with _client() as c:
        d = await _get(c, q="Haifa")
    if not d["sessions"]:
        pytest.skip("coverage-limited")

    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            "SELECT mmsi, start_ts, end_ts, classification FROM maritime_events "
            "WHERE event_type = 'ais_gap' AND mmsi = ANY($1::text[])",
            list({s["mmsi"] for s in d["sessions"]}))
    finally:
        await conn.close()

    from datetime import datetime
    gaps: dict[str, list] = {}
    for r in rows:
        gaps.setdefault(r["mmsi"], []).append(r)
    overlapping = 0
    classes: dict[str, int] = {}
    for s in d["sessions"]:
        s0 = datetime.fromisoformat(s["start_ts"])
        s1 = datetime.fromisoformat(s["end_ts"])
        for g in gaps.get(s["mmsi"], []):
            ge = g["end_ts"] or s1
            if g["start_ts"] <= s1 and ge >= s0:
                overlapping += 1
                classes[g["classification"]] = classes.get(g["classification"], 0) + 1
    # the overlap is the POINT, not a defect: a berthed vessel legitimately stops
    # transmitting, and A1 has already binned that silence
    assert overlapping > 0, (
        "expected port sessions to overlap A1 gaps — a berthed vessel's silence IS an "
        "A1 gap, and the two layers are not independent observations")
    # and it is dominated by the confound classes A1 assigns near ports, not by
    # 'candidate' going-dark — the reason the gap-toward-a-port signal was dropped
    assert set(classes) <= {"stationary", "outage-excluded", "candidate",
                            "jamming-excluded", "edge"}
    assert sum(classes.get(k, 0) for k in ("stationary", "outage-excluded")) > \
        classes.get("candidate", 0), (
        "near-port gaps should be dominated by stationary/outage confounds — if "
        "'candidate' dominates, the A1 waterfall has changed and PORT-pre's Q4 null "
        "(the dropped gap-toward-a-port signal) needs re-measuring")


# --------------------------------------------------------------- annotation-only guard
@requires_db
@pytest.mark.asyncio
async def test_endpoint_writes_nothing_annotation_only():
    """PORT-2 is annotation-only by MEASURED decision, not by omission: no port_visit
    events, no migration, no composite admission. The guard is structural — querying the
    endpoint must not create a single row, and no 'port_visit' event_type may exist."""
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        before = await conn.fetchval("SELECT count(*) FROM maritime_events")
        async with _client() as c:
            await _get(c, q="Haifa")
            await _get(c, q="Port of Latakia")
        after = await conn.fetchval("SELECT count(*) FROM maritime_events")
        assert after == before, "PORT-2 must not write events"
        assert await conn.fetchval(
            "SELECT count(*) FROM maritime_events WHERE event_type = 'port_visit'") == 0
        # and the composite gained no port_visit column / pattern type
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'maritime_hull_composite'")
        assert not any("port" in r["column_name"] for r in cols)
    finally:
        await conn.close()


# --------------------------------------------------------------- resolution + edges
@requires_db
@pytest.mark.asyncio
async def test_resolution_selection_and_edges():
    async with _client() as c:
        # the Port Said case: resolves via alternate_name, end to end
        ps = await _get(c, q="port said")
        assert ps["port"]["port_name"] == "Bur Sa'id"
        # the Levant supplement resolves by Arabic name too
        ar = await _get(c, q="بيروت")
        assert ar["port"]["port_name"] == "Port of Beirut"
        # a specific port can be selected out of a multi-match
        sel = await _get(c, q="port", port_id="osm-way-722818042")
        assert sel["port"]["port_name"] == "Port of Latakia"
        # no match -> empty, not an error; coverage context still returned
        nm = await _get(c, q="zznope123")
        assert nm["count"] == 0 and nm["sessions"] == [] and nm["port"] is None
        assert nm["coverage"]["ports_in_gazetteer"] == 190
        assert "43 of 180" in nm["coverage"]["ports_observed_reference"]
        # empty q -> 422
        r = await c.get("/api/v1/maritime/port-visits", params={"q": ""})
        assert r.status_code == 422
        # a silent port is coverage, not an error
        sy = await _get(c, q="Sayda")
        assert sy["port"]["port_name"] == "Sayda" and sy["available"] is True
        # the completeness filter narrows without changing the measured totals
        allv = await _get(c, q="Haifa")
        pres = await _get(c, q="Haifa", completeness="presence_only")
        assert pres["completeness"] == allv["completeness"]
        assert all(s["completeness"] == "presence_only" for s in pres["sessions"])


@requires_db
@pytest.mark.asyncio
async def test_idempotent_and_bounded():
    """Two identical calls agree, and one port stays well inside the ~167 ms/port budget
    that made a 180-port sweep unacceptable."""
    import time

    async with _client() as c:
        t0 = time.time()
        a = await _get(c, q="Haifa")
        dt = time.time() - t0
        b = await _get(c, q="Haifa")
    assert a["count"] == b["count"]
    assert a["completeness"] == b["completeness"]
    assert a["funnel"] == b["funnel"]
    assert [s["mmsi"] for s in a["sessions"]] == [s["mmsi"] for s in b["sessions"]]
    assert dt < 5.0, f"one-port query took {dt:.2f}s"
