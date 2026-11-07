/**
 * UI-3 — THE TRUTH RULES, AS TYPES.
 *
 * ⛔ THE POINT OF THIS FILE IS THAT THE DISHONEST DRAWING IS NOT EXPRESSIBLE. Nine constraints come
 * out of the seven contracts, and every one of them has already been violated somewhere in this
 * project's history by a careful author who simply forgot. So none of them is a comment asking for
 * care — each is a type, a required argument, or a different return type that a chart cannot accept.
 *
 * ⛔ CHARTS NEVER SEE `buckets`. `prepare()` consumes the raw payload and returns SEGMENTS. There is
 * no exported path from a payload to a point list, so "connect the line across the gap" is not a
 * mistake a chart can make — it never holds the points that would let it.
 *
 * ⚠ AND THE BREAK IS READ FROM DECLARED WINDOWS, NOT FROM BUCKET ADJACENCY. AltitudeChart today
 * breaks when a gap exceeds 1.5x the bucket size (measured). That inference is not a substitute for
 * the contract: a declared outage SHORTER than 1.5 buckets gets connected, and a bucket that is
 * merely absent is indistinguishable from one the host was off for. `host_gaps` + `gaps` +
 * `series_start` are the authority here; adjacency is not consulted at all.
 */

import type { CoverageGap, SeriesCoverage } from "../../api/types";
import type { Measure, Unit } from "./units";
import { measure, unitOf } from "./units";

/* ============================================================================================
 * Constraint 2 — `kind` is required, everywhere, with no default
 * ========================================================================================== */

/** The three causes, never collapsed. Mirrors the wire's closed union deliberately. */
export type GapKind = CoverageGap["kind"];

/** ⛔ NO DEFAULT AND NO OPTIONALITY. `kind` and `basis` both required: a window without its cause is
 *  a window a reader will attribute to whichever cause they already believe. */
export interface Window {
  readonly startMs: number;
  readonly endMs: number;
  readonly kind: GapKind;
  readonly basis: string;
  readonly source: string;
  readonly note: string;
}

/** ⛔ THE DISPLAY SEAM. Any renderer of a gap must take `kind` as a REQUIRED positional argument, so
 *  dropping it is a compile error rather than an omission nobody notices in review. UI-4's chart
 *  furniture is expected to implement this signature. */
export type GapRenderer<T> = (kind: GapKind, window: Window) => T;

export const describeGap: GapRenderer<string> = (kind, w) => {
  const label: Record<GapKind, string> = {
    host: "المضيف كان مطفأً — كل التيّارات تتوقّف معاً",
    source: "المصدر انقطع والمضيف يعمل",
    detector: "الكاشف لم يكن يعمل — العدّ محدود بالكاشف لا بالظاهرة",
  };
  return `${label[kind]} · الأساس: ${w.basis}${w.source ? ` (${w.source})` : ""}`;
};

/* ============================================================================================
 * Constraint 5 — `not_a_series` returns a DIFFERENT TYPE
 * ========================================================================================== */

/** ⛔ A stream the backend declares is not a series. Carries the reason and ⛔ NO segments, so a
 *  chart component typed to `PreparedSeries` cannot accept it. Fail-closed by construction. */
export interface NotASeries {
  readonly kind: "not-a-series";
  readonly stream: string;
  /** the backend's own sentence, surfaced verbatim */
  readonly reason: string;
}

/* ============================================================================================
 * Constraint 6 — a non-probative `gap_basis` forces non-empty disclosures
 * ========================================================================================== */

/** Something the reader MUST be told before the numbers mean anything. */
export interface Disclosure {
  readonly code: "gap-basis-unavailable" | "fraction-basis" | "unit-choice" | "pre-observation";
  readonly text: string;
}

/** ⛔ THE HARDEST RULE IN THE PHASE, encoded as a non-empty tuple type. For AIR, `gap_basis` reads
 *  UNAVAILABLE: an upstream air outage that did NOT coincide with a host shutdown is not represented
 *  AT ALL, and `host_gaps` alone protects the series. `[Disclosure, ...Disclosure[]]` cannot be
 *  satisfied by `[]`, so a prepared series can never carry an empty disclosure list. */
