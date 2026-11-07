from __future__ import annotations

from mtscraper.metrics import MetricsTracker


def test_empty_snapshot() -> None:
    snap = MetricsTracker().snapshot()
    assert snap.total_cycles == 0
    assert snap.avg_cycle_seconds == 0.0
    assert snap.unique_vessels == 0


def test_records_and_averages() -> None:
    m = MetricsTracker()
    m.bind_unique_supplier(lambda: 42)
    m.record_cycle(seconds=2.0, tiles=10, vessels=100)
    m.record_cycle(seconds=4.0, tiles=20, vessels=200)
    m.record_browser_restart()
    m.record_page_reload()
    m.record_gap()
    m.record_failure()
    snap = m.snapshot()
    assert snap.total_cycles == 2
    assert snap.avg_cycle_seconds == 3.0
    assert snap.avg_tiles_per_cycle == 15.0
    assert snap.avg_vessels_per_cycle == 150.0
    assert snap.total_vessels_seen == 300
    assert snap.browser_restarts == 1
    assert snap.page_reloads == 1
    assert snap.collection_gaps == 1
    assert snap.total_failures == 1
    assert snap.unique_vessels == 42
    assert snap.uptime_seconds >= 0.0
