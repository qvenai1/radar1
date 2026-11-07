/**
 * Shared alert-card machinery (DASH-2e).
 *
 * These helpers were DashboardHome's private internals. 2e replaces that component with
 * `CountryEvents`, so they move here rather than being duplicated or lost — 2f's entity
 * panel and 2g's inventory board consume the same rules.
 *
 * ⚠ Every rule here is load-bearing and was argued for elsewhere; this file is a MOVE,
 * not a rewrite. In particular the hoist degrades to NO hoist rather than to a wrong one,
 * so a backend text change costs a repeated line and never a dropped caveat.
 */

import type { Alert, AlertSort } from "../api/types";
import { MARITIME_LEAD_CLAUSE, resolveSignalNote } from "./caveats";
import { isoAge } from "./format";
import { T } from "./strings";
import type { CardBadge } from "../components/ResultCards";

/**
 * How many alerts a view shows before "show all".
 *
 * 15 = three full rows of the card grid at the 1440×900 reference viewport. ⚠ That
 * reasoning was measured for the FULL-WIDTH workspace (5 columns). In the home's 340px
 * floating panel the grid is one column, so 15 is a length cap here rather than a
 * row-alignment argument — which is exactly why it still matters (DASH-2d″'s length gate).
 */
export const DEFAULT_CAP = 15;

/** the backend's own ordering (alerts.SEVERITY_ORDER): INFO sinks below LOW. */
const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };

/** alerts.IMPORTANCE_UNKNOWN — an unrecognised family sorts last, never first. */
const IMPORTANCE_UNKNOWN = 9;

/**
 * The rank of one alert on the ACTIVE axis. Lower sorts first on both.
 *
 * ⚠ Two axes, never mixed into one score. This returns a POSITION, and callers use it for
 * nothing but `Array.sort`. `importance` may not be coloured by, summed, or read as risk
 * (backend/models.py, alerts.IMPORTANCE_ORDER).
 */
export function rankOn(axis: AlertSort, a: Alert): number {
  if (axis === "importance") {
    return Number.isFinite(a.importance) ? a.importance : IMPORTANCE_UNKNOWN;
  }
  return SEVERITY_ORDER[a.severity] ?? 9;
}

/** a stable, unique key per alert (family + signal + entity) */
export function alertKey(a: Alert): string {
  return `${a.family}:${a.signal_type}:${a.entity_ref.id}`;
}

/**
 * The legend's rows, DERIVED from `SEVERITY_ORDER` rather than listed again.
 *
 * ⛔ The order is the teaching: `info` comes last because that is exactly where it sorts —
 * below `low`, deliberately, so a low-but-real anomaly outranks informational presence
 * (backend alerts.SEVERITY_ORDER). A hand-written array here could drift from the sort and
 * would then teach the wrong thing with total confidence.
 */
export const SEV_LEGEND_ORDER: string[] = Object.entries(SEVERITY_ORDER)
  .sort((a, b) => a[1] - b[1])
  .map(([k]) => k);

/** severity → badge tone. `low` has no tone on purpose: it is the quiet one. */
export const SEV_TONE: Record<string, CardBadge["tone"]> = {
  high: "high",
  medium: "warn",
  low: undefined,
  info: "info",
};

export function alertFoot(a: Alert): string {
  const age = a.vintage ? T.dashHome.vintageAgo(isoAge(a.vintage)) : "";
  // a satellite pass is a forward SGP4 prediction (not a live feed) — say so
  if (a.signal_type === "military_satellite") {
    return [T.dashHome.predicted, age].filter(Boolean).join(" · ");
  }
  if (a.live) return [T.dashHome.liveTag, age].filter(Boolean).join(" · ");
  return age || T.dashHome.frozen;
}

/* ---------------- the hoist ---------------- */

export interface Hoist {
  /** printed once on the group header, or null when nothing could be hoisted */
  header: string | null;
  /** the authoritative English behind the header note (kept reachable) */
  headerSource: string | null;
  /** what remains on each card — null means the whole basis hoisted */
  perCard: (basis: string) => string | null;
}

/** tidy a basis after a clause was lifted out of it */
function tidy(s: string): string {
  return s
    .replace(/\s*;\s*/g, "; ")
    .replace(/\s{2,}/g, " ")
    .replace(/^[\s;—-]+|[\s;]+$/g, "")
    .trim();
}

/**
 * Decide what hoists for one signal-type group. Every branch degrades to NO hoist rather
 * than to a wrong one: if the backend text stops matching what we expect, the per-card
 * notes are left exactly as they came.
 */
