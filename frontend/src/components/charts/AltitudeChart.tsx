/**
 * Max barometric altitude per bucket — a single-series line (the title names
 * it; one series ⇒ no legend box, per the dataviz method). Time-linear x;
 * segments break across missing buckets instead of lying with a bridge line.
 * Crosshair + tooltip on hover.
 */

import { useRef, useState } from "react";
import type { StatsBucket } from "../../api/types";
import { M_TO_FT, num, plausibleBaro, utcShort } from "../../lib/format";
import { T } from "../../lib/strings";
import { niceMax, ticks, xScale } from "./scale";

const W = 720;
const H = 180;
const M = { top: 10, right: 10, bottom: 22, left: 52 };

interface Props {
  buckets: StatsBucket[];
  bucketSeconds: number;
}

export default function AltitudeChart({ buckets, bucketSeconds }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<{ i: number; cx: number; cy: number; px: number; py: number } | null>(null);

  const pts = buckets
    .map((b) => ({
      t: Date.parse(b.bucket_start) / 1000,
      // bogus transponder readings (>60k ft) render as honest gaps
      altFt: plausibleBaro(b.max_baro_altitude) == null ? null : b.max_baro_altitude! * M_TO_FT,
      b,
    }))
    .filter((p): p is typeof p & { altFt: number } => p.altFt !== null);

  if (pts.length === 0) return <div className="chart-empty">{T.stats.emptyAlt}</div>;

  const t0 = pts[0].t;
  const t1 = pts[pts.length - 1].t + bucketSeconds;
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;
  const yMax = niceMax(Math.max(...pts.map((p) => p.altFt)));
  const y = (v: number) => M.top + plotH - (v / yMax) * plotH;
  const x = (t: number) => xScale(t, t0, t1, M.left, plotW);

  // break the line where a bucket is missing (gap > 1.5× bucket size)
  const segments: string[] = [];
  let d = "";
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    const gap = i > 0 && p.t - pts[i - 1].t > bucketSeconds * 1.5;
    if (i === 0 || gap) {
      if (d) segments.push(d);
      d = `M ${x(p.t).toFixed(1)} ${y(p.altFt).toFixed(1)}`;
    } else {
      d += ` L ${x(p.t).toFixed(1)} ${y(p.altFt).toFixed(1)}`;
    }
  }
  if (d) segments.push(d);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * W;
    let best = 0;
    let bestDist = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const dd = Math.abs(x(pts[i].t) - mx);
      if (dd < bestDist) {
        bestDist = dd;
        best = i;
      }
    }
    setHover({ i: best, cx: x(pts[best].t), cy: y(pts[best].altFt), px: e.clientX, py: e.clientY });
  };

  return (
    <>
      <svg
        className="chart-svg"
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={T.stats.altAria}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {ticks(yMax).map((tv) => (
          <g key={tv}>
            <line x1={M.left} x2={W - M.right} y1={y(tv)} y2={y(tv)} stroke="var(--chart-grid)" strokeWidth={1} />
            <text x={M.left - 6} y={y(tv) + 3} textAnchor="end">{num(Math.round(tv))}</text>
          </g>
        ))}
        <line x1={M.left} x2={W - M.right} y1={y(0)} y2={y(0)} stroke="var(--chart-axis)" strokeWidth={1} />
        {segments.map((seg) => (
          <path key={seg} d={seg} fill="none" stroke="var(--series-air)" strokeWidth={2} strokeLinejoin="round" />
        ))}
        {hover && (
          <>
            <line x1={hover.cx} x2={hover.cx} y1={M.top} y2={M.top + plotH} stroke="var(--chart-axis)" strokeWidth={1} strokeDasharray="3 3" />
            <circle cx={hover.cx} cy={hover.cy} r={4} fill="var(--series-air)" stroke="var(--panel)" strokeWidth={2} />
          </>
        )}
      </svg>
      {hover && (
        <div className="chart-tip" style={{ left: hover.px + 12, top: hover.py + 12 }}>
          <span className="tk">{utcShort(pts[hover.i].b.bucket_start)}</span>
          {" · "}{num(Math.round(pts[hover.i].altFt))} {T.units.ft}
        </div>
      )}
    </>
  );
}
