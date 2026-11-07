"""Alert feed + collective triage/case — /alerts and the two guarded writes."""

from __future__ import annotations

import asyncio
import hmac
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from ..config import settings
from ..db import (
    DB_ERRORS,
    read_latest_json,
)
from ..live import build_live_payload, iso
from .. import alerts as alert_feed
from ..country_iso import iso_codes as aoi_iso_codes, normalise as normalise_country
from ..country_iso import to_name as country_iso_name
from ..models import (
    Alert, AlertCounts, AlertDomainStat, AlertsResponse,
    TriageRequest,
    CaseItem, CaseRequest,
)

from .air import MILITARY_STALE_S
from .maritime import MARITIME_STALE_S, _composite_hulls
from .shared import _db, db_503

router = APIRouter()


# D5 — the alert feed (read-over-signals; per-family honest severity). The family/
# severity contract lives in backend/alerts.py. A defensive per-family cap keeps one
# noisy family from flooding the card grid; the `counts` stay TRUE (full totals).
ALERT_PER_FAMILY_CAP = 200


def _scoped(alerts: list[Alert], iso: str | None) -> list[Alert]:
    """Restrict a family's FULL list to one AOI country, before it is counted.

    Filtering here rather than at the end is what makes `counts` mean the same thing
    as the list: counts are computed from the full (uncapped) family list, so if the
    filter ran after capping, a country's count would silently become "up to the cap"
    instead of the truth."""
    return alerts if iso is None else [a for a in alerts if iso in a.countries]


async def _attach_triage(alerts: list, db) -> None:
    """Fill `triage`/`triage_note`/`triage_stale` from `alert_triage` (migration 051).

    ⛔ KEYED BY `notify.identity.dedup_key`, THE BOT'S OWN KEY — not a parallel one. The bot
    already answers "is this the same alert?" for its send ledger; a second definition would
    let the two disagree about what "the same thing" means, and the disagreement would be
    invisible until an analyst closed something the bot then re-sent.

    ⚠ Mutates in place and returns None: the caller's list is already ordered and capped, and
    rebuilding it here would be a second place where that order could drift."""
    if not alerts:
        return
    try:
        from ..notify.identity import change_sig, dedup_key
        keys = {}
        for a in alerts:
            try:
                k = dedup_key(a)
                keys[k] = a
                # ⚠ Published on every alert, triaged or not: a client needs them to SET a
                # state, which happens before any row exists. dedup_key rides along so the
                # client never re-derives the identity (the hand mirror had drifted on
                # maritime_single — see the Alert model note).
                a.change_sig = change_sig(a)
                a.dedup_key = k
            except Exception:            # noqa: BLE001 — one unkeyable alert must not blank the page
                continue
        if not keys:
            return
        pool = await db.pool()
        rows = await pool.fetch(
            "SELECT dedup_key, state, change_sig, note FROM alert_triage "
            "WHERE dedup_key = ANY($1::text[])", list(keys))
        for r in rows:
            a = keys.get(r["dedup_key"])
            if a is None:
                continue
            a.triage = r["state"]
            a.triage_note = r["note"]
            # ⛔ The stored signature is compared to the LIVE one. A closed alert whose thing
            # has since changed is not still closed — see migration 051's column comment.
            a.triage_stale = bool(r["change_sig"]) and r["change_sig"] != change_sig(a)
    except Exception:                    # noqa: BLE001 — see the fail-open note at the call site
        return


VALID_TRIAGE = ("reviewing", "closed")


#: ⛔ THE WRITE GUARD, AND WHAT IT IS NOT. `POST /alerts/triage` is the only mutating endpoint
#: in this API, and the server it runs on is deliberately bound to 0.0.0.0 (`server.py --host`,
#: chosen so several analysts on different machines reach one screen). Measured on the host:
#: 127.0.0.1, 10.0.0.252 and a 100.64/10 tunnel address. Without a guard, anything that can
#: reach the port can close an alert — and closing one hides it from every analyst.
#:
#: ⛔ IT IS NOT AUTHENTICATION AND MUST NOT BE DESCRIBED AS SUCH. The dashboard is a static
#: bundle delivered to a browser, so it cannot hold a secret from the person operating it:
#: anyone who can open the page can read the token out of it. What this stops is everything
#: that reaches the port WITHOUT opening the dashboard — a scanner, a stray service, another
#: device on the LAN. That is a real reduction in surface and an honest description of it.
#: ⚠ It is a SHARED passphrase for the team, matching the owner's decision that there is no
#: per-analyst identity. It identifies nobody, and the audit trail remains `alert_triage.note`.
#:
#: ⛔ FAIL CLOSED: an UNSET token refuses every write. The opposite default — "no token
#: configured, so allow" — is the failure mode this project has a written rule against, and it
#: would silently disarm the guard on exactly the deployment that forgot to configure it.
WRITE_TOKEN_HEADER = "x-atheer-write"


