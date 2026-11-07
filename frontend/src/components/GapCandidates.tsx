/**
 * A1 — AIS gap candidates. A self-contained panel for the maritime tab that
 * lists underway vessels which STOPPED transmitting AIS for ≥1h inside good
 * coverage, not explained by a receiver/feed outage.
 *
 * Behavioural analysis, never proof: each row is an INVESTIGATION LEAD, so the
 * honest caveat (from the response, or an Arabic paraphrase) is always shown.
 * Fetches once on mount; loading/error/empty all degrade to a compact status
 * line — it never throws.
 */

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { GapEvent, GapsResponse } from "../api/types";
import Caveat from "./Caveat";
import { useCollapsed } from "../lib/collapse";
import { T } from "../lib/strings";

function typeText(g: GapEvent): string {
  if (!g.ship_type) return "—";
  return T.vessel.types[g.ship_type] ?? g.ship_type;
}

/** duration_s → hours; still-transmitting-silent gaps have no end yet. */
function gapDuration(g: GapEvent): string {
  if (g.duration_s != null) return T.vessel.gapsHours((g.duration_s / 3600).toFixed(1));
  if (g.end_ts == null) return T.vessel.gapsStillDark;
  return "—";
}

function waters(g: GapEvent): string {
  return g.start_country || g.start_near_country || "—";
}

export default function GapCandidates() {
  const [data, setData] = useState<GapsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [collapsed, toggle] = useCollapsed("gaps");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(false);
    api
      .maritimeGaps({ classification: "candidate", min_hours: 6, limit: 50 })
      .then((res) => {
        if (alive) setData(res);
      })
      .catch(() => {
        if (alive) setError(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const events = data?.available ? data.events : [];

  let body;
  if (loading) {
    body = <div className="status">{T.vessel.gapsLoading}</div>;
  } else if (error || !data || !data.available) {
    body = <div className="status">{T.vessel.gapsError}</div>;
  } else if (events.length === 0) {
    body = <div className="status">{T.vessel.gapsEmpty}</div>;
  } else {
    body = (
      <div className="contact-list table-wrap" role="region" aria-label={T.vessel.gapsAria}>
        <table className="table">
          <thead>
            <tr>
              <th>{T.vessel.colName}</th>
              <th>{T.vessel.gapsWaters}</th>
              <th className="r">{T.vessel.lastFix}</th>
            </tr>
          </thead>
          <tbody>
            {events.map((g, i) => (
              <tr key={`${g.mmsi}-${g.start_ts}-${i}`}>
                <td className="v-name">
                  {g.name || `#${g.mmsi}`}
                  <div className="sub">
                    {[g.flag_country, typeText(g), g.imo ? `IMO ${g.imo}` : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </td>
                <td>{waters(g)}</td>
                <td className="r">{gapDuration(g)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="gap-candidates">
      <button className="section-label collapse-head" aria-expanded={!collapsed} onClick={toggle}>
        <span>
          {T.vessel.gapsSection}
          {events.length ? ` · ${T.vessel.gapsCount(events.length)}` : ""}
        </span>
        <span className="chev" aria-hidden="true" />
      </button>
      {!collapsed && (
        <>
          <Caveat k="gaps" response={data?.caveat} />
          {body}
        </>
      )}
    </div>
  );
}
