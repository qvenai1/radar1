"""DASH-1 — the /alerts country filter, in-process ASGI against the LIVE DB.

Asserts INVARIANTS, never frozen counts: which alerts a country has drifts with the
feed, but "nothing leaks past the filter", "a scoped count never exceeds the global
one" and "the scope is always echoed" are true at every vintage. Skips cleanly
without DATABASE_URL.

⚠ DASH-3 reversed DASH-1's "counts stay global under a filter" rule — counts and
domains[] are now country-scoped. See test_counts_are_scoped_but_never_ambiguous.
"""

import os
from contextlib import asynccontextmanager

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

pytestmark = pytest.mark.asyncio


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


@requires_db
async def test_no_filter_is_the_unchanged_default():
    async with _client() as c:
        d = (await c.get("/api/v1/alerts", params={"window_hours": 24})).json()
        assert d["countries_filtered"] is None
        assert "alerts" in d and "counts" in d


@requires_db
@pytest.mark.parametrize("spelling", ["SY", "sy", "Syria"])
async def test_name_and_code_any_case_are_one_query(spelling):
    """?country=SY, sy and Syria must be indistinguishable — the resolver normalises."""
    async with _client() as c:
        d = (await c.get("/api/v1/alerts",
                         params={"window_hours": 24, "country": spelling})).json()
        assert d["countries_filtered"] == "SY"


@requires_db
@pytest.mark.parametrize("code", ["SY", "TR", "IQ", "JO", "LB", "IL", "CY"])
async def test_nothing_leaks_past_the_filter(code):
    """THE invariant: every returned alert genuinely touches the requested country.
    Membership, not equality — a multi-country alert answers to each of its
    countries, which is why `countries` is a list."""
    async with _client() as c:
        d = (await c.get("/api/v1/alerts",
                         params={"window_hours": 24, "country": code})).json()
        assert d["countries_filtered"] == code
        leaks = [a for a in d["alerts"] if code not in a["countries"]]
        assert not leaks, f"{len(leaks)} alert(s) returned without {code}"


@requires_db
async def test_counts_are_scoped_but_never_ambiguous():
    """⚠ DASH-3 REVERSED the DASH-1 rule this test used to assert.

    DASH-1 kept `counts` global under a filter, on the grounds that a filtered feed
    must not make the world look smaller. DASH-3 scopes them, because a country card
    that says "103 maritime leads" and lists 3 is the worse lie. What makes the
    reversal safe is `countries_filtered`: a scoped count is never ambiguous,
    because the envelope says what it is scoped to."""
    async with _client() as c:
        whole = (await c.get("/api/v1/alerts", params={"window_hours": 24})).json()
        one = (await c.get("/api/v1/alerts",
                           params={"window_hours": 24, "country": "IL"})).json()
        assert one["counts"]["total"] <= whole["counts"]["total"]
        assert one["countries_filtered"] == "IL", "the scope MUST be echoed"
        assert whole["countries_filtered"] is None
        assert len(one["alerts"]) <= len(whole["alerts"])


@requires_db
async def test_a_filtered_subset_is_a_subset_of_the_whole():
    async with _client() as c:
        whole = (await c.get("/api/v1/alerts", params={"window_hours": 24})).json()
        one = (await c.get("/api/v1/alerts",
                           params={"window_hours": 24, "country": "CY"})).json()
        key = lambda a: (a["signal_type"], a["entity_ref"]["id"])  # noqa: E731
        assert {key(a) for a in one["alerts"]} <= {key(a) for a in whole["alerts"]}


@requires_db
@pytest.mark.parametrize("bad", ["GB", "United Kingdom", "XX", "zzz"])
async def test_a_country_outside_the_aoi_is_a_400_not_an_empty_list(bad):
    """An empty list would read as "nothing happening in Britain", which is a claim
    this project cannot make — it has no boundaries there. 400 says so."""
    async with _client() as c:
        r = await c.get("/api/v1/alerts", params={"country": bad})
        assert r.status_code == 400
        assert "expected one of" in r.json()["detail"]