export type Disclosures = readonly [Disclosure, ...Disclosure[]];

/* ============================================================================================
 * Constraints 1 + 8 — segments, and no zero-fill
 * ========================================================================================== */

export interface Point<U extends Unit> {
  readonly tMs: number;
  readonly value: Measure<U>;
}

/** A run of points with NO declared window inside it. ⛔ The only thing a chart may connect. */
export interface Segment<U extends Unit> {
  readonly points: readonly Point<U>[];
}

/** ⛔ Constraint 8 — the axis starts at `series_start`, and everything before it is a NAMED region,
 *  not zero. Filling it with 0 asserts "nothing happened"; the truth is "nobody was looking". */
export interface UnobservedRegion {
  readonly startMs: number;
  readonly endMs: number;
  readonly why: "before-collection" | GapKind;
}

/** Constraint 7 — the air key is COMPOSITE. ⛔ No optional `source` to forget. */
export interface SplitKey {
  readonly country: string;
  readonly source: string;
}
/** ⛔ Joined with an ESCAPED NUL, and the escape is the point: a raw NUL byte landed here on
 *  the first write and git classified this whole file as BINARY — no diff, no review, and the
 *  byte itself invisible. `\u0000` cannot occur in a country code or a source name, so the
 *  composite key is collision-free, and written as an escape it stays reviewable text. */
export const splitKeyId = (k: SplitKey): string => `${k.country}\u0000${k.source}`;

/** One split of one series: its key, its segments, and nothing a chart could misread. */
export interface PreparedSplit<U extends Unit> {
  readonly key: SplitKey | string;
  readonly segments: readonly Segment<U>[];
}

export interface PreparedSeries<U extends Unit> {
  readonly kind: "series";
  readonly stream: string;
  readonly unit: U;
  /** ⛔ the axis domain, from `series_start`/`series_end` — never from the bucket extremes */
  readonly axisStartMs: number;
  readonly axisEndMs: number;
  readonly splits: readonly PreparedSplit<U>[];
  /** every declared window, unioned and clipped; charts mark these, they do not span them */
  readonly windows: readonly Window[];
  readonly unobserved: readonly UnobservedRegion[];
  /** ⛔ non-empty by type */
  readonly disclosures: Disclosures;
}

/** What `prepare` can return. ⛔ A caller must discriminate; there is no way to get segments out of
 *  a `NotASeries` or a `PrepareRefusal`. */
export type Prepared<U extends Unit> = PreparedSeries<U> | NotASeries | PrepareRefusal;

/** ⛔ Fail-closed: coverage absent, unit unrecognised, or no series start. Refusing is a VALUE the
 *  UI must render, not an exception to swallow. */
export interface PrepareRefusal {
  readonly kind: "refused";
  readonly reason: string;
}

/* ============================================================================================
 * Constraint 3 — no ratio is computed here at all
 * ========================================================================================== */

/** ⛔ THE ONLY RATIO PATH. `cutoff` is a REQUIRED parameter, so a caller cannot compute a share
 *  without having gone and looked for the frame boundary. When the compared span crosses it the
 *  answer is not a number — the two denominators mean different things. */
export type RatioResult =
  | { readonly kind: "ratio"; readonly value: number }
  | { readonly kind: "incomparable"; readonly reason: string };

export function ratio(
  numerator: number,
  denominator: number,
  spanStartMs: number,
  spanEndMs: number,
  /** ⛔ `coverage.ratio_cutoff` — pass it even when null. ⚠ The backend leaves it null for every
   *  non-air stream, so the rule applies itself to air and cannot be misapplied to the sea. */
  cutoff: string | null,
): RatioResult {
  if (denominator === 0) {
    return { kind: "incomparable", reason: "المقام صفر — لا نسبة" };
  }
  if (cutoff !== null) {
    const c = Date.parse(cutoff);
    if (spanStartMs < c && spanEndMs > c) {
      return {
        kind: "incomparable",
        reason:
          "المدى يعبر حدّ الإطار (2026-07-29T13:55:36Z): الأعداد المطلقة تعبره والنسب لا — " +
          "المقام يتغيّر معناه، فالنسبتان تقارنان مجتمعين مختلفين.",
      };
    }
  }
  return { kind: "ratio", value: numerator / denominator };
}

