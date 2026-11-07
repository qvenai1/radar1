"""Coverage contract + time-series — /coverage, DASH-BOARD-2 maritime series, DASH-BOARD-3 air series."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from ..gap_detect import METHOD as GAP_METHOD
from ..air_daily import BASE_WHERE as AIR_BASE_WHERE, agg_sql
from ..coverage import (
    FRAME_BOUNDARY, STREAMS, STREAMS_BY_KEY,
    detector_gaps as cov_detector_gaps,
    fraction_gaps as cov_fraction_gaps,
    host_gap_basis as cov_host_gap_basis,
    host_gaps as cov_host_gaps,
    observed_fraction_basis as cov_fraction_basis,
    source_gap_basis as cov_source_gap_basis,
    source_gaps as cov_source_gaps,
)
from ..models import (
    CoverageGap, CoverageResponse, SeriesCoverage,
    AirCountryDaily, AirCountrySeries, CountryBucket, HullDayBucket,
    FlagDistributionResponse, FlagSlice, MaritimeSeries, RendezvousPoint,
    RendezvousPointsResponse, SeriesBucket,
)

from .shared import _db

router = APIRouter()


#: ⛔ Surfaced on every DASH-BOARD-2 series: the coverage is embedded, and this says to read it.
SERIES_CAVEAT = (
    "اقرأ coverage.series_start وقائمتَي الفجوات قبل الرسم، واقطع الخط عند كل نافذة — الفجوات "
    "ثلاثة أنواع: host (المضيف مطفأ) · source (المصدر انقطع) · detector (لم يكن أحد ينظر). "
    "⛔ ولا تجمع عبر split_by، ولا تجمع هذه السلسلة مع سلسلة جوّية أو فضائية: راجع unit."
)


#: ⛔ Surfaced on every coverage response: the contract exists to be READ, not merely served.
COVERAGE_CAVEAT = (
    "اقرأ series_start وقائمتَي الفجوات قبل رسم أي سلسلة، واقطع الخط عند كل نافذة فيهما — "
    "خطٌّ موصول عبر فجوة يدّعي قياساً لم يُؤخذ. الفجوات ثلاثة أنواع لا نوع واحد: host (المضيف "
    "مطفأ — عامّة لكل التيّارات) · source (المصدر انقطع والمضيف يعمل) · detector (الظاهرة وقعت "
    "ولم يكن أحد ينظر). ولا مجموع عابر للمجالات: راجع unit لكل سلسلة."
)


def _clip(g: CoverageGap, start, end) -> CoverageGap | None:
    """A gap intersected with a stream's span, or None when it falls entirely outside it."""
    if start is None or end is None:
        return g
    a, b = max(g.start, start), min(g.end, end)
    if b <= a:
        return None
    if (a, b) == (g.start, g.end):
        return g
    return g.model_copy(update={"start": a, "end": b, "seconds": round((b - a).total_seconds(), 1)})


def _union_seconds(gaps: list[CoverageGap]) -> float:
    """Total unobserved time with overlaps counted ONCE — the three gap kinds nest freely."""
    if not gaps:
        return 0.0
    iv = sorted(((g.start, g.end) for g in gaps), key=lambda p: p[0])
    total, cur_a, cur_b = 0.0, *iv[0]
    for a, b in iv[1:]:
        if a > cur_b:
            total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        elif b > cur_b:
            cur_b = b
    return total + (cur_b - cur_a).total_seconds()


