"""Durable JSONL persistence.

The durability engine — buffering, a background flush timer, ``fsync``, and
crash-repair of a trailing partial line — lives in :class:`DurableJsonlWriter`.
Two thin adapters build on it:

* :class:`JsonlSink` — the ICAO-dedup discovery catalogue's sink; writes
  :class:`~adsb_scraper.models.AircraftRecord` (via ``to_dict``) and satisfies the
  :class:`AircraftSink` Protocol used by the accumulator.
* the StateVector time-series sink (see :mod:`adsb_scraper.statevector`) reuses
  the same engine for arbitrary JSON lines.

Sharing one engine keeps the durability discipline identical everywhere without
duplicating the (subtle) crash-repair and flush logic.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol, TextIO, runtime_checkable

from .exceptions import WriteError
from .logger import get_logger
from .models import AircraftRecord
from .utils import Clock, SystemClock

_log = get_logger("persistence")


@runtime_checkable
class AircraftSink(Protocol):
    """A destination for aircraft records."""

    async def write(self, record: AircraftRecord) -> None: ...
    async def flush(self) -> None: ...
    async def aclose(self) -> None: ...

    @property
    def total_written(self) -> int: ...


def scan_jsonl(path: Path) -> tuple[int, int]:
    """Return ``(valid_lines, invalid_lines)`` for an existing JSONL file.

    A cheap integrity check usable from diagnostics or tests without mutating
    the file.
    """
    valid = invalid = 0
    if not path.exists():
        return (0, 0)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
                valid += 1
            except json.JSONDecodeError:
                invalid += 1
    return (valid, invalid)


class DurableJsonlWriter:
    """Buffered, fsync-durable, crash-repairing append-only JSONL writer.

    Concurrency model: a single :class:`asyncio.Lock` guards the buffer and the
    file. Critical sections are tiny (a list append, or a serialise-and-write),
    the process is single-event-loop, and disk writes are serialised through the
    same lock — so there is exactly one writer and no interleaving. This is the
    minimum locking that is still correct. This engine performs NO deduplication:
    it appends every line it is given.
    """

    def __init__(
        self,
        path: Path,
        *,
        name: str = "jsonl",
        batch_size: int = 50,
        flush_interval: float = 5.0,
        fsync: bool = True,
        clock: Clock | None = None,
    ) -> None:
        self._path = path
        self._name = name
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._fsync = fsync
        self._clock: Clock = clock or SystemClock()
        self._buffer: list[str] = []
        self._lock = asyncio.Lock()
        self._file: TextIO | None = None
        self._total_written = 0
        self._last_flush = self._clock.monotonic()
        self._flusher: asyncio.Task[None] | None = None
        self._closed = False

    # -- lifecycle --------------------------------------------------------- #

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._repair_trailing_partial)
        self._file = await asyncio.to_thread(open, self._path, "a", encoding="utf-8")
        self._flusher = asyncio.create_task(self._flush_loop(), name=f"{self._name}-flush-timer")
        _log.info("%s open at %s (fsync=%s)", self._name, self._path, self._fsync)

    async def aclose(self) -> None:
        """Flush remaining lines and release the file. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        if self._flusher is not None:
            self._flusher.cancel()
            with suppress(asyncio.CancelledError):
                await self._flusher
            self._flusher = None
        async with self._lock:
            await self._flush_locked()
            if self._file is not None:
                await asyncio.to_thread(self._file.close)
                self._file = None
        _log.info("%s closed (%d lines written)", self._name, self._total_written)

    # -- writing ----------------------------------------------------------- #

    async def write_line(self, obj: Mapping[str, Any]) -> None:
        if self._closed:
            raise WriteError("write after close")
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        async with self._lock:
            self._buffer.append(line)
            if len(self._buffer) >= self._batch_size:
                await self._flush_locked()

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def _flush_loop(self) -> None:
        """Background timer: flush at least every ``flush_interval`` seconds."""
        try:
            while True:
                await asyncio.sleep(self._flush_interval)
                async with self._lock:
                    idle = self._clock.monotonic() - self._last_flush
                    if self._buffer and idle >= self._flush_interval:
                        await self._flush_locked()
        except asyncio.CancelledError:
            raise

    async def _flush_locked(self) -> None:
        """Persist the buffer. Caller must hold ``self._lock``."""
        if not self._buffer or self._file is None:
            return
        chunk = self._buffer
        self._buffer = []
        data = "".join(chunk)
        try:
            await asyncio.to_thread(self._write_sync, data)
        except OSError as exc:
            # Put the lines back so they are retried on the next flush.
            self._buffer[:0] = chunk
            raise WriteError(f"failed to write {len(chunk)} lines: {exc}") from exc
        self._total_written += len(chunk)
        self._last_flush = self._clock.monotonic()

    def _write_sync(self, data: str) -> None:
        file = self._file
        assert file is not None
        file.write(data)
        file.flush()
        if self._fsync:
            os.fsync(file.fileno())

    # -- crash recovery ---------------------------------------------------- #

    def _repair_trailing_partial(self) -> None:
        """Truncate a final line that lacks a newline (crash mid-append)."""
        if not self._path.exists() or self._path.stat().st_size == 0:
            return
        with open(self._path, "rb+") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) == b"\n":
                return  # last record is intact
            # Walk back to the previous newline and cut everything after it.
            size = self._path.stat().st_size
            handle.seek(0)
            data = handle.read()
            last_newline = data.rfind(b"\n")
            handle.truncate(last_newline + 1)
        dropped = size - (last_newline + 1)
        _log.warning(
            "repaired %s: dropped %d trailing bytes from an incomplete line",
            self._path,
            dropped,
        )

    # -- introspection ----------------------------------------------------- #

    @property
    def total_written(self) -> int:
        return self._total_written

    @property
    def buffered(self) -> int:
        return len(self._buffer)


class JsonlSink:
    """Discovery-catalogue sink: durably appends :class:`AircraftRecord` rows.

    A thin adapter over :class:`DurableJsonlWriter` that serialises each record
    with ``to_dict``. Satisfies the :class:`AircraftSink` Protocol.
    """

    def __init__(
        self,
        path: Path,
        *,
        batch_size: int = 50,
        flush_interval: float = 5.0,
        fsync: bool = True,
        clock: Clock | None = None,
    ) -> None:
        self._writer = DurableJsonlWriter(
            path,
            name="aircraft-catalogue",
            batch_size=batch_size,
            flush_interval=flush_interval,
            fsync=fsync,
            clock=clock,
        )

    async def __aenter__(self) -> JsonlSink:
        await self._writer.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._writer.aclose()

    async def open(self) -> None:
        await self._writer.open()

    async def write(self, record: AircraftRecord) -> None:
        await self._writer.write_line(record.to_dict())

    async def flush(self) -> None:
        await self._writer.flush()

    async def aclose(self) -> None:
        await self._writer.aclose()

    @property
    def total_written(self) -> int:
        return self._writer.total_written

    @property
    def buffered(self) -> int:
        return self._writer.buffered
