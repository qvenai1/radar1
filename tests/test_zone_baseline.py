"""ZONES-3 — the per-zone familiarity baseline (empirical, in-process). No DB.

Fixtures build baselines from synthetic dwell-session history and exercise the
sufficiency gate (is_sound / verdict) and the familiar↔unfamiliar classifier that
replaces the rejected allowlist.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from backend import zone_baseline as zb

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


@dataclass
class Sess:
    zone_id: str
    mmsi: str
    ship_type: str | None
    duration_s: float
    start_ts: Any


def _rich_history():
    # 25 sessions, 12 distinct vessels, cargo/tanker, spanning 40 days → sound
    out = []
    for i in range(25):
        out.append(Sess("Z", f"v{i % 12}", "cargo" if i % 2 else "tanker",
                        3600.0 + i * 60, T0 + timedelta(days=i * 40 / 24)))
    return out


def test_build_and_soundness():
    b = zb.build_baselines(_rich_history())["Z"]
    assert b.n_sessions == 25 and len(b.vessels) == 12 and b.span_days >= 30
    assert b.is_sound()
    # a thin zone is NOT sound (few vessels/sessions/short span)
    thin = zb.build_baselines([Sess("T", "a", "cargo", 3600, T0),
                               Sess("T", "b", "cargo", 3600, T0 + timedelta(days=5))])["T"]
    assert not thin.is_sound()


def test_familiarity_classifier():
    b = zb.build_baselines(_rich_history())["Z"]
    # familiar: known vessel, known type, mid-range duration
    fam = zb.classify_familiarity(b, mmsi="v0", ship_type="cargo", duration_s=3700)
    assert fam.familiar and fam.reasons == ()
    # unfamiliar on each axis independently
    assert "vessel_new" in zb.classify_familiarity(b, mmsi="v999", ship_type="cargo", duration_s=3700).reasons
    assert "type_novel" in zb.classify_familiarity(b, mmsi="v0", ship_type="tug", duration_s=3700).reasons
    big = zb.classify_familiarity(b, mmsi="v0", ship_type="cargo", duration_s=10 * 3600)
    assert "duration_outlier" in big.reasons and not big.familiar
    # no baseline for a never-seen zone → unfamiliar by definition
    assert not zb.classify_familiarity(None, mmsi="x", ship_type="cargo", duration_s=100).familiar


def test_verdict_sufficient_vs_under_determined():
    good = zb.verdict(zb.build_baselines(_rich_history()))
    assert good["sufficient"] and good["zones_sound"] == 1
    thin = zb.verdict(zb.build_baselines([Sess("T", "a", "cargo", 3600, T0)]))
    assert not thin["sufficient"] and thin["zones_sound"] == 0


def test_readiness_reports_binding_bar_and_progress():
    # rich history → ready
    good = zb.readiness(zb.build_baselines(_rich_history()), window_hours=720)
    assert good["baseline_ready"] and good["zones_sound"] == 1
    # a zone with plenty of vessels+sessions but a SHORT span (5 days): the DAYS bar binds,
    # met by the fewest zones (0) — mirrors the live under-determined state
    short = [Sess("T", f"v{i % 12}", "cargo", 3600, T0 + timedelta(days=i * 5 / 24)) for i in range(25)]
    r = zb.readiness(zb.build_baselines(short))
    assert not r["baseline_ready"]
    assert r["binding_bar"] == "days" and r["gap_to_binding"] > 0
    assert r["zones_meeting"] == {"days": 0, "vessels": 1, "sessions": 1}
    assert r["per_zone"] and r["per_zone"][0]["zone_id"] == "T"
