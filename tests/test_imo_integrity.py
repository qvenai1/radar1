"""IMO-1 identity-integrity axis — the pure detector + the DB CHECK constraints.

Proves the CONTRACT that makes the axis honest: the ISO check digit is computed
correctly (capstone 9895915 passes; the IMO-pre fail samples fail), each malformed
class is separated, a clean re-flag is VALID (the composite's intended stitching)
while a time-overlap collision is CONTESTED (the signal A3 silences), dimension
disagreement only corroborates a collision (never fires alone), worst-verdict wins,
the module is deterministic, and migration 035's CHECK constraints reject every
inconsistent row shape — including the empty-array cardinality() case.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.imo_integrity import (
    CHECKSUM_FAIL,
    CONTESTED,
    FLAG_CHECKSUM_FAIL,
    FLAG_DIMENSION_CONFLICT,
    FLAG_MMSI_COLLISION,
    FLAG_MMSI_MULTI_IMO,
    FLAG_NON_NUMERIC,
    FLAG_PLACEHOLDER,
    FLAG_REFLAG_CLEAN,
    FLAG_WRONG_LENGTH,
    MALFORMED,
    METHOD,
    VALID,
    Member,
    checksum_ok,
    classify_identity,
    classify_shared,
    classify_value,
)


def _t(h):
    return datetime(2026, 7, 20, tzinfo=timezone.utc) + timedelta(hours=h)


# --- check digit --------------------------------------------------------------

@pytest.mark.parametrize("imo", ["9895915", "1062160", "9074729", "9321483"])
def test_checksum_pass(imo):
    assert checksum_ok(imo) is True
    assert classify_value(imo) == (VALID, set())


@pytest.mark.parametrize("imo", ["9631220", "9789930", "9451544", "9967284"])
def test_checksum_fail(imo):
    # IMO-pre's real fail samples: well-formed 7-digit, wrong check digit.
    assert checksum_ok(imo) is False
    assert classify_value(imo) == (CHECKSUM_FAIL, {FLAG_CHECKSUM_FAIL})


def test_capstone_is_valid():
    assert classify_identity("9895915").imo_quality == VALID


# --- malformed classes (each a distinct flag) --------------------------------

@pytest.mark.parametrize("imo", ["167772160", "65535", "80009064", "1032141000", "999999"])
def test_wrong_length(imo):
    # decode artifacts (0x0A000000, 0xFFFF, 8- and 10-digit over-reads, 6-digit).
    q, flags = classify_value(imo)
    assert q == MALFORMED and flags == {FLAG_WRONG_LENGTH}


@pytest.mark.parametrize("imo", ["ABC1234", "abcdefg", "98959X5"])
def test_non_numeric(imo):
    q, flags = classify_value(imo)
    assert q == MALFORMED and flags == {FLAG_NON_NUMERIC}


@pytest.mark.parametrize("imo", ["1234567", "1111111", "9999999"])
def test_placeholder(imo):
    # in-range placeholder / repeated-digit patterns. "0000000" (int 0) is
    # out-of-range -> wrong_length, and is the null sentinel the parser already nulls.
    q, flags = classify_value(imo)
    assert q == MALFORMED and flags == {FLAG_PLACEHOLDER}


def test_leading_zero_out_of_range_is_wrong_length():
    # 7 chars, numeric, but int < 1_000_000 (no real IMO has a leading zero).
    q, flags = classify_value("0999999")
    assert q == MALFORMED and flags == {FLAG_WRONG_LENGTH}


def test_null_and_empty_are_unchecked():
    assert classify_value(None) == (None, set())
    assert classify_value("") == (None, set())
    r = classify_identity(None)
    assert r.imo_quality is None and r.imo_flags == () and r.method is None


# --- shared IMO: clean re-flag vs collision ----------------------------------

def _reflag_members():
    # the real 9232931 MONOLIT/NARCISSUS case: non-overlapping windows, same hull.
    return [
        Member("273128810", _t(0), _t(48), "MONOLIT", 150.0, 20.0),
        Member("352002661", _t(60), _t(100), "NARCISSUS", 150.0, 20.0),
    ]


def test_clean_reflag_is_valid_benign():
    q, flags = classify_shared(_reflag_members())
    assert q == VALID and flags == {FLAG_REFLAG_CLEAN}
    r = classify_identity("9232931", shared=_reflag_members())
    assert r.imo_quality == VALID and r.imo_flags == (FLAG_REFLAG_CLEAN,)


def test_time_overlap_is_collision_contested():
    members = [
        Member("111111111", _t(0), _t(80), "SHIP A", 150.0, 20.0),
        Member("222222222", _t(20), _t(100), "SHIP B", 150.0, 20.0),   # overlaps A
    ]
    q, flags = classify_shared(members)
    assert q == CONTESTED and flags == {FLAG_MMSI_COLLISION}


def test_dimension_conflict_only_corroborates_collision():
    # overlap + big length gap -> collision WITH dimension_conflict.
    coll = [
        Member("1", _t(0), _t(80), "A", 150.0, 20.0),
        Member("2", _t(20), _t(100), "B", 260.0, 32.0),
    ]
    q, flags = classify_shared(coll)
    assert q == CONTESTED and flags == {FLAG_MMSI_COLLISION, FLAG_DIMENSION_CONFLICT}

    # SAME big length gap but a CLEAN switch -> reflag_clean, NO dimension_conflict
    # (the weapon never fires alone — IMO-pre: under-determined threshold).
    clean = [
        Member("1", _t(0), _t(48), "A", 150.0, 20.0),
        Member("2", _t(60), _t(100), "B", 260.0, 32.0),
    ]
    q2, flags2 = classify_shared(clean)
    assert q2 == VALID and flags2 == {FLAG_REFLAG_CLEAN}
    assert FLAG_DIMENSION_CONFLICT not in flags2


def test_single_member_group_says_nothing():
    assert classify_shared([Member("1", _t(0), _t(10))]) == (None, set())
    assert classify_shared([]) == (None, set())


def test_missing_windows_cannot_prove_overlap():
    # no time ranges -> cannot prove a collision -> treated as a clean re-flag.
    members = [Member("1", None, None, "A", 150.0, 20.0), Member("2", None, None, "B", 150.0, 20.0)]
    q, flags = classify_shared(members)
    assert q == VALID and flags == {FLAG_REFLAG_CLEAN}


# --- worst-verdict composition + latent guard --------------------------------

def test_worst_verdict_wins():
    # a malformed IMO that is ALSO cleanly shared -> malformed beats valid; union flags.
    r = classify_identity("65535", shared=[
        Member("1", _t(0), _t(48), "A", 150.0, 20.0),
        Member("2", _t(60), _t(100), "B", 150.0, 20.0),
    ])
    assert r.imo_quality == MALFORMED
    assert set(r.imo_flags) == {FLAG_WRONG_LENGTH, FLAG_REFLAG_CLEAN}


def test_mmsi_multi_imo_latent_guard():
    r = classify_identity("9895915", mmsi_multi_imo=True)
    assert r.imo_quality == CONTESTED and FLAG_MMSI_MULTI_IMO in r.imo_flags


def test_valid_never_carries_an_anomaly_flag():
    # invariant mirrored by the DB CHECK: a valid verdict keeps only benign flags.
    for imo in ["9895915", "1062160"]:
        r = classify_identity(imo)
        if r.imo_quality == VALID:
            assert set(r.imo_flags) <= {FLAG_REFLAG_CLEAN}


def test_determinism():
    args = ("9232931",)
    kw = {"shared": _reflag_members(), "mmsi_multi_imo": False}
    assert classify_identity(*args, **kw) == classify_identity(*args, **kw)
    assert classify_identity("9895915").method == METHOD


# --- migration 035 CHECK constraints (DB-backed; skips without a DB) ----------

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@requires_db
@pytest.mark.asyncio
async def test_check_constraints_reject_inconsistent_rows():
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        # a throwaway identity, always rolled back — never persisted.
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO vessel_identity (mmsi, station_type, imo) "
                "VALUES ('000000001', 'ship', '9895915')")

            bad = [
                ("valid + anomaly flag",
                 "imo_quality='valid', imo_flags=ARRAY['checksum_fail']"),
                ("non-valid + EMPTY flags (cardinality guard)",
                 "imo_quality='malformed', imo_flags=ARRAY[]::text[]"),
                ("non-valid + NULL flags",
                 "imo_quality='contested', imo_flags=NULL"),
                ("flag outside the closed set",
                 "imo_quality='malformed', imo_flags=ARRAY['bogus']"),
                ("verdict outside the closed set",
                 "imo_quality='weird', imo_flags=ARRAY['checksum_fail']"),
            ]
            for _label, setclause in bad:
                with pytest.raises(asyncpg.exceptions.CheckViolationError):
                    async with conn.transaction():   # savepoint
                        await conn.execute(
                            f"UPDATE vessel_identity SET {setclause} WHERE mmsi='000000001'")

            # accepted shapes
            for setclause in (
                "imo_quality='valid', imo_flags=NULL",
                "imo_quality='valid', imo_flags=ARRAY['reflag_clean']",
                "imo_quality='checksum_fail', imo_flags=ARRAY['checksum_fail']",
                "imo_quality=NULL, imo_flags=ARRAY['reflag_clean']",   # unchecked = unconstrained
            ):
                async with conn.transaction():
                    await conn.execute(
                        f"UPDATE vessel_identity SET {setclause} WHERE mmsi='000000001'")

            raise _Rollback()   # discard the throwaway row
    except _Rollback:
        pass
    finally:
        await conn.close()


@requires_db
@pytest.mark.asyncio
async def test_backfill_never_stamps_a_null_imo_row():
    # the ALWAYS-TRUE direction (live-robust): the runner never writes a verdict/
    # method onto a NULL-imo identity. The reverse (every imo-bearing row is stamped)
    # only holds immediately post-backfill — the feed is live, so fresh identities
    # legitimately sit unchecked until the next timer run.
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        stamped_null = await conn.fetchval(
            "SELECT count(*) FROM vessel_identity WHERE imo IS NULL AND imo_method IS NOT NULL")
        assert stamped_null == 0   # a NULL imo is never "checked"
        foreign_method = await conn.fetchval(
            "SELECT count(*) FROM vessel_identity "
            "WHERE imo_method IS NOT NULL AND imo_method <> $1", METHOD)
        assert foreign_method == 0   # every stamp is ours
    finally:
        await conn.close()


class _Rollback(Exception):
    """Sentinel to unwind the throwaway-row transaction without persisting."""
