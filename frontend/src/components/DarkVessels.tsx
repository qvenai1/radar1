/**
 * GFW satellite-AIS DARK-VESSEL panel — an ANALYSIS layer in the maritime
 * «التحليل» group. Lists vessels that went dark (stopped transmitting) where free
 * terrestrial AIS is blind (Persian Gulf / Red Sea / open ocean). Presentational:
 * App owns the fetch + the map markers; this shows the region selector + the list
 * + the honest caveat. A LEAD, not proof.
 */

import type { DarkVessel } from "../api/types";
import Caveat from "./Caveat";
import { T } from "../lib/strings";

interface Props {
  vessels: DarkVessel[];
  available: boolean;
  reason: string | null;
  loading: boolean;
  region: string;
  onRegion: (r: string) => void;
  /** UI-1: the response's caveat — was previously fetched and DISCARDED here, so the
   *  authoritative GFW methodology never reached the analyst. Now it feeds the shared
   *  Caveat control as the drift baseline + the expandable original. */
  caveat?: string | null;
}

function fmtDur(h: number | null): string {
  if (h == null) return "?";
  if (h < 48) return `${Math.round(h)}h`;
  const d = h / 24;
  return d < 90 ? `${Math.round(d)}d` : `${Math.round(d / 30)}mo`;
}

export default function DarkVessels({
  vessels, available, reason, loading, region, onRegion, caveat,
}: Props) {
  const C = T.dark;
  const suspect = vessels.reduce((n, v) => n + (v.on_land ? 1 : 0), 0);
  return (
    <div className="dark-panel">
      <div className="seg nav-seg dark-regions" role="group" aria-label={C.region}>
        {(["all", "gulf", "redsea"] as const).map((r) => (
          <button key={r} aria-pressed={region === r} onClick={() => onRegion(r)}>
            {C.regions[r]}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="filter-note">{C.loading}</div>
      ) : !available ? (
        <div className="filter-note">
          {reason === "GFW_API_TOKEN not set" ? C.disabled : C.empty}
        </div>
      ) : (
        <>
          {suspect > 0 && (
            <div className="dark-suspect-note">
              {C.suspectNote.replace("{n}", String(suspect))}
            </div>
          )}
          <div className="dark-list" role="list">
            {vessels.map((v, i) => (
              <div
                className={`dark-row${v.on_land ? " land" : ""}`}
                role="listitem"
                key={`${v.mmsi}-${v.start}-${i}`}
              >
                <span
                  className={`dark-dot${v.on_land ? " land" : v.intentional ? " int" : ""}`}
                  aria-hidden="true"
                />
                <span className="dark-name">{v.name || `MMSI ${v.mmsi ?? "?"}`}</span>
                {v.flag && <span className="dark-flag" dir="ltr">{v.flag}</span>}
                {v.on_land ? (
                  <span className="dark-tag land">{C.onLandTag}</span>
                ) : (
                  v.intentional && <span className="dark-tag">{C.intentional}</span>
                )}
                <span className="dark-dur" dir="ltr">{fmtDur(v.duration_h)}</span>
              </div>
            ))}
          </div>
          <Caveat k="dark" response={caveat} className="strip-warn" />
        </>
      )}
    </div>
  );
}
