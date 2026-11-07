"""Surveillance zones × dwell + port visits — /maritime/zone-dwell /maritime/port-visits."""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import APIRouter, Query, Request
from ..config import settings
from .. import ports as port_gaz
from .. import port_visit as pvisit
from .. import zones as zone_gaz
from .. import zone_dwell as zdwell
from .. import zone_baseline as zbase
from ..coverage_mask import build_busy_mask, build_port_mask
from ..db import (
    DB_ERRORS,
)
from ..gap_detect import reconstruct_outage_windows
from ..geo_area import haversine_km
from ..models import (
    PortSummary, PortVisitCompleteness, PortVisitCoverage, PortVisitFunnel,
    PortVisitGeometry, PortVisitResponse, PortVisitSession,
    ZoneDwellAccounting, ZoneDwellReadiness, ZoneDwellResponse, ZoneDwellSession, ZoneDwellSummary,
)

from .shared import _db, _iso, db_503

router = APIRouter()


# --- ZONES-2 — on-query zone dwell (annotation-only; no events, no composite) ---
# Mirrors the airport on-query pattern (resolve from the committed gazetteer → one
# constrained, time-bounded GiST query → PURE classification). Two signals + honest
# gating (backend.zone_dwell). The A2 port/busy masks are REUSED (not duplicated) as
# the anchorage subtractor; their ~600 ms build is cached on app.state per TTL so only
# the first request in a window pays. NOTHING is written to maritime_events.
ZONE_DWELL_DEFAULT_HOURS = 168


ZONE_DWELL_MAX_HOURS = 720


ZONE_MASK_TTL_S = settings.zone_mask_ttl_s


_ZDWELL_VERDICT_ORDER = {
    zdwell.V_INTRUSION: 0, zdwell.V_LEAD: 1, zdwell.V_DEMOTED_TYPE: 2,
    zdwell.V_EXCLUDED_ANCHORAGE: 3, zdwell.V_EXCLUDED_SHORT: 3,
}


SHIP_TYPE_CAVEAT = (
    "Ship type is self-reported AIS static (the nav_status/draught class the project "
    "distrusts). A false type declaration is a cloaking vector against the type-gate — "
    "a gated-out session with an unverifiable/inconsistent identity is flagged "
    "cloaking_exposed."
)


CLOAKING_NOTE = (
    "Demoted-by-type sessions whose vessel identity is UNVERIFIABLE (no hull dimensions) "
    "or INCONSISTENT (imo_quality checksum_fail/malformed/contested, or a "
    "dimension_conflict flag). This is the honest size of the type-gate's blind spot; a "
    "cloaking vessel would declare a low-suspicion type to be demoted here."
)


async def _zone_masks(request: Request):
    """The A2 port + busy masks, cached on app.state per ZONE_MASK_TTL_S (each build
    scans vessel_states ~200-400 ms). Reused, never duplicated."""
    st = request.app.state
    cache = getattr(st, "_zone_masks_cache", None)
    now = time.monotonic()
    if cache is not None and (now - cache[2]) < ZONE_MASK_TTL_S:
        return cache[0], cache[1]
    pool = await _db(request).pool()
    async with pool.acquire() as conn:
        port = await build_port_mask(conn)
        busy = await build_busy_mask(conn)
    st._zone_masks_cache = (port, busy, now)
    return port, busy


def _type_unverifiable(length_m, beam_m, imo_quality, imo_flags) -> bool:
    """Cloaking axis: is a vessel's self-reported type unverifiable/inconsistent?
    Missing hull dimensions (nothing to sanity-check the type against), or a
    problematic IMO identity (IMO-1 axis: checksum_fail/malformed/contested), or a
    declared-dimension conflict. NULL imo_quality (simply not-yet-assessed) is NOT
    counted — only genuinely unverifiable/inconsistent identities."""
    if length_m is None and beam_m is None:
        return True
    if imo_quality in ("checksum_fail", "malformed", "contested"):
        return True
    if imo_flags and "dimension_conflict" in imo_flags:
        return True
    return False


