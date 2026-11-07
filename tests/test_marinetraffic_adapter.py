"""
MarineTraffic tail-adapter tests — driven against a real temp file with an
injected sleep, so no real timing and no mtscraper/Playwright is touched.

The adapter's contract: tail mtscraper's positions.jsonl, feed complete lines
into the shared Fleet under the ship_id identity, and survive a missing /
rotated / truncated / half-written file.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from maritime.adapters.marinetraffic import MarineTrafficAdapter
from maritime.fleet import Fleet


def _line(ship_id: str, lat: float, lon: float, **data) -> str:
    d = {"ship_id": ship_id, "mmsi": None, "latitude": lat, "longitude": lon,
         "speed_knots": 5.0, "course": 90.0, "heading": 90.0,
         "shipname": f"SHIP {ship_id}", "shiptype": "7", "flag": "MT",
         "destination": "PORT", "length_m": 100.0, "width_m": 20.0,
         "elapsed_min": 1.0}
    d.update(data)
    return json.dumps({"ship_id": ship_id, "fetched_at": "2026-07-21T11:07:54+00:00",
                       "source": "interception", "in_area": True, "data": d,
                       "distance_km": 10.0})


def _adapter(path, **kw):
    return MarineTrafficAdapter(path, **kw)


# --------------------------- tailing ---------------------------

def test_tails_existing_lines_from_start(tmp_path):
    f = tmp_path / "positions.jsonl"
    f.write_text(_line("1", 34.5, 35.7) + "\n" + _line("2", 34.6, 35.8) + "\n")
    fleet = Fleet()
    a = _adapter(f, from_start=True)
    a._drain(fleet)
    assert a.records_fed == 2 and a.lines_read == 2
    snap = {v["ship_id"]: v for v in fleet.snapshot(max_age_s=10**12)}
    assert set(snap) == {"1", "2"}
    assert snap["1"]["source"] == "marinetraffic"
    assert snap["1"]["mmsi"] is None                 # ship_id-keyed, no MMSI
    assert snap["1"]["name"] == "SHIP 1" and snap["1"]["ship_type"] == "cargo"


def test_live_tail_skips_history(tmp_path):
    f = tmp_path / "positions.jsonl"
    f.write_text(_line("1", 34.5, 35.7) + "\n")
    fleet = Fleet()
    a = _adapter(f, from_start=False)
    a._drain(fleet)                                   # primes to EOF — history skipped
    assert a.records_fed == 0
    with f.open("a") as fh:
        fh.write(_line("2", 34.6, 35.8) + "\n")
    a._drain(fleet)                                   # only the newly-appended line
    assert a.records_fed == 1 and a.last_ship_id == "2"


def test_partial_trailing_line_held_until_complete(tmp_path):
    f = tmp_path / "positions.jsonl"
    full = _line("1", 34.5, 35.7)
    f.write_text(full + "\n" + full[:20])             # second line half-written
    fleet = Fleet()
    a = _adapter(f, from_start=True)
    a._drain(fleet)
    assert a.records_fed == 1                          # partial line NOT parsed
    with f.open("a") as fh:                            # finish the second line
        fh.write(full[20:] + "\n")
    a._drain(fleet)
    assert a.records_fed == 2


def test_rotation_or_truncation_reopens_from_start(tmp_path):
    f = tmp_path / "positions.jsonl"
    f.write_text(_line("1", 34.5, 35.7) + "\n" + _line("2", 34.6, 35.8) + "\n")
    fleet = Fleet()
    a = _adapter(f, from_start=True)
    a._drain(fleet)
    assert a.records_fed == 2
    # mtscraper restarts → file replaced with a shorter one
    f.write_text(_line("3", 34.7, 35.9) + "\n")
    a._drain(fleet)
    assert a.reopens == 1 and a.last_ship_id == "3" and a.records_fed == 3


def test_bad_json_counted_and_skipped(tmp_path):
    f = tmp_path / "positions.jsonl"
    f.write_text("{not json\n" + _line("1", 34.5, 35.7) + "\n")
    fleet = Fleet()
    a = _adapter(f, from_start=True)
    a._drain(fleet)
    assert a.records_fed == 1 and a.parse_failures == 1


def test_record_without_position_skipped(tmp_path):
    f = tmp_path / "positions.jsonl"
    f.write_text(_line("1", 34.5, 35.7, latitude=None, longitude=None) + "\n")
    fleet = Fleet()
    a = _adapter(f, from_start=True)
    a._drain(fleet)
    assert a.lines_read == 1 and a.records_fed == 0   # read but unusable


def test_missing_file_is_noop(tmp_path):
    a = _adapter(tmp_path / "nope.jsonl", from_start=True)
    a._drain(Fleet())                                  # no error
    assert a.health()["exists"] is False


def test_repeated_identical_line_is_no_new_signal(tmp_path):
    f = tmp_path / "positions.jsonl"
    f.write_text(_line("1", 34.5, 35.7) + "\n")
    fleet = Fleet()
    a = _adapter(f, from_start=True)
    a._drain(fleet)
    fleet.drain_events()                               # clear first sighting
    with f.open("a") as fh:                            # exact same position again
        fh.write(_line("1", 34.5, 35.7) + "\n")
    a._drain(fleet)
    assert fleet.drain_events() == []                  # carried no new information


# --------------------------- run loop ---------------------------

@pytest.mark.asyncio
async def test_run_feeds_then_stops(tmp_path):
    f = tmp_path / "positions.jsonl"
    f.write_text(_line("1", 34.5, 35.7) + "\n")

    async def fast_sleep(_s):
        await asyncio.sleep(0)

    a = _adapter(f, from_start=True, poll_interval=0.001, sleep=fast_sleep)
    fleet = Fleet()
    task = asyncio.create_task(a.run(fleet))
    await asyncio.sleep(0.02)
    with f.open("a") as fh:
        fh.write(_line("2", 34.6, 35.8) + "\n")
    await asyncio.sleep(0.02)
    a.stop()
    await asyncio.wait_for(task, timeout=2)
    assert a.records_fed == 2
    assert {v["ship_id"] for v in fleet.snapshot(max_age_s=10**12)} == {"1", "2"}


def test_multiple_adapters_feed_one_fleet(tmp_path):
    # Several regional mtscraper outputs (E. Med + Persian Gulf + Red Sea), each
    # its own file, all tailed into ONE Fleet — the multi-region expansion path.
    med = tmp_path / "med.jsonl"
    gulf = tmp_path / "gulf.jsonl"
    red = tmp_path / "red.jsonl"
    med.write_text(_line("100", 34.5, 35.7) + "\n")     # E. Med (off Levant)
    gulf.write_text(_line("200", 27.0, 51.0) + "\n")     # Persian Gulf
    red.write_text(_line("300", 15.0, 42.0) + "\n")      # southern Red Sea
    fleet = Fleet()
    for f in (med, gulf, red):
        _adapter(f, from_start=True)._drain(fleet)
    snap = fleet.snapshot(max_age_s=10**12)
    assert {v["ship_id"] for v in snap} == {"100", "200", "300"}   # all regions merged
    # and they carry their distinct positions (the three seas)
    by = {v["ship_id"]: (round(v["lat"]), round(v["lon"])) for v in snap}
    assert by["200"] == (27, 51) and by["300"] == (15, 42)


@pytest.mark.asyncio
async def test_health_reports_tail_state(tmp_path):
    f = tmp_path / "positions.jsonl"
    f.write_text(_line("1", 34.5, 35.7) + "\n")
    a = _adapter(f, from_start=True)
    a._drain(Fleet())
    h = a.health()
    assert h["name"] == "marinetraffic" and h["records_fed"] == 1
    assert h["exists"] is True and h["offset"] > 0
