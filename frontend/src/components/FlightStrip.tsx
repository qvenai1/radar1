/**
 * The signature element: a flight-progress strip for the selected contact.
 * Segmented boxes like a controller's strip bay — electronic in the dark
 * theme, paper strip in the light theme (styling in app.css).
 */

import { useState } from "react";
import type { AircraftEnrichment, AirportInfo, StateVector } from "../api/types";
import { MAX_PLAUSIBLE_BARO_M, callsign, coord, deg, fpm, ft, kt, utcClock, utcShort } from "../lib/format";
import { T } from "../lib/strings";
import FieldGrid from "./FieldGrid";

interface Props {
  state: StateVector | null;
  enrichment: AircraftEnrichment | null;
  enrichLoading: boolean;
  sources?: string[]; // which feeds see this aircraft (provenance, not a control)
  onClear: () => void;
}

// air sources are MERGED into one layer; this labels the provenance badge only.
const SRC_LABEL: Record<string, string> = {
  opensky: "OpenSky",
  military: "عسكري",
  adsbexchange: "ADS-B",
};

function Airport({ a }: { a: AirportInfo }) {
  return (
    <div className="ap">
      <div className="code">{a.iata || a.icao || "—"}</div>
      <div className="city">{a.municipality || a.name || a.country || ""}</div>
    </div>
  );
}

export default function FlightStrip({ state, enrichment, enrichLoading, sources = [], onClear }: Props) {
  const [copied, setCopied] = useState(false);
  if (!state) {
    return (
      <div className="strip" aria-live="polite">
        <div className="empty">{T.strip.empty}</div>
      </div>
    );
  }
  const implausibleBaro =
    state.baro_altitude != null && state.baro_altitude > MAX_PLAUSIBLE_BARO_M;
  const copyLink = () => {
    const url = new URL(window.location.href);
    url.searchParams.set("icao", state.icao24);
    void navigator.clipboard?.writeText(url.toString()).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    });
  };
  const cells: Array<[string, string]> = [
    [
      T.strip.baroAlt + (implausibleBaro ? " ⚠" : ""),
      ft(state.baro_altitude),
    ],
    [T.strip.speed, kt(state.velocity)],
    [T.strip.track, deg(state.true_track)],
    [T.strip.vRate, fpm(state.vertical_rate)],
    [T.strip.squawk, state.squawk || "—"],
    [T.strip.geoAlt, ft(state.geo_altitude)],
    [T.strip.position, coord(state.latitude, state.longitude)],
    [T.strip.origin, state.origin_country || "—"],
    [T.strip.lastSeen, utcClock(state.last_contact)],
  ];
  // geo classification (P3) — shown when the row carries it (adsbexchange today)
  if (state.country) cells.push([T.strip.country, state.country]);
  if (state.near_country) cells.push([T.strip.nearBorder, state.near_country]);
  /* ---------- PAYLOAD-PROV ----------
   * ⛔ The two cells above state WHERE the aircraft is and HOW GOOD the fix is. Until now
   * neither said WHO decided. `geo_source` is the classifier behind `country`/`near_country`
   * and `position_quality`; printing the verdict without it breaks «a source and a date for
   * every fact» on the one surface that shows the verdict.
   * ⚠ Each is conditional because each is genuinely optional on the contract (/live omits the
   * whole geo group). An absent value renders NO cell — never «—», which would claim the
   * field was checked and found empty. */
  if (state.geo_source) cells.push([T.strip.geoSource, state.geo_source]);
  if (state.position_source != null) {
    const code = String(state.position_source);
    cells.push([T.strip.posSource, T.strip.posSourceNames[code] ?? `#${code}`]);
  }
  if (state.fetched_at) cells.push([T.strip.fetchedAt, utcShort(state.fetched_at)]);
  if (state.position_quality)
    cells.push([
      T.strip.posQuality,
      T.strip.quality[state.position_quality] ?? state.position_quality,
    ]);
  if (enrichment?.registration) cells.push([T.strip.registration, enrichment.registration]);
  if (enrichment?.aircraft_type) cells.push([T.strip.aircraftType, enrichment.aircraft_type]);
  if (enrichment?.operator) cells.push([T.strip.operator, enrichment.operator]);

  const route = enrichment?.route ?? null;
  const photo = enrichment?.photo ?? null;

  return (
    <div className="strip" aria-live="polite">
      <div className="strip-head">
        <span className="cs">
          {callsign(state.callsign)}
          {sources.map((s) => (
            <span key={s} className={`badge src-badge src-${s}`}>{SRC_LABEL[s] ?? s}</span>
          ))}
        </span>
        <span className="icao">
          {state.icao24.toUpperCase()}
          {state.on_ground ? ` · ${T.strip.gnd}` : ""}
          {state.spi ? " · SPI" : ""}
        </span>
      </div>

      {photo && (
        <div className="strip-photo">
          <a href={photo.page ?? undefined} target="_blank" rel="noreferrer">
            <img
              src={photo.thumb}
              alt={`${callsign(state.callsign)} — ${enrichment?.aircraft_type ?? ""}`}
              loading="lazy"
              onError={(e) => {
                (e.currentTarget.closest(".strip-photo") as HTMLElement).style.display = "none";
              }}
            />
          </a>
          {photo.photographer && (
            <div className="credit">
              {T.strip.photoBy}: {photo.photographer} ·{" "}
              <a href={photo.page ?? "#"} target="_blank" rel="noreferrer">Planespotters</a>
            </div>
          )}
        </div>
      )}

      {route && (
        <div className="strip-route" title={T.strip.routeNote}>
          <Airport a={route.origin} />
          <span className="arrow" aria-hidden="true">✈</span>
          <Airport a={route.destination} />
        </div>
      )}
      {!enrichment && enrichLoading && (
        <div className="strip-loading">{T.strip.loadingEnrich}</div>
      )}

      <FieldGrid
        cells={cells}
        cellTitle={(k) => (k.endsWith("⚠") ? T.strip.implausible : undefined)}
      />
      <div className="strip-foot">
        <span>{T.strip.stripWord} · {utcClock(state.snapshot_time)}</span>
        <span>
          <button onClick={copyLink}>{copied ? T.strip.copied : T.strip.copyLink}</button>{" "}
          <button onClick={onClear}>{T.strip.clear}</button>
        </span>
      </div>
    </div>
  );
}
