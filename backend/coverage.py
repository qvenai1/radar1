"""DASH-BOARD-0 — the coverage contract that every time series must carry.

⛔ THE RULE THIS EXISTS FOR: «a contract that can express a year but cannot express *this
period was not observed* is the more dangerous of the two». Schemas and contracts here are
built for year-and-longer windows, and every series states, beside its values, exactly which
parts of that window were actually observed.

⛔ THREE KINDS OF GAP, NOT ONE. Measured in GNSS-REVIVE and DASH-BOARD-pre, and they have
three different sources and three different meanings. A contract carrying only the first
would draw the other two as real quiet periods:

  1. ``host``     — the machine was off. GLOBAL: every stream stops together. Measured on
                    2026-07-17 / 07-23 / 07-24 / 07-28, where air, sea and satellite TLE all
                    report exactly 0 rows.
  2. ``source``   — the upstream feed dropped while the host was UP. PER STREAM: on
                    2026-07-21 the host ran, air recorded 10,093,424 rows and sea recorded 0.
  3. ``detector`` — the phenomenon was happening and nothing was looking. PER DETECTOR: a
                    derived series cannot start before its timer was enabled, so a series that
                    RISES at deployment reads «the phenomenon increased» and means «observation
                    began». `gnss_anomaly` is the clearest case — enabled 2026-08-04.

⛔ NONE OF THE THREE IS HAND-MAINTAINED. Each is read from a source that updates itself:
  host      → ``journalctl --list-boots -o json`` (the boot table; 0.09 s, cached)
  source    → ``signal_events`` through :func:`gap_detect.reconstruct_outage_windows` — the
              SAME machinery DISK-1 used to compute the 45.46 % AIS outage. ⛔ Not
              reimplemented here; a second outage calculation would be free to disagree.
  detector  → the mtime of the timer's own ``timers.target.wants`` symlink, i.e. the moment
              ``systemctl enable`` created it.

⚠ A hand-filled list would be correct on the day it was written and wrong on the next power
cycle — and these are CLEAN, DELIBERATE shutdowns (measured: `systemd-shutdown: Syncing
filesystems`), a standing usage pattern that will recur, not a fault awaiting repair.
"""

from __future__ import annotations

import json
import os
import subprocess

from .config import settings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .gap_detect import reconstruct_outage_windows

#: A boot-to-boot silence longer than this is a host outage rather than a reboot. A reboot
#: on this machine takes seconds to a couple of minutes; 15 min is comfortably past that and
#: well inside the shortest real outage measured (35.41 h).
HOST_GAP_MIN_S = settings.host_gap_min_s

#: ⛔ THE FRAME BOUNDARY — RATIOS MAY NOT CROSS IT. On 2026-07-29 13:55:36Z the upstream air
#: sampling frame was narrowed from global to the AOI. Measured, it does NOT step the absolute
#: per-country counts (Syria: 303/408/367 before, 306/400/394/334/361 after), but it destroys
#: every DENOMINATOR: `country IS NULL` covers 96.05 % of pre-frame adsbexchange rows and
#: 29.6 % of post-frame ones. Any share, percentage or "fraction of traffic" computed across
#: it compares two different populations. Carried in the contract, not left to the chart.
FRAME_BOUNDARY = datetime(2026, 7, 29, 13, 55, 36, tzinfo=timezone.utc)

#: How long a host-gap read is reused. The boot table only changes when the machine boots.
_HOST_CACHE_S = settings.host_cache_s

_UNITS_DIR = os.path.expanduser("~/.config/systemd/user/timers.target.wants")


