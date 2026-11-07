/**
 * Vessel search detail (E1) — the expandable "everything about this vessel"
 * card. Collapsed = the summary (identity + last known fix + behavioural event
 * counts). Expanding on demand pulls + draws the full track (the expensive part,
 * one vessel at a time) and reveals its stats: fix count, time span, and the
 * position-quality breakdown — so a spoofed vessel's implausible fixes are
 * COUNTED and visible, never hidden. The map draws the track itself.
 */

import type { VesselSummary, VesselTrackPoint } from "../api/types";
import { utcShort } from "../lib/format";
import { T } from "../lib/strings";
import FieldGrid from "./FieldGrid";

interface Props {
  summary: VesselSummary | null;
  track: VesselTrackPoint[] | null;
  trackLoading: boolean;
  trackHasMore: boolean;
  expanded: boolean;
  onToggleTrack: () => void;
  onClear: () => void;
}

const Q_ORDER = ["plausible", "suspect", "implausible"] as const;

function qualityCounts(track: VesselTrackPoint[]): Record<string, number> {
  const c: Record<string, number> = {};
  for (const p of track) {
    const k = p.position_quality ?? "plausible";
    c[k] = (c[k] ?? 0) + 1;
  }
  return c;
}

export default function VesselSearchDetail({
  summary, track, trackLoading, trackHasMore, expanded, onToggleTrack, onClear,
}: Props) {
  const E = T.entitySearch;
  if (!summary) {
    return (
      <div className="strip" aria-live="polite">
        <div className="empty">{E.detailEmpty}</div>
      </div>
    );
  }
  const s = summary;
  const typeText = s.ship_type ? T.vessel.types[s.ship_type] ?? s.ship_type : "—";
  const dims = s.length_m != null && s.beam_m != null ? `${s.length_m}×${s.beam_m} m` : "—";
  const cells: Array<[string, string]> = [
    ["MMSI", s.mmsi],
    ["IMO", s.imo || "—"],
    [T.vessel.callsign, s.callsign || "—"],
    [T.vessel.type, typeText],
    [E.dimensions, dims],
    [T.vessel.status, s.station_type || "—"],
  ];
  const patterns: Array<[string, number]> = [
    [E.pat.gaps, s.n_gaps],
    [E.pat.rendezvous, s.n_rendezvous],
    [E.pat.identity_changes, s.n_identity_changes],
    [E.pat.gnss, s.n_gnss],
  ].filter(([, n]) => (n as number) > 0) as Array<[string, number]>;

  const qc = track ? qualityCounts(track) : null;
  const span =
    track && track.length > 1
      ? `${utcShort(track[0].ts)} — ${utcShort(track[track.length - 1].ts)}`
      : null;

  return (
    <div className="strip" aria-live="polite">
      <div className="strip-head">
        <span className="cs">{s.name || `#${s.mmsi}`}</span>
        <span className="icao">{s.flag_country || ""}</span>
      </div>

      <FieldGrid cells={cells} />

      {/* last known fix — the data vintage */}
      <div className="detail-line">
        <span className="k">{E.lastFix}</span>
        <span className="v">
          {s.last_ts ? utcShort(s.last_ts) : "—"}
          {s.last_position_quality && s.last_position_quality !== "plausible" && (
            <span className={`badge q-badge q-${s.last_position_quality}`}>
              {E.quality[s.last_position_quality] ?? s.last_position_quality}
            </span>
          )}
        </span>
      </div>
      {s.last_lat != null && s.last_lon != null && (
        <div className="detail-line">
          <span className="k">{E.position}</span>
          <span className="v mono">{s.last_lat.toFixed(4)}, {s.last_lon.toFixed(4)}</span>
        </div>
      )}

      {/* behavioural patterns — investigation leads */}
      <div className="section-label">{E.patternsLabel}</div>
      {patterns.length > 0 ? (
        <div className="pat-chips">
          {patterns.map(([label, n]) => (
            <span className="pat-chip" key={label}>
              {label} <b>{n}</b>
            </span>
          ))}
        </div>
      ) : (
        <div className="filter-note">{E.patternsNone}</div>
      )}

      {/* track expansion (on demand) */}
      <div className="track-controls">
        <button aria-pressed={expanded} onClick={onToggleTrack}>
          {expanded ? E.hideTrack : E.showTrack}
        </button>
        <span className="filter-note">{E.fixesLabel(s.n_fixes)}</span>
      </div>
      {expanded && (
        <div className="track-stats" aria-live="polite">
          {trackLoading ? (
            <div className="filter-note">{E.trackLoading}</div>
          ) : !track || track.length === 0 ? (
            <div className="filter-note">{E.trackEmpty}</div>
          ) : (
            <>
              {span && (
                <div className="detail-line">
                  <span className="k">{E.trackSpan}</span>
                  <span className="v mono">{span}</span>
                </div>
              )}
              <div className="q-legend">
                {Q_ORDER.filter((q) => qc && qc[q]).map((q) => (
                  <span className={`q-key q-${q}`} key={q}>
                    <i className="q-sw" /> {E.quality[q]} {qc![q]}
                  </span>
                ))}
              </div>
              {trackHasMore && <div className="filter-note">{E.trackCapped}</div>}
            </>
          )}
        </div>
      )}

      <div className="strip-warn">{E.caveat}</div>
      <div className="strip-foot">
        <span className="filter-note">{E.frozen}</span>
        <button onClick={onClear}>{E.clear}</button>
      </div>
    </div>
  );
}
