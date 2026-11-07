"""Unit tests: backend.api time parsing + range caps (the 422 guardrails)."""

import pytest
from fastapi import HTTPException

from backend.api import parse_time, resolve_range


def test_epoch_seconds_accepted():
    assert parse_time("1783509251", "from") == 1783509251


def test_iso_utc_accepted():
    assert parse_time("2026-07-08T11:14:11+00:00", "from") == 1783509251
    assert parse_time("2026-07-08T11:14:11Z", "from") == 1783509251


def test_naive_iso_treated_as_utc():
    assert parse_time("2026-07-08T11:14:11", "from") == 1783509251


def test_garbage_rejected_422():
    with pytest.raises(HTTPException) as e:
        parse_time("not-a-time", "from")
    assert e.value.status_code == 422


def test_none_passthrough():
    assert parse_time(None, "from") is None


def test_range_inverted_rejected():
    with pytest.raises(HTTPException) as e:
        resolve_range("2000", "1000", default_span=3600, max_span=86400)
    assert e.value.status_code == 422


def test_range_cap_enforced():
    with pytest.raises(HTTPException) as e:
        resolve_range("0", str(10 * 86400), default_span=3600, max_span=7 * 86400)
    assert e.value.status_code == 422


def test_defaults_fill_window():
    f, t = resolve_range(None, "10000", default_span=3600, max_span=86400)
    assert (f, t) == (6400, 10000)
