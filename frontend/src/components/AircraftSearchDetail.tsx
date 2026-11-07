/**
 * Aircraft search detail (E2) — the air parallel of VesselSearchDetail. Collapsed
 * = a PRESENCE summary (identity + last-seen fix + AOI status); air has NO
 * behavioural-event layer, so there are no pattern chips — that absence is stated
 * honestly. Expanding on demand pulls + draws the track (via /history) and shows
 * its stats. Field labels reuse the existing T.strip.* aviation vocabulary.
 */

import type { AircraftSummary } from "../api/types";
import { M_TO_FT, utcShort } from "../lib/format";
import { T } from "../lib/strings";
import FieldGrid from "./FieldGrid";

type TrailPoint = [number, number, number | null];

interface Props {
  summary: AircraftSummary | null;
  trail: TrailPoint[] | null;
  trackLoading: boolean;
  trackHasMore: boolean;
  expanded: boolean;
  onToggleTrack: () => void;
  onClear: () => void;
}

const MS_TO_KT = 1.94384;

export default function AircraftSearchDetail({
  summary, trail, trackLoading, trackHasMore, expanded, onToggleTrack, onClear,
}: Props) {
  const E = T.entitySearch;
  if (!summary) {
    return (
      <div className="strip" aria-live="polite">
        <div className="empty">{E.detailEmpty}</div>
      </div>
    );
  }
  const a = summary;
  const alt = a.baro_altitude ?? a.geo_altitude;
  const cells: Array<[string, string]> = [
    ["ICAO24", a.icao24],
    [T.strip.registration, a.registration || "—"],
    [T.strip.aircraftType, a.aircraft_type || a.icao_type || "—"],
    [T.strip.operator, a.operator || "—"],
    [T.strip.baroAlt, alt == null ? "—" : `${Math.round(alt * M_TO_FT).toLocaleString("en")} ${T.units.ft}`],
    [T.strip.speed, a.velocity == null ? "—" : `${Math.round(a.velocity * MS_TO_KT)} ${T.units.kt}`],
  ];
  // AOI status (from the last fix's P3 geo columns)
  const aoi = a.country
    ? `${T.strip.country}: ${a.country}`
    : a.near_country
      ? `${T.strip.nearBorder}: ${a.near_country}`
      : "—";
  const hasRoute = a.origin_icao || a.dest_icao;
  const trailFixes = trail ? trail.length : 0;

  return (
    <div className="strip" aria-live="polite">
      <div className="strip-head">
        <span className="cs">{a.callsign || `#${a.icao24}`}</span>
        <span className="icao">{a.operator_country || ""}</span>
      </div>

      <FieldGrid cells={cells} />

      {/* standing route (sparse — flight_routes by callsign) */}
      <div className="detail-line">
        <span className="k">{E.air.route}</span>
        <span className="v mono">
          {hasRoute ? `${a.origin_icao || "?"} → ${a.dest_icao || "?"}` : E.air.noRoute}
        </span>
      </div>

      {/* last-known fix — the presence signal (live for an airborne aircraft) */}
      <div className="detail-line">
        <span className="k">{T.strip.lastSeen}</span>
        <span className="v mono">
          {a.last_ts ? utcShort(a.last_ts) : "—"}
          <span className="badge q-badge">{a.on_ground ? E.air.onGround : E.air.airborne}</span>
        </span>
      </div>
      {a.last_lat != null && a.last_lon != null && (
        <div className="detail-line">
          <span className="k">{T.strip.position}</span>
          <span className="v mono">{a.last_lat.toFixed(4)}, {a.last_lon.toFixed(4)}</span>
        </div>
      )}
      <div className="detail-line">
        <span className="k">{T.strip.country}</span>
        <span className="v">{aoi}</span>
      </div>
      {a.first_seen && (
        <div className="detail-line">
          <span className="k">{E.air.trackedSince}</span>
          <span className="v mono">{utcShort(a.first_seen)}</span>
        </div>
      )}

      {/* honest: no behavioural-event layer for air */}
      <div className="filter-note">{E.air.presenceNote}</div>

      {/* track expansion (on demand) — via /history */}
      <div className="track-controls">
        <button aria-pressed={expanded} onClick={onToggleTrack}>
          {expanded ? E.hideTrack : E.showTrack}
        </button>
        <span className="filter-note">{E.air.trackNote}</span>
      </div>
      {expanded && (
        <div className="track-stats" aria-live="polite">
          {trackLoading ? (
            <div className="filter-note">{E.trackLoading}</div>
          ) : trailFixes === 0 ? (
            <div className="filter-note">{E.trackEmpty}</div>
          ) : (
            <>
              <div className="detail-line">
                <span className="k">{E.trackSection}</span>
                <span className="v mono">{E.fixesLabel(trailFixes)}</span>
              </div>
              {trackHasMore && <div className="filter-note">{E.trackCapped}</div>}
            </>
          )}
        </div>
      )}

      <div className="strip-warn">{T.strip.routeNote}</div>
      <div className="strip-foot">
        <span className="filter-note">{a.source || ""}</span>
        <button onClick={onClear}>{E.clear}</button>
      </div>
    </div>
  );
}
