/**
 * SGP4 propagation helpers (satellite.js). This is scraplan's ONE piece of
 * client-side physics: a satellite's position is deterministic from its TLE,
 * so we compute it locally instead of polling a per-position API. The backend
 * only serves cached TLEs; motion here costs the server nothing.
 *
 * All angles degrees, altitude km, velocity km/s. Returns null when SGP4
 * fails (decayed/unpropagatable element) so callers can drop that satellite.
 */

import * as satellite from "satellite.js";
import type { SatelliteElement } from "../api/types";

export interface SatRec {
  norad: number;
  name: string;
  rec: satellite.SatRec;
  inclDeg: number | null;
  periodMin: number | null;
}

export interface SatFix {
  lat: number;
  lon: number;
  altKm: number;
  velKmS: number | null;
}

/** Parse static orbital params straight from TLE line 2 (no propagation). */
function orbitalParams(tle2: string): { inclDeg: number | null; periodMin: number | null } {
  const incl = parseFloat(tle2.slice(8, 16));
  const meanMotion = parseFloat(tle2.slice(52, 63)); // revs/day
  return {
    inclDeg: Number.isFinite(incl) ? incl : null,
    periodMin: Number.isFinite(meanMotion) && meanMotion > 0 ? 1440 / meanMotion : null,
  };
}

export function makeSatRec(el: SatelliteElement): SatRec | null {
  try {
    const rec = satellite.twoline2satrec(el.tle1, el.tle2);
    if (!rec || rec.error) return null;
    const { inclDeg, periodMin } = orbitalParams(el.tle2);
    return { norad: el.norad_id, name: el.name, rec, inclDeg, periodMin };
  } catch {
    return null;
  }
}

/**
 * Ground-track: the sub-satellite lat/lon over `spanMin` from `start`, sampled at
 * `points` steps (E3 search expansion). This is scraplan's client-side physics
 * (same SGP4 as the live tab) — a satellite has NO stored fixes, so its "track" is
 * COMPUTED, not pulled. Callers pass spanMin = one orbital period for a full loop;
 * for a GEO the loop is a tiny equatorial band (near-stationary), which is honest.
 * Points where SGP4 fails are skipped.
 */
export function groundTrack(sat: SatRec, start: Date, spanMin: number, points: number): [number, number][] {
  const out: [number, number][] = [];
  const n = Math.max(2, points);
  for (let i = 0; i <= n; i++) {
    const when = new Date(start.getTime() + (spanMin * 60_000 * i) / n);
    const f = propagate(sat, when);
    if (f) out.push([f.lat, f.lon]);
  }
  return out;
}

export function propagate(sat: SatRec, when: Date): SatFix | null {
  try {
    const pv = satellite.propagate(sat.rec, when);
    const eci = pv.position;
    if (!eci || typeof eci === "boolean") return null;
    const gmst = satellite.gstime(when);
    const geo = satellite.eciToGeodetic(eci, gmst);
    const lat = satellite.degreesLat(geo.latitude);
    const lon = satellite.degreesLong(geo.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    let velKmS: number | null = null;
    const v = pv.velocity;
    if (v && typeof v !== "boolean") {
      velKmS = Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
    }
    return { lat, lon, altKm: geo.height, velKmS };
  } catch {
    return null;
  }
}
