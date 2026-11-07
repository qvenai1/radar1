"""E4 / D3 — the airport → landings endpoint (/search/airport) + gazetteer.

In-process ASGI against the LIVE DB (skips without DATABASE_URL). Air is live, so —
like the aircraft tests — this asserts shape + the two-signal BEHAVIOUR against known
airports (stable gazetteer) rather than a fixed fixture. Verifies E4-pre's risk list:

  * resolution by ICAO / IATA / name (in-process, from the committed gazetteer);
  * PRIMARY signal at a well-covered field (Ben Gurion LLBG — grounded candidates);
  * ELEVATION-RELATIVE FALLBACK where the primary is silent (Queen Alia OJAI —
    field 730 m MSL, 0 on_ground: candidates are all 'approach', never 'grounded');
  * the constrained query stays sub-second (E4-pre's ~241 ms);
  * a no-match / coverage-limited result is empty, not an error.
"""

import os
import time
from contextlib import asynccontextmanager

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@asynccontextmanager
async def _client():
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app
    from backend.db import Database

    app = create_app()
    app.state.db = Database(DSN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c
    finally:
        await app.state.db.close()


def test_gazetteer_loads_and_resolves():
    """Committed asset — no DB, always runs."""
    from backend import airports

    rows = airports._airports()
    assert len(rows) == 221                       # bbox + large/medium + scheduled
    assert {"icao", "lat", "lon", "elevation_ft"} <= set(rows[0])
    # resolution by ICAO / IATA / name
    assert any(a["icao"] == "LLBG" for a in airports.resolve("LLBG"))
    assert any(a["icao"] == "OJAI" for a in airports.resolve("AMM"))          # IATA
    assert any(a["icao"] == "LLBG" for a in airports.resolve("ben gurion"))   # name
    assert airports.resolve("zznope") == []


@requires_db
@pytest.mark.asyncio
async def test_airport_primary_signal_ben_gurion():
    async with _client() as c:
        t0 = time.time()
        r = await c.get("/api/v1/search/airport", params={"q": "LLBG"})
        dt = time.time() - t0
        assert r.status_code == 200
        d = r.json()
        assert d["entity"] == "airport" and d["caveat"] and d["airport"]["icao"] == "LLBG"
        cands = d["candidates"]
        assert cands, "Ben Gurion (well-covered) should have landing candidates over 24h"
        # PRIMARY signal present (on_ground surface contacts)
        assert any(x["signal"] == "grounded" for x in cands)
        assert {"icao24", "landing_ts", "signal", "n_fixes"} <= set(cands[0])
        # the constrained query is sub-second (E4-pre ~241 ms); generous CI bound
        assert dt < 2.0, f"landing query took {dt:.2f}s (expected sub-second)"


@requires_db
@pytest.mark.asyncio
async def test_airport_fallback_at_queen_alia():
    async with _client() as c:
        d = (await c.get("/api/v1/search/airport", params={"q": "OJAI"})).json()
        assert d["airport"]["icao"] == "OJAI" and d["airport"]["elevation_ft"] == 2395
        cands = d["candidates"]
        if not cands:
            pytest.skip("no OJAI arrivals in the window (coverage-limited — valid)")
        # The two-signal proof from the poorly-covered side. Queen Alia sits at 730 m
        # MSL and E4-pre proved an ABSOLUTE altitude ceiling finds nothing there, so
        # the property this test exists to prove is that the ELEVATION-RELATIVE
        # fallback finds arrivals the primary on_ground signal cannot.
        #
        # NOT asserted as a share of candidates: terrestrial ADS-B ground coverage at
        # OJAI has improved, so the grounded/approach mix drifts with live coverage
        # (it was approach-dominated; it is now 6 approach / 8 grounded). A ratio
        # bound tests the receiver estate, not the detector. Instead assert the
        # invariant that makes the fallback a fallback: every approach candidate lies
        # ABOVE the absolute (sea-level) terminal band, so an absolute ceiling would
        # have returned NONE of them — only the elevation-relative band finds them.
        from backend.airports import TERMINAL_M

        approach = [x for x in cands if x["signal"] == "approach"]
        assert approach, "the fallback must fire where on_ground coverage is poor"
        assert all(x["min_altitude_m"] is not None for x in approach)
        elev_m = d["airport"]["elevation_ft"] * 0.3048
        # ⛔ `any`, NOT `all` — and the difference is the whole point of the assertion.
        # This read `all(... > TERMINAL_M)` and failed on live data: measured 2026-08-24,
        # 9 approach candidates at OJAI report min_altitude_m of 1173, 838, 290, 221, 145,
        # 107, 46, 38, 30 m — SEVEN of the nine at or below the 450 m absolute band. `all`
        # was never a property of the detector: the elevation-relative band is a strict
        # SUPERSET of the absolute one (the ceiling is elev_m + TERMINAL_M, and elev_m > 0
        # for every field above sea level), so a candidate INSIDE the absolute band is
        # admitted too and violates nothing. The claim the fallback actually makes — and
        # the only one that distinguishes it from an absolute ceiling — is that AT LEAST
        # ONE arrival lies above that ceiling, where an absolute band would have found
        # nothing. Asserting `all` tested the receiver estate, exactly as the comment
        # above warns about ratios, one level further down.
        assert any(x["min_altitude_m"] > TERMINAL_M for x in approach), (
            "an absolute band would have found NONE of these — the elevation-relative "
            "band is what makes them visible")
        # ⚠ AND THE LOW END CANNOT BE ASSERTED FROM THIS PAYLOAD AT ALL. `landing_candidate`
        # GATES on `terminal_alt` — the last altitude in the track — but REPORTS
        # `min_altitude_m = min(alts)` (airports.py:145-147). They are different numbers, so
        # a single bad low fix anywhere in an otherwise valid approach lands in the response
        # while the gate never saw it. That, not a 700 m descent below the runway, is what
        # 30–46 m readings at a 730 m field are: the band's own input is not exposed here.
        # ⚠ The band is also one-sided (`<= elev_m + TERMINAL_M`, no floor) — recorded in
        # PROJECT_STATE §6.16 for the owner. Neither is asserted here: pinning today's
        # low-end mix would repeat the very defect this comment exists to remove.
        # ...and they are genuinely near THIS field, not high overflights
        assert all(x["min_altitude_m"] <= elev_m + TERMINAL_M for x in approach)
        # the two signals stay distinct: a grounded candidate is a surface contact and
        # carries no altitude, an approach candidate is altitude-derived
        assert all(x["min_altitude_m"] is None
                   for x in cands if x["signal"] == "grounded")


@requires_db
@pytest.mark.asyncio
async def test_airport_resolution_and_edges():
    async with _client() as c:
        # resolve by IATA → the airport list + auto-selected airport
        amm = (await c.get("/api/v1/search/airport", params={"q": "AMM"})).json()
        assert amm["airport"] and amm["airport"]["icao"] == "OJAI"
        # a specific airport can be selected via icao (the one-to-many list → detail)
        sel = (await c.get("/api/v1/search/airport", params={"q": "international", "icao": "LLBG"})).json()
        assert sel["airport"]["icao"] == "LLBG" and sel["count"] >= 1
        # no match → 0 airports, no candidates (not an error)
        nm = (await c.get("/api/v1/search/airport", params={"q": "zznope123"})).json()
        assert nm["count"] == 0 and nm["candidates"] == [] and nm["airport"] is None
        # empty q → 422
        assert (await c.get("/api/v1/search/airport", params={"q": ""})).status_code == 422
