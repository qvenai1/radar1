/**
 * caseKey ↔ dedup_key parity anchor — the JS side.
 *
 * ⛔ VECTORS must stay byte-identical to tests/test_case_key_parity.py (same
 * discipline as the orbital anchor). These pin the FALLBACK mirror; the primary
 * path — a server-published `dedup_key` — is drift-free by construction and is
 * asserted to win below.
 */
import { describe, expect, it } from "vitest";
import { caseKey } from "./client";
import type { Alert } from "./types";

// [signal_type, entity id, ts, summary, expected]
const VECTORS: Array<[string, string | null, string | null, string, string]> = [
  ["emergency_squawk", "ae01ce", "2026-09-01T04:00:00+00:00", "squawk 7700",
   "emergency_squawk:ae01ce"],
  ["military_aircraft", "43c701", "2026-09-01T04:00:00+00:00", "RRR4952 presence",
   "military_aircraft:43c701"],
  ["military_satellite", "26407", "2026-08-30T16:43:40+00:00", "pass over Turkey",
   "military_satellite:26407:2026-08-30T16:43:40+00:00"],
  ["maritime_composite", "9235012", "2026-08-29T10:00:00+00:00", "3 patterns",
   "maritime_composite:9235012"],
  ["maritime_single", "371681", "2026-08-28T02:00:00+00:00",
   "rendezvous 3.2h with 477995018",
   "maritime_single:371681:rendezvous 3.2h with 477995018"],
  ["maritime_single", "371681", "2026-08-28T02:00:00+00:00",
   "zone_dwell 5.1h at Leviathan",
   "maritime_single:371681:zone_dwell 5.1h at Leviathan"],
  ["implausible_position", "512000999", "2026-09-01T01:00:00+00:00", "3 fixes on land",
   "implausible_position:512000999"],
  ["future_family", "x1", "2026-09-01T00:00:00+00:00", "whatever",
   "future_family:x1:2026-09-01T00:00:00+00:00"],
];

const alert = (sig: string, id: string | null, ts: string | null, summary: string,
               dedup?: string): Alert =>
  ({ signal_type: sig, entity_ref: { id }, ts, summary,
     ...(dedup ? { dedup_key: dedup } : {}) } as unknown as Alert);

describe("caseKey mirrors notify.identity.dedup_key", () => {
  it("matches every shared vector on the fallback path", () => {
    for (const [sig, id, ts, summary, expected] of VECTORS) {
      expect(caseKey(alert(sig, id, ts, summary)), `${sig} · ${summary}`).toBe(expected);
    }
  });
  it("keeps one ship's two patterns apart (the property the old mirror broke)", () => {
    const a = caseKey(alert("maritime_single", "371681", null, "rendezvous 3.2h with 477995018"));
    const b = caseKey(alert("maritime_single", "371681", null, "zone_dwell 5.1h at Leviathan"));
    expect(a).not.toBe(b);
  });
  it("normalises a null entity id to empty, like Python's str(id or '')", () => {
    expect(caseKey(alert("emergency_squawk", null, null, ""))).toBe("emergency_squawk:");
  });
  it("always prefers the server-published key over the mirror", () => {
    expect(caseKey(alert("maritime_single", "371681", null, "anything", "SERVER:KEY")))
      .toBe("SERVER:KEY");
  });
});
