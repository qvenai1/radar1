"""MT-DETECT-1 — which detector may read which track, and the one that must never.

⛔ THE DEFECT THIS GUARDS IS NOT A CRASH, IT IS A TRUE-LOOKING FALSE CLAIM. Pointing the gap
detector at MarineTraffic does not fail: the query runs, rows come back, and every one of
them says "this vessel stopped transmitting" when the truth is "our scraper did not observe
it". Nothing downstream can tell the difference — the event is shaped correctly, enters the
composite, and reaches a subscriber as an investigation lead.

So the tests here are mostly about REFUSALS, and about the two structural guards that make a
second source safe: the composite must not admit unstitched hulls, and a per-source
recompute must not delete another source's rows.
"""

from __future__ import annotations

import os
import re

import pytest
from dotenv import load_dotenv

load_dotenv()

from backend import track_source as TS  # noqa: E402

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


# =========================================================================================
# THE REFUSAL THAT MATTERS MOST
# =========================================================================================

def test_the_gap_detector_may_never_read_marinetraffic():
    """⛔ A GAP MEANS SOMETHING ELSE IN A SCRAPED SERIES. A1's entire contract is confound
    removal against OUR OWN receivers — `outage-excluded` is our collector being down,
    `edge` is our coverage mask, `candidate` is "underway in good coverage and stopped
    reporting". MarineTraffic is a scraped website: a hole means our scraper did not see the
    vessel, which folds in MT's aggregation coverage, MT's rate limiting and our polling.

    Measured: max inter-fix gap on MT is 1,403 minutes (23.4 h) for one ship_id. Fed to A1
    that is a 23-hour "went dark" candidate about a vessel that may have been transmitting
    the whole time."""
    assert TS.allowed("ais_gap", TS.MT) is False
    assert TS.sources_for("ais_gap") == [TS.AIS]

    why = TS.refusal("ais_gap", TS.MT)
    assert why and "scraper" in why.lower(), "the refusal must state the SEMANTIC reason"


def test_the_gap_refusal_is_semantic_not_merely_a_missing_field():
    """⛔ THE DISTINCTION IS THE POINT. If the refusal were only about `nav_status`, someone
    would 'fix' it by supplying a default and re-enable a detector whose claim still does not
    hold. Adding every field MT lacks must NOT unblock it."""
    patched = dict(TS.FIELDS)
    patched[TS.MT] = TS.FIELDS[TS.AIS]          # pretend MT carries everything
    original, TS.FIELDS = TS.FIELDS, patched
    try:
        assert TS.allowed("ais_gap", TS.MT) is False, \
            "a semantic refusal must survive the fields being satisfied"
    finally:
        TS.FIELDS = original


def test_draught_delta_is_refused_on_data_not_meaning():
    """The other kind of refusal, and it reads differently: nothing to difference."""
    assert TS.allowed("draught_delta", TS.MT) is False
    why = TS.refusal("draught_delta", TS.MT)
    assert "draught" in why and "scraper" not in why.lower()


# =========================================================================================
# WHAT IS ADMITTED
# =========================================================================================

def test_which_detectors_survive_the_source_change():
    """⛔ THE GATE WAS WRONG AT FIRST FOR THREE OF FOUR, and the correction is worth more
    than the original. Their `requires` sets had been written from what each detector
    CONCEPTUALLY needs; reading the actual SQL showed otherwise:

        zone_dwell       reads implied_speed_kn, position_quality, ship_type
        gnss_anomaly     reads implied_speed_kn, position_quality
        identity_change  groups by (imo, mmsi) — its signal IS the IMO-anchored re-flag

    A gate written from intent instead of from the code admits a detector to a source it
    cannot actually read, and it keeps its name and event_type while losing the claim it is
    named for.

    ⚠ AND THE ANSWER THEN CHANGED AGAIN, ONCE THE BLOCKER WAS UNDERSTOOD. zone_dwell and
    gnss_anomaly were refused not by MarineTraffic's DATA but by a missing PREPROCESSING
    stage: implied_speed_kn and position_quality are written by the quality tagger, which
    had only ever run over vessel_states. MT-DETECT-2 (migration 045) taught it
    `--track marinetraffic`, and both were admitted — a fixable gap, unlike the three that
    remain refused below."""
    for d in ("rendezvous", "zone_dwell", "gnss_anomaly"):
        assert TS.allowed(d, TS.MT) is True, f"{d} should read MT after MT-DETECT-2"
    # still refused, and for reasons no preprocessing can remove
    for d in ("ais_gap", "identity_change", "draught_delta"):
        assert TS.allowed(d, TS.MT) is False


