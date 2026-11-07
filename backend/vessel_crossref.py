"""
AIS × MarineTraffic cross-source correlation — pure, unit-testable core.

The two maritime sources have DIFFERENT identity keys (AIS = MMSI, MarineTraffic
= ship_id) and MarineTraffic almost never carries an MMSI, so they cannot be
joined on a key. This module decides which MarineTraffic ship_id and which AIS
MMSI are the SAME physical vessel, from the evidence both feeds DO share:

  * NAME — the strong anchor. Within a small AOI + time window a normalized-name
    match is highly discriminative (verified against live data: EDT ARGONAUT,
    PROTI, DEEP BLUE … match one-to-one). Names are normalized (uppercase,
    strip AIS '@' padding + punctuation) before comparison.
  * POSITION over TIME — corroboration. The same vessel seen by both feeds must,
    at the closest-in-time pair of fixes, be either co-located (direct
    co-observation) or at a separation the vessel could plausibly have travelled
    in the time gap. A name match whose positions are KINEMATICALLY IMPOSSIBLE is
    a likely name collision (two different hulls), not a link.
  * DIMENSIONS — corroboration. Matching length/beam (±tolerance) strengthens a
    link; a hard conflict is evidence AGAINST it.

Confidence is honest and tiered — `strong` / `probable` / `weak` — with the
evidence attached, so a derived link is never mistaken for a broadcast fact.
This is a LEAD generator (coverage + spoof/collision signals), not proof.

No I/O here: the DB runner (maritime side) feeds already-fetched observations in
and persists the resulting links. That keeps the matching logic fully testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from math import acos, cos, radians, sin
from typing import Any

# --- tolerances (documented, tunable) --------------------------------------
NAME_MIN_LEN = 3          # ignore 1-2 char normalized names (not discriminative)
DIRECT_TIME_MIN = 90.0    # ≤ this gap + close distance ⇒ direct co-observation
DIRECT_DIST_KM = 12.0     # ≤ this at a small time gap ⇒ same place, same time
MAX_SPEED_KMH = 80.0      # ~43 kn — above this implied speed, positions can't be one hull
DIM_TOL = 0.15            # ±15% length/beam agreement


def normalize_name(name: str | None) -> str | None:
    """Uppercase, drop AIS '@' padding and all non-alphanumerics, collapse — the
    comparable form. Returns None if nothing discriminative remains."""
    if not name:
        return None
    n = re.sub(r"[^A-Za-z0-9]", "", name.upper())
    return n if len(n) >= NAME_MIN_LEN else None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km. Clamped to the valid acos domain so tiny
    floating-point overshoots on identical points never raise."""
    a = (sin(radians(lat1)) * sin(radians(lat2))
         + cos(radians(lat1)) * cos(radians(lat2)) * cos(radians(lon1 - lon2)))
    return 6371.0 * acos(max(-1.0, min(1.0, a)))


def dimensions_match(l1: float | None, b1: float | None,
                     l2: float | None, b2: float | None) -> bool | None:
    """True if BOTH dimensions agree within tolerance, False if either conflicts,
    None if not enough data to judge (a missing dimension is not a conflict)."""
    pairs = [(l1, l2), (b1, b2)]
    checked = False
    for a, b in pairs:
        if a is None or b is None or a <= 0 or b <= 0:
            continue
        checked = True
        if abs(a - b) / max(a, b) > DIM_TOL:
            return False
    return True if checked else None


@dataclass(frozen=True, slots=True)
class Obs:
    """One position observation, source-agnostic (the runner adapts each table)."""
    key: str                 # ship_id (MarineTraffic) or mmsi (AIS)
    name: str | None
    lat: float
    lon: float
    ts: datetime
    length_m: float | None = None
    beam_m: float | None = None


@dataclass(frozen=True, slots=True)
class CrossMatch:
    ship_id: str
    mmsi: str
    mt_name: str | None
    ais_name: str | None
    confidence: str          # 'strong' | 'probable' | 'weak'
    score: float             # 0..1, for ranking / collision resolution
    distance_km: float       # at the closest-in-time fix pair
    dt_minutes: float
    dim_match: bool | None
    reason: str
    ambiguous: bool = False  # name matched >1 equally-good counterpart
    evidence: dict[str, Any] = field(default_factory=dict)


