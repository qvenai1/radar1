"""
Maritime DB layer — the sea-going parallel of ingest.py's Ingester.

Two entry points, one idempotent write path (exactly the aviation design):
  * VesselIngester — live mirror used by tracker_maritime.py (failure-tolerant;
    a DB outage never stops JSONL archiving);
  * backfill_vessels() — rebuild vessel_states from vessels.jsonl, safe to
    re-run (natural key (mmsi, ts, source) + ON CONFLICT DO NOTHING).

Row shape is the VesselState dict (maritime/schema.py). ts is the vessel's
own position-fix timestamp (ISO 8601) — parsed to a tz-aware datetime here.
Rows without mmsi or a usable timestamp are skipped (cannot key them).
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import asyncpg

from backend.geo_area import GEO_SOURCE, get_area, territorial_sea_km
from backend.mmsi_flag import MID_SOURCE, mmsi_to_flag

logger = logging.getLogger("maritime.db")

# M2a: deterministic flag-state (country of registration) from the MMSI's MID,
# cached per-vessel in vessel_identity. Idempotent + stable derived_at via
# ON CONFLICT DO NOTHING — the derivation is deterministic, so an already-derived
# MMSI is never rewritten. Additive to the M1 path; never touches vessel_states.
IDENTITY_UPSERT_SQL = """
INSERT INTO vessel_identity (mmsi, flag_country, station_type, flag_source, derived_at)
SELECT d.mmsi, d.flag_country, d.station_type, $4, now()
FROM unnest($1::text[], $2::text[], $3::text[]) AS d(mmsi, flag_country, station_type)
ON CONFLICT (mmsi) DO NOTHING
"""

# M2b: promote the merged AIS-static identity into the vessel_identity SPINE.
# SLOW-CHANGING identity only (voyage state stays on vessel_states). The M2a
# upsert above runs first in the same transaction, so every batch MMSI already
# has a row — this is an UPDATE. COALESCE keeps a known value when a later report
# omits a field (never wipes with NULL); the WHERE-changed guard makes it a no-op
# when nothing actually changed, so identity_updated_at is stable and re-ingest
# causes 0 spurious writes. Touches ONLY the identity block — flag/station (M2a)
# and vessel_states (M1) are untouched.
SPINE_FIELDS = ("name", "ship_type", "ship_type_code", "callsign", "imo",
                "length_m", "beam_m")

IDENTITY_PROMOTE_SQL = """
UPDATE vessel_identity AS vi SET
    name                = COALESCE(d.name, vi.name),
    ship_type           = COALESCE(d.ship_type, vi.ship_type),
    ship_type_code      = COALESCE(d.ship_type_code, vi.ship_type_code),
    callsign            = COALESCE(d.callsign, vi.callsign),
    imo                 = COALESCE(d.imo, vi.imo),
    length_m            = COALESCE(d.length_m, vi.length_m),
    beam_m              = COALESCE(d.beam_m, vi.beam_m),
    identity_source     = 'ais-static',
    identity_updated_at = now()
FROM unnest($1::text[], $2::text[], $3::text[], $4::int[], $5::text[],
            $6::text[], $7::float8[], $8::float8[])
     AS d(mmsi, name, ship_type, ship_type_code, callsign, imo, length_m, beam_m)
WHERE vi.mmsi = d.mmsi
  AND (   vi.name           IS DISTINCT FROM COALESCE(d.name, vi.name)
       OR vi.ship_type      IS DISTINCT FROM COALESCE(d.ship_type, vi.ship_type)
       OR vi.ship_type_code IS DISTINCT FROM COALESCE(d.ship_type_code, vi.ship_type_code)
       OR vi.callsign       IS DISTINCT FROM COALESCE(d.callsign, vi.callsign)
       OR vi.imo            IS DISTINCT FROM COALESCE(d.imo, vi.imo)
       OR vi.length_m       IS DISTINCT FROM COALESCE(d.length_m, vi.length_m)
       OR vi.beam_m         IS DISTINCT FROM COALESCE(d.beam_m, vi.beam_m))
