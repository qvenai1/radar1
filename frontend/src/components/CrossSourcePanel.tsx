/**
 * AIS × MarineTraffic cross-source panel for the maritime tab — VISUAL.
 *
 * Two independent maritime sources now cover the AOI; this shows, at a glance,
 * how much they AGREE. A proportion bar splits MarineTraffic's fleet into
 * "matched to an AIS vessel" (cyan — the AIS source hue) vs "MarineTraffic only"
 * (amber — the MarineTraffic hue), so the bar literally reads as coverage
 * corroboration. A second thin bar splits the matches by confidence. A
 * status-colored callout surfaces dimension discrepancies (a possible-spoof /
 * data-quality signal). Below, the linked pairs with evidence.
 *
 * Links are DERIVED (MarineTraffic carries no MMSI) — a LEAD, not proof; the
 * honest caveat is always shown. Fetches once on mount; loading/empty/error
 * degrade to a status line.
 */

import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CrossMatch, CrossrefResponse } from "../api/types";
import Caveat from "./Caveat";
import { useCollapsed } from "../lib/collapse";
import { T } from "../lib/strings";

function evidence(m: CrossMatch): string {
  const bits: string[] = [];
  if (m.distance_km != null) bits.push(`Δ${m.distance_km.toFixed(m.distance_km < 10 ? 1 : 0)}km`);
  if (m.dt_minutes != null) bits.push(`${(m.dt_minutes / 60).toFixed(1)}h`);
  if (m.flag) bits.push(m.flag);
  return bits.join(" · ");
}

/** A proportion bar: flex segments sized by value, 2px surface gap between them
 *  (the dataviz spacer). Counts live in the legend, not on the color. */
function Bar({ segs }: { segs: { cls: string; value: number }[] }) {
  const total = segs.reduce((s, x) => s + x.value, 0) || 1;
  return (
    <div className="xref-bar" role="img"
         aria-label={segs.map((s) => `${s.cls} ${s.value}`).join(", ")}>
      {segs.filter((s) => s.value > 0).map((s) => (
        <div key={s.cls} className={`xseg ${s.cls}`}
             style={{ flexGrow: s.value, flexBasis: `${(s.value / total) * 100}%` }} />
      ))}
    </div>
  );
}

export default function CrossSourcePanel() {
  const [data, setData] = useState<CrossrefResponse | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "empty" | "error">("loading");
  const [collapsed, toggle] = useCollapsed("crossref");

  useEffect(() => {
    let cancelled = false;
    api
      .maritimeCrossref({ limit: 200 })
      .then((r) => {
        if (cancelled) return;
        setData(r);
        setState(r.available && r.matches.length ? "ok" : "empty");
      })
      .catch(() => !cancelled && setState("error"));
    return () => {
      cancelled = true;
    };
  }, []);

  const C = T.vessel.crossref;
  // one narrowing point: `ok` holds the response only when it is renderable, so the
  // body below needs no further null checks
  const ok = state === "ok" && data?.summary ? data : null;
  const s = ok?.summary ?? null;
  // this panel's headline figure IS the corroborated share, so that is what its
  // collapsed header carries (the other sections carry a row count)
  const pct = s ? Math.round((s.matched / Math.max(1, s.mt_vessels)) * 100) : null;

  const head = (
    <button className="section-label collapse-head" aria-expanded={!collapsed} onClick={toggle}>
      <span>
        {C.title}
        {pct != null ? ` · ${pct}%` : ""}
      </span>
      <span className="chev" aria-hidden="true" />
    </button>
  );

  if (!ok || !s) {
    return (
      <div className="xref-panel">
        {head}
        {!collapsed && (
          <div className="filter-note">{state === "loading" ? C.loading : C.empty}</div>
        )}
      </div>
    );
  }

  if (collapsed) return <div className="xref-panel">{head}</div>;

  return (
    <div className="xref-panel">
      {head}

      {/* headline: share of MarineTraffic corroborated by AIS */}
      <div className="xref-head">
        <span className="xref-pct">{pct}%</span>
        <span className="xref-pct-l">{C.corroborated}</span>
      </div>

      {/* coverage: matched (also on AIS) vs MarineTraffic-only */}
      <Bar segs={[{ cls: "matched", value: s.matched }, { cls: "mtonly", value: s.mt_only }]} />
      <div className="xref-legend">
        <span><i className="sw matched" /> {C.matched} <b>{s.matched}</b></span>
        <span><i className="sw mtonly" /> {C.mtOnly} <b>{s.mt_only}</b></span>
      </div>

      {/* confidence split of the matches */}
      <div className="xref-sub">{C.confidence}</div>
      <Bar segs={[{ cls: "strong", value: s.strong }, { cls: "probable", value: s.probable },
                  { cls: "weak", value: s.weak }]} />
      <div className="xref-legend">
        <span><i className="sw strong" /> {C.strong} <b>{s.strong}</b></span>
        {s.probable > 0 && <span><i className="sw probable" /> {C.probable} <b>{s.probable}</b></span>}
        {s.weak > 0 && <span><i className="sw weak" /> {C.weak} <b>{s.weak}</b></span>}
      </div>

      {/* discrepancy — a reserved STATUS colour + icon + label (never colour alone) */}
      {s.discrepancies > 0 && (
        <div className="xref-disc" role="note">
          <span className="ic" aria-hidden="true">⚠</span>
          <span><b>{s.discrepancies}</b> {C.discrepancyBanner}</span>
        </div>
      )}

      {/* the linked pairs */}
      <div className="xref-list" role="list">
        {ok.matches.map((m) => (
          <div className="xref-row" role="listitem" key={m.ship_id}>
            <i className={`sw dot c-${m.confidence ?? "feed"}`} aria-hidden="true" />
            <span className="xref-name">{m.name || `#${m.ship_id}`}</span>
            <span className="xref-mmsi" dir="ltr">{m.mmsi}</span>
            {m.discrepancy && <span className="xref-flag" title={m.reason ?? ""}>{C.discrepancyTag}</span>}
            {m.ambiguous && <span className="xref-flag amb">{C.ambiguousTag}</span>}
            <span className="xref-ev" dir="ltr">{evidence(m)}</span>
          </div>
        ))}
      </div>

      <Caveat k="crossref" response={ok.caveat} className="strip-warn" />
    </div>
  );
}