def _best_pair(a: list[Obs], b: list[Obs]) -> tuple[float, float, Obs, Obs]:
    """The (distance_km, dt_minutes, obs_a, obs_b) of the closest-IN-TIME fix
    pair between two observation lists — the moment the two feeds are most
    comparable."""
    best: tuple[float, float, Obs, Obs] | None = None
    for oa in a:
        for ob in b:
            dt = abs((oa.ts - ob.ts).total_seconds()) / 60.0
            if best is None or dt < best[1]:
                best = (haversine_km(oa.lat, oa.lon, ob.lat, ob.lon), dt, oa, ob)
    assert best is not None
    return best


def _classify(distance_km: float, dt_min: float,
              dim_match: bool | None) -> tuple[str, float, str]:
    """(confidence, score, reason) for a NAME match, from its corroborating
    evidence. The name is always the anchor; this decides how strongly the rest
    confirms it — HONESTLY:

      * A dimension CONFLICT, or a position the hull could not physically have
        travelled in the time gap, vetoes to `weak` (a likely name collision, or
        an interesting position discrepancy / possible AIS spoof).
      * `strong` requires a real corroborator: a true co-observation (close in
        BOTH space and time), OR matching dimensions, OR co-location (same place,
        name matches). Note we do NOT treat "kinematically consistent over a large
        time gap" as strong — over many hours almost any two points in the AOI are
        'consistent', so that would inflate confidence on the name alone.
      * Otherwise `probable`: the name matched and nothing contradicts it, but the
        position could not independently confirm it.
    """
    dt_h = max(dt_min / 60.0, 1e-6)
    colocated = distance_km <= DIRECT_DIST_KM
    # Co-location is the STRONGEST single signal — two hulls cannot share a point,
    # so name + same place confirms identity even against a dimension conflict
    # (which is then a data-quality / possible-spoof NOTE, not a different vessel).
    if colocated:
        direct = dt_min <= DIRECT_TIME_MIN
        reason = "direct co-observation" if direct else "name + co-location"
        if dim_match is False:
            reason += " (dimension discrepancy)"
        return "strong", 0.95 if direct else 0.85, reason
    # Not co-located:
    if distance_km / dt_h > MAX_SPEED_KMH:      # impossible for one hull in the gap
        return "weak", 0.15, "positions kinematically inconsistent"
    if dim_match is False:
        return "weak", 0.1, "dimensions conflict (likely name collision)"
    if dim_match is True:
        return "strong", 0.85, "name + dimensions match"
    return "probable", 0.6, "name match; position non-contradictory"


def correlate(mt_obs: list[Obs], ais_obs: list[Obs]) -> list[CrossMatch]:
    """Name-anchored, position/dimension-corroborated matching. Returns at most
    one match per ship_id and per mmsi (one-to-one), each with its confidence and
    evidence. A ship_id whose best two candidates tie is flagged `ambiguous`."""
    mt_by_name: dict[str, list[str]] = {}
    ais_by_name: dict[str, list[str]] = {}
    mt_tracks: dict[str, list[Obs]] = {}
    ais_tracks: dict[str, list[Obs]] = {}
    for o in mt_obs:
        mt_tracks.setdefault(o.key, []).append(o)
    for o in ais_obs:
        ais_tracks.setdefault(o.key, []).append(o)
    # one normalized name per track (the modal / first non-null)
    mt_name: dict[str, str | None] = {}
    for k, obs in mt_tracks.items():
        nn = next((normalize_name(o.name) for o in obs if normalize_name(o.name)), None)
        mt_name[k] = nn
        if nn:
            mt_by_name.setdefault(nn, []).append(k)
    ais_name: dict[str, str | None] = {}
    for k, obs in ais_tracks.items():
        nn = next((normalize_name(o.name) for o in obs if normalize_name(o.name)), None)
        ais_name[k] = nn
        if nn:
            ais_by_name.setdefault(nn, []).append(k)

    # build every candidate (ship_id, mmsi) sharing a normalized name
    candidates: list[CrossMatch] = []
    for nn, ships in mt_by_name.items():
        mmsis = ais_by_name.get(nn)
        if not mmsis:
            continue
        for ship_id in ships:
            for mmsi in mmsis:
                dist, dt, oa, ob = _best_pair(mt_tracks[ship_id], ais_tracks[mmsi])
                dm = dimensions_match(oa.length_m, oa.beam_m, ob.length_m, ob.beam_m)
                conf, score, reason = _classify(dist, dt, dm)
                candidates.append(CrossMatch(
                    ship_id=ship_id, mmsi=mmsi,
                    mt_name=oa.name, ais_name=ob.name,
                    confidence=conf, score=score,
                    distance_km=round(dist, 2), dt_minutes=round(dt, 1),
                    dim_match=dm, reason=reason,
                    evidence={"name_key": nn,
                              "mt_dims": [oa.length_m, oa.beam_m],
                              "ais_dims": [ob.length_m, ob.beam_m]},
                ))

    return _resolve_one_to_one(candidates)


