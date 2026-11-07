/**
 * Satellite search detail (E3) — the space parallel of Vessel/AircraftSearchDetail.
 * The conceptual difference: NO stored fixes. The summary composes layers whose
 * VINTAGES DIFFER and are shown as distinct badges (identity SATCAT / orbit
 * derived-SGP4 "live" / strategic UCS FROZEN 2023 — the frozen one visibly set
 * apart). The live sub-point is self-propagated (SGP4, lib/orbit, 1 Hz) like the
 * existing SatelliteStrip. Expanding computes the ground-track (App → map); for a
 * GEO the detail states the stationing honestly instead of implying a moving track.
 */

import { useEffect, useMemo, useState } from "react";
import type { SatelliteSummary } from "../api/types";
import { makeSatRec, propagate } from "../lib/orbit";
import type { SatFix } from "../lib/orbit";
import { T } from "../lib/strings";
import FieldGrid from "./FieldGrid";

interface Props {
  summary: SatelliteSummary | null;
  expanded: boolean;
  onToggleTrack: () => void;
  onClear: () => void;
}

export default function SatelliteSearchDetail({ summary, expanded, onToggleTrack, onClear }: Props) {
  const S = T.entitySearch.sat;
  const sat = useMemo(
    () => (summary && summary.tle1 && summary.tle2
      ? makeSatRec({ norad_id: summary.norad_id, name: summary.object_name ?? "", tle1: summary.tle1, tle2: summary.tle2, epoch: summary.tle_epoch })
      : null),
    [summary],
  );
  const [fix, setFix] = useState<SatFix | null>(null);
  useEffect(() => {
    if (!sat) { setFix(null); return; }
    const tick = () => setFix(propagate(sat, new Date()));
    tick();
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, [sat]);

  if (!summary) {
    return (
      <div className="strip" aria-live="polite">
        <div className="empty">{T.entitySearch.detailEmpty}</div>
      </div>
    );
  }
  const s = summary;
  const geo = s.is_geostationary === true;

  const identity: Array<[string, string]> = [
    [S.norad, String(s.norad_id)],
    [S.objType, s.object_type_label ? (T.satellite.typeLabels[s.object_type_label] ?? s.object_type_label) : s.object_type || "—"],
    [S.owner, s.owner_name || "—"],
    [S.launch, s.launch_date || "—"],
    [S.intlDes, s.international_designator || "—"],
  ];
  const orbit: Array<[string, string]> = [
    [S.orbitClass, geo ? "GEO" : s.orbit_class || "—"],
    [S.inclination, s.inclination_deg != null ? `${s.inclination_deg.toFixed(1)}°` : "—"],
    [S.period, s.period_min != null ? `${Math.round(s.period_min)} ${T.satellite.min}` : "—"],
    [S.apogee, s.apogee_km != null ? `${Math.round(s.apogee_km)} ${S.km}` : "—"],
    [S.perigee, s.perigee_km != null ? `${Math.round(s.perigee_km)} ${S.km}` : "—"],
  ];
  // UI-3: was built inline inside the JSX; lifted to a variable so FieldGrid can
  // take it. Same filter, same order — only the cells with a value are shown.
  const strategic = (
    [
      [S.users, s.users],
      [S.purpose, s.purpose],
      [S.operatorCountry, s.operator_country],
    ] as Array<[string, string | null]>
  ).filter((c): c is [string, string] => Boolean(c[1]));
  const hasStrategic = strategic.length > 0;

  return (
    <div className="strip" aria-live="polite">
      <div className="strip-head">
        <span className="cs">{s.object_name || `#${s.norad_id}`}</span>
        <span className="icao">#{s.norad_id}</span>
      </div>

      {/* vintage badges — DISTINCT: strategic is frozen-2023 and visibly set apart */}
      <div className="vintage-row" aria-label={S.vintages}>
        {s.identity_vintage && <span className="badge v-badge v-current">{S.vIdentity(s.identity_vintage)}</span>}
        {s.orbit_vintage && <span className="badge v-badge v-live">{S.vOrbitLive}</span>}
        {s.strategic_vintage && <span className="badge v-badge v-frozen">{S.vStrategic(s.strategic_vintage)}</span>}
      </div>

      <div className="section-label">{S.identitySection}</div>
      <FieldGrid cells={identity} />

      <div className="section-label">{S.orbitSection}</div>
      <FieldGrid cells={orbit} />

      {hasStrategic && (
        <>
          <div className="section-label v-frozen-label">{S.strategicSection}</div>
          <FieldGrid cells={strategic} />
        </>
      )}

      {/* live sub-point (SGP4, 1 Hz) + AOI passes */}
      <div className="detail-line">
        <span className="k">{S.live}</span>
        <span className="v mono">
          {fix ? `${fix.lat.toFixed(2)}, ${fix.lon.toFixed(2)} · ${Math.round(fix.altKm)} ${S.km}` : "—"}
        </span>
      </div>
      <div className="detail-line">
        <span className="k">AOI</span>
        <span className="v">
          {s.n_passes > 0 ? (s.is_persistent ? S.persistent : S.passes(s.n_passes)) : S.noPasses}
        </span>
      </div>
      {s.n_passes > 0 && s.pass_window_start && s.pass_window_end && (
        <div className="filter-note">{S.passWindow(s.pass_window_start.slice(0, 10), s.pass_window_end.slice(0, 10))}</div>
      )}

      {/* ground-track expansion — COMPUTED (SGP4), not a stored-fix pull */}
      <div className="track-controls">
        <button aria-pressed={expanded} disabled={!sat} onClick={onToggleTrack}>
          {expanded ? S.hideTrack : S.showTrack}
        </button>
      </div>
      {expanded && (
        <div className="track-stats">
          {!sat ? (
            <div className="filter-note">{S.noTle}</div>
          ) : geo ? (
            <div className="filter-note">{S.trackGeo(fix ? fix.lon.toFixed(1) : "?")}</div>
          ) : (
            <div className="filter-note">{S.trackLeo(s.period_min != null ? Math.round(s.period_min) : 0)}</div>
          )}
        </div>
      )}

      <div className="strip-warn">{S.warn}</div>
      <div className="strip-foot">
        <span className="filter-note">SGP4 · @1Hz</span>
        <button onClick={onClear}>{T.entitySearch.clear}</button>
      </div>
    </div>
  );
}