async def build_series_coverage(pool, st, now: datetime) -> SeriesCoverage:
    """⛔ THE ONE PLACE A STREAM'S COVERAGE IS BUILT (DASH-BOARD-0).

    Extracted from the /coverage endpoint in DASH-BOARD-2 so the four maritime question
    contracts can EMBED their coverage rather than recompute it. A second builder beside this
    one would be free to disagree with /coverage about the same stream, and then neither
    number would be authoritative — the same reason `reconstruct_outage_windows` is never
    reimplemented.
    """
    start = end = None
    rows = None
    try:
        where = ""
        if st.table == "maritime_events":
            where = f" WHERE event_type = '{st.key.removesuffix('_events')}'"
        r = await pool.fetchrow(
            f"SELECT min({st.ts_column}) a, max({st.ts_column}) b, count(*) n "
            f"FROM {st.table}{where}")
        start, end, rows = r["a"], r["b"], r["n"]
    except Exception:                                            # noqa: BLE001
        pass

    hg = [CoverageGap(**g) for g in await asyncio.to_thread(cov_host_gaps, now)]
    gaps = [CoverageGap(**g) for g in await cov_source_gaps(pool, st, now)]
    # ⛔ UI-0 — EVERY detector window that limits this series, its own AND each contributor's.
    # A composite embedding one contributor's window draws the others' deployment dates as
    # movements in the phenomenon (measured: multi-pattern listed 1 of 5).
    dgaps, unmeasured = cov_detector_gaps(st, start)
    gaps.extend(CoverageGap(**g) for g in dgaps)

    # ⛔ CLIP EVERY GAP TO THIS STREAM'S OWN SPAN. The boot table reaches back months before
    # any stream began; a February host outage is not a hole in a July series, and shipping it
    # would bury the ones that matter under noise.
    stream_hg = [g for g in (_clip(g, start, end) for g in hg) if g]
    gaps = [g for g in (_clip(g, start, end) for g in gaps) if g]

    # ⛔ UNION BEFORE SUBTRACTING. The three kinds OVERLAP by construction — a detector window
    # spans host outages inside it — so summing double-counts and drives the fraction to a
    # false 0 (measured: this exact bug reported 0.0 for five streams).
    # ⛔ AND UI-0: `cov_fraction_gaps` decides WHICH windows are inputs. For a composite the
    # contributors' detector windows are listed in `gaps` but excluded here — each blinds one
    # axis, not the series, and including them reads 0.0088 over a period holding 8,657 rows.
    frac = None
    if start and end and end > start:
        span = (end - start).total_seconds()
        counted = cov_fraction_gaps(st, stream_hg, gaps)
        frac = round(max(0.0, min(1.0, 1.0 - _union_seconds(counted) / span)), 4)

    # ⛔ An unmeasurable detector must not look like a fully observed one: `detector_gaps`
    # reports what it could not read, and it travels in BOTH basis fields.
    gap_basis = cov_source_gap_basis(st)
    if unmeasured:
        gap_basis += (" ⛔ DETECTOR WINDOWS MISSING — not measured for: "
                      + ", ".join(unmeasured) + " (this list is incomplete, not empty)")

    return SeriesCoverage(
        stream=st.key, domain=st.domain, unit=st.unit, source=st.source,
        series_start=start, series_end=end, rows=rows, computed_at=now,
        host_gaps=stream_hg, gaps=gaps,
        host_gap_basis=cov_host_gap_basis(), gap_basis=gap_basis,
        observed_fraction=frac,
        observed_fraction_basis=cov_fraction_basis(st, unmeasured),
        ratio_cutoff=FRAME_BOUNDARY if st.domain == "air" else None,
        ratio_cutoff_note=(
            "⛔ Absolute counts may cross this instant; RATIOS MAY NOT. The upstream air "
            "sampling frame narrowed from global to the AOI here, so every denominator "
            "changes meaning (country IS NULL: 96.05% → 29.6% on adsbexchange)."
            if st.domain == "air" else ""),
        not_a_series=st.not_a_series, caveats=list(st.caveats),
    )


@router.get("/coverage", response_model=CoverageResponse)
async def coverage(request: Request, stream: str | None = None) -> CoverageResponse:
    """DASH-BOARD-0 — what was actually observed, for every series the dashboard may draw.

    ⛔ THIS ENDPOINT PRECEDES EVERY TIME SERIES. The project's rule for this phase is that a
    contract able to express a year but unable to express «this period was not observed» is
    the more dangerous of the two, so the shape here is deliberately built for long windows
    AND for their holes. A consumer reads `series_start` and both gap lists BEFORE plotting,
    and breaks the line across every window it finds; a connected line across a gap asserts a
    measurement nobody took.

    ⛔ THE THREE GAP KINDS ARE NOT INTERCHANGEABLE and each is derived from a source that
    maintains itself — nothing here is hand-filled:
      host     → the boot table (`journalctl --list-boots`); global, all streams at once
      source   → `signal_events` through `reconstruct_outage_windows`, the SAME machinery the
                 detectors use — not a second calculation that could disagree with them
      detector → the enable-mtime of the timer's own `timers.target.wants` symlink

    `ratio_cutoff` carries the 2026-07-29 13:55:36Z frame boundary: absolute counts may cross
    it, ratios may not (`country IS NULL` goes 96.05 % → 29.6 % on adsbexchange).
    """
    now = datetime.now(timezone.utc)
    try:
        pool = await _db(request).pool()
    except Exception:                                            # noqa: BLE001
        return CoverageResponse(available=False, generated_at=now, caveat=COVERAGE_CAVEAT)

    picked = [s for s in STREAMS if stream is None or s.key == stream]
    out = [await build_series_coverage(pool, st, now) for st in picked]
    return CoverageResponse(
        available=True, generated_at=now, frame_boundary=FRAME_BOUNDARY,
        caveat=COVERAGE_CAVEAT, streams=out)


# ===================== DASH-BOARD-2 — the four maritime question contracts =====================
#
# ⛔ EVERY ONE OF THESE EMBEDS ITS DASH-BOARD-0 COVERAGE via `build_series_coverage` — the same
# builder /coverage uses. None recomputes gaps, and none may: a second gap calculation would be
# free to disagree with /coverage about the same stream.
#
# ⛔ AND NONE OF THE FOUR CARRIES A RATIO CUTOFF. `ratio_cutoff` is AIR in origin — the upstream
# air sampling frame narrowed on 2026-07-29 13:55:36Z. Measured, no maritime stream is sourced
# from that frame (these read `maritime_events` / the hull views over AISStream positions), so
# `build_series_coverage` leaves it null for domain != "air" and it is NOT stamped here. Applying
# an air boundary to a sea series would invent a discontinuity that never happened.