/* ============================================================================================
 * Constraint 9 (UI-0) — the fraction is never shown without its basis
 * ========================================================================================== */

export type FractionDisplay =
  | { readonly kind: "fraction"; readonly value: number; readonly basis: string }
  | { readonly kind: "unavailable"; readonly reason: string };

/** ⛔ Returns the value and its basis TOGETHER or neither. On a composite series the contributors'
 *  detector windows are listed in `gaps` but excluded from the fraction, so a bare number invites
 *  exactly the wrong reading — and an empty `observed_fraction_basis` is itself a refusal. */
export function observedFraction(coverage: SeriesCoverage): FractionDisplay {
  if (coverage.observed_fraction === null) {
    return { kind: "unavailable", reason: "الكسر غير محسوب لهذا التيّار" };
  }
  if (!coverage.observed_fraction_basis.trim()) {
    return {
      kind: "unavailable",
      reason: "⛔ الكسر موجود بلا أساس معلَن — لا يُعرض رقم لا نعرف ما عدّه",
    };
  }
  return {
    kind: "fraction",
    value: coverage.observed_fraction,
    basis: coverage.observed_fraction_basis,
  };
}

/* ============================================================================================
 * prepare()
 * ========================================================================================== */

const toWindow = (g: CoverageGap): Window => ({
  startMs: Date.parse(g.start),
  endMs: Date.parse(g.end),
  kind: g.kind,
  basis: g.basis,
  source: g.source,
  note: g.note,
});

/** Union overlapping windows of the SAME kind. ⛔ Kinds are never merged with each other — that is
 *  constraint 2 again: a host outage inside a detector window is two facts, not one. */
function unionByKind(ws: readonly Window[]): Window[] {
  const out: Window[] = [];
  for (const kind of ["host", "source", "detector"] as const) {
    const of = ws.filter((w) => w.kind === kind).sort((a, b) => a.startMs - b.startMs);
    for (const w of of) {
      const last = out[out.length - 1];
      if (last && last.kind === kind && w.startMs <= last.endMs) {
        if (w.endMs > last.endMs) out[out.length - 1] = { ...last, endMs: w.endMs };
      } else {
        out.push(w);
      }
    }
  }
  return out.sort((a, b) => a.startMs - b.startMs);
}

export interface RawBucket {
  /** ISO timestamp as the wire sends it */
  readonly bucket: string;
  readonly key: SplitKey | string;
  readonly value: number;
}

/**
 * ⛔ THE ONE DOOR from a payload to something drawable.
 *
 * @param buckets  already keyed by the caller (which knows its own contract's field names) — this
 *                 function never guesses whether the count lives in `count`, `airframes` or
 *                 `distinct_hulls`.
 * @param coverage the contract's embedded coverage. ⛔ Required; there is no overload without it.
 */
