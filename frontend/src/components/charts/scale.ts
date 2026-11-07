/** Tiny shared chart-scale helpers (SVG charts, no chart library). */

export function niceMax(rawMax: number): number {
  if (rawMax <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(rawMax));
  const norm = rawMax / mag;
  const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return nice * mag;
}

export function ticks(max: number, count = 4): number[] {
  const out: number[] = [];
  for (let i = 0; i <= count; i++) out.push((max / count) * i);
  return out;
}

/** x position for a time value in [t0, t1] mapped to [left, left+width]. */
export function xScale(t: number, t0: number, t1: number, left: number, width: number): number {
  if (t1 <= t0) return left;
  return left + ((t - t0) / (t1 - t0)) * width;
}
