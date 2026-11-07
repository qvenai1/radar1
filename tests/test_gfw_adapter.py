"""
GFW adapter — dark-vessel (AIS gap) mapping tests.

Offline: a fake client returns an event in the REAL live-API shape (pinned from a
verified capture), so the mapping is tested without a token. The `enabled`/
disabled contract is checked too.
"""

from __future__ import annotations

import pytest

from maritime.adapters.gfw import GfwAdapter

# One gap event in the exact v3 shape returned by the live API.
LIVE_GAP = {
    "start": "2024-01-01 00:00:00+00:00",
    "end": "2024-01-03 00:00:00+00:00",
    "type": "gap",
    "position": {"lat": 26.19, "lon": 55.30},
    "gap": {"duration_hours": 48.0, "intentional_disabling": True,
            "distance_km": "123.4"},           # GFW sends some numerics as strings
    "vessel": {"id": "abc", "ssvid": "511100689", "name": "GHOST", "flag": "PLW",
               "type": "cargo"},
}


class _FakeEvent:
    def __init__(self, d): self._d = d
    def model_dump(self): return self._d


class _FakeResult:
    def __init__(self, evs): self._evs = evs
    def data(self): return self._evs


class _FakeEvents:
    def __init__(self, evs): self._evs = evs; self.calls = []
    async def get_all_events(self, **kw):
        self.calls.append(kw)
        return _FakeResult(self._evs)


class _FakeClient:
    def __init__(self, evs): self.events = _FakeEvents([_FakeEvent(e) for e in evs])


GULF = {"type": "Polygon", "coordinates": [[[48, 24], [57, 24], [57, 30], [48, 30], [48, 24]]]}


@pytest.mark.asyncio
async def test_find_ais_gaps_maps_live_shape():
    a = GfwAdapter(token="tok")
    a._client = _FakeClient([LIVE_GAP])           # inject fake client (no network)
    gaps = await a.find_ais_gaps(start="2024-01-01", end="2024-04-01", region=GULF)
    assert len(gaps) == 1
    g = gaps[0]
    assert g["mmsi"] == "511100689" and g["flag"] == "PLW" and g["name"] == "GHOST"
    assert g["duration_h"] == 48.0 and g["intentional"] is True
    assert g["lat"] == 26.19 and g["lon"] == 55.30
    assert g["distance_km"] == 123.4              # string coerced to float
    assert g["data_origin"] == "satellite"
    # the region was passed to the API as `geometry`, dataset is the gaps dataset
    call = a._client.events.calls[0]
    assert call["geometry"] == GULF
    assert call["datasets"] == ["public-global-gaps-events:latest"]


@pytest.mark.asyncio
async def test_disabled_without_token_is_honest(monkeypatch):
    monkeypatch.delenv("GFW_API_TOKEN", raising=False)   # ignore any real token in .env
    a = GfwAdapter(token="")
    assert a.enabled is False
    assert a.health()["status"].startswith("disabled")
    with pytest.raises(RuntimeError):
        await a.find_ais_gaps(start="2024-01-01", end="2024-02-01")


def test_health_active_with_token():
    a = GfwAdapter(token="tok")
    h = a.health()
    assert h["enabled"] is True and h["data_origin"] == "satellite"
    assert "active" in h["status"]