export function prepare<U extends Unit>(
  buckets: readonly RawBucket[],
  coverage: SeriesCoverage | null,
  extraDisclosures: readonly Disclosure[] = [],
): Prepared<U> {
  if (coverage === null) {
    return { kind: "refused", reason: "⛔ لا تغطية مضمَّنة — لا تُرسَم سلسلة بلا نوافذها" };
  }

  // ---- constraint 5, before anything else: this may not be a series at all ----
  if (coverage.not_a_series !== null) {
    return { kind: "not-a-series", stream: coverage.stream, reason: coverage.not_a_series };
  }

  // ---- constraint 4: the unit is resolved to a type, or we refuse ----
  const unit = unitOf(coverage.unit) as U | null;
  if (unit === null) {
    return {
      kind: "refused",
      reason: `⛔ وحدة غير معروفة: «${coverage.unit}» — لا محور بلا وحدة`,
    };
  }

  // ---- constraint 8: the axis comes from series_start, never from the buckets ----
  if (coverage.series_start === null) {
    return { kind: "refused", reason: "⛔ لا series_start — لا بداية محور" };
  }
  const axisStartMs = Date.parse(coverage.series_start);
  const axisEndMs = coverage.series_end !== null
    ? Date.parse(coverage.series_end)
    : Date.parse(coverage.computed_at);

  // ---- constraint 1: BOTH lists, unioned, clipped to the axis ----
  const windows = unionByKind(
    [...coverage.host_gaps, ...coverage.gaps]
      .map(toWindow)
      .filter((w) => Number.isFinite(w.startMs) && Number.isFinite(w.endMs))
      .map((w) => ({
        ...w,
        startMs: Math.max(w.startMs, axisStartMs),
        endMs: Math.min(w.endMs, axisEndMs),
      }))
      .filter((w) => w.endMs > w.startMs),
  );

  // ---- constraint 6: disclosures, non-empty BY TYPE ----
  const disclosures: Disclosure[] = [];
  const basis = coverage.gap_basis;
  if (!basis || /unavailable/i.test(basis)) {
    disclosures.push({
      code: "gap-basis-unavailable",
      text:
        "⛔ انقطاع المصدر غير مُمثَّل في هذه السلسلة: gap_basis غير مُثبِت " +
        `(«${basis || "فارغ"}»). انقطاعٌ لم يصادف إطفاء مضيف لا يظهر إطلاقاً، ` +
        "وفجوات المضيف وحدها تحمي هذا الخطّ.",
    });
  }
  const frac = observedFraction(coverage);
  disclosures.push(
    frac.kind === "fraction"
      ? { code: "fraction-basis", text: `النسبة المرصودة ${frac.value} — ${frac.basis}` }
      : { code: "fraction-basis", text: frac.reason },
  );
  if (unit === "hull-days") {
    disclosures.push({
      code: "unit-choice",
      text:
        "الوحدة بدن-يوم: بدن حاضر عشرة أيام يساهم بعشرة. ⚠ ليست بديلاً منقوصاً عن عدّ الأبدان — " +
        "هي ترجيح للحضور عبر الزمن، وهو الأنسب لسؤال «أي الدول تتصدّر عبر الزمن».",
    });
  }
  disclosures.push(...extraDisclosures);
  disclosures.push(...coverage.caveats.map((t) => ({ code: "pre-observation" as const, text: t })));
  // ⛔ the type demands one; the fraction disclosure above always supplies it, and this assertion
  // documents that invariant rather than hiding a possible empty list behind a cast.
  const nonEmpty = disclosures as unknown as Disclosures;
  if (disclosures.length === 0) {
    return { kind: "refused", reason: "⛔ لا إفصاحات — تعذّر بناء الإفصاح الإلزامي" };
  }

  // ---- constraint 7 + 1: split, then cut at every window. ⛔ no zero-fill anywhere ----
  const byKey = new Map<string, { key: SplitKey | string; pts: Point<U>[] }>();
  for (const b of buckets) {
    const id = typeof b.key === "string" ? b.key : splitKeyId(b.key);
    const t = Date.parse(b.bucket);
    if (!Number.isFinite(t)) continue;
    let e = byKey.get(id);
    if (!e) { e = { key: b.key, pts: [] }; byKey.set(id, e); }
    e.pts.push({ tMs: t, value: measure<U>(b.value, unit) });
  }

  const splits: PreparedSplit<U>[] = [];
  for (const { key, pts } of byKey.values()) {
    pts.sort((a, b) => a.tMs - b.tMs);
    const segments: Segment<U>[] = [];
    let cur: Point<U>[] = [];
    for (const p of pts) {
      const insideWindow = windows.some((w) => p.tMs >= w.startMs && p.tMs < w.endMs);
      const crossed = cur.length > 0 &&
        windows.some((w) => w.startMs >= cur[cur.length - 1].tMs && w.endMs <= p.tMs);
      if (insideWindow || crossed) {
        if (cur.length) segments.push({ points: cur });
        cur = insideWindow ? [] : [p];
        continue;
      }
      cur.push(p);
    }
    if (cur.length) segments.push({ points: cur });
    splits.push({ key, segments });
  }

  const unobserved: UnobservedRegion[] = windows.map((w) => ({
    startMs: w.startMs, endMs: w.endMs, why: w.kind,
  }));

  return {
    kind: "series",
    stream: coverage.stream,
    unit,
    axisStartMs,
    axisEndMs,
    splits,
    windows,
    unobserved,
    disclosures: nonEmpty,
  };
}
