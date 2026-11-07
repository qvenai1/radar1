"""caseKey ↔ dedup_key parity anchor (the second hand-mirror, pinned like the first).

`frontend/src/api/client.ts` mirrors `notify.identity.dedup_key` for the fallback
path, and the mirror HAD silently drifted: maritime_single lost its `:summary`
part, merging a ship's rendezvous and zone_dwell into one triage identity while
the server kept them apart — a triage write that could never render back (caught
2026-09-01, before any live row was written).

⛔ VECTORS below must stay byte-identical to frontend/src/api/caseKey.test.ts —
the same discipline as the orbital anchor (tests/test_passes.py ↔ orbit.test.ts).
"""

from __future__ import annotations

from types import SimpleNamespace as NS

from backend.notify.identity import dedup_key

# (signal_type, entity id, ts, summary) -> expected key
VECTORS = [
    ("emergency_squawk", "ae01ce", "2026-09-01T04:00:00+00:00", "squawk 7700",
     "emergency_squawk:ae01ce"),
    ("military_aircraft", "43c701", "2026-09-01T04:00:00+00:00", "RRR4952 presence",
     "military_aircraft:43c701"),
    ("military_satellite", "26407", "2026-08-30T16:43:40+00:00", "pass over Turkey",
     "military_satellite:26407:2026-08-30T16:43:40+00:00"),
    ("maritime_composite", "9235012", "2026-08-29T10:00:00+00:00", "3 patterns",
     "maritime_composite:9235012"),
    ("maritime_single", "371681", "2026-08-28T02:00:00+00:00",
     "rendezvous 3.2h with 477995018",
     "maritime_single:371681:rendezvous 3.2h with 477995018"),
    ("maritime_single", "371681", "2026-08-28T02:00:00+00:00",
     "zone_dwell 5.1h at Leviathan",
     "maritime_single:371681:zone_dwell 5.1h at Leviathan"),
    ("implausible_position", "512000999", "2026-09-01T01:00:00+00:00", "3 fixes on land",
     "implausible_position:512000999"),
    # unknown family -> the safe-but-noisy fallback WITH ts (can re-send, can never suppress)
    ("future_family", "x1", "2026-09-01T00:00:00+00:00", "whatever",
     "future_family:x1:2026-09-01T00:00:00+00:00"),
]


def _alert(sig, eid, ts, summary):
    return NS(signal_type=sig, entity_ref=NS(id=eid), ts=ts, summary=summary)


def test_every_vector_matches_the_python_key():
    for sig, eid, ts, summary, expected in VECTORS:
        assert dedup_key(_alert(sig, eid, ts, summary)) == expected, (sig, summary)


def test_maritime_single_keeps_patterns_apart():
    """The property the drifted mirror broke: one ship, two patterns, TWO identities."""
    a = dedup_key(_alert("maritime_single", "371681", None, "rendezvous 3.2h with 477995018"))
    b = dedup_key(_alert("maritime_single", "371681", None, "zone_dwell 5.1h at Leviathan"))
    assert a != b


def test_none_entity_normalises_to_empty():
    assert dedup_key(_alert("emergency_squawk", None, None, "")) == "emergency_squawk:"


async def _no_rows(*a, **k):
    return []


def test_attach_triage_publishes_the_key(monkeypatch):
    """The structural fix: every alert leaves /alerts carrying its own dedup_key,
    so the client never re-derives identity on the primary path."""
    import asyncio

    from backend.routes.alerts import _attach_triage

    class FakePool:
        async def fetch(self, sql, *args):
            return []

    class FakeDB:
        async def pool(self):
            return FakePool()

    a = _alert("maritime_single", "371681", "2026-08-28T02:00:00+00:00",
               "rendezvous 3.2h with 477995018")
    a.change_sig = ""
    a.dedup_key = ""
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _attach_triage([a], FakeDB()))
    assert a.dedup_key == "maritime_single:371681:rendezvous 3.2h with 477995018"


def test_no_relative_import_in_routes_points_at_a_ghost_module():
    """The class guard for what this file's anchor caught live: the api.py split
    relocated function-body imports one package deeper, so `from .notify…` inside
    backend/routes/ silently resolved to a module that does not exist — and the
    handlers' deliberate fail-open excepts swallowed the ModuleNotFoundError,
    killing triage attachment and case still_live for weeks of nobody noticing.
    Every relative import anywhere under backend/routes/ must actually resolve.
    """
    import importlib.util
    import re
    from pathlib import Path

    routes = Path("backend/routes")
    bad = []
    for f in routes.glob("*.py"):
        for m in re.finditer(r"^\s*from (\.+)([\w.]*) import", f.read_text(), re.M):
            dots, mod = m.group(1), m.group(2)
            # resolve relative to backend.routes (package of every file here)
            base = "backend.routes".split(".")
            up = len(dots) - 1
            parent = base if up == 0 else base[:-up] if up <= len(base) else None
            if parent is None:
                bad.append((f.name, dots + mod, "escapes the top package"))
                continue
            target = ".".join(parent + ([mod] if mod else []))
            if importlib.util.find_spec(target) is None:
                bad.append((f.name, dots + mod, f"resolves to nonexistent {target}"))
    assert not bad, bad
