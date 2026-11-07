#!/usr/bin/env python3
"""
Atheer RADAR — JSONL → TimescaleDB ingestion
==============================================
One idempotent ingestion path, two modes:

    backfill   Read the whole states.jsonl archive into the DB. Safe to
               re-run at any time: the DB's natural-key constraint
               (icao24, ts) makes every insert ON CONFLICT DO NOTHING.
               Ends with a reconciliation report and a full refresh of the
               1-minute continuous aggregate.

    follow     The LIVE ingestion path (Phase 2 decision: a separate
               DB-writer process, NOT a collector patch). Tails states.jsonl
               by byte offset, ingesting new complete lines as they appear,
               and NOTIFYs 'new_snapshot' so the API server can push to
               WebSocket clients.

Why a separate writer instead of extending tracker_opensky.py
--------------------------------------------------------------
* The collector stays 100% DB-agnostic — zero risk to the ground-truth
  JSONL/latest.json archiving, which is the resilience requirement stated
  in its strongest possible form.
* One ingestion implementation: backfill and live share this exact code
  path, so they cannot drift apart.
* Gap recovery is free: the tailer's offset only advances after a commit,
  so a DB outage simply pauses the tail; when the DB returns, ingestion
  resumes from the archive at the exact point it stopped. No data loss,
  no special backfill choreography.

Offset state lives in <archive-dir>/.ingest_state.json (inode + offset).
If the archive is replaced/truncated the tailer detects it and rescans from
zero — harmless, because ingestion is idempotent.

bbox note: raw JSONL rows do not carry the collector's bounding box; it is
read from latest.json when the tailed snapshot_time matches (and on backfill,
for whichever snapshot latest.json currently describes). Other historical
snapshots keep a NULL bbox — a documented property of the data, not a bug.

Usage
    python ingest.py backfill [--archive opensky_output/states.jsonl]
    python ingest.py follow   [--archive ...] [--poll 2.0]
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import signal
import sys
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

from backend.geo_area import GEO_SOURCE, get_area

logger = logging.getLogger("opensky_ingest")

NOTIFY_CHANNEL = "new_snapshot"

# Each feed archives to <prefix>*.jsonl[.gz]; a directory backfill picks the
# right files per source. opensky/airplanes_live are scraplan's own collectors;
# adsbexchange is an EXTERNAL producer (adsb_scraper) writing state_vectors.jsonl
# already in StateVector shape — scraplan only consumes it here.
ARCHIVE_PREFIX = {
    "opensky": "states",
    "airplanes_live": "military",
    "adsbexchange": "state_vectors",
}
SOURCES = tuple(ARCHIVE_PREFIX)

INSERT_SQL = """
-- $20 = source, $21..$23 = military extras (NULL for OpenSky). The natural
-- key is (icao24, ts, source) since migration 006, so two feeds reporting
-- the same aircraft in the same second never collide.
-- $24..$29 = geo-classification/validation provenance (migration 012, ADDITIVE);
-- every one is NULL for sources that do not classify (opensky, airplanes_live),
-- so this INSERT is unchanged in behaviour for them.
INSERT INTO state_vectors (
    ts, snapshot_time, icao24, callsign, origin_country, time_position,
    last_contact, longitude, latitude, baro_altitude, geo_altitude,
    on_ground, velocity, true_track, vertical_rate, squawk, spi,
    position_source, category, fetched_at, source, actype, acdesc, reg,
    country, near_country, position_quality, validation_flags, distance_km, geo_source
) VALUES (
    to_timestamp($1::bigint), $1, $2, $3, $4, $5,
    $6, $7, $8, $9, $10,
    $11, $12, $13, $14, $15, $16,
    $17, $18, $19, $20, $21, $22, $23,
    $24, $25, $26, $27, $28, $29
)
ON CONFLICT (icao24, ts, source) DO NOTHING
"""

# count is always recomputed from the rows actually stored — never trusted
# from the writer — so re-runs and partial batches converge to the truth.
UPSERT_SNAPSHOT_SQL = """
INSERT INTO snapshots (snapshot_time, source, ts, count, lamin, lomin, lamax, lomax, generated_at)
VALUES (
    $1, $7, to_timestamp($1::bigint),
    (SELECT count(*) FROM state_vectors
      WHERE ts = to_timestamp($1::bigint) AND source = $7),
    $2, $3, $4, $5, $6
)
ON CONFLICT (snapshot_time, source) DO UPDATE SET
    count        = EXCLUDED.count,
    lamin        = COALESCE(snapshots.lamin, EXCLUDED.lamin),
    lomin        = COALESCE(snapshots.lomin, EXCLUDED.lomin),
    lamax        = COALESCE(snapshots.lamax, EXCLUDED.lamax),
    lomax        = COALESCE(snapshots.lomax, EXCLUDED.lomax),
    generated_at = COALESCE(snapshots.generated_at, EXCLUDED.generated_at)
