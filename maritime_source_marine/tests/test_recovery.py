from __future__ import annotations

import json
from pathlib import Path

from mtscraper.config import RecoveryConfig
from mtscraper.recovery import RecoveryAction, RecoveryPolicy, StateRecovery


def _policy() -> RecoveryPolicy:
    return RecoveryPolicy(RecoveryConfig(empty_reload_threshold=3, empty_restart_threshold=5))


def test_empty_escalates_reload_then_restart() -> None:
    p = _policy()
    assert p.on_empty_cycle() is RecoveryAction.NONE  # 1
    assert p.on_empty_cycle() is RecoveryAction.NONE  # 2
    assert p.on_empty_cycle() is RecoveryAction.RELOAD  # 3
    assert p.on_empty_cycle() is RecoveryAction.NONE  # 4
    assert p.on_empty_cycle() is RecoveryAction.RESTART  # 5
    # restart resets the empty counter
    assert p.consecutive_empty == 0


def test_productive_cycle_clears_counters() -> None:
    p = _policy()
    p.on_empty_cycle()
    p.on_productive_cycle()
    assert p.consecutive_empty == 0


def test_parser_failure_saves_then_reloads() -> None:
    p = RecoveryPolicy(RecoveryConfig(parser_failure_threshold=2))
    assert p.on_parser_failure() is RecoveryAction.SAVE_PAYLOAD
    assert p.on_parser_failure() is RecoveryAction.RELOAD


def test_restart_failures_abort() -> None:
    p = RecoveryPolicy(RecoveryConfig(max_restart_failures=2))
    assert p.on_restart_failed() is RecoveryAction.NONE
    assert p.on_restart_failed() is RecoveryAction.ABORT


def test_state_recovery_rebuilds_from_results(tmp_path: Path) -> None:
    results = tmp_path / "vessels.jsonl"
    cache = tmp_path / ".cache.txt"
    results.write_text(
        json.dumps({"ship_id": "1"}) + "\n" + json.dumps({"ship_id": "2"}) + "\n"
    )
    import asyncio

    sr = StateRecovery(results, cache)
    known = asyncio.run(sr.load_completed())
    assert known == {"1", "2"}
    # a cache file is written for the fast path next time
    assert cache.exists()


def test_state_recovery_append_to_cache(tmp_path: Path) -> None:
    import asyncio

    cache = tmp_path / ".cache.txt"
    sr = StateRecovery(tmp_path / "vessels.jsonl", cache)
    asyncio.run(sr.append_to_cache({"10", "20"}))
    assert set(cache.read_text().split()) == {"10", "20"}
