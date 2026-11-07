"""Unified search — /search and the five typed searches (vessel/aircraft/satellite/airport/area)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from .. import airports as airport_gaz
from .. import area as area_search
from ..live import build_live_payload
from ..models import (
    AircraftSearchResponse, AircraftSummary,
    AirportSummary, AirportSearchResponse, LandingCandidate,
    AreaEntry, AreaSearchResponse,
    SatelliteSearchResponse, SatelliteSummary,
    SearchResponse, VesselSearchResponse, VesselSummary,
)

from .shared import _db, _iso, db_503, parse_time
from .space import OBJECT_TYPE_LABELS

router = APIRouter()


VESSEL_SEARCH_MAX_MATCHES = 50         # name substrings can be broad — bound the match set


VESSEL_SEARCH_CAVEAT = (
    "Event counts tally INVESTIGATION LEADS (A1 gaps / A2 rendezvous / A3 "
    "identity changes / GNSS anomalies), never proof: terrestrial AIS is spoofable "
    "and coverage is imperfect. Data is frozen — each vessel's last-fix timestamp "
    "is its data vintage."
)


# E2 entity search — aircraft. icao24 is PK-direct and registration is a cheap
# scan of the 22k-row aircraft_info, but callsign is UNINDEXED on the 33.6M-row
# state_vectors hypertable — a naive callsign scan is ~90 s. A callsign is a live
# FLIGHT identifier (one airframe flies many over time), so callsign resolves over
# a RECENT window bounded by the ts index (~80 ms), NOT across all history and
# NOT via a new multi-GB index. See the E2 report / Step 0.
AIRCRAFT_SEARCH_MAX_MATCHES = 50


AIRCRAFT_CALLSIGN_WINDOW_S = 3600      # callsign resolves to aircraft seen in the last hour


AIRCRAFT_SEARCH_CAVEAT = (
    "Presence summary — the air domain has NO behavioural-event layer (unlike the "
    "maritime domain): identity + last-seen + AOI status, and the track is the "
    "expansion. Callsign resolves to aircraft seen in the last hour (a callsign is a "
    "FLIGHT identifier, not the airframe — one icao24 flies many); registration and "
    "route are standing data, not the filed flight plan. Live data updates continuously."
)


# E3 entity search — satellite. No stored fixes: position is SGP4-computed from the
# TLE, and the "track" is a derived ground-track (propagated client-side, matching
# the existing satellites tab). The summary composes layers whose vintages DIFFER
# and are kept distinct (identity SATCAT ~2026 / orbit derived-SGP4 live / strategic
# UCS FROZEN 2023 / passes forward ~24 h prediction).
SATELLITE_SEARCH_MAX_MATCHES = 50


SATELLITE_SEARCH_CAVEAT = (
    "Layers have DIFFERENT vintages, kept distinct: identity (SATCAT) and orbit "
    "(derived-SGP4) are current; strategic data (users/purpose/operator) is UCS and "
    "FROZEN at its 2023 snapshot. AOI passes are a forward ~24 h PREDICTION, not "
    "history. Position/ground-track are SGP4-computed from the TLE — accuracy decays "
    "from the TLE epoch (display, not precise conjunction). The CelesTrak catalog is "
    "public (no covert or actively-manoeuvring objects)."
)


@router.get("/search", response_model=SearchResponse)
async def search(
    request: Request,
    q: str | None = Query(None, max_length=20),
    min_alt: float | None = Query(None, description="baro_altitude >= (meters)"),
    max_alt: float | None = Query(None, description="baro_altitude <= (meters)"),
    on_ground: bool | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> Any:
    payload = await build_live_payload(_db(request))

    def keep(s: dict[str, Any]) -> bool:
        if q:
            needle = q.strip().lower()
            if needle not in s["icao24"].lower() and needle not in (s.get("callsign") or "").lower():
                return False
        alt = s.get("baro_altitude")
        if min_alt is not None and (alt is None or alt < min_alt):
            return False
        if max_alt is not None and (alt is None or alt > max_alt):
            return False
        if on_ground is not None and bool(s.get("on_ground")) != on_ground:
            return False
        return True

    matches = [s for s in payload["states"] if keep(s)][:limit]
    return {
        "snapshot_time": payload["snapshot_time"],
        "source": payload["source"],
        "count": len(matches),
        "states": matches,
    }


def _like_escape(q: str) -> str:
    """Escape ILIKE metacharacters so a name query is a literal substring."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/search/vessel", response_model=VesselSearchResponse)