def _resolve_one_to_one(cands: list[CrossMatch]) -> list[CrossMatch]:
    """Keep the best candidate per ship_id and per mmsi (highest score). When a
    ship_id's top two candidates tie on score, keep the top but flag it
    `ambiguous` — an honest 'name collision, could not disambiguate'."""
    by_ship: dict[str, list[CrossMatch]] = {}
    for c in cands:
        by_ship.setdefault(c.ship_id, []).append(c)

    chosen: list[CrossMatch] = []
    for _ship_id, cs in by_ship.items():
        cs.sort(key=lambda c: c.score, reverse=True)
        top = cs[0]
        ambiguous = len(cs) > 1 and abs(cs[1].score - top.score) < 1e-9
        chosen.append(replace(top, ambiguous=ambiguous))

    # enforce mmsi uniqueness too: if two ship_ids claim one mmsi, keep the higher
    best_for_mmsi: dict[str, CrossMatch] = {}
    for c in sorted(chosen, key=lambda c: c.score, reverse=True):
        cur = best_for_mmsi.get(c.mmsi)
        if cur is None:
            best_for_mmsi[c.mmsi] = c
        # a lower-scoring collision on the same mmsi is dropped (not the same hull twice)
    return sorted(best_for_mmsi.values(), key=lambda c: (-c.score, c.ship_id))


# ===================================================================
# DB runner — reads both sources over a window, correlates, and persists the
# derived links into vessel_xref (migration 034). Kept thin: all the judgement
# is in the pure core above, so this is only I/O + adaptation.

# Eastern-Mediterranean box MarineTraffic covers — bounds the AIS scan.
DEFAULT_BOX = (31.0, 36.0, 32.0, 36.0)   # (lat_min, lat_max, lon_min, lon_max)

_MT_FETCH = """
SELECT ship_id, name, latitude, longitude, ts, length_m, beam_m, flag
FROM mt_vessel_states
WHERE ts BETWEEN $1 AND $2 AND latitude IS NOT NULL AND longitude IS NOT NULL
"""

# AIS dimensions live on the vessel_identity spine (M2b), joined by mmsi.
_AIS_FETCH = """
SELECT vs.mmsi, vs.name, vs.latitude, vs.longitude, vs.ts, vi.length_m, vi.beam_m
FROM vessel_states vs
LEFT JOIN vessel_identity vi ON vi.mmsi = vs.mmsi
WHERE vs.source = 'aisstream' AND vs.ts BETWEEN $1 AND $2
  AND vs.name IS NOT NULL
  AND vs.latitude BETWEEN $3 AND $4 AND vs.longitude BETWEEN $5 AND $6
"""

