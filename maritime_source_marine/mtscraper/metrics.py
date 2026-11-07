"""Lifetime metrics tracking.

:class:`MetricsTracker` accumulates counters across the whole run and produces
an immutable :class:`~mtscraper.models.MetricsSnapshot` on demand. The number of
unique vessels is not tracked here — it is owned by the accumulator — so the
tracker takes a *supplier* callable it can poll, keeping a single source of truth.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .models import MetricsSnapshot


class MetricsTracker:
    """Mutable counters -> immutable snapshots."""

    __slots__ = (
        "_browser_restarts",
        "_cycle_seconds",
        "_failures",
        "_gaps",
        "_identities",
        "_page_reloads",
        "_start",
        "_tiles",
        "_unique_supplier",
        "_vessels",
    )

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._cycle_seconds: list[float] = []
        self._tiles: list[int] = []
        self._vessels: list[int] = []
        self._failures = 0
        self._browser_restarts = 0
        self._page_reloads = 0
        self._gaps = 0
        self._identities = 0
        self._unique_supplier: Callable[[], int] = lambda: 0

    def bind_unique_supplier(self, supplier: Callable[[], int]) -> None:
        self._unique_supplier = supplier

    def record_identities(self, n: int) -> None:
        self._identities += n

    def record_cycle(self, *, seconds: float, tiles: int, vessels: int) -> None:
        self._cycle_seconds.append(seconds)
        self._tiles.append(tiles)
        self._vessels.append(vessels)

    def record_failure(self) -> None:
        self._failures += 1

    def record_browser_restart(self) -> None:
        self._browser_restarts += 1

    def record_page_reload(self) -> None:
        self._page_reloads += 1

    def record_gap(self) -> None:
        self._gaps += 1

    def snapshot(self) -> MetricsSnapshot:
        cycles = len(self._cycle_seconds)
        return MetricsSnapshot(
            uptime_seconds=time.monotonic() - self._start,
            total_cycles=cycles,
            total_failures=self._failures,
            browser_restarts=self._browser_restarts,
            page_reloads=self._page_reloads,
            collection_gaps=self._gaps,
            total_vessels_seen=sum(self._vessels),
            unique_vessels=self._unique_supplier(),
            identities_resolved=self._identities,
            avg_cycle_seconds=_mean(self._cycle_seconds),
            avg_tiles_per_cycle=_mean(self._tiles),
            avg_vessels_per_cycle=_mean(self._vessels),
        )


def _mean(values: list[float] | list[int]) -> float:
    return sum(values) / len(values) if values else 0.0
