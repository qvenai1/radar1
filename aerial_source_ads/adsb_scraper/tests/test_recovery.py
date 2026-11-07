"""Unit tests for the pure recovery policy and resume state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adsb_scraper.config import RecoveryConfig
from adsb_scraper.recovery import RecoveryAction, RecoveryPolicy, StateRecovery


def _policy() -> RecoveryPolicy:
    return RecoveryPolicy(
        RecoveryConfig(
            empty_reload_threshold=3,
            empty_restart_threshold=6,
            parser_failure_threshold=2,
            max_restart_failures=3,
        )
    )


def test_empty_cycle_escalation() -> None:
    p = _policy()
    actions = [p.on_empty_cycle() for _ in range(6)]
    assert actions == [
        RecoveryAction.NONE,
        RecoveryAction.NONE,
        RecoveryAction.RELOAD,
        RecoveryAction.NONE,
        RecoveryAction.NONE,
        RecoveryAction.RESTART,
    ]
    assert p.consecutive_empty == 0  # reset after restart


def test_productive_cycle_resets() -> None:
    p = _policy()
    p.on_empty_cycle()
    p.on_empty_cycle()
    p.on_productive_cycle()
    assert p.consecutive_empty == 0


def test_parser_failure_escalates_on_threshold() -> None:
    p = _policy()
    assert p.on_parser_failure() == RecoveryAction.SAVE_PAYLOAD
    assert p.on_parser_failure() == RecoveryAction.RELOAD
    assert p.consecutive_parser_failures == 0


def test_restart_failure_aborts() -> None:
    p = _policy()
    assert p.on_restart_failed() == RecoveryAction.NONE
    assert p.on_restart_failed() == RecoveryAction.NONE
    assert p.on_restart_failed() == RecoveryAction.ABORT


def test_restart_success_resets_failures() -> None:
    p = _policy()
    p.on_restart_failed()
    p.on_restart_succeeded()
    assert p.consecutive_restart_failures == 0


@pytest.mark.asyncio
async def test_state_recovery_rebuilds_from_results(tmp_path: Path) -> None:
    results = tmp_path / "aircraft.jsonl"
    cache = tmp_path / ".cache.txt"
    lines = [
        json.dumps({"icao": "aaa", "data": {}}),
        json.dumps({"icao": "bbb", "data": {}}),
        "corrupt-line-not-json",
        json.dumps({"icao": "aaa", "data": {}}),  # duplicate
    ]
    results.write_text("\n".join(lines) + "\n", encoding="utf-8")
    state = StateRecovery(results, cache)
    known = await state.load_completed()
    assert known == {"aaa", "bbb"}
    assert cache.exists()  # fast-path cache rewritten


@pytest.mark.asyncio
async def test_state_recovery_prefers_fresh_cache(tmp_path: Path) -> None:
    results = tmp_path / "aircraft.jsonl"
    cache = tmp_path / ".cache.txt"
    results.write_text(json.dumps({"icao": "zzz", "data": {}}) + "\n", encoding="utf-8")
    cache.write_text("aaa\nbbb\n", encoding="utf-8")
    import os
    import time

    future = time.time() + 100
    os.utime(cache, (future, future))  # cache newer than results
    state = StateRecovery(results, cache)
    assert await state.load_completed() == {"aaa", "bbb"}


@pytest.mark.asyncio
async def test_append_to_cache_is_idempotent(tmp_path: Path) -> None:
    cache = tmp_path / ".cache.txt"
    state = StateRecovery(tmp_path / "r.jsonl", cache)
    await state.append_to_cache({"aaa", "bbb"})
    await state.append_to_cache({"bbb", "ccc"})
    stored = {line for line in cache.read_text().splitlines() if line}
    assert stored == {"aaa", "bbb", "ccc"}
