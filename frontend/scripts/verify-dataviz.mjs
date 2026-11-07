/**
 * DATAVIZ-1 — the chart-palette gate, and the first version of it that is committed.
 *
 * ⛔ WHY THIS FILE EXISTS AT ALL. tokens.css has cited a "dataviz six-checks script" since
 * THEME-2, and that script is **nowhere in this repository** — nothing under frontend/ or
 * anywhere else runs it. A gate you cannot re-run is a claim, not a gate: it validated once,
 * on a surface the project has since replaced, and nothing has re-checked it in the two
 * identity changes that followed. So the gate is written down here and made runnable:
 * `npm run verify:dataviz`.
 *
 * ⛔ AND IT READS THE SHIPPED SURFACE, IT DOES NOT CARRY ONE. Every colour below — the series,
 * the grid, the axis AND the background they sit on — is parsed out of styles/tokens.css and
 * its `var()` chain resolved. Nothing is hard-coded, so the day a token moves this script
 * moves with it. A validator holding its own copy of the palette would keep passing after the
 * palette changed underneath it, which is exactly how the 5.95:1 figure in tokens.css survived
 * the switch to Direction 2 while being false (BE-SUMMARY-4's lesson: a check against a
 * surface you do not ship is decoration).
 *
 * ⛔ TWO THRESHOLDS, BY ROLE, NOT ONE FOR EVERYTHING (WCAG 1.4.11 vs 1.4.3):
 *   · a data series, a gridline, an axis rule  →  NON-TEXT mark   →  3:1
 *   · an axis label, a value label, a legend   →  TEXT            →  4.5:1
 * Applying the text bar to a gridline would demand a gridline that shouts; applying the
 * non-text bar to a tick label would ship an unreadable number.
 *
 * ⛔ AND CONTRAST IS ONLY HALF. Two series can each clear the surface and still be
 * indistinguishable FROM EACH OTHER — worse than one dim series, because the reader believes
 * they are reading two things. So every pair is also checked with CIEDE2000, at the same
 * threshold the severity axis is held to (≥20, THEME-2b), and again under a deuteranopia
 * simulation, since green-blindness is the common form and this identity is green.
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

//: ⛔ BOTH SHIPPED SHEETS, because the palette spans them: forest.css declares the RAW ramp
//: (--forest-*/--wheat-*/--char-*/--umber-*) at :root and tokens.css declares the semantic
//: layer that points into it. Reading tokens.css alone leaves every `var(--forest-900)`
//: unresolvable — which is how a validator quietly ends up hard-coding what it cannot follow.
const DIR = path.join(import.meta.dirname, "..", "src", "styles");
const SHEETS = ["forest.css", "tokens.css"];
//: ⛔ COMMENTS ARE STRIPPED BEFORE ANY SELECTOR SEARCH — verify-layout.mjs already paid for
//: this lesson once. Both sheets DISCUSS `:root` and `[data-theme="dark"]` in their prose
//: ("Forest's raw palette lands at :root…"), and a plain indexOf finds the sentence, then
//: reads whatever block happens to follow it. Measured symptom before the fix: the gate
//: resolved --bg to #f4f1ec and reported the LIGHT palette as if it were the shipped one.
const css = SHEETS.map((f) => readFileSync(path.join(DIR, f), "utf8"))
  .join("\n")
  .replace(/\/\*[\s\S]*?\*\//g, "");

/* ---------- read the SHIPPED dark theme out of the stylesheet ---------- */

/** Every block for a selector, in source order (a selector may repeat across sheets). */
function allBlocks(selector) {
  const out = [];
  let from = 0;
  for (;;) {
    const i = css.indexOf(selector, from);
    if (i < 0) break;
    out.push(blockVars(i));
    from = i + selector.length;
  }
  if (!out.length) throw new Error(`selector not found in the shipped sheets: ${selector}`);
  return out;
}

/** Custom properties declared inside the block starting at index `i`. */
function blockVars(i) {
  const open = css.indexOf("{", i);
  let depth = 0, end = open;
  for (let j = open; j < css.length; j++) {
    if (css[j] === "{") depth++;
    else if (css[j] === "}") { depth--; if (depth === 0) { end = j; break; } }
  }
  const body = css.slice(open + 1, end).replace(/\/\*[\s\S]*?\*\//g, "");
  const out = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

//: `:root` is declared in BOTH sheets; merge every occurrence rather than taking the first.
const root = Object.assign({}, ...allBlocks(":root"));
const dark = Object.assign({}, ...allBlocks('[data-theme="dark"]'));
//: ⛔ THE LIGHT BLOCK IS READ TOO, AND IT IS NOT DECORATION. `.chart-svg text` is declared once,
//: OUTSIDE any theme, so a single fill has to clear the text bar on BOTH shipped surfaces. This
//: gate previously resolved the dark theme only, and that hole was not theoretical: it reported
//: `--ink-dim` PASS at 8.40:1 on dark --bg #002623 while the same token sat at 4.22:1 on light
//: --bg #f4f1ec — under the 4.5 bar, in the one check written to catch exactly this.
const light = Object.assign({}, ...allBlocks('[data-theme="light"]'));

/** Resolve a value through its var() chain against the raw palette + ONE theme's overrides. */
function resolve(value, theme = dark, seen = 0) {
  if (seen > 12) throw new Error("var() chain too deep: " + value);
  const m = /^var\(\s*(--[\w-]+)\s*\)$/.exec(value.trim());
  if (!m) return value.trim();
  const next = theme[m[1]] ?? root[m[1]];
  if (next === undefined) throw new Error(`unresolved token ${m[1]}`);
  return resolve(next, theme, seen + 1);
}

const tokIn = (theme, name) => {
  const raw = theme[name] ?? root[name];
  if (raw === undefined) throw new Error(`token not declared: ${name}`);
  return resolve(raw, theme);
};
const tok = (name) => tokIn(dark, name);

/* ---------- colour maths ---------- */

const hex = (h) => {
  const s = h.replace("#", "").trim();
  if (!/^[0-9a-fA-F]{6}$/.test(s)) throw new Error(`not a plain hex colour: ${h}`);
  return [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16));
};
const lin = (u) => (u / 255 <= 0.04045 ? u / 255 / 12.92 : (((u / 255) + 0.055) / 1.055) ** 2.4);
const lum = (c) => { const [r, g, b] = hex(c).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

function lab(c) {
  const [r, g, b] = hex(c).map(lin);
  const X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047;
  const Y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
  const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (841 / 108) * t + 4 / 29);
  const [fx, fy, fz] = [f(X), f(Y), f(Z)];
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

function ciede2000(c1, c2) {
  const [L1, a1, b1] = lab(c1), [L2, a2, b2] = lab(c2);
  const C1 = Math.hypot(a1, b1), C2 = Math.hypot(a2, b2), Cb = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)) || 0);
  const a1p = (1 + G) * a1, a2p = (1 + G) * a2;
  const C1p = Math.hypot(a1p, b1), C2p = Math.hypot(a2p, b2);
  const deg = (x) => (x * 180) / Math.PI, rad = (x) => (x * Math.PI) / 180;
  const h1 = (a1p || b1) ? (deg(Math.atan2(b1, a1p)) + 360) % 360 : 0;
  const h2 = (a2p || b2) ? (deg(Math.atan2(b2, a2p)) + 360) % 360 : 0;
  const dL = L2 - L1, dC = C2p - C1p;
  let dh = 0;
  if (C1p * C2p !== 0) {
    dh = h2 - h1;
    if (dh > 180) dh -= 360; else if (dh < -180) dh += 360;
  }
  const dH = 2 * Math.sqrt(C1p * C2p) * Math.sin(rad(dh) / 2);
  const Lb = (L1 + L2) / 2, Cbp = (C1p + C2p) / 2;
  let hb;
  if (C1p * C2p === 0) hb = h1 + h2;
  else if (Math.abs(h1 - h2) <= 180) hb = (h1 + h2) / 2;
  else hb = (h1 + h2 + (h1 + h2 < 360 ? 360 : -360)) / 2;
  const T = 1 - 0.17 * Math.cos(rad(hb - 30)) + 0.24 * Math.cos(rad(2 * hb))
            + 0.32 * Math.cos(rad(3 * hb + 6)) - 0.20 * Math.cos(rad(4 * hb - 63));
  const Sl = 1 + (0.015 * (Lb - 50) ** 2) / Math.sqrt(20 + (Lb - 50) ** 2);
  const Sc = 1 + 0.045 * Cbp, Sh = 1 + 0.015 * Cbp * T;
  const Rt = -Math.sin(rad(2 * (30 * Math.exp(-(((hb - 275) / 25) ** 2)))))
             * 2 * Math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7));
  return Math.sqrt((dL / Sl) ** 2 + (dC / Sc) ** 2 + (dH / Sh) ** 2 + Rt * (dC / Sc) * (dH / Sh));
}

