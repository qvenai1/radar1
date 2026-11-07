/**
 * CAPSTONE — hull composite (cross-pattern intelligence). A self-contained
 * panel for the maritime tab that aggregates the three behavioural patterns
 * (A1 gap / A2 rendezvous / A3 identity change) PER HULL, STITCHED BY IMO across
 * a vessel's identities — so a hull that went dark AND met another AND changed
 * identity appears as ONE picture, not three disconnected leads.
 *
 * Behavioural analysis, never proof: composite_rank PRIORITISES review order
 * (multi-pattern + grey/sanctions → high); it does NOT accuse. A composite of
 * leads is a STRONGER lead, still NOT proof — the compounding caveat (from the
 * response, or an Arabic paraphrase) is always shown. Each hull row is
 * EXPANDABLE (local state, no extra fetch) to reveal its stitched identities and
 * its cross-pattern timeline. Fetches once on mount; loading/error/empty all
 * degrade to a compact status line — it never throws.
 */

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CompositeHull, CompositeResponse } from "../api/types";
import Caveat from "./Caveat";
import { useCollapsed } from "../lib/collapse";
import { T } from "../lib/strings";

/** display name = first identity's name, else the raw hull key. */
function hullName(h: CompositeHull): string {
  return h.identities[0]?.name || `#${h.hull}`;
}

function hullFlag(h: CompositeHull): string {
  return h.identities[0]?.flag_country || "—";
}

/** one identity → "NAME (Flag)". */
function identityLabel(name: string | null, flag: string | null): string {
  const n = name || "—";
  return flag ? `${n} (${flag})` : n;
}

/** ISO → compact "MM-DD HH:MMZ" (keeps the date, unlike utcShort). */
function tsLabel(iso: string): string {
  const s = new Date(iso).toISOString();
  return `${s.slice(5, 10)} ${s.slice(11, 16)}Z`;
}

/** event_type → the timeline-dot class suffix. */
function dotClass(t: string): string {
  if (t === "ais_gap") return "tl-gap";
  if (t === "rendezvous") return "tl-rv";
  return "tl-id";
}

export default function HullComposite() {
  const [data, setData] = useState<CompositeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // `toggle` below is the per-hull row expander; this is the whole section's disclosure
  const [collapsed, toggleSection] = useCollapsed("composite");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(false);
    api
      .maritimeComposite({ min_pattern_types: 2, limit: 50 })
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

  const toggle = (hull: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(hull)) next.delete(hull);
      else next.add(hull);
      return next;
    });

  const hulls = data?.available ? data.hulls : [];

  let body;
  if (loading) {
    body = <div className="status">{T.vessel.cxLoading}</div>;
  } else if (error || !data || !data.available) {
    body = <div className="status">{T.vessel.cxError}</div>;
  } else if (hulls.length === 0) {
    body = <div className="status">{T.vessel.cxEmpty}</div>;
  } else {
    body = (
      <div className="contact-list" role="region" aria-label={T.vessel.cxAria}>
        {hulls.map((h) => {
          const open = expanded.has(h.hull);
          // API delivers time order; sort a copy defensively.
          const timeline = [...h.timeline].sort((a, b) => a.ts.localeCompare(b.ts));
          return (
            <div className="hull-row" key={h.hull}>
              <button
                type="button"
                className="hull-head"
                aria-expanded={open}
                onClick={() => toggle(h.hull)}
              >
                <span className={`badge rank-badge rank-${h.composite_rank}`}>
                  {T.vessel.rvRank[h.composite_rank] ?? h.composite_rank}
                </span>
                <span className="hull-name">{hullName(h)}</span>
                <span className="hull-flag">{h.is_imo ? `IMO ${h.hull}` : hullFlag(h)}</span>
                <span className="pattern-dots" aria-hidden="true">
                  <span className={`pd pd-gap${h.n_gaps ? "" : " pd-zero"}`} title={T.vessel.cxGapWord}>
                    {h.n_gaps}
                  </span>
                  <span className={`pd pd-rv${h.n_rendezvous ? "" : " pd-zero"}`} title={T.vessel.cxRvWord}>
                    {h.n_rendezvous}
                  </span>
                  <span
                    className={`pd pd-id${h.n_identity_changes ? "" : " pd-zero"}`}
                    title={T.vessel.cxIdWord}
                  >
                    {h.n_identity_changes}
                  </span>
                </span>
                <span className="patterns-chip">{T.vessel.cxPatterns(h.n_pattern_types)}</span>
                {h.identities.length > 1 ? (
                  <span className="reflag-hint">{T.vessel.cxReflag(h.identities.length)}</span>
                ) : null}
                {h.involves_grey ? <span className="grey-flag">⚑ {T.vessel.cxGrey}</span> : null}
                {h.imo_quality && h.imo_quality !== "valid" ? (
                  <span className="spine-badge">{T.spine[h.imo_quality]}</span>
                ) : null}
                {h.aoi_waters.length ? (
                  <span className="hull-waters">{h.aoi_waters.join("، ")}</span>
                ) : null}
              </button>
              {open ? (
                <div className="hull-detail">
                  <div className="identities">
                    <span className="idn-label">{T.vessel.cxIdentities}: </span>
                    {/* UI-3: this line is MIXED — an Arabic label, then Latin vessel
                        names, then an Arabic note. The container must stay RTL, so only
                        the Latin run is isolated (see .idn-names). */}
                    <span className="idn-names">
                      {h.identities
                        .map((id) => identityLabel(id.name, id.flag_country))
                        .join(" · ")}
                    </span>
                    {!h.is_imo ? <span className="idn-note"> · {T.vessel.cxNoImo}</span> : null}
                  </div>
                  <ol className="timeline">
                    {timeline.map((e, i) => (
                      <li className="tl-entry" key={`${e.event_type}-${e.ts}-${i}`}>
                        <span
                          className={`tl-dot ${dotClass(e.event_type)}`}
                          title={T.vessel.cxEvents[e.event_type] ?? e.event_type}
                        />
                        <span className="tl-ts">{tsLabel(e.ts)}</span>
                        <span className="tl-sep">·</span>
                        <span className="tl-summary">{e.summary}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="gap-candidates hull-composite">
      <button className="section-label collapse-head" aria-expanded={!collapsed} onClick={toggleSection}>
        <span>
          {T.vessel.cxSection}
          {hulls.length ? ` · ${T.vessel.cxCount(hulls.length)}` : ""}
        </span>
        <span className="chev" aria-hidden="true" />
      </button>
      {!collapsed && (
        <>
          <Caveat k="composite" response={data?.caveat} />
          {body}
        </>
      )}
    </div>
  );
}