# Derived-link upsert. The WHERE guard means a FED link — the MMSI came from
# MarineTraffic itself, whether on the tile record ('marinetraffic-feed') or from
# its per-vessel detail endpoint ('marinetraffic-detail') — is NEVER overwritten
# by a derived one; a derived row updates only itself or a bare row.
#
# 'marinetraffic-detail' must be listed here explicitly: in practice it is the
# ONLY fed source that ever appears, because the tile feed omits MMSI entirely
# (zero occurrences across 2.94M archived rows). Guarding only the tile value
# would leave every real fed link overwritable by a correlation guess.
_XREF_CORR_UPSERT = """
INSERT INTO vessel_xref
    (ship_id, mmsi, name, flag, link_source,
     match_confidence, match_score, match_distance_km, match_dt_minutes,
     match_ambiguous, match_reason, linked_at, updated_at)
SELECT d.ship_id, d.mmsi, d.name, d.flag, 'ais-correlation',
       d.conf, d.score, d.dist, d.dt, d.amb, d.reason, now(), now()
FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[],
            $6::float8[], $7::float8[], $8::float8[], $9::bool[], $10::text[])
     AS d(ship_id, mmsi, name, flag, conf, score, dist, dt, amb, reason)
ON CONFLICT (ship_id) DO UPDATE SET
    mmsi              = EXCLUDED.mmsi,
    name              = COALESCE(vessel_xref.name, EXCLUDED.name),
    flag              = COALESCE(vessel_xref.flag, EXCLUDED.flag),
    link_source       = 'ais-correlation',
    match_confidence  = EXCLUDED.match_confidence,
    match_score       = EXCLUDED.match_score,
    match_distance_km = EXCLUDED.match_distance_km,
    match_dt_minutes  = EXCLUDED.match_dt_minutes,
    match_ambiguous   = EXCLUDED.match_ambiguous,
    match_reason      = EXCLUDED.match_reason,
    updated_at        = now()
WHERE vessel_xref.link_source NOT IN ('marinetraffic-feed', 'marinetraffic-detail')
"""


def _obs_from_rows(rows: list, key: str) -> tuple[list[Obs], dict[str, str | None]]:
    """DB rows → Obs list, plus a key→flag map (only present for the MT rows)."""
    obs: list[Obs] = []
    flags: dict[str, str | None] = {}
    for r in rows:
        obs.append(Obs(
            key=str(r[key]), name=r["name"], lat=r["latitude"], lon=r["longitude"],
            ts=r["ts"], length_m=r["length_m"], beam_m=r["beam_m"],
        ))
        if "flag" in r.keys():
            flags.setdefault(str(r[key]), r["flag"])
    return obs, flags


async def correlate_and_store(dsn: str, *, window: tuple | None = None,
                              box: tuple[float, float, float, float] = DEFAULT_BOX,
                              ais_pad_days: float = 2.0) -> dict[str, Any]:
    """Correlate MarineTraffic × AIS over a time window and persist the derived
    links. `window` defaults to the full span of mt_vessel_states. The AIS scan
    is PADDED by ais_pad_days on each side: MarineTraffic is a periodic scrape
    and AIS fixes for the same hull are often offset by hours, so a scan bounded
    to the MarineTraffic span alone would miss them (the name anchor still
    discriminates within the small AOI; the confidence tier reflects the weaker
    positional corroboration a large time gap gives). Returns a report (counts by
    confidence). Idempotent: re-running upserts the same link set."""
    from datetime import timedelta

    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        if window is None:
            span = await conn.fetchrow("SELECT min(ts) a, max(ts) b FROM mt_vessel_states")
            if not span or span["a"] is None:
                return {"stored": 0, "note": "no MarineTraffic data"}
            window = (span["a"], span["b"])
        pad = timedelta(days=ais_pad_days)
        mt_rows = await conn.fetch(_MT_FETCH, window[0], window[1])
        ais_rows = await conn.fetch(_AIS_FETCH, window[0] - pad, window[1] + pad,
                                    box[0], box[1], box[2], box[3])
        mt_obs, mt_flags = _obs_from_rows(mt_rows, "ship_id")
        ais_obs, _ = _obs_from_rows(ais_rows, "mmsi")

        matches = correlate(mt_obs, ais_obs)
        if matches:
            cols: list[list[Any]] = [[] for _ in range(10)]
            for m in matches:
                vals = [m.ship_id, m.mmsi, m.mt_name, mt_flags.get(m.ship_id),
                        m.confidence, m.score, m.distance_km, m.dt_minutes,
                        m.ambiguous, m.reason]
                for i, v in enumerate(vals):
                    cols[i].append(v)
            await conn.execute(_XREF_CORR_UPSERT, *cols)

        by_conf: dict[str, int] = {}
        for m in matches:
            by_conf[m.confidence] = by_conf.get(m.confidence, 0) + 1
        return {
            "window": [window[0].isoformat(), window[1].isoformat()],
            "mt_vessels": len({o.key for o in mt_obs}),
            "ais_vessels": len({o.key for o in ais_obs}),
            "matches": len(matches),
            "by_confidence": by_conf,
            "ambiguous": sum(1 for m in matches if m.ambiguous),
        }
    finally:
        await conn.close()
