/**
 * Caveat registry (UI-1 Part A) — the ONE place a backend caveat is paired with its
 * Arabic counterpart, and the drift guard over that pairing.
 *
 * THE FLIP. Every panel used to render `data?.caveat?.trim() || T.*`. The backend
 * populates `caveat` on EVERY path (including available=false), so the English always
 * won and the Arabic paraphrases in strings.ts were dead code. The precedence is now
 * inverted: the Arabic paraphrase is shown, and the response caveat is the fallback
 * only when no Arabic counterpart exists. `caveat` stays in the API contract untouched
 * — it is the methodological text for non-UI consumers, and it stays reachable here.
 *
 * THE DRIFT GUARD (two layers, both cheap, no test runner needed):
 *
 *  1. COMPLETENESS — compile-time. `CAVEATS` is typed `Record<CaveatKey, CaveatEntry>`,
 *     so adding a key to the union without an entry (or misspelling one at a call site)
 *     is a `tsc -b` error. `tsc -b` is one of this repo's only two gates, so the check
 *     runs on every build with no new infrastructure.
 *
 *  2. STALENESS — runtime. Each entry records `en`: the EXACT backend text the Arabic
 *     paraphrases, captured at authoring time. `resolveCaveat` compares the live
 *     response against that baseline; a mismatch means the backend's methodological
 *     text changed and the paraphrase may no longer describe it. That surfaces as
 *     `drifted`, which the Caveat component renders as a visible marker and logs once
 *     in dev — an honest "the Arabic may be stale, read the English" signal rather than
 *     a silent regression.
 *
 * THE AUTHORITATIVE TEXT IS NEVER LOST. `resolveCaveat` always returns the live
 * backend English as `authoritative`, falling back to the recorded baseline when the
 * response carries none. The Caveat component exposes it behind a disclosure, so the
 * methodology is one click away in every place a caveat is shown.
 */

import { T } from "./strings";

/** One key per caveat the UI actually renders. */
export type CaveatKey =
  | "gaps"        // A1  — gap_detect.CANDIDATE_CAVEAT
  | "rendezvous"  // A2  — rendezvous.CANDIDATE_CAVEAT
  | "identity"    // A3  — identity_change.CANDIDATE_CAVEAT
  | "composite"   // CAPSTONE — composite.CANDIDATE_CAVEAT
  | "crossref"    // AIS×MarineTraffic — api.CROSSREF_CAVEAT
  | "dark"        // GFW satellite AIS — api.DARK_CAVEAT
  | "alerts"      // D5 alert feed — alerts.CAVEAT
  | "area";       // D4 area search — api.AREA_CAVEAT

interface CaveatEntry {
  /** Shown by default — the Arabic paraphrase (lives in strings.ts). */
  ar: string;
  /**
   * DRIFT BASELINE: the exact backend English `ar` paraphrases, as of authoring.
   * Also the fallback `authoritative` text when a response carries no caveat.
   * If the backend text changes, this no longer matches and `drifted` goes true.
   */
  en: string;
}

/**
 * The mapping. Arabic comes from strings.ts (single string home); the English
 * baselines are transcribed from the backend constants named in each comment.
 */
