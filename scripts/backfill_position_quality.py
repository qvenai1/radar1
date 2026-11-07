#!/usr/bin/env python3
"""Maritime layer G2 — per-position GNSS quality backfill + continuous runner.

ONE script, two duties (the detect_ais_gaps.py pattern): the one-off historical
backfill of every vessel_states row, and the ongoing incremental currency run a
systemd timer fires. No detection logic lives here — backend.gnss_anomaly (G1) is
the only source of verdicts; this script owns all I/O and the recompute-and-
replace discipline.

WHAT IT DOES per (mmsi, source), fixes ordered by ts:
  * fetches the track (+ vessel_identity.ship_type for the fast-craft allowance),
  * computes inland depth with get_area().inland_depth_km(...) (union metric),
  * passes PLAIN data into the pure module's classify_fix(prev, cur, nxt, ...),
  * writes position_quality / anomaly_flags / implied_speed_kn / inland_km, stamps
    quality_method from the module and quality_computed_at = now() at write time.

OVERLAP CONTRACT (why re-processing is correct, not double-counting): a fix's
verdict needs its neighbours. Each per-MMSI fetch re-includes the ONE fix before
the window (the overlap prev) so the earliest in-window fix gets its incoming
segment; that overlap fix is used but never re-written. The OUTGOING segment of
the last fix in a window is unknown now, so teleport_return for a window-edge fix
stays unresolved until the next run, when its successor has been ingested and the
edge fix re-enters the (overlapping) window and is recomputed — recompute-and-
replace on the natural key, exactly like every other detector. The last fix of a
vessel's whole history likewise has no successor until one is ingested.

IDEMPOTENT BY CONSTRUCTION: same data => identical verdicts (the module is
deterministic). Writes go through a WHERE-CHANGED guard (IS DISTINCT FROM on every
quality column EXCEPT the timestamp — the M2b churn-guard precedent), so a no-op
re-run over an already-stamped window changes 0 rows and does not even touch
quality_computed_at.

Modes:
    --dry-run                 compute + print the summary, write nothing
    --since <ISO/epoch>       incremental: (re)process fixes at/after this instant
    --since-minutes <N>       incremental: since = now() - N minutes (timer duty)
    (neither)                 full history — the one-off backfill

Compression (one-off backfill only, --decompress-chunks): an UPDATE touching
effectively every row of a COMPRESSED chunk is the pathological case for DML-on-
compressed (per-row decompression-buffer overhead + bloat), so the honest one-off
operation bulk-decompresses each compressed chunk, runs the backfill, then
recompresses and verifies. The 7-day compression POLICY is never touched.

Usage:
    python scripts/backfill_position_quality.py --dry-run
    python scripts/backfill_position_quality.py --decompress-chunks   # one-off backfill
    python scripts/backfill_position_quality.py --since-minutes 45     # timer duty
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.geo_area import get_area  # noqa: E402
from backend.gnss_anomaly import (  # noqa: E402
    FLAG_ON_LAND,
    IMPLAUSIBLE,
    METHOD,
    PLAUSIBLE,
    SUSPECT,
    Fix,
    classify_fix,
)

logger = logging.getLogger("backfill_position_quality")

#: Parameterised — NOT hardcoded to the dev box. Rows per write transaction.
DEFAULT_BATCH_SIZE = 10_000

# ⛔ MT-DETECT-2 — THE SAME CLASSIFIER, POINTED AT EITHER TRACK. `classify_fix` reads only
# prev/cur/nxt timestamps and positions plus an optional ship_type, so the tagger's logic is
# source-agnostic; only the TABLE, its KEY COLUMN and where ship_type lives differ. Those
# three differences live here, once, instead of in a forked copy of the script.
#
# ⚠ `key` is the natural per-track identifier — MMSI for AIS, ship_id for MarineTraffic —
# and is aliased to `mmsi` throughout the queries so the pure computation below never learns
# which identifier space it is in.
#
# ⚠ NOT to be confused with the existing `source` COLUMN on vessel_states, which records the
# AIS feed a fix arrived on. mt_vessel_states carries its own `source` column too
# ('marinetraffic'), so the (key, source) pairing works unchanged for both.
TRACKS: dict[str, dict[str, str]] = {
    "aisstream": {
        "table": "vessel_states", "key": "mmsi",
        "ship_type": "vi.ship_type",
        "join": "LEFT JOIN vessel_identity vi ON vi.mmsi = v.mmsi",
    },
    "marinetraffic": {
        "table": "mt_vessel_states", "key": "ship_id",
        # MT carries the static type on the position row itself — no identity table to join,
        # and none exists for ship_id.
        "ship_type": "v.ship_type",
        "join": "",
    },
}

# (key, source) tracks to process. Full history when $1 is NULL; else only tracks
# carrying a fix in the incremental window (their overlap prev is fetched below).
PAIRS_SQL = """
SELECT DISTINCT {key} AS mmsi, source
FROM {table}
WHERE latitude IS NOT NULL AND longitude IS NOT NULL
  AND ($1::timestamptz IS NULL OR ts >= $1)
