#!/usr/bin/env python3
"""
Maritime AIS collector — the sea-going sibling of tracker_opensky /
tracker_airplanes, built to the SAME architecture so the system stays
consistent:

    live adapter(s) → Fleet (event accumulator) → periodic snapshot
    → maritime_output/vessels.json   (atomic replace — live view source)
    → maritime_output/vessels.jsonl  (append — durable ground truth)
    → [phase 2] DB mirror into a unified vessel_states hypertable

Difference from the aviation collectors, and why: ADS-B sources are POLLED
(whole snapshots per request), but AIS is a PUSH stream of single-vessel
events. The Fleet class absorbs that impedance mismatch — it accumulates
stream events and lets this loop emit coherent fleet snapshots on the same
cadence/storage pattern the rest of the project uses.

Sources are pluggable adapters (maritime/adapters/); only AISStream is a
live feed today. GFW / Equasis / Copernicus are lookup/stub adapters wired
through the same interface (enable per env var).

LIMITS (also in every adapter + the README):
  * free AIS is TERRESTRIAL only (~40–60 NM off shore) — open ocean is dark;
  * AIS is self-reported and spoofable — never a verified truth alone.

Usage
    export AISSTREAM_API_KEY=...            # required
    python tracker_maritime.py              # global bbox, 30 s snapshots
    python tracker_maritime.py --bbox 20 30 45 65   # lamin lomin lamax lomax
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

from archive import RawLog, emit_signal
from maritime.adapters.aisstream import AisStreamAdapter
from maritime.fleet import Fleet

logger = logging.getLogger("maritime.collector")


def _bbox_to_aisstream(b: list[float] | None) -> list:
    """[lamin, lomin, lamax, lomax] → AISStream [[[lat,lon],[lat,lon]]]."""
    if not b:
        return [[[-90.0, -180.0], [90.0, 180.0]]]
    lamin, lomin, lamax, lomax = b
    return [[[lamin, lomin], [lamax, lomax]]]


async def run(bbox: list[float] | None, out_dir: Path, interval: float,
              max_age: float, db_url: str = "") -> int:
    api_key = os.getenv("AISSTREAM_API_KEY", "")
    if not api_key:
        logger.error("AISSTREAM_API_KEY not set — see .env.example")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    latest_path = out_dir / "vessels.json"
    signals_raw = RawLog(out_dir / "ais-signals.jsonl")  # every new signal (ground truth)
    op_signals = RawLog(out_dir / "signals.jsonl")       # operational signal log

    fleet = Fleet()
    adapter = AisStreamAdapter(
        api_key=api_key,
        bounding_boxes=_bbox_to_aisstream(bbox),
    )

    # Optional DB mirror into vessel_states — SAME failure-tolerant contract
    # as the aviation collector: the JSONL archive is ground truth and is
    # written FIRST; any DB error is logged and swallowed so a DB outage can
    # never stop archiving. Gaps are covered by `backfill` when the DB returns.
    db_sink = None
    db_connected = False
    if db_url:
        from maritime.db import VesselIngester, vessel_row
        db_sink = VesselIngester(db_url)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    feed_task = asyncio.create_task(adapter.run(fleet), name="aisstream-feed")

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
                "bbox": bbox,
                "count": len(states),
                "adapter": adapter.health(),
                "states": states,
            }
            tmp = latest_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(latest_path)

            # EVERY new-information signal since last tick (position + static
            # events) → raw ground-truth log; position events → vessel_states.
            events = fleet.drain_events()
            positions = [e for e in events if e.get("kind") == "position"]
            if events:
                signals_raw.append_many(events)
                # DB mirror AFTER the raw write (ground truth first).
                if db_sink is not None:
                    try:
                        if not db_connected:
                            await db_sink.connect()
                            db_connected = True
                        rows = [r for v in positions if (r := vessel_row(v)) is not None]
                        await db_sink.ingest(rows, snapshot={
                            "snapshot_time": snapshot_time, "source": "aisstream",
                            "count": len(states), "bbox": _bbox_dict(bbox),
                            "generated_at": payload["generated_at"],
                        }, identities=positions)  # M2b: promote merged static identity
                    except Exception as exc:  # noqa: BLE001 — DB never stops archiving
                        logger.error("DB mirror failed (archive unaffected, "
                                     "backfill covers the gap): %s", exc)
                        with suppress(Exception):
                            await db_sink.close()
                        db_connected = False

            # operational signals from the feed (connect/disconnect) → unified log + DB
            for kind, detail in adapter.drain_signals():
                db_exec = db_sink.conn if (db_sink and db_connected) else None
                await emit_signal(op_signals, db_exec, "aisstream", kind, detail)

            h = adapter.health()
            logger.info(
                "maritime @ %d: %d live | +%d signals (%d pos) | msgs=%d reconnects=%d",
                snapshot_time, len(states), len(events), len(positions),
                h["messages"], h["reconnects"],
            )
    finally:
        adapter.stop()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(feed_task, timeout=5)
        if db_sink is not None:
            with suppress(Exception):
                await db_sink.close()

    logger.info("maritime collector stopped")
    return 0


def _bbox_dict(b: list[float] | None) -> dict[str, float] | None:
    if not b:
        return None
    return {"lamin": b[0], "lomin": b[1], "lamax": b[2], "lomax": b[3]}


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    # local .env is convenient for the key in dev; env still wins
    with suppress(Exception):
        from dotenv import load_dotenv
        load_dotenv()

    p = argparse.ArgumentParser(description="Maritime AIS collector (AISStream.io)")
    sub = p.add_subparsers(dest="cmd")

    # default (no subcommand) = live collect
    p.add_argument("--bbox", type=float, nargs=4, metavar=("LAMIN", "LOMIN", "LAMAX", "LOMAX"),
                   default=None, help="Bounding box; omit for global coverage")
    p.add_argument("--interval", type=float, default=30.0,
                   help="Seconds between fleet snapshots")
    p.add_argument("--max-age", type=float, default=600.0,
                   help="Drop vessels whose last fix is older than this (s)")
    p.add_argument("--output", type=Path, default=Path("maritime_output"))
    p.add_argument("--db-url", type=str, default=None,
                   help="Optional: also mirror vessels into the DB "
                        "(source=aisstream). Falls back to $OPENSKY_DB_URL. "
                        "DB failures never stop archiving.")

    pb = sub.add_parser("backfill", help="Load vessels.jsonl into the DB (idempotent)")
    pb.add_argument("--archive", type=Path, default=Path("maritime_output"),
                    help="vessels.jsonl file or a directory (reads rotated .gz too)")

    a = p.parse_args()

    if a.cmd == "backfill":
        dsn = os.getenv("OPENSKY_DB_URL") or os.getenv("DATABASE_URL", "")
        if not dsn:
            print("ERROR: set OPENSKY_DB_URL or DATABASE_URL for backfill", file=sys.stderr)
            return 2
        from maritime.db import backfill_vessels
        return asyncio.run(backfill_vessels(dsn, a.archive))

    if a.interval < 5:
        p.error("interval must be >= 5s")
    db_url = a.db_url if a.db_url is not None else os.getenv("OPENSKY_DB_URL", "")
    return asyncio.run(run(a.bbox, a.output, a.interval, a.max_age, db_url))


if __name__ == "__main__":
    sys.exit(main())
