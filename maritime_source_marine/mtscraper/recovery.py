"""Resume support and automated self-healing.

Two concerns, both about surviving unattended for weeks:

* :class:`StateRecovery` restores the set of already-seen SHIP_IDs on startup so
  a restart continues where it left off instead of re-cataloguing everything.
* :class:`RecoveryPolicy` is the *pure* decision core for self-healing. It holds
  the failure counters and, given an event, returns a :class:`RecoveryAction`
  for the orchestrator to execute. Being side-effect free means the whole
  escalation ladder (empty -> reload -> restart -> abort) is unit-testable
  without a browser, a clock, or a filesystem.
"""

from __future__ import annotations

import asyncio
import enum
import json
from collections.abc import Set as AbstractSet
from pathlib import Path

from .config import RecoveryConfig
from .logger import get_logger

_log = get_logger("recovery")


class RecoveryAction(enum.Enum):
    """What the orchestrator should do in response to a recovery event."""

    NONE = "none"
    RELOAD = "reload"
    RESTART = "restart"
    SAVE_PAYLOAD = "save_payload"
    ABORT = "abort"


class RecoveryPolicy:
    """Failure counters and the escalation rules over them (no side effects)."""

    __slots__ = ("_cfg", "_empty", "_parser_failures", "_restart_failures")

    def __init__(self, cfg: RecoveryConfig) -> None:
        self._cfg = cfg
        self._empty = 0
        self._parser_failures = 0
        self._restart_failures = 0

    @property
    def consecutive_empty(self) -> int:
        return self._empty

    @property
    def consecutive_parser_failures(self) -> int:
        return self._parser_failures

    @property
    def consecutive_restart_failures(self) -> int:
        return self._restart_failures

    def on_productive_cycle(self) -> None:
        """A cycle produced data: clear the transient failure counters."""
        self._empty = 0
        self._parser_failures = 0

    def on_empty_cycle(self) -> RecoveryAction:
        """A cycle produced no vessels. Escalate reload -> restart on thresholds."""
        self._empty += 1
        if self._empty >= self._cfg.empty_restart_threshold:
            self._empty = 0  # a restart is a clean slate
            _log.warning("empty-cycle restart threshold reached; requesting RESTART")
            return RecoveryAction.RESTART
        if self._empty == self._cfg.empty_reload_threshold:
            _log.warning("empty-cycle reload threshold reached; requesting RELOAD")
            return RecoveryAction.RELOAD
        return RecoveryAction.NONE

    def on_parser_failure(self) -> RecoveryAction:
        """A payload could not be parsed. Always archive; escalate on repeats."""
        self._parser_failures += 1
        if self._parser_failures >= self._cfg.parser_failure_threshold:
            self._parser_failures = 0
            _log.warning("parser-failure threshold reached; requesting RELOAD")
            return RecoveryAction.RELOAD
        return RecoveryAction.SAVE_PAYLOAD

    def on_restart_succeeded(self) -> None:
        self._restart_failures = 0

    def on_restart_failed(self) -> RecoveryAction:
        """Too many failed restarts means the environment is broken: abort."""
        self._restart_failures += 1
        if self._restart_failures >= self._cfg.max_restart_failures:
            _log.error("restart failure limit reached; requesting ABORT")
            return RecoveryAction.ABORT
        return RecoveryAction.NONE


class StateRecovery:
    """Loads and persists the set of SHIP_IDs already collected."""

    __slots__ = ("_cache_file", "_results_file")

    def __init__(self, results_file: Path, cache_file: Path) -> None:
        self._results_file = results_file
        self._cache_file = cache_file

    async def load_completed(self) -> set[str]:
        """Return known SHIP_IDs, preferring the fast cache when it is current."""
        return await asyncio.to_thread(self._load_completed_sync)

    def _load_completed_sync(self) -> set[str]:
        cache, results = self._cache_file, self._results_file
        if cache.exists() and (
            not results.exists() or cache.stat().st_mtime >= results.stat().st_mtime
        ):
            try:
                text = cache.read_text(encoding="utf-8")
                loaded = {line.strip() for line in text.splitlines() if line.strip()}
                _log.info("resume: loaded %d ship ids from cache", len(loaded))
                return loaded
            except OSError as exc:
                _log.warning("resume: cache unreadable (%s); rebuilding from results", exc)

        if not results.exists():
            return set()

        done: set[str] = set()
        try:
            with results.open(encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    ship_id = obj.get("ship_id") if isinstance(obj, dict) else None
                    if isinstance(ship_id, str) and ship_id:
                        done.add(ship_id)
        except OSError as exc:
            _log.warning("resume: could not read results file: %s", exc)
            return done
        _log.info("resume: rebuilt %d ship ids from results", len(done))
        self._write_cache_sync(done)
        return done

    async def append_to_cache(self, new_ids: AbstractSet[str]) -> None:
        if not new_ids:
            return
        await asyncio.to_thread(self._append_to_cache_sync, new_ids)

    def _append_to_cache_sync(self, new_ids: AbstractSet[str]) -> None:
        existing: set[str] = set()
        if self._cache_file.exists():
            try:
                text = self._cache_file.read_text(encoding="utf-8")
                existing = {line.strip() for line in text.splitlines() if line.strip()}
            except OSError as exc:
                _log.debug("cache read failed before append: %s", exc)
        merged = existing | set(new_ids)
        if merged != existing:
            self._write_cache_sync(merged)

    def _write_cache_sync(self, ids: set[str]) -> None:
        tmp = self._cache_file.with_suffix(self._cache_file.suffix + ".tmp")
        try:
            tmp.write_text("\n".join(sorted(ids)), encoding="utf-8")
            tmp.replace(self._cache_file)  # atomic swap
        except OSError as exc:
            _log.warning("could not persist ship-id cache: %s", exc)
