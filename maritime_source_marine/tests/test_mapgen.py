from __future__ import annotations

import json
from pathlib import Path

from mtscraper.mapgen import build_map, latest_positions, load_identities, render_html


def _pos(ship_id: str, lat: float, lon: float, fetched: str, speed: float = 0.0) -> dict:
    data = {"latitude": lat, "longitude": lon, "speed_knots": speed, "shipname": f"S{ship_id}"}
    return {"ship_id": ship_id, "fetched_at": fetched, "data": data}


def test_latest_positions_keeps_last(tmp_path: Path) -> None:
    p = tmp_path / "positions.jsonl"
    with p.open("w") as fh:
        fh.write(json.dumps(_pos("1", 33.0, 35.0, "t1")) + "\n")
        fh.write(json.dumps(_pos("1", 33.5, 35.5, "t2")) + "\n")  # later wins
        fh.write(json.dumps(_pos("2", 32.0, 34.0, "t1")) + "\n")
    latest = latest_positions(p)
    assert latest["1"]["data"]["latitude"] == 33.5
    assert set(latest) == {"1", "2"}


def test_load_identities(tmp_path: Path) -> None:
    p = tmp_path / "vessel_identities.jsonl"
    p.write_text(json.dumps({"ship_id": "1", "mmsi": "636020543", "name": "MSC AMELIA"}) + "\n")
    ids = load_identities(p)
    assert ids["1"]["mmsi"] == "636020543"


def test_render_html_has_disclosure_and_dots() -> None:
    positions = {"1": _pos("1", 33.2, 35.2, "2026-08-01T00:00:00Z", speed=12.0)}
    identities = {"1": {"ship_id": "1", "mmsi": "636020543", "name": "MSC AMELIA", "imo": "9"}}
    rings = [[(34.0, 33.0), (36.0, 33.0), (36.0, 34.0), (34.0, 34.0), (34.0, 33.0)]]
    html = render_html(positions, identities, rings, latest_fetch="2026-08-01T00:00:00Z")
    assert "NOT LIVE" in html
    assert "2026-08-01T00:00:00Z" in html
    assert "636020543" in html  # resolved mmsi in the tooltip
    assert "<circle" in html and "<path" in html
    assert "MSC AMELIA" in html


def test_build_map_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "positions.jsonl").write_text(
        json.dumps(_pos("1", 33.2, 35.2, "2026-08-01T00:00:00Z")) + "\n"
    )
    (tmp_path / "vessel_identities.jsonl").write_text(
        json.dumps({"ship_id": "1", "mmsi": "636020543", "name": "X"}) + "\n"
    )
    out = tmp_path / "map.html"
    written = build_map(tmp_path, out)
    assert written.exists()
    text = written.read_text()
    assert "<svg" in text and "NOT LIVE" in text


def test_build_map_no_data_still_writes(tmp_path: Path) -> None:
    out = build_map(tmp_path, tmp_path / "map.html")
    assert out.exists()
    assert "NOT LIVE" in out.read_text()
