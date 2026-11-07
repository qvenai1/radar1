"""Service health — /health."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from fastapi import APIRouter, Request
from ..config import settings
from ..db import (
    DB_ERRORS,
    read_latest_json,
)
from ..models import (
    ArchiveHealth, DbHealth,
    HealthResponse, IngestionHealth,
)

from .shared import _db

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> Any:
    db = _db(request)
    now = time.time()

    db_connected = False
    latest_st: int | None = None
    try:
        snap = await db.latest_snapshot_row()
        db_connected = True
        latest_st = snap["snapshot_time"] if snap else None
    except DB_ERRORS:
        pass

    snapshot_age = (now - latest_st) if latest_st else None
    db_stale = snapshot_age is None or snapshot_age > settings.stale_after_seconds

    latest_present = settings.latest_json.exists()
    archive_age = (now - settings.latest_json.stat().st_mtime) if latest_present else None

    lag: float | None = None
    if latest_present and latest_st:
        data = await asyncio.to_thread(read_latest_json, settings.latest_json)
        if data and data.get("snapshot_time"):
            lag = float(data["snapshot_time"] - latest_st)
    fresh = db_connected and lag is not None and abs(lag) <= settings.stale_after_seconds

    return HealthResponse(
        ok=db_connected and not db_stale,
        db=DbHealth(
            connected=db_connected,
            latest_snapshot_time=latest_st,
            snapshot_age_seconds=round(snapshot_age, 1) if snapshot_age else None,
            stale=db_stale,
        ),
        archive=ArchiveHealth(
            latest_json_present=latest_present,
            age_seconds=round(archive_age, 1) if archive_age is not None else None,
        ),
        ingestion=IngestionHealth(fresh=fresh, lag_seconds=lag),
    )