@router.get("/maritime/zone-dwell", response_model=ZoneDwellResponse)
async def maritime_zone_dwell(
    request: Request,
    zone_id: str | None = Query(None, max_length=64, description="a single zone; omit for all detectable zones"),
    hours: int = Query(ZONE_DWELL_DEFAULT_HOURS, ge=1, le=ZONE_DWELL_MAX_HOURS),
    signal: str = Query("all", pattern="^(all|safety_zone_intrusion|standoff_dwell|field_dwell)$"),
    include_gated: bool = Query(True, description="keep the gated-out (excluded) set queryable"),
    limit: int = Query(500, ge=1, le=2000),
) -> Any:
    """ZONES-2 — on-query zone dwell at published offshore infrastructure, ranked and
    annotation-only (no zone_dwell rows, no composite — that is ZONES-3). Two signals:
    safety_zone_intrusion (platform 500 m core — rare, strong, type as context only) and
    standoff_dwell (500 m..3 km — fully gated); gas_fields give a single weaker
    field_dwell. Speed is position-derived (implied_speed_kn); the G3 rule drops
    implausible fixes; duration is a LOWER BOUND. Gating ranks down (never deletes) a
    self-reported low-suspicion type and subtracts A2 anchorage/busy cells; the exclusion
    accounting and cloaking exposure are reported. `caveat` MUST be surfaced."""
    if zone_id:
        zlist = [z for z in zone_gaz.detectable_zones() if z["zone_id"] == zone_id]
    else:
        zlist = zone_gaz.detectable_zones()
    resp = ZoneDwellResponse(window_hours=hours, zones_queried=len(zlist),
                             method=zdwell.METHOD, caveat=zdwell.CANDIDATE_CAVEAT)
    if not zlist:
        return resp

    try:
        port, busy = await _zone_masks(request)
        pool = await _db(request).pool()
    except DB_ERRORS as exc:
        resp.available = False
        resp.caveat = f"database unavailable: {exc}"
        return resp

    def in_anchorage(la: float, lo: float) -> bool:
        return port.contains(la, lo) or busy.contains(la, lo)

    all_sessions: list[tuple[zdwell.DwellSession, Any]] = []
    accounting: list[ZoneDwellAccounting] = []
    with db_503():
        for z in zlist:
            outer_m = zdwell.STANDOFF_OUTER_M if z["zone_type"] == "platform" else z["radius_m"]
            dlat = outer_m / 111320.0
            dlon = outer_m / (111320.0 * max(math.cos(math.radians(z["lat"])), 1e-6))
            rows = await pool.fetch(
                """
                SELECT vs.mmsi, vs.ts, vs.latitude, vs.longitude, vs.implied_speed_kn, vs.speed,
                       vs.ship_type AS vs_type, vi.ship_type AS vi_type, vi.length_m, vi.beam_m,
                       vi.imo_quality, vi.imo_flags, vi.name, vi.imo, vi.flag_country
                FROM vessel_states vs
                LEFT JOIN vessel_identity vi ON vi.mmsi = vs.mmsi
                WHERE vs.ts > now() - make_interval(hours => $1)
                  AND vs.position_quality IS DISTINCT FROM 'implausible'
                  AND point(vs.longitude, vs.latitude) <@ box(point($2, $3), point($4, $5))
                ORDER BY vs.mmsi, vs.ts
                """,
                hours, z["lon"] - dlon, z["lat"] - dlat, z["lon"] + dlon, z["lat"] + dlat,
            )
            by_mmsi: dict[str, list] = {}
            for r in rows:
                by_mmsi.setdefault(r["mmsi"], []).append(r)
            zsessions: list[tuple[zdwell.DwellSession, Any]] = []
            for mmsi, frows in by_mmsi.items():
                head = frows[0]
                ship_type = head["vi_type"] or head["vs_type"]
                tu = _type_unverifiable(head["length_m"], head["beam_m"],
                                        head["imo_quality"], head["imo_flags"])
                fixes = [(r["ts"], r["latitude"], r["longitude"], r["implied_speed_kn"], r["speed"])
                         for r in frows]
                for s in zdwell.zone_dwell_sessions(
                    z, mmsi, fixes, ship_type=ship_type, type_unverifiable=tu,
                    in_anchorage=in_anchorage,
                ):
                    zsessions.append((s, head))
            all_sessions.extend(zsessions)
            accounting.append(_zone_accounting(z, [s for s, _ in zsessions]))

    # summary over the FULL measured set (the honest picture), before display filters
    resp.summary = _zone_summary([s for s, _ in all_sessions])
    resp.accounting = accounting
    # baseline-readiness (ZONES-4 dormancy fix): make the standoff_dwell deferral a VISIBLE
    # state — which bar binds and the per-zone progress — over this query window.
    baselines = zbase.build_baselines([s for s, _ in all_sessions])
    resp.readiness = ZoneDwellReadiness(**zbase.readiness(baselines, window_hours=hours))

    # display list: signal + gated filters, ranked, capped
    def keep(s: zdwell.DwellSession) -> bool:
        if signal != "all" and s.signal != signal:
            return False
        if not include_gated and s.verdict in (zdwell.V_EXCLUDED_ANCHORAGE, zdwell.V_EXCLUDED_SHORT):
            return False
        return True

    shown = [(s, h) for (s, h) in all_sessions if keep(s)]
    shown.sort(key=lambda sh: (_ZDWELL_VERDICT_ORDER.get(sh[0].verdict, 9), -sh[0].duration_s))
    resp.sessions = [_zone_dwell_model(s, h) for s, h in shown[:limit]]
    resp.count = len(resp.sessions)
    resp.available = True
    return resp


