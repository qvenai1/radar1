/**
 * UI-3 — THE TRUTH-RULE GATE. Nine constraints, and for each one a DETONATION: a state that
 * violates it, and the refusal or type error that state produces.
 *
 * ⛔ WHY DETONATION AND NOT ASSERTION. BE-SUMMARY-4's lesson: a gate that reports a defect and
 * passes is a gate that will be ignored. And the GNSS lesson: two tests passed for thirteen days
 * because the detector was dead and the expected value was a constant. So every check here changes
 * the INPUT and asserts the OUTPUT moved — nothing is compared to a frozen expectation.
 *
 * The compile-time constraints (units, required `kind`, non-empty disclosures) are proven by
 * feeding tsc a file that violates them and asserting it FAILS, then asserting the same file
 * without the violation PASSES. A type rule nobody has watched reject something is a claim.
 *
 *   node scripts/verify-series-rules.mjs
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const ROOT = path.join(import.meta.dirname, "..");
const pass = [], fail = [];
const ok = (name, cond, detail = "") => {
  (cond ? pass : fail).push(name);
  console.log(`  ${cond ? "✅" : "⛔"} ${name}${detail ? ` — ${detail}` : ""}`);
};

/* ---------------------------------------------------------------- compile-time detonations ---- */

const TMP = mkdtempSync(path.join(tmpdir(), "ui3-"));

/**
 * Typecheck a snippet against the real project types. Returns ONLY the diagnostics whose file is
 * the snippet itself.
 *
 * ⛔ THE FILTER IS THE POINT, and it is here because the first version of this gate did not have
 * it. Matching a bare /error TS/ over all of tsc's output means an unrelated diagnostic — from a
 * project file, or from `@types/*` when a flag is missing — reads as "the constraint rejected the
 * violation". A gate that can pass for a reason other than the one it claims is the shape UI-1
 * corrected in verify-dataviz (encode nothing; derive everything).
 */
function errorsIn(snippet, label) {
  const rel = path.join("src", "lib", "series", `__detonate_${label}.ts`);
  const f = path.join(ROOT, rel);
  writeFileSync(f, snippet);
  try {
    execFileSync("npx", ["tsc", "--noEmit", "--strict", "--target", "ES2022", "--module", "esnext",
                         "--moduleResolution", "bundler", "--skipLibCheck",
                         "--lib", "ES2022,DOM", f],
                 { cwd: ROOT, stdio: "pipe" });
    return [];
  } catch (e) {
    const out = (e.stdout?.toString() || "") + (e.stderr?.toString() || "");
    const mine = out.split("\n").filter((l) => l.includes(`__detonate_${label}.ts`) && /error TS/.test(l));
    const foreign = out.split("\n").filter((l) => /error TS/.test(l) && !l.includes(`__detonate_${label}.ts`));
    if (foreign.length) {
      // ⛔ never silently absorbed: a diagnostic from elsewhere means this run cannot be trusted
      console.log(`       ⚠ ${foreign.length} diagnostic(s) from OTHER files in run "${label}" ` +
                  `— excluded from the verdict: ${foreign[0].trim().slice(0, 70)}`);
    }
    return mine;
  } finally {
    rmSync(f, { force: true });
  }
}
/** true when the snippet itself was rejected. */
const rejects = (snippet, label) => errorsIn(snippet, label).length > 0;
/** true when the snippet itself is clean. */
const accepts = (snippet, label) => errorsIn(snippet, label).length === 0;

console.log("=== compile-time constraints — each proven by a type error ===");

// C4 — units
{
  const bad = `
import { measure, add } from "./units";
const a = measure(3, "airframes");
const h = measure(4, "hull-days");
export const boom = add(a, h);            // ⛔ must NOT typecheck
`;
  const good = `
import { measure, add } from "./units";
const a = measure(3, "airframes");
const b = measure(4, "airframes");
export const fine = add(a, b);
`;
  const e = errorsIn(bad, "unit_mix");
  ok("C4 adding airframes to hull-days is a type error", e.length > 0,
     e[0]?.trim().slice(0, 88) || "");
  ok("C4 …and adding two airframes still compiles", accepts(good, "unit_same"));
}

