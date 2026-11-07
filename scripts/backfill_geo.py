#!/usr/bin/env python3
"""One-off historical geo-backfill (P3's deferred step).

Classifies EVERY positioned state_vectors row through scraplan's shared
classifier (backend.geo_area) and writes the geo columns, so history matches
what new ingested rows already carry. Target set:

    geo_source IS DISTINCT FROM 'scraplan-aoi' AND latitude/longitude NOT NULL

That predicate is the whole idempotency/resume story: every UPDATE sets
geo_source='scraplan-aoi', so an already-classified row leaves the target set
and a re-run (after an interrupt or power loss) redoes ZERO done rows and
continues from where it stopped — no external cursor.

Batched per TimescaleDB chunk (oldest first). A chunk that is compressed is
decompressed before its UPDATEs and recompressed after, so the compressed-chunk
DML cost is paid one chunk at a time and the DB's compression posture is
preserved (chunks that were uncompressed stay uncompressed for the policy).
Each batch is one bounded transaction; progress is logged per batch.

Only the geo columns change. The natural key (icao24, ts, source), snapshot_time
and the ts=to_timestamp(snapshot_time) CHECK are never touched.

Usage:
    python scripts/backfill_geo.py --dry-run     # report plan, change nothing
    python scripts/backfill_geo.py [--batch-size 10000]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.geo_area import GEO_SOURCE, get_area  # noqa: E402

logger = logging.getLogger("backfill_geo")

# The predicate that both selects work and guarantees idempotency/resume.
TARGET = (
    "latitude IS NOT NULL AND longitude IS NOT NULL "
    "AND geo_source IS DISTINCT FROM 'scraplan-aoi'"
)

SELECT_BATCH = f"""
SELECT icao24, extract(epoch FROM ts)::bigint AS ts_epoch, source,
       latitude, longitude, position_quality
FROM state_vectors
WHERE ts >= $1 AND ts < $2 AND {TARGET}
LIMIT $3
"""

# One statement per batch: zip the classified arrays with unnest and join on the
# natural key. Fast (single UPDATE) and safe (no key/ts columns touched).
#
# CRITICAL — chunk scoping: the {lo}/{hi} literal ts bounds are what keep this
# UPDATE inside ONE chunk. Without a *constant* time predicate the planner cannot
# do TimescaleDB chunk exclusion (the join's ts values are only known at runtime),
# so the DML plan spans the WHOLE hypertable — and scanning a compressed chunk in
# a DML forces it to decompress, blowing past
# timescaledb.max_tuples_decompressed_per_dml_transaction. With the literal bounds
# the plan targets only the current (already-decompressed) chunk, so no other
# compressed chunk is ever touched. The bounds come from
# timescaledb_information.chunks (trusted), inlined as literals so exclusion runs
# at plan time.
UPDATE_BATCH_TMPL = """
UPDATE state_vectors AS sv SET
    country          = d.country,
    near_country     = d.near_country,
    position_quality = d.position_quality,
    distance_km      = d.distance_km,
    geo_source       = $8
FROM unnest($1::text[], $2::bigint[], $3::text[], $4::text[], $5::text[],
            $6::text[], $7::float8[])
     AS d(icao24, ts_epoch, source, country, near_country, position_quality, distance_km)
WHERE sv.icao24 = d.icao24
  AND sv.ts = to_timestamp(d.ts_epoch)
  AND sv.source = d.source
  AND sv.ts >= '{lo}'::timestamptz AND sv.ts < '{hi}'::timestamptz
