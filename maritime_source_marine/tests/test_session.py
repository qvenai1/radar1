from __future__ import annotations

from pathlib import Path

import pytest

from mtscraper.exceptions import ConfigError
from mtscraper.session import Session, SessionRotator, build_sessions, load_proxies


def test_single_session_no_rotation() -> None:
    sessions = build_sessions(
        proxies=[], profile_base=None, profile_count=1, single_profile="/tmp/p"
    )
    assert sessions == [Session(label="s0", proxy=None, profile_dir="/tmp/p")]
    rot = SessionRotator(sessions)
    assert rot.size == 1
    # rotating a single-session ring is a no-op
    assert rot.rotate() is rot.current()
    assert rot.rotations == 0


def test_rotation_pairs_proxies_and_profiles(tmp_path: Path) -> None:
    proxies = ["http://a:1", "http://b:2"]
    sessions = build_sessions(proxies=proxies, profile_base=tmp_path, profile_count=1)
    assert len(sessions) == 2
    assert sessions[0].proxy == "http://a:1"
    assert sessions[1].proxy == "http://b:2"
    # each session got its own persistent profile dir, and it was created
    assert sessions[0].profile_dir != sessions[1].profile_dir
    assert Path(sessions[0].profile_dir).is_dir()  # type: ignore[arg-type]


def test_profile_count_exceeds_proxies_wraps(tmp_path: Path) -> None:
    sessions = build_sessions(proxies=["http://a:1"], profile_base=tmp_path, profile_count=3)
    assert len(sessions) == 3
    assert [s.proxy for s in sessions] == ["http://a:1"] * 3  # wraps the single proxy


def test_rotator_advances_and_wraps(tmp_path: Path) -> None:
    sessions = build_sessions(proxies=[], profile_base=tmp_path, profile_count=3)
    rot = SessionRotator(sessions)
    assert rot.current().label == "s0"
    assert rot.rotate().label == "s1"
    assert rot.rotate().label == "s2"
    assert rot.rotate().label == "s0"  # wraps
    assert rot.rotations == 3


def test_rotation_without_profile_base_fails() -> None:
    with pytest.raises(ConfigError, match="profile base"):
        build_sessions(proxies=["http://a:1"], profile_base=None, profile_count=2)


def test_load_proxies(tmp_path: Path) -> None:
    f = tmp_path / "proxies.txt"
    f.write_text("# comment\nhttp://a:1\n\n  http://b:2  \n")
    assert load_proxies(f) == ["http://a:1", "http://b:2"]
    assert load_proxies(None) == []


def test_load_proxies_missing_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_proxies(tmp_path / "nope.txt")


def test_empty_rotator_rejected() -> None:
    with pytest.raises(ConfigError, match="at least one"):
        SessionRotator([])
