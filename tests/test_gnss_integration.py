"""G4b — GNSS integration into the composite + API.

Proves the individual-gnss tier enters the composite (and area/glitch do not), the
gnss rank baseline is low (victim-leaning — contributes to multi-pattern but does not
inflate a hull to high rank alone), and the API summarizes gnss as gnss, never as an
identity change. The DB-guarded case asserts the migration-029 view admits exactly
the candidate/individual gnss rows.
"""

import os

import pytest

from backend.api import _gnss_summary, _idc_summary
from backend.composite import HIGH, LOW, MEDIUM, composite_rank, underlying_rank_ord


def test_gnss_rank_baseline_is_low_below_gap():
    assert underlying_rank_ord("gnss_anomaly", {}) == 1        # LOW baseline (victim-leaning)
    assert underlying_rank_ord("ais_gap", {}) == 2             # gap baseline unchanged
    # an explicit suspicion_rank still wins if a gnss episode ever carries one
    assert underlying_rank_ord("gnss_anomaly", {"suspicion_rank": "high"}) == 3


def test_lone_gnss_individual_does_not_reach_high():
    assert composite_rank(1, False, 1) == LOW        # a lone gnss episode stays low
    assert composite_rank(2, False, 1) == MEDIUM     # gnss + one other, no grey/high -> medium
    assert composite_rank(4, False, 1) == HIGH       # ...but a 4-pattern hull is elevated


def test_gnss_summary_is_not_identity_change():
    d = {"n_implausible_fixes": 6, "implied_speed_max": 900.0, "inland_km_max": 90.0}
    s = _gnss_summary(d)
    assert "GNSS" in s and "implausible" in s
    assert s != _idc_summary(d)          # must never be routed to the identity-change summary


DSN = os.environ.get("DATABASE_URL")


@pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_composite_admits_only_individual_gnss():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        in_view = await conn.fetchval(
            "SELECT count(*) FROM maritime_hull_events WHERE event_type='gnss_anomaly'")
        candidates = await conn.fetchval(
            "SELECT count(*) FROM maritime_events WHERE source='aisstream' AND event_type='gnss_anomaly' "
            "AND classification='candidate'")
        excluded = await conn.fetchval(
            "SELECT count(*) FROM maritime_events WHERE source='aisstream' AND event_type='gnss_anomaly' "
            "AND classification IN ('area-jamming','glitch')")
        # only the individual/candidate tier reaches the composite view
        assert in_view == candidates
        assert excluded > 0                       # area + glitch exist, but are excluded
        # no area/glitch gnss row leaked into the view
        leaked = await conn.fetchval(
            "SELECT count(*) FROM maritime_hull_events hv "
            "JOIN maritime_events e ON e.source='aisstream' AND e.event_type='gnss_anomaly' "
            "  AND e.mmsi=hv.via_mmsi AND e.start_ts=hv.start_ts "
            "WHERE hv.event_type='gnss_anomaly' AND e.classification IN ('area-jamming','glitch')")
        assert leaked == 0
    finally:
        await conn.close()
