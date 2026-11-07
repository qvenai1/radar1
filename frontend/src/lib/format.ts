/**
 * THE unit/format module. All metric→display conversions live here and
 * nowhere else (contract rule: no scattered magic constants).
 * Contract units: altitude m, velocity & vertical_rate m/s, track deg.
 * Display units: ft, kt, fpm.
 */

import { T } from "./strings";

export const M_TO_FT = 3.28084;
export const MS_TO_KT = 1.94384;
export const MS_TO_FPM = M_TO_FT * 60;

/** Bogus-transponder guard (display layer ONLY — archive/DB stay verbatim):
 *  baro altitudes above 60,000 ft are implausible for anything we track. */
export const MAX_PLAUSIBLE_BARO_M = 18288;

export function plausibleBaro(alt: number | null): number | null {
  return alt != null && alt > MAX_PLAUSIBLE_BARO_M ? null : alt;
}

const EM_DASH = "—";

export function ft(meters: number | null): string {
  return meters == null ? EM_DASH : `${Math.round(meters * M_TO_FT).toLocaleString("en-US")} ${T.units.ft}`;
}

export function kt(ms: number | null): string {
  return ms == null ? EM_DASH : `${Math.round(ms * MS_TO_KT)} ${T.units.kt}`;
}

export function fpm(ms: number | null): string {
  if (ms == null) return EM_DASH;
  const v = Math.round(ms * MS_TO_FPM);
  return `${v > 0 ? "+" : ""}${v.toLocaleString("en-US")} ${T.units.fpm}`;
}

export function deg(d: number | null): string {
  return d == null ? EM_DASH : `${Math.round(d)}°`;
}

export function coord(lat: number | null, lon: number | null): string {
  if (lat == null || lon == null) return EM_DASH;
  return `${lat.toFixed(3)}, ${lon.toFixed(3)}`;
}

/** Contract: callsign may be empty or padded with trailing spaces. */
export function callsign(raw: string): string {
  const c = raw.trim();
  return c === "" ? T.units.noCallsign : c;
}

export function utcClock(epochSeconds: number | null): string {
  if (epochSeconds == null) return EM_DASH;
  return new Date(epochSeconds * 1000).toISOString().slice(11, 19) + "Z";
}

export function utcShort(iso: string): string {
  // "2026-07-08T11:14:00+00:00" → "11:14Z"
  const d = new Date(iso);
  return d.toISOString().slice(11, 16) + "Z";
}

export function utcDateTime(epochSeconds: number): string {
  const s = new Date(epochSeconds * 1000).toISOString();
  return s.slice(0, 10) + " " + s.slice(11, 19) + "Z";
}

export function ageSeconds(sec: number | null): string {
  if (sec == null) return EM_DASH;
  if (sec < 90) return `${Math.round(sec)}s`;
  if (sec < 5400) return `${Math.round(sec / 60)}m`;
  // UI-FRESH: days past 48 h. A four-day-old MarineTraffic position used to read
  // "101.4h", which does not land as "this is days old" — the whole point of marking it.
  if (sec < 172_800) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86_400).toFixed(1)}d`;
}

/** The backend's SNAPSHOT freshness window (api.MARITIME_STALE_S) — it describes whether a
 *  SOURCE's file/store is current. The backend's own `SourceVintage.stale` uses it; the
 *  strip just displays that verdict. */
export const LIVE_WINDOW_S = 180;

/**
 * The PER-ROW position-age threshold — deliberately NOT the 180 s snapshot window.
 *
 * A vessel's own position age reflects AIS reporting cadence, not feed health: an anchored
 * ship transmits about every 3 minutes, so 180 s marks perfectly-current vessels as stale.
 * Measured on the live feed (2026-07-29, n=238 aisstream / 2,616 marinetraffic):
 *
 *   aisstream     median 2.8m · p90 8.0m · max 12m  → 46% exceed 180 s, 0% exceed 15m
 *   marinetraffic median ~4.0d · min >1d            → 100% exceed every threshold
 *
 * 900 s separates the two populations exactly: no false positives on the live feed, every
 * row of the frozen store marked, and a genuinely stalled AIS row (>15m, well past p90)
 * still gets flagged. A 180 s per-row rule would have marked half the live feed and
 * drowned out the four-day-old rows it exists to expose.
 */
export const ROW_STALE_S = 900;

/** How old an ISO instant is, in seconds — null when absent/unparseable. */
export function isoAgeSeconds(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.now() - Date.parse(iso);
  return Number.isNaN(ms) ? null : Math.max(0, ms / 1000);
}

/** Is this ROW's own position older than the per-row threshold? Flag, never drop (G3). */
export function isStale(iso: string | null | undefined, windowS = ROW_STALE_S): boolean {
  const a = isoAgeSeconds(iso);
  return a != null && a > windowS;
}

export function num(n: number | null | undefined): string {
  return n == null ? EM_DASH : n.toLocaleString("en-US");
}

export function isoAge(iso: string | null): string {
  // an ISO instant → how long ago ("18s" / "30m" / "4.7h"); the freshness read.
  if (!iso) return EM_DASH;
  const ms = Date.now() - Date.parse(iso);
  if (Number.isNaN(ms)) return EM_DASH;
  return ageSeconds(Math.max(0, ms / 1000));
}
