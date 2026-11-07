/**
 * A2 — rendezvous candidates. A self-contained panel for the maritime tab that
 * lists PAIRS of vessels which loitered close together (≤500 m), slow, for ≥1h
 * in open sea away from ports — a potential ship-to-ship transfer.
 *
 * Behavioural analysis, never proof: each row is an INVESTIGATION LEAD (most are
 * legitimate). suspicion_rank PRIORITISES review order (high→low); it does NOT
 * accuse. The honest caveat (from the response, or an Arabic paraphrase) is
 * always shown. Fetches once on mount; loading/error/empty all degrade to a
 * compact status line — it never throws.
 */

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { RendezvousEvent, RendezvousResponse, RendezvousVessel } from "../api/types";
import Caveat from "./Caveat";
import { useCollapsed } from "../lib/collapse";
import { T } from "../lib/strings";

function typeText(v: RendezvousVessel): string {
  if (!v.ship_type) return "—";
  return T.vessel.types[v.ship_type] ?? v.ship_type;
}

function vesselName(v: RendezvousVessel): string {
  return v.name || `#${v.mmsi}`;
}

function vesselSub(v: RendezvousVessel): string {
  return [v.flag_country, typeText(v), v.imo ? `IMO ${v.imo}` : null]
    .filter(Boolean)
    .join(" · ");
}

/** duration_min → minutes, or hours past 60; still-together pairs have no end. */
function duration(e: RendezvousEvent): string {
  if (e.duration_min != null) {
    return e.duration_min >= 60
      ? T.vessel.rvHours((e.duration_min / 60).toFixed(1))
      : T.vessel.rvMinutes(Math.round(e.duration_min).toString());
  }
  if (e.end_ts == null) return T.vessel.rvStillTogether;
  return "—";
}

function distance(e: RendezvousEvent): string {
  return e.min_distance_m != null ? T.vessel.rvMeters(Math.round(e.min_distance_m).toString()) : "—";
}

function offshore(e: RendezvousEvent): string {
  return e.offshore_km != null ? T.vessel.rvKm(e.offshore_km.toFixed(0)) : "—";
}

function waters(e: RendezvousEvent): string {
  return e.start_country || e.start_near_country || "—";
}

export default function Rendezvous() {
  const [data, setData] = useState<RendezvousResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [collapsed, toggle] = useCollapsed("rendezvous");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(false);
    api
      .maritimeRendezvous({ rank: "all", min_minutes: 60, limit: 50 })
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
    body = <div className="status">{T.vessel.rvLoading}</div>;
  } else if (error || !data || !data.available) {
    body = <div className="status">{T.vessel.rvError}</div>;
  } else if (events.length === 0) {
    body = <div className="status">{T.vessel.rvEmpty}</div>;
  } else {
    body = (
      <div className="contact-list table-wrap" role="region" aria-label={T.vessel.rvAria}>
        <table className="table">
          <thead>
            <tr>
              <th>{T.vessel.colName}</th>
              <th>{T.vessel.rvWaters}</th>
              <th className="r">{T.vessel.rvDistance}</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={`${e.primary.mmsi}-${e.partner.mmsi}-${e.start_ts}-${i}`}>
                <td className="v-name">
                  <span className={`badge rank-badge rank-${e.suspicion_rank}`}>
                    {T.vessel.rvRank[e.suspicion_rank] ?? e.suspicion_rank}
                  </span>
                  {vesselName(e.primary)}
                  <div className="sub">{vesselSub(e.primary)}</div>
                  <div className="pair-line">{T.vessel.rvWith} {vesselName(e.partner)}</div>
                  <div className="sub">{vesselSub(e.partner)}</div>
                </td>
                <td>{waters(e)}</td>
                <td className="r">
                  {distance(e)}
                  <div className="sub">
                    {[duration(e), offshore(e)].filter((x) => x !== "—").join(" · ") || "—"}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="gap-candidates rendezvous">
      <button className="section-label collapse-head" aria-expanded={!collapsed} onClick={toggle}>
        <span>
          {T.vessel.rvSection}
          {events.length ? ` · ${T.vessel.rvCount(events.length)}` : ""}
        </span>
        <span className="chev" aria-hidden="true" />
      </button>
      {!collapsed && (
        <>
          <Caveat k="rendezvous" response={data?.caveat} />
          {body}
        </>
      )}
    </div>
  );
}
