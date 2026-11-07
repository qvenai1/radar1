"""Maritime layer G1 — GNSS position-quality classification (backend/gnss_anomaly).

Proves the two rules verified in the G0 assessment, each case traceable to a G0
finding: impossible kinematics (R1) with its 500 m dual-condition floor, inland
depth (R2) including the coherent-offset pattern R1 cannot see, worst-verdict
composition, first-fix handling, determinism/idempotency, and — guarded by a live
DATABASE_URL — that migration 027's CHECK constraints make an inconsistent verdict
unrepresentable. Rule functions are pure; only the constraint test touches the DB
(INSERT-then-rollback, nothing committed, no backfill).
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.gnss_anomaly import (
    FAST_CRAFT_ALLOWANCE_KN,
    FLAG_IMPOSSIBLE_SPEED,
    FLAG_ON_LAND,
    FLAG_TELEPORT_RETURN,
    IMPLAUSIBLE,
    INLAND_IMPLAUSIBLE_KM,
    INLAND_SUSPECT_KM,
    JUMP_MIN_DISTANCE_M,
    METHOD,
    PLAUSIBLE,
    QUALITY_CAVEAT,
    SOG_SENTINEL_KN,
    SPEED_HIGH_KN,
    SPEED_IMPOSSIBLE_KN,
    SPEED_SUSPECT_KN,
    SUSPECT,
    Fix,
    classify_fix,
    clean_sog,
    inland_verdict,
    segment_speed_kn,
    speed_verdict,
)

UTC = timezone.utc
BASE = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def _fix(lat, lon, sec):
    return Fix(BASE + timedelta(seconds=sec), lat, lon)


# --- constants are frozen at the G0-approved values --------------------------

def test_frozen_thresholds():
    assert (SPEED_IMPOSSIBLE_KN, SPEED_HIGH_KN, SPEED_SUSPECT_KN) == (100.0, 60.0, 40.0)
    assert FAST_CRAFT_ALLOWANCE_KN == 60.0
    assert JUMP_MIN_DISTANCE_M == 500.0
    assert (INLAND_IMPLAUSIBLE_KM, INLAND_SUSPECT_KM) == (5.0, 2.0)
    assert SOG_SENTINEL_KN == 102.2
    assert METHOD == "gnss-quality-v1"
    assert "not proof" in QUALITY_CAVEAT.lower() and "victim" in QUALITY_CAVEAT.lower()


# --- R1: impossible kinematics + the 500 m dual-condition floor (G0/V3) -------

def test_speed_over_100_is_impossible():
    v, f = speed_verdict(50_000.0, 3000.0, None, None, "cargo")   # 3000 kn, 50 km jump
    assert v == IMPLAUSIBLE and f == {FLAG_IMPOSSIBLE_SPEED}


def test_speed_60_to_100_is_implausible():
    v, f = speed_verdict(2000.0, 75.0, None, None, "cargo")
    assert v == IMPLAUSIBLE and f == {FLAG_IMPOSSIBLE_SPEED}


def test_speed_40_to_60_cargo_is_suspect_but_fast_craft_is_clean():
    # A cargo vessel at ~50 kn is suspect; a high-speed craft at the same speed is
    # legitimate (the 40–60 allowance) — G0 R3.
    v_cargo, f_cargo = speed_verdict(3000.0, 50.0, None, None, "cargo")
    assert v_cargo == SUSPECT and f_cargo == {FLAG_IMPOSSIBLE_SPEED}
    v_hsc, f_hsc = speed_verdict(3000.0, 50.0, None, None, "high_speed_craft")
    assert v_hsc == PLAUSIBLE and f_hsc == set()
    # ...but a fast craft ABOVE 60 kn is still flagged (the exemption caps at 60).
    v_hsc_fast, f_hsc_fast = speed_verdict(3000.0, 61.0, None, None, "high_speed_craft")
    assert v_hsc_fast == IMPLAUSIBLE and f_hsc_fast == {FLAG_IMPOSSIBLE_SPEED}


def test_short_dt_small_jump_killed_by_500m_floor():
    # ~50 kn implied but only a 300 m jump (GPS jitter at a short Δt) -> the dual
    # condition suppresses it. The same speed over >=500 m fires.
    assert speed_verdict(300.0, 50.0, None, None, "cargo") == (PLAUSIBLE, set())
    v, f = speed_verdict(600.0, 50.0, None, None, "cargo")
    assert v == SUSPECT and f == {FLAG_IMPOSSIBLE_SPEED}


def test_teleport_and_return_around_stable_position():
    # G0's cleanest signature: jump far AND snap back. Classify the middle fix.
    prev = _fix(34.0, 32.0, 0)
    cur = _fix(34.0, 33.0, 60)      # ~92 km east
    nxt = _fix(34.0, 32.0, 120)     # back to the start
    r = classify_fix(prev, cur, nxt, ship_type="cargo")
    assert r.position_quality == IMPLAUSIBLE
    assert set(r.anomaly_flags) == {FLAG_IMPOSSIBLE_SPEED, FLAG_TELEPORT_RETURN}


def test_single_jump_is_impossible_but_not_teleport_return():
    # Jump in, then continue normally (no snap-back) -> impossible_speed only.
    prev = _fix(34.0, 32.0, 0)
    cur = _fix(34.0, 33.0, 60)      # ~92 km jump in
    nxt = _fix(34.0, 33.001, 120)   # ~90 m onward -> tiny outgoing speed
    r = classify_fix(prev, cur, nxt, ship_type="cargo")
    assert r.position_quality == IMPLAUSIBLE
    assert set(r.anomaly_flags) == {FLAG_IMPOSSIBLE_SPEED}
    assert FLAG_TELEPORT_RETURN not in r.anomaly_flags


# --- R2: inland depth, including the coherent-offset pattern (G0/V1) ----------

def test_inland_bands():
    assert inland_verdict(None) == (PLAUSIBLE, set())          # at sea
    assert inland_verdict(0.3) == (PLAUSIBLE, set())           # quay / port
    assert inland_verdict(3.0) == (SUSPECT, {FLAG_ON_LAND})    # 2–5 km
    assert inland_verdict(19.0) == (IMPLAUSIBLE, {FLAG_ON_LAND})  # deep inland


def test_coherent_offset_flagged_by_R2_with_R1_silent():
    # The Lebanon cell (G0/V1): a connected track at a realistic ~10 kn, but the
    # whole track is displaced ~19 km inland. R1 sees nothing (speeds are normal);
    # R2 alone catches it. This is why the two rules are independent.
    prev = _fix(33.35, 35.40, 0)
    cur = _fix(33.35, 35.45, 900)     # ~4.6 km in 15 min ≈ 10 kn
    nxt = _fix(33.35, 35.50, 1800)
    r = classify_fix(prev, cur, nxt, ship_type="tanker", inland_km=19.0)
    assert r.position_quality == IMPLAUSIBLE
    assert set(r.anomaly_flags) == {FLAG_ON_LAND}
    assert FLAG_IMPOSSIBLE_SPEED not in r.anomaly_flags
    assert r.implied_speed_kn == pytest.approx(10.0, abs=1.0)   # realistic, R1 silent


def test_quay_position_is_clean():
    prev = _fix(32.80, 34.99, 0)
    cur = _fix(32.80, 34.995, 600)
    r = classify_fix(prev, cur, None, ship_type="cargo", inland_km=0.3)
    assert r.position_quality == PLAUSIBLE
    assert r.anomaly_flags == ()


# --- composition, first-fix, evidence, determinism ---------------------------

def test_worst_verdict_when_both_rules_fire():
    # R1 suspect (~48 kn cargo) AND R2 implausible (deep inland) -> worst wins
    # (implausible), flags are the UNION.
    prev = _fix(34.0, 32.0, 0)
    cur = _fix(34.0, 32.027, 100)     # ~2.5 km in 100 s ≈ 48 kn
    r = classify_fix(prev, cur, None, ship_type="cargo", inland_km=19.0)
    assert r.position_quality == IMPLAUSIBLE
    assert set(r.anomaly_flags) == {FLAG_IMPOSSIBLE_SPEED, FLAG_ON_LAND}


def test_first_fix_has_no_kinematics_only_R2():
    # prev is None (window start): no incoming segment, so R1 cannot fire even with
    # a far next fix; implied_speed_kn is None. Only R2 applies.
    cur = _fix(34.0, 32.0, 0)
    far_next = _fix(34.0, 33.0, 60)
    r_sea = classify_fix(None, cur, far_next, ship_type="cargo", inland_km=None)
    assert r_sea.position_quality == PLAUSIBLE
    assert r_sea.anomaly_flags == () and r_sea.implied_speed_kn is None
    r_land = classify_fix(None, cur, far_next, ship_type="cargo", inland_km=42.0)
    assert r_land.position_quality == IMPLAUSIBLE
    assert set(r_land.anomaly_flags) == {FLAG_ON_LAND}


def test_clean_fix_is_plausible_and_stamped():
    prev = _fix(34.0, 32.0, 0)
    cur = _fix(34.01, 32.01, 600)     # slow, at sea
    r = classify_fix(prev, cur, None, ship_type="cargo", inland_km=None)
    assert r.position_quality == PLAUSIBLE
    assert r.anomaly_flags == ()
    assert r.method == METHOD          # provenance stamped even when clean


def test_sentinel_sog_is_nulled_reported_sog_never_a_rule_input():
    assert clean_sog(SOG_SENTINEL_KN) is None
    assert clean_sog(102.3) is None
    assert clean_sog(1023.0) is None
    assert clean_sog(None) is None
    assert clean_sog(15.0) == 15.0
    # classify_fix takes NO reported-SOG parameter — SOG can never reach a rule.
    import inspect
    assert "sog" not in inspect.signature(classify_fix).parameters


def test_zero_or_backward_dt_yields_no_speed():
    a = _fix(34.0, 32.0, 100)
    b = _fix(34.0, 33.0, 100)         # same instant
    assert segment_speed_kn(a, b) == (None, None)
    c = _fix(34.0, 33.0, 50)          # earlier than a
    assert segment_speed_kn(a, c) == (None, None)


def test_determinism_and_idempotency():
    prev, cur, nxt = _fix(34.0, 32.0, 0), _fix(34.0, 33.0, 60), _fix(34.0, 32.0, 120)
    r1 = classify_fix(prev, cur, nxt, ship_type="cargo", inland_km=42.0)
    r2 = classify_fix(prev, cur, nxt, ship_type="cargo", inland_km=42.0)
    assert r1 == r2                                     # frozen dataclass equality
    assert r1.anomaly_flags == tuple(sorted(r1.anomaly_flags))   # deterministic order


# --- migration 027 CHECK constraints (DB-guarded; INSERT-then-rollback) -------

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

#: A far-future instant so the INSERT lands in a fresh, uncompressed chunk that
#: the surrounding transaction rolls back — no real data is ever touched.
DB_TS = datetime(2035, 1, 1, tzinfo=UTC)
_COLS = ("mmsi", "ts", "source", "position_quality", "anomaly_flags",
         "implied_speed_kn", "inland_km", "quality_method", "quality_computed_at")


async def _insert(conn, **vals):
    row = {"mmsi": "999999999", "ts": DB_TS, "source": "pytest-gnss",
           "position_quality": None, "anomaly_flags": None, "implied_speed_kn": None,
           "inland_km": None, "quality_method": METHOD, "quality_computed_at": DB_TS}
    row.update(vals)
    cols = ", ".join(_COLS)
    ph = ", ".join(f"${i + 1}" for i in range(len(_COLS)))
    await conn.execute(f"INSERT INTO vessel_states ({cols}) VALUES ({ph})",
                       *[row[c] for c in _COLS])


@requires_db
@pytest.mark.asyncio
async def test_valid_verdict_row_is_accepted():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        tx = conn.transaction()
        await tx.start()
        await _insert(conn, position_quality="implausible",
                      anomaly_flags=["impossible_speed", "on_land"], implied_speed_kn=3000.0)
        await tx.rollback()   # never commit — the row exists only inside the tx
    finally:
        await conn.close()


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("vals", [
    dict(position_quality="implausible", anomaly_flags=None),        # verdict, no reasons
    dict(position_quality="suspect", anomaly_flags=[]),              # empty == no reasons
    dict(position_quality="plausible", anomaly_flags=["on_land"]),   # reasons contradict verdict
    dict(position_quality="implausible", anomaly_flags=["bogus_flag"]),  # flag outside closed set
    dict(position_quality="weird", anomaly_flags=["on_land"]),       # verdict outside closed set
    dict(position_quality="implausible", anomaly_flags=["on_land"], implied_speed_kn=-1.0),  # neg evidence
])
async def test_check_constraints_reject_inconsistent_rows(vals):
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            async with conn.transaction():
                await _insert(conn, **vals)
    finally:
        await conn.close()
