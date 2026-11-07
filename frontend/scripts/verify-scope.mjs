/**
 * verify-scope.mjs — G10, the SCOPE gate: a dashboard item may not move a domain tab.
 *
 * ⛔ WHY THIS EXISTS. `verify-layout-system.mjs` drives eight surfaces and every one of them
 * is inside the لوحة tab (entity/*, area/*, detail/*, stats/*). It never navigates to جوّي,
 * بحري or فضائي — so a dashboard change that reaches a component those tabs also render is
 * invisible to it. That is exactly how DASH-IDLE's styling of `CountryEvents` (which
 * `DomainOverlay` renders on all three tabs) shipped green.
 *
 * ⛔ WHY A GATE AND NOT A RULE IN THE PROMPT. The scope constraint was written in prose for
 * three consecutive items and leaked anyway, because the ACCEPTANCE CRITERIA were runnable and
 * the scope was not — and G5 actively rewards making a width the same across surfaces, so the
 * gate and the constraint pulled in opposite directions and the gate won silently. A scope
 * that is not measurable is a scope that loses that argument every time.
 *
 * ⛔ WHAT IS COMPARED: A CSS-ONLY FINGERPRINT, NOT THE RAW GEOMETRY. The first version of this
 * measurement recorded `lead.h` (809px at 1440) and the LENGTH of the grid list — both of which
 * follow the live feed, not the stylesheet. A baseline built from those would fail the moment a
 * vessel stopped transmitting, and a gate that cries wolf is a gate that gets disabled. So the
 * fingerprint keeps only what CSS decides — positions, widths, resolved track lists, paddings,
 * gaps, font sizes — and drops every height and count that content moves.
 * ⚠ The cost is stated rather than hidden: a change that alters ONLY a content-driven height on
 * a domain tab passes this gate. That is the correct trade — those heights are not ours to
 * freeze, and freezing them would make the gate useless within a day.
 *
 * Usage:  node scripts/verify-scope.mjs                 # gate: compare against the baseline
 *         node scripts/verify-scope.mjs --bless         # ⛔ rewrite the baseline (see below)
 *         node scripts/verify-scope.mjs --out=x.json    # ad-hoc capture, no comparison
 *
 * ⛔ `--bless` IS AN ADMISSION, NOT A FIX. Running it says "this item deliberately changed the
 * domain tabs and here is why", and the reason belongs in the commit message beside the new
 * baseline. Blessing a diff you did not intend is how the leak this gate exists to catch gets
 * re-committed with the gate's own blessing on it.
 */
import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const ARGS = process.argv.slice(2);
const arg = (n, d) => (ARGS.find((a) => a.startsWith(`--${n}=`)) || `--${n}=${d}`).slice(n.length + 3);
const URL = arg("url", "http://127.0.0.1:8000/");
const OUT = arg("out", "");
const DIFF = arg("diff", "");
const BLESS = ARGS.includes("--bless");
// ⚠ NOT `new URL(...)`: the module-scope const `URL` above (the app address) shadows the
// global URL constructor. `verify-layout-system.mjs` uses import.meta.dirname; so does this.
const BASELINE = path.join(import.meta.dirname, "domain-tabs-baseline.json");
const PORT = 9333;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CHROME = ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/snap/bin/chromium"].find((p) => existsSync(p));
if (!CHROME) { console.error("⛔ no Chrome/Chromium binary — fail closed, not skipped"); process.exit(1); }

const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, "--no-first-run",
  "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
  `--user-data-dir=/tmp/probe-domain-tabs-${PORT}`, "about:blank"], { stdio: "ignore" });

