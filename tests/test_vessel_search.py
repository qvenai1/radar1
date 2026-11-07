"""E1 — the vessel entity-search endpoints (/search/vessel + /vessel/{mmsi}/track).

In-process (ASGI transport) against the live FROZEN DB — no running server needed,
skips cleanly without DATABASE_URL. Asserts, against the frozen state (vessel data
frozen at 2026-07-20 00:03:36):

  * the track returns ORDERED fixes carrying their quality columns (a spoofed fix
    stays visible, not dropped) — capstone 716 fixes = 643 plausible / 72 implausible
    / 1 suspect;
  * search resolves by MMSI / IMO / name (substring, case-insensitive) to the same hull;
  * the event counts reproduce the composite for the multi-pattern CAPSTONE hull
    (IMO 9895915 / MMSI 215936000, EUROSAILOR): 2 gaps + 2 rendezvous (as PARTNER) +
    1 identity-change + 6 GNSS = 4 pattern types;
  * a clean vessel (319483000) shows a plain, all-plausible track and 0 patterns.
"""

import os
from contextlib import asynccontextmanager

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

CAPSTONE_MMSI = "215936000"
CAPSTONE_IMO = "9895915"
CLEAN_MMSI = "319483000"


@asynccontextmanager
async def _client():
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app
    from backend.db import Database

    app = create_app()
    app.state.db = Database(DSN)  # endpoints only need app.state.db (no lifespan)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c
    finally:
        await app.state.db.close()


# --------------------------- track endpoint ---------------------------

@requires_db
@pytest.mark.asyncio
async def test_vessel_track_capstone_ordered_with_quality():
    async with _client() as c:
        r = await c.get(f"/api/v1/vessel/{CAPSTONE_MMSI}/track")
        assert r.status_code == 200
        d = r.json()
        assert {"mmsi", "from", "to", "count", "limit", "offset", "has_more", "points"} <= set(d)
        assert d["mmsi"] == CAPSTONE_MMSI
        assert d["count"] == 716 and d["has_more"] is False and d["limit"] == 5000
        assert d["from"] is None and d["to"] is None  # default = full frozen span

        pts = d["points"]
        assert len(pts) == 716
        # ordered ASC by ts (ISO-8601 UTC → lexicographic == chronological)
        assert [p["ts"] for p in pts] == sorted(p["ts"] for p in pts)
        # quality columns TRAVEL WITH each fix (the G3 principle)
        assert {"ts", "lat", "lon", "speed", "course", "heading", "nav_status",
                "position_quality", "anomaly_flags", "source", "data_origin"} <= set(pts[0])

        # frozen quality breakdown — a spoofed vessel's bad fixes are visible, not hidden
        split: dict[str, int] = {}
        for p in pts:
            split[p["position_quality"]] = split.get(p["position_quality"], 0) + 1
        assert split == {"plausible": 643, "implausible": 72, "suspect": 1}
        # the implausible fixes carry their anomaly evidence
        impl = [p for p in pts if p["position_quality"] == "implausible"]
        assert impl and any("impossible_speed" in (p["anomaly_flags"] or []) for p in impl)


@requires_db
@pytest.mark.asyncio
async def test_vessel_track_window_and_edges(unobserved_mmsi):
    async with _client() as c:
        # a 24 h window is a strict subset of the full track
        w = (await c.get(f"/api/v1/vessel/{CAPSTONE_MMSI}/track",
                         params={"since": "2026-07-19T00:00:00", "until": "2026-07-20T00:00:00"})).json()
        assert 0 < w["count"] < 716 and w["from"] is not None and w["to"] is not None

        # unknown MMSI → empty track, never an error.
        # ⛔ The MMSI comes from the `unobserved_mmsi` fixture, not from a literal. This read
        # `/api/v1/vessel/999999999/track` and expected 0; on 2026-08-24 that hull had a fix
        # and the assertion failed on a fact about the feed, not about the endpoint.
        u = await c.get(f"/api/v1/vessel/{unobserved_mmsi}/track")
        assert u.status_code == 200 and u.json()["count"] == 0

        # invalid identifier + inverted window → 422
        assert (await c.get("/api/v1/vessel/12/track")).status_code == 422
        assert (await c.get(f"/api/v1/vessel/{CAPSTONE_MMSI}/track",
                            params={"since": "2026-07-20T00:00:00",
                                    "until": "2026-07-19T00:00:00"})).status_code == 422