_BUCKETS = {"hour": "hour", "day": "day", "week": "week", "month": "month"}


def _bucket_arg(bucket: str) -> str:
    if bucket not in _BUCKETS:
        raise HTTPException(422, f"bucket must be one of {sorted(_BUCKETS)}")
    return _BUCKETS[bucket]


@router.get("/maritime/series/ais-gaps", response_model=MaritimeSeries)
async def maritime_series_ais_gaps(
    request: Request, bucket: str = "day", hours: int | None = None,
) -> MaritimeSeries:
    """Owner question (ب) — is the frequency of AIS outages rising or falling?

    ⛔ THE CLASSIFICATION AXIS IS NEVER COLLAPSED, and that is the whole contract. Measured
    today: `stationary` 22,141 · `outage-excluded` 16,960 · `candidate` 7,456 · `edge` 2,658 ·
    `jamming-excluded` 300. **`outage-excluded` and `jamming-excluded` are OUR OWN downtime**,
    removed by the detector as confounds — they are not vessel behaviour. A single "AIS gaps"
    line summing all five would plot our outages as ships going dark, which inverts the
    meaning of the question being asked. So the response is split by classification and
    `split_by` says so; a client that sums across keys has broken the contract, not read it.

    ⚠ This series measures OUR DETECTOR, not the world — and the detector has been on a timer
    only since 2026-07-19, which the embedded coverage carries as a `detector` gap.
    """
    now = datetime.now(timezone.utc)
    try:
        pool = await _db(request).pool()
    except Exception:                                            # noqa: BLE001
        return MaritimeSeries(available=False, question="ب", caveat=SERIES_CAVEAT)
    b = _bucket_arg(bucket)
    since = now - timedelta(hours=hours) if hours else None
    rows = await pool.fetch(
        f"SELECT date_trunc('{b}', start_ts) AS bk, classification, count(*) AS n "
        "FROM maritime_events WHERE source = 'aisstream' AND event_type = 'ais_gap' "
        "AND ($1::timestamptz IS NULL OR start_ts >= $1) GROUP BY 1, 2 ORDER BY 1, 2", since)
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["classification"]] = totals.get(r["classification"], 0) + r["n"]
    return MaritimeSeries(
        question="ب — تكرار انقطاعات البثّ عبر الزمن",
        bucket=bucket, unit="detected AIS-gap events",
        source="scraplan ais-gap-detector-v1 over AISStream positions (maritime_events)",
        method=GAP_METHOD, caveat=SERIES_CAVEAT,
        split_by="classification",
        split_note=(
            "⛔ لا تجمع التصنيفات في رقم واحد: outage-excluded و jamming-excluded هي "
            "انقطاعاتنا نحن، أزالها الكاشف كمُربِكات — لا سلوك سفن. خطٌّ واحد يجمع الخمسة "
            "يرسم أعطالنا كأنها سفن أطفأت بثّها."),
        totals=totals,
        buckets=[SeriesBucket(bucket=r["bk"], key=r["classification"], count=r["n"])
                 for r in rows],
        coverage=await build_series_coverage(pool, STREAMS_BY_KEY["ais_gap_events"], now),
    )