ORDER BY mmsi, source
"""

# One track ordered by ts. Full history: every fix (all in_window). Incremental:
# fixes at/after $3 (in_window) PLUS the single latest fix before $3 (the overlap
# prev, in_window = false — used as prev, never written).
FIXES_SQL = """
SELECT v.ts, v.latitude AS lat, v.longitude AS lon, {ship_type} AS ship_type,
       ($3::timestamptz IS NULL OR v.ts >= $3) AS in_window
FROM {table} v
{join}
WHERE v.{key} = $1 AND v.source = $2
  AND v.latitude IS NOT NULL AND v.longitude IS NOT NULL
  AND ($3::timestamptz IS NULL
       OR v.ts >= $3
       OR v.ts = (SELECT max(w.ts) FROM {table} w
                  WHERE w.{key} = $1 AND w.source = $2 AND w.ts < $3
                    AND w.latitude IS NOT NULL AND w.longitude IS NOT NULL))
ORDER BY v.ts
"""

CREATE_TEMP = """
CREATE TEMP TABLE _q (
    mmsi TEXT, ts TIMESTAMPTZ, source TEXT,
    position_quality TEXT, anomaly_flags TEXT[],
    implied_speed_kn DOUBLE PRECISION, inland_km DOUBLE PRECISION,
    quality_method TEXT
) ON COMMIT PRESERVE ROWS
"""

# WHERE-CHANGED guard: update (and re-stamp) only rows whose verdict/evidence
# actually differs — quality_computed_at is deliberately NOT in the comparison, so
# a no-op re-run touches nothing.
#
# {floor} is a LITERAL ``AND v.ts >= '...'`` bound injected for incremental runs.
# It is essential, not cosmetic: the join predicate ``v.ts = q.ts`` is NOT
# chunk-excludable (q.ts is a column, unknown at plan time), so without a constant
# ts bound the planner scans EVERY chunk — and DML over a compressed chunk
# decompresses its tuples, hitting timescaledb.max_tuples_decompressed_per_dml_
# transaction. The literal bound lets TSDB prune the old compressed chunks, so an
# incremental (timer) run only ever touches recent uncompressed chunks. The full
# backfill uses no floor but decompresses its chunks up front instead.
UPDATE_SQL = """
UPDATE {table} v SET
    position_quality    = q.position_quality,
    anomaly_flags       = q.anomaly_flags,
    implied_speed_kn    = q.implied_speed_kn,
    inland_km           = q.inland_km,
    quality_method      = q.quality_method,
    quality_computed_at = now()
FROM _q q
WHERE v.{key} = q.mmsi AND v.ts = q.ts AND v.source = q.source
{floor}  AND (v.position_quality IS DISTINCT FROM q.position_quality
    OR v.anomaly_flags    IS DISTINCT FROM q.anomaly_flags
    OR v.implied_speed_kn IS DISTINCT FROM q.implied_speed_kn
    OR v.inland_km        IS DISTINCT FROM q.inland_km
    OR v.quality_method   IS DISTINCT FROM q.quality_method)
