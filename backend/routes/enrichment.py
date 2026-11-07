"""Third-party enrichment — /vessel/{mmsi} /vessel/{mmsi}/track /aircraft/{icao24}."""

from __future__ import annotations

import re
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from ..enrich import Enricher
from ..models import (
    AircraftEnrichment, AirportInfo, PhotoInfo, RouteInfo, VesselEnrichment,
    VesselTrackPoint, VesselTrackResponse,
)

from .shared import _db, db_503, parse_time

router = APIRouter()


# E1 entity search — vessel. The track is PK-bounded (mmsi prefix of the PK), so
# the query is always cheap; the row cap just bounds a single response. A busy
# vessel over the full ~9-day frozen span is < 3000 fixes, so 5000 default / 20000
# max comfortably covers any single vessel's whole track without pagination.
VESSEL_TRACK_DEFAULT_LIMIT = 5000


VESSEL_TRACK_MAX_LIMIT = 20000


@router.get("/vessel/{mmsi}", response_model=VesselEnrichment)
async def vessel(
    request: Request,
    mmsi: str,
    imo: str | None = Query(None, max_length=15),
    name: str | None = Query(None, max_length=64),
) -> Any:
    """Best-effort vessel photo (Wikimedia Commons, by IMO then name),
    cached server-side. Coverage is sparse — photo is null for most vessels;
    never an error. Attribution (photographer + Commons page + license) is
    included when a photo is found."""
    mmsi = mmsi.strip()
    if not (3 <= len(mmsi) <= 15 and mmsi.isdigit()):
        raise HTTPException(422, "mmsi must be a 3-15 digit identifier")
    db = _db(request)
    enricher: Enricher = request.app.state.enricher
    with db_503():
        pool = await db.pool()

    info = await enricher.get_vessel_photo(pool, mmsi, imo, name)
    photo = None
    if info.get("photo_thumb"):
        photo = PhotoInfo(
            thumb=info["photo_thumb"],
            page=info.get("photo_page"),
            photographer=info.get("photographer"),
        )
    return VesselEnrichment(mmsi=mmsi, photo=photo, license=info.get("license"))


@router.get("/vessel/{mmsi}/track", response_model=VesselTrackResponse,
            response_model_by_alias=True)
async def vessel_track(
    request: Request,
    mmsi: str,
    since: str | None = Query(None, description="window start — epoch seconds or ISO-8601"),
    until: str | None = Query(None, description="window end — epoch seconds or ISO-8601"),
    limit: int = Query(VESSEL_TRACK_DEFAULT_LIMIT, ge=1, le=VESSEL_TRACK_MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> Any:
    """E1 — one vessel's ordered fix sequence (the maritime analog of /history),
    PK-backed over vessel_states (mmsi, ts, source). Default window = the full frozen
    span (omit since/until). Each fix carries its quality columns (position_quality,
    anomaly_flags) so a spoofed/jammed fix is VISIBLE in the track, never silently
    dropped (the G3 principle — surface quality, don't hide it). Capped at `limit` rows
    (default 5000, max 20000); has_more=true → narrow the window. Positions only —
    identity/photo live on /vessel/{mmsi}."""
    mmsi = mmsi.strip()
    if not (3 <= len(mmsi) <= 15 and mmsi.isdigit()):
        raise HTTPException(422, "mmsi must be a 3-15 digit identifier")
    since_t = parse_time(since, "since")
    until_t = parse_time(until, "until")
    if since_t is not None and until_t is not None and since_t >= until_t:
        raise HTTPException(422, "'since' must be earlier than 'until'")

    conds = ["mmsi = $1"]
    params: list[Any] = [mmsi]
    if since_t is not None:
        params.append(since_t)
        conds.append(f"ts >= to_timestamp(${len(params)}::bigint)")
    if until_t is not None:
        params.append(until_t)
        conds.append(f"ts < to_timestamp(${len(params)}::bigint)")
    limit_i, offset_i = len(params) + 1, len(params) + 2

    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            "SELECT ts, latitude, longitude, speed, course, heading, nav_status, "
            "position_quality, anomaly_flags, source, data_origin "
            "FROM vessel_states WHERE " + " AND ".join(conds) +
            f" ORDER BY ts ASC LIMIT ${limit_i} OFFSET ${offset_i}",
            *params, limit + 1, offset,
        )

    has_more = len(rows) > limit
    rows = rows[:limit]
    points = [
        VesselTrackPoint(
            ts=r["ts"].isoformat(),
            lat=r["latitude"], lon=r["longitude"],
            speed=r["speed"], course=r["course"], heading=r["heading"],
            nav_status=r["nav_status"],
            position_quality=r["position_quality"],
            anomaly_flags=list(r["anomaly_flags"]) if r["anomaly_flags"] is not None else None,
            source=r["source"], data_origin=r["data_origin"],
        )
        for r in rows
    ]
    return {
        "mmsi": mmsi,
        "from": since_t, "to": until_t,
        "count": len(points), "limit": limit, "offset": offset,
        "has_more": has_more, "points": points,
    }


@router.get("/aircraft/{icao24}", response_model=AircraftEnrichment)
async def aircraft(
    request: Request,
    icao24: str,
    callsign: str | None = Query(None, max_length=12),
) -> Any:
    """Best-effort enrichment for one airframe (see AircraftEnrichment).
    Sources: adsbdb.com (airframe + standing route), planespotters.net
    (photo — attribution required and included). Cached server-side."""
    icao24 = icao24.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{4,10}", icao24):
        raise HTTPException(422, "icao24 must be a 4-10 char hex identifier")
    db = _db(request)
    enricher: Enricher = request.app.state.enricher
    with db_503():
        pool = await db.pool()

    info = await enricher.get_aircraft(pool, icao24)

    photo = None
    if info.get("photo_thumb"):
        photo = PhotoInfo(
            thumb=info["photo_thumb"],
            page=info.get("photo_page"),
            photographer=info.get("photographer"),
        )

    route = None
    cs = (callsign or "").strip().upper()
    if cs and not re.fullmatch(r"[A-Z0-9-]{1,12}", cs):
        raise HTTPException(422, "callsign must be 1-12 chars of A-Z, 0-9, '-'")
    if cs:
        r = await enricher.get_route(pool, cs)
        if r.get("found"):
            route = RouteInfo(
                callsign=cs,
                airline=r.get("airline_name"),
                origin=AirportInfo(
                    icao=r.get("origin_icao"), iata=r.get("origin_iata"),
                    name=r.get("origin_name"), municipality=r.get("origin_municipality"),
                    country=r.get("origin_country"),
                    lat=r.get("origin_lat"), lon=r.get("origin_lon"),
                ),
                destination=AirportInfo(
                    icao=r.get("dest_icao"), iata=r.get("dest_iata"),
                    name=r.get("dest_name"), municipality=r.get("dest_municipality"),
                    country=r.get("dest_country"),
                    lat=r.get("dest_lat"), lon=r.get("dest_lon"),
                ),
            )

    return AircraftEnrichment(
        icao24=icao24,
        registration=info.get("registration"),
        aircraft_type=info.get("aircraft_type"),
        operator=info.get("operator"),
        operator_country=info.get("operator_country"),
        photo=photo,
        route=route,
    )