async def search_vessel(
    request: Request,
    q: str = Query(min_length=1, max_length=64, description="MMSI (exact), IMO (exact), or name (substring)"),
) -> Any:
    """E1 — DB-backed vessel lookup (NOT the snapshot-only /search, which is untouched).
    Resolves q against the identity spine (vessel_identity) by MMSI (exact), IMO (exact),
    or name (case-insensitive substring), plus a bare tracked MMSI present in
    vessel_states. Returns a SUMMARY per match: identity + last known fix + behavioural
    event counts (A1 gap candidate / A2 rendezvous incl. partner / A3 identity_change /
    G4a GNSS individual), stitched by MMSI ∪ IMO so a re-flagged hull's events unify. The
    track is the expansion (/vessel/{mmsi}/track). `caveat` MUST be surfaced — the counts
    tally investigation leads, not proof."""
    qs = q.strip()
    if not qs:
        return VesselSearchResponse(query=q, count=0, caveat=VESSEL_SEARCH_CAVEAT)
    like = f"%{_like_escape(qs)}%"
    db = _db(request)
    with db_503():
        pool = await db.pool()
        id_rows = await pool.fetch(
            "SELECT mmsi, imo, name, flag_country, ship_type, ship_type_code, "
            "callsign, length_m, beam_m, station_type, imo_quality, imo_flags "
            "FROM vessel_identity "
            "WHERE mmsi = $1 OR imo = $1 OR name ILIKE $2 "
            "ORDER BY (mmsi = $1) DESC, (imo = $1) DESC, name ASC NULLS LAST "
            "LIMIT $3",
            qs, like, VESSEL_SEARCH_MAX_MATCHES,
        )
        # bare tracked MMSI with no identity row still resolves (search by MMSI)
        ident = {r["mmsi"]: r for r in id_rows}
        if qs.isdigit() and qs not in ident:
            has_fixes = await pool.fetchval(
                "SELECT 1 FROM vessel_states WHERE mmsi = $1 LIMIT 1", qs)
            if has_fixes:
                ident[qs] = None  # synthetic identity-less match

        mmsis = list(ident.keys())
        if not mmsis:
            return VesselSearchResponse(query=q, count=0, caveat=VESSEL_SEARCH_CAVEAT)
        imos = [r["imo"] for r in id_rows if r["imo"]]

        # last fix per matched mmsi (PK-backed: mmsi prefix + ts DESC)
        last_rows = await pool.fetch(
            "SELECT DISTINCT ON (mmsi) mmsi, ts, latitude, longitude, speed, "
            "position_quality, anomaly_flags FROM vessel_states "
            "WHERE mmsi = ANY($1::text[]) ORDER BY mmsi, ts DESC",
            mmsis,
        )
        last = {r["mmsi"]: r for r in last_rows}
        nfix = {r["mmsi"]: r["n"] for r in await pool.fetch(
            "SELECT mmsi, count(*) AS n FROM vessel_states "
            "WHERE mmsi = ANY($1::text[]) GROUP BY mmsi", mmsis)}
        # every event touching any matched hull (as subject via mmsi/imo, OR as a
        # rendezvous PARTNER) — attributed per hull in Python (imo-stitched)
        evs = await pool.fetch(
            "SELECT event_type, classification, mmsi, imo, "
            "detail ->> 'partner_mmsi' AS partner_mmsi, detail ->> 'partner_imo' AS partner_imo "
            "FROM maritime_events WHERE source = 'aisstream' AND (mmsi = ANY($1::text[]) OR imo = ANY($2::text[])) "
            "OR detail ->> 'partner_mmsi' = ANY($1::text[]) OR detail ->> 'partner_imo' = ANY($2::text[])",
            mmsis, imos,
        )

    def counts_for(m: str, i: str | None) -> tuple[int, int, int, int]:
        """(n_gaps, n_rendezvous, n_identity_changes, n_gnss) for one hull, reusing
        the per-type semantics: gaps/gnss counted at the candidate/individual tier;
        rendezvous counts this hull whether it was the primary OR the partner; all
        stitched by MMSI ∪ IMO."""
        def subject(e: Any) -> bool:
            return e["mmsi"] == m or (i is not None and e["imo"] == i)

        def rdv_involved(e: Any) -> bool:
            return subject(e) or e["partner_mmsi"] == m or (i is not None and e["partner_imo"] == i)

        n_gaps = sum(1 for e in evs if e["event_type"] == "ais_gap"
                     and e["classification"] == "candidate" and subject(e))
        n_rdv = sum(1 for e in evs if e["event_type"] == "rendezvous" and rdv_involved(e))
        n_idc = sum(1 for e in evs if e["event_type"] == "identity_change" and subject(e))
        n_gnss = sum(1 for e in evs if e["event_type"] == "gnss_anomaly"
                     and e["classification"] == "candidate" and subject(e))
        return n_gaps, n_rdv, n_idc, n_gnss

    summaries: list[VesselSummary] = []
    for m in mmsis:
        row = ident[m]
        imo = row["imo"] if row is not None else None
        lf = last.get(m)
        n_gaps, n_rdv, n_idc, n_gnss = counts_for(m, imo)
        n_pattern_types = sum(1 for n in (n_gaps, n_rdv, n_idc, n_gnss) if n > 0)
        summaries.append(VesselSummary(
            mmsi=m,
            imo=imo,
            name=row["name"] if row is not None else None,
            flag_country=row["flag_country"] if row is not None else None,
            ship_type=row["ship_type"] if row is not None else None,
            ship_type_code=row["ship_type_code"] if row is not None else None,
            callsign=row["callsign"] if row is not None else None,
            length_m=row["length_m"] if row is not None else None,
            beam_m=row["beam_m"] if row is not None else None,
            station_type=row["station_type"] if row is not None else None,
            last_ts=lf["ts"].isoformat() if lf and lf["ts"] else None,
            last_lat=lf["latitude"] if lf else None,
            last_lon=lf["longitude"] if lf else None,
            last_speed=lf["speed"] if lf else None,
            last_position_quality=lf["position_quality"] if lf else None,
            last_anomaly_flags=(list(lf["anomaly_flags"]) if lf and lf["anomaly_flags"] is not None else None),
            imo_quality=row["imo_quality"] if row is not None else None,
            imo_flags=(list(row["imo_flags"]) if row is not None and row["imo_flags"] is not None else None),
            n_fixes=nfix.get(m, 0),
            n_gaps=n_gaps, n_rendezvous=n_rdv, n_identity_changes=n_idc, n_gnss=n_gnss,
            n_pattern_types=n_pattern_types,
        ))

    # exact MMSI/IMO first (id_rows already ordered so), then most-signal, then name
    summaries.sort(key=lambda s: (
        0 if (s.mmsi == qs or s.imo == qs) else 1,
        -s.n_pattern_types,
        (s.name or f"#{s.mmsi}").lower(),
    ))
    return VesselSearchResponse(
        query=q, count=len(summaries), caveat=VESSEL_SEARCH_CAVEAT, matches=summaries,
    )


