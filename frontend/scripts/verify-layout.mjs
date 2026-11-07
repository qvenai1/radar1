/**
 * Layout invariants (FIX-LAYOUT-1) — the regression guard the fix owes.
 *
 * WHY A SCRIPT AND NOT A TEST. This project has no frontend test runner: package.json
 * carries only dev/build/preview, and there is no vitest/jest anywhere (docs §10.7). Adding
 * one to pin a CSS invariant would be a bigger change than the fix. So this is a plain node
 * script with no dependencies: `npm run verify:layout`.
 *
 * WHAT IT GUARDS, and why these are the right assertions. The regression it exists to catch
 * was NOT a text or overflow problem — measured, the panels never overflowed. It was a dead
 * grid track: `.shell` reserved `340px 1fr` for a sidebar that DASH-2g′ had moved inside
 * `.main`, so `<main>` — the shell's only child — landed in the 340px column and everything
 * inside it collapsed into a strip. The two 340px panels then had to coincide.
 *
 * So the invariant is not "the panels do not overlap" (geometry, needs a browser) but the
 * two structural facts that MAKE overlap impossible:
 *   1. the shell gives its single child the full width, and no rule re-reserves a fixed track
 *   2. the two panels are capped and anchored so 2×cap + 2×inset can never reach 100 % of it
 * Both are checkable from the stylesheet alone, which is what makes this runnable in CI or a
 * pre-commit hook without a display.
 *
 * ⚠ It does NOT replace the visual pass. Geometry was proven for this fix by a real headless
 * measurement at 1440 and 1900 (gap 740px and 1200px, centres 0.875/0.125 and 0.905/0.095).
 * This script stops the CAUSE returning silently; it cannot see the screen.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

/* ⚠ TWO SHEETS SINCE THEME-3 commit 2, and reading only one of them would have made this
 * script lie. The bidi rules moved out of app.css into bidi.css so they load after Forest's
 * component layer; a coverage check still pointed at app.css alone would have reported the
 * count collapsing to zero and called it a regression, when nothing regressed. The layout
 * assertions (1 and 2) only ever concern app.css; the bidi count (3) spans both. */
const dir = path.join(import.meta.dirname, "..", "src", "styles");
const SHEETS = ["app.css", "bidi.css"];
const css = SHEETS.map((f) => readFileSync(path.join(dir, f), "utf8")).join("\n");

/** rules as [selectorList, body], comments stripped */
function rules() {
  const out = [];
  const re = /([^{}]*)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css)) !== null) {
    const sel = m[1].replace(/\/\*[\s\S]*?\*\//g, "").trim();
    // ⚠ strip comments from the BODY too. Without this, a declaration that happens to sit
    // after an explanatory comment is invisible to `decl()` — which is exactly how this
    // script first reported the panel width missing when it was there all along.
    if (sel) out.push([sel, m[2].replace(/\/\*[\s\S]*?\*\//g, "")]);
  }
  return out;
}
const R = rules();
const decl = (body, prop) => {
  const m = body.match(new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`));
  return m ? m[1].trim() : null;
};

const fail = [];
const pass = [];
const check = (ok, name, detail = "") => (ok ? pass : fail).push(name + (detail ? ` — ${detail}` : ""));

/* ---- 1. the shell gives its only child the whole width ---- */
{
  const shell = R.filter(([s]) => s.split(",").some((x) => x.trim() === ".shell"));
  const cols = shell.map(([, b]) => decl(b, "grid-template-columns")).filter(Boolean);
  check(cols.length > 0, "`.shell` declares grid-template-columns", cols.join(" | "));
  // ⛔ the exact regression: a fixed first track with nothing to put in it
  const fixed = cols.filter((c) => /\d+px\s+\S/.test(c));
  check(fixed.length === 0,
    "`.shell` reserves NO fixed sidebar track (the FIX-LAYOUT-1 regression)",
    fixed.length ? `found ${fixed.join(" | ")}` : "single column");

  // and no `.shell.<modifier>` may re-introduce one
  const mods = R.filter(([s]) => s.split(",").some((x) => /^\.shell\.[\w-]+$/.test(x.trim())));
  const bad = mods.filter(([, b]) => {
    const c = decl(b, "grid-template-columns");
    return c && /\d+px\s+\S/.test(c);
  }).map(([s]) => s);
  check(bad.length === 0, "no `.shell.<modifier>` re-reserves a fixed track",
    bad.length ? bad.join(" | ") : "none");
}

/* ---- 2. the two panels cannot meet, at any viewport width ---- */
{
  const panel = R.find(([s]) => s.split(",").some((x) => x.trim() === ".home-panel"));
  check(!!panel, "`.home-panel` rule exists");
  if (panel) {
    const w = decl(panel[1], "width") || "";
    const m = w.match(/min\(\s*(\d+)px\s*,\s*(\d+)vw\s*\)/);
    check(!!m, "`.home-panel` width is min(<px>, <vw>) — a cap, not a free width", w);
    if (m) {
      const vw = Number(m[2]);
      // insets are 10px each; 2×vw% + 20px < 100% holds for every width above 100px
      check(vw * 2 < 100,
        "two panels + their insets can never span the container",
        `2 × ${vw}vw = ${vw * 2}vw < 100vw`);
    }
    check(decl(panel[1], "position") === "absolute",
      "`.home-panel` is absolutely positioned (so it floats over the map, not in flow)");
  }
  // opposite LOGICAL edges — physical left/right would swap under a direction change
  const lead = R.find(([s]) => s.includes(".home-panel.lead"));
  const ent = R.find(([s]) => s.includes(".home-panel.entity"));
  check(!!lead && !!decl(lead[1], "inset-inline-start"),
    "`.lead` is anchored with inset-inline-START (the RIGHT edge in RTL)");
  check(!!ent && !!decl(ent[1], "inset-inline-end"),
    "`.entity` is anchored with inset-inline-END (the LEFT edge in RTL)");
  const physical = [lead, ent].filter(Boolean)
    .filter(([, b]) => /(?:^|;)\s*(left|right)\s*:/.test(b)).length;
  check(physical === 0, "neither panel uses physical left/right");
}

/* ---- 3. bidi coverage must not shrink (21 surfaces: 18 isolate + 3 plaintext) ---- */
{
  let iso = 0, pt = 0;
  for (const [sel, body] of R) {
    const n = sel.split(",").filter((x) => x.trim()).length;
    if (/unicode-bidi:\s*isolate/.test(body)) iso += n;
    if (/unicode-bidi:\s*plaintext/.test(body)) pt += n;
  }
  check(iso + pt >= 21, "bidi-isolated surfaces ≥ 21", `${iso} isolate + ${pt} plaintext = ${iso + pt}`);
}

console.log("=== layout invariants ===");
for (const p of pass) console.log("  ✅ " + p);
if (fail.length) {
  console.log("=== FAILED ===");
  for (const f of fail) console.log("  ⛔ " + f);
}
console.log(`\n${pass.length} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);
