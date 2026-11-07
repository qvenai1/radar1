/**
 * G1–G9 — THE LAYOUT-SYSTEM GATE (UI-FIX).
 *
 * ⛔ WHY A NINTH GATE WHEN EIGHT ALREADY PASS. Six items (UI-1 · 1b · 2 · 2b · 2c · 2d) each passed
 * every quantitative gate this repository had, and the owner still saw the defect. UI-AUDIT measured
 * why: the existing gates ask "does anything collide / overflow / exceed a cap?" and the defect was
 * never any of those. It was that layout values are attached to SELECTORS rather than to ROLES, so
 * nothing is governed unless it is named — and the visible consequences (32–57 distinct alignment
 * lines, a 12px page margin on one side and 16px on the other, four tiles in five tracks) are
 * invisible to a collision test.
 *
 * ⛔ SO THESE GATES MEASURE THE SYSTEM, NOT THE SYMPTOM:
 *   G1 symmetry     the page's edge inset comes from ONE token, and both edges equal it
 *   G2 the line     every row-level element starts on the region's declared line, exactly
 *   G3 the offsets  the distinct near-line offsets are a small subset of the spacing ladder
 *   G4 the cap      every control-role element is within the field cap
 *   G5 sameness     siblings of one class share a width; twin grids produce twin tiles
 *   G6 tracks       no grid reserves a track it has no child for
 *   G8 one source   no px literal duplicates a token's value; no token lacks a reader
 *   G9 ink          no element's own glyphs are covered by a foreign element
 * (G7, emptiness, is DIAGNOSTIC ONLY and never blocks — see the note at reportVoids.)
 *
 * ⛔ AND IT FAILS CLOSED. A gate that cannot measure must not pass: no browser, no server, no
 * surface reached — all are failures, not skips. `--detonate` proves each gate can fail, because
 * this project has been bitten three times by a check that could only pass (BE-SUMMARY-4's
 * assertions, UI-3's cosmetic constraint 4, and UI-2c's css-syntax counter whose first verdict was
 * its own bug).
 *
 * Usage:  node scripts/verify-layout-system.mjs [--full] [--detonate] [--url=http://…]
 *   --full      the whole 4×3×9 matrix instead of the 14-state gate set
 *   --detonate  inject one violation per gate and assert the gate catches it
 */
import { spawn } from "node:child_process";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import path from "node:path";

const ARGS = process.argv.slice(2);
const FULL = ARGS.includes("--full");
const DETONATE = ARGS.includes("--detonate");
const URL = (ARGS.find((a) => a.startsWith("--url=")) || "--url=http://127.0.0.1:8000/").slice(6);
const CHROME = ["/usr/bin/google-chrome", "/usr/bin/chromium", "/snap/bin/chromium", "/usr/bin/chromium-browser"]
  .find((p) => existsSync(p));
const PORT = 9800 + (process.pid % 120);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const R = (n) => Math.round(n * 10) / 10;

