/**
 * A3 — identity changes. A self-contained panel for the maritime tab that lists
 * vessels broadcasting a different name/flag/MMSI over time (a potential
 * disguise). `reflag` = same IMO (the permanent hull anchor) under a new
 * MMSI+flag — the shadow-fleet signature; `rename`/`retype` = a confirmed
 * name/type change under one MMSI.
 *
 * Behavioural analysis, never proof: a rename/re-flag is USUALLY LEGAL, so each
 * row is a DOCUMENTED CHANGE FOR INVESTIGATION — not proven evasion. The honest
 * caveat (from the response, or an Arabic paraphrase) is always shown.
 * suspicion_rank PRIORITISES review order (high→low); `correlated` flags a hull
 * that also shows an A1 gap / A2 rendezvous — the intel multiplier. Fetches once
 * on mount; loading/error/empty all degrade to a compact status line — it never
 * throws.
 */

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { IdentityChangeEvent, IdentityChangesResponse } from "../api/types";
import Caveat from "./Caveat";
import { useCollapsed } from "../lib/collapse";
import { T } from "../lib/strings";

/** reflag → "name (flag)"; rename/retype → the raw old value. */
function oldIdentity(e: IdentityChangeEvent): string {
  if (e.change_type === "reflag") {
    return e.old_flag ? `${e.old_name || "—"} (${e.old_flag})` : e.old_name || e.old_value || "—";
  }
  return e.old_value || "—";
}

function newIdentity(e: IdentityChangeEvent): string {
  if (e.change_type === "reflag") {
    return e.new_flag ? `${e.new_name || "—"} (${e.new_flag})` : e.new_name || e.new_value || "—";
  }
  return e.new_value || "—";
}

/** reflag carries an MMSI change; note it under the transition. */
function mmsiNote(e: IdentityChangeEvent): string | null {
  if (e.change_type === "reflag" && e.old_mmsi && e.new_mmsi) {
    return T.vessel.idMmsi(e.old_mmsi, e.new_mmsi);
  }
  return null;
}

export default function IdentityChanges() {
  const [data, setData] = useState<IdentityChangesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [collapsed, toggle] = useCollapsed("identity");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(false);
    api
      .maritimeIdentityChanges({ rank: "all", limit: 50 })
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
    body = <div className="status">{T.vessel.idLoading}</div>;
  } else if (error || !data || !data.available) {
    body = <div className="status">{T.vessel.idError}</div>;
  } else if (events.length === 0) {
    body = <div className="status">{T.vessel.idEmpty}</div>;
  } else {
    body = (
      <div className="contact-list table-wrap" role="region" aria-label={T.vessel.idAria}>
        <table className="table">
          <thead>
            <tr>
              <th>{T.vessel.idColIdentity}</th>
              <th className="r">{T.vessel.idColAnchor}</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => {
              const note = mmsiNote(e);
              return (
                <tr key={`${e.mmsi}-${e.first_new_ts}-${i}`}>
                  <td className="v-name">
                    <span className={`badge rank-badge rank-${e.suspicion_rank}`}>
                      {T.vessel.rvRank[e.suspicion_rank] ?? e.suspicion_rank}
                    </span>
                    <span className="change-tag">
                      {T.vessel.idTypes[e.change_type] ?? e.change_type}
                    </span>
                    <div className="id-transition">
                      <span className="id-old">{oldIdentity(e)}</span>
                      <span className="id-arrow">{T.vessel.idArrow}</span>
                      <span className="id-new">{newIdentity(e)}</span>
                    </div>
                    {note ? <div className="sub">{note}</div> : null}
                    {e.correlated ? (
                      <div className="correlated">⚑ {T.vessel.idCorrelated}</div>
                    ) : null}
                  </td>
                  <td className="r">{e.imo ? `IMO ${e.imo}` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="gap-candidates identity-changes">
      <button className="section-label collapse-head" aria-expanded={!collapsed} onClick={toggle}>
        <span>
          {T.vessel.idSection}
          {events.length ? ` · ${T.vessel.idCount(events.length)}` : ""}
        </span>
        <span className="chev" aria-hidden="true" />
      </button>
      {!collapsed && (
        <>
          <Caveat k="identity" response={data?.caveat} />
          {body}
        </>
      )}
    </div>
  );
}