"""

COUNT_TARGET = f"SELECT count(*) FROM state_vectors WHERE {TARGET}"
PENDING_IN_CHUNK = f"SELECT count(*) FROM state_vectors WHERE ts >= $1 AND ts < $2 AND {TARGET}"


async def _remaining(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(COUNT_TARGET)


async def _chunks(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT chunk_schema, chunk_name, range_start, range_end, is_compressed "
        "FROM timescaledb_information.chunks "
        "WHERE hypertable_name = 'state_vectors' ORDER BY range_start"
    )


def _classify_batch(rows: list[asyncpg.Record]):
    """rows -> the seven parallel arrays UPDATE_BATCH consumes."""
    area = get_area()
    icaos, epochs, sources = [], [], []
    countries, nears, pqs, dists = [], [], [], []
    for r in rows:
        geo = area.classify_columns(r["latitude"], r["longitude"])
        icaos.append(r["icao24"])
        epochs.append(r["ts_epoch"])
        sources.append(r["source"])
        countries.append(geo.country)
        nears.append(geo.near_country)
        dists.append(geo.distance_km)
        # scraplan owns confirmed-vs-no_position (all rows here have a fix, so
        # never no_position); an incoming 'suspect' verdict is preserved.
        pqs.append("suspect" if (r["position_quality"] == "suspect") else "confirmed")
    return icaos, epochs, sources, countries, nears, pqs, dists


async def _is_compressed(conn: asyncpg.Connection, schema: str, name: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT is_compressed FROM timescaledb_information.chunks "
        "WHERE chunk_schema = $1 AND chunk_name = $2", schema, name,
    ))


async def run(dsn: str, batch_size: int, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        total = await _remaining(conn)
        chunks = await _chunks(conn)
        est_batches = (total + batch_size - 1) // batch_size
        logger.info("=" * 64)
        logger.info("PLAN")
        logger.info("  rows to classify (positioned, not yet scraplan-aoi): %d", total)
        logger.info("  chunks: %d (%d compressed)", len(chunks),
                    sum(1 for c in chunks if c["is_compressed"]))
        logger.info("  batch size: %d", batch_size)
        logger.info("  estimated batches: %d", est_batches)
        logger.info("=" * 64)
        if dry_run:
            logger.info("--dry-run: nothing changed.")
            return 0
        if total == 0:
            logger.info("Nothing to do — already fully classified.")
            return 0

        # Warm (and fail fast on) the classifier before mutating anything.
        get_area()

        done = 0
        t0 = time.monotonic()
        for ci, ch in enumerate(chunks, 1):
            schema, name = ch["chunk_schema"], ch["chunk_name"]
            start, end = ch["range_start"], ch["range_end"]
            # Cheap pre-check (a read — never hits the DML decompression limit).
            # Skipping fully-classified chunks means an already-done COMPRESSED
            # chunk is never decompressed just to find nothing to update.
            pending = await conn.fetchval(PENDING_IN_CHUNK, start, end)
            if pending == 0:
                logger.info("chunk %d/%d %s: nothing pending — skip", ci, len(chunks), name)
                continue
            was_compressed = await _is_compressed(conn, schema, name)
            if was_compressed:
                # Bulk-decompress to heap FIRST (the supported bulk op), so the
                # scoped UPDATEs below are ordinary heap writes — not per-DML
                # implicit decompression.
                logger.info("chunk %d/%d %s: decompressing (%d pending)…",
                            ci, len(chunks), name, pending)
                await conn.execute(
                    "SELECT decompress_chunk(format('%I.%I', $1::text, $2::text)::regclass)",
                    schema, name,
                )
            # Scope the UPDATE to THIS chunk's range (literal bounds) so the plan
            # excludes every other chunk — see UPDATE_BATCH_TMPL.
            update_sql = UPDATE_BATCH_TMPL.format(lo=start.isoformat(), hi=end.isoformat())
            chunk_done = 0
            while True:
                rows = await conn.fetch(SELECT_BATCH, start, end, batch_size)
                if not rows:
                    break
                arrays = _classify_batch(rows)
                async with conn.transaction():
                    await conn.execute(update_sql, *arrays, GEO_SOURCE)
                done += len(rows)
                chunk_done += len(rows)
                rate = done / max(time.monotonic() - t0, 1e-6)
                logger.info(
                    "  chunk %d/%d %s | +%d | chunk=%d | total=%d/%d (%.1f%%) | "
                    "remaining=%d | %.0f rows/s",
                    ci, len(chunks), name, len(rows), chunk_done, done, total,
                    100.0 * done / total, total - done, rate,
                )
            if was_compressed:
                # Restore compression state (a chunk we decompressed is recompressed;
                # chunks that were already uncompressed are left for the policy).
                logger.info("chunk %d/%d %s: recompressing…", ci, len(chunks), name)
                await conn.execute(
                    "SELECT compress_chunk(format('%I.%I', $1::text, $2::text)::regclass)",
                    schema, name,
                )

        elapsed = time.monotonic() - t0
        remaining = await _remaining(conn)
        logger.info("=" * 64)
        logger.info("DONE: classified %d rows in %.1fs (%.0f rows/s); remaining target rows: %d",
                    done, elapsed, done / max(elapsed, 1e-6), remaining)
        logger.info("=" * 64)
        return 0 if remaining == 0 else 1
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="Historical geo-backfill for state_vectors")
    p.add_argument("--batch-size", type=int, default=10000)
    p.add_argument("--dry-run", action="store_true", help="report the plan, change nothing")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.batch_size, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
