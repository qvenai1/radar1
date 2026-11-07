#!/usr/bin/env python3
"""
Satellite orbital-elements collector — CelesTrak → TimescaleDB.

Architecture (per the source review): DON'T poll a per-position API (N2YO is
observer-centric, 1000 req/hr). Cache TLE/orbital elements from CelesTrak
(free, keyless) and PROPAGATE POSITIONS LOCALLY in the browser (satellite.js,
SGP4) — a satellite position is deterministic from its TLE, so the backend
only serves elements and the client animates with zero server load.

CelesTrak throttles aggressive polling (verified: rapid repeats → 403), so
this is a BATCH collector run a few times a day by a systemd TIMER (not a
long-running service): one pass over the configured groups, then exit. A
polite delay separates group fetches.

Each group is refreshed transactionally (DELETE grp + INSERT) so membership
changes (decays, launches) don't leave stale rows.

Usage
    export OPENSKY_DB_URL=...            # or DATABASE_URL
    python tracker_satellites.py                       # curated default groups
    python tracker_satellites.py --groups stations,gps-ops,military
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import asyncpg

from archive import RawLog, emit_signal
from backend.spacetrack_client import alpha5_to_int

logger = logging.getLogger("sat_tracker")

CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
USER_AGENT = os.environ.get("ENRICH_CONTACT_UA", "AtheerRADAR/1.0 (+mailto:qvenai@gmail.com)")

# Curated default set — broad and useful without the ~7k Starlink flood
# (Starlink is available on demand; the frontend limits what it renders).
DEFAULT_GROUPS = ["stations", "gps-ops", "galileo", "weather",
                  "noaa", "science", "military", "geo"]

POLITE_DELAY_S = 3.0  # between group fetches — respect CelesTrak throttling

UPSERT_SQL = """
INSERT INTO satellite_elements (norad_id, grp, object_name, tle_line1, tle_line2, epoch, fetched_at)
VALUES ($1, $2, $3, $4, $5, $6, now())
ON CONFLICT (norad_id, grp) DO UPDATE SET
    object_name=$3, tle_line1=$4, tle_line2=$5, epoch=$6, fetched_at=now()