"""


def identity_spine_fields(rec: dict[str, Any]) -> dict[str, Any] | None:
    """The durable-identity subset of a vessel dict, or None if it carries no
    identity. Includes ONLY slow-changing identity (name/type/callsign/imo/
    dimensions) — NEVER voyage state (destination/draught/eta), which stays on
    the vessel_states time series."""
    fields = {k: rec.get(k) for k in SPINE_FIELDS}
    return fields if any(v is not None for v in fields.values()) else None

# $18..$21 = maritime geo classification (migration 021, ADDITIVE): the shared
# scraplan classifier (backend.geo_area) applied to THIS vessel's lat/lon, so a
# position's territorial-sea membership is a property of the position, exactly
# as the aircraft layer classifies every feed from one asset. Appended AFTER the
# base columns so existing INSERT parameter positions are unchanged.
INSERT_SQL = """
INSERT INTO vessel_states (
    ts, mmsi, source, data_origin, latitude, longitude, speed, course,
    heading, nav_status, imo, name, callsign, ship_type_code, ship_type,
    destination, draught,
    country, near_country, distance_km, geo_source
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8,
    $9, $10, $11, $12, $13, $14, $15,
    $16, $17,
    $18, $19, $20, $21
)
ON CONFLICT (mmsi, ts, source) DO NOTHING
"""

# count is recomputed from stored rows would be wrong here (we archive only
# the moved-vessel delta, not the full fleet) — so the live fleet count is
# passed in from the collector and trusted for this snapshot-metadata row.
UPSERT_SNAPSHOT_SQL = """
INSERT INTO vessel_snapshots (snapshot_time, source, ts, count,
                              lamin, lomin, lamax, lomax, generated_at)
VALUES ($1, $2, to_timestamp($1::bigint), $3, $4, $5, $6, $7, $8)
ON CONFLICT (snapshot_time, source) DO UPDATE SET
    count        = EXCLUDED.count,
    lamin        = COALESCE(vessel_snapshots.lamin, EXCLUDED.lamin),
    lomin        = COALESCE(vessel_snapshots.lomin, EXCLUDED.lomin),
    lamax        = COALESCE(vessel_snapshots.lamax, EXCLUDED.lamax),
    lomax        = COALESCE(vessel_snapshots.lomax, EXCLUDED.lomax),
    generated_at = COALESCE(vessel_snapshots.generated_at, EXCLUDED.generated_at)