@dataclass(frozen=True)
class Stream:
    """One measurable series, and everything the contract must say about it.

    ⛔ `unit` is not decoration. Air counts AIRFRAMES, maritime counts DETECTOR EVENTS, space
    counts PASSES — three different things — so no total may be taken across domains. Naming
    the unit in the contract is what makes that visible to whatever draws the series.
    """

    key: str
    domain: str            # air | sea | space
    unit: str              # what ONE value of this series counts — never omitted
    source: str            # the NAMED upstream, never "our data"
    table: str
    ts_column: str
    #: the signal_events source whose feed_connect/feed_disconnect reconstruct this
    #: stream's own outages — None when the stream records no feed events at all
    signal_source: str | None = None
    #: the timer whose enable time bounds a DERIVED series; None for a raw feed
    detector_timer: str | None = None
    #: ⛔ UI-0 — the keys of OTHER streams whose detectors contribute rows to this one.
    #: Set only on a COMPOSITE series: one that counts across several detectors at once, so
    #: it is detector-limited whenever ANY contributor was not running. Its coverage must
    #: therefore carry EVERY contributor's detector window, not just its own — a composite
    #: that embeds one contributor's coverage draws the other contributors' deployment dates
    #: as movements in the phenomenon. Empty for every single-detector stream.
    contributes_from: tuple[str, ...] = ()
    #: ⛔ set when the stream is not a time series at all, and says why
    not_a_series: str | None = None
    #: honesty notes that belong to the series itself, carried with it
    caveats: tuple[str, ...] = ()


