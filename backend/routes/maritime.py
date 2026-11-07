"""Maritime live + detector-event readers — /maritime and /maritime/* (overlay, vessel, crossref, dark-vessels, gaps, gnss, jamming, rendezvous, identity, composite)."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from ..config import settings
from ..db import (
    DB_ERRORS,
    Database,
    read_latest_json,
)
from ..gap_detect import CANDIDATE_CAVEAT, METHOD as GAP_METHOD, reconstruct_outage_windows
from ..rendezvous import CANDIDATE_CAVEAT as RDV_CAVEAT, METHOD as RDV_METHOD
from ..identity_change import CANDIDATE_CAVEAT as IDC_CAVEAT, METHOD as IDC_METHOD
from ..gnss_anomaly_events import (
    CANDIDATE_CAVEAT as GNSS_CAVEAT, METHOD as GNSS_METHOD,
    ZONE_CELL_DEG, affected_zone_cells,
)
from ..composite import (
    CANDIDATE_CAVEAT as CMP_CAVEAT, METHOD as CMP_METHOD,
    involves_grey_flag, rank_with_reason as _rank_with_reason, underlying_rank_ord,
)
from ..imo_integrity import worst_quality as _worst_imo_quality
from ..models import (
    AlertEntityRef, PatternSpan,
    CompositeHull, CompositeIdentity, CompositeResponse, CompositeTimelineEntry, CrossMatch, CrossrefResponse, CrossrefSummary,
    DarkVessel, DarkVesselsResponse,
    GapEvent, GapsResponse, GnssAnomaliesResponse, GnssEvent,
    IdentityChangeEvent, IdentityChangesResponse, JammingZonesResponse, ZoneCell,
    MaritimeResponse, RendezvousEvent, RendezvousResponse, RendezvousVessel, SourceVintage,
    Vessel, VesselLiveResponse,
)

from .shared import _db, _iso, db_503

router = APIRouter()


MARITIME_STALE_S = settings.maritime_stale_s  # AIS cadence 30 s; allow a few misses


_MARITIME_SOURCES = {
    # source → (live snapshot file, DB table for the data-vintage read)
    "aisstream": (lambda: settings.vessels_json, "vessel_states"),
    "marinetraffic": (lambda: settings.mt_vessels_json, "mt_vessel_states"),
}


def _read_snapshot(src: str) -> tuple[dict[str, Any], float] | None:
    """One source's live snapshot file + its age in seconds, or None if absent
    or unreadable.

    THE single place a live maritime snapshot is read. `/maritime` builds the whole
    overlay from it and `/maritime/vessel` (DASH-4) pulls one row out of it, so both
    see the byte-identical dicts and there is exactly ONE definition of what a live
    vessel is. Callers apply the freshness rule themselves against the shared
    MARITIME_STALE_S, because they differ in what they do with a STALE file:
    `/maritime` still needs its age to report `age_seconds` honestly, while the
    single-vessel path treats stale as not-live and falls through to last-known.

    Staleness is judged on the FILE's mtime, not on any timestamp inside it — a
    collector that dies mid-write leaves a parseable file full of old fixes, and
    only the mtime catches that."""
    path = _MARITIME_SOURCES[src][0]()
    data = read_latest_json(path)
    if not data or not isinstance(data.get("states"), list):
        return None
    return data, time.time() - path.stat().st_mtime


@router.get("/maritime", response_model=MaritimeResponse)
async def maritime(
    request: Request,
    source: str = Query("aisstream", pattern="^(aisstream|marinetraffic|all)$"),
) -> Any:
    """Live maritime overlay from the collectors. File-based; speed is knots
    (AIS-native). available=false when stale/off — never 500. LIMITS: terrestrial
    AIS only (~40-60 NM off shore); self-reported and spoofable.

    `source` (additive, migration 032) selects the feed. DEFAULT 'aisstream' keeps
    the pre-032 behavior byte-for-byte. 'marinetraffic' serves the browser-scraped
    MarineTraffic fleet (ship_id-keyed, usually no mmsi); 'all' merges both, each
    vessel carrying its own `source`.

    MarineTraffic is a PERIODIC scrape, not a continuous feed, so unlike AIS it has
    a DB FALLBACK: when no fresh `marinetraffic_output/vessels.json` exists (the
    collector isn't running), the latest position per ship_id from mt_vessel_states
    is served instead — the stored fleet stays visible rather than vanishing. Each
    such vessel carries its own (older) `timestamp`, so the frontend shows its true
    age. `data_vintage` = max(ts) over the selected source's table(s). Guarded: DB
    down → fallback empty / data_vintage None, never an error."""
    picked = ["aisstream", "marinetraffic"] if source == "all" else [source]
    src_maxts = await _maritime_source_maxts(request, picked)   # per-source store freshness
    contrib = {s: 0 for s in picked}                            # rows each source adds HERE

    states: list[dict[str, Any]] = []
    count = 0
    snapshot_time: int | None = None
    fresh_age: float | None = None    # freshest age among fresh files (available case)
    seen_age: float | None = None     # min age among present files (stale case honesty)
    mt_fresh = False                  # did a fresh MarineTraffic FILE contribute?
    for src in picked:
        snap = await asyncio.to_thread(_read_snapshot, src)  # the one snapshot reader (shared with DASH-4)
        if snap is None:
            continue
        data, age = snap
        seen_age = age if seen_age is None else min(seen_age, age)
        st = data.get("snapshot_time")
        if st is not None:
            snapshot_time = st if snapshot_time is None else max(snapshot_time, st)
        if age > MARITIME_STALE_S:
            continue  # present but stale — its states are not live
        n = len(data["states"])
        states.extend(data["states"])
        count += data.get("count", n)
        contrib[src] += n
        fresh_age = age if fresh_age is None else min(fresh_age, age)
        if src == "marinetraffic":
            mt_fresh = True

    # MarineTraffic DB fallback: no live scrape file ⇒ show the stored fleet (may be STALE;
    # flag, never drop — the per-source vintage below marks its age; consumption decides).
    if "marinetraffic" in picked and not mt_fresh:
        mt_states = await _mt_latest_from_db(request)
        states.extend(mt_states)
        count += len(mt_states)
        contrib["marinetraffic"] += len(mt_states)

    # Per-source freshness (MT-FRESH). data_vintage = OLDEST contributing source's max ts,
    # so a blend can never over-claim freshness; sources[] carries the full per-source truth.
    now = datetime.now(timezone.utc)
    src_vintages: list[SourceVintage] = []
    for s in picked:
        lf = src_maxts.get(s)
        src_age = (now - lf).total_seconds() if lf is not None else None
        src_vintages.append(SourceVintage(
            source=s, last_fix=lf.isoformat() if lf is not None else None,
            age_seconds=round(src_age, 1) if src_age is not None else None,
            rows=contrib[s], stale=(src_age is not None and src_age > MARITIME_STALE_S)))
    contributing = [src_maxts[s] for s in picked if contrib[s] > 0 and src_maxts.get(s) is not None]
    dv = min(contributing) if contributing else min(
        (v for v in src_maxts.values() if v is not None), default=None)
    data_vintage = dv.isoformat() if dv is not None else None

    if not states:
        return MaritimeResponse(
            available=False, snapshot_time=snapshot_time,
            age_seconds=round(seen_age, 1) if seen_age is not None else None,
            data_vintage=data_vintage, sources=src_vintages)
    return MaritimeResponse(
        available=True,
        snapshot_time=snapshot_time,
        age_seconds=round(fresh_age, 1) if fresh_age is not None else None,
        count=count,
        data_vintage=data_vintage,
        sources=src_vintages,
        states=states,
    )


# MarineTraffic is periodic — surface its stored fleet (latest fix per ship_id)
# when no fresh scrape file exists, so the source stays visible on the map.
MT_DB_LOOKBACK_DAYS = settings.mt_db_lookback_days


_MT_LATEST_SQL = """
SELECT DISTINCT ON (ship_id)
    ship_id, mmsi, latitude, longitude, speed, course, heading, nav_status,
    imo, name, callsign, ship_type_code, ship_type, destination, draught, flag, ts
FROM mt_vessel_states
WHERE ts > now() - ($1::int * INTERVAL '1 day')
ORDER BY ship_id, ts DESC
LIMIT $2
"""


async def _mt_latest_from_db(request: Request) -> list[dict[str, Any]]:
    """Latest positioned fix per MarineTraffic ship_id → Vessel dicts. Guarded:
    any DB error → [] (the overlay degrades to empty, never a 500)."""
    try:
        pool = await _db(request).pool()
        rows = await pool.fetch(_MT_LATEST_SQL, MT_DB_LOOKBACK_DAYS, settings.mt_latest_limit)
    except DB_ERRORS:
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["latitude"] is None or r["longitude"] is None:
            continue
        out.append({
            "mmsi": r["mmsi"], "ship_id": r["ship_id"],
            "lat": r["latitude"], "lon": r["longitude"],
            "speed": r["speed"], "course": r["course"], "heading": r["heading"],
            "nav_status": r["nav_status"],
            "timestamp": r["ts"].isoformat() if r["ts"] is not None else None,
            "imo": r["imo"], "name": r["name"], "callsign": r["callsign"],
            "ship_type_code": r["ship_type_code"], "ship_type": r["ship_type"],
            "destination": r["destination"], "draught": r["draught"],
            "flag": r["flag"], "source": "marinetraffic", "data_origin": "terrestrial",
        })
    return out


async def _maritime_source_maxts(request: Request, sources: list[str]) -> dict[str, Any]:
    """Per-source freshest ts — max(ts) over EACH selected source's table (MT-FRESH).
    Returned per source (not collapsed to one max) so a blended payload can report each
    feed's own freshness honestly. ts-indexed → instant. Guarded: any DB error → all None
    (never an error on this overlay read path)."""
    out: dict[str, Any] = {s: None for s in sources}
    try:
        pool = await _db(request).pool()
        for s in sources:
            out[s] = await pool.fetchval(f"SELECT max(ts) FROM {_MARITIME_SOURCES[s][1]}")
        return out
    except DB_ERRORS:
        return {s: None for s in sources}


# --- DASH-4: one vessel's live payload, keyed by MMSI ------------------------------- #
# The contract fields whose NULL means "the device did not report it". ship_id and flag
# are excluded on purpose: they are null for EVERY AIS vessel by source shape (measured
# 0/556 in the live snapshot — ship_id is MarineTraffic's key, flag is its field), so
# listing them as "not reported" would misdescribe a structural null as device silence.
_VESSEL_REPORTABLE = (
    "lat", "lon", "speed", "course", "heading", "nav_status", "timestamp",
    "imo", "name", "callsign", "ship_type_code", "ship_type", "destination", "draught",
)


# Last stored fix for one MMSI. Index-backed on (mmsi, ts) — measured 0.4 ms execution
# against 1.45 M rows, so this is a single-row read, NOT a fleet scan.
_VESSEL_LAST_FIX_SQL = """
SELECT mmsi, ts, latitude, longitude, speed, course, heading, nav_status,
       imo, name, callsign, ship_type_code, ship_type, destination, draught,
       source, data_origin
FROM vessel_states
WHERE mmsi = $1
ORDER BY ts DESC
LIMIT 1
"""


# The static identity spine. Read, never recomputed — imo_quality/flag derivation belong
# to their own paths (/search/vessel), not to this one.
_VESSEL_IDENTITY_SQL = """
SELECT mmsi, name, imo, callsign, ship_type, ship_type_code, identity_updated_at
FROM vessel_identity
WHERE mmsi = $1
"""


def _not_reported(v: dict[str, Any]) -> list[str]:
    """Contract fields that are NULL because nothing reported them — in contract order,
    so a client can render them as a stable list."""
    return [f for f in _VESSEL_REPORTABLE if v.get(f) is None]


async def _maritime_vessel_by_ship_id(request: Request, key: str) -> Any:
    """ONE MarineTraffic vessel by ship_id — the same `Vessel` contract, same outcomes.

    ⛔ NO SECOND DERIVATION, the rule this endpoint already lives by: the row is shaped by
    the SAME projection `_mt_latest_from_db` uses for the fleet overlay, so one vessel and
    the whole fleet can never disagree about a field.

    ⚠ TWO OUTCOMES, NOT THREE. MarineTraffic has no live snapshot file — it is a periodic
    scrape — so there is no `live` state to report here; the freshest stored fix IS the
    answer, and its age is returned so a stale one reads as stale. Inventing a `live` verdict
    from a scrape cadence would assert a currency the source does not have."""
    if not key:
        raise HTTPException(status_code=404, detail="unknown ship_id: empty")
    try:
        pool = await _db(request).pool()
        r = await pool.fetchrow(
            "SELECT DISTINCT ON (ship_id) ship_id, mmsi, latitude, longitude, speed, "
            "course, heading, nav_status, imo, name, callsign, ship_type_code, ship_type, "
            "destination, draught, flag, ts FROM mt_vessel_states "
            "WHERE ship_id = $1 ORDER BY ship_id, ts DESC", key)
        n_fixes = await pool.fetchval(
            "SELECT count(*) FROM mt_vessel_states WHERE ship_id = $1", key) if r else 0
    except DB_ERRORS as exc:
        raise HTTPException(
            status_code=503,
            detail="vessel store unreachable — cannot say whether this ship_id is known") from exc
    if r is None or r["latitude"] is None or r["longitude"] is None:
        raise HTTPException(status_code=404, detail=f"unknown ship_id: {key}")

    v = Vessel(**{
        "mmsi": r["mmsi"], "ship_id": r["ship_id"],
        "lat": r["latitude"], "lon": r["longitude"],
        "speed": r["speed"], "course": r["course"], "heading": r["heading"],
        "nav_status": r["nav_status"],
        "timestamp": r["ts"].isoformat() if r["ts"] is not None else None,
        "imo": r["imo"], "name": r["name"], "callsign": r["callsign"],
        "ship_type_code": r["ship_type_code"], "ship_type": r["ship_type"],
        "destination": r["destination"], "draught": r["draught"],
        "flag": r["flag"], "source": "marinetraffic", "data_origin": "terrestrial",
    })
    return VesselLiveResponse(
        mmsi=r["mmsi"] or key, state="last_known", vessel=v,
        age_seconds=_fix_age_seconds(v.timestamp), n_fixes=n_fixes,
        not_reported=_not_reported(v.model_dump()),
    )


@router.get("/maritime/vessel", response_model=VesselLiveResponse)
async def maritime_vessel(
    request: Request,
    mmsi: str | None = Query(None, min_length=1, max_length=16,
                             description="one MMSI, exact (the AIS track)"),
    ship_id: str | None = Query(None, min_length=1, max_length=16,
                                description="one MarineTraffic ship_id, exact. EXACTLY "
                                            "one of mmsi/ship_id."),
) -> Any:
    """ONE vessel's live payload — the same `Vessel` contract `/maritime` serves, for a
    single MMSI (DASH-4).

    WHY IT EXISTS. DASH-2f measured the asymmetry: aircraft and satellites had a
    single-entity route to their LIVE contract, vessels did not, so the home's left panel
    fell back to the SUMMARY contract and honestly named the four fields it could not
    reach (heading · destination · nav_status · draught). This serves all of them.

    ⛔ NO SECOND DERIVATION. For a live vessel this returns the SAME dict `/maritime`
    reads from the SAME snapshot, through the SAME `Vessel` model — not a re-query, not a
    re-shape. `_read_snapshot` is the one reader both use. A test asserts the two are
    field-for-field identical for the same MMSI, so drift is caught rather than trusted.

    ⛔ NO FLEET FETCH. Measured before building: the live path is one 240 KB file parsed
    in 1.2 ms (the snapshot is a single file — there is no way to read one row from it
    without parsing it, and this is the same read `/maritime` already performs), then one
    dict is selected and exactly ONE `Vessel` is constructed — cheaper than `/maritime`,
    which constructs ~550. The last-known path is one index-backed row, 0.4 ms against
    1.45 M rows. Neither path loads a fleet to return one hull.

    THREE HONEST OUTCOMES:
      · live       — in the current snapshot, inside the 180 s window
      · last_known — MMSI known, NOT transmitting now; last stored fix + its age
      · 404        — MMSI never seen. An explicit error, never an empty object

    ⚠ AIS-KEYED BY CONSTRUCTION. MMSI addresses the aisstream feed; the MarineTraffic
    fleet is `ship_id`-keyed and usually carries no MMSI, so it is not searched here. That
    is the source's shape, not a limitation being hidden — `/maritime?source=marinetraffic`
    remains the way to that fleet.

    ⚠ NOTHING IS CLASSIFIED OR ENRICHED HERE. `source` and `data_origin` are carried
    through as stored, never fabricated. `position_quality` is deliberately absent: it is
    not part of the `Vessel` contract, and adding it would be exactly the second shape this
    endpoint exists to avoid — it is served, already computed, by `/search/vessel`."""
    # ⛔ MT-DETECT-3 — `ship_id` IS A SECOND IDENTIFIER SPACE, NOT A SECOND SPELLING OF MMSI.
    # It exists because MarineTraffic-derived events (migration 043) name a ship_id, and an
    # alert whose entity cannot be opened is worse than no alert: it looks actionable and is
    # not. Measured before building: /maritime/vessel?mmsi=<ship_id> returned 404 for every
    # MT event's entity, so the alert families that carry them had no destination at all.
    #
    # ⚠ EXACTLY ONE, ENFORCED. Accepting both would force a precedence rule between two
    # namespaces that can collide numerically, and silently answering about the wrong vessel
    # is the failure this refuses.
    # ⚠ 422, NOT 400 — preserving the contract this endpoint already had. Before ship_id
    # existed, `mmsi` was required and FastAPI answered a missing one with 422; making both
    # optional moved that check into the handler, and answering 400 would have silently
    # changed an existing client-visible response for the no-params case (caught by
    # test_blank_mmsi_is_rejected). 422 also fits: each parameter is individually valid, it
    # is their COMBINATION that is not.
    if (mmsi is None) == (ship_id is None):
        raise HTTPException(status_code=422,
                            detail="pass exactly one of mmsi or ship_id")

    if ship_id is not None:
        return await _maritime_vessel_by_ship_id(request, ship_id.strip())

    key = (mmsi or "").strip()   # both-absent falls to the empty-404 below
    if not key:
        raise HTTPException(status_code=404, detail="unknown MMSI: empty")

    # --- live: the same snapshot /maritime reads, one row out of it ------------------ #
    snap = await asyncio.to_thread(_read_snapshot, "aisstream")
    if snap is not None:
        data, age = snap
        if age <= MARITIME_STALE_S:      # same freshness rule as /maritime
            for row in data["states"]:
                if row.get("mmsi") == key:
                    v = Vessel(**row)
                    return VesselLiveResponse(
                        mmsi=key, state="live", vessel=v,
                        age_seconds=_fix_age_seconds(v.timestamp),
                        snapshot_time=data.get("snapshot_time"),
                        snapshot_age_seconds=round(age, 1),
                        not_reported=_not_reported(v.model_dump()),
                    )

    # --- last known: one indexed fix + the static spine, two named vintages ---------- #
    try:
        pool = await _db(request).pool()
        fix = await pool.fetchrow(_VESSEL_LAST_FIX_SQL, key)
        ident = await pool.fetchrow(_VESSEL_IDENTITY_SQL, key)
        n_fixes = await pool.fetchval(
            "SELECT count(*) FROM vessel_states WHERE mmsi = $1", key) if fix else 0
    except DB_ERRORS as exc:
        raise HTTPException(
            status_code=503,
            detail="vessel store unreachable — cannot say whether this MMSI is known") from exc

    if fix is None and ident is None:
        raise HTTPException(status_code=404, detail=f"unknown MMSI: {key}")

    # Dynamic + voyage from the ONE stored fix (stamped `timestamp`); static identity from
    # the spine (stamped `identity_as_of`). Not blended — AIS itself separates them, and
    # the latest position report carries no statics for ~79 % of vessels. Measured: the
    # spine dominates (coalesce(fix, spine) resolves the same 2,835 names as the spine
    # alone), so statics are single-sourced rather than merged per field.
    #
    # ship_id / flag stay None: null for every AIS vessel in the live snapshot (0/556), so
    # filling them here would make last_known disagree with live — the drift this endpoint
    # is built to prevent. flag_country in the spine is a MID-DERIVED country name, a
    # different thing from the contract's source-reported `flag`; it is NOT substituted.
    v = Vessel(
        mmsi=key, ship_id=None, flag=None,
        lat=fix["latitude"] if fix else None,
        lon=fix["longitude"] if fix else None,
        speed=fix["speed"] if fix else None,
        course=fix["course"] if fix else None,
        heading=fix["heading"] if fix else None,
        nav_status=fix["nav_status"] if fix else None,
        destination=fix["destination"] if fix else None,
        draught=fix["draught"] if fix else None,
        timestamp=fix["ts"].isoformat() if fix and fix["ts"] is not None else None,
        name=ident["name"] if ident else None,
        imo=ident["imo"] if ident else None,
        callsign=ident["callsign"] if ident else None,
        ship_type=ident["ship_type"] if ident else None,
        ship_type_code=ident["ship_type_code"] if ident else None,
        source=(fix["source"] if fix and fix["source"] else "aisstream"),
        data_origin=(fix["data_origin"] if fix and fix["data_origin"] else "terrestrial"),
    )
    iu = ident["identity_updated_at"] if ident else None
    return VesselLiveResponse(
        mmsi=key, state="last_known", vessel=v,
        age_seconds=_fix_age_seconds(v.timestamp),
        identity_as_of=iu.isoformat() if iu is not None else None,
        not_reported=_not_reported(v.model_dump()),
        n_fixes=n_fixes or 0,
    )


def _fix_age_seconds(ts: str | None) -> float | None:
    """now − a fix timestamp, seconds. None when there is no timestamp to age."""
    if not ts:
        return None
    try:
        return round((datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds(), 1)
    except ValueError:
        return None


CROSSREF_CAVEAT = (
    "Derived AIS×MarineTraffic links — a LEAD, not proof. MarineTraffic carries no "
    "MMSI, so links are inferred from name (anchor) + position-over-time + dimensions; "
    "'weak'/ambiguous rows are unconfirmed, and a dimension 'discrepancy' at one "
    "location is a data-quality or possible-spoof signal, not a verdict."
)


# Summary is computed over ALL links (independent of the display limit); a feed
# link (NULL confidence) is a confirmed direct link and always counts as matched.
_CROSSREF_SUMMARY_SQL = """
SELECT
  count(*)                                                              AS mt_matched,
  count(*) FILTER (WHERE match_confidence IN ('strong','probable')
                      OR link_source = 'marinetraffic-feed')            AS matched,
  count(*) FILTER (WHERE match_confidence = 'strong')                   AS strong,
  count(*) FILTER (WHERE match_confidence = 'probable')                 AS probable,
  count(*) FILTER (WHERE match_confidence = 'weak')                     AS weak,
  count(*) FILTER (WHERE match_ambiguous)                               AS ambiguous,
  count(*) FILTER (WHERE match_reason LIKE '%discrepancy%')             AS discrepancies
FROM vessel_xref
WHERE mmsi IS NOT NULL
"""


# The displayed matches — confidence-filtered (feed links always shown) + limited.
_CROSSREF_SQL = """
SELECT ship_id, mmsi, name, flag, link_source,
       match_confidence, match_score, match_distance_km, match_dt_minutes,
       match_ambiguous, match_reason
FROM vessel_xref
WHERE mmsi IS NOT NULL
  AND (link_source <> 'ais-correlation' OR match_confidence = ANY($1::text[]))
ORDER BY (link_source = 'marinetraffic-feed') DESC,
         (match_confidence = 'strong') DESC NULLS LAST,
         match_score DESC NULLS LAST, name
LIMIT $2
"""


@router.get("/maritime/crossref", response_model=CrossrefResponse)
async def maritime_crossref(
    request: Request,
    min_confidence: str = Query("weak", pattern="^(strong|probable|weak)$"),
    limit: int = Query(500, ge=1, le=5000),
) -> Any:
    """AIS × MarineTraffic cross-source view (migration 034). Returns the derived
    identity links (`vessel_xref`) with their confidence + evidence, a coverage
    summary (how many MarineTraffic vessels are matched vs MarineTraffic-only —
    computed over ALL links, not just the displayed page), and the flagged
    discrepancies. Computed offline by `tracker_marinetraffic.py correlate`; this
    endpoint only reads. available=false when nothing is computed / DB down —
    never a 500."""
    allowed = {"strong": ["strong"], "probable": ["strong", "probable"],
               "weak": ["strong", "probable", "weak"]}[min_confidence]
    try:
        pool = await _db(request).pool()
        s = await pool.fetchrow(_CROSSREF_SUMMARY_SQL)
        mt_vessels = await pool.fetchval("SELECT count(DISTINCT ship_id) FROM mt_vessel_states")
        rows = await pool.fetch(_CROSSREF_SQL, allowed, limit)
    except DB_ERRORS:
        return CrossrefResponse(available=False, caveat=CROSSREF_CAVEAT)

    matches = [
        CrossMatch(
            ship_id=r["ship_id"], mmsi=r["mmsi"], name=r["name"], flag=r["flag"],
            confidence=r["match_confidence"], score=r["match_score"],
            distance_km=r["match_distance_km"], dt_minutes=r["match_dt_minutes"],
            ambiguous=bool(r["match_ambiguous"]),
            discrepancy=bool(r["match_reason"] and "discrepancy" in r["match_reason"]),
            reason=r["match_reason"], link_source=r["link_source"],
        )
        for r in rows
    ]
    mt_matched = s["mt_matched"] or 0
    summary = CrossrefSummary(
        matched=s["matched"] or 0, strong=s["strong"] or 0, probable=s["probable"] or 0,
        weak=s["weak"] or 0, ambiguous=s["ambiguous"] or 0,
        discrepancies=s["discrepancies"] or 0,
        mt_vessels=mt_vessels or 0, mt_matched=mt_matched,
        mt_only=max(0, (mt_vessels or 0) - mt_matched),
    )
    return CrossrefResponse(
        available=mt_matched > 0,
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=summary, caveat=CROSSREF_CAVEAT, matches=matches,
    )


DARK_CAVEAT = (
    "Dark-vessel events from GFW SATELLITE AIS — a LEAD, not proof. A gap can be a "
    "receiver outage or a legitimate silence; 'intentional' is GFW's own likelihood "
    "flag, not a verdict. This fills the blind spot free terrestrial AIS leaves in the "
    "Persian Gulf / Red Sea / open ocean. Events whose reported position falls ON LAND "
    "are flagged (on_land) as a SUSPECT / spoofed AIS fix — a signal, not a location; a "
    "coarse land mask can also flag a berth at the coastal edge, so it is not a verdict."
)


# Query regions as GeoJSON polygons; 'all' spans the eastern-Med + Red Sea + Gulf.
_DARK_REGIONS: dict[str, dict[str, Any]] = {
    "gulf": {"type": "Polygon", "coordinates": [[[48, 24], [57, 24], [57, 30], [48, 30], [48, 24]]]},
    "redsea": {"type": "Polygon", "coordinates": [[[32, 12], [44, 12], [44, 30], [32, 30], [32, 12]]]},
    "all": {"type": "Polygon", "coordinates": [[[25, 11], [60, 11], [60, 42], [25, 42], [25, 11]]]},
}


# GFW is historical + rate-limited + ~seconds per call, so cache by (region, months, limit).
_DARK_CACHE: dict[tuple, tuple[float, DarkVesselsResponse]] = {}


_DARK_TTL_S = settings.dark_ttl_s


@router.get("/maritime/dark-vessels", response_model=DarkVesselsResponse)
async def maritime_dark_vessels(
    region: str = Query("all", pattern="^(gulf|redsea|all)$"),
    months: int = Query(6, ge=1, le=24),
    # 50 is the safe default: GFW's events API can return an empty page at limit=100
    # over the large 'all' polygon (observed), whereas ≤50 is reliable across regions.
    limit: int = Query(50, ge=1, le=200),
) -> Any:
    """GFW SATELLITE-AIS dark-vessel events (a vessel that stopped transmitting) over
    the last `months` in `region` — filling the blind spot free terrestrial AIS leaves
    in the Gulf / Red Sea / open ocean. A LEAD, not proof. Cached (GFW is historical +
    rate-limited). available=false (with `reason`) when GFW_API_TOKEN is unset or the
    API is unreachable — never a 500."""
    from global_land_mask import globe

    from maritime.adapters.gfw import GfwAdapter

    adapter = GfwAdapter()
    if not adapter.enabled:
        return DarkVesselsResponse(available=False, region=region, caveat=DARK_CAVEAT,
                                   reason="GFW_API_TOKEN not set")

    key = (region, months, limit)
    now = time.time()
    hit = _DARK_CACHE.get(key)
    if hit and now - hit[0] < _DARK_TTL_S:
        return hit[1]

    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=months * 31)).isoformat()
    end = today.isoformat()
    try:
        gaps = await adapter.find_ais_gaps(start=start, end=end,
                                           region=_DARK_REGIONS[region], limit=limit)
    except Exception:  # noqa: BLE001 — the overlay degrades, never 500s
        return DarkVesselsResponse(available=False, region=region, caveat=DARK_CAVEAT,
                                   reason="GFW API unreachable")

    # positioned events only (needed to place a map marker), deduped on the exact
    # event (GFW can return the same one twice), most-recent first
    seen: set[tuple] = set()
    vessels: list[DarkVessel] = []
    for g in gaps:
        lat, lon = g.get("lat"), g.get("lon")
        if lat is None or lon is None:
            continue
        mmsi = g.get("mmsi")
        # SHIP stations only — MMSI first digit 2-7 (ITU). Aids-to-navigation (99x),
        # SAR aircraft (111x), base/coast stations (00x) and test transmitters are NOT
        # vessels and legitimately sit ON LAND (a coastal beacon, an airport, an inland
        # test rig). Drop them — that is NOISE, not a spoof signal.
        if not (isinstance(mmsi, str) and mmsi[:1] in "234567"):
            continue
        # A ship-station reporting an ON-LAND position is a likely SPOOFED / erroneous
        # AIS fix. We SURFACE it (on_land flag) as its own signal rather than hiding it —
        # a distinct suspect layer on the map. bool() unwraps the numpy scalar.
        on_land = bool(globe.is_land(lat, lon))
        k = (mmsi, g.get("start"), round(lat, 3), round(lon, 3))
        if k in seen:
            continue
        seen.add(k)
        vessels.append(DarkVessel(
            mmsi=g.get("mmsi"), name=g.get("name"), flag=g.get("flag"),
            lat=lat, lon=lon, start=g.get("start"), end=g.get("end"),
            duration_h=g.get("duration_h"), intentional=g.get("intentional"),
            distance_km=g.get("distance_km"), on_land=on_land,
        ))
    vessels.sort(key=lambda v: v.start or "", reverse=True)
    resp = DarkVesselsResponse(
        available=len(vessels) > 0, region=region,
        generated_at=datetime.now(timezone.utc).isoformat(),
        count=len(vessels), suspect=sum(v.on_land for v in vessels),
        caveat=DARK_CAVEAT, vessels=vessels,
    )
    _DARK_CACHE[key] = (now, resp)
    return resp


@router.get("/maritime/gaps", response_model=GapsResponse)
async def maritime_gaps(
    request: Request,
    classification: str = Query("candidate", pattern="^(candidate|edge|stationary|outage-excluded)$"),
    min_hours: float = Query(1.0, ge=0.0, le=720.0),
    limit: int = Query(200, ge=1, le=1000),
) -> Any:
    """Behavioural analysis A1 — AIS-gap events (migration 024), joined to the
    identity spine (M2b name/imo, M2a flag) and M1 geo (whose waters it went dark
    in). Default classification='candidate' — a gap that survived confound removal
    (not a feed outage, vessel underway, in dense coverage). Ordered by duration
    (longest first = most actionable). min_hours filters to the long-gap tail.

    HONEST LIMITATION (always returned in `caveat`): a candidate is an
    INVESTIGATION LEAD, never proof of intent — terrestrial AIS is spoofable and
    coverage is imperfect. available=false when detection has not run."""
    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            """
            SELECT e.mmsi, e.imo, e.start_ts, e.end_ts, e.duration_s,
                   e.start_lat, e.start_lon, e.end_lat, e.end_lon,
                   e.classification, e.method, e.detail,
                   vi.name, vi.flag_country, vi.ship_type
            FROM maritime_events e
            LEFT JOIN vessel_identity vi ON vi.mmsi = e.mmsi
            WHERE e.source = 'aisstream' AND e.event_type = 'ais_gap' AND e.classification = $1
              AND (e.duration_s IS NULL OR e.duration_s >= $2)
            ORDER BY e.duration_s DESC NULLS FIRST
            LIMIT $3
            """,
            classification, min_hours * 3600.0, limit,
        )


    events = []
    for r in rows:
        d = r["detail"]
        d = json.loads(d) if isinstance(d, str) else (d or {})
        events.append(GapEvent(
            mmsi=r["mmsi"], imo=r["imo"],
            name=r["name"] or d.get("name"),
            flag_country=r["flag_country"] or d.get("flag_country"),
            ship_type=r["ship_type"],
            start_ts=_iso(r["start_ts"]), end_ts=_iso(r["end_ts"]),
            duration_s=r["duration_s"],
            start_lat=r["start_lat"], start_lon=r["start_lon"],
            end_lat=r["end_lat"], end_lon=r["end_lon"],
            start_country=d.get("start_country"),
            start_near_country=d.get("start_near_country"),
            distance_at_start_km=d.get("distance_at_start_km"),
            speed_at_last_fix=d.get("speed_at_last_fix"),
            nav_status=d.get("nav_status"),
            coverage_cell_density=d.get("coverage_cell_density"),
            classification=r["classification"], method=r["method"],
        ))
    return GapsResponse(
        available=bool(events), count=len(events),
        classification=classification, method=GAP_METHOD,
        caveat=CANDIDATE_CAVEAT, events=events,
    )


@router.get("/maritime/gnss-anomalies", response_model=GnssAnomaliesResponse)
async def maritime_gnss_anomalies(
    request: Request,
    gnss_type: str | None = Query(None, pattern="^(individual|area|glitch)$"),
    classification: str | None = Query(None, pattern="^(candidate|area-jamming|glitch)$"),
    limit: int = Query(1000, ge=1, le=5000),
) -> Any:
    """GNSS-anomaly episodes (G4a, maritime_events event_type='gnss_anomaly'), joined
    to the identity spine. Per-vessel implausible episodes classified individual
    (a real vessel spoofed/jammed — an investigation lead) / area (an environmental-
    jamming victim) / glitch (a bad/test transmitter). start_lat/start_lon is the first
    implausible fix (the decoy landing for decoy episodes, e.g. the Amman cell);
    affected_lat/lon is the inferred real position.

    HONEST LIMITATION (always in `caveat`): implausibility can be deliberate spoofing,
    area jamming, or a receiver fault — a spoofed vessel is usually a VICTIM, not an
    actor. available=false when detection has not run."""
    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            """
            SELECT e.mmsi, e.imo, e.start_ts, e.end_ts, e.duration_s,
                   e.start_lat, e.start_lon, e.end_lat, e.end_lon,
                   e.classification, e.method, e.detail,
                   vi.name, vi.flag_country, vi.ship_type
            FROM maritime_events e
            LEFT JOIN vessel_identity vi ON vi.mmsi = e.mmsi
            WHERE e.source = 'aisstream' AND e.event_type = 'gnss_anomaly'
              AND ($1::text IS NULL OR e.detail->>'gnss_type' = $1)
              AND ($2::text IS NULL OR e.classification = $2)
            ORDER BY e.start_ts DESC
            LIMIT $3
            """,
            gnss_type, classification, limit,
        )


    anomalies = []
    for r in rows:
        d = r["detail"]
        d = json.loads(d) if isinstance(d, str) else (d or {})
        anomalies.append(GnssEvent(
            mmsi=r["mmsi"], imo=r["imo"], name=r["name"],
            flag_country=r["flag_country"], ship_type=r["ship_type"],
            start_ts=_iso(r["start_ts"]), end_ts=_iso(r["end_ts"]),
            duration_s=r["duration_s"],
            start_lat=r["start_lat"], start_lon=r["start_lon"],
            end_lat=r["end_lat"], end_lon=r["end_lon"],
            classification=r["classification"], gnss_type=d.get("gnss_type"),
            decoy_match=d.get("decoy_match"), n_implausible_fixes=d.get("n_implausible_fixes"),
            implied_speed_max=d.get("implied_speed_max"), inland_km_max=d.get("inland_km_max"),
            affected_lat=d.get("affected_lat"), affected_lon=d.get("affected_lon"),
            method=r["method"],
        ))
    return GnssAnomaliesResponse(
        available=bool(anomalies), count=len(anomalies),
        method=GNSS_METHOD, caveat=GNSS_CAVEAT, anomalies=anomalies,
    )


@router.get("/maritime/jamming-zones", response_model=JammingZonesResponse)
async def maritime_jamming_zones(
    request: Request,
    variant: str = Query("broadcast", pattern="^(broadcast|affected)$"),
    min_vessels: int = Query(10, ge=1, le=1000),
    limit: int = Query(2000, ge=1, le=10000),
) -> Any:
    """The jamming-zone choropleth — 'where GPS was being denied' — as 0.1° cell x
    hourly-window distinct-vessel counts, served from persisted data (no detector
    re-run). Two variants (G4a / G5-pre):

      * broadcast — WHERE spoofed positions LANDED (implausible broadcasts; the decoy,
        e.g. the Amman cell). G4a confirmed implausible broadcasts are naturally
        disjoint from feed silence (2/3,988 in any outage window), so NO outage
        subtraction is applied.
      * affected — WHERE vessels ACTUALLY were when jamming began (the last-good
        position before each episode). It reasons from the onset of implausibility, so
        it MUST subtract feed-outage windows — reusing reconstruct_outage_windows (the
        G3-addendum machinery, imported not reimplemented) — or a feed-outage silence
        would render as a jam. The 33 h+ open window contributes zero affected cells.

    Both bucket via the SAME tested pure aggregator (affected_zone_cells); broadcast
    passes an empty outage-window list. `caveat` = 'lead, not proof'."""
    db = _db(request)
    with db_503():
        pool = await db.pool()
        if variant == "broadcast":
            rows = await pool.fetch(
                "SELECT mmsi, ts, latitude, longitude FROM vessel_states "
                "WHERE position_quality = 'implausible' "
                "AND latitude IS NOT NULL AND longitude IS NOT NULL")
            counts = affected_zone_cells(
                [(r["mmsi"], r["ts"], r["latitude"], r["longitude"]) for r in rows], [])
        else:
            ev = await pool.fetch(
                "SELECT ts, kind FROM signal_events WHERE source = 'aisstream' "
                "AND kind IN ('feed_disconnect','feed_connect') ORDER BY ts")
            cts = [r["ts"] for r in ev if r["kind"] == "feed_connect"]
            lp = await pool.fetch(
                "SELECT c AS ct, (SELECT max(v.ts) FROM vessel_states v "
                "WHERE v.source = 'aisstream' AND v.ts < c) AS last_pos "
                "FROM unnest($1::timestamptz[]) AS c", cts)
            windows = reconstruct_outage_windows(
                [(r["ts"], r["kind"]) for r in ev], datetime.now(timezone.utc),
                {r["ct"]: r["last_pos"] for r in lp})
            arows = await pool.fetch(
                "SELECT mmsi, start_ts, (detail->>'affected_lat')::float AS alat, "
                "(detail->>'affected_lon')::float AS alon FROM maritime_events "
                "WHERE source = 'aisstream' "
                "  AND event_type = 'gnss_anomaly' AND detail->>'affected_lat' IS NOT NULL")
            counts = affected_zone_cells(
                [(r["mmsi"], r["start_ts"], r["alat"], r["alon"]) for r in arows],
                [(w.start, w.end) for w in windows])

    cells = [
        ZoneCell(
            clat=clat, clon=clon,
            lat_min=clat * ZONE_CELL_DEG, lat_max=(clat + 1) * ZONE_CELL_DEG,
            lon_min=clon * ZONE_CELL_DEG, lon_max=(clon + 1) * ZONE_CELL_DEG,
            window_start=win.isoformat(), distinct_vessels=n, variant=variant,
        )
        for (clat, clon, win), n in counts.items() if n >= min_vessels
    ]
    cells.sort(key=lambda c: c.distinct_vessels, reverse=True)
    return JammingZonesResponse(
        variant=variant, count=len(cells[:limit]), min_vessels=min_vessels,
        caveat=GNSS_CAVEAT, cells=cells[:limit],
    )


@router.get("/maritime/rendezvous", response_model=RendezvousResponse)
async def maritime_rendezvous(
    request: Request,
    rank: str = Query("all", pattern="^(all|high|medium|low)$"),
    min_minutes: float = Query(60.0, ge=0.0, le=100000.0),
    limit: int = Query(200, ge=1, le=1000),
) -> Any:
    """Behavioural analysis A2 — rendezvous candidates (migration 024/025): two
    vessels that loitered close together (≤500 m), slow (≤3 kn), for ≥1h in open
    sea away from ports. BOTH vessels' identity is joined from the spine (M2a
    flag + M2b name/imo). Ordered high→low `suspicion_rank` then longest first —
    the rank PRIORITISES (tanker+tanker / flags-of-convenience high; offshore /
    ferry / service low); it does NOT accuse.

    HONEST LIMITATION (always in `caveat`): a candidate is an INVESTIGATION LEAD,
    not proof of a transfer — most candidates are legitimate offshore work or
    scheduled traffic. available=false when detection has not run."""
    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            """
            SELECT e.mmsi, e.imo, e.start_ts, e.end_ts, e.duration_s,
                   e.start_lat, e.start_lon, e.detail,
                   vp.name p_name, vp.flag_country p_flag, vp.ship_type p_type,
                   vq.imo q_imo, vq.name q_name, vq.flag_country q_flag, vq.ship_type q_type
            FROM maritime_events e
            LEFT JOIN vessel_identity vp ON vp.mmsi = e.mmsi
            LEFT JOIN vessel_identity vq ON vq.mmsi = e.detail ->> 'partner_mmsi'
            WHERE e.source = 'aisstream' AND e.event_type = 'rendezvous'
              AND ($1 = 'all' OR e.detail ->> 'suspicion_rank' = $1)
              AND (e.duration_s IS NULL OR e.duration_s >= $2)
            ORDER BY CASE e.detail ->> 'suspicion_rank'
                       WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                     (e.detail ->> 'max_duration_min')::float DESC NULLS LAST
            LIMIT $3
            """,
            rank, min_minutes * 60.0, limit,
        )


    events = []
    for r in rows:
        d = r["detail"]
        d = json.loads(d) if isinstance(d, str) else (d or {})
        events.append(RendezvousEvent(
            primary=RendezvousVessel(
                mmsi=r["mmsi"], imo=r["imo"],
                name=r["p_name"] or d.get("primary_name"),
                flag_country=r["p_flag"] or d.get("primary_flag"),
                ship_type=r["p_type"] or d.get("primary_ship_type")),
            partner=RendezvousVessel(
                mmsi=d.get("partner_mmsi"), imo=r["q_imo"] or d.get("partner_imo"),
                name=r["q_name"] or d.get("partner_name"),
                flag_country=r["q_flag"] or d.get("partner_flag"),
                ship_type=r["q_type"] or d.get("partner_ship_type")),
            start_ts=_iso(r["start_ts"]), end_ts=_iso(r["end_ts"]),
            duration_min=d.get("max_duration_min"),
            min_distance_m=d.get("min_distance_m"), both_max_speed=d.get("both_max_speed"),
            offshore_km=d.get("offshore_km"),
            start_country=d.get("start_country"), start_near_country=d.get("start_near_country"),
            start_lat=r["start_lat"], start_lon=r["start_lon"],
            suspicion_rank=d.get("suspicion_rank", "medium"),
            classification="candidate", method=RDV_METHOD,
            draught=d.get("draught"),
        ))
    return RendezvousResponse(
        available=bool(events), count=len(events), rank=rank,
        method=RDV_METHOD, caveat=RDV_CAVEAT, events=events,
    )


@router.get("/maritime/identity-changes", response_model=IdentityChangesResponse)
async def maritime_identity_changes(
    request: Request,
    rank: str = Query("all", pattern="^(all|high|medium|low)$"),
    change_type: str = Query("all", pattern="^(all|reflag|rename|retype)$"),
    limit: int = Query(200, ge=1, le=1000),
) -> Any:
    """Behavioural analysis A3 — identity-change candidates (maritime_events
    event_type='identity_change'): a vessel that broadcast a different name / flag
    / MMSI over time, IMO-anchored (the M2b spine payoff). change_type is
    'reflag' (same IMO, new MMSI+flag — the shadow-fleet signature), 'rename', or
    'retype'. Ordered high→low `suspicion_rank`.

    HONEST LIMITATION (always in `caveat`): renaming / re-flagging is usually
    LEGAL — a candidate is a documented change for investigation, NOT proven
    evasion. available=false when detection has not run."""
    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            """
            SELECT mmsi, imo, start_ts, detail
            FROM maritime_events
            WHERE source = 'aisstream' AND event_type = 'identity_change'
              AND ($1 = 'all' OR detail ->> 'suspicion_rank' = $1)
              AND ($2 = 'all' OR detail ->> 'change_type' = $2)
            ORDER BY CASE detail ->> 'suspicion_rank'
                       WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                     start_ts DESC
            LIMIT $3
            """,
            rank, change_type, limit,
        )

    events = []
    for r in rows:
        d = r["detail"]
        d = json.loads(d) if isinstance(d, str) else (d or {})
        events.append(IdentityChangeEvent(
            mmsi=r["mmsi"], imo=r["imo"],
            change_type=d.get("change_type", "rename"), field=d.get("field"),
            old_value=d.get("old_value"), new_value=d.get("new_value"),
            old_mmsi=d.get("old_mmsi"), new_mmsi=d.get("new_mmsi"),
            old_flag=d.get("old_flag"), new_flag=d.get("new_flag"),
            old_name=d.get("old_name"), new_name=d.get("new_name"),
            last_old_ts=d.get("last_old_ts"),
            first_new_ts=d.get("first_new_ts") or (r["start_ts"].isoformat() if r["start_ts"] else ""),
            gap_hours=d.get("gap_hours"), sustained_new_rows=d.get("sustained_new_rows"),
            suspicion_rank=d.get("suspicion_rank", "medium"),
            correlated=bool(d.get("correlated")),
            correlated_events={k: int(v) for k, v in (d.get("correlated_events") or {}).items()},
            classification="candidate", method=IDC_METHOD,
        ))
    return IdentityChangesResponse(
        available=bool(events), count=len(events), rank=rank,
        method=IDC_METHOD, caveat=IDC_CAVEAT, events=events,
    )


def _jd(v: Any) -> dict:
    return json.loads(v) if isinstance(v, str) else (v or {})


def _gap_summary(d: dict, duration_s: float | None) -> str:
    h = (duration_s or 0) / 3600.0
    w = d.get("start_country") or d.get("start_near_country")
    return f"went dark {h:.1f}h" + (f" in {w} waters" if w else "")


def _rdv_summary(d: dict, as_partner: bool) -> str:
    other = d.get("primary_name") if as_partner else d.get("partner_name")
    dist, dur = d.get("min_distance_m"), d.get("max_duration_min")
    tail = f" ({dist:.0f} m, {dur:.0f} min)" if dist is not None and dur is not None else ""
    # DRAUGHT-1 corroborating caveat. insufficient_data (one/both sides blind) MUST read
    # "draught not evidenced" — never "no change" (we cannot assert absence of a transfer
    # about a side we did not see).
    dr = d.get("draught") or {}
    note = ""
    if dr:
        p, q = dr.get("primary") or {}, dr.get("partner") or {}
        if dr.get("transfer_shaped") and p.get("delta") is not None and q.get("delta") is not None:
            note = f" · draught {p['delta']:+.1f}/{q['delta']:+.1f} m — transfer-shaped (corroborating, not proof)"
        elif p.get("state") == "insufficient_data" or q.get("state") == "insufficient_data":
            note = " · draught not evidenced"
        else:
            note = " · draught: no transfer shape"
    return f"met {other or 'another vessel'}{tail}{note}"


def _idc_summary(d: dict) -> str:
    ct = d.get("change_type")
    if ct == "reflag":
        base = f"re-flagged {d.get('old_flag')}→{d.get('new_flag')}"
        if d.get("old_name") != d.get("new_name"):
            base += f" ({d.get('old_name')}→{d.get('new_name')})"
        return base
    label = "renamed" if ct == "rename" else "changed type"
    return f"{label} {d.get('old_value')}→{d.get('new_value')}"


def _gnss_summary(d: dict) -> str:
    # Only the 'individual' tier reaches the composite (a real vessel spoofed/jammed).
    n = d.get("n_implausible_fixes") or 0
    parts = []
    if d.get("implied_speed_max"):
        parts.append(f"{d['implied_speed_max']:.0f} kn")
    if d.get("inland_km_max"):
        parts.append(f"{d['inland_km_max']:.0f} km inland")
    tail = f" ({', '.join(parts)})" if parts else ""
    return f"GNSS-anomalous positions: {n} implausible fixes{tail}"


def _zone_dwell_summary(d: dict, duration_s: float | None) -> str:
    # ZONES-3: only the STRONG subset reaches the composite (safety_zone_intrusion,
    # and baseline-unfamiliar standoff). Render its own summary — must NOT fall through
    # to _idc_summary (the G4b lesson).
    sig = (d.get("signal_class") or "zone_dwell").replace("_", " ")
    zone = d.get("zone_name") or d.get("zone_id") or "infrastructure"
    zt = f" ({d['zone_type']})" if d.get("zone_type") else ""
    hrs = f"{duration_s / 3600:.1f} h" if duration_s else "?"
    lb = " lower-bound" if d.get("duration_is_lower_bound") else ""
    dist = d.get("min_distance_m")
    dtail = f", min {dist:.0f} m" if isinstance(dist, (int, float)) else ""
    return f"Zone dwell — {sig} at {zone}{zt}: {hrs}{lb}{dtail}"


async def _composite_hulls(
    db: Database, min_pattern_types: int, waters: str | None, limit: int,
) -> list[CompositeHull]:
    """The shared hull-composite computation — used by BOTH /maritime/composite and
    the D5 alert feed (so the ranking is single-sourced, never forked). Fetches the
    composite view + its events + identities, ranks each hull (composite.py: pattern
    count + grey flag + strongest underlying event), filters by waters, sorts
    high→low, caps at `limit`. Raises HTTPException(503) on a DB error; returns []
    when no hull meets the threshold."""
    with db_503():
        pool = await db.pool()
        comp = await pool.fetch(
            "SELECT hull, n_gaps, n_rendezvous, n_identity_changes, n_pattern_types, "
            # DASH-3: first_seen_at + its coverage count (migration 049/050). Read together —
            # see CompositeHull.n_events_first_seen for why a bare date would mislead.
            "first_event_ts, last_event_ts, first_seen_at, n_events_first_seen, "
            "mmsis FROM maritime_hull_composite "
            "WHERE n_pattern_types >= $1", min_pattern_types)
        if not comp:
            return []
        hull_keys = [r["hull"] for r in comp]
        evrows = await pool.fetch(
            # DASH-4b-2: `end_ts` joins the select so each pattern keeps its OWN window.
            # ⚠ It is a column on the view already (migration 049) — no new query, no new join.
            "SELECT hull, event_type, via_mmsi, start_ts, end_ts, duration_s, detail, as_partner "
            "FROM maritime_hull_events WHERE hull = ANY($1::text[]) ORDER BY start_ts",
            hull_keys)
        mmsis = sorted({r["via_mmsi"] for r in evrows if r["via_mmsi"]}
                       | {m for r in comp for m in (r["mmsis"] or [])})
        ident = {r["mmsi"]: r for r in await pool.fetch(
            "SELECT mmsi, name, flag_country, ship_type, imo_quality, imo_flags "
            "FROM vessel_identity WHERE mmsi = ANY($1::text[])", mmsis)}

    ev_by_hull: dict[str, list] = {}
    for r in evrows:
        ev_by_hull.setdefault(r["hull"], []).append(r)


    hulls: list[CompositeHull] = []
    for r in comp:
        hull = r["hull"]
        evs = ev_by_hull.get(hull, [])
        timeline, flags, waters_seen, partners = [], [], [], []
        counterparts: dict[str, AlertEntityRef] = {}   # keyed to dedupe repeat meetings
        spans: list[PatternSpan] = []                 # DASH-4b-2 — one window per pattern
        max_ord = 0
        for e in evs:
            d = _jd(e["detail"])
            et = e["event_type"]
            max_ord = max(max_ord, underlying_rank_ord(et, d))
            # ⛔ EVERY pattern keeps its OWN window. The hull's envelope (first_event_ts →
            # last_event_ts) cannot stand in: a gap on Monday and a rendezvous on Friday share
            # one five-day block, which read as a lane says both ran all week. Co-occurrence is
            # the composite's whole claim, and it can only be READ if the bars are separate.
            # ⚠ `end_ts` may be NULL — an open gap. It is passed through as None, never
            # substituted with start_ts: a renderer must be able to tell "still open" from
            # "instantaneous".
            if e["start_ts"] is not None:
                spans.append(PatternSpan(type=et, start=_iso(e["start_ts"]),
                                         end=_iso(e["end_ts"])))
            flags += [d.get("old_flag"), d.get("new_flag"), d.get("partner_flag"), d.get("primary_flag")]
            for wkey in ("start_country", "start_near_country"):
                if d.get(wkey):
                    waters_seen.append(d[wkey])
            if et == "ais_gap":
                summary = _gap_summary(d, e["duration_s"])
            elif et == "rendezvous":
                summary = _rdv_summary(d, e["as_partner"])
                other = d.get("primary_name") if e["as_partner"] else d.get("partner_name")
                if other:
                    partners.append(other)
                # ⛔ DASH-3 — THE COUNTERPART AS AN IDENTITY, not just a display name. The
                # pairing has always been in `detail`; nothing above could open the other
                # vessel because only its name travelled.
                # ⚠ THE SIDES SWAP. On an `as_partner` row THIS hull is the partner, so the
                # counterpart is the PRIMARY — reading `partner_*` on both branches would make
                # every mirrored rendezvous point the vessel at itself.
                ok, on = ("primary_mmsi", "primary_name") if e["as_partner"] else ("partner_mmsi", "partner_name")
                okey = d.get(ok) or (e["via_mmsi"] if e["as_partner"] else None)
                if okey:
                    counterparts[str(okey)] = AlertEntityRef(
                        kind="vessel", id=str(okey), label=d.get(on) or f"#{okey}",
                        live_key=str(okey))
            elif et == "gnss_anomaly":
                summary = _gnss_summary(d)          # must NOT fall through to _idc_summary
            elif et == "zone_dwell":
                summary = _zone_dwell_summary(d, e["duration_s"])   # ZONES-3 (G4b): same
            else:
                summary = _idc_summary(d)
            timeline.append(CompositeTimelineEntry(
                event_type=et, ts=_iso(e["start_ts"]), summary=summary,
                rank=d.get("suspicion_rank")))
        hull_mmsis = list(dict.fromkeys(m for m in (r["mmsis"] or []) if m))
        identities = [CompositeIdentity(
            mmsi=m, name=(ident.get(m) or {}).get("name"),
            flag_country=(ident.get(m) or {}).get("flag_country"),
            ship_type=(ident.get(m) or {}).get("ship_type")) for m in hull_mmsis]
        flags += [i.flag_country for i in identities]
        grey = involves_grey_flag(flags)
        # RANK-S2: the reason travels WITH the rank, from the one function that owns the
        # rule — so the Arabic `why` names the condition that actually fired instead of
        # re-testing it (and drifting) at the prose layer.
        rank, rank_reason = _rank_with_reason(r["n_pattern_types"], grey, max_ord)
        aoi = sorted(set(waters_seen))
        if waters and waters not in aoi:
            continue                       # filter: hulls touching these waters
        is_imo = len(hull) == 7 and hull.isdigit()
        # IMO-2 spine annotation: the worst imo_quality across the hull's identities
        # + the union of their flags (a caveat on the lead — never hides the hull).
        spine_q = _worst_imo_quality((ident.get(m) or {}).get("imo_quality") for m in hull_mmsis)
        spine_flags = sorted({f for m in hull_mmsis
                              for f in ((ident.get(m) or {}).get("imo_flags") or [])})
        hulls.append(CompositeHull(
            hull=hull, is_imo=is_imo, imo=hull if is_imo else None,
            identities=identities,
            n_gaps=r["n_gaps"], n_rendezvous=r["n_rendezvous"],
            n_identity_changes=r["n_identity_changes"],
            n_pattern_types=r["n_pattern_types"], composite_rank=rank,
            rank_reason=rank_reason,
            involves_grey=grey, aoi_waters=aoi,
            rendezvous_partners=sorted(set(partners)),
            # ⚠ Sorted by label for a stable payload; the dict key already deduped a hull that
            # met the same vessel more than once (a repeat meeting is one counterpart).
            counterparts=sorted(counterparts.values(), key=lambda x: x.label),
            # ⚠ CAPPED AT 60, AND THE TRUE COUNT TRAVELS BESIDE IT. A lane strip is a SHAPE,
            # and a hull with 400 gaps renders the same shape at 60 — but a consumer that saw
            # only the cap would say "60 events" where the truth is 400. Sorted by start so the
            # cap keeps the OLDEST, i.e. the window's real left edge, rather than an arbitrary
            # 60 that would make the strip start wherever the database happened to return.
            pattern_spans=sorted(spans, key=lambda x: x.start)[:60],
            n_pattern_spans=len(spans),
            first_seen_at=_iso(r["first_seen_at"]),
            n_events_first_seen=int(r["n_events_first_seen"] or 0),
            first_event_ts=_iso(r["first_event_ts"]), last_event_ts=_iso(r["last_event_ts"]),
            timeline=timeline,
            imo_quality=spine_q, imo_flags=spine_flags))

    order = {"high": 0, "medium": 1, "low": 2}
    hulls.sort(key=lambda h: (order.get(h.composite_rank, 3), -h.n_pattern_types,
                              h.last_event_ts or ""), reverse=False)
    return hulls[:limit]


@router.get("/maritime/composite", response_model=CompositeResponse)
async def maritime_composite(
    request: Request,
    min_pattern_types: int = Query(2, ge=1, le=3),
    waters: str | None = Query(None, max_length=32),
    limit: int = Query(100, ge=1, le=500),
) -> Any:
    """Cross-pattern intelligence — the IMO-centric COMPOSITE (capstone). Aggregates
    the existing behavioural events (A1 gap, A2 rendezvous, A3 identity_change,
    candidate tier) per hull, STITCHED BY IMO across the vessel's broadcast
    identities (a re-flagged hull's events under both MMSIs unify). Ranked by
    multi-pattern signal + grey/sanctions weighting; each hull carries its
    timeline, all identities, and AOI waters touched. Default min_pattern_types=2
    (the multi-pattern hulls — the point of the composite).

    HONEST LIMITATION (always in `caveat`, and it COMPOUNDS): a composite of
    investigation leads is a STRONGER lead — still NOT proof; legitimate
    explanations exist. available=false when no events / detectors have not run."""
    hulls = await _composite_hulls(_db(request), min_pattern_types, waters, limit)
    return CompositeResponse(
        available=bool(hulls), count=len(hulls), min_pattern_types=min_pattern_types,
        method=CMP_METHOD, caveat=CMP_CAVEAT, hulls=hulls,
    )
