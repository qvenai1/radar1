/**
 * Stats view: time-bucketed charts from /api/v1/stats (continuous aggregate).
 * Range presets pick a bucket size that stays far under the 5000-bucket cap.
 * Filters sit in one row above the charts (dataviz interaction rule).
 */

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { StatsBucketSize, StatsResponse } from "../api/types";
import { T } from "../lib/strings";
import AltitudeChart from "./charts/AltitudeChart";
import TrafficChart from "./charts/TrafficChart";
import DashViewToggle from "./DashViewToggle";

interface Preset {
  key: string;
  seconds: number;
  bucket: StatsBucketSize;
  bucketSeconds: number;
}

const PRESETS: Preset[] = [
  { key: "1h", seconds: 3600, bucket: "1m", bucketSeconds: 60 },
  { key: "6h", seconds: 6 * 3600, bucket: "5m", bucketSeconds: 300 },
  { key: "24h", seconds: 24 * 3600, bucket: "15m", bucketSeconds: 900 },
  { key: "7d", seconds: 7 * 86400, bucket: "1h", bucketSeconds: 3600 },
];

interface Props {
  /** DASH-NAV — back to رصد. The switch is the SURFACE's own control now (it moved out of
   *  the navbar), so this side renders its instance and hands App the same setView. */
  onDashView: (v: "monitor" | "stats") => void;
}

export default function StatsView({ onDashView }: Props) {
  const [preset, setPreset] = useState<Preset>(PRESETS[2]);
  const [data, setData] = useState<StatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const now = Math.floor(Date.now() / 1000);
    api
      .stats({ from: now - preset.seconds, to: now, bucket: preset.bucket })
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [preset]);

  return (
    <div className="stats-view" role="region" aria-label={T.stats.aria}>
      <div className="stats-controls">
        <DashViewToggle view="stats" onSelect={onDashView} />
        <span className="lbl">{T.stats.range}</span>
        <div className="seg segment" role="group" aria-label={T.stats.rangeAria}>
          {PRESETS.map((p) => (
            <button key={p.key} aria-pressed={p.key === preset.key} onClick={() => setPreset(p)}>
              {T.stats.presets[p.key]}
            </button>
          ))}
        </div>
        <span className="lbl">{T.stats.bucket}: {T.stats.buckets[preset.bucket]}</span>
        {loading && <span className="lbl">{T.stats.loading}</span>}
      </div>

      {error && <div className="chart-empty" role="alert">{T.stats.unavailable}{error}</div>}

      {data && !error && (
        <>
          <div className="chart-card">
            <h3>{T.stats.trafficTitle}</h3>
            <div className="sub">{T.stats.trafficSub(T.stats.buckets[preset.bucket])}</div>
            <TrafficChart buckets={data.buckets} bucketSeconds={preset.bucketSeconds} />
          </div>
          <div className="chart-card">
            <h3>{T.stats.altTitle}</h3>
            <div className="sub">{T.stats.altSub}</div>
            <AltitudeChart buckets={data.buckets} bucketSeconds={preset.bucketSeconds} />
          </div>
        </>
      )}
    </div>
  );
}