#: ⛔ EVERY STREAM THE DASHBOARD MAY DRAW, each with its unit and its named source.
STREAMS: tuple[Stream, ...] = (
    Stream(
        key="air_states", domain="air", unit="airframe position reports",
        source="OpenSky Network / airplanes.live / ADS-B Exchange (three distinct populations)",
        table="state_vectors", ts_column="ts",
        caveats=(
            "⛔ opensky and adsbexchange are DIFFERENT COMMUNITIES — counts from the two are "
            "never summed and never compared as one population.",
            "⛔ Ratios may not cross the frame boundary; absolute counts may. See ratio_cutoff.",
        ),
    ),
    Stream(
        key="sea_states", domain="sea", unit="AIS position reports",
        source="AISStream (live) + MarineTraffic (scraped, stale-tolerated)",
        table="vessel_states", ts_column="ts", signal_source="aisstream",
    ),
    Stream(
        key="ais_gap_events", domain="sea", unit="detected AIS-gap events",
        source="scraplan ais-gap-detector-v1 over AISStream positions",
        table="maritime_events", ts_column="start_ts",
        signal_source="aisstream", detector_timer="scraplan-gaps.timer",
        caveats=(
            "⚠ THIS SERIES MEASURES OUR DETECTOR, NOT THE WORLD. `outage-excluded` (16,882) "
            "and `jamming-excluded` (301) are gaps the detector REMOVED as our own downtime — "
            "plotting the raw total without the classification breakdown counts our outages "
            "as vessel behaviour. ⛔ The classification axis is never collapsed.",
        ),
    ),
    Stream(
        key="rendezvous_events", domain="sea", unit="detected rendezvous events",
        source="scraplan rendezvous-detector-v1", table="maritime_events", ts_column="start_ts",
        detector_timer="scraplan-rendezvous.timer",
        caveats=("Every event is `candidate` — an investigation lead, never proof.",),
    ),
    Stream(
        key="gnss_anomaly_events", domain="sea", unit="detected GNSS-anomaly episodes",
        source="scraplan gnss-anomaly-detector-v1", table="maritime_events", ts_column="start_ts",
        detector_timer="scraplan-gnss.timer",
        caveats=(
            "⛔ Enabled only on 2026-08-04 (GNSS-REVIVE): before that the detector was DEAD for "
            "13 d 17 h, so every earlier count is detector-limited, not phenomenon-limited.",
        ),
    ),
    Stream(
        key="zone_dwell_events", domain="sea", unit="detected zone-dwell sessions",
        source="scraplan zone-dwell-v1", table="maritime_events", ts_column="start_ts",
        detector_timer="scraplan-zone-dwell.timer",
    ),
    Stream(
        key="identity_change_events", domain="sea", unit="detected identity-change events",
        source="scraplan identity-change-detector-v1", table="maritime_events",
        ts_column="start_ts", detector_timer="scraplan-identity.timer",
    ),
    Stream(
        # ⛔ UI-0 — THE COMPOSITE. `/maritime/series/multi-pattern` counts
        # `count(DISTINCT event_type)` over `maritime_hull_events`, so its coverage is not any
        # one detector's. It used to embed `zone_dwell_events`, which reported that stream's
        # identity, unit (`detected zone-dwell sessions` for a series whose unit is HULLS), span,
        # row count and fraction in place of the series' own — and carried ONE of five detector
        # windows. Measured before the fix: 1 gap listed, 4 missing.
        key="hull_multi_pattern", domain="sea",
        unit="hulls (IMO-stitched), not vessels and not events",
        source="maritime_hull_events — the same view /maritime/composite ranks from",
        table="maritime_hull_events", ts_column="start_ts", signal_source="aisstream",
        # ⛔ NO detector_timer of its own: the view has no detector. Its detector limits are
        # entirely its contributors', which is what `contributes_from` expresses.
        contributes_from=(
            "ais_gap_events", "rendezvous_events", "gnss_anomaly_events",
            "zone_dwell_events", "identity_change_events",
        ),
        caveats=(
            "⛔ SIX event types reach this series, and only FIVE have a stream with a timer. "
            "The sixth, `imo_integrity`, is written by scripts/backfill_imo_integrity.py and "
            "has NO timer at all — it runs only when run by hand, so it is never systematically "
            "observed. Measured 2026-08-04: 1 event on 1 hull, and 0 hulls reach "
            "n_pattern_types > 1 because of it (289 multi-pattern hulls with it and 289 "
            "without). It is stated here rather than emitted as a gap window because a window "
            "spanning the whole series carries no position information and would make the "
            "break-the-line rule degenerate — but any `imo_integrity` pattern on a hull is a "
            "hand-run finding, not a monitored one.",
            "⛔ A `detector` gap on THIS series blinds ONE axis of six, not the series — the "
            "other detectors kept recording inside it. So the count is DEPRESSED by an unknown "
            "amount in that window, never zero, and the window is not evidence of quiet.",
        ),
    ),
    Stream(
        key="satellite_tle", domain="space", unit="TLE element fetches",
        source="CelesTrak (+ Space-Track for the strategic set)",
        table="satellite_history", ts_column="fetched_at",
        detector_timer="scraplan-satellites.timer",
    ),
    Stream(
        key="satellite_passes", domain="space", unit="computed AOI passes",
        source="scraplan SGP4 propagation over CelesTrak elements",
        table="satellite_passes", ts_column="start_time",
        not_a_series=(
            "⛔ NOT A TIME SERIES. satellite_passes holds ONE rolling window (measured: 359 rows, "
            "a single window_start) and is RECOMPUTED, not accumulated — there is no history of "
            "passes to plot. Drawing it as a stream with gaps would imply a past that was never "
            "stored. It answers «now», and only «now», until pass retention is built."
        ),
        caveats=(
            "⛔ Counts OBJECTS WITH A COMPUTED PASS over the AOI — never «tracked satellites». "
            "The element catalogue carries no country.",
        ),
    ),
)

STREAMS_BY_KEY: dict[str, Stream] = {s.key: s for s in STREAMS}

