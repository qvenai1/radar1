"""Unit tests for layered configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from adsb_scraper.config import load_config
from adsb_scraper.exceptions import ConfigError


def test_defaults() -> None:
    cfg = load_config([])
    assert cfg.area.buffer_km == 50.0
    assert cfg.area.persist_out_of_area is False
    assert cfg.persistence.fsync is True
    assert cfg.results_file.name == "aircraft.jsonl"


def test_cli_overrides() -> None:
    cfg = load_config(["--area-buffer-km", "80", "--persist-out-of-area", "--no-headless"])
    assert cfg.area.buffer_km == 80.0
    assert cfg.area.persist_out_of_area is True
    assert cfg.browser.headless is False


def test_env_layer() -> None:
    cfg = load_config([], environ={"ADSB_AREA_BUFFER_KM": "30", "ADSB_PERSISTENCE_FSYNC": "off"})
    assert cfg.area.buffer_km == 30.0
    assert cfg.persistence.fsync is False


def test_precedence_file_env_cli(tmp_path: Path) -> None:
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text(
        "output_dir = '/tmp/x'\n[area]\nbuffer_km = 15.0\n[timing]\nfetch_interval = 30.0\n",
        encoding="utf-8",
    )
    cfg = load_config(
        ["--config", str(cfg_file), "--interval", "12"],
        environ={"ADSB_AREA_BUFFER_KM": "22"},
    )
    assert cfg.output_dir == Path("/tmp/x")  # from file
    assert cfg.area.buffer_km == 22.0  # env beats file
    assert cfg.timing.fetch_interval == 12.0  # cli beats file


def test_invalid_value_raises() -> None:
    with pytest.raises(ConfigError):
        load_config(["--area-buffer-km", "-5"])


def test_unknown_file_option_raises(tmp_path: Path) -> None:
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text('{"area": {"nonsense": 1}}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(["--config", str(cfg_file)])


def test_missing_file_raises() -> None:
    with pytest.raises(ConfigError):
        load_config(["--config", "/does/not/exist.toml"])