// C2 — kind is required, no default
{
  const bad = `
import type { Window } from "./prepare";
export const w: Window = {                 // ⛔ missing \`kind\` must NOT typecheck
  startMs: 0, endMs: 1, basis: "recorded", source: "", note: "",
};
`;
  const badCall = `
import { describeGap } from "./prepare";
// calling the renderer with one argument instead of two must fail
export const s = describeGap({ startMs: 0, endMs: 1, kind: "host", basis: "b", source: "", note: "" });
`;
  ok("C2 a Window without `kind` is a type error", rejects(bad, "kind_missing"));
  const ac = errorsIn(badCall, "kind_argless");
  ok("C2 a gap renderer called without `kind` is a type error", ac.length > 0,
     ac[0]?.trim().slice(0, 88) || "");
}

// C5 — NotASeries cannot reach a chart typed to PreparedSeries
{
  const bad = `
import type { Prepared, PreparedSeries } from "./prepare";
import type { Unit } from "./units";
declare function chart<U extends Unit>(s: PreparedSeries<U>): void;
declare const p: Prepared<"passes">;
export function draw() { chart(p); }       // ⛔ un-narrowed union must NOT typecheck
`;
  const good = `
import type { Prepared, PreparedSeries } from "./prepare";
import type { Unit } from "./units";
declare function chart<U extends Unit>(s: PreparedSeries<U>): void;
declare const p: Prepared<"passes">;
export function draw() { if (p.kind === "series") chart(p); }
`;
  ok("C5 an un-narrowed Prepared cannot be handed to a chart", rejects(bad, "nas"));
  ok("C5 …narrowing to kind === 'series' compiles", accepts(good, "nas_ok"));
}

// C6 — disclosures cannot be empty
{
  const bad = `
import type { Disclosures } from "./prepare";
export const d: Disclosures = [];          // ⛔ empty must NOT typecheck
`;
  const good = `
import type { Disclosures } from "./prepare";
export const d: Disclosures = [{ code: "fraction-basis", text: "x" }];
`;
  ok("C6 an empty disclosure list is a type error", rejects(bad, "disc_empty"));
  ok("C6 …one disclosure compiles", accepts(good, "disc_one"));
}

// C7 — the air key is composite
{
  const bad = `
import type { SplitKey } from "./prepare";
export const k: SplitKey = { country: "SY" };   // ⛔ missing \`source\` must NOT typecheck
`;
  ok("C7 a SplitKey without `source` is a type error", rejects(bad, "key"));
}

// C1 — a chart cannot reach raw points: PreparedSeries exposes segments only
{
  const bad = `
import type { PreparedSeries } from "./prepare";
declare const s: PreparedSeries<"events">;
export const pts = s.splits[0].points;      // ⛔ there is no \`points\` on a split
`;
  ok("C1 a split exposes `segments`, never a flat point list", rejects(bad, "raw_points"));
}

rmSync(TMP, { recursive: true, force: true });

/* ------------------------------------------------------------------ runtime detonations ------- */

console.log("\n=== runtime constraints — each proven by feeding it a violation ===");

// Compile the two modules once, to plain JS we can import.
const OUT = mkdtempSync(path.join(tmpdir(), "ui3out-"));
// ⚠ CommonJS on purpose: the emitted ESM keeps TypeScript's extensionless `./units` specifier,
// which Node's ESM loader will not resolve. The gate must not require prepare.ts to carry a `.js`
// suffix it does not need in the app, so the GATE adapts rather than the source.
execFileSync("npx", ["tsc", "src/lib/series/prepare.ts", "src/lib/series/units.ts",
                     "--outDir", OUT, "--rootDir", "src",
                     "--target", "ES2022", "--module", "commonjs",
                     "--moduleResolution", "node10", "--skipLibCheck", "--lib", "ES2022,DOM"],
             { cwd: ROOT, stdio: "pipe" });
const require_ = createRequire(import.meta.url);
const P = require_(path.join(OUT, "lib", "series", "prepare.js"));

