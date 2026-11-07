"""IMO-2 consumers — the composite hull-key fallback, the annotation, and the latent
collision event (migration 036 + the writer).

Proves: a MALFORMED IMO no longer serves as a hull key (falls back to the MMSI, like
a NULL IMO), and a shared malformed value SPLITS into its real constituents instead
of false-merging; a CHECKSUM_FAIL IMO KEEPS its key (deliberate); reflag_clean
stitching is UNCHANGED (the regression that matters most); the collision path builds a
correctly-shaped imo_integrity event that enters the composite as a distinct pattern
type. The pure event-builder is unit-tested; the view behaviour is proven DB-backed
against synthetic rows in a rolled-back transaction (never persisted).
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.imo_integrity import (
    CONTESTED,
    Member,
    build_collision_event,
    collision_window,
    worst_quality,
)


def _t(h):
    return datetime(2026, 7, 20, tzinfo=timezone.utc) + timedelta(hours=h)


# --- pure: the collision event builder + helpers -----------------------------

def test_collision_window_finds_overlap():
    members = [Member("1", _t(0), _t(80)), Member("2", _t(20), _t(100))]
    assert collision_window(members) == (_t(20), _t(80))


def test_collision_window_none_for_clean_switch():
    members = [Member("1", _t(0), _t(48)), Member("2", _t(60), _t(100))]
    assert collision_window(members) is None


def test_build_collision_event_shape():
    members = [
        Member("222222222", _t(20), _t(100), "B", 260.0, 32.0),
        Member("111111111", _t(0), _t(80), "A", 150.0, 20.0),   # overlaps
    ]
    ev = build_collision_event("9321483", members)
    assert ev["event_type"] == "imo_integrity"
    assert ev["classification"] == "candidate"      # already in the CHECK set — no new value
    assert ev["imo"] == "9321483"
    assert ev["mmsi"] == "111111111"                # lexicographically-first representative
    assert ev["start_ts"] == _t(20) and ev["end_ts"] == _t(80)
    d = ev["detail"]
    assert d["integrity_type"] == "mmsi_collision"
    assert set(d["conflicting_mmsis"]) == {"111111111", "222222222"}
    assert d["dimension_conflict"] is True          # 150 vs 260 m


def test_build_collision_event_none_for_clean_reflag():
    members = [Member("1", _t(0), _t(48), "A", 150.0, 20.0),
               Member("2", _t(60), _t(100), "B", 150.0, 20.0)]
    assert build_collision_event("9232931", members) is None


def test_worst_quality_ordering():
    assert worst_quality(["valid", "checksum_fail", None]) == "checksum_fail"
    assert worst_quality(["malformed", "contested"]) == CONTESTED
    assert worst_quality([None, None]) is None
    assert worst_quality(["valid"]) == "valid"


# --- DB-backed: the view hull-key behaviour (synthetic, rolled back) ----------

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

# synthetic MMSIs unlikely to collide with real data
M1, M2, M3, M4, M5, M6 = ("900000001", "900000002", "900000003",
                          "900000004", "900000005", "900000006")


async def _hulls_for(conn, mmsis):
    """DISTINCT hull keys the view assigns to events whose via_mmsi is in `mmsis`."""
    rows = await conn.fetch(
        "SELECT DISTINCT hull FROM maritime_hull_events WHERE via_mmsi = ANY($1::text[])",
        mmsis)
    return sorted(r["hull"] for r in rows)


async def _ins_identity(conn, mmsi, imo, quality, flags):
    await conn.execute(
        "INSERT INTO vessel_identity (mmsi, station_type, imo, imo_quality, imo_flags, imo_method) "
        "VALUES ($1,'ship',$2,$3,$4,'imo-integrity-v1')",
        mmsi, imo, quality, flags)


async def _ins_gap(conn, mmsi, imo):
    await conn.execute(
        "INSERT INTO maritime_events (event_type, mmsi, start_ts, imo, classification, method) "
        "VALUES ('ais_gap',$1,$2,$3,'candidate','test')", mmsi, _t(0), imo)


class _Rollback(Exception):
    pass


async def _in_txn(fn):
    import asyncpg
    conn = await asyncpg.connect(DSN)
    result = {}
    try:
        async with conn.transaction():
            await fn(conn, result)
            raise _Rollback()
    except _Rollback:
        pass
    finally:
        await conn.close()
    return result


@requires_db
@pytest.mark.asyncio
async def test_malformed_imo_rekeys_to_mmsi():
    async def body(conn, out):
        await _ins_identity(conn, M1, "167772160", "malformed", ["wrong_length"])
        await _ins_gap(conn, M1, "167772160")
        out["hulls"] = await _hulls_for(conn, [M1])
    out = await _in_txn(body)
    assert out["hulls"] == [M1]              # keyed on the MMSI, NOT the malformed IMO


@requires_db
@pytest.mark.asyncio
async def test_shared_malformed_imo_splits_not_merges():
    async def body(conn, out):
        # two UNRELATED vessels both emitting the same decode artifact 65535
        await _ins_identity(conn, M1, "65535", "malformed", ["wrong_length"])
        await _ins_identity(conn, M2, "65535", "malformed", ["wrong_length"])
        await _ins_gap(conn, M1, "65535")
        await _ins_gap(conn, M2, "65535")
        out["hulls"] = await _hulls_for(conn, [M1, M2])
    out = await _in_txn(body)
    assert out["hulls"] == [M1, M2]          # SPLIT into two real hulls, not one phantom "65535"


@requires_db
@pytest.mark.asyncio
async def test_reflag_stitching_unchanged():
    async def body(conn, out):
        # two MMSIs sharing a VALID IMO (a clean re-flag) MUST still merge to one hull
        await _ins_identity(conn, M3, "9074729", "valid", ["reflag_clean"])
        await _ins_identity(conn, M4, "9074729", "valid", ["reflag_clean"])
        await _ins_gap(conn, M3, "9074729")
        await _ins_gap(conn, M4, "9074729")
        out["hulls"] = await _hulls_for(conn, [M3, M4])
    out = await _in_txn(body)
    assert out["hulls"] == ["9074729"]       # ONE merged hull — stitching intact


@requires_db
@pytest.mark.asyncio
async def test_checksum_fail_keeps_its_key():
    async def body(conn, out):
        await _ins_identity(conn, M5, "9631220", "checksum_fail", ["checksum_fail"])
        await _ins_gap(conn, M5, "9631220")
        out["hulls"] = await _hulls_for(conn, [M5])
    out = await _in_txn(body)
    assert out["hulls"] == ["9631220"]       # a well-formed-but-bad-check-digit IMO is kept


@requires_db
@pytest.mark.asyncio
async def test_collision_event_enters_composite_as_pattern_type():
    async def body(conn, out):
        # a contested IMO with a gap AND an imo_integrity collision event -> 2 pattern types
        await _ins_identity(conn, M6, "9321483", "contested", ["mmsi_collision"])
        await _ins_gap(conn, M6, "9321483")
        await conn.execute(
            "INSERT INTO maritime_events (event_type, mmsi, start_ts, imo, classification, method, detail) "
            "VALUES ('imo_integrity',$1,$2,$3,'candidate','imo-integrity-v1',"
            "'{\"integrity_type\":\"mmsi_collision\"}'::jsonb)", M6, _t(5), "9321483")
        row = await conn.fetchrow(
            "SELECT n_pattern_types, n_imo_integrity FROM maritime_hull_composite WHERE hull='9321483'")
        out["patterns"] = row["n_pattern_types"]
        out["n_imo_integrity"] = row["n_imo_integrity"]
    out = await _in_txn(body)
    assert out["n_imo_integrity"] == 1
    assert out["patterns"] == 2              # ais_gap + imo_integrity are distinct pattern types
