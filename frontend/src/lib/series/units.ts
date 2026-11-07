/**
 * UI-3 constraint 4 — THE UNIT IS A TYPE, NOT A STRING.
 *
 * ⛔ THE DEFECT THIS PREVENTS is one the project has already shipped once: `/adsb` summed counts
 * from populations that do not share a denominator. Here the risk is wider, because the dashboard
 * draws four different things that all look like "a number over time":
 *   airframes  — distinct aircraft (air, /air/series/country)
 *   events     — detector firings (sea, ais-gaps / rendezvous-points)
 *   hulls      — IMO-stitched vessels (sea, multi-pattern / flags)
 *   hull-days  — an airframe-day product (air, /air/series/country-daily)
 *   passes     — computed AOI passes (space)
 * A `unit: string` beside a `count: number` asks every future author to remember; a phantom type
 * makes the compiler remember instead.
 *
 * ⚠ HULL-DAYS ARE NOT A DEGRADED AIRFRAME COUNT. The backend's own words: for "which countries lead
 * over time" they are arguably the BETTER measure, because they weight presence over time rather
 * than mere appearance. So `hull-days` is a peer unit here, not a fallback — and `unitLabel` states
 * it as a choice. What is forbidden is placing it beside `airframes` as if they were the same axis.
 */

/** The five units the seven contracts actually carry. ⚠ Closed on purpose — unlike the wire's open
 *  `unit` string, this is OUR axis vocabulary, and a sixth axis is a deliberate change here. */
export type Unit = "airframes" | "events" | "hulls" | "hull-days" | "passes";

declare const UNIT_BRAND: unique symbol;

/** A number that knows what it counts. ⛔ Two `Measure`s of different `U` are different types, so
 *  `add` cannot be called across them — the error is at compile time, not in review. */
export type Measure<U extends Unit> = number & { readonly [UNIT_BRAND]: U };

export const measure = <U extends Unit>(n: number, _unit: U): Measure<U> => n as Measure<U>;

/** ⛔ THE ONLY ADDITION. Both operands carry the same `U`, so summing airframes onto hull-days is
 *  not "discouraged" — it does not typecheck.
 *
 * ⚠ `NoInfer` ON THE SECOND OPERAND IS LOAD-BEARING, AND ITS ABSENCE WAS A REAL HOLE — measured,
 * not anticipated. With `(a: Measure<U>, b: Measure<U>)` TypeScript infers `U` from BOTH arguments
 * and happily widens it to the UNION: `add(airframes, hullDays)` inferred
 * `U = "airframes" | "hull-days"` and COMPILED, because `Measure<"airframes">` is assignable to
 * `Measure<"airframes" | "hull-days">`. The first version of this file shipped that hole and the
 * gate caught it. `NoInfer` pins `U` to the first operand, so the second must match it exactly. */
export const add = <U extends Unit>(a: Measure<U>, b: NoInfer<Measure<U>>): Measure<U> =>
  ((a as number) + (b as number)) as Measure<U>;

export const sum = <U extends Unit>(xs: readonly Measure<U>[], unit: U): Measure<U> =>
  measure(xs.reduce<number>((t, x) => t + (x as number), 0), unit);

/** ⛔ The wire's `unit` is prose (e.g. "hulls (IMO-stitched), not vessels and not events"), so it is
 *  MAPPED here rather than trusted as a key. An unrecognised unit is not guessed: the caller gets
 *  null and must refuse to draw, which is the fail-closed direction. */
export function unitOf(wireUnit: string): Unit | null {
  const u = wireUnit.toLowerCase();
  if (u.includes("hull-day") || u.includes("hull day")) return "hull-days";  // ⛔ before "hull"
  if (u.includes("airframe")) return "airframes";
  if (u.includes("hull")) return "hulls";
  if (u.includes("pass")) return "passes";
  if (u.includes("event") || u.includes("episode") || u.includes("session")) return "events";
  return null;
}

/** What a reader must be told the axis counts. ⚠ hull-days carries its own note, because a bare
 *  "hull-days" invites reading it as a hull count. */
export const unitLabel: Record<Unit, string> = {
  airframes: "أبدان جوية مميَّزة",
  events: "أحداث كاشف",
  hulls: "أبدان (مُخاطة بالـIMO)",
  "hull-days": "بدن-يوم — حضور موزون بالزمن، لا عدد أبدان",
  passes: "ممرّات محسوبة",
};