/* ---------- the ladder and the roles, read from the sheet rather than restated ---------- */
const STYLES = path.join(import.meta.dirname, "..", "src", "styles");
const SHEETS = readdirSync(STYLES).filter((f) => f.endsWith(".css"));
const CSS = Object.fromEntries(SHEETS.map((f) => [f, readFileSync(path.join(STYLES, f), "utf8")]));
const CSS_NOCOMMENT = Object.fromEntries(Object.entries(CSS).map(([f, t]) => [f, t.replace(/\/\*[\s\S]*?\*\//g, "")]));

const fail = [], pass = [], diag = [], report = [];
const check = (ok, name, detail = "") => (ok ? pass : fail).push(name + (detail ? ` — ${detail}` : ""));

/* ================= G8: ONE SOURCE (static; no browser needed) ================= */
const LAYOUT_PROP = /^(width|max-width|min-width|height|max-height|min-height|padding|padding-\w+|padding-inline|padding-block|margin|margin-\w+|margin-inline|margin-block|gap|row-gap|column-gap|inset|inset-\w+|inset-inline|inset-inline-\w+|inset-block|inset-block-\w+|top|right|bottom|left|grid-template-columns|grid-template-rows)$/;
function g8() {
  const tokenVal = {}, tokenRaw = {}, decls = [], allDecls = [];
  for (const [f, raw] of Object.entries(CSS_NOCOMMENT)) {
    const re = /([^{}]+)\{([^{}]*)\}/g; let m;
    while ((m = re.exec(raw))) {
      const sel = m[1].trim().replace(/\s+/g, " ");
      if (sel.startsWith("@")) continue;
      for (const d of m[2].split(";")) {
        const i = d.indexOf(":"); if (i < 0) continue;
        const prop = d.slice(0, i).trim(), val = d.slice(i + 1).trim();
        if (!val) continue;
        if (prop.startsWith("--")) { tokenRaw[prop] = val; continue; }
        allDecls.push(val);
        if (!LAYOUT_PROP.test(prop)) continue;
        decls.push({ f, sel, prop, val });
      }
    }
  }
  /* ⛔ TOKEN VALUES ARE RESOLVED, NOT PATTERN-MATCHED. UI-FIX step 5 rewrote the ladder as multiples of
   * its base (`--sp-3: calc(3 * var(--sp-1))`) so the base would have readers and "4px base" would be
   * arithmetic rather than a comment. A gate that only understood `12px` would have silently lost the
   * whole ladder at that moment — G8a's duplicate detection and G3's allowed-offset set both read it —
   * and reported everything green. ⛔ That is the failure mode this file exists to prevent, so the
   * resolver came in with the change that needed it. */
  const resolve = (v, depth = 0) => {
    if (v == null || depth > 8) return NaN;
    const mult = /^calc\(\s*(\d+(?:\.\d+)?)\s*\*\s*var\((--[\w-]+)\)\s*\)$/.exec(v.trim());
    if (mult) return Number(mult[1]) * resolve(tokenRaw[mult[2]], depth + 1);
    const alias = /^var\((--[\w-]+)\)$/.exec(v.trim());
    if (alias) return resolve(tokenRaw[alias[1]], depth + 1);
    const px = /^(\d+(?:\.\d+)?)px$/.exec(v.trim());
    return px ? parseFloat(px[1]) : NaN;
  };
  for (const [t, v] of Object.entries(tokenRaw)) { const n = resolve(v); if (Number.isFinite(n)) tokenVal[t] = n; }

  /* ⛔ SCOPE, DECLARED: this gate governs the SECTION-level geometry this project's layout items own —
   * the page inset, the region padding/gap, the seat, the floating panels, the nav clearance and the
   * grids that lay out rows. It does NOT police the per-component 1–7px paddings inside cards, chips
   * and table cells: UI-2c scoped those out with a measurement (eight distinct sub-8px values across
   * a dozen rules on four surfaces) and sweeping them is a redesign, not a layout fix. A gate whose
   * scope is not written down is a gate that will be widened by whoever next reads it. */
  const SECTION = /^(\.map-host|\.workspace|\.stats-view|\.home-panel|\.map-detail|\.draw-hint|\.navbar|\.side|\.domain-detail|\.stat-grid|\.card-grid|\.shell|\.main|:root)/;
  const sectionDecls = decls.filter((d) => SECTION.test(d.sel));
  const byValue = new Map();
  for (const [t, v] of Object.entries(tokenVal)) { if (!byValue.has(v)) byValue.set(v, []); byValue.get(v).push(t); }

  const dupes = [];
  for (const d of sectionDecls) {
    for (const n of [...d.val.matchAll(/(?<![\w-])(\d+(?:\.\d+)?)px/g)].map((x) => parseFloat(x[1]))) {
      const toks = byValue.get(n);
      if (toks) dupes.push(`${d.sel} { ${d.prop}: ${d.val} } duplicates ${toks.join("/")} (=${n}px) [${d.f}]`);
    }
  }
  check(dupes.length === 0, "G8a section-level geometry uses no px literal that a token already means",
    dupes.length ? `${dupes.length}: ` + dupes.slice(0, 6).join(" · ") : "zero duplicated distances");

  /* ⛔ USAGE IS TESTED ACROSS **EVERY** DECLARATION, not just the layout ones, and the first version
   * got this wrong in a way that matters: it searched only `decls` (filtered to layout properties), so
   * `--radius-sm` and `--radius-xs` — read all over the sheet as `border-radius` — were reported as
   * orphans. A gate that invents orphans gets its orphan list ignored, which is how the two REAL ones
   * (`--sp-1`, `--sp-2`) would have survived another six items. */
  const orphans = Object.keys(tokenVal).filter((t) => {
    const used = allDecls.some((v) => v.includes(`var(${t})`)) || Object.values(tokenRaw).some((v) => v.includes(`var(${t})`));
    return !used;
  });
  check(orphans.length === 0, "G8b every declared geometry token has a reader",
    orphans.length ? `orphans: ${orphans.join(", ")}` : `${Object.keys(tokenVal).length} tokens, all read`);

  /* ================= G8c: A TOKEN'S OWN COMMENT MUST AGREE WITH THE TOKEN =================
   * ⛔ WHY THIS EXISTS. The file header of tokens.css claimed --surface-max was 1,180px for
   * three weeks after the token had become 920px — a fully-measured justification of a world
   * that no longer existed, sitting above the live value. This project's most repeated defect,
   * caught by nothing: G8a reads only declarations (comments are stripped), and no other gate
   * reads prose. So:
   *
   *   For every custom-property declaration in tokens.css whose value RESOLVES to px, gather
   *   its OWN comment — the contiguous comment block immediately above it plus any trailing
   *   comment on the declaration line — and check every `Npx` named in it:
   *     · N == this token's value                      → fine
   *     · N == ANY resolvable token's value            → a cross-reference, fine
   *     · marked as history/reference                  → fine
   *     · otherwise                                    → ⛔ violation
   *
   *   The comment must also NAME the token (`--surface-max …`) for a mismatch to count: prose
   *   about a child measurement ("renders 27px tall") is not a claim about the token itself.
   *
   * ⛔ THE MARKING SYNTAX, DEFINED ONCE (use one of these; anything else is a violation):
   *     · "… was 1,180px …"        — the natural past tense, directly before the number
   *     · "… 1,180px (historic) "  — an explicit tag directly after the number
   *     · a line carrying "[hist]" or "[ref]" — [hist] = superseded measurement,
   *       [ref] = a live sub-measurement that is not the token's own value
   * Anything unmarked is treated as a CURRENT claim about the token and must match it. */
  function scanTokenComments(src) {
    const viols = [];
    /* ⛔ LINEAR TOKENIZER, AND THE FIRST DRAFT IS WHY. The regex-chain draft
     * (/((?:\/\*[\s\S]*?\*\/\s*)+)(--…)/) backtracked a lazy body across its own
     * terminator and chained the FILE HEADER onto --sp-1; its tempered repair
     * ((?:[^*]|\*(?!/))*) then went exponential on this file's " * " comment rails
     * (40 s+, killed). So: collect comment spans with the plain per-comment matcher,
     * then walk them — a chain continues only across WHITESPACE gaps, so an
     * intervening `:root {` correctly orphans the file header from every token. */
    const spans = [];
    const cre = /\/\*[\s\S]*?\*\//g;
    let cm;
    while ((cm = cre.exec(src))) spans.push({ s: cm.index, e: cm.index + cm[0].length, text: cm[0].slice(2, -2) });
    let i = 0;
    while (i < spans.length) {
      let j = i;
      while (j + 1 < spans.length && /^\s*$/.test(src.slice(spans[j].e, spans[j + 1].s))) j++;
      const chainEnd = spans[j].e;
      const dm = /^\s*(--[\w-]+\s*:\s*[^;]*;)([ \t]*\/\*[^\n]*?\*\/)?/.exec(src.slice(chainEnd));
      if (!dm) { i = j + 1; continue; }
      const decl = dm[1], trailing = (dm[2] ?? "").replace(/^\/\*/, "").replace(/\*\/$/, "");
      const name = /--([\w-]+)\s*:/.exec(decl)?.[1] ?? "?";
      const val = decl.slice(decl.indexOf(":") + 1).replace(/;\s*$/, "").trim();
      const resolved = resolve(val);
      if (Number.isFinite(resolved)) {
        const text = spans.slice(i, j + 1).map((sp) => sp.text).join("\n") + "\n" + trailing;
        if (text.includes(`--${name}`)) {          /* must NAME the token to claim its value */
          const numRe = /(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)px/g;
          let n;
          while ((n = numRe.exec(text))) {
            const v = parseFloat(n[1].replace(/,/g, ""));
            if (v === resolved) continue;
            if (Object.values(tokenVal).includes(v)) continue;   /* cross-reference to another token */
            const lineStart = text.lastIndexOf("\n", n.index) + 1;
            const lineEnd = text.indexOf("\n", n.index) < 0 ? text.length : text.indexOf("\n", n.index);
            const line = text.slice(lineStart, lineEnd);
            if (/\[(hist|ref)\]/i.test(line)) continue;
            const before = text.slice(Math.max(0, n.index - 24), n.index);
            const after = text.slice(n.index + n[0].length, n.index + n[0].length + 14);
            if (/\b(was|were)\b[\s,]*(the )?$/i.test(before)) continue;
            if (/^\s*\((historic|superseded)\)/i.test(after)) continue;
            viols.push(`--${name}=${resolved}px but its comment claims ${n[1]}px unmarked: …${text.slice(Math.max(0, n.index - 30), n.index + n[0].length + 10).replace(/\n/g, " ")}…`);
          }
        }
      }
      i = j + 1;
    }
    return [...new Set(viols)];
  }
  const tokSrc = CSS["tokens.css"] ?? "";
  const g8c = scanTokenComments(tokSrc);
  check(g8c.length === 0, "G8c a token's own comment agrees with the token (or is marked was/[hist]/[ref])",
    g8c.length ? g8c.join("\n  ⛔ ") : "clean");

  return { tokenVal, tokenRaw, scanTokenComments };
}
const { tokenVal, scanTokenComments } = g8();
const LADDER = [0, ...Object.entries(tokenVal).filter(([k]) => /^--sp-\d$/.test(k)).map(([, v]) => v)].sort((a, b) => a - b);

/* ================= the browser half ================= */
if (!CHROME) {
  fail.push("G1–G7,G9 could not be measured — no Chrome/Chromium binary found (fail closed, not skipped)");
  finish();
}
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, "--no-first-run",
  "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
  `--user-data-dir=/tmp/verify-layout-system-${PORT}`, "about:blank"], { stdio: "ignore" });
let ws, id = 0; const pending = new Map();
const send = (m, p = {}, s) => new Promise((res, rej) => { const x = { id: ++id, method: m, params: p }; if (s) x.sessionId = s; pending.set(x.id, { res, rej }); ws.send(JSON.stringify(x)); });
let wsUrl;
for (let i = 0; i < 80; i++) { try { wsUrl = (await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json()).webSocketDebuggerUrl; break; } catch { await sleep(250); } }
if (!wsUrl) { fail.push("G1–G7,G9 could not be measured — Chrome did not open a debugging port (fail closed)"); finish(); }
ws = new WebSocket(wsUrl);
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result); } };
await new Promise((r) => (ws.onopen = r));
const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId: S } = await send("Target.attachToTarget", { targetId, flatten: true });
await send("Page.enable", {}, S); await send("Runtime.enable", {}, S);
const ev = (x) => send("Runtime.evaluate", { expression: x, returnByValue: true, awaitPromise: true }, S).then((r) => r.result?.value);
try { const probe = await fetch(URL); if (!probe.ok) throw new Error(probe.status); }
catch (e) { fail.push(`G1–G7,G9 could not be measured — ${URL} unreachable (${e.message}); build and serve first (fail closed)`); finish(); }