_host_cache: tuple[float, list[dict[str, Any]]] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def host_gaps(now: datetime | None = None) -> list[dict[str, Any]]:
    """Windows in which the HOST was off — global to every stream.

    Read from ``journalctl --list-boots -o json``: the boot table is the machine's own record
    of when it was running, so this list re-derives itself on every boot with nothing to
    maintain. Each entry carries ``first_entry`` / ``last_entry`` as µs epoch; a silence
    between one boot's last entry and the next boot's first that exceeds
    :data:`HOST_GAP_MIN_S` is a host outage.

    ⚠ Returns ``[]`` and says so through ``basis`` when the journal cannot be read, rather
    than pretending the host never stopped: an empty list from a FAILED read and an empty list
    from a perfect uptime must not look the same to a caller, so the failure is reported by
    :func:`host_gap_basis`.
    """
    global _host_cache
    now = now or _now()
    if _host_cache is not None and (now.timestamp() - _host_cache[0]) < _HOST_CACHE_S:
        return _host_cache[1]
    out: list[dict[str, Any]] = []
    try:
        raw = subprocess.run(
            ["journalctl", "--list-boots", "--no-pager", "-o", "json"],
            capture_output=True, text=True, timeout=20, check=True).stdout
        boots = json.loads(raw)
        boots.sort(key=lambda b: b["first_entry"])
        for a, b in zip(boots, boots[1:], strict=False):  # pairwise — lengths differ by design
            start = datetime.fromtimestamp(a["last_entry"] / 1e6, timezone.utc)
            end = datetime.fromtimestamp(b["first_entry"] / 1e6, timezone.utc)
            secs = (end - start).total_seconds()
            if secs >= HOST_GAP_MIN_S:
                out.append({
                    "start": start, "end": end, "kind": "host",
                    "basis": "journal-boot-table", "source": "systemd-journald",
                    "seconds": round(secs, 1),
                    "note": "host powered off — every stream stops together",
                })
    except Exception as exc:                                     # noqa: BLE001
        _host_cache = (now.timestamp(), [])
        _host_cache_err(str(exc))
        return []
    _host_cache = (now.timestamp(), out)
    _host_cache_err(None)
    return out


_host_err: str | None = None


def _host_cache_err(msg: str | None) -> None:
    global _host_err
    _host_err = msg


def host_gap_basis() -> str:
    """How the host list was obtained — so an empty list is never ambiguous."""
    return "journal-boot-table" if _host_err is None else f"UNAVAILABLE: {_host_err}"


def detector_gap(stream: Stream, series_start: datetime | None) -> dict[str, Any] | None:
    """The window in which this detector's phenomenon was unobserved because the detector
    was not running — bounded by the mtime of its ``timers.target.wants`` symlink, i.e. the
    moment ``systemctl enable`` created it.

    ⛔ This is the gap kind that reads as GOOD NEWS if it is omitted: a series that steps up
    on the deployment date looks like a rising phenomenon and is a rising *detector*.

    ⚠ Its limit, stated rather than hidden: the symlink records the ENABLE time, so a detector
    that was run by hand before deployment (all of these were, at least once) has some earlier
    events. The window is therefore "not systematically observed", not "no data at all" — and
    the events inside it are exactly the hand-run ones, which is why it must not be read as a
    rate.
    """
    if not stream.detector_timer or series_start is None:
        return None
    enabled = _timer_enabled(stream.detector_timer)
    if enabled is None or enabled <= series_start:
        return None
    return _detector_window(stream.detector_timer, series_start, enabled)


def _timer_enabled(timer: str) -> datetime | None:
    """When ``systemctl enable`` created this timer's symlink, or None if it cannot be read.

    ⚠ The two None cases are DIFFERENT and callers must not collapse them: a missing symlink
    means "never enabled", an unreadable one means "not measured". :func:`detector_gaps`
    separates them; :func:`detector_gap` cannot, which is why it is only used for a stream
    that owns its timer and whose absence is already visible in `gap_basis`.
    """
    try:
        return datetime.fromtimestamp(os.lstat(os.path.join(_UNITS_DIR, timer)).st_mtime,
                                      timezone.utc)
    except OSError:
        return None


def _detector_window(timer: str, series_start: datetime, enabled: datetime) -> dict[str, Any]:
    """The one shape a detector gap has — built here so no caller re-spells it."""
    return {
        "start": series_start, "end": enabled, "kind": "detector",
        "basis": "timer-enable-mtime", "source": timer,
        "seconds": round((enabled - series_start).total_seconds(), 1),
        "note": ("detector not on a timer before this instant — counts inside this window are "
                 "detector-limited, not phenomenon-limited"),
    }


