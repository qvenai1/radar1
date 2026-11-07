/**
 * Reentry watch — a compact panel for the أقمار tab listing objects whose
 * PREDICTED reentry impact point falls INSIDE/NEAR the seven-country region
 * (Space-Track TIP, geo-classified by the shared AOI classifier). Minimal by
 * design: the predicted point is a single nominal point with large uncertainty,
 * so this flags "worth watching," it is NOT a precise impact map.
 */

import type { ReentryEvent } from "../api/types";
import { T } from "../lib/strings";

interface Props {
  events: ReentryEvent[];
}

function whenText(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toISOString().slice(0, 16).replace("T", " ")}Z`;
}

export default function ReentryWatch({ events }: Props) {
  const aoi = events.filter((e) => e.aoi);
  return (
    <div className="reentry-watch" role="region" aria-label={T.reentry.section}>
      <div className="section-label">
        {T.reentry.section} — {T.reentry.aoiWatch} ({aoi.length})
      </div>
      {aoi.length === 0 ? (
        <div className="empty" style={{ fontStyle: "italic", padding: "4px 0" }}>
          {T.reentry.none}
        </div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>{T.reentry.colObject}</th>
              <th>{T.reentry.colTime}</th>
              <th>{T.reentry.colWhere}</th>
            </tr>
          </thead>
          <tbody>
            {aoi.map((e) => (
              <tr key={e.norad_id}>
                <td className="v-name">{e.object_name || `#${e.norad_id}`}</td>
                <td>
                  {whenText(e.decay_epoch)}
                  {e.window_min != null ? ` ${T.reentry.window(e.window_min)}` : ""}
                </td>
                <td>
                  {e.country
                    ? `${T.reentry.inside} ${e.country}`
                    : e.near_country
                      ? `${T.reentry.near} ${e.near_country}`
                      : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="strip-warn">
        {T.reentry.imprecise} · {T.reentry.source}
      </div>
    </div>
  );
}