def test_the_preprocessing_blocker_was_removed_not_waived():
    """⛔ THE DISTINCTION THAT MATTERS: the two were unblocked because the missing fields are
    now really there (migration 045 columns + the tagger's --track flag), NOT because the
    requirement was relaxed. Their `requires` sets must still name what their SQL reads."""
    for d in ("zone_dwell", "gnss_anomaly"):
        assert "implied_speed_kn" in TS.GATES[d].requires
        assert "position_quality" in TS.GATES[d].requires
    for f in ("implied_speed_kn", "position_quality"):
        assert f in TS.FIELDS[TS.MT], "migration 045 added the column; declare it"


def test_identity_change_is_blocked_on_its_own_anchor():
    """Its claim is "same hull, new MMSI" — anchored on the IMO, which MT does not carry
    (measured 0 of 3,612 vessels)."""
    assert "imo" in TS.refusal("identity_change", TS.MT)


def test_every_detector_may_always_read_the_original_source():
    for d in TS.GATES:
        assert TS.allowed(d, TS.AIS) is True, f"{d} lost its own source"


def test_an_unregistered_detector_fails_closed():
    """⛔ A new detector must OPT IN after someone has thought about whether its claim
    survives — never default into a second source."""
    assert TS.allowed("some_future_detector", TS.MT) is False
    assert TS.sources_for("some_future_detector") == [TS.AIS]
    assert "not registered" in TS.refusal("some_future_detector", TS.MT)


def test_marinetraffic_is_declared_without_the_fields_it_lacks():
    """The field table is the measured record; a wrong entry here silently re-admits a
    detector. mmsi/imo were 100 % NULL over 24 h, nav_status and draught 0 % populated, and
    implied_speed_kn/position_quality are tagger output that never ran for MT."""
    for absent in ("mmsi", "imo", "nav_status", "draught"):
        assert absent not in TS.FIELDS[TS.MT], f"{absent} is NOT carried by MT (measured)"
    for present in ("latitude", "longitude", "speed", "name", "flag"):
        assert present in TS.FIELDS[TS.MT]


# =========================================================================================
# THE TWO STRUCTURAL GUARDS
# =========================================================================================

@requires_db
@pytest.mark.asyncio
async def test_an_mt_event_never_reaches_the_hull_composite():
    """⛔ DETONATED, NOT ASSERTED. `maritime_hull_events` derives hull =
    COALESCE(imo, vessel_identity.imo, mmsi) — all three NULL for an MT row. Without the
    explicit `source = 'aisstream'` guard (migration 043) every MT event would collapse into
    ONE bogus `hull = NULL` group and be counted as a single multi-pattern vessel."""
    import asyncpg
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    try:
        before = await c.fetchval("SELECT count(*) FROM maritime_hull_composite")
        await c.execute(
            "INSERT INTO maritime_events (event_type, ship_id, source, start_ts, end_ts,"
            " classification, method) VALUES ('rendezvous','SHIP_X','marinetraffic',"
            " now(), now(), 'candidate', 'test')")
        assert await c.fetchval(
            "SELECT count(*) FROM maritime_hull_composite WHERE hull IS NULL") == 0
        assert await c.fetchval("SELECT count(*) FROM maritime_hull_composite") == before
    finally:
        await tr.rollback()
        await c.close()