/* ---------- the measurement, injected once ---------- */
const MEASURE = String.raw`(() => {
  const R = (n) => Math.round(n * 10) / 10;
  const vw = innerWidth, vh = innerHeight;
  const desc = (e) => e === document.body ? "body" : e === document.documentElement ? "html"
    : e.tagName.toLowerCase() + (typeof e.className === "string" && e.className.trim()
      ? "." + e.className.trim().split(/\s+/).slice(0, 3).join(".") : "");
  const main = document.querySelector(".main");
  if (!main) return { error: "no .main" };
  const region = document.querySelector(".workspace") || document.querySelector(".stats-view");
  if (!region) return { error: "no content region" };

  const cs = (e) => getComputedStyle(e);
  /* ⛔ DASH-IDLE AMENDMENT — the skipped subtree of a CLOSED <details> keeps STALE layout
   * boxes in Chromium. Measured on entity/rest: with details.sev-legend closed,
   * b.sl-word still reported a 23×15px box at y 561 — underneath .dash-alerts-head,
   * which owns that band — while painting and hit-testing correctly showed nothing
   * there. Every geometric gate downstream (offsets, chains, occlusion) would read a
   * ghost. So anything inside a closed details is excluded, EXCEPT the summary subtree,
   * which is the part a closed disclosure actually shows. */
  const hiddenByDetails = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (p.tagName === "DETAILS" && !p.open) {
        for (let s = el; s && s !== p; s = s.parentElement) if (s.tagName === "SUMMARY") return false;
        return true;
      }
    }
    return false;
  };
  const all = [...main.querySelectorAll("*")].filter((e) => {
    if (e.closest(".leaflet-pane")) return false;
    if (hiddenByDetails(e)) return false;
    if (e.tagName === "svg" || e.closest("svg")) return e.classList.contains("chart-svg");
    const s = cs(e);
    if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) === 0) return false;
    const r = e.getBoundingClientRect();
    return r.width >= 2 && r.height >= 2;
  });
  /* clip-aware: a row scrolled out of a list still has a rect (UI-2c's correction) */
  const clipOf = (el) => { let x0 = 0, y0 = 0, x1 = vw, y1 = vh;
    for (let p = el.parentElement; p; p = p.parentElement) { const s = cs(p);
      if (s.overflowX === "visible" && s.overflowY === "visible") continue;
      const r = p.getBoundingClientRect();
      x0 = Math.max(x0, r.x); y0 = Math.max(y0, r.y); x1 = Math.min(x1, r.right); y1 = Math.min(y1, r.bottom); }
    return { x0, y0, x1, y1 }; };
  const vis = (el) => { const r = el.getBoundingClientRect(), c = clipOf(el);
    const x = Math.max(r.x, c.x0), y = Math.max(r.y, c.y0), rr = Math.min(r.right, c.x1), bb = Math.min(r.bottom, c.y1);
    return (rr - x >= 2 && bb - y >= 2) ? { x, y, w: rr - x, h: bb - y, r: rr, b: bb } : null; };

  /* ---- G1: the page's two insets, and whether one token owns both ---- */
  const seat = document.querySelector(".map-host");
  /* ⛔ '.home-panel' ONLY, and '.map-detail' deliberately NOT: the first is inset from the VIEWPORT
   * edge and so is a page-edge element; the second is inset from the SEAT's edge and past Leaflet's
   * control lane (--sp-3 + --map-chrome-lane = 68), which is a different distance with its own
   * declared source. Including it made G1 report "panel 68" as if the page had a third margin. */
/* ⛔ DASH-7 — RENDERED, NOT MERELY PRESENT, and this is the SECOND time the distinction has
 * mattered: seatIsSeated needed it when the map became display:none, and the floating
 * .home-panel needs it now for the same reason. React keeps the element mounted while CSS
 * takes it out of layout, and a display:none element still answers getBoundingClientRect()
 * with zeros — which G1 read as "panel 72" and G1b as "panel clears the bar by -55", two
 * confident numbers about a box nobody paints. offsetParent is null exactly when the element
 * or an ancestor is display:none. */
  const panelEl = document.querySelector(".home-panel");
  const panel = panelEl && panelEl.offsetParent ? panelEl : null;
  const rs = cs(region), regionPadStart = parseFloat(rs.paddingRight) || 0;   /* RTL: inline start = right */
  /* ⛔ Reading right !== "auto" WAS A FAIL-OPEN BUG AND IT PASSED G1 VACUOUSLY ON ALL 16 RUNS.
   * For an absolutely positioned box, getComputedStyle returns the USED value of right — a pixel
   * number — even when the declaration is auto. So "is the seat stacked?" was true everywhere, every
   * run was skipped, and the gate reported agreement it had never checked. The stacked state is read
   * from the breakpoint the sheet actually switches on, which is the only source that cannot lie. */
  /* ⛔ AND THE SEAT COUNTS ONLY WHEN IT IS A SEAT. On the stats surface there is no '.workspace'
   * sibling, so '.map-host:has(~ .workspace)' does not apply and the map is full-bleed at left: 0 —
   * BY DESIGN, as the canvas under an opaque curtain, not as content against the page edge. Comparing
   * its 0 to the region's inset reported a page-margin defect on the one surface that has no page
   * margin to get wrong. The test is structural (does the seat rule apply?), not a value allowlist. */
  /* ⛔ DASH-6 — "SEATED" MEANS RENDERED IN ITS SEAT, NOT MERELY PRESENT IN THE DOM. The
   * predicate was structural only (a .map-host with a .workspace sibling), which was complete
   * while the map was either seated or unmounted: on stats it is unmounted, so seat is null
   * and every seat measurement is skipped. DASH-6b added a third state — MOUNTED BUT NOT
   * DISPLAYED (.shell.entity-surface .map-host with display:none, keeping the single Leaflet
   * instance alive) — and a display:none element still answers getBoundingClientRect() with
   * zeros. G1b then measured the seat's top at y=0 and reported "seat clears the bar by -55"
   * on all four entity runs: a real number about a box nobody renders.
   * NOTE: no backticks in this block either — String.raw, same trap as G9's amendment.
   * ⚠ offsetParent is the check, not a width test: it is null exactly when the element or an
   * ancestor is display:none, and it does not confuse a hidden map with a legitimately
   * zero-width one. */
  const seatIsSeated = !!(seat && seat.offsetParent
    && seat.parentElement && seat.parentElement.querySelector(".workspace"));
  const g1 = {
    seatLeft: seatIsSeated ? parseFloat(cs(seat).left) || 0 : null,
    seatStacked: matchMedia("(max-width: 1319px)").matches,
    regionPadInlineStart: regionPadStart,
    regionPadInlineEnd: parseFloat(rs.paddingLeft) || 0,
    panelInset: panel ? R(Math.min(...[cs(panel).insetInlineStart, cs(panel).insetInlineEnd, cs(panel).left, cs(panel).right]
      .map((v) => parseFloat(v)).filter((v) => Number.isFinite(v) && v > 0))) : null,
  };

  /* ---- G1b: the nav clearance, VERIFIED rather than trusted ----
   * ⛔ --nav-h is a hardcoded 43px copy of a height nothing declares, and UI-FIX step 5 kept it after
   * measuring that the bar is 45px at or below 680px, so pinning it would clip. The honest way to keep
   * a copy is to check it: the rendered gap between the bar's bottom and every region that clears it
   * must equal --nav-gap, and the bar's rendered height must equal --nav-h. If either drifts, the
   * single-source claim is false and this says so instead of the next item discovering it. */
  const nav = document.querySelector(".navbar");
  const navBottom = nav ? nav.getBoundingClientRect().bottom : null;
  const gapTo = (el) => (el && navBottom != null) ? R(el.getBoundingClientRect().y - navBottom) : null;
  /* ⛔ A TOKEN'S LENGTH IS MEASURED, NOT PARSED. getPropertyValue on a custom property returns its
   * token stream — after step 5 that is 'calc(6 * 4px)' for --nav-gap — and parseFloat of that is NaN.
   * The first version of G1b therefore compared every gap against null and failed all 16 runs on its
   * own arithmetic. A probe element renders the value and the browser does the resolving. */
  const lenOf = (token) => {
    const probe = document.createElement("div");
    probe.style.cssText = "position:absolute;visibility:hidden;height:0;width:var(" + token + ")";
    main.appendChild(probe);
    const w = probe.getBoundingClientRect().width;
    probe.remove();
    return Number.isFinite(w) && w > 0 ? R(w) : null;
  };
  g1.navH = nav ? R(nav.getBoundingClientRect().height) : null;
  g1.navHToken = lenOf("--nav-h");
  g1.navGapToken = lenOf("--nav-gap");
  g1.pageInsetToken = lenOf("--page-inset");
  g1.navGaps = {
    seat: seatIsSeated && !matchMedia("(max-width: 1319px)").matches ? gapTo(seat) : null,
    /* ⛔ THE FIRST PAINTED CHILD, NOT THE BOX. One region clears the bar with an offset, the other with
     * padding; comparing boxes reported the stats curtain at -55 (it runs from y 0 under the floating
     * bar by design) and hid that the two regions put their first content 24px and 40px below the bar. */
    regionContent: matchMedia("(max-width: 1319px)").matches && region.classList.contains("workspace")
      ? null
      : (() => { const k = [...region.children].filter((c) => vis(c)); return k.length ? R(Math.min(...k.map((c) => c.getBoundingClientRect().y)) - navBottom) : null; })(),
    panel: panel ? gapTo(panel) : null,
  };

  /* ---- G2 / G3: the declared line, and who is off it ---- */
  const rb = region.getBoundingClientRect();
  const regionInner = R(rb.width - regionPadStart - (parseFloat(rs.paddingLeft) || 0));
  /* ⛔ A CENTRED REGION'S LINE IS ITS TRACK'S START, NOT ITS PADDING EDGE. The stats surface declares
   * 'justify-content: center' and does so deliberately — UI-1 sized its cards to the charts' authored
   * 720px and centred them, and UI-2d recorded keeping that as an exception because no map sits beside
   * it. Measuring it against the padding edge reported every one of its rows as "off by 327", which is
   * the gate misreading a documented decision as a defect. So the line is read from the region's own
   * justification: the padding edge when it starts, the track's own edge when it centres. */
  const centred = /center|space-|end/.test(rs.justifyContent);
  let line = R(rb.right - regionPadStart);              /* RTL: the inline-start content edge */
  if (centred) {
    const kids = [...region.children].filter((k) => vis(k)).map((k) => k.getBoundingClientRect().right);
    if (kids.length) line = R(Math.max(...kids));
  }
  /* ⛔ G2 AND G3 ARE STRUCTURAL, NOT A VALUE ALLOWLIST, and the first version of both was the wrong
   * question. UI-AUDIT measured 32–57 distinct start lines and the instinct was to allow a short list of
   * offsets. But the chains behind those offsets are all DECLARED: 0 = the region's padding, 1 = a
   * hairline border, 12 = the sidebar's padding, 13 = a metric's padding + its hairline, 15 = a card's
   * 14px padding + its hairline. ⛔ An allowlist of sums can never accept a nested panel's legitimate
   * inset, and can never reject a 14px padding that happens to sum to an allowed number. So:
   *   G2 — every element starts on ITS OWN container's content line. A nested panel re-establishes the
   *        line for its children; that is what "re-establishing the line" means. For a multi-track
   *        container the requirement is that its NEAREST child sits on the line (the others are in
   *        later tracks, which is the container's declared business).
  /*   G3 — every inline-start PADDING in every ancestor chain is a ladder value. Inline-start
  *        borders wider than a hairline are ACCENT RULES / severity marks — each width is a
  *        documented design decision (RANK-NAME's frozen silhouettes), not rhythm — and were
  *        moved out of this check by DASH-IDLE's measurement (see the note at the verdict).
  *        The ragged lines the owner saw came from 3 · 10 · 14px
  *        paddings pulled from five unrelated sources, and that is what this rejects.
   * ⚠ Fractional offsets (34.2 · 37.7 · 40.9 …) belong to text and flex-positioned items inside a row,
   * not to a container's inset — a sum-based rule flagged them forever and could never be satisfied. */
  const rows = [], offsets = new Map(), badChain = new Map();
  const LADDER_SET = __LADDER__;   /* substituted from the Node side; the page cannot see Node scope */
  const groupMin = new Map();
  for (const e of all) {
    if (e === region || !region.contains(e)) continue;
    const v = vis(e); if (!v) continue;
    const b = e.getBoundingClientRect();
    const off = R(line - b.right);
    if (off >= -0.6 && off <= 60) {
      if (!offsets.has(off)) offsets.set(off, []);
      offsets.get(off).push(desc(e));
    }
    /* --- G3: the chain --- */
    for (let p = e.parentElement; p && p !== region.parentElement; p = p.parentElement) {
      const ps = cs(p);
      const pad = R(parseFloat(ps.paddingRight) || 0);
      if (pad === 0) continue;
      if (LADDER_SET.includes(pad)) continue;
      const k = desc(p) + " padding " + pad;
      if (!badChain.has(k)) badChain.set(k, 0);
      badChain.set(k, badChain.get(k) + 1);
      if (p === region) break;
    }
    /* --- G2: the local line ---
     * ⛔ BLOCK-LEVEL CHILDREN ONLY, and the first version got this wrong in a way that made the gate
     * unsatisfiable: it flagged <b> runs inside a sentence (off 34.2 · 37 · 140.9). An inline element's
     * position is decided by the text flow, so demanding it start on its container's line is not a
     * layout rule at all — it is a demand that prose begin at the margin. */
    const ecs = cs(e);
    /* ⛔ DASH-IDLE AMENDMENT — inline-flex / inline-grid join the text-flow skip. An
     * inline-level element's position is decided by the flow it sits in, and that was this
     * skip's documented rationale when it covered only inline / inline-block. The gate
     * never met a .badge chip before: measured on detail/sea, .src-badge follows its
     * vessel's name INSIDE a td, so its offset is the name's width — prose, not layout.
     * Block-level children of block containers are still required on the line. */
    if (/^(inline|inline-block|inline-flex|inline-grid|contents|table-cell|table-row|table-row-group|table-header-group|table-footer-group)$/.test(ecs.display)) continue;
    const par = e.parentElement;
    if (!par) continue;
    const pcs = cs(par), pb = par.getBoundingClientRect();
    const contentStart = R(pb.right - (parseFloat(pcs.paddingRight) || 0) - (parseFloat(pcs.borderRightWidth) || 0));
    const localOff = R(contentStart - b.right);
    /* ⛔ AND A CONTAINER THAT DECLARES A NON-START JUSTIFICATION HAS NO "NEAREST child on the line" TO
     * demand: '.stats-view' centres its chart cards deliberately (UI-1 sized them to the charts' authored
     * 720px), and the gate reported that documented decision as a 329px defect — the same mistake the
     * region-level line already had to be taught about. */
    if (/center|space-|end|right/.test(pcs.justifyContent)) continue;
    const multiTrack = /flex|grid/.test(pcs.display);
    if (multiTrack) {
      const key = desc(par) + "@" + R(pb.right);
      if (!groupMin.has(key) || localOff < groupMin.get(key).off) groupMin.set(key, { off: localOff, par: desc(par), el: desc(e) });
    } else if (Math.abs(localOff) > 0.6) {
      rows.push({ el: desc(e), parent: desc(par), off: localOff, w: R(b.width) });
    }
  }
  for (const g of groupMin.values()) if (Math.abs(g.off) > 0.6)
    rows.push({ el: g.el, parent: g.par + " (nearest child of a multi-track container)", off: g.off, w: 0 });

  /* ---- G4: the control role. ⛔ BY ROLE, NOT BY SELECTOR LIST — that is the whole point. ---- */
  const CAP = parseFloat(cs(document.documentElement).getPropertyValue("--field-max")) || 420;
  const controls = [];
  for (const e of all) {
    if (!region.contains(e)) continue;
    const t = e.tagName;
    const isControl = t === "INPUT" || t === "SELECT" || t === "TEXTAREA"
      || e.classList.contains("seg") || (t === "BUTTON" && e.parentElement && e.parentElement.classList.contains("search-row"));
    if (!isControl) continue;
    const b = e.getBoundingClientRect();
    if (b.width > CAP + 0.6) controls.push({ el: desc(e), w: R(b.width), cap: CAP, parent: desc(e.parentElement) });
  }

  /* ---- G5: siblings of one class must share a width; twin grids must produce twin tiles ---- */
  const groups = new Map();
  for (const e of all) {
    const p = e.parentElement; if (!p) continue;
    const sig = desc(p) + " > " + (typeof e.className === "string" ? e.className.trim() : "") + "|" + e.tagName;
    if (!groups.has(sig)) groups.set(sig, []);
    groups.get(sig).push(R(e.getBoundingClientRect().width));
  }
  const UNIFORM = /(metric|dash-stat|result-card|chart-card)/;   /* the roles that must be equal-sized */
  const nonuniform = [];
  for (const [sig, ws] of groups) {
    if (ws.length < 2 || !UNIFORM.test(sig)) continue;
    const spread = R(Math.max(...ws) - Math.min(...ws));
    if (spread > 0.6) nonuniform.push({ sig, n: ws.length, spread, widths: ws.slice(0, 8) });
  }
  /* and across grids of the same role on one surface */
  const tileW = new Map();
  for (const g of document.querySelectorAll(".stat-grid")) {
    for (const t of g.children) { const w = R(t.getBoundingClientRect().width); tileW.set(w, (tileW.get(w) || 0) + 1); }
  }
  const twinGrids = tileW.size > 1 ? { widths: [...tileW.keys()], counts: [...tileW.values()] } : null;

  /* ---- G6: a grid must not reserve a track it has no child for ---- */
  const emptyTracks = [];
  for (const e of all) {
    const s = cs(e);
    if (!s.display.includes("grid")) continue;
    /* ⛔ A COLLAPSED TRACK RESERVES NOTHING, and counting it would have made this gate reject its own
     * fix: 'auto-fit' collapses an unused track and Chrome reports it as '0px' in the computed value, so
     * a naive split counts 5 tracks for 4 tiles exactly as 'auto-fill' did. G6's subject is RESERVED
     * space, not the track list. */
    const tracks = s.gridTemplateColumns.split(" ").filter((x) => x && x !== "none" && parseFloat(x) > 0.5).length;
    const kids = [...e.children].filter((k) => { const ks = cs(k); return ks.display !== "none"; }).length;
    if (tracks > 1 && kids > 0 && kids < tracks) {
      /* only if the children do not span: a single spanning child in 3 tracks is fine */
      const spans = [...e.children].some((k) => { const gc = cs(k).gridColumn; return /span|\//.test(gc) && gc !== "auto"; });
      if (!spans) emptyTracks.push({ el: desc(e), tracks, kids, cols: s.gridTemplateColumns });
    }
  }

  /* ---- G7 (diagnostic): the largest empty rectangle ---- */
  const STEP = 10, SHELL = /^(workspace|stats-view|main|shell|card-grid|domain-detail|stat-grid)$/;
  const cols = Math.floor(vw / STEP), rowsN = Math.floor(vh / STEP);
  const gridMap = [];
  for (let gy = 0; gy < rowsN; gy++) { const row = [];
    for (let gx = 0; gx < cols; gx++) { const h = document.elementFromPoint(gx * STEP + STEP / 2, gy * STEP + STEP / 2);
      const cl = h && typeof h.className === "string" ? h.className.trim() : "";
      row.push(!h || h === document.body || h === document.documentElement || SHELL.test(cl) ? 0 : 1); }
    gridMap.push(row); }
  let biggest = 0; const heights = new Array(cols).fill(0);
  for (let gy = 0; gy < rowsN; gy++) {
    for (let gx = 0; gx < cols; gx++) heights[gx] = gridMap[gy][gx] === 0 ? heights[gx] + 1 : 0;
    const st = [];
    for (let gx = 0; gx <= cols; gx++) { const hgt = gx === cols ? 0 : heights[gx]; let start = gx;
      while (st.length && st[st.length - 1].h >= hgt) { const top = st.pop();
        const w = (gx - top.i) * STEP, hh = top.h * STEP;
        if (w >= 40 && hh >= 40) biggest = Math.max(biggest, w * hh); start = top.i; }
      st.push({ i: start, h: hgt }); }
  }

  /* ---- G9: is any element's own ink covered by a foreign element? ---- */
  const inkRect = (el) => { let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, found = false;
    for (const n of el.childNodes) { if (n.nodeType !== 3 || !n.nodeValue.trim()) continue;
      const rg = document.createRange(); rg.selectNodeContents(n);
      for (const r of rg.getClientRects()) { if (r.width < .5 || r.height < .5) continue;
        found = true; x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y); x1 = Math.max(x1, r.right); y1 = Math.max(y1, r.bottom); } }
    return found ? { x: x0, y: y0, r: x1, b: y1, w: x1 - x0, h: y1 - y0 } : null; };
  const occluded = [];
  for (const e of all) {
    /* ⛔ DASH-6 AMENDMENT — A CLOSED <details> STILL REPORTS RANGE RECTS FOR ITS CONTENT.
     * Same false-positive class as the -webkit-line-clamp one below, different mechanism:
     * Chrome collapses a closed <details> through content-visibility:hidden on the
     * ::details-content pseudo, so the children are NOT PAINTED while
     * Range.getClientRects() still returns their laid-out geometry. G9 then reads that
     * phantom ink, finds the following element painted there, and condemns pixels nobody
     * can see. Measured 2026-08-24: details.sev-legend reports open=false and a 23px box,
     * while its .sl-list reports a 109px box on لوحة and 124.8px on جوّي — i.e. the phantom
     * is present on the domain tabs too and predates DASH-6; the surface release only moved
     * the geometry enough for the overlap to sample a hit.
     * ⚠ Detonated rather than assumed: with the legend CLOSED the browser's own
     * checkVisibility() returns false (so the ink is phantom) and this skips it; with it
     * OPEN the skip does not apply; and the summary is never skipped, because a summary IS
     * painted while closed.
     * NOTE: no backticks in this block. It lives inside String.raw and a backtick ends it —
     * which is exactly how the first draft of this comment broke the gate. */
    const inClosedDetails = e.closest("details:not([open])");
    if (inClosedDetails && !e.closest("summary")) continue;
    const ik = inkRect(e); if (!ik) continue;
    const c = clipOf(e);
    /* ⛔ DASH-IDLE AMENDMENT — the element's OWN overflow clip bounds its ink too.
     * Measured on entity/rest: .rc-sub clamps its headline with -webkit-line-clamp +
     * overflow:hidden, but the text's Range rects still report every laid-out line,
     * including the ones painted out — so the phantom tail overlapped the badge row
     * beneath it and G9 condemned pixels nobody can see. Ancestors were already
     * respected (clipOf); the element itself was not. */
    const os = cs(e);
    let gx0 = c.x0, gy0 = c.y0, gx1 = c.x1, gy1 = c.y1;
    if (!/visible/.test(os.overflow)) {
      const b = e.getBoundingClientRect();
      gx0 = Math.max(gx0, b.x); gy0 = Math.max(gy0, b.y);
      gx1 = Math.min(gx1, b.right); gy1 = Math.min(gy1, b.bottom);
    }
    const x = Math.max(ik.x, gx0), y = Math.max(ik.y, gy0), rr = Math.min(ik.r, gx1), bb = Math.min(ik.b, gy1);
    if (rr - x < 2 || bb - y < 2) continue;
    const pts = [["c", x + (rr - x) / 2, y + (bb - y) / 2], ["tl", x + 1.5, y + 1.5], ["tr", rr - 1.5, y + 1.5],
      ["bl", x + 1.5, bb - 1.5], ["br", rr - 1.5, bb - 1.5], ["t", x + (rr - x) / 2, y + 1.5],
      ["b", x + (rr - x) / 2, bb - 1.5], ["l", x + 1.5, y + (bb - y) / 2], ["rr", rr - 1.5, y + (bb - y) / 2]]
      .filter(([, px, py]) => px >= 0 && py >= 0 && px < vw && py < vh);
    const bad = [];
    for (const [tag, px, py] of pts) { const hit = document.elementFromPoint(px, py);
      if (!hit || hit === e || e.contains(hit) || hit.contains(e)) continue;
      bad.push(tag + ":" + desc(hit)); }
    if (bad.length) occluded.push({ el: desc(e), hits: bad.slice(0, 4) });
  }

  return { vw, vh, line, regionInner, g1,
    offsets: [...offsets.entries()].map(([off, els]) => ({ off, n: els.length, els: [...new Set(els)].slice(0, 4) })).sort((a, b) => a.off - b.off),
    rowsOffLine: rows,
    rowCount: [...groupMin.keys()].length,
    badChain: [...badChain.entries()], controls, nonuniform, twinGrids, emptyTracks, biggestVoid: biggest, occluded };
})()`;