"""


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def vessel_row(rec: dict[str, Any]) -> tuple | None:
    """VesselState dict → INSERT_SQL parameter tuple, or None if unkeyable."""
    mmsi = rec.get("mmsi")
    ts = _parse_ts(rec.get("timestamp"))
    if not (isinstance(mmsi, str) and mmsi.strip()) or ts is None:
        return None
    lat, lon = rec.get("lat"), rec.get("lon")
    if lat is None or lon is None:
        return None
    stc = rec.get("ship_type_code")
    # Maritime geo classification (migration 021) — scraplan's ONE shared
    # classifier (backend.geo_area), the same AEQD machinery the aircraft layer
    # uses, applied to THIS position with the 12 NM territorial-sea band. Every
    # row reaching here has a position (guarded above), so it is always
    # classified. A missing asset fails fast (GeoAssetError propagates), matching
    # the aircraft ingest path.
    geo = get_area().classify_maritime_columns(lat, lon, territorial_sea_km())
    return (
        ts,
        mmsi.strip(),
        str(rec.get("source") or "aisstream"),
        str(rec.get("data_origin") or "terrestrial"),
        lat, lon, rec.get("speed"), rec.get("course"),
        rec.get("heading"),
        rec.get("nav_status") if isinstance(rec.get("nav_status"), int) else None,
        rec.get("imo"), rec.get("name"), rec.get("callsign"),
        stc if isinstance(stc, int) else None,
        rec.get("ship_type"), rec.get("destination"), rec.get("draught"),
        geo.country, geo.near_country, geo.distance_km, GEO_SOURCE,
    )


def parse_lines(lines: Iterable[str]) -> tuple[list[tuple], int]:
    rows: list[tuple] = []
    invalid = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        row = vessel_row(rec) if isinstance(rec, dict) else None
        if row is None:
            invalid += 1
        else:
            rows.append(row)
    return rows, invalid


class VesselIngester:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._conn: asyncpg.Connection | None = None

    async def connect(self) -> None:
        self._conn = await asyncpg.connect(self.dsn)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> asyncpg.Connection:
        assert self._conn is not None, "call connect() first"
        return self._conn

    async def row_count(self) -> int:
        return await self.conn.fetchval("SELECT count(*) FROM vessel_states")

    async def ingest(self, rows: list[tuple], snapshot: dict[str, Any] | None = None,
                     identities: list[dict[str, Any]] | None = None) -> None:
        """Insert vessel rows (+ optional snapshot-metadata row) in ONE
        transaction. Idempotent via the natural-key ON CONFLICT DO NOTHING.

        `identities` (the position event dicts) drives the M2b identity-spine
        promotion — additive, and only slow-changing identity is promoted."""
        if not rows and snapshot is None:
            return
        async with self.conn.transaction():
            if rows:
                await self.conn.executemany(INSERT_SQL, rows)
                # M2a (additive): derive each distinct MMSI's flag/station locally
                # and cache it. Pure computation + a DO-NOTHING upsert; the M1
                # vessel_states write above is untouched by it.
                mmsis = sorted({r[1] for r in rows})   # r[1] = mmsi (vessel_row order)
                flags = [None] * len(mmsis)
                stations = [""] * len(mmsis)
                for i, m in enumerate(mmsis):
                    flags[i], stations[i] = mmsi_to_flag(m)
                await self.conn.execute(IDENTITY_UPSERT_SQL, mmsis, flags, stations, MID_SOURCE)
                # M2b (additive): promote the merged static identity into the
                # spine. Runs AFTER the M2a upsert (so every row exists) and only
                # touches the identity block; M1 geo + M2a flag are untouched.
                await self._promote_identities(identities)
            if snapshot is not None:
                b = snapshot.get("bbox") or {}
                gen = _parse_ts(snapshot.get("generated_at"))
                await self.conn.execute(
                    UPSERT_SNAPSHOT_SQL,
                    snapshot["snapshot_time"], snapshot.get("source", "aisstream"),
                    snapshot.get("count", len(rows)),
                    b.get("lamin"), b.get("lomin"), b.get("lamax"), b.get("lomax"),
                    gen,
                )

    async def _promote_identities(self, identities: list[dict[str, Any]] | None) -> None:
        """M2b: promote the current-best static identity per MMSI into the spine
        (latest-wins on change). Dedupe by MMSI (one UPDATE per vessel) and drop
        dicts with no identity to promote."""
        if not identities:
            return
        latest: dict[str, dict[str, Any]] = {}
        for rec in identities:
            mmsi = rec.get("mmsi")
            spine = identity_spine_fields(rec) if isinstance(rec, dict) else None
            if mmsi and spine is not None:
                latest[str(mmsi).strip()] = spine  # last event wins within the batch
        if not latest:
            return
        cols = {f: [] for f in ("mmsi", *SPINE_FIELDS)}
        for mmsi, spine in latest.items():
            cols["mmsi"].append(mmsi)
            for f in SPINE_FIELDS:
                cols[f].append(spine[f])
        await self.conn.execute(
            IDENTITY_PROMOTE_SQL,
            cols["mmsi"], cols["name"], cols["ship_type"], cols["ship_type_code"],
            cols["callsign"], cols["imo"], cols["length_m"], cols["beam_m"],
        )


# =====================================================================
# MarineTraffic source (migration 032/033) — the sea-going sibling of the
# aisstream path above, but keyed on MarineTraffic's own ship_id (mmsi is
# usually absent). A DEDICATED table + a ship_id↔mmsi bridge, never a hack
# onto vessel_states' frozen (mmsi, ts, source) key. Everything here is
# ADDITIVE: the aisstream INSERT/identity path above is untouched.

# Column order MUST match migration 032. mmsi is a nullable enrichment column
# (the link lives in vessel_xref), never part of the key.
MT_INSERT_SQL = """
INSERT INTO mt_vessel_states (
    ts, ship_id, source, data_origin, mmsi, latitude, longitude, speed, course,
    heading, nav_status, imo, name, callsign, ship_type_code, ship_type,
    destination, draught, length_m, beam_m, flag,
    country, near_country, distance_km, geo_source
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9,
    $10, $11, $12, $13, $14, $15, $16,
    $17, $18, $19, $20, $21,
    $22, $23, $24, $25
)
ON CONFLICT (ship_id, ts, source) DO NOTHING
"""

# Identity bridge (migration 033): link ship_id → mmsi ONLY when MarineTraffic
# actually carried an mmsi (link_source 'marinetraffic-feed'). COALESCE never
# wipes a known value with NULL; the WHERE-changed guard makes a re-link a
# no-op so updated_at stays stable and re-ingest causes 0 spurious writes.
MT_XREF_UPSERT_SQL = """
INSERT INTO vessel_xref (ship_id, mmsi, name, flag, link_source, linked_at, updated_at)
SELECT d.ship_id, d.mmsi, d.name, d.flag, 'marinetraffic-feed', now(), now()
FROM unnest($1::text[], $2::text[], $3::text[], $4::text[])
     AS d(ship_id, mmsi, name, flag)
