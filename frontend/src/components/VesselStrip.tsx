/**
 * Selected-vessel detail panel for the maritime tab — the sea-going parallel
 * of FlightStrip. Shows a best-effort Commons photo (sparse coverage;
 * degrades silently), identity + navigation cells, and the standing AIS
 * caveat. Photo attribution (photographer + Commons link + license) is a
 * term of use and is always shown when a photo is present.
 */

import type { PhotoInfo, Vessel } from "../api/types";
import { coord, isStale, isoAge, utcShort } from "../lib/format";
import { T } from "../lib/strings";
import FieldGrid from "./FieldGrid";

interface Props {
  vessel: Vessel | null;
  photo: PhotoInfo | null;
  license: string | null;
  photoLoading: boolean;
  onClear: () => void;
}

const NAV: Record<number, string> = {
  0: "تسير بمحرّك", 1: "راسية (مرساة)", 2: "غير متحكَّم بها", 3: "مناورة محدودة",
  4: "مقيّدة بالغاطس", 5: "مربوطة (رصيف)", 6: "جانحة", 7: "تصطاد", 8: "تسير بالشراع",
};

export default function VesselStrip({ vessel, photo, license, photoLoading, onClear }: Props) {
  if (!vessel) {
    return (
      <div className="strip" aria-live="polite">
        <div className="empty">{T.vessel.stripEmpty}</div>
      </div>
    );
  }
  const v = vessel;
  const mt = v.source === "marinetraffic";
  const stale = isStale(v.timestamp);
  const age = isoAge(v.timestamp);
  const typeText = v.ship_type ? T.vessel.types[v.ship_type] ?? v.ship_type : "—";
  // Identity cell is source-shaped: AIS shows MMSI, MarineTraffic shows its ship_id.
  const idCell: [string, string] = v.mmsi
    ? ["MMSI", v.mmsi]
    : [T.vessel.shipId, v.ship_id ? `#${v.ship_id}` : "—"];
  const cells: Array<[string, string]> = [
    idCell,
    ["IMO", v.imo || "—"],
    [T.vessel.callsign, v.callsign || "—"],
    [T.vessel.type, typeText],
    ...(v.flag ? ([[T.vessel.flag, v.flag]] as Array<[string, string]>) : []),
    [T.vessel.speed, v.speed == null ? "—" : `${v.speed.toFixed(1)} ${T.vessel.kn}`],
    [T.vessel.course, v.course == null ? "—" : `${Math.round(v.course)}°`],
    [T.vessel.heading, v.heading == null ? "—" : `${Math.round(v.heading)}°`],
    [T.vessel.draught, v.draught == null ? "—" : `${v.draught.toFixed(1)} m`],
    [T.vessel.status, v.nav_status != null && NAV[v.nav_status] ? NAV[v.nav_status] : "—"],
    /* ---------- PAYLOAD-PROV ----------
     * ⛔ POSITION. `lat`/`lon` arrive on this contract and were never rendered, while the
     * aircraft card shows its coordinates. That asymmetry is not cosmetic here: `last_known`
     * claims "this is where it WAS", and a claim about a place that never shows the place is
     * an empty claim. Uses the same `coord()` the air card uses, so the two read alike. */
    [T.vessel.position, coord(v.lat, v.lon)],
    /* ⛔ SOURCE, FOR EVERY VESSEL. This was an «MT» badge for MarineTraffic only, so AIS rows
     * — the majority — showed numbers with no feed named at all. An unmapped source falls
     * through verbatim rather than being hidden: a name we do not recognise is still a name. */
    [T.vessel.sourceLabel, T.vessel.sourceNames[v.source] ?? v.source ?? "—"],
    [T.vessel.dataOrigin, v.data_origin || "—"],
  ];
  return (
    <div className="strip" aria-live="polite">
      <div className="strip-head">
        <span className="cs">
          {v.name || T.vessel.noName}
          {mt && <span className="badge src-badge src-mt" title={T.vessel.sourceMt}>MT</span>}
        </span>
        <span className="icao">{v.destination ? `→ ${v.destination}` : ""}</span>
      </div>

      {photo ? (
        <div className="strip-photo">
          <a href={photo.page ?? undefined} target="_blank" rel="noreferrer">
            <img
              src={photo.thumb}
              alt={v.name || v.mmsi || v.ship_id || ""}
              loading="lazy"
              onError={(e) => {
                (e.currentTarget.closest(".strip-photo") as HTMLElement).style.display = "none";
              }}
            />
          </a>
          {photo.photographer && (
            <div className="credit">
              {T.strip.photoBy}: {photo.photographer}
              {license ? ` · ${license}` : ""} ·{" "}
              <a href={photo.page ?? "#"} target="_blank" rel="noreferrer">Wikimedia Commons</a>
            </div>
          )}
        </div>
      ) : photoLoading ? (
        <div className="strip-loading">{T.vessel.photoLoading}</div>
      ) : null}

      <FieldGrid cells={cells} />
      <div className="strip-foot">
        <span>
          {T.vessel.lastFix} {v.timestamp ? utcShort(v.timestamp) : "—"}
          {/* UI-FRESH: the detail card is where an analyst decides to act on a position,
              so its age is stated here too — not only in the list. */}
          {stale && (
            <span className="stale-tag" title={T.vessel.staleTitle(age)}>
              {age}
            </span>
          )}
        </span>
        <button onClick={onClear}>{T.strip.clear}</button>
      </div>
      <div className="strip-warn">{T.vessel.warn}</div>
    </div>
  );
}