/* ---------- drivers ---------- */
const clickIncl = (sel, t) => ev(`(() => { const b = [...document.querySelectorAll(${JSON.stringify(sel)})].find(x => x.textContent.includes(${JSON.stringify(t)})); if (!b) return "MISS"; b.click(); return "OK"; })()`);
const clickText = (sel, t) => ev(`(() => { const b = [...document.querySelectorAll(${JSON.stringify(sel)})].find(x => x.textContent.trim() === ${JSON.stringify(t)}); if (!b) return "MISS"; b.click(); return "OK"; })()`);
const toMonitor = async () => { const r = await clickIncl(".nav-tab", "لوحة"); await sleep(1100); return r; };
/* ⛔ DASH-NAV — the head row replaced the bare mode toggle at .workspace's top level
 * (the رصد/إحصاءات switch moved INTO the page beside it), and the drivers follow the DOM:
 * both segments are addressed inside .dash-head-row, by text — «رصد»/«إحصاءات» never
 * collide with «بحث كيان»/«بحث منطقة»/«تفصيلي». */
/* ⛔ DASH-7 — THE DRIVERS FOLLOW THE CONSOLE, because the console is now the only navigation
 * this tab has. The surface switcher lives on `.mon-surf` (three buttons), the رصد/إحصاءات
 * switch leads the same command line, and the two destinations that are NOT switcher buttons
 * have their own doors: 📁 (`.mon-case`) for the case file and the status line's domain counts
 * for `تفصيلي`. Every one of them is addressed by the control the analyst actually uses — a
 * driver that reaches a surface by a route no analyst has is a gate testing a private door. */