let ws, id = 0; const pending = new Map();
const send = (m, p = {}, s) => new Promise((res, rej) => {
  const x = { id: ++id, method: m, params: p }; if (s) x.sessionId = s;
  pending.set(x.id, { res, rej }); ws.send(JSON.stringify(x));
});
let wsUrl;
for (let i = 0; i < 80; i++) {
  try { wsUrl = (await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json()).webSocketDebuggerUrl; break; }
  catch { await sleep(250); }
}
if (!wsUrl) { console.error("⛔ Chrome did not open a debugging port"); process.exit(1); }
ws = new WebSocket(wsUrl);
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id); pending.delete(m.id);
    m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
  }
};
await new Promise((r) => (ws.onopen = r));
const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId: S } = await send("Target.attachToTarget", { targetId, flatten: true });
await send("Page.enable", {}, S); await send("Runtime.enable", {}, S);
const ev = (x) => send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true },
  S).then((r) => r.result?.value);

try { const p = await fetch(URL); if (!p.ok) throw new Error(p.status); }
catch (e) { console.error(`⛔ ${URL} unreachable (${e.message}) — build and serve first`); process.exit(1); }

/* ---------------------------------------------------------------------------------------
 * What is measured, and why each one.
 *
 * `.home-panel.lead`      — the country card pinned over the map. It renders CountryEvents,
 *                           the component the dashboard also mounts, so it is the surface a
 *                           dashboard change reaches.
 * `.dash-stats.triage`    — the tile row inside it, and its tiles: the measured -17px.
 * `.card-grid` tracks     — resolved gridTemplateColumns, where auto-fill → auto-fit landed.
 * `.sat-groups button`    — the satellite tab's own control, touched by a ladder pass.
 * `.map-host`             — proof the map is still full-bleed on these tabs.
 * ⚠ Resolved values are read from getComputedStyle, not from the stylesheet: a track list is
 * what the browser computed, not what the author wrote.
 * ------------------------------------------------------------------------------------- */
