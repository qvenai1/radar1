/**
 * AreaPanel (D4) — the area-search MODE content in the dashboard sidebar. NOT a
 * fifth entity (SearchEntity is untouched): a mode alongside entity-search. It
 * drives the draw-a-box interaction, the window + domain filter, and renders the
 * multi-domain crossing list (air/sea/space). Each row is a light discovery hit;
 * selecting it drills to that entity's track on the map (reusing the E1–E3 layers).
 */

import type { AreaSearchResponse } from "../api/types";
import Caveat from "./Caveat";
import ResultCards from "./ResultCards";
import TimeWindowPicker from "./TimeWindowPicker";
import { utcShort } from "../lib/format";
import { T } from "../lib/strings";

export type AreaDomain = "air" | "sea" | "space";

export interface AreaState {
  drawing: boolean;
  onDraw: () => void;
  hasBox: boolean;
  windowH: number;
  onWindow: (h: number) => void;
  air: boolean;
  sea: boolean;
  space: boolean;
  onAir: () => void;
  onSea: () => void;
  onSpace: () => void;
  result: AreaSearchResponse | null;
  loading: boolean;
  selected: { domain: AreaDomain; id: string } | null;
  onSelect: (domain: AreaDomain, id: string) => void;
}

export default function AreaPanel({ area }: { area: AreaState }) {
  const D = T.dashboard;
  const r = area.result;

  const group = (domain: AreaDomain, entries: AreaSearchResponse["air"], label: string) => (
    <div>
      <div className="section-label">{label}</div>
      <ResultCards
        items={entries.map((e) => ({
          id: e.id,
          title: e.label,
          sub: e.sublabel,
          badges: e.ts ? [{ label: utcShort(e.ts) }] : [],
        }))}
        selected={area.selected?.domain === domain ? area.selected.id : null}
        onOpen={(id) => area.onSelect(domain, id)}
        empty="—"
        aria={label}
      />
    </div>
  );

  return (
    <>
      <div className="search-row">
        <button className="draw-box-btn" aria-pressed={area.drawing} onClick={area.onDraw}>
          {area.drawing ? D.drawing : area.hasBox ? D.redraw : D.drawBox}
        </button>
      </div>

      {/* ENHANCE — the SAME shared time-window control the entity searches use */}
      <TimeWindowPicker value={area.windowH} onChange={area.onWindow} max={168} />

      <div>
        <div className="section-label">{D.domainsLabel}</div>
        <div className="seg segment" role="group" aria-label={D.domainsLabel}>
          <button aria-pressed={area.air} onClick={area.onAir}>{D.areaAir}</button>
          <button aria-pressed={area.sea} onClick={area.onSea}>{D.areaSea}</button>
          <button aria-pressed={area.space} onClick={area.onSpace}>{D.areaSpace}</button>
        </div>
        {area.space && <div className="layer-hint">{D.areaSpaceCost}</div>}
      </div>

      {area.loading ? (
        <div className="filter-note">{T.entitySearch.loading}</div>
      ) : !r || !area.hasBox ? (
        <div className="filter-note">{D.areaPrompt}</div>
      ) : r.air.length + r.sea.length + r.space.length === 0 ? (
        <div className="filter-note">{D.areaEmpty}</div>
      ) : (
        <>
          {area.air && group("air", r.air, D.resultsAir(r.air.length))}
          {area.sea && group("sea", r.sea, D.resultsSea(r.sea.length))}
          {area.space && group("space", r.space, D.resultsSpace(r.space.length))}
          <Caveat k="area" response={r.caveat} className="strip-warn" />
        </>
      )}
    </>
  );
}
