"""The current-snapshot payload — shared by REST and the WebSocket stream.

Lives OUTSIDE the route module on purpose: the transport layer (`stream.py`)
used to import the entire 4,000-line REST surface to reach this one function,
inverting the layering (architecture review finding 1). Routes and stream now
both import from here; nothing here imports from the routes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from .config import settings
from .db import (
    DB_ERRORS,
    Database,
    bbox_or_none,
    read_latest_json,
    state_record_to_dict,
)


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


async def build_live_payload(db: Database) -> dict[str, Any]:
    """
    Current snapshot. Shared by /live, /search, /api/latest (legacy) and the
    WebSocket stream.

    Source order is ARCHIVE FIRST, deliberately: latest.json is the artifact
    the collector atomically replaces the instant a snapshot completes — it
    is always whole and always newest (the DB derives from the archive, so
    it can never be ahead). The DB path is the fallback for when latest.json
    is missing/corrupt, restricted to COMPLETE snapshots so a half-ingested
    snapshot is never presented as "current".
    """
    data = await asyncio.to_thread(read_latest_json, settings.latest_json)
    if data and data.get("snapshot_time") and isinstance(data.get("states"), list):
        st = data["snapshot_time"]
        return {
            "snapshot_time": st,
            "ts": iso(st),
            "bbox": data.get("bbox"),
            "count": data.get("count", len(data["states"])),
            "source": "archive",
            "states": data["states"],
        }

    try:
        snap = await db.latest_snapshot_row(complete_only=True)
        if snap is not None:
            states = await db.states_at(snap["snapshot_time"])
            return {
                "snapshot_time": snap["snapshot_time"],
                "ts": snap["ts"].isoformat(),
                "bbox": bbox_or_none(snap),
                "count": len(states),
                "source": "db",
                "states": [state_record_to_dict(r) for r in states],
            }
    except DB_ERRORS:
        pass

    raise HTTPException(503, "No snapshot available (no latest.json and DB unreachable or empty)")