ON CONFLICT (ship_id) DO UPDATE SET
    mmsi       = COALESCE(EXCLUDED.mmsi, vessel_xref.mmsi),
    name       = COALESCE(EXCLUDED.name, vessel_xref.name),
    flag       = COALESCE(EXCLUDED.flag, vessel_xref.flag),
    updated_at = now()
WHERE vessel_xref.mmsi IS DISTINCT FROM COALESCE(EXCLUDED.mmsi, vessel_xref.mmsi)
   OR vessel_xref.name IS DISTINCT FROM COALESCE(EXCLUDED.name, vessel_xref.name)
   OR vessel_xref.flag IS DISTINCT FROM COALESCE(EXCLUDED.flag, vessel_xref.flag)
"""


def mt_vessel_row(rec: dict[str, Any]) -> tuple | None:
    """VesselState dict (live Fleet event OR backfilled line) → MT_INSERT_SQL
    tuple, or None if unkeyable. Keyed on ship_id (not mmsi); a row without a
    ship_id, a usable timestamp, or a position cannot be archived."""
    ship_id = rec.get("ship_id")
    ts = _parse_ts(rec.get("timestamp"))
    if not (isinstance(ship_id, str) and ship_id.strip()) or ts is None:
        return None
    lat, lon = rec.get("lat"), rec.get("lon")
    if lat is None or lon is None:
        return None
    stc = rec.get("ship_type_code")
    mmsi = rec.get("mmsi")
    mmsi = mmsi.strip() if isinstance(mmsi, str) and mmsi.strip() else None
    # Same shared classifier as the aisstream path (backend.geo_area) — identical
    # 12 NM territorial-sea geography, so AIS×MT cross-views join on it directly.
    geo = get_area().classify_maritime_columns(lat, lon, territorial_sea_km())
    return (
        ts,
        ship_id.strip(),
        str(rec.get("source") or "marinetraffic"),
        str(rec.get("data_origin") or "terrestrial"),
        mmsi,
        lat, lon, rec.get("speed"), rec.get("course"),
        rec.get("heading"),
        rec.get("nav_status") if isinstance(rec.get("nav_status"), int) else None,
        rec.get("imo"), rec.get("name"), rec.get("callsign"),
        stc if isinstance(stc, int) else None,
        rec.get("ship_type"), rec.get("destination"), rec.get("draught"),
        rec.get("length_m"), rec.get("beam_m"), rec.get("flag"),
        geo.country, geo.near_country, geo.distance_km, GEO_SOURCE,
    )


def mt_xref_row(rec: dict[str, Any]) -> tuple[str, str, str | None, str | None] | None:
    """(ship_id, mmsi, name, flag) for the bridge — ONLY when a real mmsi is
    present (a genuine link). ship_id-only vessels are not bridged here; the
    bridge stays a link table, not a second full catalogue."""
    ship_id = rec.get("ship_id")
    mmsi = rec.get("mmsi")
    if not (isinstance(ship_id, str) and ship_id.strip()):
        return None
    if not (isinstance(mmsi, str) and mmsi.strip()):
        return None
    return (ship_id.strip(), mmsi.strip(), rec.get("name"), rec.get("flag"))


def parse_mt_lines(lines: Iterable[str]) -> tuple[list[tuple], list[tuple], int]:
    """Raw positions.jsonl lines → (mt_vessel rows, xref rows, invalid count).
    Uses the same translator as the live adapter, so backfill and live archival
    produce byte-identical rows."""
    from maritime.adapters.marinetraffic_translate import parse_mt_record, to_vessel_dict
    rows: list[tuple] = []
    xrefs: list[tuple] = []
    invalid = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        parsed = parse_mt_record(rec) if isinstance(rec, dict) else None
        if parsed is None:
            invalid += 1
            continue
        vessel = to_vessel_dict(parsed)
        row = mt_vessel_row(vessel)
        if row is None:
            invalid += 1
            continue
        rows.append(row)
        xref = mt_xref_row(vessel)
        if xref is not None:
            xrefs.append(xref)
    return rows, xrefs, invalid


class MtVesselIngester:
    """Live mirror for the MarineTraffic source (used by the collector) and the
    write path for backfill_mt. Same failure-tolerant contract as VesselIngester:
    the JSONL archive is ground truth; a DB outage never stops archiving."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._conn: asyncpg.Connection | None = None

    async def connect(self) -> None:
        self._conn = await asyncpg.connect(self.dsn)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> asyncpg.Connection:
        assert self._conn is not None, "call connect() first"
        return self._conn

    async def row_count(self) -> int:
        return await self.conn.fetchval("SELECT count(*) FROM mt_vessel_states")

    async def ingest(self, rows: list[tuple], snapshot: dict[str, Any] | None = None,
                     xrefs: list[tuple] | None = None) -> None:
        """Insert MT rows (+ optional bridge links + snapshot-metadata row) in ONE
        transaction. Idempotent via ON CONFLICT. The snapshot row REUSES
        vessel_snapshots with source='marinetraffic'."""
        if not rows and snapshot is None and not xrefs:
            return
        async with self.conn.transaction():
            if rows:
                await self.conn.executemany(MT_INSERT_SQL, rows)
            if xrefs:
                # dedupe by ship_id (last wins) so unnest gets one row per id
                latest: dict[str, tuple] = {x[0]: x for x in xrefs}
                cols = list(zip(*latest.values()))
                await self.conn.execute(
                    MT_XREF_UPSERT_SQL,
                    list(cols[0]), list(cols[1]), list(cols[2]), list(cols[3]),
                )
            if snapshot is not None:
                b = snapshot.get("bbox") or {}
                gen = _parse_ts(snapshot.get("generated_at"))
                await self.conn.execute(
                    UPSERT_SNAPSHOT_SQL,
                    snapshot["snapshot_time"], snapshot.get("source", "marinetraffic"),
                    snapshot.get("count", len(rows)),
                    b.get("lamin"), b.get("lomin"), b.get("lamax"), b.get("lomax"),
                    gen,
                )


