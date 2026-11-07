"""Unit tests: archive.RawLog + emit_signal (raw-log path, no DB needed)."""

from __future__ import annotations

import json

import pytest

from archive import RawLog, emit_signal


def test_rawlog_append_roundtrip(tmp_path):
    log = RawLog(tmp_path / "sub" / "raw.jsonl")  # parent auto-created
    log.append({"a": 1, "name": "سفينة"})
    log.append_many([{"b": 2}, {"c": 3}])
    lines = (tmp_path / "sub" / "raw.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0]) == {"a": 1, "name": "سفينة"}   # non-ASCII preserved
    assert json.loads(lines[2]) == {"c": 3}


def test_rawlog_append_recreates_after_rotation(tmp_path):
    p = tmp_path / "raw.jsonl"
    log = RawLog(p)
    log.append({"x": 1})
    p.rename(tmp_path / "raw-old.jsonl")   # simulate rotation
    log.append({"x": 2})                    # must recreate the live file
    assert p.exists()
    assert json.loads(p.read_text().strip()) == {"x": 2}


@pytest.mark.asyncio
async def test_emit_signal_writes_raw_and_survives_no_db(tmp_path):
    log = RawLog(tmp_path / "signals.jsonl")
    # db=None must be safe (raw log is ground truth; DB is best-effort)
    await emit_signal(log, None, "aisstream", "ais_gap",
                      {"session_seconds": 12.5, "reason": "stream dropped"})
    ev = json.loads((tmp_path / "signals.jsonl").read_text().strip())
    assert ev["source"] == "aisstream" and ev["kind"] == "ais_gap"
    assert ev["detail"]["session_seconds"] == 12.5
    assert ev["ts"].endswith("+00:00")   # UTC ISO 8601


class _BoomDB:
    async def execute(self, *_a):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_emit_signal_swallows_db_errors(tmp_path):
    log = RawLog(tmp_path / "signals.jsonl")
    # a failing DB must NOT raise — the raw log still captures the signal
    await emit_signal(log, _BoomDB(), "celestrak", "throttle", {"http": 403})
    assert (tmp_path / "signals.jsonl").read_text().count("throttle") == 1