def _is_hex_icao(q: str) -> bool:
    """True when q could be a full/partial icao24 (1-6 hex chars)."""
    return 1 <= len(q) <= 6 and all(ch in "0123456789abcdef" for ch in q.lower())


@router.get("/search/aircraft", response_model=AircraftSearchResponse)
async def search_aircraft(
    request: Request,
    q: str = Query(min_length=1, max_length=32, description="icao24 (exact), registration or callsign (substring)"),
) -> Any:
    """E2 — DB-backed aircraft lookup (NOT the snapshot-only /search). Resolves q by
    icao24 (exact, PK), registration (substring, aircraft_info — a cheap 22k-row scan),
    and callsign (substring, over the last hour via the ts index — a callsign is a live
    FLIGHT identifier, never scanned across all 33.6M history rows). Returns a PRESENCE
    summary per TRACKED match (air has no behavioural-event layer): identity + last-seen
    fix (position/altitude/on_ground + AOI classification) + first_seen. The track is the
    expansion (/history?icao24=). `caveat` MUST be surfaced."""
    qs = q.strip()
    if not qs:
        return AircraftSearchResponse(query=q, caveat=AIRCRAFT_SEARCH_CAVEAT)
    like = f"%{_like_escape(qs)}%"
    db = _db(request)
    with db_503():
        pool = await db.pool()
        cand: set[str] = set()
        if _is_hex_icao(qs):
            cand.add(qs.lower())                         # icao24 exact (confirmed tracked below)
        # registration substring — a cheap scan of the identity table
        for r in await pool.fetch(
            "SELECT icao24 FROM aircraft_info WHERE registration ILIKE $1 LIMIT $2",
            like, AIRCRAFT_SEARCH_MAX_MATCHES,
        ):
            cand.add(r["icao24"])
        # callsign substring — RECENT window only (ts-indexed), never full history
        for r in await pool.fetch(
            "SELECT DISTINCT icao24 FROM state_vectors "
            "WHERE ts >= now() - make_interval(secs => $1) AND callsign ILIKE $2 "
            "LIMIT $3",
            AIRCRAFT_CALLSIGN_WINDOW_S, like, AIRCRAFT_SEARCH_MAX_MATCHES,
        ):
            cand.add(r["icao24"])
        if not cand:
            return AircraftSearchResponse(query=q, caveat=AIRCRAFT_SEARCH_CAVEAT)
        cand_list = list(cand)[:AIRCRAFT_SEARCH_MAX_MATCHES]

        # last fix per candidate (PK backward scan LIMIT 1, ~0.3 ms each). The
        # CROSS JOIN LATERAL drops any candidate with no fix — this is a TRACKING
        # search, so an identity-only (never-tracked) match is intentionally omitted.
        last_rows = await pool.fetch(
            "SELECT c.icao24, lf.ts, lf.latitude, lf.longitude, lf.baro_altitude, lf.geo_altitude, "
            "lf.on_ground, lf.velocity, lf.true_track, lf.callsign, lf.country, lf.near_country, "
            "lf.position_quality, lf.distance_km, lf.reg, lf.actype, lf.acdesc, lf.source, "
            "(SELECT min(ts) FROM state_vectors WHERE icao24 = c.icao24) AS first_seen "
            "FROM unnest($1::text[]) AS c(icao24) "
            "CROSS JOIN LATERAL ("
            "  SELECT ts, latitude, longitude, baro_altitude, geo_altitude, on_ground, velocity, "
            "         true_track, callsign, country, near_country, position_quality, distance_km, "
            "         reg, actype, acdesc, source "
            "  FROM state_vectors WHERE icao24 = c.icao24 ORDER BY ts DESC LIMIT 1) lf",
            cand_list,
        )
        if not last_rows:
            return AircraftSearchResponse(query=q, caveat=AIRCRAFT_SEARCH_CAVEAT)
        tracked = [r["icao24"] for r in last_rows]
        info = {r["icao24"]: r for r in await pool.fetch(
            "SELECT icao24, registration, aircraft_type, icao_type, operator, operator_country "
            "FROM aircraft_info WHERE icao24 = ANY($1::text[])", tracked)}
        callsigns = sorted({(r["callsign"] or "").strip() for r in last_rows if (r["callsign"] or "").strip()})
        routes = {}
        if callsigns:
            routes = {r["callsign"]: r for r in await pool.fetch(
                "SELECT callsign, origin_icao, origin_name, dest_icao, dest_name "
                "FROM flight_routes WHERE callsign = ANY($1::text[])", callsigns)}


    matches: list[AircraftSummary] = []
    for r in last_rows:
        ic = r["icao24"]
        ai = info.get(ic)
        cs = (r["callsign"] or "").strip() or None
        rt = routes.get(cs) if cs else None
        matches.append(AircraftSummary(
            icao24=ic,
            callsign=cs,
            registration=(ai["registration"] if ai else None) or r["reg"],
            aircraft_type=(ai["aircraft_type"] if ai else None) or r["acdesc"],
            icao_type=(ai["icao_type"] if ai else None) or r["actype"],
            operator=ai["operator"] if ai else None,
            operator_country=ai["operator_country"] if ai else None,
            origin_icao=rt["origin_icao"] if rt else None,
            origin_name=rt["origin_name"] if rt else None,
            dest_icao=rt["dest_icao"] if rt else None,
            dest_name=rt["dest_name"] if rt else None,
            last_ts=_iso(r["ts"]),
            last_lat=r["latitude"], last_lon=r["longitude"],
            baro_altitude=r["baro_altitude"], geo_altitude=r["geo_altitude"],
            on_ground=r["on_ground"], velocity=r["velocity"], true_track=r["true_track"],
            country=r["country"], near_country=r["near_country"],
            position_quality=r["position_quality"], distance_km=r["distance_km"],
            source=r["source"], first_seen=_iso(r["first_seen"]),
        ))
    # exact icao24 first, then by callsign/registration/icao24 label
    matches.sort(key=lambda m: (
        0 if m.icao24 == qs.lower() else 1,
        (m.callsign or m.registration or m.icao24).lower(),
    ))
    return AircraftSearchResponse(
        query=q, count=len(matches), caveat=AIRCRAFT_SEARCH_CAVEAT, matches=matches,
    )