# Identity bridge, second feed: mtscraper's `vessel_identities.jsonl`.
#
# WHY A SECOND SOURCE VALUE. 'marinetraffic-feed' means the TILE record carried
# an MMSI — measured across 2.94M archived rows, that has happened exactly zero
# times, because the tile feed omits MMSI by design. These links instead come
# from MarineTraffic's per-vessel detail endpoint, which mtscraper polls slowly
# under its own rate limit. Both are fed (never derived), so both must outrank
# 'ais-correlation'; but they are not the same evidence, and this table exists to
# keep provenance honest, so they get distinct values rather than one blurred one.
#
# link_source is overwritten on conflict: a detail-endpoint MMSI is authoritative
# and supersedes a correlation-derived guess. linked_at moves only when a real
# link is established for the first time; the WHERE-changed guard keeps a re-run
# a no-op so updated_at stays meaningful.
MT_IDENTITY_XREF_UPSERT_SQL = """
INSERT INTO vessel_xref (ship_id, mmsi, name, flag, link_source, linked_at, updated_at)
SELECT d.ship_id, d.mmsi, d.name, d.flag, 'marinetraffic-detail', now(), now()
FROM unnest($1::text[], $2::text[], $3::text[], $4::text[])
     AS d(ship_id, mmsi, name, flag)
ON CONFLICT (ship_id) DO UPDATE SET
    mmsi        = COALESCE(EXCLUDED.mmsi, vessel_xref.mmsi),
    name        = COALESCE(EXCLUDED.name, vessel_xref.name),
    flag        = COALESCE(EXCLUDED.flag, vessel_xref.flag),
    link_source = 'marinetraffic-detail',
    linked_at   = CASE WHEN vessel_xref.mmsi IS NULL AND EXCLUDED.mmsi IS NOT NULL
                       THEN now() ELSE vessel_xref.linked_at END,
    updated_at  = now()
WHERE vessel_xref.mmsi        IS DISTINCT FROM COALESCE(EXCLUDED.mmsi, vessel_xref.mmsi)
   OR vessel_xref.name        IS DISTINCT FROM COALESCE(EXCLUDED.name, vessel_xref.name)
   OR vessel_xref.flag        IS DISTINCT FROM COALESCE(EXCLUDED.flag, vessel_xref.flag)
   OR vessel_xref.link_source IS DISTINCT FROM 'marinetraffic-detail'
"""