@router.get("/maritime/series/multi-pattern", response_model=MaritimeSeries)
async def maritime_series_multi_pattern(
    request: Request, bucket: str = "week", hours: int | None = None,
) -> MaritimeSeries:
    """Owner question (د) — how many hulls carry more than one pattern, over time?

    ⛔ A TIME-BUCKETED VIEW OF AN EXISTING CONTRACT, NOT A SECOND CALCULATION. It reads
    `maritime_hull_events` — the same IMO-stitched view `_composite_hulls` and
    `/maritime/composite` rank from — so "how many" can never disagree with "which ones".

    ⚠ AND THE SERIES IS DOMINATED BY DETECTOR AVAILABILITY, NOT BY HULL BEHAVIOUR. Measured
    weekly today: 7 / 54 / 5 / 159 / 10. The 159 is the week `zone_dwell` and `identity_change`
    came onto timers (2026-07-29 15:11–15:12Z) and `gnss_anomaly` followed (2026-08-04) — a
    hull cannot show a second pattern while the detector for that pattern is not running. The
    embedded coverage carries those windows as `detector` gaps, and this series must not be
    read as a trend across them.

    ⛔ THAT LAST SENTENCE WAS FALSE UNTIL UI-0, and this is the correction. The coverage embedded
    here was `STREAMS_BY_KEY["zone_dwell_events"]` — ONE contributor of six. It carried that
    stream's identity, its unit (`detected zone-dwell sessions`, for a series whose unit is
    HULLS), its span, its row count (60, for a view holding 8,856 rows) and its fraction, plus
    exactly ONE of five detector windows. The `identity_change` and `gnss_anomaly` windows this
    docstring named as the cause of the 159 were absent from the payload that was supposed to
    prove it. It now embeds `hull_multi_pattern`, a composite stream whose `contributes_from`
    names all five timer-owning contributors, so every window is present.

    ⚠ SIX event types reach the view, not five: `rendezvous` and `identity_change`
    unconditionally, plus `ais_gap` / `gnss_anomaly` / `imo_integrity` / `zone_dwell` where
    classification = 'candidate' (migration 037). The sixth, `imo_integrity`, has NO timer — it
    is a hand-run backfill — so it owns no stream and no window; the composite's caveats state
    it, with its measured influence (1 event, 1 hull, 0 hulls made multi-pattern by it).
    """
    now = datetime.now(timezone.utc)
    try:
        pool = await _db(request).pool()
    except Exception:                                            # noqa: BLE001
        return MaritimeSeries(available=False, question="د", caveat=SERIES_CAVEAT)
    b = _bucket_arg(bucket)
    since = now - timedelta(hours=hours) if hours else None
    rows = await pool.fetch(
        f"WITH h AS (SELECT date_trunc('{b}', start_ts) AS bk, hull, "
        "                  count(DISTINCT event_type) AS n "
        "           FROM maritime_hull_events "
        "           WHERE ($1::timestamptz IS NULL OR start_ts >= $1) GROUP BY 1, 2) "
        "SELECT bk, count(*) FILTER (WHERE n > 1) AS multi, count(*) AS total "
        "FROM h GROUP BY 1 ORDER BY 1", since)
    out: list[SeriesBucket] = []
    totals = {"multi_pattern": 0, "hulls_with_any_event": 0}
    for r in rows:
        out.append(SeriesBucket(bucket=r["bk"], key="multi_pattern", count=r["multi"]))
        out.append(SeriesBucket(bucket=r["bk"], key="hulls_with_any_event", count=r["total"]))
        totals["multi_pattern"] += r["multi"]
        totals["hulls_with_any_event"] += r["total"]
    return MaritimeSeries(
        question="د — الأبدان متعدّدة الأنماط عبر الزمن",
        bucket=bucket, unit="hulls (IMO-stitched), not vessels and not events",
        source="maritime_hull_events — the same view /maritime/composite ranks from",
        method="hull-composite-v1", caveat=SERIES_CAVEAT,
        split_by="series",
        split_note=(
            "⛔ multi_pattern ⊂ hulls_with_any_event — الرقمان ليسا فئتين تُجمعان بل بسط ومقام. "
            "⚠ والسلسلة تحكمها إتاحة الكواشف: قفزة أسبوع 2026-07-27 هي نشر مؤقّتَي zone_dwell "
            "وidentity، لا زيادة في سلوك الأبدان — اقرأ gaps[] من نوع detector قبل أي قراءة اتجاه."),
        totals=totals, buckets=out,
        # ⛔ UI-0 — the COMPOSITE stream, not one contributor of six. See the docstring.
        coverage=await build_series_coverage(pool, STREAMS_BY_KEY["hull_multi_pattern"], now),
    )


@router.get("/maritime/series/rendezvous-points", response_model=RendezvousPointsResponse)
async def maritime_rendezvous_points(
    request: Request, hours: int | None = None, limit: int = 500,
) -> RendezvousPointsResponse:
    """Owner question (ج) — where do rendezvous cluster geographically?

    ⛔ THE HONEST ANSWER IS THAT N DOES NOT SUPPORT THE QUESTION AS ASKED. Measured: **47
    events over 24 days ≈ 2 per day**. A density surface, heat map or hex bin over that
    manufactures a spatial pattern out of sampling noise — the shape you would see is the
    shape of where two dozen arbitrary points happened to fall. So this contract returns the
    POINTS AS THEY ARE, with their count and span in the open, and states the refusal in the
    payload (`density_refused`) rather than in a comment a client cannot see.

    ⚠ Clustering becomes a real question when N is an order of magnitude larger. Until then
    "where do they cluster" is answered by looking at 47 leads on a map, one at a time.
    """
    now = datetime.now(timezone.utc)
    try:
        pool = await _db(request).pool()
    except Exception:                                            # noqa: BLE001
        return RendezvousPointsResponse(available=False, question="ج", caveat=SERIES_CAVEAT)
    since = now - timedelta(hours=hours) if hours else None
    rows = await pool.fetch(
        "SELECT mmsi, start_ts, end_ts, duration_s, start_lat, start_lon, end_lat, end_lon, "
        "classification FROM maritime_events WHERE source = 'aisstream' AND event_type = 'rendezvous' "
        "AND ($1::timestamptz IS NULL OR start_ts >= $1) ORDER BY start_ts DESC LIMIT $2",
        since, limit)
    span = None
    per_day = None
    if rows:
        lo = min(r["start_ts"] for r in rows)
        hi = max(r["start_ts"] for r in rows)
        span = round((hi - lo).total_seconds() / 86400, 2)
        per_day = round(len(rows) / span, 2) if span and span > 0 else None
    return RendezvousPointsResponse(
        question="ج — أين تتجمّع الالتقاءات جغرافياً",
        count=len(rows), span_days=span, per_day=per_day,
        unit="detected rendezvous events (candidate leads)",
        source="maritime_events (rendezvous-detector-v1) over AISStream positions",
        method="rendezvous-detector-v1", caveat=SERIES_CAVEAT,
        density_refused=(
            f"⛔ لا سطح كثافة ولا تسخين: {len(rows)} حدثاً على "
            f"{span if span else '—'} يوماً (~{per_day if per_day else '—'} في اليوم). "
            "أي تجميع مكاني على هذا العدد يصنع نمطاً من ضجيج العيّنة — الشكل الظاهر سيكون شكل "
            "مواقع بضع عشرات من النقاط لا شكل ظاهرة. النقاط تُعاد كما هي، وكل واحدة candidate: "
            "دليل تحقيق لا إثبات."),
        points=[RendezvousPoint(
            mmsi=r["mmsi"] or "", start_ts=r["start_ts"], end_ts=r["end_ts"],
            duration_s=r["duration_s"], start_lat=r["start_lat"], start_lon=r["start_lon"],
            end_lat=r["end_lat"], end_lon=r["end_lon"],
            classification=r["classification"] or "") for r in rows],
        coverage=await build_series_coverage(pool, STREAMS_BY_KEY["rendezvous_events"], now),
    )