@requires_db
@pytest.mark.asyncio
async def test_exactly_one_identifier_per_event():
    """An event can never be keyless (unaddressable) or double-keyed (ambiguous about which
    identifier space it lives in).

    ⚠ THE TWO INVALID CASES ARE CAUGHT BY DIFFERENT CONSTRAINTS, which is worth pinning
    because only one of them is the CHECK that was written for the job:

      both NULL -> NotNullViolation on the GENERATED `vessel_key`. It is COALESCE(mmsi,
                   ship_id) and sits in the primary key, so it rejects the row before
                   `maritime_events_one_key_ck` is ever evaluated.
      both set  -> CheckViolation on `maritime_events_one_key_ck`, as intended.

    Asserting a single exception type here would have failed while the protection worked."""
    import asyncpg
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    try:
        cases = [("NULL", "NULL", asyncpg.exceptions.NotNullViolationError),
                 ("'1'", "'2'", asyncpg.exceptions.CheckViolationError)]
        for mmsi, ship, exc in cases:
            # ⚠ A savepoint per case: the first rejection aborts the enclosing transaction,
            # so without one the SECOND case fails with InFailedSQLTransactionError and the
            # test would pass or fail for a reason unrelated to the constraint.
            with pytest.raises(exc):
                async with c.transaction():
                    await c.execute(
                        f"INSERT INTO maritime_events (event_type, mmsi, ship_id, source,"
                        f" start_ts, end_ts, classification, method) VALUES ('rendezvous',"
                        f" {mmsi}, {ship}, 'aisstream', now(), now(), 'candidate', 't')")
    finally:
        await tr.rollback()
        await c.close()


def test_a_per_source_recompute_cannot_delete_another_sources_rows():
    """⛔ THE MOST DESTRUCTIVE FAILURE AVAILABLE HERE, and it is one forgotten clause away.
    The detector recomputes by DELETE-then-INSERT. Unscoped, a MarineTraffic run would wipe
    every AIS-derived rendezvous in the table — 57,801 events at the time of writing."""
    src = open("scripts/detect_rendezvous.py").read()
    m = re.search(r"DELETE FROM maritime_events WHERE event_type='rendezvous'[^\"]*", src)
    assert m, "the recompute DELETE is no longer where this test can check it"
    assert "AND source=$1" in m.group(0), \
        "the recompute DELETE must be scoped to its own source"


# =========================================================================================
# THE INCIDENT — a recompute that deleted more than it could rebuild
# =========================================================================================

def test_the_recompute_delete_is_scoped_to_the_window_it_scanned():
    """⛔ REGRESSION ON REAL DATA LOSS. Adding `--since-days` turned this detector into a
    recompute-replace whose DELETE was WIDER than its scan.

    The AIS feed has been frozen since 2026-08-05. Once it aged past the 7-day default the
    scan matched no fixes and produced no events — and the unscoped DELETE removed all 53
    existing AIS rendezvous rows. Caught in the timer's own log:

        16:20  FUNNEL: pairs=113 -> encounters=80 -> stored candidates=53
        16:40  FUNNEL: pairs=0   -> encounters=0  -> stored candidates=0
        16:40  stored 0 rendezvous candidates; maritime_events rendezvous rows: 0

    THE RULE: a recompute may only delete what it could have recomputed. Narrowing the scan
    must narrow the delete with it, or the run becomes a truncate the moment the data moves
    out of range — failing OPEN, in the most destructive way available here."""
    src = open("scripts/detect_rendezvous.py").read()
    # ⚠ Matched to the end of the execute() CALL, not to the first closing quote: the
    # statement is split across adjacent string literals, and a regex that stopped at the
    # first `"` saw only half of it and reported a missing window that was actually there.
    m = re.search(r"DELETE FROM maritime_events WHERE event_type='rendezvous'.*?\)\n",
                  src, re.S)
    assert m, "the recompute DELETE moved — this guard can no longer see it"
    stmt = m.group(0)
    assert "AND source=$1" in stmt, "must not delete another source's rows"
    assert "make_interval(days => $2)" in stmt, \
        "must not delete events outside the window it scanned"


