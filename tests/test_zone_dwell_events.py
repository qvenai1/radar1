"""ZONES-3 — zone_dwell events + composite admission (the strong subset).

In-process against the LIVE DB (skips without DATABASE_URL). Asserts, non-mutatingly,
that the written events are the STRONG subset (candidate + safety_zone_intrusion today)
and that the composite view's disjunct admits ONLY the candidate ones. Assumes the
writer (scripts/detect_zone_dwell.py) has populated events; skips if none exist so the
suite stays green where the writer has not been run.
"""

import json
import os

import asyncpg
import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@requires_db
@pytest.mark.asyncio
async def test_zone_dwell_events_are_candidate_strong_subset():
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            "SELECT mmsi, classification, method, duration_s, detail "
            "FROM maritime_events WHERE source='aisstream' AND event_type='zone_dwell'")
        if not rows:
            pytest.skip("no zone_dwell events written (run scripts/detect_zone_dwell.py)")
        for r in rows:
            d = r["detail"]
            d = json.loads(d) if isinstance(d, str) else d
            assert r["classification"] == "candidate"          # only candidates are written
            assert r["method"] == "zone-dwell-v1"
            # today only the safety_zone_intrusion class is admitted (standoff deferred)
            assert d["signal_class"] == "safety_zone_intrusion"
            assert d["zone_type"] == "platform" and d["radius_basis"] == "sourced"
            assert d["duration_is_lower_bound"] is True
            assert d.get("caveat")                             # lead-not-proof caveat carried
    finally:
        await conn.close()


@requires_db
@pytest.mark.asyncio
async def test_composite_disjunct_admits_only_candidate_zone_dwell():
    conn = await asyncpg.connect(DSN)
    try:
        n_candidate = await conn.fetchval(
            "SELECT count(*) FROM maritime_events "
            "WHERE source='aisstream' AND event_type='zone_dwell' "
            "AND classification='candidate'")
        if not n_candidate:
            pytest.skip("no candidate zone_dwell events")
        # every candidate zone_dwell enters maritime_hull_events; non-candidates never
        n_in_view = await conn.fetchval(
            "SELECT count(*) FROM maritime_hull_events WHERE event_type='zone_dwell'")
        n_total = await conn.fetchval(
            "SELECT count(*) FROM maritime_events WHERE source='aisstream' AND event_type='zone_dwell'")
        assert n_in_view == n_candidate                        # disjunct requires candidate
        # n_zone_dwell parity column exists and sums to the candidate count
        n_zone_dwell_col = await conn.fetchval(
            "SELECT coalesce(sum(n_zone_dwell),0) FROM maritime_hull_composite")
        assert n_zone_dwell_col == n_candidate
        # the capstone (IMO 9895915) has NO zone_dwell — admission did not touch it
        cap = await conn.fetchrow(
            "SELECT n_zone_dwell, n_pattern_types FROM maritime_hull_composite WHERE hull='9895915'")
        if cap:
            assert cap["n_zone_dwell"] == 0
        assert n_in_view <= n_total
    finally:
        await conn.close()
