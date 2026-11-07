"""Durable append-only JSONL persistence.

:class:`DurableJsonlWriter` is a small crash-resistant engine: every record is
serialised to a single line, flushed, and ``fsync``'d so a power loss can lose
at most the record currently mid-write (and a truncated trailing line is
repaired on the next open). Two thin sinks compose it:

* :class:`CatalogueSink` — SHIP_ID-deduplicated discovery catalogue.
* :class:`PositionSink` — the position time-series, written on change plus a
  periodic heartbeat.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from . import constants as C
from .logger import get_logger
from .models import VesselRecord

_log = get_logger("persistence")


class DurableJsonlWriter:
    """Append JSON objects one-per-line with fsync durability."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._repair_trailing_partial_line()
        # Line-buffered append; we still fsync explicitly for durability.
        self._fh = self._path.open("a", encoding="utf-8")

    def write(self, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        self._fh.write(line + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        finally:
            self._fh.close()

    def __enter__(self) -> DurableJsonlWriter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _repair_trailing_partial_line(self) -> None:
        """If a prior crash left a truncated final line, drop it."""
        if not self._path.exists() or self._path.stat().st_size == 0:
            return
        with self._path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) == b"\n":
                return  # last line is complete
        # Rewrite keeping only complete lines.
        with self._path.open("rb") as fh:
            data = fh.read()
        last_nl = data.rfind(b"\n")
        repaired = data[: last_nl + 1] if last_nl >= 0 else b""
        with self._path.open("wb") as fh:
            fh.write(repaired)
        _log.warning("repaired truncated trailing line in %s", self._path.name)


class CatalogueSink:
    """SHIP_ID-deduplicated catalogue of distinct vessels ever seen.

    Known ids are loaded from the existing file on open, so restarts do not
    re-emit vessels already catalogued.
    """

    def __init__(self, path: Path, known_ids: set[str] | None = None) -> None:
        self._writer = DurableJsonlWriter(path)
        # A caller (e.g. StateRecovery) may supply an already-loaded id set from
        # a fast cache; otherwise scan the archive to rebuild it.
        self._known: set[str] = (
            set(known_ids) if known_ids is not None else self._load_known_ids(path)
        )

    @property
    def known_count(self) -> int:
        return len(self._known)

    def is_known(self, ship_id: str) -> bool:
        return ship_id in self._known

    def known_ids(self) -> set[str]:
        return set(self._known)

    def add(self, record: VesselRecord) -> bool:
        """Persist a vessel if unseen. Returns True if newly added."""
        if record.ship_id in self._known:
            return False
        self._writer.write(record.to_dict())
        self._known.add(record.ship_id)
        return True

    def close(self) -> None:
        self._writer.close()

    @staticmethod
    def _load_known_ids(path: Path) -> set[str]:
        known: set[str] = set()
        if not path.exists():
            return known
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ship_id = json.loads(line).get("ship_id")
                except json.JSONDecodeError:
                    continue
                if isinstance(ship_id, str):
                    known.add(ship_id)
        _log.info("loaded %d known vessel ids from %s", len(known), path.name)
        return known


#: Observation fields that constitute a genuinely new report. ``elapsed_min`` is
#: deliberately absent: it is the *age* of MarineTraffic's fix and therefore ticks
#: upward on every poll of an otherwise unchanged record, which would make change
#: detection match nothing. Its information is preserved by the heartbeat, which
#: re-writes an idle vessel periodically with the age it had at that moment.
_REPORT_FIELDS: Final = (
    "latitude",
    "longitude",
    "speed_knots",
    "course",
    "heading",
    "destination",
    "shipname",
    "shiptype",
    "flag",
)


class PositionSink:
    """Position time-series, written when an observation carries something new.

    The upstream feed refreshes a moving vessel roughly every 9 minutes while the
    scraper polls every ~2, so appending unconditionally stores each distinct fix
    about 6.5x over (measured: 15.3% of a 300k-record sample were distinct
    positions). Those repeats restate what the previous line already says.

    Two rules keep the series faithful while dropping that redundancy:

    * a record is written whenever any field in :data:`_REPORT_FIELDS` changes, or
      the validation verdict does — a vessel going stale or suspect is itself an
      event worth a line;
    * an unchanged vessel is still written every ``heartbeat_s`` so the archive
      keeps positive evidence that it was present and reporting. Without it a
      vessel anchored for a week would leave one line, and nothing would separate
      "still moored here" from "left the area days ago".

    Suppression state is in memory only, so the first cycle after a restart
    re-writes every vessel once. That is one cycle of redundancy per restart —
    deliberately cheaper than reconstructing the state from a multi-GB archive.
    """

    def __init__(self, path: Path, heartbeat_s: float = C.POSITION_HEARTBEAT_S) -> None:
        self._writer = DurableJsonlWriter(path)
        self._heartbeat_s = heartbeat_s
        self._last: dict[str, tuple[tuple[Any, ...], float]] = {}
        self._written = 0
        self._suppressed = 0

    @property
    def written_count(self) -> int:
        return self._written

    @property
    def suppressed_count(self) -> int:
        return self._suppressed

    def add(self, record: VesselRecord) -> bool:
        """Persist the observation unless it merely restates the last one.

        Returns True if a line was written.
        """
        signature = self._signature(record)
        seen_at = self._epoch(record.fetched_at)
        previous = self._last.get(record.ship_id)

        if previous is not None and seen_at is not None:
            last_signature, last_seen_at = previous
            unchanged = signature == last_signature
            within_heartbeat = (seen_at - last_seen_at) < self._heartbeat_s
            if unchanged and within_heartbeat:
                self._suppressed += 1
                return False

        self._writer.write(record.to_dict())
        # An unparseable timestamp cannot anchor a heartbeat window, so such a
        # record is written and left out of the suppression state entirely.
        if seen_at is not None:
            self._last[record.ship_id] = (signature, seen_at)
        self._written += 1
        return True

    def close(self) -> None:
        self._writer.close()

    @staticmethod
    def _signature(record: VesselRecord) -> tuple[Any, ...]:
        data = record.data
        return (
            *(data.get(field) for field in _REPORT_FIELDS),
            record.position_quality,
            record.flags,
            record.in_area,
        )

    @staticmethod
    def _epoch(fetched_at: str) -> float | None:
        try:
            return datetime.fromisoformat(fetched_at).timestamp()
        except (TypeError, ValueError):
            return None
