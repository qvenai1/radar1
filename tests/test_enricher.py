"""Enricher.get_aircraft cache contract — the three branches the review found untested.

Offline detonation (arch review finding 10): the upstream seam is _fetch_aircraft,
the storage seam is a fake pool. No HTTP, no DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.enrich import Enricher

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


class FakePool:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    async def fetchrow(self, sql, *args):
        return self.row

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def _row(found=True, age=timedelta(0), **extra):
    r = {"icao24": "4009f9", "found": found, "registration": "G-TEST",
         "fetched_at": NOW() - age}
    r.update(extra)
    return r


@pytest.mark.asyncio
async def test_fresh_cache_hit_never_calls_upstream(monkeypatch):
    e = Enricher()
    async def boom(icao24):
        raise AssertionError("upstream must not be called on a fresh cache hit")
    monkeypatch.setattr(e, "_fetch_aircraft", boom)
    pool = FakePool(row=_row(age=timedelta(days=1)))          # < 30d TTL → fresh
    out = await e.get_aircraft(pool, "4009F9 ")
    assert out["registration"] == "G-TEST" and pool.executed == []


@pytest.mark.asyncio
async def test_stale_row_beats_unreachable_upstream(monkeypatch):
    e = Enricher()
    async def unreachable(icao24):
        return None                                            # both upstreams down
    monkeypatch.setattr(e, "_fetch_aircraft", unreachable)
    pool = FakePool(row=_row(age=timedelta(days=90)))          # stale
    out = await e.get_aircraft(pool, "4009f9")
    assert out["registration"] == "G-TEST", "stale beats nothing"
    assert pool.executed == [], "an unreachable upstream must not overwrite the cache"


@pytest.mark.asyncio
async def test_no_row_and_unreachable_degrades_to_not_found(monkeypatch):
    e = Enricher()
    async def unreachable(icao24):
        return None
    monkeypatch.setattr(e, "_fetch_aircraft", unreachable)
    out = await e.get_aircraft(FakePool(row=None), "4009f9")
    assert out == {"icao24": "4009f9", "found": False}


@pytest.mark.asyncio
async def test_fetched_result_is_upserted_and_returned(monkeypatch):
    e = Enricher()
    async def fetched(icao24):
        return {"found": True, "registration": "G-NEW", "photo_thumb": "http://t"}
    monkeypatch.setattr(e, "_fetch_aircraft", fetched)
    pool = FakePool(row=_row(age=timedelta(days=90)))
    out = await e.get_aircraft(pool, "4009f9")
    assert out["registration"] == "G-NEW" and out["icao24"] == "4009f9"
    assert len(pool.executed) == 1 and "INSERT INTO aircraft_info" in pool.executed[0][0]
