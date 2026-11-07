/**
 * Airport search detail (E4) — the ONE branch whose detail is a nested LIST (the
 * landing candidates), not a single-entity card. This is why the dashboard frame
 * was built first: the airport is one-to-many (airport → the aircraft that landed
 * there). Selecting a candidate drills to that aircraft's track (reusing E2's
 * /history + aircraftTrail map layer). Candidates are leads, not proven landings;
 * a sparse/empty result at a low-coverage field is correct and is said so.
 */

import { useEffect, useRef } from "react";
import type { AirportSummary, LandingCandidate } from "../api/types";
import { utcShort } from "../lib/format";
import { T } from "../lib/strings";

interface Props {
  airport: AirportSummary | null;
  candidates: LandingCandidate[];
  loading: boolean;
  windowHours: number;
  caveat: string;
  landingSelected: string | null; // icao24 of the drilled aircraft
  onSelectLanding: (icao24: string | null) => void;
}

export default function AirportSearchDetail({
  airport, candidates, loading, windowHours, caveat, landingSelected, onSelectLanding,
}: Props) {
  const A = T.entitySearch.air4;
  const bodyRef = useRef<HTMLTableSectionElement>(null);

  useEffect(() => {
    if (!landingSelected || !bodyRef.current) return;
    bodyRef.current
      .querySelector<HTMLTableRowElement>(`tr[data-icao="${landingSelected}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [landingSelected]);

  if (!airport) {
    return (
      <div className="strip" aria-live="polite">
        <div className="empty">{T.entitySearch.detailEmpty}</div>
      </div>
    );
  }

  return (
    <div className="strip" aria-live="polite">
      <div className="strip-head">
        <span className="cs">{airport.name || airport.icao}</span>
        <span className="icao mono">{airport.icao}{airport.iata ? ` / ${airport.iata}` : ""}</span>
      </div>
      <div className="detail-line">
        <span className="k">{A.colWhere}</span>
        <span className="v">{airport.municipality || "—"}{airport.country ? ` · ${airport.country}` : ""}</span>
      </div>
      <div className="detail-line">
        <span className="k">{A.elevation}</span>
        <span className="v mono">{airport.elevation_ft != null ? `${airport.elevation_ft} ${A.ft}` : "—"}</span>
      </div>

      <div className="section-label">{A.landingsSection(candidates.length)}</div>
      <div className="filter-note">{A.windowNote(windowHours)} · {A.trackNote}</div>

      {loading ? (
        <div className="filter-note">{T.entitySearch.loading}</div>
      ) : candidates.length === 0 ? (
        <div className="filter-note" style={{ lineHeight: 1.6 }}>{A.landingsEmpty}</div>
      ) : (
        <div className="contact-list table-wrap" role="region" aria-label={A.landingsSection(candidates.length)}>
          <table className="table">
            <tbody ref={bodyRef}>
              {candidates.map((c) => (
                <tr
                  key={c.icao24 + c.landing_ts}
                  data-icao={c.icao24}
                  aria-selected={c.icao24 === landingSelected}
                  tabIndex={0}
                  onClick={() => onSelectLanding(c.icao24 === landingSelected ? null : c.icao24)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelectLanding(c.icao24 === landingSelected ? null : c.icao24);
                    }
                  }}
                >
                  <td className="v-name mono">{c.callsign || c.registration || `#${c.icao24}`}</td>
                  <td>{utcShort(c.landing_ts)}</td>
                  <td className="r">
                    <span
                      className={`pat-chip ${c.signal === "approach" ? "sig-approach" : "sig-grounded"}`}
                      title={c.signal === "approach" ? A.signalApproachHint : A.signalGroundedHint}
                    >
                      {c.signal === "approach" ? A.signalApproach : A.signalGrounded}
                      {c.signal === "approach" && c.min_altitude_m != null ? ` · ${c.min_altitude_m}${A.m}` : ""}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="strip-warn">{caveat || A.caveat}</div>
    </div>
  );
}