def _zone_dwell_model(s: zdwell.DwellSession, head: Any) -> ZoneDwellSession:
    return ZoneDwellSession(
        zone_id=s.zone_id, zone_name=s.zone_name, zone_type=s.zone_type,
        radius_basis=s.radius_basis, coord_precision=s.coord_precision, signal=s.signal,
        mmsi=s.mmsi, imo=head["imo"], name=head["name"], flag_country=head["flag_country"],
        ship_type=s.ship_type, ship_type_caveat=SHIP_TYPE_CAVEAT,
        start_ts=_iso(s.start_ts), end_ts=_iso(s.end_ts),
        duration_s=s.duration_s, duration_is_lower_bound=s.duration_is_lower_bound,
        n_fixes=s.n_fixes, min_distance_m=s.min_distance_m,
        mean_implied_speed_kn=s.mean_implied_speed_kn, min_implied_speed_kn=s.min_implied_speed_kn,
        reported_sog_kn=s.reported_sog_kn, verdict=s.verdict,
        exclusion_reasons=list(s.exclusion_reasons), cloaking_exposed=s.cloaking_exposed,
    )


def _zone_accounting(z: dict[str, Any], sessions: list[zdwell.DwellSession]) -> ZoneDwellAccounting:
    def n(*verdicts: str) -> int:
        return sum(1 for s in sessions if s.verdict in verdicts)
    return ZoneDwellAccounting(
        zone_id=z["zone_id"], zone_name=z.get("zone_name"), zone_type=z["zone_type"],
        sessions_total=len(sessions), intrusions=n(zdwell.V_INTRUSION), leads=n(zdwell.V_LEAD),
        demoted_type=n(zdwell.V_DEMOTED_TYPE), excluded_anchorage=n(zdwell.V_EXCLUDED_ANCHORAGE),
        excluded_short=n(zdwell.V_EXCLUDED_SHORT), no_dwell_observed=not sessions,
    )


def _zone_summary(sessions: list[zdwell.DwellSession]) -> ZoneDwellSummary:
    def sig(name: str) -> int:
        return sum(1 for s in sessions if s.signal == name)
    def ver(*v: str) -> int:
        return sum(1 for s in sessions if s.verdict in v)
    return ZoneDwellSummary(
        safety_zone_intrusion=sig(zdwell.SAFETY_ZONE_INTRUSION),
        standoff_dwell=sig(zdwell.STANDOFF_DWELL), field_dwell=sig(zdwell.FIELD_DWELL),
        leads=ver(zdwell.V_INTRUSION, zdwell.V_LEAD), demoted=ver(zdwell.V_DEMOTED_TYPE),
        excluded=ver(zdwell.V_EXCLUDED_ANCHORAGE, zdwell.V_EXCLUDED_SHORT),
        cloaking_exposed_sessions=sum(1 for s in sessions if s.cloaking_exposed),
        cloaking_note=CLOAKING_NOTE,
    )