def mt_identity_xref(rec: dict[str, Any]) -> tuple[str, str, str | None, str | None] | None:
    """One `vessel_identities.jsonl` line → an xref tuple, or None if unusable.

    A record without a ship_id or without an MMSI is not a link and is skipped —
    this table records links that were established, never placeholders. `flag`
    takes the ISO `country_code`, the same 2-letter space the tile feed's `flag`
    uses, rather than the spelled-out country, so both feeds stay comparable.
    """
    ship_id = rec.get("ship_id")
    mmsi = rec.get("mmsi")
    if not (isinstance(ship_id, str) and ship_id.strip()):
        return None
    if not (isinstance(mmsi, str) and mmsi.strip()):
        return None
    name = rec.get("name")
    flag = rec.get("country_code")
    return (
        ship_id.strip(),
        mmsi.strip(),
        name.strip() if isinstance(name, str) and name.strip() else None,
        flag.strip() if isinstance(flag, str) and flag.strip() else None,
    )


async def ingest_mt_identities(dsn: str, archive: Path, batch_size: int = 5000) -> int:
    """Load mtscraper's resolved identities into vessel_xref, idempotently.

    ISOLATION: this reads mtscraper's JSONL as a data contract, exactly as the
    position adapter does. scraplan imports none of mtscraper's Python.

    The identity file is small (one line per distinct resolved vessel, no
    time-series), so it is streamed in the same batched shape as backfill_mt for
    consistency rather than out of necessity.
    """
    if archive.is_dir():
        files = sorted(archive.glob("vessel_identities*.jsonl*"))
    else:
        files = [archive]
    files = [f for f in files if f.exists()]
    if not files:
        logger.error("no identity archive found at %s", archive)
        return 2

    conn = await asyncpg.connect(dsn)
    try:
        linked_sql = "SELECT count(*) FROM vessel_xref WHERE mmsi IS NOT NULL"
        before = await conn.fetchval(linked_sql)
        read = usable = skipped = 0
        batch: list[tuple] = []

        async def flush() -> None:
            if not batch:
                return
            latest = {x[0]: x for x in batch}      # one row per ship_id for unnest
            cols = list(zip(*latest.values()))
            await conn.execute(
                MT_IDENTITY_XREF_UPSERT_SQL,
                list(cols[0]), list(cols[1]), list(cols[2]), list(cols[3]),
            )
            batch.clear()

        for path in files:
            logger.info("ingesting %s", path.name)
            with _open(path) as f:
                for line in f:
                    read += 1
                    try:
                        rec = json.loads(line)
                    except (ValueError, TypeError):
                        skipped += 1
                        continue
                    row = mt_identity_xref(rec)
                    if row is None:
                        skipped += 1
                        continue
                    usable += 1
                    batch.append(row)
                    if len(batch) >= batch_size:
                        await flush()
                await flush()

        after = await conn.fetchval(linked_sql)
        total = await conn.fetchval("SELECT count(*) FROM vessel_xref")
        logger.info("=" * 56)
        logger.info("MARINETRAFFIC IDENTITY BRIDGE")
        logger.info("  files:              %d", len(files))
        logger.info("  lines read:         %d", read)
        logger.info("  usable links:       %d", usable)
        logger.info("  skipped (no mmsi):  %d", skipped)
        logger.info("  linked before:      %d", before)
        logger.info("  linked after:       %d", after)
        logger.info("  newly linked:       %d", after - before)
        logger.info("  vessel_xref total:  %d  (incl. rows with no mmsi yet)", total)
        logger.info("=" * 56)
        return 0
    finally:
        await conn.close()