const COV = (over = {}) => ({
  stream: "s", domain: "sea", unit: "detected AIS-gap events", source: "src",
  series_start: "2026-07-01T00:00:00Z", series_end: "2026-07-10T00:00:00Z",
  rows: 10, computed_at: "2026-07-10T00:00:00Z",
  host_gaps: [], gaps: [], host_gap_basis: "journal-boot-table",
  gap_basis: "reconstruct_outage_windows", observed_fraction: 0.9,
  observed_fraction_basis: "host + source", ratio_cutoff: null, ratio_cutoff_note: "",
  not_a_series: null, caveats: [], ...over,
});
const B = (iso, v, key = "k") => ({ bucket: iso, key, value: v });

// C1 — a DECLARED window shorter than a bucket still cuts the line
{
  const buckets = [B("2026-07-02T00:00:00Z", 1), B("2026-07-03T00:00:00Z", 2),
                   B("2026-07-04T00:00:00Z", 3)];
  const clean = P.prepare(buckets, COV());
  const withGap = P.prepare(buckets, COV({
    gaps: [{ start: "2026-07-03T01:00:00Z", end: "2026-07-03T02:00:00Z", kind: "source",
             basis: "recorded", source: "aisstream", seconds: 3600, note: "" }],
  }));
  const segs = (p) => p.splits[0].segments.length;
  ok("C1 one declared 1-hour window splits a daily line (adjacency would NOT)",
     segs(clean) === 1 && segs(withGap) === 2, `${segs(clean)} → ${segs(withGap)} segments`);
}

// C1b — the axis is series_start, not the first bucket
{
  const p = P.prepare([B("2026-07-05T00:00:00Z", 1)], COV());
  ok("C8 the axis starts at series_start, not at the first bucket",
     p.axisStartMs === Date.parse("2026-07-01T00:00:00Z"),
     new Date(p.axisStartMs).toISOString());
}

// C8 — no zero-fill: absent buckets stay absent
{
  const p = P.prepare([B("2026-07-02T00:00:00Z", 5), B("2026-07-08T00:00:00Z", 7)], COV());
  const n = p.splits[0].segments.reduce((t, s) => t + s.points.length, 0);
  ok("C8 six absent days are NOT filled with zeros", n === 2, `${n} points for 2 buckets`);
}

// C5 — not_a_series returns a different kind
{
  const p = P.prepare([B("2026-07-02T00:00:00Z", 1)],
                      COV({ unit: "computed AOI passes", not_a_series: "ONE rolling window" }));
  ok("C5 not_a_series yields kind 'not-a-series' with no segments",
     p.kind === "not-a-series" && p.segments === undefined, `kind=${p.kind}`);
}

// C4 — an unrecognised unit is refused, not guessed
{
  const p = P.prepare([B("2026-07-02T00:00:00Z", 1)], COV({ unit: "sprockets per fortnight" }));
  ok("C4 an unrecognised unit is refused", p.kind === "refused", p.reason?.slice(0, 42));
}
{
  const p = P.prepare([B("2026-07-02T00:00:00Z", 1)], COV({ unit: "hull-days over air_country_daily" }));
  ok("C4 hull-days resolves before 'hull' and carries its own disclosure",
     p.kind === "series" && p.unit === "hull-days" &&
     p.disclosures.some((d) => d.code === "unit-choice"));
}

// C6 — a non-probative gap_basis forces a disclosure
{
  const air = P.prepare([B("2026-07-02T00:00:00Z", 1)], COV({
    domain: "air", unit: "airframe position reports",
    gap_basis: "source gaps: UNAVAILABLE — this stream records no feed events",
  }));
  const has = air.disclosures.some((d) => d.code === "gap-basis-unavailable");
  const probative = P.prepare([B("2026-07-02T00:00:00Z", 1)], COV());
  ok("C6 UNAVAILABLE gap_basis emits the air disclosure; a probative basis does not",
     has && !probative.disclosures.some((d) => d.code === "gap-basis-unavailable"));
  ok("C6 disclosures are never empty on a real payload", air.disclosures.length > 0,
     `${air.disclosures.length}`);
}

