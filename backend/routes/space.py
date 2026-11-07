"""Space domain — /satellite-groups /satellites /satellite/{norad_id} /reentry."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from ..db import (
    DB_ERRORS,
)
from ..models import (
    SatelliteElement, SatelliteEnrichment, SatelliteGroup, SatelliteGroupsResponse,
    SatellitesResponse, SatelliteReentry, ReentryResponse,
)

from .shared import _db, _iso, db_503

router = APIRouter()


# SATCAT OBJECT_TYPE code -> readable label (raw code is stored authoritative;
# this is presentation only, mirroring how the frontend labels raw type codes).
OBJECT_TYPE_LABELS = {"PAY": "payload", "R/B": "rocket_body", "DEB": "debris", "UNK": "unknown"}


@router.get("/satellite-groups", response_model=SatelliteGroupsResponse)
async def satellite_groups(request: Request) -> Any:
    """CelesTrak groups currently cached (for the frontend group selector)."""
    db = _db(request)
    try:
        pool = await db.pool()
        rows = await pool.fetch(
            "SELECT grp, count(*) AS n, max(fetched_at) AS fetched "
            "FROM satellite_elements GROUP BY grp ORDER BY grp"
        )
    except DB_ERRORS:
        return SatelliteGroupsResponse(groups=[])
    return SatelliteGroupsResponse(groups=[
        SatelliteGroup(group=r["grp"], count=r["n"],
                       fetched_at=r["fetched"].isoformat() if r["fetched"] else None)
        for r in rows
    ])


@router.get("/satellites", response_model=SatellitesResponse)
async def satellites(request: Request, group: str = Query("stations", max_length=40)) -> Any:
    """Orbital elements for one CelesTrak group. The client propagates
    positions from these TLEs locally (SGP4) — the backend never computes
    positions, so there is no rate limit on 'live' satellite motion."""
    group = group.strip().lower()
    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            "SELECT norad_id, object_name, tle_line1, tle_line2, epoch, fetched_at "
            "FROM satellite_elements WHERE grp = $1 ORDER BY object_name",
            group,
        )
    if not rows:
        return SatellitesResponse(available=False, group=group)
    return SatellitesResponse(
        available=True,
        group=group,
        count=len(rows),
        fetched_at=rows[0]["fetched_at"].isoformat() if rows[0]["fetched_at"] else None,
        elements=[
            SatelliteElement(
                norad_id=r["norad_id"], name=r["object_name"],
                tle1=r["tle_line1"], tle2=r["tle_line2"],
                epoch=r["epoch"].isoformat() if r["epoch"] else None,
            )
            for r in rows
        ],
    )


@router.get("/satellite/{norad_id}", response_model=SatelliteEnrichment)
async def satellite(request: Request, norad_id: int) -> Any:
    """SATCAT identity for one satellite (country/launch/object-type/RCS/ops
    status), cached offline from CelesTrak SATCAT (satellite_info, migration
    016). A pure local read — never fetches upstream. available=false when the
    norad_id has no SATCAT row yet; never an error. Orbital motion stays
    client-side (SGP4 from the TLE); this only adds IDENTITY."""
    if norad_id <= 0:
        raise HTTPException(422, "norad_id must be a positive integer")
    db = _db(request)
    with db_503():
        pool = await db.pool()
        row = await pool.fetchrow(
            "SELECT norad_id, international_designator, object_name, object_type, "
            "owner, owner_name, launch_date, launch_site, decay_date, ops_status, "
            "rcs_m2, source, satcat_snapshot_date "
            "FROM satellite_info WHERE norad_id = $1",
            norad_id,
        )
    if row is None:
        return SatelliteEnrichment(norad_id=norad_id, available=False)


    ot = row["object_type"]
    return SatelliteEnrichment(
        norad_id=row["norad_id"],
        available=True,
        international_designator=row["international_designator"],
        object_name=row["object_name"],
        object_type=ot,
        object_type_label=OBJECT_TYPE_LABELS.get(ot) if ot else None,
        owner=row["owner"],
        owner_name=row["owner_name"],
        launch_date=_iso(row["launch_date"]),
        launch_site=row["launch_site"],
        decay_date=_iso(row["decay_date"]),
        ops_status=row["ops_status"],
        rcs_m2=row["rcs_m2"],
        source=row["source"],
        satcat_snapshot_date=_iso(row["satcat_snapshot_date"]),
    )


@router.get("/reentry", response_model=ReentryResponse)
async def reentry(
    request: Request,
    region: str = Query("all", pattern="^(all|aoi)$"),
    limit: int = Query(200, ge=1, le=1000),
) -> Any:
    """Predicted satellite reentries (Space-Track TIP, migration 017), joined to
    S1 SATCAT identity, ordered soonest-first. Because TIP predictions REFINE
    over time (each a new message, all kept), the row returned per object is:
      * region='all' — its LATEST prediction overall (current best);
      * region='aoi' — its latest IN-REGION prediction (the seven-country watch
        list — an object whose predicted impact has been INSIDE/NEAR the AOI,
        even if a later message moved outside; the append history makes this
        answerable).
    The predicted point is IMPRECISE — window_min (± minutes) and next_report
    carry the uncertainty; never treat lat/lon as exact."""
    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (r.norad_id)
                r.norad_id, r.decay_epoch, r.window_min, r.lat, r.lon,
                r.direction, r.next_report, r.high_interest, r.country,
                r.near_country, r.distance_km, r.msg_epoch, r.source,
                i.object_name, i.owner, i.owner_name, i.object_type
            FROM satellite_reentry r
            LEFT JOIN satellite_info i ON i.norad_id = r.norad_id
            WHERE ($1 = 'all' OR r.country IS NOT NULL OR r.near_country IS NOT NULL)
            ORDER BY r.norad_id, r.msg_epoch DESC NULLS LAST
            """,
            region,
        )
        rows = sorted(
            rows,
            key=lambda r: (r["decay_epoch"] is None, r["decay_epoch"]),
        )[:limit]


    events = []
    inside = near = 0
    for r in rows:
        in_c, near_c = r["country"], r["near_country"]
        if in_c:
            inside += 1
        elif near_c:
            near += 1
        events.append(SatelliteReentry(
            norad_id=r["norad_id"],
            object_name=r["object_name"], owner=r["owner"],
            owner_name=r["owner_name"], object_type=r["object_type"],
            decay_epoch=_iso(r["decay_epoch"]), window_min=r["window_min"],
            lat=r["lat"], lon=r["lon"], direction=r["direction"],
            next_report=r["next_report"], high_interest=r["high_interest"],
            country=in_c, near_country=near_c, distance_km=r["distance_km"],
            aoi=bool(in_c or near_c), msg_epoch=_iso(r["msg_epoch"]), source=r["source"],
        ))
    return ReentryResponse(
        available=bool(events), count=len(events),
        inside=inside, near=near, region=region, events=events,
    )