async def backfill_mt(dsn: str, archive: Path, batch_size: int = 5000) -> int:
    """Rebuild mt_vessel_states (+ vessel_xref links) from mtscraper's JSONL,
    idempotently. Reads positions.jsonl (the time-series) plus any rotated *.gz.
    Mirrors backfill_vessels with a reconciliation report."""
    if archive.is_dir():
        files = sorted(archive.glob("positions*.jsonl*"))
    else:
        files = [archive]
    files = [f for f in files if f.exists()]
    if not files:
        logger.error("no MarineTraffic archive found at %s", archive)
        return 2

    ing = MtVesselIngester(dsn)
    await ing.connect()
    try:
        before = await ing.row_count()
        read = parsed = invalid = linked = 0
        for path in files:
            logger.info("ingesting %s", path.name)
            with _open(path) as f:
                batch: list[str] = []
                for line in f:
                    read += 1
                    batch.append(line)
                    if len(batch) >= batch_size:
                        r, x, inv = parse_mt_lines(batch)
                        parsed += len(r); invalid += inv; linked += len(x)
                        await ing.ingest(r, xrefs=x)
                        batch.clear()
                r, x, inv = parse_mt_lines(batch)
                parsed += len(r); invalid += inv; linked += len(x)
                await ing.ingest(r, xrefs=x)
        after = await ing.row_count()
        inserted = after - before
        logger.info("=" * 56)
        logger.info("MARINETRAFFIC RECONCILIATION")
        logger.info("  files:            %d", len(files))
        logger.info("  lines read:       %d", read)
        logger.info("  valid rows:       %d", parsed)
        logger.info("  invalid/skipped:  %d", invalid)
        logger.info("  mmsi links seen:  %d  (rows carrying a real MMSI)", linked)
        logger.info("  rows before:      %d", before)
        logger.info("  rows after:       %d", after)
        logger.info("  newly inserted:   %d", inserted)
        logger.info("  duplicates:       %d  (re-runs / repeated fixes — expected)",
                    parsed - inserted)
        logger.info("=" * 56)
        return 0
    finally:
        await ing.close()


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" \
        else path.open(encoding="utf-8")


async def backfill_vessels(dsn: str, archive: Path, batch_size: int = 5000) -> int:
    """Rebuild vessel_states from the raw ground-truth logs, idempotently.
    Reads the current ais-signals.jsonl (every new signal; static events lack
    a position and are skipped by vessel_row) plus legacy vessels.jsonl and
    any rotated *.gz. Mirrors ingest.py backfill with a reconciliation report."""
    if archive.is_dir():
        files = sorted(archive.glob("ais-signals*.jsonl*")) + sorted(archive.glob("vessels*.jsonl*"))
    else:
        files = [archive]
    files = [f for f in files if f.exists()]
    if not files:
        logger.error("no vessel archive found at %s", archive)
        return 2

    ing = VesselIngester(dsn)
    await ing.connect()
    try:
        before = await ing.row_count()
        read = parsed = invalid = 0
        for path in files:
            logger.info("ingesting %s", path.name)
            with _open(path) as f:
                batch: list[str] = []
                for line in f:
                    read += 1
                    batch.append(line)
                    if len(batch) >= batch_size:
                        r, inv = parse_lines(batch)
                        parsed += len(r); invalid += inv
                        await ing.ingest(r)
                        batch.clear()
                r, inv = parse_lines(batch)
                parsed += len(r); invalid += inv
                await ing.ingest(r)
        after = await ing.row_count()
        inserted = after - before
        logger.info("=" * 56)
        logger.info("VESSEL RECONCILIATION")
        logger.info("  files:            %d", len(files))
        logger.info("  lines read:       %d", read)
        logger.info("  valid rows:       %d", parsed)
        logger.info("  invalid/skipped:  %d", invalid)
        logger.info("  rows before:      %d", before)
        logger.info("  rows after:       %d", after)
        logger.info("  newly inserted:   %d", inserted)
        logger.info("  duplicates:       %d  (re-runs / repeated fixes — expected)",
                    parsed - inserted)
        logger.info("=" * 56)
        return 0
    finally:
        await ing.close()