# ---------------------------------------------------------------------------
# PORT-2 — on-query port visits (measurement and context, annotation-only).
#
# ONE resolved port per call, never the gazetteer: PORT-pre measured ~167 ms/port
# and ~30 s for a 180-port sweep. No port_visit events, no migration, no composite
# admission, no alerts surface, no suspicion rank — all measured decisions, see
# backend/port_visit.py's NOT BUILT block.
# ---------------------------------------------------------------------------

PORT_VISIT_DEFAULT_HOURS = 168


PORT_VISIT_MAX_HOURS = 720


#: Largest radius in the gazetteer, in km — bounds the co-claimant prefilter below.
_MAX_PORT_RADIUS_KM = max(p["radius_m"] for p in port_gaz.all_ports()) / 1000.0


#: The stricter-than-G3 quality rule, mirrored from port_visit.accept_fix. NULL PASSES
#: (it means "not yet scored by the 15-min timer"); suspect and implausible are dropped
#: because a harbour radius is where on-land decoy positions land. Fixes are fetched
#: UNFILTERED so the pure module owns the rule and can attribute the drop per session.
PORT_QUALITY_RULE = ("position_quality NOT IN ('suspect','implausible') with NULL PASSING "
                     "(stricter than the standard G3 rule, which passes 'suspect': a "
                     "harbour radius is exactly where on-land decoy positions land; NULL "
                     "means 'not yet scored by the 15-min quality timer', so dropping it "
                     "would truncate every in-progress session)")


#: A CITED measurement, not a live recount (recounting = the 30 s sweep this refuses).
PORTS_OBSERVED_REFERENCE = ("43 of 180 WPI ports produced any session over a 16-day window "
                            "(PORT-pre, 2026-07-27). A silent port is a COVERAGE fact, "
                            "never 'no commerce'.")


PORT_FIX_SQL = """
SELECT vs.mmsi, vs.ts, vs.latitude, vs.longitude, vs.implied_speed_kn, vs.speed,
       vs.position_quality,
       COALESCE(vi.ship_type, vs.ship_type) AS ship_type,
       vi.imo, vi.name, vi.flag_country
FROM vessel_states vs
LEFT JOIN vessel_identity vi ON vi.mmsi = vs.mmsi
WHERE vs.ts > now() - make_interval(hours => $1)
  AND vs.latitude IS NOT NULL AND vs.longitude IS NOT NULL
  AND point(vs.longitude, vs.latitude) <@ box(point($2, $3), point($4, $5))
ORDER BY vs.mmsi, vs.ts
"""


def _port_summary(p: dict) -> PortSummary:
    return PortSummary(
        port_id=p["port_id"], port_name=p["port_name"],
        alternate_name=p.get("alternate_name"), unlocode=p.get("unlocode"),
        country=p.get("country"), lat=p["lat"], lon=p["lon"],
        radius_m=p["radius_m"], radius_basis=p["radius_basis"],
        coord_precision=p.get("coord_precision"), harbor_size=p.get("harbor_size"),
        harbor_type=p.get("harbor_type"), harbour_category=p.get("harbour_category"),
        source=p["source"], license=p["license"], source_ref=p["source_ref"],
        merges_with=list(p.get("merges_with") or []), source_note=p.get("source_note"),
    )