def _require_write_token(request: Request) -> None:
    """Refuse the write unless the caller presents the configured shared token."""
    expected = (os.environ.get("DASHBOARD_WRITE_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(
            503,
            "writes are disabled: DASHBOARD_WRITE_TOKEN is not configured. "
            "Set it in .env and restart — an unset token refuses every write rather than "
            "allowing them (fail closed).")
    got = (request.headers.get(WRITE_TOKEN_HEADER) or "").strip()
    # ⚠ compare_digest, not `!=` — a plain comparison returns early on the first differing
    # byte, which leaks the token's prefix to anyone who can time the response.
    if not got or not hmac.compare_digest(got, expected):
        raise HTTPException(403, "missing or invalid write token")


@router.post("/alerts/triage")
async def set_triage(body: TriageRequest, request: Request):
    """Set the COLLECTIVE triage state of one alert. Shared by every analyst.

    ⛔ THE FIRST WRITE ENDPOINT IN THIS API, and that is worth stating rather than
    discovering. Everything else here is read-only over a read-only store; this one mutates.
    Two consequences, both deliberate:
      * IT CARRIES NO USER IDENTITY, because this project has no user model, no auth and no
        sessions, and the owner chose to keep it that way (a shared screen, one collective
        state). ⛔ AN EARLIER VERSION OF THIS DOCSTRING SAID THE ENDPOINT WAS "SAFE ONLY WHILE
        THE API IS BOUND TO LOCALHOST" — and it never was: the server runs `--host 0.0.0.0`
        deliberately, so several analysts on different machines reach one screen. The comment
        stated a precondition that was already false, which is worse than no comment. It is
        guarded by a SHARED WRITE TOKEN now (see `_require_write_token`), which reduces the
        surface without pretending to be authentication.
      * IT WRITES ONE ROW BY PRIMARY KEY and nothing else. No bulk path, no wildcard, no
        "close everything": a single mistaken call can only lose one item's state.

    ⚠ `state=null` DELETES the row rather than storing "open" — see TriageRequest.
    ⛔ The state list is validated HERE as well as by the table's CHECK. The constraint is the
    guarantee; this is the readable error, so a typo returns 400 and not a 500 from asyncpg.
    """
    _require_write_token(request)
    key = (body.dedup_key or "").strip()
    if not key:
        raise HTTPException(400, "dedup_key is required")
    if body.state is not None and body.state not in VALID_TRIAGE:
        raise HTTPException(400, f"state must be one of {VALID_TRIAGE} or null to reopen")
    with db_503():
        pool = await request.app.state.db.pool()
        if body.state is None:
            await pool.execute("DELETE FROM alert_triage WHERE dedup_key = $1", key)
            return {"dedup_key": key, "state": None}
        # ⚠ `change_sig` is stored from the CALLER, not re-derived here: the client is holding
        # the alert it is triaging, and re-deriving would need the alert rebuilt server-side —
        # a second computation that could disagree with the one the analyst was looking at.
        await pool.execute(
            "INSERT INTO alert_triage (dedup_key, state, change_sig, note, updated_at) "
            "VALUES ($1, $2, COALESCE($3, ''), $4, now()) "
            "ON CONFLICT (dedup_key) DO UPDATE SET state = EXCLUDED.state, "
            "  change_sig = EXCLUDED.change_sig, note = EXCLUDED.note, updated_at = now()",
            key, body.state, getattr(body, "change_sig", None), body.note)
        return {"dedup_key": key, "state": body.state, "note": body.note}


def _parse_ts(v: str | None) -> datetime | None:
    """ISO string → datetime for asyncpg, or None. Never raises: see the call site."""
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


@router.post("/alerts/case")
async def set_case(body: CaseRequest, request: Request):
    """Add one alert to the SHARED case file, or remove it.

    ⛔ THE SNAPSHOT IS THE POINT. The feed is stateless, so this stores the claim as it was —
    entity, severity, and the three calibrated fields — rather than a key that would resolve to
    nothing once the window rolled past. See migration 052.
    ⚠ `caveat` is stored like the rest and is NOT optional: a lead kept for a report without
    what it does NOT mean is the failure mode models.py exists to prevent, made durable.
    ⚠ Re-adding the same key REFRESHES the snapshot deliberately — an analyst re-collecting an
    item is choosing the newer statement. The passive path (reading) never refreshes it."""
    _require_write_token(request)
    key = (body.dedup_key or "").strip()
    if not key:
        raise HTTPException(400, "dedup_key is required")
    try:
        pool = await request.app.state.db.pool()
        if body.remove:
            await pool.execute("DELETE FROM case_items WHERE dedup_key = $1", key)
            return {"dedup_key": key, "removed": True}
        if not (body.label or "").strip():
            # ⛔ A row with no label is unusable in a report and cannot be identified later.
            raise HTTPException(400, "label is required when adding")
        await pool.execute(
            "INSERT INTO case_items (dedup_key, label, domain, signal_type, severity, "
            "  headline, why, caveat, countries, alert_ts, note, added_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::text[],$10::timestamptz,$11, now()) "
            "ON CONFLICT (dedup_key) DO UPDATE SET label=EXCLUDED.label, domain=EXCLUDED.domain, "
            "  signal_type=EXCLUDED.signal_type, severity=EXCLUDED.severity, "
            "  headline=EXCLUDED.headline, why=EXCLUDED.why, caveat=EXCLUDED.caveat, "
            "  countries=EXCLUDED.countries, alert_ts=EXCLUDED.alert_ts, note=EXCLUDED.note, "
            "  added_at=now()",
            key, body.label, body.domain, body.signal_type, body.severity,
            body.headline, body.why, body.caveat, list(body.countries or []),
            # ⚠ PARSED HERE, NOT CAST IN SQL. asyncpg binds parameters by TYPE, so a `::timestamptz`
            # cast on the placeholder does not make it accept a string — it rejects with "expected a
            # datetime instance, got 'str'". A malformed stamp becomes NULL rather than a 503: the
            # item's own time is context, and losing it must not cost the analyst the evidence.
            _parse_ts(body.alert_ts), body.note)
        return {"dedup_key": key, "removed": False}
    except HTTPException:
        raise
    except DB_ERRORS as exc:
        raise HTTPException(503, f"database unavailable: {exc}") from exc


@router.get("/alerts/case", response_model=list[CaseItem])
async def get_case(request: Request):
    """The shared case file, newest first.

    ⚠ `still_live` is computed by re-deriving today's dedup keys and testing membership — it
    says whether the same thing is CURRENTLY in the feed. It is information, never a
    correction: an item that has left the window has not been disproven, and the snapshot is
    not refreshed from the live alert.
    ⚠ FAILS OPEN toward the snapshot: if the live feed cannot be read, every item reports
    `still_live=False`, which is the honest "not confirmed present" rather than a claim that
    it is gone."""
    with db_503():
        pool = await request.app.state.db.pool()
        rows = await pool.fetch(
            "SELECT dedup_key, label, domain, signal_type, severity, headline, why, caveat, "
            "       countries, alert_ts, note, added_at FROM case_items ORDER BY added_at DESC")

    live: set[str] = set()
    try:
        from ..notify.identity import dedup_key
        # ⛔ EXPLICIT DEFAULTS, BECAUSE A DIRECT CALL DOES NOT GET FASTAPI'S. The parameter
        # defaults are `Query(...)` OBJECTS — FastAPI resolves them per request, so calling the
        # function in-process passes those objects straight into the query and it fails. Caught
        # by the values it produced: every case item reported `still_live=False`, including one
        # taken from the live feed a second earlier. The fail-open path was working exactly as
        # designed and was hiding a broken call.
        current = await alerts(request=request, window_hours=24, country=None,
                               sort="severity")
        for a in current.alerts:
            try:
                live.add(dedup_key(a))
            except Exception:                            # noqa: BLE001
                continue
    except Exception:                                    # noqa: BLE001 — see the fail-open note
        live = set()

    return [
        CaseItem(
            dedup_key=r["dedup_key"], label=r["label"], domain=r["domain"],
            signal_type=r["signal_type"], severity=r["severity"], headline=r["headline"],
            why=r["why"], caveat=r["caveat"], countries=list(r["countries"] or []),
            alert_ts=r["alert_ts"].isoformat() if r["alert_ts"] else None,
            note=r["note"], added_at=r["added_at"].isoformat(),
            still_live=r["dedup_key"] in live,
        )
        for r in rows
    ]


@router.get("/alerts", response_model=AlertsResponse)
async def alerts(
    request: Request,
    window_hours: int = Query(24, ge=1, le=720),
    country: str | None = Query(
        None,
        description="Restrict to alerts TOUCHING one AOI country — ISO alpha-2 or "
                    "name, any case (SY | sy | Syria). Omit for every country, the "
                    "unchanged default. 400 if it is not one of the seven.",
    ),
    sort: str = Query(
        "severity",
        pattern="^(severity|importance)$",
        description="severity (default, unchanged — the honest threat ordering) or "
                    "importance (the country card's DISPLAY order, which lifts "
                    "military presence while every card keeps its INFO badge).",
    ),
) -> Any:
    """D5 — the monitor's live classification feed AND the dashboard's domain stats,
    in ONE envelope (a single fetch fills the resting dashboard). FOUR families read
    LIVE over existing signals — emergency squawks (air), military presence (air +
    satellite AOI passes), the maritime hull composite (≥2 pattern types), and
    implausible AIS positions — with per-family HONEST severity (see backend/alerts.py):
    emergency HIGH, military INFO, maritime = its own composite rank, implausible LOW.

    `window_hours` bounds the LIVE families: implausible fixes in the last window and
    forward satellite passes in the next window. Emergency + military-air are the
    CURRENT snapshot; the maritime composite is a standing multi-pattern picture (each
    hull carries its last-event vintage, so a 3-day-old lead reads as 3 days old, not
    dropped). `counts` are TRUE per-family totals; `alerts` is per-family capped and
    sorted most-severe first. Degrades family-by-family — never a 500.

    `country` (optional) restricts the feed to alerts TOUCHING one AOI country,
    matching ANY member of an alert's `countries` — a hull whose events span Israeli
    and Cypriot waters answers to both, because a scalar "the" country would be a
    fiction. It filters the ASSEMBLED list rather than each family's query: the
    per-family sources are heterogeneous (a JSON snapshot, two SQL reads, a view),
    and one predicate over one already-computed field cannot drift the way five
    would.

    `sort` picks the ORDER only, never the content. `severity` (default) is the
    unchanged honest threat ordering; `importance` is the country card's display
    order, a SECOND ORTHOGONAL AXIS that lifts military presence to the top while
    every card still carries `severity="info"` and its `severity_basis`. Importance
    is NOT a threat score — see backend/alerts.py IMPORTANCE_ORDER.

    ⚠ `counts` and `domains[]` are SCOPED BY `country` when it is given (DASH-3,
    reversing the DASH-1 rule that kept them global). The reason the reversal is
    safe is `countries_filtered`: it echoes the scope, so a scoped count can never
    be mistaken for a world total. Without that echo the old rule was the only
    honest option; with it, a country card that says "3 maritime leads" and lists 3
    is more honest than one that says 103 and lists 3.

    `counts` remain UNCAPPED totals within that scope — each family is filtered
    BEFORE it is counted and only then sliced to ALERT_PER_FAMILY_CAP, so a country's
    count is its truth, not "up to the cap".

    ⚠ Domain counts are scoped by CLASSIFYING POSITIONS through the shared
    classifier, never by reading a country field — the live JSON artifacts do not
    carry one (see alerts.py's module docstring). Air stays opensky-only, the same
    population as its global figure; `source` names the population on every stat so
    a country number is never read against a total from a different feed."""
    db = _db(request)
    # NB: named country_filter, NOT `iso` — `iso()` is a module-level helper used
    # further down this same function; a local of that name shadows it.
    country_filter = None
    if country is not None:
        country_filter = normalise_country(country)
        if country_filter is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown country {country!r} — expected one of "
                       f"{', '.join(aoi_iso_codes())} (ISO alpha-2 or name)",
            )
    now = time.time()
    now_dt = datetime.now(timezone.utc)
    all_alerts: list[Alert] = []
    counts = {"emergency": 0, "military": 0, "maritime": 0, "implausible": 0}
    domains: list[AlertDomainStat] = []

    # --- air: the live snapshot → emergency squawks + the air domain stat ---
    air_count, air_live, air_vintage = 0, False, None
    live_states: list[dict] = []
    try:
        live = await build_live_payload(db)
        states = live.get("states", []) or []
        live_states = states                   # kept for the country-scoped domain count
        air_count = live.get("count", len(states))
        st = live.get("snapshot_time")
        air_vintage = iso(st) if st else None
        air_live = st is not None and (now - st) < settings.stale_after_seconds
        em = _scoped(alert_feed.build_emergency_alerts(states, air_vintage, air_live),
                     country_filter)
        counts["emergency"] = len(em)
        all_alerts += em[:ALERT_PER_FAMILY_CAP]
    except HTTPException:
        pass                                  # no air snapshot — air families absent
    # ⚠ ONE POPULATION, never mixed. This count is opensky/latest.json only — the
    # SAME states the global count came from — so the country figure and the total
    # are the same population. adsbexchange (/adsb) is a DIFFERENT and larger feed;
    # a country number from one beside a total from the other would be a lie by
    # juxtaposition, so it is never reached into here. The source string says which.
    air_src = "opensky (latest.json)"
    if country_filter is not None:
        air_count = alert_feed.count_positions_in(live_states, country_filter)
        air_src += f" — positions in {country_filter}"
    domains.append(AlertDomainStat(domain="air", count=air_count, live=air_live,
                                   vintage=air_vintage, source=air_src))

    # --- military: live aircraft (airplanes.live overlay, file-based) ---
    mil_air: list[Alert] = []
    mpath = settings.output_dir / "military.json"
    mdata = await asyncio.to_thread(read_latest_json, mpath)
    if mdata and isinstance(mdata.get("states"), list):
        mlive = (now - mpath.stat().st_mtime) <= MILITARY_STALE_S
        mvint = iso(mdata["snapshot_time"]) if mdata.get("snapshot_time") else None
        if mlive:
            mil_air = _scoped(
                alert_feed.build_military_air_alerts(mdata["states"], mvint, True),
                country_filter)

    # --- military: satellites with an AOI pass in the forward window ---
    mil_sat: list[Alert] = []
    try:
        pool = await db.pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (p.norad_id) p.norad_id, i.object_name, p.start_time,
                   -- DASH-3: the pass is an INTERVAL and a PREDICTION, and both facts were
                   -- already in this table and simply not selected. `end_time` closes the
                   -- interval; `computed_at` is the OGC `resultTime` — when the TLE was
                   -- propagated — and `computed_at < start_time` is what makes Alert.
                   -- is_prediction true for this family without anyone declaring a flag.
                   p.end_time, p.computed_at,
                   p.closest_km, p.countries, p.near_countries, p.tle_epoch,
                   s.users, s.operator_country,
                   -- ALT-1: the object's own orbit, so the ground-track distance in the
                   -- summary can never be read as a range (see alerts._satellite_ar).
                   o.apogee_km, o.perigee_km,
                   -- BASELINE-1: what is USUAL for this object (migration 041), so the
                   -- alert can say whether this pass differs from its own norm instead of
                   -- reporting every predicted crossing as news.
                   -- ⚠ is_persistent rides along because a geosync object is STATIONED
                   -- over the band rather than transiting it — routine by construction,
                   -- and decided before any sample counting (pass_baseline.classify).
                   p.is_persistent,
                   b.n_passes AS bl_n, b.window_days AS bl_days,
                   b.passes_per_day AS bl_per_day, b.closest_p10 AS bl_p10,
                   b.closest_median AS bl_median, b.usual_countries AS bl_countries
            FROM satellite_passes p
            JOIN satellite_strategic s ON s.norad_id = p.norad_id
            LEFT JOIN satellite_info i ON i.norad_id = p.norad_id
            LEFT JOIN satellite_orbit o ON o.norad_id = p.norad_id
            LEFT JOIN satellite_pass_baseline b ON b.norad_id = p.norad_id
            WHERE s.users ILIKE '%Military%'
              -- ⛔ GNSS-1: NAVIGATION CONSTELLATIONS ARE EXCLUDED, and the reason is that
              -- their passes carry no information about the AOI. A GNSS satellite is
              -- ENGINEERED to cover the whole globe continuously — that is its function —
              -- so "a GPS satellite passes over Iraq" is true of every hour of every day
              -- and of every country. Reporting it as a military-presence signal made the
              -- family's count meaningless and buried the objects that do carry signal
              -- (SAR-LUPE, DMSP, CORIOLIS).
              --
              -- MEASURED on the live forward window before this change: 7 of 13 objects
              -- (53 %) were NAVSTAR/GPS. They were also the worst case for the distance
              -- defect fixed alongside this — MEO at ~20,000 km, shown as «41 كم».
              --
              -- ⚠ FILTERED ON UCS's OWN `purpose` FIELD, never on the object NAME. The
              -- name-regex version of this rule (NAVSTAR|GPS|GLONASS|...) was the obvious
              -- one and it rots: it misses new constellations, misses non-Latin naming,
              -- and silently re-admits everything the day CelesTrak changes a label.
              -- `purpose = 'Navigation/Global Positioning'` is the catalogue's own
              -- classification and covers 47 objects across Military/Commercial and
              -- Military/Government.
              AND s.purpose IS DISTINCT FROM 'Navigation/Global Positioning'
              AND p.start_time >= $1 AND p.start_time < $2
            ORDER BY p.norad_id, p.start_time
            """,
            now_dt, now_dt + timedelta(hours=window_hours),
        )
        mil_sat = _scoped(alert_feed.build_military_sat_alerts([dict(r) for r in rows]),
                          country_filter)
    except DB_ERRORS:
        pass
    counts["military"] = len(mil_air) + len(mil_sat)
    all_alerts += mil_air[:ALERT_PER_FAMILY_CAP]
    all_alerts += mil_sat[:ALERT_PER_FAMILY_CAP]

    # --- maritime: the hull composite (≥2 pattern types — the standing picture) ---
    try:
        hulls = await _composite_hulls(db, min_pattern_types=2, waters=None,
                                       limit=ALERT_PER_FAMILY_CAP)
        # IMO → MMSI is ONE-TO-MANY across a re-flag, and only one MMSI is current.
        # Resolve "the one transmitting now" = most recent fix, for every hull in ONE
        # query rather than N: the alert list is capped but still ~100 hulls.
        live_keys: dict[str, list[str]] = {}
        cand = sorted({m for h in hulls for m in
                       (i.mmsi for i in h.identities) if m})
        if cand:
            try:
                pool = await db.pool()
                seen = {r["mmsi"]: r["last_ts"] for r in await pool.fetch(
                    "SELECT mmsi, max(ts) AS last_ts FROM vessel_states "
                    "WHERE mmsi = ANY($1::text[]) GROUP BY mmsi", cand)}
            except DB_ERRORS:
                seen = {}
            for h in hulls:
                ms = [i.mmsi for i in h.identities if i.mmsi]
                # Most-recently-heard first; never-heard MMSIs sort last but are KEPT
                # (they are still the hull's history, not noise).
                live_keys[h.hull] = sorted(
                    ms, key=lambda m: (seen.get(m) is None, -(seen[m].timestamp() if seen.get(m) else 0)))
        mar = _scoped(alert_feed.build_maritime_alerts(hulls, live_keys), country_filter)
        counts["maritime"] = len(mar)
        all_alerts += mar
    except HTTPException:
        pass

    # --- MT-DETECT-3: MarineTraffic single-source events (LOW, out-of-composite) ---
    # ⛔ READ DIRECTLY FROM maritime_events, NOT through the hull composite — MT events are
    # excluded from it by design (migration 043) because a ship_id cannot stitch to a hull.
    # This is the one path by which they reach a surface at all, and it keeps them visibly
    # separate from the composite rather than blended into it.
    try:
        pool = await db.pool()
        mt_rows = await pool.fetch(
            """
            SELECT DISTINCT ON (e.ship_id, e.event_type)
                   e.ship_id, e.event_type, e.start_ts,
                   v.name, v.flag, v.country, v.near_country
            FROM maritime_events e
            LEFT JOIN LATERAL (
                SELECT name, flag, country, near_country
                FROM mt_vessel_states m
                WHERE m.ship_id = e.ship_id ORDER BY m.ts DESC LIMIT 1) v ON TRUE
            WHERE e.source = 'marinetraffic'
              AND e.classification = 'candidate'
              -- ⛔ gnss_anomaly IS DELIBERATELY EXCLUDED FROM THE ALERT SURFACE, and the
              -- measurement is why. On MarineTraffic it is overwhelmingly the AGGREGATOR'S
              -- OWN NOISE, not vessel behaviour: MT flags implausible positions at ~2x the
              -- AIS rate (1.87 % vs 0.93 %) and `suspect` at ~107x (5,024 vs 47), because
              -- its positions are merged from several feeds with differing timestamps.
              --
              -- Measured on the candidate set the moment it was wired: 3,219 gnss_anomaly
              -- events across 673 vessels against 38 zone_dwell (6 vessels) and 8
              -- rendezvous (4). Admitting it would have made 98.5 % of this family a
              -- property of the SOURCE dressed as a property of the vessels, and buried the
              -- ten observations that are actually about behaviour.
              --
              -- ⚠ The events are NOT discarded — they remain in maritime_events, source
              -- tagged and queryable. A data-quality signal about a feed belongs in a
              -- source-health surface, not in an operator's alert feed. The AIS
              -- `implausible` family already frames such a fix as "usually a VICTIM, not an
              -- actor"; on MT it is a further step removed — not even the vessel's
              -- transmitter, but the aggregator's pipeline.
              AND e.event_type IN ('rendezvous', 'zone_dwell')
              AND e.start_ts >= $1
            ORDER BY e.ship_id, e.event_type, e.start_ts DESC
            """,
            now_dt - timedelta(hours=window_hours))
        mt_alerts = _scoped(alert_feed.build_mt_alerts([dict(r) for r in mt_rows]),
                            country_filter)
        counts["maritime"] += len(mt_alerts)
        all_alerts += mt_alerts[:ALERT_PER_FAMILY_CAP]
    except DB_ERRORS:
        pass

    # --- sea liveness (last-fix vintage) + implausible positions in the window ---
    sea_count, sea_live, sea_vintage = 0, False, None
    try:
        pool = await db.pool()
        v = await pool.fetchval("SELECT max(ts) FROM vessel_states")
        sea_vintage = v.isoformat() if v is not None else None
        imp = await pool.fetch(
            """
            SELECT vs.mmsi, count(*) AS n_fixes, max(vs.ts) AS last_ts,
                   vi.name, vi.flag_country,
                   -- BE-SUMMARY-2: the three implausibility KINDS, counted separately so
                   -- the Arabic headline can name what was actually observed instead of
                   -- one vague "implausible position". Read from anomaly_flags, which
                   -- gnss_anomaly.py wrote (R1 speed / R2 inland) — no re-derivation here.
                   count(*) FILTER (WHERE 'on_land' = ANY(vs.anomaly_flags))          AS n_on_land,
                   count(*) FILTER (WHERE 'impossible_speed' = ANY(vs.anomaly_flags)) AS n_impossible_speed,
                   count(*) FILTER (WHERE 'teleport_return' = ANY(vs.anomaly_flags))  AS n_teleport,
                   -- GEOGRAPHIC attribution, aggregated over the hull's implausible
                   -- fixes in the window: a drifting spoofed track can cross waters,
                   -- so this is a set, not a scalar. Distinct from vi.flag_country,
                   -- which is registration (whose it is, not where it is).
                   array_agg(DISTINCT vs.country)
                       FILTER (WHERE vs.country IS NOT NULL) AS countries,
                   array_agg(DISTINCT vs.near_country)
                       FILTER (WHERE vs.near_country IS NOT NULL) AS near_countries
            FROM vessel_states vs
            LEFT JOIN vessel_identity vi ON vi.mmsi = vs.mmsi
            WHERE vs.position_quality = 'implausible' AND vs.ts >= $1
            GROUP BY vs.mmsi, vi.name, vi.flag_country
            ORDER BY max(vs.ts) DESC
            """,
            now_dt - timedelta(hours=window_hours),
        )
        impl_alerts = _scoped(
            alert_feed.build_implausible_alerts([dict(r) for r in imp], live=True),
            country_filter)
        counts["implausible"] = len(impl_alerts)
        all_alerts += impl_alerts[:ALERT_PER_FAMILY_CAP]
    except DB_ERRORS:
        pass
    # the live AIS snapshot count (mirrors /maritime); dark feed → count 0, and the
    # vintage above still tells the analyst when the last real fix landed.
    vpath = settings.vessels_json
    vdata = await asyncio.to_thread(read_latest_json, vpath)
    if vdata and isinstance(vdata.get("states"), list):
        sea_live = (now - vpath.stat().st_mtime) <= MARITIME_STALE_S
        sea_count = vdata.get("count", len(vdata["states"]))
    sea_src = "aisstream + vessel_states"
    if country_filter is not None:
        # vessels.json carries lat/lon and no country (same archive/DB split as air),
        # so the scoped count classifies positions through the shared classifier.
        sea_count = alert_feed.count_positions_in(
            (vdata or {}).get("states") or [], country_filter, "lat", "lon")
        sea_src += f" — positions in {country_filter}"
    domains.append(AlertDomainStat(domain="sea", count=sea_count, live=sea_live,
                                   vintage=sea_vintage, source=sea_src))

    # --- space: the tracked-TLE catalogue count (+ freshness) ---
    sat_count, sat_live, sat_vintage = 0, False, None
    try:
        pool = await db.pool()
        row = await pool.fetchrow(
            "SELECT count(*) AS n, max(fetched_at) AS f FROM satellite_elements")
        sat_count = int(row["n"] or 0)
        sat_vintage = row["f"].isoformat() if row["f"] else None
        sat_live = sat_count > 0
    except DB_ERRORS:
        pass
    sat_src = "celestrak TLE"
    if country_filter is not None:
        # ⚠ A DIFFERENT QUESTION, answered honestly rather than forced. The global
        # space number is the size of the tracked TLE CATALOGUE — a catalogue has no
        # country, and a satellite is over many. The only country-meaningful space
        # figure is "how many tracked objects have a pass over this country in the
        # window", so that is what is counted, and `source` says so plainly instead
        # of letting a reader assume it is the catalogue filtered.
        sat_src = f"satellite_passes — objects with a pass over {country_filter} in the window"
        sat_count = 0
        try:
            pool = await db.pool()
            names = [country_iso_name(country_filter)]
            sat_count = int(await pool.fetchval(
                "SELECT count(DISTINCT norad_id) FROM satellite_passes "
                "WHERE start_time >= $1 AND start_time < $2 "
                "  AND (countries && $3::text[] OR near_countries && $3::text[])",
                now_dt, now_dt + timedelta(hours=window_hours), names) or 0)
        except DB_ERRORS:
            pass
    domains.append(AlertDomainStat(domain="space", count=sat_count, live=sat_live,
                                   vintage=sat_vintage, source=sat_src))

    # TRUE totals — computed BEFORE any country filter, deliberately. A filtered
    # view must not shrink the domain picture (same rule as the per-family cap).
    counts["total"] = counts["emergency"] + counts["military"] + counts["maritime"] + counts["implausible"]
    # all_alerts is ALREADY country-scoped (each family was filtered before its
    # count), so counts and the list can never disagree.
    # ⛔ DASH-4a — COLLECTIVE TRIAGE, ATTACHED LAST. It is looked up ONE query for the whole
    # page rather than per alert: 226 alerts would otherwise be 226 round trips on a surface
    # that must feel live.
    # ⚠ FAILS OPEN, DELIBERATELY, AND THIS IS THE ONE PLACE IN THIS FILE WHERE THAT IS RIGHT.
    # If the triage store is unreachable, every alert renders as `triage=None` — i.e. OPEN, i.e.
    # "nobody has claimed this". The alternative reading — hiding or dimming an alert because
    # its state could not be read — would suppress a live signal on the strength of a failed
    # query. An analyst re-opening something already handled costs a duplicate look; an analyst
    # never seeing an emergency because a lookup timed out costs the thing this project exists
    # to prevent.
    await _attach_triage(all_alerts, db)
    order = alert_feed.sort_by_importance if sort == "importance" else alert_feed.sort_alerts
    return AlertsResponse(
        generated_at=now_dt.isoformat(),
        window_hours=window_hours,
        counts=AlertCounts(**counts),
        countries_filtered=country_filter,
        domains=domains,
        caveat=alert_feed.CAVEAT,
        alerts=order(all_alerts),
        sorted_by=sort,
    )
