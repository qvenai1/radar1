/**
 * UI-3 — THE CONTRACT GATE: the seven coverage-bearing payloads against their declared types,
 * IN BOTH DIRECTIONS.
 *
 * ⛔ WHY BOTH. The 2a method, applied: a type that is merely a SUBSET of the wire is a type that
 * silently drops fields — and for these contracts the dropped field is `coverage`, i.e. the holes.
 * A type that is a SUPERSET is a type describing a response nobody sends, which is how a consumer
 * ends up branching on a key that never arrives. So this fails on either:
 *   · a key the API returns that the type does not declare   → the type is behind the backend
 *   · a key the type declares that the API does not return   → the type is fiction
 *
 * ⛔ AND IT READS BOTH SIDES RATHER THAN CARRYING EITHER. The interfaces are parsed out of
 * src/api/types.ts and the payloads are fetched from the running server, so neither is hard-coded
 * here. A gate holding its own copy of the shape keeps passing after the shape moves — the lesson
 * DATAVIZ-1's uncommitted "six-checks script" and UI-1's hard-coded surface both taught.
 *
 * ⚠ FRESHNESS IS CHECKED, NOT ASSUMED: the server has been stale five times in this project's
 * history. Before comparing anything, this asserts the running API serves a field that only the
 * current backend has (`observed_fraction_basis`, added by UI-0). A stale server would otherwise
 * "prove" the types wrong about a backend it is not running.
 *
 *   node scripts/verify-contracts.mjs            (needs the API on 127.0.0.1:8000)
 */

import { readFileSync } from "node:fs";
import path from "node:path";

const API = process.env.API_BASE || "http://127.0.0.1:8000/api/v1";
const TYPES = readFileSync(
  path.join(import.meta.dirname, "..", "src", "api", "types.ts"), "utf8",
).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

/** Field names declared directly on `interface Name { … }` (nesting is followed via `shape`). */
function iface(name) {
  const at = TYPES.search(new RegExp(`\\binterface\\s+${name}\\s*\\{`));
  if (at < 0) throw new Error(`interface not declared in types.ts: ${name}`);
  const open = TYPES.indexOf("{", at);
  let depth = 0, end = open;
  for (let i = open; i < TYPES.length; i++) {
    if (TYPES[i] === "{") depth++;
    else if (TYPES[i] === "}") { depth--; if (depth === 0) { end = i; break; } }
  }
  const body = TYPES.slice(open + 1, end);
  const out = new Map();
  // top level only: skip anything nested inside a braced value
  let d = 0;
  for (const line of body.split("\n")) {
    const m = /^\s*([A-Za-z_][\w]*)(\??)\s*:\s*(.+?);?\s*$/.exec(line);
    if (d === 0 && m) out.set(m[1], { optional: m[2] === "?", type: m[3].trim() });
    d += (line.match(/\{/g) || []).length - (line.match(/\}/g) || []).length;
  }
  return out;
}

/** A representative object for a nested list/obj field, so children are compared too. */
const CHILD = (v) => (Array.isArray(v) ? v[0] : v);

const CONTRACTS = [
  { route: "/coverage", type: "CoverageResponse",
    shape: { streams: "SeriesCoverage" } },
  { route: "/maritime/series/ais-gaps", type: "MaritimeSeries",
    shape: { buckets: "SeriesBucket", coverage: "SeriesCoverage" } },
  { route: "/maritime/series/multi-pattern", type: "MaritimeSeries",
    shape: { buckets: "SeriesBucket", coverage: "SeriesCoverage" } },
  { route: "/maritime/series/rendezvous-points", type: "RendezvousPointsResponse",
    shape: { points: "RendezvousPoint", coverage: "SeriesCoverage" } },
  { route: "/maritime/flags/multi-pattern", type: "FlagDistributionResponse",
    shape: { slices: "FlagSlice", coverage: "SeriesCoverage" } },
  { route: "/air/series/country?hours=6", type: "AirCountrySeries",
    shape: { buckets: "CountryBucket", coverage: "SeriesCoverage" } },
  { route: "/air/series/country-daily", type: "AirCountryDaily",
    shape: { buckets: "HullDayBucket", coverage: "SeriesCoverage" } },
];

const fail = [];
let checks = 0;

/* ---------- freshness, before anything is concluded from the wire ---------- */
{
  const r = await fetch(`${API}/coverage?stream=hull_multi_pattern`);
  if (!r.ok) { console.error(`⛔ API unreachable: ${API} → HTTP ${r.status}`); process.exit(1); }
  const d = await r.json();
  const s = d.streams?.[0];
  const fresh = !!s && s.stream === "hull_multi_pattern" && "observed_fraction_basis" in s;
  console.log(`server freshness: ${fresh ? "CURRENT" : "⛔ STALE"} ` +
              `(hull_multi_pattern + observed_fraction_basis present: ${fresh})`);
  if (!fresh) {
    console.error("⛔ REFUSING TO COMPARE against a server that is not running the current " +
                  "backend — it would report the types wrong about code nobody is serving.");
    process.exit(1);
  }
}

/* ---------- the two-way key comparison ---------- */
function compare(label, declared, live) {
  const dk = new Set(declared.keys());
  const lk = new Set(Object.keys(live ?? {}));
  const missingInType = [...lk].filter((k) => !dk.has(k));
  const missingOnWire = [...dk].filter((k) => !lk.has(k) && !declared.get(k).optional);
  checks++;
  const ok = missingInType.length === 0 && missingOnWire.length === 0;
  console.log(`  ${ok ? "✅" : "⛔"} ${label.padEnd(42)} ${dk.size} declared / ${lk.size} on wire`);
  if (missingInType.length) {
    fail.push(`${label}: API returns keys the type does not declare → ${missingInType.join(", ")}`);
    console.log(`       ⛔ on wire, undeclared: ${missingInType.join(", ")}`);
  }
  if (missingOnWire.length) {
    fail.push(`${label}: type declares keys the API does not return → ${missingOnWire.join(", ")}`);
    console.log(`       ⛔ declared, never sent: ${missingOnWire.join(", ")}`);
  }
}

console.log("\n=== UI-3 contract gate — seven payloads, both directions ===");
for (const c of CONTRACTS) {
  const r = await fetch(API + c.route);
  if (!r.ok) { fail.push(`${c.route}: HTTP ${r.status}`); console.log(`  ⛔ ${c.route} HTTP ${r.status}`); continue; }
  const body = await r.json();
  compare(`${c.route} → ${c.type}`, iface(c.type), body);
  for (const [field, childType] of Object.entries(c.shape ?? {})) {
    const sample = CHILD(body[field]);
    if (sample == null) {
      // ⚠ an empty list or a null coverage proves nothing about the child shape; say so rather
      // than pass silently, which is how an unexercised branch reads as a verified one.
      console.log(`       ⚠ ${field}: no sample in this response — ${childType} not exercised`);
      continue;
    }
    compare(`  ${field}[] → ${childType}`, iface(childType), sample);
  }
}

console.log(`\n${checks - fail.length}/${checks} key-set comparisons clean`);
if (fail.length) { console.error("\n⛔ FAILURES:"); for (const f of fail) console.error("  " + f); process.exit(1); }
console.log("✅ zero undeclared keys on the wire, zero fictional keys in the types");
