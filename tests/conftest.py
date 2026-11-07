"""Session-wide test setup — and the gate that makes the DB-path decision DETERMINISTIC.

⛔ THE DEFECT THIS FILE CLOSES. 24 test files decide whether they can reach the database
with a MODULE-LEVEL read, evaluated at import time:

    DSN = os.environ.get("DATABASE_URL")
    requires_db = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")

Nothing in the repo puts `DATABASE_URL` into `os.environ`. It arrives as a SIDE EFFECT of
importing `backend.config`, which calls `load_dotenv()` at import time (config.py:11). So
the answer to "can this file reach the DB?" depended on whether some earlier import had
already pulled `backend.config` in — that is, on COLLECTION ORDER.

⚠ 13 of those 24 files have no top-level `backend` import at all: they defer it into
`_client()` so the ASGI app is built per test. In a full run they are rescued by accident —
`tests/test_air_daily.py` imports `backend.app` on line 18, above its own DSN read on line
21, and every module imported after that sees a populated environment. Run one of the 13
ALONE and it prints `1 skipped`: a green line that means NOTHING RAN.

⛔ AND THAT IS HOW A REAL FAILURE WAS FILED AS AN ARTEFACT. §11.3 item 1 recorded "the
failing tests skip or pass in isolation, so the defect is cross-file state sharing, not the
product". They did not pass in isolation — they never executed. With the environment
populated deterministically the same six fail every run, against the live DB. The skip was
not hiding a flake; it was hiding the measurement.

Two things follow, and this file is both:

  1. **One loader, before collection.** `import backend.config` below runs the project's
     OWN `load_dotenv()` once, before any test module is imported, so all 24 files answer
     the same question the same way. No second `load_dotenv()` call is added: a second
     loader is a second definition of where configuration comes from.

  2. **A gate that fails closed.** A silent skip is the exact failure mode above, so it is
     never allowed to report green: if `.env` declares `DATABASE_URL` and anything still
     skipped for "DATABASE_URL not set", the load above is broken and the session EXITS
     NON-ZERO. A machine with no DSN at all is still allowed to run the pure tests — but it
     prints how many tests did not run, so a DB-less pass cannot be read as a full one.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

# ⛔ IMPORTED FOR ITS SIDE EFFECT, AND THE ORDER IS THE POINT: conftest.py is imported by
# pytest before it collects any test module, so this is what makes every module-level
# `os.environ.get("DATABASE_URL")` below it read the same value. Do not "clean this up"
# into a lazy import — the side effect IS the contract. `load_dotenv()` does not override
# an already-exported DATABASE_URL, so an explicit env var still wins.
import backend.config  # noqa: F401,E402

_SKIP_REASON = "DATABASE_URL not set"

# Nodeids skipped because no DSN was visible. Collected during the run, judged at the end.
_db_skipped: list[str] = []


def _env_file_declares_dsn() -> bool:
    """Does `.env` itself declare a DATABASE_URL?

    This is the question that separates "this machine has no database" (legitimate — run
    the pure tests) from "the loader above is broken" (a defect that must not report
    green). Read as text, never parsed for the VALUE: nothing here needs the credential,
    and a test log is the last place it should be able to appear.
    """
    env = Path(__file__).resolve().parent.parent / ".env"
    try:
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("DATABASE_URL") and "=" in stripped:
                # A declared-but-empty value is not a declaration.
                return bool(stripped.split("=", 1)[1].strip())
    except OSError:
        return False
    return False


def _db_path_line() -> str:
    """One line naming the DB-path decision.

    The defect above was invisible because the decision was never printed — a skipped file
    and a passing file produce the same shape of quiet output. One line makes "the DB path
    did not run" impossible to miss.
    """
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        # Host and port only. The credential never reaches a log line.
        tail = dsn.rsplit("@", 1)[-1] if "@" in dsn else "(unparsed)"
        return f"db-path: ENABLED — DATABASE_URL resolved, target {tail}"
    if _env_file_declares_dsn():
        return "db-path: ⛔ BROKEN — .env declares DATABASE_URL but it is not in the environment"
    return "db-path: DISABLED — no DATABASE_URL in the environment or .env; DB tests will skip"


def pytest_report_header(config) -> str:
    return _db_path_line()


def pytest_runtest_logreport(report) -> None:
    if report.skipped and _SKIP_REASON in str(report.longrepr):
        _db_skipped.append(report.nodeid)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    # ⚠ REPEATED HERE ON PURPOSE, NOT BY OVERSIGHT: `pytest_report_header` is suppressed
    # under `-q`, and `-q` is the form §11.2's gate runs. A visibility fix that is invisible
    # in the only place it is run is not a fix.
    write = terminalreporter.write_line
    write("")
    write(_db_path_line())
    if not _db_skipped:
        return
    if _env_file_declares_dsn():
        write(
            f"⛔ {len(_db_skipped)} tests skipped for «{_SKIP_REASON}» while .env DECLARES it.",
            red=True,
            bold=True,
        )
        write(
            "   The env load in tests/conftest.py is not reaching the test modules. "
            "These tests did not run, so this session proves nothing about the DB path.",
            red=True,
        )
    else:
        write(
            f"⚠ {len(_db_skipped)} tests did NOT run — no DATABASE_URL in the environment "
            "or .env. This is a partial pass, not a full one.",
            yellow=True,
            bold=True,
        )


def pytest_sessionfinish(session, exitstatus) -> None:
    """Fail closed.

    ⛔ A guard that cannot measure must refuse, not allow — and here the thing that could
    not be measured is the whole DB path. When `.env` declares a DSN, a
    "DATABASE_URL not set" skip means the loader broke: the honest exit code is non-zero,
    because the alternative is a green run that tested nothing it claims to test.
    """
    if _db_skipped and _env_file_declares_dsn():
        session.exitstatus = 1


# --------------------------------------------------------------------------------------- #
# An MMSI nothing has ever observed — MEASURED, not assumed.
# --------------------------------------------------------------------------------------- #

# MID 999 is not an ITU-assigned Maritime Identification Digit, so no legitimately
# configured transmitter should broadcast in this space. That is the REASON to look here
# first — it is not the proof, and treating it as proof is what broke two tests.
_SENTINEL_CANDIDATES = [str(n) for n in range(999_999_999, 999_999_899, -1)]

_UNOBSERVED_SQL = """
    SELECT c FROM unnest($1::text[]) AS c
    WHERE NOT EXISTS (SELECT 1 FROM vessel_states   WHERE mmsi = c)
      AND NOT EXISTS (SELECT 1 FROM vessel_identity WHERE mmsi = c)
    LIMIT 1