def detector_gaps(
    stream: Stream, series_start: datetime | None,
    by_key: dict[str, Stream] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """⛔ UI-0 — EVERY detector window that limits this series: its own AND each contributor's.

    Returns ``(gaps, unmeasured)``. A single-detector stream returns at most its own window and
    an empty ``unmeasured`` — identical to :func:`detector_gap`, which this composes rather than
    reimplements. A COMPOSITE stream (``contributes_from`` non-empty) counts across several
    detectors at once, so it is detector-limited whenever ANY of them was not yet on a timer,
    and every one of those windows belongs in ``gaps``.

    ⛔ WHY THE CONTRIBUTOR WINDOWS START AT **THIS** SERIES' START, not the contributor's own.
    The question the window answers is "over which part of THIS series was that axis blind?",
    and this series began when it began. Using the contributor's own first event would also cost
    one query per contributor for a bound that ``_clip`` then discards.

    ⛔ FAILS CLOSED. A contributor key that is not in the registry, or a timer whose symlink
    cannot be stat'd, is NOT silently dropped — it is returned in ``unmeasured`` so the caller
    states it in ``gap_basis``. An unreadable symlink producing no gap would make an
    unmeasured detector look like a fully observed one, which is the fail-open shape this
    project has already been bitten by (BACKUP-HARDEN 1).
    """
    if series_start is None:
        return [], []
    reg = STREAMS_BY_KEY if by_key is None else by_key
    out: list[dict[str, Any]] = []
    unmeasured: list[str] = []

    own = detector_gap(stream, series_start)
    if own:
        out.append(own)
    if stream.detector_timer and _timer_enabled(stream.detector_timer) is None:
        unmeasured.append(stream.detector_timer)

    for key in stream.contributes_from:
        contrib = reg.get(key)
        if contrib is None:
            unmeasured.append(f"{key} (not in the stream registry)")
            continue
        if not contrib.detector_timer:
            unmeasured.append(f"{key} (contributes but owns no timer)")
            continue
        enabled = _timer_enabled(contrib.detector_timer)
        if enabled is None:
            unmeasured.append(contrib.detector_timer)
            continue
        if enabled > series_start:
            out.append(_detector_window(contrib.detector_timer, series_start, enabled))
    return out, unmeasured


def _kind_of(gap: Any) -> str:
    """A gap's kind whether it is still a dict or already a `CoverageGap`.

    ⚠ Deliberate duck typing rather than importing the pydantic model: this module is the one
    the models' own contract points at, and importing back into it would couple the derivation
    to the serialisation layer. :func:`fraction_gaps` is called after the dicts become models,
    so it must read both shapes.
    """
    return gap["kind"] if isinstance(gap, dict) else str(getattr(gap, "kind", ""))


def fraction_gaps(stream: Stream, host_gaps_: list[Any], gaps: list[Any]) -> list[Any]:
    """⛔ WHICH WINDOWS COUNT AGAINST ``observed_fraction`` — and why a composite differs.

    The fraction answers ONE question: what share of the span was the series **as a whole**
    unobserved? For a single-detector stream every kind answers it — host off, feed down, or
    detector not running all mean nothing was recorded — so all three count.

    ⛔ FOR A COMPOSITE THEY DO NOT, and including them is not conservative, it is false.
    MEASURED on 2026-08-04 for `hull_multi_pattern`: the five contributors' detector windows
    union to 23.43 d of a 23.64 d span, because `gnss_anomaly` only came onto a timer four
    hours ago. Counting them gives observed_fraction = 0.0088 — "we observed essentially
    nothing" — for a series that recorded 7,514 ais_gap and 1,143 gnss_anomaly rows in exactly
    that period. A detector window on a composite blinds ONE axis of six; the series kept
    measuring on the others, so the count inside it is DEPRESSED BY AN UNKNOWN AMOUNT, which is
    not the same claim as "unobserved" and cannot be folded into one scalar.

    So a composite's fraction counts the windows that blind EVERY axis at once — ``host`` (the
    machine was off) and ``source`` (the AIS feed all six detectors read was down) — and its
    detector windows stay in ``gaps`` where they do their real work: telling a consumer where to
    break the line. ⚠ The exclusion is stated in ``observed_fraction_basis``, never left to be
    inferred from a number that looks plausible.
    """
    if not stream.contributes_from:
        return host_gaps_ + gaps
    return host_gaps_ + [g for g in gaps if _kind_of(g) != "detector"]


def observed_fraction_basis(stream: Stream, unmeasured: list[str]) -> str:
    """What the fraction counted — so a plausible-looking number is never read as more than it
    is. ⛔ Names the excluded kind for a composite, and names any detector that could not be
    measured at all (the fraction is then an UPPER BOUND, and says so)."""
    if stream.contributes_from:
        base = (
            "host + source windows only — the kinds that blind every contributing detector at "
            f"once. ⛔ The {len(stream.contributes_from)} contributors' `detector` windows are "
            "LISTED IN gaps[] but EXCLUDED here: each blinds one axis of this composite, not "
            "the series, so inside them the count is depressed by an unknown amount rather "
            "than unobserved. Including them reads 0.0088 for a period holding 8,657 rows."
        )
    else:
        base = "host + source + detector windows, unioned before subtracting (all three blind this stream entirely)"
    if unmeasured:
        base += (" ⛔ UPPER BOUND — these detectors could not be measured, so their windows are "
                 "absent from the calculation: " + ", ".join(unmeasured))
    return base


async def source_gaps(pool, stream: Stream, now: datetime | None = None) -> list[dict[str, Any]]:
    """Upstream-feed outages for ONE stream, while the host was up.

    ⛔ REUSES :func:`gap_detect.reconstruct_outage_windows` — the same machinery DISK-1 used
    for the 45.46 % figure and that `detect_ais_gaps` / `detect_gnss_anomaly` / the port-visit
    endpoint already share. A second outage calculation beside it would be free to disagree
    with the detectors, and then no number would be authoritative.

    ⚠ Only streams that RECORD feed events can be reconstructed this way. Measured, that is
    `aisstream` alone: `signal_events` holds feed_connect/feed_disconnect for aisstream, but
    only `collector_run` markers for celestrak/spacetrack and NOTHING for any air source. For
    those streams this returns [] and the reason travels with the contract
    (:func:`source_gap_basis`) — an unreconstructable stream must not look like a perfect one.
    """
    if not stream.signal_source:
        return []
    now = now or _now()
    ev = await pool.fetch(
        "SELECT ts, kind FROM signal_events WHERE source = $1 "
        "AND kind IN ('feed_disconnect','feed_connect') ORDER BY ts", stream.signal_source)
    if not ev:
        return []
    cts = [r["ts"] for r in ev if r["kind"] == "feed_connect"]
    lp = await pool.fetch(
        f"SELECT c AS ct, (SELECT max(v.{stream.ts_column}) FROM {stream.table} v "
        f"WHERE v.source = $2 AND v.{stream.ts_column} < c) AS last_pos "
        "FROM unnest($1::timestamptz[]) AS c", cts, stream.signal_source
    ) if stream.table == "vessel_states" else []
    windows = reconstruct_outage_windows(
        [(r["ts"], r["kind"]) for r in ev], now,
        {r["ct"]: r["last_pos"] for r in lp} or None)
    return [{
        "start": w.start, "end": w.end, "kind": "source",
        "basis": w.basis + ("-inferred" if w.inferred else ""),
        "source": stream.signal_source,
        "seconds": round((w.end - w.start).total_seconds(), 1),
        "note": "upstream feed down while the host was up",
    } for w in windows]


def source_gap_basis(stream: Stream) -> str:
    """Why a stream's `gaps[]` is what it is — including when it cannot be computed."""
    if stream.signal_source:
        return f"source gaps: signal_events({stream.signal_source}) via reconstruct_outage_windows"
    # ⚠ Scoped wording on purpose: this describes the SOURCE reconstruction only. A stream can
    # still carry a detector gap while source gaps are unavailable, and an unqualified
    # "UNAVAILABLE" beside a populated gaps[] would read as a contradiction.
    return ("source gaps: UNAVAILABLE — this stream records no feed_connect/feed_disconnect "
            "events, so upstream outages cannot be reconstructed for it; host gaps (and any "
            "detector gap) still apply and are listed")