@router.get("/maritime/flags/multi-pattern", response_model=FlagDistributionResponse)
async def maritime_flags_multi_pattern(request: Request) -> FlagDistributionResponse:
    """Owner question (هـ) — the registry-flag distribution of multi-pattern hulls.

    ⛔ THE DENOMINATOR TRAVELS WITH THE SHARES, because it is small. A flag holding 13 hulls
    is ~4 %, and ONE hull moves that by ~0.3 points — a percentage quoted without its
    denominator invites a precision the data does not have.

    ⛔ ONE ATTRIBUTION, NAMED. This uses `vessel_identity.flag_country` and says so in
    `attribution_field`. The project has a SECOND flag derivation — `flag_iso`, from the MMSI's
    MID prefix — and the two disagree by ~6 points (99.39 % vs 93.7 % coverage). They are not
    interchangeable and neither is presented as the other.

    ⛔ THE «غير منسوب» SLICE IS ALWAYS EMITTED, even at 100 % attribution. Dropping a
    zero-sized unattributed slice teaches a reader that attribution is total; it is not, the
    population changes, and the slice is where that will show.
    """
    now = datetime.now(timezone.utc)
    try:
        pool = await _db(request).pool()
    except Exception:                                            # noqa: BLE001
        return FlagDistributionResponse(available=False, question="هـ", caveat=SERIES_CAVEAT)
    rows = await pool.fetch(
        "WITH h AS (SELECT hull, mmsis FROM maritime_hull_composite WHERE n_pattern_types > 1), "
        "     f AS (SELECT h.hull, max(vi.flag_country) AS flag FROM h "
        "           LEFT JOIN LATERAL unnest(h.mmsis) AS m(mmsi) ON true "
        "           LEFT JOIN vessel_identity vi ON vi.mmsi = m.mmsi GROUP BY 1) "
        "SELECT flag, count(*) AS n FROM f GROUP BY 1 ORDER BY 2 DESC, 1")
    denom = sum(r["n"] for r in rows)
    unattr = sum(r["n"] for r in rows if not r["flag"])
    slices = [FlagSlice(
        flag=(r["flag"] or "غير منسوب"), hulls=r["n"],
        pct=round(100.0 * r["n"] / denom, 2) if denom else 0.0,
        attributed=bool(r["flag"])) for r in rows]
    if not any(not s.attributed for s in slices):
        # ⛔ emitted at zero rather than omitted — see the docstring
        slices.append(FlagSlice(flag="غير منسوب", hulls=0, pct=0.0, attributed=False))
    return FlagDistributionResponse(
        question="هـ — توزيع أعلام التسجيل في الأبدان متعدّدة الأنماط",
        denominator=denom,
        denominator_note=(
            f"المقام {denom} بدناً — صغير. بدن واحد يحرّك الحصّة ~"
            f"{round(100.0 / denom, 2) if denom else 0} نقطة، فلا تُقرأ النسب أدقّ من ذلك."),
        attribution_field="vessel_identity.flag_country",
        attribution_note=(
            "⛔ هذا الاشتقاق وحده. المشروع يملك اشتقاقاً ثانياً (flag_iso من بادئة MID في "
            "الـMMSI) يفترق عنه ~6 نقاط تغطيةً — ولا يُعرض أحدهما مكان الآخر."),
        attributed=denom - unattr, unattributed=unattr,
        unit="hulls (IMO-stitched) with >1 distinct pattern type",
        source="maritime_hull_composite × vessel_identity",
        method="hull-composite-v1", caveat=SERIES_CAVEAT,
        slices=slices,
        # ⛔ UI-0 — THE SAME DEFECT AS /maritime/series/multi-pattern, found by the item-4 audit.
        # This population IS the multi-pattern one (`n_pattern_types > 1` over the same six-
        # detector view), so it is detector-limited by every contributor — yet it embedded
        # `identity_change_events`: one contributor, whose unit reads «detected identity-change
        # events» for a response whose own `unit` two lines above says HULLS. Same composite.
        coverage=await build_series_coverage(pool, STREAMS_BY_KEY["hull_multi_pattern"], now),
    )