const MEASURE = String.raw`(() => {
  const R = (n) => Math.round(n * 10) / 10;
  const box = (e) => { if (!e) return null; const r = e.getBoundingClientRect();
    return { x: R(r.x), y: R(r.y), w: R(r.width), h: R(r.height) }; };
  const out = { lead: box(document.querySelector(".home-panel.lead")),
                map: box(document.querySelector(".map-host")), children: [], tiles: [], grids: [], satBtn: null };
  const lead = document.querySelector(".home-panel.lead");
  if (lead) for (const c of lead.children) {
    const cs = getComputedStyle(c);
    out.children.push({ tag: c.tagName.toLowerCase(),
      cls: (typeof c.className === "string" ? c.className.trim().split(/\s+/).slice(0,2).join(".") : ""),
      ...box(c), pad: cs.padding, gap: cs.gap });
  }
  for (const t of document.querySelectorAll(".home-panel.lead .dash-stat")) {
    const cs = getComputedStyle(t); out.tiles.push({ ...box(t), pad: cs.padding });
  }
  for (const g of document.querySelectorAll(".home-panel.lead .dash-stats, .home-panel.lead .card-grid")) {
    const cs = getComputedStyle(g);
    out.grids.push({ cls: g.className, cols: cs.gridTemplateColumns, gap: cs.gap, ...box(g) });
  }
  /* PADDING AND FONT ONLY, NOT THE BOX. The first .sat-groups button on a domain tab is a
     COUNTRY CHIP, and its box width also moves with its CONTENT: Flag.tsx (commit 00f1198,
     the FLAG-1 drawn-flag item) replaced an emoji with an SVG and widened every chip by 3px.
     That is a deliberate change from a different workstream, and a probe reporting it as a
     dashboard regression cries wolf. This probe asks whether a DASHBOARD item moved a domain
     tab, so it watches what CSS controls and lets content be content.
     NOTE: no backticks in this block. It lives inside String.raw and a backtick ends it. */
  const sb = document.querySelector(".sat-groups button");
  if (sb) { const cs = getComputedStyle(sb);
    out.satBtn = { pad: cs.padding, fs: cs.fontSize, h: R(sb.getBoundingClientRect().height) }; }

  /* ⛔ THE GROUND, AND THE DETONATION THAT PROVED IT WAS MISSING. Everything above is
     geometry, and THEME-3 repainted every domain tab while this gate reported 6/6 green —
     the leak it exists to catch, walking straight past it. Re-detonated at the head of
     DASH-6 before any edit: --panel set to #7a1f1f under [data-theme=dark], rebuilt, and
     the جوّي tab painted 12+ visible elements in it — the map zoom controls, an input,
     .dash-secondary (314x39) and eight .card boxes (314x195) — with the gate still green,
     exit 0.
     ⚠ BOTH THE TOKEN AND WHAT IT PAINTS. A token read alone would miss a rule that stops
     USING it; a painted colour alone would miss a token renamed behind an identical value.
     .home-panel.lead paints from --panel-glass and its tiles from --panel-raised, NOT from
     --panel, which is exactly the kind of indirection that made the raw token insufficient. */
  const TOK = ["--bg", "--panel", "--panel-raised", "--panel-glass", "--hair", "--scrim"];
  const tokens = (e) => { const cs = getComputedStyle(e); const o = {};
    for (const t of TOK) o[t] = cs.getPropertyValue(t).trim(); return o; };
  const paint = (e) => { if (!e) return null; const cs = getComputedStyle(e);
    return { bg: cs.backgroundColor, bi: cs.backgroundImage, bc: cs.borderTopColor, fg: cs.color }; };
  out.ink = {
    root: tokens(document.documentElement),
    body: paint(document.body),
    lead: paint(lead),
    tile: paint(document.querySelector(".home-panel.lead .dash-stat")),
    card: paint(document.querySelector(".home-panel.lead .card")),
    secondary: paint(document.querySelector(".home-panel.lead .dash-secondary")),
  };

  /* ⛔ TILE LABELS, NEVER TILE NUMBERS — and the distinction is the honest half of this.
     96b7f29 said the fingerprint carries "the triage tile's TEXT" to catch DASH-4a's swap of
     sev.medium for the triage count. That claim was never implemented (this file was last
     touched by c58b16e), and it could not have been implemented as stated: the swapped value
     is a COUNT off the live feed, so freezing it is precisely the cry-wolf baseline the
     header above refuses. What IS stable is the LABEL each tile promises, so that is what is
     frozen — a label change, a tile appearing or disappearing, or the .n/.l structure being
     rebuilt all fail here.
     ⇒ The value swap is answered STRUCTURALLY instead, by DASH-6 part A: CountryEvents no
     longer carries a dashboard-only branch, so there is no per-surface value left to differ.
     A gate cannot freeze live content; removing the fork removes the need to. */
  out.tileText = [...document.querySelectorAll(".home-panel.lead .dash-stat")]
    .map((t) => { const l = t.querySelector(".l"); const n = t.querySelector(".n");
      return { label: (l ? l.textContent : "").trim(), hasN: !!n, kids: t.children.length }; });
  return out;
})()`;

const clickTab = async (label) => ev(`(() => {
  const b = [...document.querySelectorAll(".nav-tab")].find(x => x.textContent.includes(${JSON.stringify(label)}));
  if (!b) return "MISS"; b.click(); return "OK"; })()`);

const TABS = [["جوّي", "air"], ["بحري", "sea"], ["فضائي", "space"]];
const WIDTHS = [[1440, 900], [1900, 950]];
const result = {};

for (const [w, h] of WIDTHS) {
  await send("Emulation.setDeviceMetricsOverride",
    { width: w, height: h, deviceScaleFactor: 1, mobile: false }, S);
  for (const [label, key] of TABS) {
    await send("Page.navigate", { url: URL }, S);
    await sleep(3200);
    const hit = await clickTab(label);
    await sleep(3000);
    const m = await ev(MEASURE);
    result[`${w}/${key}`] = hit === "OK" ? m : { error: `tab ${label} not found` };
  }
}

ws.close(); chrome.kill();

