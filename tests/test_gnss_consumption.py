"""G3 — the layers CONSUME position_quality (subtractive filtering).

Proves the one shared consumption filter (coverage_mask.PLAUSIBLE_FILTER) is wired
into every consumer SQL, and — against a live DB (insert-then-rollback, nothing
committed) — that it excludes ONLY 'implausible' while NULL (not-yet-checked) and
'suspect' both PASS, that the coverage mask does not count implausible fixes, and
that an A1 gap can neither start nor end at an implausible (spoofed) fix.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.coverage_mask import (
    BUSY_CELLS_SQL, CELL_COUNTS_SQL, PLAUSIBLE_FILTER, PORT_CELLS_SQL,
)
from scripts.detect_ais_gaps import GAP_SQL
from scripts.detect_rendezvous import FIX_SQL, PAIR_SQL

UTC = timezone.utc


# --- structural: the filter is the fixed clause and wired into EVERY consumer ---

def test_plausible_filter_is_the_fixed_null_safe_clause():
    # NULL and 'suspect' pass; only 'implausible' is subtracted. IS DISTINCT FROM
    # is the null-safe operator (a plain != would drop NULLs).
    assert PLAUSIBLE_FILTER == "position_quality IS DISTINCT FROM 'implausible'"


@pytest.mark.parametrize("sql", [CELL_COUNTS_SQL, PORT_CELLS_SQL, BUSY_CELLS_SQL, GAP_SQL, PAIR_SQL, FIX_SQL])
def test_every_consumer_wires_the_filter(sql):
    # mask density, port + busy masks (A2), the A1 gap stream, and the A2 pair +
    # fix streams all carry the exclusion — a missed consumer fails here.
    assert PLAUSIBLE_FILTER in sql


# --- DB-backed: filter semantics + mask + gap-endpoint exclusion ---------------

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")
DB_TS = datetime(2035, 3, 1, tzinfo=UTC)   # fresh, uncompressed, rolled back
ISOLATED = (5.05, 5.05)                    # Gulf of Guinea — no real AOI data here

_COLS = ("mmsi", "ts", "source", "latitude", "longitude", "speed", "nav_status",
         "distance_km", "position_quality", "anomaly_flags", "quality_method")


async def _insert(conn, mmsi, ts, lat, lon, pq, *, speed=8.0, nav_status=0, dist=50.0):
    flags = ["on_land"] if pq in ("implausible", "suspect") else None
    vals = [mmsi, ts, "pytest-g3", lat, lon, speed, nav_status, dist, pq, flags,
            ("gnss-quality-v1" if pq is not None else None)]
    cols = ", ".join(_COLS)
    ph = ", ".join(f"${i + 1}" for i in range(len(_COLS)))
    await conn.execute(f"INSERT INTO vessel_states ({cols}) VALUES ({ph})", *vals)


@requires_db
@pytest.mark.asyncio
async def test_filter_excludes_only_implausible_null_and_suspect_pass():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        tx = conn.transaction(); await tx.start()
        try:
            mm = "888800001"
            for i, pq in enumerate(("plausible", "suspect", None, "implausible")):
                await _insert(conn, mm, DB_TS + timedelta(minutes=i), *ISOLATED, pq)
            passed = await conn.fetchval(
                f"SELECT count(*) FROM vessel_states WHERE mmsi=$1 AND {PLAUSIBLE_FILTER}", mm)
            assert passed == 3   # plausible + suspect + NULL pass; implausible excluded
        finally:
            await tx.rollback()
    finally:
        await conn.close()


@requires_db
@pytest.mark.asyncio
async def test_coverage_mask_density_excludes_implausible():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        tx = conn.transaction(); await tx.start()
        try:
            mm = "888800002"
            for i, pq in enumerate(("plausible", "plausible", "suspect", None, "implausible", "implausible")):
                await _insert(conn, mm, DB_TS + timedelta(minutes=i), *ISOLATED, pq)
            # CELL_COUNTS_SQL is the mask's own density query; pull our isolated cell.
            n = await conn.fetchval(
                f"SELECT n FROM ({CELL_COUNTS_SQL}) c "
                "WHERE clat = floor(5.05/0.1)::int AND clon = floor(5.05/0.1)::int")
            # 6 inserted, 2 implausible excluded -> the isolated cell counts 4
            assert n == 4
        finally:
            await tx.rollback()
    finally:
        await conn.close()


@requires_db
@pytest.mark.asyncio
async def test_a1_gap_cannot_start_or_end_at_an_implausible_fix():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        tx = conn.transaction(); await tx.start()
        try:
            mm = "888800003"
            t0 = DB_TS                              # plausible
            t1 = DB_TS + timedelta(hours=1)          # implausible (spoofed) — must be skipped
            t2 = DB_TS + timedelta(hours=2, minutes=30)  # plausible
            await _insert(conn, mm, t0, *ISOLATED, "plausible")
            await _insert(conn, mm, t1, 31.72, 35.99, "implausible")   # a decoy jump
            await _insert(conn, mm, t2, *ISOLATED, "plausible")
            gaps = [g for g in await conn.fetch(GAP_SQL, None) if g["mmsi"] == mm]
            # exactly one gap, from the plausible T0 to the plausible T2 — the
            # implausible T1 neither starts nor ends it (it was filtered out).
            assert len(gaps) == 1
            assert gaps[0]["start_ts"] == t0 and gaps[0]["end_ts"] == t2
        finally:
            await tx.rollback()
    finally:
        await conn.close()