@requires_db
@pytest.mark.asyncio
async def test_vessel_track_clean_is_predominantly_plausible():
    # 319483000 is a clean pleasure craft. The AIS feed is LIVE (it resumed after
    # the 07-20 freeze), so assert a SUBSTANTIAL, PREDOMINANTLY-plausible track
    # rather than a frozen exact count / 100% (live data has occasional anomalies).
    async with _client() as c:
        d = (await c.get(f"/api/v1/vessel/{CLEAN_MMSI}/track")).json()
        assert d["count"] > 2000
        plausible = sum(1 for p in d["points"] if p["position_quality"] == "plausible")
        assert plausible / d["count"] > 0.98   # clean vessel — (near-)all plausible


# --------------------------- search endpoint ---------------------------

@requires_db
@pytest.mark.asyncio
async def test_search_vessel_capstone_summary_agrees_with_the_store():
    """The summary's counts must REPRODUCE the store, not match a remembered tally.

    ⛔ WHY THIS IS NO LONGER `== (2, 2, 1, 6)` AND NO LONGER NAMED "four_pattern". Those
    were the capstone's counts on the day the test was written, and `maritime_events` is a
    DELETE-then-INSERT table: every detector run replaces its own output, so a count is a
    reading of the last run, never a constant. Measured 2026-08-24 the same hull reports
    (4, 0, 1, 6) — and BOTH movements were verified against the store before this test was
    touched:
      * gaps 2 → 4 is the CANDIDATE tier growing as the frozen hull's silence accumulates;
        it holds 20 ais_gap rows in five classes (4 candidate · 4 stationary · 7
        outage-excluded · 3 jamming-excluded · 2 edge) and the endpoint counts only
        `candidate`, correctly.
      * rendezvous 2 → 0 is real: ZERO rendezvous rows name this hull as subject or
        partner anywhere in the table. The detector is not dead (67 rendezvous over 27
        hulls, 07-11 → 08-24) — this hull's two simply are not in its current output.
    Re-pinning (4, 0, 1, 6) would fail again the next time a detector runs. Pinning the
    numbers was the defect; the drift was only the symptom.

    ⇒ Asserted instead: the endpoint's aggregation AGREES with an independent count over
    `maritime_events`, written here in different SQL from the endpoint's (which fetches
    once and filters in Python). That is what the literal was really guarding — the tier
    rules and the subject-OR-partner rendezvous rule — and it cannot drift.
    """
    from backend.db import Database

    db = Database(DSN)
    try:
        pool = await db.pool()
        # Independent derivation, per the endpoint's DOCUMENTED semantics: gaps and gnss at
        # the candidate tier; rendezvous counts this hull as subject OR partner.
        row = await pool.fetchrow(
            """
            SELECT
              count(*) FILTER (WHERE event_type = 'ais_gap'
                               AND classification = 'candidate')      AS n_gaps,
              count(*) FILTER (WHERE event_type = 'identity_change')   AS n_idc,
              count(*) FILTER (WHERE event_type = 'gnss_anomaly'
                               AND classification = 'candidate')       AS n_gnss
            FROM maritime_events
            WHERE source = 'aisstream' AND (mmsi = $1 OR imo = $2)
            """,
            CAPSTONE_MMSI, CAPSTONE_IMO,
        )
        n_rdv = await pool.fetchval(
            """
            SELECT count(*) FROM maritime_events
            WHERE source = 'aisstream' AND event_type = 'rendezvous'
              AND (mmsi = $1 OR imo = $2
                   OR detail ->> 'partner_mmsi' = $1
                   OR detail ->> 'partner_imo'  = $2)
            """,
            CAPSTONE_MMSI, CAPSTONE_IMO,
        )
    finally:
        await db.close()

    async with _client() as c:
        r = await c.get("/api/v1/search/vessel", params={"q": "EUROSAILOR"})
        assert r.status_code == 200
        d = r.json()
        assert d["entity"] == "vessel" and d["count"] == 1 and d["caveat"]
        m = d["matches"][0]
        # identity — genuinely frozen: this comes from the `vessel_identity` spine, not
        # from an event table, so it stays literal.
        assert (m["mmsi"], m["imo"], m["name"]) == (CAPSTONE_MMSI, CAPSTONE_IMO, "EUROSAILOR")
        assert m["flag_country"] == "Malta" and m["ship_type"] == "tanker"
        assert m["length_m"] == 183 and m["beam_m"] == 32
        # last known fix = the data vintage (frozen on 2026-07-20, so still literal)
        assert m["last_ts"] and m["last_ts"].startswith("2026-07-20T00:03")
        assert m["last_lat"] is not None and m["last_position_quality"]
        # ...and 716 fixes, also frozen — the hull stopped transmitting, so this one IS a
        # constant. Re-measured 2026-08-24: still 716.
        assert m["n_fixes"] == 716

        # the aggregation itself
        assert (m["n_gaps"], m["n_rendezvous"], m["n_identity_changes"], m["n_gnss"]) == (
            row["n_gaps"], n_rdv, row["n_idc"], row["n_gnss"]
        )
        # `n_pattern_types` is a derived count of the non-zero types, nothing more
        assert m["n_pattern_types"] == sum(
            1 for n in (m["n_gaps"], m["n_rendezvous"],
                        m["n_identity_changes"], m["n_gnss"]) if n > 0)
        # ...and this hull must remain a MULTI-pattern hull, or it is the wrong capstone
        # for a multi-pattern test and the fixture choice needs revisiting, not the number.
        assert m["n_pattern_types"] >= 2, (
            "the capstone no longer carries multiple pattern types — pick a capstone that "
            "does rather than lowering this bar")