// C3 — a ratio crossing the cutoff is refused
{
  const cut = "2026-07-29T13:55:36Z";
  const across = P.ratio(1, 2, Date.parse("2026-07-20T00:00:00Z"), Date.parse("2026-08-02T00:00:00Z"), cut);
  const before = P.ratio(1, 2, Date.parse("2026-07-20T00:00:00Z"), Date.parse("2026-07-25T00:00:00Z"), cut);
  const sea = P.ratio(1, 2, Date.parse("2026-07-20T00:00:00Z"), Date.parse("2026-08-02T00:00:00Z"), null);
  ok("C3 a ratio spanning the frame boundary is 'incomparable', not a number",
     across.kind === "incomparable" && across.value === undefined);
  ok("C3 …the same ratio inside one frame is a number", before.kind === "ratio");
  ok("C3 …and a sea stream (cutoff null) is never blocked by the air rule", sea.kind === "ratio");
}

// C9 — the fraction is never shown without its basis
{
  const noBasis = P.observedFraction(COV({ observed_fraction_basis: "  " }));
  const withBasis = P.observedFraction(COV());
  const nullFrac = P.observedFraction(COV({ observed_fraction: null }));
  ok("C9 a fraction with a blank basis is withheld",
     noBasis.kind === "unavailable" && noBasis.value === undefined);
  ok("C9 …with a basis it carries value AND basis together",
     withBasis.kind === "fraction" && typeof withBasis.basis === "string");
  ok("C9 …a null fraction is withheld", nullFrac.kind === "unavailable");
}

// C2 — kinds are unioned within a kind, never merged across kinds
{
  const p = P.prepare([B("2026-07-02T00:00:00Z", 1)], COV({
    host_gaps: [{ start: "2026-07-03T00:00:00Z", end: "2026-07-05T00:00:00Z", kind: "host",
                  basis: "journal-boot-table", source: "systemd", seconds: 1, note: "" }],
    gaps: [{ start: "2026-07-03T12:00:00Z", end: "2026-07-04T00:00:00Z", kind: "detector",
             basis: "timer-enable-mtime", source: "t.timer", seconds: 1, note: "" }],
  }));
  const kinds = p.windows.map((w) => w.kind).sort();
  ok("C2 a detector window nested in a host window stays TWO windows",
     p.windows.length === 2 && kinds.join(",") === "detector,host", kinds.join(","));
}

// C7 — the composite key round-trips and is never collapsed
{
  const p = P.prepare([
    { bucket: "2026-07-02T00:00:00Z", key: { country: "SY", source: "opensky" }, value: 1 },
    { bucket: "2026-07-02T00:00:00Z", key: { country: "SY", source: "adsbexchange" }, value: 2 },
  ], COV({ domain: "air", unit: "airframe position reports" }));
  ok("C7 (country, source) yields TWO splits, not one summed line",
     p.splits.length === 2, `${p.splits.length} splits`);
}

/* ------------------------------------------------------------------ real payloads ------------- */

console.log("\n=== the NotASeries and disclosure states on REAL contracts ===");
const API = process.env.API_BASE || "http://127.0.0.1:8000/api/v1";
try {
  const cov = await (await fetch(`${API}/coverage`)).json();
  const space = cov.streams.find((s) => s.not_a_series !== null);
  ok("space: a real stream declares not_a_series and prepare() refuses it as a series",
     !!space && P.prepare([], space).kind === "not-a-series",
     space ? space.stream : "no such stream on the wire");

  const air = await (await fetch(`${API}/air/series/country?hours=6`)).json();
  const prep = P.prepare(
    air.buckets.map((b) => ({ bucket: b.bucket, key: { country: b.country, source: b.source },
                              value: b.airframes })),
    air.coverage,
  );
  ok("air: gap_basis is non-probative on the wire, so the air disclosure fires",
     prep.kind === "series" &&
     prep.disclosures.some((d) => d.code === "gap-basis-unavailable"),
     prep.kind === "series" ? `${prep.disclosures.length} disclosures, ${prep.splits.length} splits`
                            : prep.reason);
} catch (e) {
  ok("real-contract checks reached the API", false, String(e).slice(0, 60));
}

rmSync(OUT, { recursive: true, force: true });
console.log(`\n${pass.length}/${pass.length + fail.length} constraint detonations behaved`);
if (fail.length) { console.error("⛔ " + fail.join("\n⛔ ")); process.exit(1); }
