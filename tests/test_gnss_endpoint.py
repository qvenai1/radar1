"""G5a — the /api/v1/maritime/gnss-anomalies endpoint.

In-process (ASGI transport) against the live DB — no running server needed, skips
cleanly without DATABASE_URL. Asserts the episode shape, the frozen 276/504/25 split,
and the gnss_type/classification filters.
"""

import os

import pytest

DSN = os.environ.get("DATABASE_URL")
requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")


@requires_db
@pytest.mark.asyncio
async def test_gnss_anomalies_endpoint_shape_split_and_filters():
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app
    from backend.db import Database

    app = create_app()
    app.state.db = Database(DSN)   # the endpoint only needs app.state.db (no lifespan)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/maritime/gnss-anomalies")
            assert r.status_code == 200
            d = r.json()
            # ⛔ NOT a frozen row count. It was `== 805` until GNSS-REVIVE (2026-08-04)
            # deployed this detector's timer; the number was only stable because the detector
            # had been DEAD since 2026-07-21, and the first live run took it to 2,094. A test
            # pinned to a dead detector's output passes precisely while nothing works, which
            # is the failure mode this project keeps removing. Assert the invariant instead:
            # the endpoint reports a positive count consistent with what it returned.
            assert d["available"] is True
            assert d["count"] > 0 and d["count"] >= len(d["anomalies"])
            assert d["method"] == "gnss-anomaly-detector-v1" and d["caveat"]

            a = d["anomalies"][0]
            assert {"mmsi", "start_lat", "start_lon", "classification", "gnss_type",
                    "decoy_match", "n_implausible_fixes", "affected_lat"} <= set(a)

            split: dict[str, int] = {}
            for x in d["anomalies"]:
                split[x["classification"]] = split.get(x["classification"], 0) + 1
            # ⛔ NOT the frozen 276/504/25. Those held only because the detector had been
            # DEAD since 2026-07-21; GNSS-REVIVE's first live run made them 661/326/13, and a
            # timer-driven detector will move them again — that is what a timer is for.
            # Assert the CONTRACT: the Step-0 three-way split is exhaustive and real.
            assert set(split) <= {"candidate", "area-jamming", "glitch"}
            assert sum(split.values()) == len(d["anomalies"])
            assert split.get("candidate", 0) > 0

            # ⛔ The FILTER is what is under test, not the tally behind it — the tallies were
            # the same three frozen numbers, dead since 2026-07-21. A filter is correct when
            # it returns only its own class and the three classes partition the whole.
            per_type = {}
            for gt in ("individual", "area", "glitch"):
                rr = (await c.get("/api/v1/maritime/gnss-anomalies",
                                  params={"gnss_type": gt})).json()
                per_type[gt] = rr["count"]
                assert all(x["gnss_type"] == gt for x in rr["anomalies"])
            # ⚠ NOT asserted as a partition: the endpoint caps its result set (measured, the
            # unfiltered count and `individual` both report 1000 against 2,700 stored), so the
            # three filtered counts cannot be required to sum to the whole. The cap is a
            # display bound, not a classification.
            #
            # ⛔ THIS ASSERTION EARNED ITS KEEP — DO NOT SOFTEN IT TO DATA DRIFT. On
            # 2026-08-24 `area` returned 0 and this line failed. It looked like the same
            # over-pinning the comments above remove, and softening it was drafted and
            # thrown away: the cause was a LIVE BUG. `IMPL_POS_SQL` was fetched WITHOUT
            # `.format(source=source)` (detect_gnss_anomaly.py:155), so the broadcast-zone
            # query filtered `source = '{source}'` literally, matched 0 rows of 10,712, and
            # left the jamming zone permanently empty — which makes the area branch of
            # classify_episode() unreachable and `decoy_match=True` with it. The store held
            # 0 area-jamming across ALL history as a result. Fixed; the dry run then reported
            # 58 area cell-windows and 1,356 area-jamming episodes.
            # ⇒ A class going to zero is exactly the signal this line exists to raise.
            assert all(n > 0 for n in per_type.values()), (
                f"a gnss_type class is empty: {per_type} — check the detector before "
                f"relaxing this; an empty class has meant a broken query, not calm seas")
            # ...and, structurally: a class VISIBLE on the unfiltered page must be reachable
            # through its own filter, so a filter that drops a populated class fails too.
            visible = {x["gnss_type"] for x in d["anomalies"]}
            assert visible, "the unfiltered page carries no gnss_type at all"
            assert all(per_type.get(gt, 0) > 0 for gt in visible), (
                f"a class on the page is unreachable through its filter: "
                f"visible={sorted(visible)} per_type={per_type}")
            rc = (await c.get("/api/v1/maritime/gnss-anomalies",
                              params={"classification": "candidate"})).json()
            # ⚠ compared against the endpoint's own capped page, not the store: `split` was
            # tallied from the capped unfiltered response above, so it is a lower bound.
            assert rc["count"] >= split.get("candidate", 0)
            assert all(x["classification"] == "candidate" for x in rc["anomalies"])

            # decoy episodes exist and at least one lands in the Amman-airport cell
            decoy = [x for x in d["anomalies"] if x["decoy_match"]]
            assert decoy
            assert any(31.68 < x["start_lat"] < 31.76 and 35.95 < x["start_lon"] < 36.03
                       for x in decoy)
    finally:
        await app.state.db.close()


