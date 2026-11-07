"""DASH-4 — the single-vessel live endpoint (/maritime/vessel).

Closes the gap DASH-2f measured and worked around: aircraft and satellites had a
single-entity route to their LIVE contract, vessels did not, so the home's left panel
rendered the SUMMARY contract and named the four fields it could not reach.

The load-bearing test here is `test_single_vessel_is_byte_identical_to_maritime`. The
endpoint's whole promise is that it serves the SAME `Vessel` contract `/maritime` serves
— not a second derivation that looks the same today and drifts next month. That promise
is only worth the bytes it is written in if something checks it, so this compares the two
payloads literally, from the SAME snapshot generation, and fails on any difference.

In-process (ASGI transport). The live-path tests read the real snapshot file and skip
cleanly when it is absent or stale; the DB-path tests skip without DATABASE_URL.
"""

import json
import os
import time
from contextlib import asynccontextmanager

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

# ⛔ NO HARD-CODED "IMPOSSIBLE" MMSI. This read:
#     # An MMSI that cannot exist in the AIS feed: MID 999 is unassigned.
#     UNKNOWN_MMSI = "999999999"
# and on 2026-08-24 the feed contained exactly that MMSI — one fix, 2026-08-18, off Cyprus,
# 100.8 kn, nav_status 15, no identity — so this test failed while the endpoint was right.
# An unassigned MID says how transmitters are SUPPOSED to be configured; this project exists
# to watch traffic that ignores such rules, so "cannot exist" was never ours to assume. The
# `unobserved_mmsi` fixture (tests/conftest.py) establishes the absence from the STORE
# instead — see PROJECT_STATE §6.18 for the detection gap the occupied sentinel exposed.