export const CAVEATS: Record<CaveatKey, CaveatEntry> = {
  gaps: {
    ar: T.vessel.gapsCaveat,
    // backend/gap_detect.py CANDIDATE_CAVEAT
    en: "Investigation lead, not proof: an underway vessel in good AIS coverage stopped reporting for >= the gap threshold, not explained by a receiver/feed outage. Terrestrial AIS is spoofable and coverage is imperfect — intent cannot be proven from a gap alone.",
  },
  rendezvous: {
    ar: T.vessel.rvCaveat,
    // backend/rendezvous.py CANDIDATE_CAVEAT
    en: "Investigation lead, not proof: two low-speed vessels loitered close together, away from port, in open sea. This does NOT prove a transfer occurred — many candidates are legitimate offshore work (supply/drilling) or scheduled traffic (ferries). AIS is spoofable and identity is point-in-time. Ranking prioritises, it does not accuse.",
  },
  identity: {
    ar: T.vessel.idCaveat,
    // backend/identity_change.py CANDIDATE_CAVEAT
    en: "Documented identity change, not proof of evasion: a vessel broadcast a different name/flag/MMSI over time. Renaming, sale, and re-flagging are USUALLY LEGAL and routine. AIS static is spoofable and error-prone. Ranking prioritises leads (grey/sanctions flags, correlation with gaps/rendezvous); it does not accuse.",
  },
  composite: {
    ar: T.vessel.cxCaveat,
    // backend/composite.py CANDIDATE_CAVEAT
    en: "Composite of investigation leads — a STRONGER lead, still NOT proof. Each underlying event (gap, rendezvous, identity change) is already only a lead; a hull exhibiting several has more reasons to investigate, but legitimate explanations exist (a sold/re-flagged vessel that anchored in sparse coverage and did offshore supply work could trip all three innocently). AIS is spoofable. This prioritises for review; it does not accuse.",
  },
  crossref: {
    ar: T.vessel.crossref.caveatFallback,
    // backend/api.py CROSSREF_CAVEAT
    en: "Derived AIS×MarineTraffic links — a LEAD, not proof. MarineTraffic carries no MMSI, so links are inferred from name (anchor) + position-over-time + dimensions; 'weak'/ambiguous rows are unconfirmed, and a dimension 'discrepancy' at one location is a data-quality or possible-spoof signal, not a verdict.",
  },
  dark: {
    ar: T.dark.caveatFallback,
    // backend/api.py DARK_CAVEAT
    en: "Dark-vessel events from GFW SATELLITE AIS — a LEAD, not proof. A gap can be a receiver outage or a legitimate silence; 'intentional' is GFW's own likelihood flag, not a verdict. This fills the blind spot free terrestrial AIS leaves in the Persian Gulf / Red Sea / open ocean. Events whose reported position falls ON LAND are flagged (on_land) as a SUSPECT / spoofed AIS fix — a signal, not a location; a coarse land mask can also flag a berth at the coastal edge, so it is not a verdict.",
  },
  alerts: {
    ar: T.dashHome.caveat,
    // backend/alerts.py CAVEAT
    en: "Alerts are read LIVE over existing signals — no stored alert table, no new detector. Severity is per-family and honest: emergency squawks are HIGH; military presence (aircraft + satellite AOI passes) is INFO — informational, not a threat; maritime composites carry their own rank (a stronger lead, still NOT proof); implausible positions are LOW (a spoofed/jammed vessel is usually a victim). Liveness is MIXED and shown per alert: air/sea feeds can be stale, satellite passes are forward SGP4 predictions, and each maritime composite carries its last-event vintage. AIS and ADS-B are spoofable.",
  },
  area: {
    ar: T.dashboard.areaCaveat,
    // backend/api.py AREA_CAVEAT
    en: "Entities whose track crossed the box in the window. Mixed liveness: air is live, sea is frozen (last real AIS fix — see the freshness strip). The space list is an SGP4 sampling — a brief crossing of a very small box between samples can be missed. Each row is a discovery hit; open it for the full entity detail.",
  },
};

/**
 * SECOND REGISTRY (UI-1 Part B) — the per-signal `severity_basis`.
 *
 * These are the four bases that are 100% CONSTANT per signal type (backend
 * alerts.py SEVERITY_BASIS), so they hoist WHOLE to a group header. Same {ar, en}
 * shape and the same drift comparison as CAVEATS: if the backend basis changes, the
 * Arabic is no longer trusted and the live English is shown instead.
 *
 * NOT included: `maritime_composite`, whose basis is composed per hull
 * (alerts.py _maritime_basis) and is therefore only PARTLY hoistable — see
 * MARITIME_LEAD_CLAUSE.
 */
export type SignalNoteKey =
  | "emergency_squawk"
  | "military_aircraft"
  | "military_satellite"
  | "implausible_position";

/* ⚠ RANK-NAME touches the `ar` side ONLY, and that boundary is load-bearing: `en` is the
 * recorded baseline that `resolve()` compares against the backend's LIVE `severity_basis` to
 * detect drift. Editing `en` here would either mask a real drift or invent one, and the
 * backend's English basis has not changed. So every `en` below is byte-identical to before.
 *
 * ⛔ `military_aircraft.ar` had to be REWRITTEN, not token-swapped: it read «معلوماتي، لا
 * تهديد», and with `info` now labelled «لا تهديد» the naive swap says «لا تهديد، لا تهديد». */
