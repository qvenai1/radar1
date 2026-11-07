/**
 * Selected-satellite detail — parallel to Flight/VesselStrip. Self-propagates
 * the selected satellite once per second via satellite.js (one satrec, cheap)
 * so the readout shows LIVE altitude / velocity / sub-point, alongside static
 * orbital params (inclination, period) and the TLE-age accuracy caveat.
 */

import { useEffect, useMemo, useState } from "react";
import type { SatelliteElement, SatelliteEnrichment } from "../api/types";
import { makeSatRec, propagate } from "../lib/orbit";
import type { SatFix } from "../lib/orbit";
import { T } from "../lib/strings";
import FieldGrid from "./FieldGrid";

interface Props {
  element: SatelliteElement | null;
  enrichment?: SatelliteEnrichment | null;
  onClear: () => void;
}

function ageText(epoch: string | null): string {
  if (!epoch) return "—";
  const h = (Date.now() - Date.parse(epoch)) / 3_600_000;
  if (!Number.isFinite(h)) return "—";
  return h < 48 ? `${Math.round(h)} س` : `${(h / 24).toFixed(1)} يوم`;
}

export default function SatelliteStrip({ element, enrichment, onClear }: Props) {
  const sat = useMemo(() => (element ? makeSatRec(element) : null), [element]);
  const [fix, setFix] = useState<SatFix | null>(null);

  useEffect(() => {
    if (!sat) {
      setFix(null);
      return;
    }
    const tick = () => setFix(propagate(sat, new Date()));
    tick();
    const t = window.setInterval(tick, 1000);
    return () => window.clearInterval(t);
  }, [sat]);

  if (!element) {
    return (
      <div className="strip" aria-live="polite">
        <div className="empty">{T.satellite.stripEmpty}</div>
      </div>
    );
  }

  const cells: Array<[string, string]> = [
    [T.satellite.norad, String(element.norad_id)],
    [T.satellite.altitude, fix ? `${Math.round(fix.altKm)} ${T.satellite.km}` : T.satellite.computing],
    [T.satellite.velocity, fix?.velKmS != null ? `${fix.velKmS.toFixed(2)} ${T.satellite.kms}` : "—"],
    [T.satellite.inclination, sat?.inclDeg != null ? `${sat.inclDeg.toFixed(1)}°` : "—"],
    [T.satellite.period, sat?.periodMin != null ? `${Math.round(sat.periodMin)} ${T.satellite.min}` : "—"],
    [T.satellite.epoch, ageText(element.epoch)],
    [T.satellite.position, fix ? `${fix.lat.toFixed(2)}, ${fix.lon.toFixed(2)}` : "—"],
  ];

  // SATCAT identity (S1) — only the facts that are actually known; empty fields
  // are dropped rather than shown as blanks (project-wide "nulls, not zeros").
  const e = enrichment && enrichment.norad_id === element.norad_id ? enrichment : null;
  const status = e?.decay_date
    ? T.satellite.decayed(e.decay_date)
    : (e?.ops_status
        ? T.satellite.opsStatus[e.ops_status] ?? `#${e.ops_status}`
        : null);
  const objType = e?.object_type_label
    ? T.satellite.typeLabels[e.object_type_label] ?? e.object_type_label
    : e?.object_type || null;
  const identity: Array<[string, string | null]> = [
    [T.satellite.country, e ? e.owner_name || e.owner : null],
    [T.satellite.objectType, objType],
    [T.satellite.launchDate, e?.launch_date || null],
    [T.satellite.launchSite, e?.launch_site || null],
    [T.satellite.intlDes, e?.international_designator || null],
    [T.satellite.status, status],
  ];
  const identityCells = identity.filter((c): c is [string, string] => Boolean(c[1]));

  return (
    <div className="strip" aria-live="polite">
      <div className="strip-head">
        <span className="cs">{element.name}</span>
        <span className="icao">#{element.norad_id}</span>
      </div>
      <FieldGrid cells={cells} />
      {identityCells.length > 0 && (
        <FieldGrid cells={identityCells} />
      )}
      {e?.satcat_snapshot_date && (
        <div className="strip-warn">{T.satellite.satcatProvenance(e.satcat_snapshot_date)}</div>
      )}
      <div className="strip-foot">
        <span>SGP4 محلي · @1Hz</span>
        <button onClick={onClear}>{T.strip.clear}</button>
      </div>
      <div className="strip-warn">{T.satellite.warn}</div>
    </div>
  );
}
