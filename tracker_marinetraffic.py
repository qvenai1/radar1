#!/usr/bin/env python3
"""
MarineTraffic collector — the browser-scraped sibling of tracker_maritime,
built to the SAME architecture so the system stays consistent:

    tail mtscraper's positions.jsonl → Fleet (event accumulator) → periodic snapshot
    → marinetraffic_output/vessels.json   (atomic replace — live view source)
    → marinetraffic_output/positions.jsonl (append — durable ground truth)
    → DB mirror into mt_vessel_states (+ vessel_xref links)   [source=marinetraffic]

WHY A SEPARATE TRACKER (not a flag on tracker_maritime): MarineTraffic is an
INDEPENDENT live source with a different identity space (ship_id, not MMSI) and
a different transport (a file written by the standalone `mtscraper` project, not
a socket). It therefore gets its own composition root, its own Fleet, and its
own output dir — and runs as its own process, exactly as the DB schema was
designed for (source is part of every natural key so sources never collide).

ISOLATION (a hard requirement inherited from mtscraper): we import NONE of
mtscraper's code and never launch it. The only coupling is the data contract —
its positions.jsonl — read through the MarineTraffic adapter. mtscraper runs on
its own (its browser stays in its own process); this collector just consumes the
file it produces. If mtscraper is not running, this simply sees no new lines.

DB failure-tolerance is identical to the AIS collector: the JSONL archive is
ground truth and is written FIRST; any DB error is logged and swallowed so a DB
outage can never stop archiving. `backfill` covers the gap when the DB returns.

LEGAL: MarineTraffic's ToS prohibit scraping (see mtscraper's README). Rows are
provenance-tagged source='marinetraffic' so the origin is never ambiguous.

Usage
    # live: tail the default mtscraper output, 30 s snapshots, mirror to DB
    python tracker_marinetraffic.py --mt-output ../mtscraper/mt_output

    # rebuild the DB from the archive (idempotent)
    python tracker_marinetraffic.py backfill --archive ../mtscraper/mt_output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from archive import RawLog
from maritime.adapters.marinetraffic import MarineTrafficAdapter
from maritime.fleet import Fleet

logger = logging.getLogger("marinetraffic.collector")

DEFAULT_MT_OUTPUT = (
    Path("maritime_source_marine/mt_output")
    if Path("maritime_source_marine/mt_output").exists()
    else (
        Path("mtscraper/mt_output")
        if Path("mtscraper/mt_output").exists()
        else Path("../mtscraper/mt_output")
    )
)


async def run(positions_paths: list[Path], out_dir: Path, interval: float, max_age: float,
              poll: float, from_start: bool, db_url: str = "") -> int:
    for p in positions_paths:
        if not p.exists():
            logger.warning("MarineTraffic positions file not found yet at %s — will wait "
                           "for mtscraper to create it", p)

    out_dir.mkdir(parents=True, exist_ok=True)
    latest_path = out_dir / "vessels.json"
    positions_raw = RawLog(out_dir / "positions.jsonl")  # every new signal (ground truth)

    fleet = Fleet()
    # One tail adapter per regional mtscraper output, ALL feeding the SHARED Fleet
    # (keyed by ship_id, so regions merge without collision). This is how several
    # MarineTraffic viewports — e.g. E. Med + Persian Gulf + Red Sea, each scraped
    # by its own mtscraper instance into its own dir — become one unified fleet.
    adapters = [MarineTrafficAdapter(p, poll_interval=poll, from_start=from_start)
                for p in positions_paths]

    # Optional DB mirror into mt_vessel_states — same contract as the AIS
    # collector: archive first, DB best-effort.
    db_sink = None
    db_connected = False
    if db_url:
        from maritime.db import MtVesselIngester, mt_vessel_row, mt_xref_row
        db_sink = MtVesselIngester(db_url)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    feed_tasks = [asyncio.create_task(a.run(fleet), name=f"marinetraffic-feed-{i}")
                  for i, a in enumerate(adapters)]

    try:
        while not stop.is_set():
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)
            if stop.is_set():
                break

            snapshot_time = int(datetime.now(timezone.utc).timestamp())
            states = fleet.snapshot(max_age_s=max_age)
            payload: dict[str, Any] = {
                "snapshot_time": snapshot_time,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "count": len(states),
                "adapters": [a.health() for a in adapters],
                "states": states,
            }
            tmp = latest_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(latest_path)

            events = fleet.drain_events()
            positions = [e for e in events if e.get("kind") == "position"]
            if events:
                positions_raw.append_many(events)
                # DB mirror AFTER the raw write (ground truth first).
                if db_sink is not None:
                    try:
                        if not db_connected:
                            await db_sink.connect()
                            db_connected = True
                        rows = [r for e in positions if (r := mt_vessel_row(e)) is not None]
                        xrefs = [x for e in positions if (x := mt_xref_row(e)) is not None]
                        await db_sink.ingest(rows, snapshot={
                            "snapshot_time": snapshot_time, "source": "marinetraffic",
                            "count": len(states), "bbox": {},
                            "generated_at": payload["generated_at"],
                        }, xrefs=xrefs)
                    except Exception as exc:  # noqa: BLE001 — DB never stops archiving
                        logger.error("DB mirror failed (archive unaffected, "
                                     "backfill covers the gap): %s", exc)
                        with suppress(Exception):
                            await db_sink.close()
                        db_connected = False

            fed = sum(a.health()["records_fed"] for a in adapters)
            reopens = sum(a.health()["reopens"] for a in adapters)
            logger.info(
                "marinetraffic @ %d: %d live | +%d signals (%d pos) | sources=%d fed=%d reopens=%d",
                snapshot_time, len(states), len(events), len(positions),
                len(adapters), fed, reopens,
            )
    finally:
        for a in adapters:
            a.stop()
        for t in feed_tasks:
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(t, timeout=5)
        if db_sink is not None:
            with suppress(Exception):
                await db_sink.close()

    logger.info("marinetraffic collector stopped")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    with suppress(Exception):
        from dotenv import load_dotenv
        load_dotenv()

    p = argparse.ArgumentParser(description="MarineTraffic collector (tails mtscraper output)")
    sub = p.add_subparsers(dest="cmd")

    p.add_argument("--mt-output", type=Path, nargs="+", default=[DEFAULT_MT_OUTPUT],
                   help="One OR MORE mtscraper output dirs (or positions.jsonl files). "
                        "Pass several to cover several regions at once — e.g. the E. Med "
                        "run plus a Persian-Gulf run plus a Red-Sea run, each its own "
                        f"mtscraper instance/dir. (default {DEFAULT_MT_OUTPUT})")
    p.add_argument("--interval", type=float, default=30.0,
                   help="Seconds between fleet snapshots")
    p.add_argument("--poll", type=float, default=5.0,
                   help="Seconds between file-tail polls")
    p.add_argument("--max-age", type=float, default=1800.0,
                   help="Drop vessels whose last fix is older than this (s). Higher "
                        "than AIS: MarineTraffic reports are cyclic, not per-second.")
    p.add_argument("--from-start", action="store_true",
                   help="Also ingest the file's existing history on startup "
                        "(default: live tail from EOF)")
    p.add_argument("--output", type=Path, default=Path("marinetraffic_output"))
    p.add_argument("--db-url", type=str, default=None,
                   help="Optional: also mirror into mt_vessel_states "
                        "(source=marinetraffic). Falls back to $OPENSKY_DB_URL. "
                        "DB failures never stop archiving.")

    pb = sub.add_parser("backfill", help="Load positions.jsonl into the DB (idempotent)")
    pb.add_argument("--archive", type=Path, default=DEFAULT_MT_OUTPUT,
                    help="positions.jsonl file or a directory (reads rotated .gz too)")

    pi = sub.add_parser("identities",
                        help="Load mtscraper's resolved MMSI/IMO into vessel_xref "
                             "(idempotent). FED links — stronger than correlation.")
    pi.add_argument("--archive", type=Path, default=DEFAULT_MT_OUTPUT,
                    help="vessel_identities.jsonl file, or an mtscraper output dir")

    pc = sub.add_parser("correlate",
                        help="Correlate MarineTraffic × AIS by name/position/dimensions "
                             "and store derived links in vessel_xref (idempotent)")
    pc.add_argument("--ais-pad-days", type=float, default=2.0,
                    help="Pad the AIS scan window by N days each side (default 2)")

    a = p.parse_args()

    if a.cmd == "identities":
        dsn = os.getenv("OPENSKY_DB_URL") or os.getenv("DATABASE_URL", "")
        if not dsn:
            print("ERROR: set OPENSKY_DB_URL or DATABASE_URL for identities", file=sys.stderr)
            return 2
        from maritime.db import ingest_mt_identities
        return asyncio.run(ingest_mt_identities(dsn, a.archive))

    if a.cmd == "backfill":
        dsn = os.getenv("OPENSKY_DB_URL") or os.getenv("DATABASE_URL", "")
        if not dsn:
            print("ERROR: set OPENSKY_DB_URL or DATABASE_URL for backfill", file=sys.stderr)
            return 2
        from maritime.db import backfill_mt
        return asyncio.run(backfill_mt(dsn, a.archive))

    if a.cmd == "correlate":
        dsn = os.getenv("OPENSKY_DB_URL") or os.getenv("DATABASE_URL", "")
        if not dsn:
            print("ERROR: set OPENSKY_DB_URL or DATABASE_URL for correlate", file=sys.stderr)
            return 2
        import json as _json

        from backend.vessel_crossref import correlate_and_store
        rep = asyncio.run(correlate_and_store(dsn, ais_pad_days=a.ais_pad_days))
        logger.info("CROSSREF %s", _json.dumps(rep, ensure_ascii=False))
        return 0

    # Resolve each dir to its positions.jsonl; accept direct file paths too.
    positions_paths = [d / "positions.jsonl" if d.is_dir() else d for d in a.mt_output]
    if a.interval < 5:
        p.error("interval must be >= 5s")
    db_url = a.db_url if a.db_url is not None else os.getenv("OPENSKY_DB_URL", "")
    return asyncio.run(run(positions_paths, a.output, a.interval, a.max_age,
                           a.poll, a.from_start, db_url))


if __name__ == "__main__":
    sys.exit(main())
