"""
MarineTraffic live adapter — a first-class maritime source fed by tailing the
JSONL that the standalone `mtscraper` project writes.

WHY A FILE TAIL, NOT A CODE IMPORT. mtscraper is a deliberately isolated
project: it shares no code with its siblings and imports nothing from them, so
that running or breaking one can never affect the other (its README states this
as a hard design goal). We honour that here. The ONLY coupling is the data
contract — mtscraper's `positions.jsonl` line shape — read through
`marinetraffic_translate`. scraplan imports none of mtscraper's Python, and
Playwright/Chromium never enters scraplan's process. mtscraper stays a black box
that produces a file; this adapter consumes it.

This mirrors the impedance role the AISStream adapter plays: AIS is a push
socket, MarineTraffic is a cyclic scraper writing a file — both normalize into
the shared `Fleet` and from there flow through the unchanged storage pattern
(atomic latest snapshot + JSONL archive + DB mirror). The one structural
difference from AIS is identity: MarineTraffic keys on its internal `ship_id`
(MMSI is usually absent), so events are fed via the Fleet's ship_id-keyed API.

Resilience (parity with the AISStream adapter):
  * survives a missing/rotated/truncated file — it waits for the file to appear
    and re-reads from the start if it shrinks (mtscraper restart / log rotation);
  * only ever processes COMPLETE lines (advances its offset to the last newline),
    so a line caught mid-write is picked up whole on the next poll;
  * every poll is stop-aware, so shutdown is prompt even between cycles.

The poll `sleep` and the file `reader` are injectable so tests drive the loop
deterministically against a temp file with no real timing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..fleet import Fleet
from .base import MaritimeAdapter
from .marinetraffic_translate import parse_mt_record

logger = logging.getLogger("maritime.marinetraffic")

DEFAULT_POLL_S = 5.0


class MarineTrafficAdapter(MaritimeAdapter):
    name = "marinetraffic"
    # MarineTraffic aggregates terrestrial + (some) satellite AIS but the tile
    # feed does not distinguish per vessel, so we record the conservative,
    # honest default. Absence of a vessel is NOT evidence of absence.
    data_origin = "terrestrial"

    def __init__(
        self,
        positions_path: Path | str,
        *,
        poll_interval: float = DEFAULT_POLL_S,
        from_start: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        reader: Callable[[Path, int], tuple[bytes, int]] | None = None,
    ) -> None:
        self._path = Path(positions_path)
        self._poll = max(0.0, poll_interval)
        # Resume cursor: byte offset of the last COMPLETE line consumed. Start at
        # EOF for a live tail (ignore history) unless from_start is requested
        # (e.g. a cold start that should also pick up the current file).
        self._offset = 0
        self._from_start = from_start
        self._sleep = sleep
        self._read = reader or _read_from
        self._stop = asyncio.Event()
        self._primed = False
        # health / observability counters
        self.lines_read = 0
        self.records_fed = 0
        self.parse_failures = 0
        self.reopens = 0
        self.last_ship_id: str | None = None
        self._last_size = 0

    # -- lifecycle --------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    async def run(self, fleet: Fleet) -> None:
        logger.info("MarineTraffic adapter tailing %s (poll=%.1fs)", self._path, self._poll)
        while not self._stop.is_set():
            try:
                self._drain(fleet)
            except Exception as exc:  # noqa: BLE001 — a bad cycle never kills the tail
                self.parse_failures += 1
                logger.warning("MarineTraffic tail cycle failed (continuing): %s", exc)
            if self._stop.is_set():
                break
            await self._wait(self._poll)
        logger.info("MarineTraffic adapter stopped")

    async def _wait(self, seconds: float) -> None:
        """Stop-aware sleep so a shutdown is honoured promptly between polls."""
        if self._sleep is not asyncio.sleep:
            await self._sleep(seconds)
            return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # -- tailing ----------------------------------------------------------

    def _drain(self, fleet: Fleet) -> None:
        """Read every complete line appended since the last offset and feed it."""
        if not self._path.exists():
            return
        size = self._path.stat().st_size

        # First sighting of the file: seek to EOF for a live tail (skip history),
        # unless from_start. Priming once — not on every reopen — so lines that
        # land between priming and the first real read are not lost.
        if not self._primed:
            self._offset = 0 if self._from_start else size
            self._primed = True
        # Rotation / truncation: mtscraper restarted or the log rolled. The file
        # is now shorter than where we were reading — restart from the top.
        elif size < self._offset:
            logger.info("MarineTraffic file shrank (%d < %d) — reopening from start",
                        size, self._offset)
            self._offset = 0
            self.reopens += 1

        self._last_size = size
        if size <= self._offset:
            return

        chunk, consumed = self._read(self._path, self._offset)
        if not chunk:
            return
        self._offset += consumed
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            self.lines_read += 1
            self._feed_line(line, fleet)

    def _feed_line(self, line: bytes, fleet: Fleet) -> None:
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            self.parse_failures += 1
            return
        parsed = parse_mt_record(rec)
        if parsed is None:
            return
        key = parsed.ship_id
        # Static first, so the position event carries the merged identity (the
        # DB mirror archives position events, and we want them identity-complete);
        # each MarineTraffic line is self-contained, unlike AIS's split messages.
        fleet.apply_static_keyed(key, parsed.static, self.name,
                                 mmsi=parsed.mmsi, ship_id=parsed.ship_id)
        fleet.apply_position_keyed(key, parsed.position, self.name,
                                   mmsi=parsed.mmsi, ship_id=parsed.ship_id)
        self.records_fed += 1
        self.last_ship_id = parsed.ship_id

    # -- observability ----------------------------------------------------

    def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_origin": self.data_origin,
            "path": str(self._path),
            "exists": self._path.exists(),
            "offset": self._offset,
            "file_size": self._last_size,
            "lines_read": self.lines_read,
            "records_fed": self.records_fed,
            "parse_failures": self.parse_failures,
            "reopens": self.reopens,
            "last_ship_id": self.last_ship_id,
            "note": "tail of mtscraper positions.jsonl; MarineTraffic ToS prohibits "
                    "scraping — source is provenance-tagged 'marinetraffic'",
        }


def _read_from(path: Path, offset: int) -> tuple[bytes, int]:
    """Read from `offset` to EOF, returning (bytes, bytes-up-to-last-newline).

    Only whole lines are consumed: the offset is advanced to just past the last
    '\\n', so a trailing partial line (a record still being written) is left for
    the next poll and never parsed half-formed."""
    with path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read()
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        return b"", 0            # no complete line yet
    return data[: last_nl + 1], last_nl + 1