# ================== DASH-BOARD-3 — air country × time, ON DEMAND (no new storage) ==================
#
# ⛔ THE STORAGE THIS ITEM WAS SCOPED TO BUILD WAS NOT BUILT, and the measurement is why. Two
# findings, either of them sufficient:
#
#  1. THE ON-DEMAND QUERY IS ALREADY FAST — and faster over MORE data. Measured on the live
#     table: 1 d 279 ms · 3 d 793 ms · **7 d 1,537 ms** · 14 d 464 ms · **27 d / 78 M rows
#     506 ms** · the compressed pre-frame era alone (73.6 M rows) **350 ms**. The 7-day window
#     is the SLOWEST because `compress_after = 7 days` leaves exactly that window in the
#     rowstore, while everything older is columnstore — which aggregates four columns at
#     ~210 M rows/s. DASH-BOARD-pre's «27 d ≈ 8 s» was a linear extrapolation from one
#     uncompressed day; compression breaks linearity in our favour, by ~16×.
#     Projected to a YEAR on that measured basis: ~338 M compressed rows ≈ 1.6 s, plus the
#     constant ~1.5 s hot week = **~3.2 s** for a whole-year, all-country, all-source query —
#     and the hot part does NOT grow with the window.
#
#  2. ⛔ A CORRECT CONTINUOUS AGGREGATE IS NOT BUILDABLE HERE ANYWAY. The unit is DISTINCT
#     AIRFRAMES, and `count(distinct icao24)` is not decomposable: 1-minute distincts cannot
#     be rolled up to a day (an airframe seen in forty minutes would count forty times), and
#     daily distincts cannot be summed to a week. The existing `state_vectors_1m` works
#     precisely because it stores only `count(*)`, `sum()` and `max()` — all decomposable.
#     Doing this correctly needs an approximate-distinct sketch, and `timescaledb_toolkit` /
#     `hll` are **not available** on this instance (installed: plpgsql, timescaledb 2.28.2).
#     A fixed-granularity cagg would be silently WRONG at every other granularity, and
#     question (أ) asks how the ranking moves over time — i.e. at several.
#
# So: no cagg, no migration, and 9.2 GB of write amplification not spent on a 7.3 GB database.

AIR_POPULATION_COVERAGE = {"opensky": 74.46, "airplanes_live": 70.88, "adsbexchange": 41.19}


