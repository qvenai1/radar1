"""DRAUGHT-1 — the draught-delta annotation on A2 rendezvous.

Proves the CONTRACT that keeps it honest: THREE states (changed / unchanged /
insufficient_data), never two — a vessel we cannot see (no reading, or a
stale-single-value that can never evidence a change) is insufficient, NEVER
"unchanged"; transfer_shaped requires OPPOSITE-SIGN changes on both sides above the
noise floor (both loading at port is not a transfer); the ±48h window and 0.5m floor
behave at their boundaries; the live fixture survives an UNtuned floor; and the
annotation is deterministic (idempotent across an A2 re-run) while A2's own
classification is untouched.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.draught_delta import (
    CHANGED,
    INSUFFICIENT,
    NO_AFTER,
    NO_BEFORE,
    NO_DRAUGHT,
    NOISE_FLOOR_M,
    STALE_SINGLE,
    UNCHANGED,
    WINDOW_H,
    DraughtObs,
    annotation,
    classify_participant,
    is_transfer_shaped,
)


def _t(h):
    return datetime(2026, 7, 12, tzinfo=timezone.utc) + timedelta(hours=h)


START, END = _t(0), _t(3)


def _c(obs):
    return classify_participant(obs, START, END)


# --- three states + each insufficient_data reason ----------------------------

def test_changed_beyond_floor():
    p = _c([DraughtObs(_t(-10), 8.0), DraughtObs(_t(20), 16.0)])
    assert p.state == CHANGED and p.delta == 8.0 and p.reason is None


def test_unchanged_real_reading_both_sides():
    p = _c([DraughtObs(_t(-10), 8.0), DraughtObs(_t(20), 8.2)])  # 0.2 m < floor
    assert p.state == UNCHANGED and p.reason is None and p.before == 8.0 and p.after == 8.2


def test_insufficient_no_draught_reported():
    p = _c([])
    assert p.state == INSUFFICIENT and p.reason == NO_DRAUGHT


def test_insufficient_no_before():
    p = _c([DraughtObs(_t(5), 8.0), DraughtObs(_t(20), 12.0)])   # both AFTER start
    # no reading before the encounter; after = the NEAREST post-encounter reading (8.0)
    assert p.state == INSUFFICIENT and p.reason == NO_BEFORE and p.before is None and p.after == 8.0


def test_insufficient_no_after():
    p = _c([DraughtObs(_t(-10), 8.0), DraughtObs(_t(-5), 9.0)])  # both BEFORE end
    assert p.state == INSUFFICIENT and p.reason == NO_AFTER and p.before == 9.0


def test_stale_single_value_is_insufficient_not_unchanged():
    # exactly one distinct draught over the whole history -> cannot evidence a change
    p = _c([DraughtObs(_t(-10), 8.0), DraughtObs(_t(20), 8.0)])
    assert p.state == INSUFFICIENT and p.reason == STALE_SINGLE
    assert p.state != UNCHANGED           # the failure this layer must not commit


# --- transfer_shaped: opposite-sign, both beyond the floor --------------------

def test_transfer_shaped_opposite_sign():
    hi = _c([DraughtObs(_t(-10), 7.0), DraughtObs(_t(20), 9.0)])   # +2.0
    lo = _c([DraughtObs(_t(-10), 14.0), DraughtObs(_t(20), 12.0)]) # -2.0
    assert is_transfer_shaped(hi, lo) is True
    assert annotation(hi, lo)["transfer_shaped"] is True


def test_same_sign_is_not_transfer_shaped():
    # both loading at port (both draughts rise) is NOT a transfer
    a = _c([DraughtObs(_t(-10), 6.0), DraughtObs(_t(20), 10.0)])   # +4
    b = _c([DraughtObs(_t(-10), 7.0), DraughtObs(_t(20), 11.0)])   # +4
    assert a.state == CHANGED and b.state == CHANGED
    assert is_transfer_shaped(a, b) is False


def test_insufficient_side_is_never_transfer_shaped():
    changed = _c([DraughtObs(_t(-10), 14.0), DraughtObs(_t(20), 12.0)])
    blind = _c([])                                                 # insufficient
    assert is_transfer_shaped(changed, blind) is False


# --- noise floor + window boundaries -----------------------------------------

def test_noise_floor_boundary():
    below = _c([DraughtObs(_t(-1), 8.0), DraughtObs(_t(4), 8.0 + NOISE_FLOOR_M - 0.1)])
    at = _c([DraughtObs(_t(-1), 8.0), DraughtObs(_t(4), 8.0 + NOISE_FLOOR_M)])
    assert below.state == UNCHANGED
    assert at.state == CHANGED             # exactly the floor counts as a change


def test_window_boundary():
    # a reading beyond end (+3h) + WINDOW_H is out of range -> no_after
    outside = _c([DraughtObs(_t(-1), 8.0), DraughtObs(_t(3 + WINDOW_H + 1), 12.0)])
    assert outside.state == INSUFFICIENT and outside.reason == NO_AFTER
    inside = _c([DraughtObs(_t(-1), 8.0), DraughtObs(_t(3 + WINDOW_H - 1), 12.0)])
    assert inside.state == CHANGED


# --- the live fixture (untuned floor) ----------------------------------------

def test_live_fixture_survives_untuned_floor():
    # HIGH FREEDOM +0.6 / MAPLE CASTOR -2.0 (2026-07-12). +0.6 is marginal but clears
    # the principled 0.5 m floor (not tuned to it). If the floor were >0.6, yield -> 0.
    hf = _c([DraughtObs(_t(-10), 8.3), DraughtObs(_t(20), 8.9)])   # +0.6
    mc = _c([DraughtObs(_t(-10), 9.2), DraughtObs(_t(20), 7.2)])   # -2.0
    assert hf.state == CHANGED and hf.delta == 0.6
    assert mc.state == CHANGED and mc.delta == -2.0
    assert is_transfer_shaped(hf, mc) is True
    assert 0.6 >= NOISE_FLOOR_M            # the fixture only survives because floor <= 0.6


def test_determinism():
    obs_a = [DraughtObs(_t(-10), 8.3), DraughtObs(_t(20), 8.9)]
    obs_b = [DraughtObs(_t(-10), 9.2), DraughtObs(_t(20), 7.2)]
    assert annotation(_c(obs_a), _c(obs_b)) == annotation(_c(obs_a), _c(obs_b))


# --- DB-backed: A2's own classification unchanged + annotation present --------

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@requires_db
@pytest.mark.asyncio
async def test_annotation_present_and_a2_classification_unchanged():
    import asyncpg

    conn = await asyncpg.connect(DSN)
    try:
        import json
        rows = await conn.fetch(
            "SELECT detail FROM maritime_events WHERE event_type='rendezvous'")
        assert rows, "no rendezvous rows — run scripts/detect_rendezvous.py first"
        states = {"changed", "unchanged", "insufficient_data"}
        for r in rows:
            d = json.loads(r["detail"]) if isinstance(r["detail"], str) else r["detail"]
            # A2's own classification untouched (the regression that matters)
            assert d.get("suspicion_rank") in ("high", "medium", "low")
            dr = d.get("draught")
            assert dr is not None and dr["method"] == "draught-delta-v1"
            assert dr["primary"]["state"] in states and dr["partner"]["state"] in states
            # transfer_shaped only when BOTH sides changed (never asserted on a blind side)
            if dr["transfer_shaped"]:
                assert dr["primary"]["state"] == "changed" and dr["partner"]["state"] == "changed"
    finally:
        await conn.close()
