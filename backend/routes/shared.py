"""Cross-domain request helpers: the DB accessor and the time-range grammar."""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import HTTPException, Request

from ..db import DB_ERRORS, Database


def _db(request: Request) -> Database:
    return request.app.state.db


def parse_time(value: str | None, param: str) -> int | None:
    """Accept unix epoch seconds or ISO-8601; return epoch seconds."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        raise HTTPException(422, f"'{param}' must be unix epoch seconds or ISO-8601") from None


def resolve_range(from_raw: str | None, to_raw: str | None,
                  default_span: int, max_span: int) -> tuple[int, int]:
    to_t = parse_time(to_raw, "to") or int(time.time())
    from_t = parse_time(from_raw, "from")
    if from_t is None:
        from_t = to_t - default_span
    if from_t >= to_t:
        raise HTTPException(422, "'from' must be earlier than 'to'")
    if to_t - from_t > max_span:
        raise HTTPException(
            422, f"time range too large (max {max_span}s); narrow the window"
        )
    return from_t, to_t


def _iso(v: Any) -> str | None:
    """isoformat-or-None — the detail-row timestamp mapper every list reader uses.

    Was nine identical nested copies across the old api.py (arch review finding 9);
    a fix to timestamp mapping now lands in exactly one place.
    """
    return v.isoformat() if v is not None else None


@contextmanager
def db_503() -> Iterator[None]:
    """The uniform DB-unavailable contract: any DB_ERRORS inside becomes one 503.

    Replaces 24 hand-copied try/except blocks (arch review finding 9) — a change to
    the degradation message or the caught set now lands in exactly one place. A sync
    contextmanager on purpose: `await` inside the with-block propagates through it.
    """
    try:
        yield
    except DB_ERRORS as exc:
        raise HTTPException(503, f"database unavailable: {exc}") from exc