"""

_COPY_COLS = ("mmsi", "ts", "source", "position_quality", "anomaly_flags",
              "implied_speed_kn", "inland_km", "quality_method")


def _track_rows(mmsi, source, fixes, area, stats):
    """Compute write-rows for one track. Overlap-prev fixes are used as prev only,
    never emitted. Tallies stats in place (for the dry-run/real summary)."""
    fx = [Fix(r["ts"], r["lat"], r["lon"]) for r in fixes]
    n = len(fx)
    rows = []
    for i in range(n):
        if not fixes[i]["in_window"]:
            continue
        prev = fx[i - 1] if i > 0 else None
        nxt = fx[i + 1] if i < n - 1 else None
        depth = area.inland_depth_km(fx[i].lat, fx[i].lon)
        pq = classify_fix(prev, fx[i], nxt, ship_type=fixes[i]["ship_type"], inland_km=depth)

        stats["verdict"][pq.position_quality] += 1
        for f in pq.anomaly_flags:
            stats["flag"][f] += 1
        if pq.position_quality != PLAUSIBLE:
            stats["dirty_mmsi"].add(mmsi)
        if FLAG_ON_LAND in pq.anomaly_flags:
            stats["on_land_country"][area.classify(fx[i].lat, fx[i].lon).country] += 1

        rows.append((mmsi, fixes[i]["ts"], source, pq.position_quality,
                     list(pq.anomaly_flags) or None, pq.implied_speed_kn, depth, METHOD))
    return rows


async def _flush(conn, buf, update_sql):
    async with conn.transaction():
        await conn.execute("TRUNCATE _q")
        await conn.copy_records_to_table("_q", records=buf, columns=list(_COPY_COLS))
        status = await conn.execute(update_sql)
    return int(status.split()[-1])   # "UPDATE <n>"


async def _decompress(conn):
    rows = await conn.fetch(
        "SELECT format('%I.%I', chunk_schema, chunk_name) AS c FROM timescaledb_information.chunks "
        "WHERE hypertable_name = 'vessel_states' AND is_compressed ORDER BY range_start")
    names = [r["c"] for r in rows]
    for c in names:
        t0 = time.monotonic()
        await conn.execute("SELECT decompress_chunk($1::regclass)", c)
        logger.info("decompressed %s in %.1fs", c, time.monotonic() - t0)
    return names


async def _recompress(conn, names):
    for c in names:
        t0 = time.monotonic()
        await conn.execute("SELECT compress_chunk($1::regclass)", c)
        logger.info("recompressed %s in %.1fs", c, time.monotonic() - t0)
    still = await conn.fetchval(
        "SELECT count(*) FROM timescaledb_information.chunks "
        "WHERE hypertable_name='vessel_states' AND is_compressed")
    logger.info("compressed chunks after recompress: %d (expected %d)", still, len(names))


async def run(dsn, since, batch_size, dry_run, decompress_chunks, track="aisstream"):
    # ⛔ VALIDATED BEFORE IT IS FORMATTED INTO SQL. The table and key names are interpolated
    # (they cannot be bound), so this membership test is what stands between an argument and
    # the query text.
    if track not in TRACKS:
        logger.error("unknown track %r — expected one of %s", track, list(TRACKS))
        return 2
    T = TRACKS[track]
    area = get_area()
    conn = await asyncpg.connect(dsn)
    decompressed = []
    try:
        # A full-history write must handle compressed chunks explicitly: a plain
        # UPDATE over them hits timescaledb.max_tuples_decompressed_per_dml_transaction.
        # Fail LOUDLY with guidance rather than mid-run with a cryptic limit error.
        if not dry_run and since is None and not decompress_chunks:
            n_comp = await conn.fetchval(
                "SELECT count(*) FROM timescaledb_information.chunks "
                "WHERE hypertable_name='vessel_states' AND is_compressed")
            if n_comp:
                logger.error(
                    "full-history write with %d compressed chunk(s): pass --decompress-chunks "
                    "(bulk decompress -> update -> recompress), or bound the window with --since. "
                    "A plain UPDATE over compressed chunks exceeds "
                    "timescaledb.max_tuples_decompressed_per_dml_transaction.", n_comp)
                return 2

        if decompress_chunks and not dry_run:
            if since is not None:
                logger.warning("--decompress-chunks ignored with an incremental --since window")
            else:
                decompressed = await _decompress(conn)

        # Literal ts floor for incremental runs so TSDB prunes old (compressed)
        # chunks from the UPDATE; full backfill uses no floor (chunks decompressed).
        floor = f"  AND v.ts >= '{since.isoformat()}'::timestamptz\n" if since is not None else ""
        update_sql = UPDATE_SQL.format(floor=floor, table=T['table'], key=T['key'])

        if not dry_run:
            await conn.execute(CREATE_TEMP)

        pairs = await conn.fetch(PAIRS_SQL.format(**T), since)
        logger.info("tracks to process: %d %s", len(pairs),
                    f"(since {since.isoformat()})" if since else "(full history)")

        stats = {"verdict": Counter(), "flag": Counter(),
                 "on_land_country": Counter(), "dirty_mmsi": set()}
        buf, changed, processed, t0 = [], 0, 0, time.monotonic()

        for k, rec in enumerate(pairs, 1):
            fixes = await conn.fetch(FIXES_SQL.format(**T), rec["mmsi"], rec["source"], since)
            rows = _track_rows(rec["mmsi"], rec["source"], fixes, area, stats)
            processed += len(rows)
            if not dry_run:
                buf.extend(rows)
                if len(buf) >= batch_size:
                    changed += await _flush(conn, buf, update_sql)
                    buf.clear()
            if k % 500 == 0:
                logger.info("  %d/%d tracks | %d fixes | %.0fs", k, len(pairs), processed,
                            time.monotonic() - t0)
        if buf and not dry_run:
            changed += await _flush(conn, buf, update_sql)

        _summary(stats, processed, changed, dry_run, time.monotonic() - t0)

        if not dry_run and since is None:
            null_q = await conn.fetchval(
                "SELECT count(*) FROM vessel_states WHERE quality_method IS NULL")
            null_pos = await conn.fetchval(
                "SELECT count(*) FROM vessel_states WHERE latitude IS NULL OR longitude IS NULL")
            logger.info("post-backfill: quality_method IS NULL = %d (must equal NULL-lat/lon = %d) -> %s",
                        null_q, null_pos, "OK" if null_q == null_pos else "MISMATCH")
        return 0
    finally:
        if decompressed:
            await _recompress(conn, decompressed)
        await conn.close()


def _summary(stats, processed, changed, dry_run, secs):
    logger.info("=" * 64)
    logger.info("%s SUMMARY — %d fixes processed in %.0fs", "DRY-RUN" if dry_run else "WRITE", processed, secs)
    for v in (PLAUSIBLE, SUSPECT, IMPLAUSIBLE):
        logger.info("  verdict %-12s %d", v, stats["verdict"].get(v, 0))
    for f, n in sorted(stats["flag"].items(), key=lambda x: -x[1]):
        logger.info("  flag    %-16s %d", f, n)
    logger.info("  distinct vessels with any anomaly: %d", len(stats["dirty_mmsi"]))
    if stats["on_land_country"]:
        logger.info("  on_land by country: %s",
                    ", ".join(f"{c}={n}" for c, n in stats["on_land_country"].most_common()))
    if not dry_run:
        logger.info("  rows CHANGED (write-guard): %d", changed)
    logger.info("=" * 64)


def _parse_since(since, since_minutes):
    if since_minutes is not None:
        return datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    if not since:
        return None
    try:
        return datetime.fromtimestamp(int(since), tz=timezone.utc)
    except ValueError:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="G2 per-position GNSS quality backfill + runner")
    p.add_argument("--dry-run", action="store_true", help="compute + summarise, write nothing")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--since", type=str, default=None, help="incremental: fixes at/after this ISO-8601/epoch")
    g.add_argument("--since-minutes", type=int, default=None, help="incremental: since = now() - N minutes")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                   help=f"rows per write transaction (default {DEFAULT_BATCH_SIZE})")
    p.add_argument("--track", default="aisstream", choices=list(TRACKS),
                   help="which track to tag (default aisstream). MT-DETECT-2: the same "
                        "classifier, pointed at mt_vessel_states. ⚠ Verdicts are more "
                        "CONSERVATIVE on marinetraffic — its ~2.2 min median cadence makes "
                        "impossible_speed and teleport_return under-detect, while on_land "
                        "is unaffected. See migration 045.")
    p.add_argument("--decompress-chunks", action="store_true",
                   help="one-off backfill: decompress compressed chunks, update, recompress")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    since = _parse_since(a.since, a.since_minutes)
    return asyncio.run(run(dsn, since, a.batch_size, a.dry_run, a.decompress_chunks,
                           a.track))


if __name__ == "__main__":
    sys.exit(main())