async def _port_outage_windows(pool) -> list:
    """Reconstructed feed-outage windows — REUSED from gap_detect (the G3-addendum
    machinery), never reimplemented, so a missing boundary can be attributed to a feed
    outage rather than blamed on the vessel."""
    ev = await pool.fetch(
        "SELECT ts, kind FROM signal_events WHERE source = 'aisstream' "
        "AND kind IN ('feed_disconnect','feed_connect') ORDER BY ts")
    cts = [r["ts"] for r in ev if r["kind"] == "feed_connect"]
    lp = await pool.fetch(
        "SELECT c AS ct, (SELECT max(v.ts) FROM vessel_states v "
        "WHERE v.source = 'aisstream' AND v.ts < c) AS last_pos "
        "FROM unnest($1::timestamptz[]) AS c", cts)
    return reconstruct_outage_windows(
        [(r["ts"], r["kind"]) for r in ev], datetime.now(timezone.utc),
        {r["ct"]: r["last_pos"] for r in lp})


@router.get("/maritime/port-visits", response_model=PortVisitResponse)
async def maritime_port_visits(
    request: Request,
    q: str = Query(min_length=1, max_length=64,
                   description="port name / alternate name / UN-LOCODE / port_id"),
    port_id: str | None = Query(None, max_length=64,
                                description="resolve visits for this specific port"),
    hours: int = Query(PORT_VISIT_DEFAULT_HOURS, ge=1, le=PORT_VISIT_MAX_HOURS),
    completeness: str = Query("all",
                              pattern="^(all|complete|open_ended|truncated|presence_only)$"),
    flank_pad_km: float = Query(
        pvisit.FLANK_PAD_KM, ge=0.0, le=50.0,
        description="how far beyond the port radius to look for the arrival/departure fix "
                    "that makes a boundary OBSERVED. Widens the aperture, not the port: it "
                    "cannot create or move a visit, only reveal a boundary that was already "
                    "there. Costs query time and shifts the Ambarli ratio (see funnel)."),
    limit: int = Query(500, ge=1, le=2000),
) -> Any:
    """PORT-2 — on-query port VISITS for one resolved port. Measurement and context, NOT a
    lead surface: a port visit is routine commerce (measured cargo 29 % / passenger 15 % /
    pleasure 14 % / tug 12 % / tanker 8.5 %), so nothing here writes events, enters the
    composite, feeds alerts, or carries a suspicion rank.

    Ports resolve IN-PROCESS from the committed gazetteer (`backend/ports.py`, no DB
    identity table — the airport precedent), searching name, alternate_name and UN-LOCODE.
    Visits are DERIVED on-query over the port's padded vicinity box (the `vessel_states`
    GiST path, ~167 ms/port) by the pure `backend.port_visit` module.

    The four completeness states are FIRST-CLASS — only ~1 in 5 visits has both boundaries
    observed, so `open_ended` / `truncated` / `presence_only` are the majority of the
    output, each with WHY its boundary is missing (outage / vessel_silence / window_edge).
    `arrival_ts_is_unknown` marks the two entry-missing states, where the lower-bound
    duration bounds an UNKNOWN quantity. `boundary_note` never reads "still in port".

    Every completeness figure is returned BESIDE the `geometry` that produced it, plus the
    transit/visit `funnel`, the quality-filter drop count, and gazetteer `coverage` context
    — because completeness is PARAMETER-DEPENDENT in two measured directions: PORT-pre
    measured `complete` moving 20.9 % → 54.9 % on the core radius, and PORT-2 measured
    19.7 % → 9.5 % on the flank pad alone.

    `flank_pad_km` exposes that second axis rather than tuning it away. The default stays
    conservative (10 km): raising a completeness figure by widening the default would be
    tuning a metric, not measuring the world. An analyst tracing ONE hull can widen it
    deliberately and pay the query cost. It widens only the APERTURE — the region searched
    for the arrival/departure fix — never the port, so it cannot create, move or extend a
    visit; it can only reveal a boundary that was already there. Note it also raises
    `vessels_outside_radius`, so the Ambarlı ratio is itself pad-dependent.

    `caveat` MUST be surfaced."""
    matches_raw = port_gaz.resolve(q)
    resp = PortVisitResponse(
        query=q, count=len(matches_raw), window_hours=hours, method=pvisit.METHOD,
        caveat=pvisit.VISIT_CAVEAT, matches=[_port_summary(p) for p in matches_raw],
        coverage=PortVisitCoverage(
            ports_in_gazetteer=len(port_gaz.all_ports()), ports_queried=0,
            ports_with_visits=0, ports_observed_reference=PORTS_OBSERVED_REFERENCE),
    )
    if not matches_raw:
        return resp

    # which port's visits — the explicit port_id (resolved directly, so it need not be
    # inside the capped q-matches), else the top match (the airport pattern)
    sel = None
    if port_id:
        pid = port_id.strip()
        sel = (next((p for p in matches_raw if p["port_id"] == pid), None)
               or next((p for p in port_gaz.resolve(pid) if p["port_id"] == pid), None))
    if sel is None:
        sel = matches_raw[0]
    resp.port = _port_summary(sel)
    resp.coverage.ports_queried = 1
    resp.geometry = PortVisitGeometry(
        radius_m=sel["radius_m"], radius_basis=sel["radius_basis"],
        coord_precision=sel.get("coord_precision"),
        gap_split_s=pvisit.GAP_SPLIT_S, low_speed_kn=pvisit.LOW_SPEED_KN,
        min_duration_s=pvisit.MIN_DURATION_S,
        min_low_speed_span_s=pvisit.MIN_LOW_SPEED_SPAN_S,
        majority_low_frac=pvisit.MAJORITY_LOW_FRAC, flank_s=pvisit.FLANK_S,
        flank_pad_km=flank_pad_km, quality_rule=PORT_QUALITY_RULE,
    )

    lamin, lomin, lamax, lomax = port_gaz.vicinity_box(sel, pad_km=flank_pad_km)

    # Attribution candidates, computed ONCE. port_gaz.ports_containing scans the whole
    # gazetteer, and calling it per fix at a dense port is ~9 M haversines (measured 3.5 s
    # at Haifa). Only ports whose own circle can reach into this port's padded box are
    # possible co-claimants, so narrow to that set first and keep the per-fix test tiny.
    _reach_km = sel["radius_m"] / 1000.0 + flank_pad_km
    _co = [p for p in port_gaz.ports_near(sel["lat"], sel["lon"],
                                          radius_km=_reach_km + _MAX_PORT_RADIUS_KM)
           if p["port_id"] != sel["port_id"]
           and p["distance_km"] <= _reach_km + p["radius_m"] / 1000.0]

    def _containing(lat: float, lon: float) -> list[dict]:
        return [p for p in _co
                if haversine_km(lat, lon, p["lat"], p["lon"]) * 1000.0 <= p["radius_m"]]

    try:
        pool = await _db(request).pool()
        rows = await pool.fetch(PORT_FIX_SQL, hours, lomin, lamin, lomax, lamax)
        outage_windows = await _port_outage_windows(pool)
        now_dt = await pool.fetchval("SELECT now()")
    except DB_ERRORS as exc:
        resp.available = False
        resp.caveat = f"database unavailable: {exc}"
        return resp

    window_start = now_dt - timedelta(hours=hours)
    by_mmsi: dict[str, list] = {}
    for r in rows:
        by_mmsi.setdefault(r["mmsi"], []).append(r)

    funnel = pvisit.PortFunnel(port_id=sel["port_id"], port_name=sel["port_name"])
    sessions: list[tuple] = []
    r_m = float(sel["radius_m"])
    for mmsi, frows in by_mmsi.items():
        head = frows[0]
        fixes = [pvisit.PortFix(ts=r["ts"], lat=r["latitude"], lon=r["longitude"],
                                implied_speed_kn=r["implied_speed_kn"],
                                reported_sog_kn=r["speed"],
                                position_quality=r["position_quality"])
                 for r in frows]
        # in/outside-radius vessel split, for the Ambarlı rule (geometry vs traffic)
        if any(haversine_km(f.lat, f.lon, sel["lat"], sel["lon"]) * 1000.0 <= r_m
               for f in fixes):
            funnel.vessels_in_radius += 1
        else:
            funnel.vessels_outside_radius += 1
        vs, dropped_at, dropped_fixes = pvisit.port_visits(
            sel, mmsi, fixes, window_start=window_start, window_end=now_dt,
            outage_windows=outage_windows, containing_ports=_containing,
        )
        for bar, n in dropped_at.items():
            funnel.dropped_at[bar] = funnel.dropped_at.get(bar, 0) + n
        funnel.dropped_fixes_quality += dropped_fixes
        for v in vs:
            sessions.append((v, head))

    funnel.visits = len(sessions)
    funnel.raw_crossings = funnel.visits + sum(funnel.dropped_at.values())
    funnel.no_presence_observed = funnel.raw_crossings == 0
    funnel.geometry_under_covers = pvisit.geometry_under_covers(
        sel, funnel.vessels_in_radius, funnel.vessels_outside_radius)
    resp.funnel = [PortVisitFunnel(**funnel.as_dict())]
    resp.coverage.ports_with_visits = 1 if sessions else 0

    # the four-state distribution over the FULL measured set, before display filters
    def n_state(s: str) -> int:
        return sum(1 for v, _ in sessions if v.completeness == s)
    entry_reasons: dict[str, int] = {}
    exit_reasons: dict[str, int] = {}
    for v, _ in sessions:
        if v.entry_missing_reason:
            entry_reasons[v.entry_missing_reason] = entry_reasons.get(v.entry_missing_reason, 0) + 1
        if v.exit_missing_reason:
            exit_reasons[v.exit_missing_reason] = exit_reasons.get(v.exit_missing_reason, 0) + 1
    resp.completeness = PortVisitCompleteness(
        complete=n_state(pvisit.COMPLETE), open_ended=n_state(pvisit.OPEN_ENDED),
        truncated=n_state(pvisit.TRUNCATED), presence_only=n_state(pvisit.PRESENCE_ONLY),
        arrival_unknown=sum(1 for v, _ in sessions if v.arrival_ts_is_unknown),
        missing_entry_reasons=entry_reasons, missing_exit_reasons=exit_reasons,
    )

    keep = [(v, h) for v, h in sessions
            if completeness == "all" or v.completeness == completeness]
    keep.sort(key=lambda x: x[0].start_ts, reverse=True)
    resp.sessions = [
        PortVisitSession(
            port_id=v.port_id, port_name=v.port_name, mmsi=v.mmsi,
            imo=h["imo"], name=h["name"], flag_country=h["flag_country"],
            ship_type=h["ship_type"],
            start_ts=v.start_ts.isoformat(), end_ts=v.end_ts.isoformat(),
            duration_s=v.duration_s, duration_is_lower_bound=v.duration_is_lower_bound,
            low_speed_span_s=v.low_speed_span_s, n_fixes=v.n_fixes,
            n_low_speed_fixes=v.n_low_speed_fixes, frac_low_speed=v.frac_low_speed,
            min_distance_m=v.min_distance_m, lat=v.lat, lon=v.lon,
            mean_implied_speed_kn=v.mean_implied_speed_kn,
            min_implied_speed_kn=v.min_implied_speed_kn,
            reported_sog_kn=v.reported_sog_kn,
            completeness=v.completeness,
            entry_ts=v.entry_ts.isoformat() if v.entry_ts else None,
            exit_ts=v.exit_ts.isoformat() if v.exit_ts else None,
            entry_missing_reason=v.entry_missing_reason,
            exit_missing_reason=v.exit_missing_reason,
            entry_outage_gap_s=v.entry_outage_gap_s,
            exit_outage_gap_s=v.exit_outage_gap_s,
            arrival_ts_is_unknown=v.arrival_ts_is_unknown,
            boundary_note=pvisit.describe_boundary(v),
            radius_m=v.radius_m, radius_basis=v.radius_basis,
            coord_precision=v.coord_precision,
            candidate_ports=list(v.candidate_ports), dropped_fixes=v.dropped_fixes,
        )
        for v, h in keep[:limit]
    ]
    resp.count = len(sessions)      # the FULL measured count, before the display cap
    resp.returned = len(resp.sessions)
    return resp