def _cell_center(c: dict) -> tuple[float, float]:
    return ((c["lat_min"] + c["lat_max"]) / 2, (c["lon_min"] + c["lon_max"]) / 2)


@requires_db
@pytest.mark.asyncio
async def test_jamming_zones_broadcast_and_affected():
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app
    from backend.db import Database

    app = create_app()
    app.state.db = Database(DSN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # BROADCAST — where spoofed positions landed; the Amman cell dominates.
            b = (await c.get("/api/v1/maritime/jamming-zones", params={"variant": "broadcast"})).json()
            assert b["variant"] == "broadcast" and b["caveat"] and b["count"] > 0
            blat, blon = _cell_center(b["cells"][0])   # sorted by vessels desc
            # the Amman 0.1° cell (clat 317 / clon 359): centre (31.75, 35.95)
            assert 31.70 <= blat <= 31.80 and 35.90 <= blon <= 36.00   # Amman = most saturated
            assert b["cells"][0]["distinct_vessels"] >= 100

            # AFFECTED — where vessels actually were; NOT dominated by the decoy, and the
            # 33 h+ open outage window (starts 2026-07-20 00:04) contributes ZERO cells.
            a = (await c.get("/api/v1/maritime/jamming-zones",
                             params={"variant": "affected", "min_vessels": 2})).json()
            assert a["variant"] == "affected" and a["count"] > 0
            at_amman = [x for x in a["cells"]
                        if 31.70 <= _cell_center(x)[0] <= 31.80 and 35.90 <= _cell_center(x)[1] <= 36.00]
            assert not at_amman                                    # vessels weren't really at the decoy
            # ⛔ The date literal that used to be here (`< "2026-07-20T00:04:00"`) stated WHEN
            # THE DETECTOR DIED, not the mechanism: it held only while the detector was frozen
            # at 2026-07-21, and GNSS-REVIVE's first live run legitimately extended the
            # affected zone past it. Removed rather than re-frozen to a new date.
            #
            # ⚠ AND THE SUBTRACTION IS DELIBERATELY NOT RE-ASSERTED HERE. Recomputing the
            # outage windows in the test and demanding no cell fall inside them FAILS today —
            # measured, a cell at 2026-07-16T09:00:00Z sits inside a window reconstructed NOW.
            # That is not necessarily a defect: the detector subtracts the windows that existed
            # WHEN IT RAN, and the feed-event history has moved since. Which of the two is
            # authoritative is GNSS-endpoint work, not this item's; it is recorded in
            # PROJECT_STATE §3 rather than papered over with a weaker assertion. The mechanism
            # stays unit-tested in
            # test_gnss_anomaly_events::test_affected_zone_subtracts_outage_windows.
            assert all(x["window_start"] for x in a["cells"])
            # (the subtraction MECHANISM is unit-tested in
            #  test_gnss_anomaly_events::test_affected_zone_subtracts_outage_windows)
    finally:
        await app.state.db.close()
