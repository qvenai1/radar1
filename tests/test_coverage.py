"""DASH-BOARD-0 — the coverage contract, and the property that makes it trustworthy.

⛔ THE POINT OF THESE TESTS IS NOT THAT THE NUMBERS ARE RIGHT TODAY. It is that the contract
SPEAKS FOR ITSELF: every gap list is derived from a source that changes on its own, so a
hand-maintained list can never drift into it. Each test therefore CHANGES the underlying
source and asserts the contract followed — a fixed expected value would pass just as well
against a hard-coded answer, and prove nothing.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone


from backend import coverage as cov
from backend.api import _clip, _union_seconds
from backend.models import CoverageGap


def _boot(first: datetime, last: datetime) -> dict:
    return {"first_entry": int(first.timestamp() * 1e6),
            "last_entry": int(last.timestamp() * 1e6)}


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


class _Run:
    """Stand-in for subprocess.run returning a chosen boot table."""

    def __init__(self, boots):
        self.stdout = json.dumps(boots)


# ---------------------------------------------------------------- host gaps

def test_host_gaps_are_read_from_the_boot_table_not_a_list(monkeypatch):
    """⛔ SELF-UPDATING PROOF, kind 1: change the boot table, the contract changes.

    Two runs over two DIFFERENT tables must produce two different answers. If host gaps were
    hand-maintained anywhere, the second run would still return the first run's windows."""
    cov._host_cache = None
    a = [_boot(_utc(2026, 7, 1), _utc(2026, 7, 2)), _boot(_utc(2026, 7, 5), _utc(2026, 7, 6))]
    monkeypatch.setattr(cov.subprocess, "run", lambda *_, **__: _Run(a))
    first = cov.host_gaps(now=_utc(2026, 7, 6, 1))
    assert len(first) == 1
    assert first[0]["start"] == _utc(2026, 7, 2)
    assert first[0]["end"] == _utc(2026, 7, 5)
    assert first[0]["kind"] == "host"
    assert first[0]["basis"] == "journal-boot-table"

    # the machine boots again — a NEW window appears, with nothing edited by hand
    cov._host_cache = None
    b = a + [_boot(_utc(2026, 7, 9), _utc(2026, 7, 10))]
    monkeypatch.setattr(cov.subprocess, "run", lambda *_, **__: _Run(b))
    second = cov.host_gaps(now=_utc(2026, 7, 10, 1))
    assert len(second) == 2
    assert second[1]["start"] == _utc(2026, 7, 6)
    assert second[1]["end"] == _utc(2026, 7, 9)


def test_a_reboot_is_not_an_outage(monkeypatch):
    """A boot-to-boot silence under the threshold is a restart, not a coverage hole."""
    cov._host_cache = None
    quick = [_boot(_utc(2026, 7, 1), _utc(2026, 7, 2, 0, 0, 0)),
             _boot(_utc(2026, 7, 2, 0, 5, 0), _utc(2026, 7, 3))]
    monkeypatch.setattr(cov.subprocess, "run", lambda *_, **__: _Run(quick))
    assert cov.host_gaps(now=_utc(2026, 7, 3, 1)) == []


def test_an_unreadable_journal_is_reported_not_silently_empty(monkeypatch):
    """⛔ An empty list from a FAILED read must never look like perfect uptime."""
    cov._host_cache = None

    def boom(*_a, **_k):
        raise OSError("journalctl missing")

    monkeypatch.setattr(cov.subprocess, "run", boom)
    assert cov.host_gaps(now=_utc(2026, 7, 3)) == []
    assert cov.host_gap_basis().startswith("UNAVAILABLE")


# ------------------------------------------------------------ detector gaps

def test_detector_gap_tracks_the_timer_symlink(monkeypatch, tmp_path):
    """⛔ SELF-UPDATING PROOF, kind 3: move the timer's enable time, the gap moves.

    The detector window is bounded by the mtime of the unit's own `timers.target.wants`
    symlink. Re-enabling a timer rewrites that mtime, and the contract must follow without
    anyone editing a date."""
    link = tmp_path / "scraplan-fake.timer"
    link.write_text("")
    monkeypatch.setattr(cov, "_UNITS_DIR", str(tmp_path))
    stream = cov.Stream(key="k", domain="sea", unit="u", source="s",
                        table="maritime_events", ts_column="start_ts",
                        detector_timer="scraplan-fake.timer")
    series_start = _utc(2026, 7, 1)

    enabled_a = _utc(2026, 7, 10)
    os.utime(link, (enabled_a.timestamp(), enabled_a.timestamp()))
    g1 = cov.detector_gap(stream, series_start)
    assert g1["kind"] == "detector" and g1["basis"] == "timer-enable-mtime"
    assert g1["end"] == enabled_a

    # the timer is re-enabled later — the unobserved window grows, by itself
    enabled_b = _utc(2026, 7, 20)
    os.utime(link, (enabled_b.timestamp(), enabled_b.timestamp()))
    g2 = cov.detector_gap(stream, series_start)
    assert g2["end"] == enabled_b
    assert g2["seconds"] > g1["seconds"]