@router.get("/search/satellite", response_model=SatelliteSearchResponse)
async def search_satellite(
    request: Request,
    q: str = Query(min_length=1, max_length=64, description="NORAD id (exact) or object name (substring)"),
) -> Any:
    """E3 — DB-backed satellite lookup. Resolves q by NORAD id (exact, PK everywhere)
    or object_name (substring; satellite_info/elements are small — sub-ms). Each match
    composes the satellite LAYERS with their vintages kept DISTINCT (identity SATCAT /
    orbit derived-SGP4 / strategic UCS-2023 / AOI passes forward-window). No stored
    fixes: the TLE travels with the summary so the client propagates the ground-track
    (matching the existing satellites tab). `caveat` MUST be surfaced."""
    qs = q.strip()
    if not qs:
        return SatelliteSearchResponse(query=q, caveat=SATELLITE_SEARCH_CAVEAT)
    like = f"%{_like_escape(qs)}%"
    db = _db(request)
    with db_503():
        pool = await db.pool()
        cand: set[int] = set()
        if qs.isdigit():
            cand.add(int(qs))                              # NORAD exact
        for r in await pool.fetch(
            "SELECT norad_id FROM satellite_info WHERE object_name ILIKE $1 LIMIT $2",
            like, SATELLITE_SEARCH_MAX_MATCHES,
        ):
            cand.add(r["norad_id"])
        for r in await pool.fetch(
            "SELECT DISTINCT norad_id FROM satellite_elements WHERE object_name ILIKE $1 LIMIT $2",
            like, SATELLITE_SEARCH_MAX_MATCHES,
        ):
            cand.add(r["norad_id"])
        if not cand:
            return SatelliteSearchResponse(query=q, caveat=SATELLITE_SEARCH_CAVEAT)
        norads = list(cand)[:SATELLITE_SEARCH_MAX_MATCHES]

        info = {r["norad_id"]: r for r in await pool.fetch(
            "SELECT norad_id, object_name, international_designator, object_type, "
            "owner, owner_name, launch_date, satcat_snapshot_date "
            "FROM satellite_info WHERE norad_id = ANY($1::int[])", norads)}
        orbit = {r["norad_id"]: r for r in await pool.fetch(
            "SELECT norad_id, orbit_class, apogee_km, perigee_km, inclination_deg, "
            "period_min, is_geostationary, tle_epoch "
            "FROM satellite_orbit WHERE norad_id = ANY($1::int[])", norads)}
        strat = {r["norad_id"]: r for r in await pool.fetch(
            "SELECT norad_id, users, purpose, operator_country, ucs_snapshot_date "
            "FROM satellite_strategic WHERE norad_id = ANY($1::int[])", norads)}
        elem = {r["norad_id"]: r for r in await pool.fetch(
            "SELECT DISTINCT ON (norad_id) norad_id, object_name, tle_line1, tle_line2, epoch "
            "FROM satellite_elements WHERE norad_id = ANY($1::int[]) "
            "ORDER BY norad_id, epoch DESC NULLS LAST", norads)}
        passes = {r["norad_id"]: r for r in await pool.fetch(
            "SELECT norad_id, count(*) AS n, bool_or(is_persistent) AS persistent, "
            "min(window_start) AS ws, max(window_end) AS we "
            "FROM satellite_passes WHERE norad_id = ANY($1::int[]) GROUP BY norad_id", norads)}


    matches: list[SatelliteSummary] = []
    for n in norads:
        ii, oo, ss, ee, pp = info.get(n), orbit.get(n), strat.get(n), elem.get(n), passes.get(n)
        if ii is None and ee is None and oo is None:
            continue  # a bare NORAD with no data in any layer — drop
        otype = ii["object_type"] if ii else None
        matches.append(SatelliteSummary(
            norad_id=n,
            object_name=(ii["object_name"] if ii else None) or (ee["object_name"] if ee else None),
            international_designator=ii["international_designator"] if ii else None,
            object_type=otype,
            object_type_label=OBJECT_TYPE_LABELS.get(otype) if otype else None,
            owner_name=(ii["owner_name"] if ii else None) or (ii["owner"] if ii else None),
            launch_date=_iso(ii["launch_date"]) if ii else None,
            identity_vintage=_iso(ii["satcat_snapshot_date"]) if ii else None,
            orbit_class=oo["orbit_class"] if oo else None,
            apogee_km=oo["apogee_km"] if oo else None,
            perigee_km=oo["perigee_km"] if oo else None,
            inclination_deg=oo["inclination_deg"] if oo else None,
            period_min=oo["period_min"] if oo else None,
            is_geostationary=oo["is_geostationary"] if oo else None,
            orbit_vintage=_iso(oo["tle_epoch"]) if oo else None,
            users=ss["users"] if ss else None,
            purpose=ss["purpose"] if ss else None,
            operator_country=ss["operator_country"] if ss else None,
            strategic_vintage=_iso(ss["ucs_snapshot_date"]) if ss else None,
            n_passes=pp["n"] if pp else 0,
            is_persistent=bool(pp["persistent"]) if pp else False,
            pass_window_start=_iso(pp["ws"]) if pp else None,
            pass_window_end=_iso(pp["we"]) if pp else None,
            tle1=ee["tle_line1"] if ee else None,
            tle2=ee["tle_line2"] if ee else None,
            tle_epoch=_iso(ee["epoch"]) if ee else None,
        ))
    matches.sort(key=lambda m: (
        0 if str(m.norad_id) == qs else 1,
        (m.object_name or str(m.norad_id)).lower(),
    ))
    return SatelliteSearchResponse(
        query=q, count=len(matches), caveat=SATELLITE_SEARCH_CAVEAT, matches=matches,
    )