const openDomainDetail = async () => { const r = await ev(`(() => { const b = document.querySelector(".mon-go, .ms-go"); if (!b) return "MISS"; b.click(); return "OK"; })()`); await sleep(1600); return r; };
const setSurface = async (l) => { const r = await clickText(".mon-surf button", l); await sleep(1400); return r; };
const setMode = async (l) => { const r = await clickIncl(".mon-cmd button", l); await sleep(1400); return r; };
const typeQ = async (q) => { const r = await ev(`(() => { const i = document.querySelector('.mon-q-in'); if (!i) return "MISS";
  const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; s.call(i, ${JSON.stringify(q)});
  i.dispatchEvent(new Event('input',{bubbles:true})); return "OK"; })()`); await sleep(2800); return r; };

const BASE = [
  { k: "mon/rest", d: async () => { await toMonitor(); return setSurface("المراقبة"); } },
  { k: "mon/query", d: async () => { await toMonitor(); await setSurface("المراقبة"); return typeQ("a"); } },
  { k: "mon/card", d: async () => { await toMonitor(); await setSurface("المراقبة"); await typeQ("a");
      const r = await ev(`(() => { const c = document.querySelector('.result-card'); if (!c) return "MISS"; c.click(); return "OK"; })()`); await sleep(2200); return r; } },
  { k: "ent/rest", d: async () => { await toMonitor(); return setSurface("الكيان"); } },
  /* ⛔ DASH-7 — AN ENTITY SURFACE WITH AN ENTITY ON IT. `ent/rest` shows the empty state, and an
   * empty surface exercises almost none of it: the floating live payload, the claims at 78ch and
   * the lane stack all appear only once an alert has been opened. The panel that covered half
   * this surface (aside.home-panel.entity, 340×483 at z-1250) passed every gate because no state
   * drove it. A surface the gate visits only while empty is a surface with no gate. */
  { k: "ent/open", d: async () => { await toMonitor(); await setSurface("المراقبة"); await sleep(1200);
      const r1 = await ev(`(() => { const h = document.querySelector('.entity-row .er-head'); if (!h) return "MISS"; h.click(); return "OK"; })()`);
      if (r1 !== "OK") return r1; await sleep(900);
      const r = await ev(`(() => { const h = document.querySelector('.entity-row.open .er-head'); if (!h) return "MISS"; h.click(); return "OK"; })()`);
      await sleep(2600); return r; } },
  { k: "case/rest", d: async () => { await toMonitor();
      const r = await ev(`(() => { const b = document.querySelector('.mon-case'); if (!b) return "MISS"; b.click(); return "OK"; })()`); await sleep(1800); return r; } },
  { k: "stats/rest", d: async () => { await toMonitor(); const r = await setMode("إحصاءات"); await sleep(3400); return r; } },
];
const EXTRA = [];
/* ⛔ DASH-4b — THE MAP SURFACE JOINS THE BASE SET, not EXTRA. A surface the gate never visits
 * is a surface with no gate, and that is exactly how four leaked rules reached the three
 * domain tabs (see scripts/verify-scope.mjs). This one releases the map to full bleed and
 * collapses `.workspace`, so it changes MORE geometry than any other state here — it belongs
 * in every run, not only under --full.
 * ⚠ It is appended AFTER stats/rest deliberately: the surfaces run in order against one page
 * per (width, state), and putting a state that hides the workspace mid-list would leave the
 * following states measuring a page they did not set up. */