"""


@pytest.fixture(scope="session")
def unobserved_mmsi() -> str:
    """A 9-digit MMSI this project has never observed, established by measurement.

    ⛔ WHY THIS EXISTS. Two tests hard-coded `999999999` and called it impossible:
    "an MMSI that cannot exist in the AIS feed: MID 999 is unassigned". On 2026-08-24 the
    feed contained it — one fix, 2026-08-18, off Cyprus, speed 100.8 kn, nav_status 15,
    no identity. So `/maritime/vessel` answered 200 `last_known` and `/vessel/.../track`
    returned 1 fix, and BOTH tests failed. Neither endpoint was wrong: an unassigned MID
    is a statement about how transmitters are SUPPOSED to be configured, and this project
    exists to watch traffic that ignores such rules. A sentinel derived from a regulation
    is an assumption; this one is derived from the data.

    ⛔ AND IT IS ESTABLISHED FROM THE STORE, NOT FROM THE ENDPOINTS UNDER TEST. The
    absence is read from `vessel_states` + `vessel_identity` — the two tables whose empty
    result IS the 404 branch (api.py: `if fix is None and ident is None`) — plus the live
    snapshot, so a hull that has arrived but not yet landed in the store cannot be picked.
    Probing with `/maritime/vessel` until something returned 404 and then asserting 404
    would have proved nothing at all.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip(_SKIP_REASON)

    async def _probe() -> str | None:
        from backend.api import MARITIME_STALE_S, _read_snapshot
        from backend.db import Database

        # Anything in a FRESH snapshot is observed right now even if the store has not
        # caught up yet. A stale snapshot says nothing about the present, so it is ignored
        # here exactly as the endpoint ignores it.
        live: set[str] = set()
        snap = _read_snapshot("aisstream")
        if snap is not None:
            data, age = snap
            if age <= MARITIME_STALE_S:
                live = {str(r.get("mmsi")) for r in data.get("states", [])}

        candidates = [c for c in _SENTINEL_CANDIDATES if c not in live]
        db = Database(dsn)
        try:
            pool = await db.pool()
            return await pool.fetchval(_UNOBSERVED_SQL, candidates)
        finally:
            await db.close()

    found = asyncio.run(_probe())
    if not found:
        pytest.skip(
            f"every one of the {len(_SENTINEL_CANDIDATES)} MID-999 sentinels has been "
            "observed — pick a wider candidate range rather than assuming one is free"
        )
    return found