/** Deuteranopia (green-blind) simulation — Brettel/Viénot LMS projection. */
function deuter(c) {
  const [r, g, b] = hex(c).map(lin);
  const L = 17.8824 * r + 43.5161 * g + 4.11935 * b;
  const M = 3.45565 * r + 27.1554 * g + 3.86714 * b;
  const S = 0.0299566 * r + 0.184309 * g + 1.46709 * b;
  const M2 = 0.494207 * L + 1.24827 * S;                       // green channel reconstructed
  const R = 0.080944 * L - 0.130504 * M2 + 0.116721 * S;
  const G = -0.0102485 * L + 0.0540194 * M2 - 0.113615 * S;
  const B = -0.000365294 * L - 0.00412163 * M2 + 0.693513 * S;
  const enc = (u) => {
    const v = u <= 0.0031308 ? 12.92 * u : 1.055 * u ** (1 / 2.4) - 0.055;
    return Math.max(0, Math.min(255, Math.round(v * 255)));
  };
  return "#" + [R, G, B].map((u) => enc(u).toString(16).padStart(2, "0")).join("");
}

/* ---------- what is being validated, and against which bar ---------- */

const APP_CSS = readFileSync(path.join(DIR, "app.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

//: ⛔ THE SURFACE IS DERIVED FROM THE STYLESHEET — ⚠ AND UI-1 IS WHY IT HAS TO BE.
//: DATAVIZ-1 and CHART-TEXT-1 both hard-coded `--bg` here, on the true-at-the-time observation
//: that `.chart-card` and `.stats-view` declared NO background anywhere. UI-1 then gave
//: `.chart-card` a `--panel` background — and every figure this gate printed silently became a
//: measurement against a surface the charts had stopped sitting on. A gate that encodes a BELIEF
//: about the CSS fails exactly when the CSS changes, which is the one moment it is needed.
//:
//: So it now READS the innermost painted ancestor of a chart <text>: if `.chart-card` paints, that
//: is the surface; otherwise the page. ⛔ Repaint the card, unpaint it, or point it at a different
//: token, and the numbers below follow on their own — nobody has to remember to come back here.
function chartSurfaceToken() {
  const m = /\.chart-card\s*\{[^}]*?background:\s*var\(\s*(--[\w-]+)\s*\)/s.exec(APP_CSS);
  return m ? m[1] : "--bg";
}
const SURFACE_TOKEN = chartSurfaceToken();
const SURFACE = tok(SURFACE_TOKEN);
const SURFACE_ALT = tok(SURFACE_TOKEN === "--bg" ? "--panel" : "--bg");

//: ⛔ THE TWO SHIPPED SURFACES, EACH CARRYING ITS THEME NAME. Every contrast figure this gate
//: prints is paired with the surface it was measured on — the general rule CHART-TEXT-1 was
//: corrected by, after "8.40", "11.44" and "4.22" all turned out to be true numbers quoted
//: against surfaces nobody had named. ⚠ UI-1 adds the second half of that rule: the surface is
//: not only NAMED, it is LOOKED UP, because naming a surface that later changes is the same
//: defect one step removed.
const SURFACES = [
  { theme: "dark",  vars: dark,  bg: tokIn(dark, SURFACE_TOKEN) },
  { theme: "light", vars: light, bg: tokIn(light, SURFACE_TOKEN) },
];

const MARK = 3.0;      // WCAG 1.4.11 — non-text contrast
const TEXT = 4.5;      // WCAG 1.4.3  — body text

const ITEMS = [
  { token: "--series-air",    role: "data series (line + bar fill)",  bar: MARK, series: true },
  { token: "--series-ground", role: "data series (bar fill)",         bar: MARK, series: true },
  { token: "--chart-grid",    role: "gridline",                       bar: MARK },
  { token: "--chart-axis",    role: "axis rule + hover crosshair",    bar: MARK },
];

const fail = [];
const pass = [];
const note = [];

console.log("=== dataviz palette gate ===");
console.log(`chart surface DERIVED from app.css: ${SURFACE_TOKEN} = ${SURFACE}` +
            `   (the other page surface = ${SURFACE_ALT}, not what a chart sits on)\n`);
console.log(`token            value    role                                bar    vs ${SURFACE_TOKEN.padEnd(7)} verdict`);
for (const it of ITEMS) {
  const v = tok(it.token);
  const c = contrast(v, SURFACE);
  const ok = c >= it.bar;
  (ok ? pass : fail).push(`${it.token} ${c.toFixed(2)}:1 vs ${it.bar}:1`);
  console.log(
    `${it.token.padEnd(16)} ${v}  ${it.role.padEnd(34)} ${String(it.bar).padEnd(6)} ` +
    `${c.toFixed(2).padStart(6)}:1  ${ok ? "PASS" : "⛔ FAIL"}`);
}

//: ⚠ THE PALETTE BAR ABOVE IS DARK-ONLY, AND THAT IS A DECISION, NOT AN OVERSIGHT. The chart
//: TEXT is gated on both surfaces because one theme-less rule serves both; the chart FURNITURE
//: is not, because the light block is frozen and failing on record (THEME-4 — see the --sev-
//: block in tokens.css), index.html stamps `dark` unconditionally, and re-picking the light
//: chart tokens is that item's work. ⛔ Printed as notes rather than left unmeasured, so the
//: narrowness is visible: a gate silent about what it does not check reads as a gate that
//: checked everything.
for (const it of ITEMS) {
  const v = tokIn(light, it.token);
  const c = contrast(v, tokIn(light, SURFACE_TOKEN));
  if (c < it.bar) {
    note.push(`light is FROZEN (THEME-4) and NOT gated: ${it.token} ${v} = ${c.toFixed(2)}:1 ` +
              `on light ${SURFACE_TOKEN} ${tokIn(light, SURFACE_TOKEN)} vs its ${it.bar}:1 bar`);
  }
}

/* ---------- pairwise separation, plain and green-blind ---------- */

const series = ITEMS.filter((i) => i.series).map((i) => ({ name: i.token, hex: tok(i.token) }));
//: ⚠ Grid and axis are compared against the series too. They are not series and the ≥20 bar
//: is not applied to them, but a gridline a reader could mistake for data is a real defect,
//: so the numbers are printed rather than left unmeasured.
const marks = ITEMS.map((i) => ({ name: i.token, hex: tok(i.token) }));
console.log("\npairwise separation (CIEDE2000, threshold 20 — the severity-axis bar):");
let worst = Infinity, worstPair = "";
for (let i = 0; i < series.length; i++) {
  for (let j = i + 1; j < series.length; j++) {
    const d = ciede2000(series[i].hex, series[j].hex);
    const dd = ciede2000(deuter(series[i].hex), deuter(series[j].hex));
    const m = Math.min(d, dd);
    if (m < worst) { worst = m; worstPair = `${series[i].name} / ${series[j].name}`; }
    console.log(`  ${series[i].name} / ${series[j].name}: ΔE00 ${d.toFixed(2)}  ` +
                `· green-blind ${dd.toFixed(2)} (${deuter(series[i].hex)} vs ${deuter(series[j].hex)})`);
  }
}
console.log("\nseparation of every mark from every other (reported, not gated except for series):");
for (let i = 0; i < marks.length; i++) {
  for (let j = i + 1; j < marks.length; j++) {
    const d = ciede2000(marks[i].hex, marks[j].hex);
    const dd = ciede2000(deuter(marks[i].hex), deuter(marks[j].hex));
    console.log(`  ${marks[i].name.padEnd(16)}/ ${marks[j].name.padEnd(16)} ΔE00 ${d.toFixed(2).padStart(6)}` +
                `  · green-blind ${dd.toFixed(2).padStart(6)}`);
  }
}

if (series.length > 1) {
  const ok = worst >= 20;
  (ok ? pass : fail).push(`worst pair (incl. green-blind) ${worst.toFixed(2)} vs 20`);
  console.log(`  worst, either view: ${worst.toFixed(2)} (${worstPair}) → ${ok ? "PASS" : "⛔ FAIL"}`);
}

/* ---------- CHART TEXT: the 4.5:1 bar, and a gate that FAILS instead of warning ----------
 *
 * ⛔ DATAVIZ-1 left this as a printed warning and the gate still exited 0. A gate that reports
 * a defect and passes is a gate that will be ignored — BE-SUMMARY-4's lesson — so it now
 * FAILS. Two assertions, because either alone has a hole:
 *
 *   1. THE RULE EXISTS AND IS BRIGHT ENOUGH. The fill is set in CSS, not in JSX, precisely so
 *      this script can read it: a `fill=` attribute in a component is invisible to a
 *      stylesheet-reading gate, and an unreadable gate is the thing being fixed.
 *   2. EVERY CHART IS COVERED BY IT. Any .tsx under components/charts/ that renders a <text>
 *      must put it inside an svg carrying the class the rule targets. Without this a new chart
 *      would silently inherit SVG's initial BLACK again and the palette check would still pass.
 */

const TEXT_SELECTOR = ".chart-svg text";

{
  // find the rule block for the selector — plain string search, no regex escaping to get wrong
  const at = APP_CSS.indexOf(TEXT_SELECTOR);
  const body = at < 0 ? null
    : APP_CSS.slice(APP_CSS.indexOf("{", at) + 1, APP_CSS.indexOf("}", at));
  const m = body === null ? null : [null, body];
  const decl = m && /(?:^|;)\s*fill\s*:\s*([^;]+)/.exec(m[1]);

  //: ⛔ ONE MEASUREMENT PATH, WHETHER OR NOT THE RULE EXISTS. A missing rule is not a special
  //: case to be shouted about — it is a fill of #000000, because that is literally what the
  //: browser paints. Measuring it like any other value keeps every printed number attached to
  //: the verdict it earned. ⚠ The first draft of this block failed BOTH surfaces on a missing
  //: rule while printing 18.64:1 for light — a number that PASSES the bar, stamped FAIL. That
  //: is the same defect this whole item exists to remove, reproduced inside its own gate.
  const raw = decl ? decl[1].trim() : "#000000";

  //: …but the ABSENCE of the rule is still its own failure, on structural grounds and not on
  //: contrast: an unmanaged fill that happens to be readable on one surface is luck, not a
  //: decision, and it is exactly the state this item was opened to end.
  if (!decl) {
    fail.push(`no CSS rule sets a fill for \`${TEXT_SELECTOR}\` — the fill is UNMANAGED and ` +
              `falls back to SVG's initial black`);
  }

  console.log(`\nchart text — ONE theme-less rule, so BOTH surfaces are gated and either fails:`);
  if (!decl) console.log(`  ⛔ NO RULE — fill is unmanaged; measured below as SVG's initial black`);
  for (const s of SURFACES) {
    const v = resolve(raw, s.vars);
    const c = contrast(v, s.bg);
    const ok = c >= TEXT;
    (ok ? pass : fail).push(
      `${TEXT_SELECTOR} fill ${raw} = ${v} → ${c.toFixed(2)}:1 on ${s.theme} --bg ${s.bg} vs ${TEXT}:1`);
    console.log(`  ${s.theme.padEnd(6)} ${raw} = ${v}  on --bg ${s.bg}  ` +
                `${c.toFixed(2).padStart(6)}:1 vs ${TEXT}:1  ${ok ? "PASS" : "⛔ FAIL"}`);
  }

  // every chart that draws text must be inside the covered svg
  const CHARTS = path.join(import.meta.dirname, "..", "src", "components", "charts");
  const cls = TEXT_SELECTOR.split(" ")[0].slice(1);
  for (const f of readdirSync(CHARTS).filter((x) => x.endsWith(".tsx"))) {
    const src = readFileSync(path.join(CHARTS, f), "utf8");
    if (!src.includes("<text")) continue;
    const covered = src.includes(cls);
    (covered ? pass : fail).push(`${f} <text> covered by .${cls}`);
    console.log(`  ${f.padEnd(20)} draws <text> · inside .${cls}: ${covered ? "yes" : "⛔ NO"}`);
  }
}

/* ---------- the old advisory note, kept only where it is still true ---------- */

if (note.length) {
  console.log("\nnotes:");
  for (const n of note) console.log("  ⚠ " + n);
}

console.log(`\n${pass.length} passed, ${fail.length} failed`);
for (const f of fail) console.log("  ⛔ " + f);
process.exit(fail.length ? 1 : 0);