BASE.push({ k: "map/rest", d: async () => { await toMonitor(); return setSurface("الخريطة"); } });
const STATES = FULL ? [...BASE, ...EXTRA] : BASE;
const WIDTHS = FULL ? [[1440, 900], [1900, 950], [1100, 900]] : [[1440, 900], [1900, 950]];

const runs = [];
for (const [w, h] of WIDTHS) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false }, S);
  for (const st of STATES) {
    await send("Page.navigate", { url: URL }, S);
    await sleep(3400);
    let drove; try { drove = await st.d(); } catch (e) { drove = "THREW:" + e.message; }
    const m = await ev(MEASURE.replace("__LADDER__", JSON.stringify(LADDER)));
    if (!m || m.error) { fail.push(`could not measure ${w}x${h} ${st.k} — ${m ? m.error : "no result"} (fail closed)`); continue; }
    if (drove === "MISS") fail.push(`could not REACH ${w}x${h} ${st.k} — driver missed its target (fail closed, not skipped)`);
    runs.push({ w, h, k: st.k, m });
  }
}
if (!runs.length) { fail.push("no surface could be measured at all (fail closed)"); finish(); }

/* ================= the verdicts ================= */
{ /* G1 */
  const bad = [];
  for (const r of runs) {
    const g = r.m.g1;
    if (g.seatStacked) continue;                       /* stacked: the seat is symmetric by its own pair */
    /* ⛔ DASH-7 — THE TOKEN JOINS THE COMPARISON, and this is a strengthening the console forced
     * rather than a workaround. G1 compared three MEASURED insets to each other, which only
     * detects a drift while at least two of them are on screen. The monitor now hides the map
     * and the floating panel on its console surfaces, so one value was left comparing against
     * itself and `--detonate` correctly reported that G1 "cannot fail". The page inset is not a
     * consensus among whatever happens to be visible — it is `--page-inset`, and every edge that
     * IS rendered must equal it. */
    const set = [g.seatLeft, g.regionPadInlineStart, g.panelInset].filter((v) => v != null);
    if (g.pageInsetToken != null) set.push(g.pageInsetToken);
    if (new Set(set.map((v) => R(v))).size > 1) bad.push(`${r.w} ${r.k}: seat.left ${g.seatLeft} · region ${g.regionPadInlineStart} · panel ${g.panelInset} · token ${g.pageInsetToken}`);
  }
  check(bad.length === 0, "G1 the page's edge inset is ONE value on every edge and every surface",
    bad.length ? bad.slice(0, 4).join(" | ") : `all ${runs.length} runs agree`);

  /* G1b — the nav clearance is derived from --nav-top + --nav-h + --nav-gap, so all three must still
   * describe the rendered bar. A hardcoded height is only honest while something checks it. */
  const drift = [];
  for (const r of runs) {
    const g = r.m.g1;
    if (g.navH != null && g.navHToken != null && Math.abs(g.navH - g.navHToken) > 0.6)
      drift.push(`${r.w} ${r.k}: --nav-h says ${g.navHToken} but the bar renders ${g.navH}`);
    for (const [who, v] of Object.entries(g.navGaps)) {
      if (v == null) continue;
      if (Math.abs(v - g.navGapToken) > 0.6) drift.push(`${r.w} ${r.k}: ${who} clears the bar by ${v}, --nav-gap says ${g.navGapToken}`);
    }
  }
  check(drift.length === 0, "G1b the nav clearance's three terms still describe the rendered bar",
    drift.length ? drift.slice(0, 4).join(" | ") : `bar height and every clearance gap match their tokens across ${runs.length} runs`);
}
{ /* G2 */
  const bad = runs.filter((r) => r.m.rowsOffLine.length);
  check(bad.length === 0, "G2 every element starts on its own container's content line",
    bad.length ? bad.slice(0, 3).map((r) => `${r.w} ${r.k}: ${r.m.rowsOffLine.slice(0, 3).map((x) => x.el + " in " + x.parent + " off " + x.off).join(", ")}`).join(" | ")
      : `${runs.reduce((a, r) => a + r.m.rowCount, 0)} containers, every child on its line`);
}
{ /* G3 */
  const offenders = new Map();
  for (const r of runs) {
    for (const [k, n] of r.m.badChain) offenders.set(k, (offenders.get(k) || 0) + n);
    report.push(`G3 ${String(r.w).padStart(4)} ${r.k.padEnd(13)} offsets [${r.m.offsets.map((o) => o.off + "×" + o.n).join(" ")}]`);
  }
  check(offenders.size === 0, `G3 every inline-start PADDING in every ancestor chain is a ladder value {${LADDER.join(",")}}`,
    offenders.size ? [...offenders.entries()].slice(0, 6).map(([k, n]) => `${k} (${n} elements affected)`).join(" · ")
      : "every chain is built from the ladder");
  /* ⛔ DASH-IDLE AMENDMENT — borders LEFT the chain check entirely, and the reason is a
   * measurement, not convenience. Until this item, no gated surface ever rendered the
   * sheet's inline-start ACCENT RULES (`.result-card.sev-*` / `.sl-row` stripes at 5/5/5/2px,
   * frozen by RANK-NAME because the WIDTH is the non-colour channel; `.rc-caveat`'s 2px
   * marker). Mounting CountryEvents into the workspace put them in front of the gate for
   * the first time and it condemned all of them as "off-ladder spacing" — which they are
   * not: each width is its own documented design decision (the severity silhouettes must
   * stay distinguishable in greyscale). A mark is governed by its own gate; only PADDING
   * is rhythm. ⚠ Narrowing a check can only ever produce fewer failures — the detonation
   * shot below still injects a padding violation, which is what this gate now polices. */
  /* ⚠ the ≤6-distinct-offsets half is REPORTED, never enforced — the owner's amendment: breaking a
   * component to reach an offset count is not a fix. */
  const many = runs.filter((r) => r.m.offsets.filter((o) => o.off > 0.6).length > 6);
  if (many.length) diag.push(`G3b more than 6 distinct near-line offsets on: ${many.map((r) => r.w + " " + r.k + " (" + r.m.offsets.filter((o) => o.off > 0.6).length + ")").join(", ")} — reported, not enforced`);
}
{ /* G4 */
  const bad = runs.filter((r) => r.m.controls.length);
  check(bad.length === 0, "G4 every control-role element is within --field-max",
    bad.length ? bad.slice(0, 3).map((r) => `${r.w} ${r.k}: ${r.m.controls.slice(0, 3).map((c) => c.el + " " + c.w + ">" + c.cap).join(", ")}`).join(" | ")
      : "no control exceeds the cap");
}
{ /* G5 */
  const bad = runs.filter((r) => r.m.nonuniform.length || r.m.twinGrids);
  check(bad.length === 0, "G5 same-role siblings share a width, and twin grids produce twin tiles",
    bad.length ? bad.slice(0, 3).map((r) => `${r.w} ${r.k}: ` + [...r.m.nonuniform.map((n) => n.sig + " spread " + n.spread),
      r.m.twinGrids ? "tile widths " + r.m.twinGrids.widths.join("/") : null].filter(Boolean).join(", ")).join(" | ")
      : "every same-role group is uniform");
}
{ /* G6 */
  const bad = runs.filter((r) => r.m.emptyTracks.length);
  check(bad.length === 0, "G6 no grid reserves a track it has no child for",
    bad.length ? bad.slice(0, 3).map((r) => `${r.w} ${r.k}: ${r.m.emptyTracks.slice(0, 2).map((t) => t.el + " " + t.kids + " kids in " + t.tracks + " tracks").join(", ")}`).join(" | ")
      : "no reserved-empty track");
}
{ /* G7 — ⛔ DIAGNOSTIC ONLY, BY THE OWNER'S DECISION, and the reason is worth keeping: the largest
   * void measured in UI-AUDIT (552,500px² on entity/rest) is an HONEST EMPTY STATE — no query has
   * been typed, so there is nothing to show. A ceiling on emptiness pushes toward inventing filler,
   * which is the opposite of what this surface owes the analyst. Measured, never blocking. */
  const worst = runs.reduce((a, r) => (r.m.biggestVoid > a.v ? { v: r.m.biggestVoid, k: r.k, w: r.w } : a), { v: 0 });
  diag.push(`G7 largest empty rectangle (min side 40px): ${worst.v.toLocaleString()}px² on ${worst.w} ${worst.k} — DIAGNOSTIC, never blocking`);
}
{ /* G9 */
  const bad = runs.filter((r) => r.m.occluded.length);
  check(bad.length === 0, "G9 no element's own ink is covered by a foreign element",
    bad.length ? bad.slice(0, 4).map((r) => `${r.w} ${r.k}: ${r.m.occluded.slice(0, 3).map((o) => o.el + "→" + o.hits[0]).join(", ")}`).join(" | ")
      : `${runs.length} runs, zero occluded glyphs`);
}