@asynccontextmanager
async def _client():
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app
    from backend.db import Database

    app = create_app()
    if DSN:
        app.state.db = Database(DSN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c
    finally:
        if DSN:
            await app.state.db.close()


def _fresh_snapshot_states():
    """The live AIS snapshot's states, or None when there is nothing fresh to assert on.

    Uses the endpoint's own freshness rule so this test never disagrees with the code
    about what "live" means."""
    from backend.api import MARITIME_STALE_S, _read_snapshot

    snap = _read_snapshot("aisstream")
    if snap is None:
        return None
    data, age = snap
    if age > MARITIME_STALE_S:
        return None
    return data["states"] or None


def _pick_live_mmsi(states):
    """Prefer a vessel carrying the four fields DASH-2f could not reach, so the test
    proves the gap is actually closed rather than that nulls match nulls."""
    for r in states:
        if all(r.get(f) is not None for f in ("heading", "destination", "nav_status", "draught")):
            return r["mmsi"]
    return states[0]["mmsi"]


@pytest.mark.asyncio
async def test_single_vessel_is_byte_identical_to_maritime():
    """⛔ THE ANTI-DRIFT TEST. One vessel from /maritime/vessel must be literally the
    same JSON object as that vessel's row inside /maritime — same keys, same values,
    same field count. A difference means a second derivation of "vessel" has appeared,
    which is the exact failure this endpoint was built to avoid."""
    states = _fresh_snapshot_states()
    if not states:
        pytest.skip("no fresh AIS snapshot to compare against")
    mmsi = _pick_live_mmsi(states)

    async with _client() as c:
        # Align generations: the collector rewrites the snapshot every ~30 s, so a naive
        # pair of reads can straddle two of them and fail for a reason that is not drift.
        for _ in range(6):
            one = (await c.get("/api/v1/maritime/vessel", params={"mmsi": mmsi})).json()
            fleet = (await c.get("/api/v1/maritime")).json()
            if one.get("snapshot_time") == fleet.get("snapshot_time"):
                break
            time.sleep(0.5)
        else:
            pytest.skip("could not read both endpoints from one snapshot generation")

    row = next((r for r in fleet["states"] if r["mmsi"] == mmsi), None)
    assert row is not None, f"{mmsi} is in the snapshot but missing from /maritime"
    assert one["state"] == "live"
    assert set(one["vessel"]) == set(row), "field sets differ — the contracts have diverged"
    assert json.dumps(one["vessel"], sort_keys=True) == json.dumps(row, sort_keys=True), (
        "single-vessel payload is not byte-identical to /maritime's row — a second "
        "derivation exists"
    )


@pytest.mark.asyncio
async def test_live_vessel_serves_the_four_fields_2f_could_not_reach():
    """The point of DASH-4: heading · destination · nav_status · draught arrive for a
    vessel that broadcasts them. The summary contract could not carry these."""
    states = _fresh_snapshot_states()
    if not states:
        pytest.skip("no fresh AIS snapshot")
    full = [r for r in states
            if all(r.get(f) is not None
                   for f in ("heading", "destination", "nav_status", "draught"))]
    if not full:
        pytest.skip("no vessel currently broadcasting all four fields")

    async with _client() as c:
        d = (await c.get("/api/v1/maritime/vessel", params={"mmsi": full[0]["mmsi"]})).json()
    v = d["vessel"]
    for f in ("heading", "destination", "nav_status", "draught"):
        assert v[f] is not None, f"{f} did not survive to the single-vessel payload"
    assert d["not_reported"] == [] or all(
        f not in d["not_reported"] for f in ("heading", "destination", "nav_status", "draught"))


@pytest.mark.asyncio
async def test_unknown_mmsi_is_404_not_an_empty_object(unobserved_mmsi):
    """"Not observed" ≠ "does not exist". An MMSI nothing has ever heard of is an
    explicit error, never a Vessel full of nulls that a client would render as a hull."""
    async with _client() as c:
        r = await c.get("/api/v1/maritime/vessel", params={"mmsi": unobserved_mmsi})
    assert r.status_code == 404
    assert unobserved_mmsi in r.json()["detail"]


@pytest.mark.asyncio
async def test_blank_mmsi_is_rejected():
    async with _client() as c:
        assert (await c.get("/api/v1/maritime/vessel", params={"mmsi": ""})).status_code == 422
        assert (await c.get("/api/v1/maritime/vessel")).status_code == 422


@requires_db
@pytest.mark.asyncio
async def test_silent_vessel_is_last_known_and_says_so():
    """A known-but-not-transmitting hull returns its last stored fix, LABELLED, with the
    age that makes it un-mistakable for a current position — and with the static identity
    carrying its OWN vintage, because the two are not the same moment."""
    from backend.db import Database

    db = Database(DSN)
    try:
        pool = await db.pool()
        row = await pool.fetchrow(
            "SELECT mmsi FROM vessel_states GROUP BY mmsi "
            "HAVING max(ts) < now() - INTERVAL '2 hours' ORDER BY max(ts) DESC LIMIT 1")
    finally:
        await db.close()
    if row is None:
        pytest.skip("no vessel silent for >2 h in the store")

    async with _client() as c:
        d = (await c.get("/api/v1/maritime/vessel", params={"mmsi": row["mmsi"]})).json()

    assert d["state"] == "last_known"
    assert d["age_seconds"] is not None and d["age_seconds"] > 7200, (
        "a stale fix must carry an age that prevents it reading as current")
    assert d["snapshot_time"] is None, "last_known must not borrow the live snapshot's stamp"
    # the contract is the SAME shape as live — 19 fields, no more, no fewer
    assert len(d["vessel"]) == 19
    assert d["vessel"]["mmsi"] == row["mmsi"]


@requires_db
@pytest.mark.asyncio
async def test_not_reported_names_absences_and_excludes_structural_nulls():
    """`not_reported` distinguishes device silence from source shape. ship_id and flag are
    null for EVERY AIS vessel (measured 0/556 in the live snapshot), so they are structural
    and must never be listed as something the device failed to report."""
    from backend.db import Database

    db = Database(DSN)
    try:
        pool = await db.pool()
        row = await pool.fetchrow(
            "SELECT mmsi FROM vessel_states GROUP BY mmsi "
            "HAVING max(ts) < now() - INTERVAL '2 hours' ORDER BY max(ts) DESC LIMIT 1")
    finally:
        await db.close()
    if row is None:
        pytest.skip("no silent vessel in the store")

    async with _client() as c:
        d = (await c.get("/api/v1/maritime/vessel", params={"mmsi": row["mmsi"]})).json()

    v, nr = d["vessel"], d["not_reported"]
    assert "ship_id" not in nr and "flag" not in nr, "structural nulls must not be listed"
    # every listed field really is null, and every reportable null really is listed
    from backend.api import _VESSEL_REPORTABLE
    for f in nr:
        assert v[f] is None, f"{f} listed as not-reported but has a value"
    for f in _VESSEL_REPORTABLE:
        if v[f] is None:
            assert f in nr, f"{f} is null but was not named in not_reported"