def test_no_detector_gap_when_the_timer_predates_the_data():
    """A detector enabled before the first row leaves no unobserved window."""
    stream = cov.STREAMS_BY_KEY["ais_gap_events"]
    assert cov.detector_gap(stream, _utc(2030, 1, 1)) is None


def test_a_raw_feed_has_no_detector_gap():
    """Only DERIVED series can be detector-limited; a raw feed has no detector."""
    assert cov.detector_gap(cov.STREAMS_BY_KEY["air_states"], _utc(2026, 7, 1)) is None


# ------------------------------------------------------- arithmetic honesty

def test_overlapping_gaps_are_counted_once():
    """⛔ The three kinds NEST — a detector window spans host outages inside it. Summing
    their durations double-counts and drives the observed fraction to a false zero (measured:
    this exact bug reported 0.0 for five streams before the union was added)."""
    g = [CoverageGap(start=_utc(2026, 7, 1), end=_utc(2026, 7, 10), kind="detector", basis="b"),
         CoverageGap(start=_utc(2026, 7, 3), end=_utc(2026, 7, 4), kind="host", basis="b"),
         CoverageGap(start=_utc(2026, 7, 5), end=_utc(2026, 7, 6), kind="source", basis="b")]
    assert _union_seconds(g) == timedelta(days=9).total_seconds()


def test_disjoint_gaps_add_up():
    g = [CoverageGap(start=_utc(2026, 7, 1), end=_utc(2026, 7, 2), kind="host", basis="b"),
         CoverageGap(start=_utc(2026, 7, 5), end=_utc(2026, 7, 6), kind="host", basis="b")]
    assert _union_seconds(g) == timedelta(days=2).total_seconds()


def test_gaps_are_clipped_to_the_streams_own_span():
    """⛔ The boot table reaches back months before any stream began. A February host outage
    is not a hole in a July series, and shipping it would bury the ones that matter."""
    g = CoverageGap(start=_utc(2026, 2, 1), end=_utc(2026, 2, 20), kind="host", basis="b")
    assert _clip(g, _utc(2026, 7, 1), _utc(2026, 8, 1)) is None
    inside = CoverageGap(start=_utc(2026, 7, 10), end=_utc(2026, 8, 10), kind="host", basis="b")
    c = _clip(inside, _utc(2026, 7, 1), _utc(2026, 8, 1))
    assert c.end == _utc(2026, 8, 1)
    assert c.seconds == timedelta(days=22).total_seconds()


# ------------------------------------------------------- the contract shape

def test_every_stream_names_its_unit_and_a_real_source():
    """⛔ No cross-domain total is possible if every series says what it counts, and no
    number is anonymous if every series names its upstream rather than «our data»."""
    for s in cov.STREAMS:
        assert s.unit and not s.unit.isspace()
        assert s.source and "our data" not in s.source.lower()
        assert s.domain in {"air", "sea", "space"}


def test_the_space_pass_stream_is_declared_not_a_series():
    """satellite_passes is ONE recomputed window — drawing it as a stream with gaps would
    imply a past that was never stored."""
    s = cov.STREAMS_BY_KEY["satellite_passes"]
    assert s.not_a_series and "NOT A TIME SERIES" in s.not_a_series


def test_streams_without_feed_events_say_so_rather_than_look_perfect():
    """⛔ Measured: only aisstream records feed_connect/feed_disconnect. An air stream with
    no reconstructable outages must not be indistinguishable from a flawless one."""
    assert "UNAVAILABLE" in cov.source_gap_basis(cov.STREAMS_BY_KEY["air_states"])
    assert "reconstruct_outage_windows" in cov.source_gap_basis(cov.STREAMS_BY_KEY["sea_states"])


def test_the_frame_boundary_is_the_measured_instant():
    """The ratio cutoff is the upstream narrowing, not a rounded date."""
    assert cov.FRAME_BOUNDARY == datetime(2026, 7, 29, 13, 55, 36, tzinfo=timezone.utc)