export const SIGNAL_NOTES: Record<SignalNoteKey, CaveatEntry> = {
  emergency_squawk: {
    ar: "إشارة طوارئ نشِطة — «مرتفع» بحكم التعريف.",
    en: "active emergency squawk — high by definition",
  },
  military_aircraft: {
    ar: "وجود طائرة عسكرية — «لا تهديد»: معلومة لا حكم خطر.",
    en: "military aircraft presence — informational, not a threat",
  },
  military_satellite: {
    ar: "مرور مداري لقمر عسكري فوق النطاق — تنبؤ SGP4 مستقبلي؛ الوسم الاستراتيجي مجمّد عند 2023.",
    en: "military satellite AOI pass — forward SGP4 prediction; strategic tag frozen 2023",
  },
  implausible_position: {
    ar: "شذوذ في رصد موضع AIS — السفينة غالباً ضحية (خداع/تشويش/خطأ مستقبِل) لا فاعلاً.",
    en: "implausible AIS position — usually a victim (spoof / jam / receiver fault), not an actor",
  },
};

/**
 * The ONE clause present on every maritime composite basis (measured 66/66 in UI-pre).
 * Only this hoists. The `composite of {N} pattern type(s)` count, the
 * `; involves a grey/sanctions flag` clause (43/66 — hoisting it would falsely assert a
 * grey flag on the other 23 hulls) and `; spine: …` are row-varying and stay per card.
 */
export const MARITIME_LEAD_CLAUSE = "— a stronger lead, still not proof";

export interface ResolvedCaveat {
  /** What to show: the Arabic paraphrase (or the live English if none exists). */
  text: string;
  /** The authoritative backend methodology — live response, else the recorded baseline. */
  authoritative: string;
  /** True when the live text no longer matches the paraphrased baseline. */
  drifted: boolean;
}

/**
 * Warn at most ONCE per key. Deliberately not dev-gated: a drifted paraphrase is a
 * real honesty signal wherever it happens, and one log per caveat per session is
 * cheap. (`import.meta.env` is unavailable here — the app tsconfig carries no
 * `vite/client` types, and widening it is outside this phase.)
 */
const warned = new Set<string>();

const norm = (s: string) => s.replace(/\s+/g, " ").trim();

/** The shared resolve: Arabic first, live English authoritative, drift compared. */
function resolve(id: string, entry: CaveatEntry, live0?: string | null): ResolvedCaveat {
  const live = norm(live0 ?? "");
  const baseline = norm(entry.en);
  const drifted = live !== "" && live !== baseline;
  if (drifted && !warned.has(id)) {
    warned.add(id);
    console.warn(
      `[caveats] drift on "${id}": the backend text changed, so the Arabic paraphrase ` +
        `may be stale. Update its (ar + en) pair in lib/caveats.ts.\n` +
        `baseline: ${baseline}\nlive:     ${live}`,
    );
  }
  return {
    // the flip: Arabic first — but a DRIFTED paraphrase is not trusted, so the live
    // English wins until the pair is updated. Honesty beats localisation.
    text: drifted ? live : entry.ar.trim() || live || baseline,
    authoritative: live || baseline,
    drifted,
  };
}

/**
 * Resolve a caveat for display. `response` is the endpoint's `caveat` field (which
 * the API always populates) — kept as the authoritative text, no longer the display
 * text. Normalised on whitespace so reflowed backend literals don't read as drift.
 */
export function resolveCaveat(key: CaveatKey, response?: string | null): ResolvedCaveat {
  return resolve(`caveat:${key}`, CAVEATS[key], response);
}

/**
 * Resolve a per-signal `severity_basis` (Part B's hoist). Returns null for a signal
 * type with no constant basis — today only `maritime_composite`, which is composed
 * per hull and must not be hoisted whole.
 */
export function resolveSignalNote(
  signalType: string,
  liveNote?: string | null,
): ResolvedCaveat | null {
  const entry = SIGNAL_NOTES[signalType as SignalNoteKey];
  if (!entry) return null;
  return resolve(`signal:${signalType}`, entry, liveNote);
}