"""

# append-only history: a new epoch = a new row; re-fetch between epochs no-ops
HISTORY_SQL = """
INSERT INTO satellite_history (epoch, norad_id, grp, object_name, tle_line1, tle_line2, fetched_at)
VALUES ($1, $2, $3, $4, $5, $6, now())
ON CONFLICT (norad_id, epoch, grp) DO NOTHING
"""


def parse_tle_epoch(line1: str) -> datetime | None:
    """TLE line 1 cols 19-32: 'YYDDD.DDDDDDDD' → UTC datetime."""
    try:
        field = line1[18:32].strip()
        yy = int(field[:2])
        doy = float(field[2:])
        year = 2000 + yy if yy < 57 else 1900 + yy
        return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
    except (ValueError, IndexError):
        return None


def parse_3le(text: str) -> list[tuple[int, str, str, str, datetime | None]]:
    """CelesTrak 3LE text → [(norad_id, name, line1, line2, epoch)].

    The catalog number (columns 3-7) is decoded with alpha5_to_int so 6-digit
    ids (catalog > 99999, Alpha-5 encoded in the fixed-width TLE since 2026-07-11)
    round-trip to the correct integer instead of being silently dropped."""
    lines = [ln.rstrip("\r").rstrip() for ln in text.splitlines() if ln.strip()]
    out = []
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            continue
        try:
            norad = alpha5_to_int(l1[2:7].strip())
        except ValueError:
            continue
        out.append((norad, name.strip(), l1, l2, parse_tle_epoch(l1)))
    return out


async def fetch_group(session: aiohttp.ClientSession, group: str) -> str | None:
    try:
        async with session.get(CELESTRAK, params={"GROUP": group, "FORMAT": "tle"}) as resp:
            if resp.status != 200:
                logger.warning("CelesTrak %s -> HTTP %d (throttled?)", group, resp.status)
                return None
            return await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("CelesTrak %s failed: %s", group, exc)
        return None


async def run(dsn: str, groups: list[str], out_dir: Path) -> int:
    conn = await asyncpg.connect(dsn)
    timeout = aiohttp.ClientTimeout(total=45, connect=10)
    tle_log = RawLog(out_dir / "tle.jsonl")        # append-only ground truth
    op_signals = RawLog(out_dir / "signals.jsonl")  # operational signal log
    now_iso = datetime.now(timezone.utc).isoformat()
    total = history_rows = fetched_groups = throttled = 0
    try:
        async with aiohttp.ClientSession(
            timeout=timeout, headers={"User-Agent": USER_AGENT}
        ) as session:
            for i, group in enumerate(groups):
                if i:
                    await asyncio.sleep(POLITE_DELAY_S)
                text = await fetch_group(session, group)
                if text is None:
                    throttled += 1
                    await emit_signal(op_signals, conn, "celestrak", "throttle", {"group": group})
                    continue
                elements = parse_3le(text)
                if not elements:
                    logger.warning("group %s parsed 0 elements", group)
                    continue
                # current table: transactional refresh (membership can't go stale)
                async with conn.transaction():
                    await conn.execute("DELETE FROM satellite_elements WHERE grp = $1", group)
                    await conn.executemany(
                        UPSERT_SQL,
                        [(n, group, nm, l1, l2, ep) for (n, nm, l1, l2, ep) in elements],
                    )
                # history: append every element with a parseable epoch
                hist = [(ep, n, group, nm, l1, l2)
                        for (n, nm, l1, l2, ep) in elements if ep is not None]
                if hist:
                    await conn.executemany(HISTORY_SQL, hist)
                    history_rows += len(hist)
                # raw ground-truth log (every fetched element this run)
                tle_log.append_many([
                    {"norad_id": n, "grp": group, "name": nm, "tle1": l1, "tle2": l2,
                     "epoch": ep.isoformat() if ep else None, "fetched_at": now_iso}
                    for (n, nm, l1, l2, ep) in elements
                ])
                total += len(elements)
                fetched_groups += 1
                logger.info("group %-10s → %d satellites", group, len(elements))
        await emit_signal(op_signals, conn, "celestrak", "collector_run", {
            "groups": fetched_groups, "elements": total,
            "history_rows": history_rows, "throttled": throttled,
        })
        logger.info("=" * 48)
        logger.info("SATELLITE REFRESH: %d groups, %d elements, %d history rows",
                    fetched_groups, total, history_rows)
        logger.info("=" * 48)
        return 0 if fetched_groups else 1
    finally:
        await conn.close()


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" \
        else path.open(encoding="utf-8")


async def backfill_history(dsn: str, archive: Path) -> int:
    """Rebuild satellite_history from the raw tle.jsonl log(s), idempotently."""
    files = sorted(archive.glob("tle*.jsonl*")) if archive.is_dir() else [archive]
    files = [f for f in files if f.exists()]
    if not files:
        logger.error("no TLE archive found at %s", archive)
        return 2
    conn = await asyncpg.connect(dsn)
    try:
        before = await conn.fetchval("SELECT count(*) FROM satellite_history")
        rows = 0
        for path in files:
            batch = []
            with _open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not r.get("epoch"):
                        continue
                    ep = datetime.fromisoformat(r["epoch"])
                    batch.append((ep, r["norad_id"], r["grp"], r.get("name", ""),
                                  r["tle1"], r["tle2"]))
                    if len(batch) >= 5000:
                        await conn.executemany(HISTORY_SQL, batch); rows += len(batch); batch.clear()
            if batch:
                await conn.executemany(HISTORY_SQL, batch); rows += len(batch)
        after = await conn.fetchval("SELECT count(*) FROM satellite_history")
        logger.info("backfill: %d lines processed, rows %d → %d (+%d new)",
                    rows, before, after, after - before)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s",
                        datefmt="%H:%M:%S")
    from contextlib import suppress
    with suppress(Exception):
        from dotenv import load_dotenv
        load_dotenv()

    p = argparse.ArgumentParser(description="CelesTrak → DB satellite elements collector")
    p.add_argument("--groups", type=str, default=",".join(DEFAULT_GROUPS),
                   help="comma-separated CelesTrak groups")
    p.add_argument("--output", type=Path, default=Path("satellite_output"))
    sub = p.add_subparsers(dest="cmd")
    pb = sub.add_parser("backfill", help="rebuild satellite_history from tle.jsonl")
    pb.add_argument("--archive", type=Path, default=Path("satellite_output"))
    a = p.parse_args()

    dsn = os.getenv("OPENSKY_DB_URL") or os.getenv("DATABASE_URL", "")
    if not dsn:
        print("ERROR: set OPENSKY_DB_URL or DATABASE_URL", file=sys.stderr)
        return 2
    if a.cmd == "backfill":
        return asyncio.run(backfill_history(dsn, a.archive))
    groups = [g.strip() for g in a.groups.split(",") if g.strip()]
    return asyncio.run(run(dsn, groups, a.output))


if __name__ == "__main__":
    sys.exit(main())