"""

# Completeness (see migrations/003): a snapshot older than the newest is
# complete by write-ordering; the newest is complete when its stored row
# count matches the count the collector published in latest.json ($1=$2 are
# latest.json's snapshot_time/count, or -1 when unknown). Runs in the same
# transaction as the inserts — no window where a closed snapshot is unmarked.
MARK_COMPLETE_SQL = """
UPDATE snapshots SET complete = true
WHERE NOT complete AND source = $3
  AND (snapshot_time < (SELECT max(snapshot_time) FROM snapshots WHERE source = $3)
       OR (snapshot_time = $1 AND count = $2))
"""


# ------------------------------------------------------------------------------
# Record parsing — nulls stay nulls, wrong types invalidate the record
# ------------------------------------------------------------------------------

def _num(val: Any) -> float | None:
    """Accept numbers, keep nulls, REJECT anything else (no silent coercion)."""
    if val is None or isinstance(val, (int, float)):
        return val
    raise ValueError(f"expected number or null, got {type(val).__name__}")


def _intn(val: Any) -> int | None:
    if val is None:
        return None
    if isinstance(val, int):
        return val
    raise ValueError(f"expected int or null, got {type(val).__name__}")


def _strn(val: Any) -> str | None:
    """Optional text: keep nulls as NULL (do NOT coerce to ''), stringify the rest."""
    if val is None:
        return None
    return str(val)


def _strlist(val: Any) -> list[str] | None:
    """Optional TEXT[]: None → NULL; a list/tuple → list[str]; anything else invalid."""
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        return [str(x) for x in val]
    raise ValueError(f"expected array or null, got {type(val).__name__}")


def parse_record(rec: dict[str, Any], default_source: str = "opensky") -> tuple | None:
    """JSONL dict → INSERT_SQL parameter tuple, or None if structurally invalid."""
    icao24 = rec.get("icao24")
    snapshot_time = rec.get("snapshot_time")
    if not isinstance(icao24, str) or len(icao24.strip()) < 4:
        return None
    if not isinstance(snapshot_time, int) or snapshot_time <= 0:
        return None
    try:
        fetched_at = None
        if rec.get("fetched_at"):
            fetched_at = datetime.fromisoformat(rec["fetched_at"])
        lon = _num(rec.get("longitude"))
        lat = _num(rec.get("latitude"))
        # P3: scraplan-owned geo classification, applied to EVERY source from
        # lat/lon ONLY (degrees; metric altitude/velocity never enter it). The
        # ONE shared classifier (backend/geo_area.py) re-classifies all feeds
        # from a single asset, so country/near/distance never disagree between
        # sources — including adsbexchange rows, which are re-classified here
        # rather than trusting the incoming values. A missing asset fails fast
        # (GeoAssetError is not ValueError/TypeError, so it propagates).
        country = near_country = distance_km = None
        if lat is not None and lon is not None:
            geo = get_area().classify_columns(lat, lon)
            country, near_country, distance_km = geo.country, geo.near_country, geo.distance_km
            # scraplan owns the confirmed-vs-no_position axis; an incoming
            # 'suspect' is a kinematic verdict the producing feed computed in
            # its native units — preserved here, never silently upgraded.
            position_quality = (
                "suspect" if str(rec.get("position_quality") or "").lower() == "suspect"
                else "confirmed"
            )
        else:
            position_quality = "no_position"
        return (
            snapshot_time,
            icao24.strip().lower(),
            str(rec.get("callsign") or ""),
            str(rec.get("origin_country") or ""),
            _intn(rec.get("time_position")),
            _intn(rec.get("last_contact")),
            lon,
            lat,
            _num(rec.get("baro_altitude")),
            _num(rec.get("geo_altitude")),
            bool(rec.get("on_ground")),
            _num(rec.get("velocity")),
            _num(rec.get("true_track")),
            _num(rec.get("vertical_rate")),
            str(rec.get("squawk") or ""),
            bool(rec.get("spi")),
            _intn(rec.get("position_source")),
            _intn(rec.get("category")),
            fetched_at,
            # self-describing: military JSONL carries source + extras;
            # OpenSky records lack them and fall back to opensky/NULL.
            str(rec.get("source") or default_source),
            rec.get("actype"),
            rec.get("acdesc"),
            rec.get("reg"),
            # Geo classification (migration 012 columns) — computed by scraplan's
            # shared classifier for THIS position, regardless of feed. geo_source
            # records the classifier identity (not the feed). validation_flags is
            # preserved as the source supplied it (scraplan does not recompute the
            # kinematic flags, which are unit-sensitive).
            country,
            near_country,
            position_quality,
            _strlist(rec.get("validation_flags")),
            distance_km,
            GEO_SOURCE,
        )
    except (ValueError, TypeError) as exc:
        logger.debug("Invalid record (%s): %r", exc, rec)
        return None


def parse_lines(lines: list[str], default_source: str = "opensky") -> tuple[list[tuple], int]:
    """Parse JSONL lines; returns (rows, invalid_count)."""
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
        row = parse_record(rec, default_source) if isinstance(rec, dict) else None
        if row is None:
            invalid += 1
        else:
            rows.append(row)
    return rows, invalid


def read_bbox_from_latest(latest_path: Path) -> dict[str, Any] | None:
    """{snapshot_time, lamin.., generated_at} from latest.json, or None."""
    try:
        with latest_path.open(encoding="utf-8") as f:
            data = json.load(f)
        bbox = data.get("bbox") or {}
        generated_at = None
        if data.get("generated_at"):
            generated_at = datetime.fromisoformat(data["generated_at"])
        return {
            "snapshot_time": data.get("snapshot_time"),
            "count": data.get("count"),
            "lamin": bbox.get("lamin"), "lomin": bbox.get("lomin"),
            "lamax": bbox.get("lamax"), "lomax": bbox.get("lomax"),
            "generated_at": generated_at,
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------------------
# DB ingestion core — shared by both modes
# ------------------------------------------------------------------------------

class Ingester:
    def __init__(self, dsn: str, latest_path: Path) -> None:
        self.dsn = dsn
        self.latest_path = latest_path
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
        return await self.conn.fetchval("SELECT count(*) FROM state_vectors")

    async def ingest_rows(self, rows: list[tuple], notify: bool) -> list[int]:
        """
        Insert a batch of state-vector rows + upsert their snapshots rows,
        all in ONE transaction. Returns the distinct snapshot_times touched.
        Source-aware: each row carries its source at index 19, so a batch is
        grouped into (snapshot_time, source) snapshot rows. The transaction
        boundary is what makes the tailer's offset persistence safe.
        """
        if not rows:
            return []
        # (snapshot_time, source) pairs present in this batch
        pairs = sorted({(r[0], r[19]) for r in rows})
        bbox = read_bbox_from_latest(self.latest_path)
        async with self.conn.transaction():
            await self.conn.executemany(INSERT_SQL, rows)
            for st, source in pairs:
                # bbox from latest.json applies only to the matching source+time
                if bbox and bbox.get("snapshot_time") == st and source == "opensky":
                    args = (st, bbox["lamin"], bbox["lomin"], bbox["lamax"],
                            bbox["lomax"], bbox["generated_at"], source)
                else:
                    args = (st, None, None, None, None, None, source)
                await self.conn.execute(UPSERT_SNAPSHOT_SQL, *args)
            for source in {s for _, s in pairs}:
                match_st = (bbox or {}).get("snapshot_time") if source == "opensky" else None
                match_ct = (bbox or {}).get("count") if source == "opensky" else None
                await self.conn.execute(
                    MARK_COMPLETE_SQL, match_st or -1, match_ct or -1, source
                )
            if notify:
                for st in sorted({p[0] for p in pairs}):
                    await self.conn.execute("SELECT pg_notify($1, $2)",
                                            NOTIFY_CHANNEL, str(st))
        return sorted({p[0] for p in pairs})

    async def refresh_rollups(self) -> None:
        """Materialize the 1m continuous aggregate over ALL time (backfill)."""
        await self.conn.execute(
            "CALL refresh_continuous_aggregate('state_vectors_1m', NULL, NULL)"
        )


# ------------------------------------------------------------------------------
# Mode: backfill
# ------------------------------------------------------------------------------

def _open_archive(path: Path):
    """Open plain or gzip-rotated archive files transparently."""
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")


def _archive_files(target: Path, prefix: str) -> list[Path]:
    """A file → itself. A directory → every <prefix>*.jsonl[.gz] inside,
    sorted, so a full rebuild covers rotated history + the live file."""
    if target.is_dir():
        return sorted(
            p for p in target.iterdir()
            if p.name.startswith(prefix)
            and (p.name.endswith(".jsonl") or p.name.endswith(".jsonl.gz"))
        )
    return [target]


async def run_backfill(dsn: str, archive: Path, latest: Path, batch_size: int,
                       source: str) -> int:
    prefix = ARCHIVE_PREFIX.get(source, "states")
    files = _archive_files(archive, prefix) if archive.exists() else []
    if not files:
        logger.error("No %s*.jsonl[.gz] archive files found at: %s", prefix, archive)
        return 2

    ing = Ingester(dsn, latest)
    await ing.connect()
    try:
        rows_before = await ing.row_count()
        lines_read = parsed = invalid_total = 0
        t0 = time.monotonic()

        for path in files:
            logger.info("Ingesting %s (source=%s) ...", path.name, source)
            with _open_archive(path) as f:
                batch: list[str] = []
                for line in f:
                    lines_read += 1
                    batch.append(line)
                    if len(batch) >= batch_size:
                        rows, invalid = parse_lines(batch, source)
                        parsed += len(rows)
                        invalid_total += invalid
                        await ing.ingest_rows(rows, notify=False)
                        batch.clear()
                        logger.info("  %d lines processed...", lines_read)
                rows, invalid = parse_lines(batch, source)
                parsed += len(rows)
                invalid_total += invalid
                await ing.ingest_rows(rows, notify=False)

        logger.info("Refreshing continuous aggregate over backfilled range...")
        await ing.refresh_rollups()

        rows_after = await ing.row_count()
        inserted = rows_after - rows_before
        duplicates = parsed - inserted
        dt = time.monotonic() - t0

        logger.info("=" * 60)
        logger.info("RECONCILIATION (%.1fs)", dt)
        logger.info("  JSONL lines read:        %d", lines_read)
        logger.info("  valid records parsed:    %d", parsed)
        logger.info("  invalid/skipped lines:   %d", invalid_total)
        logger.info("  DB rows before:          %d", rows_before)
        logger.info("  DB rows after:           %d", rows_after)
        logger.info("  newly inserted:          %d", inserted)
        logger.info("  duplicates skipped:      %d  (already in DB or repeated "
                    "natural key — expected on re-runs; the collector's own "
                    "snapshot dedup means a fresh archive has none)", duplicates)
        logger.info("=" * 60)
        return 0
    finally:
        await ing.close()


# ------------------------------------------------------------------------------
# Mode: follow — tail the archive, survive DB outages
# ------------------------------------------------------------------------------

class TailState:
    """Byte-offset persistence: <state-dir>/.ingest_state.json.

    Defaults to the archive's own directory (scraplan's own feeds). For an
    EXTERNAL archive (adsb_scraper), pass state_dir so scraplan keeps its
    consumer offset under its own tree and never writes into the foreign
    output dir — whose rotation/cleanup must not be able to drop our offset.
    """

    def __init__(self, archive: Path, state_dir: Path | None = None) -> None:
        base = state_dir if state_dir is not None else archive.parent
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / ".ingest_state.json"
        self.archive = archive
        self.inode: int | None = None
        self.offset = 0

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("archive") == str(self.archive):
                self.inode = data.get("inode")
                self.offset = int(data.get("offset", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "archive": str(self.archive), "inode": self.inode, "offset": self.offset,
        }), encoding="utf-8")
        tmp.replace(self.path)


def _read_new_complete_lines(archive: Path, state: TailState) -> tuple[list[str], int]:
    """
    Read complete lines appended since state.offset. Returns (lines, new_offset).
    A trailing partial line (collector mid-flush) is left for the next poll.
    Detects file replacement/truncation via inode/size and rescans from zero.
    """
    st = os.stat(archive)
    if state.inode is not None and (st.st_ino != state.inode or st.st_size < state.offset):
        logger.warning("Archive replaced or truncated — rescanning from start "
                       "(idempotent, no duplicates possible)")
        state.offset = 0
    state.inode = st.st_ino
    if st.st_size <= state.offset:
        return [], state.offset

    with archive.open("rb") as f:
        f.seek(state.offset)
        blob = f.read(st.st_size - state.offset)
    last_nl = blob.rfind(b"\n")
    if last_nl == -1:
        return [], state.offset
    complete = blob[: last_nl + 1]
    lines = complete.decode("utf-8", errors="replace").splitlines()
    return lines, state.offset + last_nl + 1


async def run_follow(dsn: str, archive: Path, latest: Path, poll: float,
                     source: str = "opensky", state_dir: Path | None = None) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    state = TailState(archive, state_dir)
    state.load()
    logger.info("Following %s from offset %d", archive, state.offset)

    ing = Ingester(dsn, latest)
    connected = False
    backoff = 1.0

    while not stop.is_set():
        try:
            if not connected:
                await ing.connect()
                connected = True
                backoff = 1.0
                logger.info("DB connected")

            if archive.exists():
                lines, new_offset = _read_new_complete_lines(archive, state)
                if lines:
                    rows, invalid = parse_lines(lines, source)
                    if invalid:
                        logger.warning("%d invalid lines skipped", invalid)
                    touched = await ing.ingest_rows(rows, notify=True)
                    # Offset advances ONLY after the commit above — a crash or
                    # DB outage between read and commit re-reads those lines,
                    # and idempotency absorbs the replay.
                    state.offset = new_offset
                    state.save()
                    if touched:
                        logger.info("Ingested %d vectors (snapshots: %s)",
                                    len(rows), ", ".join(map(str, touched)))

        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, ConnectionError) as exc:
            logger.error("DB unavailable (%s) — collector keeps archiving to "
                         "JSONL; will retry in %.0fs", exc, backoff)
            with suppress(Exception):
                await ing.close()
            connected = False
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            backoff = min(backoff * 2, 30.0)
            continue

        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll)

    await ing.close()
    logger.info("Follow mode stopped (offset %d persisted)", state.offset)
    return 0


# ------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    load_dotenv()

    p = argparse.ArgumentParser(description="OpenSky JSONL → TimescaleDB ingester")
    sub = p.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--archive", type=Path,
                        default=Path("opensky_output/states.jsonl"),
                        help="Archive file, or a directory to ingest every "
                             "<prefix>*.jsonl[.gz] inside (rotated history)")
    common.add_argument("--latest", type=Path,
                        default=Path("opensky_output/latest.json"))
    common.add_argument("--source", default="opensky", choices=list(SOURCES),
                        help="Feed identity for these records (part of the "
                             "natural key). Selects <prefix>*.jsonl in a directory "
                             "backfill: opensky=states, airplanes_live=military, "
                             "adsbexchange=state_vectors. Records are self-describing "
                             "(their own `source`/geo fields win); this is the default.")

    pb = sub.add_parser("backfill", parents=[common],
                        help="Idempotently load the whole archive")
    pb.add_argument("--batch-size", type=int, default=5000)

    pf = sub.add_parser("follow", parents=[common],
                        help="Tail the archive continuously (live DB path)")
    pf.add_argument("--poll", type=float, default=2.0,
                    help="Seconds between file checks")
    pf.add_argument("--state-dir", type=Path, default=None,
                    help="Where to persist the tail offset (.ingest_state.json). "
                         "Defaults to the archive's own dir; set it for an EXTERNAL "
                         "archive (adsbexchange) so scraplan owns its offset.")

    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2

    if a.mode == "backfill":
        return asyncio.run(run_backfill(dsn, a.archive, a.latest, a.batch_size, a.source))
    return asyncio.run(run_follow(dsn, a.archive, a.latest, a.poll, a.source, a.state_dir))


if __name__ == "__main__":
    sys.exit(main())