def _airport_summary(a: dict[str, Any]) -> AirportSummary:
    return AirportSummary(
        icao=a["icao"], iata=a.get("iata"), name=a.get("name"),
        municipality=a.get("municipality"), country=a.get("country"),
        lat=a["lat"], lon=a["lon"], elevation_ft=a.get("elevation_ft"), type=a.get("type"),
    )


@router.get("/search/airport", response_model=AirportSearchResponse)
async def search_airport(
    request: Request,
    q: str = Query(min_length=1, max_length=64, description="ICAO/IATA (exact) or name/municipality (substring)"),
    icao: str | None = Query(None, max_length=8, description="resolve landings for this specific airport"),
    hours: int = Query(airport_gaz.DEFAULT_WINDOW_HOURS, ge=1, le=airport_gaz.MAX_WINDOW_HOURS),
) -> Any:
    """E4 — airport → landing CANDIDATES (the one-to-many entity). Resolves airports
    from the committed OurAirports gazetteer IN-PROCESS (no DB identity table, unlike
    the other three entities). For the selected airport (the `icao` param, else the top
    match) it DERIVES landings ON-QUERY from state_vectors over the vicinity box + an
    ELEVATION-RELATIVE altitude band (E4-pre): primary = on_ground fixes; fallback = a
    descending approach terminating near field elevation. Candidates, NOT proven
    landings; quality is coverage-bounded (sparse/empty at a low-coverage field is
    correct). `caveat` MUST be surfaced."""
    matches_raw = airport_gaz.resolve(q)
    matches = [_airport_summary(a) for a in matches_raw]
    resp = AirportSearchResponse(
        query=q, count=len(matches), caveat=airport_gaz.SEARCH_CAVEAT,
        window_hours=hours, matches=matches,
    )
    if not matches_raw:
        return resp

    # which airport's landings — the explicit icao (resolved directly from the
    # gazetteer, so it need not be within the capped q-matches), else the top match
    sel = None
    if icao:
        icu = icao.strip().upper()
        sel = (next((a for a in matches_raw if a["icao"].upper() == icu), None)
               or next((a for a in airport_gaz.resolve(icu) if a["icao"].upper() == icu), None))
    if sel is None:
        sel = matches_raw[0]

    elev_m = airport_gaz.elevation_m(sel)
    ceil = elev_m + airport_gaz.APPROACH_CEIL_M
    v = airport_gaz.VICINITY_DEG
    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            "SELECT icao24, callsign, ts, on_ground, baro_altitude, reg, source "
            "FROM state_vectors "
            "WHERE ts >= now() - make_interval(hours => $1) "
            "  AND latitude BETWEEN $2 AND $3 AND longitude BETWEEN $4 AND $5 "
            "  AND (on_ground OR baro_altitude < $6) "
            "ORDER BY icao24, ts",
            hours, sel["lat"] - v, sel["lat"] + v, sel["lon"] - v, sel["lon"] + v, ceil,
        )

    by_ac: dict[str, list] = {}                     # group by aircraft → one candidate each
    for r in rows:
        by_ac.setdefault(r["icao24"], []).append(dict(r))
    candidates: list[LandingCandidate] = []
    for fixes in by_ac.values():
        c = airport_gaz.landing_candidate(fixes, elev_m)
        if c is not None:
            candidates.append(LandingCandidate(**c))
    candidates.sort(key=lambda c: c.landing_ts, reverse=True)

    resp.airport = _airport_summary(sel)
    resp.candidates = candidates[:airport_gaz.MAX_CANDIDATES]
    return resp