/* ================= DETONATION ================= */
if (DETONATE) {
  console.log("\n=== detonation: every gate must FAIL on an injected violation ===");
  const shots = [
    /* ⚠ THE PREDICATE IS THE CHECK'S TWIN AND MUST STAY ITS TWIN. It was a second copy of G1's
     * rule that did not learn about `--page-inset` when the check did, so the shot landed and
     * the predicate could not see it — a gate reporting "cannot fail" because its detonator
     * disagreed with it, which is the two-halves-disagreeing failure this file already records
     * once for G3. */
    ["G1", `.workspace { padding-right: 33px !important }`, (m) => new Set([m.g1.seatLeft, m.g1.regionPadInlineStart, m.g1.panelInset, m.g1.pageInsetToken].filter((v) => v != null).map(R)).size > 1],
    ["G2", `.workspace > * { margin-right: 7px !important }`, (m) => m.rowsOffLine.length > 0],
    /* ⛔ THIS PREDICATE WENT STALE THE MOMENT G3 WAS REDEFINED, and it reported "the surface already
     * violates it" while the verdict said green — the two halves of one check disagreeing, which is the
     * exact failure this file is built to prevent. It tested the old sum-based rule against `m.offsets`,
     * where fractional text offsets live, so it was permanently true. It now tests what G3 tests. */
    ["G3", `.workspace .search-row { padding-right: 7px !important }`, (m) => m.badChain.length > 0],
    ["G4", `.workspace .search-row input { max-width: none !important }`, (m) => m.controls.length > 0],
    ["G5", `.stat-grid > *:first-child { width: 77px !important }`, (m) => m.nonuniform.length > 0 || !!m.twinGrids],
    ["G6", `.stat-grid { grid-template-columns: repeat(9, 1fr) !important }`, (m) => m.emptyTracks.length > 0],
    /* ⛔ THE OVERLAY MUST NOT BE THE INK'S OWN ANCESTOR. This injected `.workspace::after`, and
     * `elementFromPoint` reports a pseudo-element as its ORIGINATING element — so every hit came
     * back as `.workspace`, which `hit.contains(e)` then skipped for every descendant. It only
     * ever "caught" anything because floating panels outside `.workspace` had ink; the console
     * hides those on its own surfaces, so the shot stopped landing. `.mon-stat` is a SIBLING of
     * the stage's text: it can genuinely cover it, which is what the gate must detect. */
    ["G9", `.mon-stat { position: fixed !important; inset: 0 !important; z-index: 9999 !important; background: red !important }`, (m) => m.occluded.length > 0],
  ];
  for (const [name, css, detects] of shots) {
    await send("Page.navigate", { url: URL }, S);
    await sleep(3400);
    /* ⛔ DASH-7 — THE DETONATOR REACHES تفصيلي THE WAY AN ANALYST DOES. It called
     * setMode("تفصيلي") — a mode row that no longer exists — so it landed on المراقبة, where
     * .search-row, .stat-grid and .side are absent and every injected rule matched nothing.
     * Six gates reported "cannot fail" for that reason alone: not blinded gates, a driver
     * pointed at the wrong surface. The door is the status line's domain count. */
    await toMonitor(); await openDomainDetail(); await sleep(2600);
    const clean = await ev(MEASURE.replace("__LADDER__", JSON.stringify(LADDER)));
    await ev(`(() => { const s = document.createElement("style"); s.id = "detonate"; s.textContent = ${JSON.stringify(css)}; document.head.appendChild(s); return "ok"; })()`);
    await sleep(600);
    const shot = await ev(MEASURE.replace("__LADDER__", JSON.stringify(LADDER)));
    const before = detects(clean), after = detects(shot);
    check(!before && after, `DETONATION ${name} fails on an injected violation`,
      before ? "the surface already violates it, so the shot proves nothing" : after ? "caught" : "⛔ NOT caught — the gate cannot fail");
    await ev(`document.getElementById("detonate")?.remove()`);
  }
  /* G8 is static: detonate against a synthetic sheet rather than the real one */
  {
    const before = fail.filter((f) => f.startsWith("G8")).length;
    const probeSel = ".workspace { padding: 12px }";     /* 12px === --sp-3 */
    const hit = /(?<![\w-])12px/.test(probeSel) && Object.values(tokenVal).includes(12);
    check(hit, "DETONATION G8 recognises a literal that duplicates a token", hit ? "caught (12px === --sp-3)" : "⛔ NOT caught");
    void before;
  }

  /* G8c is static too: run the SAME scanner over a synthetic sheet. Two shots — the unmarked
   * lie (--probe declares 10px; its own comment claims a 16px cap, naming the token) MUST be
   * flagged, and the marked one ("was 16px") MUST pass — proving the exemption works and the
   * gate is not merely always-red. ⛔ A third probe proves the name requirement: prose about
   * someone ELSE's number beside an unnamed token is not this gate's subject. */
  {
    const bad = `/* --probe: the cap was 10px once, now a 17px cap */\n--probe: 10px;`;
    const marked = `/* --probe2: the cap was 16px once */\n--probe2: 10px;`;
    const otherNum = `/* renders 99px tall beside nothing in particular */\n.proule { width: 1px }`;
    const hitBad = scanTokenComments(bad).length > 0;
    const cleanMarked = scanTokenComments(marked).length === 0;
    const ignoresOther = scanTokenComments(otherNum).length === 0;
    check(hitBad, "DETONATION G8c flags a token comment that names a different value", hitBad ? "caught (17px ≠ 10px, unmarked)" : "⛔ NOT caught");
    check(cleanMarked, "DETONATION G8c passes a history-marked value", cleanMarked ? "was-marked passes" : "⛔ marker rejected — marking syntax broken");
    check(ignoresOther, "DETONATION G8c ignores comments that do not name their token", ignoresOther ? "unnamed prose skipped" : "⛔ false positive on non-token prose");
  }
}

finish();

function finish() {
  console.log("=== G1–G9 layout-system gate ===");
  for (const p of pass) console.log("  ✅ " + p);
  for (const d of diag) console.log("  ℹ  " + d);
  if (process.env.VERBOSE) for (const r of report) console.log("     " + r);
  if (fail.length) { console.log("=== FAILED ==="); for (const f of fail) console.log("  ⛔ " + f); }
  console.log(`\n${pass.length} passed, ${fail.length} failed${diag.length ? `, ${diag.length} diagnostic` : ""}`);
  try { ws?.close(); } catch {}
  try { chrome?.kill(); } catch {}
  process.exit(fail.length ? 1 : 0);
}
