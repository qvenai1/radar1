/**
 * Stacked bars: airborne + ground state-vector counts per time bucket.
 * Dataviz method: part-to-whole per bucket → stacked bar; two categorical
 * series in validated slots (--series-air / --series-ground); 2px surface
 * gap between stacked segments; time-linear x-axis so missing buckets show
 * as honest gaps (the API returns only non-empty buckets — never assume
 * zero-fill); legend present (2 series); hover tooltip per mark.
 */

import { useState } from "react";
import type { StatsBucket } from "../../api/types";
import { num, utcShort } from "../../lib/format";
import { T } from "../../lib/strings";
import { niceMax, ticks, xScale } from "./scale";

const W = 720;
const H = 220;
const M = { top: 10, right: 10, bottom: 22, left: 46 };

interface Props {
  buckets: StatsBucket[];
  bucketSeconds: number;
}

interface Tip {
  x: number;
  y: number;
  b: StatsBucket;
}

export default function TrafficChart({ buckets, bucketSeconds }: Props) {
  const [tip, setTip] = useState<Tip | null>(null);
  if (buckets.length === 0) return <div className="chart-empty">{T.stats.emptyTraffic}</div>;

  const t0 = Date.parse(buckets[0].bucket_start) / 1000;
  const t1 = Date.parse(buckets[buckets.length - 1].bucket_start) / 1000 + bucketSeconds;
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;
  const yMax = niceMax(Math.max(...buckets.map((b) => b.state_count)));
  const y = (v: number) => M.top + plotH - (v / yMax) * plotH;
  const barW = Math.max((bucketSeconds / (t1 - t0)) * plotW - 2, 1);

  const xTickCount = Math.min(6, buckets.length);
  const xTicks = Array.from({ length: xTickCount }, (_, i) => t0 + ((t1 - t0) / (xTickCount - 1 || 1)) * i);

  return (
    <>
      <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={T.stats.trafficAria}>
        {ticks(yMax).map((tv) => (
          <g key={tv}>
            <line x1={M.left} x2={W - M.right} y1={y(tv)} y2={y(tv)} stroke="var(--chart-grid)" strokeWidth={1} />
            <text x={M.left - 6} y={y(tv) + 3} textAnchor="end">{num(Math.round(tv))}</text>
          </g>
        ))}
        {xTicks.map((tv) => (
          <text key={tv} x={xScale(tv, t0, t1, M.left, plotW)} y={H - 6} textAnchor="middle">
            {utcShort(new Date(tv * 1000).toISOString())}
          </text>
        ))}
        <line x1={M.left} x2={W - M.right} y1={y(0)} y2={y(0)} stroke="var(--chart-axis)" strokeWidth={1} />
        {buckets.map((b) => {
          const bx = xScale(Date.parse(b.bucket_start) / 1000, t0, t1, M.left, plotW);
          const hGround = (b.ground_count / yMax) * plotH;
          const hAir = (b.airborne_count / yMax) * plotH;
          return (
            <g
              key={b.bucket_start}
              onMouseEnter={(e) => setTip({ x: e.clientX, y: e.clientY, b })}
              onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, b })}
              onMouseLeave={() => setTip(null)}
            >
              {/* hover target wider than the mark */}
              <rect x={bx - 2} y={M.top} width={barW + 4} height={plotH} fill="transparent" />
              <rect x={bx} y={y(0) - hGround} width={barW} height={Math.max(hGround, b.ground_count > 0 ? 1 : 0)} fill="var(--series-ground)" rx={1} />
              {/* 2px surface gap between stacked segments */}
              <rect
                x={bx}
                y={y(0) - hGround - 2 - hAir}
                width={barW}
                height={Math.max(hAir, b.airborne_count > 0 ? 1 : 0)}
                fill="var(--series-air)"
                rx={1}
              />
            </g>
          );
        })}
      </svg>
      <div className="legend">
        <span className="key"><span className="sw" style={{ background: "var(--series-air)" }} /> {T.stats.legendAir}</span>
        <span className="key"><span className="sw" style={{ background: "var(--series-ground)" }} /> {T.stats.legendGround}</span>
      </div>
      {tip && (
        <div className="chart-tip" style={{ left: tip.x + 12, top: tip.y + 12 }}>
          <span className="tk">{utcShort(tip.b.bucket_start)}</span>
          {" · "}{T.stats.tipAir} {num(tip.b.airborne_count)} · {T.stats.tipGround} {num(tip.b.ground_count)}
          {" · "}{T.stats.tipSnaps} {num(tip.b.snapshot_count)}
        </div>
      )}
    </>
  );
}