# D4 area-search — "draw a box → what crossed it". Air/sea hit the native-point
# GiST (migration 031); space is an SGP4 recompute. Short default window (the GiST
# makes it cheap and extendable); per-domain cap keeps a huge box bounded.
AREA_DEFAULT_WINDOW_H = 2.0


AREA_MAX_WINDOW_H = 720.0              # 30 days — the GiST affords a long window


AREA_MAX_PER_DOMAIN = 200


AREA_CAVEAT = (
    "Entities whose track crossed the box in the window. Mixed liveness: air is "
    "live, sea is frozen (last real AIS fix — see the freshness strip). The space "
    "list is an SGP4 sampling — a brief crossing of a very small box between samples "
    "can be missed. Each row is a discovery hit; open it for the full entity detail."
)


@router.get("/search/area", response_model=AreaSearchResponse)
async def search_area(
    request: Request,
    lat1: float = Query(..., ge=-90, le=90),
    lon1: float = Query(..., ge=-180, le=180),
    lat2: float = Query(..., ge=-90, le=90),
    lon2: float = Query(..., ge=-180, le=180),
    since: str | None = Query(None, description="window start — epoch seconds or ISO-8601"),
    until: str | None = Query(None, description="window end — epoch seconds or ISO-8601"),
    domains: str = Query("air,sea,space", description="comma subset of air,sea,space"),
) -> Any:
    """D4 — multi-domain area-search. `point(longitude,latitude) <@ box(...)` hits the
    GiST (migration 031) for air + sea; space is an SGP4 box×window recompute (in a
    thread). Returns LIGHT discovery rows per domain (id + label + last-in-box
    position); the frontend opens a row via the E1–E3 search-by-id endpoints for the
    full summary + detail. Short default window (extendable). `caveat` MUST be surfaced."""
    lat_min, lat_max = sorted((lat1, lat2))
    lon_min, lon_max = sorted((lon1, lon2))
    until_t = parse_time(until, "until") or int(time.time())
    since_t = parse_time(since, "since")
    if since_t is None:
        since_t = until_t - int(AREA_DEFAULT_WINDOW_H * 3600)
    if since_t >= until_t:
        raise HTTPException(422, "'since' must be earlier than 'until'")
    since_t = max(since_t, until_t - int(AREA_MAX_WINDOW_H * 3600))   # clamp window
    window_h = (until_t - since_t) / 3600.0
    want = {d.strip() for d in domains.split(",") if d.strip()}

    db = _db(request)
    air: list[AreaEntry] = []
    sea: list[AreaEntry] = []
    space: list[AreaEntry] = []
    with db_503():
        pool = await db.pool()
        if "air" in want:
            rows = await pool.fetch(
                "SELECT DISTINCT ON (icao24) icao24, callsign, ts, latitude, longitude "
                "FROM state_vectors "
                "WHERE point(longitude, latitude) <@ box(point($1,$2), point($3,$4)) "
                "  AND ts >= to_timestamp($5::bigint) AND ts < to_timestamp($6::bigint) "
                "ORDER BY icao24, ts DESC LIMIT $7",
                lon_min, lat_min, lon_max, lat_max, since_t, until_t, AREA_MAX_PER_DOMAIN,
            )
            air = [AreaEntry(id=r["icao24"], label=(r["callsign"] or "").strip() or r["icao24"],
                             sublabel=r["icao24"], lat=r["latitude"], lon=r["longitude"],
                             ts=r["ts"].isoformat()) for r in rows]
        if "sea" in want:
            rows = await pool.fetch(
                "SELECT DISTINCT ON (mmsi) mmsi, name, ts, latitude, longitude "
                "FROM vessel_states "
                "WHERE point(longitude, latitude) <@ box(point($1,$2), point($3,$4)) "
                "  AND ts >= to_timestamp($5::bigint) AND ts < to_timestamp($6::bigint) "
                "ORDER BY mmsi, ts DESC LIMIT $7",
                lon_min, lat_min, lon_max, lat_max, since_t, until_t, AREA_MAX_PER_DOMAIN,
            )
            sea = [AreaEntry(id=r["mmsi"], label=r["name"] or f"#{r['mmsi']}",
                             sublabel=r["mmsi"], lat=r["latitude"], lon=r["longitude"],
                             ts=r["ts"].isoformat()) for r in rows]
        if "space" in want:
            tle = await pool.fetch(
                "SELECT DISTINCT ON (norad_id) norad_id, tle_line1, tle_line2 "
                "FROM satellite_elements ORDER BY norad_id, epoch DESC NULLS LAST")
            tle_rows = [(r["norad_id"], r["tle_line1"], r["tle_line2"]) for r in tle]
            start = datetime.fromtimestamp(since_t, tz=timezone.utc)
            # CPU-bound SGP4 batch → a thread, so the event loop isn't blocked
            crossings = await asyncio.to_thread(
                area_search.satellites_crossing_box,
                tle_rows, lat_min, lat_max, lon_min, lon_max, start, window_h,
            )
            crossings = crossings[:AREA_MAX_PER_DOMAIN]
            norads = [c["norad"] for c in crossings]
            names = {r["norad_id"]: r["object_name"] for r in await pool.fetch(
                "SELECT norad_id, object_name FROM satellite_info WHERE norad_id = ANY($1::int[])",
                norads)}
            space = [AreaEntry(id=str(c["norad"]), label=names.get(c["norad"]) or str(c["norad"]),
                               sublabel=str(c["norad"]), lat=c["lat"], lon=c["lon"],
                               ts=c["ts"].isoformat()) for c in crossings]

    return AreaSearchResponse(
        box=[lat_min, lon_min, lat_max, lon_max], since=since_t, until=until_t,
        window_hours=round(window_h, 2), caveat=AREA_CAVEAT, air=air, sea=sea, space=space,
    )