@requires_db
async def test_every_alert_carries_the_countries_field():
    """Present on all five families — absent means "not attributable to the seven",
    never "the field is missing"."""
    async with _client() as c:
        d = (await c.get("/api/v1/alerts", params={"window_hours": 24})).json()
        assert all(isinstance(a.get("countries"), list) for a in d["alerts"])


# --- country-scoped counts and domains (DASH-3 step 1) ---------------------
#
# DASH-1 deliberately kept counts global under a filter. DASH-3 reverses that: the
# card needs its own numbers, and `countries_filtered` is what makes a scoped count
# unmistakable for a world total. These pin both halves of the reversal.

@requires_db
async def test_unfiltered_counts_and_domains_stay_global():
    """The default path must be untouched by the reversal."""
    async with _client() as c:
        d = (await c.get("/api/v1/alerts", params={"window_hours": 24})).json()
        assert d["countries_filtered"] is None
        for stat in d["domains"]:
            assert "positions in" not in stat["source"]
            assert "pass over" not in stat["source"]


@requires_db
@pytest.mark.parametrize("code", ["SY", "TR", "IL"])
async def test_counts_are_scoped_and_agree_with_the_list(code):
    """The invariant that makes a country card coherent: what it counts is what it
    shows. counts are computed from the FULL family list before the cap, so this
    holds as long as no family exceeds ALERT_PER_FAMILY_CAP."""
    async with _client() as c:
        d = (await c.get("/api/v1/alerts",
                         params={"window_hours": 24, "country": code})).json()
        shown = {}
        for a in d["alerts"]:
            shown[a["family"]] = shown.get(a["family"], 0) + 1
        for fam in ("emergency", "military", "maritime", "implausible"):
            assert d["counts"][fam] >= shown.get(fam, 0), "a count may never be below what is shown"
        assert d["counts"]["total"] == sum(
            d["counts"][f] for f in ("emergency", "military", "maritime", "implausible"))


@requires_db
async def test_a_scoped_count_is_never_larger_than_the_global_one():
    async with _client() as c:
        whole = (await c.get("/api/v1/alerts", params={"window_hours": 24})).json()
        one = (await c.get("/api/v1/alerts",
                           params={"window_hours": 24, "country": "SY"})).json()
        for fam in ("emergency", "military", "maritime", "implausible", "total"):
            assert one["counts"][fam] <= whole["counts"][fam]


@requires_db
async def test_domains_are_scoped_and_name_their_population():
    """⚠ The /adsb trap: air is opensky-only, the same population as its global
    figure. The source string must say which population a number came from, so a
    country count is never read against a total from a different feed."""
    async with _client() as c:
        d = (await c.get("/api/v1/alerts",
                         params={"window_hours": 24, "country": "SY"})).json()
        by = {x["domain"]: x for x in d["domains"]}
        assert "opensky" in by["air"]["source"] and "positions in SY" in by["air"]["source"]
        assert "adsbexchange" not in by["air"]["source"], "populations must not be mixed"
        assert "positions in SY" in by["sea"]["source"]
        # space is a DIFFERENT question — a catalogue has no country
        assert "pass over SY" in by["space"]["source"]


@requires_db
async def test_scoped_domain_counts_do_not_exceed_the_global_ones():
    async with _client() as c:
        whole = {x["domain"]: x["count"]
                 for x in (await c.get("/api/v1/alerts",
                                       params={"window_hours": 24})).json()["domains"]}
        one = {x["domain"]: x["count"]
               for x in (await c.get("/api/v1/alerts",
                                     params={"window_hours": 24,
                                             "country": "SY"})).json()["domains"]}
        for dom in ("air", "sea", "space"):
            assert one[dom] <= whole[dom], f"{dom}: scoped {one[dom]} > global {whole[dom]}"
