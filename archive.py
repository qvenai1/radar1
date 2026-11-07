"""
Shared archival primitives — the reusable core of "leave no signal".

Two pieces every collector uses so archiving is uniform, not per-source:

  * RawLog       — append-only JSONL writer. The file IS the ground truth;
    the DB is a derived, rebuildable index. Opened per append (append mode),
    which is exactly what makes daily rotation safe: after rotate_archive.sh
    moves the file away, the next append recreates it (same behaviour the
    aviation/maritime collectors already rely on).

  * emit_signal  — record ONE operational signal (feed up/down, AIS gap,
    upstream throttle, DB-mirror failure, collector run) to BOTH the raw
    signals.jsonl log and the signal_events hypertable (migration 010).
    Ground-truth file write happens first and always; the DB insert is
    best-effort and swallowed on failure — a signal must never crash a
    collector, same resilience contract as the data mirrors.

All timestamps UTC ISO 8601.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("archive")


class RawLog:
    """Append-only JSONL ground-truth log, rotation-safe."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        self.append_many((record,))

    def append_many(self, records: "list[dict[str, Any]] | tuple[dict[str, Any], ...]") -> None:
        if not records:
            return
        blob = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(blob)


class _Executor(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...


_INSERT_SIGNAL = (
    "INSERT INTO signal_events (ts, source, kind, detail) "
    "VALUES (now(), $1, $2, $3::jsonb)"
)


async def emit_signal(
    raw_log: RawLog | None,
    db: "_Executor | None",
    source: str,
    kind: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Record one operational signal to the raw log (always) and the DB
    (best-effort). Never raises."""
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "kind": kind,
        "detail": detail or {},
    }
    if raw_log is not None:
        try:
            raw_log.append(event)
        except OSError as exc:  # disk full etc — log, don't crash the collector
            logger.warning("signal raw-log write failed: %s", exc)
    if db is not None:
        try:
            await db.execute(_INSERT_SIGNAL, source, kind, json.dumps(event["detail"]))
        except Exception as exc:  # noqa: BLE001 — DB signal is best-effort
            logger.debug("signal DB insert failed (raw log kept it): %s", exc)