@router.get("/air/series/country", response_model=AirCountrySeries)
async def air_series_country(
    request: Request, bucket: str = "day", hours: int = 720,
    source: str | None = None, min_airframes: int = 1,
) -> AirCountrySeries:
    """Owner question (أ) — which countries lead, and how does the ranking move over time?

    ⛔ THE UNIT IS DISTINCT AIRFRAMES (`count(distinct icao24)`), not position reports and not
    events. A country with one aircraft circling all day is one airframe, not ten thousand
    rows. ⛔ No total may be taken across domains: sea counts detector events, space counts
    passes.

    ⛔ ALWAYS SPLIT BY SOURCE. opensky / adsbexchange / airplanes_live are different
    communities with measured hourly coverage of 74.46 % / 41.19 % / 70.88 %; folding them
    into one number manufactures a population that never existed.

    ⛔ `coverage.ratio_cutoff` carries 2026-07-29T13:55:36Z and **applies here** (unlike every
    maritime contract): absolute airframe counts may cross it, ratios may not.

    ⚠ AND THE COVERAGE IS WEAKER THAN IT LOOKS, which the contract says rather than hides:
    `coverage.gap_basis` is UNAVAILABLE for air because `signal_events` records
    feed_connect/feed_disconnect for `aisstream` only. An upstream air outage that did not
    coincide with a host shutdown is therefore **not represented in any gap list**. Only
    `host_gaps` protect this series.
    """
    now = datetime.now(timezone.utc)
    try:
        pool = await _db(request).pool()
    except Exception:                                            # noqa: BLE001
        return AirCountrySeries(available=False, question="أ", caveat=SERIES_CAVEAT)
    b = _bucket_arg(bucket)
    # ⛔ THE WINDOW IS BOUNDED BY A MEASURED NUMBER, not by taste. The marginal throughput of
    # this aggregate is ~32.5 M rows/s (measured: 944 k rows → 948 ms, 78 M → 3,302 ms), so a
    # full year at the current ~944 k rows/day is ~345 M rows ≈ **11.5 s** — not interactive,
    # and 11.5 s of parallel scan on a shared disk is exactly what must not be handed to a
    # dashboard refresh. 720 h (30 d) is the default because it is the largest window measured
    # under ~3.3 s. Longer windows are ALLOWED and not silently truncated — they are answered,
    # slowly, and `cost_note` says what was asked for.
    # The 8,784 h (366 d) ceiling keeps that policy — a full year answers in the measured
    # ~11.5 s — while closing the unbounded case an open, unauthenticated port could be
    # driven through repeatedly (security review finding 5).
    if not (1 <= hours <= 8784):
        raise HTTPException(422, "hours must be between 1 and 8784 (366 days)")
    since = now - timedelta(hours=hours)
    # Predicates are BUILT rather than guarded with `($1 IS NULL OR ts >= $1)`, so an absent
    # window leaves no clause for the planner to reason about at all.
    # ⚠ Measured honestly, this is worth ~10 %, not the 7× a first reading suggested — that
    # reading came from timing `SELECT count(*) FROM (…)`, a wrapper that lets the planner skip
    # the sort and the row materialisation this endpoint actually needs. The wrapper, not the
    # guard, was the difference. Kept because it is still the clearer form and still cheaper.
    where = [AIR_BASE_WHERE]
    args: list[Any] = []
    args.append(since)
    where.append(f"ts >= ${len(args)}")
    if source is not None:
        args.append(source)
        where.append(f"source = ${len(args)}")
    args.append(min_airframes)
    # ⛔ ONE DEFINITION, SHARED WITH THE NIGHTLY JOB (backend.air_daily.agg_sql). What counts
    # as "an airframe over a country from one feed" is decided in exactly one place, so the
    # live series and the summary table cannot drift apart on the classification, the distinct
    # key or the NOT NULL filter. Pinned by an equivalence test on a real day.
    rows = await pool.fetch(agg_sql(b, where, f"${len(args)}"), *args)
    totals: dict[str, int] = {}
    pops: set[str] = set()
    for r in rows:
        # ⛔ totals are per COUNTRY×SOURCE, never per country alone: summing an airframe seen
        # by two feeds would count it twice, and the two feeds are different populations.
        totals[f"{r['country']}|{r['source']}"] = \
            totals.get(f"{r['country']}|{r['source']}", 0) + r["distinct_hulls"]
        pops.add(r["source"])
    return AirCountrySeries(
        question="أ — أي الدول تتصدّر، وكيف يتغيّر الترتيب عبر الزمن",
        bucket=bucket,
        unit="distinct airframes (icao24) with a classified position over the country",
        method="on-demand aggregate over state_vectors.country (AOI polygon classifier)",
        caveat=SERIES_CAVEAT,
        split_by="country × source",
        split_note=(
            "⛔ لا تجمع عبر source: opensky وadsbexchange وairplanes_live مجتمعات مختلفة "
            "بتغطيات مقيسة 74.46% و41.19% و70.88% — وجمعها في رقم واحد يصنع مجتمعاً لم يوجد. "
            "⛔ والوحدة أبدان متمايزة لا رسائل موضع، فلا تُجمع مع سلسلة بحرية (أحداث) أو "
            "فضائية (ممرّات)."),
        window_hours=hours,
        cost_note=(
            f"نافذة {hours} ساعة. المقيس: 24س ~948ms · 168س ~1,431ms · 336س ~2,388ms · "
            f"720س (78M صفّ) ~3,302ms · وسنة (8,760س، ~345M صفّ) ≈11.5s. النوافذ الأطول "
            f"تُجاب ولا تُقتطع صامتةً — لكنها بطيئة، والرقم أعلاه هو الوعد."),
        populations=sorted(pops),
        population_note=(
            "التغطية الساعية المقيسة لكل مجتمع: "
            + " · ".join(f"{k} {v}%" for k, v in AIR_POPULATION_COVERAGE.items())),
        storage_note=(
            "⛔ يُحسب عند الطلب بلا تخزين جديد، والسبب مقيس لا مُفترض. الكلفة الحقيقية "
            "(الاستعلام كما هو، بلا غلاف count): يوم 948ms · 7 أيام 1,431ms · 14 يوماً "
            "2,388ms · 30 يوماً (78M صفّ) 3,302ms. الإنتاجية الحدّية ~32.5M صفّ/ث، فسنة "
            "(~345M صفّ) ≈11.5s — ⛔ ليست تفاعلية، ولهذا النافذة محدودة افتراضياً. "
            "⛔ وcagg صحيح غير ممكن لهذه الوحدة أصلاً: count(distinct icao24) غير قابل "
            "للتفكيك (دقائق لا تُجمع في يوم، وأيام لا تُجمع في أسبوع)، وextension الـ"
            "hyperloglog غير متاح على هذه النسخة (المثبَّت: plpgsql · timescaledb 2.28.2). "
            "فالتخزين قرار مالك: إمّا وحدة أخرى عند التبويب الخشن، أو تثبيت sketch."),
        totals=totals,
        buckets=[CountryBucket(bucket=r["bk"], country=r["country"], source=r["source"],
                               airframes=r["distinct_hulls"]) for r in rows],
        coverage=await build_series_coverage(pool, STREAMS_BY_KEY["air_states"], now),
    )