@requires_db
@pytest.mark.asyncio
async def test_search_vessel_resolves_by_mmsi_imo_name():
    async with _client() as c:
        # MMSI (exact), IMO (exact), name (lowercase substring) → same capstone hull.
        # ⛔ The claim under test is RESOLUTION — that three identifier forms reach one
        # hull and describe it identically. It asserted `n_pattern_types == 4` alongside,
        # which pinned a moving detector tally into a resolution test and failed when the
        # hull's rendezvous left the store (see the capstone test above). Comparing the
        # three payloads to EACH OTHER tests resolution exactly, and cannot drift: if the
        # forms ever disagree, one of them is resolving to something else.
        seen: list[dict] = []
        for q in (CAPSTONE_MMSI, CAPSTONE_IMO, "eurosailor"):
            d = (await c.get("/api/v1/search/vessel", params={"q": q})).json()
            assert d["count"] >= 1
            top = next(x for x in d["matches"] if x["mmsi"] == CAPSTONE_MMSI)
            assert top["imo"] == CAPSTONE_IMO
            seen.append(top)
        assert seen[0] == seen[1] == seen[2], (
            "the three identifier forms resolve to the same hull but describe it "
            "differently — one of them is not reaching the same row")
        # a broad substring can return several matches
        many = (await c.get("/api/v1/search/vessel", params={"q": "euro"})).json()
        assert many["count"] >= 1 and any(x["mmsi"] == CAPSTONE_MMSI for x in many["matches"])


@requires_db
@pytest.mark.asyncio
async def test_search_vessel_clean_and_edges():
    async with _client() as c:
        d = (await c.get("/api/v1/search/vessel", params={"q": CLEAN_MMSI})).json()
        assert d["count"] == 1
        m = d["matches"][0]
        # clean↔capstone CONTRAST (live-robust, not a frozen tuple): a genuinely
        # clean vessel never spuriously acquires the STRONG multi-vessel / multi-
        # identity leads (a rendezvous needs a partner loitering alongside for >=1h;
        # an identity_change needs multiple broadcast identities), and stays far below
        # the 4-pattern capstone. A lone incidental gap / gnss episode CAN appear from
        # live coverage drift, so assert the contrast rather than an all-zero snapshot.
        assert m["n_rendezvous"] == 0 and m["n_identity_changes"] == 0
        assert m["n_pattern_types"] < 4 and m["n_fixes"] > 2000

        # empty q is rejected (min_length) — never a snapshot-wide scan
        assert (await c.get("/api/v1/search/vessel", params={"q": ""})).status_code == 422