# ------------------------------------------- UI-0: a composite carries EVERY contributor

def _composite_fixture(tmp_path, monkeypatch, contributors):
    """A registry with one composite over ``contributors``, each owning a real timer file.

    ⛔ Nothing is frozen: the windows the tests assert on come from mtimes this helper sets,
    so a test can MOVE a timer and watch the contract follow it — the GNSS lesson (two tests
    passed because the detector was dead and the expected value was a constant)."""
    monkeypatch.setattr(cov, "_UNITS_DIR", str(tmp_path))
    reg = {}
    for name, enabled in contributors.items():
        timer = f"scraplan-{name}.timer"
        (tmp_path / timer).write_text("")
        os.utime(tmp_path / timer, (enabled.timestamp(), enabled.timestamp()))
        reg[f"{name}_events"] = cov.Stream(
            key=f"{name}_events", domain="sea", unit=f"detected {name} events",
            source="s", table="maritime_events", ts_column="start_ts", detector_timer=timer)
    composite = cov.Stream(
        key="composite", domain="sea", unit="hulls", source="v",
        table="maritime_hull_events", ts_column="start_ts",
        contributes_from=tuple(reg))
    reg["composite"] = composite
    return composite, reg


def test_a_composite_carries_every_contributors_detector_window(tmp_path, monkeypatch):
    """⛔ UI-0, THE DEFECT ITSELF: embedding one contributor's coverage drew the other
    detectors' deployment dates as movements in the phenomenon. Measured on the live endpoint
    before the fix: 1 window listed, 4 missing."""
    start = _utc(2026, 7, 1)
    composite, reg = _composite_fixture(tmp_path, monkeypatch, {
        "alpha": _utc(2026, 7, 5), "beta": _utc(2026, 7, 12), "gamma": _utc(2026, 7, 20),
    })
    gaps, unmeasured = cov.detector_gaps(composite, start, reg)
    assert unmeasured == []
    ends = sorted(g["end"] for g in gaps)
    assert ends == [_utc(2026, 7, 5), _utc(2026, 7, 12), _utc(2026, 7, 20)]
    assert {g["source"] for g in gaps} == {
        "scraplan-alpha.timer", "scraplan-beta.timer", "scraplan-gamma.timer"}
    assert all(g["kind"] == "detector" and g["start"] == start for g in gaps)


def test_adding_a_contributor_adds_its_window(tmp_path, monkeypatch):
    """⛔ FOLLOWS ITS SOURCE: the window count is driven by the registry, never by a literal.
    A sixth detector admitted into the view tomorrow appears here by being named."""
    start = _utc(2026, 7, 1)
    _, reg = _composite_fixture(tmp_path, monkeypatch, {
        "alpha": _utc(2026, 7, 5), "beta": _utc(2026, 7, 12),
    })
    two = reg["composite"]
    assert len(cov.detector_gaps(two, start, reg)[0]) == 2

    # admit a third — same code path, one more window, no test constant to update
    (tmp_path / "scraplan-delta.timer").write_text("")
    later = _utc(2026, 7, 25)
    os.utime(tmp_path / "scraplan-delta.timer", (later.timestamp(), later.timestamp()))
    reg["delta_events"] = cov.Stream(
        key="delta_events", domain="sea", unit="u", source="s", table="maritime_events",
        ts_column="start_ts", detector_timer="scraplan-delta.timer")
    three = cov.Stream(**{**two.__dict__, "contributes_from": (*two.contributes_from, "delta_events")})
    gaps, _ = cov.detector_gaps(three, start, reg)
    assert len(gaps) == 3
    assert later in {g["end"] for g in gaps}


def test_moving_one_contributors_timer_moves_only_its_window(tmp_path, monkeypatch):
    """⛔ The same self-updating proof as the single-stream case, through the composite: the
    window is the mtime, so re-enabling one timer grows one window and leaves the rest."""
    start = _utc(2026, 7, 1)
    composite, reg = _composite_fixture(tmp_path, monkeypatch, {
        "alpha": _utc(2026, 7, 5), "beta": _utc(2026, 7, 12),
    })
    before = {g["source"]: g["seconds"] for g in cov.detector_gaps(composite, start, reg)[0]}

    moved = _utc(2026, 7, 18)
    os.utime(tmp_path / "scraplan-beta.timer", (moved.timestamp(), moved.timestamp()))
    after = {g["source"]: g["seconds"] for g in cov.detector_gaps(composite, start, reg)[0]}

    assert after["scraplan-beta.timer"] > before["scraplan-beta.timer"]
    assert after["scraplan-alpha.timer"] == before["scraplan-alpha.timer"]