@router.get("/air/series/country-daily", response_model=AirCountryDaily)
async def air_series_country_daily(
    request: Request, days: int = Query(365, ge=1, le=730), country: str | None = None,
    source: str | None = None,
) -> AirCountryDaily:
    """DASH-BOARD-3b — the LONG air trend, from the daily summary table.

    ⛔ A SEPARATE ENDPOINT FROM `/air/series/country`, ON PURPOSE, AND THE REASON IS THE UNIT.
    That endpoint returns DISTINCT AIRFRAMES over the window it was asked for. This one reads
    pre-computed per-day distinct counts, so any multi-day sum of it is **HULL-DAYS** — an
    airframe present on ten days contributes 10. Serving two different units behind one path,
    switched by a flag, is precisely how a reader ends up adding them. They get two names.

    ⚠ And hull-days are arguably the BETTER measure for question (أ): they weight presence
    over time rather than mere appearance, so a country an airframe crossed once does not
    score like one it loitered over for a week.

    ⛔ ABSENT DAYS ARE ABSENT. The four host-outage days (2026-07-17 / 07-23 / 07-24 / 07-28)
    have no row and are not filled with zeros here or anywhere: a zero reads «no aircraft
    flew», an absent day reads «we did not observe». The table's `distinct_hulls > 0` CHECK
    makes the zero-fill fail at the schema rather than pass quietly.
    """
    now = datetime.now(timezone.utc)
    try:
        pool = await _db(request).pool()
    except Exception:                                            # noqa: BLE001
        return AirCountryDaily(available=False, question="أ", caveat=SERIES_CAVEAT)
    where = ["day >= (now() - ($1 || ' days')::interval)::date"]
    args: list[Any] = [str(days)]
    if country is not None:
        args.append(country)
        where.append(f"country = ${len(args)}")
    if source is not None:
        args.append(source)
        where.append(f"source = ${len(args)}")
    t0 = time.perf_counter()
    rows = await pool.fetch(
        "SELECT day, country, source, distinct_hulls, rows FROM air_country_daily "
        f"WHERE {' AND '.join(where)} ORDER BY day, distinct_hulls DESC", *args)
    query_ms = round((time.perf_counter() - t0) * 1000, 1)

    totals: dict[str, int] = {}
    for r in rows:
        # ⛔ keyed country|source, and the value is HULL-DAYS. No country-only total exists,
        # for the same reason as /air/series/country: two feeds are two populations.
        k = f"{r['country']}|{r['source']}"
        totals[k] = totals.get(k, 0) + r["distinct_hulls"]
    present = len({r["day"] for r in rows})
    return AirCountryDaily(
        question="أ — الاتجاه الجوّي الطويل، دولةً دولةً",
        unit="hull-days (بدن-يوم) — NOT distinct airframes",
        unit_warning=(
            "⛔ كل صفّ عدٌّ متمايز دقيق ليومه وحده، لكن جمع الأيام يعطي «بدن-يوم» لا «أبداناً "
            "متمايزة»: بدن حاضر عشرة أيام = 10. ⛔ ولا يوضع هذا الرقم قطّ باسم «عدد الأبدان» "
            "ولا بجوار العدّ المتمايز من /air/series/country بلا تمييز — وحدتان لا تُجمعان. "
            "⚠ وهو مقياس أدقّ للسؤال (أ) لا أنقص: يرجّح مدّة الحضور لا مجرّد الظهور."),
        source="air_country_daily ← state_vectors (same aggregate definition as the live series)",
        method="air-daily-v1 (backend.air_daily.agg_sql — one definition, two readers)",
        caveat=SERIES_CAVEAT,
        table_note=(
            "جدول ملخّص يوميّ — النمط الأول من نوعه في المشروع، أُضيف لأن cagg صحيح مستحيل "
            "لهذه الوحدة (count(distinct icao24) غير قابل للتفكيك، ولا hyperloglog متاح)."),
        days_present=present,
        days_absent_note=(
            "⛔ الأيام التي كان المضيف فيها مطفأً (2026-07-17 · 07-23 · 07-24 · 07-28) غائبة "
            "من buckets ولم تُملأ أصفاراً. الصفر يُقرأ «لا طائرات»، والغياب يُقرأ «لم نرصد»."),
        query_ms=query_ms, totals=totals,
        buckets=[HullDayBucket(day=r["day"], country=r["country"], source=r["source"],
                               distinct_hulls=r["distinct_hulls"], rows=r["rows"])
                 for r in rows],
        coverage=await build_series_coverage(pool, STREAMS_BY_KEY["air_states"], now),
    )
