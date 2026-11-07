"""Cross-cutting helpers with no business logic of their own.

Everything here is deliberately tiny, pure(ish), and independently testable:

* :class:`Clock` / :class:`SystemClock` — an injectable time source so that
  flush timers and metrics can be tested without ``sleep``.
* :class:`BackoffPolicy` — exponential backoff with jitter.
* :class:`MovingAverage` — O(1) rolling mean for observability.
* :class:`MemoryProbe` — resident-set-size sampling with graceful fallbacks.
* small, defensive value-coercion helpers used when validating remote data.
"""

from __future__ import annotations

import os
import random
from collections import deque
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


@runtime_checkable
class Clock(Protocol):
    """A source of time.

    Injected everywhere instead of calling :func:`time.monotonic` /
    :func:`datetime.now` directly, so tests can supply a deterministic clock.
    """

    def monotonic(self) -> float:
        """Monotonic seconds; only differences are meaningful."""

    def now(self) -> datetime:
        """Timezone-aware wall-clock time (UTC)."""


class SystemClock:
    """The real clock, backed by the standard library."""

    __slots__ = ()

    def monotonic(self) -> float:
        import time

        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Backoff
# --------------------------------------------------------------------------- #


class BackoffPolicy:
    """Exponential backoff with additive jitter, capped at ``max_wait``.

    ``rng`` is injectable so the jitter is deterministic under test.
    """

    __slots__ = ("_base", "_jitter", "_max_wait", "_rng")

    def __init__(
        self,
        base: float = 2.0,
        max_wait: float = 30.0,
        jitter: float = 1.0,
        rng: random.Random | None = None,
    ) -> None:
        if base <= 1.0:
            raise ValueError("base must be > 1.0")
        if max_wait <= 0:
            raise ValueError("max_wait must be > 0")
        if jitter < 0:
            raise ValueError("jitter must be >= 0")
        self._base = base
        self._max_wait = max_wait
        self._jitter = jitter
        self._rng = rng or random.Random()

    def delay_for(self, attempt: int) -> float:
        """Return the delay (seconds) for a 0-based ``attempt`` index."""
        if attempt < 0:
            raise ValueError("attempt must be >= 0")
        exponential = min(self._base**attempt, self._max_wait)
        return exponential + self._rng.uniform(0.0, self._jitter)


# --------------------------------------------------------------------------- #
# Rolling statistics
# --------------------------------------------------------------------------- #


class MovingAverage:
    """Fixed-window rolling mean, computed incrementally in O(1) per sample."""

    __slots__ = ("_samples", "_sum", "_window")

    def __init__(self, window: int = 128) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self._window = window
        self._samples: deque[float] = deque(maxlen=window)
        self._sum = 0.0

    def add(self, value: float) -> None:
        if len(self._samples) == self._window:
            self._sum -= self._samples[0]
        self._samples.append(value)
        self._sum += value

    @property
    def value(self) -> float:
        return self._sum / len(self._samples) if self._samples else 0.0

    @property
    def count(self) -> int:
        return len(self._samples)


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #


class MemoryProbe:
    """Samples the process resident-set size in megabytes.

    Prefers ``psutil`` when installed, falls back to Linux ``/proc/self/statm``,
    and finally to :mod:`resource`. Never raises: observability must not be able
    to take the collector down, so an unknown value is reported as ``None``.
    """

    __slots__ = ("_page_size", "_psutil_process")

    def __init__(self) -> None:
        self._page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
        self._psutil_process: object | None = None
        try:  # pragma: no cover - depends on optional dependency
            import psutil

            self._psutil_process = psutil.Process()
        except Exception:  # noqa: BLE001 - optional dependency, any failure is fine
            self._psutil_process = None

    def rss_mb(self) -> float | None:
        proc = self._psutil_process
        if proc is not None:  # pragma: no cover - optional path
            try:
                return float(proc.memory_info().rss) / (1024 * 1024)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        try:
            with open("/proc/self/statm", encoding="ascii") as handle:
                resident_pages = int(handle.read().split()[1])
            return resident_pages * self._page_size / (1024 * 1024)
        except (OSError, ValueError, IndexError):
            pass
        try:
            import resource

            max_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return float(max_rss_kb) / 1024
        except Exception:  # noqa: BLE001 - last resort
            return None


# --------------------------------------------------------------------------- #
# Defensive coercion (remote payloads are never trusted)
# --------------------------------------------------------------------------- #


def coerce_float(value: object) -> float | None:
    """Best-effort float conversion. Returns ``None`` for anything unusable."""
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            result = float(stripped)
        except ValueError:
            return None
    else:
        return None
    # Reject NaN / +-inf: they poison comparisons and JSON round-trips.
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def coerce_str(value: object) -> str | None:
    """Return a stripped, non-empty string, or ``None``."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None