export function planHoist(signalType: string, bases: string[]): Hoist {
  const none: Hoist = { header: null, headerSource: null, perCard: (b) => b || null };
  if (bases.length === 0) return none;

  // (a) signal types whose basis is a per-family CONSTANT → hoist the whole thing, but
  //     only if it really is identical across every card in the group.
  const constant = resolveSignalNote(signalType, bases[0]);
  if (constant) {
    const allSame = bases.every((b) => b === bases[0]);
    if (!allSame) return none;
    return { header: constant.text, headerSource: constant.authoritative, perCard: () => null };
  }

  // (b) maritime composites — only the universal clause hoists. The pattern count, the
  //     grey/sanctions clause (a MINORITY of hulls) and the spine clause are row facts.
  if (bases.every((b) => b.includes(MARITIME_LEAD_CLAUSE))) {
    return {
      header: T.dashHome.hoistedLead,
      headerSource: MARITIME_LEAD_CLAUSE.replace(/^—\s*/, ""),
      perCard: (b) => tidy(b.split(MARITIME_LEAD_CLAUSE).join(" ")) || null,
    };
  }

  return none;
}

/* ---------------- AOI countries ---------------- */

/** The seven AOI countries, in the order the picker offers them. */
export const AOI_COUNTRIES = ["SY", "TR", "IQ", "JO", "LB", "IL", "CY"] as const;

/**
 * The Unicode regional-indicator flag for an ISO alpha-2 code — derived, never a file.
 *
 * ⚠ Measured in THEME-1: these codepoints are absent from Qomra and fall through to the
 * system emoji face with no tofu and no zero-width glyph. So this costs no font asset and
 * no licence, and it is the reason the flag is derived rather than shipped as an image.
 *
 * ⛔ SY RETURNS THE EMPTY STRING, AND THAT IS THE POLICY, NOT A BUG (FLAG-1). Syria
 * readopted the green-white-black three-star flag in 2025, and which of the two flags 🇸🇾
 * paints is decided by the VIEWER'S emoji font vintage — measured: Noto Color Emoji 2.048
 * is current, anything older paints the superseded red-white-black two-star flag. The
 * derivation cannot feature-detect that, so in a STRING context, where no SVG can be
 * returned, it declines to guess. The mandatory words «علم التسجيل: …» then carry the
 * registry alone — which is the same fallback this file already documents for the no-ISO
 * case below, and the part that was never allowed to be dropped anyway.
 *
 * ⚠ CALLERS MUST TOLERATE "". Build the line with a filter, never with `${isoFlag(x)} `,
 * or Syria gets a leading space where the glyph would have been.
 *
 * For JSX contexts use `<Flag iso>` (components/Flag.tsx) instead — it draws Syria as
 * geometry and is deterministic on every viewer.
 */
export function isoFlag(iso: string): string {
  const cc = iso.toUpperCase();
  if (cc === "SY") return "";
  return [...cc].map((c) => String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65)).join("");
}

/* ---------------- DASH-CARD: the maritime Arabic register ---------------- */

/**
 * The backend's `headline` ends with the registration flag, e.g.
 *   «… انقطع بثّها مرتين · علم التسجيل: Togolese Republic»
 * The owner's card model wants that as its OWN line, so it is split off here rather than
 * repeated — printing the headline whole AND a flag line would state the registry twice.
 *
 * ⛔ THE SPLIT IS DEFENSIVE, NOT ASSUMED. `BE-SUMMARY-1` builds these from fixed templates,
 * and the marker was measured present on 106/106 maritime headlines — but if it is ever
 * absent the headline is returned WHOLE and the flag line is simply not rendered. A parse
 * that silently truncated prose would be worse than one that declines to split.
 *
 * ⚠ NO FLAG EMOJI IS RETURNED, and that is a contract limit, not an oversight. `isoFlag`
 * needs ISO alpha-2; the alert carries the registry as an English NAME inside this string
 * and no `flag_country`/ISO field exists anywhere on the contract (verified across all 324
 * live alerts). Mapping ~200 registry names to ISO here would be inventing a table the
 * backend deliberately did not ship. The mandatory words «علم التسجيل» carry the meaning
 * instead — which is the part that must never be dropped, since a flag is an administrative
 * fact and not a position.
 */
const FLAG_MARK = " · علم التسجيل: ";

export function splitHeadline(headline: string): { main: string; flagCountry: string | null } {
  const i = headline.lastIndexOf(FLAG_MARK);
  if (i < 0) return { main: headline, flagCountry: null };
  const country = headline.slice(i + FLAG_MARK.length).trim();
  if (!country) return { main: headline, flagCountry: null };
  return { main: headline.slice(0, i).trim(), flagCountry: country };
}

/**
 * Does this alert carry the Arabic register? Maritime-only today.
 *
 * ⛔ The test is on the FIELDS, not on `family === "maritime"`. When the other four families
 * are generalised to they will light up here with no edit, and — more importantly — a
 * maritime row that somehow arrived without them renders its absence honestly instead of
 * crashing or falling back to English.
 */
export function hasArabicRegister(a: Alert): boolean {
  return Boolean(a.headline && a.why && a.caveat);
}
