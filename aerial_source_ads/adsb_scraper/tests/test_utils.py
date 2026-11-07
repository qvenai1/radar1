"""Unit tests for cross-cutting helpers."""

from __future__ import annotations

import random

import pytest

from adsb_scraper.utils import BackoffPolicy, MovingAverage, coerce_float, coerce_str


def test_backoff_is_bounded_and_monotonic_base() -> None:
    policy = BackoffPolicy(base=2.0, max_wait=10.0, jitter=0.0, rng=random.Random(0))
    delays = [policy.delay_for(i) for i in range(6)]
    assert delays[0] == pytest.approx(1.0)
    assert delays[1] == pytest.approx(2.0)
    assert all(d <= 10.0 for d in delays)  # capped


def test_backoff_jitter_within_range() -> None:
    policy = BackoffPolicy(base=2.0, max_wait=100.0, jitter=1.0, rng=random.Random(1))
    for i in range(5):
        base = min(2.0**i, 100.0)
        d = policy.delay_for(i)
        assert base <= d <= base + 1.0


def test_backoff_rejects_bad_params() -> None:
    with pytest.raises(ValueError):
        BackoffPolicy(base=1.0)
    with pytest.raises(ValueError):
        BackoffPolicy(max_wait=0)


def test_moving_average() -> None:
    avg = MovingAverage(window=3)
    assert avg.value == 0.0
    for v in (3.0, 6.0, 9.0):
        avg.add(v)
    assert avg.value == pytest.approx(6.0)
    avg.add(12.0)  # evicts 3.0 -> window [6, 9, 12]
    assert avg.value == pytest.approx(9.0)
    assert avg.count == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        (2.5, 2.5),
        ("3.14", 3.14),
        ("  5 ", 5.0),
        (True, None),
        (None, None),
        ("abc", None),
        ("", None),
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_coerce_float(value: object, expected: float | None) -> None:
    assert coerce_float(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("  hi ", "hi"), ("", None), (42, "42"), (True, None), (None, None)],
)
def test_coerce_str(value: object, expected: str | None) -> None:
    assert coerce_str(value) == expected
