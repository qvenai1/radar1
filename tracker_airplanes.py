#!/usr/bin/env python3
"""
Military overlay collector — airplanes.live /v2/mil
====================================================
Complements the OpenSky pipeline with UNFILTERED military traffic (the one
thing OpenSky's feed largely lacks). Deliberately additive and isolated:

  * separate process, separate files (military.json + military.jsonl) —
    the OpenSky collector, DB schema and frozen API v1 are untouched.
    Full archival integration (source column in the natural key) is a
    planned, separate migration; this overlay is the live layer.
  * fields are NORMALIZED TO THE PROJECT'S METRIC CONTRACT at ingestion
    (airplanes.live uses ft / kt / ft-min): baro/geo altitude → meters,
    gs → m/s, baro_rate → m/s — so every existing frontend formatter and
    the StateVector shape work unchanged. Extra fields: actype (ICAO type),
    acdesc (full name), reg (registration).

Source etiquette: 1 request/second hard limit upstream — we poll every 30 s
with a descriptive User-Agent. /v2/mil returns global military traffic;
we filter to the configured bbox locally.

Usage
    python tracker_airplanes.py --lamin 12 --lomin 24 --lamax 42 --lomax 63.5
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

import aiohttp

logger = logging.getLogger("mil_tracker")

MIL_URL = "https://api.airplanes.live/v2/mil"
USER_AGENT = "AtheerRADAR/1.0 (+mailto:qvenai@gmail.com)"

FT_TO_M = 0.3048
KT_TO_MS = 0.514444
FPM_TO_MS = 0.00508


def normalize(a: dict[str, Any], snapshot_time: int) -> dict[str, Any] | None:
    """airplanes.live aircraft dict → project StateVector dict (metric)."""
    hexid = a.get("hex")
    if not isinstance(hexid, str) or len(hexid.strip()) < 4:
        return None
    lat, lon = a.get("lat"), a.get("lon")
    alt_raw = a.get("alt_baro")
    on_ground = alt_raw == "ground"
    baro_m = None if on_ground or not isinstance(alt_raw, (int, float)) else alt_raw * FT_TO_M
    geo_raw = a.get("alt_geom")
    gs = a.get("gs")
    vr = a.get("baro_rate")
    return {
        "icao24": hexid.strip().lower(),
        "callsign": str(a.get("flight") or ""),
        "origin_country": "",
        "time_position": None,
        "last_contact": snapshot_time,
        "longitude": lon if isinstance(lon, (int, float)) else None,
        "latitude": lat if isinstance(lat, (int, float)) else None,
        "baro_altitude": baro_m,
        "geo_altitude": geo_raw * FT_TO_M if isinstance(geo_raw, (int, float)) else None,
        "on_ground": on_ground,
        "velocity": gs * KT_TO_MS if isinstance(gs, (int, float)) else None,
        "true_track": a.get("track") if isinstance(a.get("track"), (int, float)) else None,
        "vertical_rate": vr * FPM_TO_MS if isinstance(vr, (int, float)) else None,
        "squawk": str(a.get("squawk") or ""),
        "spi": False,
        "position_source": None,
        "category": None,
        "snapshot_time": snapshot_time,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        # extras beyond the StateVector contract (additive, nullable)
        "source": "airplanes_live",
        "actype": a.get("t"),
        "acdesc": a.get("desc"),
        "reg": a.get("r"),
    }


async def poll_loop(bbox: dict[str, float], out_dir: Path, interval: float,
                    db_url: str = "") -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    out_dir.mkdir(parents=True, exist_ok=True)
    latest_path = out_dir / "military.json"
    archive_path = out_dir / "military.jsonl"
    backoff = interval
    last_written: int | None = None

    # Optional live DB mirror (unified state_vectors, source=airplanes_live).
    # Same failure-tolerant contract as the OpenSky collector: DB errors are
    # logged and swallowed; the JSONL archive is never affected.
    db_sink = None
    db_connected = False
    if db_url:
        from ingest import Ingester, parse_record
        db_sink = Ingester(db_url, latest_path)

    timeout = aiohttp.ClientTimeout(total=25, connect=10)
    async with aiohttp.ClientSession(
        timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    ) as session:
        while not stop.is_set():
            try:
                async with session.get(MIL_URL) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                snapshot_time = int((data.get("now") or 0) / 1000) or int(
                    datetime.now(timezone.utc).timestamp()
                )
                states = []
                for a in data.get("ac") or []:
                    s = normalize(a, snapshot_time)
                    if (
                        s is not None
                        and s["latitude"] is not None
                        and s["longitude"] is not None
                        and bbox["lamin"] <= s["latitude"] <= bbox["lamax"]
                        and bbox["lomin"] <= s["longitude"] <= bbox["lomax"]
                    ):
                        states.append(s)

                payload = {
                    "snapshot_time": snapshot_time,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "bbox": bbox,
                    "count": len(states),
                    "states": states,
                }
                tmp = latest_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                tmp.replace(latest_path)

                if states and snapshot_time != last_written:
                    with archive_path.open("a", encoding="utf-8") as f:
                        for s in states:
                            f.write(json.dumps(s, ensure_ascii=False) + "\n")
                    last_written = snapshot_time
                    # DB mirror AFTER the archive write (ground truth first).
                    if db_sink is not None:
                        try:
                            if not db_connected:
                                await db_sink.connect()
                                db_connected = True
                            rows = [row for s in states
                                    if (row := parse_record(s)) is not None]
                            await db_sink.ingest_rows(rows, notify=False)
                        except Exception as exc:  # noqa: BLE001 — DB must never stop archiving
                            logger.error("DB mirror failed (archive unaffected, "
                                         "backfill covers the gap): %s", exc)
                            with suppress(Exception):
                                await db_sink.close()
                            db_connected = False
                logger.info("military snapshot @ %d: %d in box (of %d global)",
                            snapshot_time, len(states), len(data.get("ac") or []))
                backoff = interval
            except Exception as exc:  # noqa: BLE001 — resilient poller
                logger.error("poll failed (%s) — retrying in %.0fs", exc, backoff)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                backoff = min(backoff * 2, 300.0)
                continue

            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)

    logger.info("military tracker stopped")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s",
                        datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="airplanes.live military overlay collector")
    p.add_argument("--lamin", type=float, default=12.0)
    p.add_argument("--lomin", type=float, default=24.0)
    p.add_argument("--lamax", type=float, default=42.0)
    p.add_argument("--lomax", type=float, default=63.5)
    p.add_argument("--interval", type=float, default=30.0,
                   help="Seconds between polls (upstream limit: 1 req/s)")
    p.add_argument("--output", type=Path, default=Path("opensky_output"))
    p.add_argument("--db-url", type=str, default=None,
                   help="Optional: also mirror military snapshots into the "
                        "unified DB (source=airplanes_live). Falls back to "
                        "$OPENSKY_DB_URL. DB failures never stop archiving.")
    a = p.parse_args()
    if a.interval < 5:
        p.error("interval must be >= 5s (be polite to the free API)")
    bbox = {"lamin": a.lamin, "lomin": a.lomin, "lamax": a.lamax, "lomax": a.lomax}
    db_url = a.db_url if a.db_url is not None else os.getenv("OPENSKY_DB_URL", "")
    return asyncio.run(poll_loop(bbox, a.output, a.interval, db_url))


if __name__ == "__main__":
    sys.exit(main())