def test_an_unmeasurable_contributor_is_reported_not_dropped(tmp_path, monkeypatch):
    """⛔ FAILS CLOSED. A missing symlink, a contributor with no timer, and a key absent from
    the registry each yield NO window — so if they were dropped silently, an unmeasured
    detector would be indistinguishable from a fully observed one (BACKUP-HARDEN 1's shape)."""
    start = _utc(2026, 7, 1)
    composite, reg = _composite_fixture(tmp_path, monkeypatch, {"alpha": _utc(2026, 7, 5)})
    (tmp_path / "scraplan-alpha.timer").unlink()                 # enabled, then unreadable
    reg["timerless_events"] = cov.Stream(
        key="timerless_events", domain="sea", unit="u", source="s",
        table="maritime_events", ts_column="start_ts")           # contributes, owns no timer
    wired = cov.Stream(**{**composite.__dict__,
                         "contributes_from": ("alpha_events", "timerless_events", "ghost_events")})

    gaps, unmeasured = cov.detector_gaps(wired, start, reg)
    assert gaps == []
    joined = " ".join(unmeasured)
    assert "scraplan-alpha.timer" in joined
    assert "timerless_events" in joined and "owns no timer" in joined
    assert "ghost_events" in joined and "not in the stream registry" in joined
    # ⛔ and the failure reaches the payload's own basis field as an upper-bound warning
    assert "UPPER BOUND" in cov.observed_fraction_basis(wired, unmeasured)


def test_a_composite_fraction_excludes_detector_windows_and_says_so(tmp_path, monkeypatch):
    """⛔ THE MEASUREMENT THAT FORCED THIS. Live on 2026-08-04 the five contributors' windows
    union to 23.43 d of a 23.64 d span (gnss_anomaly came onto a timer four hours earlier), so
    counting them reports observed_fraction 0.0088 for a period holding 8,657 rows. A detector
    window on a composite blinds ONE axis of six — the count inside it is depressed by an
    unknown amount, which is not the claim «unobserved» and cannot be folded into one scalar."""
    composite, _ = _composite_fixture(tmp_path, monkeypatch, {"alpha": _utc(2026, 7, 5)})
    single = cov.STREAMS_BY_KEY["ais_gap_events"]
    host = [CoverageGap(start=_utc(2026, 7, 2), end=_utc(2026, 7, 3), kind="host", basis="b")]
    gaps = [CoverageGap(start=_utc(2026, 7, 1), end=_utc(2026, 7, 20), kind="detector", basis="b"),
            CoverageGap(start=_utc(2026, 7, 25), end=_utc(2026, 7, 26), kind="source", basis="b")]

    # the composite drops the detector window from the arithmetic — and keeps it in `gaps`
    counted = cov.fraction_gaps(composite, host, gaps)
    assert all(g.kind != "detector" for g in counted)
    assert _union_seconds(counted) == timedelta(days=2).total_seconds()
    assert "EXCLUDED here" in cov.observed_fraction_basis(composite, [])

    # ⛔ a single-detector stream is UNCHANGED: there the detector blinds the whole series
    assert cov.fraction_gaps(single, host, gaps) == host + gaps
    assert "all three blind this stream entirely" in cov.observed_fraction_basis(single, [])


def test_the_multi_pattern_composite_names_all_five_timer_contributors():
    """⛔ The shipped registry, not a fixture: the six event types reaching
    `maritime_hull_events` (migration 037) are ais_gap · rendezvous · gnss_anomaly ·
    zone_dwell · identity_change · imo_integrity. Five own timers; imo_integrity is a hand-run
    backfill and owns no stream, which the caveats must state rather than omit."""
    c = cov.STREAMS_BY_KEY["hull_multi_pattern"]
    assert set(c.contributes_from) == {
        "ais_gap_events", "rendezvous_events", "gnss_anomaly_events",
        "zone_dwell_events", "identity_change_events"}
    assert all(k in cov.STREAMS_BY_KEY for k in c.contributes_from)
    assert all(cov.STREAMS_BY_KEY[k].detector_timer for k in c.contributes_from)
    assert c.detector_timer is None                     # the view has no detector of its own
    assert "hulls" in c.unit                            # ⛔ not «zone-dwell sessions»
    assert any("imo_integrity" in cv for cv in c.caveats)