def test_the_scan_and_the_delete_share_one_window_parameter():
    """The two must be driven by the SAME value. Two independent windows would drift, and
    the drift is silent until it deletes something."""
    import importlib.util
    import sys
    sys.argv = ["x"]
    spec = importlib.util.spec_from_file_location("_d", "scripts/detect_rendezvous.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for q in (mod.PAIR_SQL, mod.FIX_SQL):
        assert "make_interval(days =>" in q, "the scan must be windowed"
    assert isinstance(mod.DEFAULT_SINCE_DAYS, int) and mod.DEFAULT_SINCE_DAYS > 0


def test_every_windowed_detector_scopes_its_recompute_delete():
    """⛔ THE BUG CLASS, not the single bug. A recompute-replace whose DELETE is wider than
    its scan becomes a TRUNCATE the moment the data moves out of range. It cost 53
    rendezvous events on 2026-08-15, and the same shape was latent in zone_dwell (windowed
    by --hours since it was written, deleting unscoped).

    Each detector below is checked against the rule that fits it: a WINDOWED scan must
    window its delete; an UNBOUNDED scan may delete its whole source."""
    windowed = {
        "scripts/detect_rendezvous.py": ("rendezvous", "make_interval(days => $2)"),
        "scripts/detect_zone_dwell.py": ("zone_dwell", "make_interval(hours => $1)"),
    }
    for path, (event, window) in windowed.items():
        src = open(path).read()
        m = re.search(rf"DELETE FROM maritime_events WHERE event_type='{event}'.*?\)\n",
                      src, re.S)
        assert m, f"{path}: the recompute DELETE moved"
        assert window in m.group(0), \
            f"{path}: a windowed scan must window its DELETE — see the 2026-08-15 incident"

    # unbounded scan -> a full per-source delete is exactly what it can rebuild
    src = open("scripts/detect_identity_change.py").read()
    m = re.search(r"DELETE FROM maritime_events WHERE event_type='identity_change'.*?\)\n",
                  src, re.S)
    assert m and "source=" in m.group(0), "must at least be scoped to its own source"


def test_no_detector_still_targets_the_pre_043_primary_key():
    """⛔ REGRESSION ON A LIVE OUTAGE I CAUSED. Migration 043 moved the primary key from
    (event_type, mmsi, start_ts) to (event_type, source, vessel_key, start_ts). Every
    detector's UPSERT still named the old target, so all of them began failing with

        no unique or exclusion constraint matching the ON CONFLICT specification

    and four of five timers went to exit=1 — a whole analysis layer silently dead while the
    migration itself reported success. Nothing was lost (each run is transactional), but
    nothing was written either.

    ⚠ The lesson is that changing a PRIMARY KEY is an API change for every writer, and the
    migration that makes it must be paired with a sweep of the writers."""
    import glob
    stale = [p for p in glob.glob("scripts/*.py")
             if "ON CONFLICT (event_type, mmsi, start_ts)" in open(p).read()]
    assert not stale, f"these still target the pre-043 key: {stale}"


def test_every_event_writer_names_the_current_conflict_target():
    """The complement: every writer that upserts into maritime_events must name the key that
    actually exists, so a future key change fails here rather than in a timer at 03:00."""
    import glob
    writers = [p for p in glob.glob("scripts/*.py")
               if "INSERT INTO maritime_events" in open(p).read()
               and "ON CONFLICT" in open(p).read()]
    assert writers, "no event writers found — this guard has lost its subject"
    for p in writers:
        assert "ON CONFLICT (event_type, source, vessel_key, start_ts)" in open(p).read(), \
            f"{p} does not name the current primary key"


def test_every_event_writer_sets_source_and_the_right_key_column():
    """⛔ REGRESSION ON REAL CONTAMINATION. detect_zone_dwell and detect_gnss_anomaly were
    pointed at the MarineTraffic track but their UPSERTs still named neither `source` nor
    `ship_id`. The bound key therefore landed in `mmsi` and `source` took its column DEFAULT
    of 'aisstream', so 3,210 gnss_anomaly and 33 zone_dwell MT events were filed as AIS —
    and the hull composite moved 319 -> 320.

    ⚠ THE MIGRATION-043 GUARD WAS NEVER WRONG. It filters on `source`, and the source was a
    lie. A structural guard is only as good as the honesty of the column it reads, which is
    why this test checks the WRITERS rather than re-checking the guard."""
    import glob
    for p in glob.glob("scripts/detect_*.py"):
        src = open(p).read()
        if "INSERT INTO maritime_events" not in src:
            continue
        if "TS.SOURCES" not in src:
            continue                      # AIS-only writer; source DEFAULT is correct
        assert "ship_id, source" in src, \
            f"{p} writes to a second track but never names ship_id/source"
        assert "'aisstream' THEN" in src, \
            f"{p} must route the key to mmsi or ship_id by source"


# =========================================================================================
# MT-DETECT-3 — the single-source alert family
# =========================================================================================

def test_the_mt_family_is_the_weakest_basis_and_says_so():
    """⛔ IT MUST NEVER INHERIT THE COMPOSITE'S WEIGHT. The composite claims "≥2 patterns
    coincide on one STITCHED hull". An MT event has ONE pattern and no stitchable identity,
    so it is a strictly weaker observation and its basis must say all three limits."""
    from backend.alerts import SEVERITY_BASIS
    b = SEVERITY_BASIS["maritime_single"]
    assert "one source" in b and "no cross-pattern corroboration" in b
    assert "cannot be joined" in b


def test_the_mt_family_is_fixed_low_and_therefore_never_interrupts():
    """LOW is the honest rank for an uncorroborated single-source lead, and it is what
    routes it to the digest instead of an instant message."""
    from backend.models import Alert, AlertEntityRef
    from backend.notify import policy
    a = Alert(domain="sea", family="maritime", signal_type="maritime_single",
              severity="low", severity_basis="b",
              entity_ref=AlertEntityRef(kind="vessel", id="123", live_key="123"))
    assert policy.route_for(a) == policy.DIGEST


def test_gnss_anomaly_is_excluded_from_the_mt_alert_surface():
    """⛔ MEASURED THE MOMENT IT WAS WIRED: 3,219 gnss_anomaly events across 673 vessels,
    against 38 zone_dwell (6 vessels) and 8 rendezvous (4). MT flags implausible positions
    at ~2x the AIS rate and `suspect` at ~107x — that difference is a property of
    MarineTraffic's aggregation, not of the vessels. Admitting it would have made 98.5 % of
    this family source noise dressed as vessel signal.

    ⚠ The events are not discarded, only kept out of the ALERT surface."""
    src = open("backend/routes/alerts.py").read()
    assert "AND e.event_type IN ('rendezvous', 'zone_dwell')" in src, \
        "the MT alert query must not admit gnss_anomaly"


def test_an_mt_alert_entity_can_actually_be_opened():
    """⛔ THE REASON /maritime/vessel GAINED ship_id. An alert whose entity has no
    destination is worse than no alert: it looks actionable and is not. Before MT-DETECT-3
    every MT event's entity returned 404."""
    src = open("backend/routes/maritime.py").read()
    assert "async def _maritime_vessel_by_ship_id" in src
    assert "pass exactly one of mmsi or ship_id" in src, \
        "two identifier namespaces must not be silently merged"


def test_the_mt_family_has_an_identity_rule():
    """The notify drift gate enforces this at import, but assert it here too so the reason
    is stated: a family without a rule falls to the generic identity, which cannot tell a
    new observation from a re-read."""
    from backend.notify import identity
    assert "maritime_single" in identity._RULES
    from backend.notify import render
    assert "maritime_single" in render.SHORT_CAVEAT
    assert "maritime_single" in render.SIGNAL_AR