/* ---------------------------------------------------------------------------------------
 * THE FINGERPRINT — what CSS decides, and nothing content moves.
 *
 * Dropped on purpose, each because the live feed moves it:
 *   lead.h / children[].h / tiles[].h  — panel and row heights follow how many events there are
 *   grids.length / tiles.length        — how many groups the feed produced
 *   children[].y                       — every child below a variable-height sibling shifts
 * Kept, because the stylesheet decides them:
 *   lead x/y/w · map box · resolved gridTemplateColumns + gap per grid CLASS · tile width and
 *   padding · child paddings and gaps · the sat button's padding, font-size and height.
 * ⚠ Grid specs are collected as a SORTED UNIQUE SET keyed by class, so two card-grids and three
 * card-grids fingerprint identically as long as their tracks resolve the same.
 * ------------------------------------------------------------------------------------- */
const fingerprint = (m) => {
  if (!m || m.error) return { error: m?.error || "no measurement" };
  const grid = [...new Set((m.grids || []).map((g) => `${g.cls}|${g.cols}|${g.gap}`))].sort();
  const kids = [...new Set((m.children || []).map((c) => `${c.cls}|${c.pad}|${c.gap}|${c.w}`))].sort();
  const t = (m.tiles || [])[0];
  return {
    lead: m.lead ? { x: m.lead.x, y: m.lead.y, w: m.lead.w } : null,
    map: m.map || null,
    grids: grid,
    children: kids,
    tile: t ? { w: t.w, pad: t.pad } : null,
    satBtn: m.satBtn || null,
    /* ⛔ COLOUR IS KEPT WHOLE, not reduced the way the boxes are. Heights and counts were
     * dropped above because the live feed moves them; a resolved colour is decided by the
     * stylesheet alone and moves for exactly one reason — someone repainted it. */
    ink: m.ink || null,
    tileText: m.tileText || null,
  };
};

const fps = Object.fromEntries(Object.entries(result).map(([k, v]) => [k, fingerprint(v)]));

if (OUT) { writeFileSync(OUT, JSON.stringify(result, null, 2)); console.log(`→ ${OUT}`); process.exit(0); }

if (BLESS) {
  writeFileSync(BASELINE, JSON.stringify(fps, null, 2) + "\n");
  console.log(`⛔ BASELINE REWRITTEN → scripts/domain-tabs-baseline.json`);
  console.log("   This says the domain tabs changed ON PURPOSE. Say why in the commit message.");
  process.exit(0);
}

const refPath = DIFF || BASELINE;
if (!existsSync(refPath)) {
  console.error(`⛔ G10 could not run — no baseline at ${refPath}. Create it with --bless (fail closed, not skipped).`);
  process.exit(1);
}
const ref = JSON.parse(readFileSync(refPath, "utf8"));
const refFps = DIFF ? Object.fromEntries(Object.entries(ref).map(([k, v]) => [k, fingerprint(v)])) : ref;

let diffs = 0;
const keys = [...new Set([...Object.keys(refFps), ...Object.keys(fps)])].sort();
for (const k of keys) {
  const A = refFps[k], B = fps[k];
  if (!A) { diffs++; console.log(`  ⛔ ${k} — NEW surface, absent from the baseline`); continue; }
  if (!B) { diffs++; console.log(`  ⛔ ${k} — MISSING, the baseline has it`); continue; }
  if (B.error) { diffs++; console.log(`  ⛔ ${k} — could not measure: ${B.error} (fail closed)`); continue; }
  if (JSON.stringify(A) === JSON.stringify(B)) { console.log(`  ✅ ${k}`); continue; }
  diffs++;
  console.log(`  ⛔ ${k} — a dashboard change moved this domain tab:`);
  for (const f of Object.keys({ ...A, ...B })) {
    const x = JSON.stringify(A[f]), y = JSON.stringify(B[f]);
    if (x !== y) console.log(`       ${f}\n         baseline ${x}\n         now      ${y}`);
  }
}

console.log(diffs === 0
  ? `\n✅ G10 the three domain tabs are exactly where the baseline left them — ${keys.length} tab×width runs`
  : `\n⛔ G10 FAILED — ${diffs} of ${keys.length} runs moved. Revert, or --bless and justify it.`);
process.exit(diffs === 0 ? 0 : 1);
